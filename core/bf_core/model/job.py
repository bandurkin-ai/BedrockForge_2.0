"""The jobs `implicit.build` and `implicit.update` — an implicit model into its files and its outputs (подшаг 19.12; ТЗ
8.9.3; ADR 0026 решения 12–15, 22–26, «Варианты» → «Обновить»; docs/progress/e19-api.md §2, §6, §7).

    out = run_build(engine, folder, params, tick, update=False, threads=None, job_id=None)    # the body of the job

`params` — what the route checked: `model_id`, `expected_version` (of the model), `threads` (1–64). The job marks the
model `building` in its first transaction (`_begin`; the state is in `meta`, not in `objects.state`); a cancel puts back
the state it had (`ready` or `error` with its reason).

The stages (`spec.STAGES`, the text of a message starts with its stage): «Данные» (`data.load`) → «Участки» (the octree
and the solution of the patches of every group) → «Изоповерхности» (the bricks) → «Тела» (the chronology, the veins, the
expansions, the post-processing) → «Проверки» (8.9.3 п. 7) → «Запись» (`job_write.py`, one transaction). The groups are
built in the order of the model; `implicit.update` is the same run that takes from the project's cache what has not
changed (`update.py`: the patches by the hash of their content, the bricks by the hash of the patches that reach them)
and says how much in `reused`. The result is bit for bit that of a full build (Т8.9-8).

Nothing is in the project before the last transaction: a cancel (`JobCancelled` from `tick`, at any stage) leaves the
model as it was (the state goes back; no blob, no wireframe, no row of the graph); an error — `ApiError` {code, message,
hint} in Russian — puts the model into the state `error` with the reason (`meta.error`) and the job fails with the same
text; the wireframes of the last run stay.

The answer: {model_id, version, state: "ready", mode, outputs [{wireframe_id, kind, group, wing, threshold, name,
volume, area, triangles}], stats (e19-api §2), reused {patches, bricks}, journal [lines], stages {seconds}, events}.
`memory_mb(params)` is what the scheduler waits for.
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
from bf_core.jobs.registry import handler

if TYPE_CHECKING:
    from bf_core.jobs.context import JobContext

    from .job_stages import Built

KIND_BUILD, KIND_UPDATE = "implicit.build", "implicit.update"
TITLE_BUILD, TITLE_UPDATE = "Построение неявной модели", "Обновление неявной модели"
Tick = Callable[[float | None, str | None], None]
BASE_MB, POINT_MB, THREAD_MB, BRICK_MB = 400.0, 0.0012, 160.0, 120.0
MAX_THREAD_MB = 2048.0  # `solve.solve_patches` holds the matrices of the patches in flight to 2 GB


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


# --- the memory -------------------------------------------------------------------------------------------------------


def memory_mb(params: dict[str, Any]) -> int:
    """What the scheduler waits for (ADR 0002 §4): the interpreter and the kernels, the points (`points` of the route,
    300 000 when it does not say), the matrices of the patches in flight (a patch of N_уч points is (N_уч + 4)² × 16
    bytes at the peak; the pool is held to 2 GB), the bricks of the isosurface."""
    from bf_core.composites import strips

    threads = max(1, int(params.get("threads") or strips.default_threads()))
    points = int(params.get("points") or 300_000)
    n_patch = int(params.get("n_patch") or 1000)
    patch_mb = 16.0 * (3 * n_patch + 4) ** 2 / 2**20  # a patch of the ball of 3·N_уч points at most
    return int(BASE_MB + POINT_MB * points + min(threads * patch_mb, MAX_THREAD_MB) + BRICK_MB + 0.2 * THREAD_MB)


# --- the state of the model -------------------------------------------------------------------------------------------


def _error_of(err: ApiError) -> dict[str, Any]:
    out: dict[str, Any] = {"code": err.code, "message": err.message, "hint": err.hint}
    if err.extra.get("group") is not None:
        out["group"] = err.extra["group"]
    return out


def _begin(engine: sa.Engine, model_id: int, expected: Any, job_id: int | None) -> dict[str, Any]:
    """The job starts: the model is marked `building` with the id of the job (in `meta`, see `store`); returns the
    state it goes back to on a cancel. A model that another job (alive) is building — 409 `model_building`; a mark whose
    job is gone is taken over (the model goes back to the state in `meta.back`)."""
    from . import store as st
    from . import store_base as sb

    with dialect.write_tx(engine) as conn:
        row = sb.stored(conn, model_id)
        if expected is not None and int(row.version) != int(expected):
            raise sb.conflict(int(row.version))
        meta = row.meta or {}
        if meta.get("state") == "building" and meta.get("job_id") not in (None, job_id) and sb.is_building(conn, meta):
            raise sb.building()
        state, error = sb.back_of(meta)
        st.set_state(conn, model_id, "building", job_id=job_id)
    return {"state": state, "error": error}


def _settle(engine: sa.Engine, model_id: int, state: str, error: dict[str, Any] | None) -> None:
    """The job ended without a result: the state of the model (`ready` again after a cancel, `error` with the reason).
    A model deleted meanwhile is nothing to settle."""
    from . import store as st

    try:
        with dialect.write_tx(engine) as conn:
            st.set_state(conn, model_id, state, error=error)
    except ApiError:
        pass


def _events(model_id: int) -> list[list[Any]]:
    return [["implicit.changed", {"kind": "implicit_model", "id": model_id}]]


# --- the run ----------------------------------------------------------------------------------------------------------


def _stats(points_n: int, built: Built, solver_reused: tuple[int, int], bricks: tuple[int, int],
           seconds: float) -> dict[str, Any]:  # fmt: skip
    parts = [p.stats for p in sorted(built.parts, key=lambda p: (p.group, p.wing))]
    maxes = [p["max_residual"] for p in parts if p["max_residual"] is not None]
    conds = [p["cond_max"] for p in parts if p["cond_max"] is not None]
    etas = [p["eta_max"] for p in parts if p["eta_max"] is not None]
    return {"points": int(points_n), "patches": sum(int(p["patches"]) for p in parts),
            "max_residual": max(maxes) if maxes else None, "cond_max": max(conds) if conds else None,
            "eta_max": max(etas) if etas else None, "seconds": round(seconds, 3),
            "reused": {"patches": int(solver_reused[0]), "bricks": int(bricks[0])},
            "by_group": parts, "balance": built.balance}  # fmt: skip


def _answer(model_id: int, done: Any, mode: str, outputs: list[dict[str, Any]], stats: dict[str, Any],
            journal: list[str], stages: dict[str, float], events: list[list[Any]]) -> dict[str, Any]:  # fmt: skip
    return {"model_id": model_id, "version": done.version, "state": "ready", "mode": mode, "outputs": outputs,
            "stats": stats, "reused": stats["reused"], "journal": journal, "stages": stages,
            "events": events}  # fmt: skip


def run_build(
    engine: sa.Engine, folder: Any, params: dict[str, Any], tick: Tick = _noop, *, update: bool = False,
    threads: int | None = None, job_id: int | None = None, memory_mb: float | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The body of `implicit.build` / `implicit.update` (the handlers, the tests and the bench in the process; the
    module text). `memory_mb` is not used yet (the pool of the patches holds itself to 2 GB)."""
    from bf_core.jobs.context import JobCancelled

    model_id = int(params["model_id"])
    expected = params.get("expected_version")
    back = _begin(engine, model_id, expected, job_id)
    try:
        return _run(engine, Path(folder), model_id, expected, params, tick, update, threads, job_id)
    except JobCancelled as exc:
        _settle(engine, model_id, back["state"], back["error"])
        exc.events = _events(model_id)
        raise
    except ApiError as err:
        _settle(engine, model_id, "error", _error_of(err))
        raise
    except BaseException:
        _settle(engine, model_id, "error", {"code": "internal", "message": "Построение прервано внутренней ошибкой.",
                                            "hint": "Скопируйте журнал задания и отправьте разработчику."})  # fmt: skip
        raise


def _run(engine: sa.Engine, folder: Path, model_id: int, expected: Any, params: dict[str, Any], tick: Tick,
         update: bool, threads: int | None, job_id: int | None) -> dict[str, Any]:  # fmt: skip
    from bf_core import lineage  # noqa: F401 - the providers of the graph are registered with the package
    from bf_core.composites import strips
    from bf_core.jobs.context import JobCancelled
    from bf_core.storage.blobs import BlobStore

    from . import job_stages as js
    from . import job_write as jw
    from . import store as st
    from . import store_base as sb
    from . import update as up

    started = time.perf_counter()
    store, prog = BlobStore(folder), js.Progress(tick)
    n_threads = max(1, int(threads or params.get("threads") or strips.default_threads()))
    mode = "update" if update else "build"
    prog(0.0, f"{js.S_DATA}: параметры модели")
    with dialect.read_tx(engine) as conn:
        row = sb.stored(conn, model_id)
        meta = dict(row.meta or {})
        spec0 = sb.load_spec(meta)
        prep = st.prepare(conn, spec0)
        spec = prep.spec
        js.refuse_unready(spec)
        pre = js.prefetch(conn, store, prep)
        cache = up.PatchCache.empty()
        same_program = meta.get("algo") == sb.ALGO and meta.get("versions") == st._versions()  # noqa: SLF001
        take = update and same_program  # the same program made the last run: its hashes mean the same
        if take:
            cache = up.PatchCache.from_blobs(conn, store, model_id)
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    points = js.load_points(project, spec, prog)
    solver = up.CachingSolver(spec, cache, matrix=pre.matrix, threads=n_threads)
    built = js.build_groups(project, prep, pre, points, solver, folder=folder, model_id=model_id, read_bricks=take,
                            threads=n_threads, prog=prog)  # fmt: skip
    prog(js.AT_CHECK, f"{js.S_CHECK}: тела модели")
    journal = [*points.notes, *built.journal, *js.check_outputs(built, spec, prog)]
    reused = solver.reused()
    if update:
        journal.append(f"Обновление: из кеша взято участков {reused[0]} из {reused[1]}, кирпичей {built.bricks.hits}.")
    seconds = time.perf_counter() - started
    bricks = (built.bricks.hits, built.bricks.hits + built.bricks.puts)
    stats = _stats(points.n, built, reused, bricks, seconds)
    files = jw.stage_files(store, spec, str(meta.get("name") or spec.name), points, built, prog)
    try:
        last_run = {"at": dialect.utcnow().isoformat(), "job": job_id, "mode": mode, "seconds": round(seconds, 3)}
        done = jw.commit(engine, store, model_id, files, spec=spec, prep=prep, expected_version=expected,
                         last_run=last_run, stats=stats)  # fmt: skip
    finally:
        files.discard()
    jw.drop_levels(folder, done.removed)
    up.sweep(folder, model_id, built.used)
    try:
        prog(1.0, None)
    except JobCancelled:
        pass  # the commit is done: a cancel now takes nothing back (`run_build` would put the old state over it)
    with dialect.read_tx(engine) as conn:
        listed = st.outputs_of(conn, {"outputs": done.outputs})
    return _answer(model_id, done, mode, listed, stats, journal, prog.stages(), jw.events(model_id, done))


# --- the handlers -----------------------------------------------------------------------------------------------------


def _handle(ctx: JobContext, update: bool) -> dict[str, Any]:
    from bf_core.jobs.context import JobError

    assert ctx.project_folder is not None
    try:
        return run_build(ctx.engine(), ctx.project_folder, ctx.params, ctx.tick, update=update, job_id=ctx.id,
                         memory_mb=ctx.job.get("memory_mb"))  # fmt: skip
    except ApiError as exc:
        events = _events(int(ctx.params["model_id"]))  # the tree shows the state `error` of the model
        raise JobError(exc.code, exc.message, exc.hint, retry=False, events=events) from None


@handler(KIND_BUILD, TITLE_BUILD, queue="heavy", max_attempts=1, estimate_memory=memory_mb)
def build_job(ctx: JobContext) -> dict[str, Any]:
    return _handle(ctx, False)


@handler(KIND_UPDATE, TITLE_UPDATE, queue="heavy", max_attempts=1, estimate_memory=memory_mb)
def update_job(ctx: JobContext) -> dict[str, Any]:
    return _handle(ctx, True)
