"""/api/v1/composites — ore intervals by the ГКЗ method (ТЗ 7.35 Д-26; ADR 0018 §9, §11, решения 19, 22, 26): the job,
its preview, the protocol of a hole, «Перестроить».

  * `POST /ore` {set_id, table, field, cutoff, m_min, m_waste_max, mc_min?, c_min?, mode, missing, missing_max?,
    thickness {source, field?}, domain?, contacts_values?, name?, replace?} → {source, id} of
    `composite.ore_intervals` (heavy; `ore_job`). 423 `project_read_only`; 422 {code, message, hint, param} —
    `ore_bad_param`, `ore_no_domain`, `bad_param`, `domain_missing`, `name_is_source`, `wireframe_open`; 404
    `table_not_found`, `field_not_found`; 409 `column_exists` — ORE_ID / ORE_CLS of the samples not written by
    Д-26; 409 `table_exists` {name, suggested, lost} — «Заменить?», again with `replace: true`. The result of the
    job: {set_id, result_id, table_id, name, chart_id, rows, columns, replaced, fresh, journal, events}.
  * `POST /ore/preview` {params, hole_id} and `GET /results/{id}/protocol?hole=<name>` → one form: {hole, unit,
    samples {from, to, values {<field>, ORE_CLS (class names), ORE_ID}}, result {from, to, values {CLASS, NESTED,
    M, C, MC, BASIS, ORE_ID, N_SAMPLES, WASTE_LEN, FLAG, LABEL, MANUAL, …}}, protocol {events, rows, columns,
    cells, …}, summary, set_summary, notes}. One hole, the same computation as the job (synchronous, in the thread
    pool of the API; the kernels imported lazily). `protocol` — `ore_protocol.hole_protocol`: `events`
    (`OreEvents.records` without `hole`; `row0`, `row1` — samples of the hole, 0-based) and the form И1, сл. 17
    (`rows`, `columns`, `cells`). `set_summary` — the summary of the whole table when it has ≤ 200 000 rows
    (cached by revisions and parameters), else null; the protocol of a result has null. Nothing is written.
    The protocol only of a fresh result of the current `algo`: otherwise 409 `stale` (решение 19). The manual
    edits of the hole (`report.edits`) are applied: the rows (MANUAL), the samples and the protocol after them.
  * `POST /results/{id}/protocol` {format, path, holes?, overwrite?} → {source, id} of `composite.ore_protocol`
    (`ore_protocol_job`): XLSX / CSV; 409 `stale`, `files_exist {files}`.
  * `POST /results/{id}/edit` {hole, action, from, to} → {rows, violations, report {edits}, hole}: the hole in
    one transaction (`ore_edit_job`; решение 24); 422 `ore_edit_refused`, 409 `stale`.
  * `POST /conditions` {…, cutoffs[], m_min[], m_waste_max[], exclude?} → {source, id} of
    `composite.conditions_analysis` (`ore_conditions_job`): a result without a table, `report.variants`.
  * `POST /results/{id}/rebuild` {drop_lost?} of an ore result: the job with its params on the current samples
    (found by name), the table replaced; the same hash — no computation, unless the result has manual edits
    (they are lost: computed again); 409 `dependents_lost` as `routes_run.rebuild`. «Анализ кондиций» — its job
    again. Other results — `routes_prep.rebuild`.

Registered before `routes_prep` and `routes_run`: their `/results/{id}/rebuild` and `/{op}/preview` take any op.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, Query, Request

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_holes, dh_results, dh_tables
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Payload = Annotated[dict[str, Any], Body()]
SET_SUMMARY_ROWS = 200_000

_lock = threading.Lock()
_whole_cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна инструмента.")
    return raw


@router.post("/ore")
def ore(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from . import ore_job as oj
    from .routes_run import _taken

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        spec = oj.parse_ore(conn, _body(body))
        oj.check_columns(conn, spec)
        _taken(conn, spec.set_id, spec.name, spec.replace)
    params = {"body": {**spec.body, "replace": spec.replace}, "rows": spec.rows}
    key, job_id = s.submit(oj.KIND, params, title=f"{spec.name} ({spec.table_name})")
    return {"source": key, "id": job_id}


# --- the preview and the protocol of one hole --------------------------------------------------------------


def _revisions(conn: Any, spec: Any) -> tuple[Any, ...]:
    from bf_core.db.schema import objects

    out: list[Any] = []
    for tid, _f in spec.tables():
        t = conn.execute(sa.select(dh_tables.c.rev, dh_tables.c.created_at).where(dh_tables.c.id == tid)).one()
        out += [tid, str(t.created_at), int(t.rev)]
    if spec.domain is not None and spec.domain.wireframes is not None:  # the bodies cut the paths of the set
        out.append(conn.execute(sa.select(objects.c.version).where(objects.c.id == spec.set_id)).scalar())
    return tuple(out)


def _st(project: Any, spec: Any) -> Any:
    """The trajectories when the domain is by wireframes (the preview needs no X, Y, Z), else None."""
    from . import source

    by_wf = spec.domain is not None and spec.domain.wireframes is not None
    return source.trajectories(project, spec.set_id) if by_wf else None


def _whole(project: Any, spec: Any) -> dict[str, Any] | None:
    """The summary of the whole table (≤ 200 000 rows), cached by revisions and parameters; None — larger."""
    from . import jobs_coding as jc
    from . import ore_job as oj
    from . import strips
    from .jobs_prep import clean

    if spec.rows > SET_SUMMARY_ROWS:
        return None
    with dialect.read_tx(project.engine) as conn:
        key = (project.uid, *_revisions(conn, spec),
               json.dumps(spec.hash_params(), sort_keys=True, ensure_ascii=False))  # fmt: skip
    with _lock:
        hit = _whole_cache.get(key)
        if hit is not None:
            _whole_cache.move_to_end(key)
            return hit
    src, dom = oj.read_ore(project.engine, spec, cache_dir=project.folder / "cache" / "tables")
    if src.rows > SET_SUMMARY_ROWS:
        return None
    with jc.reading(project):
        made = oj.compute_ore(spec, src, dom, _st(project, spec), threads=strips.default_threads())
    out: dict[str, Any] = clean(made.res.summary)
    with _lock:
        _whole_cache[key] = out
        while len(_whole_cache) > 8:
            _whole_cache.popitem(last=False)
    return out


def _protocol(made: Any, unit: str | None, edits: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The protocol of the hole (`ore_protocol.hole_protocol`, 12.15): `events` — the records of the kernel without
    `hole` (`row0`, `row1` — its samples, 0-based; `n`, `mark`, `text`), and the form И1, сл. 17 — `rows`,
    `columns`, `cells`; `algo`, `mode`, `stats`; with manual edits — also `edits`, `violations` (12.17)."""
    from .ore_protocol import hole_protocol

    hp = hole_protocol(made.lines, made.kernel, 0, unit=unit or "", edits=edits or None).as_dict()
    events = [{k: v for k, v in e.items() if k != "hole"} for e in hp["events"]]
    return {**{k: v for k, v in hp.items() if k not in ("hole", "unit", "intervals")}, "events": events}


def _form(project: Any, spec: Any, hole_id: int, hole: str,
          edits: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], Any]:  # fmt: skip
    """The answer of the preview and of the protocol for one hole (without `set_summary`); `edits` — the manual
    edits of the hole stored in the result (`report.edits`): the rows, the samples and the protocol after them."""
    from . import jobs_coding as jc
    from . import ore_edit_job as oe
    from . import ore_job as oj
    from .jobs_prep import clean
    from .routes_run import _nulls

    src, dom = oj.read_ore(project.engine, spec, holes=[hole_id])
    with jc.reading(project):
        made = oj.compute_ore(spec, src, dom, _st(project, spec))
    made, manual, _he = oe.with_edits(spec, src, made, edits if src.rows else None)
    out = oe.hole_rows(spec, src, made, manual, None)
    values = oe.values_of(out)["values"]
    samples = {spec.field: _nulls(src.numbers[spec.field])}
    samples.update({c.name: _nulls(v) for c, v in oj.sample_columns(made)})
    notes = list(made.journal)
    if not src.rows:
        notes.insert(0, f"В скважине {hole} нет проб в таблице «{spec.table_name}».")
    answer = {
        "hole": hole,
        "unit": spec.unit,
        "samples": {"from": _nulls(src.from_m), "to": _nulls(src.to_m), "values": samples},
        "result": {"from": _nulls(out.from_m), "to": _nulls(out.to_m), "values": values},
        "protocol": clean(_protocol(made, spec.unit, edits if src.rows else None)),
        "summary": clean(made.res.summary),
        "set_summary": None,
        "notes": notes,
    }
    return answer, made


@router.post("/ore/preview")
def ore_preview(svc: Svc, body: Payload) -> dict[str, Any]:
    from . import ore_job as oj
    from .routes_run import _hole

    raw = _body(body)
    params = {k: v for k, v in _body(raw.get("params")).items() if k not in ("name", "replace")}
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        spec = oj.parse_ore(conn, params)
        hole = _hole(conn, spec.set_id, raw.get("hole_id"))
    answer, _made = _form(project, spec, int(raw["hole_id"]), hole)
    answer["set_summary"] = _whole(project, spec)
    return answer


def _current_params(conn: Any, rec: dict[str, Any]) -> dict[str, Any]:
    """The params of a result on the current tables of its inputs (found by name: a replaced one has another id)."""
    from .results import with_meshes
    from .routes_run import _current

    set_id = int(rec["set_id"])
    tables = (rec["inputs"] or {}).get("tables") or []
    now = [_current(conn, set_id, t) for t in tables]
    if not now:
        raise ApiError(409, "source_gone", "Источник результата не найден.", "Удалите результат.")
    again = {**with_meshes(conn, rec["params"] or {}, rec["inputs"] or {}), "set_id": set_id, "table": str(now[0].id)}
    dom = again.get("domain")
    if isinstance(dom, dict) and str(dom.get("field") or "").strip():
        again["domain"] = {**dom, "table": str(now[1].id if len(now) > 1 else now[0].id)}
    return {**again, "_rows": sum(int(t.row_count or 0) for t in now)}


def _record(conn: Any, result_id: int) -> dict[str, Any] | None:
    rec = conn.execute(sa.select(dh_results).where(dh_results.c.id == result_id)).mappings().first()
    return None if rec is None else dict(rec)


def record_of(conn: Any, result_id: int) -> dict[str, Any]:
    """The record of a result; 404 `result_not_found`."""
    rec = _record(conn, result_id)
    if rec is None:
        raise ApiError(404, "result_not_found", "Результат обработки не найден.", "Обновите дерево проекта.")
    return rec


def stale_reason(conn: Any, rec: dict[str, Any], algo: str) -> str | None:
    """Why a result is out of date for its protocol, export or edit (решение 19); None — fresh."""
    from . import results

    reason = results._reason(conn, results._state(conn, int(rec["set_id"])), rec["inputs"] or {})
    if reason is None and rec["algo"] != algo:
        reason = "изменились правила выделения интервалов в этой версии программы"
    return reason


def fresh_spec(conn: Any, rec: dict[str, Any], what: str = "откройте протокол") -> Any:
    """`OreSpec` of a fresh ore result on its current tables: 422 `no_protocol` — not an ore result, 409 `stale`
    — out of date or of another `algo` (решение 19)."""
    from . import ore_job as oj

    if rec["op"] != oj.OP:
        raise ApiError(422, "no_protocol", "У этого результата нет протокола оконтуривания.",
                       "Протокол есть у результата «Рудные интервалы».")  # fmt: skip
    reason = stale_reason(conn, rec, oj.ALGO)
    if reason is not None:
        raise ApiError(409, "stale", f"Результат устарел: {reason}.", f"Перестройте результат, затем {what}.")
    params = _current_params(conn, rec)
    params.pop("_rows")
    return oj.parse_ore(conn, {k: v for k, v in params.items() if k != "name"})


@router.get("/results/{result_id}/protocol")
def protocol(svc: Svc, result_id: int, hole: Annotated[str, Query(min_length=1)]) -> dict[str, Any]:
    from . import ore_edit_job as oe

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        rec = record_of(conn, result_id)
        spec = fresh_spec(conn, rec)
        hole_id = conn.execute(
            sa.select(dh_holes.c.id).where(dh_holes.c.set_id == spec.set_id, dh_holes.c.name == hole)
        ).scalar()
        if hole_id is None:
            raise ApiError(404, "hole_not_found", f"Скважины «{hole}» нет в наборе.", "Выберите скважину из списка.")
    answer, _made = _form(project, spec, int(hole_id), hole, oe.hole_edits(rec["report"], hole))
    return answer


@router.post("/results/{result_id}/protocol")
def protocol_export(svc: Svc, s: Sched, result_id: int, body: Payload) -> dict[str, Any]:
    """The protocol (or the variants of «Анализ кондиций») to XLSX / CSV: the job `composite.ore_protocol`."""
    from . import ore_protocol_job as pj

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        params, title = pj.parse_export(conn, record_of(conn, result_id), _body(body))
    key, job_id = s.submit(pj.KIND, params, title=title)
    return {"source": key, "id": job_id}


@router.post("/results/{result_id}/edit")
def edit(svc: Svc, request: Request, result_id: int, body: Payload) -> dict[str, Any]:
    """Manual edit of the intervals of one hole (`ore_edit_job`; решение 24)."""
    from . import ore_edit_job as oe

    project = svc.require_current(writable=True)
    out = oe.edit_result(project, result_id, _body(body))
    events = request.app.state.core.events
    for name, payload in out.pop("events"):
        events.publish(name, payload)
    return out


@router.post("/conditions")
def conditions(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    """«Анализ кондиций»: the job `composite.conditions_analysis` (a result without a table)."""
    from . import ore_conditions_job as cj

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        params, title = cj.parse_conditions(conn, _body(body))
    key, job_id = s.submit(cj.KIND, params, title=title)
    return {"source": key, "id": job_id}


# --- «Перестроить» -----------------------------------------------------------------------------------------


@router.post("/results/{result_id}/rebuild")
def rebuild(
    svc: Svc, s: Sched, result_id: int, body: Annotated[dict[str, Any] | None, Body()] = None
) -> dict[str, Any]:
    from . import jobs as cj
    from . import ore_job as oj
    from . import routes_prep

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        rec = _record(conn, result_id)
        if rec is not None and rec["op"] == "conditions":
            from . import ore_conditions_job as ocj

            again = _current_params(conn, rec)
            again.pop("_rows")
            params, title = ocj.parse_conditions(conn, {**again, "name": rec["name"]})
            key, job_id = s.submit(ocj.KIND, params, title=f"Перестроить: {rec['name']}")
            return {"source": key, "id": job_id}
        if rec is None or rec["op"] != oj.OP:
            return routes_prep.rebuild(svc, s, result_id, body)
        table = conn.execute(
            sa.select(dh_tables.c.name, dh_tables.c.state).where(dh_tables.c.id == rec["table_id"])
        ).first()
        if table is None or table.state != "ready":
            raise ApiError(409, "result_busy", "Результат сейчас пересчитывается.", "Дождитесь окончания задания.")
        again = _current_params(conn, rec)
        cj.refuse_lost(conn, int(rec["table_id"]), body)
    rows = again.pop("_rows")
    params = {"body": {**again, "name": table.name, "replace": True}, "rows": rows, "rebuild": result_id}
    if (rec["report"] or {}).get("edits"):  # computed again from the samples: the manual edits are lost
        params.pop("rebuild")
    key, job_id = s.submit(oj.KIND, params, title=f"Перестроить: {table.name}")
    return {"source": key, "id": job_id}
