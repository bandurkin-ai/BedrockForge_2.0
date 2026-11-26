"""Э19, 19.3: octree and patches of the RBF partition of unity (ADR 0026, decision 8; ТЗ 8.9.2, 8.9.4).

References are structural (exact tiling of the extent, ball ⊇ leaf box, counts) and bitwise (permutation of the
canonically ordered input, threads), not numeric fits.
"""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from bf_core.api.errors import ApiError
from bf_core.model import octree
from bf_core.model.octree import GROWTH, PatchSet, build_patches, hash_patches, transform_extent, transform_points

F8 = NDArray[np.float64]
CORE = Path(__file__).resolve().parents[1]


def canonical(points: F8, f: F8 | None = None) -> tuple[F8, F8]:
    """The caller's canonical order (ADR 0026, decision 4): (x, y, z, f, type), lexicographic."""
    f = np.zeros(len(points)) if f is None else f
    order = np.lexsort((f, points[:, 2], points[:, 1], points[:, 0]))
    return np.ascontiguousarray(points[order]), np.ascontiguousarray(f[order])


def cloud(n: int, seed: int = 12345, scale: float = 1000.0, origin: float = 0.0) -> F8:
    rng = np.random.default_rng(seed)
    return origin + rng.random((n, 3)) * scale * np.array([1.0, 0.8, 0.3])


def clustered(n: int, seed: int = 7) -> F8:
    """Hard data: a dense core, a thin sheet, a few outliers far away (non-uniform octree)."""
    rng = np.random.default_rng(seed)
    core = rng.normal(0.0, 5.0, (n // 2, 3))
    sheet = np.column_stack([rng.random(n // 3) * 800, rng.random(n // 3) * 800, 100 + rng.normal(0, 0.01, n // 3)])
    far = rng.random((n - len(core) - len(sheet), 3)) * 5000 + 2000
    return np.vstack([core, sheet, far])


def membership(ps: PatchSet, n: int) -> NDArray[np.int64]:
    return np.bincount(ps.indices, minlength=n)


def same(a: PatchSet, b: PatchSet) -> bool:
    return (
        np.array_equal(a.centers, b.centers)
        and np.array_equal(a.radii, b.radii)
        and np.array_equal(a.indptr, b.indptr)
        and np.array_equal(a.indices, b.indices)
        and np.array_equal(a.leaf_min, b.leaf_min)
        and np.array_equal(a.leaf_max, b.leaf_max)
        and np.array_equal(a.leaf_counts, b.leaf_counts)
        and a.hashes == b.hashes
    )


# ---------------------------------------------------------------- permutation


@pytest.mark.parametrize("make", [lambda: cloud(6000), lambda: clustered(6000)], ids=["uniform", "clustered"])
def test_permutation_after_canonicalisation_is_bitwise_same(make) -> None:  # type: ignore[no-untyped-def]
    pts = make()
    rng = np.random.default_rng(1)
    f = rng.normal(size=len(pts))
    base_pts, _ = canonical(pts, f)
    ref = build_patches(base_pts, n_patch=400, n_min=50, delta=0.25)
    for seed in (2, 3, 4):
        perm = np.random.default_rng(seed).permutation(len(pts))
        got_pts, _ = canonical(pts[perm], f[perm])
        assert np.array_equal(got_pts, base_pts)
        assert same(ref, build_patches(got_pts, n_patch=400, n_min=50, delta=0.25))


def test_permutation_without_canonicalisation_gives_same_point_sets() -> None:
    """Even a non-canonical order does not change the geometry: each patch holds the same coordinates."""
    pts = cloud(3000)
    ref = build_patches(pts, n_patch=300, n_min=40)
    perm = np.random.default_rng(5).permutation(len(pts))
    got = build_patches(pts[perm], n_patch=300, n_min=40)
    assert np.array_equal(ref.centers, got.centers) and np.array_equal(ref.radii, got.radii)
    for j in range(ref.n_patches):
        a = np.sort(pts[ref.patch(j)], axis=0)
        b = np.sort(pts[perm][got.patch(j)], axis=0)
        assert np.array_equal(a, b)


def test_input_not_modified_and_readonly_accepted() -> None:
    pts = cloud(2000)
    pts.setflags(write=False)
    before = pts.copy()
    build_patches(pts, n_patch=300, n_min=40)
    assert np.array_equal(pts, before)


# ---------------------------------------------------------------- coverage and structure


@pytest.mark.parametrize("make", [lambda: cloud(8000), lambda: clustered(8000)], ids=["uniform", "clustered"])
def test_every_point_in_some_patch_and_leaves_tile_the_extent(make) -> None:  # type: ignore[no-untyped-def]
    pts, _ = canonical(make())
    ps = build_patches(pts, n_patch=500, n_min=60, delta=0.25)
    assert membership(ps, len(pts)).min() >= 1
    # the leaves tile the extent +10 %: volumes add up, every leaf lies inside it
    box = ps.extent_max - ps.extent_min
    vol = np.prod(ps.leaf_max - ps.leaf_min, axis=1)
    assert vol.sum() == pytest.approx(np.prod(box), rel=1e-12)
    assert (ps.leaf_min >= ps.extent_min - 1e-9).all() and (ps.leaf_max <= ps.extent_max + 1e-9).all()
    # the sum of leaf counts is every point exactly once
    assert ps.leaf_counts.sum() == len(pts)
    # random probe points of the extent: each is inside >= 1 patch ball (the PoU is defined everywhere there)
    probes = ps.extent_min + np.random.default_rng(9).random((4000, 3)) * box
    d = np.linalg.norm(probes[:, None, :] - ps.centers[None, :, :], axis=2)
    assert (d <= ps.radii[None, :]).any(axis=1).all()
    # each ball contains its whole leaf box (all 8 corners)
    corners = np.stack([np.where(np.array(c), ps.leaf_max, ps.leaf_min)
                        for c in np.ndindex(2, 2, 2)])  # fmt: skip
    dist = np.linalg.norm(corners - ps.centers[None], axis=2)
    assert (dist <= ps.radii[None, :] * (1 + 1e-12)).all()


def test_extent_is_union_of_given_extent_and_points_plus_ten_percent() -> None:
    pts = cloud(1000)
    lo, hi = np.array([0.0, 0.0, 0.0]), np.array([1000.0, 800.0, 300.0])
    ps = build_patches(pts, n_patch=300, n_min=40, extent=(lo, hi))
    np.testing.assert_allclose(ps.extent_min, lo - 0.1 * (hi - lo), atol=1e-9)
    np.testing.assert_allclose(ps.extent_max, hi + 0.1 * (hi - lo), atol=1e-9)
    # data outside the given extent is never lost: the extent grows to the points
    ps2 = build_patches(pts, n_patch=300, n_min=40, extent=(lo + 100, hi - 100))
    assert (ps2.extent_min <= pts.min(axis=0)).all() and (ps2.extent_max >= pts.max(axis=0)).all()
    assert membership(ps2, len(pts)).min() >= 1


def test_leaves_have_at_most_n_patch_points() -> None:
    pts, _ = canonical(clustered(9000))
    ps = build_patches(pts, n_patch=300, n_min=40)
    assert ps.leaf_counts.max() <= 300
    # an octree is minimal: a leaf is split only when it holds more than n_patch points, so the mean is high
    assert ps.n_patches >= len(pts) // 300


def sheet_and_core() -> F8:
    """Hard data of 19.4b: a big sparse leaf beside a dense plane (drill holes through wavy surfaces)."""
    rng = np.random.default_rng(5)
    dense = np.column_stack([rng.random(6000) * 100, rng.random(6000) * 100, rng.normal(0, 1.0, 6000)])
    sparse = rng.random((400, 3)) * np.array([600.0, 600.0, 150.0]) + np.array([0.0, 0.0, 20.0])
    return np.vstack([dense, sparse])


def test_ball_of_a_patch_is_bounded_in_n_patch() -> None:
    pts, _ = canonical(sheet_and_core())
    old = build_patches(pts, n_patch=300, n_min=40, ball_factor=None)
    new = build_patches(pts, n_patch=300, n_min=40, ball_factor=3.0)
    assert old.sizes.max() > 3 * 300  # the case exists: without the bound the patch is far larger
    # a patch that did not grow obeys the bound exactly; a grown one holds ~n_min points (the least radius), so all do
    r0 = 1.25 * 0.5 * np.linalg.norm(new.leaf_max - new.leaf_min, axis=1)
    assert (new.sizes[new.radii == r0] <= 3 * 300).all()
    assert new.sizes.max() <= 3 * 300
    assert new.sizes.max() < old.sizes.max() / 2
    assert new.n_patches > old.n_patches
    assert new.leaf_counts.sum() == len(pts) and membership(new, len(pts)).min() >= 1
    lo, hi = new.leaf_min, new.leaf_max  # leaves still tile the extent exactly
    vol = float(np.sum(np.prod(hi - lo, axis=1)))
    assert vol == pytest.approx(float(np.prod(new.extent_max - new.extent_min)), rel=1e-9)
    again = build_patches(pts, n_patch=300, n_min=40, ball_factor=3.0)
    assert same(new, again)


def test_dense_duplicates_stop_at_max_depth() -> None:
    """2000 coincident points cannot be split: the depth cap stops the descent, no hang, every point is covered."""
    pts = np.vstack([np.full((2000, 3), 5.0), cloud(500, seed=3)])
    ps = build_patches(pts, n_patch=300, n_min=40, max_depth=12)
    assert membership(ps, len(pts)).min() >= 1
    assert ps.leaf_counts.max() >= 2000
    assert ps.n_patches < 1000  # the ball bound keeps splitting the empty neighbours of the cluster down to the cap
    off = build_patches(pts, n_patch=300, n_min=40, max_depth=12, ball_factor=None)
    assert off.n_patches < 200


def test_patch_order_is_morton_depth_first_and_stable() -> None:
    pts, _ = canonical(cloud(5000))
    a = build_patches(pts, n_patch=300, n_min=40)
    b = build_patches(pts, n_patch=300, n_min=40)
    assert same(a, b)
    # the order is fixed: the first leaf holds the lower corner of the extent, the last the upper one
    assert (a.leaf_min[0] == a.extent_min).all()
    assert (a.leaf_max[-1] == a.extent_max).all()


# ---------------------------------------------------------------- N_min and radii


def test_n_min_is_met_with_the_least_radius_not_under_r0() -> None:
    """R = R0 when the ball of R0 holds n_min points, else the distance to the n_min-th nearest point (the least R that
    does; between the last two steps of the ×1.25 rule of ТЗ 8.9.2, review 19.29б)."""
    pts, _ = canonical(clustered(6000))
    n_min, delta = 80, 0.25
    ps = build_patches(pts, n_patch=400, n_min=n_min, delta=delta)
    sizes = np.diff(ps.indptr)
    assert sizes.min() >= n_min
    r0 = (1 + delta) * 0.5 * np.linalg.norm(ps.leaf_max - ps.leaf_min, axis=1)
    assert (ps.radii >= r0).all()
    grown = np.flatnonzero(ps.radii > r0)
    assert grown.size > 5, "the clustered set must have patches that needed growing"
    for j in grown:
        d = np.sort(np.linalg.norm(pts - ps.centers[j], axis=1))
        assert d[n_min - 1] <= ps.radii[j] <= d[n_min - 1] * (1 + 1e-8)  # the n_min-th point is at the edge
        assert (d <= r0[j]).sum() < n_min  # R0 did not hold n_min: the old rule would have grown it by 1.25^k
        # inside the old rule's radius: the first step 1.25^k·R0 that holds n_min points is not smaller
        k = np.ceil(np.log(d[n_min - 1] / r0[j]) / np.log(GROWTH) - 1e-12)
        assert ps.radii[j] <= r0[j] * GROWTH**k * (1 + 1e-8)
    kept = np.flatnonzero(ps.radii == r0)
    assert kept.size and (sizes[kept] >= n_min).all()


def test_a_sparse_patch_next_to_a_dense_core_does_not_swallow_it() -> None:
    """Review 19.29б: the ball of an empty leaf was grown ×1.25 under n_min, the last step jumped over the edge of a
    dense core and the patch held the whole core (16 000 points for n_patch = 1000). The least radius for n_min stops
    at ~n_min points: no patch is over ball_factor · n_patch."""
    rng = np.random.default_rng(1)
    core = rng.normal(0, 3.0, (8000, 3)) + np.array([60.0, -40.0, 20.0])
    sparse = rng.uniform(-200, 200, (1200, 3))
    pts, _ = canonical(np.vstack([core, sparse]))
    ps = build_patches(pts, n_patch=300, n_min=40, delta=0.25, extent=([-200.0] * 3, [200.0] * 3))
    assert ps.sizes.max() <= 3 * 300
    assert ps.leaf_counts.sum() == len(pts) and membership(ps, len(pts)).min() >= 1
    assert same(ps, build_patches(pts, n_patch=300, n_min=40, delta=0.25, extent=([-200.0] * 3, [200.0] * 3)))


def test_patch_points_are_exactly_the_ball_in_ascending_order() -> None:
    pts, _ = canonical(cloud(4000))
    ps = build_patches(pts, n_patch=300, n_min=40)
    for j in (0, 1, ps.n_patches // 2, ps.n_patches - 1):
        d = np.linalg.norm(pts - ps.centers[j], axis=1)
        expected = np.flatnonzero(d <= ps.radii[j])
        assert np.array_equal(ps.patch(j), expected)
        assert (np.diff(ps.patch(j)) > 0).all()
    assert ps.indices.dtype == np.int64 and ps.indptr.dtype == np.int64


def test_input_with_exactly_n_min_points_gives_patches_with_all_of_them() -> None:
    pts, _ = canonical(cloud(40, seed=2))
    ps = build_patches(pts, n_patch=300, n_min=40)
    assert (np.diff(ps.indptr) == 40).all()


# ---------------------------------------------------------------- errors (ADR: {code, message, hint}, Russian)


def _err(**kw) -> ApiError:  # type: ignore[no-untyped-def]
    with pytest.raises(ApiError) as e:
        build_patches(**kw)
    err = e.value
    assert err.status == 422 and err.code and err.message and err.hint
    assert any("а" <= ch <= "я" for ch in err.message.lower()), "the text must be Russian"
    assert any("а" <= ch <= "я" for ch in err.hint.lower())
    return err


def test_empty_input_is_a_clear_error() -> None:
    assert _err(points=np.empty((0, 3))).code == "rbf_no_points"


def test_input_smaller_than_n_min_is_a_clear_error() -> None:
    err = _err(points=cloud(30), n_min=100)
    assert err.code == "rbf_few_points"
    assert "30" in err.message and "100" in err.message


@pytest.mark.parametrize(
    "bad",
    [np.zeros((5, 2)), np.zeros(9), np.array([[0.0, np.nan, 1.0]] * 200), np.array([[0.0, np.inf, 1.0]] * 200)],
    ids=["two-columns", "flat", "nan", "inf"],
)
def test_bad_points_shape_or_values(bad: F8) -> None:
    assert _err(points=bad).code == "rbf_bad_points"


@pytest.mark.parametrize(
    "kw",
    [
        {"n_patch": 0},
        {"n_min": 0},
        {"delta": -0.1},
        {"delta": float("nan")},
        {"max_depth": 0},
        {"ball_factor": 0.5},
        {"ball_factor": float("nan")},
    ],
)
def test_bad_parameters(kw: dict[str, float]) -> None:
    assert _err(points=cloud(500), **kw).code == "bad_param"


def test_singular_or_bad_matrix() -> None:
    assert _err(points=cloud(500), matrix=np.diag([1.0, 1.0, 0.0])).code == "bad_param"
    assert _err(points=cloud(500), matrix=np.eye(2)).code == "bad_param"
    assert _err(points=cloud(500), matrix=np.full((3, 3), np.nan)).code == "bad_param"


def test_bad_extent() -> None:
    lo, hi = np.zeros(3), np.ones(3)
    assert _err(points=cloud(500), extent=(hi, lo)).code == "bad_param"
    assert _err(points=cloud(500), extent=(lo, np.array([1.0, np.nan, 1.0]))).code == "bad_param"


# ---------------------------------------------------------------- hard geometry


def test_planar_collinear_and_huge_offset_data() -> None:
    rng = np.random.default_rng(3)
    planar = np.column_stack([rng.random(3000) * 500, rng.random(3000) * 500, np.full(3000, 42.0)])
    line = np.column_stack([np.linspace(0, 900, 3000), np.full(3000, 7.0), np.full(3000, 1.0)])
    utm = cloud(3000, origin=6_000_000.0)  # UTM-like offsets: float64 keeps the metre-level structure
    for pts in (planar, line, utm):
        ps = build_patches(pts, n_patch=300, n_min=40)
        assert membership(ps, len(pts)).min() >= 1
        assert np.isfinite(ps.centers).all() and (ps.radii > 0).all()
        assert np.diff(ps.indptr).min() >= 40


def test_single_distinct_point_repeated() -> None:
    ps = build_patches(np.full((150, 3), 3.0), n_patch=300, n_min=100)
    assert ps.n_patches >= 1 and np.diff(ps.indptr).min() >= 100
    assert membership(ps, 150).min() >= 1


# ---------------------------------------------------------------- matrix A


def test_matrix_applied_inside_equals_pretransformed_by_caller() -> None:
    pts, _ = canonical(cloud(3000))
    ang = np.deg2rad(30.0)
    rot = np.array([[np.cos(ang), -np.sin(ang), 0], [np.sin(ang), np.cos(ang), 0], [0, 0, 1.0]])
    a = np.diag([1.0, 2.0, 4.0]) @ rot
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    inside = build_patches(pts, n_patch=300, n_min=40, extent=(lo, hi), matrix=a)
    pre = build_patches(transform_points(pts, a), n_patch=300, n_min=40, extent=transform_extent(lo, hi, a))
    assert np.array_equal(inside.centers, pre.centers) and np.array_equal(inside.indices, pre.indices)
    assert np.array_equal(inside.radii, pre.radii) and inside.hashes == pre.hashes
    assert np.array_equal(inside.matrix, a) and pre.matrix is None
    assert membership(inside, len(pts)).min() >= 1


def test_transform_extent_is_the_box_of_the_eight_corners() -> None:
    a = np.array([[1.0, 0.5, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 3.0]])
    lo, hi = np.array([-1.0, -2.0, 0.0]), np.array([3.0, 4.0, 1.0])
    out_lo, out_hi = transform_extent(lo, hi, a)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    moved = corners @ a.T
    np.testing.assert_allclose(out_lo, moved.min(axis=0), atol=1e-12)
    np.testing.assert_allclose(out_hi, moved.max(axis=0), atol=1e-12)
    np.testing.assert_allclose(transform_points(corners, a), moved, atol=1e-12)


# ---------------------------------------------------------------- hashes (cache of updates, 19.12)


def test_hash_changes_only_for_patches_containing_the_changed_point() -> None:
    pts, f = canonical(cloud(6000), np.random.default_rng(4).normal(size=6000))
    ps = build_patches(pts, n_patch=400, n_min=50)
    h0 = hash_patches(pts, ps, values=f)
    assert len(set(h0)) == len(h0), "hashes of distinct patches are distinct"
    k = 1234
    inside = [j for j in range(ps.n_patches) if k in set(ps.patch(j).tolist())]
    f2 = f.copy()
    f2[k] += 1.0
    h1 = hash_patches(pts, ps, values=f2)
    assert {j for j in range(ps.n_patches) if h0[j] != h1[j]} == set(inside)
    # a moved point (the order and the tree unchanged) does the same on the points-only hashes of the build
    moved = pts.copy()
    moved[k] += np.array([1e-6, 0.0, 0.0])
    ps2 = build_patches(moved, n_patch=400, n_min=50)
    assert np.array_equal(ps2.centers, ps.centers) and np.array_equal(ps2.radii, ps.radii)
    assert {j for j in range(ps.n_patches) if ps.hashes[j] != ps2.hashes[j]} == set(inside)


def test_hash_depends_on_salt_values_and_is_hex_sha256() -> None:
    pts, f = canonical(cloud(2000), np.random.default_rng(1).normal(size=2000))
    ps = build_patches(pts, n_patch=300, n_min=40)
    plain = hash_patches(pts, ps)
    assert plain == list(ps.hashes), "the hashes of the build are the points-only hashes"
    assert all(len(h) == 64 and int(h, 16) >= 0 for h in plain)
    assert hash_patches(pts, ps, values=f) != plain
    assert hash_patches(pts, ps, salt=b"kernel=linear") != plain
    assert hash_patches(pts, ps, salt=b"kernel=linear") != hash_patches(pts, ps, salt=b"kernel=cubic")
    assert hash_patches(pts, ps, values=f, salt=b"x") == hash_patches(pts, ps, values=f, salt=b"x")
    with pytest.raises(ApiError):
        hash_patches(pts, ps, values=f[:-1])


# ---------------------------------------------------------------- threads, cancellation, imports


def test_threads_do_not_affect_the_result() -> None:
    from threadpoolctl import threadpool_limits

    pts, _ = canonical(clustered(8000))
    ref = build_patches(pts, n_patch=400, n_min=50)

    def run(limit: int) -> PatchSet:
        with threadpool_limits(limits=limit):
            return build_patches(pts, n_patch=400, n_min=50)

    for limit in (1, 2, 4, 8):
        assert same(ref, run(limit))
    with ThreadPoolExecutor(max_workers=8) as pool:  # concurrent calls: no shared state
        for got in pool.map(lambda _: build_patches(pts, n_patch=400, n_min=50), range(8)):
            assert same(ref, got)


def test_tick_is_called_and_can_cancel() -> None:
    calls: list[int] = []
    pts = cloud(4000)
    build_patches(pts, n_patch=300, n_min=40, tick=lambda: calls.append(1))
    assert len(calls) > 5

    class Cancelled(Exception):
        pass

    def stop() -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        build_patches(pts, n_patch=300, n_min=40, tick=stop)


def test_result_is_immutable_and_arrays_readonly() -> None:
    ps = build_patches(cloud(1000), n_patch=300, n_min=40)
    for arr in (ps.centers, ps.radii, ps.indptr, ps.indices, ps.leaf_min, ps.leaf_max, ps.leaf_counts):
        assert not arr.flags.writeable
    with pytest.raises(AttributeError):
        ps.centers = ps.centers  # type: ignore[misc]


def test_import_does_not_load_numba() -> None:
    code = "import bf_core.model.octree, sys; raise SystemExit(3 if 'numba' in sys.modules else 0)"
    run = subprocess.run([sys.executable, "-c", code], cwd=CORE, capture_output=True, text=True, timeout=120,
                         env={"PYTHONPATH": str(CORE), "BF_NO_WARMUP": "1", "PATH": "/usr/bin:/bin"})  # fmt: skip
    assert run.returncode == 0, run.stderr[-300:]


def test_module_is_small() -> None:
    assert len(Path(octree.__file__).read_text(encoding="utf-8").splitlines()) <= 600
