"""The job `simulate.run` — a simulation into the blob of its realizations (подшаг 18.32; ТЗ 8.8.1; ADR 0024 §2, §9;
ADR 0025; docs/progress/e18-api.md §2, §9–§10).

    out = run_simulate(engine, folder, params, tick, threads=None, job_id=None, memory_mb=None)   # the body of the job

`params` — the body of `POST /simulate/run` checked by the route: `simulation_id` (a stored row) or `simulation` (a
body: the row is made at the end, in the transaction of the write), `expected_version` (of the row, optional); the
route may add `nodes` (the most targets) and `samples` (rows of the table) for `memory_mb`, and `author`.

What it does:
  * «Пробы» — the row against the project (404, 409 `version_conflict`, `source_lost`, 422), the samples
    (`estimate.samples.load`: the same selection, top cut, weights, domains and soft boundary as the estimation), the
    targets — the parents of the blocks of the domain that pass the restrictor (`targets.py`; a model with a variable
    step is 422 `variable_grid`, sub-blocks take the value of their parent, № 83);
  * «Нормальные оценки» → «Реализации k / N» — `sgs.simulate` in the frame of the grid: the samples, the angles of
    the variogram model (Д-38: in normal scores, `scale: y`) and of the search ellipsoid are turned by the rotation φ
    of the model (the ellipsoid and the model keep their angles relative to the grid axes: `ang1 + φ`);
  * «Запись» — one Parquet file `real` (`real.write`), and in one transaction the blob, `meta.last_run` + the inputs of
    the row (`store.commit_run`), the row itself for a body, `lineage.record` of the node `object:simulation`. A cancel,
    a failure and a stale version leave no file and no row changed.

The answer (`contract_answers.simulate_result`): {simulation_id, nodes, realizations, seconds, counts {degenerate,
free, on_sample, cond {min, mean, max}}, log [lines], stages {seconds}, events}.

The memory: `memory_mb(params)` is what the scheduler waits for: the realizations (f8, n_real·n), the samples of the
nodes (n·n_max·4), the buffers of a thread (≈ 17·n bytes); the pool of `sgs.simulate` is cut to the budget.
Heavy imports (numba, scipy, pyarrow) are inside the functions: the process of the API registers this module.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.geostat.estimate import store_base as sb
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore

from . import store as sto
from . import store_check as sc
from .spec import RunSpec, SimulationSpec, parse_run, parse_simulation

if TYPE_CHECKING:
    from bf_core.jobs.context import JobContext
    from bf_core.lineage.store import Node

KIND = "simulate.run"
TITLE = "Моделирование"
S_SAMPLES, S_NSCORE, S_REAL, S_WRITE = "Пробы", "Нормальные оценки", "Реализации", "Запись"
Tick = Callable[[float | None, str | None], None]
AT_LOAD, AT_SIM, AT_WRITE = 0.02, 0.10, 0.94  # the share of the progress a stage starts at


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


class _Clock:
    """Passes the ticks and counts the seconds of the stages (the message starts with the name of a stage)."""

    def __init__(self, tick: Tick) -> None:
        self.tick, self.t0 = tick, time.perf_counter()
        self.at, self.stage = self.t0, "samples"
        self.took: dict[str, float] = {}

    def __call__(self, progress: float | None = None, message: str | None = None) -> None:
        self.tick(progress, message)
        key = None
        if message is not None:
            key = ("write" if message.startswith(S_WRITE) else "realizations" if message.startswith(S_REAL)
                   else "scores" if message.startswith(S_NSCORE) else None)  # fmt: skip
        if key is not None and key != self.stage:
            now = time.perf_counter()
            self.took[self.stage] = self.took.get(self.stage, 0.0) + now - self.at
            self.at, self.stage = now, key

    def stages(self) -> dict[str, float]:
        now = time.perf_counter()
        took = {**self.took, self.stage: self.took.get(self.stage, 0.0) + now - self.at}
        return {**{k: round(v, 3) for k, v in took.items()}, "total": round(now - self.t0, 3)}


# --- the memory --------------------------------------------------------------------------------------------------

#: MB: the interpreter, the kernels, the model's reader; a sample of the row (arrays, the tree) — measured by 18.32
BASE_MB, SAMPLE_MB = 330.0, 0.0006


def node_bytes(n_real: int, n_max: int, threads: int) -> float:
    """Bytes per node: the realizations f8, the samples of the node (i4 × n_max), the centres, the cell, the index,
    the path / draws / flags of a thread (≈ 17 B), the f4 copy of a row group is bounded (not per node)."""
    return n_real * 8.0 + n_max * 4.0 + 24.0 + 8.0 + 4.0 + 2.0 + threads * 17.0 + 16.0


def memory_mb(params: dict[str, Any]) -> int:
    """What the scheduler waits for (ADR 0002 §4): the nodes (`nodes` of the route, 1 000 000 when it does not say),
    the realizations, the samples of the table."""
    from bf_core.composites import strips

    n = int(params.get("nodes") or 1_000_000)
    n_real = int(params.get("n_real") or 20)
    n_max = int(params.get("n_max") or 24)
    samples = int(params.get("samples") or 300_000)
    threads = int(params.get("threads") or strips.default_threads())
    return int(BASE_MB + samples * SAMPLE_MB + n * node_bytes(n_real, n_max, threads) / 2**20 + 60)


def _threads(want: int, n: int, n_real: int, n_max: int, budget_mb: float | None, samples: int) -> int:
    """The threads the budget holds (None — no budget): the realizations are shared, a thread adds ≈ 17·n bytes."""
    if budget_mb is None:
        return max(1, want)
    fixed = BASE_MB + samples * SAMPLE_MB + n * node_bytes(n_real, n_max, 0) / 2**20
    per = n * 17.0 / 2**20
    return max(1, min(want, int((float(budget_mb) - fixed) // per) if per > 0 else want))


# --- the frame of the grid -----------------------------------------------------------------------------------------


def grid_frame(spec: SimulationSpec, samples: Any, grid: Any) -> tuple[Any, Any, Any, Any]:
    """(model, ellipsoid, samples, steps) of the kernel in the axes of the grid (docstring of the module): the
    variogram model and the ellipsoid with `ang1 + φ`, the samples at `grid.to_local(xyz)` (the corner of the model is
    the origin; a rotation of 0 changes nothing but the origin), the parent size. `spec.search` must be resolved."""
    import dataclasses

    from bf_core.geostat.estimate.search import Ellipsoid
    from bf_core.geostat.variogram.model import Model, Structure, parse_model

    s = spec.search
    assert spec.variogram is not None and spec.variogram.snapshot is not None
    if s.angles is None or s.radii is None:
        raise ApiError(422, "search_unresolved", "Эллипсоид поиска не определён.",
                       "Выберите модель вариограммы или задайте радиусы поиска вручную.",
                       {"param": "search"})  # fmt: skip
    phi = float(grid.rotation)
    model = parse_model(spec.variogram.snapshot)
    a1, a2, a3 = s.angles
    factor = float(s.passes[0].factor)
    ell = Ellipsoid.of((a1 + phi, a2, a3), tuple(r * factor for r in s.radii))
    if phi != 0.0:
        shifted = tuple(Structure(st.type, st.c, st.a1, st.a2, st.a3, st.ang1 + phi, st.ang2, st.ang3)
                        for st in model.structures)  # fmt: skip
        model = Model(model.c0, shifted, model.common_axes)
    local = grid.to_local(samples.xyz)
    return model, ell, dataclasses.replace(samples, local=local), grid.regular()


def _sampler(spec: SimulationSpec, samples: Any, ell: Any) -> Any:
    """The `Searcher` of the samples of the nodes: the one pass of the row (its radii are in `ell` already)."""
    import dataclasses

    from bf_core.geostat.estimate.search import Searcher

    soft = 0.0 if spec.samples.soft is None else float(spec.samples.soft[1])
    return Searcher(samples, ell, (dataclasses.replace(spec.search.passes[0], factor=1.0),), soft)


# --- the answer ------------------------------------------------------------------------------------------------------


def _blocks(n: int) -> str:
    tail = n % 100
    if tail % 10 == 1 and tail != 11:
        return f"{n} узел"
    if 2 <= tail % 10 <= 4 and not 12 <= tail <= 14:
        return f"{n} узла"
    return f"{n} узлов"


def _log(spec: SimulationSpec, res: Any, n: int, notes: list[str]) -> list[str]:
    tot = res.counts.sum(axis=0)
    lines = [f"Смоделировано {_blocks(n)} × {spec.n_real} реализаций (зерно {spec.seed}).", *notes]
    if int(tot[1]):
        lines.append(f"Узлов без условных данных (пробы и смоделированные соседи не найдены): {int(tot[1])} "
                     "по всем реализациям — значение взято из N(0, 1) без условия.")  # fmt: skip
    if int(tot[2]):
        lines.append(f"Узлов в точках проб: {int(tot[2])} — взято значение пробы.")
    return lines


def _answer(spec: SimulationSpec, ident: int, res: Any, n: int, clock: _Clock, log: list[str]) -> dict[str, Any]:
    tot = res.counts.sum(axis=0)
    cond = res.cond
    return {"simulation_id": ident, "nodes": n, "realizations": spec.n_real, "seconds": round(float(res.seconds), 3),
            "counts": {"degenerate": int(tot[0]), "free": int(tot[1]), "on_sample": int(tot[2]),
                       "cond": {"min": int(cond.min()), "mean": round(float(cond.mean()), 3), "max": int(cond.max())}},
            "log": log, "stages": clock.stages(),
            "events": [["geostat.changed", {"kind": sto.KIND, "id": ident}]]}  # fmt: skip


# --- the run ----------------------------------------------------------------------------------------------------------


def plan_run(conn: sa.Connection, store: BlobStore,
             run: RunSpec) -> tuple[Any, SimulationSpec, Any, list[dict[str, Any]], Any]:  # fmt: skip
    """The row (or the body) checked against the project: (row | None, spec, model, inputs, target plan). A stored row
    runs with the copy of the variogram model it keeps (a new version of the model is taken on `PUT`)."""
    from bf_core.blocks.model import Model

    from . import targets

    row = None
    if run.simulation_id is not None:
        row = sc.stored(conn, run.simulation_id)  # 404
        if run.expected_version is not None and int(row.version) != run.expected_version:
            raise sc.conflict(int(row.version))
        meta = dict(row.meta or {})
        if sc.freshness(conn, meta)["source_lost"]:
            raise ApiError(409, "source_lost",
                           f"У моделирования «{row.name}» удалён источник: таблица, набор или модель.",
                           "Выберите существующие данные в параметрах или удалите моделирование.")  # fmt: skip
        spec = sc.load_spec(meta)
    else:
        assert run.simulation is not None
        spec = parse_simulation(run.simulation)
        if spec.name is not None:
            sb.final_name(conn, sto.KIND, spec.name, "")  # 409 `name_taken` before the long work
        spec, _data = sc.check(conn, spec)
    inputs = sc.inputs_of(conn, spec)
    model = Model.open(conn, store, spec.model_id)
    return row, spec, model, inputs, targets.TargetPlan.of(conn, store, spec, model)


def run_simulate(
    engine: sa.Engine, folder: Any, params: dict[str, Any], tick: Tick = _noop, *, threads: int | None = None,
    job_id: int | None = None, memory_mb: float | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The body of `simulate.run` (the handler, the tests and the bench in the process; module docstring)."""
    import numpy as np

    from bf_core.composites import strips
    from bf_core.geostat.estimate import samples as sm

    from . import real, targets
    from . import sgs as sg
    from .nodes import NodeGrid

    run = parse_run(params)
    folder = Path(folder)
    store = BlobStore(folder)
    clock = _Clock(tick)
    clock(0.01, S_SAMPLES)
    with dialect.read_tx(engine) as conn:
        row, spec, model, inputs, plan = plan_run(conn, store, run)
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    clock(AT_LOAD, S_SAMPLES)
    samples = sm.load(project, spec, cancel=lambda: clock(None, None))
    clock(AT_LOAD + 0.04, S_SAMPLES)
    p = targets.find(plan, lambda _a, _b: clock(None, None))
    if p.size == 0:
        raise ApiError(422, "no_targets", "В выбранном домене нет блоков: моделировать нечего.",
                       "Проверьте домен блоков и ограничитель.", {"param": "target"})  # fmt: skip
    grid = model.grid
    ng = NodeGrid.of(grid.counts, p)
    kmodel, ell, local, steps = grid_frame(spec, samples, grid)
    n_max = spec.search.passes[0].n_max
    par = sg.SgsParams(kmodel, ell, steps, spec.seed, spec.n_real, n_max, spec.nodes, *spec.nscore.tails(),
                       spec.nscore.ltpar, spec.nscore.utpar)  # fmt: skip
    centres = sg.grid_centres(ng, steps)
    sampler = _sampler(spec, local, ell)
    want = strips.default_threads() if threads is None else int(threads)
    n_threads = _threads(want, ng.n, spec.n_real, n_max, memory_mb, samples.n)

    def sim_tick(progress: float | None, label: str | None) -> None:
        if progress is None:
            clock(None, None)
        elif label is None:
            clock(AT_LOAD + 0.04 + 0.02 * progress, S_NSCORE)
        else:
            clock(AT_SIM + (AT_WRITE - AT_SIM) * progress, label)

    clock(AT_LOAD + 0.04, S_NSCORE)
    res = sg.simulate(par, local, ng, centres, threads=n_threads, tick=sim_tick, sampler=sampler)
    clock(AT_WRITE, S_WRITE)
    staged = real.write(store, p, np.zeros(p.size, dtype=np.int16), res.real)
    summary = real.stats_of(res.real)
    res.real = np.empty((0, 0))  # the realizations are in the file now: the memory goes back before the transaction
    now = dialect.utcnow().isoformat()
    log = _log(spec, res, ng.n, list(res.notes))
    try:
        with dialect.write_tx(engine) as conn:
            ident = _commit(conn, store, row, spec, staged, inputs, samples, ng.n, now, job_id, res, summary,
                            run_author(params))  # fmt: skip
    except BaseException:
        staged[0].unlink(missing_ok=True)
        raise
    clock(1.0, None)
    return _answer(spec, ident, res, ng.n, clock, log)


def run_author(params: dict[str, Any]) -> str | None:
    a = params.get("author")
    return None if a in (None, "") else str(a)


def _commit(conn: sa.Connection, store: BlobStore, row: Any, spec: SimulationSpec, staged: tuple[Path, str, int],
            inputs: list[dict[str, Any]], samples: Any, n: int, now: str, job_id: int | None, res: Any,
            summary: dict[str, Any], author: str | None) -> int:  # fmt: skip
    """In the write transaction: the row (a body — made now; a stored one — the version is still the one read, or 409),
    the blob, `last_run`. The files of the realizations replace the old ones."""
    if row is None:
        prep = sc.Prepared(spec, [float(v) for v in samples.origin], inputs, None)
        ident = int(sto.create(conn, None, spec, author, prepared=prep)["id"])
    else:
        ident = int(row.id)
        now_row = sc.stored(conn, ident)
        if int(now_row.version) != int(row.version):
            raise sc.conflict(int(now_row.version))
    tot = res.counts.sum(axis=0)
    used = samples.counts
    last = {"at": now, "job": job_id, "nodes": n, "realizations": spec.n_real, "seconds": round(float(res.seconds), 3),
            "counts": {"degenerate": int(tot[0]), "free": int(tot[1]), "on_sample": int(tot[2])},
            "cond": {"min": int(res.cond.min()), "mean": round(float(res.cond.mean()), 3), "max": int(res.cond.max())},
            "samples": {"rows": int(used.get("rows", 0)), "used": int(used.get("selected", 0))},
            "summary": summary}  # fmt: skip
    sto.commit_run(conn, store, ident, staged, last, inputs, [float(v) for v in samples.origin])
    return ident


# --- «Перестроить» ------------------------------------------------------------------------------------------------


def rebuild_row(svc: Any, cap: Any, node: Node, _body: dict[str, Any]) -> None:
    """The maker of the node `object:simulation` for `lineage.rebuild`: the job of this row (the same seed — the same
    realizations when the inputs are the same)."""
    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        row = sc.stored(conn, node.id)
    cap.submit(KIND, {"simulation_id": node.id}, title=f"Моделирование: {row.name}")


def register_rebuild() -> None:
    """`lineage.rebuild.register` for the row (the owner of itself). Safe to repeat."""
    from bf_core.lineage import rebuild as rb

    rb.register(sc.LINEAGE_KIND, lambda _conn, node: node, rebuild_row)


register_rebuild()


# --- the handler ----------------------------------------------------------------------------------------------------


@handler(KIND, TITLE, queue="heavy", max_attempts=1, estimate_memory=memory_mb)
def simulate_job(ctx: JobContext) -> dict[str, Any]:
    from bf_core.jobs.context import JobError

    assert ctx.project_folder is not None
    try:
        return run_simulate(ctx.engine(), ctx.project_folder, ctx.params, ctx.tick, job_id=ctx.id,
                            memory_mb=ctx.job.get("memory_mb"))  # fmt: skip
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint, retry=False) from None
