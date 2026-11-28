"""The columns of block models kept in the memory of the API process (ADR 0019 §3, решение 4): what a swath plot
reads of a model again and again while its box is turned.

    cols = columns(project, model, ["AU", "DOMAIN"])      # model — `Model` of `model_of(conn, bm_id)`
        cols.idx            the numbers of the occupied cells, ascending: uint32 (a grid of < 2³² cells — every
                            regular model of Э11, ≤ 200 million), else uint64 as in the file
        cols.number("AU")   float64 [n], NaN — empty (never 0)
        cols.codes("DOMAIN") int32 [n], −1 — empty; the labels — `model.labels("DOMAIN")`

Cache (`MODELS`): LRU by bytes, ≤ 600 МБ in all; key (project uid, model id, SHA-256 of the file of cells,
column[, SHA-256 of the file `fld:` of the column]) — models v2 (ADR 0023, 17.4) keep a field of a calculation in
its own file; a column of `cells` has a key of four. The file of a model never changes in place: a new file is a new
SHA — a miss; the columns of the older file of that model go when the new ones come, so do those of another project
(a project closed and another opened) and a column that a new file `fld:` replaced (a field of that name in
another file). A column bigger than the limit is not kept. Concurrent requests of one model read its file once (a
lock per file): the columns missing are read in one pass over the row groups of the Parquet files, straight into
arrays of the size of the model (no list of parts, no concatenation; `blocks.model.Reader`); the memory pyarrow
keeps for itself after that goes back to the system (13.35). Arrays are shared by requests: read-only.

The disk (ADR 0023 §2, `cache/blocks/<SHA-256 of the file>/<name>.npy`): a model of `DISK_MIN_ROWS` rows or more
leaves its columns there after the first read of the Parquet file, and the next read of a column the memory has
lost (restart, LRU) is one `np.load` instead of the decoding of zstd. Written beside under a temporary name and
renamed; a file that does not fit (size, dtype) is read from the model again. The files are derived data: they go
without a loss (`clear_disk`).

A model with a turn, sub-blocks or a field FRAC (v2) is not `plain`: swath reads its rows itself, by portions
(`stats.swath_sub`, 17.10), not through these columns (`Model.plain`; `grid` keeps its `rotation` and `div`).
numpy and pyarrow load on first use (tests/test_process.py)."""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field as dfield
from pathlib import Path
from typing import Any

LIMIT_BYTES = 600 * 1024 * 1024
IDX = "\0idx"
U32_CELLS = 1 << 32
DISK_MIN_ROWS = 1_000_000  # a model of fewer rows is read from its files again (decoding it takes milliseconds)
CELLS = "cells"


@dataclass(frozen=True)
class Model:
    """What a series needs of a block model besides its cells: the grid, the fields, the SHA-256 of its files."""

    id: int
    name: str
    grid: dict[str, Any]
    fields: list[dict[str, Any]]
    sha: str
    cells: int
    shas: dict[str, str] = dfield(default_factory=dict)  # file role → SHA-256 (`sha` is that of `cells`)
    plain: bool = True  # no turn, no sub-blocks, no FRAC: the cells are boxes along the axes (Э13 reads them)

    def field(self, name: str) -> dict[str, Any] | None:
        """The field of that name (case aside), or None."""
        key = str(name).casefold()
        return next((f for f in self.fields if str(f["name"]).casefold() == key), None)

    def labels(self, name: str) -> list[str]:
        f = self.field(name)
        return list(f.get("labels") or []) if f is not None else []

    def file_of(self, name: str) -> str:
        """The file of a field: `cells` or `fld:<key>` (a v1 field has no `file` — `cells`)."""
        f = self.field(name)
        return str(f.get("file") or CELLS) if f is not None else CELLS


def model_of(conn: Any, bm_id: int) -> Model:
    """The model `bm_id` (404 `block_model_not_found`, 409 `block_model_broken` — no file of cells). A model with a
    turn, sub-blocks or FRAC is `plain=False` (swath: `stats.swath_sub`); its `grid` is whole."""
    from bf_core.blocks import store
    from bf_core.blocks.model import is_plain

    info = store.block_model_info(conn, bm_id)
    if info.get("layout") == "list":  # the statistics and the swath read the grid of parents
        from bf_core.blocks.model import list_unsupported

        raise list_unsupported(str(info["name"]), "Статистика и профили по сетке")
    grid = dict(info["grid"])
    fields = list(info["fields"])
    plain = is_plain(info) and not any(f.get("role") == "frac" for f in fields)
    if plain:
        for key in ("rotation", "div", "levels"):  # the grid as swath knows it (v1)
            grid.pop(key, None)
    roles = {CELLS, *(str(f.get("file") or CELLS) for f in fields)}
    shas = {role: store.cells_sha(conn, bm_id, role) for role in sorted(roles)}
    return Model(int(info["id"]), str(info["name"]), grid, fields, shas[CELLS], int(info["cells"]), shas, plain)


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
        self.loads = 0  # columns taken from the disk cache

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
            stale = [k for k in self._items if k[0] != key[0] or (k[1] == key[1] and k[2] != key[2])
                     or (k[1] == key[1] and k[3] == key[3] and k[4:] != key[4:])]  # fmt: skip
            for k in stale:  # another project; an older file of the model; the field of a new file `fld:`
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
LIMIT_MIN_MB, LIMIT_MAX_MB = 64, 4096


def set_limit(megabytes: int, cache: ModelCache = MODELS) -> int:
    """The limit of the cache in МБ (ADR 0023 §1: a setting up to 1,2 ГБ for 20 million cells; default 600) → МБ in
    force; `[LIMIT_MIN_MB; LIMIT_MAX_MB]`. What does not fit now goes out at the next put."""
    mb = min(max(int(megabytes), LIMIT_MIN_MB), LIMIT_MAX_MB)
    with cache._lock:
        cache.limit = mb * 1024 * 1024
    return mb


@dataclass
class ModelColumns:
    """The columns of one model read for a request (module docstring)."""

    model: Model
    idx: Any
    numbers: dict[str, Any] = dfield(default_factory=dict)
    category: dict[str, Any] = dfield(default_factory=dict)

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
    """One pass over the row groups of the files: the columns `names` (and `idx`) into arrays of `m.cells` rows."""
    import numpy as np

    from bf_core.blocks import schema as bs
    from bf_core.blocks.model import P, Reader
    from bf_core.storage.blobs import BlobStore

    reader = Reader(BlobStore(project.folder), m.shas or {CELLS: m.sha}, m.fields, m.cells)
    small = bs.cell_count(m.grid) < U32_CELLS
    got = reader.fill(names, p=want_idx, p_dtype=np.uint32 if small else np.uint64)
    out = {n: got[n] for n in names}
    if want_idx:
        out[IDX] = got[P]
    return out


# --- the disk: cache\blocks\<SHA-256 of the file>\<hash of the name>.npy ------------------------------------------


def disk_dir(project: Any) -> Path:
    return Path(project.folder) / "cache" / "blocks"


def _disk_path(project: Any, sha: str, name: str) -> Path:
    key = hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]
    return disk_dir(project) / sha / f"{key}.npy"


def _disk_load(project: Any, sha: str, name: str, rows: int, dtype: Any) -> Any:
    """The column of the disk cache, or None (no file, another size or type, an unreadable file)."""
    import numpy as np

    try:
        a = np.load(_disk_path(project, sha, name), allow_pickle=False)
    except (OSError, ValueError, EOFError):
        return None
    return a if a.ndim == 1 and a.shape[0] == rows and a.dtype == np.dtype(dtype) else None


def _disk_save(project: Any, sha: str, name: str, value: Any) -> None:
    """Write the column beside under a temporary name and rename it; a failure (no space) leaves nothing."""
    import numpy as np

    path = _disk_path(project, sha, name)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as fh:
            np.save(fh, value, allow_pickle=False)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


def clear_disk(project: Any) -> None:
    """Remove the disk cache of the project's columns (derived data: the next read makes it again)."""
    shutil.rmtree(disk_dir(project), ignore_errors=True)


def columns(project: Any, m: Model, names: Sequence[str], *, cache: ModelCache = MODELS,
            disk: bool | None = None) -> ModelColumns:  # fmt: skip
    """`idx` and the fields `names` (the stored names, `Model.field`) of the model, from the cache, the disk
    cache or the files. `disk` — use the disk cache (None: models of `DISK_MIN_ROWS` rows or more)."""
    import numpy as np

    from bf_core.blocks import schema as bs

    wanted = list(dict.fromkeys(names))
    use_disk = m.cells >= DISK_MIN_ROWS if disk is None else disk
    head = (project.uid, m.id, m.sha)

    def file_of(name: str) -> str:
        return CELLS if name == IDX else m.file_of(name)

    def key_of(name: str) -> tuple[Any, ...]:  # (…, column[, SHA-256 of its file `fld:`])
        file = file_of(name)
        return (*head, name) if file == CELLS else (*head, name, m.shas.get(file, file))

    def dtype_of(name: str) -> Any:
        if name == IDX:
            return np.uint32 if bs.cell_count(m.grid) < U32_CELLS else np.uint64
        f = m.field(name)
        return np.int32 if f is not None and f["kind"] == bs.CATEGORY else np.float64

    got: dict[str, Any] = {}
    with cache.gate(head):  # concurrent requests of one model read its files once
        for key in (IDX, *wanted):
            hit = cache.peek(key_of(key))
            if hit is not None:
                got[key] = hit
        missing = [n for n in (IDX, *wanted) if n not in got]
        if use_disk:
            for key in list(missing):
                value = _disk_load(project, m.shas.get(file_of(key), m.sha), key, m.cells, dtype_of(key))
                if value is not None:
                    got[key] = _frozen(value)
                    cache.put(key_of(key), got[key])
                    with cache._lock:
                        cache.loads += 1
                    missing.remove(key)
        if missing:
            with cache._lock:
                cache.misses += len(missing)
                cache.reads += 1
            fresh = _read(project, m, [n for n in missing if n != IDX], IDX in missing)
            for key, value in fresh.items():
                got[key] = _frozen(value)
                cache.put(key_of(key), got[key])
                if use_disk:
                    _disk_save(project, m.shas.get(file_of(key), m.sha), key, got[key])
    out = ModelColumns(m, got[IDX])
    for name in wanted:
        f = m.field(name)
        if f is not None and f["kind"] == bs.CATEGORY:
            out.category[name] = got[name]
        else:
            out.numbers[name] = got[name]
    return out
