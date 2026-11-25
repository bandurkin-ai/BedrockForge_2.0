"""Indicator kriging (ТЗ 8.8.1; ADR 0024 §6; Э18, 18.9): Т8.8-13 (the IK is non-decreasing, in [0; 1]; 1/2/4 threads
to the bit); IK with one model for all the thresholds = OK of the indicators (to the bit); the median IK = the same λ
applied to every indicator (`apply_weights`); the order relations of GSLIB; the E-type and the means of the classes;
gaps; the degenerate system; the passes; the warm-up."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.geostat.estimate import cov as cv
from bf_core.geostat.estimate import indicator as ik
from bf_core.geostat.estimate import krige as kg
from bf_core.geostat.estimate import search as se
from bf_core.geostat.estimate.samples import prepare
from bf_core.geostat.estimate.spec_parts import Pass
from bf_core.geostat.variogram.model import Model, Structure

SPH = Model(0.1, (Structure("spherical", 0.9, 30.0, 25.0, 20.0, 30.0, 10.0, 5.0),))
EXP = Model(0.05, (Structure("exponential", 0.5, 20.0, 20.0, 20.0),))
T = [0.5, 1.0, 2.5]
SIZE = (4.0, 4.0, 2.0)
DISC = (2, 2, 2)


def cm(model: Model = SPH) -> cv.CovModel:
    return cv.cov_model(model)


def make_samples(n: int, seed: int = 1, extent: float = 60.0):
    rng = np.random.default_rng(seed)
    xyz = rng.uniform(0.0, extent, (n, 3)) * np.array([1.0, 1.0, 0.3])
    z = rng.lognormal(0.0, 0.9, n)
    w = rng.uniform(0.5, 2.0, n)
    s = prepare(xyz, z, None, w, np.zeros(n, np.int32), ["h"], np.zeros(n, np.int16), np.arange(n, dtype=np.int64),
                np.zeros((n, 2)), origin=np.zeros(3))  # fmt: skip
    return s


def searcher_of(s, passes=None, r: float = 30.0) -> se.Searcher:
    ell = se.Ellipsoid.of((90.0, 0.0, 0.0), (r, r, r))
    return se.Searcher(s, ell, passes or [Pass(1.0, n_min=3, n_max=16)])


def centres_of(m: int, seed: int = 2, extent: float = 60.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.uniform(0.0, extent, (m, 3)) * np.array([1.0, 1.0, 0.3])


def setup_of(covs, means=(0.2, 0.7, 1.6, 4.0), t=T, size=SIZE) -> ik.IkSetup:
    return ik.IkSetup.of(t, covs, means, size=size, disc=DISC)


def block(s, m=40, passes=None):
    sr = searcher_of(s, passes)
    c = centres_of(m)
    return sr, c, sr.gather(c, 0)


# --- the indicators, the order relations, the E-type, the means ---


def test_indicators_gap_is_not_zero() -> None:
    out = ik.indicators([0.4, 0.5, 0.9, np.nan, 3.0], [0.5, 1.0])
    np.testing.assert_array_equal(out[:3], [[1, 1], [1, 1], [0, 1]])  # z ≤ t: the threshold itself is below
    assert np.isnan(out[3]).all() and not np.isnan(out[[0, 1, 2, 4]]).any()
    np.testing.assert_array_equal(out[4], [0, 0])


def test_order_relations_gslib() -> None:
    f = np.array([-0.1, 0.4, 0.3, 1.2, 0.9])
    ik.order_relations(f)
    # clip: [0, .4, .3, 1, .9]; up: [0, .4, .4, 1, 1]; down: [0, .3, .3, .9, .9]; the mean
    np.testing.assert_allclose(f, [0.0, 0.35, 0.35, 0.95, 0.95], atol=1e-15)
    g = np.array([0.1, 0.2, 0.7])
    ik.order_relations(g)
    np.testing.assert_array_equal(g, [0.1, 0.2, 0.7])  # an ordered row is not touched
    h = np.array([0.1, np.nan, 0.7])
    ik.order_relations(h)
    assert np.isnan(h).all()  # a gap is a gap, not a 0


def test_order_relations_random_monotone() -> None:
    rng = np.random.default_rng(5)
    for _ in range(200):
        f = rng.uniform(-0.5, 1.5, rng.integers(1, 21))
        ik.order_relations(f)
        assert (f >= 0.0).all() and (f <= 1.0).all() and (np.diff(f) >= 0.0).all()


def test_etype_by_hand() -> None:
    f = np.array([0.2, 0.5, 0.9])
    m = np.array([1.0, 2.0, 3.0, 5.0])
    assert ik.etype(f, m) == pytest.approx(0.2 * 1 + 0.3 * 2 + 0.4 * 3 + 0.1 * 5, abs=1e-15)
    one = np.array([1.0])  # one threshold, the whole mass below it
    assert ik.etype(one, np.array([2.0, 9.0])) == 2.0
    assert ik.etype(np.array([0.0]), np.array([2.0, 9.0])) == 9.0


def test_class_means_declustered_and_empty() -> None:
    z = np.array([0.1, 0.3, 0.5, 0.8, 1.0, 3.0, 4.0, np.nan])
    w = np.array([1.0, 3.0, 1.0, 2.0, 2.0, 1.0, 1.0, 5.0])
    m = ik.class_means(z, w, [0.5, 1.0, 2.5])
    np.testing.assert_allclose(m[0], (0.1 + 3 * 0.3 + 0.5) / 5, atol=1e-15)  # z ≤ 0.5: the border belongs below
    np.testing.assert_allclose(m[1], (2 * 0.8 + 2 * 1.0) / 4, atol=1e-15)
    assert m[2] == pytest.approx(1.75, abs=1e-15)  # (1; 2.5] is empty: the middle of its thresholds
    assert m[3] == pytest.approx(3.5, abs=1e-15)
    plain = ik.class_means(z, None, [0.5, 1.0, 2.5])  # no weights — the plain means
    assert plain[0] == pytest.approx(0.3) and plain[3] == pytest.approx(3.5)
    empty_top = ik.class_means(np.array([0.1, 0.2]), None, [0.5, 1.0])
    np.testing.assert_allclose(empty_top, [0.15, 0.75, 1.0], atol=1e-15)  # the extreme empty class: its one threshold
    low = ik.class_means(np.array([5.0, 6.0]), None, [0.5, 1.0])
    assert low[0] == 0.5 and low[1] == 0.75 and low[2] == 5.5
    nothing = ik.class_means(np.array([np.nan]), None, [1.0, 2.0])
    np.testing.assert_allclose(nothing, [1.0, 1.5, 2.0])


def test_class_means_unweighted_class_falls_back() -> None:
    z = np.array([0.1, 0.3, 2.0])
    w = np.array([np.nan, np.nan, 1.0])  # the class 0 has samples but no weight — the plain mean, not the middle
    m = ik.class_means(z, w, [0.5])
    assert m[0] == pytest.approx(0.2) and m[1] == 2.0


def test_proportions() -> None:
    z = np.array([0.1, 0.6, 2.0, np.nan])
    w = np.array([1.0, 1.0, 2.0, 9.0])
    np.testing.assert_allclose(ik.proportions(z, w, [0.5, 1.0]), [0.25, 0.5], atol=1e-15)
    np.testing.assert_allclose(ik.proportions(z, None, [0.5, 1.0]), [1 / 3, 2 / 3], atol=1e-15)
    assert np.isnan(ik.proportions(np.array([np.nan]), None, [1.0])).all()


# --- IK = OK of the indicators; the median IK = the same weights ---


def test_one_model_equals_ok_of_indicators_to_the_bit() -> None:
    s = make_samples(150)
    sr, c, nb = block(s)
    cov = cm()
    setup = setup_of((cov, cov, cov))
    res = ik.ik_chunk(nb, s.local, c, setup)
    assert (res.status == 0).sum() > 30
    bv = cv.BlockCov(cov).vv(SIZE, DISC)
    offs = cv.disc_points(SIZE, DISC)
    work = kg.KrigeWork()
    for b in np.flatnonzero(res.status == 0):
        n = int(nb.n[b])
        xyz = s.local[nb.idx[b, :n]]
        zz = nb.z[b, :n]
        for q, t in enumerate(T):
            assert work.run(cov, xyz, (zz <= t).astype(np.float64), c[b], offs, bv, 0.0, 1) == 0
            assert res.raw[b, q] == work.out[kg.O_EST]  # to the bit
    # P = the order relations of the very same numbers
    for b in np.flatnonzero(res.status == 0)[:10]:
        f = res.raw[b].copy()
        ik.order_relations(f)
        np.testing.assert_array_equal(res.p[b], f)
        assert res.etype[b] == ik.etype(f, setup.means)


def test_models_per_threshold_differ_and_each_is_its_own_ok() -> None:
    s = make_samples(120, seed=3)
    sr, c, nb = block(s, 25)
    covs = (cm(SPH), cm(EXP), cm(SPH))
    res = ik.ik_chunk(nb, s.local, c, setup_of(covs))
    work = kg.KrigeWork()
    offs = cv.disc_points(SIZE, DISC)
    for b in np.flatnonzero(res.status == 0)[:12]:
        n = int(nb.n[b])
        for q, t in enumerate(T):
            bv = cv.BlockCov(covs[q]).vv(SIZE, DISC)
            work.run(covs[q], s.local[nb.idx[b, :n]], (nb.z[b, :n] <= t).astype(np.float64), c[b], offs, bv, 0.0, 1)
            assert res.raw[b, q] == work.out[kg.O_EST]
    other = ik.ik_chunk(nb, s.local, c, setup_of((cm(SPH),) * 3))
    assert not np.array_equal(other.raw[:, 1], res.raw[:, 1])  # the model of the threshold matters


def test_median_is_apply_weights_of_the_same_lambdas() -> None:
    s = make_samples(150, seed=4)
    sr, c, nb = block(s)
    cov = cm()
    res = ik.ik_chunk(nb, s.local, c, setup_of((cov,)))
    bv = cv.BlockCov(cov).vv(SIZE, DISC)
    offs = cv.disc_points(SIZE, DISC)
    work = kg.KrigeWork()
    done = np.flatnonzero(res.status == 0)
    assert done.size > 30
    for b in done:
        n = int(nb.n[b])
        zz = nb.z[b, :n]
        assert work.run(cov, s.local[nb.idx[b, :n]], (zz <= T[0]).astype(np.float64), c[b], offs, bv, 0.0, 1) == 0
        assert res.raw[b, 0] == work.out[kg.O_EST]
        for q in (1, 2):
            f = kg.apply_weights(work.sol, n, (zz <= T[q]).astype(np.float64), 0.0, 1)
            assert res.raw[b, q] == f  # one LU, the weights of the first indicator
    # and the "per" with the same model gives the same numbers (the weights are the same)
    per = ik.ik_chunk(nb, s.local, c, setup_of((cov, cov, cov)))
    np.testing.assert_allclose(res.raw[done], per.raw[done], atol=1e-12)


def test_median_makes_one_system_not_k() -> None:
    s = make_samples(150, seed=4)
    sr, c, nb = block(s, 200)
    cov = cm()
    ik.ik_chunk(nb, s.local, c, setup_of((cov,), t=T))  # compiled
    t20 = list(np.linspace(0.2, 6.0, 20))
    means = np.linspace(0.1, 7.0, 21)
    one = ik.IkSetup.of(t20, (cov,), means, size=SIZE, disc=DISC)
    many = ik.IkSetup.of(t20, (cov,) * 20, means, size=SIZE, disc=DISC)
    for st in (one, many):
        ik.ik_chunk(nb, s.local, c, st)
    t0 = time.perf_counter()
    for _ in range(3):
        ik.ik_chunk(nb, s.local, c, one)
    t_one = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(3):
        ik.ik_chunk(nb, s.local, c, many)
    t_many = time.perf_counter() - t0
    assert t_one < t_many / 3.0  # K = 20 systems against one


# --- Т8.8-13: monotone, [0; 1]; the correction really works ---


def test_t88_13_monotone_in_unit_interval_and_raw_needs_it() -> None:
    s = make_samples(200, seed=7)
    sr, c, nb = block(s, 300)
    t = [0.3, 0.6, 1.0, 1.6, 2.5, 4.0]
    means = np.linspace(0.1, 6.0, 7)
    # models with a nugget small enough for negative weights: the raw F leaves [0; 1] and is not ordered
    cov = cm(Model(0.02, (Structure("spherical", 0.98, 40.0, 40.0, 40.0),)))
    for covs in ((cov,), (cov,) * 6):
        res = ik.ik_chunk(nb, s.local, c, ik.IkSetup.of(t, covs, means, size=SIZE, disc=DISC))
        ok = res.status == 0
        assert ok.sum() > 100
        p = res.p[ok]
        assert (p >= 0.0).all() and (p <= 1.0).all()
        assert (np.diff(p, axis=1) >= 0.0).all()
        assert not np.isnan(p).any() and not np.isnan(res.etype[ok]).any()
        r = res.raw[ok]
        assert (r < 0.0).any() or (r > 1.0).any() or (np.diff(r, axis=1) < 0.0).any()  # a test that tests something
        assert np.isnan(res.p[~ok]).all() and np.isnan(res.etype[~ok]).all()


def test_etype_inside_the_range_of_class_means() -> None:
    s = make_samples(150, seed=8)
    sr, c, nb = block(s, 60)
    cov = cm()
    means = ik.class_means(s.z, s.w, T)
    res = ik.ik_chunk(nb, s.local, c, setup_of((cov,), means=means))
    e = res.etype[res.status == 0]
    assert e.size and (e >= means.min() - 1e-12).all() and (e <= means.max() + 1e-12).all()


def test_point_target_at_a_sample_gives_its_indicator() -> None:
    s = make_samples(60, seed=9)
    sr = searcher_of(s)
    c = s.local[:5].copy()
    nb = sr.gather(c, 0)
    exact = cm(Model(0.0, (Structure("spherical", 1.0, 30.0, 30.0, 30.0),)))
    res = ik.ik_chunk(nb, s.local, c, ik.IkSetup.of(T, (exact,) * 3, [0.2, 0.7, 1.6, 4.0]))
    assert (res.status == 0).all()
    np.testing.assert_allclose(res.raw, ik.indicators(s.z[:5], T), atol=1e-9)  # an exact interpolator, no nugget


# --- gaps, a degenerate system, the search did not hold ---


def _nb(local, rows, zs, n_max=6):
    nb = se.Neighbours.empty(len(rows), n_max)
    for i, (idx, z) in enumerate(zip(rows, zs, strict=True)):
        nb.idx[i, : len(idx)] = idx
        nb.z[i, : len(z)] = z
        nb.n[i] = len(idx)
        nb.ok[i] = True
    return nb


def test_gaps_do_not_take_part_and_are_not_zero() -> None:
    rng = np.random.default_rng(11)
    local = rng.uniform(0, 20, (8, 3))
    cov = cm()
    setup = setup_of((cov, cov, cov))
    c = np.array([[10.0, 10.0, 5.0]])
    with_gap = _nb(local, [[0, 1, 2, 3, 4, 5]], [[0.2, 0.9, np.nan, 3.0, 0.4, 2.0]])
    without = _nb(local, [[0, 1, 3, 4, 5]], [[0.2, 0.9, 3.0, 0.4, 2.0]])
    a = ik.ik_chunk(with_gap, local, c, setup)
    b = ik.ik_chunk(without, local, c, setup)
    assert a.status[0] == 0
    np.testing.assert_array_equal(a.p, b.p)  # the gap is dropped, to the bit
    zero = _nb(local, [[0, 1, 2, 3, 4, 5]], [[0.2, 0.9, 0.0, 3.0, 0.4, 2.0]])
    assert not np.array_equal(ik.ik_chunk(zero, local, c, setup).p, a.p)  # …and is not a zero sample
    allgap = _nb(local, [[0, 1, 2]], [[np.nan, np.nan, np.nan]])
    r = ik.ik_chunk(allgap, local, c, setup)
    assert r.status[0] == 2 and np.isnan(r.p).all() and np.isnan(r.etype).all()


def test_degenerate_system_status_3_and_few_status_2() -> None:
    local = np.array([[1.0, 1.0, 1.0], [1.0, 1.0, 1.0], [5.0, 5.0, 5.0]])  # two samples at one point
    cov = cm(Model(0.0, (Structure("spherical", 1.0, 30.0, 30.0, 30.0),)))  # no nugget: the system is singular
    setup = ik.IkSetup.of(T, (cov,), [0.2, 0.7, 1.6, 4.0], size=None)
    nb = _nb(local, [[0, 1, 2], [0, 1, 2], [0, 1, 2]], [[0.2, 0.3, 2.0]] * 3)
    nb.ok[1] = False  # the search did not hold
    c = np.array([[2.0, 2.0, 2.0], [2.0, 2.0, 2.0], [np.nan, 0.0, 0.0]])
    res = ik.ik_chunk(nb, local, c, setup)
    assert res.status.tolist() == [3, 2, 2]
    assert np.isnan(res.p).all() and np.isnan(res.etype).all() and np.isnan(res.raw).all()
    per = ik.ik_chunk(nb, local, c, ik.IkSetup.of(T, (cov,) * 3, [0.2, 0.7, 1.6, 4.0], size=None))
    assert per.status.tolist() == [3, 2, 2]  # the first singular threshold is final, the others are skipped


def test_setup_errors() -> None:
    cov = cm()
    with pytest.raises(ApiError):
        ik.IkSetup.of([1.0, 1.0], (cov,), [0.0, 1.0, 2.0])  # not strictly ascending
    with pytest.raises(ApiError):
        ik.IkSetup.of([1.0, 2.0], (cov, cov, cov), [0.0, 1.0, 2.0])  # 3 models for 2 thresholds
    with pytest.raises(ApiError):
        ik.IkSetup.of([1.0, 2.0], (cov,), [0.0, 1.0])  # the means of the classes: K + 1
    with pytest.raises(ApiError):
        ik.IkSetup.of(list(range(1, 22)), (cov,), np.arange(22.0))  # more than 20


# --- the passes ---


def test_passes_fall_through_and_status() -> None:
    s = make_samples(80, seed=12, extent=60.0)
    passes = [Pass(1.0, n_min=6, n_max=12), Pass(3.0, n_min=3, n_max=16)]
    sr = searcher_of(s, passes, r=14.0)
    c = np.vstack([centres_of(60, seed=13), [[300.0, 300.0, 5.0]]])  # the last one is far from everything
    cov = cm()
    setup = setup_of((cov,))
    run = ik.ik_passes(sr, s.local, c, setup)
    assert set(np.unique(run.pass_)) >= {0, 1, 2}
    assert run.status[-1] == 2 and run.pass_[-1] == 0 and np.isnan(run.p[-1]).all()
    ok = run.status == 0
    assert ((run.pass_ > 0) == ok).all()
    assert (run.nb.n[ok] >= 3).all()
    # a block of the pass 2 equals the chunk of the pass 2
    b = int(np.flatnonzero(run.pass_ == 2)[0])
    nb2 = sr.gather(c[b : b + 1], 1)
    one = ik.ik_chunk(nb2, s.local, c[b : b + 1], setup)
    np.testing.assert_array_equal(run.p[b], one.p[0])
    assert run.etype[b] == one.etype[0]
    # `start` skips the passes
    late = ik.ik_passes(sr, s.local, c, setup, start=1)
    assert (late.pass_[late.status == 0] == 2).all()


def test_degenerate_pass_goes_on_to_the_next() -> None:
    # two coincident samples and no nugget: the system is singular in every pass — STATUS 3, not 2
    xyz = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [5.0, 5.0, 1.0]])
    z = np.array([0.2, 0.9, 2.0])
    s0 = prepare(xyz, z, None, None, np.array([0, 1, 2], np.int32), ["a", "b", "c"], np.zeros(3, np.int16),
                 np.arange(3, dtype=np.int64), np.zeros((3, 2)), origin=np.zeros(3))  # fmt: skip
    pts = s0.local.copy()
    pts[0] = pts[1]  # `prepare` averages the coincident ones; here they are made coincident after it
    s = replace(s0, xyz=pts, local=pts)
    sr = searcher_of(s, [Pass(1.0, n_min=1, n_max=3), Pass(3.0, n_min=1, n_max=3)], r=10.0)
    cov = cm(Model(0.0, (Structure("spherical", 1.0, 30.0, 30.0, 30.0),)))
    setup = ik.IkSetup.of(T, (cov,), [0.2, 0.7, 1.6, 4.0], size=None)
    run = ik.ik_passes(sr, s.local, np.array([[2.0, 2.0, 1.0]]), setup)
    assert run.status.tolist() == [3] and run.pass_.tolist() == [0]


# --- the threads, the warm-up, the speed ---


@pytest.mark.parametrize("covs_n", [1, 3])
def test_threads_1_2_4_bitwise(covs_n: int) -> None:
    s = make_samples(300, seed=21)
    sr = searcher_of(s, [Pass(1.0, n_min=3, n_max=20), Pass(2.0, n_min=3, n_max=20)])
    c = centres_of(1500, seed=22)
    cov = cm()
    setup = setup_of((cov,) * covs_n)
    whole = ik.ik_passes(sr, s.local, c, setup)
    assert (whole.status == 0).sum() > 1000
    for workers in (1, 2, 4):
        parts = np.array_split(np.arange(c.shape[0]), workers * 3)
        with ThreadPoolExecutor(workers) as ex:
            got = list(ex.map(lambda r: ik.ik_passes(sr, s.local, c[r], setup), parts))
        p = np.vstack([g.p for g in got])
        et = np.concatenate([g.etype for g in got])
        st = np.concatenate([g.status for g in got])
        np.testing.assert_array_equal(p, whole.p)  # NaN == NaN here: array_equal on the same NaN pattern
        np.testing.assert_array_equal(et, whole.etype)
        np.testing.assert_array_equal(st, whole.status)


def test_warm_one_signature_for_writable_and_read_only() -> None:
    ik.warm_indicator()
    n0 = len(ik._ik_kernel.signatures)
    s = make_samples(100, seed=31)
    sr, c, nb = block(s, 20)
    cov = cm()
    setup = setup_of((cov, cov, cov))
    ik.ik_chunk(nb, s.local, c, setup)
    ro = [np.array(a) for a in (s.local, c)]
    for a in ro:
        a.setflags(write=False)
    ik.ik_chunk(nb, ro[0], ro[1], setup)
    frozen = ik.IkSetup(setup.thresholds, setup.covs, setup.bv, setup.offs, setup.means)
    frozen.offs.setflags(write=False)
    ik.ik_chunk(nb, s.local, c, frozen)
    pts = cv.BlockCov(cov).points(SIZE, DISC)  # read-only, as BlockCov gives
    assert not pts.flags.writeable
    ik.ik_chunk(nb, s.local, c, ik.IkSetup(setup.thresholds, setup.covs, setup.bv, pts, setup.means))
    assert len(ik._ik_kernel.signatures) == n0 == 1


def test_speed_us_per_block(capsys: pytest.CaptureFixture[str]) -> None:
    s = make_samples(2000, seed=41, extent=100.0)
    sr = searcher_of(s, [Pass(1.0, n_min=4, n_max=24)], r=30.0)
    c = centres_of(4000, seed=42, extent=100.0)
    nb = sr.gather(c, 0)
    cov = cm()
    t6 = list(np.linspace(0.3, 4.0, 6))
    m7 = np.linspace(0.1, 6.0, 7)
    out = {}
    for name, covs in (("median", (cov,)), ("per", (cov,) * 6)):
        setup = ik.IkSetup.of(t6, covs, m7, size=SIZE, disc=(4, 4, 2))
        ik.ik_chunk(nb, s.local, c, setup)
        t0 = time.perf_counter()
        res = ik.ik_chunk(nb, s.local, c, setup)
        out[name] = (time.perf_counter() - t0) / c.shape[0] * 1e6
        assert (res.status == 0).mean() > 0.9
    with capsys.disabled():
        print(f"\nIK K=6, n<=24, 4x4x2 disc: median {out['median']:.1f} us/block, per {out['per']:.1f} us/block")
    assert out["median"] < 2000.0
