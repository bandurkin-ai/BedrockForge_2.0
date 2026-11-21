"""The previews of «Истинная мощность» and «Рудные пересечения» — one hole, synchronous, nothing written (ADR 0021
§10, решение 18; the forms — docs/progress/e15-api.md). Included into `router.router` (prefix `/intercepts`).

  * `POST /tt/preview` {params, hole_id} → {hole, rows: {from, to, length, TT, TT_REL, TT_FLAG, TT_SRC, ALPHA,
    DIPDIR, HOLE_THETA, HOLE_AZ}, counts: {src, flags}, mesh_skipped, set_summary, notes}: the computation of the
    job (`jobs.read` + `jobs.compute`) on the rows of the hole (the other tables of the orientation — of the hole
    too), in the order of the lines. `params` — the body of `POST /tt` (the parse and its refusals are the job's:
    422 / 404 / 409 `column_exists`); `hole_id` — `dh_holes.id`, 422 / 404 `hole_not_found`. TT_REL = TT / LENGTH
    (empty when LENGTH ≤ 0 or TT is empty); the angles are given whatever `angles` is. `set_summary` — `counts` of
    the whole table when it has ≤ 200 000 rows (cached by the revisions of the tables read and the parameters),
    else null. `mesh_skipped` — the source «каркас» was not computed (until 15.13 — always, when listed).
    Empty — null, never 0.

The preview of `/run` comes with 15.12 (`jobs_run`). Plain `def` routes run in the thread pool.
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
from bf_core.db.schema import dh_tables
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Payload = Annotated[dict[str, Any], Body()]
SET_SUMMARY_ROWS = 200_000
WHOLE_MAX = 8  # cached counts of whole tables

_lock = threading.Lock()
_whole_cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна инструмента.")
    return raw


def _revisions(conn: sa.Connection, spec: Any) -> tuple[Any, ...]:
    out: list[Any] = []
    for tid, _f in spec.tables():
        t = conn.execute(sa.select(dh_tables.c.rev, dh_tables.c.created_at).where(dh_tables.c.id == tid)).one()
        out += [tid, str(t.created_at), int(t.rev)]
    return tuple(out)


def _whole(project: Any, spec: Any, st: Any) -> dict[str, Any] | None:
    """`counts` of the whole source table (≤ 200 000 rows), else None; cached by revisions and parameters."""
    from . import jobs

    if spec.rows > SET_SUMMARY_ROWS:
        return None
    with dialect.read_tx(project.engine) as conn:
        key = (project.uid, *_revisions(conn, spec), repr(getattr(st, "key", "")),
               json.dumps(spec.hash_params(), sort_keys=True, ensure_ascii=False))  # fmt: skip
    with _lock:
        hit = _whole_cache.get(key)
        if hit is not None:
            _whole_cache.move_to_end(key)
            return hit
    lines, tables = jobs.read(project.engine, spec, cache_dir=project.folder / "cache" / "tables")
    if lines.rows > SET_SUMMARY_ROWS:
        return None
    out: dict[str, Any] = jobs.report_of(jobs.compute(spec, lines, tables, st, project))["counts"]
    with _lock:
        _whole_cache[key] = out
        while len(_whole_cache) > WHOLE_MAX:
            _whole_cache.popitem(last=False)
    return out


def _nulls(a: Any) -> list[Any]:
    from bf_core.composites.routes_run import _nulls as nulls

    return nulls(a)


@router.post("/tt/preview")
def tt_preview(svc: Svc, body: Payload) -> dict[str, Any]:
    import numpy as np

    from bf_core.composites import source
    from bf_core.composites.routes_run import _hole

    from . import jobs

    raw = _body(body)
    params = _body(raw.get("params"))
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        spec = jobs.parse(conn, params)
        hole = _hole(conn, spec.set_id, raw.get("hole_id"))
    lines, tables = jobs.read(project.engine, spec, holes=[raw["hole_id"]])
    st = source.trajectories(project, spec.set_id)
    t = jobs.compute(spec, lines, tables, st, project)
    rep = jobs.report_of(t)
    length = lines.to_m - lines.from_m
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.where(length > 0.0, t.tt / np.where(length > 0.0, length, 1.0), np.nan)
    notes = list(t.journal)
    if not lines.rows:
        notes.insert(0, f"В скважине {hole} нет строк в таблице «{spec.table_name}».")
    return {
        "hole": hole,
        "rows": {
            "from": _nulls(lines.from_m),
            "to": _nulls(lines.to_m),
            "length": _nulls(length),
            "TT": _nulls(t.tt),
            "TT_REL": _nulls(rel),
            "TT_FLAG": [int(v) for v in t.flag.tolist()],
            "TT_SRC": _nulls(t.src_text()),
            "ALPHA": _nulls(t.alpha),
            "DIPDIR": _nulls(t.dipdir),
            "HOLE_THETA": _nulls(t.theta),
            "HOLE_AZ": _nulls(t.azimuth),
        },
        "counts": rep["counts"],
        "mesh_skipped": "mesh" in rep["absent"],
        "set_summary": _whole(project, spec, st),
        "notes": notes,
    }
