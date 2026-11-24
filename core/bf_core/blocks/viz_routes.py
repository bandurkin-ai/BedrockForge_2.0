"""The display of a block model: BFA1 `blocks`, `blocks_slice`, the block under a point, the polygons of a section
(подшаг 17.27; ТЗ 8.7.6, ADR 0023 §11, decisions 16–19; e17-api §12).

Two routers, which `viz/router.py` and `blocks/router.py` include (the lines are in the report of 17.27):

`router` (under `/viz`):
  * `POST /viz/blocks/{id}/shell` {filter_id?, restrict?, colour {field, kind, from, to, log, breaks, hidden}, mode:
    "shell" | "all", extent?, limit?, channel?, seq?} → BFA1 `blocks` (≤ 3 s at 20 million blocks);
  * `POST /viz/blocks/{id}/slice` {filter_id?, restrict?, plane {origin, normal}, field?, hidden?, max_px?, channel?,
    seq?} → BFA1 `blocks_slice` (≤ 1 s);
  * `POST /viz/blocks/{id}/at` {point, normal?, filter_id?, restrict?, fields?} → JSON `{found, row, p, ijk, sub,
    centre, size, passed, f, values}` (≤ 100 ms from the cache of columns).
`polygons_router` (under `/blocks`):
  * `POST /blocks/{id}/slice/polygons` {plane, filter_id?, restrict?, field?, limit?} → {polygons [{row, value, ring}],
    count, area, truncated} (the print of Э22).

The shell and the slice are calculations in one of the HEAVY slots of the charts; `channel` (a string of 1–64
characters) and `seq` (≥ 0) make a request overtake an older one of its channel: the older one answers 409
`superseded`. Nothing here writes: all of it works in a project «только просмотр». Errors are `{code, message, hint,
param?}` in Russian: 404 `block_model_not_found`, `block_field_not_found`, `filter_not_found`; 422 `bad_param {param}`,
`filter_field_missing`, `filter_rules`; 409 `superseded`, `block_model_broken`. A plain `def` route runs in the thread
pool; numpy and the kernels load on the first request (`viz_data`, `viz_slice`, `viz_spec`).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import Response

from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
polygons_router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Payload = Annotated[Any, Body()]
BINARY = "application/octet-stream"
NO_CACHE = {"Cache-Control": "no-cache"}


@router.post("/blocks/{bm_id}/shell")
def shell(svc: Svc, bm_id: int, body: Payload) -> Response:
    from . import viz_data as vd
    from . import viz_spec as sp

    spec = sp.parse_view(body)
    project = svc.require_current()
    data = sp.overtaken(spec.common, lambda tok: vd.shell_bytes(project, bm_id, spec, tok))
    return Response(content=data, media_type=BINARY, headers=NO_CACHE)


@router.post("/blocks/{bm_id}/slice")
def slice_(svc: Svc, bm_id: int, body: Payload) -> Response:
    from . import viz_slice as vs
    from . import viz_spec as sp

    spec = vs.parse_slice(body)
    project = svc.require_current()
    data = sp.overtaken(spec.common, lambda tok: vs.slice_bytes(project, bm_id, spec, tok))
    return Response(content=data, media_type=BINARY, headers=NO_CACHE)


@router.post("/blocks/{bm_id}/at")
def at(svc: Svc, bm_id: int, body: Payload) -> dict[str, Any]:
    from . import viz_slice as vs

    project = svc.require_current()
    return vs.at_answer(project, bm_id, body)


@polygons_router.post("/{bm_id}/slice/polygons")
def polygons(svc: Svc, bm_id: int, body: Payload) -> dict[str, Any]:
    from . import viz_slice as vs
    from . import viz_spec as sp

    spec = vs.parse_slice(body)
    project = svc.require_current()
    return sp.overtaken(spec.common, lambda tok: vs.polygons_answer(project, bm_id, spec, tok))
