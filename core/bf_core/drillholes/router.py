"""/api/v1/drillholes/sets/… — sets, their tables and the «Таблица» panel (ТЗ 7.10)."""

from __future__ import annotations

from pathlib import Path
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


# --- tools (ТЗ 7.34 п. 22, 31–36) -------------------------------------------------------------------


def _tool(
    request: Request, project: Any, set_id: int, table: str, run: Any, *, rev: int | None = None
) -> dict[str, Any]:
    """Runs a tool in one write transaction on the snapshot the caller saw; the table gets a new revision."""
    from . import edit as e
    from .snapshot import snapshots as cache

    snap = _snapshot(project, set_id, table)
    expected = snap.key[-1] if rev is None else rev
    with dialect.write_tx(project.engine) as conn:
        t = e.target(conn, set_id, table)
        out = run(conn, t, snap) or {}
        new_rev = e.bump(conn, t, expected, rows_delta=int(out.pop("_rows_delta", 0)))
    cache.forget(set_id)
    _changed(request, set_id, table, new_rev)
    return {**out, "rev": new_rev}


class ReplaceBody(BaseModel):
    column: str
    find: str = Field(max_length=1000)
    replace: str = Field(default="", max_length=1000)
    whole: bool = False
    case: bool = False
    hole: int | None = None
    where: str | None = Field(default=None, max_length=2000)
    rev: int | None = None


@router.post("/sets/{set_id}/tables/{table}/replace")
def find_replace(svc: Svc, request: Request, set_id: int, table: str, body: ReplaceBody) -> dict[str, Any]:
    from . import tools

    project = svc.require_current(writable=True)

    def run(conn: Any, t: Any, snap: Snapshot) -> dict[str, Any]:
        n = tools.replace(
            conn,
            t,
            snap,
            body.column,
            body.find,
            body.replace,
            whole=body.whole,
            case=body.case,
            hole=body.hole,
            where=body.where,
        )
        if not n:
            raise ApiError(404, "nothing_found", "Ничего не найдено.", "Измените условие поиска.")
        return {"replaced": n}

    return _tool(request, project, set_id, table, run, rev=body.rev)


class ColumnBody(BaseModel):
    name: str = Field(max_length=200)
    kind: str = "number"
    unit: str | None = Field(default=None, max_length=40)
    expression: str | None = Field(default=None, max_length=2000)
    rev: int | None = None


@router.post("/sets/{set_id}/tables/{table}/columns")
def add_column(svc: Svc, request: Request, set_id: int, table: str, body: ColumnBody) -> dict[str, Any]:
    """«+ Колонка» and «Вычисляемое поле» (with an expression)."""
    from . import tools

    project = svc.require_current(writable=True)
    return _tool(
        request,
        project,
        set_id,
        table,
        lambda conn, t, snap: tools.add_column(
            conn, t, snap, body.name, body.kind, unit=body.unit, expression=body.expression
        ),
        rev=body.rev,
    )


class RenameBody(BaseModel):
    name: str = Field(max_length=200)
    rev: int | None = None


@router.patch("/sets/{set_id}/tables/{table}/columns/{column}")
def rename_column(svc: Svc, request: Request, set_id: int, table: str, column: str, body: RenameBody) -> dict[str, Any]:
    from . import tools

    project = svc.require_current(writable=True)
    return _tool(
        request,
        project,
        set_id,
        table,
        lambda conn, t, _s: tools.rename_column(conn, t, column, body.name),
        rev=body.rev,
    )


@router.delete("/sets/{set_id}/tables/{table}/columns/{column}")
def delete_column(
    svc: Svc, request: Request, set_id: int, table: str, column: str, rev: int | None = None
) -> dict[str, Any]:
    from . import tools

    project = svc.require_current(writable=True)
    return _tool(request, project, set_id, table, lambda conn, t, _s: tools.delete_column(conn, t, column), rev=rev)


class AppendBody(BaseModel):
    source: str
    rev: int | None = None


@router.post("/sets/{set_id}/tables/{table}/append")
def append_table(svc: Svc, request: Request, set_id: int, table: str, body: AppendBody) -> dict[str, Any]:
    """«Объединение таблиц»: rows of another table of the set are added to this one."""
    from . import edit as e
    from . import tools

    project = svc.require_current(writable=True)
    src = _snapshot(project, set_id, body.source)

    def run(conn: Any, t: Any, _snap: Snapshot) -> dict[str, Any]:
        out = tools.append_table(conn, t, e.target(conn, set_id, body.source), src)
        return {**out, "_rows_delta": out["rows"]}

    return _tool(request, project, set_id, table, run, rev=body.rev)


class MergeBody(BaseModel):
    source: str
    key: list[str] = Field(min_length=1, max_length=5)
    columns: list[str] = Field(min_length=1, max_length=50)
    rev: int | None = None


@router.post("/sets/{set_id}/tables/{table}/merge")
def merge_by_key(svc: Svc, request: Request, set_id: int, table: str, body: MergeBody) -> dict[str, Any]:
    """«Слияние по ключу»: columns of another table where hole + key fields match."""
    from . import edit as e
    from . import tools

    project = svc.require_current(writable=True)
    src = _snapshot(project, set_id, body.source)
    return _tool(
        request,
        project,
        set_id,
        table,
        lambda conn, t, snap: tools.merge_by_key(
            conn, t, snap, e.target(conn, set_id, body.source), src, body.key, body.columns
        ),
        rev=body.rev,
    )


class CodesBody(BaseModel):
    name: str = Field(max_length=200)
    items: list[dict[str, str]] = Field(max_length=10_000)


@router.get("/codes")
def list_codes(svc: Svc) -> dict[str, Any]:
    from . import tools

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"lists": tools.code_lists(conn)}


@router.put("/codes")
def save_codes(svc: Svc, body: CodesBody) -> dict[str, Any]:
    from . import tools

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        tools.save_code_list(conn, body.name, body.items)
        return {"lists": tools.code_lists(conn)}


class LinkBody(BaseModel):
    column: str
    codes: str | None = None


@router.put("/sets/{set_id}/tables/{table}/codes")
def link_codes(svc: Svc, set_id: int, table: str, body: LinkBody) -> dict[str, Any]:
    from . import edit as e
    from . import tools

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        tools.link_codes(conn, e.target(conn, set_id, table), body.column, body.codes)
    return {"ok": True}


@router.get("/sets/{set_id}/tables/{table}/codes/check")
def check_codes(svc: Svc, set_id: int, table: str, column: str) -> dict[str, Any]:
    """«Проверка по справочнику»: values that are not in the list, and a filter that shows their rows."""
    from . import edit as e
    from . import tools

    project = svc.require_current()
    snap = _snapshot(project, set_id, table)
    with dialect.read_tx(project.engine) as conn:
        return tools.check_codes(conn, e.target(conn, set_id, table), snap, column)


# --- sets (ТЗ 7.10: «+» у «Скважин», set.create) -------------------------------------------------------


class SetBody(BaseModel):
    name: str = Field(max_length=200)


@router.post("/sets")
def create_set(svc: Svc, request: Request, body: SetBody) -> dict[str, Any]:
    """An empty set: collars, surveys and tables are added by import or by hand."""
    from .store import NameTaken
    from .store import create_set as make

    project = svc.require_current(writable=True)
    name = " ".join(body.name.split())
    if not name:
        raise ApiError(422, "name_empty", "Название набора не может быть пустым.", "Введите название набора.")
    with dialect.write_tx(project.engine) as conn:
        try:
            set_id = make(conn, name)
        except NameTaken:
            raise ApiError(409, "name_taken", f"Набор «{name}» уже есть.", "Введите другое название.") from None
    request.app.state.core.events.publish("objects.changed", {"kind": "drillhole_set", "id": set_id})
    return {"id": set_id, "name": name}


@router.patch("/sets/{set_id}")
def rename_set(svc: Svc, request: Request, set_id: int, body: SetBody) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    name = " ".join(body.name.split())
    if not name:
        raise ApiError(422, "name_empty", "Название набора не может быть пустым.", "Введите название набора.")
    with dialect.write_tx(project.engine) as conn:
        taken = conn.execute(
            sa.select(objects.c.id).where(
                objects.c.kind == "drillhole_set",
                objects.c.name_key == dialect.name_key(name),
                objects.c.id != set_id,
                objects.c.state.in_(("ready", "building")),
            )
        ).first()
        if taken is not None:
            raise ApiError(409, "name_taken", f"Набор «{name}» уже есть.", "Введите другое название.")
        res = conn.execute(
            sa.update(objects)
            .where(objects.c.id == set_id, objects.c.kind == "drillhole_set", objects.c.state == "ready")
            .values(name=name, name_key=dialect.name_key(name), updated_at=dialect.utcnow())
        )
        if res.rowcount != 1:
            raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")
    request.app.state.core.events.publish("objects.changed", {"kind": "drillhole_set", "id": set_id})
    return {"id": set_id, "name": name}


@router.delete("/sets/{set_id}")
def delete_set(svc: Svc, request: Request, set_id: int) -> dict[str, Any]:
    """Deletes a set with its holes, tables and import history; its source files leave the storage
    (the storage cleanup removes files nobody owns)."""
    from .store import drop_import_owners

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        row = conn.execute(
            sa.select(objects.c.state).where(objects.c.id == set_id, objects.c.kind == "drillhole_set")
        ).first()
        if row is None:
            raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")
        if row.state != "ready":
            raise ApiError(409, "set_busy", "В набор сейчас идёт импорт.", "Дождитесь окончания или отмените задание.")
        if conn.execute(
            sa.select(dh_tables.c.id).where(dh_tables.c.set_id == set_id, dh_tables.c.state == "building")
        ).first():
            raise ApiError(409, "set_busy", "В набор сейчас идёт импорт.", "Дождитесь окончания или отмените задание.")
        drop_import_owners(conn, [set_id])
        from bf_core.db.schema import blob_owners

        conn.execute(
            sa.delete(blob_owners).where(blob_owners.c.owner_kind == "object", blob_owners.c.owner_id == set_id)
        )
        conn.execute(sa.delete(objects).where(objects.c.id == set_id))
    snapshots.forget(set_id)
    request.app.state.core.events.publish("objects.changed", {"kind": "drillhole_set", "id": set_id, "deleted": True})
    return {"deleted": set_id}


class ExportBody(BaseModel):
    path: str = Field(max_length=2000)
    format: str = Field(pattern="^(dat|csv|xlsx)$")
    columns: list[str] | None = None
    hole: int | None = None
    where: str | None = Field(default=None, max_length=2000)
    sort: str | None = None
    desc: bool = False
    delimiter: str | None = Field(default=None, max_length=1)
    decimal: str | None = Field(default=None, pattern="^[.,]$")
    encoding: str | None = Field(default=None, max_length=20)


@router.post("/sets/{set_id}/tables/{table}/export")
def export_table(svc: Svc, request: Request, set_id: int, table: str, body: ExportBody) -> dict[str, Any]:
    """Job «Экспорт таблицы» (DAT / CSV / XLSX) of the rows as the panel shows them."""
    from bf_core.jobs.service import jobs_of

    project = svc.require_current()
    actions.current(project, set_id, table)  # 404 for an unknown table
    folder = Path(body.path).parent
    if not folder.is_dir():
        raise ApiError(404, "folder_not_found", "Папка для файла не найдена.", "Выберите папку заново.")
    params = {"set_id": set_id, "table": table, **body.model_dump()}
    key, job_id = jobs_of(request.app.state.core).submit(
        "export.table", params, title=f"Экспорт таблицы ({body.format.upper()})"
    )
    return {"source": key, "id": job_id}
