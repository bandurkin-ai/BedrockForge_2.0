"""/api/v1/pit — «Оптимизация карьера» (ТЗ 8.11; ADR 0028 «API»; подшаг 21.11; the forms —
docs/progress/e21-api.md).

The frame of the module: the form, the readiness, the check of the template, the scenarios and the run
(`routes.py`), the template, the report, the surfaces, the solid and the comparison (`routes_extra.py`; the
surfaces and the solid answer 501 until their jobs of 21.12 are registered). Security — that of every router of the
core (`api/security.py`: 127.0.0.1, `X-BF-Token`, Host / Origin): the app adds it to all of them. Nothing heavy is
imported here: the routes load numpy and the kernels on the first request.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import routes, routes_extra

router = APIRouter(prefix="/pit")
router.include_router(routes.router)
router.include_router(routes_extra.router)
