"""Weighted statistics of a sample (ADR 0018 §7): the percentile rule of BF and a sample sorted once with its
running sums. The top cut (Э12) is built on it; the histogram, Q-Q and the log-probability plot of Э13 take the
same functions.

    weighted_percentiles(values, weights, ps) -> float64[len(ps)]
        BF `stats/histogram.py` (GSLIB): the sorted values sit at the middle of their cumulative weight,
        P_i = (C_i − w_i / 2) / W, linear in between, the first and the last value outside. Without weights
        it is the Hazen rule (numpy method="hazen"). Equal values keep the order of the rows.
    sort_groups(values, weights, codes=None, n_groups=1, tick=None) -> list[Sorted]
        one sort for all the groups (domains): group k holds the rows of code k, ascending by value
    Sorted: v, w, cw = Σ w, cm = Σ w·v, cq = Σ w·(v − pivot)² up to and including a row
        .n .total .metal .mean .variance .cv .vmin .vmax
        .percentiles(ps)   the rule above
        .capped(t)         the sample with every value above t taken as t: mean, var, cv, n_cut, loss, vmax
        .metal_at(q)       Σ w·v of the lowest share q of the total weight (a row is split by weight)
        .value_at(q, upper=False)   the value of the row that holds the share q (the bounds of a slice)
    pair_sums(a, b) -> (Σ a, Σ a·b)   pairwise, in the order of the rows (ТЗ 8.0.1)
    warm()                            compiles the kernels (the `composites` step of the warm-up)

Rows without a value (NaN), without a weight or with a weight ≤ 0 take no part (BF: «samples with a missing or
non-positive weight are left out»); a missing value never becomes 0. Variance is the population one,
Σ w·(v − m)² / W; CV = σ / m (NaN when m ≤ 0).

Numbers. The running sums are compensated (Neumaier), in the order of the sorted rows, so a difference of two
of them keeps ~1e−15 of the total; squares are taken about the median value — the variance of values far from
zero loses nothing. One thread, a fixed order: the same bits on any machine and with any number of threads.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.jit import njit

Tick = Callable[[float], None]
_DEPTH = 60  # levels of the cascade of `pair_sums`: 8 · 2**60 rows


class StatsError(ValueError):
    """A statistic cannot be computed: {code, message, hint} in Russian, shown to the user (API — 422). The
    same shape as `composites.intervals.CompositeError`; `param` — the key of the parameter at fault."""

    def __init__(self, code: str, message: str, hint: str = "", *, param: str | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.hint, self.param = code, message, hint, param

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


# --- kernels -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def pair_sums(a, b):  # type: ignore[no-untyped-def]
    """(Σ a, Σ a·b): blocks of 8 rows left to right, the blocks combined as a binary counter (pairwise)."""
    st = np.zeros((_DEPTH, 2))
    depth = 0
    s0 = 0.0
    s1 = 0.0
    for i in range(a.shape[0]):
        s0 += a[i]
        s1 += a[i] * b[i]
        k = i + 1
        if (k & 7) == 0:
            m = k >> 3
            while (m & 1) == 0:
                depth -= 1
                s0 = st[depth, 0] + s0
                s1 = st[depth, 1] + s1
                m >>= 1
            st[depth, 0] = s0
            st[depth, 1] = s1
            s0 = 0.0
            s1 = 0.0
            depth += 1
    while depth > 0:
        depth -= 1
        s0 = st[depth, 0] + s0
        s1 = st[depth, 1] + s1
    return s0, s1


@njit(cache=True, nogil=True)
def _fix_ties(v, order):  # type: ignore[no-untyped-def]
    """Rows of equal value go in the order of the rows — what a stable sort gives, whatever sort made `order`."""
    n = order.shape[0]
    a = 0
    while a < n:
        x = v[order[a]]
        b = a + 1
        while b < n and v[order[b]] == x:
            b += 1
        if b - a > 1:
            order[a:b].sort()
        a = b


@njit(cache=True, nogil=True)
def _scatter(order, v, w, codes, starts, ov, ow):  # type: ignore[no-untyped-def]
    """The rows in sorted order into the stretch of their group (a counting sort: the order is kept)."""
    fill = starts[:-1].copy()
    for j in range(order.shape[0]):
        i = order[j]
        g = codes[i]
        p = fill[g]
        ov[p] = v[i]
        ow[p] = w[i]
        fill[g] = p + 1


@njit(cache=True, nogil=True)
def _prefix(starts, v, w, cw, cm, cq, pivots):  # type: ignore[no-untyped-def]
    """Running sums of every group: Σ w, Σ w·v, Σ w·(v − pivot)², compensated; pivot — the median row."""
    for g in range(starts.shape[0] - 1):
        a = starts[g]
        b = starts[g + 1]
        if b <= a:
            continue
        pv = v[a + (b - a) // 2]
        pivots[g] = pv
        s0 = 0.0
        c0 = 0.0
        s1 = 0.0
        c1 = 0.0
        s2 = 0.0
        c2 = 0.0
        for i in range(a, b):
            x = w[i]
            t = s0 + x
            if abs(s0) >= abs(x):
                c0 += (s0 - t) + x
            else:
                c0 += (x - t) + s0
            s0 = t
            x = w[i] * v[i]
            t = s1 + x
            if abs(s1) >= abs(x):
                c1 += (s1 - t) + x
            else:
                c1 += (x - t) + s1
            s1 = t
            d = v[i] - pv
            x = w[i] * d * d
            t = s2 + x
            if abs(s2) >= abs(x):
                c2 += (s2 - t) + x
            else:
                c2 += (x - t) + s2
            s2 = t
            cw[i] = s0 + c0
            cm[i] = s1 + c1
            cq[i] = s2 + c2


# --- a sorted sample ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Capped:
    """A sample with the values above a threshold taken as the threshold; arrays over the thresholds."""

    mean: np.ndarray  # Σ w·min(v, t) / W
    var: np.ndarray  # the population variance of min(v, t)
    cv: np.ndarray  # σ / mean of min(v, t); NaN when the mean ≤ 0
    n_cut: np.ndarray  # int64: rows with v > t
    loss: np.ndarray  # 100·Σ w·(v − min(v, t)) / Σ w·v, %; NaN when Σ w·v ≤ 0
    vmax: np.ndarray  # min(max v, t)


@dataclass(frozen=True)
class Sorted:
    """The rows of one group ascending by value, with their running sums (see the module docstring)."""

    v: np.ndarray
    w: np.ndarray
    cw: np.ndarray
    cm: np.ndarray
    cq: np.ndarray
    pivot: float

    @property
    def n(self) -> int:
        return int(self.v.shape[0])

    @property
    def total(self) -> float:
        return float(self.cw[-1]) if self.n else 0.0

    @property
    def metal(self) -> float:
        return float(self.cm[-1]) if self.n else 0.0

    @property
    def mean(self) -> float:
        return self.metal / self.total if self.n else float("nan")

    @property
    def variance(self) -> float:
        if not self.n:
            return float("nan")
        return max(0.0, float(self.cq[-1]) / self.total - (self.mean - self.pivot) ** 2)

    @property
    def cv(self) -> float:
        m = self.mean
        return float(np.sqrt(self.variance)) / m if self.n and m > 0.0 else float("nan")

    @property
    def vmin(self) -> float:
        return float(self.v[0]) if self.n else float("nan")

    @property
    def vmax(self) -> float:
        return float(self.v[-1]) if self.n else float("nan")

    def percentiles(self, ps: Any) -> np.ndarray:
        """The percentiles `ps` (0–100) by the rule of BF; NaN when the group is empty."""
        p = np.atleast_1d(np.asarray(ps, dtype=np.float64))
        if not self.n:
            return np.full(p.shape, np.nan)
        pos = (self.cw - 0.5 * self.w) / self.total
        return np.asarray(np.interp(p / 100.0, pos, self.v), dtype=np.float64)

    def _head(self, k: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Σ w, Σ w·v, Σ w·(v − pivot)² of the first k rows."""
        j = np.maximum(k - 1, 0)
        some = k > 0
        return np.where(some, self.cw[j], 0.0), np.where(some, self.cm[j], 0.0), np.where(some, self.cq[j], 0.0)

    def capped(self, t: Any) -> Capped:
        """Statistics of min(v, t) for every threshold of `t` (NaN or +inf — nothing is cut)."""
        thr = np.atleast_1d(np.asarray(t, dtype=np.float64))
        thr = np.where(np.isnan(thr), np.inf, thr)
        if not self.n:
            nan = np.full(thr.shape, np.nan)
            return Capped(nan, nan, nan, np.zeros(thr.shape, dtype=np.int64), nan, nan)
        k = np.searchsorted(self.v, thr, side="right")  # rows with v ≤ t
        hw, hm, hq = self._head(k)
        total, metal = self.total, self.metal
        rest = np.where(k < self.n, total - hw, 0.0)
        cap = np.where(k < self.n, thr, 0.0)  # +inf never meets a rest of 0
        mean = (hm + cap * rest) / total
        d = cap - self.pivot
        var = np.maximum((hq + d * d * rest) / total - (mean - self.pivot) ** 2, 0.0)
        lost = np.maximum((metal - hm) - cap * rest, 0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            cv = np.where(mean > 0.0, np.sqrt(var) / mean, np.nan)
            loss = 100.0 * lost / metal if metal > 0.0 else np.full(thr.shape, np.nan)
        return Capped(mean, var, cv, (self.n - k).astype(np.int64), loss, np.minimum(self.vmax, thr))

    def _row_at(self, q: Any) -> tuple[np.ndarray, np.ndarray]:
        """The cumulative weight x = q·W and the row that holds it (the last row for q = 1)."""
        x = np.clip(np.atleast_1d(np.asarray(q, dtype=np.float64)), 0.0, 1.0) * self.total
        return x, np.minimum(np.searchsorted(self.cw, x, side="right"), self.n - 1)

    def metal_at(self, q: Any) -> np.ndarray:
        """Σ w·v of the lowest share `q` (0–1) of the total weight; the row at the boundary is split by weight."""
        if not self.n:
            return np.full(np.atleast_1d(np.asarray(q)).shape, np.nan)
        x, i = self._row_at(q)
        hw, hm, _ = self._head(i)
        return np.asarray(hm + (x - hw) * self.v[i], dtype=np.float64)

    def value_at(self, q: Any, upper: bool = False) -> np.ndarray:
        """The value of the row that holds the share `q` (0–1) of the total weight; at a boundary between two
        rows — the higher one, with `upper` (the top of a slice) — the lower one (1e−12 of the total decides)."""
        if not self.n:
            return np.full(np.atleast_1d(np.asarray(q)).shape, np.nan)
        x = np.clip(np.atleast_1d(np.asarray(q, dtype=np.float64)), 0.0, 1.0) * self.total
        eps = 1e-12 * self.total
        i = np.searchsorted(self.cw, x - eps, side="left") if upper else np.searchsorted(self.cw, x + eps, side="right")
        return np.asarray(self.v[np.minimum(i, self.n - 1)], dtype=np.float64)


_NONE = np.zeros(0, dtype=np.float64)
EMPTY = Sorted(_NONE, _NONE, _NONE, _NONE, _NONE, float("nan"))


def sort_groups(
    values: Any, weights: Any, codes: Any = None, n_groups: int = 1, *, tick: Tick | None = None
) -> list[Sorted]:
    """The rows that have a value and a positive weight, sorted once and split by `codes` (0 … n_groups − 1;
    None — one group; a code outside — the row takes no part). `tick(fraction)` is called between the steps
    and may raise to cancel."""
    v = np.ascontiguousarray(values, dtype=np.float64)
    w = np.ascontiguousarray(weights, dtype=np.float64)
    if v.ndim != 1 or w.shape != v.shape:
        raise ValueError("values and weights must be 1D of one length")
    keep = np.isfinite(v) & np.isfinite(w) & (w > 0.0)
    c = None
    if codes is not None:
        c = np.ascontiguousarray(codes, dtype=np.int64)
        if c.shape != v.shape:
            raise ValueError("codes must have the length of values")
        keep &= (c >= 0) & (c < n_groups)
    else:
        n_groups = 1
    if not keep.all():
        rows = np.flatnonzero(keep)
        v, w = v[rows], w[rows]
        c = None if c is None else c[rows]
    step = (lambda f: None) if tick is None else tick
    step(0.05)
    order = np.argsort(v)  # any sort: the ties are put in the order of the rows next
    step(0.55)
    _fix_ties(v, order)
    step(0.65)
    n = v.shape[0]
    if c is None:
        starts = np.array([0, n], dtype=np.int64)
        ov, ow = v[order], w[order]
    else:
        starts = np.zeros(n_groups + 1, dtype=np.int64)
        np.cumsum(np.bincount(c, minlength=n_groups), out=starts[1:])
        ov, ow = np.empty(n), np.empty(n)
        _scatter(order, v, w, c, starts, ov, ow)
    step(0.85)
    cw, cm, cq = np.empty(n), np.empty(n), np.empty(n)
    pivots = np.full(n_groups, np.nan)
    _prefix(starts, ov, ow, cw, cm, cq, pivots)
    step(1.0)
    out = []
    for g in range(n_groups):
        a, b = int(starts[g]), int(starts[g + 1])
        out.append(Sorted(ov[a:b], ow[a:b], cw[a:b], cm[a:b], cq[a:b], float(pivots[g])) if b > a else EMPTY)
    return out


def weighted_percentiles(values: Any, weights: Any, ps: Any) -> np.ndarray:
    """Percentiles `ps` (0–100) of weighted data by the rule of BF (see the module docstring); `weights` None —
    equal weights. Rows without a value or with a weight ≤ 0 take no part; nothing left — NaN."""
    v = np.asarray(values, dtype=np.float64)
    w = np.ones(v.shape) if weights is None else weights
    return sort_groups(v, w)[0].percentiles(ps)


def warm() -> None:
    """Compile the kernels on a few rows (first compilation — in the background)."""
    rows = np.array([3.0, 1.0, 1.0, 2.0])
    sort_groups(rows, rows, np.array([0, 1, 0, 1]), 2)
    pair_sums(rows, rows)
