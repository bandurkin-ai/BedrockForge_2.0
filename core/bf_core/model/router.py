"""/api/v1/implicit — «Неявные модели» (ТЗ 8.9; ADR 0026 «API»; подшаг 19.16; the forms — docs/progress/e19-api.md).

The frame of the module: the models, the defaults and the run (`routes.py`), the groups (`routes_groups.py`), the
preview of the points (`routes_points.py`), the outputs, «В каркас» and the deletion (`routes_outputs.py`), the
comparison with the holes (`routes_compare.py`, 19.19), the check and the support map (`routes_check.py`, 19.17).
Security — that of every router of the core
(`api/security.py`: 127.0.0.1, `X-BF-Token`, Host / Origin): the app adds it to all of them. Nothing heavy is imported
here: the routes load numpy and the kernels on the first request.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import routes, routes_check, routes_compare, routes_groups, routes_outputs, routes_points

router = APIRouter(prefix="/implicit")
router.include_router(routes.router)
router.include_router(routes_groups.router)
router.include_router(routes_points.router)
router.include_router(routes_outputs.router)
router.include_router(routes_compare.router)
router.include_router(routes_check.router)
