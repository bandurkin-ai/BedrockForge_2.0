"""The back-transform of a model of normal scores to the units of Z — the Hermite expansion (ТЗ 8.6.3; ADR 0022
§8, decision 12; e16-api.md `POST /variography/back`; Т8.6-6). numpy, float64; no numba: 200 nodes × 31 terms and
a fit of 3 × 100 points.

    Z = Φ_Z(Y), Y ~ N(0, 1) — the table of normal scores read back with the tails of `backtr` (`stats.transforms`)
    H_0 = 1, H_1 = y, H_(n+1) = (y·H_n − √n·H_(n−1)) / √(n + 1)       orthonormal: E[H_m·H_n] = δ_mn
    φ_n = E[Φ_Z(Y)·H_n(Y)] = Σ_k w_k·Φ_Z(x_k)·H_n(x_k)               x, w — `hermegauss(200)`, w / Σw
    ρ_Y = 1 − γ_Y        C_Z(ρ) = Σ_(n=1..30) φ_n²·ρ^n        γ_Z = C_Z(1) − C_Z(ρ_Y) = γ_Y·Σ_(k=0..29) T_k·ρ_Y^k
    T_k = Σ_(n=k+1..30) φ_n²  (the factored form: no loss of digits near h = 0; T_0 = C_Z(0), the sill)
    share = Σ_(n=1..30) φ_n² / Var(Z) — below 0,95 a warning, the model of Z is given all the same

Трактовки (ТЗ says no more than the lines above):
- Var(Z) — that of Φ_Z(Y) by the same quadrature, Σ_k w_k·(Φ_Z(x_k) − φ_0)²: the polynomials H_0…H_199 are
  orthonormal on the 200 nodes, so the share is the part of the variance the 30 terms keep, never above 1 (the
  variance of the data differs from it by the declustering weights and the interpolation of the table).
- ρ_Y = 1 − γ_Y literally: a model of Y with the sill S_Y ≠ 1 is taken as it is, with a warning; S_Y outside
  (0; 2] (|ρ_Y| > 1, the series diverges) is refused.
- The model of Z: the types and the angles of the model of Y. Its nugget is exact — γ_Z just above 0,
  γ_Z(C0_Y); its sill is fixed at γ_Z(S_Y) (= C_Z(0) at S_Y = 1); the contributions and the ranges are fitted
  by `fit.fit_model` (equal weights) to the curves γ_Z / γ_Z(S_Y) as `nscore` (the sill 1 fixed, C0 fixed).
- The curves: the axes of the first structure (`curve` takes the same); along axis i 100 distances
  h_k = k·H_i / 100, k = 1…100, H_i = 1,5 × the largest range of the structures along it (h = 0 is not in a
  fit: γ(0) = 0 is not the nugget). The ranges of the fit lie in [¼ × the least; 2 × the largest] of the
  ranges of Y (the ranges of Z are not longer: C_Z(ρ) / C_Z(1) ≤ ρ at 0 ≤ ρ ≤ 1).
- `residual` — Σ (γ_Z − γ_fit)² over the 3 × 100 points, in the units of Z².
- A model of Y without structures (a pure nugget) gives a pure nugget of Z and no curves.
- The model of a request may be of `nscore`, `semivariogram` or `correlogram` (all are of 1 − ρ_Y on the
  normal scores); `pairwise` is refused. The model of Z is a `semivariogram`.

    quadrature(nodes=200) -> (x, w)                hermite(y, terms=30) -> H [terms + 1, *y.shape]
    anamorphosis(table | callable, *, ltail, utail, ltpar, utpar, nodes=200, terms=30) -> Anamorphosis
        .phi [31]  .var_z  .mean  .sill (C_Z(0))  .share  .cov(ρ)  .gamma(γ_Y) -> γ_Z      NaN stays NaN
    back_transform(model_y: Model, ana, *, tick=None) -> BackResult
        .model (Z)  .share  .var_z  .residual  .n  .curves [Curve(axis, h, gamma_z, gamma_fit)]  .warnings
        .answer() -> the answer of the API
    back_variogram(result, model, table, *, ltail="linear", utail="linear", ltpar=None, utpar=None, tick=None)
        `POST /variography/back`: {model (Z), share, warning: null | "…", residual, curves: [{axis, h [],
        gamma_z [], gamma_fit []}]}; `result` — `job.result` of the calc (`transform` must be `nscore`), `table`
        — its `NormalScoreTransform` (the blob `nscore`). For `meta.back` of a stored model — the answer
        without `curves`.

Errors — `StatsError {code, message, hint}` with `param` (API — 422): `bad_param` — `key` (not a calc of
normal scores, no table), `model`, `model.type`, `model.…` (`exchange.api_model`), `transform`, the tails
`ltail`, `utail`, `ltpar`, `utpar` (`stats.transforms.check_tails`).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

from bf_core.geostat.rotation import bad_param
from bf_core.stats.transforms import NormalScoreTransform

from .exchange import api_model, model_form
from .fit import FitPoints, fit_model
from .model import Model, Structure, reduced_distance

NODES = 200  # ADR 0022, decision 12
TERMS = 30
SHARE_MIN = 0.95
CURVE_POINTS = 100
CURVE_SPAN = 1.5  # × the largest range along an axis
SILL_TOL = 1e-6  # relative: a sill of Y this close to 1 is 1
SILL_MAX = 2.0  # |ρ_Y| ≤ 1
RANGE_LO, RANGE_HI = 0.25, 2.0  # the bounds of the ranges of the fit × the least / the largest range of Y
EDGE = 1e-6  # relative: a range this close to its upper bound is on it
Y_TYPES = ("nscore", "semivariogram", "correlogram")
_KEY_HINT = "Рассчитайте вариограммы нормальных оценок (преобразование nscore) и повторите."


def _num(x: float) -> str:
    return f"{x:.6g}".replace(".", ",")


@lru_cache(maxsize=4)
def _gauss(nodes: int) -> tuple[np.ndarray, np.ndarray]:
    from numpy.polynomial.hermite_e import hermegauss

    x, w = hermegauss(nodes)
    x, w = np.asarray(x, dtype=np.float64), np.asarray(w, dtype=np.float64)
    w = w / w.sum()  # the probability weights of N(0, 1): Σw = 1, E[1] exact
    x.setflags(write=False)
    w.setflags(write=False)
    return x, w


def quadrature(nodes: int = NODES) -> tuple[np.ndarray, np.ndarray]:
    """The nodes and the weights of Gauss–Hermite (the weight e^(−y²/2)), the weights summing to 1."""
    x, w = _gauss(int(nodes))
    return x.copy(), w.copy()


def hermite(y: Any, terms: int = TERMS) -> np.ndarray:
    """H_0 … H_terms at y (the orthonormal Hermite polynomials of N(0, 1), He_n / √n!): [terms + 1, *y.shape]."""
    yy = np.asarray(y, dtype=np.float64)
    out = np.empty((terms + 1, *yy.shape))
    out[0] = 1.0
    if terms >= 1:
        out[1] = yy
    for n in range(1, terms):
        out[n + 1] = (yy * out[n] - math.sqrt(n) * out[n - 1]) / math.sqrt(n + 1)
    return out


@dataclass(frozen=True, eq=False)
class Anamorphosis:
    """The Hermite coefficients φ_0 … φ_terms of Φ_Z and Var(Z) of the same quadrature."""

    phi: np.ndarray
    var_z: float
    nodes: int = NODES

    @property
    def terms(self) -> int:
        return int(self.phi.size) - 1

    @property
    def mean(self) -> float:
        """E[Z] = φ_0."""
        return float(self.phi[0])

    @property
    def tails(self) -> np.ndarray:
        """T_k = Σ_(n=k+1..terms) φ_n², k = 0 … terms − 1."""
        sq = self.phi[1:] * self.phi[1:]
        return np.asarray(np.cumsum(sq[::-1])[::-1])

    @property
    def sill(self) -> float:
        """C_Z(0) = Σ_(n≥1) φ_n²."""
        return float(self.tails[0]) if self.terms else 0.0

    @property
    def share(self) -> float:
        """C_Z(0) / Var(Z): the part of the variance the terms keep."""
        return self.sill / self.var_z if self.var_z > 0.0 else math.nan

    def cov(self, rho: Any) -> np.ndarray:
        """C_Z(ρ) = Σ_(n=1..terms) φ_n²·ρ^n (Horner); NaN stays NaN."""
        r = np.asarray(rho, dtype=np.float64)
        sq = self.phi * self.phi
        out = np.zeros(r.shape)
        for n in range(self.terms, 0, -1):
            out = (out + sq[n]) * r
        return np.asarray(out)

    def gamma(self, gamma_y: Any) -> np.ndarray:
        """γ_Z = C_Z(1) − C_Z(1 − γ_Y), factored: γ_Y·Σ_k T_k·ρ^k; 0 at γ_Y = 0, NaN stays NaN."""
        g = np.asarray(gamma_y, dtype=np.float64)
        r = 1.0 - g
        t = self.tails
        q = np.zeros(g.shape)
        for k in range(t.size - 1, -1, -1):
            q = q * r + t[k]
        return np.asarray(g * q)


def anamorphosis(transform: Any, *, ltail: Any = "linear", utail: Any = "linear", ltpar: Any = None,
                 utpar: Any = None, nodes: int = NODES, terms: int = TERMS) -> Anamorphosis:  # fmt: skip
    """φ_n of Φ_Z: a table of normal scores (`NormalScoreTransform`, read back with the tails of `backtr`) or a
    function y → z of arrays (the analytic Z = e^Y of Т8.6-6)."""
    x, w = _gauss(int(nodes))
    if isinstance(transform, NormalScoreTransform):
        f = transform.inverse(x, ltail, utail, ltpar, utpar)
    elif callable(transform):
        f = np.asarray(transform(x), dtype=np.float64)
    else:
        raise bad_param("transform", "Нет таблицы нормальных оценок.", _KEY_HINT)
    if f.shape != x.shape or not bool(np.isfinite(f).all()):
        raise bad_param(
            "transform",
            "Преобразование дало пропуски или бесконечные значения в узлах квадратуры.",
            "Проверьте таблицу нормальных оценок и хвосты обратного преобразования.",
        )
    wf = w * f
    phi = np.sum(hermite(x, terms) * wf, axis=1)  # pairwise sums along the nodes: the same bits on any BLAS
    d = f - phi[0]
    return Anamorphosis(np.asarray(phi), float(np.sum(w * d * d)), int(nodes))


# --- the model of Z -------------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Curve:
    axis: int
    h: np.ndarray
    gamma_z: np.ndarray
    gamma_fit: np.ndarray


@dataclass
class BackResult:
    model: Model
    anamorphosis: Anamorphosis
    residual: float
    n: int
    curves: list[Curve]
    warnings: list[str] = field(default_factory=list)

    @property
    def share(self) -> float:
        return self.anamorphosis.share

    @property
    def var_z(self) -> float:
        return self.anamorphosis.var_z

    def answer(self) -> dict[str, Any]:
        """The answer of `POST /variography/back`."""
        curves = [{"axis": c.axis, "h": [float(v) for v in c.h], "gamma_z": [float(v) for v in c.gamma_z],
                   "gamma_fit": [float(v) for v in c.gamma_fit]} for c in self.curves]  # fmt: skip
        return {
            "model": model_form("semivariogram", self.model),
            "share": float(self.share),
            "warning": " ".join(self.warnings) or None,
            "residual": float(self.residual),
            "curves": curves,
        }


def _sill_of_y(model: Model) -> float:
    sill = model.sill
    if not sill > 0.0:
        raise bad_param("model", "Порог модели нормальных оценок равен нулю: обратное преобразование не определено.",
                        "Задайте самородок или вклады структур (порог модели нормальных оценок — 1).")  # fmt: skip
    if sill > SILL_MAX:
        raise bad_param("model", f"Порог модели нормальных оценок C0 + Σc = {_num(sill)} больше 2: ρ_Y = 1 − γ_Y"
                        " меньше −1, ряд Эрмита расходится.",
                        "Подберите модель величины «нормальные оценки» (порог 1).")  # fmt: skip
    return sill


def _axis_curve(model: Model, ana: Anamorphosis, u: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(h, γ_Z) along the unit u: 100 distances to 1,5 × the largest range of the structures along it."""
    top = max(1.0 / float(reduced_distance(s, u)) for s in model.structures)  # h_r = 1 at that distance
    h = (CURVE_SPAN * top) * np.arange(1, CURVE_POINTS + 1, dtype=np.float64) / CURVE_POINTS
    return h, ana.gamma(model.gamma(h[:, None] * u))


def back_transform(model: Model, ana: Anamorphosis, *, tick: Callable[[], None] | None = None) -> BackResult:
    """The model of Z of the model of normal scores `model` (module docstring)."""
    if tick is not None:
        tick()
    sill_y = _sill_of_y(model)
    warnings: list[str] = []
    share = ana.share
    if not share >= SHARE_MIN:
        pct = f"{100.0 * share:.1f}".replace(".", ",")
        warnings.append(f"Разложение Эрмита ({ana.terms} членов) передаёт {pct} % дисперсии Z — меньше 95 %:"
                        " порог модели Z занижен. Проверьте выбросы и хвосты распределения.")  # fmt: skip
    if abs(sill_y - 1.0) > SILL_TOL:
        warnings.append(f"Порог модели нормальных оценок {_num(sill_y)}, а не 1: ρ_Y = 1 − γ_Y на больших"
                        " расстояниях не равна нулю.")  # fmt: skip
    s_z = float(ana.gamma(sill_y))
    if not s_z > 0.0:
        raise bad_param("transform", "Значения Z в узлах квадратуры постоянны: порог модели Z равен нулю.",
                        "Проверьте таблицу нормальных оценок.")  # fmt: skip
    c0_z = float(ana.gamma(model.c0)) + 0.0
    if not model.structures:
        return BackResult(Model(c0_z, (), model.common_axes), ana, 0.0, 0, [], warnings)
    axes = model.structures[0].axes
    parts = [_axis_curve(model, ana, axes[i]) for i in range(3)]
    h = np.concatenate([p[0] for p in parts])
    u = np.concatenate([np.tile(axes[i], (CURVE_POINTS, 1)) for i in range(3)])
    axis = np.repeat(np.arange(1, 4, dtype=np.int64), CURVE_POINTS)
    points = FitPoints(h, np.concatenate([p[1] for p in parts]) / s_z, np.ones(h.size), u, axis)
    ranges = [a for s in model.structures for a in s.ranges]
    lo, hi = RANGE_LO * min(ranges), RANGE_HI * max(ranges)
    start = Model(c0_z / s_z, model.structures, model.common_axes)
    got = fit_model(points, start, kind="nscore", scale=1.0, lo=lo, hi=hi, weights="n", fix={("c0", -1)},
                    tick=tick)  # fmt: skip
    fitted = got.model
    structures = tuple(Structure(s.type, s_z * s.c, *s.ranges, *s.angles) for s in fitted.structures)
    model_z = Model(c0_z, structures, fitted.common_axes)
    curves, residual = [], 0.0
    for i, (hh, gz) in enumerate(parts):
        gf = model_z.gamma(hh[:, None] * axes[i])
        d = gz - gf
        residual += float(np.dot(d, d))
        curves.append(Curve(i + 1, hh, gz, gf))
    edge = [f"структура {k + 1}, ось {j + 1}" for k, s in enumerate(structures) for j, a in enumerate(s.ranges)
            if a >= hi * (1.0 - EDGE)]  # fmt: skip
    if edge:
        warnings.append(f"Дальность модели Z на границе {_num(hi)} м (2 × наибольшей дальности модели нормальных"
                        f" оценок): {'; '.join(edge)}.")  # fmt: skip
    return BackResult(model_z, ana, residual, int(h.size), curves, warnings)


def back_variogram(result: Any, model: Any, table: Any, *, ltail: Any = "linear", utail: Any = "linear",
                   ltpar: Any = None, utpar: Any = None,
                   tick: Callable[[], None] | None = None) -> dict[str, Any]:  # fmt: skip
    """`POST /variography/back`: the model of Z of `model` (in the units of Y) of the calc `result` (`job.result`)
    with the table of normal scores `table`."""
    if not isinstance(result, Mapping) or result.get("transform") != "nscore":
        raise bad_param("key", "Обратное преобразование — только для расчёта с преобразованием nscore.", _KEY_HINT)
    if not isinstance(table, NormalScoreTransform):
        raise bad_param("key", "У расчёта нет таблицы нормальных оценок.", _KEY_HINT)
    kind, start = api_model(model)
    if kind not in Y_TYPES:
        raise bad_param("model.type", "Обратное преобразование — для модели нормальных оценок; парная относительная"
                        " вариограмма не подходит.", "Подберите модель величины «нормальные оценки».")  # fmt: skip
    ana = anamorphosis(table, ltail=ltail, utail=utail, ltpar=ltpar, utpar=utpar)
    return back_transform(start, ana, tick=tick).answer()
