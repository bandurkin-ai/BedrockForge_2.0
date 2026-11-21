"""Q-Q plots: the quantiles of a sample against a fitted distribution or against another sample (ТЗ 7.26;
ADR 0019 §4) — the port of BF `stats/qq.py` onto `weighted.Sorted` (one sort of a side for its statistics, its
quantiles and the distance).

    qq_distribution(sample, distribution="lognormal") -> dict
        every value at its plotting position P = (C_i − w_i / 2) / W against the quantile μ + σ·Φ⁻¹(P) of the
        normal or the lognormal distribution with the weighted mean and σ of the sample (of ln for the
        lognormal; values ≤ 0 are dropped and counted). More than MAX_POINTS (2000) values are thinned: the
        sample is interpolated at 2000 even steps of probability between the first and the last position, the
        least and the greatest value stay as they are. PPCC — the correlation of the points (Φ⁻¹(P) against
        the value or its ln); near 1 the distribution fits.
        {points [[theory, value, P %]], count, distribution {name, mu, sigma, dropped_nonpositive}, ppcc, marks}
    qq_samples(x, y, quantiles=99) -> dict
        the quantiles of both sides at p_k = k / (n + 1), by the rule of `Sorted.percentiles` (GSLIB); points
        on the line 1:1 — equal distributions, a parallel offset — a shift, another slope — a change of scale
        {points [[x, y, p %]], ratio [y / x | None], extremes {x, y: [min, max]}, ks, marks}
    ks_distance(x, y) -> float     the Kolmogorov–Smirnov distance D = max |F_x − F_y| of the weighted samples
    qq_chart(x, weighted=None, distribution="lognormal", y=None, y_weighted=None, quantiles=99, tick=None)
        the answer of `POST /stats/qq` but the names: {mode: "theory" | "sample", x: {weighted, counts, stats},
        y: the same | None, …the keys above}; `distribution` "sample" — against the selection `y`
    probabilities(n) -> k / (n + 1) in percent;  warm()

`marks` — the points of P10, P50 and P90 [[x, y, p %]]. A number that is not defined (a ratio to 0, a quantile
that overflows) is None. Errors — StatsError: `no_values`, `too_few_values`, `no_spread`, `bad_param` {param}.

The two sides of a large chart (each ≥ 200 thousand rows) are sorted in two threads — the kernels release the
GIL, each side is computed alone, so the answer has the same bits as without threads; `tick` is then called
between the steps only, from the caller's thread.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from numbers import Real
from typing import Any

import numpy as np
from scipy.special import ndtri

from bf_core.jit import njit

from .describe import Moments, describe, moments
from .select import Selection, bad_param, no_values
from .weighted import Sorted, StatsError, Tick

MAX_POINTS = 2000
DISTRIBUTIONS = ("lognormal", "normal")
QUANTILES = 99  # every percent
MIN_QUANTILES, MAX_QUANTILES = 3, 999
MARKS = (10.0, 50.0, 90.0)
PARALLEL_FROM = 200_000  # rows of the smaller side from which the sides are sorted in two threads
_P_MIN, _P_MAX = float(np.nextafter(0.0, 1.0)), float(np.nextafter(1.0, 0.0))  # Φ⁻¹ is finite inside


def probabilities(n: int) -> np.ndarray:
    """n probabilities k / (n + 1), in percent."""
    return np.asarray(np.arange(1, n + 1) / (n + 1) * 100.0)


def check_quantiles(value: Any) -> int:
    """The number of quantiles of a chart of two samples: an integer 3 … 999 (None — 99)."""
    if value is None:
        return QUANTILES
    ok = isinstance(value, Real) and not isinstance(value, bool) and float(value).is_integer()
    if not ok or not MIN_QUANTILES <= int(value) <= MAX_QUANTILES:
        raise bad_param(
            "quantiles", f"Число квантилей — целое от {MIN_QUANTILES} до {MAX_QUANTILES}.",
            "Например, 99 — каждый процент, 19 — каждые 5 %.",
        )  # fmt: skip
    return int(value)


def check_distribution(value: Any) -> str:
    if value not in DISTRIBUTIONS:
        raise bad_param(
            "distribution", f"Распределение «{value}» неизвестно.",
            "Допустимо: lognormal — логнормальное, normal — нормальное, sample — вторая выборка.",
        )  # fmt: skip
    return str(value)


def _numbers(a: np.ndarray) -> list[float | None]:
    """A column of the answer: None for what is not finite."""
    if bool(np.all(np.isfinite(a))):
        return [float(x) for x in a.tolist()]
    return [float(x) if math.isfinite(x) else None for x in a.tolist()]


def _points(*columns: np.ndarray) -> list[list[float | None]]:
    return [list(row) for row in zip(*(_numbers(c) for c in columns), strict=True)]


# --- two samples -------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _ks(xv, xc, yv, yc):  # type: ignore[no-untyped-def]
    """max |F_x − F_y| over the values of both sorted samples; F = Σ w of the rows ≤ t / Σ w."""
    n = xv.shape[0]
    m = yv.shape[0]
    wx = xc[n - 1]
    wy = yc[m - 1]
    i = 0
    j = 0
    d = 0.0
    while i < n or j < m:
        if j >= m or (i < n and xv[i] <= yv[j]):
            t = xv[i]
        else:
            t = yv[j]
        while i < n and xv[i] <= t:
            i += 1
        while j < m and yv[j] <= t:
            j += 1
        fx = xc[i - 1] / wx if i > 0 else 0.0
        fy = yc[j - 1] / wy if j > 0 else 0.0
        gap = abs(fx - fy)
        if gap > d:
            d = gap
    return d


def ks_distance(x: Sorted, y: Sorted) -> float:
    """The largest gap between the weighted distribution functions (two-sample Kolmogorov–Smirnov D)."""
    if x.n == 0 or y.n == 0:
        raise no_values()
    return float(_ks(x.v, x.cw, y.v, y.cw))


def qq_samples(x: Sorted, y: Sorted, quantiles: Any = QUANTILES) -> dict[str, Any]:
    """The quantiles of two sorted samples at k / (n + 1), their ratio, the extremes and the distance D."""
    n = check_quantiles(quantiles)
    if x.n == 0 or y.n == 0:
        raise no_values()
    ps, marks = probabilities(n), np.asarray(MARKS)
    both = np.concatenate((ps, marks))  # one pass over a side for its quantiles and its marks
    qx, qy = x.percentiles(both), y.percentiles(both)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(qx[:n] != 0.0, qy[:n] / qx[:n], np.nan)
    return {
        "points": _points(qx[:n], qy[:n], ps),
        "ratio": _numbers(ratio),
        "extremes": {"x": [x.vmin, x.vmax], "y": [y.vmin, y.vmax]},
        "ks": ks_distance(x, y),
        "marks": _points(qx[n:], qy[n:], marks),
    }


# --- a sample against a distribution -----------------------------------------------------------------------


def qq_distribution(
    sample: Sorted, distribution: Any = "lognormal", *, max_points: int = MAX_POINTS, fit: Moments | None = None
) -> dict[str, Any]:
    """The points of a sorted sample against the fitted normal or lognormal distribution (see the module
    docstring): x — the theoretical quantile, y — the value, at the plotting positions of the sample. `fit` —
    the weighted `moments(sample)[0]` when the caller has them already."""
    dist = check_distribution(distribution)
    n = sample.n
    if n == 0:
        raise no_values()
    fit = moments(sample)[0] if fit is None else fit
    lognormal = dist == "lognormal"
    k0 = int(np.searchsorted(sample.v, 0.0, side="right")) if lognormal else 0  # rows 0 … k0 − 1 have no ln
    if n - k0 < 2:
        if lognormal:
            raise StatsError(
                "too_few_values", "Для логнормального распределения нужны хотя бы два значения больше нуля.",
                f"Значений больше нуля — {n - k0} из {n}: выберите нормальное распределение или другое поле.",
            )  # fmt: skip
        raise StatsError("too_few_values", "Для Q-Q графика нужны хотя бы два значения.", "Проверьте поле и отбор.")
    mu, var = (fit.mean_ln, fit.variance_ln) if lognormal else (fit.mean, fit.variance)
    sigma = math.sqrt(var) if var > 0.0 else 0.0
    if not (sigma > 0.0 and math.isfinite(sigma) and math.isfinite(mu)):
        if var == 0.0:
            raise StatsError(
                "no_spread", "Все значения одинаковы: распределение не подобрать.",
                "Проверьте поле и отсечки: у выборки нет разброса.",
            )  # fmt: skip
        raise StatsError(
            "no_spread", "Значения слишком велики: распределение не подобрать.",
            "Проверьте поле: в нём есть значения, квадрат которых не помещается в число (больше 1e150).",
        )  # fmt: skip
    v, w, cw = sample.v[k0:], sample.w[k0:], sample.cw[k0:]
    below = float(sample.cw[k0 - 1]) if k0 else 0.0
    total = float(cw[-1]) - below
    p = ((cw - below) - 0.5 * w) / total
    np.maximum.accumulate(p, out=p)  # weights below the rounding of the running sum must not turn it back
    marks = np.asarray(MARKS) / 100.0
    at_marks = np.interp(marks, p, v)
    if v.shape[0] > max_points:
        # thinned at even steps of probability; the end values are kept as they are
        grid = np.linspace(p[0], p[-1], max_points)
        thin = np.interp(grid, p, v)
        thin[0], thin[-1] = v[0], v[-1]
        v, p = thin, grid
    p = np.clip(p, _P_MIN, _P_MAX)
    z = ndtri(p)
    zm = ndtri(marks)
    with np.errstate(over="ignore", invalid="ignore"):
        theory, tm = mu + sigma * z, mu + sigma * zm
        if lognormal:
            theory, tm = np.exp(theory), np.exp(tm)
        data = np.log(v) if lognormal else v
        ppcc = float(np.corrcoef(z, data)[0, 1]) if z.shape[0] > 2 else math.nan
    return {
        "points": _points(theory, v, p * 100.0),
        "count": n - k0,
        "distribution": {"name": dist, "mu": mu, "sigma": sigma, "dropped_nonpositive": k0},
        "ppcc": ppcc if math.isfinite(ppcc) else None,
        "marks": _points(tm, at_marks, marks * 100.0),
    }


# --- the chart ---------------------------------------------------------------------------------------------


def _side(selection: Selection, weighted: bool, tick: Tick | None) -> tuple[Sorted, dict[str, Any], Moments]:
    """A side sorted once, what the window shows about it, and its weighted moments."""
    sample = selection.sort(tick=tick)
    fit = moments(sample)
    stats = describe(sample, weighted=weighted, fit=fit)
    return sample, {"weighted": weighted, "counts": dict(selection.counts), "stats": stats}, fit[0]


def qq_chart(
    x: Selection,
    *,
    weighted: bool | None = None,
    distribution: Any = "lognormal",
    y: Selection | None = None,
    y_weighted: bool | None = None,
    quantiles: Any = QUANTILES,
    tick: Tick | None = None,
    parallel_from: int = PARALLEL_FROM,
) -> dict[str, Any]:
    """The answer of a Q-Q chart for the rows of `x`: against the distribution `distribution` ("lognormal",
    "normal"), or with "sample" against the rows of `y`. `weighted` / `y_weighted` say which statistics of a
    side are shown (None — as the selection was made: with weights or without)."""
    step = (lambda f: None) if tick is None else tick
    wx = x.weighted if weighted is None else weighted
    if distribution != "sample":
        dist = check_distribution(distribution)
        x.require()
        sample, info, fit = _side(x, wx, lambda f: step(0.9 * f))
        out = qq_distribution(sample, dist, fit=fit)
        step(1.0)
        return {"mode": "theory", "x": info, "y": None, **out}
    if y is None:
        raise bad_param("y_field", "Не выбрана вторая выборка.", "Выберите набор и поле стороны Y.")
    n = check_quantiles(quantiles)
    x.require()
    y.require()
    wy = y.weighted if y_weighted is None else y_weighted
    if min(x.n, y.n) >= parallel_from:
        step(0.02)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="bf-qq") as pool:
            other = pool.submit(_side, y, wy, None)
            sx, x_info, _ = _side(x, wx, None)
            sy, y_info, _ = other.result()
    else:
        sx, x_info, _ = _side(x, wx, lambda f: step(0.42 * f))
        sy, y_info, _ = _side(y, wy, lambda f: step(0.42 + 0.43 * f))
    step(0.85)
    out = qq_samples(sx, sy, n)
    step(1.0)
    return {"mode": "sample", "x": x_info, "y": y_info, **out}


def warm() -> None:
    """Compile the kernel on a few rows (the `stats` step of the warm-up)."""
    from .weighted import sort_groups

    rows = np.array([1.0, 3.0, 2.0, 5.0])
    a, b = sort_groups(rows, rows)[0], sort_groups(rows + 1.0, rows)[0]
    qq_samples(a, b, MIN_QUANTILES)
    qq_distribution(a, "lognormal")
