import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.engine import latest_run
from app.demo import create_demo_project
from app.db import get_session
from app.models import (
    Assay, AssistantChat, AuditFinding, Chart, Collar, Component, Dataset, ImportJob, IntervalTable, Project, Survey, Wireframe,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])
# Drillhole data lives in drillhole sets (datasets of kind "drillholes").
sets_router = APIRouter(prefix="/api/sets", tags=["drillholes"])

Kind = Literal["collar", "survey", "assay"]


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=4000)


class ProjectPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str = ""
    created_at: datetime


class ImportJobOut(BaseModel):
    id: int
    kind: str
    filename: str
    row_count: int
    created_at: datetime


class ComponentOut(BaseModel):
    name: str
    unit: str


class SummaryOut(BaseModel):
    counts: dict[str, int]
    components: list[ComponentOut]
    imports: list[ImportJobOut]


class TablePage(BaseModel):
    total: int
    columns: list[str]
    rows: list[dict]


def get_project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Проект не найден")
    return project


def get_set(session: Session, set_id: int) -> Dataset:
    dataset = session.get(Dataset, set_id)
    if dataset is None or dataset.kind != "drillholes":
        raise HTTPException(404, "Набор скважин не найден")
    return dataset


@router.get("", response_model=list[ProjectOut])
def list_projects(session: Session = Depends(get_session)):
    return session.scalars(select(Project).order_by(Project.id)).all()


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectIn, session: Session = Depends(get_session)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Нужно название проекта")
    project = Project(name=name, description=body.description.strip())
    session.add(project)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Проект с таким именем уже существует") from None
    return project


@router.post("/demo", response_model=ProjectOut)
def demo_project(session: Session = Depends(get_session)):
    """Create the sample project (or return it if it already exists)."""
    project, _ = create_demo_project(session)
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
def patch_project(project_id: int, body: ProjectPatch, session: Session = Depends(get_session)):
    """Rename a project or change its description."""
    project = get_project(session, project_id)
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "Нужно название проекта")
        project.name = name
    if body.description is not None:
        project.description = body.description.strip()
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Проект с таким именем уже существует") from None
    return project


@router.get("/{project_id}/stats")
def project_stats(project_id: int, session: Session = Depends(get_session)) -> dict:
    """What a project holds: objects of every kind, holes and samples, and when it last changed."""
    project = get_project(session, project_id)
    kinds = dict(session.execute(
        select(Dataset.kind, func.count()).where(Dataset.project_id == project_id).group_by(Dataset.kind)
    ).all())
    set_ids = select(Dataset.id).where(Dataset.project_id == project_id, Dataset.kind == "drillholes").scalar_subquery()

    def count(model) -> int:
        return session.scalar(select(func.count()).select_from(model).where(model.set_id.in_(set_ids))) or 0

    wireframes = session.scalar(
        select(func.count()).select_from(Wireframe).join(Dataset, Wireframe.dataset_id == Dataset.id).where(Dataset.project_id == project_id)
    ) or 0
    interval_rows = session.scalar(
        select(func.coalesce(func.sum(IntervalTable.row_count), 0)).where(IntervalTable.set_id.in_(set_ids))
    ) or 0
    charts = session.scalar(
        select(func.count()).select_from(Chart).join(Dataset, Chart.dataset_id == Dataset.id).where(Dataset.project_id == project_id)
    ) or 0
    chats = session.scalar(select(func.count()).select_from(AssistantChat).where(AssistantChat.project_id == project_id)) or 0
    stamps = [
        project.created_at,
        session.scalar(select(func.max(Dataset.created_at)).where(Dataset.project_id == project_id)),
        session.scalar(select(func.max(ImportJob.created_at)).where(ImportJob.set_id.in_(set_ids))),
        session.scalar(select(func.max(AssistantChat.updated_at)).where(AssistantChat.project_id == project_id)),
    ]
    return {
        "id": project.id,
        "datasets": {k: int(kinds.get(k, 0)) for k in ("drillholes", "wireframes", "lines", "rasters", "blocks")},
        "holes": count(Collar), "surveys": count(Survey), "assays": count(Assay), "interval_rows": int(interval_rows),
        "wireframes": int(wireframes), "charts": int(charts), "chats": int(chats),
        "updated_at": max(s for s in stamps if s is not None).isoformat(),
    }


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, session: Session = Depends(get_session)) -> None:
    session.delete(get_project(session, project_id))
    session.commit()


@sets_router.get("/{set_id}/summary", response_model=SummaryOut)
def summary(set_id: int, session: Session = Depends(get_session)):
    get_set(session, set_id)

    def count(model) -> int:
        return session.scalar(select(func.count()).select_from(model).where(model.set_id == set_id)) or 0

    components = session.scalars(
        select(Component).where(Component.set_id == set_id).order_by(Component.name)
    ).all()
    jobs = session.scalars(
        select(ImportJob).where(ImportJob.set_id == set_id).order_by(ImportJob.id.desc()).limit(10)
    ).all()
    return SummaryOut(
        counts={"collar": count(Collar), "survey": count(Survey), "assay": count(Assay)},
        components=[ComponentOut(name=c.name, unit=c.unit) for c in components],
        imports=[
            ImportJobOut(id=j.id, kind=j.kind, filename=j.filename, row_count=j.row_count, created_at=j.created_at)
            for j in jobs
        ],
    )


_TABLES = {
    "collar": (Collar, ["hole_id", "x", "y", "z", "td", "azimuth", "dip", "date", "status", "stage"],
               (Collar.hole_id,)),
    "survey": (Survey, ["hole_id", "depth", "azimuth", "dip"], (Survey.hole_id, Survey.depth)),
    "assay": (Assay, ["hole_id", "from_m", "to_m", "sample_id"], (Assay.hole_id, Assay.from_m)),
}


@sets_router.get("/{set_id}/tables/{kind}", response_model=TablePage)
def table_page(
    set_id: int,
    kind: Kind,
    hole_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    get_set(session, set_id)
    model, base_columns, order = _TABLES[kind]
    conditions = [model.set_id == set_id]
    if hole_id:
        conditions.append(model.hole_id == hole_id)
    total = session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0
    items = session.scalars(
        select(model).where(*conditions).order_by(*order, model.id).limit(limit).offset(offset)
    ).all()

    columns = list(base_columns)
    if kind == "assay":
        columns += [
            c.name for c in session.scalars(
                select(Component).where(Component.set_id == set_id).order_by(Component.name)
            )
        ]
    rows = []
    for item in items:
        row = {c: getattr(item, c) for c in base_columns}
        if kind == "assay":
            row.update(item.analytes)
        rows.append(row)
    return TablePage(total=total, columns=columns, rows=rows)


class HoleOut(BaseModel):
    hole_id: str
    x: float | None
    y: float | None
    z: float | None
    td: float | None
    status: str | None
    survey_count: int
    assay_count: int
    # Number of audit findings for this hole by severity (zeros before the first audit).
    error: int
    warning: int
    info: int


class HoleFinding(BaseModel):
    code: str
    severity: str
    count: int
    message: str


class HoleDetail(HoleOut):
    azimuth: float | None
    dip: float | None
    date: str | None
    stage: str | None
    attributes: dict
    findings: list[HoleFinding]


def _natural_key(value: str) -> list:
    """'H-2' sorts before 'H-10'."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def _counts_by_hole(session: Session, model, set_id: int) -> dict[str, int]:
    rows = session.execute(
        select(model.hole_id, func.count()).where(model.set_id == set_id).group_by(model.hole_id)
    )
    return {hole: n for hole, n in rows}


def _finding_counts(session: Session, set_id: int) -> dict[str, dict[str, int]]:
    run = latest_run(session, set_id)
    result: dict[str, dict[str, int]] = {}
    if run is None:
        return result
    rows = session.execute(
        select(AuditFinding.hole_id, AuditFinding.severity, func.count())
        .where(AuditFinding.run_id == run.id, AuditFinding.hole_id.is_not(None))
        .group_by(AuditFinding.hole_id, AuditFinding.severity)
    )
    for hole, severity, n in rows:
        result.setdefault(hole, {})[severity] = n
    return result


def _hole_out(c: Collar, surveys: dict, assays: dict, findings: dict) -> dict:
    found = findings.get(c.hole_id, {})
    return {
        "hole_id": c.hole_id, "x": c.x, "y": c.y, "z": c.z, "td": c.td, "status": c.status,
        "survey_count": surveys.get(c.hole_id, 0), "assay_count": assays.get(c.hole_id, 0),
        "error": found.get("error", 0), "warning": found.get("warning", 0), "info": found.get("info", 0),
    }


@sets_router.get("/{set_id}/holes", response_model=list[HoleOut])
def list_holes(set_id: int, session: Session = Depends(get_session)):
    """Collars with data counts and audit findings, for the hole navigator."""
    get_set(session, set_id)
    collars = session.scalars(select(Collar).where(Collar.set_id == set_id)).all()
    surveys = _counts_by_hole(session, Survey, set_id)
    assays = _counts_by_hole(session, Assay, set_id)
    findings = _finding_counts(session, set_id)
    collars = sorted(collars, key=lambda c: _natural_key(c.hole_id))
    return [_hole_out(c, surveys, assays, findings) for c in collars]


@sets_router.get("/{set_id}/holes/{hole_id}", response_model=HoleDetail)
def hole_detail(set_id: int, hole_id: str, session: Session = Depends(get_session)):
    get_set(session, set_id)
    collar = session.scalars(
        select(Collar).where(Collar.set_id == set_id, Collar.hole_id == hole_id)
    ).first()
    if collar is None:
        raise HTTPException(404, "Скважина не найдена")

    surveys = _counts_by_hole(session, Survey, set_id)
    assays = _counts_by_hole(session, Assay, set_id)
    findings = _finding_counts(session, set_id)
    found: list[HoleFinding] = []
    run = latest_run(session, set_id)
    if run is not None:
        rows = session.scalars(
            select(AuditFinding)
            .where(AuditFinding.run_id == run.id, AuditFinding.hole_id == hole_id)
            .order_by(func.array_position(["error", "warning", "info"], AuditFinding.severity), AuditFinding.code)
        )
        found = [HoleFinding(code=f.code, severity=f.severity, count=f.count, message=f.message) for f in rows]
    return HoleDetail(
        **_hole_out(collar, surveys, assays, findings),
        azimuth=collar.azimuth, dip=collar.dip, date=collar.date, stage=collar.stage,
        attributes=collar.attributes, findings=found,
    )
