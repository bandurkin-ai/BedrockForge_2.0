"""Where drillholes cross wireframes (ТЗ 7.15; ADR 0007 §5, ADR 0013 §4 `intersect.py`; BF `test_intersect.py`):
the depths of BF; a circular arc against its formula and curved holes of every desurvey method against a
bisection on `positions()` — to 1e-6 m where the acceptance asks for 1 mm; entering and leaving a closed
solid and the intervals inside it; a hole through a shared edge, a vertex, a station lying on the surface, a
touch of a crease; trenches; thousands of holes over a terrain against the interpolated surface."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.drillholes.desurvey import METHODS, Collars, Surveys, Trajectories, desurvey
from bf_core.geo import intersect as gi
from tests.meshutil import UTM, cube, grid, icosphere, stored

pytestmark = pytest.mark.windows

DEPTH = 1e-6  # metres along the hole; the acceptance of ТЗ 7.15 is 1 mm
NAN = math.nan


def holes(collars, surveys=(), method="minimum_curvature", coords=None) -> Trajectories:  # type: ignore[no-untyped-def]
    """collars [(x, y, z, td)], surveys [(hole, depth, azimuth, dip)], coords — xyz of the stations or None."""
    c = np.array(collars, dtype=float).reshape(-1, 4)
    s = np.array(surveys, dtype=float).reshape(-1, 4)
    xyz = np.full((len(s), 3), NAN) if coords is None else np.array(coords, dtype=float)
    return desurvey(
        Collars(np.arange(len(c)), c[:, 0], c[:, 1], c[:, 2], c[:, 3]),
        Surveys(s[:, 0].astype(np.int64), s[:, 1], s[:, 2], s[:, 3], xyz[:, 0], xyz[:, 1], xyz[:, 2]),
        method=method,
        n_holes=len(c),
    )


def squares() -> tuple[np.ndarray, np.ndarray]:
    """BF: two level squares at z = 70 and z = 40, two triangles each, the shared diagonal under the hole."""
    v = np.array([[-5, -5, 70], [5, -5, 70], [5, 5, 70], [-5, 5, 70],
                  [-5, -5, 40], [5, -5, 40], [5, 5, 40], [-5, 5, 40]], dtype=float)  # fmt: skip
    return v, np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]], dtype=np.int32)


def reference(traj: Trajectories, h: int, state, step: float = 0.01) -> list[tuple[float, bool]]:  # type: ignore[no-untyped-def]
    """[(depth, state before)] of the changes of `state(points)` along a hole: a scan and a bisection."""
    length = float(traj.lengths()[h])
    d = np.unique(np.append(np.arange(0.0, length, step), length))
    s = state(traj.positions(np.full(len(d), h), d))
    out = []
    for i in np.flatnonzero(s[1:] != s[:-1]):
        a, b = d[i], d[i + 1]
        for _ in range(60):
            m = 0.5 * (a + b)
            if state(traj.positions(np.array([h]), np.array([m])))[0] == s[i]:
                a = m
            else:
                b = m
        out.append((0.5 * (a + b), bool(s[i])))
    return out


HARD = [  # surveys of holes that bend: (depth, azimuth, dip)
    [],
    [(0.0, 45.0, -60.0)],
    [(0.0, 10.0, -80.0), (50.0, 40.0, -60.0), (120.0, 90.0, -35.0), (200.0, 150.0, -10.0), (260.0, 200.0, 15.0)],
    [(0.0, 0.0, -89.0), (30.0, 180.0, -20.0), (60.0, 350.0, -70.0), (140.0, 300.0, -50.0)],
    [(0.0, 300.0, -45.0), (80.0, 120.0, 45.0)],  # the direction turns back: no arc fits
    [(0.0, 77.0, -55.0), (12.0, 78.0, -54.5), (24.0, 79.5, -54.0), (150.0, 95.0, -30.0), (151.0, 140.0, -80.0)],
]


def hard_holes(method: str, centre: np.ndarray) -> Trajectories:
    collars, surveys = [], []
    rng = np.random.default_rng(42)
    for h, stations in enumerate(HARD * 3):
        x, y = rng.uniform(-25.0, 25.0, 2)
        collars.append((centre[0] + x, centre[1] + y, centre[2] + rng.uniform(70.0, 110.0), 270.0))
        surveys += [(h, d, (az + 37.0 * (h // len(HARD))) % 360.0, dip) for d, az, dip in stations]
    return holes(collars, surveys, method=method)


# --- BF and the formula ------------------------------------------------------------------------------------


def test_wireframe_crossings_at_their_depths() -> None:
    v, t = squares()
    traj = holes([(0.0, 0.0, 100.0, 100.0)])
    hits = gi.hole_crossings(traj, [gi.MeshTarget(v, t)])
    assert hits.depth.tolist() == [30.0, 60.0]  # the shared diagonal gives one hit, not two
    assert hits.xyz == pytest.approx(np.array([[0.0, 0.0, 70.0], [0.0, 0.0, 40.0]]), abs=1e-12)
    assert hits.hole.tolist() == [0, 0] and hits.mesh.tolist() == [0, 0] and hits.event.tolist() == [gi.CROSS] * 2
    assert hits.side.tolist() == [-1, -1]  # downwards, against the normals that look up
    assert set(hits.triangle.tolist()) <= {0, 1, 2, 3}
    beside = gi.hole_crossings(traj, [gi.MeshTarget(v + [100.0, 0.0, 0.0], t)])
    assert len(beside) == 0 and beside.xyz.shape == (0, 3)


def test_arc_of_minimum_curvature_against_its_formula() -> None:
    """A hole that starts straight down and turns 30° to the east over 100 m is an arc of radius 100 / (π/6) in
    the plane y = const: x = R (1 − cos(d/R)), z = −R sin(d/R). A level plane 50 m below the collar is met at
    d = R asin(50/R); a plane tilted 20° to the east — where the formula says (solved by bisection)."""
    r = 100.0 / (math.pi / 6.0)
    traj = holes([(UTM[0], UTM[1], UTM[2], 100.0)], [(0, 0.0, 90.0, -90.0), (0, 100.0, 90.0, -60.0)])
    level = grid(40, 40, lambda x, y: 0.0 * x - 50.0, step=10.0, offset=UTM - [200.0, 200.0, 0.0])
    hit = gi.hole_crossings(traj, [gi.MeshTarget(*level)])
    assert len(hit) == 1
    d = r * math.asin(50.0 / r)
    assert hit.depth[0] == pytest.approx(d, abs=DEPTH)
    assert hit.xyz[0] - UTM == pytest.approx([r * (1.0 - math.cos(d / r)), 0.0, -50.0], abs=DEPTH)
    k = math.tan(math.radians(20.0))
    tilted = grid(40, 40, lambda x, y: -30.0 - k * (x - 200.0), step=10.0, offset=UTM - [200.0, 200.0, 0.0])
    a, b = 0.0, 100.0  # g(d) = z(d) − plane(x(d)) goes from +30 to below zero
    for _ in range(80):
        m = 0.5 * (a + b)
        if -r * math.sin(m / r) + 30.0 + k * r * (1.0 - math.cos(m / r)) > 0.0:
            a = m
        else:
            b = m
    hit = gi.hole_crossings(traj, [gi.MeshTarget(*tilted)])
    assert len(hit) == 1 and hit.depth[0] == pytest.approx(a, abs=DEPTH)
    assert np.abs(hit.xyz[0] - traj.positions(np.array([0]), hit.depth)[0]).max() == 0.0  # a point of the path


# --- every method against a bisection on positions() -------------------------------------------------------


@pytest.mark.parametrize("method", METHODS)
def test_curved_holes_against_bisection_on_a_plane(method: str) -> None:
    kx, ky = 0.35, -0.2
    offset = UTM - [300.0, 300.0, 0.0]
    v, t = grid(60, 60, lambda x, y: kx * (x - 300.0) + ky * (y - 300.0), step=10.0, offset=offset)
    traj = hard_holes(method, UTM)
    hits = gi.hole_crossings(traj, [gi.MeshTarget(v, t)])

    def above(p: np.ndarray) -> np.ndarray:
        return p[:, 2] - UTM[2] > kx * (p[:, 0] - UTM[0]) + ky * (p[:, 1] - UTM[1])

    total = 0
    for h in range(traj.n_holes):
        ref = reference(traj, h, above)
        got = hits.depth[hits.hole == h]
        assert len(got) == len(ref), (method, h)
        assert got == pytest.approx([d for d, _ in ref], abs=DEPTH), (method, h)
        assert hits.side[hits.hole == h].tolist() == [-1 if was_above else 1 for _, was_above in ref]
        total += len(ref)
    assert total >= 15  # most holes meet the plane, some come back up through it
    assert (hits.event == gi.CROSS).all() and (np.diff(hits.hole) >= 0).all()
    on_path = traj.positions(hits.hole, hits.depth)
    assert np.abs(hits.xyz - on_path).max() == 0.0


@pytest.mark.parametrize("method", METHODS)
def test_curved_holes_enter_and_leave_a_solid(method: str) -> None:
    v, t = cube(12, 120.0, offset=UTM - [60.0, 60.0, 120.0])
    lo, hi = v.min(axis=0), v.max(axis=0)
    traj = hard_holes(method, UTM)

    def inside(p: np.ndarray) -> np.ndarray:
        return ((p >= lo) & (p <= hi)).all(axis=1)

    for solid, tri in (("outward", t), ("inward", t[:, ::-1])):
        mesh = gi.MeshTarget(v, np.ascontiguousarray(tri), solid=solid)
        hits = gi.hole_crossings(traj, [mesh])
        spans = gi.inside_intervals(traj, hits)
        for h in range(traj.n_holes):
            ref = reference(traj, h, inside)
            mine = hits.hole == h
            assert hits.depth[mine] == pytest.approx([d for d, _ in ref], abs=DEPTH), (method, solid, h)
            assert hits.event[mine].tolist() == [gi.LEAVE if was_in else gi.ENTER for _, was_in in ref]
            # the intervals inside: from every entry to the next exit, or to the end of the hole
            depths = [d for d, _ in ref]
            length = float(traj.lengths()[h])
            want = [(depths[i], depths[i + 1] if i + 1 < len(depths) else length) for i in range(0, len(depths), 2)]
            got = spans.hole == h
            assert spans.from_m[got] == pytest.approx([a for a, _ in want], abs=DEPTH)
            assert spans.to_m[got] == pytest.approx([b for _, b in want], abs=DEPTH)
            assert spans.to_end[got].tolist() == [i + 1 >= len(depths) for i in range(0, len(depths), 2)]
            assert not spans.from_collar[got].any()
        assert len(hits) >= 2 * len(HARD)
    plain = gi.hole_crossings(traj, [gi.MeshTarget(v, t)])  # not said to be a solid: crossings only
    assert (plain.event == gi.CROSS).all() and len(gi.inside_intervals(traj, plain)) == 0


# --- solids ------------------------------------------------------------------------------------------------


def test_intervals_of_holes_that_start_or_end_inside() -> None:
    v, t = cube(5, 100.0, offset=[0.0, 0.0, 0.0])
    traj = holes(
        [
            (50.0, 50.0, 130.0, 200.0),  # through: 30 … 130
            (20.0, 30.0, 60.0, 100.0),  # the collar inside: 0 … 60
            (70.0, 20.0, 150.0, 90.0),  # the end inside: 50 … 90
            (40.0, 40.0, 80.0, 30.0),  # all of it inside: no crossing at all
            (500.0, 50.0, 130.0, 200.0),  # beside
        ]
    )
    mesh = gi.MeshTarget.from_stats(v, t, {"closed": True, "orientation": "outward"})
    hits = gi.hole_crossings(traj, [mesh])
    assert hits.hole.tolist() == [0, 0, 1, 2]
    assert hits.depth.tolist() == [30.0, 130.0, 60.0, 50.0]
    assert hits.event.tolist() == [gi.ENTER, gi.LEAVE, gi.LEAVE, gi.ENTER]
    spans = gi.inside_intervals(traj, hits)
    assert spans.hole.tolist() == [0, 1, 2] and spans.mesh.tolist() == [0, 0, 0]
    assert spans.from_m.tolist() == [30.0, 0.0, 50.0] and spans.to_m.tolist() == [130.0, 60.0, 90.0]
    assert spans.from_collar.tolist() == [False, True, False] and spans.to_end.tolist() == [False, False, True]
    ends = np.array([[50.0, 50.0, 100.0], [20.0, 30.0, 60.0], [70.0, 20.0, 100.0]])
    assert spans.xyz_from == pytest.approx(ends, abs=1e-12)
    assert spans.xyz_to == pytest.approx(
        np.array([[50.0, 50.0, 0.0], [20.0, 30.0, 0.0], [70.0, 20.0, 60.0]]), abs=1e-12
    )
    # a hole wholly inside a solid has no crossing to tell it by: no interval (said in the docstring)
    assert 3 not in spans.hole.tolist()
    opened = gi.MeshTarget.from_stats(v, t, {"closed": False, "orientation": None})
    assert (gi.hole_crossings(traj, [opened]).event == gi.CROSS).all()


def test_shared_edge_vertex_station_on_the_surface_and_a_touch() -> None:
    v, t = cube(1, 100.0, offset=[0.0, 0.0, 0.0], seed=None)  # 12 triangles: every face has one diagonal
    solid = gi.MeshTarget(v, t, solid="outward")
    # straight down through the centre of the top: on the diagonal shared by its two triangles
    through = holes([(50.0, 50.0, 130.0, 200.0)])
    hits = gi.hole_crossings(through, [solid])
    assert hits.depth.tolist() == [30.0, 130.0] and hits.event.tolist() == [gi.ENTER, gi.LEAVE]
    # through two opposite corners of the cube: a vertex of three faces on the way in and on the way out
    d = 100.0 * math.sqrt(3.0)
    diagonal = holes([(-10.0, -10.0, -10.0, 0.0)], [(0, 1.2 * d, NAN, NAN)], coords=[(110.0, 110.0, 110.0)])
    hits = gi.hole_crossings(diagonal, [solid])
    assert hits.depth == pytest.approx([0.1 * d, 1.1 * d], abs=1e-9)
    assert hits.event.tolist() == [gi.ENTER, gi.LEAVE]
    # a survey station exactly on the top face: the two courses that meet there give one crossing
    station = holes([(30.0, 60.0, 130.0, 200.0)], [(0, 0.0, 0.0, -90.0), (0, 30.0, 0.0, -90.0), (0, 80.0, 0.0, -90.0)])
    hits = gi.hole_crossings(station, [solid])
    assert hits.depth.tolist() == [30.0, 130.0] and hits.event.tolist() == [gi.ENTER, gi.LEAVE]
    # a line that only touches a vertical edge of the cube: into one face and out of the other at one point
    length = 20.0 * math.sqrt(2.0)
    touch = holes([(-10.0, 10.0, 50.0, 0.0)], [(0, length, NAN, NAN)], coords=[(10.0, -10.0, 50.0)])
    hits = gi.hole_crossings(touch, [solid])
    assert hits.depth == pytest.approx([length / 2.0], abs=1e-9) and hits.event.tolist() == [gi.CROSS]
    assert hits.side.tolist() == [0] and len(gi.inside_intervals(touch, hits)) == 0
    # a hole that stops exactly on a face it came to from outside has not crossed it
    stops = holes([(50.0, 50.0, 130.0, 30.0)])
    assert len(gi.hole_crossings(stops, [solid])) == 0


def test_trench_through_two_walls() -> None:
    """A trench is a polyline through its surveyed points (stations with coordinates, ТЗ 7.35 Д-16)."""
    walls_v = np.array([[5, -5, -5], [5, 5, -5], [5, 5, 5], [5, -5, 5],
                        [5, 5, -5], [15, 5, -5], [15, 5, 5], [5, 5, 5]], dtype=float) + UTM  # fmt: skip
    walls_t = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]], dtype=np.int32)
    traj = holes(
        [(UTM[0], UTM[1], UTM[2], 20.0)],
        [(0, 10.0, NAN, NAN), (0, 20.0, NAN, NAN)],
        coords=[UTM + [10.0, 0.0, 0.0], UTM + [10.0, 10.0, 0.0]],
    )
    hits = gi.hole_crossings(traj, [gi.MeshTarget(walls_v, walls_t)])
    assert hits.depth == pytest.approx([5.0, 15.0], abs=1e-8)
    assert hits.xyz - UTM == pytest.approx(np.array([[5.0, 0.0, 0.0], [10.0, 5.0, 0.0]]), abs=1e-8)


# --- many holes, several wireframes ------------------------------------------------------------------------


def test_thousands_of_holes_over_a_terrain_against_the_interpolated_surface() -> None:
    def relief(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return 20.0 * np.sin(x / 37.0) * np.cos(y / 53.0)

    nx = ny = 300
    step = 2.0
    v, t = grid(nx, ny, relief, step=step)  # 180 000 shuffled triangles
    rng = np.random.default_rng(7)
    n = 3000
    x, y = rng.uniform(1.0, nx * step - 1.0, n), rng.uniform(1.0, ny * step - 1.0, n)
    traj = holes(np.column_stack([UTM[0] + x, UTM[1] + y, np.full(n, UTM[2] + 60.0), np.full(n, 150.0)]))
    px, py = (traj.xyz[traj.offsets[:-1], 0] - UTM[0]) / step, (traj.xyz[traj.offsets[:-1], 1] - UTM[1]) / step
    i, j = np.floor(px).astype(int), np.floor(py).astype(int)
    u, w = px - i, py - j

    def z(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return relief(a * step, b * step)

    z00, z10, z11, z01 = z(i, j), z(i + 1, j), z(i + 1, j + 1), z(i, j + 1)
    surface = np.where(w <= u, z00 + u * (z10 - z00) + w * (z11 - z10), z00 + u * (z11 - z01) + w * (z01 - z00))
    mesh = gi.MeshTarget(v, t)
    hits = gi.hole_crossings(traj, [mesh])
    assert hits.hole.tolist() == list(range(n))  # one crossing a hole, in the order of the holes
    assert np.abs(hits.depth - (60.0 - surface)).max() <= 1e-8
    assert (hits.side == -1).all()
    # `triangle` is the index in the array as given (shuffled here): the hit is on that triangle
    a, b, c = (v[t[hits.triangle, k]] for k in range(3))
    normal = np.cross(b - a, c - a)
    off_plane = np.einsum("ij,ij->i", hits.xyz - a, normal) / np.linalg.norm(normal, axis=1)
    assert np.abs(off_plane).max() <= 1e-8
    bary = np.einsum("ij,ij->i", np.cross(b - hits.xyz, c - hits.xyz), normal) / np.einsum("ij,ij->i", normal, normal)
    assert bary.min() >= -1e-9 and bary.max() <= 1.0 + 1e-9
    # the same wireframe as the store keeps it (in spatial order already) gives the same crossings
    sv, st, _parts = stored(v, t)
    kept = gi.hole_crossings(traj, [gi.MeshTarget(sv, st, ordered=True)])
    assert kept.depth.tobytes() == hits.depth.tobytes() and kept.xyz.tobytes() == hits.xyz.tobytes()
    some = gi.hole_crossings(traj, [mesh], holes=np.array([2999, 5, 17]))
    assert some.hole.tolist() == [5, 17, 2999] and some.depth.tolist() == hits.depth[[5, 17, 2999]].tolist()


def test_several_wireframes_holes_without_a_trajectory_and_the_order() -> None:
    ball_v, ball_t = icosphere(3, 30.0, centre=[0.0, 0.0, 0.0])
    level_v, level_t = grid(8, 8, lambda x, y: 0.0 * x + 10.0, step=20.0, offset=[-80.0, -80.0, 0.0])
    meshes = [gi.MeshTarget(level_v, level_t), gi.MeshTarget(ball_v, ball_t, solid="outward")]
    collars = Collars(np.array([0, 1, 3]), np.array([1.0, NAN, 3.0]), np.array([2.0, 5.0, -4.0]),
                      np.array([100.0, 100.0, 100.0]), np.array([200.0, 200.0, 200.0]))  # fmt: skip
    traj = desurvey(collars, None, n_holes=4)  # hole 1 has no X, hole 2 has no collar at all
    hits = gi.hole_crossings(traj, meshes)
    assert hits.hole.tolist() == [0, 0, 0, 3, 3, 3] and hits.mesh.tolist() == [1, 0, 1, 1, 0, 1]
    assert hits.event.tolist() == [gi.ENTER, gi.CROSS, gi.LEAVE] * 2
    assert hits.depth[[1, 4]].tolist() == [90.0, 90.0] and (np.diff(hits.depth[:3]) > 0).all()
    spans = gi.inside_intervals(traj, hits)
    assert spans.hole.tolist() == [0, 3] and spans.mesh.tolist() == [1, 1]
    chord = 2.0 * math.sqrt(30.0**2 - 5.0)  # hole 0 passes 5^0.5 m from the centre of the ball
    assert spans.to_m[0] - spans.from_m[0] == pytest.approx(chord, abs=0.3)  # the ball is a polyhedron
    assert len(gi.hole_crossings(traj, [])) == 0 and len(gi.hole_crossings(traj, meshes, holes=[1, 2])) == 0
    empty = gi.MeshTarget(ball_v, np.zeros((0, 3), dtype=np.int32))
    assert len(gi.hole_crossings(traj, [empty])) == 0
    with pytest.raises(ValueError):
        gi.hole_crossings(traj, meshes, holes=[4])
    with pytest.raises(ValueError):
        gi.MeshTarget(ball_v, ball_t + len(ball_v))
    with pytest.raises(ValueError):
        gi.MeshTarget(ball_v, ball_t, solid="closed")


def test_tick_reports_and_cancels() -> None:
    v, t = grid(20, 20, lambda x, y: 0.0 * x, step=10.0)
    n = 20_000
    traj = holes(np.column_stack([np.full(n, UTM[0] + 50.0), np.full(n, UTM[1] + 50.0), np.full(n, UTM[2] + 10.0),
                                  np.full(n, 20.0)]))  # fmt: skip
    mesh = gi.MeshTarget(v, t)
    seen: list[float] = []
    hits = gi.hole_crossings(traj, [mesh, mesh], tick=lambda f, m: seen.append(f))
    assert len(hits) == 2 * n and len(seen) >= 4 and seen[0] == 0.0 and seen[-1] == 1.0 and seen == sorted(seen)

    class Cancelled(Exception):
        pass

    calls = []

    def stop(fraction: float | None, message: str | None) -> None:
        calls.append(fraction)
        if len(calls) > 1:
            raise Cancelled

    with pytest.raises(Cancelled):
        gi.hole_crossings(traj, [mesh], tick=stop)
