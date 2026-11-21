"""Bootstrap of the statistics of a sample (ТЗ 7.29; ADR 0019 §4, §7) — the port of BF
`api/app/stats/uncertainty.py::bootstrap` and of the bootstrap of BF `routers/stats.py`; the six statistics of
the tab «Статистика» are counted in one pass. `resample_groups` and the `local_*` maps of BF are Э16.

    bootstrap(data, stat, n_boot=1000, weights=None, seed=None) -> float64[n_boot]
        BF as it is: `stat` of every resample — any callable of a 1D array
    realizations(values, n_boot=1000, weights=None, seed=None, tick=None) -> {name: float64[n_boot]}
        the statistics `STATISTICS` (mean, median, std, cv, p10, p90) of the same resamples; NaN — undefined
    observed(values, weights=None) -> {name: float}      the statistics of the data themselves
    bootstrap_stats(values, n_boot=1000, weights=None, seed=None, confidence=0.95, tick=None) -> dict
        JSON-ready, for the tab: count, n_boot (10 … 5000), seed, confidence, weighted and per statistic
        observed, observed_weighted, mean and std of the realizations, ci_low, ci_high, defined (realizations
        where it is defined), edges and counts (the histogram of 24 bins); None — undefined, never 0
    draws(n, weights, n_boot, seed) -> iterator of int64[n]     the rows of every realization

Resampling (BF). A realization draws n rows with replacement: `rng.integers(0, n, n)` of
`numpy.random.default_rng(seed)`; with weights (declustering) a row is drawn with the probability of its
weight — the row whose step of the cumulative weight holds `rng.random(n)` — and the statistic of the resample
itself has no weights (GSLIB-style). One seed — one result; without a seed one is drawn and returned.

Statistics. σ is the population one, CV = σ / mean — undefined when the mean ≤ 0 (as `stats/describe.py`; BF:
when it is 0). The median, P10 and P90 follow the percentile rule of the project (`stats/weighted.py`: a value
sits at the middle of its step, i.e. Hazen without weights) — the P10 of the tab is the P10 of the histogram;
BF took the linear rule of numpy there. The interval is the percentiles (1 − c) / 2 and (1 + c) / 2 of the
realizations by the same rule. With weights `observed_weighted` is the statistic of the data with the weights
(what the realizations gather around); `observed` — without them, as BF.

One pass: the data are sorted once; the mean and σ of a realization are sums over its rows in the order of
the draws (about the median of the data — values far from zero lose nothing; numpy's pairwise sums, no BLAS:
the same bits on any machine), its percentiles are read from the number of times every sorted row was drawn
(`bincount`, the cumulative counts). The realizations equal `stat(data[idx])` of BF on the same draws to
1e−12. 20 000 rows × 1000 realizations — about 0.2 s, 0.4 s with weights.

Inputs must be finite (the tab leaves out the rows without a value first). Errors — `StatsError` {code,
message, hint}: `bad_input`, `no_values`, `bad_param` with `param` (a ValueError, as in BF).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from typing import Any

import numpy as np

from .transforms import as_values, as_weights
from .weighted import StatsError, Tick

STATISTICS = ("mean", "median", "std", "cv", "p10", "p90")
MIN_BOOT, MAX_BOOT = 10, 5000  # realizations of the tab (BF)
HIST_BINS = 24
CONFIDENCE = 0.95  # the interval 2.5–97.5 % (ADR 0019 §4)
_SHARES = (0.1, 0.5, 0.9)  # the percentiles of a resample: p10, median, p90
_TICK = 16  # realizations between two calls of `tick`
_WALK = 6  # passes of `_Steps.rows` before the plain search


def _bad(param: str, message: str, hint: str) -> StatsError:
    return StatsError("bad_param", message, hint, param=param)


def _sample(values: Any) -> np.ndarray:
    x = as_values(values)
    if x.size == 0:
        raise StatsError("no_values", "Нет значений для бутстрапа.", "Проверьте поле и отбор.")
    return x


class _Steps:
    """The steps of the cumulative weight of the rows: `rows(u)` is the row whose step holds u —
    `searchsorted(cdf, u, side="right")` exactly, found several times faster on unsorted u: a table of n equal
    parts of 0 … 1 gives a row not above that of any u of the part (the part of u is ⌊u·n⌋; the table is built
    a few last bits lower than the rounding of u·n can reach), a few passes move the rows up, and what they
    did not settle (long runs of tiny weights) is searched plainly."""

    def __init__(self, w: np.ndarray) -> None:
        cw = np.cumsum(w)
        self.n = int(w.size)
        self.cdf = cw / cw[-1]  # the last one is 1: above any u of [0; 1)
        self.guide = np.searchsorted(self.cdf, np.arange(self.n) / self.n * (1.0 - 2.0**-50), side="right")

    def rows(self, u: np.ndarray) -> np.ndarray:
        n, cdf = self.n, self.cdf
        idx = self.guide.take(np.minimum((u * n).astype(np.intp), n - 1))
        for _ in range(_WALK):
            low = cdf.take(idx) <= u
            if not low.any():
                return idx  # type: ignore[no-any-return]
            idx += low
        low = cdf.take(idx) <= u
        if low.any():
            idx[low] = np.searchsorted(cdf, u[low], side="right")
        return idx  # type: ignore[no-any-return]


def draws(n: int, w: np.ndarray | None, n_boot: int, seed: Any) -> Iterator[np.ndarray]:
    """The rows of every realization: n of 0 … n − 1 with replacement (BF: `rng.integers(0, n, n)`; with
    weights — `searchsorted(cdf, rng.random(n), side="right")`). One seed — the same rows; the bootstrap of the
    pairs of QAQC (`qaqc.external`, ADR 0020 §4) takes its resamples here."""
    rng = np.random.default_rng(seed)
    if w is None:
        for _ in range(n_boot):
            yield rng.integers(0, n, n)
        return
    steps = _Steps(w)
    for _ in range(n_boot):
        yield steps.rows(rng.random(n))


_draws = draws  # the name before Э14


def bootstrap(
    data: Any, stat: Callable[[np.ndarray], Any], n_boot: int = 1000, weights: Any = None, seed: Any = None
) -> np.ndarray:
    """The bootstrap distribution of `stat(sample)` (BF `bootstrap`): `n_boot` realizations.

    :param stat: a callable of a 1D array that returns a number
    :param weights: not negative, one per value: the probability of a row to be drawn (declustering)
    :param seed: the seed of the generator (None — a new one every time)
    """
    x = _sample(data)
    if n_boot < 1:
        raise _bad("n_boot", "Число реализаций должно быть не меньше 1.", "Задайте, например, 1000.")
    w = as_weights(weights, int(x.size))
    out = np.empty(n_boot)
    for b, idx in enumerate(draws(int(x.size), w, n_boot, seed)):
        out[b] = stat(x[idx])
    return out


class _Sorted:
    """The data sorted once: what the statistics of a resample are read from."""

    def __init__(self, x: np.ndarray) -> None:
        n = int(x.size)
        order = np.argsort(x, kind="stable")
        self.n = n
        self.xs = x[order]
        self.rank = np.empty(n, dtype=np.intp)  # the sorted place of every row
        self.rank[order] = np.arange(n)
        self.pivot = float(self.xs[n // 2])  # sums are taken about the median: no loss far from zero
        self.dev = x - self.pivot
        # The percentile rule: the place n·p − ½ among the sorted values, the end values outside.
        h = np.clip(n * np.array(_SHARES) - 0.5, 0.0, n - 1.0)
        lo = np.floor(h).astype(np.intp)
        self.places = np.column_stack([lo, np.minimum(lo + 1, n - 1)]).ravel()  # 6 order statistics
        self.part = h - lo

    def moments(self, dev: np.ndarray, weights: np.ndarray | None = None) -> tuple[float, float, float]:
        """(mean, std, cv) of the values `pivot + dev` (with weights — the weighted ones)."""
        if weights is None:
            m = float(dev.sum()) / dev.size
            q = float((dev * dev).sum()) / dev.size
        else:
            total = float(weights.sum())
            m = float((weights * dev).sum()) / total
            q = float((weights * dev * dev).sum()) / total
        mean, std = self.pivot + m, math.sqrt(max(0.0, q - m * m))
        return mean, std, std / mean if mean > 0.0 else math.nan

    def stats(self, idx: np.ndarray) -> tuple[float, float, float, float, float, float]:
        """(mean, median, std, cv, p10, p90) of the resample of the rows `idx`: the sums in the order of the
        draws, the percentiles by the number of times every sorted row was drawn."""
        mean, std, cv = self.moments(self.dev[idx])
        drawn = np.cumsum(np.bincount(self.rank[idx], minlength=self.n))
        v = self.xs[np.searchsorted(drawn, self.places, side="right")]
        p10, median, p90 = (float(v[2 * i] + self.part[i] * (v[2 * i + 1] - v[2 * i])) for i in range(3))
        return mean, median, std, cv, p10, p90


def realizations(
    values: Any, n_boot: int = 1000, weights: Any = None, seed: Any = None, *, tick: Tick | None = None
) -> dict[str, np.ndarray]:
    """The statistics `STATISTICS` of `n_boot` resamples (see the module docstring): float64[n_boot] by name;
    NaN where a statistic is undefined (the CV of a mean ≤ 0). `tick(fraction)` may raise to cancel."""
    x = _sample(values)
    n = int(x.size)
    if n_boot < 1:
        raise _bad("n_boot", "Число реализаций должно быть не меньше 1.", "Задайте, например, 1000.")
    w = as_weights(weights, n)
    data = _Sorted(x)
    out = np.empty((n_boot, len(STATISTICS)))
    for b, idx in enumerate(draws(n, w, n_boot, seed)):
        out[b] = data.stats(idx)
        if tick is not None and (b + 1) % _TICK == 0:
            tick((b + 1) / n_boot)
    if tick is not None:
        tick(1.0)
    return {name: np.ascontiguousarray(out[:, k]) for k, name in enumerate(STATISTICS)}


def observed(values: Any, weights: Any = None) -> dict[str, float]:
    """The statistics of the data themselves; with `weights` — the weighted ones: the mean and σ with the
    weights, the percentiles by the rule of `stats/weighted.py`. NaN — undefined."""
    x = _sample(values)
    w = as_weights(weights, int(x.size))
    data = _Sorted(x)
    if w is None:
        return dict(zip(STATISTICS, data.stats(np.arange(x.size)), strict=True))
    from .weighted import weighted_percentiles

    mean, std, cv = data.moments(data.dev, w)
    p10, median, p90 = (float(p) for p in weighted_percentiles(x, w, [100.0 * s for s in _SHARES]))
    return {"mean": mean, "median": median, "std": std, "cv": cv, "p10": p10, "p90": p90}


def _whole(raw: Any, lo: int, hi: int, param: str, label: str) -> int:
    try:
        if isinstance(raw, bool):
            raise TypeError
        value = float(raw)
    except (TypeError, ValueError):
        value = math.nan
    if not lo <= value <= hi or value != int(value):
        raise _bad(param, f"{label}: нужно целое число от {lo} до {hi}.", f"Задайте целое число от {lo} до {hi}.")
    return int(value)


def _confidence(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = math.nan
    if not 0.5 < value < 1.0:
        raise _bad(
            "confidence",
            "Доверительная вероятность должна быть больше 0,5 и меньше 1.",
            "Задайте, например, 0,95 — интервал 2,5–97,5 %.",
        )
    return value


def _seed(raw: Any) -> int:
    """The seed given (a whole number ≥ 0) or a new one."""
    if raw is None:
        return int(np.random.SeedSequence().generate_state(1)[0])
    if isinstance(raw, bool) or not isinstance(raw, (int, np.integer)) or raw < 0:
        raise _bad(
            "seed", "Зерно генератора — целое число не меньше нуля.", "Задайте, например, 1, или оставьте пустым."
        )
    return int(raw)


def _num(x: float) -> float | None:
    return float(x) if math.isfinite(x) else None


def bootstrap_stats(
    values: Any,
    n_boot: Any = 1000,
    weights: Any = None,
    seed: Any = None,
    confidence: Any = CONFIDENCE,
    *,
    tick: Tick | None = None,
) -> dict[str, Any]:
    """The bootstrap of the tab «Статистика»: the intervals of the mean, the median, σ, CV, P10 and P90 — see
    the module docstring. `weights` — of declustering (None — equal)."""
    x = _sample(values)
    boots = _whole(n_boot, MIN_BOOT, MAX_BOOT, "n_boot", "Число реализаций")
    conf = _confidence(confidence)
    used = _seed(seed)
    reals = realizations(x, boots, weights, used, tick=tick)
    plain = observed(x)
    heavy = observed(x, weights) if weights is not None else None
    tail = 100.0 * (1.0 - conf) / 2.0
    stats: dict[str, Any] = {}
    for name in STATISTICS:
        r = reals[name]
        r = r[np.isfinite(r)]
        entry: dict[str, Any] = {
            "observed": _num(plain[name]),
            "observed_weighted": None if heavy is None else _num(heavy[name]),
            "defined": int(r.size),
        }
        if r.size:
            lo, hi = np.percentile(r, [tail, 100.0 - tail], method="hazen")  # the rule of `weighted_percentiles`
            counts, edges = np.histogram(r, bins=HIST_BINS)
            entry.update(mean=float(r.mean()), std=float(r.std()), ci_low=float(lo), ci_high=float(hi),
                         edges=[float(e) for e in edges], counts=[int(c) for c in counts])  # fmt: skip
        else:
            entry.update(mean=None, std=None, ci_low=None, ci_high=None, edges=[], counts=[])
        stats[name] = entry
    return {
        "count": int(x.size),
        "n_boot": boots,
        "seed": used,
        "confidence": conf,
        "weighted": weights is not None,
        "statistics": stats,
    }
