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
from app.routers.projects import get_set

router = APIRouter(prefix="/api/sets/{set_id}", tags=["visualization"])

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


def _trajectories(session: Session, set_id: int, hole_id: str | None = None) -> tuple[dict[str, Trajectory], list[str]]:
    collar_query = select(Collar).where(Collar.set_id == set_id)
    survey_query = select(Survey.hole_id, Survey.depth, Survey.azimuth, Survey.dip).where(Survey.set_id == set_id)
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
    set_id: int,
    step: float = Query(10.0, ge=1.0, le=100.0, description="Maximum distance between path points, m"),
    session: Session = Depends(get_session),
):
    get_set(session, set_id)
    trajs, skipped = _trajectories(session, set_id)
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
def intervals(set_id: int, component: str, session: Session = Depends(get_session)):
    """Assay intervals of one component with their 3D end points on the trajectories."""
    get_set(session, set_id)
    unit = session.scalar(
        select(Component.unit).where(Component.set_id == set_id, Component.name == component)
    )
    if unit is None:
        raise HTTPException(404, f"Компонент «{component}» не найден в наборе")

    trajs, _ = _trajectories(session, set_id)
    by_hole: dict[str, list[tuple[float, float, float]]] = {}
    rows = session.execute(
        select(Assay.hole_id, Assay.from_m, Assay.to_m, Assay.analytes[component].as_float())
        .where(Assay.set_id == set_id, Assay.analytes.has_key(component))
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
def hole_log(set_id: int, hole_id: str, session: Session = Depends(get_session)):
    """Everything the hole log needs: survey stations and all intervals with every component."""
    get_set(session, set_id)
    collar = session.scalars(
        select(Collar).where(Collar.set_id == set_id, Collar.hole_id == hole_id)
    ).first()
    if collar is None:
        raise HTTPException(404, "Скважина не найдена")

    survey = session.execute(
        select(Survey.depth, Survey.azimuth, Survey.dip)
        .where(Survey.set_id == set_id, Survey.hole_id == hole_id)
        .order_by(Survey.depth, Survey.id)
    ).all()
    assays = session.scalars(
        select(Assay).where(Assay.set_id == set_id, Assay.hole_id == hole_id)
        .order_by(Assay.from_m, Assay.id).limit(_MAX_LOG_INTERVALS + 1)
    ).all()
    components = session.execute(
        select(Component.name, Component.unit).where(Component.set_id == set_id).order_by(Component.name)
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


@router.get("/snap")
def snap_points(set_id: int, step: float = Query(10.0, ge=1.0, le=100.0), session: Session = Depends(get_session)):
    """Snapping targets of the string editor along the holes of a set, exact (float64, not rounded):
    collars, points of the trajectories and the boundaries of every interval (assays and interval
    tables) placed on the trajectories.

    Binary: uint32 length of a JSON header, the header, padding to 8 bytes, then the arrays the
    header lists in `arrays` ([name, dtype, count of values]) one after another, little-endian.
    """
    import json

    from fastapi.responses import Response

    from app.models import IntervalRow, IntervalTable

    get_set(session, set_id)
    trajs, _ = _trajectories(session, set_id)
    names = list(trajs)
    index = {name: i for i, name in enumerate(names)}
    depths: dict[str, list[float]] = {}
    for query in (
        select(Assay.hole_id, Assay.from_m, Assay.to_m).where(Assay.set_id == set_id),
        select(IntervalRow.hole_id, IntervalRow.from_m, IntervalRow.to_m).join(IntervalTable, IntervalTable.id == IntervalRow.table_id)
        .where(IntervalTable.set_id == set_id),
    ):
        for hole, from_m, to_m in session.execute(query):
            if hole in index:
                bucket = depths.setdefault(hole, [])
                if from_m is not None:
                    bucket.append(from_m)
                if to_m is not None:
                    bucket.append(to_m)
    collars = np.zeros((len(names), 3))
    trace, trace_offsets = [], [0]
    bounds, bound_hole, bound_depth = [], [], []
    for i, name in enumerate(names):
        traj = trajs[name]
        path = sample_path(traj, step)[:, :3]
        collars[i] = path[0]
        trace.append(path)
        trace_offsets.append(trace_offsets[-1] + len(path))
        d = np.unique(np.asarray(depths.get(name, []), dtype=np.float64))
        d = d[np.isfinite(d) & (d >= 0) & (d <= float(traj.md[-1]) + 1e-6)]
        if len(d):
            bounds.append(traj.positions(d))
            bound_hole.append(np.full(len(d), i, dtype="<u4"))
            bound_depth.append(d.astype("<f4"))
    arrays = [
        ("collars", "<f8", collars),
        ("trace", "<f8", np.vstack(trace) if trace else np.zeros((0, 3))),
        ("trace_offsets", "<u4", np.asarray(trace_offsets, dtype="<u4")),
        ("bounds", "<f8", np.vstack(bounds) if bounds else np.zeros((0, 3))),
        ("bound_hole", "<u4", np.concatenate(bound_hole) if bound_hole else np.zeros(0, dtype="<u4")),
        ("bound_depth", "<f4", np.concatenate(bound_depth) if bound_depth else np.zeros(0, dtype="<f4")),
    ]
    header = json.dumps({"holes": names, "arrays": [[n, dt, int(np.asarray(a).size)] for n, dt, a in arrays]},
                        ensure_ascii=False).encode()
    body = bytearray(len(header).to_bytes(4, "little") + header)
    body += b"\x00" * (-len(body) % 8)
    for _, dtype, a in arrays:
        body += np.ascontiguousarray(a, dtype=dtype).tobytes()
        body += b"\x00" * (-len(body) % 8)
    return Response(bytes(body), media_type="application/octet-stream", headers={"Cache-Control": "no-store"})


class IntersectionObject(BaseModel):
    kind: str  # "wireframe" | "blocks"
    id: int  # wireframe id or block model dataset id
    dataset_id: int
    name: str


class IntersectionHole(BaseModel):
    hole_id: str
    # [depth, x, y, z, object index, event] — event 0: crossing a wireframe, 1: into a block model, 2: out of it
    points: list[list[float]]


class IntersectionsOut(BaseModel):
    objects: list[IntersectionObject]
    holes: list[IntersectionHole]
    # objects or holes left out (too large a block model for the remaining budget and so on)
    notes: list[str]


@router.get("/intersections", response_model=IntersectionsOut)
def intersections(
    set_id: int,
    wireframes: bool = True,
    blocks: bool = True,
    session: Session = Depends(get_session),
):
    """Where the holes of a set cross the wireframes of the project and enter or leave its block models."""
    from app.geo.intersect import BlockTarget, MeshTarget, hole_segments, new_budget
    from app.models import Dataset, Wireframe
    from app.storage import blobs
    from app.storage.geometry import mesh_arrays

    s = get_set(session, set_id)
    trajs, _ = _trajectories(session, set_id)
    paths = {h: hole_segments(t) for h, t in trajs.items()}
    objects: list[IntersectionObject] = []
    found: dict[str, list[list[float]]] = {}
    notes: list[str] = []

    def add(hole: str, obj: int, hits) -> None:
        for h in hits:
            found.setdefault(hole, []).append([round(h.depth, 3), round(h.xyz[0], 3), round(h.xyz[1], 3), round(h.xyz[2], 3),
                                               obj, 0 if h.event == "cross" else 1 if h.event == "in" else 2])

    datasets = list(session.scalars(select(Dataset).where(Dataset.project_id == s.project_id)))
    if wireframes:
        ids = [d.id for d in datasets if d.kind == "wireframes"]
        for w in session.scalars(select(Wireframe).where(Wireframe.dataset_id.in_(ids)).order_by(Wireframe.id)) if ids else []:
            try:
                target = MeshTarget(*mesh_arrays(w.blobs))
            except Exception as exc:  # a missing blob: the other objects still count
                notes.append(f"Каркас «{w.name}» не прочитан: {exc}")
                continue
            obj = len(objects)
            objects.append(IntersectionObject(kind="wireframe", id=w.id, dataset_id=w.dataset_id, name=w.name))
            for hole, (points, md) in paths.items():
                add(hole, obj, target.hits(points, md))
    if blocks:
        budget = new_budget()
        for d in (d for d in datasets if d.kind == "blocks"):
            grid = (d.meta or {}).get("grid") or {}
            if "centres" not in d.blobs or "sizes" not in d.blobs or not grid.get("parent"):
                continue
            target = BlockTarget(blobs.read_array(d.blobs["centres"], "<f8", 3), blobs.read_array(d.blobs["sizes"], "<f4", 3),
                                 grid["origin"], grid["parent"], grid["counts"])
            obj = len(objects)
            objects.append(IntersectionObject(kind="blocks", id=d.id, dataset_id=d.id, name=d.name))
            skipped = budget[1]
            for hole, traj in trajs.items():
                add(hole, obj, target.hits(traj, budget))
            if budget[1] > skipped:
                notes.append(f"Блочная модель «{d.name}»: слишком мелкие блоки, у скважин ({budget[1] - skipped}) пересечения не искались")
    holes = [IntersectionHole(hole_id=h, points=sorted(p)) for h, p in found.items()]
    return IntersectionsOut(objects=objects, holes=holes, notes=notes)
