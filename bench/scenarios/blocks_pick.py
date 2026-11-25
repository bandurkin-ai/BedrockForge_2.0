"""The block under a point: the route `POST /viz/blocks/{id}/at` (ТЗ 8.7.6; ADR 0023 «Замеры»: ci — ≤ 50 мс, full —
≤ 100 мс from the cache of columns).

The model of `tools/gen/blockmodel.py` with sub-blocks (`rows` rows; AU, DENSITY, F2, F3, SM, DOM), the route as a
function (`viz_routes.at`) for 40 points inside the model (the same points on both paths), in a process that has
read nothing yet:
  cold_*   the path with no columns in the cache (the first look: a binary search over the file by the groups of
           the layer `P` of the point, only the rows of it are read): `cold_first_ms` the first point (the file is
           opened), `cold_median_ms`, `cold_max_ms` of the rest
  warm_*   the path from the cache of columns (what the window of the display has after the first shell: `idx`, `s`, `w`
           and AU are in the memory): `warm_median_ms`, `warm_max_ms`; `prime_s` — the shell that fills the cache
What is checked (a failure stops the run): every point is found; the block holds the point (its centre ± half of its
size, the sub-block of the row); AU of the answer is the AU of the row of the file (to the bit), on both paths; the
answers of the two paths are the same.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc

POINTS = 40


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        ctx.keep(**bc.sub_model(ctx, int(params["rows"]), fields=int(params.get("fields", 4))))
    finally:
        ctx.close()


def load() -> None:
    bc.warm()


def points(grid: Any, parents: int, n: int = POINTS) -> list[list[float]]:
    """`n` points in the parents 0 … `parents` − 1 (the generator fills the grid layer by layer: the last layer may
    be partial), at 5–95 % of the parent along every axis."""
    nx, ny, _nz = grid.counts
    size = np.asarray(grid.regular())
    rng = np.random.default_rng(5)
    out = []
    for q in rng.integers(0, parents, n):
        ijk = np.array([q % nx, (q // nx) % ny, q // (nx * ny)])
        out.append([float(x) for x in np.asarray(grid.corner) + (ijk + 0.05 + 0.9 * rng.random(3)) * size])
    return out


def ask(ctx: bc.Ctx, mid: int, pts: list[list[float]]) -> tuple[list[dict[str, Any]], list[float]]:
    from bf_core.blocks import viz_routes

    out, ms = [], []
    for pt in pts:
        t0 = time.perf_counter()
        out.append(viz_routes.at(ctx.svc, mid, {"point": pt, "fields": ["AU", "SM"]}))
        ms.append((time.perf_counter() - t0) * 1000.0)
    return out, ms


def check(ctx: bc.Ctx, mid: int, pts: list[list[float]], answers: list[dict[str, Any]]) -> None:
    model = ctx.model(mid)
    for pt, a in zip(pts, answers, strict=True):
        assert a["found"] and a["passed"], (pt, a)
        lo = np.asarray(a["centre"]) - np.asarray(a["size"]) / 2.0
        hi = np.asarray(a["centre"]) + np.asarray(a["size"]) / 2.0
        assert (lo - 1e-9 <= np.asarray(pt)).all() and (np.asarray(pt) < hi + 1e-9).all(), (pt, a["centre"], a["size"])
        row = int(a["row"])
        got = model.columns(["AU", "SM"], row, row + 1)
        assert a["values"]["AU"] == float(got["AU"][0]) and a["values"]["SM"] == float(got["SM"][0]), (a, got)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.blocks import viz_routes

    bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    mid = meta["model_id"]
    info: dict[str, Any] = {"rows": meta["rows"], "parents": meta["parents"]}
    try:
        pts = points(ctx.model(mid).grid, meta["parents"])
        cold, ms = ask(ctx, mid, pts)
        info.update(cold_first_ms=round(ms[0], 1), cold_median_ms=round(bc.median(ms[1:]), 1),
                    cold_max_ms=round(max(ms[1:]), 1))  # fmt: skip
        t0 = time.perf_counter()
        viz_routes.shell(ctx.svc, mid, {"mode": "shell", "colour": {"field": "AU", "kind": "scale"}})
        info["prime_s"] = round(time.perf_counter() - t0, 3)
        warm, ms = ask(ctx, mid, pts)
        info.update(warm_median_ms=round(bc.median(ms), 1), warm_max_ms=round(max(ms), 1))
        check(ctx, mid, pts, cold)
        assert warm == cold, "the cache of columns changes the answer"
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
