"""Jobs of composites (ТЗ 8.2.2–8.2.3; ADR 0018 §2–3, §6, решения 18, 26): `composite.run` — by length, by domains
(a field of the samples or of another interval table, or the bodies of closed wireframes — `jobs_coding`), by
benches; «Перестроить».

    spec = parse_run(conn, body)            # the body of `POST /composites/run` → RunSpec; ApiError 422 / 404
    made = compose.compute(spec, src, dom, ends, st)   # the rows of the new table, the report (job and preview)

The job (`composite.run`, heavy) reads the chosen columns (`source.read`), the trajectories (X, Y, Z of the
middle of every composite) and the depths of the holes, hashes the inputs (SHA-256, `source.input_hash`),
computes, builds the table aside and commits it in one transaction (`sink`). Params of the job:
{"body": the request as given, "rows": rows of the source (memory estimate), "rebuild": result id | absent}.
«Перестроить» with the same hash and `algo` does not compute: the record follows the current inputs
(`results.mark_fresh`). Replacing a result table (a rebuild or «Заменить») repeats the operations that wrote
columns into it (top cut, equivalent, DECL_W — `DEPENDENTS`, registered by their modules) in the order they
were made, before the final transaction; their records go to the new table (решение 18). The column operations
of 12.21 (`jobs_prep`) are registered at the first look at DEPENDENTS; while they repeat, `AGAIN` holds the
project and the ids of the old and the new table.

The result of the job carries `events` — [[event, data], …]: `drillholes.changed` for the new and the replaced
tables, `composites.changed {set_id}`; the scheduler publishes them (line of the orchestrator).

`dh_results.params` is the body of the request as given (`table`, `fields` as a list, `domain`); the numbers the
kernel ran with are `report.kernel`; `report.summary {before, after}` — the summary of the window over the set,
`report.seconds`, `report.edits` (always empty here: no hand edits of composites).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_results, dh_tables
from bf_core.jobs.context import JobError
from bf_core.jobs.registry import handler

if TYPE_CHECKING:
    from bf_core.jobs.context import JobContext

    from . import sink

ALGO = {"length": "length-1"}
METHODS = ("length", "domain", "bench")
ALGO.update(domain="domain-1", bench="bench-1", merge="merge-1", coords="coords-1")
READY = frozenset(METHODS)
NAME_MAX = 200


def _bad(code: str, message: str, hint: str, param: str | None = None) -> ApiError:
    return ApiError(422, code, message, hint, {"param": param} if param else None)


def not_ready(what: str) -> ApiError:
    return ApiError(
        422, "not_ready", f"{what}: расчёт ещё не подключён в этой сборке.", "Используйте композиты по длине."
    )


def as_api(exc: Exception) -> ApiError:
    """CompositeError of a kernel, StatsError of `stats/*` → 422 {code, message, hint, param}."""
    from bf_core.stats.weighted import StatsError

    from .intervals import CompositeError

    if isinstance(exc, (CompositeError, StatsError)):
        return _bad(exc.code, exc.message, exc.hint, exc.param)
    raise exc


# --- the request -------------------------------------------------------------------------------------------


@dataclass
class RunSpec:
    set_id: int
    table_id: int
    table_name: str
    method: str
    fields: list[str]  # stored names of the chosen fields (numbers and text)
    numbers: list[str]
    texts: list[str]
    units: dict[str, str | None]
    density: str | None
    kernel: Any  # LengthParams (length, domain) | BenchParams (bench)
    name: str
    replace: bool
    body: dict[str, Any]  # the request as given, without `replace` — `dh_results.params`
    rows: int = 0
    domain: DomainSpec | None = None

    def read_fields(self) -> list[str]:
        """Fields read from the samples: the chosen ones, the density, the domain field of the same table."""
        d = self.domain
        own = [d.field] if d is not None and d.wireframes is None and d.table_id == self.table_id else []
        return list(dict.fromkeys([*self.fields, *([self.density] if self.density else []), *own]))

    def other_domain(self) -> DomainSpec | None:
        """The domain when it is a field of another table (read on its own)."""
        d = self.domain
        return d if d is not None and d.wireframes is None and d.table_id != self.table_id else None

    def tables(self) -> list[tuple[int, list[str]]]:
        """(table id, fields) read — for `results.make_inputs`."""
        d = self.other_domain()
        return [(self.table_id, self.read_fields()), *([(d.table_id, [d.field])] if d else [])]

    def mesh_ids(self) -> list[int]:
        """The wireframes of a domain by wireframes — `inputs.meshes`."""
        d = self.domain
        return [] if d is None or d.wireframes is None else d.wireframes.ids

    def hash_params(self) -> dict[str, Any]:
        """What the result depends on besides the data (not the name of the table)."""
        d = self.domain
        dom = None if d is None else {"table": dialect.name_key(d.table_name), "field": d.field}
        if d is not None and d.wireframes is not None:
            dom = {"wireframes": d.wireframes.keys(), "names": d.wireframes.names()}
        return {"method": self.method, "fields": self.fields, "density": self.density, "domain": dom,
                **asdict(self.kernel)}  # fmt: skip


@dataclass
class DomainSpec:
    table_id: int
    table_name: str
    field: str  # stored name ("" — by wireframes)
    kind: str  # number | text | wireframes
    wireframes: Any = None  # jobs_coding.Wireframes: the bodies by priority, DOMAIN — their names


def _names(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.replace(";", ",").split(",")
    if not isinstance(raw, list):
        raise _bad("bad_param", "Поля: нужен список названий.", "Отметьте поля таблицы.", "fields")
    return list(dict.fromkeys(str(x).strip() for x in raw if str(x).strip()))


def resolve_table(conn: sa.Connection, set_id: int, given: Any) -> Any:
    """A ready interval table of the set by its id (as text or a number) or by its name; 404 otherwise."""
    q = sa.select(dh_tables.c.id, dh_tables.c.name, dh_tables.c.kind, dh_tables.c.row_count).where(
        dh_tables.c.set_id == set_id, dh_tables.c.state == "ready", dh_tables.c.kind.in_(("assay", "interval"))
    )
    text = str(given).strip() if given is not None else ""
    row = None
    if text.isdigit():
        row = conn.execute(q.where(dh_tables.c.id == int(text))).first()
    if row is None and text:
        row = conn.execute(q.where(dh_tables.c.name_key == dialect.name_key(text))).first()
    if row is None:
        raise ApiError(404, "table_not_found", "Таблица не найдена.", "Выберите интервальную таблицу набора.")
    return row


def _size(x: float) -> str:
    return f"{x:.6f}".rstrip("0").rstrip(".").replace(".", ",")


def default_name(method: str, p: Any) -> str:
    if method == "domain":
        return "Композиты по доменам"
    if method == "bench":
        return f"Композиты по уступам {_size(p.bench)} м"
    return f"Композиты {_size(p.length)} м"


def parse_domain(conn: sa.Connection, set_id: int, table: Any, raw: Any) -> DomainSpec:
    """`domain` of the body: {table, field} (no table — the samples), a field name of the samples, or
    {wireframe_ids} — closed bodies by priority (`jobs_coding.parse_wireframes`: 422 `wireframe_open`)."""
    from .source import _fields

    if isinstance(raw, dict) and raw.get("wireframe_ids"):
        from .jobs_coding import parse_wireframes

        wf = parse_wireframes(conn, raw["wireframe_ids"])
        return DomainSpec(int(table.id), str(table.name), "", "wireframes", wf)
    if isinstance(raw, str) and raw.strip():
        raw = {"field": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("field"), str) or not raw["field"].strip():
        raise _bad("domain_missing", "Метод «По доменам»: не выбран домен.", "Выберите поле домена.", "domain")
    t = resolve_table(conn, set_id, raw.get("table") or table.id)
    f = next(iter(_fields(conn, int(t.id), [raw["field"].strip()]).values()))
    return DomainSpec(int(t.id), t.name, f.name, f.kind)


def parse_run(conn: sa.Connection, body: dict[str, Any]) -> RunSpec:
    """The body of `POST /composites/run` checked against the project and the ranges of 8.2.2."""
    from .length import LengthParams
    from .source import _fields

    set_id = body.get("set_id")
    if not isinstance(set_id, int) or isinstance(set_id, bool):
        raise ApiError(404, "set_not_found", "Не задан набор скважин.", "Выберите набор скважин.")
    method = body.get("method", "length")
    if method not in METHODS:
        raise _bad(
            "bad_param", f"Метод: неизвестное значение «{method}».", "Допустимо: length, domain, bench.", "method"
        )
    t = resolve_table(conn, set_id, body.get("table"))
    domain = parse_domain(conn, set_id, t, body.get("domain")) if method == "domain" else None
    fields = _names(body.get("fields"))
    if not fields:
        fields = [
            c.name
            for c in conn.execute(
                sa.select(dh_columns.c.name, dh_columns.c.role)
                .where(dh_columns.c.table_id == t.id, dh_columns.c.deleted.is_(False), dh_columns.c.kind == "number")
                .order_by(dh_columns.c.position, dh_columns.c.id)
            )
            if c.role not in ("source", "fixed")
        ]
    density = body.get("density")
    density = str(density).strip() if isinstance(density, str) and density.strip() else None
    chosen = _fields(conn, int(t.id), [*fields, *([density] if density else [])])
    by_name = {n.casefold(): f for n, f in chosen.items()}
    stored = [by_name[n.casefold()].name if n.casefold() in by_name else n for n in fields]
    stored = list(dict.fromkeys(stored))
    numbers = [n for n in stored if chosen[n].kind == "number"]
    if not numbers:
        raise _bad("no_numeric_fields", "Не выбрано ни одного числового поля.", "Отметьте поля содержаний.", "fields")
    if density is not None:
        d = by_name[density.casefold()]
        if d.kind != "number":
            raise _bad("bad_param", f"Плотность: поле «{d.name}» не числовое.", "Выберите числовое поле.", "density")
        density = d.name
    try:
        if method == "bench":
            from .benches import BenchParams

            p: Any = BenchParams.of(body)
        else:
            p = LengthParams.of(body)
    except Exception as exc:  # noqa: BLE001 - CompositeError → 422, anything else re-raised
        raise as_api(exc) from None
    name = body.get("name")
    name = str(name).strip() if isinstance(name, str) and name.strip() else default_name(method, p)
    if len(name) > NAME_MAX:
        raise _bad("bad_param", "Имя таблицы слишком длинное.", f"Не больше {NAME_MAX} знаков.", "name")
    if dialect.name_key(name) == dialect.name_key(t.name):
        raise _bad("name_is_source", "Имя результата совпадает с таблицей-источником.", "Задайте другое имя.", "name")
    return RunSpec(
        set_id=set_id,
        table_id=int(t.id),
        table_name=t.name,
        method=method,
        fields=stored,
        numbers=numbers,
        texts=[n for n in stored if chosen[n].kind == "text"],
        units={n: f.unit for n, f in chosen.items()},
        density=density,
        kernel=p,
        name=name,
        replace=body.get("replace") is True,
        body={k: v for k, v in body.items() if k != "replace"},
        rows=int(t.row_count or 0),
        domain=domain,
    )


def memory_mb(params: dict[str, Any]) -> int:
    """ADR 0018 §5: rows × (fields + 6) × 8 B × 3."""
    body = params.get("body") or {}
    n_fields = len(_names(body.get("fields"))) or 10
    return max(256, int(int(params.get("rows") or 0) * (n_fields + 6) * 8 * 3 / 2**20) + 128)


# --- dependent operations (решение 18) ---------------------------------------------------------------------


@dataclass
class Again:
    """What a repeated dependent operation gives: columns for the new table and its record."""

    columns: list[tuple[Any, Any]]  # (store.ColumnSpec, values in the row order of the new table)
    fields: list[str]  # fields of the new table it read (its inputs)
    params: dict[str, Any] | None = None  # None — as before
    sha256: str | None = None
    algo: str | None = None
    report: dict[str, Any] = field(default_factory=dict)


#: op → fn(out: sink.OutTable, record: dict of dh_results) -> Again. Registered by the modules of the column
#: operations (12.21: top cut, equivalent, declustering) at import; the worker imports them by HANDLER_MODULES.
DEPENDENTS: dict[str, Callable[[Any, dict[str, Any]], Again]] = {}


@dataclass(frozen=True)
class AgainContext:
    """What a repeated dependent may read besides the arrays of the new table: the project (a domain of another
    table, the trajectories), the replaced table and the new one (its id goes into the repeated `params`)."""

    engine: Any
    set_id: int
    old_table: int | None
    new_table: int | None


#: Set while `_again` repeats the dependents of one job.
AGAIN: ContextVar[AgainContext | None] = ContextVar("composites_again", default=None)


def _registered() -> dict[str, Callable[[Any, dict[str, Any]], Again]]:
    """DEPENDENTS with the column operations of 12.21 registered (in the API process too, not only in a worker)."""
    from . import jobs_prep  # noqa: F401 - registers top cut, equivalent and DECL_W

    return DEPENDENTS


def register_dependent(
    op: str,
) -> Callable[[Callable[[Any, dict[str, Any]], Again]], Callable[[Any, dict[str, Any]], Again]]:
    def wrap(fn: Callable[[Any, dict[str, Any]], Again]) -> Callable[[Any, dict[str, Any]], Again]:
        DEPENDENTS[op] = fn
        return fn

    return wrap


def dependents(conn: sa.Connection, table_id: int) -> list[dict[str, Any]]:
    """Results that wrote columns into the table, oldest first."""
    from .results import TABLE_OPS

    rows = conn.execute(
        sa.select(dh_results)
        .where(dh_results.c.table_id == table_id, dh_results.c.op.not_in(TABLE_OPS))
        .order_by(dh_results.c.created_at, dh_results.c.id)
    ).mappings()
    return [dict(r) for r in rows]


def lost_dependents(conn: sa.Connection, table_id: int) -> list[dict[str, Any]]:
    """What a replacement of the table loses: dependents without a way to repeat them (for «Заменить?»)."""
    from .results import OP_TITLES

    return [
        {"id": int(r["id"]), "op": r["op"], "title": OP_TITLES.get(r["op"], r["op"]), "name": r["name"]}
        for r in dependents(conn, table_id)
        if r["op"] not in _registered()
    ]


def _replaced_table(conn: sa.Connection, set_id: int, name: str) -> int | None:
    tid = conn.execute(
        sa.select(dh_tables.c.id).where(
            dh_tables.c.set_id == set_id,
            dh_tables.c.name_key == dialect.name_key(name),
            dh_tables.c.state == "ready",
            dh_tables.c.kind == "interval",
        )
    ).scalar()
    return None if tid is None else int(tid)


def _again(engine: sa.Engine, spec: Any, out: sink.OutTable, table_id: int | None = None) -> list[sink.Dependent]:
    if not spec.replace:
        return []
    with dialect.read_tx(engine) as conn:
        old = _replaced_table(conn, spec.set_id, spec.name)
        deps = [] if old is None else dependents(conn, old)
    made = []
    known = _registered()
    token = AGAIN.set(AgainContext(engine, spec.set_id, old, table_id))
    try:
        for r in deps:
            fn = known.get(r["op"])
            if fn is None:
                continue  # lost: the route listed it in «Заменить?» before the job was started
            made.append(_dependent(r, fn(out, r)))
    finally:
        AGAIN.reset(token)
    return made


def _dependent(r: dict[str, Any], a: Again) -> sink.Dependent:
    from . import sink

    return sink.Dependent(op=r["op"], name=r["name"], params=a.params or r["params"], fields=a.fields,
                          sha256=a.sha256 or r["sha256"], algo=a.algo or r["algo"],
                          report={**(r["report"] or {}), **a.report}, columns=a.columns)  # fmt: skip


# --- the job -----------------------------------------------------------------------------------------------


def _versions() -> dict[str, str]:
    import numba
    import numpy as np

    return {"numpy": np.__version__, "numba": numba.__version__}


def _user(exc: ApiError) -> JobError:
    return JobError(exc.code, exc.message, exc.hint, retry=False)


def _fresh(
    engine: sa.Engine, rebuild: Any, set_id: int, sha: str, algo: str, inputs: dict[str, Any]
) -> dict[str, Any] | None:
    """«Перестроить» with the same hash and algorithm: no computation, the record follows the inputs."""
    from . import results

    if rebuild is None:
        return None
    with dialect.write_tx(engine) as conn:
        rec = conn.execute(sa.select(dh_results).where(dh_results.c.id == int(rebuild))).mappings().first()
        if rec is None or rec["sha256"] != sha or rec["algo"] != algo:
            return None
        results.mark_fresh(conn, int(rebuild), inputs)
    return {"set_id": set_id, "result_id": int(rebuild), "table_id": rec["table_id"], "fresh": True,
            "events": [["composites.changed", {"set_id": set_id}]]}  # fmt: skip


Tick2 = Callable[[float | None, str | None], None]


def _store(
    engine: sa.Engine, folder: Any, spec: Any, made: Any, *, op: str, inputs: dict[str, Any], sha: str, algo: str,
    job_id: int, t0: float, tick: Tick2,
) -> dict[str, Any]:  # fmt: skip
    """The table aside, the repeated dependents, the final transaction, the snapshot; the result of the job."""
    from . import sink

    with sink.guarded(engine, job_id):
        tid = sink.build_table(engine, spec.set_id, made.out, job_id=job_id,
                               tick=lambda f, m: tick(0.6 + 0.3 * (f or 0.0), m or "Запись таблицы"))  # fmt: skip
        again = _again(engine, spec, made.out, tid)
        report = {**made.report, "versions": _versions(), "seconds": round(time.perf_counter() - t0, 3)}
        res = sink.commit(engine, sink.Commit(set_id=spec.set_id, op=op, name=spec.name, params=spec.body,
                                              inputs=inputs, sha256=sha, algo=algo, report=report, job_id=job_id,
                                              table=tid, mode="replace" if spec.replace else "rename",
                                              dependents=again))  # fmt: skip
    # no tick from here on: a cancel after the final transaction would report a committed result as cancelled
    try:
        sink.write_snapshot(engine, folder / "cache" / "tables", tid, sink.with_dependents(made.out, again))
    except Exception:  # noqa: BLE001 - the snapshot only speeds up reading; the table is committed
        pass
    set_id = spec.set_id
    events: list[list[Any]] = [["drillholes.changed", {"set_id": set_id, "table": str(tid), "rev": res["rev"]}]]
    events += [["drillholes.changed", {"set_id": set_id, "table": str(t), "rev": 0}] for t in res["replaced"]]
    events.append(["composites.changed", {"set_id": set_id}])
    return {"set_id": set_id, "result_id": res["result_id"], "table_id": tid, "name": res["name"],
            "rows": made.out.from_m.shape[0], "replaced": res["replaced"], "fresh": False,
            "journal": made.journal, "events": events}  # fmt: skip


def _body_of(params: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    body = dict(params.get("body") or {})
    rebuild = params.get("rebuild")
    if rebuild is not None:
        body["replace"] = True
    return body, rebuild


def run(
    engine: sa.Engine,
    folder: Any,
    params: dict[str, Any],
    *,
    job_id: int,
    tick: Callable[[float | None, str | None], None],
) -> dict[str, Any]:
    """The body of `composite.run` (the handler and the tests in the process)."""
    from . import compose, jobs_coding, results, source, strips

    t0 = time.perf_counter()
    body, rebuild = _body_of(params)
    with dialect.read_tx(engine) as conn:
        spec = parse_run(conn, body)
        inputs = results.make_inputs(conn, spec.set_id, spec.tables(), trajectories=True, meshes=spec.mesh_ids())
    tick(0.02, "Чтение проб")
    cache = folder / "cache" / "tables"
    src = source.read(engine, spec.set_id, spec.table_id, spec.read_fields(), cache_dir=cache, tick=tick)
    d = spec.other_domain()
    dom = None if d is None else source.read(engine, spec.set_id, d.table_id, [d.field], cache_dir=cache, tick=tick)
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    st = source.trajectories(project, spec.set_id, tick=tick)
    algo = ALGO[spec.method]
    stations = source.stations_bytes(st, src.hole_db_ids.tolist(), tick=tick)
    sha = source.input_hash([src, *([dom] if dom else [])], spec.hash_params(), stations=stations, algo=algo,
                            tick=tick)  # fmt: skip
    fresh = _fresh(engine, rebuild, spec.set_id, sha, algo, inputs)
    if fresh is not None:
        return fresh
    with dialect.read_tx(engine) as conn:
        ends = compose.hole_depths(conn, spec.set_id, src.hole_db_ids)
    tick(0.1, "Композиты")
    with jobs_coding.reading(project, keep=False):  # domains by wireframes read their geometry
        made = compose.compute(
            spec, src, dom, ends, st, threads=strips.default_threads(), tick=lambda f: tick(0.1 + 0.5 * f, None),
            release=True,
        )  # fmt: skip
    del src, dom
    return _store(engine, folder, spec, made, op=spec.method, inputs=inputs, sha=sha, algo=algo, job_id=job_id,
                  t0=t0, tick=tick)  # fmt: skip


def run_tables(
    engine: sa.Engine,
    folder: Any,
    params: dict[str, Any],
    op: str,
    *,
    job_id: int,
    tick: Callable[[float | None, str | None], None],
) -> dict[str, Any]:
    """The body of `composite.merge` and `composite.coords`."""
    from . import compose_tables as ct
    from . import results, source, strips

    t0 = time.perf_counter()
    body, rebuild = _body_of(params)
    with dialect.read_tx(engine) as conn:
        spec = ct.parse_tables(conn, body, op)
        inputs = results.make_inputs(
            conn, spec.set_id, [(i, f) for i, _n, f in spec.tables], trajectories=op == "coords"
        )
    tick(0.02, "Чтение таблиц")
    cache = folder / "cache" / "tables"
    srcs = [source.read(engine, spec.set_id, i, f, cache_dir=cache, tick=tick) for i, _n, f in spec.tables]
    algo = ALGO[op]
    st = None
    stations = None
    if op == "coords":
        st = source.trajectories(SimpleNamespace(engine=engine, uid=f"job:{folder}"), spec.set_id, tick=tick)
        stations = source.stations_bytes(st, srcs[0].hole_db_ids.tolist(), tick=tick)
    sha = source.input_hash(srcs, spec.hash_params(), stations=stations, algo=algo, tick=tick)
    fresh = _fresh(engine, rebuild, spec.set_id, sha, algo, inputs)
    if fresh is not None:
        return fresh
    tick(0.1, "Объединение" if op == "merge" else "Координаты")
    part = lambda f: tick(0.1 + 0.5 * f, None)  # noqa: E731
    threads = strips.default_threads()
    made = (
        ct.merge_compute(spec, srcs, threads=threads, tick=part, release=True)
        if op == "merge"
        else ct.coords_compute(spec, srcs[0], st, threads=threads, tick=part)
    )
    del srcs
    return _store(engine, folder, spec, made, op=op, inputs=inputs, sha=sha, algo=algo, job_id=job_id, t0=t0,
                  tick=tick)  # fmt: skip


def _log_journal(ctx: JobContext, lines: Sequence[str]) -> None:
    for line in lines:
        ctx.log(line, "warning")


@handler("composite.run", "Композиты", queue="heavy", estimate_memory=memory_mb)
def run_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        out = run(ctx.engine(), ctx.project_folder, ctx.params, job_id=ctx.id, tick=ctx.tick)
    except ApiError as exc:
        raise _user(exc) from None
    _log_journal(ctx, out.get("journal") or [])
    return out


def _tables_job(ctx: JobContext, op: str) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        out = run_tables(ctx.engine(), ctx.project_folder, ctx.params, op, job_id=ctx.id, tick=ctx.tick)
    except ApiError as exc:
        raise _user(exc) from None
    _log_journal(ctx, out.get("journal") or [])
    return out


@handler("composite.merge", "Объединение таблиц", queue="heavy", estimate_memory=memory_mb)
def merge_job(ctx: JobContext) -> dict[str, Any]:
    return _tables_job(ctx, "merge")


@handler("composite.coords", "Координаты проб", queue="heavy", estimate_memory=memory_mb)
def coords_job(ctx: JobContext) -> dict[str, Any]:
    return _tables_job(ctx, "coords")
