"""Covariance of the estimation (ТЗ 8.8.2; ADR 0024 §5; Э18, 18.3): C against `Model.cov` (1e−15), C*, the means
C̄_iV and C̄_VV (Т8.8-3), the discretization of a block with the turn R_z(φ), the cache of C̄_VV, writable and
read-only input arrays (the lesson of 17.39б)."""

from __future__ import annotations

import itertools
import math
import threading

import numpy as np
import pytest

from bf_core.geostat.estimate import cov as cv
from bf_core.geostat.variogram.model import Model, Structure, reduced_distance, shape
from bf_core.stats.weighted import StatsError

TOL = 1e-15


def frozen(a: np.ndarray) -> np.ndarray:
    out = np.array(a)
    out.setflags(write=False)
    return out


def lags(seed: int = 3, n: int = 400, scale: float = 25.0) -> np.ndarray:
    """Lags inside, near and beyond the ranges, with the zero lag and tiny ones."""
    d = np.random.default_rng(seed).normal(size=(n, 3)) * scale
    d[0] = 0.0
    d[1] = (1e-9, 0.0, 0.0)
    d[2] = (0.0, 0.0, -30.0)
    d[3] = (-1e-12, 2e-12, 0.0)
    return d


def cov_of(model: Model, d: np.ndarray) -> np.ndarray:
    c = cv.cov_model(model)
    return np.array([cv.cov_c(*c.args, *row) for row in d])


MODELS = {
    "spherical": Model(0.1, (Structure("spherical", 0.9, 30.0, 30.0, 30.0),)),
    "exponential": Model(0.2, (Structure("exponential", 0.8, 40.0, 40.0, 40.0),)),
    "gaussian": Model(0.05, (Structure("gaussian", 1.2, 25.0, 25.0, 25.0),)),
    "no_nugget": Model(0.0, (Structure("spherical", 1.0, 20.0, 12.0, 6.0, 40.0, 0.0, 0.0),)),
    "aniso_3_angles": Model(0.3, (Structure("spherical", 0.7, 50.0, 25.0, 10.0, 35.0, -20.0, 15.0),)),
    "three_structures": Model(
        0.1,
        (
            Structure("exponential", 0.3, 15.0, 10.0, 5.0, 20.0, 10.0, -30.0),
            Structure("spherical", 0.4, 40.0, 30.0, 8.0, 20.0, 10.0, -30.0),
            Structure("gaussian", 0.5, 90.0, 60.0, 30.0, 20.0, 10.0, -30.0),
        ),
    ),
    "own_axes": Model(
        0.15,
        (
            Structure("spherical", 0.5, 30.0, 10.0, 10.0, 90.0, 0.0, 0.0),
            Structure("exponential", 0.5, 60.0, 20.0, 5.0, 0.0, -45.0, 60.0),
        ),
        common_axes=False,
    ),
    "zero_contribution": Model(
        0.4, (Structure("spherical", 0.0, 10.0, 10.0, 10.0), Structure("gaussian", 0.6, 7.0, 7.0, 7.0))
    ),
    "nugget_only": Model(0.7, ()),
    "a2_over_a1": Model(0.1, (Structure("spherical", 0.9, 10.0, 40.0, 20.0, 15.0, 5.0, 0.0),)),
}


# --- C = Model.cov ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", MODELS)
def test_c_equals_model_cov(name: str) -> None:
    m = MODELS[name]
    d = lags()
    err = np.abs(cov_of(m, d) - m.cov(d)).max()
    assert err <= TOL, err


@pytest.mark.parametrize("name", ["spherical", "no_nugget", "aniso_3_angles", "own_axes", "a2_over_a1"])
def test_c_spherical_is_bit_for_bit_the_model(name: str) -> None:
    """The same operations in the same order — the early exit beyond the range changes no bit."""
    m = MODELS[name]
    d = lags(seed=11, n=2000, scale=40.0)
    got = cov_of(m, d)
    if any(s.type != "spherical" for s in m.structures):
        assert np.abs(got - m.cov(d)).max() <= TOL  # the exponential: libm against numpy, 1 ulp at most
    else:
        assert np.array_equal(got, m.cov(d))


def test_c_zero_lag_is_sill_and_nan_stays_nan() -> None:
    m = MODELS["aniso_3_angles"]
    c = cv.cov_model(m)
    assert cv.cov_c(*c.args, 0.0, 0.0, 0.0) == m.sill == c.sill
    for d in ((math.nan, 0.0, 0.0), (1.0, math.nan, 2.0), (0.0, 0.0, math.nan)):
        assert math.isnan(cv.cov_c(*c.args, *d))
        assert math.isnan(cv.cov_cstar(*c.args, *d))
        assert np.isnan(m.cov(np.array(d)))  # as the model does


def test_c_beyond_range_is_zero_for_spherical() -> None:
    m = MODELS["spherical"]
    c = cv.cov_model(m)
    assert cv.cov_c(*c.args, 30.0, 0.0, 0.0) == pytest.approx(0.0, abs=TOL)
    assert cv.cov_c(*c.args, 0.0, 31.0, 0.0) == pytest.approx(0.0, abs=TOL)


def test_cov_model_arrays() -> None:
    m = MODELS["own_axes"]
    c = cv.cov_model(m)
    assert c.kinds.dtype == np.int64 and c.pars.dtype == np.float64 and c.head.dtype == np.float64
    assert c.kinds.tolist() == [0, 1] and c.pars.shape == (2, 13)
    assert c.pars[1, 0] == 0.5 and c.pars[1, 1:4].tolist() == [60.0, 20.0, 5.0]
    np.testing.assert_array_equal(c.pars[1, 4:13].reshape(3, 3), m.structures[1].axes)
    assert c.head.tolist() == [0.15, m.sill, 1.0]
    assert cv.cov_model(Model(0.7, ())).kinds.shape == (0,)
    assert c.key == cv.cov_model(m).key != cv.cov_model(MODELS["spherical"]).key


def test_cov_matrix_symmetric_and_equals_model() -> None:
    m = MODELS["three_structures"]
    xyz = np.random.default_rng(5).uniform(-40.0, 40.0, size=(24, 3))
    xyz[7] = xyz[3]  # the same place twice: C(0) = the sill on the off-diagonal
    c = cv.cov_model(m)
    out = np.empty((24, 24))
    cv.cov_matrix(*c.args, xyz, out)
    ref = m.cov(xyz[:, None, :] - xyz[None, :, :])
    assert np.abs(out - ref).max() <= TOL
    assert np.array_equal(out, out.T)
    assert out[3, 7] == m.sill


# --- C* and the means ------------------------------------------------------------------------------------------------


def cstar_ref(m: Model, delta: np.ndarray) -> float:
    """C* by the definition: Σ c_s·(1 − g_s); at zero Σ c_s (the model is read through its public functions)."""
    if not delta.any():
        return sum(s.c for s in m.structures)
    return float(sum(s.c * (1.0 - shape(s.type, reduced_distance(s, delta))) for s in m.structures))


@pytest.mark.parametrize("name", ["spherical", "aniso_3_angles", "three_structures", "own_axes", "nugget_only"])
def test_cstar_is_c_without_the_nugget(name: str) -> None:
    m = MODELS[name]
    c = cv.cov_model(m)
    d = lags(seed=8, n=200)
    got = np.array([cv.cov_cstar(*c.args, *row) for row in d])
    ref = np.array([cstar_ref(m, row) for row in d])
    assert np.abs(got - ref).max() <= 1e-15
    assert got[0] == sum(s.c for s in m.structures)  # C*(0) = Σ c_s
    # off the zero lag, C and C* differ by rounding only: the nugget is in C(0) alone
    assert np.abs(got[1:] - cov_of(m, d)[1:]).max() <= 1e-15


def disc_ref(size: tuple[float, float, float], disc: tuple[int, int, int], phi: float) -> np.ndarray:
    """The points of a block by a plain loop and the explicit turn."""
    nx, ny, nz = disc
    a = math.radians(phi)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    pts = []
    for i, j, k in itertools.product(range(nx), range(ny), range(nz)):
        local = np.array([((i + 0.5) / nx - 0.5) * size[0], ((j + 0.5) / ny - 0.5) * size[1]])
        x, y = rot @ local
        pts.append((x, y, ((k + 0.5) / nz - 0.5) * size[2]))
    return np.array(pts)


def pb_ref(m: Model, p: np.ndarray, centre: np.ndarray, pts: np.ndarray) -> float:
    if len(pts) == 1:
        return float(m.cov(p - (centre + pts[0])))
    return sum(cstar_ref(m, p - (centre + u)) for u in pts) / len(pts)


def vv_ref(m: Model, pts: np.ndarray) -> float:
    n = len(pts)
    if n == 1:
        return m.sill
    return sum(cstar_ref(m, a - b) for a in pts for b in pts) / (n * n)


def test_ok_etalon_cbar_vv_t883() -> None:
    """Э-ОК-1 (ТЗ 8.8.5): C0 = 0,1 + spherical c = 0,9, a = 30, block 10×10 m, 4×4: C̄_VV = 0,677710 (1e−6)."""
    m = MODELS["spherical"]
    cache = cv.BlockCov(cv.cov_model(m))
    vv = cache.vv((10.0, 10.0, 0.0), (4, 4, 1))
    assert vv == pytest.approx(0.677710, abs=1e-6)
    assert vv == pytest.approx(vv_ref(m, disc_ref((10.0, 10.0, 0.0), (4, 4, 1), 0.0)), abs=1e-14)


def test_ok_etalon_block_kriging_t883() -> None:
    """The same case end to end with the building blocks: z* = 2,024652, KV = 0,246936 (1e−6) — the system is
    solved by numpy here (the LU of the kernel is 18.4); the point (0; 0) of Т8.8-1 is the centre of the block."""
    m = MODELS["spherical"]
    c = cv.cov_model(m)
    xyz = np.array([[0.0, 10.0, 0.0], [10.0, 0.0, 0.0], [-6.0, -8.0, 0.0], [20.0, 20.0, 0.0]])
    z = np.array([1.0, 2.0, 3.0, 4.0])
    n = len(z)
    offs = cv.disc_points((10.0, 10.0, 0.0), (4, 4, 1))
    big = np.empty((n, n))
    cv.cov_matrix(*c.args, xyz, big)
    rhs = np.empty(n)
    cv.cov_rhs(*c.args, xyz, np.zeros(3), offs, rhs)
    a = np.zeros((n + 1, n + 1))
    a[:n, :n] = big
    a[:n, n] = a[n, :n] = 1.0
    sol = np.linalg.solve(a, np.append(rhs, 1.0))
    lam, mu = sol[:n], sol[n]
    vv = cv.cov_bb(*c.args, offs)
    assert float(lam @ z) == pytest.approx(2.024652, abs=1e-6)
    assert vv - float(lam @ rhs) - mu == pytest.approx(0.246936, abs=1e-6)


@pytest.mark.parametrize(
    ("name", "size", "disc", "phi"),
    [
        ("spherical", (10.0, 10.0, 5.0), (4, 4, 2), 0.0),
        ("aniso_3_angles", (12.5, 5.0, 3.0), (4, 4, 2), 25.0),
        ("three_structures", (20.0, 10.0, 6.0), (3, 2, 5), -70.0),
        ("own_axes", (5.0, 5.0, 5.0), (1, 1, 1), 0.0),
        ("no_nugget", (10.0, 10.0, 10.0), (2, 3, 4), 180.0),
        ("nugget_only", (10.0, 10.0, 10.0), (2, 2, 2), 0.0),
    ],
)
def test_means_against_plain_loops(
    name: str, size: tuple[float, float, float], disc: tuple[int, int, int], phi: float
) -> None:
    m = MODELS[name]
    c = cv.cov_model(m)
    pts = cv.disc_points(size, disc, phi)
    np.testing.assert_allclose(pts, disc_ref(size, disc, phi), atol=1e-13)
    assert cv.cov_bb(*c.args, pts) == pytest.approx(vv_ref(m, pts), abs=1e-13)
    centre = np.array([100.0, -50.0, 20.0])
    samples = centre + np.random.default_rng(2).uniform(-30.0, 30.0, size=(15, 3))
    samples[4] = centre + pts[0]  # a sample on a discretization point: C*(0) = Σ c_s, no nugget (N > 1)
    out = np.empty(len(samples))
    cv.cov_rhs(*c.args, samples, centre, pts, out)
    ref = np.array([pb_ref(m, p, centre, pts) for p in samples])
    assert np.abs(out - ref).max() <= 1e-13
    for i in (0, 4, 9):
        got = cv.cov_pb(*c.args, *samples[i], *centre, pts)
        assert got == out[i]


def test_one_point_block_is_c_with_nugget() -> None:
    """N = 1: C̄_iV = C(x_i − x_0), C̄_VV = C(0) — the nugget included."""
    m = MODELS["aniso_3_angles"]
    c = cv.cov_model(m)
    pts = cv.disc_points((10.0, 10.0, 10.0), (1, 1, 1))
    assert pts.tolist() == [[0.0, 0.0, 0.0]]
    p, x0 = np.array([3.0, -4.0, 5.0]), np.array([1.0, 1.0, 1.0])
    assert cv.cov_pb(*c.args, *p, *x0, pts) == float(m.cov(p - x0))
    assert cv.cov_pb(*c.args, *x0, *x0, pts) == m.sill
    assert cv.cov_bb(*c.args, pts) == m.sill


def test_block_variance_is_below_point_variance_and_grows_with_discretization() -> None:
    m = MODELS["spherical"]
    cache = cv.BlockCov(cv.cov_model(m))
    coarse = cache.vv((20.0, 20.0, 10.0), (2, 2, 2))
    fine = cache.vv((20.0, 20.0, 10.0), (6, 6, 4))
    assert fine < coarse < m.sill - m.c0 + 1e-12


# --- the discretization ----------------------------------------------------------------------------------------------


def test_disc_points_unturned() -> None:
    pts = cv.disc_points((8.0, 4.0, 2.0), (4, 2, 2))
    assert pts.shape == (16, 3) and pts.dtype == np.float64 and pts.flags.c_contiguous
    assert pts[0].tolist() == [-3.0, -1.0, -0.5]  # ix = iy = iz = 0
    assert pts[1].tolist() == [-3.0, -1.0, 0.5]  # iz is the fastest
    assert pts[2].tolist() == [-3.0, 1.0, -0.5]
    assert pts[4].tolist() == [-1.0, -1.0, -0.5]
    assert pts[-1].tolist() == [3.0, 1.0, 0.5]
    assert np.abs(pts.sum(axis=0)).max() == 0.0  # the centres of the cells are symmetric about the centre


def test_disc_points_turn_about_z() -> None:
    size, disc = (8.0, 4.0, 2.0), (4, 2, 2)
    base = cv.disc_points(size, disc)
    turned = cv.disc_points(size, disc, 90.0)  # counter-clockwise seen from above: (x, y) → (−y, x)
    assert np.array_equal(turned[:, 0], -base[:, 1] + 0.0) and np.array_equal(turned[:, 1], base[:, 0])
    assert np.array_equal(turned[:, 2], base[:, 2])
    assert np.array_equal(cv.disc_points(size, disc, 180.0)[:, :2], -base[:, :2] + 0.0)
    assert np.array_equal(cv.disc_points(size, disc, 360.0), base)
    for phi in (30.0, -125.0, 270.0):
        t = cv.disc_points(size, disc, phi)
        a = math.radians(phi)
        np.testing.assert_allclose(t[:, 0], math.cos(a) * base[:, 0] - math.sin(a) * base[:, 1], atol=1e-13)
        np.testing.assert_allclose(t[:, 1], math.sin(a) * base[:, 0] + math.cos(a) * base[:, 1], atol=1e-13)
        np.testing.assert_allclose(np.hypot(t[:, 0], t[:, 1]), np.hypot(base[:, 0], base[:, 1]), atol=1e-13)


def test_disc_points_flat_size_and_signed_zero() -> None:
    pts = cv.disc_points((10.0, 10.0, 0.0), (2, 2, 3))
    assert np.all(pts[:, 2] == 0.0) and not np.signbit(pts[:, 2]).any()
    assert not np.signbit(cv.disc_points((4.0, 4.0, 4.0), (1, 1, 1), 33.0)).any()


@pytest.mark.parametrize("disc", [(0, 1, 1), (1, 11, 1), (1, 1), (1.5, 1, 1), "abc", None, (math.nan, 1, 1)])
def test_disc_points_bad_disc(disc: object) -> None:
    with pytest.raises(StatsError) as e:
        cv.disc_points((1.0, 1.0, 1.0), disc)
    assert e.value.code == "bad_param" and e.value.param == "disc" and e.value.hint


@pytest.mark.parametrize("size", [(1.0, 1.0), (1.0, -1.0, 1.0), (math.nan, 1.0, 1.0), None])
def test_disc_points_bad_size(size: object) -> None:
    with pytest.raises(StatsError) as e:
        cv.disc_points(size, (2, 2, 2))
    assert e.value.code == "bad_param" and e.value.param == "size"


def test_turned_block_equals_block_of_swapped_sides() -> None:
    """A block 10×2 turned by 90° is the block 2×10: the same C̄_VV for an anisotropic model."""
    m = MODELS["aniso_3_angles"]
    cache = cv.BlockCov(cv.cov_model(m))
    turned = cache.vv((10.0, 2.0, 4.0), (4, 2, 2), 90.0)
    plain = cache.vv((2.0, 10.0, 4.0), (2, 4, 2), 0.0)
    assert turned == pytest.approx(plain, abs=1e-13)
    assert cache.vv((10.0, 2.0, 4.0), (4, 2, 2), 0.0) != pytest.approx(turned, abs=1e-6)  # the turn matters


def test_isotropic_model_does_not_feel_the_turn() -> None:
    cache = cv.BlockCov(cv.cov_model(MODELS["spherical"]))
    a = cache.vv((10.0, 4.0, 2.0), (4, 2, 2), 0.0)
    b = cache.vv((10.0, 4.0, 2.0), (4, 2, 2), 37.0)
    assert a == pytest.approx(b, abs=1e-14)


# --- the cache of C̄_VV -----------------------------------------------------------------------------------------------


def test_block_cache_by_size_disc_and_turn() -> None:
    m = MODELS["aniso_3_angles"]
    cache = cv.BlockCov(cv.cov_model(m))
    first = cache.vv((10.0, 10.0, 5.0), (4, 4, 2))
    assert (cache.computed, cache.hits) == (1, 0)
    assert cache.vv([10.0, 10.0, 5.0], [4, 4, 2]) == first  # a list, ints — the same key
    assert cache.vv((10, 10, 5), (4, 4, 2), 0.0) == first
    assert (cache.computed, cache.hits) == (1, 2)
    cache.vv((10.0, 10.0, 2.5), (4, 4, 2))
    cache.vv((10.0, 10.0, 5.0), (4, 4, 1))
    cache.vv((10.0, 10.0, 5.0), (4, 4, 2), 15.0)
    assert (cache.computed, cache.hits) == (4, 2)
    assert first == cv.cov_bb(*cache.cov.args, cv.disc_points((10.0, 10.0, 5.0), (4, 4, 2)))  # bit for bit
    pts = cache.points((10.0, 10.0, 5.0), (4, 4, 2))
    assert pts is cache.points((10.0, 10.0, 5.0), (4, 4, 2)) and not pts.flags.writeable


def test_block_cache_two_models_do_not_mix() -> None:
    a = cv.BlockCov(cv.cov_model(MODELS["spherical"]))
    b = cv.BlockCov(cv.cov_model(MODELS["gaussian"]))
    assert a.vv((10.0, 10.0, 10.0), (3, 3, 3)) != b.vv((10.0, 10.0, 10.0), (3, 3, 3))


def test_block_cache_threads_get_one_value() -> None:
    cache = cv.BlockCov(cv.cov_model(MODELS["three_structures"]))
    out: list[float] = []

    def work() -> None:
        out.append(cache.vv((15.0, 15.0, 7.5), (5, 5, 3), 12.0))

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(out)) == 1 and cache.computed >= 1 and cache.computed + cache.hits == 8


# --- writable and read-only inputs -----------------------------------------------------------------------------------


def test_read_only_and_writable_arrays_give_the_same_bits() -> None:
    m = MODELS["three_structures"]
    c = cv.cov_model(m)
    xyz = np.random.default_rng(9).uniform(-50.0, 50.0, size=(30, 3))
    centre = np.array([1.5, -2.5, 0.25])
    offs = cv.disc_points((10.0, 10.0, 5.0), (4, 4, 2), 20.0)
    ro = [frozen(a) for a in (*c.args, xyz, centre, offs)]
    k, p, h, x, ce, o = ro
    assert not any(a.flags.writeable for a in ro)
    w1, w2, r1, r2 = np.empty((30, 30)), np.empty(30), np.empty((30, 30)), np.empty(30)
    cv.cov_matrix(*c.args, xyz, w1)
    cv.cov_rhs(*c.args, xyz, centre, offs, w2)
    cv.cov_matrix(k, p, h, x, r1)
    cv.cov_rhs(k, p, h, x, ce, o, r2)
    assert np.array_equal(w1, r1) and np.array_equal(w2, r2)
    assert cv.cov_bb(k, p, h, o) == cv.cov_bb(*c.args, offs)
    assert cv.cov_pb(k, p, h, 1.0, 2.0, 3.0, *ce, o) == cv.cov_pb(*c.args, 1.0, 2.0, 3.0, *centre, offs)


def test_warm_kernels_leave_no_second_specialization() -> None:
    """After `warm_kernels` a call on writable or on read-only arrays compiles nothing new."""
    cv.warm_kernels()
    kernels = (cv.cov_c, cv.cov_cstar, cv.cov_pb, cv.cov_bb, cv.cov_matrix, cv.cov_rhs)
    before = [len(k.signatures) for k in kernels]
    m = MODELS["own_axes"]
    c = cv.cov_model(m)
    xyz = np.random.default_rng(1).uniform(-5.0, 5.0, size=(6, 3))
    offs = cv.disc_points((2.0, 2.0, 2.0), (2, 2, 2))
    for freeze in (False, True):
        k, p, h = (frozen(a) if freeze else a for a in c.args)
        x, o, ce = (frozen(a) if freeze else a for a in (xyz, offs, np.zeros(3)))
        cv.cov_c(k, p, h, 1.0, 2.0, 3.0)
        cv.cov_cstar(k, p, h, 1.0, 2.0, 3.0)
        cv.cov_pb(k, p, h, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, o)
        cv.cov_bb(k, p, h, o)
        cv.cov_matrix(k, p, h, x, np.empty((6, 6)))
        cv.cov_rhs(k, p, h, x, ce, o, np.empty(6))
    assert [len(k.signatures) for k in kernels] == before


def test_kernels_are_nogil_and_cached() -> None:
    for k in (cv.cov_c, cv.cov_cstar, cv.cov_pb, cv.cov_bb, cv.cov_matrix, cv.cov_rhs):
        assert k.targetoptions.get("nogil") is True
        assert not k.targetoptions.get("fastmath")
        assert k._cache.__class__.__name__ != "NullCache"
