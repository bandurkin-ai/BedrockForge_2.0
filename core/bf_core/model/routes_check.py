"""`GET /implicit/models/{id}/check` and `POST /implicit/models/{id}/support` — the check of a model against its data
(ТЗ 7.35 Д-32; e19-api.md §8.1, §8.2): the table of the deviations at the data points and the map of the support.

`GET …/check?group&page=1&size=500&sort=residual|source&format=json|csv&highlight=` — see `check.page`; `format=csv`
(or `…/check.csv`) is the file of all the rows, `highlight` (m) the threshold of the flag `over` (default 0,1·h).
`POST …/support {wireframe_id?, threshold?, format? json|f4}` — see `check_support`; with `format: "f4"` and a
`wireframe_id` the body of the answer is the float32 little-endian distances of the vertices, the rest of the answer
(the entry of the surface, the threshold, the warnings) is the JSON in the header `X-BF-Support`.

Nothing is written but `support_share` of the statistics of the model (the version stays), so both work in a project
«только просмотр». 404 `model_not_found`, `group_not_found`, `wireframe_not_found`; 409 `model_building`, `not_built`;
422 `bad_param` {param}.

`model/router.py` (подшаг 19.16) includes `router` of this module (the paths are relative to `/implicit`):

    from . import routes_check
    router.include_router(routes_check.router)
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse, Response

from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Payload = Annotated[Any, Body()]
WHOLE = re.compile(r"^\d+$")


def _query(params: Mapping[str, str]) -> dict[str, Any]:
    """The query as `spec.parse_check` reads it: the numbers are numbers (a text that is not one stays a text and is
    refused there)."""
    out: dict[str, Any] = dict(params)
    for key in ("group", "page", "size"):
        if isinstance(out.get(key), str) and WHOLE.match(out[key]):
            out[key] = int(out[key])
    return out


def _highlight(raw: Any) -> float | None:
    from . import store_base as sb

    if raw is None or raw == "":
        return None
    try:
        value = float(str(raw).replace(",", "."))
    except ValueError:
        value = -1.0
    if not 0.0 <= value < float("inf"):
        raise sb.bad("highlight", "Порог подсветки — число метров, не меньше нуля.", "Задайте порог невязки в метрах.")
    return value


def _check(svc: ProjectService, model_id: int, params: Mapping[str, str], fmt: str | None) -> Response:
    from . import check as ck
    from .spec import parse_check

    q = _query(params)
    if fmt is not None:
        q["format"] = fmt
    cs = parse_check(q)
    hl = _highlight(q.get("highlight"))
    loaded = ck.load(svc.require_current(), model_id)
    if cs.format == "csv":
        name = quote(f"Проверка модели № {model_id}.csv")
        return Response(
            ck.csv_bytes(loaded, cs, hl),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}", "Cache-Control": "no-store"},
        )
    return JSONResponse(ck.page(loaded, cs, hl))


@router.get("/models/{model_id}/check", response_model=None)
def check(svc: Svc, model_id: int, request: Request) -> Response:
    return _check(svc, model_id, request.query_params, None)


@router.get("/models/{model_id}/check.csv", response_model=None)
def check_csv(svc: Svc, model_id: int, request: Request) -> Response:
    return _check(svc, model_id, request.query_params, "csv")


@router.post("/models/{model_id}/support", response_model=None)
def support(svc: Svc, model_id: int, body: Payload = None) -> Response:
    from . import check_support as cs

    req = cs.parse(body)
    res = cs.run(svc.require_current(), model_id, req)
    if req.format == "f4" and res.values is not None:
        head = {
            "threshold": res.out["threshold"],
            "surface": res.summary,
            "warnings": res.out["warnings"],
            "recorded": res.out["recorded"],
        }
        return Response(
            res.values.astype("<f4").tobytes(),
            media_type="application/octet-stream",
            headers={"X-BF-Support": json.dumps(head, ensure_ascii=True), "Cache-Control": "no-store"},
        )
    return JSONResponse(res.out)
