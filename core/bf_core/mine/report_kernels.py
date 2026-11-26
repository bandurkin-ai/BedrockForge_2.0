"""The kernels of the pit-by-pit report (подшаг 21.8; ТЗ 8.11.4; ADR 0028 decisions 16, 18): numba `cache=True,
nogil=True`, float64; the cache — bf_core.paths.

The report is a pass over the rows of the model (`econ.iter_rows`) that adds every row of a node of the shell k = PIT
(the number of the smallest shell holding the node; 0 — outside the pit) to the accumulators of the increment
ΔC_k = C_k \\ C_(k−1) at the base price RF = 1. The accumulators are Neumaier pairs (sum, carry) fed in the order of the
rows of the file by one thread: the sums are the same bit for bit with any portion of rows and any number of threads
of the economics (nothing is summed between threads).

Slots of `inc` [NS, N + 1, 2] (shell k = 1…N; the row k = 0 is not used): 0 ore tonnes, 1 waste tonnes, 2 revenue
(before the selling cost and the royalty), 3 the cost of mining (every row), 4 the cost of processing (ore rows), 5 the
selling cost, 6 the royalty, 7 CF of the rows (an ore row: gross − selling − royalty − mining − processing; a waste
row: −mining); then four slots per element e: 8 + 4e tonnes of ore with the grade, 9 + 4e Σ tonnes·grade, 10 + 4e the
metal in the ore, 11 + 4e the recoverable metal. A grade that is NaN adds nothing to the four (a gap is not a zero).

`ben` [3, (N + 1)·L, 2] — the same for the benches of the model (the level of a row = the layer of its parent; the
cell (k, level) is k·L + level): 0 ore tonnes, 1 waste tonnes, 2 CF of the rows.
"""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit

S_ORE, S_WASTE, S_REV, S_MINE, S_PROC, S_SELL, S_ROY, S_CF = 0, 1, 2, 3, 4, 5, 6, 7
S_ELEM = 8  # the first slot of the element 0
PER_ELEM = 4  # tonnes with the grade, Σ tonnes·grade, metal, recoverable metal
B_ORE, B_WASTE, B_CF = 0, 1, 2
ST_OK, ST_DEFAULT = 1, 2  # `econ_kernels`: the states of a counted row


@njit(cache=True, nogil=True)
def _nadd(acc, r, j, x):  # type: ignore[no-untyped-def]
    """Neumaier: acc[r, j, 0] += x with the lost part kept in acc[r, j, 1]."""
    s = acc[r, j, 0]
    t = s + x
    if abs(s) >= abs(x):
        acc[r, j, 1] += (s - t) + x
    else:
        acc[r, j, 1] += (x - t) + s
    acc[r, j, 0] = t


@njit(cache=True, nogil=True)
def accumulate(  # type: ignore[no-untyped-def]
    n, node, pit, lev, nlev, state, ore, t, cm, cp, g, m, mrec, gross, selling, royalty, inc, ben
):
    """The rows 0…n−1 of a portion, in order. `node` int32 — the node of the row (−1: not in the graph), `pit` int32
    [nodes] — the shell of the node, `lev` int32 — the level of the row; `state`, `ore`, `t`, `cm`, `cp` — the economics
    of the rows, `g` [E, n] — the grades (NaN: a gap), `m`, `mrec` [E, n], `gross`, `selling`, `royalty` —
    `econ_kernels.terms` at RF = 1."""
    ne = g.shape[0]
    for i in range(n):
        nd = node[i]
        if nd < 0:
            continue
        s = state[i]
        if s != ST_OK and s != ST_DEFAULT:
            continue
        k = pit[nd]
        if k <= 0:
            continue
        cell = k * nlev + lev[i]
        _nadd(inc, S_MINE, k, cm[i])
        if ore[i] != 0:
            cf = gross[i] - selling[i] - royalty[i] - cm[i] - cp[i]
            _nadd(inc, S_ORE, k, t[i])
            _nadd(inc, S_REV, k, gross[i])
            _nadd(inc, S_PROC, k, cp[i])
            _nadd(inc, S_SELL, k, selling[i])
            _nadd(inc, S_ROY, k, royalty[i])
            for e in range(ne):
                ge = g[e, i]
                if np.isfinite(ge):
                    b = S_ELEM + PER_ELEM * e
                    _nadd(inc, b, k, t[i])
                    _nadd(inc, b + 1, k, t[i] * ge)
                    _nadd(inc, b + 2, k, m[e, i])
                    _nadd(inc, b + 3, k, mrec[e, i])
            _nadd(ben, B_ORE, cell, t[i])
        else:
            cf = -cm[i]
            _nadd(inc, S_WASTE, k, t[i])
            _nadd(ben, B_WASTE, cell, t[i])
        _nadd(inc, S_CF, k, cf)
        _nadd(ben, B_CF, cell, cf)


@njit(cache=True, nogil=True)
def cumulate(inc, out):  # type: ignore[no-untyped-def]
    """out[s, k] = Σ_(j ≤ k) of the increment j of the slot s (inc[s, j, 0] + inc[s, j, 1]), Neumaier in the order of k;
    out[s, 0] = 0."""
    for s in range(inc.shape[0]):
        sm = 0.0
        c = 0.0
        out[s, 0] = 0.0
        for k in range(1, inc.shape[1]):
            x = inc[s, k, 0] + inc[s, k, 1]
            t = sm + x
            if abs(sm) >= abs(x):
                c += (sm - t) + x
            else:
                c += (x - t) + sm
            sm = t
            out[s, k] = sm + c


@njit(cache=True, nogil=True)
def cumulate_benches(ben, nlev, out):  # type: ignore[no-untyped-def]
    """out[s, k, L] = Σ_(j ≤ k) of the bench L of the increment j (C_k by levels), Neumaier in the order of k."""
    nk = ben.shape[1] // nlev
    for s in range(ben.shape[0]):
        for lv in range(nlev):
            sm = 0.0
            c = 0.0
            out[s, 0, lv] = 0.0
            for k in range(1, nk):
                j = k * nlev + lv
                x = ben[s, j, 0] + ben[s, j, 1]
                t = sm + x
                if abs(sm) >= abs(x):
                    c += (sm - t) + x
                else:
                    c += (x - t) + sm
                sm = t
                out[s, k, lv] = sm + c


def warm() -> None:
    """Compile every kernel once on tiny arrays (the warm-up of `ops/warm.py` calls it)."""
    n, ne, nl = 2, 1, 2
    node, lev = np.zeros(n, dtype=np.int32), np.zeros(n, dtype=np.int32)
    pit = np.ones(1, dtype=np.int32)
    state, ore = np.ones(n, dtype=np.int8), np.ones(n, dtype=np.uint8)
    t, cm, cp, gr, se, ro = (np.ones(n) for _ in range(6))
    g, m, mrec = np.ones((ne, n)), np.ones((ne, n)), np.ones((ne, n))
    inc = np.zeros((S_ELEM + PER_ELEM * ne, 2, 2))
    ben = np.zeros((3, 2 * nl, 2))
    accumulate(n, node, pit, lev, nl, state, ore, t, cm, cp, g, m, mrec, gr, se, ro, inc, ben)
    cumulate(inc, np.empty(inc.shape[:2]))
    cumulate_benches(ben, nl, np.empty((3, 2, nl)))
