"""The curve «tonnage — grade» of 100 cut-offs, one element (ТЗ 8.10 «Время»: 100 бортов на 20 млн блоков ≤ 2 с;
ADR 0027 «Замеры», подшаг 20.11).

The model of `resources_report` (`resources_common`). The job `resources.report` (`job.run_report`: a preview) for
100 cut-offs of AU in the table (0 … 4,95 g/t, step 0,05), the element AU, no keys, no restrictor, and the curve of
100 points from the minimum to the maximum of AU — the table of the total and the curve share one pass:
  curve_s      the whole job
  again_s      the same once more
  read_s, kernel_s, control_s   the stages of the job
What is checked (a failure stops the run), by numpy over the columns: every row of the table (100 rows: the blocks
exactly, the volume, the tonnes, ḡ and the sum of the grade 1e-9; the blocks with no AU are in none of them) and the
100 points of the curve (the same); the control; `again` is the same answer bit by bit.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import blocks_common as bc
from . import resources_common as rc

POINTS = 100
CUTOFFS = [round(i * 0.05, 2) for i in range(POINTS)]


def load() -> None:
    rc.load()  # the hook of `bench.run`: the kernels loaded, not timed


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        ctx.keep(**rc.prepare_model(ctx, int(params["rows"])))
    finally:
        ctx.close()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    mid = meta["model_id"]
    info: dict[str, Any] = {"rows": meta["rows"]}
    try:
        body = rc.request(mid, elements=[{"field": "AU"}], cutoffs=CUTOFFS, curve=POINTS)
        ans, info["curve_s"] = rc.run_job(ctx, body)
        again, info["again_s"] = rc.run_job(ctx, body)
        for k, v in (ans["seconds"] or {}).items():
            info[f"{k}_s"] = v
        t0 = time.perf_counter()
        edges = [*CUTOFFS, *ans["curve"]["cutoffs"]]
        ref = rc.reference(ctx.model(mid), edges, 0, lambda ch: [], vol=rc.volume_of(5.0), restrict=False,
                           elements=("AU",))  # fmt: skip  (no keys: only the total)
        info["reference_s"] = time.perf_counter() - t0
        info["table_rows"] = rc.check_rows(ans, ref, CUTOFFS, lambda g: [], 0)
        rc.check_curve(ans, ref, POINTS)
        assert ans["control"]["sum_groups"]["ok"] or not ans["control"]["sum_groups"]["checked"]
        assert rc.same_bits(ans, again), "again"
        assert ans["rows"][0]["blocks"] <= meta["rows"] and ans["rows"][0]["total"], ans["rows"][0]
        info["blocks"] = ans["rows"][0]["blocks"]
    finally:
        ctx.close()
    for k in ("curve_s", "again_s", "reference_s"):
        info[k] = round(info[k], 3)
    info["peak_mb"] = bc.peak_mb()
    return info
