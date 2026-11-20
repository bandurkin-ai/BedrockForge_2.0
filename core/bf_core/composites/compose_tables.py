"""Merging of tables and coordinates of samples — the request and the computation for the jobs `composite.merge`,
`composite.coords` and their previews (ТЗ 8.2.1–8.2.2; ADR 0018 §3, §6; kernels `merge.py`, `coords.py`).

    spec = parse_tables(conn, body, op)              # "merge": {set_id, tables, name?, replace?};
                                                     # "coords": {set_id, table, name?, replace?}
    made = merge_compute(spec, srcs, threads=…, tick=…)      # «Объединение: …»
    made = coords_compute(spec, src, st, threads=…, tick=…)  # «‹Таблица› — координаты»

Every field of a table is taken (not the fixed and source columns): numbers with their units and roles, text as
text. Merging: the holes of all the tables in one index by name, `merge.merge_tables` over their lines, a name
met in two tables gets the name of its table (`merge.merged_names`); no cover — empty, not zero. Coordinates:
a copy of every row of the table (in line order) with XB…ZE of `coords.line_coords`; a hole without a
trajectory — empty and a line of the journal. `report.summary {before, after}` — the summary «до/после» of the
numeric fields of the first table (the metal of a merge is the metal of the table).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_columns

if TYPE_CHECKING:
    from . import source
    from .compose import Made

NAME_MAX = 200


@dataclass
class TablesSpec:
    set_id: int
    op: str  # merge | coords
    tables: list[tuple[int, str, list[str]]]  # (id, name, fields)
    name: str
    replace: bool
    body: dict[str, Any]  # the request as given, without `replace`
    rows: int = 0

    def hash_params(self) -> dict[str, Any]:
        return {"op": self.op, "tables": [[dialect.name_key(n), f] for _i, n, f in self.tables]}


def all_fields(conn: sa.Connection, table_id: int) -> list[str]:
    return [
        r.name
        for r in conn.execute(
            sa.select(dh_columns.c.name, dh_columns.c.role)
            .where(dh_columns.c.table_id == table_id, dh_columns.c.deleted.is_(False))
            .order_by(dh_columns.c.position, dh_columns.c.id)
        )
        if r.role not in ("source", "fixed")
    ]


def _list(raw: Any) -> list[Any]:
    if isinstance(raw, str):
        return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]
    return list(raw) if isinstance(raw, list) else []


def parse_tables(conn: sa.Connection, body: dict[str, Any], op: str) -> TablesSpec:
    from .jobs import _bad, resolve_table

    set_id = body.get("set_id")
    if not isinstance(set_id, int) or isinstance(set_id, bool):
        raise ApiError(404, "set_not_found", "Не задан набор скважин.", "Выберите набор скважин.")
    given = _list(body.get("tables")) if op == "merge" else [body.get("table")]
    rows = [resolve_table(conn, set_id, g) for g in given]
    if len({int(r.id) for r in rows}) != len(rows):
        raise _bad("bad_param", "Таблица выбрана дважды.", "Отметьте разные таблицы.", "tables")
    if op == "merge" and len(rows) < 2:
        raise _bad(
            "bad_param", "Объединение: нужно не меньше двух таблиц.", "Отметьте две таблицы или больше.", "tables"
        )
    tables = [(int(r.id), str(r.name), all_fields(conn, int(r.id))) for r in rows]
    name = body.get("name")
    if isinstance(name, str) and name.strip():
        name = name.strip()
    elif op == "merge":
        name = "Объединение: " + ", ".join(t[1] for t in tables)
    else:
        name = f"{tables[0][1]} — координаты"
    if len(name) > NAME_MAX:
        raise _bad("bad_param", "Имя таблицы слишком длинное.", f"Не больше {NAME_MAX} знаков.", "name")
    if any(dialect.name_key(name) == dialect.name_key(t[1]) for t in tables):
        raise _bad("name_is_source", "Имя результата совпадает с таблицей-источником.", "Задайте другое имя.", "name")
    return TablesSpec(set_id, op, tables, name, body.get("replace") is True,
                      {k: v for k, v in body.items() if k != "replace"},
                      sum(int(r.row_count or 0) for r in rows))  # fmt: skip


def _column(src: source.Lines, name: str, label: str, values: np.ndarray) -> tuple[Any, np.ndarray]:
    """A column of the result with the unit and the role of the source field; text codes → labels."""
    from bf_core.drillholes.store import ColumnSpec

    f = src.fields[name]
    if f.kind == "number":
        role = f.role if f.role in ("component", "value") else "value"
        return ColumnSpec(label, "number", f.unit, role), np.ascontiguousarray(values, dtype=float)
    labels = np.array([*src.labels[name], None], dtype=object)
    return ColumnSpec(label, "text"), labels[values]  # −1 → None


def _summary(lengths: np.ndarray, columns: Sequence[tuple[str, np.ndarray]]) -> dict[str, Any]:
    """`length.summary` of the fields given as columns, one at a time: the sums of a field do not depend on
    the other fields, so the numbers are those of the matrix of all of them — which is not built."""
    from .length import summary

    out = summary(lengths, np.zeros((len(lengths), 0)), [])
    for name, col in columns:
        one = np.ascontiguousarray(col, dtype=np.float64).reshape(-1, 1)
        out["fields"].update(summary(lengths, one, [name])["fields"])
    return out


def _numbers(src: source.Lines) -> list[str]:
    return [n for n in src.fields if src.fields[n].kind == "number"]


def _holes(names: Sequence[str], index: np.ndarray) -> Sequence[str]:
    """The hole name of every row as an object array (`sink.OutTable.holes`): no list of millions of names."""
    arr = np.array(list(names), dtype=object)[index] if len(index) else np.zeros(0, dtype=object)
    return cast("Sequence[str]", arr)


def merge_compute(
    spec: TablesSpec, srcs: list[source.Lines], *, threads: int = 1, tick: Callable[[float], None] | None = None,
    release: bool = False,
) -> Made:  # fmt: skip
    """`release` (the job: nothing reads `srcs` after it) — a column of a source is dropped once it is laid on
    the merged rows."""
    from . import intervals as iv
    from . import sink
    from .compose import Made
    from .merge import merge_tables, merged_names

    holes = sorted({h for s in srcs for h in s.holes})
    rank = {h: i for i, h in enumerate(holes)}
    lines = []
    for s in srcs:
        r = np.array([rank[h] for h in s.holes], dtype=np.int64)
        lines.append(iv.build_lines(r[s.hole_of()] if len(s.holes) else np.zeros(0, np.int64), s.from_m, s.to_m))
    first, l0 = srcs[0], lines[0]
    before = _summary(l0.lengths(), [(n, l0.take(first.numbers[n])) for n in _numbers(first)])
    m = merge_tables(lines, names=[t[1] for t in spec.tables], threads=threads, tick=tick)
    labels = merged_names([(t[1], list(s.fields)) for t, s in zip(spec.tables, srcs, strict=True)])
    cols, vals = [], []
    merged0: list[tuple[str, np.ndarray]] = []  # the number columns of the first table on the merged rows
    for k, (s, names) in enumerate(zip(srcs, labels, strict=True)):
        for field, label in zip(list(s.fields), names, strict=True):
            number = s.fields[field].kind == "number"
            raw = s.numbers[field] if number else s.codes[field]
            got = m.numbers(k, lines[k].take(raw), threads=threads) if number else m.codes(k, lines[k].take(raw))
            del raw
            if release:
                (s.numbers if number else s.codes).pop(field)
            spec_k, v = _column(s, field, label, got)
            if k == 0 and number:
                merged0.append((field, v))
            cols.append(spec_k)
            vals.append(v)
    ml = m.lines
    out = sink.OutTable(spec.name, _holes(holes, ml.hole_ids()), ml.frm, ml.to, cols, vals)
    report = {**m.report, "summary": {"before": before, "after": _summary(ml.lengths(), merged0)},
              "edits": [], "journal": m.journal}  # fmt: skip
    return Made(out, report, list(m.journal))


def coords_compute(
    spec: TablesSpec, src: source.Lines, st: Any, *, threads: int = 1, tick: Callable[[float], None] | None = None
) -> Made:
    from bf_core.drillholes.store import ColumnSpec

    from . import intervals as iv
    from . import sink
    from .compose import Made
    from .coords import hole_index, line_coords

    lines = iv.Lines(np.arange(len(src.holes), dtype=np.int64), src.starts, src.from_m, src.to_m)  # every row
    dense = hole_index(st.hole_ids, src.hole_db_ids)
    c = line_coords(lines, st.traj, dense, threads=threads, tick=tick)
    cols, vals = [], []
    for field in src.fields:
        raw = src.numbers[field] if src.fields[field].kind == "number" else src.codes[field]
        spec_k, v = _column(src, field, field, raw)
        cols.append(spec_k)
        vals.append(v)
    taken = {x.name.casefold() for x in cols}
    for name, v in c.columns().items():
        cols.append(ColumnSpec(name if name.casefold() not in taken else f"{name}_TRAJ", "number", "м"))
        vals.append(v)
    out = sink.OutTable(spec.name, _holes(src.holes, src.hole_of()), src.from_m, src.to_m, cols, vals)
    journal = list(c.journal)
    if src.skipped:
        journal.insert(
            0, f"Таблица «{spec.tables[0][1]}»: строк без «от» или «до» — {src.skipped}, они не скопированы."
        )
    same = _summary(lines.lengths(), [(n, src.numbers[n]) for n in _numbers(src)])
    report = {
        "rows": lines.n,
        "no_trajectory": int(c.no_trajectory.shape[0]),
        "rows_without": c.rows_without,
        "skipped": src.skipped,
        "summary": {"before": same, "after": same},
        "edits": [],
        "journal": journal,
    }
    return Made(out, report, journal)
