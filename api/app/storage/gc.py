"""Garbage collection of the object store: unreferenced blobs, stray files, stale uploads."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.storage import blobs

# Tables whose `blobs` JSONB column ({role: sha256}) references stored files.
# A new table that stores files must be added here, or its files will be collected.
OWNERS = ("dataset", "wireframe", "line_layer", "line_layer_version", "raster")

# A blob is kept this long after registration even when nothing references it yet.
GRACE = timedelta(hours=1)
# Uploads that did not receive a chunk for this long are dropped.
UPLOAD_TTL = timedelta(hours=48)


@dataclass
class GcReport:
    blobs: int = 0
    bytes: int = 0
    stray_files: int = 0
    tmp_files: int = 0
    uploads: int = 0


def referenced_sql() -> str:
    return " UNION ".join(f"SELECT value FROM {t}, jsonb_each_text({t}.blobs) WHERE value IS NOT NULL" for t in OWNERS)


def collect(session: Session, *, grace: timedelta = GRACE, upload_ttl: timedelta = UPLOAD_TTL) -> GcReport:
    """Deletes what nothing references. Runs in one transaction under the exclusive store lock."""
    report = GcReport()
    blobs.lock_exclusive(session)
    rows = session.execute(
        text(
            f"DELETE FROM blob WHERE created_at < now() - :grace "
            f"AND sha256 NOT IN ({referenced_sql()}) RETURNING sha256, size"
        ),
        {"grace": grace},
    ).all()
    for sha, size in rows:
        blobs.path_of(sha).unlink(missing_ok=True)
        report.blobs += 1
        report.bytes += size

    # Files without a row (a writer crashed between the rename and its commit).
    known = {r[0] for r in session.execute(text("SELECT sha256 FROM blob"))}
    cutoff = time.time() - grace.total_seconds()
    base = blobs.root() / "blobs"
    if base.is_dir():
        for p in base.glob("*/*/*"):
            if p.name not in known and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                report.stray_files += 1
    report.tmp_files = _sweep(blobs.root() / "tmp", cutoff)

    stale = session.execute(
        text("DELETE FROM upload WHERE updated_at < now() - :ttl RETURNING id"), {"ttl": upload_ttl}
    ).all()
    for (upload_id,) in stale:
        (blobs.root() / "uploads" / f"{upload_id}.part").unlink(missing_ok=True)
        report.uploads += 1
    live = {r[0] for r in session.execute(text("SELECT id FROM upload"))}
    up_dir = blobs.root() / "uploads"
    if up_dir.is_dir():
        for p in up_dir.glob("*.part"):
            if p.stem not in live and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
    session.commit()
    return report


def _sweep(folder: Path, cutoff: float) -> int:
    n = 0
    if folder.is_dir():
        for p in folder.iterdir():
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                n += 1
    return n
