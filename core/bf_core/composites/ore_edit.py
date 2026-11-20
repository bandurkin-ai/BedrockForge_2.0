"""Manual edit of the ore intervals of a hole (ТЗ 7.35 Д-26 «Место»: «последнее слово за геологом»; ADR 0018
§12, решение 24): split, merge, move a boundary by the boundaries of the samples — the rows of the hole are
made anew, К, Мпп and the прирезка are checked again, what is broken is named, the rows that differ are MANUAL.

    edit_hole(lines, params, edits, hole=0) -> HoleEdit

Nothing is stored here (the job writes the rows of the hole in one transaction and keeps the edits in
`report.edits`). The kernel is given all the edits of the hole — those stored and the new one last — and
applies them in order to what `ore_intervals` gives for the hole, so the rows of an edited hole are always
its calculation run again plus its edits (as the protocol is, решение 10). Without edits the rows are those of
the kernel bit for bit. An edit that cannot be applied raises `OreError` (`ore_edit_refused`, in Russian).

An edit is `{action, from, to}` — depths along the hole, sample boundaries (within 1e−6 м); an «interval» is a
row `ore` or `subgrade` with NESTED = 0:

    split   from — FROM of the interval, to — the depth of the cut inside it → [FROM; to] and [to; TO]
    merge   from — FROM of the upper interval, to — TO of the lower one → one interval [from; to]; what lies
            between them (waste, an excluded прослой, other intervals) goes inside
    move    from — a boundary as it is (FROM or TO of an interval), to — the new depth; the interval grows over
            waste or shrinks; it never enters another interval (that is `merge`) and never vanishes. Two
            intervals that touch at `from`: the one that shrinks is moved

An interval made by an edit begins and ends with an ore sample (g ≥ Сб) — a boundary at a waste sample is
refused (step 7 of Д-26) — and lies in one sampling line. Its class is counted anew: `ore` with BASIS when it
meets К, else `subgrade` (all its samples). What an edit lets go falls back to the samples: runs of ore
samples — `subgrade`, of waste — `waste` (joined with the waste next to it); an excluded прослой the edit cut
into falls back too. M, MC, WASTE_LEN, the classes of the samples, ORE_ID (1…k from the top) and the nested
rows (`waste_in`, `frame`) are written by the kernels of `ore_intervals`.

MANUAL = 1: an interval made by an edit, its nested rows, and every other row that is not a row of the
calculation (the same class, depths and NESTED) — what an edit let go, waste it cut, a frame it moved.

Violations — for the intervals made by the edits, as the hole stands after the last one (`code`, `message`,
`hint`, `from`, `to` of the interval):

    not_k   the interval does not meet К (it is `subgrade`)
    layer   a fragment «порода … порода» with C < Сб and M > Мпп lies inside (modes «строгий»)
    trim    the прирезка by stages (step 2; «упрощённый» — the growth), run on the samples of the interval
            alone, does not join it into one unit
    join    the next interval of the line would be joined to it by the прирезка: the waste between them is
            not longer than Мпп (or they touch), C of both прирезки ≥ Сб and (modes «строгий») no fragment
            «порода … порода» would appear; «упрощённый» — C of the whole stretch ≥ Сб

«По контактам» has no edit: there the boundaries are those of the domain.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.jit import njit

from . import ore_steps as steps
from . import ore_units as ou
from .ore_intervals import _nested_rows, _put_row, ore_intervals
from .ore_protocol import _num
from .ore_set import label
from .ore_types import (
    IV_F_COLS,
    IV_I_COLS,
    IV_NESTED,
    OreError,
    OreEvents,
    OreIntervals,
    OreLines,
    OreParams,
    Pieces,
    pieces,
)

__all__ = ["ACTIONS", "HoleEdit", "edit_hole", "warm"]

ACTIONS = ("split", "merge", "move")
EPS = ou.GAP_EPS  # m: a depth of an edit within this of a sample boundary is that boundary
_INTERVAL = (ou.CLS_ORE, ou.CLS_SUBGRADE)


def _refuse(message: str, hint: str) -> OreError:
    return OreError("ore_edit_refused", message, hint)


@njit(cache=True, nogil=True)
def _rows(  # type: ignore[no-untyped-def]
    ns, sa, sb, scls, sbasis, line_of, line_start, line_no, hole, cb, mpp,
    p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f,
):  # fmt: skip
    """The rows of a hole from its segments [sa, sb) of pieces with the classes `scls` (and BASIS `sbasis`), in
    the order of the kernel: a row per segment, the nested rows after their `ore`. Returns the rows."""
    ni = np.int64(0)
    ore_no = np.int64(0)
    none = np.int64(0)
    for k in range(ns):
        a = sa[k]
        b = sb[k]
        j = line_of[a]
        if scls[k] != ou.CLS_ORE:
            ni = _put_row(
                a, b, scls[k], none, none, none, hole, line_no[j], cb,
                p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
            )  # fmt: skip
            continue
        ore_no += 1
        ni = _put_row(
            a, b, scls[k], sbasis[k], ore_no, none, hole, line_no[j], cb,
            p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
        )  # fmt: skip
        lo = line_start[j]  # the frame: not above the `ore` row above, not into the one below
        for v in range(k - 1, -1, -1):
            if line_of[sa[v]] != j:
                break
            if scls[v] == ou.CLS_ORE:
                lo = sb[v]
                break
        hi = line_start[j + 1]
        for v in range(k + 1, ns):
            if line_of[sa[v]] != j:
                break
            if scls[v] == ou.CLS_ORE:
                hi = sa[v]
                break
        ni = _nested_rows(
            a, b, lo, hi, ore_no, hole, line_no[j], cb, mpp,
            p_row, p_from, p_to, p_l, p_g, p_miss, s_cls, s_ore, iv_i, iv_f, ni,
        )  # fmt: skip
    return ni


@njit(cache=True, nogil=True)
def _check(  # type: ignore[no-untyped-def]
    a, b, simple, strict, p_row, p_from, p_to, p_l, p_g, cb, mpp, c_min, m_min, mc_min,
    eb, eo, em, emc, ub, uo, um, umc, uchg, wb, wo, wm, wmc, wchg, acc, ev_i, ev_f,
):  # fmt: skip
    """The pieces [a, b) as a line of their own: the first fragment «порода … порода» (strict modes) and the
    series the прирезка by stages (or the growth of «упрощённый») ends with — in (ub, uo).
    Returns (first piece of the fragment or −1, its end, its M, its MC, units of the series)."""
    zero = np.int64(0)
    nel = ou.elementary(a, b, p_l, p_g, cb, eb, eo, em, emc)
    la = np.int64(-1)
    lb = np.int64(-1)
    lm = 0.0
    lmc = 0.0
    if strict:
        s, t = ou.find_layer(zero, nel, zero, eo, em, emc, cb, mpp)
        if s >= 0:
            la = eb[s]
            lb = eb[t + 1]
            for k in range(s, t + 1):
                lm += em[k]
                lmc += emc[k]
    nu = nel
    for k in range(nel):
        ub[k] = eb[k]
        uo[k] = eo[k]
        um[k] = em[k]
        umc[k] = emc[k]
    ub[nel] = eb[nel]
    ne = np.int64(0)
    if simple:
        nu, ne = ou.grow_simple(nu, ub, uo, um, umc, cb, zero, zero, p_row, p_from, p_to, ev_i, ev_f, ne)
    else:
        nu, ne, _ = steps.trim_stages(
            nu, ub, uo, um, umc, uchg, wb, wo, wm, wmc, wchg, acc, cb, mpp, c_min, m_min, mc_min,
            zero, zero, p_row, p_from, p_to, ev_i, ev_f, ne,
        )  # fmt: skip
    return la, lb, lm, lmc, nu


def _ge(a: float, b: float) -> bool:
    """`ore_units.ge`: a ≥ b, equal within TOL counts as equal."""
    return a >= b - ou.TOL * max(abs(a), abs(b))


def _le(a: float, b: float) -> bool:
    return a <= b + ou.TOL * max(abs(a), abs(b))


@dataclass(frozen=True)
class HoleEdit:
    """A hole after its edits. `intervals` — its rows of all classes, the nested ones too, and the classes of
    its samples (the rows of the samples are counted from the first sample of the hole); `manual` — MANUAL
    of every row; `made` — 1 for the intervals made by the edits; `violations` — what they break; `edits` —
    the edits applied."""

    hole: int
    intervals: OreIntervals
    manual: np.ndarray
    made: np.ndarray
    violations: list[dict[str, Any]]
    edits: list[dict[str, Any]]

    def events(self) -> OreEvents:
        """The records of the protocol (step 6 — ручная правка): the intervals made by the edits as they
        stand — the samples, the depths, M, C, MC, кондиционный (by Мрт or by МС) or некондиционный."""
        iv = self.intervals
        rows = np.flatnonzero(self.made)
        ev_i = np.zeros((len(rows), ou.EI_COLS), np.int64)
        ev_f = np.empty((len(rows), ou.EF_COLS), np.float64)
        basis = iv.basis[rows]
        ev_i[:, ou.EI_HOLE] = iv.hole[rows]
        ev_i[:, ou.EI_LINE] = iv.line[rows]
        ev_i[:, ou.EI_STEP] = ou.STEP_EDIT
        ev_i[:, ou.EI_KIND] = ou.K_INTERVAL
        ev_i[:, ou.EI_ORE] = 1
        ev_i[:, ou.EI_CMP] = np.choose(basis, [ou.CMP_K, ou.CMP_MRT, ou.CMP_MC])
        ev_i[:, ou.EI_DEC] = np.where(basis > 0, ou.D_ORE, ou.D_SUBGRADE)
        ev_i[:, ou.EI_ROW0] = iv.row0[rows]
        ev_i[:, ou.EI_ROW1] = iv.row1[rows]
        ev_i[:, ou.EI_SEQ] = iv.ore_id[rows]
        ev_f[:, ou.EF_FROM] = iv.frm[rows]
        ev_f[:, ou.EF_TO] = iv.to[rows]
        ev_f[:, ou.EF_M] = iv.m[rows]
        ev_f[:, ou.EF_C] = iv.mc[rows] / iv.m[rows]
        ev_f[:, ou.EF_MC] = iv.mc[rows]
        return OreEvents(ev_i, ev_f)

    def rows(self) -> list[dict[str, Any]]:
        """`OreIntervals.rows` with `manual`."""
        return [{**r, "manual": m} for r, m in zip(self.intervals.rows(), self.manual.tolist(), strict=True)]

    def columns(self, unit: str = "") -> dict[str, Any]:
        """The rows as the preview and the protocol answer them: `{from, to, values{CLASS, …, MANUAL, LABEL}}`."""
        rows = self.rows()
        values = {
            "CLASS": [r["cls"] for r in rows],
            "NESTED": [r["nested"] for r in rows],
            "MANUAL": [r["manual"] for r in rows],
            "M": [r["m"] for r in rows],
            "C": [r["c"] for r in rows],
            "MC": [r["mc"] for r in rows],
            "BASIS": [r["basis"] for r in rows],
            "ORE_ID": [r["ore_id"] for r in rows],
            "N_SAMPLES": [r["n_samples"] for r in rows],
            "WASTE_LEN": [r["waste_len"] for r in rows],
            "FLAG": [r["flag"] for r in rows],
            "LABEL": [label(r["m"], r["c"], r["mc"], unit) if r["cls"] == "ore" else "" for r in rows],
        }
        return {"from": [r["from"] for r in rows], "to": [r["to"] for r in rows], "values": values}


class _Hole:
    """The pieces of a hole and its top-level rows as segments [a, b, class, BASIS, made by an edit]."""

    def __init__(self, sub: OreLines, p: OreParams) -> None:
        self.p = p
        self.sub = sub
        self.auto = ore_intervals(sub, p, nested=True)
        self.pc: Pieces = pieces(sub, p)
        pc = self.pc
        self.frm: list[float] = pc.frm.tolist()
        self.to: list[float] = pc.to.tolist()
        self.thick: list[float] = pc.thick.tolist()
        self.grade: list[float] = pc.grade.tolist()
        self.ore: list[bool] = (pc.grade >= p.cutoff - ou.TOL * np.maximum(np.abs(pc.grade), abs(p.cutoff))).tolist()
        self.line_of = np.repeat(np.arange(len(pc.line_no), dtype=np.int64), np.diff(pc.line_start))
        self.line: list[int] = self.line_of.tolist()
        top = {f: k for k, f in enumerate(self.frm)}
        end = {t: k + 1 for k, t in enumerate(self.to)}
        self.segs: list[list[Any]] = []
        self.calculated: set[tuple[Any, ...]] = set()  # the rows of the calculation: (nested, class, from, to)
        iv = self.auto
        for f, t, c, b, nested in zip(
            iv.frm.tolist(), iv.to.tolist(), iv.cls.tolist(), iv.basis.tolist(), iv.nested.tolist(), strict=True
        ):
            self.calculated.add((nested, c, f, t))
            if not nested:
                self.segs.append([top[f], end[t], c, b, False])

    # --- depths and sums ---

    def span(self, a: int, b: int) -> str:
        return f"{_num(self.frm[a], 2)}–{_num(self.to[b - 1], 2)} м"

    def starts_at(self, depth: float) -> int:
        """The piece that begins at the depth, −1 — none."""
        k = bisect_left(self.frm, depth - EPS)
        return k if k < len(self.frm) and abs(self.frm[k] - depth) <= EPS else -1

    def ends_at(self, depth: float) -> int:
        """The end of the piece that ends at the depth (the index after it), −1 — none."""
        k = bisect_left(self.to, depth - EPS)
        return k + 1 if k < len(self.to) and abs(self.to[k] - depth) <= EPS else -1

    def sums(self, a: int, b: int) -> tuple[float, float]:
        """M and MC of the pieces [a, b), summed as the kernel sums a row."""
        m = 0.0
        mc = 0.0
        for k in range(a, b):
            m += self.thick[k]
            mc += self.thick[k] * self.grade[k]
        return m, mc

    def basis(self, m: float, mc: float) -> int:
        """`ore_units.cond_k`."""
        p = self.p
        if not m > 0.0 or not _ge(mc / m, p.c_min):  # type: ignore[arg-type]
            return ou.BASIS_NONE
        if _ge(m, p.m_min):
            return ou.BASIS_THICKNESS
        return ou.BASIS_MC if _ge(mc, p.mc_min) else ou.BASIS_NONE  # type: ignore[arg-type]

    # --- segments ---

    def made(self, a: int, b: int) -> list[Any]:
        """An interval made by an edit: it begins and ends with an ore sample, lies in one line; К gives its class."""
        if self.line[a] != self.line[b - 1]:
            raise _refuse(
                f"Интервал {self.span(a, b)} пересекает разрыв линии опробования (пропуск или границу домена).",
                "Интервал должен лежать в одной линии опробования: выберите границы по одну сторону разрыва.",
            )
        for k in (a, b - 1):
            if not self.ore[k]:
                raise _refuse(
                    f"Граница на безрудной пробе {self.span(k, k + 1)}: интервал {self.span(a, b)} должен "
                    "начинаться и кончаться рудной пробой (содержание ≥ Сб).",
                    "Сдвиньте границу к ближайшей рудной пробе.",
                )
        basis = self.basis(*self.sums(a, b))
        return [a, b, ou.CLS_ORE if basis else ou.CLS_SUBGRADE, basis, True]

    def fallback(self, a: int, b: int) -> list[list[Any]]:
        """The pieces [a, b) outside the intervals: runs of ore samples — `subgrade`, of waste — `waste`."""
        out: list[list[Any]] = []
        for k in range(a, b):
            cls = ou.CLS_SUBGRADE if self.ore[k] else ou.CLS_WASTE
            if out and out[-1][2] == cls and self.line[k] == self.line[k - 1]:
                out[-1][1] = k + 1
            else:
                out.append([k, k + 1, cls, 0, False])
        return out

    def interval_from(self, depth: float) -> int:
        """The index of the interval that begins at the depth, −1 — none."""
        k = self.starts_at(depth)
        return next((i for i, s in enumerate(self.segs) if s[0] == k and s[2] in _INTERVAL), -1)

    def interval_to(self, depth: float) -> int:
        k = self.ends_at(depth)
        return next((i for i, s in enumerate(self.segs) if s[1] == k and s[2] in _INTERVAL), -1)

    def put(self, new: list[list[Any]]) -> None:
        """The segments `new` (in order, adjacent) take the place of what they cover: a segment cut into gives
        way — waste stays waste, an excluded прослой falls back to its samples — and waste next to waste of
        the same line is one row."""
        a, b = new[0][0], new[-1][1]
        out: list[list[Any]] = []
        for s in self.segs:
            if s[1] <= a or s[0] >= b:
                out.append(s)
                continue
            for x, y in ((s[0], min(s[1], a)), (max(s[0], b), s[1])):  # what is left of it above and below
                if x < y:
                    out += [[x, y, s[2], 0, False]] if s[2] == ou.CLS_WASTE else self.fallback(x, y)
        out = sorted(out + new, key=lambda s: s[0])
        self.segs = []
        for s in out:
            last = self.segs[-1] if self.segs else None
            if last and last[2] == s[2] == ou.CLS_WASTE and self.line[last[0]] == self.line[s[0]]:
                self.segs[-1] = [last[0], s[1], ou.CLS_WASTE, 0, False]
            else:
                self.segs.append(s)

    # --- the edits ---

    def split(self, frm: float, to: float) -> None:
        i = self.interval_from(frm)
        if i < 0:
            raise _refuse(
                f"Интервал с началом {_num(frm, 2)} м не найден среди рудных интервалов скважины.",
                "Укажите FROM рудного интервала; обновите окно, если результат изменился.",
            )
        a, b = self.segs[i][0], self.segs[i][1]
        x = self.starts_at(to)
        if not a < x < b:
            raise _refuse(
                f"Глубина разреза {_num(to, 2)} м не совпадает с границей проб внутри интервала {self.span(a, b)}.",
                "Выберите границу между пробами строго внутри интервала.",
            )
        self.put([self.made(a, x), self.made(x, b)])

    def merge(self, frm: float, to: float) -> None:
        i, j = self.interval_from(frm), self.interval_to(to)
        if i < 0 or j < 0 or j <= i:
            raise _refuse(
                f"Объединить {_num(frm, 2)}–{_num(to, 2)} м нельзя: нужны начало верхнего интервала и конец нижнего.",
                "Укажите FROM верхнего и TO нижнего из двух (или более) рудных интервалов скважины.",
            )
        self.put([self.made(self.segs[i][0], self.segs[j][1])])

    def move(self, frm: float, to: float) -> None:
        lower, upper = self.interval_from(frm), self.interval_to(frm)  # its top is at `frm`; its bottom is
        if lower < 0 and upper < 0:
            raise _refuse(
                f"Граница {_num(frm, 2)} м не найдена: это не начало и не конец рудного интервала.",
                "Укажите FROM или TO рудного интервала; обновите окно, если результат изменился.",
            )
        if abs(to - frm) <= EPS:
            raise _refuse("Граница уже на этой глубине.", "Укажите другую глубину.")
        top = lower >= 0 and (upper < 0 or to > frm)  # two intervals touch here: the one that shrinks is moved
        a, b = self.segs[lower if top else upper][:2]
        x = self.starts_at(to) if top else self.ends_at(to)
        if x < 0:
            raise _refuse(
                f"Новая граница {_num(to, 2)} м не совпадает с границей проб.", "Выберите глубину границы пробы."
            )
        if (x >= b) if top else (x <= a):
            raise _refuse(
                f"Интервал {self.span(a, b)} исчезает: граница уходит за его другой конец.",
                "Сдвиньте границу в пределах интервала или удалите интервал перестроением результата.",
            )
        grown = (x, a) if top else (b, x)  # the pieces the interval takes; empty when it shrinks
        for s in self.segs:
            if s[2] in _INTERVAL and s[0] < grown[1] and s[1] > grown[0]:
                raise _refuse(
                    f"Граница заходит в соседний интервал {self.span(s[0], s[1])}.",
                    "Чтобы соединить интервалы, используйте «Объединить».",
                )
        new = self.made(x, b) if top else self.made(a, x)
        let_go = self.fallback(a, x) if top else self.fallback(x, b)  # empty when the interval grows
        self.put([*let_go, new] if top else [new, *let_go])

    def apply(self, edit: Mapping[str, Any]) -> dict[str, Any]:
        action = edit.get("action")
        try:
            frm, to = float(edit["from"]), float(edit["to"])
        except (KeyError, TypeError, ValueError):
            frm = to = math.nan
        if action not in ACTIONS or not (math.isfinite(frm) and math.isfinite(to)):
            raise OreError(
                "ore_bad_param",
                "Правка задана неверно: нужны действие (разделить, объединить, сдвинуть) и две глубины.",
                "Повторите правку из окна результата.",
            )
        {"split": self.split, "merge": self.merge, "move": self.move}[action](frm, to)
        return {"action": action, "from": frm, "to": to}

    # --- the result ---

    def result(self, hole: int) -> tuple[OreIntervals, np.ndarray, np.ndarray]:
        pc, p = self.pc, self.p
        ns = len(self.segs)
        sa, sb, scls, sbasis = (np.array([s[k] for s in self.segs], np.int64) for k in range(4))
        room = 3 * ns + len(pc.row) + 1
        iv_i = np.empty((room, IV_I_COLS), np.int64)
        iv_f = np.empty((room, IV_F_COLS), np.float64)
        s_cls = np.full(len(self.sub.frm), ou.CLS_NONE, np.int8)
        s_ore = np.zeros(len(self.sub.frm), np.int32)
        ni = _rows(
            ns, sa, sb, scls, sbasis, self.line_of, pc.line_start, pc.line_no,
            hole, p.cutoff, p.m_waste_max, pc.row, pc.frm, pc.to, pc.thick, pc.grade, pc.miss,
            s_cls, s_ore, iv_i, iv_f,
        )  # fmt: skip
        iv = OreIntervals(p, iv_i[:ni].copy(), iv_f[:ni].copy(), s_cls, s_ore, pc.state, dict(self.auto.stats))
        manual = np.zeros(ni, np.int8)
        made = np.zeros(ni, np.int8)
        k = -1
        by_edit = False
        keys = zip(iv.iv_i[:, IV_NESTED].tolist(), iv.cls.tolist(), iv.frm.tolist(), iv.to.tolist(), strict=True)
        for r, key in enumerate(keys):
            if not key[0]:  # the rows with NESTED = 0 go in the order of the segments; the nested ones follow theirs
                k += 1
                by_edit = self.segs[k][4]
                made[r] = by_edit
            manual[r] = by_edit or key not in self.calculated
        return iv, manual, made

    def violations(self) -> list[dict[str, Any]]:
        """What the intervals made by the edits break."""
        p, pc = self.p, self.pc
        strict = p.mode in ("strict", "strict_excl")
        room = len(pc.row) + 2
        i8 = [np.empty(room, np.int8) for _ in range(5)]
        i64 = [np.empty(room, np.int64) for _ in range(3)]
        f64 = [np.empty(room, np.float64) for _ in range(6)]
        none = (np.empty((0, ou.EI_COLS), np.int64), np.empty((0, ou.EF_COLS), np.float64))

        def check(a: int, b: int) -> tuple[int, int, float, float, int]:
            return _check(  # type: ignore[no-any-return]
                a, b, p.mode == "simple", strict, pc.row, pc.frm, pc.to, pc.thick, pc.grade,
                p.cutoff, p.m_waste_max, p.c_min, p.m_min, p.mc_min,
                i64[0], i8[0], f64[0], f64[1], i64[1], i8[1], f64[2], f64[3], i8[2],
                i64[2], i8[3], f64[4], f64[5], i8[4], np.empty(room, np.int8), *none,
            )  # fmt: skip

        out: list[dict[str, Any]] = []

        def add(code: str, a: int, b: int, message: str, hint: str) -> None:
            out.append({"code": code, "message": message, "hint": hint, "from": self.frm[a], "to": self.to[b - 1]})

        cb, mpp = _num(p.cutoff, 3), _num(p.m_waste_max, 2)
        for i, (a, b, cls, _, made) in enumerate(self.segs):
            if cls not in _INTERVAL:
                continue
            m, mc = self.sums(a, b)
            if made:
                if cls == ou.CLS_SUBGRADE:
                    why = (
                        f"C {_num(mc / m, 3)} < Смин {_num(p.c_min, 3)}"
                        if not _ge(mc / m, p.c_min)  # type: ignore[arg-type]
                        else f"M {_num(m, 2)} м < Мрт {_num(p.m_min, 2)} м и MC {_num(mc, 2)} < МС {_num(p.mc_min, 2)}"
                    )
                    add("not_k", a, b, f"Интервал {self.span(a, b)} не отвечает условию К: {why} — некондиционный.",
                        "Объедините его с соседним интервалом или верните границу.")  # fmt: skip
                la, lb, lm, lmc, units = check(a, b)
                if la >= 0:
                    add("layer", a, b,
                        f"В интервале {self.span(a, b)} пустой прослой {self.span(la, lb)}: {_num(lm, 2)} м @ "
                        f"{_num(lmc / lm, 3)} (M > Мпп {mpp} м, C < Сб {cb}).",
                        "Разделите интервал по прослою или сдвиньте границу.")  # fmt: skip
                if cls == ou.CLS_ORE and units != 1:
                    parts = [
                        self.span(int(i64[1][u]), int(i64[1][u + 1])) for u in range(units) if i8[1][u] == ou.U_ORE
                    ]
                    left = (
                        ", ".join(parts[:4]) + (" …" if len(parts) > 4 else "")
                        if parts
                        else "рудных частей не остаётся"
                    )
                    rule = "наращиванием" if p.mode == "simple" else "прирезкой по этапам"
                    add("trim", a, b, f"Интервал {self.span(a, b)} не получается {rule}: {left}.",
                        "Проверьте границы: средневзвешенное содержание каждой прирезки должно быть ≥ Сб.")  # fmt: skip
            nxt = next((s for s in self.segs[i + 1 :] if s[2] != ou.CLS_WASTE), None)
            if nxt is None or nxt[2] not in _INTERVAL or not (made or nxt[4]) or self.line[nxt[0]] != self.line[a]:
                continue  # the pair is as the calculation left it, or an excluded прослой lies between
            gap_m = self.sums(b, nxt[0])[0]
            m_up, mc_up = self.sums(a, nxt[0])
            m_low, mc_low = self.sums(b, nxt[1])
            m_all, mc_all = self.sums(a, nxt[1])
            both = f"{self.span(a, b)} и {self.span(nxt[0], nxt[1])}"
            between = "примыкают друг к другу" if nxt[0] == b else f"промежуток {_num(gap_m, 2)} м ≤ Мпп {mpp} м"
            if p.mode == "simple":
                if _ge(mc_all / m_all, p.cutoff):
                    add("join", a, nxt[1], f"Интервалы {both} по правилу наращивания объединяются: среднее "
                        f"содержание {_num(mc_all / m_all, 3)} ≥ Сб {cb}.", "Объедините интервалы.")  # fmt: skip
            elif _le(gap_m, p.m_waste_max) and _ge(mc_up / m_up, p.cutoff) and _ge(mc_low / m_low, p.cutoff):
                if not strict or check(a, nxt[1])[0] < 0:
                    add("join", a, nxt[1], f"Интервалы {both} по правилу прирезки объединяются: {between}, C прирезок "
                        f"{_num(mc_up / m_up, 3)} и {_num(mc_low / m_low, 3)} ≥ Сб {cb}.",
                        "Объедините интервалы или раздвиньте их границы.")  # fmt: skip
        return out


def edit_hole(lines: OreLines, params: OreParams, edits: Sequence[Mapping[str, Any]], hole: int = 0) -> HoleEdit:
    """Hole `hole` of the lines after its `edits` (`{action, from, to}`, applied in order to the rows the
    kernel gives for it). An edit that cannot be applied raises `OreError` — nothing is to be stored then."""
    p = params.checked()
    state = _Hole(lines.holes(hole), p)
    if edits and p.mode == "contacts":
        raise _refuse(
            "В режиме «По контактам» границы интервалов задаёт домен: ручная правка недоступна.",
            "Измените домен (кодирование каркасом) и перестройте результат.",
        )
    applied = [state.apply(e) for e in edits]
    intervals, manual, made = state.result(hole)
    return HoleEdit(hole, intervals, manual, made, state.violations() if applied else [], applied)


def warm() -> None:
    """The kernels of the edit once, with the dtypes of the route (`composites/warm.py`): the rows of a hole,
    the checks of the прирезка by stages and of the growth of «упрощённый»."""
    grades = [0.1, 1.8, 0.8, 5.6, 0.7, 4.3, 0.2, 0.3, 2.0, 0.1]
    lines = OreLines.of([0, 10], np.arange(10.0), np.arange(1.0, 11.0), grades)
    edit_hole(lines, OreParams(1.0, 2.0, 2.0), [{"action": "merge", "from": 1.0, "to": 9.0}])
    edit_hole(lines, OreParams(1.0, 2.0, 2.0, mode="simple"), [{"action": "move", "from": 9.0, "to": 6.0}])
