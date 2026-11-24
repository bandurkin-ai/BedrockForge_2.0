"""The jobs `blocks.prototype` and `blocks.regularize` (подшаг 17.29; ТЗ 8.7.3, 8.7.5, 8.7.7; ADR 0023 §6, §9,
решения 14, 22; the forms — `docs/progress/e17-api.md` §6, §9, §15). The jobs of the fields
`blocks.density` and `blocks.classify` are in `jobs_fields.py`; the routes — `routes_ops.py`.

    out = run_prototype(engine, folder, params, tick=tick)           # an empty grid → a model with 0 rows
    out = run_regularize(engine, folder, params, tick=tick)          # a model → a new regular model (or in place)

The prototype (queue quick). `params` = the body of the request with the box resolved by the route (`extent`): {size,
extent, pad, rotation, levels, full, name, folder}. Stages «Охват» (the plan: the grid and its counts), «Запись» (the
model with 0 rows). Result: `prototype.create_prototype` + `stages`.

The regularization (queue heavy). `params`: {model_id, size, grades, density, categories, name, folder, rebuild: null |
{model_id, expected_version}}; for «Перестроить» (`routes.REBUILD["regularize"]`) `model_id` is the source model (the
one in `inputs.model` of the regularized model) and the regularized model is rewritten in place (the id and the name
stay, version + 1; the fields of other makers leave — `dropped`; 409 `version_conflict {version}`). Stages «Чтение»
(the source and the plan), «Перекрытия» (`regularize.regularize`: the overlaps by portions of the rows), «Запись» (the
file of the new model and its object in one transaction). A cancel at any tick, a failure and a stale version leave
neither the file nor a row. The result: {model_id, name, rows, totals {before, after, metal_basis}, stages {read,
overlap, write, total}, warnings, events[, version, dropped]}. The threads — `composites.strips.default_threads()`; the
result is the same bit for bit with any number of them (17.25).

Errors — ApiError {code, message, hint, param?} in Russian; in a handler a failure with the same code (no retry).
numpy and the kernels load when a job runs, not with this module.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import schema as bs
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore

if TYPE_CHECKING:
    from bf_core.jobs.context import JobContext

KIND_PROTOTYPE = "blocks.prototype"
KIND_REGULARIZE = "blocks.regularize"
TITLE_PROTOTYPE = "Прототип блочной модели"
TITLE_REGULARIZE = "Регуляризация блочной модели"
S_EXTENT, S_WRITE = "Охват", "Запись"
S_READ, S_OVERLAP = "Чтение", "Перекрытия"
Tick = Callable[[float | None, str | None], None]


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


class Clock:
    """Seconds of the stages of a job from the messages of its ticks (`stages()` → {key: seconds, total})."""

    def __init__(self, tick: Tick, keys: dict[str, str]) -> None:
        self.tick, self.keys = tick, keys
        self.t0 = self.at = time.perf_counter()
        self.stage: str | None = None
        self.took: dict[str, float] = {}

    def __call__(self, progress: float | None = None, message: str | None = None) -> None:
        if message is not None and message != self.stage:
            self._close()
            self.stage = message
        self.tick(progress, message)

    def _close(self) -> None:
        now = time.perf_counter()
        if self.stage is not None:
            key = self.keys.get(self.stage, self.stage)
            self.took[key] = self.took.get(key, 0.0) + now - self.at
        self.at = now

    def stages(self) -> dict[str, float]:
        self._close()
        self.stage = None
        return {**{k: round(v, 3) for k, v in self.took.items()}, "total": round(time.perf_counter() - self.t0, 3)}


def _events(model_id: int) -> list[list[Any]]:
    return [["objects.changed", {"kind": bs.KIND, "id": model_id}]]


# --- the prototype ----------------------------------------------------------------------------------------------------


def numbers(value: Any) -> Any:
    """A text like «0,5» is the number 0.5 (also inside lists); anything else — as it is (the checks refuse it)."""
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", "."))
        except ValueError:
            return value
    if isinstance(value, list | tuple):
        return [numbers(v) for v in value]
    return value


def plan_of(params: dict[str, Any]) -> Any:
    """`prototype.Plan` of the request (the route and the job make it the same way): size, extent, pad, rotation,
    levels, full. Texts like «0,5» are numbers."""
    from bf_core.blocks import prototype as pr

    num = numbers

    return pr.make_plan(
        num(params.get("size")), num(params.get("extent")), pad=num(params.get("pad", 0.0)),
        rotation=num(params.get("rotation", 0.0)), levels=num(params.get("levels")),
        full=params.get("full", False),
    )  # fmt: skip


def run_prototype(engine: sa.Engine, folder: Any, params: dict[str, Any], *, tick: Tick = _noop) -> dict[str, Any]:
    """The body of `blocks.prototype` (the handler and the tests)."""
    from bf_core.blocks import prototype as pr

    clock = Clock(tick, {S_EXTENT: "extent", S_WRITE: "write"})
    clock(0.05, S_EXTENT)
    plan = plan_of(params)
    store = BlobStore(Path(folder))
    clock(0.5, S_WRITE)
    with dialect.write_tx(engine) as conn:
        out = pr.create_prototype(conn, store, plan, name=str(params.get("name") or pr.NAME),
                                  folder=params.get("folder"), job_id=params.get("job_id"))  # fmt: skip
    return {**out, "stages": clock.stages()}


# --- the regularization -----------------------------------------------------------------------------------------------


def memory_mb(params: dict[str, Any]) -> int:
    """MB: the portion of the rows and the layers of the new cells (a window of at most 3 GB, 17.25); the estimate
    follows the rows of the source: 2·10⁶ rows ≈ 1.3 GB, 20·10⁶ ≈ 3 GB."""
    rows = int(params.get("rows") or 0)
    return max(768, min(3072, 768 + rows // 8000))


def _threads(threads: int | None) -> int:
    from bf_core.composites import strips

    return strips.default_threads() if threads is None else int(threads)


def _name_of(conn: sa.Connection, model_id: int) -> str:
    return str(conn.execute(sa.select(objects.c.name).where(objects.c.id == model_id)).scalar_one())


def _patch_meta(conn: sa.Connection, model_id: int, algo: str, names: dict[str, Any]) -> None:
    """The model written by a commit of the fill (`replace_in_place`) or of the regularization gets the algorithm of
    the regularization and the name of its source in `source.params.names` («удалена модель «…»» of «устарел»)."""
    meta = dict(conn.execute(sa.select(objects.c.meta).where(objects.c.id == model_id)).scalar_one())
    source = dict(meta.get("source") or {})
    source["params"] = {**(source.get("params") or {}), "names": names}
    conn.execute(sa.update(objects).where(objects.c.id == model_id).values(meta={**meta, "source": source,
                                                                                "algo": algo}))  # fmt: skip


def run_regularize(
    engine: sa.Engine, folder: Any, params: dict[str, Any], *, tick: Tick = _noop, threads: int | None = None,
    chunk: int | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The body of `blocks.regularize` (the handler, the tests and the bench in the process)."""
    from bf_core.blocks import regularize as rg
    from bf_core.blocks.fill import commit, inputs
    from bf_core.blocks.model import CHUNK, Model

    store = BlobStore(Path(folder))
    clock = Clock(tick, {S_READ: "read", S_OVERLAP: "overlap", S_WRITE: "write"})
    clock(0.01, S_READ)
    rebuild = params.get("rebuild")
    with dialect.read_tx(engine) as conn:
        model = Model.open(conn, store, int(params["model_id"]))
        # «устарел» compares the files of ALL the fields of the source (`fill.inputs.model_sha`), not the cells only
        source_inputs = {"model": inputs.inputs_of(conn, [], model.id)["model"]}
        if rebuild is not None:  # a model that is gone or changed is a refusal before the work
            row = conn.execute(sa.select(objects.c.version).where(objects.c.id == int(rebuild["model_id"]))).first()
            if row is None:
                raise commit.source_lost("регуляризованная модель удалена")
            if int(row.version) != int(rebuild["expected_version"]):
                from bf_core.blocks.fields_store import version_conflict

                raise version_conflict(int(row.version))
    clock(0.03, S_OVERLAP)

    def progress(f: float) -> None:
        clock(0.03 + 0.92 * f, S_OVERLAP)

    res = rg.regularize(model, store, params["size"], grades=params.get("grades"), density=params.get("density"),
                        categories=params.get("categories"), threads=_threads(threads), tick=progress,
                        chunk=chunk or CHUNK)  # fmt: skip
    res.inputs = source_inputs
    names = {"model": {str(model.id): model.name}}
    name = str(params.get("name") or "").strip() or f"{model.name} (регуляризация)"
    try:  # whatever happens from here, the staged file does not stay (a cancel at the first tick of the stage too)
        clock(0.97, S_WRITE)
        with dialect.write_tx(engine) as conn:
            if rebuild is None:
                out = rg.commit(conn, store, model, res, name=name, folder=params.get("folder"),
                                job_id=params.get("job_id"))  # fmt: skip
                _patch_meta(conn, int(out["model_id"]), rg.ALGO, names)
            else:
                target = int(rebuild["model_id"])
                done = commit.replace_in_place(conn, store, res.staged, target, int(rebuild["expected_version"]),
                                               op=rg.OP, params=res.plan.params(), inputs=res.inputs,
                                               warnings=[])  # fmt: skip
                _patch_meta(conn, target, rg.ALGO, names)
                out = {"model_id": target, "name": _name_of(conn, target), "rows": res.rows, "totals": res.totals,
                       "events": _events(target), **done}  # fmt: skip
    finally:
        res.staged.discard()
    return {**out, "warnings": [], "stages": clock.stages()}


# --- the warm-up ------------------------------------------------------------------------------------------------------


def warm() -> dict[str, float]:
    """The kernels of the regularization (numba, `cache=True`): a model of 2 × 2 × 2 parents is regularized in a
    temporary project (`blocks/warm.py` calls it as a step). The first compilation is ≈ 1.3 s."""
    import tempfile

    import numpy as np

    from bf_core.blocks import store as bm
    from bf_core.db import migrate

    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="bf-warm-") as d:
        engine = dialect.create_engine(dialect.sqlite_url(str(Path(d) / "project.sqlite")))
        try:
            migrate.upgrade(engine, "project")
            store = BlobStore(Path(d))
            grid = bs.GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": [2, 2, 2], "size": [1.0, 1.0, 1.0]})
            fields: list[dict[str, Any]] = [{"name": "A", "dtype": "f8", "role": "grade"},
                                            {"name": "C", "dtype": "cat", "labels": ["a", "b"]}]  # fmt: skip
            with bm.CellsWriterV2(store, grid, fields) as w:
                w.write(np.arange(8, dtype=np.uint64), {"A": np.linspace(0.5, 4.0, 8),
                                                        "C": (np.arange(8) % 2).astype(np.int32)})  # fmt: skip
                staged = w.finish()
            with dialect.write_tx(engine) as conn:
                mid = bm.commit_block_model(conn, store, staged, name="warm")
            staged.discard()
            run_regularize(engine, d, {"model_id": mid, "size": [2.0, 2.0, 2.0]}, threads=1)
        finally:
            engine.dispose()
    return {"regularize": time.perf_counter() - t0}


# --- the handlers -----------------------------------------------------------------------------------------------------


def _user(exc: ApiError) -> Any:
    from bf_core.jobs.context import JobError

    return JobError(exc.code, exc.message, exc.hint, retry=False)


@handler(KIND_PROTOTYPE, TITLE_PROTOTYPE, queue="quick")
def prototype_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        return run_prototype(ctx.engine(), ctx.project_folder, {**ctx.params, "job_id": ctx.id}, tick=ctx.tick)
    except ApiError as exc:
        raise _user(exc) from None


@handler(KIND_REGULARIZE, TITLE_REGULARIZE, queue="heavy", estimate_memory=memory_mb)
def regularize_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        return run_regularize(ctx.engine(), ctx.project_folder, {**ctx.params, "job_id": ctx.id}, tick=ctx.tick)
    except ApiError as exc:
        raise _user(exc) from None
