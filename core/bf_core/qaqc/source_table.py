"""A table of a drillhole set as the source of QAQC data (ADR 0020 §2, вопрос № 37): a snapshot, not a live link.

    found = resolve(conn, set_id, table)                 # the table by name or id → TableRef (set and table names, rev)
    columns = table_columns(conn, found)                 # [(name, kind)] — the fields, then the pseudo-columns
    cols, ref = read_columns(project, set_id, table, names, tick)   # {name: formats.table.Column}, rows in line order

The rows are the «lines» of `composites/source.read`: ordered by (hole, FROM, TO, row id), the same order every
time — «row of the file» of a snapshot. Pseudo-columns: «HOLE» — the name of the hole of the row, «FROM», «TO» —
the depths (a field of the table with such a name wins). The snapshot keeps `rev` of the table: when it grows, the
data are «устарели» and «Обновить» imports them again with the same mapping (`service.source_state`)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects

HOLE, FROM, TO = "HOLE", "FROM", "TO"
PSEUDO = (HOLE, FROM, TO)
SAMPLE_ROWS = 50


@dataclass(frozen=True)
class TableRef:
    set_id: int
    table_id: int
    set_name: str
    table_name: str
    rev: int
    rows: int

    def source(self) -> dict[str, Any]:
        return {"kind": "table", "set_id": self.set_id, "table_id": self.table_id, "set_name": self.set_name,
                "table_name": self.table_name, "rev": self.rev}  # fmt: skip


def resolve(conn: sa.Connection, set_id: Any, table: Any) -> TableRef:
    """The table of the set by its name or id; 404 `set_not_found`, `table_not_found`."""
    from bf_core.stats import source as ss

    if isinstance(set_id, bool) or not isinstance(set_id, int):
        raise ApiError(422, "bad_param", "Не выбран набор скважин.", "Выберите набор и таблицу проб.",
                       extra={"param": "set_id"})  # fmt: skip
    s = conn.execute(sa.select(objects.c.name).where(objects.c.id == set_id, objects.c.kind == "drillhole_set")).first()
    if s is None:
        raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")
    t = ss.find_table(conn, set_id, table)
    row = conn.execute(sa.select(dh_tables.c.rev, dh_tables.c.row_count).where(dh_tables.c.id == t.id)).one()
    return TableRef(set_id, int(t.id), str(s.name), str(t.name), int(row.rev), int(row.row_count))


def _fields(conn: sa.Connection, ref: TableRef) -> list[Any]:
    from bf_core.stats import source as ss

    t = ss.find_table(conn, ref.set_id, ref.table_id)
    return [f for f in ss.fields_of(conn, t) if f.role != ss.LENGTH]


def table_columns(conn: sa.Connection, ref: TableRef) -> list[tuple[str, str]]:
    """[(name, 'number' | 'text')]: the fields of the table, then HOLE, FROM, TO unless a field has that name."""
    out = [(f.name, "number" if f.kind == "number" else "text") for f in _fields(conn, ref)]
    have = {n.casefold() for n, _k in out}
    kinds = {HOLE: "text", FROM: "number", TO: "number"}
    return out + [(p, kinds[p]) for p in PSEUDO if p.casefold() not in have]


def read_columns(
    project: Any,
    set_id: int,
    table: Any,
    names: Sequence[str],
    tick: Callable[[], None] | None = None,
    holes: Sequence[int] | None = None,
) -> tuple[dict[str, Any], TableRef]:
    """The chosen columns of the table as reader columns (`formats.table.Column`), rows in line order."""
    import numpy as np
    import pyarrow as pa

    from bf_core.composites import source as cs
    from bf_core.formats.table import Column

    with dialect.read_tx(project.engine) as conn:
        ref = resolve(conn, set_id, table)
        own = {f.name.casefold(): f.name for f in _fields(conn, ref)}
    fields = [own[n.casefold()] for n in dict.fromkeys(names) if n.casefold() in own]
    lines = cs.read(project.engine, ref.set_id, ref.table_id, fields, cache_dir=project.folder / "cache" / "tables",
                    holes=holes, tick=(lambda _p, _m: tick()) if tick else None)  # fmt: skip
    out: dict[str, Any] = {}
    for name in dict.fromkeys(names):
        real = own.get(name.casefold())
        if real is not None and real in lines.numbers:
            out[name] = Column(name, "number", np.ascontiguousarray(lines.numbers[real], dtype=np.float64))
        elif real is not None and real in lines.codes:
            codes, labels = lines.codes[real], lines.labels[real]
            arr = pa.DictionaryArray.from_arrays(pa.array(codes, type=pa.int32(), mask=codes < 0),
                                                 pa.array(labels, type=pa.string()))  # fmt: skip
            out[name] = Column(name, "text", pa.chunked_array([arr.cast(pa.string())]))
        elif name == HOLE:
            holes_txt = np.asarray(lines.holes, dtype=object)[lines.hole_of()] if lines.rows else []
            out[name] = Column(name, "text", pa.chunked_array([pa.array(list(holes_txt), type=pa.string())]))
        elif name in (FROM, TO):
            out[name] = Column(name, "number", np.asarray(lines.from_m if name == FROM else lines.to_m, np.float64))
        else:
            raise ApiError(422, "field_not_found", f"В таблице нет поля «{name}».",
                           "Выберите поле из списка колонок таблицы.", extra={"param": name})  # fmt: skip
    return out, ref


def preview(project: Any, ref: TableRef) -> dict[str, Any]:
    """Columns with samples and the first lines of the first holes (`POST /data/inspect` of a table)."""
    import sqlalchemy as sa

    from bf_core.db.schema import dh_holes

    with dialect.read_tx(project.engine) as conn:
        columns = table_columns(conn, ref)
        holes = [int(h) for h in conn.execute(
            sa.select(dh_holes.c.id).where(dh_holes.c.set_id == ref.set_id).order_by(dh_holes.c.name).limit(20)
        ).scalars()]  # fmt: skip
    cols, _ = read_columns(project, ref.set_id, ref.table_id, [n for n, _k in columns], holes=holes)
    texts = {n: c.text() for n, c in cols.items()}
    n = len(next(iter(texts.values()))) if texts else 0
    rows = [[texts[name][i] or "" for name, _k in columns] for i in range(min(SAMPLE_ROWS, n))]
    return {"columns": columns, "rows": rows, "total": ref.rows}
