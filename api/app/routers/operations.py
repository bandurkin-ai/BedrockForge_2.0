"""Operations on lines and wireframes (jobs), query of a triangle by click, export."""

from __future__ import annotations

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.jobs import queue
from app.models import Dataset, Wireframe
from app.ops.operations import OPERATIONS, OpError, precheck
from app.routers.datasets import clean_folder
from app.routers.projects import get_project
from app.storage.geometry import mesh_arrays

router = APIRouter(prefix="/api", tags=["operations"])


class OperationIn(BaseModel):
    op: str
    params: dict = Field(default_factory=dict)
    # Name of the result; by default made from the sources («A − B», «ЦМР: слой»).
    name: str | None = Field(None, max_length=255)
    # Folder of the result in the tree; by default the folder of the first source.
    folder: str | None = Field(None, max_length=255)


class RayIn(BaseModel):
    # A ray in project coordinates: from the eye through the cursor.
    origin: list[float] = Field(min_length=3, max_length=3)
    direction: list[float] = Field(min_length=3, max_length=3)


class SnapRayIn(RayIn):
    wireframe_ids: list[int] = Field(min_length=1, max_length=500)
    # Only hits closer than this along the ray count (the far side of a section window).
    max_distance: float | None = Field(None, gt=0)


@router.post("/pick")
def pick_any(body: SnapRayIn, session: Session = Depends(get_session)) -> dict:
    """The nearest triangle of several wireframes on a ray: the snapping target of the string
    editor on wireframes (its exact corners give vertex and edge snaps) and the query by click."""
    from sqlalchemy import select

    from app.geo.pick import ray_pick

    if not any(body.direction):
        raise HTTPException(422, "Направление луча не задано")
    best = None
    for w in session.scalars(select(Wireframe).where(Wireframe.id.in_(body.wireframe_ids))):
        v, t = mesh_arrays(w.blobs)
        hit = ray_pick(v, t, body.origin, body.direction, chunks=(w.render or {}).get("chunks") or None, mesh_origin=w.origin)
        if hit and (body.max_distance is None or hit["distance"] <= body.max_distance) and (best is None or hit["distance"] < best["hit"]["distance"]):
            best = {"id": w.id, "name": w.name, "hit": hit}
    return best or {"id": None, "name": None, "hit": None}


@router.get("/operations")
def list_operations() -> list[dict]:
    return [{"op": key, "title": op.title} for key, op in OPERATIONS.items()]


def _enqueue(session: Session, project_id: int, body: OperationIn) -> dict:
    try:
        precheck(session, project_id, body.op, body.params)
    except OpError as exc:
        raise HTTPException(422, str(exc)) from exc
    title = OPERATIONS[body.op].title + (f": {body.name}" if body.name else "")
    job = queue.enqueue(session, "geometry.operation", {
        "project_id": project_id, "op": body.op, "params": body.params, "name": body.name,
        "folder": None if body.folder is None else clean_folder(body.folder),
    }, project_id=project_id, title=title, max_attempts=1)
    session.commit()
    return queue.job_out(job)


@router.post("/projects/{project_id}/operations", status_code=202)
def start_operation(project_id: int, body: OperationIn, session: Session = Depends(get_session)) -> dict:
    """Checks the preconditions (422 says what is wrong and which tool fixes it) and queues the job.
    The result is a new object next to the first source; sources are not changed."""
    get_project(session, project_id)
    return _enqueue(session, project_id, body)


def _wireframe(session: Session, wireframe_id: int) -> tuple[Wireframe, Dataset]:
    w = session.get(Wireframe, wireframe_id)
    if w is None:
        raise HTTPException(404, "Каркас не найден")
    return w, session.get(Dataset, w.dataset_id)


@router.post("/wireframes/{wireframe_id}/rebuild", status_code=202)
def rebuild(wireframe_id: int, session: Session = Depends(get_session)) -> dict:
    """Runs the operation that made this wireframe again, on the present state of its sources
    (e.g. after the contours were edited): tie lines and parameters are taken as they were."""
    w, dataset = _wireframe(session, wireframe_id)
    made = (w.stats or {}).get("operation") or (dataset.meta or {}).get("operation")
    if not made:
        raise HTTPException(422, "Каркас загружен из файла, а не построен операцией: перестраивать нечего")
    return _enqueue(session, dataset.project_id, OperationIn(op=made["op"], params=made.get("params") or {},
                                                             name=made.get("name"), folder=dataset.folder))


@router.post("/wireframes/{wireframe_id}/pick")
def pick_triangle(wireframe_id: int, body: RayIn, session: Session = Depends(get_session)) -> dict:
    """Query by click: the first triangle the ray meets, from the exact float64 data (not the
    display copy) — its corners, area, normal, dip and dip direction."""
    from app.geo.pick import ray_pick

    w, _ = _wireframe(session, wireframe_id)
    if not any(body.direction):
        raise HTTPException(422, "Направление луча не задано")
    v, t = mesh_arrays(w.blobs)
    hit = ray_pick(v, t, body.origin, body.direction, chunks=(w.render or {}).get("chunks") or None, mesh_origin=w.origin)
    return {"id": w.id, "name": w.name, "hit": hit}


@router.get("/wireframes/{wireframe_id}/self-intersections")
def self_intersections(wireframe_id: int, limit: int = 5000, session: Session = Depends(get_session)) -> dict:
    """Triangles taking part in self-intersections, to mark them in the view: corners float64
    relative to the wireframe's origin, flat [x, y, z] * 3 per triangle."""
    from app.geo.repair import self_intersecting_triangles

    w, _ = _wireframe(session, wireframe_id)
    v, t = mesh_arrays(w.blobs)
    ids = self_intersecting_triangles(np.asarray(v), np.asarray(t), limit=max(1, min(limit, 100_000)))
    corners = np.asarray(v)[np.asarray(t)[ids]] - np.asarray(w.origin, dtype=np.float64) if len(ids) else np.zeros((0, 3, 3))
    return {"id": w.id, "origin": w.origin, "triangles": [int(i) for i in ids], "corners": corners.reshape(-1).tolist()}


# ---- export


def _download(path, filename: str, media: str):
    from urllib.parse import quote

    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask

    return FileResponse(path, media_type=media, background=BackgroundTask(lambda: path.unlink(missing_ok=True)),
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}", "Cache-Control": "no-store"})


def _ids(ids: str) -> list[int]:
    try:
        out = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, "ids — номера объектов через запятую") from None
    if not out:
        raise HTTPException(422, "Не выбраны объекты для экспорта")
    return out


@router.get("/export/lines")
def export_lines(ids: str, format: str = "str", session: Session = Depends(get_session)):
    """Line layers as a Micromine STR (binary doubles: coordinates bit-exact) or a DXF file.
    The layer name is the string code / the DXF layer."""
    from sqlalchemy import select

    from app.formats.export import ExportError, write_dxf_lines, write_micromine_str
    from app.formats.lines import LineLayer as FileLayer
    from app.formats.lines import Polyline
    from app.models import LineLayer
    from app.storage import blobs
    from app.storage.geometry import line_arrays

    if format not in ("str", "dxf"):
        raise HTTPException(422, "Формат экспорта линий: str или dxf")
    wanted = _ids(ids)
    rows = {r.id: r for r in session.scalars(select(LineLayer).where(LineLayer.id.in_(wanted)))}
    layers = []
    for layer_id in wanted:
        row = rows.get(layer_id)
        if row is None:
            raise HTTPException(404, f"Слой линий {layer_id} не найден")
        v, o, c = line_arrays(row.blobs)
        attrs = row.line_attributes or []
        lines = [Polyline(np.array(v[o[i]:o[i + 1]], dtype=np.float64), bool(c[i]), dict(attrs[i]) if i < len(attrs) else {})
                 for i in range(len(c))]
        layers.append(FileLayer(row.name, lines, row.color))
    if not any(layer.lines for layer in layers):
        raise HTTPException(422, "В выбранных слоях нет линий")
    path = blobs.tmp_path(f".{format}")
    try:
        (write_micromine_str if format == "str" else write_dxf_lines)(path, layers)
    except ExportError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    name = rows[wanted[0]].name if len(wanted) == 1 else "Линии"
    return _download(path, f"{name}.{'STR' if format == 'str' else 'dxf'}", "application/octet-stream")


@router.get("/export/wireframes")
def export_wireframes(ids: str, format: str = "dxf", session: Session = Depends(get_session)):
    """Wireframes as DXF (polyface meshes) or Wavefront OBJ, coordinates written with full float64 precision."""
    from sqlalchemy import select

    from app.formats.export import ExportError, write_dxf_mesh, write_obj
    from app.storage import blobs

    if format not in ("dxf", "obj"):
        raise HTTPException(422, "Формат экспорта каркасов: dxf или obj")
    wanted = _ids(ids)
    rows = {r.id: r for r in session.scalars(select(Wireframe).where(Wireframe.id.in_(wanted)))}
    meshes = []
    for wid in wanted:
        w = rows.get(wid)
        if w is None:
            raise HTTPException(404, f"Каркас {wid} не найден")
        v, t = mesh_arrays(w.blobs)
        meshes.append((w.name, v, t, w.color))
    path = blobs.tmp_path(f".{format}")
    try:
        (write_dxf_mesh if format == "dxf" else write_obj)(path, meshes)
    except ExportError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    name = rows[wanted[0]].name if len(wanted) == 1 else "Каркасы"
    return _download(path, f"{name}.{format}", "application/octet-stream")


class StitchPreviewIn(BaseModel):
    """Contours (with the editor's unsaved changes) in the order the user picked them."""
    contours: list[list[list[float]]] = Field(min_length=2, max_length=500)
    ties: list[list[list[float]]] = Field(default_factory=list, max_length=2000)
    start_cap: str = "none"
    end_cap: str = "none"


PREVIEW_MAX_POINTS = 200_000


@router.post("/stitch/preview")
def stitch_preview(body: StitchPreviewIn) -> dict:
    """The solid «Собрать из контуров» would make, for the view: the wireframe grows with every
    picked contour; open ends until the user closes them. Nothing is stored."""
    from app.geo.stitch import CAPS, StitchError, stitch_contours

    if sum(len(c) for c in body.contours) > PREVIEW_MAX_POINTS:
        raise HTTPException(422, f"Для предпросмотра слишком много вершин (больше {PREVIEW_MAX_POINTS:,}): создайте каркас операцией «Сшить контуры»".replace(",", " "))
    for cap in (body.start_cap, body.end_cap):
        if cap not in CAPS:
            raise HTTPException(422, f"Неизвестный способ замыкания торца «{cap}»")
    try:
        contours = [np.asarray(c, dtype=np.float64) for c in body.contours]
        ties = [np.asarray(t, dtype=np.float64)[[0, -1]] for t in body.ties if len(t) >= 2]
        r = stitch_contours(contours, ties, start_cap=body.start_cap, end_cap=body.end_cap)
    except (StitchError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    tri = r.triangles
    closed = bool(r.report.get("closed"))
    volume = None
    if closed and len(tri):
        v = r.vertices - r.vertices.mean(axis=0)  # near the origin: no loss of digits
        a, b, c = v[tri[:, 0]], v[tri[:, 1]], v[tri[:, 2]]
        volume = abs(float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0)
    return {
        "corners": r.vertices[tri.reshape(-1)].reshape(-1).tolist(),
        "triangles": int(len(tri)),
        "closed": closed,
        "volume": volume,
        "warnings": list(r.report.get("warnings", [])),
    }
