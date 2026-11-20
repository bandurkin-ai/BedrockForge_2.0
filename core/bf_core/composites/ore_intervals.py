"""Ore intervals by the ГКЗ method (ТЗ 7.35 Д-26, ADR 0018 §8): parameters, modes, classes, the run over a set.

    ore_intervals(lines, params, *, events=False, threads=1, tick=None) -> OreIntervals

`lines` — the samples of a set of holes ordered by (hole, FROM): `OreLines.of(starts, frm, to, grade, thick,
domain)`. For a sampling line (`ore_units`, step 0) the kernel builds the elementary units (step 1), runs the
steps of the mode and writes the classes (step 5):

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

from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from bf_core.jit import njit

from . import ore_steps as steps
from . import ore_units as ou
from .strips import Tick, run

ALGO = 1  # version of the rules (ТЗ 7.35.10: answers № 17–24 are in); a change raises it → results go «устарел»
#: Modes the kernel runs now; the rest arrive with подшаги 12.12–12.13.
READY_MODES = ("simple", "soft")

# interval rows: integer and float columns
IV_HOLE, IV_LINE, IV_CLS, IV_BASIS, IV_N, IV_FLAG, IV_ORE_ID, IV_ROW0, IV_ROW1 = range(9)
IV_I_COLS = 9
IV_FROM, IV_TO, IV_M, IV_MC, IV_WASTE = range(5)
IV_F_COLS = 5

MODE_TITLES = {
    "strict_excl": "Строгий, исключить пустую породу",
    "strict": "Строгий",
    "soft": "Смягчённый",
    "simple": "Упрощённый",
    "contacts": "По контактам",
}


class OreError(ValueError):
    """The calculation cannot run: {code, message, hint} in Russian, shown to the user."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


def _bad(message: str, hint: str = "Исправьте значение параметра.") -> OreError:
    return OreError("ore_bad_param", message, hint)


@dataclass(frozen=True)
class OreParams:
    """Кондиции and the mode (ТЗ 7.35 Д-26; names — as in the command `composite.ore_intervals`)."""

    cutoff: float  # Сб — бортовое содержание
    m_min: float  # Мрт — минимальная мощность рудного тела
    m_waste_max: float  # Мпп — максимальная мощность прослоя пустых пород
    mc_min: float | None = None  # МС — минимальный метропроцент; None — Сб·Мрт
    c_min: float | None = None  # Смин — минимальное содержание в интервале; None — выключен (= Сб)
    mode: str = "strict_excl"
    missing: str = "break"  # "break" — a missing value ends the line; "waste" — пустая порода до missing_max
    missing_max: float = 0.0  # L_н, m along the hole

    def checked(self) -> OreParams:
        """The parameters the kernel runs with: validated, МС and Смин filled in."""

        def number(value: Any, title: str, *, positive: bool = False) -> float:
            try:
                x = float(value)
            except (TypeError, ValueError):
                raise _bad(f"{title}: ожидается число.") from None
            if not np.isfinite(x) or x < 0.0 or (positive and x == 0.0):
                raise _bad(f"{title}: значение должно быть {'больше нуля' if positive else 'не меньше нуля'}.")
            return x

        cutoff = number(self.cutoff, "Бортовое содержание Сб", positive=True)
        m_min = number(self.m_min, "Минимальная мощность рудного тела Мрт")
        m_waste_max = number(self.m_waste_max, "Максимальная мощность пустого прослоя Мпп")
        mc_min = cutoff * m_min if self.mc_min is None else number(self.mc_min, "Минимальный метропроцент МС")
        c_min = cutoff if self.c_min is None else number(self.c_min, "Минимальное содержание Смин")
        if self.mode not in ou.MODES:
            raise _bad(f"Неизвестный режим «{self.mode}».", "Выберите один из пяти режимов выделения интервалов.")
        if self.missing not in ("break", "waste"):
            raise _bad(f"Пропуски: неизвестное значение «{self.missing}».", "Выберите «Разрыв» или «Пустая порода».")
        missing_max = number(self.missing_max, "Наибольший неопробованный промежуток")
        return replace(
            self, cutoff=cutoff, m_min=m_min, m_waste_max=m_waste_max, mc_min=mc_min, c_min=c_min,
            missing_max=missing_max,
        )  # fmt: skip


@dataclass(frozen=True)
class OreLines:
    """Samples of a set of holes ordered by (hole, FROM): rows `starts[h] : starts[h + 1]` belong to hole h."""

    starts: np.ndarray  # int64[holes + 1]
    frm: np.ndarray  # float64
    to: np.ndarray
    grade: np.ndarray  # NaN — no value
    thick: np.ndarray | None = None  # thickness of a sample; None — its length along the hole
    domain: np.ndarray | None = None  # int32 codes, −1 — outside the domains; None — no domains

    @staticmethod
    def of(
        starts: Any, frm: Any, to: Any, grade: Any, thick: Any | None = None, domain: Any | None = None
    ) -> OreLines:
        s = np.ascontiguousarray(starts, dtype=np.int64)
        cols = [np.ascontiguousarray(a, dtype=np.float64) for a in (frm, to, grade)]
        th = None if thick is None else np.ascontiguousarray(thick, dtype=np.float64)
        dm = None if domain is None else np.ascontiguousarray(domain, dtype=np.int32)
        n = len(cols[0])
        sizes = {len(a) for a in (*cols, *(x for x in (th, dm) if x is not None))}
        if s.ndim != 1 or len(s) < 1 or s[0] != 0 or s[-1] != n or np.any(np.diff(s) < 0) or sizes != {n}:
            raise OreError(
                "ore_bad_lines", "Пробы переданы ядру в неверном виде.", "Сообщите разработчику: это ошибка программы."
            )
        return OreLines(s, cols[0], cols[1], cols[2], th, dm)


@dataclass(frozen=True)
class OreEvents:
    """Protocol events of the kernel (ADR 0018 §9): `i[n, EI_COLS]`, `f[n, EF_COLS]` (`ore_units`)."""

    i: np.ndarray
    f: np.ndarray

    def __len__(self) -> int:
        return len(self.i)

    def records(self) -> list[dict[str, Any]]:
        """Events as dicts with names instead of codes (tests, the protocol of one hole)."""
        out = []
        for a, b in zip(self.i.tolist(), self.f.tolist(), strict=True):
            out.append(
                {
                    "hole": a[ou.EI_HOLE],
                    "line": a[ou.EI_LINE],
                    "step": a[ou.EI_STEP],
                    "stage": a[ou.EI_STAGE],
                    "kind": ou.KINDS[a[ou.EI_KIND]],
                    "side": ou.SIDES[a[ou.EI_SIDE]],
                    "ore": bool(a[ou.EI_ORE]),
                    "compared": ou.COMPARED[a[ou.EI_CMP]],
                    "decision": ou.DECISIONS[a[ou.EI_DEC]],
                    "row0": a[ou.EI_ROW0],
                    "row1": a[ou.EI_ROW1],
                    "seq": a[ou.EI_SEQ],
                    "from": b[ou.EF_FROM],
                    "to": b[ou.EF_TO],
                    "m": b[ou.EF_M],
                    "c": b[ou.EF_C],
                    "mc": b[ou.EF_MC],
                }
            )
        return out


@dataclass(frozen=True)
class OreIntervals:
    """Result of the kernel: interval rows in the order (hole, FROM) and the classes of the samples."""

    params: OreParams
    iv_i: np.ndarray  # int64[rows, IV_I_COLS]
    iv_f: np.ndarray  # float64[rows, IV_F_COLS]
    sample_cls: np.ndarray  # int8 per sample: CLS_* of `ore_units`, −1 — outside the sampling lines
    sample_ore_id: np.ndarray  # int32 per sample: number of its ore interval in the hole (1…k), 0 — none
    sample_state: np.ndarray  # int8 per sample: ROW_* of `ore_units`
    stats: dict[str, int]
    events: OreEvents | None = None

    def __len__(self) -> int:
        return len(self.iv_i)

    @property
    def hole(self) -> np.ndarray:
        return self.iv_i[:, IV_HOLE]

    @property
    def line(self) -> np.ndarray:
        return self.iv_i[:, IV_LINE]

    @property
    def cls(self) -> np.ndarray:
        return self.iv_i[:, IV_CLS]

    @property
    def basis(self) -> np.ndarray:
        return self.iv_i[:, IV_BASIS]

    @property
    def n_samples(self) -> np.ndarray:
        return self.iv_i[:, IV_N]

    @property
    def flag(self) -> np.ndarray:
        """1 — the interval holds a missing stretch counted as waste («пустая порода до L_н»)."""
        return self.iv_i[:, IV_FLAG]

    @property
    def ore_id(self) -> np.ndarray:
        return self.iv_i[:, IV_ORE_ID]

    @property
    def row0(self) -> np.ndarray:
        """Row of the first sample of the interval (−1 — it has no samples)."""
        return self.iv_i[:, IV_ROW0]

    @property
    def row1(self) -> np.ndarray:
        """Row of the last sample of the interval."""
        return self.iv_i[:, IV_ROW1]

    @property
    def frm(self) -> np.ndarray:
        return self.iv_f[:, IV_FROM]

    @property
    def to(self) -> np.ndarray:
        return self.iv_f[:, IV_TO]

    @property
    def m(self) -> np.ndarray:
        return self.iv_f[:, IV_M]

    @property
    def mc(self) -> np.ndarray:
        return self.iv_f[:, IV_MC]

    @property
    def c(self) -> np.ndarray:
        return self.iv_f[:, IV_MC] / self.iv_f[:, IV_M]

    @property
    def waste_len(self) -> np.ndarray:
        return self.iv_f[:, IV_WASTE]

    def rows(self) -> list[dict[str, Any]]:
        """Interval rows as dicts with class and basis names (tests, previews of one hole)."""
        out = []
        for a, b in zip(self.iv_i.tolist(), self.iv_f.tolist(), strict=True):
            out.append(
                {
                    "hole": a[IV_HOLE],
                    "line": a[IV_LINE],
                    "cls": ou.CLASSES[a[IV_CLS]],
                    "basis": ou.BASES[a[IV_BASIS]],
                    "n_samples": a[IV_N],
                    "flag": a[IV_FLAG],
                    "ore_id": a[IV_ORE_ID],
                    "row0": a[IV_ROW0],
                    "row1": a[IV_ROW1],
                    "from": b[IV_FROM],
                    "to": b[IV_TO],
                    "m": b[IV_M],
                    "mc": b[IV_MC],
                    "c": b[IV_MC] / b[IV_M],
                    "waste_len": b[IV_WASTE],
                }
            )
        return out


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
        cap = 64 + 8 * rows if events else 0
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
