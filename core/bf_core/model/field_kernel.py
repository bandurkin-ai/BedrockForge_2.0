"""The numba kernel of the field of the implicit model (ТЗ 8.9.2; ADR 0026, decision 10; Э19, 19.4).

One band of query points against the patches that may cover it (`evaluate.PatchField` finds them):

    f(x) = Σ_j W_j·f_j(x) / Σ_j W_j,   W_j = (1 − r)⁴₊·(4r + 1),  r = ‖x − c_j‖ / R_j,   f_j = Σ_i λ_i·φ(d_i) + P(x)

over the patches with r < 1, in the order of their numbers; every point is the sum of its own patches and sources in a
fixed order, so the bytes of a point do not depend on the other points of the band, on the band, or on the threads
(no `prange`, no `fastmath`; the bands run on Python threads over this `nogil` code, as in `geo/nearest`). A point no
patch reaches (Σ W = 0, a NaN coordinate) is NaN, never zero (8.0.1).

Per-patch data are flat arrays (CSR over the sources): `srct` [3, K] holds the sources relative to the centre of the
patch in the space of its solution (x, y, z rows: contiguous loads for SIMD), `amat[j]` turns `x − c_j` into that
space when `has_a[j]` (the structural anisotropy A_j of 8.9.2; the global A is applied to the points by the caller
before the kernel), `poly[j]` is (c0, cx, cy, cz) of the coordinates divided by `scale[j]`.

This module imports numba: `evaluate.py` imports it inside the function that builds a field, so a job handler that
imports `evaluate` does not load numba in the API process.
"""

from __future__ import annotations

import math

import numpy as np

from bf_core.jit import njit

#: sources per chunk of the buffer of `_patch_sum`: φ of a chunk is computed in a loop without a reduction (SIMD), then
#: summed in a fixed order.
_CHUNK = 256


@njit(cache=True, nogil=True)
def _patch_sum(px: float, py: float, pz: float, srct: np.ndarray, lam: np.ndarray, k0: int, k1: int, kind: int,
               a: float, buf: np.ndarray) -> float:  # fmt: skip
    """Σ_i λ_i·φ(‖p − s_i‖) over the sources k0…k1 of one patch (`srct` [3, K]: x, y, z rows). The terms of a chunk of
    `_CHUNK` sources are written to `buf` by a loop the compiler vectorises (no reduction in it, no `fastmath`: every
    term is the IEEE value of the scalar formula) and added in a FIXED order — four partial sums over the positions
    k mod 4 of the chunk, joined (s0 + s1) + (s2 + s3), chunk after chunk — the same bytes in every run."""
    sx = srct[0]
    sy = srct[1]
    sz = srct[2]
    total = 0.0
    for c0 in range(k0, k1, _CHUNK):
        m = min(_CHUNK, k1 - c0)
        if kind == 0:  # linear φ = −r
            for u in range(m):
                dx = px - sx[c0 + u]
                dy = py - sy[c0 + u]
                dz = pz - sz[c0 + u]
                buf[u] = lam[c0 + u] * (0.0 - math.sqrt(dx * dx + dy * dy + dz * dz))
        elif kind == 1:  # cubic φ = r³
            for u in range(m):
                dx = px - sx[c0 + u]
                dy = py - sy[c0 + u]
                dz = pz - sz[c0 + u]
                r = math.sqrt(dx * dx + dy * dy + dz * dz)
                buf[u] = lam[c0 + u] * (r * r * r)
        else:  # finite radius a: (1 − t)⁴·(4t + 1), t = min(r/a, 1) — exactly 0 from r = a on, as `kernels._phi`
            for u in range(m):
                dx = px - sx[c0 + u]
                dy = py - sy[c0 + u]
                dz = pz - sz[c0 + u]
                t = min(math.sqrt(dx * dx + dy * dy + dz * dz) / a, 1.0)
                w = 1.0 - t
                w2 = w * w
                buf[u] = lam[c0 + u] * (w2 * w2 * (4.0 * t + 1.0))
        s0 = 0.0
        s1 = 0.0
        s2 = 0.0
        s3 = 0.0
        u = 0
        while u + 4 <= m:
            s0 += buf[u]
            s1 += buf[u + 1]
            s2 += buf[u + 2]
            s3 += buf[u + 3]
            u += 4
        while u < m:
            s0 += buf[u]
            u += 1
        total += (s0 + s1) + (s2 + s3)
    return total


@njit(cache=True, nogil=True)
def field_band(q: np.ndarray, out: np.ndarray, cand: np.ndarray, centres: np.ndarray, radii: np.ndarray,
               indptr: np.ndarray, srct: np.ndarray, lam: np.ndarray, poly: np.ndarray, scale: np.ndarray,
               arad: np.ndarray, amat: np.ndarray, has_a: np.ndarray, kind: int) -> None:  # fmt: skip
    """out[i] = f(q[i]) over the candidate patches `cand` (ascending numbers; a superset of the patches that reach
    the band). q [n, 3], out [n] float64; `srct` [3, K]."""
    buf = np.empty(_CHUNK, dtype=np.float64)
    for i in range(q.shape[0]):
        x = q[i, 0]
        y = q[i, 1]
        z = q[i, 2]
        num = 0.0
        den = 0.0
        hit = False
        for t in range(cand.shape[0]):
            j = cand[t]
            px = x - centres[j, 0]
            py = y - centres[j, 1]
            pz = z - centres[j, 2]
            r = math.sqrt(px * px + py * py + pz * pz) / radii[j]
            if not (r < 1.0):  # also a NaN point: it reaches no patch
                continue
            u = 1.0 - r
            u2 = u * u
            w = u2 * u2 * (4.0 * r + 1.0)
            if has_a[j]:
                qx = amat[j, 0, 0] * px + amat[j, 0, 1] * py + amat[j, 0, 2] * pz
                qy = amat[j, 1, 0] * px + amat[j, 1, 1] * py + amat[j, 1, 2] * pz
                qz = amat[j, 2, 0] * px + amat[j, 2, 1] * py + amat[j, 2, 2] * pz
                px = qx
                py = qy
                pz = qz
            s = _patch_sum(px, py, pz, srct, lam, indptr[j], indptr[j + 1], kind, arad[j], buf)
            c = scale[j]
            fj = s + poly[j, 0] + (poly[j, 1] * (px / c) + poly[j, 2] * (py / c) + poly[j, 3] * (pz / c))
            num += w * fj
            den += w
            hit = True
        out[i] = num / den if hit else math.nan


@njit(cache=True, nogil=True)
def count_terms(q: np.ndarray, cand: np.ndarray, centres: np.ndarray, radii: np.ndarray, indptr: np.ndarray) -> int:
    """The number of kernel terms `field_band` sums for the band: Σ over the points of the sizes of the patches that
    reach them (a measure for ns/term)."""
    total = 0
    for i in range(q.shape[0]):
        for t in range(cand.shape[0]):
            j = cand[t]
            px = q[i, 0] - centres[j, 0]
            py = q[i, 1] - centres[j, 1]
            pz = q[i, 2] - centres[j, 2]
            if math.sqrt(px * px + py * py + pz * pz) / radii[j] < 1.0:
                total += indptr[j + 1] - indptr[j]
    return total
