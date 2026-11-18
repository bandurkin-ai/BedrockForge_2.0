"""/api/v1/rasters — rasters of the project (ТЗ 7.19; ADR 0011 §3).

`GET /rasters`, `GET /rasters/{id}` — the tree «Растры» and the properties (size, georeference, corners,
deviations at control points, elevation, opacity). Changes carry `expected_version`; a stale one is a 409 with
the current raster:
  * `PATCH /rasters/{id}` — `raster.set`: name, elevation (null — under the lowest object), opacity 0.05…1,
    `vertical` {x1, y1, x2, y2, z_top, z_bottom} — the edges of a raster in a vertical plane;
  * `POST /rasters/{id}/shift {dz}` — every corner of a vertical raster, the level of a plan one; a plan raster
    without an elevation → 422 `no_elevation`;
  * `POST /rasters/{id}/place-on-section {z_top}` — `raster.place_on_section`: into the plane of the section
    line of the view settings;
  * `DELETE /rasters/{id}` — a raster draped on a DTM → 422 `raster_in_use` with the list of the DTMs.
`GET /rasters/{id}/image?level=0|1` — the display copy (≤ 4096 / ≤ 1024 px) with an ETag; If-None-Match → 304.
`POST /rasters/{id}/export` starts the job `export.raster` (`rasters/export.py`): the source file or a GeoTIFF.
Numbers may come as text with a comma or a point («−5,5»).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

# numpy and Pillow load on first use, not at the start of the core (tests/test_process.py): `store` and
# `export` are imported inside the endpoints.

router = APIRouter(prefix="/rasters")
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
IfNoneMatch = Annotated[str | None, Header()]
CACHE_HEADERS = {"Cache-Control": "no-cache"}
Number = float | str


def _number(value: Any, label: str) -> float | None:
    """«12,5», «12.5» and «−5» are numbers; empty — None; anything else — 422 `not_a_number`."""
    from bf_core.drillholes.edit import parse_number

    if isinstance(value, str):
        value = value.replace("−", "-")
    return parse_number(value, label)


def _required(value: Any, label: str) -> float:
    out = _number(value, label)
    if out is None:
        raise ApiError(422, "not_a_number", f"«{label}»: нужно число.", "Введите число, например 12,5.")
    return out


def _changed(request: Request, raster_id: int, **extra: Any) -> None:
    request.app.state.core.events.publish("objects.changed", {"kind": "raster", "id": raster_id, **extra})


@router.get("")
def list_rasters(svc: Svc) -> dict[str, Any]:
    from . import store

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"rasters": store.list_rasters(conn)}


@router.get("/{raster_id}")
def get_raster(svc: Svc, raster_id: int) -> dict[str, Any]:
    from . import store

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return store.raster_info(conn, raster_id)


class VerticalPlacement(BaseModel):
    """A raster standing in a vertical plane: left edge (x1, y1), right edge (x2, y2), from z_top down to z_bottom."""

    x1: Number
    y1: Number
    x2: Number
    y2: Number
    z_top: Number
    z_bottom: Number


class RasterPatch(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=200)
    elevation: Number | None = None  # null or empty clears it: the raster goes under the lowest object
    opacity: Number | None = None
    vertical: VerticalPlacement | None = None


_EDGES = {
    "x1": "Левый край X", "y1": "Левый край Y", "x2": "Правый край X", "y2": "Правый край Y",
    "z_top": "Верх Z", "z_bottom": "Низ Z",
}  # fmt: skip


@router.patch("/{raster_id}")
def patch_raster(svc: Svc, request: Request, raster_id: int, body: RasterPatch) -> dict[str, Any]:
    from . import store

    project = svc.require_current(writable=True)
    given = body.model_fields_set
    changes: dict[str, Any] = {}
    if "name" in given and body.name is not None:
        changes["name"] = body.name
    if "elevation" in given:
        changes["elevation"] = _number(body.elevation, "Высота")
    if "opacity" in given and body.opacity is not None:
        changes["opacity"] = _required(body.opacity, "Непрозрачность")
    if body.vertical is not None:
        changes["vertical"] = {k: _required(getattr(body.vertical, k), label) for k, label in _EDGES.items()}
    with dialect.write_tx(project.engine) as conn:
        out = store.update_raster(conn, raster_id, body.expected_version, changes)
    _changed(request, raster_id)
    return out


class ShiftBody(BaseModel):
    dz: Number
    expected_version: int = Field(ge=1)


@router.post("/{raster_id}/shift")
def shift_raster(svc: Svc, request: Request, raster_id: int, body: ShiftBody) -> dict[str, Any]:
    """«Сдвинуть» (ТЗ 7.19): +dz up, −dz down; all four corners of a vertical raster move together."""
    from . import store

    project = svc.require_current(writable=True)
    dz = _required(body.dz, "Сдвиг по Z")
    with dialect.write_tx(project.engine) as conn:
        out = store.shift_raster(conn, raster_id, dz, body.expected_version)
    _changed(request, raster_id)
    return out


class SectionBody(BaseModel):
    z_top: Number | None = None  # the top edge; empty — the raster spans the heights of the data
    expected_version: int = Field(ge=1)


@router.post("/{raster_id}/place-on-section")
def place_on_section(svc: Svc, request: Request, raster_id: int, body: SectionBody) -> dict[str, Any]:
    from . import store

    project = svc.require_current(writable=True)
    z_top = _number(body.z_top, "Верх Z")
    with dialect.write_tx(project.engine) as conn:
        out = store.place_on_section(conn, raster_id, z_top, body.expected_version)
    _changed(request, raster_id)
    return out


@router.delete("/{raster_id}")
def delete_raster(svc: Svc, request: Request, raster_id: int) -> dict[str, Any]:
    from . import store

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        store.delete_raster(conn, raster_id)
    _changed(request, raster_id, deleted=True)
    return {"deleted": raster_id}


@router.get("/{raster_id}/image")
def raster_image(
    svc: Svc, raster_id: int, level: Annotated[int, Query()] = 0, if_none_match: IfNoneMatch = None
) -> Response:
    """The display copy of a level. The ETag is the SHA-256 of the image: a re-imported raster that got the id
    of a deleted one is never taken for it."""
    from bf_core.storage.blobs import BlobStore
    from bf_core.viz.keys import matches

    from . import store

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        sha, media, size = store.image_blob(conn, raster_id, level)
    tag = f'"{sha}"'
    headers = {"ETag": tag, **CACHE_HEADERS}
    if matches(if_none_match, tag):
        return Response(status_code=304, headers=headers)
    blobs = BlobStore(project.folder)
    if not blobs.exists(sha):
        raise ApiError(
            404, "raster_image_missing", "Изображение растра не найдено в проекте.", "Загрузите растр заново."
        )
    headers["Content-Length"] = str(size)
    return StreamingResponse(blobs.iter_range(sha, 0, size - 1), media_type=media, headers=headers)


class ExportBody(BaseModel):
    path: str = Field(max_length=2000)
    format: str = Field(default="source", max_length=16)  # source | geotiff
    # files next to the chosen one that the export replaces (409 `files_exist` names them): the user agreed
    overwrite: bool = False


@router.post("/{raster_id}/export")
def export_raster(svc: Svc, s: Sched, raster_id: int, body: ExportBody) -> dict[str, Any]:
    """`rasters.export`: the job «Экспорт растра» writes the source file with its georeference, or a GeoTIFF.
    409 `files_exist` {files}: the export would replace files the save window did not ask about — the window
    asks and repeats the call with `overwrite: true`."""
    from bf_core.storage.blobs import BlobStore

    from . import export

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        params = export.request(
            conn, raster_id=raster_id, path=body.path, format=body.format, overwrite=body.overwrite,
            store=BlobStore(project.folder),
        )  # fmt: skip
    label = "GeoTIFF" if body.format == "geotiff" else "исходный файл"
    key, job_id = s.submit("export.raster", params, title=f"Экспорт растра ({label})")
    return {"source": key, "id": job_id}
