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
against. numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

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
    CloudChunk,
    CloudPart,
    StagedCloud,
    commit_cloud,
)

#: A part lies in a square of at most 2¹⁴ = 16 384 m: the lowest 28 bits of a code.
MAX_NODE_BITS = 28
MAX_SPAN_M = 16_384.0
RANKS = len(LEVELS) - 1

assert all(LEVELS[j] == LEVELS[1] * 4 ** (j - 1) for j in range(1, len(LEVELS)))  # what `level_ranks` relies on


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


def columns(chunk: CloudChunk, base: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The columns of a portion as the builder takes them (`CloudChunk`); `base` — the points of the stream
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


def build_cloud(chunks: Iterable[CloudChunk], *, part_points: int = PART_POINTS) -> CloudData:
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
    for start, stop in part_rows(*distinct(codes), part_points):
        rows = order[start:stop]
        part, *cols = finish_part(start, xyz[rows], seq[rows], cls[rows], ret[rows])
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
    conn: sa.Connection, store: BlobStore, chunks: Iterable[CloudChunk], *, file_id: int, name: str, **kw: Any
) -> int:
    """Build in memory, stage and commit in one go (small clouds, tests). Returns the id of the layer."""
    staged = stage_data(store, build_cloud(chunks))
    try:
        return commit_cloud(conn, store, staged, file_id=file_id, name=name, **kw)
    finally:
        staged.discard()
