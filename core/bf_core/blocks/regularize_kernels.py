"""Kernels of the regularization (`blocks/regularize.py`): numba `cache=True, nogil=True` (the cache — bf_core.paths),
so strips and bands run on Python threads. Both write only what they own: `_prep` the rows of its strip, `_accumulate`
the cells of its band of new rows (y), summing every cell in the order of the rows — the result does not depend on how
the strips and bands are scheduled.
"""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit

SNAP = 1e-9  # an edge of a row this close (in new cells) to an edge of the new grid is that edge
BASE = 3  # accumulators before the fields: rows that overlap the cell, Σ overlap·FRAC, rows with a FRAC


@njit(cache=True, nogil=True)
def _snap(x):  # type: ignore[no-untyped-def]
    r = np.rint(x)
    return r if abs(x - r) < SNAP else x


@njit(cache=True, nogil=True)
def _nadd(s, c, i, x):  # type: ignore[no-untyped-def]
    """Neumaier: s[i] += x with the lost part kept in c[i]."""
    t = s[i] + x
    if abs(s[i]) >= abs(x):
        c[i] += (s[i] - t) + x
    else:
        c[i] += (x - t) + s[i]
    s[i] = t


@njit(cache=True, nogil=True)
def prep(
    p,
    has_sub,
    s,
    w,
    nx,
    ny,
    d,
    div,
    q0,
    size,
    frac,
    has_frac,
    dens,
    dens_mode,
    dens_c,
    vals,
    tlo,
    thi,
    clo,
    chi,
    lo,
    hi,
):  # type: ignore[no-untyped-def]
    """Rows [lo, hi): the box in the units of the new grid (`tlo`, `thi`), the cells it reaches (`clo`, `chi`,
    inclusive), and the Neumaier sums (sum, carry) of the totals before: volume, tonnes, metal of each number
    field → [2 + nf, 2]."""
    nf = vals.shape[0]
    ss = np.zeros(2 + nf)
    cc = np.zeros(2 + nf)
    for r in range(lo, hi):
        pr = np.int64(p[r])
        prod = 1.0
        for a in range(3):
            if a == 0:
                ia = pr % nx
            elif a == 1:
                ia = (pr // nx) % ny
            else:
                ia = pr // (nx * ny)
            so = np.float64(s[r, a]) if has_sub else 0.0
            wo = np.float64(w[r, a]) if has_sub else div[a]
            a0 = (np.float64(ia) + so / div[a]) * d[a]
            a1 = (np.float64(ia) + (so + wo) / div[a]) * d[a]
            wd = wo * d[a] / div[a]
            prod = wd if a == 0 else prod * wd  # (wx·wy)·wz, as geometry.row_volumes
            t0 = _snap((a0 - q0[a]) / size[a])
            t1 = _snap((a1 - q0[a]) / size[a])
            tlo[r, a] = t0
            thi[r, a] = t1
            clo[r, a] = np.int32(np.floor(t0))
            chi[r, a] = np.int32(np.ceil(t1)) - 1
        fr = frac[r] if has_frac else 1.0
        if fr != fr:
            continue
        vol = prod * fr
        dn = np.nan
        if dens_mode == 1:
            dn = dens[r]
        elif dens_mode == 2:
            dn = dens_c
        _nadd(ss, cc, 0, vol)
        if dens_mode != 0 and dn == dn:
            _nadd(ss, cc, 1, vol * dn)
        for f in range(nf):
            v = vals[f, r]
            if v != v:
                continue
            if dens_mode == 0:
                _nadd(ss, cc, 2 + f, vol * v)
            elif dn == dn:
                _nadd(ss, cc, 2 + f, (vol * dn) * v)
    out = np.empty((2 + nf, 2))
    out[:, 0] = ss
    out[:, 1] = cc
    return out


@njit(cache=True, nogil=True)
def prep_list(
    blo,
    bhi,
    wd3,
    q0,
    size,
    frac,
    has_frac,
    dens,
    dens_mode,
    dens_c,
    vals,
    tlo,
    thi,
    clo,
    chi,
    lo,
    hi,
):  # type: ignore[no-untyped-def]
    """`prep` of a list of blocks (ADR 0030 decision 10): the boxes are given as they are — the low and the high edge
    `blo`, `bhi` and the sizes `wd3` (n×3, local axes) — not computed from (P, s, w); the rest is the same."""
    nf = vals.shape[0]
    ss = np.zeros(2 + nf)
    cc = np.zeros(2 + nf)
    for r in range(lo, hi):
        prod = 1.0
        for a in range(3):
            wd = wd3[r, a]
            prod = wd if a == 0 else prod * wd  # (wx·wy)·wz, as geometry.row_volumes
            t0 = _snap((blo[r, a] - q0[a]) / size[a])
            t1 = _snap((bhi[r, a] - q0[a]) / size[a])
            tlo[r, a] = t0
            thi[r, a] = t1
            clo[r, a] = np.int32(np.floor(t0))
            chi[r, a] = np.int32(np.ceil(t1)) - 1
        fr = frac[r] if has_frac else 1.0
        if fr != fr:
            continue
        vol = prod * fr
        dn = np.nan
        if dens_mode == 1:
            dn = dens[r]
        elif dens_mode == 2:
            dn = dens_c
        _nadd(ss, cc, 0, vol)
        if dens_mode != 0 and dn == dn:
            _nadd(ss, cc, 1, vol * dn)
        for f in range(nf):
            v = vals[f, r]
            if v != v:
                continue
            if dens_mode == 0:
                _nadd(ss, cc, 2 + f, vol * v)
            elif dn == dn:
                _nadd(ss, cc, 2 + f, (vol * dn) * v)
    out = np.empty((2 + nf, 2))
    out[:, 0] = ss
    out[:, 1] = cc
    return out


@njit(cache=True, nogil=True)
def accumulate(
    r0,
    r1,
    na,
    nb,
    base,
    tlo,
    thi,
    clo,
    chi,
    frac,
    has_frac,
    dens,
    dens_mode,
    dens_c,
    vals,
    wts,
    cats,
    cat_off,
    acc,
    j0,
    j1,
):  # type: ignore[no-untyped-def]
    """Rows [r0, r1) into the cells of the new rows [j0, j1) of the window `acc` [nacc, slots·na·nb] (the layer `base`
    is its first slot). A row reaches the cells with a positive overlap along every axis: the weight of the overlap is
    the product of the three, times FRAC (and the density for a tonnes weight)."""
    nf = vals.shape[0]
    nc = cats.shape[0]
    layer = na * nb
    for r in range(r0, r1):
        ylo = max(clo[r, 1], j0)
        yhi = min(chi[r, 1], j1 - 1)
        if yhi < ylo:
            continue
        fr = frac[r] if has_frac else 1.0
        fok = fr == fr
        dn = np.nan
        if dens_mode == 1:
            dn = dens[r]
        elif dens_mode == 2:
            dn = dens_c
        for cz in range(clo[r, 2], chi[r, 2] + 1):
            ovz = min(thi[r, 2], cz + 1.0) - max(tlo[r, 2], np.float64(cz))
            if ovz <= 0.0:
                continue
            zoff = (cz - base) * layer
            for cy in range(ylo, yhi + 1):
                ovy = min(thi[r, 1], cy + 1.0) - max(tlo[r, 1], np.float64(cy))
                if ovy <= 0.0:
                    continue
                for cx in range(clo[r, 0], chi[r, 0] + 1):
                    ovx = min(thi[r, 0], cx + 1.0) - max(tlo[r, 0], np.float64(cx))
                    if ovx <= 0.0:
                        continue
                    cell = zoff + cy * na + cx
                    acc[0, cell] += 1.0
                    if not fok:
                        continue
                    wv = ((ovx * ovy) * ovz) * fr
                    acc[1, cell] += wv
                    acc[2, cell] += 1.0
                    for f in range(nf):
                        v = vals[f, r]
                        if v != v:
                            continue
                        if wts[f] == 0:
                            acc[BASE + 2 * f, cell] += wv * v
                            acc[BASE + 2 * f + 1, cell] += wv
                        elif dn == dn:
                            acc[BASE + 2 * f, cell] += (wv * dn) * v
                            acc[BASE + 2 * f + 1, cell] += wv * dn
                    for c in range(nc):
                        code = cats[c, r]
                        if code >= 0:
                            acc[cat_off[c] + code, cell] += wv
