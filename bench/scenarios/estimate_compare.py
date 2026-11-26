"""«Сравнить оценки» — the job `estimate.compare` (ТЗ 07-35 Д-37; ADR 0027 решение 25 и «Замеры»: Δ на 20 млн блоков
+ таблица по доменам, ориентир ≤ 5 с вне ТЗ; подшаг 20.10).

A regular model of `rows` blocks: the category DOM (5 domains, 2 % without a label) and two estimates of it, OK and ID,
as the job of the estimate writes them (`source.op = "estimate"`): OK_EST, ID_EST (f8, 8 % of the blocks have no value,
the two sets of gaps differ) and their STATUS (i1: 0 where there is a value, else 1 or 2). The job `estimate.compare`
(`compare_job.run_compare`, the body of the handler) is the timed part: the read of the six fields, Δ = A − B, the
statistics (Chan merge, percentiles by Hazen), the histogram, the table by domains weighted by volume, the changes of
STATUS, the write of the field Δ and the record in the graph.
  compare_s            the whole call; stages read_s / calc_s / write_s are `stages` of the answer
  forecast_20m_s       the straight line to 20 млн blocks (the goal ≤ 5 s is of the full profile)
  n, changed, blocks_per_s
What is checked (a failure stops the run): the file after the job against numpy over the whole model (portions): Δ is
A − B and a gap in A or B is a gap, never 0; the number of Δ (`stats.n`), the sum of the blocks of the domains, the
number of blocks whose STATUS changed; the mean of Δ to 1e-9.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc

DOMAINS = 5
EST = {"op": "estimate", "params": {}, "inputs": [], "algo": "estimate-1"}


def fields() -> list[dict[str, Any]]:
    return [{"name": "DOM", "dtype": "cat", "labels": [f"D{i}" for i in range(DOMAINS)], "role": "code"}]


def make_dom(rng: np.random.Generator, n: int) -> dict[str, np.ndarray]:
    codes = rng.integers(0, DOMAINS, n).astype(np.int32)
    codes[rng.random(n) < 0.02] = -1  # without a label
    return {"DOM": codes}


def estimate(rng: np.random.Generator, n: int, shift: float) -> tuple[np.ndarray, np.ndarray]:
    """(EST, STATUS) of one estimate: 8 % of the blocks have no value (STATUS 1 or 2); the value is lognormal."""
    est = rng.lognormal(0.0, 1.0, n) + shift
    gap = rng.random(n) < 0.08
    est[gap] = np.nan
    status = np.where(gap, rng.integers(1, 3, n), 0).astype(np.float64)
    return est, status


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.blocks.fields_store import FieldsWriter, commit_fields
    from bf_core.db import dialect

    ctx = bc.Ctx.create(work)
    try:
        model = bc.regular_model(ctx, int(params["rows"]), fields(), make_dom)
        view = ctx.model(model["model_id"])
        rng = np.random.default_rng(7)
        specs = [("OK_EST", "f8"), ("OK_STATUS", "i1"), ("ID_EST", "f8"), ("ID_STATUS", "i1")]
        with FieldsWriter(ctx.store, view, view.version) as w:  # type: ignore[arg-type]
            for name, dtype in specs:
                w.add(name, dtype, unit="g/t" if dtype == "f8" else None)
            for lo in range(0, view.rows, bc.PORTION):
                n = min(bc.PORTION, view.rows - lo)
                ok, ok_s = estimate(rng, n, 0.0)
                idw, id_s = estimate(rng, n, 0.05)
                w.write({"OK_EST": ok, "OK_STATUS": ok_s, "ID_EST": idw, "ID_STATUS": id_s})
            staged = w.finish()
        try:
            with dialect.write_tx(ctx.engine) as conn:
                commit_fields(conn, ctx.store, staged, source=EST)
        finally:
            staged.discard()
        ctx.keep(**model)
    finally:
        ctx.close()


def load() -> None:
    from bf_core.blocks.warm import warm

    warm()


def check(ctx: bc.Ctx, model_id: int, out: dict[str, Any]) -> None:
    """The field Δ and the numbers of the answer against numpy, portion by portion."""
    names = ["OK_EST", "ID_EST", "OK_STATUS", "ID_STATUS", out["field"]]
    n = changed = 0
    total = 0.0
    for chunk in ctx.model(model_id).iter(names, bc.PORTION):
        c = chunk.cols
        want = np.asarray(c["OK_EST"], dtype=np.float64) - np.asarray(c["ID_EST"], dtype=np.float64)
        got = np.asarray(c[out["field"]], dtype=np.float64)
        assert np.array_equal(np.isnan(got), np.isnan(want)), "a gap of A or B is not a gap of Δ"
        ok = np.isfinite(want)
        assert np.allclose(got[ok], want[ok], rtol=1e-12, atol=0.0), "Δ is not A − B"
        n += int(ok.sum())
        total += float(want[ok].sum())
        changed += int((c["OK_STATUS"] != c["ID_STATUS"]).sum())
    assert n == out["stats"]["n"], (n, out["stats"]["n"])
    assert abs(total / n - out["stats"]["mean"]) < 1e-9, "the mean of Δ"
    assert changed == out["status"]["changed"], (changed, out["status"]["changed"])
    assert sum(d["blocks"] + d["missing"] for d in out["domains"]) == ctx.model(model_id).rows


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.blocks import compare_job

    bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    try:
        body = {"model_id": meta["model_id"], "field_a": "OK_EST", "field_b": "ID_EST", "domain": "DOM"}
        t0 = time.perf_counter()
        out = compare_job.run_compare(ctx.engine, ctx.folder, body)
        seconds = time.perf_counter() - t0
        assert out["rows"] == meta["rows"] and out["field_written"] is True, out["rows"]
        info: dict[str, Any] = {"rows": meta["rows"], "compare_s": round(seconds, 3),
                                "read_s": out["stages"].get("read"), "calc_s": out["stages"].get("calc"),
                                "write_s": out["stages"].get("write"), "n": out["stats"]["n"],
                                "changed": out["status"]["changed"],
                                "blocks_per_s": round(meta["rows"] / max(seconds, 1e-9)),
                                "forecast_20m_s": round(seconds * 20_000_000 / max(meta["rows"], 1), 2)}  # fmt: skip
        check(ctx, meta["model_id"], out)
    finally:
        ctx.close()
    info["peak_mb"] = bc.peak_mb()
    return info
