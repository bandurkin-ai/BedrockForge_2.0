"""The prototype of a block model: the grid for an extent, no row (ТЗ 8.7.2; ADR 0023 «Замеры»: ci — 2 млн родителей
≤ 0,5 с, full — 20 млн ≤ 2 с).

The job `blocks.prototype` (`jobs.run_prototype`: the box, the grid of blocks 10 × 10 × 5 m with sub-blocks of two
levels in X and Y and one in Z, the model with no row written to the project). What is checked (a failure stops
the run): the parents are the cells of the grid (`counts` of the params), the model has 0 rows and a grid of the
asked size, the corners of the frame are those of the extent.
  prototype_s  the job;  parents — the cells of the grid;  frame_s — the 8 corners of the frame for the window.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import blocks_common as bc

SIZE = [10.0, 10.0, 5.0]
LEVELS = [2, 2, 1]


def prepare(params: dict[str, Any], work: Path) -> None:
    bc.Ctx.create(work).close()


def load() -> None:
    from bf_core.blocks import jobs  # noqa: F401 — the module and numpy imported outside the timing


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.blocks import jobs
    from bf_core.blocks import prototype as pr

    bc.limit_threads(params)
    n = [int(v) for v in params["counts"]]
    ctx = bc.Ctx.open(work)
    try:
        extent = [0.0, 0.0, 0.0, *(n[a] * SIZE[a] for a in range(3))]
        body = {"size": SIZE, "extent": extent, "levels": LEVELS}
        t0 = time.perf_counter()
        out = jobs.run_prototype(ctx.engine, ctx.folder, body)
        info: dict[str, Any] = {"prototype_s": round(time.perf_counter() - t0, 3), "parents": out["parents"]}
        t0 = time.perf_counter()
        frame = pr.make_plan(SIZE, extent, levels=LEVELS).preview()["frame"]
        info["frame_s"] = round(time.perf_counter() - t0, 3)
        assert out["parents"] == n[0] * n[1] * n[2], (out["parents"], n)
        assert out["grid"]["counts"] == n and out["grid"]["size"] == SIZE, out["grid"]
        model = ctx.model(out["model_id"])
        assert model.rows == 0 and model.grid.cells == out["parents"], (model.rows, model.grid.cells)
        assert frame[0] == [0.0, 0.0, 0.0] and frame[7] == extent[3:], (frame[0], frame[7])
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
