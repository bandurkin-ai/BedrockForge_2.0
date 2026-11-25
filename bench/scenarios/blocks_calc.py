"""The calculator of block models: the fields written to the file of the model (ТЗ 8.7.4, ADR 0023 «Замеры»: ci —
2 млн блоков ≤ 0,8 с, full — «5 полей × 20 млн ≤ 5 с»).

A regular model of `rows` blocks (AU with 3 % of gaps, CU f4, DENS, the category DOM; `blocks_common`). The job
`blocks.calc` (`calc_job.run_calc`, the body of the handler) for six expressions, each a new field of the file:
  F1  AU * DENS                                   F2  IF(AU > 1, AU * 2 + CU, 0)
  F3  SQRT(ABS(AU)) + LN(DENS) * CU  (f4)         F4  IF(DOM = 'A' OR DOM = 'B', 'руда', 'порода')  (a category)
  F5  ROUND(IF(AU > 1, AU * DENS, COALESCE(CU, 0)), 1) over the blocks of the restrictor AU > 0.5
  F6  IF(AU > 1, AU * DENS + CU, F1 - F2 * 0.5)   five fields read — «5 полей × N блоков»
  one_s … five_s  F1 … F5;  calc5_s — F6 (the target of ТЗ);  all_s — the six together
What is checked (a failure stops the run), against numpy over the first portion of the rows: F1, F2, F6 (1e-12,
the gaps stay gaps — NaN is not a number: `AU * DENS` of a block without AU is NaN), the classes of F4, the blocks
of F5 outside the restrictor are empty (`outside: nan`).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc

RICH = {"cond": {"field": "AU", "op": ">", "value": 0.5}}  # the restrictor of F5
EXPRS = [
    ("F1", "AU * DENS", "f8", None),
    ("F2", "IF(AU > 1, AU * 2 + CU, 0)", "f8", None),
    ("F3", "SQRT(ABS(AU)) + LN(DENS) * CU", "f4", None),
    ("F4", "IF(DOM = 'A' OR DOM = 'B', 'руда', 'порода')", None, None),
    ("F5", "ROUND(IF(AU > 1, AU * DENS, COALESCE(CU, 0)), 1)", "f8", RICH),
    ("F6", "IF(AU > 1, AU * DENS + CU, F1 - F2 * 0.5)", "f8", None),
]
KEYS = ["one", "two", "three", "four", "five", "calc5"]


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        model = bc.regular_model(ctx, int(params["rows"]), bc.grade_fields(), bc.grade_values)
        ctx.keep(**model)
    finally:
        ctx.close()


def load() -> None:
    bc.warm()


def check(ctx: bc.Ctx, model_id: int, rows: int) -> None:
    names = ["AU", "CU", "DENS", "DOM", *(e[0] for e in EXPRS)]
    chunk = next(iter(ctx.model(model_id).iter(names, 1 << 18)))
    c = chunk.cols
    au, cu, dens = (np.asarray(c[k], dtype=np.float64) for k in ("AU", "CU", "DENS"))
    f1 = au * dens
    f2 = np.where(au > 1, au * 2 + cu, 0.0)
    f2 = np.where(np.isnan(au), np.nan, f2)
    ok = np.isfinite(f1)
    assert np.array_equal(np.isnan(np.asarray(c["F1"], dtype=np.float64)), np.isnan(f1)), "F1: the gaps moved"
    assert np.allclose(np.asarray(c["F1"], dtype=np.float64)[ok], f1[ok], rtol=1e-12, atol=0.0), "F1 differs"
    got2 = np.asarray(c["F2"], dtype=np.float64)
    assert np.array_equal(np.isnan(got2), np.isnan(f2)), "F2: the gaps moved"
    assert np.allclose(got2[~np.isnan(f2)], f2[~np.isnan(f2)], rtol=1e-12, atol=0.0), "F2 differs"
    inside = np.nan_to_num(au, nan=0.0) > 0.5
    f5 = np.asarray(c["F5"], dtype=np.float64)
    assert np.isnan(f5[~inside]).all() and not np.isnan(f5[inside]).any(), "F5: the restrictor is not kept"


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.blocks import calc_job

    bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    info: dict[str, Any] = {"rows": meta["rows"]}
    try:
        total = 0.0
        for (field, expr, dtype, restrict), key in zip(EXPRS, KEYS, strict=True):
            body = {"model_id": meta["model_id"], "expected_version": ctx.version(meta["model_id"]), "expr": expr,
                    "field": field, "dtype": dtype, "restrict": restrict, "outside": "nan",
                    "replace": True}  # fmt: skip
            t0 = time.perf_counter()
            out = calc_job.run_calc(ctx.engine, ctx.folder, body)
            seconds = time.perf_counter() - t0
            total += seconds
            info[f"{key}_s"] = round(seconds, 3)
            assert out["rows"] == meta["rows"], out["rows"]
        info["all_s"] = round(total, 3)
        check(ctx, meta["model_id"], meta["rows"])
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
