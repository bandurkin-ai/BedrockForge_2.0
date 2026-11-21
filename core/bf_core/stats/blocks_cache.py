"""The columns of block models kept in the memory of the API process (ADR 0019 §3, решение 4): what a swath plot
reads of a model again and again while its box is turned.

    cols = columns(project, model, ["AU", "DOMAIN"])      # model — `Model` of `model_of(conn, bm_id)`
        cols.idx            the numbers of the occupied cells, ascending: uint32 (a grid of < 2³² cells — every
                            regular model of Э11, ≤ 200 million), else uint64 as in the file
        cols.number("AU")   float64 [n], NaN — empty (never 0)
        cols.codes("DOMAIN") int32 [n], −1 — empty; the labels — `model.labels("DOMAIN")`

Cache (`MODELS`): LRU by bytes, ≤ 600 МБ in all; key (project uid, model id, SHA-256 of the file of cells,
column). The file of a model never changes in place: a new file is a new SHA — a miss; the columns of the older
file of that model go when the new ones come, so do those of another project (a project closed and another
opened). A column bigger than the limit is not kept. Concurrent requests of one model read its file once (a
lock per file): the columns missing are read in one pass over the row groups of the Parquet file, straight into
arrays of the size of the model (no list of parts, no concatenation); the memory pyarrow keeps for itself after
that goes back to the system (13.35). Arrays are shared by requests: read-only.
numpy and pyarrow load on first use (tests/test_process.py)."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

LIMIT_BYTES = 600 * 1024 * 1024
IDX = "\0idx"
U32_CELLS = 1 << 32


@dataclass(frozen=True)
class Model:
    """What a series needs of a block model besides its cells: the grid, the fields, the SHA-256 of its file."""

    id: int
    name: str
    grid: dict[str, Any]
    fields: list[dict[str, Any]]
    sha: str
    cells: int

    def field(self, name: str) -> dict[str, Any] | None:
        """The field of that name (case aside), or None."""
        key = str(name).casefold()
        return next((f for f in self.fields if str(f["name"]).casefold() == key), None)

    def labels(self, name: str) -> list[str]:
        f = self.field(name)
        return list(f.get("labels") or []) if f is not None else []


def model_of(conn: Any, bm_id: int) -> Model:
    """The model `bm_id` (404 `block_model_not_found`, 409 `block_model_broken` — no file of cells)."""
    from bf_core.blocks import store

    info = store.block_model_info(conn, bm_id)
    return Model(int(info["id"]), str(info["name"]), dict(info["grid"]), list(info["fields"]),
                 store.cells_sha(conn, bm_id), int(info["cells"]))  # fmt: skip


def _nbytes(value: Any) -> int:
    return int(getattr(value, "nbytes", 64))


class ModelCache:
    """LRU of the columns of models by bytes (module docstring)."""

    def __init__(self, limit: int = LIMIT_BYTES) -> None:
        self.limit = limit
        self._lock = threading.Lock()
        self._items: OrderedDict[tuple[Any, ...], tuple[Any, int]] = OrderedDict()
        self._gates: dict[tuple[Any, ...], threading.Lock] = {}
        self.size = 0
        self.hits = 0
        self.misses = 0
        self.reads = 0  # passes over a file

    def peek(self, key: tuple[Any, ...]) -> Any:
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return hit[0]

    def put(self, key: tuple[Any, ...], value: Any) -> None:
        n = _nbytes(value)
        with self._lock:
            stale = [k for k in self._items if k[0] != key[0] or (k[1] == key[1] and k[2] != key[2])]
            for k in stale:  # another project; an older file of the model
                self._drop(k)
            if key in self._items:
                self._drop(key)
            if n > self.limit:
                return
            self._items[key] = (value, n)
            self.size += n
            while self.size > self.limit:
                self._drop(next(iter(self._items)))

    def _drop(self, key: tuple[Any, ...]) -> None:
        _v, n = self._items.pop(key)
        self.size -= n

    def gate(self, key: tuple[Any, ...]) -> threading.Lock:
        """The lock of one file: `with cache.gate(k):` — one reader of it at a time."""
        with self._lock:
            return self._gates.setdefault(key, threading.Lock())

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._gates.clear()
            self.size = 0

    def keys(self) -> list[tuple[Any, ...]]:
        with self._lock:
            return list(self._items)


MODELS = ModelCache()


@dataclass
class ModelColumns:
    """The columns of one model read for a request (module docstring)."""

    model: Model
    idx: Any
    numbers: dict[str, Any] = field(default_factory=dict)
    category: dict[str, Any] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return int(self.idx.shape[0])

    def number(self, name: str) -> Any:
        return self.numbers[name]

    def codes(self, name: str) -> Any:
        return self.category[name]


def _frozen(a: Any) -> Any:
    a.flags.writeable = False
    return a


def _read(project: Any, m: Model, names: Sequence[str], want_idx: bool) -> dict[str, Any]:
    """One pass over the row groups of the file: the columns `names` (and `idx`) into arrays of `m.cells` rows."""
    import numpy as np

    from bf_core.blocks import schema as bs
    from bf_core.blocks import store
    from bf_core.storage.blobs import BlobStore

    kinds = {f["name"]: f["kind"] for f in m.fields}
    small = bs.cell_count(m.grid) < U32_CELLS
    out: dict[str, Any] = {}
    if want_idx:
        out[IDX] = np.empty(m.cells, dtype=np.uint32 if small else np.uint64)
    for name in names:
        out[name] = np.empty(m.cells, dtype=np.float64 if kinds[name] == bs.NUMBER else np.int32)
    pos = 0
    for idx, values in store.iter_cells(BlobStore(project.folder), m.sha, list(names)):
        n = int(idx.shape[0])
        if pos + n > m.cells:
            raise store.broken()
        if want_idx:
            out[IDX][pos : pos + n] = idx
        for name in names:
            out[name][pos : pos + n] = values[name]
        pos += n
    if pos != m.cells:
        raise store.broken()
    import pyarrow as pa

    pa.default_memory_pool().release_unused()  # what the pool keeps of the row groups: ~90 МБ at 20 million cells
    return out


def columns(project: Any, m: Model, names: Sequence[str], *, cache: ModelCache = MODELS) -> ModelColumns:
    """`idx` and the fields `names` (the stored names, `Model.field`) of the model, from the cache or the file."""
    from bf_core.blocks import schema as bs

    wanted = list(dict.fromkeys(names))
    head = (project.uid, m.id, m.sha)
    got: dict[str, Any] = {}
    with cache.gate(head):  # concurrent requests of one model read its file once
        for key in (IDX, *wanted):
            hit = cache.peek((*head, key))
            if hit is not None:
                got[key] = hit
        missing = [n for n in wanted if n not in got]
        if missing or IDX not in got:
            with cache._lock:
                cache.misses += len(missing) + (IDX not in got)
                cache.reads += 1
            fresh = _read(project, m, missing, IDX not in got)
            for key, value in fresh.items():
                got[key] = _frozen(value)
                cache.put((*head, key), got[key])
    out = ModelColumns(m, got[IDX])
    for name in wanted:
        f = m.field(name)
        if f is not None and f["kind"] == bs.CATEGORY:
            out.category[name] = got[name]
        else:
            out.numbers[name] = got[name]
    return out
