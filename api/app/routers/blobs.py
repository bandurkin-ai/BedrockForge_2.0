"""Stored files by SHA-256: immutable, cacheable forever, partial reads with HTTP Range."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from app.storage import blobs

router = APIRouter(prefix="/api/blobs", tags=["blobs"])

_IMMUTABLE = "public, max-age=31536000, immutable"


def blob_url(sha: str) -> str:
    return f"/api/blobs/{sha}"


@router.get("/{sha}")
def get_blob(sha: str, request: Request) -> Response:
    """Raw bytes (application/octet-stream). The content never changes for a given hash, so the
    browser keeps it; `Range: bytes=a-b` returns a part (206), as Starlette's FileResponse does."""
    try:
        path = blobs.path_of(sha)
    except ValueError:
        raise HTTPException(404, "Файл не найден") from None
    if not path.is_file():
        raise HTTPException(404, "Файл не найден")
    etag = f'"{sha}"'
    if etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": _IMMUTABLE})
    return FileResponse(path, media_type="application/octet-stream",
                        headers={"ETag": etag, "Cache-Control": _IMMUTABLE})
