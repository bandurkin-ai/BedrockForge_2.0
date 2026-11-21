"""Merging of interval tables (ТЗ 8.2.2, ADR 0018 §6): all FROM / TO of a hole over all the tables, ascending;
a sub-interval takes the values of the covering interval of every table; no cover — NaN / empty.

    m = merge_tables([lines_a, lines_b, …], names=["Пробы", "Литология", …], threads=…, tick=…) -> Merged
        m.lines — the sub-intervals (holes, starts, frm, to): the rows of «Объединение: …»
        m.index[k] — int64 [n]: the row of table k, in its line order, that covers the sub-interval; −1 — none
        m.report, m.journal
    m.numbers(k, values) -> float64 [n] or [n, n_fields]    a column of table k (`lines.take(column)`) or
                                                            several (`intervals.stack_fields`); no cover — NaN
    m.codes(k, codes)    -> int32 [n]                       codes of a text column; no cover or empty — −1
    merged_names([("Пробы", ["AU", "CU"]), ("Литология", ["ROCK", "Au"])])
        -> [["AU (Пробы)", "CU"], ["ROCK", "Au (Литология)"]]

`tables` — `intervals.build_lines` of every table (rows with TO ≤ FROM and overlaps are left out there and
counted: the journal says so). The sub-intervals are those of `intervals.overlay_many`: a part of a hole no
table covers gives no row; boundaries of different tables nearer than `DEPTH_EPS` (1e−6 m) are one
boundary. A value is copied from the covering row as it is — a missing one stays missing, nothing becomes a
zero; a hole a table does not have is «no cover» all along.

Names of the columns: a field keeps its name; a name met in two tables (names differ not only by case — the
key is that of `db.dialect.name_key`) gets the name of its table, «AU (Пробы)».
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.db.dialect import name_key
from bf_core.jit import njit

from . import intervals as iv
from . import strips
from .intervals import DEPTH_EPS
from .strips import Tick

BLOCK_ROWS = 1 << 16  # rows of a block of `numbers`


@njit(cache=True, nogil=True)
def _gather(r0, r1, index, vals, out):  # type: ignore[no-untyped-def]
    """out[i] = vals[index[i]] for the rows r0 ≤ i < r1; NaN where the index is −1."""
    nf = vals.shape[1]
    for i in range(r0, r1):
        j = index[i]
        if j < 0:
            for f in range(nf):
                out[i, f] = np.nan
        else:
            for f in range(nf):
                out[i, f] = vals[j, f]


@dataclass(frozen=True)
class Merged:
    lines: iv.Lines  # the sub-intervals, ordered by (hole, FROM)
    index: list[np.ndarray]  # per table: int64 [n] — its covering row in line order, −1 — none
    sizes: tuple[int, ...]  # rows of every table (its line order)
    report: dict[str, Any]  # numbers for `dh_results.report`
    journal: list[str]  # lines for the job log

    def numbers(self, k: int, values: Any, *, threads: int = 1) -> np.ndarray:
        """Numeric values of table k for the sub-intervals: `values` — float64 [rows of the table] or
        [rows, n_fields] in its line order; the result has the same number of dimensions. No cover — NaN."""
        vals = np.ascontiguousarray(values, dtype=np.float64)
        flat = vals.ndim == 1
        if flat:
            vals = vals.reshape(-1, 1)
        if vals.ndim != 2 or vals.shape[0] != self.sizes[k]:
            raise ValueError(f"values are not in the line order of table {k}")
        index = self.index[k]
        out = np.empty((index.shape[0], vals.shape[1]), dtype=np.float64)
        strips.run(lambda lo, hi: _gather(lo, hi, index, vals, out), index.shape[0], threads=threads, size=BLOCK_ROWS)
        return out.reshape(-1) if flat else out

    def codes(self, k: int, codes: Any) -> np.ndarray:
        """Codes of a text column of table k for the sub-intervals; no cover or an empty value — −1."""
        code = np.ascontiguousarray(codes, dtype=np.int32)
        if code.shape[0] != self.sizes[k]:
            raise ValueError(f"codes are not in the line order of table {k}")
        index = self.index[k]
        out = np.full(index.shape[0], -1, dtype=np.int32)
        have = index >= 0
        out[have] = code[index[have]]
        return out


def merge_tables(
    tables: Sequence[iv.Lines],
    *,
    names: Sequence[str] | None = None,
    eps: float = DEPTH_EPS,
    threads: int = 1,
    tick: Tick | None = None,
) -> Merged:
    """The sub-intervals between all the boundaries of the tables and the covering row of every table (module
    docstring). `names` — the names of the tables for the journal. The result does not depend on `threads`."""
    if not tables:
        raise ValueError("no tables")
    if names is not None and len(names) != len(tables):
        raise ValueError("names are not per table")
    titles = [f"№ {k + 1}" for k in range(len(tables))] if names is None else list(names)
    if len(tables) == 1:  # nothing to merge with: the table itself
        lines, index = tables[0], [np.arange(tables[0].n, dtype=np.int64)]
        if tick is not None:
            tick(1.0)
    else:
        lines, index = iv.overlay_many(tables, "union", eps=eps, threads=threads, tick=tick)
    length = lines.lengths()
    per, journal = [], []
    for k, table in enumerate(tables):
        have = index[k] >= 0
        covered = int(np.count_nonzero(have))
        per.append(
            {
                "rows": table.n,
                "left_out": table.n_bad + table.n_overlap,
                "covered": covered,
                "covered_len": float(length[have].sum()),
            }
        )
        journal.extend(table.notes(titles[k]))
        if covered < lines.n:
            gap = f"{float(length[~have].sum()):.2f}".replace(".", ",")
            journal.append(
                f"Таблица «{titles[k]}»: подынтервалов без покрытия — {lines.n - covered} (всего {gap} м): "
                "её поля там пусты (не ноль)."
            )
    report = {"rows": lines.n, "length": float(length.sum()), "holes": lines.n_holes, "tables": per}
    return Merged(lines, index, tuple(t.n for t in tables), report, journal)


def merged_names(tables: Sequence[tuple[str, Sequence[str]]]) -> list[list[str]]:
    """The names of the columns of the merged table, per table in the order given: (table, its fields) →
    names. A name met in more than one table gets the name of its table — «AU (Пробы)»; a name so made
    that is taken already gets a number — «AU (Пробы) (2)». Names are compared by `name_key` (case does not
    make a name)."""
    met: dict[str, int] = {}
    for _table, columns in tables:
        for key in {name_key(c) for c in columns}:
            met[key] = met.get(key, 0) + 1
    taken = {name_key(c) for _table, columns in tables for c in columns if met[name_key(c)] == 1}
    out = []
    for table, columns in tables:
        row = []
        for c in columns:
            name = c
            if met[name_key(c)] > 1:
                name = f"{c} ({table})"
                n = 2
                while name_key(name) in taken:
                    name = f"{c} ({table}) ({n})"
                    n += 1
                taken.add(name_key(name))
            row.append(name)
        out.append(row)
    return out
