"""Weighted histogram of a sample field (ТЗ 7.25; ADR 0019 §4) — the port of BF `stats/histogram.py`
`histogram` and of the answer of its route.

    HistogramSpec(log, bin_width, bins, start, end)      the bins of the settings of a chart;
        `HistogramSpec.from_settings(settings)` — StatsError `bad_param` {param} / `too_many_bins`
    histogram(values, weights, spec, tick=None) -> Histogram
        edges (n + 1), counts, weights (Σ w of a bin), width, below / above / nonpositive (count, weight),
        total_count, total_weight; .percent, .cumulative_percent, .as_dict()
    histogram_chart(selection, spec, weighted=None, tick=None) -> dict
        the answer of `POST /stats/histogram` but the names: counts, statistics with the weights and without
        them (one sort), bins, conventions
    warm()

Bins (BF; the conventions of Leapfrog and Micromine):
- half-open [a; b), the last one closed; a value within 1e−12 of a bin below an edge belongs to the bin that
  starts there (0.3 / 0.1 is 2.9999999999999996 in float64 — the sample 0.3 is in [0.3; 0.4));
- linear, or equal in log10 (`log`): then `bin_width` is in log10 units (0.1 — ten bins per decade), the values
  ≤ 0 lie below the range (`below` holds them, `nonpositive` says how many of `below` they are), a `start` or
  an `end` ≤ 0 is not used;
- width: `bin_width`, or the range divided by `bins`, or automatic — a «round» width (1, 2, 2.5, 5 × 10^k) for
  about √n bins within 10 … 60; with a given width or the automatic one the edges are multiples of the width
  unless `start` fixes the first one; `bin_width` wins over `bins`;
- range: `start` … `end`, the minimum and the maximum of the values where not given; values outside are
  counted in `below` and `above`, so Σ over the bins + below + above = the total;
- no more than 1000 bins (`too_many_bins`).

Y of the chart: `counts` (число), `weights` (Σ весов), `percent` = 100 · weight / total (доля %); the running
curve `cumulative_percent` starts from the weight below the range and ends at 100 − the share above it.

Rows without a value or with a weight ≤ 0 take no part (a selection leaves none). One pass of a numba kernel
over blocks of 2**20 rows with `tick` between them, in the order of the rows; the sums of a bin are compensated
(Neumaier). The input may be sorted or not — the bins do not need the sort.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from numbers import Real
from typing import Any

import numpy as np

from bf_core.jit import njit

from .describe import CONVENTIONS as STATS_CONVENTIONS
from .describe import describe_both
from .select import Selection, bad_param, no_values
from .weighted import StatsError, Tick

MAX_BINS = 1000
CONVENTIONS = STATS_CONVENTIONS + " Бины [a; b), последний закрыт."
_BLOCK = 1 << 20  # rows between two calls of `tick`
_EDGE = 1e-12  # of a bin: a value this close below an edge belongs to the bin above (BF)
_CLOSE = 1e-9  # of a bin: a value this close to the last edge closes the last bin (BF)


def _too_many(n: float, param: str) -> StatsError:
    count = f"{int(n)}" if n < 1e9 else "больше миллиарда"
    hint = (
        "Увеличьте ширину бина или сузьте диапазон «от — до»."
        if param == "bin_width"
        else f"Задайте число бинов не больше {MAX_BINS}."
    )
    return StatsError("too_many_bins", f"Слишком много бинов ({count}, максимум {MAX_BINS}).", hint, param=param)


def _number(key: str, value: Any, title: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise bad_param(key, f"{title}: нужно число.", "Введите число или очистите поле.")
    return float(value)


@dataclass(frozen=True)
class HistogramSpec:
    """The bins of a histogram (the keys of `charts.settings`, ADR 0019 §9)."""

    log: bool = False
    bin_width: float | None = None  # data units; log10 units with `log`
    bins: int | None = None
    start: float | None = None
    end: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.log, bool):
            raise bad_param("log", "«Логарифмическая шкала» — это да или нет.", "Передайте true или false.")
        if self.bin_width is not None and not _number("bin_width", self.bin_width, "Ширина бина") > 0.0:
            raise bad_param("bin_width", "Ширина бина должна быть больше нуля.", "Введите ширину больше нуля.")
        if self.bins is not None:
            if isinstance(self.bins, bool) or not isinstance(self.bins, int) or self.bins < 1:
                raise bad_param("bins", "Число бинов — целое от 1.", f"Введите целое число от 1 до {MAX_BINS}.")
            if self.bins > MAX_BINS:
                raise _too_many(self.bins, "bins")
        for key, title in (("start", "Начало диапазона"), ("end", "Конец диапазона")):
            if getattr(self, key) is not None:
                _number(key, getattr(self, key), title)
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise bad_param(
                "end", "Конец диапазона должен быть больше начала.",
                f"Начало — {self.start:g}: задайте конец больше него или очистите одно из полей.",
            )  # fmt: skip

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> HistogramSpec:
        """The bins of the settings of a chart; keys that are absent or None take the defaults."""
        own = {f.name for f in fields(cls)}
        given = {k: v for k, v in settings.items() if k in own and v is not None}
        bins = given.get("bins")
        if isinstance(bins, float) and bins.is_integer():
            given["bins"] = int(bins)
        for key in ("bin_width", "start", "end"):
            if isinstance(given.get(key), int) and not isinstance(given[key], bool):
                given[key] = float(given[key])
        return cls(**given)


@dataclass(frozen=True)
class Tail:
    """What lies outside the range: the number of rows and their weight."""

    count: int
    weight: float

    def as_dict(self) -> dict[str, Any]:
        return {"count": self.count, "weight": self.weight}


@dataclass(frozen=True)
class Histogram:
    """The bins of a sample (see the module docstring)."""

    log: bool
    edges: np.ndarray  # n + 1, in data units
    counts: np.ndarray  # int64
    weights: np.ndarray  # Σ w of a bin
    width: float  # of a bin: data units, log10 units with `log`
    below: Tail
    above: Tail
    nonpositive: Tail  # the part of `below` that has no logarithm (`log` only)

    @property
    def total_count(self) -> int:
        return int(self.counts.sum()) + self.below.count + self.above.count

    @property
    def total_weight(self) -> float:
        return math.fsum(self.weights) + self.below.weight + self.above.weight

    @property
    def percent(self) -> np.ndarray:
        """The share of the total weight in every bin, %."""
        return np.asarray(self.weights / self.total_weight * 100.0)

    @property
    def cumulative_percent(self) -> np.ndarray:
        """The share of the total weight up to the end of every bin, %, with what lies below the range."""
        return np.asarray((self.below.weight + np.cumsum(self.weights)) / self.total_weight * 100.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "edges": self.edges.tolist(),
            "counts": self.counts.tolist(),
            "weights": self.weights.tolist(),
            "percent": self.percent.tolist(),
            "cumulative_percent": self.cumulative_percent.tolist(),
            "width": self.width,
            "below": self.below.as_dict(),
            "above": self.above.as_dict(),
            "nonpositive": self.nonpositive.as_dict(),
        }


# --- kernels -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _span(v, w, has_w, log):  # type: ignore[no-untyped-def]
    """(min, max, rows on the scale, rows with a value and a weight); on a log scale — of the values > 0."""
    lo = np.inf
    hi = -np.inf
    k = 0
    m = 0
    for i in range(v.shape[0]):
        x = v[i]
        if not np.isfinite(x):
            continue
        if has_w and not (w[i] > 0.0 and np.isfinite(w[i])):
            continue
        m += 1
        if log and x <= 0.0:
            continue
        k += 1
        if x < lo:
            lo = x
        if x > hi:
            hi = x
    return lo, hi, k, m


@njit(cache=True, nogil=True)
def _add(sums, comps, j, y):  # type: ignore[no-untyped-def]
    t = sums[j] + y
    if abs(sums[j]) >= abs(y):
        comps[j] += (sums[j] - t) + y
    else:
        comps[j] += (y - t) + sums[j]
    sums[j] = t


@njit(cache=True, nogil=True)
def _fill(v, w, has_w, log, first, width, n, a, b, counts, sums, comps, tn, ts, tc):  # type: ignore[no-untyped-def]
    """Rows a … b − 1 into the bins; tails: 0 — below, 1 — above, 2 — values ≤ 0 on a log scale (part of 0)."""
    last = first + width * n
    for i in range(a, b):
        x = v[i]
        if not np.isfinite(x):
            continue
        y = 1.0
        if has_w:
            y = w[i]
            if not (y > 0.0 and np.isfinite(y)):
                continue
        if log:
            if x <= 0.0:
                tn[2] += 1
                _add(ts, tc, 2, y)
                tn[0] += 1
                _add(ts, tc, 0, y)
                continue
            x = np.log10(x)
        t = (x - first) / width + _EDGE
        if t < 0.0:
            tn[0] += 1
            _add(ts, tc, 0, y)
        elif t >= n:
            if t < n + 1 and abs(x - last) <= width * _CLOSE:  # the last bin is closed
                counts[n - 1] += 1
                _add(sums, comps, n - 1, y)
            else:
                tn[1] += 1
                _add(ts, tc, 1, y)
        else:
            j = int(t)
            counts[j] += 1
            _add(sums, comps, j, y)


# --- the bins ----------------------------------------------------------------------------------------------


def nice_width(span: float, target: int) -> float:
    """The least «round» width (1, 2, 2.5, 5, 10 × 10^k) that covers `span` with `target` bins (BF)."""
    raw = span / max(target, 1)
    if not raw > 0.0 or not math.isfinite(raw):
        return 1.0
    p = 10.0 ** math.floor(math.log10(raw))
    return min((m * p for m in (1.0, 2.0, 2.5, 5.0, 10.0) if m * p >= raw), default=10.0 * p)


def layout(lo: float, hi: float, rows: int, spec: HistogramSpec) -> tuple[float, float, int]:
    """(first edge, width, number of bins) for the values `lo` … `hi` of `rows` rows — on the scale of the
    bins (log10 of the values with `spec.log`)."""
    start, end = spec.start, spec.end
    if spec.log:
        start = math.log10(start) if start is not None and start > 0.0 else None
        end = math.log10(end) if end is not None and end > 0.0 else None
    if start is None and end is not None and end <= lo:
        lo = end - (abs(end) * 0.1 or 1.0)  # the range ends below every value
    lo = lo if start is None else start
    hi = hi if end is None else end
    if hi <= lo:
        hi = lo + (abs(lo) * 0.1 or 1.0)
    if spec.bin_width is not None:
        width = float(spec.bin_width)
    elif spec.bins is not None:
        width = (hi - lo) / spec.bins
    else:
        width = nice_width(hi - lo, int(np.clip(round(math.sqrt(rows)), 10, 60)))
    if not width > 0.0 or not math.isfinite(width):
        raise bad_param("bin_width", "Ширина бина должна быть больше нуля.", "Сузьте диапазон «от — до».")
    # the edges are multiples of the width unless the start or the number of bins fixes the first one
    fixed = start is not None or (spec.bin_width is None and spec.bins is not None)
    if not fixed and not abs(lo / width) < 1e15:  # the multiples of the width cannot be told apart there
        raise _too_many(math.inf, "bin_width")
    first = lo if fixed else math.floor(lo / width + 1e-9) * width
    count = (hi - first) / width - 1e-9
    if not count <= MAX_BINS:  # NaN and inf too
        raise _too_many(math.ceil(count) if math.isfinite(count) else math.inf, "bin_width")
    return first, width, max(math.ceil(count), 1)


def histogram(values: Any, weights: Any, spec: HistogramSpec, *, tick: Tick | None = None) -> Histogram:
    """The bins of `values` with `weights` (None — every row 1) by `spec`; StatsError `no_values` — no rows,
    `no_positive` — a log scale without a value > 0, `too_many_bins`."""
    v = np.ascontiguousarray(values, dtype=np.float64)
    if v.ndim != 1:
        raise ValueError("values must be 1D")
    has_w = weights is not None
    w = np.ascontiguousarray(weights, dtype=np.float64) if has_w else v
    if w.shape != v.shape:
        raise ValueError("values and weights must be of one length")
    lo, hi, on_scale, rows = _span(v, w, has_w, spec.log)
    if rows == 0:
        raise no_values()
    if on_scale == 0:
        raise StatsError(
            "no_positive", "Для логарифмической шкалы нужны значения больше нуля.",
            "Снимите логарифмическую шкалу или исключите значения ≤ 0: положительных значений в выборке нет.",
            param="log",
        )  # fmt: skip
    if spec.log:
        lo, hi = math.log10(lo), math.log10(hi)
    first, width, n = layout(float(lo), float(hi), int(on_scale), spec)
    counts = np.zeros(n, dtype=np.int64)
    sums, comps = np.zeros(n), np.zeros(n)
    tn, ts, tc = np.zeros(3, dtype=np.int64), np.zeros(3), np.zeros(3)
    size = v.shape[0]
    for a in range(0, size, _BLOCK):
        b = min(a + _BLOCK, size)
        _fill(v, w, has_w, spec.log, first, width, n, a, b, counts, sums, comps, tn, ts, tc)
        if tick is not None:
            tick(b / size)
    edges = first + width * np.arange(n + 1)
    tails = [Tail(int(tn[k]), float(ts[k] + tc[k])) for k in range(3)]
    return Histogram(
        spec.log, 10.0**edges if spec.log else edges, counts, sums + comps, width, tails[0], tails[1], tails[2]
    )


def histogram_chart(
    selection: Selection, spec: HistogramSpec, *, weighted: bool | None = None, tick: Tick | None = None
) -> dict[str, Any]:
    """The answer of a histogram for the rows of `selection`: counts (with `nonpositive_log`), `stats` — with
    the weights when `weighted` (None — as the selection was made), then `stats_unweighted` beside them,
    `bins`, `conventions` (the form of BF). One sort; `tick` goes 0 … 0.8 over the sort and to 1 over the bins."""
    selection.require()
    weighted = selection.weighted if weighted is None else weighted
    sample = selection.sort(tick=None if tick is None else lambda f: tick(0.8 * f))
    with_weights, plain = describe_both(sample)
    bins = histogram(sample.v, sample.w, spec, tick=None if tick is None else lambda f: tick(0.8 + 0.2 * f))
    return {
        "log": spec.log,
        "weighted": weighted,
        "counts": {**selection.counts, "nonpositive_log": bins.nonpositive.count},
        "stats": with_weights if weighted else plain,
        "stats_unweighted": plain if weighted else None,
        "bins": bins.as_dict(),
        "conventions": CONVENTIONS,
    }


def warm() -> None:
    """Compile the kernels on a few rows (the `stats` step of the warm-up)."""
    rows = np.array([1.0, 2.0, 0.0, 4.0])
    histogram(rows, rows + 1.0, HistogramSpec())
    histogram(rows, None, HistogramSpec(log=True))
