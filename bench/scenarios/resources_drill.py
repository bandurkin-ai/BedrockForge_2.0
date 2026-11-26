"""The jump from a cell of the resource report to its blocks (ТЗ 7.35 Д-36, ТД36-1; ADR 0027 «Замеры»: дерево правил и
показ оболочки на 20 млн ≤ 3 с, как Д-02 / 8.7.7; подшаг 20.9).

A regular model of `rows` blocks of 500 m³ (AU with 3 % of gaps, density DENS, category DOM; `blocks_common`) and
a SAVED report on it (`resources.report`, written in `prepare`, not timed): the groups DOM × the benches of 25 m, the
cut-offs «без борта», 0,5 and 1, the balance 1 / 0,5. The timed parts are the routes as functions (`drill.answer`,
`drill.rows_answer` — what the route calls: the rules, the cell counted again by the kernel of the report):
  group_s    a cell of a group (DOM = B, a bench by share, AU ≥ 0,5): the tree with a `slab` and the pass over the model
  total_s    the cell of the total at the cut-off 0,5 (no leaf: the whole tree is one expression)
  rows_s     the first page (1000 blocks) of the cell of the group: the pass for the cell + the read of the page
  again_s    `group` once more
What is checked (a failure stops the run): the cell of the group and of the total are the rows of the report table
(blocks exactly, volume and tonnes to 1e−12) — ТД36-1; the page has 1000 rows (or all the blocks) in the order of the
file, Σ of their volume ≤ the cell; `again` is the same answer bit by bit.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import blocks_common as bc

CUT = 0.5
BASE = {"elements": ["AU"], "density": "DENS", "groups": ["DOM", {"kind": "bench"}], "bench": {"h": 25.0},
        "cutoffs": [None, 0.5, 1.0], "balance": {"cutoff": 1.0, "off_cutoff": 0.5}, "curve": 0}  # fmt: skip


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.resources import job as rj

    ctx = bc.Ctx.create(work)
    try:
        made = bc.regular_model(ctx, int(params["rows"]), bc.grade_fields(), bc.grade_values)
        out = rj.run_report(ctx.engine, ctx.folder, {"params": {"model_id": made["model_id"], **BASE}, "save": True,
                                                     "name": "Отчёт замера"})  # fmt: skip
        ctx.keep(**made, report_id=out["report_id"])
    finally:
        ctx.close()


def load() -> None:
    bc.warm()
    from bf_core.resources import kernels

    kernels.warm()


def close(a: float, b: float, rel: float = 1e-12) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b), 1.0)


def table(ctx: bc.Ctx, rid: int) -> list[dict[str, Any]]:
    from bf_core.db import dialect
    from bf_core.resources import store as st

    with dialect.read_tx(ctx.engine) as conn:
        return list(st.read_rows(conn, ctx.store, rid, 1, 5000)["rows"])


def row_of(rows: list[dict[str, Any]], kind: str, cutoff: float | None, group: list[str] | None) -> dict[str, Any]:
    got = [r for r in rows if r["kind"] == kind and r["cutoff"] == cutoff
           and (r["total"] if group is None else r["g"] == group)]  # fmt: skip
    assert len(got) == 1, (kind, cutoff, group, len(got))
    return got[0]


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.db import dialect
    from bf_core.resources import drill as dr
    from bf_core.resources import spec

    bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    rid = int(meta["report_id"])
    info: dict[str, Any] = {"rows": meta["rows"]}
    try:
        rows = table(ctx, rid)
        mid = [r for r in rows if r["kind"] == "cutoff" and r["cutoff"] == CUT and not r["total"] and r["g"][0] == "B"
               and r["blocks"] > 0]  # fmt: skip
        want = mid[len(mid) // 2]
        cell = {"report_id": rid, "group": want["g"], "cutoff": CUT}
        d, dt = spec.parse_drill(cell), spec.parse_drill({"report_id": rid, "group": None, "cutoff": CUT})
        dp = spec.parse_drill_rows({**cell, "page": 1, "size": 1000})
        with dialect.read_tx(ctx.engine) as conn:
            t0 = time.perf_counter()
            first = dr.answer(conn, ctx.store, d)
            info["group_s"] = round(time.perf_counter() - t0, 3)
            t0 = time.perf_counter()
            whole = dr.answer(conn, ctx.store, dt)
            info["total_s"] = round(time.perf_counter() - t0, 3)
            t0 = time.perf_counter()
            page = dr.rows_answer(conn, ctx.store, dp)
            info["rows_s"] = round(time.perf_counter() - t0, 3)
            t0 = time.perf_counter()
            again = dr.answer(conn, ctx.store, d)
            info["again_s"] = round(time.perf_counter() - t0, 3)
        for got, row in ((first["cell"], want), (whole["cell"], row_of(rows, "cutoff", CUT, None))):
            assert got["blocks"] == row["blocks"], (got, row["blocks"])
            assert close(got["volume"], row["volume"]) and close(got["tonnes"], row["tonnes"]), (got, row)
        assert json.dumps(again, sort_keys=True) == json.dumps(first, sort_keys=True), "again"
        assert page["cell"] == first["cell"] and page["total"] == first["cell"]["blocks"]
        assert len(page["rows"]) == min(1000, page["total"]) and page["rules"] == first["rules"]
        keys = [(r["k"], r["j"], r["i"]) for r in page["rows"]]
        assert keys == sorted(keys), "the order of the file"
        assert sum(r["volume"] for r in page["rows"]) <= first["cell"]["volume"] * (1 + 1e-12)
        info.update(blocks=first["cell"]["blocks"], total_blocks=whole["cell"]["blocks"])
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
