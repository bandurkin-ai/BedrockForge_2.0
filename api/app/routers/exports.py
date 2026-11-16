"""Export of project objects to files, as background jobs.

    POST /api/projects/{id}/exports      start an export (a job); body: format, dataset_ids, filename
    GET  /api/jobs/{id}/download         the file of a finished export job
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.exporting.files import export_path
from app.exporting.omf import EXPORT_KINDS
from app.jobs import queue
from app.models import Dataset, Job
from app.routers.projects import get_project

router = APIRouter(prefix="/api", tags=["exports"])

FORMATS = {"omf": (".omf", "export.omf"), "bfggis": (".bfggis", "export.project")}


class ExportIn(BaseModel):
    format: Literal["omf"] = "omf"
    dataset_ids: list[int] = Field(min_length=1, max_length=500)
    # File name for the download; the project name when empty.
    filename: str = Field("", max_length=200)


def clean_filename(name: str, fallback: str, suffix: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name.strip()).strip(" .") or fallback
    if not name.lower().endswith(suffix):
        name += suffix
    return name[:200]


@router.post("/projects/{project_id}/exports", status_code=202)
def start_export(project_id: int, body: ExportIn, session: Session = Depends(get_session)) -> dict:
    project = get_project(session, project_id)
    suffix, kind = FORMATS[body.format]
    ids = list(dict.fromkeys(body.dataset_ids))
    found = {d.id: d for d in session.scalars(select(Dataset).where(Dataset.id.in_(ids), Dataset.project_id == project_id))}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(404, f"Наборов нет в проекте: {', '.join(map(str, missing))}")
    wrong = [found[i].name for i in ids if found[i].kind not in EXPORT_KINDS]
    if wrong:
        raise HTTPException(422, f"В OMF записываются каркасы, линии и блочные модели; не подходят: {', '.join(wrong)}")
    filename = clean_filename(body.filename, project.name, suffix)
    job = queue.enqueue(session, kind, {"project_id": project_id, "dataset_ids": ids, "filename": filename},
                        project_id=project_id, title=f"Экспорт OMF: {filename}"[:255], max_attempts=1)
    session.commit()
    return queue.job_out(job)


@router.get("/jobs/{job_id}/download")
def download(job_id: int, session: Session = Depends(get_session)) -> FileResponse:
    job = session.get(Job, job_id)
    if job is None or not job.kind.startswith("export."):
        raise HTTPException(404, "Задание экспорта не найдено")
    if job.status != "done" or not job.result:
        raise HTTPException(409, "Экспорт ещё не готов")
    suffix = next((s for s, k in FORMATS.values() if k == job.kind), "")
    path = export_path(job.id, suffix)
    if not path.is_file():
        raise HTTPException(410, "Файл экспорта удалён по сроку хранения: запустите экспорт снова")
    name = job.result.get("filename") or path.name
    # An ASCII name for clients that ignore filename* (RFC 6266), the real one in UTF-8.
    stem = name[: -len(suffix)] if suffix and name.lower().endswith(suffix) else name
    fallback = (re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_.") or "export") + suffix
    disposition = f"attachment; filename=\"{fallback}\"; filename*=utf-8''{quote(name)}"
    return FileResponse(path, media_type="application/octet-stream", headers={"Content-Disposition": disposition})
