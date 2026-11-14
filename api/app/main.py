from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app import mcp_server
from app.routers import assistant, audit, imports, projects, viz

mcp_app = mcp_server.http_app()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # The MCP endpoint is a mounted sub-application; its session manager needs this app's lifespan.
    async with mcp_server.mcp.session_manager.run():
        yield


app = FastAPI(title="BedrockForge API", version=__version__, lifespan=lifespan)
app.include_router(imports.router)
app.include_router(projects.router)
app.include_router(audit.router)
app.include_router(viz.router)
app.include_router(assistant.router)

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
