"""/api/v1/blocks — the export of a block model to a file (подшаг 17.38; ТЗ 8.7.7, 7.35 Д-17; ADR 0023 §12, decision 20;
docs/progress/e17-api.md §11).

The router has no prefix of its own: `blocks/router.py` includes it under `/blocks`.

  * `POST /blocks/{id}/export` {format, path, fields?, filter_id?, restrict?, header?, encoding?, delimiter?, decimal?,
    empty?, overwrite?} → {source, id} of the job `blocks.export` (queue heavy; `export.py`: the result is `{path,
    format, rows, total, bytes, seconds, warnings}`). Checked before the job starts (`export.request`): 404 (model,
    field, filter), 409 `files_exist {files}`, 422 `bad_param {param}`, `bad_path`, `omf_subblocks`,
    `omf_not_exportable`, `filter_field_missing`.
The export writes nothing of the project but the row of the journal: it works in a project «только просмотр». The job
not registered in this build — 422 `not_ready`. A plain `def` route runs in the thread pool.

  * `POST /blocks/{id}/check` {} → {source, id} of the job `blocks.check` (queue quick; `check_job.py`: the checks of
    7.31 and Т8.7-5 are made again and written into `meta.checks`, the version does not change; the result is
    `{model_id, checks, events}`); 404 `block_model_not_found`, 423 `project_read_only` (it writes the meta).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Payload = Annotated[Any, Body()]


@router.post("/{bm_id}/check")
def check_model(svc: Svc, s: Sched, bm_id: int) -> dict[str, Any]:
    from bf_core.jobs.registry import KINDS as REGISTERED
    from bf_core.storage.blobs import BlobStore

    from . import check_job
    from .model import ANY, Model

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        Model.open(conn, BlobStore(project.folder), bm_id, ANY)  # 404 for a model that is not there
    if check_job.KIND not in REGISTERED:
        raise ApiError(422, "not_ready", "Проверка блочных моделей не подключена в этой сборке.",
                       "Обновите программу.")
    key, job_id = s.submit(check_job.KIND, {"model_id": bm_id}, title=f"{check_job.TITLE} №{bm_id}")
    return {"source": key, "id": job_id}


@router.post("/{bm_id}/export")
def export_model(svc: Svc, s: Sched, bm_id: int, body: Payload) -> dict[str, Any]:
    from bf_core.jobs.registry import KINDS as REGISTERED

    from . import export as bx

    if not isinstance(body, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна экспорта.")
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        params = bx.request(conn, project.folder, bm_id, body)
    if bx.KIND not in REGISTERED:
        raise ApiError(422, "not_ready", "Экспорт блочных моделей не подключён в этой сборке.", "Обновите программу.")
    key, job_id = s.submit(bx.KIND, params, title=f"{bx.TITLE} ({params['format'].upper()})")
    return {"source": key, "id": job_id}
