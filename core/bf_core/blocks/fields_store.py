"""Files of fields of a block model v2 (ADR 0023 §2, decision 4; contract — docs/progress/e17-api.md §2–3).

A field (or the group of fields of one calculation) is its own immutable Parquet file, the blob `fld:<key>` of the
model: the same rows in the same order and in the same row groups as the file `cells`, without `idx`. A new field
never touches `cells` (its SHA-256 stays), a replaced field is a new file, a deleted one leaves `meta` and a file
without fields leaves `blob_owners` (the file goes with the next collection).

Writing, as for the other objects: `FieldsWriter` streams the values into a temporary file of the store outside the
write transaction, counting the stats; `commit_fields` inside it checks the version (409 `version_conflict`), places
the file, writes `meta` v2 and raises `objects.version`. A failure at any point leaves nothing: the temp file goes
with the writer, a failed commit moves no file and writes no row.

The helpers `Column` (a field → the Arrow column and its stats), `GroupSink` (row groups of a given size), `seal` and
`decode` are shared with the writer of the cells (`store.CellsWriterV2`) and the readers (`store.read_cells`, 17.4).
In memory (e17-api.md §2): numbers of any `dtype` — float64, NaN — empty; categories — int32 codes, −1 — empty.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, objects
from bf_core.storage.blobs import CHUNK, BlobStore

from . import schema as bs
from .meta import check_field_v2, upgrade

if TYPE_CHECKING:
    import numpy as np
    import pyarrow as pa

OWNER = "object"
Staged = tuple[Path, str, int]
INT_RANGE = {"i4": (-(2**31), 2**31 - 1), "i2": (-(2**15), 2**15 - 1), "i1": (-(2**7), 2**7 - 1)}


def version_conflict(version: int) -> ApiError:
    return ApiError(
        409, "version_conflict", "Модель изменили, пока вы её правили.", "Перечитайте модель и повторите.",
        extra={"version": version},
    )  # fmt: skip


def bad_values(message: str) -> ApiError:
    return ApiError(422, "field_values_invalid", message, "Проверьте значения поля и тип хранения.")


def rows_mismatch(got: int, rows: int) -> ApiError:
    return ApiError(
        422, "field_rows_mismatch", f"Записано {got} значений, а в модели {rows} строк.",
        "Поле пишется на все строки модели по порядку строк.",
    )  # fmt: skip


# --- statistics (moved from Э11 `store.py`) --------------------------------------------------------------


class NumberStats:
    """count, min, max, mean and M2 merged batch by batch (Chan et al.), so the mean of 20 M cells is exact
    to float64 rounding of each batch, not of a running sum."""

    def __init__(self) -> None:
        self.count, self.missing = 0, 0
        self.mean = self.m2 = 0.0
        self.lo, self.hi = float("inf"), float("-inf")

    def add(self, values: np.ndarray) -> None:
        import numpy as np

        ok = values[np.isfinite(values)]
        self.missing += len(values) - len(ok)
        n = len(ok)
        if not n:
            return
        mean = float(ok.mean())
        m2 = float(((ok - mean) ** 2).sum())
        total = self.count + n
        delta = mean - self.mean
        self.mean += delta * n / total
        self.m2 += m2 + delta * delta * self.count * n / total
        self.count = total
        self.lo, self.hi = min(self.lo, float(ok.min())), max(self.hi, float(ok.max()))

    def result(self) -> dict[str, Any]:
        if not self.count:
            return {"count": 0, "missing": self.missing, "min": None, "max": None, "mean": None, "std": None}
        std = (self.m2 / self.count) ** 0.5
        return {"count": self.count, "missing": self.missing, "min": self.lo, "max": self.hi, "mean": self.mean,
                "std": std}  # fmt: skip


class CategoryStats:
    def __init__(self, labels: int) -> None:
        self.labels = labels
        self.counts = [0] * labels
        self.missing = 0

    def add(self, codes: np.ndarray) -> None:
        import numpy as np

        empty = codes == -1
        self.missing += int(empty.sum())
        valid = codes[~empty]
        if len(valid) and (int(valid.min()) < 0 or int(valid.max()) >= self.labels):
            raise bs.bad_model("Код категории вне списка подписей.")
        if len(valid):
            for code, n in enumerate(np.bincount(valid, minlength=self.labels).tolist()):
                self.counts[code] += int(n)

    def result(self) -> dict[str, Any]:
        return {"count": sum(self.counts), "missing": self.missing, "counts": list(self.counts)}


# --- a column of a file: values → Arrow, decode back -----------------------------------------------------


def _arrow_type(dtype: str) -> Any:
    import pyarrow as pa

    return {"f8": pa.float64(), "f4": pa.float32(), "i4": pa.int32(), "i2": pa.int16(), "i1": pa.int8(),
            "cat": pa.int16()}[dtype]  # fmt: skip


class Column:
    """One field of a file: `encode(values)` → an Arrow array of the stored type (f8 double, f4 float — NaN is
    empty; i4/i2/i1 and cat — null is empty), counting the stats of what is written; `result()` → the field with
    its `stats`. Values that the type cannot keep exactly are refused (never rounded or turned into empty)."""

    def __init__(self, spec: Mapping[str, Any], *, sub: bool = False) -> None:
        raw = dict(spec)
        raw.setdefault("kind", bs.CATEGORY if raw.get("dtype") == "cat" else bs.NUMBER)
        self.field = check_field_v2(raw, sub=sub)
        self.name, self.dtype = self.field["name"], self.field["dtype"]
        labels = self.field.get("labels") or []
        if self.dtype == "cat" and len(labels) > bs.MAX_LABELS_V2:
            raise ApiError(
                422, "too_many_labels", f"У поля «{self.name}» {len(labels)} подписей: не больше {bs.MAX_LABELS_V2}.",
                "Объедините редкие категории.", extra={"param": "labels"},
            )  # fmt: skip
        self.stats: NumberStats | CategoryStats = CategoryStats(len(labels)) if self.dtype == "cat" else NumberStats()
        self.type = _arrow_type(self.dtype)

    def encode(self, raw: Any) -> pa.Array:
        import numpy as np
        import pyarrow as pa

        arr = np.asarray(raw)
        if arr.ndim != 1:
            raise bad_values(f"Поле «{self.name}»: ожидается одномерный массив значений.")
        if self.dtype == "cat":
            if len(arr) and not np.issubdtype(arr.dtype, np.integer):
                raise bad_values(f"Поле «{self.name}»: коды категорий должны быть целыми.")
            codes = arr.astype(np.int64)
            if len(codes) and (int(codes.min()) < -1 or int(codes.max()) >= self.stats.labels):  # type: ignore[union-attr]
                raise bad_values(f"Поле «{self.name}»: код категории вне списка подписей.")
            self.stats.add(codes.astype(np.int32))
            return pa.array(codes.astype(np.int16), mask=codes < 0, type=self.type)
        try:
            vals = arr.astype(np.float64)
        except (TypeError, ValueError):
            raise bad_values(f"Поле «{self.name}»: значения не числа.") from None
        vals[~np.isfinite(vals)] = np.nan
        ok = ~np.isnan(vals)
        if self.dtype == "f8":
            self.stats.add(vals)
            return pa.array(vals, type=self.type)
        if self.dtype == "f4":
            with np.errstate(over="ignore"):
                single = vals.astype(np.float32)
            if (ok & ~np.isfinite(single)).any():
                raise bad_values(f"Поле «{self.name}»: значение не помещается в тип f4.")
            self.stats.add(single.astype(np.float64))
            return pa.array(single, type=self.type)
        lo, hi = INT_RANGE[self.dtype]
        good = vals[ok]
        if len(good) and (bool((good != np.round(good)).any()) or good.min() < lo or good.max() > hi):
            raise bad_values(f"Поле «{self.name}»: значения не целые или не помещаются в тип {self.dtype}.")
        self.stats.add(vals)
        return pa.array(np.where(ok, vals, 0.0).astype(np.int64), mask=~ok, type=self.type)

    def result(self) -> dict[str, Any]:
        return {**self.field, "stats": self.stats.result()}


def decode(column: Any, spec: Mapping[str, Any]) -> np.ndarray:
    """A Parquet column (Arrow array or chunked array) → memory form: numbers float64 with NaN for empty;
    categories int32 with −1 for empty (a v1 category — int32 with −1 — reads the same way)."""
    import numpy as np
    import pyarrow as pa

    if spec["kind"] == bs.CATEGORY:
        return np.asarray(column.fill_null(-1).to_numpy(zero_copy_only=False)).astype(np.int32, copy=False)
    return np.asarray(column.cast(pa.float64()).to_numpy(zero_copy_only=False)).astype(np.float64, copy=False)


# --- the file: row groups, fsync, SHA-256 -------------------------------------------------------------------


class GroupSink:
    """A Parquet file (zstd) written by row groups: `sizes` — one size for all groups (the last is the rest) or the
    list of sizes, as the groups of the file `cells`. `push(table)` any number of times, `close()`, or `abort()`
    (the file goes). `on_group(table)` sees every group written (the writer of the cells counts `groups`)."""

    def __init__(self, path: Path, schema: pa.Schema, sizes: int | Sequence[int], *, dictionary: Sequence[str] = (),
                 on_group: Callable[[Any], None] | None = None) -> None:  # fmt: skip
        self.path, self.schema, self.dictionary, self.on_group = path, schema, list(dictionary), on_group
        self._fixed = sizes if isinstance(sizes, int) else None
        self._sizes = [] if isinstance(sizes, int) else [int(s) for s in sizes]
        self._group, self._n = 0, 0
        self._pending: list[Any] = []
        self._writer: Any = None

    def _open(self) -> Any:
        import pyarrow.parquet as pq

        if self._writer is None:
            self._writer = pq.ParquetWriter(self.path, self.schema, compression="zstd",
                                            use_dictionary=self.dictionary or False)  # fmt: skip
        return self._writer

    def _next(self) -> int:
        if self._fixed is not None:
            return self._fixed
        if self._group >= len(self._sizes):
            raise bad_values("Больше значений, чем строк в группах файла.")
        return self._sizes[self._group]

    def push(self, table: Any) -> None:
        import pyarrow as pa

        if not table.num_rows:
            return
        self._pending.append(table)
        self._n += table.num_rows
        while self._n >= self._next():
            size = self._next()
            merged = pa.concat_tables(self._pending)
            self._write(merged.slice(0, size))
            rest = merged.slice(size)
            self._pending, self._n = ([rest] if rest.num_rows else []), rest.num_rows
            if not self._n:
                break

    def _write(self, head: Any) -> None:
        self._open().write_table(head, row_group_size=head.num_rows)
        if self.on_group is not None:
            self.on_group(head)
        self._group += 1

    def close(self) -> None:
        import pyarrow as pa

        if self._n:
            if self._fixed is None:
                raise bad_values("Меньше значений, чем строк в группах файла.")
            self._write(pa.concat_tables(self._pending))
            self._pending, self._n = [], 0
        elif self._fixed is None and self._group != len(self._sizes):
            raise bad_values("Меньше значений, чем строк в группах файла.")
        self._open().close()
        self._writer = None

    def abort(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001 — the file goes anyway
                pass
            self._writer = None
        self.path.unlink(missing_ok=True)


def seal(path: Path) -> tuple[str, int]:
    """fsync of the finished file and its SHA-256 and size."""
    h = hashlib.sha256()
    with open(path, "rb+") as fh:
        os.fsync(fh.fileno())
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest(), path.stat().st_size


def group_sizes(meta: Mapping[str, Any]) -> list[int]:
    """Row groups of the file `cells` of a model (the `groups` of v2; a v1 file — groups of ROW_GROUP rows)."""
    if meta.get("groups") is not None:
        return [int(g["rows"]) for g in meta["groups"]]
    rows = int(meta["rows"])
    sizes = [bs.ROW_GROUP] * (rows // bs.ROW_GROUP)
    return sizes + ([rows % bs.ROW_GROUP] if rows % bs.ROW_GROUP else [])


# --- reading a model for writing -------------------------------------------------------------------------------


class ModelView(Protocol):
    """What `FieldsWriter` needs of a model: `blocks.model.Model` (17.4) and `ModelRef` both fit."""

    id: int
    version: int
    meta: Mapping[str, Any]
    rows: int


@dataclass(frozen=True)
class ModelRef:
    """A model as the writer needs it: id, version and meta v2 (a v1 meta is turned by `meta.upgrade`)."""

    id: int
    version: int
    meta: dict[str, Any]

    @property
    def rows(self) -> int:
        return int(self.meta["rows"])

    @classmethod
    def load(cls, conn: sa.Connection, model_id: int) -> ModelRef:
        row = conn.execute(
            sa.select(objects.c.version, objects.c.meta).where(
                objects.c.kind == bs.KIND, objects.c.id == model_id, objects.c.state == "ready"
            )
        ).first()
        if row is None:
            from .store import not_found

            raise not_found()
        return cls(model_id, int(row.version), upgrade(row.meta))


# --- writing -----------------------------------------------------------------------------------------------------


@dataclass
class StagedFields:
    """A file of fields ready to be committed: the fields (stats included), the names they replace."""

    model_id: int
    expected_version: int
    key: str
    rows: int
    fields: list[dict[str, Any]]
    replaces: list[str]
    file: Staged | None

    def discard(self) -> None:
        if self.file is not None:
            self.file[0].unlink(missing_ok=True)
            self.file = None


class FieldsWriter:
    """Streams the values of new fields into one temporary file `fld:<key>`: `add(...)` the fields, `write(values)`
    the next rows in the order of the rows of the model any number of times, `finish()` → StagedFields. As a context
    manager it drops the temp file on an exception. A stale `expected_version` — 409 at once. A name the model
    has already — 409 `field_exists {field}` unless `replace=True` (the old field leaves the meta at the commit)."""

    def __init__(self, store: BlobStore, model: ModelView, expected_version: int) -> None:
        if model.version != expected_version:
            raise version_conflict(model.version)
        self.store, self.model = store, model
        self._meta = upgrade(model.meta)
        self.rows = int(self._meta["rows"])
        self._taken = {f["name"].casefold() for f in self._meta["fields"]}
        used = {f["file"] for f in self._meta["fields"]}
        self.key = bs.new_field_file()
        while self.key in used:
            self.key = bs.new_field_file()
        self._cols: list[Column] = []
        self._replaces: list[str] = []
        self._offset = 0
        self._sink: GroupSink | None = None
        self.path: Path | None = None
        self._done = False

    def __enter__(self) -> FieldsWriter:
        return self

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        if exc_type is not None or not self._done:  # an exception, or left without `finish()`
            self.discard()

    def add(
        self, name: str, dtype: str = "f8", *, unit: str | None = None, role: str | None = None,
        description: str | None = None, labels: Sequence[str] | None = None, replace: bool = False,
    ) -> None:  # fmt: skip
        if self._sink is not None or self._done:
            raise RuntimeError("fields are added before the first write")
        spec: dict[str, Any] = {"name": name, "dtype": dtype, "unit": unit, "role": role, "description": description,
                                "file": self.key}  # fmt: skip
        if labels is not None:
            spec["labels"] = list(labels)
        col = Column(spec, sub=bool(self._meta["sub"]))
        key = col.name.casefold()
        if any(c.name.casefold() == key for c in self._cols):
            raise bs.bad_model(f"Поле «{col.name}» добавлено дважды.")
        if key in self._taken:
            if not replace:
                raise ApiError(
                    409, "field_exists", f"Поле «{col.name}» в модели уже есть.",
                    "Задайте другое имя или замените поле.", extra={"field": col.name},
                )  # fmt: skip
            self._replaces.append(key)
        self._cols.append(col)

    def _start(self) -> GroupSink:
        import pyarrow as pa

        if not self._cols:
            raise RuntimeError("no fields were added")
        self.store.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.store.tmp_dir / uuid.uuid4().hex
        schema = pa.schema([(c.name, c.type) for c in self._cols])
        codes = [c.name for c in self._cols if c.dtype == "cat"]
        self._sink = GroupSink(self.path, schema, group_sizes(self._meta), dictionary=codes)
        return self._sink

    def write(self, values: Mapping[str, Any]) -> None:
        """The next rows of every added field (memory form: numbers — anything float64 takes, NaN — empty;
        categories — int codes, −1 — empty)."""
        import pyarrow as pa

        if self._done:
            raise RuntimeError("FieldsWriter is finished")
        sink = self._sink or self._start()
        missing = [c.name for c in self._cols if c.name not in values]
        if missing:
            raise bad_values(f"Нет значений поля «{missing[0]}».")
        n = {len(values[c.name]) for c in self._cols}
        if len(n) != 1:
            raise bad_values("У полей разное число значений.")
        count = n.pop()
        if self._offset + count > self.rows:
            raise rows_mismatch(self._offset + count, self.rows)
        arrays = [c.encode(values[c.name]) for c in self._cols]
        self._offset += count
        sink.push(pa.Table.from_arrays(arrays, schema=sink.schema))

    def finish(self) -> StagedFields:
        """Closes the file (fsync) and counts its SHA-256: the fields are ready to be committed."""
        try:
            sink = self._sink or self._start()
            if self._offset != self.rows:
                raise rows_mismatch(self._offset, self.rows)
            sink.close()
            assert self.path is not None
            sha, size = seal(self.path)
        except BaseException:
            self.discard()
            raise
        self._done = True
        return StagedFields(self.model.id, self.model.version, self.key, self.rows, [c.result() for c in self._cols],
                            list(self._replaces), (self.path, sha, size))  # fmt: skip

    def discard(self) -> None:
        self._done = True
        if self._sink is not None:
            self._sink.abort()
        elif self.path is not None:
            self.path.unlink(missing_ok=True)


# --- committing: new field, replacement, deletion -----------------------------------------------------------------


def _modify(
    conn: sa.Connection, model_id: int, expected_version: int, edit: Callable[[dict[str, Any]], None]
) -> tuple[dict[str, Any], int]:
    """Inside the write transaction: the row of the model locked, the version checked (409), `edit(meta)` applied to
    meta v2, the roles of files left without fields dropped, the whole meta checked again → (meta, old version)."""
    row = conn.execute(
        sa.select(objects.c.version, objects.c.meta)
        .where(objects.c.kind == bs.KIND, objects.c.id == model_id, objects.c.state == "ready")
        .with_for_update()
    ).first()
    if row is None:
        from .store import not_found

        raise not_found()
    if int(row.version) != expected_version:
        raise version_conflict(int(row.version))
    meta = upgrade(row.meta)
    old_files = {f["file"] for f in meta["fields"]}
    edit(meta)
    meta = upgrade(meta)
    for gone in sorted(old_files - {f["file"] for f in meta["fields"]} - {bs.ROLE}):
        conn.execute(
            sa.delete(blob_owners).where(
                blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == model_id, blob_owners.c.role == gone
            )
        )
    return meta, int(row.version)


def _save(conn: sa.Connection, model_id: int, meta: dict[str, Any], version: int) -> int:
    """The meta and the next version (the row is locked by `_modify`, the version was checked there)."""
    conn.execute(
        sa.update(objects)
        .where(objects.c.id == model_id)
        .values(meta=meta, version=version, updated_at=dialect.utcnow())
    )
    return version


def commit_fields(conn: sa.Connection, store: BlobStore, staged: StagedFields, *, source: Mapping[str, Any]) -> int:
    """Inside the write transaction: the file `fld:<key>` into place, its fields into `meta` (a replaced field keeps
    its place in the list) with `source` {op, params, inputs, algo, versions, at}, the version + 1 → the new version.
    The model changed since `expected_version` — 409 `version_conflict {version}`, nothing is written, the temp
    file goes; a failure after the file was moved takes the moved file away (if it was new)."""
    if staged.file is None:
        raise RuntimeError("the fields were discarded")
    placed: Path | None = None
    try:
        src = {"params": {}, "inputs": [], "algo": None, "versions": {}, "at": dialect.utcnow().isoformat(),
               **dict(source)}  # fmt: skip
        new = [{**f, "source": dict(src)} for f in staged.fields]

        def edit(meta: dict[str, Any]) -> None:
            if int(meta["rows"]) != staged.rows:
                raise rows_mismatch(staged.rows, int(meta["rows"]))
            names = {f["name"].casefold() for f in meta["fields"] if f["name"].casefold() not in staged.replaces}
            for f in new:
                if f["name"].casefold() in names:
                    raise ApiError(409, "field_exists", f"Поле «{f['name']}» в модели уже есть.",
                                   "Задайте другое имя или замените поле.", extra={"field": f["name"]})  # fmt: skip
            fresh = {f["name"].casefold(): f for f in new}
            out: list[dict[str, Any]] = []
            for f in meta["fields"]:
                key = f["name"].casefold()
                if key in staged.replaces:
                    out.append(fresh.pop(key))
                else:
                    out.append(f)
            meta["fields"] = out + list(fresh.values())

        meta, old = _modify(conn, staged.model_id, staged.expected_version, edit)
        path = store.path_of(staged.file[1])
        placed = None if path.exists() else path  # a file that is new goes with a failure
        store.commit_staged(conn, staged.file, media_type=bs.PARQUET_MEDIA, owner_kind=OWNER,
                            owner_id=staged.model_id, role=staged.key)  # fmt: skip
        staged.file = None  # in place now: a later discard() must not touch it
        version = _save(conn, staged.model_id, meta, old + 1)
        placed = None
        return version
    except BaseException:
        staged.discard()
        if placed is not None:
            placed.unlink(missing_ok=True)
        raise


def delete_fields(conn: sa.Connection, model_id: int, expected_version: int, names: Sequence[str]) -> int:
    """Inside the write transaction: the fields leave `meta` (the data stay in their files, which are immutable;
    a file left without fields leaves `blob_owners`) → the new version. 404 `block_field_not_found {param:
    "names.N"}`; 409 `version_conflict {version}`."""

    def edit(meta: dict[str, Any]) -> None:
        have = {f["name"].casefold() for f in meta["fields"]}
        for n, name in enumerate(names):
            if str(name).casefold() not in have:
                raise ApiError(
                    404, "block_field_not_found", f"Поле «{name}» не найдено.", "Обновите свойства модели.",
                    extra={"param": f"names.{n}"},
                )  # fmt: skip
        gone = {str(n).casefold() for n in names}
        meta["fields"] = [f for f in meta["fields"] if f["name"].casefold() not in gone]

    meta, old = _modify(conn, model_id, expected_version, edit)
    version = _save(conn, model_id, meta, old + 1)
    from bf_core import lineage

    for name in {str(n).casefold() for n in names}:  # the rows of the graph of a field that is gone
        lineage.forget(conn, lineage.Node("bm_field", model_id, name))
    return version


def iter_fields(store: BlobStore, sha: str, fields: Sequence[Mapping[str, Any]]) -> Iterator[dict[str, np.ndarray]]:
    """The fields of one file by row groups, in memory form (`decode`): {name: values}."""
    import pyarrow.parquet as pq

    from .store import broken

    path = store.path_of(sha)
    if not path.is_file():
        raise broken()
    with open(path, "rb") as fh:
        pf = pq.ParquetFile(fh)
        names = [f["name"] for f in fields]
        for g in range(pf.num_row_groups):
            part = pf.read_row_group(g, columns=names)
            yield {f["name"]: decode(part.column(f["name"]), f) for f in fields}
