"""The regularization of a list of blocks to a grid of 10 m (ADR 0030 «Замеры»; ТЗ 8.7.4, Т8.7-7; подшаг 27.4г).

The model of `blocks_files.write_list_dat` imported as a list (1 million blocks, columns of 10 × 10 m cut along Z into
blocks of random height) to blocks of 10 × 10 × 10 m, the grade AU by tonnes, the density DENSITY: the job
`blocks.regularize` (`jobs.run_regularize`: the boxes of the list against the layers of the new grid, the file written,
the model committed). Target: **≤ 1.5 × `blocks_regularize`**. Checked (a failure stops the run): the totals the job
reports — the volume, the tonnes and the metal of AU before are those of the generator (1e-9 relative), the totals after
are the same (the regularization keeps them: 1e-9), the file of one thread is the file of the params, bit by bit.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import blocks_common as bc
from . import blocks_files

SIZE = [10.0, 10.0, 10.0]
GRADES = [{"field": "AU", "weight": "tonnes"}]


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        ctx.keep(**blocks_files.list_model(ctx, work, int(params["rows"]), int(params.get("fields", 20))))
    finally:
        ctx.close()


def load() -> None:
    bc.warm()


def body(model_id: int) -> dict[str, Any]:
    return {"model_id": model_id, "size": SIZE, "grades": GRADES, "density": "DENSITY"}


def close(a: float, b: float, rel: float = 1e-9) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b), 1.0)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.blocks import jobs
    from bf_core.blocks import regularize as rg

    threads = bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    mid = meta["model_id"]
    info: dict[str, Any] = {"rows": meta["rows"], "threads": threads}
    try:
        t0 = time.perf_counter()
        out = jobs.run_regularize(ctx.engine, ctx.folder, body(mid), threads=threads)
        info["regularize_s"] = round(time.perf_counter() - t0, 3)
        info["rows_out"] = out["rows"]
        info.update({f"stage_{k}_s": round(float(v), 3) for k, v in dict(out["stages"]).items()}
                    if isinstance(out["stages"], dict) else {})  # fmt: skip
        model = ctx.model(mid)
        one = rg.regularize(model, ctx.store, SIZE, grades=GRADES, density="DENSITY", threads=1)
        sha_one = one.staged.file[1]
        one.staged.discard()
        many = rg.regularize(model, ctx.store, SIZE, grades=GRADES, density="DENSITY", threads=threads)
        sha_many = many.staged.file[1]
        many.staged.discard()
        before, after = out["totals"]["before"], out["totals"]["after"]
        assert close(before["volume"], meta["volume"]) and close(after["volume"], meta["volume"]), (before, meta)
        assert close(before["tonnes"], meta["tonnes"]) and close(after["tonnes"], meta["tonnes"]), (before, meta)
        assert close(after["metal"]["AU"], before["metal"]["AU"]), (before["metal"], after["metal"])
        assert 0 < out["rows"] < meta["rows"], (out["rows"], meta["rows"])
        assert sha_one == sha_many, "the file of one thread differs from that of the params"
        info["volume_error"] = abs(after["volume"] - meta["volume"]) / meta["volume"]
        info["metal_error"] = abs(after["metal"]["AU"] - before["metal"]["AU"]) / abs(before["metal"]["AU"])
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
