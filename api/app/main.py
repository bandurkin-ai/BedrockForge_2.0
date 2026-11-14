from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.routers import audit, imports, projects, viz

app = FastAPI(title="Geology API", version=__version__)
app.include_router(imports.router)
app.include_router(projects.router)
app.include_router(audit.router)
app.include_router(viz.router)

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
