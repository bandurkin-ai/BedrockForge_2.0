"""Stitching a series of contours (ТЗ 7.23; ADR 0015 §7; 9.6): prisms and pyramids, the five kinds of caps,
contours of different shape and vertex count, tie lines, branching 1 : 2, 2 : 1 : 2 and 1 : 3, series, inclined
sections, UTM coordinates, bit-identical input vertices, errors with the numbers of the sections.

From BF `tests/test_stitch.py` with the assertions unchanged (the strips are in `test_stitch_strip.py`,
extrusion and triangle edits in `test_extrude.py`) and new: two squares 10 × 10 ten metres apart, every kind of
cap with its exact volume, branches no line separates, the limit on a pair, cancellation, the same bits with
strips in threads. Tolerances: volumes of prisms, pyramids and extrusions — 1e-9 relative (exact up to float64
rounding in project coordinates); morphing and branching cases — bounds stated in each test. No database: pure
geometry.
"""

import time

import numpy as np
import pytest

from bf_core.geo import stitch as gs
from bf_core.geo.checks import orientation_conflicts, self_intersections
from bf_core.geo.meshes import edge_summary, mesh_stats
from bf_core.geo.stitch import StitchError, StitchResult, stitch_contours

ORIGIN = np.array([512345.678, 6789012.345, 250.0])  # UTM-size coordinates
EXACT = 1e-9


# ---- helpers


def section(xz, position, azimuth=30.0):
    """A contour on a vertical section: (along strike, elevation) → project coordinates."""
    xz = np.asarray(xz, dtype=np.float64)
    a = np.radians(azimuth)
    strike = np.array([np.cos(a), np.sin(a), 0.0])
    across = np.array([-np.sin(a), np.cos(a), 0.0])
    return ORIGIN + xz[:, :1] * strike + position * across + xz[:, 1:] * np.array([0.0, 0.0, 1.0])


def level(xy, z):
    """A plan contour on a level."""
    xy = np.asarray(xy, dtype=np.float64)
    return ORIGIN + np.column_stack([xy, np.full(len(xy), float(z))])


def ellipse(n, a, b, cx=0.0, cy=0.0, phase=0.0):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False) + phase
    return np.column_stack([cx + a * np.cos(t), cy + b * np.sin(t)])


def rounded_rectangle(a, b, r):
    """17 vertices: four corners of four points each and one extra point on the bottom side."""
    pts = [[0.0, -b]]
    for cx, cy, start in ((a - r, -b + r, -90), (a - r, b - r, 0), (-a + r, b - r, 90), (-a + r, -b + r, 180)):
        for d in (0, 30, 60, 90):
            t = np.radians(start + d)
            pts.append([cx + r * np.cos(t), cy + r * np.sin(t)])
    return np.array(pts)


def half_ring(n, r0, r1, a0, a1):
    t = np.radians(np.linspace(a0, a1, n))
    outer = np.column_stack([r1 * np.cos(t), r1 * np.sin(t)])
    inner = np.column_stack([r0 * np.cos(t[::-1]), r0 * np.sin(t[::-1])])
    return np.vstack([outer, inner])


def rotate(xy, degrees):
    c, s = np.cos(np.radians(degrees)), np.sin(np.radians(degrees))
    return xy @ np.array([[c, s], [-s, c]])


def area(xy):
    x, y = xy[:, 0], xy[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y)))


def volume(v, t):
    """Signed volume (positive: normals outward), computed about the centre of the solid."""
    c = v - (v.min(axis=0) + v.max(axis=0)) / 2
    return float(np.einsum("ij,ij->i", c[t[:, 0]], np.cross(c[t[:, 1]], c[t[:, 2]])).sum()) / 6.0


def assert_solid(v, t):
    """Closed, outward, consistently oriented, no self-intersections."""
    stats = mesh_stats(v, t)
    assert stats["closed"] and stats["open_edges"] == 0 and stats["nonmanifold_edges"] == 0, stats
    assert stats["orientation"] == "outward"
    assert orientation_conflicts(t, len(v)) == 0
    assert self_intersections(v, t).pairs == 0
    assert t.dtype == np.int32 and v.dtype == np.float64
    assert volume(v, t) > 0


def has_edge(t, i, j):
    return bool(((t == i).any(axis=1) & (t == j).any(axis=1)).any())


def index_of(v, point):
    hit = np.flatnonzero((v == point).all(axis=1))
    assert len(hit) == 1
    return int(hit[0])


L_SHAPE = np.array([[0, 0], [40, 0], [40, 10], [10, 10], [10, 30], [0, 30]], dtype=np.float64)  # area 600


# ---- 1, 2: prism and pyramid


def test_two_identical_contours_give_a_prism():
    a, b = section(L_SHAPE, 0.0), section(L_SHAPE, 25.0)
    result = stitch_contours([a, b])
    assert isinstance(result, StitchResult)
    assert_solid(result.vertices, result.triangles)
    assert volume(result.vertices, result.triangles) == pytest.approx(600.0 * 25.0, rel=EXACT)
    assert len(result.vertices) == 12 and len(result.triangles) == 12 + 2 * 4  # no new vertices
    report = result.report
    assert report["closed"] and [s["contours"] for s in report["sections"]] == [[0], [1]]
    assert report["sections"][1]["position"] - report["sections"][0]["position"] == pytest.approx(25.0, abs=1e-6)
    assert report["pairs"] == [{"sections": [0, 1], "a": [0], "b": [1], "triangles": 12, "branch": False}]
    assert abs(np.dot(report["normal"], [-np.sin(np.radians(30)), np.cos(np.radians(30)), 0])) == pytest.approx(1)


def test_winding_and_a_repeated_last_vertex_do_not_matter():
    a = section(L_SHAPE[::-1], 0.0)
    b = section(np.vstack([L_SHAPE, L_SHAPE[:1]]), 25.0)
    result = stitch_contours([a, b])
    assert_solid(result.vertices, result.triangles)
    assert volume(result.vertices, result.triangles) == pytest.approx(15000.0, rel=EXACT)
    assert len(result.vertices) == 12


def test_pinch_out_to_a_point_is_a_pyramid():
    a, b = section(L_SHAPE, 0.0), section(L_SHAPE, 25.0)
    result = stitch_contours([a, b], end_cap="point", cap_distance=0.4)
    assert_solid(result.vertices, result.triangles)
    height = 0.4 * 25.0
    assert volume(result.vertices, result.triangles) - 15000.0 == pytest.approx(600.0 * height / 3.0, rel=EXACT)
    # Both ends pinched: two pyramids on the prism.
    both = stitch_contours([a, b], start_cap="point", end_cap="point", cap_distance=0.4)
    assert_solid(both.vertices, both.triangles)
    assert volume(both.vertices, both.triangles) == pytest.approx(15000.0 + 2 * 600.0 * height / 3.0, rel=EXACT)
    assert len(both.vertices) == 14


def test_extension_cap_continues_the_end_contours():
    a, b = section(L_SHAPE, 0.0), section(L_SHAPE, 25.0)
    result = stitch_contours([a, b], start_cap="extend", end_cap="extend", cap_distance=0.5)
    assert_solid(result.vertices, result.triangles)
    assert volume(result.vertices, result.triangles) == pytest.approx(600.0 * 50.0, rel=EXACT)


def test_open_ends_are_reported():
    result = stitch_contours([section(L_SHAPE, 0.0), section(L_SHAPE, 25.0)], start_cap="none", end_cap="none")
    assert not result.report["closed"]
    assert edge_summary(result.triangles, len(result.vertices)) == (12, 0)
    assert orientation_conflicts(result.triangles, len(result.vertices)) == 0


# ---- 3: different shapes and vertex counts


def test_contours_of_different_shape_and_vertex_count():
    big = ellipse(40, 30.0, 14.0, phase=2.0)                    # start vertex rotated
    small = rounded_rectangle(18.0, 9.0, 4.0)[::-1] + [6.0, 3.0]  # 17 vertices, opposite winding, shifted
    result = stitch_contours([section(big, 0.0), section(small, 20.0)])
    assert_solid(result.vertices, result.triangles)
    assert len(result.vertices) == 57 and len(result.triangles) == 57 + 38 + 15
    low, high = sorted([area(big), area(small)])
    # Between the prisms over the smaller and the larger contour.
    assert low * 20.0 < volume(result.vertices, result.triangles) < high * 20.0


# ---- 4: tie lines


def test_tie_lines_are_edges_of_the_result():
    lower, upper = ellipse(40, 30.0, 14.0), ellipse(28, 22.0, 16.0, cx=5.0)
    a, b = section(lower, 0.0), section(upper, 20.0)
    # Deliberately not the nearest vertices: the body turns by a few vertices between the sections.
    ties = [np.array([a[i], b[j]]) for i, j in ((0, 3), (10, 9), (22, 17), (31, 24))]
    # Ends may be given the other way round and slightly off the vertex (within the tolerance).
    ties[1] = ties[1][::-1] + 0.01
    result = stitch_contours([a, b], ties)
    assert_solid(result.vertices, result.triangles)
    assert result.report["ties_used"] == 4
    assert result.report["tolerance"] == pytest.approx(1e-3 * np.linalg.norm(np.ptp(a, axis=0)), rel=0.5)
    for i, j in ((0, 3), (10, 9), (22, 17), (31, 24)):
        assert has_edge(result.triangles, i, 40 + j)
    free = stitch_contours([a, b])
    assert not has_edge(free.triangles, 0, 40 + 3)  # without the tie this edge is not chosen


def test_ties_fix_a_twisted_pair():
    """A half ring and the same half ring turned by 100° on the next level: matched by shape alone
    the strip crosses itself; a few tie lines between corresponding vertices give a valid solid."""
    ring = half_ring(40, 22.0, 30.0, 90.0, 270.0)
    a, b = level(ring, 0.0), level(rotate(ring, 100.0), 15.0)
    with pytest.raises(StitchError) as error:
        stitch_contours([a, b])
    assert error.value.between == (0, 1)
    assert "разрезов 1 и 2" in str(error.value) and "тай-линии" in str(error.value)
    twisted = stitch_contours([a, b], check=False)  # still closed: only the geometry is wrong
    assert mesh_stats(twisted.vertices, twisted.triangles)["closed"]
    assert self_intersections(twisted.vertices, twisted.triangles).pairs > 0

    marks = (0, 13, 26, 39, 40, 52, 65, 78, 79)
    result = stitch_contours([a, b], [np.array([a[k], b[k]]) for k in marks])
    assert_solid(result.vertices, result.triangles)
    for k in marks:
        assert has_edge(result.triangles, k, 80 + k)
    # The body keeps its section while turning: at least 70 % of the prism (the cross-section
    # shrinks to cos 50° = 64 % in size half-way up) and not more than the prism.
    assert 0.7 * area(ring) * 15.0 < volume(result.vertices, result.triangles) <= area(ring) * 15.0 * (1 + 1e-9)


def test_very_dissimilar_contours_still_give_a_strip():
    """A sliver far to the side of a sprawling contour: the cheapest path walks one whole contour
    from a single vertex of the other (two cones glued along an edge, not a strip); the search
    that counts turns is used instead and the body is a valid closed solid."""
    wide = np.array([[4.83, 4.08], [-2.22, 2.75], [-14.44, 0.44], [-14.12, -3.18], [-12.79, -7.15],
                     [-12.52, -7.96], [-16.24, -10.97], [-18.23, -10.54], [-2.59, -36.54], [5.39, -38.51],
                     [1.03, -22.21], [0.93, -14.39], [0.83, -6.56], [0.73, 1.27]])
    sliver = np.array([[-56.51, 27.84], [-37.3, 38.77], [-39.46, 38.39], [-41.78, 41.61], [-43.03, 41.47],
                       [-43.18, 42.65], [-43.85, 44.86]])
    result = stitch_contours([level(wide, 0.0), level(sliver, 10.85)], check=False)
    stats = mesh_stats(result.vertices, result.triangles)
    assert stats["closed"] and stats["orientation"] == "outward"
    assert orientation_conflicts(result.triangles, len(result.vertices)) == 0
    # The same with tie lines that allow such a path: two ties from one vertex.
    a, b = section(ellipse(12, 30.0, 14.0), 0.0), section(ellipse(9, 22.0, 16.0), 20.0)
    tied = stitch_contours([a, b], [np.array([a[0], b[0]]), np.array([a[0], b[5]])], check=False)
    assert mesh_stats(tied.vertices, tied.triangles)["closed"]
    assert has_edge(tied.triangles, 0, 12) and has_edge(tied.triangles, 0, 12 + 5)


def test_crossing_and_misplaced_ties_are_refused():
    a, b = section(ellipse(40, 30.0, 14.0), 0.0), section(ellipse(28, 22.0, 16.0), 20.0)
    with pytest.raises(StitchError, match="пересекаются") as error:
        stitch_contours([a, b], [np.array([a[0], b[0]]), np.array([a[10], b[14]]), np.array([a[20], b[7]])])
    assert error.value.between == (0, 1)
    with pytest.raises(StitchError, match="не совпадает ни с одной вершиной"):
        stitch_contours([a, b], [np.array([a[0], b[0] + [0.0, 0.0, 5.0]])])
    with pytest.raises(StitchError, match="соседних разрезов"):
        stitch_contours([a, b], [np.array([a[0], a[5]])])
    c = section(ellipse(20, 10.0, 8.0), 40.0)
    with pytest.raises(StitchError, match="соседних разрезов") as error:
        stitch_contours([a, b, c], [np.array([a[0], c[0]])])
    assert error.value.between == (0, 2)


# ---- 5: branching


PARENT = ellipse(60, 40.0, 15.0)
CHILD_1 = ellipse(30, 14.0, 10.0, cx=-22.0)
CHILD_2 = ellipse(24, 13.0, 9.0, cx=23.0, phase=1.0)[::-1]


def test_branching_one_to_two():
    contours = [section(PARENT, 0.0), section(CHILD_1, 20.0), section(CHILD_2, 20.0)]
    result = stitch_contours(contours)
    assert_solid(result.vertices, result.triangles)
    report = result.report
    assert [s["contours"] for s in report["sections"]] == [[0], [1, 2]]
    assert len(report["pairs"]) == 1 and report["pairs"][0]["branch"]
    assert report["pairs"][0]["a"] == [0] and report["pairs"][0]["b"] == [1, 2]
    # New vertices: only the cut of the parent (two ends on its outline and the chord between them).
    added = result.vertices[114:]
    assert 3 <= len(added) <= 12
    strike = [np.cos(np.radians(30)), np.sin(np.radians(30)), 0]
    assert np.abs((added - ORIGIN) @ strike).max() < 3.0  # the cut is between the children (x = −8…10)
    # The parent tapers into two smaller bodies: less than the prism over the parent, more than
    # the two cylinders over the children.
    children = area(CHILD_1) + area(CHILD_2)
    assert children * 20.0 < volume(result.vertices, result.triangles) < area(PARENT) * 20.0
    # The same series given upside down (two → one) is the same body.
    flipped = stitch_contours(contours[::-1])
    assert_solid(flipped.vertices, flipped.triangles)
    same = volume(result.vertices, result.triangles)
    assert volume(flipped.vertices, flipped.triangles) == pytest.approx(same, rel=1e-9)


def test_branching_two_one_two_series():
    contours = [section(CHILD_1, 0.0), section(CHILD_2, 0.01), section(PARENT, 20.0),
                # the upper branches part along the other axis of the parent: the two cuts cross in projection
                section(ellipse(20, 12.0, 5.0, cy=8.0), 40.0), section(ellipse(22, 12.0, 5.0, cy=-8.0), 40.0)]
    result = stitch_contours(contours)
    assert_solid(result.vertices, result.triangles)
    report = result.report
    assert [s["contours"] for s in report["sections"]] == [[0, 1], [2], [3, 4]]
    assert [(p["a"], p["b"], p["branch"]) for p in report["pairs"]] == [([0, 1], [2], True), ([2], [3, 4], True)]
    low = (area(CHILD_1) + area(CHILD_2)) * 20.0 / 2 + 2 * area(ellipse(20, 12.0, 5.0)) * 20.0 / 2
    high = area(PARENT) * 40.0
    assert low < volume(result.vertices, result.triangles) < high


def test_branching_one_to_three_with_caps():
    contours = [level(ellipse(80, 40.0, 40.0), 0.0), level(ellipse(20, 8.0, 8.0, cx=-22.0, cy=-10.0), -20.0),
                level(ellipse(20, 8.0, 8.0, cx=22.0, cy=-10.0), -20.0), level(ellipse(20, 6.0, 6.0, cy=28.0), -20.0)]
    result = stitch_contours(contours, start_cap="point", end_cap="extend")
    assert_solid(result.vertices, result.triangles)
    assert result.report["pairs"][0]["b"] == [1, 2, 3] and result.report["pairs"][0]["branch"]


def test_tie_line_in_a_branching():
    contours = [section(PARENT, 0.0), section(CHILD_1, 20.0), section(CHILD_2, 20.0)]
    # Vertex 30 of the parent is its left end (x = −40); vertex 15 of the first child is its left end.
    result = stitch_contours(contours, [np.array([contours[0][30], contours[1][15]])])
    assert_solid(result.vertices, result.triangles)
    assert has_edge(result.triangles, 30, 60 + 15)
    with pytest.raises(StitchError, match="другой ветвью"):
        stitch_contours(contours, [np.array([contours[0][0], contours[1][15]])])  # the right end belongs to child 2


# ---- 6: series, line and extension caps, plan contours


def test_series_of_sections_with_line_and_extension_caps():
    shapes = [ellipse(30 + 4 * i, 30.0 + 5.0 * np.sin(i), 10.0 + i, cx=3.0 * i, phase=0.7 * i) for i in range(6)]
    contours = [section(s if i % 2 else s[::-1], 20.0 * i) for i, s in enumerate(shapes)]
    order = [3, 0, 5, 1, 4, 2]  # the order of the list does not matter, except for which end is the first
    calls = []
    result = stitch_contours([contours[i] for i in order], start_cap="line", end_cap="extend", cap_distance=0.5,
                             progress=lambda f, m: calls.append((f, m)))
    assert_solid(result.vertices, result.triangles)
    # Sections are sorted along the normal, whose sign puts the first contour of the list before
    # the last one: here the first (drawn on section 3) lies beyond the last (section 2), so the
    # series runs from section 5 down to section 0 and the start cap is on section 5.
    assert [s["contours"] for s in result.report["sections"]] == [[2], [4], [0], [5], [3], [1]]
    positions = [s["position"] for s in result.report["sections"]]
    assert np.allclose(np.diff(positions), 20.0, atol=1e-6)
    areas = [area(s) for s in shapes]
    body = sum((areas[i] + areas[i + 1]) / 2 * 20.0 for i in range(5))
    total = volume(result.vertices, result.triangles)
    # Body: within 5 % of the trapezoid rule. Start (section 5): ridge cap, between a third and
    # the whole of its prism. End (section 0): extension, exactly its prism.
    cap_prism = areas[5] * 10.0
    assert 0.95 * body + cap_prism / 3 + areas[0] * 10.0 < total < 1.05 * body + cap_prism + areas[0] * 10.0
    assert calls[0][0] == 0.0 and calls[-1] == (1.0, "Готово")
    assert [f for f, _ in calls] == sorted(f for f, _ in calls)


def test_line_cap_of_a_rectangle_is_a_hip_roof():
    rect = np.array([[x, -5.0] for x in np.linspace(-20, 20, 9)] + [[20.0, 0.0]]
                    + [[x, 5.0] for x in np.linspace(20, -20, 9)] + [[-20.0, 0.0]])
    a, b = level(rect, 0.0), level(rect, 10.0)
    result = stitch_contours([a, b], end_cap="line", cap_distance=0.6)
    assert_solid(result.vertices, result.triangles)
    ridge = result.vertices[len(a) + len(b):]
    assert np.allclose(ridge[:, 2], ORIGIN[2] + 16.0) and np.allclose(ridge[:, 1], ORIGIN[1], atol=1e-9)
    assert ridge[:, 0].min() >= ORIGIN[0] - 10.0 - 1e-9 and ridge[:, 0].max() <= ORIGIN[0] + 10.0 + 1e-9
    # Hip roof 40 × 10 with a ridge of 20 at height 6: V = h·b·(2a + ridge) / 6 = 1000. Triangles
    # may cut the corners between the slopes, so: more than the pyramid (800), at most the roof.
    assert 800.0 < volume(result.vertices, result.triangles) - 4000.0 <= 1000.0 * (1 + 1e-9)


def test_plan_contours_on_levels():
    shapes = [ellipse(36, 25.0 - 2.0 * i, 18.0 + i, cx=2.0 * i) for i in range(5)]
    contours = [level(s, 100.0 - 10.0 * i) for i, s in enumerate(shapes)]
    result = stitch_contours(contours)
    assert_solid(result.vertices, result.triangles)
    assert np.abs(result.report["normal"]) == pytest.approx([0, 0, 1], abs=1e-12)
    assert [s["contours"] for s in result.report["sections"]] == [[0], [1], [2], [3], [4]]
    areas = [area(s) for s in shapes]
    body = sum((areas[i] + areas[i + 1]) / 2 * 10.0 for i in range(4))
    assert volume(result.vertices, result.triangles) == pytest.approx(body, rel=0.02)


def test_inclined_sections_and_section_tolerance():
    # Sections dipping 60°, contours digitised with a few centimetres of noise across the plane.
    rng = np.random.default_rng(7)
    normal = np.array([0.0, np.sin(np.radians(60)), np.cos(np.radians(60))])
    u, v = np.array([1.0, 0.0, 0.0]), np.cross(normal, [1.0, 0.0, 0.0])

    def on_plane(xy, position):
        return ORIGIN + xy[:, :1] * u + xy[:, 1:] * v + (position + rng.normal(0, 0.02, len(xy)))[:, None] * normal

    contours = [on_plane(ellipse(30, 20.0, 8.0), 0.0), on_plane(ellipse(30, 6.0, 5.0, cx=-12.0), 15.3),
                on_plane(ellipse(30, 6.0, 5.0, cx=12.0), 14.7), on_plane(ellipse(30, 5.0, 4.0, cx=-12.0), 30.0),
                on_plane(ellipse(30, 5.0, 4.0, cx=12.0), 30.0)]
    result = stitch_contours(contours)
    assert_solid(result.vertices, result.triangles)
    assert [s["contours"] for s in result.report["sections"]] == [[0], [1, 2], [3, 4]]
    assert result.report["section_tolerance"] == pytest.approx(1.5, rel=0.05)
    # A tolerance below the offset between the two branches puts them on different sections: the
    # series becomes a chain 0 → 2 → 1 → …, which is not what was drawn, but it is what was asked for.
    assert len(stitch_contours(contours, section_tolerance=0.1, check=False).report["sections"]) == 4


# ---- 7: exact coordinates and errors


def test_input_vertices_are_bit_identical_in_the_output():
    contours = [section(PARENT, 0.0), section(CHILD_1[::-1], 20.0), section(CHILD_2, 20.0),
                section(np.vstack([ellipse(15, 5.0, 4.0, cx=-22.0), ellipse(15, 5.0, 4.0, cx=-22.0)[:1]]), 41.5)]
    for caps in ("flat", "point", "line", "extend"):
        result = stitch_contours(contours, start_cap=caps, end_cap=caps)
        given = np.vstack([contours[0], contours[1], contours[2], contours[3][:-1]])
        assert result.vertices[:len(given)].tobytes() == given.tobytes()
        assert len(result.vertices) > len(given)  # branch cut and caps add vertices after the given ones
        assert len(np.unique(result.triangles)) == len(result.vertices)  # no unused vertices
        assert_solid(result.vertices, result.triangles)


def test_errors_are_stitch_errors_in_russian():
    a = section(L_SHAPE, 0.0)
    with pytest.raises(StitchError, match="хотя бы на двух разрезах") as error:
        stitch_contours([a])
    assert error.value.between is None and isinstance(error.value, ValueError)
    with pytest.raises(StitchError, match="на одном разрезе"):
        stitch_contours([a, section(L_SHAPE + [60.0, 0.0], 0.0)])
    with pytest.raises(StitchError, match="Контур 2: нужно не меньше трёх вершин"):
        stitch_contours([a, a[:2] + 5.0])
    with pytest.raises(StitchError, match="Контур 2 вырожден"):
        stitch_contours([a, np.array([[0.0, 0, 0], [1, 1, 1], [2, 2, 2], [3, 3, 3]]) + ORIGIN])
    with pytest.raises(StitchError, match="не в плоскости разрезов"):
        stitch_contours([a, section(L_SHAPE, 20.0), section(L_SHAPE, 40.0), level(L_SHAPE, 0.0)])
    with pytest.raises(StitchError, match="замыкания торца"):
        stitch_contours([a, section(L_SHAPE, 20.0)], end_cap="round")
    with pytest.raises(StitchError, match="пропуски"):
        stitch_contours([a, section(L_SHAPE, 20.0) * np.nan])


def test_self_intersecting_strip_names_the_sections():
    # Sections 3 and 4 (indices 2, 3): the half ring turned too far; the others are fine.
    ring = half_ring(40, 22.0, 30.0, 90.0, 270.0)
    contours = [level(ring, 0.0), level(ring, 15.0), level(ring, 30.0), level(rotate(ring, 100.0), 45.0),
                level(rotate(ring, 100.0), 60.0)]
    with pytest.raises(StitchError, match="Между контурами разрезов 3 и 4 получаются самопересечения") as error:
        stitch_contours(contours)
    assert error.value.between == (2, 3)


# ---- 9: speed


def test_hundred_contours_of_five_hundred_vertices():
    contours = [section(ellipse(500, 30.0 + 5.0 * np.sin(i), 12.0 + i % 7, cx=4.0 * np.sin(0.3 * i), phase=0.1 * i),
                        20.0 * i) for i in range(100)]
    stitch_contours(contours[:3])  # warm-up: numba kernels are compiled (or loaded from the cache)
    start = time.perf_counter()
    result = stitch_contours(contours)
    seconds = time.perf_counter() - start
    start = time.perf_counter()
    stitch_contours(contours, check=False)
    unchecked = time.perf_counter() - start
    print(f"\nstitch 100 contours x 500 vertices: {seconds:.2f} s with the self-intersection check, "
          f"{unchecked:.2f} s without; {len(result.triangles)} triangles")
    assert len(result.triangles) == 99 * 1000 + 2 * 498
    assert mesh_stats(result.vertices, result.triangles)["closed"]
    assert seconds < 20.0


# ---- Э9: acceptance, caps, branches, limits


SQUARE = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]])


def test_two_squares_ten_metres_apart_give_a_solid_of_1000():
    """ТЗ 7.23, приёмка: два контура 10 × 10 на расстоянии 10 м с плоскими торцами → замкнутое тело 1000."""
    a, b = level(SQUARE, 0.0), level(SQUARE, 10.0)
    result = stitch_contours([a, b], start_cap="flat", end_cap="flat")
    assert_solid(result.vertices, result.triangles)
    assert mesh_stats(result.vertices, result.triangles)["volume"] == pytest.approx(1000.0, rel=EXACT)
    assert result.report["closed"] and result.report["volume"] == pytest.approx(1000.0, rel=EXACT)
    assert result.vertices.tobytes() == np.vstack([a, b]).tobytes()  # nothing added, bit for bit


@pytest.mark.parametrize("kind", gs.CAPS)
def test_every_kind_of_cap_on_utm_squares(kind):
    a, b = level(SQUARE, 0.0), level(SQUARE, 10.0)
    result = stitch_contours([a, b], start_cap=kind, end_cap=kind, cap_distance=0.6)
    v, t = result.vertices, result.triangles
    assert v[:8].tobytes() == np.vstack([a, b]).tobytes()
    h = 0.6 * 10.0
    if kind == "none":
        assert not result.report["closed"] and result.report["volume"] is None
        assert edge_summary(t, len(v)) == (8, 0) and orientation_conflicts(t, len(v)) == 0
        return
    assert_solid(v, t)
    got = mesh_stats(v, t)["volume"]
    assert result.report["volume"] == pytest.approx(got, rel=1e-12)
    expected = {"flat": 1000.0, "point": 1000.0 + 2 * 100.0 * h / 3.0, "extend": 1000.0 + 2 * 100.0 * h}
    if kind in expected:
        assert got == pytest.approx(expected[kind], rel=EXACT)
    else:  # a ridge over half the square: more than two pyramids, less than two prisms
        assert 1000.0 + 2 * 100.0 * h / 3.0 < got < 1000.0 + 2 * 100.0 * h


def test_branches_that_no_line_separates_are_refused_with_the_sections():
    parent = ellipse(60, 25.0, 20.0, cx=15.0, cy=10.0)
    hook = np.array([[0, 0], [20, 0], [20, 4], [4, 4], [4, 12], [14, 12], [14, 8], [18, 8], [18, 16], [0, 16]], float)
    for children in ([ellipse(20, 5.0, 5.0), ellipse(20, 5.0, 5.0)], [hook, (np.array([30.0, 20.0]) - hook)[::-1]]):
        contours = [level(parent, 0.0), level(children[0], 10.0), level(children[1], 10.0), level(parent, 20.0)]
        with pytest.raises(StitchError, match="разрез(ами|ов) 1 и 2") as error:
            stitch_contours(contours)
        assert error.value.between == (0, 1) and error.value.code in ("stitch_branch", "stitch_self_intersection")
        assert error.value.hint and set(error.value.contours) <= {0, 1, 2} and error.value.contours
    with pytest.raises(StitchError, match="контур 1, ветви — контуры 2, 3") as error:
        stitch_contours(contours[:1] + [level(ellipse(20, 5.0, 5.0), 10.0)] * 2)
    assert error.value.code == "stitch_branch" and error.value.contours == (0, 1, 2)


def test_branching_on_utm_keeps_the_given_vertices_and_closes():
    contours = [section(PARENT, 0.0), section(CHILD_1, 20.0), section(CHILD_2, 20.0), section(PARENT, 40.0)]
    result = stitch_contours(contours)
    assert_solid(result.vertices, result.triangles)
    given = np.vstack(contours)
    assert result.vertices[: len(given)].tobytes() == given.tobytes()
    assert [p["branch"] for p in result.report["pairs"]] == [True, True]


def test_a_pair_too_large_is_refused(monkeypatch):
    monkeypatch.setattr(gs, "MAX_SPANS", 40 * 28)
    a, b = section(ellipse(40, 30.0, 14.0), 0.0), section(ellipse(28, 22.0, 16.0), 20.0)
    assert stitch_contours([a, b]).report["closed"]
    c = section(ellipse(45, 22.0, 16.0), 40.0)
    with pytest.raises(StitchError, match="Между контурами разрезов 2 и 3 контуры слишком подробны") as error:
        stitch_contours([a, b, c])
    assert error.value.code == "stitch_too_large" and error.value.between == (1, 2)


def test_strips_in_threads_give_the_same_solid_bit_for_bit():
    """ADR 0015 «Замеры»: strips at once in threads (ADR 0014, no prange) — vertices, triangles and the report
    are the same for 1, 2 and 4 threads; with a branching 2 : 1 : 2, tie lines and different vertex counts."""
    shapes = [ellipse(30 + 7 * i, 30.0 + 5.0 * np.sin(i), 10.0 + i, cx=3.0 * i, phase=0.7 * i) for i in range(7)]
    contours = [section(s if i % 2 else s[::-1], 20.0 * i) for i, s in enumerate(shapes)]
    contours += [section(CHILD_1, 140.0), section(CHILD_2, 140.01)]
    ties = [np.array([contours[1][0], contours[2][5]])]
    results = [stitch_contours(contours, ties, start_cap="point", end_cap="extend", threads=t) for t in (1, 2, 4)]
    assert_solid(results[0].vertices, results[0].triangles)
    assert results[0].report["pairs"][-1]["branch"]
    for other in results[1:]:
        assert other.vertices.tobytes() == results[0].vertices.tobytes()
        assert other.triangles.tobytes() == results[0].triangles.tobytes()
        assert other.report == results[0].report


def test_progress_may_cancel_between_strips():
    contours = [section(ellipse(30, 10.0 + i, 8.0), 10.0 * i) for i in range(5)]
    seen = []

    def progress(fraction, message):
        seen.append(message)
        if message.startswith("Сшивка контуров: 3"):
            raise KeyboardInterrupt  # what JobContext.tick raises is any exception: it goes through untouched

    with pytest.raises(KeyboardInterrupt):
        stitch_contours(contours, progress=progress)
    assert seen[-1] == "Сшивка контуров: 3 из 4"
