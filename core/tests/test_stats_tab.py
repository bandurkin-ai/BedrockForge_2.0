"""The tab «Статистика» (ТЗ 7.29; ADR 0019 §7; Э13, 13.12): the points of the tab (the middle of an interval in
plan), the limit of 20 000 (Т7.29-2), declustering by cells and by polygons on the kernels of Э12 — a regular
grid without clusters (Т7.29-1), the curve «размер → среднее» — normal scores and the bootstrap with the weights
of declustering: the tests of BF `test_stats_api.py` (declustering, nscore, backtr, bootstrap) on arrays.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

import numpy as np
import pytest

from bf_core.stats import tab
from bf_core.stats.declus3d import DeclusParams, declus_weights
from bf_core.stats.declustering import cell_declus, polygonal_declus
from bf_core.stats.transforms import nscore
from bf_core.stats.weighted import StatsError


def seeded() -> tuple[np.ndarray, np.ndarray]:
    """BF `seeded`: vertical holes — a regular 6 × 6 grid (100 m) plus a dense cluster of 30 holes of high
    grades; two samples a hole (one place in plan). Middles of the intervals (n×3) and AU."""
    rng = np.random.default_rng(5)
    holes = [(100.0 * i, 100.0 * j) for i in range(6) for j in range(6)]
    holes += [(150.0 + rng.uniform(-8, 8), 150.0 + rng.uniform(-8, 8)) for _ in range(30)]
    pos, au = [], []
    for x, y in holes:
        near = np.hypot(x - 150, y - 150) < 20
        for i in range(2):
            pos.append((x, y, 100.0 - 5.0 - 10.0 * i))
            au.append(float(rng.lognormal(0.0, 0.3) * (4.0 if near else 1.0)))
    return np.array(pos), np.array(au)


def regular(nx: int = 20, ny: int = 15, step: float = 50.0, per_hole: int = 1, seed: int = 3):
    """A regular grid of vertical holes without clusters, at UTM-like coordinates."""
    gx, gy = np.meshgrid(512_000.0 + np.arange(nx) * step, 7_101_000.0 + np.arange(ny) * step, indexing="ij")
    x, y = np.repeat(gx.ravel(), per_hole), np.repeat(gy.ravel(), per_hole)
    z = np.tile(200.0 - 2.0 * np.arange(per_hole), nx * ny)
    return np.column_stack([x, y, z]), np.random.default_rng(seed).lognormal(0.0, 1.0, x.size)


# --- the points, the limit ------------------------------------------------------------------------------------


def test_points_are_the_middles_with_a_value_and_a_place() -> None:
    """An interval takes part with a value and a place in plan; the rest is counted — never a zero."""
    pos = np.array([[10.0, 20.0, 5.0], [np.nan, np.nan, np.nan], [30.0, 40.0, np.nan], [50.0, 60.0, 1.0],
                    [70.0, np.nan, 2.0], [90.0, 95.0, 3.0]])  # fmt: skip
    v = np.array([1.5, 2.5, np.nan, 0.0, 4.5, np.inf])
    p = tab.tab_points(pos, v)
    assert p.n == 2 and p.rows.tolist() == [0, 3] and p.rows.dtype == np.int64
    assert p.x.tolist() == [10.0, 50.0] and p.y.tolist() == [20.0, 60.0] and p.v.tolist() == [1.5, 0.0]
    assert p.counts == {"rows": 6, "missing": 2, "no_position": 2, "used": 2}
    flat = tab.tab_points(pos[:, :2], v)  # X, Y alone
    assert flat.rows.tolist() == [0, 3] and flat.counts == p.counts
    for bad in (pos[:-1], pos.reshape(-1), np.zeros((6, 4))):
        with pytest.raises(ValueError):
            tab.tab_points(bad, v)
    with pytest.raises(StatsError) as e:
        tab.tab_points(pos, np.full(6, np.nan))
    assert e.value.code == "no_values" and "без значения — 6" in e.value.hint
    with pytest.raises(StatsError) as e:
        tab.tab_points(np.full((6, 3), np.nan), v)
    assert e.value.code == "no_values" and "без положения" in e.value.hint and "траектори" in e.value.hint
    with pytest.raises(StatsError) as e:
        tab.tab_points(pos, v, least=3)
    assert e.value.code == "too_few_values" and "3" in e.value.message and e.value.hint


def test_t7_29_2_more_than_20000_intervals_are_refused_with_the_advice_to_composite() -> None:
    """Т7.29-2: 20 001 интервал — отказ с советом композитировать; 20 000 — считается. The limit counts the
    intervals that take part: rows without a value or a place are not held against the table."""
    assert tab.MAX_SAMPLES == 20_000
    rng = np.random.default_rng(1)
    pos, v = rng.uniform(0.0, 5000.0, (20_001, 3)), rng.lognormal(0.0, 1.0, 20_001)
    with pytest.raises(StatsError) as e:
        tab.tab_points(pos, v)
    assert e.value.code == "too_many_samples" and e.value.param == "table"
    assert "20 001" in e.value.message and "20 000" in e.value.message
    assert "композит" in e.value.hint.lower() and "Обработка проб" in e.value.hint
    assert set(e.value.as_dict()) == {"code", "message", "hint"}
    exact = tab.tab_points(pos[:20_000], v[:20_000])
    assert exact.n == 20_000
    holey = v.copy()
    holey[7] = np.nan  # 20 001 rows, 20 000 of them with a value
    assert tab.tab_points(pos, holey).counts == {"rows": 20_001, "missing": 1, "no_position": 0, "used": 20_000}
    # Every analysis of the tab at the limit, in time (bench `stats_tab`: ≤ 3 s).
    t0 = time.perf_counter()
    d = tab.tab_declus(exact, {"ncell": 24, "noff": 8}, threads=2)
    n = tab.tab_nscore(exact, declus={"method": "cell"})
    b = tab.tab_bootstrap(exact, n_boot=1000, seed=1)
    seconds = time.perf_counter() - t0
    assert d["count"] == n["count"] == b["count"] == 20_000 and len(d["cell_sizes"]) == 26
    assert seconds < 6.0, f"{seconds:.2f} s"  # ~1.5 s; the first call compiles the kernel of the cells


# --- declustering ---------------------------------------------------------------------------------------------


def test_t7_29_1_regular_grid_without_clusters() -> None:
    """Т7.29-1: на регулярной сетке без кластеров декластеризованное среднее = обычному. Exactly so where the
    method has no edge effect: cells not larger than the step of the grid (every hole alone in its cell,
    whatever the shifts), cells of whole steps laid on the grid, polygons bounded half a step around the holes."""
    for per_hole in (1, 3):
        pos, v = regular(per_hole=per_hole)
        pts = tab.tab_points(pos, v)
        naive = float(v.mean())
        r = tab.tab_declus(pts, {"cmin": 5.0, "cmax": 45.0, "ncell": 16, "noff": 8}, weights=True)
        assert r["uniform"] is True and r["best_cell_size"] is None and r["count"] == 300 * per_hole
        assert r["weight_min"] == r["weight_max"] == 1.0 and r["weights"] == [1.0] * pts.n
        assert r["naive_mean"] == naive and r["declustered_mean"] == pytest.approx(naive, rel=1e-12)
        assert r["means"] == pytest.approx([naive] * 18, rel=1e-12) and "веса равны 1" in r["notes"][0]
        # 100 m cells laid on a 50 m grid of 20 × 16 holes (one origin): 2 × 2 holes in every cell.
        even, ve = regular(nx=20, ny=16, per_hole=per_hole)
        r = tab.tab_declus(tab.tab_points(even, ve), {"cmin": 100.0, "cmax": 100.0, "ncell": 1, "noff": 1})
        assert r["declustered_mean"] == pytest.approx(float(ve.mean()), rel=1e-12)
        assert r["weight_min"] == pytest.approx(1.0, rel=1e-12) and r["weight_max"] == pytest.approx(1.0, rel=1e-12)
        # Polygons: the area of influence of every hole is one cell of the grid.
        x0, y0 = pos[:, 0].min(), pos[:, 1].min()
        bounds = [x0 - 25.0, x0 + 19 * 50.0 + 25.0, y0 - 25.0, y0 + 14 * 50.0 + 25.0]
        r = tab.tab_declus(pts, {"method": "polygon", "bounds": bounds, "nx": 200, "ny": 150})
        assert r["declustered_mean"] == pytest.approx(naive, rel=1e-12) and r["cell_sizes"] is None
        assert r["weight_min"] == pytest.approx(1.0, rel=1e-12) and r["weight_max"] == pytest.approx(1.0, rel=1e-12)
        assert r["bounds"] == bounds and (r["nx"], r["ny"]) == (200, 150)
    # A grade that the cells cannot lower — the default sweep of sizes (2 … 50 % of the extent) leaves the
    # weights uniform: a constant; a plane rising to the north-east (the grid of GSLIB starts below the
    # south-west hole, so its border cells only raise the mean, and «min» takes none of them).
    pos, v = regular()
    for grade in (
        np.full(300, 2.5),
        1.0 + 0.001 * (pos[:, 0] - pos[:, 0].min()) + 0.002 * (pos[:, 1] - pos[:, 1].min()),
    ):
        r = tab.tab_declus(tab.tab_points(pos, grade))
        assert r["uniform"] is True and r["declustered_mean"] == pytest.approx(r["naive_mean"], rel=1e-12)
        assert r["cmin"] == pytest.approx(0.02 * 950.0) and r["cmax"] == pytest.approx(0.5 * 950.0)
    # With random grades the sweep of GSLIB over cells larger than the step picks an edge effect of the grid
    # (the holes of its border weigh more): the mean moves by a few per cent — the method of BF and GSLIB as it
    # is; the answer says which size did it, the curve shows no minimum to trust.
    r = tab.tab_declus(tab.tab_points(pos, v))
    assert r["uniform"] is False and r["best_cell_size"] > 50.0
    assert abs(r["declustered_mean"] / r["naive_mean"] - 1.0) < 0.06


def test_declustering_cell_reduces_mean_and_reports_curve() -> None:
    """BF `test_declustering_cell_reduces_mean_and_reports_curve` — and the kernel of Э12 behind it."""
    pos, au = seeded()
    pts = tab.tab_points(pos, au)
    b = tab.tab_declus(pts, {"cmin": 10, "cmax": 300, "ncell": 15, "noff": 4}, weights=True)
    assert b["count"] == 132 and b["method"] == "cell"
    assert b["declustered_mean"] < b["naive_mean"]  # clustered highs are down-weighted
    assert len(b["cell_sizes"]) == len(b["means"]) == 17 and b["cell_sizes"][0] == 0.0
    assert b["means"][0] == b["naive_mean"] == float(au.mean())
    assert b["best_cell_size"] is not None and 10 <= b["best_cell_size"] <= 300
    assert sum(b["weights"]) == pytest.approx(132) and b["rows"] == list(range(132))
    assert b["weight_min"] < 1 < b["weight_max"] and b["uniform"] is False and b["zero_weights"] == 0
    assert (b["cmin"], b["cmax"], b["ncell"], b["noff"], b["minmax"]) == (10.0, 300.0, 15, 4, "min")
    assert b["counts"] == {"rows": 132, "missing": 0, "no_position": 0, "used": 132}
    # The weights, the sizes and the means are those of BF `cell_declus` and of `composite.declus` of Э12.
    w, sizes, means = cell_declus(pos[:, 0], pos[:, 1], au, 10, 300, ncell=15, noff=4)
    assert b["weights"] == pytest.approx(w, rel=1e-9) and b["cell_sizes"] == pytest.approx(sizes)
    assert b["means"] == pytest.approx(means, rel=1e-9)
    assert b["declustered_mean"] == pytest.approx(float(np.average(au, weights=w)), rel=1e-9)
    assert b["best_cell_size"] == sizes[1:][int(np.argmin(means[1:]))]
    e12 = declus_weights(
        pos[:, 0], pos[:, 1], None, au, DeclusParams.of({"cmin": 10, "cmax": 300, "ncell": 15, "noff": 4})
    )
    assert b["weights"] == e12.weights.tolist() and b["declustered_mean"] == e12.report["mean"]
    high = tab.tab_declus(pts, {"cmin": 10, "cmax": 300, "ncell": 15, "noff": 4, "minmax": "max"})
    assert high["declustered_mean"] >= high["naive_mean"] and "weights" not in high and "rows" not in high
    assert json.loads(json.dumps(b)) == b  # JSON-ready


def test_declustering_polygonal_and_defaults() -> None:
    """BF `test_declustering_polygonal_and_defaults`: polygons in plan; the default cells — 2 % and 50 % of the
    larger extent, 20 steps, 8 shifts."""
    pos, au = seeded()
    pts = tab.tab_points(pos, au)
    b = tab.tab_declus(pts, {"method": "polygon", "nx": 80, "ny": 80}, weights=True)
    assert b["method"] == "polygon" and b["declustered_mean"] < b["naive_mean"]
    assert b["cell_sizes"] is None and b["means"] is None and b["best_cell_size"] is None
    assert (b["nx"], b["ny"]) == (80, 80) and b["bounds"] == [0.0, 500.0, 0.0, 500.0]
    w = polygonal_declus(pos[:, 0], pos[:, 1], nx=80, ny=80)
    assert b["weights"] == pytest.approx(w, rel=1e-12) and sum(b["weights"]) == pytest.approx(132)
    d = tab.tab_declus(pts)
    assert d["method"] == "cell" and (d["ncell"], d["noff"], d["minmax"]) == (20, 8, "min")
    assert d["cmin"] == pytest.approx(0.02 * 500, rel=0.05) and d["cmax"] == pytest.approx(0.5 * 500, rel=0.05)
    assert len(d["cell_sizes"]) == 22 and d["declustered_mean"] < d["naive_mean"]
    auto = tab.tab_declus(pts, {"method": "polygon"})  # the grid by the number of holes (Э12)
    assert auto["nx"] == auto["ny"] == 200 and auto["declustered_mean"] < auto["naive_mean"]
    # Polygons smaller than a node of a coarse grid: weights of 0, told in the notes.
    coarse = tab.tab_declus(pts, {"method": "polygon", "nx": 12, "ny": 12})
    assert coarse["zero_weights"] > 0 and coarse["weight_min"] == 0.0 and "нулевым весом" in coarse["notes"][0]


def test_declustering_refusals() -> None:
    pos, au = seeded()
    pts = tab.tab_points(pos, au)
    cases: list[tuple[dict[str, Any], str, str | None]] = [
        ({"cmin": 50, "cmax": 10}, "bad_param", "cmin"),  # BF: 422
        ({"method": "cell3d"}, "bad_param", "method"),  # the tab is in plan
        ({"method": "kriging"}, "bad_param", "method"),
        ({"cmin": -1}, "bad_param", "cmin"),
        ({"ncell": 0}, "bad_param", "ncell"),
        ({"noff": 1000}, "bad_param", "noff"),
        ({"minmax": "avg"}, "bad_param", "minmax"),
        ({"method": "polygon", "nx": 0}, "bad_param", "nx"),
        ({"method": "polygon", "bounds": [0, 500, 0]}, "bad_param", "bounds"),
        ({"method": "polygon", "bounds": [0, 500, 300, 300]}, "bad_param", "bounds"),
        ({"method": "polygon", "bounds": [0, math.nan, 0, 500]}, "bad_param", "bounds"),
    ]
    for params, code, param in cases:
        with pytest.raises(StatsError) as e:
            tab.tab_declus(pts, params)
        assert (e.value.code, e.value.param) == (code, param), e.value.message
        assert e.value.message and e.value.hint
    # One hole: every interval at one place in plan — nothing to decluster; one interval — nothing at all.
    one_hole = tab.tab_points(np.column_stack([np.full(5, 3.0), np.full(5, 4.0), np.arange(5.0)]), np.arange(1.0, 6.0))
    for params in ({}, {"method": "polygon"}):
        with pytest.raises(StatsError) as e:
            tab.tab_declus(one_hole, params)
        assert e.value.code == "no_spread" and "в одной точке плана" in e.value.message and e.value.hint
    with pytest.raises(StatsError) as e:
        tab.tab_declus(tab.tab_points(pos[:1], au[:1]))
    assert e.value.code == "too_few_values"
    # A profile (the holes on one line): cells work, polygons need an extent along both axes.
    line = tab.tab_points(np.column_stack([np.arange(8.0) * 25, np.zeros(8), np.zeros(8)]), np.arange(1.0, 9.0))
    assert tab.tab_declus(line)["count"] == 8
    with pytest.raises(StatsError) as e:
        tab.tab_declus(line, {"method": "polygon"})
    assert e.value.code == "bad_param" and e.value.param == "bounds" and "ячейки" in e.value.hint


# --- normal scores ----------------------------------------------------------------------------------------------


def test_nscore_table_and_sample_scores() -> None:
    """BF `test_nscore_table_and_sample_scores`."""
    pos, au = seeded()
    pts = tab.tab_points(pos, au)
    b = tab.tab_nscore(pts)
    assert len(b["values"]) == len(b["scores"]) == len(b["sample_scores"]) == b["count"] == 132 == b["table"]
    assert b["values"] == sorted(b["values"]) and b["scores"] == sorted(b["scores"])
    assert abs(np.mean(b["sample_scores"])) < 0.01 and b["rows"] == list(range(132)) and b["weighted"] is False
    assert b["zmin"] == au.min() and b["zmax"] == au.max() and b["counts"]["used"] == 132
    weighted = tab.tab_nscore(pts, declus={"method": "polygon"})
    assert weighted["scores"] != b["scores"] and weighted["weighted"] is True  # weights change the table
    w = polygonal_declus(pos[:, 0], pos[:, 1], nx=200, ny=200)
    ns, tr = nscore(au, wts=w)
    assert weighted["sample_scores"] == pytest.approx(ns, abs=1e-12) and weighted["values"] == tr.values.tolist()
    # The clustered high grades weigh less: their scores are higher than without the weights.
    top = int(np.argmax(au))
    assert weighted["sample_scores"][top] > b["sample_scores"][top]
    # Weights of 0 (a coarse grid of polygons) leave their samples out of the table, not out of the scores.
    coarse = tab.tab_nscore(pts, declus={"method": "polygon", "nx": 12, "ny": 12})
    assert coarse["table"] < 132 == len(coarse["sample_scores"]) and all(
        math.isfinite(s) for s in coarse["sample_scores"]
    )
    wide = tab.tab_nscore(pts, zmin=0.0, zmax=au.max() * 3)
    assert wide["zmin"] == 0.0 and wide["zmax"] == au.max() * 3 and wide["scores"] == b["scores"]
    with pytest.raises(StatsError) as e:
        tab.tab_nscore(pts, zmin=au.min() + 1)
    assert e.value.code == "bad_param" and e.value.param == "zmin"
    one = tab.tab_nscore(tab.tab_points(pos[:1], au[:1]))  # a single interval has a table too (BF: min_n = 1)
    assert one["scores"] == [0.0] and json.loads(json.dumps(b)) == b


def test_backtr_round_trips_sample_scores() -> None:
    """BF `test_backtr_round_trips_sample_scores`."""
    pos, au = seeded()
    pts = tab.tab_points(pos, au)
    ns = tab.tab_nscore(pts)
    back = tab.tab_backtr(pts, ns["sample_scores"][:20])["values"]
    assert len(back) == 20 and all(au.min() <= v <= au.max() for v in back)
    assert back == pytest.approx(au[:20].tolist(), rel=1e-12)  # the samples themselves
    # a table score maps back to its value
    assert tab.tab_backtr(pts, ns["scores"][10:13])["values"] == pytest.approx(ns["values"][10:13])
    wns = tab.tab_nscore(pts, declus={"method": "polygon"})
    wback = tab.tab_backtr(pts, wns["sample_scores"], declus={"method": "polygon"})["values"]
    assert wback == pytest.approx(au.tolist(), rel=1e-12)


def test_backtr_tails_and_validation() -> None:
    """BF `test_backtr_tails_and_validation`."""
    pos, au = seeded()
    pts = tab.tab_points(pos, au)
    top = tab.tab_nscore(pts)["values"][-1]
    clamp = tab.tab_backtr(pts, [6.0], utail=None)
    lin = tab.tab_backtr(pts, [6.0], zmax=top * 3)
    hyp = tab.tab_backtr(pts, [6.0], utail="hyperbolic", utpar=1.5)
    assert clamp["values"] == [top] and lin["values"][0] > top and hyp["values"][0] > top
    assert tab.tab_backtr(pts, [-6.0], ltail="power", ltpar=2.0, zmin=0.0)["values"][0] < au.min()
    cases: list[tuple[Any, dict[str, Any], str, str | None]] = [
        ([0.0], {"ltail": "power"}, "bad_param", "ltpar"),  # power tail needs ltpar
        ([], {}, "bad_param", "scores"),
        ([0.0] * 20_001, {}, "bad_param", "scores"),
        ([0.0, math.nan], {}, "bad_input", None),
        ("много", {}, "bad_input", None),
        ([0.0], {"utail": "cubic"}, "bad_param", "utail"),
    ]
    for scores, kwargs, code, param in cases:
        with pytest.raises(StatsError) as e:
            tab.tab_backtr(pts, scores, **kwargs)
        assert (e.value.code, e.value.param) == (code, param), e.value.message
    # Far beyond the data the hyperbolic tail has no number to give: null, not ∞ and not 0.
    far = tab.tab_backtr(pts, [0.0, 39.0], utail="hyperbolic", utpar=1.0)["values"]
    assert far[1] is None and far[0] == pytest.approx(float(np.median(au)), rel=0.05)


# --- bootstrap ------------------------------------------------------------------------------------------------


def test_bootstrap_interval_contains_observed_and_is_reproducible() -> None:
    """BF `test_bootstrap_interval_contains_observed_and_is_reproducible`, `…declustering_weights_lower_the_mean`,
    `…validation`."""
    pos, au = seeded()
    pts = tab.tab_points(pos, au)
    b = tab.tab_bootstrap(pts, n_boot=400, confidence=0.9, seed=7)
    assert b["count"] == 132 and b["n_boot"] == 400 and b["confidence"] == 0.9 and b["counts"]["used"] == 132
    m = b["statistics"]["mean"]
    assert m["ci_low"] < m["observed"] < m["ci_high"] and m["observed"] == pytest.approx(float(au.mean()), rel=1e-12)
    assert m["std"] > 0 and abs(m["mean"] - m["observed"]) < 3 * m["std"]
    assert len(m["edges"]) == len(m["counts"]) + 1 and sum(m["counts"]) == 400
    assert tab.tab_bootstrap(pts, n_boot=400, confidence=0.9, seed=7) == b  # same seed, same answer
    wider = tab.tab_bootstrap(pts, n_boot=400, confidence=0.99, seed=7)["statistics"]["mean"]
    assert wider["ci_high"] - wider["ci_low"] > m["ci_high"] - m["ci_low"]
    for name in ("median", "std", "cv", "p10", "p90"):
        assert b["statistics"][name]["ci_low"] <= b["statistics"][name]["ci_high"]
    plain = tab.tab_bootstrap(pts, n_boot=600, seed=1)
    weighted = tab.tab_bootstrap(pts, n_boot=600, seed=1, declus={"method": "polygon"})
    pm, wm = plain["statistics"]["mean"], weighted["statistics"]["mean"]
    assert wm["mean"] < pm["mean"]  # clustered highs drawn less often
    assert wm["observed"] == pytest.approx(pm["observed"])  # the statistic itself stays unweighted
    assert weighted["weighted"] is True and plain["weighted"] is False and plain["confidence"] == 0.95
    w = polygonal_declus(pos[:, 0], pos[:, 1], nx=200, ny=200)
    assert wm["observed_weighted"] == pytest.approx(float(np.average(au, weights=w)), rel=1e-12)
    for kwargs, param in (
        ({"n_boot": 5}, "n_boot"),
        ({"n_boot": 99999}, "n_boot"),
        ({"confidence": 1.2}, "confidence"),
    ):
        with pytest.raises(StatsError) as e:
            tab.tab_bootstrap(pts, **kwargs)  # type: ignore[arg-type]
        assert e.value.code == "bad_param" and e.value.param == param
    with pytest.raises(StatsError) as e:
        tab.tab_bootstrap(tab.tab_points(pos[:1], au[:1]))
    assert e.value.code == "too_few_values"
    assert json.loads(json.dumps(b)) == b
