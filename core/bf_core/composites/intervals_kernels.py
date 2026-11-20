"""numba kernels of the interval engine (`composites/intervals.py`, ADR 0018 §4–5).

Data. «Lines»: interval rows ordered by (hole, FROM); `starts[h] : starts[h + 1]` are the rows of hole h; inside
a hole FROM[i] ≥ TO[i − 1] and TO > FROM, so TO ascends too. A second set of intervals (targets) is addressed
by its own holes; `src[h]` is the hole of the source lines with the same id, −1 — none.

Strips. A kernel with `h0, h1` handles those holes only and writes only their outputs (`strips.py`); `fill` =
False is the count pass (`counts[h]`), True — the fill pass (from `starts[h]`). All of them are compiled once:
the wrappers pass C-contiguous float64 / int64 / int32 arrays, Python ints, floats and bools.
`cache=True, nogil=True`; no `fastmath`, no `parallel`.

Sums. A sum over the samples of one target is taken in the order of the samples: blocks of 8 consecutive
overlapping samples are added left to right, the blocks are combined as a binary counter (a cascade, i.e.
pairwise), what is left is folded from the last block to the first. A sample without a value adds nothing —
the tree is the same for every field. Reference: `tests/test_composites_intervals.py::cascade`.
"""

from __future__ import annotations

import math

import numpy as np

from bf_core.jit import njit

REL = 1e-9  # relative tolerance of the comparisons, towards equality
DEPTH = 56  # levels of the cascade: 8 · 2**56 samples in a target
MODE_CODES = 32  # distinct codes of a target the buffers of `mode_strip` hold (more — larger ones for it)

# --- lines -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def rows_sorted(hole, frm, to, ids):  # type: ignore[no-untyped-def]
    """True when the rows with finite depths already go by (hole, FROM, TO, id)."""
    have = False
    ph = 0
    pf = 0.0
    pt = 0.0
    pi = 0
    for i in range(hole.shape[0]):
        f = frm[i]
        t = to[i]
        if not (math.isfinite(f) and math.isfinite(t)):
            continue
        h = hole[i]
        if have:
            if h < ph:
                return False
            if h == ph:
                if f < pf:
                    return False
                if f == pf and (t < pt or (t == pt and ids[i] < pi)):
                    return False
        have = True
        ph = h
        pf = f
        pt = t
        pi = ids[i]
    return True


@njit(cache=True, nogil=True)
def scan_rows(hole, frm, to, eps, keep):  # type: ignore[no-untyped-def]
    """keep[i] of rows sorted by (hole, FROM): False for a row without depths or with TO ≤ FROM (`bad`) and for
    one that overlaps the previous kept row of its hole by more than eps (`over`). Returns (bad, over)."""
    bad = 0
    over = 0
    have = False
    ph = 0
    pt = 0.0
    for i in range(hole.shape[0]):
        f = frm[i]
        t = to[i]
        if not (math.isfinite(f) and math.isfinite(t) and t > f):
            keep[i] = False
            bad += 1
            continue
        h = hole[i]
        if have and h == ph and (f < pt - eps or t <= pt):
            keep[i] = False
            over += 1
            continue
        keep[i] = True
        have = True
        ph = h
        pt = t
    return bad, over


@njit(cache=True, nogil=True)
def snap_rows(starts, frm, to):  # type: ignore[no-untyped-def]
    """FROM that is below the previous TO by float noise (≤ eps, kept by `scan_rows`) becomes that TO."""
    for h in range(starts.shape[0] - 1):
        for i in range(starts[h] + 1, starts[h + 1]):
            if frm[i] < to[i - 1]:
                frm[i] = to[i - 1]


@njit(cache=True, nogil=True)
def _first_over(to, lo, hi, x):  # type: ignore[no-untyped-def]
    """The first row of [lo, hi) with TO > x (TO ascends inside a hole)."""
    while lo < hi:
        mid = (lo + hi) >> 1
        if to[mid] > x:
            hi = mid
        else:
            lo = mid + 1
    return lo


# --- aggregate -------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def aggregate_strip(  # type: ignore[no-untyped-def]
    h0, h1, t_starts, t_src, t_frm, t_to, s_starts, s_frm, s_to, vals, w, rho, p_min, fill,
    out, out_rho, out_len, out_n, out_low,
):  # fmt: skip
    """Values of the targets of holes [h0, h1) (ТЗ 8.2.2). For target [c0; c1] and field f, over the samples
    with a value, l — the overlap: out = Σ l·w·g / Σ l·w; L_f = Σ l. `w`, `rho` — per sample or empty (w = 1;
    no density). A sample without a weight (NaN, ≤ 0) takes the mean weight of the target, Σ l·w / Σ l over
    its samples that have one; no weight in the whole target — w = 1. L_f < p_min·(c1 − c0) → `out_low` and,
    without `fill`, NaN. `fill` not NaN — the part of the target without a value takes it:
    out = (out·L_f + fill·(c1 − c0 − L_f)) / (c1 − c0). out_rho = Σ l·ρ / Σ l; out_len, out_n — length and
    number of the samples with a value in any field (no fields — of all the samples)."""
    nf = vals.shape[1]
    use_w = w.shape[0] > 0
    use_rho = rho.shape[0] > 0
    use_fill = fill == fill
    # per field: 0 Σ l·w·g, 1 Σ l·w (samples with a weight); 2 Σ l·g, 3 Σ l (without one); 4 Σ l
    acc = np.zeros((nf, 5))
    st = np.zeros((DEPTH, nf, 5))
    sc = np.zeros(5)  # 0 Σ l·ρ, 1 Σ l (with ρ); 2 Σ l (sampled); 3 Σ l·w, 4 Σ l (with a weight)
    ss = np.zeros((DEPTH, 5))
    for h in range(h0, h1):
        sh = t_src[h]
        r0 = 0
        r1 = 0
        if sh >= 0:
            r0 = s_starts[sh]
            r1 = s_starts[sh + 1]
        p = r0
        for t in range(t_starts[h], t_starts[h + 1]):
            c0 = t_frm[t]
            c1 = t_to[t]
            if p > r0 and s_to[p - 1] > c0:  # targets may come in any order: step back by bisection
                p = _first_over(s_to, r0, p, c0)
            while p < r1 and s_to[p] <= c0:
                p += 1
            for f in range(nf):
                for c in range(5):
                    acc[f, c] = 0.0
            for c in range(5):
                sc[c] = 0.0
            cnt = 0
            k = 0
            depth = 0
            i = p
            while i < r1 and s_frm[i] < c1:
                a = s_frm[i]
                if a < c0:
                    a = c0
                b = s_to[i]
                if b > c1:
                    b = c1
                ln = b - a
                if ln > 0.0:
                    lw = ln
                    if use_w:
                        lw = ln * w[i]
                    wok = lw > 0.0
                    if use_w and wok:
                        sc[3] += lw
                        sc[4] += ln
                    anyv = nf == 0
                    for f in range(nf):
                        v = vals[i, f]
                        if v == v:
                            anyv = True
                            acc[f, 4] += ln
                            if wok:
                                acc[f, 0] += lw * v
                                acc[f, 1] += lw
                            else:
                                acc[f, 2] += ln * v
                                acc[f, 3] += ln
                    if anyv:
                        sc[2] += ln
                        cnt += 1
                    if use_rho:
                        r = rho[i]
                        if r == r:
                            sc[0] += ln * r
                            sc[1] += ln
                    k += 1
                    if (k & 7) == 0:  # a block of 8 samples is full: push it on the cascade
                        m = k >> 3
                        while (m & 1) == 0:
                            depth -= 1
                            for f in range(nf):
                                for c in range(5):
                                    acc[f, c] = st[depth, f, c] + acc[f, c]
                            for c in range(5):
                                sc[c] = ss[depth, c] + sc[c]
                            m >>= 1
                        for f in range(nf):
                            for c in range(5):
                                st[depth, f, c] = acc[f, c]
                                acc[f, c] = 0.0
                        for c in range(5):
                            ss[depth, c] = sc[c]
                            sc[c] = 0.0
                        depth += 1
                i += 1
            while depth > 0:
                depth -= 1
                for f in range(nf):
                    for c in range(5):
                        acc[f, c] = st[depth, f, c] + acc[f, c]
                for c in range(5):
                    sc[c] = ss[depth, c] + sc[c]
            length = c1 - c0
            need = p_min * length * (1.0 - REL)
            low = nf == 0 and sc[2] < need
            for f in range(nf):
                num = acc[f, 0]
                den = acc[f, 1]
                if acc[f, 3] > 0.0:  # samples without a weight
                    if sc[4] > 0.0:
                        mean_w = sc[3] / sc[4]
                        num += mean_w * acc[f, 2]
                        den += mean_w * acc[f, 3]
                    else:
                        num = acc[f, 2]
                        den = acc[f, 3]
                lf = acc[f, 4]
                g = np.nan
                if den > 0.0:
                    g = num / den
                if lf < need:
                    low = True
                    if not use_fill:
                        g = np.nan
                if use_fill:
                    lu = length - lf
                    if lu > REL * length:
                        if g == g:
                            g = (g * lf + fill * lu) / (lf + lu)
                        else:
                            g = fill
                out[t, f] = g
            if sc[1] > 0.0:
                out_rho[t] = sc[0] / sc[1]
            else:
                out_rho[t] = np.nan
            out_len[t] = sc[2]
            out_n[t] = cnt
            out_low[t] = low


@njit(cache=True, nogil=True)
def _mode(c0, c1, p, r1, s_frm, s_to, codes, seen, lens):  # type: ignore[no-untyped-def]
    """The code with the greatest total overlap with [c0; c1] among the rows p ≤ i < r1 (the first of them
    reaches below c0); −2 — more distinct codes than the buffers `seen`, `lens` hold."""
    m = 0
    i = p
    while i < r1 and s_frm[i] < c1:
        c = codes[i]
        a = s_frm[i]
        if a < c0:
            a = c0
        b = s_to[i]
        if b > c1:
            b = c1
        ln = b - a
        if c >= 0 and ln > 0.0:
            j = 0
            while j < m and seen[j] != c:
                j += 1
            if j < m:
                lens[j] += ln
            elif m == seen.shape[0]:
                return -2
            else:
                seen[m] = c
                lens[m] = ln
                m += 1
        i += 1
    best = -1
    most = 0.0
    tol = REL * (c1 - c0)
    for j in range(m):
        if best < 0 or lens[j] > most + tol:
            best = seen[j]
            most = lens[j]
    return best


@njit(cache=True, nogil=True)
def mode_strip(h0, h1, t_starts, t_src, t_frm, t_to, s_starts, s_frm, s_to, codes, out):  # type: ignore[no-untyped-def]
    """out[t] — the code with the greatest total overlap with target t; equal lengths (within 1e−9 of the
    target) — the code met first; −1 (empty) takes no part; no code at all — −1."""
    seen = np.empty(MODE_CODES, np.int32)
    lens = np.empty(MODE_CODES, np.float64)
    for h in range(h0, h1):
        sh = t_src[h]
        r0 = 0
        r1 = 0
        if sh >= 0:
            r0 = s_starts[sh]
            r1 = s_starts[sh + 1]
        p = r0
        for t in range(t_starts[h], t_starts[h + 1]):
            c0 = t_frm[t]
            c1 = t_to[t]
            if p > r0 and s_to[p - 1] > c0:
                p = _first_over(s_to, r0, p, c0)
            while p < r1 and s_to[p] <= c0:
                p += 1
            best = _mode(c0, c1, p, r1, s_frm, s_to, codes, seen, lens)
            cap = MODE_CODES
            while best == -2:  # a target with very many codes: its own, larger buffers
                cap *= 4
                best = _mode(c0, c1, p, r1, s_frm, s_to, codes, np.empty(cap, np.int32), np.empty(cap, np.float64))
            out[t] = best


# --- overlay, runs ---------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def overlay_strip(  # type: ignore[no-untyped-def]
    h0, h1, ha, hb, a_starts, a_frm, a_to, b_starts, b_frm, b_to, how, eps, counts, starts,
    o_frm, o_to, o_ia, o_ib, fill,
):  # fmt: skip
    """Sub-intervals between the boundaries of two sets of intervals of holes [h0, h1) with the rows that
    cover them (−1 — none). how: 0 — covered by a or b, 1 — by a, 2 — by both. Slivers ≤ eps are left out."""
    for h in range(h0, h1):
        i = 0
        a1 = 0
        if ha[h] >= 0:
            i = a_starts[ha[h]]
            a1 = a_starts[ha[h] + 1]
        j = 0
        b1 = 0
        if hb[h] >= 0:
            j = b_starts[hb[h]]
            b1 = b_starts[hb[h] + 1]
        x = -np.inf
        n = 0
        q = 0
        if fill:
            q = starts[h]
        while i < a1 or j < b1:
            fa = np.inf
            if i < a1:
                fa = a_frm[i]
            fb = np.inf
            if j < b1:
                fb = b_frm[j]
            s = min(fa, fb)
            if s < x:
                s = x
            ca = fa <= s
            cb = fb <= s
            e = fa
            if ca:
                e = a_to[i]
            e2 = fb
            if cb:
                e2 = b_to[j]
            if e2 < e:
                e = e2
            emit = how == 0 or (how == 1 and ca) or (ca and cb)
            if emit and e - s > eps:
                if fill:
                    o_frm[q] = s
                    o_to[q] = e
                    o_ia[q] = i if ca else -1
                    o_ib[q] = j if cb else -1
                    q += 1
                n += 1
            x = e
            if ca and a_to[i] <= e:
                i += 1
            if cb and b_to[j] <= e:
                j += 1
        if not fill:
            counts[h] = n


@njit(cache=True, nogil=True)
def runs_strip(h0, h1, starts, frm, to, codes, gap, counts, r_starts, o_frm, o_to, o_code, o_first, o_last, fill):  # type: ignore[no-untyped-def]
    """Runs of holes [h0, h1): consecutive rows with one code and no gap wider than `gap` between them."""
    for h in range(h0, h1):
        r0 = starts[h]
        r1 = starts[h + 1]
        n = 0
        q = 0
        if fill:
            q = r_starts[h]
        i = r0
        while i < r1:
            j = i + 1
            while j < r1 and codes[j] == codes[i] and frm[j] - to[j - 1] <= gap:
                j += 1
            if fill:
                o_frm[q] = frm[i]
                o_to[q] = to[j - 1]
                o_code[q] = codes[i]
                o_first[q] = i
                o_last[q] = j
                q += 1
            n += 1
            i = j
        if not fill:
            counts[h] = n


# --- targets ---------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _cut(a, b, o, length, equal, f_min, attach, o_frm, o_to, q, fill, info):  # type: ignore[no-untyped-def]
    """Composites of the stretch [a; b] (ТЗ 8.2.2), written from o_frm[q] when `fill`; returns their number.
    `o` — the depth the grid of `length` is counted from (NaN or a — from the start of the stretch).
    The count pass adds to info: [0] stretches left without a composite, [1] their length, [2] remainders
    dropped, [3] their length."""
    d = b - a
    if not (d > 0.0):
        return 0
    if equal:
        n = int(math.floor(d / length + 0.5))
        if n < 1:
            n = 1
        if fill:
            for k in range(n):
                o_frm[q + k] = a if k == 0 else a + d * k / n
                o_to[q + k] = b if k == n - 1 else a + d * (k + 1) / n
        return n
    tol = REL * length
    thr = f_min * length * (1.0 - REL)
    g0 = a  # the first boundary of the grid inside the stretch
    lead = 0.0  # the piece above it
    if o == o and abs(o - a) > tol:
        g0 = o + math.ceil((a - o) / length - REL) * length
        lead = g0 - a
        if lead <= tol:
            lead = 0.0
            g0 = a
    one = False  # the whole stretch is one composite
    none = False  # … or is left out
    n0 = 0
    r = 0.0
    if lead > 0.0 and g0 >= b - tol:  # no boundary of the grid inside the stretch
        one = d >= thr
        none = not one
    else:
        body = b - g0
        n0 = int(math.floor(body / length + REL))
        r = body - n0 * length
        if r <= tol:
            r = 0.0
    lead_own = lead > 0.0 and lead >= thr
    tail_own = r > 0.0 and r >= thr
    head = 0  # a composite of the lead piece
    tail = 0  # a composite of the remainder
    first = a
    last = b
    if not (one or none):
        if n0 == 0:
            if lead == 0.0:
                one = tail_own
                none = not one
            elif lead_own and tail_own:
                head = 1
                tail = 1
            elif attach:
                one = lead_own or tail_own or d >= thr
                none = not one
            elif lead_own:
                head = 1
                if not fill:
                    info[2] += 1.0
                    info[3] += r
            elif tail_own:
                tail = 1
                if not fill:
                    info[2] += 1.0
                    info[3] += lead
            else:
                none = True
        else:
            if lead > 0.0:
                if lead_own:
                    head = 1
                elif not attach:
                    first = g0
                    if not fill:
                        info[2] += 1.0
                        info[3] += lead
            if r > 0.0:
                if tail_own:
                    tail = 1
                elif not attach:
                    last = g0 + n0 * length
                    if not fill:
                        info[2] += 1.0
                        info[3] += r
    if none:
        if not fill:
            info[0] += 1.0
            info[1] += d
        return 0
    if one:
        if fill:
            o_frm[q] = a
            o_to[q] = b
        return 1
    if fill:
        if head == 1:
            o_frm[q] = a
            o_to[q] = g0
            q += 1
        for k in range(n0):
            lo = g0 + k * length
            if k == 0 and head == 0:
                lo = first
            hi = g0 + (k + 1) * length
            if k == n0 - 1 and tail == 0:
                hi = last
            o_frm[q] = lo
            o_to[q] = hi
            q += 1
        if tail == 1:
            o_frm[q] = g0 + n0 * length
            o_to[q] = b
    return head + n0 + tail


@njit(cache=True, nogil=True)
def targets_strip(  # type: ignore[no-untyped-def]
    h0, h1, s_starts, s_frm, s_to, s_org, length, equal, f_min, attach, counts, t_starts,
    o_frm, o_to, o_stretch, fill, info,
):  # fmt: skip
    """Composites of the stretches of holes [h0, h1): count pass → counts[h], info[0:4] (see `_cut`); fill
    pass → o_frm, o_to, o_stretch from t_starts[h]. `s_org` — per stretch or empty (from its start)."""
    use_org = s_org.shape[0] > 0
    for h in range(h0, h1):
        n = 0
        q = 0
        if fill:
            q = t_starts[h]
        for s in range(s_starts[h], s_starts[h + 1]):
            o = np.nan
            if use_org:
                o = s_org[s]
            m = _cut(s_frm[s], s_to[s], o, length, equal, f_min, attach, o_frm, o_to, q, fill, info)
            if fill:
                for k in range(m):
                    o_stretch[q + k] = s
                q += m
            n += m
        if not fill:
            counts[h] = n
