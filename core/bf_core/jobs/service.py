"""The job scheduler of a running core, tied to the project service (created together with it)."""

from __future__ import annotations

import threading

from fastapi import Request

from bf_core.api.app import CoreState
from bf_core.projects.project import Project
from bf_core.projects.store import ProjectService

from .scheduler import Scheduler, Source

_lock = threading.Lock()


def attach(state: CoreState, svc: ProjectService) -> Scheduler:
    with _lock:
        sched = state.services.get("jobs")
        if sched is not None:
            return sched  # type: ignore[no-any-return]
        sched = Scheduler(state.events, Source("instance", svc.instance, True), heavy_workers=1)

        def opened(p: Project) -> None:
            if not p.read_only:
                sched.attach_project(p.uid, p.engine, str(p.folder))

        svc.on_open.append(opened)
        svc.on_close.append(lambda p: sched.detach_project(p.uid))
        svc.is_released = sched.released
        if svc.current is not None:
            opened(svc.current)
        state.services["jobs"] = sched
        return sched


def jobs_of(state: CoreState) -> Scheduler:
    from bf_core.projects.service import projects_of

    projects_of(state)  # creates the scheduler too
    return state.services["jobs"]  # type: ignore[no-any-return]


def jobs(request: Request) -> Scheduler:
    """FastAPI dependency."""
    return jobs_of(request.app.state.core)
