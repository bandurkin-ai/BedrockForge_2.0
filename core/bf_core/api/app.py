"""FastAPI application factory. Module routers are listed in api/modules.py."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header
from fastapi.responses import StreamingResponse

import bf_core

from .errors import ApiError, api_error_handler, unexpected_error_handler
from .events import EventBus, format_sse
from .modules import ROUTER_MODULES
from .security import APP_ORIGIN, GuardMiddleware

API_PREFIX = "/api/v1"


@dataclass
class CoreState:
    token: str
    mode: str = "desktop"
    port: int | None = None
    data_dir: str | None = None
    #: Folder of projects (`--projects`; default Документы\BedrockForge\Проекты, ТЗ 6.5).
    projects_dir: str | None = None
    #: Lazily created services of modules (projects, jobs, …), keyed by module.
    services: dict[str, Any] = field(default_factory=dict)
    events: EventBus = field(default_factory=EventBus)
    request_shutdown: Callable[[], None] = field(default=lambda: None)


def discover_routers() -> list[APIRouter]:
    """Import `bf_core.<module>.router` for every module of ROUTER_MODULES."""
    return [importlib.import_module(f"bf_core.{name}.router").router for name in ROUTER_MODULES]


def create_app(state: CoreState, extra_origins: tuple[str, ...] = ()) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        # Close databases before the process exits (WAL checkpoint, locks released): reverse creation order.
        for service in reversed(list(state.services.values())):
            close = getattr(service, "shutdown", None)
            if close:
                close()

    app = FastAPI(title="bf-core", version=bf_core.__version__, docs_url=None, redoc_url=None, lifespan=lifespan)
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
    async def events(last_event_id: Annotated[str | None, Header()] = None) -> StreamingResponse:
        last = int(last_event_id) if last_event_id and last_event_id.isdigit() else None
        queue, unsubscribe = state.events.subscribe(last)

        async def stream() -> AsyncIterator[bytes]:
            try:
                yield b"event: hello\ndata: {}\n\n"
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield b": keep-alive\n\n"
                        continue
                    yield format_sse(item)
            finally:
                unsubscribe()

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    app.include_router(base)
    for router in discover_routers():
        app.include_router(router, prefix=API_PREFIX)
    return app
