"""The variograms and the local uncertainty of the tab «Статистика» at its limit (ТЗ 7.29, Т7.29-2: «больше 20 000
интервалов не считаются»; ADR 0022 «Замеры», решение 21: 20 000 точек — 4 направления, карта 21 × 21, `local`
30 × 30 × 50 — ≤ 3 с по анализам; the same volume in ci and full: it is the limit of the tab).

The set of `stats_tab` (`_samples`: 202 holes of 100 samples of 1 m, `fields` 3 — AU, DENSITY, ROCK): about
19 900 intervals with a value and a place — 1 % of the samples have no value, one hole has no collar, every fifth
hole stands in a tight cluster of infill drilling; under 100 000 rows the table is read from `dh_rows`, as in the
program. The routes `POST /stats/tab/…` of 16.17 as functions (`_charts.ask`: `routes_tab_vario.*_of` in a HEAVY
slot, 2 threads), the settings by default as the tab sends them (the lag — a round step of the extent / 20, 10
lags, 30 pairs):
  omni_s       the omnidirectional variogram — the first request of the tab: omni_read_s (the column),
               omni_traj_s (the trajectories), omni_points_s (the middles of the intervals), omni_kernel_s
               (`tab_vario.tab_variogram`);  omni_again_s — the same request from the columns of the process;
  four_s       four directions 0°, 45°, 90°, 135° (22,5°);  map_s — the map of ±10 cells (21 × 21);
  local_s      the local uncertainty: the grid 30 × 30, 50 realizations, k 8, seed 1, the threshold 1 г/т;
  *_again_s    the same request again (the answer the same bit for bit — checked);  *_kb — its JSON;
  tab_s        the four analyses together;  slowest_s — the slowest of them (`limits`: 3 s an analysis);
  ref_error    the largest relative difference (to max(1, |x|)) of h̄ and γ of the variograms and of γ of the map
               of the kernels of the tab with the settings of the answers over a sample of `ref` (2000) of their
               points from BF over every pair of the sample (`tests/ref_bf_variogram.experimental_variogram`,
               `variogram_map`; BF over all the points takes 100 s): `limits` 1e−12, N exactly;  ref_s — that
               check (not in the analyses).
What is checked (a failure stops the run), against the columns by numpy: the points are the intervals with a value
and a place, not more than 20 000; a lag or a cell without pairs is null (never 0); the map of the answer is
symmetric bit for bit; the grid of `local` is 30 × 30, P10 ≤ P90, the probability of exceeding is in [0; 1].
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import _charts, _samples

FIELD = "AU"
STAGES = {"read": ("read",), "traj": ("traj",), "points": ("points",), "kernel": ("kernel",)}
FOUR = (0.0, 45.0, 90.0, 135.0)
MAP_N = 10  # 21 × 21
LOCAL = {"grid": {"nx": 30, "ny": 30}, "realizations": 50, "seed": 1, "threshold": 1.0}
REL = 1e-12
REF = 2000  # points of the check against BF
SEED = 12345


def spots() -> _charts.Spots:
    from bf_core.composites import coords
    from bf_core.composites import source as lines
    from bf_core.stats import local, source, tab_vario

    return ((source, "_read_lines", "read"), (lines, "trajectories", "traj"), (coords, "midpoints", "points"),
            (tab_vario, "tab_variogram", "kernel"), (tab_vario, "tab_map", "kernel"),
            (local, "tab_local", "kernel"))  # fmt: skip


def base(meta: dict[str, Any]) -> dict[str, Any]:
    return {"set_id": meta["set_id"], "table": str(meta["samples"]), "field": FIELD}


def analyses(project: Any, meta: dict[str, Any], info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The four requests in the order of the panels of the tab, timed into `info`."""
    from bf_core.stats import routes_tab_vario as rv

    body = base(meta)
    got = {"omni": _charts.measure(project, rv.variogram_of, {**body, "directions": "omni"}, "omni", info, spots(),
                                   STAGES)}  # fmt: skip
    got["four"] = _charts.measure(project, rv.variogram_of, {**body, "directions": "four"}, "four", info, spots(),
                                  STAGES, cold=False)  # fmt: skip
    got["map"] = _charts.measure(project, rv.map_of, {**body, "n": MAP_N}, "map", info, spots(), STAGES, cold=False)
    got["local"] = _charts.measure(project, rv.local_of, {**body, **LOCAL}, "local", info, spots(), STAGES,
                                   cold=False)  # fmt: skip
    info["tab_s"] = round(sum(info[f"{key}_s"] for key in got), 3)
    info["slowest_s"] = max(info[f"{key}_s"] for key in got)
    return got


def _error(got: Any, want: Any, what: str) -> float:
    import numpy as np

    a = np.array([np.nan if x is None else x for x in np.ravel(np.array(got, dtype=object))], dtype=np.float64)
    b = np.ravel(np.asarray(want, dtype=np.float64))
    assert a.shape == b.shape and np.array_equal(np.isnan(a), np.isnan(b)), what  # no pairs — null, never 0
    ok = ~np.isnan(b)
    return float((np.abs(a[ok] - b[ok]) / np.maximum(1.0, np.abs(b[ok]))).max(initial=0.0))


def against_bf(got: dict[str, dict[str, Any]], xyz: Any, v: Any, size: int) -> dict[str, Any]:
    """The kernels of the tab with the settings of the answers (lag, tolerance, cell, min_pairs) over a sample of
    `size` of their points against BF over every pair of that sample (BF over all the 20 000 — 100 s)."""
    import numpy as np

    from bf_core.stats import tab, tab_vario
    from tests import ref_bf_variogram as ref

    t0 = time.perf_counter()
    rng = np.random.Generator(np.random.PCG64(SEED))
    at = np.sort(rng.choice(len(v), size=min(size, len(v)), replace=False))
    points = tab.tab_points(xyz[at], v[at])
    xy = np.column_stack([points.x, points.y])
    err = 0.0
    for key in ("omni", "four"):
        a = got[key]
        out = tab_vario.tab_variogram(points, lag=a["lag"], nlag=a["nlag"], lag_tol=a["lag_tol"], directions=key,
                                      min_pairs=a["min_pairs"])  # fmt: skip
        want = [(0.0, 90.0)] if key == "omni" else [(az, 22.5) for az in FOUR]
        assert [(d["az"], d["az_tol"]) for d in a["directions"]] == want == [(d["az"], d["az_tol"])
                                                                             for d in out["directions"]]  # fmt: skip
        for d, (az, tol) in zip(out["directions"], want, strict=True):
            h, g, n = ref.experimental_variogram(xy, points.v, a["lag"], a["nlag"], lag_tol=a["lag_tol"], azimuth=az,
                                                 azimuth_tol=tol)  # fmt: skip
            assert d["n"] == n.tolist(), (key, az)  # N exactly
            err = max(err, _error(d["h"], h, f"{key} h"), _error(d["gamma"], g, f"{key} gamma"))
    m = got["map"]
    mine = tab_vario.tab_map(points, cell=m["cell"], n=m["nu"], min_pairs=m["min_pairs"])
    gam, npp = ref.variogram_map(xy, points.v, m["nu"], m["nv"], m["cell"], m["cell"], minnp=m["min_pairs"])
    assert mine["n"] == npp.tolist(), "map N"
    err = max(err, _error(mine["gamma"], gam, "map gamma"))
    return {"ref_error": err, "ref_points": points.n, "ref_s": round(time.perf_counter() - t0, 3)}


def symmetric(m: dict[str, Any]) -> bool:
    """The map of the answer turned by 180°: the same N and γ, bit for bit."""
    n, g = m["n"], m["gamma"]
    return n == [row[::-1] for row in n[::-1]] and g == [row[::-1] for row in g[::-1]]


def check(got: dict[str, dict[str, Any]], cols: Any, positions: Any) -> dict[str, Any]:
    import numpy as np

    v = cols.number(FIELD)
    used = np.isfinite(v) & np.isfinite(positions[:, 0]) & np.isfinite(positions[:, 1])
    n = int(used.sum())
    for key in ("omni", "four", "map", "local"):
        c = got[key]["counts"]
        assert got[key]["count"] == c["used"] == n <= 20_000 and c["rows"] == cols.n, (key, c)
        assert c["missing"] == int((~np.isfinite(v)).sum()) > 0 and c["no_position"] > 0, (key, c)
    m = got["map"]
    assert m["nu"] == m["nv"] == MAP_N and len(m["gamma"]) == 2 * MAP_N + 1 == len(m["gamma"][0])
    loc = got["local"]
    assert loc["grid"]["nx"] == loc["grid"]["ny"] == 30 and loc["realizations"] == 50 and loc["seed"] == 1
    p10, p90, prob = (np.array(loc[k], dtype=np.float64) for k in ("p10", "p90", "prob"))
    assert p10.shape == p90.shape == prob.shape == (30, 30)
    have = np.isfinite(p10)
    assert np.array_equal(have, np.isfinite(p90)) and (p10[have] <= p90[have]).all()
    assert ((prob[have] >= 0.0) & (prob[have] <= 1.0)).all() and int((~have).sum()) == loc["empty"]
    return {"points": n, "local_empty": loc["empty"], "lag_m": got["omni"]["lag"],
            "map_cell_m": m["cell"], "local_radius_m": round(float(loc["radius"]), 1)}  # fmt: skip


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)


def load() -> None:
    def every(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        analyses(_charts.project_of(engine, folder), meta, {})

    _samples.warm(every, fields=3)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import numpy as np

    from bf_core.drillholes import trajectory
    from bf_core.stats import source

    meta, engine, folder = _samples.opened(work)
    info: dict[str, Any] = {"rows": meta["rows"]}
    try:
        project = _charts.project_of(engine, folder)
        with trajectory._lock:
            trajectory._cache.clear()  # those of the warm-up are of another project; none of this one yet
        got = analyses(project, meta, info)
        cols = _charts.columns(project, meta["set_id"], [FIELD], meta["samples"])
        positions = source.midpoints(project, cols)
    finally:
        engine.dispose()
    info.update(check(got, cols, positions))
    assert symmetric(got["map"]), "the map is not symmetric"
    v = cols.number(FIELD)
    used = np.isfinite(v) & np.isfinite(positions[:, 0]) & np.isfinite(positions[:, 1])
    info.update(against_bf(got, positions[used], v[used], int(params.get("ref", REF))))
    assert info["ref_error"] <= REL, info["ref_error"]
    info["peak_mb"] = _samples.peak_mb()
    return info
