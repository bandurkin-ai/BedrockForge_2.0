"""The precision of duplicates by Thompson and Howarth (ТЗ 8.4.2; ADR 0020 §4): the line of the median
difference of the pairs against their mean and the precision P(c) it gives. A pure function of numpy over the
pairs that are taken (`duplicates.select`: both values, none negative, x1 + x2 > 0), in the order of the file.

    thompson_howarth(x1, x2, group=11, c=()) -> ThompsonHowarth
        state      "ok"; "few_pairs" — fewer than `need` pairs; "no_fit" — every group has the same mean
        group      pairs in a group (`th_group`: odd, 5 … 51 — else `QaqcError` `bad_param` {param: th_group})
        need       pairs the method needs: max(50, 2·group) — at least two groups make a line
        a, b       med = a + b·m̄ by least squares over the groups (NaN — no line);  k — the factor K
        m, med, size   of every group: the mean of m, the median of |x1 − x2|, the number of pairs
        precision  ((q, c, P), …): at P10, P50, P90 of the means m of the pairs (q = 10, 50, 90) and at every
                   given `c` (q — None); P — NaN when it is not defined
    k_factor() -> 1 / (√2·Φ⁻¹(0,75)) = 1,0483580825… (`scipy.special.ndtri`; an expression, not a literal)

Method (ADR 0020 §4). m = (x1 + x2) / 2 and d = |x1 − x2| of every pair; the pairs go by m ascending (a stable
sort: equal m keep the order of the file) into groups of `group`, the incomplete last one is added to the one
before it; of a group — the mean of m and the median of d; the line by least squares, every group with the
same weight. σ(c) = K·(a + b·c);  P(c) = 200·σ(c) / c, % — not defined where a + b·c ≤ 0 or c ≤ 0. P10, P50,
P90 of m follow the percentile rule of the project (Hazen). The given `c` — up to 20 numbers > 0.

Numbers: the sums of the least squares are taken about the means of the groups; a = 0 and b = 0,1 on
|x1 − x2| = 0,1·m come out to 1e−12. 150 thousand pairs — about 0.02 s.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .limits import TH_MIN_PAIRS, Limits, QaqcError
from .standards import values_of

PERCENTILES = (10, 50, 90)
MAX_GIVEN = 20  # values of c of a request
_NONE = np.zeros(0)


@dataclass(frozen=True)
class ThompsonHowarth:
    """The line of Thompson and Howarth of the pairs of a type (see the module docstring)."""

    state: str
    group: int
    need: int
    a: float
    b: float
    k: float
    m: np.ndarray
    med: np.ndarray
    size: np.ndarray
    precision: tuple[tuple[int | None, float, float], ...]


def k_factor() -> float:
    """K of σ(c) = K·(a + b·c): the median of |x1 − x2| of a normal error is √2·Φ⁻¹(0,75)·σ."""
    from scipy.special import ndtri

    return 1.0 / (math.sqrt(2.0) * float(ndtri(0.75)))


def _given(c: Any) -> list[float]:
    """The values of c of a request, checked: up to `MAX_GIVEN` numbers > 0."""
    if c is None:
        return []
    ok = isinstance(c, list | tuple) and len(c) <= MAX_GIVEN
    ok = ok and all(not isinstance(v, bool) and isinstance(v, int | float) and math.isfinite(v) and v > 0 for v in c)
    if not ok:
        raise QaqcError("bad_param", f"Содержания для точности P(c) — до {MAX_GIVEN} чисел больше нуля.",
                        "Задайте, например, c = [0.5, 1, 5].", param="c")  # fmt: skip
    return [float(v) for v in c]


def precision_at(c: float, a: float, b: float, k: float) -> float:
    """P(c) = 200·K·(a + b·c) / c, %; NaN — not defined (no line, c ≤ 0, a + b·c ≤ 0)."""
    c, a, b, k = float(c), float(a), float(b), float(k)
    spread = a + b * c
    if not (c > 0.0 and spread > 0.0):
        return math.nan
    try:
        p = 200.0 * k * spread / c
    except OverflowError:
        return math.nan
    return p if math.isfinite(p) else math.nan


def thompson_howarth(x1: Any, x2: Any, group: int = 11, c: Any = ()) -> ThompsonHowarth:
    """The line med = a + b·m̄ of the groups of the pairs and the precision P(c) (see the module docstring)."""
    first = values_of(x1, name="x1")
    n = int(first.shape[0])
    second = values_of(x2, n, "x2")
    size = Limits(th_group=group).th_group
    given = _given(c)
    k = k_factor()
    need = max(TH_MIN_PAIRS, 2 * size)
    if n < need:
        return ThompsonHowarth("few_pairs", size, need, math.nan, math.nan, k, _NONE, _NONE, np.zeros(0, np.int64), ())
    with np.errstate(all="ignore"):
        m, d = 0.5 * (first + second), np.abs(first - second)
        order = np.argsort(m, kind="stable")
        m, d = m[order], d[order]
        count = n // size
        head = (count - 1) * size  # the last group takes what is left: size … 2·size − 1 pairs
        mean = np.concatenate((m[:head].reshape(count - 1, size).mean(axis=1), [m[head:].mean()]))
        med = np.concatenate((np.median(d[:head].reshape(count - 1, size), axis=1), [np.median(d[head:])]))
        sizes = np.full(count, size, dtype=np.int64)
        sizes[-1] = n - head
        mx, my = float(mean.mean()), float(med.mean())
        dx = mean - mx
        sxx = float((dx * dx).sum())
        b = float((dx * (med - my)).sum()) / sxx if sxx > 0.0 else math.nan
        a = my - b * mx
        at = np.percentile(m, PERCENTILES, method="hazen").tolist()
    if not (math.isfinite(a) and math.isfinite(b)):
        return ThompsonHowarth("no_fit", size, need, math.nan, math.nan, k, mean, med, sizes, ())
    places: list[tuple[int | None, float]] = [*zip(PERCENTILES, at, strict=True), *((None, v) for v in given)]
    precision = tuple((q, v, precision_at(v, a, b, k)) for q, v in places)
    return ThompsonHowarth("ok", size, need, a, b, k, mean, med, sizes, precision)
