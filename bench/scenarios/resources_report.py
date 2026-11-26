"""The resource report: 10 cut-offs × 2 keys (domain × category) × 3 elements with a restrictor-expression (ТЗ 8.10
«Время»: 20 млн блоков ≤ 5 с; ADR 0027 «Замеры», подшаг 20.11).

A regular model of `rows` blocks of 500 m³ (AU 3 % and AG 5 % of gaps, CU f4, the density DENS, DOM — 4 domains, GEO —
5 categories, a tenth of the blocks of each has no label: «(пусто)»; `resources_common`). The job `resources.report`
(`job.run_report`, the body of the handler: a preview, nothing is written) for the cut-offs 0 … 5 g/t of AU, the keys
DOM and GEO, the elements AU, CU, AG, the restrictor `CU > 0,3` and the curve of 100 points:
  report_s     the whole job: the read of the model and the rules, the pass, the control, the rows, the curve
  again_s      the same once more
  read_s, kernel_s, control_s   the stages of the job (what it tells in `seconds`)
What is checked (a failure stops the run), by numpy over the columns (the sums of the report are Neumaier's, in the
order of the rows: 1e-9 of numpy's pairwise sums): every row of the table — the blocks exactly, the volume, the
tonnes, the grades and their sums 1e-9, the blocks without a grade exactly — for the total and for every group of
each cut-off (no row more or less); the 100 points of the curve; the control `Σ групп = итог`; `again` is the same
answer bit by bit. The peak of memory includes the reference (a portion of numpy columns).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import blocks_common as bc
from . import resources_common as rc

KEYS = ("DOM", "GEO")
RADIX = (len(rc.DOM) + 1, len(rc.GEO) + 1)  # the digit 0 is «(пусто)»


def load() -> None:
    rc.load()  # the hook of `bench.run`: the kernels loaded, not timed


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        ctx.keep(**rc.prepare_model(ctx, int(params["rows"])))
    finally:
        ctx.close()


def shares(ch: Any) -> list[tuple[Any, Any]]:
    import numpy as np

    code = (ch.cols["DOM"].astype(np.int64) + 1) * RADIX[1] + (ch.cols["GEO"].astype(np.int64) + 1)
    return [(code, np.ones(ch.n))]


def labels(code: int) -> list[str]:
    d1, d2 = divmod(code, RADIX[1])
    return [rc.EMPTY if d1 == 0 else rc.DOM[d1 - 1], rc.EMPTY if d2 == 0 else rc.GEO[d2 - 1]]


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    mid = meta["model_id"]
    info: dict[str, Any] = {"rows": meta["rows"]}
    try:
        body = rc.request(mid, groups=list(KEYS), restrict=rc.RESTRICT, curve=100)
        ans, info["report_s"] = rc.run_job(ctx, body)
        again, info["again_s"] = rc.run_job(ctx, body)
        for k, v in (ans["seconds"] or {}).items():
            info[f"{k}_s"] = v
        t0 = time.perf_counter()
        edges = [*rc.CUTS, *ans["curve"]["cutoffs"]]
        ref = rc.reference(ctx.model(mid), edges, RADIX[0] * RADIX[1], shares, vol=rc.volume_of(5.0), restrict=True)
        info["reference_s"] = time.perf_counter() - t0
        info["table_rows"] = rc.check_rows(ans, ref, rc.CUTS, labels, len(KEYS))
        rc.check_curve(ans, ref, 100)
        assert ans["control"]["sum_groups"]["ok"], ans["control"]["sum_groups"]
        assert rc.same_bits(ans, again), "again"
        assert 0 < ans["rows"][0]["blocks"] < meta["rows"], "the restrictor"
        info["blocks"] = ans["rows"][0]["blocks"]
        info["groups"] = len(ref.present())
    finally:
        ctx.close()
    for k in ("report_s", "again_s", "reference_s"):
        info[k] = round(info[k], 3)
    info["peak_mb"] = bc.peak_mb()
    return info
