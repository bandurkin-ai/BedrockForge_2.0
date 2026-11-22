"""The fan of 12 directions with its rose and the variogram map 41 × 41 — the whole job `variography.map` (ADR 0022
«Замеры», решения 11, 24; ТЗ 8.6.4: «200 тыс. композитов, h_max = 300 м: веер 12 направлений и карта 41×41 — ≤ 20
с»; ci — 50 тыс., ориентир ≤ 4 с).

The data — those of `variogram_axes` (the composites of 2 m of AU, the snapshot ready). The request: the plane
`plane` (by default horizontal: a1 north, a2 west), L = 15 m × 20 lags, the fan of 12 rays (tolerance 180°/12/2,
dip 22,5°, band_v 2L), the rose at q = 1, the map of ±20 cells of L (41 × 41) in the layer of 2L. The job is the
function its handler calls — `jobs_map.run_map` — with clocks around the functions of its stages:
  map_s           the job from its start to the record written — what the target of ТЗ counts;
  map_read_s, map_traj_s, map_fan_s, map_map_s, map_write_s   the stages the job reports itself
                  (`job.result.stages`: read, trajectories, fan, map, write);
  map_fan_plan_s, map_grid_plan_s   `fan.prepare`, `varmap.prepare`: the grids and the estimate of the pair
                  checks over all the points (and the plans of the sample, when there is one);  map_sample_s —
                  `points.sample`;
  map_fan_kernel_s, map_grid_kernel_s   `fan.run`, `varmap.run` — the kernels;  fan_ns_check, map_ns_check —
                  their nanoseconds a pair check (wall time at `threads` threads);
  fan_checks, map_checks, estimate   pair checks of the runs and of all the points;  sampled_n — the points of
                  the sample (all of them — no sample);
  map_mb          the peak of the process at the end of the job (VmHWM);  map_tick_gap_s — the longest wait
                  between two ticks.
After the job (not in `map_s`):
  asymmetric      cells of the map of the job that differ from their symmetric ones: N and Σ(z_i − z_j)² of
                  the cell (i, j) and of (−i, −j), bit for bit (`limits`: 0);  centre_n — N of the centre (the
                  self-pairs of every point and the pairs closer than ½ cell in the plane);  map_pairs — the pairs
                  of the map without the self-pairs;  map_empty — cells under `min_pairs`;
  map_brute_error  the largest relative difference (to max(1, |x|)) of γ between `varmap.varmap` over a sample of
                  `brute` (2000) points of the job and every ordered pair on NumPy (written apart from the kernel,
                  as `tests/test_vario_fan_map.brute_map`): `limits` 1e−12, N exactly;  fan_brute_error — the
                  same for the rays of `fan.fan` against `tests/ref_bf_variogram.brute_force` (the horizontal
                  plane: the tolerances of a ray in the plane are those of its direction in the world; ρ as
                  `variogram_axes.compare`: fan_rho_flat — the lags of σ = 0, not compared);
  rose_rays, rose_beyond   the rays of the rose and those without a range («> h_max»);
  repeat_s        the same request again: the record of the cache by the key (`fresh: true`).
What is checked (a failure stops the run): 12 rays of 20 lags, a lag without pairs and a cell under `min_pairs`
are null (never 0); the map has 41 × 41 cells; the record of the cache is there.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import _samples
from . import variogram_axes as axes

PLANE = {"ang1": 0.0, "ang2": 0.0, "ang3": 0.0}
FAN_N, NU = 12, 20
STAGES = {"read": "read", "traj": "trajectories", "fan": "fan", "map": "map", "write": "write"}
CLOCKS = {k: (k,) for k in ("fan_plan", "grid_plan", "sample", "fan_kernel", "grid_kernel", "record")}
REL = 1e-12


def body(meta: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    return {"set_id": meta["set_id"], "table": str(meta["composites"]), "field": axes.FIELD,
            "transform": params.get("transform", "none"), "plane": dict(params.get("plane", PLANE)),
            "what": ["fan", "map"], "lag": axes.LAG, "nlag": axes.NLAG, "seed": axes.SEED,
            "fan": {"n": FAN_N, "q": 1.0}, "map": {"nu": NU, "nv": NU}}  # fmt: skip


def job(
    meta: dict[str, Any], engine: Any, folder: Path, params: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, float], _samples.Ticks, list[Any]]:
    """`jobs_map.run_map` with clocks inside its stages: (its result, the clocks, the ticks, the calls of
    `varmap.calculate` — the points and the spec it was given, what it made)."""
    from bf_core.geostat.variogram import fan, points, store, varmap
    from bf_core.geostat.variogram import jobs_map as vm

    ticks = _samples.Ticks()
    with _samples.captured(varmap, "calculate") as calls, _samples.stages(
        (fan, "prepare", "fan_plan"), (varmap, "prepare", "grid_plan"), (points, "sample", "sample"),
        (fan, "run", "fan_kernel"), (varmap, "run", "grid_kernel"), (store, "write_result", "record"),
    ) as took:  # fmt: skip
        t0 = time.perf_counter()
        out = vm.run_map(engine, folder, {"body": body(meta, params), "rows": meta["composite_rows"]}, tick=ticks)
        took["done"] = time.perf_counter() - t0
    return out, took, ticks, calls


def symmetry(r: Any) -> dict[str, Any]:
    """The map of the job against itself turned by 180°: N and the sums of the cells, bit for bit; the pairs of
    the map without the self-pairs of the centre."""
    import numpy as np

    m = r.map
    asym = int((m.n != m.n[::-1, ::-1]).sum() + (m.s2 != m.s2[::-1, ::-1]).sum())
    return {"asymmetric": asym, "centre_n": int(m.n[m.nv, m.nu]), "map_pairs": int((m.n.sum() - m.n_points) // 2),
            "map_empty": int(np.isnan(m.gamma).sum())}  # fmt: skip


def _brute_map(x: Any, y: Any, z: Any, v: Any, m: Any) -> tuple[Any, Any]:
    """Every ordered pair (i, j), the self-pairs too, on NumPy: the cell rint((p_j − p_i)·u / cell),
    rint((p_j − p_i)·v / cell) within the layer |Δ·n| ≤ thickness."""
    import numpy as np

    p = np.stack([x, y, z], axis=1)
    pu, pv, pn = (p @ np.asarray(m.frame[k], dtype=np.float64) for k in range(3))
    iu = np.rint((pu[None, :] - pu[:, None]) / m.cell)
    iv = np.rint((pv[None, :] - pv[:, None]) / m.cell)
    ok = (np.abs(iu) <= m.nu) & (np.abs(iv) <= m.nv) & (np.abs(pn[None, :] - pn[:, None]) <= m.thickness)
    k = ((iv + m.nv) * (2 * m.nu + 1) + iu + m.nu)[ok].astype(np.int64)
    d2 = ((v[None, :] - v[:, None]) ** 2)[ok]
    size = (2 * m.nv + 1) * (2 * m.nu + 1)
    n = np.bincount(k, minlength=size).reshape(2 * m.nv + 1, 2 * m.nu + 1)
    s = np.bincount(k, weights=d2, minlength=size).reshape(n.shape)
    gamma = np.full(n.shape, np.nan)
    np.divide(s, 2 * n, out=gamma, where=(n >= m.min_pairs) & (n > 0))
    return n, gamma


def brute(call: Any, size: int) -> dict[str, Any]:
    """The kernels of the map and of the fan over a sample of `size` points of the job against every pair."""
    import numpy as np

    from bf_core.geostat.variogram import fan, points, varmap
    from tests import ref_bf_variogram as ref

    (p, spec), _kw, r = call
    q, _ = points.sample(p, min(size, p.n), axes.SEED)
    x, y, z = (np.ascontiguousarray(q.xyz[:, k]) for k in range(3))
    v = np.ascontiguousarray(q.values)
    m = varmap.varmap(x, y, z, v, spec.cell, frame=r.map.frame, nu=spec.nu, nv=spec.nv, thickness=spec.thickness,
                      min_pairs=spec.map_min_pairs)  # fmt: skip
    n, gamma = _brute_map(x, y, z, v, m)
    assert np.array_equal(m.n, n), "map N"
    out: dict[str, Any] = {"map_brute_error": axes._error(m.gamma, gamma, "map gamma"), "fan_brute_error": None}
    if all(a == 0.0 for a in r.plane):  # the horizontal plane: the rays are directions of the world
        f = fan.fan(x, y, z, v, spec.lags.lag, spec.lags.nlag, spec.lags.tol, plane=r.plane, n=spec.fan_n,
                    tol=spec.fan_tol, dip_tol=spec.dip_tol, band_v=spec.band_v)  # fmt: skip
        rays = [(d.az, d.plunge, d.az_tol, d.dip_tol, d.band_h, d.band_v) for d in f.directions]
        want = ref.brute_force(x, y, z, v, rays, spec.lags.lag, spec.lags.nlag, spec.lags.tol)
        got = [axes.compare(f.e.sums, k, w, f"ray {k}") for k, w in enumerate(want)]
        out.update(fan_brute_error=max(g[0] for g in got), fan_rho_flat=sum(g[1] for g in got),
                   fan_brute_pairs=int(f.e.n.sum()))  # fmt: skip
    return out


def check_form(out: dict[str, Any], folder: Path) -> dict[str, Any]:
    from bf_core.geostat.variogram import store

    rays = out["fan"]["directions"]
    assert len(rays) == FAN_N and all(len(d["n"]) == axes.NLAG for d in rays)
    for d in rays:
        for n, g in zip(d["n"], d["gamma"], strict=True):
            assert (n == 0) == (g is None), (n, g)  # no pairs — null, never 0
    grid = out["map"]
    side = 2 * NU + 1
    assert len(grid["gamma"]) == side and all(len(row) == side for row in grid["gamma"])
    for nrow, grow in zip(grid["n"], grid["gamma"], strict=True):
        for n, g in zip(nrow, grow, strict=True):
            assert (n < grid["min_pairs"]) == (g is None), (n, g)
    assert store.result_path(folder, out["key"]).is_file()
    beyond = sum(1 for ray in out["rose"]["rays"] if ray["beyond"])
    return {"rose_rays": len(out["rose"]["rays"]), "rose_beyond": beyond}


def prepare(params: dict[str, Any], work: Path) -> None:
    axes.build(work, params)


def load() -> None:
    def once(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        calls = job(meta, engine, folder, {})[3]
        brute(calls[0], 300)
        job(meta, engine, folder, {"plane": {"ang1": 30.0, "ang2": -20.0, "ang3": 10.0}})

    axes.warm(once)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    meta, engine, folder = _samples.opened(work)
    try:
        axes.clean(folder)
        out, took, ticks, calls = job(meta, engine, folder, params)
        peak = _samples.peak_mb()
        t0 = time.perf_counter()
        again = job(meta, engine, folder, params)[0]
        repeat_s = time.perf_counter() - t0
    finally:
        engine.dispose()
    assert not out["fresh"] and again["fresh"] and again["key"] == out["key"]
    (p, _spec), _kw, r = calls[0]
    sym = symmetry(r)
    assert sym["asymmetric"] == 0 and sym["centre_n"] >= r.map.n_points == (out["sampled"] or {"n": p.n})["n"], sym
    checked = check_form(out, folder)
    vs = brute(calls[0], int(params.get("brute", axes.BRUTE)))
    assert vs["map_brute_error"] <= REL and (vs["fan_brute_error"] or 0.0) <= REL, vs
    threads = axes._threads()
    fan_checks, map_checks = int(r.fan.e.checks), int(r.map.checks)

    def ns(key: str, checks: int) -> float:
        return round(took.get(key, 0.0) / max(checks, 1) * 1e9, 3)

    return {
        "map_s": round(took["done"], 3),
        **{f"map_{name}_s": out["stages"].get(stage, 0.0) for name, stage in STAGES.items()},
        **_samples.timings(took, CLOCKS, "map_"),
        "fan_ns_check": ns("fan_kernel", fan_checks),
        "map_ns_check": ns("grid_kernel", map_checks),
        "threads": threads,
        "fan_checks": fan_checks,
        "map_checks": map_checks,
        "estimate": int(r.estimate),
        "sampled_n": int(out["sampled"]["n"]) if out["sampled"] else p.n,
        "points": p.n,
        "map_mb": peak,
        "map_tick_gap_s": round(ticks.gap, 3),
        **sym,
        **vs,
        **checked,
        "repeat_s": round(repeat_s, 3),
    }
