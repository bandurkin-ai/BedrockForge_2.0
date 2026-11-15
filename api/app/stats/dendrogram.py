"""Dendrogram of variables: hierarchical clustering by correlation (as Leapfrog / ioGAS show it).

- Correlation: Pearson, or Spearman (Pearson of average ranks); weighted when weights are given
  (e.g. interval length); optionally of ln values (positive values only, as grades are lognormal).
- Missing values: "pairwise" uses, for every pair, the rows where both are present; "complete"
  only rows where every chosen variable is present (one common sample).
- Distance: 1 − r (anti-correlated variables are far apart) or 1 − |r| (strength only).
- Linkage: average (UPGMA, default), complete, single, weighted (WPGMA) — scipy.cluster.hierarchy.
  Ward is not offered: it assumes Euclidean distances, which correlation distances are not.
"""

from __future__ import annotations

import numpy as np
from scipy.cluster.hierarchy import dendrogram, linkage
from scipy.spatial.distance import squareform
from scipy.stats import rankdata

LINKAGES = ("average", "complete", "single", "weighted")
MIN_PAIRS = 3


class DendrogramError(ValueError):
    pass


def weighted_corr(a: np.ndarray, b: np.ndarray, w: np.ndarray) -> float:
    total = w.sum()
    ma, mb = np.sum(w * a) / total, np.sum(w * b) / total
    da, db = a - ma, b - mb
    den = np.sqrt(np.sum(w * da * da) * np.sum(w * db * db))
    return float(np.sum(w * da * db) / den) if den > 0 else float("nan")


def correlation_matrix(values: np.ndarray, weights: np.ndarray, method: str = "pearson",
                       missing: str = "pairwise") -> tuple[np.ndarray, np.ndarray]:
    """values: (rows, k) with NaN for missing. Returns (r, number of rows used) as k × k matrices."""
    rows, k = values.shape
    present = np.isfinite(values)
    if missing == "complete":
        keep = present.all(axis=1)
        values, weights, present = values[keep], weights[keep], present[keep]
    r = np.eye(k)
    n = np.zeros((k, k), dtype=np.int64)
    for i in range(k):
        n[i, i] = int(present[:, i].sum())
        for j in range(i + 1, k):
            both = present[:, i] & present[:, j]
            m = int(both.sum())
            n[i, j] = n[j, i] = m
            if m < MIN_PAIRS:
                r[i, j] = r[j, i] = np.nan
                continue
            a, b, w = values[both, i], values[both, j], weights[both]
            if method == "spearman":
                a, b = rankdata(a), rankdata(b)
            r[i, j] = r[j, i] = weighted_corr(a, b, w)
    return r, n


def cluster(r: np.ndarray, names: list[str], distance: str = "1-r", method: str = "average") -> dict:
    """Linkage and drawing coordinates (scipy's dendrogram layout: leaves at x = 5, 15, 25, ...)."""
    if len(names) < 2:
        raise DendrogramError("Нужно хотя бы два поля")
    if method not in LINKAGES:
        raise DendrogramError(f"Неизвестный способ связи «{method}»")
    if np.isnan(r).any():
        bad = sorted({names[i] for i, j in zip(*np.where(np.isnan(r)))})
        raise DendrogramError(f"Мало общих проб для корреляции: {', '.join(bad)}")
    d = 1 - np.abs(r) if distance == "1-|r|" else 1 - r
    d = np.clip((d + d.T) / 2, 0, 2)
    np.fill_diagonal(d, 0)
    z = linkage(squareform(d, checks=False), method=method)
    tree = dendrogram(z, labels=names, no_plot=True)
    return {
        "linkage": z.tolist(),
        "leaves": tree["ivl"],
        "order": [int(i) for i in tree["leaves"]],
        "icoord": tree["icoord"],
        "dcoord": tree["dcoord"],
        "max_distance": float(z[:, 2].max()),
    }
