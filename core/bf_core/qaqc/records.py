"""QAQC records (ADR 0020 §2): the columns of one `qaqc_data` object, their Parquet file and the column cache.

    recs = open_file(path, sha=sha, scope=project_uid)    # lazy: a column is read on first use, via CACHE
    recs.n, recs.schema.elements                          # rows; (Element(name, unit, has_dl), …)
    recs.number(v("AU")), recs.flags(q("AU"))             # float64 [n] (NaN — missing, never 0); uint8 [n]
    recs.number(p("AU")), recs.number(b("AU"))            # the value of the parent sample; of the previous one
    recs.text(LAB)                                        # Text: int32 codes [n] (−1 — empty) into sorted labels
    recs.ids(SAMPLE_ID)                                   # pyarrow string array (identifiers, not a dictionary)
    recs.index(PARENT_ROW), recs.row                      # int32 [n] rows of the file (−1 — none); own rows
    recs.date()                                           # float64 [n] ms since 1970-01-01 UTC, NaN — no date
    sub = recs.take(mask)                                 # the rows of a bool mask (or int indices), order kept

Columns (`Schema.columns()`): `sample_id`, `parent_id` — text; `hole`, `type_code` (the code of the file as it
is), `crm_id`, `lab`, `batch`, `parent_lab` — text by dictionary; `date` — timestamp[ms] or empty; `seq` — float64;
`parent_row`, `prev_row` — int32 (−1 — none); per element E: `v:E` float64, `q:E` uint8 (`Q_NUMBER` 0 — a number
or missing, `Q_BELOW` 1 — «<DL» (the value is DL/2), `Q_ABOVE` 2 — «>x»), `p:E` — `v:E` of the parent sample,
`b:E` — `v:E` of the previous sample of the batch, `dl:E` — DL by field when mapped; other fields `x:<name>` —
float64 or text by dictionary (≤ 40). `row` is not stored: it is the position in the file (0-based), kept by
`take`, so that the rows of a filtered calculation and of `take(mask)` are the same numbers (Т8.4-8).

`parent_row`, `prev_row` are rows of the whole file, not of a `take`: what they point to is already copied into
`p:E`, `b:E`, `parent_lab`. Labels of a Text are sorted by code points: groups come out by names (ADR 0020 §5);
`take` keeps the labels (codes of a part mean the same names as in the whole).

File: Parquet zstd, row groups of 250 000, the schema in the key-value metadata `bf.qaqc` (JSON `v: 1`). Cache:
`CACHE` — `stats/source.ColumnCache` of 256 МБ; key (scope, sha256 of the file, 0, 0, column): another scope
(project) drops the columns of the previous one, closing a project drops its own (`watch`); reads of one file
go one at a time, each absent column once.
Limits: ≤ 5 million rows, ≤ 60 elements, ≤ 40 other fields (`check_size`, 422 with a hint).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.stats.source import ColumnCache

VERSION = 1
META_KEY = b"bf.qaqc"
MEDIA_TYPE = "application/vnd.apache.parquet"
MAX_ROWS = 5_000_000
MAX_ELEMENTS = 60
MAX_EXTRA = 40
ROW_GROUP = 250_000
CACHE = ColumnCache(256 * 1024 * 1024)

SAMPLE_ID, PARENT_ID = "sample_id", "parent_id"
HOLE, TYPE_CODE, CRM_ID, LAB, BATCH, PARENT_LAB = "hole", "type_code", "crm_id", "lab", "batch", "parent_lab"
DATE, SEQ, PARENT_ROW, PREV_ROW = "date", "seq", "parent_row", "prev_row"
IDS = (SAMPLE_ID, PARENT_ID)
TEXTS = (HOLE, TYPE_CODE, CRM_ID, LAB, BATCH, PARENT_LAB)
BASE = (SAMPLE_ID, HOLE, TYPE_CODE, PARENT_ID, CRM_ID, LAB, BATCH, DATE, SEQ, PARENT_ROW, PREV_ROW, PARENT_LAB)
Q_NUMBER, Q_BELOW, Q_ABOVE = 0, 1, 2
NUMBER, TEXT = "number", "text"


_watched: list[Any] = []


def watch(svc: Any) -> None:
    """Once per project service: the columns of a project leave `CACHE` when it is closed (`svc.on_close`)."""
    if any(w is svc for w in _watched):
        return

    def closed(project: Any) -> bool:
        CACHE.forget((project.uid,))
        return False  # nothing to keep the project for

    svc.on_close.append(closed)
    _watched.append(svc)


def v(e: str) -> str:
    return f"v:{e}"


def q(e: str) -> str:
    return f"q:{e}"


def p(e: str) -> str:
    return f"p:{e}"


def b(e: str) -> str:
    return f"b:{e}"


def dl(e: str) -> str:
    return f"dl:{e}"


def x(name: str) -> str:
    return f"x:{name}"


def too_big(what: str, count: int, limit: int) -> ApiError:
    texts = {
        "rows": ("too_many_rows", f"Строк в данных QAQC: {count:,}, больше предела {limit:,}.".replace(",", " ")),
        "elements": ("too_many_elements", f"Элементов: {count}, больше предела {limit}."),
        "extra": ("too_many_fields", f"Прочих полей: {count}, больше предела {limit}."),
    }
    code, message = texts[what]
    hint = {
        "rows": "Оставьте в файле контрольные пробы и их исходные пробы, остальные рядовые пробы уберите.",
        "elements": "Сопоставьте только нужные элементы, остальные колонки оставьте без сопоставления.",
        "extra": "Оставьте для фильтра только нужные прочие поля.",
    }[what]
    return ApiError(422, code, message, hint, extra={"param": what})


def check_size(rows: int, elements: int, extra: int = 0) -> None:
    """422 `too_many_rows` / `too_many_elements` / `too_many_fields` past the limits of ADR 0020 §2."""
    if rows > MAX_ROWS:
        raise too_big("rows", rows, MAX_ROWS)
    if elements > MAX_ELEMENTS:
        raise too_big("elements", elements, MAX_ELEMENTS)
    if extra > MAX_EXTRA:
        raise too_big("extra", extra, MAX_EXTRA)


# --- schema --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Element:
    name: str
    unit: str | None = None
    has_dl: bool = False  # a column `dl:E` (DL by field) is there


@dataclass(frozen=True)
class Extra:
    name: str
    kind: str  # NUMBER | TEXT


@dataclass(frozen=True)
class Schema:
    elements: tuple[Element, ...] = ()
    extra: tuple[Extra, ...] = ()

    def element(self, name: str) -> Element | None:
        key = name.casefold()
        return next((e for e in self.elements if e.name.casefold() == key), None)

    def columns(self) -> list[str]:
        out = list(BASE)
        for e in self.elements:
            out += [v(e.name), q(e.name), p(e.name), b(e.name)] + ([dl(e.name)] if e.has_dl else [])
        return out + [x(f.name) for f in self.extra]

    def kind(self, column: str) -> str:
        """'id', 'text', 'date', 'index', 'flag' or 'number' of a column; KeyError — not a column of the schema."""
        if column in IDS:
            return "id"
        if column in TEXTS:
            return "text"
        if column == DATE:
            return "date"
        if column in (PARENT_ROW, PREV_ROW):
            return "index"
        if column == SEQ:
            return "number"
        head, _, name = column.partition(":")
        if head in ("v", "p", "b", "q", "dl") and self.element(name) is not None:
            if head == "dl" and not self.element(name).has_dl:  # type: ignore[union-attr]
                raise KeyError(column)
            return "flag" if head == "q" else "number"
        if head == "x":
            for f in self.extra:
                if f.name == name:
                    return "text" if f.kind == TEXT else "number"
        raise KeyError(column)

    def to_json(self) -> dict[str, Any]:
        return {
            "v": VERSION,
            "elements": [{"name": e.name, "unit": e.unit, "has_dl": e.has_dl} for e in self.elements],
            "extra": [{"name": f.name, "kind": f.kind} for f in self.extra],
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> Schema:
        els = tuple(Element(str(e["name"]), e.get("unit"), bool(e.get("has_dl"))) for e in raw.get("elements", ()))
        extra = tuple(Extra(str(f["name"]), str(f["kind"])) for f in raw.get("extra", ()))
        return Schema(els, extra)


# --- text columns ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Text:
    """A text column by dictionary: `codes` int32 [n] (−1 — empty) into `labels` sorted by code points."""

    codes: np.ndarray
    labels: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.codes)

    def take(self, idx: np.ndarray) -> Text:
        return Text(self.codes[idx], self.labels)

    def values(self) -> list[str | None]:
        labels = self.labels
        return [labels[c] if c >= 0 else None for c in self.codes.tolist()]

    def code(self, label: str) -> int:
        """The code of `label` (exact), −1 — not among the labels."""
        import bisect

        i = bisect.bisect_left(self.labels, label)
        return i if i < len(self.labels) and self.labels[i] == label else -1

    def used(self) -> Text:
        """The same values with only the labels in use (a written file keeps no others)."""
        present = np.zeros(len(self.labels) + 1, dtype=bool)
        present[self.codes + 1] = True
        keep = np.flatnonzero(present[1:])
        if len(keep) == len(self.labels):
            return self
        remap = np.full(len(self.labels) + 1, -1, dtype=np.int32)
        remap[keep + 1] = np.arange(len(keep), dtype=np.int32)
        return Text(remap[self.codes + 1], tuple(self.labels[i] for i in keep.tolist()))

    @staticmethod
    def of(values: Any) -> Text:
        """From a list / pyarrow array of str or None: empty and blank strings are empty (−1)."""
        import pyarrow as pa
        import pyarrow.compute as pc

        arr = values if isinstance(values, pa.Array | pa.ChunkedArray) else pa.array(list(values), type=pa.string())
        if isinstance(arr, pa.ChunkedArray):
            arr = arr.combine_chunks() if arr.num_chunks else pa.array([], type=pa.string())
        if pa.types.is_dictionary(arr.type):
            arr = arr.cast(arr.type.value_type)
        if not pa.types.is_string(arr.type) and not pa.types.is_large_string(arr.type):
            arr = arr.cast(pa.string())
        arr = pc.if_else(pc.equal(pc.utf8_length(pc.utf8_trim_whitespace(arr)), 0), None, arr)
        return _from_dictionary(pc.dictionary_encode(arr))


def _from_dictionary(arr: Any) -> Text:
    """A pyarrow DictionaryArray → Text with sorted labels."""
    labels = [str(s) for s in arr.dictionary.to_pylist()]
    order = sorted(range(len(labels)), key=labels.__getitem__)
    remap = np.empty(len(labels) + 1, dtype=np.int32)
    remap[0] = -1
    remap[np.asarray(order, dtype=np.int64) + 1] = np.arange(len(order), dtype=np.int32)
    idx = arr.indices.fill_null(-1).to_numpy(zero_copy_only=False).astype(np.int64)
    return Text(remap[idx + 1], tuple(labels[i] for i in order))


def _pack_ids(arr: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A pyarrow string array as numpy buffers (validity bytes, offsets, data) — sized right for the cache."""
    import pyarrow as pa

    arr = arr.cast(pa.large_string())
    valid = arr.is_valid().to_numpy(zero_copy_only=False)
    _validity, offsets, data = arr.buffers()
    off = np.frombuffer(offsets, dtype=np.int64)[arr.offset : arr.offset + len(arr) + 1].copy()
    raw = np.frombuffer(data, dtype=np.uint8).copy() if data is not None else np.empty(0, np.uint8)
    return valid, off, raw


def _unpack_ids(packed: tuple[np.ndarray, np.ndarray, np.ndarray]) -> Any:
    import pyarrow as pa

    valid, off, raw = packed
    mask = pa.array(valid).buffers()[1] if not valid.all() else None
    return pa.Array.from_buffers(pa.large_string(), len(off) - 1, [mask, pa.py_buffer(off), pa.py_buffer(raw)])


# --- records ----------------------------------------------------------------------------------------------------


Loader = Callable[[str], Any]


@dataclass(eq=False)
class Records:
    """The rows of QAQC data; columns come from `loader` on first use and stay with the object.

    Values of the loader (and of the cache): numpy arrays; Text as a tuple (codes, labels); ids as the tuple of
    `_pack_ids`. `row` — int32 positions in the file."""

    n: int
    schema: Schema
    loader: Loader
    row: np.ndarray
    _have: dict[str, Any] = field(default_factory=dict, repr=False)
    path: Path | None = None  # the file of the records (`open_file`); None — in memory or a `take`

    def _get(self, name: str) -> Any:
        hit = self._have.get(name)
        if hit is None:
            self.schema.kind(name)  # KeyError for a column the schema does not have
            hit = self._have[name] = self.loader(name)
        return hit

    def column(self, name: str) -> Any:
        """np.ndarray, Text or a pyarrow string array (ids) — by `schema.kind(name)`."""
        kind = self.schema.kind(name)
        raw = self._get(name)
        if kind == "text":
            return Text(raw[0], raw[1])
        if kind == "id":
            return _unpack_ids(raw)
        return raw

    def number(self, name: str) -> np.ndarray:
        if self.schema.kind(name) != "number":
            raise KeyError(name)
        return self._get(name)  # type: ignore[no-any-return]

    def flags(self, name: str) -> np.ndarray:
        if self.schema.kind(name) != "flag":
            raise KeyError(name)
        return self._get(name)  # type: ignore[no-any-return]

    def index(self, name: str) -> np.ndarray:
        if self.schema.kind(name) != "index":
            raise KeyError(name)
        return self._get(name)  # type: ignore[no-any-return]

    def text(self, name: str) -> Text:
        if self.schema.kind(name) != "text":
            raise KeyError(name)
        return self.column(name)  # type: ignore[no-any-return]

    def ids(self, name: str = SAMPLE_ID) -> Any:
        if self.schema.kind(name) != "id":
            raise KeyError(name)
        return self.column(name)

    def date(self) -> np.ndarray:
        """float64 [n]: ms since 1970-01-01 (UTC, the date as written — no time zone), NaN — no date."""
        return self._get(DATE)  # type: ignore[no-any-return]

    def load(self, names: Iterable[str]) -> None:
        """Read the columns `names` now (one pass over the file for those not here yet)."""
        want = [n for n in dict.fromkeys(names) if n not in self._have]
        for n in want:
            self.schema.kind(n)
        many = getattr(self.loader, "many", None)
        if many is not None and len(want) > 1:
            self._have.update(many(want))
        else:
            for n in want:
                self._get(n)

    def take(self, sel: np.ndarray) -> Records:
        """The rows of a bool mask [n] or of int indices, in that order; columns taken on first use."""
        idx = np.flatnonzero(sel) if sel.dtype == bool else np.asarray(sel, dtype=np.int64)
        if sel.dtype == bool and len(sel) != self.n:
            raise ValueError("mask length differs from the rows")
        parent = self

        def load(name: str) -> Any:
            raw = parent._get(name)
            kind = parent.schema.kind(name)
            if kind == "text":
                return (raw[0][idx], raw[1])
            if kind == "id":
                return _pack_ids(_unpack_ids(raw).take(idx))
            return raw[idx]

        return Records(len(idx), self.schema, load, self.row[idx])

    @staticmethod
    def of(schema: Schema, columns: dict[str, Any]) -> Records:
        """Records in memory: numpy arrays, Text, pyarrow string arrays (ids) — every column of the schema."""
        names = schema.columns()
        missing = [c for c in names if c not in columns]
        if missing:
            raise KeyError(f"missing columns: {missing}")
        n = len(columns[SAMPLE_ID])
        have: dict[str, Any] = {}
        for c in names:
            value = columns[c]
            if len(value) != n:
                raise ValueError(f"column {c} has {len(value)} rows, not {n}")
            kind = schema.kind(c)
            if kind == "text":
                t = value if isinstance(value, Text) else Text.of(value)
                have[c] = (np.ascontiguousarray(t.codes, dtype=np.int32), t.labels)
            elif kind == "id":
                have[c] = _pack_ids(_as_strings(value))
            else:
                have[c] = np.ascontiguousarray(value, dtype=_DTYPES[kind])

        def nothing(name: str) -> Any:
            raise KeyError(name)

        return Records(n, schema, nothing, np.arange(n, dtype=np.int32), have)


_DTYPES = {"number": np.float64, "date": np.float64, "index": np.int32, "flag": np.uint8}


def _as_strings(value: Any) -> Any:
    import pyarrow as pa

    if isinstance(value, pa.ChunkedArray):
        value = value.combine_chunks()
    if isinstance(value, pa.Array):
        return value.cast(pa.large_string())
    return pa.array(list(value), type=pa.large_string())


# --- Parquet --------------------------------------------------------------------------------------------------------


def _arrow(recs: Records, name: str) -> Any:
    import pyarrow as pa

    kind = recs.schema.kind(name)
    raw = recs._get(name)
    if kind == "text":
        t = Text(raw[0], raw[1]).used()
        codes = pa.array(t.codes, type=pa.int32(), mask=t.codes < 0)
        return pa.DictionaryArray.from_arrays(codes, pa.array(list(t.labels), type=pa.string()))
    if kind == "id":
        return _unpack_ids(raw).cast(pa.string())
    if kind == "date":
        ms = np.where(np.isnan(raw), 0, raw).astype(np.int64)
        return pa.array(ms, type=pa.timestamp("ms"), mask=np.isnan(raw))
    return pa.array(raw)


def write(recs: Records, path: str | Path, tick: Callable[[], None] | None = None) -> None:
    """The records as Parquet (zstd, row groups of 250 000, the schema in the metadata `bf.qaqc`)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    names = recs.schema.columns()
    arrays = []
    for name in names:
        arrays.append(_arrow(recs, name))
        if tick is not None:
            tick()
    table = pa.Table.from_arrays(arrays, names=names)
    meta = dict(table.schema.metadata or {})
    meta[META_KEY] = json.dumps(recs.schema.to_json(), ensure_ascii=False).encode()
    table = table.replace_schema_metadata(meta)
    pq.write_table(table, str(path), compression="zstd", row_group_size=ROW_GROUP)


def schema_of(path: str | Path) -> tuple[Schema, int]:
    """(schema, rows) of a records file."""
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(str(path))
    raw = (pf.schema_arrow.metadata or {}).get(META_KEY)
    if raw is None:
        raise ApiError(409, "qaqc_data_broken", "Файл записей QAQC повреждён.", "Импортируйте данные заново.")
    return Schema.from_json(json.loads(raw)), int(pf.metadata.num_rows)


def _convert(kind: str, col: Any) -> Any:
    import pyarrow as pa

    if kind == "text":
        chunks = col.unify_dictionaries().chunks if col.num_chunks else []
        if not chunks:
            return (np.empty(0, np.int32), ())
        arr = pa.chunked_array(chunks).combine_chunks() if len(chunks) > 1 else chunks[0]
        if not pa.types.is_dictionary(arr.type):
            arr = arr.dictionary_encode()
        t = _from_dictionary(arr)
        return (t.codes, t.labels)
    if kind == "id":
        return _pack_ids(col.combine_chunks() if col.num_chunks else pa.array([], type=pa.string()))
    if kind == "date":
        ms = col.cast(pa.int64())
        out = ms.to_numpy(zero_copy_only=False)
        values = np.asarray(out, dtype=np.float64)
        if col.null_count:
            values[col.is_null().to_numpy(zero_copy_only=False)] = np.nan
        return values
    return np.ascontiguousarray(col.to_numpy(), dtype=_DTYPES[kind])


def _read(path: str | Path, schema: Schema, names: Sequence[str]) -> dict[str, Any]:
    import pyarrow.parquet as pq

    texts = [n for n in names if schema.kind(n) == "text"]
    table = pq.read_table(str(path), columns=list(names), read_dictionary=texts, memory_map=True)
    return {n: _convert(schema.kind(n), table.column(n)) for n in names}


def open_file(path: str | Path, *, sha: str | None = None, scope: Any = None, cache: ColumnCache = CACHE) -> Records:
    """Records of a file, columns read on first use. With `sha` the columns go through `cache` under
    (scope, sha, 0, 0, column); without — they stay only with the returned object."""
    schema, n = schema_of(path)

    def key(name: str) -> tuple[Any, ...]:
        return (scope, sha, 0, 0, name)

    def load(name: str) -> Any:
        return many([name])[name]

    def many(names: list[str]) -> dict[str, Any]:
        """The columns from the cache, the absent ones in one read of the file. Reads of one file go one at a time
        (concurrent requests of the same columns read them once: the second finds them in the cache)."""
        if sha is None:
            return _read(path, schema, names)
        got = {nm: cache.peek(key(nm)) for nm in names}
        if all(v is not None for v in got.values()):
            return got
        with cache.locked(key("\0file")):
            for nm in names:
                if got[nm] is None:
                    got[nm] = cache.peek(key(nm))
            absent = [nm for nm in names if got[nm] is None]
            if absent:
                with cache._lock:
                    cache.misses += len(absent)
                for nm, value in _read(path, schema, absent).items():
                    cache.put(key(nm), value)
                    got[nm] = value
        return got

    load.many = many  # type: ignore[attr-defined]
    return Records(n, schema, load, np.arange(n, dtype=np.int32), path=Path(path))


def read_rows(path: str | Path, rows: np.ndarray) -> Records:
    """Every column of the given rows of a file (sorted, unique positions): only the row groups that hold them,
    past the cache — the card of a record reads a few rows, not the columns of every element."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema, _n = schema_of(path)
    pf = pq.ParquetFile(str(path))
    sizes = np.array([pf.metadata.row_group(g).num_rows for g in range(pf.num_row_groups)], dtype=np.int64)
    starts = np.concatenate(([0], np.cumsum(sizes)))
    rows = np.asarray(rows, dtype=np.int64)
    names = schema.columns()
    group_of = np.searchsorted(starts, rows, side="right") - 1  # the row group of every row
    groups = np.unique(group_of)
    if len(groups):
        table = pf.read_row_groups(groups.tolist(), columns=names)
        base = np.concatenate(([0], np.cumsum(sizes[groups])))[:-1]  # where each group read starts in the table
        local = base[np.searchsorted(groups, group_of)] + rows - starts[group_of]
        table = table.take(pa.array(local))
    else:
        table = pf.schema_arrow.empty_table().select(names)
    have = {c: _convert(schema.kind(c), table.column(c)) for c in names}

    def nothing(name: str) -> Any:
        raise KeyError(name)

    return Records(len(rows), schema, nothing, rows.astype(np.int32), have)
