"""The system of a patch of the implicit model (ТЗ 8.9.2, Т8.9-3; ADR 0026 §3, решение 9; Э19, 19.2): [Φ + ηI, P; Pᵀ, 0]
assembled and solved by LU on one patch — the data are reproduced at η = 0, λ is orthogonal to P, the solution equals
that of the plain system in metres, the condition number is the LAPACK estimate, a degenerate patch is a 422
`rbf_singular` with the hint of the ADR, the bytes do not depend on the pool of threads, numba stays unloaded."""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from threadpoolctl import threadpool_info

from bf_core.api.errors import ApiError
from bf_core.model import system as sy

ROOT = Path(__file__).resolve().parents[2]
KINDS = ["linear", "cubic", "wendland"]
RADIUS = 160.0  # a of the finite-radius kernel, metres: wider than the data of `shells`


def fibonacci(n: int, r: float) -> np.ndarray:
    k = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * k / n)
    th = np.pi * (1 + 5**0.5) * k
    return r * np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], axis=1)


def sphere_data(n: int = 300, r: float = 50.0) -> tuple[np.ndarray, np.ndarray]:
    """Т8.9-1: contacts (f = 0) on r = R, points inside at R/2 (−R/2) and outside at 1.5·R (+R/2)."""
    pts = np.concatenate([fibonacci(n, r), fibonacci(n // 2, 0.5 * r), fibonacci(n // 2, 1.5 * r)])
    f = np.concatenate([np.zeros(n), np.full(n // 2, -0.5 * r), np.full(n // 2, 0.5 * r)])
    return pts, f


def kwargs(kind: str) -> dict:
    return {"kind": kind, "radius": RADIUS if kind == "wendland" else None}


@pytest.mark.parametrize("kind", KINDS)
def test_t89_3_data_reproduced_at_zero_eta(kind: str) -> None:
    """Т8.9-3: η = 0 → abs(f(x_i) − f_i) ≤ 1e−8·max abs(f) at every point of the data (one patch)."""
    pts, f = sphere_data()
    sol = sy.solve_patch(pts, f, **kwargs(kind))
    assert np.max(np.abs(sol.evaluate(pts) - f)) <= 1e-8 * np.max(np.abs(f))
    assert sol.lam.shape == f.shape and sol.poly.shape == (4,) and sol.eta == 0.0


@pytest.mark.parametrize("kind", KINDS)
def test_system_rows_and_orthogonality(kind: str) -> None:
    """The conditions of the system: Φλ + P·c = f and Pᵀλ = 0 (P in the coordinates of the patch)."""
    pts, f = sphere_data(120)
    sol = sy.solve_patch(pts, f, eta=0.0, **kwargs(kind))
    xn = (pts - sol.centre) / sol.scale
    p = np.column_stack([np.ones(len(pts)), xn])
    lam_n = sol.lam * sy.kernel_scale(kind, sol.scale)  # λ of the system in the units of the patch
    assert np.max(np.abs(p.T @ lam_n)) <= 1e-10 * np.max(np.abs(lam_n)) * len(pts)
    assert np.max(np.abs(sol.gram_apply(pts) + p @ sol.poly - f)) <= 1e-8 * np.max(np.abs(f))


@pytest.mark.parametrize("kind", KINDS)
def test_equals_the_plain_system_in_metres(kind: str) -> None:
    """The solution does not depend on the units the block is built in: the same λ and c as [Φ_m + ηI, P; Pᵀ, 0] solved
    densely with the distances in metres (n = 70, η > 0)."""
    from bf_core.model import kernels as kn

    pts, f = sphere_data(40)
    pts = pts + np.array([3.0, -2.0, 1.0])  # the centre of the patch is not the origin
    eta = {"linear": 0.5, "cubic": 5.0, "wendland": 1e-3}[kind]
    sol = sy.solve_patch(pts, f, eta=eta, **kwargs(kind))
    n = len(pts)
    a = np.zeros((n + 4, n + 4))
    a[:n, :n] = kn.gram(pts, kind, RADIUS if kind == "wendland" else None, eta=eta)
    p = np.column_stack([np.ones(n), (pts - sol.centre) / sol.scale])
    a[:n, n:], a[n:, :n] = p, p.T
    x = np.linalg.solve(a, np.concatenate([f, np.zeros(4)]))
    assert np.max(np.abs(sol.lam - x[:n])) <= 1e-8 * np.max(np.abs(x[:n]))
    assert np.max(np.abs(sol.poly - x[n:])) <= 1e-8 * np.max(np.abs(x[n:]))


def test_assemble_structure() -> None:
    xn = np.random.default_rng(2).uniform(-1, 1, size=(9, 3))
    a = sy.assemble(xn, "cubic", None, eta=0.25)
    assert a.shape == (13, 13) and a.dtype == np.float64
    d = np.linalg.norm(xn[:, None, :] - xn[None, :, :], axis=2)
    assert np.allclose(a[:9, :9], d**3 + 0.25 * np.eye(9), rtol=0, atol=1e-15)
    assert np.array_equal(a[:9, 9], np.ones(9)) and np.array_equal(a[:9, 10:], xn)
    assert np.array_equal(a[9:, :9], a[:9, 9:].T) and np.all(a[9:, 9:] == 0.0)


def test_smoothing_does_not_interpolate_but_satisfies_the_system() -> None:
    pts, f = sphere_data(100)
    sol = sy.solve_patch(pts, f, kind="linear", eta=2.0)
    res = np.abs(sol.evaluate(pts) - f)
    assert 1e-6 < np.max(res) < 0.5 * np.max(np.abs(f))  # a smoothed fit, not a reproduction
    assert sol.eta == 2.0
    # Φλ + ηλ + P·c = f exactly (η is on the diagonal of the system, not in the field)
    xn = (pts - sol.centre) / sol.scale
    p = np.column_stack([np.ones(len(pts)), xn])
    assert np.max(np.abs(sol.gram_apply(pts) + 2.0 * sol.lam + p @ sol.poly - f)) <= 1e-9 * np.max(np.abs(f))


@pytest.mark.parametrize("kind", KINDS)
def test_a_linear_function_is_reproduced_by_the_polynomial(kind: str) -> None:
    """f = a·x + b exactly: λ = 0 and c carries it (the polynomial part of the system)."""
    rng = np.random.default_rng(8)
    pts = rng.uniform(-30, 30, size=(80, 3)) + np.array([100.0, 200.0, -50.0])
    f = 0.3 * pts[:, 0] - 0.2 * pts[:, 1] + 0.05 * pts[:, 2] + 7.0
    sol = sy.solve_patch(pts, f, **kwargs(kind))
    assert np.max(np.abs(sol.lam)) <= 1e-8 * np.max(np.abs(f))
    probe = rng.uniform(-30, 30, size=(10, 3)) + np.array([100.0, 200.0, -50.0])
    want = 0.3 * probe[:, 0] - 0.2 * probe[:, 1] + 0.05 * probe[:, 2] + 7.0
    assert np.max(np.abs(sol.evaluate(probe) - want)) <= 1e-8 * np.max(np.abs(f))


def test_condition_number_is_the_lapack_estimate() -> None:
    from bf_core.model import kernels as kn

    pts, f = sphere_data(40)
    sol = sy.solve_patch(pts, f, kind="cubic", eta=0.0)
    n = len(pts)
    xn = (pts - sol.centre) / sol.scale
    a = np.zeros((n + 4, n + 4))
    a[:n, :n] = kn.gram(xn, "cubic")
    p = np.column_stack([np.ones(n), xn])
    a[:n, n:], a[n:, :n] = p, p.T
    true = np.linalg.cond(a, 1)
    # xGECON estimates 1/‖A⁻¹‖₁ from below: never above the true condition number, within a small factor of it
    assert true / 10 <= sol.cond1 <= true * (1 + 1e-6)
    assert sol.rcond == pytest.approx(1.0 / sol.cond1)


def test_hard_data_far_from_the_origin_and_thin() -> None:
    """Project coordinates of 1e6–1e7 m and a slab 5 m thick over 800 m: the same field as the data at the origin."""
    rng = np.random.default_rng(31)
    pts = np.column_stack([rng.uniform(0, 800, 400), rng.uniform(0, 800, 400), rng.uniform(0, 5, 400)])
    f = 0.01 * (pts[:, 0] - 400) + np.sin(pts[:, 1] / 120.0) * 3.0 + pts[:, 2]
    shift = np.array([6_100_000.0, 7_250_000.0, 1_400.0])
    base = sy.solve_patch(pts, f, kind="linear", eta=0.0)
    moved = sy.solve_patch(pts + shift, f, kind="linear", eta=0.0)
    assert np.max(np.abs(moved.evaluate(pts + shift) - f)) <= 1e-7 * np.max(np.abs(f))
    probe = rng.uniform(0, 800, size=(20, 3)) * np.array([1, 1, 0.006])
    assert np.max(np.abs(moved.evaluate(probe + shift) - base.evaluate(probe))) <= 1e-6 * np.max(np.abs(f))


def test_duplicates_with_different_values_need_smoothing() -> None:
    """Coincident points with equal f: singular at η = 0. With different f (the case of ADR 0026 §2, решение 4) the
    matrix is singular too, and η > 0 solves it."""
    pts, f = sphere_data(80)
    pts2 = np.concatenate([pts, pts[:3]])
    f2 = np.concatenate([f, f[:3] + 1.0])
    with pytest.raises(sy.RbfSingular) as err:
        sy.solve_patch(pts2, f2, kind="linear", eta=0.0)
    assert err.value.extra["reason"] == "coincident"
    assert err.value.extra["points"][:3] == [[0, len(pts)], [1, len(pts) + 1], [2, len(pts) + 2]]
    with pytest.raises(sy.RbfSingular):
        sy.solve_patch(pts2, np.concatenate([f, f[:3]]), kind="cubic", eta=0.0)
    sol = sy.solve_patch(pts2, f2, kind="linear", eta=0.1)
    assert np.all(np.isfinite(sol.lam))
    # the two values at a doubled point are fitted by their mean (the smoothing decides, 8.9.2 η)
    assert abs(sol.evaluate(pts[:1])[0] - (f[0] + 0.5)) < 0.5


@pytest.mark.parametrize("kind", KINDS)
def test_coplanar_points_are_singular(kind: str) -> None:
    rng = np.random.default_rng(4)
    xy = rng.uniform(-50, 50, size=(60, 2))
    pts = np.column_stack([xy, 0.3 * xy[:, 0] + 0.2 * xy[:, 1] + 5.0])  # the plane of Т8.9-2
    f = rng.normal(size=60)
    with pytest.raises(sy.RbfSingular) as err:
        sy.solve_patch(pts, f, eta=0.5, **kwargs(kind))  # smoothing does not help: P is rank 3
    assert err.value.extra["reason"] == "coplanar"
    line = np.column_stack([np.linspace(0, 90, 30), np.linspace(0, 45, 30), np.linspace(0, 10, 30)])
    with pytest.raises(sy.RbfSingular) as err2:
        sy.solve_patch(line, np.arange(30.0), **kwargs(kind))
    assert err2.value.extra["reason"] == "coplanar"


@pytest.mark.parametrize("n", [0, 1, 3])
def test_too_few_points_are_singular(n: int) -> None:
    pts = np.random.default_rng(1).normal(size=(n, 3))
    with pytest.raises(sy.RbfSingular) as err:
        sy.solve_patch(pts, np.zeros(n), kind="linear")
    assert err.value.extra["reason"] == "few_points"


def test_singular_error_is_the_documented_422() -> None:
    pts, f = sphere_data(20)
    with pytest.raises(ApiError) as caught:
        sy.solve_patch(np.concatenate([pts, pts[:1]]), np.concatenate([f, f[:1]]), kind="linear")
    err = caught.value
    assert (err.status, err.code) == (422, "rbf_singular")
    assert err.message.strip() and all(ord(c) > 127 or not c.isalpha() for c in err.message[:3])  # Russian text
    assert "s > 0" in err.hint and "Авто" in err.hint  # the hint of ADR 0026, решение 9
    assert err.extra["points"] == [[0, len(pts)]]


def test_a_numerically_singular_system_is_refused_not_solved() -> None:
    """Points that are not equal but the same to 4e-16 of the patch: the pivot is not exactly 0, the condition
    estimate is beyond 1/ε."""
    pts, f = sphere_data(60)
    twin = pts[:2] * (1 + 4e-16)
    sol_args = (np.concatenate([pts, twin]), np.concatenate([f, f[:2]]))
    with pytest.raises(sy.RbfSingular):
        sy.solve_patch(*sol_args, kind="linear", eta=0.0)


def test_non_finite_data_are_refused_never_zeroed() -> None:
    pts, f = sphere_data(30)
    bad = f.copy()
    bad[3] = np.nan
    with pytest.raises(ApiError) as e1:
        sy.solve_patch(pts, bad, kind="linear")
    assert e1.value.code == "rbf_bad_data" and e1.value.status == 422
    badp = pts.copy()
    badp[5, 0] = np.inf
    with pytest.raises(ApiError) as e2:
        sy.solve_patch(badp, f, kind="linear")
    assert e2.value.code == "rbf_bad_data"
    with pytest.raises(ApiError):
        sy.solve_patch(pts, f[:-1], kind="linear")
    with pytest.raises(ApiError):
        sy.solve_patch(pts, f, kind="wendland")  # no radius


def test_inputs_are_not_modified_and_may_be_read_only() -> None:
    pts, f = sphere_data(60)
    pts.setflags(write=False)
    f.setflags(write=False)
    before = (pts.tobytes(), f.tobytes())
    a = sy.solve_patch(pts, f, kind="cubic")
    assert (pts.tobytes(), f.tobytes()) == before
    b = sy.solve_patch(np.array(pts), np.array(f), kind="cubic")
    assert a.lam.tobytes() == b.lam.tobytes() and a.poly.tobytes() == b.poly.tobytes()


def patches(k: int = 8, n: int = 260) -> list[tuple[np.ndarray, np.ndarray]]:
    out = []
    for s in range(k):
        rng = np.random.default_rng(100 + s)
        pts = rng.uniform(-60, 60, size=(n, 3))
        out.append((pts, np.sqrt((pts**2).sum(1)) - 40.0 + 0.01 * rng.normal(size=n)))
    return out


def test_same_bytes_on_any_number_of_threads() -> None:
    """Т8.9-8 at the level of the patch: a pool of 2, 4, 8 threads over independent patches (BLAS on one thread around
    the pool: `single_blas_thread`) gives the bytes of the serial run in the calling thread; the same patch twice."""
    work = patches()

    def one(item: tuple[np.ndarray, np.ndarray]) -> bytes:
        s = sy.solve_patch(item[0], item[1], kind="cubic", eta=1e-3)
        return s.lam.tobytes() + s.poly.tobytes() + np.float64(s.cond1).tobytes()

    serial = [one(w) for w in work]
    assert [one(w) for w in work] == serial
    for workers in (1, 2, 4, 8):
        with sy.single_blas_thread(), ThreadPoolExecutor(workers) as pool:
            assert list(pool.map(one, work)) == serial


def blas_threads() -> list[int]:
    return sorted(m["num_threads"] for m in threadpool_info() if m["user_api"] == "blas")


def test_blas_limit_is_set_for_the_section_and_restored() -> None:
    before = blas_threads()
    with sy.single_blas_thread():
        inside = blas_threads()
        with sy.single_blas_thread():  # nested: no change, no early release
            assert blas_threads() == inside
        assert blas_threads() == inside
        sy.solve_patch(*sphere_data(40), kind="linear")  # a patch inside the section does not touch the limit
        assert blas_threads() == inside
    assert blas_threads() == before
    assert all(t == 1 for t in inside)
    sy.solve_patch(*sphere_data(40), kind="linear")  # outside: the patch limits BLAS itself and gives it back
    assert blas_threads() == before


def test_model_system_does_not_load_numba() -> None:
    """`system.py` is imported by job handlers: numba comes only with the kernels, inside the function that builds a
    block (the API process does not pay for it — `test_job_handler_modules_do_not_load_numba`)."""
    code = "import bf_core.model.system, sys; raise SystemExit(3 if 'numba' in sys.modules else 0)"
    run = subprocess.run([sys.executable, "-c", code], cwd=ROOT / "core", capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-300:] or "numba is loaded at import of bf_core.model.system"
