"""Coding of samples by wireframes — the request, the computation and the job `composite.code` (ТЗ 8.2.2, Т8.2-7;
ADR 0018 §6, решение 6: closed solids only, an open one is refused with a hint; an overlap goes to the first
body of the list). Kernel — `coding.py` (12.7).

    spec = parse_coding(conn, body)              # {set_id, table, wireframe_ids, split?, name?, replace?}
    made = compute_coding(spec, src, st, bodies(project, spec.wf), …)   # the job and the preview of a hole
    wf = parse_wireframes(conn, raw)             # also `domain {wireframe_ids}` of `composite.run` (jobs.py)

The measures of every wireframe (`wireframes.stats`) are checked before anything is read: an open one — 422
`wireframe_open` {param: wireframe_ids} in the request itself; a wireframe that is not in the project (or has no
triangles) — 422 `wireframe_not_found`. Shells wound different ways are found by the kernel in the job
(`wireframe_orientation`). The bodies are read one at a time (`bodies` is a generator; in the job a wireframe
leaves the cache of `wireframes/exact.py` as soon as it is read — only the one at hand is held).

The new table «‹Таблица› — коды» (ADR 0018 §2): every field of the source (units and roles kept; a field named
CODE or INSIDE_FRAC gets «_SRC»), CODE — the name of the body (empty — in none), INSIDE_FRAC — without the
division only (the share of the length inside). With the division the parts of a sample copy its values.
`report` — that of the kernel (split, rows_in, rows, cut, codes [{name, rows, length}], bodies, no_trajectory,
below_end…), `kernel {split, wireframes [{id, name}]}`, `summary {before, after}`, `journal`, `edits` ([]).
The hash covers the samples, the stations of their holes, the geometries and the names of the bodies in their
order and the division; `inputs.meshes` — the wireframes (a changed geometry makes the result «устарел»).

Domains by wireframes (`jobs.parse_domain`, `compose.compute`): the stretches inside the bodies
(`coding.body_stretches`) are the domains, DOMAIN — the name of the body. The geometry is read through the
project of `reading(project)` (the job, the preview routes).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.registry import handler

from . import jobs as cj

if TYPE_CHECKING:
    from bf_core.jobs.context import JobContext

    from . import coding, source
    from .compose import Made

KIND = "composite.code"
OWN = ("CODE", "INSIDE_FRAC")
MAX_IDS = 2000  # wireframes in one request (`wireframes/exact.py`)
TRIANGLE_BYTES = 200  # vertices, triangles and the tree of boxes of a wireframe in memory, per triangle
cj.ALGO.update(coding="coding-1")
Tick = Callable[[float], None]

#: (project, keep): the project the geometry is read from while a job or a preview computes domains by wireframes;
#: `keep` — leave the wireframes in the cache of `exact` (the API process), else drop each one once read (a job).
PROJECT: ContextVar[tuple[Any, bool] | None] = ContextVar("composites_coding_project", default=None)


@contextmanager
def reading(project: Any, *, keep: bool = True) -> Iterator[None]:
    token = PROJECT.set((project, keep))
    try:
        yield
    finally:
        PROJECT.reset(token)


# --- the wireframes ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Wireframes:
    """The bodies chosen, by priority: (viz.meshes.MeshState, stats) of every one."""

    targets: list[tuple[Any, Any]]

    @property
    def ids(self) -> list[int]:
        return [int(s.wireframe_id) for s, _st in self.targets]

    def names(self) -> list[str]:
        return [str(s.name) for s, _st in self.targets]

    def keys(self) -> list[str]:
        return [str(s.cache_key()) for s, _st in self.targets]

    def triangles(self) -> int:
        return max((int(s.triangles) for s, _st in self.targets), default=0)


def _ids(raw: Any) -> list[int]:
    items = raw.replace(";", ",").split(",") if isinstance(raw, str) else raw
    if isinstance(items, int) and not isinstance(items, bool):
        items = [items]
    bad = cj._bad("bad_param", "Каркасы: нужен список номеров каркасов.", "Отметьте замкнутые каркасы в списке.",
                  "wireframe_ids")  # fmt: skip
    if not isinstance(items, list):
        raise bad
    out: list[int] = []
    for x in items:
        if isinstance(x, int) and not isinstance(x, bool):
            out.append(x)
        elif isinstance(x, str) and x.strip().isascii() and x.strip().isdigit() and len(x.strip()) <= 18:
            out.append(int(x.strip()))
        elif not (isinstance(x, str) and not x.strip()):
            raise bad
    if not out:
        raise cj._bad("bad_param", "Не выбрано ни одного каркаса.", "Отметьте замкнутые каркасы в списке.",
                      "wireframe_ids")  # fmt: skip
    if len(out) > MAX_IDS:
        raise cj._bad("bad_param", f"Каркасов больше {MAX_IDS}.", "Выберите меньше каркасов.", "wireframe_ids")
    if len(set(out)) != len(out):
        raise cj._bad("bad_param", "Каркас выбран дважды.", "Отметьте каждый каркас один раз.", "wireframe_ids")
    return out


def parse_wireframes(conn: sa.Connection, raw: Any) -> Wireframes:
    """The wireframes of `wireframe_ids` in the order given (the priority), each a closed solid by its stored
    measures; 422 `wireframe_not_found`, `wireframe_open` {param: wireframe_ids}."""
    from bf_core.wireframes import exact

    from . import coding

    ids = _ids(raw)
    found = {int(s.wireframe_id): (s, stats) for s, stats in exact.set_targets(conn, ids)}
    missing = [i for i in ids if i not in found]
    if missing:
        raise cj._bad("wireframe_not_found", f"Каркас №{missing[0]} не найден в проекте или пуст.",
                      "Обновите список каркасов и отметьте их снова.", "wireframe_ids")  # fmt: skip
    targets = [found[i] for i in ids]
    try:
        for s, stats in targets:
            coding.check_stats(str(s.name), stats)
    except ValueError as exc:  # CompositeError `wireframe_open` → 422
        raise cj.as_api(exc) from None
    return Wireframes(targets)


def bodies(project: Any, wf: Wireframes, *, keep: bool = True) -> Iterator[coding.Body]:
    """The bodies one at a time (`coding.body_stretches` holds one): the tree of boxes of the cached geometry;
    closed without a stored orientation — measured here. `keep=False` — the wireframe leaves the cache once read."""
    from bf_core.wireframes import exact

    from . import coding

    for state, stats in wf.targets:
        coding.check_stats(str(state.name), stats)
        g = exact.geometry(project, state)
        if (stats or {}).get("orientation") in ("outward", "inward"):
            body = coding.Body(str(state.name), g.target(stats))
        else:
            body = coding.Body.of(str(state.name), g.xyz, g.tri, None, ordered=True)
        if not keep:
            exact.drop(g.key)
        del g
        yield body
        del body


def read_bodies(wf: Wireframes) -> Iterator[coding.Body]:
    """`bodies` through the project of `reading` (domains by wireframes)."""
    got = PROJECT.get()
    if got is None:
        raise RuntimeError("domains by wireframes are computed inside jobs_coding.reading(project)")
    return bodies(got[0], wf, keep=got[1])


def reading_project() -> Any:
    got = PROJECT.get()
    return None if got is None else got[0]


# --- the request -------------------------------------------------------------------------------------------


@dataclass
class CodingSpec:
    set_id: int
    table_id: int
    table_name: str
    fields: list[str]  # every field of the source (not the fixed and source columns)
    wf: Wireframes
    split: bool
    name: str
    replace: bool
    body: dict[str, Any]  # the request as given, without `replace` — `dh_results.params`
    rows: int = 0

    def tables(self) -> list[tuple[int, list[str]]]:
        return [(self.table_id, self.fields)]

    def hash_params(self) -> dict[str, Any]:
        return {"op": "coding", "fields": self.fields, "split": self.split, "wireframes": self.wf.keys(),
                "names": self.wf.names()}  # fmt: skip


def default_name(table: str) -> str:
    return f"{table} — коды"


def parse_coding(conn: sa.Connection, body: dict[str, Any]) -> CodingSpec:
    """The body of `POST /composites/coding` checked against the project; ApiError 404 / 422."""
    from .compose_tables import NAME_MAX, all_fields

    set_id = body.get("set_id")
    if not isinstance(set_id, int) or isinstance(set_id, bool):
        raise ApiError(404, "set_not_found", "Не задан набор скважин.", "Выберите набор скважин.")
    t = cj.resolve_table(conn, set_id, body.get("table"))
    wf = parse_wireframes(conn, body.get("wireframe_ids"))
    split = body.get("split", True)
    if split is None:
        split = True
    if not isinstance(split, bool):
        raise cj._bad("bad_param", "«Делить пробы на границе»: нужно «да» или «нет».", "Выберите да или нет.",
                      "split")  # fmt: skip
    name = body.get("name")
    name = name.strip() if isinstance(name, str) and name.strip() else default_name(str(t.name))
    if len(name) > NAME_MAX:
        raise cj._bad("bad_param", "Имя таблицы слишком длинное.", f"Не больше {NAME_MAX} знаков.", "name")
    if dialect.name_key(name) == dialect.name_key(t.name):
        raise cj._bad("name_is_source", "Имя результата совпадает с таблицей-источником.", "Задайте другое имя.",
                      "name")  # fmt: skip
    return CodingSpec(set_id=set_id, table_id=int(t.id), table_name=str(t.name), fields=all_fields(conn, int(t.id)),
                      wf=wf, split=split, name=name, replace=body.get("replace") is True,
                      body={k: v for k, v in body.items() if k != "replace"}, rows=int(t.row_count or 0))  # fmt: skip


def memory_mb(params: dict[str, Any]) -> int:
    """`jobs.memory_mb` and the largest wireframe (one is held at a time)."""
    return cj.memory_mb(params) + int(int(params.get("triangles") or 0) * TRIANGLE_BYTES / 2**20)


# --- the computation ---------------------------------------------------------------------------------------


def compute_coding(
    spec: CodingSpec,
    src: source.Lines,
    st: Any,
    bodies_in: Iterator[coding.Body],
    *,
    threads: int = 1,
    tick: Tick | None = None,
) -> Made:
    """The rows of «‹Таблица› — коды» for the lines `src` (the whole table, or one hole for the preview)."""
    from bf_core.drillholes.store import ColumnSpec

    from . import coding, sink
    from . import intervals as iv
    from .compose import Made, _dense, own_names
    from .compose_tables import _column, _summary
    from .jobs_prep import clean

    lines = iv.build_lines(src.hole_of().astype(np.int64), src.from_m, src.to_m)
    tix = _dense(st, src.hole_db_ids)
    try:
        c = coding.code_by_wireframes(lines, st.traj, tix[lines.holes], bodies_in, split=spec.split,
                                      n_bodies=len(spec.wf.targets), threads=threads, tick=tick)  # fmt: skip
    except ValueError as exc:  # CompositeError `wireframe_open`, `wireframe_orientation` → 422
        raise cj.as_api(exc) from None
    cl = c.lines
    labels = own_names(src.fields, OWN)
    cols, vals = [], []
    for name in src.fields:
        raw = src.numbers[name] if src.fields[name].kind == "number" else src.codes[name]
        col, v = _column(src, name, labels[name], cl.take(raw))
        cols.append(col)
        vals.append(v)
    cols.append(ColumnSpec("CODE", "text"))
    vals.append(c.text())
    if c.inside_frac is not None:
        cols.append(ColumnSpec("INSIDE_FRAC", "number"))
        vals.append(c.inside_frac)
    out = sink.OutTable(spec.name, [src.holes[i] for i in cl.hole_ids().tolist()], cl.frm, cl.to, cols, vals)
    journal = [*lines.notes(spec.table_name), *c.journal]
    if src.skipped:
        journal.insert(0, f"Таблица «{spec.table_name}»: строк без «от» или «до» — {src.skipped}, они не скопированы.")
    report = {
        **clean(c.report),
        "kernel": {"split": spec.split, "wireframes": [{"id": i, "name": n} for i, n in
                                                       zip(spec.wf.ids, spec.wf.names(), strict=True)]},
        "skipped": src.skipped,
        "summary": {"before": _summary(lines.lengths(), src, lines.take),
                    "after": _summary(cl.lengths(), src, cl.take)},
        "journal": journal,
        "edits": [],
    }  # fmt: skip
    return Made(out, report, journal)


# --- the job -----------------------------------------------------------------------------------------------


def run_coding(
    engine: sa.Engine, folder: Any, params: dict[str, Any], *, job_id: int,
    tick: Callable[[float | None, str | None], None],
) -> dict[str, Any]:  # fmt: skip
    """The body of `composite.code` (the handler and the tests in the process)."""
    from . import results, source, strips

    t0 = time.perf_counter()
    body, rebuild = cj._body_of(params)
    with dialect.read_tx(engine) as conn:
        spec = parse_coding(conn, body)
        inputs = results.make_inputs(conn, spec.set_id, spec.tables(), trajectories=True, meshes=spec.wf.ids)
    tick(0.02, "Чтение проб")
    src = source.read(engine, spec.set_id, spec.table_id, spec.fields, cache_dir=folder / "cache" / "tables",
                      tick=tick)  # fmt: skip
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    st = source.trajectories(project, spec.set_id, tick=tick)
    algo = cj.ALGO["coding"]
    stations = source.stations_bytes(st, src.hole_db_ids.tolist(), tick=tick)
    sha = source.input_hash([src], spec.hash_params(), stations=stations, meshes=spec.wf.keys(), algo=algo,
                            tick=tick)  # fmt: skip
    fresh = cj._fresh(engine, rebuild, spec.set_id, sha, algo, inputs)
    if fresh is not None:
        return fresh
    tick(0.1, "Кодирование каркасами")
    made = compute_coding(spec, src, st, bodies(project, spec.wf, keep=False), threads=strips.default_threads(),
                          tick=lambda f: tick(0.1 + 0.5 * f, None))  # fmt: skip
    return cj._store(engine, folder, spec, made, op="coding", inputs=inputs, sha=sha, algo=algo, job_id=job_id,
                     t0=t0, tick=tick)  # fmt: skip


@handler(KIND, "Кодирование каркасами", queue="heavy", estimate_memory=memory_mb)
def code_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        out = run_coding(ctx.engine(), ctx.project_folder, ctx.params, job_id=ctx.id, tick=ctx.tick)
    except ApiError as exc:
        raise cj._user(exc) from None
    cj._log_journal(ctx, out.get("journal") or [])
    return out
