"""Ore intervals by the ГКЗ method — the whole job `composite.ore_intervals` (ADR 0018 «Замеры», решение 16; ТЗ 7.35
Д-26, Т8.2-19: «50 тыс. скважин, 5 млн проб — ≤ 20 с; результат не зависит от числа потоков (1, 2, 4, 8 —
побитно)»; ci — 5 тыс., 500 тыс., ориентир ≤ 4 с).

The set of `_samples`; the field — AU of «Пробы» (г/т): heavy-tailed, with nuggets, ties at the detection limit,
1 % of the samples and whole runs of 20 without a value (they break the sampling line: «разрыв»), gaps between
the samples, holes without a collar (their rows have no X, Y, Z). The mode — «строгий, исключить» (strict_excl,
the default), Сб `cutoff` (near the median: ore and waste alternate every few samples — the many short lines
are the hard case of steps 2–4), Мрт `m_min`, Мпп `m_waste_max`; the thickness — the length along the hole; the
other 8 numeric fields are averaged into the table. The job is the function its handler calls —
`ore_job.run_ore`: the request, the columns read from the snapshot of the table (решение 16), the trajectories,
SHA-256 of the inputs, the kernel, the table «Рудные интервалы» built aside, and one final transaction with the
table, ORE_ID and ORE_CLS of the samples in place, the display and the record of the result:
  ore_s            the job from its start to the end of the final transaction — what Т8.2-19 counts;
  ore_read_s       `read_ore`;  ore_traj_s — the wait for the trajectories after it (they are built on a thread
                   while the samples are read);  ore_hash_s — SHA-256 of the inputs;
  ore_kernel_s     `ore_set.run_set`: Д-26 with the nested rows, the means of the fields, the summary (ADR: ≤ 5 с);
  ore_compute_s    the kernel with the samples made into its lines before it;
  ore_table_s      the table: the middles on the trajectories, its columns, `sink.build_table`;
  ore_fields_s     ORE_ID and ORE_CLS written into the rows of «Пробы» (`sink.write_columns`, inside the final
                   transaction);  ore_commit_s — the final transaction with them (its WAL is folded into the file
                   after it, on a thread: `dialect.fold_later`);  ore_snapshot_s — the snapshot of the new table,
                   after it;
  ore_rows         rows of the table;  ore_mb — the peak of the process at the end of the job (VmHWM), ore_read_mb —
                   when the samples were read;  ore_tick_gap_s — the longest wait between two ticks.
After the job (not in `ore_s`):
  hashes           different SHA-256 of the result among the job itself and `compute_ore` on the same samples with
                   1, 2, 4 and 8 threads (the rows, the classes and numbers of the samples, the means, the summary —
                   their bytes): 1 — the result does not depend on the threads (`limits`);  hash — its first 16
                   digits;  kernel_t1_s … kernel_t8_s — the computation with that many threads;
  balance_m        |Σ M of the rows with NESTED = 0 − the length of the samples with a grade| over that length,
  balance_mc       the same for Σ MC and Σ l·g — both against the arrays of the generator, ≤ 1e−9 (`limits`);
  check_s          ORE_ID and ORE_CLS read back from `dh_rows` (the snapshot of «Пробы» is of the revision before).
What is checked (a failure stops the run): the samples came from the snapshot and every one is read; the fields
read back are, sample for sample, what the kernel made (an empty ORE_ID is NaN, never 0); a class — for every
sample with a grade and for no other; a number — for the samples of the ore intervals and no other: the `ore`
ones have g ≥ Сб and their `waste_in` ones g < Сб; the table has the rows of the summary; the report is in
`dh_results`.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from . import _samples

STAGES = {"read": ("read",), "traj": ("traj",), "hash": ("hash",), "kernel": ("kernel",), "compute": ("compute",),
          "table": ("middles", "columns", "table"), "fields": ("fields",), "commit": ("commit",),
          "snapshot": ("snapshot",)}  # fmt: skip
FIELD = "AU"
THREADS = (1, 2, 4, 8)
NAME = "Замер: рудные интервалы"


def body(meta: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    out = {"set_id": meta["set_id"], "table": str(meta["samples"]), "field": FIELD,
           "cutoff": float(params.get("cutoff", 0.5)), "m_min": float(params.get("m_min", 2.0)),
           "m_waste_max": float(params.get("m_waste_max", 2.0)), "name": NAME}  # fmt: skip
    if params.get("mode"):
        out["mode"] = params["mode"]
    return out


def digest(made: Any) -> str:
    """SHA-256 of what `compute_ore` made: the bytes of the rows, of the fields of the samples, of the means and
    the summary."""
    res = made.res
    h = hashlib.sha256()
    for a in (res.intervals.iv_i, res.intervals.iv_f, res.ore_id, res.ore_cls, res.domain,
              *(res.fields[name] for name in sorted(res.fields))):  # fmt: skip
        h.update(f"{a.dtype.str}{a.shape}".encode())
        h.update(memoryview(a).cast("B") if a.flags.c_contiguous else a.tobytes())
    h.update(json.dumps(res.summary, sort_keys=True, ensure_ascii=False).encode())
    return h.hexdigest()


def job(
    meta: dict[str, Any], engine: Any, folder: Path, params: dict[str, Any], job_id: int = 1
) -> tuple[dict[str, Any], dict[str, float], _samples.Ticks, list[Any], list[str]]:
    """`ore_job.run_ore` with the clocks of its stages: (its result, the clocks, the ticks, the calls of
    `compute_ore` — what it was given and what it made, where the samples were read from)."""
    from bf_core.composites import ore_job, ore_set, sink, source

    read_from: list[str] = []
    ticks = _samples.Ticks()
    with _samples.captured(ore_job, "compute_ore") as calls, _samples.stages(
        (ore_job, "read_ore", "read"), (source, "trajectories", "traj"), (source, "stations_bytes", "hash"),
        (source, "input_hash", "hash"), (ore_job, "compute_ore", "compute"), (ore_set, "run_set", "kernel"),
        (ore_job, "middles", "middles"), (ore_job, "out_table", "columns"), (sink, "build_table", "table"),
        (sink, "write_columns", "fields"), (sink, "commit", "commit"), (sink, "write_snapshot", "snapshot"),
        note=lambda key, got: read_from.append(got[0].source) if key == "read" else None,
    ) as took:  # fmt: skip
        t0 = time.perf_counter()
        out = ore_job.run_ore(engine, folder, {"body": body(meta, params), "rows": meta["rows"]}, job_id=job_id,
                              tick=ticks)  # fmt: skip
        took["done"] = took["@commit"] - t0
    return out, took, ticks, calls, read_from


def threads_hashes(call: Any) -> tuple[set[str], dict[str, float]]:
    """The result of the job and of `compute_ore` on its samples with 1, 2, 4, 8 threads: their hashes and the
    seconds of every computation."""
    from bf_core.composites import ore_job

    (spec, src, dom, st), _kw, made = call
    found = {digest(made)}
    took: dict[str, float] = {}
    for threads in THREADS:
        t0 = time.perf_counter()
        again = ore_job.compute_ore(spec, src, dom, st, threads=threads)
        took[f"kernel_t{threads}_s"] = round(time.perf_counter() - t0, 3)
        found.add(digest(again))
        del again
    return found, took


def balance(report: dict[str, Any], ref: dict[str, Any]) -> tuple[float, float]:
    """Σ M and Σ MC of the rows with NESTED = 0 against the sampling lines of the generator, relative."""
    import math

    main = [c for c in report["summary"]["classes"] if not c["nested"]]
    m, mc = math.fsum(c["m"] for c in main), math.fsum(c["mc"] for c in main)
    return abs(m - ref["au_m"]) / ref["au_m"], abs(mc - ref["metal_au"]) / ref["metal_au"]


def check_fields(meta: dict[str, Any], engine: Any, made: Any, cutoff: float) -> None:
    """ORE_ID and ORE_CLS of «Пробы» read back against what the kernel made, and against the grades."""
    import numpy as np

    from bf_core.composites import source
    from bf_core.composites.ore_set import CLASS_NAMES

    back = source.read(engine, meta["set_id"], meta["samples"], [FIELD, "ORE_ID", "ORE_CLS"])
    assert back.source == "rows" and back.rows == meta["rows"], (back.source, back.rows)
    res = made.res
    number = back.numbers["ORE_ID"]
    want = np.where(res.ore_id == 0, np.nan, res.ore_id.astype(np.float64))
    assert np.array_equal(number, want, equal_nan=True)
    names = np.array([*back.labels["ORE_CLS"], ""], dtype=object)
    wanted = np.array([*CLASS_NAMES, ""], dtype=object)
    assert np.array_equal(names[back.codes["ORE_CLS"]], wanted[res.ore_cls])
    classed = back.codes["ORE_CLS"] >= 0
    graded = ~np.isnan(back.numbers[FIELD])
    assert np.array_equal(classed, graded) and int(classed.sum()) == meta["ref"]["au_n"], int(classed.sum())
    cls, grade = names[back.codes["ORE_CLS"]], back.numbers[FIELD]
    ore, inner = cls == "ore", cls == "waste_in"  # the samples of an ore interval: the ore ones and its waste
    assert ore.any() and np.array_equal(ore | inner, number > 0), (int(ore.sum()), int((number > 0).sum()))
    assert (grade[ore] >= cutoff).all() and (grade[inner] < cutoff).all()  # g = Сб — ore


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)


def load() -> None:
    def once(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        threads_hashes(job(meta, engine, folder, {})[3][0])

    _samples.warm(once)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder, restore_s = _samples.fresh(work)
    try:
        out, took, ticks, calls, read_from = job(meta, engine, folder, params)
        peak = _samples.peak_mb()
        report = _samples.report_of(engine, out["result_id"])
        found, kernels = threads_hashes(calls[0])
        t0 = time.perf_counter()
        check_fields(meta, engine, calls[0][2], body(meta, params)["cutoff"])
        check_s = time.perf_counter() - t0
    finally:
        engine.dispose()
    summary = report["summary"]
    assert read_from == [_samples.read_from(meta["rows"])], read_from
    assert not out["fresh"] and out["columns"] == ["ORE_ID", "ORE_CLS"], out
    assert out["rows"] == summary["rows"] == sum(c["count"] for c in summary["classes"]) > 0, out["rows"]
    assert not summary.get("bad") and not summary.get("overlap"), summary
    balance_m, balance_mc = balance(report, meta["ref"])
    by_class = {c["cls"]: c for c in summary["classes"]}
    return {
        "ore_s": round(took["done"], 3),
        **_samples.timings(took, STAGES, "ore_"),
        "ore_rows": out["rows"],
        "ore_intervals": by_class["ore"]["count"],
        "ore_read_mb": took.get("^read"),
        "ore_mb": peak,
        "ore_tick_gap_s": round(ticks.gap, 3),
        "hashes": len(found),
        "hash": sorted(found)[0][:16],
        **kernels,
        "balance_m": balance_m,
        "balance_mc": balance_mc,
        "check_s": round(check_s, 3),
        "rows": meta["rows"],
        "restore_s": round(restore_s, 3),
    }
