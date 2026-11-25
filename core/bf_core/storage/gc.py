"""Collector of unused blobs (ТЗ 7.6 «Очистка», ADR 0002 §5). Runs in a write transaction, so it never races
a writer registering a blob. Busy files (Windows: open mmap, WinError 32) are left for the next pass.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import asdict, dataclass

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, blobs

from .blobs import BlobStore, valid_sha

STRAY_GRACE_S = 15 * 60  # a file without a row: a writer may be between the move and its COMMIT
TMP_GRACE_S = 24 * 3600


@dataclass
class GcReport:
    blobs: int = 0
    bytes: int = 0
    stray_files: int = 0
    tmp_files: int = 0
    cache_dirs: int = 0
    busy: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def collect(engine: sa.Engine, store: BlobStore, *, now: float | None = None) -> GcReport:
    now = time.time() if now is None else now
    report = GcReport()
    with dialect.write_tx(engine) as conn:
        orphan = conn.execute(
            sa.select(blobs.c.sha256, blobs.c.size).where(~sa.exists().where(blob_owners.c.sha256 == blobs.c.sha256))
        ).all()
        for sha, size in orphan:
            try:
                store.path_of(sha).unlink(missing_ok=True)
            except PermissionError:
                report.busy += 1
                continue
            conn.execute(sa.delete(blobs).where(blobs.c.sha256 == sha))
            report.blobs += 1
            report.bytes += int(size)
        known = {r[0] for r in conn.execute(sa.select(blobs.c.sha256))}
        if store.root.is_dir():
            for path in store.root.glob("??/??/*"):
                name = path.name
                if not valid_sha(name) or name in known:
                    continue
                try:
                    if now - path.stat().st_mtime < STRAY_GRACE_S:
                        continue
                    path.unlink()
                    report.stray_files += 1
                except FileNotFoundError:
                    continue
                except PermissionError:
                    report.busy += 1
        if store.tmp_dir.is_dir():
            for path in store.tmp_dir.iterdir():
                try:
                    if now - path.stat().st_mtime >= TMP_GRACE_S:
                        path.unlink()
                        report.tmp_files += 1
                except FileNotFoundError:
                    continue
                except PermissionError:
                    report.busy += 1
        _drop_block_cache(conn, store, report)
    return report


def _drop_block_cache(conn: sa.Connection, store: BlobStore, report: GcReport) -> None:
    """The disk cache of the columns of block models, `cache/blocks/<SHA-256 of the file>/` (stats/blocks_cache.py),
    is derived data: a folder whose file has no owner any more (the model was deleted or its file was replaced) goes."""
    root = store.root.parent / "cache" / "blocks"
    if not root.is_dir():
        return
    owned = {str(r[0]) for r in conn.execute(sa.select(blob_owners.c.sha256).distinct())}
    for path in root.iterdir():
        if path.is_dir() and valid_sha(path.name) and path.name not in owned:
            shutil.rmtree(path, ignore_errors=True)
            report.cache_dirs += 1
