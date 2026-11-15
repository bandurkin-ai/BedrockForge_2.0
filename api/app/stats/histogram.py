"""Weighted histogram and descriptive statistics of a sample variable.

Conventions (shared by Leapfrog and Micromine, and stated in the response so they are visible):

- Weights: none (every sample 1), the interval length, another field, or length × field (e.g.
  density: a tonnage weight). Samples with a missing or non-positive weight are left out.
- Percentiles of weighted data (GSLIB convention): the sorted values are placed at the middle of
  their cumulative weight, P = (C_i − w_i / 2) / W, and interpolated linearly; without weights
  this is the Hazen definition (numpy method="hazen"). With integer weights it is NOT the same
  as repeating samples (mean and variance are).
- Variance is the population (weighted) variance Σw(v−m)²/W; CV = σ / m.
- Bins are half-open [a, b), the last one closed. Logarithmic bins are equal in log10.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

PERCENTILES = (5, 10, 25, 50, 75, 90, 95, 97.5, 99)
MAX_BINS = 1000


class HistogramError(ValueError):
    pass


def weighted_percentiles(values: np.ndarray, weights: np.ndarray, ps) -> list[float]:
    order = np.argsort(values, kind="stable")
    v, w = values[order], weights[order]
    c = np.cumsum(w)
    total = c[-1]
    pos = (c - w / 2) / total
    return [float(np.interp(p / 100.0, pos, v)) for p in ps]


def describe(values: np.ndarray, weights: np.ndarray | None = None) -> dict:
    """Descriptive statistics; `weights` None means equal weights."""
    v = np.asarray(values, dtype=np.float64)
    n = len(v)
    if n == 0:
        return {"count": 0}
    w = np.ones(n) if weights is None else np.asarray(weights, dtype=np.float64)
    total = float(w.sum())
    mean = float(np.sum(w * v) / total)
    var = float(np.sum(w * (v - mean) ** 2) / total)
    std = math.sqrt(var)
    pct = weighted_percentiles(v, w, PERCENTILES)
    out = {
        "count": n,
        "weight_sum": total,
        "min": float(v.min()),
        "max": float(v.max()),
        "mean": mean,
        "variance": var,
        "std": std,
        "cv": std / mean if mean != 0 else None,
        "skewness": float(np.sum(w * (v - mean) ** 3) / total / std**3) if std > 0 else None,
        "median": pct[PERCENTILES.index(50)],
        "percentiles": {str(p): q for p, q in zip(PERCENTILES, pct)},
    }
    positive = v > 0
    if positive.any():
        lv, lw = np.log(v[positive]), w[positive]
        lt = float(lw.sum())
        lmean = float(np.sum(lw * lv) / lt)
        out["log"] = {
            "count": int(positive.sum()),
            "mean_ln": lmean,
            "variance_ln": float(np.sum(lw * (lv - lmean) ** 2) / lt),
            "geometric_mean": math.exp(lmean),
        }
    return out


@dataclass
class HistogramSpec:
    log: bool = False
    # Linear: bin width in data units; log: in log10 units (0.1 = ten bins per decade).
    bin_width: float | None = None
    bins: int | None = None
    start: float | None = None
    end: float | None = None


@dataclass
class HistogramResult:
    edges: list[float]
    weights: list[float]
    counts: list[int]
    below: dict = field(default_factory=dict)
    above: dict = field(default_factory=dict)
    bin_width: float = 0.0


def _nice_width(span: float, target: int) -> float:
    raw = span / max(target, 1)
    if raw <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(raw))
    return min((m * p for m in (1, 2, 2.5, 5, 10) if m * p >= raw), default=10 * p)


def histogram(values: np.ndarray, weights: np.ndarray, spec: HistogramSpec) -> HistogramResult:
    v = np.asarray(values, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    if spec.log:
        keep = v > 0
        if not keep.any():
            raise HistogramError("Для логарифмической шкалы нужны положительные значения")
        x, wx = np.log10(v[keep]), w[keep]
        start = math.log10(spec.start) if spec.start and spec.start > 0 else None
        end = math.log10(spec.end) if spec.end and spec.end > 0 else None
    else:
        x, wx = v, w
        start, end = spec.start, spec.end
    lo = float(x.min()) if start is None else start
    hi = float(x.max()) if end is None else end
    if hi <= lo:
        hi = lo + (abs(lo) * 0.1 or 1.0)
    if spec.bin_width:
        width = float(spec.bin_width)
    else:
        target = spec.bins or int(np.clip(round(math.sqrt(len(x))), 10, 60))
        width = (hi - lo) / target if spec.bins else _nice_width(hi - lo, target)
    if width <= 0:
        raise HistogramError("Ширина бина должна быть больше нуля")
    # Edges on multiples of the width unless the user fixed the start (as Leapfrog does).
    first = lo if start is not None or spec.bins else math.floor(lo / width + 1e-9) * width
    n = int(math.ceil((hi - first) / width - 1e-9))
    n = max(n, 1)
    if n > MAX_BINS:
        raise HistogramError(f"Слишком много бинов ({n}, максимум {MAX_BINS}): увеличьте ширину")
    edges = first + width * np.arange(n + 1)
    idx = np.floor((x - first) / width + 1e-12).astype(np.int64)
    idx[np.isclose(x, edges[-1], rtol=0, atol=width * 1e-9) & (idx == n)] = n - 1  # last bin is closed
    inside = (idx >= 0) & (idx < n)
    counts = np.bincount(idx[inside], minlength=n)
    sums = np.bincount(idx[inside], weights=wx[inside], minlength=n)
    below, above = idx < 0, idx >= n
    out_edges = (10 ** edges) if spec.log else edges
    return HistogramResult(
        edges=out_edges.tolist(), weights=sums.tolist(), counts=counts.astype(int).tolist(),
        below={"count": int(below.sum()), "weight": float(wx[below].sum())},
        above={"count": int(above.sum()), "weight": float(wx[above].sum())},
        bin_width=width,
    )
