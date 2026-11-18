"""Project file storage `blobs\\ab\\cd\\<sha256>` (ТЗ 6.5, 7.6; ADR 0002 §5). Ported from BF storage/blobs.py
without the PostgreSQL advisory lock: SQLite has one writer, the owner transaction is the mutual exclusion.

Write: stream into `blobs\\.tmp\\<uuid>` counting SHA-256 → fsync → inside the owner's write transaction:
move into place (or drop the temp file if the blob exists) → `blobs` row → `blob_owners` row → COMMIT.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import IO, Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import BLOB_OWNER_KINDS, blob_owners, blobs, valid_blob_role

CHUNK = 1 << 20
_HEX = set("0123456789abcdef")


def valid_sha(sha: str) -> bool:
    return len(sha) == 64 and set(sha) <= _HEX


class BlobStore:
    def __init__(self, folder: Path) -> None:
        self.root = folder / "blobs"

    def path_of(self, sha: str) -> Path:
        if not valid_sha(sha):
            raise ValueError(f"not a sha256: {sha!r}")
        return self.root / sha[:2] / sha[2:4] / sha

    @property
    def tmp_dir(self) -> Path:
        return self.root / ".tmp"

    def exists(self, sha: str) -> bool:
        return self.path_of(sha).is_file()

    # --- writing ----------------------------------------------------------------------------

    def stage_stream(self, chunks: Iterable[bytes]) -> tuple[Path, str, int]:
        """Writes chunks to a temp file with fsync. Returns (temp path, sha256, size)."""
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.tmp_dir / uuid.uuid4().hex
        h = hashlib.sha256()
        size = 0
        try:
            with open(tmp, "wb") as fh:
                for chunk in chunks:
                    h.update(chunk)
                    size += len(chunk)
                    fh.write(chunk)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return tmp, h.hexdigest(), size

    def stage_file(self, src: Path) -> tuple[Path, str, int]:
        with open(src, "rb") as fh:
            return self.stage_stream(iter(lambda: fh.read(CHUNK), b""))

    def stage_bytes(self, data: bytes) -> tuple[Path, str, int]:
        return self.stage_stream([data])

    def commit_staged(
        self,
        conn: sa.Connection,
        staged: tuple[Path, str, int],
        *,
        media_type: str,
        owner_kind: str,
        owner_id: int,
        role: str = "data",
    ) -> str:
        """Within the owner's write transaction: file into place, `blobs` and `blob_owners` rows."""
        sha = self.place(conn, staged, media_type=media_type)
        add_owner(conn, sha, owner_kind, owner_id, role)
        return sha

    def place(self, conn: sa.Connection, staged: tuple[Path, str, int], *, media_type: str) -> str:
        """Within a write transaction: the file into place and its `blobs` row, without an owner yet — the
        caller registers the owners in the same transaction (`add_owner`, `add_owners`: a blob shared by many)."""
        tmp, sha, size = staged
        dst = self.path_of(sha)
        if dst.exists():
            tmp.unlink(missing_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, dst)
        exists = conn.execute(sa.select(blobs.c.sha256).where(blobs.c.sha256 == sha)).first()
        if exists is None:
            conn.execute(
                sa.insert(blobs).values(sha256=sha, size=size, media_type=media_type, created_at=dialect.utcnow())
            )
        return sha

    def put_bytes(self, conn: sa.Connection, data: bytes, **owner: Any) -> str:
        return self.commit_staged(conn, self.stage_bytes(data), **owner)

    def put_file(self, conn: sa.Connection, src: Path, **owner: Any) -> str:
        return self.commit_staged(conn, self.stage_file(src), **owner)

    # --- reading ----------------------------------------------------------------------------

    def open(self, sha: str) -> IO[bytes]:
        return open(self.path_of(sha), "rb")

    def read_bytes(self, sha: str) -> bytes:
        return self.path_of(sha).read_bytes()

    def iter_range(self, sha: str, start: int, end: int) -> Iterator[bytes]:
        """Bytes [start, end] inclusive, in chunks."""
        with self.open(sha) as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = fh.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk


def add_owner(conn: sa.Connection, sha: str, owner_kind: str, owner_id: int, role: str = "data") -> None:
    """Registers a reference to a blob. Every kind of owner is declared in schema.BLOB_OWNER_KINDS (ТЗ 6.5),
    every role in schema.BLOB_ROLES (ADR 0011 §1)."""
    if owner_kind not in BLOB_OWNER_KINDS:
        raise ValueError(f"blob owner kind {owner_kind!r} is not registered in bf_core.db.schema.BLOB_OWNER_KINDS")
    if not valid_blob_role(role):
        raise ValueError(f"blob role {role!r} is not registered in bf_core.db.schema.BLOB_ROLES")
    key = (
        (blob_owners.c.sha256 == sha)
        & (blob_owners.c.owner_kind == owner_kind)
        & (blob_owners.c.owner_id == owner_id)
        & (blob_owners.c.role == role)
    )
    if conn.execute(sa.select(blob_owners.c.sha256).where(key)).first() is None:
        conn.execute(sa.insert(blob_owners).values(sha256=sha, owner_kind=owner_kind, owner_id=owner_id, role=role))


def add_owners(conn: sa.Connection, rows: Sequence[tuple[str, str, int, str]]) -> None:
    """References (sha256, owner kind, owner id, role) of owners made in this transaction, in one statement:
    such owners have no references yet, so nothing is looked up. Kinds and roles are checked as in `add_owner`."""
    for _sha, owner_kind, _owner_id, role in rows:
        if owner_kind not in BLOB_OWNER_KINDS:
            raise ValueError(f"blob owner kind {owner_kind!r} is not registered in bf_core.db.schema.BLOB_OWNER_KINDS")
        if not valid_blob_role(role):
            raise ValueError(f"blob role {role!r} is not registered in bf_core.db.schema.BLOB_ROLES")
    if rows:
        keys = ("sha256", "owner_kind", "owner_id", "role")
        conn.execute(sa.insert(blob_owners), [dict(zip(keys, row, strict=True)) for row in rows])


def drop_owner(conn: sa.Connection, owner_kind: str, owner_id: int) -> None:
    """Forget every blob of an owner (the files go with the next collection)."""
    conn.execute(
        sa.delete(blob_owners).where((blob_owners.c.owner_kind == owner_kind) & (blob_owners.c.owner_id == owner_id))
    )
