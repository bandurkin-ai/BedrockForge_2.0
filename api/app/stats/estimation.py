"""Inverse distance weighted estimation on a set of locations."""
import numpy as np
from scipy.spatial import cKDTree


def idw(coords, values, targets, k=8, power=2.0, radius=None):
    """Inverse distance weighted estimate at ``targets`` (m, 2).

    Uses the ``k`` nearest data within ``radius`` (default: unlimited); a target
    on top of a datum gets that datum's value (mean of coincident data);
    targets with no data in range are NaN.
    """
    coords = np.asarray(coords, dtype=float)
    values = np.asarray(values, dtype=float)
    targets = np.asarray(targets, dtype=float)
    if coords.ndim != 2 or coords.shape[1] != 2 or targets.ndim != 2 or targets.shape[1] != 2:
        raise ValueError("coords and targets must have shape (n, 2)")
    if coords.shape[0] != values.shape[0] or coords.shape[0] == 0:
        raise ValueError("coords and values must be non-empty and of equal length")
    if k < 1 or power <= 0 or (radius is not None and radius <= 0):
        raise ValueError("require k >= 1, power > 0, radius > 0")
    k = min(k, coords.shape[0])
    dist, idx = cKDTree(coords).query(targets, k=k, distance_upper_bound=np.inf if radius is None else radius)
    dist = dist.reshape(len(targets), -1)
    idx = idx.reshape(len(targets), -1)
    ok = np.isfinite(dist)                       # missing neighbours come back as inf
    safe_idx = np.where(ok, idx, 0)
    zero = ok & (dist < 1e-9)
    w = np.where(ok, 1.0 / np.maximum(dist, 1e-9) ** power, 0.0)
    w = np.where(zero.any(axis=1, keepdims=True), zero.astype(float), w)   # coincident datum wins
    total = w.sum(axis=1)
    est = (w * values[safe_idx]).sum(axis=1) / np.where(total > 0, total, 1.0)
    return np.where(total > 0, est, np.nan)
