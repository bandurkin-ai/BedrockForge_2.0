"""Every table of a drillhole set in one shape: collars, surveys, the built-in assays and the interval and
event tables. Rows page with sorting and a hole filter, every field has basic statistics, and rows can be
corrected by hand: each change goes to the edit log of the set and can be undone.

    GET    /api/sets/{set_id}/datatables                       the tables of the set (tabs)
    GET    /api/sets/{set_id}/datatables/{key}                 a page of rows (sort, desc, hole_id)
    GET    /api/sets/{set_id}/datatables/{key}/stats           statistics of every field
    POST   /api/sets/{set_id}/datatables/{key}/rows            a new row
    PATCH  /api/sets/{set_id}/datatables/{key}/rows/{row_id}   change fields of a row
    DELETE /api/sets/{set_id}/datatables/{key}/rows/{row_id}   delete a row
    GET    /api/sets/{set_id}/edits                            the edit log of the set
    POST   /api/edits/{edit_id}/undo                           undo one edit

`key`: "collar", "survey", "assay" or the id of an interval / event table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Float, String, case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Assay, Collar, Component, DataEdit, IntervalRow, IntervalTable, Survey
from app.routers.projects import get_set

router = APIRouter(prefix="/api", tags=["datatables"])

MAX_TOP = 8
MAX_ATTRIBUTE_KEYS = 40


@dataclass
class Col:
    name: str
    kind: Literal["number", "text"]
    unit: str = ""
    # an attribute of the model, or a key of one of its JSON fields
    attr: str | None = None
    json: str | None = None
    required: bool = False


@dataclass
class Spec:
    key: str
    name: str
    kind: str  # collar | survey | assay | interval | event
    model: Any
    where: list
    columns: list[Col]
    order: list
    defaults: dict = field(default_factory=dict)
    table: IntervalTable | None = None


def _attribute_cols(session: Session, model, where: list, used: set[str]) -> list[Col]:
    """Extra fields kept in `attributes` (columns of the source file not in the standard set)."""
    keys = session.scalars(
        select(func.jsonb_object_keys(model.attributes)).where(*where).distinct().limit(MAX_ATTRIBUTE_KEYS)
    ).all()
    out = []
    for k in sorted(k for k in keys if k not in used):
        non_number = session.scalar(
            select(func.count()).select_from(model).where(
                *where, model.attributes.has_key(k), func.jsonb_typeof(model.attributes[k]) != "number"  # noqa: W601
            )
        )
        out.append(Col(k, "text" if non_number else "number", json="attributes"))
    return out


def spec_of(session: Session, set_id: int, key: str) -> Spec:
    if key == "collar":
        where = [Collar.set_id == set_id]
        cols = [Col("hole_id", "text", attr="hole_id", required=True)] + [
            Col(n, "number", "м" if n in ("x", "y", "z", "td") else "°", attr=n) for n in ("x", "y", "z", "td", "azimuth", "dip")
        ] + [Col(n, "text", attr=n) for n in ("date", "status", "stage")]
        cols += _attribute_cols(session, Collar, where, {c.name for c in cols})
        return Spec(key, "Устья", "collar", Collar, where, cols, [Collar.hole_id, Collar.id], {"set_id": set_id, "attributes": {}})
    if key == "survey":
        where = [Survey.set_id == set_id]
        cols = [Col("hole_id", "text", attr="hole_id", required=True), Col("depth", "number", "м", attr="depth", required=True),
                Col("azimuth", "number", "°", attr="azimuth"), Col("dip", "number", "°", attr="dip")]
        cols += _attribute_cols(session, Survey, where, {c.name for c in cols})
        return Spec(key, "Инклинометрия", "survey", Survey, where, cols, [Survey.hole_id, Survey.depth, Survey.id], {"set_id": set_id, "attributes": {}})
    if key == "assay":
        where = [Assay.set_id == set_id]
        cols = [Col("hole_id", "text", attr="hole_id", required=True), Col("from", "number", "м", attr="from_m", required=True),
                Col("to", "number", "м", attr="to_m", required=True), Col("sample_id", "text", attr="sample_id")]
        cols += [Col(c.name, "number", c.unit or "", json="analytes")
                 for c in session.scalars(select(Component).where(Component.set_id == set_id).order_by(Component.name))]
        cols += _attribute_cols(session, Assay, where, {c.name for c in cols})
        return Spec(key, "Пробы", "assay", Assay, where, cols, [Assay.hole_id, Assay.from_m, Assay.id],
                    {"set_id": set_id, "analytes": {}, "attributes": {}})
    if not key.isdigit():
        raise HTTPException(404, "Таблица не найдена")
    t = session.get(IntervalTable, int(key))
    if t is None or t.set_id != set_id:
        raise HTTPException(404, "Таблица не найдена")
    event = t.kind == "event"
    where = [IntervalRow.table_id == t.id]
    cols = [Col("hole_id", "text", attr="hole_id", required=True)]
    cols += [Col("depth", "number", "м", attr="from_m", required=True)] if event else [
        Col("from", "number", "м", attr="from_m", required=True), Col("to", "number", "м", attr="to_m", required=True)]
    cols += [Col(c["name"], "number" if c.get("kind") == "number" else "text", c.get("unit") or "", json="values") for c in t.columns]
    return Spec(key, t.name, "event" if event else "interval", IntervalRow, where, cols,
                [IntervalRow.hole_id, IntervalRow.from_m, IntervalRow.id], {"table_id": t.id, "values": {}}, t)


def _expr(spec: Spec, c: Col):
    """The SQL value of a field: numbers of a JSON field only where they are numbers."""
    if c.attr:
        return getattr(spec.model, c.attr)
    j = getattr(spec.model, c.json)[c.name]
    if c.kind == "number":
        return case((func.jsonb_typeof(j) == "number", j.astext.cast(Float)), else_=None)
    return case((func.jsonb_typeof(j).in_(["string", "number", "boolean"]), j.astext), else_=None)


def _row(spec: Spec, item) -> dict:
    out: dict[str, Any] = {"_id": item.id}
    for c in spec.columns:
        out[c.name] = getattr(item, c.attr) if c.attr else (getattr(item, c.json) or {}).get(c.name)
    return out


def _col_out(c: Col) -> dict:
    return {"name": c.name, "kind": c.kind, "unit": c.unit, "required": c.required}


# ---- reading


@router.get("/sets/{set_id}/datatables")
def list_tables(set_id: int, session: Session = Depends(get_session)) -> list[dict]:
    get_set(session, set_id)

    def count(model) -> int:
        return session.scalar(select(func.count()).select_from(model).where(model.set_id == set_id)) or 0

    out = [
        {"key": "collar", "name": "Устья", "kind": "collar", "rows": count(Collar)},
        {"key": "survey", "name": "Инклинометрия", "kind": "survey", "rows": count(Survey)},
    ]
    assays = count(Assay)
    if assays:
        out.append({"key": "assay", "name": "Пробы", "kind": "assay", "rows": assays})
    for t in session.scalars(select(IntervalTable).where(IntervalTable.set_id == set_id).order_by(IntervalTable.id)):
        out.append({"key": str(t.id), "name": t.name, "kind": "event" if t.kind == "event" else "interval", "rows": t.row_count})
    return out


@router.get("/sets/{set_id}/datatables/{key}")
def table_rows(
    set_id: int, key: str,
    offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
    sort: str | None = None, desc: bool = False, hole_id: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    get_set(session, set_id)
    spec = spec_of(session, set_id, key)
    where = list(spec.where)
    if hole_id:
        where.append(spec.model.hole_id == hole_id)
    total = session.scalar(select(func.count()).select_from(spec.model).where(*where)) or 0
    order = list(spec.order)
    if sort:
        col = next((c for c in spec.columns if c.name == sort), None)
        if col is None:
            raise HTTPException(422, f"Нет поля {sort}")
        e = _expr(spec, col)
        order = [e.desc().nulls_last() if desc else e.asc().nulls_last(), spec.model.id]
    items = session.scalars(select(spec.model).where(*where).order_by(*order).offset(offset).limit(limit)).all()
    return {"key": spec.key, "name": spec.name, "kind": spec.kind, "total": total,
            "columns": [_col_out(c) for c in spec.columns], "rows": [_row(spec, i) for i in items]}


@router.get("/sets/{set_id}/datatables/{key}/stats")
def table_stats(set_id: int, key: str, hole_id: str | None = None, session: Session = Depends(get_session)) -> dict:
    """Numbers: filled, empty, min, max, mean, median, standard deviation, sum. Text: filled, empty, distinct values, the most frequent."""
    get_set(session, set_id)
    spec = spec_of(session, set_id, key)
    where = list(spec.where)
    if hole_id:
        where.append(spec.model.hole_id == hole_id)
    total = session.scalar(select(func.count()).select_from(spec.model).where(*where)) or 0
    fields = []
    for c in spec.columns:
        e = _expr(spec, c)
        if c.kind == "number":
            n, lo, hi, mean, std, total_sum, median = session.execute(
                select(func.count(e), func.min(e), func.max(e), func.avg(e), func.stddev_samp(e), func.sum(e),
                       func.percentile_cont(0.5).within_group(e)).where(*where)
            ).one()
            fields.append({**_col_out(c), "count": n, "empty": total - n, "min": lo, "max": hi,
                           "mean": _num(mean), "median": _num(median), "std": _num(std), "sum": _num(total_sum)})
        else:
            t = func.nullif(func.trim(e.cast(String)), "")
            n, unique = session.execute(select(func.count(t), func.count(distinct(t))).where(*where)).one()
            top = session.execute(
                select(t, func.count()).where(*where, t.is_not(None)).group_by(t).order_by(func.count().desc(), t).limit(MAX_TOP)
            ).all()
            fields.append({**_col_out(c), "count": n, "empty": total - n, "unique": unique, "top": [[v, k] for v, k in top]})
    return {"key": spec.key, "name": spec.name, "total": total, "fields": fields}


def _num(v) -> float | None:
    if v is None:
        return None
    f = float(v)
    return f if math.isfinite(f) else None


# ---- editing


class RowIn(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


def _convert(c: Col, raw: Any) -> Any:
    """A value typed in the table: empty -> None, numbers with a comma or a point."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        if c.required:
            raise HTTPException(422, f"Поле «{c.name}» обязательно")
        return None
    if c.kind == "number":
        if isinstance(raw, bool):
            raise HTTPException(422, f"«{c.name}»: нужно число")
        try:
            v = float(str(raw).strip().replace(",", ".").replace(" ", "")) if not isinstance(raw, (int, float)) else float(raw)
        except ValueError:
            raise HTTPException(422, f"«{c.name}»: «{raw}» — не число") from None
        if not math.isfinite(v):
            raise HTTPException(422, f"«{c.name}»: нужно конечное число")
        return int(v) if v.is_integer() and c.json else v
    text = str(raw).strip()
    if c.name == "hole_id" and len(text) > 64:
        raise HTTPException(422, "Номер скважины длиннее 64 знаков")
    return text[:500]


def _get_item(session: Session, spec: Spec, row_id: int):
    item = session.get(spec.model, row_id)
    owner = (item.table_id == spec.table.id) if (item is not None and spec.table is not None) else (item is not None and item.set_id == spec.defaults.get("set_id"))
    if not owner:
        raise HTTPException(404, "Строка не найдена")
    return item


def _apply(spec: Spec, item, values: dict) -> dict:
    """Writes the values into the row; returns {field: [old, new]} of what changed."""
    by_name = {c.name: c for c in spec.columns}
    unknown = [k for k in values if k not in by_name]
    if unknown:
        raise HTTPException(422, f"Нет полей: {', '.join(unknown)}")
    changes = {}
    jsons: dict[str, dict] = {}
    for name, raw in values.items():
        c = by_name[name]
        new = _convert(c, raw)
        if c.attr:
            old = getattr(item, c.attr)
            if old != new:
                setattr(item, c.attr, new)
                changes[name] = [old, new]
        else:
            data = jsons.setdefault(c.json, dict(getattr(item, c.json) or {}))
            old = data.get(name)
            if old != new:
                if new is None:
                    data.pop(name, None)
                else:
                    data[name] = new
                changes[name] = [old, new]
    for attr, data in jsons.items():
        setattr(item, attr, data)  # a new dict: the JSONB column is written
    _check(spec, item)
    return changes


def _check(spec: Spec, item) -> None:
    if spec.kind in ("assay", "interval") and item.from_m is not None and item.to_m is not None and item.to_m < item.from_m:
        raise HTTPException(422, f"Интервал {item.hole_id}: «до» ({item.to_m:g}) меньше «от» ({item.from_m:g})")
    if spec.kind == "event":
        item.to_m = item.from_m  # an event has one depth


def _unique_collar(session: Session, spec: Spec, item) -> None:
    if spec.kind != "collar":
        return
    with session.no_autoflush:  # the changed row is not written before the check
        dup = session.scalar(select(func.count()).select_from(Collar).where(
            Collar.set_id == item.set_id, Collar.hole_id == item.hole_id, Collar.id != (item.id or -1)))
    if dup:
        raise HTTPException(409, f"Устье {item.hole_id} уже есть в наборе")


def _log(session: Session, set_id: int, spec: Spec, item, action: str, changes: dict) -> DataEdit:
    edit = DataEdit(set_id=set_id, table_key=spec.key, table_name=spec.name, row_id=item.id, hole_id=item.hole_id,
                    action=action, changes=changes)
    session.add(edit)
    return edit


def _snapshot(spec: Spec, item) -> dict:
    return {c.name: [v, None] for c, v in ((c, _row(spec, item)[c.name]) for c in spec.columns) if v is not None}


@router.patch("/sets/{set_id}/datatables/{key}/rows/{row_id}")
def update_row(set_id: int, key: str, row_id: int, body: RowIn, session: Session = Depends(get_session)) -> dict:
    get_set(session, set_id)
    spec = spec_of(session, set_id, key)
    item = _get_item(session, spec, row_id)
    changes = _apply(spec, item, body.values)
    _unique_collar(session, spec, item)
    if changes:
        _log(session, set_id, spec, item, "update", changes)
    session.commit()
    return {"row": _row(spec, item), "changed": list(changes)}


@router.post("/sets/{set_id}/datatables/{key}/rows", status_code=201)
def insert_row(set_id: int, key: str, body: RowIn, session: Session = Depends(get_session)) -> dict:
    get_set(session, set_id)
    spec = spec_of(session, set_id, key)
    item = spec.model(**{k: (dict(v) if isinstance(v, dict) else v) for k, v in spec.defaults.items()})
    missing = [c.name for c in spec.columns if c.required and c.name not in body.values]
    if missing:
        raise HTTPException(422, f"Нужны поля: {', '.join(missing)}")
    _apply(spec, item, body.values)
    _unique_collar(session, spec, item)
    session.add(item)
    session.flush()
    if spec.table is not None:
        spec.table.row_count = (spec.table.row_count or 0) + 1
    _log(session, set_id, spec, item, "insert", {k: [None, v] for k, v in _row(spec, item).items() if k != "_id" and v is not None})
    session.commit()
    return {"row": _row(spec, item)}


@router.delete("/sets/{set_id}/datatables/{key}/rows/{row_id}", status_code=204)
def delete_row(set_id: int, key: str, row_id: int, session: Session = Depends(get_session)) -> None:
    get_set(session, set_id)
    spec = spec_of(session, set_id, key)
    item = _get_item(session, spec, row_id)
    _log(session, set_id, spec, item, "delete", _snapshot(spec, item))
    if spec.table is not None:
        spec.table.row_count = max(0, (spec.table.row_count or 0) - 1)
    session.delete(item)
    session.commit()


def _edit_out(e: DataEdit) -> dict:
    return {"id": e.id, "table_key": e.table_key, "table_name": e.table_name, "row_id": e.row_id, "hole_id": e.hole_id,
            "action": e.action, "changes": e.changes, "undone": e.undone, "created_at": e.created_at.isoformat() if e.created_at else None}


@router.get("/sets/{set_id}/edits")
def list_edits(set_id: int, limit: int = Query(100, ge=1, le=1000), session: Session = Depends(get_session)) -> list[dict]:
    get_set(session, set_id)
    edits = session.scalars(select(DataEdit).where(DataEdit.set_id == set_id).order_by(DataEdit.id.desc()).limit(limit))
    return [_edit_out(e) for e in edits]


@router.post("/edits/{edit_id}/undo")
def undo_edit(edit_id: int, session: Session = Depends(get_session)) -> dict:
    """Puts back what an edit changed: old values, a deleted row (as a new row), or removes an inserted one."""
    e = session.get(DataEdit, edit_id)
    if e is None:
        raise HTTPException(404, "Правка не найдена")
    if e.undone:
        raise HTTPException(409, "Эта правка уже отменена")
    spec = spec_of(session, e.set_id, e.table_key)
    if e.action == "update":
        item = _get_item(session, spec, e.row_id)
        changes = _apply(spec, item, {k: v[0] for k, v in e.changes.items()})
        _unique_collar(session, spec, item)
        if changes:
            _log(session, e.set_id, spec, item, "update", changes)
    elif e.action == "insert":
        item = session.get(spec.model, e.row_id)
        if item is not None:
            _log(session, e.set_id, spec, item, "delete", _snapshot(spec, item))
            if spec.table is not None:
                spec.table.row_count = max(0, (spec.table.row_count or 0) - 1)
            session.delete(item)
    else:
        item = spec.model(**{k: (dict(v) if isinstance(v, dict) else v) for k, v in spec.defaults.items()})
        _apply(spec, item, {k: v[0] for k, v in e.changes.items()})
        _unique_collar(session, spec, item)
        session.add(item)
        session.flush()
        if spec.table is not None:
            spec.table.row_count = (spec.table.row_count or 0) + 1
        _log(session, e.set_id, spec, item, "insert", {k: [None, v] for k, v in _row(spec, item).items() if k != "_id" and v is not None})
    e.undone = True
    session.commit()
    return _edit_out(e)
