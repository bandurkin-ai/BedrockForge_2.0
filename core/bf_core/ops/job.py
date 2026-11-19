"""The job `geometry.operation` (ТЗ 7.23; ADR 0015 §6): {op, params, name, folder, rebuild_of, known, size}.

`geometry.operation` runs in the quick queue; an input over `registry.QUICK_TRIANGLES` triangles goes to the
same handler as `geometry.operation.heavy` in the shared queue (decision 1: the quick queue has one worker).
One attempt: a failed operation fails the same way again.

Steps: inside one read transaction the preconditions again (`registry.precheck` — the objects may have changed
since the request) and the arrays of the sources (`Sources.load`) → the run (`run_meshes`, `run_lines`; between
its stages and the portions of its kernels `tick` reports and may cancel; a call of manifold3d is one stage —
it cannot be cancelled inside, PROGRESS «Э9 Т») → the meshes staged (`wireframes.store.stage_wireframe`; the small
ones without files, then `wireframes.files.stage_files` puts them into one pack `pack:<n>` — the thousand parts of
«На части» are a few blobs, not two a part) and the layers (`lines.store.stage_layers`) into temporary files → one
write transaction that makes every object of the
result: wireframes in the folder of the first source, lines — a line file (`meta.format = "operation"`) with its
layers; each gets `meta.built` (`ops/provenance.py`). A cancel or a failure before that transaction leaves
neither objects nor temporary files; the sources are only read.

The levels of detail are not made here: the window starts `wireframe.check` on the wireframes of the result
(as after an import), and the view shows a part whole until its levels are there (ADR 0015 §8).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.jobs.registry import handler

from . import registry
from .registry import HEAVY_JOB, JOB, Checked

LINE_OPS = frozenset({"stitch", "extrude", "intersection_lines", "contours", "volume_between"})
TITLE = "Операция с каркасами и линиями"
LINE_FILE = "line_file"
MAX_NAME = 200
LOGGED = 20  # wireframes of a result named in the log one by one; the rest — in one line


def _n(x: float) -> str:
    return f"{int(x):,}".replace(",", " ")


def _num(x: float) -> str:
    return f"{x:.6g}".replace(".", ",")


def job_params(
    checked: Checked,
    *,
    name: str | None = None,
    folder: str | None = None,
    rebuild_of: int | None = None,
    known: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    clean = " ".join(str(name or "").split())[:200] or None
    return {
        "op": checked.op,
        "params": checked.params,
        "name": clean,
        "folder": folder,
        "rebuild_of": rebuild_of,
        "known": dict(known or {}),
        "size": checked.size,
    }


def submit(sched: Any, checked: Checked, **kw: Any) -> tuple[str, int]:
    """Queue the job of a checked operation: `geometry.operation` (or `.heavy`), or `model.dtm` for `dtm`."""
    if checked.job == "model.dtm":
        return sched.submit("model.dtm", checked.job_params, title="Построение ЦТМ")  # type: ignore[no-any-return]
    return sched.submit(checked.queue_job, job_params(checked, **kw), title=checked.title)  # type: ignore[no-any-return]


def _memory(params: dict[str, Any]) -> int:
    return registry.memory_mb(params)


@handler(JOB, TITLE, queue="quick", max_attempts=1, estimate_memory=_memory)
def operation(ctx: JobContext) -> dict[str, Any]:
    return run(ctx)


@handler(HEAVY_JOB, TITLE, queue="heavy", max_attempts=1, estimate_memory=_memory)
def operation_heavy(ctx: JobContext) -> dict[str, Any]:
    return run(ctx)


def run(ctx: JobContext) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from .output import kernel_error, kernel_errors

    if ctx.project_folder is None:
        raise RuntimeError("geometry.operation is a project job")
    p = ctx.params
    engine = ctx.engine()
    store = BlobStore(ctx.project_folder)

    def part(start: float, stop: float) -> Callable[[float, str], None]:
        def tick(f: float, m: str = "") -> None:
            ctx.tick(start + (stop - start) * max(0.0, min(1.0, f)), m or None)

        return tick

    ctx.tick(0.0, "Чтение исходных")
    try:
        with dialect.read_tx(engine) as conn:
            checked = registry.precheck(conn, store, p.get("op"), p.get("params"), known=p.get("known"))
            assert checked.src is not None
            checked.src.load(lambda f: ctx.tick(0.1 * f, "Чтение исходных"))
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    module = importlib.import_module(f"bf_core.ops.{'run_lines' if checked.op in LINE_OPS else 'run_meshes'}")
    try:
        out = module.RUNS[checked.op](checked.src, checked.params, part(0.1, 0.8))
    except kernel_errors() as exc:
        raise kernel_error(exc) from exc
    for text in out.warnings:
        ctx.log(text, "warn")
    return _write(ctx, engine, store, checked, out)


def _names(conn: sa.Connection, kind: str) -> set[str]:
    return {str(k) for k in conn.execute(sa.select(objects.c.name_key).where(objects.c.kind == kind)).scalars()}


def _free(name: str, taken: set[str], default: str) -> str:
    """`name`, or «name (2)», «name (3)»… — the first whose `name_key` is not `taken`, which it then is: one query
    for the thousand parts of «На части», not one a part (as `wireframes/store.py::free_name`)."""
    base = " ".join(str(name).split())[:MAX_NAME] or default
    candidate, k = base, 1
    while dialect.name_key(candidate) in taken:
        k += 1
        candidate = base[: MAX_NAME - len(f" ({k})")] + f" ({k})"
    taken.add(dialect.name_key(candidate))
    return candidate


def _write(ctx: JobContext, engine: sa.Engine, store: Any, checked: Checked, out: Any) -> dict[str, Any]:
    from bf_core.lines import store as ls_store
    from bf_core.wireframes import files as wf_files
    from bf_core.wireframes import store as wf_store

    from .provenance import jsonable, record

    p = ctx.params
    name = p.get("name")
    folder = p.get("folder") if p.get("folder") is not None else checked.folder
    result: dict[str, Any] = {
        "op": checked.op, "title": checked.title, "name": name or out.name, "wireframe_ids": [], "layer_ids": [],
        "file_id": None, "report": jsonable(out.report), "warnings": out.warnings, "rebuild_of": p.get("rebuild_of"),
    }  # fmt: skip
    if not out.meshes and not out.layers:  # a report only («Объём» with «только отчёт»)
        with suppress(JobCancelled):
            ctx.tick(1.0, "Готово")
        return result
    meshes: list[Any] = []
    layers: list[Any] = []
    try:
        n = max(1, len(out.meshes))
        for k, m in enumerate(out.meshes):

            def progress(f: float, message: str, k: int = k) -> None:
                ctx.tick(0.8 + 0.1 * (k + max(0.0, min(1.0, f))) / n, message)

            small = wf_files.array_bytes(len(m.vertices), len(m.triangles)) <= wf_files.PACK_LIMIT
            try:  # a small one gets no files yet: it goes into the pack of the result (`stage_files`)
                meshes.append(wf_store.stage_wireframe(store, m.vertices, m.triangles, progress=progress,
                                                       write=not small))  # fmt: skip
            except ApiError as exc:
                raise JobError(exc.code, exc.message, exc.hint) from exc
        wf_files.stage_files(store, meshes, lambda k: ctx.tick(0.9 + 0.02 * k / n, "Запись каркаса"))
        if out.layers:
            layers = ls_store.stage_layers(store, out.layers, lambda _k: ctx.tick(0.93, "Запись линий"))
        ctx.tick(0.95, "Сохранение результата в проект")
        built = record(
            op=checked.op, title=checked.title, params=checked.params, sources=checked.sources, report=out.report,
            tolerances=out.tolerances, rebuild_of=p.get("rebuild_of"),
        )  # fmt: skip
        with dialect.write_tx(engine) as conn:
            taken = _names(conn, wf_store.WIREFRAME) if meshes else set()
            for m, staged in zip(out.meshes, meshes, strict=True):
                given = name if name and len(out.meshes) == 1 else m.name
                wf_id = wf_store.commit_wireframe(
                    conn, store, staged, name=_free(given, taken, "Каркас"), color=m.color, attrs=m.attrs,
                    meta={"built": built}, folder=folder,
                )  # fmt: skip
                result["wireframe_ids"].append(wf_id)
            if layers:
                file_id = ls_store.create_file(
                    conn, store, name=_free(name or out.name, _names(conn, LINE_FILE), "Линии"),
                    meta={"format": "operation", "built": built}, folder=folder,
                )  # fmt: skip
                ids = ls_store.commit_layers(conn, store, layers, file_id=file_id)
                conn.execute(sa.update(objects).where(objects.c.id.in_(ids)).values(meta={"built": built}))
                result["file_id"], result["layer_ids"] = file_id, ids
    except BaseException:
        for one in [*meshes, *layers]:
            one.discard()
        raise
    for wf_id, staged in list(zip(result["wireframe_ids"], meshes, strict=True))[:LOGGED]:
        volume = staged.stats.get("volume")
        ctx.log(
            f"Создан каркас № {wf_id}: треугольников {_n(len(staged.tri))}"
            + (f", объём {_num(volume)} м³" if volume is not None else ", не замкнут")
        )
    if len(meshes) > LOGGED:
        ctx.log(f"Созданы ещё каркасы: {_n(len(meshes) - LOGGED)} (№ {result['wireframe_ids'][LOGGED]}–"
                f"{result['wireframe_ids'][-1]})")  # fmt: skip
    if result["layer_ids"]:
        ctx.log(f"Создан файл линий № {result['file_id']}: слоёв {len(result['layer_ids'])}")
    result["volumes"] = [s.stats.get("volume") for s in meshes]
    with suppress(JobCancelled):  # the result is in the project: a cancel this late undoes nothing
        ctx.tick(1.0, "Готово")
    return result
