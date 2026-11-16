"""/api/v1/projects — the «Проекты» window, the project selector and the commands project.* (ТЗ 7.3)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from bf_core.db import dialect

from .project import Project
from .service import projects
from .store import ProjectService, normalize_description, summary

router = APIRouter(prefix="/projects")
Svc = Annotated[ProjectService, Depends(projects)]


class Description(BaseModel):
    deposit: str = ""
    license: str = ""
    stage: str = ""


class CreateBody(BaseModel):
    name: str = Field(max_length=400)
    description: Description | None = None


class UpdateBody(BaseModel):
    name: str | None = Field(default=None, max_length=400)
    description: Description | None = None
    expected_version: int | None = None


def _info(p: Project) -> dict[str, Any]:
    meta = p.meta()
    with dialect.read_tx(p.engine) as conn:
        counts = summary(conn)
    return {
        "uid": p.uid,
        "name": meta["name"],
        "description": normalize_description(meta["description"]),
        "created_at": meta["created_at"].isoformat(),
        "updated_at": meta["updated_at"].isoformat(),
        "version": meta["version"],
        "summary": counts,
        "path": str(p.folder),
        "read_only": p.read_only,
        "lock_owner": p.lock_owner,
    }


@router.get("")
def list_projects(svc: Svc, q: str = "") -> dict[str, Any]:
    return {"root": str(svc.root), "projects": svc.list(q)}


@router.post("")
def create_project(svc: Svc, body: CreateBody) -> dict[str, Any]:
    desc = body.description.model_dump() if body.description else None
    return _info(svc.create(body.name, desc))


@router.get("/current")
def current_project(svc: Svc) -> dict[str, Any]:
    p = svc.current
    return {"project": _info(p) if p else None}


@router.post("/demo")
def demo_project(svc: Svc) -> dict[str, Any]:
    """`project.demo`: creates «Демо: участок Северный» or opens it if it exists (ТЗ 7.5)."""
    from bf_core.demo.project import create_or_open

    project, created = create_or_open(svc)
    return {**_info(project), "created": created}


@router.post("/{uid}/open")
def open_project(svc: Svc, uid: str) -> dict[str, Any]:
    return _info(svc.open(uid))


@router.post("/close")
def close_project(svc: Svc) -> dict[str, Any]:
    svc.close()
    return {"project": None}


@router.patch("/{uid}")
def update_project(svc: Svc, uid: str, body: UpdateBody) -> dict[str, Any]:
    desc = body.description.model_dump() if body.description else None
    svc.update_meta(uid, name=body.name, description=desc, expected_version=body.expected_version)
    return _info(svc.require_current())


@router.delete("/{uid}")
def delete_project(svc: Svc, uid: str) -> dict[str, Any]:
    svc.delete(uid)
    return {"deleted": uid}
