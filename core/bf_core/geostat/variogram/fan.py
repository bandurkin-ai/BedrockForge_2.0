"""The fan of directions in a plane and the rose of ranges (ТЗ 8.6.1, 8.6.4; ADR 0022 §5, решение 8).

    frame = plane_frame(plane=(0, 0, 0))     R of 8.6.3 (`rotation`): rows a1, a2 — the plane, a3 — its normal
    X, Y, Z = to_plane(x, y, z, frame)        the coordinates of the pair kernel: X = r·a2, Y = r·a1, Z = r·a3
    f = fan(x, y, z, v, lag, nlag, lag_tol=None, *, plane=(0, 0, 0), n=12, tol=None, dip_tol=22.5,
            band_v=inf, threads=1, tick=None, cells=None)
        a row with a missing or infinite number takes no part and is counted (`f.e.n_missing`)
    f.thetas        float64[N]: θ_k = k·180° / N — the angle in the plane from a1 towards a2
    f.directions    the rays in the world: `pairs.Direction(az, plunge, tol, dip_tol, inf, band_v)`
    f.e             `experimental.Experimental` of the rays (curves [ray, lag]: h, n, gamma, rho, pairwise, …)
    f.to_json(min_pairs=30) -> {"directions": [{theta, az, plunge, az_tol, dip_tol, band_h, band_v, lags, h, n,
                                                gamma, rho, pairwise, n_pw, few}]}     (docs/progress/e16-api.md)
    r = rose(f, sigma2, q=1.0, min_pairs=30);  r.ranges float64[N] (NaN — not reached);  r.to_json()
    plane = turn(plane, theta)    the angles whose a1 lies at θ in the same plane (a click on a ray or the map)
    plan = prepare(x, y, z, lag, nlag, lag_tol=None, …) / run(plan, v, …)   the same in two steps: the estimate
                                  of the pair checks first (`plan.checks`), the values next (`varmap.calculate`)

The plane (ADR 0022 §5) is given by the three angles of a model: its axes are a1 and a2, its normal a3; zero
angles — the horizontal plane, a1 north (0; 1; 0), a2 west (−1; 0; 0). The points are taken into the system of
the plane and the definitions of `pairs` hold there: the «azimuth» of the kernel is the angle θ in the plane
from a1 towards a2 (Y of the kernel along a1, X along a2), the «plunge» — the way out of the plane (Z along the
normal a3). A ray k of the fan has θ_k = k·180° / N, the tolerance in the plane `tol` (90° / N by default:
neighbours touch, never overlap), no band in the plane (∞) and, out of the plane, the dip tolerance and the
vertical band of the window (`dip_tol`, `band_v`). The directions in the world are w = cos θ·a1 + sin θ·a2 as
(az, plunge) — θ = 0 is (ang1, −ang2) itself; the tolerances there are those in the plane. The coordinates
in the plane are r·a in a fixed order of the additions ((x·a_x + y·a_y) + z·a_z): exact for an axis of the
world (the horizontal plane gives X = −x, Y = y, Z = z bit for bit).

The rose (ADR 0022 §5): the range of a ray is the first distance where γ̂ ≥ q·σ² — over the points (h̄_k, γ_k)
of the semivariogram with N ≥ min_pairs (others are skipped), linear between the last point below the level
and the first one at or above it; when the first such point is already at the level its h̄ is the range (no
point below it to start from); never reached — NaN (null and «beyond»: «> h_max»). σ² — the variance of the
variable of the calculation (1 for normal scores).

Kernel (`_rays_strip`): the plan of `pairs.prepare` in the system of the plane (m = FAN_CELLS, `pairs.CELLS`
by default) walked as `pairs._strip` walks it, but for coplanar rays: what does not depend on the ray is checked
once a pair, the rays are found by the angle of the pair, the cones by squares — the decisions and the order of
the additions are those of `pairs._strip`, so the sums are the same bit for bit (and with 1, 2, 4, 8 threads).
An accepted pair costs most (8 compensated sums): ~24 ns a check a thread on the composites of 16.4 against
~27 ns of `pairs.run` — the time of the fan goes with its pairs, not with the checks.

Errors — `StatsError {code, message, hint}` with `param`: `bad_param`, `too_few_values` (of `experimental`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.composites import strips
from bf_core.geostat import rotation
from bf_core.jit import njit
from bf_core.sections.geometry import sin_cos_deg

from . import experimental as ex
from . import pairs
from .pairs import _EPS, Direction, Tick, bad, into_lags

FAN_N = 12  # rays by default (8.6.4)
FAN_CELLS: int | None = None  # m of the grid of the fan (None — `pairs.CELLS`; by the measurement of 16.4)
FAN_MIN, FAN_MAX = 4, 72
Q_MIN, Q_MAX = 0.5, 1.2
_DEG = 180.0 / math.pi
Plane = tuple[float, float, float]
HORIZONTAL: Plane = (0.0, 0.0, 0.0)


def plane_frame(plane: Sequence[float] = HORIZONTAL) -> np.ndarray:
    """R of 8.6.3 for the angles (ang1, ang2, ang3) of the plane: rows a1, a2, a3 (`rotation.rotation_matrix`)."""
    return rotation.rotation_matrix(*plane)


def project(x: np.ndarray, y: np.ndarray, z: np.ndarray, axis: np.ndarray) -> np.ndarray:
    """r·axis for every row, the additions in a fixed order: ((x·a_x + y·a_y) + z·a_z)."""
    return np.ascontiguousarray((x * float(axis[0]) + y * float(axis[1])) + z * float(axis[2]))


def to_plane(x: Any, y: Any, z: Any, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The coordinates of the pair kernel in the plane of `frame` (rows a1, a2, a3): X = r·a2, Y = r·a1, Z = r·a3
    (the kernel's azimuth from Y towards X is θ from a1 towards a2)."""
    xs, ys, zs = (np.asarray(c, dtype=np.float64) for c in (x, y, z))
    return project(xs, ys, zs, frame[1]), project(xs, ys, zs, frame[0]), project(xs, ys, zs, frame[2])


def thetas(n: int = FAN_N) -> np.ndarray:
    """θ_k = k·180° / N, k = 0 … N − 1."""
    return np.array([k * 180.0 / n for k in range(n)], dtype=np.float64)


def world_direction(frame: np.ndarray, theta: float) -> np.ndarray:
    """w = cos θ·a1 + sin θ·a2 (exact on the multiples of 90°)."""
    s, c = sin_cos_deg(theta)
    return np.asarray(c * frame[0] + s * frame[1], dtype=np.float64) + 0.0


def az_plunge(u: np.ndarray) -> tuple[float, float]:
    """(azimuth, plunge) of a unit vector as `experimental.axes` reads the axes: a vertical one has the azimuth 0."""
    flat = math.hypot(float(u[0]), float(u[1]))
    if flat < 1e-15:
        return 0.0, (90.0 if u[2] < 0.0 else -90.0)
    az = math.degrees(math.atan2(float(u[0]), float(u[1]))) % 360.0
    az = 0.0 if az >= 360.0 else az + 0.0
    return az, math.degrees(math.atan2(-float(u[2]), flat)) + 0.0


def check_fan(n: int, tol: float | None) -> tuple[int, float]:
    """N 4–72, the tolerance in the plane (0°; 90°] (90° / N when None)."""
    if isinstance(n, bool) or int(n) != n or not (FAN_MIN <= int(n) <= FAN_MAX):
        raise bad("fan.n", "Число направлений веера должно быть целым от 4 до 72.", "Обычно берут 12 направлений.")
    n = int(n)
    t = 90.0 / n if tol is None else float(tol)
    if not (0.0 < t <= 90.0):
        raise bad("fan.tol", "Допуск направления в плоскости должен быть больше 0° и не больше 90°.",
                  f"По умолчанию — 90° / N = {90.0 / n:g}°.")  # fmt: skip
    return n, t


@dataclass(frozen=True)
class Fan:
    plane: Plane
    thetas: np.ndarray  # float64[N]
    directions: tuple[Direction, ...]  # in the world
    e: ex.Experimental  # the run in the plane: its `directions` are those of the kernel (az = θ)

    def to_json(self, min_pairs: int = 30) -> dict[str, Any]:
        """`fan` of the answer of `variography.map`."""
        rays = []
        for k, (t, d) in enumerate(zip(self.thetas.tolist(), self.directions, strict=True)):
            head = {"theta": float(t), "az": float(d.az), "plunge": float(d.plunge), "az_tol": float(d.az_tol),
                    "dip_tol": float(d.dip_tol), "band_h": ex.length_json(d.band_h),
                    "band_v": ex.length_json(d.band_v)}  # fmt: skip
            rays.append(head | self.e.sums.curves_json(k, self.e.lag, min_pairs))
        return {"directions": rays}


@dataclass(frozen=True)
class FanPlan:
    plane: Plane
    thetas: np.ndarray
    directions: tuple[Direction, ...]  # in the world
    local: tuple[Direction, ...]  # of the kernel
    lags: tuple[float, int, float]
    plan: pairs.Plan
    n_missing: int

    @property
    def checks(self) -> int:
        return self.plan.checks


Rays = tuple[Plane, np.ndarray, tuple[Direction, ...], tuple[Direction, ...]]


def rays(plane: Sequence[float] = HORIZONTAL, n: int = FAN_N, tol: float | None = None, dip_tol: float = 22.5,
         band_v: float = math.inf) -> Rays:  # fmt: skip
    """(plane, θ, the rays in the world, the rays of the kernel in the plane)."""
    a, b, c = rotation.normalize_angles(*plane)
    n, t = check_fan(n, tol)
    frame = rotation.rotation_matrix(a, b, c)
    th = thetas(n)
    local = tuple(Direction(float(x), 0.0, t, dip_tol, math.inf, band_v) for x in th.tolist())
    world = []
    for x in th.tolist():
        az, pl = (a, -b + 0.0) if x == 0.0 else az_plunge(world_direction(frame, x))
        world.append(Direction(az, pl, t, dip_tol, math.inf, band_v))
    return (a, b, c), th, tuple(world), local


def prepare(
    x: Any, y: Any, z: Any, lag: float, nlag: int, lag_tol: float | None = None, *,
    plane: Sequence[float] = HORIZONTAL, n: int = FAN_N, tol: float | None = None, dip_tol: float = 22.5,
    band_v: float = math.inf, cells: int | None = None, n_missing: int = 0,
) -> FanPlan:  # fmt: skip
    """The plan of the fan over the rows (`pairs.prepare` in the system of the plane): rows without a position
    are dropped and counted with `n_missing` (the values of `run` are those of the rows kept, in their order)."""
    lags = pairs.check_lags(lag, nlag, lag_tol)
    p, th, world, local = rays(plane, n, tol, dip_tol, band_v)
    table = pairs.check_directions(local)
    (xs, ys, zs), dropped = ex.finite_rows(x, y, z)
    if len(xs) < 2:
        raise ex.too_few()
    X, Y, Z = to_plane(xs, ys, zs, rotation.rotation_matrix(*p))
    m = FAN_CELLS if cells is None else cells
    return FanPlan(p, th, world, local, lags, pairs.prepare(X, Y, Z, table, *lags, m), n_missing + dropped)


# --- the kernel of coplanar rays ------------------------------------------------------------------------------


@njit(cache=True, nogil=True, inline="always")
def _atan_unit(r):  # type: ignore[no-untyped-def]
    """atan(r) for 0 ≤ r ≤ 1, |error| ≤ 1e−5 rad (Abramowitz & Stegun 4.4.49)."""
    r2 = r * r
    return r * (0.9998660 + r2 * (-0.3302995 + r2 * (0.1801410 + r2 * (-0.0851330 + r2 * 0.0208351))))


@njit(cache=True, nogil=True)
def _rays_strip(xs, ys, zs, vs, ws, rows, ckey, cstart, cijk, dims, off, tab, lag, tol, nlag, c0, c1, acc, cnt):  # type: ignore[no-untyped-def]  # noqa: E501
    """`pairs._strip` for the rays of a fan in the system of its plane — ray d at the azimuth d·180° / N, plunge
    0, no horizontal band, one tolerance, one dip tolerance and one vertical band for all — over the same plan
    and the same pairs in the same order: the sums are those of `pairs._strip` bit for bit. What does not depend
    on the ray is checked once a pair, without branches: the reach, the vertical band (abs(cp·Δz + sp·ds) =
    abs(Δz) at the plunge 0) and the cone of the dip (abs(ds·cp − Δz·sp) / h = d_xy / h) by d_xy² ≥ cos²·h²
    less 1e−14. The rays that may take a pair are those within the tolerance (+ 0.01°) of its angle in the plane,
    read by an approximate atan (1e−5 rad); a cone is checked by g² against cos²·d_xy², and exactly (the
    division of `pairs._strip`) only within 1e−14 of its edge — farther from it the two cannot differ."""
    nc = ckey.shape[0]
    no = off.shape[0]
    nray = tab.shape[0]
    nx, ny, nz = dims[0], dims[1], dims[2]
    comp = np.zeros(acc.shape[0])
    cen = np.empty(nlag)
    for k in range(nlag):
        cen[k] = lag * (k + 1)
    hmax = cen[nlag - 1] + tol
    hi2 = (hmax * (1.0 + 1e-12)) ** 2
    lo2 = -1.0
    if lag - tol > 0.0:
        lo2 = ((lag - tol) * (1.0 - 1e-12)) ** 2
    inv = 1.0 / lag
    span = 1 if tol <= 0.5 * lag else 2
    sa = tab[:, 0].copy()
    ca = tab[:, 1].copy()
    cat = tab[:, 4].copy()
    cdt = tab[0, 6]
    bv = tab[0, 7]
    ux = tab[:, 8].copy()
    uy = tab[:, 9].copy()
    uz = tab[:, 10].copy()
    azc = tab[0, 11] != 0.0
    dipc = tab[0, 12] != 0.0
    cat2 = np.zeros(nray)  # the test of `pairs._strip` before the exact one
    cat2hi = np.zeros(nray)  # g² at or above it: inside the cone for sure
    for d in range(nray):
        if azc and cat[d] > 0.0:
            cat2[d] = cat[d] * cat[d] * (1.0 - 1e-14)
            cat2hi[d] = cat[d] * cat[d] * (1.0 + 1e-14)
    cdt2 = -1.0  # d_xy² below cdt2·h² is outside the cone of the dip for sure; at or above cdt2hi·h² — inside
    cdt2hi = -1.0
    if dipc and cdt > 0.0:
        cdt2 = cdt * cdt * (1.0 - 1e-14)
        cdt2hi = cdt * cdt * (1.0 + 1e-14)
    step = 180.0 / nray
    reach = 90.0  # degrees about the angle of a pair: every ray
    if azc:
        reach = math.degrees(math.acos(min(1.0, max(-1.0, cat[0])))) + 0.01
    delta = np.empty(no, dtype=np.int64)
    ptr = np.empty(no, dtype=np.int64)
    for o in range(no):
        delta[o] = off[o, 0] + nx * (off[o, 1] + ny * off[o, 2])
        ptr[o] = np.searchsorted(ckey, ckey[c0] + delta[o]) if c0 < nc else nc
    most = 1
    for c in range(nc):
        most = max(most, cstart[c + 1] - cstart[c])
    near = np.empty(most, dtype=np.int64)
    qx = np.empty(most)
    qy = np.empty(most)
    qz = np.empty(most)
    q2 = np.empty(most)
    for c in range(c0, c1):
        a0 = cstart[c]
        a1 = cstart[c + 1]
        for o in range(no):
            jx = cijk[c, 0] + off[o, 0]
            jy = cijk[c, 1] + off[o, 1]
            jz = cijk[c, 2] + off[o, 2]
            if jx < 0 or jx >= nx or jy < 0 or jy >= ny or jz < 0 or jz >= nz:
                continue
            same = delta[o] == 0
            if same:
                b0 = a0
                b1 = a1
            else:
                target = ckey[c] + delta[o]
                p = ptr[o]
                while p < nc and ckey[p] < target:
                    p += 1
                ptr[o] = p
                if p >= nc or ckey[p] != target:
                    continue
                b0 = cstart[p]
                b1 = cstart[p + 1]
            for a in range(a0, a1):
                xa = xs[a]
                ya = ys[a]
                za = zs[a]
                bs = a + 1 if same else b0
                for t in range(b1 - bs):  # the distances to the points of the cell: no branches
                    b = bs + t
                    dx = xs[b] - xa
                    dy = ys[b] - ya
                    dz = zs[b] - za
                    qx[t] = dx
                    qy[t] = dy
                    qz[t] = dz
                    q2[t] = (dx * dx + dy * dy) + dz * dz
                nb = 0
                for t in range(b1 - bs):  # the reach, the vertical band, the cone of the dip (h < 1e−20 passes)
                    h2 = q2[t]
                    dx = qx[t]
                    dy = qy[t]
                    dz = qz[t]
                    qx[nb] = dx
                    qy[nb] = dy
                    qz[nb] = dz
                    q2[nb] = h2
                    near[nb] = bs + t
                    cone = ((dx * dx + dy * dy) >= cdt2 * h2) | (h2 < 1e-39)
                    nb += (h2 <= hi2) & (h2 >= lo2) & (abs(dz) <= bv) & cone
                for t in range(nb):
                    b = near[t]
                    dx = qx[t]
                    dy = qy[t]
                    dz = qz[t]
                    d2 = dx * dx + dy * dy
                    h = math.sqrt(q2[t])
                    if dipc and d2 < cdt2hi * q2[t]:  # near the edge of the cone of the dip: as `pairs._strip`
                        if h >= _EPS and math.sqrt(d2) / h < cdt:
                            continue
                    k0 = 0
                    k1 = nray - 1
                    if azc and d2 >= 1e-39:  # the rays about the angle of the pair (from a1 towards a2)
                        fx = dx
                        fy = dy
                        if fy < 0.0 or (fy == 0.0 and fx < 0.0):
                            fx = -fx
                            fy = -fy
                        ax = abs(fx)
                        if ax <= fy:
                            phi = _DEG * _atan_unit(ax / fy)
                        else:
                            phi = 90.0 - _DEG * _atan_unit(fy / ax)
                        if fx < 0.0:
                            phi = 180.0 - phi
                        k0 = int(math.ceil((phi - reach) / step))
                        k1 = int(math.floor((phi + reach) / step))
                        if k1 - k0 >= nray:
                            k0 = 0
                            k1 = nray - 1
                    dv = vs[a] - vs[b]
                    sm = vs[a] + vs[b]
                    for kk in range(k0, k1 + 1):
                        d = kk - nray if kk >= nray else (kk + nray if kk < 0 else kk)
                        g = dx * sa[d] + dy * ca[d]
                        gg = g * g
                        if not (gg >= cat2[d] * (d2 - 1e-40)):  # the test of `pairs._strip` before the exact one
                            continue
                        if azc and gg < cat2hi[d] * d2:  # near the edge of the cone: as `pairs._strip`
                            dxy = math.sqrt(d2)
                            if dxy >= _EPS and abs(g) / dxy < cat[d]:
                                continue
                        s = dx * ux[d] + dy * uy[d] + dz * uz[d]
                        ta = (s > 0.0) | ((s == 0.0) & (rows[a] < rows[b]))  # the tail is i when Δ·u ≥ 0
                        it = a * ta + b * (1 - ta)
                        ih = a + b - it
                        into_lags(acc, comp, cnt, d * nlag, h, dv, sm, ws[it], ws[ih], cen, tol, inv, span)
    for p in range(acc.shape[0]):
        acc[p] += comp[p]


def run(fp: FanPlan, v: Any, *, threads: int = 1, tick: Tick | None = None) -> Fan:
    """The fan of the plan for the values `v` (float64, finite, in the order of the rows of the plan): the sums
    of `pairs.run` over the plan, bit for bit (`_rays_strip`)."""
    p = fp.plan
    nray, (_lag, nlag, _tol) = p.table.shape[0], fp.lags
    vs = np.ascontiguousarray(np.asarray(v, dtype=np.float64)[p.order])
    shift = float(np.mean(vs))
    ws = vs - shift
    acc = np.zeros((p.strips, nray, nlag, pairs.NSUM))
    cnt = np.zeros((p.strips, nray, nlag, 2), dtype=np.int64)

    def one(s: int, _hi: int) -> None:
        _rays_strip(p.xs, p.ys, p.zs, vs, ws, p.order, p.ckey, p.cstart, p.cijk, p.dims, p.off, p.table, p.lag,
                    p.tol, p.nlag, p.bounds[s], p.bounds[s + 1], acc[s].reshape(-1), cnt[s].reshape(-1))  # fmt: skip

    strips.run(one, p.strips, threads=threads, tick=tick, size=1)
    total = cnt.sum(axis=0)
    both = np.array([d.omni for d in fp.local], dtype=bool)
    sums = ex.PairSums(total[:, :, 0].copy(), total[:, :, 1].copy(), pairs.pairwise(acc), shift, both)
    lag, nlag, tol = fp.lags
    e = ex.Experimental(fp.local, lag, nlag, tol, sums, p.n, fp.n_missing, p.checks, p.strips, p.cells,
                        estimate=p.checks)  # fmt: skip
    return Fan(fp.plane, fp.thetas, fp.directions, e)


def fan(
    x: Any, y: Any, z: Any, v: Any, lag: float, nlag: int, lag_tol: float | None = None, *,
    plane: Sequence[float] = HORIZONTAL, n: int = FAN_N, tol: float | None = None, dip_tol: float = 22.5,
    band_v: float = math.inf, threads: int = 1, tick: Tick | None = None, cells: int | None = None,
) -> Fan:  # fmt: skip
    """The fan of N rays in the plane (see the module); z = None — a plan (z = 0)."""
    (xs, ys, zs, vs), n_missing = ex.finite_rows(x, y, z, v)
    fp = prepare(xs, ys, zs, lag, nlag, lag_tol, plane=plane, n=n, tol=tol, dip_tol=dip_tol, band_v=band_v,
                 cells=cells, n_missing=n_missing)  # fmt: skip
    return run(fp, vs, threads=threads, tick=tick)


# --- the rose -------------------------------------------------------------------------------------------------


def crossing(h: np.ndarray, gamma: np.ndarray, n: np.ndarray, level: float, min_pairs: int) -> float:
    """The first distance where γ̂ ≥ level over the points with N ≥ min_pairs (see the module); NaN — none."""
    prev: tuple[float, float] | None = None
    for hk, gk, nk in zip(h.tolist(), gamma.tolist(), n.tolist(), strict=True):
        if nk < min_pairs or not (math.isfinite(hk) and math.isfinite(gk)):
            continue
        if gk >= level:
            if prev is None:
                return float(hk)
            h0, g0 = prev
            return float(h0 + (hk - h0) * (level - g0) / (gk - g0))
        prev = (hk, gk)
    return math.nan


@dataclass(frozen=True)
class Rose:
    q: float
    level: float  # q·σ²
    thetas: np.ndarray
    ranges: np.ndarray  # float64[N], NaN — not reached («> h_max»)

    def to_json(self) -> dict[str, Any]:
        out = [{"theta": float(t), "range": None if math.isnan(r) else float(r), "beyond": bool(math.isnan(r))}
               for t, r in zip(self.thetas.tolist(), self.ranges.tolist(), strict=True)]  # fmt: skip
        return {"q": float(self.q), "level": float(self.level), "rays": out}


def rose(f: Fan, sigma2: float, q: float = 1.0, min_pairs: int = 30) -> Rose:
    """The ranges of the rays of the fan at the level q·σ² of the semivariogram."""
    q = float(q)
    if not (Q_MIN <= q <= Q_MAX):
        raise bad("fan.q", "Уровень дальности розы q должен быть от 0,5 до 1,2.", "Обычно q = 1 (уровень σ²).")
    s2 = float(sigma2)
    if not (math.isfinite(s2) and s2 > 0.0):
        raise bad("sigma2", "Дисперсия переменной должна быть больше нуля.", "Проверьте значения поля.")
    level = q * s2
    e = f.e
    r = [crossing(e.h[k], e.gamma[k], e.n[k], level, int(min_pairs)) for k in range(len(f.thetas))]
    return Rose(q, level, f.thetas, np.array(r, dtype=np.float64))


# --- a click on a ray or the map -----------------------------------------------------------------------------


def turn(plane: Sequence[float], theta: float) -> Plane:
    """The angles of the same plane whose major axis lies at θ (from a1 towards a2): a1′ = cos θ·a1 + sin θ·a2,
    a2′ = −sin θ·a1 + cos θ·a2, a3′ = a3 (`rotation.angles_of`; a vertical a1′ keeps ang1); θ = 0 — the angles
    as they are."""
    a, b, c = rotation.normalize_angles(*plane)
    t = rotation.number(theta, "theta", "Угол направления в плоскости")
    if math.fmod(t, 360.0) == 0.0:  # the axis itself: the angles unchanged
        return a, b, c
    r = rotation.rotation_matrix(a, b, c)
    s, k = sin_cos_deg(t)
    axes = np.array([k * r[0] + s * r[1], -s * r[0] + k * r[1], r[2]], dtype=np.float64) + 0.0
    return rotation.angles_of(axes, a)
