"""Ore intervals by the ГКЗ method (ТЗ 7.35 Д-26, ADR 0018 §8): parameters, modes, classes, the run over a set.

    ore_intervals(lines, params, *, events=False, nested=False, threads=1, tick=None) -> OreIntervals

`lines` — the samples of a set of holes ordered by (hole, FROM): `OreLines.of(starts, frm, to, grade, thick,
domain)`; the parameters, the result and the events — `ore_types`; the table of a set, the fields of the
samples and the summary — `ore_set.run_set`. For a sampling line (`ore_units`, step 0) the kernel builds the
elementary units (step 1), runs the steps of the mode and writes the classes (step 5):

    strict_excl  step 2 — прирезка по этапам, step 3 — «исключить весь фрагмент», step 4 — сближенные
    strict       steps 2, 3 — «разорвать», 4
    soft         steps 2, 4                                   (`ore_steps`)
    simple       наращивание while the average of the whole interval stays ≥ Сб (`ore_units.grow_simple`)
    contacts     the interval is the sampling line of a domain of the body (`contacts_values`; all by default)

Classes. A unit of the final series that is ore and meets К (C ≥ Смин and (M ≥ Мрт or MC ≥ МС)) is an `ore` row
with BASIS `thickness` or `mc`; its samples are `ore` (g ≥ Сб) and `waste_in`. An excluded fragment «порода …
порода» is an `interlayer` row, all its samples are `interlayer`. Everything else falls back to the samples: a
run of ore samples is a `subgrade` row, a run of waste samples — a `waste` row. «По контактам»: a line of the
body is `ore` when MC ≥ МС (BASIS `mc`; И1, сл. 6), else one `subgrade` row; a line of another domain is one
`waste` row. These rows (NESTED = 0) cover a line without overlaps: Σ M of the rows = the thickness of the line.
M and MC of a row are summed over its pieces in their order.

Nested rows (`nested=True`; NESTED = 1; ADR 0018, решение 21) follow their `ore` row: a `waste_in` row for
every run of waste samples inside it, then the обрамление (step 7; И1, сл. 11) — a `frame` row above and one
below: the samples next to the interval, taken until their thickness reaches Мпп (whole samples; the last one
may reach beyond Мпп), not beyond the sampling line and not into another `ore` interval. They carry the ORE_ID
of the interval and do not change the classes of the samples.

Fragments «порода … порода» with C < Сб and M > Мпп inside an ore interval are not checked in `simple` and
`soft`; with events on they are reported («не проверяется в этом режиме»).

Strips (`strips.run`): the holes are handled 4096 at a time, a strip writes only its own rows, the parts are
joined in the order of the strips — the result does not depend on the number of threads. `tick(fraction)` is
called after every strip and may raise to cancel.
"""

from __future__ import annotations

import numpy as np
from numba import float64, int8, int32, int64

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
    IV_NESTED,
    IV_ORE_ID,
    IV_ROW0,
    IV_ROW1,
    IV_TO,
    IV_WASTE,
    OreError,
    OreEvents,
    OreIntervals,
    OreLines,
    OreParams,
)
from .strips import Tick, run

__all__ = ["ALGO", "READY_MODES", "OreError", "OreEvents", "OreIntervals", "OreLines", "OreParams", "ore_intervals"]

ALGO = 1  # version of the rules (ТЗ 7.35.10: answers № 17–24 are in); a change raises it → results go «устарел»
#: Modes the kernel runs: all five.
READY_MODES = ou.MODES


_F1 = float64[::1]
_PIECES = (int64[::1], _F1, _F1, _F1, _F1, int8[::1])  # p_row, p_from, p_to, p_l, p_g, p_miss
_OUT = (int8[::1], int32[::1], int64[:, ::1], float64[:, ::1])  # s_cls, s_ore, iv_i, iv_f


@njit((*(int64,) * 8, float64, *_PIECES, *_OUT, int64), cache=True, nogil=True)
def _put_row(  # type: ignore[no-untyped-def]
    a, b, cls, basis, ore_id, nested, hole, line, cb,
    p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
):  # fmt: skip
    """Row `ni` of the intervals — the pieces [a, b) — written when there is room; a row that is not nested
    names the classes of its samples. Returns ni + 1."""
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
            if nested == 0:
                s_cls[r] = pc
                s_ore[r] = ore_id
    if ni < iv_i.shape[0]:
        iv_i[ni, IV_HOLE] = hole
        iv_i[ni, IV_LINE] = line
        iv_i[ni, IV_CLS] = cls
        iv_i[ni, IV_BASIS] = basis
        iv_i[ni, IV_N] = n
        iv_i[ni, IV_FLAG] = flag
        iv_i[ni, IV_ORE_ID] = ore_id
        iv_i[ni, IV_ROW0] = r0
        iv_i[ni, IV_ROW1] = r1
        iv_i[ni, IV_NESTED] = nested
        iv_f[ni, IV_FROM] = p_from[a]
        iv_f[ni, IV_TO] = p_to[b - 1]
        iv_f[ni, IV_M] = m
        iv_f[ni, IV_MC] = mc
        iv_f[ni, IV_WASTE] = waste
    return ni + 1


@njit(cache=True, nogil=True)
def _nested_rows(  # type: ignore[no-untyped-def]
    a, b, lo, hi, ore_id, hole, line, cb, mpp, p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni
):
    """The nested rows of the `ore` interval of the pieces [a, b): its waste stretches, the frame above (not
    above piece `lo`) and the frame below (not beyond piece `hi`). Returns the number of rows so far."""
    k = a
    while k < b:
        if ou.ge(p_g[k], cb):
            k += 1
            continue
        k2 = k + 1
        while k2 < b and not ou.ge(p_g[k2], cb):
            k2 += 1
        ni = _put_row(
            k, k2, ou.CLS_WASTE_IN, 0, ore_id, 1, hole, line, cb,
            p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
        )  # fmt: skip
        k = k2
    k = a
    m = 0.0
    while k > lo and not ou.ge(m, mpp):
        k -= 1
        m += p_l[k]
    if k < a:
        ni = _put_row(
            k, a, ou.CLS_FRAME, 0, ore_id, 1, hole, line, cb,
            p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
        )  # fmt: skip
    k = b
    m = 0.0
    while k < hi and not ou.ge(m, mpp):
        m += p_l[k]
        k += 1
    if k > b:
        ni = _put_row(
            b, k, ou.CLS_FRAME, 0, ore_id, 1, hole, line, cb,
            p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
        )  # fmt: skip
    return ni


@njit(cache=True, nogil=True)
def _classify(  # type: ignore[no-untyped-def]
    nu, ub, uo, um, umc, kb, p0, nested, hole, line, ore_no, cb, mpp, c_min, m_min, mc_min,
    p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni, ev_i, ev_f, ne,
):  # fmt: skip
    """Steps 5 and 7 for the final series of a line that begins with piece `p0`: rows `ore` (К) with their
    nested rows, `interlayer`, `subgrade` (runs of ore samples outside them), `waste`. `kb` — scratch.
    Returns (rows, ore intervals of the hole so far, events)."""
    for u in range(nu):
        kb[u] = ou.cond_k(um[u], umc[u], c_min, m_min, mc_min) if uo[u] == ou.U_ORE else ou.BASIS_NONE
    wa = -1  # first piece of the waste row being collected
    ore_end = p0  # the end of the `ore` row above
    for u in range(nu):
        a = ub[u]
        b = ub[u + 1]
        if kb[u] != ou.BASIS_NONE or uo[u] == ou.U_LAYER:
            if wa >= 0:
                ni = _put_row(
                    wa, a, ou.CLS_WASTE, 0, 0, 0, hole, line, cb,
                    p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
                )  # fmt: skip
                wa = -1
            if uo[u] == ou.U_LAYER:
                ni = _put_row(
                    a, b, ou.CLS_INTERLAYER, 0, 0, 0, hole, line, cb,
                    p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
                )  # fmt: skip
                continue
            ore_no += 1
            ni = _put_row(
                a, b, ou.CLS_ORE, kb[u], ore_no, 0, hole, line, cb,
                p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
            )  # fmt: skip
            ne = ou.emit_frag(
                ev_i, ev_f, ne, hole, line, ou.STEP_CLASS, 0, ou.K_INTERVAL, ou.SIDE_NONE, 1,
                ou.CMP_MRT if kb[u] == ou.BASIS_THICKNESS else ou.CMP_MC, ou.D_ORE, ore_no,
                a, b, p_row, p_from, p_to, um[u], umc[u],
            )  # fmt: skip
            if nested:
                hi = ub[nu]
                for v in range(u + 1, nu):
                    if kb[v] != ou.BASIS_NONE:
                        hi = ub[v]
                        break
                ni = _nested_rows(
                    a, b, ore_end, hi, ore_no, hole, line, cb, mpp,
                    p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
                )  # fmt: skip
            ore_end = b
            continue
        if uo[u] == ou.U_ORE:
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
                        wa, k, ou.CLS_WASTE, 0, 0, 0, hole, line, cb,
                        p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
                    )  # fmt: skip
                    wa = -1
                ni = _put_row(
                    k, k2, ou.CLS_SUBGRADE, 0, 0, 0, hole, line, cb,
                    p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
                )  # fmt: skip
            elif wa < 0:
                wa = k
            k = k2
    if wa >= 0:
        ni = _put_row(
            wa, ub[nu], ou.CLS_WASTE, 0, 0, 0, hole, line, cb,
            p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
        )  # fmt: skip
    return ni, ore_no, ne


@njit(cache=True, nogil=True)
def _contact(  # type: ignore[no-untyped-def]
    p0, p1, body, nested, hole, line, ore_no, cb, mc_min,
    p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni, ev_i, ev_f, ne,
):  # fmt: skip
    """«По контактам»: the line of the pieces [p0, p1) is one interval — `ore` when it lies in the body and
    MC ≥ МС, `subgrade` when it lies in the body, else `waste`. Returns (rows, ore intervals so far, events)."""
    if not body:
        ni = _put_row(
            p0, p1, ou.CLS_WASTE, 0, 0, 0, hole, line, cb,
            p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
        )  # fmt: skip
        return ni, ore_no, ne
    m = 0.0
    mc = 0.0
    for k in range(p0, p1):
        m += p_l[k]
        mc += p_l[k] * p_g[k]
    ore = ou.ge(mc, mc_min)
    if ore:
        ore_no += 1
    ni = _put_row(
        p0, p1, ou.CLS_ORE if ore else ou.CLS_SUBGRADE, ou.BASIS_MC if ore else 0, ore_no if ore else 0, 0,
        hole, line, cb, p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
    )  # fmt: skip
    ne = ou.emit_frag(
        ev_i, ev_f, ne, hole, line, ou.STEP_CLASS, 0, ou.K_INTERVAL, ou.SIDE_NONE, 1, ou.CMP_MC,
        ou.D_ORE if ore else ou.D_SUBGRADE, ore_no if ore else 0, p0, p1, p_row, p_from, p_to, m, mc,
    )  # fmt: skip
    if ore and nested:
        ni = _nested_rows(
            p0, p1, p0, p1, ore_no, hole, line, cb, 0.0,
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
    nl, line_start, line_hole, line_no, p_row, p_from, p_to, p_l, p_g, p_miss, domain, body, body_all,
    mode, nested, cb, m_min, mpp, mc_min, c_min, s_cls, s_ore, iv_i, iv_f, ev_i, ev_f, ne,
):  # fmt: skip
    """Steps 1–7 for the lines of a strip. The rows that find no room in `iv_*` are counted, not written.
    Returns (rows, events)."""
    cap = 2
    for j in range(nl):
        cap = max(cap, line_start[j + 1] - line_start[j] + 2)
    eb = np.empty(cap, np.int64)  # elementary units of the line
    eo = np.empty(cap, np.int8)
    em = np.empty(cap, np.float64)
    emc = np.empty(cap, np.float64)
    ub = np.empty(cap, np.int64)  # the series of step 2
    uo = np.empty(cap, np.int8)
    um = np.empty(cap, np.float64)
    umc = np.empty(cap, np.float64)
    uchg = np.empty(cap, np.int8)
    wb = np.empty(cap, np.int64)  # scratch of step 2, then the series of steps 3–4
    wo = np.empty(cap, np.int8)
    wm = np.empty(cap, np.float64)
    wmc = np.empty(cap, np.float64)
    wchg = np.empty(cap, np.int8)
    acc = np.empty(cap, np.int8)
    st_a = np.empty(cap, np.int64)  # the stack of step 3
    st_b = np.empty(cap, np.int64)
    st_k = np.empty(cap, np.int8)
    st_h = np.empty(cap, np.int64)
    pd = np.empty(cap, np.float64)  # running totals of step 3
    pm = np.empty(cap, np.float64)
    sm = np.empty(cap, np.float64)
    strict = mode == ou.MODE_STRICT or mode == ou.MODE_STRICT_EXCL
    ni = np.int64(0)  # not the constant 0: one specialization of the kernels they go to (`ore_units`)
    ore_no = np.int64(0)
    hole_prev = -1
    for j in range(nl):
        hole = line_hole[j]
        line = line_no[j]
        p0 = line_start[j]
        p1 = line_start[j + 1]
        if hole != hole_prev:
            ore_no = np.int64(0)
            hole_prev = hole
        nel = ou.elementary(p0, p1, p_l, p_g, cb, eb, eo, em, emc)
        ne = ou.emit_units(nel, eb, eo, em, emc, hole, line, p_row, p_from, p_to, ev_i, ev_f, ne)
        if mode == ou.MODE_CONTACTS:
            inside = body_all
            if not inside:
                code = domain[p_row[p0]]
                k = np.searchsorted(body, code)
                inside = k < body.shape[0] and body[k] == code
            ni, ore_no, ne = _contact(
                p0, p1, inside, nested, hole, line, ore_no, cb, mc_min,
                p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni, ev_i, ev_f, ne,
            )  # fmt: skip
            continue
        nu = nel
        for k in range(nel):
            ub[k] = eb[k]
            uo[k] = eo[k]
            um[k] = em[k]
            umc[k] = emc[k]
        ub[nel] = eb[nel]
        if mode == ou.MODE_SIMPLE:
            nu, ne = ou.grow_simple(nu, ub, uo, um, umc, cb, hole, line, p_row, p_from, p_to, ev_i, ev_f, ne)
            if ev_i.shape[0] > 0:
                ne = _layers_not_checked(
                    nu, ub, uo, um, umc, nel, eb, eo, em, emc, hole, line, cb, mpp, c_min, m_min, mc_min,
                    p_row, p_from, p_to, ev_i, ev_f, ne,
                )  # fmt: skip
            ni, ore_no, ne = _classify(
                nu, ub, uo, um, umc, acc, p0, nested, hole, line, ore_no, cb, mpp, c_min, m_min, mc_min,
                p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni, ev_i, ev_f, ne,
            )  # fmt: skip
            continue
        nu, ne, _ = steps.trim_stages(
            nu, ub, uo, um, umc, uchg, wb, wo, wm, wmc, wchg, acc, cb, mpp, c_min, m_min, mc_min,
            hole, line, p_row, p_from, p_to, ev_i, ev_f, ne,
        )  # fmt: skip
        # steps 3–4: the series of step 2 → the series w; e — the elementary units of the unit [i0, e)
        nw = np.int64(0)
        e = np.int64(0)
        for u in range(nu):
            i0 = e
            while e < nel and eb[e] < ub[u + 1]:
                e += 1
            if e - i0 < 3 or (uo[u] == ou.U_ORE and not strict):
                wb[nw] = ub[u]  # fewer than three elementary units: no waste inside to cut, no chain to join
                wo[nw] = uo[u]
                wm[nw] = um[u]
                wmc[nw] = umc[u]
                nw += 1
            elif uo[u] == ou.U_ORE:
                nw, ne = steps.split_layers(
                    i0, e, mode == ou.MODE_STRICT_EXCL, eb, eo, em, emc, cb, mpp, c_min, m_min, mc_min,
                    st_a, st_b, st_k, st_h, pd, pm, sm,
                    wb, wo, wm, wmc, nw, hole, line, p_row, p_from, p_to, ev_i, ev_f, ne,
                )  # fmt: skip
            else:
                nw, ne = steps.join_near(
                    i0, e, u > 0, u + 1 < nu, strict, eb, eo, em, emc, cb, mpp, c_min, m_min, mc_min,
                    wb, wo, wm, wmc, nw, hole, line, p_row, p_from, p_to, ev_i, ev_f, ne,
                )  # fmt: skip
        wb[nw] = p1
        if ev_i.shape[0] > 0 and not strict:
            ne = _layers_not_checked(
                nw, wb, wo, wm, wmc, nel, eb, eo, em, emc, hole, line, cb, mpp, c_min, m_min, mc_min,
                p_row, p_from, p_to, ev_i, ev_f, ne,
            )  # fmt: skip
        ni, ore_no, ne = _classify(
            nw, wb, wo, wm, wmc, acc, p0, nested, hole, line, ore_no, cb, mpp, c_min, m_min, mc_min,
            p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni, ev_i, ev_f, ne,
        )  # fmt: skip
    return ni, ne


def ore_intervals(
    lines: OreLines,
    params: OreParams,
    *,
    events: bool = False,
    nested: bool = False,
    threads: int = 1,
    tick: Tick | None = None,
) -> OreIntervals:
    """Ore intervals of a set of holes. `events` — collect the protocol (one hole or a few: it is not kept
    for a whole set); `nested` — add the `waste_in` and `frame` rows. `tick(fraction)` is called after every
    strip of holes and may raise to cancel."""
    p = params.checked()
    mode = ou.MODES.index(p.mode)
    if mode == ou.MODE_CONTACTS and lines.domain is None:
        raise OreError(
            "ore_no_domain",
            "Режим «По контактам» требует поле домена.",
            "Выберите поле домена (или кодирование каркасом) либо другой режим.",
        )
    bridge = p.missing == "waste"
    n = len(lines.frm)
    holes = len(lines.starts) - 1
    use_thick = lines.thick is not None
    use_domain = lines.domain is not None
    thick = lines.thick if lines.thick is not None else np.empty(0, np.float64)
    domain = lines.domain if lines.domain is not None else np.empty(0, np.int32)
    body = np.array(p.contacts_values or (), np.int32)
    body_all = p.contacts_values is None
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
        extra = rows // 2 + 16 if nested else 0  # room of the nested rows; grows below as well
        while True:
            counts[:] = 0
            ev_i = np.empty((cap, ou.EI_COLS), np.int64)
            ev_f = np.empty((cap, ou.EF_COLS), np.float64)
            npc, nl, ne = ou.pieces_strip(
                h0, h1, lines.starts, lines.frm, lines.to, lines.grade, thick, use_thick, domain, use_domain,
                bridge, p.missing_max, ou.GAP_EPS, p_row, p_from, p_to, p_l, p_g, p_miss,
                line_start, line_hole, line_no, s_state, counts, ev_i, ev_f, 0,
            )  # fmt: skip
            iv_i = np.empty((npc + extra, IV_I_COLS), np.int64)
            iv_f = np.empty((npc + extra, IV_F_COLS), np.float64)
            ni, ne = _run_strip(
                nl, line_start, line_hole, line_no, p_row, p_from, p_to, p_l, p_g, p_miss, domain, body, body_all,
                mode, nested, p.cutoff, p.m_min, p.m_waste_max, p.mc_min, p.c_min,
                s_cls, s_ore, iv_i, iv_f, ev_i, ev_f, ne,
            )  # fmt: skip
            if (not events or ne <= cap) and ni <= npc + extra:
                break
            cap = max(cap, ne) if events else 0  # something did not fit: once more with the room it asked for
            extra = max(extra, ni - npc)
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
