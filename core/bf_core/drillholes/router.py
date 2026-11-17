"""/api/v1/drillholes/sets/… — sets, their tables and the «Таблица» panel (ТЗ 7.10)."""

from __future__ import annotations

from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_holes, dh_surveys, dh_tables, objects
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from . import actions
from .expr import ExprError
from .query import page, stats
from .snapshot import Snapshot, build_collars, build_rows, build_surveys, snapshots

router = APIRouter(prefix="/drillholes")
Svc = Annotated[ProjectService, Depends(projects)]
TABLE_TITLES = {"collar": "Устья", "survey": "Инклинометрия"}


def _snapshot(project: Any, set_id: int, table: str) -> Snapshot:
    key = actions.current(project, set_id, table)

    def build() -> Snapshot:
        with dialect.read_tx(project.engine) as conn:
            if table == "collar":
                return build_collars(conn, set_id, key)
            if table == "survey":
                return build_surveys(conn, set_id, key)
            return build_rows(conn, set_id, int(table), key, project.folder / "cache" / "tables")

    return snapshots.get(key, build)


def _changed(request: Request, set_id: int, table: str, rev: int) -> None:
    """3D, charts and the audit learn that the data of a set changed (ТЗ 7.10 «После правки»)."""
    request.app.state.core.events.publish("drillholes.changed", {"set_id": set_id, "table": table, "rev": rev})


def _expr_error(exc: ExprError) -> ApiError:
    return ApiError(422, "bad_expression", str(exc), 'Пример: AU > 0,5 и [Литология] = "гранит".')


@router.get("/sets/{set_id}/tables")
def list_tables(svc: Svc, set_id: int) -> dict[str, Any]:
    """Tabs of a set: collars, surveys, then the interval tables (assay first)."""
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        s = conn.execute(
            sa.select(objects.c.id, objects.c.name, objects.c.version, objects.c.crs).where(
                objects.c.id == set_id, objects.c.kind == "drillhole_set", objects.c.state == "ready"
            )
        ).first()
        if s is None:
            raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")
        collars = conn.execute(
            sa.select(sa.func.count())
            .select_from(dh_holes)
            .where(dh_holes.c.set_id == set_id, dh_holes.c.has_collar.is_(True))
        ).scalar_one()
        surveys = conn.execute(
            sa.select(sa.func.count()).select_from(dh_surveys).where(dh_surveys.c.set_id == set_id)
        ).scalar_one()
        holes = conn.execute(
            sa.select(sa.func.count()).select_from(dh_holes).where(dh_holes.c.set_id == set_id)
        ).scalar_one()
        tabs: list[dict[str, Any]] = [
            {"table": "collar", "kind": "collar", "name": "Устья", "rows": collars, "rev": s.version},
            {"table": "survey", "kind": "survey", "name": "Инклинометрия", "rows": surveys, "rev": s.version},
        ]
        order = sa.case((dh_tables.c.kind == "assay", 0), (dh_tables.c.kind == "interval", 1), else_=2)
        for t in conn.execute(
            sa.select(dh_tables)
            .where(dh_tables.c.set_id == set_id, dh_tables.c.state == "ready")
            .order_by(order, dh_tables.c.id)
        ):
            tabs.append({"table": str(t.id), "kind": t.kind, "name": t.name, "rows": t.row_count, "rev": t.rev})
    return {"set": {"id": s.id, "name": s.name, "crs": s.crs, "holes": holes}, "tables": tabs}


@router.get("/sets/{set_id}/tables/{table}")
def table_page(
    svc: Svc,
    set_id: int,
    table: str,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    sort: str | None = None,
    desc: bool = False,
    hole: int | None = None,
    where: Annotated[str | None, Query(max_length=2000)] = None,
) -> dict[str, Any]:
    """A page of rows (ТЗ 7.10 «Просмотр», «Сортировка», «Выбор скважины», 7.34 п. 21 «Фильтр по выражению»)."""
    project = svc.require_current()
    snap = _snapshot(project, set_id, table)
    sort = sort or None
    if sort is not None and sort not in {c.key for c in snap.columns}:
        raise ApiError(422, "column_not_found", "Нет такой колонки для сортировки.", "Обновите таблицу.")
    try:
        out = page(snap, offset=offset, limit=limit, sort=sort, desc=desc, hole=hole, where=where)
    except ExprError as exc:
        raise _expr_error(exc) from exc
    out["columns"] = [c.as_dict() for c in snap.columns]
    out["rev"] = snap.key[-1]
    out["kind"] = snap.kind
    return out


@router.get("/sets/{set_id}/tables/{table}/stats")
def field_stats(
    svc: Svc,
    set_id: int,
    table: str,
    column: str,
    hole: int | None = None,
    where: Annotated[str | None, Query(max_length=2000)] = None,
) -> dict[str, Any]:
    """Σ: filled, empty, min, max, mean, median, standard deviation; for text — unique and frequent values."""
    project = svc.require_current()
    snap = _snapshot(project, set_id, table)
    try:
        snap.column(column)
    except KeyError:
        raise ApiError(404, "column_not_found", "Колонка не найдена.", "Обновите таблицу.") from None
    try:
        return stats(snap, column, hole=hole, where=where)
    except ExprError as exc:
        raise _expr_error(exc) from exc


class CellBody(BaseModel):
    column: str = Field(max_length=300)
    value: Any = None
    rev: int | None = None


class RowBody(BaseModel):
    values: dict[str, Any]
    rev: int | None = None


@router.patch("/sets/{set_id}/tables/{table}/rows/{row_id}")
def edit_cell(svc: Svc, request: Request, set_id: int, table: str, row_id: int, body: CellBody) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    out = actions.update_cell(project, set_id, table, row_id, body.column, body.value, body.rev)
    if out.get("changed"):
        _changed(request, set_id, table, out["rev"])
    return out


@router.post("/sets/{set_id}/tables/{table}/rows")
def add_row(svc: Svc, request: Request, set_id: int, table: str, body: RowBody) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    out = actions.insert_row(project, set_id, table, body.values, body.rev)
    _changed(request, set_id, table, out["rev"])
    return out


@router.delete("/sets/{set_id}/tables/{table}/rows/{row_id}")
def remove_row(
    svc: Svc, request: Request, set_id: int, table: str, row_id: int, rev: int | None = None
) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    out = actions.delete_row(project, set_id, table, row_id, rev)
    _changed(request, set_id, table, out["rev"])
    return out


@router.get("/sets/{set_id}/edits")
def edits(svc: Svc, set_id: int, table: str | None = None, limit: int = 200) -> dict[str, Any]:
    project = svc.require_current()
    return {"edits": actions.history(project, set_id, table=table, limit=limit)}


@router.post("/edits/{edit_id}/undo")
def undo_edit(svc: Svc, request: Request, edit_id: int) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    out = actions.undo(project, edit_id)
    _changed(request, out["set_id"], out["table"], out["rev"])
    return out
