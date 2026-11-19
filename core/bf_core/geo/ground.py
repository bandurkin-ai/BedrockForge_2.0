"""Фильтр «земли» облака точек: земля / растительность / шум (ТЗ 7.35 Д-22; ADR 0014 §4).

    classify_ground(xyz, ret=None, params=None, *, threads=1, tick=None, block=BLOCK) -> GroundResult
    make_params(profile="quarry", cr=…, ri=…, ct=…, r_b=…, noise=…) -> GroundParams   («Равнина», «Карьер»)

The task is the one of the cloth simulation filter — W. Zhang, J. Qi, P. Wan, H. Wang, D. Xie, X. Wang, G. Yan,
«An Easy-to-Use Airborne LiDAR Data Filtering Method Based on Cloth Simulation», Remote Sensing 8(6), 501,
2016: a cloth is pressed to the cloud turned upside down, the points near it are ground. The adaptations for an
open pit follow the ideas of Incline Design (MIT, © 2026 Leo Timmins, Lucas Timmins and contributors; the ideas
are listed in ТЗ 7.35 Д-22) — their code was neither read for porting nor ported, this is an implementation of
its own (ADR 0012, ADR 0014 §7).

Stages (the kernels are in `ground_kernels.py`, `ground_cloth.py`, `ground_grow.py`, `ground_faces.py`; the
lattice, its blocks and the strips of the threads — `ground_blocks.py`):
  1 index      points by plan cells of CR/2 (counting sort);
  2 noise      a point with fewer than k_min neighbours within r (3D) → class 7, out of everything below;
  3 obstacle   under every particle of the cloth (step CR) — a plane through the lowest returns around it;
  4 cloth      the rest position of a membrane pressed from below, u ≤ obstacle (a cascade of grids);
  5 seeds      cells whose lowest return is within CT of the cloth along its normal;
  6 growth     from the seeds along the slope: down any drop, up not steeper than α; smooth areas at the edge;
  7 classes    the height h above the ground along the normal: |h| ≤ CT → 2, above → 3 / 4 / 5 (0.5 and 2 m),
               below → 1; noise → 7;
  8 faces      points on a steep flat patch with ground above and below within R_б → 2.

Blocks. The lattice of cells is anchored to the whole origin of the cloud; it is cut into blocks of `block`
particles a side, each computed with a margin (64 m, at least two widths of the cloth) and giving the classes of
the points of its core. A cloud of 50 million points at 20 points/m² is one block.

The step and the cloud. CR must not be finer than the cloud: with a return in fewer than a quarter of the cells
of CR/2 (CR under the mean distance between the points) the filter refuses — GroundError `cloud_too_sparse`
with the step to take; under 35 % the report carries the warning `cloud_sparse`. The share is estimated right
after the index of the cells, by every point of the cloud (`fill_early`), and a cloud too sparse is refused
there — before the noise, the obstacle and the cloth of any block; the share the report and the warning go by
(`fill`) is counted with the noise aside, block by block.

Threads and determinism. The stages run over fixed strips of rows (or of points) on Python threads; the
kernels release the GIL, a strip writes only its own outputs, reductions are counts and maxima. The growth is
one thread in a fixed order. `cls` and `h` do not depend on `threads` bit for bit (ADR 0014, Т-З4).

Input: `xyz` (n, 3) float64 project coordinates (a contiguous array or a memory map of the blob is taken as it
is, anything else is copied once), `ret` (n,) uint8 — return number in the low 4 bits, number of returns in
the high 4 (the cloud blob `ret`), optional: a last return is preferred as the lowest of its cell.
Output: `cls` uint8 — ASPRS codes 1, 2, 3, 4, 5, 7; `h` float32 — height above the ground along the normal,
m (NaN — no ground near). A point with a coordinate that is not finite is left out: class 1, h NaN.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from . import ground_blocks as gb
from . import ground_cloth as gc
from . import ground_faces as gf
from . import ground_grow as gg
from . import ground_kernels as gk
from .ground_blocks import STRIP_POINTS, STRIP_ROWS

BLOCK = 2048  # particles a side of a block
MARGIN_M = 64.0  # margin of a block, m: ≥ 2 widths of the cloth at RI = 3
WIDTHS = {1: 8.0, 2: 16.0, 3: 32.0}  # RI → width of a gap in the data the cloth rises into by ≤ 0.5 m
CLOTH_TOL = 1e-4  # a sweep of the cloth moves no particle further, m
FILL_MIN = 0.25  # share of the cells of CR/2 that hold a return: under it the cloud is too sparse for CR
FILL_LOW = 0.35  # … and under this the report carries a warning
ALGO = "CSF (Zhang et al., 2016) как задача с препятствием; рост по склону; грани — версия 1"

Tick = Callable[[float, str], None] | None

STAGES = ("Индекс ячеек", "Шум", "Препятствие", "Ткань", "Рост по склону", "Классы", "Грани")


class GroundError(ValueError):
    """The filter cannot run: {code, message, hint} in Russian, shown to the user."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


@dataclass(frozen=True)
class GroundParams:
    """Parameters of the filter (ТЗ 7.35 Д-22). `alpha_deg`, `face_deg` and `edge_areas` are constants of a
    profile, preliminary: chosen on synthetic scenes, to be tuned on a real cloud of a pit."""

    cr: float = 1.0  # step of the cloth, m: 0.1–50
    ri: int = 2  # rigidness: 1–3
    ct: float = 0.5  # a point is ground within this distance of the ground surface, m: 0.05–5
    r_b: float | None = 5.0  # faces: ground above and below within this distance in plan, m: 1–20; None — off
    noise_r: float | None = 2.0  # noise: radius, m: 0.5–20; None — off
    noise_k: int = 3  # noise: fewer neighbours than this → class 7: 1–20
    alpha_deg: float = 55.0  # the growth goes up a slope not steeper than this
    face_deg: float = 60.0  # a face is a flat patch steeper than this
    edge_areas: bool = True  # smooth areas at the edge of the survey the cloth did not reach are ground


PROFILES: dict[str, GroundParams] = {
    "plain": GroundParams(ri=3, r_b=None, alpha_deg=30.0),
    "quarry": GroundParams(),
}
PROFILE_TITLES = {"plain": "Равнина", "quarry": "Карьер"}
DEFAULT_PROFILE = "quarry"

_RANGES = {
    "cr": (0.1, 50.0, "Шаг ткани CR", "м"),
    "ct": (0.05, 5.0, "Порог земли CT", "м"),
    "r_b": (1.0, 20.0, "Радиус поиска земли у грани R_б", "м"),
    "noise_r": (0.5, 20.0, "Радиус шума", "м"),
}
_WHOLE = {"ri": (min(WIDTHS), max(WIDTHS), "Жёсткость ткани RI"), "noise_k": (1, 20, "Число соседей для шума")}


def ranges() -> dict[str, dict[str, Any]]:
    """The ranges of ТЗ 7.35 Д-22 as the interface shows them: {parameter: {lo, hi, title, unit, whole}}."""
    out: dict[str, dict[str, Any]] = {}
    for k, (lo, hi, title, unit) in _RANGES.items():
        out[k] = {"lo": lo, "hi": hi, "title": title, "unit": unit, "whole": False}
    for k, (low, high, title) in _WHOLE.items():
        out[k] = {"lo": low, "hi": high, "title": title, "unit": "", "whole": True}
    return out


def _fmt(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def check_params(p: GroundParams) -> None:
    """Raises GroundError `bad_param` when a parameter is outside the range of ТЗ 7.35 Д-22."""
    for name, (lo, hi, title, unit) in _RANGES.items():
        value = getattr(p, name)
        if value is None and name in ("r_b", "noise_r"):
            continue
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
            raise GroundError(
                "bad_param", f"{title}: допустимо от {_fmt(lo)} до {_fmt(hi)} {unit}.", "Исправьте значение параметра."
            )
    if p.ri not in WIDTHS:
        raise GroundError("bad_param", "Жёсткость ткани RI: допустимо 1, 2 или 3.", "Исправьте значение параметра.")
    lo, hi, title = _WHOLE["noise_k"]
    if p.noise_r is not None and not (isinstance(p.noise_k, int) and lo <= p.noise_k <= hi):
        raise GroundError("bad_param", f"{title}: допустимо от {lo} до {hi}.", "Исправьте значение параметра.")
    if not (0.0 < p.alpha_deg < 90.0 and 0.0 < p.face_deg < 90.0):
        raise GroundError("bad_param", "Углы профиля должны быть между 0° и 90°.", "Выберите профиль заново.")


def make_params(profile: str = DEFAULT_PROFILE, **changes: Any) -> GroundParams:
    """Parameters of a profile («plain» — «Равнина», «quarry» — «Карьер») with the fields of the command
    `ground.classify` laid over: cr, ri, ct, r_b, noise ({"r", "k_min"}) and any other field of GroundParams.
    A value of None keeps the profile. `r_b`, `noise`: False — the stage is off; True — it is on, with the
    numbers of the profile or, when the profile has it off, the defaults of GroundParams."""
    if profile not in PROFILES:
        raise GroundError("bad_param", f"Неизвестный профиль «{profile}».", "Выберите «Равнина» или «Карьер».")
    base, default = PROFILES[profile], GroundParams()
    fields: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "noise":
            if value is False:
                fields["noise_r"] = None
            elif isinstance(value, dict) or value is True:
                given = value if isinstance(value, dict) else {}
                if given.get("r") is not None:
                    fields["noise_r"] = float(given["r"])
                elif base.noise_r is None:
                    fields["noise_r"] = default.noise_r
                if given.get("k_min") is not None:
                    fields["noise_k"] = int(given["k_min"])
            elif value is not None:
                raise GroundError("bad_param", "Шум: ожидается {r, k_min} или false.", "Исправьте параметр.")
        elif key == "r_b" and isinstance(value, bool):
            fields["r_b"] = (default.r_b if base.r_b is None else base.r_b) if value else None
        elif value is None:
            continue
        elif key not in GroundParams.__dataclass_fields__:
            raise GroundError("bad_param", f"Неизвестный параметр «{key}».", "Проверьте параметры команды.")
        else:
            fields[key] = int(value) if key in ("ri", "noise_k") else value
    p = replace(base, **fields)
    check_params(p)
    return p


@dataclass
class GroundResult:
    cls: np.ndarray  # (n,) uint8, ASPRS codes
    h: np.ndarray  # (n,) float32, height above the ground along the normal, m; NaN — no ground near
    report: dict[str, Any]


def _as_cloud(xyz: Any, ret: Any) -> tuple[np.ndarray, np.ndarray | None]:
    a = np.asarray(xyz)
    if a.ndim != 2 or a.shape[1] != 3:
        raise GroundError(
            "bad_input", "Облако точек: ожидается массив координат из 3 столбцов.", "Проверьте исходные данные."
        )
    a = np.ascontiguousarray(a, dtype=np.float64)
    r = None
    if ret is not None:
        r = np.ascontiguousarray(np.asarray(ret), dtype=np.uint8)
        if r.shape != (len(a),):
            raise GroundError(
                "bad_input", "Возвраты: число значений не равно числу точек облака.", "Проверьте исходные данные."
            )
    if len(a) >= 2**31:
        raise GroundError("too_many_points", "В облаке больше 2 млрд точек.", "Проредите облако при загрузке.")
    return a, r


def classify_ground(
    xyz: Any,
    ret: Any = None,
    params: GroundParams | None = None,
    *,
    threads: int = 1,
    tick: Tick = None,
    block: int = BLOCK,
) -> GroundResult:
    """Classes and heights above the ground of the points of a cloud (see the module docstring).

    `tick(fraction, stage)` is called between the stages, the blocks and the sweeps of the cloth and may raise
    to cancel. `block` — particles a side of a block (the default suits a job; tests force small blocks).
    The report: counts by class (`classes`), parameters, `blocks`, `threads`, `sweeps` of the cloth by block
    and level, `seconds` by stage, `fill` (the share of cells with a return; `fill_early` — its estimate after the
    index), `warnings` [{code, message, hint}], `seeds`, `ground_cells`, `edge_areas`, `face_points`, `noise`,
    `skipped` (points without coordinates).
    """
    p = params or PROFILES[DEFAULT_PROFILE]
    check_params(p)
    pts, returns = _as_cloud(xyz, ret)
    n = len(pts)
    cls = np.ones(n, np.uint8)
    h = np.full(n, np.nan, np.float32)
    report: dict[str, Any] = {
        "points": n,
        "params": {k: getattr(p, k) for k in GroundParams.__dataclass_fields__},
        "algo": ALGO,
        "threads": max(1, int(threads)),
        "blocks": 0,
        "noise": 0,
        "seeds": 0,
        "ground_cells": 0,
        "edge_areas": 0,
        "face_points": 0,
        "cells_inside": 0,
        "cells_filled": 0,
        "warnings": [],
        "sweeps": [],
        "seconds": dict.fromkeys(STAGES, 0.0),
    }
    pool = gb.Pool(threads)
    try:
        if tick:
            tick(0.0, STAGES[0])
        box = pool.strips(lambda lo, hi: gk.extent(pts, lo, hi), n, STRIP_POINTS)
        box = [b for b in box if b[4]]
        if box:
            lat = gb.lattice(box, p.cr)
            margin = _margin_cells(p)
            span = 2 * int(block)
            blocks = gb.blocks(lat, span, margin)
            lists = gb.split(pts, lat, len(blocks), span, pool)
            live = [k for k, ids in enumerate(lists) if ids is not None]
            index = _early_fill(pts, lists, live, lat, blocks, p, pool, tick, report)
            for done, k in enumerate(live):
                cand = gb.candidates(k, lists, lat, span, -(-margin // span))

                def step(part: float, stage: str, done: int = done) -> None:
                    if tick:
                        tick((done + part) / len(live), stage)

                _run_block(pts, returns, cand, lat, blocks[k], p, pool, step, cls, h, report, index)
                index = None
                report["blocks"] += 1
        report["skipped"] = n - sum(int(b[4]) for b in box)
    finally:
        pool.close()
    _check_fill(report, p)
    counts = np.bincount(cls, minlength=8)
    report["classes"] = {int(code): int(counts[code]) for code in (1, 2, 3, 4, 5, 7)}
    report["noise"] = int(counts[7])
    report["seconds"] = {k: round(v, 3) for k, v in report["seconds"].items()}
    if tick:
        tick(1.0, STAGES[-1])
    return GroundResult(cls, h, report)


def _sparse(filled: int, inside: int, p: GroundParams) -> tuple[float, GroundError, dict[str, str]]:
    """The share of the cells with a return, the refusal for a cloud too sparse and the warning for a sparse one."""
    fill = max(filled / inside, 1e-6)
    # cells of the step that would hold a return in 40 % of them: the share is 1 − exp(−density · cell²)
    step = math.ceil(p.cr * math.sqrt(math.log(1.0 - 0.4) / math.log(1.0 - fill)) * 10.0) / 10.0
    hint = f"Задайте шаг ткани CR не меньше {_fmt(step)} м."
    cells = f"CR = {_fmt(p.cr)} м"
    share = f"{max(round(100.0 * fill), 1)} %"
    refusal = GroundError(
        "cloud_too_sparse", f"Облако слишком редкое для шага ткани {cells}: точки есть только в {share} ячеек.", hint
    )
    warning = {
        "code": "cloud_sparse",
        "message": f"Облако редкое для шага ткани {cells} (точки в {share} ячеек): "
        "земля под растительностью может быть найдена не полностью.",
        "hint": hint,
    }
    return fill, refusal, warning


def _check_fill(report: dict[str, Any], p: GroundParams) -> None:
    """The lattice must not be finer than the cloud: with a return in fewer than a quarter of the cells of CR/2
    the growth has nothing to go by and the planes nothing to be fitted to — an error with the step to take;
    under `FILL_LOW` — a warning in the report."""
    inside, filled = report.pop("cells_inside"), report.pop("cells_filled")
    report["fill"] = round(filled / inside, 3) if inside else None
    if not inside or filled >= FILL_LOW * inside:
        return
    fill, refusal, warning = _sparse(filled, inside, p)
    if fill < FILL_MIN:
        raise refusal
    report["warnings"].append(warning)


def _early_fill(pts, lists, live, lat, blocks, p, pool, tick, report):  # type: ignore[no-untyped-def]
    """The share of the cells with a point right after the index (`ground_blocks.presence`): under `FILL_MIN`
    — the refusal of `_check_fill`, now, and not after the noise, the obstacle and the cloth of every block.
    Returns the index of the only block of a cloud: stage 1 of `_run_block` is done."""
    t = time.perf_counter()
    inside, filled, index = gb.presence(
        pts, lists, live, lat, blocks, pool, (lambda: tick(0.0, STAGES[0])) if tick else None
    )
    report["fill_early"] = round(filled / inside, 3) if inside else None
    report["seconds"][STAGES[0]] += time.perf_counter() - t
    if inside and filled < FILL_MIN * inside:
        raise _sparse(filled, inside, p)[1]
    return index if len(blocks) == 1 else None


def cloth_width(p: GroundParams) -> float:
    """W, m: the cloth rises into a gap of this width in the data by 0.5 m at most (p = 4 / W²). At a coarse
    step the cloth keeps at least four particles across it."""
    return max(WIDTHS[p.ri], 4.0 * p.cr)


def _margin_cells(p: GroundParams) -> int:
    margin = max(MARGIN_M, 2.0 * cloth_width(p))
    return 2 * int(math.ceil(margin / p.cr))


def block_cells(width: float, height: float, p: GroundParams, block: int = BLOCK) -> int:
    """The cells of the largest block of a cloud of this plan extent, m, with its margin: what the grids of a
    block are sized by (the estimate of the memory of the job). Never under the real number."""
    span, margin = 2 * int(block), _margin_cells(p)

    def widest(extent: float) -> int:
        if not extent < (3 * span + 2 * margin) * p.cr:  # a block with the whole margin on both sides
            return span + 2 * margin
        n = 2 * (int(math.floor(max(float(extent), 0.0) / p.cr)) + 2 + 2 * gb.PAD)  # the lattice, at most
        return max(min(c0 + span + margin, n) - max(c0 - margin, 0) for c0 in range(0, n, span))

    return widest(width) * widest(height)


def _run_block(pts, returns, cand, lat, blk, p, pool, step, cls, h, report, index=None) -> None:  # type: ignore[no-untyped-def]
    """One block: stages 1–8 over its cells (with the margin), then the classes of the points of its core.
    `index` — stage 1 when it is already done (`_early_fill`)."""
    seconds = report["seconds"]
    t = time.perf_counter()

    def done(stage: int, part: float) -> None:
        nonlocal t
        now = time.perf_counter()
        seconds[STAGES[stage]] += now - t
        t = now
        step(part, STAGES[min(stage + 1, len(STAGES) - 1)])

    grid = (lat.ox, lat.oy, lat.c, blk.cx0, blk.cy0, blk.ncx, blk.ncy)
    start, order = index or gb.index(pts, cand, grid, pool)
    del cand, index
    pcls = np.zeros(len(order), np.uint8)
    ph = np.full(len(order), np.nan, np.float32)
    done(0, 0.1)
    cells = _lowest(pts, returns, order, start, pcls, grid, p, pool)
    done(1, 0.3)
    hob, mask = _obstacle(cells, grid, p, pool)
    done(2, 0.4)
    width = cloth_width(p)
    u, sweeps = gc.solve(
        hob,
        mask,
        4.0 / (width * width) * p.cr * p.cr,
        tol=CLOTH_TOL,
        rows=pool.even if pool.threads > 1 else None,
        tick=lambda: step(0.5, STAGES[3]),
    )
    del hob
    report["sweeps"].append(sweeps)
    done(3, 0.6)
    state, gx, gy = _growth(cells, u, mask, grid, p, pool, report)
    done(4, 0.75)
    g, gn, weak = _planes(cells, state, gx, gy, grid, p, pool)
    del cells
    ncx, ncy = blk.ncx, blk.ncy
    pool.strips(
        lambda a, b: gg.classify_rows(
            pts, order, start, state, g, gx, gy, gn, weak, u, mask, ncx, ncy, a, b,
            lat.ox, lat.oy, lat.c, p.cr, blk.cx0, blk.cy0, p.ct, pcls, ph,
        ),
        ncy,
        STRIP_ROWS,
    )  # fmt: skip
    done(5, 0.9)
    if p.r_b is not None:
        report["face_points"] += _faces(pts, order, start, pcls, ph, (state, g, gx, gy), grid, blk.core, p, pool)
    col0, row0, col1, row1 = blk.core
    pool.strips(
        lambda a, b: gk.store_rows(order, start, pcls, ph, ncx, row0 + a, row0 + b, col0, col1, cls, h),
        row1 - row0,
        STRIP_ROWS,
    )
    done(6, 1.0)


def _lowest(pts, returns, order, start, pcls, grid, p, pool):  # type: ignore[no-untyped-def]
    """Stage 2: noise (class 7 in pcls) and the lowest return of every cell → (cmin, mx, my, mz)."""
    ox, oy, c, cx0, cy0, ncx, ncy = grid
    if p.noise_r is not None:
        radius, k_min = float(p.noise_r), int(p.noise_k)
        pool.strips(
            lambda a, b: gk.noise_rows(pts, order, start, ncx, ncy, a, b, ox, oy, c, cx0, cy0, radius, k_min, pcls),
            ncy,
            STRIP_ROWS,
        )
    cmin = np.empty(ncx * ncy, np.int32)
    mx = np.zeros(ncx * ncy, np.float32)
    my = np.zeros(ncx * ncy, np.float32)
    mz = np.zeros(ncx * ncy, np.float64)
    use_ret = returns is not None
    rets = returns if use_ret else np.zeros(1, np.uint8)
    pool.strips(
        lambda a, b: gk.minima_rows(
            pts, rets, use_ret, order, start, pcls, ncx, a, b, ox, oy, c, cx0, cy0, cmin, mx, my, mz
        ),
        ncy,
        STRIP_ROWS,
    )
    return cmin, mx, my, mz


def _obstacle(cells, grid, p, pool):  # type: ignore[no-untyped-def]
    """Stage 3: the obstacle under every particle and the particles of the system → (hob, mask)."""
    cmin, mx, my, mz = cells
    c, ncx, ncy = grid[2], grid[5], grid[6]
    npx, npy = ncx // 2 + 1, ncy // 2 + 1
    hob = np.zeros((npy, npx), np.float64)
    mask = np.zeros((npy, npx), np.uint8)
    pool.strips(
        lambda a, b: gc.obstacle_rows(cmin, mx, my, mz, ncx, ncy, npx, a, b, c, p.cr, p.ct / 5.0, hob, mask),
        npy,
        STRIP_ROWS,
    )
    return hob, mask


def _growth(cells, u, mask, grid, p, pool, report):  # type: ignore[no-untyped-def]
    """Stages 5–6: seeds by the cloth, the growth along the slope → (state, gx, gy) of the cells."""
    cmin, mx, my, mz = cells
    c, ncx, ncy = grid[2], grid[5], grid[6]
    state = np.zeros(ncx * ncy, np.uint8)
    gx = np.zeros(ncx * ncy, np.float32)
    gy = np.zeros(ncx * ncy, np.float32)
    seeds = pool.strips(
        lambda a, b: gc.seed_rows(cmin, mx, my, mz, u, ncx, a, b, c, p.cr, p.ct, state, gx, gy), ncy, STRIP_ROWS
    )
    report["seeds"] += int(sum(seeds))
    edge = np.zeros(ncx * ncy, np.uint8)
    counts = pool.strips(lambda a, b: gg.edge_rows(cmin, mask, ncx, a, b, edge), ncy, STRIP_ROWS)
    inside, filled = sum(int(k[0]) for k in counts), sum(int(k[1]) for k in counts)
    report["cells_inside"] += inside
    report["cells_filled"] += filled
    min_cells = min_edge = 0
    if p.edge_areas:
        # the share of cells that hold a return: a sparse cloud fills an area with fewer of them
        fill = filled / inside if inside else 1.0
        width = cloth_width(p)
        min_cells = max(int(width * width / (c * c) * fill), 16)
        min_edge = max(int(0.5 * width / c * fill), 4)
    tan_up = math.tan(math.radians(p.alpha_deg))
    ground, areas, _cells = gg.grow(
        cmin, mx, my, mz, state, gx, gy, edge, ncx, ncy, c, p.ct, tan_up, min_cells, min_edge
    )
    report["ground_cells"] += int(ground)
    report["edge_areas"] += int(areas)
    return state, gx, gy


def _faces(pts, order, start, pcls, ph, ground, grid, core, p, pool) -> int:  # type: ignore[no-untyped-def]
    """Stage 8: the points of steep faces with ground above and below become ground (pcls, ph of the core)."""
    ox, oy, c, cx0, cy0, ncx, ncy = grid
    col0, row0, col1, row1 = core
    r_b = float(p.r_b)
    kb = int(math.ceil(r_b / c))
    reach = int(math.ceil(2.5 * p.cr / c)) + 1  # the widest patch of a voxel
    box = (max(col0 - reach, 0), max(row0 - reach, 0), min(col1 + reach, ncx), min(row1 + reach, ncy))
    high, low, longest = gf.relief(ground[0], ground[1], start, ncx, ncy, kb)
    lattice = (ncx, ncy, ox, oy, c)
    vox = gf.voxels(pts, order, start, pcls, high, low, longest, lattice, kb, p.ct, box)
    if not len(vox[1]):
        return 0
    par = (p.cr, p.ct, r_b, math.cos(math.radians(p.face_deg)))
    hills, cell0, cols = (high, low), (cx0, cy0), (col0, col1)
    turned = pool.strips(
        lambda a, b: gf.face_rows(
            pts, order, start, pcls, ph, ground, hills, vox, lattice, cell0, row0 + a, row0 + b, cols, box, par
        ),
        row1 - row0,
        STRIP_ROWS,
    )
    return int(sum(turned))


def _planes(cells, state, gx, gy, grid, p, pool):  # type: ignore[no-untyped-def]
    """Stage 7, the surface: the plane of every ground cell → (g, gn, weak); gx, gy are rewritten."""
    cmin, mx, my, mz = cells
    c, ncx, ncy = grid[2], grid[5], grid[6]
    g = np.empty(ncx * ncy, np.float64)
    gn = np.empty(ncx * ncy, np.float32)
    weak = np.empty(ncx * ncy, np.uint8)
    pool.strips(
        lambda a, b: gg.plane_rows(cmin, mx, my, mz, state, gx, gy, ncx, ncy, a, b, c, p.ct, g, gn, weak),
        ncy,
        STRIP_ROWS,
    )
    return g, gn, weak


def warm() -> None:
    """Compiles (or loads from the cache) every kernel on a tiny cloud — before threads start, outside timings."""
    rng = np.random.default_rng(1)
    xy = rng.random((4000, 2)) * 24.0
    pts = np.column_stack([xy, np.where(xy[:, 0] > 12.0, 6.0, 0.0) + rng.normal(0.0, 0.01, len(xy))])
    wall = np.column_stack([np.full(1500, 12.0), rng.random(1500) * 24.0, rng.random(1500) * 6.0])
    cloud = np.vstack([pts, wall]) + [1000.0, 2000.0, 50.0]
    ret = np.full(len(cloud), 0x11, np.uint8)
    classify_ground(cloud, ret, PROFILES["quarry"], block=8)
    classify_ground(cloud, None, PROFILES["plain"])
    # a memory map of a blob is read-only — another signature of every kernel that takes the points
    cloud.setflags(write=False)
    ret.setflags(write=False)
    classify_ground(cloud, ret, PROFILES["quarry"], block=8)
