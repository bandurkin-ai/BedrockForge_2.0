"""The restrictor of a block model: the rules of the filter over the columns (ТЗ 7.31: «AU > 1 И DOMAIN = 2» и
обратный; 20 млн — ограничитель; ADR 0023 §3; the goal of ТЗ is none — the figures are the reference of 17.8).

A regular model of `rows` blocks (AU with 3 % of gaps, DOMAIN 0…4 with 2 % of gaps; `blocks_common`). The route
«Проверить на модели» (`restrict.test_rules`: the rules compiled to calc expressions, every portion of the rows
read, the blocks with f > 0 counted) for
  and_s   AU > 1 AND DOMAIN = 2      not_s   NOT (AU > 1 AND DOMAIN = 2) — the opposite       again_s  `and` once more
and `restrict.evaluate` (the whole column of f: `evaluate_s`).
What is checked (a failure stops the run), against numpy over the columns: the number of blocks that pass; the
opposite is the complement — passed(and) + passed(not) = rows (a gap is not a number: `AU > 1` of a gap is false,
so the opposite passes it); the weight is the number of the blocks (f ∈ {0; 1} of a number leaf).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import blocks_common as bc

AND = {"op": "and", "items": [{"cond": {"field": "AU", "op": ">", "value": 1}},
                              {"cond": {"field": "DOMAIN", "op": "=", "value": 2}}]}  # fmt: skip
NOT = {"op": "not", "items": [AND]}


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        ctx.keep(**bc.regular_model(ctx, int(params["rows"]), bc.grade_fields(), bc.grade_values))
    finally:
        ctx.close()


def load() -> None:
    bc.warm()


def reference(ctx: bc.Ctx, model_id: int) -> int:
    """The blocks AU > 1 and DOMAIN = 2 by numpy over the portions of the columns (a gap compares false)."""
    both = 0
    for chunk in ctx.model(model_id).iter(["AU", "DOMAIN"], bc.PORTION):
        both += int(((chunk.cols["AU"] > 1.0) & (chunk.cols["DOMAIN"] == 2.0)).sum())
    return both


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.blocks import restrict
    from bf_core.db import dialect

    bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    model_id = meta["model_id"]
    info: dict[str, Any] = {"rows": meta["rows"]}
    try:
        got = {}
        for key, rules in (("and", AND), ("not", NOT), ("again", AND)):
            t0 = time.perf_counter()
            got[key] = restrict.test_rules(ctx.project, model_id, rules)
            info[f"{key}_s"] = round(time.perf_counter() - t0, 3)
        with dialect.read_tx(ctx.engine) as conn:
            model = ctx.model(model_id)
            t0 = time.perf_counter()
            f = restrict.evaluate(model, AND, conn=conn, store=ctx.store)
            info["evaluate_s"] = round(time.perf_counter() - t0, 3)
        both = reference(ctx, model_id)
        info.update(passed_and=got["and"]["passed"], passed_not=got["not"]["passed"])
        assert got["and"]["passed"] == both == got["again"]["passed"], (got["and"]["passed"], both)
        assert got["not"]["total"] == meta["rows"] and f.shape == (meta["rows"],), (got["not"], f.shape)
        assert int((f > 0.0).sum()) == both, (int((f > 0.0).sum()), both)
        assert got["not"]["passed"] == meta["rows"] - both, (got["not"]["passed"], both)
        assert got["and"]["weight"] == float(both), (got["and"]["weight"], both)
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
