"""«Упростить», «Сгладить», «В один объект», «На части» and the largest deviation (ADR 0015 §7, 9.10).

Ported from BF `tests/test_repair.py` (simplification, smoothing, merge and split, deviations; the assertions
kept, the meshes of `tests/meshutil`: UTM coordinates, triangles shuffled). References and tolerances:

  * distances: a 10 × 10 square at UTM with the answers worked out by hand (height, 3-4-5, 2-3-6), a concentric
    sphere (between the gap and the gap plus the sagitta), brute force over every triangle — abs 1e-9 m;
  * the volume of a smoothed closed mesh equals the source's to rel 1e-9 (ТЗ 5.4; the 9.10 criterion);
  * the reported deviation is never below the one measured independently both ways;
  * split(merge(a, b)) gives a and b back bit for bit.
Hard data: triangles of very different sizes (the big ones are kept off the grid), points far from the surface
(every triangle tested), read-only inputs (pyfqmr gets writable copies of its own: ТЗ 17 п. 6), cancellation.
"""

from __future__ import annotations

import itertools
import time
from typing import Any

import numpy as np
import pytest

from bf_core.geo import deviation as D
from bf_core.geo.deviation import max_deviation
from bf_core.geo.repair import repair_checks
from bf_core.geo.reshape import merge_meshes, simplify_mesh, smooth_mesh, split_components
from tests.meshutil import UTM, cube, grid, icosphere

SIDE = 60.0
VOLUME = SIDE**3


def signed_volume(v: np.ndarray, t: np.ndarray, centre: np.ndarray = UTM) -> float:
    a, b, c = (v[t[:, i]] - centre for i in range(3))
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0


def wavy(n: int) -> tuple[np.ndarray, np.ndarray]:
    """A terrain of n × n cells over 4 km (BF `wavy_surface`): open, normals up, at UTM, vertices row by row."""
    f = lambda x, y: 50.0 * np.sin(x / 300.0) * np.cos(y / 410.0) + 400.0  # noqa: E731
    return grid(n, n, f, step=4000.0 / n)  # type: ignore[no-any-return]


def border_of(n: int) -> np.ndarray:
    g = np.arange((n + 1) ** 2).reshape(n + 1, n + 1)
    return np.unique(np.concatenate([g[0], g[-1], g[:, 0], g[:, -1]]))


def rows(a: np.ndarray) -> set[bytes]:
    return {r.tobytes() for r in np.ascontiguousarray(a)}


# ---- simplification


def test_simplify_to_a_triangle_count() -> None:
    v, t = icosphere(5, 100.0)  # 20 480 triangles
    out_v, out_t, report = simplify_mesh(v, t, target_triangles=2000)
    assert report["method"] == "quadric" and report["triangles_before"] == len(t)
    assert report["triangles_after"] == len(out_t) and 1500 <= len(out_t) <= 2100
    assert report["closed_before"] and report["closed_after"]
    # the reported deviation is the measured one (both ways), and small for a radius-100 sphere
    measured = max(max_deviation(v, out_v, out_t), max_deviation(out_v, v, t))
    assert measured <= report["max_deviation"] + 1e-9 and report["max_deviation"] < 1.0
    assert report["volume_before"] == pytest.approx(signed_volume(v, t), rel=1e-12)
    assert report["volume_after"] == pytest.approx(repair_checks(out_v, out_t)["volume"], rel=1e-12)
    change = (report["volume_after"] - report["volume_before"]) / report["volume_before"] * 100
    assert report["volume_change_percent"] == pytest.approx(change) and abs(change) < 1.0
    assert np.abs(out_v - UTM).max() < 101 and out_v.dtype == np.float64 and out_t.dtype == np.int32

    # nothing to do: the mesh comes back untouched
    same_v, same_t, report = simplify_mesh(v, t, target_triangles=10**6)
    assert report["method"] == "none" and same_v.tobytes() == v.tobytes() and np.array_equal(same_t, t)
    with pytest.raises(ValueError, match="допуск") as err:
        simplify_mesh(v, t)
    assert err.value.code == "bad_simplify" and err.value.hint  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="больше нуля"):
        simplify_mesh(v, t, tolerance=0.0)


@pytest.mark.parametrize("tolerance", [0.05, 0.5])
def test_simplify_to_a_tolerance(tolerance: float) -> None:
    v, t = icosphere(5, 100.0)
    out_v, out_t, report = simplify_mesh(v, t, tolerance=tolerance)
    # the sphere itself is within 0.05 m of its 20 480 triangles, so little can go at that tolerance
    share = 0.75 if tolerance < 0.1 else 0.25
    assert report["max_deviation"] <= tolerance and report["triangles_after"] < len(t) * share
    # checked independently, both ways, with exact point–triangle distances
    assert max_deviation(v, out_v, out_t) <= tolerance and max_deviation(out_v, v, t) <= tolerance
    assert report["closed_after"] and abs(report["volume_change_percent"]) < 3 * tolerance
    assert repair_checks(out_v, out_t, intersections=False)["orientation"] == "outward"


def test_a_looser_tolerance_gives_fewer_triangles_and_flat_faces_cost_nothing() -> None:
    v, t = icosphere(5, 100.0)
    fine = simplify_mesh(v, t, tolerance=0.05)[2]["triangles_after"]
    coarse = simplify_mesh(v, t, tolerance=0.5)[2]["triangles_after"]
    assert coarse < fine

    v, t = cube(16, SIDE, UTM)  # 3 072 triangles on six planes
    calls: list[float] = []
    out_v, _out_t, report = simplify_mesh(v, t, tolerance=1e-3, progress=calls.append)
    assert report["triangles_after"] < 200 and report["max_deviation"] <= 1e-3 and report["closed_after"]
    assert report["volume_after"] == pytest.approx(VOLUME, rel=1e-6) and calls[-1] == 1.0 and calls == sorted(calls)
    # the corners of the cube are vertices of the result bit for bit (ADR 0015 §7: surviving vertices)
    corners = UTM + SIDE * np.array(list(itertools.product([0.0, 1.0], repeat=3)))
    assert rows(corners) <= rows(v) and rows(corners) <= rows(out_v)


def test_simplify_an_open_surface_keeps_its_border() -> None:
    n = 60
    v, t = wavy(n)
    out_v, out_t, report = simplify_mesh(v, t, tolerance=1.0)
    assert report["max_deviation"] <= 1.0 and report["triangles_after"] < len(t)
    assert not report["closed_before"] and report["volume_before"] is None and report["volume_change_percent"] is None
    assert rows(v[border_of(n)]) <= rows(out_v)  # the border, bit for bit
    assert repair_checks(out_v, out_t, intersections=False)["open_edges"] == 4 * n


def test_pyfqmr_gets_writable_copies_and_the_inputs_stay(monkeypatch: pytest.MonkeyPatch) -> None:
    """ТЗ 17 п. 6: the input may be a read-only view of a file; pyfqmr writes into the arrays it is given."""
    import pyfqmr

    seen: list[tuple[bool, bool]] = []
    real = pyfqmr.Simplify

    class Spy:
        def __init__(self) -> None:
            self.inner = real()

        def setMesh(self, vertices: np.ndarray, faces: np.ndarray, *args: Any) -> None:  # noqa: N802
            seen.append((vertices.flags.writeable and faces.flags.writeable, np.shares_memory(vertices, v)))
            self.inner.setMesh(vertices, faces, *args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    monkeypatch.setattr(pyfqmr, "Simplify", Spy)
    v, t = icosphere(4, 100.0)
    v.flags.writeable = False
    t.flags.writeable = False
    keep = v.tobytes() + t.tobytes()
    for kwargs in ({"target_triangles": 500}, {"tolerance": 0.5}):
        _, out_t, _ = simplify_mesh(v, t, **kwargs)
        assert 0 < len(out_t) < len(t)
    assert seen and all(w and not shared for w, shared in seen)
    assert v.tobytes() + t.tobytes() == keep


def test_simplify_and_smooth_can_be_cancelled() -> None:
    v, t = icosphere(4, 100.0)

    class Stop(Exception):
        pass

    def cancel(fraction: float) -> None:
        if fraction > 0.2:
            raise Stop

    with pytest.raises(Stop):
        simplify_mesh(v, t, tolerance=0.5, progress=cancel)
    with pytest.raises(Stop):
        smooth_mesh(v, t, iterations=50, progress=cancel)


# ---- smoothing


def noisy_sphere(seed: int = 11) -> tuple[np.ndarray, np.ndarray]:
    v, t = icosphere(4, 100.0)
    radial = (v - UTM) / 100.0
    return v + radial * np.random.default_rng(seed).normal(0.0, 1.0, size=(len(v), 1)), t


def test_smoothing_a_noisy_sphere_keeps_the_volume_and_removes_roughness() -> None:
    noisy, t = noisy_sphere()
    before = repair_checks(noisy, t, intersections=False)["volume"]
    calls: list[float] = []
    out_v, out_t, report = smooth_mesh(noisy, t, iterations=10, strength=0.5, progress=calls.append)
    assert np.array_equal(out_t, t) and len(out_v) == len(noisy) and calls[-1] == 1.0
    # 9.10: the final scale makes the volume equal to the source's — rel 1e-9
    assert repair_checks(out_v, out_t, intersections=False)["volume"] == pytest.approx(before, rel=1e-9)
    assert report["volume_before"] == pytest.approx(before, rel=1e-12)
    assert report["volume_after"] == pytest.approx(before, rel=1e-9) and abs(report["volume_change_percent"]) < 1e-7
    rough_before = np.std(np.linalg.norm(noisy - UTM, axis=1))
    rough_after = np.std(np.linalg.norm(out_v - UTM, axis=1))
    assert rough_after < rough_before / 2
    assert 0 < report["max_deviation"] < 5.0 and report["iterations"] == 10
    assert report["max_deviation"] >= max_deviation(out_v, noisy, t) - 1e-9
    assert report["max_deviation"] >= max_deviation(noisy, out_v, t) - 1e-9

    # without the correction Taubin smoothing alone barely changes the volume (no shrinkage)
    assert abs(report["scale"] - 1.0) < 2e-3
    same_v, _, report = smooth_mesh(noisy, t, iterations=0)
    assert same_v.tobytes() == noisy.tobytes() and report["max_deviation"] == 0.0


def test_smoothing_a_sphere_keeps_its_volume_on_hard_data() -> None:
    """A finer sphere far from the origin with strong smoothing and a cube (sharp edges, flat faces)."""
    v, t = icosphere(5, 7.5, UTM + [1e5, -2e5, 3e3])
    exact = signed_volume(v, t, UTM + [1e5, -2e5, 3e3])
    out_v, _, report = smooth_mesh(v, t, iterations=60, strength=0.9)
    assert signed_volume(out_v, t, UTM + [1e5, -2e5, 3e3]) == pytest.approx(exact, rel=1e-9)
    assert report["volume_after"] == pytest.approx(exact, rel=1e-9)
    cv, ct = cube(8, SIDE, UTM)
    out_v, _, report = smooth_mesh(cv, ct, iterations=30, strength=0.6)
    assert repair_checks(out_v, ct, intersections=False)["volume"] == pytest.approx(VOLUME, rel=1e-9)
    assert report["scale"] != 1.0 and report["max_deviation"] > 0.1  # the corners are rounded off


def test_smoothing_an_open_surface_keeps_its_boundary() -> None:
    n = 30
    v, t = wavy(n)
    rng = np.random.default_rng(2)
    noisy = v + np.column_stack([np.zeros(len(v)), np.zeros(len(v)), rng.normal(0, 3.0, len(v))])
    out_v, _out_t, report = smooth_mesh(noisy, t)
    border = border_of(n)
    assert out_v[border].tobytes() == noisy[border].tobytes()
    assert report["volume_before"] is None and report["volume_change_percent"] is None
    inner = np.setdiff1d(np.arange(len(v)), border)
    assert np.std(out_v[inner, 2] - v[inner, 2]) < np.std(noisy[inner, 2] - v[inner, 2]) / 2
    moved = smooth_mesh(noisy, t, keep_boundary=False)[0]
    assert moved[border].tobytes() != noisy[border].tobytes()


# ---- merge and split


def test_merge_and_split_round_trip() -> None:
    parts = [icosphere(3, 100.0), cube(4, SIDE, UTM + [200.0, 0.0, 0.0]), icosphere(2, 10.0, UTM + [400.0, 0.0, 0.0])]
    v, t = merge_meshes(parts)
    assert len(v) == sum(len(p[0]) for p in parts) and len(t) == sum(len(p[1]) for p in parts)
    checks = repair_checks(v, t, intersections=False)
    assert checks["components"] == 3 and checks["closed"]
    v = np.concatenate([v, UTM + [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]])  # vertices nothing uses: dropped by split

    got = split_components(v, t)
    assert [len(p[1]) for p in got] == sorted((len(p[1]) for p in parts), reverse=True)
    for (gv, gt), (pv, pt) in zip(got, sorted(parts, key=lambda p: -len(p[1])), strict=True):
        assert gv.tobytes() == pv.tobytes() and np.array_equal(gt, pt) and gt.dtype == np.int32
    total = sum(repair_checks(*p, intersections=False)["volume"] for p in got)
    assert total == pytest.approx(checks["volume"], rel=1e-12)
    assert merge_meshes([])[1].shape == (0, 3) and split_components(v, t[:0]) == []
    assert len(split_components(*cube(6, SIDE, UTM))) == 1


def test_split_of_merge_gives_the_parts_back() -> None:
    """9.10: split(merge(a, b)) = a, b — vertices bit for bit (NaN-free UTM, −0.0 kept), triangles as given."""
    a = icosphere(2, 30.0)
    b = cube(3, 12.0, np.array([-0.0, 0.0, -0.0]))
    b = (b[0].copy(), b[1])
    b[0][0] = [-0.0, -0.0, -0.0]
    got = split_components(*merge_meshes([a, b]))
    assert len(got) == 2
    for (gv, gt), (pv, pt) in zip(got, [a, b], strict=True):
        assert gv.tobytes() == pv.tobytes() and np.array_equal(gt, pt)


# ---- deviations


def test_max_deviation_against_exact_distances() -> None:
    # a 10 × 10 square in the plane z = 0 (two triangles), at UTM coordinates
    v = UTM + np.array([[0.0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]])
    t = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    cases = [
        ([5.0, 5.0, 2.5], 2.5),  # above the interior: the height
        ([3.0, 7.0, -0.125], 0.125),  # below, over the diagonal
        ([13.0, 5.0, 4.0], 5.0),  # beyond an edge: 3-4-5 to the edge
        ([12.0, 13.0, 6.0], 7.0),  # beyond a corner: 2-3-6 to the corner
        ([4.0, 4.0, 0.0], 0.0),  # on the surface
    ]
    for point, expected in cases:
        assert max_deviation(UTM + np.array([point]), v, t) == pytest.approx(expected, abs=1e-9)
    assert max_deviation(UTM + np.array([c[0] for c in cases]), v, t) == pytest.approx(7.0, abs=1e-9)
    assert max_deviation(np.zeros((0, 3)), v, t) == 0.0 and np.isnan(max_deviation(v, v, t[:0]))

    # points on a larger concentric sphere: the distance to the polyhedron lies between the gap and the gap plus
    # its sagitta (the polyhedron is inside its sphere)
    sv, st = icosphere(4, 100.0)
    edge = np.linalg.norm(sv[st[:, 0]] - sv[st[:, 1]], axis=1).max()
    sagitta = 100.0 - np.sqrt(100.0**2 - (edge / np.sqrt(3)) ** 2)
    d = max_deviation(UTM + (icosphere(3, 100.0)[0] - UTM) * 1.07, sv, st)
    assert 7.0 - 1e-9 <= d <= 7.0 + sagitta + 1e-9
    # a far point takes the brute-force path: still exact (the distance to the nearest corner of a cube)
    cv, ct = cube(8, SIDE, UTM)
    assert max_deviation(UTM + np.array([[-300.0, -400.0, 0.0]]), cv, ct) == pytest.approx(500.0, abs=1e-9)
    # against a brute-force evaluation of every triangle
    pts = UTM + np.random.default_rng(4).uniform(-150, 150, size=(20, 3))
    brute = max(
        min(D.point_triangle_d2(*(p - UTM), *(sv[a] - UTM), *(sv[b] - UTM), *(sv[c] - UTM)) for a, b, c in st)
        for p in pts
    )
    assert max_deviation(pts, sv, st) == pytest.approx(brute**0.5, abs=1e-9)


def test_deviation_on_triangles_of_very_different_sizes_and_on_threads() -> None:
    # a fine terrain with one huge triangle above it (kept off the grid); points near the terrain (as the vertices
    # of a simplified or smoothed copy are) and a few far ones (every triangle tested for them)
    v, t = grid(80, 80, lambda x, y: 0.01 * x * y, step=1.0)
    big = UTM + np.array([[-500.0, -500.0, 300.0], [900.0, -500.0, 300.0], [0.0, 900.0, 300.0]])
    v2 = np.concatenate([v, big])
    t2 = np.concatenate([t, [[len(v), len(v) + 1, len(v) + 2]]]).astype(np.int32)
    surface = D.Surface(v2 - UTM, t2)
    assert len(surface.big_ids) == 1
    rng = np.random.default_rng(9)
    near = rng.uniform(0, 80, size=(50_000, 2))
    near = np.column_stack([near, 0.01 * near[:, 0] * near[:, 1] + rng.normal(0.0, 2.0, len(near))])
    pts = np.concatenate([near, rng.uniform([-100, -100, -50], [180, 180, 400], size=(200, 3))])
    started = time.perf_counter()
    one = surface.distances(pts)
    seconds = time.perf_counter() - started
    four = surface.distances(pts, threads=4)
    assert one.tobytes() == four.tobytes() and len(one) == len(pts)
    check = pts[::2500]
    exact = [
        min(D.point_triangle_d2(*p, *(v2[a] - UTM), *(v2[b] - UTM), *(v2[c] - UTM)) for a, b, c in t2) ** 0.5
        for p in check
    ]
    assert np.allclose(one[::2500], exact, rtol=0.0, atol=1e-9)
    assert one[-200:].min() > 0.0 and seconds < 10.0  # measured: about 0.3 s
