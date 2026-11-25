"""The checks of a block model: duplicates, overlaps, the volume, the empty and the constant fields (ТЗ 8.7.1; ADR 0023
§2; Т8.7-5, 7.31: «перекрытия → находка»; 17.12 asked ci ≤ 0,7 с for 2 млн, full ≤ 20 с for 20 млн).

The model of `tools/gen/blockmodel.py` (`rows` rows; 25 % of the parents split into eight octants, the rest
whole; a grid of 4 × 4 × 2 sub-cells; the fields AU, DENSITY, F2, F3 and the category DOM). `checks.check_cells` —
what the job `blocks.check` and the import of a file run over the stored file (`ModelChecker.feed` by the portions
of the file in the order of the key); `check_s` is its time. What is checked (a failure stops the run): no finding
of an error, 0 duplicates, 0 overlaps, and the volume of the rows equals the parents × the volume of a parent
(1e-12; every parent of the generator is occupied and its eighths cover it).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import blocks_common as bc


def prepare(params: dict[str, Any], work: Path) -> None:
    ctx = bc.Ctx.create(work)
    try:
        ctx.keep(**bc.sub_model(ctx, int(params["rows"]), fields=int(params.get("fields", 4))))
    finally:
        ctx.close()


def load() -> None:
    bc.warm()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.blocks import checks

    bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    model = ctx.model(meta["model_id"])
    info: dict[str, Any] = {"rows": model.rows, "parents": model.parents}
    try:
        t0 = time.perf_counter()
        report = checks.check_cells(ctx.store, model.shas()["cells"], model.meta)
        info["check_s"] = round(time.perf_counter() - t0, 3)
        info["findings"] = [f["code"] for f in report.findings]
        assert report.ok and report.rows == model.rows, (report.findings, report.rows)
        assert report.finding("duplicates") is None and report.finding("overlaps") is None, report.findings
        a, b, c = model.grid.regular()
        whole = model.parents * float(a * b * c)
        assert abs(report.checks["volume"] - whole) <= 1e-12 * whole, (report.checks["volume"], whole)
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
