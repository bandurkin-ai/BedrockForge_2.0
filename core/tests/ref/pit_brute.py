"""References of the pit-optimisation kernels (подшаг 21.4; ТЗ 8.11.3, 8.11.6; ADR 0028 decision 14, finding 5).

TEST-ONLY code (never imported by bf_core). Two independent checks of a maximum-closure answer:

* `brute_profiles` — the enumeration of every admissible 2D pit PROFILE (a column is mined from the top down to a
  depth, the depths of neighbouring columns are tied by the slope template). It needs no flow at all: Э-ПИТ-1 has
  7442 profiles, the 4×6 random models of Т8.11-2 have a few hundred.
* `scipy_closure` — `scipy.sparse.csgraph.maximum_flow` over the same arcs as the solver under test. IMPORTANT: scipy
  silently truncates capacities to int32 (a capacity 2³¹ + 5 gives a flow 0, ADR 0028 finding 5), so it is allowed
  only for integer values with Σ(v > 0) + 1 ≤ 2³¹ − 1 (the «infinity» is that sum plus one); a bigger one raises.

Conventions (as in `bf_core.mine.graph`): a 2D model is rows from the TOP down; an offset (Δc, m), m ≥ 1, of a block
points to its predecessor (c + Δc, r − m) — m rows UP (levels grow up). The profile enumeration requires (0, 1) in the
template (the block above), otherwise a mined set is not a prefix of a column and profiles are not exhaustive.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

I32_MAX = 2**31 - 1
DEFAULT_OFFSETS = ((-1, 1), (0, 1), (1, 1))  # «the block above and its two neighbours above» (Э-ПИТ-1)


@dataclass(frozen=True)
class BruteResult:
    n_feasible: int  # admissible profiles (the empty pit is one of them)
    best: int  # the optimal value
    optimal: NDArray[np.int64]  # (k, ncols): every optimal profile (depth in blocks per column)
    smallest: NDArray[np.int64]  # (ncols,): the intersection of all optimal pits = the elementwise minimum
    unique: bool  # one optimal profile only


def feasible_profiles(
    ncols: int, nrows: int, offsets: tuple[tuple[int, int], ...] = DEFAULT_OFFSETS
) -> NDArray[np.int64]:
    """All depth vectors d ∈ [0; nrows]^ncols with d[c + Δc] ≥ d[c] − m for every offset (Δc, m) inside the grid."""
    if (0, 1) not in offsets:
        raise ValueError("the profile enumeration needs the offset (0, 1) in the template")
    if any(m < 1 for _, m in offsets):
        raise ValueError("an offset must point up (m >= 1)")
    base = nrows + 1
    total = base**ncols
    if total > 5_000_000:
        raise ValueError("too many profiles to enumerate")
    idx = np.arange(total, dtype=np.int64)
    prof = np.empty((total, ncols), dtype=np.int64)
    for c in range(ncols):
        prof[:, c] = (idx // base**c) % base
    ok = np.ones(total, dtype=bool)
    for dc, m in offsets:
        for c in range(ncols):
            c2 = c + dc
            if 0 <= c2 < ncols:
                ok &= prof[:, c2] >= prof[:, c] - m
    return prof[ok]


def brute_profiles(values: NDArray[np.int64], offsets: tuple[tuple[int, int], ...] = DEFAULT_OFFSETS) -> BruteResult:
    """The optimum of a 2D model (rows from the top, shape (nrows, ncols)) by the enumeration of profiles."""
    v = np.asarray(values, dtype=np.int64)
    nrows, ncols = v.shape
    prof = feasible_profiles(ncols, nrows, offsets)
    prefix = np.zeros((ncols, nrows + 1), dtype=np.int64)
    prefix[:, 1:] = np.cumsum(v, axis=0).T
    tot = prefix[np.arange(ncols)[None, :], prof].sum(axis=1)
    best = int(tot.max())
    opt = prof[tot == best]
    return BruteResult(len(prof), best, opt, opt.min(axis=0), len(opt) == 1)


def profile_mask(profile: NDArray[np.int64], nrows: int) -> NDArray[np.bool_]:
    """Mined mask (nrows, ncols), rows from the top: row r of column c is mined iff r < profile[c]."""
    return np.arange(nrows)[:, None] < np.asarray(profile)[None, :]


def depths_of(mask: NDArray[np.bool_]) -> NDArray[np.int64]:
    """Column depths of a mined mask (nrows, ncols); raises if a column is not a prefix from the top."""
    m = np.asarray(mask, dtype=bool)
    d = m.sum(axis=0).astype(np.int64)
    if not np.array_equal(m, profile_mask(d, m.shape[0])):
        raise ValueError("the mined set is not a prefix of every column")
    return d


def scipy_closure(
    value: NDArray[np.int64], indptr: NDArray[np.int64], indices: NDArray[np.int32]
) -> tuple[int, NDArray[np.bool_]]:
    """(optimal value, the smallest optimal closure) by `scipy.sparse.csgraph.maximum_flow`, arcs u → its predecessors.

    Only for Σ(v > 0) + 1 ≤ 2³¹ − 1; the closure is the set reachable from s in the residual network."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import breadth_first_order, maximum_flow

    v = np.asarray(value, dtype=np.int64)
    n = len(v)
    pos_sum = int(v[v > 0].sum())
    inf = pos_sum + 1
    if inf > I32_MAX:
        raise ValueError("scipy truncates capacities to int32: values are too large for this reference")
    if pos_sum == 0:
        return 0, np.zeros(n, dtype=bool)
    s, t = n, n + 1
    deg = np.diff(indptr)
    tails = np.concatenate([np.repeat(np.arange(n, dtype=np.int64), deg), np.full(int((v > 0).sum()), s),
                            np.flatnonzero(v < 0)])  # fmt: skip
    heads = np.concatenate([np.asarray(indices, dtype=np.int64), np.flatnonzero(v > 0), np.full(int((v < 0).sum()), t)])
    caps = np.concatenate([np.full(len(indices), inf, dtype=np.int64), v[v > 0], -v[v < 0]])
    cap = csr_matrix((caps.astype(np.int32), (tails, heads)), shape=(n + 2, n + 2))
    cap.sum_duplicates()
    res = maximum_flow(cap, s, t)
    resid = (cap - res.flow).tocsr()
    resid.data = (resid.data > 0).astype(np.int8)
    resid.eliminate_zeros()
    order = breadth_first_order(resid, s, directed=True, return_predecessors=False)
    mined = np.zeros(n + 2, dtype=bool)
    mined[order] = True
    return pos_sum - int(res.flow_value), mined[:n]
