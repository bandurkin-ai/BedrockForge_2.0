"""FastAPI application factory. Module routers are listed in api/modules.py."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from fastapi import APIRouter, FastAPI
from fastapi.responses import StreamingResponse

import bf_core

from .errors import ApiError, api_error_handler, unexpected_error_handler
from .modules import ROUTER_MODULES
from .security import APP_ORIGIN, GuardMiddleware

API_PREFIX = "/api/v1"


@dataclass
class CoreState:
    token: str
    mode: str = "desktop"
    port: int | None = None
    data_dir: str | None = None
    request_shutdown: Callable[[], None] = field(default=lambda: None)


def discover_routers() -> list[APIRouter]:
    """Import `bf_core.<module>.router` for every module of ROUTER_MODULES."""
    return [importlib.import_module(f"bf_core.{name}.router").router for name in ROUTER_MODULES]


def create_app(state: CoreState, extra_origins: tuple[str, ...] = ()) -> FastAPI:
    app = FastAPI(title="bf-core", version=bf_core.__version__, docs_url=None, redoc_url=None)
    app.state.core = state
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    origins = (APP_ORIGIN, *extra_origins)
    app.add_middleware(GuardMiddleware, token=state.token, allowed_origins=origins, port=state.port)

    base = APIRouter(prefix=API_PREFIX)

    @base.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": bf_core.__version__, "mode": state.mode}

    @base.post("/shutdown")
    def shutdown() -> dict[str, str]:
        state.request_shutdown()
        return {"status": "stopping"}

    @base.get("/events")
    async def events() -> StreamingResponse:
        async def stream() -> AsyncIterator[bytes]:
            yield b"event: hello\ndata: {}\n\n"
            while True:
                await asyncio.sleep(15)
                yield b": keep-alive\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    app.include_router(base)
    for router in discover_routers():
        app.include_router(router, prefix=API_PREFIX)
    return app
