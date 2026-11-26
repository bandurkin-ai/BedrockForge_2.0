"""The radial functions φ of the implicit model (ТЗ 8.9.2; ADR 0026 §3, решения 7, 10; Э19, 19.2): φ against the
formulas (1e−15), the block Φ (Gram and cross) against a NumPy reference, the compact support, NaN kept (never zero),
the same bytes whatever the number of threads."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from bf_core.model import kernels as kn

TOL = 1e-15


def ref_phi(r: np.ndarray, kind: str, a: float) -> np.ndarray:
    """The formulas of 8.9.2 written independently of the kernel: −r, r³, (1 − r/a)⁴₊·(4r/a + 1)."""
    if kind == "linear":
        return -r
    if kind == "cubic":
        return r**3
    t = r / a
    return np.where(t < 1.0, (1.0 - t) ** 4 * (4.0 * t + 1.0), 0.0)


def radii(a: float = 7.5) -> np.ndarray:
    """Zero, tiny, inside, at and far beyond the support radius."""
    r = np.concatenate(
        [[0.0, 1e-300, 1e-12, 1e-6], np.linspace(0.0, 3.0 * a, 997), [a * (1 - 1e-15), a, a * (1 + 1e-15), 1e6]]
    )
    return np.sort(r)


@pytest.mark.parametrize("kind", ["linear", "cubic", "wendland"])
def test_phi_matches_formula(kind: str) -> None:
    a = 7.5
    r = radii(a)
    got = kn.phi(r, kind, a)
    want = ref_phi(r, kind, a)
    assert got.dtype == np.float64 and got.shape == r.shape
    assert np.max(np.abs(got - want)) <= TOL * max(1.0, float(np.max(np.abs(want[r < 1e3]))))
    # relative, away from the exact zeros of the compact kernel
    keep = np.abs(want) > 0
    assert np.max(np.abs(got[keep] - want[keep]) / np.abs(want[keep])) <= 4e-15


def test_wendland_support_and_values() -> None:
    a = 3.0
    assert kn.phi(np.array([0.0]), "wendland", a)[0] == 1.0
    beyond = kn.phi(np.array([a, a * 1.0000001, 2 * a, 1e9]), "wendland", a)
    assert np.all(beyond == 0.0)  # an exact zero beyond a: a patch far from a point sees nothing of it
    inside = kn.phi(np.linspace(0.0, a, 200, endpoint=False), "wendland", a)
    assert np.all(np.diff(inside) < 0) and np.all(inside > 0)


def test_phi_keeps_nan_and_rejects_unknown_kernel() -> None:
    out = kn.phi(np.array([1.0, np.nan, 2.0]), "cubic", 1.0)
    assert out[0] == 1.0 and np.isnan(out[1]) and out[2] == 8.0
    with pytest.raises(ValueError):
        kn.kernel_code("gauss")
    assert [kn.kernel_code(k) for k in kn.KERNELS] == [kn.LINEAR, kn.CUBIC, kn.WENDLAND]
    # the exponent s of φ(R·r) = R^s·φ(r) used by the equilibration of the system (system.py)
    assert [kn.homogeneity(kn.kernel_code(k)) for k in ("linear", "cubic", "wendland")] == [1, 3, 0]


def cloud(n: int, seed: int = 5, scale: float = 40.0, offset: float = 0.0) -> np.ndarray:
    return np.random.default_rng(seed).normal(size=(n, 3)) * scale + offset


@pytest.mark.parametrize("kind", ["linear", "cubic", "wendland"])
def test_gram_matches_numpy(kind: str) -> None:
    a = 55.0
    x = cloud(180)
    eta = 0.125
    g = kn.gram(x, kind, a, eta=eta)
    d = x[:, None, :] - x[None, :, :]
    want = ref_phi(np.sqrt(np.sum(d * d, axis=2)), kind, a) + eta * np.eye(len(x))
    assert g.shape == (180, 180) and g.dtype == np.float64
    assert np.max(np.abs(g - want)) <= TOL * max(1.0, float(np.max(np.abs(want))))
    assert np.array_equal(g, g.T)  # exactly symmetric: the mirror is a copy


def test_gram_writes_into_the_corner_of_a_larger_matrix() -> None:
    x = cloud(30)
    big = np.full((34, 34), -7.0)
    kn.gram(x, "cubic", 1.0, eta=0.5, out=big)
    assert np.array_equal(big[:30, :30], kn.gram(x, "cubic", 1.0, eta=0.5))
    assert np.all(big[30:, :] == -7.0) and np.all(big[:, 30:] == -7.0)  # the polynomial border is the caller's


@pytest.mark.parametrize("kind", ["linear", "cubic", "wendland"])
def test_cross_matches_numpy_and_gram(kind: str) -> None:
    a = 60.0
    x, y = cloud(70, 1), cloud(41, 2)
    c = kn.cross(x, y, kind, a)
    d = x[:, None, :] - y[None, :, :]
    want = ref_phi(np.sqrt(np.sum(d * d, axis=2)), kind, a)
    assert c.shape == (70, 41)
    assert np.max(np.abs(c - want)) <= TOL * max(1.0, float(np.max(np.abs(want))))
    assert np.array_equal(kn.cross(x, x, kind, a), kn.gram(x, kind, a))


def test_nan_coordinate_gives_nan_not_zero() -> None:
    x = cloud(6)
    x[2, 1] = np.nan
    g = kn.gram(x, "linear", 1.0)
    assert np.all(np.isnan(g[2, [0, 1, 3, 4, 5]])) and np.all(np.isnan(g[[0, 1, 3, 4, 5], 2]))
    assert not np.isnan(g[0, 1])


def test_same_bytes_on_any_number_of_threads() -> None:
    """The kernels let the GIL go (`nogil`); a block is a function of its points alone."""
    pts = [cloud(300, seed=s) for s in range(8)]
    serial = [kn.gram(p, "cubic", 1.0, eta=1e-3).tobytes() for p in pts]
    for workers in (2, 4, 8):
        with ThreadPoolExecutor(workers) as pool:
            got = list(pool.map(lambda p: kn.gram(p, "cubic", 1.0, eta=1e-3).tobytes(), pts))
        assert got == serial


def test_input_arrays_may_be_read_only_and_strided() -> None:
    x = cloud(50)
    ro = x.copy()
    ro.setflags(write=False)
    strided = np.asfortranarray(x)
    want = kn.gram(x, "linear", 1.0)
    assert np.array_equal(kn.gram(ro, "linear", 1.0), want)
    assert np.array_equal(kn.gram(strided, "linear", 1.0), want)
