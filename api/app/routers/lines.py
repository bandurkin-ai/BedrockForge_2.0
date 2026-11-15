"""Line layers as editable objects: new layers, exact geometry for the string editor,
saving with a version check, the journal of versions and returning to a version."""

from __future__ import annotations

import json
from datetime import datetime

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.importing.objects import PALETTE, add_line_layer, used_colors
from app.lines.edit import LineEditError, VersionConflict, locked_layer, restore_version, save_layer
from app.models import Dataset, LineLayer, LineLayerVersion
from app.routers.blobs import blob_url
from app.routers.datasets import DatasetOut, LineLayerOut, _line_out, clean_folder, dataset_out, get_dataset
from app.routers.projects import get_project
from app.storage import geometry

router = APIRouter(prefix="/api", tags=["lines"])

# Body limit of one save: 20 million vertices.
MAX_SAVE_BYTES = 480_000_000


class NewLayerIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    folder: str = Field("", max_length=255)
    # Add the layer to this file entry of the tree instead of making a new one.
    dataset_id: int | None = None


class VersionOut(BaseModel):
    version: int
    action: str
    note: str
    author: str | None
    line_count: int
    point_count: int
    prev_line_count: int
    prev_point_count: int
    created_at: datetime
    current: bool


class MoveLinesIn(BaseModel):
    # Lines of this layer (0-based, in stored order) that go to the end of the target layer.
    lines: list[int] = Field(min_length=1, max_length=100_000)
    target_id: int
    base_version: int
    target_version: int | None = None
    author: str | None = Field(None, max_length=128)


def _layer(session: Session, layer_id: int, lock: bool = False) -> LineLayer:
    layer = locked_layer(session, layer_id) if lock else session.get(LineLayer, layer_id)
    if layer is None:
        raise HTTPException(404, "Слой линий не найден")
    return layer


@router.post("/projects/{project_id}/line-layers", response_model=DatasetOut, status_code=201)
def create_line_layer(project_id: int, body: NewLayerIn, session: Session = Depends(get_session)):
    """An empty layer to draw in. The layer name is the string code written to STR on export."""
    get_project(session, project_id)
    name = body.name.strip()
    if body.dataset_id is not None:
        dataset = get_dataset(session, body.dataset_id)
        if dataset.kind != "lines" or dataset.project_id != project_id:
            raise HTTPException(422, "Слой можно добавить только к линиям этого проекта")
    else:
        dataset = Dataset(project_id=project_id, kind="lines", name=name, folder=clean_folder(body.folder),
                          meta={"format": "BedrockForge", "created": True}, blobs={})
        session.add(dataset)
        session.flush()
    color = (body.color or PALETTE[used_colors(session, project_id) % len(PALETTE)]).lower()
    add_line_layer(session, dataset.id, name, color, [], action="create")
    session.commit()
    return dataset_out(session, dataset)


@router.get("/lines/{layer_id}/exact")
def line_exact(layer_id: int, session: Session = Depends(get_session)) -> dict:
    """The exact arrays for the editor: float64 vertices, int64 line starts (lines + 1), uint8
    closed flags — raw little-endian by /api/blobs/{sha}; a closed line repeats its first vertex."""
    layer = _layer(session, layer_id)
    return {
        "id": layer.id, "name": layer.name, "color": layer.color, "version": layer.version,
        "vertex_count": layer.point_count, "line_count": layer.line_count,
        "vertices": blob_url(layer.blobs["vertices"]), "offsets": blob_url(layer.blobs["offsets"]),
        "closed": blob_url(layer.blobs["closed"]), "attributes": layer.line_attributes, "stats": layer.stats,
    }


async def _read_array(file: UploadFile, dtype: str, what: str) -> np.ndarray:
    data = await file.read(MAX_SAVE_BYTES + 1)
    if len(data) > MAX_SAVE_BYTES:
        raise HTTPException(413, "Слой слишком большой для сохранения одним запросом")
    if len(data) % np.dtype(dtype).itemsize:
        raise HTTPException(422, f"Неверный размер массива «{what}»")
    return np.frombuffer(data, dtype=dtype)


@router.put("/lines/{layer_id}/geometry", response_model=LineLayerOut)
async def save_geometry(
    layer_id: int,
    base_version: int = Form(..., description="The version the editor started from"),
    vertices: UploadFile = File(..., description="raw little-endian float64 x, y, z"),
    offsets: UploadFile = File(..., description="raw little-endian uint32: start of every line, plus the end"),
    closed: UploadFile = File(..., description="raw uint8 per line"),
    attributes: str = Form("[]", description="JSON: one object per line"),
    author: str | None = Form(None),
    note: str = Form(""),
    session: Session = Depends(get_session),
):
    """Saves the layer from the editor as its next version. The server recalculates the extent,
    the measures and the copy for display. 409 when the layer was saved elsewhere meanwhile."""
    v = await _read_array(vertices, "<f8", "вершины")
    if len(v) % 3:
        raise HTTPException(422, "Неверный размер массива «вершины»")
    o = await _read_array(offsets, "<u4", "начала линий")
    c = await _read_array(closed, "u1", "замкнутость")
    try:
        attrs = json.loads(attributes)
        if not isinstance(attrs, list):
            raise ValueError("ожидается список")
    except ValueError as exc:
        raise HTTPException(422, f"Атрибуты линий не читаются: {exc}") from exc
    layer = _layer(session, layer_id, lock=True)
    try:
        save_layer(session, layer, v.reshape(-1, 3), o.astype("<i8"), c, attrs, base_version=base_version,
                   note=note, author=author)
    except VersionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except LineEditError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return _line_out(layer)


@router.get("/lines/{layer_id}/versions", response_model=list[VersionOut])
def list_versions(layer_id: int, session: Session = Depends(get_session)):
    """The journal of the layer, newest first."""
    layer = _layer(session, layer_id)
    rows = session.scalars(select(LineLayerVersion).where(LineLayerVersion.layer_id == layer.id)
                           .order_by(LineLayerVersion.version.desc()))
    return [VersionOut(version=r.version, action=r.action, note=r.note, author=r.author, line_count=r.line_count,
                       point_count=r.point_count, prev_line_count=r.prev_line_count, prev_point_count=r.prev_point_count,
                       created_at=r.created_at, current=r.version == layer.version) for r in rows]


@router.post("/lines/{layer_id}/versions/{version}/restore", response_model=LineLayerOut)
def restore(layer_id: int, version: int, author: str | None = None, session: Session = Depends(get_session)):
    """Makes an old version current again — as a new version, so nothing is erased."""
    layer = _layer(session, layer_id, lock=True)
    if version == layer.version:
        raise HTTPException(422, "Это текущая версия слоя")
    try:
        restore_version(session, layer, version, author=author)
    except LineEditError as exc:
        raise HTTPException(404, str(exc)) from exc
    session.commit()
    return _line_out(layer)


@router.post("/lines/{layer_id}/move-lines", response_model=list[LineLayerOut])
def move_lines(layer_id: int, body: MoveLinesIn, session: Session = Depends(get_session)):
    """Moves lines to another layer of the project; both layers get a new version."""
    if body.target_id == layer_id:
        raise HTTPException(422, "Линии уже в этом слое")
    # Locked in id order, so two opposite moves cannot deadlock.
    first, second = sorted((layer_id, body.target_id))
    locked = {first: _layer(session, first, lock=True), second: _layer(session, second, lock=True)}
    source, target = locked[layer_id], locked[body.target_id]
    if get_dataset(session, source.dataset_id).project_id != get_dataset(session, target.dataset_id).project_id:
        raise HTTPException(422, "Слои из разных проектов")
    picked = sorted(set(body.lines))
    if picked[0] < 0 or picked[-1] >= source.line_count:
        raise HTTPException(422, "Такой линии в слое нет")
    v, o, c = geometry.line_arrays(source.blobs)
    tv, to, tc = geometry.line_arrays(target.blobs)
    take = np.zeros(source.line_count, dtype=bool)
    take[picked] = True

    def subset(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, list]:
        idx = np.flatnonzero(mask)
        parts = [np.asarray(v[o[i]:o[i + 1]]) for i in idx]
        starts = np.zeros(len(idx) + 1, dtype=np.int64)
        starts[1:] = np.cumsum([len(p) for p in parts])
        attrs = [(source.line_attributes[i] if i < len(source.line_attributes) else {}) for i in idx]
        return (np.vstack(parts) if parts else np.zeros((0, 3))), starts, np.asarray(c)[idx], attrs

    mv, mo, mc, ma = subset(take)
    kv, ko, kc, ka = subset(~take)
    note = f"Перенос линий ({len(picked)}): «{source.name}» → «{target.name}»"
    try:
        save_layer(session, source, kv, ko, kc, ka, base_version=body.base_version, action="move", note=note, author=body.author)
        save_layer(session, target, np.vstack([np.asarray(tv), mv]), np.concatenate([np.asarray(to), mo[1:] + len(tv)]),
                   np.concatenate([np.asarray(tc), mc]), [*(target.line_attributes or []), *ma],
                   base_version=body.target_version, action="move", note=note, author=body.author)
    except VersionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except LineEditError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return [_line_out(source), _line_out(target)]
