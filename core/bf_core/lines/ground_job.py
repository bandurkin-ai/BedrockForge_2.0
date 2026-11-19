"""Job `ground.classify` — «Фильтр земли» of a point cloud (ТЗ 7.35 Д-22; ADR 0014 §4, §6).

Parameters {cloud_id, profile, cr, ri, ct, r_b, noise, threads} — `request` makes them from what the command
`ground.classify {cloud_id, profile?, cr?, ri?, ct?, r_b?, noise?}` gives:

* `profile` — "quarry" («Карьер», the default) or "plain" («Равнина»): the constants of the kernel (the slope the
  growth climbs, the steepness of a face) and the defaults of the numbers below. «Свой» of the window is a
  profile with its numbers changed;
* `cr` — the step of the cloth, m (0.1–50); `ri` — its rigidness (1–3); `ct` — a point is ground within this
  distance of the ground surface, m (0.05–5); `r_b` — ground is looked for above and below a face within this
  distance, m (1–20), false — faces are not looked for; `noise` — {r (0.5–20 m), k_min (1–20)}: a point with
  fewer neighbours within r is noise, false — noise is not looked for. A value that is not given is the one of
  the profile; the ranges are checked by `geo/ground.py` (`bad_param`);
* `threads` — by default min(8, cores // workers of the heavy queue); the classes do not depend on it.

The stored parameters are the resolved numbers, so a repeated job gives the same classes whatever the profiles
become. Steps: open the cloud → `geo.ground.warm` («Подготовка ядер»: the kernels are compiled or loaded from
the cache) → `classify_ground` over the columns of the cloud (`xyz` — a map of the file, not a copy; `ret`) →
`stage_values` (`class`, `h` as temp files) → one write transaction (`replace_values`): the new columns, a new
version, `meta.cloud.classes` and `meta.cloud.ground` {params, counts, algo, at, blocks, threads, seconds}.
A cancel or a failure before that transaction leaves the cloud as it was and no file; a cloud whose columns
were replaced while the filter ran is not written over (`cloud_changed`). The job writes every point anew:
classes 1, 2, 3, 4, 5, 7 — a class of the source file does not survive it.
"""

from __future__ import annotations

import os
import time
from contextlib import suppress
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import line_layers, objects
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.jobs.registry import handler

KIND = "ground.classify"
TITLE = "Фильтр земли"
MAX_THREADS = 8
LAYER = "line_layer"
#: ASPRS classes the journal names (the interface has the full table: `modules/lines/classes.ts`).
CLASS_NAMES = {
    0: "не классифицировано, создано",
    1: "не классифицировано",
    2: "земля",
    3: "низкая растительность",
    4: "средняя растительность",
    5: "высокая растительность",
    6: "здание",
    7: "шум",
    9: "вода",
}


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def _num(x: float) -> str:
    return f"{x:.10g}".replace(".", ",")


def _bad(exc: Any) -> ApiError:
    return ApiError(422, exc.code, exc.message, exc.hint)


def _threads(asked: Any, heavy_workers: int) -> int:
    if asked is None:
        asked = (os.cpu_count() or 1) // max(1, int(heavy_workers))
    return max(1, min(MAX_THREADS, int(asked)))


def _resolve(profile: Any, cr: Any, ri: Any, ct: Any, r_b: Any, noise: Any) -> Any:
    """`GroundParams` of the fields of the command: None — as the profile has it, false (`r_b`, `noise`) — off.
    GroundError `bad_param` when a value is out of its range."""
    from bf_core.geo import ground

    try:
        whole = None if ri is None else int(ri)
        if whole is not None and (isinstance(ri, bool) or float(ri) != whole):
            raise ValueError(ri)
    except (TypeError, ValueError):
        raise ground.GroundError(
            "bad_param", "Жёсткость ткани RI: допустимо 1, 2 или 3.", "Исправьте значение параметра."
        ) from None
    try:
        return ground.make_params(str(profile or ground.DEFAULT_PROFILE), cr=cr, ri=whole, ct=ct, r_b=r_b, noise=noise)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ground.GroundError):
            raise
        raise ground.GroundError(
            "bad_param", "Параметры фильтра земли заданы неверно.", "Введите числа в допустимых пределах."
        ) from None


def kernel_params(p: dict[str, Any]) -> Any:
    """`GroundParams` of the stored parameters of a job (there `r_b` null and `noise` false mean «off»)."""
    r_b, noise = p.get("r_b"), p.get("noise")
    return _resolve(
        p.get("profile"), p.get("cr"), p.get("ri"), p.get("ct"), False if r_b is None else r_b, noise or False
    )


def request(
    conn: sa.Connection,
    cloud_id: int,
    *,
    profile: str | None = None,
    cr: float | str | None = None,
    ri: int | None = None,
    ct: float | str | None = None,
    r_b: float | str | bool | None = None,
    noise: dict[str, Any] | bool | None = None,
    threads: int | None = None,
    heavy_workers: int = 1,
) -> dict[str, Any]:
    """Checked parameters of the job `ground.classify` (for the endpoint that starts it); ApiError otherwise.
    Numbers may come as text with a comma («0,5»); `heavy_workers` — of the scheduler (`Scheduler.heavy_workers`)."""
    from bf_core.drillholes.edit import parse_number
    from bf_core.geo import ground
    from bf_core.lines.cloud import cloud_meta, not_a_cloud

    row = conn.execute(
        sa.select(objects.c.name, objects.c.meta, line_layers.c.point_count)
        .join(line_layers, line_layers.c.object_id == objects.c.id)
        .where(objects.c.id == cloud_id, objects.c.kind == LAYER, objects.c.state == "ready")
    ).first()
    if row is None:
        raise ApiError(404, "line_layer_not_found", "Облако точек не найдено.", "Обновите дерево проекта.")
    if cloud_meta(row.meta) is None:
        raise not_a_cloud()
    name = str(profile or ground.DEFAULT_PROFILE)
    cr, ct = parse_number(cr, "Шаг ткани CR"), parse_number(ct, "Порог земли CT")
    if r_b is not False:
        r_b = None if r_b is True else parse_number(r_b, "Радиус поиска земли у грани R_б")
    if isinstance(noise, dict):
        noise = {"r": parse_number(noise.get("r"), "Радиус шума"), "k_min": noise.get("k_min")}
    elif noise is not False:
        noise = None
    try:
        p = _resolve(name, cr, ri, ct, r_b, noise)
    except ground.GroundError as exc:
        raise _bad(exc) from exc
    return {
        "cloud_id": int(cloud_id),
        "profile": name,
        "cr": float(p.cr),
        "ri": int(p.ri),
        "ct": float(p.ct),
        "r_b": None if p.r_b is None else float(p.r_b),
        "noise": False if p.noise_r is None else {"r": float(p.noise_r), "k_min": int(p.noise_k)},
        "threads": _threads(threads, heavy_workers),
        "points": int(row.point_count),
        "name": str(row.name),
    }


def _memory(params: dict[str, Any]) -> int:
    """The coordinates are a map of their file (24 bytes a point, pages of the file cache), the returns, the
    new classes and heights (6), the index of the kernel (≤ 12) and the grids of one block. Measured with the
    scene «карьер» on 4 threads: 1 million points — 332 MB at the peak, 10 million — 711 MB."""
    return max(512, int(int(params.get("points") or 0) * 46 / 2**20) + 400)


def _classes_text(counts: dict[int, int]) -> str:
    if not counts:
        return "точек нет"
    return "; ".join(f"{CLASS_NAMES.get(code, 'класс')} ({code}) — {_n(n)}" for code, n in sorted(counts.items()))


def _params_text(p: Any, title: str) -> str:
    faces = "грани выключены" if p.r_b is None else f"R_б {_num(p.r_b)} м"
    noise = "шум не ищется" if p.noise_r is None else f"шум: радиус {_num(p.noise_r)} м, соседей меньше {p.noise_k}"
    return f"профиль «{title}»: CR {_num(p.cr)} м, RI {p.ri}, CT {_num(p.ct)} м, {faces}, {noise}"


@handler(KIND, TITLE, queue="heavy", estimate_memory=_memory)
def ground_classify(ctx: JobContext) -> dict[str, Any]:
    from dataclasses import asdict

    from bf_core.geo import ground
    from bf_core.lines import cloud as lc
    from bf_core.storage.blobs import BlobStore

    if ctx.project_folder is None:
        raise RuntimeError("ground.classify is a project job")
    p = ctx.params
    engine = ctx.engine()
    store = BlobStore(ctx.project_folder)
    ctx.tick(0.0, "Чтение облака")
    try:
        params = kernel_params(p)
        with dialect.read_tx(engine) as conn:
            cloud = lc.open_cloud(conn, store, int(p["cloud_id"]))
    except (ground.GroundError, ApiError) as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    profile = str(p.get("profile") or ground.DEFAULT_PROFILE)
    threads = _threads(p.get("threads"), 1)
    title = ground.PROFILE_TITLES.get(profile, profile)
    ctx.log(f"Облако «{cloud.name}»: точек {_n(cloud.count)}; {_params_text(params, title)}; потоков {threads}")
    ctx.log(f"До фильтра: {_classes_text(dict(cloud.classes))}")

    ctx.tick(0.0, "Подготовка ядер")
    ground.warm()  # compiled or loaded from the cache, before the threads start
    started = time.perf_counter()
    xyz = cloud.memmap("xyz")
    try:
        result = ground.classify_ground(
            xyz,
            cloud.read("ret"),
            params,
            threads=threads,
            tick=lambda share, stage: ctx.tick(0.02 + 0.88 * max(0.0, min(1.0, share)), stage),
        )
    except (ground.GroundError, ApiError) as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    finally:
        del xyz  # the map of the file is not held while the columns are written (Windows: ADR 0013)
    seconds = round(time.perf_counter() - started, 2)
    blocks = int(result.report.get("blocks") or 0)

    staged = lc.stage_values(
        store, cloud, {"class": result.cls, "h": result.h}, tick=lambda: ctx.tick(0.92, "Запись классов")
    )
    counts = dict(staged.classes or {})
    by_code = {str(code): n for code, n in sorted(counts.items())}
    meta = {
        "params": {"profile": profile, **asdict(params)},
        "counts": by_code,
        "algo": ground.ALGO,
        "at": dialect.utcnow().isoformat(),
        "blocks": blocks,
        "threads": threads,
        "seconds": seconds,
    }
    try:
        ctx.tick(0.98, "Сохранение классов в проект")
        with dialect.write_tx(engine) as conn:
            version = lc.replace_values(conn, store, cloud, staged, ground=meta)
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    finally:
        staged.discard()  # what the transaction took is no longer among the staged files
    ctx.log(f"После фильтра: {_classes_text(counts)}")
    ctx.log(f"Фильтр земли: блоков {_n(blocks)}, потоков {threads}, время {_num(seconds)} с")
    with suppress(JobCancelled):  # the classes are in the project: a cancel this late undoes nothing
        ctx.tick(1.0, "Готово")
    return {
        "cloud_id": cloud.layer_id,
        "version": version,
        "points": cloud.count,
        "classes": by_code,
        "ground": int(counts.get(2, 0)),
        "noise": int(counts.get(7, 0)),
        "blocks": blocks,
        "threads": threads,
        "seconds": seconds,
    }
