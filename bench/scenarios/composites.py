"""Composites by length and by domains — the whole job `composite.run` (ADR 0018 «Замеры»; ТЗ 8.2.3: «Композиты по
длине или доменам — ≤ 30 с, ≤ 2 ГБ» на 50 тыс. скважин, 5 млн проб, 10 полей; ci — 5 тыс., 500 тыс.).

The set of `_samples` («Пробы»: 8 grades, DENSITY, the text ROCK; «Литология» with contacts inside the samples).
Every method of `methods` is one job — the function its handler calls, `composites.jobs.run`: the request, the
read of the chosen columns from the snapshot of the table (решение 16), the trajectories, SHA-256 of the inputs,
the kernel, the table built aside, the final transaction, the snapshot of the result:
  length  L = `length` m over the whole holes, 9 fields weighted by DENSITY;
  domain  the same inside the stretches of one rock: `domain` "table" — ROCK of «Литология» (another table: its
          rows are laid on the holes of the samples), "field" — ROCK of the samples themselves;
  bench   benches of `bench` m from the highest collar (`composite_bench` runs this module with it).
Before every job the project is as `prepare` left it (`_samples.reset`: on a repeated run — the earlier result
deleted, `reset_s`), so a job never sees the table of another one. Per method `m`:
  m_s           the job from its start to the end of the final transaction — what the target of ТЗ counts;
  m_read_s      `source.read` (the samples, the table of the domain);  m_traj_s — the trajectories of the set;
  m_hash_s      SHA-256 of the inputs;  m_kernel_s — the kernel alone (ADR: ≤ 3 с);  m_compute_s — the kernel
                with the lines before it, the columns and the summary «до/после» after it;
  m_write_s     the table built aside and the final transaction;  m_snapshot_s — the snapshot, after it;
  m_rows        composites;  m_mb — the peak of the process so far (VmHWM);  m_tick_gap_s — the longest wait
                between two ticks: how long a cancel may stay unanswered.
`cold_read_s` (`cold: true`) — the same columns read from `dh_rows` without the snapshot, timed in `prepare`: the
line of the report without a budget (решение 16). What is checked (a failure stops the run): the samples came
from the snapshot, every sample is read, the composites of every hole cover its stretch — Σ LENGTH is the sum
of the holes to their TD (to the bottom of the last sample without one; benches: of the holes with a collar)
within 1e-9 of it — and the report is in `dh_results`.
"""

from __future__ import annotations

import gc
import time
from pathlib import Path
from typing import Any

from . import _samples

STAGES = {"read": ("read",), "traj": ("traj",), "hash": ("hash",), "kernel": ("kernel",), "compute": ("compute",),
          "write": ("table", "commit"), "snapshot": ("snapshot",)}  # fmt: skip
NAMES = {"length": "Замер: по длине", "domain": "Замер: по доменам", "bench": "Замер: по уступам"}


def body(meta: dict[str, Any], method: str, params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"set_id": meta["set_id"], "table": str(meta["samples"]), "method": method,
                           "fields": [*meta["grades"], _samples.ROCK], "density": _samples.DENSITY,
                           "name": NAMES[method]}  # fmt: skip
    if method == "bench":
        out["bench"] = float(params.get("bench", 10.0))
    else:
        out["length"] = float(params.get("length", 2.0))
    if method == "domain":
        other = params.get("domain", "table") == "table"
        out["domain"] = {"table": str(meta["litho"]), "field": _samples.ROCK} if other else _samples.ROCK
    return out


def job(
    meta: dict[str, Any], engine: Any, folder: Path, method: str, params: dict[str, Any], job_id: int = 1
) -> tuple[dict[str, Any], dict[str, float], _samples.Ticks, list[str]]:
    """`composites.jobs.run` with the clocks of its stages: (its result, the clocks, the ticks, where every table
    was read from)."""
    from bf_core.composites import benches, compose, domains, length, sink, source
    from bf_core.composites import jobs as cj

    kernels = {"length": [(length, "composite_by_length", "kernel")],
               "domain": [(domains, "domain_stretches", "kernel"), (domains, "composite_by_domains", "kernel")],
               "bench": [(benches, "composite_by_benches", "kernel")]}[method]  # fmt: skip
    read_from: list[str] = []
    ticks = _samples.Ticks()
    with _samples.stages(
        (source, "read", "read"), (source, "trajectories", "traj"), (source, "stations_bytes", "hash"),
        (source, "input_hash", "hash"), (compose, "compute", "compute"), *kernels, (sink, "build_table", "table"),
        (sink, "commit", "commit"), (sink, "write_snapshot", "snapshot"),
        note=lambda key, got: read_from.append(got.source) if key == "read" else None,
    ) as took:  # fmt: skip
        t0 = time.perf_counter()
        out = cj.run(engine, folder, {"body": body(meta, method, params), "rows": meta["rows"]}, job_id=job_id,
                     tick=ticks)  # fmt: skip
        took["done"] = took["@commit"] - t0
    return out, took, ticks, read_from


def measure(meta: dict[str, Any], engine: Any, folder: Path, method: str, params: dict[str, Any]) -> dict[str, Any]:
    out, took, ticks, read_from = job(meta, engine, folder, method, params)
    report = _samples.report_of(engine, out["result_id"])
    ref = meta["ref"]
    want = ref["span_collar_m"] if method == "bench" else ref["span_m"]
    got = report["summary"]["after"]["length"]
    assert read_from[0] == "parquet", read_from
    assert not out["fresh"] and out["rows"] == report["summary"]["after"]["count"] > 0, out
    assert report["rows_in"] == meta["rows"] and report["skipped"] == 0, (report["rows_in"], report["skipped"])
    assert _samples.close(got, want, 1e-9), (method, got, want)
    return {
        f"{method}_s": round(took["done"], 3),
        **_samples.timings(took, STAGES, f"{method}_"),
        f"{method}_rows": out["rows"],
        f"{method}_mb": _samples.peak_mb(),
        f"{method}_tick_gap_s": round(ticks.gap, 3),
    }


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params, cold=bool(params.get("cold")))


def load() -> None:
    def jobs(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        for k, method in enumerate(("length", "domain", "bench")):
            job(meta, engine, folder, method, {}, job_id=k + 1)

    _samples.warm(jobs)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder = _samples.opened(work)
    info: dict[str, Any] = {"rows": meta["rows"]}
    if "cold_read_s" in meta:
        info["cold_read_s"] = meta["cold_read_s"]
    reset_s = 0.0
    try:
        for method in params.get("methods", ["length", "domain"]):
            reset_s += _samples.reset(engine, folder, meta)
            gc.collect()
            info.update(measure(meta, engine, folder, method, params))
    finally:
        engine.dispose()
    info["reset_s"] = round(reset_s, 3)
    return info
