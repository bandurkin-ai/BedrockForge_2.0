"""Holes that start, bend or end exactly on a wireframe, go exactly through its edges and vertices, or run
within the 1 mm of their straight pieces from a crease (ТЗ 7.15; `geo/intersect.py`: what the numbers leave open
is told by the hole moved by an infinitesimal — up, then east, then north).

References and tolerances:
  * the ridge of the review of Э8 — a grid of 10 × 4 cells, z = −1.2 · clip(x − 2, 0, 4), a collar on the crest
    (2, 2, 0), azimuth 90°, dip −47°: the hole first goes into the air over the face (50.2°) and meets the lower
    level at 4.8 / sin 47° = 6.5632 m — one crossing, to 1e-9 m; the directions around it, a collar on a vertex,
    on an edge and inside a triangle, plain and UTM-sized coordinates, a curved hole, both orientations;
  * any fan — holes from the vertices of a random relief (peaks, pits, ridges, valleys and saddles) and through
    them, against a scan with bisection along `positions()` in which the collar is above the surface: 1e-6 m;
  * exact arithmetic — polylines through vertices, middles of edges and points of faces of a relief and of a
    closed solid whose coordinates are multiples of 1/8, against the same polylines moved by (1e-18, 1e-27, 1e-9)
    and crossed with the triangles in rational numbers (`fractions`): the same crossings with the same sides,
    depths to 1e-6 m;
  * a graze — an arc of radius 191 m cut into pieces 1 mm off it: the pieces come out of a valley the arc passes
    0.2–0.9 mm under (and into a ridge it passes over) — no crossing; an arc that does come out by 0.5 mm gives
    both crossings where the formula says, to 1e-6 m.
"""

from __future__ import annotations

import math
from fractions import Fraction

import numpy as np
import pytest

from bf_core.drillholes.desurvey import METHODS, Trajectories
from bf_core.geo import intersect as gi
from tests.meshutil import UTM, grid
from tests.test_geo_intersect import NAN, holes

pytestmark = pytest.mark.windows

DEPTH = 1e-6  # metres along the hole; the acceptance of ТЗ 7.15 is 1 mm
ZERO = np.zeros(3)


def crossings(traj: Trajectories, *meshes: gi.MeshTarget) -> list[list[tuple[float, int]]]:
    """[(depth, side)] of every hole, of all the wireframes together."""
    hits = gi.hole_crossings(traj, list(meshes))
    assert set(hits.side.tolist()) <= {-1, 1}
    return [list(zip(hits.depth[hits.hole == h].tolist(), hits.side[hits.hole == h].tolist(), strict=True))
            for h in range(traj.n_holes)]  # fmt: skip


def same(got: list[tuple[float, int]], want: list[tuple[float, int]], tol: float = DEPTH) -> bool:
    return [s for _d, s in got] == [s for _d, s in want] and [d for d, _s in got] == pytest.approx(
        [d for d, _s in want], abs=tol
    )


# --- the ridge of the review -------------------------------------------------------------------------------

CREST = 2 * 11 + 2  # the vertex (2, 2) of a grid of 10 × 4 cells


def ridge(offset: np.ndarray = ZERO, slope: float = 1.2, up: float = -1.0) -> tuple[np.ndarray, np.ndarray]:
    """A level at z = 0 for x ≤ 2, a face down (`up` 1: up) to x = 6, a level beyond."""
    return grid(10, 4, lambda x, y: up * slope * np.clip(x - 2.0, 0.0, 4.0) + 0.0 * y, step=1.0, offset=offset)


def along(azimuth: float, dip: float, depth: float) -> float:
    """The depth at which a straight hole has gone `depth` metres down."""
    return depth / math.sin(math.radians(-dip))


@pytest.mark.parametrize("offset", [ZERO, UTM], ids=["plain", "utm"])
def test_collar_on_the_crest_of_a_ridge(offset: np.ndarray) -> None:
    v, t = ridge(offset)
    x, y, z = v[CREST]  # the collar is the vertex itself, bit for bit — as a DTM built through the collars has it
    cases = [
        ((90.0, -47.0), [(along(90.0, -47.0, 4.8), -1)]),  # over the face (50.2°) into the air, down on the level
        ((90.0, -50.0), [(along(90.0, -50.0, 4.8), -1)]),
        ((90.0, -51.0), [(0.0, -1)]),  # steeper than the face: into the ground at once
        ((90.0, -60.0), [(0.0, -1)]),
        ((90.0, -30.0), []),  # over the lower level to the end of the surface
        ((270.0, -47.0), [(0.0, -1)]),  # under the level behind the crest
        ((270.0, 10.0), []),  # up into the air
        ((0.0, -47.0), [(0.0, -1)]),  # down in the plane of the crest
        ((0.0, 0.0), []),  # along the crest itself
        ((45.0, -30.0), []),  # askew over the face: its dip along the hole is 40.3°
        ((45.0, -45.0), [(0.0, -1)]),
    ]
    traj = holes([(x, y, z, 12.0)] * len(cases), [(h, 0.0, az, dip) for h, ((az, dip), _w) in enumerate(cases)])
    for flipped in (False, True):  # the orientation of the surface changes the sides, not the crossings
        tri = np.ascontiguousarray(t[:, ::-1]) if flipped else t
        got = crossings(traj, gi.MeshTarget(v, tri))
        for h, (angles, want) in enumerate(cases):
            turned = [(d, -s if flipped else s) for d, s in want]
            assert same(got[h], turned, 1e-9), (angles, flipped, got[h])
            if want and want[0][0] == 0.0:
                assert got[h][0][0] == 0.0  # at the collar exactly


def test_collar_on_an_edge_inside_a_triangle_and_in_a_valley() -> None:
    v, t = ridge(slope=1.25)  # every number of it is exact: a face of 51.3°
    mesh = gi.MeshTarget(v, t)
    places = {
        "crest edge": (2.0, 2.5, 0.0),
        "crest vertex": (2.0, 2.0, 0.0),
        "diagonal of the level": (1.5, 2.5, 0.0),
        "inside a level triangle": (1.25, 2.5, 0.0),
    }
    for name, (x, y, z) in places.items():
        on_crest = x == 2.0
        down = (5.0 - 1.25 * 0.0) / math.sin(math.radians(47.0))
        cases = [
            ((90.0, -47.0), [(down, -1)] if on_crest else [(0.0, -1)]),
            ((90.0, -60.0), [(0.0, -1)]),
            ((270.0, -47.0), [(0.0, -1)]),
            ((90.0, 20.0), []),
            ((300.0, 5.0), []),
        ]
        traj = holes([(x, y, z, 12.0)] * len(cases), [(h, 0.0, az, dip) for h, ((az, dip), _w) in enumerate(cases)])
        got = crossings(traj, mesh)
        for h, (angles, want) in enumerate(cases):
            assert same(got[h], want, 1e-9), (name, angles, got[h])
    # a collar on the face itself: out into the air at 47°, into the ground at 60°
    x, z = 3.25, -1.25 * 1.25
    traj = holes([(x, 1.5, z, 12.0)] * 3, [(0, 0.0, 90.0, -47.0), (1, 0.0, 90.0, -60.0), (2, 0.0, 270.0, -47.0)])
    got = crossings(traj, mesh)
    assert same(got[0], [((5.0 + z) / math.sin(math.radians(47.0)), -1)], 1e-9), got[0]
    assert got[1] == [(0.0, -1)]
    # … against the face, out of it behind the crest: 47° up the face of 51.3° is under it
    h = 1.25 * 1.25 / math.sin(math.radians(47.0))
    assert same(got[2], [(0.0, -1), (h, 1)], 1e-9) and 3.25 - h * math.cos(math.radians(47.0)) < 2.0, got[2]

    # a valley: the face rises from the collar; under it at +40°, over it at +60°
    v, t = ridge(slope=1.25, up=1.0)
    out = 5.0 / math.sin(math.radians(40.0))  # … and out through the upper level
    cases = [((90.0, -47.0), [(0.0, -1)]), ((90.0, 40.0), [(0.0, -1), (out, 1)]), ((90.0, 60.0), []),
             ((270.0, -1.0), [(0.0, -1)]), ((270.0, 1.0), [])]  # fmt: skip
    for x, y in ((2.0, 2.0), (2.0, 2.5)):
        traj = holes([(x, y, 0.0, 12.0)] * len(cases), [(h, 0.0, az, dip) for h, ((az, dip), _w) in enumerate(cases)])
        got = crossings(traj, gi.MeshTarget(v, t))
        for h, (angles, want) in enumerate(cases):
            assert same(got[h], want, 1e-9), ((x, y), angles, got[h])


@pytest.mark.parametrize("method", METHODS)
def test_curved_hole_from_the_crest(method: str) -> None:
    """The first course of the hole is curved (it flattens by 2° in 30 m): what it does at the collar is told
    by its first direction, and the crossing at the collar is at depth 0 exactly."""
    v, t = ridge(UTM)
    x, y, z = v[CREST]
    cases = [(-47.0, False), (-60.0, True), (-49.0, False), (-52.0, True)]
    surveys = [row for h, (dip, _g) in enumerate(cases) for row in ((h, 0.0, 90.0, dip), (h, 30.0, 92.0, dip + 2.0))]
    traj = holes([(x, y, z, 30.0)] * len(cases), surveys, method=method)
    hits = gi.hole_crossings(traj, [gi.MeshTarget(v, t)])
    for h, (dip, into_ground) in enumerate(cases):
        depth = hits.depth[hits.hole == h]
        assert (len(depth) > 0 and depth[0] == 0.0) == into_ground, (method, dip, depth)
        if not into_ground:  # down on the lower level, 4.8 m below the collar
            assert len(depth) == 1 and 4.8 / math.sin(math.radians(-dip)) < depth[0] < 7.5
            assert hits.xyz[hits.hole == h][0, 2] == pytest.approx(z - 4.8, abs=1e-8)


# --- any fan against a scan --------------------------------------------------------------------------------

N = 12  # cells of the relief each way


def relief(seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A surface of N × N cells of 1 m with heights of whole halves of a metre: every vertex is a peak, a pit,
    a ridge, a valley or a saddle. Returns (xyz, tri, heights (N + 1) × (N + 1))."""
    z = np.random.default_rng(seed).integers(-3, 4, (N + 1, N + 1)) / 2.0
    v, t = grid(N, N, lambda x, y: z[y.astype(int), x.astype(int)], step=1.0, offset=ZERO, seed=seed)
    return v, t, z


def height(z: np.ndarray, p: np.ndarray) -> np.ndarray:
    """The height of the relief over the points (the diagonal of a cell runs from its low corner)."""
    i = np.clip(np.floor(p[:, 0]).astype(int), 0, N - 1)
    j = np.clip(np.floor(p[:, 1]).astype(int), 0, N - 1)
    u, w = p[:, 0] - i, p[:, 1] - j
    z00, z10, z11, z01 = z[j, i], z[j, i + 1], z[j + 1, i + 1], z[j + 1, i]
    return np.where(w <= u, z00 + u * (z10 - z00) + w * (z11 - z10), z00 + u * (z11 - z01) + w * (z01 - z00))


def scan(traj: Trajectories, h: int, z: np.ndarray, collar_above: bool, step: float = 1.37e-3) -> list[tuple[float, int]]:
    """[(depth, side)] of the changes of «above the relief» along a hole: a scan and a bisection. A collar on
    the surface is above it (`collar_above`); else the hole starts as its first step finds it."""
    length = float(traj.lengths()[h])
    d = np.append(np.arange(step, length, step), length)

    def above(depths: np.ndarray) -> np.ndarray:
        p = traj.positions(np.full(len(depths), h), depths)
        return np.asarray(p[:, 2] > height(z, p))

    s = above(d)
    d, s = np.concatenate([[0.0], d]), np.concatenate([[True if collar_above else s[0]], s])
    out = []
    for i in np.flatnonzero(s[1:] != s[:-1]):
        a, b = d[i], d[i + 1]
        for _ in range(60):
            m = 0.5 * (a + b)
            if above(np.array([m]))[0] == s[i]:
                a = m
            else:
                b = m
        out.append((0.5 * (a + b), -1 if s[i] else 1))
    return out


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_holes_from_the_vertices_of_a_relief_and_through_them(seed: int) -> None:
    v, t, z = relief(seed)
    mesh = gi.MeshTarget(v, t)
    rng = np.random.default_rng(100 + seed)
    nodes = [(int(i), int(j)) for i, j in rng.integers(4, N - 3, (150, 2))]
    az, dip = rng.uniform(0.0, 360.0, len(nodes)), rng.uniform(-89.0, 89.0, len(nodes))
    start = [(float(i), float(j), float(z[j, i]), 3.0) for i, j in nodes]
    from_vertex = holes(start, [(h, 0.0, az[h], dip[h]) for h in range(len(nodes))])
    got = crossings(from_vertex, mesh)
    at_collar = 0
    for h in range(len(nodes)):
        want = scan(from_vertex, h, z, True)
        assert same(got[h], want), (seed, nodes[h], az[h], dip[h], got[h], want)
        at_collar += bool(want) and want[0][0] < DEPTH
    assert 40 < at_collar < 110  # into the ground and into the air

    # straight through a vertex, from a point to its mirror image: the vertex is the middle of the hole
    k = rng.integers(-16, 17, (len(nodes), 3)) / 8.0
    k[np.abs(k).sum(axis=1) == 0.0] = [0.5, 0.25, 1.0]
    a = np.array([[i, j, z[j, i]] for i, j in nodes]) - k
    length = 2.0 * np.linalg.norm(k, axis=1)
    through = holes(
        [(*a[h], length[h]) for h in range(len(nodes))],
        [(h, length[h], NAN, NAN) for h in range(len(nodes))],
        coords=a + 2.0 * k,
    )
    middle = through.positions(np.arange(len(nodes)), length / 2.0)
    assert np.abs(middle - (a + k)).max() <= 1e-12
    got = crossings(through, mesh)
    crossed = touched = 0
    for h in range(len(nodes)):
        want = scan(through, h, z, False)
        assert same(got[h], want), (seed, nodes[h], k[h].tolist(), got[h], want)
        at_vertex = [d for d, _s in want if abs(d - length[h] / 2.0) < DEPTH]
        crossed += len(at_vertex)
        touched += not at_vertex
    assert crossed > 20 and touched > 20  # through the surface at the vertex, and only touching it there


# --- exact arithmetic --------------------------------------------------------------------------------------

LIFT = (Fraction(1, 10**18), Fraction(1, 10**27), Fraction(1, 10**9))  # east, north, up


def exact(p: np.ndarray) -> list[Fraction]:
    return [Fraction(float(c)) for c in p]


def sub(a: list[Fraction], b: list[Fraction]) -> list[Fraction]:
    return [x - y for x, y in zip(a, b, strict=True)]


def cross(a: list[Fraction], b: list[Fraction]) -> list[Fraction]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def dot(a: list[Fraction], b: list[Fraction]) -> Fraction:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def lifted(points: np.ndarray, depths: np.ndarray, v: np.ndarray, t: np.ndarray) -> list[tuple[float, int]]:
    """[(depth, side)] of the polyline through `points` moved by `LIFT` against the triangles, in rational
    numbers; crossings at one place are one (the sign of the sum of their signs; none when it is zero)."""
    lo, hi = v[t].min(axis=1), v[t].max(axis=1)
    found = []
    for k in range(len(points) - 1):
        a, b = points[k], points[k + 1]
        near = ((np.minimum(a, b) - 1e-3 <= hi) & (np.maximum(a, b) + 1e-3 >= lo)).all(axis=1)
        fa = [x + m for x, m in zip(exact(a), LIFT, strict=True)]
        fb = [x + m for x, m in zip(exact(b), LIFT, strict=True)]
        for i in np.flatnonzero(near):
            p0, p1, p2 = (exact(v[c]) for c in t[i])
            n = cross(sub(p1, p0), sub(p2, p0))
            ga, gb = dot(sub(fa, p0), n), dot(sub(fb, p0), n)
            assert ga != 0 and gb != 0
            if (ga > 0) == (gb > 0):
                continue
            s = ga / (ga - gb)
            x = [p + s * (q - p) for p, q in zip(fa, fb, strict=True)]
            edges = [dot(cross(sub(q, p), sub(x, p)), n) for p, q in ((p0, p1), (p1, p2), (p2, p0))]
            assert all(e != 0 for e in edges)
            if all(e > 0 for e in edges):
                found.append((float(depths[k] + s * Fraction(float(depths[k + 1] - depths[k]))), 1 if ga < 0 else -1))
    found.sort()
    out: list[tuple[float, int]] = []
    i = 0
    while i < len(found):
        j, total = i + 1, found[i][1]
        while j < len(found) and found[j][0] - found[j - 1][0] <= DEPTH:
            total += found[j][1]
            j += 1
        if total:
            out.append((found[i][0], 1 if total > 0 else -1))
        i = j
    return out


def octahedron(centre: tuple[float, float, float], r: float) -> tuple[np.ndarray, np.ndarray]:
    """A closed solid of eight faces, normals outward."""
    c = np.array(centre)
    v = c + r * np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], dtype=float)
    t = [(0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4), (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)]
    return v, np.array(t, dtype=np.int32)


def special(rng: np.random.Generator, v: np.ndarray, t: np.ndarray, kinds: str) -> np.ndarray:
    """A point of a wireframe: `v` a vertex, `e` the middle of an edge, `f` a point inside a triangle."""
    a, b, c = v[t[rng.integers(len(t))][rng.permutation(3)]]
    kind = kinds[rng.integers(len(kinds))]
    return a if kind == "v" else (a + b) / 2.0 if kind == "e" else a / 2.0 + b / 4.0 + c / 4.0


def polylines(seed: int, v: np.ndarray, t: np.ndarray, count: int) -> list[np.ndarray]:
    """Polylines whose vertices are multiples of 1/8 and that meet the wireframe exactly: they start on a
    vertex, an edge or a face of it; bend on a vertex or a face; go through a vertex or the middle of an edge;
    run from vertex to vertex — along edges, across faces, through the solid."""
    rng = np.random.default_rng(seed)
    lo, hi = v.min(axis=0) - 1.0, v.max(axis=0) + 1.0

    def anywhere() -> np.ndarray:
        return np.round(rng.uniform(lo, hi) * 8.0) / 8.0

    out = []
    for n in range(count):
        kind = n % 4
        if kind == 0:
            line = [special(rng, v, t, "vef"), anywhere(), anywhere()]
        elif kind == 1:
            line = [anywhere(), special(rng, v, t, "vf"), anywhere(), special(rng, v, t, "v"), anywhere()]
        elif kind == 2:
            s, k = special(rng, v, t, "ve"), rng.integers(-12, 13, 3) / 8.0
            line = [s - k, s + k, anywhere()]
        else:
            line = [special(rng, v, t, "v") for _ in range(4)]
        points = np.array(line)
        keep = np.concatenate([[True], np.abs(np.diff(points, axis=0)).sum(axis=1) > 0.0])
        if keep.sum() >= 2:
            out.append(points[keep])
    return out


@pytest.mark.parametrize("seed", [11, 12])
def test_polylines_through_vertices_edges_and_faces_against_rational_arithmetic(seed: int) -> None:
    rv, rt, _z = relief(seed)
    ov, ot = octahedron((6.0, 6.0, 0.5), 3.0)
    lines = polylines(seed, rv, rt, 160) + polylines(seed + 50, ov, ot, 160)
    depths = [np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]) for p in lines]
    traj = holes(
        [(*p[0], d[-1]) for p, d in zip(lines, depths, strict=True)],
        [(h, d[k], NAN, NAN) for h, d in enumerate(depths) for k in range(1, len(d))],
        coords=np.concatenate([p[1:] for p in lines]),
    )
    for h, (p, d) in enumerate(zip(lines, depths, strict=True)):  # the trajectory is the polyline
        assert np.abs(traj.positions(np.full(len(d), h), d) - p).max() <= 1e-12
    crossed = at_station = 0
    for v, t, solid in ((rv, rt, None), (ov, ot, "outward")):
        mesh = gi.MeshTarget(v, t, solid=solid)
        got = crossings(traj, mesh)
        hits = gi.hole_crossings(traj, [mesh])
        for h, (p, d) in enumerate(zip(lines, depths, strict=True)):
            want = lifted(p, d, v, t)
            assert same(got[h], want), (seed, solid, h, p.tolist(), got[h], want)
            crossed += len(want)
            at_station += sum(1 for depth, _s in want if np.abs(d - depth).min() < DEPTH)
        if solid:  # into the solid as often as out of it, or the polyline ends inside
            spans = gi.inside_intervals(traj, hits)
            enters, leaves = (int(np.count_nonzero(hits.event == e)) for e in (gi.ENTER, gi.LEAVE))
            assert enters - leaves == int(spans.to_end.sum()) - int(spans.from_collar.sum())
    assert crossed > 300 and at_station > 60  # … of them at the ends of the pieces of the polylines


# --- a graze -----------------------------------------------------------------------------------------------

RADIUS = 100.0 / math.radians(30.0)  # of a course of 100 m that turns by 30°
LOWEST = RADIUS * (1.0 - math.cos(math.radians(15.0)))  # how far below its ends the arc goes
MIDDLE = RADIUS * math.sin(math.radians(15.0))  # … at this distance from its start
K = math.tan(math.radians(30.0))  # the slopes of the two faces of the crease


def crease(under: float, flip: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """A valley across the hole whose bottom line is `under` metres above the lowest point of the arc (`flip`
    −1: the mirror image of it and of the hole — a ridge under the top of an arc)."""
    return grid(8, 2, lambda x, y: flip * (under - LOWEST + K * np.abs(x - 40.0)) + 0.0 * y, step=10.0,
                offset=np.array([MIDDLE - 40.0, -10.0, 0.0]))  # fmt: skip


def arc(flip: float = 1.0) -> Trajectories:
    """East from the origin, 15° down at the collar and 15° up at 100 m (`flip` −1: up, then down)."""
    return holes([(0.0, 0.0, 0.0, 100.0)], [(0, 0.0, 90.0, -15.0 * flip), (0, 100.0, 90.0, 15.0 * flip)])


def test_arc_within_a_millimetre_of_a_crease() -> None:
    traj = arc()
    low = traj.positions(np.array([0]), np.array([50.0]))[0]
    assert low == pytest.approx([MIDDLE, 0.0, -LOWEST], abs=1e-9)
    # the middle piece of the arc is 0.9976 mm above it: over a valley bottom the arc passes under
    pieces = math.ceil(math.sqrt(100.0 * math.radians(30.0) / 8.0 / gi.SAGITTA_M))
    sagitta = RADIUS * (1.0 - math.cos(50.0 / pieces / RADIUS))
    assert pieces % 2 == 1 and 0.9e-3 < sagitta <= gi.SAGITTA_M
    for under in (0.2e-3, 0.5e-3, 0.9e-3):
        assert under < sagitta
        for flip in (1.0, -1.0):
            v, t = crease(under, flip)
            for tri in (t, np.ascontiguousarray(t[:, ::-1])):
                hits = gi.hole_crossings(arc(flip), [gi.MeshTarget(v, tri)])
                assert len(hits) == 0, (under, flip, hits.depth.tolist(), hits.side.tolist())
    # the arc itself comes out of the valley by `over`: at s from the bottom line, s² / 2R + over = K · |s|
    for over in (0.5e-3, 3e-3):
        s = RADIUS * K - math.sqrt((RADIUS * K) ** 2 - 2.0 * RADIUS * over)  # the smaller root
        for flip in (1.0, -1.0):
            v, t = crease(-over, flip)
            hits = gi.hole_crossings(arc(flip), [gi.MeshTarget(v, t)])
            half = RADIUS * math.asin(s / RADIUS)  # the depth from the middle of the arc to x = MIDDLE ± s
            assert hits.depth == pytest.approx([50.0 - half, 50.0 + half], abs=DEPTH), (over, flip)
            assert hits.side.tolist() == [1, -1] and 1.7e-3 * over / 0.5e-3 < hits.depth[1] - hits.depth[0]
            assert np.abs(hits.xyz[:, 2] * flip + LOWEST - over - K * s).max() <= 1e-7  # on the faces
