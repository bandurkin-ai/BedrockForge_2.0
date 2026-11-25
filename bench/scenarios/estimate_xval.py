"""Cross-validation of a row (ТЗ 8.8.1; ADR 0024 §8, «Замеры»: 100 тыс. проб ≤ 2 мин на 4 ядрах; ci — 20 тыс. проб на 2
ядрах).

The deposit of `estimate_common` (holes with infill, twins, outlier), OK of a point with two structures, three
passes; `xval.cross_validate` leaves out a whole hole (`hole_s`, the mode of the window) and a sample (`sample_s`) for
every sample of the domain and estimates it from the rest — the job `estimate.xval` without its file and the
database; then the summary (`summary_s`: the figures of the window, the histogram of the errors) and the Parquet of
the answer (`parquet_s`, `parquet_mb`).
  samples, hole_s, sample_s, summary_s, parquet_s   the stages;  forecast_100k_s — `hole_s` for 100 тыс. samples
  estimated_hole, estimated_sample   the samples that got an estimate
What is checked (a failure stops the run): the tallies; 3 samples of each mode against the estimate from the rest of
the samples by the kernel (`samples.take`: the same bits, Т8.8-12) and against the brute-force NumPy kriging of
`tests/reference/krige3d_numpy.py` (1e−9 relative, `ref_err`); the error of the table is `est − true`; the summary
counts the samples of the status 0."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import estimate_common as ec


def prepare(params: dict[str, Any], work: Path) -> None:
    ec.keep(work, ec.Deposit.of(params))


def load() -> None:
    from bf_core.geostat.estimate.warm import warm

    warm()


def _left_out(spec: Any, s: Any, rest: np.ndarray, point: np.ndarray) -> Any:
    from bf_core.geostat.estimate import kernel as kn
    from bf_core.geostat.estimate import xval

    return kn.estimate(kn.compile_row(xval.xval_spec(spec), s.take(rest)), point)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.geostat.estimate import xval

    threads = ec.limit_threads(params)
    dep = ec.Deposit.of(params)
    s = ec.open_samples(work)
    spec = ec.spec(dep, "ok", variogram=ec.TWO)
    info: dict[str, Any] = {"threads": threads, "samples": s.n}
    t0 = time.perf_counter()
    by_hole = xval.cross_validate(spec, s, "hole", threads=threads)
    t1 = time.perf_counter()
    by_sample = xval.cross_validate(spec, s, "sample", threads=threads)
    t2 = time.perf_counter()
    summary = by_hole.summary()
    xval.histogram(by_hole.error)
    t3 = time.perf_counter()
    blob = xval.to_parquet(by_hole, {"by": "hole", "estimation_id": 1})
    t4 = time.perf_counter()
    info.update(hole_s=round(t1 - t0, 3), sample_s=round(t2 - t1, 3), summary_s=round(t3 - t2, 3),
                parquet_s=round(t4 - t3, 3), parquet_mb=round(len(blob) / 2**20, 2),
                forecast_100k_s=round((t1 - t0) * 100_000 / s.n, 1),
                estimated_hole=int((by_hole.status == 0).sum()),
                estimated_sample=int((by_sample.status == 0).sum()))  # fmt: skip
    assert by_hole.n == by_sample.n == int((s.dom == 0).sum()) and (by_hole.i == np.arange(s.n)).all()
    assert info["estimated_hole"] >= 0.9 * s.n and info["estimated_sample"] >= 0.99 * s.n, info
    assert np.array_equal(by_hole.error, by_hole.est - by_hole.true, equal_nan=True)
    assert summary["by_status"][0] == info["estimated_hole"] and summary["n"] == info["estimated_hole"], summary
    worst = 0.0
    ang, rad, passes = ec._ellipsoid(ec.row(dep, "ok", s, variogram=ec.TWO))
    rng = np.random.default_rng(5)
    for table, mode in ((by_sample, "sample"), (by_hole, "hole")):
        done = np.flatnonzero(table.status == 0)
        for k in rng.choice(done, 3, replace=False):
            rest = (np.delete(np.arange(s.n), k) if mode == "sample" else np.flatnonzero(s.hole != s.hole[k]))
            want = _left_out(spec, s, rest, s.xyz[k : k + 1])
            assert want.status[0] == 0 and table.est[k] == want.values["EST"][0], (mode, int(k))  # the same bits
            assert table.kv[k] == want.values["KV"][0] and table.ns[k] == want.values["NS"][0], (mode, int(k))
            ref = ec._numpy().krige_blocks(
                s.xyz[rest], s.z[rest], s.hole[rest], s.xyz[k : k + 1], model=ec.TWO, kind="ok", size=ec.SIZE,
                disc=(1, 1, 1), phi_deg=0.0, angles=ang, radii=rad, passes=passes)  # fmt: skip
            assert ref["PASS"][0] == table.pass_[k], (mode, int(k))
            worst = max(worst, ec.rel_err(np.array([table.est[k]]), ref["EST"]),
                        ec.rel_err(np.array([table.kv[k]]), ref["KV"]))  # fmt: skip
    assert worst <= ec.REL_NUMPY, f"the cross-validation differs from the NumPy kriging by {worst:.2e}"
    info["ref_err"] = worst
    return info
