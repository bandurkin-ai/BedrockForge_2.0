"""/api/v1/drillholes/sets/{id}/tables/{table}/… — tools of the «Таблица» panel and the table export
(ТЗ 7.34 п. 22, 31–36; 9.12). Included into `router.py` (one module per router in api/modules.py)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect

from .router import Svc, _changed, snapshot_of

if TYPE_CHECKING:
    from .snapshot import Snapshot

router = APIRouter()


# --- tools (ТЗ 7.34 п. 22, 31–36) -------------------------------------------------------------------


def _tool(
    request: Request, project: Any, set_id: int, table: str, run: Any, *, rev: int | None = None
) -> dict[str, Any]:
    """Runs a tool in one write transaction on the snapshot the caller saw; the table gets a new revision."""
    from . import edit as e
    from .snapshot import snapshots as cache

    snap = snapshot_of(project, set_id, table)
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
    src = snapshot_of(project, set_id, body.source)

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
    src = snapshot_of(project, set_id, body.source)
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
    snap = snapshot_of(project, set_id, table)
    with dialect.read_tx(project.engine) as conn:
        return tools.check_codes(conn, e.target(conn, set_id, table), snap, column)


class ExportBody(BaseModel):
    path: str = Field(max_length=2000)
    format: str = Field(pattern="^(dat|csv|xlsx)$")
    columns: list[str] | None = None
    hole: int | None = None
    where: str | None = Field(default=None, max_length=2000)
    sort: str | None = None
    desc: bool = False
    delimiter: str | None = Field(default=None, min_length=1, max_length=1)
    decimal: str | None = Field(default=None, pattern="^[.,]$")
    encoding: str | None = Field(default=None, max_length=20)


@router.post("/sets/{set_id}/tables/{table}/export")
def export_table(svc: Svc, request: Request, set_id: int, table: str, body: ExportBody) -> dict[str, Any]:
    """Job «Экспорт таблицы» (DAT / CSV / XLSX) of the rows as the panel shows them."""
    from bf_core.jobs.service import jobs_of

    project = svc.require_current()
    from . import actions

    actions.current(project, set_id, table)  # 404 for an unknown table
    if not Path(body.path).is_absolute():
        raise ApiError(422, "path_not_absolute", "Путь к файлу должен быть полным.", "Выберите файл в окне сохранения.")
    folder = Path(body.path).parent
    if not folder.is_dir():
        raise ApiError(404, "folder_not_found", "Папка для файла не найдена.", "Выберите папку заново.")
    with dialect.read_tx(project.engine) as conn:
        tabs_rows = _rows_of(conn, set_id, table)
    params = {"set_id": set_id, "table": table, "rows": tabs_rows, **body.model_dump()}
    key, job_id = jobs_of(request.app.state.core).submit(
        "export.table", params, title=f"Экспорт таблицы ({body.format.upper()})"
    )
    return {"source": key, "id": job_id}


def _rows_of(conn: Any, set_id: int, table: str) -> int:
    """Rows of a table for the memory estimate of the export job."""
    import sqlalchemy as sa

    from bf_core.db.schema import dh_holes, dh_surveys, dh_tables

    if table == "collar":
        q = sa.select(sa.func.count()).select_from(dh_holes).where(dh_holes.c.set_id == set_id)
    elif table == "survey":
        q = sa.select(sa.func.count()).select_from(dh_surveys).where(dh_surveys.c.set_id == set_id)
    else:
        q = sa.select(dh_tables.c.row_count).where(dh_tables.c.id == int(table))
    return int(conn.execute(q).scalar() or 0)
