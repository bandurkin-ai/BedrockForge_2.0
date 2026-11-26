"""The solid of a pit (подшаг 21.12; ТЗ 8.11.4 «Выходы»; ADR 0028 решение 26, находка 6): the body between the relief
and the surface of a shell on one grid of nodes (`mine/solid.py`, variant Б), the fallback (variant А, the difference of
two `under_solid`). The solid is closed (`is_closed_solid`, manifold3d), its volume is Σ d·A of the columns of the grid
of centres, two pits are two parts, two pits that touch by a corner are two parts that share no vertex, a notch is taken
by variant А, a node outside the relief is not built, a model turned about Z gives the same body. Plain numpy: the
nodes and the thicknesses are made here by hand; the jobs are `test_pit_dtm_job.py`."""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from bf_core.geo import solids as gs
from bf_core.mine import solid as so

DXY, DZ, TOP = 10.0, 10.0, 100.0
A = DXY * DXY


def nodes(nx: int, ny: int, angle: float = 0.0, origin: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """The centres of the columns of a grid (column c = i + nx·j), turned about Z by `angle` degrees."""
    xs, ys = np.meshgrid((np.arange(nx) + 0.5) * DXY, (np.arange(ny) + 0.5) * DXY)
    q = np.column_stack([xs.ravel(), ys.ravel()])
    a = math.radians(angle)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return q @ rot.T + np.asarray(origin)


def depth_grid(nx: int, ny: int, cells: dict[tuple[int, int], int]) -> np.ndarray:
    d = np.zeros((ny, nx))
    for (i, j), m in cells.items():
        d[j, i] = m
    return d


def run(d: np.ndarray, top: np.ndarray | float = TOP, angle: float = 0.0) -> tuple[so.Solid, np.ndarray]:
    ny, nx = d.shape
    xy = nodes(nx, ny, angle)
    t = np.full(nx * ny, top) if np.isscalar(top) else np.asarray(top, dtype=np.float64)
    return so.between(xy, t, t - d.ravel() * DZ, nx, ny), xy


def assert_closed(s: so.Solid) -> None:
    ok, why = gs.is_closed_solid(s.xyz, s.tri)
    assert ok, why
    gs.to_manifold(s.xyz - gs.local_origin(s.xyz), s.tri, "Карьер")  # manifold3d accepts it
    assert s.volume > 0 and s.method == "between"


def prisms(d: np.ndarray) -> float:
    return float(d.sum() * DZ * A)


BOWL = {(i, j): max(0, 3 - max(abs(i - 5), abs(j - 4))) for i in range(11) for j in range(9)}


# --- the DTM of a shell -----------------------------------------------------------------------------------------------


def test_the_cell_has_two_triangles_that_face_up() -> None:
    tri = so.cell_triangles(4, 3)
    assert tri.shape == (2 * 3 * 2, 3) and tri.dtype == np.int64
    xy = nodes(4, 3)
    a, b, c = (xy[tri[:, k]] for k in range(3))
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    assert (cross > 0).all()  # counter-clockwise: the normal looks up
    assert so.cell_triangles(1, 5).shape == (0, 3)


def test_a_surface_has_the_nodes_it_knows_and_a_nan_node_is_not_built() -> None:
    xy = nodes(5, 4)
    z = np.arange(20, dtype=float)
    xyz, tri = so.surface_mesh(xy, z, 5, 4)
    assert xyz.shape == (20, 3) and tri.shape == (2 * 4 * 3, 3) and tri.dtype == np.int32
    assert np.array_equal(xyz[:, 2], z)
    z[7] = np.nan  # (2, 1): the four cells around it lose their triangles that use it
    xyz, tri = so.surface_mesh(xy, z, 5, 4)
    assert not np.isnan(xyz).any() and len(xyz) == 19 and len(tri) == 24 - 6  # a node has six triangles
    z[:] = np.nan
    assert so.surface_mesh(xy, z, 5, 4)[1].shape == (0, 3)  # nothing: the job answers for it


# --- the solid --------------------------------------------------------------------------------------------------------


def test_the_solid_is_closed_and_its_volume_is_the_prisms_of_the_columns() -> None:
    d = depth_grid(11, 9, BOWL)
    s, _ = run(d)
    assert_closed(s)
    assert s.parts == 1 and s.volume == pytest.approx(prisms(d), rel=1e-12)


def test_the_volume_does_not_depend_on_the_relief() -> None:
    d = depth_grid(11, 9, BOWL)
    xy = nodes(11, 9)
    top = 100.0 + 0.3 * xy[:, 0] - 0.1 * xy[:, 1]  # a sloping relief: the bottom follows it down by d
    s = so.between(xy, top, top - d.ravel() * DZ, 11, 9)
    assert_closed(s)
    assert s.volume == pytest.approx(prisms(d), rel=1e-12)


def test_two_pits_are_two_parts() -> None:
    d = depth_grid(14, 10, {(2, 2): 1, (3, 2): 1, (2, 3): 1, (3, 3): 2, (9, 6): 3, (10, 6): 2, (9, 7): 2})
    s, _ = run(d)
    assert_closed(s)
    assert s.parts == 2 and s.volume == pytest.approx(prisms(d), rel=1e-12)


def test_two_pits_that_touch_by_a_corner_are_two_parts_that_share_no_vertex() -> None:
    # (3, 3) and (4, 4) are joined by the diagonal of a cell: one pit
    d = depth_grid(9, 9, {(3, 3): 1, (4, 4): 2})
    s, _ = run(d)
    assert_closed(s)
    assert s.parts == 1 and s.volume == pytest.approx(prisms(d), rel=1e-12)
    # (4, 3) and (3, 4) are the other diagonal: no triangle joins them, the pits touch at the corner of the cell
    d = depth_grid(9, 9, {(4, 3): 1, (3, 4): 2})
    s, _ = run(d)
    assert_closed(s)
    assert s.parts == 2 and s.volume == pytest.approx(prisms(d), rel=1e-12)
    # no vertex index is shared: the vertices that sit in the same place are two (a pinched vertex is not a manifold)
    v = s.xyz
    labels = gs.part_labels(s.tri, len(v))
    pieces = [set(map(tuple, v[labels == k].tolist())) for k in np.unique(labels)]
    assert len(pieces) == 2 and pieces[0] & pieces[1]  # they touch at a point


def test_a_node_inside_the_pit_that_is_not_mined_does_not_pinch_the_solid() -> None:
    d = depth_grid(11, 9, {(i, j): 2 for i in range(3, 8) for j in range(2, 7)})
    d[4, 5] = 0  # an island: the node is on the seam, its eight neighbours are mined
    s, _ = run(d)
    assert_closed(s)
    assert s.volume == pytest.approx(prisms(d), rel=1e-12)


def test_a_bottom_above_the_relief_is_no_pit() -> None:
    """A parent partly under a sloping relief can have its bottom above the relief at the centre of its column: the
    thickness there is 0, not negative (the node is on the seam)."""
    d = depth_grid(11, 9, BOWL)
    xy = nodes(11, 9)
    top = np.full(len(xy), TOP)
    bottom = top - d.ravel() * DZ
    rim = int(np.flatnonzero(d.ravel() == 1)[0])  # a node of the rim: a thickness of one level
    bottom[rim] = TOP + 3.0
    s = so.between(xy, top, bottom, 11, 9)
    assert_closed(s)
    assert s.volume == pytest.approx(prisms(d) - DZ * A, rel=1e-12)  # that node gives nothing
    assert s.xyz[:, 2].max() == TOP  # nothing is built above the relief


def test_a_pit_at_the_edge_of_the_grid_has_a_wall() -> None:
    d = depth_grid(8, 6, {(0, 0): 1, (1, 0): 2, (0, 1): 1, (1, 1): 2, (2, 1): 1, (6, 5): 1, (7, 5): 2})
    s, xy = run(d)
    assert_closed(s)
    top = np.full(len(xy), TOP)
    other = so.difference(xy, top, top - d.ravel() * DZ, 8, 6)  # the same body made the other way
    assert s.volume == pytest.approx(other.volume, rel=1e-9) and s.parts == other.parts == 2
    assert s.volume < prisms(d)  # the solid spans from the first to the last centre: the edge columns count for less


def test_a_node_without_a_relief_is_not_built_and_its_boundary_is_a_wall() -> None:
    d = depth_grid(11, 9, BOWL)
    xy = nodes(11, 9)
    top = np.full(len(xy), TOP)
    top[xy[:, 0] > 65] = np.nan  # the relief does not cover the east of the plan: it cuts the pit
    s = so.between(xy, top, top - d.ravel() * DZ, 11, 9)
    assert_closed(s)
    other = so.difference(xy, top, top - d.ravel() * DZ, 11, 9)
    assert s.volume == pytest.approx(other.volume, rel=1e-9) and s.volume < prisms(d)
    assert not np.isnan(s.xyz).any()


def test_a_model_turned_about_z_gives_the_same_body() -> None:
    d = depth_grid(11, 9, BOWL)
    ny, nx = d.shape
    flat, _ = run(d)
    xy = nodes(nx, ny, angle=30.0) + np.array([5_000_000.0, 7_000_000.0])  # the numbers of a UTM zone
    turned = so.between(xy, np.full(nx * ny, TOP), TOP - d.ravel() * DZ, nx, ny)
    assert_closed(turned)
    assert turned.volume == pytest.approx(flat.volume, rel=1e-9) and turned.parts == flat.parts
    assert turned.xyz[:, 0].min() > 4_999_000.0  # it is where the model is


def test_the_notch_is_taken_by_the_fallback() -> None:
    # the nodes a = (5, 4) and e = (6, 5) are not mined, the rest of their fans is: the two triangles of the cell on
    # both sides of the edge a–e are one run round a and round e — the weld would give that edge four triangles
    d = depth_grid(12, 9, {(i, j): 2 for i in range(2, 10) for j in range(1, 8)})
    d[4, 5] = d[5, 6] = 0
    xy = nodes(12, 9)
    top = np.full(len(xy), TOP)
    bottom = top - d.ravel() * DZ
    with pytest.raises(gs.SolidError):
        so.between(xy, top, bottom, 12, 9)
    built = so.build(xy, top, bottom, 12, 9)
    assert built.method == "difference" and gs.is_closed_solid(built.xyz, built.tri)[0]
    assert built.volume == pytest.approx(prisms(d), rel=1e-9) and built.parts == 1


def test_a_knife_edge_that_variant_b_cannot_close_goes_to_variant_a() -> None:
    """The random pits of a checkerboard of depths make every kind of contact; whatever Б cannot close, А does, and the
    volume of both is that of the prisms (the pit is inside the grid)."""
    xy = nodes(12, 10)
    top = np.full(len(xy), TOP)
    methods = set()
    for seed in range(12):
        rng = np.random.default_rng(seed)
        d = np.zeros((10, 12))
        d[1:-1, 1:-1] = rng.integers(0, 3, (8, 10))
        s = so.build(xy, top, top - d.ravel() * DZ, 12, 10)
        methods.add(s.method)
        assert gs.is_closed_solid(s.xyz, s.tri)[0]
        assert s.volume == pytest.approx(prisms(d), rel=1e-9)
    assert methods == {"between", "difference"}


def test_nothing_mined_is_empty() -> None:
    xy = nodes(6, 5)
    top = np.full(len(xy), TOP)
    with pytest.raises(gs.SolidError) as e:
        so.build(xy, top, top.copy(), 6, 5)
    assert e.value.code == "empty_mesh"
    with pytest.raises(gs.SolidError):
        so.build(xy, np.full(len(xy), np.nan), top, 6, 5)  # no relief anywhere: nothing is known


def test_the_same_nodes_give_the_same_bytes() -> None:
    d = depth_grid(11, 9, BOWL)
    a, _ = run(d)
    b, _ = run(d)
    assert a.xyz.tobytes() == b.xyz.tobytes() and a.tri.tobytes() == b.tri.tobytes()


@pytest.mark.slow
def test_a_shell_of_a_million_columns_is_built_in_seconds() -> None:
    nx = ny = 1000
    xy = nodes(nx, ny)
    top = 100.0 + 0.01 * xy[:, 0]
    r = np.hypot(xy[:, 0] - 5000, xy[:, 1] - 5000)
    depth = np.floor(np.clip(4000 - r, 0, None) * 0.2 / DZ) * DZ  # a cone that fills the plan
    t0 = time.perf_counter()
    s = so.between(xy, top, top - depth, nx, ny)
    assert time.perf_counter() - t0 <= 10.0
    assert s.parts == 1 and s.volume == pytest.approx(float(depth.sum() * A), rel=1e-9)
