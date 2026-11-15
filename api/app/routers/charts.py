"""Saved charts of drillhole sets: shown in the project tree, opened again with their settings.

    GET    /api/sets/{id}/charts
    POST   /api/sets/{id}/charts        {kind, name, settings}
    PATCH  /api/charts/{id}             {name} renames (the name stops following the settings),
                                        {settings, auto_name} updates what the window shows
    DELETE /api/charts/{id}
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Chart
from app.routers.projects import get_set

router = APIRouter(prefix="/api", tags=["charts"])

ChartKind = Literal["histogram", "qq", "dendrogram", "striplog"]


class ChartIn(BaseModel):
    kind: ChartKind
    name: str = Field(min_length=1, max_length=255)
    settings: dict = Field(default_factory=dict)


class ChartPatch(BaseModel):
    # A name typed by the user.
    name: str | None = Field(None, min_length=1, max_length=255)
    settings: dict | None = None
    # The name made from the settings; ignored once the user renamed the chart.
    auto_name: str | None = Field(None, min_length=1, max_length=255)


class ChartOut(BaseModel):
    id: int
    set_id: int
    kind: str
    name: str
    auto_name: bool
    settings: dict
    created_at: datetime
    updated_at: datetime


def chart_out(c: Chart) -> ChartOut:
    return ChartOut(id=c.id, set_id=c.dataset_id, kind=c.kind, name=c.name, auto_name=c.auto_name,
                    settings=c.settings or {}, created_at=c.created_at, updated_at=c.updated_at)


def _get(session: Session, chart_id: int) -> Chart:
    c = session.get(Chart, chart_id)
    if c is None:
        raise HTTPException(404, "График не найден")
    return c


@router.get("/sets/{set_id}/charts", response_model=list[ChartOut])
def list_charts(set_id: int, session: Session = Depends(get_session)):
    get_set(session, set_id)
    return [chart_out(c) for c in session.scalars(select(Chart).where(Chart.dataset_id == set_id).order_by(Chart.id))]


@router.post("/sets/{set_id}/charts", response_model=ChartOut, status_code=201)
def create_chart(set_id: int, body: ChartIn, session: Session = Depends(get_session)):
    get_set(session, set_id)
    c = Chart(dataset_id=set_id, kind=body.kind, name=body.name.strip(), auto_name=True, settings=body.settings)
    session.add(c)
    session.commit()
    return chart_out(c)


@router.get("/charts/{chart_id}", response_model=ChartOut)
def read_chart(chart_id: int, session: Session = Depends(get_session)):
    return chart_out(_get(session, chart_id))


@router.patch("/charts/{chart_id}", response_model=ChartOut)
def patch_chart(chart_id: int, body: ChartPatch, session: Session = Depends(get_session)):
    c = _get(session, chart_id)
    if body.settings is not None:
        c.settings = body.settings
    if body.name is not None:
        c.name = body.name.strip()
        c.auto_name = False
    elif body.auto_name is not None and c.auto_name:
        c.name = body.auto_name.strip()
    session.commit()
    return chart_out(c)


@router.delete("/charts/{chart_id}", status_code=204)
def delete_chart(chart_id: int, session: Session = Depends(get_session)) -> None:
    session.delete(_get(session, chart_id))
    session.commit()
