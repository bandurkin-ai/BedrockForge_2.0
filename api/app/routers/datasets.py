"""The project tree: datasets of every kind, drillhole sets and wireframes."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.audit.engine import latest_run
from app.config import settings
from app.db import get_session
from app.formats.lines import LINE_SUFFIXES
from app.formats.rasters import IMAGE_SUFFIXES, SIDECAR_SUFFIXES, RasterFormatError, vertical_georef
from app.formats.wireframes import WIREFRAME_SUFFIXES
from app.importing.objects import (
    ImportFailed, SectionPlacement, import_lines, import_rasters, import_wireframes, raster_placement, shift_raster_z,
)
from app.models import Assay, Chart, Collar, Component, Dataset, IntervalTable, LineLayer, Raster, Survey, Wireframe
from app.routers.blobs import blob_url
from app.routers.projects import get_project
from app.storage import blobs

router = APIRouter(prefix="/api", tags=["datasets"])


class WireframeOut(BaseModel):
    id: int
    name: str
    color: str
    point_count: int
    triangle_count: int
    extent: dict
    stats: dict
    # Simplified copies for distant views (they appear after the checks job).
    lod_levels: int = 0
    # Settings of a terrain model (the «ЦТМ» section); None for an ordinary wireframe.
    dtm: dict | None = None


class LineLayerOut(BaseModel):
    id: int
    name: str
    color: str
    line_count: int
    point_count: int
    extent: dict
    stats: dict
    # Number of the saved state (the string editor's version check).
    version: int = 1


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
    folder: str = ""
    created_at: datetime
    meta: dict
    # drillholes: {"collar": n, "survey": n, "assay": n}
    counts: dict[str, int] | None = None
    components: list[dict[str, str]] | None = None
    audit: dict[str, int] | None = None
    # drillholes: saved charts [{id, kind, name}]
    charts: list[dict] | None = None
    # drillholes: interval tables besides the assays [{id, name, row_count}]
    interval_tables: list[dict] | None = None
    # wireframes
    wireframes: list[WireframeOut] | None = None
    lines: list[LineLayerOut] | None = None
    rasters: list[RasterOut] | None = None


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class DatasetPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    # "/"-separated folder inside the kind's group; "" puts the object back at the top.
    folder: str | None = Field(None, max_length=255)


def clean_folder(folder: str) -> str:
    parts = [p.strip() for p in folder.replace("\\", "/").split("/")]
    return "/".join(p for p in parts if p)[:255]


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
    # Raise (> 0) or lower the raster by this many metres: a plan one, or every corner of a vertical one.
    z_shift: float | None = Field(None, ge=-20000, le=20000)


class DtmSettings(BaseModel):
    # The raster draped on the surface; 0 takes it off.
    raster_id: int | None = Field(None, ge=0)
    # Hide the part of the raster outside the surface (otherwise it stays flat at its elevation).
    clip: bool | None = None
    # Colour the image is multiplied by ("#ffffff": as is) and how much of it covers the surface.
    tint: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    opacity: float | None = Field(None, ge=0, le=1)
    # Light and shadow of the relief on the image.
    shade: bool | None = None


class WireframePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    # Every attribute of the wireframe (replaces the old ones): name -> text, number, yes/no or empty.
    attributes: dict[str, str | float | int | bool | None] | None = Field(None, max_length=200)
    # True: move the wireframe to the «ЦТМ» section (terrain models); False: back to the wireframes.
    dtm: bool | None = None
    # Settings of a terrain model (only the given ones change).
    dtm_settings: DtmSettings | None = None


DTM_DEFAULTS = {"raster_id": None, "clip": True, "tint": "#ffffff", "opacity": 1.0, "shade": True}


def get_dataset(session: Session, dataset_id: int) -> Dataset:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "Объект не найден")
    return dataset


def _wireframe_out(w: Wireframe) -> WireframeOut:
    return WireframeOut(
        id=w.id, name=w.name, color=w.color, point_count=w.point_count,
        triangle_count=w.triangle_count, extent=w.extent, stats=w.stats,
        lod_levels=len((w.render or {}).get("lods", [])),
        dtm={**DTM_DEFAULTS, **w.dtm} if w.dtm is not None else None,
    )


def dataset_out(session: Session, d: Dataset) -> DatasetOut:
    out = DatasetOut(id=d.id, kind=d.kind, name=d.name, folder=d.folder or "", created_at=d.created_at, meta=d.meta or {})
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
        out.charts = [{"id": c.id, "kind": c.kind, "name": c.name}
                      for c in session.scalars(select(Chart).where(Chart.dataset_id == d.id).order_by(Chart.id))]
        out.interval_tables = [{"id": t.id, "name": t.name, "row_count": t.row_count, "kind": t.kind}
                               for t in session.scalars(select(IntervalTable).where(IntervalTable.set_id == d.id).order_by(IntervalTable.id))]
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
    return LineLayerOut(id=x.id, name=x.name, color=x.color, line_count=x.line_count, point_count=x.point_count, extent=x.extent, stats=x.stats, version=x.version)


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
def patch_dataset(dataset_id: int, body: DatasetPatch, session: Session = Depends(get_session)):
    """Rename an object or move it to a folder of the tree."""
    dataset = get_dataset(session, dataset_id)
    if body.name is not None:
        dataset.name = body.name.strip()
    if body.folder is not None:
        dataset.folder = clean_folder(body.folder)
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
    """Read a TRIDB or DXF file in this request (small files, scripts); every wireframe in it
    becomes an item of one new dataset. Large files: resumable upload + import job (routers/uploads.py)."""
    get_project(session, project_id)
    filename = file.filename or "wireframes"
    suffix = Path(filename).suffix.lower()
    if suffix not in WIREFRAME_SUFFIXES:
        raise HTTPException(415, f"Каркасы читаются из файлов {', '.join(WIREFRAME_SUFFIXES)}")
    with tempfile.TemporaryDirectory(dir=blobs.tmp_dir()) as tmp:
        path = Path(tmp) / f"data{suffix}"
        await _save_upload(file, path)
        try:
            dataset = import_wireframes(session, project_id, path, filename)
        except ImportFailed as exc:
            raise HTTPException(422, str(exc)) from exc
    session.commit()
    return dataset_out(session, dataset)


def _get_wireframe(session: Session, wireframe_id: int) -> Wireframe:
    wireframe = session.get(Wireframe, wireframe_id)
    if wireframe is None:
        raise HTTPException(404, "Каркас не найден")
    return wireframe


@router.get("/wireframes/{wireframe_id}/geometry")
def wireframe_geometry(wireframe_id: int, session: Session = Depends(get_session)) -> dict:
    """Where the render buffers are: float32 x, y, z relative to `origin` and uint32 triangle
    indices, raw little-endian, served by /api/blobs/{sha256} (immutable, Range requests)."""
    w = _get_wireframe(session, wireframe_id)
    render = w.render or {}
    lods = [
        {"level": i, "triangles": lod["triangles"], "vertex_count": lod["vertices"],
         "vertices": blob_url(w.blobs[f"lod{i}_vertices"]), "triangles_url": blob_url(w.blobs[f"lod{i}_triangles"])}
        for i, lod in enumerate(render.get("lods", []), start=1)
    ]
    return {
        "id": w.id,
        "origin": w.origin,
        "vertex_count": w.point_count,
        "triangle_count": w.triangle_count,
        "vertices": blob_url(w.blobs["render_vertices"]),
        "triangles": blob_url(w.blobs["triangles"]),
        # Parts of the triangle buffer in spatial order: [first triangle, count, min xyz, max xyz]
        # relative to origin; read one with Range: bytes first*12 .. (first+count)*12-1.
        "chunks": render.get("chunks", []),
        # Simplified copies, coarser and coarser (only for large meshes).
        "lods": lods,
    }


@router.patch("/wireframes/{wireframe_id}", response_model=WireframeOut)
def patch_wireframe(wireframe_id: int, body: WireframePatch, session: Session = Depends(get_session)):
    w = _get_wireframe(session, wireframe_id)
    if body.name is not None:
        w.name = body.name.strip()
    if body.color is not None:
        w.color = body.color.lower()
    if body.attributes is not None:
        attrs = {}
        for k, v in body.attributes.items():
            key = k.strip()[:100]
            if not key:
                raise HTTPException(422, "У атрибута должно быть название")
            if key in attrs:
                raise HTTPException(422, f"Атрибут «{key}» указан дважды")
            attrs[key] = v.strip()[:2000] if isinstance(v, str) else v
        w.stats = {**w.stats, "attributes": attrs}
    if body.dtm is True and w.dtm is None:
        w.dtm = dict(DTM_DEFAULTS)
    elif body.dtm is False:
        w.dtm = None
    if body.dtm_settings is not None:
        if w.dtm is None:
            raise HTTPException(422, "Каркас не в разделе ЦТМ: сначала перенесите его в ЦТМ")
        changes = body.dtm_settings.model_dump(exclude_unset=True)
        raster_id = changes.get("raster_id")
        if raster_id:
            project_id = get_dataset(session, w.dataset_id).project_id
            raster = session.get(Raster, raster_id)
            if raster is None or get_dataset(session, raster.dataset_id).project_id != project_id:
                raise HTTPException(422, "Растр не найден в этом проекте")
            if raster.georef.get("corners3d"):
                raise HTTPException(422, "Растр стоит в вертикальной плоскости: на рельеф кладётся только растр в плане")
        elif "raster_id" in changes:
            changes["raster_id"] = None
        if changes.get("tint"):
            changes["tint"] = changes["tint"].lower()
        w.dtm = {**DTM_DEFAULTS, **w.dtm, **{k: v for k, v in changes.items() if v is not None or k == "raster_id"}}
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
                raise HTTPException(413, f"Файл «{file.filename}» слишком большой для прямой загрузки; используйте загрузку частями")
            out.write(chunk)
    return size


# ---- lines


@router.post("/projects/{project_id}/lines", response_model=DatasetOut, status_code=201)
async def upload_lines(project_id: int, file: UploadFile, session: Session = Depends(get_session)):
    """Read a Micromine / Surpac STR or a DXF file; every layer (string code, DXF layer) becomes an item."""
    get_project(session, project_id)
    filename = file.filename or "lines"
    suffix = Path(filename).suffix.lower()
    if suffix not in LINE_SUFFIXES:
        raise HTTPException(415, f"Линии читаются из файлов {', '.join(LINE_SUFFIXES)}")
    with tempfile.TemporaryDirectory(dir=blobs.tmp_dir()) as tmp:
        path = Path(tmp) / f"data{suffix}"
        await _save_upload(file, path)
        try:
            dataset = import_lines(session, project_id, path, filename)
        except ImportFailed as exc:
            raise HTTPException(422, str(exc)) from exc
    session.commit()
    return dataset_out(session, dataset)


def _get_line_layer(session: Session, layer_id: int) -> LineLayer:
    layer = session.get(LineLayer, layer_id)
    if layer is None:
        raise HTTPException(404, "Слой линий не найден")
    return layer


@router.get("/lines/{layer_id}/geometry")
def line_geometry(layer_id: int, session: Session = Depends(get_session)) -> dict:
    """Render buffers: float32 vertices relative to `origin`, uint32 line starts (lines + 1), uint8 closed flags."""
    layer = _get_line_layer(session, layer_id)
    return {
        "id": layer.id,
        "origin": layer.origin,
        "version": layer.version,
        "vertex_count": layer.point_count,
        "line_count": layer.line_count,
        "vertices": blob_url(layer.blobs["render_vertices"]),
        "offsets": blob_url(layer.blobs["render_offsets"]),
        "closed": blob_url(layer.blobs["closed"]),
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


def _section_fallback(section: str | None) -> SectionPlacement | None:
    """Images without a georeference stand on the current section line, if the client sent one."""
    if not section:
        return None
    try:
        placement = VerticalPlacement(**json.loads(section))
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"Неверные параметры разреза: {exc}") from exc
    return SectionPlacement(**placement.model_dump())


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
    placement = _section_fallback(section)
    names = [f.filename or f"file{i}" for i, f in enumerate(files)]
    unknown = [n for n in names if Path(n).suffix.lower() not in IMAGE_SUFFIXES + SIDECAR_SUFFIXES]
    if unknown:
        raise HTTPException(415, f"Неподдерживаемые файлы: {', '.join(unknown)}. Изображения: {', '.join(IMAGE_SUFFIXES)}; привязка: {', '.join(SIDECAR_SUFFIXES)}")
    with tempfile.TemporaryDirectory(dir=blobs.tmp_dir()) as tmp:
        paths = {}
        for i, (name, upload) in enumerate(zip(names, files)):
            path = Path(tmp) / f"{i}{Path(name).suffix.lower()}"
            await _save_upload(upload, path)
            paths[name] = path
        try:
            created = import_rasters(session, project_id, paths, section=placement)
        except ImportFailed as exc:
            raise HTTPException(422, str(exc)) from exc
    session.commit()
    return [dataset_out(session, d) for d in created]


def _get_raster(session: Session, raster_id: int) -> Raster:
    raster = session.get(Raster, raster_id)
    if raster is None:
        raise HTTPException(404, "Растр не найден")
    return raster


@router.get("/rasters/{raster_id}/image")
def raster_image(raster_id: int, session: Session = Depends(get_session)) -> FileResponse:
    raster = _get_raster(session, raster_id)
    sha = raster.blobs["image"]
    return FileResponse(blobs.path_of(sha), media_type=raster.mime,
                        headers={"Cache-Control": "private, max-age=86400", "ETag": f'"{sha}"'})


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
    if body.z_shift:
        if raster.elevation is None and not raster.georef.get("corners3d"):
            raise HTTPException(422, "У растра нет высоты: сначала задайте её, потом сдвигайте")
        raster.georef, raster.extent, raster.elevation = shift_raster_z(raster.georef, raster.extent, raster.elevation, body.z_shift)
    session.commit()
    return _raster_out(raster)


# ---- sections (stage 10.5)


class SectionIn(BaseModel):
    wireframe_ids: list[int] = Field(min_length=1, max_length=500)
    # A point of the plane (project coordinates) and its normal.
    point: list[float] = Field(min_length=3, max_length=3)
    normal: list[float] = Field(min_length=3, max_length=3)


@lru_cache(maxsize=512)
def _cached_section(vertices_sha: str, triangles_sha: str, origin: tuple, point: tuple, normal: tuple,
                    chunks_json: str) -> list:
    from app.geo.section import plane_section
    from app.storage.geometry import mesh_arrays

    v, t = mesh_arrays({"vertices": vertices_sha, "triangles": triangles_sha})
    return plane_section(v, t, origin, point, normal, json.loads(chunks_json) or None)


@router.post("/sections")
def sections(body: SectionIn, session: Session = Depends(get_session)) -> dict:
    """Exact sections of wireframes by one plane, float64, relative to every wireframe's origin.
    Only the parts of each mesh that the plane touches are read; results are cached per plane."""
    import time

    if not any(body.normal):
        raise HTTPException(422, "Нормаль плоскости не задана")
    started = time.perf_counter()
    rows = session.scalars(select(Wireframe).where(Wireframe.id.in_(body.wireframe_ids)))
    point = tuple(round(x, 6) for x in body.point)
    normal = tuple(round(x, 9) for x in body.normal)
    out = []
    for w in rows:
        polylines = _cached_section(w.blobs["vertices"], w.blobs["triangles"], tuple(w.origin), point, normal,
                                    json.dumps((w.render or {}).get("chunks", [])))
        out.append({"id": w.id, "origin": w.origin, "polylines": polylines})
    return {"sections": out, "ms": round((time.perf_counter() - started) * 1000, 1)}


@router.post("/datasets/{dataset_id}/check", status_code=202)
def check_wireframes(dataset_id: int, session: Session = Depends(get_session)) -> dict:
    """Runs the checks and builds the levels of detail of a wireframe file again (a job);
    for files loaded before these existed, or after the rules changed."""
    from app.importing.objects import queue_derive
    from app.jobs import queue
    from app.models import Job

    dataset = get_dataset(session, dataset_id)
    if dataset.kind != "wireframes":
        raise HTTPException(422, "Проверка — только для каркасов")
    queue_derive(session, dataset.project_id, dataset)
    session.flush()
    job = session.scalars(select(Job).where(Job.kind == "wireframes.derive").order_by(Job.id.desc()).limit(1)).first()
    session.commit()
    return queue.job_out(job)
