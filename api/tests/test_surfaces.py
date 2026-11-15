"""Surfaces: DTM (constrained Delaunay), contours, volumes — synthetic data with known answers.

Pure geometry, no database. Tolerances are written in every test.
"""

import time
from itertools import pairwise

import numpy as np
import pytest
from scipy.spatial import ConvexHull, Delaunay

from app.formats.wireframes import mesh_stats
from app.geo.surfaces import (
    SurfaceError,
    build_dtm,
    contours,
    surface_footprint,
    under_solid,
    volume_between,
    volume_to_level,
)

UTM = np.array([512345.678, 6789012.345, 0.0])


def plan_areas(v, t):
    """Signed plan areas of triangles (coordinates centred first)."""
    p = v[:, :2] - np.floor(v[:, :2].mean(axis=0))
    a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
    return 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))


def edge_set(t):
    e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    return {(min(a, b), max(a, b)) for a, b in e.tolist()}


def vertex_index(v, p):
    """Index of the vertex with exactly these X, Y."""
    hit = np.nonzero((v[:, 0] == p[0]) & (v[:, 1] == p[1]))[0]
    assert len(hit) == 1, f"vertex {p} not found exactly once"
    return int(hit[0])


def assert_breakline_is_edges(v, t, line, tol=1e-7):
    """Every segment of the line is an edge or a chain of collinear edges; no edge crosses it."""
    edges = edge_set(t)
    ea = np.array(sorted(edges))
    o = np.floor(v[:, :2].mean(axis=0))
    p = v[:, :2] - o
    for a3, b3 in pairwise(line):
        a, b = a3[:2] - o, b3[:2] - o
        d = b - a
        length = np.hypot(*d)
        s = ((p - a) @ d) / length ** 2
        off = np.abs((p[:, 0] - a[0]) * d[1] - (p[:, 1] - a[1]) * d[0]) / length
        on = np.nonzero((off <= tol) & (s >= -tol / length) & (s <= 1 + tol / length))[0]
        on = on[np.argsort(s[on])]
        assert len(on) >= 2
        assert np.hypot(*(p[on[0]] - a)) <= tol and np.hypot(*(p[on[-1]] - b)) <= tol
        for i, j in pairwise(on.tolist()):
            assert (min(i, j), max(i, j)) in edges, "a piece of the breakline is not an edge"
        # No edge crosses the segment properly.
        pa, pb = p[ea[:, 0]], p[ea[:, 1]]
        d1 = d[0] * (pa[:, 1] - a[1]) - d[1] * (pa[:, 0] - a[0])
        d2 = d[0] * (pb[:, 1] - a[1]) - d[1] * (pb[:, 0] - a[0])
        e = pb - pa
        d3 = e[:, 0] * (a[1] - pa[:, 1]) - e[:, 1] * (a[0] - pa[:, 0])
        d4 = e[:, 0] * (b[1] - pa[:, 1]) - e[:, 1] * (b[0] - pa[:, 0])
        eps = tol * length
        crossing = (d1 * d2 < 0) & (d3 * d4 < 0) & (np.abs(d1) > eps) & (np.abs(d2) > eps)
        assert not crossing.any(), "an edge crosses the breakline"


def cloud(n, f, seed=0, size=100.0, offset=UTM):
    rng = np.random.default_rng(seed)
    xy = rng.random((n, 2)) * size
    return np.column_stack([xy, f(xy[:, 0], xy[:, 1])]) + offset


def grid(nx, ny, f, w=100.0, h=50.0, offset=UTM):
    x, y = np.meshgrid(np.linspace(0, w, nx), np.linspace(0, h, ny))
    x, y = x.ravel(), y.ravel()
    return np.column_stack([x, y, f(x, y)]) + offset


# ---------------------------------------------------------------- DTM


def test_dtm_plane_nodes_exact_count_and_area():
    def plane(x, y):
        return 100.0 + 0.3 * x - 0.2 * y

    pts = cloud(3000, plane, seed=1)
    v, t, report = build_dtm(pts)
    # Deviation in the nodes is exactly zero: the vertices are the input points, bit for bit.
    assert np.array_equal(v, pts)
    assert t.dtype == np.int32 and v.dtype == np.float64
    # Points in general position: triangles = 2n - 2 - h (h = points on the convex hull).
    hull = ConvexHull(pts[:, :2] - UTM[:2])
    assert len(t) == 2 * len(pts) - 2 - len(hull.vertices) == report["triangles"]
    assert (plan_areas(v, t) > 0).all()  # oriented upward
    # Plan area = area of the convex hull; true area = plan area * sqrt(1 + a^2 + b^2). rel 1e-12.
    assert report["area_plan"] == pytest.approx(hull.volume, rel=1e-12)
    assert report["area"] == pytest.approx(hull.volume * np.sqrt(1 + 0.3 ** 2 + 0.2 ** 2), rel=1e-12)
    assert report["points"] == 3000 and report["merged_duplicates"] == 0 and report["breakline_segments"] == 0
    assert "CDT" in report["method"] and report["tolerance"] == pytest.approx(1e-9 * 100, rel=1e-3)


def test_dtm_plane_patch_on_grid_is_analytic():
    _, t, report = build_dtm(grid(21, 11, lambda x, y: 5 + 0.75 * x))
    assert len(t) == 2 * 20 * 10
    assert report["area_plan"] == pytest.approx(100 * 50, rel=1e-13)
    assert report["area"] == pytest.approx(100 * 50 * 1.25, rel=1e-13)  # sqrt(1 + 0.75^2) = 1.25


def test_dtm_analytic_surface_is_delaunay():
    pts = cloud(2000, lambda x, y: 20 * np.sin(x / 15) * np.cos(y / 20), seed=2)
    v, t, _ = build_dtm(pts)
    assert np.array_equal(v, pts)
    assert (plan_areas(v, t) > 0).all()
    reference = Delaunay(pts[:, :2] - np.floor(UTM[:2]))
    assert {tuple(sorted(s)) for s in t.tolist()} == {tuple(sorted(s)) for s in reference.simplices.tolist()}


def test_dtm_breakline_diagonal_ridge():
    pts = cloud(1500, lambda x, y: 10 + 0 * x, seed=3)
    # A ridge across the cloud; its vertices are not among the points.
    ridge = np.array([[3.0, 4.0, 25.0], [31.0, 28.5, 27.0], [62.0, 70.0, 26.0], [97.0, 95.0, 24.0]]) + UTM
    v, t, report = build_dtm(pts, [ridge])
    assert report["breakline_segments"] == 3 and report["points"] == 1504
    assert np.array_equal(v[:1500], pts) and np.array_equal(v[1500:], ridge)
    assert_breakline_is_edges(v, t, ridge)
    assert (plan_areas(v, t) > 0).all()
    # Without the breakline some edge does cross the ridge (the check above is not vacuous).
    v0, t0, _ = build_dtm(np.concatenate([pts, ridge]))
    with pytest.raises(AssertionError):
        assert_breakline_is_edges(v0, t0, ridge)


def test_dtm_closed_breakline_pit():
    pts = grid(26, 26, lambda x, y: 10 + 0 * x, w=50, h=50)
    crest = np.array([[11.3, 12.1, 10], [38.7, 12.1, 10], [38.7, 37.4, 10], [11.3, 37.4, 10], [11.3, 12.1, 10.0]]) + UTM
    v, t, report = build_dtm(pts, [crest])
    assert report["breakline_segments"] == 4
    assert report["merged_duplicates"] == 1  # the repeated closing vertex
    assert_breakline_is_edges(v, t, crest)
    assert report["area_plan"] == pytest.approx(2500, rel=1e-12)


def test_dtm_breaklines_crossing_at_equal_z():
    pts = cloud(800, lambda x, y: 5 + 0 * x, seed=4)
    one = np.array([[10.0, 10.0, 20.0], [90.0, 90.0, 40.0]]) + UTM
    two = np.array([[10.0, 90.0, 28.0], [90.0, 10.0, 32.0]]) + UTM  # both are at Z = 30 in (50, 50)
    v, t, report = build_dtm(pts, [one, two])
    assert report["crossings_added"] == 1 and report["points"] == 805
    cross = v[-1]
    assert cross[:2] == pytest.approx(UTM[:2] + 50, abs=1e-8) and cross[2] == pytest.approx(30.0, abs=1e-9)
    assert_breakline_is_edges(v, t, one)
    assert_breakline_is_edges(v, t, two)
    # T-junction: a line ending on another one at the same Z is fine too.
    tee = np.array([[50.0, 50.0, 30.0], [50.0, 95.0, 11.0]]) + UTM
    v, t, report = build_dtm(pts, [one, tee])
    assert_breakline_is_edges(v, t, one)
    assert_breakline_is_edges(v, t, tee)


def test_dtm_breaklines_crossing_at_different_z_is_an_error():
    pts = cloud(200, lambda x, y: 5 + 0 * x, seed=5)
    one = np.array([[10.0, 10.0, 20.0], [90.0, 90.0, 40.0]]) + UTM
    two = np.array([[10.0, 90.0, 50.0], [90.0, 10.0, 50.0]]) + UTM
    with pytest.raises(SurfaceError) as err:
        build_dtm(pts, [one, two])
    message = str(err.value)
    assert "1 шт" in message and "512395.678" in message and "30.000" in message and "50.000" in message


def test_dtm_duplicates():
    pts = cloud(500, lambda x, y: 0.1 * x + 7, seed=6)
    # Identical duplicates merge silently; the first of them is kept.
    v, _, report = build_dtm(np.concatenate([pts, pts[:40]]))
    assert report["merged_duplicates"] == 40 and np.array_equal(v, pts)
    # Within the tolerance (1e-9 of the extent) in plan — the same.
    near = pts[:10].copy()
    near[:, 0] += 3e-8
    v, _, report = build_dtm(np.concatenate([pts, near]))
    assert report["merged_duplicates"] == 10 and np.array_equal(v, pts)
    # Different Z: an error with the number of such places and examples, never a silent choice.
    clash = pts[:7].copy()
    clash[:, 2] += 1.5
    with pytest.raises(SurfaceError) as err:
        build_dtm(np.concatenate([pts, clash]))
    message = str(err.value)
    assert "7 шт" in message and message.count("X=") == 5
    assert f"X={pts[0, 0]:.3f} Y={pts[0, 1]:.3f}" in message
    assert f"{pts[0, 2]:.3f}, {pts[0, 2] + 1.5:.3f}" in message


def test_dtm_refuses_bad_input():
    with pytest.raises(SurfaceError):
        build_dtm(None)
    with pytest.raises(SurfaceError):
        build_dtm(np.array([[0.0, 0, 0], [1, 1, 1], [2, 2, 2]]))  # collinear
    with pytest.raises(SurfaceError):
        build_dtm(np.array([[0.0, 0, 0], [1, 0, np.nan], [0, 1, 0]]))


def test_dtm_boundary_concave_polygon():
    def plane(x, y):
        return 50 + 0.1 * x + 0.2 * y

    pts = cloud(4000, plane, seed=7)
    ring = np.array([[10.0, 10], [90, 10], [90, 90], [50, 45], [10, 90]]) + UTM[:2]
    v, t, report = build_dtm(pts, boundary=ring)
    # The surface fills the polygon exactly: area 80*80 - (80*45/2) = 4600, rel 1e-12.
    assert report["area_plan"] == pytest.approx(4600.0, rel=1e-12)
    assert plan_areas(v, t).sum() == pytest.approx(4600.0, rel=1e-12)
    # The polygon's vertices are in the surface; their Z is interpolated from the surface (a plane: abs 1e-9).
    for corner in ring:
        k = vertex_index(v, corner)
        assert v[k, 2] == pytest.approx(plane(*(corner - UTM[:2])), abs=1e-9)
    closed = np.column_stack([np.vstack([ring, ring[:1]]), np.zeros(6)])
    assert_breakline_is_edges(v, t, closed)
    loops = surface_footprint(v, t)
    assert len(loops) == 1 and len(loops[0]) == 5
    assert report["unused_points"] > 0 and report["points"] == len(v) < 4005
    # Every kept input point is unchanged.
    inside = {tuple(p) for p in pts.tolist()}
    assert sum(tuple(p) in inside for p in v.tolist()) == len(v) - 5

    # Boundary with Z: structural, the given Z is kept.
    ring3 = np.column_stack([ring, [70.0, 71, 72, 73, 74]])
    v, t, report = build_dtm(pts, boundary=ring3)
    assert report["area_plan"] == pytest.approx(4600.0, rel=1e-12)
    assert v[vertex_index(v, ring3[2])][2] == 72.0

    # A boundary reaching beyond the data: Z of the nearest point of the data hull.
    square = np.array([[0.0, 0], [100, 0], [100, 100], [0, 100]])
    data = grid(11, 11, lambda x, y: 3 + 0 * x, w=100, h=100, offset=0)
    wide = np.array([[-20.0, -20], [120, -20], [120, 120], [-20, 120]])
    v, t, report = build_dtm(data, boundary=wide)
    assert report["area_plan"] == pytest.approx(140 * 140, rel=1e-12) and report["boundary_extrapolated"] == 4
    assert (v[:, 2] == 3.0).all()
    assert build_dtm(data, boundary=square)[2]["boundary_extrapolated"] == 0

    with pytest.raises(SurfaceError):  # self-intersecting boundary
        build_dtm(pts, boundary=np.array([[10.0, 10], [90, 90], [90, 10], [10, 90]]) + UTM[:2])


def test_dtm_boundary_crossing_a_breakline():
    pts = cloud(1000, lambda x, y: 5 + 0 * x, seed=8)
    line = np.array([[5.0, 50.0, 30.0], [95.0, 50.0, 40.0]]) + UTM
    ring = np.array([[20.0, 20], [80, 20], [80, 80], [20, 80]]) + UTM[:2]
    v, t, report = build_dtm(pts, [line], boundary=ring)
    assert report["area_plan"] == pytest.approx(3600.0, rel=1e-12) and report["crossings_added"] == 2
    # The crossing points take the Z of the breakline: 30 + 10*(15/90) and 30 + 10*(75/90).
    left = v[np.argmin(np.hypot(v[:, 0] - ring[0, 0], v[:, 1] - UTM[1] - 50))]
    right = v[np.argmin(np.hypot(v[:, 0] - ring[1, 0], v[:, 1] - UTM[1] - 50))]
    assert left[2] == pytest.approx(30 + 10 * 15 / 90, abs=1e-9)
    assert right[2] == pytest.approx(30 + 10 * 75 / 90, abs=1e-9)
    assert_breakline_is_edges(v, t, np.array([left, right]))


def test_dtm_max_edge_peels_from_outside_only():
    # Unit grid in the shape of the letter C (20 x 20 without the block x > 6, 6 < y < 14).
    x, y = np.meshgrid(np.arange(21.0), np.arange(21.0))
    x, y = x.ravel(), y.ravel()
    keep = ~((x > 6) & (y > 6) & (y < 14))
    pts = np.column_stack([x[keep], y[keep], 0.01 * x[keep]]) + UTM
    v, t, report = build_dtm(pts)
    assert report["area_plan"] == pytest.approx(400.0, rel=1e-12) and report["dropped_long"] == 0
    v, t, report = build_dtm(pts, max_edge=1.5)
    # The mouth of the C (14 x 8) is open again, except the two short corner triangles (legs 1, 1) at its
    # inner corners; everything else stays. rel 1e-12.
    assert report["area_plan"] == pytest.approx(400.0 - 14 * 8 + 2 * 0.5, rel=1e-12)
    assert report["dropped_long"] > 0 and np.array_equal(v, pts)
    edges = np.concatenate([v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 1]], v[t[:, 0]] - v[t[:, 2]]])
    assert np.hypot(edges[:, 0], edges[:, 1]).max() < 1.5

    # A gap enclosed by short triangles is not opened: long triangles go only from the outside inward.
    keep = ~((x > 6) & (x < 14) & (y > 6) & (y < 14))
    ring_pts = np.column_stack([x[keep], y[keep], 0 * x[keep]]) + UTM
    v, t, report = build_dtm(ring_pts, max_edge=1.5)
    assert report["dropped_long"] == 0 and report["area_plan"] == pytest.approx(400.0, rel=1e-12)

    with pytest.raises(SurfaceError):
        build_dtm(ring_pts, max_edge=0.5)  # nothing left


# ---------------------------------------------------------------- contours


def test_contours_of_a_plane_are_straight_parallel_lines():
    v, t, _ = build_dtm(grid(41, 17, lambda x, y: 0.5 * x, w=10, h=4))
    lines = contours(v, t, interval=1.0, major_every=2)
    # z = 0.5 x over x in [0, 10]: levels 1..5 at x = 2, 4, 6, 8, 10. Level 0 only touches the lowest
    # edge of the surface (nothing lies below it), so it gives no line.
    assert [c["z"] for c in lines] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert [c["major"] for c in lines] == [False, True, False, True, False]
    for c in lines:
        p = c["points"]
        assert not c["closed"] and p.dtype == np.float64 and p.shape[1] == 3
        assert np.abs(p[:, 0] - (UTM[0] + 2 * c["z"])).max() < 1e-9  # position, abs 1e-9 m
        assert (p[:, 2] == c["z"]).all()
        y = p[:, 1] - UTM[1]
        assert min(y[0], y[-1]) == pytest.approx(0, abs=1e-9) and max(y[0], y[-1]) == pytest.approx(4, abs=1e-9)
        assert (np.diff(y) > 0).all() or (np.diff(y) < 0).all()  # ordered, no repeated points
    # Limits and base.
    assert [c["z"] for c in contours(v, t, interval=1.0, z_min=2.0, z_max=3.5)] == [2.0, 3.0]
    shifted = contours(v, t, interval=2.0, base=0.5, major_every=1)
    assert [c["z"] for c in shifted] == [0.5, 2.5, 4.5] and all(c["major"] for c in shifted)
    assert contours(v, t, interval=100.0, base=50.0) == []
    with pytest.raises(SurfaceError):
        contours(v, t, interval=0.0)
    with pytest.raises(SurfaceError):
        contours(v, t, interval=1e-9)


def test_contours_of_a_paraboloid_are_closed_circles():
    # z = (x^2 + y^2) / 100 on a unit grid, radius of level L: 10 * sqrt(L).
    x, y = np.meshgrid(np.arange(-60.0, 61.0), np.arange(-60.0, 61.0))
    x, y = x.ravel(), y.ravel()
    pts = np.column_stack([x, y, (x * x + y * y) / 100.0]) + UTM
    v, t, _ = build_dtm(pts)
    lines = contours(v, t, interval=2.5, major_every=5, z_max=35.0)
    closed = [c for c in lines if c["closed"]]
    # Levels 2.5 .. 35 (14 levels) lie inside the grid (radius up to 59.2): one closed circle each.
    assert [c["z"] for c in lines] == [2.5 * k for k in range(1, 15)] and len(closed) == 14
    assert [c["major"] for c in lines] == [k % 5 == 0 for k in range(1, 15)]
    for c in closed:
        p = c["points"]
        r = np.hypot(p[:, 0] - UTM[0], p[:, 1] - UTM[1])
        # Linear interpolation on a unit grid: the radius is exact on the grid lines and off by less than
        # h^2/(8R)-order elsewhere; 0.02 m is the discretisation tolerance.
        assert np.abs(r - 10 * np.sqrt(c["z"])).max() < 0.02
        assert len(np.unique(p[:, :2], axis=0)) == len(p)  # no repeated points
        assert not np.array_equal(p[0], p[-1])
        # Higher ground (outside of the bowl) on the left: the circles run clockwise.
        q = p[:, :2] - UTM[:2]
        assert np.sum(q[:, 0] * np.roll(q[:, 1], -1) - np.roll(q[:, 0], -1) * q[:, 1]) < 0
        gaps = np.hypot(*(np.roll(q, -1, axis=0) - q).T)
        assert gaps.max() < 1.5  # chained without gaps (a segment stays inside one unit triangle)


def test_contours_through_vertices_have_no_duplicates_and_no_gaps():
    # Pyramid z = 10 - max(|x|, |y|) on the integer grid: every integer level passes exactly through vertices.
    x, y = np.meshgrid(np.arange(-10.0, 11.0), np.arange(-10.0, 11.0))
    x, y = x.ravel(), y.ravel()
    pts = np.column_stack([x, y, 10.0 - np.maximum(np.abs(x), np.abs(y))]) + UTM
    v, t, _ = build_dtm(pts)
    lines = contours(v, t, interval=1.0)
    # Level 0 is the lowest (the outline of the surface) and level 10 only touches the apex: no lines.
    assert [c["z"] for c in lines] == [float(k) for k in range(1, 10)]
    for c in lines:
        p = c["points"]
        side = 2 * (10 - c["z"])
        assert c["closed"]
        assert len(np.unique(p[:, :2], axis=0)) == len(p)
        q = np.vstack([p, p[:1]])[:, :2]
        length = np.hypot(*np.diff(q, axis=0).T).sum()
        assert length == pytest.approx(4 * side, rel=1e-12)  # a square of the known side
        assert np.maximum(np.abs(p[:, 0] - UTM[0]), np.abs(p[:, 1] - UTM[1])) == pytest.approx(side / 2, abs=1e-9)
    # Half levels: the same squares between the vertices.
    half = contours(v, t, interval=1.0, base=0.5)
    assert len(half) == 10 and all(c["closed"] for c in half)

    # A flat terrace exactly on a level is outlined once, on the side where the ground falls.
    z = np.where(np.maximum(np.abs(x), np.abs(y)) <= 4, 6.0, 10.0 - np.maximum(np.abs(x), np.abs(y)))
    v, t, _ = build_dtm(np.column_stack([x, y, z]))
    at6 = [c for c in contours(v, t, interval=1.0) if c["z"] == 6.0]
    assert len(at6) == 1 and at6[0]["closed"]
    q = np.vstack([at6[0]["points"], at6[0]["points"][:1]])[:, :2]
    assert np.hypot(*np.diff(q, axis=0).T).sum() == pytest.approx(32.0, rel=1e-12)


def test_contours_open_and_closed_together():
    # A dome cut by the edge of the data: inner levels close, outer levels end on the boundary.
    pts = cloud(20000, lambda x, y: 40 - np.hypot(x - 50, y - 50) * 0.5, seed=9)
    # max_edge removes the long thin triangles along the convex hull of the random cloud (they would
    # carry the lines along the sides of the square).
    v, t, report = build_dtm(pts, max_edge=4.0)
    assert report["dropped_long"] > 0 and report["area_plan"] > 9900
    lines = contours(v, t, interval=5.0)
    by_level = {}
    for c in lines:
        by_level.setdefault(c["z"], []).append(c)
    assert sorted(by_level) == [5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0]
    # Radius of level L is 2*(40-L): closed while it is well inside the square (r <= 40) ...
    for level in (20.0, 25.0, 30.0, 35.0):
        assert len(by_level[level]) == 1 and by_level[level][0]["closed"]
    # ... and four open arcs through the corners when it is cut by the sides (r = 60).
    assert len(by_level[10.0]) == 4 and not any(c["closed"] for c in by_level[10.0])
    for c in lines:
        r = np.hypot(c["points"][:, 0] - UTM[0] - 50, c["points"][:, 1] - UTM[1] - 50)
        # Discretisation: 0.7 m point spacing inside, triangles up to 4 m long at the hull → 0.3 m.
        assert np.abs(r - 2 * (40 - c["z"])).max() < 0.3


# ---------------------------------------------------------------- footprint and solids


def test_footprint_with_a_hole_and_under_solid():
    v, t, _ = build_dtm(grid(11, 6, lambda x, y: 10 + 0.1 * x))
    loops = surface_footprint(v, t)
    assert len(loops) == 1 and loops[0].shape[1] == 2
    q = loops[0] - UTM[:2]
    area = 0.5 * np.sum(q[:, 0] * np.roll(q[:, 1], -1) - np.roll(q[:, 0], -1) * q[:, 1])
    assert area == pytest.approx(5000.0, rel=1e-12)  # counter-clockwise outer loop

    # Remove the triangles around the centre: the hole comes second and runs clockwise.
    centre = v[t].mean(axis=1)[:, :2] - UTM[:2]
    holed = t[~((np.abs(centre[:, 0] - 50) < 10) & (np.abs(centre[:, 1] - 25) < 5))]
    loops = surface_footprint(v, holed)
    assert len(loops) == 2
    q = loops[1] - UTM[:2]
    assert 0.5 * np.sum(q[:, 0] * np.roll(q[:, 1], -1) - np.roll(q[:, 0], -1) * q[:, 1]) == pytest.approx(-200.0, rel=1e-12)

    # Solid under the plane z = 10 + 0.1 x down to z = 4: volume = 5000 * (15 - 4) = 55000.
    sv, st = under_solid(v, t, 4.0)
    stats = mesh_stats(sv, st)
    assert stats["closed"] and stats["orientation"] == "outward"
    assert stats["volume"] == pytest.approx(55000.0, abs=0.01)  # mesh_stats rounds to 0.01
    assert st.dtype == np.int32 and np.array_equal(sv[:len(v)], v)
    # With a hole: still closed (walls around the hole), volume without the column of the hole.
    sv, st = under_solid(v, holed, 4.0)
    stats = mesh_stats(sv, st)
    assert stats["closed"] and stats["orientation"] == "outward"
    assert stats["volume"] == pytest.approx(55000.0 - 200 * (15 - 4), abs=0.01)
    # The solid above the surface up to z = 30.
    sv, st = under_solid(v, t, 30.0)
    stats = mesh_stats(sv, st)
    assert stats["closed"] and stats["orientation"] == "outward" and stats["volume"] == pytest.approx(75000.0, abs=0.01)
    with pytest.raises(SurfaceError):
        under_solid(v, t, 12.0)  # the level crosses the surface


# ---------------------------------------------------------------- volumes


def test_volume_between_two_planes_over_a_rectangle():
    # top: z = 10 + 0.1 x; bottom: z = 12. Over 100 x 50 they cross at x = 20.
    top = build_dtm(grid(8, 5, lambda x, y: 10 + 0.1 * x))[:2]
    bottom = build_dtm(grid(4, 7, lambda x, y: 12 + 0 * x))[:2]
    r = volume_between(top, bottom)
    # cut = 50 * integral_20^100 (0.1x - 2) dx = 16000; fill = 50 * integral_0^20 (2 - 0.1x) dx = 1000. rel 1e-9.
    assert r["cut"] == pytest.approx(16000.0, rel=1e-9)
    assert r["fill"] == pytest.approx(1000.0, rel=1e-9)
    assert r["net"] == pytest.approx(15000.0, rel=1e-9) and r["area"] == pytest.approx(5000.0, rel=1e-9)
    assert r["tolerance"] < 1e-6 and "manifold3d" in r["method"]
    swapped = volume_between(bottom, top)
    assert swapped["cut"] == pytest.approx(1000.0, rel=1e-9) and swapped["fill"] == pytest.approx(16000.0, rel=1e-9)

    # Top everywhere above the bottom: fill is exactly nothing.
    low = build_dtm(grid(3, 3, lambda x, y: 4 + 0.02 * y))[:2]
    r = volume_between(top, low)
    assert r["cut"] == pytest.approx(5000 * (15 - 4.5), rel=1e-9) and r["fill"] == pytest.approx(0.0, abs=1e-6)

    # A limiting polygon: the triangle (10,10) (90,10) (10,40), area 1200.
    # integral of (0.1x - 2) over it = 0.1 * (x centroid 36.667) * 1200 - 2400 = 2000 = cut - fill.
    polygon = np.array([[10.0, 10], [90, 10], [10, 40]]) + UTM[:2]
    r = volume_between(top, bottom, polygon=polygon)
    assert r["area"] == pytest.approx(1200.0, rel=1e-9) and r["net"] == pytest.approx(2000.0, rel=1e-9)
    # fill: x from 10 to 20, height of the triangle there y = 30*(90-x)/80: integral (2-0.1x)*30*(90-x)/80 dx
    fill = 30 / 80 * ((2 * 90 * 10) - (2 + 9) * (20 ** 2 - 10 ** 2) / 2 + 0.1 * (20 ** 3 - 10 ** 3) / 3)
    assert r["fill"] == pytest.approx(fill, rel=1e-9) and r["cut"] == pytest.approx(2000.0 + fill, rel=1e-9)

    # Common region = intersection of the footprints (bottom shifted by 30 in X: overlap 70 x 50).
    moved = (bottom[0] + [30.0, 0, 0], bottom[1])
    r = volume_between(top, moved)
    assert r["area"] == pytest.approx(3500.0, rel=1e-9)
    assert r["net"] == pytest.approx(50 * (0.05 * (100 ** 2 - 30 ** 2) - 2 * 70), rel=1e-9)
    with pytest.raises(SurfaceError):  # no common region
        volume_between(top, (bottom[0] + [500.0, 0, 0], bottom[1]))


def test_volume_between_surfaces_that_coincide_outside_a_pit():
    # Original ground: plane z = 10. Excavated: the same plane with a pit, crest 20 x 20, floor 10 x 10 at z = 5.
    # The two surfaces coincide outside the pit and are triangulated differently.
    ground = build_dtm(grid(13, 9, lambda x, y: 10 + 0 * x, w=60, h=60))[:2]
    crest = np.array([[20.0, 20, 10], [40, 20, 10], [40, 40, 10], [20, 40, 10], [20, 20, 10]]) + UTM
    floor = np.array([[25.0, 25, 5], [35, 25, 5], [35, 35, 5], [25, 35, 5], [25, 25, 5]]) + UTM
    rim = grid(7, 7, lambda x, y: 10 + 0 * x, w=60, h=60)
    rim = rim[(np.abs(rim[:, 0] - UTM[0] - 30) > 12) | (np.abs(rim[:, 1] - UTM[1] - 30) > 12)]
    pit = build_dtm(rim, [crest, floor])[:2]
    r = volume_between(ground, pit)
    # Frustum of a pyramid: h/3 * (A1 + A2 + sqrt(A1*A2)) = 5/3 * (400 + 100 + 200). rel 1e-9.
    assert r["cut"] == pytest.approx(5 / 3 * 700, rel=1e-9)
    assert r["fill"] == pytest.approx(0.0, abs=1e-6) and r["area"] == pytest.approx(3600.0, rel=1e-9)
    same = volume_between(ground, ground)
    assert same["cut"] == pytest.approx(0.0, abs=1e-6) and same["fill"] == pytest.approx(0.0, abs=1e-6)


def test_volume_to_level_under_a_pyramid():
    # Pyramid: base 20 x 20 at z = 100 (UTM offsets in X, Y), apex 12 above the centre.
    v = np.array([[0.0, 0, 100], [20, 0, 100], [20, 20, 100], [0, 20, 100], [10, 10, 112]]) + UTM
    t = np.array([[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]])
    total = 400 * 12 / 3
    r = volume_to_level((v, t), 100.0)
    assert r["cut"] == pytest.approx(total, rel=1e-12) and r["fill"] == 0.0 and r["area"] == pytest.approx(400, rel=1e-12)
    # Level at half height: the cap above is a pyramid 10 x 10 x 6; net = total - 6 * 400.
    r = volume_to_level((v, t), 106.0)
    assert r["cut"] == pytest.approx(100 * 6 / 3, rel=1e-12)
    assert r["fill"] == pytest.approx(100 * 6 / 3 - total + 6 * 400, rel=1e-12)
    assert r["net"] == pytest.approx(total - 6 * 400, rel=1e-12)
    r = volume_to_level((v, t), 120.0)
    assert r["cut"] == 0.0 and r["fill"] == pytest.approx(20 * 400 - total, rel=1e-12)
    # Triangles given clockwise (normals down) describe the same surface.
    assert volume_to_level((v, t[:, ::-1]), 106.0)["cut"] == pytest.approx(200.0, rel=1e-12)
    # Limited by a polygon (the quarter x < 10, y < 10): a quarter of everything. Booleans: rel 1e-9.
    quarter = np.array([[0.0, 0], [10, 0], [10, 10], [0, 10]]) + UTM[:2]
    r = volume_to_level((v, t), 106.0, polygon=quarter)
    assert r["area"] == pytest.approx(100.0, rel=1e-9)
    assert r["cut"] == pytest.approx(50.0, rel=1e-9) and r["fill"] == pytest.approx((200 - total + 2400) / 4, rel=1e-9)


def test_volume_to_level_paraboloid_tin_matches_booleans():
    # Paraboloid TIN: the prismatic formula and the boolean method are both exact for the TIN → equal (rel 1e-9).
    pts = cloud(3000, lambda x, y: 30 - ((x - 50) ** 2 + (y - 50) ** 2) / 100, seed=10)
    v, t, report = build_dtm(pts)
    exact = volume_to_level((v, t), 12.0)
    hull = np.array(surface_footprint(v, t)[0])
    by_booleans = volume_to_level((v, t), 12.0, polygon=hull)
    for key in ("cut", "fill", "net", "area"):
        assert by_booleans[key] == pytest.approx(exact[key], rel=1e-9)
    assert exact["area"] == pytest.approx(report["area_plan"], rel=1e-12)
    assert exact["cut"] > 1000 and exact["fill"] > 1000
    # Against the analytic paraboloid: the cap above 12 has radius sqrt(1800) and volume pi*R^2*h/2 = pi*1800*18/2;
    # the TIN of 3000 random points lies below the convex-up surface by a few percent at most.
    assert exact["cut"] == pytest.approx(np.pi * 1800 * 18 / 2, rel=0.02)


def test_volume_refuses_surfaces_not_single_valued_in_z():
    v = np.array([[0.0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [10, 0, 5], [10, 10, 5]])
    wall = np.array([[0, 1, 2], [0, 2, 3], [1, 4, 5], [1, 5, 2]])  # a vertical wall on one side
    with pytest.raises(SurfaceError) as err:
        volume_to_level((v, wall), -1.0)
    assert "вертикальных треугольников: 2" in str(err.value)
    v2 = np.array([[0.0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [4, 0, 5], [4, 10, 5]])
    overhang = np.array([[0, 1, 2], [0, 2, 3], [1, 4, 5], [1, 5, 2]])  # folded back over itself
    with pytest.raises(SurfaceError) as err:
        volume_between((v2, overhang), (v, wall[:2]))
    assert "перевёрнутых" in str(err.value) and "Верхняя" in str(err.value)
    with pytest.raises(SurfaceError):
        under_solid(v2, overhang, -5.0)


# ---------------------------------------------------------------- speed


def test_speed_dtm_and_contours_one_million_points(capsys):
    rng = np.random.default_rng(11)
    xy = rng.random((1_000_000, 2)) * 10_000
    pts = np.column_stack([xy, 50 * np.sin(xy[:, 0] / 700) * np.cos(xy[:, 1] / 900)]) + UTM
    build_dtm(pts[:1000])  # compile the numba kernels outside the timing
    contours(*build_dtm(pts[:1000])[:2], interval=1.0)

    start = time.perf_counter()
    v, t, report = build_dtm(pts)
    dtm_seconds = time.perf_counter() - start
    assert np.array_equal(v, pts) and report["triangles"] > 1_990_000

    start = time.perf_counter()
    lines = contours(v, t, interval=1.0)  # about 100 levels
    contour_seconds = time.perf_counter() - start
    assert 95 <= len({c["z"] for c in lines}) <= 101

    start = time.perf_counter()
    level = volume_to_level((v, t), 0.0)
    level_seconds = time.perf_counter() - start
    assert level["area"] == pytest.approx(report["area_plan"], rel=1e-12)

    with capsys.disabled():
        print(f"\n[speed] DTM 1M points: {dtm_seconds:.2f} s ({report['triangles']} triangles); "
              f"contours x{len({c['z'] for c in lines})} levels: {contour_seconds:.2f} s "
              f"({len(lines)} lines, {sum(len(c['points']) for c in lines)} points); "
              f"volume to level: {level_seconds:.2f} s")
    # Generous limits (the measured values are printed above).
    assert dtm_seconds < 120 and contour_seconds < 60 and level_seconds < 60


def test_speed_dtm_with_breaklines_and_volume_between(capsys):
    rng = np.random.default_rng(12)
    xy = rng.random((200_000, 2)) * 2000
    pts = np.column_stack([xy, 20 * np.sin(xy[:, 0] / 300) + 100]) + UTM
    # 50 breaklines of 400 segments each, crossing one another at equal Z (Z is linear in X, Y).
    lines = []
    for k in range(50):
        s = np.linspace(0, 1, 401)
        x = 50 + 1900 * s if k % 2 else 100 + 36 * k + 0 * s
        y = 100 + 36 * k + 0 * s if k % 2 else 50 + 1900 * s
        lines.append(np.column_stack([x, y, 100 + 0.01 * x + 0.02 * y]) + UTM)
    start = time.perf_counter()
    v, t, report = build_dtm(pts, lines)
    dtm_seconds = time.perf_counter() - start
    assert report["breakline_segments"] == 20000 and report["crossings_added"] > 0
    assert_breakline_is_edges(v, t, lines[0][:5])

    other = build_dtm(np.column_stack([xy[:150_000], 15 * np.cos(xy[:150_000, 1] / 250) + 100]) + UTM)[:2]
    start = time.perf_counter()
    r = volume_between((v, t), other)
    volume_seconds = time.perf_counter() - start
    assert r["cut"] > 0 and r["fill"] > 0 and r["area"] > 3.9e6
    with capsys.disabled():
        print(f"\n[speed] DTM 200k points + 20000 breakline segments: {dtm_seconds:.2f} s; "
              f"volume between {len(t)} and {len(other[1])} triangles: {volume_seconds:.2f} s")
    assert dtm_seconds < 120 and volume_seconds < 300
