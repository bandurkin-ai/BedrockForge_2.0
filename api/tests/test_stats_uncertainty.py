import numpy as np
import pytest

from app import stats

try:  # reference implementation, only needed for the cross-check tests
    from geostatspy import geostats as gs
except ImportError:  # pragma: no cover
    gs = None

needs_legacy = pytest.mark.skipif(gs is None, reason="geostatspy not installed")
rng = np.random.default_rng(21)


# ---- bootstrap -----------------------------------------------------------
def test_bootstrap_mean_matches_standard_error_and_is_reproducible():
    x = rng.normal(10, 2, 400)
    a = stats.bootstrap(x, np.mean, 2000, seed=1)
    assert a.shape == (2000,)
    assert a.mean() == pytest.approx(x.mean(), abs=0.05)
    assert a.std() == pytest.approx(x.std() / np.sqrt(len(x)), rel=0.1)    # sigma / sqrt(n)
    assert np.array_equal(a, stats.bootstrap(x, np.mean, 2000, seed=1))
    assert not np.array_equal(a, stats.bootstrap(x, np.mean, 2000, seed=2))


def test_bootstrap_weights_pull_towards_heavy_samples():
    x = np.r_[np.zeros(50), np.ones(50)]
    heavy_ones = stats.bootstrap(x, np.mean, 500, weights=np.r_[np.ones(50), 9 * np.ones(50)], seed=3)
    assert heavy_ones.mean() == pytest.approx(0.9, abs=0.03)
    only_ones = stats.bootstrap(x, np.mean, 50, weights=np.r_[np.zeros(50), np.ones(50)], seed=3)
    assert np.all(only_ones == 1.0)


def test_bootstrap_validation():
    with pytest.raises(ValueError):
        stats.bootstrap([], np.mean)
    with pytest.raises(ValueError):
        stats.bootstrap([1.0, np.nan], np.mean)
    with pytest.raises(ValueError):
        stats.bootstrap([1.0, 2.0], np.mean, n_boot=0)
    with pytest.raises(ValueError):
        stats.bootstrap([1.0, 2.0], np.mean, weights=[0.0, 0.0])
    with pytest.raises(ValueError):
        stats.bootstrap([1.0, 2.0], np.mean, weights=[1.0])


def test_resample_groups_keeps_holes_whole():
    groups = np.array(["a", "a", "b", "b", "b", "c"])
    draws = stats.resample_groups(groups, 200, seed=4)
    assert len(draws) == 200
    for idx in draws:
        counts = {g: int((groups[idx] == g).sum()) for g in "abc"}
        assert counts["a"] % 2 == 0 and counts["b"] % 3 == 0              # whole holes only
    assert {len(d) for d in draws} > {6}                                   # sizes vary with the draw
    assert all(np.array_equal(x, y) for x, y in zip(draws, stats.resample_groups(groups, 200, seed=4)))
    with pytest.raises(ValueError):
        stats.resample_groups([], 5)


# ---- local statistics ----------------------------------------------------
R = rng.lognormal(0, 0.5, (30, 6, 7))


def test_local_statistics_match_numpy_per_cell():
    assert np.allclose(stats.local_expectation(R), R.mean(axis=0))
    assert np.allclose(stats.local_std(R), R.std(axis=0))
    assert np.allclose(stats.local_percentile(R, 90), np.percentile(R, 90, axis=0))
    t = 1.2
    assert np.allclose(stats.local_prob_exceed(R, t), (R >= t).mean(axis=0))
    assert stats.local_expectation(R).shape == (6, 7)


@needs_legacy
def test_local_statistics_match_legacy():
    assert np.allclose(stats.local_expectation(R), gs.local_expectation(R))
    assert np.allclose(stats.local_std(R), gs.local_standard_deviation(R))
    assert np.allclose(stats.local_percentile(R, 10), gs.local_percentile(R, 10))
    assert np.allclose(stats.local_prob_exceed(R, 1.0), gs.local_probability_exceedance(R, 1.0))


def test_local_statistics_skip_nan_and_flag_empty_cells():
    r = R.copy()
    r[:10, 0, 0] = np.nan                                                  # 20 valid left
    r[:, 1, 1] = np.nan                                                    # none valid
    assert stats.local_expectation(r)[0, 0] == pytest.approx(R[10:, 0, 0].mean())
    assert stats.local_prob_exceed(r, 1.0)[0, 0] == pytest.approx((R[10:, 0, 0] >= 1.0).mean())
    for f in (stats.local_expectation, stats.local_std, lambda a: stats.local_percentile(a, 50),
              lambda a: stats.local_prob_exceed(a, 1.0)):
        assert np.isnan(f(r)[1, 1])


def test_local_validation_and_1d_input():
    with pytest.raises(ValueError):
        stats.local_percentile(R, 101)
    with pytest.raises(ValueError):
        stats.local_expectation(np.empty((0, 3)))
    assert stats.local_expectation(np.array([1.0, 3.0])) == pytest.approx(2.0)   # a single location


# ---- idw -----------------------------------------------------------------
def test_idw_exact_at_data_symmetric_and_masked_by_radius():
    pts = np.array([[0.0, 0.0], [10.0, 0.0]])
    v = np.array([1.0, 3.0])
    est = stats.idw(pts, v, np.array([[0.0, 0.0], [5.0, 0.0], [10.0, 0.0], [5.0, 40.0]]), k=2, radius=20)
    assert est[0] == pytest.approx(1.0) and est[2] == pytest.approx(3.0)   # honours the data
    assert est[1] == pytest.approx(2.0)                                    # midpoint
    assert np.isnan(est[3])                                                # nothing within 20
    near = stats.idw(pts, v, np.array([[2.0, 0.0]]), k=2)
    assert 1.0 < near[0] < 2.0                                             # closer to the first datum


def test_idw_coincident_data_and_validation():
    pts = np.array([[0.0, 0.0], [0.0, 0.0], [5.0, 0.0]])
    assert stats.idw(pts, np.array([1.0, 3.0, 9.0]), np.array([[0.0, 0.0]]), k=3)[0] == pytest.approx(2.0)
    assert stats.idw(pts, np.array([1.0, 3.0, 9.0]), np.array([[1.0, 0.0]]), k=1)[0] in (1.0, 3.0)   # k=1: one datum
    for bad in ({"k": 0}, {"power": 0}, {"radius": -1}):
        with pytest.raises(ValueError):
            stats.idw(pts, np.ones(3), np.zeros((1, 2)), **bad)
    with pytest.raises(ValueError):
        stats.idw(np.zeros((3, 3)), np.ones(3), np.zeros((1, 2)))
