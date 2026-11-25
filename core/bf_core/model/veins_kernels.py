"""The numba loops of the 2D thin-plate surfaces of a vein (`veins_surface.py`; 19.15): φ(r) = r²·ln r = ½·r²·ln r².

    gram(pts, out)                    out[:n, :n] = Φ_ij = φ(‖p_i − p_j‖), symmetric, φ(0) = 0
    evaluate(q, pts, lam, poly)       f8[m]: poly₀ + poly₁·x + poly₂·y + Σ λ_j·φ(‖q − p_j‖) at the points q [m, 2]
    warm()                            compiles both on tiny arrays (the warm-up of the cache, 19.21)

Every loop runs in one fixed order (no `fastmath`, no `prange`): the same bytes for the same arrays on any number of
threads — the patches of a surface go to a pool of Python threads and the kernels let the GIL go (`nogil`). Arrays are
float64, C-ordered and writable (the wrappers copy what they are given: one signature for the cache). This module is
imported inside the functions of `veins_surface`, so a handler of jobs does not pay for numba at import.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from bf_core.jit import njit


@njit(cache=True, nogil=True)
def _gram(pts: np.ndarray, out: np.ndarray) -> None:
    n = pts.shape[0]
    for i in range(n):
        out[i, i] = 0.0
        for j in range(i):
            dx = pts[i, 0] - pts[j, 0]
            dy = pts[i, 1] - pts[j, 1]
            r2 = dx * dx + dy * dy
            v = 0.5 * r2 * math.log(r2) if r2 > 0.0 else 0.0
            out[i, j] = v
            out[j, i] = v


@njit(cache=True, nogil=True)
def _evaluate(q: np.ndarray, pts: np.ndarray, lam: np.ndarray, poly: np.ndarray, out: np.ndarray) -> None:
    m, n = q.shape[0], pts.shape[0]
    for i in range(m):
        x, y = q[i, 0], q[i, 1]
        s = 0.0
        for j in range(n):
            dx = x - pts[j, 0]
            dy = y - pts[j, 1]
            r2 = dx * dx + dy * dy
            if r2 > 0.0:
                s += lam[j] * (0.5 * r2 * math.log(r2))
        out[i] = (poly[0] + poly[1] * x + poly[2] * y) + s


def _f8(a: Any, ndim: int) -> np.ndarray:
    arr = np.array(a, dtype=np.float64, order="C", copy=True)
    if arr.ndim != ndim:
        raise ValueError(f"expected an array of {ndim} dimensions, got {arr.shape}")
    return arr


def gram(pts: Any, out: np.ndarray) -> None:
    """Φ of the points [n, 2] into the upper-left n × n corner of `out` (float64, C-ordered)."""
    p = _f8(pts, 2)
    n = p.shape[0]
    if out.dtype != np.float64 or out.ndim != 2 or out.shape[0] < n or out.shape[1] < n:
        raise ValueError("`out` must be a float64 matrix of at least n × n")
    _gram(p, out)


def evaluate(q: Any, pts: Any, lam: Any, poly: Any) -> np.ndarray:
    """The spline of the points `pts` [n, 2] with the weights `lam` [n] and the polynomial `poly` [3] at `q` [m, 2]."""
    out = np.empty(len(q), dtype=np.float64)
    _evaluate(_f8(q, 2), _f8(pts, 2), _f8(lam, 1), _f8(poly, 1), out)
    return out


def warm() -> None:
    pts = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    out = np.zeros((3, 3))
    gram(pts, out)
    evaluate(pts, pts, np.zeros(3), np.zeros(3))
