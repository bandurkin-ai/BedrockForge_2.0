"""Log-probability plot (ТЗ 8.3; ADR 0019 §4): the grade on a log axis against Φ⁻¹(P) of its plotting position,
a line per domain — a lognormal population is a straight line, a bend marks a mix of populations (the place
of a top cut).

    probability_line(sample, max_points=2000) -> dict | None
        the points of one sorted sample: {count, nonpositive, weight_sum, min, max, points [[value, z, P %]],
        thinned, r}; None — no value > 0
    probplot_chart(selection, codes=None, labels=None, max_points=2000, tick=None) -> dict
        the answer of `POST /stats/probplot` but the names:
        {weighted, counts, lines [{domain, role, …the keys of `probability_line`}], conventions}
    warm()

A point (ТЗ 8.3): x — the value (the axis is logarithmic), y = Φ⁻¹(P), P = (C − w / 2) / W — the middle of the
cumulative weight of the sample among the values > 0 of its domain (the positions of `weighted_percentiles`
and of the Q-Q plot). Values ≤ 0 have no place on a log axis: they are counted (`nonpositive`,
`counts.nonpositive_log`) and left out; a domain without a value > 0 gives no line.

More than `max_points` (2000) points of a domain are thinned: the samples nearest above even steps of
y = Φ⁻¹(P) between the first and the last point are kept — the least and the greatest value always stay, and
the tails, where the bends are looked for, keep their samples. Every point is a sample of the table: a click
on a bend gives a threshold that is a real grade (the window of the top cut takes it).

`r` — the correlation of ln(value) with y over the points shown: near 1 the domain is lognormal (a straight
line; ≥ 0.999 for a lognormal sample). None for fewer than three points or no spread.

Domains — `domain_stats.group_samples`: one sort for all of them; rows without a domain make a line of their
own (role "none"); without `codes` there is one line of all the rows (role "all").
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.special import ndtri

from .domain_stats import group_samples
from .select import Selection
from .weighted import Sorted, StatsError, Tick

MAX_POINTS = 2000
CONVENTIONS = (
    "Ось X — значение (логарифмическая), ось Y — Φ⁻¹(P), P = (C − w / 2) / W: середина накопленного веса пробы "
    "среди значений больше нуля. Прямая линия — логнормальное распределение; перегиб — смесь совокупностей."
)
_P_MIN, _P_MAX = float(np.nextafter(0.0, 1.0)), float(np.nextafter(1.0, 0.0))  # Φ⁻¹ is finite inside


def probability_line(sample: Sorted, max_points: int = MAX_POINTS) -> dict[str, Any] | None:
    """The points of a sorted sample on the log-probability plot (see the module docstring)."""
    k0 = int(np.searchsorted(sample.v, 0.0, side="right"))  # rows 0 … k0 − 1 have no logarithm
    n = sample.n - k0
    if n <= 0:
        return None
    v, w, cw = sample.v[k0:], sample.w[k0:], sample.cw[k0:]
    below = float(sample.cw[k0 - 1]) if k0 else 0.0
    total = float(cw[-1]) - below
    p = ((cw - below) - 0.5 * w) / total
    np.maximum.accumulate(p, out=p)  # weights below the rounding of the running sum must not turn it back
    z = ndtri(np.clip(p, _P_MIN, _P_MAX))
    thinned = n > max_points
    if thinned:
        steps = np.linspace(z[0], z[-1], max(max_points, 2))
        at = np.unique(np.minimum(np.searchsorted(z, steps, side="left"), n - 1))
        at[0], at[-1] = 0, n - 1
        v, z, p = v[at], z[at], p[at]
    r = math.nan
    if v.shape[0] >= 3:
        with np.errstate(invalid="ignore", divide="ignore"):
            r = float(np.corrcoef(z, np.log(v))[0, 1])
    return {
        "count": n,
        "nonpositive": k0,
        "weight_sum": total,
        "min": float(sample.v[k0]),
        "max": float(sample.v[-1]),
        "points": np.column_stack((v, z, p * 100.0)).tolist(),
        "thinned": thinned,
        "r": r if math.isfinite(r) else None,
    }


def probplot_chart(
    selection: Selection, codes: Any = None, labels: Any = None, *, max_points: int = MAX_POINTS,
    tick: Tick | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The lines of the rows of `selection` by the domains `codes` / `labels` (columns of the rows the
    selection was made of; None — one line of all the rows). StatsError: `no_values`, `no_positive` (no value
    > 0 at all), `too_many_domains`."""
    step = (lambda f: None) if tick is None else tick
    found = group_samples(selection, codes, labels, tick=lambda f: step(0.85 * f))
    lines, dropped = [], 0
    for g in found.groups:
        line = probability_line(g.sample, max_points)
        dropped += g.sample.n - (0 if line is None else line["count"])
        if line is not None:
            lines.append({**g.head(), **line})
    if not lines:
        raise StatsError(
            "no_positive", "Для лог-вероятностного графика нужны значения больше нуля.",
            "Положительных значений в выборке нет: выберите другое поле или правило «половина модуля» для "
            "значений меньше нуля.",
        )  # fmt: skip
    step(1.0)
    return {
        "weighted": selection.weighted,
        "counts": {**found.counts, "nonpositive_log": dropped},
        "lines": lines,
        "conventions": CONVENTIONS,
    }


def warm() -> None:
    """Load Φ⁻¹ and compile the kernels of the sort on a few rows (the `stats` step of the warm-up)."""
    from .select import select_values

    rows = np.array([1.0, 3.0, 2.0, 5.0, 0.0])
    probplot_chart(select_values(rows, rows + 1.0), np.array([0, 1, 0, 0, 1]), ["a", "b"], max_points=2)
