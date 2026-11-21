"""External control of QAQC (ТЗ 8.4.2; ADR 0020 §4): the bias of the main laboratory against the control one, the
paired t-test and the 95 % intervals of the RMA line by a bootstrap of the pairs. Pure functions of numpy over
the pairs that are taken (`duplicates.select`; x1 — the main laboratory, x2 — the control one); HARD, the shares
and the line itself are those of the duplicates (`duplicates.measure`, `duplicates.scatter_stats`).

    bias(mean_main, mean_check) -> float       100·(x̄₁ − x̄₂) / x̄₂, %; NaN — not defined
    paired_t(x1, x2) -> (t, p, df)             `scipy.stats.ttest_rel(x1, x2)`; all NaN — fewer than two pairs
                                               or every difference is the same (no spread: nothing to test)
    realizations(n, n_boot) -> int             `n_boot`, or ⌊10⁸ / n⌋ (not below 100) when n·n_boot > 10⁸
    rma_intervals(x, y, n_boot=1000, seed=12345, tick=None) -> RmaIntervals
        a, b    (low, high) — the percentiles 2,5 and 97,5 of the intercept and the slope of y = a + b·x over
                the realizations (linear interpolation); None — fewer than three pairs or no line
        n_boot  the realizations made (0 — none)

Bootstrap (ADR 0020 §4, вопрос № 39). A realization draws n pairs with replacement — the rows of
`stats.bootstrap.draws` (PCG64 by the seed; the generator starts anew for every call, so a group does not
depend on the groups beside it: one seed — one result). Of a realization only five sums are taken — Σx, Σy,
Σx², Σy², Σxy about the means of the data, by the number of times every pair was drawn; b = sign(Sxy)·√(Syy /
Sxx), a = ȳ − b·x̄ as `stats.scatter`. A realization without spread of x has no line and is left out. On
equal values (y = x) every realization gives b = 1 and a = 0 exactly: the intervals are [1; 1] and [0; 0].
`tick(share)` is called every 16 realizations — a job may cancel there.

100 thousand pairs × 1000 realizations — about 0.9 s, 8 MB (`stats.bootstrap` and its numba kernels are loaded
by `rma_intervals` only).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from .standards import values_of

MAX_DRAWS = 10**8  # n·B of a group
MIN_BOOT = 100
LEVELS = (2.5, 97.5)
_TICK = 16
_FLAT = 1e-12  # Sxx below this share of Σx² is no spread


@dataclass(frozen=True)
class RmaIntervals:
    """The 95 % intervals of the RMA line (see the module docstring)."""

    a: tuple[float, float] | None
    b: tuple[float, float] | None
    n_boot: int


def bias(mean_main: float | None, mean_check: float | None) -> float:
    """The bias of the main laboratory against the control one, %."""
    if mean_main is None or mean_check is None or not mean_check > 0.0:
        return math.nan
    out = 100.0 * (mean_main - mean_check) / mean_check
    return out if math.isfinite(out) else math.nan


def paired_t(x1: Any, x2: Any) -> tuple[float, float, float]:
    """The paired t-test of the two laboratories: (t, p, degrees of freedom); NaN — not defined."""
    a = values_of(x1, name="x1")
    n = int(a.shape[0])
    b = values_of(x2, n, "x2")
    if n < 2 or float(np.ptp(a - b)) == 0.0:
        return math.nan, math.nan, math.nan
    from scipy.stats import ttest_rel

    got = ttest_rel(a, b)
    t, p = float(got.statistic), float(got.pvalue)
    if not (math.isfinite(t) and math.isfinite(p)):
        return math.nan, math.nan, math.nan
    return t, p, float(got.df)


def realizations(n: int, n_boot: int) -> int:
    """The realizations a group of n pairs gets: n·B ≤ 10⁸, but not fewer than 100."""
    return n_boot if n * n_boot <= MAX_DRAWS else max(MIN_BOOT, MAX_DRAWS // max(n, 1))


def rma_intervals(
    x: Any, y: Any, n_boot: int = 1000, seed: int = 12345, *, tick: Callable[[float], None] | None = None
) -> RmaIntervals:
    """The 95 % intervals of the intercept and the slope of the RMA line y = a + b·x by a bootstrap of the
    pairs (see the module docstring)."""
    xs = values_of(x, name="x")
    n = int(xs.shape[0])
    ys = values_of(y, n, "y")
    if n < 3:
        return RmaIntervals(None, None, 0)
    from bf_core.stats.bootstrap import draws

    count = realizations(n, n_boot)
    mx, my = float(xs.mean()), float(ys.mean())
    dx, dy = xs - mx, ys - my
    table = np.ascontiguousarray(np.stack((dx, dy, dx * dx, dy * dy, dx * dy)))
    sums = np.empty((count, 5))
    for i, rows in enumerate(draws(n, None, count, seed)):
        times = np.bincount(rows, minlength=n).astype(np.float64)
        sums[i] = np.einsum("i,ji->j", times, table)
        if tick is not None and (i + 1) % _TICK == 0:
            tick((i + 1) / count)
    with np.errstate(all="ignore"):
        sxx = sums[:, 2] - sums[:, 0] ** 2 / n
        syy = sums[:, 3] - sums[:, 1] ** 2 / n
        sxy = sums[:, 4] - sums[:, 0] * sums[:, 1] / n
        slope = np.where(sxy != 0.0, np.copysign(np.sqrt(syy / sxx), sxy), 0.0)
        intercept = (my + sums[:, 1] / n) - slope * (mx + sums[:, 0] / n)
        defined = (sxx > _FLAT * sums[:, 2]) & np.isfinite(slope) & np.isfinite(intercept)
    if tick is not None:
        tick(1.0)
    if int(np.count_nonzero(defined)) < 2:
        return RmaIntervals(None, None, count)
    a_low, a_high = np.percentile(intercept[defined], LEVELS).tolist()
    b_low, b_high = np.percentile(slope[defined], LEVELS).tolist()
    return RmaIntervals((a_low, a_high), (b_low, b_high), count)
