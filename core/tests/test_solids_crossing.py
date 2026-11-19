"""Solids, part 3: lines where two wireframes cross (ТЗ 7.23 «Линии пересечения»; ADR 0015 §7, 9.13).

Ported from BF `tests/test_solids.py` (intersection lines) with the assertions kept; new: a cube and a plane
give a closed square, two DTMs, coplanar pairs give nothing, the 9.13 time criterion, cancellation.
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from bf_core.geo.crossing import intersection_lines
from bf_core.geo.section import plane_section
from bf_core.geo.solids import boolean
from tests.solidutil import OFFSET, box, grid, length, sphere


def test_cube_and_plane_give_a_closed_square() -> None:
    cube = box((0, 0, 0), (10, 10, 10))
    plane = grid(7, 9, (-3, 13), (-2, 12), lambda x, y: 0 * x + 4.25)
    lines = intersection_lines(cube, plane)
    assert len(lines) == 1 and lines[0]["closed"]
    p = lines[0]["points"] - OFFSET
    assert p.dtype == np.float64 and np.abs(p[:, 2] - 4.25).max() < 1e-9
    assert length(lines[0]["points"], True) == pytest.approx(40, abs=1e-9)
    corners = {(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)}
    assert corners <= {tuple(q) for q in np.round(p[:, :2], 9).tolist()}
    assert not np.array_equal(lines[0]["points"][0], lines[0]["points"][-1])


def test_two_crossing_planes_give_one_straight_line() -> None:
    horizontal = grid(13, 7, (0, 10), (0, 10), lambda x, y: 0 * x + 2)
    # The plane x = 4 over y in [-3, 8], z in [0, 5].
    v, t = grid(9, 5, (-3, 8), (0, 5), offset=np.zeros(3))
    vertical = (np.column_stack([np.full(len(v), 4.0), v[:, 0], v[:, 1]]) + OFFSET, t)
    lines = intersection_lines(horizontal, vertical)
    assert len(lines) == 1 and not lines[0]["closed"]
    points = lines[0]["points"] - OFFSET
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


def test_coplanar_pairs_give_nothing() -> None:
    """The same plane triangulated two ways (and a shifted copy of one mesh): no line at all."""
    a = grid(10, 10, (0, 10), (0, 10), lambda x, y: 0 * x + 3)
    b = grid(7, 13, (-2, 8), (1, 12), lambda x, y: 0 * x + 3)
    assert intersection_lines(a, b) == []
    assert intersection_lines(a, a) == []
    tilted = grid(10, 10, (0, 10), (0, 10), lambda x, y: 0.3 * x - 0.2 * y)
    other = grid(6, 9, (2, 12), (-1, 9), lambda x, y: 0.3 * x - 0.2 * y)
    assert intersection_lines(tilted, other) == []


def test_two_dtms_cross_along_their_equal_heights() -> None:
    """A tilted plane z = 0.1 x and a ridge z = 3 − |y − 5| · 0.4: lines where 0.1 x = 3 − 0.4 |y − 5|."""
    a = grid(30, 30, (0, 30), (0, 10), lambda x, y: 0.1 * x)
    b = grid(25, 20, (0, 30), (0, 10), lambda x, y: 3 - 0.4 * np.abs(y - 5))
    lines = intersection_lines(a, b)
    assert len(lines) == 1 and not lines[0]["closed"]
    p = lines[0]["points"] - OFFSET
    # Both meshes are exact on their grids (the ridge has a node row on y = 5): 1e-9 m.
    assert np.abs(p[:, 2] - 0.1 * p[:, 0]).max() < 1e-9
    assert np.abs(p[:, 2] - (3 - 0.4 * np.abs(p[:, 1] - 5))).max() < 1e-9
    # A "V" in plan from (10, 0) through (30, 5) to (10, 10).
    ends = sorted([tuple(np.round(p[0, :2], 9)), tuple(np.round(p[-1, :2], 9))])
    assert ends == [(10.0, 0.0), (10.0, 10.0)]
    assert length(p[:, :2], False) == pytest.approx(2 * math.hypot(20, 5), abs=1e-9)


def test_sphere_and_plane_give_a_closed_loop() -> None:
    r, h = 5.0, 1.3
    ball = sphere(r, 96, 192)
    plane = grid(37, 41, (-7, 7), (-7, 7), lambda x, y: 0 * x + h)
    lines = intersection_lines(ball, plane)
    assert len(lines) == 1 and lines[0]["closed"]
    points = lines[0]["points"] - OFFSET
    assert np.abs(points[:, 2] - h).max() < 1e-9
    radius = math.sqrt(r * r - h * h)
    # The loop is a polygon inscribed in the circle (192 meridians): its length is below the circle's by
    # < 0.1 %, the points are within 0.1 % of the radius.
    assert length(points, True) == pytest.approx(2 * math.pi * radius, rel=1e-3)
    assert np.hypot(points[:, 0], points[:, 1]) == pytest.approx(radius, rel=1e-3)
    assert not np.allclose(points[0], points[-1])  # a closed line does not repeat its first point
    # The same loop as the exact section of the sphere by the plane.
    section = plane_section(ball[0], ball[1], OFFSET + [0, 0, h], [0, 0, 1])
    assert len(section.closed) == 1
    assert length(points, True) == pytest.approx(length(section.xyz, True), abs=1e-5)


def test_two_cubes_cross_along_one_loop() -> None:
    a = box((0, 0, 0), (10, 10, 10))
    b = box((4, 3, 2), (14, 13, 12))
    lines = intersection_lines(a, b)
    assert len(lines) == 1 and lines[0]["closed"]
    points = lines[0]["points"] - OFFSET
    # A hexagon-like loop around the corner (10, 10, 10) of A and (4, 3, 2) of B.
    assert length(points, True) == pytest.approx(2 * (6 + 7 + 8), abs=1e-9)
    corners = {(10, 3, 2), (4, 10, 2), (4, 3, 10), (10, 10, 2), (10, 3, 10), (4, 10, 10)}
    found = {tuple(np.round(p, 9).tolist()) for p in points}
    assert corners <= found
    # Every point lies on the surface of both cubes.
    on_a = np.isclose(points, 0, atol=1e-9) | np.isclose(points, 10, atol=1e-9)
    on_b = np.isclose(points, [4, 3, 2], atol=1e-9) | np.isclose(points, [14, 13, 12], atol=1e-9)
    assert on_a.any(axis=1).all() and on_b.any(axis=1).all()


def test_intersection_line_bounds_the_boolean_result() -> None:
    """Two spheres: the line is the edge between the two skins of their intersection."""
    a, b = sphere(5, 64, 128), sphere(4, 48, 96, centre=(3, 2, 1))
    lines = intersection_lines(a, b)
    assert len(lines) == 1 and lines[0]["closed"]
    points = lines[0]["points"]
    d = math.sqrt(14)
    x = (d * d + 25 - 16) / (2 * d)
    assert length(points, True) == pytest.approx(2 * math.pi * math.sqrt(25 - x * x), rel=5e-3)
    v, _, _ = boolean(a, b, "intersection")
    known = {tuple(p) for p in np.round(v, 6).tolist()}
    hits = sum(tuple(p) in known for p in np.round(points, 6).tolist())
    # The boolean result has the same vertices along the cut (1e-6 m); manifold3d may merge some that are
    # closer than its tolerance.
    assert hits >= 0.95 * len(points)


def test_ticks_and_cancellation() -> None:
    a = grid(60, 60, (0, 100), (0, 100), lambda x, y: 5 * np.sin(x / 9))
    b = grid(50, 50, (3, 103), (-2, 98), lambda x, y: 4 * np.cos(y / 7))
    seen: list[float] = []
    intersection_lines(a, b, tick=lambda fraction, message: seen.append(fraction))
    assert seen == sorted(seen) and seen[0] == 0.0 and seen[-1] == 1.0

    def stop(fraction: float, message: str) -> None:
        if fraction > 0:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        intersection_lines(a, b, tick=stop)


def test_speed_two_hundred_thousand_triangles() -> None:
    """9.13 criterion: 2 × 100 000 triangles ≤ 1 s (after the kernels are loaded)."""
    n = 224  # 2 × 224² = 100 352 triangles each
    a = grid(n, n, (0, 1000), (0, 1000), lambda x, y: 20 * np.sin(x / 60) * np.cos(y / 45))
    b = grid(n, n, (3, 1003), (-2, 998), lambda x, y: 15 * np.cos(x / 50) + 0.01 * y - 5)
    intersection_lines(grid(2, 2, (0, 1), (0, 1)), grid(2, 2, (0, 1), (0, 1), lambda x, y: x - 0.5))  # warm-up
    started = time.perf_counter()
    lines = intersection_lines(a, b)
    seconds = time.perf_counter() - started
    points = sum(len(line["points"]) for line in lines)
    print(f"\nintersection_lines: {len(a[1])} × {len(b[1])}: {seconds:.2f} s, {len(lines)} lines, {points} points")
    assert len(lines) > 3 and points > 1000
    # Every point lies on both surfaces: within 0.1 m of the smooth functions (facets of 4.5 m).
    p = np.vstack([line["points"] for line in lines]) - OFFSET
    assert np.abs(p[:, 2] - 20 * np.sin(p[:, 0] / 60) * np.cos(p[:, 1] / 45)).max() < 0.1
    assert np.abs(p[:, 2] - (15 * np.cos(p[:, 0] / 50) + 0.01 * p[:, 1] - 5)).max() < 0.1
    assert seconds < 1.0


@pytest.mark.slow
def test_speed_against_a_coarse_body() -> None:
    """A mesh against a coarse body with triangles far larger than the grid cells (BF)."""
    n = 317  # 200 978 triangles
    a = grid(n, n, (0, 1000), (0, 1000), lambda x, y: 20 * np.sin(x / 60) * np.cos(y / 45))
    ball = sphere(400, 224, 448, centre=(500, 500, 0))
    started = time.perf_counter()
    lines = intersection_lines(a, ball)
    seconds = time.perf_counter() - started
    print(f"\nintersection_lines: {len(a[1])} × {len(ball[1])}: {seconds:.2f} s, {len(lines)} lines")
    assert len(lines) == 1 and lines[0]["closed"]
    assert seconds < 20
