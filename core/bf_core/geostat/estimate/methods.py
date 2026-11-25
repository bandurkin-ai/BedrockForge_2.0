"""The point methods of the estimation (ТЗ 8.8.1–8.8.2; ADR 0024 §5–6; Д-27; Э18, 18.7): NN, IDW and the loop over the
blocks of one pass for SK / OK. numba, `cache=True, nogil=True`, no `prange`, no `fastmath`, no BLAS; float64. Every
kernel works on the rows `rows` of a portion (the arrays are those of `search.Neighbours`) and allocates only its
own small scratch buffers (one set per call — one per thread); the operations run in a fixed order, so one input
gives one answer to the bit on any number of threads.

    nn(z, h, idx) -> float                        the value of the nearest sample by (h_s, number): at equal h_s the
                                                  smaller number (ТЗ 8.8.1); `z`, `h`, `idx` — the selected ones
    idw_into(z, h, power, r1, w) -> float         z* = Σ w_i·z_i / Σ w_i, w_i = 1 / h_i^p, h_i = h_s; a sample with
                                                  h_s < 1e−9·r1 makes z* the mean of such samples (as BF `idw`:
                                                  `stats.local.idw`); w[:n] gets the normalized weights (Σ = 1)
    idw(z, h, power, r1) -> (est, weights)        the same, for tests and the inspector
    nn_rows / idw_rows / krige_rows(…)            the loops over the blocks `rows` of a portion → the estimates

A block that the method cannot give (nothing selected, a NaN) gets NaN, not 0 (8.0.1). The status of the kriging
(`krige.KR_*`) is returned per block: 0 — solved; the others — the system of the block is not solvable.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from bf_core.geostat.estimate.krige import (
    KR_OK,
    O_EST,
    O_KE,
    O_KV,
    O_LAGR,
    O_SLOPE,
    O_SNW,
    OUT_N,
    krige,
)
from bf_core.jit import njit

COINCIDENT = 1e-9  # ТЗ 8.8.1: h_s < 1e−9·r1 — the samples of one place
K_EST, K_KV, K_SLOPE, K_KE, K_LAGR, K_SNW = O_EST, O_KV, O_SLOPE, O_KE, O_LAGR, O_SNW
N_COLS = 6  # the columns of the result of a row loop: EST, KV, SLOPE, KE, LAGR, SNW (`krige.OUT`)


@njit(cache=True, nogil=True)
def nn(z: np.ndarray, h: np.ndarray, idx: np.ndarray) -> float:
    """The value of the nearest of the selected samples: the least h_s, at equal h_s the least number; NaN — none."""
    best = -1
    for i in range(z.shape[0]):
        if idx[i] < 0 or math.isnan(h[i]):
            continue
        if best < 0 or h[i] < h[best] or (h[i] == h[best] and idx[i] < idx[best]):
            best = i
    if best < 0:
        return math.nan
    return z[best]


@njit(cache=True, nogil=True)
def idw_into(z: np.ndarray, h: np.ndarray, power: float, r1: float, w: np.ndarray) -> float:
    """IDW of the samples z[:], h[:] (h = h_s); the normalized weights go to w[:n]. NaN — none, or a NaN in the
    input. The sums run in the order of the samples (they come by (h_s, number))."""
    n = z.shape[0]
    if n == 0:
        return math.nan
    tiny = COINCIDENT * r1
    near = 0
    for i in range(n):
        if math.isnan(h[i]) or math.isnan(z[i]):
            for j in range(n):
                w[j] = math.nan
            return math.nan
        if h[i] < tiny:
            near += 1
    if near > 0:  # the samples of one place: their mean
        s = 0.0
        for i in range(n):
            if h[i] < tiny:
                s += z[i]
                w[i] = 1.0
            else:
                w[i] = 0.0
        for i in range(n):
            w[i] = w[i] / near
        return s / near
    sw = 0.0
    sz = 0.0
    for i in range(n):
        wi = 1.0 / h[i] ** power
        w[i] = wi
        sw += wi
        sz += wi * z[i]
    for i in range(n):
        w[i] = w[i] / sw
    return sz / sw


def idw(z: np.ndarray, h: np.ndarray, power: float, r1: float) -> tuple[float, np.ndarray]:
    """`idw_into` with the weights returned (a copy for the caller; tests, the inspector)."""
    zz = np.ascontiguousarray(z, dtype=np.float64)
    hh = np.ascontiguousarray(h, dtype=np.float64)
    w = np.empty(zz.shape[0], dtype=np.float64)
    est = float(idw_into(zz, hh, float(power), float(r1), w))
    return est, w


@njit(cache=True, nogil=True)
def nn_rows(
    idx: np.ndarray,
    h: np.ndarray,
    z: np.ndarray,
    n: np.ndarray,
    rows: np.ndarray,
    cols: np.ndarray,
    status: np.ndarray,
    lam: np.ndarray,
    want_lam: bool,
) -> None:
    """NN of the blocks `rows`: cols[r, K_EST]; the weights — 1 for the nearest. status[r] = 0 (NN has no system)."""
    for t in range(rows.shape[0]):
        r = rows[t]
        k = n[r]
        status[r] = KR_OK
        cols[r, K_EST] = nn(z[r, :k], h[r, :k], idx[r, :k])
        if want_lam:
            best = -1
            for i in range(k):
                if best < 0 or h[r, i] < h[r, best] or (h[r, i] == h[r, best] and idx[r, i] < idx[r, best]):
                    best = i
            for i in range(k):
                lam[r, i] = 1.0 if i == best else 0.0


@njit(cache=True, nogil=True)
def idw_rows(
    h: np.ndarray,
    z: np.ndarray,
    n: np.ndarray,
    rows: np.ndarray,
    power: float,
    r1: float,
    cols: np.ndarray,
    status: np.ndarray,
    lam: np.ndarray,
    want_lam: bool,
) -> None:
    """IDW of the blocks `rows`: cols[r, K_EST]; the normalized weights into lam[r, :n] when asked for."""
    w = np.empty(h.shape[1], dtype=np.float64)
    for t in range(rows.shape[0]):
        r = rows[t]
        k = n[r]
        status[r] = KR_OK
        cols[r, K_EST] = idw_into(z[r, :k], h[r, :k], power, r1, w)
        if want_lam:
            for i in range(k):
                lam[r, i] = w[i]


@njit(cache=True, nogil=True)
def krige_rows(
    kinds: np.ndarray,
    pars: np.ndarray,
    head: np.ndarray,
    local: np.ndarray,
    idx: np.ndarray,
    z: np.ndarray,
    n: np.ndarray,
    centres: np.ndarray,
    rows: np.ndarray,
    offs: np.ndarray,
    bv: float,
    mean: float,
    ok: int,
    cols: np.ndarray,
    status: np.ndarray,
    lam: np.ndarray,
    want_lam: bool,
) -> None:
    """SK (ok = 0) / OK (ok = 1) of the blocks `rows` of a portion: the selected samples `idx[r, :n[r]]` (positions
    in `local`), their values `z[r, :]`, the block at `centres[r]` with the points `offs` and C̄_VV `bv`.
    status[r] — the `krige.KR_*` status; with a status ≠ 0 the row of `cols` is NaN; cols[r, :] = EST, KV, SLOPE,
    KE, LAGR, SNW; lam[r, :n] — the weights when asked for (μ is in cols[r, K_LAGR] for OK)."""
    width = idx.shape[1]
    m = width + 1
    xs = np.empty((width, 3), dtype=np.float64)
    zs = np.empty(width, dtype=np.float64)
    a = np.empty((m, m), dtype=np.float64)
    rhs = np.empty(m, dtype=np.float64)
    sol = np.empty(m, dtype=np.float64)
    out = np.empty(OUT_N, dtype=np.float64)
    for t in range(rows.shape[0]):
        r = rows[t]
        k = n[r]
        for j in range(k):
            p = idx[r, j]
            xs[j, 0] = local[p, 0]
            xs[j, 1] = local[p, 1]
            xs[j, 2] = local[p, 2]
            zs[j] = z[r, j]
        # `krige` gets the types a Python caller gives (int64, float64): int(int16) would be a second specialization
        n64: Any = np.int64(k)
        ok64: Any = np.int64(ok)
        st = krige(kinds, pars, head, xs, n64, zs, centres[r], offs, bv, mean, ok64, a, rhs, sol, out)
        status[r] = st
        if st == KR_OK:
            for c in range(N_COLS):
                cols[r, c] = out[c]
            if want_lam:
                for j in range(k):
                    lam[r, j] = sol[j]
        else:
            for c in range(N_COLS):
                cols[r, c] = math.nan


# --- the warm-up -----------------------------------------------------------------------------------------------------


def warm_methods() -> None:
    """Compiles the row loops (the inputs are the kernel's own writable arrays — one signature; `want_lam` both ways)
    and the point kernels on read-only arrays too."""
    from bf_core.geostat.estimate.cov import cov_model, disc_points
    from bf_core.geostat.variogram.model import Model, Structure

    cov = cov_model(Model(0.1, (Structure("spherical", 0.9, 30.0, 20.0, 10.0, 30.0, 10.0, 5.0),)))
    local = np.array([[0.0, 0.0, 0.0], [5.0, 1.0, 2.0], [-3.0, 4.0, 1.0], [8.0, -6.0, 3.0]])
    idx = np.array([[0, 1, 2, 3], [3, 2, 1, -1]], dtype=np.int32)
    h = np.array([[0.1, 0.2, 0.3, 0.4], [0.2, 0.3, 0.5, math.nan]])
    z = np.array([[1.0, 2.0, 3.0, 2.5], [2.5, 3.0, 2.0, math.nan]])
    n = np.array([4, 3], dtype=np.int16)
    centres = np.array([[1.0, 2.0, 0.5], [0.0, 0.0, 0.0]])
    rows = np.array([0, 1], dtype=np.int64)
    block = disc_points((4.0, 4.0, 2.0), (2, 2, 2), 10.0)
    point = np.zeros((1, 3))
    cols = np.empty((2, N_COLS))
    status = np.empty(2, dtype=np.int64)
    lam = np.empty((2, 4))
    for want in (False, True):
        for ok in (1, 0):
            for offs in (block, point):
                krige_rows(*cov.args, local, idx, z, n, centres, rows, offs, 0.5, 1.5, ok, cols, status, lam, want)
        nn_rows(idx, h, z, n, rows, cols, status, lam, want)
        idw_rows(h, z, n, rows, 2.0, 30.0, cols, status, lam, want)
    for ro in (False, True):
        hh, zz, ii = (_frozen(a) if ro else a for a in (h[0], z[0], idx[0]))
        w = np.empty(4)
        nn(zz, hh, ii)
        idw_into(zz, hh, 2.0, 30.0, w)


def _frozen(a: np.ndarray) -> np.ndarray:
    out = np.array(a)
    out.setflags(write=False)
    return out
