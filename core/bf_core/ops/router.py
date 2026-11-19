"""API of the operations with lines and wireframes (ТЗ 7.23; ADR 0015 §2, §6). Errors {code, message, hint} in
Russian; a project open read-only — 423.

`GET /operations` — the operations and the fields of their windows (`ops/schema.py`).
`POST /operations/check {op, params}` — the preconditions only (`registry.precheck`): {op, title, job, queue,
size, sources, warning}; the window of confirmation shows `warning` (a large input goes to the shared queue,
decision 1) and a refusal with its reason (`wireframe_open` carries `fix` — the button «Исправить каркас»).
`POST /operations {op, params, name?, folder?}` — `model.operation`: the same check (422 before anything is
queued), then the job `geometry.operation` (`ops/job.py`) or, for `dtm`, `model.dtm` → {source, id, job, queue}.
`POST /wireframes/{id}/rebuild`, `POST /lines/layers/{id}/rebuild` — `model.rebuild` (`ops/rebuild.py`).
`POST /geometry/stitch/preview` — the preview of «Собрать из контуров» (`ops/preview.py`), BFA1 `mesh`.
The window follows the job by the events `job` and, when it is done, `objects.changed`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field

from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

# numpy, the kernels and the registry load on first use, not at the start of the core (tests/test_process.py).
router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
HEAVY_WARNING = (
    "Исходные объекты больше 2 млн треугольников: операция пойдёт в общую очередь и может ждать другие задания."
)


class CheckBody(BaseModel):
    op: str = Field(max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


class OperationBody(CheckBody):
    name: str | None = Field(default=None, max_length=400)
    folder: str | None = Field(default=None, max_length=200)


def _summary(checked: Any) -> dict[str, Any]:
    from .registry import JOB

    job = checked.queue_job
    return {
        "op": checked.op,
        "title": checked.title,
        "job": job,
        "queue": "quick" if job == JOB else "heavy",
        "size": checked.size,
        "sources": checked.sources,
        "warning": HEAVY_WARNING if job != JOB and checked.job != "model.dtm" else None,
    }


@router.get("/operations")
def list_operations() -> dict[str, Any]:
    from .schema import listing

    return {"operations": listing()}


@router.post("/operations/check")
def check_operation(svc: Svc, body: CheckBody) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from .registry import precheck

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return _summary(precheck(conn, BlobStore(project.folder), body.op, body.params))


@router.post("/operations")
def start_operation(svc: Svc, s: Sched, body: OperationBody) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from .job import submit
    from .registry import precheck

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        checked = precheck(conn, BlobStore(project.folder), body.op, body.params, name=body.name)
    key, job_id = submit(s, checked, name=body.name, folder=body.folder)
    return {"source": key, "id": job_id, **_summary(checked)}


def _rebuild(svc: ProjectService, s: Scheduler, object_id: int, kind: str) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from .job import submit
    from .rebuild import plan

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        checked, kw = plan(conn, BlobStore(project.folder), object_id, kind)
    key, job_id = submit(s, checked, **kw)
    return {"source": key, "id": job_id, "rebuild_of": object_id, **_summary(checked)}


@router.post("/wireframes/{wireframe_id}/rebuild")
def rebuild_wireframe(svc: Svc, s: Sched, wireframe_id: int) -> dict[str, Any]:
    """`model.rebuild {wireframe_id}`."""
    return _rebuild(svc, s, wireframe_id, "wireframe")


@router.post("/lines/layers/{layer_id}/rebuild")
def rebuild_layer(svc: Svc, s: Sched, layer_id: int) -> dict[str, Any]:
    """`model.rebuild {layer_id}`: a layer made by an operation (isolines, lines of intersection)."""
    return _rebuild(svc, s, layer_id, "line_layer")


Point = list[float | None]


class PreviewBody(BaseModel):
    contours: list[list[Point]]
    ties: list[list[Point]] = Field(default_factory=list)
    start_cap: str = Field(default="flat", max_length=16)
    end_cap: str = Field(default="flat", max_length=16)
    cap_distance: float = 0.5


@router.post("/geometry/stitch/preview")
def stitch_preview(svc: Svc, body: PreviewBody) -> Response:
    from .preview import stitch_preview as preview

    svc.require_current()
    data = preview(
        body.contours, body.ties, start_cap=body.start_cap, end_cap=body.end_cap, cap_distance=body.cap_distance
    )
    return Response(data, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})
