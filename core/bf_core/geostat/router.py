"""/api/v1/geostat — «Вариография» and the trends (ТЗ 8.6; ADR 0022 §14; the forms — docs/progress/e16-api.md).

The frame of the module (подшаг 16.13): the stored variogram models (`variogram/routes_model.py`), the trends
(`routes_trend.py`, 16.16). The jobs `variography.experimental`, `variography.map` and the previews (16.14–16.15)
are included here by their subrouters. Security — that of every router of the core (`api/security.py`:
127.0.0.1, `X-BF-Token`, Host / Origin): the app adds it to all of them.
"""

from __future__ import annotations

from fastapi import APIRouter

from .routes_trend import router as trends_router
from .variogram.routes import router as calc_router
from .variogram.routes_model import router as models_router

router = APIRouter(prefix="/geostat")
router.include_router(models_router)
router.include_router(trends_router)
router.include_router(calc_router)
