"""«Построить ЦТМ» (ТЗ 7.20, 7.23; ADR 0011 §3 `model.dtm`): the surface passes through the source points
exactly (tolerance 0, bit for bit); triangle count, plan and true area of a plane against the analytic answer
(1e-12); the triangulation is the Delaunay one of scipy; breaklines are edges; crossings, duplicates, the
boundary polygon, `max_edge`; hard data (UTM offsets, a grid of cocircular points, a narrow strip, empty
heights); errors are {code, message, hint} in Russian. Ported from the tests of BF `test_surfaces.py`.
The job `model.dtm` builds the DTM of a project from layers of points and lines and collars."""

from __future__ import annotations

import time
from itertools import pairwise
from typing import Any

import numpy as np
import pytest
from scipy.spatial import ConvexHull, Delaunay

from bf_core.geo.dtm import DtmError, build_dtm

pytestmark = pytest.mark.windows

UTM = np.array([512345.678, 6789012.345, 0.0])


def plan_areas(v: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Signed plan areas of triangles (coordinates centred first)."""
    p = v[:, :2] - np.floor(v[:, :2].mean(axis=0))
    a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
    return np.asarray(0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])))


def edge_set(t: np.ndarray) -> set[tuple[int, int]]:
    e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    return {(min(a, b), max(a, b)) for a, b in e.tolist()}


def vertex_index(v: np.ndarray, p: Any) -> int:
    """Index of the vertex with exactly these X, Y."""
    hit = np.nonzero((v[:, 0] == p[0]) & (v[:, 1] == p[1]))[0]
    assert len(hit) == 1, f"vertex {p} not found exactly once"
    return int(hit[0])


def assert_breakline_is_edges(v: np.ndarray, t: np.ndarray, line: np.ndarray, tol: float = 1e-7) -> None:
    """Every segment of the line is an edge or a chain of collinear edges; no edge crosses it."""
    edges = edge_set(t)
    ea = np.array(sorted(edges))
    o = np.floor(v[:, :2].mean(axis=0))
    p = v[:, :2] - o
    for a3, b3 in pairwise(line):
        a, b = a3[:2] - o, b3[:2] - o
        d = b - a
        length = np.hypot(*d)
        s = ((p - a) @ d) / length**2
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


def cloud(n: int, f: Any, seed: int = 0, size: float = 100.0, offset: np.ndarray = UTM) -> np.ndarray:
    rng = np.random.default_rng(seed)
    xy = rng.random((n, 2)) * size
    return np.asarray(np.column_stack([xy, f(xy[:, 0], xy[:, 1])]) + offset)


def grid(nx: int, ny: int, f: Any, w: float = 100.0, h: float = 50.0, offset: Any = UTM) -> np.ndarray:
    x, y = np.meshgrid(np.linspace(0, w, nx), np.linspace(0, h, ny))
    x, y = x.ravel(), y.ravel()
    return np.asarray(np.column_stack([x, y, f(x, y)]) + offset)


def flat(level: float) -> Any:
    return lambda x, y: level + 0 * x


def refused(*args: Any, **kw: Any) -> DtmError:
    with pytest.raises(DtmError) as err:
        build_dtm(*args, **kw)
    assert err.value.code and err.value.message and err.value.hint
    return err.value


# --- the surface passes through the points ---------------------------------------------------------------


def test_plane_nodes_exact_count_and_area() -> None:
    def plane(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return 100.0 + 0.3 * x - 0.2 * y

    pts = cloud(3000, plane, seed=1)
    seen: list[float] = []
    v, t, report = build_dtm(pts, progress=seen.append)
    # Deviation in the nodes is exactly zero: the vertices are the input points, bit for bit.
    assert v.tobytes() == pts.tobytes()
    assert t.dtype == np.int32 and v.dtype == np.float64
    # Points in general position: triangles = 2n - 2 - h (h = points on the convex hull).
    hull = ConvexHull(pts[:, :2] - UTM[:2])
    assert len(t) == 2 * len(pts) - 2 - len(hull.vertices) == report["triangles"]
    assert (plan_areas(v, t) > 0).all()  # oriented upward
    # Plan area = area of the convex hull; true area = plan area * sqrt(1 + a^2 + b^2). rel 1e-12.
    assert report["area_plan"] == pytest.approx(hull.volume, rel=1e-12)
    assert report["area"] == pytest.approx(hull.volume * np.sqrt(1 + 0.3**2 + 0.2**2), rel=1e-12)
    assert report["points"] == 3000 and report["merged_duplicates"] == 0 and report["breakline_segments"] == 0
    assert "CDT" in report["method"] and report["tolerance"] == pytest.approx(1e-9 * 100, rel=1e-3)
    assert seen[0] == 0.0 and seen[-1] == 1.0 and seen == sorted(seen)


def test_plane_patch_on_grid_is_analytic() -> None:
    # a grid: every cell has four cocircular points — the hard case for a Delaunay triangulation
    pts = grid(21, 11, lambda x, y: 5 + 0.75 * x)
    v, t, report = build_dtm(pts)
    assert v.tobytes() == pts.tobytes() and len(t) == 2 * 20 * 10
    assert report["area_plan"] == pytest.approx(100 * 50, rel=1e-13)
    assert report["area"] == pytest.approx(100 * 50 * 1.25, rel=1e-13)  # sqrt(1 + 0.75^2) = 1.25
    assert (plan_areas(v, t) > 0).all()


def test_analytic_surface_is_delaunay() -> None:
    pts = cloud(2000, lambda x, y: 20 * np.sin(x / 15) * np.cos(y / 20), seed=2)
    v, t, _ = build_dtm(pts)
    assert v.tobytes() == pts.tobytes()
    assert (plan_areas(v, t) > 0).all()
    reference = Delaunay(pts[:, :2] - np.floor(UTM[:2]))
    assert {tuple(sorted(s)) for s in t.tolist()} == {tuple(sorted(s)) for s in reference.simplices.tolist()}


def test_hard_data_far_origin_strip_and_scale() -> None:
    # coordinates of a 6-degree zone with a false northing: 9 significant digits before the metres
    far = np.array([13_512_345.678_901, 6_789_012.345_678, -1234.567_891])
    pts = cloud(1500, lambda x, y: 0.001 * x * y, seed=11, size=30.0, offset=far)
    v, t, report = build_dtm(pts)
    assert v.tobytes() == pts.tobytes() and (plan_areas(v, t) > 0).all()
    reference = Delaunay(pts[:, :2] - np.floor(far[:2]))
    assert {tuple(sorted(s)) for s in t.tolist()} == {tuple(sorted(s)) for s in reference.simplices.tolist()}
    # a strip 40 km long and 2 m wide (a road survey): extent tolerance 4e-5 m, points 1 mm apart stay apart
    rng = np.random.default_rng(12)
    strip = np.column_stack([rng.random(4000) * 40_000, rng.random(4000) * 2, rng.random(4000)]) + UTM
    strip[1] = strip[0] + [0.001, 0.0, 0.5]  # 1 mm in plan, another height: two points, not a conflict
    v, t, report = build_dtm(strip)
    assert v.tobytes() == strip.tobytes() and report["merged_duplicates"] == 0
    hull = ConvexHull(strip[:, :2] - UTM[:2])
    assert report["area_plan"] == pytest.approx(hull.volume, rel=1e-9) and (plan_areas(v, t) > 0).all()
    # millimetre data (a core tray scan): the tolerance follows the extent
    tiny = cloud(500, lambda x, y: x + y, seed=13, size=0.05)
    v, t, report = build_dtm(tiny)
    assert v.tobytes() == tiny.tobytes() and len(t) == 2 * 500 - 2 - len(ConvexHull(tiny[:, :2] - UTM[:2]).vertices)


# --- breaklines ------------------------------------------------------------------------------------------


def test_breakline_diagonal_ridge() -> None:
    pts = cloud(1500, flat(10), seed=3)
    # A ridge across the cloud; its vertices are not among the points.
    ridge = np.array([[3.0, 4.0, 25.0], [31.0, 28.5, 27.0], [62.0, 70.0, 26.0], [97.0, 95.0, 24.0]]) + UTM
    v, t, report = build_dtm(pts, [ridge])
    assert report["breakline_segments"] == 3 and report["points"] == 1504
    assert v[:1500].tobytes() == pts.tobytes() and v[1500:].tobytes() == ridge.tobytes()
    assert_breakline_is_edges(v, t, ridge)
    assert (plan_areas(v, t) > 0).all()
    # Without the breakline some edge does cross the ridge (the check above is not vacuous).
    v0, t0, _ = build_dtm(np.concatenate([pts, ridge]))
    with pytest.raises(AssertionError):
        assert_breakline_is_edges(v0, t0, ridge)


def test_closed_breakline_pit() -> None:
    pts = grid(26, 26, flat(10), w=50, h=50)
    crest = np.array([[11.3, 12.1, 10], [38.7, 12.1, 10], [38.7, 37.4, 10], [11.3, 37.4, 10], [11.3, 12.1, 10.0]]) + UTM
    v, t, report = build_dtm(pts, [crest])
    assert report["breakline_segments"] == 4
    assert report["merged_duplicates"] == 1  # the repeated closing vertex
    assert_breakline_is_edges(v, t, crest)
    assert report["area_plan"] == pytest.approx(2500, rel=1e-12)


def test_breaklines_crossing_at_equal_z() -> None:
    pts = cloud(800, flat(5), seed=4)
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


def test_breaklines_crossing_at_different_z_is_an_error() -> None:
    pts = cloud(200, flat(5), seed=5)
    one = np.array([[10.0, 10.0, 20.0], [90.0, 90.0, 40.0]]) + UTM
    two = np.array([[10.0, 90.0, 50.0], [90.0, 10.0, 50.0]]) + UTM
    e = refused(pts, [one, two])
    assert e.code == "breakline_z_conflict"
    assert "1 шт" in e.message and "512395,678" in e.message and "30,000" in e.message and "50,000" in e.message
    assert "не выбирается автоматически" in e.hint


def test_lines_only_and_contours_as_breaklines() -> None:
    # closed contour lines of a cone (no points at all): every contour is a chain of edges at its level
    lines = []
    for r, z in ((40.0, 100.0), (30.0, 110.0), (20.0, 120.0), (10.0, 130.0)):
        a = np.linspace(0, 2 * np.pi, 49)
        ring = np.column_stack([50 + r * np.cos(a), 50 + r * np.sin(a), np.full(49, z)]) + UTM
        ring[-1] = ring[0]  # a closed line repeats its first vertex
        lines.append(ring)
    v, t, report = build_dtm(None, lines)
    assert report["breakline_segments"] == 4 * 48 and report["merged_duplicates"] == 4 and report["points"] == 4 * 48
    for ring in lines:
        assert_breakline_is_edges(v, t, ring)
    assert set(np.unique(v[:, 2]).tolist()) == {100.0, 110.0, 120.0, 130.0}  # the heights of the contours only


# --- duplicates and bad input ----------------------------------------------------------------------------


def test_duplicates() -> None:
    pts = cloud(500, lambda x, y: 0.1 * x + 7, seed=6)
    # Identical duplicates merge silently; the first of them is kept.
    v, _, report = build_dtm(np.concatenate([pts, pts[:40]]))
    assert report["merged_duplicates"] == 40 and v.tobytes() == pts.tobytes()
    # Within the tolerance (1e-9 of the extent) in plan — the same.
    near = pts[:10].copy()
    near[:, 0] += 3e-8
    v, _, report = build_dtm(np.concatenate([pts, near]))
    assert report["merged_duplicates"] == 10 and v.tobytes() == pts.tobytes()
    # Different Z: an error with the number of such places and examples, never a silent choice.
    clash = pts[:7].copy()
    clash[:, 2] += 1.5
    e = refused(np.concatenate([pts, clash]))
    assert e.code == "z_conflict" and "7 шт" in e.message and e.message.count("X=") == 5

    def ru(value: float) -> str:
        return f"{value:.3f}".replace(".", ",")

    assert f"X={ru(pts[0, 0])} Y={ru(pts[0, 1])}" in e.message
    assert f"{ru(pts[0, 2])}; {ru(pts[0, 2] + 1.5)}" in e.message


def test_bad_input_is_refused() -> None:
    assert refused(None).code == "no_data"
    assert refused(np.zeros((0, 3)), []).code == "no_data"
    assert refused(np.array([[0.0, 0, 0], [1, 1, 1], [2, 2, 2]])).code == "too_few_points"  # collinear
    assert refused(np.array([[5.0, 5, 1], [5, 5, 1], [5, 5, 1]])).code == "too_few_points"  # one place
    # an empty height is not a zero: the point is not silently put at sea level
    e = refused(np.array([[0.0, 0, 0], [1, 0, np.nan], [0, 1, 0], [1, 1, np.inf]]))
    assert e.code == "bad_coordinates" and "2 точек" in e.message
    assert refused(np.zeros((4, 2))).code == "bad_input"
    pts = cloud(50, flat(1), seed=9)
    assert refused(pts, max_edge=0).code == "bad_max_edge"
    assert refused(pts, max_edge=float("nan")).code == "bad_max_edge"
    assert refused(pts, boundary=np.array([[0.0, 0], [1, 1]])).code == "bad_boundary"
    assert refused(pts, boundary=np.array([[0.0, 0], [1, np.nan], [2, 2]])).code == "bad_coordinates"


# --- boundary and max_edge -------------------------------------------------------------------------------


def test_boundary_concave_polygon() -> None:
    def plane(x: Any, y: Any) -> Any:
        return 50 + 0.1 * x + 0.2 * y

    pts = cloud(4000, plane, seed=7)
    ring = np.array([[10.0, 10], [90, 10], [90, 90], [50, 45], [10, 90]]) + UTM[:2]
    v, t, report = build_dtm(pts, boundary=ring)
    # The surface fills the polygon exactly: area 80*80 - (80*45/2) = 4600, rel 1e-12.
    assert report["area_plan"] == pytest.approx(4600.0, rel=1e-12)
    assert plan_areas(v, t).sum() == pytest.approx(4600.0, rel=1e-12)
    # The polygon's vertices are in the surface; their Z is interpolated from the surface (a plane: abs 1e-9).
    for corner in ring:
        i = vertex_index(v, corner)
        assert v[i, 2] == pytest.approx(plane(*(corner - UTM[:2])), abs=1e-9)
    closed = np.column_stack([np.vstack([ring, ring[:1]]), np.zeros(6)])
    assert_breakline_is_edges(v, t, closed)
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
    data = grid(11, 11, flat(3), w=100, h=100, offset=0)
    wide = np.array([[-20.0, -20], [120, -20], [120, 120], [-20, 120]])
    v, t, report = build_dtm(data, boundary=wide)
    assert report["area_plan"] == pytest.approx(140 * 140, rel=1e-12) and report["boundary_extrapolated"] == 4
    assert (v[:, 2] == 3.0).all()
    assert build_dtm(data, boundary=square)[2]["boundary_extrapolated"] == 0

    e = refused(pts, boundary=np.array([[10.0, 10], [90, 90], [90, 10], [10, 90]]) + UTM[:2])
    assert e.code == "bad_boundary" and "самопересекается" in e.message
    away = np.array([[500.0, 500], [600, 500], [600, 600], [500, 600]]) + UTM[:2]
    assert refused(pts, boundary=away).code == "boundary_outside"


def test_boundary_crossing_a_breakline() -> None:
    pts = cloud(1000, flat(5), seed=8)
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


def test_max_edge_peels_from_outside_only() -> None:
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
    assert report["dropped_long"] > 0 and v.tobytes() == pts.tobytes()
    edges = np.concatenate([v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 1]], v[t[:, 0]] - v[t[:, 2]]])
    assert np.hypot(edges[:, 0], edges[:, 1]).max() < 1.5

    # A gap enclosed by short triangles is not opened: long triangles go only from the outside inward.
    keep = ~((x > 6) & (x < 14) & (y > 6) & (y < 14))
    ring_pts = np.column_stack([x[keep], y[keep], 0 * x[keep]]) + UTM
    v, t, report = build_dtm(ring_pts, max_edge=1.5)
    assert report["dropped_long"] == 0 and report["area_plan"] == pytest.approx(400.0, rel=1e-12)

    e = refused(ring_pts, max_edge=0.5)  # nothing left
    assert e.code == "all_long" and "0,5 м" in e.message


def test_cancel_from_the_progress_callback() -> None:
    class Stop(Exception):
        pass

    def cancel(value: float) -> None:
        if value >= 0.2:
            raise Stop

    with pytest.raises(Stop):
        build_dtm(cloud(2000, flat(1), seed=10), progress=cancel)


# --- speed -----------------------------------------------------------------------------------------------


@pytest.mark.slow
def test_speed_one_million_points(capsys: pytest.CaptureFixture[str]) -> None:
    rng = np.random.default_rng(11)
    xy = rng.random((1_000_000, 2)) * 10_000
    pts = np.column_stack([xy, 50 * np.sin(xy[:, 0] / 700) * np.cos(xy[:, 1] / 900)]) + UTM
    build_dtm(pts[:1000])  # compile the numba kernels outside the timing
    start = time.perf_counter()
    v, t, report = build_dtm(pts)
    seconds = time.perf_counter() - start
    assert v.tobytes() == pts.tobytes() and report["triangles"] > 1_990_000
    with capsys.disabled():
        print(f"\n[speed] DTM 1M points: {seconds:.2f} s ({report['triangles']} triangles)")
    assert seconds < 30  # ADR 0011 «Замеры»: the first measure sets the budget (about 3 s here)
