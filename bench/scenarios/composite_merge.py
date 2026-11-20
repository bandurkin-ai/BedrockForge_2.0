"""Merging of two interval tables — the whole job `composite.merge` (ADR 0018 «Замеры»; ТЗ 8.2.3: «Объединение
таблиц 5 млн + 1 млн интервалов — ≤ 30 с»; ci — 500 тыс. + 100 тыс.).

The set of `_samples`: «Пробы» (`holes × per_hole` samples of 10 fields, with gaps) and «Литология» (`holes ×
litho_per_hole` intervals whose contacts are multiples of 0.1 m and so fall inside the samples: nearly every
contact makes a new row). The job is the function its handler calls — `composites.jobs.run_tables(…, "merge")`:
both tables read from their snapshots (решение 16), SHA-256 of the inputs, the kernel, the columns of both
tables on the merged rows, the table built aside, the final transaction, the snapshot of the result:
  merge_s          from the start of the job to the end of the final transaction — what the target counts;
  merge_read_s     `source.read` of both tables;  merge_hash_s — SHA-256 of the inputs;
  merge_kernel_s   `merge.merge_tables` — the common boundaries;  merge_compute_s — with the 11 columns laid on
                   the rows and the summary «до/после»;
  merge_write_s    the table and the final transaction;  merge_snapshot_s — the snapshot, after it;
  merge_rows       rows of the result;  merge_mb — the peak of the process (VmHWM);  merge_tick_gap_s — the
                   longest wait between two ticks.
What is checked (a failure stops the run): the result is where either table is — Σ of its lengths is the length
of the holes the lithology has (it covers them whole) and the sampled length of the others — and the metal
Σ l·AU of the samples is kept, both within 1e-9; no fewer rows than samples; the report is in `dh_results`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import _samples

STAGES = {"read": ("read",), "hash": ("hash",), "kernel": ("kernel",), "compute": ("compute",),
          "write": ("table", "commit"), "snapshot": ("snapshot",)}  # fmt: skip


def job(
    meta: dict[str, Any], engine: Any, folder: Path, job_id: int = 1
) -> tuple[dict[str, Any], dict[str, float], Any]:
    """`composites.jobs.run_tables(…, "merge")` with the clocks of its stages."""
    from bf_core.composites import compose_tables, merge, sink, source
    from bf_core.composites import jobs as cj

    body = {"set_id": meta["set_id"], "tables": [str(meta["samples"]), str(meta["litho"])],
            "name": "Замер: объединение"}  # fmt: skip
    ticks = _samples.Ticks()
    with _samples.stages(
        (source, "read", "read"), (source, "input_hash", "hash"), (compose_tables, "merge_compute", "compute"),
        (merge, "merge_tables", "kernel"), (sink, "build_table", "table"), (sink, "commit", "commit"),
        (sink, "write_snapshot", "snapshot"),
    ) as took:  # fmt: skip
        t0 = time.perf_counter()
        out = cj.run_tables(engine, folder, {"body": body, "rows": meta["rows"] + meta["litho_rows"]}, "merge",
                            job_id=job_id, tick=ticks)  # fmt: skip
        took["done"] = took["@commit"] - t0
    return out, took, ticks


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)


def load() -> None:
    _samples.warm(lambda meta, engine, folder: job(meta, engine, folder))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder, restore_s = _samples.fresh(work)
    try:
        out, took, ticks = job(meta, engine, folder)
        report = _samples.report_of(engine, out["result_id"])
    finally:
        engine.dispose()
    ref, after = meta["ref"], report["summary"]["after"]
    assert not out["fresh"] and out["rows"] == after["count"] >= meta["rows"], out
    assert _samples.close(after["length"], ref["merge_m"], 1e-9), (after["length"], ref["merge_m"])
    assert _samples.close(after["fields"]["AU"]["metal"], ref["metal_au"], 1e-9), (after["fields"]["AU"], ref)
    return {
        "merge_s": round(took["done"], 3),
        **_samples.timings(took, STAGES, "merge_"),
        "merge_rows": out["rows"],
        "merge_mb": _samples.peak_mb(),
        "merge_tick_gap_s": round(ticks.gap, 3),
        "rows": meta["rows"],
        "litho_rows": meta["litho_rows"],
        "restore_s": round(restore_s, 3),
    }
