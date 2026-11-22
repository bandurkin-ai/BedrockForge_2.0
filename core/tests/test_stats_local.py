"""The local uncertainty of the tab «Статистика» (`local`; ТЗ 7.29; ADR 0022 §11; 16.10): the holes resampled with
replacement, IDW in plan on a grid, the maps of the mean, σ, P10, P90 and the probability to exceed a threshold.

The reference is BF as it is — `api/app/stats/estimation.py` (`idw` on `cKDTree`), `uncertainty.py`
(`resample_groups`, `local_*`) and the route `local-uncertainty` of `routers/stats.py` — copied below (`bf_*`);
the tests of BF `api/tests/test_stats_uncertainty.py` (resample_groups, local_*, idw) are ported. Where no two
data lie at one distance from a node the kernel equals BF within 1e−12. At equal distances BF takes whichever
the tree gives (its test: «k=1: one datum» in (1, 3)); here the data tied at the k-th distance share what is left
of k (`local.py`): the answer does not depend on the order of the rows, and a vertical hole (one place in plan)
counts as the mean of its samples.
"""

from __future__ import annotations

import json
import math
import warnings

import numpy as np
import pytest
from scipy.spatial import cKDTree

from bf_core.stats import local, tab
from bf_core.stats.weighted import StatsError

# ---- BF, as it is ----------------------------------------------------------------------------------------------


def bf_idw(coords, values, targets, k=8, power=2.0, radius=None):
    coords = np.asarray(coords, dtype=float)
    values = np.asarray(values, dtype=float)
    targets = np.asarray(targets, dtype=float)
    if coords.ndim != 2 or coords.shape[1] != 2 or targets.ndim != 2 or targets.shape[1] != 2:
        raise ValueError("coords and targets must have shape (n, 2)")
    if coords.shape[0] != values.shape[0] or coords.shape[0] == 0:
        raise ValueError("coords and values must be non-empty and of equal length")
    if k < 1 or power <= 0 or (radius is not None and radius <= 0):
        raise ValueError("require k >= 1, power > 0, radius > 0")
    k = min(k, coords.shape[0])
    dist, idx = cKDTree(coords).query(targets, k=k, distance_upper_bound=np.inf if radius is None else radius)
    dist = dist.reshape(len(targets), -1)
    idx = idx.reshape(len(targets), -1)
    ok = np.isfinite(dist)
    safe_idx = np.where(ok, idx, 0)
    zero = ok & (dist < 1e-9)
    w = np.where(ok, 1.0 / np.maximum(dist, 1e-9) ** power, 0.0)
    w = np.where(zero.any(axis=1, keepdims=True), zero.astype(float), w)
    total = w.sum(axis=1)
    est = (w * values[safe_idx]).sum(axis=1) / np.where(total > 0, total, 1.0)
    return np.where(total > 0, est, np.nan)


def bf_resample_groups(groups, n_boot, seed=None):
    groups = np.asarray(groups)
    _, inverse = np.unique(groups, return_inverse=True)
    members = [np.flatnonzero(inverse == g) for g in range(inverse.max() + 1)]
    rng = np.random.default_rng(seed)
    return [np.concatenate([members[g] for g in rng.integers(0, len(members), len(members))]) for _ in range(n_boot)]


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return fn(*args, **kwargs)


def bf_local(x, y, v, holes, nx=30, ny=30, nreal=50, k=8, power=2.0, radius=None, threshold=None, seed=None):
    """The body of BF `local_uncertainty` after `load_samples`."""
    x0, x1, y0, y1 = float(x.min()), float(x.max()), float(y.min()), float(y.max())
    span = max(x1 - x0, y1 - y0)
    radius = radius if radius is not None else 0.25 * span
    cx = x0 + (np.arange(nx) + 0.5) * max(x1 - x0, span / nx) / nx
    cy = y0 + (np.arange(ny) + 0.5) * max(y1 - y0, span / ny) / ny
    targets = np.column_stack([np.tile(cx, ny), np.repeat(cy, nx)])
    xy = np.column_stack([x, y])
    stack = np.empty((nreal, ny, nx))
    for r, idx in enumerate(bf_resample_groups(holes, nreal, seed)):
        stack[r] = bf_idw(xy[idx], v[idx], targets, k=k, power=power, radius=radius).reshape(ny, nx)
    valid = np.isfinite(stack).sum(axis=0)
    hit = (np.where(np.isfinite(stack), stack, -np.inf) >= (threshold or 0.0)).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        prob = np.where(valid > 0, hit / valid, np.nan)
    return {
        "radius": radius, "x": cx, "y": cy,
        "mean": _quiet(np.nanmean, stack, axis=0), "sd": _quiet(np.nanstd, stack, axis=0),
        "p10": _quiet(np.nanpercentile, stack, 10, axis=0), "p90": _quiet(np.nanpercentile, stack, 90, axis=0),
        "prob": None if threshold is None else prob,
    }  # fmt: skip


# ---- helpers ---------------------------------------------------------------------------------------------------


def close(a, b, tol=1e-12):
    a = np.array([math.nan if x is None else x for x in np.ravel(np.asarray(a, dtype=object))], dtype=float)
    b = np.asarray(b, dtype=float).ravel()
    assert a.shape == b.shape
    assert np.array_equal(np.isnan(a), np.isnan(b))
    ok = ~np.isnan(b)
    err = np.abs(a[ok] - b[ok]) / np.maximum(1.0, np.abs(b[ok]))
    assert err.size == 0 or float(err.max()) <= tol, float(err.max())


def inclined(holes=40, per=12, seed=8):
    """Inclined holes: every sample has its own place in plan (no ties of distance)."""
    r = np.random.default_rng(seed)
    cx, cy = r.uniform(0, 500, holes), r.uniform(0, 400, holes)
    az = r.uniform(0, 2 * np.pi, holes)
    depth = np.arange(per) * 3.0 + 1.5
    x = (cx[:, None] + 0.6 * depth[None] * np.sin(az)[:, None]).ravel()
    y = (cy[:, None] + 0.6 * depth[None] * np.cos(az)[:, None]).ravel()
    v = np.exp(np.sin(x / 80) + 0.5 * np.cos(y / 60) + r.normal(0, 0.3, x.size))
    return x, y, v, np.repeat(np.arange(holes), per)


def points_of(x, y, v):
    return tab.tab_points(np.column_stack([x, y, np.zeros_like(x)]), v)


R = np.random.default_rng(21).lognormal(0, 0.5, (30, 6, 7))


# ---- BF tests: resample_groups ---------------------------------------------------------------------------------
def test_resample_groups_keeps_holes_whole_and_equals_bf():
    groups = np.array(["a", "a", "b", "b", "b", "c"])
    draws = local.resample_groups(groups, 200, seed=4)
    assert len(draws) == 200
    for idx in draws:
        counts = {g: int((groups[idx] == g).sum()) for g in "abc"}
        assert counts["a"] % 2 == 0 and counts["b"] % 3 == 0  # whole holes only
    assert {len(d) for d in draws} > {6}  # sizes vary with the draw
    assert all(np.array_equal(x, y) for x, y in zip(draws, local.resample_groups(groups, 200, seed=4), strict=True))
    assert all(np.array_equal(x, y) for x, y in zip(draws, bf_resample_groups(groups, 200, seed=4), strict=True))
    with pytest.raises(ValueError):
        local.resample_groups([], 5)
    with pytest.raises(ValueError):
        local.resample_groups(groups, 0)


def test_group_counts_are_the_draws_of_bf():
    groups = np.array([7, 7, 3, 3, 3, 9, 1, 1])
    inverse, counts = local.group_counts(groups, 50, seed=11)
    for r, idx in enumerate(bf_resample_groups(groups, 50, seed=11)):
        assert np.array_equal(np.bincount(inverse[idx], minlength=counts.shape[1]) // np.bincount(inverse),
                              counts[r])  # fmt: skip
    assert counts.sum(axis=1).tolist() == [4] * 50  # as many holes as there are


# ---- BF tests: local statistics ---------------------------------------------------------------------------------
def test_local_statistics_match_numpy_per_cell():
    assert np.allclose(local.local_expectation(R), R.mean(axis=0))
    assert np.allclose(local.local_std(R), R.std(axis=0))
    assert np.allclose(local.local_percentile(R, 90), np.percentile(R, 90, axis=0))
    assert np.allclose(local.local_prob_exceed(R, 1.2), (R >= 1.2).mean(axis=0))
    assert local.local_expectation(R).shape == (6, 7)


def test_local_statistics_skip_nan_and_flag_empty_cells():
    r = R.copy()
    r[:10, 0, 0] = np.nan  # 20 valid left
    r[:, 1, 1] = np.nan  # none valid
    assert local.local_expectation(r)[0, 0] == pytest.approx(R[10:, 0, 0].mean())
    assert local.local_prob_exceed(r, 1.0)[0, 0] == pytest.approx((R[10:, 0, 0] >= 1.0).mean())
    for f in (local.local_expectation, local.local_std, lambda a: local.local_percentile(a, 50),
              lambda a: local.local_prob_exceed(a, 1.0)):  # fmt: skip
        assert np.isnan(f(r)[1, 1])  # empty, never 0


def test_local_validation_and_1d_input():
    with pytest.raises(ValueError):
        local.local_percentile(R, 101)
    with pytest.raises(ValueError):
        local.local_expectation(np.empty((0, 3)))
    assert local.local_expectation(np.array([1.0, 3.0])) == pytest.approx(2.0)  # a single location


# ---- BF tests: idw -----------------------------------------------------------------------------------------------
def test_idw_exact_at_data_symmetric_and_masked_by_radius():
    pts = np.array([[0.0, 0.0], [10.0, 0.0]])
    v = np.array([1.0, 3.0])
    est = local.idw(pts, v, np.array([[0.0, 0.0], [5.0, 0.0], [10.0, 0.0], [5.0, 40.0]]), k=2, radius=20)
    assert est[0] == pytest.approx(1.0) and est[2] == pytest.approx(3.0)  # honours the data
    assert est[1] == pytest.approx(2.0)  # midpoint
    assert np.isnan(est[3])  # nothing within 20
    near = local.idw(pts, v, np.array([[2.0, 0.0]]), k=2)
    assert 1.0 < near[0] < 2.0  # closer to the first datum
    edge = local.idw(np.array([[3.0, 4.0]]), np.array([2.0]), np.array([[0.0, 0.0]]), radius=5.0)
    assert np.isnan(edge[0])  # at the radius exactly: outside, as `cKDTree.query(distance_upper_bound)`


def test_idw_coincident_data_and_validation():
    pts = np.array([[0.0, 0.0], [0.0, 0.0], [5.0, 0.0]])
    assert local.idw(pts, np.array([1.0, 3.0, 9.0]), np.array([[0.0, 0.0]]), k=3)[0] == pytest.approx(2.0)
    # BF: k=1 takes one of the two coincident data, whichever the tree gives (1 or 3); here they share the one
    # place of k: the mean of the two
    assert local.idw(pts, np.array([1.0, 3.0, 9.0]), np.array([[1.0, 0.0]]), k=1)[0] == pytest.approx(2.0)
    for bad in ({"k": 0}, {"power": 0}, {"radius": -1}):
        with pytest.raises(ValueError):
            local.idw(pts, np.ones(3), np.zeros((1, 2)), **bad)
    with pytest.raises(ValueError):
        local.idw(np.zeros((3, 3)), np.ones(3), np.zeros((1, 2)))
    with pytest.raises(ValueError):
        local.idw(np.zeros((0, 2)), np.ones(0), np.zeros((1, 2)))


@pytest.mark.parametrize(("k", "power", "radius"), [(8, 2.0, None), (1, 1.0, 60.0), (5, 3.5, 25.0), (64, 2.0, 500.0)])
def test_idw_equals_bf_without_ties(k, power, radius):
    x, y, v, _ = inclined()
    r = np.random.default_rng(3)
    targets = np.column_stack([r.uniform(-50, 550, 700), r.uniform(-50, 450, 700)])
    targets[:5] = np.column_stack([x[:5], y[:5]])  # on a datum
    got = local.idw(np.column_stack([x, y]), v, targets, k=k, power=power, radius=radius)
    close(got, bf_idw(np.column_stack([x, y]), v, targets, k=k, power=power, radius=radius))
    assert np.array_equal(got[:5], v[:5])


def test_idw_of_resampled_holes_equals_bf_on_the_concatenation():
    """A hole drawn twice is the same data twice: the ties are of equal values, so BF is defined there."""
    x, y, v, holes = inclined(holes=25)
    xy = np.column_stack([x, y])
    r = np.random.default_rng(5)
    targets = np.column_stack([r.uniform(0, 500, 400), r.uniform(0, 400, 400)])
    inverse, counts = local.group_counts(holes, 6, seed=9)
    for real, idx in enumerate(bf_resample_groups(holes, 6, seed=9)):
        want = bf_idw(xy[idx], v[idx], targets, k=7, radius=150.0)
        got = local.idw(xy, v, targets, k=7, radius=150.0, multiplicity=counts[real][inverse])
        close(got, want)


# ---- the tab: BF `local-uncertainty` ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kw", "threshold"),
    [({}, None), ({"grid": {"nx": 12, "ny": 9}, "k": 4, "power": 1.5, "radius": 80.0}, 2.0),
     ({"grid": {"nx": 2, "ny": 2}, "realizations": 5, "k": 1}, 1.0)],
)  # fmt: skip
def test_tab_local_equals_bf(kw, threshold):
    x, y, v, holes = inclined()
    p = points_of(x, y, v)
    out = json.loads(json.dumps(local.tab_local(p, holes, seed=17, threshold=threshold, **kw), allow_nan=False))
    g = kw.get("grid", {})
    want = bf_local(x, y, v, holes, nx=g.get("nx", 30), ny=g.get("ny", 30), nreal=kw.get("realizations", 50),
                    k=kw.get("k", 8), power=kw.get("power", 2.0), radius=kw.get("radius"), threshold=threshold,
                    seed=17)  # fmt: skip
    assert out["grid"]["x"] == want["x"].tolist() and out["grid"]["y"] == want["y"].tolist()
    assert out["radius"] == want["radius"] and out["seed"] == 17 and out["holes"] == 40 and out["count"] == x.size
    for name in ("mean", "sd", "p10", "p90"):
        assert len(out[name]) == g.get("ny", 30) and len(out[name][0]) == g.get("nx", 30)  # [row by y][column by x]
        close(out[name], want[name])
    if threshold is None:
        assert out["prob"] is None and out["threshold"] is None
    else:
        close(out["prob"], want["prob"])


def test_tab_local_empty_cells_are_null_and_counted():
    x, y, v, holes = inclined()
    out = local.tab_local(points_of(x, y, v), holes, grid={"nx": 40, "ny": 40}, radius=12.0, seed=2, threshold=1.0)
    empty = sum(m is None for row in out["mean"] for m in row)
    assert 0 < empty == out["empty"] < 1600
    for name in ("sd", "p10", "p90", "prob"):
        assert sum(m is None for row in out[name] for m in row) == empty  # no realization reached the node
    assert all(m is None or m > 0 for row in out["mean"] for m in row)  # nothing empty became 0


def test_tab_local_seed_threads_and_tick():
    x, y, v, holes = inclined()
    p = points_of(x, y, v)
    a = local.tab_local(p, holes, seed=5, realizations=20, threads=1)
    assert local.tab_local(p, holes, seed=5, realizations=20, threads=4) == a  # bit for bit
    assert local.tab_local(p, holes, seed=6, realizations=20)["mean"] != a["mean"]
    drawn = local.tab_local(p, holes, realizations=5, grid={"nx": 3, "ny": 3})
    assert isinstance(drawn["seed"], int) and drawn["seed"] >= 0  # a seed is drawn and told: «повторить»
    assert local.tab_local(p, holes, realizations=5, grid={"nx": 3, "ny": 3}, seed=drawn["seed"]) == drawn
    seen: list[float] = []
    local.tab_local(p, holes, seed=5, realizations=20, tick=seen.append)
    assert seen and seen == sorted(seen) and seen[-1] == pytest.approx(1.0)

    def cancel(f: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        local.tab_local(p, holes, seed=5, realizations=20, tick=cancel)


def test_vertical_holes_do_not_depend_on_the_order_of_the_rows():
    """One place in plan for all the samples of a hole: BF takes k of them as the tree gives; here they share."""
    r = np.random.default_rng(12)
    cx, cy = np.repeat(r.uniform(0, 300, 30), 15), np.repeat(r.uniform(0, 300, 30), 15)
    v = r.lognormal(0.0, 0.7, cx.size)
    holes = np.repeat(np.arange(30), 15)
    a = local.tab_local(points_of(cx, cy, v), holes, seed=3, realizations=10, grid={"nx": 15, "ny": 15}, k=8)
    perm = r.permutation(cx.size)
    b = local.tab_local(points_of(cx[perm], cy[perm], v[perm]), holes[perm], seed=3, realizations=10,
                        grid={"nx": 15, "ny": 15}, k=8)  # fmt: skip
    for name in ("mean", "sd", "p10", "p90"):
        close(b[name], np.array(a[name], dtype=float))
    # one hole only near a node: the mean of the hole, whatever k
    xy = np.column_stack([cx, cy])
    node = np.array([[cx[0] + 0.5, cy[0]]])
    for k in (1, 3, 15):
        assert local.idw(xy, v, node, k=k, radius=0.9)[0] == pytest.approx(v[:15].mean(), rel=1e-12)
    # the hole that is the k-th: its samples share what is left of k
    two = np.array([[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [10.0, 0.0], [10.0, 0.0]])
    vals = np.array([1.0, 2.0, 3.0, 10.0, 20.0])
    est = local.idw(two, vals, np.array([[4.0, 0.0]]), k=4, power=1.0)[0]
    # three data at 4 m (1/4 each), one place of k left for the two data at 6 m: half of it each, (1/2) / 6
    assert est == pytest.approx(((1 + 2 + 3) / 4 + (10 + 20) / 12) / (3 / 4 + 2 / 12), rel=1e-12)


def test_missing_values_and_groups_of_the_rows_of_the_table():
    x, y, v, holes = inclined()
    vv = v.copy()
    vv[[0, 5, 70]] = np.nan
    p = points_of(x, y, vv)
    keep = np.isfinite(vv)
    by_rows = local.tab_local(p, holes, seed=1, realizations=8, grid={"nx": 6, "ny": 6})  # a label per row
    by_points = local.tab_local(p, holes[keep], seed=1, realizations=8, grid={"nx": 6, "ny": 6})  # per point
    assert by_rows == by_points
    want = bf_local(x[keep], y[keep], v[keep], holes[keep], nx=6, ny=6, nreal=8, seed=1)
    close(by_rows["mean"], want["mean"])
    assert by_rows["counts"]["missing"] == 3 and by_rows["count"] == x.size - 3
    with pytest.raises(ValueError):
        local.tab_local(p, holes[:10])


def test_the_limit_too_few_holes_and_one_place():
    rng = np.random.default_rng(1)
    pos, v = rng.uniform(0.0, 5000.0, (20_001, 3)), rng.lognormal(0.0, 1.0, 20_001)
    big = tab.TabPoints(pos[:, 0], pos[:, 1], v, np.arange(20_001), {"rows": 20_001, "missing": 0, "no_position": 0,
                                                                      "used": 20_001})  # fmt: skip
    with pytest.raises(StatsError) as e:
        local.tab_local(big, np.arange(20_001))
    assert e.value.code == "too_many_samples" and e.value.param == "table" and "Скомпозитируйте" in e.value.hint
    x, y, v, holes = inclined(holes=2)
    with pytest.raises(StatsError) as e:
        local.tab_local(points_of(x, y, v), holes)
    assert e.value.code == "too_few_values" and "скважин" in e.value.message
    with pytest.raises(StatsError) as e:
        local.tab_local(points_of(x[:2], y[:2], v[:2]), [0, 1])
    assert e.value.code == "too_few_values"
    with pytest.raises(StatsError) as e:
        local.tab_local(points_of(np.ones(9), np.ones(9), np.arange(9.0)), np.arange(9) // 3)
    assert e.value.code == "no_spread"


@pytest.mark.parametrize(
    ("kw", "param"),
    [
        ({"grid": {"nx": 1}}, "grid.nx"),
        ({"grid": {"ny": 101}}, "grid.ny"),
        ({"grid": [3, 3]}, "grid"),
        ({"k": 0}, "k"),
        ({"k": 65}, "k"),
        ({"power": 0}, "power"),
        ({"power": 10.5}, "power"),
        ({"radius": 0}, "radius"),
        ({"realizations": 4}, "realizations"),
        ({"realizations": 301}, "realizations"),
        ({"seed": -1}, "seed"),
        ({"seed": 1.5}, "seed"),
        ({"threshold": "много"}, "threshold"),
    ],
)
def test_bad_parameters(kw, param):
    x, y, v, holes = inclined(holes=5, per=3)
    with pytest.raises(StatsError) as e:
        local.tab_local(points_of(x, y, v), holes, **kw)
    assert e.value.code == "bad_param" and e.value.param == param and e.value.hint


def test_text_numbers_and_the_defaults_of_bf():
    x, y, v, holes = inclined(holes=6, per=4)
    p = points_of(x, y, v)
    out = local.tab_local(p, holes, seed=1)
    assert (out["grid"]["nx"], out["grid"]["ny"], out["realizations"], out["k"], out["power"]) == (30, 30, 50, 8, 2.0)
    span = max(np.ptp(x), np.ptp(y))
    assert out["radius"] == 0.25 * span and out["threshold"] is None
    text = local.tab_local(p, holes, seed=1, k="8", power="2,0", radius=str(0.25 * span), realizations=50.0)
    assert text == out


def test_warm_compiles_the_kernel():
    assert local.warm() >= 0.0


# ---- hard data: ties everywhere, far coordinates, a line, nodes outside -----------------------------------------
def brute_idw(xy, v, mult, targets, k, power=2.0, radius=math.inf):
    """The rule of `local.py` on every datum by NumPy (apart from the kernel: no grid, no rings): the k nearest
    copies, the ties at the k-th distance (1e−9 × max(1, d)) share what is left of k; a datum closer than 1e−9 —
    the mean of such data."""
    out = np.full(len(targets), np.nan)
    for t, (tx, ty) in enumerate(targets):
        d = np.sqrt((xy[:, 0] - tx) ** 2 + (xy[:, 1] - ty) ** 2)
        use = (mult > 0) & (d < radius)
        if not use.any():
            continue
        d, m, vals = d[use], mult[use].astype(float), v[use]
        if m.sum() <= k:
            share = m
        else:
            order = np.argsort(d, kind="stable")
            dk = d[order][np.searchsorted(np.cumsum(m[order]), k)]
            tol = 1e-9 * max(1.0, dk)
            full, tie = d < dk - tol, np.abs(d - dk) <= tol
            share = np.where(full, m, np.where(tie, m * (k - m[full].sum()) / m[tie].sum(), 0.0))
        zero = (share > 0) & (d < 1e-9)
        if zero.any():
            out[t] = (share * vals)[zero].sum() / share[zero].sum()
        else:
            w = share / np.maximum(d, 1e-9) ** power
            out[t] = (w * vals).sum() / w.sum()
    return out


def lattice(step=10.0, side=12, per=6, seed=4, offset=(0.0, 0.0)):
    """Vertical holes on a square lattice: `per` samples in one place a hole, every node of a cell is at one
    distance from its four holes."""
    r = np.random.default_rng(seed)
    gx, gy = np.meshgrid(np.arange(side) * step, np.arange(side) * step)
    x = np.repeat(gx.ravel(), per) + offset[0]
    y = np.repeat(gy.ravel(), per) + offset[1]
    return x, y, r.lognormal(0.0, 0.8, x.size), np.repeat(np.arange(side * side), per)


@pytest.mark.parametrize(("k", "power", "radius"), [(1, 2.0, math.inf), (4, 2.0, 25.0), (7, 1.0, 40.0),
                                                    (23, 3.0, 15.0), (64, 2.0, math.inf)])  # fmt: skip
def test_ties_of_a_lattice_equal_every_datum_by_numpy(k, power, radius):
    x, y, v, holes = lattice()
    xy = np.column_stack([x, y])
    r = np.random.default_rng(6)
    targets = np.vstack([
        np.column_stack([r.integers(-1, 13, 150) * 10.0 + 5.0, r.integers(-1, 13, 150) * 10.0 + 5.0]),  # centres
        np.column_stack([r.integers(0, 12, 50) * 10.0, r.integers(0, 12, 50) * 10.0]),  # on a hole
        np.column_stack([r.integers(0, 12, 50) * 10.0 + 5.0, r.integers(0, 12, 50) * 10.0]),  # between two
        np.column_stack([r.uniform(-300, 400, 50), r.uniform(-300, 400, 50)]),  # anywhere, far outside too
    ])  # fmt: skip
    inverse, counts = local.group_counts(holes, 4, seed=k)
    for real in range(4):
        mult = counts[real][inverse]
        rad = None if math.isinf(radius) else radius
        close(local.idw(xy, v, targets, k=k, power=power, radius=rad, multiplicity=mult),
              brute_idw(xy, v, mult, targets, k, power, radius))  # fmt: skip


def test_far_coordinates_a_line_of_holes_and_nodes_far_away():
    x, y, v, holes = lattice()
    near = local.tab_local(points_of(x, y, v), holes, seed=8, realizations=12, grid={"nx": 17, "ny": 13})
    far_x, far_y, _, _ = lattice(offset=(512_345.678, 7_012_345.25))  # UTM: the coordinates as they are
    far = local.tab_local(points_of(far_x, far_y, v), holes, seed=8, realizations=12, grid={"nx": 17, "ny": 13})
    for name in ("mean", "sd", "p10", "p90"):
        close(far[name], np.array(near[name], dtype=float), 1e-9)
    assert far["grid"]["x"][0] == pytest.approx(512_345.678 + near["grid"]["x"][0], abs=1e-6)
    # holes along one line in plan: the grid of the search is one cell high, the map a strip (BF)
    r = np.random.default_rng(9)
    lx = np.repeat(np.arange(25) * 8.0, 4) + np.tile(np.arange(4) * 0.5, 25) + r.uniform(0.0, 0.2, 100)  # no ties
    ly = np.full(lx.size, 300.0)
    lv, lh = r.lognormal(0, 0.5, lx.size), np.repeat(np.arange(25), 4)
    out = local.tab_local(points_of(lx, ly, lv), lh, seed=3, realizations=6, grid={"nx": 9, "ny": 5}, k=5)
    want = bf_local(lx, ly, lv, lh, nx=9, ny=5, nreal=6, k=5, seed=3)
    assert out["grid"]["y"] == want["y"].tolist()
    close(out["mean"], want["mean"])
    # a node far from every datum with no radius: the rings go over the whole grid
    xy = np.column_stack([lx, ly])
    targets = np.array([[-5000.0, -5000.0], [1e5, 300.0], [96.0, 1e4]])
    close(local.idw(xy, lv, targets, k=3), bf_idw(xy, lv, targets, k=3))


def test_percentiles_of_both_q_are_those_of_bf():
    r = R.copy()
    r[:10, 0, 0] = np.nan
    r[:, 1, 1] = np.nan
    both = local.percentiles(r, (10.0, 90.0))
    for i, q in enumerate((10.0, 90.0)):
        assert np.array_equal(both[i], local.local_percentile(r, q), equal_nan=True)  # bit for bit
    assert np.array_equal(local.percentiles(R, (50.0,))[0], np.percentile(R, 50, axis=0))
