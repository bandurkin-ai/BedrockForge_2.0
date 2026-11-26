"""/api/v1/blocks — «Сравнить оценки» (подшаг 20.10; ТЗ 07-35 Д-37; `docs/progress/e20-api.md` §9.2).

The router has no prefix of its own: `blocks/router.py` includes it under `/blocks`.

  * `POST /blocks/{id}/compare` {field_a, field_b, domain?, out?, expected_version?} → {source, id} of the job
    `estimate.compare` (queue quick; `compare_job.py`; the result — §9.2). Checked before the job starts: the shapes
    (422 `bad_param {param}`), the model (404 `block_model_not_found`; 409 `version_conflict {version}` for a stale
    `expected_version`), the fields (404 `block_field_not_found {param}`; 422 `bad_param` for a field that is not a
    number, a domain that is not a category, a name of Δ that is A or B), the name of Δ (409 `field_exists {field}`
    for a field that is not a Δ of an earlier comparison). A project «только просмотр» — 423. The job not registered in
    this build — 422 `not_ready`.
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
FIELDS = ("field_a", "field_b", "domain", "out")  # what `compare_job.parse` reads of the body
KIND = "estimate.compare"  # = compare_job.KIND (this module loads the job only when it is needed)


@router.post("/{bm_id}/compare")
def compare(svc: Svc, s: Sched, bm_id: int, body: Payload) -> dict[str, Any]:
    from bf_core.jobs.registry import KINDS as REGISTERED
    from bf_core.storage.blobs import BlobStore

    from . import compare_job as cj
    from .model import Model

    if not isinstance(body, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна сравнения оценок.")
    spec, expected = cj.parse(bm_id, body)
    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        model = Model.open(conn, BlobStore(project.folder), bm_id)
    plan = cj.check(model, spec, expected)
    if KIND not in REGISTERED:
        raise ApiError(422, "not_ready", "Сравнение оценок ещё не подключено в этой сборке.", "Обновите программу.")
    params = {k: body[k] for k in FIELDS if k in body}  # the checked fields only, not whatever the client sent
    params.update({"model_id": bm_id, "expected_version": model.version, "rows": model.rows})
    key, job_id = s.submit(KIND, params, title=f"Сравнение оценок: {plan.a} и {plan.b} · {model.name}")
    return {"source": key, "id": job_id}
