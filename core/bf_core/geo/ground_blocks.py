"""The lattice of the ground filter, its blocks and the strips of its stages (`geo/ground.py`, ADR 0014 §4).

Lattice. Cells of CR/2 anchored to the whole origin of the cloud (a multiple of CR), `PAD` particles of empty
lattice around the points. It is cut into blocks of `span` cells a side, row by row; a block is computed with
a margin around its core and gives the classes of the points of its core. `split` — the points of every core
(a counting sort by block), `candidates` — the points a block may need (its core and the cores within its
margin), `index` — stage 1: the points of a grid by its cells.

Strips. `Pool.strips` runs a kernel over fixed strips of rows (or of points) on Python threads; the kernels
release the GIL, a strip writes only its own outputs — the result does not depend on the number of threads.

How full the lattice is. `presence` — the cells inside the data and those of them that hold a point, from the
index alone (`ground_kernels.near_rows`, `fill_rows`): the cores of the blocks one by one, every cell once.
It is what `classify_ground` refuses a cloud too sparse for its CR by, before any block is computed.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import ground_kernels as gk

PAD = 4  # particles of empty lattice around the cloud
STRIP_ROWS = 64  # rows of cells (of particles) in a strip
STRIP_POINTS = 1 << 18  # points in a strip


class Pool:
    """Strips of a stage on Python threads (the kernels release the GIL); one thread — in place."""

    def __init__(self, threads: int) -> None:
        self.threads = max(1, int(threads))
        self._ex = ThreadPoolExecutor(self.threads, thread_name_prefix="bf-ground") if self.threads > 1 else None

    def strips(self, fn: Callable[[int, int], Any], n: int, size: int) -> list[Any]:
        ranges = [(lo, min(lo + size, n)) for lo in range(0, n, size)]
        if self._ex is None or len(ranges) < 2:
            return [fn(lo, hi) for lo, hi in ranges]
        return list(self._ex.map(lambda r: fn(r[0], r[1]), ranges))

    def even(self, fn: Callable[[int, int], Any], n: int) -> list[Any]:
        """One strip a thread — for the sweeps of the cloth, whose result does not depend on the strips."""
        return self.strips(fn, n, -(-n // self.threads))

    def close(self) -> None:
        if self._ex is not None:
            self._ex.shutdown(wait=True, cancel_futures=True)


@dataclass(frozen=True)
class Lattice:
    ox: float  # whole origin of the cloud (a multiple of CR), project coordinates
    oy: float
    c: float  # cell, CR/2
    ncx: int  # cells of the whole lattice (even)
    ncy: int


@dataclass(frozen=True)
class Block:
    cx0: int  # the cells of the block with its margin
    cy0: int
    ncx: int
    ncy: int
    core: tuple[int, int, int, int]  # col0, row0, col1, row1 of the core, in the cells of the block


def lattice(box: list[Any], cr: float) -> Lattice:
    """The lattice over the extents of the strips of points (`ground_kernels.extent`)."""
    xmin, xmax = min(b[0] for b in box), max(b[1] for b in box)
    ymin, ymax = min(b[2] for b in box), max(b[3] for b in box)
    ox = (math.floor(xmin / cr) - PAD) * cr
    oy = (math.floor(ymin / cr) - PAD) * cr
    ncx = 2 * (int(math.floor((xmax - ox) / cr)) + 1 + PAD)
    ncy = 2 * (int(math.floor((ymax - oy) / cr)) + 1 + PAD)
    return Lattice(ox, oy, cr / 2.0, ncx, ncy)


def blocks(lat: Lattice, span: int, margin: int) -> list[Block]:
    """Blocks of `span` cells a side, row by row, each with `margin` cells around its core."""
    out = []
    for by in range(-(-lat.ncy // span)):
        for bx in range(-(-lat.ncx // span)):
            c0, r0 = bx * span, by * span
            c1, r1 = min(c0 + span, lat.ncx), min(r0 + span, lat.ncy)
            x0, y0 = max(c0 - margin, 0), max(r0 - margin, 0)
            x1, y1 = min(c1 + margin, lat.ncx), min(r1 + margin, lat.ncy)
            out.append(Block(x0, y0, x1 - x0, y1 - y0, (c0 - x0, r0 - y0, c1 - x0, r1 - y0)))
    return out


def split(pts: np.ndarray, lat: Lattice, count: int, span: int, pool: Pool) -> list[Any]:
    """The numbers of the points of the core of every block (None — the block is empty)."""
    n = len(pts)
    if count == 1:
        return [np.arange(n, dtype=np.int32)]
    nbx, nby = -(-lat.ncx // span), -(-lat.ncy // span)
    key = np.empty(n, np.int32)
    pool.strips(lambda lo, hi: gk.block_keys(pts, lo, hi, lat.ox, lat.oy, lat.c, span, nbx, nby, key), n, STRIP_POINTS)
    start, order = gk.counting_sort(key, np.arange(n, dtype=np.int32), nbx * nby)
    return [order[start[b] : start[b + 1]] if start[b + 1] > start[b] else None for b in range(nbx * nby)]


def candidates(k: int, lists: list[Any], lat: Lattice, span: int, reach: int) -> np.ndarray:
    """The points block `k` may need: its core and the cores of the blocks within its margin (`reach` blocks)."""
    if len(lists) == 1:
        return np.asarray(lists[0])
    nbx, nby = -(-lat.ncx // span), -(-lat.ncy // span)
    by, bx = divmod(k, nbx)
    near = [
        lists[j * nbx + i]
        for j in range(max(by - reach, 0), min(by + reach + 1, nby))
        for i in range(max(bx - reach, 0), min(bx + reach + 1, nbx))
        if lists[j * nbx + i] is not None
    ]
    return np.concatenate(near)


def index(pts: np.ndarray, cand: np.ndarray, grid: tuple[Any, ...], pool: Pool) -> tuple[np.ndarray, np.ndarray]:
    """Stage 1: the points `cand` by the cells of a grid (ox, oy, c, cx0, cy0, ncx, ncy) → (start, order)."""
    ox, oy, c, cx0, cy0, ncx, ncy = grid
    cid = np.empty(len(cand), np.int32)
    pool.strips(
        lambda lo, hi: gk.cell_ids(pts, cand, lo, hi, ox, oy, c, cx0, cy0, ncx, ncy, cid), len(cand), STRIP_POINTS
    )
    start, order = gk.counting_sort(cid, cand, ncx * ncy)
    return start, order


def presence(
    pts: np.ndarray,
    lists: list[Any],
    live: list[int],
    lat: Lattice,
    grids: list[Block],
    pool: Pool,
    tick: Callable[[], None] | None = None,
) -> tuple[int, int, tuple[np.ndarray, np.ndarray] | None]:
    """(cells inside the data, those of them that hold a point, the index of the last block) over the cores
    of the blocks `live`: every cell of the lattice is counted once, every point counts. A cell is inside when
    the 6 × 6 particles around it have a point within 2·CR; near the border of a core that is judged by the
    points of the core alone, so a rim of cells of a sparse cloud is left out of both numbers — the share is
    not moved by it. `tick()` is called before every block and may raise to cancel.

    The index of a block is that of its core: for the only block of a cloud (no margin) it is the index
    `ground._run_block` needs — the caller hands it over instead of building it again."""
    inside = filled = 0
    found = None
    for k in live:
        if tick is not None:
            tick()
        col0, row0, col1, row1 = grids[k].core
        ncx, ncy = col1 - col0, row1 - row0
        grid = (lat.ox, lat.oy, lat.c, grids[k].cx0 + col0, grids[k].cy0 + row0, ncx, ncy)
        found = None  # one index at a time
        found = index(pts, np.asarray(lists[k]), grid, pool)
        inside_k, filled_k = _fill(found[0], ncx, ncy, pool)
        inside += inside_k
        filled += filled_k
    return inside, filled, found


def _fill(start: np.ndarray, ncx: int, ncy: int, pool: Pool) -> tuple[int, int]:
    near = np.empty((ncy // 2 + 1, ncx // 2 + 1), np.uint8)
    pool.strips(lambda a, b: gk.near_rows(start, ncx, ncy, a, b, near), len(near), STRIP_ROWS)
    counts = pool.strips(lambda a, b: gk.fill_rows(start, near, ncx, a, b), ncy // 2, STRIP_ROWS)
    return sum(int(c[0]) for c in counts), sum(int(c[1]) for c in counts)
