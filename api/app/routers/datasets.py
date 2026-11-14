"""The project tree: datasets of every kind, drillhole sets and wireframes."""

from __future__ import annotations

import base64
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.audit.engine import latest_run
from app.config import settings
from app.db import get_session
from app.formats.wireframes import WIREFRAME_SUFFIXES, WireframeFormatError, mesh_stats, read_wireframes
from app.models import Assay, Collar, Component, Dataset, Survey, Wireframe
from app.routers.projects import get_project

router = APIRouter(prefix="/api", tags=["datasets"])

# Distinct colours for wireframes that carry none in the file.
PALETTE = ["#e8873a", "#4f9dde", "#5fbf6a", "#c06fd8", "#e0c040", "#e05d6f", "#46c7c0", "#9a8f7a"]


class WireframeOut(BaseModel):
    id: int
    name: str
    color: str
    point_count: int
    triangle_count: int
    extent: dict
    stats: dict


class DatasetOut(BaseModel):
    id: int
    kind: str
    name: str
    created_at: datetime
    meta: dict
    # drillholes: {"collar": n, "survey": n, "assay": n}
    counts: dict[str, int] | None = None
    components: list[dict[str, str]] | None = None
    audit: dict[str, int] | None = None
    # wireframes
    wireframes: list[WireframeOut] | None = None


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class WireframePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")


def get_dataset(session: Session, dataset_id: int) -> Dataset:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "Объект не найден")
    return dataset


def _wireframe_out(w: Wireframe) -> WireframeOut:
    return WireframeOut(
        id=w.id, name=w.name, color=w.color, point_count=w.point_count,
        triangle_count=w.triangle_count, extent=w.extent, stats=w.stats,
    )


def dataset_out(session: Session, d: Dataset) -> DatasetOut:
    out = DatasetOut(id=d.id, kind=d.kind, name=d.name, created_at=d.created_at, meta=d.meta or {})
    if d.kind == "drillholes":
        def count(model) -> int:
            return session.scalar(select(func.count()).select_from(model).where(model.set_id == d.id)) or 0

        out.counts = {"collar": count(Collar), "survey": count(Survey), "assay": count(Assay)}
        out.components = [
            {"name": c.name, "unit": c.unit}
            for c in session.scalars(select(Component).where(Component.set_id == d.id).order_by(Component.name))
        ]
        run = latest_run(session, d.id)
        out.audit = run.counts["by_severity"] if run else None
    elif d.kind == "wireframes":
        rows = session.scalars(select(Wireframe).where(Wireframe.dataset_id == d.id).order_by(Wireframe.id))
        out.wireframes = [_wireframe_out(w) for w in rows]
    return out


@router.get("/projects/{project_id}/datasets", response_model=list[DatasetOut])
def list_datasets(project_id: int, session: Session = Depends(get_session)):
    """Everything in the project tree, grouped by kind on the client."""
    get_project(session, project_id)
    datasets = session.scalars(select(Dataset).where(Dataset.project_id == project_id).order_by(Dataset.id))
    return [dataset_out(session, d) for d in datasets]


@router.post("/projects/{project_id}/sets", response_model=DatasetOut, status_code=201)
def create_set(project_id: int, body: NameIn, session: Session = Depends(get_session)):
    """An empty drillhole set; fill it through the import wizard."""
    get_project(session, project_id)
    dataset = Dataset(project_id=project_id, kind="drillholes", name=body.name.strip(), meta={})
    session.add(dataset)
    session.commit()
    return dataset_out(session, dataset)


@router.patch("/datasets/{dataset_id}", response_model=DatasetOut)
def rename_dataset(dataset_id: int, body: NameIn, session: Session = Depends(get_session)):
    dataset = get_dataset(session, dataset_id)
    dataset.name = body.name.strip()
    session.commit()
    return dataset_out(session, dataset)


@router.delete("/datasets/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: int, session: Session = Depends(get_session)) -> None:
    """Deletes the object with all its data (holes, assays, audit; or all wireframes of the file)."""
    session.execute(delete(Dataset).where(Dataset.id == get_dataset(session, dataset_id).id))
    session.commit()


# ---- wireframes


@router.post("/projects/{project_id}/wireframes", response_model=DatasetOut, status_code=201)
async def upload_wireframes(project_id: int, file: UploadFile, session: Session = Depends(get_session)):
    """Read a TRIDB or DXF file; every wireframe in it becomes an item of one new dataset."""
    get_project(session, project_id)
    filename = file.filename or "wireframes"
    suffix = Path(filename).suffix.lower()
    if suffix not in WIREFRAME_SUFFIXES:
        raise HTTPException(415, f"Каркасы читаются из файлов {', '.join(WIREFRAME_SUFFIXES)}")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"data{suffix}"
        size = 0
        with path.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, "Файл слишком большой")
                out.write(chunk)
        try:
            fmt, meshes = read_wireframes(path, suffix)
        except WireframeFormatError as exc:
            raise HTTPException(422, str(exc)) from exc

    dataset = Dataset(project_id=project_id, kind="wireframes", name=Path(filename).name[:255], meta={"format": fmt, "filename": filename})
    session.add(dataset)
    session.flush()
    used = session.scalar(
        select(func.count()).select_from(Wireframe).join(Dataset).where(Dataset.project_id == project_id)
    ) or 0
    for i, mesh in enumerate(meshes):
        v = np.ascontiguousarray(mesh.vertices, dtype="<f8")
        t = np.ascontiguousarray(mesh.triangles, dtype="<i4")
        stats = mesh_stats(v, t)
        if mesh.attributes:
            stats["attributes"] = mesh.attributes
        session.add(Wireframe(
            dataset_id=dataset.id,
            name=mesh.name[:255],
            color=mesh.color or PALETTE[(used + i) % len(PALETTE)],
            point_count=len(v),
            triangle_count=len(t),
            extent={"min": np.round(v.min(axis=0), 2).tolist(), "max": np.round(v.max(axis=0), 2).tolist()} if len(v) else {"min": [0, 0, 0], "max": [0, 0, 0]},
            stats=stats,
            vertices=v.tobytes(),
            triangles=t.tobytes(),
        ))
    session.commit()
    return dataset_out(session, dataset)


def _get_wireframe(session: Session, wireframe_id: int) -> Wireframe:
    wireframe = session.get(Wireframe, wireframe_id)
    if wireframe is None:
        raise HTTPException(404, "Каркас не найден")
    return wireframe


@router.get("/wireframes/{wireframe_id}/geometry")
def wireframe_geometry(wireframe_id: int, session: Session = Depends(get_session)) -> dict:
    """Geometry for the 3D view: float32 vertices relative to `origin` (UTM-sized numbers lose
    precision in float32) and uint32 triangle indices, both base64-encoded."""
    w = _get_wireframe(session, wireframe_id)
    vertices = np.frombuffer(w.vertices, dtype="<f8").reshape(-1, 3)
    origin = np.floor(vertices.min(axis=0)) if len(vertices) else np.zeros(3)
    relative = (vertices - origin).astype("<f4")
    triangles = np.frombuffer(w.triangles, dtype="<i4").astype("<u4")
    return {
        "id": w.id,
        "origin": origin.tolist(),
        "vertices": base64.b64encode(relative.tobytes()).decode(),
        "triangles": base64.b64encode(triangles.tobytes()).decode(),
    }


@router.patch("/wireframes/{wireframe_id}", response_model=WireframeOut)
def patch_wireframe(wireframe_id: int, body: WireframePatch, session: Session = Depends(get_session)):
    w = _get_wireframe(session, wireframe_id)
    if body.name is not None:
        w.name = body.name.strip()
    if body.color is not None:
        w.color = body.color.lower()
    session.commit()
    return _wireframe_out(w)


@router.delete("/wireframes/{wireframe_id}", status_code=204)
def delete_wireframe(wireframe_id: int, session: Session = Depends(get_session)) -> None:
    w = _get_wireframe(session, wireframe_id)
    dataset_id = w.dataset_id
    session.delete(w)
    session.flush()
    # The file entry goes away with its last wireframe.
    if not session.scalar(select(func.count()).select_from(Wireframe).where(Wireframe.dataset_id == dataset_id)):
        session.execute(delete(Dataset).where(Dataset.id == dataset_id))
    session.commit()

