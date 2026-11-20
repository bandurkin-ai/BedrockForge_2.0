"""/api/v1/composites — the jobs of sample preparation, the analysis of the top cut, their previews and «Перестроить»
(ADR 0018 §7, §11, решения 8, 12, 26).

  * `POST /topcut` {set_id, table, field, domain?, percentile? | value? | thresholds?}, `POST /equivalent` {set_id,
    table, base, base_price, base_recovery?, others, nan_zero?, name?, units…}, `POST /declus` {set_id, table, field,
    method, cmin?, cmax?, ncell?, noff?, minmax?, anisy?, anisz?} → {source, id} of `composite.topcut`,
    `composite.equivalent`, `composite.declus` (heavy: fields written in place into the table). 423
    `project_read_only`; 422 {code, message, hint, param} — out of the ranges of 8.2.2 (`bad_param`, `unit_unknown`,
    StatsError of `stats/*` the same way); 404 `table_not_found`, 422 `field_not_found`; 409 `column_exists` — a
    field of that name the operation did not write.
  * `POST /coding` — 422 `not_ready` until the kernel of coding by wireframes is in this build (12.7).
  * `POST /topcut/analysis` {set_id, table, field, domain?, percentiles?, decile_limit?, percentile_limit?} → the
    answer of `topcut.topcut_analysis` as it is (field, limits, domains [{domain, n, …, curves, deciles, top,
    quantiles}]): synchronous, over the whole table; nothing is written (решение 8).
  * `POST /topcut/preview`, `POST /equivalent/preview` {params, hole_id} → {hole, samples, result, summary,
    set_summary, notes}: one hole, the same computation as the job; the thresholds of the top cut by percentile
    come from the whole table (cached by revisions and parameters) — the preview of a hole is the job's rows;
    `set_summary` — the summary of the whole table (top cut: when computed for the thresholds or ≤ 200 000 rows;
    equivalent: ≤ 200 000 rows), else null. The top cut adds `thresholds` {domain: T | null}. Nothing is written.
    `POST /declus/preview` — 422 `no_preview` (the weights depend on all the samples; the window has none);
    `POST /coding/preview` — `not_ready` (12.7).
  * `POST /results/{id}/rebuild` {drop_lost?} — of a top cut, an equivalent, a DECL_W: the same job with the params
    of the result on the current table (the same hash — no computation); other results — `routes_run.rebuild`.

Registered before `routes_run` (its `/{op}/preview` takes any op). The kernels are imported lazily.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_results, dh_tables
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Payload = Annotated[dict[str, Any], Body()]
SET_SUMMARY_ROWS = 200_000
PREP_OPS = ("topcut", "equivalent", "declus")


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна инструмента.")
    return raw


def _coding_not_ready() -> ApiError:
    return ApiError(
        422,
        "not_ready",
        "Кодирование каркасами: расчёт ещё не подключён в этой сборке.",
        "Обновите приложение до сборки с кодированием каркасами.",
    )


def _submit(svc: ProjectService, s: Scheduler, body: dict[str, Any], op: str) -> dict[str, Any]:
    from . import jobs_prep as jp

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        spec = jp.parse_prep(conn, _body(body), op)
        jp.check_columns(conn, spec)
    title = f"{spec.name} ({spec.table_name})"
    key, job_id = s.submit(jp.KINDS[op], {"body": spec.body, "rows": spec.rows}, title=title)
    return {"source": key, "id": job_id}


@router.post("/topcut")
def topcut(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    return _submit(svc, s, body, "topcut")


@router.post("/equivalent")
def equivalent(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    return _submit(svc, s, body, "equivalent")


@router.post("/declus")
def declus(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    return _submit(svc, s, body, "declus")


@router.post("/coding")
def coding(svc: Svc, body: Payload) -> dict[str, Any]:
    svc.require_current(writable=True)
    raise _coding_not_ready()  # TODO(12.7): `composite.code` with `composites/coding.py`


# --- the analysis of the top cut ---------------------------------------------------------------------------


def _percentiles(raw: Any) -> list[float] | None:
    if raw is None or raw == "":
        return None
    items = raw.replace(",", ".").replace(";", " ").split() if isinstance(raw, str) else raw
    if not isinstance(items, list):
        items = [items]
    try:
        return [float(x) for x in items]
    except (TypeError, ValueError):
        raise ApiError(
            422, "bad_param", "Перцентили кривых: нужны числа от 0 до 100.", "Задайте, например, 90; 95; 99.",
            {"param": "percentiles"},
        ) from None  # fmt: skip


@router.post("/topcut/analysis")
def topcut_analysis(svc: Svc, body: Payload) -> dict[str, Any]:
    from . import jobs as cj
    from . import jobs_prep as jp
    from .topcut import topcut_analysis as analyse

    raw = _body(body)
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        spec = jp.parse_prep(conn, raw, "topcut", kernel=False)
    src, dom = jp.read_prep(project.engine, spec, cache_dir=project.folder / "cache" / "tables")
    codes, names = jp.src_codes(spec, src, dom)
    f = spec.fields[0]
    limits = {k: raw[k] for k in ("decile_limit", "percentile_limit") if raw.get(k) not in (None, "")}
    try:
        out: dict[str, Any] = analyse(src.numbers[f], src.to_m - src.from_m, codes=codes, names=names, field=f,
                                      percentiles=_percentiles(raw.get("percentiles")), **limits)  # fmt: skip
    except ValueError as exc:  # CompositeError → 422
        raise cj.as_api(exc) from None
    return out


# --- previews ------------------------------------------------------------------------------------------------

_lock = threading.Lock()
_whole_cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()


def _revisions(conn: Any, spec: Any) -> tuple[Any, ...]:
    out: list[Any] = []
    for tid, _f in spec.tables():
        t = conn.execute(sa.select(dh_tables.c.rev, dh_tables.c.created_at).where(dh_tables.c.id == tid)).one()
        out += [tid, str(t.created_at), int(t.rev)]
    return tuple(out)


def _whole(project: Any, spec: Any) -> dict[str, Any] | None:
    """The job over the whole table — {thresholds, summary, notes}: always for the top cut by percentile (its
    thresholds), else when the table has ≤ 200 000 rows; None otherwise. Cached by revisions and parameters."""
    from . import jobs_prep as jp

    by_p = spec.op == "topcut" and spec.kernel.mode == "percentile"
    if not by_p and spec.rows > SET_SUMMARY_ROWS:
        return None
    with dialect.read_tx(project.engine) as conn:
        key = (project.uid, spec.op, *_revisions(conn, spec),
               json.dumps(spec.hash_params(), sort_keys=True, ensure_ascii=False))  # fmt: skip
    with _lock:
        hit = _whole_cache.get(key)
        if hit is not None:
            _whole_cache.move_to_end(key)
            return hit
    src, dom = jp.read_prep(project.engine, spec, cache_dir=project.folder / "cache" / "tables")
    if not by_p and src.rows > SET_SUMMARY_ROWS:
        return None
    made = jp.compute_lines(spec, src, dom, None)
    out = {"thresholds": made.report.get("thresholds"), "summary": made.report["summary"], "notes": made.journal}
    with _lock:
        _whole_cache[key] = out
        while len(_whole_cache) > 8:
            _whole_cache.popitem(last=False)
    return out


def _preview(svc: ProjectService, op: str, body: dict[str, Any]) -> dict[str, Any]:
    from . import jobs_prep as jp
    from .routes_run import _hole, _nulls, _samples

    raw = _body(body)
    params = _body(raw.get("params"))
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        spec = jp.parse_prep(conn, params, op)
        hole = _hole(conn, spec.set_id, raw.get("hole_id"))
    src, dom = jp.read_prep(project.engine, spec, holes=[raw["hole_id"]])
    whole = _whole(project, spec)
    kernel = None
    if op == "topcut" and whole is not None:  # the thresholds of the whole table, cut here as in the job
        from .topcut import TopcutParams

        thr = {k: v for k, v in (whole["thresholds"] or {}).items() if v is not None}
        kernel = TopcutParams("thresholds", thresholds=thr)
    made = jp.compute_lines(spec, src, dom, None, kernel=kernel)
    notes = list(whole["notes"]) if whole is not None else ([] if op == "topcut" else made.journal)
    if not src.rows:
        notes.insert(0, f"В скважине {hole} нет проб в таблице «{spec.table_name}».")
    out: dict[str, Any] = {
        "hole": hole,
        "samples": {"from": _nulls(src.from_m), "to": _nulls(src.to_m), "values": _samples(src, spec.read_fields())},
        "result": {
            "from": _nulls(src.from_m),
            "to": _nulls(src.to_m),
            "values": {c.name: _nulls(v) for c, v in made.columns},
        },
        "summary": made.report["summary"],
        "set_summary": None if whole is None else whole["summary"],
        "notes": notes,
    }
    if op == "topcut":
        out["thresholds"] = (whole or made.report)["thresholds"]
    return out


@router.post("/topcut/preview")
def topcut_preview(svc: Svc, body: Payload) -> dict[str, Any]:
    return _preview(svc, "topcut", body)


@router.post("/equivalent/preview")
def equivalent_preview(svc: Svc, body: Payload) -> dict[str, Any]:
    return _preview(svc, "equivalent", body)


@router.post("/declus/preview")
def declus_preview(svc: Svc, body: Payload) -> dict[str, Any]:
    svc.require_current()
    raise ApiError(
        422,
        "no_preview",
        "Декластеризация: предпросмотр по скважине не строится — веса зависят от всех проб.",
        "Нажмите «Применить»: отчёт покажет кривую «размер ячейки → среднее» и лучший размер.",
    )


@router.post("/coding/preview")
def coding_preview(svc: Svc, body: Payload) -> dict[str, Any]:
    svc.require_current()
    raise _coding_not_ready()  # TODO(12.7)


# --- «Перестроить» -----------------------------------------------------------------------------------------


@router.post("/results/{result_id}/rebuild")
def rebuild(
    svc: Svc, s: Sched, result_id: int, body: Annotated[dict[str, Any] | None, Body()] = None
) -> dict[str, Any]:
    """A column operation: its params on the table that holds its columns now (and the domain table found by
    name); any other result — `routes_run.rebuild`."""
    from . import jobs_prep as jp
    from . import routes_run

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        rec = conn.execute(sa.select(dh_results).where(dh_results.c.id == result_id)).mappings().first()
        if rec is None or rec["op"] not in PREP_OPS:
            return routes_run.rebuild(svc, s, result_id, body)
        table = conn.execute(
            sa.select(dh_tables.c.name, dh_tables.c.state, dh_tables.c.row_count).where(
                dh_tables.c.id == rec["table_id"]
            )
        ).first()
        if table is None or table.state != "ready":
            raise ApiError(
                409, "result_busy", "Таблица результата сейчас пересчитывается.", "Дождитесь окончания задания."
            )
        set_id = int(rec["set_id"])
        again = {**(rec["params"] or {}), "set_id": set_id, "table": str(rec["table_id"])}
        dom = again.get("domain")
        tables_in = (rec["inputs"] or {}).get("tables") or []
        if isinstance(dom, dict) and dom.get("field"):
            other = routes_run._current(conn, set_id, tables_in[1]) if len(tables_in) > 1 else None
            again["domain"] = {**dom, "table": str(rec["table_id"] if other is None else other.id)}
    params = {"body": again, "rows": int(table.row_count or 0), "rebuild": result_id}
    key, job_id = s.submit(jp.KINDS[rec["op"]], params, title=f"Перестроить: {rec['name']}")
    return {"source": key, "id": job_id}
