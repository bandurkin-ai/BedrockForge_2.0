"""/api/v1/resources — «Подсчёт ресурсов» (ТЗ 8.10; ADR 0027 «API»; подшаг 20.7; the forms — docs/progress/e20-api.md).

The frame of the module: the defaults, the run, the reports (`routes.py`), the templates, the export and the routes of
Д-36 / Д-37 (`routes_extra.py`; the drill and the comparison are 501 until 20.9 / 20.10). Security — that of every
router of the core (`api/security.py`: 127.0.0.1, `X-BF-Token`, Host / Origin): the app adds it to all of them.
Nothing heavy is imported here: the routes load numpy and the kernels on the first request.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import routes, routes_extra

router = APIRouter(prefix="/resources")
router.include_router(routes.router)
router.include_router(routes_extra.router)
