"""Data of up to 30 holes for the window «Данные по скважинам» (ТЗ 7.17, ADR 0007 §6):
`POST /viz/sets/{id}/log` and the list of hole names `GET /viz/sets/{id}/hole-names`.

Rows are read from the database by hole through the index (table, hole, «от»), not from a column snapshot: 30
holes of a 5 million-row table need neither the snapshot nor its build. Per hole and table: rows with «от»
ascending by «от» (rows without it cannot be drawn), optionally those that meet the depth window [from; to];
at most 20 000 rows, more — `truncated`. Empty values are null, never 0; events have `to` null.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import dh_holes, dh_rows, dh_tables

MAX_HOLES = 30
MAX_TABLES = 16
MAX_FIELDS = 64
MAX_ROWS = 20_000


def too_many_holes(n: int) -> ApiError:
    return ApiError(
        422,
        "too_many_holes",
        f"Выбрано скважин: {n}, а окно показывает не больше {MAX_HOLES}.",
        f"Снимите отметку с лишних скважин: не больше {MAX_HOLES}.",
    )


def hole_names(conn: sa.Connection, set_id: int) -> list[str]:
    """Names of the holes of the set in the order of the set (as they were added)."""
    return list(
        conn.execute(sa.select(dh_holes.c.name).where(dh_holes.c.set_id == set_id).order_by(dh_holes.c.id)).scalars()
    )


@dataclass(frozen=True)
class Column:
    name: str
    kind: str  # number | text
    unit: str | None
    slot: int


@dataclass(frozen=True)
class Table:
    key: str  # as the request named it
    id: int
    name: str
    kind: str  # assay | interval | event
    columns: list[Column]

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "columns": [{"name": c.name, "kind": c.kind, "unit": c.unit} for c in self.columns],
        }


def resolve(conn: sa.Connection, set_id: int, key: str, fields: list[str] | None) -> Table:
    """A ready table by its id or `name_key`; its value columns by name (all of them when `fields` is None)."""
    from bf_core.drillholes.snapshot import table_columns

    from .keys import fields_of, table_by_key

    ref = None
    if key.strip().isdigit():
        r = conn.execute(
            sa.select(dh_tables.c.name).where(
                dh_tables.c.id == int(key), dh_tables.c.set_id == set_id, dh_tables.c.state == "ready"
            )
        ).scalar()
        if r is not None:
            ref = table_by_key(conn, set_id, r)
    if ref is None:
        ref = table_by_key(conn, set_id, key)
    cols = {c.key: c for c in table_columns(conn, ref.id, ref.kind) if c.role != "fixed" and c.slot is not None}
    if fields is None:
        chosen = list(cols.values())
    else:
        chosen = [cols[f.key] for f in fields_of(conn, ref, list(dict.fromkeys(fields))) if f.key in cols]
    columns = [Column(c.name, c.kind, c.unit, int(c.slot)) for c in chosen if c.slot is not None]
    return Table(key, ref.id, ref.name, ref.kind, columns)


def _number(v: float) -> float | None:
    return None if math.isnan(v) else v


def _rows(conn: sa.Connection, t: Table, hole_id: int, lo: float | None, hi: float | None) -> dict[str, Any]:
    from bf_core.drillholes.snapshot import _decode_vectors

    q = sa.select(dh_rows.c.from_m, dh_rows.c.to_m, dh_rows.c.num, dh_rows.c.txt).where(
        dh_rows.c.table_id == t.id, dh_rows.c.hole_id == hole_id, dh_rows.c.from_m.is_not(None)
    )
    if lo is not None:
        q = q.where(sa.func.coalesce(dh_rows.c.to_m, dh_rows.c.from_m) >= lo)
    if hi is not None:
        q = q.where(dh_rows.c.from_m <= hi)
    got = conn.execute(q.order_by(dh_rows.c.from_m, dh_rows.c.id).limit(MAX_ROWS + 1)).all()
    truncated = len(got) > MAX_ROWS
    got = got[:MAX_ROWS]
    numbers = [c for c in t.columns if c.kind == "number"]
    width = max((c.slot for c in numbers), default=-1) + 1
    matrix = _decode_vectors([r.num for r in got], width) if width else None
    values: dict[str, list[Any]] = {}
    for c in t.columns:
        if c.kind == "number" and matrix is not None:
            values[c.name] = [_number(v) for v in matrix[:, c.slot].tolist()]
        else:
            key = str(c.slot)
            texts = [(r.txt or {}).get(key) for r in got]
            values[c.name] = [None if v is None or v == "" else str(v) for v in texts]
    return {
        "from": [float(r.from_m) for r in got],
        "to": [None if t.kind == "event" or r.to_m is None else float(r.to_m) for r in got],
        "values": values,
        "truncated": truncated,
    }


def log(
    conn: sa.Connection, set_id: int, holes: list[str], tables: list[Table], lo: float | None, hi: float | None
) -> dict[str, Any]:
    names = list(dict.fromkeys(holes))
    found = {
        r.name: r
        for r in conn.execute(
            sa.select(dh_holes.c.id, dh_holes.c.name, dh_holes.c.z, dh_holes.c.td, dh_holes.c.has_collar).where(
                dh_holes.c.set_id == set_id, dh_holes.c.name.in_(names)
            )
        )
    }
    out = []
    for name in names:
        h = found.get(name)
        item: dict[str, Any] = {"name": name, "found": h is not None, "collar_z": None, "td": None, "tables": {}}
        if h is not None:
            if h.has_collar:
                item["collar_z"] = None if h.z is None else float(h.z)
                item["td"] = None if h.td is None else float(h.td)
            item["tables"] = {t.key: _rows(conn, t, int(h.id), lo, hi) for t in tables}
        out.append(item)
    return {"holes": out, "tables": [t.as_dict() for t in tables]}


def check(holes: list[str], tables: int, lo: float | None, hi: float | None) -> None:
    if len(dict.fromkeys(holes)) > MAX_HOLES:
        raise too_many_holes(len(dict.fromkeys(holes)))
    if tables > MAX_TABLES:
        raise ApiError(
            422, "too_many_tables", f"Таблиц больше {MAX_TABLES}.", f"Выберите не больше {MAX_TABLES} таблиц."
        )
    for v in (lo, hi):
        if v is not None and not math.isfinite(v):
            raise ApiError(422, "bad_range", "Глубина интервала — не число.", "Введите глубины «от» и «до» числами.")
    if lo is not None and hi is not None and lo > hi:
        raise ApiError(422, "bad_range", "Глубина «от» больше глубины «до».", "Поменяйте глубины местами.")
