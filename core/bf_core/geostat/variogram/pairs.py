"""The pair kernel of the experimental variogram: grid of cells, mask of cell offsets, strips (ADR 0022 §3–4).

    Direction(az=0, plunge=0, az_tol=22.5, dip_tol=22.5, band_h=inf, band_v=inf)   degrees, metres; frozen
        .unit  u = (cos p·sin Az, cos p·cos Az, −sin p)      .omni  both tolerances ≥ 90°      .vertical  p = ±90°
    check_directions(directions) -> float64[ndir, 13]   the table of the kernel; StatsError `bad_param {param}`
    check_lags(lag, nlag, lag_tol=None) -> (lag, nlag, tol)
    plan = prepare(x, y, z, table, lag, nlag, tol, cells=CELLS) -> Plan     .checks .strips .cells .n
    run(plan, v, threads=1, tick=None) -> Raw(n, n_rel, sums, shift)
        n, n_rel  int64[ndir, nlag]     pairs of a lag; those of them with z_i + z_j > 0
        sums      float64[ndir, nlag, 8]   columns S_H … S_REL (below)
        shift     the mean of v: the tail and head sums are of v − shift

The pair (GSLIB gamv, ADR 0022 §3). Unordered, i < j by the row; Δ = r_j − r_i, h = |Δ|, d_xy — its horizontal
length. In this order: abs(Δx·sin Az + Δy·cos Az) / d_xy ≥ cos(az_tol) − 1e−12 (d_xy < 1e−20 passes);
abs(sin Az·Δy − cos Az·Δx) ≤ band_h; d_xy takes the sign of the projection on the azimuth;
abs(d_xy·cos p − Δz·sin p) / h ≥ cos(dip_tol) − 1e−12 (h < 1e−20 passes); abs(cos p·Δz + sin p·d_xy) ≤ band_v.
A tolerance of 90° is not checked. A vertical direction (p = ±90°) has no azimuth: neither the azimuth nor the
horizontal band is checked, the vertical band is the radius of a cylinder about it. sin and cos of the multiples
of 90° are exact. Lag k = 1 … nlag: abs(h − k·L) ≤ tol, edges included (a pair on an edge is in both lags, as
BF); no lag at 0, no pair of a point with itself. The tail of a pair is i when Δ·u ≥ 0, otherwise j.

What is summed for a lag of a direction, in one pass: S_H Σh, S_D2 Σ(z_i − z_j)², S_T Σz_tail, S_HD Σz_head,
S_TT Σz_tail², S_HH Σz_head², S_TH Σz_tail·z_head (z − shift in these five), S_REL Σ((z_i − z_j) /
((z_i + z_j) / 2))² over the pairs with z_i + z_j > 0.

Grid. Cells of side c = h_max / m, h_max = nlag·L + tol (the farthest pair; (nlag + 0.5)·L at tol = L / 2);
the points are sorted by (cell, row), a pair is met once — from the cell with the smaller key. For m > 1 a pair
of cells is an offset of up to ±m cells: every offset keeps the list of the directions that can take a pair of
it (the box of Δ of the offset against the distances, the bands and the cones — a superset, so nothing is
lost), an offset without directions is not walked. The result does not depend on m beyond the order of the
additions (1e−12); `Plan.checks` — the pair checks of the grid — is the estimate of ADR 0022 §4. Strips: at
most 256 runs of cells with about equal numbers of pair checks; a strip owns its sums [direction × lag],
compensated (Neumaier); the strips are added pairwise in their order. The split depends on the data and the
parameters only: the result is the same bit for bit with 1, 2, 4 and 8 threads (`nogil` kernel on the threads
of `composites.strips`). `tick(fraction)` is called after every strip in the calling thread and may raise to
cancel.

Coordinates are taken as they are (float64): Δ of two close points is exact, nothing is subtracted beforehand.
Rows must be finite — `experimental` drops and counts the others.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

import numpy as np

from bf_core.composites import strips
from bf_core.jit import njit
from bf_core.sections.geometry import sin_cos_deg
from bf_core.stats.weighted import StatsError

NSUM = 8
S_H, S_D2, S_T, S_HD, S_TT, S_HH, S_TH, S_REL = range(NSUM)
CELLS = 6  # m: cells per h_max (ADR 0022 §4): the fastest of 2, 3, 4, 6, 8 by the measurement of 16.2
MAX_CELLS = 16
MAX_STRIPS = 256
STRIP_CHECKS = 2_000_000  # pair checks in a strip, at least: small sets take few strips
_EPS = 1e-20  # GSLIB EPSLON: a shorter d_xy or h is not checked for the angle
_GROW = 1e-6  # the cell is this much wider than h_max / m: rounding never puts a pair m + 1 cells apart
_AXIS = 2**31  # cells along an axis, at most
_NCOL = 13  # columns of the direction table
_SA, _CA, _SP, _CP, _CAT, _BH, _CDT, _BV, _UX, _UY, _UZ, _AZC, _DIPC = range(_NCOL)

Tick = Callable[[float], None]


@dataclass(frozen=True)
class Direction:
    """A direction of the variogram: azimuth from north clockwise, plunge positive down, tolerances and bands."""

    az: float = 0.0
    plunge: float = 0.0
    az_tol: float = 22.5
    dip_tol: float = 22.5
    band_h: float = math.inf
    band_v: float = math.inf

    @property
    def unit(self) -> tuple[float, float, float]:
        sa, ca = sin_cos_deg(self.az)
        sp, cp = sin_cos_plunge(self.plunge)
        return (cp * sa, cp * ca, -sp + 0.0)

    @property
    def vertical(self) -> bool:
        return sin_cos_plunge(self.plunge)[1] == 0.0

    @property
    def omni(self) -> bool:
        """Any pair fits the angles: it counts both ways in the tail and head sums."""
        return self.az_tol >= 90.0 and self.dip_tol >= 90.0


class Plan(NamedTuple):
    """The grid, the offsets and the strips of a run — everything but the values."""

    order: np.ndarray  # int64[n]: rows by (cell, row)
    xs: np.ndarray
    ys: np.ndarray
    zs: np.ndarray
    ckey: np.ndarray  # int64[nc]: keys of the cells that hold points, ascending
    cstart: np.ndarray  # int64[nc + 1]
    cijk: np.ndarray  # int64[nc, 3]
    dims: np.ndarray  # int64[3]
    off: np.ndarray  # int64[no, 3]: cell offsets that can hold a pair of some direction
    optr: np.ndarray  # int64[no + 1]
    odir: np.ndarray  # int64[…]: the directions of every offset
    table: np.ndarray  # float64[ndir, 13]
    lag: float
    nlag: int
    tol: float
    bounds: np.ndarray  # int64[strips + 1]: cells of the strips
    checks: int  # pair checks of the run — the estimate of ADR 0022 §4
    cells: int  # m

    @property
    def strips(self) -> int:
        return len(self.bounds) - 1

    @property
    def n(self) -> int:
        return len(self.order)


class Raw(NamedTuple):
    n: np.ndarray
    n_rel: np.ndarray
    sums: np.ndarray
    shift: float


def sin_cos_plunge(plunge: float) -> tuple[float, float]:
    """sin and cos of the plunge p as `rotation` takes them — of ang2 = −p: u is the major axis of (Az, −p, 0) bit
    for bit; exact on the multiples of 90°."""
    s2, c2 = sin_cos_deg(-plunge + 0.0)
    return -s2 + 0.0, c2


def bad(param: str, message: str, hint: str) -> StatsError:
    return StatsError("bad_param", message, hint, param=param)


def check_lags(lag: float, nlag: int, lag_tol: float | None = None) -> tuple[float, int, float]:
    """Lag L > 0, nlag ≥ 1, tolerance 0 … L (L / 2 when not given)."""
    lag = float(lag)
    if not (math.isfinite(lag) and lag > 0.0):
        raise bad("lag", "Лаг должен быть числом больше нуля.", "Задайте лаг в метрах, например 5 длин композита.")
    if int(nlag) != nlag or int(nlag) < 1:
        raise bad("nlag", "Число лагов должно быть целым и не меньше 1.", "Задайте от 1 до 100 лагов.")
    tol = 0.5 * lag if lag_tol is None else float(lag_tol)
    if not (0.0 <= tol <= lag):
        raise bad("lag_tol", "Допуск лага должен быть от 0 до длины лага.", "Обычно берут половину лага.")
    return lag, int(nlag), tol


def check_directions(directions: Sequence[Direction]) -> np.ndarray:
    """The table of the kernel, a row per direction; a value out of range — `bad_param` with the parameter."""
    if len(directions) == 0:
        raise bad("directions", "Не задано ни одного направления.", "Укажите хотя бы одно направление расчёта.")
    t = np.zeros((len(directions), _NCOL))
    for k, d in enumerate(directions):
        if not math.isfinite(d.az):
            raise bad("az", "Азимут направления должен быть числом.", "Задайте азимут от 0° до 360°.")
        if not (-90.0 <= d.plunge <= 90.0):
            raise bad("plunge", "Погружение должно быть от −90° до 90°.", "Вниз — плюс, вверх — минус.")
        for name, tol in (("az_tol", d.az_tol), ("dip_tol", d.dip_tol)):
            if not (0.0 < tol <= 90.0):
                raise bad(name, "Допуск угла должен быть больше 0° и не больше 90°.", "90° — без ограничения по углу.")
        for name, band in (("band_h", d.band_h), ("band_v", d.band_v)):
            if not band > 0.0:
                raise bad(name, "Ширина полосы должна быть больше нуля.", "Задайте ширину в метрах или уберите полосу.")
        sa, ca = sin_cos_deg(d.az)
        sp, cp = sin_cos_plunge(d.plunge)
        vertical = cp == 0.0
        t[k] = (
            sa, ca, sp, cp,
            sin_cos_deg(d.az_tol)[1] - 1e-12, math.inf if vertical else d.band_h,
            sin_cos_deg(d.dip_tol)[1] - 1e-12, d.band_v,
            cp * sa, cp * ca, -sp,
            0.0 if (vertical or d.az_tol >= 90.0) else 1.0, 0.0 if d.dip_tol >= 90.0 else 1.0,
        )  # fmt: skip
    return t


# --- offsets of cells and their directions ------------------------------------------------------------------


def _lin_min(a: float, b: float, u0: Any, u1: Any, w0: Any, w1: Any) -> Any:
    """The least a·u + b·w over the rectangles [u0; u1] × [w0; w1]."""
    return a * (u0 if a >= 0.0 else u1) + b * (w0 if b >= 0.0 else w1)


def _abs_min(a: float, b: float, u0: Any, u1: Any, w0: Any, w1: Any) -> Any:
    """The least abs(a·u + b·w) over the rectangles."""
    lo, hi = _lin_min(a, b, u0, u1, w0, w1), -_lin_min(-a, -b, u0, u1, w0, w1)
    return np.where((lo <= 0.0) & (hi >= 0.0), 0.0, np.minimum(np.abs(lo), np.abs(hi)))


def _wedge(a: float, b: float, t: float, u0: Any, u1: Any, w0: Any, w1: Any, slack: float) -> Any:
    """Can abs(perp) ≤ t·abs(along) hold in the rectangles? along = a·u + b·w, perp = −b·u + a·w. Each of the
    two wedges is two half-planes; a rectangle off one of them is off the wedge — the answer is a superset."""
    fwd = (_lin_min(-b - t * a, a - t * b, u0, u1, w0, w1) <= slack) & (
        _lin_min(b - t * a, -a - t * b, u0, u1, w0, w1) <= slack
    )
    back = (_lin_min(-b + t * a, a + t * b, u0, u1, w0, w1) <= slack) & (
        _lin_min(b + t * a, -a + t * b, u0, u1, w0, w1) <= slack
    )
    return fwd | back


def _tan(cos_tol: float) -> float:
    """tan of the tolerance whose cosine (less 1e−12) is given, a little wider."""
    return math.sqrt(max(0.0, 1.0 - cos_tol * cos_tol)) / cos_tol * (1.0 + 1e-9) + 1e-12


def _can_take(row: np.ndarray, box: tuple[Any, ...], slack: float) -> Any:
    """Offsets whose box of Δ may hold a pair of the direction `row`: the checks of the kernel against the box,
    one by one and loosened by `slack` — never fewer offsets than the kernel needs."""
    x0, x1, y0, y1, z0, z1 = box
    sa, ca, sp, cp = (float(row[c]) for c in (_SA, _CA, _SP, _CP))
    wide = 1.0 + 1e-9
    ok = _abs_min(-ca, sa, x0, x1, y0, y1) <= row[_BH] * wide + slack  # the horizontal band
    if row[_AZC] != 0.0 and row[_CAT] > 0.0:  # the cone of the azimuth
        ok &= _wedge(sa, ca, _tan(float(row[_CAT])), x0, x1, y0, y1, slack)
    g_pos = -_lin_min(-sa, -ca, x0, x1, y0, y1) >= -slack  # the projection on the azimuth may be ≥ 0
    g_neg = _lin_min(sa, ca, x0, x1, y0, y1) <= slack
    near = np.hypot(np.maximum(0.0, np.maximum(x0, -x1)), np.maximum(0.0, np.maximum(y0, -y1))) * (2.0 - wide) - slack
    far = np.hypot(np.maximum(np.abs(x0), np.abs(x1)), np.maximum(np.abs(y0), np.abs(y1))) * wide + slack

    def in_plane(d0: Any, d1: Any) -> Any:  # the vertical plane of the pair: (signed d_xy, Δz)
        fit = _abs_min(sp, cp, d0, d1, z0, z1) <= row[_BV] * wide + slack  # the vertical band
        if row[_DIPC] != 0.0 and row[_CDT] > 0.0:  # the cone of the plunge
            fit &= _wedge(cp, -sp, _tan(float(row[_CDT])), d0, d1, z0, z1, slack)
        return fit

    return ok & ((g_pos & in_plane(near, far)) | (g_neg & in_plane(-far, -near)))


def offset_table(
    dims: Sequence[int], cells: int, cell: float, hmin: float, hmax: float, table: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Offsets (di, dj, dk) of the half-space «the other cell has a greater key», the cell itself first, with the
    directions each of them can serve (CSR: `optr`, `odir`); offsets that serve none are left out."""
    rx, ry, rz = (min(cells, int(n) - 1) for n in dims)
    dk, dj, di = np.meshgrid(np.arange(-rz, rz + 1), np.arange(-ry, ry + 1), np.arange(-rx, rx + 1), indexing="ij")
    half = (dk > 0) | ((dk == 0) & (dj > 0)) | ((dk == 0) & (dj == 0) & (di >= 0))
    off = np.stack([di[half], dj[half], dk[half]], axis=1).astype(np.int64)  # ascending by the key offset
    slack = 2.0 * _GROW * cell
    box = tuple(b for a in range(3) for b in ((off[:, a] - 1.0) * cell - slack, (off[:, a] + 1.0) * cell + slack))
    near2 = sum(np.maximum(0.0, np.maximum(box[2 * a], -box[2 * a + 1])) ** 2 for a in range(3))
    far2 = sum(np.maximum(np.abs(box[2 * a]), np.abs(box[2 * a + 1])) ** 2 for a in range(3))
    reach = (near2 <= (hmax * (1.0 + 1e-9)) ** 2) & (far2 >= (max(hmin, 0.0) * (1.0 - 1e-9)) ** 2)
    can = np.stack([reach & _can_take(row, box, slack) for row in table], axis=1)  # [offset, direction]
    keep = can.any(axis=1)
    off, can = off[keep], can[keep]
    optr = np.zeros(len(off) + 1, dtype=np.int64)
    np.cumsum(can.sum(axis=1), out=optr[1:])
    return off, optr, np.nonzero(can)[1].astype(np.int64)


# --- kernels ------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _cell_checks(ckey, cstart, cijk, dims, off):  # type: ignore[no-untyped-def]
    """Pair checks of every cell: its points against the points of the cells at the offsets."""
    nc = ckey.shape[0]
    no = off.shape[0]
    nx, ny, nz = dims[0], dims[1], dims[2]
    cost = np.zeros(nc, dtype=np.int64)
    delta = np.empty(no, dtype=np.int64)
    ptr = np.zeros(no, dtype=np.int64)
    for o in range(no):
        delta[o] = off[o, 0] + nx * (off[o, 1] + ny * off[o, 2])
    for ca in range(nc):
        na = cstart[ca + 1] - cstart[ca]
        total = 0
        for o in range(no):
            jx = cijk[ca, 0] + off[o, 0]
            jy = cijk[ca, 1] + off[o, 1]
            jz = cijk[ca, 2] + off[o, 2]
            if jx < 0 or jx >= nx or jy < 0 or jy >= ny or jz < 0 or jz >= nz:
                continue
            if delta[o] == 0:
                total += na * (na - 1) // 2
                continue
            target = ckey[ca] + delta[o]
            p = ptr[o]
            while p < nc and ckey[p] < target:
                p += 1
            ptr[o] = p
            if p < nc and ckey[p] == target:
                total += na * (cstart[p + 1] - cstart[p])
        cost[ca] = total
    return cost


@njit(cache=True, nogil=True, inline="always")
def _add(acc, comp, p, x):  # type: ignore[no-untyped-def]
    """acc[p] += x with the exact error of the addition kept in comp[p] (Neumaier's sum without the branch)."""
    s = acc[p]
    t = s + x
    bp = t - s
    comp[p] += (s - (t - bp)) + (x - bp)
    acc[p] = t


@njit(cache=True, nogil=True, inline="always")
def into_lags(acc, comp, cnt, row, h, dv, sm, wt, wh, cen, tol, inv, span):  # type: ignore[no-untyped-def]
    """A pair at distance h into the lags of the row `row` (= direction · nlag) whose windows hold it — one or two
    (a pair on an edge is in both): N, Σh, Σ(z_i − z_j)², the tail and head sums, the pairwise relative one."""
    nlag = cen.shape[0]
    k0 = int(h * inv + 0.5)
    for k in range(max(k0 - span, 1) - 1, min(k0 + span, nlag)):
        if abs(h - cen[k]) <= tol:
            p = (row + k) * 8
            cnt[(row + k) * 2] += 1
            _add(acc, comp, p, h)
            _add(acc, comp, p + 1, dv * dv)
            _add(acc, comp, p + 2, wt)
            _add(acc, comp, p + 3, wh)
            _add(acc, comp, p + 4, wt * wt)
            _add(acc, comp, p + 5, wh * wh)
            _add(acc, comp, p + 6, wt * wh)
            if sm > 0.0:
                r = dv / (sm / 2.0)
                cnt[(row + k) * 2 + 1] += 1
                _add(acc, comp, p + 7, r * r)


@njit(cache=True, nogil=True)
def _strip(xs, ys, zs, vs, ws, rows, ckey, cstart, cijk, dims, off, optr, odir, tab, lag, tol, nlag, c0, c1, acc, cnt):  # type: ignore[no-untyped-def]  # noqa: E501
    """The pairs of the cells c0 ≤ c < c1 (with the cells at the offsets) into acc[ndir·nlag·8] and
    cnt[ndir·nlag·2] (flat [direction, lag, sum]). `vs` — values, `ws` — values less their mean, `rows` — rows
    of the sorted points. A point meets a cell in steps: the distances to all its points (no branches), those
    within reach to the front; then, a direction at a time, its band and cone without roots (no branches) and
    the exact checks of those left. The order of the additions into a sum of [direction, lag] is that of the
    pairs (cell, offset, point, point) — whatever the steps."""
    nc = ckey.shape[0]
    no = off.shape[0]
    ndir = tab.shape[0]
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
    span = 1 if tol <= 0.5 * lag else 2  # lags around the nearest one that may hold the pair
    sa = tab[:, 0].copy()
    ca = tab[:, 1].copy()
    sp = tab[:, 2].copy()
    cp = tab[:, 3].copy()
    cat = tab[:, 4].copy()
    bh = tab[:, 5].copy()
    cdt = tab[:, 6].copy()
    bv = tab[:, 7].copy()
    ux = tab[:, 8].copy()
    uy = tab[:, 9].copy()
    uz = tab[:, 10].copy()
    azc = tab[:, 11].copy()
    dipc = tab[:, 12].copy()
    cat2 = np.zeros(ndir)  # g² below this share of d_xy² is outside the cone for sure (0 — no cone): no root
    for d in range(ndir):
        if azc[d] != 0.0 and cat[d] > 0.0:
            cat2[d] = cat[d] * cat[d] * (1.0 - 1e-14)
    delta = np.empty(no, dtype=np.int64)
    ptr = np.empty(no, dtype=np.int64)
    for o in range(no):
        delta[o] = off[o, 0] + nx * (off[o, 1] + ny * off[o, 2])
        ptr[o] = np.searchsorted(ckey, ckey[c0] + delta[o]) if c0 < nc else nc
    most = 1
    for c in range(nc):
        most = max(most, cstart[c + 1] - cstart[c])
    near = np.empty(most, dtype=np.int64)
    keep = np.empty(most, dtype=np.int64)
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
            d0 = optr[o]
            d1 = optr[o + 1]
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
                for t in range(b1 - bs):  # those within reach, to the front
                    h2 = q2[t]
                    qx[nb] = qx[t]
                    qy[nb] = qy[t]
                    qz[nb] = qz[t]
                    q2[nb] = h2
                    near[nb] = bs + t
                    nb += (h2 <= hi2) & (h2 >= lo2)
                for e in range(d0, d1):
                    d = odir[e]
                    sad = sa[d]
                    cad = ca[d]
                    bhd = bh[d]
                    c2d = cat2[d]
                    nk = 0
                    for t in range(nb):  # the band and the cone of the azimuth without roots: no branches
                        dx = qx[t]
                        dy = qy[t]
                        g = dx * sad + dy * cad
                        keep[nk] = t  # d_xy² < 1e−40 (d_xy < 1e−20) is not checked for the cone
                        nk += (abs(sad * dy - cad * dx) <= bhd) & (g * g >= c2d * ((dx * dx + dy * dy) - 1e-40))
                    for r in range(nk):
                        t = keep[r]
                        b = near[t]
                        dx = qx[t]
                        dy = qy[t]
                        dz = qz[t]
                        dxy = math.sqrt(dx * dx + dy * dy)
                        g = dx * sad + dy * cad
                        if azc[d] != 0.0 and dxy >= _EPS and abs(g) / dxy < cat[d]:
                            continue
                        if dxy >= _EPS and g != 0.0:  # d_xy with the sign of the projection on the azimuth
                            ds = dxy if g > 0.0 else -dxy
                        else:  # no projection: Δ = r_j − r_i of the rows i < j takes the plus
                            ds = dxy if rows[a] < rows[b] else -dxy
                        if abs(cp[d] * dz + sp[d] * ds) > bv[d]:
                            continue
                        h = math.sqrt(q2[t])
                        if dipc[d] != 0.0 and h >= _EPS and abs(ds * cp[d] - dz * sp[d]) / h < cdt[d]:
                            continue
                        s = dx * ux[d] + dy * uy[d] + dz * uz[d]
                        ta = (s > 0.0) | ((s == 0.0) & (rows[a] < rows[b]))  # the tail is i when Δ·u ≥ 0
                        it = a * ta + b * (1 - ta)
                        ih = a + b - it
                        dv = vs[a] - vs[b]
                        into_lags(acc, comp, cnt, d * nlag, h, dv, vs[a] + vs[b], ws[it], ws[ih], cen, tol, inv, span)
    for p in range(acc.shape[0]):
        acc[p] += comp[p]


# --- the run ------------------------------------------------------------------------------------------------


def strip_bounds(cost: np.ndarray, checks_in_strip: int | None = None) -> np.ndarray:
    """Cells of the strips: at most MAX_STRIPS runs of cells with about equal sums of `cost`, at least
    `checks_in_strip` (STRIP_CHECKS) in a strip. Depends on the costs only."""
    nc = len(cost)
    total = int(cost.sum())
    want = max(1, min(MAX_STRIPS, nc, -(-total // int(checks_in_strip or STRIP_CHECKS))))
    cum = np.cumsum(cost)
    cuts = [int(np.searchsorted(cum, (t * total) // want, side="left")) + 1 for t in range(1, want)]
    return np.unique(np.clip(np.array([0, *cuts, nc], dtype=np.int64), 0, nc))


def pairwise(parts: np.ndarray) -> np.ndarray:
    """The sum over the first axis: neighbours are added in pairs, in the order of the parts."""
    while parts.shape[0] > 1:
        if parts.shape[0] % 2:
            parts = np.concatenate([parts, np.zeros_like(parts[:1])])
        parts = parts[0::2] + parts[1::2]
    return parts[0]


def grid(x: np.ndarray, y: np.ndarray, z: np.ndarray, cell: float) -> tuple[np.ndarray, ...]:
    """Points by cells of side `cell`: (order, ckey, cstart, cijk, dims); `order` — rows by (cell, row)."""
    idx = []
    for c in (x, y, z):
        span = (float(c.max()) - float(c.min())) / cell
        if not span < _AXIS:
            raise bad("lag", "Лаг слишком мал для охвата данных.", "Увеличьте лаг или число лагов либо сузьте домен.")
        idx.append(np.floor((c - c.min()) / cell).astype(np.int64))
    dims = np.array([int(i.max()) + 1 for i in idx], dtype=np.int64)
    if int(dims[0]) * int(dims[1]) * int(dims[2]) >= 2**62:
        raise bad("lag", "Лаг слишком мал для охвата данных.", "Увеличьте лаг или число лагов либо сузьте домен.")
    key = idx[0] + dims[0] * (idx[1] + dims[1] * idx[2])
    order = np.argsort(key, kind="stable")
    ks = key[order]
    first = np.flatnonzero(np.concatenate([[True], ks[1:] != ks[:-1]]))
    ckey = ks[first]
    cstart = np.concatenate([first, [len(ks)]]).astype(np.int64)
    cijk = np.stack([ckey % dims[0], (ckey // dims[0]) % dims[1], ckey // (dims[0] * dims[1])], axis=1)
    return order, ckey, cstart, np.ascontiguousarray(cijk), dims


def prepare(
    x: np.ndarray, y: np.ndarray, z: np.ndarray, table: np.ndarray, lag: float, nlag: int, tol: float,
    cells: int | None = None,
) -> Plan:  # fmt: skip
    """The plan of a run over finite float64 points (at least one): grid, offsets, strips and the number of
    pair checks. `cells` — m, cells per h_max (CELLS when None)."""
    m = CELLS if cells is None else cells
    if int(m) != m or not (1 <= int(m) <= MAX_CELLS):
        raise bad("cells", "Число ячеек сетки на h_max должно быть целым от 1 до 16.", "Оставьте по умолчанию.")
    m = int(m)
    hmax = nlag * lag + tol
    cell = hmax / m * (1.0 + _GROW)
    order, ckey, cstart, cijk, dims = grid(x, y, z, cell)
    off, optr, odir = offset_table(dims.tolist(), m, cell, lag - tol, hmax, table)
    cost = _cell_checks(ckey, cstart, cijk, dims, off)
    xs, ys, zs = (np.ascontiguousarray(c[order]) for c in (x, y, z))
    return Plan(
        order, xs, ys, zs, ckey, cstart, cijk, dims, off, optr, odir, np.ascontiguousarray(table), lag, nlag, tol,
        strip_bounds(cost), int(cost.sum()), m,
    )  # fmt: skip


def run(plan: Plan, v: np.ndarray, *, threads: int = 1, tick: Tick | None = None) -> Raw:
    """The sums of the pairs of `plan` for the values `v` (float64[n], finite, in the order of the rows)."""
    p = plan
    ndir = p.table.shape[0]
    shift = float(np.mean(v))
    vs = np.ascontiguousarray(v[p.order])
    ws = vs - shift
    acc = np.zeros((p.strips, ndir, p.nlag, NSUM))
    cnt = np.zeros((p.strips, ndir, p.nlag, 2), dtype=np.int64)

    def one(s: int, _hi: int) -> None:
        _strip(
            p.xs, p.ys, p.zs, vs, ws, p.order, p.ckey, p.cstart, p.cijk, p.dims, p.off, p.optr, p.odir, p.table,
            p.lag, p.tol, p.nlag, p.bounds[s], p.bounds[s + 1], acc[s].reshape(-1), cnt[s].reshape(-1),
        )  # fmt: skip

    strips.run(one, p.strips, threads=threads, tick=tick, size=1)
    total = cnt.sum(axis=0)
    return Raw(total[:, :, 0].copy(), total[:, :, 1].copy(), pairwise(acc), shift)
