"""«Модель на скважины» (ТЗ 7.35 Д-34 ч. 1; ADR 0026 решения 25, 28; e19-api.md §8.4): the bodies of an implicit model
(or closed wireframes) cut the holes of a set into an interval table `FROM, TO, CODE`; a number field of a block
model at the middle of the intervals of a table. The job `drillholes.from_model` (heavy).

    st = on_holes(traj, dense, bodies, ids=None, threads=…, tick=…)      # coding.Stretches of the WHOLE holes
    samples, used = whole_samples(traj, dense, ids)                      # one interval [0, length] a hole
    rows = fill_rows(st, samples.holes, code_of)                         # stretches + the gaps between them
    v = at_midpoints(view, field, lines, traj, dense, threads=…, tick=…) # float64 [n], NaN — outside the model

Bodies. `composites.coding.body_stretches` as it is (the tree of boxes, the entries and exits by the side of the
triangle, the collar check, `wireframe_open` / `wireframe_orientation` with a hint): the hole is one sample from 0 to
the end of its path, so a stretch that reaches the end of the path or starts at the collar is told as such. Bodies
that overlap — the first of the list (the priority). A hole without a path (no collar coordinates, no stations) gets
no row and is counted (`no_trajectory`); a gap between the bodies — a row with an empty CODE (None, never a code of
0); two touching stretches of one code (the wings of a group) are one row. The rows cover [0, length] of every hole:
the first FROM is 0, the last TO the end of the path, bit for bit; a boundary is a crossing of the course itself
(`geo/intersect.py`, 1e−8 m).

Field. The point of the path at (FROM + TO) / 2 (`composites.coords.midpoints`: the path of 7.11, a hole without a
trajectory — NaN) is looked for in the rows of the model (`blocks.geometry.locate_rows`: sub-blocks, the turn, a gap
of a sparse model → −1); the value of a missing block or of an empty cell is NaN — an empty cell of the table.

The table is written aside (state `building`) and swapped in with the rows of the graph (ADR 0025, решение 25):
`lineage.record(dh_table, [model | wireframes | bm_field + dh_table, dh_set])`, in one transaction; a cancel or a
failure leaves nothing. The table is new: a taken name — 409 `table_exists`, nothing is replaced.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.registry import handler

if TYPE_CHECKING:
    from bf_core.composites import coding, sink
    from bf_core.composites import intervals as iv
    from bf_core.drillholes.desurvey import Trajectories
    from bf_core.jobs.context import JobContext

KIND = "drillholes.from_model"
ALGO = "from-model-1"
DEPTH_EPS = 1e-6  # the shortest stretch (`composites.intervals.DEPTH_EPS`)
BLOCK_POINTS = 1 << 20  # points looked up in the rows of a block model at a time
TRIANGLE_BYTES = 200  # a wireframe in memory, per triangle (`composites.jobs_coding.TRIANGLE_BYTES`)
Tick = Callable[[float | None, str | None], None]
Part = Callable[[float], None]


# --- the stretches of whole holes -------------------------------------------------------------------------------


def whole_samples(traj: Trajectories, dense: Any = None, ids: Any = None) -> tuple[iv.Lines, np.ndarray]:
    """One interval [0, length of the path] for every hole of `dense` (indices into `traj`, ascending; None — all)
    that has a path longer than `DEPTH_EPS`: `(lines, used)`, `used` — their indices into `traj`, `lines.holes` —
    their labels (`ids`, parallel to `dense`; default — the index into `traj`)."""
    from bf_core.composites import intervals as iv

    off = np.asarray(traj.offsets, dtype=np.int64)
    index = np.arange(traj.n_holes, dtype=np.int64) if dense is None else np.ascontiguousarray(dense, dtype=np.int64)
    label = index if ids is None else np.ascontiguousarray(ids, dtype=np.int64)
    if label.shape != index.shape:
        raise ValueError("ids must be parallel to dense")
    inside = (index >= 0) & (index < traj.n_holes)
    at = np.where(inside, index, 0)
    built = inside & (off[at + 1] > off[at]) if traj.n_holes else inside
    length = np.zeros(index.shape[0])
    length[built] = np.asarray(traj.lengths())[index[built]]
    keep = built & (length > DEPTH_EPS)
    used, labels, ends = index[keep], label[keep], length[keep]
    order = np.argsort(labels, kind="stable")
    used, labels, ends = used[order], labels[order], ends[order]
    n = labels.shape[0]
    lines = iv.Lines(labels, np.arange(n + 1, dtype=np.int64), np.zeros(n), ends)
    return lines, used


def on_holes(
    traj: Trajectories,
    dense: Any,
    bodies: Any,
    *,
    ids: Any = None,
    n_bodies: int | None = None,
    threads: int = 1,
    tick: Part | None = None,
) -> coding.Stretches:
    """The stretches of the holes `dense` (module docstring) inside the `bodies` (a list or a generator of
    `coding.Body`, by priority): `coding.body_stretches` of the whole holes. `st.lines.holes` — the labels `ids`
    of the holes that have a stretch, `st.code` — the body of every stretch, `st.ends` — the end of the path of
    every hole processed (`whole_samples` tells which). CompositeError `wireframe_open`, `wireframe_orientation`."""
    from bf_core.composites import coding

    samples, used = whole_samples(traj, dense, ids)
    return coding.body_stretches(samples, traj, used, bodies, n_bodies=n_bodies, threads=threads, tick=tick)


@dataclass(frozen=True)
class Rows:
    """The rows of the table: `hole` — the index of the hole in `labels` (`whole_samples(...)[0].holes`), `code` —
    the index into the list of the codes, −1 — a gap between the bodies."""

    hole: np.ndarray
    frm: np.ndarray
    to: np.ndarray
    code: np.ndarray


def fill_rows(st: coding.Stretches, labels: np.ndarray, ends: np.ndarray, code_of: Any) -> Rows:
    """The stretches `st` and the gaps between them, collar to end of path, for every hole of `labels` (ascending);
    `ends` — the end of the path of each; `code_of` — the code of every body (int, the place in the list of codes:
    bodies of one code merge where they touch). A gap not longer than `DEPTH_EPS` is not a row."""
    lines = st.lines
    m = labels.shape[0]
    pos = np.searchsorted(labels, lines.holes)
    p = np.repeat(pos, np.diff(lines.starts)).astype(np.int64)
    f, t = lines.frm, lines.to
    c = np.asarray(code_of, dtype=np.int32)[st.code] if f.shape[0] else np.zeros(0, dtype=np.int32)
    n = f.shape[0]
    first = np.ones(n, dtype=np.bool_)
    last = np.ones(n, dtype=np.bool_)
    if n > 1:
        first[1:] = p[1:] != p[:-1]
        last[:-1] = p[:-1] != p[1:]
    before = np.zeros(n)
    if n > 1:
        before[1:] = t[:-1]
    before[first] = 0.0
    head = (f - before) > DEPTH_EPS  # a gap above the stretch (from the collar, or from the stretch above)
    tail = last & ((ends[p] - t) > DEPTH_EPS) if n else np.zeros(0, dtype=np.bool_)
    none = np.ones(m, dtype=np.bool_)
    none[pos] = False
    free = np.flatnonzero(none)
    parts = [(p, f, t, c), (p[head], before[head], f[head], np.full(int(head.sum()), -1, dtype=np.int32)),
             (p[tail], t[tail], ends[p][tail], np.full(int(tail.sum()), -1, dtype=np.int32)),
             (free, np.zeros(free.shape[0]), ends[free], np.full(free.shape[0], -1, dtype=np.int32))]  # fmt: skip
    hole, frm, to, code = (np.concatenate(col) for col in zip(*parts, strict=True))
    order = np.lexsort((frm, hole))
    hole, frm, to, code = hole[order], frm[order], to[order], code[order]
    if hole.shape[0] > 1:  # one code, touching: one row
        same = (hole[1:] == hole[:-1]) & (code[1:] == code[:-1]) & (code[1:] >= 0)
        same &= (frm[1:] - to[:-1]) <= DEPTH_EPS
        new = np.concatenate(([True], ~same))
        at = np.flatnonzero(new)
        end = np.concatenate((at[1:] - 1, [hole.shape[0] - 1]))
        hole, frm, to, code = hole[at], frm[at], to[end], code[at]
    return Rows(hole, frm, to, code)


# --- a field of a block model at the middle of the intervals -------------------------------------------------------


def at_midpoints(
    view: Any, field: str, lines: iv.Lines, traj: Trajectories, dense: Any, *, threads: int = 1,
    tick: Part | None = None,
) -> np.ndarray:  # fmt: skip
    """The number field `field` of the block model `view` (`blocks.model.Model`) at the point of the path at
    (FROM + TO) / 2 of every row of `lines`: float64 [n]. NaN — a hole without a trajectory, a point outside the grid
    or in a gap of a sparse model, an empty cell: never 0. `dense` — per hole of the lines, its index in `traj`."""
    from bf_core.blocks import geometry
    from bf_core.composites import coords

    n = lines.n
    out = np.full(n, np.nan)
    if n == 0:
        return out
    half: Part | None = None if tick is None else (lambda f: tick(0.5 * f))
    xyz = coords.midpoints(lines, traj, dense, threads=threads, tick=half)
    ok = np.flatnonzero(np.isfinite(xyz).all(axis=1))
    if ok.shape[0] == 0:
        return out
    p = view.p()
    sub = view.subs()
    s, w = (None, None) if sub is None else sub
    values = np.asarray(view.columns([field])[view.field(field)["name"]], dtype=np.float64)
    for a in range(0, ok.shape[0], BLOCK_POINTS):
        at = ok[a : a + BLOCK_POINTS]
        row = geometry.locate_rows(p, s, w, view.grid, xyz[at])
        hit = row >= 0
        out[at[hit]] = values[row[hit]]
        if tick is not None:
            tick(0.5 + 0.5 * min(1.0, (a + BLOCK_POINTS) / ok.shape[0]))
    return out


# --- the table -----------------------------------------------------------------------------------------------------


@dataclass
class Made:
    out: sink.OutTable
    report: dict[str, Any]
    journal: list[str]
    holes: int
    no_trajectory: int


def _part(tick: Tick, lo: float, hi: float) -> Part:
    return lambda f: tick(lo + (hi - lo) * f, None)


def _names_at(names: Sequence[str], at: np.ndarray) -> Any:
    return np.array(list(names), dtype=object)[at] if at.shape[0] else np.zeros(0, dtype=object)


def _codes_report(codes: list[str], rows: Rows) -> list[dict[str, Any]]:
    length = rows.to - rows.frm
    return [{"code": name, "rows": int((rows.code == k).sum()), "length": float(length[rows.code == k].sum())}
            for k, name in enumerate(codes)]  # fmt: skip


def compute_bodies(spec: Any, st: Any, bodies: Any, *, threads: int, tick: Tick) -> Made:
    """The table `FROM, TO, CODE` of the holes of the set `st` (`drillholes.trajectory.SetTrajectories`) cut by
    `bodies` (a generator of `coding.Body`, by the priority of `spec.codes`)."""
    from bf_core.composites import coding, sink
    from bf_core.composites import jobs as cj
    from bf_core.drillholes.store import ColumnSpec

    codes = list(dict.fromkeys(spec.codes))
    code_of = [codes.index(c) for c in spec.codes]
    samples, used = whole_samples(st.traj, None, st.hole_ids)
    try:
        got = coding.body_stretches(samples, st.traj, used, bodies, n_bodies=len(spec.codes), threads=threads,
                                    tick=_part(tick, 0.05, 0.6))  # fmt: skip
    except ValueError as exc:  # CompositeError `wireframe_open`, `wireframe_orientation` → 422
        raise cj.as_api(exc) from None
    rows = fill_rows(got, samples.holes, samples.to, code_of)
    names = [str(st.names[int(i)]) for i in used]
    text = np.array([*codes, None], dtype=object)[rows.code]  # −1 → the last one: None
    out = sink.OutTable(spec.name, _names_at(names, rows.hole), rows.frm, rows.to, [ColumnSpec("CODE", "text")],
                        [text], hole_index=(names, rows.hole))  # fmt: skip
    lost = int(len(st.hole_ids) - used.shape[0])
    journal = [*spec.journal, *got.journal]
    if lost:
        journal.append(f"Скважин без траектории или нулевой длины — {lost}: строк у них нет. Проверьте устья.")
    per = [{**b, "code": spec.codes[k]} for k, b in enumerate(got.report["bodies"])]
    report = {"mode": "bodies", "model": spec.model, "bodies": per, "codes": _codes_report(codes, rows),
              "holes": int(used.shape[0]), "no_trajectory": lost, "stretches": int(got.lines.n),
              "length_inside": float(got.lines.lengths().sum()), "length": float(samples.to.sum()),
              "rows": int(rows.hole.shape[0])}  # fmt: skip
    return Made(out, report, journal, int(used.shape[0]), lost)


def compute_field(spec: Any, st: Any, src: Any, view: Any, *, threads: int, tick: Tick) -> Made:
    """The table `FROM, TO, <field>`: the intervals of the table `src` and the field of the block model at their
    middle (`at_midpoints`)."""
    from bf_core.composites import coords, sink
    from bf_core.composites import intervals as iv
    from bf_core.drillholes.store import ColumnSpec

    lines = iv.build_lines(src.hole_of().astype(np.int64), src.from_m, src.to_m)
    tix = coords.hole_index(st.hole_ids, src.hole_db_ids)
    tix[np.isin(tix, np.asarray(st.traj.skipped, dtype=np.int64))] = -1
    dense = tix[lines.holes]
    block = spec.block
    value = at_midpoints(view, block["field"], lines, st.traj, dense, threads=threads, tick=_part(tick, 0.1, 0.6))
    at = lines.hole_ids().astype(np.int64)  # the index into `src.holes` of every row
    out = sink.OutTable(spec.name, _names_at(src.holes, at), lines.frm, lines.to,
                        [ColumnSpec(block["field"], "number", block.get("unit"))], [value],
                        hole_index=(src.holes, at))  # fmt: skip
    journal = [*spec.journal, *lines.notes(spec.table["name"])]
    lost = int((dense < 0).sum())
    if lost:
        journal.append(f"Скважин без траектории — {lost}: значения в их интервалах пусты. Проверьте устья.")
    empty = int(np.isnan(value).sum())
    report = {"mode": "field", "block_model": {"id": block["id"], "name": block["name"], "field": block["field"]},
              "table": spec.table, "rows": int(lines.n), "empty": empty, "no_trajectory": lost,
              "holes": int(lines.n_holes) - lost}  # fmt: skip
    return Made(out, report, journal, int(lines.n_holes) - lost, lost)


# --- the graph, the job --------------------------------------------------------------------------------------------


def graph_inputs(spec: Any) -> list[Any]:
    """The nodes the table is built from (ADR 0026 решение 25): the model, or its wireframes, or the field and the
    table of the block model; and the set."""
    from bf_core import lineage

    if spec.mode == "field":
        nodes = [lineage.Node("bm_field", spec.block["id"], str(spec.block["field"]).casefold()),
                 lineage.Node("dh_table", spec.table["id"])]  # fmt: skip
    elif spec.model is not None:
        nodes = [lineage.Node("object:implicit_model", spec.model["id"])]
    else:
        nodes = [lineage.Node("wireframe", i) for i in spec.wireframe_ids]
    return [*nodes, lineage.Node("dh_set", spec.set_id)]


def memory_mb(params: dict[str, Any]) -> int:
    """256 MB, the largest wireframe (one is held at a time), the holes (the paths and the rows)."""
    return 256 + int(int(params.get("triangles") or 0) * TRIANGLE_BYTES / 2**20) + int(params.get("holes") or 0) // 100


def _store(engine: sa.Engine, spec: Any, made: Made, *, job_id: int, t0: float, tick: Tick) -> dict[str, Any]:
    """The table aside, then — in one transaction — the swap and the rows of the graph. A cancel or a failure before
    the transaction ends leaves nothing; no tick after it (a cancel would report a committed table as cancelled)."""
    from bf_core import lineage
    from bf_core.composites import sink
    from bf_core.db.schema import dh_tables
    from bf_core.drillholes import store

    from . import from_model_spec as fs

    with sink.guarded(engine, job_id):
        tid = sink.build_table(engine, spec.set_id, made.out, job_id=job_id,
                               tick=lambda f, m: tick(0.6 + 0.35 * (f or 0.0), m or "Запись таблицы"))  # fmt: skip
        with dialect.write_tx(engine) as conn:
            fs.check_free(conn, spec.set_id, spec.name)  # taken while the job ran: nothing is replaced
            store.swap_in(conn, spec.set_id, tid)
            lineage.record(conn, lineage.Node("dh_table", tid), graph_inputs(spec))
            rev = int(conn.execute(sa.select(dh_tables.c.rev).where(dh_tables.c.id == tid)).scalar_one())
    report = {**made.report, "journal": made.journal, "seconds": round(time.perf_counter() - t0, 3)}
    return {"set_id": spec.set_id, "table_id": tid, "name": spec.name, "rows": int(made.out.from_m.shape[0]),
            "holes": made.holes, "no_trajectory": made.no_trajectory, "report": report, "journal": made.journal,
            "events": [["drillholes.changed", {"set_id": spec.set_id, "table": str(tid), "rev": rev}]]}  # fmt: skip


def run(engine: sa.Engine, folder: Any, params: dict[str, Any], *, job_id: int, tick: Tick) -> dict[str, Any]:
    """The body of `drillholes.from_model` (the handler and the tests in the process)."""
    from bf_core.composites import jobs_coding as jc
    from bf_core.composites import source, strips
    from bf_core.storage.blobs import BlobStore

    from . import from_model_spec as fs

    t0 = time.perf_counter()
    store = BlobStore(folder)
    spec = fs.lookup(engine, store, params.get("body"))
    tick(0.02, "Траектории скважин")
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    st = source.trajectories(project, spec.set_id, tick=tick)
    threads = strips.default_threads()
    if spec.mode == "bodies":
        with dialect.read_tx(engine) as conn:
            wf = jc.parse_wireframes(conn, spec.wireframe_ids)
        tick(0.05, "Пересечения с телами")
        made = compute_bodies(spec, st, jc.bodies(project, wf, keep=False), threads=threads, tick=tick)
    else:
        from bf_core.blocks.model import Model

        assert spec.block is not None and spec.table is not None
        with dialect.read_tx(engine) as conn:
            view = Model.open(conn, store, spec.block["id"])
        src = source.read(engine, spec.set_id, spec.table["id"], [], cache_dir=folder / "cache" / "tables", tick=tick)
        tick(0.1, "Значения поля в серединах интервалов")
        made = compute_field(spec, st, src, view, threads=threads, tick=tick)
    return _store(engine, spec, made, job_id=job_id, t0=t0, tick=tick)


@handler(KIND, "Модель на скважины", queue="heavy", estimate_memory=memory_mb)
def from_model_job(ctx: JobContext) -> dict[str, Any]:
    from bf_core.jobs.context import JobError

    assert ctx.project_folder is not None
    try:
        out = run(ctx.engine(), ctx.project_folder, ctx.params, job_id=ctx.id, tick=ctx.tick)
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint, retry=False) from None
    for line in out.get("journal") or []:
        ctx.log(line)
    return out
