"""Display polylines of hole paths within a chord tolerance (ADR 0007 §1 `paths`).

The points are the stations of `desurvey.Trajectories` plus, on minimum-curvature courses, points of the arc
every √(8·R·tol) of depth, R = course length / dogleg angle: a chord of arc length s lies at most
R·(1 − cos(s/2R)) ≤ s²/8R ≤ tol from its arc. The arc points come from the desurvey kernel itself
(`desurvey._delta`), so the polyline goes through `Trajectories.positions()` at every vertex. Other methods
are straight between their points: the stations, and the middle of a course for the balanced tangent (also a
reversed minimum-curvature course, taken as two straight halves).

numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from bf_core.drillholes.desurvey import _STRAIGHT, BALANCED_TANGENT, METHODS, MINIMUM_CURVATURE, _delta
from bf_core.jit import njit

DEFAULT_TOL = 0.01
MIN_TOL = 0.001  # below this the float32 step (≤ 0.49 mm) matters more than the chord
MAX_TOL = 10.0
MAX_POINTS = 50_000_000

COUNT, BOUNDS, FILL = range(3)  # modes of the walk


@njit(cache=True, nogil=True)
def _chords(method, seg, ax, ay, az, bx, by, bz, tol):  # type: ignore[no-untyped-def]
    """Number of straight pieces of one course of `seg` metres between the directions a and b."""
    if seg <= 0.0:
        return 1
    cos_b = ax * bx + ay * by + az * bz
    ux = ay * bz - az * by
    uy = az * bx - ax * bz
    uz = ax * by - ay * bx
    sin_b = math.sqrt(ux * ux + uy * uy + uz * uz)
    if sin_b < _STRAIGHT and cos_b > 0.0:
        return 1  # one direction: straight for every method
    if method == BALANCED_TANGENT:
        return 2  # the corner in the middle
    if method != MINIMUM_CURVATURE:
        return 1
    if sin_b < _STRAIGHT:
        return 2  # reversed: two straight halves (desurvey._delta)
    radius = seg / math.atan2(sin_b, cos_b)
    return max(1, int(math.ceil(seg / math.sqrt(8.0 * radius * tol))))


@njit(cache=True, nogil=True)
def _emit(mode, w, x, y, z, d, origin, out_xyz, out_md, box):  # type: ignore[no-untyped-def]
    if mode == FILL:
        out_xyz[w, 0] = np.float32(x - origin[0])
        out_xyz[w, 1] = np.float32(y - origin[1])
        out_xyz[w, 2] = np.float32(z - origin[2])
        out_md[w] = d
    elif mode == BOUNDS:
        box[0] = min(box[0], x)
        box[1] = min(box[1], y)
        box[2] = min(box[2], z)
        box[3] = max(box[3], x)
        box[4] = max(box[4], y)
        box[5] = max(box[5], z)
    return w + 1


@njit(cache=True, nogil=True)
def _walk(mode, method, offsets, md, xyz, dirs, holes, tol, origin, p_off, out_xyz, out_md, bounds):  # type: ignore[no-untyped-def]
    """COUNT: points of holes[j] into p_off[j + 1]; BOUNDS: also their extent into bounds[j]; FILL: the points
    (xyz − origin as float32, depth) from p_off[j]."""
    for j in range(holes.shape[0]):
        h = holes[j]
        a = offsets[h]
        b = offsets[h + 1]
        box = bounds[j]
        if mode == BOUNDS:
            box[0] = box[1] = box[2] = np.inf
            box[3] = box[4] = box[5] = -np.inf
        w = p_off[j] if mode == FILL else 0
        if b > a:
            w = _emit(mode, w, xyz[a, 0], xyz[a, 1], xyz[a, 2], md[a], origin, out_xyz, out_md, box)
        for s in range(a, b - 1):
            seg = md[s + 1] - md[s]
            m = _chords(
                method, seg, dirs[s, 0], dirs[s, 1], dirs[s, 2], dirs[s + 1, 0], dirs[s + 1, 1], dirs[s + 1, 2], tol
            )
            if mode == COUNT:
                w += m
                continue
            for i in range(1, m):
                along = seg * i / m
                dx, dy, dz = _delta(method, along, seg, dirs[s, 0], dirs[s, 1], dirs[s, 2],
                                    dirs[s + 1, 0], dirs[s + 1, 1], dirs[s + 1, 2])  # fmt: skip
                w = _emit(mode, w, xyz[s, 0] + dx, xyz[s, 1] + dy, xyz[s, 2] + dz, md[s] + along, origin,
                          out_xyz, out_md, box)  # fmt: skip
            w = _emit(mode, w, xyz[s + 1, 0], xyz[s + 1, 1], xyz[s + 1, 2], md[s + 1], origin, out_xyz, out_md, box)
        if mode != FILL:
            p_off[j + 1] = w
        if mode == BOUNDS and b == a:
            box[:] = np.nan


def _args(traj: Any, holes: Any, tol: float) -> tuple[int, np.ndarray, float]:
    if not (math.isfinite(tol) and MIN_TOL <= tol <= MAX_TOL):
        raise ValueError(f"tol must be within {MIN_TOL}…{MAX_TOL} m, got {tol}")
    which = np.require(np.asarray(holes), dtype=np.int64, requirements=["C"])
    if len(which) and (which.min() < 0 or which.max() >= traj.n_holes):
        raise ValueError(f"hole index outside 0…{traj.n_holes - 1}")
    return METHODS.index(traj.method), which, float(tol)


_NO_ORIGIN = np.zeros(3)


def hole_bounds(traj: Any, holes: Any, tol: float = DEFAULT_TOL) -> np.ndarray:
    """Extent (n, 6) of the display polyline of each hole: xmin, ymin, zmin, xmax, ymax, zmax; NaN — no path."""
    code, which, tol = _args(traj, holes, tol)
    bounds = np.empty((len(which), 6))
    p_off = np.zeros(len(which) + 1, dtype=np.int64)
    _walk(BOUNDS, code, traj.offsets, traj.md, traj.xyz, traj.dirs, which, tol, _NO_ORIGIN, p_off,
          np.empty((0, 3), dtype=np.float32), np.empty(0), bounds)  # fmt: skip
    return bounds


def densify(
    traj: Any, holes: Any, tol: float, origin: Any, *, max_points: int = MAX_POINTS
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Polylines of `holes` in their order: offsets (n + 1, int64), xyz (m, 3) float32 from `origin`, md (m,)
    float64. The vertices of hole j are rows offsets[j]:offsets[j + 1]; a hole without a path has none."""
    code, which, tol = _args(traj, holes, tol)
    o = np.ascontiguousarray(np.asarray(origin, dtype=np.float64).reshape(3))
    counts = np.zeros(len(which) + 1, dtype=np.int64)
    no_box = np.empty((len(which), 6))
    _walk(COUNT, code, traj.offsets, traj.md, traj.xyz, traj.dirs, which, tol, o, counts,
          np.empty((0, 3), dtype=np.float32), np.empty(0), no_box)  # fmt: skip
    offsets = np.cumsum(counts)
    total = int(offsets[-1])
    if total > max_points:
        raise ValueError(f"the paths would have {total} points, more than {max_points}: take a larger tolerance")
    xyz = np.empty((total, 3), dtype=np.float32)
    md = np.empty(total)
    _walk(FILL, code, traj.offsets, traj.md, traj.xyz, traj.dirs, which, tol, o, offsets, xyz, md, no_box)
    return offsets, xyz, md
