"""The grade–tonnage report of a block model (ТЗ 7.31: борт 0 = полный тоннаж; ADR 0023 «Замеры»: ci — 2 млн блоков
≤ 0,5 с, full — 20 млн ≤ 3 с, a reference: the goal of ТЗ is none; 17.28 asked ≤ 5 с).

A regular model of `rows` blocks of 500 m³ (AU with 3 % of gaps, CU f4, the density DENS, the category DOM;
`blocks_common`).
The job `blocks.report` (`report.run_report`, the body of the handler) for:
  base_s       two grades (AU, CU) over three cut-offs 0; 0,5; 1 by AU, the density DENS, the curve of 50 points
  groups_s     the same with groups by DOM and the CSV text
  restrict_s   the same with the restrictor CU > 0,3
  again_s      `base` once more
What is checked (a failure stops the run), by numpy over the columns (the sums of the report are Neumaier's, in
the order of the rows: 1e-9 of numpy's pairwise sums): of the base request — for every cut-off the blocks, the
volume, the tonnes and the grades AU and CU (weighted by the tonnes); the cut-off 0 keeps every block that has a
value of AU; the groups add up to the whole (blocks exactly); `again` is the same answer bit by bit.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc

CUTS = [0.0, 0.5, 1.0]
BASE = {"grades": ["AU", "CU"], "density": "DENS", "cutoffs": CUTS, "cutoff_field": "AU", "curve": 50}
VOLUME = bc.SIZE[0] * bc.SIZE[1] * bc.SIZE[2]


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        ctx.keep(**bc.regular_model(ctx, int(params["rows"]), bc.grade_fields(), bc.grade_values))
    finally:
        ctx.close()


def load() -> None:
    bc.warm()


def reference(ctx: bc.Ctx, model_id: int) -> list[dict[str, float]]:
    """Per cut-off: blocks, tonnes and the weighted grades of AU and CU, by numpy over the portions."""
    acc = [{"blocks": 0, "tonnes": 0.0, "au": 0.0, "cu": 0.0} for _ in CUTS]
    for chunk in ctx.model(model_id).iter(["AU", "CU", "DENS"], bc.PORTION):
        au, cu, dens = chunk.cols["AU"], chunk.cols["CU"].astype(np.float64), chunk.cols["DENS"]
        for a, cut in zip(acc, CUTS, strict=True):
            keep = au >= cut  # a gap is not ≥ any cut-off
            t = VOLUME * dens[keep]
            a["blocks"] += int(keep.sum())
            a["tonnes"] += float(t.sum())
            a["au"] += float((au[keep] * t).sum())
            a["cu"] += float((cu[keep] * t).sum())
    return acc


def close(a: float, b: float, rel: float = 1e-9) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b), 1.0)


def check(base: dict[str, Any], groups: dict[str, Any], want: list[dict[str, float]], rows: int) -> None:
    whole = [r for r in base["rows"] if r["group"] is None]
    assert [r["cutoff"] for r in whole] == CUTS, [r["cutoff"] for r in whole]
    for r, w in zip(whole, want, strict=True):
        assert r["blocks"] == w["blocks"], (r["cutoff"], r["blocks"], w["blocks"])
        assert close(r["tonnes"], w["tonnes"]) and close(r["volume"], w["blocks"] * VOLUME), r["cutoff"]
        assert close(r["grades"]["AU"]["grade"], w["au"] / w["tonnes"]), (r["cutoff"], r["grades"]["AU"])
        assert close(r["grades"]["CU"]["grade"], w["cu"] / w["tonnes"]), (r["cutoff"], r["grades"]["CU"])
    assert whole[0]["blocks"] <= rows
    parts = [r for r in groups["rows"] if r["group"] is not None and r["cutoff"] == CUTS[0]]
    top = [r for r in groups["rows"] if r["group"] is None and r["cutoff"] == CUTS[0]][0]
    assert sum(r["blocks"] for r in parts) == top["blocks"], (sum(r["blocks"] for r in parts), top["blocks"])
    assert groups["csv"].count("\n") >= len(groups["rows"]), "the CSV has fewer lines than the rows"


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.blocks import report as rp

    bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    mid = meta["model_id"]
    info: dict[str, Any] = {"rows": meta["rows"]}
    try:
        answers = {}
        bodies = {"base": BASE, "groups": {**BASE, "groups": "DOM", "csv": True},
                  "restrict": {**BASE, "restrict": {"cond": {"field": "CU", "op": ">", "value": 0.3}}},
                  "again": BASE}  # fmt: skip
        for key, body in bodies.items():
            t0 = time.perf_counter()
            answers[key] = rp.run_report(ctx.engine, ctx.folder, {"model_id": mid, **body})
            info[f"{key}_s"] = round(time.perf_counter() - t0, 3)
        check(answers["base"], answers["groups"], reference(ctx, mid), meta["rows"])
        assert json.dumps(answers["again"], sort_keys=True) == json.dumps(answers["base"], sort_keys=True), "again"
        assert answers["restrict"]["rows"][0]["blocks"] < answers["base"]["rows"][0]["blocks"], "the restrictor"
        info["blocks"] = answers["base"]["rows"][0]["blocks"]
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
