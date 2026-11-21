"""The direction of a hole at a depth and the true thickness of intervals summed along the hole (ТЗ 8.5.1;
ADR 0021 §3).

    d = directions(traj, hole, depth, threads=…, tick=…)            float64 [n, 3], unit; NaN — no direction
    t = thickness(traj, hole, frm, to, normal, step=1.0, eps=0.1, threads=…, tick=…) -> Thickness
        t.tt              float64 [n]  TT = Σ_j l_j·|n·d_j|, NaN — none (never a zero for a missing one)
        t.flag            uint8 [n]    bits of TT_FLAG: 1 — TT / l < ε_в; 2 — no normal; 8 — no trajectory
        t.theta, t.azimuth  float64 [n]  HOLE_THETA, HOLE_AZ — by the direction at the middle of the interval
        t.n_bad           rows left out: without depths or with TO ≤ FROM;  t.n_capped — see «Parts»
    tangent(t1, t2, f, method=…)                                    the direction at the share f of one course
    warm()                                                          compiles the kernels (the step `intercepts`)

`traj` — `desurvey.Trajectories` of the set; `hole` — the dense hole index of every row (`coords.hole_index`;
−1 — the set does not have the hole); `normal` — unit normals [n, 3] of the rows (`formulas.plane_normal`,
`upper_normal`) or one [3] for all of them; a row whose normal has a NaN has no orientation.

Direction. The tangent to the very curve `Trajectories.positions` goes along — the derivative of
`desurvey._delta` by depth, a unit vector:
  minimum curvature   t(f) = [sin((1 − f)·ψ)·t1 + sin(f·ψ)·t2] / sin ψ, ψ = atan2(|t1 × t2|, t1·t2),
                      f = (D − D1) / (D2 − D1); sin ψ < 1e−9 — t1; a reversed course (t2 = −t1) — t1 in its
                      upper half, t2 in the lower one;
  tangent             t1;   balanced tangent — t1 in the upper half of the course, t2 in the lower one;
  average angle       the direction of the mean azimuth and the mean dip, the same along the course;
  stations with coordinates (a trench) — the unit vector of the chord, whatever the depths say of its length.
A depth above the collar takes the direction at the collar, a depth at or below the last station — the
direction of that station (the path is extended to TD with it). A station belongs to the course below it —
the search is that of `positions`. No direction (NaN): an unknown hole, a hole without a trajectory, a
missing depth, a segment between two stations with the same coordinates.

Parts. An interval is cut into k = ⌈(TO − FROM) / step⌉ equal parts, d_j is taken at the middle of a part,
TT = l_j·Σ_j |n·d_j| — a pairwise sum in the order of the parts (terms are combined as a binary counter, the
rest is folded from the last block to the first). On a straight hole it is l·|n·d| to 1e−12·l. The error of
a part on an arc is ψ²/24 of it (ψ — the turn of the hole on the part); where the path has a corner (the
methods other than the minimum curvature, a trench) and the corner falls inside a part — up to half the part
times the jump of |n·d|. No more than `MAX_PARTS` parts an interval: a longer one takes a larger step and is
counted in `n_capped` (a line of the journal).

Strips. Rows are independent — nothing is summed across them — so `STRIP_ROWS` rows make a strip, strips go
to Python threads (the kernels are `nogil`; no `prange`, no `fastmath`), and the result is the same bit for
bit with 1, 2, 4 and 8 threads and for any order of the rows (ТЗ 8.0.2; ADR 0021, решение 20). `tick` gets
the share done after every strip and may raise to cancel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.composites import strips
from bf_core.composites.length import check_range
from bf_core.composites.strips import Tick
from bf_core.drillholes.desurvey import (
    _STRAIGHT,
    _VERTICAL,
    AVERAGE_ANGLE,
    METHODS,
    MINIMUM_CURVATURE,
    TANGENT,
    Trajectories,
)
from bf_core.jit import njit

from .formulas import (
    EPS_DEFAULT,
    EPS_RANGE,
    FLAG_NO_ORIENTATION,
    FLAG_NO_PATH,
    FLAG_PARALLEL,
    STEP_DEFAULT,
    STEP_RANGE,
)

STRIP_ROWS = 1 << 16  # rows of a strip: a few milliseconds between two ticks
MAX_PARTS = 100_000  # parts of one interval (ADR 0021 §12)

_CONST, _ARC, _HALVES = range(3)  # how the direction goes along a course
_LEVELS = 64  # of the pairwise sum: more than log2(MAX_PARTS)


# --- kernels -------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _course(method, ax, ay, az, bx, by, bz):  # type: ignore[no-untyped-def]
    """The direction along a course between the directions a (upper station) and b (lower station) — the
    branches of `desurvey._delta`: (kind, cx, cy, cz, ψ, sin ψ). `_CONST` — c all along; `_ARC` — the formula
    of ТЗ 8.5.1 with ψ; `_HALVES` — a in the upper half, b in the lower one."""
    if ax == bx and ay == by and az == bz:  # straight; of stations with coordinates — the chord per metre
        norm = math.sqrt(ax * ax + ay * ay + az * az)
        if norm > 0.0:
            return _CONST, ax / norm, ay / norm, az / norm, 0.0, 1.0
        return _CONST, np.nan, np.nan, np.nan, 0.0, 1.0  # two stations at one point: no direction
    if method == TANGENT:
        return _CONST, ax, ay, az, 0.0, 1.0
    if method == AVERAGE_ANGLE:
        ha = math.hypot(ax, ay)
        hb = math.hypot(bx, by)
        dip = 0.5 * (math.atan2(az, ha) + math.atan2(bz, hb))
        if ha < _VERTICAL:
            azimuth = math.atan2(bx, by) if hb >= _VERTICAL else 0.0
        elif hb < _VERTICAL:
            azimuth = math.atan2(ax, ay)
        else:
            azimuth = math.atan2(ax, ay)
            turn = math.atan2(bx, by) - azimuth
            if turn > math.pi:
                turn -= 2.0 * math.pi
            elif turn < -math.pi:
                turn += 2.0 * math.pi
            azimuth += 0.5 * turn
        c = math.cos(dip)
        return _CONST, math.sin(azimuth) * c, math.cos(azimuth) * c, math.sin(dip), 0.0, 1.0
    if method == MINIMUM_CURVATURE:
        cos_b = ax * bx + ay * by + az * bz
        ux = ay * bz - az * by
        uy = az * bx - ax * bz
        uz = ax * by - ay * bx
        sin_b = math.sqrt(ux * ux + uy * uy + uz * uz)
        if sin_b >= _STRAIGHT:
            psi = math.atan2(sin_b, cos_b)
            return _ARC, 0.0, 0.0, 0.0, psi, math.sin(psi)
        if cos_b > 0.0:
            return _CONST, ax, ay, az, 0.0, 1.0
    return _HALVES, 0.0, 0.0, 0.0, 0.0, 1.0  # the balanced tangent; a reversed course of the minimum curvature


@njit(cache=True, nogil=True)
def _along(kind, psi, sin_psi, ax, ay, az, bx, by, bz, f):  # type: ignore[no-untyped-def]
    """The direction at the share f of a course of the kind `_ARC` or `_HALVES`."""
    if kind == _HALVES:
        if f < 0.5:
            return ax, ay, az
        return bx, by, bz
    u = math.sin((1.0 - f) * psi) / sin_psi
    v = math.sin(f * psi) / sin_psi
    return u * ax + v * bx, u * ay + v * by, u * az + v * bz


@njit(cache=True, nogil=True)
def _locate(md, first, last, hint, d):  # type: ignore[no-untyped-def]
    """The station s of a hole (stations first … last) a depth d ≥ 0 belongs to: s = last — at or below the
    last station; else the course s … s + 1 with md[s] ≤ d < md[s + 1], s — the last such station (the search
    of `desurvey._positions`). `hint` — the answer for another depth, or −1: rows usually go down a hole."""
    if d >= md[last]:
        return last
    lo = first
    if first <= hint < last and md[hint] <= d:
        if d < md[hint + 1]:
            return hint
        lo = hint + 1
        if lo < last and d < md[lo + 1]:
            return lo
    hi = last
    while hi - lo > 1:
        mid = (lo + hi) >> 1
        if md[mid] <= d:
            lo = mid
        else:
            hi = mid
    return lo


@njit(cache=True, nogil=True)
def _hole_angles(dx, dy, dz):  # type: ignore[no-untyped-def]
    """θ and Aс, degrees, of a unit direction — `formulas.hole_angles` for one vector."""
    level = math.hypot(dx, dy)
    theta = math.degrees(math.atan2(level, -dz))
    if level < _VERTICAL:
        return theta, 0.0
    azimuth = math.degrees(math.atan2(dx, dy)) % 360.0
    if azimuth >= 360.0:
        azimuth = 0.0
    return theta, azimuth


@njit(cache=True, nogil=True)
def _directions(method, offsets, md, dirs, hole, depth, r0, r1, out):  # type: ignore[no-untyped-def]
    """The unit direction of the rows r0 ≤ r < r1 into out[r]."""
    n_holes = offsets.shape[0] - 1
    hint = -1
    for r in range(r0, r1):
        h = hole[r]
        d = depth[r]
        if h < 0 or h >= n_holes or d != d or offsets[h] == offsets[h + 1]:
            out[r, 0] = out[r, 1] = out[r, 2] = np.nan
            continue
        last = offsets[h + 1] - 1
        if d < 0.0:  # depths are clipped to the hole, as `positions` clips them
            d = 0.0
        s = _locate(md, offsets[h], last, hint, d)
        hint = s
        e = s + 1 if s < last else s
        kind, cx, cy, cz, psi, sin_psi = _course(method, dirs[s, 0], dirs[s, 1], dirs[s, 2],
                                                 dirs[e, 0], dirs[e, 1], dirs[e, 2])  # fmt: skip
        if kind != _CONST:
            cx, cy, cz = _along(kind, psi, sin_psi, dirs[s, 0], dirs[s, 1], dirs[s, 2], dirs[e, 0], dirs[e, 1],
                                dirs[e, 2], (d - md[s]) / (md[e] - md[s]))  # fmt: skip
        out[r, 0] = cx
        out[r, 1] = cy
        out[r, 2] = cz


@njit(cache=True, nogil=True)
def _thickness(method, offsets, md, dirs, hole, frm, to, normal, step, eps, max_parts, r0, r1, tt, flag, theta,
               azimuth):  # type: ignore[no-untyped-def]  # fmt: skip
    """TT, the bits 1, 2, 8 of the flag, θ and Aс of the rows r0 ≤ r < r1 (module docstring). Returns the rows
    left out for their depths and the intervals that took a larger step."""
    n_holes = offsets.shape[0] - 1
    shared = normal.shape[0] == 1  # one normal for all the rows
    acc = np.empty(_LEVELS, dtype=np.float64)
    n_bad = 0
    n_capped = 0
    hint = -1
    key = -1  # whose constants are kept: s — the course s … s + 1; −2 − s — at and below the last station s
    kind = _CONST
    cx = cy = cz = psi = 0.0
    sin_psi = 1.0
    for r in range(r0, r1):
        h = hole[r]
        f = frm[r]
        t = to[r]
        q = 0 if shared else r
        nx = normal[q, 0]
        ny = normal[q, 1]
        nz = normal[q, 2]
        bits = 0
        if not (math.isfinite(nx) and math.isfinite(ny) and math.isfinite(nz)):
            bits |= FLAG_NO_ORIENTATION
        length = t - f
        measured = math.isfinite(f) and math.isfinite(t) and length > 0.0
        if not measured:
            n_bad += 1
        tt[r] = np.nan
        theta[r] = np.nan
        azimuth[r] = np.nan
        if h < 0 or h >= n_holes or offsets[h] == offsets[h + 1]:
            flag[r] = bits | FLAG_NO_PATH
            continue
        first = offsets[h]
        last = offsets[h + 1] - 1
        k = 0
        part = 0.0
        if measured and bits == 0:
            ratio = length / step
            if ratio > max_parts:
                k = max_parts
                n_capped += 1
            else:
                k = max(1, int(math.ceil(ratio)))
            part = length / k
        top = 0
        for j in range(-1, k):  # −1 — the middle of the interval: θ and Aс; then the middles of the parts
            d = 0.5 * (f + t) if j < 0 else f + (j + 0.5) * part
            if d != d:
                continue
            if d < 0.0:
                d = 0.0
            s = _locate(md, first, last, hint, d)
            hint = s
            e = s + 1 if s < last else s
            want = s if s < last else -2 - s
            if want != key:
                key = want
                kind, cx, cy, cz, psi, sin_psi = _course(method, dirs[s, 0], dirs[s, 1], dirs[s, 2],
                                                         dirs[e, 0], dirs[e, 1], dirs[e, 2])  # fmt: skip
            if kind == _CONST:
                dx = cx
                dy = cy
                dz = cz
            else:
                dx, dy, dz = _along(kind, psi, sin_psi, dirs[s, 0], dirs[s, 1], dirs[s, 2], dirs[e, 0],
                                    dirs[e, 1], dirs[e, 2], (d - md[s]) / (md[e] - md[s]))  # fmt: skip
            if j < 0:
                theta[r], azimuth[r] = _hole_angles(dx, dy, dz)
                continue
            v = abs(nx * dx + ny * dy + nz * dz)
            m = j + 1
            while (m & 1) == 0:  # the pairwise sum: a binary counter of blocks
                top -= 1
                v = acc[top] + v
                m >>= 1
            acc[top] = v
            top += 1
        if k > 0:
            total = acc[top - 1]
            for i in range(top - 2, -1, -1):
                total = acc[i] + total
            value = part * total
            tt[r] = value
            if value != value:  # a part without a direction
                bits |= FLAG_NO_PATH
            elif value < eps * length:
                bits |= FLAG_PARALLEL
        flag[r] = bits
    return n_bad, n_capped


# --- wrappers ------------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Thickness:
    """The true thickness of intervals — see the module docstring."""

    tt: np.ndarray  # float64 [n]: TT, m; NaN — none
    flag: np.ndarray  # uint8 [n]: FLAG_PARALLEL | FLAG_NO_ORIENTATION | FLAG_NO_PATH
    theta: np.ndarray  # float64 [n]: HOLE_THETA, degrees; NaN — no trajectory or no depths
    azimuth: np.ndarray  # float64 [n]: HOLE_AZ, degrees
    n_bad: int  # rows without depths or with TO ≤ FROM: no TT
    n_capped: int  # intervals of more than MAX_PARTS parts: computed with a larger step


def _plain(values: Any, dtype: Any) -> np.ndarray:
    """A C-contiguous writable array of the dtype (one numba specialization of every kernel)."""
    return np.require(values, dtype=dtype, requirements=["C", "W"])


def _holes(hole: Any) -> np.ndarray:
    h = np.asarray(hole)
    if h.size and h.dtype.kind not in "iu":
        raise TypeError(f"hole must be an integer array of hole indices, got {h.dtype}")
    return _plain(h, np.int64)


def _same_length(hole: np.ndarray, *columns: np.ndarray) -> None:
    if hole.ndim != 1 or any(c.shape != hole.shape for c in columns):
        raise ValueError("the hole and the depths of the rows must be one-dimensional and of one length")


def _stations(traj: Trajectories) -> tuple[int, np.ndarray, np.ndarray, np.ndarray]:
    return (METHODS.index(traj.method), _plain(traj.offsets, np.int64), _plain(traj.md, np.float64),
            _plain(traj.dirs, np.float64))  # fmt: skip


def directions(traj: Trajectories, hole: Any, depth: Any, *, threads: int = 1, tick: Tick | None = None) -> np.ndarray:
    """The unit direction down the hole [n, 3] at measured depths; NaN rows — no direction (module docstring)."""
    h = _holes(hole)
    d = _plain(depth, np.float64)
    _same_length(h, d)
    method, offsets, md, dirs = _stations(traj)
    out = np.empty((h.shape[0], 3), dtype=np.float64)
    strips.run(lambda lo, hi: _directions(method, offsets, md, dirs, h, d, lo, hi, out), h.shape[0],
               threads=threads, tick=tick, size=STRIP_ROWS)  # fmt: skip
    return out


def thickness(
    traj: Trajectories,
    hole: Any,
    frm: Any,
    to: Any,
    normal: Any,
    *,
    step: float = STEP_DEFAULT,
    eps: float = EPS_DEFAULT,
    threads: int = 1,
    tick: Tick | None = None,
) -> Thickness:
    """TT = Σ_j l_j·|n·d_j| of the intervals [frm; to] of holes with the unit normals `normal` ([n, 3] or one
    [3]); `step` — the part of a hole, m; `eps` — ε_в (the ranges of ТЗ 8.5.4, else CompositeError
    `bad_param`). See the module docstring."""
    step = check_range(step, *STEP_RANGE, "Часть ствола", "step", " м")
    eps = check_range(eps, *EPS_RANGE, "Порог ε_в", "eps")
    h = _holes(hole)
    f = _plain(frm, np.float64)
    t = _plain(to, np.float64)
    _same_length(h, f, t)
    n = h.shape[0]
    nrm = _plain(normal, np.float64)
    if nrm.shape == (3,):
        nrm = nrm.reshape(1, 3)
    if nrm.shape not in ((n, 3), (1, 3)):
        raise ValueError(f"normal must be [{n}, 3] or [3], got the shape {nrm.shape}")
    method, offsets, md, dirs = _stations(traj)
    tt = np.empty(n, dtype=np.float64)
    flag = np.empty(n, dtype=np.uint8)
    theta = np.empty(n, dtype=np.float64)
    azimuth = np.empty(n, dtype=np.float64)
    counts = strips.run(
        lambda lo, hi: _thickness(method, offsets, md, dirs, h, f, t, nrm, step, eps, MAX_PARTS, lo, hi, tt, flag,
                                  theta, azimuth),
        n, threads=threads, tick=tick, size=STRIP_ROWS,
    )  # fmt: skip
    return Thickness(tt, flag, theta, azimuth, sum(int(c[0]) for c in counts), sum(int(c[1]) for c in counts))


def tangent(t1: Any, t2: Any, f: float, *, method: str = "minimum_curvature") -> np.ndarray:
    """The direction [3] at the share f ∈ [0; 1] of a course between the unit directions t1 (upper station)
    and t2 (lower station) — ТЗ 8.5.1 for the minimum curvature."""
    a = np.asarray(t1, dtype=np.float64)
    b = np.asarray(t2, dtype=np.float64)
    if a.shape != (3,) or b.shape != (3,) or not 0.0 <= float(f) <= 1.0:
        raise ValueError("t1 and t2 must be vectors of 3 components and f a share of [0; 1]")
    kind, cx, cy, cz, psi, sin_psi = _course(METHODS.index(method), a[0], a[1], a[2], b[0], b[1], b[2])
    if kind != _CONST:
        cx, cy, cz = _along(kind, psi, sin_psi, a[0], a[1], a[2], b[0], b[1], b[2], float(f))
    return np.array([cx, cy, cz], dtype=np.float64)


def warm() -> None:
    """Compiles the kernels with the dtypes of the job and of the preview (into NUMBA_CACHE_DIR)."""
    from bf_core.drillholes.desurvey import Collars, Surveys, desurvey

    traj = desurvey(Collars([0, 1], [0.0, 5.0], [0.0, 5.0], [0.0, 0.0], [20.0, 20.0]),
                    Surveys([0, 0], [0.0, 10.0], [0.0, 90.0], [-90.0, -45.0]))  # fmt: skip
    hole = np.array([0, 0, 1], dtype=np.int64)
    frm = np.array([0.0, 4.0, 2.0])
    directions(traj, hole, frm)
    thickness(traj, hole, frm, frm + 3.0, np.array([0.0, 0.0, 1.0]))
    tangent([0.0, 0.0, -1.0], [0.0, 1.0, 0.0], 0.5)
