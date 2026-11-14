"""Data for the plan, section, hole log and 3D views."""

from __future__ import annotations

import math

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.geo.trajectory import Trajectory, build_trajectory, sample_path
from app.models import Assay, Collar, Component, Survey
from app.routers.projects import get_project

router = APIRouter(prefix="/api/projects/{project_id}", tags=["visualization"])

_MAX_LOG_INTERVALS = 5000


class TrajectoryHole(BaseModel):
    hole_id: str
    td: float | None
    source: str
    # Polyline points [x, y, z, measured depth], rounded to a centimetre.
    path: list[list[float]]


class TrajectoriesOut(BaseModel):
    holes: list[TrajectoryHole]
    # Holes without usable X/Y/Z cannot be drawn.
    skipped: list[str]
    extent: dict[str, list[float]] | None


class ValueStats(BaseModel):
    count: int
    min: float | None
    max: float | None
    p05: float | None
    p50: float | None
    p95: float | None


class IntervalsOut(BaseModel):
    component: str
    unit: str | None
    stats: ValueStats
    # hole_id -> [[from, to, value, x1, y1, z1, x2, y2, z2], ...] for intervals with a value.
    holes: dict[str, list[list[float]]]


class LogInterval(BaseModel):
    from_m: float | None
    to_m: float | None
    sample_id: str | None
    values: dict[str, float | None]


class LogStation(BaseModel):
    depth: float | None
    azimuth: float | None
    dip: float | None


class HoleLog(BaseModel):
    hole_id: str
    td: float | None
    components: list[dict[str, str]]
    survey: list[LogStation]
    intervals: list[LogInterval]
    truncated: bool


def _trajectories(session: Session, project_id: int, hole_id: str | None = None) -> tuple[dict[str, Trajectory], list[str]]:
    collar_query = select(Collar).where(Collar.project_id == project_id)
    survey_query = select(Survey.hole_id, Survey.depth, Survey.azimuth, Survey.dip).where(Survey.project_id == project_id)
    if hole_id:
        collar_query = collar_query.where(Collar.hole_id == hole_id)
        survey_query = survey_query.where(Survey.hole_id == hole_id)

    stations: dict[str, list] = {}
    for hole, depth, azimuth, dip in session.execute(survey_query):
        stations.setdefault(hole, []).append((depth, azimuth, dip))

    result: dict[str, Trajectory] = {}
    skipped: list[str] = []
    for c in session.scalars(collar_query.order_by(Collar.hole_id)):
        traj = build_trajectory(c.hole_id, c.x, c.y, c.z, c.td, c.azimuth, c.dip, stations.get(c.hole_id, []))
        if traj is None:
            skipped.append(c.hole_id)
        else:
            result[c.hole_id] = traj
    return result, skipped


def _round(values: np.ndarray) -> list:
    return np.round(values, 2).tolist()


@router.get("/trajectories", response_model=TrajectoriesOut)
def trajectories(
    project_id: int,
    step: float = Query(10.0, ge=1.0, le=100.0, description="Maximum distance between path points, m"),
    session: Session = Depends(get_session),
):
    get_project(session, project_id)
    trajs, skipped = _trajectories(session, project_id)
    holes = []
    lows, highs = [], []
    for hole_id, traj in trajs.items():
        path = sample_path(traj, step)
        lows.append(path[:, :3].min(axis=0))
        highs.append(path[:, :3].max(axis=0))
        collar_td = float(traj.md[-1])
        holes.append(TrajectoryHole(hole_id=hole_id, td=collar_td, source=traj.source, path=_round(path)))
    extent = {"min": _round(np.min(lows, axis=0)), "max": _round(np.max(highs, axis=0))} if holes else None
    return TrajectoriesOut(holes=holes, skipped=skipped, extent=extent)


def _stats(values: np.ndarray) -> ValueStats:
    positive = values[np.isfinite(values) & (values > 0)]
    if len(values) == 0:
        return ValueStats(count=0, min=None, max=None, p05=None, p50=None, p95=None)
    finite = values[np.isfinite(values)]
    p05, p50, p95 = (float(v) for v in np.percentile(positive, [5, 50, 95])) if len(positive) else (None, None, None)
    return ValueStats(
        count=int(len(finite)),
        min=float(finite.min()) if len(finite) else None,
        max=float(finite.max()) if len(finite) else None,
        p05=p05, p50=p50, p95=p95,
    )


@router.get("/intervals", response_model=IntervalsOut)
def intervals(project_id: int, component: str, session: Session = Depends(get_session)):
    """Assay intervals of one component with their 3D end points on the trajectories."""
    get_project(session, project_id)
    unit = session.scalar(
        select(Component.unit).where(Component.project_id == project_id, Component.name == component)
    )
    if unit is None:
        raise HTTPException(404, f"Компонент «{component}» не найден в проекте")

    trajs, _ = _trajectories(session, project_id)
    by_hole: dict[str, list[tuple[float, float, float]]] = {}
    rows = session.execute(
        select(Assay.hole_id, Assay.from_m, Assay.to_m, Assay.analytes[component].as_float())
        .where(Assay.project_id == project_id, Assay.analytes.has_key(component))
    )
    for hole, from_m, to_m, value in rows:
        if hole in trajs and None not in (from_m, to_m, value) and to_m > from_m and math.isfinite(value):
            by_hole.setdefault(hole, []).append((from_m, to_m, value))

    holes: dict[str, list[list[float]]] = {}
    all_values: list[float] = []
    for hole, items in by_hole.items():
        arr = np.array(items)
        order = np.argsort(arr[:, 0], kind="stable")
        arr = arr[order]
        start = trajs[hole].positions(arr[:, 0])
        end = trajs[hole].positions(arr[:, 1])
        # Positions to a centimetre; grades stay at full precision, they span orders of magnitude.
        table = np.column_stack([arr, np.round(start, 2), np.round(end, 2)])
        holes[hole] = table.tolist()
        all_values.extend(arr[:, 2].tolist())
    return IntervalsOut(component=component, unit=unit, stats=_stats(np.array(all_values)), holes=holes)


@router.get("/holes/{hole_id}/log", response_model=HoleLog)
def hole_log(project_id: int, hole_id: str, session: Session = Depends(get_session)):
    """Everything the hole log needs: survey stations and all intervals with every component."""
    get_project(session, project_id)
    collar = session.scalars(
        select(Collar).where(Collar.project_id == project_id, Collar.hole_id == hole_id)
    ).first()
    if collar is None:
        raise HTTPException(404, "Скважина не найдена")

    survey = session.execute(
        select(Survey.depth, Survey.azimuth, Survey.dip)
        .where(Survey.project_id == project_id, Survey.hole_id == hole_id)
        .order_by(Survey.depth, Survey.id)
    ).all()
    assays = session.scalars(
        select(Assay).where(Assay.project_id == project_id, Assay.hole_id == hole_id)
        .order_by(Assay.from_m, Assay.id).limit(_MAX_LOG_INTERVALS + 1)
    ).all()
    components = session.execute(
        select(Component.name, Component.unit).where(Component.project_id == project_id).order_by(Component.name)
    ).all()
    truncated = len(assays) > _MAX_LOG_INTERVALS
    return HoleLog(
        hole_id=hole_id,
        td=collar.td,
        components=[{"name": n, "unit": u} for n, u in components],
        survey=[LogStation(depth=d, azimuth=a, dip=p) for d, a, p in survey],
        intervals=[
            LogInterval(from_m=a.from_m, to_m=a.to_m, sample_id=a.sample_id, values=a.analytes)
            for a in assays[:_MAX_LOG_INTERVALS]
        ],
        truncated=truncated,
    )
