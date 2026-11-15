"""The sample table of a drillhole set that audit and statistics work on.

A set may hold several assay tables: the built-in «Пробы» (rows in `assay`, components in
`component`) and named tables imported from other files (rows in `interval_row`, numbers in
`values`). Audit and statistics take one of them, by key: "assay" or the table id as text.
Without a key: the built-in assays when the set has them, else its first table with numbers.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Assay, Component, IntervalTable

BUILTIN = "assay"


@dataclass(frozen=True)
class Source:
    set_id: int
    # "assay" or the id of an interval table, as text
    key: str
    name: str
    table_id: int | None
    # numeric fields -> unit
    units: dict[str, str]

    @property
    def builtin(self) -> bool:
        return self.table_id is None


def source_tables(session: Session, set_id: int) -> list[dict]:
    """The sample tables a set offers: built-in assays (when it has rows) and tables with numbers."""
    out = []
    n = session.scalar(select(func.count()).select_from(Assay).where(Assay.set_id == set_id)) or 0
    if n:
        out.append({"key": BUILTIN, "name": "Пробы", "row_count": n})
    for t in session.scalars(select(IntervalTable).where(IntervalTable.set_id == set_id).order_by(IntervalTable.id)):
        if t.kind != "event" and any(c.get("kind") == "number" for c in t.columns or []):
            out.append({"key": str(t.id), "name": t.name, "row_count": t.row_count})
    return out


def default_table(session: Session, set_id: int) -> str | None:
    tables = source_tables(session, set_id)
    return tables[0]["key"] if tables else None


def resolve_source(session: Session, set_id: int, key: str | None) -> Source:
    """The sample table by key; none given: the set's default. 404 for a table of another set."""
    key = (key or "").strip() or default_table(session, set_id) or BUILTIN
    if key == BUILTIN:
        units = {c.name: c.unit for c in session.scalars(select(Component).where(Component.set_id == set_id))}
        return Source(set_id, BUILTIN, "Пробы", None, units)
    try:
        table_id = int(key)
    except ValueError:
        raise HTTPException(422, f"Неизвестная таблица проб «{key}»") from None
    t = session.get(IntervalTable, table_id)
    if t is None or t.set_id != set_id:
        raise HTTPException(404, "Таблица проб не найдена в наборе")
    units = {c["name"]: c.get("unit", "") for c in t.columns or [] if c.get("kind") == "number"}
    return Source(set_id, key, t.name, t.id, units)


def source_sql(src: Source) -> tuple[str, dict]:
    """A FROM item named `src` with id, hole_id, sample_id, from_m, to_m, analytes, attributes.
    For a table of its own its numbers are the analytes (its texts stay out: they are not numbers)."""
    if src.builtin:
        return ("(SELECT id, hole_id, sample_id, from_m, to_m, analytes, attributes FROM assay "
                "WHERE set_id = :src_set) AS src", {"src_set": src.set_id})
    return ("(SELECT id, hole_id, NULL::varchar AS sample_id, from_m, to_m, \"values\" AS analytes, "
            "'{}'::jsonb AS attributes FROM interval_row WHERE table_id = :src_table) AS src", {"src_table": src.table_id})
