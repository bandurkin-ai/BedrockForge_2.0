"""Exact pick: the first triangle of a wireframe a ray meets, from its float64 arrays (ТЗ 7.21 «треугольник по
клику»; ADR 0004 §2 «точный выбор в ядре», ADR 0013 §4).

Ported from BF `geo/pick.py`. The index is the part table of the stored wireframe (`wireframes.render["parts"]`):
the ray is tested against the boxes of the parts, the parts it enters are scanned nearest first and the scan
stops at the first part that starts behind the hit already found — a few parts of 64 000 triangles a click.
Differences from BF: the watertight test of `tri_kernels.line_triangle` instead of Möller–Trumbore with
tolerances (a ray aimed at a shared edge or vertex cannot slip between triangles); the planes of the section
window limit the ray (`POST /pick {clip}`); the point of the hit is computed on the triangle from its corners;
a level triangle has no dip direction (None, not 0); one thread.

    ray_pick(xyz, tri, origin, direction, parts=…, clip=…)  → PickHit or None
    fine_parts(xyz, tri, parts)                              → a finer part table, to keep with an open wireframe
    triangle_attitude(a, b, c)                               → area, unit normal, dip, dip direction

Both sides of a triangle are hit. Of equally near triangles (the ray goes through their common edge) the one
with the lower number is returned.

numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.jit import njit

from .tri_kernels import BOX_PAD_M, half_spaces, line_triangle, mesh_arrays, part_arrays, shear, vector

_LEVEL = 1e-12  # level part of a unit normal below which a triangle has no dip direction


@njit(cache=True, nogil=True)
def _box_entry(box, p, o, d, t_min, t_max):  # type: ignore[no-untyped-def]
    """Where the ray o + t · d, t_min ≤ t ≤ t_max, enters the box of part p grown by the pad; inf — it misses."""
    t0 = t_min
    t1 = t_max
    for c in range(3):
        lo = box[p, c] - BOX_PAD_M
        hi = box[p, c + 3] + BOX_PAD_M
        if d[c] == 0.0:
            if o[c] < lo or o[c] > hi:
                return np.inf
        else:
            ta = (lo - o[c]) / d[c]
            tb = (hi - o[c]) / d[c]
            if ta > tb:
                ta, tb = tb, ta
            if ta > t0:
                t0 = ta
            if tb < t1:
                t1 = tb
            if t0 > t1:
                return np.inf
    return t0


@njit(cache=True, nogil=True)
def _pick(xyz, tri, first, count, box, o, d, t_min, t_max):  # type: ignore[no-untyped-def]
    """(t, triangle, v, w) of the nearest crossing of the ray o + t · d (d a unit vector), t_min ≤ t ≤ t_max;
    triangle −1 — none, −2 — a triangle refers to a vertex that does not exist."""
    nv = xyz.shape[0]
    parts = first.shape[0]
    entered = np.empty(parts, dtype=np.int64)  # the parts whose box the ray enters, and where
    entry = np.empty(parts, dtype=np.float64)
    n = 0
    for p in range(parts):
        at = _box_entry(box, p, o, d, t_min, t_max)
        if at < np.inf and count[p] > 0:
            entered[n] = p
            entry[n] = at
            n += 1
    order = np.argsort(entry[:n])
    kx, ky, kz, sx, sy, sz = shear(d[0], d[1], d[2])
    best = t_max
    found = -1
    best_v = 0.0
    best_w = 0.0
    for q in range(n):
        if entry[order[q]] > best:  # this part and every later one start behind the hit already found
            break
        p = entered[order[q]]
        for i in range(first[p], first[p] + count[p]):
            i0, i1, i2 = tri[i, 0], tri[i, 1], tri[i, 2]
            if i0 < 0 or i1 < 0 or i2 < 0 or i0 >= nv or i1 >= nv or i2 >= nv:
                return np.nan, -2, 0.0, 0.0
            hit, t, v, w = line_triangle(xyz, i0, i1, i2, o, kx, ky, kz, sx, sy, sz)
            if hit and t >= t_min and (t < best or (t == best and (found < 0 or i < found))):
                best = t
                found = i
                best_v = v
                best_w = w
    return best, found, best_v, best_w


@njit(cache=True, nogil=True)
def _run_boxes(xyz, tri, first, count, size, out):  # type: ignore[no-untyped-def]
    """Rows [first, count, box] of runs of at most `size` consecutive triangles of every part into `out`.
    Returns their number; −1 — a triangle refers to a vertex that does not exist."""
    nv = xyz.shape[0]
    n = 0
    for p in range(first.shape[0]):
        for a in range(first[p], first[p] + count[p], size):
            b = min(a + size, first[p] + count[p])
            for c in range(3):
                out[n, 2 + c] = np.inf
                out[n, 5 + c] = -np.inf
            for i in range(a, b):
                for k in range(3):
                    q = tri[i, k]
                    if q < 0 or q >= nv:
                        return -1
                    for c in range(3):
                        x = xyz[q, c]
                        if x < out[n, 2 + c]:
                            out[n, 2 + c] = x
                        if x > out[n, 5 + c]:
                            out[n, 5 + c] = x
            out[n, 0] = a
            out[n, 1] = b - a
            n += 1
    return n


def fine_parts(xyz: Any, tri: Any, parts: Any = None, *, size: int = 1024) -> np.ndarray:
    """A finer part table for `ray_pick`: every part of `parts` (`wireframes.render["parts"]`; None — the
    whole mesh) cut into runs of at most `size` consecutive triangles, with their exact boxes — float64 p×8 in
    the layout of `parts`. One pass over the triangles (0.1–0.2 s for 10 million), 64 bytes a run; the
    triangles must be in spatial order (a stored wireframe is). Optional: with the table of the store alone a
    click on 10 million triangles takes 4–6 ms, and up to 30 ms for a ray that grazes a large surface (it scans
    every part of 64 000 triangles on its way); with this one — under 2 ms in every case. Derived data: keep
    it with the wireframe for as long as its geometry is open."""
    if size < 1:
        raise ValueError("a run holds at least one triangle")
    v, t = mesh_arrays(xyz, tri)
    first, count, _box = part_arrays(parts, len(t))
    out = np.empty((int(((count + size - 1) // size).sum()), 8), dtype=np.float64)
    n = _run_boxes(v, t, first, count, size, out)
    if n < 0:
        raise ValueError("a triangle refers to a vertex that does not exist")
    return out[:n]


# --- the answer --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Attitude:
    """How a triangle lies. Dip — the angle of its plane from the level one, 0…90°; dip direction — the
    azimuth it descends to (clockwise from north, [0; 360)), None for a level triangle; for a vertical one —
    the azimuth its normal looks at. A triangle without area has an area of 0 and nothing else."""

    area: float
    normal: tuple[float, float, float] | None  # unit, by the order of the corners
    dip: float | None
    dip_direction: float | None


def triangle_attitude(a: Any, b: Any, c: Any) -> Attitude:
    p = np.asarray(a, dtype=np.float64)
    n = np.cross(np.asarray(b, dtype=np.float64) - p, np.asarray(c, dtype=np.float64) - p)
    twice = float(np.linalg.norm(n))
    if not twice > 0.0:
        return Attitude(0.0, None, None, None)
    unit = n / twice + 0.0  # -0.0 → 0.0
    level = math.hypot(unit[0], unit[1])
    dip = math.degrees(math.atan2(level, abs(unit[2])))
    direction = None
    if level > _LEVEL:
        up = unit if unit[2] >= 0.0 else -unit
        direction = math.fmod(math.degrees(math.atan2(up[0], up[1])) + 360.0, 360.0)
    return Attitude(0.5 * twice, (float(unit[0]), float(unit[1]), float(unit[2])), dip, direction)


@dataclass(frozen=True)
class PickHit:
    triangle: int  # its number in `tri` as given (the order of the stored wireframe)
    distance: float  # from the origin of the ray, metres
    point: tuple[float, float, float]  # on the triangle, project coordinates
    vertices: tuple[tuple[float, float, float], ...]  # the three corners, exactly as stored
    indices: tuple[int, int, int]  # their numbers in `xyz`
    normal: tuple[float, float, float] | None  # unit, by the order of the corners
    area: float
    dip: float | None  # degrees, 0…90
    dip_direction: float | None  # degrees clockwise from north; None — a level triangle


def ray_pick(
    xyz: Any,
    tri: Any,
    origin: Any,
    direction: Any,
    *,
    parts: Any = None,
    clip: Any = None,
    max_distance: float = math.inf,
) -> PickHit | None:
    """The nearest triangle the ray `origin + s · direction`, s ≥ 0, meets (project coordinates), or None.

    `xyz` float64 n×3 and `tri` int32 m×3 — the blobs of a wireframe (memory maps are read in place); `parts` —
    `wireframes.render["parts"]` (without it every triangle is tested); `clip` — planes k×4 [nx, ny, nz, c] of
    the section window: only the piece of the ray where every n·p + c ≥ 0 counts; `max_distance` — the hit
    found so far in other wireframes. ValueError: a direction that is zero, a value that is not a number, a
    triangle of a vertex that is not there.
    """
    v, t = mesh_arrays(xyz, tri)
    o = vector(origin, "origin")
    d = vector(direction, "direction")
    length = float(np.linalg.norm(d))
    if not length > 0.0:
        raise ValueError("the direction of the ray is zero")
    d = d / length
    t_min, t_max = 0.0, float(max_distance)
    for nx, ny, nz, c in half_spaces(clip).tolist():
        along = nx * d[0] + ny * d[1] + nz * d[2]
        height = nx * o[0] + ny * o[1] + nz * o[2] + c
        if along == 0.0:
            if height < 0.0:
                return None
        elif along > 0.0:
            t_min = max(t_min, -height / along)
        else:
            t_max = min(t_max, -height / along)
    first, count, box = part_arrays(parts, len(t))
    if not t_min <= t_max or len(t) == 0:
        return None
    dist, found, bv, bw = _pick(v, t, first, count, box, o, d, t_min, t_max)
    if found == -2:
        raise ValueError("a triangle refers to a vertex that does not exist")
    if found < 0:
        return None
    i0, i1, i2 = (int(k) for k in t[found])
    a, b, c3 = (np.array(v[k], dtype=np.float64) for k in (i0, i1, i2))
    at = triangle_attitude(a, b, c3)
    point = a + bv * (b - a) + bw * (c3 - a)
    # (a triangle without area is not hit; one whose area is lost to rounding keeps None in its attitude)
    return PickHit(
        triangle=int(found),
        distance=float(dist),
        point=(float(point[0]), float(point[1]), float(point[2])),
        vertices=tuple((float(p[0]), float(p[1]), float(p[2])) for p in (a, b, c3)),
        indices=(i0, i1, i2),
        normal=at.normal,
        area=at.area,
        dip=at.dip,
        dip_direction=at.dip_direction,
    )
