"""The project service of a running core (created on first use: SQLAlchemy and Alembic load after BF-READY)."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import Request

from bf_core.api.app import CoreState
from bf_core.db import dialect, migrate
from bf_core.paths import default_projects_dir, instance_db

from .store import ProjectService

_create_lock = threading.Lock()


class _Service(ProjectService):
    def shutdown(self) -> None:
        self.close()
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
    return service  # type: ignore[no-any-return]


def projects(request: Request) -> ProjectService:
    """FastAPI dependency."""
    return projects_of(request.app.state.core)
