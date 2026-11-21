"""Oriented core: a measurement (α_к, β_к) → the world normal of the plane, and measurements → intervals
(ТЗ 8.5.2; ADR 0021 §6).

    n = core_normal(d, alpha_c, beta_c, line="boh")               the formula; d — directions down the hole [..., 3]
    m = measurements(traj, dense, depth, alpha_c, beta_c, line="boh", threads=…, tick=…) -> Measurements
        m.normal     float64 [m, 3]  the world upper normal; NaN — the measurement was not turned or is unused
        m.sin_alpha  float64 [m]     sin α_к; NaN — unused
        m.state      uint8 [m]       STATE_OK | STATE_LOCAL | STATE_UNUSED;  m.n_ok, m.n_local, m.n_unused
    o = assign(hole, frm, to, m_hole, m_depth, m, d_core=10.0, threads=…, tick=…) -> CoreOrientation
        o.kind       uint8 [n]       KIND_NONE | KIND_MEASURED («керн: замер») | KIND_NEAREST («керн: ближайший»)
        o.normal     float64 [n, 3]  the normal of the interval (ALPHA, DIPDIR — `formulas.plane_angles`); NaN — none
        o.sin_alpha  float64 [n]     not NaN — TT = l·sin α_к; NaN — TT is the sum along the hole with `normal`
        o.flag       uint8 [n]       FLAG_CORE_LOCAL (bit 4): TT = l·sin α_к, no ALPHA and DIPDIR
        o.count      int32 [n]       measurements inside the interval
    warm()                           compiles the kernels (the step `intercepts`)

The formula, as the ТЗ writes it. d — the direction of the hole at the depth of the measurement
(`directions.directions`), g = (0, 0, −1):
    e1 = (g − (g·d)·d) / ‖g − (g·d)·d‖   the bottom line of the core (BOH); the top line (TOH) — e1 = −e1
    e2 = d × e1                           90° clockwise, looking down the hole
    n  = sin α_к·d − cos α_к·(cos β_к·e1 + sin β_к·e2)        β_к — to the far point of the ellipse down the hole
then to the upper side (`formulas.upper_normal`): n_z < 0 → −n. |n·d| = sin α_к always. β_к is taken modulo
360. ‖g − (g·d)·d‖ = sin θ: at θ < 1° — and at θ > 179°, a hole straight up — the bottom line is not defined
and the measurement is not turned into a world normal.

A measurement. `dense` — the dense hole index of the trajectories (−1 — none), `depth` — its depth (of an
interval table — the middle). States:
    STATE_OK      turned: `normal` is its plane in the world
    STATE_LOCAL   not turned — θ < 1° or the hole has no trajectory: it knows sin α_к only
    STATE_UNUSED  takes no part and is counted for the journal: no depth; α_к missing or outside [0; 90];
                  β_к missing (but α_к = 90° needs no β_к: the plane is square to the core)

An interval [FROM; TO] of the same hole (`hole` and `m_hole` — any integer key of the hole, the same for
both: its id; holes without a trajectory take part too):
    measurements inside — a depth in [FROM − 1e−6; TO + 1e−6] m (one on a boundary belongs to both intervals):
        one turned       KIND_MEASURED, its normal, TT = l·sin α_к (ТЗ)
        several turned   KIND_MEASURED, the principal eigenvector of Σ n·nᵀ (`numpy.linalg.eigh`, n_z ≥ 0), TT —
                         the sum along the hole
        none turned      KIND_MEASURED, no normal, TT = l·(the mean sin α_к of them), bit 4. Measurements that
                         were not turned count only where no turned one is inside
    none inside — the nearest turned measurement of the hole not farther than d_к (+ 1e−6 m) along the hole
        from the nearer end of the interval; equal distances — the upper one: KIND_NEAREST, TT — the sum
    else KIND_NONE. A row without depths or with TO ≤ FROM gets nothing.

Strips and threads — as `directions`: rows are independent, `directions.STRIP_ROWS` rows a strip, the same
bits with 1, 2, 4 and 8 threads; the eigenvectors are taken afterwards in one thread.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.composites import strips
from bf_core.composites.intervals import DEPTH_EPS
from bf_core.composites.length import check_choice, check_range
from bf_core.composites.strips import Tick
from bf_core.drillholes.desurvey import Trajectories
from bf_core.jit import njit

from . import D_CORE_DEFAULT, D_CORE_RANGE, LINES  # d_к, m, and the line β_к is counted from (ТЗ 8.5.4)
from . import directions as dr
from .formulas import FLAG_CORE_LOCAL, upper_normal

STEEP_DEG = 1.0  # θ below which the bottom line of the core is not defined

STATE_OK, STATE_LOCAL, STATE_UNUSED = range(3)
KIND_NONE, KIND_MEASURED, KIND_NEAREST = range(3)


# --- the formula ---------------------------------------------------------------------------------------------


def _line_sign(line: str) -> float:
    return 1.0 if check_choice(line, LINES, "Линия отсчёта керна", "line") == "boh" else -1.0


def core_normal(d: Any, alpha_c: Any, beta_c: Any, line: str = "boh") -> np.ndarray:
    """The world upper normal [..., 3] of the plane of a core measurement: α_к ∈ [0; 90] — the angle between
    the plane and the core axis, β_к — around the core from the line `line`, degrees; d — the direction down
    the hole there (of any length). NaN: θ < 1°, a missing d, α_к or β_к (module docstring)."""
    sign = _line_sign(line)
    v = np.asarray(d, dtype=np.float64)
    if v.shape[-1:] != (3,):
        raise ValueError(f"directions must have 3 components, got the shape {v.shape}")
    a = np.asarray(alpha_c, dtype=np.float64)[..., None]
    b = np.asarray(beta_c, dtype=np.float64)[..., None]
    with np.errstate(invalid="ignore", divide="ignore"):
        u = v / np.sqrt((v * v).sum(axis=-1, keepdims=True))
        b = np.where(np.isfinite(b), b % 360.0, np.where(a == 90.0, 0.0, np.nan))
        a = np.radians(np.where((a >= 0.0) & (a <= 90.0), a, np.nan))
        b = np.radians(b)
        g = np.array([0.0, 0.0, -1.0])
        p = g - (u * g).sum(axis=-1, keepdims=True) * u  # g − (g·d)·d
        level = np.sqrt((p * p).sum(axis=-1, keepdims=True))  # sin θ
        e1 = sign * p / level
        e2 = np.cross(u, e1)
        n = np.sin(a) * u - np.cos(a) * (np.cos(b) * e1 + np.sin(b) * e2)
        n = np.where(level < math.sin(math.radians(STEEP_DEG)), np.nan, n)
    return upper_normal(n)


# --- kernels -------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _group(keys, key, hint):  # type: ignore[no-untyped-def]
    """The index of `key` among the ascending `keys`, −1 — none; `hint` — the answer for the row before."""
    n = keys.shape[0]
    if 0 <= hint < n and keys[hint] == key:
        return hint
    lo = 0
    hi = n
    while lo < hi:
        mid = (lo + hi) >> 1
        if keys[mid] < key:
            lo = mid + 1
        else:
            hi = mid
    if lo < n and keys[lo] == key:
        return lo
    return -1


@njit(cache=True, nogil=True)
def _lower(depth, a, b, x):  # type: ignore[no-untyped-def]
    """The first of the measurements a ≤ i < b (ascending depths) with depth[i] ≥ x; b — none."""
    while a < b:
        mid = (a + b) >> 1
        if depth[mid] < x:
            a = mid + 1
        else:
            b = mid
    return a


@njit(cache=True, nogil=True)
def _assign(keys, starts, m_depth, m_turned, m_normal, m_sin, hole, frm, to, d_core, r0, r1, kind, normal,
            sin_alpha, flag, count, n_turned):  # type: ignore[no-untyped-def]  # fmt: skip
    """The rows r0 ≤ r < r1 (module docstring). The measurements go by (hole, depth): those of keys[g] are
    starts[g] : starts[g + 1]. A row with several turned measurements gets no normal here — `n_turned[r]` says
    how many it has."""
    g = -1
    for r in range(r0, r1):
        kind[r] = KIND_NONE
        normal[r, 0] = normal[r, 1] = normal[r, 2] = np.nan
        sin_alpha[r] = np.nan
        flag[r] = 0
        count[r] = 0
        n_turned[r] = 0
        f = frm[r]
        t = to[r]
        if not (math.isfinite(f) and math.isfinite(t) and t > f):
            continue
        g = _group(keys, hole[r], g)
        if g < 0:
            continue
        a = starts[g]
        b = starts[g + 1]
        i = _lower(m_depth, a, b, f - DEPTH_EPS)
        n_ok = 0
        n_local = 0
        one = -1
        local_sum = 0.0
        j = i
        while j < b and m_depth[j] <= t + DEPTH_EPS:
            if m_turned[j]:
                n_ok += 1
                one = j
            else:
                n_local += 1
                local_sum += m_sin[j]
            j += 1
        count[r] = n_ok + n_local
        n_turned[r] = n_ok
        if n_ok > 0:
            kind[r] = KIND_MEASURED
            if n_ok == 1:
                normal[r, 0] = m_normal[one, 0]
                normal[r, 1] = m_normal[one, 1]
                normal[r, 2] = m_normal[one, 2]
                sin_alpha[r] = m_sin[one]
            continue
        if n_local > 0:
            kind[r] = KIND_MEASURED
            sin_alpha[r] = local_sum / n_local
            flag[r] = FLAG_CORE_LOCAL
            continue
        up = -1
        k = i - 1
        while k >= a and f - m_depth[k] <= d_core + DEPTH_EPS:
            if m_turned[k]:
                up = k
                break
            k -= 1
        down = -1
        k = i
        while k < b and m_depth[k] - t <= d_core + DEPTH_EPS:
            if m_turned[k]:
                down = k
                break
            k += 1
        if up >= 0 and (down < 0 or f - m_depth[up] <= m_depth[down] - t):  # equal distances — the upper one
            down = up
        if down >= 0:
            kind[r] = KIND_NEAREST
            normal[r, 0] = m_normal[down, 0]
            normal[r, 1] = m_normal[down, 1]
            normal[r, 2] = m_normal[down, 2]


@njit(cache=True, nogil=True)
def _scatter(keys, starts, m_depth, m_turned, m_normal, hole, frm, to, rows, out):  # type: ignore[no-untyped-def]
    """Σ n·nᵀ of the turned measurements inside the intervals `rows`, in the order of their depths: out[i]."""
    g = -1
    for i in range(rows.shape[0]):
        r = rows[i]
        g = _group(keys, hole[r], g)
        j = _lower(m_depth, starts[g], starts[g + 1], frm[r] - DEPTH_EPS)
        for p in range(3):
            for q in range(3):
                out[i, p, q] = 0.0
        while j < starts[g + 1] and m_depth[j] <= to[r] + DEPTH_EPS:
            if m_turned[j]:
                for p in range(3):
                    for q in range(3):
                        out[i, p, q] += m_normal[j, p] * m_normal[j, q]
            j += 1


# --- wrappers ------------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Measurements:
    """Core measurements turned into world normals — see the module docstring."""

    normal: np.ndarray  # float64 [m, 3]: the world upper normal; NaN — not turned or unused
    sin_alpha: np.ndarray  # float64 [m]: sin α_к; NaN — unused
    state: np.ndarray  # uint8 [m]: STATE_OK | STATE_LOCAL | STATE_UNUSED

    def _count(self, state: int) -> int:
        return int((self.state == state).sum())

    @property
    def n_ok(self) -> int:
        return self._count(STATE_OK)

    @property
    def n_local(self) -> int:
        """Not turned into a world normal: θ < 1° or no trajectory."""
        return self._count(STATE_LOCAL)

    @property
    def n_unused(self) -> int:
        """Left out: no depth, no α_к or β_к, α_к outside [0; 90] — a line of the journal."""
        return self._count(STATE_UNUSED)


@dataclass(frozen=True, eq=False)
class CoreOrientation:
    """The orientation of intervals by oriented core — see the module docstring."""

    kind: np.ndarray  # uint8 [n]: KIND_NONE | KIND_MEASURED | KIND_NEAREST
    normal: np.ndarray  # float64 [n, 3]: NaN — none
    sin_alpha: np.ndarray  # float64 [n]: TT = l·sin α_к; NaN — TT is the sum along the hole
    flag: np.ndarray  # uint8 [n]: FLAG_CORE_LOCAL
    count: np.ndarray  # int32 [n]: measurements inside the interval


def measurements(
    traj: Trajectories,
    dense: Any,
    depth: Any,
    alpha_c: Any,
    beta_c: Any,
    *,
    line: str = "boh",
    threads: int = 1,
    tick: Tick | None = None,
) -> Measurements:
    """Core measurements of a set of holes as world normals: `dense` — the dense hole index of every
    measurement (−1 — none), `depth` — its depth, `alpha_c`, `beta_c` — α_к and β_к, degrees."""
    _line_sign(line)
    h = dr._holes(dense)
    s = dr._plain(depth, np.float64)
    a = dr._plain(alpha_c, np.float64)
    b = dr._plain(beta_c, np.float64)
    dr._same_length(h, s, a, b)
    d = dr.directions(traj, h, s, threads=threads, tick=tick)
    with np.errstate(invalid="ignore"):
        used = np.isfinite(s) & (a >= 0.0) & (a <= 90.0) & (np.isfinite(b) | (a == 90.0))
    normal = core_normal(d, np.where(used, a, np.nan), b, line)
    turned = used & np.isfinite(normal).all(axis=1)
    state = np.where(turned, STATE_OK, np.where(used, STATE_LOCAL, STATE_UNUSED)).astype(np.uint8)
    return Measurements(normal, np.where(used, np.sin(np.radians(np.where(used, a, np.nan))), np.nan), state)


def assign(
    hole: Any,
    frm: Any,
    to: Any,
    m_hole: Any,
    m_depth: Any,
    meas: Measurements,
    *,
    d_core: float = D_CORE_DEFAULT,
    threads: int = 1,
    tick: Tick | None = None,
) -> CoreOrientation:
    """The orientation of the intervals [frm; to] of holes by the core measurements `meas` at the depths
    `m_depth` of the holes `m_hole`; `hole`, `m_hole` — one integer key of a hole for both (its id); `d_core` —
    d_к, m (0–1000, else CompositeError `bad_param`). See the module docstring."""
    d_core = check_range(d_core, *D_CORE_RANGE, "Дальность замера керна d_к", "d_core", " м")
    h = dr._holes(hole)
    f = dr._plain(frm, np.float64)
    t = dr._plain(to, np.float64)
    dr._same_length(h, f, t)
    mh = dr._holes(m_hole)
    md = dr._plain(m_depth, np.float64)
    dr._same_length(mh, md, meas.sin_alpha, meas.state)
    if meas.normal.shape != (mh.shape[0], 3):
        raise ValueError(f"the normals of the measurements must be [{mh.shape[0]}, 3], got {meas.normal.shape}")
    used = np.flatnonzero((meas.state != STATE_UNUSED) & np.isfinite(md))
    order = used[np.lexsort((md[used], mh[used]))]  # by hole, then by depth; equal depths — as given
    s_hole = mh[order]
    keys, first = np.unique(s_hole, return_index=True)
    starts = np.append(first, len(s_hole)).astype(np.int64)
    s_depth = dr._plain(md[order], np.float64)
    s_turned = dr._plain(meas.state[order] == STATE_OK, np.bool_)
    s_normal = dr._plain(meas.normal[order], np.float64)
    s_sin = dr._plain(meas.sin_alpha[order], np.float64)
    keys = dr._plain(keys, np.int64)
    n = h.shape[0]
    kind = np.empty(n, dtype=np.uint8)
    normal = np.empty((n, 3), dtype=np.float64)
    sin_alpha = np.empty(n, dtype=np.float64)
    flag = np.empty(n, dtype=np.uint8)
    count = np.empty(n, dtype=np.int32)
    n_turned = np.empty(n, dtype=np.int32)
    strips.run(
        lambda lo, hi: _assign(keys, starts, s_depth, s_turned, s_normal, s_sin, h, f, t, d_core, lo, hi, kind,
                               normal, sin_alpha, flag, count, n_turned),
        n, threads=threads, tick=tick, size=dr.STRIP_ROWS,
    )  # fmt: skip
    several = np.flatnonzero(n_turned > 1)
    if len(several):  # the principal vector of Σ n·nᵀ: in one thread, whatever `threads` is
        scatter = np.empty((len(several), 3, 3), dtype=np.float64)
        _scatter(keys, starts, s_depth, s_turned, s_normal, h, f, t, several, scatter)
        normal[several] = upper_normal(np.linalg.eigh(scatter)[1][:, :, -1])
    return CoreOrientation(kind, normal, sin_alpha, flag, count)


def warm() -> None:
    """Compiles the kernels with the dtypes of the job and of the preview (into NUMBA_CACHE_DIR)."""
    from bf_core.drillholes.desurvey import Collars, desurvey

    traj = desurvey(Collars([0], [0.0], [0.0], [0.0], [50.0], [120.0], [-60.0]))
    m_hole = np.array([4, 4, 4], dtype=np.int64)
    m_depth = np.array([1.0, 2.5, 20.0])
    m = measurements(traj, np.zeros(3, dtype=np.int64), m_depth, np.array([40.0, 55.0, 70.0]),
                     np.array([10.0, 200.0, 45.0]))  # fmt: skip
    hole = np.array([4, 4, 4], dtype=np.int64)
    frm = np.array([0.0, 3.0, 12.0])
    assign(hole, frm, frm + 3.0, m_hole, m_depth, m)
