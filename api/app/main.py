import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app import mcp_server
from app.config import settings
from app.routers import assistant, audit, blobs, blockmodels, charts, datasets, datatables, exports, imports, intervals, jobs, lines, operations, projects, stats, styles, uploads, viz

mcp_app = mcp_server.http_app()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Local development: job workers inside the API process (production runs app.jobs.worker).
    workers = []
    if settings.jobs_in_api:
        from app.db import SessionLocal
        from app.jobs.worker import QUICK_KINDS, Worker, _ensure_handlers

        # In this thread, before any worker thread starts: no import race between them.
        try:
            _ensure_handlers()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("job handlers cannot be loaded")

        for i in range(settings.jobs_in_api):
            worker = Worker(SessionLocal, name=f"api-{i}")
            worker.start_thread()
            workers.append(worker)
        if settings.jobs_in_api:
            # Operations on lines and wireframes do not wait behind a long import.
            quick = Worker(SessionLocal, name="api-quick", kinds=QUICK_KINDS)
            quick.start_thread()
            workers.append(quick)
    # The MCP endpoint is a mounted sub-application; its session manager needs this app's lifespan.
    try:
        async with mcp_server.mcp.session_manager.run():
            yield
    finally:
        for worker in workers:
            worker.stopping.set()


app = FastAPI(title="BedrockForge API", version=__version__, lifespan=lifespan)
app.include_router(imports.router)
app.include_router(datatables.router)
app.include_router(projects.router)
app.include_router(projects.sets_router)
app.include_router(datasets.router)
app.include_router(lines.router)
app.include_router(operations.router)
app.include_router(audit.router)
app.include_router(viz.router)
app.include_router(stats.router)
app.include_router(assistant.router)
app.include_router(blobs.router)
app.include_router(uploads.router)
app.include_router(jobs.router)
app.include_router(exports.router)
app.include_router(charts.router)
app.include_router(blockmodels.router)
app.include_router(intervals.router)
app.include_router(styles.router)

# Local single-user MVP: the web dev server runs on a different port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """An unexpected failure as JSON: its type, text and the end of the stack go into the web
    application's import/export journal (instead of a bare «Internal Server Error»)."""
    logging.getLogger("app").error("%s %s failed", request.method, request.url.path, exc_info=exc)
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-12:])
    return JSONResponse(
        status_code=500,
        content={"detail": f"Внутренняя ошибка сервера: {type(exc).__name__}: {exc}"[:2000], "trace": trace[-8000:]},
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


# Last, so it only receives what the API routes above did not match: POST /mcp.
app.mount("/", mcp_app)
