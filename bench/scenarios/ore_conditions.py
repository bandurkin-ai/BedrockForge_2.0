"""«Анализ кондиций» — the whole job `composite.conditions_analysis` (ADR 0018 решение 27: «анализ кондиций 12
вариантов ≤ 3 с (ci) / 30 с (full)»; ТЗ 7.35 Д-26, Т8.2-20; ci — 5 тыс. скважин, 500 тыс. проб, full — 50 тыс., 5 млн).

The set of `_samples`; the field — AU of «Пробы», as in `ore_intervals`. 12 variants: Сб `cutoffs` × Мрт `m_min` ×
Мпп `m_waste_max` (3 × 2 × 2), the mode «строгий, исключить»; every variant is a run of the kernel of Д-26 over
all the holes. The job is the function its handler calls — `ore_conditions_job.run_conditions`: the request
checked, the column read from the snapshot of the table (решение 16), the samples made into the lines of the
kernel, the variants (a task — a strip of 4096 holes in a variant, on threads), SHA-256 of the inputs, the record
`dh_results` without a table in one transaction:
  conditions_s         the job from its start to the end of that transaction — what решение 27 counts;
  conditions_read_s    `source.read`;  conditions_lines_s — `ore_lines`;
  conditions_kernel_s  `ore_conditions.conditions_analysis`: the 12 variants;  conditions_hash_s — SHA-256;
  conditions_write_s   the record of the result;
  variant_s            one variant by itself — `ore_set.run_set` without the nested rows, after the job;
  conditions_mb        the peak of the process at the end of the job (VmHWM);  conditions_tick_gap_s — the longest
                       wait between two ticks.
What is checked (a failure stops the run): 12 variants in the order (Мрт, Мпп, Сб), each with intervals; against
the arrays of the generator — Σ M of the ore is within the length of the samples with a grade and Σ MC within
their metal Σ l·g, the mean grade of the ore is not below Сб; along a group (Мрт, Мпп) the ore does not grow with
Сб and no variant is flagged for it; the variant (`cutoffs[1]`, `m_min[-1]`, `m_waste_max[0]`) is, bit for bit,
the class `ore` of the summary of a separate run of Д-26 with those parameters (number, Σ M, Σ MC); the report
is in `dh_results` and has no table.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import _samples

STAGES: dict[str, tuple[str, ...]] = {"read": ("read",), "lines": ("lines",), "kernel": ("kernel",),
                                      "hash": ("hash",), "write": ("record",)}  # fmt: skip
FIELD = "AU"
LISTS = {"cutoffs": [0.3, 0.5, 1.0], "m_min": [1.0, 2.0], "m_waste_max": [2.0, 4.0]}


def body(meta: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    lists = {k: [float(x) for x in params.get(k, v)] for k, v in LISTS.items()}
    return {"set_id": meta["set_id"], "table": str(meta["samples"]), "field": FIELD, **lists}


def job(
    meta: dict[str, Any], engine: Any, folder: Path, params: dict[str, Any], job_id: int = 1
) -> tuple[dict[str, Any], dict[str, float], _samples.Ticks, list[Any], list[str]]:
    """`run_conditions` with the clocks of its stages: (its result, the clocks, the ticks, the calls of
    `ore_lines`, where the samples were read from)."""
    from bf_core.composites import ore_conditions, ore_conditions_job, results, source

    read_from: list[str] = []
    ticks = _samples.Ticks()
    with _samples.captured(ore_conditions_job, "ore_lines") as calls, _samples.stages(
        (source, "read", "read"), (ore_conditions_job, "ore_lines", "lines"),
        (ore_conditions, "conditions_analysis", "kernel"), (source, "input_hash", "hash"),
        (results, "record", "record"),
        note=lambda key, got: read_from.append(got.source) if key == "read" else None,
    ) as took:  # fmt: skip
        t0 = time.perf_counter()
        out = ore_conditions_job.run_conditions(engine, folder, {"body": body(meta, params), "rows": meta["rows"]},
                                                job_id=job_id, tick=ticks)  # fmt: skip
        took["done"] = time.perf_counter() - t0
    return out, took, ticks, calls, read_from


def alone(call: Any, variant: dict[str, Any]) -> tuple[dict[str, Any], float]:
    """The class `ore` of the summary of one run of Д-26 with the parameters of `variant` on the lines of the
    job, and the seconds it took."""
    from bf_core.composites.ore_set import run_set
    from bf_core.composites.ore_types import OreParams

    _args, _kw, (lines, kernel, _journal) = call
    p = OreParams(variant["cutoff"], variant["m_min"], variant["m_waste_max"], mode=kernel.mode,
                  missing=kernel.missing, missing_max=kernel.missing_max).checked()  # fmt: skip
    t0 = time.perf_counter()
    res = run_set(lines, p, nested=False, threads=4)
    took = time.perf_counter() - t0
    return next(c for c in res.summary["classes"] if c["cls"] == "ore"), took


def check(variants: list[dict[str, Any]], lists: dict[str, list[float]], ref: dict[str, Any]) -> None:
    want = [(mrt, mpp, cb) for mrt in lists["m_min"] for mpp in lists["m_waste_max"] for cb in lists["cutoffs"]]
    assert [(v["m_min"], v["m_waste_max"], v["cutoff"]) for v in variants] == want, len(variants)
    for k, v in enumerate(variants):
        assert v["count"] > 0 and 0.0 < v["m_sum"] <= ref["au_m"] and 0.0 < v["mc_sum"] <= ref["metal_au"], v
        assert v["c_mean"] >= v["cutoff"] * (1.0 - 1e-9) and _samples.close(v["m_mean"] * v["count"], v["m_sum"], 1e-12)
        if k % len(lists["cutoffs"]):  # against the lower cutoff of its group
            below = variants[k - 1]
            assert v["m_sum"] <= below["m_sum"] and v["step_pct"] >= 0.0, (below["m_sum"], v["m_sum"])
            assert v["c_trim"] is None or v["c_trim"] < v["c_mean"], v  # what the lower cutoff adds is poorer
        else:
            assert v["step_pct"] is None and not v["step_flag"], v


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)


def load() -> None:
    def once(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        out, _took, _ticks, calls, _from = job(meta, engine, folder, {})
        alone(calls[0], out["variants"][0])

    _samples.warm(once)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder, restore_s = _samples.fresh(work)
    try:
        out, took, ticks, calls, read_from = job(meta, engine, folder, params)
        peak = _samples.peak_mb()
        report = _samples.report_of(engine, out["result_id"])
    finally:
        engine.dispose()
    lists = {k: v for k, v in body(meta, params).items() if k in LISTS}
    variants = report["variants"]
    assert read_from == [_samples.read_from(meta["rows"])], read_from
    assert not out["fresh"] and out["count"] == len(variants) == 12 and out["variants"] == variants, out["count"]
    assert report["samples"] == meta["rows"] and not report.get("excluded") and not report.get("written"), report
    check(variants, lists, meta["ref"])
    pick = next(v for v in variants if (v["cutoff"], v["m_min"], v["m_waste_max"]) ==
                (lists["cutoffs"][1], lists["m_min"][-1], lists["m_waste_max"][0]))  # fmt: skip
    ore, variant_s = alone(calls[0], pick)
    assert (pick["count"], pick["m_sum"], pick["mc_sum"]) == (ore["count"], ore["m"], ore["mc"]), (pick, ore)
    return {
        "conditions_s": round(took["done"], 3),
        **_samples.timings(took, STAGES, "conditions_"),
        "variants": len(variants),
        "variant_s": round(variant_s, 3),
        "ore_intervals": pick["count"],
        "flagged": sum(bool(v["step_flag"]) for v in variants),
        "conditions_mb": peak,
        "conditions_tick_gap_s": round(ticks.gap, 3),
        "rows": meta["rows"],
        "restore_s": round(restore_s, 3),
    }
