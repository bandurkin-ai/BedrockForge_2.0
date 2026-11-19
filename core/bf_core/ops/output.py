"""What a run of an operation gives back to the job (`ops/job.py` writes it): meshes, layers of lines, a report,
warnings and tolerances; and the errors of the kernels as the errors of the job."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.jobs.context import JobError

#: `tick(fraction 0…1 of the run, message)`; raises JobCancelled after a cancel.
Tick = Callable[[float, str], None]


@dataclass
class MeshOut:
    name: str
    vertices: np.ndarray
    triangles: np.ndarray
    color: str | None = None
    attrs: Any = None


@dataclass
class Output:
    name: str  # of the result (one mesh), of the line file, or the title of a report
    meshes: list[MeshOut] = field(default_factory=list)
    layers: list[Any] = field(default_factory=list)  # formats.lineset.LineSet
    report: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    tolerances: dict[str, Any] = field(default_factory=dict)


def failed(code: str, message: str, hint: str) -> JobError:
    return JobError(code, message, hint, retry=False)


def kernel_error(exc: Exception) -> JobError:
    """MeshError, StitchError, SolidError, SurfaceError, DtmError — all carry {code, message, hint}."""
    code = str(getattr(exc, "code", "operation_failed"))
    hint = str(getattr(exc, "hint", "") or "Проверьте исходные объекты и параметры операции.")
    return failed(code, str(getattr(exc, "message", exc)), hint)


def kernel_errors() -> tuple[type[Exception], ...]:
    from bf_core.geo.meshes import MeshError
    from bf_core.geo.solids import SolidError

    return (MeshError, SolidError)  # StitchError ⊂ MeshError, SurfaceError ⊂ SolidError


def within(tick: Tick, start: float, stop: float) -> Callable[[float], None]:
    """A `progress(fraction)` of a kernel inside [start, stop] of the run."""
    return lambda f, *_rest: tick(start + (stop - start) * max(0.0, min(1.0, float(f or 0.0))), "")


def staged(tick: Tick, start: float, stop: float, message: str) -> Callable[[float, str], None]:
    """A `tick(fraction, message)` of a kernel inside [start, stop]; an empty message — `message`."""

    def inner(f: float, m: str = "") -> None:
        tick(start + (stop - start) * max(0.0, min(1.0, float(f or 0.0))), m or message)

    return inner
