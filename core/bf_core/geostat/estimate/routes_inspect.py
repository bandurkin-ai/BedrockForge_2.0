"""POST /api/v1/geostat/estimate/inspect — the inspector of a block (ТЗ 7.35 Д-30; Э18, 18.Д3; e18-api.md §7).

    POST /estimate/inspect  {model_id, block: [i, j, k] | xyz: [x, y, z] | row, params_id?, channel?, seq?}
        → {model_id, model_version, block, results: [...], warnings}   (the form — `inspect.py`)

Synchronous, in a HEAVY slot of the charts, ≤ 200 мс with a warm cache of the compiled row; `channel` (a string of 1–64
characters) and `seq` (an integer ≥ 0) make a request overtake an older one of its channel: the older one answers 409
`superseded`. Nothing here writes: it works in a project «только просмотр». Without `params_id` — every row of the
estimation plan whose fields the model has and whose domain holds the block. Errors `{code, message, hint, param?}` in
Russian: 404 `block_model_not_found`, `estimation_not_found`; 409 `not_estimated`, `source_lost`, `superseded`; 422
`bad_param {param: block | xyz | row | params_id | model_id | channel | seq}`.

The router is included by `geostat/router.py` (the line is in the report of 18.Д3).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Payload = Annotated[Any, Body()]


@router.post("/estimate/inspect")
def post_inspect(svc: Svc, body: Payload) -> JSONResponse:
    from bf_core.blocks import viz_spec as vs

    from . import inspect as ins
    from .spec import parse_inspect

    req = parse_inspect(body)
    common = vs.parse_common(body)
    project = svc.require_current()

    def work(tok: vs.Token) -> dict[str, Any]:
        with vs.heavy(tok):
            return ins.run(project, req, tok)

    return JSONResponse(vs.overtaken(common, work))
