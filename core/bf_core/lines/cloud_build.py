"""Building a point cloud from a source (ADR 0014 §1 «Порядок хранения», «Сборка при импорте»).

**The storage order** is the order of the Morton codes of the 1 m cells of the points (`geo/cloud_kernels.py`:
X and Y of the project grid, counted from −2³⁰ m, so the order does not depend on the extent of the cloud) and,
in a cell, the order of the source. A run of codes with a common prefix is a square of the grid.

**Parts** (`cut`). The square of the whole grid is quartered while it holds more than `PART_POINTS` points or
is wider than 16 384 m; a square that fits is a part. Neighbouring quarters of one square that are parts
themselves are joined while they fit together, so a part is a run of the storage order that lies inside one
square of the grid and holds no more than a quarter of what its square does at a common density. A 1 m cell
with more points than a part takes (a scan of one spot) is cut in the order of the source. A part is at most
16 384 m wide: float32 offsets from its whole-metre origin keep 0.25 mm (the origin is the centre of the
extent; along Z — the median height when the heights of a part spread wider than that, a stray return).

**Levels** (`finish_part`). Inside a part the points go by falling rank (`level_ranks`): first the first
points of the 32 m cells, then of the 8, 2 and 0.5 m cells, then the rest, the storage order kept among equals.
Level j of a part is its first `n[j]` points: one point in every cell of `LEVELS[j]` metres, the levels nest.

`build_cloud` does it in memory — small clouds, tests, and the reference the streamed build is checked
against.

**The streamed build** (`stage_cloud`) gives the same cloud from a source of any size in about 300 MB of
memory, going through the source twice by its portions:
1. the points are counted by tiles — squares of 64 m, i.e. by the upper bits of their codes (a cloud spread so
   thin that it has more than `MAX_TILES` tiles is counted by tiles of 128 m … 2048 m: the tiles are only the
   buckets of the sort, the cloud does not depend on their size); the extent, the classes;
2. the points are put into a temporary file tile after tile, in the order of the source inside a tile (a sort
   by counting: the place of every tile in the file is known from the first pass);
3. the tiles are cut into parts (`cut` over the tiles), a part is read from the temporary file, sorted by its
   codes and finished; a tile with more points than a part takes is sorted the same way by its 1 m cells —
   in memory, or through a second temporary file when it is larger than a portion — and cut inside;
4. the columns of the parts are appended to four temporary files of the blob store with their SHA-256.
Files are read and written through `seek`, never mapped: a mapped file counts into the memory of the job, and on
Windows it cannot be removed while it is mapped. A cancel (`tick` raising) or a failure leaves no file behind.

numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Callable, Generator, Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import numpy as np
import sqlalchemy as sa

from bf_core.formats.errors import FormatError
from bf_core.geo import cloud_kernels as ck
from bf_core.storage.blobs import BlobStore

from . import store as ls
from .cloud import (
    BASE_ROLES,
    CLASS_COUNT,
    LEVELS,
    PART_POINTS,
    ROLES,
    Box,
    ChunkSource,
    CloudPart,
    Points,
    Staged,
    StagedCloud,
    commit_cloud,
)

#: A part lies in a square of at most 2¹⁴ = 16 384 m: the lowest 28 bits of a code.
MAX_NODE_BITS = 28
MAX_SPAN_M = 16_384.0
RANKS = len(LEVELS) - 1
TILE_SHIFT = 6  # a tile of the streamed build: 2⁶ = 64 m
MAX_TILE_SHIFT = 11  # … at most 2048 m: 4 million 1 m cells in a tile
MAX_TILES = 1 << 16
CHUNK = 1 << 20  # records read back from a temporary file at a time
#: More parts than this is not a cloud but points scattered over a continent (junk coordinates): refused, so
#: that `meta.cloud` stays small (150 bytes a part; 200 million points of a survey are about 600 parts).
MAX_PARTS = 8192
#: A point in the temporary files of the streamed build (30 bytes).
REC = np.dtype([("xyz", "<f8", (3,)), ("seq", "<u4"), ("cls", "u1"), ("ret", "u1")])

assert all(LEVELS[j] == LEVELS[1] * 4 ** (j - 1) for j in range(1, len(LEVELS)))  # what `level_ranks` relies on


def source_changed() -> FormatError:
    return FormatError(
        "source_changed",
        "Источник облака изменился, пока оно загружалось.",
        "Дождитесь, пока файл будет записан полностью, и загрузите облако заново.",
    )


def too_wide() -> FormatError:
    return FormatError(
        "cloud_too_wide",
        "Точки облака разбросаны по слишком большой площади.",
        "Проверьте колонки X, Y, Z и единицы координат: точки должны быть в метрах одной системы координат.",
    )


def no_points() -> FormatError:
    return FormatError(
        "no_points",
        "В облаке нет ни одной точки с координатами.",
        "Проверьте файл: у точек должны быть числовые X, Y и Z в метрах.",
    )


# --- parts -----------------------------------------------------------------------------------------------


def cut(codes: np.ndarray, cum: np.ndarray, limit: int, max_bits: int = MAX_NODE_BITS) -> list[tuple[int, int]]:
    """The squares of the grid the points are cut into. `codes` — different Morton codes, ascending (int64);
    `cum[i]` — the number of points before code i (`len(codes) + 1` numbers). Returns runs `(lo, hi)` of code
    indices, in order, covering all of them: a run holds at most `limit` points and lies in a square of
    `2^(max_bits / 2)` metres — or is one code (which may hold more than `limit`)."""
    out: list[tuple[int, int]] = []

    def node(lo: int, hi: int, bits: int) -> bool:
        """The codes [lo, hi) share everything above `bits`. True — the node became one run."""
        if hi - lo == 1 or (int(cum[hi] - cum[lo]) <= limit and bits <= max_bits):
            out.append((lo, hi))
            return True
        step = bits - 2
        prefix = (int(codes[lo]) >> bits) << 2
        edges = [lo]
        edges += [lo + int(np.searchsorted(codes[lo:hi], (prefix + q) << step)) for q in (1, 2, 3)]
        edges.append(hi)
        quarters = [(a, b) for a, b in zip(edges[:-1], edges[1:], strict=True) if b > a]
        if len(quarters) == 1:  # the points lie in one quarter: the same node, smaller
            return node(lo, hi, step)
        run = -1  # the place in `out` of a quarter that may take the next one
        for a, b in quarters:
            if not node(a, b, step):
                run = -1
            elif run >= 0 and bits <= max_bits and int(cum[b] - cum[out[run][0]]) <= limit:
                out.pop()
                out[run] = (out[run][0], b)
            else:
                run = len(out) - 1
        return False

    if len(codes):
        node(0, len(codes), ck.CODE_BITS)
    return out


def part_rows(codes: np.ndarray, cum: np.ndarray, limit: int) -> list[tuple[int, int]]:
    """Rows `(start, stop)` of the parts of points in storage order (`cut`; a code with more than `limit`
    points is cut every `limit` points)."""
    out: list[tuple[int, int]] = []
    for lo, hi in cut(codes, cum, limit):
        start, stop = int(cum[lo]), int(cum[hi])
        while stop - start > limit:
            out.append((start, start + limit))
            start += limit
        out.append((start, stop))
    return out


def finish_part(
    start: int, xyz: np.ndarray, seq: np.ndarray, cls: np.ndarray, ret: np.ndarray
) -> tuple[CloudPart, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The points of a part (in storage order) put by falling level; the part with the sizes of its levels,
    its extent and origin; the four columns as they are stored."""
    n = len(xyz)
    rank = np.empty(n, dtype=np.uint8)
    ck.level_ranks(xyz, LEVELS[1], RANKS, rank)
    order = np.empty(n, dtype=np.int64)
    sizes = np.empty(RANKS + 1, dtype=np.int64)
    ck.rank_order(rank, RANKS, order, sizes)
    xyz = xyz[order]
    lo, hi = xyz.min(axis=0), xyz.max(axis=0)
    centre = np.round((lo + hi) * 0.5)
    if hi[2] - lo[2] > MAX_SPAN_M:
        centre[2] = np.round(np.median(xyz[:, 2]))
    box: Box = (float(lo[0]), float(lo[1]), float(lo[2]), float(hi[0]), float(hi[1]), float(hi[2]))
    origin = (float(centre[0]) + 0.0, float(centre[1]) + 0.0, float(centre[2]) + 0.0)  # −0.0 → 0.0
    part = CloudPart(start, tuple(int(v) for v in sizes), box, origin)
    return part, xyz, seq[order], cls[order], ret[order]


# --- the points of a source ------------------------------------------------------------------------------


def columns(chunk: Points, base: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The columns of a portion as the builder takes them (`cloud.Points`); `base` — the points of the stream
    before it."""
    xyz = np.ascontiguousarray(chunk.xyz, dtype=np.float64)
    if xyz.ndim != 2 or xyz.shape[1] != 3:
        raise ValueError("cloud: the points of a portion are triples X, Y, Z")
    n = len(xyz)

    def column(values: Any, dtype: str, what: str) -> np.ndarray:
        if values is None:
            return np.zeros(n, dtype=dtype)
        arr = np.ascontiguousarray(values, dtype=dtype)
        if arr.shape != (n,):
            raise ValueError(f"cloud: {what} is given for {arr.shape} of {n} points")
        return arr

    if chunk.seq is None:
        if base + n > 1 << 32:
            raise ValueError("cloud: more than 2³² records in a source")
        seq = np.arange(base, base + n, dtype=np.int64).astype("<u4")
    else:
        seq = column(chunk.seq, "<u4", "the record number")
    return xyz, seq, column(chunk.cls, "u1", "the class"), column(chunk.ret, "u1", "the return")


def distinct(codes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The different codes of an ascending array and the number of codes before each (one number more)."""
    n = len(codes)
    first = np.flatnonzero(np.concatenate([[True], codes[1:] != codes[:-1]])) if n else np.zeros(0, dtype=np.int64)
    return codes[first], np.concatenate([first, [n]]).astype(np.int64)


@dataclass
class CloudData:
    """A cloud in memory, in storage order."""

    xyz: np.ndarray
    seq: np.ndarray
    cls: np.ndarray
    ret: np.ndarray
    parts: list[CloudPart]
    bbox: Box
    classes: dict[int, int]
    skipped: int = 0

    @property
    def count(self) -> int:
        return len(self.xyz)


def build_cloud(chunks: Iterable[Points], *, part_points: int = PART_POINTS) -> CloudData:
    """The cloud of the points of a source, built in memory (about 100 bytes a point). No points with
    coordinates — `FormatError("no_points")`."""
    taken = []
    base = 0
    for chunk in chunks:
        taken.append(columns(chunk, base))
        base += len(taken[-1][0])
    if not taken:
        raise no_points()
    xyz, seq, cls, ret = (np.concatenate([t[i] for t in taken]) for i in range(4))
    del taken
    codes = np.empty(len(xyz), dtype=np.int64)
    box = np.array([np.inf] * 3 + [-np.inf] * 3)
    valid = int(ck.morton_codes(xyz, codes, box))
    if not valid:
        raise no_points()
    skipped = len(xyz) - valid
    order = np.argsort(codes, kind="stable")[skipped:]  # the points without a place have the code −1
    codes = codes[order]
    parts: list[CloudPart] = []
    out: list[tuple[np.ndarray, ...]] = []
    rows = part_rows(*distinct(codes), part_points)
    if len(rows) > MAX_PARTS:
        raise too_wide()
    for start, stop in rows:
        take = order[start:stop]
        part, *cols = finish_part(start, xyz[take], seq[take], cls[take], ret[take])
        parts.append(part)
        out.append(tuple(cols))
    xyz, seq, cls, ret = (np.concatenate([t[i] for t in out]) for i in range(4))
    counts = np.bincount(cls, minlength=CLASS_COUNT)
    classes = {int(code): int(counts[code]) for code in np.flatnonzero(counts)}
    return CloudData(xyz, seq, cls, ret, parts, tuple(float(v) for v in box), classes, skipped)  # type: ignore[arg-type]


def stage_data(store: BlobStore, data: CloudData, tick: Callable[[], None] | None = None) -> StagedCloud:
    """The columns of a cloud built in memory as temporary files (no database access)."""
    staged = StagedCloud({}, data.count, list(data.parts), data.bbox, dict(data.classes), data.skipped)
    try:
        for role, arr in zip(BASE_ROLES, (data.xyz, data.seq, data.cls, data.ret), strict=True):
            staged.files[role] = store.stage_stream(ls._chunks(np.ascontiguousarray(arr, dtype=ROLES[role]), tick))
    except BaseException:
        staged.discard()
        raise
    return staged


def put_cloud(
    conn: sa.Connection, store: BlobStore, chunks: Iterable[Points], *, file_id: int, name: str, **kw: Any
) -> int:
    """Build in memory, stage and commit in one go (small clouds, tests). Returns the id of the layer."""
    staged = stage_data(store, build_cloud(chunks))
    try:
        return commit_cloud(conn, store, staged, file_id=file_id, name=name, **kw)
    finally:
        staged.discard()


# --- the streamed build ----------------------------------------------------------------------------------


class _Columns:
    """The four columns of a cloud written part after part into temporary files of the blob store."""

    def __init__(self, store: BlobStore) -> None:
        store.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.paths = {role: store.tmp_dir / uuid.uuid4().hex for role in BASE_ROLES}
        self.files: dict[str, IO[bytes]] = {}
        self.sha = {role: hashlib.sha256() for role in BASE_ROLES}
        self.size = dict.fromkeys(BASE_ROLES, 0)
        try:
            for role, path in self.paths.items():
                self.files[role] = open(path, "wb")
        except BaseException:
            self.discard()
            raise

    def write(self, *arrays: np.ndarray) -> None:
        for role, arr in zip(BASE_ROLES, arrays, strict=True):
            raw = memoryview(np.ascontiguousarray(arr, dtype=ROLES[role])).cast("B")
            self.files[role].write(raw)
            self.sha[role].update(raw)
            self.size[role] += len(raw)

    def close(self) -> dict[str, Staged]:
        for fh in self.files.values():
            fh.flush()
            os.fsync(fh.fileno())
            fh.close()
        self.files.clear()
        return {role: (self.paths[role], self.sha[role].hexdigest(), self.size[role]) for role in BASE_ROLES}

    def discard(self) -> None:
        for fh in self.files.values():
            fh.close()
        self.files.clear()
        for path in self.paths.values():
            path.unlink(missing_ok=True)


class _Scatter:
    """A sort by counting into a file: the records of bucket b go to `[cum[b], cum[b + 1])`, in the order they
    come. Records that do not fit the counts mean the source is not what it was when it was counted."""

    def __init__(self, fh: IO[bytes], counts: np.ndarray) -> None:
        self.fh = fh
        self.counts = counts
        self.cum = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
        self.fill = np.zeros(len(counts), dtype=np.int64)
        fh.truncate(int(self.cum[-1]) * REC.itemsize)

    def put(self, rec: np.ndarray, bucket: np.ndarray) -> None:
        if not len(rec):
            return
        # buckets are few (≤ 65 536 tiles as a rule): 16-bit keys are sorted by counting, not by comparing
        keys = bucket.astype(np.uint16) if len(self.counts) <= 1 << 16 else bucket
        order = np.argsort(keys, kind="stable")
        sorted_buckets = bucket[order]
        raw = memoryview(rec[order].tobytes())
        first = np.flatnonzero(np.concatenate([[True], sorted_buckets[1:] != sorted_buckets[:-1]]))
        length = np.diff(np.concatenate([first, [len(rec)]]))
        b = sorted_buckets[first]
        dest = self.cum[b] + self.fill[b]
        self.fill[b] += length
        if np.any(self.fill[b] > self.counts[b]):
            raise source_changed()
        # runs that follow one another in the file are written at once
        gap = np.flatnonzero(np.concatenate([[True], dest[1:] != dest[:-1] + length[:-1]]))
        ends = np.concatenate([gap[1:], [len(first)]])
        size = REC.itemsize
        for g, e in zip(gap.tolist(), ends.tolist(), strict=True):
            a = int(first[g])
            z = int(first[e - 1] + length[e - 1])
            self.fh.seek(int(dest[g]) * size)
            piece = raw[a * size : z * size]
            while len(piece):  # an unbuffered file may take a write in several goes
                piece = piece[self.fh.write(piece) :]

    def done(self) -> None:
        if not np.array_equal(self.fill, self.counts):
            raise source_changed()


def _records(xyz: np.ndarray, seq: np.ndarray, cls: np.ndarray, ret: np.ndarray) -> np.ndarray:
    rec = np.empty(len(xyz), dtype=REC)
    rec["xyz"], rec["seq"], rec["cls"], rec["ret"] = xyz, seq, cls, ret
    return rec


def _codes(xyz: np.ndarray, box: np.ndarray | None = None) -> tuple[np.ndarray, int]:
    codes = np.empty(len(xyz), dtype=np.int64)
    return codes, int(ck.morton_codes(xyz, codes, np.array([np.inf] * 3 + [-np.inf] * 3) if box is None else box))


def _merge(a: np.ndarray, na: np.ndarray, b: np.ndarray, nb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two lists of keys with their counts as one, the keys ascending."""
    keys, where = np.unique(np.concatenate([a, b]), return_inverse=True)
    counts = np.zeros(len(keys), dtype=np.int64)
    np.add.at(counts, where, np.concatenate([na, nb]))
    return keys, counts


@dataclass
class _Survey:
    """What the first pass over a source finds."""

    shift: int  # a tile is 2^shift metres
    tiles: np.ndarray  # the codes of the tiles that have points, ascending
    counts: np.ndarray
    box: np.ndarray
    classes: np.ndarray
    seen: int  # records of the source
    total: int  # … that have a place


def _survey(source: ChunkSource, max_tiles: int, step: Callable[[int], None]) -> _Survey:
    shift = TILE_SHIFT
    tiles = counts = np.zeros(0, dtype=np.int64)
    box = np.array([np.inf] * 3 + [-np.inf] * 3)
    classes = np.zeros(CLASS_COUNT, dtype=np.int64)
    seen = total = 0
    for chunk in source():
        step(seen)
        xyz, _seq, cls, _ret = columns(chunk, seen)
        seen += len(xyz)
        codes, valid = _codes(xyz, box)
        if not valid:
            continue
        if valid < len(xyz):
            keep = codes >= 0
            codes, cls = codes[keep], cls[keep]
        total += valid
        classes += np.bincount(cls, minlength=CLASS_COUNT)
        tiles, counts = _merge(tiles, counts, *np.unique(codes >> (2 * shift), return_counts=True))
        while len(tiles) > max_tiles and shift < MAX_TILE_SHIFT:
            shift += 1
            tiles, counts = _merge(tiles >> 2, counts, tiles[:0], counts[:0])
        if len(tiles) > 64 * MAX_PARTS:  # a part is at most 8 × 8 of the widest tiles
            raise too_wide()
    return _Survey(shift, tiles, counts, box, classes, seen, total)


def _read(fh: IO[bytes], first: int, count: int) -> np.ndarray:
    fh.seek(first * REC.itemsize)
    rec = np.fromfile(fh, dtype=REC, count=count)
    if len(rec) != count:
        raise OSError("cloud: the temporary file of the build is shorter than it was written")
    return rec


def _big_tile(
    fh: IO[bytes], first: int, count: int, shift: int, limit: int, chunk: int, spare: Path, tick: Callable[[], None]
) -> Generator[np.ndarray, None, None]:
    """The records of the parts of a tile that holds more points than a part takes: sorted by the 1 m cells
    of the tile, cut inside it (`part_rows`)."""
    if count <= chunk:
        rec = _read(fh, first, count)
        codes = _codes(np.ascontiguousarray(rec["xyz"]))[0]
        order = np.argsort(codes, kind="stable")
        rec = rec[order]
        for a, b in part_rows(*distinct(codes[order]), limit):
            yield rec[a:b]
        return
    mask = (1 << (2 * shift)) - 1
    cells = np.zeros(mask + 1, dtype=np.int64)
    prefix = 0
    for at in range(0, count, chunk):
        tick()
        codes = _codes(np.ascontiguousarray(_read(fh, first + at, min(chunk, count - at))["xyz"]))[0]
        prefix = int(codes[0]) & ~mask
        cells += np.bincount(codes & mask, minlength=mask + 1)
    used = np.flatnonzero(cells)
    rank = np.zeros(mask + 1, dtype=np.int64)
    rank[used] = np.arange(len(used))
    with open(spare, "w+b", buffering=0) as out:
        scatter = _Scatter(out, cells[used])
        for at in range(0, count, chunk):
            tick()
            rec = _read(fh, first + at, min(chunk, count - at))
            scatter.put(rec, rank[_codes(np.ascontiguousarray(rec["xyz"]))[0] & mask])
        scatter.done()
        for a, b in part_rows(used + prefix, scatter.cum, limit):
            yield _read(out, a, b - a)


def stage_cloud(
    store: BlobStore,
    source: ChunkSource,
    tick: Callable[[float], None] | None = None,
    *,
    expected: int | None = None,
    part_points: int = PART_POINTS,
    max_tiles: int = MAX_TILES,
    chunk: int = CHUNK,
) -> StagedCloud:
    """The cloud of a source as temporary files of the blob store (no database access): the streamed build of
    the module docstring, memory does not grow with the cloud. `tick(done)` is called at least once a portion
    and a part with the share of the work done (0…1) and may raise to cancel: no file is left then.
    `expected` — the records of the source when known (the share during the first pass)."""
    tmp = store.tmp_dir / uuid.uuid4().hex
    spare = store.tmp_dir / uuid.uuid4().hex
    out: _Columns | None = None

    def share(base: float, width: float, done: int, of: int | None) -> None:
        if tick is not None:
            tick(base + width * (min(1.0, done / of) if of else 0.0))

    try:
        survey = _survey(source, max_tiles, lambda seen: share(0.0, 0.25, seen, expected))
        if not survey.total:
            raise no_points()
        shift = survey.shift
        store.tmp_dir.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w+b", buffering=0) as fh:
            scatter = _Scatter(fh, survey.counts)
            seen = 0
            for portion in source():
                share(0.25, 0.35, seen, survey.seen)
                xyz, seq, cls, ret = columns(portion, seen)
                seen += len(xyz)
                codes, valid = _codes(xyz)
                rec = _records(xyz, seq, cls, ret)
                if valid < len(xyz):
                    keep = codes >= 0
                    codes, rec = codes[keep], rec[keep]
                tile = codes >> (2 * shift)
                bucket = np.minimum(np.searchsorted(survey.tiles, tile), len(survey.tiles) - 1)
                if np.any(survey.tiles[bucket] != tile):
                    raise source_changed()
                scatter.put(rec, bucket)
            if seen != survey.seen:
                raise source_changed()
            scatter.done()

            out = _Columns(store)
            parts: list[CloudPart] = []
            done = 0

            def finish(rec: np.ndarray) -> None:
                nonlocal done
                assert out is not None
                share(0.6, 0.4, done, survey.total)
                if len(parts) >= MAX_PARTS:
                    raise too_wide()
                part, *cols = finish_part(done, np.ascontiguousarray(rec["xyz"]), rec["seq"], rec["cls"], rec["ret"])
                out.write(*cols)
                parts.append(part)
                done += len(rec)

            squares = cut(survey.tiles << (2 * shift), scatter.cum, part_points)
            if len(squares) > MAX_PARTS:
                raise too_wide()
            for lo, hi in squares:
                first, count = int(scatter.cum[lo]), int(scatter.cum[hi] - scatter.cum[lo])
                if count > part_points:  # one tile
                    inside = _big_tile(
                        fh, first, count, shift, part_points, chunk, spare, lambda: share(0.6, 0.4, done, survey.total)
                    )
                    with closing(inside):  # a cancel closes the second temporary file before it is removed
                        for rec in inside:
                            finish(rec)
                    continue
                rec = _read(fh, first, count)
                codes = _codes(np.ascontiguousarray(rec["xyz"]))[0]
                finish(rec[np.argsort(codes, kind="stable")])
        files = out.close()
        share(1.0, 0.0, 1, 1)
    except BaseException:
        if out is not None:
            out.discard()
        raise
    finally:
        tmp.unlink(missing_ok=True)
        spare.unlink(missing_ok=True)
    classes = {int(code): int(survey.classes[code]) for code in np.flatnonzero(survey.classes)}
    box: Box = tuple(float(v) for v in survey.box)  # type: ignore[assignment]
    return StagedCloud(files, survey.total, parts, box, classes, survey.seen - survey.total)
