"""The kernels of the economics of a block (подшаг 21.2; ТЗ 8.11.2; ADR 0028 decisions 5–8, 16): numba `cache=True,
nogil=True`, float64; the cache — bf_core.paths.

For a block b of the tonnage t = V·FRAC·f·ρ, the rock r and the grades g_e (k_e turns g into the units of the price):

    m_e = t·g_e·k_e·rec_e(r)                                (the recoverable metal; g_e = NaN — no m_e, never 0)
    R(RF) = Σ_e m_e·(RF·P_e − S_e)·(1 − roy_e) = A·RF − B  (A = Σ m_e·P_e·(1−roy_e), B = Σ m_e·S_e·(1−roy_e))
    C_m = t·(c_m0 + c_gl·max(0; (Z_ref − z)/H_gl)),  C_p = t·(c_p(r) + c_mp(r)),  D = B + C_p
    v(RF) = max(A·RF − D; 0) − C_m,   ore at RF = 1: A − D > 0   (a «only waste» rock: A = B = 0)

R is linear in RF, so the model is read ONCE: a row keeps (A, D, C_m) and the value of every RF is a pass over these
three numbers (`node_values`). A parent of sub-blocks is a node whose value is the sum of the values of its rows
(each row is ore or waste by itself, decision 7): the Neumaier sum in the order of the rows of the file — the same
bits with any portion and any number of threads (a strip of nodes is one thread's, nothing is summed between strips).

The kernels that fill the outputs of rows take `lo, hi` and write only their rows (`nogil`: strips on Python threads,
`bf_core.composites.strips`); the counts and the Neumaier sums of «Контроль» (`control`) are one sequential pass in
the order of the rows. State of a row: 0 — air (f = 0 or an «air» rock: out of the graph, not a waste), 1 — counted,
2 — counted with the default density, 3 — no volume (FRAC empty: out), 4 — no density (an error of the pass).
"""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit

ST_AIR, ST_OK, ST_DEFAULT, ST_NO_VOLUME, ST_NO_DENSITY = 0, 1, 2, 3, 4

# slots of the counts of `control`: rows, air, no volume, no density, the default density, cells absent from the file,
# then one slot per element — the blocks of an ore-capable rock with no grade of it
CNT_ROWS, CNT_AIR, CNT_NO_VOLUME, CNT_NO_DENSITY, CNT_DEFAULT, CNT_EMPTY, CNT_GAP0 = 0, 1, 2, 3, 4, 5, 6
# rows of the Neumaier accumulators `acc` [Q, 2] (sum, carry): tonnes in the graph, the volume of the blocks of the
# default density, the volume of the absent cells, then one row per element — the tonnes with no grade of it
ACC_TONNES, ACC_DEFAULT_VOLUME, ACC_EMPTY_VOLUME, ACC_GAP0 = 0, 1, 2, 3


@njit(cache=True, nogil=True)
def _nadd(acc, r, x):  # type: ignore[no-untyped-def]
    """Neumaier: acc[r, 0] += x with the lost part kept in acc[r, 1]."""
    s = acc[r, 0]
    t = s + x
    if abs(s) >= abs(x):
        acc[r, 1] += (s - t) + x
    else:
        acc[r, 1] += (x - t) + s
    acc[r, 0] = t


@njit(cache=True, nogil=True)
def row_econ(
    lo,
    hi,
    f,
    vol,
    dens,
    dens_c,
    dens_def,
    rock,
    z,
    g,
    rock_air,
    rock_waste,
    rock_cp,
    rock_rec,
    k,
    price,
    sell,
    roy,
    cm0,
    cgl,
    zref,
    hgl,
    state,
    t,
    a,
    b,
    cp,
    cm,
    ore,
):  # type: ignore[no-untyped-def]
    """Rows lo ≤ i < hi. In: `f` — the share of the restrictor; `vol` — the volume with FRAC (NaN: no FRAC);
    `dens` — the density of each row (length 0: the constant `dens_c`), `dens_def` — the default density (NaN: none);
    `rock` — the rock of each row; `z` — the centre of the block; `g` [E, n] — the grades (NaN: a gap); the tables of
    rocks (`rock_cp` = c_p + c_mp, `rock_rec` [R, E]) and of elements. Out: `state`, `t`, `a` (A), `b` (B), `cp`
    (C_p), `cm` (C_m), `ore` (1 — ore at RF = 1); every out row is written (0 where the row is not counted)."""
    ne = g.shape[0]
    for i in range(lo, hi):
        state[i] = ST_AIR
        t[i] = 0.0
        a[i] = 0.0
        b[i] = 0.0
        cp[i] = 0.0
        cm[i] = 0.0
        ore[i] = 0
        r = rock[i]
        if not f[i] > 0.0 or rock_air[r]:
            continue
        v = vol[i]
        if np.isnan(v):
            state[i] = ST_NO_VOLUME
            continue
        d = dens[i] if dens.shape[0] > 0 else dens_c
        st = ST_OK
        if not (np.isfinite(d) and d > 0.0):
            if np.isfinite(dens_def) and dens_def > 0.0:
                d = dens_def
                st = ST_DEFAULT
            else:
                state[i] = ST_NO_DENSITY
                continue
        tt = v * f[i] * d  # V·FRAC·f·ρ
        aa = 0.0
        bb = 0.0
        if not rock_waste[r]:
            for e in range(ne):
                ge = g[e, i]
                if np.isfinite(ge):
                    m = tt * ge * k[e] * rock_rec[r, e]
                    w = 1.0 - roy[e]
                    aa += m * price[e] * w
                    bb += m * sell[e] * w
        cpi = tt * rock_cp[r]
        dz = (zref - z[i]) / hgl
        cmi = tt * (cm0 + (cgl * dz if dz > 0.0 else 0.0))
        state[i] = st
        t[i] = tt
        a[i] = aa
        b[i] = bb
        cp[i] = cpi
        cm[i] = cmi
        if (not rock_waste[r]) and (aa - bb - cpi > 0.0):
            ore[i] = 1


@njit(cache=True, nogil=True)
def control(n, is_empty, state, rock, vol, f, t, g, ore, rock_waste, cnt, acc, rock_cnt, rock_acc):  # type: ignore[no-untyped-def]
    """The counts and the sums of «Контроль» of a portion, in the order of its rows (one thread). `is_empty` — the
    rows are cells absent from the file. `rock_cnt` [R, 2]: counted blocks, ore blocks; `rock_acc` [2R, 2]: the
    tonnes of ore (row 2r) and of waste (2r + 1)."""
    ne = g.shape[0]
    for i in range(n):
        s = state[i]
        cnt[CNT_ROWS] += 1
        if s == ST_AIR:
            cnt[CNT_AIR] += 1
            continue
        if s == ST_NO_VOLUME:
            cnt[CNT_NO_VOLUME] += 1
            continue
        if s == ST_NO_DENSITY:
            cnt[CNT_NO_DENSITY] += 1
            continue
        r = rock[i]
        ve = vol[i] * f[i]
        _nadd(acc, ACC_TONNES, t[i])
        if s == ST_DEFAULT:
            cnt[CNT_DEFAULT] += 1
            _nadd(acc, ACC_DEFAULT_VOLUME, ve)
        if is_empty:
            cnt[CNT_EMPTY] += 1
            _nadd(acc, ACC_EMPTY_VOLUME, ve)
        rock_cnt[r, 0] += 1
        if ore[i] != 0:
            rock_cnt[r, 1] += 1
            _nadd(rock_acc, 2 * r, t[i])
        else:
            _nadd(rock_acc, 2 * r + 1, t[i])
        if not rock_waste[r]:
            for e in range(ne):
                if not np.isfinite(g[e, i]):
                    cnt[CNT_GAP0 + e] += 1
                    _nadd(acc, ACC_GAP0 + e, t[i])


@njit(cache=True, nogil=True)
def terms(lo, hi, rf, state, rock, t, g, rock_waste, rock_rec, k, price, sell, roy, m, mrec, gross, selling, royalty):  # type: ignore[no-untyped-def]
    """The revenue terms of the rows lo ≤ i < hi at the factor `rf` (the report and the pit-by-pit, 21.8): per element
    `m` = t·g·k (the metal in the rock) and `mrec` = m·rec (recoverable), per row `gross` = Σ mrec·rf·P, `selling` =
    Σ mrec·S, `royalty` = Σ mrec·(rf·P − S)·roy; R = gross − selling − royalty. A row not counted, of a «only waste»
    rock, or an element with no grade gives 0 (a gap is not a metal)."""
    ne = g.shape[0]
    for i in range(lo, hi):
        gross[i] = 0.0
        selling[i] = 0.0
        royalty[i] = 0.0
        for e in range(ne):
            m[e, i] = 0.0
            mrec[e, i] = 0.0
        if (state[i] != ST_OK and state[i] != ST_DEFAULT) or rock_waste[rock[i]]:
            continue
        r = rock[i]
        for e in range(ne):
            ge = g[e, i]
            if np.isfinite(ge):
                me = t[i] * ge * k[e]
                mr = me * rock_rec[r, e]
                m[e, i] = me
                mrec[e, i] = mr
                gross[i] += mr * (rf * price[e])
                selling[i] += mr * sell[e]
                royalty[i] += mr * (rf * price[e] - sell[e]) * roy[e]


@njit(cache=True, nogil=True)
def node_values(lo, hi, seg, a, d, cm, rf, out):  # type: ignore[no-untyped-def]
    """The value of the nodes lo ≤ n < hi at the factor `rf`: Σ over the rows seg[n]:seg[n+1] of max(A·rf − D; 0) − C_m,
    the Neumaier sum in the order of the rows (a node of one row — the value itself, no rounding added)."""
    for n in range(lo, hi):
        s = 0.0
        c = 0.0
        for j in range(seg[n], seg[n + 1]):
            x = a[j] * rf - d[j]
            if x < 0.0:
                x = 0.0
            x -= cm[j]
            tt = s + x
            if abs(s) >= abs(x):
                c += (s - tt) + x
            else:
                c += (x - tt) + s
            s = tt
        out[n] = s + c


@njit(cache=True, nogil=True)
def abs_sum(v):  # type: ignore[no-untyped-def]
    """Σ |v| by Neumaier, in the order of the array."""
    s = 0.0
    c = 0.0
    for i in range(v.shape[0]):
        x = abs(v[i])
        t = s + x
        if abs(s) >= abs(x):
            c += (s - t) + x
        else:
            c += (x - t) + s
        s = t
    return s + c


@njit(cache=True, nogil=True)
def scale_round(lo, hi, v, q, out):  # type: ignore[no-untyped-def]
    """out[i] = round(v[i] / q) (half to even) as int64, lo ≤ i < hi."""
    for i in range(lo, hi):
        out[i] = np.int64(np.rint(v[i] / q))


def warm() -> None:
    """Compile every kernel once on tiny arrays (the warm-up of `ops/warm.py` calls it)."""
    n, ne, nr = 2, 1, 1
    f, vol, z = np.ones(n), np.ones(n), np.zeros(n)
    g, rec = np.ones((ne, n)), np.ones((nr, ne))
    flag = np.zeros(nr, dtype=np.bool_)
    cpv, kv, pv, sv, rv = np.zeros(nr), np.ones(ne), np.ones(ne), np.zeros(ne), np.zeros(ne)
    state, rock = np.empty(n, dtype=np.int8), np.zeros(n, dtype=np.int32)
    t, a, b, cp, cm = (np.empty(n) for _ in range(5))
    ore = np.empty(n, dtype=np.uint8)
    row_econ(0, n, f, vol, np.empty(0), 2.5, np.nan, rock, z, g, flag, flag, cpv, rec, kv, pv, sv, rv, 0.0, 0.0, 0.0,
             10.0, state, t, a, b, cp, cm, ore)  # fmt: skip
    cnt, acc = np.zeros(CNT_GAP0 + ne, dtype=np.int64), np.zeros((ACC_GAP0 + ne, 2))
    control(n, False, state, rock, vol, f, t, g, ore, flag, cnt, acc, np.zeros((nr, 2), dtype=np.int64),
            np.zeros((2 * nr, 2)))  # fmt: skip
    m, mrec = np.empty((ne, n)), np.empty((ne, n))
    gr, se, ro = np.empty(n), np.empty(n), np.empty(n)
    terms(0, n, 1.0, state, rock, t, g, flag, rec, kv, pv, sv, rv, m, mrec, gr, se, ro)
    out = np.empty(1)
    node_values(0, 1, np.array([0, 2], dtype=np.int64), a, b, cm, 1.0, out)
    scale_round(0, 1, out, 0.01, np.empty(1, dtype=np.int64))
    abs_sum(out)
