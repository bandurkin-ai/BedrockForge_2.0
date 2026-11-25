"""KNA, the analysis of the kriging parameters (ТЗ 8.8.5; ADR 0024 §8, «Замеры»: 3 блока × 16 вариантов ≤ 5 с; full —
100 тыс. проб, ci — 20 тыс. проб).

The deposit of `estimate_common`, OK of a block 10 × 10 × 5 м (disc 4×4×2), three passes. The test blocks are the three
of the rule (`kna.auto_blocks`: «много», «средне», «мало» of the blocks of `TARGET_LIMIT` = 200 000 cut evenly along
the model), the series — all four (`n_max` 16 variants, radius 7, disc 5, size 3): the job `estimate.kna` without
its project (`kna.analyse_blocks` over a compiled row).
  auto_s       the picks: the samples inside the radius of the first and of the last pass of every candidate (two
               trees, 2 × 200 000 queries)
  analyse_s    the variants: `kernel.estimate_block` of every block with every value;  variants — their number
  total_s      auto_s + analyse_s — what the user waits for;  recommend — the n_max of the answer
What is checked (a failure stops the run): three blocks under three different labels; every series of the answer has
its values; the base indicators of each block (KV, NS, status) against the brute-force NumPy block kriging of
`tests/reference/krige3d_numpy.py` (the same samples and number, KV 1e−9: `ref_err`); the variant with the same n_max
as the row is the base one."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import estimate_common as ec

DISC = (4, 4, 2)


def prepare(params: dict[str, Any], work: Path) -> None:
    ec.keep(work, ec.Deposit.of(params))


def load() -> None:
    from bf_core.geostat.estimate.warm import warm

    warm()


def targets(dep: ec.Deposit) -> Any:
    """The blocks of the row as `kna_blocks.Blocks.targets` gives them: `TARGET_LIMIT` rows cut evenly along the
      model."""
    from bf_core.geostat.estimate.kna_blocks import TARGET_LIMIT, Targets

    n = min(dep.rows, TARGET_LIMIT)
    rows = np.linspace(0, dep.rows - 1, n).astype(np.int64)
    nx, ny, _ = dep.counts
    ijk = np.column_stack([rows % nx, (rows // nx) % ny, rows // (nx * ny)])
    return Targets(rows, rows.astype(np.uint64), ijk, dep.centres_of(rows), np.tile(ec.SIZE, (n, 1)), dep.rows)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.geostat.estimate import kernel as kn
    from bf_core.geostat.estimate import kna

    ec.limit_threads(params)
    dep = ec.Deposit.of(params)
    s = ec.open_samples(work)
    spec = ec.spec(dep, "ok", variogram=ec.TWO, disc=DISC)
    rk = kn.compile_row(spec, s)  # no block size: the size goes with every block
    t0 = time.perf_counter()
    blocks = kna.auto_blocks(rk, targets(dep))
    t1 = time.perf_counter()
    out = kna.analyse_blocks(rk, blocks)
    t2 = time.perf_counter()
    variants = sum(len(v) for b in out["blocks"] for v in b["vary"].values())
    info: dict[str, Any] = {"samples": s.n, "blocks": len(blocks), "variants": variants,
                            "auto_s": round(t1 - t0, 3), "analyse_s": round(t2 - t1, 3), "total_s": round(t2 - t0, 3),
                            "recommend": out["recommend"]["n_max"],
                            "n_r1": [b.n_r1 for b in blocks]}  # fmt: skip
    assert len(blocks) == 3 and sorted(b.auto for b in blocks) == ["мало", "много", "средне"]
    assert [len(b["vary"]["n_max"]) for b in out["blocks"]] == [16] * 3 and variants == 3 * (16 + 7 + 5 + 3)
    ang, rad, passes = ec._ellipsoid(rk)
    worst = 0.0
    for b, got in zip(blocks, out["blocks"], strict=True):
        base = got["base"]
        ref = ec._numpy().krige_blocks(s.xyz, s.z, s.hole, b.centre[None, :], model=ec.TWO, kind="ok", size=b.size,
                                       disc=DISC, phi_deg=0.0, angles=ang, radii=rad, passes=passes)  # fmt: skip
        assert base["status"] == 0 and ref["PASS"][0] > 0 and base["NS"] == ref["NS"][0], (b.auto, base, ref)
        worst = max(worst, ec.rel_err(np.array([base["KV"]]), ref["KV"]))
        same = [v for v in got["vary"]["n_max"] if v["value"] == 24]
        assert same and same[0]["KV"] == base["KV"] and same[0]["NS"] == base["NS"], "n_max 24 is the row itself"
    assert worst <= ec.REL_NUMPY, f"KNA differs from the NumPy kriging by {worst:.2e}"
    info["ref_err"] = worst
    return info
