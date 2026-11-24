"""Boundary parents: the parents that a triangle of the body goes through (ТЗ 8.7.3, step 2; ADR 0023 §6).

    b = boundary_parents(grid, mesh, threads=…, tick=…)          # `mesh` — `columns.Mesh`
    b.p             uint64 [n]   the boundary parents, P = i + nx·(j + ny·k), ascending
    b.ptr, b.tri    CSR: the triangles of b.p[r] are b.tri[b.ptr[r]:b.ptr[r + 1]] (int32, ascending)
    b.mask(cells)   bool [cells] — a flag of every parent of the grid
    tri_box_overlap(…)   the test itself (njit): a triangle and a box on the axes of separation

The test: a box and a triangle overlap when no one of 13 axes — the 3 normals of the box, the normal of the
triangle, the 9 products of an edge of the triangle with an axis of the box — separates their projections
(Akenine-Möller). The projections must overlap with a positive length: a triangle that only touches a face of the
box (a cube of 100 m on a grid of 10 m) does not make the parent a boundary one — the parent is wholly inside or
outside, and the centre ray tells which. The box is taken smaller than the parent by 1e−9 of its size: what is
less is not a crossing; such a triangle does not change the volume more than 1e−9. A triangle without an area
(a line or a point) overlaps nothing.

The search goes per triangle through the parents of its box, and for every column of them only through the layers
the plane of the triangle passes (its z over the cell, padded): a long inclined triangle does not test the whole
volume of its box. The triangles go in fixed chunks, the pairs of a chunk to its own arrays; the chunks are joined in
their order and sorted by P with a stable sort: the result is the same bit for bit with any number of threads.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from bf_core.blocks.fill.columns import Mesh
from bf_core.blocks.schema import GridV2
from bf_core.composites import strips
from bf_core.jit import njit

SHRINK = 1e-9  # the parent box is smaller than the parent by this share of its size
CHUNK_TRIANGLES = 1 << 15  # triangles in a chunk of the search

Tick = Callable[[float], None]


@njit(cache=True, nogil=True)
def _separated(ax, ay, az, p0, p1, p2, hx, hy, hz):  # type: ignore[no-untyped-def]
    """Do the projections of the triangle (p0, p1, p2 — its vertices on the axis a) and of the box (half-sizes
    h) not overlap? A zero axis separates nothing."""
    if ax * ax + ay * ay + az * az == 0.0:
        return False
    r = hx * abs(ax) + hy * abs(ay) + hz * abs(az)
    return min(p0, p1, p2) >= r or max(p0, p1, p2) <= -r


@njit(cache=True, nogil=True)
def tri_box_overlap(cx, cy, cz, hx, hy, hz, ax, ay, az, bx, by, bz, qx, qy, qz):  # type: ignore[no-untyped-def]
    """Does the triangle (a, b, q) have a point in the open box with the centre c and the half-sizes h?"""
    ax, ay, az = ax - cx, ay - cy, az - cz
    bx, by, bz = bx - cx, by - cy, bz - cz
    qx, qy, qz = qx - cx, qy - cy, qz - cz
    if min(ax, bx, qx) >= hx or max(ax, bx, qx) <= -hx:
        return False
    if min(ay, by, qy) >= hy or max(ay, by, qy) <= -hy:
        return False
    if min(az, bz, qz) >= hz or max(az, bz, qz) <= -hz:
        return False
    e0x, e0y, e0z = bx - ax, by - ay, bz - az
    e1x, e1y, e1z = qx - bx, qy - by, qz - bz
    e2x, e2y, e2z = ax - qx, ay - qy, az - qz
    nx = e0y * e1z - e0z * e1y
    ny = e0z * e1x - e0x * e1z
    nz = e0x * e1y - e0y * e1x
    d = nx * ax + ny * ay + nz * az
    if abs(d) >= hx * abs(nx) + hy * abs(ny) + hz * abs(nz):  # (also the case of the triangle without an area)
        return False
    for k in range(3):
        if k == 0:
            ex, ey, ez = e0x, e0y, e0z
        elif k == 1:
            ex, ey, ez = e1x, e1y, e1z
        else:
            ex, ey, ez = e2x, e2y, e2z
        # e × X = (0, ez, −ey), e × Y = (−ez, 0, ex), e × Z = (ey, −ex, 0)
        if _separated(0.0, ez, -ey, ez * ay - ey * az, ez * by - ey * bz, ez * qy - ey * qz, hx, hy, hz):
            return False
        if _separated(-ez, 0.0, ex, -ez * ax + ex * az, -ez * bx + ex * bz, -ez * qx + ex * qz, hx, hy, hz):
            return False
        if _separated(ey, -ex, 0.0, ey * ax - ex * ay, ey * bx - ex * by, ey * qx - ex * qy, hx, hy, hz):
            return False
    return True


@njit(cache=True, nogil=True)
def _chunk(v, t, lo, hi, nx, ny, nz, dx, dy, dz, shrink):  # type: ignore[no-untyped-def]
    """The pairs (P, triangle) of the triangles [lo, hi), in the order of triangles, then of cells."""
    cap = 4096
    out_p = np.empty(cap, np.int64)
    out_t = np.empty(cap, np.int32)
    n = 0
    hx, hy, hz = 0.5 * dx * (1.0 - shrink), 0.5 * dy * (1.0 - shrink), 0.5 * dz * (1.0 - shrink)
    pad = 1e-6 * dz
    for m in range(lo, hi):
        a, b, c = t[m, 0], t[m, 1], t[m, 2]
        ax, ay, az = v[a, 0], v[a, 1], v[a, 2]
        bx, by, bz = v[b, 0], v[b, 1], v[b, 2]
        cx, cy, cz = v[c, 0], v[c, 1], v[c, 2]
        xmin, xmax = min(ax, bx, cx), max(ax, bx, cx)
        ymin, ymax = min(ay, by, cy), max(ay, by, cy)
        zmin, zmax = min(az, bz, cz), max(az, bz, cz)
        i0, i1 = max(int(math.floor(xmin / dx)), 0), min(int(math.floor(xmax / dx)), nx - 1)
        j0, j1 = max(int(math.floor(ymin / dy)), 0), min(int(math.floor(ymax / dy)), ny - 1)
        if i1 < i0 or j1 < j0 or zmax <= 0.0 or zmin >= nz * dz:
            continue
        # the plane z = az + gx·(x − ax) + gy·(y − ay), when it is not too steep to bound z over a cell
        px = (by - ay) * (cz - az) - (bz - az) * (cy - ay)
        py = (bz - az) * (cx - ax) - (bx - ax) * (cz - az)
        pz = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        norm = math.sqrt(px * px + py * py + pz * pz)
        flat = abs(pz) > 1e-6 * norm
        for j in range(j0, j1 + 1):
            ya, yb = max(j * dy, ymin), min((j + 1) * dy, ymax)
            for i in range(i0, i1 + 1):
                k0, k1 = 0, nz - 1
                if flat:
                    xa, xb = max(i * dx, xmin), min((i + 1) * dx, xmax)
                    zlo, zhi = zmax, zmin
                    for sx in range(2):
                        for sy in range(2):
                            x = xb if sx else xa
                            y = yb if sy else ya
                            z = az - (px * (x - ax) + py * (y - ay)) / pz
                            zlo, zhi = min(zlo, z), max(zhi, z)
                    zlo, zhi = max(zlo, zmin) - pad, min(zhi, zmax) + pad
                    if zhi < zlo:
                        continue
                    k0, k1 = max(int(math.floor(zlo / dz)), 0), min(int(math.floor(zhi / dz)), nz - 1)
                for k in range(k0, k1 + 1):
                    if tri_box_overlap((i + 0.5) * dx, (j + 0.5) * dy, (k + 0.5) * dz, hx, hy, hz,
                                       ax, ay, az, bx, by, bz, cx, cy, cz):  # fmt: skip
                        if n == cap:
                            cap *= 2
                            np_ = np.empty(cap, np.int64)
                            nt_ = np.empty(cap, np.int32)
                            np_[:n] = out_p[:n]
                            nt_[:n] = out_t[:n]
                            out_p, out_t = np_, nt_
                        out_p[n] = i + nx * (j + ny * k)
                        out_t[n] = m
                        n += 1
    return out_p[:n].copy(), out_t[:n].copy()


@dataclass(frozen=True, eq=False)
class Boundary:
    """The boundary parents `p` (uint64, ascending) and their triangles (CSR `ptr`, `tri`); `pairs` — the
    number of (parent, triangle) pairs."""

    p: np.ndarray
    ptr: np.ndarray
    tri: np.ndarray

    @property
    def pairs(self) -> int:
        return len(self.tri)

    def triangles(self, r: int) -> np.ndarray:
        """The triangles of the boundary parent number r (of `p`)."""
        return self.tri[self.ptr[r] : self.ptr[r + 1]]

    def mask(self, cells: int) -> np.ndarray:
        out = np.zeros(int(cells), dtype=bool)
        out[self.p.astype(np.intp)] = True
        return out


def boundary_parents(
    grid: GridV2, mesh: Mesh, *, threads: int = 1, tick: Tick | None = None, chunk: int = CHUNK_TRIANGLES
) -> Boundary:
    """The boundary parents of the body and the triangles that go through each."""
    nx, ny, nz = grid.counts
    dx, dy, dz = grid.regular("Заполнение")
    t = mesh.t

    def one(lo: int, hi: int) -> tuple[np.ndarray, np.ndarray]:
        return _chunk(mesh.v, t, lo, hi, nx, ny, nz, dx, dy, dz, SHRINK)  # type: ignore[no-any-return]

    parts = strips.run(one, len(t), threads=threads, tick=tick, size=int(chunk))
    if not parts:
        return Boundary(np.zeros(0, np.uint64), np.zeros(1, np.int64), np.zeros(0, np.int32))
    pairs_p = np.concatenate([a for a, _ in parts])
    pairs_t = np.concatenate([b for _, b in parts])
    order = np.argsort(pairs_p, kind="stable")  # the triangles stay ascending within a parent
    pairs_p, pairs_t = pairs_p[order], pairs_t[order]
    del order
    first = np.ones(len(pairs_p), dtype=bool)
    np.not_equal(pairs_p[1:], pairs_p[:-1], out=first[1:])
    starts = np.flatnonzero(first)
    ptr = np.empty(len(starts) + 1, np.int64)
    ptr[:-1] = starts
    ptr[-1] = len(pairs_p)
    return Boundary(pairs_p[starts].astype(np.uint64), ptr, np.ascontiguousarray(pairs_t))
