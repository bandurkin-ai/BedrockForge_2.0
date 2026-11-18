"""Exact pick of a triangle by a ray (ТЗ 7.21 «треугольник по клику»; ADR 0013 §4 `pick.py`, ADR 0004 §2): the
corners come back bit for bit, the area to 1e-9, dip and dip direction to 1e-9°; the nearest hit equals a
brute-force Möller–Trumbore over all triangles (1e-9 m) with and without the parts of the store; rays aimed at
shared edges and vertices of a surface on UTM coordinates never slip between triangles; the planes of the section
window limit the ray."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.geo import pick as gp
from tests.meshutil import UTM, cube, grid, icosphere, stored

pytestmark = pytest.mark.windows


def hills(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return 20.0 * np.sin(x / 17.0) * np.cos(y / 23.0)


def brute(v: np.ndarray, t: np.ndarray, o: np.ndarray, d: np.ndarray) -> tuple[float, int]:
    """Möller–Trumbore over every triangle (numpy, float64): (distance, triangle) of the nearest hit."""
    d = d / np.linalg.norm(d)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    e1, e2 = b - a, c - a
    p = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, p)
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / det
        s = o - a
        u = np.einsum("ij,ij->i", s, p) * inv
        q = np.cross(s, e1)
        w = np.einsum("ij,j->i", q, d) * inv
        dist = np.einsum("ij,ij->i", e2, q) * inv
    ok = (np.abs(det) > 1e-300) & (u >= 0) & (w >= 0) & (u + w <= 1) & (dist >= 0)
    if not ok.any():
        return math.inf, -1
    dist = np.where(ok, dist, np.inf)
    k = int(np.argmin(dist))
    return float(dist[k]), k


def inside(hit: gp.PickHit, tol: float = 1e-8) -> bool:
    """The point of the hit is on its triangle."""
    a, b, c = (np.array(p) for p in hit.vertices)
    p = np.array(hit.point)
    n = np.cross(b - a, c - a)
    area2 = float(np.linalg.norm(n))
    if abs((p - a) @ n) / area2 > tol:
        return False
    bary = [np.cross(b - p, c - p) @ n, np.cross(c - p, a - p) @ n, np.cross(a - p, b - p) @ n]
    return min(bary) / area2**2 >= -tol


# --- the answer --------------------------------------------------------------------------------------------


def test_corners_area_dip_and_dip_direction() -> None:
    dip, direction = 30.0, 120.0
    h = np.array([math.sin(math.radians(direction)), math.cos(math.radians(direction)), 0.0])
    down = h * math.cos(math.radians(dip)) - np.array([0.0, 0.0, math.sin(math.radians(dip))])
    strike = np.array([h[1], -h[0], 0.0])
    v = np.array([UTM, UTM + 10.0 * strike, UTM + 7.0 * down + 3.0 * strike])
    centre = v.mean(axis=0)
    for tri in ([0, 1, 2], [0, 2, 1]):  # either winding: the same attitude
        t = np.array([tri], dtype=np.int32)
        hit = gp.ray_pick(v, t, centre + [0.0, 0.0, 250.0], [0.0, 0.0, -3.0])
        assert hit is not None and hit.triangle == 0 and hit.indices == tuple(tri)
        assert np.array(hit.vertices).tobytes() == v[tri].tobytes()  # the exact corners
        assert hit.distance == pytest.approx(250.0, abs=1e-9)
        assert np.abs(np.array(hit.point) - centre).max() <= 2e-9
        assert hit.area == pytest.approx(35.0, abs=1e-9)
        assert hit.dip == pytest.approx(dip, abs=1e-9)
        assert hit.dip_direction == pytest.approx(direction, abs=1e-9)
        assert np.linalg.norm(hit.normal) == pytest.approx(1.0, abs=1e-12)
        assert (hit.normal[2] > 0.0) == (tri == [0, 1, 2])  # the normal follows the order of the corners
    up = gp.ray_pick(v, np.array([[0, 1, 2]], dtype=np.int32), centre - [0.0, 0.0, 40.0], [0.0, 0.0, 1.0])
    assert up is not None and up.distance == pytest.approx(40.0, abs=1e-9)  # from below: both sides are hit


def test_level_and_vertical_triangles() -> None:
    level = np.array([[0, 0, 5], [4, 0, 5], [0, 3, 5]], dtype=float) + UTM
    t = np.array([[0, 1, 2]], dtype=np.int32)
    hit = gp.ray_pick(level, t, UTM + [1.0, 1.0, 50.0], [0.0, 0.0, -1.0])
    assert hit is not None and hit.dip == 0.0 and hit.dip_direction is None  # no direction: not a zero azimuth
    assert hit.area == pytest.approx(6.0, abs=1e-12) and hit.normal == (0.0, 0.0, 1.0)
    wall = np.array([[0, 0, 0], [0, 4, 0], [0, 0, 3]], dtype=float) + UTM  # the plane x = const, normal to the east
    hit = gp.ray_pick(wall, t, UTM + [-20.0, 1.0, 1.0], [1.0, 0.0, 0.0])
    assert hit is not None and hit.dip == pytest.approx(90.0, abs=1e-12)
    assert hit.dip_direction == pytest.approx(90.0, abs=1e-12) and hit.distance == pytest.approx(20.0, abs=1e-9)
    attitude = gp.triangle_attitude(wall[0], wall[2], wall[1])  # the other winding faces west
    assert attitude.dip_direction == pytest.approx(270.0, abs=1e-12) and attitude.area == pytest.approx(6.0)
    flat = gp.triangle_attitude(wall[0], wall[1], wall[1])  # no area: nothing to measure
    assert flat.area == 0.0 and flat.dip is None and flat.dip_direction is None


def test_nearest_face_of_a_cube() -> None:
    v, t, parts = stored(*cube(10, 100.0), part_triangles=64)
    lo = v.min(axis=0)
    origin = lo + [-40.0, 33.3, 61.7]
    for kw in ({}, {"parts": parts}):
        hit = gp.ray_pick(v, t, origin, [2.0, 0.0, 0.0], **kw)
        assert hit is not None and hit.distance == pytest.approx(40.0, abs=1e-9) and inside(hit)
        assert hit.point[0] == pytest.approx(lo[0], abs=1e-9) and hit.dip == pytest.approx(90.0, abs=1e-9)
        assert hit.dip_direction == pytest.approx(270.0, abs=1e-9)  # the west face looks west
        assert hit.area == pytest.approx(50.0, abs=1e-9) and hit.indices == tuple(int(i) for i in t[hit.triangle])
        within = gp.ray_pick(v, t, lo + [50.0, 33.3, 61.7], [1.0, 0.0, 0.0], **kw)  # from inside: the east face
        assert within is not None and within.distance == pytest.approx(50.0, abs=1e-9)
        assert gp.ray_pick(v, t, origin, [-1.0, 0.0, 0.0], **kw) is None  # looking away
        assert gp.ray_pick(v, t, origin, [0.0, 0.0, 1.0], **kw) is None  # beside
        assert gp.ray_pick(v, t, origin, [1.0, 0.0, 0.0], max_distance=39.999, **kw) is None
        on = gp.ray_pick(v, t, lo + [0.0, 33.3, 61.7], [1.0, 0.0, 0.0], **kw)  # the origin on the face itself
        assert on is not None and on.distance == 0.0
        along = gp.ray_pick(v, t, lo + [-5.0, 33.3, 100.0], [1.0, 0.0, 0.0], **kw)  # in the plane of the top
        assert along is None or along.distance == pytest.approx(5.0, abs=1e-9)  # never a triangle seen edge-on


def test_equals_brute_force_with_and_without_parts() -> None:
    v, t = icosphere(4, 80.0)  # 5120 triangles
    rng = np.random.default_rng(11)
    v = v + rng.normal(0.0, 1.5, v.shape)  # a rough body: several hits along a ray
    sv, st, parts = stored(v, t, part_triangles=100)
    assert len(parts) > 50
    hits = 0
    for _ in range(400):
        o = UTM + rng.uniform(-200.0, 200.0, 3)
        d = (UTM + rng.uniform(-60.0, 60.0, 3)) - o if rng.random() < 0.8 else rng.normal(size=3)
        ref_dist, ref_tri = brute(sv, st, o, d)
        plain = gp.ray_pick(sv, st, o, d)
        fast = gp.ray_pick(sv, st, o, d, parts=parts)
        if ref_tri < 0:
            assert plain is None and fast is None
            continue
        hits += 1
        assert plain is not None and fast is not None
        assert plain.distance == pytest.approx(ref_dist, abs=1e-9) and plain.triangle == ref_tri and inside(plain)
        assert (fast.triangle, fast.distance, fast.point) == (plain.triangle, plain.distance, plain.point)
    assert hits > 250


def test_rays_through_shared_edges_and_vertices_never_slip_between_triangles() -> None:
    v, t, parts = stored(*grid(60, 60, hills), part_triangles=300)
    rng = np.random.default_rng(3)
    a, b = v[t[:, 0]], v[t[:, 1]]
    w = rng.random((len(t), 1))
    targets = np.vstack([v, 0.5 * (a + b), a + (b - a) * w])  # vertices, middles of edges, points of edges
    targets = targets[rng.permutation(len(targets))[:4000]]
    lo, hi = v.min(axis=0), v.max(axis=0)
    interior = ((targets[:, :2] > lo[:2] + 1e-6) & (targets[:, :2] < hi[:2] - 1e-6)).all(axis=1)
    targets = targets[interior]
    for direction in ([0.0, 0.0, -1.0], [0.3, 0.2, -1.0]):
        d = np.array(direction) / np.linalg.norm(direction)
        for target in targets:
            hit = gp.ray_pick(v, t, target - 500.0 * d, d, parts=parts)
            assert hit is not None, target  # a miss here is a ray that went between two triangles
            # the first hit is at the target or nearer (a hill in front of it), never behind it
            assert hit.distance <= 500.0 + 1e-6
            if direction[0] == 0.0:
                assert hit.distance == pytest.approx(500.0, abs=1e-6)
                assert abs(hit.point[2] - target[2]) <= 1e-6


def test_exact_rays_through_vertices_of_a_lattice() -> None:
    """Vertices on a lattice of 1/8 m around UTM-sized whole numbers and rays whose numbers are all exact: every
    ray passes through a vertex where six triangles meet. Möller–Trumbore without a tolerance loses about one
    such ray in a hundred between the triangles (`brute` is that test); none may be lost here."""

    def relief(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return np.round(24.0 * np.sin(x / 5.0) * np.cos(y / 7.0)) / 8.0

    v, t, parts = stored(*grid(40, 40, relief, offset=[512_000.0, 6_234_000.0, 400.0]), part_triangles=300)
    lo, hi = v.min(axis=0), v.max(axis=0)
    inner = v[((v[:, :2] > lo[:2] + 2.0) & (v[:, :2] < hi[:2] - 2.0)).all(axis=1)]
    rng = np.random.default_rng(1)
    lost = 0
    for target in inner[rng.permutation(len(inner))[:1200]]:
        d = np.array([rng.integers(-3, 4), rng.integers(-3, 4), -8.0]) / 8.0
        origin = target - 512.0 * d  # exact
        reach = 512.0 * float(np.linalg.norm(d))
        hit = gp.ray_pick(v, t, origin, d, parts=parts)
        assert hit is not None and hit.distance <= reach + 1e-9, target  # at the vertex, or a hill before it
        dist, _tri = brute(v, t, origin, d)
        lost += dist > reach + 1e-9
        if hit.distance >= reach - 1e-9:
            assert np.abs(np.array(hit.point) - target).max() <= 1e-9
            assert any(np.array_equal(corner, target) for corner in np.array(hit.vertices))
    assert lost > 0  # the data are hard: the plain test does lose rays here


def test_planes_of_the_section_window_limit_the_ray() -> None:
    v, t, parts = stored(*cube(10, 100.0), part_triangles=64)
    lo = v.min(axis=0)
    origin, d = lo + [-40.0, 33.3, 61.7], [1.0, 0.0, 0.0]
    keep_far = [[1.0, 0.0, 0.0, -(lo[0] + 10.0)], [-1.0, 0.0, 0.0, lo[0] + 105.0]]  # 10 ≤ x − lo ≤ 105
    hit = gp.ray_pick(v, t, origin, d, parts=parts, clip=keep_far)
    assert hit is not None and hit.distance == pytest.approx(140.0, abs=1e-9)  # the near face is cut away
    keep_none = [[1.0, 0.0, 0.0, -(lo[0] + 10.0)], [-1.0, 0.0, 0.0, lo[0] + 90.0]]
    assert gp.ray_pick(v, t, origin, d, parts=parts, clip=keep_none) is None
    keep_all = [[1.0, 0.0, 0.0, -(lo[0] - 10.0)], [0.0, 1.0, 0.0, -(lo[1] - 10.0)]]
    assert gp.ray_pick(v, t, origin, d, parts=parts, clip=keep_all).distance == pytest.approx(40.0, abs=1e-9)  # type: ignore[union-attr]
    # a ray parallel to a plane of the window and outside it sees nothing
    outside = [[0.0, 1.0, 0.0, -(lo[1] + 50.0)]]  # y − lo ≥ 50, the ray runs at 33.3
    assert gp.ray_pick(v, t, origin, d, parts=parts, clip=outside) is None
    with pytest.raises(ValueError):
        gp.ray_pick(v, t, origin, d, clip=[[0.0, 0.0, 0.0, 1.0]])


def test_fine_parts_cover_the_same_triangles_and_give_the_same_pick() -> None:
    v, t, parts = stored(*grid(120, 90, hills), part_triangles=2000)
    fine = gp.fine_parts(v, t, parts, size=64)
    assert fine.dtype == np.float64 and fine.shape[1] == 8 and len(fine) > 20 * len(parts)
    first, count = fine[:, 0].astype(int), fine[:, 1].astype(int)
    assert first[0] == 0 and (first[1:] == first[:-1] + count[:-1]).all() and first[-1] + count[-1] == len(t)
    assert count.max() <= 64 and set(first.tolist()) >= {int(p[0]) for p in parts}  # runs do not cross the parts
    for k in (0, len(fine) // 2, len(fine) - 1):  # the box of a run is the exact box of its triangles
        corners = v[t[first[k] : first[k] + count[k]].ravel()]
        assert fine[k, 2:5].tolist() == corners.min(axis=0).tolist()
        assert fine[k, 5:8].tolist() == corners.max(axis=0).tolist()
    rng = np.random.default_rng(8)
    lo, hi = v.min(axis=0), v.max(axis=0)
    found = 0
    for k in range(200):
        target = lo + rng.random(3) * (hi - lo)
        origin = target + [300.0 * math.cos(k), 300.0 * math.sin(k), 40.0 + 200.0 * (k % 3)]
        coarse = gp.ray_pick(v, t, origin, target - origin, parts=parts)
        assert gp.ray_pick(v, t, origin, target - origin, parts=fine) == coarse
        assert gp.ray_pick(v, t, origin, target - origin) == coarse
        found += coarse is not None
    assert 100 < found
    whole = gp.fine_parts(v, t, size=1000)  # without a part table: the whole mesh in runs
    assert len(whole) == math.ceil(len(t) / 1000) and whole[:, 1].sum() == len(t)
    with pytest.raises(ValueError):
        gp.fine_parts(v, t, parts, size=0)
    bad = t.copy()
    bad[-1, 2] = len(v)
    with pytest.raises(ValueError):
        gp.fine_parts(v, bad, parts)


def test_memory_maps_and_bad_input(tmp_path) -> None:  # type: ignore[no-untyped-def]
    v, t, parts = stored(*cube(6, 60.0), part_triangles=64)
    v.tofile(tmp_path / "xyz.bin")
    t.tofile(tmp_path / "tri.bin")
    mv = np.memmap(tmp_path / "xyz.bin", dtype="<f8", mode="r").reshape(-1, 3)
    mt = np.memmap(tmp_path / "tri.bin", dtype="<i4", mode="r").reshape(-1, 3)
    origin = v.min(axis=0) + [20.0, 20.0, 300.0]
    a = gp.ray_pick(v, t, origin, [0.0, 0.0, -1.0], parts=parts)
    b = gp.ray_pick(mv, mt, origin, [0.0, 0.0, -1.0], parts=parts)
    assert a is not None and a == b and a.distance == pytest.approx(240.0, abs=1e-9)
    assert gp.ray_pick(v, np.zeros((0, 3), dtype=np.int32), origin, [0.0, 0.0, -1.0]) is None
    for o, d in ((origin, [0.0, 0.0, 0.0]), (origin, [math.nan, 0.0, 1.0]), ([math.inf, 0.0, 0.0], [0.0, 0.0, 1.0])):
        with pytest.raises(ValueError):
            gp.ray_pick(v, t, o, d)
    bad = t.copy()
    bad[0, 0] = -1
    with pytest.raises(ValueError):
        gp.ray_pick(v, bad, origin, [0.0, 0.0, -1.0])
    with pytest.raises(ValueError):
        gp.ray_pick(v, t, origin, [0.0, 0.0, -1.0], parts=[[5, len(t), 0, 0, 0, 1, 1, 1]])
