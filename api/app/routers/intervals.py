"""Interval tables of a drillhole set and the data along holes for the downhole log.

The assays are the set's built-in interval table (key "assay"); other tables (lithology,
alteration, RQD, more assays) are `interval_table` rows, keyed by their id.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Assay, Collar, Component, IntervalRow, IntervalTable
from app.routers.projects import get_set

router = APIRouter(prefix="/api", tags=["intervals"])
MAX_HOLES = 30


def assay_columns(session: Session, set_id: int) -> list[dict]:
    comps = [{"name": c.name, "kind": "number", "unit": c.unit}
             for c in session.scalars(select(Component).where(Component.set_id == set_id).order_by(Component.name))]
    keys = session.execute(
        select(func.jsonb_object_keys(Assay.attributes)).where(Assay.set_id == set_id).distinct()
    ).scalars().all()
    attrs = []
    for k in sorted(keys):
        sample = session.execute(select(Assay.attributes[k]).where(Assay.set_id == set_id, Assay.attributes.has_key(k)).limit(200)).scalars().all()
        numeric = bool(sample) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in sample if v is not None)
        attrs.append({"name": k, "kind": "number" if numeric else "text", "unit": ""})
    return comps + attrs


def tables_of(session: Session, set_id: int) -> list[dict]:
    assays = session.scalar(select(func.count()).select_from(Assay).where(Assay.set_id == set_id)) or 0
    # The built-in assays only when the set has them (assay files are named tables of their own now).
    out = [{"key": "assay", "id": None, "name": "Пробы", "builtin": True, "row_count": assays, "kind": "interval",
            "columns": assay_columns(session, set_id)}] if assays else []
    for t in session.scalars(select(IntervalTable).where(IntervalTable.set_id == set_id).order_by(IntervalTable.id)):
        out.append({"key": str(t.id), "id": t.id, "name": t.name, "builtin": False, "row_count": t.row_count,
                    "columns": t.columns, "filename": t.filename, "kind": t.kind})
    return out


@router.get("/sets/{set_id}/interval-tables")
def list_tables(set_id: int, session: Session = Depends(get_session)) -> list[dict]:
    get_set(session, set_id)
    return tables_of(session, set_id)


def _table(session: Session, table_id: int) -> IntervalTable:
    t = session.get(IntervalTable, table_id)
    if t is None:
        raise HTTPException(404, "Интервальная таблица не найдена")
    return t


class TablePatch(BaseModel):
    name: str = Field(min_length=1, max_length=255)


@router.patch("/interval-tables/{table_id}")
def rename_table(table_id: int, body: TablePatch, session: Session = Depends(get_session)) -> dict:
    t = _table(session, table_id)
    name = body.name.strip()
    clash = session.scalar(select(IntervalTable).where(IntervalTable.set_id == t.set_id, IntervalTable.name == name,
                                                       IntervalTable.id != t.id))
    if clash is not None or name == "Пробы":
        raise HTTPException(409, f"Таблица «{name}» в наборе уже есть")
    t.name = name
    session.commit()
    return {"id": t.id, "name": t.name}


@router.delete("/interval-tables/{table_id}", status_code=204)
def delete_table(table_id: int, session: Session = Depends(get_session)) -> None:
    t = _table(session, table_id)
    session.execute(delete(IntervalRow).where(IntervalRow.table_id == t.id))
    session.delete(t)
    session.commit()


@router.get("/interval-tables/{table_id}/rows")
def table_rows(table_id: int, offset: int = 0, limit: int = 500, session: Session = Depends(get_session)) -> dict:
    """Rows of an interval table for the table view: hole, from, to and every column."""
    t = _table(session, table_id)
    limit = max(1, min(limit, 5000))
    rows = session.scalars(select(IntervalRow).where(IntervalRow.table_id == t.id)
                           .order_by(IntervalRow.hole_id, IntervalRow.from_m).offset(max(0, offset)).limit(limit))
    return {"total": t.row_count, "columns": t.columns,
            "rows": [{"hole_id": r.hole_id, "from": r.from_m, "to": r.to_m, **r.values} for r in rows]}


class DownholeIn(BaseModel):
    # the downhole log window: up to MAX_HOLES holes side by side; `all`: every hole of the set (the 3D view)
    holes: list[str] = Field(default_factory=list, max_length=MAX_HOLES)
    all: bool = False
    # "assay" and / or ids of interval tables; empty: all tables
    tables: list[str] = Field(default_factory=list, max_length=50)
    # table key -> the fields to send (others are left out); a table without an entry sends all of its fields
    fields: dict[str, list[str]] = Field(default_factory=dict)


@router.post("/sets/{set_id}/downhole")
def downhole(set_id: int, body: DownholeIn, session: Session = Depends(get_session)) -> dict:
    """Intervals of the chosen tables along the chosen holes (the downhole log)."""
    get_set(session, set_id)
    tables = tables_of(session, set_id)
    wanted = [t for t in tables if not body.tables or t["key"] in body.tables]
    if body.all:
        names = list(session.scalars(select(Collar.hole_id).where(Collar.set_id == set_id).order_by(Collar.hole_id)))
    elif body.holes:
        names = body.holes
    else:
        raise HTTPException(422, "Нужны скважины: список holes или all")
    collars = {c.hole_id: c for c in session.scalars(select(Collar).where(Collar.set_id == set_id, Collar.hole_id.in_(names)))}

    def pick(key: str, values: dict | None) -> dict:
        keep = body.fields.get(key)
        values = values or {}
        return values if keep is None else {k: values[k] for k in keep if k in values}

    # every row of the chosen tables for the chosen holes in one query per table;
    # with `all`, hole names are matched without regard to case and outer spaces (tables often differ in that)
    norm = (lambda h: h.strip().upper()) if body.all else (lambda h: h)
    wanted_holes = {norm(h): h for h in names}
    by_table: dict[str, dict[str, list]] = {t["key"]: {} for t in wanted}
    for t in wanted:
        if t["key"] == "assay":
            query = select(Assay).where(Assay.set_id == set_id).order_by(Assay.hole_id, Assay.from_m)
            if not body.all:
                query = query.where(Assay.hole_id.in_(names))
            for r in session.scalars(query):
                hole = wanted_holes.get(norm(r.hole_id))
                if hole is not None:
                    by_table["assay"].setdefault(hole, []).append(
                        {"from": r.from_m, "to": r.to_m, "values": pick("assay", {**(r.attributes or {}), **(r.analytes or {})})})
        else:
            query = select(IntervalRow).where(IntervalRow.table_id == t["id"]).order_by(IntervalRow.hole_id, IntervalRow.from_m)
            if not body.all:
                query = query.where(IntervalRow.hole_id.in_(names))
            for r in session.scalars(query):
                hole = wanted_holes.get(norm(r.hole_id))
                if hole is not None:
                    by_table[t["key"]].setdefault(hole, []).append({"from": r.from_m, "to": r.to_m, "values": pick(t["key"], r.values)})
    holes = []
    for hole in names:
        c = collars.get(hole)
        data = {t["key"]: by_table[t["key"]].get(hole, []) for t in wanted}
        depths = [iv["to"] for rows in data.values() for iv in rows if iv["to"] is not None]
        holes.append({"hole_id": hole, "found": c is not None, "td": c.td if c else None,
                      "max_depth": max([*(depths or [0.0]), c.td or 0.0] if c else depths or [0.0]), "tables": data})
    return {"tables": [{k: t[k] for k in ("key", "name", "columns", "kind")} for t in wanted], "holes": holes}
