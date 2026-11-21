"""Bootstrap of the statistics of a sample (ТЗ 7.29; ADR 0019 §4, §7; Э13, 13.12): the tests of BF
`test_stats_uncertainty.py` (bootstrap), one seed — one result, the six statistics of the tab in one pass
against the plain loop of BF on the same draws (1e−12), the percentile rule of the project, weights of
declustering, «hard» data (a constant, a zero mean, ties), 20 000 × 1000 in time.
"""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np
import pytest

from bf_core.stats import bootstrap as bs
from bf_core.stats.weighted import StatsError, weighted_percentiles

rng = np.random.default_rng(21)


# --- BF ------------------------------------------------------------------------------------------------------


def test_bootstrap_mean_matches_standard_error_and_is_reproducible() -> None:
    x = rng.normal(10, 2, 400)
    a = bs.bootstrap(x, np.mean, 2000, seed=1)
    assert a.shape == (2000,)
    assert a.mean() == pytest.approx(x.mean(), abs=0.05)
    assert a.std() == pytest.approx(x.std() / np.sqrt(len(x)), rel=0.1)  # sigma / sqrt(n)
    assert np.array_equal(a, bs.bootstrap(x, np.mean, 2000, seed=1))
    assert not np.array_equal(a, bs.bootstrap(x, np.mean, 2000, seed=2))


def test_bootstrap_weights_pull_towards_heavy_samples() -> None:
    x = np.r_[np.zeros(50), np.ones(50)]
    heavy_ones = bs.bootstrap(x, np.mean, 500, weights=np.r_[np.ones(50), 9 * np.ones(50)], seed=3)
    assert heavy_ones.mean() == pytest.approx(0.9, abs=0.03)
    only_ones = bs.bootstrap(x, np.mean, 50, weights=np.r_[np.zeros(50), np.ones(50)], seed=3)
    assert np.all(only_ones == 1.0)


def test_bootstrap_validation() -> None:
    with pytest.raises(ValueError):
        bs.bootstrap([], np.mean)
    with pytest.raises(ValueError):
        bs.bootstrap([1.0, np.nan], np.mean)
    with pytest.raises(ValueError):
        bs.bootstrap([1.0, 2.0], np.mean, n_boot=0)
    with pytest.raises(ValueError):
        bs.bootstrap([1.0, 2.0], np.mean, weights=[0.0, 0.0])
    with pytest.raises(ValueError):
        bs.bootstrap([1.0, 2.0], np.mean, weights=[1.0])


# --- the six statistics of the tab ----------------------------------------------------------------------------


def hazen(a: np.ndarray, p: float) -> float:
    return float(np.percentile(a, p, method="hazen"))


PLAIN: dict[str, Any] = {
    "mean": np.mean,
    "median": np.median,
    "std": np.std,
    "cv": lambda a: float(np.std(a) / np.mean(a)) if np.mean(a) > 0 else math.nan,
    "p10": lambda a: hazen(a, 10),
    "p90": lambda a: hazen(a, 90),
}


def assays(n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Grades as they are: lognormal over four orders of magnitude, a fifth at the detection limit written as
    0, «<0.01» as −0.01, an outlier; declustering weights with zeros."""
    g = np.random.default_rng(seed)
    x = g.lognormal(0.0, 1.5, n) * 10.0 ** g.integers(-2, 3, n)
    x[g.random(n) < 0.2] = 0.0
    x[g.integers(0, n, n // 40)] = -0.01
    x[g.integers(0, n)] = 5e5
    w = g.uniform(0.1, 6.0, n)
    w[g.integers(0, n, n // 25)] = 0.0
    return x, w


@pytest.mark.parametrize("n", [1, 2, 7, 500, 501])
def test_realizations_are_those_of_the_plain_loop(n: int) -> None:
    """One pass gives the six statistics of every realization; each equals the statistic of the resample of BF
    (`stat(data[idx])` on the same draws of the same seed) to 1e−12 — with weights and without."""
    x, w = assays(n, seed=n)
    for weights in (None, w if w.sum() > 0 else None):
        got = bs.realizations(x, 60, weights=weights, seed=11)
        assert set(got) == set(bs.STATISTICS) == set(PLAIN)
        for name, stat in PLAIN.items():
            want = bs.bootstrap(x, stat, 60, weights=weights, seed=11)
            assert got[name].shape == (60,)
            scale = np.abs(x).max()
            assert np.allclose(got[name], want, rtol=1e-12, atol=1e-13 * scale, equal_nan=True), (name, weights is None)
        if n > 1:
            assert len(np.unique(got["mean"])) > 1


def test_percentiles_follow_the_rule_of_the_project() -> None:
    """P10, P90 and the median of a resample are those of `weighted_percentiles` without weights (Hazen: a
    value sits at the middle of its step) — the P10 of the tab is the P10 of the histogram."""
    x, _ = assays(300, seed=4)
    draws = np.random.default_rng(8)
    reals = bs.realizations(x, 25, seed=8)
    for b in range(25):
        sample = x[draws.integers(0, 300, 300)]
        want = weighted_percentiles(sample, None, [10, 50, 90])
        got = [reals["p10"][b], reals["median"][b], reals["p90"][b]]
        assert np.allclose(got, want, rtol=1e-12, atol=1e-9)
    seen = bs.observed(x)
    assert np.allclose(
        [seen["p10"], seen["median"], seen["p90"]], weighted_percentiles(x, None, [10, 50, 90]), rtol=1e-12
    )
    assert seen["mean"] == pytest.approx(x.mean(), rel=1e-12) and seen["std"] == pytest.approx(x.std(), rel=1e-12)
    assert seen["cv"] == pytest.approx(x.std() / x.mean(), rel=1e-12)


def test_intervals_of_the_six_statistics() -> None:
    """BF `test_bootstrap_interval_contains_observed_and_is_reproducible`, `…other_statistics`: the interval
    holds the observed statistic, a wider confidence — a wider interval, the histogram counts every
    realization; one seed — one result."""
    x = np.random.default_rng(5).lognormal(0.0, 0.5, 600)
    r = bs.bootstrap_stats(x, n_boot=400, seed=7, confidence=0.9)
    assert r["count"] == 600 and r["n_boot"] == 400 and r["confidence"] == 0.9 and r["seed"] == 7
    assert r["weighted"] is False and list(r["statistics"]) == list(bs.STATISTICS)
    for name, s in r["statistics"].items():
        assert s["ci_low"] < s["observed"] < s["ci_high"], name
        assert s["std"] > 0 and abs(s["mean"] - s["observed"]) < 3 * s["std"]
        assert len(s["edges"]) == len(s["counts"]) + 1 == bs.HIST_BINS + 1 and sum(s["counts"]) == 400 == s["defined"]
        assert s["observed"] == pytest.approx(float(PLAIN[name](x)), rel=1e-12) and s["observed_weighted"] is None
    assert bs.bootstrap_stats(x, n_boot=400, seed=7, confidence=0.9) == r  # the same seed, the same answer
    other = bs.bootstrap_stats(x, n_boot=400, seed=8, confidence=0.9)
    assert other["statistics"]["mean"]["ci_low"] != r["statistics"]["mean"]["ci_low"]
    wider = bs.bootstrap_stats(x, n_boot=400, seed=7, confidence=0.99)
    for name in bs.STATISTICS:
        a, b = wider["statistics"][name], r["statistics"][name]
        assert a["ci_high"] - a["ci_low"] > b["ci_high"] - b["ci_low"] and a["mean"] == b["mean"]
    # The interval of the mean is that of the normal theory: ±1.645·σ / √n.
    half = 1.645 * x.std() / math.sqrt(600)
    m = r["statistics"]["mean"]
    assert m["ci_high"] - m["observed"] == pytest.approx(half, rel=0.15)
    assert m["observed"] - m["ci_low"] == pytest.approx(half, rel=0.15)
    # No seed: one is drawn and told, so the answer can be had again.
    free = bs.bootstrap_stats(x, n_boot=50)
    assert isinstance(free["seed"], int) and bs.bootstrap_stats(x, n_boot=50, seed=free["seed"]) == free
    assert free["confidence"] == 0.95  # the interval 2.5–97.5 % (ADR 0019 §4)


def test_declustering_weights_lower_the_mean() -> None:
    """BF `test_bootstrap_declustering_weights_lower_the_mean`: samples are drawn with the probability of their
    weights, so the clustered high grades come up less often; the statistic of a resample has no weights, the
    statistic of the data is told both ways."""
    g = np.random.default_rng(6)
    x = np.r_[g.lognormal(0.0, 0.3, 72), 4.0 * g.lognormal(0.0, 0.3, 60)]  # the cluster of high grades
    w = np.r_[np.full(72, 132 * 0.9 / 72), np.full(60, 132 * 0.1 / 60)]
    plain = bs.bootstrap_stats(x, n_boot=600, seed=1)
    weighted = bs.bootstrap_stats(x, n_boot=600, seed=1, weights=w)
    pm, wm = plain["statistics"]["mean"], weighted["statistics"]["mean"]
    assert wm["mean"] < pm["mean"] and weighted["weighted"] is True
    assert wm["observed"] == pytest.approx(pm["observed"])  # the statistic itself stays unweighted
    assert wm["observed_weighted"] == pytest.approx(np.average(x, weights=w), rel=1e-12)
    assert wm["ci_low"] < wm["observed_weighted"] < wm["ci_high"] < wm["observed"]
    for name in ("median", "p10", "p90"):
        s = weighted["statistics"][name]
        p = {"median": 50, "p10": 10, "p90": 90}[name]
        assert s["observed_weighted"] == pytest.approx(float(weighted_percentiles(x, w, [p])[0]), rel=1e-12)
        assert s["ci_low"] <= s["observed_weighted"] <= s["ci_high"]
    sd = weighted["statistics"]["std"]["observed_weighted"]
    m = np.average(x, weights=w)
    assert sd == pytest.approx(math.sqrt(np.average((x - m) ** 2, weights=w)), rel=1e-12)
    assert weighted["statistics"]["cv"]["observed_weighted"] == pytest.approx(sd / m, rel=1e-12)
    # Weights of 0 are never drawn; the scale of the weights does not matter.
    only = bs.realizations(x, 40, weights=np.r_[np.zeros(72), np.ones(60)], seed=2)
    assert only["mean"].min() > 2.0
    again = bs.realizations(x, 40, weights=w * 1e-9, seed=1)
    assert np.allclose(again["mean"], bs.realizations(x, 40, weights=w, seed=1)["mean"], rtol=1e-12)


def test_weighted_draws_are_the_plain_search_of_bf() -> None:
    """With weights a row is drawn as BF draws it — `searchsorted(cdf, u, side="right")` — whatever the
    weights: equal, zeros in runs and at the ends, a thousand tiny ones in a row, twenty orders of magnitude."""
    g = np.random.default_rng(12)
    n = 3000
    zeros = g.uniform(0.5, 2.0, n)
    zeros[:40] = 0.0
    zeros[-25:] = 0.0
    zeros[g.integers(0, n, 900)] = 0.0
    tiny = g.uniform(0.5, 2.0, n)
    tiny[1000:2200] = 1e-12
    wild = 10.0 ** g.uniform(-10, 10, n)
    for w in (np.ones(n), g.uniform(0.1, 6.0, n), zeros, tiny, wild, np.r_[np.zeros(n - 1), 1.0], np.array([2.0])):
        m = int(w.size)
        cdf = np.cumsum(w) / np.cumsum(w)[-1]
        mine = bs._draws(m, w, 30, 5)
        rng_bf = np.random.default_rng(5)
        for idx in mine:
            want = np.minimum(np.searchsorted(cdf, rng_bf.random(m), side="right"), m - 1)
            assert np.array_equal(idx, want)
            assert (w[idx] > 0).all()  # a weight of 0 is never drawn
    steps = bs._Steps(tiny)
    below_one = np.nextafter(1.0, 0.0)  # `rng.random` is in [0; 1)
    edge = np.minimum(np.r_[0.0, steps.cdf, np.nextafter(steps.cdf, 0.0), (np.arange(n) + 1) / n], below_one)
    assert np.array_equal(steps.rows(edge), np.searchsorted(steps.cdf, edge, side="right"))


def test_hard_data_never_give_a_zero_for_what_is_undefined() -> None:
    """A constant: every realization is the constant, σ and CV are true zeros. A mean that is not positive: the
    CV is undefined — null, not 0 and not ∞ (as `describe`). One sample: nothing to vary."""
    r = bs.bootstrap_stats(np.full(40, 2.5), n_boot=30, seed=1)
    for name in ("mean", "median", "p10", "p90"):
        s = r["statistics"][name]
        assert s["observed"] == s["mean"] == s["ci_low"] == s["ci_high"] == 2.5 and s["std"] == 0.0
        assert sum(s["counts"]) == 30 and s["edges"][0] < 2.5 < s["edges"][-1]
    assert r["statistics"]["std"]["mean"] == 0.0 and r["statistics"]["cv"]["observed"] == 0.0
    neg = bs.bootstrap_stats(np.array([-3.0, -1.0, -2.0, -5.0]), n_boot=20, seed=1)
    cv = neg["statistics"]["cv"]
    assert cv["observed"] is None and cv["mean"] is None and cv["ci_low"] is None and cv["ci_high"] is None
    assert cv["defined"] == 0 and cv["edges"] == [] and cv["counts"] == [] and cv["std"] is None
    assert neg["statistics"]["mean"]["observed"] == -2.75 and neg["statistics"]["mean"]["defined"] == 20
    # A sample around zero: the CV of the realizations with a mean ≤ 0 is left out and counted.
    mixed = bs.bootstrap_stats(np.array([-1.0, 1.0, -2.0, 2.5, 0.0, 0.3]), n_boot=200, seed=3)
    cv = mixed["statistics"]["cv"]
    assert 0 < cv["defined"] < 200 and sum(cv["counts"]) == cv["defined"] and cv["ci_low"] > 0
    one = bs.bootstrap_stats([7.0], n_boot=10, seed=1)
    assert one["count"] == 1 and one["statistics"]["mean"]["ci_low"] == one["statistics"]["mean"]["ci_high"] == 7.0
    assert all(math.isfinite(v) for s in one["statistics"].values() for v in s["edges"])


def test_refusals() -> None:
    """BF `test_bootstrap_validation` (API): the limits of the tab; {code, message, hint, param}."""
    x = np.arange(1.0, 30.0)
    cases = [
        ({"n_boot": 5}, "bad_param", "n_boot"),
        ({"n_boot": 99999}, "bad_param", "n_boot"),
        ({"n_boot": 100.5}, "bad_param", "n_boot"),
        ({"n_boot": "много"}, "bad_param", "n_boot"),
        ({"confidence": 1.2}, "bad_param", "confidence"),
        ({"confidence": 0.5}, "bad_param", "confidence"),
        ({"confidence": None}, "bad_param", "confidence"),
        ({"seed": -1}, "bad_param", "seed"),
        ({"seed": 1.5}, "bad_param", "seed"),
        ({"weights": np.zeros(29)}, "no_values", None),
        ({"weights": -np.ones(29)}, "bad_input", None),
        ({"weights": np.ones(28)}, "bad_input", None),
    ]
    for kwargs, code, param in cases:
        with pytest.raises(StatsError) as e:
            bs.bootstrap_stats(x, **kwargs)  # type: ignore[arg-type]
        assert (e.value.code, e.value.param) == (code, param), e.value.message
        assert e.value.message and e.value.hint
    for bad, code in (([], "no_values"), ([1.0, math.nan], "bad_input"), ([[1.0, 2.0]], "bad_input")):
        with pytest.raises(StatsError) as e:
            bs.bootstrap_stats(bad)
        assert e.value.code == code
    assert bs.MAX_BOOT == 5000 and bs.MIN_BOOT == 10
    ok = bs.bootstrap_stats(x, n_boot=10, seed=2**40)
    assert ok["n_boot"] == 10 and ok["seed"] == 2**40


def test_twenty_thousand_by_a_thousand_in_time() -> None:
    """The limit of the tab: 20 000 samples × 1000 realizations ≤ 3 s (13.12) — the six statistics at once,
    with weights and without; progress rises to 1, `tick` may raise to cancel."""
    x, w = assays(20_000, seed=2)
    for weights in (None, w):
        ticks: list[float] = []
        t0 = time.perf_counter()
        r = bs.bootstrap_stats(x, n_boot=1000, seed=1, weights=weights, tick=ticks.append)
        seconds = time.perf_counter() - t0
        assert seconds < 3.0, f"{seconds:.2f} s"
        assert ticks == sorted(ticks) and ticks[-1] == 1.0 and len(ticks) >= 10
        assert all(s["defined"] == 1000 for s in r["statistics"].values())

    class Stop(Exception):
        pass

    def stop(_: float) -> None:
        raise Stop

    with pytest.raises(Stop):
        bs.bootstrap_stats(x, n_boot=1000, seed=1, tick=stop)
