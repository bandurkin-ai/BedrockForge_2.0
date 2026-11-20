"""/api/v1/omf — export to OMF v1 (ТЗ 7.24; ADR 0017 §4; `exporting/omf_job.py`).

  * `GET /omf/exportable` → {wireframes: [{id, name, folder, dtm, triangles, available, reason}], line_files:
    [{id, name, folder, layers, points, available, reason}], block_models: [{id, name, folder, cells, available,
    reason}], read_only, reason} — the three lists of the window «Экспорт OMF»; `reason` {message, hint} of an
    object — why it is not available (a point cloud; a model over 100 million cells); `read_only` — the project is
    open only for reading (ТЗ 5.7): nothing can be exported, the `reason` of the answer says why (a text, as
    `GET /export/formats`);
  * `POST /omf/export` {wireframe_ids, line_file_ids, block_model_ids, path, overwrite?} → {source, id} of the job
    `export.omf` (heavy). 423 `project_read_only`; 422 `nothing_to_export`, `omf_not_exportable`; 404
    `object_not_found`; 409 `files_exist` {files} — the file is there and the save window did not ask: the
    window asks «Заменить?» and repeats with `overwrite: true`. The file is written as «‹имя›.omf.part» and
    renamed when complete.
The import of OMF is the wizard «Импорт файлов» (`import.objects`, format `omf`).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter(prefix="/omf")
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Ids = Annotated[list[int], Field(max_length=5000)]
READ_ONLY = "Проект открыт только для чтения: экспорт недоступен."  # as `exporting/router.py` (it imports this)


@router.get("/exportable")
def exportable(svc: Svc) -> dict[str, Any]:
    from . import omf_job

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        out: dict[str, Any] = dict(omf_job.exportable(conn))
    out["read_only"] = bool(project.read_only)
    out["reason"] = READ_ONLY if project.read_only else None
    return out


class ExportBody(BaseModel):
    path: Annotated[str, Field(min_length=1, max_length=2000)]
    wireframe_ids: Ids = []
    line_file_ids: Ids = []
    block_model_ids: Ids = []
    overwrite: bool = False


@router.post("/export")
def export_omf(svc: Svc, s: Sched, body: ExportBody) -> dict[str, Any]:
    from . import omf_job

    project = svc.require_current(writable=True)  # 423: «только просмотр» (ТЗ 5.7), as `POST /export/objects`
    with dialect.read_tx(project.engine) as conn:
        params = omf_job.request(
            conn, path=body.path, wireframe_ids=body.wireframe_ids, line_file_ids=body.line_file_ids,
            block_model_ids=body.block_model_ids, overwrite=body.overwrite,
        )  # fmt: skip
    key, job_id = s.submit("export.omf", params, title=f"Экспорт OMF ({omf_job.target_path(body.path).name})")
    return {"source": key, "id": job_id}
