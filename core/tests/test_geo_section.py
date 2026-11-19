"""Exact sections of wireframes by a plane (ТЗ 7.14, 7.21; ADR 0013 §4 `section.py`; BF `test_wireframes_stage10`):
a cube gives one closed square (perimeter to 1e-8 m on UTM coordinates, points on the plane to 2e-9 m); a plane
through vertices gives the same loop without double points; an oblique cut against the polygon of the cube
edges; open surfaces, several bodies, non-manifold and degenerate triangles; the parts of the store and
read-only memory maps change nothing; the window of a section line (ends, faces) and clipping."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.geo import section as gs
from tests.meshutil import UTM, cube, grid, stored

pytestmark = pytest.mark.windows

ON_PLANE = 2e-9  # a point of a section from its plane, metres (one rounding of a UTM northing is 9.3e-10)
LENGTH = 1e-8  # a perimeter, metres


def hills(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return 20.0 * np.sin(x / 17.0) * np.cos(y / 23.0)


def perimeter(points: np.ndarray, closed: bool) -> float:
    p = np.vstack([points, points[:1]]) if closed else points
    return float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum())


def only(s: gs.Section) -> tuple[np.ndarray, bool]:
    assert len(s) == 1
    return s.line(0), bool(s.closed[0])


def distinct(points: np.ndarray, closed: bool) -> bool:
    p = np.vstack([points, points[:1]]) if closed else points
    return bool((np.abs(np.diff(p, axis=0)).max(axis=1) > 0).all())


def cube_polygon(lo: np.ndarray, side: float, point: np.ndarray, normal: np.ndarray) -> tuple[float, float]:
    """(perimeter, area) of the cut of a cube by a plane, from its 12 edges (the reference)."""
    n = normal / np.linalg.norm(normal)
    corners = np.array([[i, j, k] for i in (0, 1) for j in (0, 1) for k in (0, 1)], dtype=float) * side
    pts = []
    for a in range(8):
        for b in range(a + 1, 8):
            if np.abs(corners[a] - corners[b]).sum() != side:
                continue
            da, db = (corners[a] + lo - point) @ n, (corners[b] + lo - point) @ n
            if (da >= 0) != (db >= 0):
                pts.append(corners[a] + (corners[b] - corners[a]) * (da / (da - db)))
    p = np.array(pts)
    c = p.mean(axis=0)
    u = np.cross(n, [1.0, 0.0, 0.0] if abs(n[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    w = np.cross(n, u)
    p = p[np.argsort(np.arctan2((p - c) @ w, (p - c) @ u))]
    q = np.vstack([p, p[:1]])
    area = 0.5 * abs(sum(np.cross(q[i] - c, q[i + 1] - c) @ n for i in range(len(p))))
    return float(np.linalg.norm(np.diff(q, axis=0), axis=1).sum()), float(area)


def polygon_area(points: np.ndarray, normal: np.ndarray) -> float:
    n = normal / np.linalg.norm(normal)
    c = points.mean(axis=0)
    q = np.vstack([points, points[:1]]) - c
    return 0.5 * abs(sum(np.cross(q[i], q[i + 1]) @ n for i in range(len(points))))


# --- the cube ----------------------------------------------------------------------------------------------


def test_section_of_a_cube_is_one_exact_closed_square() -> None:
    v, t = cube(10, 100.0)
    x = UTM[0] + 37.25
    pts, closed = only(gs.plane_section(v, t, [x, 0.0, 0.0], [1.0, 0.0, 0.0]))
    assert closed and pts.dtype == np.float64
    assert np.abs(pts[:, 0] - x).max() <= ON_PLANE
    assert perimeter(pts, True) == pytest.approx(400.0, abs=LENGTH)
    assert len(pts) == 80 and distinct(pts, True)  # 4 faces × (10 grid lines + 10 diagonals)
    lo, hi = v.min(axis=0), v.max(axis=0)
    on_face = (np.abs(pts[:, 1:] - lo[1:]) <= ON_PLANE) | (np.abs(pts[:, 1:] - hi[1:]) <= ON_PLANE)
    assert on_face.any(axis=1).all()  # every point is on the surface of the cube
    assert len(gs.plane_section(v, t, [lo[0] - 1.0, 0.0, 0.0], [1.0, 0.0, 0.0])) == 0  # beside the cube


def test_plane_through_vertices_gives_the_same_loop_without_double_points() -> None:
    v, t = cube(10, 100.0)
    x = float(np.unique(v[:, 0])[3])  # a grid line of the cube, exactly
    pts, closed = only(gs.plane_section(v, t, [x, UTM[1], UTM[2]], [1.0, 0.0, 0.0]))
    assert closed and (pts[:, 0] == x).all()
    assert len(pts) == 40 and distinct(pts, True)
    assert perimeter(pts, True) == pytest.approx(400.0, abs=LENGTH)
    assert set(map(bytes, pts)) <= set(map(bytes, np.ascontiguousarray(v)))  # the vertices themselves, bit for bit


def test_a_vertex_on_the_plane_is_on_its_positive_side() -> None:
    v, t = cube(4, 100.0)
    lo, hi = v.min(axis=0), v.max(axis=0)
    # the plane of a face: the outline of the face when the cube is behind the plane, nothing when in front
    assert len(gs.plane_section(v, t, lo, [1.0, 0.0, 0.0])) == 0
    assert len(gs.plane_section(v, t, hi, [-1.0, 0.0, 0.0])) == 0
    for point, normal in ((hi, [1.0, 0.0, 0.0]), (lo, [-1.0, 0.0, 0.0])):
        pts, closed = only(gs.plane_section(v, t, point, normal))
        assert closed and (pts[:, 0] == point[0]).all() and len(pts) == 16
        assert perimeter(pts, True) == pytest.approx(400.0, abs=LENGTH)


@pytest.mark.parametrize(
    ("shift", "normal"),
    [
        ((3.1, -2.7, 1.3), (0.3, -0.5, 0.81)),
        ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),  # the regular hexagon through the centre
        ((-17.0, 21.5, 30.0), (0.02, 0.01, 1.0)),  # nearly level
        ((40.0, 0.0, 0.0), (1.0, 1e-7, 0.0)),  # a hair off a grid direction
    ],
)
def test_oblique_cut_against_the_polygon_of_the_cube_edges(shift, normal) -> None:  # type: ignore[no-untyped-def]
    v, t = cube(12, 100.0)
    lo = v.min(axis=0)
    point = lo + 50.0 + np.array(shift)
    n = np.array(normal, dtype=float)
    pts, closed = only(gs.plane_section(v, t, point, n))
    assert closed and distinct(pts, True)
    assert np.abs((pts - point) @ (n / np.linalg.norm(n))).max() <= ON_PLANE
    ref_perimeter, ref_area = cube_polygon(lo, 100.0, point, n)
    assert perimeter(pts, True) == pytest.approx(ref_perimeter, abs=LENGTH)
    assert polygon_area(pts, n) == pytest.approx(ref_area, abs=1e-6)


def test_plane_along_the_vertical_edges_of_the_cube_still_closes() -> None:
    """x + y = const through two opposite vertical edges: on UTM coordinates the vertices of those edges fall on
    either side of the plane by rounding. The lines stay closed and cover the diagonal rectangle."""
    v, t = cube(10, 100.0)
    lo = v.min(axis=0)
    s = gs.plane_section(v, t, lo + [0.0, 100.0, 0.0], [1.0, 1.0, 0.0])
    assert len(s) >= 1 and s.closed.all()
    total = sum(perimeter(s.line(i), True) for i in range(len(s)))
    assert total == pytest.approx(200.0 + 200.0 * math.sqrt(2.0), abs=1e-6)


# --- other meshes ------------------------------------------------------------------------------------------


def test_open_surface_gives_an_open_line_from_border_to_border() -> None:
    v, t = grid(40, 30, hills)
    x = UTM[0] + 12.3
    pts, closed = only(gs.plane_section(v, t, [x, 0.0, 0.0], [1.0, 0.0, 0.0]))
    assert not closed and len(pts) == 61 and distinct(pts, False)  # 31 grid lines + 30 diagonals
    pts = pts[np.argsort(pts[:, 1])]
    assert pts[0, 1] == UTM[1] and pts[-1, 1] == UTM[1] + 30.0  # the ends are on the border of the surface
    assert np.abs(pts[:, 0] - x).max() <= ON_PLANE
    on_lines = pts[::2]  # the points on the grid lines y = j: between the vertices (12, j) and (13, j)
    j = np.arange(31.0)
    z = hills(np.full(31, 12.0), j) + (hills(np.full(31, 13.0), j) - hills(np.full(31, 12.0), j)) * (x - UTM[0] - 12.0)
    assert np.abs(on_lines[:, 2] - (z + UTM[2])).max() <= 1e-8


def test_two_bodies_give_two_loops() -> None:
    v1, t1 = cube(6, 60.0)
    v2, t2 = cube(5, 40.0, offset=UTM + [200.0, 10.0, 5.0])
    v, t = np.vstack([v1, v2]), np.vstack([t1, t2 + len(v1)]).astype(np.int32)
    s = gs.plane_section(v, t, UTM + [0.0, 30.0, 0.0], [0.0, 1.0, 0.0])
    assert len(s) == 2 and s.closed.all()
    assert sorted(np.round(s.lengths(), 7).tolist()) == [160.0, 240.0]
    assert s.offsets.tolist() == [0, len(s.line(0)), len(s.xyz)]
    one = gs.plane_section(v, t, UTM + [0.0, 55.0, 0.0], [0.0, 1.0, 0.0])
    assert len(one) == 1 and one.lengths()[0] == pytest.approx(240.0, abs=LENGTH)


def test_non_manifold_edge_and_degenerate_triangles() -> None:
    # three triangles around one edge (a fin): the plane z = 0.5 cuts the edge and gives three segments
    v = np.array([[0, 0, 0], [0, 0, 1], [1, 0, 0], [-1, 1, 0], [-1, -1, 0], [5, 5, 5]], dtype=float) + UTM
    t = np.array([[0, 1, 2], [0, 1, 3], [0, 1, 4], [0, 0, 1], [1, 5, 5], [2, 3, 4]], dtype=np.int32)
    s = gs.plane_section(v, t, UTM + [0.0, 0.0, 0.5], [0.0, 0.0, 1.0])
    assert not s.closed.any()
    segments = sum(len(s.line(i)) - 1 for i in range(len(s)))
    assert segments == 3  # every cut triangle once; the triangles with a repeated vertex give nothing
    assert s.lengths().sum() == pytest.approx(0.5 + 2.0 * math.hypot(0.5, 0.5), abs=LENGTH)


def test_parts_of_the_store_and_memory_maps_change_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    v, t, parts = stored(*grid(120, 90, hills), part_triangles=200)
    assert len(parts) > 100
    point, normal = UTM + [61.7, 40.2, 0.0], [0.6, -0.8, 0.05]
    whole = gs.plane_section(v, t, point, normal)
    assert len(whole) >= 1
    cut = gs.plane_section(v, t, point, normal, parts=parts)
    assert cut.xyz.tobytes() == whole.xyz.tobytes() and cut.offsets.tolist() == whole.offsets.tolist()
    assert len(gs.parts_cut(parts, point, normal)) < len(parts) // 2  # only the parts the plane touches are read
    v.tofile(tmp_path / "xyz.bin")
    t.tofile(tmp_path / "tri.bin")
    mv = np.memmap(tmp_path / "xyz.bin", dtype="<f8", mode="r").reshape(-1, 3)
    mt = np.memmap(tmp_path / "tri.bin", dtype="<i4", mode="r").reshape(-1, 3)
    mapped = gs.plane_section(mv, mt, point, normal, parts=parts)
    assert mapped.xyz.tobytes() == whole.xyz.tobytes()
    as_int64 = gs.plane_section(v.tolist(), t.astype(np.int64), point, normal)
    assert as_int64.xyz.tobytes() == whole.xyz.tobytes()


def test_empty_and_bad_input() -> None:
    v, t = cube(2, 10.0)
    empty = gs.plane_section(v, t, UTM + [500.0, 0.0, 0.0], [1.0, 0.0, 0.0])
    assert len(empty) == 0 and empty.xyz.shape == (0, 3) and empty.offsets.tolist() == [0] and len(empty.closed) == 0
    none = gs.plane_section(v, np.zeros((0, 3), dtype=np.int32), UTM, [1.0, 0.0, 0.0])
    assert len(none) == 0
    for point, normal in ((UTM, [0.0, 0.0, 0.0]), (UTM, [math.nan, 0.0, 1.0]), ([math.inf, 0.0, 0.0], [1.0, 0.0, 0.0])):
        with pytest.raises(ValueError):
            gs.plane_section(v, t, point, normal)
    with pytest.raises(ValueError):
        gs.plane_section(v, t[:, :2], UTM, [1.0, 0.0, 0.0])
    bad = t.copy()
    bad[3, 1] = len(v)  # a vertex that does not exist
    with pytest.raises(ValueError):
        gs.plane_section(v, bad, UTM + 5.0, [1.0, 0.0, 0.0])
    with pytest.raises(ValueError):
        gs.plane_section(v, t, UTM + 5.0, [1.0, 0.0, 0.0], parts=[[0, len(t) + 1, 0, 0, 0, 1, 1, 1]])


def test_tick_reports_and_cancels() -> None:
    v, t, parts = stored(*grid(200, 200, hills), part_triangles=2000)
    seen: list[float] = []
    gs.plane_section(v, t, UTM + 100.0, [1.0, 0.0, 0.0], parts=parts, tick=lambda f, m: seen.append(f))
    assert seen and seen[0] == 0.0 and seen[-1] == 1.0 and seen == sorted(seen)

    class Cancelled(Exception):
        pass

    def stop(fraction: float | None, message: str | None) -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        gs.plane_section(v, t, UTM + 100.0, [1.0, 0.0, 0.0], parts=parts, tick=stop)


# --- the window of a section line and clipping -------------------------------------------------------------


def test_window_of_a_section_line_matches_the_3d_view() -> None:
    w = gs.section_window(UTM[0] + 20.0, UTM[1] + 30.0, 90.0, length=60.0, towards=25.0, away=10.0)
    assert w.along.tolist() == [1.0, 0.0, 0.0] and w.normal.tolist() == [0.0, -1.0, 0.0]  # exact on 90°
    assert w.point.tolist() == [UTM[0] + 20.0, UTM[1] + 30.0, 0.0]
    assert w.planes().shape == (4, 4) and w.ends().shape == (2, 4)
    assert gs.section_window(0.0, 0.0, 33.0).planes().shape == (2, 4)
    assert gs.section_window(0.0, 0.0, 33.0).ends().shape == (0, 4)
    # the same window as `inCorridor` of ui/src/viz/section/math.ts
    rng = np.random.default_rng(5)
    for az in (0.0, 90.0, 37.5, 251.0):
        w = gs.section_window(UTM[0], UTM[1], az, length=80.0, towards=25.0, away=10.0)
        p = np.column_stack([UTM[0] + rng.uniform(-60, 140, 400), UTM[1] + rng.uniform(-60, 140, 400),
                             rng.uniform(-500, 500, 400)])  # fmt: skip
        s, c = math.sin(math.radians(az)), math.cos(math.radians(az))
        dx, dy = p[:, 0] - UTM[0], p[:, 1] - UTM[1]
        u, d = dx * s + dy * c, dx * c - dy * s
        clear = (np.abs(d - 25.0) > 1e-6) & (np.abs(d + 10.0) > 1e-6) & (np.abs(u) > 1e-6) & (np.abs(u - 80.0) > 1e-6)
        inside = (d <= 25.0) & (d >= -10.0) & (u >= 0.0) & (u <= 80.0)
        got = (p @ w.planes()[:, :3].T + w.planes()[:, 3] >= 0.0).all(axis=1)
        assert (got == inside)[clear].all() and inside.any() and not inside.all()
    for bad in ({"length": 0.0}, {"towards": -1.0}, {"away": math.nan}):
        with pytest.raises(ValueError):
            gs.section_window(0.0, 0.0, 10.0, **bad)
    with pytest.raises(ValueError):
        gs.section_window(0.0, math.nan, 10.0)


def test_section_in_the_window_of_a_line() -> None:
    v, t = cube(10, 100.0)
    lo = v.min(axis=0)
    y = lo[1] + 33.0
    unlimited = gs.section_window(lo[0] + 20.0, y, 90.0)
    pts, closed = only(gs.window_section(v, t, unlimited))
    assert closed and perimeter(pts, True) == pytest.approx(400.0, abs=LENGTH)
    assert np.abs(pts[:, 1] - y).max() <= ON_PLANE
    # a line 60 m long from x = 20: the square is cut at x = 20 and x = 80 into its top and its bottom
    w = gs.section_window(lo[0] + 20.0, y, 90.0, length=60.0, towards=25.0, away=10.0)
    s = gs.window_section(v, t, w)
    assert len(s) == 2 and not s.closed.any()
    assert s.lengths() == pytest.approx([60.0, 60.0], abs=LENGTH)
    for i in range(2):
        line = s.line(i)
        ends = sorted([line[0, 0] - lo[0], line[-1, 0] - lo[0]])
        assert ends == pytest.approx([20.0, 80.0], abs=1e-8) and distinct(line, False)
        assert np.ptp(line[:, 2]) <= ON_PLANE and abs(abs(line[0, 2] - lo[2] - 50.0) - 50.0) <= ON_PLANE
    # the faces of the window: 25 m towards the viewer (south of a line going east) and 10 m away
    near = gs.window_section(v, t, w, offset=w.towards)
    far = gs.window_section(v, t, w, offset=-w.away)
    assert np.abs(near.xyz[:, 1] - (y - 25.0)).max() <= ON_PLANE
    assert np.abs(far.xyz[:, 1] - (y + 10.0)).max() <= ON_PLANE
    assert near.lengths() == pytest.approx([60.0, 60.0], abs=LENGTH)
    # a window of zero depth keeps the section of its own plane (the planes of the faces do not clip it)
    flat = gs.section_window(lo[0] + 20.0, y, 90.0, length=60.0, towards=0.0, away=0.0)
    assert gs.window_section(v, t, flat).lengths() == pytest.approx([60.0, 60.0], abs=LENGTH)


def test_clip_cuts_closed_lines_at_the_planes() -> None:
    v, t = cube(8, 80.0)
    lo = v.min(axis=0)
    square = gs.plane_section(v, t, lo + 40.0, [0.0, 0.0, 1.0])
    assert square.closed.all()
    inside = square.clip([[1.0, 0.0, 0.0, -(lo[0] - 5.0)]])  # x ≥ lo − 5: everything
    assert inside.closed.all() and inside.xyz.tobytes() == square.xyz.tobytes()
    assert len(square.clip([[-1.0, 0.0, 0.0, lo[0] - 5.0]])) == 0  # x ≤ lo − 5: nothing
    half = square.clip([[1.0, 0.0, 0.0, -(lo[0] + 30.0)]])  # x ≥ lo + 30
    pts, closed = only(half)
    assert not closed and distinct(pts, False)
    assert perimeter(pts, False) == pytest.approx(50.0 + 80.0 + 50.0, abs=LENGTH)
    assert sorted([pts[0, 0], pts[-1, 0]]) == pytest.approx([lo[0] + 30.0] * 2, abs=1e-8)
    # two planes: a strip 20 m wide leaves two pieces of the square
    strip = square.clip([[1.0, 0.0, 0.0, -(lo[0] + 30.0)], [-1.0, 0.0, 0.0, lo[0] + 50.0]])
    assert len(strip) == 2 and strip.lengths() == pytest.approx([20.0, 20.0], abs=LENGTH)
    # a plane through vertices of the line: the vertex on the plane is kept, no piece of one point appears
    x = float(np.unique(v[:, 0])[3])
    exact = square.clip([[1.0, 0.0, 0.0, -x]])
    pts, closed = only(exact)
    assert not closed and pts[0, 0] == x and pts[-1, 0] == x and distinct(pts, False)
    assert perimeter(pts, False) == pytest.approx(50.0 + 80.0 + 50.0, abs=LENGTH)
    # the corner of the square alone on the plane: one point is not a line
    corner = square.clip([[-1.0, -1.0, 0.0, float(lo[0] + lo[1])]])
    assert len(corner) == 0 or corner.lengths().max() <= 1e-6
    with pytest.raises(ValueError):
        square.clip([[0.0, 0.0, 0.0, 1.0]])
