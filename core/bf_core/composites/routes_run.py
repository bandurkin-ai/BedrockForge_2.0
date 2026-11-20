"""/api/v1/composites — the jobs of composites, «Перестроить», the preview of one hole (ADR 0018 §11, решения 12, 26).

  * `POST /run` {set_id, table, fields?, method, length?, remainder?, f_min?, p_min?, origin?, density?,
    unsampled?, domain?, bench?, bench_top?, name?, replace?} → {source, id} of the job `composite.run` (heavy).
    423 `project_read_only`; 422 {code, message, hint, param} — a value out of the ranges of 8.2.2 (`bad_param`),
    no numeric field, a method whose kernel is not in this build (`not_ready`); 404 `table_not_found`,
    `field_not_found`; 409 `table_exists` {name, suggested, lost} — the window asks «Заменить?» (listing `lost`:
    the column operations on that table that cannot be repeated) and repeats with `replace: true`.
  * `POST /merge`, `POST /coords` — 422 `not_ready` until the kernels of 12.5–12.6.
  * `POST /results/{id}/rebuild` {drop_lost?} → {source, id}: the same job with the parameters of the result on
    the current data; the same hash and algorithm — no computation, «устарел» goes. 409 `dependents_lost` {lost}
    — the operations on the table that cannot be repeated; again with `drop_lost: true`.
  * `POST /{op}/preview` {params, hole_id} → {hole, samples {from, to, values}, result {from, to, values},
    summary {before, after}, set_summary | null, notes}: synchronous, one hole, the same computation as the job;
    `set_summary` — over the whole table when it has ≤ 200 000 rows (cached by revision and parameters), else
    null (the job's report has it). Missing values are null, never 0.

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
PREVIEW_OPS = {"length", "domain", "bench", "merge", "coords", "coding", "topcut", "equivalent", "declus", "ore"}


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна инструмента.")
    return raw


@router.post("/run")
def run(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from . import jobs as cj
    from . import sink

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        spec = cj.parse_run(conn, _body(body))
        other = sink._ready(conn, spec.set_id, spec.name)
        if other is not None and (not spec.replace or other.kind != "interval"):
            lost = cj.lost_dependents(conn, int(other.id)) if other.kind == "interval" else []
            raise ApiError(
                409,
                "table_exists",
                f"В наборе уже есть таблица «{spec.name}».",
                "Выберите «Заменить», чтобы заменить её, или задайте другое название.",
                {"name": spec.name, "suggested": sink.free_name(conn, spec.set_id, spec.name), "lost": lost},
            )
    params = {"body": {**spec.body, "replace": spec.replace}, "rows": spec.rows}
    key, job_id = s.submit("composite.run", params, title=f"{spec.name} ({spec.table_name})")
    return {"source": key, "id": job_id}


@router.post("/merge")
def merge(svc: Svc, body: Payload) -> dict[str, Any]:
    from .jobs import not_ready

    svc.require_current(writable=True)
    raise not_ready("Объединение таблиц")  # TODO(12.5): `composites/merge.py`, job `composite.merge`


@router.post("/coords")
def coords(svc: Svc, body: Payload) -> dict[str, Any]:
    from .jobs import not_ready

    svc.require_current(writable=True)
    raise not_ready("Координаты проб")  # TODO(12.6): `composites/coords.py`, job `composite.coords`


@router.post("/results/{result_id}/rebuild")
def rebuild(
    svc: Svc, s: Sched, result_id: int, body: Annotated[dict[str, Any] | None, Body()] = None
) -> dict[str, Any]:
    from . import jobs as cj
    from .results import OP_TITLES

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        rec = conn.execute(sa.select(dh_results).where(dh_results.c.id == result_id)).mappings().first()
        if rec is None:
            raise ApiError(404, "result_not_found", "Результат обработки не найден.", "Обновите дерево проекта.")
        if rec["op"] not in cj.METHODS or rec["op"] not in cj.READY:
            raise cj.not_ready(f"Перестроение «{OP_TITLES.get(rec['op'], rec['op'])}»")
        table = conn.execute(
            sa.select(dh_tables.c.name, dh_tables.c.state).where(dh_tables.c.id == rec["table_id"])
        ).first()
        if table is None or table.state != "ready":
            raise ApiError(409, "result_busy", "Результат сейчас пересчитывается.", "Дождитесь окончания задания.")
        src = ((rec["inputs"] or {}).get("tables") or [{}])[0]
        src_id = conn.execute(
            sa.select(dh_tables.c.id, dh_tables.c.row_count).where(
                dh_tables.c.set_id == rec["set_id"],
                dh_tables.c.name_key == src.get("key"),
                dh_tables.c.state == "ready",
            )
        ).first()
        if src_id is None:
            raise ApiError(
                409,
                "source_gone",
                f"Таблица-источник «{src.get('name')}» удалена.",
                "Удалите результат или постройте его заново из другой таблицы.",
            )
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
    again = {**(rec["params"] or {}), "set_id": int(rec["set_id"]), "table": str(src_id.id), "name": table.name,
             "replace": True}  # fmt: skip
    params = {"body": again, "rows": int(src_id.row_count or 0), "rebuild": result_id}
    key, job_id = s.submit("composite.run", params, title=f"Перестроить: {table.name}")
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
    from . import jobs as cj
    from . import source

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
    src = source.read(project.engine, spec.set_id, spec.table_id, spec.read_fields(),
                      cache_dir=project.folder / "cache" / "tables")  # fmt: skip
    if src.rows > SET_SUMMARY_ROWS:
        return None
    with dialect.read_tx(project.engine) as conn:
        ends = cj.hole_depths(conn, spec.set_id, src.hole_db_ids)
    made = cj.compute(spec, src, ends, None, threads=threads)  # X, Y, Z do not enter the summary
    out: dict[str, Any] = made.report["summary"]
    with _lock:
        _set_cache[key] = out
        while len(_set_cache) > 8:
            _set_cache.popitem(last=False)
    return out


@router.post("/{op}/preview")
def preview(svc: Svc, op: str, body: Payload) -> dict[str, Any]:
    from . import jobs as cj
    from . import source, strips

    if op not in PREVIEW_OPS:
        raise ApiError(404, "unknown_operation", f"Нет предпросмотра «{op}».", "Обновите приложение.")
    if op in ("merge", "coords", "coding", "topcut", "equivalent", "declus", "ore"):
        raise cj.not_ready("Предпросмотр этой операции")  # TODO(12.5–12.6, 12.21–12.22)
    raw = _body(body)
    params = _body(raw.get("params"))
    hole_id = raw.get("hole_id")
    if not isinstance(hole_id, int) or isinstance(hole_id, bool):
        raise ApiError(422, "hole_not_found", "Не выбрана скважина.", "Выберите скважину для предпросмотра.")
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        spec = cj.parse_run(conn, {**{k: v for k, v in params.items() if k != "name"}, "method": op})
        hole = conn.execute(
            sa.select(dh_holes.c.name).where(dh_holes.c.id == hole_id, dh_holes.c.set_id == spec.set_id)
        ).scalar()
        if hole is None:
            raise ApiError(404, "hole_not_found", "Скважина не найдена в наборе.", "Выберите скважину из списка.")
        ends = None
    src = source.read(project.engine, spec.set_id, spec.table_id, spec.read_fields(), holes=[hole_id])
    with dialect.read_tx(project.engine) as conn:
        ends = cj.hole_depths(conn, spec.set_id, src.hole_db_ids)
    st = source.trajectories(project, spec.set_id)
    made = cj.compute(spec, src, ends, st, threads=1)
    samples: dict[str, list[Any]] = {}
    for name in spec.fields:
        if name in src.numbers:
            samples[name] = _nulls(src.numbers[name])
        else:
            import numpy as np

            labels = np.array([*src.labels[name], None], dtype=object)
            samples[name] = _nulls(labels[src.codes[name]])
    if spec.density and spec.density not in samples:
        samples[spec.density] = _nulls(src.numbers[spec.density])
    out = made.out
    notes = list(made.journal)
    if not src.rows:
        notes.insert(0, f"В скважине {hole} нет проб в таблице «{spec.table_name}».")
    if params.get("domain") and op == "length":
        notes.append("Домен методом «По длине» не учитывается: выберите метод «По доменам».")
    return {
        "hole": hole,
        "samples": {"from": _nulls(src.from_m), "to": _nulls(src.to_m), "values": samples},
        "result": {
            "from": _nulls(out.from_m),
            "to": _nulls(out.to_m),
            "values": {c.name: _nulls(v) for c, v in zip(out.columns, out.values, strict=True)},
        },
        "summary": made.report["summary"],
        "set_summary": _set_summary(project, spec, strips.default_threads()),
        "notes": notes,
    }
