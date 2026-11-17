"""/api/v1/audit/sets/{set_id}/… — the audit of a drillhole set (ТЗ 7.12): run, result, findings, thresholds,
Excel report. numpy and the checks load on first use (not at the start of the core)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter(prefix="/audit")
Svc = Annotated[ProjectService, Depends(projects)]


def _set(conn: sa.Connection, set_id: int) -> None:
    if (
        conn.execute(
            sa.select(objects.c.id).where(
                objects.c.id == set_id, objects.c.kind == "drillhole_set", objects.c.state == "ready"
            )
        ).first()
        is None
    ):
        raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")


@router.get("/rules")
def rules() -> dict[str, Any]:
    from .rules import EXAMPLE_FIELDS, RULES, SEVERITY_LABELS, TABLE_LABELS

    return {
        "rules": [
            {"code": r.code, "severity": r.severity, "table": r.table, "title": r.title, "description": r.description}
            for r in RULES.values()
        ],
        "severities": SEVERITY_LABELS,
        "tables": TABLE_LABELS,
        "fields": EXAMPLE_FIELDS,
    }


@router.get("/sets/{set_id}")
def result(svc: Svc, set_id: int) -> dict[str, Any]:
    """The last result (every rule, passed ones too), «устарел», thresholds and the sample tables to choose."""
    from .service import get_settings, latest, sample_tables

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        _set(conn, set_id)
        return {
            "result": latest(conn, set_id),
            "settings": get_settings(conn, set_id),
            "tables": sample_tables(conn, set_id),
        }


class SettingsBody(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    table: str | None = Field(default=None, max_length=32)


@router.put("/sets/{set_id}/settings")
def save_settings(svc: Svc, set_id: int, body: SettingsBody) -> dict[str, Any]:
    from .service import put_settings

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        _set(conn, set_id)
        return put_settings(conn, set_id, body.config, body.table)


@router.post("/sets/{set_id}/run")
def run(svc: Svc, request: Request, set_id: int) -> dict[str, Any]:
    """Job «Аудит данных скважин» with the saved thresholds and table."""
    from bf_core.jobs.service import jobs_of

    from .service import get_settings

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        _set(conn, set_id)
        st = get_settings(conn, set_id)
        rows = 0
        if st["table"]:
            rows = int(
                conn.execute(sa.select(dh_tables.c.row_count).where(dh_tables.c.id == int(st["table"]))).scalar() or 0
            )
    params = {"set_id": set_id, "table": st["table"], "config": st["config"], "rows": rows}
    key, job_id = jobs_of(request.app.state.core).submit("audit.run", params, title="Аудит данных скважин")
    return {"source": key, "id": job_id}


@router.get("/sets/{set_id}/findings")
def list_findings(
    svc: Svc,
    set_id: int,
    severity: str | None = None,
    table: str | None = None,
    code: str | None = None,
    hole: Annotated[str | None, Query(max_length=200)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> dict[str, Any]:
    """Findings grouped by hole and rule (count and up to 20 examples), filtered (ТЗ 7.12)."""
    from .service import findings, latest

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        _set(conn, set_id)
        res = latest(conn, set_id)
        if res is None:
            return {"total": 0, "findings": []}
        return findings(
            conn, res["run_id"], severity=severity, source=table, code=code, hole=hole, offset=offset, limit=limit
        )


class ExportBody(BaseModel):
    path: str = Field(max_length=2000)


@router.post("/sets/{set_id}/export")
def export(svc: Svc, request: Request, set_id: int, body: ExportBody) -> dict[str, Any]:
    """Job «Отчёт аудита Excel» (sheets «Сводка», «По правилам», «Находки»)."""
    from bf_core.jobs.service import jobs_of

    from .service import latest

    project = svc.require_current()
    path = Path(body.path)
    if not path.is_absolute() or not path.parent.is_dir():
        raise ApiError(404, "folder_not_found", "Папка для файла не найдена.", "Выберите файл в окне сохранения.")
    with dialect.read_tx(project.engine) as conn:
        _set(conn, set_id)
        if latest(conn, set_id) is None:
            raise ApiError(409, "no_audit", "Аудит набора ещё не запускался.", "Запустите аудит.")
    key, job_id = jobs_of(request.app.state.core).submit(
        "audit.export", {"set_id": set_id, "path": body.path}, title="Отчёт аудита Excel"
    )
    return {"source": key, "id": job_id}
