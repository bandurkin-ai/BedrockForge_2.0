"""Lines where two wireframes cross, and the grid broad phase they share with `clip_sheet.py` (ТЗ 7.23
«Линии пересечения»; ADR 0015 §7).

    boxes(v, t)                          lower and upper corners of every triangle
    Grid.build(lo, hi, box_lo, box_hi)   triangles listed by the cells their boxes cover
    overlapping_centroids(p, t, tick=)   triangles whose centroid (2D) lies strictly inside another one

The grid: cells about twice the typical triangle, every triangle listed in the cells its box covers (a
triangle covering more than MAX_CELLS_PER_TRIANGLE cells is kept aside as "big" and tested against
everything); keys sorted, runs found by binary search. Ported from BF `geo/solids.py` without `prange` (ADR
0014): the scans run on one thread in portions of PORTION triangles, `tick(fraction, message)` between the
portions reports progress and may raise to cancel.

numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from bf_core.jit import njit

Tick = Callable[[float, str], None] | None

MAX_CELLS_PER_TRIANGLE = 512
PORTION = 1 << 17  # triangles scanned between two calls of `tick`
MAX_GRID = 2**20  # cells along an axis


def _tick(tick: Tick, fraction: float, message: str) -> None:
    if tick is not None:
        tick(float(min(max(fraction, 0.0), 1.0)), message)


# --- the grid --------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True, inline="always")
def _cell_index(x, g, cell, n):  # type: ignore[no-untyped-def]
    c = int((x - g) // cell)
    if c < 0:
        return 0
    if c >= n:
        return n - 1
    return c


@njit(cache=True, nogil=True)
def _grid_counts(lo, hi, cell, gmin, dims):  # type: ignore[no-untyped-def]
    m = lo.shape[0]
    out = np.empty(m, dtype=np.int64)
    for i in range(m):
        n = 1
        for k in range(3):
            n *= _cell_index(hi[i, k], gmin[k], cell, dims[k]) - _cell_index(lo[i, k], gmin[k], cell, dims[k]) + 1
        out[i] = n
    return out


@njit(cache=True, nogil=True)
def _grid_fill(lo, hi, cell, gmin, dims, starts, keys, ids, big):  # type: ignore[no-untyped-def]
    for i in range(lo.shape[0]):
        if big[i]:
            continue
        x0 = _cell_index(lo[i, 0], gmin[0], cell, dims[0])
        y0 = _cell_index(lo[i, 1], gmin[1], cell, dims[1])
        z0 = _cell_index(lo[i, 2], gmin[2], cell, dims[2])
        x1 = _cell_index(hi[i, 0], gmin[0], cell, dims[0])
        y1 = _cell_index(hi[i, 1], gmin[1], cell, dims[1])
        z1 = _cell_index(hi[i, 2], gmin[2], cell, dims[2])
        p = starts[i]
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    keys[p] = (x * dims[1] + y) * dims[2] + z
                    ids[p] = i
                    p += 1


def boxes(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lo = v[t[:, 0]]
    hi = lo.copy()
    for k in (1, 2):
        corner = v[t[:, k]]
        np.minimum(lo, corner, out=lo)
        np.maximum(hi, corner, out=hi)
    return lo, hi


@dataclass(frozen=True)
class Grid:
    cell: float
    gmin: np.ndarray  # float64 (3,): the lower corner
    dims: np.ndarray  # int64 (3,)
    ukeys: np.ndarray  # int64: the cells that hold triangles, ascending
    runs: np.ndarray  # int64 len(ukeys) + 1: their runs in `ids`
    ids: np.ndarray  # int64: triangles, cell by cell
    big: np.ndarray  # int64: triangles too large to be listed

    @staticmethod
    def build(
        lo: np.ndarray, hi: np.ndarray, box_lo: np.ndarray, box_hi: np.ndarray, size: float | None = None
    ) -> Grid:
        """The grid over the box [box_lo, box_hi] with the triangles of the boxes lo, hi; cells twice `size`
        (default: the median longest side of a box)."""
        if size is None:
            size = float(np.median((hi - lo).max(axis=1))) if len(lo) else 1.0
        span = float((box_hi - box_lo).max())
        cell = max(size * 2.0, span / MAX_GRID, 1e-9)
        dims = np.floor((box_hi - box_lo) / cell).astype(np.int64) + 1
        gmin = np.ascontiguousarray(box_lo, dtype=np.float64)
        counts = _grid_counts(lo, hi, cell, gmin, dims)
        big = counts > MAX_CELLS_PER_TRIANGLE
        counts[big] = 0
        starts = np.zeros(len(counts), dtype=np.int64)
        np.cumsum(counts[:-1], out=starts[1:])
        total = int(counts.sum())
        keys = np.empty(total, dtype=np.int64)
        ids = np.empty(total, dtype=np.int64)
        _grid_fill(lo, hi, cell, gmin, dims, starts, keys, ids, big)
        order = np.argsort(keys, kind="stable")
        keys, ids = keys[order], ids[order]
        first = (
            np.flatnonzero(np.concatenate([[True], keys[1:] != keys[:-1]])) if total else np.zeros(0, dtype=np.int64)
        )
        runs = np.append(first, total).astype(np.int64)
        return Grid(cell, gmin, dims, keys[first], runs, ids, np.flatnonzero(big).astype(np.int64))


# --- centroids inside other triangles (is a surface a single-valued sheet?) ------------------------------


@njit(cache=True, nogil=True)
def _strictly_inside(x, y, p, a, b, c):  # type: ignore[no-untyped-def]
    ax, ay = p[a, 0], p[a, 1]
    bx, by = p[b, 0], p[b, 1]
    cx, cy = p[c, 0], p[c, 1]
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if area == 0.0:
        return False
    w0 = ((bx - ax) * (y - ay) - (by - ay) * (x - ax)) / area
    w1 = ((cx - bx) * (y - by) - (cy - by) * (x - bx)) / area
    w2 = ((ax - cx) * (y - cy) - (ay - cy) * (x - cx)) / area
    return w0 > 1e-9 and w1 > 1e-9 and w2 > 1e-9


@njit(cache=True, nogil=True)
def _centroids_inside(p, t, start, stop, cell, gmin, dims, ukeys, runs, ids, big):  # type: ignore[no-untyped-def]
    """Triangles start…stop−1 whose centroid lies strictly inside another triangle (2D, z = 0)."""
    found = 0
    for i in range(start, stop):
        x = (p[t[i, 0], 0] + p[t[i, 1], 0] + p[t[i, 2], 0]) / 3.0
        y = (p[t[i, 0], 1] + p[t[i, 1], 1] + p[t[i, 2], 1]) / 3.0
        key = (_cell_index(x, gmin[0], cell, dims[0]) * dims[1] + _cell_index(y, gmin[1], cell, dims[1])) * dims[2]
        hit = False
        r = np.searchsorted(ukeys, key)
        if r < ukeys.shape[0] and ukeys[r] == key:
            for q in range(runs[r], runs[r + 1]):
                j = ids[q]
                if j != i and _strictly_inside(x, y, p, t[j, 0], t[j, 1], t[j, 2]):
                    hit = True
                    break
        if not hit:
            for q in range(big.shape[0]):
                j = big[q]
                if j != i and _strictly_inside(x, y, p, t[j, 0], t[j, 1], t[j, 2]):
                    hit = True
                    break
        if hit:
            found += 1
    return found


def overlapping_centroids(p: np.ndarray, t: np.ndarray, *, tick: Tick = None, message: str = "") -> int:
    """Number of triangles whose centroid, in the plane z = 0 of `p` (n×3, z ignored), lies strictly inside
    another triangle: 0 for a single-valued sheet."""
    if len(t) < 2:
        return 0
    flat = np.ascontiguousarray(p, dtype=np.float64).copy()
    flat[:, 2] = 0.0
    tri = np.ascontiguousarray(t, dtype=np.int64)
    lo, hi = boxes(flat, tri)
    grid = Grid.build(lo, hi, lo.min(axis=0), hi.max(axis=0))
    found = 0
    for start in range(0, len(tri), PORTION):
        stop = min(start + PORTION, len(tri))
        found += int(
            _centroids_inside(
                flat, tri, start, stop, grid.cell, grid.gmin, grid.dims, grid.ukeys, grid.runs, grid.ids, grid.big
            )
        )
        _tick(tick, stop / len(tri), message)
    return found
