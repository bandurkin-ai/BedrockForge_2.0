"""Filling by wireframes, stages «Столбцы» and «Граничные родители» (подшаг 17.17; ТЗ 8.7.3, Т8.7-1; ADR 0023 §6):
the parity of the vertical rays through the centres of the parents, the offset of the rays, the boundary parents
by the axes of separation, an open wireframe → 422, the same bits with 1, 2, 4 threads."""

from __future__ import annotations

import hashlib
import itertools
import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gen.mesh import uv_sphere  # noqa: E402

from bf_core.api.errors import ApiError  # noqa: E402
from bf_core.blocks.fill import boundary as fb  # noqa: E402
from bf_core.blocks.fill import columns as fc  # noqa: E402
from bf_core.blocks.schema import GridV2  # noqa: E402


def grid_of(counts: tuple[int, int, int], size: float = 10.0, rotation: float = 0.0) -> GridV2:
    return GridV2(corner=(0.0, 0.0, 0.0), counts=counts, size=(size, size, size), rotation=rotation)


def box(lo: tuple[float, ...], hi: tuple[float, ...]) -> tuple[np.ndarray, np.ndarray]:
    """An axis-aligned box, outward normals; the diagonals of the top and the bottom go along x = y."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = np.array(
        [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]
    )
    t = [[0, 3, 2], [0, 2, 1], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2],
         [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]]  # fmt: skip
    return v, np.array(t, dtype=np.int32)


def sha(*arrays: np.ndarray) -> str:
    h = hashlib.sha256()
    for a in arrays:
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def lattice(grid: GridV2) -> np.ndarray:
    """n×3 local centres of the parents, in the order of P."""
    nx, ny, nz = grid.counts
    dx, dy, dz = grid.regular()
    k, j, i = np.meshgrid(np.arange(nz), np.arange(ny), np.arange(nx), indexing="ij")
    return np.stack([(i.ravel() + 0.5) * dx, (j.ravel() + 0.5) * dy, (k.ravel() + 0.5) * dz], axis=1)


def corners(centres: np.ndarray, size: float) -> np.ndarray:
    """n×8×3 corners of the parents."""
    s = np.array(list(itertools.product((-0.5, 0.5), repeat=3))) * size
    return centres[:, None, :] + s[None, :, :]


# --- Т8.7-1 ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("rotation", [0.0, 30.0])
def test_t871_cube_100_m_on_parents_10_m(rotation: float) -> None:
    """A cube of 100 m on a grid of 10 m, edges on the borders of the parents: 1000 parents inside, none on the
    boundary (a face on the border of a parent only touches it), the volume 10⁶; with the grid turned too."""
    grid = grid_of((14, 14, 14), rotation=rotation)
    v, t = box((20.0, 20.0, 20.0), (120.0, 120.0, 120.0))
    mesh = fc.Mesh.of_project(grid, grid.to_project(v), t, "Куб")
    assert np.abs(mesh.v - v).max() < 1e-12
    cols = fc.inside_columns(grid, mesh)
    assert cols.count == 1000 and cols.orientation == "outward"
    assert cols.crossings == 2 * 10 * 10 and cols.rays >= 100  # a ray per column of the box (and its margin)
    assert cols.count * 10.0**3 == 1e6
    b = fb.boundary_parents(grid, mesh)
    assert len(b.p) == 0 and b.pairs == 0 and b.ptr.tolist() == [0]
    inside = cols.inside.reshape(14, 14, 14)  # [k, j, i]
    assert inside[2:12, 2:12, 2:12].all() and inside.sum() == 1000


def test_cube_off_the_borders_boundary_parents() -> None:
    """A cube [23; 123]: the parents it touches are 11³, those wholly inside — 9³, the rest (602) are boundary;
    the centres inside — 10³."""
    grid = grid_of((14, 14, 14))
    v, t = box((23.0, 23.0, 23.0), (123.0, 123.0, 123.0))
    mesh = fc.Mesh.of(grid, v, t, "Куб")
    b = fb.boundary_parents(grid, mesh)
    assert len(b.p) == 11**3 - 9**3
    mask = b.mask(grid.cells).reshape(14, 14, 14)
    assert mask[2:13, 2:13, 2:13].sum() == 602 and mask.sum() == 602 and not mask[3:12, 3:12, 3:12].any()
    assert fc.inside_columns(grid, mesh).count == 1000
    # every triangle of a face goes through the 11 × 11 parents (or part) of its face: the pairs are exact
    per_parent = np.diff(b.ptr)
    assert per_parent.min() >= 1 and b.pairs == int(per_parent.sum())
    assert (b.tri[b.ptr[0] : b.ptr[1]] == np.sort(b.tri[b.ptr[0] : b.ptr[1]])).all()


# --- rays --------------------------------------------------------------------------------------------------


def test_ray_offset_is_a_share_of_the_subcell() -> None:
    grid = GridV2(corner=(0.0, 0.0, 0.0), counts=(8, 8, 8), size=(10.0, 10.0, 10.0), sub=GridV2.of(
        {"corner": [0, 0, 0], "counts": [1, 1, 1], "size": [1, 1, 1], "div": [8, 4, 2]}).sub)  # fmt: skip
    v, t = box((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    mesh = fc.Mesh.of(grid, v, t)
    assert mesh.unit == (1.25, 2.5)
    assert mesh.offsets() == pytest.approx((math.sqrt(2) * 1e-6 * 1.25, math.sqrt(3) * 1e-6 * 2.5), rel=1e-15)
    assert mesh.offsets((10.0, 10.0)) == pytest.approx((math.sqrt(2) * 1e-5, math.sqrt(3) * 1e-5), rel=1e-15)


def test_ray_offset_gets_round_numbers_right() -> None:
    """A cube [25; 125] whose faces go through the centres of the parents: the ray is shifted to +X and +Y (into
    the cube at its low faces, out of it at the high ones) — 25..115 along X and Y; along Z there is no shift,
    a crossing at the very height of a centre is not below it: 35..125. Always 10³, whatever the strips."""
    grid = grid_of((14, 14, 14))
    v, t = box((25.0, 25.0, 25.0), (125.0, 125.0, 125.0))
    mesh = fc.Mesh.of(grid, v, t)
    for strip in (1, 14, 4096):
        cols = fc.inside_columns(grid, mesh, strip=strip)
        inside = cols.inside.reshape(14, 14, 14)
        assert cols.count == 1000
        assert inside[3:13, 2:12, 2:12].all()  # [k, j, i]


def test_diagonal_through_the_centres_is_crossed_once() -> None:
    """The diagonals of the top and the bottom go along x = y, through the centres of the columns (25, 25),
    (35, 35)… A ray exactly on an edge (no shift) is counted once: one crossing in each of the two triangles'
    sides — the same result as with the shift, and no ray with an odd or a doubled crossing."""
    grid = grid_of((14, 14, 14))
    v, t = box((20.0, 20.0, 20.0), (120.0, 120.0, 120.0))
    mesh = fc.Mesh.of(grid, v, t)
    shifted = fc.inside_columns(grid, mesh)
    flat = fc.inside_columns(grid, mesh, unit=(0.0, 0.0))
    assert shifted.count == flat.count == 1000
    assert (shifted.inside == flat.inside).all()


def test_octahedron_with_vertices_on_the_rays() -> None:
    """An octahedron whose apexes are exactly above and below a column centre and whose equator is at the height
    of a layer centre: against |x − 55| + |y − 55| + |z − 55| < 50 at the shifted centres."""
    grid = grid_of((12, 12, 12))
    c = 55.0
    v = np.array([[c + 50, c, c], [c - 50, c, c], [c, c + 50, c], [c, c - 50, c], [c, c, c + 50], [c, c, c - 50]])
    t = np.array([[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4], [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]], np.int32)
    mesh = fc.Mesh.of(grid, v, t, "Октаэдр")
    cols = fc.inside_columns(grid, mesh)
    q = lattice(grid)
    ex, ey = mesh.offsets()
    want = np.abs(q[:, 0] + ex - c) + np.abs(q[:, 1] + ey - c) + np.abs(q[:, 2] - c) < 50.0
    assert (cols.inside.astype(bool) == want).all() and want.sum() > 100
    assert (fc.inside_points(mesh, q) == want).all()


def sphere_mesh(
    grid: GridV2, n_triangles: int, radius: float, centre: tuple[float, float, float], name: str = "Шар"
) -> fc.Mesh:
    v, t = uv_sphere(n_triangles, radius=radius, centre=(0.0, 0.0, 0.0))
    return fc.Mesh.of(grid, v + np.asarray(centre), t, name)


def convex_inside(mesh: fc.Mesh, p: np.ndarray, margin: float = 0.0) -> np.ndarray:
    """Points of a convex polyhedron: on the inner side of every plane of a triangle (the sphere is one)."""
    a, b, c = (mesh.v[mesh.t[:, i]] for i in range(3))
    n = np.cross(b - a, c - a)
    n /= np.linalg.norm(n, axis=1)[:, None]
    d = np.einsum("ij,ij->i", n, a)
    out = np.empty(len(p), dtype=bool)
    for lo in range(0, len(p), 20_000):
        s = p[lo : lo + 20_000] @ n.T - d  # signed distance to every plane
        out[lo : lo + 20_000] = (s < -margin).all(axis=1)
    return out


@pytest.mark.parametrize("rotation", [0.0, -37.0])
def test_sphere_parity_and_boundary_against_the_convex_body(rotation: float) -> None:
    grid = grid_of((16, 16, 16), size=5.0, rotation=rotation)
    mesh = sphere_mesh(grid, 3000, 30.0, (40.0, 41.0, 39.0))
    cols = fc.inside_columns(grid, mesh)
    b = fb.boundary_parents(grid, mesh)
    q = lattice(grid)
    ex, ey = mesh.offsets()
    shifted = q + np.array([ex, ey, 0.0])
    want = convex_inside(mesh, shifted)
    assert (cols.inside.astype(bool) == want).all() and 700 < want.sum() < 1200
    # the parents with the eight corners inside / outside one plane are not boundary; the others (cut) are
    cs = corners(q, 5.0)
    flat = cs.reshape(-1, 3)
    corner_in = convex_inside(mesh, flat).reshape(-1, 8)
    a, bb, c = (mesh.v[mesh.t[:, i]] for i in range(3))
    n = np.cross(bb - a, c - a)
    n /= np.linalg.norm(n, axis=1)[:, None]
    d = np.einsum("ij,ij->i", n, a)
    beyond = np.zeros(len(q), dtype=bool)  # all eight corners above one plane — far from the body
    for lo in range(0, len(q), 2000):
        s = cs[lo : lo + 2000].reshape(-1, 3) @ n.T - d
        beyond[lo : lo + 2000] = (s.reshape(-1, 8, len(n)) > 1e-9).all(axis=1).any(axis=1)
    mask = b.mask(grid.cells)
    assert not mask[corner_in.all(axis=1)].any() and not mask[beyond].any()
    cut = corner_in.any(axis=1) & ~corner_in.all(axis=1)
    assert mask[cut].all() and mask.sum() >= cut.sum()
    # the pairs: every triangle of the sphere is in some parent, and a parent lists only triangles that reach it
    assert len(np.unique(b.tri)) == len(mesh.t)


# --- boundary parents against an independent test ----------------------------------------------------------


def clip_nonempty(tri: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> bool:
    """Sutherland–Hodgman: does the triangle have a point in the box (closed)?"""
    poly = [p for p in tri]
    for ax in range(3):
        for sign, bound in ((1.0, hi[ax]), (-1.0, -lo[ax])):
            out = []
            for a, b in zip(poly, poly[1:] + poly[:1], strict=True):
                da, db = sign * a[ax] - bound, sign * b[ax] - bound
                if da <= 0:
                    out.append(a)
                if (da < 0 < db) or (db < 0 < da):
                    out.append(a + (b - a) * (da / (da - db)))
            poly = out
            if not poly:
                return False
    return True


@pytest.mark.parametrize("seed", [1, 2])
def test_boundary_pairs_against_clipping(seed: int) -> None:
    rng = np.random.default_rng(seed)
    grid = grid_of((6, 5, 4))
    n = 150
    v = rng.uniform(-8.0, 68.0, size=(3 * n, 3))
    v[:, 2] = rng.uniform(-6.0, 46.0, size=3 * n)
    # a part of the triangles small, a part long and thin (to meet the test of the layers of the plane)
    v[: n * 2] = np.repeat(v[:n], 2, axis=0)[: n * 2] + rng.uniform(-6.0, 6.0, size=(n * 2, 3))
    t = np.arange(3 * n, dtype=np.int32).reshape(n, 3)
    mesh = fc.Mesh.of(grid, v, t, "Случайный", check=False)
    b = fb.boundary_parents(grid, mesh)
    got = {(int(p), int(m)) for r, p in enumerate(b.p) for m in b.triangles(r)}
    assert len(got) == b.pairs
    shrink = 1e-9 * 10.0 / 2
    want = set()
    for m in range(n):
        tri = v[t[m]]
        for k, j, i in itertools.product(range(4), range(5), range(6)):
            lo = np.array([i, j, k]) * 10.0 + shrink
            if clip_nonempty(tri, lo, lo + 10.0 - 2 * shrink):
                want.add((i + 6 * (j + 5 * k), m))
    assert got == want and len(want) > 100


def test_tri_box_overlap_touching_and_degenerate() -> None:
    f = fb.tri_box_overlap
    h = 5.0
    # a triangle in the plane of a face of the box, on its border: touching is not an overlap
    assert not f(0.0, 0.0, 0.0, h, h, h, 5.0, -3.0, -3.0, 5.0, 3.0, -3.0, 5.0, 0.0, 3.0)
    assert f(0.0, 0.0, 0.0, h, h, h, 4.9, -3.0, -3.0, 4.9, 3.0, -3.0, 4.9, 0.0, 3.0)
    # a triangle without an area, and one far away
    assert not f(0.0, 0.0, 0.0, h, h, h, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0)
    assert not f(0.0, 0.0, 0.0, h, h, h, 9.0, 9.0, 9.0, 9.0, 9.0, 12.0, 9.0, 12.0, 9.0)
    # across the box with no vertex inside it: through the middle
    assert f(0.0, 0.0, 0.0, h, h, h, -20.0, -20.0, 0.0, 20.0, -20.0, 0.0, 0.0, 20.0, 0.0)


# --- closed and consistent bodies --------------------------------------------------------------------------


def test_open_wireframe_is_422_in_russian() -> None:
    grid = grid_of((14, 14, 14))
    v, t = box((20.0, 20.0, 20.0), (120.0, 120.0, 120.0))
    with pytest.raises(ApiError) as exc:
        fc.Mesh.of(grid, v, t[:-1], "Рудное тело", index=1)
    e = exc.value
    assert e.status == 422 and e.code == "wireframe_open"
    assert "Рудное тело" in e.message and "не замкнут" in e.message and "открытых рёбер" in e.message
    assert e.hint and e.extra == {"param": "wireframes.1.id", "name": "Рудное тело"}
    # by the stored measures of the wireframe (`wireframes.stats`), before anything is read
    with pytest.raises(ApiError) as exc2:
        fc.check_body("Поверхность", v, t, stats={"closed": False, "open_edges": 0, "nonmanifold_edges": 0})
    assert exc2.value.code == "wireframe_open" and "поверхность" in exc2.value.message
    with pytest.raises(ApiError) as exc3:
        fc.check_body("Пустой", v, t[:0])
    assert exc3.value.code == "wireframe_open" and exc3.value.status == 422
    bad = v.copy()
    bad[0, 0] = np.nan
    with pytest.raises(ApiError) as exc4:
        fc.check_body("Дефектный", bad, t)
    assert exc4.value.code == "wireframe_open"
    fc.check_body("Куб", v, t, stats={"closed": True})  # a closed body: no error


def test_neighbours_wound_opposite_ways_is_422() -> None:
    grid = grid_of((14, 14, 14))
    v, t = box((20.0, 20.0, 20.0), (120.0, 120.0, 120.0))
    t = t.copy()
    t[3] = t[3][[0, 2, 1]]
    with pytest.raises(ApiError) as exc:
        fc.Mesh.of(grid, v, t, "Куб", index=2)
    assert exc.value.status == 422 and exc.value.code == "wireframe_orientation"
    assert exc.value.hint and exc.value.extra["param"] == "wireframes.2.id" and "Куб" in exc.value.message


def test_rays_that_disagree_on_the_side_are_422() -> None:
    """Two cubes in one wireframe, each wound consistently, one of them inside out: no edge disagrees, the rays do."""
    grid = grid_of((14, 14, 14))
    v1, t1 = box((10.0, 10.0, 10.0), (50.0, 50.0, 50.0))
    v2, t2 = box((80.0, 80.0, 80.0), (120.0, 120.0, 120.0))
    t = np.vstack([t1, t2[:, [0, 2, 1]] + 8]).astype(np.int32)
    mesh = fc.Mesh.of(grid, np.vstack([v1, v2]), t, "Два тела")
    with pytest.raises(ApiError) as exc:
        fc.inside_columns(grid, mesh)
    assert exc.value.code == "wireframe_orientation" and exc.value.status == 422 and exc.value.hint


def test_overlapping_shells_are_422() -> None:
    grid = grid_of((14, 14, 14))
    v1, t1 = box((10.0, 10.0, 10.0), (70.0, 70.0, 70.0))
    v2, t2 = box((40.0, 40.0, 40.0), (110.0, 110.0, 110.0))
    mesh = fc.Mesh.of(grid, np.vstack([v1, v2]), np.vstack([t1, t2 + 8]), "Пересечение")
    with pytest.raises(ApiError) as exc:
        fc.inside_columns(grid, mesh)
    assert exc.value.code == "wireframe_orientation"


def test_a_body_wholly_inward_is_a_solid_all_the_same() -> None:
    grid = grid_of((14, 14, 14))
    v, t = box((20.0, 20.0, 20.0), (120.0, 120.0, 120.0))
    out = fc.inside_columns(grid, fc.Mesh.of(grid, v, t))
    inv = fc.inside_columns(grid, fc.Mesh.of(grid, v, t[:, [0, 2, 1]]))
    assert out.orientation == "outward" and inv.orientation == "inward" and (out.inside == inv.inside).all()


def test_variable_step_is_422() -> None:
    v, t = box((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    grid = GridV2(corner=(0.0, 0.0, 0.0), counts=(2, 2, 2), tensors=((10.0, 20.0), (10.0, 10.0), (10.0, 10.0)))
    with pytest.raises(ApiError) as exc:
        fc.Mesh.of(grid, v, t)
    assert exc.value.code == "variable_grid" and exc.value.status == 422


def test_a_body_off_the_grid_and_a_strip_of_the_index() -> None:
    grid = grid_of((6, 6, 6))
    v, t = box((200.0, 200.0, 200.0), (230.0, 230.0, 230.0))
    mesh = fc.Mesh.of(grid, v, t)
    assert fc.inside_columns(grid, mesh).count == 0 and len(fb.boundary_parents(grid, mesh).p) == 0
    assert not fc.inside_points(mesh, np.array([[210.0, 210.0, 210.0]])).any()
    # a body that is only partly in the grid: the parents of the grid that are inside it
    v, t = box((-30.0, -30.0, -30.0), (25.0, 25.0, 25.0))
    part = fc.Mesh.of(grid, v, t)
    # the centres 5 and 15 along X and Y (25 is on the face), 5, 15, 25 along Z
    assert fc.inside_columns(grid, part).count == 2 * 2 * 3


def test_index_cells_grow_with_the_budget_and_the_answer_does_not_change() -> None:
    grid = grid_of((16, 16, 16), size=5.0)
    v, t = uv_sphere(4000, radius=30.0, centre=(40.0, 40.0, 40.0))
    a = fc.Mesh.of(grid, v, t, "Шар")
    b = fc.Mesh.of(grid, v, t, "Шар", max_entries=3000)
    assert a.index.stride == 1 and b.index.stride > 1
    assert (fc.inside_columns(grid, a).inside == fc.inside_columns(grid, b).inside).all()


# --- threads -----------------------------------------------------------------------------------------------


def test_one_hash_with_1_2_and_4_threads() -> None:
    grid = grid_of((40, 36, 32), size=2.5, rotation=15.0)
    v, t = uv_sphere(30_000, radius=40.0, centre=(50.0, 45.0, 40.0))
    mesh = fc.Mesh.of(grid, v, t, "Шар")
    q = lattice(grid)[::7] + 0.37
    hashes = set()
    for threads in (1, 2, 4):
        cols = fc.inside_columns(grid, mesh, threads=threads, strip=64)
        b = fb.boundary_parents(grid, mesh, threads=threads, chunk=1000)
        pts = fc.inside_points(mesh, q, threads=threads, chunk=500)
        hashes.add((sha(cols.inside, b.p, b.ptr, b.tri, pts), cols.rays, cols.crossings))
    assert len(hashes) == 1
    ref = fc.inside_columns(grid, mesh)  # strips of the default size, one thread
    assert sha(ref.inside) == sha(fc.inside_columns(grid, mesh, threads=4, strip=1).inside)
    assert fb.boundary_parents(grid, mesh).pairs == fb.boundary_parents(grid, mesh, threads=4, chunk=17).pairs
    assert ref.count > 10_000


def test_inside_points_follow_the_centre_rays() -> None:
    grid = grid_of((16, 16, 16), size=5.0)
    mesh = sphere_mesh(grid, 3000, 30.0, (40.0, 41.0, 39.0))
    cols = fc.inside_columns(grid, mesh)
    assert (fc.inside_points(mesh, lattice(grid)).astype(np.uint8) == cols.inside).all()
    far = np.array([[1000.0, 0.0, 0.0], [40.0, 41.0, 39.0], [40.0, 41.0, 200.0]])
    assert fc.inside_points(mesh, far).tolist() == [False, True, False]


@pytest.mark.skipif(os.environ.get("BF_MEASURE_FILL") != "1", reason="a measurement: BF_MEASURE_FILL=1 pytest -s")
def test_measure_1e6_triangles_20e6_parents() -> None:  # pragma: no cover
    """The stages on a sphere of 10⁶ triangles in a grid of 20 million parents (400 × 400 × 125, 10 m): seconds."""
    import time

    grid = grid_of((400, 400, 125), size=10.0)
    v, t = uv_sphere(1_000_000, radius=600.0, centre=(2000.0, 2000.0, 620.0))
    for threads in (1, 4):
        t0 = time.perf_counter()
        mesh = fc.Mesh.of(grid, v, t, "Шар")
        t1 = time.perf_counter()
        cols = fc.inside_columns(grid, mesh, threads=threads)
        t2 = time.perf_counter()
        b = fb.boundary_parents(grid, mesh, threads=threads)
        t3 = time.perf_counter()
        print(
            f"threads={threads}: check+index {t1 - t0:.2f} s, columns {t2 - t1:.2f} s, boundary {t3 - t2:.2f} s; "
            f"inside {cols.count}, boundary {len(b.p)}, pairs {b.pairs}"
        )
