"""Ore intervals by the ГКЗ method (ТЗ 7.35 Д-26, ADR 0018 §8): the steps that rewrite the unit series of a
sampling line. Step 2 — прирезка по этапам (`trim_stages`), step 3 — пустой прослой (`split_layers`), step 4 —
сближенные интервалы (`join_near`).

Step 2 (И1, сл. 9; И2, сл. 10–11, 17). The series alternates ore (Р) and waste (Б) units; stage 1 starts from
the elementary ones. A stage:

  a. Triples Р_в, Б, Р_н of the series the stage started with. A triple is accepted when M(Б) ≤ Мпп,
     C(Р_в ∪ Б) ≥ Сб (прирезка со стороны висячего бока) and C(Б ∪ Р_н) ≥ Сб (лежачего бока). The прирезка is
     taken over the units of the previous stage, not over what has been joined at this one (ответ № 22). A
     triple none of whose three units changed at the previous stage was rejected before and is not looked at.
  b. Runs of accepted triples become one ore unit each.
  c. Некондиционная руда. From the top of the line down: an ore unit that was not joined at this stage and
     does not meet К becomes waste and merges with the waste units next to it. The ore unit right below a
     merge waits for the next stage: its triple with the merged waste has not been checked yet (И2, сл. 17:
     sample 6 goes to waste at stage 1, sample 8 is then tried through the waste 5–7 at stage 2).

Stages repeat while the series changes. At the end every ore unit of the series meets К: a unit that does not
is either joined or, a stage later, waste.

Events of a stage s (STEP_TRIM, stage = s): K_GAP — M(Б) > Мпп; K_TRIM upper and lower — C of each прирезка
with D_PASS or D_REJ_C (both are written, also when the first one fails); K_UNIT ore with
D_JOINED — a new unit; K_UNIT ore with D_DEMOTED — the unit that became waste, followed by K_UNIT waste — the
merged waste unit. The series IN_s of the protocol is the elementary series with the K_UNIT records of the
stages 1…s applied.

Step 3 (И1, сл. 15, 19; И2, сл. 18–20; modes «строгий»). An interval of step 2 is looked through for
fragments «порода … порода»: F begins and ends with a waste elementary unit (ore may lie inside), C(F) < Сб,
M(F) > Мпп. Whole elementary units are enough — a fragment that begins or ends inside a waste unit only gets
longer and stays below Сб when the rest of the unit is added. Which fragment (the rule of подшаг 12.12):

    the waste units of the interval are tried from the top down; for a unit — the shortest fragment that
    begins with it; the fragment is taken when the part of the interval above it meets К (a кондиционный
    interval stays above); when no fragment leaves one — the first fragment from the top.

«Первый сверху» alone fails on И1, сл. 18–19 (Т8.2-17): it cuts 2,3–16,6 м, then 18,9–33,2 м, and leaves five
ore units of 2,3 м @ 5,0 that do not meet К — the empty result of the «неправильная методика». With the rule
the first cut is 10,6–24,9 м, as on the slide. The interval is then divided:

    «исключить весь фрагмент» (strict_excl)  F is a unit of its own (U_LAYER → CLASS interlayer); the parts
                                              above and below it are looked through again
    «разорвать» (strict)                      only the last waste unit of F leaves the interval; the rest of F
                                              — the прирезка — stays with the part above (И2, сл. 18: 1–4 and
                                              7–8); the parts are looked through again

A part that does not meet К falls back to its samples at step 5 (subgrade, waste). Events (STEP_LAYER):
K_LAYER with D_LAYER_OUT or D_TORN — the fragment (seq — the number of the cut in the interval); K_GAP with
D_TORN — the waste unit that left; K_LAYER with D_NOT_SELECTED (compared with К) — a fragment passed over.

Step 4 (И1, сл. 16; И2, сл. 17). Last, in the stretches outside the intervals of step 2 (the waste units of its
series: elementary waste and ore units none of which meets К). From the top down a chain starts at an ore unit
and takes the next pairs «Б + Р» while M(Б) ≤ Мпп, C of the whole chain ≥ Сб and, in the strict modes, no
fragment «порода … порода» appears in it; a chain that could not take a pair ends, the next one starts from
that ore unit. A chain of two or more ore units becomes an interval when it meets К and lies farther than Мпп
from the intervals of step 2 on both sides (samples 29–33 of И1: 5 м @ 1,16, 6 м from 18–22; samples 6–8 of
И2, сл. 17: 1 м from 0–4 — not picked). Events (STEP_NEAR, K_CHAIN): D_NEAR_JOINED; D_NOT_SELECTED compared
with К or with Мпп (too close); D_REJ_C — the pair that was not taken; D_NOT_SELECTED compared with `layer`.
"""

from __future__ import annotations

from bf_core.jit import njit

from . import ore_units as ou


@njit(cache=True, nogil=True)
def trim_stages(  # type: ignore[no-untyped-def]
    nu, ub, uo, um, umc, uchg, wb, wo, wm, wmc, wchg, acc, cb, mpp, c_min, m_min, mc_min,
    hole, line, p_row, p_from, p_to, ev_i, ev_f, ne,
):  # fmt: skip
    """Step 2 for the series (ub, uo, um, umc) of a line, rewritten in place. `uchg`, `acc` and the `w*`
    arrays are scratch of the same room. Returns (units, events, stages)."""
    for k in range(nu):
        uchg[k] = 1
    stage = 0
    while True:
        stage += 1
        changed = False
        # a. triples of the series as the stage found it
        for i in range(nu):
            acc[i] = 0
        for i in range(nu - 2):
            if uo[i] != 1 or (uchg[i] == 0 and uchg[i + 1] == 0 and uchg[i + 2] == 0):
                continue
            mb = um[i + 1]
            if not ou.le(mb, mpp):
                ne = ou.emit_frag(
                    ev_i, ev_f, ne, hole, line, ou.STEP_TRIM, stage, ou.K_GAP, ou.SIDE_NONE, 0, ou.CMP_MPP,
                    ou.D_REJ_M, 0, ub[i + 1], ub[i + 2], p_row, p_from, p_to, mb, umc[i + 1],
                )  # fmt: skip
                continue
            m_up = um[i] + mb
            mc_up = umc[i] + umc[i + 1]
            m_low = mb + um[i + 2]
            mc_low = umc[i + 1] + umc[i + 2]
            ok_up = ou.ge(mc_up / m_up, cb)
            ok_low = ou.ge(mc_low / m_low, cb)
            ne = ou.emit_frag(
                ev_i, ev_f, ne, hole, line, ou.STEP_TRIM, stage, ou.K_TRIM, ou.SIDE_UPPER, 1, ou.CMP_CB,
                ou.D_PASS if ok_up else ou.D_REJ_C, 0, ub[i], ub[i + 2], p_row, p_from, p_to, m_up, mc_up,
            )  # fmt: skip
            ne = ou.emit_frag(
                ev_i, ev_f, ne, hole, line, ou.STEP_TRIM, stage, ou.K_TRIM, ou.SIDE_LOWER, 1, ou.CMP_CB,
                ou.D_PASS if ok_low else ou.D_REJ_C, 0, ub[i + 1], ub[i + 3], p_row, p_from, p_to, m_low, mc_low,
            )  # fmt: skip
            if ok_up and ok_low:
                acc[i] = 1
        # b. runs of accepted triples → one ore unit; the series goes to the w-arrays
        nw = 0
        i = 0
        while i < nu:
            if uo[i] == 1 and acc[i] == 1:
                j = i
                m = um[i]
                mc = umc[i]
                while j + 2 < nu and acc[j] == 1:
                    m += um[j + 1]
                    m += um[j + 2]
                    mc += umc[j + 1]
                    mc += umc[j + 2]
                    j += 2
                wb[nw] = ub[i]
                wo[nw] = 1
                wm[nw] = m
                wmc[nw] = mc
                wchg[nw] = 1
                ne = ou.emit_frag(
                    ev_i, ev_f, ne, hole, line, ou.STEP_TRIM, stage, ou.K_UNIT, ou.SIDE_NONE, 1, ou.CMP_CB,
                    ou.D_JOINED, 0, ub[i], ub[j + 1], p_row, p_from, p_to, m, mc,
                )  # fmt: skip
                changed = True
                i = j + 1
            else:
                wb[nw] = ub[i]
                wo[nw] = uo[i]
                wm[nw] = um[i]
                wmc[nw] = umc[i]
                wchg[nw] = 0
                i += 1
            nw += 1
        wb[nw] = ub[nu]
        # c. некондиционная руда → безрудная, from the top down; the series goes back to the u-arrays
        nu = 0
        k = 0
        waits = False  # the waste above unit k was merged at this stage: its triple is not checked yet
        while k < nw:
            if (
                wo[k] == 1
                and wchg[k] == 0
                and not waits
                and ou.cond_k(wm[k], wmc[k], c_min, m_min, mc_min) == ou.BASIS_NONE
            ):
                ne = ou.emit_frag(
                    ev_i, ev_f, ne, hole, line, ou.STEP_TRIM, stage, ou.K_UNIT, ou.SIDE_NONE, 1, ou.CMP_K,
                    ou.D_DEMOTED, 0, wb[k], wb[k + 1], p_row, p_from, p_to, wm[k], wmc[k],
                )  # fmt: skip
                b = wb[k]
                m = 0.0
                mc = 0.0
                if nu > 0 and uo[nu - 1] == 0:
                    nu -= 1
                    b = ub[nu]
                    m = um[nu]
                    mc = umc[nu]
                m += wm[k]
                mc += wmc[k]
                k += 1
                if k < nw and wo[k] == 0:
                    m += wm[k]
                    mc += wmc[k]
                    k += 1
                ub[nu] = b
                uo[nu] = 0
                um[nu] = m
                umc[nu] = mc
                uchg[nu] = 1
                ne = ou.emit_frag(
                    ev_i, ev_f, ne, hole, line, ou.STEP_TRIM, stage, ou.K_UNIT, ou.SIDE_NONE, 0, ou.CMP_NONE,
                    ou.D_NONE, 0, b, wb[k], p_row, p_from, p_to, m, mc,
                )  # fmt: skip
                nu += 1
                changed = True
                waits = True
            else:
                ub[nu] = wb[k]
                uo[nu] = wo[k]
                um[nu] = wm[k]
                umc[nu] = wmc[k]
                uchg[nu] = wchg[k]
                if wo[k] == 1:
                    waits = False
                nu += 1
                k += 1
        ub[nu] = wb[nw]
        if not changed:
            break
    return nu, ne, stage


@njit(cache=True, nogil=True)
def layer_end(s, i1, eo, em, emc, cb, mpp):  # type: ignore[no-untyped-def]
    """The shortest fragment «порода … порода» that begins with the waste elementary unit s and ends before
    unit i1: C < Сб and M > Мпп. Returns its last unit or −1."""
    m = 0.0
    mc = 0.0
    for t in range(s, i1):
        m += em[t]
        mc += emc[t]
        if eo[t] == 0 and not ou.le(m, mpp) and not ou.ge(mc / m, cb):
            return t
    return -1


@njit(cache=True, nogil=True)
def _put_unit(a, b, kind, eb, em, emc, wb, wo, wm, wmc, nw):  # type: ignore[no-untyped-def]
    """Unit `nw` of the series w — the elementary units [a, b); returns nw + 1."""
    m = 0.0
    mc = 0.0
    for k in range(a, b):
        m += em[k]
        mc += emc[k]
    wb[nw] = eb[a]
    wo[nw] = kind
    wm[nw] = m
    wmc[nw] = mc
    return nw + 1


@njit(cache=True, nogil=True)
def _emit_units(  # type: ignore[no-untyped-def]
    a, b, step, kind, ore, cmp, dec, seq, eb, em, emc, hole, line, p_row, p_from, p_to, ev_i, ev_f, ne
):
    """An event about the elementary units [a, b)."""
    if ne >= ev_i.shape[0]:
        return ne + 1
    m = 0.0
    mc = 0.0
    for k in range(a, b):
        m += em[k]
        mc += emc[k]
    return ou.emit_frag(
        ev_i, ev_f, ne, hole, line, step, 0, kind, ou.SIDE_NONE, ore, cmp, dec, seq,
        eb[a], eb[b], p_row, p_from, p_to, m, mc,
    )  # fmt: skip


@njit(cache=True, nogil=True)
def split_layers(  # type: ignore[no-untyped-def]
    i0, i1, excl, eb, eo, em, emc, cb, mpp, c_min, m_min, mc_min, st_a, st_b, st_k, st_h,
    wb, wo, wm, wmc, nw, hole, line, p_row, p_from, p_to, ev_i, ev_f, ne,
):  # fmt: skip
    """Step 3 for an interval of step 2 — the elementary units [i0, i1), the first and the last are ore. Its
    parts go to the series w from unit `nw` on: U_ORE (К is checked at step 5), U_LAYER (an excluded
    fragment), U_WASTE (a torn-off waste unit). `st_*` — scratch of the room of the line. Returns (nw, events)."""
    scan = 3  # a part to look through; the other kinds of the stack are those of the units
    st_a[0] = i0
    st_b[0] = i1
    st_k[0] = scan
    st_h[0] = i0
    top = 1
    cuts = 0
    while top > 0:
        top -= 1
        a = st_a[top]
        b = st_b[top]
        kind = st_k[top]
        if kind != scan:
            nw = _put_unit(a, b, kind, eb, em, emc, wb, wo, wm, wmc, nw)
            continue
        hint = st_h[top]  # the waste units above it begin no fragment
        f_s = -1
        f_t = -1
        c_s = -1
        c_t = -1
        m_up = 0.0
        mc_up = 0.0
        for s in range(a, b):
            if eo[s] == 0 and s >= hint:
                t = layer_end(s, b, eo, em, emc, cb, mpp)
                if t >= 0:
                    if f_s < 0:
                        f_s = s
                        f_t = t
                    if ou.cond_k(m_up, mc_up, c_min, m_min, mc_min) != ou.BASIS_NONE:
                        c_s = s
                        c_t = t
                        break
                    ne = _emit_units(
                        s, t + 1, ou.STEP_LAYER, ou.K_LAYER, 0, ou.CMP_K, ou.D_NOT_SELECTED, cuts + 1,
                        eb, em, emc, hole, line, p_row, p_from, p_to, ev_i, ev_f, ne,
                    )  # fmt: skip
            m_up += em[s]
            mc_up += emc[s]
        if c_s < 0:
            c_s = f_s
            c_t = f_t
        if c_s < 0:
            nw = _put_unit(a, b, ou.U_ORE, eb, em, emc, wb, wo, wm, wmc, nw)
            continue
        cuts += 1
        ne = _emit_units(
            c_s, c_t + 1, ou.STEP_LAYER, ou.K_LAYER, 0, ou.CMP_MPP, ou.D_LAYER_OUT if excl else ou.D_TORN, cuts,
            eb, em, emc, hole, line, p_row, p_from, p_to, ev_i, ev_f, ne,
        )  # fmt: skip
        out = c_s if excl else c_t  # the first unit that leaves the interval
        if not excl:
            ne = _emit_units(
                c_t, c_t + 1, ou.STEP_LAYER, ou.K_GAP, 0, ou.CMP_NONE, ou.D_TORN, cuts,
                eb, em, emc, hole, line, p_row, p_from, p_to, ev_i, ev_f, ne,
            )  # fmt: skip
        st_a[top] = c_t + 1  # the part below: looked through from its top
        st_b[top] = b
        st_k[top] = scan
        st_h[top] = c_t + 1
        st_a[top + 1] = out
        st_b[top + 1] = c_t + 1
        st_k[top + 1] = ou.U_LAYER if excl else ou.U_WASTE
        st_h[top + 1] = 0
        st_a[top + 2] = a  # the part above goes first; nothing begins a fragment above f_s
        st_b[top + 2] = out
        st_k[top + 2] = scan
        st_h[top + 2] = f_s
        top += 3
    return nw, ne


@njit(cache=True, nogil=True)
def _closes_layer(i, j, em, emc, cb, mpp):  # type: ignore[no-untyped-def]
    """Would the waste unit j end a fragment «порода … порода» inside the chain that begins with the ore unit
    i? The fragments that end with j, from the shortest up."""
    m = 0.0
    mc = 0.0
    for s in range(j, i, -2):  # waste units j, j − 2, …
        m += em[s]
        mc += emc[s]
        if not ou.le(m, mpp) and not ou.ge(mc / m, cb):
            return True
        m += em[s - 1]
        mc += emc[s - 1]
    return False


@njit(cache=True, nogil=True)
def join_near(  # type: ignore[no-untyped-def]
    i0, i1, has_up, has_down, strict, eb, eo, em, emc, cb, mpp, c_min, m_min, mc_min,
    wb, wo, wm, wmc, nw, hole, line, p_row, p_from, p_to, ev_i, ev_f, ne,
):  # fmt: skip
    """Step 4 for a stretch outside the intervals of step 2 — the elementary units [i0, i1); `has_up`,
    `has_down` — an interval of step 2 lies right above, right below. The stretch goes to the series w from
    unit `nw` on: the picked chains as U_ORE, the rest as U_WASTE. Returns (nw, events)."""
    done = i0  # the units before it are in the series
    i = i0
    while i < i1:
        if eo[i] == 0:
            i += 1
            continue
        m = em[i]
        mc = emc[i]
        j = i
        while j + 2 < i1:
            if not ou.le(em[j + 1], mpp):
                break
            m2 = m + em[j + 1]
            m2 += em[j + 2]
            mc2 = mc + emc[j + 1]
            mc2 += emc[j + 2]
            if not ou.ge(mc2 / m2, cb):
                ne = ou.emit_frag(
                    ev_i, ev_f, ne, hole, line, ou.STEP_NEAR, 0, ou.K_CHAIN, ou.SIDE_NONE, 1, ou.CMP_CB,
                    ou.D_REJ_C, 0, eb[i], eb[j + 3], p_row, p_from, p_to, m2, mc2,
                )  # fmt: skip
                break
            if strict and _closes_layer(i, j + 1, em, emc, cb, mpp):
                ne = ou.emit_frag(
                    ev_i, ev_f, ne, hole, line, ou.STEP_NEAR, 0, ou.K_CHAIN, ou.SIDE_NONE, 1, ou.CMP_LAYER,
                    ou.D_NOT_SELECTED, 0, eb[i], eb[j + 3], p_row, p_from, p_to, m2, mc2,
                )  # fmt: skip
                break
            m = m2
            mc = mc2
            j += 2
        if j > i:
            why = ou.CMP_NONE
            if ou.cond_k(m, mc, c_min, m_min, mc_min) == ou.BASIS_NONE:
                why = ou.CMP_K
            if why == ou.CMP_NONE and has_up:
                d = 0.0
                for k in range(i0, i):
                    d += em[k]
                if ou.le(d, mpp):
                    why = ou.CMP_MPP
            if why == ou.CMP_NONE and has_down:
                d = 0.0
                for k in range(j + 1, i1):
                    d += em[k]
                if ou.le(d, mpp):
                    why = ou.CMP_MPP
            ne = ou.emit_frag(
                ev_i, ev_f, ne, hole, line, ou.STEP_NEAR, 0, ou.K_CHAIN, ou.SIDE_NONE, 1,
                ou.CMP_K if why == ou.CMP_NONE else why,
                ou.D_NEAR_JOINED if why == ou.CMP_NONE else ou.D_NOT_SELECTED, 0,
                eb[i], eb[j + 1], p_row, p_from, p_to, m, mc,
            )  # fmt: skip
            if why == ou.CMP_NONE:
                if i > done:
                    nw = _put_unit(done, i, ou.U_WASTE, eb, em, emc, wb, wo, wm, wmc, nw)
                wb[nw] = eb[i]
                wo[nw] = ou.U_ORE
                wm[nw] = m
                wmc[nw] = mc
                nw += 1
                done = j + 1
        i = j + 1
    if done < i1:
        nw = _put_unit(done, i1, ou.U_WASTE, eb, em, emc, wb, wo, wm, wmc, nw)
    return nw, ne
