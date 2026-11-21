"""The job «Истинная мощность» `intercept.tt` (ТЗ 8.5.1–8.5.2; ADR 0021 §8, §10, решения 13, 15–18, 20, 21): TT,
TT_FLAG, TT_SRC and, with `angles`, ALPHA, DIPDIR, HOLE_THETA, HOLE_AZ written in place into the source table in
one final transaction (`sink.commit` with `columns`: rev + 1, one journal group `column`, the earlier record of
«Истинная мощность» of this table superseded), a `dh_results` record `tt` — or nothing (a cancel, a failure).

    spec = parse(conn, body)                         # `spec.parse_tt` + 409 `column_exists`
    lines, tables = read(engine, spec, cache_dir=…, holes=…)   # the job; the preview of a hole (`holes`)
    t = compute(spec, lines, tables, st, …)          # `tt.of_table` inside `jobs_coding.reading(project)`
    report_of(t)                                     # the summary as `dh_results.report` keeps it

The params of the job (`router._tt_job`): {"body": the canonical body (`spec.body`), "rows": rows read,
"rebuild": result id | absent}. «Перестроить» with the same hash and `algo` does not compute (`jobs._fresh`):
the record follows the inputs, «устарел» goes. The hash covers the rows of the source and of the tables of the
orientation (the fields read: a domain, the core measurements — never the fields written), the stations of the
holes, the parameters with the names and geometry keys of the wireframes (`TtSpec.hash_params`), `tt.ALGO`.

`report` (the panel «Результат», `ui/src/modules/intercepts/results.ts`): `counts` {src: {TT_SRC: rows}, flags:
{"1", "2", "4", "8": rows}}; `rows`, `length` (Σ LENGTH of the rows in the calculation, m), `tt` (Σ TT, m),
`with_tt`, `excluded` (out of the calculation: no depth, TO ≤ FROM), `skipped` (no FROM or TO: not read),
`capped`, `absent` (sources that were not there), `sources` (per source of the chain);
`journal`, `versions`, `seconds`, `edits` ([]), `written` (by `sink.commit`). NaN is null.

The fields of a table of results (composites, «Рудные интервалы»…) are not repeated when that table is rebuilt:
«Истинная мощность» is not a registered dependent (ADR 0021 §8) — the rebuild lists it in 409 `dependents_lost`.
The orientation by wireframes (`mesh`, 15.13) reads the wireframes of the list one at a time, each leaving the cache
once read (`orient_mesh.project_source(keep=False)`); the steps «Пересечения стволов: каркас N из M», «Ближайшие
треугольники…» are the stages of the job. Their geometry keys are in `inputs.meshes` and in the hash: a wireframe
replaced makes the record «устарел». A d_max beyond 50 m on large wireframes is slow (the search of the nearest
triangle grows with d_max, `NEAREST_US`): the journal gives the estimate (`mesh_notes`); the range of ТЗ 8.5.4
stays.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.composites import jobs as cj
from bf_core.db import dialect
from bf_core.jobs.registry import handler

if TYPE_CHECKING:
    from bf_core.composites import source
    from bf_core.jobs.context import JobContext

    from . import orient
    from .spec import RunSpec, TtSpec
    from .tt import TtTable

KIND = "intercept.tt"
OP = "tt"
UNITS = {"TT": "м", "ALPHA": "°", "DIPDIR": "°", "HOLE_THETA": "°", "HOLE_AZ": "°"}
Tick = Callable[[float | None, str | None], None]
#: µs a query of the nearest triangle takes per √(million triangles) and per metre of d_max — measured on a dense
#: closed body (points inside and around it; 2 and 10 million triangles, d_max 50–200 m: 14–208 µs), an upper
#: estimate: the search stops at the size of the body
NEAREST_US = 0.35
SLOW_S = 10.0  # the estimate of the search the journal warns from


def parse(conn: sa.Connection, body: dict[str, Any]) -> TtSpec:
    """The body checked against the project now: `spec.parse_tt`, then 409 `column_exists`."""
    from . import spec as sp

    s = sp.parse_tt(conn, body)
    sp.check_columns(conn, s)
    return s


def memory_mb(params: dict[str, Any]) -> int:
    """Rows × (7 fields written + the lines, the trajectory parts, the orientation ≈ 16) × 8 B × 3; the wireframes
    of the orientation — one in memory at a time, the largest not known here: all, ≈ 100 B a triangle with its
    tree (`params["triangles"]`)."""
    rows, triangles = int(params.get("rows") or 0), int(params.get("triangles") or 0)
    return max(256, int((rows * 23 * 8 * 3 + triangles * 100) / 2**20) + 128)


def mesh_triangles(spec: Any) -> int:
    """Triangles of the wireframes of the sources «каркас» of `spec` (`viz.meshes.MeshState.triangles`)."""
    return sum(int(state.triangles) for s in spec.orientation if s.kind == "mesh" for state, _st in s.meshes)


def mesh_notes(spec: Any, rows: int, threads: int = 1) -> list[str]:
    """A line of the journal for a source «каркас» whose search of the nearest triangle is estimated at
    `SLOW_S` seconds or more for `rows` intervals: d_max beyond 50 m on large wireframes."""
    from .orient import _count

    out = []
    for s in spec.orientation:
        if s.kind != "mesh" or s.d_max <= 50.0:
            continue
        tri = sum(int(state.triangles) for state, _st in s.meshes)
        us = NEAREST_US * math.sqrt(tri / 1e6) * s.d_max
        took = rows * us * 1e-6 / max(1, threads)
        if took >= SLOW_S:
            d = f"{s.d_max:g}".replace(".", ",")
            out.append(f"Каркас: d_max = {d} м на каркасах в {_count(tri)} треугольников — поиск ближайшей точки "
                       f"может занять до {_count(round(took))} с (оценка: до {_count(round(us))} мкс на интервал "
                       f"без пересечения). Меньший d_max ускорит расчёт.")  # fmt: skip
    return out


def read(
    engine: sa.Engine, spec: TtSpec | RunSpec, *, cache_dir: Any = None, holes: list[int] | None = None,
    tick: Tick | None = None,
) -> tuple[source.Lines, dict[int, source.Lines]]:  # fmt: skip
    """The source with the fields its own sources read, and the other tables of the orientation by id."""
    from bf_core.composites import source

    (tid, fields), *others = spec.tables()
    lines = source.read(engine, spec.set_id, tid, fields, cache_dir=cache_dir, holes=holes, tick=tick)
    tables = {int(t): source.read(engine, spec.set_id, t, f, cache_dir=cache_dir, holes=holes, tick=tick)
              for t, f in others}  # fmt: skip
    return lines, tables


def compute(
    spec: TtSpec, lines: source.Lines, tables: Mapping[int, source.Lines], st: Any, project: Any, *,
    mesh: orient.MeshSource | None = None, keep: bool = True, threads: int = 1,
    tick: Callable[[float], None] | None = None,
) -> TtTable:  # fmt: skip
    """`tt.of_table` with the bodies of domains by wireframes read from `project` (`keep=False` — a job: each
    wireframe leaves the cache once read) and the source «каркас» `mesh` (None — not there: the chain goes on,
    `absent` names it). A kernel refusal (an open body…) — ApiError 422."""
    from bf_core.composites import jobs_coding

    from . import tt

    try:
        with jobs_coding.reading(project, keep=keep):
            return tt.of_table(spec, lines, st, tables=dict(tables), mesh=mesh, threads=threads, tick=tick)
    except ValueError as exc:  # CompositeError of the bodies → 422; any other — a failure of the job
        raise cj.as_api(exc) from None


def job_mesh(project: Any, tick: Tick) -> orient.MeshSource:
    """The source «каркас» of a job: the wireframes of the project one at a time, each leaving the cache once read;
    the step that starts is the message of the job («Пересечения стволов: каркас 2 из 5»)."""
    from . import orient_mesh

    return orient_mesh.project_source(project, keep=False, stage=lambda text: tick(None, text))


def report_of(t: TtTable) -> dict[str, Any]:
    """The summary of `tt` as JSON: `counts` {src, flags} first, then the numbers (module docstring)."""
    from bf_core.composites.jobs_prep import clean

    s = clean(t.summary)
    return {"counts": {"src": s.pop("src"), "flags": s.pop("flags")}, **s}


def columns(spec: TtSpec, t: TtTable) -> list[tuple[Any, Any]]:
    from bf_core.drillholes.store import ColumnSpec

    return [(ColumnSpec(name, kind, UNITS.get(name)), values) for name, kind, values in t.columns(spec.angles)]


def run_tt(engine: sa.Engine, folder: Any, params: dict[str, Any], *, job_id: int, tick: Tick) -> dict[str, Any]:
    """The body of `intercept.tt` (the handler, the tests and the bench in the process). The result: {set_id,
    result_id, table_id, name, columns, rows, fresh, summary (= report), journal, stages {read, trajectories,
    hash, kernel, write: s}, events}."""
    from bf_core.composites import results, sink, source, strips

    from . import tt

    t0 = time.perf_counter()
    body, rebuild = cj._body_of(params)
    with dialect.read_tx(engine) as conn:
        spec = parse(conn, body)
        inputs = results.make_inputs(conn, spec.set_id, spec.tables(), trajectories=True, meshes=spec.mesh_ids())
    stages: dict[str, float] = {}
    tick(0.02, "Чтение таблицы")
    lines, tables = read(engine, spec, cache_dir=folder / "cache" / "tables", tick=tick)
    t1 = time.perf_counter()
    stages["read"] = t1 - t0
    tick(0.06, "Траектории")
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    st = source.trajectories(project, spec.set_id, tick=tick)
    t2 = time.perf_counter()
    stages["trajectories"] = t2 - t1
    stations = source.stations_bytes(st, lines.hole_db_ids.tolist(), tick=tick)
    sha = source.input_hash([lines, *tables.values()], spec.hash_params(), stations=stations, algo=tt.ALGO,
                            tick=tick)  # fmt: skip
    t3 = time.perf_counter()
    stages["hash"] = t3 - t2
    fresh = cj._fresh(engine, rebuild, spec.set_id, sha, tt.ALGO, inputs)
    if fresh is not None:
        return {**fresh, "columns": spec.columns(), "stages": _rounded(stages)}
    tick(0.1, "Истинная мощность")
    threads = strips.default_threads()
    t = compute(spec, lines, tables, st, project, mesh=job_mesh(project, tick), keep=False, threads=threads,
                tick=lambda f: tick(0.1 + 0.65 * f, None))  # fmt: skip
    del tables
    t4 = time.perf_counter()
    stages["kernel"] = t4 - t3
    tick(0.8, "Запись")
    report = {**report_of(t), "journal": [*mesh_notes(spec, lines.rows, threads), *t.journal],
              "versions": cj._versions(), "edits": [], "seconds": round(time.perf_counter() - t0, 3)}  # fmt: skip
    cols = sink.ColumnsOut(spec.table_id, lines.row_ids, columns(spec, t))
    del t
    # no tick from here on: the final transaction writes the fields and the record, or nothing
    with dialect.fold_later(engine) as fold:  # SQLite: the WAL of millions of rows is folded after the commit
        res = sink.commit(engine, sink.Commit(set_id=spec.set_id, op=OP, name=default_name(spec.table_name),
                                              params=spec.body, inputs=inputs, sha256=sha, algo=tt.ALGO,
                                              report=report, job_id=job_id, columns=[cols]), fold=fold)  # fmt: skip
        stages["write"] = time.perf_counter() - t4
    set_id = spec.set_id
    events: list[list[Any]] = [["drillholes.changed", {"set_id": set_id, "table": c["table"], "rev": c["rev"]}]
                               for c in res["changed"]]  # fmt: skip
    events.append(["composites.changed", {"set_id": set_id}])
    return {"set_id": set_id, "result_id": res["result_id"], "table_id": spec.table_id, "name": res["name"],
            "columns": spec.columns(), "rows": lines.rows, "fresh": False,
            "summary": {**report, "written": [{"table": dialect.name_key(spec.table_name), "columns": spec.columns()}]},
            "journal": report["journal"], "stages": _rounded(stages), "events": events}  # fmt: skip


def _rounded(stages: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 3) for k, v in stages.items()}


def default_name(table: str) -> str:
    return f"Истинная мощность · {table}"


@handler(KIND, "Истинная мощность", queue="heavy", estimate_memory=memory_mb)
def tt_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        out = run_tt(ctx.engine(), ctx.project_folder, ctx.params, job_id=ctx.id, tick=ctx.tick)
    except ApiError as exc:
        raise cj._user(exc) from None
    cj._log_journal(ctx, out.get("journal") or [])
    return out
