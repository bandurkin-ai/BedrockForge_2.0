"""/api/v1/blocks — the grade–tonnage report of a block model (ТЗ 7.31; ADR 0023 §10; e17-api.md §10, §15).

The router has no prefix of its own: `blocks/router.py` includes it under `/blocks`.

  * `POST /blocks/{id}/report` {grades?, density?, cutoffs?, cutoff_field?, filter_id?, restrict?, categories?,
    groups?, curve?, csv?} → {source, id} of the job `blocks.report` (queue quick; `report.py`: the result and the
    defaults of 7.31). Checked before the job starts: the shapes (422 `bad_param {param}`), the model (404
    `block_model_not_found`), the fields (404 `block_field_not_found {param}`; 422 `bad_param` — not a number / not a
    category, no default grade or density), the labels of `categories` (422 `bad_param`), the size (422
    `report_too_big`), the filter (404 `filter_not_found`) and the fields the restrictor reads (422
    `filter_field_missing {field, model}`).
The report writes nothing: it works in a project «только просмотр». The job not registered in this build — 422
`not_ready`. A plain `def` route runs in the thread pool.
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
KIND = "blocks.report"  # = report.KIND (this module loads the job only when it is needed)


@router.post("/{bm_id}/report")
def report(svc: Svc, s: Sched, bm_id: int, body: Payload) -> dict[str, Any]:
    from bf_core.jobs.registry import KINDS as REGISTERED
    from bf_core.storage.blobs import BlobStore

    from . import calc_job, restrict
    from . import report as rp
    from .filters import schema as fs
    from .model import Model

    if not isinstance(body, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна отчёта.")
    spec = rp.parse(bm_id, body)
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        model = Model.open(conn, BlobStore(project.folder), bm_id)
        rp.make_plan(model, spec)
        tree = calc_job.rules_tree(conn, spec.filter_id, spec.restrict)
    if tree is not None:
        have = {str(f["name"]).casefold() for f in model.fields}
        for name in fs.fields_of(fs.normalize_rules(tree)):
            if name.casefold() not in have:
                raise restrict.field_missing(name, model.name)
    if KIND not in REGISTERED:
        raise ApiError(422, "not_ready", "Отчёт ещё не подключён в этой сборке.", "Обновите программу.")
    params = {**body, "model_id": bm_id, "rows": model.rows}
    key, job_id = s.submit(KIND, params, title=f"Содержание–тоннаж · {model.name}")
    return {"source": key, "id": job_id}
