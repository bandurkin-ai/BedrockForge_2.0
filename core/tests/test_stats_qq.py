"""Q-Q plot (ТЗ 7.26; ADR 0019 §4): Т7.26-1, Т7.26-2, Т7.26-3 and the tests of BF `test_qq.py` — quantiles
against numpy, a shift, the Kolmogorov–Smirnov distance, fitted distributions, thinning.

Tolerance — 1e−12 relative for quantiles and plotting positions; Т7.26-2 and Т7.26-3 — exact."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest
from scipy.special import ndtri

from bf_core.stats import qq
from bf_core.stats.describe import describe
from bf_core.stats.qq import MAX_POINTS, ks_distance, probabilities, qq_chart, qq_distribution, qq_samples
from bf_core.stats.select import SampleFilter, select_sample, select_values
from bf_core.stats.weighted import StatsError, sort_groups, weighted_percentiles

NAN = math.nan
REL = 1e-12
NONE = SampleFilter(weight="none")


def srt(v, w=None):  # type: ignore[no-untyped-def]
    """A sample sorted once (`w` None — equal weights)."""
    v = np.asarray(v, dtype=np.float64)
    return sort_groups(v, np.ones(v.shape) if w is None else w)[0]


def cols(r: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pts = np.array(r["points"], dtype=np.float64)
    return pts[:, 0], pts[:, 1], pts[:, 2]


def refused(code: str, param: str | None, call) -> StatsError:  # type: ignore[no-untyped-def]
    with pytest.raises(StatsError) as e:
        call()
    assert e.value.code == code and e.value.param == param and e.value.message and e.value.hint
    return e.value


# --- acceptance of ТЗ 7.26 ---------------------------------------------------------------------------------


def test_t726_1_lognormal_sample_fits_lognormal() -> None:
    """Т7.26-1: a lognormal sample in the mode «логнормальное» gives PPCC ≥ 0.999 (10 000 values, fixed seed)."""
    v = np.random.default_rng(7261).lognormal(0.4, 1.1, 10_000)
    r = qq_distribution(srt(v), "lognormal")
    assert r["ppcc"] >= 0.999
    assert r["distribution"]["name"] == "lognormal" and r["distribution"]["dropped_nonpositive"] == 0
    assert r["distribution"]["mu"] == pytest.approx(np.log(v).mean(), rel=REL)
    assert r["distribution"]["sigma"] == pytest.approx(np.log(v).std(), rel=REL)
    # the points lie along the line 1:1 in logarithms: the theoretical quantile against the value
    theory, value, _ = cols(r)
    assert np.corrcoef(np.log(theory), np.log(value))[0, 1] >= 0.999
    assert abs(np.polyfit(np.log(theory), np.log(value), 1)[0] - 1.0) < 0.01
    # with weights by length too; the same sample does not fit the normal distribution
    w = np.random.default_rng(1).choice([0.5, 1.0, 2.0], 10_000)
    assert qq_distribution(srt(v, w), "lognormal")["ppcc"] >= 0.999
    assert qq_distribution(srt(v), "normal")["ppcc"] < 0.9


def test_t726_2_equal_samples_lie_on_the_line() -> None:
    """Т7.26-2: two equal samples — D = 0 and the points on 1:1, exactly."""
    rng = np.random.default_rng(7262)
    v = np.round(rng.lognormal(0, 1, 3000), 2)
    w = rng.choice([0.5, 1.0, 1.5], 3000)
    r = qq_samples(srt(v, w), srt(v.copy(), w.copy()))
    x, y, p = cols(r)
    assert r["ks"] == 0.0 and np.array_equal(x, y) and len(x) == 99
    assert p == pytest.approx(np.arange(1, 100), rel=REL)  # 99 quantiles: every percent
    assert r["ratio"] == [1.0 if q != 0 else None for q in x]
    assert r["extremes"]["x"] == r["extremes"]["y"] == [float(v.min()), float(v.max())]
    assert all(m[0] == m[1] for m in r["marks"]) and [m[2] for m in r["marks"]] == [10.0, 50.0, 90.0]
    # through the chart: the same selection on both sides
    sel = select_values(v, w, SampleFilter(weight="field", weight_field="W"))
    out = qq_chart(sel, weighted=True, distribution="sample", y=sel, y_weighted=True)
    assert out["mode"] == "sample" and out["ks"] == 0.0 and out["x"] == out["y"]
    assert all(a == b for a, b, _ in out["points"])
    # the same rows in another order are the same distribution
    order = rng.permutation(3000)
    assert qq_samples(srt(v, w), srt(v[order], w[order]))["ks"] <= 1e-14


def test_t726_3_five_thousand_samples_are_thinned_to_two_thousand_points() -> None:
    """Т7.26-3: 5000 samples — no more than 2000 points, the minimum and the maximum stay."""
    v = np.random.default_rng(7263).lognormal(0, 1, 5000)
    for dist in ("lognormal", "normal"):
        r = qq_distribution(srt(v), dist)
        _, value, p = cols(r)
        assert len(r["points"]) == MAX_POINTS == 2000 and r["count"] == 5000
        assert value[0] == v.min() and value[-1] == v.max()
        assert bool(np.all(np.diff(value) >= 0.0)) and bool(np.all(np.diff(p) > 0.0))
        assert p[0] == pytest.approx(100 * 0.5 / 5000, rel=REL) and p[-1] == pytest.approx(100 * 4999.5 / 5000, rel=REL)
    # 2000 samples are not thinned: every sample is a point at its plotting position
    r = qq_distribution(srt(v[:2000]), "lognormal")
    _, value, p = cols(r)
    assert len(value) == 2000 and np.array_equal(value, np.sort(v[:2000]))
    assert p == pytest.approx((np.arange(2000) + 0.5) / 2000 * 100, rel=REL)


# --- the tests of BF `test_qq.py` --------------------------------------------------------------------------


def test_unweighted_quantiles_match_numpy_and_a_shift_is_exact() -> None:
    """BF `test_unweighted_quantiles_match_numpy_and_a_shift_is_exact`."""
    x = np.random.default_rng(2).lognormal(0, 1, 400)
    r = qq_samples(srt(x), srt(x + 1.0), 99)
    qx, qy, p = cols(r)
    assert qx == pytest.approx(np.percentile(x, probabilities(99), method="hazen"), rel=REL)
    assert np.allclose(qy - qx, 1.0, rtol=0, atol=1e-12)  # a shift moves every quantile by the same amount
    assert p[49] == pytest.approx(50.0, rel=REL)
    assert r["extremes"]["y"][0] == pytest.approx(x.min() + 1, rel=REL)
    assert r["marks"][1][0] == pytest.approx(np.percentile(x, 50, method="hazen"), rel=REL)


def test_ks_distance() -> None:
    """BF `test_ks_distance`."""
    a = np.array([1.0, 2, 3, 4])
    assert ks_distance(srt(a), srt(a)) == 0
    assert ks_distance(srt(a), srt(a + 10)) == 1.0
    assert ks_distance(srt(a), srt([2.0, 3, 4, 5])) == pytest.approx(0.25, rel=REL)
    assert ks_distance(srt(a + 10), srt(a)) == 1.0
    # weighted, of different sizes, with equal values — against the distribution functions on the joint grid
    rng = np.random.default_rng(3)
    x, y = np.round(rng.lognormal(0, 1, 700), 1), np.round(rng.lognormal(0.3, 0.8, 450), 1)
    wx, wy = rng.uniform(0.2, 3, 700), rng.uniform(0.2, 3, 450)
    grid = np.union1d(x, y)

    def cdf(v: np.ndarray, w: np.ndarray) -> np.ndarray:
        return np.array([math.fsum(w[v <= t]) for t in grid]) / math.fsum(w)

    want = float(np.max(np.abs(cdf(x, wx) - cdf(y, wy))))
    assert ks_distance(srt(x, wx), srt(y, wy)) == pytest.approx(want, rel=1e-12)
    assert ks_distance(srt(y, wy), srt(x, wx)) == pytest.approx(want, rel=1e-12)


@pytest.mark.parametrize("dist", ["normal", "lognormal"])
def test_fitted_distribution_is_a_straight_line(dist: str) -> None:
    """BF `test_fitted_distribution_is_a_straight_line`."""
    rng = np.random.default_rng(4)
    v = rng.normal(5, 2, 1500) if dist == "normal" else rng.lognormal(0.5, 0.8, 1500)
    r = qq_distribution(srt(v), dist)
    assert r["ppcc"] > 0.998 and len(r["points"]) == 1500
    t = np.log(v) if dist == "lognormal" else v
    assert r["distribution"]["mu"] == pytest.approx(t.mean(), rel=REL)
    assert r["distribution"]["sigma"] == pytest.approx(t.std(), rel=REL)
    # a uniform sample does not fit a normal distribution as well
    u = qq_distribution(srt(rng.uniform(0, 1, 1500)), "normal")
    assert u["ppcc"] < r["ppcc"]
    # the points: the quantile of the fitted distribution at the plotting position against the value
    theory, value, p = cols(r)
    z = ndtri((np.arange(1500) + 0.5) / 1500)
    want = np.exp(t.mean() + t.std() * z) if dist == "lognormal" else t.mean() + t.std() * z
    assert theory == pytest.approx(want, rel=1e-11) and np.array_equal(value, np.sort(v))
    assert r["ppcc"] == pytest.approx(np.corrcoef(z, np.sort(t))[0, 1], rel=1e-12)


def test_large_samples_are_thinned_keeping_the_ends() -> None:
    """BF `test_large_samples_are_thinned_keeping_the_ends`."""
    v = np.random.default_rng(5).normal(0, 1, 10_000)
    r = qq_distribution(srt(v), "normal")
    assert len(r["points"]) == MAX_POINTS
    assert r["points"][0][1] == v.min() and r["points"][-1][1] == v.max()
    # the thinned points are the sample interpolated at even steps of probability
    _, value, p = cols(r)
    pos = (np.arange(10_000) + 0.5) / 10_000
    assert np.diff(p) == pytest.approx((pos[-1] - pos[0]) / 1999 * 100, rel=1e-9)
    assert value == pytest.approx(np.interp(p / 100, pos, np.sort(v)), rel=1e-9, abs=1e-12)


def test_lognormal_drops_nonpositive_values() -> None:
    """BF `test_lognormal_drops_nonpositive_values`."""
    r = qq_distribution(srt([-1.0, 0, 1, 2, 4, 8]), "lognormal")
    assert r["distribution"]["dropped_nonpositive"] == 2 and len(r["points"]) == 4 and r["count"] == 4
    assert r["distribution"]["mu"] == pytest.approx(np.log([1.0, 2, 4, 8]).mean(), rel=REL)
    assert cols(r)[2] == pytest.approx([12.5, 37.5, 62.5, 87.5], rel=REL)  # positions among the positive ones


def test_chart_two_sets_and_theory() -> None:
    """BF `test_qq_api_two_sets_and_theory` without the database: set B is set A's grades times two."""
    rng = np.random.default_rng(7)
    sides, grades = [], []
    for scale in (1.0, 2.0):
        grades.append(rng.lognormal(0, 0.5, 200) * scale)
        length = 1.0 + np.arange(200) % 2
        sides.append(select_sample(grades[-1], SampleFilter(weight="length"), length=length))
    a, b = sides
    body = qq_chart(a, distribution="sample", y=b, quantiles=19)
    assert body["mode"] == "sample" and len(body["points"]) == 19 and len(body["ratio"]) == 19
    slope = np.median([p[1] / p[0] for p in body["points"]])
    assert 1.6 < slope < 2.5 and np.median(body["ratio"]) == slope
    assert body["x"]["weighted"] and body["y"]["counts"]["used"] == 200 and body["ks"] > 0.3
    assert body["x"]["stats"] == describe(a.sort()) and body["y"]["stats"]["count"] == 200

    plain = select_values(grades[0], None, NONE)  # BF: no weight on this request
    t = qq_chart(plain, distribution="lognormal")
    assert t["mode"] == "theory" and t["ppcc"] > 0.98 and len(t["points"]) == 200 and t["y"] is None
    assert not t["x"]["weighted"] and t["x"]["stats"] == describe(plain.sort(), weighted=False)
    assert cols(t)[2] == pytest.approx((np.arange(200) + 0.5) / 2, rel=REL)
    assert qq_chart(a, weighted=True)["distribution"]["name"] == "lognormal"  # the default of ТЗ 7.26
    json.dumps([body, t], allow_nan=False)

    refused("bad_param", "distribution", lambda: qq_chart(a, weighted=True, distribution="gamma"))
    refused("bad_param", "y_field", lambda: qq_chart(a, weighted=True, distribution="sample"))
    empty = select_values(np.array([NAN]), None, NONE)
    refused("no_values", None, lambda: qq_chart(empty, weighted=False, distribution="normal"))
    refused("no_values", None, lambda: qq_chart(a, weighted=True, distribution="sample", y=empty))
    for bad in (2, 1000, 19.5, "99", True):
        refused("bad_param", "quantiles", lambda bad=bad: qq_chart(a, weighted=True, distribution="sample", y=b,
                                                                   quantiles=bad))  # fmt: skip
    assert len(qq_chart(a, weighted=True, distribution="sample", y=b, quantiles=999.0)["points"]) == 999
    assert len(qq_chart(a, distribution="sample", y=b, quantiles=None)["points"]) == 99  # the default of ТЗ 7.26


# --- plotting positions, weights, hard data ----------------------------------------------------------------


def test_weighted_plotting_positions() -> None:
    """A sample sits at P = (C − w / 2) / W against μ + σ·Φ⁻¹(P) with the weighted μ and σ."""
    v = np.array([5.0, 1.0, 9.0, 2.0])
    w = np.array([2.0, 1.0, 1.0, 3.0])
    r = qq_distribution(srt(v, w), "normal")
    theory, value, p = cols(r)
    pos = np.array([0.5, 2.5, 5.0, 6.5]) / 7.0
    mu = (5 * 2 + 1 + 9 + 2 * 3) / 7.0
    sigma = math.sqrt((2 * (5 - mu) ** 2 + (1 - mu) ** 2 + (9 - mu) ** 2 + 3 * (2 - mu) ** 2) / 7.0)
    assert value.tolist() == [1.0, 2.0, 5.0, 9.0] and p == pytest.approx(pos * 100, rel=REL)
    assert theory == pytest.approx(mu + sigma * ndtri(pos), rel=REL)
    assert r["distribution"]["mu"] == pytest.approx(mu, rel=REL)
    assert r["distribution"]["sigma"] == pytest.approx(sigma, rel=REL)
    # the marks P10, P50, P90: the quantile of the distribution against the percentile of the sample
    for mark, q in zip(r["marks"], (10, 50, 90), strict=True):
        assert mark[2] == q and mark[0] == pytest.approx(mu + sigma * ndtri(q / 100), rel=REL)
        assert mark[1] == pytest.approx(weighted_percentiles(v, w, [q])[0], rel=REL)
    # two samples: the quantiles of each side are its weighted percentiles
    y, wy = np.array([3.0, 4.0, 8.0]), np.array([1.0, 1.0, 2.0])
    r = qq_samples(srt(v, w), srt(y, wy), 9)
    qx, qy, ps = cols(r)
    assert ps == pytest.approx(np.arange(1, 10) * 10.0, rel=REL)
    assert qx == pytest.approx(weighted_percentiles(v, w, ps), rel=REL)
    assert qy == pytest.approx(weighted_percentiles(y, wy, ps), rel=REL)
    assert r["ratio"] == pytest.approx(qy / qx, rel=REL)


def test_hard_data() -> None:
    # nothing to fit
    refused("no_values", None, lambda: qq_distribution(srt([]), "normal"))
    refused("too_few_values", None, lambda: qq_distribution(srt([3.0]), "normal"))
    e = refused("too_few_values", None, lambda: qq_distribution(srt([-1.0, 0.0, 0.0, 5.0]), "lognormal"))
    assert "больше нуля" in e.message
    e = refused("no_spread", None, lambda: qq_distribution(srt(np.full(50, 2.5)), "lognormal"))
    assert "одинаковы" in e.message
    refused("no_spread", None, lambda: qq_distribution(srt(np.full(50, -2.5)), "normal"))
    refused("no_values", None, lambda: qq_samples(srt([1.0, 2.0]), srt([])))
    # two values: points, but no correlation to speak of
    r = qq_distribution(srt([1.0, 3.0]), "normal")
    assert r["ppcc"] is None and len(r["points"]) == 2
    # a weight that outweighs the rest by 1e20: the positions stay inside (0; 1), the quantiles are finite
    v = np.array([1.0, 2.0, 3.0, 4.0])
    r = qq_distribution(srt(v, [1.0, 1.0e20, 1.0, 1.0]), "normal")
    theory, value, p = cols(r)
    assert bool(np.all(np.isfinite(theory))) and value.tolist() == v.tolist() and 0.0 < p[0] and p[-1] <= 100.0
    assert r["ppcc"] is not None and math.isfinite(r["ppcc"])
    # values from 1e−300 to 1e300 fit a lognormal distribution; their squares do not fit float64 — refused
    r = qq_distribution(srt([1e-300, 1.0, 1e300]), "lognormal")
    assert r["distribution"]["mu"] == pytest.approx(0.0, abs=1e-9) and len(r["points"]) == 3
    json.dumps(r, allow_nan=False)
    e = refused("no_spread", None, lambda: qq_distribution(srt([1e200, 2e200, 9e200]), "normal"))
    assert "велики" in e.message
    # many equal values (assays at the detection limit): the points do not go back
    rng = np.random.default_rng(6)
    v = np.where(rng.uniform(size=6000) < 0.4, 0.005, np.round(rng.lognormal(-1, 1.2, 6000), 2) + 0.01)
    r = qq_distribution(srt(v, rng.uniform(0.5, 2, 6000)), "lognormal")
    theory, value, p = cols(r)
    assert len(value) == 2000 and value[0] == 0.005 and value[-1] == v.max()
    assert bool(np.all(np.diff(value) >= 0.0)) and bool(np.all(np.diff(theory) > 0.0))
    # a quantile of zero has no ratio
    r = qq_samples(srt([0.0, 0.0, 0.0, 1.0]), srt([1.0, 2.0, 3.0, 4.0]), 3)
    assert r["ratio"][0] is None and r["ratio"][-1] is not None and r["ks"] == pytest.approx(0.75, rel=REL)
    json.dumps(r, allow_nan=False)


def test_two_sides_in_threads_give_the_same_bits() -> None:
    """Large sides are sorted in two threads; the answer does not depend on it."""
    rng = np.random.default_rng(9)
    n = 60_000
    a = select_values(np.round(rng.lognormal(0, 1, n), 2), rng.uniform(0.5, 2, n), NONE)
    b = select_values(np.round(rng.lognormal(0.2, 1.1, n), 2), rng.uniform(0.5, 2, n), NONE)
    one = qq_chart(a, weighted=True, distribution="sample", y=b, y_weighted=True, parallel_from=10**9)
    two = qq_chart(a, weighted=True, distribution="sample", y=b, y_weighted=True, parallel_from=1)
    assert one == two and one["ks"] > 0.05
    seen: list[float] = []
    qq_chart(a, weighted=True, distribution="sample", y=b, parallel_from=1, tick=seen.append)
    assert seen == sorted(seen) and seen[-1] == 1.0

    class Stop(Exception):
        pass

    def stop(_: float) -> None:
        raise Stop

    for limit in (1, 10**9):
        with pytest.raises(Stop):
            qq_chart(a, weighted=True, distribution="sample", y=b, parallel_from=limit, tick=stop)
    with pytest.raises(Stop):
        qq_chart(a, weighted=True, distribution="normal", tick=stop)
    qq.warm()
