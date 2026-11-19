"""/api/v1/lines — line files and their layers (ТЗ 7.18; ADR 0011 §3).

`GET /lines/files` — the tree «Линии»: files with their layers; `GET/DELETE /lines/files/{id}` — one file; deleting
it deletes its layers. `GET/PATCH /lines/layers/{id}` — the properties of a layer (name, colour, length, closed
lines, attribute schema); a PATCH carries `expected_version`, a stale one is a 409 with the current layer.
`GET /lines/layers/{id}/attrs?offset&limit&scope` — attributes by pages with an ETag (If-None-Match → 304); of a
point cloud — a slice of its blobs with X, Y, Z and the record numbers (`service.cloud_page`). The properties of a
cloud layer have `cloud` (`service.cloud_summary`).
The geometry for 3D is `/viz/lines/{id}` (`viz/router.py`).
`POST /lines/export` starts the job `export.lines` (`lines/export.py`): layers or a whole file to STR, Surpac
STR, DXF, CSV or DAT; a point cloud (alone) to LAS — by its source file (`las: "source"`) or as a new file
(`"new"`) — or to CSV `X;Y;Z;класс[;h]` (`lines/cloud_export.py`).
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

# numpy and pyarrow load on first use, not at the start of the core (tests/test_process.py): `service` and
# `export` are imported inside the endpoints.

router = APIRouter(prefix="/lines")
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
IfNoneMatch = Annotated[str | None, Header()]
CACHE_HEADERS = {"Cache-Control": "no-cache"}
MAX_PAGE = 5000  # rows of attributes in one answer


def _changed(request: Request, kind: str, object_id: int, **extra: Any) -> None:
    request.app.state.core.events.publish("objects.changed", {"kind": kind, "id": object_id, **extra})


@router.get("/files")
def list_files(svc: Svc) -> dict[str, Any]:
    from . import service

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"files": service.list_files(conn)}


@router.get("/files/{file_id}")
def get_file(svc: Svc, file_id: int) -> dict[str, Any]:
    from . import service

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return service.get_file(conn, file_id)


@router.delete("/files/{file_id}")
def delete_file(svc: Svc, request: Request, file_id: int) -> dict[str, Any]:
    """Deletes a file of lines with all its layers; their files leave the storage with its next cleanup."""
    from . import service

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        gone = service.delete_file(conn, file_id)
    _changed(request, "line_file", file_id, deleted=True)
    return {"deleted": gone}


@router.get("/layers/{layer_id}")
def get_layer(svc: Svc, layer_id: int) -> dict[str, Any]:
    from . import service

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return service.get_layer(conn, layer_id)


class LayerPatch(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=200)
    color: str | None = Field(default=None, max_length=16)  # "#rrggbb"; null — no own colour


@router.patch("/layers/{layer_id}")
def patch_layer(svc: Svc, request: Request, layer_id: int, body: LayerPatch) -> dict[str, Any]:
    from . import service

    project = svc.require_current(writable=True)
    changes: dict[str, Any] = {k: getattr(body, k) for k in ("name", "color") if k in body.model_fields_set}
    if changes.get("name") is None:
        changes.pop("name", None)  # a null name is «not given»: a layer always has a name
    with dialect.write_tx(project.engine) as conn:
        out = service.update_layer(conn, layer_id, body.expected_version, changes)
    _changed(request, "line_layer", layer_id)
    return out


@router.get("/layers/{layer_id}/attrs")
def layer_attrs(
    svc: Svc,
    layer_id: int,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 200,
    scope: Literal["feature", "vertex"] = "feature",
    if_none_match: IfNoneMatch = None,
) -> Response:
    """A page of the attributes: a row per line (per point), or per vertex with `scope=vertex`."""
    from bf_core.storage.blobs import BlobStore
    from bf_core.viz.keys import matches

    from . import service

    project = svc.require_current()
    store = BlobStore(project.folder)
    cloud = None
    with dialect.read_tx(project.engine) as conn:
        layer, shas = service.attrs_key(conn, layer_id, scope)
        if layer.get("cloud") is not None:
            from .cloud import open_cloud

            cloud = open_cloud(conn, store, layer_id)
    blobs = sorted(shas.items())
    key = json.dumps(["line-attrs", project.uid, layer_id, layer["version"], blobs, scope, offset, limit])
    tag = '"' + hashlib.sha256(key.encode()).hexdigest() + '"'
    headers = {"ETag": tag, **CACHE_HEADERS}
    if matches(if_none_match, tag):
        return Response(status_code=304, headers=headers)
    if cloud is not None:
        page = service.cloud_page(cloud, layer, scope, offset, limit)
    else:
        page = service.attrs_page(store, layer, shas, scope, offset, limit)
    return JSONResponse(page, headers=headers)


class ExportBody(BaseModel):
    path: str = Field(max_length=2000)
    format: str = Field(max_length=16)  # str | surpac | dxf | csv | dat | las (a point cloud)
    layer_ids: list[int] | None = Field(default=None, max_length=2000)
    file_id: int | None = None  # every layer of a file, when `layer_ids` is not given
    delimiter: str | None = Field(default=None, min_length=1, max_length=1)
    decimal: str | None = Field(default=None, pattern="^[.,]$")
    encoding: str | None = Field(default=None, max_length=20)
    # LAS of a point cloud: a copy of its source file with the classes of the cloud, or a new file; null — by the
    # source when the cloud was loaded from a LAS
    las: Literal["source", "new"] | None = None
    # the file under the name with the format's extension exists: the user agreed to replace it (409 before)
    overwrite: bool = False


@router.post("/export")
def export_lines(svc: Svc, s: Sched, body: ExportBody) -> dict[str, Any]:
    """`lines.export`: the job «Экспорт линий» writes the layers to the chosen file."""
    from . import export

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        params = export.request(conn, **body.model_dump())
    label = export.FORMATS[body.format].label
    key, job_id = s.submit("export.lines", params, title=f"Экспорт линий ({label})")
    return {"source": key, "id": job_id}


class ClassifyBody(BaseModel):
    """«Фильтр земли» (ТЗ 7.35 Д-22, ADR 0014): a profile and its numbers; an empty number — the profile's."""

    profile: Literal["quarry", "plain"] | None = None
    cr: float | str | None = None
    ri: int | None = None
    ct: float | str | None = None
    r_b: float | str | bool | None = None
    noise: dict[str, Any] | bool | None = None


@router.post("/layers/{layer_id}/classify")
def classify_cloud(svc: Svc, s: Sched, layer_id: int, body: ClassifyBody) -> dict[str, Any]:
    """`ground.classify`: the job «Фильтр земли» writes the classes and `h` of a point cloud (heavy queue)."""
    from . import ground_job

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        params = ground_job.request(conn, layer_id, **body.model_dump(), heavy_workers=s.heavy_workers)
    key, job_id = s.submit("ground.classify", params, title="Фильтр земли")
    return {"source": key, "id": job_id}
