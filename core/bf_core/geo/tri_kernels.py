"""Shared by the exact queries on wireframes (`geo/section.py`, `geo/pick.py`, `geo/intersect.py`; ADR 0013 §4):
the arrays of a mesh as the kernels take them, a line against a triangle, a tree of boxes over the triangles.

A line against a triangle is the test of Woop, Benthin and Wald («Watertight Ray/Triangle Intersection», 2013)
in float64. The corners are moved to the origin of the line and sheared so that the line runs along one axis;
the three edge functions are then products of the same numbers for both triangles of a shared edge, of exactly
opposite sign, so a line cannot pass between two triangles that share an edge or a vertex: on the edge itself
both triangles are hit. A triangle seen exactly edge-on, or without area, is not hit. BF used Möller–Trumbore
with tolerances, which on UTM coordinates left gaps of the order of a nanometre.

The tree (for many segments against one mesh): the triangles are taken in runs of `leaf` consecutive ones — the
order must be spatial (`geo.meshes.morton_order`; a stored wireframe is in that order already) — each run has
its box, and the boxes are joined in pairs up to the root, as an implicit binary heap of `2 · leaves − 1` nodes
(`leaves` is a power of two; node k has the children 2k + 1 and 2k + 2; a node without triangles has an empty
box). No pointers, 48 bytes a node.

numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from bf_core.jit import njit

BOX_PAD_M = 1e-6  # boxes are grown by this (and by 1e-12 of the largest coordinate): they only cull

# --- arrays ------------------------------------------------------------------------------------------------


def mesh_arrays(xyz: Any, tri: Any) -> tuple[np.ndarray, np.ndarray]:
    """Vertices float64 n×3 and triangles int32 m×3, C-contiguous, without a copy when they are that already
    (a read-only memory map of the blobs of a wireframe included). ValueError on another structure. The vertex
    numbers of int32 triangles are checked by the kernels, on the triangles they read."""
    v = np.asarray(xyz)
    if v.ndim != 2 or v.shape[1] != 3:
        raise ValueError(f"vertices must be an n × 3 array, got {v.shape}")
    v = np.ascontiguousarray(v, dtype=np.float64)
    t = np.asarray(tri)
    if t.ndim != 2 or t.shape[1] != 3:
        raise ValueError(f"triangles must be an m × 3 array, got {t.shape}")
    if t.size and t.dtype.kind not in "iu":
        raise ValueError(f"triangles must hold vertex numbers, got {t.dtype}")
    if t.dtype != np.int32:
        if t.size and (int(t.min()) < 0 or int(t.max()) >= len(v)):
            raise ValueError("a triangle refers to a vertex that does not exist")
        t = t.astype(np.int32)
    return v, np.ascontiguousarray(t)


def part_arrays(parts: Any, triangles: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The part table of a stored wireframe (`wireframes.render["parts"]`: rows [first, count, xmin, ymin,
    zmin, xmax, ymax, zmax] in project coordinates) as first int64, count int64 and boxes float64 p×6; None —
    one part with all the triangles and no box."""
    if parts is None:
        box = np.array([[-np.inf] * 3 + [np.inf] * 3])
        return np.zeros(1, dtype=np.int64), np.array([triangles], dtype=np.int64), box
    table = np.asarray(parts, dtype=np.float64).reshape(-1, 8)
    first, count = table[:, 0].astype(np.int64), table[:, 1].astype(np.int64)
    if len(table) and (first.min() < 0 or count.min() < 0 or (first + count).max() > triangles):
        raise ValueError("the part table refers to triangles that do not exist")
    if not np.isfinite(table).all():
        raise ValueError("the part table holds a value that is not a number")
    return first, count, np.ascontiguousarray(table[:, 2:8])


def vector(value: Any, name: str) -> np.ndarray:
    """Three finite float64 numbers."""
    out = np.asarray(value, dtype=np.float64).reshape(-1)
    if out.shape != (3,) or not np.isfinite(out).all():
        raise ValueError(f"{name} must be three finite numbers")
    return out


def half_spaces(planes: Any) -> np.ndarray:
    """Planes k×4 [nx, ny, nz, c] (as `THREE.Plane`: a point p is kept where n·p + c ≥ 0; project coordinates,
    the normal need not be a unit one). None — no planes."""
    if planes is None:
        return np.zeros((0, 4))
    out = np.ascontiguousarray(np.asarray(planes, dtype=np.float64).reshape(-1, 4))
    if not np.isfinite(out).all() or (len(out) and not (np.abs(out[:, :3]).max(axis=1) > 0.0).all()):
        raise ValueError("a clipping plane needs four finite numbers and a normal that is not zero")
    return out


# --- a line against a triangle -----------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def shear(dx, dy, dz):  # type: ignore[no-untyped-def]
    """The axes (kx, ky, kz) and the shear (sx, sy, sz) that put the direction (dx, dy, dz), not zero, along
    the axis kz with the handedness kept."""
    ax, ay, az = abs(dx), abs(dy), abs(dz)
    if ax >= ay and ax >= az:
        kz = 0
    elif ay >= az:
        kz = 1
    else:
        kz = 2
    kx = kz + 1 if kz < 2 else 0
    ky = kx + 1 if kx < 2 else 0
    d = (dx, dy, dz)
    if d[kz] < 0.0:
        kx, ky = ky, kx
    return kx, ky, kz, d[kx] / d[kz], d[ky] / d[kz], 1.0 / d[kz]


@njit(cache=True, nogil=True)
def line_triangle(xyz, i0, i1, i2, o, kx, ky, kz, sx, sy, sz):  # type: ignore[no-untyped-def]
    """Does the line through `o` along the direction of `shear` pass through the triangle (i0, i1, i2)?
    Returns (hit, t, v, w): the point is o + t · direction = corner0 + v · (corner1 − corner0) + w · (corner2 −
    corner0). The borders of the triangle belong to it. NaN coordinates give t = NaN, never a crash."""
    az = xyz[i0, kz] - o[kz]
    bz = xyz[i1, kz] - o[kz]
    cz = xyz[i2, kz] - o[kz]
    ax = (xyz[i0, kx] - o[kx]) - sx * az
    ay = (xyz[i0, ky] - o[ky]) - sy * az
    bx = (xyz[i1, kx] - o[kx]) - sx * bz
    by = (xyz[i1, ky] - o[ky]) - sy * bz
    cx = (xyz[i2, kx] - o[kx]) - sx * cz
    cy = (xyz[i2, ky] - o[ky]) - sy * cz
    u = cx * by - cy * bx
    v = ax * cy - ay * cx
    w = bx * ay - by * ax
    if (u < 0.0 or v < 0.0 or w < 0.0) and (u > 0.0 or v > 0.0 or w > 0.0):
        return False, 0.0, 0.0, 0.0
    det = u + v + w
    if det == 0.0:
        return False, 0.0, 0.0, 0.0
    t = (u * (sz * az) + v * (sz * bz) + w * (sz * cz)) / det
    return True, t, v / det, w / det


@njit(cache=True, nogil=True)
def plane_sides(xyz, i0, i1, i2, ax, ay, az, bx, by, bz):  # type: ignore[no-untyped-def]
    """Signed distances of the points a and b from the plane of the triangle, times twice its area (positive
    on the side its normal (corner1 − corner0) × (corner2 − corner0) looks at). The same point and triangle
    always give the same number, so consecutive segments of a polyline agree about their common point."""
    px, py, pz = xyz[i0, 0], xyz[i0, 1], xyz[i0, 2]
    e1x, e1y, e1z = xyz[i1, 0] - px, xyz[i1, 1] - py, xyz[i1, 2] - pz
    e2x, e2y, e2z = xyz[i2, 0] - px, xyz[i2, 1] - py, xyz[i2, 2] - pz
    nx = e1y * e2z - e1z * e2y
    ny = e1z * e2x - e1x * e2z
    nz = e1x * e2y - e1y * e2x
    ga = (ax - px) * nx + (ay - py) * ny + (az - pz) * nz
    gb = (bx - px) * nx + (by - py) * ny + (bz - pz) * nz
    return ga, gb


# --- the tree of boxes -------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def bvh_build(xyz, tri, leaf, pad):  # type: ignore[no-untyped-def]
    """(leaves, boxes (2 · leaves − 1) × 6 [xmin, ymin, zmin, xmax, ymax, zmax]) over runs of `leaf` consecutive
    triangles; leaves = 0 — a triangle refers to a vertex that does not exist."""
    m = tri.shape[0]
    nv = xyz.shape[0]
    runs = (m + leaf - 1) // leaf
    leaves = 1
    while leaves < runs:
        leaves *= 2
    box = np.empty((2 * leaves - 1, 6), dtype=np.float64)
    box[:, :3] = np.inf
    box[:, 3:] = -np.inf
    for run in range(runs):
        node = leaves - 1 + run
        for i in range(run * leaf, min(m, run * leaf + leaf)):
            for k in range(3):
                p = tri[i, k]
                if p < 0 or p >= nv:
                    return 0, box
                for c in range(3):
                    x = xyz[p, c]
                    if x < box[node, c]:
                        box[node, c] = x
                    if x > box[node, c + 3]:
                        box[node, c + 3] = x
        for c in range(3):
            box[node, c] -= pad
            box[node, c + 3] += pad
    for node in range(leaves - 2, -1, -1):
        a = 2 * node + 1
        for c in range(3):
            box[node, c] = min(box[a, c], box[a + 1, c])
            box[node, c + 3] = max(box[a, c + 3], box[a + 1, c + 3])
    return leaves, box


@njit(cache=True, nogil=True)
def _slabs(x0, y0, z0, x1, y1, z1, ax, ay, az, dx, dy, dz, ix, iy, iz):  # type: ignore[no-untyped-def]
    """Does the segment a + t · d, 0 ≤ t ≤ 1 (i = 1 / d, any number where d is zero), touch the box from
    (x0, y0, z0) to (x1, y1, z1)? Rounding can only say yes too often when the box is padded."""
    t0 = 0.0
    t1 = 1.0
    if dx == 0.0:
        if ax < x0 or ax > x1:
            return False
    else:
        ta = (x0 - ax) * ix
        tb = (x1 - ax) * ix
        if ta > tb:
            ta, tb = tb, ta
        if ta > t0:
            t0 = ta
        if tb < t1:
            t1 = tb
        if t0 > t1:
            return False
    if dy == 0.0:
        if ay < y0 or ay > y1:
            return False
    else:
        ta = (y0 - ay) * iy
        tb = (y1 - ay) * iy
        if ta > tb:
            ta, tb = tb, ta
        if ta > t0:
            t0 = ta
        if tb < t1:
            t1 = tb
        if t0 > t1:
            return False
    if dz == 0.0:
        if az < z0 or az > z1:
            return False
    else:
        ta = (z0 - az) * iz
        tb = (z1 - az) * iz
        if ta > tb:
            ta, tb = tb, ta
        if ta > t0:
            t0 = ta
        if tb < t1:
            t1 = tb
        if t0 > t1:
            return False
    return True


@njit(cache=True, nogil=True)
def _segment_box(box, node, ax, ay, az, dx, dy, dz, ix, iy, iz, extra):  # type: ignore[no-untyped-def]
    """Does the segment touch the box of a node grown by `extra`?"""
    if box[node, 0] > box[node, 3]:  # no triangles under this node
        return False
    return _slabs(box[node, 0] - extra, box[node, 1] - extra, box[node, 2] - extra, box[node, 3] + extra,
                  box[node, 4] + extra, box[node, 5] + extra, ax, ay, az, dx, dy, dz, ix, iy, iz)  # fmt: skip


@njit(cache=True, nogil=True)
def _segment_run(xyz, tri, first, last, ax, ay, az, dx, dy, dz, ix, iy, iz, extra):  # type: ignore[no-untyped-def]
    """Does the segment touch the box, grown by `extra`, of one of the triangles first … last − 1?"""
    for i in range(first, last):
        i0, i1, i2 = tri[i, 0], tri[i, 1], tri[i, 2]
        if _slabs(
            min(xyz[i0, 0], xyz[i1, 0], xyz[i2, 0]) - extra,
            min(xyz[i0, 1], xyz[i1, 1], xyz[i2, 1]) - extra,
            min(xyz[i0, 2], xyz[i1, 2], xyz[i2, 2]) - extra,
            max(xyz[i0, 0], xyz[i1, 0], xyz[i2, 0]) + extra,
            max(xyz[i0, 1], xyz[i1, 1], xyz[i2, 1]) + extra,
            max(xyz[i0, 2], xyz[i1, 2], xyz[i2, 2]) + extra,
            ax, ay, az, dx, dy, dz, ix, iy, iz,
        ):  # fmt: skip
            return True
    return False


@njit(cache=True, nogil=True)
def _inverse(d):  # type: ignore[no-untyped-def]
    return 1.0 / d if d != 0.0 else 0.0


@njit(cache=True, nogil=True)
def segment_leaves(xyz, tri, leaf, leaves, box, pad, stack, ax, ay, az, bx, by, bz, extra, out):  # type: ignore[no-untyped-def]
    """The runs of triangles that have a triangle within `extra` of the segment a–b, by the boxes (grown by
    `pad`; some may be false alarms): the nodes of the tree into `out`. Returns their number, or −1 when `out`
    is too short."""
    dx, dy, dz = bx - ax, by - ay, bz - az
    ix, iy, iz = _inverse(dx), _inverse(dy), _inverse(dz)
    n = 0
    stack[0] = 0
    top = 1
    while top > 0:
        top -= 1
        node = stack[top]
        if not _segment_box(box, node, ax, ay, az, dx, dy, dz, ix, iy, iz, extra):
            continue
        if node < leaves - 1:
            stack[top] = 2 * node + 1
            stack[top + 1] = 2 * node + 2
            top += 2
            continue
        # a run of consecutive triangles may hold triangles far apart (the curve of the spatial order jumps):
        # its box is then large, and only the boxes of its triangles tell whether the segment is near it
        first = (node - (leaves - 1)) * leaf
        if not _segment_run(xyz, tri, first, min(tri.shape[0], first + leaf), ax, ay, az, dx, dy, dz, ix, iy, iz,
                            extra + pad):  # fmt: skip
            continue
        if n == out.shape[0]:
            return -1
        out[n] = node
        n += 1
    return n


@njit(cache=True, nogil=True)
def leaf_hits(xyz, tri, leaf, leaves, box, nodes, count, check, ax, ay, az, bx, by, bz, out_t, out_tri, out_sign):  # type: ignore[no-untyped-def]
    """Crossings of the segment a–b with the triangles of the runs `nodes[:count]` (`check` — only of those
    whose box the segment touches): for each, t (0…1 along the segment), the triangle and the sign (+1: the
    segment goes the way the normal of the triangle looks, −1: against it). Returns their number, or −1 when
    `out_*` are too short.

    A crossing: the line of the segment passes through the triangle (`line_triangle`) and the ends are on
    different sides of its plane, an end lying on the plane counting as above it. So of two consecutive
    segments of a polyline exactly one owns a crossing at their common point, and a segment that ends on a
    surface it came to from above has not crossed it."""
    dx, dy, dz = bx - ax, by - ay, bz - az
    if dx == 0.0 and dy == 0.0 and dz == 0.0:
        return 0
    ix, iy, iz = _inverse(dx), _inverse(dy), _inverse(dz)
    kx, ky, kz, sx, sy, sz = shear(dx, dy, dz)
    o = (ax, ay, az)
    m = tri.shape[0]
    n = 0
    for q in range(count):
        node = nodes[q]
        if check and not _segment_box(box, node, ax, ay, az, dx, dy, dz, ix, iy, iz, 0.0):
            continue
        first = (node - (leaves - 1)) * leaf
        for i in range(first, min(m, first + leaf)):
            i0, i1, i2 = tri[i, 0], tri[i, 1], tri[i, 2]
            hit, _t, _v, _w = line_triangle(xyz, i0, i1, i2, o, kx, ky, kz, sx, sy, sz)
            if not hit:
                continue
            ga, gb = plane_sides(xyz, i0, i1, i2, ax, ay, az, bx, by, bz)
            if (ga >= 0.0) == (gb >= 0.0):
                continue
            if n == out_t.shape[0]:
                return -1
            out_t[n] = ga / (ga - gb)
            out_tri[n] = i
            out_sign[n] = 1 if ga < 0.0 else -1
            n += 1
    return n
