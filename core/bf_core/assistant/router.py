"""/api/v1/assistant — the Claude assistant (ТЗ 7.32; ADR 0029; подшаг 23.11; the forms — docs/progress/e23-api.md).

The state, the connection, the login and the usage — `routes.py`; the dialogs and the turns (SSE) — `routes_dialogs.py`.
Security — that of every router of the core (`api/security.py`: 127.0.0.1, `X-BF-Token`, Host / Origin); the routes of
the secret check that the request has no `Origin` (the main process of the app, not the window). Nothing heavy is
imported here: the first request loads the SDK, the loop and the database.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import routes, routes_dialogs

router = APIRouter(prefix="/assistant")
router.include_router(routes.router)
router.include_router(routes_dialogs.router)
