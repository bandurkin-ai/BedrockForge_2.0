"""Ore intervals by the ГКЗ method (ТЗ 7.35 Д-26, ADR 0018 §8–9): steps 0–1 — sampling lines, elementary
units, the sums and the comparisons every later step uses; the growth of the «упрощённый» mode; events.

Step 0 — pieces and lines. The rows of a hole go by FROM. A row with TO ≤ FROM or without depths and a row
that overlaps the previous one do not take part (counted, never silently). A «piece» is a sample with a grade
and a thickness l > 0 (the length TO − FROM or a field). A sample without a grade or a thickness, a stretch
without samples, a change of the domain and a sample outside the domains (code < 0) end the «sampling line» —
a missing value never becomes zero. Only with `bridge` (пропуски — «пустая порода до L_н») a run of missing
pieces between two samples of one line, not longer than `missing_max` along the hole, stays in the line as
waste with g = 0 and the flag `p_miss`; a longer run breaks the line. The thickness of a bridged stretch
without samples is its length along the hole; of a bridged sample — its thickness, else its length.

Step 1 — elementary units: runs of pieces with g ≥ Сб (ore, Р) and g < Сб (waste, Б); equality is ore.

Sums. M(A) = Σ l, MC(A) = Σ l·g over the pieces of a unit, in their order; a fragment of several units adds
the sums of its units left to right. Nothing is ever a difference of running totals (ADR 0018, находка 9).

Comparisons (`ge`, `le`) carry the relative tolerance TOL towards equality: C ≥ Сб, M ≤ Мпп, M ≥ Мрт,
MC ≥ МС, C ≥ Смин hold at equality within 1e−9; «C < Сб» and «M > Мпп» are their negations.

A unit series of a line: `ub[k]` — the first piece of unit k (`ub[nu]` — the end of the line), `uo[k]` — 1 ore,
0 waste, `um[k]`, `umc[k]` — M and MC. Units alternate ore / waste up to step 2; steps 3–4 (`ore_steps`) cut
the units, and an excluded fragment «порода … порода» is a unit of its own (`uo[k]` = U_LAYER).

Events (ADR 0018 §9) — one record per decision, two parallel arrays `ev_i[cap, EI_COLS]` (int64) and
`ev_f[cap, EF_COLS]` (float64); a kernel returns the number of events it wanted to write and writes while
there is room (cap = 0 — events are off, the batch over a set of holes).

One specialization of every kernel (ADR 0018 §5). A helper called from many places with constants in its
arguments (`emit`, `emit_frag`; `_put_row`, `_put_unit` of the other modules) carries its signature: numba
otherwise compiles it anew for every pattern of constants («Literal[int](0)» is a type of its own) — 13 copies
of `emit` and 8 of `_put_row`, 21 s of the first compilation instead of 9. For the same reason a counter that
a kernel hands on starts as `np.int64(0)`, not as the constant 0.
"""

from __future__ import annotations

import math

from numba import float64, int64

from bf_core.jit import njit

TOL = 1e-9  # relative tolerance of the comparisons, towards equality (ADR 0018 §5)
GAP_EPS = 1e-6  # m: FROM within this of the previous TO — the samples are adjacent

# classes of samples and intervals (CLASS, ORE_CLS)
CLS_NONE = -1  # a sample outside the sampling lines
CLS_ORE, CLS_SUBGRADE, CLS_WASTE_IN, CLS_INTERLAYER, CLS_WASTE, CLS_FRAME = 0, 1, 2, 3, 4, 5
CLASSES = ("ore", "subgrade", "waste_in", "interlayer", "waste", "frame")
BASIS_NONE, BASIS_THICKNESS, BASIS_MC = 0, 1, 2
BASES = ("", "thickness", "mc")
MODE_STRICT_EXCL, MODE_STRICT, MODE_SOFT, MODE_SIMPLE, MODE_CONTACTS = 0, 1, 2, 3, 4
MODES = ("strict_excl", "strict", "soft", "simple", "contacts")

U_WASTE, U_ORE, U_LAYER = 0, 1, 2  # a unit of the series: waste, ore, an excluded fragment «порода … порода»

# state of a row of the samples
ROW_PIECE, ROW_BAD, ROW_OVERLAP, ROW_MISSING, ROW_NO_DOMAIN, ROW_BRIDGED = 0, 1, 2, 3, 4, 5
ROW_STATES = ("piece", "bad", "overlap", "missing", "no_domain", "bridged")
# counters of step 0: counts[C_*]
C_BAD, C_OVERLAP, C_MISSING, C_NO_DOMAIN, C_BRIDGED, C_BREAKS, C_COLS = 0, 1, 2, 3, 4, 5, 6

# events: integer columns
EI_HOLE, EI_LINE, EI_STEP, EI_STAGE, EI_KIND, EI_SIDE, EI_ORE, EI_CMP, EI_DEC, EI_ROW0, EI_ROW1, EI_SEQ = range(12)
EI_COLS = 12
# events: float columns — depths of the fragment, its M, C and MC (NaN — not applicable)
EF_FROM, EF_TO, EF_M, EF_C, EF_MC = range(5)
EF_COLS = 5

# step of the algorithm (ADR 0018 §9)
STEP_BREAK, STEP_UNIT, STEP_TRIM, STEP_LAYER, STEP_NEAR, STEP_CLASS, STEP_EDIT = 0, 1, 2, 3, 4, 5, 6
# what the record is about
K_BREAK = 0  # the line ends (or a missing stretch is bridged)
K_UNIT = 1  # a unit of the series: elementary (step 1, N / IN₀) or made at a stage (step 2, IN₁…)
K_TRIM = 2  # a прирезка: Р_в ∪ Б (side upper) or Б ∪ Р_н (side lower); in «упрощённый» — the grown interval
K_GAP = 3  # the waste unit between Р_в and Р_н against Мпп
K_LAYER = 4  # a fragment «порода … порода» with C < Сб and M > Мпп
K_CHAIN = 5  # a chain of close intervals (step 4)
K_INTERVAL = 6  # the class of an interval (step 5)
KINDS = ("break", "unit", "trim", "gap", "layer", "chain", "interval")
SIDE_NONE, SIDE_UPPER, SIDE_LOWER = 0, 1, 2  # висячий бок — upper, лежачий бок — lower
SIDES = ("", "upper", "lower")
# what the value was compared with
CMP_NONE, CMP_CB, CMP_MPP, CMP_MRT, CMP_MC, CMP_CMIN, CMP_K, CMP_LAYER = 0, 1, 2, 3, 4, 5, 6, 7
R_NAN, R_GAP, R_DOMAIN, R_MISSING_MAX = 10, 11, 12, 13  # why a line ended
COMPARED = {
    CMP_NONE: "",
    CMP_CB: "cutoff",
    CMP_MPP: "m_waste_max",
    CMP_MRT: "m_min",
    CMP_MC: "mc_min",
    CMP_CMIN: "c_min",
    CMP_K: "k",
    CMP_LAYER: "layer",  # a fragment «порода … порода» would appear (step 4, strict modes)
    R_NAN: "no_value",
    R_GAP: "gap",
    R_DOMAIN: "domain",
    R_MISSING_MAX: "missing_max",
}
# decision
D_NONE = 0  # a record of a unit, nothing decided
D_PASS = 1  # the condition holds (C ≥ Сб of one прирезка)
D_JOINED = 2  # присоединён
D_REJ_C = 3  # отклонён — C < Сб
D_REJ_M = 4  # отклонён — M > Мпп
D_DEMOTED = 5  # некондиционный → безрудный
D_LAYER_OUT = 6  # прослой исключён
D_TORN = 7  # разорван
D_NEAR_JOINED = 8  # сближенные объединены
D_NOT_CHECKED = 9  # не проверяется в этом режиме
D_BREAK = 10  # the sampling line ends here
D_MISSING_WASTE = 11  # a missing stretch counted as waste (flag)
D_ORE = 12  # кондиционный
D_SUBGRADE = 13  # некондиционный
D_NOT_SELECTED = 14  # не выбран: a fragment of step 3 that leaves no кондиционный interval above; a chain of step 4
DECISIONS = (
    "",
    "pass",
    "joined",
    "rejected_c",
    "rejected_m",
    "demoted",
    "interlayer",
    "torn",
    "near_joined",
    "not_checked",
    "break",
    "missing_waste",
    "ore",
    "subgrade",
    "not_selected",
)


@njit(cache=True, nogil=True)
def ge(a, b):  # type: ignore[no-untyped-def]
    """a ≥ b; equal within TOL counts as equal."""
    return a >= b - TOL * max(abs(a), abs(b))


@njit(cache=True, nogil=True)
def le(a, b):  # type: ignore[no-untyped-def]
    """a ≤ b; equal within TOL counts as equal."""
    return a <= b + TOL * max(abs(a), abs(b))


@njit(cache=True, nogil=True)
def cond_k(m, mc, c_min, m_min, mc_min):  # type: ignore[no-untyped-def]
    """Условие К: C ≥ Смин and (M ≥ Мрт or MC ≥ МС). Returns BASIS_THICKNESS, BASIS_MC or 0 — not met."""
    if not (m > 0.0) or not ge(mc / m, c_min):
        return BASIS_NONE
    if ge(m, m_min):
        return BASIS_THICKNESS
    if ge(mc, mc_min):
        return BASIS_MC
    return BASIS_NONE


_EV = (int64[:, ::1], float64[:, ::1])  # the arrays of the events


@njit((*_EV, *(int64,) * 13, *(float64,) * 4), cache=True, nogil=True)
def emit(  # type: ignore[no-untyped-def]
    ev_i, ev_f, ne, hole, line, step, stage, kind, side, ore, cmp, dec, row0, row1, seq, f, t, m, mc
):
    """Writes event `ne` when there is room; returns ne + 1."""
    if ne < ev_i.shape[0]:
        ev_i[ne, EI_HOLE] = hole
        ev_i[ne, EI_LINE] = line
        ev_i[ne, EI_STEP] = step
        ev_i[ne, EI_STAGE] = stage
        ev_i[ne, EI_KIND] = kind
        ev_i[ne, EI_SIDE] = side
        ev_i[ne, EI_ORE] = ore
        ev_i[ne, EI_CMP] = cmp
        ev_i[ne, EI_DEC] = dec
        ev_i[ne, EI_ROW0] = row0
        ev_i[ne, EI_ROW1] = row1
        ev_i[ne, EI_SEQ] = seq
        ev_f[ne, EF_FROM] = f
        ev_f[ne, EF_TO] = t
        ev_f[ne, EF_M] = m
        ev_f[ne, EF_C] = mc / m if m > 0.0 else math.nan
        ev_f[ne, EF_MC] = mc
    return ne + 1


@njit((*_EV, *(int64,) * 13, int64[::1], float64[::1], float64[::1], float64, float64), cache=True, nogil=True)
def emit_frag(  # type: ignore[no-untyped-def]
    ev_i, ev_f, ne, hole, line, step, stage, kind, side, ore, cmp, dec, seq, a, b, p_row, p_from, p_to, m, mc
):
    """An event about the pieces [a, b): rows of its first and last samples (−1 — none), its depths."""
    if ne >= ev_i.shape[0]:
        return ne + 1
    r0 = -1
    r1 = -1
    for k in range(a, b):
        if p_row[k] >= 0:
            r0 = p_row[k]
            break
    for k in range(b - 1, a - 1, -1):
        if p_row[k] >= 0:
            r1 = p_row[k]
            break
    return emit(
        ev_i, ev_f, ne, hole, line, step, stage, kind, side, ore, cmp, dec, r0, r1, seq, p_from[a], p_to[b - 1], m, mc
    )


@njit(cache=True, nogil=True)
def pieces_strip(  # type: ignore[no-untyped-def]
    h0, h1, starts, frm, to, grade, thick, use_thick, domain, use_domain, bridge, missing_max, gap_eps,
    p_row, p_from, p_to, p_l, p_g, p_miss, line_start, line_hole, line_no, s_state, counts, ev_i, ev_f, ne,
):  # fmt: skip
    """Step 0 for the holes h0 ≤ h < h1: pieces and sampling lines.

    Out: pieces `p_row` (row of the sample, −1 — a stretch without samples), `p_from`, `p_to`, `p_l`, `p_g`,
    `p_miss` (1 — missing, counted as waste); lines `line_start[j] : line_start[j + 1]` (pieces), `line_hole`,
    `line_no` (number of the line in its hole); `s_state[row]` (ROW_*); `counts[C_*]` are added to.
    Room: pieces — rows of the strip (twice that with `bridge`) + 1; lines — rows of the strip + 1.
    Returns (pieces, lines, events).
    """
    npc = 0
    nl = 0
    for h in range(h0, h1):
        is_open = False
        lstart = 0
        lno = 0
        pend = 0  # missing pieces after the last sample of the open line, not yet in it
        pend_len = 0.0
        have_prev = False
        prev_to = 0.0
        cur_dom = 0
        for r in range(starts[h], starts[h + 1]):
            f = frm[r]
            t = to[r]
            if not (math.isfinite(f) and math.isfinite(t) and t > f):
                s_state[r] = ROW_BAD
                counts[C_BAD] += 1
                continue
            if have_prev and f < prev_to - gap_eps:
                s_state[r] = ROW_OVERLAP
                counts[C_OVERLAP] += 1
                continue
            gap = have_prev and f > prev_to + gap_eps
            gap_from = prev_to
            have_prev = True
            prev_to = t
            dom = domain[r] if use_domain else 0
            brk = 0
            sample = False
            ev_f0 = f
            g = math.nan
            ln = math.nan
            if use_domain and dom < 0:
                s_state[r] = ROW_NO_DOMAIN
                counts[C_NO_DOMAIN] += 1
                brk = R_DOMAIN
            else:
                if is_open and use_domain and dom != cur_dom:
                    brk = R_DOMAIN
                if gap and is_open and brk == 0:
                    if bridge:
                        k = npc + pend
                        p_row[k] = -1
                        p_from[k] = gap_from
                        p_to[k] = f
                        p_l[k] = f - gap_from
                        p_g[k] = 0.0
                        p_miss[k] = 1
                        pend += 1
                        pend_len += f - gap_from
                    else:
                        brk = R_GAP
                        ev_f0 = gap_from
                g = grade[r]
                ln = thick[r] if use_thick else t - f
                if math.isfinite(g) and math.isfinite(ln) and ln > 0.0:
                    sample = True
                    if is_open and brk == 0 and pend > 0:
                        if le(pend_len, missing_max):
                            for k in range(npc, npc + pend):
                                if p_row[k] >= 0:
                                    s_state[p_row[k]] = ROW_BRIDGED
                            ne = emit(
                                ev_i, ev_f, ne, h, lno, STEP_BREAK, 0, K_BREAK, SIDE_NONE, 0, R_MISSING_MAX,
                                D_MISSING_WASTE, -1, -1, pend, p_from[npc], f, pend_len, math.nan,
                            )  # fmt: skip
                            counts[C_BRIDGED] += pend
                            npc += pend
                            pend = 0
                            pend_len = 0.0
                        else:
                            brk = R_MISSING_MAX
                            ev_f0 = p_from[npc]
                else:
                    s_state[r] = ROW_MISSING
                    counts[C_MISSING] += 1
                    if is_open and brk == 0:
                        if bridge:
                            k = npc + pend
                            p_row[k] = r
                            p_from[k] = f
                            p_to[k] = t
                            p_l[k] = ln if (math.isfinite(ln) and ln > 0.0) else t - f
                            p_g[k] = 0.0
                            p_miss[k] = 1
                            pend += 1
                            pend_len += t - f
                        else:
                            brk = R_NAN
            if is_open and brk != 0:
                ev_t0 = t if brk == R_NAN else f
                ne = emit(
                    ev_i, ev_f, ne, h, lno, STEP_BREAK, 0, K_BREAK, SIDE_NONE, 0, brk, D_BREAK, r, r, 0,
                    ev_f0, ev_t0, ev_t0 - ev_f0, math.nan,
                )  # fmt: skip
                counts[C_BREAKS] += 1
                line_start[nl] = lstart
                line_hole[nl] = h
                line_no[nl] = lno
                nl += 1
                lno += 1
                is_open = False
                pend = 0
                pend_len = 0.0
            if sample:
                if not is_open:
                    is_open = True
                    lstart = npc
                    cur_dom = dom
                p_row[npc] = r
                p_from[npc] = f
                p_to[npc] = t
                p_l[npc] = ln
                p_g[npc] = g
                p_miss[npc] = 0
                npc += 1
        if is_open:
            line_start[nl] = lstart
            line_hole[nl] = h
            line_no[nl] = lno
            nl += 1
    line_start[nl] = npc
    return npc, nl, ne


@njit(cache=True, nogil=True)
def elementary(p0, p1, p_l, p_g, cb, ub, uo, um, umc):  # type: ignore[no-untyped-def]
    """Step 1: the elementary units of the pieces [p0, p1) → the series (ub, uo, um, umc); returns nu."""
    nu = 0
    k = p0
    while k < p1:
        ore = ge(p_g[k], cb)
        m = 0.0
        mc = 0.0
        ub[nu] = k
        while k < p1 and ge(p_g[k], cb) == ore:
            m += p_l[k]
            mc += p_l[k] * p_g[k]
            k += 1
        uo[nu] = 1 if ore else 0
        um[nu] = m
        umc[nu] = mc
        nu += 1
    ub[nu] = p1
    return nu


@njit(cache=True, nogil=True)
def emit_units(nu, ub, uo, um, umc, hole, line, p_row, p_from, p_to, ev_i, ev_f, ne):  # type: ignore[no-untyped-def]
    """Events of step 1: the elementary units N₁… (IN₀) of a line."""
    if ne >= ev_i.shape[0]:
        return ne + nu
    for k in range(nu):
        ne = emit_frag(
            ev_i, ev_f, ne, hole, line, STEP_UNIT, 0, K_UNIT, SIDE_NONE, uo[k], CMP_CB, D_NONE, k + 1,
            ub[k], ub[k + 1], p_row, p_from, p_to, um[k], umc[k],
        )  # fmt: skip
    return ne


@njit(cache=True, nogil=True)
def grow_simple(nu, ub, uo, um, umc, cb, hole, line, p_row, p_from, p_to, ev_i, ev_f, ne):  # type: ignore[no-untyped-def]
    """«Упрощённый» (И2, сл. 16): from an ore unit downwards the next waste and ore units are added while the
    average of the whole interval stays ≥ Сб; Мпп and the прирезка of each side are not checked. The interval
    that failed starts anew from the ore unit it could not take. The series is rewritten in place: every ore
    unit is a grown interval (its К is checked by the caller). Returns (nu, events)."""
    w = 0
    i = 0
    while i < nu:
        if uo[i] == 0:
            ub[w] = ub[i]
            uo[w] = 0
            um[w] = um[i]
            umc[w] = umc[i]
            w += 1
            i += 1
            continue
        b = ub[i]
        m = um[i]
        mc = umc[i]
        j = i
        stage = 0
        while j + 2 < nu:
            m2 = m + um[j + 1]
            m2 += um[j + 2]
            mc2 = mc + umc[j + 1]
            mc2 += umc[j + 2]
            stage += 1
            if ge(mc2 / m2, cb):
                ne = emit_frag(
                    ev_i, ev_f, ne, hole, line, STEP_TRIM, stage, K_TRIM, SIDE_NONE, 1, CMP_CB, D_JOINED, 0,
                    b, ub[j + 3], p_row, p_from, p_to, m2, mc2,
                )  # fmt: skip
                m = m2
                mc = mc2
                j += 2
            else:
                ne = emit_frag(
                    ev_i, ev_f, ne, hole, line, STEP_TRIM, stage, K_TRIM, SIDE_NONE, 1, CMP_CB, D_REJ_C, 0,
                    b, ub[j + 3], p_row, p_from, p_to, m2, mc2,
                )  # fmt: skip
                break
        ub[w] = b
        uo[w] = 1
        um[w] = m
        umc[w] = mc
        w += 1
        i = j + 1
    ub[w] = ub[nu]
    return w, ne


@njit(cache=True, nogil=True)
def find_layer(i0, i1, start, eo, em, emc, cb, mpp):  # type: ignore[no-untyped-def]
    """The first fragment «порода … порода» among the elementary units [i0, i1), looking from unit `start`
    downwards: it begins and ends with a waste unit, C < Сб and M > Мпп; of the fragments with the same
    beginning — the shortest. Returns (s, t) — its first and last units, or (−1, −1).

    Whole waste units are enough: a fragment that begins or ends inside a waste unit only gets longer and
    stays below Сб when the rest of that unit is added."""
    s = start
    while s < i1:
        if eo[s] != 0:
            s += 1
            continue
        m = 0.0
        mc = 0.0
        t = s
        while t < i1:
            m += em[t]
            mc += emc[t]
            if eo[t] == 0 and not le(m, mpp) and not ge(mc / m, cb):
                return s, t
            t += 1
        s += 1
    return -1, -1
