"""Background jobs over HTTP: start an import, follow progress (SSE), cancel.

    POST /api/projects/{id}/imports          start importing completed uploads (a job)
    POST /api/projects/{id}/detect           which object kind the files hold
    GET  /api/projects/{id}/jobs             newest first
    GET  /api/projects/{id}/jobs/events      SSE: every change of the project's jobs
    GET  /api/jobs/{id}                      one job with its journal
    GET  /api/jobs/{id}/events               SSE: changes of one job until it ends
    POST /api/jobs/{id}/cancel
    POST /api/storage/gc                     collect unreferenced files (a job)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db import SessionLocal, get_session
from app.formats.inspect import inspect
from app.formats.rasters import pair_files
from app.formats.registry import OBJECT_KINDS, detect
from app.jobs import queue
from app.models import Job
from app.routers.datasets import VerticalPlacement, clean_folder
from app.routers.projects import get_project
from app.routers.uploads import get_upload, part_path

router = APIRouter(prefix="/api", tags=["jobs"])

# Tests replace this with their own session factory.
session_factory = SessionLocal
POLL_SECONDS = 0.5


class ImportIn(BaseModel):
    uploads: list[str] = Field(min_length=1, max_length=100)
    # None: detected from the first file.
    kind: Literal["wireframes", "lines", "rasters", "blocks"] | None = None
    folder: str = Field("", max_length=255)
    # Images without a georeference stand in this vertical plane.
    section: VerticalPlacement | None = None
    # Rasters: the height of plan images (None: from the georeference file) and a shift of every image, m.
    elevation: float | None = Field(None, ge=-20000, le=20000)
    z_shift: float = Field(0.0, ge=-20000, le=20000)


class DetectIn(BaseModel):
    uploads: list[str] = Field(min_length=1, max_length=100)


def _get_job(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Задание не найдено")
    return job


def _detect_uploads(session: Session, project_id: int, ids: list[str], *, preview: bool = False) -> list[dict]:
    out = []
    for upload_id in ids:
        u = get_upload(session, upload_id)
        if u.project_id != project_id:
            raise HTTPException(404, "Загрузка не найдена")
        complete = u.status == "complete"
        d = detect(u.filename, part_path(u.id) if complete else None)
        row = {"upload": u.id, "filename": u.filename, "size": u.size, "status": u.status,
               "kind": d.kind, "format": d.format, "note": d.note}
        if preview and complete:
            row["preview"] = inspect(d.kind, u.filename, part_path(u.id))
        out.append(row)
    # Images and their georeference files pair by name, as the import will pair them.
    names = [r["filename"] for r in out if r["kind"] in ("rasters", "sidecar")]
    if names:
        groups, unmatched = pair_files(names)
        for r in out:
            if r["kind"] == "rasters":
                r["sidecars"] = groups.get(r["filename"], [])
                if not r["sidecars"]:
                    r["note"] = "Файла привязки нет: изображение встанет на линию разреза или задаётся вручную"
            elif r["kind"] == "sidecar" and r["filename"] in unmatched:
                r["note"] = "Нет изображения с тем же именем"
    return out


@router.post("/projects/{project_id}/detect")
def detect_uploads(project_id: int, body: DetectIn, session: Session = Depends(get_session)) -> list[dict]:
    """Kind, format and a quick preview of every uploaded file (the import wizard's second step)."""
    get_project(session, project_id)
    return _detect_uploads(session, project_id, body.uploads, preview=True)


@router.post("/projects/{project_id}/imports", status_code=202)
def start_import(project_id: int, body: ImportIn, session: Session = Depends(get_session)) -> dict:
    """Queues the import of completed uploads; follow it with /api/jobs/{id}/events."""
    get_project(session, project_id)
    files = _detect_uploads(session, project_id, body.uploads)
    if any(f["status"] != "complete" for f in files):
        raise HTTPException(409, "Не все файлы загружены полностью")
    kind = body.kind
    if kind is None:
        kinds = {f["kind"] for f in files} - {"sidecar"}
        if len(kinds) != 1 or not kinds <= set(OBJECT_KINDS):
            raise HTTPException(422, "Не удалось определить тип объекта по файлам; укажите его явно")
        kind = kinds.pop()
    if kind != "rasters" and any(f["kind"] == "sidecar" for f in files):
        raise HTTPException(422, "Файлы привязки загружаются только вместе с изображениями")
    names = ", ".join(f["filename"] for f in files)
    job = queue.enqueue(
        session, "import.objects",
        {"project_id": project_id, "kind": kind, "uploads": body.uploads, "folder": clean_folder(body.folder),
         "section": body.section.model_dump() if body.section else None,
         "elevation": body.elevation, "z_shift": body.z_shift},
        project_id=project_id, title=f"Импорт: {names}"[:255],
    )
    session.commit()
    return queue.job_out(job)


@router.get("/projects/{project_id}/jobs")
def list_jobs(project_id: int, limit: int = Query(50, ge=1, le=500), session: Session = Depends(get_session)) -> list[dict]:
    get_project(session, project_id)
    jobs = session.scalars(select(Job).where(Job.project_id == project_id).order_by(Job.id.desc()).limit(limit))
    return [queue.job_out(j) for j in jobs]


@router.get("/jobs/{job_id}")
def read_job(job_id: int, session: Session = Depends(get_session)) -> dict:
    job = _get_job(session, job_id)
    return {**queue.job_out(job), "params": job.params, "log": job.log}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, session: Session = Depends(get_session)) -> dict:
    job = _get_job(session, job_id)
    if job.status in queue.TERMINAL:
        return queue.job_out(job)
    return queue.job_out(queue.request_cancel(session, job))


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, session: Session = Depends(get_session)) -> dict:
    """A failed or cancelled job again, with the same files (an import needs its uploads still there)."""
    from app.models import Upload

    job = _get_job(session, job_id)
    if job.status not in ("failed", "cancelled"):
        raise HTTPException(409, "Повторить можно только задание с ошибкой или отменённое")
    ids = list((job.params or {}).get("uploads") or [])
    if ids:
        found = {u.id: u for u in session.scalars(select(Upload).where(Upload.id.in_(ids)))}
        missing = [i for i in ids if i not in found or found[i].status != "complete" or not part_path(i).is_file()]
        if missing:
            raise HTTPException(409, "Загруженных файлов этого задания уже нет: загрузите их заново")
    queue.retry(session, job)
    session.commit()
    return queue.job_out(job)


@router.post("/storage/gc", status_code=202)
def start_gc(session: Session = Depends(get_session)) -> dict:
    job = queue.enqueue(session, "storage.gc", {}, title="Очистка хранилища", max_attempts=1)
    session.commit()
    return queue.job_out(job)


# ---- server-sent events


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _changed(project_id: int | None, job_id: int | None, since: datetime | None) -> list[dict]:
    with session_factory() as s:
        stmt = select(Job)
        if job_id is not None:
            stmt = stmt.where(Job.id == job_id)
        else:
            stmt = stmt.where(Job.project_id == project_id)
        if since is not None:
            stmt = stmt.where(Job.updated_at > since)
        return [queue.job_out(j) for j in s.scalars(stmt.order_by(Job.updated_at))]


async def _stream(request: Request, project_id: int | None, job_id: int | None, seconds: float):
    """Polls the job table (indexed by project and update time) and sends what changed."""
    since: datetime | None = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    yield "retry: 2000\n\n"
    while loop.time() < deadline:
        if await request.is_disconnected():
            return
        rows = await run_in_threadpool(_changed, project_id, job_id, since)
        for row in rows:
            since = max(since, datetime.fromisoformat(row["updated_at"])) if since else datetime.fromisoformat(row["updated_at"])
            yield _sse("job", row)
            if job_id is not None and row["status"] in queue.TERMINAL:
                yield _sse("end", {"id": job_id})
                return
        if not rows:
            yield ": ping\n\n"
        await asyncio.sleep(POLL_SECONDS)


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get("/projects/{project_id}/jobs/events")
async def project_job_events(project_id: int, request: Request, seconds: float = Query(600, gt=0, le=3600)):
    """Every change of the project's jobs. The stream ends after `seconds`; EventSource reconnects."""
    return StreamingResponse(_stream(request, project_id, None, seconds), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: int, request: Request, seconds: float = Query(3600, gt=0, le=3600)):
    with session_factory() as s:
        _get_job(s, job_id)
    return StreamingResponse(_stream(request, None, job_id, seconds), media_type="text/event-stream",
                             headers=_SSE_HEADERS)
