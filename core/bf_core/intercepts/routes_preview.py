"""The previews of «Истинная мощность» and «Рудные пересечения» — one hole, synchronous, nothing written (ADR 0021
§10, решение 18; the forms — docs/progress/e15-api.md). Included into `router.router` (prefix `/intercepts`).

  * `POST /tt/preview` {params, hole_id} → {hole, rows: {from, to, length, TT, TT_REL, TT_FLAG, TT_SRC, ALPHA,
    DIPDIR, HOLE_THETA, HOLE_AZ}, counts: {src, flags}, mesh_skipped, set_summary, notes}: the computation of the
    job (`jobs.read` + `jobs.compute`) on the rows of the hole (the other tables of the orientation — of the hole
    too), in the order of the lines. `params` — the body of `POST /tt` (the parse and its refusals are the job's:
    422 / 404 / 409 `column_exists`); `hole_id` — `dh_holes.id`, 422 / 404 `hole_not_found`. TT_REL = TT / LENGTH
    (empty when LENGTH ≤ 0 or TT is empty); the angles are given whatever `angles` is. `set_summary` — `counts` of
    the whole table when it has ≤ 200 000 rows (cached by the revisions of the tables read and the parameters),
    else null. `mesh_skipped` — the source «каркас» was not computed: its wireframes have more than 2 million
    triangles (`MESH_TRIANGLES`); the chain went on to the next source and a note says so.
    Empty — null, never 0.

  * `POST /run/preview` {params, hole_id} → {hole, unit, samples: {from, to, values, ore}, candidates: [{from, to,
    length, tt, grade, kind, accepted, reason}], result: {from, to, values}, summary, mesh_skipped, set_summary,
    notes}: `jobs_run.compute` on the samples of the hole; `params` — the body of `POST /run` (`name`, `replace`
    are not taken). `ore` — g ≥ c of every sample (`rule.is_ore`); the candidates — every one of the rule, «в том
    числе» after its main one, the reason of a dropped one by `rule.REASON_TITLES`; `result.values` — every column
    of the table after HOLE, FROM, TO; `summary` — the `report` of the job (no journal); `set_summary` — that of
    the whole table at ≤ 200 000 samples, else null.

Plain `def` routes run in the thread pool.
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
MESH_TRIANGLES = 2_000_000  # the most triangles of the wireframes of a source the preview computes (ADR 0021 §5)
MESH_SKIPPED = ("Ориентировка по каркасу в предпросмотре не считается: в каркасах больше 2 млн треугольников. "
                "В расчёте она будет учтена.")  # fmt: skip

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


def _whole(project: Any, spec: Any, st: Any, make: Any) -> dict[str, Any] | None:
    """`make(src, tables)` over the whole source table (≤ 200 000 rows), else None; cached by the revisions of the
    tables read and the parameters (`spec.hash_params()` names the operation)."""
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
    out: dict[str, Any] = make(lines, tables)
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

    from bf_core.composites import source, strips
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
    mesh, skipped = preview_mesh(project, spec)
    t = jobs.compute(spec, lines, tables, st, project, mesh=mesh)
    rep = jobs.report_of(t)

    def whole(s: Any, tb: Any) -> dict[str, Any]:
        made = jobs.compute(spec, s, tb, st, project, mesh=mesh, threads=strips.default_threads())
        return jobs.report_of(made)["counts"]  # type: ignore[no-any-return]

    length = lines.to_m - lines.from_m
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.where(length > 0.0, t.tt / np.where(length > 0.0, length, 1.0), np.nan)
    notes = [*jobs.mesh_notes(spec, spec.rows, strips.default_threads()), *t.journal]
    if skipped:
        notes.append(MESH_SKIPPED)
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
        "mesh_skipped": skipped,
        "set_summary": _whole(project, spec, st, whole),
        "notes": notes,
    }


# --- «Рудные пересечения» ----------------------------------------------------------------------------------------


def preview_mesh(project: Any, spec: Any) -> tuple[Any, bool]:
    """The source «каркас» of the preview of a hole (the wireframes kept in the cache of `wireframes.exact` for the
    next preview) and whether it is skipped: a source with more than `MESH_TRIANGLES` triangles is not computed —
    the source answers None, the chain goes on to the next one."""
    from . import orient_mesh

    skipped = any(s.kind == "mesh" and sum(int(state.triangles) for state, _st in s.meshes) > MESH_TRIANGLES
                  for s in spec.orientation)  # fmt: skip
    return orient_mesh.project_source(project, max_triangles=MESH_TRIANGLES), skipped


def _candidates(spec: Any, t: Any, orienting: Any) -> list[dict[str, Any]]:
    """Every candidate of the rule in the hole, «в том числе» after its main one: {from, to, length, tt, grade,
    kind, accepted, reason}. TT — the one the acceptance was given (`min_by = tt`), else that of the orientation
    on the candidate's own [FROM; TO]; none — null."""
    import numpy as np

    from .rule import KIND_INCL, KIND_MAIN, KIND_TITLES, REASON_OK, REASON_TITLES

    out = t.outcome

    def thick(c: Any, given: np.ndarray) -> np.ndarray:
        if spec.min_by == "tt" or orienting is None or c.n == 0:
            return given
        return np.asarray(orienting.thickness(c.lines).tt, dtype=np.float64)

    def rows(c: Any, tt: np.ndarray, reason: np.ndarray, kind: int) -> list[dict[str, Any]]:
        return [{"from": f, "to": to, "length": ln, "tt": m, "grade": g, "kind": KIND_TITLES[kind],
                 "accepted": int(r) == REASON_OK, "reason": None if int(r) == REASON_OK else REASON_TITLES[int(r)]}
                for f, to, ln, m, g, r in zip(_nulls(c.lines.frm), _nulls(c.lines.to), _nulls(c.length),
                                              _nulls(tt), _nulls(c.grade), reason.tolist(), strict=True)]  # fmt: skip

    main = rows(out.main, thick(out.main, out.main_tt), out.main_reason, KIND_MAIN)
    subs: dict[int, list[dict[str, Any]]] = {}
    if out.sub is not None:
        sub = rows(out.sub, thick(out.sub, out.sub_tt), out.sub_reason, KIND_INCL)
        for p, row in zip(out.sub.parent.tolist(), sub, strict=True):
            subs.setdefault(int(p), []).append(row)
    return [r for i, m in enumerate(main) for r in (m, *subs.get(i, []))]


@router.post("/run/preview")
def run_preview(svc: Svc, body: Payload) -> dict[str, Any]:
    """`POST /run/preview` {params, hole_id} → {hole, unit, samples: {from, to, values, ore}, candidates, result:
    {from, to, values: every column of the table after HOLE, FROM, TO}, summary (= `report` of the job without
    the journal), mesh_skipped, set_summary, notes} — the job on the samples of one hole; nothing written."""
    from bf_core.composites import jobs_coding, source, strips
    from bf_core.composites.routes_run import _hole, _samples

    from . import jobs, jobs_run
    from .rule import is_ore

    raw = _body(body)
    params = {k: v for k, v in _body(raw.get("params")).items() if k not in ("name", "replace")}
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        spec = jobs_run.parse(conn, params)
        hole = _hole(conn, spec.set_id, raw.get("hole_id"))
    src, tables = jobs.read(project.engine, spec, holes=[raw["hole_id"]])
    st = source.trajectories(project, spec.set_id)
    mesh, skipped = preview_mesh(project, spec)
    _lines, t, orienting = jobs_run.compute(spec, src, tables, st, project, mesh=mesh)
    with jobs_coding.reading(project):
        cands = _candidates(spec, t, orienting)
    notes = [*jobs.mesh_notes(spec, spec.rows, strips.default_threads()), *t.journal]
    if skipped:
        notes.append(MESH_SKIPPED)
    if not src.rows:
        notes.insert(0, f"В скважине {hole} нет проб в таблице «{spec.table_name}».")
    shown = [*spec.means, *([spec.density] if spec.density else [])]

    def whole(s: Any, tb: Any) -> dict[str, Any]:
        made = jobs_run.compute(spec, s, tb, st, project, mesh=mesh, threads=strips.default_threads())[1]
        return jobs_run.report_of(made, s)

    return {
        "hole": hole,
        "unit": spec.unit,
        "samples": {
            "from": _nulls(src.from_m),
            "to": _nulls(src.to_m),
            "values": _samples(src, shown),
            "ore": is_ore(src.numbers[spec.field], spec.cutoff).tolist(),
        },  # fmt: skip
        "candidates": cands,
        "result": {"from": _nulls(t.frm), "to": _nulls(t.to), "values": {c.name: _nulls(c.values) for c in t.columns}},
        "summary": jobs_run.report_of(t, src),
        "mesh_skipped": skipped,
        "set_summary": _whole(project, spec, st, whole),
        "notes": notes,
    }
