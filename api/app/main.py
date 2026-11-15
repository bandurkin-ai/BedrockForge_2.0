from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app import mcp_server
from app.config import settings
from app.routers import assistant, audit, blobs, charts, datasets, imports, jobs, projects, stats, uploads, viz

mcp_app = mcp_server.http_app()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Local development: job workers inside the API process (production runs app.jobs.worker).
    workers = []
    if settings.jobs_in_api:
        from app.db import SessionLocal
        from app.jobs.worker import Worker

        for i in range(settings.jobs_in_api):
            worker = Worker(SessionLocal, name=f"api-{i}")
            worker.start_thread()
            workers.append(worker)
    # The MCP endpoint is a mounted sub-application; its session manager needs this app's lifespan.
    try:
        async with mcp_server.mcp.session_manager.run():
            yield
    finally:
        for worker in workers:
            worker.stopping.set()


app = FastAPI(title="BedrockForge API", version=__version__, lifespan=lifespan)
app.include_router(imports.router)
app.include_router(projects.router)
app.include_router(projects.sets_router)
app.include_router(datasets.router)
app.include_router(audit.router)
app.include_router(viz.router)
app.include_router(stats.router)
app.include_router(assistant.router)
app.include_router(blobs.router)
app.include_router(uploads.router)
app.include_router(jobs.router)
app.include_router(charts.router)

# Local single-user MVP: the web dev server runs on a different port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


# Last, so it only receives what the API routes above did not match: POST /mcp.
app.mount("/", mcp_app)
