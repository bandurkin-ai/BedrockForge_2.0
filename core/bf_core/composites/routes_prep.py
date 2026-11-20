"""/api/v1/composites — the jobs of sample preparation, the analysis of the top cut, their previews and «Перестроить»
(ADR 0018 §7, §11, решения 8, 12, 26).

  * `POST /topcut` {set_id, table, field, domain?, percentile? | value? | thresholds?}, `POST /equivalent` {set_id,
    table, base, base_price, base_recovery?, others, nan_zero?, name?, units…}, `POST /declus` {set_id, table, field,
    method, cmin?, cmax?, ncell?, noff?, minmax?, anisy?, anisz?} → {source, id} of `composite.topcut`,
    `composite.equivalent`, `composite.declus` (heavy: fields written in place into the table). 423
    `project_read_only`; 422 {code, message, hint, param} — out of the ranges of 8.2.2 (`bad_param`, `unit_unknown`,
    StatsError of `stats/*` the same way); 404 `table_not_found`, 422 `field_not_found`; 409 `column_exists` — a
    field of that name the operation did not write.
  * `POST /coding` {set_id, table, wireframe_ids, split?, name?, replace?} → {source, id} of `composite.code`
    (heavy, `jobs_coding`): a new table «‹Таблица› — коды» with CODE (and INSIDE_FRAC without the division). 422
    `wireframe_open` {param: wireframe_ids} — an open wireframe, before the job; `wireframe_not_found`; 409
    `table_exists` {name, suggested, lost} as `/run`.
  * `POST /topcut/analysis` {set_id, table, field, domain?, percentiles?, decile_limit?, percentile_limit?} → the
    answer of `topcut.topcut_analysis` as it is (field, limits, domains [{domain, n, …, curves, deciles, top,
    quantiles}]): synchronous, over the whole table; nothing is written (решение 8).
  * `POST /topcut/preview`, `POST /equivalent/preview` {params, hole_id} → {hole, samples, result, summary,
    set_summary, notes}: one hole, the same computation as the job; the thresholds of the top cut by percentile
    come from the whole table, sorted once per revisions, field and domain (any P — a binary search per domain;
    one sort at a time per key, the requests meanwhile wait for it) — the preview of a hole is the job's rows;
    `set_summary` — the summary of the whole table (top cut: when computed for the thresholds or ≤ 200 000 rows;
    equivalent: ≤ 200 000 rows), else null. The top cut adds `thresholds` {domain: T | null}. Nothing is written.
    `POST /declus/preview` — 422 `no_preview` (the weights depend on all the samples; the window has none);
    `POST /coding/preview` {params, hole_id} → the same answer: `result.values` — the fields, CODE, INSIDE_FRAC
    (without the division), the parts of a sample cut at the boundaries (with it); `set_summary` null (the job's
    report has it). `POST /domain/preview` — `routes_run.preview` with the project for domains by wireframes.
  * `POST /results/{id}/rebuild` {drop_lost?} — of a top cut, an equivalent, a DECL_W: the same job with the params
    of the result on the current table (the same hash — no computation); of coding by wireframes — `composite.code`
    on the current source with the same bodies (checked again: 422 `wireframe_open`, `wireframe_not_found`; 409
    `dependents_lost` as `routes_run.rebuild`); other results — `routes_run.rebuild`.

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

from .results import with_meshes

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


def _submit_coding(s: Scheduler, spec: Any, body: dict[str, Any], title: str, **more: Any) -> dict[str, Any]:
    from . import jobs_coding as jc

    params = {"body": body, "rows": spec.rows, "triangles": spec.wf.triangles(), **more}
    key, job_id = s.submit(jc.KIND, params, title=title)
    return {"source": key, "id": job_id}


@router.post("/coding")
def coding(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from . import jobs_coding as jc
    from .routes_run import _taken

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        spec = jc.parse_coding(conn, _body(body))
        _taken(conn, spec.set_id, spec.name, spec.replace)
    return _submit_coding(s, spec, {**spec.body, "replace": spec.replace}, f"{spec.name} ({spec.table_name})")


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


_sorted_cache: OrderedDict[tuple[Any, ...], tuple[Any, int, int]] = OrderedDict()
_gates: dict[tuple[Any, ...], threading.Lock] = {}
SORTED_MAX = 1  # 5 million rows sorted with their running sums — some 240 MB


def _sorted(project: Any, spec: Any) -> tuple[Any, int, int]:
    """(`topcut.Prepared`, rows without FROM or TO, rows) of the field of the top cut over the whole table: kept by
    the revisions, the field and the domain — not by P, the value or the thresholds: a new P is a binary search per
    domain (решение 27). One computation per key at a time: requests meanwhile wait for it (single-flight)."""
    from . import jobs_prep as jp
    from .topcut import prepare

    with dialect.read_tx(project.engine) as conn:
        key = (project.uid, "topcut", *_revisions(conn, spec),
               json.dumps({**spec.hash_params(), "kernel": None}, sort_keys=True, ensure_ascii=False))  # fmt: skip
    with _lock:
        hit = _sorted_cache.get(key)
        if hit is not None:
            _sorted_cache.move_to_end(key)
            return hit
        gate = _gates.setdefault(key, threading.Lock())
    with gate:
        with _lock:
            hit = _sorted_cache.get(key)
        if hit is not None:
            return hit
        try:
            src, dom = jp.read_prep(project.engine, spec, cache_dir=project.folder / "cache" / "tables")
            codes, names = jp.src_codes(spec, src, dom)
            f = spec.fields[0]
            out = (prepare(src.numbers[f], src.to_m - src.from_m, codes=codes, names=names), src.skipped, src.rows)
            del src, dom
            with _lock:
                _sorted_cache[key] = out
                while len(_sorted_cache) > SORTED_MAX:
                    _sorted_cache.popitem(last=False)
        finally:
            with _lock:
                _gates.pop(key, None)
    return out


def _whole_topcut(project: Any, spec: Any) -> dict[str, Any] | None:
    """{thresholds, summary, notes} of the job of the top cut over the whole table, from `_sorted`."""
    from . import jobs as cj
    from . import jobs_prep as jp
    from .topcut import settle

    prep, skipped, rows = _sorted(project, spec)
    if spec.kernel.mode != "percentile" and rows > SET_SUMMARY_ROWS:
        return None
    f = spec.fields[0]
    try:
        thr, rep = settle(prep, spec.kernel, f)
    except ValueError as exc:  # CompositeError → 422
        raise cj.as_api(exc) from None
    rep = jp.clean({**rep, "thresholds": dict(zip(prep.labels, thr, strict=True))})
    notes = [*([jp.skipped_note(spec.table_name, skipped)] if skipped else []), *rep["notes"]]
    return {"thresholds": rep["thresholds"], "summary": rep["summary"], "notes": notes}


def _whole(project: Any, spec: Any) -> dict[str, Any] | None:
    """The job over the whole table — {thresholds, summary, notes}: always for the top cut by percentile (its
    thresholds), else when the table has ≤ 200 000 rows; None otherwise. The top cut — from the table sorted once
    per field and domain (`_sorted`); the equivalent — cached by revisions and parameters."""
    from . import jobs_prep as jp

    by_p = spec.op == "topcut" and spec.kernel.mode == "percentile"
    if not by_p and spec.rows > SET_SUMMARY_ROWS:
        return None
    if spec.op == "topcut":
        return _whole_topcut(project, spec)
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
    from . import jobs_coding as jc
    from . import source
    from .routes_run import _hole, _nulls, _samples

    raw = _body(body)
    params = {k: v for k, v in _body(raw.get("params")).items() if k != "name"}
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        spec = jc.parse_coding(conn, params)
        hole = _hole(conn, spec.set_id, raw.get("hole_id"))
    src = source.read(project.engine, spec.set_id, spec.table_id, spec.fields, holes=[raw["hole_id"]])
    st = source.trajectories(project, spec.set_id)
    made = jc.compute_coding(spec, src, st, jc.bodies(project, spec.wf))
    out = made.out
    notes = list(made.journal)
    if not src.rows:
        notes.insert(0, f"В скважине {hole} нет проб в таблице «{spec.table_name}».")
    return {
        "hole": hole,
        "samples": {"from": _nulls(src.from_m), "to": _nulls(src.to_m), "values": _samples(src, spec.fields)},
        "result": {
            "from": _nulls(out.from_m),
            "to": _nulls(out.to_m),
            "values": {c.name: _nulls(v) for c, v in zip(out.columns, out.values, strict=True)},
        },
        "summary": made.report["summary"],
        "set_summary": None,
        "notes": notes,
    }


@router.post("/domain/preview")
def domain_preview(svc: Svc, body: Payload) -> dict[str, Any]:
    """`routes_run.preview` of composites by domains, with the project the bodies of a domain by wireframes are
    read from (registered before `routes_run`: this path is taken here first)."""
    from . import jobs_coding as jc
    from .routes_run import preview

    with jc.reading(svc.require_current()):
        return preview(svc, "domain", body)


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
        coded = _rebuild_coding(conn, dict(rec), body) if rec is not None and rec["op"] == "coding" else None
    if coded is not None:
        spec, again, title = coded
        return _submit_coding(s, spec, again, title, rebuild=result_id)
    if rec is None or rec["op"] not in PREP_OPS:
        return routes_run.rebuild(svc, s, result_id, body)
    with dialect.read_tx(project.engine) as conn:
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
        again = {**with_meshes(conn, rec["params"] or {}, rec["inputs"] or {}), "set_id": set_id,
                 "table": str(rec["table_id"])}  # fmt: skip
        dom = again.get("domain")
        tables_in = (rec["inputs"] or {}).get("tables") or []
        if isinstance(dom, dict) and dom.get("field"):
            other = routes_run._current(conn, set_id, tables_in[1]) if len(tables_in) > 1 else None
            again["domain"] = {**dom, "table": str(rec["table_id"] if other is None else other.id)}
    params = {"body": again, "rows": int(table.row_count or 0), "rebuild": result_id}
    key, job_id = s.submit(jp.KINDS[rec["op"]], params, title=f"Перестроить: {rec['name']}")
    return {"source": key, "id": job_id}


def _rebuild_coding(conn: Any, rec: dict[str, Any], body: dict[str, Any] | None) -> tuple[Any, dict[str, Any], str]:
    """Coding by wireframes again: its params on the current source table (found by name) and the same bodies."""
    from . import jobs as cj
    from . import jobs_coding as jc
    from . import routes_run

    q = sa.select(dh_tables.c.name, dh_tables.c.state).where(dh_tables.c.id == rec["table_id"])
    table = conn.execute(q).first()
    if table is None or table.state != "ready":
        raise ApiError(409, "result_busy", "Результат сейчас пересчитывается.", "Дождитесь окончания задания.")
    set_id = int(rec["set_id"])
    now = [routes_run._current(conn, set_id, t) for t in (rec["inputs"] or {}).get("tables") or []]
    if not now:
        raise ApiError(409, "source_gone", "Источник результата не найден.", "Удалите результат.")
    cj.refuse_lost(conn, int(rec["table_id"]), body)
    params = with_meshes(conn, rec["params"] or {}, rec["inputs"] or {})  # the bodies by name and geometry now
    again = {**params, "set_id": set_id, "name": table.name, "table": str(now[0].id), "replace": True}
    spec = jc.parse_coding(conn, again)  # the bodies are still there and closed
    return spec, again, f"Перестроить: {table.name}"
