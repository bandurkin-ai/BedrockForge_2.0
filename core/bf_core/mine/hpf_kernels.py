"""Numba kernels of the HPF solver of the maximum closure (подшаг 21.5; ТЗ 8.11.3; ADR 0028 decisions 14–16).

Hochbaum's PSEUDOFLOW algorithm in the form of Hochbaum–Orlin 2013 («Simplifications and speedups of the pseudoflow
algorithm», §3, the maximum PREFLOW problem), the LOWEST-label order, written for the closure problem. The network:
a node u with v′ > 0 is a SUPPLY b(u) = v′ (the excess it starts with), a node with v′ < 0 has an arc u → t of the
capacity −v′, a precedence arc u → q (q is a predecessor of u: it must be mined first) has an infinite capacity. The
arcs are NOT stored: the predecessors of a node are built on the fly from the offset table of the node and the dense
index of the graph (`mode = 0`), or read from a CSR (`mode = 1`, «explicit arcs» of MineLib). The only flows kept are
those on the arcs of the forest (zero on the other arcs; the sink arc of a node that is not in the forest is either
untouched or saturated — the flag `TSAT`).

State (arrays of n + 1 nodes, the node n is the sink t; the same arrays between portions of work):
  `a32[u]` — int32 columns PAR (parent, −1 — a root), FIRST / NXT / PRV (the children list of u, doubly linked),
             LAB (the distance label d(u) ≥ 1; d(t) = 0), NARC (the arcs of u already scanned at this label: the
             relative index, `deg(u)` — the sink arc is next, `deg(u) + 1` — all done), CUR (the DFS pointer),
             BNX (the next root in the bucket of its label);
  `a64[u]` — int64 columns EX (the excess; non-zero for roots only) and FLOW (the flow of the arc to the parent);
  `fl[u]`  — uint8 columns ORI (the arc to the parent: 1 — the original arc u → parent, 0 — parent → u, 2 — the sink
             arc u → t) and TSAT (the sink arc of u is saturated and u is out of the tree of t).
A tree arc has a positive flow and a positive residual capacity in the direction of the push; it is cut when a push
saturates it. Roots with a positive excess and d ≤ n are ACTIVE; they sit in buckets by label (`head[d]`).

One step (`run`, the lowest label d first): scan the nodes of the NEUTRAL subtree of the root (nodes joined to it by
tree paths of the label d) for an admissible arc (u, q): d(q) = d − 1. Found: invert the tree path root → u, hang u
under q (`merge`), push the excess along the path up to the root of q; a saturated tree arc is cut and its upper node
stays a root with the rest of the excess. Not found: lift every node of the neutral subtree by one (`relabel`); when
no node keeps the label d (a GAP) every node above it can never reach t, and with the lowest label first the work is
over. At the end the strong trees (roots with excess > 0) are the source set of a minimum cut — the SMALLEST one
(Hochbaum 2008); `extract` takes it as the nodes reachable from the strong roots in the residual network (precedence
arcs and the reverse of the tree arcs with a flow), which is the answer of OR-Tools bit for bit.

`gr` is the tuple (pos, tpl, index, tab_ptr, tab_off, mx, my, mz, indptr, indices): the arrays of the graph of 21.4
(`mode = 0`) or the CSR of the arcs (`mode = 1`; the unused arrays are dummies).

`run` works by portions: it returns after the root being processed when `work ≥ budget` (nothing is stored in a DFS
in progress), so the answer does not depend on the size of the portions. Everything is single-threaded and uses
only the order of nodes and of the offset tables: the same answer on every run.
"""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit

INF = 1 << 62  # the residual capacity of a precedence arc (Σ v′ < 2⁶² is checked by the caller)

# a32 columns
PAR, FIRST, NXT, PRV, LAB, NARC, CUR, BNX = 0, 1, 2, 3, 4, 5, 6, 7
NCOL32 = 8
# a64 columns
EX, FLOW = 0, 1
# fl columns
ORI, TSAT = 0, 1
# ctrl cells (int64)
C_LOW, C_GAP, C_WORK, C_ROOTS, C_MERGE, C_RELAB, C_ARCS, C_ACT, C_TFLOW, C_DONE = 0, 1, 2, 3, 4, 5, 6, 7, 8, 9
NCTRL = 10


@njit(cache=True, nogil=True)
def init_state(n, value, a32, a64, fl, cnt, head, ctrl):  # type: ignore[no-untyped-def]
    """Every node is a singleton tree with the label 1 and the excess max(v′, 0); the sink t = n has the label 0."""
    for u in range(n + 1):
        a32[u, PAR] = -1
        a32[u, FIRST] = -1
        a32[u, NXT] = -1
        a32[u, PRV] = -1
        a32[u, LAB] = 1
        a32[u, NARC] = 0
        a32[u, CUR] = -1
        a32[u, BNX] = -1
        a64[u, EX] = 0
        a64[u, FLOW] = 0
        fl[u, ORI] = 0
        fl[u, TSAT] = 0
    a32[n, LAB] = 0
    for d in range(n + 2):
        head[d] = -1
        cnt[d] = 0
    cnt[0] = 1
    cnt[1] = n
    act = 0
    for u in range(n - 1, -1, -1):  # the lowest node pops first
        v = value[u]
        if v > 0:
            a64[u, EX] = v
            act += v
            a32[u, BNX] = head[1]
            head[1] = u
    ctrl[C_LOW] = 1
    ctrl[C_GAP] = n
    ctrl[C_ACT] = act
    ctrl[C_DONE] = 0


# --- the forest ---------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _unlink(a32, p, v):  # type: ignore[no-untyped-def]
    """Remove v from the children list of its parent p."""
    nx = a32[v, NXT]
    pv = a32[v, PRV]
    if pv >= 0:
        a32[pv, NXT] = nx
    else:
        a32[p, FIRST] = nx
    if nx >= 0:
        a32[nx, PRV] = pv


@njit(cache=True, nogil=True)
def _link(a32, p, v):  # type: ignore[no-untyped-def]
    """Make v the first child of p."""
    f = a32[p, FIRST]
    a32[v, NXT] = f
    a32[v, PRV] = -1
    if f >= 0:
        a32[f, PRV] = v
    a32[p, FIRST] = v
    a32[v, PAR] = p


@njit(cache=True, nogil=True)
def _activate(n, v, a32, head, ctrl):  # type: ignore[no-untyped-def]
    """A root v with a positive excess enters the bucket of its label (a label > n is dead: the network of n nodes and
    the sink holds a path of at most n arcs to t, so a label can reach n — a chain through every node)."""
    d = a32[v, LAB]
    if d <= n:
        a32[v, BNX] = head[d]
        head[d] = v
        if d < ctrl[C_LOW]:
            ctrl[C_LOW] = d


# --- arcs ---------------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _scan_node(u, d, n, mode, a32, fl, value, gr, ctrl):  # type: ignore[no-untyped-def]
    """The next admissible arc (u, q) — d(q) = d − 1 — from the pointer of u: q, or −1. The sink arc is q = n."""
    pos, tpl, index, tab_ptr, tab_off, mx, my, mz, indptr, indices = gr
    a = a32[u, NARC]
    target = d - 1
    scanned = 0
    if mode == 0:
        t = tpl[u]
        e0 = tab_ptr[t]
        deg = tab_ptr[t + 1] - e0
        if a < deg:
            p = np.int64(pos[u])
            mxy = mx * my
            k = p // mxy
            r = p - k * mxy
            j = r // mx
            i = r - j * mx
            while a < deg:
                e = e0 + a
                a += 1
                scanned += 1
                ii = i + tab_off[e, 0]
                jj = j + tab_off[e, 1]
                kk = k + tab_off[e, 2]
                if ii < 0 or ii >= mx or jj < 0 or jj >= my or kk < 0 or kk >= mz:
                    continue
                q = index[ii + mx * (jj + my * kk)]
                if q >= 0 and a32[q, LAB] == target:
                    a32[u, NARC] = a
                    ctrl[C_ARCS] += scanned
                    return q
    else:
        s0 = indptr[u]
        deg = indptr[u + 1] - s0
        while a < deg:
            q = indices[s0 + a]
            a += 1
            scanned += 1
            if a32[q, LAB] == target:
                a32[u, NARC] = a
                ctrl[C_ARCS] += scanned
                return q
    ctrl[C_ARCS] += scanned
    if a == deg:  # the sink arc (u, t): admissible at the label 1 while it is not saturated
        a = deg + 1
        a32[u, NARC] = a
        if d == 1 and value[u] < 0 and fl[u, TSAT] == 0:
            return n
    return -1


# --- one root -----------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _find_merger(i, d, n, mode, a32, fl, value, stack, vis, ctrl, gr):  # type: ignore[no-untyped-def]
    """DFS over the neutral subtree of the root i (label d) for an admissible arc. The stack holds the path
    i → … → u at the end. Returns (u, q, depth of the path, nodes visited); u = −1 — there is no arc."""
    stack[0] = i
    sp = 1
    a32[i, CUR] = a32[i, FIRST]
    vis[0] = i
    nv = 1
    q = _scan_node(i, d, n, mode, a32, fl, value, gr, ctrl)
    if q >= 0:
        return i, q, sp, nv
    while sp > 0:
        u = stack[sp - 1]
        c = a32[u, CUR]
        while c >= 0 and a32[c, LAB] != d:
            c = a32[c, NXT]
        if c < 0:
            sp -= 1
            continue
        a32[u, CUR] = a32[c, NXT]
        stack[sp] = c
        sp += 1
        a32[c, CUR] = a32[c, FIRST]
        vis[nv] = c
        nv += 1
        q = _scan_node(c, d, n, mode, a32, fl, value, gr, ctrl)
        if q >= 0:
            return c, q, sp, nv
    return -1, -1, 0, nv


@njit(cache=True, nogil=True)
def _merge_push(n, i, j, q, sp, a32, a64, fl, value, stack, head, ctrl):  # type: ignore[no-untyped-def]
    """Invert the tree path i → j (stack[0:sp]), hang j under q and push the excess of i up to the root of q."""
    # invert: the node stack[m] becomes a child of stack[m + 1] with the same arc seen from the other side
    for m in range(sp - 1):
        c = stack[m + 1]
        v = stack[m]
        _unlink(a32, v, c)
        o = fl[c, ORI]
        f = a64[c, FLOW]
        _link(a32, c, v)
        fl[v, ORI] = 1 - o
        a64[v, FLOW] = f
    _link(a32, q, j)
    fl[j, ORI] = 2 if q == n else 1
    a64[j, FLOW] = 0
    cur = i
    steps = 0
    while True:
        e = a64[cur, EX]
        p = a32[cur, PAR]
        if e <= 0 or p < 0:
            break
        steps += 1
        o = fl[cur, ORI]
        if o == 1:
            r = INF
        elif o == 0:
            r = a64[cur, FLOW]
        else:
            r = -value[cur] - a64[cur, FLOW]
        if r <= e:  # the push saturates the arc: it leaves the forest, cur keeps the rest
            qq = r
            sat = True
        else:
            qq = e
            sat = False
        prev = a64[p, EX]
        a64[cur, EX] = e - qq
        a64[p, EX] = prev + qq
        if o == 0:
            a64[cur, FLOW] -= qq
        else:
            a64[cur, FLOW] += qq
        if sat:
            _unlink(a32, p, cur)
            a32[cur, PAR] = -1
            a64[cur, FLOW] = 0
            if o == 2:
                fl[cur, TSAT] = 1
            if a64[cur, EX] > 0:
                _activate(n, cur, a32, head, ctrl)
                ctrl[C_ACT] += a64[cur, EX]
        if p == n:
            ctrl[C_TFLOW] += qq
        elif a32[p, PAR] < 0 and prev <= 0 and qq > 0:  # a quiet root got an excess: it is active now
            _activate(n, p, a32, head, ctrl)
            ctrl[C_ACT] += a64[p, EX]
        elif a32[p, PAR] < 0 and prev > 0:
            ctrl[C_ACT] += qq  # the root sits in a bucket already (its excess only grows)
        cur = p
    return steps


@njit(cache=True, nogil=True)
def _relabel(i, d, nv, n, a32, vis, cnt, head, ctrl):  # type: ignore[no-untyped-def]
    """Lift the neutral subtree of the root i (the nodes vis[0:nv]) from d to d + 1; the root returns to the buckets."""
    for k in range(nv):
        u = vis[k]
        a32[u, LAB] = d + 1
        a32[u, NARC] = 0
    cnt[d] -= nv
    cnt[d + 1] += nv
    if cnt[d] == 0 and d < ctrl[C_GAP]:
        ctrl[C_GAP] = d  # a gap: the labels above d are dead
    _activate(n, i, a32, head, ctrl)


@njit(cache=True, nogil=True)
def run(budget, n, mode, a32, a64, fl, value, cnt, head, stack, vis, ctrl, gr):  # type: ignore[no-untyped-def]
    """Process the active roots, the lowest label first, until the work reaches `budget` (the check is made after a
    whole root) or no active root is left. Returns 1 when the work is done, 0 when the budget is spent."""
    work = ctrl[C_WORK]
    stop = work + budget
    while True:
        gap = ctrl[C_GAP]
        lim = gap if gap < n else n
        low = ctrl[C_LOW]
        while low <= lim and head[low] < 0:
            low += 1
        ctrl[C_LOW] = low
        if low > lim:
            ctrl[C_DONE] = 1
            ctrl[C_WORK] = work
            return 1
        if work >= stop:
            ctrl[C_WORK] = work
            return 0
        i = head[low]
        head[low] = a32[i, BNX]
        if a32[i, PAR] >= 0 or a64[i, EX] <= 0 or a32[i, LAB] != low:
            continue  # a stale entry
        ctrl[C_ACT] -= a64[i, EX]
        ctrl[C_ROOTS] += 1
        u, q, sp, nv = _find_merger(i, low, n, mode, a32, fl, value, stack, vis, ctrl, gr)
        if u >= 0:
            ctrl[C_MERGE] += 1
            work += nv + _merge_push(n, i, u, q, sp, a32, a64, fl, value, stack, head, ctrl) + 1
        else:
            ctrl[C_RELAB] += 1
            _relabel(i, low, nv, n, a32, vis, cnt, head, ctrl)
            ctrl[C_ACT] += a64[i, EX]
            work += nv + 1


# --- the answer ---------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def extract(n, mode, a32, a64, fl, queue, mask, gr):  # type: ignore[no-untyped-def]
    """mask[u] = 1 for the nodes reachable from the roots with a positive excess in the residual network: the
    precedence arcs u → q, and the reverse of the tree arcs that carry a flow. Returns the number of nodes."""
    pos, tpl, index, tab_ptr, tab_off, mx, my, mz, indptr, indices = gr
    qh = 0
    qt = 0
    for u in range(n):
        if a32[u, PAR] < 0 and a64[u, EX] > 0:
            mask[u] = 1
            queue[qt] = u
            qt += 1
    mxy = mx * my
    while qh < qt:
        a = queue[qh]
        qh += 1
        # forward arcs: a needs its predecessors
        if mode == 0:
            t = tpl[a]
            e0 = tab_ptr[t]
            deg = tab_ptr[t + 1] - e0
            p = np.int64(pos[a])
            k = p // mxy
            r = p - k * mxy
            j = r // mx
            i = r - j * mx
            for e in range(e0, e0 + deg):
                ii = i + tab_off[e, 0]
                jj = j + tab_off[e, 1]
                kk = k + tab_off[e, 2]
                if ii < 0 or ii >= mx or jj < 0 or jj >= my or kk < 0 or kk >= mz:
                    continue
                q = index[ii + mx * (jj + my * kk)]
                if q >= 0 and mask[q] == 0:
                    mask[q] = 1
                    queue[qt] = q
                    qt += 1
        else:
            for e in range(indptr[a], indptr[a + 1]):
                q = indices[e]
                if mask[q] == 0:
                    mask[q] = 1
                    queue[qt] = q
                    qt += 1
        # reverse arcs of the tree arcs with a flow
        p2 = a32[a, PAR]
        if p2 >= 0 and p2 < n and fl[a, ORI] == 0 and a64[a, FLOW] > 0 and mask[p2] == 0:
            mask[p2] = 1  # the arc parent → a carries a flow: a in the set pulls its parent in
            queue[qt] = p2
            qt += 1
        c = a32[a, FIRST]
        while c >= 0:
            if fl[c, ORI] == 1 and a64[c, FLOW] > 0 and mask[c] == 0:
                mask[c] = 1  # the arc c → a carries a flow: a in the set pulls the child in
                queue[qt] = c
                qt += 1
            c = a32[c, NXT]
    return qt
