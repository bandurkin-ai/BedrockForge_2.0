"""Files made by export jobs: DATA_DIR/exports/<job id>.<ext>, kept for EXPORT_TTL.

They are not blobs: nothing in the project refers to them, so the object store's collector would
delete them within its grace hour. The collector removes them by age instead (storage/gc.py).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from app.storage import blobs

EXPORT_TTL = timedelta(hours=48)


def exports_dir() -> Path:
    d = blobs.root() / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def export_path(job_id: int, suffix: str) -> Path:
    return exports_dir() / f"{int(job_id)}{suffix}"
