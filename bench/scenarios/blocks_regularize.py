"""The regularization of a block model with sub-blocks to a grid of larger blocks (ТЗ 8.7.4; Т8.7-7; ADR 0023 §10 and
«Замеры»: ci — 2 млн строк ≤ 8 с, full — «20 → 2,5 млн ≤ 60 с»).

The model of `tools/gen/blockmodel.py` with sub-blocks (`rows` rows, 25 % of the parents split into eight; blocks of
10 × 10 × 5 m; AU, DENSITY, F2, F3, SM, DOM) to blocks of twice the size on every axis (20 × 20 × 10 m: eight parents —
or their sub-blocks — to a new block), the grade AU by tonnes, the density DENSITY, the category DOM by the mode:
  regularize_s   the job `blocks.regularize` (`jobs.run_regularize`: the overlaps by the layers of the new grid on the
                 threads of the params, the file written, the model committed);  its `stages` — read, overlap, write
  t1_s           `regularize.regularize` of one thread (the file dropped: no commit);  `rows_out` — the rows made
What is checked (a failure stops the run): the totals the job reports — the volume, the tonnes and the metal of AU
before are those of the rows by numpy over the model (1e-9 relative: Neumaier's sums), the totals after are the same
(the regularization keeps the volume, the tonnage and the metal: 1e-9); the number of the rows is less than the rows
of the model; the file of one thread is the file of the params, bit by bit (8.0.2: SHA-256).
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from . import blocks_common as bc

SIZE = [20.0, 20.0, 10.0]
GRADES = [{"field": "AU", "weight": "tonnes"}]


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        ctx.keep(**bc.sub_model(ctx, int(params["rows"]), fields=int(params.get("fields", 4))))
    finally:
        ctx.close()


def load() -> None:
    """The kernels of the step `blocks` and those of the category mode of the job (a tiny model of its own)."""
    import tempfile

    from bf_core.blocks import jobs

    bc.warm()
    with tempfile.TemporaryDirectory(prefix="bf-bench-warm-") as tmp:
        ctx = bc.Ctx.create(Path(tmp))
        try:
            model = bc.sub_model(ctx, 20_000)
            jobs.run_regularize(ctx.engine, ctx.folder, {**body(model["model_id"])}, threads=1)
        finally:
            ctx.close()


def body(model_id: int) -> dict[str, Any]:
    return {"model_id": model_id, "size": SIZE, "grades": GRADES, "density": "DENSITY", "categories": ["DOM"]}


def reference(ctx: bc.Ctx, mid: int) -> dict[str, float]:
    """Σ volume, Σ tonnes, Σ AU·tonnes over the rows of the model by numpy (portions, the sums by `math.fsum`)."""
    from bf_core.blocks import geometry as geo

    model = ctx.model(mid)
    vol: list[float] = []
    tonnes: list[float] = []
    metal: list[float] = []
    for ch in model.iter(["AU", "DENSITY"], bc.PORTION):
        v = geo.row_volumes(model.grid, ch.p, ch.s, ch.w, None)
        t = v * ch.cols["DENSITY"]
        vol.append(math.fsum(v.tolist()))
        tonnes.append(math.fsum(t.tolist()))
        metal.append(math.fsum((ch.cols["AU"] * t).tolist()))
    return {"volume": math.fsum(vol), "tonnes": math.fsum(tonnes), "metal": math.fsum(metal)}


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
        t0 = time.perf_counter()
        one = rg.regularize(model, ctx.store, SIZE, grades=GRADES, density="DENSITY", categories=["DOM"], threads=1)
        info["t1_s"] = round(time.perf_counter() - t0, 3)
        sha_one = one.staged.file[1]
        one.staged.discard()
        many = rg.regularize(model, ctx.store, SIZE, grades=GRADES, density="DENSITY", categories=["DOM"],
                             threads=threads)  # fmt: skip
        sha_many = many.staged.file[1]
        many.staged.discard()
        want = reference(ctx, mid)
        before, after = out["totals"]["before"], out["totals"]["after"]
        assert close(before["volume"], want["volume"]) and close(after["volume"], want["volume"]), (before, want)
        assert close(before["tonnes"], want["tonnes"]) and close(after["tonnes"], want["tonnes"]), (before, want)
        assert close(before["metal"]["AU"], want["metal"]) and close(after["metal"]["AU"], want["metal"]), want
        assert 0 < out["rows"] < meta["rows"], (out["rows"], meta["rows"])
        assert sha_one == sha_many, "the file of one thread differs from that of the params"
        info["volume_error"] = abs(after["volume"] - want["volume"]) / want["volume"]
        info["metal_error"] = abs(after["metal"]["AU"] - want["metal"]) / abs(want["metal"])
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info

