"""/api/v1/intercepts — «Истинная мощность», «Рудные пересечения» (ADR 0021 §10; the forms — docs/progress/e15-api.md).

  * `POST /tt` {set_id, table, orientation, eps?, step?, angles?} → {source, id} of `intercept.tt` (heavy). 423
    `project_read_only`; 422 {code, message, hint, param} — `bad_param` (out of the ranges of 8.5.4), `no_orientation`;
    404 `set_not_found`, `table_not_found`; 409 `column_exists` {param, name}.
  * `POST /run` {set_id, table, field, cutoff, include_cutoff?, l_min?, min_by?, waste_max?, missing?, density?,
    fields?, orientation?, eps?, step?, name?, replace?} → {source, id} of `intercept.run` (heavy); the same refusals,
    409 `table_exists` {name, suggested, lost} — «Заменить?», again with `replace: true`.
  * `GET /results?set_id=` — the rows of `GET /composites/results` of the ops `tt` and `intercepts`.
  * `DELETE /results/{id}` — `DELETE /composites/results/{id}` of those ops, with the display «Рудные пересечения».
  * `POST /results/{id}/rebuild` {drop_lost?} → {source, id}: the job with the parameters of the result on the current
    tables (by name) and wireframes (by name and geometry); 404 `result_not_found` (another op too), 409 `source_gone`,
    `result_busy`, `dependents_lost` {lost}, `column_exists`.

`POST /tt` starts `intercept.tt` (`jobs.py`), `POST /run` — `intercept.run` (`jobs_run.py`); a job kind not
registered in this build — 422 `not_ready`. The params of a job: {"body": the canonical body (`spec.body`) with
`replace`, "rows": rows read (memory), "rebuild": result id | absent}. The previews (`POST /tt/preview`,
`/run/preview`) are `routes_preview`, included here; `/orientation/plane` — 15.13. Plain `def` routes run in the
thread pool.
"""

from __future__ import annotations

from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, Request

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_results, dh_tables
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from .routes_preview import router as preview_router

router = APIRouter(prefix="/intercepts")
router.include_router(preview_router)
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Payload = Annotated[dict[str, Any], Body()]
#: dh_results.op → the job kind.
KINDS = {"tt": "intercept.tt", "intercepts": "intercept.run"}


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна инструмента.")
    return raw


def _submit(s: Scheduler, kind: str, params: dict[str, Any], title: str) -> dict[str, Any]:
    from bf_core.jobs.registry import KINDS as REGISTERED

    if kind not in REGISTERED:
        raise ApiError(422, "not_ready", "Расчёт ещё не подключён в этой сборке.", "Обновите программу.")
    key, job_id = s.submit(kind, params, title=title)
    return {"source": key, "id": job_id}


def _tt_job(s: Scheduler, spec: Any, rebuild: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"body": spec.body, "rows": spec.rows}
    if rebuild is not None:
        params["rebuild"] = rebuild
    title = f"{'Перестроить: ' if rebuild else ''}Истинная мощность ({spec.table_name})"
    return _submit(s, KINDS["tt"], params, title)


def _run_job(s: Scheduler, spec: Any, rebuild: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"body": {**spec.body, "replace": spec.replace}, "rows": spec.rows}
    if rebuild is not None:
        params["rebuild"] = rebuild
    title = f"Перестроить: {spec.name}" if rebuild else f"{spec.name} ({spec.table_name})"
    return _submit(s, KINDS["intercepts"], params, title)


@router.post("/tt")
def tt(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from . import spec as sp

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        spec = sp.parse_tt(conn, _body(body))
        sp.check_columns(conn, spec)
    return _tt_job(s, spec)


@router.post("/run")
def run(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from bf_core.composites.routes_run import _taken

    from . import spec as sp

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        spec = sp.parse_run(conn, _body(body))
        _taken(conn, spec.set_id, spec.name, spec.replace)
    return _run_job(s, spec)


@router.get("/results")
def list_results(svc: Svc, set_id: int) -> dict[str, Any]:
    from bf_core.composites import results

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"results": [r for r in results.list_results(conn, set_id) if r["op"] in KINDS]}


def _record(conn: sa.Connection, result_id: int) -> dict[str, Any]:
    """A record of the ops `tt`, `intercepts`; 404 `result_not_found` — none, or of another op."""
    rec = conn.execute(sa.select(dh_results).where(dh_results.c.id == result_id)).mappings().first()
    if rec is None or rec["op"] not in KINDS:
        raise ApiError(404, "result_not_found", "Результат обработки не найден.", "Обновите дерево проекта.")
    return dict(rec)


@router.delete("/results/{result_id}")
def delete_result(svc: Svc, request: Request, result_id: int) -> dict[str, Any]:
    """«Удалить»: the columns of «Истинная мощность» (hidden: «Отменить» in the journal brings them back), the table
    of «Рудные пересечения» with its display; the record."""
    from bf_core.composites import routes_results

    from . import display

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        rec = _record(conn, result_id)
        key = conn.execute(sa.select(dh_tables.c.name_key).where(dh_tables.c.id == rec["table_id"])).scalar()
    out = routes_results.delete_result(svc, request, result_id)
    if rec["op"] == "intercepts" and key is not None and not out["deleted_charts"]:
        with dialect.write_tx(project.engine) as conn:  # until `results.delete_result` knows this op's display
            out["deleted_charts"] = display.drop_display(conn, int(rec["set_id"]), key)
        for cid in out["deleted_charts"]:
            request.app.state.core.events.publish(
                "objects.changed", {"kind": "chart", "set_id": rec["set_id"], "id": cid, "deleted": True}
            )
    return out


@router.post("/results/{result_id}/rebuild")
def rebuild(
    svc: Svc, s: Sched, result_id: int, body: Annotated[dict[str, Any] | None, Body()] = None
) -> dict[str, Any]:
    """The parameters of the result on the current tables and wireframes (`spec.rebind`), checked again."""
    from bf_core.composites import jobs as cj
    from bf_core.composites import results
    from bf_core.composites.routes_run import _current

    from . import spec as sp

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        rec = _record(conn, result_id)
        set_id = int(rec["set_id"])
        inputs = rec["inputs"] or {}
        now = [_current(conn, set_id, t) for t in inputs.get("tables") or []]
        if not now:
            raise ApiError(409, "source_gone", "Источник результата не найден.", "Удалите результат.")
        again = sp.rebind(rec["params"] or {}, [int(t.id) for t in now], results.current_meshes(conn, inputs))
        again["set_id"] = set_id
        if rec["op"] == "tt":
            tspec = sp.parse_tt(conn, again)
            sp.check_columns(conn, tspec)
        else:
            table = conn.execute(
                sa.select(dh_tables.c.name, dh_tables.c.state).where(dh_tables.c.id == rec["table_id"])
            ).first()
            if table is None or table.state != "ready":
                raise ApiError(409, "result_busy", "Результат сейчас пересчитывается.", "Дождитесь окончания задания.")
            cj.refuse_lost(conn, int(rec["table_id"]), body)
            rspec = sp.parse_run(conn, {**again, "name": table.name, "replace": True})
    if rec["op"] == "tt":
        return _tt_job(s, tspec, result_id)
    return _run_job(s, rspec, result_id)
