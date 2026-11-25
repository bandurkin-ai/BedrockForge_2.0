"""The preview and the inspector of one block (ТЗ 8.8.4; ADR 0024 §7, Д-30, «Замеры»: ≤ 100 мс; full — 100 тыс. проб, ci
— 20 тыс. проб).

The deposit of `estimate_common`, OK of a block 10 × 10 × 5 м (disc 4×4×2, two structures), three passes: what the
route `POST /estimate/preview` and the inspector do to a block that is pointed at — `kernel.estimate_block` (the same
code as the job, the candidates of the search with the reason of each, the weights of the selected ones, the ellipsoid
of the pass):
  compile_s   `kernel.compile_row` of the row (the cache of C̄_VV of the model, the search) — once per set of parameters
  cold_s      the first block of a new row;  preview_s — compile_s + cold_s: a request with a row not compiled yet
  block_s     the median of 50 blocks of the same row (a block pointed at after the previous one);  block_max_s — the
              worst
  inspect_s   the median of the same blocks with `pool` = the samples of all domains (the inspector shows the other
              domains too)
What is checked (a failure stops the run): for 10 blocks — the estimate and the variance against the brute-force NumPy
kriging of `tests/reference/krige3d_numpy.py` (1e−9: `ref_err`), the same pass and number of samples; Σλ = 1 and
EST = Σ λ·z over the selected samples (1e−12), the ellipsoid is that of the pass, the candidates are listed."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import estimate_common as ec

DISC = (4, 4, 2)
BLOCKS = 50
REPEAT = 3


def prepare(params: dict[str, Any], work: Path) -> None:
    ec.keep(work, ec.Deposit.of(params))


def load() -> None:
    from bf_core.geostat.estimate.warm import warm

    warm()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.geostat.estimate import kernel as kn

    ec.limit_threads(params)
    dep = ec.Deposit.of(params)
    s = ec.open_samples(work)
    spec = ec.spec(dep, "ok", variogram=ec.TWO, disc=DISC)
    t0 = time.perf_counter()
    rk = kn.compile_row(spec, s, block_size=ec.SIZE, rotation_deg=0.0)
    t1 = time.perf_counter()
    rows = dep.probes(BLOCKS)
    centres = dep.centres_of(rows)
    first = kn.estimate_block(rk, centres[0], pool=s)
    t2 = time.perf_counter()
    info: dict[str, Any] = {"samples": s.n, "compile_s": round(t1 - t0, 4), "cold_s": round(t2 - t1, 4),
                            "preview_s": round(t2 - t0, 4)}  # fmt: skip
    plain: list[float] = []
    pooled: list[float] = []
    details = []
    for _ in range(REPEAT):
        for c in centres:
            a = time.perf_counter()
            d = kn.estimate_block(rk, c)
            b = time.perf_counter()
            kn.estimate_block(rk, c, pool=s)
            e = time.perf_counter()
            plain.append(b - a)
            pooled.append(e - b)
            details.append(d)
    info.update(block_s=round(float(np.median(plain)), 5), block_max_s=round(max(plain), 5),
                inspect_s=round(float(np.median(pooled)), 5), inspect_max_s=round(max(pooled), 5),
                blocks=BLOCKS * REPEAT, estimated=int(sum(d.status == 0 for d in details[:BLOCKS])))  # fmt: skip
    assert first.status == 0 and info["estimated"] >= 0.9 * BLOCKS, info
    ang, rad, passes = ec._ellipsoid(rk)
    worst = 0.0
    checked = 0
    for d in details[:BLOCKS:5]:  # 10 of the 50 blocks
        ref = ec._numpy().krige_blocks(s.xyz, s.z, s.hole, d.centre[None, :], model=ec.TWO, kind="ok", size=ec.SIZE,
                                       disc=DISC, phi_deg=0.0, angles=ang, radii=rad, passes=passes)  # fmt: skip
        assert (d.status == 0) == (ref["PASS"][0] > 0), "the block is estimated by one and not by the other"
        if d.status:
            continue
        checked += 1
        assert d.pass_ == ref["PASS"][0] and d.counts["selected"] == ref["NS"][0], (d.pass_, d.counts)
        assert d.ellipsoid["pass"] == d.pass_ and d.counts["candidates"] >= d.counts["selected"] > 0
        assert abs(float(d.lam.sum()) - 1.0) <= 1e-12, float(d.lam.sum())
        assert abs(float(d.lam @ s.z[d.sel]) - d.values["EST"]) <= 1e-12 * max(1.0, abs(d.values["EST"]))
        worst = max(worst, ec.rel_err(np.array([d.values["EST"]]), ref["EST"]),
                    ec.rel_err(np.array([d.values["KV"]]), ref["KV"]))  # fmt: skip
    assert checked >= 5 and worst <= ec.REL_NUMPY, f"preview: {checked} blocks, differs from NumPy by {worst:.2e}"
    info["ref_err"] = worst
    info["ref_checked"] = checked
    return info
