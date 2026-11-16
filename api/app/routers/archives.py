"""Moving a whole project to another computer: a `.bfggis` file (app/archive/).

    POST /api/projects/{id}/archive   {filename, password?, browser?}  -> export job; file at /api/jobs/{id}/download
    POST /api/uploads                                                   the file goes up first (routers/uploads.py)
    POST /api/archives/inspect        {upload_id, password?}           -> what the file holds, name clash
    POST /api/archives/import         {upload_id, password?, name, mode} -> import job; result: project_id, id maps

A password travels in the job's parameters only until the worker takes the job: the handler removes
it at once, and job responses never show it.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.archive import importer
from app.archive.container import ArchiveError, BadPassword
from app.archive.export import summary
from app.db import get_session
from app.jobs import queue
from app.models import Project, Upload
from app.routers.exports import clean_filename
from app.routers.projects import get_project
from app.routers.uploads import get_upload, part_path

router = APIRouter(prefix="/api", tags=["archives"])


class ArchiveExportIn(BaseModel):
    filename: str = Field("", max_length=200)
    password: str | None = Field(None, max_length=256)
    # Settings of the browser (layer visibility, styles, tree order), already limited to this project.
    browser: dict | None = None


class InspectIn(BaseModel):
    upload_id: str
    password: str | None = Field(None, max_length=256)


class ArchiveImportIn(InspectIn):
    name: str = Field("", max_length=200)
    mode: Literal["copy", "rename", "replace"] = "copy"


@router.get("/projects/{project_id}/archive/summary")
def archive_summary(project_id: int, session: Session = Depends(get_session)) -> dict:
    """What an export of the project will hold (rows per table, stored files and their size)."""
    get_project(session, project_id)
    return summary(session, project_id)


@router.post("/projects/{project_id}/archive", status_code=202)
def start_archive_export(project_id: int, body: ArchiveExportIn, session: Session = Depends(get_session)) -> dict:
    project = get_project(session, project_id)
    filename = clean_filename(body.filename, project.name, ".bfggis")
    params = {"project_id": project_id, "filename": filename, "browser": body.browser}
    if body.password:
        params["password"] = body.password
    job = queue.enqueue(session, "export.project", params, project_id=project_id,
                        title=f"Экспорт проекта: {filename}"[:255], max_attempts=1)
    session.commit()
    return queue.job_out(job)


def _archive_upload(session: Session, upload_id: str) -> Upload:
    upload = get_upload(session, upload_id)
    if upload.status != "complete":
        raise HTTPException(409, "Файл загружен не полностью")
    if not part_path(upload.id).is_file():
        raise HTTPException(410, "Загруженный файл пропал: загрузите его ещё раз")
    return upload


@router.post("/archives/inspect")
def inspect_archive(body: InspectIn, session: Session = Depends(get_session)) -> dict:
    upload = _archive_upload(session, body.upload_id)
    path = part_path(upload.id)
    try:
        info = importer.inspect(path, body.password)
    except BadPassword as exc:
        # Encrypted: say so (and whether a password was given), the rest stays unreadable.
        return {"encrypted": True, "password_ok": False, "error": str(exc) if body.password else None,
                "size": path.stat().st_size, "filename": upload.filename}
    except ArchiveError as exc:
        raise HTTPException(422, str(exc)) from exc
    manifest = info["manifest"]
    name = manifest["project"]["name"]
    taken = session.scalars(select(Project).where(Project.name == name)).first()
    return {
        "encrypted": bool(info["header"].get("encrypted")), "password_ok": True,
        "size": path.stat().st_size, "filename": upload.filename,
        "app_version": manifest.get("app_version"), "schema_revision": manifest.get("schema_revision"),
        "exported_at": manifest.get("exported_at"), "project": manifest["project"], "tables": manifest.get("tables", {}),
        "files": manifest.get("files", 0), "file_bytes": manifest.get("file_bytes", 0),
        "browser": bool(manifest.get("browser")),
        "name_taken": taken is not None, "existing_project_id": taken.id if taken else None,
        "free_name": importer.free_name(session, name),
    }


@router.post("/archives/import", status_code=202)
def start_archive_import(body: ArchiveImportIn, session: Session = Depends(get_session)) -> dict:
    upload = _archive_upload(session, body.upload_id)
    name = body.name.strip()
    if body.mode in ("rename", "replace") and not name:
        raise HTTPException(422, "Укажите название проекта")
    if body.mode == "rename" and session.scalars(select(Project).where(Project.name == name)).first():
        raise HTTPException(409, f"Проект «{name}» уже есть")
    params = {"upload_id": upload.id, "name": name, "mode": body.mode, "filename": upload.filename}
    if body.password:
        params["password"] = body.password
    job = queue.enqueue(session, "import.project", params, project_id=None,
                        title=f"Импорт проекта: {upload.filename}"[:255], max_attempts=1)
    session.commit()
    return queue.job_out(job)
