"""Wireframe editing (part D): checks, repair of every defect kind, simplification, smoothing,
merge/split and deviations. Synthetic meshes with known answers; every tolerance is stated in
the test that uses it. No database."""

import time

import numpy as np
import pytest

from app.geo import repair as R
from app.geo.repair import (
    max_deviation,
    merge_meshes,
    mesh_checks,
    repair,
    self_intersecting_triangles,
    simplify_mesh,
    smooth_mesh,
    split_components,
)
from bench.synthetic import subdivided_cube, wavy_surface

# Project coordinates the size of UTM ones: nothing may depend on the numbers being small.
UTM = np.array([512345.678, 6789012.345, 250.0])
SIDE = 60.0
VOLUME = SIDE**3


def cube(n: int = 6) -> tuple[np.ndarray, np.ndarray]:
    return subdivided_cube(n, size=SIDE, origin=UTM)


def icosphere(level: int, radius: float = 100.0, centre: np.ndarray = UTM) -> tuple[np.ndarray, np.ndarray]:
    """A sphere of 20·4^level triangles, outward windings, vertices exactly on the sphere."""
    g = (1 + 5**0.5) / 2
    v = np.array([[-1, g, 0], [1, g, 0], [-1, -g, 0], [1, -g, 0], [0, -1, g], [0, 1, g], [0, -1, -g], [0, 1, -g],
                  [g, 0, -1], [g, 0, 1], [-g, 0, -1], [-g, 0, 1]], dtype=float)
    t = np.array([[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2],
                  [10, 7, 6], [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5],
                  [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]], dtype=np.int64)
    for _ in range(level):
        n = len(v)
        e = np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
        uniq, inv = np.unique(e[:, 0] * n + e[:, 1], return_inverse=True)
        v = np.concatenate([v, (v[uniq // n] + v[uniq % n]) / 2])
        ab, bc, ca = (inv.reshape(3, -1) + n)
        a, b, c = t.T
        t = np.concatenate([np.column_stack(x) for x in ((a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca))])
    v = v / np.linalg.norm(v, axis=1)[:, None] * radius
    return v + centre, t.astype(np.int32)


def sphere_volume(v: np.ndarray, t: np.ndarray, centre: np.ndarray = UTM) -> float:
    a, b, c = (v[t[:, i]] - centre for i in range(3))
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0


def assert_sound(checks: dict, volume: float | None = None, rel: float = 1e-9) -> None:
    """Every check of stage 10 passes; the volume matches within `rel`."""
    assert checks["closed"] and checks["open_edges"] == 0 and checks["nonmanifold_edges"] == 0
    assert checks["degenerate"] == 0 and checks["duplicate"] == 0 and checks["orientation_conflicts"] == 0
    assert checks["orientation"] == "outward" and checks["self_intersections"] == 0
    if volume is not None:
        assert checks["volume"] == pytest.approx(volume, rel=rel)


def rows(a: np.ndarray) -> set[bytes]:
    return {r.tobytes() for r in np.ascontiguousarray(a)}


def same_windings(a: np.ndarray, b: np.ndarray) -> bool:
    """The same triangles in the same order with the same normals (any rotation of the corners)."""
    def canonical(t):
        first = np.argmin(t, axis=1)[:, None]
        return np.take_along_axis(t, (first + np.arange(3)) % 3, axis=1)
    return a.shape == b.shape and np.array_equal(canonical(a), canonical(b))


def step(report: dict, name: str) -> dict:
    return next(s for s in report["steps"] if s["step"] == name)


def only(**on) -> dict:
    """Arguments of `repair` with every fix off except the named ones."""
    base = {"weld": None, "drop_degenerate": False, "drop_duplicate": False, "orient": False, "fill_holes": False,
            "fix_nonmanifold": False}
    base.update(on)
    return base


# ---- defects, one at a time


def soup(v, t):
    return v[t.reshape(-1)], np.arange(3 * len(t), dtype=np.int32).reshape(-1, 3)


def with_hole(v, t):
    """A block of triangles of the top face removed (a planar hole with a ragged outline)."""
    c = v[t].mean(axis=1) - UTM
    gone = (c[:, 2] > SIDE - 1e-6) & (np.abs(c[:, 0] - SIDE / 2) < 16) & (np.abs(c[:, 1] - SIDE / 2) < 16)
    assert gone.sum() >= 6
    return v, np.ascontiguousarray(t[~gone])


def with_fin(v, t):
    """A triangle standing on an edge of the mesh, its third vertex outside the solid."""
    a, b = t[0, 0], t[0, 1]
    x = (v[a] + v[b]) / 2 + np.array([0.0, 0.0, -7.0])
    return np.concatenate([v, [x]]), np.concatenate([t, [[a, b, len(v)]]]).astype(np.int32)


def with_degenerate(v, t):
    """A repeated index, a collinear triple of mesh vertices, and a zero-area triangle built into
    the surface (a vertex inserted on an edge of one of its two triangles only)."""
    line = np.flatnonzero((np.abs(v[:, 1] - UTM[1]) < 1e-9) & (np.abs(v[:, 2] - UTM[2]) < 1e-9))
    line = line[np.argsort(v[line, 0])][:3]
    extra = [[t[5, 0], t[5, 0], t[5, 1]], line.tolist()]
    i = 40
    a, b, p = t[i]
    c = len(v)
    v = np.concatenate([v, [(v[a] + v[b]) / 2]])
    t = np.concatenate([np.delete(t, i, axis=0), [[a, c, p], [c, b, p], [a, b, c]], extra]).astype(np.int32)
    return v, t


def with_duplicates(v, t):
    return v, np.concatenate([t, t[[3, 9]], t[[20, 21]][:, [1, 2, 0]], t[[30]][:, ::-1]]).astype(np.int32)


def with_flips(v, t):
    t = t.copy()
    idx = np.random.default_rng(3).choice(len(t), 25, replace=False)
    t[idx] = t[idx][:, ::-1]
    return v, t


def test_checks_of_a_sound_solid_and_of_every_defect():
    v, t = cube()
    c = mesh_checks(v, t)
    assert_sound(c, VOLUME, rel=1e-12)
    assert c["triangles"] == len(t) and c["vertices"] == len(v) and c["components"] == 1
    assert c["area"] == pytest.approx(6 * SIDE**2, rel=1e-12)
    assert mesh_checks(v, t, intersections=False)["self_intersections"] is None

    assert mesh_checks(*soup(v, t))["open_edges"] == 3 * len(t)
    assert mesh_checks(*soup(v, t))["components"] == len(t)
    assert mesh_checks(*with_hole(v, t))["open_edges"] > 0
    assert mesh_checks(*with_fin(v, t))["nonmanifold_edges"] == 1
    assert mesh_checks(*with_degenerate(v, t))["degenerate"] == 3
    assert mesh_checks(*with_duplicates(v, t))["duplicate"] == 5
    assert mesh_checks(*with_flips(v, t))["orientation_conflicts"] > 0
    inward = mesh_checks(v, t[:, ::-1])
    assert inward["orientation"] == "inward" and inward["volume"] == pytest.approx(VOLUME, rel=1e-12)
    assert mesh_checks(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32))["triangles"] == 0


def test_weld_restores_a_triangle_soup_exactly():
    v, t = cube()
    sv, st = soup(v, t)
    out_v, out_t, report = repair(sv, st)
    assert_sound(report["after"], VOLUME, rel=1e-12)
    assert report["before"]["open_edges"] == 3 * len(t) and report["tolerance"] == 1e-6
    assert step(report, "weld")["changed"] == len(sv) - len(v) and step(report, "weld")["title"]
    # No averaging: the vertices are exactly the source ones.
    assert rows(out_v) == rows(v) and len(out_t) == len(t)


def test_weld_merges_within_the_tolerance_and_keeps_the_first_vertex():
    v, t = cube()
    # Copies shifted by 0.4 of the tolerance in every direction (also across the rounding grid).
    rng = np.random.default_rng(5)
    shifted = v + rng.choice([-4e-7, 4e-7], size=v.shape)
    half = len(t) // 2
    both = np.concatenate([v, shifted])
    tt = t.copy()
    tt[half:] += len(v)
    out_v, _out_t, report = repair(both, tt)
    assert_sound(report["after"], VOLUME, rel=1e-12)
    assert rows(out_v) == rows(v)
    # A tolerance below the shift leaves the two halves apart.
    assert not repair(both, tt, weld=1e-8, fill_holes=False)[2]["after"]["closed"]
    # weld=0 merges only exactly equal vertices.
    assert repair(*soup(v, t), weld=0.0)[2]["after"]["closed"]


def test_degenerate_triangles_are_dropped_or_flipped_away():
    v, t = cube()
    dv, dt = with_degenerate(v, t)
    out_v, out_t, report = repair(dv, dt)
    assert_sound(report["after"], VOLUME, rel=1e-12)
    s = step(report, "degenerate")
    # The two loose ones are dropped; the built-in one is removed by flipping its longest edge.
    assert (s["dropped"], s["flipped"], s["changed"]) == (2, 1, 3)
    assert len(out_t) == len(t) + 2 and len(out_v) == len(v) + 1


def test_duplicate_triangles_are_dropped():
    v, t = cube()
    _out_v, out_t, report = repair(*with_duplicates(v, t))
    assert_sound(report["after"], VOLUME, rel=1e-12)
    assert step(report, "duplicate")["changed"] == 5 and np.array_equal(out_t, t)


def test_flipped_triangles_and_an_inward_mesh_are_oriented():
    v, t = cube()
    _, out_t, report = repair(*with_flips(v, t))
    assert_sound(report["after"], VOLUME, rel=1e-12)
    assert step(report, "orient")["changed"] == 25 and same_windings(out_t, t)

    _out_v, out_t, report = repair(v, t[:, ::-1])
    assert_sound(report["after"], VOLUME, rel=1e-12)
    assert step(report, "orient")["changed"] == len(t) and step(report, "orient")["turned_outward"] == 1


def test_a_planar_hole_is_closed_exactly():
    v, t = cube()
    hv, ht = with_hole(v, t)
    out_v, out_t, report = repair(hv, ht)
    # The patch lies in the plane of the face: the volume is the cube's to rounding (rel 1e-9).
    assert_sound(report["after"], VOLUME, rel=1e-9)
    assert report["after"]["area"] == pytest.approx(6 * SIDE**2, rel=1e-9)
    s = step(report, "holes")
    assert s["filled"] >= 1 and s["changed"] == len(out_t) - len(ht) and s["added_vertices"] == 0
    assert rows(out_v) <= rows(v)


def test_a_fin_on_an_edge_is_removed():
    v, t = cube()
    out_v, out_t, report = repair(*with_fin(v, t))
    assert_sound(report["after"], VOLUME, rel=1e-12)
    s = step(report, "nonmanifold")
    assert s["removed_triangles"] == 1 and s["remaining_halfedges"] == 0
    assert np.array_equal(out_t, t) and out_v.tobytes() == v.tobytes()


def test_every_defect_together_with_utm_coordinates():
    v, t = cube(10)
    dv, dt = with_hole(v, t)
    dv, dt = with_degenerate(dv, dt)
    dv, dt = with_duplicates(dv, dt)
    dv, dt = with_flips(dv, dt)
    dv, dt = with_fin(dv, dt)
    dv, dt = soup(dv, dt[:, ::-1])  # inward as a whole and unwelded
    before = mesh_checks(dv, dt)
    assert not before["closed"] and before["degenerate"] and before["open_edges"] == 3 * len(dt)

    calls = []
    out_v, out_t, report = repair(dv, dt, progress=calls.append)
    # Every fix is exact here (planar hole, vertices untouched): rel 1e-9.
    assert_sound(report["after"], VOLUME, rel=1e-9)
    assert report["after"] == mesh_checks(out_v, out_t) and report["before"] == before
    assert [s["step"] for s in report["steps"]] == ["weld", "duplicate", "degenerate", "nonmanifold", "holes", "orient"]
    assert all(s["title"] and s["changed"] > 0 for s in report["steps"])
    assert calls == sorted(calls) and calls[-1] == 1.0
    # Kept vertices are bit-identical to source ones; the fin's tip is gone.
    assert rows(out_v) <= rows(dv) and out_v.dtype == np.float64 and out_t.dtype == np.int32
    assert out_v[:, 0].min() > 512000 and out_v[:, 1].min() > 6789000


@pytest.mark.parametrize(("defect", "switch", "check"), [
    (soup, {"weld": 1e-6}, "open_edges"),
    (with_degenerate, {"drop_degenerate": True}, "degenerate"),
    (with_duplicates, {"drop_duplicate": True}, "duplicate"),
    (with_flips, {"orient": True}, "orientation_conflicts"),
    (with_hole, {"fill_holes": True}, "open_edges"),
    (with_fin, {"fix_nonmanifold": True}, "nonmanifold_edges"),
])
def test_every_fix_is_switched_on_separately(defect, switch, check):
    dv, dt = defect(*cube())
    assert mesh_checks(dv, dt)[check] > 0
    # All fixes off: nothing changes. Only this one on: its check passes.
    _, same_t, report = repair(dv, dt, **only())
    assert report["steps"] == [] and report["after"][check] == report["before"][check] and len(same_t) == len(dt)
    _, _, report = repair(dv, dt, **only(**switch))
    assert len(report["steps"]) == 1 and report["steps"][0]["changed"] > 0
    assert report["after"][check] == 0


# ---- holes


def test_an_open_surface_keeps_its_outer_boundary():
    n = 40
    v, t = wavy_surface(n)
    c = v[t].mean(axis=1)
    gone = (np.abs(c[:, 0] - v[:, 0].mean()) < 300) & (np.abs(c[:, 1] - v[:, 1].mean()) < 300)
    out_v, out_t, report = repair(v, np.ascontiguousarray(t[~gone]))
    s = step(report, "holes")
    # The inner hole is closed, the outline of the terrain (4·n edges) is not touched.
    assert s["filled"] >= 1 and s["skipped_outer"] == 1 and "оставлена открытой" in s["note"]
    assert report["after"]["open_edges"] == 4 * n and not report["after"]["closed"]
    assert report["after"]["orientation_conflicts"] == 0 and report["after"]["self_intersections"] == 0
    # The windings of an open surface stay as they were.
    assert step(report, "orient")["changed"] == 0
    assert np.array_equal(out_v[out_t[:np.count_nonzero(~gone)]], v[t[~gone]])

    # An untouched terrain is returned as it is.
    out_v, out_t, report = repair(v, t)
    assert out_v.tobytes() == v.tobytes() and np.array_equal(out_t, t) and report["after"]["open_edges"] == 4 * n

    # Explicit limits: a small limit leaves the hole, and the outline can be closed on request.
    _, _, report = repair(v, np.ascontiguousarray(t[~gone]), max_hole_edges=5)
    assert step(report, "holes")["skipped_large"] >= 1 and report["after"]["open_edges"] > 4 * n
    _, _, report = repair(v, t, keep_outer_boundary=False)
    assert report["after"]["open_edges"] == 0


def test_a_curved_hole_in_a_sphere_is_closed():
    v, t = icosphere(4)
    exact = sphere_volume(v, t)
    c = v[t].mean(axis=1) - UTM
    gone = c[:, 0] > 80.0  # a cap of the radius-100 sphere: the loop is not planar (ragged)
    out_v, _out_t, report = repair(v, np.ascontiguousarray(t[~gone]))
    assert_sound(report["after"])
    # The cap is replaced by a flat-ish patch: up to 6 % of the volume is lost, no more.
    assert 0.94 * exact < report["after"]["volume"] < exact
    assert step(report, "holes")["added_vertices"] == 0 and rows(out_v) <= rows(v)


def test_ear_clipping_of_a_loop_that_no_plane_projects_well():
    # A crown: the loop winds twice around the axis, so every projection crosses itself.
    k = 24
    a = np.linspace(0, 4 * np.pi, k, endpoint=False)
    pts = np.column_stack([np.cos(a), np.sin(a), np.linspace(0, 3, k)])
    tri, done = R._fill_polygon(pts)
    assert done and len(tri) == k - 2
    # A triangulation of the polygon: every polygon edge once and forwards, inner edges twice.
    edges = np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
    forward = {(i, (i + 1) % k) for i in range(k)}
    directed = [tuple(e) for e in edges.tolist()]
    assert forward <= set(directed) and len(set(directed)) == len(directed)
    assert all((b, a_) in set(directed) for a_, b in directed if (a_, b) not in forward)


def test_two_holes_touching_at_a_vertex_are_closed_separately():
    v, t = cube(8)
    c = v[t].mean(axis=1) - UTM
    top = np.flatnonzero(c[:, 2] > SIDE - 1e-6)
    cell = SIDE / 8
    # Two squares of the top face meeting diagonally at one vertex.
    in_a = (c[top, 0] > 2 * cell) & (c[top, 0] < 3 * cell) & (c[top, 1] > 2 * cell) & (c[top, 1] < 3 * cell)
    in_b = (c[top, 0] > 3 * cell) & (c[top, 0] < 4 * cell) & (c[top, 1] > 3 * cell) & (c[top, 1] < 4 * cell)
    assert in_a.sum() == 2 and in_b.sum() == 2
    _out_v, out_t, report = repair(v, np.delete(t, top[in_a | in_b], axis=0))
    assert_sound(report["after"], VOLUME, rel=1e-12)
    assert step(report, "holes")["filled"] == 2 and len(out_t) == len(t)


# ---- non-manifold edges


def unit_cube(origin) -> tuple[np.ndarray, np.ndarray]:
    v, t = subdivided_cube(1, size=1.0, origin=np.asarray(origin, dtype=float))
    return v, t


def test_bodies_touching_along_an_edge_are_separated_not_deleted():
    a = unit_cube(UTM)
    b = unit_cube(UTM + [1.0, 1.0, 0.0])
    v, t = merge_meshes([a, b])
    out_v, out_t, report = repair(v, t)
    # Once welded, the shared edge has four triangles.
    assert repair(v, t, **only(weld=1e-6))[2]["after"]["nonmanifold_edges"] == 1
    assert_sound(report["after"], 2.0, rel=1e-12)
    s = step(report, "nonmanifold")
    assert s["removed_triangles"] == 0 and s["duplicated_vertices"] == 2 and len(out_t) == 24
    assert report["after"]["components"] == 2 and len(out_v) == 16


def test_an_extra_sheet_over_a_face_is_removed():
    v, t = cube(1)
    top = np.flatnonzero(np.abs(v[:, 2] - UTM[2] - SIDE) < 1e-9)
    used = [tri for tri in t.tolist() if set(tri) <= set(top.tolist())]
    a, b = sorted(set(used[0]) & set(used[1]))  # the diagonal in use
    c, d = sorted(set(top.tolist()) - {a, b})
    # The same square triangulated over its other diagonal: not duplicates, but a second sheet.
    sheet = [[c, d, a], [d, c, b]]
    _out_v, out_t, report = repair(v, np.concatenate([t, sheet]).astype(np.int32))
    assert report["before"]["nonmanifold_edges"] == 4
    assert_sound(report["after"], VOLUME, rel=1e-12)
    assert step(report, "nonmanifold")["removed_triangles"] == 2 and len(out_t) == len(t)


# ---- self-intersections


def test_self_intersections_are_reported_not_fixed():
    a = cube(4)
    b = (a[0] + [SIDE / 2 + 1.0, SIDE / 2 + 2.0, SIDE / 2 + 3.0], a[1])
    v, t = merge_meshes([a, b])
    pairs = mesh_checks(v, t)["self_intersections"]
    idx = self_intersecting_triangles(v, t)
    assert pairs > 0 and len(idx) > 0 and np.all(np.diff(idx) > 0)
    # Only triangles near the crossing take part: each crosses the other cube's surface.
    lo, hi = b[0].min(axis=0), a[0].max(axis=0)
    box_lo, box_hi = v[t[idx]].min(axis=1), v[t[idx]].max(axis=1)
    assert np.all(box_hi >= lo - 1e-9) and np.all(box_lo <= hi + 1e-9)
    assert {int(i) < len(a[1]) for i in idx} == {True, False}
    assert len(self_intersecting_triangles(v, t, limit=3)) == 3
    assert len(self_intersecting_triangles(*a)) == 0

    _out_v, out_t, report = repair(v, t)
    assert report["after"]["self_intersections"] == pairs and np.array_equal(out_t, t)


# ---- simplification


def test_simplify_to_a_triangle_count():
    v, t = icosphere(5)  # 20 480 triangles
    out_v, out_t, report = simplify_mesh(v, t, target_triangles=2000)
    assert report["method"] == "quadric" and report["triangles_before"] == len(t)
    assert report["triangles_after"] == len(out_t) and 1500 <= len(out_t) <= 2100
    assert report["closed_before"] and report["closed_after"]
    # The reported deviation is the measured one (both ways), and small for a radius-100 sphere.
    measured = max(max_deviation(v, out_v, out_t), max_deviation(out_v, v, t))
    assert measured <= report["max_deviation"] + 1e-9 and report["max_deviation"] < 1.0
    assert report["volume_before"] == pytest.approx(sphere_volume(v, t), rel=1e-12)
    assert report["volume_after"] == pytest.approx(mesh_checks(out_v, out_t)["volume"], rel=1e-12)
    change = (report["volume_after"] - report["volume_before"]) / report["volume_before"] * 100
    assert report["volume_change_percent"] == pytest.approx(change) and abs(change) < 1.0
    assert np.abs(out_v - UTM).max() < 101

    # Nothing to do: the mesh comes back untouched.
    same_v, same_t, report = simplify_mesh(v, t, target_triangles=10**6)
    assert report["method"] == "none" and same_v.tobytes() == v.tobytes() and np.array_equal(same_t, t)
    with pytest.raises(ValueError, match="допуск"):
        simplify_mesh(v, t)


@pytest.mark.parametrize("tolerance", [0.05, 0.5])
def test_simplify_to_a_tolerance(tolerance):
    v, t = icosphere(5)
    out_v, out_t, report = simplify_mesh(v, t, tolerance=tolerance)
    # The sphere itself is within 0.05 m of its 20 480 triangles, so little can go at that tolerance.
    assert report["max_deviation"] <= tolerance and report["triangles_after"] < len(t) * (0.75 if tolerance < 0.1 else 0.25)
    # Checked independently, both ways, with exact point–triangle distances.
    assert max_deviation(v, out_v, out_t) <= tolerance and max_deviation(out_v, v, t) <= tolerance
    assert report["closed_after"] and abs(report["volume_change_percent"]) < 100 * 3 * tolerance / 100.0
    assert mesh_checks(out_v, out_t)["orientation"] == "outward"


def test_a_looser_tolerance_gives_fewer_triangles_and_flat_faces_cost_nothing():
    v, t = icosphere(5)
    fine = simplify_mesh(v, t, tolerance=0.05)[2]["triangles_after"]
    coarse = simplify_mesh(v, t, tolerance=0.5)[2]["triangles_after"]
    assert coarse < fine

    v, t = cube(16)  # 3072 triangles on six planes
    calls = []
    _out_v, _out_t, report = simplify_mesh(v, t, tolerance=1e-3, progress=calls.append)
    assert report["triangles_after"] < 200 and report["max_deviation"] <= 1e-3 and report["closed_after"]
    assert report["volume_after"] == pytest.approx(VOLUME, rel=1e-6) and calls[-1] == 1.0


def test_simplify_an_open_surface_keeps_its_border():
    v, t = wavy_surface(60)
    out_v, out_t, report = simplify_mesh(v, t, tolerance=1.0)
    assert report["max_deviation"] <= 1.0 and report["triangles_after"] < len(t)
    assert not report["closed_before"] and report["volume_before"] is None and report["volume_change_percent"] is None
    lo, hi = v.min(axis=0), v.max(axis=0)
    assert np.allclose(out_v.min(axis=0)[:2], lo[:2], atol=1e-6) and np.allclose(out_v.max(axis=0)[:2], hi[:2], atol=1e-6)
    assert mesh_checks(out_v, out_t, intersections=False)["open_edges"] > 0


# ---- smoothing


def test_smoothing_a_noisy_sphere_keeps_the_volume_and_removes_roughness():
    v, t = icosphere(4)
    rng = np.random.default_rng(11)
    radial = (v - UTM) / 100.0
    noisy = v + radial * rng.normal(0.0, 1.0, size=(len(v), 1))
    before = mesh_checks(noisy, t)["volume"]
    out_v, out_t, report = smooth_mesh(noisy, t, iterations=10, strength=0.5)
    assert out_t is not None and np.array_equal(out_t, t) and len(out_v) == len(noisy)
    # The final scale makes the volume equal to the source's: rel 1e-9.
    assert mesh_checks(out_v, out_t)["volume"] == pytest.approx(before, rel=1e-9)
    assert report["volume_before"] == pytest.approx(before, rel=1e-12)
    assert report["volume_after"] == pytest.approx(before, rel=1e-9) and abs(report["volume_change_percent"]) < 1e-7
    rough_before = np.std(np.linalg.norm(noisy - UTM, axis=1))
    rough_after = np.std(np.linalg.norm(out_v - UTM, axis=1))
    assert rough_after < rough_before / 2
    assert 0 < report["max_deviation"] < 5.0 and report["iterations"] == 10
    assert report["max_deviation"] >= max_deviation(out_v, noisy, t) - 1e-9

    # Without the correction Taubin smoothing alone barely changes the volume (no shrinkage).
    assert abs(report["scale"] - 1.0) < 2e-3
    same_v, _, report = smooth_mesh(noisy, t, iterations=0)
    assert same_v.tobytes() == noisy.tobytes() and report["max_deviation"] == 0.0


def test_smoothing_an_open_surface_keeps_its_boundary():
    n = 30
    v, t = wavy_surface(n)
    rng = np.random.default_rng(2)
    noisy = v + np.column_stack([np.zeros(len(v)), np.zeros(len(v)), rng.normal(0, 3.0, len(v))])
    out_v, _out_t, report = smooth_mesh(noisy, t)
    grid = np.arange(len(v)).reshape(n + 1, n + 1)
    border = np.unique(np.concatenate([grid[0], grid[-1], grid[:, 0], grid[:, -1]]))
    assert out_v[border].tobytes() == noisy[border].tobytes()
    assert report["volume_before"] is None and report["volume_change_percent"] is None
    inner = np.setdiff1d(np.arange(len(v)), border)
    assert np.std(out_v[inner, 2] - v[inner, 2]) < np.std(noisy[inner, 2] - v[inner, 2]) / 2
    moved = smooth_mesh(noisy, t, keep_boundary=False)[0]
    assert moved[border].tobytes() != noisy[border].tobytes()


# ---- merge and split


def test_merge_and_split_round_trip():
    parts = [icosphere(3), cube(4), icosphere(2, radius=10.0, centre=UTM + [400.0, 0.0, 0.0])]
    parts[1] = (parts[1][0] + [200.0, 0.0, 0.0], parts[1][1])
    v, t = merge_meshes(parts)
    assert len(v) == sum(len(p[0]) for p in parts) and len(t) == sum(len(p[1]) for p in parts)
    checks = mesh_checks(v, t)
    assert checks["components"] == 3 and checks["closed"]
    # A few vertices nothing uses: they are dropped by the split.
    v = np.concatenate([v, UTM + [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]])

    got = split_components(v, t)
    assert [len(p[1]) for p in got] == sorted((len(p[1]) for p in parts), reverse=True)
    by_size = sorted(parts, key=lambda p: -len(p[1]))
    for (gv, gt), (pv, pt) in zip(got, by_size):
        assert gv.tobytes() == pv.tobytes() and np.array_equal(gt, pt) and gt.dtype == np.int32
    assert sum(mesh_checks(*p)["volume"] for p in got) == pytest.approx(checks["volume"], rel=1e-12)
    assert merge_meshes([]) [1].shape == (0, 3) and split_components(v, t[:0]) == []
    assert len(split_components(*cube())) == 1


# ---- deviations


def test_max_deviation_against_exact_distances():
    # A 10 × 10 square in the plane z = 0 (two triangles), at UTM coordinates.
    v = UTM + np.array([[0.0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]])
    t = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    cases = [
        ([5.0, 5.0, 2.5], 2.5),            # above the interior: the height
        ([3.0, 7.0, -0.125], 0.125),        # below, over the diagonal
        ([13.0, 5.0, 4.0], 5.0),           # beyond an edge: 3-4-5 to the edge
        ([12.0, 13.0, 6.0], 7.0),          # beyond a corner: 2-3-6 to the corner
        ([4.0, 4.0, 0.0], 0.0),            # on the surface
    ]
    for point, expected in cases:
        assert max_deviation(UTM + np.array([point]), v, t) == pytest.approx(expected, abs=1e-9)
    assert max_deviation(UTM + np.array([c[0] for c in cases]), v, t) == pytest.approx(7.0, abs=1e-9)
    assert max_deviation(np.zeros((0, 3)), v, t) == 0.0

    # Points on a larger concentric sphere: the distance to the polyhedron is between the gap
    # and the gap plus its sagitta (the polyhedron lies inside its sphere).
    sv, st = icosphere(4)
    edge = np.linalg.norm(sv[st[:, 0]] - sv[st[:, 1]], axis=1).max()
    sagitta = 100.0 - np.sqrt(100.0**2 - (edge / np.sqrt(3)) ** 2)
    points = UTM + (icosphere(3)[0] - UTM) * 1.07
    d = max_deviation(points, sv, st)
    assert 7.0 - 1e-9 <= d <= 7.0 + sagitta + 1e-9
    # A far point takes the brute-force path: still exact (distance to the nearest corner of a cube).
    cv, ct = cube(8)
    assert max_deviation(UTM + np.array([[-300.0, -400.0, 0.0]]), cv, ct) == pytest.approx(500.0, abs=1e-9)
    # Against a brute-force evaluation of every triangle.
    rng = np.random.default_rng(4)
    pts = UTM + rng.uniform(-150, 150, size=(200, 3))
    brute = max(min(R._point_triangle_d2(*(p - UTM), *(sv[a] - UTM), *(sv[b] - UTM), *(sv[c] - UTM)) for a, b, c in st[::1])
                for p in pts[:20]) ** 0.5
    assert max_deviation(pts[:20], sv, st) == pytest.approx(brute, abs=1e-9)


# ---- speed


def test_repair_of_a_million_triangles():
    repair(*cube(4))  # compile the kernels first
    v, t = subdivided_cube(289)  # 1 002 252 triangles at UTM coordinates
    dv, dt = with_flips(v, t)
    c = v[t].mean(axis=1)
    gone = (c[:, 2] > v[:, 2].max() - 1e-6) & (np.abs(c[:, 0] - v[:, 0].mean()) < 100) & (np.abs(c[:, 1] - v[:, 1].mean()) < 100)
    dt = np.concatenate([dt[~gone], dt[:1000]])
    start = time.perf_counter()
    _out_v, _out_t, report = repair(dv, dt)
    seconds = time.perf_counter() - start
    print(f"\nrepair of {len(dt)} triangles: {seconds:.1f} s")
    assert_sound(report["after"], 1000.0**3, rel=1e-9)
    # Target ~10 s on four cores (measured about 4 s); the bound allows for a busy machine.
    assert seconds < 60
