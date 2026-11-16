"""Tiny numba kernels used by the self-test (cache=True exercises the on-disk cache)."""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit


@njit(cache=True)
def pairwise_sum(values: np.ndarray) -> float:
    """Pairwise (cascade) summation of a float64 array."""
    n = values.shape[0]
    if n <= 8:
        s = 0.0
        for i in range(n):
            s += values[i]
        return s
    half = n // 2
    return pairwise_sum(values[:half]) + pairwise_sum(values[half:])


@njit(cache=True)
def signed_volume(vertices: np.ndarray, triangles: np.ndarray) -> float:
    """Volume of a closed mesh by signed tetrahedra from the bbox centre (ТЗ 5.4)."""
    c0 = (vertices[:, 0].min() + vertices[:, 0].max()) * 0.5
    c1 = (vertices[:, 1].min() + vertices[:, 1].max()) * 0.5
    c2 = (vertices[:, 2].min() + vertices[:, 2].max()) * 0.5
    parts = np.empty(triangles.shape[0])
    for t in range(triangles.shape[0]):
        a = vertices[triangles[t, 0]]
        b = vertices[triangles[t, 1]]
        c = vertices[triangles[t, 2]]
        ax, ay, az = a[0] - c0, a[1] - c1, a[2] - c2
        bx, by, bz = b[0] - c0, b[1] - c1, b[2] - c2
        cx, cy, cz = c[0] - c0, c[1] - c1, c[2] - c2
        parts[t] = (ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)) / 6.0
    return pairwise_sum(parts)
