"""Kernels of the structural trend (ADR 0026, решение 27): the principal axis of Σ w·n·nᵀ of the measurements
within R_тр of a point, by a hash grid. numba is imported with this module: import it lazily (from
`trend_struct.local_axes`), never before BF-READY.

    grid = build_grid(xyz, radius)                    -> Grid (the measurements sorted by cell, CSR of the cells)
    normals, count = axes(grid, normals, q, radius, threads=1, tick=None)
        normals  float64 [m, 3]  the principal axis (unit, the sign is arbitrary — the caller turns it up);
                                 NaN — the point has no coordinates
        count    int32 [m]       the measurements within `radius` (0 — none: the caller takes the nearest one;
                                 −1 — the point has no coordinates)

The sum of a point is taken in the order of the cells and, inside a cell, of the measurements as given: it does not
depend on the bands or the threads — the same bits with 1, 2, 4 and 8 threads. The eigenvector is that of the
cyclic Jacobi method of the symmetric 3 × 3 matrix scaled to the trace 1 (the first of equal eigenvalues wins).
Coordinates are taken as differences from the lowest corner of the measurements (UTM-sized numbers keep their
digits). Memory: the grid — at most 128³ cells (a cell of `max(radius / 2, extent / 128)`: a larger cell only means
more candidates, every one is tested by its distance).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bf_core.composites import strips
from bf_core.jit import njit

__all__ = ["Grid", "axes", "build_grid"]

GRID_MAX = 128  # cells along the longest side of the extent
PER_RADIUS = 2  # cells across a radius: the search looks at (2·2 + 1)³ cells of R/2, not 3³ of R
BAND = 1 << 15  # points of a band: a few milliseconds between two ticks


@dataclass(frozen=True, eq=False)
class Grid:
    lo: np.ndarray  # float64 [3]: the lowest corner of the measurements
    cell: float
    dims: np.ndarray  # int64 [3]
    start: np.ndarray  # int64 [cells + 1]
    xyz: np.ndarray  # float64 [n, 3]: the measurements less `lo`, sorted by cell (stable)
    normal: np.ndarray  # float64 [n, 3]: the same order


def build_grid(xyz: np.ndarray, normal: np.ndarray, radius: float) -> Grid:
    """The measurements (n × 3, finite) in cells of `max(radius, extent / GRID_MAX)`."""
    lo = xyz.min(axis=0)
    rel = xyz - lo
    extent = float(rel.max()) if len(rel) else 0.0
    cell = max(float(radius) / PER_RADIUS, extent / GRID_MAX, 1e-9)
    dims = np.maximum(np.floor(rel.max(axis=0) / cell).astype(np.int64) + 1, 1)
    ij = np.minimum(np.floor(rel / cell).astype(np.int64), dims - 1)
    key = (ij[:, 0] * dims[1] + ij[:, 1]) * dims[2] + ij[:, 2]
    order = np.argsort(key, kind="stable")
    start = np.zeros(int(dims.prod()) + 1, dtype=np.int64)
    np.cumsum(np.bincount(key, minlength=int(dims.prod())), out=start[1:])
    return Grid(lo, cell, dims, start, np.ascontiguousarray(rel[order]), np.ascontiguousarray(normal[order]))


@njit(cache=True)
def _principal(a, v, out):  # type: ignore[no-untyped-def]
    """The eigenvector of the largest eigenvalue of the symmetric a (3 × 3, modified) into out[0:3]; `v` — 3 × 3
    scratch (no allocation per point: the bands run on threads)."""
    for i in range(3):
        for j in range(3):
            v[i, j] = 1.0 if i == j else 0.0
    for _ in range(12):
        off = a[0, 1] * a[0, 1] + a[0, 2] * a[0, 2] + a[1, 2] * a[1, 2]
        if off <= 1e-34:
            break
        for p, q in ((0, 1), (0, 2), (1, 2)):
            apq = a[p, q]
            if apq == 0.0:
                continue
            theta = (a[q, q] - a[p, p]) / (2.0 * apq)
            t = 1.0 / (abs(theta) + np.sqrt(theta * theta + 1.0))
            if theta < 0.0:
                t = -t
            c = 1.0 / np.sqrt(t * t + 1.0)
            s = t * c
            for k in range(3):  # a ← Jᵀ·a·J with the rotation in the plane (p, q)
                akp, akq = a[k, p], a[k, q]
                a[k, p] = c * akp - s * akq
                a[k, q] = s * akp + c * akq
            for k in range(3):
                apk, aqk = a[p, k], a[q, k]
                a[p, k] = c * apk - s * aqk
                a[q, k] = s * apk + c * aqk
            for k in range(3):
                vkp, vkq = v[k, p], v[k, q]
                v[k, p] = c * vkp - s * vkq
                v[k, q] = s * vkp + c * vkq
    best = 0
    if a[1, 1] > a[best, best]:
        best = 1
    if a[2, 2] > a[best, best]:
        best = 2
    for k in range(3):
        out[k] = v[k, best]


@njit(cache=True, nogil=True)
def _band(qx, qy, qz, lo, cell, dims, start, gx, gn, radius, a, b, out, count):  # type: ignore[no-untyped-def]
    r2 = radius * radius
    mat = np.empty((3, 3))
    work = np.empty((3, 3))
    vec = np.empty(3)
    for i in range(a, b):
        x, y, z = qx[i] - lo[0], qy[i] - lo[1], qz[i] - lo[2]
        if not (np.isfinite(x) and np.isfinite(y) and np.isfinite(z)):
            out[i, 0] = np.nan
            out[i, 1] = np.nan
            out[i, 2] = np.nan
            count[i] = -1
            continue
        i0 = max(int((x - radius) / cell), 0)  # trunc = floor above zero; below it the clip gives 0 as well
        i1 = min(int((x + radius) / cell), dims[0] - 1)
        j0 = max(int((y - radius) / cell), 0)
        j1 = min(int((y + radius) / cell), dims[1] - 1)
        k0 = min(max(int((z - radius) / cell), 0), dims[2])  # k0 = nz and k1 = −1: an empty run, indices in range
        k1 = max(min(int((z + radius) / cell), dims[2] - 1), -1)
        sxx = sxy = sxz = syy = syz = szz = 0.0
        found = 0
        for ci in range(i0, i1 + 1):
            for cj in range(j0, j1 + 1):
                row = (ci * dims[1] + cj) * dims[2]  # the cells along z of a row are one run of measurements
                for m in range(start[row + k0], start[row + k1 + 1]):
                    dx, dy, dz = gx[m, 0] - x, gx[m, 1] - y, gx[m, 2] - z
                    d2 = dx * dx + dy * dy + dz * dz
                    if d2 <= r2:
                        w = 1.0 / max(np.sqrt(d2), 1e-6)  # 1 / max(d, 1e−6 m)
                        nx, ny, nz = gn[m, 0], gn[m, 1], gn[m, 2]
                        sxx += w * nx * nx
                        sxy += w * nx * ny
                        sxz += w * nx * nz
                        syy += w * ny * ny
                        syz += w * ny * nz
                        szz += w * nz * nz
                        found += 1
        count[i] = found
        if found == 0:
            out[i, 0] = np.nan
            out[i, 1] = np.nan
            out[i, 2] = np.nan
            continue
        scale = 1.0 / (sxx + syy + szz)  # the trace — Σ w > 0
        mat[0, 0], mat[0, 1], mat[0, 2] = sxx * scale, sxy * scale, sxz * scale
        mat[1, 0], mat[1, 1], mat[1, 2] = sxy * scale, syy * scale, syz * scale
        mat[2, 0], mat[2, 1], mat[2, 2] = sxz * scale, syz * scale, szz * scale
        _principal(mat, work, vec)
        out[i, 0], out[i, 1], out[i, 2] = vec[0], vec[1], vec[2]


def axes(grid: Grid, q: np.ndarray, radius: float, *, threads: int = 1, tick=None):  # type: ignore[no-untyped-def]
    """(the principal axis, the count) of the points `q` (m × 3) — the module docstring."""
    m = len(q)
    qx, qy, qz = (np.ascontiguousarray(q[:, k], dtype=np.float64) for k in range(3))
    out = np.empty((m, 3), dtype=np.float64)
    count = np.empty(m, dtype=np.int32)
    r = float(radius)

    def band(a: int, b: int) -> None:
        _band(qx, qy, qz, grid.lo, grid.cell, grid.dims, grid.start, grid.xyz, grid.normal, r, a, b, out, count)

    strips.run(band, m, threads=threads, tick=tick, size=BAND)
    return out, count
