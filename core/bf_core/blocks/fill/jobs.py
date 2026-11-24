"""The jobs `blocks.fill` and `blocks.code` (подшаг 17.20; ТЗ 8.7.3, 8.7.7; ADR 0023 §6, решения 8, 9; the forms —
docs/progress/e17-api.md §7).

    out = run_fill(engine, folder, params, tick=tick, threads=None)   # a prototype + closed bodies → a new model
    out = run_code(engine, folder, params, tick=tick, threads=None)   # a ready model + bodies → fields or a new model

`params` (the routes build them, `routes.py`): {"spec": FillSpec.to_params(), "rebuild": null | {"model_id",
"expected_version"}, "parents": cells of the grid, "triangles": of all the bodies, "rows": of the model coded}.

The fill. Stages (the message of the job): «Каркасы» (the bodies read, checked: closed and consistent, 422), «Столбцы»
(the class of every parent by the ray through its centre), «Граничные родители» (the parents a triangle goes through),
«Дробление» (their sub-blocks by the levels L: code and FRAC by the m³ points), «Слияние» (8 children of one code
and FRAC ∈ {0; 1} into the parent), «Запись» (the file `cells` of the new model, the object). The stages of the
kernel (`octree.fill_grid`) are told by the fraction its `tick` gives: 0–0.25 columns, –0.30 boundary, –0.95 splitting,
the rest merging. The new model «‹прототип› (заполнение)»: CODE (category, role `code`, the labels — the codes of the
bodies and, with the mode «снаружи» / «оба», `outside_code`) and FRAC (f8, role `frac`), `source {op: fill,
params}`, `inputs {wireframes: [{id, sha}]}` (`inputs.py`: «устарел»), `algo`. The levels `levels` of the request
(null — the prototype's) become the division of the grid. A cancel at any tick leaves nothing: the file is written to
a temp name and committed last, with the object, in one transaction.

The coding. «Кодировать каркасами» on a ready model: `split: false` — the fields CODE (and FRAC when asked) of the
same model, a class by the box of every row (`octree.split_rows(split=False, keep=True)`: a row no body of the mode
holds has no code and FRAC NaN: the rows are not ours to drop); the sub-blocks stay; the version of the model + 1
(`FieldsWriter`, `commit_fields`; 409 `version_conflict {version}` when it changed). `split: true` — a new model
«‹имя› (кодирование)» whose rows are the sub-blocks of the boundary rows: every field of the parent is inherited by
its children (`Rows.src`), the rows keep the order (P, sz, sy, sx); `source {op: code}`, `inputs {model, wireframes}`.
Stages: «Каркасы», «Граничные родители», «Дробление», «Запись» (the classification of the rows is in the kernel of
the splitting). Both read the model in portions that end at a parent (rows of one parent are split together).

«Перестроить» (`rebuild`): the same jobs with `source.params` of the model; the rows and the fields go in place (the
same id and name, version + 1, `commit.replace_in_place`); a fill takes the grid of the model itself.

Threads — `composites.strips.default_threads()`; the result is the same bit for bit with 1, 2, 4, 8 threads.
Errors — ApiError {code, message, hint, param?} in Russian; in the job a failure with the same code (no retry).
Numpy and the kernels are imported when the job runs, not with the module (it loads with the core).
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import meta as bmeta
from bf_core.blocks import schema as bs
from bf_core.blocks.fill import commit, inputs
from bf_core.blocks.fill.spec import FillSpec
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore

if TYPE_CHECKING:
    import numpy as np

    from bf_core.blocks.model import Model
    from bf_core.jobs.context import JobContext

KIND_FILL = "blocks.fill"
KIND_CODE = "blocks.code"
TITLE_FILL = "Заполнение каркасами"
TITLE_CODE = "Кодирование каркасами"
Tick = Callable[[float | None, str | None], None]
S_WIRES, S_COLUMNS, S_BOUNDARY, S_SPLIT, S_MERGE, S_WRITE = (
    "Каркасы", "Столбцы", "Граничные родители", "Дробление", "Слияние", "Запись")  # fmt: skip
#: the share of the progress bar the stages of the kernel `fill_grid` take (its ticks 0…1 go into this span)
FILL_SPAN = (0.06, 0.90)
#: where the stages end in the fraction of `fill_grid.tick` (octree.py: 0.25 columns, 0.30 boundary, 0.95 split)
KERNEL_STAGES = ((0.25, S_COLUMNS), (0.30, S_BOUNDARY), (0.95, S_SPLIT), (1.01, S_MERGE))


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def memory_mb(params: dict[str, Any]) -> int:
    """MB: the class of a parent and its flag (3 bytes), the rows (≈ 24 bytes) and the boundary pairs (≈ 150 bytes a
    triangle); 20·10⁶ parents and 10⁶ triangles measured 1.5 GB (17.18), the limit of the ТЗ is 3 GB."""
    cells = int(params.get("parents") or 0)
    tris = int(params.get("triangles") or 0)
    rows = int(params.get("rows") or 0)
    return max(512, int((cells * 60 + tris * 150 + rows * 40) / 2**20) + 256)


class Clock:
    """Seconds of the stages of a job from the messages of its ticks."""

    KEYS = {S_WIRES: "wireframes", S_COLUMNS: "columns", S_BOUNDARY: "boundary", S_SPLIT: "split", S_MERGE: "merge",
            S_WRITE: "write"}  # fmt: skip

    def __init__(self, tick: Tick) -> None:
        self.tick, self.t0, self.at = tick, time.perf_counter(), time.perf_counter()
        self.stage: str | None = None
        self.took: dict[str, float] = {}

    def __call__(self, progress: float | None = None, message: str | None = None) -> None:
        if message is not None and message != self.stage:
            self.close()
            self.stage = message
        self.tick(progress, message)

    def close(self) -> None:
        now = time.perf_counter()
        if self.stage is not None:
            key = self.KEYS.get(self.stage, self.stage)
            self.took[key] = self.took.get(key, 0.0) + now - self.at
        self.at = now

    def stages(self, only: tuple[str, ...] | None = None) -> dict[str, float]:
        self.close()
        self.stage = None
        out = {k: round(v, 3) for k, v in self.took.items() if only is None or k in only}
        return {**out, "total": round(time.perf_counter() - self.t0, 3)}


def _read_model_row(conn: sa.Connection, model_id: int) -> Any:
    row = conn.execute(
        sa.select(objects.c.name, objects.c.version, objects.c.meta).where(
            objects.c.kind == bs.KIND, objects.c.id == model_id, objects.c.state == "ready"
        )
    ).first()
    if row is None:
        from bf_core.blocks.store import not_found

        raise not_found()
    return row


def _events(model_id: int) -> list[list[Any]]:
    return [["objects.changed", {"kind": bs.KIND, "id": model_id}]]


def _fill_grid_and_name(conn: sa.Connection, spec: FillSpec, rebuild: dict[str, Any] | None) -> tuple[bs.GridV2, str]:
    """The grid to fill and the base of the name of the new model: the prototype's grid with the levels of the
    request; «Перестроить» — the grid of the model itself."""
    from bf_core.blocks.prototype import prototype_info

    if rebuild is not None:
        row = _read_model_row(conn, int(rebuild["model_id"]))
        return bmeta.grid_of(bmeta.upgrade(row.meta)), str(row.name)
    row = _read_model_row(conn, spec.source_id)
    info = prototype_info(row.meta)
    if info is None:
        raise commit.not_prototype()
    grid: bs.GridV2 = info["grid"]
    if spec.levels is not None:
        grid = dataclasses.replace(grid, sub=bs.SubblockSpec.of_levels(spec.levels))
    return grid, str(row.name)


# --- the fill --------------------------------------------------------------------------------------------------------


def run_fill(
    engine: sa.Engine, folder: Any, params: dict[str, Any], *, tick: Tick = _noop, threads: int | None = None
) -> dict[str, Any]:  # fmt: skip
    """The body of `blocks.fill` (the handler, the tests and the bench in the process)."""
    from bf_core.blocks.fill import octree

    spec = FillSpec.of_params(params["spec"])
    rebuild = params.get("rebuild")
    store = BlobStore(Path(folder))
    clock = Clock(tick)
    clock(0.0, S_WIRES)
    with dialect.read_tx(engine) as conn:
        grid, base = _fill_grid_and_name(conn, spec, rebuild)
        infos = commit.wire_infos(conn, spec.wires)
        ins = inputs.inputs_of(conn, [w.id for w in spec.wires])
        meshes, warnings = commit.load_meshes(conn, store, grid, spec.wires, infos,
                                              tick=lambda f: clock(FILL_SPAN[0] * f, S_WIRES))  # fmt: skip
    lab = commit.Labels.of(spec.codes([str(i["name"]) for i in infos]), spec.outside_code, spec.mode)
    n_threads = _threads(threads)
    clock(FILL_SPAN[0], S_COLUMNS)

    def kernel(f: float) -> None:
        stage = next(name for end, name in KERNEL_STAGES if f < end)
        clock(FILL_SPAN[0] + (FILL_SPAN[1] - FILL_SPAN[0]) * f, stage)

    fill = octree.fill_grid(grid, meshes, priorities=[w.priority for w in spec.wires], mode=spec.mode, m=spec.m,
                            threads=n_threads, tick=kernel)  # fmt: skip
    del meshes
    if not len(fill.p):
        warnings.append("В выбранном режиме ни один блок не попал в тела: модель без строк.")
    code = lab.apply(fill.code)
    sub = grid.sub.sub
    s_all, w_all = (fill.s, fill.w) if sub else (None, None)
    clock(FILL_SPAN[1], S_WRITE)
    pr = {**spec.to_params(), "names": commit.names_of(infos)}
    fields = commit.result_fields(spec.code_field, spec.frac_field, lab.labels, commit.field_source("fill", pr))
    values = {spec.code_field: code, str(spec.frac_field): fill.frac}
    staged = commit.write_rows(store, grid, fill.p, s_all, w_all, values, fields,
                               tick=lambda f: clock(FILL_SPAN[1] + 0.09 * f, S_WRITE))  # fmt: skip
    tally = commit.Tally(lab.labels)
    tally.add(grid, fill.p, s_all, w_all, code, fill.frac)
    rows, volume = tally.result()
    n_rows, kernel_stages = len(fill.p), dict(fill.stages)
    del fill, code
    out = _commit(engine, store, staged, spec, rebuild, op="fill", params=pr, inputs_=ins, warnings=warnings,
                  name=spec.name or f"{base} (заполнение)", job_id=params.get("job_id"))  # fmt: skip
    took = clock.stages(("wireframes", "write"))
    took.update({k: round(v, 3) for k, v in kernel_stages.items()})
    return {**out, "rows": n_rows, "parents": int((staged.v2 or {}).get("parents", 0)), "codes": rows,
            "volume": volume, "stages": took, "warnings": warnings,
            "events": _events(out["model_id"])}  # fmt: skip


def _commit(
    engine: sa.Engine, store: BlobStore, staged: Any, spec: FillSpec, rebuild: dict[str, Any] | None, *, op: str,
    params: dict[str, Any], inputs_: dict[str, Any], warnings: list[str], name: str, job_id: Any,
) -> dict[str, Any]:  # fmt: skip
    """The last step: a new model, or the model in place (`rebuild`), in one write transaction."""
    try:
        with dialect.write_tx(engine) as conn:
            if rebuild is not None:
                done = commit.replace_in_place(conn, store, staged, int(rebuild["model_id"]),
                                               int(rebuild["expected_version"]), op=op, params=params,
                                               inputs=inputs_, warnings=warnings)  # fmt: skip
                return {"model_id": int(rebuild["model_id"]), "name": _name(conn, int(rebuild["model_id"])), **done}
            bm_id = commit.commit_new(conn, store, staged, name=name, op=op, params=params, inputs=inputs_,
                                      warnings=warnings, folder=spec.folder, job_id=job_id)  # fmt: skip
            return {"model_id": bm_id, "name": _name(conn, bm_id), "dropped": []}
    finally:
        staged.discard()


def _name(conn: sa.Connection, model_id: int) -> str:
    return str(conn.execute(sa.select(objects.c.name).where(objects.c.id == model_id)).scalar_one())


def _threads(threads: int | None) -> int:
    from bf_core.composites import strips

    return strips.default_threads() if threads is None else int(threads)


# --- the coding ------------------------------------------------------------------------------------------------------


class Piece:
    """Rows of the model of whole parents: p, s, w (None — no sub-blocks) and the columns of the fields."""

    def __init__(self, p: np.ndarray, s: np.ndarray | None, w: np.ndarray | None, cols: dict[str, np.ndarray]) -> None:
        self.p, self.s, self.w, self.cols = p, s, w, cols

    def cut(self, a: int, b: int) -> Piece:
        return Piece(self.p[a:b], None if self.s is None else self.s[a:b], None if self.w is None else self.w[a:b],
                     {k: v[a:b] for k, v in self.cols.items()})  # fmt: skip

    def __len__(self) -> int:
        return int(len(self.p))


def _join(a: Piece, b: Piece) -> Piece:
    import numpy as np

    return Piece(np.concatenate([a.p, b.p]), None if a.s is None or b.s is None else np.concatenate([a.s, b.s]),
                 None if a.w is None or b.w is None else np.concatenate([a.w, b.w]),
                 {k: np.concatenate([a.cols[k], b.cols[k]]) for k in a.cols})  # fmt: skip


def parent_pieces(model: Model, names: list[str], size: int = 1 << 20) -> Iterator[Piece]:
    """The rows of the model in portions of about `size` rows, each ending at the end of a parent: the rows of one
    parent (its sub-blocks) are split together and their results go to the file in the order (P, sz, sy, sx)."""
    import numpy as np

    carry: Piece | None = None
    for ch in model.iter(names, chunk=size):
        piece = Piece(ch.p, ch.s, ch.w, ch.cols)
        if carry is not None:
            piece, carry = _join(carry, piece), None
        last = int(np.searchsorted(piece.p, piece.p[-1], side="left"))
        if last == 0:  # one parent fills the portion: wait for its end
            carry = piece
            continue
        carry = piece.cut(last, len(piece))
        yield piece.cut(0, last)
    if carry is not None and len(carry):
        yield carry


def _inherited(model: Model) -> list[dict[str, Any]]:
    """The fields of the parent as specs for the new model (the values come with the rows, the stats are counted)."""
    keep = ("name", "kind", "dtype", "unit", "description", "role", "labels", "source")
    return [{k: f[k] for k in keep if k in f} for f in model.fields]


def run_code(
    engine: sa.Engine, folder: Any, params: dict[str, Any], *, tick: Tick = _noop, threads: int | None = None
) -> dict[str, Any]:  # fmt: skip
    """The body of `blocks.code` (the handler, the tests and the bench in the process)."""
    from bf_core.blocks.fill import octree
    from bf_core.blocks.fill.boundary import boundary_parents
    from bf_core.blocks.fill.frac import Bodies
    from bf_core.blocks.model import Model

    spec = FillSpec.of_params(params["spec"])
    rebuild = params.get("rebuild")
    store = BlobStore(Path(folder))
    clock = Clock(tick)
    clock(0.0, S_WIRES)
    with dialect.read_tx(engine) as conn:
        model = Model.open(conn, store, spec.source_id)
        if rebuild is None and spec.expected_version is not None and model.version != spec.expected_version:
            from bf_core.blocks.fields_store import version_conflict

            raise version_conflict(model.version)
        if model.rows == 0:
            raise ApiError(422, "block_model_empty", "В модели нет блоков: кодировать нечего.",
                           "Заполните прототип каркасами (команда «Заполнить каркасами»).")  # fmt: skip
        grid = model.grid
        infos = commit.wire_infos(conn, spec.wires)
        ins = inputs.inputs_of(conn, [w.id for w in spec.wires], model.id if spec.split else None)
        meshes, warnings = commit.load_meshes(conn, store, grid, spec.wires, infos,
                                              tick=lambda f: clock(0.06 * f, S_WIRES))  # fmt: skip
        cells = model.shas()[bs.ROLE]  # the file the rows are read from: the input of the new fields
    commit.check_names(model.fields, spec)
    lab = commit.Labels.of(spec.codes([str(i["name"]) for i in infos]), spec.outside_code, spec.mode)
    n_threads = _threads(threads)
    bodies = Bodies.of(grid, meshes, [w.priority for w in spec.wires])
    clock(0.06, S_BOUNDARY)
    nb = len(meshes)

    def edge(b: int) -> Callable[[float], None]:
        return lambda f: clock(0.06 + 0.06 * (b + f) / nb, None)

    joint = octree.join_boundaries(bodies, [boundary_parents(grid, mesh, threads=n_threads, tick=edge(b))
                                            for b, mesh in enumerate(meshes)])  # fmt: skip
    pr = {**spec.to_params(), "names": commit.names_of(infos, model)}
    tally = commit.Tally(lab.labels)
    clock(0.12, S_SPLIT)
    if spec.split:
        out = _code_split(engine, store, model, spec, rebuild, grid, bodies, joint, lab, tally, pr, ins, warnings,
                          clock, n_threads, params.get("job_id"))  # fmt: skip
    else:
        out = _code_fields(engine, store, model, spec, grid, bodies, joint, lab, tally, pr, cells, clock, n_threads)
    rows, volume = tally.result()
    return {**out, "codes": rows, "volume": volume, "stages": clock.stages(), "warnings": warnings,
            "events": _events(out["model_id"])}  # fmt: skip


def _split_rows(
    grid: bs.GridV2, bodies: Any, joint: Any, piece: Piece, spec: FillSpec, *, split: bool, n_threads: int,
    clock: Clock, span: tuple[float, float],
) -> Any:  # fmt: skip
    from bf_core.blocks.fill import octree

    return octree.split_rows(
        grid, bodies, joint, piece.p, piece.s, piece.w, m=spec.m, mode=spec.mode, split=split, keep=True,
        threads=n_threads, tick=lambda f: clock(0.12 + 0.78 * f, S_SPLIT), span=span,
    )  # fmt: skip


def _code_split(
    engine: sa.Engine, store: BlobStore, model: Model, spec: FillSpec, rebuild: dict[str, Any] | None,
    grid: bs.GridV2, bodies: Any, joint: Any, lab: commit.Labels, tally: commit.Tally, pr: dict[str, Any],
    ins: dict[str, Any], warnings: list[str], clock: Clock, n_threads: int, job_id: Any = None,
) -> dict[str, Any]:  # fmt: skip
    """`split: true`: the sub-blocks of the rows into a new model (or in place), the fields of the parent inherited."""
    from bf_core.blocks.store import CellsWriterV2

    names = [str(f["name"]) for f in model.fields]
    source = commit.field_source("code", pr)
    fields = _inherited(model) + commit.result_fields(spec.code_field, spec.frac_field, lab.labels, source)
    sub, total, done = grid.sub.sub, model.rows, 0
    with CellsWriterV2(store, grid, fields, sub=sub) as writer:
        for piece in parent_pieces(model, names):
            n = len(piece)
            rows = _split_rows(grid, bodies, joint, piece, spec, split=True, n_threads=n_threads, clock=clock,
                               span=(done / total, (done + n) / total))  # fmt: skip
            done += n
            p, s, w, code, frac, src = rows.p, rows.s, rows.w, rows.code, rows.frac, rows.src
            if not bs.ascending(p, s):  # the children of two rows of one parent interleave: the file wants the order
                o = bs.sort_rows(p, s)
                p, s, w, code, frac, src = p[o], s[o], w[o], code[o], frac[o], src[o]
            label = lab.apply(code)
            values: dict[str, np.ndarray] = {k: v[src] for k, v in piece.cols.items()}
            values[spec.code_field] = label
            if spec.frac_field is not None:
                values[spec.frac_field] = frac
            writer.write(p, values, s if sub else None, w if sub else None)
            tally.add(grid, p, s if sub else None, w if sub else None, label, frac)
        clock(0.92, S_WRITE)
        staged = writer.finish()
    if not staged.cells:
        warnings.append("В выбранном режиме ни один блок не попал в тела: модель без строк.")
    out = _commit(engine, store, staged, spec, rebuild, op="code", params=pr, inputs_=ins, warnings=warnings,
                  name=spec.name or f"{model.name} (кодирование)", job_id=job_id)  # fmt: skip
    return {**out, "rows": staged.cells, "parents": int((staged.v2 or {}).get("parents", 0))}


def _code_fields(
    engine: sa.Engine, store: BlobStore, model: Model, spec: FillSpec, grid: bs.GridV2, bodies: Any, joint: Any,
    lab: commit.Labels, tally: commit.Tally, pr: dict[str, Any], cells_sha: str, clock: Clock, n_threads: int,
) -> dict[str, Any]:  # fmt: skip
    """`split: false`: the field(s) of the same model by the box of every row; the sub-blocks stay."""
    import numpy as np

    from bf_core.blocks.fields_store import FieldsWriter, commit_fields

    assert spec.expected_version is not None
    total, done, sub = model.rows, 0, grid.sub.sub
    frac_on = spec.frac_field is not None
    with FieldsWriter(store, model, spec.expected_version) as fw:  # type: ignore[arg-type]  # ModelView: settable attrs
        fw.add(spec.code_field, "cat", role="code", description=commit.CODE_TEXT, labels=lab.labels)
        if spec.frac_field is not None:
            fw.add(spec.frac_field, "f8", role="frac", description=commit.FRAC_TEXT)
        for ch in model.iter([], chunk=1 << 20):
            piece = Piece(ch.p, ch.s, ch.w, {})
            rows = _split_rows(grid, bodies, joint, piece, spec, split=False, n_threads=n_threads, clock=clock,
                               span=(done / total, (done + ch.n) / total))  # fmt: skip
            done += ch.n
            if len(rows.p) != ch.n or not np.array_equal(rows.src, np.arange(ch.n)):
                raise ApiError(500, "code_rows", "Кодирование изменило число строк модели.",
                               "Сообщите разработчику.")  # fmt: skip
            label = lab.apply(rows.code)
            values: dict[str, np.ndarray] = {spec.code_field: label}
            if spec.frac_field is not None:
                values[spec.frac_field] = rows.frac
            fw.write(values)
            tally.add(grid, ch.p, ch.s if sub else None, ch.w if sub else None, label,
                      rows.frac if frac_on else np.ones(ch.n))  # fmt: skip
        clock(0.92, S_WRITE)
        staged = fw.finish()
    source = commit.field_source("code", pr, [cells_sha])
    with dialect.write_tx(engine) as conn:
        version = commit_fields(conn, store, staged, source={"op": "code", **{k: v for k, v in source.items()
                                                                              if k != "op"}})  # fmt: skip
        name = _name(conn, model.id)
    return {"model_id": model.id, "name": name, "version": version, "rows": model.rows, "parents": model.parents,
            "fields": [spec.code_field] + ([spec.frac_field] if spec.frac_field is not None else []),
            "dropped": []}  # fmt: skip


# --- the handlers ----------------------------------------------------------------------------------------------------


def _user(exc: ApiError) -> Any:
    from bf_core.jobs.context import JobError

    return JobError(exc.code, exc.message, exc.hint, retry=False)


@handler(KIND_FILL, TITLE_FILL, queue="heavy", estimate_memory=memory_mb)
def fill_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        return run_fill(ctx.engine(), ctx.project_folder, {**ctx.params, "job_id": ctx.id}, tick=ctx.tick)
    except ApiError as exc:
        raise _user(exc) from None


@handler(KIND_CODE, TITLE_CODE, queue="heavy", estimate_memory=memory_mb)
def code_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        return run_code(ctx.engine(), ctx.project_folder, {**ctx.params, "job_id": ctx.id}, tick=ctx.tick)
    except ApiError as exc:
        raise _user(exc) from None
