"""/api/v1/viz — geometry and data of drillhole sets for the 3D window (ADR 0007 §1).

Binary answers are BFA1 (`viz/binary.py`) with an ETag of their key (`viz/keys.py`): If-None-Match → 304
before any calculation; `Cache-Control: no-cache`, so the window asks again after every change.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

# numpy, numba and the viz modules load on first use, not at the start of the core (tests/test_process.py).

router = APIRouter(prefix="/viz")
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
