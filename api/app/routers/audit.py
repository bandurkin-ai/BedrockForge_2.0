from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.checks import AuditConfig
from app.audit.engine import rule_table, run_audit
from app.audit.report import build_xlsx
from app.db import get_session
from app.models import AuditFinding, AuditRun, ImportJob
from app.routers.projects import get_project

router = APIRouter(prefix="/api/projects/{project_id}/audit", tags=["audit"])

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ConfigIn(BaseModel):
    depth_tolerance: float = 0.01
    dogleg_per_30m: float = 10.0
    isolated_factor: float = 10.0
    z_jump: float = 50.0
    assay_outlier_z: float = 8.0


class RuleOut(BaseModel):
    code: str
    severity: str
    table: str
    title: str
    description: str
    findings: int
    rows: int


class RunOut(BaseModel):
    run_id: int
    created_at: datetime
    config: dict
    by_severity: dict[str, int]
    # True when data was imported after this audit ran.
    stale: bool
    rules: list[RuleOut]


class FindingOut(BaseModel):
    id: int
    code: str
    severity: str
    table: str
    hole_id: str | None
    count: int
    message: str
    details: dict


class FindingsPage(BaseModel):
    total: int
    items: list[FindingOut]


def _latest_run(session: Session, project_id: int) -> AuditRun | None:
    return session.scalars(
        select(AuditRun).where(AuditRun.project_id == project_id).order_by(AuditRun.id.desc()).limit(1)
    ).first()


def _run_out(session: Session, run: AuditRun) -> RunOut:
    last_import = session.scalar(
        select(func.max(ImportJob.created_at)).where(ImportJob.project_id == run.project_id)
    )
    return RunOut(
        run_id=run.id,
        created_at=run.created_at,
        config=run.config,
        by_severity=run.counts["by_severity"],
        stale=last_import is not None and last_import > run.created_at,
        rules=[RuleOut(**r) for r in rule_table(run.counts)],
    )


@router.post("", response_model=RunOut)
def start_audit(project_id: int, config: ConfigIn | None = None, session: Session = Depends(get_session)):
    """Run the audit and replace the previously stored result."""
    get_project(session, project_id)
    cfg = AuditConfig(**config.model_dump()) if config else AuditConfig()
    return _run_out(session, run_audit(session, project_id, cfg))


@router.get("", response_model=RunOut | None)
def latest_audit(project_id: int, session: Session = Depends(get_session)):
    get_project(session, project_id)
    run = _latest_run(session, project_id)
    return _run_out(session, run) if run else None


@router.get("/findings", response_model=FindingsPage)
def findings(
    project_id: int,
    severity: str | None = None,
    table: str | None = None,
    code: str | None = None,
    hole_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    get_project(session, project_id)
    run = _latest_run(session, project_id)
    if run is None:
        return FindingsPage(total=0, items=[])

    query = select(AuditFinding).where(AuditFinding.run_id == run.id)
    if severity:
        query = query.where(AuditFinding.severity == severity)
    if table:
        query = query.where(AuditFinding.table_name == table)
    if code:
        query = query.where(AuditFinding.code == code)
    if hole_id:
        query = query.where(AuditFinding.hole_id.ilike(f"%{hole_id}%"))

    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    severity_order = func.array_position(["error", "warning", "info"], AuditFinding.severity)
    rows = session.scalars(
        query.order_by(severity_order, AuditFinding.table_name, AuditFinding.code, AuditFinding.hole_id, AuditFinding.id)
        .limit(limit).offset(offset)
    ).all()
    return FindingsPage(
        total=total,
        items=[
            FindingOut(
                id=f.id, code=f.code, severity=f.severity, table=f.table_name, hole_id=f.hole_id,
                count=f.count, message=f.message, details=f.details,
            )
            for f in rows
        ],
    )


@router.get("/export.xlsx")
def export_xlsx(project_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    run = _latest_run(session, project_id)
    if run is None:
        raise HTTPException(404, "Аудит ещё не запускался")
    all_findings = session.scalars(
        select(AuditFinding).where(AuditFinding.run_id == run.id)
        .order_by(func.array_position(["error", "warning", "info"], AuditFinding.severity),
                  AuditFinding.table_name, AuditFinding.code, AuditFinding.hole_id)
    ).all()
    data = build_xlsx(project, run, rule_table(run.counts), list(all_findings))
    return Response(
        data,
        media_type=XLSX_TYPE,
        # ASCII-only name: Chromium drops names given in the RFC 5987 form (filename*).
        headers={"Content-Disposition": f'attachment; filename="audit_{project_id}.xlsx"'},
    )
