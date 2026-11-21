"""The nearest triangle of a wireframe to a point (ТЗ 8.5.2 «ближайшая к середине интервала точка каркаса»;
ADR 0021 §5, решение 8; `geo/nearest.py`): against a full search on UTM-sized coordinates — an independent
formula in numpy (1e-9 m) and the formula of the kernel itself in plain Python (bit for bit, the rule of equal
distances included); nothing beyond d_max; triangles without area; the wireframes one at a time; the normals
averaged by area within r_n; the same bytes with 1, 2, 4 and 8 threads and in any order of the points."""

from __future__ import annotations

import hashlib
import math

import numpy as np
import pytest

from bf_core.geo import nearest as gn
from bf_core.geo.deviation import point_triangle_d2
from bf_core.geo.intersect import MeshTarget
from tests.meshutil import UTM, cube, grid, icosphere, stored

TOL = 1e-9  # metres: the distance to the wireframe against the full search
NAN = math.nan
WHOLE = np.array([512_000.0, 6_234_000.0, 400.0])  # an origin the small whole numbers of a test add to exactly


# --- references --------------------------------------------------------------------------------------------


def _segment(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Distances from the points p [k, 1, 3] to the segments a–b [m, 3]: [k, m]."""
    u = b - a
    uu = (u * u).sum(-1)
    s = np.clip(((p - a) * u).sum(-1) / np.where(uu > 0.0, uu, 1.0), 0.0, 1.0)
    return np.linalg.norm(p - a - s[..., None] * u, axis=-1)  # type: ignore[no-any-return]


def ref_distances(points: np.ndarray, v: np.ndarray, t: np.ndarray) -> np.ndarray:
    """The distance from every point to every triangle, [k, m]; inf for a triangle without area. Not the
    formula of the kernel: the foot of the perpendicular when it is inside the triangle (by the three signed
    areas), else the nearest of the three sides; float64 relative to the centre of the mesh."""
    centre = 0.5 * (v.min(axis=0) + v.max(axis=0))
    a, b, c = (v[t[:, k]] - centre for k in range(3))
    n = np.cross(b - a, c - a)
    nn = (n * n).sum(-1)
    flat = ~(nn > 0.0)
    unit = n / np.sqrt(np.where(flat, 1.0, nn))[:, None]
    out = np.empty((len(points), len(t)))
    for lo in range(0, len(points), 128):
        p = (points[lo : lo + 128] - centre)[:, None, :]
        h = ((p - a) * unit).sum(-1)
        foot = p - h[..., None] * unit
        inside = np.ones(h.shape, dtype=bool)
        for q, r in ((a, b), (b, c), (c, a)):
            inside &= (np.cross(q - foot, r - foot) * unit).sum(-1) >= 0.0
        sides = np.minimum(np.minimum(_segment(p, a, b), _segment(p, b, c)), _segment(p, c, a))
        d = np.where(inside, np.abs(h), sides)
        d[:, flat] = np.inf
        out[lo : lo + 128] = d
    return out


def brute(points: np.ndarray, v: np.ndarray, t: np.ndarray, d_max: float = math.inf) -> list[tuple[int, float]]:
    """(triangle, squared distance) of every point by the formula of the kernel in plain Python — the same
    float64 operations, so the same bits: the least (d², number) over the triangles with an area, (−1, nan)
    when none is within d_max."""
    d2 = point_triangle_d2.py_func
    area = np.linalg.norm(np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]]), axis=1) > 0.0
    corners = [tuple(float(x) for x in v[t[i]].reshape(-1)) for i in range(len(t))]
    out = []
    for p in points:
        best, best_d2 = -1, d_max * d_max
        if math.isfinite(float(p.sum())):
            for i in np.flatnonzero(area):
                d = d2(float(p[0]), float(p[1]), float(p[2]), *corners[i])
                if d < best_d2 or (d == best_d2 and best < 0):
                    best, best_d2 = int(i), d
        out.append((best, best_d2 if best >= 0 else NAN))
    return out


def on_triangle(q: np.ndarray, corners: np.ndarray, tol: float = 1e-8) -> bool:
    """Is the point q on the triangle (within tol metres)?"""
    a, b, c = corners - corners[0]
    p = q - corners[0]
    n = np.cross(b, c)
    area2 = float(np.linalg.norm(n))
    if abs(float(p @ n)) / area2 > tol:
        return False
    w = float(np.cross(b, p) @ n) / area2**2
    u = float(np.cross(p, c) @ n) / area2**2
    slack = tol / math.sqrt(area2)
    return u >= -slack and w >= -slack and u + w <= 1.0 + slack


def cloud(v: np.ndarray, n: int, reach: float, seed: int) -> np.ndarray:
    """Points around a mesh: in its box grown by `reach`, a third of them within centimetres of its vertices."""
    rng = np.random.default_rng(seed)
    lo, hi = v.min(axis=0) - reach, v.max(axis=0) + reach
    p = rng.uniform(lo, hi, size=(n, 3))
    near = rng.integers(0, len(v), n // 3)
    p[: len(near)] = v[near] + rng.normal(0.0, 0.02, size=(len(near), 3))
    return p


def check(res: gn.Nearest, points: np.ndarray, v: np.ndarray, t: np.ndarray, d_max: float = math.inf) -> int:
    """`res` against the full search; returns the number of points that found a triangle."""
    ref = ref_distances(points, v, t)
    least = ref.min(axis=1)
    found = res.found
    assert np.all(np.abs(least[found != (least <= d_max)] - d_max) < TOL)  # they differ at the limit itself only
    rows = np.flatnonzero(found)
    assert np.allclose(res.distance[rows], least[rows], rtol=0.0, atol=TOL)
    assert np.all(ref[rows, res.triangle[rows]] <= least[rows] + TOL)  # the triangle named is a nearest one
    second = np.partition(ref[rows], 1, axis=1)[:, 1] if len(t) > 1 else np.full(len(rows), np.inf)
    clear = second - least[rows] > 1e-6
    assert np.array_equal(res.triangle[rows][clear], ref[rows].argmin(axis=1)[clear])
    assert np.allclose(np.linalg.norm(points[rows] - res.point[rows], axis=1), res.distance[rows], rtol=0.0, atol=TOL)
    for i in rows[:: max(1, len(rows) // 200)]:
        corners = v[t[res.triangle[i]]]
        assert on_triangle(res.point[i], corners)
        n = np.cross(corners[1] - corners[0], corners[2] - corners[0])
        assert np.allclose(res.normal[i], n / np.linalg.norm(n), rtol=0.0, atol=1e-12)
    lost = ~found
    assert (res.triangle[lost] == -1).all() and (res.mesh[lost] == -1).all()
    assert np.isnan(res.d2[lost]).all() and np.isnan(res.point[lost]).all() and np.isnan(res.normal[lost]).all()
    return len(rows)


def digest(res: gn.Nearest) -> str:
    h = hashlib.sha256()
    for a in (res.mesh, res.triangle, res.d2, res.point, res.normal):
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def relief(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return 6.0 * np.sin(0.21 * x) * np.cos(0.17 * y) + 0.3 * x  # type: ignore[no-any-return]


# --- against the full search -------------------------------------------------------------------------------


@pytest.mark.parametrize("ordered", [False, True])
def test_surface_against_full_search_on_utm(ordered: bool) -> None:
    """A relief of 3200 triangles in the order of a file and in the order of the store: 1500 points around it."""
    v, t = grid(40, 40, relief, step=2.5)
    if ordered:
        v, t, _parts = stored(v, t)
    target = MeshTarget(v, t, ordered=ordered)
    points = cloud(v, 1500, 30.0, seed=1)
    res = gn.nearest_triangles(target, points)
    assert check(res, points, v, t) == len(points) and (res.mesh == 0).all()


def test_closed_solids_against_full_search() -> None:
    """A sphere and a cube: points inside, outside and on the faces; the vertices of the cube are whole steps."""
    for v, t, seed in ((*icosphere(3, 40.0), 2), (*cube(8, 64.0), 3)):
        points = cloud(v, 900, 25.0, seed=seed)
        k = min(len(v), len(points))
        points[-k:] = v[:k]  # the vertices themselves: distance 0
        res = gn.nearest_triangles(MeshTarget(v, t), points)
        assert check(res, points, v, t) == len(points)
        assert (res.distance[-k:] == 0.0).all()


def test_bit_for_bit_with_the_formula_and_equal_distances() -> None:
    """The kernel names the least (d², number): the same d² as the formula gives in plain Python, and of the
    triangles at exactly the same distance — around a vertex, a triangle given twice — the lowest number."""
    v, t = grid(9, 9, lambda x, y: 0.0 * x, step=1.0, seed=5)  # a level square: the vertices are equally far
    t = np.concatenate([t, t[:7]])  # seven triangles once more: equal distances whatever the point
    rng = np.random.default_rng(6)
    above = v[rng.integers(0, len(v), 60)] + np.array([0.0, 0.0, 1.0]) * rng.uniform(0.0, 9.0, 60)[:, None]
    beside = v[rng.integers(0, len(v), 40)] + rng.uniform(-12.0, 12.0, size=(40, 3))  # most are nearest to the rim
    points = np.concatenate([above, beside, cloud(v, 120, 6.0, seed=7)])
    want = brute(points, v, t)
    for ordered in (False, True):
        mesh = stored(v, t)[:2] if ordered else (v, t)
        res = gn.nearest_triangles(MeshTarget(*mesh, ordered=ordered), points)
        expect = want if not ordered else brute(points, *mesh)
        assert res.triangle.tolist() == [i for i, _d in expect]
        assert res.d2.tolist() == [d for _i, d in expect]
    ref = ref_distances(above, v, t)
    ties = np.count_nonzero(ref <= ref.min(axis=1)[:, None] + 1e-9, axis=1)
    assert (ties >= 2).all() and (ties >= 6).sum() > 20  # above a vertex: every triangle around it is as near


# --- d_max -------------------------------------------------------------------------------------------------


def test_beyond_d_max_is_empty() -> None:
    v, t = grid(20, 20, relief, step=2.0)
    target = MeshTarget(v, t)
    points = cloud(v, 1200, 80.0, seed=8)
    res = gn.nearest_triangles(target, points, 12.5)
    n = check(res, points, v, t, 12.5)
    assert 100 < n < len(points) - 100 and res.distance[res.found].max() <= 12.5
    assert check(gn.nearest_triangles(target, points, 0.0), points, v, t, 0.0) == 0
    far = points + np.array([0.0, 0.0, 500.0])
    none = gn.nearest_triangles(target, far, 50.0)
    assert not none.found.any() and np.isnan(none.distance).all() and len(none) == len(far)


def test_d_max_is_inclusive() -> None:
    """A level triangle, the point exactly 50 m above it: found with d_max = 50, not with the float before."""
    v = np.array([[0.0, 0.0, 0.0], [8.0, 0.0, 0.0], [0.0, 8.0, 0.0]]) + WHOLE
    target = MeshTarget(v, np.array([[0, 1, 2]], dtype=np.int32))
    p = np.array([[512_002.0, 6_234_002.0, 450.0], [512_000.0, 6_234_000.0, 450.0], [512_010.0, 6_234_000.0, 400.0]])
    res = gn.nearest_triangles(target, p, 50.0)
    assert res.triangle.tolist() == [0, 0, 0] and res.distance.tolist() == [50.0, 50.0, 2.0]
    assert res.point.tolist() == [[512_002.0, 6_234_002.0, 400.0], v[0].tolist(), v[1].tolist()]
    assert res.normal.tolist() == [[0.0, 0.0, 1.0]] * 3
    below = gn.nearest_triangles(target, p, math.nextafter(50.0, 0.0))
    assert below.triangle.tolist() == [-1, -1, 0]
    on = gn.nearest_triangles(target, v, 0.0)  # d_max = 0: only what lies on the wireframe
    assert on.found.all() and (on.distance == 0.0).all()


# --- hard data ---------------------------------------------------------------------------------------------


def test_triangles_without_area_are_never_the_nearest() -> None:
    """A point, a triangle folded onto a line and a sliver stand between the query and the real triangle."""
    o = UTM
    v = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [2, 2, 5], [3, 2, 5], [4, 2, 5], [2, 3, 6], [2, 3, 6 + 1e-7],
                  [9, 3, 6]], dtype=float) + o  # fmt: skip
    t = np.array([[3, 3, 3], [3, 4, 5], [0, 1, 2], [6, 7, 8], [3, 5, 4]], dtype=np.int32)
    p = np.array([[2.5, 2.0, 5.5], [2.0, 3.0, 6.5]]) + o
    res = gn.nearest_triangles(MeshTarget(v, t), p)
    assert res.triangle.tolist() == [3, 3]  # the sliver has an area; the collinear ones (0, 1, 4) have none
    assert res.triangle.tolist() == [i for i, _d in brute(p, v, t)]
    flat = MeshTarget(v, t[[0, 1, 4]])
    none = gn.nearest_triangles(flat, p)
    assert not none.found.any()
    assert np.isnan(gn.area_normals(flat, p, np.tile([0.0, 0.0, 1.0], (2, 1)), 100.0)).sum() == 0


def test_points_without_coordinates_and_an_empty_mesh() -> None:
    v, t = grid(4, 4, relief)
    p = np.array([[NAN, 0.0, 0.0], [*UTM], [math.inf, 0.0, 0.0], [UTM[0], NAN, UTM[2]]])
    res = gn.nearest_triangles(MeshTarget(v, t), p)
    assert res.found.tolist() == [False, True, False, False] and np.isnan(res.point[[0, 2, 3]]).all()
    empty = MeshTarget(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32))
    none = gn.nearest_triangles(empty, p)
    assert not none.found.any() and none.point.shape == (4, 3)
    no_points = gn.nearest_triangles(MeshTarget(v, t), np.zeros((0, 3)))
    assert len(no_points) == 0 and no_points.normal.shape == (0, 3)
    with pytest.raises(ValueError):
        gn.nearest_triangles(MeshTarget(v, t), p, -1.0)
    with pytest.raises(ValueError):
        gn.nearest_triangles(MeshTarget(v, t), p, NAN)
    with pytest.raises(ValueError):
        gn.nearest_triangles(MeshTarget(v, t), np.zeros((3, 2)))


def test_scales_apart_and_far_points() -> None:
    """Two clusters a hundred kilometres apart, one of millimetre triangles, and a triangle that spans both:
    the runs of the tree hold triangles far apart; points from millimetres to thousands of kilometres away."""
    va, ta = grid(12, 12, relief, step=5.0, seed=1)
    vb, tb = grid(12, 12, lambda x, y: 0.2 * np.sin(900.0 * x), step=0.004, offset=UTM + [1e5, 2e4, -300.0], seed=2)
    big = np.array([va[0] + [0, 0, 80.0], vb[0] + [0, 0, 80.0], 0.5 * (va[0] + vb[0]) + [0, 3e4, 80.0]])
    v = np.concatenate([va, vb, big])
    t = np.concatenate([ta, tb + len(va), [[len(va) + len(vb) + k for k in range(3)]]]).astype(np.int32)
    t = t[np.random.default_rng(3).permutation(len(t))]
    rng = np.random.default_rng(4)
    points = np.concatenate([
        cloud(va, 150, 40.0, seed=5), cloud(vb, 150, 0.01, seed=6), cloud(vb, 100, 300.0, seed=7),
        rng.uniform(v.min(axis=0) - 5e3, v.max(axis=0) + 5e3, size=(150, 3)),
        v[rng.integers(0, len(v), 50)] + rng.normal(0.0, 3e6, size=(50, 3)),
    ])  # fmt: skip
    res = gn.nearest_triangles(MeshTarget(v, t), points)
    ref = ref_distances(points, v, t)
    assert res.found.all()
    assert np.allclose(res.distance, ref.min(axis=1), rtol=1e-12, atol=TOL)
    assert np.all(ref[np.arange(len(points)), res.triangle] <= ref.min(axis=1) * (1.0 + 1e-12) + TOL)
    assert res.triangle[::9].tolist() == [i for i, _d in brute(points[::9], v, t)]


# --- threads, the order of the points, cancellation --------------------------------------------------------


def test_same_bytes_with_any_threads_and_any_order_of_points() -> None:
    v, t = grid(60, 60, relief, step=1.5)
    target = MeshTarget(v, t)
    points = cloud(v, 3 * gn.BAND + 777, 20.0, seed=9)
    points[5] = NAN
    one = gn.nearest_triangles(target, points, 15.0, threads=1)
    assert 0.3 < one.found.mean() < 0.99
    for threads in (2, 4, 8):
        assert digest(gn.nearest_triangles(target, points, 15.0, threads=threads)) == digest(one)
    order = np.random.default_rng(10).permutation(len(points))  # another neighbour to start the search from
    mixed = gn.nearest_triangles(target, points[order], 15.0, threads=4)
    assert np.array_equal(mixed.triangle, one.triangle[order])
    assert mixed.d2.tobytes() == one.d2[order].tobytes() and mixed.point.tobytes() == one.point[order].tobytes()
    sums = [gn.area_normals(target, one.point, one.normal, 4.0, threads=k) for k in (1, 2, 4, 8)]
    assert all(s.tobytes() == sums[0].tobytes() for s in sums[1:]) and np.isfinite(sums[0][one.found]).all()


def test_read_only_arrays_as_the_blobs_of_a_wireframe_are_mapped() -> None:
    v, t, _parts = stored(*grid(12, 12, relief))
    v, t = np.array(v), np.array(t)
    points = cloud(v, 300, 5.0, seed=14)
    want = gn.nearest_triangles(MeshTarget(v, t, ordered=True), points, 4.0)
    sums = gn.area_normals(MeshTarget(v, t, ordered=True), want.point, want.normal, 3.0)
    for a in (v, t, points, want.point, want.normal):
        a.setflags(write=False)
    frozen = MeshTarget(v, t, ordered=True)
    assert not frozen.xyz.flags.writeable and not frozen.tri.flags.writeable
    got = gn.nearest_triangles(frozen, points, 4.0, into=gn.nearest_triangles(frozen, points[::-1][::-1], 2.0))
    assert digest(got) == digest(want)
    assert gn.area_normals(frozen, want.point, want.normal, 3.0).tobytes() == sums.tobytes()
    single = gn.nearest_triangles(frozen, points.astype(np.float32), 4.0)  # points of any float type, any layout
    assert np.array_equal(single.found, gn.nearest_triangles(frozen, points.astype(np.float32)[:, ::1], 4.0).found)


def test_tick_reports_and_cancels() -> None:
    v, t = grid(10, 10, relief)
    target = MeshTarget(v, t)
    points = cloud(v, 2 * gn.BAND + 5, 5.0, seed=11)
    seen: list[float] = []
    gn.nearest_triangles(target, points, tick=seen.append)
    assert seen == sorted(seen) and len(seen) == 3 and seen[-1] == 1.0 and seen[0] > 0.0

    class Stop(Exception):
        pass

    def stop(_fraction: float) -> None:
        raise Stop

    for threads in (1, 4):
        with pytest.raises(Stop):
            gn.nearest_triangles(target, points, threads=threads, tick=stop)
        with pytest.raises(Stop):
            gn.area_normals(target, points, np.tile([0.0, 0.0, 1.0], (len(points), 1)), 1.0, threads=threads, tick=stop)


# --- several wireframes, one at a time ---------------------------------------------------------------------


def test_wireframes_one_at_a_time() -> None:
    """Hanging wall and footwall as two surfaces and a sphere: the nearest of the three is that of the union,
    whatever was found before bounds the search in the next wireframe; equal distances — the lower number of
    the triangle, then of the wireframe."""
    parts = [grid(20, 20, lambda x, y: 0.05 * x, step=2.0, seed=1),
             grid(20, 20, lambda x, y: 0.05 * x - 3.0, step=2.0, seed=2),
             icosphere(2, 9.0, UTM + [20.0, 20.0, 14.0])]  # fmt: skip
    points = cloud(np.concatenate([v for v, _t in parts]), 1500, 10.0, seed=12)
    res = None
    for k, (v, t) in enumerate(parts):
        res = gn.nearest_triangles(MeshTarget(v, t), points, 6.0, into=res, mesh=k)
    assert res is not None
    each = [gn.nearest_triangles(MeshTarget(v, t), points, 6.0) for v, t in parts]
    d2 = np.stack([np.where(e.found, e.d2, np.inf) for e in each])
    tri = np.stack([np.where(e.found, e.triangle, np.iinfo(np.int64).max) for e in each])
    want = np.lexsort((np.arange(3)[:, None].repeat(len(points), 1), tri, d2), axis=0)[0]
    found = np.isfinite(d2.min(axis=0))
    assert np.array_equal(res.found, found) and set(res.mesh[found].tolist()) == {0, 1, 2}
    assert np.array_equal(res.mesh[found], want[found])
    for k, e in enumerate(each):
        mine = found & (want == k)
        assert np.array_equal(res.triangle[mine], e.triangle[mine]) and res.d2[mine].tobytes() == e.d2[mine].tobytes()
        assert res.point[mine].tobytes() == e.point[mine].tobytes()
        assert res.normal[mine].tobytes() == e.normal[mine].tobytes()
    # the same surface twice: equal distances and equal numbers — the first wireframe keeps the point
    twice = gn.nearest_triangles(MeshTarget(*parts[0]), points, 6.0, into=each[0], mesh=5)
    assert digest(twice) == digest(each[0])
    # midway between two level triangles: the lower number of the triangle wins, not the first wireframe
    lo = np.array([[0.0, 0, 0], [4, 0, 0], [0, 4, 0], [9, 9, 9], [9, 8, 9], [8, 9, 9]]) + WHOLE
    hi = lo[:3] + [0.0, 0.0, 2.0]
    first = MeshTarget(lo, np.array([[3, 4, 5], [0, 1, 2]], dtype=np.int32))  # the near triangle is number 1
    second = MeshTarget(hi, np.array([[0, 1, 2]], dtype=np.int32))  # … and number 0 here
    mid = np.array([[1.0, 1.0, 1.0]]) + WHOLE
    a = gn.nearest_triangles(second, mid, into=gn.nearest_triangles(first, mid, mesh=0), mesh=1)
    b = gn.nearest_triangles(first, mid, into=gn.nearest_triangles(second, mid, mesh=1), mesh=0)
    assert (a.mesh.tolist(), a.triangle.tolist(), a.distance.tolist()) == ([1], [0], [1.0])
    assert digest(a) == digest(b)
    with pytest.raises(ValueError):
        gn.nearest_triangles(first, mid[:0], into=a)


# --- the normals averaged by area --------------------------------------------------------------------------


def ref_area_normals(points: np.ndarray, normals: np.ndarray, v: np.ndarray, t: np.ndarray, r: float) -> np.ndarray:
    ref = ref_distances(points, v, t)
    half = 0.5 * np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    out = np.zeros((len(points), 3))
    for i in range(len(points)):
        take = ref[i] <= r
        sign = np.where(half[take] @ normals[i] < 0.0, -1.0, 1.0)
        out[i] = (half[take] * sign[:, None]).sum(axis=0)
    return out


def test_area_normals_against_full_search() -> None:
    v, t = grid(30, 30, relief, step=2.0)
    target = MeshTarget(v, t)
    points = cloud(v, 400, 8.0, seed=13)
    near = gn.nearest_triangles(target, points)
    ref = ref_distances(near.point, v, t)
    for r in (0.7, 5.0, 23.0, 0.0):
        got = gn.area_normals(target, near.point, near.normal, r)
        unit = gn.mean_normals(got, near.normal)
        assert np.allclose(np.linalg.norm(unit, axis=1), 1.0, rtol=0.0, atol=1e-12)
        assert ((unit * near.normal).sum(axis=1) > 0.0).all()  # never turned away from the nearest triangle
        if r == 0.0:  # only what the point lies on, to the last bit of its coordinates: often nothing
            assert (np.linalg.norm(got, axis=1) < 20.0).all()
            continue
        want = ref_area_normals(near.point, near.normal, v, t, r)
        clear = np.abs(ref - r).min(axis=1) > 1e-7  # no triangle exactly at the radius
        assert clear.sum() > 300 and np.allclose(got[clear], want[clear], rtol=1e-12, atol=1e-10)
        assert (np.linalg.norm(got, axis=1) > 2.0).all()  # the nearest triangle itself is always in the sum


def test_area_normals_of_a_thin_body_and_a_roof() -> None:
    """A plate 0,4 m thick, dipping: both of its sides are within r_n, their normals look opposite ways — the
    mean is the plane of the plate, not zero. A roof: the mean over both slopes is the level plane."""
    dip = math.radians(35.0)
    turn = np.array([[1.0, 0, 0], [0, math.cos(dip), -math.sin(dip)], [0, math.sin(dip), math.cos(dip)]])
    v, t = cube(6, 1.0, offset=np.zeros(3))
    v = (v * [60.0, 60.0, 0.4]) @ turn.T + UTM
    target = MeshTarget(v, t)
    points = (np.array([[30.0, 30.0, 0.2], [31.3, 22.0, 0.31], [20.0, 41.0, 5.0], [40.0, 33.0, -7.0]]) @ turn.T) + UTM
    near = gn.nearest_triangles(target, points)
    up = turn @ [0.0, 0.0, 1.0]
    assert np.allclose(np.abs(near.normal @ up), 1.0, atol=1e-12)
    mean = gn.mean_normals(gn.area_normals(target, near.point, near.normal, 6.0), near.normal)
    assert np.allclose(mean, near.normal, rtol=0.0, atol=1e-9)  # both faces, turned to the side of the nearest
    assert np.linalg.norm(gn.area_normals(target, near.point, near.normal, 6.0), axis=1).min() > 90.0

    roof, tr = grid(20, 20, lambda x, y: -np.abs(x - 20.0), step=2.0)  # the crest along x = 20
    rt = MeshTarget(roof, tr)
    p = np.array([[20.0, 21.0, 3.0], [26.0, 9.0, 0.0]]) + UTM
    nr = gn.nearest_triangles(rt, p)
    assert np.allclose(np.abs(nr.normal[:, 0]), math.sqrt(0.5), atol=1e-12)  # the nearest triangle: one slope
    both = gn.mean_normals(gn.area_normals(rt, nr.point[:1], nr.normal[:1], 12.0), nr.normal[:1])
    assert abs(both[0, 0]) < 0.15 and both[0, 2] > 0.98  # on the crest: the mean of the two slopes is near level
    side = gn.mean_normals(gn.area_normals(rt, nr.point[1:], nr.normal[1:], 3.0), nr.normal[1:])
    assert np.allclose(side, nr.normal[1:], rtol=0.0, atol=1e-12)  # the crest is 4,2 m from the foot


def test_mean_normals_fall_back_to_the_nearest() -> None:
    """A sum shorter than 1e-9 (a triangle of a square millimetre and less) — the normal of the nearest; rows
    without a point stay empty, never zero."""
    v = np.array([[0.0, 0, 0], [2e-5, 0, 0], [0, 2e-5, 0], [50.0, 0, 0], [58.0, 0, 0], [50.0, 0, 8.0]]) + UTM
    target = MeshTarget(v, np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32))
    p = np.array([[0.0, 0.0, 1.0], [52.0, 3.0, 2.0], [NAN, 0.0, 0.0]]) + UTM
    near = gn.nearest_triangles(target, p)
    sums = gn.area_normals(target, near.point, near.normal, 0.5)
    assert np.linalg.norm(sums[0]) < gn.NORMAL_MIN and np.allclose(sums[1], [0.0, -32.0, 0.0], atol=1e-9)
    assert np.isnan(sums[2]).all()
    mean = gn.mean_normals(sums, near.normal)
    assert mean[0].tolist() == [0.0, 0.0, 1.0] and np.allclose(mean[1], [0.0, -1.0, 0.0], atol=1e-12)
    assert np.isnan(mean[2]).all()
    with pytest.raises(ValueError):
        gn.area_normals(target, near.point, near.normal, -1.0)
    with pytest.raises(ValueError):
        gn.area_normals(target, near.point, near.normal[:2], 1.0)
