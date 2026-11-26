"""Э19, 19.4: the solution of all patches of the implicit model on a pool of threads (ТЗ 8.9.2, Т8.9-3; ADR 0026,
decision 9). References: the data reproduced by every patch (η = 0, 1e-8·max abs f), the patches independent of one
another (equal to `solve_patch` of the same points), the bytes of λ, c and cond₁ the same on 1, 2, 4 and 8 threads and
after a permutation of the input made canonical, cancellation, a degenerate patch named by its points and sources."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from bf_core.api.errors import ApiError
from bf_core.model import system as sy
from bf_core.model.octree import PatchSet, build_patches
from bf_core.model.solve import solve_patches
from bf_core.model.system import PatchSolution, RbfSingular

F8 = NDArray[np.float64]
CORE = Path(__file__).resolve().parents[1]
KINDS = ["linear", "cubic", "wendland"]
RADIUS = 160.0


def canonical(points: F8, f: F8) -> tuple[F8, F8]:
    """The caller's canonical order (ADR 0026, decision 4): (x, y, z, f), lexicographic."""
    order = np.lexsort((f, points[:, 2], points[:, 1], points[:, 0]))
    return np.ascontiguousarray(points[order]), np.ascontiguousarray(f[order])


def fibonacci(n: int, r: float) -> F8:
    k = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * k / n)
    th = np.pi * (1 + 5**0.5) * k
    return r * np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], axis=1)


def sphere_data(n: int = 600, r: float = 50.0) -> tuple[F8, F8]:
    """Т8.9-1 data: contacts (0) on r = R, points at R/2 (−R/2) and 1.5·R (+R/2); canonical order."""
    pts = np.concatenate([fibonacci(n, r), fibonacci(n // 2, 0.5 * r), fibonacci(n // 2, 1.5 * r)])
    f = np.concatenate([np.zeros(n), np.full(n // 2, -0.5 * r), np.full(n // 2, 0.5 * r)])
    return canonical(pts, f)


def kw(kind: str) -> dict[str, object]:
    return {"kind": kind, "radius": RADIUS if kind == "wendland" else None}


def digest(sols: list[PatchSolution]) -> str:
    h = hashlib.sha256()
    for s in sols:
        h.update(s.lam.tobytes())
        h.update(s.poly.tobytes())
        h.update(np.float64(s.cond1).tobytes())
    return h.hexdigest()


@pytest.fixture(scope="module")
def sphere() -> tuple[F8, F8, PatchSet]:
    pts, f = sphere_data()
    return pts, f, build_patches(pts, n_patch=150, n_min=80, delta=0.25)


@pytest.mark.parametrize("kind", KINDS)
def test_t89_3_every_patch_reproduces_its_data(kind: str, sphere: tuple[F8, F8, PatchSet]) -> None:
    """Т8.9-3: η = 0 → abs(f_j(x_i) − f_i) ≤ 1e-8·max abs(f) at every point of every patch."""
    pts, f, ps = sphere
    sols = solve_patches(pts, f, ps, **kw(kind))
    assert ps.n_patches > 4 and len(sols) == ps.n_patches
    for j, s in enumerate(sols):
        idx = ps.patch(j)
        assert s.points.shape == (len(idx), 3) and s.eta == 0.0 and s.kind == kind
        assert np.max(np.abs(s.evaluate(pts[idx]) - f[idx])) <= 1e-8 * np.max(np.abs(f))
        assert np.array_equal(s.centre, ps.centers[j]) and s.scale == ps.radii[j]
        assert s.cond1 > 1.0


def test_a_patch_equals_solve_patch_of_its_own_points(sphere: tuple[F8, F8, PatchSet]) -> None:
    """The patches are independent: the pool gives the bytes of a lone `solve_patch` of the same centre and R."""
    pts, f, ps = sphere
    sols = solve_patches(pts, f, ps, kind="cubic", threads=4)
    for j in (0, ps.n_patches // 2, ps.n_patches - 1):
        idx = ps.patch(j)
        one = sy.solve_patch(pts[idx], f[idx], "cubic", None, 0.0, centre=ps.centers[j], scale=float(ps.radii[j]))
        assert np.array_equal(sols[j].lam, one.lam) and np.array_equal(sols[j].poly, one.poly)
        assert sols[j].cond1 == one.cond1


@pytest.mark.parametrize("kind", KINDS)
def test_threads_1_2_4_8_give_the_same_bytes(kind: str, sphere: tuple[F8, F8, PatchSet]) -> None:
    """Т8.9-8: λ, c and cond₁ of every patch are bitwise the same on 1, 2, 4 and 8 threads."""
    pts, f, ps = sphere
    ref = digest(solve_patches(pts, f, ps, threads=1, **kw(kind)))
    for threads in (2, 4, 8):
        assert digest(solve_patches(pts, f, ps, threads=threads, **kw(kind))) == ref, threads


def test_permuted_input_made_canonical_gives_the_same_bytes() -> None:
    """The order of the input is the caller's: after the canonical sort, a shuffled copy is the same problem."""
    pts, f = sphere_data(400)
    rng = np.random.default_rng(5)
    perm = rng.permutation(len(f))
    pts2, f2 = canonical(pts[perm], f[perm])
    assert np.array_equal(pts, pts2)
    a = A_MATRIX
    out = []
    for p, v in ((pts, f), (pts2, f2)):
        ps = build_patches(p, n_patch=120, n_min=60, matrix=a)
        out.append(digest(solve_patches(p, v, ps, kind="linear", threads=4)))
    assert out[0] == out[1]


A_MATRIX = np.array([[1.0, 0.1, 0.0], [0.0, 0.8, 0.1], [0.05, 0.0, 0.5]])


def test_global_matrix_is_applied_once_like_a_pretransformed_input() -> None:
    """`patches.matrix` (the global A): the system is that of the points A·x; the solutions equal those of the caller
    who turned the points himself."""
    from bf_core.model.octree import transform_points

    pts, f = sphere_data(300)
    ps_a = build_patches(pts, n_patch=100, n_min=60, matrix=A_MATRIX)
    sols_a = solve_patches(pts, f, ps_a, kind="linear", threads=2)
    turned = transform_points(pts, A_MATRIX)
    ps_b = build_patches(turned, n_patch=100, n_min=60)
    sols_b = solve_patches(turned, f, ps_b, kind="linear", threads=2)
    assert np.array_equal(ps_a.centers, ps_b.centers) and np.array_equal(ps_a.indices, ps_b.indices)
    assert digest(sols_a) == digest(sols_b)


def test_eta_and_radius_per_patch_and_smoothing_changes_the_fit(sphere: tuple[F8, F8, PatchSet]) -> None:
    pts, f, ps = sphere
    m = ps.n_patches
    etas = [0.0 if j % 2 else 0.5 for j in range(m)]
    sols = solve_patches(pts, f, ps, kind="linear", eta=etas, threads=2)
    assert [s.eta for s in sols] == etas
    smooth = [j for j in range(m) if etas[j] > 0.0][0]
    assert np.max(np.abs(sols[smooth].evaluate(pts[ps.patch(smooth)]) - f[ps.patch(smooth)])) > 1e-6
    radii = [100.0 + j for j in range(m)]
    w = solve_patches(pts, f, ps, kind="wendland", radius=radii, eta=1e-6)
    assert [s.radius for s in w] == radii
    with pytest.raises(ApiError) as e:
        solve_patches(pts, f, ps, eta=[0.0] * (m - 1))
    assert e.value.code == "bad_param"


def test_patch_matrices_turn_the_points_of_that_patch_only(sphere: tuple[F8, F8, PatchSet]) -> None:
    """The hook of 19.9: A_j turns the points and the centre of patch j before its system is built."""
    from bf_core.model.octree import transform_points

    pts, f, ps = sphere
    mats: list[F8 | None] = [None] * ps.n_patches
    mats[3] = np.array([[1.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 0.5]])
    plain = solve_patches(pts, f, ps, kind="linear")
    sols = solve_patches(pts, f, ps, kind="linear", matrices=mats, threads=3)
    assert digest(sols[:3] + sols[4:]) == digest(plain[:3] + plain[4:])
    idx = ps.patch(3)
    x = transform_points(pts[idx], mats[3])  # type: ignore[arg-type]
    c = transform_points(ps.centers[3].reshape(1, 3), mats[3])[0]  # type: ignore[arg-type]
    from bf_core.model.anisotropy import stretch_of

    scale = float(ps.radii[3]) * stretch_of(mats[3])  # type: ignore[arg-type]  # the radius of the turned ball (19.9)
    one = sy.solve_patch(x, f[idx], "linear", None, 0.0, centre=c, scale=scale)
    assert np.array_equal(sols[3].lam, one.lam) and np.array_equal(sols[3].points, x)
    with pytest.raises(ApiError):
        solve_patches(pts, f, ps, matrices=[None])


def test_tick_runs_per_patch_and_cancels_the_pool(sphere: tuple[F8, F8, PatchSet]) -> None:
    pts, f, ps = sphere
    calls = []

    class Stop(Exception):
        pass

    def tick() -> None:
        calls.append(1)
        if len(calls) == 3:
            raise Stop

    for threads in (1, 4):
        calls.clear()
        before = threading.active_count()
        with pytest.raises(Stop):
            solve_patches(pts, f, ps, threads=threads, tick=tick)
        assert len(calls) == 3
        assert threading.active_count() == before  # the pool is gone: nothing runs after the exception
    ticks = []
    solve_patches(pts, f, ps, threads=2, tick=lambda: ticks.append(1))
    assert len(ticks) == ps.n_patches


def test_memory_cap_serialises_the_patches_and_changes_no_byte(
    sphere: tuple[F8, F8, PatchSet], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`max_memory`: a patch begins only while the matrices in flight fit the cap (a lone patch always passes)."""
    from bf_core.model import solve as sv

    peak = {"count": 0, "bytes": 0, "max": 0, "max_together": 0}  # in flight now; the largest count / bytes seen
    lock = threading.Lock()
    real = sv.solve_patch

    def watched(x: F8, *a: object, **k: object) -> PatchSolution:
        need = sv.patch_bytes(len(x))
        with lock:
            peak["count"] += 1
            peak["bytes"] += need
            peak["max"] = max(peak["max"], peak["count"])
            if peak["count"] > 1:
                peak["max_together"] = max(peak["max_together"], peak["bytes"])
        try:
            return real(x, *a, **k)  # type: ignore[arg-type]
        finally:
            with lock:
                peak["count"] -= 1
                peak["bytes"] -= need

    monkeypatch.setattr(sv, "solve_patch", watched)
    pts, f, ps = sphere
    free = digest(solve_patches(pts, f, ps, threads=4))
    assert peak["max"] > 1  # the pool does run patches together
    peak.update(max=0, max_together=0)
    assert digest(solve_patches(pts, f, ps, threads=4, max_memory=1)) == free
    assert peak["max"] == 1
    cap = sv.patch_bytes(int(ps.sizes.max()))  # the largest patch
    peak.update(max=0, max_together=0)
    assert digest(solve_patches(pts, f, ps, threads=4, max_memory=cap)) == free
    assert peak["max_together"] <= cap  # whatever ran together fitted the cap


def coincident_data() -> tuple[F8, F8, NDArray[np.int64]]:
    """A cloud with one point given twice (two sources): singular at η = 0, fine with a smoothing."""
    rng = np.random.default_rng(3)
    pts = rng.random((300, 3)) * 100.0
    f = pts[:, 2] - 0.5 * pts[:, 0]
    pts = np.vstack([pts, pts[17:18]])
    f = np.concatenate([f, f[17:18] + 0.25])
    pts, f = canonical(pts, f)
    return pts, f, np.arange(len(f), dtype=np.int64) * 10 + 7  # a source id for every point


def test_coincident_points_name_the_patch_the_points_and_their_sources() -> None:
    pts, f, src = coincident_data()
    ps = build_patches(pts, n_patch=120, n_min=60)
    twins = np.flatnonzero((pts == pts[:, None, :]).all(axis=2).sum(axis=0) > 1)
    assert len(twins) == 2
    for threads in (1, 4):
        with pytest.raises(RbfSingular) as e:
            solve_patches(pts, f, ps, threads=threads, sources=src)
        err = e.value
        assert err.status == 422 and err.code == "rbf_singular" and err.extra["reason"] == "coincident"
        assert err.extra["points"] == [[int(twins[0]), int(twins[1])]]  # canonical numbers, not rows of the patch
        assert err.extra["sources"] == [[int(src[twins[0]]), int(src[twins[1]])]]
        j = err.extra["patch"]
        assert set(twins) <= set(ps.patch(j).tolist()) and j == min(
            k for k in range(ps.n_patches) if set(twins) <= set(ps.patch(k).tolist())
        )  # the first patch by number, whatever the threads
        assert f"№ {twins[0] + 1}" in err.message and "сглаживание" in err.hint.lower()
    solve_patches(pts, f, ps, eta=1e-3, threads=2)  # a smoothing solves it


def test_coplanar_patch_is_reported_with_its_points() -> None:
    rng = np.random.default_rng(9)
    flat = np.column_stack([rng.random(80) * 10 + 500, rng.random(80) * 10, np.zeros(80)])
    cloud = rng.random((400, 3)) * 100.0
    pts, f = canonical(np.vstack([cloud, flat]), np.concatenate([cloud[:, 0], np.zeros(80)]))
    ps = build_patches(pts, n_patch=60, n_min=40, extent=(np.zeros(3), np.array([520.0, 100.0, 100.0])))
    with pytest.raises(RbfSingular) as e:
        solve_patches(pts, f, ps, threads=4, sources=np.arange(len(f)))
    ex = e.value.extra
    assert ex["reason"] == "coplanar" and 0 <= ex["patch"] < ps.n_patches
    assert ex["points"] == ps.patch(ex["patch"])[:20].tolist() and ex["sources"] == ex["points"]
    assert ex["n_points"] == len(ps.patch(ex["patch"])) and len(ex["centre"]) == 3
    assert e.value.message.startswith(f"Участок № {ex['patch'] + 1}")


@pytest.mark.parametrize("what", ["nan_value", "inf_value", "nan_coord"])
def test_missing_data_is_a_clear_error_never_zero(what: str, sphere: tuple[F8, F8, PatchSet]) -> None:
    pts, f, ps = sphere
    pts, f = pts.copy(), f.copy()
    if what == "nan_value":
        f[10] = np.nan
    elif what == "inf_value":
        f[10] = np.inf
    else:
        pts[10, 1] = np.nan
    with pytest.raises(ApiError) as e:
        solve_patches(pts, f, ps)
    assert e.value.status == 422 and e.value.code == "rbf_bad_data" and "№ 11" in e.value.message


def test_inconsistent_input_is_rejected(sphere: tuple[F8, F8, PatchSet]) -> None:
    pts, f, ps = sphere
    for bad in ((pts, f[:-1]), (pts[:50], f[:50])):
        with pytest.raises(ApiError) as e:
            solve_patches(bad[0], bad[1], ps)
        assert e.value.code == "rbf_bad_points"
    with pytest.raises(ApiError) as e2:
        solve_patches(pts, f, ps, sources=np.arange(5))
    assert e2.value.code == "bad_param"


def test_input_is_not_modified_and_readonly_arrays_work(sphere: tuple[F8, F8, PatchSet]) -> None:
    pts, f, ps = sphere
    p, v = pts.copy(), f.copy()
    p.setflags(write=False)
    v.setflags(write=False)
    solve_patches(p, v, ps, threads=2)
    assert np.array_equal(p, pts) and np.array_equal(v, f)


def test_hard_data_clustered_with_a_huge_offset() -> None:
    """A dense core, a thin sheet, outliers and coordinates of 1e6–1e7 m (the centre of a patch is taken away)."""
    rng = np.random.default_rng(11)
    core = rng.normal(0.0, 5.0, (500, 3))
    sheet = np.column_stack([rng.random(400) * 600, rng.random(400) * 600, 100 + rng.normal(0, 0.05, 400)])
    far = rng.random((100, 3)) * 3000 + 1000
    xyz = np.vstack([core, sheet, far]) + np.array([5.0e6, 7.0e6, 300.0])
    f = np.sin(0.01 * (xyz[:, 0] - 5.0e6)) * 20 + 0.05 * (xyz[:, 2] - 300.0)
    xyz, f = canonical(xyz, f)
    ps = build_patches(xyz, n_patch=100, n_min=60)
    sols = solve_patches(xyz, f, ps, kind="linear", threads=4)
    scale = np.max(np.abs(f))
    worst = max(np.max(np.abs(s.evaluate(xyz[ps.patch(j)]) - f[ps.patch(j)])) for j, s in enumerate(sols))
    assert worst <= 1e-8 * scale
    assert digest(sols) == digest(solve_patches(xyz, f, ps, kind="linear", threads=1))


def test_solve_module_does_not_load_numba_at_import() -> None:
    """Handlers import `solve` and `evaluate`: numba comes only with the kernels, inside the calls."""
    code = "import bf_core.model.solve, bf_core.model.evaluate, sys; raise SystemExit('numba' in sys.modules)"
    run = subprocess.run([sys.executable, "-c", code], cwd=CORE, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-300:] or "numba is loaded at import of bf_core.model.solve / evaluate"


def test_module_is_small() -> None:
    lines = (CORE / "bf_core" / "model" / "solve.py").read_text(encoding="utf-8").count("\n")
    assert lines <= 600
