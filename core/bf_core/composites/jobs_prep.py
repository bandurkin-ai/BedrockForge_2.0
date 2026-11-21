"""Jobs of sample preparation (ТЗ 8.2.2; ADR 0018 §3, §6–7, решения 3, 8, 11, 12, 18, 26): the column operations
`composite.topcut`, `composite.equivalent`, `composite.declus` — fields written in place into the source table in
one transaction (`sink.commit` with `columns`: rev + 1, one journal group, the other results follow), a
`dh_results` record with the report. `composite.code` (coding by wireframes, a new table) — `jobs_coding.py`.

    spec = parse_prep(conn, body, op)              # the body of `POST /composites/{op}` → PrepSpec; ApiError 422 / 404
    made = compute_lines(spec, src, dom, st)       # the columns and the report: the job and the preview

Bodies (ADR 0018 §10; the window sends the source as `params.ts::sourceBody`):
    topcut      {set_id, table, field, domain?, value? | thresholds? | percentile?}       → <F>_CUT, <F>_CUT_F
    equivalent  {set_id, table, base, base_price, base_recovery?, base_unit?, base_price_unit?, others,
                 nan_zero?, name?}                                                         → <name> (<base>EQ)
    declus      {set_id, table, field, method, cmin?, cmax?, ncell?, noff?, minmax?, anisy?, anisz?, nx?, ny?}
                                                                                           → DECL_W
`domain` of the top cut — {table, field} (no table — the same table) or a field name of the table; {wireframe_ids}
— 422 `not_ready` with the way round: code the samples by wireframes, then the domain is their field CODE. A
sample takes the code of its own field, or of the row of the other table with the greatest length inside it
(`intervals.mode_by_length`); no domain — the group «» (ADR 0018, 12.8).
The points of the declustering are the middles of the samples on the trajectories of the set.

A column of these names that the table already has and that the same operation did not write is never
overwritten: 409 `column_exists`. The threshold of the top cut is not applied by itself (решение 8): the
analysis and the preview write nothing. `dh_results.params` is the body as given; `report` — that of the kernel
(top cut: field, columns, mode, domains, total, summary, notes, thresholds; equivalent: factors, n…, summary;
declus: n, sizes, means, best_size…) and `seconds`, `versions`, `edits` ([]). NaN in a report is null.

Dependents (решение 18): each operation is registered by `jobs.register_dependent` — a rebuild or «Заменить» of
a result table repeats it on the new table by its `params` (the arrays of the new table; a domain of another
table and the trajectories are read through `jobs.AGAIN`).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_holes, dh_results, dh_tables
from bf_core.jobs.registry import handler

from . import jobs as cj

if TYPE_CHECKING:
    from bf_core.drillholes.store import ColumnSpec
    from bf_core.jobs.context import JobContext

    from . import sink, source

OPS = ("topcut", "equivalent", "declus")
KINDS = {"topcut": "composite.topcut", "equivalent": "composite.equivalent", "declus": "composite.declus"}
STEP = {"topcut": "Урезка", "equivalent": "Эквивалент металла", "declus": "Декластеризация"}
cj.ALGO.update(topcut="topcut-1", equivalent="equivalent-1", declus="declus-1")
DECL_W = "DECL_W"
Tick = Callable[[float], None]


def clean(x: Any) -> Any:
    """A report as JSON: NaN and ±inf → None, numpy scalars → Python, tuples → lists."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, np.ndarray):
        return [clean(v) for v in x.tolist()]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(x) else None
    return x


# --- the request -------------------------------------------------------------------------------------------


@dataclass
class PrepSpec:
    op: str
    set_id: int
    table_id: int
    table_name: str
    fields: list[str]  # stored names read: [field] (top cut, declus); base, others (equivalent)
    units: dict[str, str | None]
    kernel: Any  # TopcutParams | EquivalentParams | DeclusParams | None (the analysis)
    columns: list[str]  # names written
    name: str  # of the result: «Урезка · AU»
    body: dict[str, Any]  # the request as given, without `replace` — `dh_results.params`
    rows: int = 0
    domain: cj.DomainSpec | None = None

    def read_fields(self) -> list[str]:
        d = self.domain
        own = [d.field] if d is not None and d.table_id == self.table_id else []
        return list(dict.fromkeys([*self.fields, *own]))

    def other_domain(self) -> cj.DomainSpec | None:
        return self.domain if self.domain is not None and self.domain.table_id != self.table_id else None

    def tables(self) -> list[tuple[int, list[str]]]:
        d = self.other_domain()
        return [(self.table_id, self.read_fields()), *([(d.table_id, [d.field])] if d else [])]

    def hash_params(self) -> dict[str, Any]:
        d = self.domain
        dom = None if d is None else {"table": dialect.name_key(d.table_name), "field": d.field}
        k = self.kernel
        kernel = clean(asdict(k)) if is_dataclass(k) and not isinstance(k, type) else None
        return {"op": self.op, "fields": self.fields, "domain": dom, "kernel": kernel}


def _units(conn: sa.Connection, table_id: int) -> dict[str, str | None]:
    rows = conn.execute(
        sa.select(dh_columns.c.name, dh_columns.c.unit).where(
            dh_columns.c.table_id == table_id, dh_columns.c.deleted.is_(False), dh_columns.c.kind == "number"
        )
    )
    return {r.name: r.unit for r in rows}


def _field(conn: sa.Connection, table_id: int, raw: Any, label: str = "Поле содержания") -> Any:
    from .source import _fields

    if not isinstance(raw, str) or not raw.strip():
        raise cj._bad("bad_param", f"{label}: не выбрано поле.", "Выберите числовое поле таблицы.", "field")
    f = next(iter(_fields(conn, table_id, [raw.strip()]).values()))
    if f.kind != "number":
        raise cj._bad("bad_param", f"{label}: поле «{f.name}» не числовое.", "Выберите числовое поле.", "field")
    return f


def _domain(conn: sa.Connection, set_id: int, t: Any, raw: Any) -> cj.DomainSpec | None:
    if raw is None or raw == "" or raw == {}:
        return None
    if isinstance(raw, dict) and not raw.get("wireframe_ids") and not str(raw.get("field") or "").strip():
        raise cj._bad("domain_missing", "Не выбрано поле домена.", "Выберите поле домена или «без домена».", "domain")
    if isinstance(raw, dict) and raw.get("wireframe_ids"):
        raise cj._bad(
            "not_ready",
            "Урезка: домены по каркасам не поддерживаются.",
            "Закодируйте пробы каркасами («Кодировать каркасами») и выберите домен по полю CODE.",
            "domain",
        )
    return cj.parse_domain(conn, set_id, t, raw)


def parse_prep(conn: sa.Connection, body: dict[str, Any], op: str, *, kernel: bool = True) -> PrepSpec:
    """The body of `POST /composites/{op}` checked against the project and the ranges of 8.2.2; `kernel=False` —
    the source only (the analysis of the top cut)."""
    from .source import _fields

    set_id = body.get("set_id")
    if not isinstance(set_id, int) or isinstance(set_id, bool):
        raise ApiError(404, "set_not_found", "Не задан набор скважин.", "Выберите набор скважин.")
    t = cj.resolve_table(conn, set_id, body.get("table"))
    units = _units(conn, int(t.id))
    domain = None
    k: Any = None
    try:
        if op == "equivalent":
            from .equivalent import EquivalentParams

            k = EquivalentParams.of(body, units)
            chosen = _fields(conn, int(t.id), k.fields)
            text = [f.name for f in chosen.values() if f.kind != "number"]
            if text:
                raise cj._bad("bad_param", f"Поле «{text[0]}» не числовое.", "Выберите поля содержаний.", "others")
            fields, columns, name = list(chosen), [k.name], f"Эквивалент · {k.name}"
        else:
            f = _field(conn, int(t.id), body.get("field"))
            fields = [f.name]
            if op == "topcut":
                from .topcut import TopcutParams, column_names

                domain = _domain(conn, set_id, t, body.get("domain"))
                k = TopcutParams.of(body) if kernel else None
                columns, name = list(column_names(f.name)), f"Урезка · {f.name}"
            elif op == "declus":
                from bf_core.stats.declus3d import DeclusParams

                k = DeclusParams.of(body)
                columns, name = [DECL_W], f"Декластеризация · {f.name}"
            else:
                raise ApiError(404, "unknown_operation", f"Нет операции «{op}».", "Обновите приложение.")
    except ValueError as exc:  # CompositeError, StatsError → 422; anything else re-raised
        raise cj.as_api(exc) from None
    return PrepSpec(op=op, set_id=set_id, table_id=int(t.id), table_name=t.name, fields=fields,
                    units={n: units.get(n) for n in fields}, kernel=k, columns=columns, name=name,
                    body={k2: v for k2, v in body.items() if k2 != "replace"}, rows=int(t.row_count or 0),
                    domain=domain)  # fmt: skip


def check_columns(conn: sa.Connection, spec: PrepSpec) -> None:
    """409 `column_exists`: a column of the names to write is in the table and no earlier result of the same
    operation wrote it (a field of the user is never overwritten)."""
    live = {
        r.name.casefold(): r.name
        for r in conn.execute(
            sa.select(dh_columns.c.name).where(dh_columns.c.table_id == spec.table_id, dh_columns.c.deleted.is_(False))
        )
    }
    ours: set[str] = set()
    for r in conn.execute(
        sa.select(dh_results.c.report).where(dh_results.c.table_id == spec.table_id, dh_results.c.op == spec.op)
    ):
        for w in (r.report or {}).get("written") or []:
            ours |= {str(n).casefold() for n in w.get("columns") or []}
    clash = [live[n.casefold()] for n in spec.columns if n.casefold() in live and n.casefold() not in ours]
    if clash:
        raise ApiError(
            409,
            "column_exists",
            f"В таблице «{spec.table_name}» уже есть поле «{clash[0]}».",
            "Задайте другое имя поля результата или переименуйте поле таблицы.",
            {"param": "name" if spec.op == "equivalent" else "field", "name": clash[0]},
        )


# --- the computation ---------------------------------------------------------------------------------------


@dataclass
class Made:
    columns: list[tuple[ColumnSpec, np.ndarray]]  # in the row order of the input
    report: dict[str, Any]
    journal: list[str]


def number_names(uniques: Any) -> list[str]:
    """Names of the codes of a numeric domain field: 1.0 → «1», 2.5 → «2.5»."""
    return [format(float(u), ".15g") for u in np.asarray(uniques, dtype=float).tolist()]


def _value_codes(values: Any, kind: str) -> tuple[np.ndarray, list[str]]:
    """int32 codes (−1 — empty) and names of a domain column given by its values."""
    from .domains import number_codes
    from .source import text_codes

    if kind == "text":
        return text_codes(np.asarray(values, dtype=object))
    codes, uniques = number_codes(values)
    return codes, number_names(uniques)


def _line_codes(lines: source.Lines, d: cj.DomainSpec) -> tuple[np.ndarray, list[str]]:
    if d.kind == "text":
        return lines.codes[d.field], list(lines.labels[d.field])
    return _value_codes(lines.numbers[d.field], "number")


def codes_on(
    holes: Sequence[str], starts: np.ndarray, frm: np.ndarray, to: np.ndarray, dom: source.Lines, d: cj.DomainSpec
) -> tuple[np.ndarray, list[str]]:
    """The domain code of every row (rows grouped by hole: `holes[i]` — rows starts[i]:starts[i + 1]) from the rows
    of another interval table: the code with the greatest length inside the row; none — −1."""
    from . import intervals as iv

    rank = {name: i for i, name in enumerate(holes)}
    h = np.array([rank.get(name, -1) for name in dom.holes], dtype=np.int64)[dom.hole_of()]
    keep = h >= 0
    table = iv.build_lines(h[keep], dom.from_m[keep], dom.to_m[keep])
    codes, names = _line_codes(dom, d)
    targets = iv.Lines(np.arange(len(holes), dtype=np.int64), np.asarray(starts, dtype=np.int64),
                       np.ascontiguousarray(frm, dtype=float), np.ascontiguousarray(to, dtype=float))  # fmt: skip
    return iv.mode_by_length(table, table.take(codes[keep]), targets), names


def src_codes(spec: PrepSpec, src: source.Lines, dom: source.Lines | None) -> tuple[np.ndarray | None, list[str]]:
    d = spec.domain
    if d is None:
        return None, []
    if dom is None:
        return _line_codes(src, d)
    return codes_on(src.holes, src.starts, src.from_m, src.to_m, dom, d)


def middles(st: Any, hole_db_ids: Any, starts: Any, frm: Any, to: Any, *, threads: int = 1) -> np.ndarray:
    """X, Y, Z [n, 3] of the middles of rows grouped by hole (`hole_db_ids` — dh_holes.id per group); NaN — no
    trajectory."""
    from . import coords
    from . import intervals as iv
    from .compose import _dense

    ids = np.asarray(hole_db_ids, dtype=np.int64)
    lines = iv.Lines(np.arange(len(ids), dtype=np.int64), np.asarray(starts, dtype=np.int64),
                     np.asarray(frm, dtype=float), np.asarray(to, dtype=float))  # fmt: skip
    return coords.midpoints(lines, st.traj, _dense(st, ids), threads=threads)


def compute(
    op: str,
    kernel: Any,
    fields: Sequence[str],
    units: dict[str, str | None],
    numbers: dict[str, np.ndarray],
    lengths: np.ndarray,
    *,
    codes: np.ndarray | None = None,
    names: Sequence[str] = (),
    xyz: np.ndarray | None = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> Made:
    """The columns of `op` for rows given as arrays (any order of rows: the domain codes and the points are
    already per row)."""
    from bf_core.drillholes.store import ColumnSpec

    from .length import summary

    try:
        if op == "topcut":
            from .topcut import topcut

            f = fields[0]
            t = topcut(numbers[f], lengths, kernel, codes=codes, names=names, field=f, tick=tick)
            cut, flag = t.report["columns"]
            cols = [(ColumnSpec(cut, "number", units.get(f)), t.cut), (ColumnSpec(flag, "number"), t.flag)]
            return Made(cols, clean({**t.report, "thresholds": t.thresholds}), list(t.report["notes"]))
        if op == "equivalent":
            from .equivalent import equivalent

            grades = [numbers[f] for f in fields]
            e = equivalent(grades, kernel, tick=tick)
            before = np.column_stack(grades) if grades else np.zeros((len(lengths), 0))
            after = np.column_stack([*grades, e.values])
            rep = {**e.report, "summary": {"before": summary(lengths, before, list(fields)),
                                           "after": summary(lengths, after, [*fields, kernel.name])}}  # fmt: skip
            miss = e.report["n_missing"]
            notes = [f"Проб без эквивалента (нет содержания): {miss}."] if miss else []
            return Made([(ColumnSpec(kernel.name, "number", kernel.base.unit), e.values)], clean(rep), notes)
        from bf_core.stats.declus3d import declus_weights

        assert xyz is not None
        d = declus_weights(xyz[:, 0], xyz[:, 1], xyz[:, 2], numbers[fields[0]], kernel, threads=threads, tick=tick)
        notes = list(d.report.get("notes") or [])
        if d.report.get("n_missing"):
            notes.insert(0, f"Проб без координат или значения: {d.report['n_missing']} — вес пуст.")
        return Made([(ColumnSpec(DECL_W, "number"), d.weights)], clean({**d.report, "field": fields[0]}), notes)
    except ValueError as exc:  # CompositeError, StatsError → 422
        raise cj.as_api(exc) from None


def read_prep(
    engine: sa.Engine, spec: PrepSpec, *, cache_dir: Any = None, holes: Sequence[int] | None = None,
    tick: Callable[[float | None, str | None], None] | None = None,
) -> tuple[source.Lines, source.Lines | None]:  # fmt: skip
    from . import source

    src = source.read(engine, spec.set_id, spec.table_id, spec.read_fields(), cache_dir=cache_dir, holes=holes,
                      tick=tick)  # fmt: skip
    d = spec.other_domain()
    dom = None if d is None else source.read(engine, spec.set_id, d.table_id, [d.field], cache_dir=cache_dir,
                                             holes=holes, tick=tick)  # fmt: skip
    return src, dom


def compute_lines(
    spec: PrepSpec, src: source.Lines, dom: source.Lines | None, st: Any, *, threads: int = 1,
    tick: Tick | None = None, kernel: Any = None,
) -> Made:  # fmt: skip
    """`compute` on the lines of `source.read` (the job, the preview: the same function on the same reading)."""
    codes, names = src_codes(spec, src, dom) if spec.op == "topcut" else (None, [])
    xyz = None
    if spec.op == "declus":
        xyz = middles(st, src.hole_db_ids, src.starts, src.from_m, src.to_m, threads=threads)
    made = compute(spec.op, spec.kernel if kernel is None else kernel, spec.fields, spec.units, src.numbers,
                   src.to_m - src.from_m, codes=codes, names=names, xyz=xyz, threads=threads, tick=tick)  # fmt: skip
    if src.skipped:
        made.journal.insert(0, skipped_note(spec.table_name, src.skipped))
    return made


def skipped_note(table: str, n: int) -> str:
    return f"Таблица «{table}»: строк без «от» или «до» — {n}, поля пусты."


# --- the job -----------------------------------------------------------------------------------------------


def run_prep(
    engine: sa.Engine, folder: Any, params: dict[str, Any], op: str, *, job_id: int,
    tick: Callable[[float | None, str | None], None],
) -> dict[str, Any]:  # fmt: skip
    """The body of `composite.topcut`, `composite.equivalent`, `composite.declus` (the handler and the tests)."""
    from . import results, sink, source, strips

    t0 = time.perf_counter()
    body, rebuild = cj._body_of(params)
    with dialect.read_tx(engine) as conn:
        spec = parse_prep(conn, body, op)
        check_columns(conn, spec)
        inputs = results.make_inputs(conn, spec.set_id, spec.tables(), trajectories=op == "declus")
    tick(0.02, "Чтение проб")
    src, dom = read_prep(engine, spec, cache_dir=folder / "cache" / "tables", tick=tick)
    st = stations = None
    if op == "declus":
        st = source.trajectories(SimpleNamespace(engine=engine, uid=f"job:{folder}"), spec.set_id, tick=tick)
        stations = source.stations_bytes(st, src.hole_db_ids.tolist(), tick=tick)
    algo = cj.ALGO[op]
    sha = source.input_hash([src, *([dom] if dom else [])], spec.hash_params(), stations=stations, algo=algo,
                            tick=tick)  # fmt: skip
    fresh = cj._fresh(engine, rebuild, spec.set_id, sha, algo, inputs)
    if fresh is not None:
        return fresh
    tick(0.1, STEP[op])
    made = compute_lines(spec, src, dom, st, threads=strips.default_threads(),
                         tick=lambda f: tick(0.1 + 0.6 * f, None))  # fmt: skip
    tick(0.75, "Запись полей")
    report = {**made.report, "journal": made.journal, "versions": cj._versions(), "edits": [],
              "seconds": round(time.perf_counter() - t0, 3)}  # fmt: skip
    # no tick from here on: the final transaction writes the columns and the record, or nothing
    cols = sink.ColumnsOut(spec.table_id, src.row_ids, made.columns)
    res = sink.commit(engine, sink.Commit(set_id=spec.set_id, op=op, name=spec.name, params=spec.body,
                                          inputs=inputs, sha256=sha, algo=algo, report=report, job_id=job_id,
                                          columns=[cols]))  # fmt: skip
    set_id = spec.set_id
    events: list[list[Any]] = [["drillholes.changed", {"set_id": set_id, "table": c["table"], "rev": c["rev"]}]
                               for c in res["changed"]]  # fmt: skip
    events.append(["composites.changed", {"set_id": set_id}])
    return {"set_id": set_id, "result_id": res["result_id"], "table_id": spec.table_id, "name": res["name"],
            "rows": src.rows, "columns": spec.columns, "replaced": [], "fresh": False, "journal": made.journal,
            "events": events}  # fmt: skip


def _prep_job(ctx: JobContext, op: str) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        out = run_prep(ctx.engine(), ctx.project_folder, ctx.params, op, job_id=ctx.id, tick=ctx.tick)
    except ApiError as exc:
        raise cj._user(exc) from None
    cj._log_journal(ctx, out.get("journal") or [])
    return out


@handler("composite.topcut", "Урезка", queue="heavy", estimate_memory=cj.memory_mb)
def topcut_job(ctx: JobContext) -> dict[str, Any]:
    return _prep_job(ctx, "topcut")


@handler("composite.equivalent", "Эквивалент металла", queue="heavy", estimate_memory=cj.memory_mb)
def equivalent_job(ctx: JobContext) -> dict[str, Any]:
    return _prep_job(ctx, "equivalent")


@handler("composite.declus", "Декластеризация", queue="heavy", estimate_memory=cj.memory_mb)
def declus_job(ctx: JobContext) -> dict[str, Any]:
    return _prep_job(ctx, "declus")


# --- repeated on a new table (решение 18) --------------------------------------------------------------------


def _failed(rec: dict[str, Any], why: str) -> ApiError:
    return ApiError(
        422,
        "dependent_failed",
        f"«{rec.get('name')}» нельзя повторить на новой таблице: {why}.",
        "Удалите этот результат или верните поле в таблицу и перестройте её снова.",
    )


def _column(out: sink.OutTable, name: str, rec: dict[str, Any]) -> tuple[ColumnSpec, np.ndarray]:
    for spec, vals in zip(out.columns, out.values, strict=True):
        if spec.name.casefold() == str(name).casefold():
            return spec, np.asarray(vals)
    raise _failed(rec, f"нет поля «{name}»")


def _again_domain(
    raw: Any, rec: dict[str, Any], out: sink.OutTable, ctx: cj.AgainContext, order: np.ndarray, grouped: Any
) -> tuple[np.ndarray | None, list[str], list[str], Any]:
    """(codes in the sorted order, names, fields of the new table read, the domain for the repeated params)."""
    from . import source

    if raw is None or raw == "" or raw == {}:
        return None, [], [], raw
    field = raw if isinstance(raw, str) else str(raw.get("field") or "")
    tables = (rec.get("inputs") or {}).get("tables") or []
    if len(tables) < 2:  # a field of the same table: now of the new one
        spec, vals = _column(out, field, rec)
        codes, names = _value_codes(vals[order], spec.kind)
        given = raw if isinstance(raw, str) else {**raw, "table": str(ctx.new_table or raw.get("table"))}
        return codes, names, [spec.name], given
    with dialect.read_tx(ctx.engine) as conn:
        tid = conn.execute(
            sa.select(dh_tables.c.id).where(
                dh_tables.c.set_id == ctx.set_id,
                dh_tables.c.state == "ready",
                dh_tables.c.name_key == tables[1].get("key"),
            )  # fmt: skip
        ).scalar()
        if tid is None:
            raise _failed(rec, f"удалена таблица домена «{tables[1].get('name')}»")
        from .source import _fields

        f = next(iter(_fields(conn, int(tid), [field]).values()))
    dom = source.read(ctx.engine, ctx.set_id, int(tid), [f.name])
    d = cj.DomainSpec(int(tid), str(tables[1].get("name")), f.name, f.kind)
    holes, starts, frm, to = grouped
    codes, names = codes_on(holes, starts, frm, to, dom, d)
    return codes, names, [], {**raw, "table": str(tid)}


def again(op: str) -> Callable[[sink.OutTable, dict[str, Any]], cj.Again]:
    """The repeat of `op` by the params of its record on the arrays of a new result table."""

    def fn(out: sink.OutTable, rec: dict[str, Any]) -> cj.Again:
        from . import source

        ctx = cj.AGAIN.get()
        if ctx is None:
            raise RuntimeError("dependents are repeated by jobs._again only")
        params = dict(rec.get("params") or {})
        t0 = time.perf_counter()
        names_of = np.array(list(out.holes), dtype=object)
        empty = (np.zeros(0, dtype=object), np.zeros(0, dtype=np.int64))
        uniq, inv = np.unique(names_of, return_inverse=True) if len(names_of) else empty
        frm_all, to_all = np.asarray(out.from_m, dtype=float), np.asarray(out.to_m, dtype=float)
        order = np.lexsort((to_all, frm_all, inv)) if len(names_of) else np.zeros(0, dtype=np.int64)
        starts = np.concatenate(([0], np.cumsum(np.bincount(inv, minlength=len(uniq))))).astype(np.int64)
        holes = [str(h) for h in uniq.tolist()]
        frm, to = frm_all[order], to_all[order]
        units = {s.name: s.unit for s in out.columns}
        try:
            if op == "equivalent":
                from .equivalent import EquivalentParams

                kernel: Any = EquivalentParams.of(params, units)
                wanted = kernel.fields
            elif op == "topcut":
                from .topcut import TopcutParams

                kernel, wanted = TopcutParams.of(params), [str(params.get("field") or "")]
            else:
                from bf_core.stats.declus3d import DeclusParams

                kernel, wanted = DeclusParams.of(params), [str(params.get("field") or "")]
        except ValueError as exc:
            raise cj.as_api(exc) from None
        cols = [_column(out, w, rec) for w in wanted]
        fields = [s.name for s, _v in cols]
        numbers = {s.name: np.asarray(v, dtype=float)[order] for s, v in cols}
        codes: np.ndarray | None = None
        names: list[str] = []
        more: list[str] = []
        if op == "topcut":
            codes, names, more, given = _again_domain(params.get("domain"), rec, out, ctx, order,
                                                      (holes, starts, frm, to))  # fmt: skip
            if given is not None:
                params["domain"] = given
        xyz = None
        if op == "declus":
            with dialect.read_tx(ctx.engine) as conn:
                q = sa.select(dh_holes.c.name, dh_holes.c.id).where(dh_holes.c.set_id == ctx.set_id)
                ids = {str(r.name): int(r.id) for r in conn.execute(q)}
            st = source.trajectories(SimpleNamespace(engine=ctx.engine, uid=f"again:{ctx.engine.url}"), ctx.set_id)
            xyz = middles(st, [ids.get(h, -1) for h in holes], starts, frm, to)
        made = compute(op, kernel, fields, {f: units.get(f) for f in fields}, numbers, to - frm, codes=codes,
                       names=names, xyz=xyz)  # fmt: skip
        back = np.empty_like(order)
        back[order] = np.arange(len(order))
        columns = [(s, np.asarray(v)[back]) for s, v in made.columns]
        if ctx.new_table is not None:
            params["table"] = str(ctx.new_table)
        report = {**made.report, "journal": made.journal, "seconds": round(time.perf_counter() - t0, 3)}
        return cj.Again(columns, list(dict.fromkeys([*fields, *more])), params=params, report=report)

    return fn


for _op in OPS:
    cj.register_dependent(_op)(again(_op))
