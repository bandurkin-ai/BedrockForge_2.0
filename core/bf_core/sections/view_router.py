"""/api/v1/section-series/{id}/… — the sections of the wireframes on a section of a series and their cache
(ADR 0016 §3, §6; ТЗ 8.1.5). Its routes join the router of the series (`router.py`).

`GET …/sections/{k}/wireframes?ids=1,2,3[&seg=j]` — BFA1 `series_section` (`wf_sections.py`): the lines where
the plane t = 0 of section k (from 0; of segment j of a polyline) cuts the wireframes `ids` (≤ 200), bit for bit
those of `GET /viz/wireframes/{id}/section?point=O_k&normal=v̂`; a wireframe that is gone is listed in
`missing`. ETag by the planes of the series, k, seg and the geometry of the wireframes: If-None-Match → 304
before anything is cut; `X-BF-Cache: hit | partial | miss` — how many came from the files of the cache.

`POST …/cache {wireframe_ids, around?}` — the job `sections.cache` (`cache_job.request`): {source, id (null — the
files are there: `ready`), key, axis, first, count, total, missing}; a project open read-only — 423.
`GET …/cache?ids=` — {key, axis, total, ready, first, wireframes, missing, bytes, job} (`cache_job.cache_state`).

A series that is not there — 404 `series_not_found`; a section it has not — 422 `bad_section`; a wrong list of
wireframes — 422 `bad_wireframes`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from . import store
from .base import SeriesError

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
IfNoneMatch = Annotated[str | None, Header()]
Ids = Annotated[str | None, Query(max_length=4000)]
BINARY = "application/octet-stream"


def _errors(fn: Any) -> Any:
    try:
        return fn()
    except SeriesError as e:
        raise ApiError(422, e.code, e.message, e.hint or "Проверьте параметры серии.") from e
    except store.NotFound as e:
        raise ApiError(
            404, "series_not_found", "Серия разрезов не найдена: её удалили.", "Обновите список серий."
        ) from e


@router.get("/{series_id}/sections/{k}/wireframes")
def section_wireframes(
    svc: Svc,
    series_id: int,
    k: int,
    ids: Ids = None,
    seg: Annotated[int | None, Query()] = None,
    if_none_match: IfNoneMatch = None,
) -> Response:
    """BFA1 `series_section`: the lines of the wireframes `ids` on section k (segment `seg` of a polyline)."""
    from bf_core.viz.keys import CACHE_HEADERS, matches

    from . import wf_sections as wf
    from .geometry import series_geometry

    project = svc.require_current()
    wanted = wf.parse_ids(ids)
    with dialect.read_tx(project.engine) as conn:
        r = _errors(lambda: store.row(conn, series_id))
        found, empty, missing = wf.states(conn, wanted)
    geom = _errors(lambda: series_geometry(r.meta if isinstance(r.meta, dict) else {}))
    s = wf.slot_of(geom, k, seg)
    key = wf.series_key(geom)
    tag = wf.answer_etag(project.uid, key, s, found, empty, missing)
    headers = {"ETag": tag, **CACHE_HEADERS}
    if matches(if_none_match, tag):
        return Response(status_code=304, headers=headers)
    body, hits = wf.answer(project, series_id, key, s, found, empty, missing)
    headers["X-BF-Cache"] = "miss" if not hits else ("hit" if hits == len(found) else "partial")
    return Response(content=body, media_type=BINARY, headers=headers)


class CacheBody(BaseModel):
    wireframe_ids: list[int] = Field(max_length=200)
    #: the section (a segment of a polyline) of the window: the middle of the cached ones when K > 500
    around: int | None = Field(default=None, ge=0, le=100_000)


@router.post("/{series_id}/cache")
def cache_series(svc: Svc, sched: Sched, series_id: int, body: CacheBody) -> dict[str, Any]:
    from . import cache_job

    project = svc.require_current(writable=True)
    return _errors(lambda: cache_job.request(sched, project, series_id, body.wireframe_ids, body.around))  # type: ignore[no-any-return]


@router.get("/{series_id}/cache")
def cache_of_series(svc: Svc, series_id: int, ids: Ids = None) -> dict[str, Any]:
    from . import cache_job
    from . import wf_sections as wf

    project = svc.require_current()
    wanted = None if ids is None else wf.parse_ids(ids)
    return _errors(lambda: cache_job.cache_state(project, series_id, wanted))  # type: ignore[no-any-return]
