"""Resumable uploads of large files (up to MAX_CHUNKED_UPLOAD_MB, 4 GB by default).

    POST   /api/projects/{id}/uploads   {filename, size}       -> upload (id, chunk_size, received)
    POST   /api/uploads                 {filename, size}       -> an upload of a project archive (no project yet)
    PUT    /api/uploads/{id}?offset=N   raw bytes of one chunk -> upload
    GET    /api/uploads/{id}                                   -> upload (to resume: send from `received`)
    DELETE /api/uploads/{id}

A chunk must start exactly at `received` (else 409 with the server's `received`), so a retried or
duplicated chunk can never corrupt the file. With the `X-Chunk-SHA256` header the server checks the
chunk and drops it on mismatch (422). When `received == size` the upload is complete and can be
passed to an import job (routers/jobs.py).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.models import Upload
from app.routers.projects import get_project
from app.storage import blobs

router = APIRouter(prefix="/api", tags=["uploads"])


class UploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0)


class UploadOut(BaseModel):
    id: str
    project_id: int | None
    filename: str
    size: int
    received: int
    status: str
    chunk_size: int


def part_path(upload_id: str) -> Path:
    d = blobs.root() / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{upload_id}.part"


def _out(u: Upload) -> UploadOut:
    return UploadOut(id=u.id, project_id=u.project_id, filename=u.filename, size=u.size, received=u.received,
                     status=u.status, chunk_size=settings.upload_chunk_bytes)


def get_upload(session: Session, upload_id: str, *, lock: bool = False) -> Upload:
    stmt = select(Upload).where(Upload.id == upload_id)
    if lock:
        stmt = stmt.with_for_update()
    upload = session.scalars(stmt).first()
    if upload is None:
        raise HTTPException(404, "Загрузка не найдена (возможно, устарела)")
    return upload


@router.post("/projects/{project_id}/uploads", response_model=UploadOut, status_code=201)
def create_upload(project_id: int, body: UploadIn, session: Session = Depends(get_session)):
    get_project(session, project_id)
    return _create(session, project_id, body, settings.max_chunked_upload_bytes)


@router.post("/uploads", response_model=UploadOut, status_code=201)
def create_archive_upload(body: UploadIn, session: Session = Depends(get_session)):
    """A project archive (.bfggis): it arrives before the project it makes exists."""
    if not body.filename.lower().endswith(".bfggis"):
        raise HTTPException(422, "Нужен файл проекта BedrockForge (.bfggis)")
    return _create(session, None, body, settings.max_archive_upload_bytes)


def _create(session: Session, project_id: int | None, body: UploadIn, max_bytes: int) -> UploadOut:
    if body.size > max_bytes:
        limit = max_bytes / (1 << 30)
        raise HTTPException(413, f"Файл больше {limit:g} ГБ")
    upload = Upload(id=uuid.uuid4().hex, project_id=project_id, filename=Path(body.filename).name[:255],
                    size=body.size, received=0, status="complete" if body.size == 0 else "receiving")
    part_path(upload.id).touch()
    session.add(upload)
    session.commit()
    return _out(upload)


@router.get("/uploads/{upload_id}", response_model=UploadOut)
def read_upload(upload_id: str, session: Session = Depends(get_session)):
    return _out(get_upload(session, upload_id))


@router.put("/uploads/{upload_id}", response_model=UploadOut)
async def put_chunk(upload_id: str, request: Request, offset: int = Query(ge=0), session: Session = Depends(get_session)):
    upload = get_upload(session, upload_id, lock=True)  # one writer at a time
    if upload.status != "receiving":
        session.rollback()
        raise HTTPException(409, {"message": "Загрузка уже завершена", "received": upload.received})
    if offset != upload.received:
        received = upload.received
        session.rollback()
        raise HTTPException(409, {"message": "Часть не с того места", "received": received})
    path = part_path(upload_id)
    limit = min(upload.size - upload.received, 2 * settings.upload_chunk_bytes)
    expected = request.headers.get("x-chunk-sha256", "").strip().lower() or None
    h = hashlib.sha256()
    written = 0
    with path.open("r+b") as f:
        f.truncate(offset)  # drop the tail of an interrupted earlier attempt
        f.seek(offset)
        try:
            async for piece in request.stream():
                written += len(piece)
                if written > limit:
                    raise HTTPException(413, "Часть больше допустимого размера или выходит за конец файла")
                h.update(piece)
                f.write(piece)
            if expected and h.hexdigest() != expected:
                raise HTTPException(422, "Контрольная сумма части не совпала, отправьте её ещё раз")
        except BaseException:
            f.truncate(offset)
            session.rollback()
            raise
        f.flush()
        os.fsync(f.fileno())
    upload.received = offset + written
    if upload.received == upload.size:
        upload.status = "complete"
    session.commit()
    return _out(upload)


@router.delete("/uploads/{upload_id}", status_code=204)
def delete_upload(upload_id: str, session: Session = Depends(get_session)) -> None:
    upload = get_upload(session, upload_id, lock=True)
    session.delete(upload)
    session.commit()
    part_path(upload_id).unlink(missing_ok=True)
