"""Solids, part 2: clipping by a plane, a polygon and a surface — closed solids and open wireframes (ТЗ 7.23;
ADR 0015 §7, 9.12).

Ported from BF `tests/test_solids.py` with the assertions kept, except where ADR 0015 changes the behaviour:
a surface that does not cover the mesh is refused (BF warned and put the uncovered part «above»). New: the seam
of an open surface is shared bit for bit, an open wireframe cut by a surface («разлом по рельефу»).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.geo.checks import orientation_conflicts
from bf_core.geo.clip import clip_by_plane, clip_by_polygon, clip_by_surface
from bf_core.geo.clip_sheet import sweep_solid
from bf_core.geo.meshes import mesh_stats
from bf_core.geo.solids import SolidError, boolean
from tests.solidutil import L_SHAPE, OFFSET, area, assert_good_solid, box, grid, prism, sphere, volume


def _seam(part: tuple, others: set) -> set:
    return {tuple(p) for p in part[0].tolist()} & others


# --- plane


def test_solid_cut_by_a_level() -> None:
    cube = box((0, 0, 0), (10, 10, 10))
    out = clip_by_plane(cube, OFFSET + [0, 0, 3], [0, 0, 1])
    assert_good_solid(out["positive"])
    assert_good_solid(out["negative"])
    assert volume(out["positive"]) == pytest.approx(700, abs=1e-9)
    assert volume(out["negative"]) == pytest.approx(300, abs=1e-9)
    report = out["report"]
    assert report["kind"] == "solid" and report["volume"] == pytest.approx(1000, abs=1e-9)
    assert report["sum_error"] <= 1e-9 * 1000
    assert out["positive"][0][:, 2].min() == pytest.approx(OFFSET[2] + 3, abs=1e-9)
    only = clip_by_plane(cube, OFFSET + [0, 0, 3], [0, 0, 1], keep="negative")
    assert only["positive"] is None and volume(only["negative"]) == pytest.approx(300, abs=1e-9)
    # A plane that misses the body: everything on one side, the other part is absent.
    missed = clip_by_plane(cube, OFFSET + [0, 0, 30], [0, 0, 1])
    assert missed["positive"] is None and volume(missed["negative"]) == pytest.approx(1000, abs=1e-9)
    with pytest.raises(SolidError, match="нулевой длины"):
        clip_by_plane(cube, OFFSET, [0, 0, 0])
    with pytest.raises(SolidError, match="Неизвестный вариант") as error:
        clip_by_plane(cube, OFFSET, [0, 0, 1], keep="up")
    assert error.value.code == "bad_parameter" and error.value.hint


@pytest.mark.parametrize("body", ["sphere", "non-convex"])
def test_solid_cut_by_a_tilted_plane_keeps_the_volume(body: str) -> None:
    mesh = sphere(5, 48, 96, centre=(4, 4, 5)) if body == "sphere" else prism(L_SHAPE, 0, 10)
    out = clip_by_plane(mesh, OFFSET + [3.3, 4.1, 5.2], [0.3, -0.5, 0.8])
    total = volume(mesh)
    # 9.12 criterion: V+ + V- = V, relative 1e-9.
    assert volume(out["positive"]) + volume(out["negative"]) == pytest.approx(total, rel=1e-9)
    assert out["report"]["sum_error"] <= 1e-9 * total
    assert_good_solid(out["positive"])
    assert_good_solid(out["negative"])
    assert out["report"]["volume_positive"] > 1 and out["report"]["volume_negative"] > 1
    # The cut faces lie on the plane: nothing of a part is on the wrong side (1e-9 m).
    n = np.array([0.3, -0.5, 0.8]) / np.linalg.norm([0.3, -0.5, 0.8])
    assert ((out["positive"][0] - OFFSET - [3.3, 4.1, 5.2]) @ n).min() > -1e-9
    assert ((out["negative"][0] - OFFSET - [3.3, 4.1, 5.2]) @ n).max() < 1e-9


def test_open_surface_cut_by_a_plane() -> None:
    surface = grid(40, 30, (0, 20), (0, 15), lambda x, y: np.sin(x / 3) + 0.2 * y)
    point, normal = OFFSET + [7.77, 3.3, 0], np.array([1.0, 0.4, 0.2])
    copies = [x.copy() for x in surface]
    out = clip_by_plane(surface, point, normal)
    assert all(np.array_equal(x, y) for x, y in zip(surface, copies, strict=True))
    report = out["report"]
    assert report["kind"] == "surface" and not report["closed"]
    total = area(surface)
    # Areas of the parts add up to the surface: relative 1e-12.
    assert area(out["positive"]) + area(out["negative"]) == pytest.approx(total, rel=1e-12)
    assert report["sum_error"] <= 1e-12 * total and report["area"] == pytest.approx(total, rel=1e-12)
    n = normal / np.linalg.norm(normal)
    d_pos = (out["positive"][0] - point) @ n
    d_neg = (out["negative"][0] - point) @ n
    # Every vertex on its own side; the new vertices on the plane within 1e-9 m.
    assert d_pos.min() > -1e-9 and d_neg.max() < 1e-9
    assert np.count_nonzero(np.abs(d_pos) < 1e-9) == np.count_nonzero(np.abs(d_neg) < 1e-9) > 40
    # The seam is shared bit for bit: the vertices of the cut are the same float64 numbers in both parts.
    seam_pos = {tuple(p) for p in out["positive"][0][np.abs(d_pos) < 1e-9].tolist()}
    seam_neg = {tuple(p) for p in out["negative"][0][np.abs(d_neg) < 1e-9].tolist()}
    assert seam_pos == seam_neg and len(seam_pos) > 40
    # The vertices of the source reach the parts bit for bit.
    kept = {tuple(p) for p in np.vstack([out["positive"][0], out["negative"][0]]).tolist()}
    assert all(tuple(p) in kept for p in surface[0].tolist())
    # No caps and no cracks: the cut is one open boundary, the parts stay manifold sheets.
    for part in (out["positive"], out["negative"]):
        stats = mesh_stats(*part)
        assert not stats["closed"] and stats["nonmanifold_edges"] == 0 and stats["degenerate"] == 0
        assert orientation_conflicts(part[1], len(part[0])) == 0
    # Glued back by equal coordinates: one sheet whose border is only the outer border of the source.
    joined_v = np.vstack([out["positive"][0], out["negative"][0]])
    uniq, index = np.unique(joined_v, axis=0, return_inverse=True)
    joined_t = index.reshape(-1)[np.vstack([out["positive"][1], out["negative"][1] + len(out["positive"][0])])]
    edges = np.sort(np.concatenate([joined_t[:, [0, 1]], joined_t[:, [1, 2]], joined_t[:, [2, 0]]]), axis=1)
    keys, counts = np.unique(edges, axis=0, return_counts=True)
    border = uniq[keys[counts == 1].reshape(-1)] - OFFSET
    on_frame = np.isclose(border[:, 0], 0) | np.isclose(border[:, 0], 20) | np.isclose(border[:, 1], 0)
    assert (on_frame | np.isclose(border[:, 1], 15)).all() and counts.max() == 2


@pytest.mark.parametrize("level", [0.0, 1e-9, 1e-6, 1e-3, 5.0, 10 - 1e-9, 10.0])
def test_cube_cut_at_and_near_its_faces(level: float) -> None:
    """Hard data (ТЗ 17 п. 5): the plane on a face of the cube, or within 1e-9…1e-3 of it, at UTM."""
    cube = box((0, 0, 0), (10, 10, 10))
    out = clip_by_plane(cube, OFFSET + [0, 0, level], [0, 0, 1])
    upper = 0.0 if out["positive"] is None else volume(out["positive"])
    lower = 0.0 if out["negative"] is None else volume(out["negative"])
    assert upper + lower == pytest.approx(1000, abs=1e-9)
    assert upper == pytest.approx(100 * (10 - level), abs=1e-6) and lower == pytest.approx(100 * level, abs=1e-6)
    for part in (out["positive"], out["negative"]):
        if part is not None and volume(part) > 1e-3:
            assert_good_solid(part)


def test_flat_surface_cut_through_its_vertices() -> None:
    """The plane passes exactly through grid nodes: known areas, no empty triangles."""
    surface = grid(10, 10, (0, 10), (0, 10), offset=np.zeros(3))
    out = clip_by_plane(surface, [4, 0, 0], [1, 0, 0])
    assert area(out["positive"]) == pytest.approx(60, abs=1e-12)
    assert area(out["negative"]) == pytest.approx(40, abs=1e-12)
    assert len(out["positive"][1]) == 120 and len(out["negative"][1]) == 80
    diagonal = clip_by_plane(surface, [0, 0, 0], [1, -1, 0], keep="positive")
    assert diagonal["negative"] is None and area(diagonal["positive"]) == pytest.approx(50, abs=1e-12)
    assert mesh_stats(*diagonal["positive"])["degenerate"] == 0


# --- polygon


def test_solid_cut_by_a_polygon() -> None:
    cube = box((0, 0, 0), (10, 10, 10))
    strip = np.array([[2, -5], [7, -5], [7, 15], [2, 15]]) + OFFSET[:2]
    out = clip_by_polygon(cube, strip, keep="both")
    assert_good_solid(out["inside"])
    assert_good_solid(out["outside"])
    assert volume(out["inside"]) == pytest.approx(500, abs=1e-9)
    assert volume(out["outside"]) == pytest.approx(500, abs=1e-9)
    assert out["report"]["sum_error"] <= 1e-9 * 1000
    # Default: the inside only; a clockwise or explicitly closed contour is the same polygon.
    closed_cw = np.vstack([strip[::-1], strip[-1:]])
    only = clip_by_polygon(cube, closed_cw)
    assert only["outside"] is None and volume(only["inside"]) == pytest.approx(500, abs=1e-9)


def test_solid_cut_by_a_non_convex_polygon_keeps_the_volume() -> None:
    ball = sphere(5, 48, 96, centre=(5, 5, 5))
    contour = np.array(L_SHAPE, dtype=np.float64) * 0.8 + [1.3, 0.7] + OFFSET[:2]
    out = clip_by_polygon(ball, contour, keep="both")
    total = volume(ball)
    # Sum of the parts = the source solid: relative 1e-9.
    assert volume(out["inside"]) + volume(out["outside"]) == pytest.approx(total, rel=1e-9)
    assert out["report"]["sum_error"] <= 1e-9 * total
    assert_good_solid(out["inside"])
    assert_good_solid(out["outside"])
    assert 0.2 * total < out["report"]["volume_inside"] < 0.8 * total


def test_dtm_cut_by_a_polygon() -> None:
    """A DTM cut by a licence boundary: areas known for a plane, add up for a relief, the seam shared."""
    flat = grid(20, 20, (0, 20), (0, 20), lambda x, y: 0.5 * x)  # tilted plane
    contour = np.array(L_SHAPE, dtype=np.float64) + [3.3, 2.7] + OFFSET[:2]
    out = clip_by_polygon(flat, contour, keep="both")
    stretch = math.sqrt(1 + 0.25)
    # Plan area × the slope factor; tolerance 1e-9 relative.
    assert area(out["inside"]) == pytest.approx(64 * stretch, rel=1e-9)
    assert area(out["outside"]) == pytest.approx((400 - 64) * stretch, rel=1e-9)
    assert out["report"]["kind"] == "surface" and out["report"]["direction"] == [0, 0, 1]
    # The parts lie on the source surface (1e-9 m) and inside / outside the contour.
    for part in (out["inside"], out["outside"]):
        p = part[0] - OFFSET
        assert np.abs(p[:, 2] - 0.5 * p[:, 0]).max() < 1e-9
        assert not mesh_stats(*part)["closed"] and mesh_stats(*part)["nonmanifold_edges"] == 0
    inside = out["inside"][0][:, :2] - OFFSET[:2] - [3.3, 2.7]
    assert inside.min() > -1e-9 and inside.max() < 10 + 1e-9
    # The seam: the new vertices of the inside part (on the contour) are vertices of the outside part, bit for bit.
    source = {tuple(p) for p in flat[0].tolist()}
    new = {tuple(p) for p in out["inside"][0].tolist()} - source
    assert len(new) > 20 and new <= {tuple(p) for p in out["outside"][0].tolist()}

    relief = grid(60, 60, (0, 20), (0, 20), lambda x, y: np.sin(x / 2) * np.cos(y / 3) * 2)
    out = clip_by_polygon(relief, contour, keep="both")
    total = area(relief)
    # 9.12 criterion: the areas of the parts add up to the whole, relative 1e-9.
    assert area(out["inside"]) + area(out["outside"]) == pytest.approx(total, rel=1e-9)
    assert out["report"]["sum_error"] <= 1e-9 * total
    # Away from the cut the triangulation of the surface is kept: its vertices are still there, bit for bit.
    kept = {tuple(p) for p in np.vstack([out["inside"][0], out["outside"][0]]).tolist()}
    assert all(tuple(p) in kept for p in relief[0].tolist())
    only = clip_by_polygon(relief, contour, keep="outside")
    assert only["inside"] is None and area(only["outside"]) == pytest.approx(area(out["outside"]), rel=1e-12)


def test_bad_polygons_are_refused() -> None:
    cube = box((0, 0, 0), (10, 10, 10))
    with pytest.raises(SolidError, match="не меньше трёх"):
        clip_by_polygon(cube, [[0, 0], [1, 1]])
    bow = np.array([[0, 0], [10, 10], [10, 0], [0, 10]]) + OFFSET[:2]
    with pytest.raises(SolidError, match="самопересекается") as error:
        clip_by_polygon(cube, bow)
    assert error.value.code == "bad_polygon" and "простой" in error.value.hint


# --- sweeping and clipping by a surface


def test_sweep_solid() -> None:
    surface = grid(12, 8, (0, 12), (0, 8), lambda x, y: 0.3 * x + np.sin(y))
    v, t = sweep_solid(surface, [0, 0, -1], 7.0)
    assert_good_solid((v, t))
    # Plan area × length, whatever the relief: 1e-9 relative.
    assert volume((v, t)) == pytest.approx(12 * 8 * 7, rel=1e-9)
    assert len(t) == 2 * len(surface[1]) + 2 * 2 * (12 + 8)
    # Upwards, and with the triangles of the surface turned over: the same body.
    up = sweep_solid((surface[0], surface[1][:, ::-1]), [0, 0, 2], 7.0)
    assert_good_solid(up)
    assert volume(up) == pytest.approx(12 * 8 * 7, rel=1e-9)
    # Oblique direction: base area × length × cosine.
    oblique = sweep_solid(grid(4, 4, (0, 4), (0, 4)), [1, 0, -1], 5.0)
    assert_good_solid(oblique)
    assert volume(oblique) == pytest.approx(16 * 5 / math.sqrt(2), rel=1e-9)


def test_sweep_refuses_a_surface_that_is_not_a_sheet() -> None:
    # A vertical wall seen along Z.
    wall = grid(4, 4, (0, 4), (0, 4))
    wall = (wall[0][:, [0, 2, 1]], wall[1])
    with pytest.raises(SolidError, match="параллельных направлению") as error:
        sweep_solid(wall, [0, 0, 1], 5.0)
    assert error.value.code == "not_a_sheet"
    assert mesh_stats(*sweep_solid(wall, [0, 1, 0], 5.0))["closed"]
    # A fold: the sheet turns back over itself.
    v, t = grid(8, 4, (0, 8), (0, 4), offset=np.zeros(3))
    folded = v.copy()
    over = v[:, 0] > 4
    folded[over, 0] = 8 - v[over, 0]
    folded[:, 2] = np.where(over, (v[:, 0] - 4) * 0.5, 0.0)
    with pytest.raises(SolidError, match="складки"):
        sweep_solid((folded, t), [0, 0, 1], 5.0)
    # Two separate sheets one above the other.
    upper = grid(8, 4, (2, 6), (1, 3), lambda x, y: 0 * x + 3, offset=np.zeros(3))
    stacked = (np.vstack([v, upper[0]]), np.vstack([t, upper[1] + len(v)]))
    with pytest.raises(SolidError, match="перекрывают друг друга"):
        sweep_solid(stacked, [0, 0, 1], 5.0)
    with pytest.raises(SolidError, match="положительной"):
        sweep_solid((v, t), [0, 0, 1], 0.0)


def test_solid_cut_by_a_level_surface() -> None:
    cube = box((0, 0, 0), (10, 10, 10))
    dtm = grid(7, 9, (-3, 13), (-2, 12), lambda x, y: 0 * x + 4)
    out = clip_by_surface(cube, dtm)
    report = out["report"]
    assert report["covers"] and report["direction"] == [0, 0, 1]
    assert volume(out["above"]) == pytest.approx(600, abs=1e-9)
    assert volume(out["below"]) == pytest.approx(400, abs=1e-9)
    assert report["volume_above"] == pytest.approx(600, abs=1e-9)
    assert report["sum_error"] <= 1e-9 * 1000
    assert_good_solid(out["above"])
    assert_good_solid(out["below"])
    for keep, other in (("above", "below"), ("below", "above")):
        only = clip_by_surface(cube, dtm, keep=keep)
        assert only[other] is None and only[keep] is not None
    # The surface turned over is the same surface: "above" is still +Z.
    turned = clip_by_surface(cube, (dtm[0], dtm[1][:, ::-1]))
    assert turned["report"]["volume_above"] == pytest.approx(600, abs=1e-9)


def test_solid_cut_by_an_undulating_surface() -> None:
    ball = sphere(5, 48, 96, centre=(5, 5, 5))

    def relief(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return 5 + 1.5 * np.sin(x / 2) * np.cos(y / 3) + 0.1 * x

    dtm = grid(80, 80, (-2, 12), (-2, 12), relief)
    out = clip_by_surface(ball, dtm)
    report = out["report"]
    total = volume(ball)
    # 9.12 criterion: the parts add up to the source solid, relative 1e-9.
    assert volume(out["above"]) + volume(out["below"]) == pytest.approx(total, rel=1e-9)
    assert report["sum_error"] <= 1e-9 * total and report["covers"]
    assert_good_solid(out["above"])
    assert_good_solid(out["below"])
    assert 0.3 * total < report["volume_below"] < 0.7 * total
    # The parts lie on their sides of the surface (0.02 m: the surface is piecewise linear).
    above, below = out["above"][0] - OFFSET, out["below"][0] - OFFSET
    assert (above[:, 2] - relief(above[:, 0], above[:, 1])).min() > -0.02
    assert (below[:, 2] - relief(below[:, 0], below[:, 1])).max() < 0.02
    # The same cut as a boolean with the swept surface (independent path): 1e-9 relative.
    swept = sweep_solid(dtm, [0, 0, -1], 50.0)
    assert boolean(ball, swept, "intersection")[2]["volume"] == pytest.approx(report["volume_below"], rel=1e-9)


def test_solid_cut_by_a_tilted_fault() -> None:
    """A steep plane through a cube: the direction is taken from the surface's own normal."""
    cube = box((0, 0, 0), (10, 10, 10))
    # The plane x + 0.2 z = 6 as a sheet parametrised by (y, z): vertical in plan view.
    v, t = grid(10, 10, (-5, 15), (-5, 15), offset=np.zeros(3))
    fault = np.column_stack([6 - 0.2 * v[:, 1], v[:, 0], v[:, 1]]) + OFFSET
    out = clip_by_surface(cube, (fault, t), direction=[1, 0, 0.2])
    # Below (x + 0.2 z < 6): a prism with the trapezoid 6 … 4 wide: 10 × (6 + 4) / 2 × 10.
    assert out["report"]["volume_below"] == pytest.approx(500, abs=1e-9)
    assert out["report"]["volume_above"] == pytest.approx(500, abs=1e-9)
    assert_good_solid(out["above"])
    assert_good_solid(out["below"])
    # Default direction Z: along Z the steep sheet covers only a strip of the cube — refused (ADR 0015).
    with pytest.raises(SolidError) as error:
        clip_by_surface(cube, (fault, t))
    assert error.value.code == "surface_not_covering" and "по её нормали" in error.value.hint
    # "normal": the dominant normal of the sheet, (1, 0, 0.2) up to sign and length.
    normal = clip_by_surface(cube, (fault, t), direction="normal")
    axis = np.array(normal["report"]["direction"])
    assert np.abs(axis) == pytest.approx(np.array([1, 0, 0.2]) / math.sqrt(1.04), abs=1e-12)
    assert normal["report"]["volume_above"] == pytest.approx(500, abs=1e-9)
    # A vertical fault: Z is impossible, the dominant normal is used.
    wall = np.column_stack([np.full(len(v), 3.0), v[:, 0], v[:, 1]]) + OFFSET
    vertical = clip_by_surface(cube, (wall, t))
    assert np.abs(vertical["report"]["direction"]) == pytest.approx([1, 0, 0], abs=1e-12)
    sign = vertical["report"]["direction"][0]
    assert vertical["report"]["volume_above"] == pytest.approx(700 if sign > 0 else 300, abs=1e-9)
    assert vertical["report"]["sum_error"] <= 1e-9 * 1000


def test_surface_that_does_not_cover_the_solid_is_refused() -> None:
    cube = box((0, 0, 0), (10, 10, 10))
    dtm = grid(6, 10, (-2, 6), (-2, 12), lambda x, y: 0 * x + 4)  # covers x < 6 only
    with pytest.raises(SolidError) as error:
        clip_by_surface(cube, dtm, names=("Карьер", "Рельеф"))
    e = error.value
    assert e.code == "surface_not_covering"
    assert e.message == (
        "Поверхность «Рельеф» не перекрывает тело «Карьер» вдоль направления обрезки: непокрытая часть — 400,00 м³."
    )
    # A separate part of the solid entirely beside the surface.
    far = box((20, 0, 0), (25, 5, 5))
    two = (np.vstack([cube[0], far[0]]), np.vstack([cube[1], far[1] + 8]))
    wide = grid(8, 8, (-2, 12), (-2, 12), lambda x, y: 0 * x + 4)
    with pytest.raises(SolidError, match="125,00 м³"):
        clip_by_surface(two, wide)
    assert clip_by_surface(cube, wide)["report"]["volume_below"] == pytest.approx(400, abs=1e-9)
    # A surface that misses the solid altogether.
    with pytest.raises(SolidError, match="1000,00 м³"):
        clip_by_surface(cube, grid(4, 4, (50, 60), (50, 60)))


def test_clip_by_surface_refuses_bad_inputs() -> None:
    cube = box((0, 0, 0), (10, 10, 10))
    dtm = grid(4, 4, (-2, 12), (-2, 12), lambda x, y: 0 * x + 4)
    nonmanifold = (np.vstack([cube[0], cube[0][0] + [5, 5, 30]]), np.vstack([cube[1], [[0, 1, 8]]]))
    with pytest.raises(SolidError, match="Карьер.*трёх и более"):
        clip_by_surface(nonmanifold, dtm, names=("Карьер", "Рельеф"))
    with pytest.raises(SolidError, match="Рельеф.*не однозначна") as error:
        clip_by_surface(cube, dtm, direction=[1, 0, 0], names=("Карьер", "Рельеф"))
    assert error.value.code == "not_a_sheet"
    with pytest.raises(SolidError, match="пуст"):
        clip_by_surface(cube, (dtm[0], dtm[1][:0]))


# --- an open wireframe cut by a surface («разлом по рельефу», ADR 0015 decision 10)


def _fault() -> tuple[np.ndarray, np.ndarray]:
    """The plane x = 5 + 0.3 (z − 5) as a sheet over y ∈ [0, 10], z ∈ [−5, 15]."""
    v, t = grid(10, 20, (0, 10), (-5, 15), offset=np.zeros(3))
    return np.column_stack([5 + 0.3 * (v[:, 1] - 5), v[:, 0], v[:, 1]]) + OFFSET, t


def test_fault_cut_by_a_relief() -> None:
    fault = _fault()

    def relief(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return 4 + 0.5 * np.sin(x / 2) * np.cos(y / 3) + 0.05 * x

    dtm = grid(40, 40, (-5, 15), (-5, 15), relief)
    copies = [x.copy() for x in (*fault, *dtm)]
    out = clip_by_surface(fault, dtm, names=("Разлом", "Рельеф"))
    assert all(np.array_equal(x, y) for x, y in zip((*fault, *dtm), copies, strict=True))
    report = out["report"]
    assert report["kind"] == "surface" and not report["closed"] and report["direction"] == [0, 0, 1]
    total = area(fault)
    # 9.12 criterion: the areas of the parts add up to the whole, relative 1e-9.
    assert area(out["above"]) + area(out["below"]) == pytest.approx(total, rel=1e-9)
    assert report["sum_error"] <= 1e-9 * total
    # The parts stay on the fault (1e-9 m) and on their sides of the relief (0.02 m: piecewise linear).
    for name in ("above", "below"):
        p = out[name][0] - OFFSET
        assert np.abs(p[:, 0] - 5 - 0.3 * (p[:, 2] - 5)).max() < 1e-9
        stats = mesh_stats(*out[name])
        assert not stats["closed"] and stats["nonmanifold_edges"] == 0
    above, below = out["above"][0] - OFFSET, out["below"][0] - OFFSET
    assert (above[:, 2] - relief(above[:, 0], above[:, 1])).min() > -0.02
    assert (below[:, 2] - relief(below[:, 0], below[:, 1])).max() < 0.02
    # The seam is one line across the fault, shared by both parts bit for bit.
    seam = _seam(out["above"], {tuple(p) for p in out["below"][0].tolist()})
    assert len(seam) >= 11
    # Away from the cut the vertices of the fault are kept bit for bit.
    kept = {tuple(p) for p in np.vstack([out["above"][0], out["below"][0]]).tolist()}
    assert all(tuple(p) in kept for p in fault[0].tolist())


def test_fault_beside_the_relief_is_refused() -> None:
    small = grid(4, 4, (0, 4), (0, 10), lambda x, y: 0 * x + 4)  # covers x < 4, the fault is at x ≈ 5
    with pytest.raises(SolidError) as error:
        clip_by_surface(_fault(), small, names=("Разлом", "Рельеф"))
    assert error.value.code == "surface_not_covering" and "каркас «Разлом»" in error.value.message
    assert "м²" in error.value.message


def test_clipping_ticks_and_may_cancel() -> None:
    cube = box((0, 0, 0), (10, 10, 10))
    dtm = grid(7, 9, (-3, 13), (-2, 12), lambda x, y: 0 * x + 4)
    for run in (
        lambda tick: clip_by_plane(cube, OFFSET + [0, 0, 3], [0, 0, 1], tick=tick),
        lambda tick: clip_by_polygon(dtm, np.array(L_SHAPE, dtype=np.float64) + OFFSET[:2], tick=tick),
        lambda tick: clip_by_surface(cube, dtm, tick=tick),
    ):
        seen: list[float] = []
        run(lambda fraction, message, seen=seen: seen.append(fraction))
        assert seen == sorted(seen) and seen[0] < seen[-1] == 1.0

        def stop(fraction: float, message: str) -> None:
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            run(stop)
