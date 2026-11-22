"""Experimental variograms by directions, all the types from one pass over the pairs (ТЗ 8.6.2, ADR 0022 §3–4).

    e = experimental(x, y, z, v, directions, lag, nlag, lag_tol=None, *, threads=1, tick=None, cells=None)
        x, y, z, v   rows of points (z = None — a plan, z = 0); a row with a missing or infinite number takes no
                     part and is counted (`n_missing`) — a missing value never becomes 0
        directions   [Direction(az, plunge, az_tol, dip_tol, band_h, band_v)] — `pairs.Direction`
        lag, nlag, lag_tol   L, the number of lags, the tolerance (L / 2 when None); lag k is centred on k·L
        threads, tick        threads over the strips; tick(fraction) after every strip, may raise to cancel
        cells        m of the grid (`pairs.CELLS` when None); the result does not depend on it beyond 1e−12
    e = calculate(p, directions, lag, nlag, lag_tol=None, *, limit=PAIRS_MAX, seed=SEED, threads, tick, cells)
        the same over `points.Points` (16.12), sampled when the estimate is above `limit` (below)
    n = estimate(x, y, z, directions, lag, nlag, lag_tol=None, *, cells=None)   the pair checks of the grid
    dirs = axes(ang1, ang2, ang3, az_tol, dip_tol, band_h, band_v)   the axes a1, a2, a3 of R (8.6.3) as directions
    e.n, e.n_rel                         int64[ndir, nlag]
    e.h, e.gamma, e.rho, e.one_minus_rho, e.pairwise, e.tail, e.head      float64[ndir, nlag], NaN — no value
    e.few(min_pairs=30)                  bool[ndir, nlag]: N < min_pairs («мало пар»; a lag without pairs too)
    e.sums                               PairSums: the raw sums (what is stored), the curves are its properties
    e.n_points, e.n_missing, e.checks, e.estimate, e.strips, e.cells, e.sampled, e.pw_skipped
    e.to_json(min_pairs, axis=True)      `directions` of the answer (docs/progress/e16-api.md)

Types (ADR 0022 §3). Semivariogram γ = Σ(z_i − z_j)² / (2N). Correlogram ρ = (Σz_t·z_h / N − m_t·m_h) /
(σ_t·σ_h) over the tails and the heads of the pairs (an omnidirectional pair counts both ways); a lag where a σ
is zero has no ρ. Pairwise relative γ = Σ((z_i − z_j) / ((z_i + z_j) / 2))² / (2·N_rel) over the N_rel pairs with
z_i + z_j > 0; the others are left out and counted (N − N_rel, `pw_skipped`). Normal scores — the semivariogram of
the scores. A lag without pairs holds NaN in every curve (null in JSON), never 0. Errors — `StatsError` {code,
message, hint} in Russian: `bad_param` (with `param`), `too_few_values`.

The estimate and the sample (ADR 0022 §4, 8.6.2). The estimate is the number of pair checks of the grid by the
counts of its cells (`Plan.checks`, the upper bound of the walk, independent of the values); above `limit`
(2·10⁹) `calculate` takes n′ = ⌊n·√(limit / estimate)⌋ points without replacement, `Generator(PCG64(seed))`
(`points.sample_size`, `points.sample`), and gives `sampled {n, of, seed}`. The same points, the same seed — the
same sample and the same result, bit for bit. The variogram along the holes (`downhole`) is never sampled.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.stats.weighted import StatsError

from . import pairs
from .pairs import S_D2, S_H, S_HD, S_HH, S_REL, S_T, S_TH, S_TT, Direction, Tick

if TYPE_CHECKING:
    from .points import Points

PAIRS_MAX = 2_000_000_000  # = spec.PAIRS_MAX: checks of pairs above which the points are sampled (8.6.2)
SEED = 12345  # = spec.SEED
_FLAT = 16.0 * np.finfo(np.float64).eps  # a variance below this share of the mean square is rounding: σ = 0


def _ratio(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a / b, NaN where b is not positive."""
    out = np.full(np.shape(a), np.nan)
    np.divide(a, b, out=out, where=b > 0)
    return out


def nulls(a: Any) -> list[Any]:
    """A curve for JSON: NaN → None, numbers as Python floats."""
    return [None if not math.isfinite(x) else float(x) for x in np.asarray(a, dtype=np.float64).tolist()]


def length_json(x: float) -> float | str:
    """A band or a length for JSON: ∞ → "inf"."""
    return "inf" if math.isinf(x) else float(x)


@dataclass(frozen=True)
class PairSums:
    """The sums of the pairs by [direction, lag] (`pairs.run`) and the curves of the variogram types."""

    n: np.ndarray  # int64[ndir, nlag]: unordered pairs
    n_rel: np.ndarray  # int64[ndir, nlag]: pairs with z_i + z_j > 0
    sums: np.ndarray  # float64[ndir, nlag, 8]: pairs.S_H … pairs.S_REL
    shift: float  # the tail and head sums are of z − shift
    both: np.ndarray  # bool[ndir]: a pair counts both ways in the tail and head sums (omnidirectional)

    @property
    def h(self) -> np.ndarray:
        """The mean distance of the pairs of a lag."""
        return _ratio(self.sums[..., S_H], self.n)

    @property
    def gamma(self) -> np.ndarray:
        return _ratio(self.sums[..., S_D2], 2 * self.n)

    @property
    def pairwise(self) -> np.ndarray:
        return _ratio(self.sums[..., S_REL], 2 * self.n_rel)

    def _moments(self) -> tuple[np.ndarray, ...]:
        """N of the tails, means of the tails and heads less `shift`, their variances and the covariance."""
        s = self.sums
        both = self.both[:, None]
        n = np.where(both, 2 * self.n, self.n).astype(np.float64)
        t = np.where(both, s[..., S_T] + s[..., S_HD], s[..., S_T])
        hd = np.where(both, t, s[..., S_HD])
        tt = np.where(both, s[..., S_TT] + s[..., S_HH], s[..., S_TT])
        hh = np.where(both, tt, s[..., S_HH])
        th = np.where(both, 2.0 * s[..., S_TH], s[..., S_TH])
        mt, mh, mtt, mhh = _ratio(t, n), _ratio(hd, n), _ratio(tt, n), _ratio(hh, n)
        vt, vh = mtt - mt * mt, mhh - mh * mh
        vt = np.where(vt > _FLAT * mtt, vt, 0.0)
        vh = np.where(vh > _FLAT * mhh, vh, 0.0)
        return n, mt, mh, vt, vh, _ratio(th, n) - mt * mh

    @property
    def tail(self) -> np.ndarray:
        """The mean of the tail values."""
        return self._moments()[1] + self.shift

    @property
    def head(self) -> np.ndarray:
        return self._moments()[2] + self.shift

    @property
    def rho(self) -> np.ndarray:
        """The correlogram; NaN where a lag has no pairs or the tails or the heads do not vary."""
        _, _, _, vt, vh, cov = self._moments()
        return _ratio(cov, np.sqrt(vt * vh))

    @property
    def one_minus_rho(self) -> np.ndarray:
        """What the chart of the correlogram shows."""
        return 1.0 - self.rho

    def few(self, min_pairs: int = 30) -> np.ndarray:
        """Lags with fewer than `min_pairs` pairs (none too): drawn hollow, left out of the fit."""
        return self.n < int(min_pairs)

    def curves_json(self, k: int, lag: float, min_pairs: int) -> dict[str, Any]:
        """The curves of the row `k` for JSON: lags (centres k·L), h, n, gamma, rho, pairwise, n_pw, few."""
        nlag = self.n.shape[1]
        return {"lags": [lag * (i + 1) for i in range(nlag)], "h": nulls(self.h[k]), "n": self.n[k].tolist(),
                "gamma": nulls(self.gamma[k]), "rho": nulls(self.rho[k]), "pairwise": nulls(self.pairwise[k]),
                "n_pw": self.n_rel[k].tolist(), "few": self.few(min_pairs)[k].tolist()}  # fmt: skip


@dataclass(frozen=True)
class Experimental:
    """Experimental variograms of the directions; the curves are those of `sums`."""

    directions: tuple[Direction, ...]
    lag: float
    nlag: int
    lag_tol: float
    sums: PairSums
    n_points: int  # points that took part
    n_missing: int  # rows left out: no coordinate or no value
    checks: int  # pair checks of the run
    strips: int
    cells: int  # m of the grid
    sampled: dict[str, int] | None = None  # {"n": points taken, "of": points, "seed": seed}
    estimate: int = -1  # pair checks of all the points (the estimate of ADR 0022 §4); −1 — that of the run

    n = property(lambda self: self.sums.n)
    n_rel = property(lambda self: self.sums.n_rel)
    h = property(lambda self: self.sums.h)
    gamma = property(lambda self: self.sums.gamma)
    rho = property(lambda self: self.sums.rho)
    one_minus_rho = property(lambda self: self.sums.one_minus_rho)
    pairwise = property(lambda self: self.sums.pairwise)
    tail = property(lambda self: self.sums.tail)
    head = property(lambda self: self.sums.head)

    def few(self, min_pairs: int = 30) -> np.ndarray:
        return self.sums.few(min_pairs)

    @property
    def pw_skipped(self) -> int:
        """Pairs (over the directions and lags) left out of the pairwise relative: z_i + z_j ≤ 0."""
        return int((self.sums.n - self.sums.n_rel).sum())

    def to_json(self, min_pairs: int = 30, axis: bool = True) -> list[dict[str, Any]]:
        """`directions` of the answer: {axis (1, 2, 3 or None), az, plunge, tolerances, bands, curves}."""
        out = []
        for k, d in enumerate(self.directions):
            head = {"axis": k + 1 if axis else None, "az": float(d.az), "plunge": float(d.plunge),
                    "az_tol": float(d.az_tol), "dip_tol": float(d.dip_tol), "band_h": length_json(d.band_h),
                    "band_v": length_json(d.band_v)}  # fmt: skip
            out.append(head | self.sums.curves_json(k, self.lag, min_pairs))
        return out


def pairwise_warning(skipped: int) -> str | None:
    """The warning of the answer about the pairs left out of the pairwise relative variogram."""
    if skipped <= 0:
        return None
    return f"Парная относительная: пропущено {skipped} пар с z_i + z_j ≤ 0."


def finite_rows(*columns: Any) -> tuple[list[np.ndarray], int]:
    """float64 columns of equal length without the rows where any of them is missing or infinite, and the
    number of such rows. `None` is a column of zeros."""
    n = next(len(c) for c in columns if c is not None)
    cols = [np.zeros(n) if c is None else np.asarray(c, dtype=np.float64) for c in columns]
    if any(c.ndim != 1 or len(c) != n for c in cols):
        raise ValueError("the columns of the points must be 1D and of equal length")
    ok = np.ones(n, dtype=bool)
    for c in cols:
        ok &= np.isfinite(c)
    return [np.ascontiguousarray(c[ok]) for c in cols], int(n - ok.sum())


def too_few() -> StatsError:
    return StatsError(
        "too_few_values",
        "Для вариограммы нужно не меньше двух точек со значением и положением.",
        "Проверьте поле, домен и траектории скважин.",
    )


def axes(
    ang1: float, ang2: float = 0.0, ang3: float = 0.0, az_tol: float = 22.5, dip_tol: float = 22.5,
    band_h: float = math.inf, band_v: float = math.inf,
) -> list[Direction]:  # fmt: skip
    """The axes a1, a2, a3 of R (the rows, 8.6.3) as directions of the variogram with the common tolerances: a1 —
    (ang1, −ang2) itself (its unit vector is the row bit for bit), a2 and a3 — read from their rows (atan2; a
    vertical axis has the azimuth 0)."""
    from bf_core.geostat import rotation

    a, b, c = rotation.normalize_angles(ang1, ang2, ang3)
    rows = rotation.rotation_matrix(a, b, c)
    out = [Direction(a, -b + 0.0, az_tol, dip_tol, band_h, band_v)]
    for u in rows[1:]:
        flat = math.hypot(float(u[0]), float(u[1]))
        if flat < 1e-15:
            az, plunge = 0.0, (90.0 if u[2] < 0.0 else -90.0)
        else:
            az = math.degrees(math.atan2(float(u[0]), float(u[1]))) % 360.0
            az = 0.0 if az >= 360.0 else az + 0.0
            plunge = math.degrees(math.atan2(-float(u[2]), flat)) + 0.0
        out.append(Direction(az, plunge, az_tol, dip_tol, band_h, band_v))
    return out


def _plan(xs: np.ndarray, ys: np.ndarray, zs: np.ndarray, table: np.ndarray, lags: tuple[float, int, float],
          cells: int | None) -> pairs.Plan:  # fmt: skip
    if len(xs) < 2:
        raise too_few()
    return pairs.prepare(xs, ys, zs, table, *lags, cells)


def _columns(xyz: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """X, Y, Z of `Points.xyz` [n, 3], contiguous; they must be finite."""
    a = np.asarray(xyz, dtype=np.float64)
    if a.ndim != 2 or a.shape[1] != 3 or not np.isfinite(a).all():
        raise ValueError("the points of a calculation are finite [n, 3] (points.load)")
    return np.ascontiguousarray(a[:, 0]), np.ascontiguousarray(a[:, 1]), np.ascontiguousarray(a[:, 2])


def estimate(
    x: Any, y: Any, z: Any, directions: Sequence[Direction], lag: float, nlag: int, lag_tol: float | None = None,
    *, cells: int | None = None,
) -> int:  # fmt: skip
    """The pair checks of the grid for the points with a position (ADR 0022 §4): what decides the sample."""
    lags = pairs.check_lags(lag, nlag, lag_tol)
    (xs, ys, zs), _ = finite_rows(x, y, z)
    return _plan(xs, ys, zs, pairs.check_directions(tuple(directions)), lags, cells).checks


def _run(
    plan: pairs.Plan, v: np.ndarray, dirs: tuple[Direction, ...], lags: tuple[float, int, float], n_missing: int,
    threads: int, tick: Tick | None,
) -> Experimental:  # fmt: skip
    raw = pairs.run(plan, v, threads=threads, tick=tick)
    sums = PairSums(raw.n, raw.n_rel, raw.sums, raw.shift, np.array([d.omni for d in dirs], dtype=bool))
    lag, nlag, tol = lags
    return Experimental(dirs, lag, nlag, tol, sums, plan.n, n_missing, plan.checks, plan.strips, plan.cells,
                        estimate=plan.checks)  # fmt: skip


def experimental(
    x: Any,
    y: Any,
    z: Any,
    v: Any,
    directions: Sequence[Direction],
    lag: float,
    nlag: int,
    lag_tol: float | None = None,
    *,
    threads: int = 1,
    tick: Tick | None = None,
    cells: int | None = None,
) -> Experimental:
    """The experimental variograms of the points by the directions (see the module); no sample."""
    lags = pairs.check_lags(lag, nlag, lag_tol)
    dirs = tuple(directions)
    table = pairs.check_directions(dirs)
    (xs, ys, zs, vs), n_missing = finite_rows(x, y, z, v)
    plan = _plan(xs, ys, zs, table, lags, cells)
    return _run(plan, vs, dirs, lags, n_missing, threads, tick)


def calculate(
    p: Points,
    directions: Sequence[Direction],
    lag: float,
    nlag: int,
    lag_tol: float | None = None,
    *,
    limit: float = PAIRS_MAX,
    seed: int = SEED,
    threads: int = 1,
    tick: Tick | None = None,
    cells: int | None = None,
) -> Experimental:
    """The variograms of the points of a calculation (`points.load`): the estimate over all of them; above `limit`
    the run goes over the sample of `points.sample` and the answer gives `sampled`."""
    from . import points

    lags = pairs.check_lags(lag, nlag, lag_tol)
    dirs = tuple(directions)
    table = pairs.check_directions(dirs)
    xs, ys, zs = _columns(p.xyz)
    if not np.isfinite(p.values).all():
        raise ValueError("the points of a calculation are finite (points.load)")
    plan = _plan(xs, ys, zs, table, lags, cells)
    q, sampled = points.sample(p, points.sample_size(p.n, plan.checks, limit), seed)
    run_plan = plan if sampled is None else _plan(*_columns(q.xyz), table, lags, cells)
    e = _run(run_plan, np.ascontiguousarray(q.values, dtype=np.float64), dirs, lags, 0, threads, tick)
    return replace(e, sampled=sampled, estimate=plan.checks)
