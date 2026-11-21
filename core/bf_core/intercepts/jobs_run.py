"""The job «Рудные пересечения» `intercept.run` (ТЗ 8.5.3–8.5.4; ADR 0021 §7–8, §10, решения 10–19, 21): the rule
8.5.3 on an interval table of the set with the true thickness of the intercepts, a new table of the kind `interval`
«Рудные пересечения» (`table.ALGO` `icpt-1`), its display by the hole (`display.put_display`) and a `dh_results`
record `intercepts` — in one final transaction (`sink.commit` with `table` and `after`), or nothing (a cancel, a
failure: the table built aside under the number of the job is removed by `sink.guarded`).

    spec = parse(conn, body)                              # `spec.parse_run`
    src, tables = jobs.read(engine, spec, …)              # the samples with the grades, the density, the orientation
    lines, t = compute(spec, src, tables, st, project, mesh=…)   # `table.run` inside `jobs_coding.reading(project)`
    report_of(t, src)                                     # the summary as `dh_results.report` keeps it

The params of the job (`router._run_job`): {"body": the canonical body with `replace`, "rows": rows read,
"triangles": of the wireframes of the orientation, "rebuild": result id | absent}. «Заменить» (`replace`) puts the
new table in place of the one of the same name, its registered dependents repeated (`composites.jobs._again`);
without it a name taken meanwhile gets « (2)». «Перестроить» with the same hash and `algo` does not compute: the
record follows the inputs, «устарел» goes. The hash covers the rows of the source and of the tables of the
orientation (the fields read), the stations of the holes, the parameters with the names and geometry keys of the
wireframes (`RunSpec.hash_params`), `table.ALGO`.

`report` (the panel «Результат», `ui/src/modules/intercepts/results.ts`): `counts` {src: {TT_SRC: rows}, flags:
{"1", "2", "4", "8": rows}} of all the rows; `rows`, `intercepts`, `included`, `length` (Σ LENGTH of the main rows,
m), `tt` (Σ TT of the main rows; null — none has one), `len_g`, `mean` (= len_g / length), `waste_len`,
`miss_len`, `excluded` (samples out of the rule: no depth, TO ≤ FROM, overlaps), `skipped` (samples without FROM or
TO: not read), `unit` (of the grade), `field`; `journal`, `versions`, `seconds`, `edits` ([]), `written` and
`table_columns` (by `sink.commit`). NaN is null.

numba is imported with `table`: import it lazily, never before BF-READY.
"""

from __future__ import annotations

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
    from bf_core.composites import intervals, sink, source
    from bf_core.jobs.context import JobContext

    from . import orient
    from .spec import RunSpec
    from .table import InterceptTable, Orienting

KIND = "intercept.run"
OP = "intercepts"
Tick = Callable[[float | None, str | None], None]


def parse(conn: sa.Connection, body: dict[str, Any]) -> RunSpec:
    from . import spec as sp

    return sp.parse_run(conn, body)


def memory_mb(params: dict[str, Any]) -> int:
    """Samples × (the fields + the lines, the candidates, the rows ≈ 40) × 8 B × 3; a wireframe of the orientation
    in memory (one at a time — the largest is not known here: all of them, ≈ 100 B a triangle with its tree)."""
    rows = int(params.get("rows") or 0)
    fields = len((params.get("body") or {}).get("fields") or [])
    triangles = int(params.get("triangles") or 0)
    return max(256, int((rows * (40 + fields) * 8 * 3 + triangles * 100) / 2**20) + 128)


def lines_of(spec: RunSpec, src: source.Lines) -> tuple[intervals.Lines, dict[str, Any]]:
    """The lines of the rule over the samples and the columns it takes in line order: `grade`, `density`
    (None — none), `fields` {name: column} of the other means."""
    import numpy as np

    from bf_core.composites import intervals as iv

    lines = iv.build_lines(src.hole_of().astype(np.int64), src.from_m, src.to_m)
    cols = {
        "grade": lines.take(src.numbers[spec.field]),
        "density": None if spec.density is None else lines.take(src.numbers[spec.density]),
        "fields": {n: lines.take(src.numbers[n]) for n in spec.means[1:]},
    }
    return lines, cols


def compute(
    spec: RunSpec, src: source.Lines, tables: Mapping[int, source.Lines], st: Any, project: Any, *,
    mesh: orient.MeshSource | None = None, keep: bool = True, threads: int = 1,
    tick: Callable[[float], None] | None = None,
) -> tuple[intervals.Lines, InterceptTable, Orienting | None]:  # fmt: skip
    """`table.run` on the samples `src` with the orientation of `spec` (none — TT empty, TT_SRC «нет»; the third
    value is then None); the bodies of domains by wireframes read from `project` (`keep=False` — a job). A refusal
    of the kernels (a parameter of the rule, an open body…) — ApiError 422."""
    from bf_core.composites import jobs_coding

    from . import table
    from .rule import RuleParams

    lines, cols = lines_of(spec, src)
    params = RuleParams(spec.cutoff, l_min=spec.l_min, waste_max=spec.waste_max, min_by=spec.min_by,
                        include_cutoff=spec.include_cutoff, missing=spec.missing)  # fmt: skip
    roles = {n: f.role for n, f in src.fields.items()}
    try:
        with jobs_coding.reading(project, keep=keep):
            orienting = None
            if spec.orientation:
                orienting = table.Orienting.of(spec, src, st, tables=dict(tables), mesh=mesh)
            t = table.run(lines, cols["grade"], params, name=spec.field, unit=spec.unit, fields=cols["fields"],
                          units=spec.units, roles=roles, density=cols["density"], orienting=orienting,
                          threads=threads, tick=tick)  # fmt: skip
    except ValueError as exc:  # CompositeError of the rule or of the bodies → 422; any other — a failure
        raise cj.as_api(exc) from None
    return lines, t, orienting


def report_of(t: InterceptTable, src: source.Lines | None = None) -> dict[str, Any]:
    """The summary of `intercepts` as JSON: `counts` {src, flags} first, then the numbers (module docstring)."""
    from bf_core.composites.jobs_prep import clean

    s = clean(t.summary)
    skipped = int(getattr(src, "skipped", 0) or 0) if src is not None else 0
    return {"counts": {"src": s.pop("src"), "flags": s.pop("flags")}, **s, "skipped": skipped}


def out_table(spec: RunSpec, src: source.Lines, t: InterceptTable) -> sink.OutTable:
    import numpy as np

    from bf_core.composites import sink
    from bf_core.drillholes.store import ColumnSpec

    at = np.asarray(t.hole, dtype=np.int64)
    holes: list[str] = np.array(src.holes, dtype=object)[at].tolist() if t.n else []
    cols = [ColumnSpec(c.name, c.kind, c.unit, c.role) for c in t.columns]
    vals = [c.values for c in t.columns]
    return sink.OutTable(spec.name, holes, np.ascontiguousarray(t.frm), np.ascontiguousarray(t.to), cols, vals,
                         hole_index=(src.holes, at))  # fmt: skip


def column_names(spec: RunSpec) -> list[str]:
    from . import table

    return table.column_names(spec.field, spec.means[1:])


def run_intercepts(
    engine: sa.Engine, folder: Any, params: dict[str, Any], *, job_id: int, tick: Tick,
    mesh: Callable[[Any, Tick], orient.MeshSource | None] | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The body of `intercept.run` (the handler, the tests and the bench in the process). The result: {set_id,
    result_id, table_id, name, chart_id, rows, columns, replaced, fresh, summary (= report), journal, stages
    {read, trajectories, hash, kernel, write: s}, events}. `mesh(project, tick)` — the source «каркас» of the
    job (`jobs.job_mesh`); None — that one."""
    from bf_core.composites import results, sink, source, strips

    from . import display, jobs, table

    t0 = time.perf_counter()
    body, rebuild = cj._body_of(params)
    with dialect.read_tx(engine) as conn:
        spec = parse(conn, body)
        inputs = results.make_inputs(conn, spec.set_id, spec.tables(), trajectories=True, meshes=spec.mesh_ids())
    stages: dict[str, float] = {}
    tick(0.02, "Чтение таблицы")
    cache = folder / "cache" / "tables"
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    with source.building(project, spec.set_id) as started:  # the trajectories — while the samples are read
        src, tables = jobs.read(engine, spec, cache_dir=cache, tick=tick)
        t1 = time.perf_counter()
        stages["read"] = t1 - t0
        tick(0.06, "Траектории")
        st = source.trajectories(project, spec.set_id, tick=tick, started=started)
    t2 = time.perf_counter()
    stages["trajectories"] = t2 - t1
    stations = source.stations_bytes(st, src.hole_db_ids.tolist(), tick=tick)
    sha = source.input_hash([src, *tables.values()], spec.hash_params(), stations=stations, algo=table.ALGO,
                            tick=tick)  # fmt: skip
    t3 = time.perf_counter()
    stages["hash"] = t3 - t2
    fresh = cj._fresh(engine, rebuild, spec.set_id, sha, table.ALGO, inputs)
    if fresh is not None:
        with dialect.read_tx(engine) as conn:
            chart = display.chart_of(conn, spec.set_id, spec.name)
        return {**fresh, "name": spec.name, "columns": column_names(spec), "stages": _rounded(stages),
                **({"chart_id": chart} if chart is not None else {})}  # fmt: skip
    tick(0.1, "Рудные пересечения")
    source_of = jobs.job_mesh if mesh is None else mesh
    _lines, t, _o = compute(spec, src, tables, st, project, mesh=source_of(project, tick), keep=False,
                        threads=strips.default_threads(), tick=lambda f: tick(0.1 + 0.5 * f, None))  # fmt: skip
    del tables, _lines, _o
    t4 = time.perf_counter()
    stages["kernel"] = t4 - t3
    out = out_table(spec, src, t)
    report = {**report_of(t, src), "unit": spec.unit, "field": spec.field, "journal": t.journal,
              "versions": cj._versions(), "edits": [], "seconds": 0.0}  # fmt: skip
    rows, journal = t.n, list(t.journal)
    del t, src
    shown: dict[str, int] = {}

    def after(conn: sa.Connection, res: dict[str, Any]) -> None:
        shown["chart"] = display.put_display(conn, spec.set_id, str(res["name"]))

    with dialect.fold_later(engine) as fold:  # SQLite: the WAL of the final transaction is folded after it
        with sink.guarded(engine, job_id):
            tid = sink.build_table(engine, spec.set_id, out, job_id=job_id,
                                   tick=lambda f, m: tick(0.6 + 0.3 * (f or 0.0), m or "Запись"))  # fmt: skip
            again = cj._again(engine, spec, out, tid)
            report["seconds"] = round(time.perf_counter() - t0, 3)
            res = sink.commit(engine, sink.Commit(set_id=spec.set_id, op=OP, name=spec.name, params=spec.body,
                                                  inputs=inputs, sha256=sha, algo=table.ALGO, report=report,
                                                  job_id=job_id, table=tid,
                                                  mode="replace" if spec.replace else "rename", dependents=again,
                                                  after=after), fold=fold)  # fmt: skip
        stages["write"] = time.perf_counter() - t4
        # no tick from here on: a cancel after the final transaction would report a committed result as cancelled
        try:
            sink.write_snapshot(engine, cache, tid, sink.with_dependents(out, again))
        except Exception:  # noqa: BLE001 - the snapshot only speeds up reading; the table is committed
            pass
    set_id = spec.set_id
    events: list[list[Any]] = [["drillholes.changed", {"set_id": set_id, "table": str(tid), "rev": res["rev"]}]]
    events += [["drillholes.changed", {"set_id": set_id, "table": str(x), "rev": 0}] for x in res["replaced"]]
    events.append(["objects.changed", {"kind": "chart", "set_id": set_id, "id": shown["chart"]}])
    events.append(["composites.changed", {"set_id": set_id}])
    summary = {**report, "written": [], "table_columns": [c.name for c in out.columns]}
    return {"set_id": set_id, "result_id": res["result_id"], "table_id": tid, "name": res["name"],
            "chart_id": shown["chart"], "rows": rows, "columns": ["HOLE", "FROM", "TO", *summary["table_columns"]],
            "replaced": res["replaced"], "fresh": False, "summary": summary, "journal": journal,
            "stages": _rounded(stages), "events": events}  # fmt: skip


def _rounded(stages: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 3) for k, v in stages.items()}


@handler(KIND, "Рудные пересечения", queue="heavy", estimate_memory=memory_mb)
def run_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        out = run_intercepts(ctx.engine(), ctx.project_folder, ctx.params, job_id=ctx.id, tick=ctx.tick)
    except ApiError as exc:
        raise cj._user(exc) from None
    cj._log_journal(ctx, out.get("journal") or [])
    return out
