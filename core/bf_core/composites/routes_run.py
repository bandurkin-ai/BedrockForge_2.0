"""/api/v1/composites — the jobs of composites, «Перестроить», the preview of one hole (ADR 0018 §11, решения 12, 26).

  * `POST /run` {set_id, table, fields?, method, length?, remainder?, f_min?, p_min?, origin?, density?,
    unsampled?, domain?, bench?, bench_top?, name?, replace?} → {source, id} of the job `composite.run` (heavy).
    423 `project_read_only`; 422 {code, message, hint, param} — a value out of the ranges of 8.2.2 (`bad_param`),
    no numeric field, a method whose kernel is not in this build (`not_ready`); 404 `table_not_found`,
    `field_not_found`; 409 `table_exists` {name, suggested, lost} — the window asks «Заменить?» (listing `lost`:
    the column operations on that table that cannot be repeated) and repeats with `replace: true`.
  * `POST /merge` {set_id, tables, name?, replace?}, `POST /coords` {set_id, table, name?, replace?} → {source,
    id} of `composite.merge`, `composite.coords` (heavy); the same refusals, 409 `table_exists` as `/run`.
  * `POST /results/{id}/rebuild` {drop_lost?} → {source, id}: the same job with the parameters of the result on
    the current data; the same hash and algorithm — no computation, «устарел» goes. 409 `dependents_lost` {lost}
    — the operations on the table that cannot be repeated; again with `drop_lost: true`.
  * `POST /{op}/preview` {params, hole_id} → {hole, samples {from, to, values}, result {from, to, values},
    summary {before, after}, set_summary | null, notes}: synchronous, one hole, the same computation as the job;
    `set_summary` — over the whole table when it has ≤ 200 000 rows (cached by revision and parameters), else
    null (the job's report has it); `op` — length, domain, bench, merge, coords (merge and coords: `samples` — the
    first table, `set_summary` null). Missing values are null, never 0. The previews of the other ops have routes of
    their own (`routes_prep`, `routes_ore`, registered first); an op none of them knows — 404 `unknown_operation`.

The kernels are imported lazily (not before BF-READY); a plain `def` route runs in the thread pool of the API.
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
from bf_core.db.schema import dh_holes, dh_results, dh_tables, objects
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Payload = Annotated[dict[str, Any], Body()]
SET_SUMMARY_ROWS = 200_000


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна инструмента.")
    return raw


KINDS = {"length": "composite.run", "domain": "composite.run", "bench": "composite.run",
         "merge": "composite.merge", "coords": "composite.coords"}  # fmt: skip


def _taken(conn: Any, set_id: int, name: str, replace: bool) -> None:
    """409 `table_exists` {name, suggested, lost} when the name is taken and not to be replaced."""
    from . import jobs as cj
    from . import sink

    other = sink._ready(conn, set_id, name)
    if other is not None and (not replace or other.kind != "interval"):
        lost = cj.lost_dependents(conn, int(other.id)) if other.kind == "interval" else []
        raise ApiError(
            409,
            "table_exists",
            f"В наборе уже есть таблица «{name}».",
            "Выберите «Заменить», чтобы заменить её, или задайте другое название.",
            {"name": name, "suggested": sink.free_name(conn, set_id, name), "lost": lost},
        )


@router.post("/run")
def run(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from . import jobs as cj

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        spec = cj.parse_run(conn, _body(body))
        _taken(conn, spec.set_id, spec.name, spec.replace)
    params = {"body": {**spec.body, "replace": spec.replace}, "rows": spec.rows}
    key, job_id = s.submit("composite.run", params, title=f"{spec.name} ({spec.table_name})")
    return {"source": key, "id": job_id}


def _tables_job(svc: ProjectService, s: Scheduler, body: dict[str, Any], op: str) -> dict[str, Any]:
    from . import compose_tables as ct

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        spec = ct.parse_tables(conn, _body(body), op)
        _taken(conn, spec.set_id, spec.name, spec.replace)
    params = {"body": {**spec.body, "replace": spec.replace}, "rows": spec.rows}
    key, job_id = s.submit(KINDS[op], params, title=spec.name)
    return {"source": key, "id": job_id}


@router.post("/merge")
def merge(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    return _tables_job(svc, s, body, "merge")


@router.post("/coords")
def coords(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    return _tables_job(svc, s, body, "coords")


def _current(conn: Any, set_id: int, t: dict[str, Any]) -> Any:
    """The ready table of the set an input names (by its name key); 409 `source_gone` — none."""
    row = conn.execute(
        sa.select(dh_tables.c.id, dh_tables.c.row_count).where(
            dh_tables.c.set_id == set_id, dh_tables.c.name_key == t.get("key"), dh_tables.c.state == "ready"
        )
    ).first()
    if row is None:
        raise ApiError(
            409,
            "source_gone",
            f"Таблица-источник «{t.get('name')}» удалена.",
            "Удалите результат или постройте его заново из другой таблицы.",
        )
    return row


@router.post("/results/{result_id}/rebuild")
def rebuild(
    svc: Svc, s: Sched, result_id: int, body: Annotated[dict[str, Any] | None, Body()] = None
) -> dict[str, Any]:
    """The parameters of the result on the current tables (found by name: a replaced source has another id)."""
    from . import jobs as cj
    from . import results
    from .results import OP_TITLES

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        rec = conn.execute(sa.select(dh_results).where(dh_results.c.id == result_id)).mappings().first()
        if rec is None:
            raise ApiError(404, "result_not_found", "Результат обработки не найден.", "Обновите дерево проекта.")
        if rec["op"] not in KINDS:  # the other ops are taken by `routes_ore` and `routes_prep`: one of a newer build
            raise ApiError(
                422,
                "not_ready",
                f"Перестроение «{OP_TITLES.get(rec['op'], rec['op'])}» в этой версии программы недоступно.",
                "Обновите программу или удалите результат и постройте его заново.",
            )
        table = conn.execute(
            sa.select(dh_tables.c.name, dh_tables.c.state).where(dh_tables.c.id == rec["table_id"])
        ).first()
        if table is None or table.state != "ready":
            raise ApiError(409, "result_busy", "Результат сейчас пересчитывается.", "Дождитесь окончания задания.")
        set_id = int(rec["set_id"])
        now = [_current(conn, set_id, t) for t in (rec["inputs"] or {}).get("tables") or []]
        if not now:
            raise ApiError(409, "source_gone", "Источник результата не найден.", "Удалите результат.")
        cj.refuse_lost(conn, int(rec["table_id"]), body)
        now_params = results.with_meshes(conn, rec["params"] or {}, rec["inputs"] or {})  # ids of now
    again = {**now_params, "set_id": set_id, "name": table.name, "replace": True}
    if rec["op"] == "merge":
        again["tables"] = [str(t.id) for t in now]
    else:
        again["table"] = str(now[0].id)
    if rec["op"] == "domain" and len(now) > 1 and isinstance(again.get("domain"), dict):
        again["domain"] = {**again["domain"], "table": str(now[1].id)}
    params = {"body": again, "rows": sum(int(t.row_count or 0) for t in now), "rebuild": result_id}
    key, job_id = s.submit(KINDS[rec["op"]], params, title=f"Перестроить: {table.name}")
    return {"source": key, "id": job_id}


# --- the preview ---------------------------------------------------------------------------------------------

_lock = threading.Lock()
_set_cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()


def _nulls(a: Any) -> list[Any]:
    """An array as JSON: NaN and None → null (never 0)."""
    import numpy as np

    arr = np.asarray(a)
    if arr.dtype.kind == "f":
        return [None if v != v else float(v) for v in arr.tolist()]
    return [None if v is None or v == "" else v for v in arr.tolist()]


def _set_summary(project: Any, spec: Any, threads: int) -> dict[str, Any] | None:
    """«До/после» over the whole table (≤ 200 000 rows): cached by the revisions and the parameters."""
    from . import compose, source

    if spec.rows > SET_SUMMARY_ROWS:
        return None
    with dialect.read_tx(project.engine) as conn:
        t = conn.execute(
            sa.select(dh_tables.c.rev, dh_tables.c.created_at).where(dh_tables.c.id == spec.table_id)
        ).one()
        version = conn.execute(sa.select(objects.c.version).where(objects.c.id == spec.set_id)).scalar()
    key = (project.uid, spec.table_id, str(t.created_at), int(t.rev), version,
           json.dumps(spec.hash_params(), sort_keys=True, ensure_ascii=False))  # fmt: skip
    with _lock:
        hit = _set_cache.get(key)
        if hit is not None:
            _set_cache.move_to_end(key)
            return hit
    cache = project.folder / "cache" / "tables"
    src = source.read(project.engine, spec.set_id, spec.table_id, spec.read_fields(), cache_dir=cache)
    if src.rows > SET_SUMMARY_ROWS:
        return None
    d = spec.other_domain()
    dom = None if d is None else source.read(project.engine, spec.set_id, d.table_id, [d.field], cache_dir=cache)
    with dialect.read_tx(project.engine) as conn:
        ends = compose.hole_depths(conn, spec.set_id, src.hole_db_ids)
    st = source.trajectories(project, spec.set_id) if spec.method == "bench" else None  # benches need the paths
    made = compose.compute(spec, src, dom, ends, st, threads=threads)  # X, Y, Z do not enter the summary
    out: dict[str, Any] = made.report["summary"]
    with _lock:
        _set_cache[key] = out
        while len(_set_cache) > 8:
            _set_cache.popitem(last=False)
    return out


def _samples(src: Any, names: list[str]) -> dict[str, list[Any]]:
    import numpy as np

    out: dict[str, list[Any]] = {}
    for name in names:
        if name in src.numbers:
            out[name] = _nulls(src.numbers[name])
        elif name in src.codes:
            out[name] = _nulls(np.array([*src.labels[name], None], dtype=object)[src.codes[name]])
    return out


def _hole(conn: Any, set_id: int, hole_id: Any) -> str:
    if not isinstance(hole_id, int) or isinstance(hole_id, bool):
        raise ApiError(422, "hole_not_found", "Не выбрана скважина.", "Выберите скважину для предпросмотра.")
    hole = conn.execute(
        sa.select(dh_holes.c.name).where(dh_holes.c.id == hole_id, dh_holes.c.set_id == set_id)
    ).scalar()
    if hole is None:
        raise ApiError(404, "hole_not_found", "Скважина не найдена в наборе.", "Выберите скважину из списка.")
    return str(hole)


@router.post("/{op}/preview")
def preview(svc: Svc, op: str, body: Payload) -> dict[str, Any]:
    from . import compose, source, strips
    from . import compose_tables as ct
    from . import jobs as cj

    if op not in KINDS:  # coding, topcut, equivalent, declus, ore: their own routes, registered first
        raise ApiError(404, "unknown_operation", f"Нет предпросмотра «{op}».", "Обновите приложение.")
    raw = _body(body)
    params = {k: v for k, v in _body(raw.get("params")).items() if k != "name"}
    project = svc.require_current()
    notes: list[str] = []
    set_summary = None
    if op in ("merge", "coords"):
        with dialect.read_tx(project.engine) as conn:
            tspec = ct.parse_tables(conn, params, op)
            hole = _hole(conn, tspec.set_id, raw.get("hole_id"))
        srcs = [source.read(project.engine, tspec.set_id, i, f, holes=[raw["hole_id"]]) for i, _n, f in tspec.tables]
        if op == "merge":
            made = ct.merge_compute(tspec, srcs)
        else:
            made = ct.coords_compute(tspec, srcs[0], source.trajectories(project, tspec.set_id))
        src, fields, table_name = srcs[0], tspec.tables[0][2], tspec.tables[0][1]
    else:
        with dialect.read_tx(project.engine) as conn:
            spec = cj.parse_run(conn, {**params, "method": op})
            hole = _hole(conn, spec.set_id, raw.get("hole_id"))
        src = source.read(project.engine, spec.set_id, spec.table_id, spec.read_fields(), holes=[raw["hole_id"]])
        d = spec.other_domain()
        dom = None
        if d is not None:
            dom = source.read(project.engine, spec.set_id, d.table_id, [d.field], holes=[raw["hole_id"]])
        with dialect.read_tx(project.engine) as conn:
            ends = compose.hole_depths(conn, spec.set_id, src.hole_db_ids)
        made = compose.compute(spec, src, dom, ends, source.trajectories(project, spec.set_id), threads=1)
        fields = [*spec.fields, *([spec.density] if spec.density else [])]
        table_name = spec.table_name
        set_summary = _set_summary(project, spec, strips.default_threads())
        if params.get("domain") and op != "domain":
            notes.append("Домен учитывается только методом «По доменам».")
    out = made.out
    if not src.rows:
        notes.insert(0, f"В скважине {hole} нет проб в таблице «{table_name}».")
    return {
        "hole": hole,
        "samples": {"from": _nulls(src.from_m), "to": _nulls(src.to_m), "values": _samples(src, fields)},
        "result": {
            "from": _nulls(out.from_m),
            "to": _nulls(out.to_m),
            "values": {c.name: _nulls(v) for c, v in zip(out.columns, out.values, strict=True)},
        },
        "summary": made.report["summary"],
        "set_summary": set_summary,
        "notes": [*made.journal, *notes],
    }
