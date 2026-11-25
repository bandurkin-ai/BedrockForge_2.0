"""The job `estimate.run` whole, with the fields written to the model (ТЗ 8.8; ADR 0024 §7, «Замеры»: OK блок 4×4×2 на
20 млн блоков ≤ 25 мин на 4 ядрах, ≤ 4 ГБ; ci — 150 тыс. блоков по 20 тыс. проб на 2 ядрах).

A project of `estimate_world` (the set with the samples, the model of the variogram, the regular block model with DOM —
90 % of the columns «руда» —, one stored row of the plan: OK of a block 4×4×2, three passes, the domain «руда»). The
body of the handler `job.run_estimate`: the samples are read (`samples.load`: the trajectories, the table, the
averaging), the row is compiled, the model is read by chunks, the pieces of 2¹⁶ blocks go to the pool, the fields
(STATUS, EST, KV, NS, NH, AD, PASS, SLOPE, KE) are written by portions to a new version of the file, the row and the
graph are recorded (`replace` — a repeated run rewrites the fields).
  job_s              the whole call: the time of ТЗ;  forecast_20m_s — the straight line to 20 млн blocks
  samples_s, search_s, write_s   `stages` of the answer: the samples, the search and the kriging, the commit
  us_per_block       the cost of a block of the MODEL (a tenth of them is out of the domain and cheap)
  by_status, by_pass, first_share   the tallies of the answer
What is checked (a failure stops the run): the statuses of the answer sum to the rows of the model, STATUS 1 are the
blocks of «порода» exactly; the file after the job: the same statuses and no 0 in place of a gap (EST, KV, NS of a
block that is not estimated are empty); 40 blocks of the model — the estimate, the pass, the number of samples, the
variance (f4) — against the brute-force NumPy kriging of `tests/reference/krige3d_numpy.py` over the samples the job
read (1e−9; KV 1e−6): `ref_err`."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import estimate_common as ec
from . import estimate_world as ew

NAMES = ("STATUS", "EST", "KV", "NS", "PASS")


def prepare(params: dict[str, Any], work: Path) -> None:
    ew.build(work, params)


def load() -> None:
    from bf_core.geostat.estimate.warm import warm

    warm()


def read_back(ctx: Any, meta: dict[str, Any], probes: np.ndarray) -> tuple[dict[str, np.ndarray], list[int], int]:
    """The fields of the file by portions → ({name: values at the probes}, the rows by STATUS, the 0 in a gap)."""
    model = ctx.model(meta["model_id"])
    names = [f"{ew.PREFIX}{n}" for n in NAMES]
    got: dict[str, list[float]] = {n: [] for n in NAMES}
    by_status = np.zeros(4, np.int64)
    zeros = 0
    for chunk in model.iter(["DOM", *names]):
        st = chunk.cols[f"{ew.PREFIX}STATUS"]
        by_status += np.bincount(st.astype(np.int64), minlength=4)
        bad = st != 0
        for n in ("EST", "KV", "NS", "PASS"):
            col = np.asarray(chunk.cols[f"{ew.PREFIX}{n}"], dtype=np.float64)
            zeros += int((bad & (col == 0.0)).sum())  # a block without an estimate has a gap, never 0
            assert np.isnan(col[bad]).all(), f"{n}: a block that is not estimated has a value"
        at = probes[(probes >= chunk.start) & (probes < chunk.start + chunk.n)] - chunk.start
        for n in NAMES:
            got[n].extend(np.asarray(chunk.cols[f"{ew.PREFIX}{n}"], dtype=np.float64)[at].tolist())
    return {n: np.asarray(v) for n, v in got.items()}, by_status.tolist(), zeros


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.geostat.estimate import job

    threads = ec.limit_threads(params)
    ctx, meta = ew.open_world(work)
    dep = ec.Deposit.of(params)
    try:
        body = {"expected_version": ctx.version(meta["model_id"]), "replace": True,
                "estimation_ids": [meta["estimation"]]}  # fmt: skip
        t0 = time.perf_counter()
        out = job.run_estimate(ctx.engine, ctx.folder, body, threads=threads)
        job_s = time.perf_counter() - t0
        counts = out["rows"][0]["counts"]
        stages = out["stages"]
        info: dict[str, Any] = {"threads": threads, "samples": counts["samples"]["used"], "model_blocks": meta["rows"],
                                "job_s": round(job_s, 3), **ec.cost(job_s, meta["rows"]),
                                "by_status": counts["by_status"], "by_pass": counts["by_pass"],
                                "first_share": round(counts["by_pass"][0] / max(counts["by_status"][0], 1), 4),
                                "stages": {k: round(v, 3) for k, v in stages.items()}}  # fmt: skip
        for key in ("samples", "search", "write"):  # the stages of the answer (`job._Clock.stages`)
            info[f"{key}_s"] = round(float(stages[key]), 3)
        waste = (dep.counts[0] - ew.ore_columns(dep)) * dep.counts[1] * dep.counts[2]
        assert sum(counts["by_status"]) == counts["targets"] == meta["rows"], counts
        assert counts["by_status"][1] == waste and counts["by_status"][0] >= 0.99 * (meta["rows"] - waste), counts
        want = ("STATUS", "EST", "KV", "NS", "NH", "AD", "PASS", "SLOPE", "KE")
        assert out["fields"] == [f"{ew.PREFIX}{n}" for n in want]
        # the file as the job left it, against NumPy over the samples the job read
        probes = dep.probes(60)
        got, by_status, zeros = read_back(ctx, meta, probes)
        assert by_status == counts["by_status"] and zeros == 0, (by_status, zeros)
        _, s, rk = ew.look(ctx, meta)
        ore = (probes % dep.counts[0]) < ew.ore_columns(dep)
        assert np.array_equal(got["STATUS"][~ore], np.ones(int((~ore).sum()))) and ore.sum() >= 30
        ref = ec.reference_krige(dep, s, rk, "ok", ec.TWO, probes[ore])
        pick = {n: got[n][ore] for n in NAMES}
        pick["PASS"] = np.nan_to_num(pick["PASS"])
        info["ref_err"] = ec.assert_probes({**pick, "EST": pick["EST"]}, ref, "job", ("EST",))
        done = ref["PASS"] > 0
        kv = ec.rel_err(pick["KV"][done], ref["KV"][done])
        assert kv <= 1e-6, f"KV of the file differs from NumPy by {kv:.2e} (f4)"
        assert np.array_equal(pick["NS"][done], ref["NS"][done]), "NS of the file differs from NumPy"
        info["ref_kv_err"] = kv
        info["probes"] = int(ore.sum())
        info["peak_mb_job"] = ec.peak_mb()
        return info
    finally:
        ctx.close()
