"""Jobs of composites (ТЗ 8.2.2–8.2.3; ADR 0018 §2–3, §6, решения 18, 26): `composite.run` — by length (by domains
and by benches — when the kernels of 12.4–12.5 come), «Перестроить» of its results.

    spec = parse_run(conn, body)            # the body of `POST /composites/run` → RunSpec; ApiError 422 / 404
    made = compute(spec, src, ends, st)     # the rows of the new table, the report, the journal (job and preview)

The job (`composite.run`, heavy) reads the chosen columns (`source.read`), the trajectories (X, Y, Z of the
middle of every composite) and the depths of the holes, hashes the inputs (SHA-256, `source.input_hash`),
computes, builds the table aside and commits it in one transaction (`sink`). Params of the job:
{"body": the request as given, "rows": rows of the source (memory estimate), "rebuild": result id | absent}.
«Перестроить» with the same hash and `algo` does not compute: the record follows the current inputs
(`results.mark_fresh`). Replacing a result table (a rebuild or «Заменить») repeats the operations that wrote
columns into it (top cut, equivalent, DECL_W — `DEPENDENTS`, registered by their modules) in the order they
were made, before the final transaction; their records go to the new table (решение 18).

The result of the job carries `events` — [[event, data], …]: `drillholes.changed` for the new and the replaced
tables, `composites.changed {set_id}`; the scheduler publishes them (line of the orchestrator).

`dh_results.params` is the body of the request as given (`table`, `fields` as a list, `domain`); the numbers the
kernel ran with are `report.kernel`; `report.summary {before, after}` — the summary of the window over the set,
`report.seconds`, `report.edits` (always empty here: no hand edits of composites).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_holes, dh_results, dh_tables
from bf_core.jobs.context import JobError
from bf_core.jobs.registry import handler

if TYPE_CHECKING:
    import numpy as np

    from bf_core.jobs.context import JobContext

    from . import sink, source
    from .length import LengthParams

ALGO = {"length": "length-1"}
METHODS = ("length", "domain", "bench")
READY = frozenset({"length"})  # TODO(12.4, 12.5): "domain", "bench" — `composites/{domains,benches}.py`
OWN = ("LENGTH", "X", "Y", "Z", "DENSITY", "SAMPLED_LEN", "N_SAMPLES", "DOMAIN", "FLAG")
NAME_MAX = 200


def _bad(code: str, message: str, hint: str, param: str | None = None) -> ApiError:
    return ApiError(422, code, message, hint, {"param": param} if param else None)


def not_ready(what: str) -> ApiError:
    return ApiError(
        422, "not_ready", f"{what}: расчёт ещё не подключён в этой сборке.", "Используйте композиты по длине."
    )


def as_api(exc: Exception) -> ApiError:
    """CompositeError of a kernel → 422 {code, message, hint, param}."""
    from .intervals import CompositeError

    if isinstance(exc, CompositeError):
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
    length: LengthParams
    name: str
    replace: bool
    body: dict[str, Any]  # the request as given, without `replace` — `dh_results.params`
    rows: int = 0

    def read_fields(self) -> list[str]:
        return list(dict.fromkeys([*self.fields, *([self.density] if self.density else [])]))

    def hash_params(self) -> dict[str, Any]:
        """What the result depends on besides the data (not the name of the table)."""
        return {"method": self.method, "fields": self.fields, "density": self.density, **asdict(self.length)}


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


def default_name(method: str, p: LengthParams) -> str:
    from .length import _m

    if method == "domain":
        return "Композиты по доменам"
    return f"Композиты {_m(p.length).rstrip('0').rstrip(',')} м"


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
    if method not in READY:
        raise not_ready("Композиты по доменам" if method == "domain" else "Композиты по уступам")
    t = resolve_table(conn, set_id, body.get("table"))
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
        length=p,
        name=name,
        replace=body.get("replace") is True,
        body={k: v for k, v in body.items() if k != "replace"},
        rows=int(t.row_count or 0),
    )


def memory_mb(params: dict[str, Any]) -> int:
    """ADR 0018 §5: rows × (fields + 6) × 8 B × 3."""
    body = params.get("body") or {}
    n_fields = len(_names(body.get("fields"))) or 10
    return max(256, int(int(params.get("rows") or 0) * (n_fields + 6) * 8 * 3 / 2**20) + 128)


# --- the computation (the job and the preview) -------------------------------------------------------------


@dataclass
class Made:
    out: sink.OutTable
    report: dict[str, Any]
    journal: list[str]


def hole_depths(conn: sa.Connection, set_id: int, hole_db_ids: Any) -> np.ndarray:
    """TD of the holes (dh_holes.id, in the given order); NaN — not given."""
    import numpy as np

    td = {
        int(r.id): r.td
        for r in conn.execute(sa.select(dh_holes.c.id, dh_holes.c.td).where(dh_holes.c.set_id == set_id))
    }
    got = [td.get(int(h)) for h in np.asarray(hole_db_ids, dtype=np.int64).tolist()]
    return np.array([math.nan if v is None else float(v) for v in got], dtype=float)


def _midpoints(st: Any, hole_db_ids: np.ndarray, hole_of: np.ndarray, depth: np.ndarray) -> tuple[np.ndarray, int]:
    """X, Y, Z (n, 3) at the depths; NaN for holes without a trajectory. Returns (xyz, holes without one)."""
    import numpy as np

    xyz = np.full((len(depth), 3), np.nan)
    if st is None:
        return xyz, len(hole_db_ids)
    tix = np.array([st.index.get(int(h), -1) for h in hole_db_ids.tolist()], dtype=np.int64)
    skipped = np.asarray(st.traj.skipped, dtype=np.int64)
    good = (tix >= 0) & ~np.isin(tix, skipped)
    rows = np.flatnonzero(good[hole_of]) if len(hole_of) else np.zeros(0, dtype=np.int64)
    if len(rows):
        xyz[rows] = st.traj.positions(tix[hole_of[rows]], depth[rows])
    return xyz, int(np.count_nonzero(~good))


def compute(
    spec: RunSpec,
    src: source.Lines,
    ends: Any,
    st: Any,
    *,
    threads: int = 1,
    tick: Callable[[float], None] | None = None,
) -> Made:
    """Composites by length of the lines `src` (all the holes of the table, or one for the preview)."""
    import numpy as np

    from bf_core.drillholes.store import ColumnSpec

    from . import intervals as iv
    from . import sink
    from .length import FLAG_LOW, composite_by_length, flag_column, summary

    lines = iv.build_lines(src.hole_of().astype(np.int64), src.from_m, src.to_m)
    values = iv.stack_fields(lines, [src.numbers[n] for n in spec.numbers])
    density = lines.take(src.numbers[spec.density]) if spec.density else None
    texts = [lines.take(src.codes[n]) for n in spec.texts]
    depth = np.asarray(ends, dtype=float)[lines.holes] if lines.n_holes else np.zeros(0)
    try:
        c = composite_by_length(lines, values, spec.length, density=density, by_density=density is not None,
                                texts=texts, ends=depth, threads=threads, tick=tick)  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - CompositeError → 422
        raise as_api(exc) from None
    cl = c.lines
    hole = cl.hole_ids().astype(np.int64)  # dense index of src.holes
    n = cl.n
    xyz, no_traj = _midpoints(st, src.hole_db_ids, hole, (cl.frm + cl.to) / 2.0)
    taken = {o.casefold() for o in OWN}

    def own(name: str) -> str:
        return f"{name}_SRC" if name.casefold() in taken else name

    cols: list[ColumnSpec] = [ColumnSpec("LENGTH", "number", "м"), ColumnSpec("X", "number", "м"),
                              ColumnSpec("Y", "number", "м"), ColumnSpec("Z", "number", "м")]  # fmt: skip
    vals: list[np.ndarray] = [cl.to - cl.frm, xyz[:, 0], xyz[:, 1], xyz[:, 2]]
    for k, name in enumerate(spec.numbers):
        role = src.fields[name].role if src.fields[name].role in ("component", "value") else "value"
        cols.append(ColumnSpec(own(name), "number", spec.units.get(name), role))
        vals.append(np.ascontiguousarray(c.values.values[:, k]))
    for k, name in enumerate(spec.texts):
        labels = np.array([*src.labels[name], None], dtype=object)
        cols.append(ColumnSpec(own(name), "text"))
        vals.append(labels[c.values.texts[k]])  # −1 → the last one: None
    cols += [ColumnSpec("DENSITY", "number", spec.units.get(spec.density) if spec.density else None),
             ColumnSpec("SAMPLED_LEN", "number", "м"), ColumnSpec("N_SAMPLES", "number"),
             ColumnSpec("DOMAIN", "text"), ColumnSpec("FLAG", "text")]  # fmt: skip
    vals += [c.values.density, c.values.sampled, c.values.count.astype(float), np.full(n, None, dtype=object),
             flag_column([(FLAG_LOW, c.values.low)], n)]  # fmt: skip
    names = [src.holes[i] for i in hole.tolist()]
    out = sink.OutTable(spec.name, names, cl.frm, cl.to, cols, vals)
    journal = list(lines.notes(spec.table_name)) + list(c.journal)
    if src.skipped:
        journal.insert(0, f"Таблица «{spec.table_name}»: строк без «от» или «до» — {src.skipped}, в расчёт не вошли.")
    if no_traj and n:
        journal.append(f"Скважин без траектории — {no_traj}: X, Y, Z композитов пусты.")
    report = {
        **c.report,
        "kernel": asdict(spec.length),
        "rows_in": src.rows,
        "skipped": src.skipped,
        "no_trajectory": no_traj,
        "summary": {
            "before": summary(lines.lengths(), values, spec.numbers),
            "after": summary(cl.to - cl.frm, c.values.values, spec.numbers),
        },
        "journal": journal,
        "edits": [],
    }
    return Made(out, report, journal)


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
        if r["op"] not in DEPENDENTS
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


def _again(engine: sa.Engine, spec: RunSpec, out: sink.OutTable) -> list[sink.Dependent]:
    from . import sink

    if not spec.replace:
        return []
    with dialect.read_tx(engine) as conn:
        old = _replaced_table(conn, spec.set_id, spec.name)
        deps = [] if old is None else dependents(conn, old)
    made = []
    for r in deps:
        fn = DEPENDENTS.get(r["op"])
        if fn is None:
            continue  # lost: the route listed it in «Заменить?» before the job was started
        a = fn(out, r)
        made.append(sink.Dependent(op=r["op"], name=r["name"], params=a.params or r["params"], fields=a.fields,
                                   sha256=a.sha256 or r["sha256"], algo=a.algo or r["algo"],
                                   report={**(r["report"] or {}), **a.report}, columns=a.columns))  # fmt: skip
    return made


# --- the job -----------------------------------------------------------------------------------------------


def _versions() -> dict[str, str]:
    import numba
    import numpy as np

    return {"numpy": np.__version__, "numba": numba.__version__}


def _user(exc: ApiError) -> JobError:
    return JobError(exc.code, exc.message, exc.hint, retry=False)


def run(
    engine: sa.Engine,
    folder: Any,
    params: dict[str, Any],
    *,
    job_id: int,
    tick: Callable[[float | None, str | None], None],
) -> dict[str, Any]:
    """The body of `composite.run` (the handler and the tests in the process)."""
    from . import results, sink, source, strips

    t0 = time.perf_counter()
    body = dict(params.get("body") or {})
    rebuild = params.get("rebuild")
    if rebuild is not None:
        body["replace"] = True
    with dialect.read_tx(engine) as conn:
        spec = parse_run(conn, body)
        inputs = results.make_inputs(conn, spec.set_id, [(spec.table_id, spec.read_fields())], trajectories=True)
    tick(0.02, "Чтение проб")
    cache = folder / "cache" / "tables"
    src = source.read(engine, spec.set_id, spec.table_id, spec.read_fields(), cache_dir=cache)
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}")
    st = source.trajectories(project, spec.set_id)
    algo = ALGO[spec.method]
    stations = source.stations_bytes(st, src.hole_db_ids.tolist())
    sha = source.input_hash([src], spec.hash_params(), stations=stations, algo=algo)
    if rebuild is not None:
        with dialect.write_tx(engine) as conn:
            rec = conn.execute(sa.select(dh_results).where(dh_results.c.id == int(rebuild))).mappings().first()
            if rec is not None and rec["sha256"] == sha and rec["algo"] == algo:
                results.mark_fresh(conn, int(rebuild), inputs)
                return {"set_id": spec.set_id, "result_id": int(rebuild), "table_id": rec["table_id"], "fresh": True,
                        "events": [["composites.changed", {"set_id": spec.set_id}]]}  # fmt: skip
    with dialect.read_tx(engine) as conn:
        ends = hole_depths(conn, spec.set_id, src.hole_db_ids)
    tick(0.1, "Композиты")
    made = compute(spec, src, ends, st, threads=strips.default_threads(), tick=lambda f: tick(0.1 + 0.5 * f, None))
    with sink.guarded(engine, job_id):
        tid = sink.build_table(engine, spec.set_id, made.out, job_id=job_id,
                               tick=lambda f, m: tick(0.6 + 0.3 * (f or 0.0), m or "Запись таблицы"))  # fmt: skip
        again = _again(engine, spec, made.out)
        report = {**made.report, "versions": _versions(), "seconds": round(time.perf_counter() - t0, 3)}
        res = sink.commit(engine, sink.Commit(set_id=spec.set_id, op=spec.method, name=spec.name, params=spec.body,
                                              inputs=inputs, sha256=sha, algo=algo, report=report, job_id=job_id,
                                              table=tid, mode="replace" if spec.replace else "rename",
                                              dependents=again))  # fmt: skip
    # no tick from here on: a cancel after the final transaction would report a committed result as cancelled
    try:
        sink.write_snapshot(engine, cache, tid, sink.with_dependents(made.out, again))
    except Exception:  # noqa: BLE001 - the snapshot only speeds up reading; the table is committed
        pass
    events: list[list[Any]] = [["drillholes.changed", {"set_id": spec.set_id, "table": str(tid), "rev": res["rev"]}]]
    events += [["drillholes.changed", {"set_id": spec.set_id, "table": str(t), "rev": 0}] for t in res["replaced"]]
    events.append(["composites.changed", {"set_id": spec.set_id}])
    return {"set_id": spec.set_id, "result_id": res["result_id"], "table_id": tid, "name": res["name"],
            "rows": made.out.from_m.shape[0], "replaced": res["replaced"], "fresh": False,
            "journal": made.journal, "events": events}  # fmt: skip


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
