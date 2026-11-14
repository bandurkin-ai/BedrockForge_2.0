from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Assay, Collar, Component, ImportJob, Project, Survey

router = APIRouter(prefix="/api/projects", tags=["projects"])

Kind = Literal["collar", "survey", "assay"]


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectOut(BaseModel):
    id: int
    name: str
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


@router.get("", response_model=list[ProjectOut])
def list_projects(session: Session = Depends(get_session)):
    return session.scalars(select(Project).order_by(Project.id)).all()


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectIn, session: Session = Depends(get_session)):
    project = Project(name=body.name.strip())
    session.add(project)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Проект с таким именем уже существует") from None
    return project


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, session: Session = Depends(get_session)) -> None:
    session.delete(get_project(session, project_id))
    session.commit()


@router.get("/{project_id}/summary", response_model=SummaryOut)
def summary(project_id: int, session: Session = Depends(get_session)):
    get_project(session, project_id)

    def count(model) -> int:
        return session.scalar(select(func.count()).select_from(model).where(model.project_id == project_id)) or 0

    components = session.scalars(
        select(Component).where(Component.project_id == project_id).order_by(Component.name)
    ).all()
    jobs = session.scalars(
        select(ImportJob).where(ImportJob.project_id == project_id).order_by(ImportJob.id.desc()).limit(10)
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


@router.get("/{project_id}/tables/{kind}", response_model=TablePage)
def table_page(
    project_id: int,
    kind: Kind,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    get_project(session, project_id)
    model, base_columns, order = _TABLES[kind]
    total = session.scalar(select(func.count()).select_from(model).where(model.project_id == project_id)) or 0
    items = session.scalars(
        select(model).where(model.project_id == project_id).order_by(*order, model.id).limit(limit).offset(offset)
    ).all()

    columns = list(base_columns)
    if kind == "assay":
        columns += [
            c.name for c in session.scalars(
                select(Component).where(Component.project_id == project_id).order_by(Component.name)
            )
        ]
    rows = []
    for item in items:
        row = {c: getattr(item, c) for c in base_columns}
        if kind == "assay":
            row.update(item.analytes)
        rows.append(row)
    return TablePage(total=total, columns=columns, rows=rows)
