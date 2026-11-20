"""Ore intervals by the ГКЗ method (ТЗ 7.35 Д-26, ADR 0018 §8): parameters, modes, classes, the run over a set.

    ore_intervals(lines, params, *, events=False, threads=1, tick=None) -> OreIntervals

`lines` — the samples of a set of holes ordered by (hole, FROM): `OreLines.of(starts, frm, to, grade, thick,
domain)`; the parameters, the result and the events — `ore_types`. For a sampling line (`ore_units`, step 0)
the kernel builds the elementary units (step 1), runs the steps of the mode and writes the classes (step 5):

    simple       наращивание while the average of the whole interval stays ≥ Сб (`ore_units.grow_simple`)
    soft         step 2 — прирезка по этапам (`ore_steps.trim_stages`)
    strict, strict_excl, contacts — steps 3–4 and the contacts: подшаги 12.12–12.13

Classes. An ore unit of the final series that meets К (C ≥ Смин and (M ≥ Мрт or MC ≥ МС)) is an `ore` row with
BASIS `thickness` or `mc`; its samples are `ore` (g ≥ Сб) and `waste_in`. Everything else falls back to the
samples: a run of ore samples is a `subgrade` row, a run of waste samples — a `waste` row. The rows of a line
cover it without overlaps: Σ M of the rows = the thickness of the line. M and MC of a row are summed over its
pieces in their order.

Fragments «порода … порода» with C < Сб and M > Мпп inside an ore interval are not checked in `simple` and
`soft`; with events on they are reported («не проверяется в этом режиме»).

Strips (`strips.run`): the holes are handled 4096 at a time, a strip writes only its own rows, the parts are
joined in the order of the strips — the result does not depend on the number of threads. `tick(fraction)` is
called after every strip and may raise to cancel.
"""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit

from . import ore_steps as steps
from . import ore_units as ou
from .ore_types import (
    IV_BASIS,
    IV_CLS,
    IV_F_COLS,
    IV_FLAG,
    IV_FROM,
    IV_HOLE,
    IV_I_COLS,
    IV_LINE,
    IV_M,
    IV_MC,
    IV_N,
    IV_ORE_ID,
    IV_ROW0,
    IV_ROW1,
    IV_TO,
    IV_WASTE,
    MODE_TITLES,
    OreError,
    OreEvents,
    OreIntervals,
    OreLines,
    OreParams,
)
from .strips import Tick, run

__all__ = ["ALGO", "READY_MODES", "OreError", "OreEvents", "OreIntervals", "OreLines", "OreParams", "ore_intervals"]

ALGO = 1  # version of the rules (ТЗ 7.35.10: answers № 17–24 are in); a change raises it → results go «устарел»
#: Modes the kernel runs now; the rest arrive with подшаги 12.12–12.13.
READY_MODES = ("simple", "soft")

@njit(cache=True, nogil=True)
def _put_row(  # type: ignore[no-untyped-def]
    a, b, cls, basis, ore_id, hole, line, cb, p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni
):
    """Row `ni` of the intervals — the pieces [a, b) — and the classes of their samples; returns ni + 1."""
    m = 0.0
    mc = 0.0
    waste = 0.0
    n = 0
    flag = 0
    r0 = -1
    r1 = -1
    for k in range(a, b):
        m += p_l[k]
        mc += p_l[k] * p_g[k]
        pc = cls
        if cls == ou.CLS_ORE and not ou.ge(p_g[k], cb):
            pc = ou.CLS_WASTE_IN
            waste += p_l[k]
        if p_miss[k] != 0:
            flag = 1
        r = p_row[k]
        if r >= 0:
            if r0 < 0:
                r0 = r
            r1 = r
            n += 1
            s_cls[r] = pc
            s_ore[r] = ore_id
    iv_i[ni, IV_HOLE] = hole
    iv_i[ni, IV_LINE] = line
    iv_i[ni, IV_CLS] = cls
    iv_i[ni, IV_BASIS] = basis
    iv_i[ni, IV_N] = n
    iv_i[ni, IV_FLAG] = flag
    iv_i[ni, IV_ORE_ID] = ore_id
    iv_i[ni, IV_ROW0] = r0
    iv_i[ni, IV_ROW1] = r1
    iv_f[ni, IV_FROM] = p_from[a]
    iv_f[ni, IV_TO] = p_to[b - 1]
    iv_f[ni, IV_M] = m
    iv_f[ni, IV_MC] = mc
    iv_f[ni, IV_WASTE] = waste
    return ni + 1


@njit(cache=True, nogil=True)
def _classify(  # type: ignore[no-untyped-def]
    nu, ub, uo, um, umc, hole, line, ore_no, cb, c_min, m_min, mc_min,
    p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni, ev_i, ev_f, ne,
):  # fmt: skip
    """Step 5 for the final series of a line: rows `ore` (К), `subgrade` (runs of ore samples outside them),
    `waste`. Returns (rows, ore intervals of the hole so far, events)."""
    wa = -1  # first piece of the waste row being collected
    for u in range(nu):
        a = ub[u]
        b = ub[u + 1]
        basis = ou.cond_k(um[u], umc[u], c_min, m_min, mc_min) if uo[u] == 1 else ou.BASIS_NONE
        if basis != ou.BASIS_NONE:
            if wa >= 0:
                ni = _put_row(
                    wa, a, ou.CLS_WASTE, 0, 0, hole, line, cb,
                    p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
                )  # fmt: skip
                wa = -1
            ore_no += 1
            ni = _put_row(
                a, b, ou.CLS_ORE, basis, ore_no, hole, line, cb,
                p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
            )  # fmt: skip
            ne = ou.emit_frag(
                ev_i, ev_f, ne, hole, line, ou.STEP_CLASS, 0, ou.K_INTERVAL, ou.SIDE_NONE, 1,
                ou.CMP_MRT if basis == ou.BASIS_THICKNESS else ou.CMP_MC, ou.D_ORE, ore_no,
                a, b, p_row, p_from, p_to, um[u], umc[u],
            )  # fmt: skip
            continue
        if uo[u] == 1:
            ne = ou.emit_frag(
                ev_i, ev_f, ne, hole, line, ou.STEP_CLASS, 0, ou.K_INTERVAL, ou.SIDE_NONE, 1, ou.CMP_K,
                ou.D_SUBGRADE, 0, a, b, p_row, p_from, p_to, um[u], umc[u],
            )  # fmt: skip
        k = a
        while k < b:
            ore = ou.ge(p_g[k], cb)
            k2 = k + 1
            while k2 < b and ou.ge(p_g[k2], cb) == ore:
                k2 += 1
            if ore:
                if wa >= 0:
                    ni = _put_row(
                        wa, k, ou.CLS_WASTE, 0, 0, hole, line, cb,
                        p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
                    )  # fmt: skip
                    wa = -1
                ni = _put_row(
                    k, k2, ou.CLS_SUBGRADE, 0, 0, hole, line, cb,
                    p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
                )  # fmt: skip
            elif wa < 0:
                wa = k
            k = k2
    if wa >= 0:
        ni = _put_row(
            wa, ub[nu], ou.CLS_WASTE, 0, 0, hole, line, cb,
            p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
        )  # fmt: skip
    return ni, ore_no, ne


@njit(cache=True, nogil=True)
def _layers_not_checked(  # type: ignore[no-untyped-def]
    nu, ub, uo, um, umc, nel, eb, eo, em, emc, hole, line, cb, mpp, c_min, m_min, mc_min,
    p_row, p_from, p_to, ev_i, ev_f, ne,
):  # fmt: skip
    """Events «не проверяется в этом режиме»: fragments «порода … порода» with C < Сб and M > Мпп inside the
    ore intervals of a mode without step 3."""
    e = 0
    for u in range(nu):
        if uo[u] != 1 or ou.cond_k(um[u], umc[u], c_min, m_min, mc_min) == ou.BASIS_NONE:
            continue
        while e < nel and eb[e] < ub[u]:
            e += 1
        i0 = e
        i1 = i0
        while i1 < nel and eb[i1] < ub[u + 1]:
            i1 += 1
        start = i0
        while start < i1:
            s, t = ou.find_layer(i0, i1, start, eo, em, emc, cb, mpp)
            if s < 0:
                break
            m = 0.0
            mc = 0.0
            for k in range(s, t + 1):
                m += em[k]
                mc += emc[k]
            ne = ou.emit_frag(
                ev_i, ev_f, ne, hole, line, ou.STEP_LAYER, 0, ou.K_LAYER, ou.SIDE_NONE, 0, ou.CMP_MPP,
                ou.D_NOT_CHECKED, 0, eb[s], eb[t + 1], p_row, p_from, p_to, m, mc,
            )  # fmt: skip
            start = t + 1
    return ne


@njit(cache=True, nogil=True)
def _run_strip(  # type: ignore[no-untyped-def]
    nl, line_start, line_hole, line_no, p_row, p_from, p_to, p_l, p_g, p_miss,
    mode, cb, m_min, mpp, mc_min, c_min, s_cls, s_ore, iv_i, iv_f, ev_i, ev_f, ne,
):  # fmt: skip
    """Steps 1–5 for the lines of a strip. Room of `iv_*` — the pieces of the strip. Returns (rows, events)."""
    cap = 2
    for j in range(nl):
        cap = max(cap, line_start[j + 1] - line_start[j] + 2)
    eb = np.empty(cap, np.int64)  # elementary units of the line
    eo = np.empty(cap, np.int8)
    em = np.empty(cap, np.float64)
    emc = np.empty(cap, np.float64)
    ub = np.empty(cap, np.int64)  # the series the steps rewrite
    uo = np.empty(cap, np.int8)
    um = np.empty(cap, np.float64)
    umc = np.empty(cap, np.float64)
    uchg = np.empty(cap, np.int8)
    wb = np.empty(cap, np.int64)  # scratch of step 2
    wo = np.empty(cap, np.int8)
    wm = np.empty(cap, np.float64)
    wmc = np.empty(cap, np.float64)
    wchg = np.empty(cap, np.int8)
    acc = np.empty(cap, np.int8)
    ni = 0
    ore_no = 0
    hole_prev = -1
    for j in range(nl):
        hole = line_hole[j]
        line = line_no[j]
        if hole != hole_prev:
            ore_no = 0
            hole_prev = hole
        nel = ou.elementary(line_start[j], line_start[j + 1], p_l, p_g, cb, eb, eo, em, emc)
        ne = ou.emit_units(nel, eb, eo, em, emc, hole, line, p_row, p_from, p_to, ev_i, ev_f, ne)
        nu = nel
        for k in range(nel):
            ub[k] = eb[k]
            uo[k] = eo[k]
            um[k] = em[k]
            umc[k] = emc[k]
        ub[nel] = eb[nel]
        if mode == ou.MODE_SIMPLE:
            nu, ne = ou.grow_simple(nu, ub, uo, um, umc, cb, hole, line, p_row, p_from, p_to, ev_i, ev_f, ne)
        elif mode == ou.MODE_SOFT:
            nu, ne, _ = steps.trim_stages(
                nu, ub, uo, um, umc, uchg, wb, wo, wm, wmc, wchg, acc, cb, mpp, c_min, m_min, mc_min,
                hole, line, p_row, p_from, p_to, ev_i, ev_f, ne,
            )  # fmt: skip
        if ev_i.shape[0] > 0:
            ne = _layers_not_checked(
                nu, ub, uo, um, umc, nel, eb, eo, em, emc, hole, line, cb, mpp, c_min, m_min, mc_min,
                p_row, p_from, p_to, ev_i, ev_f, ne,
            )  # fmt: skip
        ni, ore_no, ne = _classify(
            nu, ub, uo, um, umc, hole, line, ore_no, cb, c_min, m_min, mc_min,
            p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni, ev_i, ev_f, ne,
        )  # fmt: skip
    return ni, ne


def ore_intervals(
    lines: OreLines, params: OreParams, *, events: bool = False, threads: int = 1, tick: Tick | None = None
) -> OreIntervals:
    """Ore intervals of a set of holes. `events` — collect the protocol (one hole or a few: it is not kept
    for a whole set). `tick(fraction)` is called after every strip of holes and may raise to cancel."""
    p = params.checked()
    if p.mode not in READY_MODES:
        raise OreError(
            "ore_mode_unavailable", f"Режим «{MODE_TITLES[p.mode]}» пока недоступен.", "Выберите другой режим."
        )
    mode = ou.MODES.index(p.mode)
    bridge = p.missing == "waste"
    n = len(lines.frm)
    holes = len(lines.starts) - 1
    use_thick = lines.thick is not None
    use_domain = lines.domain is not None
    thick = lines.thick if lines.thick is not None else np.empty(0, np.float64)
    domain = lines.domain if lines.domain is not None else np.empty(0, np.int32)
    s_cls = np.full(n, ou.CLS_NONE, np.int8)
    s_ore = np.zeros(n, np.int32)
    s_state = np.zeros(n, np.int8)

    def strip(h0: int, h1: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        rows = int(lines.starts[h1] - lines.starts[h0])
        room = rows * (2 if bridge else 1) + 1
        p_row = np.empty(room, np.int64)
        p_from = np.empty(room, np.float64)
        p_to = np.empty(room, np.float64)
        p_l = np.empty(room, np.float64)
        p_g = np.empty(room, np.float64)
        p_miss = np.empty(room, np.int8)
        line_start = np.empty(rows + 2, np.int64)
        line_hole = np.empty(rows + 1, np.int64)
        line_no = np.empty(rows + 1, np.int64)
        counts = np.zeros(ou.C_COLS + 2, np.int64)
        cap = min(64 + 8 * rows, 1 << 16) if events else 0  # grows below when the events ask for more
        while True:
            counts[:] = 0
            ev_i = np.empty((cap, ou.EI_COLS), np.int64)
            ev_f = np.empty((cap, ou.EF_COLS), np.float64)
            npc, nl, ne = ou.pieces_strip(
                h0, h1, lines.starts, lines.frm, lines.to, lines.grade, thick, use_thick, domain, use_domain,
                bridge, p.missing_max, ou.GAP_EPS, p_row, p_from, p_to, p_l, p_g, p_miss,
                line_start, line_hole, line_no, s_state, counts, ev_i, ev_f, 0,
            )  # fmt: skip
            iv_i = np.empty((npc, IV_I_COLS), np.int64)
            iv_f = np.empty((npc, IV_F_COLS), np.float64)
            ni, ne = _run_strip(
                nl, line_start, line_hole, line_no, p_row, p_from, p_to, p_l, p_g, p_miss,
                mode, p.cutoff, p.m_min, p.m_waste_max, p.mc_min, p.c_min, s_cls, s_ore, iv_i, iv_f, ev_i, ev_f, ne,
            )  # fmt: skip
            if not events or ne <= cap:
                break
            cap = ne  # the events did not fit: once more with the room they asked for
        counts[ou.C_COLS] = npc
        counts[ou.C_COLS + 1] = nl
        return iv_i[:ni].copy(), iv_f[:ni].copy(), ev_i[:ne], ev_f[:ne], counts

    parts = run(strip, holes, threads=threads, tick=tick)
    if parts:
        iv_i = np.concatenate([a[0] for a in parts])
        iv_f = np.concatenate([a[1] for a in parts])
        counts = np.sum([a[4] for a in parts], axis=0)
    else:
        iv_i = np.empty((0, IV_I_COLS), np.int64)
        iv_f = np.empty((0, IV_F_COLS), np.float64)
        counts = np.zeros(ou.C_COLS + 2, np.int64)
    found = None
    if events:
        ev_i = np.concatenate([a[2] for a in parts]) if parts else np.empty((0, ou.EI_COLS), np.int64)
        ev_f = np.concatenate([a[3] for a in parts]) if parts else np.empty((0, ou.EF_COLS), np.float64)
        # by hole and line; inside a line — in the order of the steps, the end of the line (step 0) last
        order = np.lexsort((ev_i[:, ou.EI_STEP] == ou.STEP_BREAK, ev_i[:, ou.EI_LINE], ev_i[:, ou.EI_HOLE]))
        found = OreEvents(ev_i[order], ev_f[order])
    stats = {
        "samples": n,
        "holes": holes,
        "lines": int(counts[ou.C_COLS + 1]),
        "pieces": int(counts[ou.C_COLS]),
        "bad": int(counts[ou.C_BAD]),  # TO ≤ FROM or no depths
        "overlap": int(counts[ou.C_OVERLAP]),
        "missing": int(counts[ou.C_MISSING]),  # no grade or no thickness
        "no_domain": int(counts[ou.C_NO_DOMAIN]),
        "bridged": int(counts[ou.C_BRIDGED]),  # missing stretches counted as waste
        "breaks": int(counts[ou.C_BREAKS]),
    }
    return OreIntervals(p, iv_i, iv_f, s_cls, s_ore, s_state, stats, found)
