"""Job `ground.classify` — «Фильтр земли» of a point cloud (ТЗ 7.35 Д-22; ADR 0014 §4, §6).

Parameters {cloud_id, profile, cr, ri, ct, r_b, noise, threads} — `request` makes them from what the command
`ground.classify {cloud_id, profile?, cr?, ri?, ct?, r_b?, noise?}` gives:

* `profile` — "quarry" («Карьер», the default) or "plain" («Равнина»): the constants of the kernel (the slope the
  growth climbs, the steepness of a face) and the defaults of the numbers below. «Свой» of the window is a
  profile with its numbers changed;
* `cr` — the step of the cloth, m (0.1–50); `ri` — its rigidness (1–3); `ct` — a point is ground within this
  distance of the ground surface, m (0.05–5); `r_b` — ground is looked for above and below a face within this
  distance, m (1–20); `noise` — {r (0.5–20 m), k_min (1–20)}: a point with fewer neighbours within r is noise.
  The ranges are checked by `geo/ground.py` (`bad_param`);
* `threads` — by default min(8, cores // workers of the heavy queue); the classes do not depend on it.

One meaning of an empty value and of a switch, in the command, in the body of the request and in the stored
parameters alike: **null** (or a value that is not given, an empty text) — as the profile has it; **false**
(`r_b`, `noise`) — the stage is off: faces, or noise, are not looked for; **true** — the stage is on with the
numbers of the profile, or with the defaults of the kernel when the profile has it off (`r_b` over «Равнина»).
`GroundParams` and `meta.cloud.ground.params` are not a request but what the kernel ran with: there a stage
that is off has no number (None).

The stored parameters are the resolved numbers (and false for a stage that is off), so a repeated job gives
the same classes whatever the profiles become; `cells` — the cells of the largest block of the cloud, for the
estimate of the memory. `profiles()` is what `GET /lines/ground/profiles` answers: the window «Фильтр земли»
shows the profiles and the ranges of the kernel, not a copy of them.

Steps: open the cloud → `geo.ground.warm` («Подготовка ядер»: the kernels are compiled or loaded from the
cache) → `classify_ground` over the columns of the cloud (`xyz` — a map of the file, not a copy; `ret`) →
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
#: Memory of the filter, bytes: a point — the coordinates as a map of their file (24), the return, the new
#: class and height (6), the index of the kernel (≤ 12) and the split into blocks; a cell of CR/2 of a block —
#: its grids (measured: 51 at the peak of a block of 16 million cells, 6 million points — 1.16 GB in all).
BYTES_A_POINT, BYTES_A_CELL = 46, 52
#: Cells of a block when the parameters do not tell them: a full block of CR = 1 m, (2 · 2048 + 2 · 128)².
FULL_BLOCK_CELLS = 18_939_904
#: What a profile is for; {alpha} — the slope its growth climbs (a constant of the kernel).
PROFILE_HINTS = {
    "quarry": "Уступы, бермы, откосы и бровки: ткань мягче, рост по склону до {alpha}°, "
    "вертикальные грани с землёй сверху и снизу — земля",
    "plain": "Пологий рельеф: жёсткая ткань, рост по склону до {alpha}°, грани не ищутся",
}
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
    """`GroundParams` of the fields of a request: None — as the profile has it; `r_b`, `noise`: false — off,
    true — on (the numbers of the profile, or the defaults of the kernel when the profile has the stage off).
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
    """`GroundParams` of the stored parameters of a job: the fields of a request (false — off, null — the
    profile's), resolved by `request`."""
    return _resolve(p.get("profile"), p.get("cr"), p.get("ri"), p.get("ct"), p.get("r_b"), p.get("noise"))


def profiles() -> dict[str, Any]:
    """`GET /lines/ground/profiles`: the profiles of the kernel in the fields of a request (false — the stage
    is off), with the constants of the profile, its title and what it is for; `default` — the profile of a
    request that names none; `defaults` — the numbers of a stage switched on over a profile that has it off;
    `ranges` — {lo, hi, title, unit, whole} of every number (`geo.ground.ranges`)."""
    from bf_core.geo import ground

    def fields(p: Any) -> dict[str, Any]:
        return {
            "cr": float(p.cr),
            "ri": int(p.ri),
            "ct": float(p.ct),
            "r_b": False if p.r_b is None else float(p.r_b),
            "noise": False if p.noise_r is None else {"r": float(p.noise_r), "k_min": int(p.noise_k)},
        }

    def one(name: str) -> dict[str, Any]:
        p = ground.PROFILES[name]
        return {
            "id": name,
            "title": ground.PROFILE_TITLES[name],
            "hint": PROFILE_HINTS[name].format(alpha=_num(p.alpha_deg)),
            **fields(p),
            "alpha_deg": float(p.alpha_deg),
            "face_deg": float(p.face_deg),
            "edge_areas": bool(p.edge_areas),
        }

    names = [ground.DEFAULT_PROFILE, *sorted(set(ground.PROFILES) - {ground.DEFAULT_PROFILE})]
    return {
        "default": ground.DEFAULT_PROFILE,
        "profiles": [one(name) for name in names],
        "defaults": fields(ground.GroundParams()),
        "ranges": ground.ranges(),
    }


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
    meta = cloud_meta(row.meta)
    if meta is None:
        raise not_a_cloud()
    name = str(profile or ground.DEFAULT_PROFILE)
    cr, ct = parse_number(cr, "Шаг ткани CR"), parse_number(ct, "Порог земли CT")
    if not isinstance(r_b, bool):
        r_b = parse_number(r_b, "Радиус поиска земли у грани R_б")
    if isinstance(noise, dict):
        noise = {"r": parse_number(noise.get("r"), "Радиус шума"), "k_min": noise.get("k_min")}
    elif not isinstance(noise, bool):
        noise = None
    try:
        p = _resolve(name, cr, ri, ct, r_b, noise)
    except ground.GroundError as exc:
        raise _bad(exc) from exc
    try:
        box = [float(v) for v in meta["bbox"]]
        cells = ground.block_cells(box[3] - box[0], box[4] - box[1], p)
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):  # the job itself tells a broken cloud
        cells = FULL_BLOCK_CELLS
    return {
        "cloud_id": int(cloud_id),
        "profile": name,
        "cr": float(p.cr),
        "ri": int(p.ri),
        "ct": float(p.ct),
        "r_b": False if p.r_b is None else float(p.r_b),
        "noise": False if p.noise_r is None else {"r": float(p.noise_r), "k_min": int(p.noise_k)},
        "threads": _threads(threads, heavy_workers),
        "points": int(row.point_count),
        "cells": int(cells),
        "name": str(row.name),
    }


def _memory(params: dict[str, Any]) -> int:
    """`BYTES_A_POINT` a point and `BYTES_A_CELL` a cell of the largest block (`cells` of the parameters;
    without it — four cells a point, the sparsest cloud the filter takes, a full block at most) over the
    libraries. Measured on 4 threads: the scene «карьер», 20 points/m² — 1 million points 332 MB at the peak,
    10 million 711 MB (2 million cells); 6 million points in a block of 16 million cells — 1.16 GB."""
    points = int(params.get("points") or 0)
    cells = params.get("cells")
    cells = min(4 * points, FULL_BLOCK_CELLS) if cells is None else int(cells)
    return max(512, int((points * BYTES_A_POINT + cells * BYTES_A_CELL) / 2**20) + 400)


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
    for w in result.report.get("warnings") or []:  # {code, message, hint}: a sparse cloud… (geo/ground.py)
        ctx.log(f"{w['message']} {w['hint']}".strip(), "warn")
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
