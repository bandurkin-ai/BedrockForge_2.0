"""Swath plot by samples and the common accounting of a series (ТЗ 7.30, 7.35 Д-01; ADR 0019 §5; Э13, 13.10):
means, counts and weights by swath against a direct count (BF `test_means_match_a_direct_count`), sums that are
`pair_sums` of the rows of a swath bit for bit, samples in the cuboid and inside the blocks of a model
(Т7.30-2; BF `test_points_in_blocks…`), the cuboid by the data and by `limits` (BF `test_limits…`), the limit
of 12 series (Т7.30-1), an azimuth of 90° as X bit for bit (ТД01-5), a turned frame against points turned
beforehand (ТД01-6, 1e−9), an empty swath is empty, not 0.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import pytest

from bf_core.blocks import schema
from bf_core.stats import swath as sw
from bf_core.stats import swath_frame as sf
from bf_core.stats.select import SampleFilter, select_sample, select_values
from bf_core.stats.weighted import StatsError, pair_sums

ORIGIN = np.array([512_000.0, 7_101_000.0, 200.0])  # UTM-like


def bits(a: Any, b: Any) -> bool:
    x, y = np.asarray(a), np.asarray(b)
    return x.shape == y.shape and x.dtype == y.dtype and x.tobytes() == y.tobytes()


def holes(n_holes: int, per_hole: int, seed: int, spread: tuple[float, float, float] = (400.0, 300.0, 120.0)):
    """Samples of inclined holes: middles of the intervals, lengths, grades — with what real tables have:
    empty grades, «<0.01» written as −0.01, zeros, an outlier, empty and zero lengths, a hole without a survey."""
    rng = np.random.default_rng(seed)
    collar = ORIGIN + rng.uniform(0.0, 1.0, (n_holes, 3)) * [spread[0], spread[1], 10.0] + [0.0, 0.0, spread[2]]
    az, dip = rng.uniform(0, 360, n_holes), rng.uniform(-90, -45, n_holes)
    a, b = np.radians(az), np.radians(dip)
    d = np.column_stack([np.sin(a) * np.cos(b), np.cos(a) * np.cos(b), np.sin(b)])
    length = rng.choice([0.5, 1.0, 1.0, 1.5, 2.0], (n_holes, per_hole))
    depth = np.cumsum(length, axis=1) - 0.5 * length
    xyz = (collar[:, None, :] + depth[:, :, None] * d[:, None, :]).reshape(-1, 3)
    n = n_holes * per_hole
    length = length.reshape(-1).copy()
    grade = rng.lognormal(0.0, 1.0, n)
    grade[rng.integers(0, n, n // 40)] = np.nan
    grade[rng.integers(0, n, n // 60)] = -0.01
    grade[rng.integers(0, n, n // 80)] = 0.0
    grade[rng.integers(0, n)] = 950.0
    length[rng.integers(0, n, n // 90)] = np.nan
    length[rng.integers(0, n, n // 120)] = 0.0
    xyz[: per_hole // 2] = np.nan  # the first hole has no survey for half of its samples
    xyz[rng.integers(0, n, 7), rng.integers(0, 3, 7)] = np.nan
    return xyz, grade, length


def direct(t: np.ndarray, v: np.ndarray, w: np.ndarray, start: float, width: float, n: int):
    """Means by swath of the rows inside the cuboid, counted one swath at a time with plain sums."""
    idx = np.clip(np.floor((t - start) / width), 0, n - 1).astype(int)  # the far boundary is in the last swath
    mean, count, weight = np.full(n, np.nan), np.zeros(n, dtype=np.int64), np.zeros(n)
    for k in range(n):
        m = idx == k
        count[k], weight[k] = m.sum(), w[m].sum()
        if m.any():
            mean[k] = (v[m] * w[m]).sum() / w[m].sum()
    return mean, count, weight


# --- the common accounting ------------------------------------------------------------------------------------


def test_means_match_a_direct_count() -> None:
    """BF `test_means_match_a_direct_count`."""
    rng = np.random.default_rng(1)
    t = rng.uniform(-5, 105, 2000)
    v = rng.lognormal(0, 1, 2000)
    w = rng.uniform(0.5, 2, 2000)
    v[::50] = np.nan
    r = sw.swath_means(t, v, w, 0.0, 10.0, 10)
    for k in range(10):
        m = (t >= 10 * k) & (t < 10 * (k + 1)) & np.isfinite(v)
        assert r.mean[k] == pytest.approx((v[m] * w[m]).sum() / w[m].sum(), rel=1e-13)
        assert r.count[k] == m.sum() and r.weight[k] == pytest.approx(w[m].sum(), rel=1e-13)
    assert r.counts["outside_box"] == int(((t < 0) | (t > 100)).sum())
    inside = (t >= 0) & (t <= 100) & np.isfinite(v)
    assert r.total["mean"] == pytest.approx((v[inside] * w[inside]).sum() / w[inside].sum(), rel=1e-13)
    assert r.total["count"] == inside.sum() == r.counts["used"] and r.total["weight"] == pytest.approx(w[inside].sum())
    assert r.count.dtype == np.int64 and r.mean.dtype == r.weight.dtype == np.float64
    # The far boundary belongs to the last swath; an empty swath has no mean.
    edge = sw.swath_means(np.array([100.0, 5.0]), np.array([3.0, 1.0]), np.ones(2), 0.0, 10.0, 10)
    assert edge.mean[9] == 3.0 and np.isnan(edge.mean[4]) and edge.count.sum() == 2


def test_an_empty_swath_is_empty_not_zero() -> None:
    """A swath without samples has no mean (None in the answer), its count and weight are the true zeros; rows
    without a value or a weight never count as zeros."""
    t = np.array([5.0, 6.0, 25.0, 26.0, 27.0, 45.0])
    v = np.array([2.0, 4.0, np.nan, 0.0, 6.0, 9.0])
    w = np.array([1.0, 3.0, 5.0, 2.0, np.nan, 0.0])
    r = sw.swath_means(t, v, w, 0.0, 10.0, 5)
    assert r.mean[0] == 3.5 and r.mean[2] == 0.0 and np.isnan(r.mean[[1, 3, 4]]).all()  # a real 0 stays a 0
    assert r.count.tolist() == [2, 0, 1, 0, 0] and r.weight.tolist() == [4.0, 0.0, 2.0, 0.0, 0.0]
    d = r.as_dict()
    assert d["mean"] == [3.5, None, 0.0, None, None] and d["count"] == [2, 0, 1, 0, 0]
    assert d["total"] == {"mean": 14.0 / 6.0, "count": 3, "weight": 6.0}
    none = sw.swath_means(np.array([500.0, np.nan]), np.ones(2), np.ones(2), 0.0, 10.0, 5)
    assert none.as_dict()["total"] == {"mean": None, "count": 0, "weight": 0.0} and none.as_dict()["mean"] == [None] * 5
    assert none.counts == {"rows": 2, "outside_box": 2, "used": 0}
    empty = sw.swath_means(np.zeros(0), np.zeros(0), np.zeros(0), 0.0, 10.0, 3)
    assert empty.count.tolist() == [0, 0, 0] and np.isnan(empty.mean).all()
    with pytest.raises(ValueError):
        sw.swath_means(np.zeros(3), np.zeros(2), np.zeros(3), 0.0, 10.0, 3)


def test_sums_of_a_swath_are_pairwise_in_the_order_of_its_rows() -> None:
    """The weight and Σ w·v of a swath are `pair_sums` of its rows bit for bit — whatever the other swaths hold
    and however the rows are cut into blocks for `tick`."""
    rng = np.random.default_rng(8)
    n, count = 300_000, 37
    t = rng.uniform(-3.0, 40.0, n)
    v = rng.lognormal(0.0, 2.0, n) * 10.0 ** rng.integers(-6, 7, n)  # 12 orders of magnitude
    w = rng.uniform(0.1, 5.0, n)
    ticks: list[float] = []
    r = sw.swath_means(t, v, w, 0.0, 1.0, count, tick=ticks.append)
    idx = sf.swath_index(t, 0.0, 1.0, count)
    for k in range(count):
        m = idx == k
        s0, s1 = pair_sums(np.ascontiguousarray(w[m]), np.ascontiguousarray(v[m]))
        assert r.weight[k] == s0 and r.mean[k] == s1 / s0 and r.count[k] == m.sum(), k
    assert ticks == sorted(ticks) and ticks[-1] == 1.0 and len(ticks) >= 2
    old = sw.BLOCK
    try:
        sw.BLOCK = 4099  # other blocks, the same bits
        again = sw.swath_means(t, v, w, 0.0, 1.0, count)
    finally:
        sw.BLOCK = old
    assert bits(again.mean, r.mean) and bits(again.weight, r.weight) and bits(again.count, r.count)
    assert again.total == r.total and r.total["weight"] == math.fsum(r.weight.tolist())
    # Accuracy: 1e−12 against the exactly rounded sums.
    for k in (0, 17, 36):
        m = idx == k
        exact = math.fsum((w[m] * v[m]).tolist()) / math.fsum(w[m].tolist())
        assert r.mean[k] == pytest.approx(exact, rel=1e-12)

    class Stop(Exception):
        pass

    def stop(_: float) -> None:
        raise Stop

    with pytest.raises(Stop):  # `tick` may raise to cancel
        sw.swath_means(t, v, w, 0.0, 1.0, count, tick=stop)


def test_deviation_from_the_first_series_and_the_limit_of_12_series() -> None:
    """Т7.30-1: the 13th series is refused. The deviation is (m − m₁) / m₁ · 100, empty where either is."""
    base = np.array([2.0, 4.0, np.nan, 0.0, 5.0])
    other = np.array([3.0, 3.0, 1.0, 1.0, np.nan])
    dev = sw.deviation(other, base)
    assert dev[0] == 50.0 and dev[1] == -25.0 and np.isnan(dev[2:]).all() and dev.shape == (5,)
    assert sw.deviation(base, base)[[0, 1, 4]].tolist() == [0.0, 0.0, 0.0]
    assert float(sw.deviation(1.1, 1.0)) == pytest.approx(10.0) and math.isnan(float(sw.deviation(None, 1.0)))
    assert sw.MAX_SERIES == 12
    for n in (1, 12):
        sw.check_series(n)
    with pytest.raises(StatsError) as e:
        sw.check_series(13)
    assert e.value.code == "too_many_series" and e.value.param == "series" and "12" in e.value.message and e.value.hint
    with pytest.raises(StatsError) as e:
        sw.check_series(0)
    assert e.value.code == "bad_param" and e.value.param == "series"


# --- the cuboid ---------------------------------------------------------------------------------------------------


def test_cuboid_by_the_data_models_first_then_samples() -> None:
    f = sf.direction_frame("x")
    model = np.array([[1000.0, 1100.0], [-560.0, -500.0], [0.0, 40.0]])
    samples = np.array([[990.0, 1203.0], [-700.0, -400.0], [-30.0, 55.0]])
    c = sw.cuboid(f, models=[model], samples=[samples], block=5.0)
    assert (c.start, c.width, c.count) == (1000.0, 5.0, 20) and not c.custom and c.block == 5.0
    assert c.limits.tolist() == c.box.tolist() == [[1000.0, 1100.0], [-560.0, -500.0], [0.0, 40.0]]
    assert c.centres.tolist() == [1002.5 + 5 * k for k in range(20)] and sw.width_note(c) is None
    # Several models: the cuboid is around all of them; along u — to whole swaths.
    more = np.array([[1090.0, 1203.0], [-520.0, -480.0], [10.0, 50.0]])
    c = sw.cuboid(f, models=[model, more, np.full((3, 2), np.nan)], samples=[samples], block=5.0)
    assert (c.start, c.width, c.count) == (1000.0, 5.0, 41) and c.box[0].tolist() == [1000.0, 1205.0]
    assert c.limits.tolist() == c.box.tolist() == [[1000.0, 1205.0], [-560.0, -480.0], [0.0, 50.0]]
    # No model: around the samples, a round width near 1/25 of the extent (Т7.30-4).
    c = sw.cuboid(f, samples=[samples])
    assert (c.start, c.width, c.count) == (990.0, 10.0, 22) and c.box[0].tolist() == [990.0, 1210.0] and c.block is None
    # A width and an anchor: a boundary passes through the anchor.
    c = sw.cuboid(f, models=[model], width=30.0, anchor=5.0, block=5.0)
    assert (c.start, c.width, c.count) == (995.0, 30.0, 4) and c.box[0].tolist() == [995.0, 1115.0]
    narrow = sw.cuboid(f, models=[model], width=2.0, block=5.0)
    note = sw.width_note(narrow)
    assert narrow.count == 50 and note is not None and "уже блока" in note and "часть полос пуста" in note
    as_dict = narrow.as_dict()
    assert as_dict["start"] == 1000.0 and as_dict["count"] == 50 and as_dict["limits"]["v"] == [-560.0, -500.0]
    assert (
        as_dict["custom_limits"] is False and len(as_dict["centres"]) == 50 and as_dict["box"]["u"] == [1000.0, 1100.0]
    )
    # Refusals: too many swaths (Т7.30-1), no data at all.
    with pytest.raises(StatsError) as e:
        sw.cuboid(f, models=[model], width=0.01)
    assert e.value.code == "too_many_swaths" and e.value.param == "width"
    for nothing in ({}, {"models": [np.full((3, 2), np.nan)], "samples": []}):
        with pytest.raises(StatsError) as e:
            sw.cuboid(f, **nothing)  # type: ignore[arg-type]
        assert e.value.code == "no_data" and e.value.hint


def test_limits_of_the_comparison() -> None:
    """BF `test_limits…`: the cuboid given by hand — what is outside it is left out; the swaths start at it."""
    f = sf.direction_frame("x")
    model = np.array([[1000.0, 1020.0], [-515.0, -500.0], [0.0, 10.0]])
    limits = {"u": [1005.0, 1015.0], "v": [-515.0, -500.0], "w": [0.0, 10.0]}
    c = sw.cuboid(f, models=[model], limits=limits, block=5.0)
    assert c.custom and (c.start, c.width, c.count) == (1005.0, 5.0, 2) and c.limits[0].tolist() == [1005.0, 1015.0]
    assert (
        sw.cuboid(f, limits=np.array([[1005.0, 1015.0], [-515.0, -500.0], [0.0, 10.0]])).limits.tolist()
        == c.limits.tolist()
    )
    wide = sw.cuboid(f, limits=limits, width=4.0)  # the last swath sticks out of the limits: the box is wider
    assert wide.count == 3 and wide.box[0].tolist() == [1005.0, 1017.0] and wide.limits[0].tolist() == [1005.0, 1015.0]
    pts = np.array([[1000.0, 505.0, 5.0], [1005.0, 505.0, 5.0], [1011.0, 505.0, 5.0], [1016.0, 505.0, 5.0],
                    [1010.0, 520.0, 5.0], [1010.0, 505.0, 10.0 + 5e-9], [1010.0, 505.0, 10.1],
                    [np.nan, 505.0, 5.0]])  # fmt: skip
    assert sw.inside(pts, f, wide.limits).tolist() == [False, True, True, False, False, True, False, False]
    # BF `test_z_swaths_turn_in_plan_and_the_limits_are_inside`.
    assert sw.inside(pts[:3] * [1, 0, 0], f, [[-1.0, 1010.0], [-1, 1], [-1, 1]]).tolist() == [True, True, False]
    sel = select_values(np.array([1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0]))
    r = sw.sample_series(pts, sel, f, wide)
    assert r.counts["no_position"] == 1 and r.counts["outside_box"] == 4 and r.counts["used"] == 3
    assert r.as_dict()["mean"] == [2.0, 18.0, None] and r.count.tolist() == [1, 2, 0]  # 16 m is past the limits
    one = [0.0, 1.0]
    for bad in ({"u": [5.0, 5.0], "v": one, "w": one}, {"u": one, "v": [2.0, 1.0], "w": one}, {"u": one, "v": one},
                [one, [0.0, math.inf], one], "box"):  # fmt: skip
        with pytest.raises(StatsError) as e:
            sw.cuboid(f, limits=bad)  # type: ignore[arg-type]
        assert e.value.code == "bad_param" and e.value.param == "limits"


# --- samples ------------------------------------------------------------------------------------------------------


def series(xyz, grade, length, f, flt=None, **kwargs):
    """A sample series as the API builds it: the selection, the cuboid around the samples, the swaths."""
    sel = select_sample(grade, flt or SampleFilter(), length=length)
    c = kwargs.pop("cuboid", None) or sw.cuboid(f, samples=[sw.sample_bounds(f, xyz, sel)], **kwargs)
    return sel, c, sw.sample_series(xyz, sel, f, c)


def test_sample_series_against_a_direct_count_with_the_rules_of_the_selection() -> None:
    xyz, grade, length = holes(60, 80, seed=3)
    f = sf.frame(120.0, -35.0, 20.0)
    flt = SampleFilter(weight="length", negatives="half_abs", top_cut=30.0)
    sel, c, r = series(xyz, grade, length, f, flt, width=12.5)
    assert c.count > 20 and r.counts["capped"] >= 1 and r.counts["negative"] > 10 and r.counts["missing"] > 50
    # The direct count: the rows of the selection by hand, the projection as a plain dot product.
    ok = np.isfinite(grade) & np.isfinite(length) & (length > 0)
    v = np.where(grade < 0, -grade / 2, grade)
    v = np.minimum(v, 30.0)
    placed = ok & np.isfinite(xyz).all(axis=1)
    t = sf.project(xyz[placed], f.u)
    assert np.allclose(t, xyz[placed] @ f.u, rtol=0, atol=1e-8)
    mean, count, weight = direct(t, v[placed], length[placed], c.start, c.width, c.count)
    assert (r.count == count).all() and np.allclose(r.weight, weight, rtol=1e-12, atol=0)
    assert (
        np.allclose(r.mean, mean, rtol=1e-12, atol=0, equal_nan=True) and np.isnan(r.mean).sum() == (count == 0).sum()
    )
    assert r.counts["no_position"] == int((ok & ~placed).sum()) > 30 and r.counts["outside_box"] == 0
    assert r.counts["selected"] == int(ok.sum()) == r.counts["no_position"] + r.counts["used"]
    assert r.counts["rows"] == len(grade) and r.counts["used"] == int(count.sum()) == r.total["count"]
    assert r.total["mean"] == pytest.approx((v[placed] * length[placed]).sum() / length[placed].sum(), rel=1e-12)
    for key in ("rows", "missing", "bad_weight", "negative", "zero_excluded", "capped", "top_excluded"):
        assert r.counts[key] == sel.counts[key]
    assert r.counts["outside_blocks"] == 0 and set(r.as_dict()) == {"mean", "count", "weight", "total", "counts"}
    # The swath of every row is the swath of its projection, and the projection is that of `swath_frame`.
    k = sw.locate(xyz, f, c, sel.rows)
    has = np.isfinite(xyz[sel.rows]).all(axis=1)
    want = sf.swath_index(sf.project(xyz[sel.rows], f.u), c.start, c.width, c.count)
    assert k.dtype == np.int32 and k.shape == (sel.n,) and (k[has] == want[has]).all() and (k[has] >= 0).all()
    assert (k[~has] == sw.NO_POSITION).all() and (sw.locate(xyz[sel.rows], f, c) == k).all()
    # No weights, negatives left out, zeros left out: other rows, the same accounting.
    flt2 = SampleFilter(weight="none", negatives="exclude", exclude_zero=True)
    sel2, c2, r2 = series(xyz, grade, length, f, flt2, cuboid=c)
    ok2 = np.isfinite(grade) & (grade > 0) & sw.inside(xyz, f, c.limits)  # rows without a length are back
    assert r2.counts["outside_box"] == int(
        (np.isfinite(grade) & (grade > 0) & np.isfinite(xyz).all(axis=1) & ~ok2).sum()
    )
    mean2, count2, _ = direct(sf.project(xyz[ok2], f.u), grade[ok2], np.ones(int(ok2.sum())), c.start, c.width, c.count)
    assert (r2.count == count2).all() and np.allclose(r2.mean, mean2, rtol=1e-12, equal_nan=True)
    assert (r2.weight == r2.count).all()  # without weights the weight of a swath is its number of samples
    dev = sw.deviation(r2.mean, r.mean)
    both = np.isfinite(r.mean) & np.isfinite(r2.mean)
    assert np.allclose(dev[both], (r2.mean[both] - r.mean[both]) / r.mean[both] * 100.0) and np.isnan(dev[~both]).all()
    with pytest.raises(ValueError):
        sw.sample_series(xyz[:-1], sel, f, c)  # the positions are those of every row of the table
    # Nothing taken, nothing placed: every swath is empty, nothing is 0 but the counts.
    nothing = sw.sample_series(xyz, select_values(np.full(len(grade), np.nan)), f, c)
    assert nothing.as_dict()["mean"] == [None] * c.count and nothing.counts["used"] == nothing.counts["selected"] == 0
    lost = np.where(np.arange(len(xyz))[:, None] % 2 == 0, np.inf, np.nan) * np.ones(3)
    unplaced = sw.sample_series(lost, sel, f, c)
    assert unplaced.counts["no_position"] == sel.n and unplaced.total == {"mean": None, "count": 0, "weight": 0.0}


def test_azimuth_90_is_x_bit_for_bit() -> None:
    """ТД01-5: a sample series by the azimuth 90° (dip = roll = 0) equals the series by X bit for bit — and
    both are the swaths of the plain X coordinate."""
    xyz, grade, length = holes(50, 60, seed=5)
    answers = []
    for f in (sf.direction_frame("x"), sf.frame(90.0, 0.0, 0.0), sf.direction_frame("angles", 450.0), sf.frame(-270.0)):
        sel, c, r = series(xyz, grade, length, f, width=20.0, anchor=3.0)
        answers.append((c, r))
    c0, r0 = answers[0]
    for c, r in answers[1:]:
        assert (c.start, c.width, c.count) == (c0.start, c0.width, c0.count) and bits(c.limits, c0.limits)
        assert bits(r.mean, r0.mean) and bits(r.weight, r0.weight) and bits(r.count, r0.count)
        assert r.total == r0.total and r.counts == r0.counts
    sel = select_sample(grade, SampleFilter(), length=length)
    x = xyz[sel.rows, 0]
    placed = np.isfinite(xyz[sel.rows]).all(axis=1)
    idx = sf.swath_index(np.where(placed, x, np.nan), c0.start, c0.width, c0.count)
    assert c0.start == 3.0 + math.floor((np.nanmin(x[placed]) - 3.0) / 20.0) * 20.0 and r0.counts["used"] > 2000
    for k in range(c0.count):
        m = idx == k
        s0, s1 = pair_sums(np.ascontiguousarray(sel.weights[m]), np.ascontiguousarray(sel.values[m]))
        assert (
            r0.count[k] == m.sum()
            and r0.weight[k] == s0
            and (r0.mean[k] == s1 / s0 if m.any() else np.isnan(r0.mean[k]))
        )
    # Y is the azimuth 0°, Z — a dip of +90° whatever the azimuth: the coordinate itself.
    for f, col in ((sf.frame(0.0), 1), (sf.frame(360.0), 1), (sf.frame(33.0, 90.0), 2), (sf.direction_frame("z"), 2)):
        _, c, r = series(xyz, grade, length, f, width=10.0)
        idx = sf.swath_index(np.where(placed, xyz[sel.rows, col], np.nan), c.start, c.width, c.count)
        assert (np.bincount(idx[idx >= 0], minlength=c.count) == r.count).all()
        assert c.start == np.nanmin(xyz[sel.rows, col][placed])


TURNED = [(120.0, -35.0, 20.0), (300.0, 60.0, -110.0), (37.5, 0.0, 0.0), (10.0, -89.999, 45.0), (200.0, 90.0, 30.0),
          (0.0, 15.0, 180.0)]  # fmt: skip


@pytest.mark.parametrize("angles", TURNED)
def test_a_turned_frame_equals_points_turned_beforehand(angles: tuple[float, float, float]) -> None:
    """ТД01-6: a swath with a dip and a roll = the swath along X of the points turned into the frame of the
    cuboid beforehand, 1e−9."""
    xyz, grade, length = holes(40, 70, seed=11)
    f = sf.frame(*angles)
    flt = SampleFilter(weight="length", negatives="half_abs", top_cut=50.0)
    sel, c, r = series(xyz, grade, length, f, flt, width=15.0)
    local = xyz @ rotation(*angles).T  # (p·u, p·v, p·w) by another road: three turns and a plain product
    has = np.isfinite(xyz).all(axis=1)
    assert np.allclose(local[has], sf.to_frame(f, xyz[has]), rtol=0, atol=1e-7) and np.isnan(local[~has]).all()
    x = sf.direction_frame("x")  # u = X, v = −Y, w = Z: the same local coordinates
    turned = local * [1.0, -1.0, 1.0]
    sel_x, c_x, r_x = series(turned, grade, length, x, flt, width=15.0)
    assert c_x.count == c.count and c_x.start == pytest.approx(c.start, rel=1e-9, abs=1e-9)
    assert np.allclose(c_x.limits, c.limits, rtol=1e-9, atol=1e-9)
    assert (r_x.count == r.count).all() and r.counts == r_x.counts and r.counts["used"] > 2000
    assert np.allclose(r_x.mean, r.mean, rtol=1e-9, atol=0, equal_nan=True)
    assert np.allclose(r_x.weight, r.weight, rtol=1e-9, atol=0)
    assert r_x.total["mean"] == pytest.approx(r.total["mean"], rel=1e-9) and r_x.total["count"] == r.total["count"]
    # The same in a cuboid given by hand: what is outside it is outside in both.
    lim = c.limits.copy()
    lim[0] += [30.0, -45.0]
    lim[1] += [20.0, -20.0]
    lim[2] += [5.0, -15.0]
    a = sw.sample_series(xyz, sel, f, sw.cuboid(f, limits=lim, width=15.0))
    b = sw.sample_series(turned, sel_x, x, sw.cuboid(x, limits=lim, width=15.0))
    assert a.counts == b.counts and 0 < a.counts["outside_box"] < sel.n and (a.count == b.count).all()
    assert np.allclose(a.mean, b.mean, rtol=1e-9, atol=0, equal_nan=True)


def rotation(azimuth: float, dip: float, roll: float) -> np.ndarray:
    """The axes u, v, w (rows) built without the formulas of the core: north and east turned about Z by the
    azimuth (clockwise), tilted about the horizontal axis by the dip, rolled about the axis of the swaths."""

    def turn(axis: np.ndarray, deg: float, x: np.ndarray) -> np.ndarray:  # Rodrigues, the right-hand rule
        a, c, s = axis / np.linalg.norm(axis), np.cos(np.radians(deg)), np.sin(np.radians(deg))
        return x * c + np.cross(a, x) * s + a * (a @ x) * (1.0 - c)

    up = np.array([0.0, 0.0, 1.0])
    u0, h = turn(up, -azimuth, np.array([0.0, 1.0, 0.0])), turn(up, -azimuth, np.array([1.0, 0.0, 0.0]))
    u, n = turn(h, dip, u0), turn(h, dip, up)
    return np.array([u, turn(u, -roll, h), turn(u, -roll, n)])


# --- samples inside the blocks of a model -----------------------------------------------------------------------


def model(tensors: Any = False, seed: int = 4, share: float = 0.6):
    """A grid 8 × 6 × 4 with a part of the cells occupied (the rest is air)."""
    grid: dict[str, Any] = {"corner": [512_000.0, 7_101_000.0, 200.0], "counts": [8, 6, 4], "order": "F"}
    if tensors:
        grid["tensors"] = {
            "x": [20.0, 10.0, 5.0, 5.0, 5.0, 5.0, 10.0, 20.0],
            "y": [10.0] * 6,
            "z": [2.5, 2.5, 5.0, 10.0],
        }
        if tensors == "wild":  # cells far thinner than a slot of the table of the axis, and far wider
            grid["tensors"]["x"] = [300.0, 0.01, 0.01, 0.02, 40.0, 0.5, 0.01, 900.0]
            grid["tensors"]["z"] = [0.05, 80.0, 0.05, 0.05]
        del grid["counts"]
    else:
        grid["size"] = [5.0, 5.0, 2.5]
    grid = schema.check_grid(grid)
    rng = np.random.default_rng(seed)
    n = schema.cell_count(grid)
    idx = np.sort(rng.choice(n, int(n * share), replace=False)).astype(np.uint64)
    return grid, idx


def brute(points: np.ndarray, grid: dict[str, Any], idx: np.ndarray, tol: float = sw.FACE_TOL) -> np.ndarray:
    """The direct test of BF: a point is inside when it is within half a side of a centre, faces included."""
    centres = schema.centers(grid, idx)
    i, j, k = schema.ijk_of(idx, grid["counts"])
    half = 0.5 * np.column_stack([np.asarray(schema.steps(grid, a))[n.astype(int)] for a, n in enumerate((i, j, k))])
    return (np.abs(points[:, None, :] - centres[None]) <= half[None] + tol).all(axis=2).any(axis=1)


@pytest.mark.parametrize("tensors", [False, True, "wild"])
def test_points_in_cells_matches_a_direct_test(tensors: Any) -> None:
    """BF `test_points_in_blocks_matches_a_direct_test` on the cells of a regular model (the lookup by `idx`):
    random points, points on the faces, edges and corners of the cells, points beyond the grid, NaN."""
    grid, idx = model(tensors)
    lo, hi = np.array(schema.extent(grid)[:3]), np.array(schema.extent(grid)[3:])
    rng = np.random.default_rng(4)
    points = rng.uniform(lo - 4.0, hi + 4.0, (4000, 3))
    edges = [np.asarray(schema.edges(grid, a)) for a in range(3)]
    nodes = np.array(list(itertools.product(*edges)))  # every corner of every cell
    mids = np.array(list(itertools.product(*[(e[:-1] + e[1:]) / 2 for e in edges])))
    m = len(nodes)
    on_faces = np.vstack([np.column_stack([nodes[:, 0], rng.uniform(lo[1], hi[1], m), rng.uniform(lo[2], hi[2], m)]),
                          np.column_stack([rng.uniform(lo[0], hi[0], m), nodes[:, 1], nodes[:, 2]])])  # fmt: skip
    hair = nodes + rng.choice([-5e-7, 5e-7, 2e-6, -2e-6], nodes.shape)  # a hair off the faces: within and beyond 1e−6
    bad = np.array([[np.nan, lo[1] + 1, lo[2] + 1], [lo[0] + 1, np.inf, lo[2] + 1], [1e300, 0.0, 0.0]])
    every = np.vstack([points, nodes, mids, on_faces, hair, bad])
    want = brute(every, grid, idx)
    cells = sw.cells_of(grid, idx)
    for method in ("bits", "search"):
        got = sw.points_in_cells(every, cells, method=method)
        assert got.dtype == np.bool_ and (got == want).all(), method
    assert 0 < want[:4000].sum() < 4000 and not want[-3:].any()  # some cells are air
    assert (sw.points_in_cells(every, cells) == want).all()
    # The middles of the cells: inside exactly where the cell is occupied.
    occupied = np.zeros(schema.cell_count(grid), dtype=bool)
    occupied[idx.astype(np.int64)] = True
    i, j, k = np.unravel_index(np.arange(len(mids)), [len(e) - 1 for e in edges])
    assert (sw.points_in_cells(mids, cells) == occupied[schema.idx_of(i, j, k, grid["counts"]).astype(np.int64)]).all()
    # Other integer types of `idx` (uint32 of the cache of columns), no cells, no points.
    for dtype in (np.uint32, np.int64):
        assert (sw.points_in_cells(every, sw.cells_of(grid, idx.astype(dtype)), method="search") == want).all()
        assert (sw.points_in_cells(every, sw.cells_of(grid, idx.astype(dtype)), method="bits") == want).all()
    assert not sw.points_in_cells(points, sw.cells_of(grid, idx[:0])).any()
    assert sw.points_in_cells(points[:0], cells).shape == (0,)
    with pytest.raises(StatsError) as e:
        sw.cells_of(grid, np.array([0, schema.cell_count(grid)], dtype=np.uint64))
    assert e.value.code == "bad_param"


def test_samples_inside_the_blocks_leave_out_the_samples_outside_them() -> None:
    """Т7.30-2: a sample series «inside the blocks» of a model series does not count the samples outside its
    blocks; «in the cuboid» counts every sample of the cuboid."""
    grid, idx = model(share=0.5)
    lo, hi = np.array(schema.extent(grid)[:3]), np.array(schema.extent(grid)[3:])
    rng = np.random.default_rng(21)
    xyz = rng.uniform(lo - [10.0, 5.0, 2.0], hi + [10.0, 5.0, 2.0], (6000, 3))
    grade, length = rng.lognormal(0.0, 1.0, 6000), rng.choice([0.5, 1.0, 2.0], 6000)
    grade[::37] = np.nan
    xyz[5::211] = np.nan
    f = sf.direction_frame("x")
    box = np.array([[lo[0], hi[0]], [-hi[1], -lo[1]], [lo[2], hi[2]]])  # the extent of the model in the frame of X
    c = sw.cuboid(f, models=[box], samples=[sf.bounds(f, xyz)], block=sf.block_side(f.u, sf.grid_sides(grid)))
    assert (c.start, c.width, c.count) == (lo[0], 5.0, 8)
    sel = select_sample(grade, SampleFilter(), length=length)
    in_box = sw.sample_series(xyz, sel, f, c)
    in_blocks = sw.sample_series(xyz, sel, f, c, cells=sw.cells_of(grid, idx))
    ok = np.isfinite(grade)
    placed = ok & np.isfinite(xyz).all(axis=1)
    tol = 1e-9 * np.maximum(1.0, np.maximum(np.abs(lo), np.abs(hi)))  # 7 mm at a northing of 7·10⁶ m
    with np.errstate(invalid="ignore"):
        inside_box = placed & ((xyz >= lo - tol) & (xyz <= hi + tol)).all(axis=1)
    assert (inside_box == sw.inside(xyz, f, c.limits) & ok).all()
    inside_blocks = inside_box & brute(np.nan_to_num(xyz, nan=1e30), grid, idx)
    assert in_box.counts["used"] == int(inside_box.sum()) and in_box.counts["outside_blocks"] == 0
    assert in_blocks.counts["used"] == int(inside_blocks.sum()) < in_box.counts["used"]
    assert in_blocks.counts["outside_blocks"] == int((inside_box & ~inside_blocks).sum()) > 500
    assert in_blocks.counts["outside_box"] == in_box.counts["outside_box"] == int((placed & ~inside_box).sum()) > 500
    assert in_blocks.counts["no_position"] == int((ok & ~placed).sum()) > 10
    for r, mask in ((in_box, inside_box), (in_blocks, inside_blocks)):
        mean, count, weight = direct(xyz[mask, 0], grade[mask], length[mask], c.start, c.width, c.count)
        assert (r.count == count).all() and np.allclose(r.mean, mean, rtol=1e-12, equal_nan=True)
        assert np.allclose(r.weight, weight, rtol=1e-12)
    # A model with one occupied cell: only its samples are left, the other swaths are empty — not 0.
    one = sw.sample_series(xyz, sel, f, c, cells=sw.cells_of(grid, idx[:1]))
    cell = brute(np.nan_to_num(xyz, nan=1e30), grid, idx[:1]) & placed
    assert one.counts["used"] == int(cell.sum()) > 0 and np.isnan(one.mean).sum() == 7 and (one.count > 0).sum() == 1
    none = sw.sample_series(xyz, sel, f, c, cells=sw.cells_of(grid, idx[:0]))
    assert none.counts["used"] == 0 and none.as_dict()["mean"] == [None] * 8 and none.total["mean"] is None
    # A turned frame: the blocks are found in project coordinates, whatever the frame of the swaths.
    g = sf.frame(40.0, -20.0, 10.0)
    cg = sw.cuboid(g, models=[sf.bounds(g, schema.centers(grid, idx), np.array([5.0, 5.0, 2.5]))], block=5.0)
    turned = sw.sample_series(xyz, sel, g, cg, cells=sw.cells_of(grid, idx))
    assert turned.counts["used"] == int(inside_blocks.sum()) and turned.counts["outside_box"] > 0


def test_sample_bounds_and_warm() -> None:
    xyz, grade, length = holes(20, 30, seed=2)
    f = sf.frame(75.0, 10.0, -30.0)
    sel = select_sample(grade, SampleFilter(), length=length)
    got = sw.sample_bounds(f, xyz, sel)
    assert np.allclose(got, sf.bounds(f, xyz[sel.rows]), rtol=0, atol=0) and got.shape == (3, 2)
    assert np.isnan(sw.sample_bounds(f, xyz, select_values(np.full(len(xyz), np.nan)))).all()
    assert np.allclose(sw.sample_bounds(f, xyz), sf.bounds(f, xyz), rtol=0, atol=0)
    sw.warm()
