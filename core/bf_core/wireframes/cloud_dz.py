"""The map of differences of a point cloud (ADR 0014 §5 «Карта разностей»): the column `dz` — Z of every point
minus the surface of a wireframe under or over it, NaN where there is none — and `meta.cloud.dz`.

Two ways in:

* the job `cloud.dz` {cloud_id, wireframe_id, threads} — `POST /lines/layers/{id}/dz {wireframe_id}`: the
  differences against any wireframe or DTM of the project (`request` checks both and counts the memory);
* `after_build` — the last step of the job `model.dtm` when its sources have point clouds: every such cloud gets
  its `dz` against the surface just built. The surface is in the project by then, so nothing here fails that
  job: a cancel, a cloud that changed meanwhile, a cloud beside the surface — a line of the journal.

Steps: the surface as an index of its triangles by the cells of the plan (`geo/dz_kernels.py`: holes, several
pieces, solids — the nearest surface along the vertical) → the cloud by slices of `ROWS` points on `threads`
threads (the answer does not depend on them) → `stage_values` (a temp file) → one write transaction
(`replace_values`): the new column, `objects.version + 1`, the attribute «Отклонение от ЦТМ, м» and
`meta.cloud.dz` = {wireframe_id, name, at, points, outside, by, count, rms, p95, max}: the points that have a
difference and that have none; the measures of |dz| — root mean square, the 95th percentile (the largest of
the lower 95 %), the largest — by the points of the ground (class 2), `by: "ground"`, or by every point when
the cloud has no ground under the surface, `by: "all"`. A cancel or a failure before the transaction leaves
the cloud as it was and no file; a cloud whose columns were replaced meanwhile is not written over.
Memory: 4 bytes a point for the column, 6 more for the measures, a slice a thread, the index.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import line_layers, objects
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.jobs.registry import handler
from bf_core.lines import cloud as lc

from . import store as wf_store

KIND = "cloud.dz"
TITLE = "Карта разностей"
LAYER = "line_layer"
GROUND = 2
MAX_THREADS = 8
ROWS = 1 << 19  # points of a slice
COUNTING, WRITING, SAVING = TITLE, "Запись отклонений", "Сохранение отклонений в проект"

Tick = Callable[[float, str], None]


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def _num(x: float) -> str:
    return f"{x:.3f}".replace(".", ",")


def _threads(asked: Any, heavy_workers: int = 1) -> int:
    if asked is None:
        asked = (os.cpu_count() or 1) // max(1, int(heavy_workers))
    return max(1, min(MAX_THREADS, int(asked)))


def no_overlap(cloud: str, surface: str) -> ApiError:
    return ApiError(
        422,
        "no_overlap",
        f"Под точками облака «{cloud}» и над ними нет поверхности каркаса «{surface}».",
        "Выберите каркас или ЦТМ, который лежит в плане там же, где облако.",
    )


def request(
    conn: sa.Connection, cloud_id: int, wireframe_id: int, *, threads: int | None = None, heavy_workers: int = 1
) -> dict[str, Any]:
    """Checked parameters of the job `cloud.dz` (for the endpoint that starts it); ApiError otherwise."""
    row = conn.execute(
        sa.select(objects.c.name, objects.c.meta, line_layers.c.point_count)
        .join(line_layers, line_layers.c.object_id == objects.c.id)
        .where(objects.c.id == cloud_id, objects.c.kind == LAYER, objects.c.state == "ready")
    ).first()
    if row is None:
        raise ApiError(404, "line_layer_not_found", "Облако точек не найдено.", "Обновите дерево проекта.")
    if lc.cloud_meta(row.meta) is None:
        raise lc.not_a_cloud()
    surface = wf_store.wireframe_info(conn, int(wireframe_id))
    return {
        "cloud_id": int(cloud_id),
        "wireframe_id": int(wireframe_id),
        "threads": _threads(threads, heavy_workers),
        "points": int(row.point_count),
        "triangles": int(surface["triangle_count"]),
        "name": str(row.name),
        "surface": str(surface["name"]),
    }


def memory_mb(points: int, triangles: int) -> int:
    """The column and the measures (10 bytes a point), the surface and its index (about 150 bytes a triangle
    with what the build of the index holds), a slice of the cloud a thread."""
    return int((points * 10 + triangles * 150) / 2**20) + MAX_THREADS * 32


def _memory(params: dict[str, Any]) -> int:
    return max(512, 300 + memory_mb(int(params.get("points") or 0), int(params.get("triangles") or 0)))


# --- the calculation -------------------------------------------------------------------------------------


def compute(cloud: lc.Cloud, index: Any, *, threads: int = 1, tick: Callable[[float], None] | None = None) -> Any:
    """`dz` of every point of the cloud (float32, the storage order) against an indexed surface. The cloud is
    read by slices; `tick(share)` is called after every slice and may raise to cancel."""
    dz = np.empty(cloud.count, dtype=np.float32)
    slices = [(a, min(ROWS, cloud.count - a)) for a in range(0, cloud.count, ROWS)]

    def one(a: int, n: int) -> None:
        dz[a : a + n] = index.dz(cloud.read("xyz", a, n))

    def step(done: int) -> None:
        if tick is not None:
            tick(done / max(1, len(slices)))

    step(0)
    if threads <= 1 or len(slices) <= 1:
        for done, (a, n) in enumerate(slices):
            one(a, n)
            step(done + 1)
        return dz
    with ThreadPoolExecutor(threads, thread_name_prefix="bf-dz") as pool:
        futures = [pool.submit(one, a, n) for a, n in slices]
        try:
            for done, future in enumerate(futures):
                future.result()
                step(done + 1)
        finally:
            for future in futures:
                future.cancel()  # after a cancel or a failure: the slices not started are not read
    return dz


def measures(cloud: lc.Cloud, dz: np.ndarray, tick: Callable[[], None] | None = None) -> dict[str, Any]:
    """What `meta.cloud.dz` tells about the column: see the module docstring."""
    chosen = np.isfinite(dz)
    with_dz = int(np.count_nonzero(chosen))
    ground = np.zeros(cloud.count, dtype=bool)
    for a in range(0, cloud.count, ROWS):
        if tick is not None:
            tick()
        n = min(ROWS, cloud.count - a)
        ground[a : a + n] = cloud.read("class", a, n) == GROUND
    ground &= chosen
    by = "ground"
    if ground.any():
        chosen = ground
    else:
        by = "all"
    del ground
    out: dict[str, Any] = {"points": with_dz, "outside": cloud.count - with_dz, "by": by, "count": 0}
    values = np.abs(dz[chosen])
    del chosen
    if len(values):
        squares = 0.0
        for a in range(0, len(values), ROWS):
            part = values[a : a + ROWS].astype(np.float64)
            squares += float(np.dot(part, part))
        k = int(0.95 * (len(values) - 1))
        out.update(
            count=len(values),
            rms=round(math.sqrt(squares / len(values)), 4),
            max=round(float(values.max()), 4),
            p95=round(float(np.partition(values, k)[k]), 4),  # reorders `values`: the last use
        )
    return out


def journal_line(cloud: str, surface: str, count: int, m: dict[str, Any]) -> str:
    text = f"Карта разностей: облако «{cloud}» относительно каркаса «{surface}» — точек с отклонением "
    text += f"{_n(m['points'])} из {_n(count)}"
    if m["outside"]:
        text += f" (вне поверхности {_n(m['outside'])})"
    if m["count"]:
        whom = "по земле" if m["by"] == "ground" else "по всем точкам (точек земли под поверхностью нет)"
        text += (
            f"; {whom}, точек {_n(m['count'])}: СКО {_num(m['rms'])} м, P95 {_num(m['p95'])} м, "
            f"наибольшее по модулю {_num(m['max'])} м"
        )
    return text


def apply(
    engine: sa.Engine,
    store: Any,
    cloud: lc.Cloud,
    index: Any,
    wireframe_id: int,
    *,
    threads: int,
    tick: Tick,
) -> tuple[dict[str, Any], str]:
    """`dz` of the cloud against the surface `wireframe_id` (indexed), written to the project: the result of
    the job for this cloud and the line of the journal. `tick(share, stage)` may raise to cancel; ApiError —
    `no_overlap`, `cloud_changed`, `wireframe_not_found` (the wireframe went meanwhile). Nothing is left then."""
    dz = compute(cloud, index, threads=threads, tick=lambda share: tick(0.8 * share, COUNTING))
    m = measures(cloud, dz, tick=lambda: tick(0.8, COUNTING))
    if not m["points"]:
        with dialect.read_tx(engine) as conn:
            raise no_overlap(cloud.name, str(wf_store.wireframe_info(conn, wireframe_id)["name"]))
    staged = lc.stage_values(store, cloud, {"dz": dz}, tick=lambda: tick(0.9, WRITING))
    del dz
    try:
        tick(0.97, SAVING)
        with dialect.write_tx(engine) as conn:
            name = str(wf_store.wireframe_info(conn, wireframe_id)["name"])
            record = {"wireframe_id": int(wireframe_id), "name": name, "at": dialect.utcnow().isoformat(), **m}
            version = lc.replace_values(conn, store, cloud, staged, dz=record)
    finally:
        staged.discard()  # what the transaction took is no longer among the staged files
    result = {
        "cloud_id": cloud.layer_id,
        "wireframe_id": int(wireframe_id),
        "version": version,
        "with_dz": m["points"],
        **{k: v for k, v in m.items() if k != "points"},
        "points": cloud.count,
    }
    return result, journal_line(cloud.name, name, cloud.count, m)


# --- the job ---------------------------------------------------------------------------------------------


@handler(KIND, TITLE, queue="heavy", estimate_memory=_memory)
def cloud_dz(ctx: JobContext) -> dict[str, Any]:
    from bf_core.geo import dz_kernels
    from bf_core.storage.blobs import BlobStore

    if ctx.project_folder is None:
        raise RuntimeError("cloud.dz is a project job")
    p = ctx.params
    engine = ctx.engine()
    store = BlobStore(ctx.project_folder)
    wf_id = int(p["wireframe_id"])
    threads = _threads(p.get("threads"))
    try:
        ctx.tick(0.0, "Чтение облака")
        with dialect.read_tx(engine) as conn:
            cloud = lc.open_cloud(conn, store, int(p["cloud_id"]))
        ctx.tick(0.01, "Чтение каркаса")
        with dialect.read_tx(engine) as conn:
            xyz, tri = wf_store.read_wireframe(conn, store, wf_id)
        ctx.tick(0.03, "Подготовка ядер")
        dz_kernels.warm()  # compiled or loaded from the cache, before the threads start
        ctx.tick(0.05, "Разбор каркаса")
        index = dz_kernels.surface_index(xyz, tri)
        del xyz, tri
        result, line = apply(
            engine, store, cloud, index, wf_id, threads=threads,
            tick=lambda share, stage: ctx.tick(0.08 + 0.9 * max(0.0, min(1.0, share)), stage),
        )  # fmt: skip
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    ctx.log(line)
    ctx.log(f"Потоков {threads}; треугольников поверхности в плане {_n(index.triangles)}")
    with suppress(JobCancelled):  # the column is in the project: a cancel this late undoes nothing
        ctx.tick(1.0, "Готово")
    return {**result, "threads": threads}


def after_build(
    ctx: JobContext,
    engine: sa.Engine,
    store: Any,
    wireframe_id: int,
    xyz: np.ndarray,
    tri: np.ndarray,
    cloud_ids: Sequence[int],
    span: tuple[float, float],
) -> list[dict[str, Any]]:
    """The last step of `model.dtm`: `dz` of the clouds the surface was built from, against it. Returns the
    results of the clouds that got their column; what went wrong is a line of the journal — the surface is in
    the project and the job is done whatever happens here. `span` — the share of the progress of the job."""
    from bf_core.geo import dz_kernels

    out: list[dict[str, Any]] = []
    threads = _threads(None)
    name = "?"
    try:
        ctx.tick(span[0], COUNTING)
        dz_kernels.warm()
        index = dz_kernels.surface_index(xyz, tri)
        for k, cloud_id in enumerate(cloud_ids):
            lo = span[0] + (span[1] - span[0]) * k / len(cloud_ids)
            width = (span[1] - span[0]) / len(cloud_ids)
            try:
                with dialect.read_tx(engine) as conn:
                    cloud = lc.open_cloud(conn, store, int(cloud_id))
                name = cloud.name
                result, line = apply(
                    engine, store, cloud, index, wireframe_id, threads=threads,
                    tick=lambda share, stage, lo=lo, width=width: ctx.tick(lo + width * share, stage),  # type: ignore[misc]
                )  # fmt: skip
            except ApiError as exc:
                ctx.log(f"Карта разностей облака «{name}» не построена: {exc.message} {exc.hint}".strip(), "warn")
                continue
            out.append(result)
            ctx.log(line)
    except JobCancelled:
        ctx.log("Карта разностей не построена: задание отменено. Поверхность сохранена в проекте.", "warn")
    except Exception as exc:  # noqa: BLE001 - the surface is committed: its job must not fail (and be repeated)
        ctx.log(f"Карта разностей не построена: внутренняя ошибка ({type(exc).__name__}).", "warn")
    return out
