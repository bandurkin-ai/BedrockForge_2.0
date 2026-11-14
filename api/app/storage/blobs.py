"""Content-addressed file storage for geometry, images and source files.

Large binary data (vertices, triangles, images, uploaded source files) lives in files named
by their SHA-256, not in PostgreSQL: a `bytea` field is limited to 1 GB, is always read whole,
and bloats backups of the database. The database keeps only the `blob` row (hash, size) and
the references from the objects that use it (`blobs` JSONB columns, see `OWNERS`).

Layout under `root()`:

    blobs/ab/cd/abcd…    immutable content, one file per SHA-256
    tmp/                 files being written; moved into blobs/ by an atomic rename
    uploads/             resumable uploads in progress (routers/uploads.py)

Consistency with the garbage collector: a writer registers a blob under a shared advisory lock
held until its transaction ends, the collector deletes under the exclusive one. So a blob the
collector sees as unreferenced cannot be picked up by a writer that has not committed yet.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings

# pg_advisory_xact_lock key for "blob registration vs garbage collection".
_LOCK_KEY = 0x0B10B
_READ_CHUNK = 8 << 20
_root_override: Path | None = None


def root() -> Path:
    return _root_override or settings.data_dir


def set_root(path: Path | None) -> None:
    """Tests and tools point the store at another folder."""
    global _root_override
    _root_override = path


def path_of(sha: str) -> Path:
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError(f"not a SHA-256: {sha!r}")
    return root() / "blobs" / sha[:2] / sha[2:4] / sha


def tmp_dir() -> Path:
    d = root() / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def tmp_path(suffix: str = "") -> Path:
    return tmp_dir() / f"{uuid.uuid4().hex}{suffix}"


def exists(sha: str) -> bool:
    return path_of(sha).is_file()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_READ_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _lock_shared(session: Session) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock_shared(:k)"), {"k": _LOCK_KEY})


def lock_exclusive(session: Session) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})


def put_file(session: Session, src: Path, *, sha: str | None = None, media_type: str | None = None) -> str:
    """Moves `src` into the store (the file is consumed) and registers it; returns its SHA-256.

    `src` should be on the same file system as the store (use `tmp_path()`), so the move is a rename.
    The registration belongs to the caller's transaction: commit it together with the reference.
    """
    sha = sha or file_sha256(src)
    size = src.stat().st_size
    _lock_shared(session)
    dest = path_of(sha)
    if dest.is_file():
        src.unlink()
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _fsync(src)
        os.replace(src, dest)
    session.execute(
        text(
            "INSERT INTO blob (sha256, size, media_type) VALUES (:sha, :size, :mt) "
            "ON CONFLICT (sha256) DO UPDATE SET created_at = now()"
        ),
        {"sha": sha, "size": size, "mt": media_type},
    )
    return sha


def put_bytes(session: Session, data: bytes | memoryview, *, media_type: str | None = None) -> str:
    tmp = tmp_path()
    with tmp.open("wb") as f:
        f.write(data)
    return put_file(session, tmp, media_type=media_type)


def put_array(session: Session, array: np.ndarray, dtype: str) -> str:
    """Stores an array as raw little-endian values of `dtype` (e.g. "<f8"), C order."""
    a = np.ascontiguousarray(array, dtype=dtype)
    tmp = tmp_path()
    a.tofile(tmp)
    return put_file(session, tmp, media_type="application/octet-stream")


def read_bytes(sha: str) -> bytes:
    return path_of(sha).read_bytes()


def read_array(sha: str, dtype: str, columns: int | None = None) -> np.ndarray:
    """A read-only memory map of a stored array: nothing is read until it is used."""
    p = path_of(sha)
    item = np.dtype(dtype).itemsize
    size = p.stat().st_size
    if size % (item * (columns or 1)):
        raise ValueError(f"blob {sha[:12]}… is not a whole number of {dtype} rows")
    if size == 0:
        a = np.empty(0, dtype=dtype)
    else:
        a = np.memmap(p, dtype=dtype, mode="r")
    return a.reshape(-1, columns) if columns else a


@contextmanager
def open_blob(sha: str) -> Iterator:
    with path_of(sha).open("rb") as f:
        yield f
