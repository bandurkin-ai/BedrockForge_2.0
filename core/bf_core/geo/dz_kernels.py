"""The height of a surface under or over a point: the kernels of the map of differences of a point cloud
(ADR 0014 §5 «Карта разностей»; `wireframes/cloud_dz.py`).

    index = surface_index(xyz, tri)        # the triangles of a wireframe, as stored
    dz = index.dz(points)                  # float64: Z of a point − Z of the surface there, NaN — no surface

* A triangle covers a point when the point is inside it or on its border **in plan** (X, Y); the height of the
  surface there is linear over the triangle. A triangle without an area in plan (a vertical wall, a triangle
  with a repeated vertex) is not a surface in plan and is left out.
* Several triangles cover the point (a solid: its top and its bottom): the one nearest to the point along the
  vertical counts; of two equally near — the lower one. The answer does not depend on the order of the
  triangles or of the points, so slices of a cloud may be counted by any number of threads.
* No cracks: the side of an edge a point lies on is counted from the ends of the edge taken in the order of
  their coordinates, so the two triangles of an edge get the same number with opposite signs and a point on
  the edge is on both — whether they share the numbers of the vertices or only their coordinates.
* A point at a vertex takes the height of the vertex itself: a point of a cloud that a surface was built from
  has `dz` exactly 0.
* The triangles need no neighbours and no order: holes, several pieces and overhangs are fine. (The walk of
  `dtm_kernels.locate` needs the neighbours of a convex triangulation and stops at the first hole.)

The index: square cells in plan, by levels — the cell of level j is `c0 · 2^j`, c0 the median size of the
triangles; a triangle goes to the first level where it spans at most `SPAN` cells and is listed in the cells
its box touches, at most 3 × 3. The cells are kept by blocks of 8 × 8: a small hash table of the blocks that
have triangles (it stays in the cache of the processor) and the cells of a block side by side, in the order
the triangles come — a stored wireframe and a stored cloud are both in spatial order, so neighbours are read
together. Only what has triangles takes memory: a far outlier vertex or a few huge triangles cost nothing
(30–50 bytes a triangle whatever the extent); scattered triangles that would waste blocks get blocks of one
cell. Coordinates are float64, counted from a whole origin near the surface (the shift is exact); no
`fastmath`. The kernels release the GIL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.jit import njit

SPAN = 2.0  # a triangle spans at most this many cells of its level along an axis
MAX_CELLS = 1 << 26  # cells along an axis at level 0 (a block number takes 28 bits of a key)
BLOCK_BITS = 3  # a block is 8 × 8 cells
WASTE = 8  # cells a triangle the blocks may take, with `SPARE` more: beyond it — blocks of one cell
SPARE = 1 << 16


@njit(cache=True, nogil=True, inline="always")
def _side(ax, ay, bx, by, px, py):  # type: ignore[no-untyped-def]
    """Twice the signed area of (a, b, p): > 0 — p is on the left of a → b. The ends are taken in the order of
    (x, y), so the edge gives the same number from either of its triangles, with the opposite sign."""
    if ax < bx or (ax == bx and ay < by):
        return (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    return -((ax - bx) * (py - by) - (ay - by) * (px - bx))


@njit(cache=True, nogil=True, inline="always")
def _find(keys, key):  # type: ignore[no-untyped-def]
    """The slot of a key in the table of blocks, or the free slot it would take. The constants fit int64;
    int64 arithmetic wraps, the mask keeps the slot ≥ 0."""
    mask = len(keys) - 1
    h = key * 0x5851F42D4C957F2D
    h ^= h >> 31
    h *= 0x2545F4914F6CDD1D
    h ^= h >> 29
    h &= mask
    while keys[h] != key and keys[h] != -1:
        h = (h + 1) & mask
    return h


@njit(cache=True, nogil=True, inline="always")
def _cells(vxy, tri, t, s, x0, y0, c0, levels):  # type: ignore[no-untyped-def]
    """The level of triangle t (of size s) and the cells of that level its box touches."""
    level = 0
    cell = c0
    while s > SPAN * cell and level < levels - 1:
        cell *= 2.0
        level += 1
    inv = 1.0 / cell
    a = tri[t, 0]
    b = tri[t, 1]
    c = tri[t, 2]
    ix0 = np.int64((min(vxy[a, 0], vxy[b, 0], vxy[c, 0]) - x0) * inv)
    ix1 = np.int64((max(vxy[a, 0], vxy[b, 0], vxy[c, 0]) - x0) * inv)
    iy0 = np.int64((min(vxy[a, 1], vxy[b, 1], vxy[c, 1]) - y0) * inv)
    iy1 = np.int64((max(vxy[a, 1], vxy[b, 1], vxy[c, 1]) - y0) * inv)
    return np.int64(level), ix0, ix1, iy0, iy1


@njit(cache=True, nogil=True)
def tri_sizes(vxy, tri, size):  # type: ignore[no-untyped-def]
    """`size[t]` — the longer side of the box of triangle t in plan, −1 — the triangle has no area in plan.
    Returns (triangles with an area, the lows and the highs of their boxes)."""
    n = 0
    x0 = np.inf
    y0 = np.inf
    x1 = -np.inf
    y1 = -np.inf
    for t in range(len(tri)):
        a = tri[t, 0]
        b = tri[t, 1]
        c = tri[t, 2]
        area2 = _side(vxy[a, 0], vxy[a, 1], vxy[b, 0], vxy[b, 1], vxy[c, 0], vxy[c, 1])
        if not (area2 > 0.0 or area2 < 0.0):  # zero; a NaN fails both
            size[t] = -1.0
            continue
        lx = min(vxy[a, 0], vxy[b, 0], vxy[c, 0])
        hx = max(vxy[a, 0], vxy[b, 0], vxy[c, 0])
        ly = min(vxy[a, 1], vxy[b, 1], vxy[c, 1])
        hy = max(vxy[a, 1], vxy[b, 1], vxy[c, 1])
        s = max(hx - lx, hy - ly)
        if not s < np.inf:
            size[t] = -1.0
            continue
        size[t] = s
        n += 1
        x0 = min(x0, lx)
        y0 = min(y0, ly)
        x1 = max(x1, hx)
        y1 = max(y1, hy)
    return n, x0, y0, x1, y1


@njit(cache=True, nogil=True)
def build_cells(vxy, tri, size, x0, y0, c0, levels, bits, max_cells):  # type: ignore[no-untyped-def]
    """The triangles by the cells of the plan: (keys of the blocks — −1 is a free slot, the number of the block
    of a slot, `start` — where the triangles of every cell begin in `items`, `items`). A block is
    `2^bits × 2^bits` cells; cell (ix, iy) of a level is cell `(iy & m) << bits | ix & m` of the block
    `level << 56 | (ix >> bits) << 28 | iy >> bits`. More than `max_cells` cells: `start` comes back empty."""
    n = len(tri)
    m = (1 << bits) - 1
    per = 1 << (2 * bits)
    keys = np.full(1024, -1, np.int64)
    block = np.zeros(1024, np.int32)
    blocks = 0
    for t in range(n):  # the blocks that have triangles, numbered as they are met
        if size[t] < 0.0:
            continue
        level, ix0, ix1, iy0, iy1 = _cells(vxy, tri, t, size[t], x0, y0, c0, levels)
        for by in range(iy0 >> bits, (iy1 >> bits) + 1):
            for bx in range(ix0 >> bits, (ix1 >> bits) + 1):
                key = (level << 56) | (bx << 28) | by
                h = _find(keys, key)
                if keys[h] != -1:
                    continue
                keys[h] = key
                block[h] = blocks
                blocks += 1
                if 2 * blocks > len(keys):  # the table is kept at most half full
                    more = np.full(2 * len(keys), -1, np.int64)
                    numbers = np.zeros(2 * len(keys), np.int32)
                    for j in range(len(keys)):
                        if keys[j] != -1:
                            g = _find(more, keys[j])
                            more[g] = keys[j]
                            numbers[g] = block[j]
                    keys = more
                    block = numbers
    if blocks * per > max_cells:
        return keys, block, np.zeros(0, np.int64), np.zeros(0, np.int32)
    start = np.zeros(blocks * per + 1, np.int64)
    for t in range(n):  # the triangles of every cell, counted one cell ahead
        if size[t] < 0.0:
            continue
        level, ix0, ix1, iy0, iy1 = _cells(vxy, tri, t, size[t], x0, y0, c0, levels)
        for iy in range(iy0, iy1 + 1):
            for ix in range(ix0, ix1 + 1):
                h = _find(keys, (level << 56) | ((ix >> bits) << 28) | (iy >> bits))
                start[block[h] * per + (((iy & m) << bits) | (ix & m)) + 1] += 1
    for c in range(blocks * per):
        start[c + 1] += start[c]
    items = np.empty(start[blocks * per], np.int32)
    at = start[: blocks * per].copy()
    for t in range(n):
        if size[t] < 0.0:
            continue
        level, ix0, ix1, iy0, iy1 = _cells(vxy, tri, t, size[t], x0, y0, c0, levels)
        for iy in range(iy0, iy1 + 1):
            for ix in range(ix0, ix1 + 1):
                h = _find(keys, (level << 56) | ((ix >> bits) << 28) | (iy >> bits))
                c = block[h] * per + (((iy & m) << bits) | (ix & m))
                items[at[c]] = t
                at[c] += 1
    return keys, block, start, items


@njit(cache=True, nogil=True)
def surface_dz(vxy, vz, tri, box, inv, bits, keys, block, start, items, px, py, pz, out):  # type: ignore[no-untyped-def]
    """`out[q]` = pz[q] − the height of the surface at (px[q], py[q]); NaN — no triangle covers the point.
    `box` — the lows and the highs of the surface in plan; `inv` — 1 / cell of the levels that have triangles,
    0 for the others; the rest — `build_cells`."""
    m = (1 << bits) - 1
    x0 = box[0]
    y0 = box[1]
    for q in range(len(px)):
        x = px[q]
        y = py[q]
        z = pz[q]
        best = np.nan
        found = False
        if x >= x0 and x <= box[2] and y >= y0 and y <= box[3]:  # a NaN fails
            for level in range(len(inv)):
                if inv[level] == 0.0:
                    continue
                ix = np.int64((x - x0) * inv[level])
                iy = np.int64((y - y0) * inv[level])
                h = _find(keys, (np.int64(level) << 56) | ((ix >> bits) << 28) | (iy >> bits))
                if keys[h] == -1:
                    continue
                c = (np.int64(block[h]) << (2 * bits)) | ((iy & m) << bits) | (ix & m)
                for e in range(start[c], start[c + 1]):
                    t = items[e]
                    a = tri[t, 0]
                    b = tri[t, 1]
                    c3 = tri[t, 2]
                    ax = vxy[a, 0]
                    ay = vxy[a, 1]
                    bx = vxy[b, 0]
                    by = vxy[b, 1]
                    d0 = _side(ax, ay, bx, by, x, y)
                    cx = vxy[c3, 0]
                    cy = vxy[c3, 1]
                    d1 = _side(bx, by, cx, cy, x, y)
                    if (d0 < 0.0 and d1 > 0.0) or (d0 > 0.0 and d1 < 0.0):
                        continue
                    d2 = _side(cx, cy, ax, ay, x, y)
                    if not ((d0 >= 0.0 and d1 >= 0.0 and d2 >= 0.0) or (d0 <= 0.0 and d1 <= 0.0 and d2 <= 0.0)):
                        continue
                    if x == ax and y == ay:
                        zs = vz[a]
                    elif x == bx and y == by:
                        zs = vz[b]
                    elif x == cx and y == cy:
                        zs = vz[c3]
                    else:
                        s = d0 + d1 + d2
                        if s == 0.0:
                            continue
                        zs = vz[a] + (d2 * (vz[b] - vz[a]) + d0 * (vz[c3] - vz[a])) / s
                    d = z - zs
                    if not found or abs(d) < abs(best) or (abs(d) == abs(best) and d > best):
                        best = d
                        found = True
        out[q] = best


@dataclass(frozen=True)
class SurfaceIndex:
    """The triangles of a surface by the cells of the plan (see the module docstring)."""

    origin: tuple[float, float]  # whole metres: X and Y of `vxy` are counted from it
    vxy: np.ndarray  # n×2
    vz: np.ndarray
    tri: np.ndarray  # int32 m×3
    box: np.ndarray  # the lows and the highs of the triangles that have an area in plan
    inv: np.ndarray  # 1 / cell by level, 0 — the level has no triangles
    bits: int  # a block is 2^bits × 2^bits cells
    keys: np.ndarray
    block: np.ndarray
    start: np.ndarray
    items: np.ndarray
    triangles: int  # with an area in plan
    levels: int  # that have triangles

    @property
    def nbytes(self) -> int:
        """Of the index itself, without the triangles and the heights it was given."""
        return int(sum(a.nbytes for a in (self.vxy, self.keys, self.block, self.start, self.items)))

    def dz(self, xyz: Any) -> np.ndarray:
        """Z − the surface for points n×3 (float64, project coordinates); NaN — no surface at the point."""
        p = np.asarray(xyz, dtype=np.float64)
        if p.ndim != 2 or p.shape[1] != 3:
            raise ValueError("dz: the points are an array n × 3")
        out = np.full(len(p), np.nan)
        if self.triangles and len(p):
            px = p[:, 0] - self.origin[0]  # new arrays: one signature of the kernel whatever the points came in
            py = p[:, 1] - self.origin[1]
            pz = np.array(p[:, 2], dtype=np.float64)
            surface_dz(
                self.vxy, self.vz, self.tri, self.box, self.inv, self.bits,
                self.keys, self.block, self.start, self.items, px, py, pz, out,
            )  # fmt: skip
        return out


def _empty(origin: tuple[float, float]) -> SurfaceIndex:
    f, i = np.zeros(0, dtype=np.float64), np.zeros(0, dtype=np.int64)
    none = np.zeros(0, dtype=np.int32)
    return SurfaceIndex(
        origin, np.zeros((0, 2)), f, np.zeros((0, 3), dtype=np.int32), np.zeros(4), f, 0, i, none, i, none, 0, 0
    )


def surface_index(xyz: Any, tri: Any, *, block_bits: int = BLOCK_BITS) -> SurfaceIndex:
    """The index of a surface: vertices n×3 (float64, project coordinates) and triangles m×3 (vertex numbers).
    ValueError — a vertex number out of the list or a vertex without a coordinate. `block_bits` — for tests:
    0 gives blocks of one cell, what scattered triangles get by themselves."""
    v = np.asarray(xyz, dtype=np.float64)
    t = np.ascontiguousarray(tri, dtype=np.int32)
    if v.ndim != 2 or v.shape[1] != 3 or t.ndim != 2 or t.shape[1] != 3:
        raise ValueError("dz: a surface is vertices n × 3 and triangles m × 3")
    if not len(t):
        return _empty((0.0, 0.0))
    if int(t.min()) < 0 or int(t.max()) >= len(v):
        raise ValueError("dz: a triangle names a vertex that is not in the list")
    if not t.flags.writeable:
        t = t.copy()  # one signature of the kernels: a blob read as a map is read-only
    used = np.zeros(len(v), dtype=bool)
    used[t.ravel()] = True
    if not np.isfinite(v[used]).all():
        raise ValueError("dz: a vertex of the surface has no coordinate")
    origin = (float(math.floor(v[used, 0].min())), float(math.floor(v[used, 1].min())))
    del used
    vxy = np.empty((len(v), 2), dtype=np.float64)
    vxy[:, 0] = v[:, 0] - origin[0]
    vxy[:, 1] = v[:, 1] - origin[1]
    vz = np.array(v[:, 2], dtype=np.float64)
    size = np.empty(len(t), dtype=np.float64)
    n, x0, y0, x1, y1 = tri_sizes(vxy, t, size)
    if not n:
        return _empty(origin)
    sizes = size[size >= 0.0]
    c0 = max(float(np.median(sizes)), max(x1 - x0, y1 - y0) / MAX_CELLS)
    levels = 1
    while SPAN * c0 * 2.0 ** (levels - 1) < float(sizes.max()):
        levels += 1
    del sizes
    bits = int(block_bits)
    keys, block, start, items = build_cells(vxy, t, size, x0, y0, c0, levels, bits, WASTE * n + SPARE)
    if bits and not len(start):  # scattered triangles: whole blocks would be mostly empty cells
        bits = 0
        keys, block, start, items = build_cells(vxy, t, size, x0, y0, c0, levels, bits, 1 << 62)
    # the levels that have triangles: the others are not looked into
    inv = np.zeros(levels, dtype=np.float64)
    level = np.zeros(len(t), dtype=np.int64)
    edge = SPAN * c0
    for _ in range(levels - 1):
        level += size > edge
        edge *= 2.0
    for j in np.unique(level[size >= 0.0]).tolist():
        inv[j] = 1.0 / (c0 * 2.0**j)
    box = np.array([x0, y0, x1, y1], dtype=np.float64)
    return SurfaceIndex(
        origin, vxy, vz, t, box, inv, bits, keys, block, start, items, int(n), int(np.count_nonzero(inv))
    )


def warm() -> None:
    """Compiles (or loads from the cache) the kernels on a tiny surface — before threads start."""
    v = np.array([[0.0, 0.0, 1.0], [4.0, 0.0, 2.0], [4.0, 3.0, 3.0], [0.0, 3.0, 4.0], [90.0, 80.0, 5.0]])
    index = surface_index(v, np.array([[0, 1, 2], [0, 2, 3], [0, 1, 4]], dtype=np.int32))
    index.dz(np.array([[1.0, 1.0, 0.0], [50.0, 50.0, 0.0]]))
