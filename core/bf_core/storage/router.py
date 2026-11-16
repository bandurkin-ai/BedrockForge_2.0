"""/api/v1/blobs — raw arrays and files of the open project with ETag and Range (ТЗ 7.6 «Передача в интерфейс»)."""

from __future__ import annotations

import re
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header
from fastapi.responses import Response, StreamingResponse

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from .blobs import BlobStore, valid_sha
from .gc import collect

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _not_found() -> ApiError:
    return ApiError(
        404, "blob_not_found", "Файл данных не найден в проекте.", "Обновите объект или откройте проект заново."
    )


@router.get("/blobs/{sha}")
def get_blob(
    svc: Svc,
    sha: str,
    range_: Annotated[str | None, Header(alias="Range")] = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    project = svc.require_current()
    if not valid_sha(sha):
        raise _not_found()
    with dialect.read_tx(project.engine) as conn:
        row = conn.execute(sa.select(blobs.c.size, blobs.c.media_type).where(blobs.c.sha256 == sha)).first()
    store = BlobStore(project.folder)
    if row is None or not store.exists(sha):
        raise _not_found()
    size, media = int(row.size), str(row.media_type)
    etag = f'"{sha}"'
    common = {"ETag": etag, "Accept-Ranges": "bytes", "Cache-Control": "private, max-age=31536000, immutable"}
    if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=304, headers=common)
    if range_:
        m = _RANGE.match(range_.strip())
        if not m or (not m.group(1) and not m.group(2)):
            return Response(status_code=416, headers={**common, "Content-Range": f"bytes */{size}"})
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:  # suffix: the last N bytes
            start, end = max(0, size - int(m.group(2))), size - 1
        if start >= size or start > end:
            return Response(status_code=416, headers={**common, "Content-Range": f"bytes */{size}"})
        headers = {**common, "Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)}
        return StreamingResponse(store.iter_range(sha, start, end), status_code=206, media_type=media, headers=headers)
    headers = {**common, "Content-Length": str(size)}
    return StreamingResponse(store.iter_range(sha, 0, size - 1), media_type=media, headers=headers)


@router.post("/storage/collect")
def collect_now(svc: Svc) -> dict[str, Any]:
    """Collection «вне расписания — по команде» (ТЗ 7.6)."""
    project = svc.require_current(writable=True)
    return collect(project.engine, BlobStore(project.folder)).as_dict()
