"""/api/v1/viz — geometry and data of drillhole sets, line layers, point clouds and wireframes for the 3D window
(ADR 0007 §1, ADR 0011 §3, ADR 0014 §2).

Binary answers are BFA1 (`viz/binary.py`) with an ETag of their key (`viz/keys.py`): If-None-Match → 304
before any calculation; `Cache-Control: no-cache`, so the window asks again after every change.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.blocks.viz_routes import router as _blocks_viz
from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

# numpy, numba and the viz modules load on first use, not at the start of the core (tests/test_process.py).

router = APIRouter(prefix="/viz")
router.include_router(_blocks_viz)
Svc = Annotated[ProjectService, Depends(projects)]
Method = Annotated[str, Query(max_length=40)]
IfNoneMatch = Annotated[str | None, Header()]
BINARY = "application/octet-stream"


def _method(method: str) -> str:
    from bf_core.drillholes.desurvey import METHODS

    from .keys import bad_method

    if method not in METHODS:
        raise bad_method()
    return method


def _answer(tag: str, if_none_match: str | None, body: Any) -> Response:
    from .keys import CACHE_HEADERS, matches

    headers = {"ETag": tag, **CACHE_HEADERS}
    if matches(if_none_match, tag):
        return Response(status_code=304, headers=headers)
    return Response(content=body(), media_type=BINARY, headers=headers)


@router.get("/sets/{set_id}/holes")
def holes(svc: Svc, set_id: int, method: Method = "minimum_curvature", if_none_match: IfNoneMatch = None) -> Response:
    """The hole manifest: parts, names, collars, TD, length, plan azimuth, kind, audit colour."""
    from .holes import audit_stamp, manifest
    from .keys import etag, set_state

    project = svc.require_current()
    method = _method(method)
    with dialect.read_tx(project.engine) as conn:
        state = set_state(conn, set_id)
        run = audit_stamp(conn, set_id)
    return _answer(
        etag("holes", project.uid, state, method, run), if_none_match, lambda: manifest(project, state, method)
    )


@router.get("/sets/{set_id}/paths/{k}")
def paths(
    svc: Svc,
    set_id: int,
    k: int,
    method: Method = "minimum_curvature",
    tol: Annotated[float, Query()] = 0.01,
    if_none_match: IfNoneMatch = None,
) -> Response:
    """Paths of part k: the stations and the arc points within `tol` metres."""
    from .arcs import MAX_TOL, MIN_TOL
    from .holes import paths_etag
    from .keys import set_state
    from .paths import part_paths

    project = svc.require_current()
    method = _method(method)
    if not MIN_TOL <= tol <= MAX_TOL:
        raise ApiError(
            422,
            "bad_tolerance",
            "Допуск линии ствола вне пределов.",
            f"Укажите допуск от {MIN_TOL:g} до {MAX_TOL:g}".replace(".", ",") + " м.",
        )
    with dialect.read_tx(project.engine) as conn:
        state = set_state(conn, set_id)
    tag = paths_etag(project.uid, state, method, tol, k)
    return _answer(tag, if_none_match, lambda: part_paths(project, state, method, tol, k))


def _field_names(fields: str | None) -> list[str]:
    from .intervals import MAX_FIELDS

    names = list(dict.fromkeys(f.strip() for f in (fields or "").split(",") if f.strip()))
    if len(names) > MAX_FIELDS:
        raise ApiError(
            422, "too_many_fields", f"Полей больше {MAX_FIELDS}.", f"Выберите не больше {MAX_FIELDS} полей таблицы."
        )
    return names


@router.get("/sets/{set_id}/intervals/{k}")
def intervals(
    svc: Svc,
    set_id: int,
    k: int,
    table: Annotated[str, Query(min_length=1, max_length=200)],
    fields: Annotated[str | None, Query(max_length=4000)] = None,
    method: Method = "minimum_curvature",
    if_none_match: IfNoneMatch = None,
) -> Response:
    """Rows of the holes of part k: depths and the values of up to 16 fields."""
    from .intervals import part_intervals
    from .keys import etag, fields_of, set_state, table_by_key

    project = svc.require_current()
    method = _method(method)
    names = _field_names(fields)
    with dialect.read_tx(project.engine) as conn:
        state = set_state(conn, set_id)
        t = table_by_key(conn, set_id, table)
        refs = fields_of(conn, t, names)
    tag = etag("intervals", project.uid, state, method, t.id, t.rev, t.stamp, [f.key for f in refs], k)
    return _answer(tag, if_none_match, lambda: part_intervals(project, state, method, t, refs, k))


@router.get("/sets/{set_id}/field-stats")
def field_stats(
    svc: Svc,
    set_id: int,
    table: Annotated[str, Query(min_length=1, max_length=200)],
    field: Annotated[str, Query(min_length=1, max_length=300)],
) -> dict[str, Any]:
    """Count, missing, range, quantiles, histograms or categories of one field over all rows."""
    from .intervals import snapshot
    from .keys import fields_of, set_state, table_by_key
    from .stats import field_stats as calc

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        set_state(conn, set_id)
        t = table_by_key(conn, set_id, table)
        (ref,) = fields_of(conn, t, [field])
    snap = snapshot(project, set_id, t)
    if ref.key not in snap.data:
        from .intervals import data_changed

        raise data_changed()
    return calc(snap, t, ref)


class ClassBody(BaseModel):
    table: str = Field(min_length=1, max_length=200)
    field: str = Field(min_length=1, max_length=300)
    bounds: list[float] = Field(default_factory=list, max_length=1000)


@router.post("/sets/{set_id}/class-counts")
def class_counts(svc: Svc, set_id: int, body: ClassBody) -> dict[str, Any]:
    """Number and length of the rows in each class of the bounds, by all the data of the table."""
    from .intervals import data_changed, snapshot
    from .keys import fields_of, set_state, table_by_key
    from .stats import check_bounds
    from .stats import class_counts as calc

    project = svc.require_current()
    bounds = check_bounds(body.bounds)
    with dialect.read_tx(project.engine) as conn:
        set_state(conn, set_id)
        t = table_by_key(conn, set_id, body.table)
        (ref,) = fields_of(conn, t, [body.field])
    snap = snapshot(project, set_id, t)
    if ref.key not in snap.data:
        raise data_changed()
    return calc(snap, t, ref, bounds)


# --- line and point layers (ADR 0011 §3) ---------------------------------------------------------------


@router.get("/lines/{layer_id}")
def line_layer(svc: Svc, layer_id: int, if_none_match: IfNoneMatch = None) -> Response:
    """The manifest of a line layer: its extent, the levels of detail and the parts with their ETags. A point
    cloud (ADR 0014 §2) answers at the same address: its parts as stored, five levels, classes, colour modes."""
    from . import cloud, lines

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        state = lines.layer_state(conn, layer_id)
        points = cloud.view(conn, state)
    if points is not None:
        return _answer(
            cloud.layer_etag(project.uid, state), if_none_match, lambda: cloud.manifest(project, state, points)
        )
    return _answer(lines.layer_etag(project.uid, state), if_none_match, lambda: lines.manifest(project, state))


@router.get("/lines/{layer_id}/parts/{k}")
def line_part(
    svc: Svc,
    layer_id: int,
    k: int,
    lod: Annotated[int, Query()] = 0,
    color: Annotated[str | None, Query(max_length=16)] = None,
    if_none_match: IfNoneMatch = None,
) -> Response:
    """Part k of a line layer at a level of detail: 0 — every vertex, 1 and 2 — within 4 and 32 m. Of a point
    cloud: levels 0…4 (every point; one in a cell of 0.5, 2, 8, 32 m) and `color` — what the `value` of a point
    is: `class` (the default), `h` (height above the ground) or `dz` (Z − DTM)."""
    from . import cloud, lines

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        state = lines.layer_state(conn, layer_id)
        points = cloud.view(conn, state)
    if points is not None:
        mode = cloud.check(state, points, k, lod, color)
        tag = cloud.part_etag(project.uid, state, k, lod, mode)
        return _answer(tag, if_none_match, lambda: cloud.part(project, state, points, k, lod, mode))
    if lod not in range(len(lines.LOD_TOL)):
        raise lines.bad_lod()
    tag = lines.part_etag(project.uid, state, k, lod)
    return _answer(tag, if_none_match, lambda: lines.part(project, state, k, lod))


# --- wireframes and DTMs (ADR 0011 §3) -----------------------------------------------------------------


@router.get("/wireframes/{wireframe_id}")
def wireframe(svc: Svc, wireframe_id: int, if_none_match: IfNoneMatch = None) -> Response:
    """The manifest of a wireframe: colour, DTM settings, extent, levels of detail, parts with their ETags."""
    from . import meshes

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        state = meshes.mesh_state(conn, wireframe_id)
    return _answer(meshes.manifest_etag(project.uid, state), if_none_match, lambda: meshes.manifest(project, state))


@router.get("/wireframes/{wireframe_id}/parts/{k}")
def wireframe_part(
    svc: Svc,
    request: Request,
    wireframe_id: int,
    k: int,
    lod: Annotated[int, Query()] = 0,
    if_none_match: IfNoneMatch = None,
) -> Response:
    """Part k of a wireframe at a level of detail: 0 — every triangle, 1 and 2 — about 1/8 and 1/64 of them.
    A level not made yet — the whole part as a stand-in (`standin` in its header, no ETag, `no-store`) and the
    job `wireframe.lod` asked for (ADR 0015 §8): the event `wireframes.lod` tells when the levels are there."""
    from . import meshes
    from .keys import CACHE_HEADERS, matches

    project = svc.require_current()
    if lod not in range(meshes.gm.LOD_LEVELS):
        raise meshes.bad_lod()
    with dialect.read_tx(project.engine) as conn:
        state = meshes.mesh_state(conn, wireframe_id)
    tag = meshes.part_etag(project.uid, state, k, lod)
    if matches(if_none_match, tag):
        return Response(status_code=304, headers={"ETag": tag, **CACHE_HEADERS})
    body, standin = meshes.part_answer(project, state, k, lod)
    if not standin:
        return Response(content=body, media_type=BINARY, headers={"ETag": tag, **CACHE_HEADERS})
    from bf_core.jobs.service import jobs_of
    from bf_core.wireframes import lod_job

    lod_job.request(jobs_of(request.app.state.core), project, state, (k, lod))
    return Response(content=body, media_type=BINARY, headers={"Cache-Control": "no-store"})


# --- exact queries on wireframes (ADR 0013 §5, ADR 0007 §5; `wireframes/exact.py`) -------------------------

Text = Annotated[str | None, Query(max_length=200)]
Plane = Annotated[str | None, Query(max_length=1200)]  # 40 numbers of `plane=` (viz/snap.py)


@router.get("/wireframes/{wireframe_id}/section")
def wireframe_section(
    svc: Svc,
    wireframe_id: int,
    point: Text = None,
    normal: Text = None,
    x0: Text = None,
    y0: Text = None,
    az: Text = None,
    length: Text = None,
    offset: Text = None,
    if_none_match: IfNoneMatch = None,
) -> Response:
    """BFA1 `section`: the lines where a plane cuts the wireframe, float64 project coordinates (ТЗ 7.14). The
    plane — `point=x,y,z` and `normal=x,y,z`, or a section line `x0`, `y0`, `az` with `length` (the lines are
    cut at its ends) and `offset` (the plane moved along the normal)."""
    from bf_core.wireframes import exact

    from . import meshes

    project = svc.require_current()
    plane = exact.parse_plane(point, normal, x0, y0, az, length, offset)
    with dialect.read_tx(project.engine) as conn:
        state = meshes.mesh_state(conn, wireframe_id)
    tag = exact.section_etag(project.uid, state, plane)
    return _answer(tag, if_none_match, lambda: exact.section(project, state, plane))


@router.get("/sets/{set_id}/intersections")
def intersections(
    svc: Svc,
    set_id: int,
    wireframes: Annotated[str | None, Query(max_length=40_000)] = None,
    method: Method = "minimum_curvature",
    if_none_match: IfNoneMatch = None,
) -> Response:
    """BFA1 `intersections` (ADR 0007 §5): where the holes of the set cross the wireframes `wireframes=1,2,3`
    (no parameter — every wireframe of the project), exactly by the triangles; depths to 1 mm and better."""
    from bf_core.wireframes import exact

    from .keys import set_state

    project = svc.require_current()
    method = _method(method)
    ids = exact.parse_ids(wireframes)
    with dialect.read_tx(project.engine) as conn:
        state = set_state(conn, set_id)
        targets = exact.set_targets(conn, ids)
    tag = exact.intersections_etag(project.uid, state, method, targets)
    return _answer(tag, if_none_match, lambda: exact.intersections(project, state, method, targets))


@router.get("/sets/{set_id}/snap")
def snap_targets(
    svc: Svc,
    set_id: int,
    section: Text = None,
    bbox: Text = None,
    plane: Plane = None,
    method: Method = "minimum_curvature",
    if_none_match: IfNoneMatch = None,
) -> Response:
    """BFA1 `snap` (ADR 0015 §4): collars, paths and the bounds of intervals of the holes in the window of a
    section (`section=x0,y0,az,towards,away[,length]`), of any plane of a series of sections
    (`plane=ox,oy,oz,vx,vy,vz,towards,away[,nx,ny,nz,d]…`, ADR 0016 §3) or the extent of the view (`bbox=`),
    float64 — the exact snap targets of the string editor; over 5 000 holes or 2 million points — `truncated`,
    collars only."""
    from .keys import set_state
    from .snap import build, parse_window, snap_etag

    project = svc.require_current()
    method = _method(method)
    window = parse_window(section, bbox, plane)
    with dialect.read_tx(project.engine) as conn:
        state = set_state(conn, set_id)
    tag = snap_etag(project.uid, state, method, window)
    return _answer(tag, if_none_match, lambda: build(project, state, method, window))


# --- the window «Данные по скважинам» (ТЗ 7.17, ADR 0007 §6) -----------------------------------------------


@router.get("/sets/{set_id}/hole-names")
def hole_names(svc: Svc, set_id: int) -> dict[str, Any]:
    """Names of the holes of the set in its order (the hole list of the window)."""
    from .keys import set_state
    from .log import hole_names as names

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        set_state(conn, set_id)
        return {"names": names(conn, set_id)}


class LogTable(BaseModel):
    table: str = Field(min_length=1, max_length=200)  # name_key or id of the table
    fields: list[Annotated[str, Field(max_length=300)]] | None = Field(default=None, max_length=64)


class LogBody(BaseModel):
    holes: list[Annotated[str, Field(max_length=200)]] = Field(max_length=1000)
    tables: list[LogTable] = Field(default_factory=list, max_length=64)
    from_: float | None = Field(default=None, alias="from")
    to: float | None = None


@router.post("/sets/{set_id}/log")
def hole_log(svc: Svc, set_id: int, body: LogBody) -> dict[str, Any]:
    """Depths and values of the chosen tables for up to 30 holes; null — empty."""
    from .keys import set_state
    from .log import check, log, resolve

    project = svc.require_current()
    check(body.holes, len(body.tables), body.from_, body.to)
    with dialect.read_tx(project.engine) as conn:
        set_state(conn, set_id)
        tables = [resolve(conn, set_id, t.table, t.fields) for t in body.tables]
        return log(conn, set_id, body.holes, tables, body.from_, body.to)
