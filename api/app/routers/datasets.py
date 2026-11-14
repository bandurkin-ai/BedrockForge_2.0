"""The project tree: datasets of every kind, drillhole sets and wireframes."""

from __future__ import annotations

import base64
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.audit.engine import latest_run
from app.config import settings
from app.db import get_session
from app.formats.lines import LINE_SUFFIXES, LineFormatError, line_stats, pack, read_lines
from app.formats.rasters import (
    IMAGE_SUFFIXES, SIDECAR_SUFFIXES, Georef, RasterFormatError, corners, corners3d, is_vertical, pair_files, pixel_size,
    plane_azimuth, read_raster, rotation_deg, vertical_georef,
)
from app.formats.wireframes import WIREFRAME_SUFFIXES, WireframeFormatError, mesh_stats, read_wireframes
from app.models import Assay, Collar, Component, Dataset, LineLayer, Raster, Survey, Wireframe
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


class LineLayerOut(BaseModel):
    id: int
    name: str
    color: str
    line_count: int
    point_count: int
    extent: dict
    stats: dict


class RasterOut(BaseModel):
    id: int
    name: str
    width: int
    height: int
    georef: dict
    extent: dict
    elevation: float | None
    opacity: float


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
    lines: list[LineLayerOut] | None = None
    rasters: list[RasterOut] | None = None


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class LinePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")


class VerticalPlacement(BaseModel):
    """A raster standing in a vertical plane: left edge (x1, y1), right edge (x2, y2), from z_top down to z_bottom."""

    x1: float
    y1: float
    x2: float
    y2: float
    z_top: float
    z_bottom: float


class RasterPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    # Re-place the image in a vertical plane (e.g. on the current section line).
    vertical: VerticalPlacement | None = None
    # None clears it: the raster goes under the lowest object of the scene.
    elevation: float | None = None
    opacity: float | None = Field(None, ge=0.05, le=1)
    clear_elevation: bool = False


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
    elif d.kind == "lines":
        rows = session.scalars(select(LineLayer).where(LineLayer.dataset_id == d.id).order_by(LineLayer.id))
        out.lines = [_line_out(x) for x in rows]
    elif d.kind == "rasters":
        rows = session.scalars(select(Raster).where(Raster.dataset_id == d.id).order_by(Raster.id))
        out.rasters = [_raster_out(r) for r in rows]
    return out


def _line_out(x: LineLayer) -> LineLayerOut:
    return LineLayerOut(id=x.id, name=x.name, color=x.color, line_count=x.line_count, point_count=x.point_count, extent=x.extent, stats=x.stats)


def _raster_out(r: Raster) -> RasterOut:
    return RasterOut(id=r.id, name=r.name, width=r.width, height=r.height, georef=r.georef, extent=r.extent, elevation=r.elevation, opacity=r.opacity)


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



# ---- shared upload helpers


async def _save_upload(file: UploadFile, path: Path) -> int:
    size = 0
    with path.open("wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                raise HTTPException(413, f"Файл «{file.filename}» слишком большой")
            out.write(chunk)
    return size


def _used_colors(session: Session, project_id: int) -> int:
    lines = session.scalar(select(func.count()).select_from(LineLayer).join(Dataset).where(Dataset.project_id == project_id)) or 0
    meshes = session.scalar(select(func.count()).select_from(Wireframe).join(Dataset).where(Dataset.project_id == project_id)) or 0
    return lines + meshes


# ---- lines


@router.post("/projects/{project_id}/lines", response_model=DatasetOut, status_code=201)
async def upload_lines(project_id: int, file: UploadFile, session: Session = Depends(get_session)):
    """Read a Micromine / Surpac STR or a DXF file; every layer (string code, DXF layer) becomes an item."""
    get_project(session, project_id)
    filename = file.filename or "lines"
    suffix = Path(filename).suffix.lower()
    if suffix not in LINE_SUFFIXES:
        raise HTTPException(415, f"Линии читаются из файлов {', '.join(LINE_SUFFIXES)}")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"data{suffix}"
        await _save_upload(file, path)
        try:
            fmt, layers = read_lines(path, suffix)
        except LineFormatError as exc:
            raise HTTPException(422, str(exc)) from exc

    dataset = Dataset(project_id=project_id, kind="lines", name=Path(filename).name[:255], meta={"format": fmt, "filename": filename})
    session.add(dataset)
    session.flush()
    used = _used_colors(session, project_id)
    for i, layer in enumerate(layers):
        vertices, offsets, closed = pack(layer.lines)
        session.add(LineLayer(
            dataset_id=dataset.id,
            name=layer.name[:255],
            color=layer.color or PALETTE[(used + i) % len(PALETTE)],
            line_count=len(layer.lines),
            point_count=len(vertices),
            extent={"min": np.round(vertices.min(axis=0), 2).tolist(), "max": np.round(vertices.max(axis=0), 2).tolist()},
            stats=line_stats(layer.lines),
            line_attributes=[_jsonable(line.attributes) for line in layer.lines],
            vertices=np.ascontiguousarray(vertices, dtype="<f8").tobytes(),
            offsets=offsets.astype("<i8").tobytes(),
            closed=closed.tobytes(),
        ))
    session.commit()
    return dataset_out(session, dataset)


def _jsonable(attributes: dict) -> dict:
    out = {}
    for k, v in attributes.items():
        if isinstance(v, float) and not np.isfinite(v):
            v = None
        out[str(k)] = v if isinstance(v, (str, int, float, bool)) or v is None else str(v)
    return out


def _get_line_layer(session: Session, layer_id: int) -> LineLayer:
    layer = session.get(LineLayer, layer_id)
    if layer is None:
        raise HTTPException(404, "Слой линий не найден")
    return layer


@router.get("/lines/{layer_id}/geometry")
def line_geometry(layer_id: int, session: Session = Depends(get_session)) -> dict:
    """float32 vertices relative to `origin`, uint32 line starts (lines + 1) and closed flags, base64."""
    layer = _get_line_layer(session, layer_id)
    vertices = np.frombuffer(layer.vertices, dtype="<f8").reshape(-1, 3)
    origin = np.floor(vertices.min(axis=0)) if len(vertices) else np.zeros(3)
    return {
        "id": layer.id,
        "origin": origin.tolist(),
        "vertices": base64.b64encode((vertices - origin).astype("<f4").tobytes()).decode(),
        "offsets": base64.b64encode(np.frombuffer(layer.offsets, dtype="<i8").astype("<u4").tobytes()).decode(),
        "closed": base64.b64encode(layer.closed).decode(),
    }


@router.patch("/lines/{layer_id}", response_model=LineLayerOut)
def patch_lines(layer_id: int, body: LinePatch, session: Session = Depends(get_session)):
    layer = _get_line_layer(session, layer_id)
    if body.name is not None:
        layer.name = body.name.strip()
    if body.color is not None:
        layer.color = body.color.lower()
    session.commit()
    return _line_out(layer)


@router.delete("/lines/{layer_id}", status_code=204)
def delete_lines(layer_id: int, session: Session = Depends(get_session)) -> None:
    layer = _get_line_layer(session, layer_id)
    dataset_id = layer.dataset_id
    session.delete(layer)
    session.flush()
    if not session.scalar(select(func.count()).select_from(LineLayer).where(LineLayer.dataset_id == dataset_id)):
        session.execute(delete(Dataset).where(Dataset.id == dataset_id))
    session.commit()


# ---- rasters


def raster_placement(g: Georef, width: int, height: int, checks: list, notes: list) -> tuple[dict, dict]:
    """The stored georeference (plan or vertical) and the plan extent of a raster."""
    pts = corners(g.affine, width, height)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    vertical = is_vertical(g, width, height)
    georef = {
        "affine": g.affine, "affine_z": g.affine_z if vertical else None, "source": g.source, "crs": g.crs,
        "placement": "vertical" if vertical else "plan",
        "pixel_size": round(pixel_size(g.affine, g.affine_z if vertical else None), 4),
        "rotation": round(rotation_deg(g.affine), 4),
        "corners": [[round(x, 3), round(y, 3)] for x, y in pts],
        "checks": checks, "notes": notes,
    }
    extent = {"min": [round(min(xs), 2), round(min(ys), 2)], "max": [round(max(xs), 2), round(max(ys), 2)]}
    if vertical:
        c3 = corners3d(g, width, height)
        zs = [p[2] for p in c3]
        georef["corners3d"] = [[round(v, 3) for v in p] for p in c3]
        georef["azimuth"] = round(plane_azimuth(g.affine), 3)
        extent = {"min": [*extent["min"], round(min(zs), 2)], "max": [*extent["max"], round(max(zs), 2)]}
    return georef, extent


def _section_fallback(section: str | None):
    """Images without a georeference stand on the current section line, if the client sent one."""
    if not section:
        return None
    try:
        placement = VerticalPlacement(**json.loads(section))
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"Неверные параметры разреза: {exc}") from exc
    return lambda w, h: vertical_georef(w, h, placement.x1, placement.y1, placement.x2, placement.y2, placement.z_top, placement.z_bottom)


@router.post("/projects/{project_id}/rasters", response_model=list[DatasetOut], status_code=201)
async def upload_rasters(
    project_id: int,
    files: list[UploadFile] = File(...),
    section: str | None = Form(None, description="JSON VerticalPlacement for images without a georeference"),
    session: Session = Depends(get_session),
):
    """Images with their georeference files (same name, any of .jgw/.pgw/.tfw/.wld, .tab, .grf).
    Images without one stand on the section line given in `section`, if any.
    Every image becomes a dataset of kind "rasters"."""
    get_project(session, project_id)
    fallback = _section_fallback(section)
    names = [f.filename or f"file{i}" for i, f in enumerate(files)]
    unknown = [n for n in names if Path(n).suffix.lower() not in IMAGE_SUFFIXES + SIDECAR_SUFFIXES]
    if unknown:
        raise HTTPException(415, f"Неподдерживаемые файлы: {', '.join(unknown)}. Изображения: {', '.join(IMAGE_SUFFIXES)}; привязка: {', '.join(SIDECAR_SUFFIXES)}")
    groups, unmatched = pair_files(names)
    if not groups:
        raise HTTPException(422, "Среди файлов нет изображения (jpg, png, tif, bmp)")
    if unmatched:
        raise HTTPException(422, f"Файлы привязки без изображения с тем же именем: {', '.join(unmatched)}")

    created: list[Dataset] = []
    with tempfile.TemporaryDirectory() as tmp:
        paths = {}
        for i, (name, upload) in enumerate(zip(names, files)):
            path = Path(tmp) / f"{i}{Path(name).suffix.lower()}"
            await _save_upload(upload, path)
            paths[name] = path
        images = []
        for image_name, sidecar_names in groups.items():
            sidecars = {}
            for s_name in sidecar_names:
                raw = paths[s_name].read_bytes()
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("cp1251", errors="replace")
                sidecars[Path(s_name).suffix.lower()] = text
            try:
                raster = read_raster(paths[image_name], image_name, sidecars, fallback)
                if raster.georef.source == "Вручную по разрезу":
                    raster.notes.append("Файла привязки нет: изображение поставлено в плоскость линии разреза, уточните края и высоты в свойствах")
                images.append((image_name, sidecar_names, raster))
            except RasterFormatError as exc:
                raise HTTPException(422, str(exc)) from exc

    for image_name, sidecar_names, r in images:
        georef, extent = raster_placement(r.georef, r.width, r.height, r.checks, r.notes)
        dataset = Dataset(project_id=project_id, kind="rasters", name=Path(image_name).name[:255],
                          meta={"format": Path(image_name).suffix.lstrip(".").upper(), "filename": image_name, "sidecars": sidecar_names})
        session.add(dataset)
        session.flush()
        session.add(Raster(
            dataset_id=dataset.id,
            name=Path(image_name).stem[:255],
            width=r.width,
            height=r.height,
            georef=georef,
            extent=extent,
            elevation=r.georef.elevation,
            opacity=1.0,
            image=r.image,
            mime=r.mime,
        ))
        created.append(dataset)
    session.commit()
    return [dataset_out(session, d) for d in created]


def _get_raster(session: Session, raster_id: int) -> Raster:
    raster = session.get(Raster, raster_id)
    if raster is None:
        raise HTTPException(404, "Растр не найден")
    return raster


@router.get("/rasters/{raster_id}/image")
def raster_image(raster_id: int, session: Session = Depends(get_session)) -> Response:
    raster = _get_raster(session, raster_id)
    return Response(raster.image, media_type=raster.mime, headers={"Cache-Control": "private, max-age=86400"})


@router.patch("/rasters/{raster_id}", response_model=RasterOut)
def patch_raster(raster_id: int, body: RasterPatch, session: Session = Depends(get_session)):
    raster = _get_raster(session, raster_id)
    if body.name is not None:
        raster.name = body.name.strip()
    if body.clear_elevation:
        raster.elevation = None
    elif body.elevation is not None:
        raster.elevation = body.elevation
    if body.opacity is not None:
        raster.opacity = body.opacity
    if body.vertical is not None:
        v = body.vertical
        try:
            g = vertical_georef(raster.width, raster.height, v.x1, v.y1, v.x2, v.y2, v.z_top, v.z_bottom)
        except RasterFormatError as exc:
            raise HTTPException(422, str(exc)) from exc
        notes = [n for n in raster.georef.get("notes", []) if "плоскость линии разреза" not in n]
        raster.georef, raster.extent = raster_placement(g, raster.width, raster.height, [], notes)
    session.commit()
    return _raster_out(raster)
