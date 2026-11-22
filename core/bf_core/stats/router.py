"""/api/v1/stats — charts, the «Статистика» tab and swath plots (ADR 0019 §10).

`GET /stats/fields?set_id=&table=` — the tables of a set with their numeric fields (components, attributes,
`length` — the interval length) and text fields; with `table` (its name or id) — only it, and its text fields
carry their values (≤ 300, `values` — how many, `more`). Errors: 404 `set_not_found`, `table_not_found`.

The routes of the parts are included here (`routes_charts`, `routes_more`, `routes_tab`, `routes_contact`,
`routes_swath`, `routes_selection`). numpy and the kernels load on the first request, not with this module
(tests/test_process.py: nothing heavy before BF-READY)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter(prefix="/stats")
Svc = Annotated[ProjectService, Depends(projects)]


@router.get("/fields")
def fields(svc: Svc, set_id: int, table: Annotated[str | None, Query(max_length=200)] = None) -> dict[str, Any]:
    from . import source

    project = svc.require_current()
    return source.fields_list(project, set_id, table)


from . import routes_charts  # noqa: E402

router.include_router(routes_charts.router)

from . import routes_more  # noqa: E402

router.include_router(routes_more.router)

from . import routes_swath  # noqa: E402

router.include_router(routes_swath.router)

from . import routes_selection  # noqa: E402

router.include_router(routes_selection.router)

from . import routes_tab  # noqa: E402

router.include_router(routes_tab.router)  # its own prefix /tab

from . import routes_contact  # noqa: E402

router.include_router(routes_contact.router)  # registers "contact" for /stats/chart/{id} too

from . import routes_tab_vario  # noqa: E402

router.include_router(routes_tab_vario.router)  # its own prefix /tab (16.17)
