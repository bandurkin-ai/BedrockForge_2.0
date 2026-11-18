"""/api/v1/wireframes — wireframes and digital terrain models of the project (ТЗ 7.20; ADR 0011 §3).

`GET /wireframes`, `GET /wireframes/{id}` — the tree («Каркасы»: `dtm` null; «ЦТМ»: `dtm` {raster_id, clip,
tint, opacity, shade}) and the properties (counts, extent, measures, attributes of the user). Changes carry
`expected_version`; a stale one is a 409 with the current wireframe:
  * `PATCH /wireframes/{id}` — name, colour (`#rrggbb`; null — no own colour);
  * `PATCH /wireframes/{id}/attrs` — attributes of the user: `rename` {old: new}, `remove` [names], `set`
    {name: value} — in this order; value — text, number, yes/no or null (empty);
  * `PUT /wireframes/{id}/dtm` — `wireframe.dtm` {on, raster_id, clip, tint, opacity, shade}: settings without
    `on` move the wireframe to the DTMs; `raster_id` puts a plan raster on the relief (a vertical one → 422
    `drape_vertical_raster`), 0 or null takes it off; `on: false` — «Вернуть в каркасы»: the settings and the
    raster are cleared;
  * `DELETE /wireframes/{id}` — a wireframe other objects refer to → 422 `wireframe_in_use` with their list.
A raster draped on a DTM cannot be deleted: `DELETE /rasters/{id}` → 422 `raster_in_use` with the DTMs.
`POST /wireframes/build-dtm` starts the job `model.dtm` («Построить ЦТМ», `wireframes/jobs.py`).
The geometry for 3D is `/viz/wireframes/{id}` (`viz/router.py`). Numbers may come as text with a comma.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

# numpy and the store load on first use, not at the start of the core (tests/test_process.py).

router = APIRouter(prefix="/wireframes")
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Number = float | str
AttrValue = str | float | int | bool | None
AttrName = Annotated[str, Field(max_length=400)]


def _number(value: Any, label: str) -> float | None:
    """«0,5», «0.5» and «−5» are numbers; empty — None; anything else — 422 `not_a_number`."""
    from bf_core.drillholes.edit import parse_number

    if isinstance(value, str):
        value = value.replace("−", "-")
    return parse_number(value, label)


def _changed(request: Request, wireframe_id: int, **extra: Any) -> None:
    request.app.state.core.events.publish("objects.changed", {"kind": "wireframe", "id": wireframe_id, **extra})


@router.get("")
def list_wireframes(svc: Svc) -> dict[str, Any]:
    from . import store

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"wireframes": store.list_wireframes(conn)}


@router.get("/{wireframe_id}")
def get_wireframe(svc: Svc, wireframe_id: int) -> dict[str, Any]:
    from . import store

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return store.wireframe_info(conn, wireframe_id)


class WireframePatch(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=400)
    color: str | None = Field(default=None, max_length=16)  # null clears the own colour


@router.patch("/{wireframe_id}")
def patch_wireframe(svc: Svc, request: Request, wireframe_id: int, body: WireframePatch) -> dict[str, Any]:
    from . import store

    project = svc.require_current(writable=True)
    given = body.model_fields_set
    changes: dict[str, Any] = {}
    if "name" in given and body.name is not None:
        changes["name"] = body.name
    if "color" in given:
        changes["color"] = body.color
    with dialect.write_tx(project.engine) as conn:
        out = store.update_wireframe(conn, wireframe_id, body.expected_version, changes)
    _changed(request, wireframe_id)
    return out


class AttrsPatch(BaseModel):
    expected_version: int = Field(ge=1)
    rename: dict[AttrName, AttrName] | None = Field(default=None, max_length=200)
    remove: list[AttrName] | None = Field(default=None, max_length=200)
    set: dict[AttrName, AttrValue] | None = Field(default=None, max_length=200)


@router.patch("/{wireframe_id}/attrs")
def patch_attrs(svc: Svc, request: Request, wireframe_id: int, body: AttrsPatch) -> dict[str, Any]:
    """Attributes of the user (ТЗ 7.20): add, change, rename, delete."""
    from . import store

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = store.edit_attrs(
            conn, wireframe_id, body.expected_version, rename=body.rename, remove=body.remove, values=body.set
        )
    _changed(request, wireframe_id)
    return out


class DtmBody(BaseModel):
    expected_version: int = Field(ge=1)
    on: bool | None = None  # false — «Вернуть в каркасы»
    raster_id: int | None = Field(default=None, ge=0)  # 0 or null takes the raster off
    clip: bool | None = None  # «Скрыть данные за ЦТМ»
    tint: str | None = Field(default=None, max_length=16)  # the colour the image is multiplied by
    opacity: Number | None = None  # 0…1: how much of the image covers the colour of the surface
    shade: bool | None = None  # light and shadow of the relief on the image


@router.put("/{wireframe_id}/dtm")
def put_dtm(svc: Svc, request: Request, wireframe_id: int, body: DtmBody) -> dict[str, Any]:
    """`wireframe.dtm` (ТЗ 7.20): only the given settings change."""
    from . import store

    project = svc.require_current(writable=True)
    given = body.model_fields_set - {"expected_version"}
    changes: dict[str, Any] = {key: getattr(body, key) for key in given}
    if changes.get("opacity") is not None:
        changes["opacity"] = _number(changes["opacity"], "Непрозрачность растра")
    with dialect.write_tx(project.engine) as conn:
        out = store.set_dtm(conn, wireframe_id, body.expected_version, changes)
    _changed(request, wireframe_id)
    return out


@router.delete("/{wireframe_id}")
def delete_wireframe(svc: Svc, request: Request, wireframe_id: int) -> dict[str, Any]:
    from . import store

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        gone = store.delete_wireframe(conn, wireframe_id)
    if gone["shas"] and not gone["shared"]:  # the levels of detail of a geometry nobody has any more
        from bf_core.viz import meshes

        meshes.drop_cache(project.folder, meshes.cache_key(tuple(sorted(gone["shas"].items())), gone["render"]))
    _changed(request, wireframe_id, deleted=True)
    return {"deleted": wireframe_id}


class Boundary(BaseModel):
    layer_id: int
    line: int | None = Field(default=None, ge=0)  # the closed line of the layer, from 0; null — the only one
    use_z: bool = False  # the outline is structural: its own heights are kept


class BuildDtmBody(BaseModel):
    name: str | None = Field(default=None, max_length=400)
    layer_ids: list[int] = Field(default_factory=list, max_length=2000)  # layers of points and lines
    set_ids: list[int] = Field(default_factory=list, max_length=2000)  # collars of drillhole sets
    breaklines: bool = True  # lines are structural: their segments are edges of the surface
    boundary: Boundary | None = None
    max_edge: Number | None = None
    to_dtm: bool = True  # «Сразу в раздел ЦТМ»
    color: str | None = Field(default=None, max_length=16)


@router.post("/build-dtm")
def build_dtm(svc: Svc, s: Sched, body: BuildDtmBody) -> dict[str, Any]:
    """`model.dtm`: the job «Построение ЦТМ» makes a wireframe from points, lines and collars."""
    from . import jobs as dtm_jobs

    project = svc.require_current(writable=True)
    max_edge = _number(body.max_edge, "Наибольшая длина ребра")
    with dialect.read_tx(project.engine) as conn:
        params = dtm_jobs.request(
            conn,
            name=body.name,
            layer_ids=body.layer_ids,
            set_ids=body.set_ids,
            breaklines=body.breaklines,
            boundary=None if body.boundary is None else body.boundary.model_dump(),
            max_edge=max_edge,
            to_dtm=body.to_dtm,
            color=body.color,
        )
    key, job_id = s.submit("model.dtm", params, title="Построение ЦТМ")
    return {"source": key, "id": job_id}
