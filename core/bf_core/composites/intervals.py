"""The interval engine of sample processing (ADR 0018 §4–5): one set of kernels for composites, merging of
tables, coding by wireframes, top cuts and ore intervals (Д-26).

Representation — `Lines`: interval rows ordered by (hole, FROM) with the starts of the holes.

    lines = build_lines(hole_ids, frm, to, ids=None, eps=DEPTH_EPS)
        holes   int64[nh]      ids of the holes that have rows, ascending
        starts  int64[nh + 1]  rows starts[i] : starts[i + 1] belong to holes[i]
        frm, to float64[n]     inside a hole frm[i] ≥ to[i − 1] and to > frm
        rows    int64[n]|None  the source row of every line row; None — the source rows as they are
        n_source, n_bad, n_overlap — rows given; left out: without depths or with TO ≤ FROM; overlapping
    Rows with TO ≤ FROM and rows that overlap the previous kept one do not take part and are counted, never
    silently: `lines.notes(«Пробы»)` is the text for the job log (совет — аудит). A column of the table goes
    into line order with `lines.take(column)`; numeric fields — `stack_fields(lines, columns)` → float64
    [n, n_fields] (a row of the array is a sample: what the kernels read and what `dh_rows.num` stores).
    Text — int32 codes (`pandas.factorize`), empty — −1. Holes are ordered by id whatever the order of the
    input, so shuffling the holes or the rows of the input does not change a result.

Kernels (each takes `threads` and `tick`; strips of 4096 holes — `strips.py`; bit for bit the same with any
number of threads):

    overlay(a, b, how="union"|"left"|"inner") -> Overlay(lines, ia, ib)
        sub-intervals between all the boundaries of a and b; ia, ib — the covering row of a and of b, −1 —
        none. `overlay_many([l0, l1, …])` folds N tables: (lines, [index of every table])
    runs(lines, codes, gap=DEPTH_EPS) -> Runs(lines, code, first, last)
        stretches of consecutive rows with one code and no gap wider than `gap`; rows first[k] : last[k].
        By a threshold: codes = (value ≥ T) as int32, NaN → −1 (a row of code −1 makes a run of its own kind)
    targets(stretches, length, rule="remainder"|"equal", f_min=0.5, attach=True, origin=None)
        -> Targets(lines, stretch, n_short, len_short, n_cut, len_cut) — the composites of every stretch [a; b]
    aggregate(lines, values, targets, weight=None, density=None, p_min=0.0, fill=nan)
        -> Aggregate(values, density, sampled, count, low): Σ l·w·g / Σ l·w per field, ρ, L_оп, N.
        NaN takes no part and never becomes 0; sums — pairwise in the order of the samples (ТЗ 8.0.1)
    mode_by_length(lines, codes, targets) -> int32[n]: the code with the greatest length, a tie — the first

Targets of `aggregate` and `mode_by_length` are any `Lines` (composites, ore intervals, benches): grouped by
hole, in any order inside it, they may overlap each other; a hole the source does not have gives NaN / −1.
Tolerances: `DEPTH_EPS` (1e−6 m) — float noise of depths; comparisons of lengths — 1e−9 relative, towards
equality (L_оп = p_min·L is enough, r = f_min·L is a composite of its own).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import intervals_kernels as ik
from . import strips
from .strips import Tick

DEPTH_EPS = 1e-6  # m: depths closer than this are the same depth (float noise of imported tables)
_HOW = {"union": 0, "left": 1, "inner": 2}
_EMPTY = np.zeros(0, dtype=np.float64)
_BLOCK_ROWS = 16384  # rows of a block of `stack_fields`


class CompositeError(ValueError):
    """An operation cannot run: {code, message, hint} in Russian, shown to the user (API — 422)."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


def _arr(x: Any, dtype: Any) -> np.ndarray:
    """A C-contiguous writable array of the dtype (one numba specialization of every kernel)."""
    a = np.ascontiguousarray(x, dtype=dtype)
    return a if a.flags.writeable else a.copy()


@dataclass(frozen=True)
class Lines:
    """Interval rows ordered by (hole, FROM) — see the module docstring."""

    holes: np.ndarray
    starts: np.ndarray
    frm: np.ndarray
    to: np.ndarray
    rows: np.ndarray | None = None
    n_source: int = 0
    n_bad: int = 0
    n_overlap: int = 0

    @property
    def n(self) -> int:
        return int(self.frm.shape[0])

    @property
    def n_holes(self) -> int:
        return int(self.holes.shape[0])

    def take(self, column: Any) -> np.ndarray:
        """A column of the source table in line order (the column itself when no row was dropped or moved)."""
        col = np.asarray(column)
        if self.rows is None:
            if col.shape[0] != self.n:
                raise ValueError(f"column of {col.shape[0]} rows for lines of {self.n}")
            return col
        return col[self.rows]  # type: ignore[no-any-return]

    def hole_ids(self) -> np.ndarray:
        """The hole id of every row."""
        return np.repeat(self.holes, np.diff(self.starts))  # type: ignore[no-any-return]

    def lengths(self) -> np.ndarray:
        return self.to - self.frm  # type: ignore[no-any-return]

    def notes(self, table: str) -> list[str]:
        """Lines for the job log about the rows left out (empty when none)."""
        out = []
        if self.n_bad:
            out.append(f"Таблица «{table}»: строк без глубины или с «до» ≤ «от» — {self.n_bad}, в расчёт не вошли.")
        if self.n_overlap:
            out.append(
                f"Таблица «{table}»: строк, перекрывающих предыдущий интервал, — {self.n_overlap}, в расчёт не вошли."
            )
        if out:
            out.append("Проверьте таблицу: «Данные → Аудит».")
        return out


def _lines(holes: np.ndarray, counts: np.ndarray, frm: np.ndarray, to: np.ndarray) -> tuple[Lines, np.ndarray]:
    """Derived lines of per-hole counts (holes without rows are left out) and the mask of the holes kept."""
    keep = counts > 0
    return Lines(holes[keep], strips.offsets(counts[keep]), frm, to), keep


def build_lines(hole_ids: Any, frm: Any, to: Any, *, ids: Any = None, eps: float = DEPTH_EPS) -> Lines:
    """Lines of the rows of an interval table (any order). `ids` — row ids that settle the order of rows with
    equal (hole, FROM, TO); without them — the order given. A row without FROM or TO, with TO ≤ FROM or
    overlapping the previous kept row of its hole by more than `eps` is left out and counted; an overlap
    within `eps` is float noise — FROM is moved to the previous TO."""
    hole = _arr(hole_ids, np.int64)
    f = _arr(frm, np.float64)
    t = _arr(to, np.float64)
    n = hole.shape[0]
    if f.shape[0] != n or t.shape[0] != n:
        raise ValueError("hole_ids, frm and to differ in length")
    rid = _arr(ids, np.int64) if ids is not None else np.arange(n, dtype=np.int64)
    order = None
    if not ik.rows_sorted(hole, f, t, rid):
        order = np.lexsort((rid, t, f, hole))
        hole, f, t = hole[order], f[order], t[order]
    keep = np.empty(n, dtype=np.bool_)
    bad, over = ik.scan_rows(hole, f, t, float(eps), keep)
    rows = None
    if bad or over:
        idx = np.flatnonzero(keep)
        hole, f, t = hole[idx], f[idx], t[idx]
        rows = idx if order is None else order[idx]
    elif order is not None:
        rows = order
    else:
        f = f.copy()  # the lines must not share FROM with the caller: `snap_rows` writes it
    cut = np.flatnonzero(hole[1:] != hole[:-1]) + 1
    holes = hole[np.concatenate(([0], cut))] if len(hole) else np.zeros(0, dtype=np.int64)
    starts = np.concatenate(([0], cut, [len(hole)])).astype(np.int64) if len(hole) else np.zeros(1, dtype=np.int64)
    ik.snap_rows(starts, f, t)
    return Lines(holes, starts, f, t, rows, n, int(bad), int(over))


def stack_fields(lines: Lines, columns: Sequence[Any]) -> np.ndarray:
    """Numeric columns of the source table as float64 [n, n_fields] in line order (one allocation; filled by
    blocks of rows — the columns are interleaved inside the cache)."""
    cols = [np.asarray(c) for c in columns]
    rows = lines.rows
    if rows is None and any(c.shape[0] != lines.n for c in cols):
        raise ValueError(f"columns do not have the {lines.n} rows of the lines")
    out = np.empty((lines.n, len(cols)), dtype=np.float64)
    for lo in range(0, lines.n, _BLOCK_ROWS):
        hi = min(lo + _BLOCK_ROWS, lines.n)
        pick = None if rows is None else rows[lo:hi]
        for k, c in enumerate(cols):
            out[lo:hi, k] = c[lo:hi] if pick is None else c[pick]
    return out


def _match(holes: np.ndarray, into: np.ndarray) -> np.ndarray:
    """For every hole id — its index in the ascending ids `into`, −1 — none."""
    if into.shape[0] == 0:
        return np.full(holes.shape[0], -1, dtype=np.int64)
    pos = np.searchsorted(into, holes)
    pos[pos == into.shape[0]] = 0
    return np.where(into[pos] == holes, pos, -1).astype(np.int64)


# --- overlay, runs, targets --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Overlay:
    lines: Lines  # the sub-intervals
    ia: np.ndarray  # int64: the row of `a` that covers the sub-interval, −1 — none
    ib: np.ndarray  # int64: the same of `b`


def overlay(
    a: Lines, b: Lines, how: str = "union", *, eps: float = DEPTH_EPS, threads: int = 1, tick: Tick | None = None
) -> Overlay:
    """Sub-intervals between all the boundaries of two sets of intervals, hole by hole (ТЗ 8.2.2
    «объединение таблиц»): "union" — those covered by a or b, "left" — by a, "inner" — by both. Boundaries
    closer than `eps` are one boundary (a sliver is not a sub-interval)."""
    holes = np.union1d(a.holes, b.holes)
    ha, hb = _match(holes, a.holes), _match(holes, b.holes)
    nh = holes.shape[0]
    counts = np.zeros(nh, dtype=np.int64)
    none_f, none_i = _EMPTY, np.zeros(0, dtype=np.int64)
    args = (ha, hb, a.starts, a.frm, a.to, b.starts, b.frm, b.to, _HOW[how], float(eps), counts)
    strips.run(
        lambda lo, hi: ik.overlay_strip(lo, hi, *args, none_i, none_f, none_f, none_i, none_i, False),
        nh, threads=threads, tick=tick, span=(0.0, 0.4),
    )  # fmt: skip
    starts = strips.offsets(counts)
    n = int(starts[-1])
    frm, to = np.empty(n, dtype=np.float64), np.empty(n, dtype=np.float64)
    ia, ib = np.empty(n, dtype=np.int64), np.empty(n, dtype=np.int64)
    strips.run(
        lambda lo, hi: ik.overlay_strip(lo, hi, *args, starts, frm, to, ia, ib, True),
        nh, threads=threads, tick=tick, span=(0.4, 1.0),
    )  # fmt: skip
    return Overlay(_lines(holes, counts, frm, to)[0], ia, ib)


def overlay_many(
    tables: Sequence[Lines], how: str = "union", *, eps: float = DEPTH_EPS, threads: int = 1, tick: Tick | None = None
) -> tuple[Lines, list[np.ndarray]]:
    """`overlay` of N tables by folding: the sub-intervals and, for every table, its covering row (−1 — none)."""
    if not tables:
        raise ValueError("no tables")
    cur = tables[0]
    index = [np.arange(cur.n, dtype=np.int64)]
    for k, nxt in enumerate(tables[1:]):
        span = (k / (len(tables) - 1), (k + 1) / (len(tables) - 1))
        part = None if tick is None else (lambda f, s=span, t=tick: t(s[0] + (s[1] - s[0]) * f))
        ov = overlay(cur, nxt, how, eps=eps, threads=threads, tick=part)
        index = [np.where(ov.ia >= 0, ix[ov.ia], -1) for ix in index]
        index.append(ov.ib)
        cur = ov.lines
    return cur, index


@dataclass(frozen=True)
class Runs:
    lines: Lines  # [a; b] of every run: from the FROM of its first row to the TO of its last
    code: np.ndarray  # int32
    first: np.ndarray  # int64: rows first[k] ≤ r < last[k] of the source lines make run k
    last: np.ndarray


def runs(lines: Lines, codes: Any, *, gap: float = DEPTH_EPS, threads: int = 1, tick: Tick | None = None) -> Runs:
    """Continuous stretches by code: consecutive rows of a hole with the same code and no gap wider than `gap`
    between them (`math.inf` — gaps do not break a run). Code −1 (empty) makes runs too — a stretch without a
    domain; the caller decides what they mean."""
    code = _arr(codes, np.int32)
    if code.shape[0] != lines.n:
        raise ValueError("codes are not in line order")
    nh = lines.n_holes
    counts = np.zeros(nh, dtype=np.int64)
    none_f, none_i, none_c = _EMPTY, np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int32)
    args = (lines.starts, lines.frm, lines.to, code, float(gap), counts)
    strips.run(
        lambda lo, hi: ik.runs_strip(lo, hi, *args, none_i, none_f, none_f, none_c, none_i, none_i, False),
        nh, threads=threads, tick=tick, span=(0.0, 0.4),
    )  # fmt: skip
    starts = strips.offsets(counts)
    n = int(starts[-1])
    frm, to = np.empty(n, dtype=np.float64), np.empty(n, dtype=np.float64)
    out_code = np.empty(n, dtype=np.int32)
    first, last = np.empty(n, dtype=np.int64), np.empty(n, dtype=np.int64)
    strips.run(
        lambda lo, hi: ik.runs_strip(lo, hi, *args, starts, frm, to, out_code, first, last, True),
        nh, threads=threads, tick=tick, span=(0.4, 1.0),
    )  # fmt: skip
    return Runs(_lines(lines.holes, counts, frm, to)[0], out_code, first, last)


@dataclass(frozen=True)
class Targets:
    lines: Lines  # the composites [c0; c1]
    stretch: np.ndarray  # int64: the row of the stretches every composite lies in
    n_short: int  # stretches left without a composite (n0 = 0 and the remainder is below f_min·L) — to the log
    len_short: float  # … their total length, m
    n_cut: int  # remainders dropped by the rule «отбросить»
    len_cut: float  # … their total length, m


def targets(
    stretches: Lines,
    length: float,
    *,
    rule: str = "remainder",
    f_min: float = 0.5,
    attach: bool = True,
    origin: Any = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> Targets:
    """Boundaries of the composites of every stretch [a; b], D = b − a (ТЗ 8.2.2).

    "equal": n = max(1, floor(D/L + 0,5)) composites of D/n.
    "remainder": n0 = floor(D/L + 1e−9) composites of L and the remainder r = D − n0·L; r ≥ f_min·L — a
    composite of its own; less — joined to the last one (`attach`) or dropped; n0 = 0 and r < f_min·L — the
    stretch gets no composite and is counted (`n_short`).
    `origin` (per stretch; None or NaN — its start a) — the depth the grid of L is counted from («от устья»:
    0; «от кровли первой пробы»). Then the stretch is cut by the boundaries origin + k·L; the piece above the
    first of them follows the rule of the remainder too: ≥ f_min·L — its own composite, less — joined to the
    next one or dropped. "equal" does not depend on the origin."""
    if rule not in ("remainder", "equal"):
        raise ValueError(rule)
    if not (length > 0.0 and math.isfinite(length)):
        raise ValueError("length must be positive")
    org = _EMPTY if origin is None else _arr(origin, np.float64)
    if origin is not None and org.shape[0] != stretches.n:
        raise ValueError("origin is not per stretch")
    nh = stretches.n_holes
    counts = np.zeros(nh, dtype=np.int64)
    none_f, none_i = _EMPTY, np.zeros(0, dtype=np.int64)
    rules = (float(length), rule == "equal", float(f_min), bool(attach))
    args = (stretches.starts, stretches.frm, stretches.to, org, *rules, counts)

    def count(lo: int, hi: int) -> np.ndarray:
        info = np.zeros(4, dtype=np.float64)
        ik.targets_strip(lo, hi, *args, none_i, none_f, none_f, none_i, False, info)
        return info

    infos = strips.run(count, nh, threads=threads, tick=tick, span=(0.0, 0.4))
    starts = strips.offsets(counts)
    n = int(starts[-1])
    frm, to = np.empty(n, dtype=np.float64), np.empty(n, dtype=np.float64)
    stretch = np.empty(n, dtype=np.int64)
    info = np.zeros(4, dtype=np.float64)
    strips.run(
        lambda lo, hi: ik.targets_strip(lo, hi, *args, starts, frm, to, stretch, True, info),
        nh, threads=threads, tick=tick, span=(0.4, 1.0),
    )  # fmt: skip
    return Targets(
        _lines(stretches.holes, counts, frm, to)[0],
        stretch,
        int(sum(int(i[0]) for i in infos)),
        strips.total(float(i[1]) for i in infos),
        int(sum(int(i[2]) for i in infos)),
        strips.total(float(i[3]) for i in infos),
    )


# --- values ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Aggregate:
    values: np.ndarray  # float64 [n_targets, n_fields]: g_c, NaN — no value
    density: np.ndarray  # float64 [n_targets]: ρ_c = Σ l·ρ / Σ l, NaN — no density
    sampled: np.ndarray  # float64 [n_targets]: L_оп — length of the samples with a value in any of the fields
    count: np.ndarray  # int32 [n_targets]: N — number of those samples
    low: np.ndarray  # bool [n_targets]: «мало проб» — a field with L_оп < p_min·length


def aggregate(
    lines: Lines,
    values: Any,
    targets: Lines,
    *,
    weight: Any = None,
    density: Any = None,
    p_min: float = 0.0,
    fill: float = math.nan,
    threads: int = 1,
    tick: Tick | None = None,
) -> Aggregate:
    """Values of the targets [c0; c1] over the samples of `lines` (ТЗ 8.2.2). `values` — float64 [n, n_fields]
    in line order (`stack_fields`); `weight`, `density` — per sample, in line order, or None.

    For a field: l_i — the overlap of sample i that has a value (and a weight > 0, when weights are given);
    g_c = Σ l_i·w_i·g_i / Σ l_i·w_i (w = 1 without weights); L_f = Σ l_i. No such sample — NaN, never 0.
    L_f < p_min·(c1 − c0) → `low` and g_c = NaN. `fill` (not NaN: 0 or a number) — the part of the target
    without a value takes it: g_c = (g_c·L_f + fill·(c1 − c0 − L_f)) / (c1 − c0), and p_min only marks `low`.
    ρ_c = Σ l_i·ρ_i / Σ l_i over the samples with a density. Sums — in the order of the samples, pairwise
    (`intervals_kernels`: blocks of 8, cascade)."""
    vals = _arr(values, np.float64)
    if vals.ndim != 2 or vals.shape[0] != lines.n:
        raise ValueError("values must be [rows of the lines, fields]")
    w = _EMPTY if weight is None else _arr(weight, np.float64)
    rho = _EMPTY if density is None else _arr(density, np.float64)
    if (weight is not None and w.shape[0] != lines.n) or (density is not None and rho.shape[0] != lines.n):
        raise ValueError("weight and density must be in line order")
    n = targets.n
    out = np.empty((n, vals.shape[1]), dtype=np.float64)
    out_rho, out_len = np.empty(n, dtype=np.float64), np.empty(n, dtype=np.float64)
    out_n, out_low = np.empty(n, dtype=np.int32), np.empty(n, dtype=np.bool_)
    src = _match(targets.holes, lines.holes)
    spans = (targets.starts, src, targets.frm, targets.to, lines.starts, lines.frm, lines.to)
    args = (*spans, vals, w, rho, float(p_min), float(fill))
    strips.run(
        lambda lo, hi: ik.aggregate_strip(lo, hi, *args, out, out_rho, out_len, out_n, out_low),
        targets.n_holes, threads=threads, tick=tick,
    )  # fmt: skip
    return Aggregate(out, out_rho, out_len, out_n, out_low)


def mode_by_length(
    lines: Lines, codes: Any, targets: Lines, *, threads: int = 1, tick: Tick | None = None
) -> np.ndarray:
    """The code of a text field for every target: the one with the greatest total length inside it; equal
    lengths — the one met first down the hole; empty (−1) takes no part; nothing — −1. int32 [n_targets]."""
    code = _arr(codes, np.int32)
    if code.shape[0] != lines.n:
        raise ValueError("codes are not in line order")
    out = np.empty(targets.n, dtype=np.int32)
    src = _match(targets.holes, lines.holes)
    args = (targets.starts, src, targets.frm, targets.to, lines.starts, lines.frm, lines.to, code, out)
    strips.run(lambda lo, hi: ik.mode_strip(lo, hi, *args), targets.n_holes, threads=threads, tick=tick)
    return out
