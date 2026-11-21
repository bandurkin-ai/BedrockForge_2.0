"""Descriptive statistics of a sample sorted once (ТЗ 7.25; ADR 0019 §4) — the port of BF `stats/histogram.py`
`describe` onto `weighted.Sorted`: the statistics with the weights and without them stand beside and come from
the same sort.

    describe(sample, weighted=True) -> dict        the statistics of a `Sorted` (JSON-ready: float, int, None)
    describe_both(sample) -> (weighted, plain)     both from one pass
    moments(sample) -> (Moments, Moments)          the raw numbers (Q-Q takes μ and σ of the values and of ln)
    hazen(sample, ps) -> float64[len(ps)]          percentiles without weights (numpy method="hazen")
    warm()

Keys (BF): count, weight_sum, min, max, mean, median, std, variance, cv, skewness, percentiles {"5" … "99"},
log {count, mean_ln, variance_ln, geometric_mean} — the logarithms of the values > 0, None when there are none.
What cannot be computed is None, never 0: an empty sample (count 0), CV of a mean ≤ 0 (as `Sorted.cv`), the
skewness of a sample without spread, numbers that overflow float64.

Conventions (ADR 0019 §4, shown in the window — `CONVENTIONS`): m = Σ w·c / Σ w; the variance is the population
one, Σ w·(c − m)² / Σ w; CV = σ / m; skewness = Σ w·(c − m)³ / (Σ w·σ³); percentiles by GSLIB — a value sits at
the middle of its cumulative weight, P_i = (C_i − w_i / 2) / W, linear in between, the first and the last
value outside (`Sorted.percentiles`); without weights it is the Hazen rule.

Numbers. The mean comes from the compensated sums of `Sorted`; the central moments are summed about it in a
second pass (the first-order term corrects what is left of the mean's rounding), the logarithms — about the
logarithm of the median row, the weighted median for the weighted statistics (|mean − median| ≤ σ, so nothing
drowns). Sums run over blocks of 1024 rows in the order of the sorted rows, the blocks are added with
compensation (Neumaier): 1e−12 relative against a direct count, the same bits on any machine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.jit import njit

from .weighted import Sorted, pair_sums

PERCENTILES = (5, 10, 25, 50, 75, 90, 95, 97.5, 99)
CONVENTIONS = (
    "Процентили по GSLIB: середина накопленного веса, линейная интерполяция (без весов — Hazen). "
    "Дисперсия — генеральная."
)
_MEDIAN = PERCENTILES.index(50)
_CHUNK = 1024
NAN = float("nan")


@dataclass(frozen=True)
class Moments:
    """The moments of a sample (weighted or plain); NaN — not defined."""

    n: int
    total: float  # Σ w; n for the plain statistics
    mean: float
    variance: float  # the population variance
    skewness: float  # NaN without spread
    log_n: int  # rows with a value > 0
    log_total: float  # their Σ w
    mean_ln: float
    variance_ln: float

    @property
    def std(self) -> float:
        return math.sqrt(self.variance) if self.variance >= 0.0 else NAN


_NO = Moments(0, 0.0, NAN, NAN, NAN, 0, 0.0, NAN, NAN)


@njit(cache=True, nogil=True)
def _flush(acc, comp, s):  # type: ignore[no-untyped-def]
    """Add the sums of a block to the totals with compensation; the block sums are zeroed."""
    for j in range(s.shape[0]):
        x = s[j]
        t = acc[j] + x
        if abs(acc[j]) >= abs(x):
            comp[j] += (acc[j] - t) + x
        else:
            comp[j] += (x - t) + acc[j]
        acc[j] = t
        s[j] = 0.0


@njit(cache=True, nogil=True)
def _sums(v, w, mw, mu, k0, lw0, lu0):  # type: ignore[no-untyped-def]
    """Sums over the sorted rows: [0–2] Σ w·d, Σ w·d², Σ w·d³ with d = v − mw; [3–5] Σ e, Σ e², Σ e³ with
    e = v − mu; over the rows k0 … (v > 0): [6] Σ w, [7–8] Σ w·a, Σ w·a² with a = ln v − lw0; [9–10] Σ b, Σ b²
    with b = ln v − lu0."""
    acc = np.zeros(11)
    comp = np.zeros(11)
    s = np.zeros(11)
    n = v.shape[0]
    a = 0
    while a < n:
        b = min(a + _CHUNK, n)
        s0 = 0.0
        s1 = 0.0
        s2 = 0.0
        s3 = 0.0
        s4 = 0.0
        s5 = 0.0
        for i in range(a, b):
            d = v[i] - mw
            x = w[i] * d
            s0 += x
            x *= d
            s1 += x
            s2 += x * d
            e = v[i] - mu
            s3 += e
            x = e * e
            s4 += x
            s5 += x * e
        s[0] = s0
        s[1] = s1
        s[2] = s2
        s[3] = s3
        s[4] = s4
        s[5] = s5
        _flush(acc, comp, s)
        a = b
    a = k0
    while a < n:
        b = min(a + _CHUNK, n)
        s6 = 0.0
        s7 = 0.0
        s8 = 0.0
        s9 = 0.0
        s10 = 0.0
        for i in range(a, b):
            ln = np.log(v[i])
            x = w[i]
            s6 += x
            d = ln - lw0
            x *= d
            s7 += x
            s8 += x * d
            e = ln - lu0
            s9 += e
            s10 += e * e
        s[6] = s6
        s[7] = s7
        s[8] = s8
        s[9] = s9
        s[10] = s10
        _flush(acc, comp, s)
        a = b
    return acc + comp


def _central(total: float, s1: float, s2: float, s3: float) -> tuple[float, float]:
    """The variance and the skewness from the sums of d, d², d³ about a mean that is off by s1 / total."""
    delta = s1 / total
    var = max(s2 / total - delta * delta, 0.0)
    if not var > 0.0:
        return var, NAN  # no spread, or NaN — not defined
    m3 = (s3 - 3.0 * delta * s2 + 3.0 * delta * delta * s1) / total - delta**3
    return var, m3 / (var * math.sqrt(var))


def moments(sample: Sorted) -> tuple[Moments, Moments]:
    """The moments of the sample with its weights and without them (every row 1)."""
    n = sample.n
    if n == 0:
        return _NO, _NO
    v, w, cw = sample.v, sample.w, sample.cw
    total, vmin, vmax = sample.total, float(v[0]), float(v[-1])
    k0 = int(np.searchsorted(v, 0.0, side="right"))  # rows 0 … k0 − 1 are ≤ 0
    log_n = n - k0
    if vmin == vmax:  # no spread: the mean is the value itself, whatever the rounding of Σ w·v / Σ w
        ln = math.log(vmin) if log_n else NAN
        var_ln = 0.0 if log_n else NAN
        return (
            Moments(n, total, vmin, 0.0, NAN, log_n, total if log_n else 0.0, ln, var_ln),
            Moments(n, float(n), vmin, 0.0, NAN, log_n, float(log_n), ln, var_ln),
        )
    mw = min(max(sample.mean, vmin), vmax)
    mu = min(max(float(pair_sums(v, v)[0]) / n, vmin), vmax)
    lw0 = lu0 = 0.0
    if log_n:
        below = float(cw[k0 - 1]) if k0 else 0.0
        mid = int(np.searchsorted(cw, 0.5 * (below + total), side="left"))
        lw0 = math.log(float(v[min(max(mid, k0), n - 1)]))
        lu0 = math.log(float(v[(k0 + n) // 2]))
    with np.errstate(over="ignore", invalid="ignore"):
        s = [float(x) for x in _sums(v, w, mw, mu, k0, lw0, lu0)]
    var_w, skew_w = _central(total, s[0], s[1], s[2])
    var_u, skew_u = _central(float(n), s[3], s[4], s[5])
    if log_n:
        aw, au = s[7] / s[6], s[9] / log_n
        logs_w = (s[6], lw0 + aw, max(s[8] / s[6] - aw * aw, 0.0))
        logs_u = (float(log_n), lu0 + au, max(s[10] / log_n - au * au, 0.0))
    else:
        logs_w = logs_u = (0.0, NAN, NAN)
    return Moments(n, total, mw, var_w, skew_w, log_n, *logs_w), Moments(n, float(n), mu, var_u, skew_u, log_n, *logs_u)


def hazen(sample: Sorted, ps: Any) -> np.ndarray:
    """Percentiles `ps` (0–100) of the values without weights: row i sits at (i + 0.5) / n, linear in between,
    the first and the last value outside — numpy `percentile(…, method="hazen")`. NaN when the sample is empty."""
    p = np.atleast_1d(np.asarray(ps, dtype=np.float64))
    n = sample.n
    if n == 0:
        return np.full(p.shape, np.nan)
    v = sample.v
    if n == 1:
        return np.full(p.shape, float(v[0]))
    x = np.clip(p / 100.0 * n - 0.5, 0.0, n - 1.0)
    i = np.minimum(np.floor(x).astype(np.int64), n - 2)
    f = x - i
    lo, hi = v[i], v[i + 1]
    step = hi - lo
    return np.asarray(np.where(f >= 0.5, hi - step * (1.0 - f), lo + step * f), dtype=np.float64)


def _num(x: float) -> float | None:
    """A number of the answer: None for what is not defined (NaN, ±inf) — JSON has neither."""
    x = float(x)
    return x if math.isfinite(x) else None


def _stats(m: Moments, vmin: float, vmax: float, pct: np.ndarray) -> dict[str, Any]:
    std = m.std
    log = None
    if m.log_n:
        with np.errstate(over="ignore"):
            geometric = float(np.exp(m.mean_ln))  # inf when it overflows → None
        log = {
            "count": m.log_n,
            "mean_ln": _num(m.mean_ln),
            "variance_ln": _num(m.variance_ln),
            "geometric_mean": _num(geometric),
        }
    return {
        "count": m.n,
        "weight_sum": _num(m.total) if m.n else 0.0,
        "min": _num(vmin),
        "max": _num(vmax),
        "mean": _num(m.mean),
        "median": _num(pct[_MEDIAN]),
        "std": _num(std),
        "variance": _num(m.variance),
        "cv": _num(std / m.mean) if m.mean > 0.0 else None,
        "skewness": _num(m.skewness),
        "percentiles": {f"{p:g}": _num(q) for p, q in zip(PERCENTILES, pct, strict=True)},
        "log": log,
    }


def describe_both(sample: Sorted) -> tuple[dict[str, Any], dict[str, Any]]:
    """The statistics with the weights of the sample and without them (see the module docstring)."""
    weighted, plain = moments(sample)
    vmin, vmax = sample.vmin, sample.vmax
    return (
        _stats(weighted, vmin, vmax, sample.percentiles(PERCENTILES)),
        _stats(plain, vmin, vmax, hazen(sample, PERCENTILES)),
    )


def describe(sample: Sorted, *, weighted: bool = True) -> dict[str, Any]:
    """The statistics of the sample: with its weights, or with `weighted=False` — every row 1."""
    return describe_both(sample)[0 if weighted else 1]


def warm() -> None:
    """Compile the kernel on a few rows (the `stats` step of the warm-up)."""
    from .weighted import sort_groups

    rows = np.array([3.0, 1.0, 0.0, 2.0])
    describe_both(sort_groups(rows, rows + 1.0)[0])
