"""Q-Q plots: quantiles of one sample against another sample or a fitted distribution.

- Sample against sample: the quantiles of both at the same probabilities p_k = k / (n + 1),
  weighted by the GSLIB convention (stats/histogram.weighted_percentiles). Points on the 1:1 line
  mean equal distributions; a parallel offset is a shift, a different slope a scale change.
  Also the Kolmogorov–Smirnov distance D = max |F_x − F_y| of the weighted distributions.
- Sample against a distribution: every value (thinned to at most MAX_POINTS by interpolating at
  evenly spaced probabilities) at its plotting position P = (C_i − w_i/2)/W, against the normal or
  lognormal quantile with the sample's weighted mean and standard deviation (of ln for lognormal).
  PPCC — the correlation of the points — near 1 means the distribution fits.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.special import ndtri

from app.stats.histogram import weighted_percentiles

MAX_POINTS = 2000


class QQError(ValueError):
    pass


def probabilities(n: int) -> np.ndarray:
    """n probabilities k / (n + 1), in percent."""
    return np.arange(1, n + 1) / (n + 1) * 100.0


def sample_vs_sample(x: np.ndarray, wx: np.ndarray, y: np.ndarray, wy: np.ndarray, n: int) -> dict:
    ps = probabilities(n)
    qx = weighted_percentiles(x, wx, ps)
    qy = weighted_percentiles(y, wy, ps)
    return {
        "points": [[a, b, p] for a, b, p in zip(qx, qy, ps.tolist())],
        "extremes": {"x": [float(x.min()), float(x.max())], "y": [float(y.min()), float(y.max())]},
        "ks": ks_distance(x, wx, y, wy),
    }


def _ecdf(v: np.ndarray, w: np.ndarray, at: np.ndarray) -> np.ndarray:
    order = np.argsort(v, kind="stable")
    sv, cw = v[order], np.cumsum(w[order])
    idx = np.searchsorted(sv, at, side="right")
    out = np.zeros(len(at))
    out[idx > 0] = cw[idx[idx > 0] - 1] / cw[-1]
    return out


def ks_distance(x: np.ndarray, wx: np.ndarray, y: np.ndarray, wy: np.ndarray) -> float:
    """Largest gap between the weighted distribution functions (two-sample Kolmogorov–Smirnov D)."""
    grid = np.union1d(x, y)
    return float(np.max(np.abs(_ecdf(x, wx, grid) - _ecdf(y, wy, grid))))


def sample_vs_distribution(v: np.ndarray, w: np.ndarray, dist: str) -> dict:
    """x: theoretical quantile, y: the sample's value, at the sample's plotting positions."""
    if dist == "lognormal":
        positive = v > 0
        if positive.sum() < 2:
            raise QQError("Для логнормального распределения нужны хотя бы два положительных значения")
        dropped = int((~positive).sum())
        v, w = v[positive], w[positive]
        t = np.log(v)
    elif dist == "normal":
        dropped = 0
        t = v
    else:
        raise QQError(f"Неизвестное распределение «{dist}»")
    if len(v) < 2:
        raise QQError("Нужно хотя бы два значения")
    total = w.sum()
    mu = float(np.sum(w * t) / total)
    sigma = math.sqrt(float(np.sum(w * (t - mu) ** 2) / total))
    if sigma == 0:
        raise QQError("Все значения одинаковы: распределение не подобрать")
    order = np.argsort(v, kind="stable")
    sv, sw = v[order], w[order]
    p = (np.cumsum(sw) - sw / 2) / total
    if len(sv) > MAX_POINTS:
        # Thinned at evenly spaced probabilities; the end values are kept as they are.
        grid = np.linspace(p[0], p[-1], MAX_POINTS)
        sv, p = np.interp(grid, p, sv), grid
    z = ndtri(p)
    theory = np.exp(mu + sigma * z) if dist == "lognormal" else mu + sigma * z
    data = np.log(sv) if dist == "lognormal" else sv
    ppcc = float(np.corrcoef(z, data)[0, 1]) if len(z) > 2 else None
    return {
        "points": [[a, b, c * 100] for a, b, c in zip(theory.tolist(), sv.tolist(), p.tolist())],
        "distribution": {"name": dist, "mu": mu, "sigma": sigma, "dropped_nonpositive": dropped},
        "ppcc": ppcc,
    }
