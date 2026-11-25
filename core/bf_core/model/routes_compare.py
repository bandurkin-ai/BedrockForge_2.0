"""`POST /implicit/models/{id}/compare_holes` — «Сверка данных и модели» (ТЗ 7.35 Д-33; e19-api.md §8.3).

Body `{set_id, table, field, page?, size?, channel?, seq?}`; the answer — see `compare_holes` (the matrices «код
данных × код модели» by length and by count, the list of the intervals that do not agree, longest first, by pages,
with the hole and the depth for the 7.17 window). A synchronous calculation in one of the HEAVY slots; a request with
a newer `seq` of its `channel` overtakes this one (409 `superseded`). Nothing is written, so it works in a project
«только просмотр»; 409 `no_project`, `model_building`, `not_built`; 404 `set_not_found`, `model_not_found`,
`table_not_found`; 422 `bad_param` {param}, `no_bodies`, `wireframe_open`.

`model/router.py` (подшаг 19.16) includes `router` of this module (the paths are relative to `/implicit`):

    from . import routes_compare
    router.include_router(routes_compare.router)
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends

from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Payload = Annotated[Any, Body()]


@router.post("/models/{model_id}/compare_holes")
def compare_holes(svc: Svc, model_id: int, body: Payload) -> dict[str, Any]:
    from . import compare_holes as ch

    req = ch.parse(body)
    return ch.run(svc.require_current(), model_id, req)
