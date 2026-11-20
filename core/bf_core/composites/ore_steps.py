"""Ore intervals by the ГКЗ method (ТЗ 7.35 Д-26, ADR 0018 §8): the steps that rewrite the unit series of a
sampling line. Step 2 — прирезка по этапам (`trim_stages`); steps 3–4 — подшаги 12.12–12.13.

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
