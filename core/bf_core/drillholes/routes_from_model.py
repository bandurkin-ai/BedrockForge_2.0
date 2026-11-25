"""`POST /api/v1/drillholes/from_model` — «Модель на скважины» (ТЗ 7.35 Д-34 ч. 1; e19-api.md §8.4). Included into
`router.py` (one module per router in api/modules.py).

`{set_id, name?, model_id | mesh_ids: [id…], codes?: {group id | wireframe id: "код"}}` — the bodies of an implicit
model (by the priority of its groups) or closed wireframes (by the order of the list) cut the holes of the set into a
new interval table `FROM, TO, CODE` (a gap between the bodies — an empty CODE); or `{set_id, name?, block_model_id,
field, table}` — a new table `FROM, TO, <field>`: a number field of a block model at the middle of the
intervals of `table`.
The request is checked here (`from_model_spec.parse`); the work is the job `drillholes.from_model` (heavy) →
`{source, id}`; its result `{set_id, table_id, name, rows, holes, no_trajectory, report, events}`.
Refusals: 423 `project_read_only`; 404 `set_not_found`, `model_not_found`, `block_model_not_found`, `table_not_found`,
`wireframe_not_found`; 409 `model_building`, `table_exists` {name, suggested}; 422 `bad_param` {param}, `no_bodies`,
`wireframe_open`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends

from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs

from .router import Svc

router = APIRouter()
Sched = Annotated[Scheduler, Depends(jobs)]
Payload = Annotated[dict[str, Any], Body()]


@router.post("/from_model")
def from_model(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from . import from_model as fm
    from . import from_model_spec as fs

    project = svc.require_current(writable=True)
    spec = fs.lookup(project.engine, BlobStore(project.folder), body)
    title = f"Модель на скважины: {spec.name}"
    key, job_id = s.submit(fm.KIND, {"body": body, "triangles": spec.triangles}, title=title)
    return {"source": key, "id": job_id}
