"""Numba kernels of the closure graph (подшаг 21.4; ADR 0028 decisions 13, 16).

Arrays: `pos[u]` (int32) — the linear index of node u in the trimmed box, p = i + mx·(j + my·k); `index[p]` (int32) —
the node at p or −1; `tpl[u]` (uint8) — the offset table of u; the tables are concatenated: table t is
`tab_off[tab_ptr[t]:tab_ptr[t + 1]]`, a row is (Δi, Δj, m), m ≥ 1, and points to the PREDECESSOR (i + Δi, j + Δj, k + m)
(levels grow up). A predecessor outside the box or without a node (air) is not an arc. Nodes are numbered by growing
p, so the nodes of a level k are one contiguous range `level_ptr[k]:level_ptr[k + 1]`.

`cone_kernel` is the only parallel kernel: it walks the levels from the bottom to the top and, per level, `prange`s over
its nodes; a node pushes `needed = 1` to its predecessors, which lie on HIGHER levels only, so the threads of one level
never read what another writes, and a write is the same byte (1) from anyone: the result is the logical OR, bit for bit
the same on any number of threads. The other kernels are single-threaded (the cost is the reading of arrays).
"""

from __future__ import annotations

import numpy as np
from numba import prange

from bf_core.jit import njit


@njit(cache=True, nogil=True)
def mark_bridges(occ, offs):  # type: ignore[no-untyped-def]
    """The bridges of the reduced template (see `graph.py`, BRIDGES). `occ` (uint8 [mz, my, mx], the trimmed box): 1 —
    a real node, 0 — «air»; an «air» cell that has a node (real or bridge) among its predecessors by `offs` ((Δi, Δj, m)
    rows of the bridge table) is set to 2. The levels go from the top down: a cell reads HIGHER levels only (m ≥ 1), so
    a chain of bridges is found in one pass and the answer does not depend on the order inside a level. Returns the
    number of bridges."""
    mz, my, mx = occ.shape
    n = 0
    ne = offs.shape[0]
    for k in range(mz - 2, -1, -1):
        for j in range(my):
            for i in range(mx):
                if occ[k, j, i] != 0:
                    continue
                for e in range(ne):
                    ii = i + offs[e, 0]
                    jj = j + offs[e, 1]
                    kk = k + offs[e, 2]
                    if ii < 0 or ii >= mx or jj < 0 or jj >= my or kk >= mz:
                        continue
                    if occ[kk, jj, ii] != 0:
                        occ[k, j, i] = 2
                        n += 1
                        break
    return n


@njit(cache=True, nogil=True)
def degrees(pos, tpl, index, tab_ptr, tab_off, mx, my, mz):  # type: ignore[no-untyped-def]
    """Number of arcs (u → predecessor) of every node."""
    n = pos.shape[0]
    deg = np.zeros(n, dtype=np.int64)
    mxy = mx * my
    for u in range(n):
        p = np.int64(pos[u])
        k = p // mxy
        r = p - k * mxy
        j = r // mx
        i = r - j * mx
        t = tpl[u]
        c = 0
        for e in range(tab_ptr[t], tab_ptr[t + 1]):
            ii = i + tab_off[e, 0]
            jj = j + tab_off[e, 1]
            kk = k + tab_off[e, 2]
            if ii < 0 or ii >= mx or jj < 0 or jj >= my or kk < 0 or kk >= mz:
                continue
            if index[ii + mx * (jj + my * kk)] >= 0:
                c += 1
        deg[u] = c
    return deg


@njit(cache=True, nogil=True)
def fill_arcs(pos, tpl, index, tab_ptr, tab_off, mx, my, mz, indptr, indices):  # type: ignore[no-untyped-def]
    """Fill `indices` (the predecessors of node u are `indices[indptr[u]:indptr[u + 1]]`, in table order)."""
    n = pos.shape[0]
    mxy = mx * my
    for u in range(n):
        p = np.int64(pos[u])
        k = p // mxy
        r = p - k * mxy
        j = r // mx
        i = r - j * mx
        t = tpl[u]
        w = indptr[u]
        for e in range(tab_ptr[t], tab_ptr[t + 1]):
            ii = i + tab_off[e, 0]
            jj = j + tab_off[e, 1]
            kk = k + tab_off[e, 2]
            if ii < 0 or ii >= mx or jj < 0 or jj >= my or kk < 0 or kk >= mz:
                continue
            q = index[ii + mx * (jj + my * kk)]
            if q >= 0:
                indices[w] = q
                w += 1


@njit(cache=True, nogil=True, parallel=True)
def cone_kernel(pos, tpl, index, tab_ptr, tab_off, mx, my, mz, level_ptr, needed):  # type: ignore[no-untyped-def]
    """`needed` (uint8, 1 at the seeds) → the union of the cones of the seeds, in place (bottom to top by levels)."""
    mxy = mx * my
    for lev in range(mz):
        for u in prange(level_ptr[lev], level_ptr[lev + 1]):
            if needed[u] == 0:
                continue
            p = np.int64(pos[u])
            k = p // mxy
            r = p - k * mxy
            j = r // mx
            i = r - j * mx
            t = tpl[u]
            for e in range(tab_ptr[t], tab_ptr[t + 1]):
                ii = i + tab_off[e, 0]
                jj = j + tab_off[e, 1]
                kk = k + tab_off[e, 2]
                if ii < 0 or ii >= mx or jj < 0 or jj >= my or kk < 0 or kk >= mz:
                    continue
                q = index[ii + mx * (jj + my * kk)]
                if q >= 0:
                    needed[q] = 1


@njit(cache=True, nogil=True)
def closure_violations(pos, tpl, index, tab_ptr, tab_off, mx, my, mz, mined):  # type: ignore[no-untyped-def]
    """(count of arcs u → q with u mined and q not, first such u, first such q); (0, −1, −1) — the set is closed."""
    n = pos.shape[0]
    mxy = mx * my
    bad = 0
    first_u = -1
    first_q = -1
    for u in range(n):
        if not mined[u]:
            continue
        p = np.int64(pos[u])
        k = p // mxy
        r = p - k * mxy
        j = r // mx
        i = r - j * mx
        t = tpl[u]
        for e in range(tab_ptr[t], tab_ptr[t + 1]):
            ii = i + tab_off[e, 0]
            jj = j + tab_off[e, 1]
            kk = k + tab_off[e, 2]
            if ii < 0 or ii >= mx or jj < 0 or jj >= my or kk < 0 or kk >= mz:
                continue
            q = index[ii + mx * (jj + my * kk)]
            if q >= 0 and not mined[q]:
                if bad == 0:
                    first_u = u
                    first_q = q
                bad += 1
    return bad, first_u, first_q


@njit(cache=True, nogil=True)
def nesting_violations(pos, tpl, index, tab_ptr, tab_off, mx, my, mz, lab):  # type: ignore[no-untyped-def]
    """(count, first u, first q, lab[u], lab[q]) over the arcs u → q with lab[u] > 0 and NOT 0 < lab[q] <= lab[u];
    (0, −1, −1, 0, 0) — the shells `lab` (the number of the smallest shell holding a node, 0 — in none) are nested and
    every one is closed. One pass over the arcs: the shell k is `0 < lab <= k`, so «every shell is closed» for all k at
    once is «for every arc, the shell of u from which u is mined holds q too»: the least such k is lab[u]."""
    n = pos.shape[0]
    mxy = mx * my
    bad = 0
    first_u = -1
    first_q = -1
    lu0 = 0
    lq0 = 0
    for u in range(n):
        lu = lab[u]
        if lu <= 0:
            continue
        p = np.int64(pos[u])
        k = p // mxy
        r = p - k * mxy
        j = r // mx
        i = r - j * mx
        t = tpl[u]
        for e in range(tab_ptr[t], tab_ptr[t + 1]):
            ii = i + tab_off[e, 0]
            jj = j + tab_off[e, 1]
            kk = k + tab_off[e, 2]
            if ii < 0 or ii >= mx or jj < 0 or jj >= my or kk < 0 or kk >= mz:
                continue
            q = index[ii + mx * (jj + my * kk)]
            if q >= 0:
                lq = lab[q]
                if lq <= 0 or lq > lu:
                    if bad == 0:
                        first_u = u
                        first_q = q
                        lu0 = lu
                        lq0 = lq
                    bad += 1
    return bad, first_u, first_q, lu0, lq0
