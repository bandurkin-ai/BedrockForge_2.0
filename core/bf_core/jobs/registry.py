"""Job kinds and their handlers (ТЗ 6.7). Handlers are registered by an explicit import of their modules
(`HANDLER_MODULES`), never from `__main__`: a `spawn` worker re-imports them by name.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .context import JobContext

#: Modules that register handlers. Each later stage appends its module here.
HANDLER_MODULES: tuple[str, ...] = (
    "bf_core.jobs.diag",
    "bf_core.archive.jobs",
    "bf_core.importing.jobs",
    "bf_core.importing.trenches",
    "bf_core.importing.objects",
    "bf_core.drillholes.export",
    "bf_core.audit.jobs",
    "bf_core.lines.export",
    "bf_core.lines.ground_job",
    "bf_core.rasters.export",
    "bf_core.wireframes.jobs",
    "bf_core.wireframes.cloud_dz",
    "bf_core.wireframes.check",
    "bf_core.wireframes.export",
    "bf_core.ops.job",
)

Queue = str  # "heavy" | "quick"


@dataclass(frozen=True)
class JobKind:
    kind: str
    title: str  # Russian, shown in «Задания» when the job has no own title
    queue: Queue
    run: Callable[[JobContext], dict[str, Any] | None]
    max_attempts: int = 3
    #: Memory the job needs, MB (ADR 0002 §4): the scheduler waits while there is not enough.
    estimate_memory: Callable[[dict[str, Any]], int] = lambda _params: 256
    #: Instance jobs live in instance.sqlite (no project: import of a project, demo).
    instance: bool = False
    #: May be started by the generic POST /jobs (diagnostics). Module jobs go through their own endpoints.
    public: bool = False


KINDS: dict[str, JobKind] = {}


def handler(
    kind: str,
    title: str,
    *,
    queue: Queue = "heavy",
    max_attempts: int | None = None,
    estimate_memory: Callable[[dict[str, Any]], int] | None = None,
    instance: bool = False,
    public: bool = False,
) -> Callable[[Callable[[JobContext], dict[str, Any] | None]], Callable[[JobContext], dict[str, Any] | None]]:
    def wrap(fn: Callable[[JobContext], dict[str, Any] | None]) -> Callable[[JobContext], dict[str, Any] | None]:
        attempts = max_attempts if max_attempts is not None else (2 if kind.startswith("import.") else 3)
        KINDS[kind] = JobKind(
            kind,
            title,
            queue,
            fn,
            attempts,
            estimate_memory or (lambda _p: 256),
            instance,
            public,
        )
        return fn

    return wrap


def load_handlers() -> dict[str, JobKind]:
    modules = list(HANDLER_MODULES)
    extra = os.environ.get("BF_EXTRA_JOB_MODULES")  # tests only
    if extra:
        modules += [m for m in extra.split(",") if m]
    for name in modules:
        importlib.import_module(name)
    return KINDS
