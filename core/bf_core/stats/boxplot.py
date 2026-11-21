"""Box plots by domains (ТЗ 8.3; ADR 0019 §4): quartiles by the rule of `weighted_percentiles`, whiskers,
outliers, the mean and the number of samples of every domain — from one sort of all the domains.

    box_stats(sample, log=False) -> Box | None      the numbers of one sorted sample (None — nothing to show:
                                                    a log scale without a value > 0)
    box_chart(selection, codes=None, labels=None, log=False, max_outliers=5000, tick=None) -> dict
        the answer of `POST /stats/box` but the names:
        {log, weighted, counts, boxes [{domain, role, count, weight_sum, nonpositive, mean, arithmetic_mean, min,
         max, q1, median, q3, whisker_low, whisker_high, outliers_low, outliers_high, outliers [values]}],
         outliers {total, shown, limit}, conventions}
    warm()

A box (ТЗ 8.3). Q1, the median and Q3 — `weighted_percentiles` of the values with the weights of the selection
(Т8.3-1: equal within 1e−12). The whiskers — the least and the greatest value inside [Q1 − 1.5·IQR; Q3 +
1.5·IQR], IQR = Q3 − Q1 (a whisker never goes inside the box: when no value lies between a fence and the
quartile, the whisker ends at the quartile). The values outside the fences are outliers, shown as points.
`mean` — the mean with the weights, `count` — the rows.

Log scale (`log`): everything is counted by logarithms — the quartiles are those of log10 of the values (given
back in data units, 10^q), the fences are 1.5·IQR of the logarithms away, `mean` is the mean of the logarithms
(the geometric mean); `arithmetic_mean` stays beside it. Values ≤ 0 have no place on the scale: they are
counted (`nonpositive` of a box, `counts.nonpositive_log`) and left out; a domain without a value > 0 is not
shown. Without `log`, `mean` = `arithmetic_mean`.

Outliers: no more than `max_outliers` (5000) points on a chart. When there are more, every tail of every box
keeps its share of the limit: its outermost value always stays, the others are taken at even steps along the
sorted tail (the first and the last of it stay). `outliers_low` / `outliers_high` of a box and
`outliers.total` are the true numbers, `outliers.shown` — the points sent.

Domains — `domain_stats.group_samples`: rows without a domain make a box of their own (role "none"); without
`codes` there is one box of all the rows (role "all"). What is not defined is None.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .domain_stats import group_samples
from .select import Selection, bad_param
from .weighted import Sorted, StatsError, Tick, pair_sums

MAX_OUTLIERS = 5000
WHISKER = 1.5  # of IQR
QUARTILES = (25.0, 50.0, 75.0)
CONVENTIONS = (
    "Квартили — по GSLIB (середина накопленного веса), с весами отбора. Усы — крайние значения в пределах "
    "[Q1 − 1,5·IQR; Q3 + 1,5·IQR], за ними — выбросы. На логарифмической шкале расчёт идёт по log10 значений, "
    "среднее — геометрическое; значения ≤ 0 не участвуют."
)


@dataclass(frozen=True)
class Box:
    """The numbers of a box; the scale of the chart is log10 of the values with `log`. `values` — the sorted
    values on the chart (data units), `low` / `high` — how many of its first / last ones are outliers."""

    count: int
    weight_sum: float
    nonpositive: int  # rows ≤ 0 left out of a log scale
    mean: float  # on the scale of the chart, in data units: the geometric mean with `log`
    arithmetic_mean: float
    q1: float
    median: float
    q3: float
    whisker_low: float
    whisker_high: float
    values: np.ndarray
    low: int
    high: int


def box_stats(sample: Sorted, log: bool = False) -> Box | None:
    """The box of a sorted sample (see the module docstring); None — no row to show."""
    k0 = int(np.searchsorted(sample.v, 0.0, side="right")) if log else 0  # rows 0 … k0 − 1 have no logarithm
    n = sample.n - k0
    if n <= 0:
        return None
    v, w = sample.v[k0:], sample.w[k0:]
    below_w = float(sample.cw[k0 - 1]) if k0 else 0.0
    below_m = float(sample.cm[k0 - 1]) if k0 else 0.0
    total = sample.total - below_w
    arithmetic = (sample.metal - below_m) / total
    if log:
        s = np.log10(v)
        pos = ((sample.cw[k0:] - below_w) - 0.5 * w) / total
        q1, q2, q3 = (float(q) for q in np.interp(np.asarray(QUARTILES) / 100.0, pos, s))
        sw, sws = pair_sums(w, s)
        mean = 10.0 ** (sws / sw)
    else:
        s = v
        q1, q2, q3 = (float(q) for q in sample.percentiles(QUARTILES))
        mean = arithmetic
    reach = WHISKER * (q3 - q1)
    first = int(np.searchsorted(s, q1 - reach, side="left"))  # the first value inside the fences
    last = int(np.searchsorted(s, q3 + reach, side="right"))  # one past the last value inside
    # the whiskers are values of the sample; a whisker never ends inside the box
    low = float(v[first]) if first < n and s[first] <= q1 else None
    high = float(v[last - 1]) if last > 0 and s[last - 1] >= q3 else None
    back = (lambda q: 10.0**q) if log else (lambda q: q)
    return Box(
        n, total, k0, mean, arithmetic, back(q1), back(q2), back(q3),
        back(q1) if low is None else low, back(q3) if high is None else high, v, first, n - last,
    )  # fmt: skip


def _thin(count: int, quota: int, keep_last: bool) -> np.ndarray:
    """`quota` positions of a sorted tail of `count` values: even steps with both ends; one — the outermost."""
    if quota >= count:
        return np.arange(count)
    if quota <= 1:
        return np.array([count - 1 if keep_last else 0])
    return np.unique(np.rint(np.linspace(0.0, count - 1.0, quota)).astype(np.int64))


def _num(x: float) -> float | None:
    x = float(x)
    return x if math.isfinite(x) else None


def box_chart(
    selection: Selection, codes: Any = None, labels: Any = None, *, log: bool = False,
    max_outliers: int = MAX_OUTLIERS, tick: Tick | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The boxes of the rows of `selection` by the domains `codes` / `labels` (columns of the rows the
    selection was made of; None — one box of all the rows). StatsError: `no_values`, `no_positive` (a log
    scale without a value > 0), `too_many_domains`."""
    if not isinstance(log, bool):
        raise bad_param("log", "«Логарифмическая шкала» — это да или нет.", "Передайте true или false.")
    step = (lambda f: None) if tick is None else tick
    found = group_samples(selection, codes, labels, tick=lambda f: step(0.85 * f))
    made = [(g, box_stats(g.sample, log)) for g in found.groups]
    dropped = sum(g.sample.n - (0 if b is None else b.count) for g, b in made)
    boxes = [(g, b) for g, b in made if b is not None]
    if not boxes:
        raise StatsError(
            "no_positive", "Для логарифмической шкалы нужны значения больше нуля.",
            "Снимите логарифмическую шкалу: положительных значений в выборке нет.", param="log",
        )  # fmt: skip
    # the tails with outliers: (box, the tail is the upper one, its size)
    tails = [(i, upper, c) for i, (_, b) in enumerate(boxes) for upper, c in ((False, b.low), (True, b.high)) if c]
    total = sum(c for _, _, c in tails)
    spare, rest = max(max_outliers - len(tails), 0), max(total - len(tails), 1)
    shown: list[list[np.ndarray]] = [[] for _ in boxes]
    for i, upper, c in tails:
        b = boxes[i][1]
        quota = c if total <= max_outliers else 1 + spare * (c - 1) // rest
        at = _thin(c, quota, upper)
        shown[i].append(b.values[b.count - c + at] if upper else b.values[at])
    out_boxes = []
    for (g, b), points in zip(boxes, shown, strict=True):
        out_boxes.append({
            **g.head(),
            "count": b.count,
            "weight_sum": _num(b.weight_sum),
            "nonpositive": b.nonpositive,
            "mean": _num(b.mean),
            "arithmetic_mean": _num(b.arithmetic_mean),
            "min": float(b.values[0]),
            "max": float(b.values[-1]),
            "q1": _num(b.q1),
            "median": _num(b.median),
            "q3": _num(b.q3),
            "whisker_low": _num(b.whisker_low),
            "whisker_high": _num(b.whisker_high),
            "outliers_low": b.low,
            "outliers_high": b.high,
            "outliers": np.concatenate(points).tolist() if points else [],
        })  # fmt: skip
    step(1.0)
    return {
        "log": log,
        "weighted": selection.weighted,
        "counts": {**found.counts, "nonpositive_log": dropped},
        "boxes": out_boxes,
        "outliers": {"total": total, "shown": sum(len(b["outliers"]) for b in out_boxes), "limit": max_outliers},
        "conventions": CONVENTIONS,
    }


def warm() -> None:
    """Compile the kernels on a few rows (the `stats` step of the warm-up)."""
    from .select import select_values

    rows = np.array([1.0, 3.0, 2.0, 50.0, 0.0])
    for log in (False, True):
        box_chart(select_values(rows, rows + 1.0), np.array([0, 1, 0, 0, 1]), ["a", "b"], log=log)
