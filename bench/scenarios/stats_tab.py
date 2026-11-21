"""The analyses of the tab «Статистика» at its limit (ТЗ 7.29, Т7.29-2: «больше 20 000 интервалов не считаются»;
ADR 0019 «Замеры»: 20 000 точек — декластеризация 25 размеров × 8 сдвигов, nscore, бутстрап 1000 — ≤ 3 с;
the same volume in ci and full: it is the limit of the tab).

A set of `_samples` of 202 holes of 100 samples (`fields` 3: AU, DENSITY, ROCK): about 19 900 intervals with a
value and a place — 1 % of the samples have no value, one hole has no collar; every fifth hole stands in a tight
cluster of infill drilling where the gold is three times the usual. The table has no snapshot (under 100 000
rows it is read from `dh_rows`, as in the program). The routes `POST /stats/tab/…` as functions (`_charts.ask`):
  declus_s     declustering by cells in plan, 25 steps of the size × 8 shifts, with the weights of the points —
               the first request of the tab: `declus_read_s` (the column), `declus_traj_s` (the trajectories),
               `declus_points_s` (the middles of the intervals), `declus_kernel_s` (`tab.tab_declus`);
  nscore_s     the normal scores with those weights (the weights are counted again: nothing is stored);
  backtr_s     41 scores back to values;
  bootstrap_s  1000 realizations drawn by those weights, seed 1;
  tab_s        the four together (a budget: ≤ 3 с).
What is checked (a failure stops the run), against the columns by numpy: the points are the intervals with a
value and a place, not more than 20 000; the weights sum to their number and give the declustered mean (1e-9),
which is below the plain one (the clusters are rich); the curve has a size for every step; the table of the
transform ascends and the back-transform of its own scores returns its values (1e-9); the intervals of the
bootstrap hold what they are drawn around.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _charts, _samples

STAGES = {"read": ("read",), "traj": ("traj",), "points": ("points",), "kernel": ("kernel",)}
FIELD = "AU"
DECLUS = {"method": "cell", "ncell": 25, "noff": 8}
BACK = 41  # scores of the back-transform


def spots() -> _charts.Spots:
    from bf_core.composites import coords
    from bf_core.composites import source as lines
    from bf_core.stats import source, tab

    return ((source, "_read_lines", "read"), (lines, "trajectories", "traj"), (coords, "midpoints", "points"),
            (tab, "tab_declus", "kernel"), (tab, "tab_nscore", "kernel"), (tab, "tab_backtr", "kernel"),
            (tab, "tab_bootstrap", "kernel"))  # fmt: skip


def base(meta: dict[str, Any]) -> dict[str, Any]:
    return {"set_id": meta["set_id"], "table": str(meta["samples"]), "field": FIELD, "declus": dict(DECLUS)}


def analyses(project: Any, meta: dict[str, Any], info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The four requests of the tab in the order of its buttons, timed into `info`."""
    from bf_core.stats import routes_tab

    body = base(meta)
    got = {"declus": _charts.measure(project, routes_tab.declus_of, {**body, "weights": True}, "declus", info,
                                     spots(), STAGES, again=False)}  # fmt: skip
    got["nscore"] = _charts.measure(project, routes_tab.nscore_of, body, "nscore", info, spots(), STAGES, cold=False,
                                    again=False)  # fmt: skip
    table = got["nscore"]["scores"]
    scores = [table[round(k * (len(table) - 1) / (BACK - 1))] for k in range(BACK)]
    got["backtr"] = _charts.measure(project, routes_tab.backtr_of, {**body, "scores": scores}, "backtr", info,
                                    cold=False, again=False)  # fmt: skip
    got["backtr"]["asked"] = scores
    got["bootstrap"] = _charts.measure(project, routes_tab.bootstrap_of, {**body, "n_boot": 1000, "seed": 1},
                                       "bootstrap", info, spots(), STAGES, cold=False, again=False)  # fmt: skip
    info["tab_s"] = round(sum(info[f"{key}_s"] for key in got), 3)
    return got


def check(got: dict[str, dict[str, Any]], cols: Any, points: Any) -> None:
    import numpy as np

    v = cols.number(FIELD)
    used = np.isfinite(v) & np.isfinite(points[:, 0]) & np.isfinite(points[:, 1])
    n = int(used.sum())
    d = got["declus"]
    assert d["count"] == n <= 20_000 and d["counts"]["used"] == n and d["counts"]["rows"] == cols.n, d["counts"]
    assert d["counts"]["missing"] == int((~np.isfinite(v)).sum()) > 0 and d["counts"]["no_position"] > 0, d["counts"]
    assert d["rows"] == cols.row_ids[used].tolist(), "the rows of the points"
    w = np.array(d["weights"])
    assert _samples.close(float(w.sum()), float(n), 1e-9) and (w >= 0.0).all(), float(w.sum())
    assert _samples.close(d["declustered_mean"], float((w * v[used]).sum() / w.sum()), 1e-9), d["declustered_mean"]
    assert _samples.close(d["naive_mean"], float(v[used].mean()), 1e-12) and not d["uniform"], d["naive_mean"]
    assert d["declustered_mean"] < d["naive_mean"] and d["best_cell_size"] > 0.0, (d["declustered_mean"], d)
    assert d["ncell"] == 25 and d["noff"] == 8 and len(d["cell_sizes"]) == len(d["means"]) == 27, len(d["means"])
    ns = got["nscore"]
    values, scores = ns["values"], ns["scores"]
    assert ns["count"] == n == len(ns["sample_scores"]) and ns["weighted"] and ns["rows"] == d["rows"], ns["count"]
    assert ns["table_rows"] == len(values) == len(scores) <= n, ns["table_rows"]
    assert all(a <= b for a, b in zip(values, values[1:], strict=False))
    assert all(a < b for a, b in zip(scores, scores[1:], strict=False)) and scores[0] < 0.0 < scores[-1]
    back = got["backtr"]
    at = {s: k for k, s in enumerate(scores)}
    for score, value in zip(back["asked"], back["values"], strict=True):
        assert _samples.close(value, values[at[score]], 1e-9), (score, value, values[at[score]])
    b = got["bootstrap"]
    assert b["count"] == n and b["n_boot"] == 1000 and b["seed"] == 1 and b["weighted"], (b["count"], b["n_boot"])
    mean, median = b["statistics"]["mean"], b["statistics"]["median"]
    assert mean["ci_low"] < mean["observed_weighted"] < mean["ci_high"] and mean["defined"] == 1000, mean
    assert _samples.close(mean["observed_weighted"], d["declustered_mean"], 1e-9), mean["observed_weighted"]
    assert _samples.close(mean["observed"], d["naive_mean"], 1e-9) and median["ci_low"] <= median["ci_high"], median
    assert sum(mean["counts"]) == 1000 and len(b["statistics"]) == 6, list(b["statistics"])


def prepare(params: dict[str, Any], work: Path) -> None:
    _samples.build(work, params)


def load() -> None:
    def every(meta: dict[str, Any], engine: Any, folder: Path) -> None:
        analyses(_charts.project_of(engine, folder), meta, {})

    _samples.warm(every, fields=3)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
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
        check(got, cols, source.midpoints(project, cols))
    finally:
        engine.dispose()
    d = got["declus"]
    info.update(points=d["count"], best_cell_m=round(d["best_cell_size"], 1),
                mean_ratio=round(d["declustered_mean"] / d["naive_mean"], 4))  # fmt: skip
    info["peak_mb"] = _samples.peak_mb()
    return info
