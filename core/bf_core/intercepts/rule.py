"""Rule 8.5.3 of the ore intercepts (ТЗ 8.5.3; ADR 0021 §7, решения 10–11): candidates, acceptance, «в том числе».

    run(lines, grade, params, density=None, tt=None, threads=1, tick=None) -> Outcome
        the whole rule: candidates → acceptance by L_min or m_min → «в том числе» by c2 inside the accepted
    candidates(lines, grade, cutoff, waste_max, density=None, missing="waste", within=None, …) -> Candidates
        steps 1–2; `within` — accepted candidates: the same steps on the samples inside each of them
    accept(candidates, l_min, min_by="length", tt=None) -> int8 reasons (REASON_OK — accepted)        step 3
    is_ore(grade, cutoff) -> bool                                                 g ≥ c as the rule compares it

`lines` — the samples or composites of a set ordered by (hole, FROM): `composites.intervals.build_lines` (rows
with TO ≤ FROM and rows that overlap the one before take no part and are counted there); `grade`, `density` —
columns in line order (`lines.take(column)`). The rule does not know domains (ТЗ 8.5.3 has none).

The rule, word for word. A hole is cut into pieces: a sample is a piece, a stretch without samples between two
samples (longer than `DEPTH_EPS`) is a piece too. A piece is ore when its grade g ≥ c. A piece without a grade
(NaN, ±inf; a stretch without samples) is «missing»: with `missing="waste"` (the default of the ТЗ) it is waste
with g = 0 and its length is counted in MISS_LEN; with `missing="break"` nothing is joined across it. It is
never ore.
  1. Consecutive ore pieces are a series R_1 … R_k.
  2. Left to right: тек = R_1; for R_(i+1): if the waste between them is ≤ L_п and the mean of
     тек ∪ waste ∪ R_(i+1) is ≥ c, тек becomes that union, else тек is closed (a candidate) and тек = R_(i+1).
     L_min plays no part here.
  3. A candidate is accepted when LENGTH ≥ L_min, or — `min_by="tt"` — when its true thickness TT ≥ m_min (the
     same number); a candidate without TT (no orientation) is checked by its length.
  4. «В том числе»: steps 1–3 with the cut-off c2 > c on the pieces of every accepted candidate.
The mean is Σ l·g / Σ l; with a density — Σ l·ρ·g / Σ l·ρ, where a piece without a density (NaN, ≤ 0; a
stretch without samples) takes the mean density of the fragment, Σ l·ρ / Σ l over its pieces that have one (as
`intervals.aggregate`); no density in the fragment — the mean by length.

Numbers (ADR 0021 §2, as ADR 0018 решение 5). The comparisons g ≥ c, waste ≤ L_п, mean ≥ c, LENGTH ≥ L_min,
TT ≥ m_min carry the relative tolerance 1e-9 towards equality: 0,7 + 0,1 + 0,1 is ore at c = 0,9. Every sum is
taken directly over the pieces of its fragment, left to right — LENGTH is Σ l of the pieces, not TO − FROM,
LG (метропроцент) is Σ l·g — never as a difference of running sums; a fragment grown by a series only goes on
adding, which is the same additions in the same order.

Strips (`composites.strips`): 4096 holes (4096 accepted candidates for «в том числе») at a time on Python
threads over the `nogil` kernel, count → offsets → fill; a strip writes only its rows — the same bytes with 1,
2, 4 and 8 threads. `tick(fraction)` is called after every strip and may raise to cancel.

numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from bf_core.composites import strips
from bf_core.composites.intervals import DEPTH_EPS, CompositeError, Lines
from bf_core.composites.intervals_kernels import REL
from bf_core.composites.strips import Tick
from bf_core.jit import njit

from . import INCLUDE_REL

__all__ = [
    "KIND_INCL", "KIND_MAIN", "KIND_TITLES", "REASON_OK", "REASON_SHORT", "REASON_THIN", "REASON_TITLES",
    "Candidates", "Intercepts", "Outcome", "RuleParams", "accept", "candidates", "is_ore", "run",
]  # fmt: skip

KIND_MAIN, KIND_INCL = 0, 1
KIND_TITLES = ("основное", "в том числе")  # KIND of a row of the table
REASON_OK, REASON_SHORT, REASON_THIN = 0, 1, 2  # why a candidate is not an intercept
REASON_TITLES = {REASON_SHORT: "короче L_min", REASON_THIN: "ИМ меньше m_min"}
MISSING = ("waste", "break")
MIN_BY = ("length", "tt")

# TT of candidates: called with their `Lines`, returns float64 [n], NaN — no orientation (ADR 0021 §3–6).
TrueThickness = Callable[[Lines], Any]

_ORE, _WASTE, _MISS = 0, 1, 2  # kinds of a piece
_CUR, _JOIN, _NEW = 0, 1, 2  # rows of the sums: тек, тек ∪ waste ∪ the series at hand, the series alone
# columns of the sums: Σ l, Σ l·g; of the pieces with a density Σ l·ρ·g, Σ l·ρ, Σ l; of those without Σ l·g, Σ l;
# Σ l of the pieces that are not ore, Σ l of the missing ones
_L, _LG, _A, _C, _E, _B, _D, _W, _M = range(9)
_SUMS = 9
I_SEG, I_FIRST, I_LAST = range(3)  # columns of a candidate: the segment (hole or parent), its rows [first, last)
F_FROM, F_TO, F_LEN, F_GRADE, F_LG, F_WASTE, F_MISS = range(7)
_EMPTY = np.zeros(0, dtype=np.float64)

# --- kernel ------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _ge(a, b):  # type: ignore[no-untyped-def]
    """a ≥ b; equal within REL counts as equal."""
    return a >= b - REL * max(abs(a), abs(b))


@njit(cache=True, nogil=True)
def _le(a, b):  # type: ignore[no-untyped-def]
    """a ≤ b; equal within REL counts as equal."""
    return a <= b + REL * max(abs(a), abs(b))


@njit(cache=True, nogil=True)
def _add(acc, k, length, g, rho, weighted, kind):  # type: ignore[no-untyped-def]
    """A piece into the sums of row k."""
    acc[k, _L] += length
    acc[k, _LG] += length * g
    if weighted:
        if rho > 0.0 and math.isfinite(rho):
            lw = length * rho
            acc[k, _A] += lw * g
            acc[k, _C] += lw
            acc[k, _E] += length
        else:
            acc[k, _B] += length * g
            acc[k, _D] += length
    if kind != _ORE:
        acc[k, _W] += length
        if kind == _MISS:
            acc[k, _M] += length


@njit(cache=True, nogil=True)
def _mean(acc, k, weighted):  # type: ignore[no-untyped-def]
    """The mean grade of the fragment of row k: by density where a piece of it has one, else by length."""
    if weighted and acc[k, _E] > 0.0:
        rho = acc[k, _C] / acc[k, _E]
        return (acc[k, _A] + rho * acc[k, _B]) / (acc[k, _C] + rho * acc[k, _D])
    return acc[k, _LG] / acc[k, _L]


@njit(cache=True, nogil=True)
def _emit(fill, out_i, out_f, k, seg, first, last, frm, to, acc, weighted):  # type: ignore[no-untyped-def]
    """тек is closed: candidate k of the rows first ≤ r < last. Returns k + 1."""
    if fill:
        out_i[k, I_SEG] = seg
        out_i[k, I_FIRST] = first
        out_i[k, I_LAST] = last
        out_f[k, F_FROM] = frm[first]
        out_f[k, F_TO] = to[last - 1]
        out_f[k, F_LEN] = acc[_CUR, _L]
        out_f[k, F_GRADE] = _mean(acc, _CUR, weighted)
        out_f[k, F_LG] = acc[_CUR, _LG]
        out_f[k, F_WASTE] = acc[_CUR, _W]
        out_f[k, F_MISS] = acc[_CUR, _M]
    return k + 1


@njit(cache=True, nogil=True)
def scan_strip(  # type: ignore[no-untyped-def]
    s0, s1, seg_first, seg_last, frm, to, grade, rho, cutoff, waste_max, as_waste, counts, starts, out_i, out_f, fill
):
    """Steps 1–2 on the segments s0 ≤ s < s1: segment s is the rows seg_first[s] ≤ r < seg_last[s] of the
    lines (a hole, or an accepted candidate). fill = False — counts[s] candidates; True — they are written
    from starts[s]. `rho` — empty: no density."""
    weighted = rho.shape[0] > 0
    acc = np.zeros((3, _SUMS), dtype=np.float64)
    room = 1
    for s in range(s0, s1):
        room = max(room, 2 * (seg_last[s] - seg_first[s]))
    p_len = np.empty(room, dtype=np.float64)
    p_g = np.empty(room, dtype=np.float64)
    p_rho = np.empty(room, dtype=np.float64)
    p_kind = np.empty(room, dtype=np.int8)
    p_row = np.empty(room, dtype=np.int64)
    for s in range(s0, s1):
        r0, r1 = seg_first[s], seg_last[s]
        n = 0  # the pieces of the segment
        for r in range(r0, r1):
            if r > r0 and frm[r] - to[r - 1] > DEPTH_EPS:  # a stretch without samples
                p_len[n] = frm[r] - to[r - 1]
                p_g[n] = 0.0
                p_rho[n] = math.nan
                p_kind[n] = _MISS
                p_row[n] = -1
                n += 1
            g = grade[r]
            p_len[n] = to[r] - frm[r]
            p_rho[n] = rho[r] if weighted else math.nan
            p_row[n] = r
            if math.isfinite(g):
                p_g[n] = g
                p_kind[n] = _ORE if _ge(g, cutoff) else _WASTE
            else:
                p_g[n] = 0.0
                p_kind[n] = _MISS
            n += 1
        k0 = starts[s] if fill else 0
        k = k0
        have = False  # тек is open
        series = False  # a series is being read
        cut = False  # nothing can be joined to тек any more: the waste after it is too long
        gap = 0.0  # the waste after тек
        cur_first, cur_last, new_first, new_last = 0, 0, 0, 0
        for j in range(n + 1):
            kind = p_kind[j] if j < n else -1  # −1 — the end of the segment
            if kind == _ORE:
                if not series:
                    series = True
                    new_first = p_row[j]
                    for c in range(_SUMS):
                        acc[_NEW, c] = 0.0
                new_last = p_row[j] + 1
                _add(acc, _NEW, p_len[j], p_g[j], p_rho[j], weighted, kind)
                if have and not cut:
                    _add(acc, _JOIN, p_len[j], p_g[j], p_rho[j], weighted, kind)
                continue
            if series:  # the series is whole: it joins тек or closes it and becomes тек
                series = False
                if have and not cut and _le(gap, waste_max) and _ge(_mean(acc, _JOIN, weighted), cutoff):
                    for c in range(_SUMS):
                        acc[_CUR, c] = acc[_JOIN, c]
                else:
                    if have:
                        k = _emit(fill, out_i, out_f, k, s, cur_first, cur_last, frm, to, acc, weighted)
                    for c in range(_SUMS):
                        acc[_CUR, c] = acc[_NEW, c]
                        acc[_JOIN, c] = acc[_NEW, c]
                    cur_first = new_first
                    have = True
                cur_last = new_last
                gap = 0.0
                cut = False
            if kind < 0:
                break
            if kind == _MISS and not as_waste:  # a break: тек is closed, nothing is joined across it
                if have:
                    k = _emit(fill, out_i, out_f, k, s, cur_first, cur_last, frm, to, acc, weighted)
                    have = False
                continue
            if have and not cut:
                gap += p_len[j]
                if _le(gap, waste_max):
                    _add(acc, _JOIN, p_len[j], p_g[j], p_rho[j], weighted, kind)
                else:
                    cut = True
        if have:
            k = _emit(fill, out_i, out_f, k, s, cur_first, cur_last, frm, to, acc, weighted)
        if not fill:
            counts[s] = k - k0


# --- parameters --------------------------------------------------------------------------------------------


def _bad(param: str, message: str, hint: str = "Исправьте значение параметра.") -> CompositeError:
    return CompositeError("bad_param", message, hint, param=param)


@dataclass(frozen=True)
class RuleParams:
    """The parameters of the rule (names — as in the command `intercept.run`; defaults — ТЗ 8.5.4). The ranges
    of the window (L_min 0–100 м, L_п 0–50 м) are the business of `intercepts/spec.py`."""

    cutoff: float  # c — борт
    l_min: float = 2.0  # L_min, m along the hole; with min_by="tt" — m_min, m of true thickness
    waste_max: float = 2.0  # L_п — the longest waste inside an intercept, m
    min_by: str = "length"  # "length" | "tt"
    include_cutoff: float | None = None  # c2 > c — «в том числе»; None — none
    missing: str = "waste"  # "waste" — a missing grade is waste with g = 0; "break" — it breaks the intercept

    def checked(self) -> RuleParams:
        """The parameters the kernel runs with; `CompositeError` (`bad_param`, `param`) on a wrong one."""

        def number(name: str, title: str, *, positive: bool = False) -> float:
            try:
                x = float(getattr(self, name))
            except (TypeError, ValueError):
                raise _bad(name, f"{title}: ожидается число.") from None
            if not math.isfinite(x) or x < 0.0 or (positive and x == 0.0):
                raise _bad(name, f"{title}: значение должно быть {'больше нуля' if positive else 'не меньше нуля'}.")
            return x

        cutoff = number("cutoff", "Бортовое содержание", positive=True)
        l_min = number("l_min", "Наименьшая длина пересечения L_min")
        waste_max = number("waste_max", "Наибольшая внутренняя пустая порода L_п")
        if self.min_by not in MIN_BY:
            raise _bad(
                "min_by", f"Критерий приёмки: неизвестное значение «{self.min_by}».",
                "Выберите «по длине» или «по истинной мощности».",
            )  # fmt: skip
        if self.missing not in MISSING:
            raise _bad(
                "missing", f"Пропуски: неизвестное значение «{self.missing}».",
                "Выберите «Пустая порода» или «Разрыв».",
            )  # fmt: skip
        include = None
        if self.include_cutoff is not None:
            include = number("include_cutoff", "Борт «в том числе»", positive=True)
            if not include > cutoff * (1.0 + INCLUDE_REL):
                raise _bad(
                    "include_cutoff", "Борт «в том числе» должен быть больше бортового содержания.",
                    "Задайте борт «в том числе» выше основного или оставьте его пустым.",
                )  # fmt: skip
        return replace(self, cutoff=cutoff, l_min=l_min, waste_max=waste_max, include_cutoff=include)


# --- candidates --------------------------------------------------------------------------------------------


def _column(values: Any, n: int, name: str) -> np.ndarray:
    """A column in line order: float64, C-contiguous."""
    out = np.ascontiguousarray(values, dtype=np.float64)
    if out.shape != (n,):
        raise ValueError(f"{name}: {out.shape} values for lines of {n} rows — the column must be in line order")
    return out


def _group(hole_ids: np.ndarray, frm: np.ndarray, to: np.ndarray) -> Lines:
    """Lines of intervals that come hole by hole (`hole_ids` does not descend)."""
    if len(hole_ids) == 0:
        return Lines(np.zeros(0, dtype=np.int64), np.zeros(1, dtype=np.int64), frm, to)
    cut = np.flatnonzero(hole_ids[1:] != hole_ids[:-1]) + 1
    holes = np.ascontiguousarray(hole_ids[np.concatenate(([0], cut))], dtype=np.int64)
    return Lines(holes, np.concatenate(([0], cut, [len(hole_ids)])).astype(np.int64), frm, to)


@dataclass(frozen=True, eq=False)
class Candidates:
    """Fragments «руда … руда» of steps 1–2, hole by hole, down the hole."""

    lines: Lines  # [FROM; TO] of every candidate — targets of `intervals.aggregate`, of TT, of the midpoints
    first: np.ndarray  # int64: the samples of the candidate are the rows first ≤ r < last of the source lines
    last: np.ndarray  # int64 (N_INT = last − first)
    length: np.ndarray  # float64: LENGTH — Σ l of its pieces, m
    grade: np.ndarray  # float64: the mean grade (by density when one is given)
    lg: np.ndarray  # float64: Σ l·g — метропроцент (г/т — метрограмм); a missing grade adds 0
    waste: np.ndarray  # float64: WASTE_LEN — the length of what is not ore inside, m (the missing included)
    miss: np.ndarray  # float64: MISS_LEN — the length without a grade counted as waste, m
    parent: np.ndarray  # int64: «в том числе» — the row of the candidates it lies in; −1 — a candidate of a hole

    @property
    def n(self) -> int:
        return int(self.first.shape[0])

    def take(self, keep: Any) -> Candidates:
        """The candidates of a boolean mask (the accepted ones), in the same order."""
        mask = np.asarray(keep, dtype=np.bool_)
        if mask.shape != (self.n,):
            raise ValueError(f"a mask of {mask.shape} for {self.n} candidates")
        cols = (self.first, self.last, self.length, self.grade, self.lg, self.waste, self.miss, self.parent)
        lines = _group(self.lines.hole_ids()[mask], self.lines.frm[mask], self.lines.to[mask])
        return Candidates(lines, *(np.ascontiguousarray(c[mask]) for c in cols))


def is_ore(grade: Any, cutoff: float) -> np.ndarray:
    """bool: g ≥ c as the rule compares it (within 1e-9, towards equality); a missing grade is not ore."""
    g = np.asarray(grade, dtype=np.float64)
    c = float(cutoff)
    with np.errstate(invalid="ignore"):
        return np.isfinite(g) & (g >= c - REL * np.maximum(np.abs(g), abs(c)))  # type: ignore[no-any-return]


def candidates(
    lines: Lines,
    grade: Any,
    cutoff: float,
    waste_max: float,
    *,
    density: Any = None,
    missing: str = "waste",
    within: Candidates | None = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> Candidates:
    """Steps 1–2 of the rule (module docstring) with the cut-off `cutoff` and L_п = `waste_max` — every
    fragment, whatever its length. `within` — candidates of the same lines: the steps run on the samples of
    each of them (`parent` is its row), nothing reaches outside it."""
    if missing not in MISSING:
        raise ValueError(f"missing is one of {MISSING}, got {missing!r}")
    g = _column(grade, lines.n, "grade")
    rho = _EMPTY if density is None else _column(density, lines.n, "density")
    frm, to = np.ascontiguousarray(lines.frm, dtype=np.float64), np.ascontiguousarray(lines.to, dtype=np.float64)
    if within is None:
        seg_first = np.ascontiguousarray(lines.starts[:-1], dtype=np.int64)
        seg_last = np.ascontiguousarray(lines.starts[1:], dtype=np.int64)
        seg_hole = lines.holes
    else:
        seg_first, seg_last, seg_hole = within.first, within.last, within.lines.hole_ids()
        if within.n and (int(seg_first.min()) < 0 or int(seg_last.max()) > lines.n):
            raise ValueError("`within` holds rows that the lines do not have")
    ns = len(seg_first)
    counts = np.zeros(ns, dtype=np.int64)
    none_i, none_f = np.zeros((0, 3), dtype=np.int64), np.zeros((0, 7), dtype=np.float64)
    args = (seg_first, seg_last, frm, to, g, rho, float(cutoff), float(waste_max), missing == "waste", counts)
    strips.run(
        lambda lo, hi: scan_strip(lo, hi, *args, counts, none_i, none_f, False),
        ns, threads=threads, tick=tick, span=(0.0, 0.4),
    )  # fmt: skip
    starts = strips.offsets(counts)
    n = int(starts[-1])
    out_i, out_f = np.empty((n, 3), dtype=np.int64), np.empty((n, 7), dtype=np.float64)
    strips.run(
        lambda lo, hi: scan_strip(lo, hi, *args, starts, out_i, out_f, True),
        ns, threads=threads, tick=tick, span=(0.4, 1.0),
    )  # fmt: skip
    seg = out_i[:, I_SEG]
    col = [np.ascontiguousarray(out_f[:, k]) for k in range(7)]
    return Candidates(
        _group(seg_hole[seg], col[F_FROM], col[F_TO]),
        np.ascontiguousarray(out_i[:, I_FIRST]),
        np.ascontiguousarray(out_i[:, I_LAST]),
        col[F_LEN], col[F_GRADE], col[F_LG], col[F_WASTE], col[F_MISS],
        np.ascontiguousarray(seg) if within is not None else np.full(n, -1, dtype=np.int64),
    )  # fmt: skip


# --- acceptance --------------------------------------------------------------------------------------------


def accept(cands: Candidates, l_min: float, *, min_by: str = "length", tt: Any = None) -> np.ndarray:
    """Step 3: int8 for every candidate — REASON_OK (it is an intercept), REASON_SHORT (LENGTH < L_min) or
    REASON_THIN (TT < m_min). min_by="tt": `tt` — the true thickness of every candidate, NaN — none; a
    candidate without TT (or all of them, when `tt` is None) is checked by its length with the same number."""
    if min_by not in MIN_BY:
        raise ValueError(f"min_by is one of {MIN_BY}, got {min_by!r}")
    limit = float(l_min)
    value = cands.length
    by_tt = np.zeros(cands.n, dtype=np.bool_)
    if min_by == "tt" and tt is not None:
        thick = _column(tt, cands.n, "tt")
        by_tt = np.isfinite(thick)
        value = np.where(by_tt, thick, cands.length)
    ok = value >= limit - REL * np.maximum(np.abs(value), abs(limit))
    return np.where(ok, REASON_OK, np.where(by_tt, REASON_THIN, REASON_SHORT)).astype(np.int8)  # type: ignore[no-any-return]


# --- the whole rule ----------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Intercepts:
    """The rows of the table of intercepts: hole by hole, a main intercept and then its «в том числе»."""

    lines: Lines  # [FROM; TO]; the rows of a hole may lie inside each other (targets of `intervals.aggregate`)
    kind: np.ndarray  # int8: KIND_MAIN, KIND_INCL (`KIND_TITLES`)
    id: np.ndarray  # int32: ID — the number of the row in its hole, from 1
    incl: np.ndarray  # int32: INCL — the ID of the main intercept of a «в том числе» row; 0 — a main row
    first: np.ndarray  # as `Candidates`
    last: np.ndarray
    length: np.ndarray
    grade: np.ndarray
    lg: np.ndarray
    waste: np.ndarray
    miss: np.ndarray
    tt: np.ndarray  # float64: the true thickness the acceptance was given; NaN — none (or min_by="length")


@dataclass(frozen=True, eq=False)
class Outcome:
    """What the rule made of a set: every candidate with the reason it was dropped for (the preview shows
    them), and `intercepts()` — the accepted ones as the rows of the table."""

    main: Candidates
    main_reason: np.ndarray  # int8: REASON_*
    main_tt: np.ndarray  # float64: TT the acceptance was given, NaN — none
    sub: Candidates | None  # «в том числе»: candidates inside the accepted; `parent` — the row of `main`
    sub_reason: np.ndarray
    sub_tt: np.ndarray

    def intercepts(self) -> Intercepts:
        ok = self.main_reason == REASON_OK
        parts, tts = [self.main.take(ok)], [self.main_tt[ok]]
        slot = np.cumsum(ok) - 1  # the row of `main` → its place among the accepted
        owner = [np.arange(parts[0].n, dtype=np.int64)]
        if self.sub is not None:
            ok2 = self.sub_reason == REASON_OK
            parts.append(self.sub.take(ok2))
            tts.append(self.sub_tt[ok2])
            owner.append(slot[parts[1].parent])
        kind = np.concatenate([np.full(p.n, k, dtype=np.int8) for k, p in enumerate(parts)])
        own = np.concatenate(owner)
        order = np.lexsort((np.arange(len(own)), kind, own))  # a main row, then its includes down the hole
        cols = {name: np.concatenate([getattr(p, name) for p in parts])[order] for name in _COLUMNS}
        hole = np.concatenate([p.lines.hole_ids() for p in parts])[order]
        frm = np.concatenate([p.lines.frm for p in parts])[order]
        to = np.concatenate([p.lines.to for p in parts])[order]
        lines = _group(hole, frm, to)
        kind, own = kind[order], own[order]
        row = np.arange(len(own), dtype=np.int64)
        ident = (row - np.repeat(lines.starts[:-1], np.diff(lines.starts)) + 1).astype(np.int32)
        place = np.zeros(parts[0].n, dtype=np.int64)
        place[own[kind == KIND_MAIN]] = row[kind == KIND_MAIN]
        incl = np.where(kind == KIND_INCL, ident[place[own]], 0).astype(np.int32)
        return Intercepts(lines=lines, kind=kind, id=ident, incl=incl, tt=np.concatenate(tts)[order], **cols)


_COLUMNS = ("first", "last", "length", "grade", "lg", "waste", "miss")


def _thickness(tt: TrueThickness | None, cands: Candidates) -> np.ndarray:
    if tt is None or cands.n == 0:
        return np.full(cands.n, np.nan, dtype=np.float64)
    return _column(tt(cands.lines), cands.n, "tt")


def run(
    lines: Lines,
    grade: Any,
    params: RuleParams,
    *,
    density: Any = None,
    tt: TrueThickness | None = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> Outcome:
    """Rule 8.5.3 on a set of holes: candidates, acceptance, «в том числе» (module docstring).

    `tt` — only for `min_by="tt"`: called with the `Lines` of the candidates (once for the holes, once for the
    «в том числе»), it returns their true thickness, NaN where there is no orientation; without it every
    candidate is checked by its length. `CompositeError` (`bad_param`) on wrong parameters."""
    p = params.checked()
    by_tt = tt if p.min_by == "tt" else None
    two = p.include_cutoff is not None

    def part(lo: float, hi: float) -> Tick | None:
        """`tick` for a part of the work: its fractions mapped into lo … hi."""
        if tick is None:
            return None
        report = tick
        return lambda f: report(lo + (hi - lo) * f)

    common: dict[str, Any] = {"density": density, "missing": p.missing, "threads": threads}
    main = candidates(lines, grade, p.cutoff, p.waste_max, tick=part(0.0, 0.5 if two else 1.0), **common)
    main_tt = _thickness(by_tt, main)
    main_reason = accept(main, p.l_min, min_by=p.min_by, tt=main_tt)
    if p.include_cutoff is None:
        return Outcome(main, main_reason, main_tt, None, np.zeros(0, dtype=np.int8), _EMPTY)
    ok = main_reason == REASON_OK
    inner = candidates(lines, grade, p.include_cutoff, p.waste_max, within=main.take(ok), tick=part(0.5, 1.0), **common)
    sub = replace(inner, parent=np.flatnonzero(ok)[inner.parent])
    sub_tt = _thickness(by_tt, sub)
    return Outcome(main, main_reason, main_tt, sub, accept(sub, p.l_min, min_by=p.min_by, tt=sub_tt), sub_tt)
