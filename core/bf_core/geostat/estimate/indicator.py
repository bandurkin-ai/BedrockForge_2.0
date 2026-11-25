"""Indicator kriging (ТЗ 8.8.1; ADR 0024 §6; Э18, 18.9): the probabilities P_k = F(z_k) of a block, the E-type.

    i_k(x) = [z(x) ≤ t_k]                    a gap stays a gap (NaN), not a 0
    F_k*   = Σ λ_i^(k)·i_k(x_i)               OK of the indicators (`krige.krige`, ok = 1), one LU per threshold:
                                              models "per" — the model of the indicator k; "median" — one model,
                                              one LU for all the thresholds, F_k = `apply_weights` of the same λ
    P_k    = order_relations(F*)              GSLIB: clip to [0; 1], the mean of the upward correction (running max)
                                              and the downward one (running min from above) — non-decreasing, ∈ [0; 1]
    ETYPE  = Σ_{j=0..K} (P_j − P_(j−1))·m_j   P_(−1) = 0, P_K = 1 (the class K above the last threshold), m_j — the
                                              mean of the class j: class 0 is z ≤ t_0, class j is (t_(j−1); t_j],
                                              class K is z > t_(K−1); `class_means`

The scale of the variograms is `z` (an indicator model, `indicator:<t>` of 18.10; Д-38); the model of a threshold is
that of its indicator, with its own sill p(1 − p) and nugget.

THE POINT OF CONNECTION (18.7 / 18.12 / 18.33). Per portion of blocks and per pass of the search:

    setup = IkSetup.of(thresholds, covs, class_means, size=(dx, dy, dz), disc=(nx, ny, nz), phi=rotation_deg)
    nb    = searcher.gather(centres, k)                      # local coordinates, as everywhere
    res   = ik_chunk(nb, samples.local, centres, setup)      # P [m, K], etype [m], status i1 [m]: 0 / 2 / 3, raw
    # the blocks with res.status == 3 (degenerate) and == 2 (nb.ok False) go on to the next pass: gather(todo=…)

or all the passes at once (`todo` and the merge of the fields are done here): `res = ik_passes(searcher, samples.local,
centres, setup)` -> `IkRun(P, etype, status, pass_, nb)` — `nb` is the merged `Neighbours` of the passes that held
(NS, NH, NHY, NOCT, AD, DMIN, PASS of the fields of §2.1). The portion has one block size (one `offs`, one C̄_VV): the
caller splits the blocks of other sizes (`sub` = "own") into portions of one size. STATUS is Д-27: 0 — estimated; 2 —
the search did not hold (`nb.ok` False) or no finite sample; 3 — the system is degenerate (a pivot ≤ 1e−12·C(0)) in
every pass where there were enough samples; the caller adds 1 («вне домена»). With STATUS ≠ 0 the P, ETYPE are NaN.

The kernel is `cache=True, nogil=True`, no `prange`; a portion is split among threads by the caller (the blocks do not
depend on each other, so the answer is the same to the bit on 1, 2, 4, 8 threads). The inputs are made writable
C-contiguous before the call (one compiled signature, lesson 17.39б); `warm_indicator()` compiles it.

A gap: a neighbour with NaN z takes no part (the indicator is NaN, and a NaN is not dropped into 0); a block with no
finite neighbour is STATUS 2. The value of a neighbour is `nb.z` (the top cut, the average of the coincident samples,
the rule of Д-29 — whatever the search used), so the indicator of an averaged sample is [average ≤ t].
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.jit import njit

from .cov import BlockCov, CovModel, disc_points
from .krige import KR_SIZE, MAX_SYS, O_EST, OUT_N, apply_weights, krige
from .samples import declus_mean
from .search import Neighbours, Searcher

ST_OK = 0
ST_FEW = 2  # Д-27 «мало проб»
ST_SINGULAR = 3  # Д-27 «вырожденная система»
_ST_SIZE = 4  # internal: a buffer too small (n_max > 128); the wrapper raises

K_MAX = 20  # the thresholds of IK (e18-api.md §3)


# --- the indicators, the order relations, the E-type -----------------------------------------------------------------


def indicators(z: Any, thresholds: Any) -> np.ndarray:
    """[z ≤ t_k] as float64 [n, K]; a NaN in z is a NaN in the whole row, not a 0."""
    zz = np.asarray(z, dtype=np.float64).reshape(-1)
    t = np.asarray(thresholds, dtype=np.float64).reshape(-1)
    out = (zz[:, None] <= t[None, :]).astype(np.float64)
    out[np.isnan(zz), :] = np.nan
    return out


@njit(cache=True, nogil=True)
def _order(f: np.ndarray, tmp: np.ndarray) -> None:
    k = f.shape[0]
    for i in range(k):
        if math.isnan(f[i]):  # a gap is a gap: the row is NaN
            for j in range(k):
                f[j] = math.nan
            return
    for i in range(k):
        v = f[i]
        if v < 0.0:
            v = 0.0
        elif v > 1.0:
            v = 1.0
        f[i] = v
    # the downward correction (the least of the ones above) goes to tmp, the upward one (the largest of the ones below)
    # is made on the way, then the mean
    run = f[k - 1]
    for i in range(k - 1, -1, -1):
        if f[i] < run:
            run = f[i]
        tmp[i] = run
    run = f[0]
    for i in range(k):
        if f[i] > run:
            run = f[i]
        f[i] = 0.5 * (run + tmp[i])


@njit(cache=True, nogil=True)
def order_relations(f: np.ndarray) -> None:
    """In place: the GSLIB correction of the order relations of F [K] (a writable C array). A NaN — the row is NaN."""
    _order(f, np.empty(f.shape[0]))


@njit(cache=True, nogil=True)
def etype(f: np.ndarray, means: np.ndarray) -> float:
    """Σ_{j=0..K} (F_j − F_(j−1))·m_j with F_(−1) = 0, F_K = 1; F [K] corrected, means [K + 1]."""
    k = f.shape[0]
    prev = 0.0
    s = 0.0
    for j in range(k):
        s += (f[j] - prev) * means[j]
        prev = f[j]
    return float(s + (1.0 - prev) * means[k])


def class_means(z: Any, w: Any, thresholds: Any) -> np.ndarray:
    """m_j [K + 1]: the declustered mean (Σ w·z / Σ w; the weight is finite and > 0) of the samples of the class j —
    class 0 is z ≤ t_0, class j is (t_(j−1); t_j], class K is z > t_(K−1). `w` None, or no sample of the class has a
    weight — the plain mean of the class. A class with no finite sample — the middle of its thresholds (an extreme one,
    having one threshold, takes it): ТЗ 8.8.1, № «Допущения» 18.1. The gaps in z are ignored."""
    zz = np.asarray(z, dtype=np.float64).reshape(-1)
    t = np.asarray(thresholds, dtype=np.float64).reshape(-1)
    ww = None if w is None else np.asarray(w, dtype=np.float64).reshape(-1)
    k = t.shape[0]
    cls = np.searchsorted(t, zz, side="left")  # z ≤ t_0 → 0; t_(j−1) < z ≤ t_j → j; z > t_(K−1) → K
    out = np.empty(k + 1, dtype=np.float64)
    for j in range(k + 1):
        sel = (cls == j) & np.isfinite(zz)
        if not sel.any():
            lo = t[j - 1] if j > 0 else t[0]
            hi = t[j] if j < k else t[k - 1]
            out[j] = 0.5 * (lo + hi)
            continue
        m = None if ww is None else declus_mean(zz[sel], ww[sel])
        out[j] = float(np.mean(zz[sel])) if m is None else m
    return out


def proportions(z: Any, w: Any, thresholds: Any) -> np.ndarray:
    """The declustered proportions of [z ≤ t_k] [K] (the global means of the indicators, for the SK of 18.33); `w` None
    — the plain ones. A gap in z takes no part; no finite sample — NaN."""
    zz = np.asarray(z, dtype=np.float64).reshape(-1)
    ind = indicators(zz, thresholds)
    ww = None if w is None else np.asarray(w, dtype=np.float64).reshape(-1)
    out = np.full(ind.shape[1], np.nan)
    fin = np.isfinite(zz)
    if not fin.any():
        return out
    for k in range(ind.shape[1]):
        m = None if ww is None else declus_mean(ind[:, k], ww)
        out[k] = float(np.mean(ind[fin, k])) if m is None else m
    return out


# --- the kernel ------------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _ik_kernel(
    kinds: np.ndarray,
    pars: np.ndarray,
    head: np.ndarray,
    local: np.ndarray,
    idx: np.ndarray,
    zn: np.ndarray,
    cnt: np.ndarray,
    centres: np.ndarray,
    offs: np.ndarray,
    bv: float,
    mean: float,
    ok: int,
    thr: np.ndarray,
    col0: int,
    ncols: int,
    raw: np.ndarray,
    st: np.ndarray,
) -> None:
    """The blocks with `st == 0`: OK of the indicator of the threshold col0 (the system of the model given), and the
    same λ for the thresholds col0 + 1 … col0 + ncols − 1 (the median IK). The result goes to raw[:, col0:col0 + ncols];
    `st` becomes 2 (no finite sample), 3 (degenerate), 4 (the buffers are too small). A block with st ≠ 0 is skipped.
    `mean` (0.0) and `ok` (1) are the arguments, not literals: `krige` keeps its own signatures (no compilation of it
    for a literal `ok`, which would be a second one in `krige.signatures`)."""
    m = idx.shape[0]
    w = idx.shape[1]
    xyz = np.empty((w + 1, 3))
    zb = np.empty(w + 1)
    ind = np.empty(w + 1)
    a = np.empty((w + 1, w + 1))
    rhs = np.empty(w + 1)
    sol = np.empty(w + 1)
    out = np.empty(OUT_N)
    for b in range(m):
        if st[b] != 0:
            continue
        c = 0
        for j in range(cnt[b]):
            i = idx[b, j]
            z = zn[b, j]
            if i < 0 or math.isnan(z):  # a gap does not take part
                continue
            xyz[c, 0] = local[i, 0]
            xyz[c, 1] = local[i, 1]
            xyz[c, 2] = local[i, 2]
            zb[c] = z
            c += 1
        if c == 0:
            st[b] = ST_FEW
            continue
        t0 = thr[col0]
        for i in range(c):
            ind[i] = 1.0 if zb[i] <= t0 else 0.0
        # np.int64(c): `c` is a literal 0 for the compiler at first; a literal would be a second signature of `krige`
        s = krige(kinds, pars, head, xyz, np.int64(c), ind, centres[b], offs, bv, mean, ok, a, rhs, sol, out)  # type: ignore[arg-type]
        if s != 0:
            st[b] = _ST_SIZE if s == KR_SIZE else ST_SINGULAR
            continue
        raw[b, col0] = out[O_EST]
        for q in range(1, ncols):
            tq = thr[col0 + q]
            for i in range(c):
                ind[i] = 1.0 if zb[i] <= tq else 0.0
            raw[b, col0 + q] = apply_weights(sol, c, ind, mean, ok)


@njit(cache=True, nogil=True)
def _finish(raw: np.ndarray, st: np.ndarray, means: np.ndarray, p: np.ndarray, et: np.ndarray) -> None:
    """P = the order relations of `raw`, ETYPE, for the blocks with st == 0; NaN for the others."""
    k = raw.shape[1]
    tmp = np.empty(k)
    row = np.empty(k)
    for b in range(raw.shape[0]):
        if st[b] != 0:
            for q in range(k):
                p[b, q] = math.nan
            et[b] = math.nan
            continue
        for q in range(k):
            row[q] = raw[b, q]
        _order(row, tmp)
        for q in range(k):
            p[b, q] = row[q]
        et[b] = etype(row, means)


# --- the setup and the portion ---------------------------------------------------------------------------------------


def _own(a: Any, dtype: Any) -> np.ndarray:
    out = np.asarray(a, dtype=dtype, order="C")
    return out if out.flags.writeable else out.copy()


def _bad(message: str) -> ApiError:
    return ApiError(500, "internal", message, "Сообщите разработчику.")


@dataclass(frozen=True)
class IkSetup:
    """What a row of the plan with `method.kind = ik` needs of the model: thresholds [K] (ascending), the models —
    K of them ("per", in the order of the thresholds) or one ("median"), C̄_VV of each, the points of the block, the
    means of the classes [K + 1]."""

    thresholds: np.ndarray
    covs: tuple[CovModel, ...]
    bv: tuple[float, ...]
    offs: np.ndarray  # float64 [N, 3]: N = 1 — a point
    means: np.ndarray  # float64 [K + 1]

    @property
    def median(self) -> bool:
        return len(self.covs) == 1

    @property
    def k(self) -> int:
        return int(self.thresholds.shape[0])

    @classmethod
    def of(
        cls,
        thresholds: Sequence[float],
        covs: Sequence[CovModel],
        means: Any,
        *,
        size: Sequence[float] | None = None,
        disc: Sequence[int] = (1, 1, 1),
        phi: float = 0.0,
        block_cov: Sequence[BlockCov] | None = None,
    ) -> IkSetup:
        """`size` None — a point (C̄_VV = C(0) of each model). `block_cov` — the caches C̄_VV of the models (a portion
        after a portion reuses them); without them the sums are made here."""
        t = np.asarray(thresholds, dtype=np.float64).reshape(-1)
        k = int(t.shape[0])
        if not 1 <= k <= K_MAX or np.isnan(t).any() or (np.diff(t) <= 0.0).any():
            raise _bad(f"Индикаторный кригинг: пороги {k} шт. не по возрастанию или вне 1–{K_MAX}.")
        cv = tuple(covs)
        if len(cv) not in (1, k):
            raise _bad(f"Индикаторный кригинг: моделей {len(cv)}, нужно 1 (медианный) или {k} (по порогам).")
        m = np.asarray(means, dtype=np.float64).reshape(-1)
        if m.shape[0] != k + 1:
            raise _bad(f"Индикаторный кригинг: средних классов {m.shape[0]}, нужно {k + 1}.")
        if size is None:
            offs = np.zeros((1, 3))
            bv = tuple(c.sill for c in cv)
        else:
            offs = disc_points(size, disc, phi)
            caches = list(block_cov) if block_cov is not None else [BlockCov(c) for c in cv]
            bv = tuple(bc.vv(size, disc, phi) for bc in caches)
        return cls(t, cv, bv, np.ascontiguousarray(offs, dtype=np.float64), m)


@dataclass
class IkChunk:
    """The result of one pass for a portion: P [m, K] (after the order relations), etype [m], the status i1 [m]
    (0 / 2 / 3), `raw` [m, K] — F* before the correction (NaN with a status ≠ 0)."""

    p: np.ndarray
    etype: np.ndarray
    status: np.ndarray
    raw: np.ndarray


def ik_chunk(nb: Neighbours, local: np.ndarray, centres: np.ndarray, setup: IkSetup) -> IkChunk:
    """IK of the blocks `centres` [m, 3] (local) from the selection `nb` of one pass; `local` — the coordinates of the
    samples (`RowSamples.local`, the numbers of `nb.idx` index it). Thread-safe; the blocks are independent."""
    c = _own(centres, np.float64).reshape(-1, 3)
    m = int(c.shape[0])
    if nb.idx.shape[0] != m:
        raise _bad("Индикаторный кригинг: число блоков и отборов не совпадает.")
    if nb.idx.shape[1] + 1 > MAX_SYS:
        raise _bad(f"Индикаторный кригинг: n_max {nb.idx.shape[1]} больше 128.")
    k = setup.k
    st = np.where(nb.ok & np.isfinite(c).all(axis=1), ST_OK, ST_FEW).astype(np.int8)
    raw = np.full((m, k), np.nan)
    args = (_own(local, np.float64), _own(nb.idx, np.int32), _own(nb.z, np.float64), _own(nb.n, np.int16), c,
            _own(setup.offs, np.float64))  # fmt: skip
    thr = _own(setup.thresholds, np.float64)
    if setup.median:
        _ik_kernel(*_cov(setup.covs[0]), *args, setup.bv[0], 0.0, 1, thr, 0, k, raw, st)
    else:
        for q, cov in enumerate(setup.covs):
            _ik_kernel(*_cov(cov), *args, setup.bv[q], 0.0, 1, thr, q, 1, raw, st)
    if (st == _ST_SIZE).any():
        raise _bad("Индикаторный кригинг: буфер системы мал (n_max > 128).")
    p = np.empty((m, k))
    et = np.empty(m)
    _finish(raw, st, _own(setup.means, np.float64), p, et)
    raw[st != ST_OK] = np.nan
    return IkChunk(p, et, st, raw)


def _cov(cov: CovModel) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return _own(cov.kinds, np.int64), _own(cov.pars, np.float64), _own(cov.head, np.float64)


@dataclass
class IkRun:
    """`ik_passes`: P [m, K], etype [m], status i1 [m] (0 / 2 / 3), pass_ i1 [m] (1…P; 0 — none holds), `nb` — the
    merged selection of the pass that estimated each block (the fields NS, NH, NHY, NOCT, AD, DMIN)."""

    p: np.ndarray
    etype: np.ndarray
    status: np.ndarray
    pass_: np.ndarray
    nb: Neighbours


def ik_passes(searcher: Searcher, local: np.ndarray, centres: np.ndarray, setup: IkSetup, *, start: int = 0,
              exclude: tuple[str, np.ndarray] | None = None) -> IkRun:  # fmt: skip
    """A block is estimated by the first pass (from `start`, 0-based) that holds and whose systems are not degenerate
    (8.8.2, Д-27). STATUS 3 — it had enough samples in at least one pass and every such pass was degenerate; 2 — in
    no pass were there enough samples (or the centre is NaN)."""
    c = _own(centres, np.float64).reshape(-1, 3)
    m = int(c.shape[0])
    width = max(p.n_max for p in searcher.passes[start:])
    nb_all = Neighbours.empty(m, width)
    p_all = np.full((m, setup.k), np.nan)
    et_all = np.full(m, np.nan)
    pass_ = np.zeros(m, np.int8)
    singular = np.zeros(m, np.bool_)
    pending = np.flatnonzero(np.isfinite(c).all(axis=1))
    for k in range(start, searcher.n_passes):
        if pending.size == 0:
            break
        sub_ex = None if exclude is None else (exclude[0], np.asarray(exclude[1])[pending])
        nb = searcher.gather(c[pending], k, exclude=sub_ex)
        res = ik_chunk(nb, local, c[pending], setup)
        done = np.flatnonzero(res.status == ST_OK)
        if done.size:
            rows = pending[done]
            p_all[rows] = res.p[done]
            et_all[rows] = res.etype[done]
            pass_[rows] = k + 1
            nb_all.put(rows, nb, done)
        singular[pending[res.status == ST_SINGULAR]] = True
        pending = pending[res.status != ST_OK]
    status = np.where(pass_ > 0, ST_OK, np.where(singular, ST_SINGULAR, ST_FEW)).astype(np.int8)
    return IkRun(p_all, et_all, status, pass_, nb_all)


# --- the warm-up -----------------------------------------------------------------------------------------------------


def warm_indicator() -> None:
    """Compiles the kernel (the inputs of the wrapper are made writable, so one signature) and exercises the
    wrapper on writable and on read-only inputs (the model, the points of a block, the samples)."""
    from bf_core.geostat.variogram.model import Model, Structure

    from .cov import cov_model

    cov = cov_model(Model(0.1, (Structure("spherical", 0.9, 30.0, 20.0, 10.0, 30.0, 10.0, 5.0),)))
    local = np.array([[0.0, 0.0, 0.0], [5.0, 1.0, 2.0], [-3.0, 4.0, 1.0], [8.0, -6.0, 3.0]])
    centres = np.array([[1.0, 2.0, 0.5], [2.0, 2.0, 0.5]])
    nb = Neighbours.empty(2, 4)
    nb.idx[:] = np.arange(4, dtype=np.int32)
    nb.z[:] = np.array([1.0, 2.0, 3.0, 2.5])
    nb.n[:] = 4
    nb.ok[:] = True
    t = [1.5, 2.2, 2.8]
    for freeze in (False, True):
        for size in (None, (4.0, 4.0, 2.0)):
            lo, ce = (_frozen(local), _frozen(centres)) if freeze else (local, centres)
            for covs in ((cov,), (cov, cov, cov)):
                setup = IkSetup.of(t, covs, [1.0, 2.0, 3.0, 4.0], size=size, disc=(2, 2, 2))
                if freeze:
                    setup = IkSetup(_frozen(setup.thresholds), setup.covs, setup.bv, _frozen(setup.offs),
                                    _frozen(setup.means))  # fmt: skip
                ik_chunk(nb, lo, ce, setup)


def _frozen(a: np.ndarray) -> np.ndarray:
    out = np.array(a)
    out.setflags(write=False)
    return out
