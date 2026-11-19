"""/api/v1/section-series — series of sections (ТЗ 8.1; ADR 0016 §3).

`GET /section-series` — the list (with `limit`); `POST /section-series {name?, folder?, params}` — a new series
(the next number); `GET / PATCH / DELETE /section-series/{id}` — one series, a change of its name, folder or
parameters (`expected_version`; a stale one is a 409 `version_conflict` with the current series), deleting it (its
contours stay); `POST /section-series/{id}/copy {name?}` — the same parameters under a new number;
`GET /section-series/{id}/table` — `{version, count, sections: [{k, name, s, origin, towards, away}]}` with an
ETag by the version (If-None-Match → 304).

Wrong parameters are a 422 `bad_series` with a hint; a project open read-only — 423. Every change publishes
`objects.changed {kind: "section_series", id[, deleted]}`. The sections of wireframes, the cache and the contours
of a series are in `view_router.py` and `contours_router.py`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from . import store
from .schema import KIND, SeriesError

router = APIRouter(prefix="/section-series")
Svc = Annotated[ProjectService, Depends(projects)]
IfNoneMatch = Annotated[str | None, Header()]
CACHE_HEADERS = {"Cache-Control": "no-cache"}


def _changed(request: Request, series_id: int, **extra: Any) -> None:
    request.app.state.core.events.publish("objects.changed", {"kind": KIND, "id": series_id, **extra})


def _errors(fn: Any) -> Any:
    try:
        return fn()
    except SeriesError as e:
        raise ApiError(422, "bad_series", str(e), e.hint) from e
    except store.NotFound as e:
        raise ApiError(
            404, "series_not_found", "Серия разрезов не найдена: её удалили.", "Обновите список серий."
        ) from e
    except store.Conflict as e:
        raise ApiError(
            409,
            "version_conflict",
            "Серию разрезов только что изменили в другом окне.",
            "Параметры серии обновлены — проверьте их и повторите изменение.",
            extra={"version": e.version, "series": e.series},
        ) from e


@router.get("")
def list_series(svc: Svc) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"series": store.list_series(conn), "limit": store.MAX_SERIES}


class SeriesBody(BaseModel):
    params: dict[str, Any]
    name: str | None = Field(default=None, max_length=200)
    folder: str | None = Field(default=None, max_length=200)


@router.post("")
def create_series(svc: Svc, request: Request, body: SeriesBody) -> dict[str, Any]:
    from bf_core.drillholes.edit import author

    project = svc.require_current(writable=True)

    def run() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            return store.create(conn, body.params, body.name, body.folder, author())

    out: dict[str, Any] = _errors(run)
    _changed(request, out["id"])
    return out


@router.get("/{series_id}")
def get_series(svc: Svc, series_id: int) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return _errors(lambda: store.get(conn, series_id))  # type: ignore[no-any-return]


class SeriesUpdate(BaseModel):
    expected_version: int = Field(ge=0)
    name: str | None = Field(default=None, max_length=200)
    folder: str | None = Field(default=None, max_length=200)
    #: the fields to change; the others stay (P2 and the azimuth exclude each other)
    params: dict[str, Any] | None = None


@router.patch("/{series_id}")
def update_series(svc: Svc, request: Request, series_id: int, body: SeriesUpdate) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    folder = body.folder if "folder" in body.model_fields_set else ...

    def run() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            return store.update(
                conn, series_id, body.expected_version, name=body.name, folder=folder, params=body.params
            )

    out: dict[str, Any] = _errors(run)
    _changed(request, series_id)
    return out


@router.delete("/{series_id}")
def delete_series(svc: Svc, request: Request, series_id: int) -> dict[str, Any]:
    project = svc.require_current(writable=True)

    def run() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            store.delete(conn, series_id)
        return {"deleted": series_id}

    out: dict[str, Any] = _errors(run)
    _changed(request, series_id, deleted=True)
    return out


class CopyBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)


@router.post("/{series_id}/copy")
def copy_series(svc: Svc, request: Request, series_id: int, body: CopyBody | None = None) -> dict[str, Any]:
    from bf_core.drillholes.edit import author

    project = svc.require_current(writable=True)

    def run() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            return store.copy(conn, series_id, body.name if body else None, author())

    out: dict[str, Any] = _errors(run)
    _changed(request, out["id"])
    return out


@router.get("/{series_id}/table")
def series_table(svc: Svc, series_id: int, if_none_match: IfNoneMatch = None) -> Response:
    """The sections of the series: number k (from 0), name, s_k, O_k [x, y, z] and the window of each."""
    from bf_core.viz.keys import matches

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        r = _errors(lambda: store.row(conn, series_id))
    tag = store.table_etag(project.uid, r)
    headers = {"ETag": tag, **CACHE_HEADERS}
    if matches(if_none_match, tag):
        return Response(status_code=304, headers=headers)
    return JSONResponse(_errors(lambda: store.table(r)), headers=headers)
