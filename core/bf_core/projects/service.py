"""The project service of a running core (created on first use: SQLAlchemy and Alembic load after BF-READY)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import sqlalchemy as sa
from fastapi import Request

from bf_core.api.app import CoreState
from bf_core.db import dialect, migrate
from bf_core.paths import default_projects_dir, instance_db

from .store import ProjectService

_create_lock = threading.Lock()


MAINTENANCE_EVERY_S = 30 * 60  # unused files go within the hour (ТЗ 7.6); daily backup (ADR 0002 §5)
log = logging.getLogger("bf_core.projects")


class _Service(ProjectService):
    def __init__(self, root: Path, instance: sa.Engine) -> None:
        super().__init__(root, instance)
        self._stop = threading.Event()
        threading.Thread(target=self._maintenance, name="project-maintenance", daemon=True).start()

    def maintain_once(self) -> None:
        from bf_core.storage.blobs import BlobStore
        from bf_core.storage.gc import collect

        with self._lock:
            p = self.current
            if p is None or p.read_only:
                return
            collect(p.engine, BlobStore(p.folder))
            p.backup_if_due()
        self.reap()

    def _maintenance(self) -> None:
        while not self._stop.wait(MAINTENANCE_EVERY_S):
            try:
                self.maintain_once()
            except Exception:  # noqa: BLE001 - maintenance never stops the core
                log.exception("project maintenance failed")

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            if self.current:
                self.current.close()
                self.current = None
            for p in self.detached.values():
                p.close()
            self.detached.clear()
        self.instance.dispose()


def projects_of(state: CoreState) -> ProjectService:
    service = state.services.get("projects")
    if service is not None:
        return service  # type: ignore[no-any-return]
    with _create_lock:
        service = state.services.get("projects")
        if service is not None:
            return service  # type: ignore[no-any-return]
        path = instance_db()
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = dialect.create_engine(dialect.sqlite_url(str(path)))
        if migrate.state(engine, "instance") == "newer":
            # A newer program used this profile: the cache is disposable, start it again.
            engine.dispose()
            path.unlink(missing_ok=True)
            engine = dialect.create_engine(dialect.sqlite_url(str(path)))
        migrate.upgrade(engine, "instance")
        root = Path(state.projects_dir) if state.projects_dir else default_projects_dir()
        service = _Service(root, engine)
        state.services["projects"] = service
    from bf_core.jobs.service import attach

    attach(state, service)  # the scheduler is created right after: shutdown closes it first
    return service  # type: ignore[no-any-return]


def projects(request: Request) -> ProjectService:
    """FastAPI dependency."""
    return projects_of(request.app.state.core)
