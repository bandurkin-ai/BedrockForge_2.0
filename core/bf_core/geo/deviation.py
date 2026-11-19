"""The largest deviation of one surface from another (ADR 0015 §7: the report of `simplify` and `smooth`).

    max_deviation(points, xyz, tri)   the largest exact distance from points to a triangle mesh (one-sided)
    Surface(vc, t).distances(p)       a mesh prepared for many queries: exact point–triangle distances
    two_sided(source, vc, t)          the sampled symmetric deviation of a result from its source

Exact distances: the point–triangle distance of Ericson («Real-Time Collision Detection», 5.1.5) in float64 on
coordinates relative to the centre of the mesh. The nearest triangle is looked for on a uniform grid of cells
about twice the median size of a triangle: every triangle is listed in the cells its box covers (a triangle that
would cover more than `MAX_CELLS` cells is kept apart and tested for every point). The distance to the nearest
vertex (KD-tree) bounds the search: only the cells within it are looked at, all of them when they are more than
`BRUTE_SHARE` of the triangles — then every triangle is tested. The result does not depend on the grid.

Points go in bands of `BAND` (progress and cancellation between bands; `threads` > 1 runs bands on threads over
the `nogil` kernels, ADR 0014 — the same numbers for any count). Memory: about 12 bytes a listed cell (2–4 a
triangle) and the vertices once more.

Ported from BF `geo/repair.py` (`_point_triangle_d2`, `_distances`, `_triangle_grid`, `_Surface`,
`max_deviation`, `_two_sided`). Differences: no `prange` (bands on threads instead), the grid of the triangles is
built here (BF took the one of its self-intersection check), progress. numba is imported with this module (import
it lazily, never before BF-READY); scipy on the first call.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np

from bf_core.jit import njit

MAX_CELLS = 512  # a triangle covering more cells than this is tested against every point instead
MIN_CELL = 1e-9
MAX_GRID = 2**20  # cells along an axis
BAND = 65_536  # points between two calls of `progress`
BRUTE_SHARE = 8  # every triangle is tested when the cells to look at exceed max(512, triangles / this)

Progress = Callable[[float], None] | None


# ---------------------------------------------------------------- kernels


@njit(cache=True, nogil=True)
def _segment_d2(px, py, pz, ax, ay, az, bx, by, bz):  # type: ignore[no-untyped-def]
    ux, uy, uz = bx - ax, by - ay, bz - az
    wx, wy, wz = px - ax, py - ay, pz - az
    uu = ux * ux + uy * uy + uz * uz
    s = 0.0
    if uu > 0.0:
        s = min(max((ux * wx + uy * wy + uz * wz) / uu, 0.0), 1.0)
    dx, dy, dz = wx - s * ux, wy - s * uy, wz - s * uz
    return dx * dx + dy * dy + dz * dz


@njit(cache=True, nogil=True)
def point_triangle_d2(px, py, pz, ax, ay, az, bx, by, bz, cx, cy, cz):  # type: ignore[no-untyped-def]
    """The squared distance from a point to a triangle (Ericson, 5.1.5)."""
    abx, aby, abz = bx - ax, by - ay, bz - az
    acx, acy, acz = cx - ax, cy - ay, cz - az
    apx, apy, apz = px - ax, py - ay, pz - az
    d1 = abx * apx + aby * apy + abz * apz
    d2 = acx * apx + acy * apy + acz * apz
    if d1 <= 0.0 and d2 <= 0.0:
        return apx * apx + apy * apy + apz * apz
    bpx, bpy, bpz = px - bx, py - by, pz - bz
    d3 = abx * bpx + aby * bpy + abz * bpz
    d4 = acx * bpx + acy * bpy + acz * bpz
    if d3 >= 0.0 and d4 <= d3:
        return bpx * bpx + bpy * bpy + bpz * bpz
    cpx, cpy, cpz = px - cx, py - cy, pz - cz
    d5 = abx * cpx + aby * cpy + abz * cpz
    d6 = acx * cpx + acy * cpy + acz * cpz
    if d6 >= 0.0 and d5 <= d6:
        return cpx * cpx + cpy * cpy + cpz * cpz
    vc = d1 * d4 - d3 * d2
    vb = d5 * d2 - d1 * d6
    va = d3 * d6 - d5 * d4
    denom = va + vb + vc
    if vc <= 0.0 or vb <= 0.0 or va <= 0.0 or denom <= 0.0:
        # nearest to an edge (or the triangle has no area): the three sides cover every case
        return min(
            _segment_d2(px, py, pz, ax, ay, az, bx, by, bz),
            _segment_d2(px, py, pz, bx, by, bz, cx, cy, cz),
            _segment_d2(px, py, pz, cx, cy, cz, ax, ay, az),
        )
    s = vb / denom
    w = vc / denom
    dx = apx - abx * s - acx * w
    dy = apy - aby * s - acy * w
    dz = apz - abz * s - acz * w
    return dx * dx + dy * dy + dz * dz


@njit(cache=True, nogil=True)
def _d2(p, x, v, t, i):  # type: ignore[no-untyped-def]
    a, b, c = t[i, 0], t[i, 1], t[i, 2]
    return point_triangle_d2(
        p[x, 0], p[x, 1], p[x, 2], v[a, 0], v[a, 1], v[a, 2], v[b, 0], v[b, 1], v[b, 2], v[c, 0], v[c, 1], v[c, 2]
    )


@njit(cache=True, nogil=True)
def _cells(lo, hi, cell, gmin, dims, c0, c1):  # type: ignore[no-untyped-def]
    """The cells (per axis, clamped to the grid) of a box; their number."""
    count = 1
    for k in range(3):
        top = float(dims[k] - 1)
        a = min(max(math.floor((lo[k] - gmin[k]) / cell), 0.0), top)  # clamped before the cast: no overflow
        b = min(max(math.floor((hi[k] - gmin[k]) / cell), 0.0), top)
        c0[k] = int(a)
        c1[k] = int(b)
        count *= c1[k] - c0[k] + 1
    return count


@njit(cache=True, nogil=True)
def cell_counts(lo, hi, cell, gmin, dims):  # type: ignore[no-untyped-def]
    m = lo.shape[0]
    out = np.empty(m, dtype=np.int64)
    c0 = np.empty(3, dtype=np.int64)
    c1 = np.empty(3, dtype=np.int64)
    for i in range(m):
        out[i] = _cells(lo[i], hi[i], cell, gmin, dims, c0, c1)
    return out


@njit(cache=True, nogil=True)
def fill_cells(lo, hi, cell, gmin, dims, starts, keys, ids, big):  # type: ignore[no-untyped-def]
    c0 = np.empty(3, dtype=np.int64)
    c1 = np.empty(3, dtype=np.int64)
    for i in range(lo.shape[0]):
        if big[i]:
            continue
        _cells(lo[i], hi[i], cell, gmin, dims, c0, c1)
        p = starts[i]
        for cx in range(c0[0], c1[0] + 1):
            for cy in range(c0[1], c1[1] + 1):
                for cz in range(c0[2], c1[2] + 1):
                    keys[p] = (cx * dims[1] + cy) * dims[2] + cz
                    ids[p] = i
                    p += 1


@njit(cache=True, nogil=True)
def distances(p, bound, v, t, cell, gmin, dims, ukeys, runs, ids, big_ids, brute_cells, out):  # type: ignore[no-untyped-def]
    """Exact distance from every point to the surface. `bound` is an upper limit (the distance to the nearest
    vertex): only the grid cells within it are searched."""
    c0 = np.empty(3, dtype=np.int64)
    c1 = np.empty(3, dtype=np.int64)
    lo = np.empty(3, dtype=np.float64)
    hi = np.empty(3, dtype=np.float64)
    for x in range(p.shape[0]):
        r = bound[x] * (1.0 + 1e-12) + 1e-300
        best = r * r
        for k in range(3):
            lo[k] = p[x, k] - r
            hi[k] = p[x, k] + r
        cells = _cells(lo, hi, cell, gmin, dims, c0, c1)
        if cells > brute_cells:
            for i in range(t.shape[0]):
                best = min(best, _d2(p, x, v, t, i))
        else:
            for cx in range(c0[0], c1[0] + 1):
                for cy in range(c0[1], c1[1] + 1):
                    for cz in range(c0[2], c1[2] + 1):
                        key = (cx * dims[1] + cy) * dims[2] + cz
                        pos = np.searchsorted(ukeys, key)
                        if pos >= len(ukeys) or ukeys[pos] != key:
                            continue
                        for y in range(runs[pos], runs[pos + 1]):
                            best = min(best, _d2(p, x, v, t, ids[y]))
            for y in range(len(big_ids)):
                best = min(best, _d2(p, x, v, t, big_ids[y]))
        out[x] = math.sqrt(best)


# ---------------------------------------------------------------- a prepared surface


class Surface:
    """A triangle mesh prepared for point-distance queries (a grid of triangles and a KD-tree of vertices).
    `vc` — the vertices relative to an origin the points share, `t` — the triangles (at least one)."""

    def __init__(self, vc: Any, t: Any) -> None:
        from scipy.spatial import cKDTree

        self.v = np.ascontiguousarray(vc, dtype=np.float64)
        self.t = np.ascontiguousarray(t, dtype=np.int32)
        if len(self.t) == 0:
            raise ValueError("deviation: a surface needs triangles")
        self.used = np.unique(self.t)
        self.tree = cKDTree(self.v[self.used])
        corners = self.v[self.t]
        lo, hi = corners.min(axis=1), corners.max(axis=1)
        del corners
        size = float(np.median((hi - lo).max(axis=1)))
        self.gmin = np.ascontiguousarray(lo.min(axis=0))
        span = float((hi.max(axis=0) - self.gmin).max())
        self.cell = max(size * 2.0, span / MAX_GRID, MIN_CELL)
        self.dims = (np.floor((hi.max(axis=0) - self.gmin) / self.cell).astype(np.int64) + 1).astype(np.int64)
        counts = cell_counts(lo, hi, self.cell, self.gmin, self.dims)
        big = counts > MAX_CELLS
        counts[big] = 0
        starts = np.zeros(len(counts), dtype=np.int64)
        np.cumsum(counts[:-1], out=starts[1:])
        total = int(counts.sum())
        keys = np.empty(total, dtype=np.int64)
        ids = np.empty(total, dtype=np.int32)
        fill_cells(lo, hi, self.cell, self.gmin, self.dims, starts, keys, ids, big)
        order = np.argsort(keys, kind="stable")
        keys, self.ids = keys[order], np.ascontiguousarray(ids[order])
        if total:
            first = np.flatnonzero(np.concatenate([[True], keys[1:] != keys[:-1]]))
            self.runs = np.append(first, total).astype(np.int64)
            self.ukeys = np.ascontiguousarray(keys[first])
        else:
            self.runs = np.zeros(1, dtype=np.int64)
            self.ukeys = np.zeros(0, dtype=np.int64)
        self.big_ids = np.flatnonzero(big).astype(np.int64)

    def distances(self, points: Any, *, threads: int = 1, progress: Progress = None) -> np.ndarray:
        """The exact distance from every point (relative to the same origin) to the surface."""
        p = np.ascontiguousarray(points, dtype=np.float64).reshape(-1, 3)
        out = np.empty(len(p), dtype=np.float64)
        if len(p) == 0:
            return out
        brute = max(512, len(self.t) // BRUTE_SHARE)  # a far point would visit more cells than there are triangles
        bands = [(a, min(a + BAND, len(p))) for a in range(0, len(p), BAND)]

        def run(band: tuple[int, int]) -> None:
            a, b = band
            bound, _ = self.tree.query(p[a:b])
            distances(
                p[a:b], np.ascontiguousarray(bound), self.v, self.t, self.cell, self.gmin, self.dims, self.ukeys,
                self.runs, self.ids, self.big_ids, brute, out[a:b],
            )  # fmt: skip

        if threads > 1 and len(bands) > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(min(int(threads), len(bands)), thread_name_prefix="bf-dev") as pool:
                for k, _ in enumerate(pool.map(run, bands)):
                    if progress:
                        progress((k + 1) / len(bands))
        else:
            for k, band in enumerate(bands):
                run(band)
                if progress:
                    progress((k + 1) / len(bands))
        return out


def _centre(v: np.ndarray) -> np.ndarray:
    return np.round((v.min(axis=0) + v.max(axis=0)) / 2.0) + 0.0  # type: ignore[no-any-return]


def max_deviation(points: Any, xyz: Any, tri: Any, *, threads: int = 1, progress: Progress = None) -> float:
    """The largest distance from `points` to the surface (exact point–triangle distances). One-sided; for a
    symmetric measure call it both ways. 0.0 without points, nan without triangles."""
    v = np.ascontiguousarray(xyz, dtype=np.float64).reshape(-1, 3)
    t = np.ascontiguousarray(tri, dtype=np.int32).reshape(-1, 3)
    p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if len(p) == 0:
        return 0.0
    if len(t) == 0:
        return float("nan")
    centre = _centre(v[np.unique(t)])
    return float(Surface(v - centre, t).distances(p - centre, threads=threads, progress=progress).max())


def two_sided(
    source: Surface, vc: np.ndarray, t: np.ndarray, *, centroids: bool = True, threads: int = 1, progress: Progress = None
) -> float:
    """The sampled symmetric deviation of a result (`vc`, `t`, relative to the origin of `source`) from the
    source surface: source vertices → result, and result vertices plus triangle centroids → source. inf for a
    result without triangles."""
    if len(t) == 0:
        return float("inf")
    half = None if progress is None else (lambda f: progress(0.5 * f))
    forward = float(Surface(vc, t).distances(source.v[source.used], threads=threads, progress=half).max())
    used = np.unique(t)
    samples = vc[used]
    if centroids:
        samples = np.concatenate([samples, (vc[t[:, 0]] + vc[t[:, 1]] + vc[t[:, 2]]) / 3.0])
    rest = None if progress is None else (lambda f: progress(0.5 + 0.5 * f))
    return max(forward, float(source.distances(samples, threads=threads, progress=rest).max()))
