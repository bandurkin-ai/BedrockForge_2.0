"""The table «Рудные пересечения» (ТЗ 8.5.3; ADR 0021 §8, решения 10, 14; docs/progress/e15-api.md): the rows of
the rule 8.5.3 with their true thickness, the means of the other fields, метропроцент, the label and the summary.

    t = run(lines, grade, params, name="AU", unit="г/т", fields={"CU": cu}, units={"CU": "%"}, density=rho,
            orienting=Orienting.of(spec, src, st, tables=…, mesh=…), threads=…, tick=…) -> InterceptTable
        the whole calculation: the rule (`rule.run`; with `min_by="tt"` the candidates are accepted by the true
        thickness of `orienting`), TT and the middles of the accepted rows, the table. No `orienting` — TT is
        empty, TT_SRC «нет», TT_FLAG 2, X, Y, Z are empty, and `min_by="tt"` checks the length
    t = build(lines, rows, name=…, …, tt=None, xyz=None) -> InterceptTable
        the table of rows `rule.Intercepts` that are there already; `tt` — `tt.TtTable` of these rows
        t.hole, t.frm, t.to   the hole of every row (as the `lines` of the samples name it), FROM, TO
        t.columns             [Column(name, kind, unit, role, values)] after HOLE, FROM, TO, in the order below;
                              numbers — float64 (NaN — empty, never 0), text — an object array of str
        t.names(), t.column(name), t.summary, t.journal; t.rows — `rule.Intercepts`; t.outcome — `rule.Outcome`
        of `run`: every candidate with the reason it was dropped for (the preview of a hole)
    label(length, grade, tt, flag, kind, unit), labels(…) -> LABEL of a row, of many rows
    column_names(grade, fields) -> the names of the table, HOLE first

`lines` — the samples or composites as the rule takes them (`composites.intervals.build_lines`); `grade`,
`density` and every column of `fields` — in line order (`lines.take(column)`). `Orienting` holds what the true
thickness of intervals of these holes needs: the parsed parameters (`orientation`, `eps`, `step` —
`spec.RunSpec`), `orient.Inputs`, and for the hole k of the lines its key `dh_holes.id` and its place in the
trajectories. `Orienting.of(spec, src, st)` makes it for lines built as the jobs of Э12 build them,
`build_lines(src.hole_of(), src.from_m, src.to_m)`.

Columns (the order of the contract): HOLE, FROM, TO, LENGTH, TT, TT_FLAG, TT_SRC, ALPHA, DIPDIR, HOLE_THETA,
HOLE_AZ, the grade of the calculation under the name of its field, the other fields, LEN_G, TT_G, WASTE_LEN,
N_INT, X, Y, Z, INCL, LABEL, ID, KIND, MISS_LEN.
    LENGTH      Σ l of the pieces of the row (the rule), m
    TT … HOLE_AZ  `tt.compute` on the rows themselves (their own [FROM; TO], one plane a row): the value the
                acceptance by m_min was given — checked, a different one is an error, never written
    grade       the mean of the rule: Σ l·g / Σ l, with a density Σ l·ρ·g / Σ l·ρ; a missing grade counted as
                waste is g = 0 there (ТЗ 8.5.3)
    fields      the other numeric fields: the mean over the samples of the row that have a value, by length or
                by length × density (`intervals.aggregate`: a sample without a density takes the mean one of
                the row); no value in the row — empty. A field named as the grade is left out; one named as a
                column of the table gets «_SRC», «_SRC_2»… (`composites.compose.own_names`)
    LEN_G       LENGTH·g — метропроцент (г/т — метрограмм): without a density the direct sum Σ l·g of the rule,
                with one LENGTH × the grade of the row (g is then the mean by density)
    TT_G        TT·g; no TT — empty
    WASTE_LEN, MISS_LEN, N_INT   what is not ore inside, m (the missing included); the missing counted as
                waste, m; the samples of the row
    X, Y, Z     the point of the path at (FROM + TO) / 2; no trajectory — empty
    ID, INCL, KIND   the number of the row in its hole from 1; the ID of the main row of a «в том числе» row,
                0 — a main row; «основное» / «в том числе»
    LABEL       «12,0 м @ 2,10 г/т (ИМ 9,8 м)»; «в том числе» — «в т. ч. » before it; no TT — no brackets;
                bit 1 of TT_FLAG — «(ИМ 0,3 м, вдоль тела)»; the unit — of the field; the numbers rounded as the
                labels of Э12 (`ore_set`: half up on the shortest notation; LENGTH and TT — 0,1, g — 0,01)

Summary (`dh_results.report`; the sums are of the main rows — the «в том числе» ones lie inside them — and
exactly rounded, `math.fsum`): rows, intercepts, included, length, tt (None — no main row has a TT), len_g,
mean = len_g / length (None — no intercepts), waste_len, miss_len, excluded (rows of the samples that took no
part: no depth, TO ≤ FROM, overlaps), src and flags (`tt.counts` over all the rows).

Every column is made strip by strip by kernels whose strips do not depend on the threads: the table and the
summary are the same bit for bit with 1, 2, 4 and 8 threads (ТЗ 8.0.2). `tick(fraction)` may raise to cancel.

numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, NamedTuple

import numpy as np

from bf_core.composites import coords
from bf_core.composites import intervals as iv
from bf_core.composites.ore_set import _PLAIN, _half_up, _ru
from bf_core.composites.strips import Tick

from . import orient, rule
from . import tt as ttm
from .formulas import FLAG_NO_ORIENTATION, FLAG_PARALLEL
from .rule import KIND_INCL, KIND_MAIN, KIND_TITLES, Intercepts, Outcome, RuleParams

__all__ = ["ALGO", "FIXED", "HEAD", "OWN", "TAIL", "Column", "InterceptTable", "Orienting", "build", "column_names",
           "label", "labels", "run"]  # fmt: skip

ALGO = "icpt-1"  # the version of the rules of the table and of `rule` — `dh_results.algo`
FIXED = ("HOLE", "FROM", "TO")
HEAD = ("LENGTH", "TT", "TT_FLAG", "TT_SRC", "ALPHA", "DIPDIR", "HOLE_THETA", "HOLE_AZ")  # before the grades
TAIL = ("LEN_G", "TT_G", "WASTE_LEN", "N_INT", "X", "Y", "Z", "INCL", "LABEL", "ID", "KIND", "MISS_LEN")
OWN = (*FIXED, *HEAD, *TAIL)  # the names a field of the samples may not take
INCLUDED = "в т. ч. "  # LABEL of a «в том числе» row begins with it
ALONG = ", вдоль тела"  # … of a row whose hole runs almost along the body (bit 1 of TT_FLAG)


class Column(NamedTuple):
    """A column of the table after HOLE, FROM, TO (`drillholes.store.ColumnSpec` and its values)."""

    name: str
    kind: str  # "number" | "text"
    unit: str | None
    role: str  # "value" | "component"
    values: np.ndarray  # float64 [n], NaN — empty; text — object [n] of str


# --- the true thickness of intervals of the holes ------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Orienting:
    """What the true thickness and the middles of intervals of the holes of the samples are taken with. The
    hole k of the lines (`Lines.holes` are the numbers 0 … nh − 1, as `build_lines(src.hole_of(), …)` has them)
    has the key `keys[k]` and the trajectory `dense[k]`."""

    spec: Any  # `orientation` (0–3 sources of `spec.Source`), `eps`, `step` — `spec.RunSpec`
    inputs: orient.Inputs  # the trajectories of the set and what the sources read
    keys: np.ndarray  # int64 [nh]: dh_holes.id
    dense: np.ndarray  # int64 [nh]: the hole of the trajectories; −1 — none

    def __post_init__(self) -> None:
        keys = np.ascontiguousarray(self.keys, dtype=np.int64)
        dense = np.ascontiguousarray(self.dense, dtype=np.int64)
        if keys.ndim != 1 or dense.shape != keys.shape:
            raise ValueError("orienting: keys and dense must be one per hole of the lines")
        object.__setattr__(self, "keys", keys)
        object.__setattr__(self, "dense", dense)

    @classmethod
    def of(cls, spec: Any, src: Any, st: Any, *, tables: Any = None, mesh: orient.MeshSource | None = None,
           bodies: Any = None) -> Orienting:  # fmt: skip
        """For the samples of a snapshot `src` (`composites.source.Lines`) and the trajectories `st` of the set
        (`drillholes.trajectory.SetTrajectories`); `tables`, `mesh`, `bodies` — `orient.Inputs`, as
        `tt.of_table` takes them."""
        keys = np.asarray(src.hole_db_ids, dtype=np.int64)
        dense = coords.hole_index(st.hole_ids, keys)
        dense[np.isin(dense, np.asarray(st.traj.skipped, dtype=np.int64))] = -1
        return cls(spec, orient.Inputs(st.traj, src, tables or {}, mesh, bodies), keys, dense)

    def _holes(self, lines: iv.Lines) -> np.ndarray:
        h = np.asarray(lines.holes, dtype=np.int64)
        if h.size and (int(h.min()) < 0 or int(h.max()) >= self.keys.shape[0]):
            raise ValueError(f"orienting: the lines name a hole outside the {self.keys.shape[0]} holes known")
        return h

    def rows(self, lines: iv.Lines) -> orient.Intervals:
        """Intervals of the holes (candidates, intercepts) as the sources of the orientation take them."""
        h = self._holes(lines)
        return orient.Intervals.grouped(self.keys[h], self.dense[h], lines.starts, lines.frm, lines.to)

    def thickness(self, lines: iv.Lines, *, threads: int = 1, tick: Tick | None = None) -> ttm.TtTable:
        """TT, TT_FLAG, TT_SRC and the angles of the intervals — each by its own [FROM; TO]."""
        return ttm.compute(self.spec, self.rows(lines), self.inputs, threads=threads, tick=tick)

    def middles(self, lines: iv.Lines, *, threads: int = 1, tick: Tick | None = None) -> np.ndarray:
        """float64 [n, 3]: the point of the path at (FROM + TO) / 2; NaN — no trajectory."""
        return coords.midpoints(lines, self.inputs.traj, self.dense[self._holes(lines)], threads=threads, tick=tick)


# --- labels ------------------------------------------------------------------------------------------------


def label(length: float, grade: float, tt: float = math.nan, flag: int = 0, kind: int = KIND_MAIN,
          unit: str | None = "") -> str:  # fmt: skip
    """LABEL of a row (module docstring): «12,0 м @ 2,10 г/т (ИМ 9,8 м)»."""
    head = INCLUDED if int(kind) == KIND_INCL else ""
    text = f"{head}{_ru(float(length), 1)} м @ {_ru(float(grade), 2)} {(unit or '').strip()}".rstrip()
    if math.isnan(tt):
        return text
    return f"{text} (ИМ {_ru(float(tt), 1)} м{ALONG if int(flag) & FLAG_PARALLEL else ''})"


def labels(length: Any, grade: Any, tt: Any, flag: Any, kind: Any, unit: str | None = "") -> list[str]:
    """`label` of many rows — the same strings, without a Decimal per number (`ore_set._half_up`); the numbers
    it does not take (negative, 1e9 and more, not finite) go to `label`."""
    ln, g, m = (np.asarray(x, dtype=np.float64) for x in (length, grade, tt))
    bits, incl = np.asarray(flag, dtype=np.uint8), np.asarray(kind, dtype=np.int8) == KIND_INCL
    has = ~np.isnan(m)

    def fits(x: np.ndarray) -> np.ndarray:
        return np.isfinite(x) & ~np.signbit(x) & (x < _PLAIN)  # type: ignore[no-any-return]

    plain = fits(ln) & fits(g) & (~has | fits(m))
    (l0, l1), (g0, g1) = _half_up(np.where(plain, ln, 0.0), 1), _half_up(np.where(plain, g, 0.0), 2)
    m0, m1 = _half_up(np.where(plain & has, m, 0.0), 1)
    tail = f" {unit.strip()}".rstrip() if unit else ""
    heads, ends = ("", INCLUDED), (" м)", f" м{ALONG})")
    out = [
        f"{heads[k]}{a},{b} м @ {c},{d:02d}{tail}" + (f" (ИМ {e},{f}{ends[p]}" if h else "")
        for k, a, b, c, d, h, e, f, p in zip(
            incl.tolist(), l0.tolist(), l1.tolist(), g0.tolist(), g1.tolist(), has.tolist(), m0.tolist(), m1.tolist(),
            ((bits & FLAG_PARALLEL) > 0).tolist(), strict=True,
        )
    ]  # fmt: skip
    for r in np.flatnonzero(~plain).tolist():
        out[r] = label(float(ln[r]), float(g[r]), float(m[r]), int(bits[r]), KIND_INCL if incl[r] else KIND_MAIN, unit)
    return out


# --- the table ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class InterceptTable:
    """The table «Рудные пересечения» — see the module docstring."""

    rows: Intercepts
    columns: list[Column]
    summary: dict[str, Any]
    journal: list[str]  # lines for the job log
    outcome: Outcome | None = None  # of `run`: every candidate and why it was dropped

    @property
    def n(self) -> int:
        return self.rows.lines.n

    @property
    def hole(self) -> np.ndarray:
        """int64 [n]: the hole of every row, as the lines of the samples name it."""
        return self.rows.lines.hole_ids()

    @property
    def frm(self) -> np.ndarray:
        return self.rows.lines.frm

    @property
    def to(self) -> np.ndarray:
        return self.rows.lines.to

    def names(self) -> list[str]:
        """The names of the table in its order: HOLE, FROM, TO, LENGTH, …, MISS_LEN."""
        return [*FIXED, *(c.name for c in self.columns)]

    def column(self, name: str) -> np.ndarray:
        for c in self.columns:
            if c.name == name:
                return c.values
        raise KeyError(name)


def _named(grade: str, fields: Iterable[str]) -> dict[str, str]:
    """The column of the grade and of every other field (a field named as the grade is left out)."""
    from bf_core.composites.compose import own_names

    others = [f for f in dict.fromkeys(fields) if f.casefold() != grade.casefold()]
    return own_names([grade, *others], OWN)


def column_names(grade: str, fields: Iterable[str] = ()) -> list[str]:
    """The names of the table of a grade field and other fields, in its order — `InterceptTable.names()`."""
    return [*FIXED, *HEAD, *_named(grade, fields).values(), *TAIL]


def _per_sample(values: Any, n: int, name: str) -> np.ndarray:
    """A column of the samples in line order: float64, C-contiguous and writable — a read-only one (a cached
    column) is copied, so that the kernels keep their one specialization (numba compiles anew for read-only)."""
    out = np.require(values, dtype=np.float64, requirements=["C", "W"])
    if out.shape != (n,):
        raise ValueError(f"{name}: {out.shape} values for lines of {n} rows — the column must be in line order")
    return out


def _summary(lines: iv.Lines, rows: Intercepts, tt: np.ndarray, len_g: np.ndarray, src: np.ndarray,
             flag: np.ndarray) -> dict[str, Any]:  # fmt: skip
    main = rows.kind == KIND_MAIN
    thick = main & ~np.isnan(tt)

    def total(x: np.ndarray, mask: np.ndarray = main) -> float:
        return math.fsum(x[mask].tolist())

    length, lg = total(rows.length), total(len_g)
    return {
        "rows": rows.lines.n,
        "intercepts": int(np.count_nonzero(main)),
        "included": int(np.count_nonzero(~main)),
        "length": length,
        "tt": total(tt, thick) if thick.any() else None,
        "len_g": lg,
        "mean": lg / length if length > 0.0 else None,
        "waste_len": total(rows.waste),
        "miss_len": total(rows.miss),
        "excluded": int(lines.n_bad) + int(lines.n_overlap),
        **ttm.counts(src, flag),
    }


def build(
    lines: iv.Lines,
    rows: Intercepts,
    *,
    name: str = "GRADE",
    unit: str | None = None,
    fields: Mapping[str, Any] | None = None,
    units: Mapping[str, str | None] | None = None,
    roles: Mapping[str, str] | None = None,
    density: Any = None,
    tt: ttm.TtTable | None = None,
    xyz: Any = None,
    outcome: Outcome | None = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> InterceptTable:
    """The table of the rows `rows` (`Outcome.intercepts()`) over the samples `lines` (module docstring).

    `name`, `unit` — of the grade field; `fields` — the other numeric fields, name → column in line order, with
    their `units` and `roles` (`dh_columns.role`; the grade is a "component" unless given); `density` — the
    column the rule was given, or None; `tt` — `tt.TtTable` of the rows (None — no orientation: TT empty, «нет»,
    bit 2); `xyz` — [n, 3] their middles (None — empty)."""
    n = rows.lines.n
    units, roles = dict(units or {}), dict(roles or {})
    unit = (unit or "").strip() or None
    named = _named(name, fields or {})
    others = [f for f in named if f != name]
    grade = np.ascontiguousarray(rows.grade, dtype=np.float64)
    if tt is None:
        thick, bits, src = np.full(n, np.nan), np.full(n, FLAG_NO_ORIENTATION, dtype=np.uint8), np.zeros(n, np.uint8)
        angles = [np.full(n, np.nan) for _ in range(4)]
    else:
        thick, bits, src = tt.tt, tt.flag, tt.src
        angles = [tt.alpha, tt.dipdir, tt.theta, tt.azimuth]
        if any(np.shape(a) != (n,) for a in (thick, bits, src, *angles)):
            raise ValueError(f"tt: the true thickness is not of the {n} rows of the table")
    if xyz is None:
        middle = np.full((n, 3), np.nan)
    else:
        middle = np.asarray(xyz, dtype=np.float64)
        if middle.shape != (n, 3):
            raise ValueError(f"xyz: {middle.shape} for the {n} rows of the table")
    rho = None if density is None else _per_sample(density, lines.n, "density")
    means = np.empty((n, len(others)), dtype=np.float64)
    if others:
        values = np.empty((lines.n, len(others)), dtype=np.float64)
        for k, f in enumerate(others):
            values[:, k] = _per_sample((fields or {})[f], lines.n, f)
        share = None if tick is None else (lambda f: tick(0.9 * f))
        means = iv.aggregate(lines, values, rows.lines, weight=rho, threads=threads, tick=share).values
        del values
    # LENGTH·g: the direct sum of the rule where the mean is by length, else by the grade of the row
    len_g = np.ascontiguousarray(rows.lg, dtype=np.float64) if rho is None else rows.length * grade
    text = labels(rows.length, grade, thick, bits, rows.kind, unit)
    per_m = f"м·{unit}" if unit else None

    def number(title: str, values: Any, of: str | None = None, role: str = "value") -> Column:
        return Column(title, "number", of, role, np.ascontiguousarray(values, dtype=np.float64))

    def words(title: str, values: Any) -> Column:
        return Column(title, "text", None, "value", np.asarray(values, dtype=object))

    columns = [
        number("LENGTH", rows.length, "м"),
        number("TT", thick, "м"),
        number("TT_FLAG", bits),
        words("TT_SRC", np.array(orient.SRC_LABELS, dtype=object)[src]),
        *(number(title, a, "°") for title, a in zip(HEAD[4:], angles, strict=True)),
        number(named[name], grade, unit, roles.get(name, "component")),
        *(number(named[f], means[:, k], units.get(f), roles.get(f, "value")) for k, f in enumerate(others)),
        number("LEN_G", len_g, per_m),
        number("TT_G", thick * grade, per_m),
        number("WASTE_LEN", rows.waste, "м"),
        number("N_INT", rows.last - rows.first),
        *(number(title, middle[:, k], "м") for k, title in enumerate("XYZ")),
        number("INCL", rows.incl),
        words("LABEL", np.array(text, dtype=object)),
        number("ID", rows.id),
        words("KIND", np.array(KIND_TITLES, dtype=object)[rows.kind]),
        number("MISS_LEN", rows.miss, "м"),
    ]
    summary = _summary(lines, rows, thick, len_g, src, bits)
    if tick is not None:
        tick(1.0)
    return InterceptTable(rows, columns, summary, list(tt.journal) if tt is not None else [], outcome)


def _ru_number(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _thickness(orienting: Orienting, threads: int, tick: Tick | None, cands: iv.Lines) -> np.ndarray:
    """TT of candidates of the rule (`rule.TrueThickness`); NaN — none."""
    return orienting.thickness(cands, threads=threads, tick=tick).tt


def run(
    lines: iv.Lines,
    grade: Any,
    params: RuleParams,
    *,
    name: str = "GRADE",
    unit: str | None = None,
    fields: Mapping[str, Any] | None = None,
    units: Mapping[str, str | None] | None = None,
    roles: Mapping[str, str] | None = None,
    density: Any = None,
    orienting: Orienting | None = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> InterceptTable:
    """«Рудные пересечения» of a set of holes: the rule 8.5.3, the true thickness, the table (module docstring).
    `CompositeError` (`bad_param`, `param`) on wrong parameters of the rule."""
    p = params.checked()
    grade = _per_sample(grade, lines.n, "grade")
    density = None if density is None else _per_sample(density, lines.n, "density")
    done = 0.0

    def report(f: float) -> None:
        nonlocal done
        done = f
        if tick is not None:
            tick(f)

    def share(lo: float, hi: float) -> Tick | None:
        return None if tick is None else (lambda f: report(lo + (hi - lo) * f))

    by_tt: rule.TrueThickness | None = None
    if orienting is not None and p.min_by == "tt":
        # the candidates are many times fewer than the samples: their TT only keeps the cancel alive
        beat = None if tick is None else (lambda _f: report(done))
        by_tt = partial(_thickness, orienting, threads, beat)
    out = rule.run(lines, grade, p, density=density, tt=by_tt, threads=threads, tick=share(0.0, 0.4))
    rows = out.intercepts()
    thick, xyz = None, None
    if orienting is not None:
        thick = orienting.thickness(rows.lines, threads=threads, tick=share(0.4, 0.65))
        if p.min_by == "tt" and not np.array_equal(thick.tt, rows.tt, equal_nan=True):
            raise ValueError("intercepts: the true thickness of a row is not the one its acceptance was given")
        xyz = orienting.middles(rows.lines, threads=threads, tick=share(0.65, 0.7))
    made = build(lines, rows, name=name, unit=unit, fields=fields, units=units, roles=roles, density=density,
                 tt=thick, xyz=xyz, outcome=out, threads=threads, tick=share(0.7, 1.0))  # fmt: skip
    journal = list(made.journal)
    if p.min_by == "tt":
        blind = int(np.count_nonzero(np.isnan(rows.tt)))
        if orienting is None:
            journal.append(f"Ориентировка не задана: истинной мощности нет, пересечения приняты по длине "
                           f"(не короче {_ru_number(p.l_min)} м).")  # fmt: skip
        elif blind:
            journal.append(f"Пересечений без истинной мощности — {blind}: приняты по длине "
                           f"(не короче {_ru_number(p.l_min)} м).")  # fmt: skip
    return replace(made, journal=journal)
