"""Simple and ordinary kriging of one target — a point or a block (ТЗ 8.8.2; ADR 0024 §5; Э18, 18.4). A numba kernel:
the covariance matrix and the right-hand side are built from `cov.py`, the system is solved by LU with partial
pivoting, the outputs of 8.8.2 are computed. float64; no `prange`, no `fastmath`, no BLAS; `cache=True, nogil=True`.
The kernel allocates nothing: every buffer is the caller's (`KrigeWork` makes them); it is meant to be called in a
loop over the blocks of a portion by `kernel.py` (18.7) and by the indicator kriging (18.9).

    SK:  Σ_j λ_j·C_ij = C̄_iV                       z* = m + Σ λ_i·(z_i − m)       KV = C̄_VV − Σ λ_i·C̄_iV
    OK:  Σ_j λ_j·C_ij + μ = C̄_iV,  Σ λ_j = 1       z* = Σ λ_i·z_i                 KV = C̄_VV − Σ λ_i·C̄_iV − μ
    SLOPE b = Σλ·C̄ / (Σλ·C̄ − μ)  (SK: 1)     KE = (C̄_VV − KV) / C̄_VV     LAGR = μ (SK: NaN — no multiplier)
    SNW = Σ of the negative λ_i, signed (≤ 0)

    krige(kinds, pars, head, xyz, n, z, centre, offs, bv, mean, ok, a, rhs, sol, out) -> status
        kinds, pars, head   `*cov_model(model).args`
        xyz [≥ n, 3], z [≥ n]   the first n samples (positions, values); a NaN in them is a NaN, not a zero
        centre [3], offs [N, 3] the target: the centre and the discretization points (`disc_points`); N = 1 — a
                            point (`offs` = [[0, 0, 0]], C̄_iV = C(x_i − x_0), C̄_VV = C(0) with the nugget)
        bv                  C̄_VV: `head[1]` for a point, `BlockCov.vv(size, disc, phi)` for a block
        mean, ok            SK: ok = 0, `mean` = m;  OK: ok = 1 (`mean` is ignored). `ok` is an int, not a bool
        a [≥ m, ≥ m], rhs [≥ m], sol [≥ m], out [≥ OUT_N]   work and result buffers, m = n + ok; filled in full
        out                 EST, KV, SLOPE, KE, LAGR, SNW (`O_*`), `O_PIV` — the least pivot / C(0) of the LU
        sol[:n] = λ, sol[n] = μ (OK)

The status (the return value) — `KR_OK` 0; `KR_SINGULAR` 1 — a pivot of the LU is ≤ 1e−12·C(0) (ТЗ 8.8.2: the
block is not estimated in the pass; for Д-27 it is STATUS 3, «вырожденная система»); `KR_NAN` 2 — a NaN in the
samples, the values, the target or `bv`; `KR_SIZE` 3 — n < 1, a buffer smaller than needed, `ok` not 0 / 1.
With a status ≠ 0 the whole of `out[:O_PIV]` and `sol[:m]` are NaN: a gap is not a zero, nothing is raised.

    apply_weights(sol, n, z, mean, ok) -> z*      the estimate of another z with the same λ (the median IK of 18.9)
    KrigeWork(size=MAX_SYS)                       the buffers; `.run(cov, xyz, z, centre, offs, bv, mean, ok)`, `.lam`
    warm_krige() -> None                          the kernel on writable and on read-only inputs (lesson 17.39б)

The operations run in a fixed order (the rows by index, the first of equal pivots), so one input gives one answer to
the bit, with a clean or a dirty buffer, on one thread or many. A permutation of the samples changes the order of
the sums: the caller fixes the order of the samples (by distance, then by number — 18.6).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from bf_core.geostat.estimate.cov import CovModel, cov_c, cov_pb, disc_points
from bf_core.jit import njit

MAX_SYS = 129  # ТЗ 8.8.2: the system of the LU, n ≤ 129 (n_max = 128 samples with the row of OK)
PIVOT_REL = 1e-12  # a pivot ≤ 1e−12·C(0) — the system is degenerate

KR_OK = 0
KR_SINGULAR = 1
KR_NAN = 2
KR_SIZE = 3

O_EST, O_KV, O_SLOPE, O_KE, O_LAGR, O_SNW, O_PIV = range(7)
OUT_N = 7  # the quantities that are NaN with a status ≠ 0 are out[:O_PIV]


@njit(cache=True, nogil=True)
def _fail(sol: np.ndarray, m: int, out: np.ndarray) -> None:
    for i in range(m):
        sol[i] = math.nan
    for t in range(O_PIV):
        out[t] = math.nan


@njit(cache=True, nogil=True)
def krige(
    kinds: np.ndarray,
    pars: np.ndarray,
    head: np.ndarray,
    xyz: np.ndarray,
    n: int,
    z: np.ndarray,
    centre: np.ndarray,
    offs: np.ndarray,
    bv: float,
    mean: float,
    ok: int,
    a: np.ndarray,
    rhs: np.ndarray,
    sol: np.ndarray,
    out: np.ndarray,
) -> int:
    """SK / OK of one target from the first n samples. See the module text for the arguments and the status."""
    m = n + ok
    if (
        (ok != 0 and ok != 1)
        or n < 1
        or n > xyz.shape[0]
        or n > z.shape[0]
        or m > a.shape[0]
        or m > a.shape[1]
        or m > rhs.shape[0]
        or m > sol.shape[0]
        or out.shape[0] < OUT_N
    ):
        for t in range(min(out.shape[0], OUT_N)):
            out[t] = math.nan
        return KR_SIZE
    out[O_PIV] = math.nan
    bad = math.isnan(bv) or (ok == 0 and math.isnan(mean))
    for t in range(3):
        if math.isnan(centre[t]):
            bad = True
    # the system: C_ij of the points (with the nugget), C̄_iV of the target; the row and the column of OK
    for i in range(n):
        if math.isnan(z[i]):
            bad = True
        for j in range(i + 1):
            c = cov_c(kinds, pars, head, xyz[i, 0] - xyz[j, 0], xyz[i, 1] - xyz[j, 1], xyz[i, 2] - xyz[j, 2])
            if math.isnan(c):
                bad = True
            a[i, j] = c
            a[j, i] = c
        r = cov_pb(kinds, pars, head, xyz[i, 0], xyz[i, 1], xyz[i, 2], centre[0], centre[1], centre[2], offs)
        if math.isnan(r):
            bad = True
        rhs[i] = r
        sol[i] = r
    if ok == 1:
        for i in range(n):
            a[i, n] = 1.0
            a[n, i] = 1.0
        a[n, n] = 0.0
        rhs[n] = 1.0
        sol[n] = 1.0
    if bad:
        _fail(sol, m, out)
        return KR_NAN
    # LU with partial pivoting (the first of equal pivots), the right-hand side goes along
    tol = PIVOT_REL * head[1]
    least = math.inf
    for k in range(m):
        p = k
        best = abs(a[k, k])
        for i in range(k + 1, m):
            v = abs(a[i, k])
            if v > best:
                best = v
                p = i
        if not (best > tol):  # also false for NaN
            _fail(sol, m, out)
            return KR_SINGULAR
        if head[1] > 0.0 and best / head[1] < least:
            least = best / head[1]
        if p != k:
            for j in range(k, m):
                t = a[k, j]
                a[k, j] = a[p, j]
                a[p, j] = t
            t = sol[k]
            sol[k] = sol[p]
            sol[p] = t
        piv = a[k, k]
        for i in range(k + 1, m):
            f = a[i, k] / piv
            if f != 0.0:
                for j in range(k + 1, m):
                    a[i, j] -= f * a[k, j]
                sol[i] -= f * sol[k]
    for i in range(m - 1, -1, -1):
        s = sol[i]
        for j in range(i + 1, m):
            s -= a[i, j] * sol[j]
        sol[i] = s / a[i, i]
    # the outputs
    sc = 0.0  # Σ λ_i·C̄_iV
    snw = 0.0
    est = 0.0
    for i in range(n):
        lam = sol[i]
        sc += lam * rhs[i]
        if lam < 0.0:
            snw += lam
        if ok == 1:
            est += lam * z[i]
        else:
            est += lam * (z[i] - mean)
    if ok == 1:
        mu = sol[n]
        kv = bv - sc - mu
        den = sc - mu
        out[O_SLOPE] = sc / den if den != 0.0 else math.nan
        out[O_LAGR] = mu
    else:
        est += mean
        kv = bv - sc
        out[O_SLOPE] = 1.0
        out[O_LAGR] = math.nan
    out[O_EST] = est
    out[O_KV] = kv
    out[O_KE] = (bv - kv) / bv if bv > 0.0 else math.nan
    out[O_SNW] = snw
    out[O_PIV] = least if least != math.inf else math.nan
    return KR_OK


@njit(cache=True, nogil=True)
def apply_weights(sol: np.ndarray, n: int, z: np.ndarray, mean: float, ok: int) -> float:
    """z* of the values z with the weights sol[:n] of a `krige` call (the same order of the sums). NaN in z — NaN."""
    est = 0.0
    for i in range(n):
        if ok == 1:
            est += sol[i] * z[i]
        else:
            est += sol[i] * (z[i] - mean)
    if ok == 1:
        return est
    return est + mean


# --- the buffers -----------------------------------------------------------------------------------------------------


class KrigeWork:
    """The work buffers of `krige` for systems up to `size`; one per thread. They need no clearing between calls."""

    def __init__(self, size: int = MAX_SYS) -> None:
        self.size = int(size)
        self.a = np.empty((self.size, self.size), dtype=np.float64)
        self.rhs = np.empty(self.size, dtype=np.float64)
        self.sol = np.empty(self.size, dtype=np.float64)
        self.out = np.empty(OUT_N, dtype=np.float64)

    def run(
        self,
        cov: CovModel,
        xyz: Any,
        z: Any,
        centre: Any,
        offs: Any,
        bv: float,
        mean: float = 0.0,
        ok: int = 1,
        n: int | None = None,
    ) -> int:
        """`krige` of the first n samples (all by default); the result is in `.out`, the weights in `.lam(n)`."""
        count = len(z) if n is None else int(n)
        buffers = (self.a, self.rhs, self.sol, self.out)
        return int(krige(*cov.args, xyz, count, z, centre, offs, float(bv), float(mean), int(ok), *buffers))

    def lam(self, n: int) -> np.ndarray:
        return self.sol[:n]

    def mu(self, n: int) -> float:
        return float(self.sol[n])


# --- the warm-up -----------------------------------------------------------------------------------------------------


def _frozen(a: np.ndarray) -> np.ndarray:
    out = np.array(a)
    out.setflags(write=False)
    return out


def warm_krige() -> None:
    """`krige` and `apply_weights` on the combinations of writable / read-only inputs the callers make: everything
    writable; everything read-only; the samples gathered into writable buffers with the cached (read-only) points of
    a block (`BlockCov.points`); the samples read-only (cached columns of `stats.source`) with a writable target. The
    buffers `a, rhs, sol, out` are always the caller's own, writable."""
    from bf_core.geostat.variogram.model import Model, Structure

    from .cov import cov_model

    cov = cov_model(Model(0.1, (Structure("spherical", 0.9, 30.0, 20.0, 10.0, 30.0, 10.0, 5.0),)))
    xyz = np.array([[0.0, 0.0, 0.0], [5.0, 1.0, 2.0], [-3.0, 4.0, 1.0], [8.0, -6.0, 3.0]])
    z = np.array([1.0, 2.0, 3.0, 2.5])
    centre = np.array([1.0, 2.0, 0.5])
    block = disc_points((4.0, 4.0, 2.0), (2, 2, 2), 10.0)
    point = np.zeros((1, 3))
    work = KrigeWork(8)
    # (model, samples, target, points) read-only?
    for ro_cov, ro_samples, ro_target, ro_offs in (
        (False, False, False, False),
        (True, True, True, True),
        (False, False, False, True),
        (False, True, False, False),
    ):
        k, p, h = (_frozen(v) if ro_cov else v for v in cov.args)
        x, zz = (_frozen(v) if ro_samples else v for v in (xyz, z))
        c = _frozen(centre) if ro_target else centre
        for ok in (1, 0):
            for offs in (block, point):
                o = _frozen(offs) if ro_offs else offs
                krige(k, p, h, x, 4, zz, c, o, 0.5, 1.5, ok, work.a, work.rhs, work.sol, work.out)
            apply_weights(work.sol, 4, zz, 1.5, ok)
