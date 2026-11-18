"""Checks of a wireframe: consistency of the normals and self-intersections (ADR 0013 §4, 8.11).

Ported from BF `tests/test_wireframes_stage10.py` (soup against brute force, cube and two crossing cubes) and
`tests/test_repair.py` (checks of a sound solid and of every defect, the crossing triangles). References:
  * `TST-FAULTS.DXF` (tests/data/real): 2 723 crossing pairs — what BF's `self_intersections` gives on the
    polyface of the file (7 444 vertices, 7 417 triangles; read here with ezdxf, not with our reader);
  * the 121 wireframes of the two real TRIDB files: no crossing pairs, 1 860 conflicts of normals — as BF;
  * an exact test of two triangles in whole numbers (Python integers, no rounding) on soups with whole
    coordinates: the kernel must give the same pairs — no tolerance;
  * a closed cube: 0 pairs, 0 conflicts, volume and area within 1e-9 relative.
Hard data: triangles of very different sizes, UTM-sized coordinates, degenerate triangles, thousands of
triangles in one place (the scan resumed many times), cancellation.
"""

from __future__ import annotations

import json
import time
import tracemalloc
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.geo import checks
from bf_core.geo.checks import mesh_checks, orientation_conflicts, run_checks, self_intersections
from bf_core.geo.meshes import MeshError
from tests.meshutil import UTM, cube, grid, icosphere

FAULTS = Path(__file__).resolve().parents[2] / "tests" / "data" / "real" / "TST-FAULTS.DXF"
SHIFT = np.array([500_000, 4_500_000, 0])


# ---------------------------------------------------------------- an exact reference in whole numbers


def orient(a: Any, b: Any, c: Any, d: Any) -> int:
    """The sign of the volume of the tetrahedron abcd — exact: Python integers."""
    ux, uy, uz = (int(b[k]) - int(a[k]) for k in range(3))
    vx, vy, vz = (int(c[k]) - int(a[k]) for k in range(3))
    wx, wy, wz = (int(d[k]) - int(a[k]) for k in range(3))
    det = ux * (vy * wz - vz * wy) - uy * (vx * wz - vz * wx) + uz * (vx * wy - vy * wx)
    return (det > 0) - (det < 0)


def pierces(p: Any, q: Any, a: Any, b: Any, c: Any) -> bool:
    """The open segment pq crosses the plane of abc strictly between its ends, inside the closed triangle."""
    if orient(a, b, c, p) * orient(a, b, c, q) >= 0:
        return False
    s = (orient(p, q, a, b), orient(p, q, b, c), orient(p, q, c, a))
    return all(x >= 0 for x in s) or all(x <= 0 for x in s)


def exact_pairs(v: np.ndarray, t: np.ndarray) -> set[tuple[int, int]]:
    """Pairs of triangles without a common vertex one of whose edges pierces the other."""
    out = set()
    for i, j in combinations(range(len(t)), 2):
        if set(t[i].tolist()) & set(t[j].tolist()):
            continue
        a, b = v[t[i]], v[t[j]]
        if any(pierces(a[k], a[(k + 1) % 3], *b) or pierces(b[k], b[(k + 1) % 3], *a) for k in range(3)):
            out.add((i, j))
    return out


def soup(n: int, seed: int, small: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """n triangles with whole coordinates in 0…1000; a share `small` of them are 100 times smaller."""
    rng = np.random.default_rng(seed)
    v = rng.integers(0, 1000, (3 * n, 3))
    t = np.arange(3 * n).reshape(-1, 3)
    tiny = rng.random(n) < small
    for c in (1, 2):
        v[t[tiny, c]] = v[t[tiny, 0]] + rng.integers(-5, 6, (int(tiny.sum()), 3))
    return v.astype(np.float64), t.astype(np.int32)


def brute(v: np.ndarray, t: np.ndarray) -> set[tuple[int, int]]:
    """Every pair without a common vertex through the kernel of one pair, on the coordinates the check uses."""
    from bf_core.geo import checks_kernels as kernels

    vc = np.ascontiguousarray(v - (v.min(axis=0) + v.max(axis=0)) / 2.0)
    tt = np.ascontiguousarray(t, dtype=np.int32)
    point = np.empty(3)
    return {
        (i, j)
        for i, j in combinations(range(len(t)), 2)
        if not set(t[i].tolist()) & set(t[j].tolist()) and kernels.crossing(vc, tt, i, j, checks.EPS, point)
    }


def point_triangle_distance(p: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """Distance from the point p to every triangle (k×3×3), by the closest point of each (Ericson, 5.1.5)."""
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = np.einsum("ij,ij->i", ab, ap), np.einsum("ij,ij->i", ac, ap)
    bp = p - b
    d3, d4 = np.einsum("ij,ij->i", ab, bp), np.einsum("ij,ij->i", ac, bp)
    cp = p - c
    d5, d6 = np.einsum("ij,ij->i", ab, cp), np.einsum("ij,ij->i", ac, cp)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = va + vb + vc
        closest = a + ab * (vb / denom)[:, None] + ac * (vc / denom)[:, None]  # inside the face
        closest = np.where(((va <= 0) & (d4 - d3 >= 0) & (d5 - d6 >= 0))[:, None],
                           b + (c - b) * ((d4 - d3) / ((d4 - d3) + (d5 - d6)))[:, None], closest)  # fmt: skip
        closest = np.where(((vb <= 0) & (d2 >= 0) & (d6 <= 0))[:, None], a + ac * (d2 / (d2 - d6))[:, None], closest)
        closest = np.where(((vc <= 0) & (d1 >= 0) & (d3 <= 0))[:, None], a + ab * (d1 / (d1 - d3))[:, None], closest)
    closest = np.where(((d6 >= 0) & (d5 <= d6))[:, None], c, closest)
    closest = np.where(((d3 >= 0) & (d4 <= d3))[:, None], b, closest)
    closest = np.where(((d1 <= 0) & (d2 <= 0))[:, None], a, closest)
    return np.asarray(np.linalg.norm(closest - p, axis=1))


# ---------------------------------------------------------------- self-intersections


@pytest.mark.parametrize(("seed", "small"), [(3, 0.0), (4, 0.7), (5, 0.95)])
def test_soup_gives_the_pairs_of_the_exact_test(seed: int, small: float) -> None:
    v, t = soup(150, seed, small)
    expected = exact_pairs(v, t)
    assert len(expected) > 5
    assert brute(v, t) == expected  # the test of one pair
    for offset in (0, SHIFT):  # … and the grid, also on UTM-sized coordinates
        found = self_intersections(v + offset, t)
        assert found.pairs == len(expected)
        assert found.triangles.tolist() == sorted({i for pair in expected for i in pair})
        assert found.triangle_count == len(found.triangles) and found.triangles.dtype == np.int32
    if small:
        assert 0 < found.big_triangles < len(t)  # the large triangles went to coarser grids


@pytest.mark.parametrize("most", [(512,), (27,), (8,)])
def test_the_pairs_do_not_depend_on_the_grid(monkeypatch: pytest.MonkeyPatch, most: tuple[int, ...]) -> None:
    v, t = soup(300, 12, small=0.9)
    expected = self_intersections(v, t)
    monkeypatch.setattr(checks, "MAX_CELLS", most)
    monkeypatch.setattr(checks, "FACTOR", 2.0 if most == (8,) else 4.0)
    found = self_intersections(v + SHIFT, t)
    assert found.pairs == expected.pairs == len(brute(v, t)) and np.array_equal(found.triangles, expected.triangles)


def test_soup_on_real_coordinates_matches_brute_force() -> None:  # BF: test_self_intersections_match_brute_force…
    rng = np.random.default_rng(3)
    v = rng.uniform(0, 100, (300, 3)) + [500000, 4500000, 0]
    t = np.arange(300).reshape(-1, 3)
    pairs = brute(v, t)
    assert len(pairs) > 0
    assert self_intersections(v, t).pairs == len(pairs)


def test_closed_cube_is_clean_and_two_crossing_cubes_are_not() -> None:  # BF, the same name
    v, t = cube(6, 60.0)
    clean = self_intersections(v, t)
    assert clean.pairs == 0 and clean.triangle_count == 0 and len(clean.triangles) == 0 and len(clean.points) == 0
    assert orientation_conflicts(t, len(v)) == 0
    both_v = np.concatenate([v, v + [31.3, 27.7, 23.1]])
    found = self_intersections(both_v, np.concatenate([t, t + len(v)]))
    assert found.pairs > 0 and len(found.points) > 0
    flipped = t.copy()
    flipped[0] = flipped[0][::-1]
    assert orientation_conflicts(flipped, len(v)) == 3


def test_crossing_triangles_are_listed_and_points_lie_on_the_crossings() -> None:  # BF test_repair: …reported…
    side = 60.0
    va, ta = cube(4, side)
    vb = va + [side / 2 + 1.0, side / 2 + 2.0, side / 2 + 3.0]
    v, t = np.concatenate([va, vb]), np.concatenate([ta, ta + len(va)])
    found = self_intersections(v, t)
    idx = found.triangles
    assert found.pairs > 0 and len(idx) == found.triangle_count > 0 and np.all(np.diff(idx) > 0)
    # only triangles near the crossing take part: each touches the box where the cubes overlap
    lo, hi = vb.min(axis=0), va.max(axis=0)
    box_lo, box_hi = v[t[idx]].min(axis=1), v[t[idx]].max(axis=1)
    assert np.all(box_hi >= lo - 1e-9) and np.all(box_lo <= hi + 1e-9)
    assert {int(i) < len(ta) for i in idx} == {True, False}  # triangles of both cubes
    few = self_intersections(v, t, limit=3)
    assert few.triangles.tolist() == idx[:3].tolist() and few.triangle_count == len(idx) and few.pairs == found.pairs
    # the points: each lies on a triangle of either cube (1e-9 m on UTM-sized coordinates), inside the overlap
    assert 0 < len(found.points) <= checks.SAMPLES
    for p in found.points:
        d = point_triangle_distance(p, v[t])
        assert d[: len(ta)].min() < 1e-6 and d[len(ta) :].min() < 1e-6
        assert np.all(p >= lo - 1e-6) and np.all(p <= hi + 1e-6)
    none = self_intersections(v, t, samples=0)
    assert len(none.points) == 0 and none.pairs == found.pairs


def faults_mesh() -> tuple[np.ndarray, np.ndarray]:
    """The polyface of `TST-FAULTS.DXF` as the file has it: its vertex list and its faces (ezdxf)."""
    import ezdxf

    (polyline,) = ezdxf.readfile(FAULTS).modelspace()
    points = [tuple(vertex.dxf.location) for vertex in polyline.vertices if not vertex.is_face_record]
    faces = []
    for vertex in polyline.vertices:
        if vertex.is_face_record:
            corners = [abs(vertex.dxf.get(name, 0)) - 1 for name in ("vtx0", "vtx1", "vtx2", "vtx3")]
            corners = [c for c in corners if c >= 0]
            faces += [corners[:3]] + ([[corners[0], corners[2], corners[3]]] if len(corners) == 4 else [])
    return np.array(points, dtype=np.float64), np.array(faces, dtype=np.int32)


def test_tst_faults_has_2723_crossing_pairs() -> None:
    if not FAULTS.is_file():
        pytest.skip("tests/data/real/TST-FAULTS.DXF is not here")
    v, t = faults_mesh()
    assert (len(v), len(t)) == (7444, 7417)
    found = self_intersections(v, t)
    assert found.pairs == 2723  # BF `self_intersections` on the same mesh (ADR 0013, 8.11)
    assert found.triangle_count == len(found.triangles) == 2057 and 100 <= len(found.points) <= 200
    assert orientation_conflicts(t, len(v)) == 0
    report = mesh_checks(v, t)
    assert report["self_intersections"] == 2723 and not report["closed"] and report["open_edges"] == 7471
    assert report["nonmanifold_edges"] == 0 and report["degenerate"] == 0 and report["volume"] is None
    # the order of the triangles and a shift of the coordinates do not change what crosses
    order = np.random.default_rng(1).permutation(len(t))
    again = self_intersections(v, t[order])
    assert again.pairs == 2723 and sorted(order[again.triangles].tolist()) == found.triangles.tolist()


@pytest.mark.parametrize(
    ("name", "count", "conflicts"), [("ORE SEP 2025.tridb", 66, 0), ("Rock Model.tridb", 55, 1860)]
)
def test_real_tridb_wireframes_do_not_cross(name: str, count: int, conflicts: int) -> None:
    """The wireframes of the owner's files: BF finds no crossing pair in any of them and these conflicts of
    normals in all (its `self_intersections` and `orientation_conflicts`, run on the same geometry)."""
    from bf_core.formats.tridb import read_tridb

    path = FAULTS.parent / name
    if not path.is_file():
        pytest.skip(f"tests/data/real/{name} is not here")
    meshes = list(read_tridb(path))
    assert len(meshes) == count
    assert [self_intersections(m.xyz, m.tri).pairs for m in meshes] == [0] * count
    assert sum(orientation_conflicts(m.tri, m.point_count) for m in meshes) == conflicts


def tri(*corners: tuple[float, float, float]) -> np.ndarray:
    return np.array(corners, dtype=np.float64)


def test_what_is_a_crossing() -> None:
    flat = tri((0, 0, 0), (10, 0, 0), (0, 10, 0))

    def pairs(other: np.ndarray, shared: bool = False) -> int:
        v = np.concatenate([flat, other]) + UTM
        second = [0, 4, 5] if shared else [3, 4, 5]
        return self_intersections(v, np.array([[0, 1, 2], second], dtype=np.int32)).pairs

    assert pairs(tri((2, 2, -1), (3, 2, 1), (2, 3, 1))) == 1  # an edge goes through the inside
    assert pairs(tri((2, 2, 0), (3, 2, 1), (2, 3, 1))) == 0  # only a corner touches the plane
    assert pairs(tri((2, 2, 1), (3, 2, 2), (2, 3, 2))) == 0  # above
    assert pairs(tri((1, 1, 0), (5, 1, 0), (1, 5, 0))) == 0  # overlap in one plane: not found (said in «Свойства»)
    assert pairs(tri((5, 0, -1), (5, 0, 1), (5, -3, 0))) == 1  # through an edge of the other: found
    assert pairs(tri((20, 20, -1), (21, 20, 1), (20, 21, 1))) == 0  # the plane is crossed outside the triangle
    assert pairs(tri((0, 0, 0), (3, 3, -1), (3, 3, 1)), shared=True) == 0  # a common vertex: the pair is not tested


def test_triangles_of_very_different_sizes() -> None:
    """A fine surface under a few triangles as large as the whole mesh: every large triangle is cut by the
    surface along a line, and the answer does not take a test of every pair."""
    v, t = grid(200, 200, lambda x, y: np.sin(x / 9.0) + np.cos(y / 7.0), step=1.0, seed=3)  # 80 000 triangles
    rng = np.random.default_rng(8)
    big = []
    for _ in range(1500):
        a = np.array([*rng.uniform(0, 200, 2), -40.0])
        big += [a, a + [*rng.uniform(-100, 100, 2), 80.0], a + [*rng.uniform(-100, 100, 2), 80.0]]
    vv = np.concatenate([v, np.array(big) + UTM])
    tt = np.concatenate([t, len(v) + np.arange(3 * 1500, dtype=np.int32).reshape(-1, 3)])
    self_intersections(*soup(20, 1, small=0.5))  # the kernels are loaded before the clock starts
    started = time.perf_counter()
    found = self_intersections(vv, tt)
    seconds = time.perf_counter() - started
    assert found.big_triangles >= 1500 and found.pairs > 100_000
    assert set(np.flatnonzero(found.triangles >= len(t)).tolist())  # the large ones are among the crossing
    # a part of it against brute force: 60 of the large triangles and the surface they can reach
    part = np.concatenate([np.arange(len(t), len(t) + 60), np.arange(0, len(t), 400)])
    sub = self_intersections(vv, tt[part])
    assert sub.pairs == len(brute(vv, tt[part]))
    print(f"\n80 000 small + 1 500 large triangles: {found.pairs} pairs in {seconds:.2f} s")
    assert seconds < 20.0  # BF's «big against all» tests 1.2·10⁸ pairs here, and 10¹⁰ on a million triangles


def test_degenerate_triangles_and_tiny_meshes() -> None:
    v, t = cube(3, 10.0)
    extra = np.array([[0, 0, 1], [0, 1, 1], [2, 2, 2], [0, 5, 9]], dtype=np.int32)  # repeated corners
    line = np.array([UTM + [5, 5, -5], UTM + [5, 5, 5], UTM + [5, 5, 15]])  # a triangle without area: a segment
    vv = np.concatenate([v, line])
    tt = np.concatenate([t, extra, [[len(v), len(v) + 1, len(v) + 2]]]).astype(np.int32)
    found = self_intersections(vv, tt)
    assert found.pairs == len(brute(vv, tt)) > 0  # the segment pierces the bottom and the top of the cube
    assert len(tt) - 1 in found.triangles
    point = np.array([UTM] * 3)
    same = self_intersections(np.concatenate([point] * 50), np.arange(150, dtype=np.int32).reshape(-1, 3))
    assert same.pairs == 0  # fifty triangles at one point: one cell, nothing crosses

    empty = self_intersections(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32))
    assert empty.pairs == 0 and len(empty.triangles) == 0 and empty.points.shape == (0, 3)
    assert self_intersections(v[:3], np.array([[0, 1, 2]], dtype=np.int32)).pairs == 0
    bad = v.copy()
    bad[5, 1] = np.nan
    with pytest.raises(MeshError) as info:
        self_intersections(bad, t)
    assert info.value.code == "bad_coordinates" and info.value.hint
    with pytest.raises(MeshError):
        self_intersections(v, t + 1)  # a vertex that does not exist
    with pytest.raises(MeshError):
        self_intersections(v * 1e300, t)


def test_progress_cancel_and_a_scan_resumed_many_times(monkeypatch: pytest.MonkeyPatch) -> None:
    v, t = soup(400, 9)  # every triangle is as large as the mesh: one cell holds them all
    whole = self_intersections(v, t)
    assert whole.pairs > 1000
    monkeypatch.setattr(checks, "BUDGET", 500)
    seen: list[float] = []
    again = self_intersections(v, t, progress=seen.append)
    assert (again.pairs, again.triangle_count) == (whole.pairs, whole.triangle_count)
    assert np.array_equal(again.triangles, whole.triangles) and np.array_equal(again.points, whole.points)
    assert len(seen) > 100 and seen == sorted(seen) and seen[0] >= 0.0 and seen[-1] == 1.0

    mixed_v, mixed_t = soup(400, 10, small=0.8)  # the scan across the levels is resumed as well
    expected = self_intersections(mixed_v, mixed_t)
    monkeypatch.setattr(checks, "BUDGET", 50)
    resumed = self_intersections(mixed_v, mixed_t)
    assert resumed.pairs == expected.pairs and np.array_equal(resumed.triangles, expected.triangles)

    class Cancelled(Exception):
        pass

    def cancel(fraction: float) -> None:
        if fraction > 0.5:
            raise Cancelled

    with pytest.raises(Cancelled):
        self_intersections(v, t, progress=cancel)


def test_points_are_spread_over_all_the_crossings() -> None:
    """Many separate crossings in a row: the 200 points are not the first 200 found."""
    blade = tri((0, 0, -1), (0, 0, 1), (0, 3, 0))
    plate = tri((-1, 1, 0), (1, 1, 0), (0, 2, 0.0))
    step = np.array([10.0, 0.0, 0.0])
    v = np.concatenate([np.concatenate([blade, plate]) + k * step for k in range(1000)]) + UTM
    t = np.arange(len(v), dtype=np.int32).reshape(-1, 3)
    found = self_intersections(v, t)
    assert found.pairs == 1000 and found.triangle_count == 2000
    x = found.points[:, 0] - UTM[0]
    assert 100 <= len(x) <= 200 and x.min() < 500 and x.max() > 9000  # from one end to the other
    assert np.all(np.diff(np.sort(x)) < 200.0)  # … without large gaps


# ---------------------------------------------------------------- normals, measures


def mobius(n: int = 40) -> tuple[np.ndarray, np.ndarray]:
    """A Möbius strip of 2n triangles: a surface with one side — its normals cannot be made consistent."""
    a = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    rows = []
    for s in (-1.0, 1.0):
        r = 10.0 + s * np.cos(a / 2.0)
        rows.append(np.column_stack([r * np.cos(a), r * np.sin(a), s * np.sin(a / 2.0)]))
    v = np.concatenate(rows) + UTM
    t = []
    for k in range(n):
        j = (k + 1) % n
        low, high = (j, j + n) if j else (n, 0)  # the strip is glued with a half turn
        t += [[k, low, k + n], [low, high, k + n]]
    return v, np.array(t, dtype=np.int32)


def test_orientation_conflicts() -> None:
    v, t = cube(4, 10.0)
    assert orientation_conflicts(t, len(v)) == 0
    assert orientation_conflicts(t[:, ::-1], len(v)) == 0  # all inward: consistent
    half = t.copy()
    half[: len(t) // 2] = half[: len(t) // 2, ::-1]
    assert orientation_conflicts(half, len(v)) > 0
    mv, mt = mobius()
    assert orientation_conflicts(mt, len(mv)) > 0 and self_intersections(mv, mt).pairs == 0
    assert orientation_conflicts(np.zeros((0, 3), dtype=np.int32), 0) == 0
    assert orientation_conflicts(np.concatenate([t, t[:1]]), len(v)) == 3  # a triangle twice


def test_checks_of_a_sound_solid_and_of_every_defect() -> None:  # BF test_repair, the same name
    v, t = cube(6, 10.0)
    c = mesh_checks(v, t)
    assert c["closed"] and c["open_edges"] == 0 and c["nonmanifold_edges"] == 0 and c["degenerate"] == 0
    assert c["orientation"] == "outward" and c["orientation_conflicts"] == 0 and c["self_intersections"] == 0
    assert c["volume"] == pytest.approx(1000.0, rel=1e-9) and c["area"] == pytest.approx(600.0, rel=1e-9)
    assert c["triangles"] == len(t) and c["vertices"] == len(v)
    assert mesh_checks(v, t, intersections=False)["self_intersections"] is None

    soup_v = v[t].reshape(-1, 3)  # every triangle with its own vertices
    assert mesh_checks(soup_v, np.arange(3 * len(t)).reshape(-1, 3))["open_edges"] == 3 * len(t)
    hole = mesh_checks(v, t[5:])
    assert hole["open_edges"] > 0 and not hole["closed"] and hole["volume"] is None
    fin = np.concatenate([t, [[t[0, 0], t[0, 1], len(v)]]])  # a third triangle on an edge
    assert mesh_checks(np.concatenate([v, [v[0] + 30.0]]), fin)["nonmanifold_edges"] == 1
    flat = np.concatenate([t, [[0, 0, 1], [2, 2, 2], [3, 4, 4]]])
    assert mesh_checks(v, flat)["degenerate"] == 3
    flips = t.copy()
    flips[::7] = flips[::7, ::-1]
    assert mesh_checks(v, flips)["orientation_conflicts"] > 0
    inward = mesh_checks(v, t[:, ::-1])
    assert inward["orientation"] == "inward" and inward["volume"] == pytest.approx(1000.0, rel=1e-9)
    nothing = mesh_checks(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32))
    assert nothing["triangles"] == 0 and nothing["self_intersections"] == 0 and nothing["volume"] is None

    sv, st = icosphere(4, 100.0)
    sphere = mesh_checks(sv, st)
    assert sphere["closed"] and sphere["self_intersections"] == 0 and sphere["orientation_conflicts"] == 0


def test_run_checks_gives_what_the_job_stores() -> None:
    va, ta = cube(4, 60.0)
    v, t = np.concatenate([va, va + [31.0, 32.0, 33.0]]), np.concatenate([ta, ta + len(va)])
    seen: list[float] = []
    out = run_checks(v, t, progress=seen.append)
    found = self_intersections(v, t)
    stats = json.loads(json.dumps(out.stats))  # JSON values only
    assert set(stats) == {"state", "normal_conflicts", "pairs", "triangles", "listed", "points", "seconds"}
    assert stats["state"] == "done" and stats["normal_conflicts"] == 0 and stats["pairs"] == found.pairs > 0
    assert stats["triangles"] == stats["listed"] == len(out.triangles) == found.triangle_count
    assert 0 < len(stats["points"]) <= 200 and all(len(p) == 3 for p in stats["points"]) and stats["seconds"] >= 0
    assert np.array_equal(out.triangles, found.triangles) and seen == sorted(seen) and seen[-1] == 1.0
    clean = run_checks(va, ta)
    assert clean.stats["pairs"] == 0 and clean.stats["points"] == [] and len(clean.triangles) == 0


@pytest.mark.slow
def test_a_million_triangles_time_and_memory() -> None:
    """ADR 0013 «Замеры»: self-intersections of 1 million triangles (the budget is set by the first measure)."""
    v, t = grid(708, 708, lambda x, y: np.sin(x / 40.0) * 15.0 + np.cos(y / 55.0) * 10.0)  # shuffled
    wall_v, wall_t = grid(700, 20, lambda x, y: x * 0.0, seed=5)
    wall_v = np.column_stack(
        [wall_v[:, 0], wall_v[:, 2] - UTM[2] + UTM[1] + 350.0, wall_v[:, 1] - UTM[1] + UTM[2] - 10]
    )
    vv, tt = np.concatenate([v, wall_v]), np.concatenate([t, wall_t + len(v)])
    self_intersections(*soup(20, 1, small=0.5))  # the kernels are loaded before the clock starts
    started = time.perf_counter()
    found = self_intersections(vv, tt)
    seconds = time.perf_counter() - started
    conflicts = orientation_conflicts(tt, len(vv))
    tracemalloc.start()
    again = self_intersections(vv, tt)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert found.pairs == again.pairs > 1000 and conflicts == 0
    print(f"\n{len(tt)} triangles: {found.pairs} pairs in {seconds:.2f} s, peak {peak / 2**20:.0f} MB")
    assert seconds < 30.0 and peak < 400 << 20
