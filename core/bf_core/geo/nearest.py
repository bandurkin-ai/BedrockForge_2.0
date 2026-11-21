"""The nearest triangle of a wireframe to a point (ТЗ 8.5.2 «ближайшая к середине интервала точка каркаса …
не дальше d_max: нормаль ближайшего треугольника или средняя по площади в радиусе r_n»; ADR 0021 §5, решение 8).

    nearest_triangles(target, points, d_max=inf, into=None, mesh=0, threads=1, tick=None) -> Nearest
        for every point: the triangle (its number in `tri` as given), the squared distance, the nearest point
        of the triangle and its unit normal (corner1 − corner0) × (corner2 − corner0); nothing within d_max —
        −1 and NaN, never zero. `into` — what the wireframes before this one gave for the same points: the
        result is the nearer of the two, `mesh` — the number written where this wireframe is the nearer one
    area_normals(target, points, normals, radius, threads=1, tick=None) -> float64 [n, 3]
        Σ area · normal of the triangles not farther than `radius` from each point, every normal turned to the
        side `normals[i]` looks at (the two sides of a thin body add up, they do not cancel)
    mean_normals(sums, fallback) -> float64 [n, 3]
        the sums as unit vectors; a sum shorter than `NORMAL_MIN` gives the row of `fallback`

Nothing is built for it: the search walks the tree of boxes a `geo.intersect.MeshTarget` has already (runs of
`leaf` consecutive triangles in spatial order under an implicit heap of boxes, `tri_kernels.bvh_build`) — one
tree a wireframe serves the crossings of the holes and this.

The answer is the least (d², number of the triangle) over the triangles of the wireframe that have an area and
are not farther than d_max (d² ≤ d_max², the limit itself included); d² is that of
`deviation.point_triangle_d2` (Ericson, 5.1.5), so «equal distances» are equal float64 numbers: the triangles
around a vertex the point is nearest to, a triangle given twice. A triangle without area — its normal
(corner1 − corner0) × (corner2 − corner0) is exactly zero, as `pick.triangle_attitude` has it — is never the
nearest and adds nothing to a sum. With `into` the order is (d², triangle, wireframe): a later wireframe takes a
point only with a smaller d² or the same d² and a lower number of the triangle.

The walk: a stack, the nearer child first, a box farther than the best found is dropped; inside a run the box
of a triangle (grown by the padding of the tree) is looked at before the triangle. The search of a point starts
from the triangle found for the point before it — consecutive intervals of a hole are metres apart — and from
what `into` holds. The start only saves work: a box is dropped when it is farther than the best, never when it
is as far, and the boxes are padded by more than the rounding of d², so every triangle that could be the least
is looked at, whatever was found first. The result of a point so depends on the point and the wireframe alone —
not on the points before it, the bands or the threads.

Coordinates: project ones, float64. The kernels read them only as differences (corner − corner, point −
corner, point − box): that is the subtraction of a reference point of ТЗ 8.0.1, exact to the last bit of the
coordinates as stored; the nearest point is returned in project coordinates.

Points go in bands of `BAND` (fixed); `threads` > 1 runs the bands on Python threads over the `nogil` kernels
(ADR 0014; no `prange`, no `fastmath`): a band writes only its rows — the same bytes with 1, 2, 4 and 8 threads.
`tick(fraction)` is called in the calling thread after every band and may raise to cancel: the bands not
started are dropped. The sums of `area_normals` are taken in the order of the triangles of the tree: inside a
run left to right, the runs joined as a binary counter (pairwise; ТЗ 8.0.1).

Memory: 68 bytes a point (`Nearest`), nothing a triangle. Time (подшаг 15.5; one thread, the middles of 1 m
intervals down 4000 holes, d_max 50 m, the triangles in the order of the store): a pit surface with nodes 2 m
apart, 1 and 10 million triangles — 2,1 and 1,9 µs a point (4,9 and 4,6 µs where a triangle is found, 0,5 µs
where none is within d_max; 0,5 µs a point on 4 threads); a closed body of 10 million triangles of 0,3 m —
6,1 µs a point (48 µs where found; 1,7 µs on 4 threads). The tree itself (`MeshTarget`, stored order): 0,02 and
0,30 s. What a point costs is the number of triangles about as far from it as the nearest one — it grows with
the distance over the size of a triangle, and is at its worst deep inside a rounded dense body with no d_max
(every triangle is about as far): keep d_max to what the orientation is good for. `area_normals`: 2 µs a point
at r_n = 2 m, 8 µs at 10 m on the same surface.

numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.jit import njit

from .deviation import point_triangle_d2
from .intersect import MeshTarget

__all__ = ["BAND", "NORMAL_MIN", "Nearest", "area_normals", "mean_normals", "nearest_triangles"]

# tick(fraction 0…1); it may raise (JobCancelled) to cancel the calculation.
Tick = Callable[[float], None]

BAND = 1 << 14  # points between two ticks; fixed, so the result does not depend on the number of threads
NORMAL_MIN = 1e-9  # a sum of area · normal shorter than this is no direction: the nearest triangle gives it
_NO_NUMBER = np.iinfo(np.int64).max  # «no triangle yet»: any number is lower
_LEVELS = 48  # of the pairwise sum: 2**48 runs of triangles
_IDENTITY = np.zeros(0, dtype=np.int64)  # `number` of a wireframe whose triangles are in the order given


# --- kernels -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _box_d2(box, node, px, py, pz):  # type: ignore[no-untyped-def]
    """The squared distance from the point to the box of a node; inf — there are no triangles under it."""
    if box[node, 0] > box[node, 3]:
        return math.inf
    d = 0.0
    g = box[node, 0] - px
    if g > 0.0:
        d += g * g
    else:
        g = px - box[node, 3]
        if g > 0.0:
            d += g * g
    g = box[node, 1] - py
    if g > 0.0:
        d += g * g
    else:
        g = py - box[node, 4]
        if g > 0.0:
            d += g * g
    g = box[node, 2] - pz
    if g > 0.0:
        d += g * g
    else:
        g = pz - box[node, 5]
        if g > 0.0:
            d += g * g
    return d


@njit(cache=True, nogil=True)
def _beyond(lo, hi, p):  # type: ignore[no-untyped-def]
    """How far the coordinate p is outside lo … hi (0 — inside)."""
    if p < lo:
        return lo - p
    if p > hi:
        return p - hi
    return 0.0


@njit(cache=True, nogil=True)
def _segment_point(px, py, pz, ax, ay, az, bx, by, bz):  # type: ignore[no-untyped-def]
    """(squared distance, the nearest point) of the segment a–b: the arithmetic of `deviation._segment_d2`."""
    ux, uy, uz = bx - ax, by - ay, bz - az
    wx, wy, wz = px - ax, py - ay, pz - az
    uu = ux * ux + uy * uy + uz * uz
    s = 0.0
    if uu > 0.0:
        s = min(max((ux * wx + uy * wy + uz * wz) / uu, 0.0), 1.0)
    dx, dy, dz = wx - s * ux, wy - s * uy, wz - s * uz
    return dx * dx + dy * dy + dz * dz, ax + s * ux, ay + s * uy, az + s * uz


@njit(cache=True, nogil=True)
def closest_point(px, py, pz, ax, ay, az, bx, by, bz, cx, cy, cz):  # type: ignore[no-untyped-def]
    """The point of the triangle a, b, c nearest to p (Ericson, 5.1.5): the branches of
    `deviation.point_triangle_d2` one for one, so it is the point that function measured the distance to."""
    abx, aby, abz = bx - ax, by - ay, bz - az
    acx, acy, acz = cx - ax, cy - ay, cz - az
    apx, apy, apz = px - ax, py - ay, pz - az
    d1 = abx * apx + aby * apy + abz * apz
    d2 = acx * apx + acy * apy + acz * apz
    if d1 <= 0.0 and d2 <= 0.0:
        return ax, ay, az
    bpx, bpy, bpz = px - bx, py - by, pz - bz
    d3 = abx * bpx + aby * bpy + abz * bpz
    d4 = acx * bpx + acy * bpy + acz * bpz
    if d3 >= 0.0 and d4 <= d3:
        return bx, by, bz
    cpx, cpy, cpz = px - cx, py - cy, pz - cz
    d5 = abx * cpx + aby * cpy + abz * cpz
    d6 = acx * cpx + acy * cpy + acz * cpz
    if d6 >= 0.0 and d5 <= d6:
        return cx, cy, cz
    vc = d1 * d4 - d3 * d2
    vb = d5 * d2 - d1 * d6
    va = d3 * d6 - d5 * d4
    denom = va + vb + vc
    if vc <= 0.0 or vb <= 0.0 or va <= 0.0 or denom <= 0.0:  # on a side: the nearest of the three
        best, qx, qy, qz = _segment_point(px, py, pz, ax, ay, az, bx, by, bz)
        d, x, y, z = _segment_point(px, py, pz, bx, by, bz, cx, cy, cz)
        if d < best:
            best, qx, qy, qz = d, x, y, z
        d, x, y, z = _segment_point(px, py, pz, cx, cy, cz, ax, ay, az)
        if d < best:
            qx, qy, qz = x, y, z
        return qx, qy, qz
    s = vb / denom
    w = vc / denom
    return ax + abx * s + acx * w, ay + aby * s + acy * w, az + abz * s + acz * w


@njit(cache=True, nogil=True)
def _stack_room(leaves):  # type: ignore[no-untyped-def]
    """Entries the stack of a walk needs: one a level of the tree and two to spare."""
    room = 3
    while leaves > 1:
        leaves >>= 1
        room += 1
    return room


@njit(cache=True, nogil=True)
def _nearest_band(  # type: ignore[no-untyped-def]
    xyz, tri, leaf, leaves, box, pad, number, points, a, b, dmax2, mesh, o_mesh, o_tri, o_d2, o_point, o_normal
):
    """The points a ≤ q < b against the wireframe (xyz, tri, leaf, leaves, box, pad — `MeshTarget.kernel_args`;
    `number` — the number as given of every triangle of the tree, empty — its own). `o_*` hold what was found
    before (−1 and NaN — nothing) and get what this wireframe has nearer."""
    m = tri.shape[0]
    inner = leaves - 1
    numbered = number.shape[0] > 0
    room = _stack_room(leaves)
    nodes = np.empty(room, dtype=np.int64)
    dist = np.empty(room, dtype=np.float64)
    prev = -1  # the triangle found for the point before: a first guess, never more than that
    for q in range(a, b):
        px, py, pz = points[q, 0], points[q, 1], points[q, 2]
        if not math.isfinite(px + py + pz):
            continue
        best = -1
        best_d2 = dmax2
        best_num = _NO_NUMBER
        if o_tri[q] >= 0 and o_d2[q] <= dmax2:
            best_d2 = o_d2[q]
            best_num = o_tri[q]
        if prev >= 0:
            i0, i1, i2 = tri[prev, 0], tri[prev, 1], tri[prev, 2]
            d2 = point_triangle_d2(px, py, pz, xyz[i0, 0], xyz[i0, 1], xyz[i0, 2], xyz[i1, 0], xyz[i1, 1],
                                   xyz[i1, 2], xyz[i2, 0], xyz[i2, 1], xyz[i2, 2])  # fmt: skip
            num = number[prev] if numbered else prev
            if d2 < best_d2 or (d2 == best_d2 and num < best_num):
                best, best_d2, best_num = prev, d2, num
        top = 0
        d = _box_d2(box, 0, px, py, pz)
        if d <= best_d2 and d < math.inf:
            nodes[0] = 0
            dist[0] = d
            top = 1
        while top > 0:
            top -= 1
            node = nodes[top]
            if dist[top] > best_d2:
                continue
            while node < inner:  # down to a run of triangles: the nearer child first, the other one waits
                left = 2 * node + 1
                dl = _box_d2(box, left, px, py, pz)
                dr = _box_d2(box, left + 1, px, py, pz)
                if dl <= dr:
                    near, dn, far, df = left, dl, left + 1, dr
                else:
                    near, dn, far, df = left + 1, dr, left, dl
                if df <= best_d2 and df < math.inf:
                    nodes[top] = far
                    dist[top] = df
                    top += 1
                if dn <= best_d2 and dn < math.inf:
                    node = near
                else:
                    node = -1
                    break
            if node < 0:
                continue
            first = (node - inner) * leaf
            for i in range(first, min(m, first + leaf)):
                i0, i1, i2 = tri[i, 0], tri[i, 1], tri[i, 2]
                ax, ay, az = xyz[i0, 0], xyz[i0, 1], xyz[i0, 2]
                bx, by, bz = xyz[i1, 0], xyz[i1, 1], xyz[i1, 2]
                cx, cy, cz = xyz[i2, 0], xyz[i2, 1], xyz[i2, 2]
                # a run may hold triangles far apart (the curve of the spatial order jumps): its box first
                g = _beyond(min(ax, bx, cx) - pad, max(ax, bx, cx) + pad, px)
                d = g * g
                if d > best_d2:
                    continue
                g = _beyond(min(ay, by, cy) - pad, max(ay, by, cy) + pad, py)
                d += g * g
                if d > best_d2:
                    continue
                g = _beyond(min(az, bz, cz) - pad, max(az, bz, cz) + pad, pz)
                d += g * g
                if d > best_d2:
                    continue
                d2 = point_triangle_d2(px, py, pz, ax, ay, az, bx, by, bz, cx, cy, cz)
                num = number[i] if numbered else i
                if d2 < best_d2 or (d2 == best_d2 and num < best_num):
                    e1x, e1y, e1z = bx - ax, by - ay, bz - az
                    e2x, e2y, e2z = cx - ax, cy - ay, cz - az
                    nx = e1y * e2z - e1z * e2y
                    ny = e1z * e2x - e1x * e2z
                    nz = e1x * e2y - e1y * e2x
                    if nx * nx + ny * ny + nz * nz > 0.0:  # a triangle without area is never the nearest
                        best, best_d2, best_num = i, d2, num
        if best < 0:
            continue
        prev = best
        i0, i1, i2 = tri[best, 0], tri[best, 1], tri[best, 2]
        ax, ay, az = xyz[i0, 0], xyz[i0, 1], xyz[i0, 2]
        bx, by, bz = xyz[i1, 0], xyz[i1, 1], xyz[i1, 2]
        cx, cy, cz = xyz[i2, 0], xyz[i2, 1], xyz[i2, 2]
        qx, qy, qz = closest_point(px, py, pz, ax, ay, az, bx, by, bz, cx, cy, cz)
        e1x, e1y, e1z = bx - ax, by - ay, bz - az
        e2x, e2y, e2z = cx - ax, cy - ay, cz - az
        nx = e1y * e2z - e1z * e2y
        ny = e1z * e2x - e1x * e2z
        nz = e1x * e2y - e1y * e2x
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        o_mesh[q] = mesh
        o_tri[q] = best_num
        o_d2[q] = best_d2
        o_point[q, 0] = qx
        o_point[q, 1] = qy
        o_point[q, 2] = qz
        o_normal[q, 0] = nx / length
        o_normal[q, 1] = ny / length
        o_normal[q, 2] = nz / length


@njit(cache=True, nogil=True)
def _area_band(xyz, tri, leaf, leaves, box, pad, points, normals, a, b, r2, out):  # type: ignore[no-untyped-def]
    """Σ area · normal of the triangles within r2 (squared) of the points a ≤ q < b, the normals turned to the
    side of `normals[q]`; a row without a point or a normal is NaN."""
    m = tri.shape[0]
    inner = leaves - 1
    nodes = np.empty(_stack_room(leaves), dtype=np.int64)
    level = np.zeros((_LEVELS, 3), dtype=np.float64)
    for q in range(a, b):
        px, py, pz = points[q, 0], points[q, 1], points[q, 2]
        rx, ry, rz = normals[q, 0], normals[q, 1], normals[q, 2]
        if not math.isfinite(px + py + pz + rx + ry + rz):
            out[q, 0] = math.nan
            out[q, 1] = math.nan
            out[q, 2] = math.nan
            continue
        count = 0  # runs added: bit j set — level[j] holds the sum of 2**j of them
        nodes[0] = 0
        top = 1
        while top > 0:
            top -= 1
            node = nodes[top]
            if not _box_d2(box, node, px, py, pz) <= r2:
                continue
            if node < inner:  # the left child is taken first: the runs come in the order of the triangles
                nodes[top] = 2 * node + 2
                nodes[top + 1] = 2 * node + 1
                top += 2
                continue
            sx = 0.0
            sy = 0.0
            sz = 0.0
            some = False
            first = (node - inner) * leaf
            for i in range(first, min(m, first + leaf)):
                i0, i1, i2 = tri[i, 0], tri[i, 1], tri[i, 2]
                ax, ay, az = xyz[i0, 0], xyz[i0, 1], xyz[i0, 2]
                bx, by, bz = xyz[i1, 0], xyz[i1, 1], xyz[i1, 2]
                cx, cy, cz = xyz[i2, 0], xyz[i2, 1], xyz[i2, 2]
                g = _beyond(min(ax, bx, cx) - pad, max(ax, bx, cx) + pad, px)
                d = g * g
                g = _beyond(min(ay, by, cy) - pad, max(ay, by, cy) + pad, py)
                d += g * g
                g = _beyond(min(az, bz, cz) - pad, max(az, bz, cz) + pad, pz)
                d += g * g
                if d > r2:
                    continue
                e1x, e1y, e1z = bx - ax, by - ay, bz - az
                e2x, e2y, e2z = cx - ax, cy - ay, cz - az
                nx = e1y * e2z - e1z * e2y
                ny = e1z * e2x - e1x * e2z
                nz = e1x * e2y - e1y * e2x
                if not nx * nx + ny * ny + nz * nz > 0.0:
                    continue
                if not point_triangle_d2(px, py, pz, ax, ay, az, bx, by, bz, cx, cy, cz) <= r2:
                    continue
                half = -0.5 if nx * rx + ny * ry + nz * rz < 0.0 else 0.5
                sx += half * nx
                sy += half * ny
                sz += half * nz
                some = True
            if not some:
                continue
            j = 0
            c = count
            while c & 1:
                sx += level[j, 0]
                sy += level[j, 1]
                sz += level[j, 2]
                j += 1
                c >>= 1
            level[j, 0] = sx
            level[j, 1] = sy
            level[j, 2] = sz
            count += 1
        sx = 0.0
        sy = 0.0
        sz = 0.0
        j = 0
        while count > 0:  # what is left: from the last runs to the first
            if count & 1:
                sx += level[j, 0]
                sy += level[j, 1]
                sz += level[j, 2]
            j += 1
            count >>= 1
        out[q, 0] = sx
        out[q, 1] = sy
        out[q, 2] = sz


# --- bands -------------------------------------------------------------------------------------------------


def _bands(fn: Callable[[int, int], None], n: int, threads: int, tick: Tick | None) -> None:
    """fn(a, b) over the fixed bands of n points; `tick` after every band, in the calling thread."""
    todo = [(a, min(a + BAND, n)) for a in range(0, n, BAND)]

    def done(k: int) -> None:
        if tick is not None:
            tick((k + 1) / len(todo))

    if threads <= 1 or len(todo) < 2:
        for k, (a, b) in enumerate(todo):
            fn(a, b)
            done(k)
        return
    pool = ThreadPoolExecutor(min(int(threads), len(todo)), thread_name_prefix="bf-nearest")
    try:
        for k, _ in enumerate(pool.map(lambda band: fn(band[0], band[1]), todo)):
            done(k)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def _rows(values: Any, name: str) -> np.ndarray:
    out = np.asarray(values, dtype=np.float64)
    if out.ndim != 2 or out.shape[1] != 3:
        raise ValueError(f"{name} must be an n × 3 array, got {out.shape}")
    return np.ascontiguousarray(out)


def _number(target: MeshTarget) -> np.ndarray:
    return _IDENTITY if target.order is None else np.ascontiguousarray(target.order, dtype=np.int64)


# --- the nearest triangle ----------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Nearest:
    """The nearest triangle of every point; a point with nothing within d_max: −1 and NaN."""

    mesh: np.ndarray  # int32: the number of the wireframe (`mesh` of the call that found it), −1 — nothing
    triangle: np.ndarray  # int64: the triangle, its number in `tri` as given, −1 — nothing
    d2: np.ndarray  # float64: the squared distance, m²
    point: np.ndarray  # float64 n×3: the nearest point of the triangle, project coordinates
    normal: np.ndarray  # float64 n×3: the unit normal of the triangle, by the order of its corners

    def __len__(self) -> int:
        return len(self.triangle)

    @property
    def found(self) -> np.ndarray:
        """bool: the point has a triangle."""
        return self.triangle >= 0  # type: ignore[no-any-return]

    @property
    def distance(self) -> np.ndarray:
        """float64: metres; NaN — nothing."""
        return np.sqrt(self.d2)  # type: ignore[no-any-return]


def nearest_triangles(
    target: MeshTarget,
    points: Any,
    d_max: float = math.inf,
    *,
    into: Nearest | None = None,
    mesh: int = 0,
    threads: int = 1,
    tick: Tick | None = None,
) -> Nearest:
    """The triangle of `target` nearest to every point (n×3, project coordinates) and not farther than d_max
    metres (0 — only a triangle the point lies on; inf — any). A point without coordinates finds nothing.

    Several wireframes are taken one at a time, the result of one going `into` the next with the same d_max
    (only one wireframe is in memory): where this wireframe is nearer — or as near, with a lower number of the
    triangle — the row becomes its own and gets `mesh`; `into` itself is not changed. What `into` holds also
    bounds the search, so the later wireframes are the cheaper the nearer the earlier ones were."""
    p = _rows(points, "points")
    limit = float(d_max)
    if not limit >= 0.0:
        raise ValueError(f"d_max must be a distance — zero or more, got {d_max!r}")
    n = len(p)
    if into is None:
        o_mesh = np.full(n, -1, dtype=np.int32)
        o_tri = np.full(n, -1, dtype=np.int64)
        o_d2 = np.full(n, np.nan, dtype=np.float64)
        o_point = np.full((n, 3), np.nan, dtype=np.float64)
        o_normal = np.full((n, 3), np.nan, dtype=np.float64)
    else:
        if len(into) != n:
            raise ValueError(f"`into` holds {len(into)} points, {n} are asked for")
        o_mesh = np.array(into.mesh, dtype=np.int32)
        o_tri = np.array(into.triangle, dtype=np.int64)
        o_d2 = np.array(into.d2, dtype=np.float64)
        o_point = np.array(into.point, dtype=np.float64, order="C")
        o_normal = np.array(into.normal, dtype=np.float64, order="C")
    xyz, tri, leaf, leaves, box, pad = target.kernel_args()
    number, limit2, k = _number(target), limit * limit, int(mesh)

    def band(a: int, b: int) -> None:
        _nearest_band(
            xyz, tri, leaf, leaves, box, pad, number, p, a, b, limit2, k, o_mesh, o_tri, o_d2, o_point, o_normal
        )

    _bands(band, n, threads, tick)
    return Nearest(o_mesh, o_tri, o_d2, o_point, o_normal)


# --- the normals averaged by area --------------------------------------------------------------------------


def area_normals(
    target: MeshTarget,
    points: Any,
    normals: Any,
    radius: float,
    *,
    threads: int = 1,
    tick: Tick | None = None,
) -> np.ndarray:
    """Σ area · normal (m²) of the triangles of `target` not farther than `radius` metres from each point —
    whole triangles, by their exact distance — every normal turned so that it does not look away from
    `normals[i]`. `points`, `normals` — `Nearest.point` and `Nearest.normal`: around the point found, to the
    side of the triangle found. A row without a point or a normal is NaN; no triangle in reach — zeros.

    The sums of several wireframes are added by the caller; `mean_normals` makes the direction of them."""
    p = _rows(points, "points")
    ref = _rows(normals, "normals")
    if len(ref) != len(p):
        raise ValueError(f"{len(ref)} normals for {len(p)} points")
    r = float(radius)
    if not (r >= 0.0 and math.isfinite(r)):
        raise ValueError(f"radius must be a distance — zero or more, got {radius!r}")
    out = np.empty((len(p), 3), dtype=np.float64)
    xyz, tri, leaf, leaves, box, pad = target.kernel_args()
    _bands(lambda a, b: _area_band(xyz, tri, leaf, leaves, box, pad, p, ref, a, b, r * r, out), len(p), threads, tick)
    return out


def mean_normals(sums: Any, fallback: Any) -> np.ndarray:
    """Unit vectors along the sums of `area_normals`; a sum shorter than `NORMAL_MIN` (or none) gives the row
    of `fallback` — the normal of the nearest triangle (NaN where there is none)."""
    s = _rows(sums, "sums")
    other = _rows(fallback, "fallback")
    if len(other) != len(s):
        raise ValueError(f"{len(other)} fallback normals for {len(s)} sums")
    length = np.sqrt((s * s).sum(axis=1))
    short = ~(length >= NORMAL_MIN)
    return np.where(short[:, None], other, s / np.where(short, 1.0, length)[:, None])  # type: ignore[no-any-return]
