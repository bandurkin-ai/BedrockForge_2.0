"""Extrusion, triangle edits and a probe of stitching on real contours (ТЗ 7.23; ADR 0015 §7; 9.7).

From BF `tests/test_stitch.py` (`test_extrude_polygon_along_the_normal_and_a_vector`,
`test_edit_triangles_removes_and_adds`) with the assertions unchanged, and new: the vertical, both sides for
every direction, several polygons, large meshes, the closed contours of `tests/data/real/*.STR`. Tolerances:
volumes 1e-9 relative (a prism is exact up to float64 rounding in project coordinates).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from bf_core.geo.checks import orientation_conflicts, self_intersections
from bf_core.geo.extrude import edit_triangles, extrude_polygon, extrude_polygons
from bf_core.geo.meshes import mesh_stats
from bf_core.geo.polygon import StitchError, area_vector
from bf_core.geo.stitch import stitch_contours

ORIGIN = np.array([512345.678, 6789012.345, 250.0])  # UTM-size coordinates
EXACT = 1e-9
REAL = Path(__file__).resolve().parents[2] / "tests" / "data" / "real"
L_SHAPE = np.array([[0, 0], [40, 0], [40, 10], [10, 10], [10, 30], [0, 30]], dtype=np.float64)  # area 600


def section(xz, position, azimuth=30.0):  # type: ignore[no-untyped-def]
    xz = np.asarray(xz, dtype=np.float64)
    a = np.radians(azimuth)
    strike = np.array([np.cos(a), np.sin(a), 0.0])
    across = np.array([-np.sin(a), np.cos(a), 0.0])
    return ORIGIN + xz[:, :1] * strike + position * across + xz[:, 1:] * np.array([0.0, 0.0, 1.0])


def volume(v, t):  # type: ignore[no-untyped-def]
    c = v - (v.min(axis=0) + v.max(axis=0)) / 2
    return float(np.einsum("ij,ij->i", c[t[:, 0]], np.cross(c[t[:, 1]], c[t[:, 2]])).sum()) / 6.0


def assert_solid(v, t):  # type: ignore[no-untyped-def]
    stats = mesh_stats(v, t)
    assert stats["closed"] and stats["open_edges"] == 0 and stats["nonmanifold_edges"] == 0, stats
    assert stats["orientation"] == "outward"
    assert orientation_conflicts(t, len(v)) == 0
    assert self_intersections(v, t).pairs == 0
    assert t.dtype == np.int32 and v.dtype == np.float64
    assert volume(v, t) > 0


# --- extrusion -----------------------------------------------------------------------------------------------


def test_extrude_polygon_along_the_normal_and_a_vector() -> None:
    polygon = section(L_SHAPE, 0.0)
    normal = np.array([-np.sin(np.radians(30)), np.cos(np.radians(30)), 0.0])
    for poly in (polygon, polygon[::-1]):
        for distance in (12.5, -12.5):
            v, t = extrude_polygon(poly, distance=distance)
            assert_solid(v, t)
            assert volume(v, t) == pytest.approx(600.0 * 12.5, rel=EXACT)
            assert v[:6].tobytes() == poly.tobytes() and len(v) == 12 and len(t) == 20
            assert np.abs((v[6:] - v[:6]) @ normal) == pytest.approx(12.5, abs=1e-6)
    # Along a vector 35° off the normal; its length is ignored.
    tilt = np.radians(35.0)
    vector = 7.0 * (np.cos(tilt) * normal + np.sin(tilt) * np.array([0.0, 0.0, 1.0]))
    v, t = extrude_polygon(polygon, distance=12.5, vector=vector)
    assert_solid(v, t)
    assert volume(v, t) == pytest.approx(600.0 * 12.5 * np.cos(tilt), rel=EXACT)
    assert np.linalg.norm(v[6:] - v[:6], axis=1) == pytest.approx(12.5, abs=1e-6)
    # Both sides: half the distance each way.
    v, t = extrude_polygon(polygon, distance=12.5, both_sides=True)
    assert_solid(v, t)
    assert volume(v, t) == pytest.approx(600.0 * 12.5, rel=EXACT)
    assert (v[:6] + v[6:]) / 2 == pytest.approx(polygon, abs=1e-6)
    with pytest.raises(StitchError, match="в плоскости полигона"):
        extrude_polygon(polygon, distance=5.0, vector=[0.0, 0.0, 1.0])
    with pytest.raises(StitchError, match="отличным от нуля"):
        extrude_polygon(polygon, distance=0.0)


def test_prism_volume_is_area_times_distance_for_every_direction() -> None:
    """9.7: by the normal — area × distance; by the vertical — plan area × distance; both sides — the same."""
    rng = np.random.default_rng(97)
    t = np.sort(rng.uniform(0, 2 * np.pi, 40))
    star = np.column_stack([np.cos(t), np.sin(t)]) * rng.uniform(20.0, 60.0, (40, 1))  # non-convex, 40 vertices
    dip = np.radians(50.0)
    u, w = np.array([1.0, 0.0, 0.0]), np.array([0.0, np.cos(dip), np.sin(dip)])  # a plane dipping 50°
    polygon = ORIGIN + star[:, :1] * u + star[:, 1:] * w
    x, y = star[:, 0], star[:, 1]
    true_area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y)))
    plan = true_area * np.cos(dip)
    assert float(np.linalg.norm(area_vector(polygon))) == pytest.approx(true_area, rel=1e-10)
    for both in (False, True):
        for distance in (7.25, -7.25):
            for kind, expected in (("normal", true_area), ("vertical", plan), ("vector", true_area * np.cos(0.3))):
                n = np.cross(u, w)
                vector = np.cos(0.3) * n + np.sin(0.3) * u if kind == "vector" else None
                v, tri = extrude_polygon(
                    polygon, distance=distance, vertical=kind == "vertical", vector=vector, both_sides=both
                )
                assert_solid(v, tri)
                assert mesh_stats(v, tri)["volume"] == pytest.approx(expected * 7.25, rel=EXACT), (kind, both)
                if not both:
                    assert v[:40].tobytes() == polygon.tobytes()
                if kind == "vertical":
                    assert np.abs(v[40:, :2] - v[:40, :2]).max() == 0.0
                    assert np.abs(v[40:, 2] - v[:40, 2]) == pytest.approx(7.25, abs=1e-9)
    with pytest.raises(StitchError, match="либо вертикаль, либо вектор"):
        extrude_polygon(polygon, distance=1.0, vertical=True, vector=[1.0, 0.0, 0.0])
    with pytest.raises(StitchError, match="в плоскости полигона") as error:
        extrude_polygon(section(L_SHAPE, 0.0), distance=1.0, vertical=True)  # a vertical section
    assert error.value.code == "bad_parameter" and error.value.hint


def test_several_polygons_in_one_mesh() -> None:
    polygons = [section(L_SHAPE, 0.0), section(L_SHAPE[::-1] + [60.0, 0.0], 0.0), section(L_SHAPE * 0.5, 30.0)]
    calls = []
    v, t = extrude_polygons(polygons, distance=4.0, progress=lambda f, m: calls.append((f, m)))
    assert_solid(v, t)
    assert mesh_stats(v, t)["volume"] == pytest.approx((600.0 + 600.0 + 150.0) * 4.0, rel=EXACT)
    assert v[:6].tobytes() == polygons[0].tobytes() and v[12:18].tobytes() == polygons[1].tobytes()
    assert calls[0] == (0.0, "Выдавливание: 1 из 3") and calls[-1] == (1.0, "Готово")
    with pytest.raises(StitchError, match="Полигон 2 вырожден"):
        extrude_polygons([polygons[0], np.array([[0.0, 0, 0], [1, 1, 1], [2, 2, 2]]) + ORIGIN], distance=1.0)
    with pytest.raises(StitchError, match="хотя бы один полигон"):
        extrude_polygons([], distance=1.0)


# --- triangle edits ------------------------------------------------------------------------------------------


def test_edit_triangles_removes_and_adds() -> None:
    polygon = section(L_SHAPE, 0.0)
    v, t = extrude_polygon(polygon, distance=10.0)
    removed = t[[0, 5]]
    # Take two triangles out: the solid is open, the vertices stay (they are still used).
    v1, t1 = edit_triangles(v, t, np.array([0, 5]), np.zeros((0, 3, 3)))
    assert len(t1) == len(t) - 2 and v1.tobytes() == v.tobytes()
    assert not mesh_stats(v1, t1)["closed"]
    # Put them back by coordinates: the same closed solid, no new vertices.
    v2, t2 = edit_triangles(v1, t1, np.array([], dtype=np.int64), v[removed])
    assert v2.tobytes() == v.tobytes() and t2.dtype == np.int32
    assert_solid(v2, t2)
    assert volume(v2, t2) == pytest.approx(6000.0, rel=EXACT)
    # A corner that is not an existing vertex becomes a new one; vertices left unused are dropped.
    apex = v[:6].mean(axis=0) + [0.0, 0.0, 100.0]
    v3, t3 = edit_triangles(v, t, np.arange(1, len(t)), np.array([[v[0], v[1], apex]]))
    assert len(t3) == 2 and len(v3) == len(np.unique(t[0])) + len({0, 1} - set(t[0].tolist())) + 1
    assert (v3[-1] == apex).all()
    with pytest.raises(StitchError, match="вне каркаса"):
        edit_triangles(v, t, np.array([len(t)]), np.zeros((0, 3, 3)))
    with pytest.raises(StitchError, match="вырожден"):
        edit_triangles(v, t, np.array([], dtype=np.int64), np.array([[v[0], v[0], v[1]]]))


def test_edit_triangles_on_a_large_mesh_keeps_the_vertices_bit_for_bit() -> None:
    """−0.0 equals 0.0; a repeated vertex is found by its first number; untouched vertices stay as they were."""
    rng = np.random.default_rng(3)
    v = ORIGIN + rng.uniform(-500.0, 500.0, (300_000, 3))
    v[7] = [0.0, 1.0, 2.0]
    v[11] = [0.0, 1.0, 2.0]  # the same point twice: the first number is used
    t = np.arange(300_000, dtype=np.int64).reshape(-1, 3)
    corners = np.array([[[-0.0, 1.0, 2.0], v[20], v[40]], [v[20], v[41], [9.0, 9.0, 9.0]]])
    nv, nt = edit_triangles(v, t, np.array([0, 3, 13]), corners)
    assert len(nt) == len(t) - 3 + 2
    assert nv[-1].tolist() == [9.0, 9.0, 9.0] and len(nv) == len(v) - 9 + 2 + 1  # 9 dropped, 40 and 41 back, 1 new
    kept = np.ones(len(v), dtype=bool)
    kept[[0, 1, 2, 9, 10, 11, 39, 40, 41]] = False
    kept[[40, 41]] = True  # used again by the added triangles; −0.0, 1, 2 is vertex 7 (11, equal to it, is dropped)
    assert nv[:-1].tobytes() == v[kept].tobytes()
    # The added triangles point at those vertices.
    index = {k: i for i, k in enumerate(np.flatnonzero(kept))}
    assert nt[-2].tolist() == [index[7], index[20], index[40]]
    assert nt[-1].tolist() == [index[20], index[41], len(nv) - 1]
    with pytest.raises(StitchError, match="не остаётся треугольников"):
        edit_triangles(v[:3], t[:1], np.array([0]), None)


# --- real contours -------------------------------------------------------------------------------------------


def real_contours() -> list[tuple[str, np.ndarray]]:
    """Closed lines of the real STR files (Micromine), without the repeated last vertex: (name, xyz)."""
    from bf_core.formats.str_micromine import read_str

    out = []
    for path in sorted(REAL.glob("*.STR")):
        for layer in read_str(path).layers:
            off, closed = layer.offsets, layer.closed
            if off is None:
                continue
            for k in range(len(off) - 1):
                if closed[k]:
                    out.append((f"{path.name}: {layer.name} #{k}", layer.xyz[off[k] : off[k + 1]]))
    return out


@pytest.mark.skipif(not REAL.is_dir() or not list(REAL.glob("*.STR")), reason="нет tests/data/real/*.STR")
def test_real_str_contours_extrude_and_stitch_without_failure() -> None:
    """10.5 п. 3 (ADR 0015): the closed contours of the real STR files — extruded and stitched without a crash.

    One section contour (its vertices are not exactly in one plane) and five plan polygons on one level. All on
    one level: refused as one section. Every contour against its copy moved along its normal: a prism of its
    area. Every contour against a smaller copy: a closed solid. Extrusion: area × distance."""
    contours = real_contours()
    assert len(contours) >= 6
    for name, xyz in contours:
        polygon = xyz[:-1] if np.array_equal(xyz[0], xyz[-1]) else xyz
        area = area_vector(polygon)
        a = float(np.linalg.norm(area))
        normal = area / a
        v, t = extrude_polygon(xyz, distance=15.0)
        assert_solid(v, t)
        assert mesh_stats(v, t)["volume"] == pytest.approx(a * 15.0, rel=EXACT), name
        moved = polygon + 15.0 * normal
        result = stitch_contours([xyz, moved])
        assert_solid(result.vertices, result.triangles)
        assert result.report["volume"] == pytest.approx(a * 15.0, rel=1e-6), name  # not exactly planar: 1e-6
        assert result.vertices[: len(polygon)].tobytes() == polygon.tobytes()
        centre = polygon.mean(axis=0)
        smaller = centre + 0.5 * (polygon - centre) + 20.0 * normal
        for caps in ("point", "line", "extend"):
            result = stitch_contours([xyz, smaller], start_cap=caps, end_cap="flat")
            assert_solid(result.vertices, result.triangles)
    plan = [xyz for name, xyz in contours if np.ptp(xyz[:, 2]) == 0.0]
    with pytest.raises(StitchError, match="на одном разрезе"):
        stitch_contours(plan)
