"""Experimental variograms of three axes and along the holes — the whole job `variography.experimental` (ADR 0022
«Замеры», решения 10, 24; ТЗ 8.6.4: «200 тыс. композитов, h_max = 300 м: 3 оси + вдоль скважин, 20 лагов — ≤ 15 с,
≤ 2 ГБ»; ci — 50 тыс., ориентир ≤ 3 с).

The data (ADR «Замеры»): the set of `_samples` (`tools/gen/drillholes.py::arrays` — collars on a grid of 25 m with
clustered infill drilling, curved holes, holes without a survey and without a collar, 1 m samples with gaps, ties
at the detection limit, nuggets, unassayed runs) composited to 2 m by the job «Композиты» itself in `prepare`
(`composites.jobs.run`, length 2 m, the grade AU): `holes × per_hole / 2` composites (ci — 500 holes, 50 тыс.;
full — 2000 holes, 200 тыс.), the snapshot of the table ready (решение 24, as решение 16 of ADR 0018). The request:
the axes of (ang1 30°, ang2 −10°, ang3 0°), L = 15 m × 20 lags (h_max = 20·L + L/2 = 307,5 m), the tolerances and
bands by default (22,5°, 3L, 2L), along the holes 2 m × 20 lags. The job is the function its handler calls —
`jobs.run_experimental` — with clocks around the functions of its stages:
  axes_s          the job from its start to the record written — what the target of ТЗ counts;
  axes_read_s, axes_traj_s, axes_pairs_s, axes_dh_s, axes_write_s   the stages the job reports itself
                  (`job.result.stages`: read, trajectories, pairs, downhole, write);
  axes_plan_s     `pairs.prepare`: the grid, the estimate of the pair checks over all the points (and the plan of
                  the sample, when there is one);  axes_sample_s — `points.sample`;
  axes_kernel_s   `pairs.run` — the kernel of the axes;  axes_ns_check — its nanoseconds a pair check (wall time at
                  `threads` threads);  axes_ns_check_thread — the same × threads;
  checks, estimate   pair checks of the run and of all the points;  pairs — the pairs accepted by the axes;
  sampled_n       the points of the sample (`sampled.n`; all of them — no sample);  points — the points;
  axes_mb         the peak of the process at the end of the job (VmHWM, `limits`: 2 ГБ);  axes_tick_gap_s — the
                  longest wait between two ticks (a cancel is answered within it).
After the job (not in `axes_s`):
  hashes          different SHA-256 of the sums of the pairs (axes and along the holes) among the job itself and
                  `jobs.compute` on its points with 1, 2, 4 and 8 threads: 1 (`limits`);  hash — its first 16
                  digits;  kernel_t1_s … kernel_t8_s — that computation;
  brute_error     the largest relative difference (to max(1, |x|)) of h̄, γ, ρ, the pairwise relative, the means
                  of the tails and the heads between `experimental.calculate` over a sample of `brute` (2000)
                  points of the job and every pair on NumPy (`tests/ref_bf_variogram.brute_force`) — `limits`:
                  1e−12; N exactly;  dh_brute_error — the same along the holes over the points of the first holes
                  (≈ `brute` points; `ref_bf_variogram.brute_downhole`);  brute_rho_flat, dh_rho_flat — the lags
                  whose tails or heads are all equal (ties at the detection limit): the kernel has no ρ there
                  (σ = 0), the reference divides its rounding — not compared, counted (`compare`);
  repeat_s        the same request again: the record of the cache by the key, nothing computed (`fresh: true`).
What is checked (a failure stops the run): the points came from the composites of the snapshot (ci: under 100 000
rows — `dh_rows`, as the program reads them); `counts` add up to the rows and the holes without a collar are
`no_position`, the composites without a grade `missing` (never a zero); 3 axes × 20 lags and 20 lags along the
holes, a lag without pairs is null in every curve; the record of the cache is there (`.json`, `.sums.parquet`).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

from . import _samples

FIELD = "AU"
COMPOSITES = "Композиты 2 м"
ANGLES = {"ang1": 30.0, "ang2": -10.0, "ang3": 0.0}
LAG, NLAG, DH_LAG, DH_NLAG = 15.0, 20, 2.0, 20
SEED = 12345
THREADS = (1, 2, 4, 8)
BRUTE = 2000  # points of the check against every pair
REL = 1e-12
STAGES = {"read": "read", "traj": "trajectories", "pairs": "pairs", "dh": "downhole", "write": "write"}
CLOCKS = {k: (k,) for k in ("plan", "sample", "kernel", "dh_kernel", "record")}


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


# --- the data -------------------------------------------------------------------------------------------------


def composite(meta: dict[str, Any], engine: Any, folder: Path) -> dict[str, Any]:
    """`more` of `_samples.build`: the composites of 2 m of AU by the job «Композиты» itself, their snapshot."""
    from bf_core.composites import jobs as cj

    body = {"set_id": meta["set_id"], "table": str(meta["samples"]), "method": "length", "length": 2.0,
            "fields": [FIELD], "name": COMPOSITES}  # fmt: skip
    out = cj.run(engine, folder, {"body": body, "rows": meta["rows"]}, job_id=1, tick=_noop)
    _samples.snapshot(engine, folder, meta["set_id"], [int(out["table_id"])])
    return {"composites": int(out["table_id"]), "composite_rows": int(out["rows"])}


def build(work: Path, params: dict[str, Any]) -> dict[str, Any]:
    """The project of a scenario of the variography (`prepare`): «Пробы» of AU, DENSITY and ROCK, their composites."""
    from bf_core.paths import configure_numba_cache

    configure_numba_cache()  # the kernels of `prepare` go where those of the child process are
    return _samples.build(work, {"litho_per_hole": 5, **params, "fields": 3}, more=composite)


def warm(job: Any) -> None:
    """`_samples.warm` with the composites: the kernels and the modules loaded outside the timing."""
    _samples.warm(job, fields=3, more=composite)


def body(meta: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    return {"set_id": meta["set_id"], "table": str(meta["composites"]), "field": FIELD,
            "transform": params.get("transform", "none"), "axes": dict(params.get("axes", ANGLES)), "lag": LAG,
            "nlag": NLAG, "downhole": True, "dh_lag": DH_LAG, "dh_nlag": DH_NLAG, "seed": SEED}  # fmt: skip


def clean(folder: Path) -> None:
    """No record of the variography in the cache of the project: a repeated run (`--repeat`) computes again."""
    shutil.rmtree(folder / "cache" / "variography", ignore_errors=True)


# --- the job --------------------------------------------------------------------------------------------------


def job(
    meta: dict[str, Any], engine: Any, folder: Path, params: dict[str, Any], threads: int | None = None
) -> tuple[dict[str, Any], dict[str, float], _samples.Ticks, list[Any]]:
    """`jobs.run_experimental` with clocks inside its stages: (its result, the clocks, the ticks, the calls of
    `jobs.compute` — the spec and the points it was given, the variograms it made)."""
    from bf_core.geostat.variogram import downhole, pairs, points, store
    from bf_core.geostat.variogram import jobs as vj

    ticks = _samples.Ticks()
    with _samples.captured(vj, "compute") as calls, _samples.stages(
        (pairs, "prepare", "plan"), (points, "sample", "sample"), (pairs, "run", "kernel"),
        (downhole, "calculate", "dh_kernel"), (store, "write_result", "record"),
    ) as took:  # fmt: skip
        t0 = time.perf_counter()
        out = vj.run_experimental(engine, folder, {"body": body(meta, params), "rows": meta["composite_rows"]},
                                  tick=ticks, threads=threads)  # fmt: skip
        took["done"] = time.perf_counter() - t0
    return out, took, ticks, calls


def digest(e: Any, d: Any) -> str:
    """SHA-256 of the sums of the pairs of the axes and along the holes."""
    h = hashlib.sha256()
    for s in (e.sums, d.sums):
        h.update(s.n.tobytes() + s.n_rel.tobytes() + s.sums.tobytes())
    return h.hexdigest()


def threads_hashes(call: Any) -> tuple[set[str], dict[str, float]]:
    """The variograms of the job and of `jobs.compute` on its points with 1, 2, 4, 8 threads: their hashes and the
    seconds of every computation."""
    from bf_core.geostat.variogram import jobs as vj

    (spec, p), kw, made = call
    found = {digest(*made)}
    took: dict[str, float] = {}
    for n in THREADS:
        t0 = time.perf_counter()
        again = vj.compute(spec, p, threads=n, limit=kw.get("limit", vj.sp.PAIRS_MAX))
        took[f"kernel_t{n}_s"] = round(time.perf_counter() - t0, 3)
        found.add(digest(*again))
        del again
    return found, took


def _error(got: Any, want: Any, what: str) -> float:
    """The largest |got − want| / max(1, |want|); NaN only where NaN."""
    import numpy as np

    a, b = np.asarray(got, dtype=np.float64), np.asarray(want, dtype=np.float64)
    assert a.shape == b.shape and np.array_equal(np.isnan(a), np.isnan(b)), what
    ok = ~np.isnan(b)
    return float((np.abs(a[ok] - b[ok]) / np.maximum(1.0, np.abs(b[ok]))).max(initial=0.0))


def compare(sums: Any, k: int, w: dict[str, Any], what: str) -> tuple[float, int]:
    """The row `k` of the sums of a kernel (`experimental.PairSums`) against every pair `w` of
    `tests/ref_bf_variogram`: N exactly; h̄, γ, ρ, the pairwise relative, the means of the tails and the heads →
    the largest relative difference, and the lags where the kernel has no ρ for a zero σ (the tails or the heads
    of the lag all equal — the variance within 16 ε of the mean square, `experimental._FLAT`) while the reference
    divides its rounding (`np.std` > 0): those ρ are not compared, the lags are counted."""
    import numpy as np

    assert np.array_equal(sums.n[k], w["n"]) and np.array_equal(sums.n_rel[k], w["n_rel"]), what
    err = max(_error(getattr(sums, name)[k], w[name], f"{what} {name}") for name in ("h", "gamma", "pairwise",
                                                                                    "tail", "head"))  # fmt: skip
    vt, vh = sums._moments()[3:5]
    rho, want = sums.rho[k], np.asarray(w["rho"], dtype=np.float64)
    flat = np.isnan(rho) & ~np.isnan(want)
    assert ((vt[k] == 0.0) | (vh[k] == 0.0))[flat].all(), what
    return max(err, _error(rho[~flat], want[~flat], f"{what} rho")), int(flat.sum())


def brute(call: Any, size: int) -> dict[str, Any]:
    """The kernels against every pair on NumPy: the axes over a sample of `size` points of the job, along the
    holes over the points of the first holes (≈ `size`)."""
    import numpy as np

    from bf_core.geostat.variogram import downhole, experimental, points
    from bf_core.geostat.variogram import jobs as vj
    from tests import ref_bf_variogram as ref

    (spec, p), _kw, _made = call
    q, _ = points.sample(p, min(size, p.n), SEED)
    dirs = vj.directions_of(spec)
    lag, nlag, tol = spec.lags.lag, spec.lags.nlag, spec.lags.tol
    e = experimental.calculate(q, dirs, lag, nlag, tol)
    rays = [(d.az, d.plunge, d.az_tol, d.dip_tol, d.band_h, d.band_v) for d in dirs]
    got = [compare(e.sums, k, w, f"axis {k + 1}") for k, w in enumerate(ref.brute_force(*q.xyz.T, q.values, rays,
                                                                                          lag, nlag, tol))]  # fmt: skip
    first = int(np.searchsorted(np.cumsum(np.bincount(p.hole)), size))  # holes of ≈ `size` points
    on = p.hole <= first
    dh = spec.dh
    d = downhole.downhole(p.hole[on], p.depth[on], p.values[on], dh.lag, dh.nlag, dh.tol)
    w = ref.brute_downhole(p.hole[on], p.depth[on], p.values[on], dh.lag, dh.nlag, dh.tol)
    dh_err, dh_flat = compare(d.sums, 0, w, "along the holes")
    return {"brute_error": max(g[0] for g in got), "brute_rho_flat": sum(g[1] for g in got),
            "brute_pairs": int(e.n.sum()), "dh_brute_error": dh_err, "dh_rho_flat": dh_flat,
            "dh_brute_points": int(on.sum()), "dh_brute_pairs": int(d.n.sum())}  # fmt: skip


def check_form(meta: dict[str, Any], out: dict[str, Any], folder: Path) -> dict[str, Any]:
    """The form of the job: the counts, the axes, the lags without pairs, the record of the cache."""
    from bf_core.geostat.variogram import jobs as vj
    from bf_core.geostat.variogram import store

    c = out["counts"]
    assert c["rows"] == meta["composite_rows"] == c["missing"] + c["no_position"] + c["outside_domain"] + c["used"]
    assert c["missing"] > 0 and c["no_position"] > 0 and c["outside_domain"] == 0, c  # holes without a collar
    assert len(out["directions"]) == 3 and [d["axis"] for d in out["directions"]] == [1, 2, 3]
    for row in [*out["directions"], out["downhole"]]:
        assert len(row["n"]) == len(row["gamma"]) == NLAG
        for n, g, h in zip(row["n"], row["gamma"], row["h"], strict=True):
            assert (n == 0) == (g is None) == (h is None), (n, g, h)  # no pairs — null, never 0
    assert store.result_path(folder, out["key"]).is_file() and vj.sums_path(folder, out["key"]).is_file()
    empty = sum(n == 0 for row in out["directions"] for n in row["n"])
    return {"points": c["used"], "missing": c["missing"], "no_position": c["no_position"], "empty_lags": empty,
            "form_kb": round(len(json.dumps(out)) / 1024, 1)}  # fmt: skip


# --- the scenario ---------------------------------------------------------------------------------------------


def prepare(params: dict[str, Any], work: Path) -> None:
    build(work, params)


def load() -> None:
    def once(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        calls = job(meta, engine, folder, {})[3]
        threads_hashes(calls[0])
        brute(calls[0], 300)

    warm(once)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder = _samples.opened(work)
    try:
        clean(folder)
        out, took, ticks, calls = job(meta, engine, folder, params)
        peak = _samples.peak_mb()
        t0 = time.perf_counter()
        again = job(meta, engine, folder, params)[0]
        repeat_s = time.perf_counter() - t0
    finally:
        engine.dispose()
    assert not out["fresh"] and again["fresh"] and again["key"] == out["key"], (out["fresh"], again["fresh"])
    checked = check_form(meta, out, folder)
    (spec, p), _kw, (e, _d) = calls[0]
    found, kernels = threads_hashes(calls[0])
    vs = brute(calls[0], int(params.get("brute", BRUTE)))
    assert vs["brute_error"] <= REL and vs["dh_brute_error"] <= REL, vs
    threads = _threads()
    kernel = took.get("kernel", 0.0)
    return {
        "axes_s": round(took["done"], 3),
        **{f"axes_{name}_s": out["stages"].get(stage, 0.0) for name, stage in STAGES.items()},
        **_samples.timings(took, CLOCKS, "axes_"),
        "axes_ns_check": round(kernel / max(e.checks, 1) * 1e9, 3),
        "axes_ns_check_thread": round(kernel * threads / max(e.checks, 1) * 1e9, 3),
        "threads": threads,
        "checks": int(e.checks),
        "estimate": int(e.estimate),
        "pairs": int(e.n.sum()),
        "sampled_n": int(out["sampled"]["n"]) if out["sampled"] else p.n,
        "axes_mb": peak,
        "axes_tick_gap_s": round(ticks.gap, 3),
        "hashes": len(found),
        "hash": sorted(found)[0][:16],
        **kernels,
        **vs,
        **checked,
        "repeat_s": round(repeat_s, 3),
        "composites": meta["composite_rows"],
        "rows": meta["rows"],
    }


def _threads() -> int:
    from bf_core.composites import strips

    return int(strips.default_threads())
