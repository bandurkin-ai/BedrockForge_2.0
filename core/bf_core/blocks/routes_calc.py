"""/api/v1/blocks — the calculator of a block model (ТЗ 8.7.4; ADR 0023 §7; e17-api.md §8).

The router has no prefix of its own: `blocks/router.py` includes it under `/blocks`.

  * `POST /blocks/calc/check` {model_id, expr, dtype?} → {text, fields_used, out_kind: "number" | "bool" |
    "category", dtype}, synchronously, nothing is written. 404 `block_model_not_found`; 422 `expr_syntax {pos, line,
    column, char, reason, param}`, `expr_field {pos, field, …}`, `expr_type {pos, …}` (a category from a number and
    the other way round, text in arithmetic…), `bad_param {param}`;
  * `POST /blocks/{id}/calc` {expected_version, expr, field, dtype?, unit?, description?, filter_id?, restrict?,
    outside: "keep" | "nan", replace: false} → {source, id} of the job `blocks.calc` (queue quick; `calc_job.py`).
    Checked before the job starts: the shapes (422 `bad_param {param}`), the model (404; 409 `version_conflict
    {version}`), the expression with its place (422 `expr_*`), the name (409 `field_exists {field}` unless `replace`:
    the window asks «Заменить?», № 69), the filter (404 `filter_not_found`) and the fields the restrictor reads
    (422 `filter_field_missing {field, model}`). The result of the job is in `calc_job.py`.
A project «только просмотр» refuses the job (423); the check is read-only. The job not registered in this build —
422 `not_ready`. Plain `def` routes run in the thread pool.
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
KIND = "blocks.calc"  # = calc_job.KIND (this module loads the job only when it is needed)
OUT_KIND = {"num": "number", "bool": "bool", "cat": "category"}


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна калькулятора.")
    return raw


def _open(svc: ProjectService, model_id: int, *, writable: bool = False) -> tuple[Any, Any]:
    from bf_core.storage.blobs import BlobStore

    from .model import Model

    project = svc.require_current(writable=writable)
    with dialect.read_tx(project.engine) as conn:
        return project, Model.open(conn, BlobStore(project.folder), model_id)


@router.post("/calc/check")
def check(svc: Svc, body: Payload) -> dict[str, Any]:
    from . import calc_job as cj

    raw = _body(body)
    model_id = raw.get("model_id")
    if isinstance(model_id, bool) or not isinstance(model_id, int):
        raise cj.bad_param("model_id", "Нужен номер блочной модели.")
    dtype = raw.get("dtype")
    if dtype is not None and dtype not in cj.DTYPES:
        raise cj.bad_param("dtype", "Неизвестный тип поля.", "Допустимо: " + ", ".join(cj.DTYPES) + ".")
    expr = raw.get("expr")
    if not isinstance(expr, str) or not expr.strip():
        raise cj.bad_param("expr", "Введите выражение.", "Например: IF(AU > 1, AU * 2, 0).")
    _, model = _open(svc, model_id)
    prog, stored = cj.compile_for(model, expr, dtype)
    return {"text": prog.expr.to_text(), "fields_used": list(prog.fields_used), "out_kind": OUT_KIND[prog.out_kind],
            "dtype": stored}  # fmt: skip


@router.post("/{bm_id}/calc")
def calc(svc: Svc, s: Sched, bm_id: int, body: Payload) -> dict[str, Any]:
    from bf_core.jobs.registry import KINDS as REGISTERED

    from . import calc_job as cj
    from . import restrict
    from .filters import schema as fs

    raw = _body(body)
    spec = cj.parse(bm_id, raw)
    project, model = _open(svc, bm_id, writable=True)
    if model.version != spec.expected_version:
        from .fields_store import version_conflict

        raise version_conflict(model.version)
    cj.compile_for(model, spec.expr, spec.dtype)
    cj.existing_field(model, spec)
    with dialect.read_tx(project.engine) as conn:
        tree = cj.rules_tree(conn, spec.filter_id, spec.restrict)
    if tree is not None:
        have = {str(f["name"]).casefold() for f in model.fields}
        for name in fs.fields_of(fs.normalize_rules(tree)):
            if name.casefold() not in have:
                raise restrict.field_missing(name, model.name)
    if KIND not in REGISTERED:
        raise ApiError(422, "not_ready", "Расчёт ещё не подключён в этой сборке.", "Обновите программу.")
    params = {**raw, "model_id": bm_id, "rows": model.rows}
    key, job_id = s.submit(KIND, params, title=f"Калькулятор: {spec.field} · {model.name}")
    return {"source": key, "id": job_id}
