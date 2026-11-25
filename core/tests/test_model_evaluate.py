"""Э19, 19.4: the field of the implicit model — the partition of unity over the patches (ТЗ 8.9.2; Т8.9-2, Т8.9-3;
ADR 0026, decision 10). References: an independent numpy evaluation of Σ W_j f_j / Σ W_j from `PatchSolution.evaluate`,
the signed distance to a plane (reproduced exactly) and to a sphere, the data of the model (η = 0), the continuity of
the field across the balls of the patches, NaN outside every ball, the same bytes on 1, 2, 4, 8 threads, for any order
of the query points and any split into bands."""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from bf_core.api.errors import ApiError
from bf_core.model import evaluate as ev
from bf_core.model.evaluate import BAND, PatchField, evaluate, partition_weight
from bf_core.model.octree import build_patches, transform_points
from bf_core.model.solve import solve_patches
from bf_core.model.system import PatchSolution

F8 = NDArray[np.float64]
CORE = Path(__file__).resolve().parents[1]
KINDS = ["linear", "cubic", "wendland"]
RADIUS = 160.0
H = 2.0  # the step h of Т8.9-1 / Т8.9-2


def canonical(points: F8, f: F8) -> tuple[F8, F8]:
    order = np.lexsort((f, points[:, 2], points[:, 1], points[:, 0]))
    return np.ascontiguousarray(points[order]), np.ascontiguousarray(f[order])


def fibonacci(n: int, r: float) -> F8:
    k = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * k / n)
    th = np.pi * (1 + 5**0.5) * k
    return r * np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], axis=1)


def sphere_data(n: int = 800, r: float = 50.0) -> tuple[F8, F8]:
    """Т8.9-1 data: contacts (0) on r = R, points at R/2 (−R/2) and 1.5·R (+R/2)."""
    pts = np.concatenate([fibonacci(n, r), fibonacci(n // 2, 0.5 * r), fibonacci(n // 2, 1.5 * r)])
    f = np.concatenate([np.zeros(n), np.full(n // 2, -0.5 * r), np.full(n // 2, 0.5 * r)])
    return canonical(pts, f)


def plane_distance(p: F8) -> F8:
    """The signed distance to z = 0.3x + 0.2y + 5 (Т8.9-2), positive above."""
    return np.asarray((p[:, 2] - 0.3 * p[:, 0] - 0.2 * p[:, 1] - 5.0) / np.sqrt(1.0 + 0.09 + 0.04))


def kw(kind: str) -> dict[str, object]:
    return {"kind": kind, "radius": RADIUS if kind == "wendland" else None}


class Model:
    def __init__(self, pts: F8, f: F8, kind: str = "linear", n_patch: int = 150, n_min: int = 80, delta: float = 0.25,
                 matrix: F8 | None = None, eta: float = 0.0) -> None:  # fmt: skip
        self.pts, self.f = pts, f
        self.ps = build_patches(pts, n_patch=n_patch, n_min=n_min, delta=delta, matrix=matrix)
        self.sols = solve_patches(pts, f, self.ps, eta=eta, threads=2, **kw(kind))
        self.field = PatchField(self.ps, self.sols)


@pytest.fixture(scope="module")
def sphere() -> Model:
    return Model(*sphere_data())


def reference(m: Model, q: F8, matrices: list[F8 | None] | None = None) -> F8:
    """Σ W_j f_j / Σ W_j from the plain numpy evaluation of every patch (order of the numbers); NaN where no weight."""
    qs = q if m.ps.matrix is None else transform_points(q, m.ps.matrix)
    num = np.zeros(len(q))
    den = np.zeros(len(q))
    for j, s in enumerate(m.sols):
        r = np.linalg.norm(qs - m.ps.centers[j], axis=1) / m.ps.radii[j]
        w = np.where(r < 1.0, partition_weight(r), 0.0)
        on = w > 0.0
        if not on.any():
            continue
        a = None if matrices is None else matrices[j]
        t = qs[on] if a is None else transform_points(qs[on], a)
        num[on] += w[on] * s.evaluate(t)
        den[on] += w[on]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0.0, num / den, np.nan)


def test_weight_function_of_8_9_2() -> None:
    """W(r) = (1 − r)⁴₊·(4r + 1): 1 at the centre, 0 and flat at r = 1, 0 beyond; C¹ at the boundary."""
    r = np.array([0.0, 0.25, 0.5, 1.0, 1.5])
    expect = np.array([1.0, 0.75**4 * 2.0, 0.5**4 * 3.0, 0.0, 0.0])
    assert np.allclose(partition_weight(r), expect, rtol=1e-15, atol=0.0)
    eps = 1e-6
    assert partition_weight(1.0 - eps) < 1e-23 and abs(partition_weight(1.0 - eps) / eps) < 1e-16  # W′(1) = 0


@pytest.mark.parametrize("kind", KINDS)
def test_t89_3_field_reproduces_the_data(kind: str) -> None:
    """Т8.9-3: η = 0 → abs(f(x_i) − f_i) ≤ 1e-8·max abs(f) at every data point, by the field (PoU) of all patches."""
    m = Model(*sphere_data(500), kind=kind)
    f = m.field(m.pts)
    assert not np.isnan(f).any()
    assert np.max(np.abs(f - m.f)) <= 1e-8 * np.max(np.abs(m.f))


@pytest.mark.parametrize("kind", KINDS)
def test_t89_2_plane_vertices_lie_on_the_plane(kind: str) -> None:
    """Т8.9-2: values = the signed distance to the plane z = 0.3x + 0.2y + 5 → the zero set of the field is the plane
    (1e-6·h). A linear function is reproduced exactly (λ = 0): the field on the plane is 0 and equals the distance
    everywhere, whatever the kernel; the vertex on a vertical line is the root of f, found by one secant step."""
    rng = np.random.default_rng(2)
    pts = rng.random((900, 3)) * np.array([100.0, 100.0, 40.0]) + np.array([0.0, 0.0, 0.0])
    f = plane_distance(pts)
    pts, f = canonical(pts, f)
    m = Model(pts, f, kind=kind)
    xy = rng.random((400, 2)) * 80.0 + 10.0
    zp = 0.3 * xy[:, 0] + 0.2 * xy[:, 1] + 5.0
    on = np.column_stack([xy, zp])
    assert np.max(np.abs(m.field(on))) <= 1e-6 * H
    up = np.column_stack([xy, zp + 1.0])
    f0, f1 = m.field(on), m.field(up)
    z_root = zp - f0 * (1.0 / (f1 - f0))  # secant between the plane and 1 m above it
    assert np.max(np.abs(z_root - zp)) <= 1e-6 * H
    q = rng.random((2000, 3)) * np.array([80.0, 80.0, 30.0]) + np.array([10.0, 10.0, 5.0])
    assert np.max(np.abs(m.field(q) - plane_distance(q))) <= 1e-6 * H


def test_field_equals_independent_numpy_partition_of_unity(sphere: Model) -> None:
    """The kernel against a plain numpy Σ W_j f_j / Σ W_j built from `PatchSolution.evaluate` (1e-10 relative)."""
    rng = np.random.default_rng(4)
    q = rng.random((1500, 3)) * 140.0 - 70.0
    got = sphere.field(q)
    ref = reference(sphere, q)
    assert np.array_equal(np.isnan(got), np.isnan(ref))
    ok = ~np.isnan(ref)
    assert ok.sum() > 800
    assert np.max(np.abs(got[ok] - ref[ok])) <= 1e-10 * np.max(np.abs(ref[ok]))


@pytest.mark.parametrize("kind", ["cubic", "wendland"])
def test_other_kernels_against_numpy(kind: str) -> None:
    m = Model(*sphere_data(400), kind=kind, n_patch=120, n_min=70)
    rng = np.random.default_rng(6)
    q = rng.random((600, 3)) * 100.0 - 50.0
    got, ref = m.field(q), reference(m, q)
    ok = ~np.isnan(ref)
    assert np.max(np.abs(got[ok] - ref[ok])) <= 1e-9 * np.max(np.abs(ref[ok]))


def test_sphere_field_is_the_signed_distance_on_the_sphere(sphere: Model) -> None:
    """Т8.9-1 on the field (the isosurface itself is 19.5): at random points of the sphere R = 50 m the zero level of
    the field is within 0.25·h of them (|f| is the distance to the level, the gradient being 1), and the sign is that
    of the distance to the centre minus R."""
    rng = np.random.default_rng(8)
    d = rng.normal(size=(1500, 3))
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    assert np.max(np.abs(sphere.field(50.0 * d))) <= 0.25 * H
    q = rng.random((3000, 3)) * 120.0 - 60.0
    r = np.linalg.norm(q, axis=1)
    f = sphere.field(q)
    far = np.abs(r - 50.0) > 3.0
    assert np.all(np.sign(f[far]) == np.sign(r[far] - 50.0))


def test_field_is_continuous_across_the_balls_of_the_patches(sphere: Model) -> None:
    """The PoU is C¹: a dense scan across several balls shows no jump (the data are a distance field, |∇f| ≈ 1), and a
    patch entering or leaving the sum at r = 1 moves the field by far less than the step."""
    ps = sphere.ps
    for line in range(3):
        a = np.array([-70.0 + 5.0 * line, -60.0, 10.0 * line - 10.0])
        b = np.array([70.0, 60.0 - 8.0 * line, 15.0])
        t = np.linspace(0.0, 1.0, 40001)
        q = a + t[:, None] * (b - a)
        f = sphere.field(q)
        assert not np.isnan(f).any()
        step = np.linalg.norm(b - a) / (len(t) - 1)
        assert np.max(np.abs(np.diff(f))) <= 2.0 * step + 1e-9  # a 1-Lipschitz field, with room for the interpolation
    # at the boundary of one ball: its weight is 0 there, the value just outside and just inside agree to 1e-9·h
    j = ps.n_patches // 3
    c, rad = ps.centers[j], ps.radii[j]
    dirs = np.random.default_rng(12).normal(size=(200, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    inside = c + (rad * (1.0 - 1e-9)) * dirs
    outside = c + (rad * (1.0 + 1e-9)) * dirs
    fi, fo = sphere.field(inside), sphere.field(outside)
    ok = ~np.isnan(fi) & ~np.isnan(fo)
    assert ok.sum() > 100 and np.max(np.abs(fi[ok] - fo[ok])) <= 1e-6 * H


def test_outside_every_patch_is_nan_never_zero(sphere: Model) -> None:
    ps = sphere.ps
    far = np.array([[1.0e4, 0.0, 0.0], [0.0, -5.0e3, 2.0e3], [3.0e5, 3.0e5, 3.0e5]])
    assert np.isnan(sphere.field(far)).all()
    nanq = np.array([[np.nan, 0.0, 0.0], [0.0, np.inf, 0.0], [0.0, 0.0, 0.0]])
    out = sphere.field(nanq)
    assert np.isnan(out[:2]).all() and np.isfinite(out[2])
    # the whole extent of the model is covered: no NaN inside it (the balls hold their leaves with a margin)
    rng = np.random.default_rng(14)
    q = ps.extent_min + rng.random((4000, 3)) * (ps.extent_max - ps.extent_min)
    assert not np.isnan(sphere.field(q)).any()
    assert sphere.field.inside(q).all() and not sphere.field.inside(far).any()
    # a model whose balls leave a gap: the points in the gap are NaN, not 0
    near = ps.extent_max + 0.5 * (ps.extent_max - ps.extent_min)
    assert np.isnan(sphere.field(near[None, :])).all()


def test_threads_1_2_4_8_and_any_order_and_any_split_give_the_same_bytes(sphere: Model) -> None:
    """Т8.9-8: the same bytes on 1, 2, 4, 8 threads; a point's value does not depend on the other points, their order
    or the bands: shuffled points, a call in pieces, and the point alone."""
    rng = np.random.default_rng(21)
    n = 3 * BAND + 777  # several bands, a partial one at the end; the points are ordered by their nearest patch inside
    q = rng.random((n, 3)) * 150.0 - 75.0
    q[::97] = np.nan
    ref = sphere.field(q, threads=1)
    assert np.isnan(ref[::97]).all()
    for threads in (2, 4, 8):
        assert np.array_equal(sphere.field(q, threads=threads), ref, equal_nan=True), threads
    perm = rng.permutation(n)
    assert np.array_equal(sphere.field(q[perm], threads=3)[np.argsort(perm)], ref, equal_nan=True)
    pieces = np.concatenate([sphere.field(q[a : a + 1500]) for a in range(0, n, 1500)])
    assert np.array_equal(pieces, ref, equal_nan=True)
    for i in (1, 500, n - 1):
        assert np.array_equal(sphere.field(q[i : i + 1]), ref[i : i + 1], equal_nan=True)
    assert np.array_equal(evaluate(sphere.ps, sphere.sols, q[:100], threads=2), ref[:100], equal_nan=True)


def test_field_does_not_depend_on_the_input_order_of_the_data_after_canonicalisation() -> None:
    pts, f = sphere_data(300)
    perm = np.random.default_rng(3).permutation(len(f))
    pts2, f2 = canonical(pts[perm], f[perm])
    q = np.random.default_rng(4).random((5000, 3)) * 100.0 - 50.0
    a, b = Model(pts, f), Model(pts2, f2)
    assert np.array_equal(a.field(q, threads=2), b.field(q, threads=4), equal_nan=True)


def test_tick_after_every_band_and_cancel(sphere: Model) -> None:
    q = np.random.default_rng(1).random((2 * BAND + 5, 3)) * 100.0 - 50.0
    ticks: list[int] = []
    sphere.field(q, tick=lambda: ticks.append(1))
    assert len(ticks) == 3

    class Stop(Exception):
        pass

    def tick() -> None:
        ticks.append(1)
        if len(ticks) == 5:
            raise Stop

    for threads in (1, 4):
        ticks.clear()
        before = threading.active_count()
        with pytest.raises(Stop):
            sphere.field(np.tile(q, (3, 1)), threads=threads, tick=tick)
        assert len(ticks) == 5 and threading.active_count() == before


def test_input_shapes_and_empty(sphere: Model) -> None:
    assert sphere.field(np.empty((0, 3))).shape == (0,)
    one = sphere.field([[0.0, 0.0, 0.0]])
    assert one.shape == (1,) and one.dtype == np.float64
    ro = np.zeros((3, 3))
    ro.setflags(write=False)
    assert sphere.field(ro).shape == (3,)
    for bad in (np.zeros(3), np.zeros((4, 2)), np.zeros((2, 3, 3))):
        with pytest.raises(ApiError) as e:
            sphere.field(bad)
        assert e.value.code == "bad_param"


def test_terms_counts_the_sources_of_the_patches_that_reach_the_points(sphere: Model) -> None:
    q = np.random.default_rng(1).random((300, 3)) * 100.0 - 50.0
    expect = 0
    for j in range(sphere.ps.n_patches):
        r = np.linalg.norm(q - sphere.ps.centers[j], axis=1) / sphere.ps.radii[j]
        expect += int((r < 1.0).sum()) * len(sphere.ps.patch(j))
    assert sphere.field.terms(q) == expect > 0


def test_global_matrix_a_is_applied_once() -> None:
    """`patches.matrix`: the points of the field are project coordinates; the field equals that of the caller who
    turned both the data and the query points himself."""
    a = np.array([[1.0, 0.1, 0.0], [0.0, 0.8, 0.1], [0.05, 0.0, 0.5]])
    pts, f = sphere_data(400)
    m = Model(pts, f, matrix=a)
    turned = Model(transform_points(pts, a), f)
    q = np.random.default_rng(2).random((2000, 3)) * 100.0 - 50.0
    assert np.array_equal(m.field(q), turned.field(transform_points(q, a)), equal_nan=True)
    assert np.max(np.abs(m.field(pts) - f)) <= 1e-8 * np.max(np.abs(f))


def test_patch_matrices_hook_matches_the_per_patch_reference(sphere: Model) -> None:
    """The anisotropy A_j of a patch (19.9) turns `x − c_j` before the kernel; the reference is `evaluate` of the patch
    solved in the space of A_j·x, with the weights of the original space."""
    rng = np.random.default_rng(15)
    ps = sphere.ps
    mats: list[F8 | None] = [None] * ps.n_patches
    for j in range(0, ps.n_patches, 2):
        th = 0.3 * (j + 1)
        rot = np.array([[np.cos(th), -np.sin(th), 0.0], [np.sin(th), np.cos(th), 0.0], [0.0, 0.0, 1.0]])
        mats[j] = rot @ np.diag([1.0, 1.3, 0.7])
    sols = solve_patches(sphere.pts, sphere.f, ps, kind="linear", matrices=mats, threads=2)
    field = PatchField(ps, sols, matrices=mats)
    twin = Model.__new__(Model)
    twin.pts, twin.f, twin.ps, twin.sols = sphere.pts, sphere.f, ps, sols
    q = rng.random((1200, 3)) * 120.0 - 60.0
    got, ref = field(q), reference(twin, q, matrices=mats)
    ok = ~np.isnan(ref)
    assert np.array_equal(np.isnan(got), ~ok)
    assert np.max(np.abs(got[ok] - ref[ok])) <= 1e-9 * np.max(np.abs(ref[ok]))
    assert np.max(np.abs(field(sphere.pts) - sphere.f)) <= 1e-8 * np.max(np.abs(sphere.f))  # still interpolates
    assert not np.array_equal(got, sphere.field(q), equal_nan=True)


def test_constructor_checks() -> None:
    m = Model(*sphere_data(200), n_patch=100, n_min=60)
    with pytest.raises(ApiError) as e:
        PatchField(m.ps, m.sols[:-1])
    assert e.value.code == "bad_param"
    with pytest.raises(ApiError):
        PatchField(m.ps, m.sols, matrices=[None])
    other = solve_patches(m.pts, m.f, m.ps, kind="cubic")
    with pytest.raises(ApiError):
        PatchField(m.ps, m.sols[:1] + other[1:])  # two kernels in one model
    moved = PatchSolution(**{**m.sols[0].__dict__, "centre": m.sols[0].centre + 1.0})
    with pytest.raises(ApiError):
        PatchField(m.ps, [moved, *m.sols[1:]])  # a solution around another centre without a matrix


def test_hard_data_huge_offset_and_clusters() -> None:
    """A dense core, a thin sheet, outliers, coordinates of 5e6–7e6 m: the field interpolates the data and has no NaN
    inside the extent."""
    rng = np.random.default_rng(11)
    core = rng.normal(0.0, 5.0, (500, 3))
    sheet = np.column_stack([rng.random(400) * 600, rng.random(400) * 600, 100 + rng.normal(0, 0.05, 400)])
    far = rng.random((100, 3)) * 3000 + 1000
    xyz = np.vstack([core, sheet, far]) + np.array([5.0e6, 7.0e6, 300.0])
    f = np.sin(0.01 * (xyz[:, 0] - 5.0e6)) * 20 + 0.05 * (xyz[:, 2] - 300.0)
    xyz, f = canonical(xyz, f)
    m = Model(xyz, f, n_patch=100, n_min=60)
    assert np.max(np.abs(m.field(xyz) - f)) <= 1e-8 * np.max(np.abs(f))
    q = m.ps.extent_min + np.random.default_rng(2).random((3000, 3)) * (m.ps.extent_max - m.ps.extent_min)
    assert not np.isnan(m.field(q, threads=4)).any()


def test_module_files_are_small_and_numba_is_lazy() -> None:
    for name in ("evaluate.py", "solve.py", "field_kernel.py"):
        assert (CORE / "bf_core" / "model" / name).read_text(encoding="utf-8").count("\n") <= 600
    assert not hasattr(ev, "field_kernel")  # the kernel module is imported inside the call, not at the import
