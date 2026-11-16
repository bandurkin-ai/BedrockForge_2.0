"""Solids: boolean operations, clipping, intersection lines (strings-wireframes-editing, 6.4 and 9).

Synthetic bodies with known answers; the tolerance of every check is written next to it.
No database: pure geometry.
"""

import math
import time

import manifold3d
import numpy as np
import pytest

from app.formats.wireframes import mesh_stats
from app.geo import solids
from app.geo.checks import orientation_conflicts, self_intersections
from app.geo.solids import (
    SolidError,
    boolean,
    boolean_many,
    clip_by_plane,
    clip_by_polygon,
    clip_by_surface,
    expand,
    intersection_lines,
    is_closed_solid,
    sweep_solid,
)

# A typical UTM position: every body is built here, not at the origin.
OFFSET = np.array([512345.678, 6789012.345, 250.0])

CUBE_T = np.array([
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
    [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
], dtype=np.int32)


def box(lo, hi, offset=OFFSET):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], dtype=np.float64)
    return v + offset, CUBE_T.copy()


def sphere(radius, n_lat, n_lon, centre=(0.0, 0.0, 0.0), offset=OFFSET):
    """UV sphere, outward; symmetric about the planes x = 0, y = 0 (n_lon divisible by 4) and z = 0 (n_lat even)."""
    theta = np.pi * np.arange(1, n_lat) / n_lat
    phi = 2 * np.pi * np.arange(n_lon) / n_lon
    ring = np.stack([np.outer(np.sin(theta), np.cos(phi)), np.outer(np.sin(theta), np.sin(phi)),
                     np.outer(np.cos(theta), np.ones(n_lon))], axis=-1).reshape(-1, 3)
    v = np.concatenate([[[0.0, 0.0, 1.0]], ring, [[0.0, 0.0, -1.0]]]) * radius + np.asarray(centre) + offset
    j = np.arange(n_lon)
    jn = (j + 1) % n_lon
    top = np.stack([np.zeros(n_lon, dtype=np.int64), 1 + j, 1 + jn], axis=1)
    i = np.arange(n_lat - 2)[:, None]
    a, b = 1 + i * n_lon + j, 1 + i * n_lon + jn
    c, d = a + n_lon, b + n_lon
    quads = np.concatenate([np.stack([a, c, d], axis=-1).reshape(-1, 3), np.stack([a, d, b], axis=-1).reshape(-1, 3)])
    last = 1 + (n_lat - 2) * n_lon
    bottom = np.stack([np.full(n_lon, len(v) - 1), last + jn, last + j], axis=1)
    return v, np.concatenate([top, quads, bottom]).astype(np.int32)


def prism(polygon, z0, z1, offset=OFFSET):
    """Vertical prism on a counter-clockwise contour (k, 2)."""
    polygon = np.asarray(polygon, dtype=np.float64)
    k = len(polygon)
    tri = np.asarray(manifold3d.triangulate([polygon]), dtype=np.int64)
    i = np.arange(k)
    j = (i + 1) % k
    v = np.concatenate([np.column_stack([polygon, np.full(k, z0)]), np.column_stack([polygon, np.full(k, z1)])])
    t = np.concatenate([tri[:, ::-1], tri + k, np.stack([i, j, j + k], axis=1), np.stack([i, j + k, i + k], axis=1)])
    return v + offset, t.astype(np.int32)


def grid(nx, ny, x, y, z=lambda x, y: 0.0 * x, offset=OFFSET):
    """Open surface z(x, y) over a rectangle, nx × ny cells, normals up."""
    xs, ys = np.linspace(*x, nx + 1), np.linspace(*y, ny + 1)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    v = np.column_stack([gx.ravel(), gy.ravel(), np.asarray(z(gx, gy), dtype=np.float64).ravel()]) + offset
    i = (np.arange(nx)[:, None] * (ny + 1) + np.arange(ny)[None, :]).ravel()
    t = np.concatenate([np.stack([i, i + ny + 1, i + ny + 2], axis=1), np.stack([i, i + ny + 2, i + 1], axis=1)])
    return v, t.astype(np.int32)


L_SHAPE = [[0, 0], [10, 0], [10, 4], [4, 4], [4, 10], [0, 10]]  # area 64, not convex


def volume(mesh):
    """Exact float64 volume (mesh_stats rounds to 0.01)."""
    v, t = mesh
    p = v - v.mean(axis=0)
    return float(np.einsum("ij,ij->i", p[t[:, 0]], np.cross(p[t[:, 1]], p[t[:, 2]])).sum()) / 6.0


def area(mesh):
    v, t = mesh
    return float(np.linalg.norm(np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]]), axis=1).sum()) / 2.0


def assert_good_solid(mesh):
    """The stage 10 checks: closed, outward, consistent normals, no self-intersections."""
    v, t = mesh
    assert v.dtype == np.float64 and t.dtype == np.int32
    stats = mesh_stats(v, t)
    assert stats["closed"], stats
    assert stats["orientation"] == "outward"
    assert orientation_conflicts(t, len(v)) == 0
    assert self_intersections(v, t)["pairs"] == 0
    assert is_closed_solid(v, t) == (True, "")


def length(points, closed):
    p = np.vstack([points, points[:1]]) if closed else points
    return float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum())


# ---- boolean operations


def test_shifted_cubes_have_exact_volumes():
    a = box((0, 0, 0), (10, 10, 10))
    b = box((4, 3, 2), (14, 13, 12))
    common = 6 * 7 * 8
    for op, expected in (("union", 2000 - common), ("intersection", common), ("difference", 1000 - common)):
        v, t, report = boolean(a, b, op)
        assert_good_solid((v, t))
        # Tolerance: 1e-6 m3 on 10 m cubes at UTM coordinates.
        assert volume((v, t)) == pytest.approx(expected, abs=1e-6)
        assert report["volume"] == pytest.approx(expected, abs=1e-6)
        assert report["volume_a"] == pytest.approx(1000, abs=1e-6)
        assert report["volume_b"] == pytest.approx(1000, abs=1e-6)
        assert report["op"] == op and report["closed"] and not report["empty"]
        assert report["parts"] == 1 and report["triangles"] == len(t)
        assert report["origin"] == np.round(OFFSET + [7, 6.5, 6]).tolist()
        assert 0 < report["tolerance"] < 1e-9
        # The result stays inside the extent of the inputs (to the last bit of float64 here).
        assert np.all(v.min(axis=0) >= OFFSET - 1e-9) and np.all(v.max(axis=0) <= OFFSET + [14, 13, 12] + 1e-9)


def test_difference_is_not_symmetric_and_disjoint_bodies():
    a = box((0, 0, 0), (10, 10, 10))
    b = box((4, 3, 2), (14, 13, 12))
    assert boolean(b, a, "difference")[2]["volume"] == pytest.approx(1000 - 336, abs=1e-6)
    far = box((100, 0, 0), (110, 10, 10))
    v, t, report = boolean(a, far, "intersection")
    assert report["empty"] and report["parts"] == 0 and report["volume"] == 0.0
    assert v.shape == (0, 3) and t.shape == (0, 3)
    v, t, report = boolean(a, far, "union")
    assert report["parts"] == 2 and report["volume"] == pytest.approx(2000, abs=1e-6)
    assert_good_solid((v, t))


def test_sphere_and_cube():
    r = 5.0
    ball = sphere(r, 64, 128, centre=(0, 0, 0))
    half = box((0, -6, -6), (6, 6, 6))  # covers the half x >= 0 of the sphere
    exact = 4 / 3 * math.pi * r**3
    ball_volume = volume(ball)
    # Discretisation of the sphere (64 × 128): its volume is below the exact one by < 0.2 %.
    assert ball_volume == pytest.approx(exact, rel=2e-3)
    v, t, report = boolean(ball, half, "intersection")
    assert_good_solid((v, t))
    # The mesh is symmetric about x = 0: exactly half of the faceted sphere (rel 1e-9).
    assert report["volume"] == pytest.approx(ball_volume / 2, rel=1e-9)
    assert report["volume"] == pytest.approx(exact / 2, rel=2e-3)
    v, t, report = boolean(half, ball, "difference")
    assert_good_solid((v, t))
    assert report["volume"] == pytest.approx(6 * 12 * 12 - ball_volume / 2, rel=1e-9)
    v, t, report = boolean(ball, half, "union")
    assert_good_solid((v, t))
    assert report["volume"] == pytest.approx(6 * 12 * 12 + ball_volume / 2, rel=1e-9)


@pytest.mark.parametrize("shift", [0.0, 1e-9, 1e-6, 1e-3])
def test_bodies_with_a_common_face(shift):
    """Two prisms built from the same contour: stacked, the upper one lowered by `shift`."""
    lower = prism(L_SHAPE, 0, 10)
    upper = prism(L_SHAPE, 10 - shift, 20 - shift)
    v, t, report = boolean(lower, upper, "union")
    assert_good_solid((v, t))
    assert report["parts"] == 1
    # Tolerance 1e-6 m3 (bodies of 640 m3).
    assert report["volume"] == pytest.approx(64 * (20 - shift), abs=1e-6)
    v, t, report = boolean(lower, upper, "intersection")
    # Empty, or the sliver the two bodies really share.
    assert report["volume"] == pytest.approx(64 * shift, abs=1e-6)
    assert report["empty"] == (len(t) == 0)
    if not report["empty"]:
        assert mesh_stats(v, t)["closed"]
    v, t, report = boolean(lower, upper, "difference")
    assert_good_solid((v, t))
    assert report["volume"] == pytest.approx(64 * (10 - shift), abs=1e-6)


@pytest.mark.parametrize("gap", [1e-9, 1e-6])
def test_bodies_almost_touching(gap):
    """The same contour with a gap between the bodies: nothing in common, no exception."""
    lower = prism(L_SHAPE, 0, 10)
    upper = prism(L_SHAPE, 10 + gap, 20 + gap)
    v, t, report = boolean(lower, upper, "union")
    assert mesh_stats(v, t)["closed"]
    assert report["volume"] == pytest.approx(1280, abs=1e-6)
    assert boolean(lower, upper, "intersection")[2]["volume"] == pytest.approx(0, abs=1e-6)
    assert boolean(lower, upper, "difference")[2]["volume"] == pytest.approx(640, abs=1e-6)


def test_side_by_side_bodies_sharing_a_wall():
    """Bodies sharing a vertical face with different triangulations of it."""
    a = prism(L_SHAPE, 0, 10)
    b = box((10, 0, 0), (15, 4, 10))
    v, t, report = boolean(a, b, "union")
    assert_good_solid((v, t))
    assert report["parts"] == 1
    assert report["volume"] == pytest.approx(640 + 200, abs=1e-6)
    # The bodies only touch: nothing, or the common wall as a skin of zero volume, marked flat.
    common = boolean(a, b, "intersection")[2]
    assert common["volume"] == 0.0 and (common["empty"] or common["flat"])
    assert not report["flat"]


VOLUME_CASES = {
    "cubes": lambda: (box((0, 0, 0), (10, 10, 10)), box((4, 3, 2), (14, 13, 12))),
    "non-convex and cube": lambda: (prism(L_SHAPE, 0, 10), box((2, 2, -3), (9, 9, 6))),
    "sphere and cube": lambda: (sphere(5, 48, 96), box((1, -2, -3), (8, 9, 4))),
    "two spheres": lambda: (sphere(5, 48, 96), sphere(4, 40, 80, centre=(3, 2, 1))),
    "sphere and non-convex": lambda: (sphere(6, 48, 96, centre=(4, 4, 5)), prism(L_SHAPE, 0, 10)),
    "common face": lambda: (prism(L_SHAPE, 0, 10), prism(L_SHAPE, 10, 20)),
}


@pytest.mark.parametrize("case", list(VOLUME_CASES))
def test_volume_identity(case):
    a, b = VOLUME_CASES[case]()
    reports = {op: boolean(a, b, op) for op in ("union", "intersection", "difference")}
    va, vb = volume(a), volume(b)
    union, common, minus = (reports[op][2]["volume"] for op in ("union", "intersection", "difference"))
    # V(A∪B) = V(A) + V(B) − V(A∩B) and V(A−B) = V(A) − V(A∩B): relative 1e-9 of V(A) + V(B).
    assert union == pytest.approx(va + vb - common, abs=1e-9 * (va + vb))
    assert minus == pytest.approx(va - common, abs=1e-9 * (va + vb))
    for op in ("union", "difference"):
        assert_good_solid(reports[op][:2])
    if not reports["intersection"][2]["empty"]:
        assert_good_solid(reports["intersection"][:2])


def test_inward_solid_is_fixed_and_reported():
    a = box((0, 0, 0), (10, 10, 10))
    b = box((4, 3, 2), (14, 13, 12))
    inside_out = (b[0], b[1][:, ::-1].copy())
    assert mesh_stats(*inside_out)["orientation"] == "inward"
    v, t, report = boolean(a, inside_out, "difference", names=("Руда", "Карьер"))
    assert report["flipped"] == ["Карьер"]
    assert report["volume"] == pytest.approx(1000 - 336, abs=1e-6)
    assert_good_solid((v, t))
    assert boolean(a, b, "union")[2]["flipped"] == []


def test_boolean_many():
    boxes = [box((0, 0, 0), (10, 10, 10)), box((5, 0, 0), (15, 10, 10)), box((0, 5, 0), (10, 15, 10))]
    v, t, report = boolean_many(boxes, "union")
    assert_good_solid((v, t))
    assert report["volume"] == pytest.approx(2000, abs=1e-6)  # 1000 + 500 + 500
    assert report["volumes"] == pytest.approx([1000, 1000, 1000], abs=1e-6)
    assert boolean_many(boxes, "intersection")[2]["volume"] == pytest.approx(250, abs=1e-6)
    # The first minus the rest.
    assert boolean_many(boxes, "difference")[2]["volume"] == pytest.approx(250, abs=1e-6)
    with pytest.raises(SolidError, match="не меньше двух"):
        boolean_many(boxes[:1], "union")


def test_progress_is_reported_and_may_cancel():
    a, b = box((0, 0, 0), (10, 10, 10)), box((4, 3, 2), (14, 13, 12))
    seen = []
    boolean(a, b, "union", progress=lambda fraction, message: seen.append((fraction, message)))
    assert [f for f, _ in seen] == sorted(f for f, _ in seen) and seen[0][0] == 0.0 and seen[-1][0] == 1.0

    class Cancelled(Exception):
        pass

    def stop(fraction, message):
        raise Cancelled

    with pytest.raises(Cancelled):
        boolean(a, b, "union", progress=stop)


# ---- refused inputs


def test_open_mesh_is_refused_with_a_russian_message():
    a = box((0, 0, 0), (10, 10, 10))
    holed = (a[0], a[1][:-2])
    ok, reason = is_closed_solid(*holed)
    assert not ok and "открытых рёбер — 4" in reason
    with pytest.raises(SolidError) as error:
        boolean(holed, a, "union", names=("Рудное тело 1", "Карьер"))
    text = str(error.value)
    assert "Рудное тело 1" in text and "не замкнут" in text and "«Исправление каркаса»" in text
    with pytest.raises(SolidError, match="Тело B"):
        boolean(a, holed, "intersection")
    with pytest.raises(SolidError, match="Неизвестная операция"):
        boolean(a, a, "xor")


def test_other_defects_are_refused():
    v, t = box((0, 0, 0), (10, 10, 10))
    ok, reason = is_closed_solid(v, np.zeros((0, 3), dtype=np.int32))
    assert not ok and "пустой" in reason
    # One triangle turned over: closed, but the normals disagree.
    turned = t.copy()
    turned[0] = turned[0, ::-1]
    ok, reason = is_closed_solid(v, turned)
    assert not ok and "не согласованы" in reason
    with pytest.raises(SolidError, match="Исправление каркаса"):
        boolean((v, turned), (v, t), "union")
    # An extra triangle on an edge: three triangles share it.
    fin = (np.vstack([v, v[0] + [5, 5, 30]]), np.vstack([t, [[0, 1, 8]]]))
    ok, reason = is_closed_solid(*fin)
    assert not ok and "неманифолдных" in reason
    with pytest.raises(SolidError, match="несуществующие вершины"):
        boolean((v, t + 1), (v, t), "union")
    with pytest.raises(SolidError, match="не реализовано"):
        expand((v, t), 1.0)


# ---- clipping by a plane


def test_solid_cut_by_a_level():
    cube = box((0, 0, 0), (10, 10, 10))
    out = clip_by_plane(cube, OFFSET + [0, 0, 3], [0, 0, 1])
    assert_good_solid(out["positive"])
    assert_good_solid(out["negative"])
    assert volume(out["positive"]) == pytest.approx(700, abs=1e-6)
    assert volume(out["negative"]) == pytest.approx(300, abs=1e-6)
    report = out["report"]
    assert report["kind"] == "solid" and report["volume"] == pytest.approx(1000, abs=1e-6)
    assert report["sum_error"] <= 1e-9 * 1000
    assert out["positive"][0][:, 2].min() == pytest.approx(OFFSET[2] + 3, abs=1e-9)
    only = clip_by_plane(cube, OFFSET + [0, 0, 3], [0, 0, 1], keep="negative")
    assert only["positive"] is None and volume(only["negative"]) == pytest.approx(300, abs=1e-6)
    # A plane that misses the body: everything on one side, the other part is absent.
    missed = clip_by_plane(cube, OFFSET + [0, 0, 30], [0, 0, 1])
    assert missed["positive"] is None and volume(missed["negative"]) == pytest.approx(1000, abs=1e-6)
    with pytest.raises(SolidError, match="нулевой длины"):
        clip_by_plane(cube, OFFSET, [0, 0, 0])
    with pytest.raises(SolidError, match="Неизвестный вариант"):
        clip_by_plane(cube, OFFSET, [0, 0, 1], keep="up")


@pytest.mark.parametrize("body", ["sphere", "non-convex"])
def test_solid_cut_by_a_tilted_plane_keeps_the_volume(body):
    mesh = sphere(5, 48, 96, centre=(4, 4, 5)) if body == "sphere" else prism(L_SHAPE, 0, 10)
    out = clip_by_plane(mesh, OFFSET + [3.3, 4.1, 5.2], [0.3, -0.5, 0.8])
    total = volume(mesh)
    parts = volume(out["positive"]) + volume(out["negative"])
    # Sum of the parts = the source solid: relative 1e-9.
    assert parts == pytest.approx(total, rel=1e-9)
    assert out["report"]["sum_error"] <= 1e-9 * total
    assert_good_solid(out["positive"])
    assert_good_solid(out["negative"])
    assert out["report"]["volume_positive"] > 1 and out["report"]["volume_negative"] > 1
    # The cut faces lie on the plane: nothing of a part is on the wrong side (1e-9 m).
    n = np.array([0.3, -0.5, 0.8]) / np.linalg.norm([0.3, -0.5, 0.8])
    assert ((out["positive"][0] - OFFSET - [3.3, 4.1, 5.2]) @ n).min() > -1e-9
    assert ((out["negative"][0] - OFFSET - [3.3, 4.1, 5.2]) @ n).max() < 1e-9


def test_open_surface_cut_by_a_plane():
    surface = grid(40, 30, (0, 20), (0, 15), lambda x, y: np.sin(x / 3) + 0.2 * y)
    point, normal = OFFSET + [7.77, 3.3, 0], np.array([1.0, 0.4, 0.2])
    out = clip_by_plane(surface, point, normal)
    report = out["report"]
    assert report["kind"] == "surface" and not report["closed"]
    total = area(surface)
    # Areas of the parts add up to the surface: relative 1e-12.
    assert area(out["positive"]) + area(out["negative"]) == pytest.approx(total, rel=1e-12)
    assert report["sum_error"] <= 1e-12 * total and report["area"] == pytest.approx(total, rel=1e-12)
    n = normal / np.linalg.norm(normal)
    d_pos = (out["positive"][0] - point) @ n
    d_neg = (out["negative"][0] - point) @ n
    # Every vertex on its own side; the new vertices on the plane within 1e-9 m.
    assert d_pos.min() > -1e-9 and d_neg.max() < 1e-9
    assert np.count_nonzero(np.abs(d_pos) < 1e-9) == np.count_nonzero(np.abs(d_neg) < 1e-9) > 40
    # No caps and no cracks: the cut is one open boundary, the parts stay manifold sheets.
    for part in (out["positive"], out["negative"]):
        stats = mesh_stats(*part)
        assert not stats["closed"] and stats["nonmanifold_edges"] == 0 and stats["degenerate"] == 0
        assert orientation_conflicts(part[1], len(part[0])) == 0


def test_flat_surface_cut_through_its_vertices():
    """The plane passes exactly through grid nodes: known areas, no empty triangles."""
    surface = grid(10, 10, (0, 10), (0, 10), offset=np.zeros(3))
    out = clip_by_plane(surface, [4, 0, 0], [1, 0, 0])
    assert area(out["positive"]) == pytest.approx(60, abs=1e-12)
    assert area(out["negative"]) == pytest.approx(40, abs=1e-12)
    assert len(out["positive"][1]) == 120 and len(out["negative"][1]) == 80
    diagonal = clip_by_plane(surface, [0, 0, 0], [1, -1, 0], keep="positive")
    assert diagonal["negative"] is None and area(diagonal["positive"]) == pytest.approx(50, abs=1e-12)
    assert mesh_stats(*diagonal["positive"])["degenerate"] == 0


# ---- clipping by a polygon


def test_solid_cut_by_a_polygon():
    cube = box((0, 0, 0), (10, 10, 10))
    strip = np.array([[2, -5], [7, -5], [7, 15], [2, 15]]) + OFFSET[:2]
    out = clip_by_polygon(cube, strip, keep="both")
    assert_good_solid(out["inside"])
    assert_good_solid(out["outside"])
    assert volume(out["inside"]) == pytest.approx(500, abs=1e-6)
    assert volume(out["outside"]) == pytest.approx(500, abs=1e-6)
    assert out["report"]["sum_error"] <= 1e-9 * 1000
    # Default: the inside only; a clockwise or explicitly closed contour is the same polygon.
    closed_cw = np.vstack([strip[::-1], strip[-1:]])
    only = clip_by_polygon(cube, closed_cw)
    assert only["outside"] is None and volume(only["inside"]) == pytest.approx(500, abs=1e-6)


def test_solid_cut_by_a_non_convex_polygon_keeps_the_volume():
    ball = sphere(5, 48, 96, centre=(5, 5, 5))
    contour = np.array(L_SHAPE, dtype=np.float64) * 0.8 + [1.3, 0.7] + OFFSET[:2]
    out = clip_by_polygon(ball, contour, keep="both")
    total = volume(ball)
    # Sum of the parts = the source solid: relative 1e-9.
    assert volume(out["inside"]) + volume(out["outside"]) == pytest.approx(total, rel=1e-9)
    assert out["report"]["sum_error"] <= 1e-9 * total
    assert_good_solid(out["inside"])
    assert_good_solid(out["outside"])
    assert 0.2 * total < out["report"]["volume_inside"] < 0.8 * total


def test_open_surface_cut_by_a_polygon():
    """A DTM cut by a licence boundary: areas known for a plane, add up for a relief."""
    flat = grid(20, 20, (0, 20), (0, 20), lambda x, y: 0.5 * x)  # tilted plane
    contour = np.array(L_SHAPE, dtype=np.float64) + [3.3, 2.7] + OFFSET[:2]
    out = clip_by_polygon(flat, contour, keep="both")
    stretch = math.sqrt(1 + 0.25)
    # Plan area × the slope factor; tolerance 1e-9 relative.
    assert area(out["inside"]) == pytest.approx(64 * stretch, rel=1e-9)
    assert area(out["outside"]) == pytest.approx((400 - 64) * stretch, rel=1e-9)
    assert out["report"]["kind"] == "surface" and out["report"]["direction"] == [0, 0, 1]
    # The parts lie on the source surface (1e-9 m) and inside / outside the contour.
    for part in (out["inside"], out["outside"]):
        p = part[0] - OFFSET
        assert np.abs(p[:, 2] - 0.5 * p[:, 0]).max() < 1e-9
        assert not mesh_stats(*part)["closed"] and mesh_stats(*part)["nonmanifold_edges"] == 0
    inside = out["inside"][0][:, :2] - OFFSET[:2] - [3.3, 2.7]
    assert inside.min() > -1e-9 and inside.max() < 10 + 1e-9

    relief = grid(60, 60, (0, 20), (0, 20), lambda x, y: np.sin(x / 2) * np.cos(y / 3) * 2)
    out = clip_by_polygon(relief, contour, keep="both")
    total = area(relief)
    assert area(out["inside"]) + area(out["outside"]) == pytest.approx(total, rel=1e-9)
    assert out["report"]["sum_error"] <= 1e-9 * total
    # Away from the cut the triangulation of the surface is kept: its vertices are still there.
    kept = {tuple(p) for p in np.vstack([out["inside"][0], out["outside"][0]]).tolist()}
    assert all(tuple(p) in kept for p in relief[0].tolist())
    only = clip_by_polygon(relief, contour, keep="outside")
    assert only["inside"] is None and area(only["outside"]) == pytest.approx(area(out["outside"]), rel=1e-12)


def test_bad_polygons_are_refused():
    cube = box((0, 0, 0), (10, 10, 10))
    with pytest.raises(SolidError, match="не меньше трёх"):
        clip_by_polygon(cube, [[0, 0], [1, 1]])
    bow = np.array([[0, 0], [10, 10], [10, 0], [0, 10]]) + OFFSET[:2]
    with pytest.raises(SolidError, match="самопересекается"):
        clip_by_polygon(cube, bow)


# ---- sweeping and clipping by a surface


def test_sweep_solid():
    surface = grid(12, 8, (0, 12), (0, 8), lambda x, y: 0.3 * x + np.sin(y))
    v, t = sweep_solid(surface, [0, 0, -1], 7.0)
    assert_good_solid((v, t))
    # Plan area × length, whatever the relief: 1e-9 relative.
    assert volume((v, t)) == pytest.approx(12 * 8 * 7, rel=1e-9)
    assert len(t) == 2 * len(surface[1]) + 2 * 2 * (12 + 8)
    # Upwards, and with the triangles of the surface turned over: the same body.
    up = sweep_solid((surface[0], surface[1][:, ::-1]), [0, 0, 2], 7.0)
    assert_good_solid(up)
    assert volume(up) == pytest.approx(12 * 8 * 7, rel=1e-9)
    # Oblique direction: base area × length × cosine.
    oblique = sweep_solid(grid(4, 4, (0, 4), (0, 4)), [1, 0, -1], 5.0)
    assert_good_solid(oblique)
    assert volume(oblique) == pytest.approx(16 * 5 / math.sqrt(2), rel=1e-9)


def test_sweep_refuses_a_surface_that_is_not_a_sheet():
    # A vertical wall seen along Z.
    wall = grid(4, 4, (0, 4), (0, 4))
    wall = (wall[0][:, [0, 2, 1]], wall[1])
    with pytest.raises(SolidError, match="параллельных направлению"):
        sweep_solid(wall, [0, 0, 1], 5.0)
    assert mesh_stats(*sweep_solid(wall, [0, 1, 0], 5.0))["closed"]
    # A fold: the sheet turns back over itself.
    v, t = grid(8, 4, (0, 8), (0, 4), offset=np.zeros(3))
    folded = v.copy()
    over = v[:, 0] > 4
    folded[over, 0] = 8 - v[over, 0]
    folded[:, 2] = np.where(over, (v[:, 0] - 4) * 0.5, 0.0)
    with pytest.raises(SolidError, match="складки"):
        sweep_solid((folded, t), [0, 0, 1], 5.0)
    # Two separate sheets one above the other.
    upper = grid(8, 4, (2, 6), (1, 3), lambda x, y: 0 * x + 3, offset=np.zeros(3))
    stacked = (np.vstack([v, upper[0]]), np.vstack([t, upper[1] + len(v)]))
    with pytest.raises(SolidError, match="перекрывают друг друга"):
        sweep_solid(stacked, [0, 0, 1], 5.0)
    with pytest.raises(SolidError, match="положительной"):
        sweep_solid((v, t), [0, 0, 1], 0.0)


def test_solid_cut_by_a_level_surface():
    cube = box((0, 0, 0), (10, 10, 10))
    dtm = grid(7, 9, (-3, 13), (-2, 12), lambda x, y: 0 * x + 4)
    out = clip_by_surface(cube, dtm)
    report = out["report"]
    assert report["covers"] and "warning" not in report and report["direction"] == [0, 0, 1]
    assert volume(out["above"]) == pytest.approx(600, abs=1e-6)
    assert volume(out["below"]) == pytest.approx(400, abs=1e-6)
    assert report["volume_above"] == pytest.approx(600, abs=1e-6)
    assert report["sum_error"] <= 1e-9 * 1000
    assert_good_solid(out["above"])
    assert_good_solid(out["below"])
    for keep, other in (("above", "below"), ("below", "above")):
        only = clip_by_surface(cube, dtm, keep=keep)
        assert only[other] is None and only[keep] is not None
    # The surface turned over is the same surface: "above" is still +Z.
    turned = clip_by_surface(cube, (dtm[0], dtm[1][:, ::-1]))
    assert turned["report"]["volume_above"] == pytest.approx(600, abs=1e-6)


def test_solid_cut_by_an_undulating_surface():
    ball = sphere(5, 48, 96, centre=(5, 5, 5))
    relief = lambda x, y: 5 + 1.5 * np.sin(x / 2) * np.cos(y / 3) + 0.1 * x
    dtm = grid(80, 80, (-2, 12), (-2, 12), relief)
    out = clip_by_surface(ball, dtm)
    report = out["report"]
    total = volume(ball)
    # Sum of the parts = the source solid: relative 1e-9.
    assert volume(out["above"]) + volume(out["below"]) == pytest.approx(total, rel=1e-9)
    assert report["sum_error"] <= 1e-9 * total and report["covers"]
    assert_good_solid(out["above"])
    assert_good_solid(out["below"])
    assert 0.3 * total < report["volume_below"] < 0.7 * total
    # The parts lie on their sides of the surface: every vertex within 0.02 m of the right
    # side (the surface is piecewise linear, the check uses the smooth function).
    above, below = out["above"][0] - OFFSET, out["below"][0] - OFFSET
    assert (above[:, 2] - relief(above[:, 0], above[:, 1])).min() > -0.02
    assert (below[:, 2] - relief(below[:, 0], below[:, 1])).max() < 0.02
    # The same cut as a boolean with the swept surface (independent path): 1e-9 relative.
    swept = sweep_solid(dtm, [0, 0, -1], 50.0)
    assert boolean(ball, swept, "intersection")[2]["volume"] == pytest.approx(report["volume_below"], rel=1e-9)


def test_solid_cut_by_a_tilted_fault():
    """A steep plane through the centre of a cube: not single-valued in Z is not required,
    the direction is taken from the surface's own normal."""
    cube = box((0, 0, 0), (10, 10, 10))
    # The plane x + 0.2 z = 6 as a sheet parametrised by (y, z): vertical in plan view.
    v, t = grid(10, 10, (-5, 15), (-5, 15), offset=np.zeros(3))
    fault = np.column_stack([6 - 0.2 * v[:, 1], v[:, 0], v[:, 1]]) + OFFSET
    out = clip_by_surface(cube, (fault, t), direction=[1, 0, 0.2])
    # Below (x + 0.2 z < 6): a prism with the trapezoid 6 … 4 wide: 10 × (6 + 4) / 2 × 10.
    assert out["report"]["volume_below"] == pytest.approx(500, abs=1e-6)
    assert out["report"]["volume_above"] == pytest.approx(500, abs=1e-6)
    assert out["report"]["covers"]
    assert_good_solid(out["above"])
    assert_good_solid(out["below"])
    # Default direction: the plane is not vertical, so Z is taken, but along Z the steep
    # sheet covers only a strip of the cube (3 <= x <= 7): the rest is reported, not cut.
    auto = clip_by_surface(cube, (fault, t))
    assert auto["report"]["direction"] == [0, 0, 1] and not auto["report"]["covers"]
    assert "по её нормали" in auto["report"]["warning"]
    assert auto["report"]["volume_below"] == pytest.approx(200, abs=1e-6)  # triangle 4 × 10 / 2 in section
    # "normal": the dominant normal of the sheet, (1, 0, 0.2) up to sign and length.
    normal = clip_by_surface(cube, (fault, t), direction="normal")
    axis = np.array(normal["report"]["direction"])
    assert np.abs(axis) == pytest.approx(np.array([1, 0, 0.2]) / math.sqrt(1.04), abs=1e-12)
    assert normal["report"]["covers"] and normal["report"]["volume_above"] == pytest.approx(500, abs=1e-6)
    # A vertical fault: Z is impossible, the dominant normal is used.
    wall = np.column_stack([np.full(len(v), 3.0), v[:, 0], v[:, 1]]) + OFFSET
    vertical = clip_by_surface(cube, (wall, t))
    assert np.abs(vertical["report"]["direction"]) == pytest.approx([1, 0, 0], abs=1e-12)
    sign = vertical["report"]["direction"][0]
    assert vertical["report"]["volume_above"] == pytest.approx(700 if sign > 0 else 300, abs=1e-6)
    assert vertical["report"]["sum_error"] <= 1e-9 * 1000


def test_surface_that_does_not_cover_the_solid():
    cube = box((0, 0, 0), (10, 10, 10))
    dtm = grid(6, 10, (-2, 6), (-2, 12), lambda x, y: 0 * x + 4)  # covers x < 6 only
    out = clip_by_surface(cube, dtm)
    report = out["report"]
    assert not report["covers"]
    assert "не покрывает" in report["warning"]
    # Below: under the surface where it exists; the rest, uncovered part included, is "above".
    assert report["volume_below"] == pytest.approx(6 * 10 * 4, abs=1e-6)
    assert report["volume_above"] == pytest.approx(1000 - 240, abs=1e-6)
    assert report["uncovered_volume"] == pytest.approx(4 * 10 * 10, abs=1e-6)
    assert report["sum_error"] <= 1e-9 * 1000
    assert_good_solid(out["above"])
    assert_good_solid(out["below"])
    # A separate part of the solid entirely beside the surface.
    far = box((20, 0, 0), (25, 5, 5))
    two = (np.vstack([cube[0], far[0]]), np.vstack([cube[1], far[1] + 8]))
    wide = grid(8, 8, (-2, 12), (-2, 12), lambda x, y: 0 * x + 4)
    report = clip_by_surface(two, wide)["report"]
    assert not report["covers"] and report["uncovered_volume"] == pytest.approx(125, abs=1e-6)
    assert report["volume_below"] == pytest.approx(400, abs=1e-6)
    # A surface that misses the solid altogether: nothing below.
    aside = grid(4, 4, (50, 60), (50, 60))
    missed = clip_by_surface(cube, aside)
    assert missed["below"] is None and not missed["report"]["covers"]
    assert missed["report"]["volume_above"] == pytest.approx(1000, abs=1e-6)


def test_clip_by_surface_refuses_bad_inputs():
    cube = box((0, 0, 0), (10, 10, 10))
    dtm = grid(4, 4, (-2, 12), (-2, 12), lambda x, y: 0 * x + 4)
    with pytest.raises(SolidError, match="Карьер.*не замкнут"):
        clip_by_surface((cube[0], cube[1][:-1]), dtm, names=("Карьер", "Рельеф"))
    with pytest.raises(SolidError, match="Рельеф.*не является однозначной"):
        clip_by_surface(cube, dtm, direction=[1, 0, 0], names=("Карьер", "Рельеф"))


# ---- intersection lines


def test_two_crossing_planes_give_one_straight_line():
    horizontal = grid(13, 7, (0, 10), (0, 10), lambda x, y: 0 * x + 2)
    # The plane x = 4 over y in [-3, 8], z in [0, 5].
    v, t = grid(9, 5, (-3, 8), (0, 5), offset=np.zeros(3))
    vertical = (np.column_stack([np.full(len(v), 4.0), v[:, 0], v[:, 1]]) + OFFSET, t)
    lines = intersection_lines(horizontal, vertical)
    assert len(lines) == 1 and not lines[0]["closed"]
    points = lines[0]["points"] - OFFSET
    assert points.dtype == np.float64
    # A straight line x = 4, z = 2 from y = 0 to y = 8: 1e-9 m.
    assert np.abs(points[:, 0] - 4).max() < 1e-9 and np.abs(points[:, 2] - 2).max() < 1e-9
    ends = sorted([points[0, 1], points[-1, 1]])
    assert ends == pytest.approx([0, 8], abs=1e-9)
    assert length(points, False) == pytest.approx(8, abs=1e-9)
    assert np.all(np.diff(points[:, 1]) != 0) and len({np.sign(d) for d in np.diff(points[:, 1])}) == 1
    # The order of the arguments does not matter; apart meshes give nothing.
    again = intersection_lines(vertical, horizontal)
    assert len(again) == 1 and length(again[0]["points"], False) == pytest.approx(8, abs=1e-9)
    assert intersection_lines(horizontal, grid(3, 3, (0, 10), (0, 10), lambda x, y: 0 * x + 9)) == []


def test_sphere_and_plane_give_a_closed_loop():
    r, h = 5.0, 1.3
    ball = sphere(r, 96, 192)
    plane = grid(37, 41, (-7, 7), (-7, 7), lambda x, y: 0 * x + h)
    lines = intersection_lines(ball, plane)
    assert len(lines) == 1 and lines[0]["closed"]
    points = lines[0]["points"] - OFFSET
    assert np.abs(points[:, 2] - h).max() < 1e-9
    radius = math.sqrt(r * r - h * h)
    # The loop is a polygon inscribed in the circle (192 meridians): its length is below the
    # circle's by < 0.1 %, the points are within 0.1 % of the radius.
    assert length(points, True) == pytest.approx(2 * math.pi * radius, rel=1e-3)
    assert np.hypot(points[:, 0], points[:, 1]) == pytest.approx(radius, rel=1e-3)
    assert not np.allclose(points[0], points[-1])  # a closed line does not repeat its first point
    # The same loop as the exact section of the sphere by the plane.
    from app.geo.section import plane_section

    section = plane_section(ball[0], ball[1], OFFSET, OFFSET + [0, 0, h], [0, 0, 1])
    assert len(section) == 1
    assert length(points, True) == pytest.approx(length(np.asarray(section[0]["points"]), True), abs=1e-5)


def test_two_cubes_cross_along_one_loop():
    a = box((0, 0, 0), (10, 10, 10))
    b = box((4, 3, 2), (14, 13, 12))
    lines = intersection_lines(a, b)
    assert len(lines) == 1 and lines[0]["closed"]
    points = lines[0]["points"] - OFFSET
    # A hexagon-like loop around the corner (10, 10, 10) of A and (4, 3, 2) of B:
    # on A's faces x = 10, y = 10, z = 10 the edges of B's box are 7 + 8, 6 + 8, 6 + 7 long.
    assert length(points, True) == pytest.approx(2 * (6 + 7 + 8), abs=1e-9)
    corners = {(10, 3, 2), (4, 10, 2), (4, 3, 10), (10, 10, 2), (10, 3, 10), (4, 10, 10)}
    found = {tuple(np.round(p, 9).tolist()) for p in points}
    assert corners <= found
    # Every point lies on the surface of both cubes.
    on_a = np.isclose(points, 0, atol=1e-9) | np.isclose(points, 10, atol=1e-9)
    on_b = np.isclose(points, [4, 3, 2], atol=1e-9) | np.isclose(points, [14, 13, 12], atol=1e-9)
    assert on_a.any(axis=1).all() and on_b.any(axis=1).all()


def test_intersection_line_bounds_the_boolean_result():
    """Two spheres: the line is the edge between the two skins of their intersection."""
    a, b = sphere(5, 64, 128), sphere(4, 48, 96, centre=(3, 2, 1))
    lines = intersection_lines(a, b)
    assert len(lines) == 1 and lines[0]["closed"]
    points = lines[0]["points"]
    # The circle where the spheres meet: radius from the distance between the centres.
    d = math.sqrt(14)
    x = (d * d + 25 - 16) / (2 * d)
    assert length(points, True) == pytest.approx(2 * math.pi * math.sqrt(25 - x * x), rel=5e-3)
    v, _, _ = boolean(a, b, "intersection")
    known = {tuple(p) for p in np.round(v, 6).tolist()}
    hits = sum(tuple(p) in known for p in np.round(points, 6).tolist())
    # The boolean result has the same vertices along the cut (1e-6 m); manifold3d may merge
    # some that are closer than its tolerance.
    assert hits >= 0.95 * len(points)


# ---- speed


def test_speed_boolean_one_million_triangles():
    ball = sphere(50, 500, 1000, centre=(0, 0, 0))
    assert len(ball[1]) == 998_000
    half = box((0, -60, -60), (60, 60, 60))
    boolean(box((0, 0, 0), (1, 1, 1)), box((0.5, 0, 0), (2, 1, 1)), "union")  # imports, numba warm-up
    start = time.perf_counter()
    v, t, report = boolean(ball, half, "intersection")
    elapsed = time.perf_counter() - start
    print(f"\nboolean: sphere of {len(ball[1])} triangles ∩ cube: {elapsed:.2f} s, result {len(t)} triangles")
    assert report["volume"] == pytest.approx(volume(ball) / 2, rel=1e-9)
    assert mesh_stats(v, t)["closed"]
    assert elapsed < 30  # generous: about 1–2 s on four cores

    start = time.perf_counter()
    v, t, report = boolean(ball, sphere(50, 500, 1000, centre=(30, 20, 10)), "union")
    elapsed = time.perf_counter() - start
    print(f"boolean: union of two spheres of {len(ball[1])} triangles: {elapsed:.2f} s, result {len(t)} triangles")
    assert mesh_stats(v, t)["closed"] and report["parts"] == 1
    assert elapsed < 60


def test_speed_intersection_lines_two_hundred_thousand_triangles():
    n = 317  # 2 × 317² = 200 978 triangles each
    a = grid(n, n, (0, 1000), (0, 1000), lambda x, y: 20 * np.sin(x / 60) * np.cos(y / 45))
    b = grid(n, n, (3, 1003), (-2, 998), lambda x, y: 15 * np.cos(x / 50) + 0.01 * y - 5)
    intersection_lines(grid(2, 2, (0, 1), (0, 1)), grid(2, 2, (0, 1), (0, 1), lambda x, y: x - 0.5))  # numba warm-up
    start = time.perf_counter()
    lines = intersection_lines(a, b)
    elapsed = time.perf_counter() - start
    points = sum(len(line["points"]) for line in lines)
    print(f"\nintersection_lines: {len(a[1])} × {len(b[1])} triangles: {elapsed:.2f} s, "
          f"{len(lines)} lines, {points} points")
    assert len(lines) > 3 and points > 2000
    # Every point lies on both surfaces: within 0.05 m of the smooth functions (facets of 3 m).
    p = np.vstack([line["points"] for line in lines]) - OFFSET
    assert np.abs(p[:, 2] - 20 * np.sin(p[:, 0] / 60) * np.cos(p[:, 1] / 45)).max() < 0.05
    assert np.abs(p[:, 2] - (15 * np.cos(p[:, 0] / 50) + 0.01 * p[:, 1] - 5)).max() < 0.05
    assert elapsed < 20  # generous: well under a second on four cores

    # A mesh against a coarse body with triangles far larger than the grid cells.
    ball = sphere(400, 224, 448, centre=(500, 500, 0))
    start = time.perf_counter()
    lines = intersection_lines(a, ball)
    elapsed = time.perf_counter() - start
    print(f"intersection_lines: {len(a[1])} × {len(ball[1])} triangles: {elapsed:.2f} s, {len(lines)} lines")
    assert len(lines) == 1 and lines[0]["closed"]
    assert elapsed < 20


def test_module_is_exported():
    for name in ("SolidError", "is_closed_solid", "boolean", "boolean_many", "clip_by_plane", "clip_by_polygon",
                 "sweep_solid", "clip_by_surface", "intersection_lines", "expand"):
        assert hasattr(solids, name)
