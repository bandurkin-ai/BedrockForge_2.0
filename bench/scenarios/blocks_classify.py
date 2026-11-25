"""The density and the categories of a block model (ТЗ 8.7.5; ADR 0023 «Замеры»: ci — 2 млн блоков ≤ 1,5 с, full —
20 млн: категории ≤ 10 с).

A regular model of `rows` blocks: SG (10 % of gaps), the category DOM (руда, порода, дайка; −1 — none), the estimate
EST (5 % of gaps) and the fields of the scheme JORC — PASS, SLOPE, NH, AD (`schemes.defaults()["jorc"]`). The kernels
as the jobs `blocks.density` and `blocks.classify` call them (the answer is the density or the categories of the
whole model by portions of 2²⁰ rows; the writing of the field is that of the calculator, `blocks_calc`):
  field_s, table_s, formula_s   the density by the field SG (the default 2,6 for a gap), by the table
                                «DOM → density» (the default 2,5) and by the formula `SG * 1.001 + EST` (the default 2)
  jorc_s       the categories of JORC (one pass over the model: the rules are compiled to one program)
  majority_s   the same with the majority filter of 1 pass (3 × 3 × 3 parents of the grid)
What is checked (a failure stops the run), against numpy over the columns: the sums of the three densities (1e-9),
the counts of the categories of JORC (exactly; a block without an estimate has none), the filter changes the
categories of few blocks and keeps the model's size.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc

TABLE = {"field": "DOM", "values": [{"value": "руда", "density": 3.1}, {"value": "порода", "density": 2.7}]}
BODIES = {
    "field": {"method": "field", "field": "SG", "default": 2.6},
    "table": {"method": "table", "default": 2.5, "table": TABLE},
    "formula": {"method": "formula", "expr": "SG * 1.001 + EST", "default": 2.0},
}


def fields() -> list[dict[str, Any]]:
    return [{"name": "SG", "dtype": "f8"}, {"name": "DOM", "dtype": "cat", "labels": ["руда", "порода", "дайка"]},
            {"name": "EST", "dtype": "f8"}, {"name": "PASS", "dtype": "i1"}, {"name": "SLOPE", "dtype": "f4"},
            {"name": "NH", "dtype": "i2"}, {"name": "AD", "dtype": "f4"}]  # fmt: skip


def values(rng: np.random.Generator, n: int) -> dict[str, np.ndarray]:
    return {"SG": np.where(rng.random(n) < 0.1, np.nan, 2.0 + rng.random(n)),
            "DOM": rng.integers(-1, 3, n).astype(np.int32),
            "EST": np.where(rng.random(n) < 0.05, np.nan, rng.random(n)),
            "PASS": rng.integers(1, 4, n).astype(np.float64), "SLOPE": rng.random(n).astype(np.float32),
            "NH": rng.integers(0, 6, n).astype(np.float64),
            "AD": rng.integers(10, 30, n).astype(np.float32)}  # fmt: skip


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        ctx.keep(**bc.regular_model(ctx, int(params["rows"]), fields(), values))
    finally:
        ctx.close()


def load() -> None:
    bc.warm()


def reference(ctx: bc.Ctx, model_id: int) -> dict[str, Any]:
    """Σ of the three densities and the counts of the three JORC labels, by numpy over the portions."""
    sums = {"field": 0.0, "table": 0.0, "formula": 0.0}
    counts = [0, 0, 0]
    for ch in ctx.model(model_id).iter(["SG", "DOM", "EST", "PASS", "SLOPE", "NH", "AD"], bc.PORTION):
        c = ch.cols
        sums["field"] += float(np.where(np.isnan(c["SG"]), 2.6, c["SG"]).sum())
        by_domain = np.select([c["DOM"] == 0, c["DOM"] == 1], [3.1, 2.7], 2.5)  # a block with no domain: the default
        sums["table"] += float(by_domain.sum())
        formula = c["SG"] * 1.001 + c["EST"]
        sums["formula"] += float(np.where(np.isnan(formula), 2.0, formula).sum())
        has = ~np.isnan(c["EST"])
        first = (c["PASS"] == 1) & (c["SLOPE"] >= 0.8) & (c["NH"] >= 3) & (c["AD"] <= 25)
        second = (c["PASS"] <= 2) & (c["SLOPE"] >= 0.5) & (c["NH"] >= 2)
        counts[0] += int((has & first).sum())
        counts[1] += int((has & ~first & second).sum())
        counts[2] += int((has & ~first & ~second).sum())
    return {"sums": sums, "counts": counts}


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.blocks import categories as cat
    from bf_core.blocks import density as dens
    from bf_core.blocks import schemes as sc

    bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    model = ctx.model(meta["model_id"])
    info: dict[str, Any] = {"rows": meta["rows"]}
    try:
        got: dict[str, float] = {}
        for key, body in BODIES.items():
            plan = dens.prepare(model, dens.parse(body))
            t0 = time.perf_counter()
            total = 0.0
            for piece in dens.iter_density(model, plan):
                total += float(piece.density.sum())
            info[f"{key}_s"] = round(time.perf_counter() - t0, 3)
            got[key] = total
        counts = {}
        for key, majority in (("jorc", 0), ("majority", 1)):
            spec = cat.parse({"scheme": "jorc", "estimate": "EST", "majority": majority}, lambda s: sc.defaults()[s])
            plan = cat.prepare(model, spec)
            t0 = time.perf_counter()
            res = cat.classify(model, plan)
            info[f"{key}_s"] = round(time.perf_counter() - t0, 3)
            counts[key] = res
        want = reference(ctx, meta["model_id"])
        for key, total in got.items():
            assert abs(total - want["sums"][key]) <= 1e-9 * abs(want["sums"][key]), (key, total, want["sums"][key])
        assert counts["jorc"].counts == want["counts"], (counts["jorc"].counts, want["counts"])
        assert counts["majority"].rows == meta["rows"] and sum(counts["majority"].counts) <= meta["rows"]
        moved = int((counts["majority"].codes != counts["jorc"].codes).sum())
        assert 0 < moved < meta["rows"], moved  # the noise is smoothed: some blocks change their category
        info.update(counts=counts["jorc"].counts, no_estimate=counts["jorc"].no_estimate, majority_moved=moved)
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
