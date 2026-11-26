"""The radial functions φ of the implicit model and the block Φ of a patch (ТЗ 8.9.2; ADR 0026 §3, решения 7, 10; 19.2).

    linear        φ(r) = −r
    cubic         φ(r) = r³
    wendland      φ(r) = (1 − r/a)⁴₊·(4·r/a + 1)       (finite radius a: an exact 0 for r ≥ a, φ(0) = 1)

    phi(r, kind, a)              φ of an array of distances
    gram(x, kind, a, eta=0, out) the symmetric block Φ_ij = φ(‖x_i − x_j‖) + η·δ_ij of n points [n, 3]
    cross(x, y, kind, a)         the block φ(‖x_i − y_j‖) [n, m]: the sum of the field at the points x of a patch of y

`kind` is a name of `KERNELS` or its code (`LINEAR`, `CUBIC`, `WENDLAND`); `a` matters to `wendland` only. Points are
float64 [n, 3] in the coordinates the caller has already turned (A of 8.9.2, the centre of a patch taken away): a
distance is √(dx² + dy² + dz²) and nothing else — no `fastmath`, no `prange`, the same bytes on any number of
threads. The kernels let the GIL go (`nogil`): the patches are run on a pool of Python threads (`system.solve_patch`).
A NaN coordinate gives NaN, never zero (8.0.1); the callers refuse it before the block is built.

The gradient of φ is not here: the model has no gradient conditions (ADR 0026 «Что вне ТЗ»). The numba kernel and its
cache are loaded with this module only: `system.py` imports it inside the function that builds a block.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from bf_core.jit import njit

LINEAR, CUBIC, WENDLAND = 0, 1, 2
KERNELS: dict[str, int] = {"linear": LINEAR, "cubic": CUBIC, "wendland": WENDLAND}
#: s of φ(R·r) = R^s·φ(r) (linear 1, cubic 3, the finite radius 0 once a is scaled with R): `system` uses it to bring
#: Φ and the polynomial border P to one size without changing the solution.
_HOMOGENEITY = {LINEAR: 1, CUBIC: 3, WENDLAND: 0}


def kernel_code(kind: str | int) -> int:
    """The code of a kernel from its name (`KERNELS`) or a code; ValueError for anything else."""
    if isinstance(kind, str):
        if kind not in KERNELS:
            raise ValueError(f"unknown RBF kernel {kind!r}, expected one of {sorted(KERNELS)}")
        return KERNELS[kind]
    code = int(kind)
    if code not in _HOMOGENEITY:
        raise ValueError(f"unknown RBF kernel code {kind!r}")
    return code


def homogeneity(kind: str | int) -> int:
    """s such that φ(R·r) = R^s·φ(r) for the linear and the cubic kernel; 0 for the finite radius."""
    return _HOMOGENEITY[kernel_code(kind)]


@njit(cache=True, nogil=True)
def _phi(r: float, kind: int, a: float) -> float:
    if kind == 0:
        return 0.0 - r
    if kind == 1:
        return r * r * r
    t = r / a
    if t >= 1.0:
        return 0.0
    u = 1.0 - t
    u2 = u * u
    return u2 * u2 * (4.0 * t + 1.0)


@njit(cache=True, nogil=True)
def _phi_array(r: np.ndarray, kind: int, a: float, out: np.ndarray) -> None:
    for i in range(r.shape[0]):
        out[i] = _phi(r[i], kind, a)


@njit(cache=True, nogil=True)
def _gram(x: np.ndarray, kind: int, a: float, eta: float, out: np.ndarray) -> None:
    n = x.shape[0]
    for i in range(n):
        xi, yi, zi = x[i, 0], x[i, 1], x[i, 2]
        out[i, i] = _phi(0.0 * xi, kind, a) + eta
        for j in range(i + 1, n):
            dx = xi - x[j, 0]
            dy = yi - x[j, 1]
            dz = zi - x[j, 2]
            v = _phi(math.sqrt(dx * dx + dy * dy + dz * dz), kind, a)
            out[i, j] = v
            out[j, i] = v


@njit(cache=True, nogil=True)
def _cross(x: np.ndarray, y: np.ndarray, kind: int, a: float, out: np.ndarray) -> None:
    for i in range(x.shape[0]):
        xi, yi, zi = x[i, 0], x[i, 1], x[i, 2]
        for j in range(y.shape[0]):
            dx = xi - y[j, 0]
            dy = yi - y[j, 1]
            dz = zi - y[j, 2]
            out[i, j] = _phi(math.sqrt(dx * dx + dy * dy + dz * dz), kind, a)


def _points(x: Any) -> np.ndarray:
    """float64 [n, 3], C-ordered and writable: one signature for the kernels, whatever the caller holds
    (read-only, strided, Fortran-ordered, a list)."""
    arr = np.array(x, dtype=np.float64, order="C", copy=True)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError(f"points must have the shape [n, 3], got {arr.shape}")
    return arr


def _radius(kind: int, a: float | None) -> float:
    if kind != WENDLAND:
        return 1.0
    if a is None or not (math.isfinite(a) and a > 0.0):
        raise ValueError("the finite-radius kernel needs a radius a > 0")
    return float(a)


def phi(r: Any, kind: str | int, a: float | None = None) -> np.ndarray:
    """φ of the distances `r` (any shape); float64 of the same shape."""
    code = kernel_code(kind)
    flat = np.array(r, dtype=np.float64, order="C", copy=True).reshape(-1)
    out = np.empty_like(flat)
    _phi_array(flat, code, _radius(code, a), out)
    return out.reshape(np.shape(r))


def gram(
    x: Any, kind: str | int, a: float | None = None, eta: float = 0.0, out: np.ndarray | None = None
) -> np.ndarray:
    """The block Φ + η·I of the points `x` [n, 3] (symmetric, copied across the diagonal). With `out` — a float64
    C-ordered array of at least n × n — the block is written to its upper-left corner and `out` is returned (the
    caller keeps the border of the polynomial there); the rest of `out` is not touched."""
    code = kernel_code(kind)
    pts = _points(x)
    n = pts.shape[0]
    if out is None:
        out = np.empty((n, n), dtype=np.float64)
    elif out.dtype != np.float64 or out.ndim != 2 or out.shape[0] < n or out.shape[1] < n:
        raise ValueError("`out` must be a float64 matrix of at least n × n")
    _gram(pts, code, _radius(code, a), float(eta), out)
    return out


def cross(x: Any, y: Any, kind: str | int, a: float | None = None) -> np.ndarray:
    """The block φ(‖x_i − y_j‖), [len(x), len(y)]."""
    code = kernel_code(kind)
    px, py = _points(x), _points(y)
    out = np.empty((px.shape[0], py.shape[0]), dtype=np.float64)
    _cross(px, py, code, _radius(code, a), out)
    return out
