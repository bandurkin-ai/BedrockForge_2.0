"""The report by benches: H = 10 m over blocks of a height dz = 5 … 15 m (ТЗ 8.10 «Время»: деление по уступам на
20 млн блоков ≤ 5 с; ADR 0027 решение 7 и «Замеры», подшаг 20.11).

A regular model of `rows` blocks 10 × 10 × `dz` m (`params["dz"]`, 15 by default — the worst case: a block touches two
or three benches; 5 — the block lies in one; 7,5 …; `resources_common`), about 1000 m of depth. The job
`resources.report` (`job.run_report`: a preview) for the cut-offs 0; 0,5; 1 g/t of AU, the element AU, the one key
«Уступ» (H = 10 m, the range of the model), no restrictor:
  bench_s      the whole job
  again_s      the same once more
  read_s, kernel_s, control_s   the stages of the job
What is checked (a failure stops the run), by numpy over the columns: every row of the table — the share of a block in
a bench is the overlap with it over the height of the block (a block on two benches is counted in both, tonnes and
metal are divided): the blocks exactly, the volume, the tonnes, ḡ and the sum of the grade 1e-9; the sum of the benches
= the total (the tonnes, the volume 1e-9; the control); `again` is the same answer bit by bit.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import blocks_common as bc
from . import resources_common as rc

CUTOFFS = [0.0, 0.5, 1.0]
H = 10.0
DEPTH = 1000.0  # m: the height of the model


def load() -> None:
    rc.load()  # the hook of `bench.run`: the kernels loaded, not timed


def layers(dz: float) -> int:
    return max(1, round(DEPTH / dz))


def prepare(params: dict[str, Any], work: Path) -> None:
    dz = float(params.get("dz", 15.0))
    ctx = bc.Ctx.create(work)
    try:
        ctx.keep(dz=dz, **rc.prepare_model(ctx, int(params["rows"]), dz=dz, nz=layers(dz)))
    finally:
        ctx.close()


def shares_of(meta: dict[str, Any], z0: float, j0: int, count: int) -> Any:
    """`shares(chunk)` of the benches j0 … j0 + count − 1 (the digit of a bench is j − j0 + 1; 0 — outside, no one)."""
    import numpy as np

    dz = float(meta["dz"])
    nxy = int(meta["counts"][0]) * int(meta["counts"][1])
    eps = rc.SLIVER * dz
    steps = int(np.ceil(dz / H)) + 1  # the benches a block can touch

    def shares(ch: Any) -> list[tuple[Any, Any]]:
        k = (ch.p // np.uint64(nxy)).astype(np.float64)  # the layer of the block
        a, e = rc.CORNER_Z + k * dz, rc.CORNER_Z + (k + 1.0) * dz
        j_lo = np.floor((a - z0) / H).astype(np.int64)
        out = []
        for m in range(steps):
            j = j_lo + m
            ov = np.minimum(e, z0 + (j + 1) * H) - np.maximum(a, z0 + j * H)
            ok = (ov > eps) & (j >= j0) & (j < j0 + count)
            out.append((np.where(ok, j - j0 + 1, -1), np.where(ok, ov / dz, 0.0)))
        return out

    return shares


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.resources.spec_parts import Bench

    bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    mid = meta["model_id"]
    info: dict[str, Any] = {"rows": meta["rows"], "dz": meta["dz"]}
    try:
        body = rc.request(mid, elements=[{"field": "AU"}], cutoffs=CUTOFFS, groups=[{"kind": "bench"}], bench={"h": H})
        ans, info["bench_s"] = rc.run_job(ctx, body)
        again, info["again_s"] = rc.run_job(ctx, body)
        for k, v in (ans["seconds"] or {}).items():
            info[f"{k}_s"] = v
        b = ans["bench"]
        grid = Bench(b["h"], b["z0"], b["z_min"], b["z_max"], b["j0"], b["n"])
        info["benches"] = b["n"]
        t0 = time.perf_counter()
        ref = rc.reference(ctx.model(mid), CUTOFFS, b["n"] + 1, shares_of(meta, b["z0"], b["j0"], b["n"]),
                           vol=rc.volume_of(meta["dz"]), restrict=False, elements=("AU",))  # fmt: skip
        info["reference_s"] = time.perf_counter() - t0
        info["table_rows"] = rc.check_rows(ans, ref, CUTOFFS, lambda g: [grid.label(grid.j0 + g - 1)], 1)
        for c in CUTOFFS:  # the shares add up: the benches make the total (the whole block once, the counts apart)
            whole = ref.row(ref.total, c)
            parts = [ref.row(g, c) for g in range(1, b["n"] + 1)]
            assert rc.close(sum(p["tonnes"] for p in parts), whole["tonnes"]), c
            assert rc.close(sum(p["volume"] for p in parts), whole["volume"]), c
        assert ans["control"]["sum_groups"]["ok"], ans["control"]["sum_groups"]
        assert rc.same_bits(ans, again), "again"
        assert any(r["g"] and r["g"][0] != rc.EMPTY for r in ans["rows"]), "no bench"
        info["blocks"] = ans["rows"][0]["blocks"]
    finally:
        ctx.close()
    for k in ("bench_s", "again_s", "reference_s"):
        info[k] = round(info[k], 3)
    info["peak_mb"] = bc.peak_mb()
    return info
