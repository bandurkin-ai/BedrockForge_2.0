"""Orientation of intervals by wireframes and surfaces (ТЗ 8.5.2 «Каркас или поверхность»; ADR 0021 §5,
решения 7, 8; `intercepts/orient_mesh.py`) — the kernel `mesh_orientation` on synthetic wireframes.

Т8.5-13: a plate, holes at different angles, the source «каркас» — TT = the thickness (1e−6 m): one interval
from the entry to the exit, samples of 1 m cut at the faces, a curved hole; the plate closed, as stored, as
the two surfaces of its sides. Then a wedge, the hanging wall and the footwall as two surfaces, a hole inside a
body, farther than d_max; the crossings and the nearest triangles against references in numpy on UTM
coordinates; a trench that goes down through a surface and comes back; r_n over all the wireframes; missing
values (never a zero). The owner's wireframes, the wireframes one at a time, threads, progress and the
source in the chain of `orient` — `test_tt_real.py`.

Tolerances: TT — 1e−6 m (the ТЗ; measured 6e−11 m on straight holes); normals — 1e−10 where the plane is known
(the corners of its triangles are rounded to float64 at UTM coordinates), 1e−9 against the full search;
distances — 1e−9 m."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterator
from typing import Any

import numpy as np
import pytest

from bf_core.composites.intervals import CompositeError
from bf_core.drillholes.desurvey import Collars, Surveys, Trajectories, desurvey
from bf_core.geo.intersect import MeshTarget, hole_crossings
from bf_core.geo.nearest import nearest_triangles
from bf_core.intercepts import directions as dr
from bf_core.intercepts import formulas as fm
from bf_core.intercepts import orient_mesh as om
from bf_core.intercepts import spec as sp
from tests.meshutil import UTM, cube, grid, icosphere, stored
from tests.test_geo_nearest import ref_area_normals, ref_distances

TT_TOL = 1e-6  # metres: Т8.5-13
EXACT = 1e-10  # a normal whose plane is known: its corners are rounded to 1e−9 m (UTM), tens of metres apart
SEARCH = 1e-9  # against the full search
NAN = math.nan
Mesh = tuple[np.ndarray, np.ndarray]


# --- geometry ----------------------------------------------------------------------------------------------


def frame(alpha: float, dipdir: float) -> np.ndarray:
    """Rows u, v, n of a right-handed frame of a plane: u — level, along the strike; v — down the dip; n — the
    upper normal."""
    n = fm.plane_normal(alpha, dipdir)
    a = math.radians(dipdir)
    u = np.array([math.cos(a), -math.sin(a), 0.0])
    return np.array([u, np.cross(n, u), n])


def plate(alpha: float, dipdir: float, thickness: float, centre: Any, size: float = 300.0, cells: int = 6) -> Mesh:
    """A closed plate `size` × `size` × `thickness`, normals outward, shuffled triangles: its middle plane dips
    `alpha` to `dipdir` through `centre`."""
    unit, tri = cube(cells, 1.0, offset=np.zeros(3), seed=3)
    return np.asarray(centre) + ((unit - 0.5) * np.array([size, size, thickness])) @ frame(alpha, dipdir), tri


def sides(v: np.ndarray, t: np.ndarray, n: np.ndarray, flip: bool = False) -> list[Mesh]:
    """The hanging wall and the footwall of a plate as two open surfaces (`flip` — the footwall is given with
    its corners the other way round)."""
    normal = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    along = (normal @ n) / np.linalg.norm(normal, axis=1)
    top, bottom = t[along > 0.99], t[along < -0.99]
    return [(v, top), (v, bottom[:, ::-1] if flip else bottom)]


def straight(points: Any, s0: Any, theta: Any, azimuth: Any, td: Any) -> tuple[Trajectories, np.ndarray]:
    """Straight holes that are at `points` at the depths `s0`; (trajectories, the directions down the holes)."""
    points = np.atleast_2d(np.asarray(points, dtype=np.float64))
    k = len(points)
    theta, azimuth, s0, td = (np.broadcast_to(np.asarray(x, dtype=np.float64), (k,)) for x in (theta, azimuth, s0, td))
    d = fm.hole_direction(theta, azimuth)
    collar = points - s0[:, None] * d
    traj = desurvey(Collars(np.arange(k), collar[:, 0], collar[:, 1], collar[:, 2], td, azimuth, theta - 90.0))
    return traj, d


def samples(k: int, td: float, step: float, cuts: Any = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(hole, FROM, TO) of intervals of `step` metres down k holes of `td` metres; `cuts[h]` — depths the
    intervals of the hole h are cut at as well."""
    hole, frm, to = [], [], []
    for h in range(k):
        edges = np.arange(0.0, td + 0.5 * step, step)
        if cuts is not None:
            edges = np.unique(np.concatenate([edges, np.asarray(cuts[h], dtype=np.float64)]))
        hole.append(np.full(len(edges) - 1, h, dtype=np.int64))
        frm.append(edges[:-1])
        to.append(edges[1:])
    return np.concatenate(hole), np.concatenate(frm), np.concatenate(to)


def unit_normals(v: np.ndarray, t: np.ndarray) -> np.ndarray:
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    return n / np.linalg.norm(n, axis=1)[:, None]  # type: ignore[no-any-return]


def merged(meshes: list[Mesh]) -> Mesh:
    """Several wireframes as one."""
    first = np.cumsum([0] + [len(v) for v, _t in meshes])
    return np.concatenate([v for v, _t in meshes]), np.concatenate([t + first[k] for k, (_v, t) in enumerate(meshes)])


def targets(meshes: list[Mesh]) -> list[MeshTarget]:
    return [MeshTarget(v, t) for v, t in meshes]


def digest(o: om.MeshOrientation) -> str:
    h = hashlib.sha256()
    for a in (o.normal, o.way, o.count, o.distance):
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def nothing(o: om.MeshOrientation, rows: Any) -> bool:
    """The rows have no orientation: NaN and the code «none», never a zero."""
    return bool(np.isnan(o.normal[rows]).all() and (o.way[rows] == om.WAY_NONE).all()
                and np.isnan(o.distance[rows]).all())  # fmt: skip


# --- references --------------------------------------------------------------------------------------------


def ref_crossings(v: np.ndarray, t: np.ndarray, a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, int]:
    """(Σ of the unit normals of the triangles the segment a → b goes through, each turned against the segment;
    their number). Not the kernel: the point of the plane on the line, then its barycentric coordinates;
    float64 relative to the centre of the mesh."""
    centre = 0.5 * (v.min(axis=0) + v.max(axis=0))
    p0, p1, p2 = (v[t[:, k]] - centre for k in range(3))
    a, d = a - centre, b - a
    e1, e2 = p1 - p0, p2 - p0
    n = np.cross(e1, e2)
    with np.errstate(divide="ignore", invalid="ignore"):
        s = ((p0 - a) * n).sum(axis=1) / (n @ d)
        w = a + s[:, None] * d - p0
        d00, d01, d11 = (e1 * e1).sum(axis=1), (e1 * e2).sum(axis=1), (e2 * e2).sum(axis=1)
        d20, d21 = (w * e1).sum(axis=1), (w * e2).sum(axis=1)
        den = d00 * d11 - d01 * d01
        bu, bv = (d11 * d20 - d01 * d21) / den, (d00 * d21 - d01 * d20) / den
        hit = (s >= 0.0) & (s <= 1.0) & (bu >= 0.0) & (bv >= 0.0) & (bu + bv <= 1.0)
    unit = n[hit] / np.linalg.norm(n[hit], axis=1)[:, None]
    return (-np.sign(unit @ d)[:, None] * unit).sum(axis=0), int(hit.sum())


def check(o: om.MeshOrientation, traj: Trajectories, hole: np.ndarray, frm: np.ndarray, to: np.ndarray,
          meshes: list[Mesh], d_max: float) -> dict[int, int]:  # fmt: skip
    """`o` of straight holes against the references: the crossings of every interval with every triangle and
    the full search of the nearest one. Returns the number of rows by `way`."""
    v, t = merged(meshes)
    normals = unit_normals(v, t)
    start, end = traj.positions(hole, frm - om.DEPTH_TOL_M), traj.positions(hole, to + om.DEPTH_TOL_M)
    ref = ref_distances(traj.positions(hole, 0.5 * (frm + to)), v, t)
    least = ref.min(axis=1)
    for i in range(len(hole)):
        total, count = ref_crossings(v, t, start[i], end[i])
        assert o.count[i] == count, i
        if count:
            assert o.way[i] == om.WAY_CROSSING, i
            assert np.allclose(o.normal[i], fm.upper_normal(total), rtol=0.0, atol=SEARCH), i
            assert np.isnan(o.distance[i])
        elif least[i] <= d_max:
            assert o.way[i] == om.WAY_NEAREST, i
            assert o.distance[i] == pytest.approx(least[i], abs=SEARCH)
            tied = np.flatnonzero(ref[i] <= least[i] + 1e-7)  # an edge or a vertex: one of its triangles
            assert np.abs(fm.upper_normal(normals[tied]) - o.normal[i]).max(axis=1).min() <= SEARCH, i
        else:
            assert nothing(o, i), i
    assert np.allclose(np.linalg.norm(o.normal[o.way > 0], axis=1), 1.0, rtol=0.0, atol=1e-14)
    assert (o.normal[o.way > 0][:, 2] >= 0.0).all()
    return {int(k): int(c) for k, c in zip(*np.unique(o.way, return_counts=True), strict=True)}


# --- Т8.5-13: a plate --------------------------------------------------------------------------------------

ALPHA, DIPDIR, THICK = 55.0, 130.0, 7.3
CENTRE = UTM + np.array([40.0, -25.0, -80.0])
ANGLES = [(0.0, 0.0), (20.0, 310.0), (35.0, 310.0), (50.0, 300.0), (60.0, 40.0), (75.0, 290.0), (90.0, 310.0),
          (115.0, 320.0), (150.0, 130.0), (40.0, 220.0), (10.0, 130.0), (65.0, 355.0)]  # fmt: skip


def plate_holes() -> tuple[Trajectories, np.ndarray, np.ndarray, np.ndarray]:
    """Holes of ANGLES (zenith, azimuth) through the middle part of the plate: (trajectories, the depths of
    the entry and of the exit — by the planes of the faces, the directions)."""
    rng = np.random.default_rng(13)
    k = len(ANGLES)
    u, v, n = frame(ALPHA, DIPDIR)
    through = CENTRE + rng.uniform(-20.0, 20.0, (k, 1)) * u + rng.uniform(-20.0, 20.0, (k, 1)) * v
    s0 = 70.0 + rng.uniform(0.0, 20.0, k)
    theta, azimuth = np.array(ANGLES).T
    traj, d = straight(through, s0, theta, azimuth, 150.0)
    chord = THICK / np.abs(d @ n)
    assert chord.max() < 60.0 and (d @ n).min() < -0.9 and (d @ n).max() > 0.5  # from above and from below
    return traj, s0 - 0.5 * chord, s0 + 0.5 * chord, d


def plate_meshes(kind: str) -> list[MeshTarget]:
    v, t = plate(ALPHA, DIPDIR, THICK, CENTRE)
    if kind == "closed":
        return [MeshTarget(v, t, solid="outward")]
    if kind == "stored":
        return [MeshTarget(*stored(v, t)[:2], ordered=True)]
    return targets(sides(v, t, fm.plane_normal(ALPHA, DIPDIR), flip=kind == "sides, one turned over"))


@pytest.mark.parametrize("kind", ["closed", "stored", "sides", "sides, one turned over"])
def test_t8_5_13_plate_true_thickness_is_its_thickness(kind: str) -> None:
    traj, s_in, s_out, d = plate_holes()
    hole = np.arange(len(s_in))
    n = fm.plane_normal(ALPHA, DIPDIR)
    o = om.mesh_orientation(traj, hole, s_in, s_out, plate_meshes(kind))
    assert (o.way == om.WAY_CROSSING).all() and (o.count == 2).all()
    assert np.allclose(o.normal, n, rtol=0.0, atol=EXACT)
    alpha, dipdir = fm.plane_angles(o.normal)
    assert np.allclose(alpha, ALPHA, rtol=0.0, atol=1e-8) and np.allclose(dipdir, DIPDIR, rtol=0.0, atol=1e-8)
    tt = dr.thickness(traj, hole, s_in, s_out, o.normal).tt
    assert np.abs(tt - THICK).max() <= TT_TOL  # Т8.5-13
    assert np.abs(tt - THICK).max() <= 1e-9  # what the kernels really give on straight holes
    assert np.abs(tt - (s_out - s_in) * np.abs((d * o.normal).sum(axis=1))).max() <= 1e-12 * 60.0  # l·|n·d|


@pytest.mark.parametrize("kind", ["closed", "sides"])
def test_t8_5_13_samples_of_a_metre_add_up_to_the_thickness(kind: str) -> None:
    """Samples of 1 m cut at the faces: the first and the last one inside the plate take the face the hole
    crosses, those between them — the nearest face; outside — the nearest face within d_max, nothing beyond."""
    traj, s_in, s_out, d = plate_holes()
    k = len(s_in)
    hole, frm, to = samples(k, 150.0, 1.0, cuts=np.column_stack([s_in, s_out]))
    n = fm.plane_normal(ALPHA, DIPDIR)
    o = om.mesh_orientation(traj, hole, frm, to, plate_meshes(kind), d_max=50.0)
    tt = dr.thickness(traj, hole, frm, to, o.normal).tt
    inside = (frm >= s_in[hole] - 1e-9) & (to <= s_out[hole] + 1e-9)
    for h in range(k):
        rows = np.flatnonzero(inside & (hole == h))
        assert len(rows) >= 2
        assert abs(tt[rows].sum() - THICK) <= TT_TOL  # Т8.5-13
        assert (o.way[rows[[0, -1]]] == om.WAY_CROSSING).all() and (o.count[rows[[0, -1]]] == 1).all()
        assert (o.way[rows[1:-1]] == om.WAY_NEAREST).all() and (o.count[rows[1:-1]] == 0).all()
    found = o.way != om.WAY_NONE
    assert np.allclose(o.normal[found], n, rtol=0.0, atol=EXACT)
    off = np.abs((traj.positions(hole, 0.5 * (frm + to)) - CENTRE) @ n) - 0.5 * THICK  # from the nearer face
    near = o.way == om.WAY_NEAREST
    assert np.allclose(o.distance[near], np.abs(off[near]), rtol=0.0, atol=SEARCH) and (off[near] <= 50.0).all()
    lost = ~found
    assert lost.any() and (off[lost] > 50.0).all() and nothing(o, lost) and o.report["none"] == int(lost.sum())
    assert np.isnan(tt[lost]).all() and not np.isnan(tt[found]).any()


def test_t8_5_13_curved_hole() -> None:
    """A hole that turns 2° in 60 m: the sum along it between the two faces (inside one course) is the
    thickness."""
    v, t = plate(ALPHA, DIPDIR, THICK, CENTRE)
    start = CENTRE - 95.0 * fm.hole_direction(22.0, 300.0)
    traj = desurvey(Collars([0], [start[0]], [start[1]], [start[2]], [200.0]),
                    Surveys([0, 0, 0, 0], [0.0, 60.0, 120.0, 180.0], [300.0, 301.5, 303.0, 304.5],
                            [-68.0, -66.5, -65.0, -63.5]))  # fmt: skip
    target = MeshTarget(v, t)
    c = hole_crossings(traj, [target])
    assert len(c) == 2 and 60.0 < c.depth[0] < c.depth[1] < 120.0
    s_in, s_out = float(c.depth[0]), float(c.depth[1])
    o = om.mesh_orientation(traj, [0], [s_in], [s_out], [target])
    assert o.way[0] == om.WAY_CROSSING
    assert np.allclose(o.normal[0], fm.plane_normal(ALPHA, DIPDIR), rtol=0.0, atol=EXACT)
    assert abs(dr.thickness(traj, [0], [s_in], [s_out], o.normal).tt[0] - THICK) <= TT_TOL
    hole, frm, to = samples(1, 200.0, 1.0, cuts=[[s_in, s_out]])
    o = om.mesh_orientation(traj, hole, frm, to, [target])
    inside = (frm >= s_in - 1e-9) & (to <= s_out + 1e-9)
    assert abs(dr.thickness(traj, hole, frm, to, o.normal).tt[inside].sum() - THICK) <= TT_TOL


# --- a wedge; the hanging wall and the footwall ------------------------------------------------------------

WEDGE_DIR, TOP_DIP, BOTTOM_DIP = 75.0, 30.0, 50.0


def wedge() -> tuple[np.ndarray, np.ndarray, Callable[[float, float, float], np.ndarray]]:
    """A closed wedge, normals outward: its top dips 30° and its bottom 50° to the azimuth 75° from a level
    edge; 120 m down the dip direction, 200 m along the edge. And the point (x from the edge down the dip
    direction, y along the edge, z above it)."""
    a = math.radians(WEDGE_DIR)
    axes = np.array([[math.sin(a), math.cos(a), 0.0], [-math.cos(a), math.sin(a), 0.0], [0.0, 0.0, 1.0]])
    at = UTM + np.array([-30.0, 60.0, -40.0])
    x, y = 120.0, 200.0
    top, bottom = -x * math.tan(math.radians(TOP_DIP)), -x * math.tan(math.radians(BOTTOM_DIP))
    local = np.array([[0, 0, 0], [0, y, 0], [x, 0, top], [x, y, top], [x, 0, bottom], [x, y, bottom]], dtype=float)
    tri = np.array([[0, 2, 3], [0, 3, 1], [0, 5, 4], [0, 1, 5], [2, 4, 5], [2, 5, 3], [0, 4, 2], [1, 3, 5]])
    return at + local @ axes, tri.astype(np.int32), lambda px, py, pz: at + np.array([px, py, pz]) @ axes


def test_wedge_entry_and_exit_give_the_middle_plane() -> None:
    v, t, point = wedge()
    n_top, n_bottom = fm.plane_normal(TOP_DIP, WEDGE_DIR), fm.plane_normal(BOTTOM_DIP, WEDGE_DIR)
    assert np.allclose(unit_normals(v, t)[[0, 2]], [n_top, -n_bottom], rtol=0.0, atol=EXACT)  # outward
    # a vertical hole 60 m down the dip direction from the edge: the top at 60·tg 30°, the bottom at 60·tg 50°
    traj, _d = straight([point(60.0, 81.0, 20.0), point(60.0, 131.0, 20.0)], 0.0, [0.0, 25.0], [0.0, 255.0], 150.0)
    z_top, z_bottom = 20.0 + 60.0 * math.tan(math.radians(30.0)), 20.0 + 60.0 * math.tan(math.radians(50.0))
    hole = np.array([0, 0, 0, 0, 0, 1])
    frm = np.array([z_top - 3.0, z_top - 3.0, z_bottom - 2.0, z_top + 2.0, z_bottom - 9.0, 0.0])
    to = np.array([z_bottom + 3.0, z_top + 1.0, z_bottom + 2.0, z_top + 6.0, z_bottom - 5.0, 150.0])
    middle = fm.upper_normal(n_top + n_bottom)  # norm(n_in − n_out) by the outward normals
    found = []
    for meshes in ([MeshTarget(v, t, solid="outward")], targets([(v, t[:2]), (v, t[2:4])]),
                   targets([(v, t[2:4, ::-1]), (v, t[:2])])):  # fmt: skip
        o = om.mesh_orientation(traj, hole, frm, to, meshes)
        assert o.way.tolist() == [om.WAY_CROSSING] * 3 + [om.WAY_NEAREST] * 2 + [om.WAY_CROSSING]
        assert o.count.tolist() == [2, 1, 1, 0, 0, 2]
        assert np.allclose(o.normal[[0, 5]], middle, rtol=0.0, atol=EXACT)  # whatever the angle of the hole
        alpha, dipdir = fm.plane_angles(o.normal[0])
        assert alpha == pytest.approx(40.0, abs=1e-8) and dipdir == pytest.approx(WEDGE_DIR, abs=1e-8)
        assert np.allclose(o.normal[[1, 3]], n_top, rtol=0.0, atol=EXACT)  # the entry alone; nearer to the top
        assert np.allclose(o.normal[[2, 4]], n_bottom, rtol=0.0, atol=EXACT)  # the exit alone; nearer to the bottom
        assert o.distance[3] == pytest.approx(4.0 * math.cos(math.radians(30.0)), abs=SEARCH)
        assert o.distance[4] == pytest.approx(7.0 * math.cos(math.radians(50.0)), abs=SEARCH)
        assert np.isnan(o.distance[[0, 1, 2, 5]]).all()
        found.append(o.normal)
    assert np.array_equal(found[0], found[1])  # the same triangles, however they are put into wireframes
    assert np.allclose(found[0], found[2], rtol=0.0, atol=1e-15)  # and whichever way their corners go


def test_hanging_wall_and_footwall_as_two_surfaces() -> None:
    """Two bumpy surfaces 9 m apart, the footwall with its normals down: an interval through both takes the
    middle plane of the two triangles; the same as one wireframe of both, in any order of the list."""
    hang = grid(40, 40, lambda x, y: 0.2 * x + 1.5 * np.sin(0.3 * y), step=5.0, seed=1)
    foot_v, foot_t = grid(40, 40, lambda x, y: 0.15 * x + 1.2 * np.cos(0.25 * y) - 9.0, step=5.0, seed=2)
    meshes = [hang, (foot_v, foot_t[:, ::-1])]
    rng = np.random.default_rng(5)
    k = 60
    through = UTM + np.column_stack([rng.uniform(40.0, 160.0, (k, 2)), np.full(k, 40.0)])
    traj, _d = straight(through, 0.0, rng.uniform(0.0, 50.0, k), rng.uniform(0.0, 360.0, k), 90.0)
    hole, frm, to = samples(k, 90.0, 30.0)
    o = om.mesh_orientation(traj, hole, frm, to, targets(meshes), d_max=25.0)
    ways = check(o, traj, hole, frm, to, meshes, 25.0)
    assert (o.count == 2).sum() >= 20 and ways[om.WAY_NEAREST] >= 20
    for other in (targets([merged(meshes)]), targets(meshes[::-1])):
        same = om.mesh_orientation(traj, hole, frm, to, other, d_max=25.0)
        assert np.array_equal(same.way, o.way) and np.array_equal(same.count, o.count)
        assert np.allclose(same.normal, o.normal, rtol=0.0, atol=1e-15, equal_nan=True)


# --- inside a body; farther than d_max ---------------------------------------------------------------------


def test_hole_inside_a_body_takes_the_nearest_triangle_within_d_max() -> None:
    v, t = icosphere(3, 60.0, UTM)
    traj, _d = straight(UTM + np.array([-10.0, 5.0, 30.0]), 0.0, 25.0, 140.0, 50.0)
    hole, frm, to = samples(1, 50.0, 2.0)
    assert len(hole_crossings(traj, [MeshTarget(v, t)])) == 0  # the hole never leaves the body
    o = om.mesh_orientation(traj, hole, frm, to, [MeshTarget(v, t, solid="outward")], d_max=60.0)
    assert check(o, traj, hole, frm, to, [(v, t)], 60.0) == {om.WAY_NEAREST: 25}
    assert o.report == {"wireframes": 1, "triangles": 1280, "crossings": 0, "asked": 25, "crossing": 0,
                        "nearest": 25, "none": 0, "cancelled": 0, "no_path": 0} and o.journal == []  # fmt: skip
    near = om.mesh_orientation(traj, hole, frm, to, [MeshTarget(v, t)], d_max=45.0)
    ways = check(near, traj, hole, frm, to, [(v, t)], 45.0)
    assert ways[om.WAY_NONE] >= 5 and ways[om.WAY_NEAREST] >= 5
    lost = near.way == om.WAY_NONE
    assert nothing(near, lost) and (near.count[lost] == 0).all() and (o.distance[lost] > 45.0).all()
    assert np.array_equal(near.normal[~lost], o.normal[~lost])
    assert len(near.journal) == 1 and "d_max = 45 м" in near.journal[0] and near.report["none"] == int(lost.sum())
    only = om.mesh_orientation(traj, hole, frm, to, [MeshTarget(v, t)], d_max=0.0)  # d_max = 0: crossings only
    assert nothing(only, slice(None)) and "d_max = 0" in only.journal[0]


def test_crossings_and_nearest_triangles_against_references_on_utm() -> None:
    """A surface, a closed body under it and a thin plate, straight holes at any angle, intervals of 3 m."""
    meshes = [grid(30, 30, lambda x, y: 4.0 * np.sin(0.2 * x) * np.cos(0.15 * y) + 0.1 * x, step=4.0, seed=4),
              icosphere(2, 25.0, UTM + np.array([60.0, 60.0, -45.0])),
              plate(35.0, 250.0, 1.2, UTM + np.array([50.0, 70.0, -20.0]), size=90.0, cells=3)]  # fmt: skip
    rng = np.random.default_rng(21)
    k = 40
    collar = UTM + np.column_stack([rng.uniform(10.0, 110.0, (k, 2)), np.full(k, 25.0)])
    traj, _d = straight(collar, 0.0, rng.uniform(0.0, 70.0, k), rng.uniform(0.0, 360.0, k), 120.0)
    hole, frm, to = samples(k, 120.0, 3.0)
    o = om.mesh_orientation(traj, hole, frm, to, targets(meshes), d_max=50.0)
    ways = check(o, traj, hole, frm, to, meshes, 50.0)
    assert ways[om.WAY_CROSSING] >= 40 and ways[om.WAY_NEAREST] >= 500 and (o.count >= 2).sum() >= 4, ways
    assert o.report["crossing"] == ways[om.WAY_CROSSING] and o.report["none"] == ways.get(om.WAY_NONE, 0)
    # a smaller d_max only takes the far ones away; as the store keeps the wireframes — the triangles in
    # spatial order, not sorted again — the same crossings and distances (of equally near triangles — any)
    kept = [MeshTarget(*stored(v, t)[:2], ordered=True) for v, t in meshes]
    near = om.mesh_orientation(traj, hole, frm, to, kept, d_max=12.0)
    ways = check(near, traj, hole, frm, to, meshes, 12.0)
    assert ways[om.WAY_NONE] >= 300 and ways[om.WAY_NEAREST] >= 200, ways
    stays = (o.way == om.WAY_CROSSING) | (o.distance <= 12.0)
    assert np.array_equal(near.way[stays], o.way[stays]) and nothing(near, ~stays)
    assert np.array_equal(near.count, o.count)
    assert np.array_equal(near.distance[stays], o.distance[stays], equal_nan=True)
    assert np.array_equal(near.normal[o.way == om.WAY_CROSSING], o.normal[o.way == om.WAY_CROSSING])


def test_crossing_on_a_boundary_belongs_to_both_intervals() -> None:
    v, t = grid(4, 4, lambda x, y: 0.0 * x, step=5.0, offset=np.array([500_000.0, 6_000_000.0, 100.0]))
    traj, _d = straight(np.array([500_007.0, 6_000_009.0, 110.0]), 0.0, 0.0, 0.0, 30.0)
    frm = np.array([8.0, 10.0, 8.0, 10.0 + 2e-6, 10.0 - 1e-6, 12.0])
    to = np.array([10.0, 12.0, 10.0 - 2e-6, 12.0, 10.0 + 1e-6, 14.0])
    o = om.mesh_orientation(traj, np.zeros(6, dtype=np.int64), frm, to, [MeshTarget(v, t)])
    assert o.count.tolist() == [1, 1, 0, 0, 1, 0]
    assert o.way.tolist() == [om.WAY_CROSSING] * 2 + [om.WAY_NEAREST] * 2 + [om.WAY_CROSSING, om.WAY_NEAREST]
    assert np.array_equal(o.normal, np.tile([0.0, 0.0, 1.0], (6, 1)))
    assert o.distance[[2, 3, 5]] == pytest.approx([1.0 + 1e-6, 1.0 + 1e-6, 3.0], abs=SEARCH)


def test_trench_that_comes_back_through_a_surface() -> None:
    """Stations with coordinates: down through a level surface and back up inside one interval. The two
    normals, each turned against the hole, cancel: the nearest triangle gives the plane."""
    v, t = grid(8, 8, lambda x, y: 0.0 * x, step=5.0, offset=np.array([-10.0, -20.0, 0.0]))
    traj = desurvey(Collars([0], [0.0], [0.0], [5.0], [20.0]),
                    Surveys([0, 0, 0], [0.0, 10.0, 20.0], [NAN] * 3, [NAN] * 3, x=[0.0, 6.0, 12.0], y=[0.0] * 3,
                            z=[5.0, -3.0, 5.0]))  # fmt: skip
    assert np.allclose(hole_crossings(traj, [MeshTarget(v, t)]).depth, [6.25, 13.75], rtol=0.0, atol=1e-9)
    frm, to = np.array([5.0, 0.0, 12.0, 7.0]), np.array([15.0, 8.0, 20.0, 13.0])
    o = om.mesh_orientation(traj, np.zeros(4, dtype=np.int64), frm, to, [MeshTarget(v, t)])
    assert o.count.tolist() == [2, 1, 1, 0]
    assert o.way.tolist() == [om.WAY_NEAREST, om.WAY_CROSSING, om.WAY_CROSSING, om.WAY_NEAREST]
    assert np.array_equal(o.normal, np.tile([0.0, 0.0, 1.0], (4, 1)))
    assert o.distance[0] == pytest.approx(3.0, abs=SEARCH) and o.report["cancelled"] == 1
    assert len(o.journal) == 1 and "туда и обратно" in o.journal[0]
    only = om.mesh_orientation(traj, np.zeros(4, dtype=np.int64), frm, to, [MeshTarget(v, t)], d_max=0.0)
    assert only.way.tolist() == [om.WAY_NONE, om.WAY_CROSSING, om.WAY_CROSSING, om.WAY_NONE]
    assert nothing(only, [0, 3]) and only.count.tolist() == [2, 1, 1, 0] and len(only.journal) == 2


# --- r_n: the normals averaged by area, over all the wireframes ---------------------------------------------


def test_r_n_of_a_thin_body_is_its_middle_plane() -> None:
    """A plate 0,4 m thick: within r_n of the point found there are triangles of both faces — they add up."""
    v, t = plate(35.0, 250.0, 0.4, CENTRE, size=200.0, cells=12)
    u, w, n = frame(35.0, 250.0)
    k = 8
    rng = np.random.default_rng(2)
    through = CENTRE + rng.uniform(-30.0, 30.0, (k, 1)) * u + rng.uniform(-30.0, 30.0, (k, 1)) * w
    traj, _d = straight(through, 40.0, rng.uniform(0.0, 40.0, k), rng.uniform(0.0, 360.0, k), 60.0)
    hole, frm, to = samples(k, 30.0, 2.0)  # above the plate: none of them reaches it
    o = om.mesh_orientation(traj, hole, frm, to, [MeshTarget(v, t)], d_max=50.0, r_n=15.0)
    assert (o.way == om.WAY_NEAREST).all() and np.allclose(o.normal, n, rtol=0.0, atol=EXACT)
    point = traj.positions(hole, 0.5 * (frm + to)) - o.distance[:, None] * n  # the point found, on the top
    ref = np.tile(n, (len(hole), 1))
    both, top = ref_area_normals(point, ref, v, t, 15.0), ref_area_normals(point, ref, *sides(v, t, n)[0], 15.0)
    assert np.allclose(np.linalg.norm(both, axis=1), 2.0 * np.linalg.norm(top, axis=1), rtol=1e-9)


def test_r_n_takes_the_triangles_of_all_the_wireframes() -> None:
    """The hanging wall and the footwall of a wedge as two wireframes, r_n reaching both: the mean by area of
    both — what one wireframe of the same triangles gives, not the plane of the nearer surface alone."""
    hang = grid(24, 24, lambda x, y: -0.25 * x + 0.8 * np.sin(0.4 * y), step=2.5, seed=6)
    foot_v, foot_t = grid(24, 24, lambda x, y: -0.6 * x + 0.5 * np.cos(0.3 * x) - 2.0, step=2.5, seed=7)
    meshes = [hang, (foot_v, foot_t[:, ::-1])]
    rng = np.random.default_rng(9)
    k = 40
    through = UTM + np.column_stack([rng.uniform(15.0, 45.0, (k, 2)), rng.uniform(3.0, 9.0, k)])  # above both
    traj, _d = straight(through, 1.0, rng.uniform(60.0, 90.0, k), rng.uniform(0.0, 360.0, k), 2.0)
    hole, frm, to = np.arange(k), np.zeros(k), np.full(k, 2.0)
    r_n = 12.0
    both = om.mesh_orientation(traj, hole, frm, to, targets(meshes), d_max=50.0, r_n=r_n)
    plain = om.mesh_orientation(traj, hole, frm, to, targets(meshes), d_max=50.0)
    assert np.array_equal(both.way, plain.way)
    assert np.array_equal(both.distance, plain.distance, equal_nan=True)  # r_n does not move the point found
    v, t = merged(meshes)
    ref = ref_distances(traj.positions(hole, 0.5 * (frm + to)), v, t)
    clear = np.partition(ref, 1, axis=1)[:, 1] - ref.min(axis=1) > 1e-6  # one nearest triangle, not an edge
    rows = np.flatnonzero((plain.way == om.WAY_NEAREST) & clear)
    assert len(rows) >= 25
    middle = traj.positions(hole, 0.5 * (frm + to))[rows]
    nearest = unit_normals(v, t)[ref[rows].argmin(axis=1)]
    assert np.allclose(plain.normal[rows], fm.upper_normal(nearest), rtol=0.0, atol=EXACT)
    point = nearest_triangles(MeshTarget(v, t), middle, 50.0).point
    assert np.allclose(np.linalg.norm(point - middle, axis=1), plain.distance[rows], rtol=0.0, atol=SEARCH)
    mean = fm.upper_normal(ref_area_normals(point, nearest, v, t, r_n))
    assert np.allclose(both.normal[rows], mean, rtol=0.0, atol=SEARCH)
    one = om.mesh_orientation(traj, hole, frm, to, targets([(v, t)]), d_max=50.0, r_n=r_n)
    assert np.allclose(one.normal, both.normal, rtol=0.0, atol=EXACT, equal_nan=True)
    alone = fm.upper_normal(ref_area_normals(point, nearest, *meshes[0], r_n))  # the hanging wall alone
    assert (np.abs(alone - both.normal[rows]).max(axis=1) > 1e-3).sum() >= 15  # where r_n reaches the footwall


# --- missing values, errors --------------------------------------------------------------------------------


def test_missing_values_give_nothing_and_never_a_zero() -> None:
    v, t = grid(4, 4, lambda x, y: 0.0 * x, step=5.0, offset=np.array([0.0, 0.0, -10.0]))
    traj = desurvey(Collars([0, 2], [7.0, 9.0], [9.0, 7.0], [0.0, 0.0], [30.0, 30.0]), n_holes=3)
    hole = np.array([0, -1, 1, 0, 0, 0, 0, 2, 5, 0])
    frm = np.array([8.0, 8.0, 8.0, NAN, 8.0, 12.0, 12.0, 8.0, 8.0, 8.0])
    to = np.array([12.0, 12.0, 12.0, 12.0, NAN, 12.0, 8.0, 12.0, 12.0, 12.0])
    todo = np.array([True] * 9 + [False])
    o = om.mesh_orientation(traj, hole, frm, to, [MeshTarget(v, t)], todo=todo)
    good = np.array([0, 7])
    assert o.way.tolist() == [om.WAY_CROSSING, 0, 0, 0, 0, 0, 0, om.WAY_CROSSING, 0, 0]
    assert np.array_equal(o.normal[good], [[0.0, 0.0, 1.0]] * 2) and o.count.tolist() == [1, 0, 0, 0, 0, 0, 0, 1, 0, 0]
    rest = np.setdiff1d(np.arange(10), good)
    assert nothing(o, rest) and not (o.normal == 0.0).all(axis=1).any()
    assert o.report["asked"] == 2 and o.report["no_path"] == 3 and o.report["none"] == 0
    assert o.normal.dtype == np.float64 and o.way.dtype == np.uint8 and o.count.dtype == np.int32
    assert todo.tolist() == [True] * 9 + [False]  # not changed
    tt = dr.thickness(traj, hole, frm, to, o.normal).tt
    assert np.isnan(tt[rest]).all() and np.allclose(tt[good], 4.0, rtol=0.0, atol=1e-12)

    class Never:
        def __len__(self) -> int:
            return 1

        def __iter__(self) -> Iterator[MeshTarget]:
            raise AssertionError("nobody asked: the wireframes are not read")

    for empty in (om.mesh_orientation(traj, hole, frm, to, Never(), todo=np.zeros(10, dtype=bool)),
                  om.mesh_orientation(traj, hole, frm, to, [])):  # fmt: skip
        assert nothing(empty, slice(None)) and (empty.count == 0).all()
    none = om.mesh_orientation(traj, np.zeros(0, dtype=np.int64), np.zeros(0), np.zeros(0), Never())
    assert none.normal.shape == (0, 3) and none.way.shape == (0,) and none.report["asked"] == 0
    ints = om.mesh_orientation(traj, [0, 2], [8, 8], [12, 12], [MeshTarget(v, t)])  # lists and whole numbers
    assert ints.way.tolist() == [om.WAY_CROSSING] * 2


def test_bad_parameters_and_the_ranges_of_the_request() -> None:
    v, t = grid(2, 2, lambda x, y: 0.0 * x)
    traj, _d = straight(UTM + np.array([1.0, 1.0, 5.0]), 0.0, 0.0, 0.0, 10.0)
    args: tuple[Any, ...] = (traj, [0], [0.0], [10.0], [MeshTarget(v, t)])
    for name, value in (("d_max", -1.0), ("d_max", 1000.5), ("d_max", NAN), ("r_n", 200.5), ("r_n", -0.1)):
        with pytest.raises(CompositeError) as e:
            om.mesh_orientation(*args, **{name: value})
        assert e.value.code == "bad_param" and e.value.param == name and e.value.hint
        assert e.value.message.startswith(sp.RANGES[name][3])  # the label of the request
    assert sp.RANGES["d_max"][:3] == (*om.D_MAX_RANGE, om.D_MAX_DEFAULT)  # the ranges of ТЗ 8.5.4: the same
    assert sp.RANGES["r_n"][:3] == (*om.R_N_RANGE, om.R_N_DEFAULT)
    om.mesh_orientation(*args, d_max=1000.0, r_n=200.0)
    with pytest.raises(TypeError):
        om.mesh_orientation(traj, [0.0], [0.0], [10.0], [MeshTarget(v, t)])
    with pytest.raises(ValueError, match="one length"):
        om.mesh_orientation(traj, [0, 0], [0.0], [10.0], [MeshTarget(v, t)])
    with pytest.raises(ValueError, match="todo"):
        om.mesh_orientation(*args, todo=[True, False])
    with pytest.raises(TypeError, match="MeshTarget"):
        om.mesh_orientation(traj, [0], [0.0], [10.0], [(v, t)])  # type: ignore[list-item]


def test_warm_compiles() -> None:
    om.warm()
