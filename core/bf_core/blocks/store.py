"""Regular block models of the project: writing, reading and deleting (ADR 0017 §3; contract — `schema.py`).

Writing, as for the other objects: `CellsWriter` streams the occupied cells into a temporary Parquet file of
the store (`blobs\\.tmp`, fsync, SHA-256) outside the write transaction, counting the statistics of the fields;
`commit_block_model` inside it adds the object (`state` ready, or building under the id of an import job — the
job turns its objects ready at the end, a cancel or a failure drops them, `jobs/store.py::drop_partial`) and
moves the file into place as the blob `cells`. `StagedCells.discard()` drops the temp file when the commit does
not happen. Reading: `iter_cells` by portions (row groups or `batch_rows`), `read_cells` at once (small models).
Ported in spirit from BF `storage/blocks.py`; BF kept dense centres and sizes, BF2 keeps cell numbers only.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, blobs, object_refs, objects
from bf_core.storage.blobs import CHUNK, BlobStore

from . import schema as bs

if TYPE_CHECKING:
    import numpy as np

OWNER = "object"
MAX_NAME = 200
Staged = tuple[Path, str, int]


def not_found() -> ApiError:
    return ApiError(404, "block_model_not_found", "Блочная модель не найдена.", "Обновите дерево проекта.")


def broken() -> ApiError:
    return ApiError(
        409, "block_model_broken", "Файл ячеек блочной модели не найден в проекте.", "Загрузите модель заново."
    )


# --- statistics ------------------------------------------------------------------------------------------


class _NumberStats:
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


class _CategoryStats:
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


# --- writing ---------------------------------------------------------------------------------------------


@dataclass
class StagedCells:
    """A Parquet file of cells ready to be committed, with the grid and the fields (stats included)."""

    grid: dict[str, Any]
    fields: list[dict[str, Any]]
    cells: int
    file: Staged | None

    def discard(self) -> None:
        if self.file is not None:
            self.file[0].unlink(missing_ok=True)
            self.file = None


@dataclass
class CellsWriter:
    """Streams occupied cells into a temporary Parquet file: `write(idx, {name: values})` any number of times
    with ascending `idx`, then `finish()` → StagedCells (or `discard()`). As a context manager it discards
    itself on an exception. Values: numbers — anything float64 takes (NaN, ±inf — empty), categories — int
    codes into the labels (−1 — empty). Memory: one row group (ROW_GROUP rows) of buffered columns."""

    store: BlobStore
    grid_in: Mapping[str, Any]
    fields_in: Sequence[Mapping[str, Any]]
    row_group: int = bs.ROW_GROUP
    grid: dict[str, Any] = field(init=False)
    fields: list[dict[str, Any]] = field(init=False)
    path: Path = field(init=False)
    cells: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        import pyarrow as pa

        self.grid = bs.check_grid(self.grid_in)
        self.fields = bs.check_fields(self.fields_in)
        self._n = bs.cell_count(self.grid)
        self._last = -1
        self._stats: list[_NumberStats | _CategoryStats] = [
            _NumberStats() if f["kind"] == bs.NUMBER else _CategoryStats(len(f["labels"])) for f in self.fields
        ]
        types = [pa.float64() if f["kind"] == bs.NUMBER else pa.int32() for f in self.fields]
        columns = [(f["name"], t) for f, t in zip(self.fields, types, strict=True)]
        self._schema = pa.schema([(bs.IDX, pa.uint64()), *columns])
        self._buf: list[list[Any]] = [[] for _ in range(len(self.fields) + 1)]
        self._buffered = 0
        self.store.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.store.tmp_dir / uuid.uuid4().hex
        self._writer: Any = None
        self._done = False

    def __enter__(self) -> CellsWriter:
        return self

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        if exc_type is not None:
            self.discard()

    def write(self, idx: Any, values: Mapping[str, Any]) -> None:
        import numpy as np

        if self._done:
            raise RuntimeError("CellsWriter is finished")
        ix = np.asarray(idx)
        if ix.ndim != 1 or (len(ix) and not np.issubdtype(ix.dtype, np.integer)):
            raise bs.bad_model("Номера ячеек должны быть целыми числами.")
        if not len(ix):
            return
        if int(ix.min()) < 0 or int(ix.max()) >= self._n:
            raise bs.bad_model("Номер ячейки вне сетки модели.")
        ix = ix.astype(np.uint64, copy=False)
        if int(ix[0]) <= self._last or (len(ix) > 1 and bool((ix[1:] <= ix[:-1]).any())):
            raise bs.bad_model("Номера ячеек должны идти по возрастанию без повторов.")
        missing = [f["name"] for f in self.fields if f["name"] not in values]
        if missing:
            raise bs.bad_model(f"Нет значений поля «{missing[0]}».")
        cols: list[Any] = [ix]
        for f, st in zip(self.fields, self._stats, strict=True):
            raw = np.asarray(values[f["name"]])
            if raw.shape != ix.shape:
                raise bs.bad_model(f"Поле «{f['name']}»: число значений не совпадает с числом ячеек.")
            if f["kind"] == bs.NUMBER:
                col = raw.astype(np.float64, copy=True)
                col[~np.isfinite(col)] = np.nan
            else:
                if len(raw) and not np.issubdtype(raw.dtype, np.integer):
                    raise bs.bad_model(f"Поле «{f['name']}»: коды категорий должны быть целыми.")
                col = raw.astype(np.int32)
            st.add(col)
            cols.append(col)
        self._last = int(ix[-1])
        self.cells += len(ix)
        for buf, col in zip(self._buf, cols, strict=True):
            buf.append(col)
        self._buffered += len(ix)
        while self._buffered >= self.row_group:
            self._flush(self.row_group)

    def _open(self) -> Any:
        """The Parquet writer: zstd; dictionary encoding for the categories only (numbers and `idx` gain nothing
        from it and cost memory: −64 MB at 5 million cells × 3 fields, and a smaller file)."""
        import pyarrow.parquet as pq

        codes = [f["name"] for f in self.fields if f["kind"] == bs.CATEGORY]
        return pq.ParquetWriter(self.path, self._schema, compression="zstd", use_dictionary=codes or False)

    def _flush(self, rows: int) -> None:
        """Writes the first `rows` buffered rows as one row group."""
        import numpy as np
        import pyarrow as pa

        if self._writer is None:
            self._writer = self._open()
        merged = [np.concatenate(buf) if len(buf) > 1 else buf[0] for buf in self._buf]
        head = [m[:rows] for m in merged]
        self._writer.write_table(pa.Table.from_arrays(head, schema=self._schema), row_group_size=rows)
        rest = [m[rows:] for m in merged]
        self._buffered = len(rest[0])
        self._buf = [[r] if len(r) else [] for r in rest]

    def finish(self) -> StagedCells:
        """Closes the file (fsync) and counts its SHA-256: the cells are ready to be committed."""

        try:
            if self._buffered:
                self._flush(self._buffered)
            if self._writer is None:  # no cells: an empty file with the columns
                self._writer = self._open()
            self._writer.close()
            self._writer = None
            h = hashlib.sha256()
            with open(self.path, "rb+") as fh:
                os.fsync(fh.fileno())
                for chunk in iter(lambda: fh.read(CHUNK), b""):
                    h.update(chunk)
            size = self.path.stat().st_size
        except BaseException:
            self.discard()
            raise
        self._done = True
        fields = [{**f, "stats": st.result()} for f, st in zip(self.fields, self._stats, strict=True)]
        return StagedCells(self.grid, fields, self.cells, (self.path, h.hexdigest(), size))

    def discard(self) -> None:
        self._done = True
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001 — the file goes anyway
                pass
            self._writer = None
        self.path.unlink(missing_ok=True)


def free_name(conn: sa.Connection, name: str, exclude_id: int | None = None) -> str:
    """`name`, or «name (2)», «name (3)»… when a block model of the project has that name already."""
    base = " ".join(str(name).split())[:MAX_NAME] or "Блочная модель"
    where = objects.c.kind == bs.KIND
    if exclude_id is not None:
        where = where & (objects.c.id != exclude_id)
    taken = set(conn.execute(sa.select(objects.c.name_key).where(where)).scalars())
    candidate, k = base, 1
    while dialect.name_key(candidate) in taken:
        k += 1
        suffix = f" ({k})"
        candidate = base[: MAX_NAME - len(suffix)] + suffix
    return candidate


def commit_block_model(
    conn: sa.Connection,
    store: BlobStore,
    staged: StagedCells,
    *,
    name: str,
    source: Mapping[str, Any] | None = None,
    warnings: Sequence[str] = (),
    parent_id: int | None = None,
    folder: str | None = None,
    state: str = "ready",
    job_id: int | None = None,
    crs: str | None = None,
    author: str | None = None,
    sort_key: float = 0.0,
) -> int:
    """Inside the write transaction: the object (a free name) and its blob `cells`."""
    if staged.file is None:
        raise RuntimeError("the cells were discarded")
    now = dialect.utcnow()
    final = free_name(conn, name)
    meta = bs.make_meta(staged.grid, staged.cells, staged.fields, source=source, warnings=warnings)
    row = dict(
        kind=bs.KIND, name=final, name_key=dialect.name_key(final), parent_id=parent_id, folder=folder,
        sort_key=sort_key, state=state, version=1, meta=meta, extent=bs.extent(staged.grid), crs=crs,
        job_id=job_id, author=author, created_at=now, updated_at=now,
    )  # fmt: skip
    res = conn.execute(sa.insert(objects), row)
    bm_id = int(res.inserted_primary_key[0])  # type: ignore[index]
    store.commit_staged(conn, staged.file, media_type=bs.PARQUET_MEDIA, owner_kind=OWNER, owner_id=bm_id,
                        role=bs.ROLE)  # fmt: skip
    staged.file = None  # in place now: a later discard() must not touch it
    return bm_id


def put_block_model(
    conn: sa.Connection,
    store: BlobStore,
    grid: Mapping[str, Any],
    fields: Sequence[Mapping[str, Any]],
    idx: Any,
    values: Mapping[str, Any],
    *,
    name: str,
    **kw: Any,
) -> int:
    """Stage and commit in one go (small models, tests). The temp file goes if the commit fails."""
    with CellsWriter(store, grid, fields) as w:
        w.write(idx, values)
        staged = w.finish()
    try:
        return commit_block_model(conn, store, staged, name=name, **kw)
    finally:
        staged.discard()


# --- reading ---------------------------------------------------------------------------------------------

_COLUMNS = (
    objects.c.id, objects.c.name, objects.c.folder, objects.c.version, objects.c.meta, objects.c.extent,
    objects.c.crs, objects.c.created_at, objects.c.updated_at,
)  # fmt: skip


def _size(conn: sa.Connection, ids: Sequence[int]) -> dict[int, int]:
    rows = conn.execute(
        sa.select(blob_owners.c.owner_id, blobs.c.size)
        .join(blobs, blobs.c.sha256 == blob_owners.c.sha256)
        .where(blob_owners.c.owner_kind == OWNER, blob_owners.c.role == bs.ROLE, blob_owners.c.owner_id.in_(ids))
    ).all()
    return {int(r.owner_id): int(r.size) for r in rows}


def _public(row: Mapping[str, Any], size: int | None, brief: bool) -> dict[str, Any]:
    meta = dict(row["meta"] or {})
    grid = dict(meta.get("grid") or {})
    variable = "tensors" in grid
    if brief:
        grid.pop("tensors", None)
    fields = meta.get("fields") or []
    if brief:
        fields = [{k: f.get(k) for k in ("name", "kind", "unit")} for f in fields]
    return {
        "id": int(row["id"]), "name": row["name"], "folder": row["folder"], "version": int(row["version"]),
        "extent": row["extent"], "crs": row["crs"], "grid": {**grid, "variable": variable},
        "cells": int(meta.get("cells") or 0), "fields": fields, "source": meta.get("source"),
        "warnings": list(meta.get("warnings") or []), "size": size,
        "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat(),
    }  # fmt: skip


def _rows(conn: sa.Connection, *where: Any, brief: bool) -> list[dict[str, Any]]:
    rows = (
        conn.execute(
            sa.select(*_COLUMNS)
            .where(objects.c.kind == bs.KIND, objects.c.state == "ready", *where)
            .order_by(objects.c.sort_key, objects.c.name_key, objects.c.id)
        )
        .mappings()
        .all()
    )
    sizes = _size(conn, [int(r["id"]) for r in rows])
    return [_public(dict(r), sizes.get(int(r["id"])), brief) for r in rows]


def list_block_models(conn: sa.Connection) -> list[dict[str, Any]]:
    """Block models of the tree: grid without `tensors` (`variable` says if they exist), fields without stats."""
    return _rows(conn, brief=True)


def block_model_info(conn: sa.Connection, bm_id: int) -> dict[str, Any]:
    """Everything «Свойства» show: the grid (tensors too), the fields with units and stats, the source."""
    found = _rows(conn, objects.c.id == bm_id, brief=False)
    if not found:
        raise not_found()
    return found[0]


def cells_sha(conn: sa.Connection, bm_id: int) -> str:
    sha = conn.execute(
        sa.select(blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == bm_id, blob_owners.c.role == bs.ROLE
        )
    ).scalar()
    if sha is None:
        raise broken()
    return str(sha)


def iter_cells(
    store: BlobStore, sha: str, columns: Sequence[str] | None = None, batch_rows: int | None = None
) -> Iterator[tuple[np.ndarray, dict[str, np.ndarray]]]:
    """(idx uint64, {field: values}) by portions in ascending `idx`: row groups, or `batch_rows` rows.
    `columns` — the fields to read (None — all). The file stays open only while the iterator runs."""
    import pyarrow.parquet as pq

    path = store.path_of(sha)
    if not path.is_file():
        raise broken()
    names = None if columns is None else [bs.IDX, *(c for c in columns if c != bs.IDX)]
    with open(path, "rb") as fh:
        pf = pq.ParquetFile(fh)
        if batch_rows is None:
            parts: Iterator[Any] = (pf.read_row_group(g, columns=names) for g in range(pf.num_row_groups))
        else:
            parts = pf.iter_batches(batch_size=batch_rows, columns=names)
        for part in parts:
            if not part.num_rows:
                continue
            cols = {n: part.column(n).to_numpy(zero_copy_only=False) for n in part.schema.names}
            idx = cols.pop(bs.IDX)
            yield idx, cols


def read_cells(
    conn: sa.Connection, store: BlobStore, bm_id: int, columns: Sequence[str] | None = None
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """All occupied cells at once: (idx uint64, {field: values}) — for small models and tests."""
    import numpy as np

    info = block_model_info(conn, bm_id)
    names = [f["name"] for f in info["fields"]] if columns is None else list(columns)
    unknown = [c for c in names if c not in {f["name"] for f in info["fields"]}]
    if unknown:
        raise ApiError(404, "block_field_not_found", f"Поле «{unknown[0]}» не найдено.", "Обновите свойства модели.")
    parts = list(iter_cells(store, cells_sha(conn, bm_id), names))
    if not parts:
        kinds = {f["name"]: f["kind"] for f in info["fields"]}
        empty = {n: np.empty(0, np.float64 if kinds[n] == bs.NUMBER else np.int32) for n in names}
        return np.empty(0, np.uint64), empty
    idx = np.concatenate([p[0] for p in parts])
    return idx, {n: np.concatenate([p[1][n] for p in parts]) for n in names}


# --- changing and deleting -------------------------------------------------------------------------------


def rename_block_model(conn: sa.Connection, bm_id: int, expected_version: int, name: str) -> dict[str, Any]:
    current = block_model_info(conn, bm_id)
    if current["version"] != expected_version:
        raise ApiError(
            409, "version_conflict", "Модель изменили, пока вы её правили.", "Проверьте и повторите.",
            extra={"current": current},
        )  # fmt: skip
    clean = " ".join(str(name).split())[:MAX_NAME]
    if not clean:
        raise ApiError(422, "name_empty", "Название не может быть пустым.", "Введите название модели.")
    if dialect.name_key(clean) != dialect.name_key(current["name"]) and free_name(conn, clean, bm_id) != clean:
        raise ApiError(
            409, "name_taken", f"Блочная модель «{clean}» уже есть.", "Выберите другое название."
        )  # fmt: skip
    conn.execute(
        sa.update(objects)
        .where(objects.c.id == bm_id)
        .values(
            name=clean, name_key=dialect.name_key(clean), version=objects.c.version + 1, updated_at=dialect.utcnow()
        )
    )
    return block_model_info(conn, bm_id)


def delete_block_model(conn: sa.Connection, bm_id: int) -> dict[str, Any]:
    """Deletes the model and its file references (the file goes with the next collection). A model other
    objects refer to stays: 422 `block_model_in_use` with their list."""
    from bf_core.drillholes.store import drop_import_owners

    block_model_info(conn, bm_id)
    users = conn.execute(
        sa.select(objects.c.id, objects.c.name, objects.c.kind)
        .join(object_refs, object_refs.c.src_id == objects.c.id)
        .where(object_refs.c.dst_id == bm_id)
        .order_by(objects.c.name_key, objects.c.id)
    ).all()
    if users:
        names = ", ".join(f"«{u.name}»" for u in users[:5]) + (" и другие" if len(users) > 5 else "")
        raise ApiError(
            422, "block_model_in_use", f"Блочная модель используется: {names}.",
            "Удалите или измените эти объекты, затем удалите модель.",
            extra={"objects": [{"id": int(u.id), "name": str(u.name), "kind": str(u.kind)} for u in users]},
        )  # fmt: skip
    shas = list(
        conn.execute(
            sa.select(blob_owners.c.sha256).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == bm_id)
        ).scalars()
    )
    drop_import_owners(conn, [bm_id])
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == bm_id))
    conn.execute(sa.delete(objects).where(objects.c.id == bm_id))
    return {"id": bm_id, "shas": shas}
