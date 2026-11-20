"""Declustering weights `DECL_W` — the whole job `composite.declus` (ADR 0018 «Замеры»: «100 тыс. точек, 25 × 8 —
≤ 5 с», full — 1 млн точек, ориентир ≤ 30 с; в ТЗ цели нет).

The set of `_samples` with `holes × per_hole` samples: the points are their middles on the trajectories, every
fifth hole stands in a tight cluster of infill drilling where the gold is three times the usual — the naive mean
is too high and cells bring it down; 1 % of the samples have no value, the holes without a collar have no
coordinates (their weight is empty, never 0). The job is the function its handler calls —
`jobs_prep.run_prep(…, "declus")`: the column read from the snapshot of the table (решение 16), the trajectories,
SHA-256, the middles, the weights by `method` ("cell3d": cells of `cmin`…`cmax` m, `ncell` + 1 sizes, `noff`
shifts, flattened by `anisz`; "cell" — the same in plan; "polygon"), the field written in place in one
transaction with the record of the result:
  declus_s           the job from its start to the end of the final transaction;
  declus_read_s      `read_prep`;  declus_traj_s — the trajectories;  declus_hash_s — SHA-256 of the inputs;
  declus_points_s    the middles of the samples;  declus_kernel_s — `declus_weights`;
  declus_write_s     the final transaction: DECL_W into the vectors of every row;
  declus_mb          the peak of the process at its end (VmHWM);  declus_tick_gap_s — the longest wait between ticks.
After the job the column is read back (`check_s`, from `dh_rows`: the snapshot of the table is of the revision
before). What is checked (a failure stops the run): a weight for every sample with a value and coordinates and
for no other, their sum is their number within 1e-9; a cell size was chosen (the weights are not all 1) and the
declustered mean is below the naive one; the report is in `dh_results`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import _samples

STAGES = {"read": ("read",), "traj": ("traj",), "hash": ("hash",), "points": ("points",), "kernel": ("kernel",),
          "write": ("commit",)}  # fmt: skip
FIELD = "AU"


def body(meta: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    keys = ("method", "cmin", "cmax", "ncell", "noff", "minmax", "anisy", "anisz", "nx", "ny")
    given = {k: params[k] for k in keys if k in params}
    return {"set_id": meta["set_id"], "table": str(meta["samples"]), "field": FIELD,
            **{"method": "cell3d", "cmin": 10.0, "cmax": 250.0, "ncell": 25, "noff": 8, "anisz": 0.2, **given}}  # fmt: skip


def job(
    meta: dict[str, Any], engine: Any, folder: Path, params: dict[str, Any], job_id: int = 1
) -> tuple[dict[str, Any], dict[str, float], Any]:
    """`jobs_prep.run_prep(…, "declus")` with the clocks of its stages."""
    from bf_core.composites import jobs_prep, sink, source
    from bf_core.stats import declus3d

    ticks = _samples.Ticks()
    with _samples.stages(
        (jobs_prep, "read_prep", "read"), (source, "trajectories", "traj"), (source, "stations_bytes", "hash"),
        (source, "input_hash", "hash"), (jobs_prep, "middles", "points"), (declus3d, "declus_weights", "kernel"),
        (sink, "commit", "commit"),
    ) as took:  # fmt: skip
        t0 = time.perf_counter()
        out = jobs_prep.run_prep(engine, folder, {"body": body(meta, params), "rows": meta["rows"]}, "declus",
                                 job_id=job_id, tick=ticks)  # fmt: skip
        took["done"] = took["@commit"] - t0
    return out, took, ticks


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)


def load() -> None:
    _samples.warm(lambda meta, engine, folder: job(meta, engine, folder, {"cmin": 5.0, "cmax": 50.0}), fields=3)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import numpy as np

    from bf_core.composites import source

    meta, engine, folder, restore_s = _samples.fresh(work)
    try:
        out, took, ticks = job(meta, engine, folder, params)
        peak = _samples.peak_mb()
        report = _samples.report_of(engine, out["result_id"])
        t0 = time.perf_counter()
        back = source.read(engine, meta["set_id"], meta["samples"], [FIELD, "DECL_W"])
        check_s = time.perf_counter() - t0
    finally:
        engine.dispose()
    w, grade = back.numbers["DECL_W"], back.numbers[FIELD]
    have = ~np.isnan(w)
    n = int(have.sum())
    assert out["columns"] == ["DECL_W"] and not out["fresh"], out
    assert n == report["n"] and n + report["n_missing"] == meta["rows"] and report["n_missing"] > 0, report["n"]
    assert not np.isnan(grade[have]).any() and (w[have] >= 0.0).all()  # no weight without a value, none below 0
    assert _samples.close(float(w[have].sum()), float(n), 1e-9), (float(w[have].sum()), n)
    assert not report["uniform"] and report["mean"] < report["naive_mean"], (report["mean"], report["naive_mean"])
    return {
        "declus_s": round(took["done"], 3),
        **_samples.timings(took, STAGES, "declus_"),
        "declus_mb": peak,
        "declus_tick_gap_s": round(ticks.gap, 3),
        "points": n,
        "best_size": report["best_size"],
        "mean_ratio": round(report["mean"] / report["naive_mean"], 4),
        "check_s": round(check_s, 3),
        "restore_s": round(restore_s, 3),
    }
