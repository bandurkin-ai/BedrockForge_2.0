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
    The protocol only of a fresh result of the current `algo`: otherwise 409 `stale` (решение 19).
  * `POST /results/{id}/rebuild` {drop_lost?} of an ore result: the job with its params on the current samples
    (found by name), the table replaced; the same hash — no computation; 409 `dependents_lost` as
    `routes_run.rebuild`. Other results — `routes_prep.rebuild`.

Registered before `routes_prep` and `routes_run`: their `/results/{id}/rebuild` and `/{op}/preview` take any op.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, Query

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


def _protocol(made: Any, unit: str | None) -> dict[str, Any]:
    """The protocol of the hole (`ore_protocol.hole_protocol`, 12.15): `events` — the records of the kernel without
    `hole` (`row0`, `row1` — its samples, 0-based; `n`, `mark`, `text`), and the form И1, сл. 17 — `rows`,
    `columns`, `cells`; `algo`, `mode`, `stats`."""
    from .ore_protocol import hole_protocol

    hp = hole_protocol(made.lines, made.kernel, 0, unit=unit or "").as_dict()
    events = [{k: v for k, v in e.items() if k != "hole"} for e in hp["events"]]
    return {**{k: v for k, v in hp.items() if k not in ("hole", "unit", "intervals")}, "events": events}


def _form(project: Any, spec: Any, hole_id: int, hole: str) -> tuple[dict[str, Any], Any]:
    """The answer of the preview and of the protocol for one hole (without `set_summary`)."""
    from . import jobs_coding as jc
    from . import ore_job as oj
    from .jobs_prep import clean
    from .routes_run import _nulls

    src, dom = oj.read_ore(project.engine, spec, holes=[hole_id])
    with jc.reading(project):
        made = oj.compute_ore(spec, src, dom, _st(project, spec))
    out = oj.out_table(spec, src, made, None)
    values = {c.name: _nulls(v) for c, v in zip(out.columns, out.values, strict=True) if c.name not in ("X", "Y", "Z")}
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
        "protocol": clean(_protocol(made, spec.unit)),
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
    from .routes_run import _current

    set_id = int(rec["set_id"])
    tables = (rec["inputs"] or {}).get("tables") or []
    now = [_current(conn, set_id, t) for t in tables]
    if not now:
        raise ApiError(409, "source_gone", "Источник результата не найден.", "Удалите результат.")
    again = {**(rec["params"] or {}), "set_id": set_id, "table": str(now[0].id)}
    dom = again.get("domain")
    if isinstance(dom, dict) and str(dom.get("field") or "").strip():
        again["domain"] = {**dom, "table": str(now[1].id if len(now) > 1 else now[0].id)}
    return {**again, "_rows": sum(int(t.row_count or 0) for t in now)}


def _record(conn: Any, result_id: int) -> dict[str, Any] | None:
    rec = conn.execute(sa.select(dh_results).where(dh_results.c.id == result_id)).mappings().first()
    return None if rec is None else dict(rec)


@router.get("/results/{result_id}/protocol")
def protocol(svc: Svc, result_id: int, hole: Annotated[str, Query(min_length=1)]) -> dict[str, Any]:
    from . import ore_job as oj
    from . import results

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        rec = _record(conn, result_id)
        if rec is None:
            raise ApiError(404, "result_not_found", "Результат обработки не найден.", "Обновите дерево проекта.")
        if rec["op"] != oj.OP:
            raise ApiError(422, "no_protocol", "У этого результата нет протокола оконтуривания.",
                           "Протокол есть у результата «Рудные интервалы».")  # fmt: skip
        set_id = int(rec["set_id"])
        reason = results._reason(conn, results._state(conn, set_id), rec["inputs"] or {})
        if reason is None and rec["algo"] != oj.ALGO:
            reason = "изменились правила выделения интервалов в этой версии программы"
        if reason is not None:
            hint = "Перестройте результат, затем откройте протокол."
            raise ApiError(409, "stale", f"Результат устарел: {reason}.", hint)
        params = _current_params(conn, rec)
        params.pop("_rows")
        spec = oj.parse_ore(conn, {k: v for k, v in params.items() if k != "name"})
        hole_id = conn.execute(
            sa.select(dh_holes.c.id).where(dh_holes.c.set_id == set_id, dh_holes.c.name == hole)
        ).scalar()
        if hole_id is None:
            raise ApiError(404, "hole_not_found", f"Скважины «{hole}» нет в наборе.", "Выберите скважину из списка.")
    answer, _made = _form(project, spec, int(hole_id), hole)
    return answer


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
        if rec is None or rec["op"] != oj.OP:
            return routes_prep.rebuild(svc, s, result_id, body)
        table = conn.execute(
            sa.select(dh_tables.c.name, dh_tables.c.state).where(dh_tables.c.id == rec["table_id"])
        ).first()
        if table is None or table.state != "ready":
            raise ApiError(409, "result_busy", "Результат сейчас пересчитывается.", "Дождитесь окончания задания.")
        again = _current_params(conn, rec)
        lost = cj.lost_dependents(conn, int(rec["table_id"]))
        if lost and not (body or {}).get("drop_lost"):
            names = ", ".join(f"«{x['name']}»" for x in lost)
            raise ApiError(
                409,
                "dependents_lost",
                f"При перестроении пропадут результаты: {names}.",
                "Подтвердите перестроение или сначала сохраните эти результаты.",
                {"lost": lost},
            )
    rows = again.pop("_rows")
    params = {"body": {**again, "name": table.name, "replace": True}, "rows": rows, "rebuild": result_id}
    key, job_id = s.submit(oj.KIND, params, title=f"Перестроить: {table.name}")
    return {"source": key, "id": job_id}
