"""Indicator kriging over the blocks of a model (ТЗ 8.8.3; ADR 0024 §9; full — 20 млн блоков по 100 тыс. проб на 4
ядрах, ci — 200 тыс. блоков по 20 тыс. проб на 2 ядрах; цели ТЗ нет — слежение за временем и расходом).

The deposit, the search and the portions of `estimate_idw`; five thresholds (the 10, 30, 50, 70, 90 % quantiles of the
samples), one model of the indicator for all of them («median»), blocks 4×4×2: every block is five block krigings on
the one selection, the order relations corrected, ETYPE from the class means.
  samples_s, row_s, first_s, run_s, us_per_block, forecast_20m_s, the tallies of the passes — as in `estimate_idw`
  per_threshold_us   the cost of a block per threshold
What is checked (a failure stops the run): the tallies as in `estimate_idw`; on EVERY block P1 ≤ … ≤ P5 inside [0; 1]
(`bad_order`, `outside`), ETYPE finite, not negative and not above the largest sample; 40 blocks spread over the model
against the NumPy kriging of the five indicators ([z ≤ t] of the samples, one model): where the five clipped answers
are already ordered the kernel gives them as they are — 1e−9 (`ref_err`, `ref_checked` blocks)."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import estimate_common as ec

DISC = (4, 4, 2)
QUANTILES = (0.1, 0.3, 0.5, 0.7, 0.9)
MODEL = {"c0": 0.05, "common_axes": True,
         "structures": [{"type": "spherical", "c": 0.2, "a1": 90.0, "a2": 70.0, "a3": 40.0, "ang1": ec.ANG1}]}


def prepare(params: dict[str, Any], work: Path) -> None:
    ec.keep(work, ec.Deposit.of(params))


def load() -> None:
    from bf_core.geostat.estimate.warm import warm

    warm()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    threads = ec.limit_threads(params)
    dep = ec.Deposit.of(params)
    t0 = time.perf_counter()
    s = ec.open_samples(work)
    th = [float(x) for x in np.quantile(s.z, QUANTILES)]
    t1 = time.perf_counter()
    rk = ec.row(dep, "ik", s, variogram=MODEL, disc=DISC, method={"ik": {"thresholds": th, "models": "median"}})
    t2 = time.perf_counter()
    k = len(th)
    names = [f"P{i + 1}" for i in range(k)]
    bad = {"order": 0, "outside": 0, "etype": 0}

    def each(res: Any) -> None:
        p = np.column_stack([res.values[n] for n in names])
        ok = res.status == 0
        p = p[ok]
        bad["order"] += int((np.diff(p, axis=1) < -1e-12).any(axis=1).sum())
        bad["outside"] += int(((p < -1e-12) | (p > 1.0 + 1e-12)).any(axis=1).sum())
        e = res.values["ETYPE"][ok]
        bad["etype"] += int((~np.isfinite(e) | (e > float(s.z.max()) * (1 + 1e-9)) | (e < 0.0)).sum())

    probes = dep.probes()
    out = ec.run_blocks(rk, dep, threads, probes=probes, keys=("PASS", "NS", "ETYPE", *names), each=each)
    run_s = time.perf_counter() - t2
    info: dict[str, Any] = {"threads": threads, "samples": s.n, "model_blocks": out["blocks"], "thresholds": k,
                            "samples_s": round(t1 - t0, 3), "row_s": round(t2 - t1, 3), "run_s": round(run_s, 3),
                            "first_s": round(out["first_s"], 3), **ec.cost(run_s, out["blocks"]), **ec.shares(out),
                            "per_threshold_us": round(1e6 * run_s / max(out["blocks"], 1) / k, 3),
                            "bad_order": bad["order"], "outside": bad["outside"],
                            "bad_etype": bad["etype"]}  # fmt: skip
    assert out["blocks"] == dep.rows and out["by_status"][0] >= 0.99 * dep.rows, out["by_status"]
    assert not any(bad.values()), f"IK broke its own rules: {bad}"
    raw = []
    for t in th:
        ref = ec.reference_krige(dep, s, rk, "ok", MODEL, probes, z=(s.z <= t).astype(np.float64))
        raw.append(np.clip(ref["EST"], 0.0, 1.0))
    ref_p = np.array(raw).T
    done = ~np.isnan(ref_p).any(axis=1)
    ordered = done & (np.diff(ref_p, axis=1) >= 0.0).all(axis=1)  # the order relations have nothing to correct there
    got = np.column_stack([out["probe"][n] for n in names])
    assert np.array_equal(~np.isnan(got[:, 0]), done), "the estimated blocks differ from the reference"
    info["ref_checked"] = int(ordered.sum())
    assert ordered.sum() >= 10, f"only {int(ordered.sum())} probes are ordered: nothing to compare"
    info["ref_err"] = float(np.abs(got[ordered] - ref_p[ordered]).max())
    assert info["ref_err"] <= ec.REL_NUMPY, f"P1…P{k} differ from the reference by {info['ref_err']:.2e}"
    assert not math.isnan(info["ref_err"])
    return info
