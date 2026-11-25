"""Kriging kernel (ТЗ 8.8.2; ADR 0024 §5; Э18, 18.4): the etalon Э-ОК-1 (Т8.8-1, -2, -3), Т8.8-8, Т8.8-10, the
comparison with `numpy.linalg.solve` on a system made from `Model.cov`, the degenerate system (Д-27), NaN, the
buffers, read-only inputs."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.geostat.estimate import cov as cv
from bf_core.geostat.estimate import krige as kg
from bf_core.geostat.variogram.model import Model, Structure

SPH = Model(0.1, (Structure("spherical", 0.9, 30.0, 30.0, 30.0),))  # Э-ОК-1
ETALON_XYZ = np.array([[0.0, 10.0, 0.0], [10.0, 0.0, 0.0], [-6.0, -8.0, 0.0], [20.0, 20.0, 0.0]])
ETALON_Z = np.array([1.0, 2.0, 3.0, 4.0])
ORIGIN = np.zeros(3)
POINT = np.zeros((1, 3))


def run(
    model: Model,
    xyz: np.ndarray,
    z: np.ndarray,
    centre: np.ndarray,
    *,
    ok: int,
    mean: float = 0.0,
    block: tuple[tuple[float, float, float], tuple[int, int, int], float] | None = None,
    work: kg.KrigeWork | None = None,
) -> tuple[int, kg.KrigeWork, np.ndarray]:
    c = cv.cov_model(model)
    if block is None:
        offs, bv = POINT, c.sill
    else:
        offs = cv.disc_points(*block)
        bv = cv.BlockCov(c).vv(*block)
    w = work or kg.KrigeWork()
    status = w.run(c, xyz, z, centre, offs, bv, mean, ok)
    return status, w, offs


def reference(
    model: Model,
    xyz: np.ndarray,
    z: np.ndarray,
    centre: np.ndarray,
    offs: np.ndarray,
    bv: float,
    mean: float,
    ok: int,
) -> dict[str, float | np.ndarray]:
    """SK / OK by `numpy.linalg.solve` on a matrix made from `Model.cov` (and the means of `cov_rhs`)."""
    n = len(z)
    big = model.cov(xyz[:, None, :] - xyz[None, :, :])
    c = cv.cov_model(model)
    rhs = np.empty(n)
    cv.cov_rhs(*c.args, xyz, centre, offs, rhs)
    if ok:
        a = np.zeros((n + 1, n + 1))
        a[:n, :n] = big
        a[:n, n] = a[n, :n] = 1.0
        sol = np.linalg.solve(a, np.append(rhs, 1.0))
        lam, mu = sol[:n], float(sol[n])
        est = float(lam @ z)
        kv = bv - float(lam @ rhs) - mu
        den = float(lam @ rhs) - mu
        slope = float(lam @ rhs) / den
    else:
        lam, mu = np.linalg.solve(big, rhs), math.nan
        est = mean + float(lam @ (z - mean))
        kv = bv - float(lam @ rhs)
        slope = 1.0
    return {
        "lam": lam,
        "mu": mu,
        "est": est,
        "kv": kv,
        "slope": slope,
        "ke": (bv - kv) / bv,
        "snw": float(lam[lam < 0].sum()),
    }


# --- Э-ОК-1 ----------------------------------------------------------------------------------------------------------


def test_t881_ok_point() -> None:
    status, w, _ = run(SPH, ETALON_XYZ, ETALON_Z, ORIGIN, ok=1)
    assert status == kg.KR_OK
    np.testing.assert_allclose(w.lam(4), [0.332890, 0.316318, 0.375815, -0.025022], atol=1e-6)
    assert w.mu(4) == pytest.approx(-0.022653, abs=1e-6)
    o = w.out
    assert o[kg.O_LAGR] == pytest.approx(-0.022653, abs=1e-6)
    assert o[kg.O_EST] == pytest.approx(1.992880, abs=1e-6)
    assert o[kg.O_KV] == pytest.approx(0.544418, abs=1e-6)
    assert o[kg.O_SLOPE] == pytest.approx(0.954774, abs=1e-6)
    assert o[kg.O_KE] == pytest.approx(0.455582, abs=1e-6)
    assert o[kg.O_SNW] == pytest.approx(-0.025022, abs=1e-6)  # the one negative weight, signed
    assert 0.0 < o[kg.O_PIV] < 1.0


def test_t882_sk_point() -> None:
    status, w, _ = run(SPH, ETALON_XYZ, ETALON_Z, ORIGIN, ok=0, mean=2.5)
    assert status == kg.KR_OK
    np.testing.assert_allclose(w.lam(4), [0.318855, 0.303071, 0.357930, -0.045490], atol=1e-6)
    assert w.out[kg.O_EST] == pytest.approx(1.980912, abs=1e-6)
    assert w.out[kg.O_KV] == pytest.approx(0.542931, abs=1e-6)
    assert w.out[kg.O_SLOPE] == 1.0
    assert math.isnan(w.out[kg.O_LAGR])  # SK has no multiplier — a gap, not a zero
    assert w.out[kg.O_SNW] == pytest.approx(-0.045490, abs=1e-6)


def test_t883_ok_block() -> None:
    block = ((10.0, 10.0, 0.0), (4, 4, 1), 0.0)
    status, w, offs = run(SPH, ETALON_XYZ, ETALON_Z, ORIGIN, ok=1, block=block)
    assert status == kg.KR_OK
    assert cv.BlockCov(cv.cov_model(SPH)).vv(*block) == pytest.approx(0.677710, abs=1e-6)
    assert w.out[kg.O_EST] == pytest.approx(2.024652, abs=1e-6)
    assert w.out[kg.O_KV] == pytest.approx(0.246936, abs=1e-6)
    assert w.out[kg.O_KE] == pytest.approx((0.677710 - 0.246936) / 0.677710, abs=1e-6)
    assert len(offs) == 16


# --- Т8.8-8, Т8.8-10 -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("target", [0, 3, 7])
def test_t888_no_nugget_at_a_sample_is_exact(target: int) -> None:
    m = Model(
        0.0,
        (
            Structure("spherical", 0.6, 25.0, 15.0, 8.0, 30.0, 10.0, 5.0),
            Structure("exponential", 0.4, 40.0, 30.0, 20.0),
        ),
    )
    rng = np.random.default_rng(10 + target)
    xyz = rng.uniform(-30.0, 30.0, size=(12, 3))
    z = rng.normal(5.0, 2.0, size=12)
    status, w, _ = run(m, xyz, z, xyz[target].copy(), ok=1)
    assert status == kg.KR_OK
    assert abs(w.out[kg.O_EST] - z[target]) <= 1e-9 * np.abs(z).max()
    assert w.out[kg.O_KV] <= 1e-9 * m.sill
    assert w.out[kg.O_SLOPE] == pytest.approx(1.0, abs=1e-9)
    assert w.out[kg.O_KE] == pytest.approx(1.0, abs=1e-9)
    assert w.lam(12)[target] == pytest.approx(1.0, abs=1e-9)


def test_t8810_sk_samples_beyond_the_range() -> None:
    xyz = np.array([[100.0, 0.0, 0.0], [0.0, 120.0, 5.0], [-90.0, -90.0, 0.0], [95.0, 5.0, 0.0]])  # all beyond 30 m
    z = np.array([4.0, 5.0, 6.0, 7.0])
    status, w, _ = run(SPH, xyz, z, ORIGIN, ok=0, mean=2.5)
    assert status == kg.KR_OK
    assert w.out[kg.O_EST] == 2.5  # z* = m
    assert w.out[kg.O_KV] == pytest.approx(SPH.sill, abs=1e-15)  # KV = C(0)
    assert w.out[kg.O_KE] == pytest.approx(0.0, abs=1e-15)
    np.testing.assert_allclose(w.lam(4), 0.0, atol=1e-15)


@pytest.mark.parametrize("n", [1, 2, 7, 40])
def test_t8810_pure_nugget_ok_weights_are_equal(n: int) -> None:
    m = Model(0.7, ())
    rng = np.random.default_rng(n)
    xyz = rng.uniform(-50.0, 50.0, size=(n, 3))
    z = rng.normal(size=n)
    status, w, _ = run(m, xyz, z, ORIGIN, ok=1)
    assert status == kg.KR_OK
    np.testing.assert_allclose(w.lam(n), 1.0 / n, atol=1e-12)
    assert w.out[kg.O_EST] == pytest.approx(z.mean(), abs=1e-12)
    assert w.out[kg.O_KV] == pytest.approx(0.7 + 0.7 / n, abs=1e-12)  # C(0) − μ with μ = −C(0)/n and c̄ = 0


# --- against numpy.linalg.solve --------------------------------------------------------------------------------------

MODELS = {
    "spherical": SPH,
    "aniso_3_angles": Model(0.3, (Structure("spherical", 0.7, 50.0, 25.0, 10.0, 35.0, -20.0, 15.0),)),
    "three_structures": Model(
        0.1,
        (
            Structure("exponential", 0.3, 15.0, 10.0, 5.0, 20.0, 10.0, -30.0),
            Structure("spherical", 0.4, 40.0, 30.0, 8.0, 20.0, 10.0, -30.0),
            Structure("gaussian", 0.5, 90.0, 60.0, 30.0, 20.0, 10.0, -30.0),
        ),
    ),
    "no_nugget": Model(0.0, (Structure("exponential", 1.0, 30.0, 20.0, 12.0, 40.0, 0.0, 0.0),)),
}


@pytest.mark.parametrize("name", list(MODELS))
@pytest.mark.parametrize("ok", [1, 0])
@pytest.mark.parametrize("n", [1, 3, 24, 64, 128])
@pytest.mark.parametrize("block", [None, ((10.0, 10.0, 5.0), (3, 3, 2), 25.0)])
def test_against_numpy_solve(name: str, ok: int, n: int, block: tuple | None) -> None:
    m = MODELS[name]
    rng = np.random.default_rng(1000 + n)
    # well spread samples; the minimum distance keeps the system far from degenerate
    xyz = rng.uniform(-60.0, 60.0, size=(n, 3)) * np.array([1.0, 1.0, 0.5]) + np.array([1.0e5, -2.0e5, 300.0])
    z = rng.lognormal(0.5, 0.8, size=n)
    centre = xyz.mean(axis=0) + np.array([2.0, -1.0, 0.5])
    status, w, offs = run(m, xyz, z, centre, ok=ok, mean=1.7, block=block)
    bv = m.sill if block is None else cv.BlockCov(cv.cov_model(m)).vv(*block)
    ref = reference(m, xyz, z, centre, offs, bv, 1.7, ok)
    assert status == kg.KR_OK
    # conditioning grows with n; the errors stay far below the tolerances of the etalons
    np.testing.assert_allclose(w.lam(n), ref["lam"], rtol=1e-6, atol=1e-8)
    o = w.out
    assert o[kg.O_EST] == pytest.approx(ref["est"], rel=1e-8, abs=1e-8)
    assert o[kg.O_KV] == pytest.approx(ref["kv"], rel=1e-6, abs=1e-8)
    assert o[kg.O_KE] == pytest.approx(ref["ke"], rel=1e-6, abs=1e-8)
    assert o[kg.O_SLOPE] == pytest.approx(ref["slope"], rel=1e-6, abs=1e-8)
    assert o[kg.O_SNW] == pytest.approx(ref["snw"], rel=1e-5, abs=1e-8)
    if ok:
        assert w.mu(n) == pytest.approx(ref["mu"], rel=1e-6, abs=1e-8)
        assert o[kg.O_LAGR] == w.mu(n)
        assert w.lam(n).sum() == pytest.approx(1.0, abs=1e-9)  # the unbiasedness condition
    else:
        assert math.isnan(o[kg.O_LAGR])


def test_apply_weights_gives_the_estimate_of_other_values() -> None:
    xyz = np.random.default_rng(4).uniform(-20.0, 20.0, size=(10, 3))
    z, z2 = np.random.default_rng(5).normal(size=(2, 10))
    for ok in (1, 0):
        _, w, _ = run(SPH, xyz, z, ORIGIN, ok=ok, mean=0.3)
        est2 = kg.apply_weights(w.sol, 10, z2, 0.3, ok)
        _, w2, _ = run(SPH, xyz, z2, ORIGIN, ok=ok, mean=0.3)
        assert est2 == w2.out[kg.O_EST]  # the same bits: the same weights, the same order
        assert kg.apply_weights(w.sol, 10, z, 0.3, ok) == w.out[kg.O_EST]


# --- degenerate systems, NaN, sizes ----------------------------------------------------------------------------------


@pytest.mark.parametrize("ok", [1, 0])
def test_two_samples_in_one_point_without_nugget_are_flagged(ok: int) -> None:
    m = Model(0.0, (Structure("spherical", 1.0, 30.0, 30.0, 30.0),))
    xyz = np.array([[5.0, 5.0, 0.0], [5.0, 5.0, 0.0], [12.0, 3.0, 0.0]])
    z = np.array([1.0, 2.0, 3.0])
    w = kg.KrigeWork()
    w.out[:] = 7.0
    w.sol[:] = 7.0
    status, w, _ = run(m, xyz, z, ORIGIN, ok=ok, mean=1.0, work=w)
    assert status == kg.KR_SINGULAR  # a flag, not an exception (Д-27: STATUS 3)
    assert np.isnan(w.out[: kg.O_PIV]).all()  # EST … SNW are NaN, not zero
    assert np.isnan(w.sol[: 3 + ok]).all()
    # the same samples without the duplicate: estimated
    status2, w2, _ = run(m, xyz[1:], z[1:], ORIGIN, ok=ok, mean=1.0)
    assert status2 == kg.KR_OK and np.isfinite(w2.out[[kg.O_EST, kg.O_KV, kg.O_SLOPE, kg.O_KE, kg.O_SNW]]).all()


def test_two_samples_in_one_point_with_nugget_are_flagged_too() -> None:
    """C(0) is the sill on the off-diagonal of coincident samples, the nugget included: the rows are equal."""
    xyz = np.array([[5.0, 5.0, 0.0], [5.0, 5.0, 0.0], [12.0, 3.0, 0.0]])
    status, _, _ = run(SPH, xyz, np.array([1.0, 2.0, 3.0]), ORIGIN, ok=1)
    assert status == kg.KR_SINGULAR


def test_near_duplicate_below_the_pivot_threshold_is_flagged() -> None:
    """Samples 1e−13 m apart: the second pivot is ≈ 1e−13·C(0) < 1e−12·C(0) — degenerate by the rule of 8.8.2; at 1e−9 m
    the pivot is ≈ 1e−10·C(0) and the system is solved (ill-conditioned but above the threshold)."""
    m = Model(0.0, (Structure("spherical", 1.0, 30.0, 30.0, 30.0),))
    z, centre = np.array([1.0, 2.0, 3.0]), np.array([10.0, 0.0, 0.0])
    near = np.array([[0.0, 0.0, 0.0], [1e-13, 0.0, 0.0], [100.0, 0.0, 0.0]])
    assert run(m, near, z, centre, ok=0)[0] == kg.KR_SINGULAR
    near[1, 0] = 1e-9
    assert run(m, near, z, centre, ok=0)[0] == kg.KR_OK


def test_a_zero_sill_model_is_degenerate_not_a_division_by_zero() -> None:
    status, w, _ = run(Model(0.0, ()), ETALON_XYZ, ETALON_Z, ORIGIN, ok=0, mean=1.0)
    assert status == kg.KR_SINGULAR and np.isnan(w.out[: kg.O_PIV]).all()


def test_nan_is_a_nan_not_a_zero() -> None:
    w = kg.KrigeWork()
    for what in ("z", "xyz", "centre", "mean", "bv"):
        xyz, z, centre = ETALON_XYZ.copy(), ETALON_Z.copy(), ORIGIN.copy()
        mean, bv = 2.5, SPH.sill
        if what == "z":
            z[2] = math.nan
        elif what == "xyz":
            xyz[1, 0] = math.nan
        elif what == "centre":
            centre[1] = math.nan
        elif what == "mean":
            mean = math.nan
        else:
            bv = math.nan
        status = w.run(cv.cov_model(SPH), xyz, z, centre, POINT, bv, mean, 0)
        assert status == kg.KR_NAN, what
        assert np.isnan(w.out[: kg.O_PIV]).all(), what
    # a NaN mean does not matter for OK
    assert w.run(cv.cov_model(SPH), ETALON_XYZ, ETALON_Z, ORIGIN, POINT, SPH.sill, math.nan, 1) == kg.KR_OK


def test_sizes_and_arguments() -> None:
    c = cv.cov_model(SPH)
    w = kg.KrigeWork(5)
    assert w.run(c, ETALON_XYZ, ETALON_Z, ORIGIN, POINT, SPH.sill, 0.0, 1) == kg.KR_OK  # n + 1 = 5 fits
    assert w.run(c, ETALON_XYZ, ETALON_Z, ORIGIN, POINT, SPH.sill, 0.0, 1, n=0) == kg.KR_SIZE
    assert w.run(c, ETALON_XYZ, ETALON_Z, ORIGIN, POINT, SPH.sill, 0.0, 2) == kg.KR_SIZE  # ok is 0 or 1
    assert w.run(c, ETALON_XYZ, ETALON_Z, ORIGIN, POINT, SPH.sill, 0.0, 1, n=5) == kg.KR_SIZE  # more than given
    assert np.isnan(w.out[: kg.O_PIV]).all()
    big = np.random.default_rng(0).uniform(0, 500, size=(130, 3))
    assert kg.KrigeWork().run(c, big, np.ones(130), ORIGIN, POINT, SPH.sill, 0.0, 1) == kg.KR_SIZE  # 131 > 129
    st, _, _ = run(SPH, big[:128], np.ones(128), np.array([250.0, 250.0, 250.0]), ok=1)
    assert st in (kg.KR_OK, kg.KR_SINGULAR)  # 128 samples + the row of OK = 129 fit


# --- determinism, buffers, signatures --------------------------------------------------------------------------------


def test_the_answer_does_not_depend_on_the_buffers() -> None:
    xyz = np.random.default_rng(8).uniform(-40.0, 40.0, size=(30, 3))
    z = np.random.default_rng(9).normal(size=30)
    block = ((8.0, 8.0, 4.0), (2, 2, 2), 15.0)
    _, clean, _ = run(MODELS["aniso_3_angles"], xyz, z, ORIGIN, ok=1, block=block)
    dirty = kg.KrigeWork()
    dirty.a[:] = 1e300
    dirty.rhs[:] = -5.0
    dirty.sol[:] = math.nan
    dirty.out[:] = 3.0
    _, dirty, _ = run(MODELS["aniso_3_angles"], xyz, z, ORIGIN, ok=1, block=block, work=dirty)
    assert np.array_equal(clean.out, dirty.out, equal_nan=True)
    assert np.array_equal(clean.sol[:31], dirty.sol[:31])
    # a smaller system after a larger one: nothing of the old matrix leaks in
    _, small, _ = run(SPH, ETALON_XYZ, ETALON_Z, ORIGIN, ok=1, work=dirty)
    _, fresh, _ = run(SPH, ETALON_XYZ, ETALON_Z, ORIGIN, ok=1)
    assert np.array_equal(small.out, fresh.out, equal_nan=True)


def test_read_only_inputs_do_not_change_the_answer_and_are_warmed() -> None:
    kg.warm_krige()
    before = len(kg.krige.signatures)
    assert before == 4
    c = cv.cov_model(SPH)
    frozen = cv.CovModel(*(_ro(a) for a in c.args))
    block = cv.disc_points((10.0, 10.0, 0.0), (4, 4, 1))
    ref = kg.KrigeWork()
    for ok in (1, 0):
        for offs in (block, POINT):
            assert ref.run(c, ETALON_XYZ, ETALON_Z, ORIGIN, offs, 0.7, 2.5, ok) == kg.KR_OK
            combos = (  # model, samples, target, points
                (frozen, _ro(ETALON_XYZ), _ro(ETALON_Z), _ro(ORIGIN), _ro(offs)),
                (c, ETALON_XYZ, ETALON_Z, ORIGIN, _ro(offs)),  # BlockCov.points are read-only
                (c, _ro(ETALON_XYZ), _ro(ETALON_Z), ORIGIN, offs),  # the cached columns of a source
            )
            for model, x, z, centre, o in combos:
                w = kg.KrigeWork()
                assert w.run(model, x, z, centre, o, 0.7, 2.5, ok) == kg.KR_OK
                assert np.array_equal(w.out, ref.out, equal_nan=True)
    assert len(kg.krige.signatures) == before  # no new specialization after the warm-up


def _ro(a: np.ndarray) -> np.ndarray:
    out = np.array(a)
    out.setflags(write=False)
    return out


def test_system_of_24_runs_in_microseconds() -> None:
    """Not a gate (the measure is in the report): a point and a block of 4×4×2, n = 24, must stay in the tens of µs."""
    import time

    kg.warm_krige()
    rng = np.random.default_rng(1)
    xyz = rng.uniform(-40.0, 40.0, size=(24, 3))
    z = rng.normal(size=24)
    c = cv.cov_model(MODELS["aniso_3_angles"])
    w = kg.KrigeWork()
    for offs, bv in ((POINT, c.sill), (cv.disc_points((10.0, 10.0, 5.0), (4, 4, 2)), 0.5)):
        w.run(c, xyz, z, ORIGIN, offs, bv, 0.0, 1)
        t0 = time.perf_counter()
        for _ in range(200):
            w.run(c, xyz, z, ORIGIN, offs, bv, 0.0, 1)
        per = (time.perf_counter() - t0) / 200
        assert per < 5e-3  # a loose guard against an accidental object-mode run
