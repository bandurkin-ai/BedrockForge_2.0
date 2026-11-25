"""Э19, 19.10: the smoothing η of the patches, «Вручную» and «Авто» (ТЗ 8.9.2, 7.35 Д-25; Т8.9-14, -15, -16; ADR 0026,
decisions 7, 9, 11а, 22). References: a dense eigendecomposition of Φ_⊥ (the truth the measured ends are checked
against, and the cond₂ the target is about), the exact formulas of η = s·h̄^k, the bytes of η on 1, 2, 4 and 8 threads
and after a permutation of the input made canonical, clouds with doubles (distance 1e-6·h and exactly zero) and the
sphere of Т8.9-1."""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from scipy.linalg import eigvalsh
from scipy.spatial.distance import pdist

from bf_core.api.errors import ApiError
from bf_core.model import smoothing as sm
from bf_core.model.octree import PatchSet, build_patches, transform_points
from bf_core.model.solve import solve_patches
from bf_core.model.spec import parse_group, parse_model
from bf_core.model.system import PatchSolution, RbfSingular, assemble, single_blas_thread, solve_patch

F8 = NDArray[np.float64]
CORE = Path(__file__).resolve().parents[1]
H = 2.0  # the step h of Т8.9-1
RADIUS = 160.0
R_SPHERE = 50.0
TARGET = 1e8


def canonical(points: F8, f: F8) -> tuple[F8, F8]:
    order = np.lexsort((f, points[:, 2], points[:, 1], points[:, 0]))
    return np.ascontiguousarray(points[order]), np.ascontiguousarray(f[order])


def fibonacci(n: int, r: float) -> F8:
    k = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * k / n)
    th = np.pi * (1 + 5**0.5) * k
    return r * np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], axis=1)


def sphere_data(n: int = 600, r: float = R_SPHERE) -> tuple[F8, F8]:
    """Т8.9-1 data: contacts (0) on r = R, points at R/2 (−R/2) and 1.5·R (+R/2); canonical order; 2·n points."""
    pts = np.concatenate([fibonacci(n, r), fibonacci(n // 2, 0.5 * r), fibonacci(n // 2, 1.5 * r)])
    f = np.concatenate([np.zeros(n), np.full(n // 2, -0.5 * r), np.full(n // 2, 0.5 * r)])
    return canonical(pts, f)


def with_doubles(pts: F8, f: F8, frac: float, dist: float, noise: float, seed: int = 1) -> tuple[F8, F8]:
    """`frac` of the points get a double `dist` metres away (0 — exactly the same place) whose f differs by `noise`."""
    sel = np.random.default_rng(seed).choice(len(pts), int(frac * len(pts)), replace=False)
    shift = np.array([dist, 0.0, 0.0])
    return canonical(np.vstack([pts, pts[sel] + shift]), np.concatenate([f, f[sel] + noise]))


def spec_of(kernel: str = "linear", mode: str = "auto", s: float | None = None, target: float = TARGET):  # noqa: ANN201
    body: dict[str, object] = {"boundary": {"box": [-100, -100, -100, 100, 100, 100]}, "kernel": kernel,
                               "smoothing": {"mode": mode, "cond_target": target}}  # fmt: skip
    if s is not None:
        body["smoothing"] = {"mode": mode, "s": s, "cond_target": target}  # type: ignore[assignment]
    if kernel == "wendland":
        body["kernel_radius"] = RADIUS
    return parse_model(body)


def one_patch(pts: F8) -> PatchSet:
    return build_patches(pts, n_patch=len(pts) + 10, n_min=80)


def truth_cond2(pts: F8, ps: PatchSet, j: int, kernel: str, eta_m: float, radius: float | None = None) -> float:
    """cond₂ of Φ_⊥ + η·I of patch j by a dense eigendecomposition (the 4 zeros of Π·Φ·Π dropped), η in metres."""
    idx = ps.patch(j)
    r = float(ps.radii[j])
    xn = (pts[idx] - ps.centers[j]) / r
    n = len(idx)
    power = {"linear": 1, "cubic": 3, "wendland": 0}[kernel]
    with single_blas_thread():
        phi = assemble(xn, kernel, None if radius is None else radius / r, 0.0)[:n, :n]
        q_p, _ = sm._complement(xn, False)
        proj = phi - q_p @ (q_p.T @ phi)
        proj = proj - (proj @ q_p) @ q_p.T
        w = eigvalsh(0.5 * (proj + proj.T), check_finite=False)
    w = np.delete(w, np.argsort(np.abs(w))[:4])  # the null space of P
    e = eta_m / r**power
    return float((w[-1] + e) / (max(w[0], 0.0) + e))


def digest(sols: list[PatchSolution]) -> bytes:
    out = b""
    for s in sols:
        out += s.lam.tobytes() + s.poly.tobytes() + np.float64(s.cond1).tobytes() + np.float64(s.eta).tobytes()
    return out


@pytest.fixture(scope="module")
def doubles_cloud() -> tuple[F8, F8, PatchSet]:
    """Т8.9-14: 2000 points of the sphere with 5 % of doubles 1e-6·h apart whose f differs; one patch."""
    pts, f = sphere_data(950)
    pts, f = with_doubles(pts, f, 0.0527, 1e-6 * H, 0.5)
    assert len(pts) == 2000 - 1 or len(pts) == 2000 or abs(len(pts) - 2000) <= 3
    return pts, f, one_patch(pts)


# --- Т8.9-14, -15, -16 -----------------------------------------------------------------------------------------


def test_t89_14_auto_brings_cond_to_the_target_and_the_solve_stands(doubles_cloud: tuple[F8, F8, PatchSet]) -> None:
    """Т8.9-14: 5 % of doubles 1e-6·h apart: «Авто» → cond₂ ≤ 1,05·target (and not far below it: η is the smallest);
    without it the spectrum is more than 10·target wide and the coefficients blow up."""
    pts, f, ps = doubles_cloud
    assert ps.n_patches == 1
    res = sm.smooth_patches(pts, ps, spec_of("linear"))
    assert res.exact[0] and res.eta[0] > 0.0
    c_auto = truth_cond2(pts, ps, 0, "linear", float(res.eta[0]))
    c_none = truth_cond2(pts, ps, 0, "linear", 0.0)
    assert 0.85 * TARGET <= c_auto <= 1.05 * TARGET, c_auto
    assert abs(res.cond[0] - c_auto) <= 0.02 * c_auto  # the cond the search ended with is the truth to 2 %
    assert c_none > 10 * TARGET  # the contrast: without η the system is degenerate
    kw = {"centre": ps.centers[0], "scale": float(ps.radii[0])}
    ok = solve_patch(pts[ps.patch(0)], f[ps.patch(0)], "linear", None, float(res.eta[0]), **kw)
    assert np.isfinite(ok.lam).all() and ok.cond1 < 1e13
    try:
        bad = solve_patch(pts[ps.patch(0)], f[ps.patch(0)], "linear", None, 0.0, cond_max=1e300, **kw)
    except RbfSingular:
        return
    assert np.max(np.abs(bad.lam)) > 10 * np.max(np.abs(ok.lam))  # a degenerate answer, not the same one


def test_t89_15_no_eta_where_none_is_needed_and_the_sphere_keeps_its_shape() -> None:
    """Т8.9-15: the data of Т8.9-1 are well conditioned: η = 0 (≤ 1e-6·max abs Φ), also when the patch is measured, and
    the zero level of the field is the sphere to 0,25·h."""
    pts, f = sphere_data(600)
    ps = one_patch(pts)
    max_phi = float(pdist(pts).max())  # φ = −r: max abs Φ is the farthest pair
    auto = sm.smooth_patches(pts, ps, spec_of("linear"))
    measured = sm.smooth_patches(pts, ps, spec_of("linear"), screen=False)
    assert measured.exact[0] and np.isfinite(measured.cond[0]) and measured.cond[0] < TARGET
    for res in (auto, measured):
        assert res.eta[0] <= 1e-6 * max_phi and res.eta[0] == 0.0
    sol = solve_patch(pts, f, "linear", None, float(auto.eta[0]), centre=ps.centers[0], scale=float(ps.radii[0]))
    assert np.max(np.abs(sol.evaluate(pts) - f)) <= 1e-8 * np.max(np.abs(f))  # η = 0 reproduces the data
    dirs = fibonacci(30, 1.0)
    radii = np.linspace(0.6 * R_SPHERE, 1.4 * R_SPHERE, 801)
    worst = 0.0
    for d in dirs:
        vals = sol.evaluate(d[None, :] * radii[:, None])
        k = int(np.flatnonzero(np.diff(np.sign(vals)) != 0)[0])
        root = radii[k] - vals[k] * (radii[k + 1] - radii[k]) / (vals[k + 1] - vals[k])
        worst = max(worst, abs(root - R_SPHERE))
    assert worst <= 0.25 * H, worst


def cloud_with_many_patches() -> tuple[F8, F8, PatchSet]:
    pts, f = sphere_data(500)
    pts, f = with_doubles(pts, f, 0.06, 1e-6 * H, 0.4, seed=7)
    return pts, f, build_patches(pts, n_patch=150, n_min=80)


@pytest.mark.parametrize("kernel", ["linear", "cubic"])
def test_t89_16_the_same_bytes_on_1_2_4_8_threads(kernel: str) -> None:
    """Т8.9-16: η, cond and the flags are bitwise the same on 1, 2, 4 and 8 threads, and so is the solve with them."""
    pts, f, ps = cloud_with_many_patches()
    spec = spec_of(kernel)
    ref = sm.smooth_patches(pts, ps, spec, threads=1)
    assert ps.n_patches > 4 and ref.exact.sum() >= 3 and (ref.eta > 0).sum() >= 3
    kw = {"kind": kernel}
    sols = solve_patches(pts, f, ps, eta=ref.eta, threads=1, **kw)
    for threads in (2, 4, 8):
        got = sm.smooth_patches(pts, ps, spec, threads=threads)
        assert got.eta.tobytes() == ref.eta.tobytes() and got.cond.tobytes() == ref.cond.tobytes()
        assert got.exact.tobytes() == ref.exact.tobytes()
        assert digest(solve_patches(pts, f, ps, eta=got.eta, threads=threads, **kw)) == digest(sols)


def test_a_permuted_input_made_canonical_gives_the_same_eta() -> None:
    pts, f, ps = cloud_with_many_patches()
    ref = sm.smooth_patches(pts, ps, spec_of("linear"), threads=2)
    perm = np.random.default_rng(11).permutation(len(f))
    pts2, f2 = canonical(pts[perm], f[perm])
    assert np.array_equal(pts, pts2) and np.array_equal(f, f2)
    ps2 = build_patches(pts2, n_patch=150, n_min=80)
    got = sm.smooth_patches(pts2, ps2, spec_of("linear"), threads=4)
    assert got.eta.tobytes() == ref.eta.tobytes() and ps2.hashes == ps.hashes


# --- the spectrum and the search ------------------------------------------------------------------------------


@pytest.mark.parametrize("kernel", ["linear", "cubic", "wendland"])
def test_the_measured_ends_are_the_dense_ones(kernel: str) -> None:
    """Lanczos on Φ and on the inverse of the system gives λ_max and λ_min of Φ_⊥ of the dense decomposition (a patch
    with doubles: the smallest eigenvalue is 1e-8 of the largest)."""
    pts, _ = sphere_data(150)
    pts, f = with_doubles(pts, np.zeros(len(pts)), 0.05, 1e-6 * H, 0.3)
    c = 0.5 * (pts.min(axis=0) + pts.max(axis=0))
    r = float(np.sqrt(np.max(np.sum((pts - c) ** 2, axis=1))))
    xn = (pts - c) / r
    rad = 2.0 if kernel == "wendland" else None
    with single_blas_thread():
        lo, hi, mx = sm.measured_ends(xn, kernel, rad, 0.0, TARGET)
        a = assemble(xn, kernel, rad, 0.0)
    n = len(xn)
    q_p, q_c = sm._complement(xn, True)
    assert q_c is not None
    w = eigvalsh(q_c.T @ a[:n, :n] @ q_c, check_finite=False)
    assert n > sm.DENSE_N
    assert hi == pytest.approx(w[-1], rel=1e-6)
    assert lo == pytest.approx(max(w[0], 0.0), rel=1e-3, abs=1e-12 * w[-1])
    assert mx == pytest.approx(np.max(np.abs(a[:n, :n])))


@pytest.mark.parametrize("kernel", ["linear", "cubic"])
def test_cond_falls_with_eta(kernel: str) -> None:
    """cond₂ (from the measured ends) and cond₁ of the system both fall as η grows; cond₂ by the formula."""
    pts, _ = sphere_data(120)
    pts, f = with_doubles(pts, np.zeros(len(pts)), 0.05, 1e-6 * H, 0.3)
    ps = one_patch(pts)
    r = float(ps.radii[0])
    power = {"linear": 1, "cubic": 3}[kernel]
    etas = [10.0**k for k in range(-11, 0, 2)]
    c2 = [truth_cond2(pts, ps, 0, kernel, e * r**power) for e in etas]
    assert all(b < a for a, b in zip(c2, c2[1:], strict=False)), c2
    c1 = [solve_patch(pts, f, kernel, None, e * r**power, centre=ps.centers[0], scale=r, cond_max=1e300).cond1
          for e in etas]  # fmt: skip
    assert all(b <= a * 1.0001 for a, b in zip(c1, c1[1:], strict=False)), c1


def test_search_eta_is_the_smallest_eta_reaching_the_target() -> None:
    lam_min, lam_max, t = 1e-9, 10.0, 1e6
    e, c = sm.search_eta(lam_min, lam_max, 0.0, 1.0, t)
    assert 0.95 * t <= c <= t and c == sm.cond2(lam_min, lam_max, e)
    assert sm.cond2(lam_min, lam_max, e * 0.9) > t  # a smaller η does not do it
    assert sm.search_eta(1e-3, 10.0, 0.0, 1.0, 1e6) == (0.0, 1e4)  # no η where none is needed (Т8.9-15)
    e0 = 1e-3
    assert sm.search_eta(lam_min, lam_max, e0, 1.0, 1e6)[0] == e0  # η₀ already reaches it
    assert sm.search_eta(lam_min, lam_max, 1e-12, 1.0, 1e6)[0] > 1e-12  # η₀ is the floor, not the answer
    e_cap, c_cap = sm.search_eta(0.0, 10.0, 0.0, 1e-3, 1e3)  # the target is out of reach below η_max
    assert e_cap == 1e-3 and c_cap > 1e3
    assert sm.cond2(0.0, 10.0, 0.0) == math.inf


# --- doubles, the modes, the spec ---------------------------------------------------------------------------


def test_coincident_points_with_a_different_f_manual_s_and_auto() -> None:
    """Points in the same place with different f: s = 0 is the `rbf_singular` of the solve; «Вручную» s > 0 solves;
    «Авто» finds an η > 0 by itself and the solve stands."""
    pts, f = sphere_data(120)
    pts, f = with_doubles(pts, f, 0.05, 0.0, 0.4)
    ps = build_patches(pts, n_patch=150, n_min=80)
    with pytest.raises(RbfSingular) as err:
        solve_patches(pts, f, ps, kind="linear", eta=0.0)
    assert err.value.code == "rbf_singular" and err.value.extra["reason"] == "coincident"
    manual = sm.smooth_patches(pts, ps, spec_of("linear", "manual", s=0.05))
    assert not manual.exact.any() and np.all(manual.eta > 0)
    sols = solve_patches(pts, f, ps, kind="linear", eta=manual.eta)
    assert all(np.isfinite(s.lam).all() for s in sols)
    auto = sm.smooth_patches(pts, ps, spec_of("linear"))
    assert auto.exact.sum() >= 1 and auto.eta.max() > 0.0
    sols = solve_patches(pts, f, ps, kind="linear", eta=auto.eta)
    assert all(np.isfinite(s.lam).all() and s.cond1 < 1e14 for s in sols)
    assert all(truth_cond2(pts, ps, j, "linear", float(auto.eta[j])) <= 1.05 * TARGET for j in range(ps.n_patches)
               if auto.exact[j])  # fmt: skip


def test_the_fit_at_a_double_is_between_its_two_values() -> None:
    """Smoothed doubles: the field at the shared place is a value between the two f (the smoothing averages)."""
    pts, f = sphere_data(120)
    pts, f2 = with_doubles(pts, f, 0.05, 0.0, 0.4)
    ps = one_patch(pts)
    res = sm.smooth_patches(pts, ps, spec_of("linear"))
    sol = solve_patch(pts, f2, "linear", None, float(res.eta[0]), centre=ps.centers[0], scale=float(ps.radii[0]))
    uniq, first, counts = np.unique(pts, axis=0, return_index=True, return_counts=True)
    for u, c in zip(uniq[counts > 1], counts[counts > 1], strict=True):
        rows = np.flatnonzero(np.all(pts == u, axis=1))
        v = float(sol.evaluate(u[None, :])[0])
        lo, hi = float(f2[rows].min()), float(f2[rows].max())
        assert c == 2 and lo - 0.1 * (hi - lo) <= v <= hi + 0.1 * (hi - lo), (v, lo, hi)


@pytest.mark.parametrize(("kernel", "power"), [("linear", 1), ("cubic", 3), ("wendland", 0)])
def test_manual_eta_is_s_times_h_bar_to_the_k(kernel: str, power: int) -> None:
    """η = s·h̄ (linear), s·h̄³ (cubic), s (finite radius); h̄ is the median distance to the nearest point."""
    pts, _ = sphere_data(150)
    pts = np.vstack([pts, pts[:10]])  # coincident points count (they give 0)
    pts, _f = canonical(pts, np.zeros(len(pts)))
    ps = build_patches(pts, n_patch=150, n_min=80)
    from scipy.spatial import cKDTree

    d, _ = cKDTree(pts).query(pts, k=2)
    h_bar = float(np.median(d[:, 1]))
    assert sm.nearest_median(pts) == h_bar > 0.0
    s = 0.2
    spec = spec_of(kernel, "manual", s=s)
    res = sm.smooth_patches(pts, ps, spec)
    want = float(f"{s * h_bar**power:.6g}")
    assert np.all(res.eta == want) and not res.exact.any() and np.isnan(res.cond).all()
    assert np.array_equal(sm.eta_for_patches(pts, None, ps, spec), res.eta)
    assert np.all(sm.smooth_patches(pts, ps, spec, h_bar=1.0).eta == float(f"{s:.6g}") * (1.0 if power else 1.0))


def test_s_by_the_type_of_the_group_and_the_default_target() -> None:
    """`s` null: 0 for a contact group and 0,05 for a shell (spec.s_of); «Авто» starts from it (η₀ is the floor) and the
    target is 1e8 unless given."""
    pts, _ = sphere_data(150)
    ps = build_patches(pts, n_patch=150, n_min=80)
    spec = parse_model({"boundary": {"box": [-100, -100, -100, 100, 100, 100]},
                        "smoothing": {"mode": "manual"}})  # fmt: skip
    dom = parse_group({"name": "D", "type": "domain", "codes": [1]}, "group")
    shell = parse_group(
        {"name": "S", "type": "shell", "params": {"result_id": 3, "field": "cu", "thresholds": [1.0]}}, "group"
    )
    h_bar = sm.nearest_median(pts)
    assert np.all(sm.smooth_patches(pts, ps, spec, group=dom).eta == 0.0)
    assert np.all(sm.smooth_patches(pts, ps, spec, group=shell).eta == float(f"{0.05 * h_bar:.6g}"))
    auto = parse_model({"boundary": {"box": [-100, -100, -100, 100, 100, 100]}, "smoothing": {"mode": "auto"}})
    assert auto.smoothing.cond_target == 1e8
    res = sm.smooth_patches(pts, ps, auto, group=shell, screen=False)  # η₀ = 0,05·h̄ is the floor of the search
    assert np.all(res.eta >= float(f"{0.05 * h_bar:.6g}")) and res.exact.all()


def test_global_matrix_and_per_patch_matrices_are_applied_as_the_solve_does() -> None:
    pts, f = sphere_data(150)
    pts, f = with_doubles(pts, f, 0.05, 1e-6 * H, 0.3)
    a = np.diag([1.0, 1.0, 2.5])
    ps_m = build_patches(pts, n_patch=150, n_min=80, matrix=a)
    ps_t = build_patches(transform_points(pts, a), n_patch=150, n_min=80)
    spec = spec_of("linear")
    res_m = sm.smooth_patches(pts, ps_m, spec)
    res_t = sm.smooth_patches(transform_points(pts, a), ps_t, spec)
    assert res_m.eta.tobytes() == res_t.eta.tobytes() and res_m.eta.max() > 0.0
    sols = solve_patches(pts, f, ps_m, kind="linear", eta=res_m.eta)  # the η of the helper is the η of the solve
    assert all(s.eta == e for s, e in zip(sols, res_m.eta, strict=True))
    mats = [np.diag([1.0, 1.0, 2.5])] * ps_t.n_patches  # the same A per patch on the untransformed points
    res_j = sm.smooth_patches(pts, build_patches(pts, n_patch=150, n_min=80), spec, matrices=mats)
    assert res_j.eta.max() > 0.0
    with pytest.raises(ApiError) as err:
        sm.smooth_patches(pts, ps_t, spec, matrices=mats[:-1])
    assert err.value.code == "bad_param"


def test_a_patch_the_solve_rejects_gets_eta0_and_the_solve_names_it() -> None:
    """Coplanar points are no case for η: the patch keeps η₀ (not measured) and `solve_patches` raises as before."""
    rng = np.random.default_rng(2)
    flat = np.column_stack([rng.random((200, 2)) * 100.0, np.zeros(200)])
    pts, f = canonical(flat, flat[:, 0])
    ps = build_patches(pts, n_patch=300, n_min=80)
    res = sm.smooth_patches(pts, ps, spec_of("linear"), screen=False)
    assert not res.exact.any() and np.all(res.eta == 0.0)
    with pytest.raises(RbfSingular) as err:
        solve_patches(pts, f, ps, kind="linear", eta=res.eta)
    assert err.value.extra["reason"] == "coplanar"


def test_refine_patch_measures_a_patch_the_screen_let_through() -> None:
    pts, f, ps = cloud_with_many_patches()
    spec = spec_of("linear")
    full = sm.smooth_patches(pts, ps, spec)
    j = int(np.flatnonzero(full.exact)[0])
    eta_j, cond_j = sm.refine_patch(pts, ps, spec, j)
    assert eta_j == full.eta[j] and cond_j == full.cond[j]
    only = sm.smooth_patches(pts, ps, spec, only=[j], screen=False)
    assert only.exact.sum() == 1 and only.eta[j] == full.eta[j]
    assert np.all(only.eta[np.arange(ps.n_patches) != j] == 0.0)


def test_tick_is_called_per_patch_and_may_cancel() -> None:
    pts, f, ps = cloud_with_many_patches()
    ticks: list[int] = []
    sm.smooth_patches(pts, ps, spec_of("linear"), threads=2, tick=lambda: ticks.append(1))
    assert len(ticks) == ps.n_patches

    class Stop(Exception):
        pass

    def stop() -> None:
        raise Stop

    for threads in (1, 4):
        with pytest.raises(Stop):
            sm.smooth_patches(pts, ps, spec_of("linear"), threads=threads, tick=stop)


def test_bad_arguments_are_russian_422() -> None:
    pts, f = sphere_data(100)
    ps = build_patches(pts, n_patch=150, n_min=80)
    with pytest.raises(ApiError) as err:
        sm.eta_for_patches(pts, f[:-1], ps, spec_of("linear"))
    assert err.value.status == 422 and "Значений" in err.value.message
    with pytest.raises(ApiError) as err:
        sm.smooth_patches(pts[:50], ps, spec_of("linear"))
    assert err.value.code == "rbf_bad_points"
    assert sm.kernel_radii("linear", None, ps) is None
    assert sm.kernel_radii("wendland", 30.0, ps) == [30.0] * ps.n_patches
    assert sm.kernel_radii("wendland", None, ps) == [2.0 * r for r in ps.radii]


def test_the_screen_is_a_figure_above_the_cond1_of_real_patches() -> None:
    """The screen never says «fine» about a patch whose system is worse than the target: on clouds with and without
    doubles, predicted ≥ the cond₁ of the solve."""
    for dist in (None, 1e-3, 1e-6, 0.0):
        pts, f = sphere_data(150)
        if dist is not None:
            pts, f = with_doubles(pts, f, 0.03, dist, 0.2)
        ps = one_patch(pts)
        xn = (pts[ps.patch(0)] - ps.centers[0]) / ps.radii[0]
        from scipy.spatial import cKDTree

        q = float(cKDTree(xn).query(xn, k=2)[0][:, 1].min())
        for kernel in ("linear", "cubic"):
            try:
                real = solve_patch(pts, f, kernel, None, 0.0, centre=ps.centers[0], scale=float(ps.radii[0]),
                                   cond_max=1e300).cond1  # fmt: skip
            except RbfSingular:
                real = math.inf
            assert sm.predicted_cond(kernel, len(xn), q) >= real, (kernel, dist)


def test_smoothing_does_not_load_numba_with_its_import() -> None:
    """The handler of the job imports this module in the process of the API: numba comes with the first matrix."""
    code = "import bf_core.model.smoothing, sys; raise SystemExit(3 if 'numba' in sys.modules else 0)"
    run = subprocess.run([sys.executable, "-c", code], cwd=CORE, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-300:] or "numba is loaded at import of bf_core.model.smoothing"
