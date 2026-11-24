"""Geometry of block models v2 (подшаг 17.2; ТЗ 8.7.2, ADR 0023 decisions 3, 5, 6): centres and boundaries of
parents and sub-blocks in the project axes with and without the turn about Z (**Т8.7-4**: 1e−9 м against an
80-bit reference), exact turns by 90° (no accumulated error), the volume of sub-blocks (Σ of a parent, FRAC, a gap
is NaN — never 0), the row ↔ cell index, the intersection with a rectangle or a box."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry as geo
from bf_core.blocks import schema as bs
from bf_core.blocks.schema import GridV2, SubblockSpec

LD = np.longdouble
PI = LD("3.14159265358979323846264338327950288")


def grid_of(
    counts=(7, 5, 4),
    size=(5.0, 10.0, 2.5),
    corner=(1000.5, 2000.25, 50.0),
    rotation=0.0,
    div=(1, 1, 1),
    tensors=None,
) -> GridV2:
    raw = {"corner": list(corner), "counts": list(counts), "div": list(div), "rotation": rotation}
    raw.update({"tensors": tensors} if tensors else {"size": list(size)})
    return GridV2.of(raw)


def all_parents(grid: GridV2) -> np.ndarray:
    return np.arange(grid.cells, dtype=np.uint64)


def random_rows(grid: GridV2, n: int, seed: int = 1):
    """n random sub-blocks (P, s, w) aligned to the sub-cells, sorted like the files."""
    rng = np.random.default_rng(seed)
    div = np.asarray(grid.sub.div)
    p = rng.integers(0, grid.cells, n).astype(np.uint64)
    s = np.stack([rng.integers(0, d, n) for d in div], axis=1)
    w = np.stack([rng.integers(1, d - s[:, a] + 1) for a, d in enumerate(div)], axis=1)
    s, w = s.astype(np.uint8), w.astype(np.uint8)
    order = bs.sort_rows(p, s)
    return p[order], s[order], w[order]


def reference_centres(grid: GridV2, p, s, w) -> np.ndarray:
    """ТЗ 8.7.2 in 80-bit floats: p = O + R_z(φ)·q, q_a = (i_a + (s_a + w_a/2)/div_a)·d_a."""
    nx, ny = grid.counts[0], grid.counts[1]
    p = [int(v) for v in p]
    phi = LD(grid.rotation) * PI / LD(180)
    sin, cos = np.sin(phi), np.cos(phi)
    out = np.empty((len(p), 3))
    for r, v in enumerate(p):
        ijk = (v % nx, (v // nx) % ny, v // (nx * ny))
        q = [
            (LD(ijk[a]) + (LD(int(s[r, a])) + LD(int(w[r, a])) / 2) / LD(grid.sub.div[a])) * LD(grid.size[a])
            for a in range(3)
        ]
        out[r] = (
            float(LD(grid.corner[0]) + cos * q[0] - sin * q[1]),
            float(LD(grid.corner[1]) + sin * q[0] + cos * q[1]),
            float(LD(grid.corner[2]) + q[2]),
        )
    return out


# --- Т8.7-4: the centres at φ = 30° -----------------------------------------------------------------------


@pytest.mark.parametrize("div", [(1, 1, 1), (4, 2, 8), (128, 1, 2)])
@pytest.mark.parametrize("rotation", [30.0, -30.0, 137.3, 0.0])
def test_t8_7_4_centres_within_1e9(div, rotation):
    grid = grid_of(rotation=rotation, div=div)
    p, s, w = random_rows(grid, 300, seed=3) if grid.sub.sub else (all_parents(grid), None, None)
    got = geo.centres(grid, p, s, w)
    ws = w if w is not None else np.ones((len(p), 3), dtype=np.uint8) * np.asarray(grid.sub.div, dtype=np.uint8)
    ss = s if s is not None else np.zeros((len(p), 3), dtype=np.uint8)
    ref = reference_centres(grid, p, ss, ws)
    assert got.dtype == np.float64
    assert np.abs(got - ref).max() < 1e-9


def test_t8_7_4_far_from_the_origin_and_hand_value():
    # a project far from zero: the error stays far below 1e-9 (the turn is done about the corner, not the origin)
    grid = grid_of(
        counts=(3, 3, 2), size=(10.0, 10.0, 5.0), corner=(5_432_100.123, 6_543_200.456, 120.0), rotation=30.0
    )
    c = geo.centres(grid, np.array([0, 4, 17], dtype=np.uint64))
    q = np.array([[5.0, 5.0, 2.5], [15.0, 15.0, 2.5], [25.0, 25.0, 7.5]])
    r = math.radians(30.0)
    want = np.column_stack(
        [
            5_432_100.123 + (math.cos(r) * q[:, 0] - math.sin(r) * q[:, 1]),
            6_543_200.456 + (math.sin(r) * q[:, 0] + math.cos(r) * q[:, 1]),
            120.0 + q[:, 2],
        ]
    )
    assert np.abs(c - want).max() < 1e-9
    assert np.abs(grid.to_local(c) - q).max() < 1e-8  # and back


def test_centres_between_sub_blocks_and_parent():
    grid = grid_of(counts=(2, 2, 2), size=(8.0, 8.0, 4.0), corner=(0.0, 0.0, 0.0), div=(4, 4, 2))
    p = np.array([0, 0, 0, 3], dtype=np.uint64)
    s = np.array([[0, 0, 0], [0, 0, 0], [3, 3, 1], [1, 2, 0]], dtype=np.uint8)
    w = np.array([[4, 4, 2], [1, 1, 1], [1, 1, 1], [2, 2, 2]], dtype=np.uint8)
    c = geo.row_local_centres(grid, p, s, w)
    assert c.tolist() == [[4.0, 4.0, 2.0], [1.0, 1.0, 1.0], [7.0, 7.0, 3.0], [12.0, 14.0, 2.0]]
    # no s, w = the whole parent: the same as s = 0, w = div
    full = geo.row_local_centres(grid, p[:1], None, None)
    assert full.tolist() == [[4.0, 4.0, 2.0]]


# --- exact turns by 90° ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phi,fx,fy",
    [(0.0, lambda x, y: (x, y), None), (90.0, lambda x, y: (-y, x), None), (180.0, lambda x, y: (-x, -y), None)]
    + [(-90.0, lambda x, y: (y, -x), None), (270.0, lambda x, y: (y, -x), None), (-270.0, lambda x, y: (-y, x), None)],
)
def test_exact_90_degrees_no_error(phi, fx, fy):
    # 0.1 is not exact in binary: still the turn adds nothing, it only swaps and negates
    grid = grid_of(counts=(900, 700, 3), size=(0.1, 0.1, 0.3), corner=(123456.789, -98765.4321, -12.5), rotation=phi)
    p = np.array([0, 1, 899, 900, 123456, 629999, 1889999], dtype=np.uint64)
    assert grid.rotation in (0.0, 90.0, 180.0, -90.0)
    loc = geo.row_local_centres(grid, p)
    got = geo.centres(grid, p)
    x, y = fx(loc[:, 0], loc[:, 1])
    assert np.array_equal(got[:, 0], 123456.789 + x)
    assert np.array_equal(got[:, 1], -98765.4321 + y)
    assert np.array_equal(got[:, 2], -12.5 + loc[:, 2])
    # the local centre itself is the product (i + 1/2)·d, not a running sum: 899 steps do not drift
    i = (p % np.uint64(900)).astype(np.float64)
    assert np.array_equal(loc[:, 0], (i + 0.5) * 0.1)


def test_exact_90_boxes_and_bounds_and_extent():
    grid = grid_of(counts=(4, 3, 2), size=(2.0, 5.0, 1.0), corner=(10.0, 20.0, 5.0), rotation=90.0, div=(2, 2, 1))
    p = all_parents(grid)
    b = geo.row_bounds(grid, p)
    lb = geo.row_boxes(grid, p)
    # x = 10 − qy, y = 20 + qx: the bounds are the swapped local box, exactly
    assert np.array_equal(b[:, 0], 10.0 - lb[:, 4])
    assert np.array_equal(b[:, 3], 10.0 - lb[:, 1])
    assert np.array_equal(b[:, 1], 20.0 + lb[:, 0])
    assert np.array_equal(b[:, 4], 20.0 + lb[:, 3])
    assert geo.extent(grid) == [-5.0, 20.0, 5.0, 10.0, 28.0, 7.0]
    assert [b[:, 0].min(), b[:, 1].min(), b[:, 2].min(), b[:, 3].max(), b[:, 4].max(), b[:, 5].max()] == geo.extent(
        grid
    )


# --- boundaries -----------------------------------------------------------------------------------------------


def test_boxes_contain_centres_and_sub_blocks_tile_the_parent():
    grid = grid_of(rotation=30.0, div=(4, 2, 8))
    p, s, w = random_rows(grid, 200, seed=5)
    box = geo.row_boxes(grid, p, s, w)
    c = geo.row_local_centres(grid, p, s, w)
    assert np.all(box[:, :3] < c) and np.all(c < box[:, 3:])
    assert np.abs((box[:, :3] + box[:, 3:]) * 0.5 - c).max() < 1e-12
    assert np.abs((box[:, 3:] - box[:, :3]) - geo.row_widths(grid, p, s, w)).max() < 1e-12
    # the footprint corners are the four turned corners of the local box; the bounds enclose them
    cor = geo.row_corners(grid, p, s, w)
    bnd = geo.row_bounds(grid, p, s, w)
    assert cor.shape == (200, 8, 3)
    assert np.array_equal(bnd[:, :3], cor.min(axis=1)) and np.array_equal(bnd[:, 3:], cor.max(axis=1))
    q = grid.to_local(cor.reshape(-1, 3)).reshape(200, 8, 3)
    assert np.abs(q[:, 0, :] - box[:, :3]).max() < 1e-9 and np.abs(q[:, 6, :] - box[:, 3:]).max() < 1e-9


def test_extent_is_the_hull_of_all_parents():
    grid = grid_of(rotation=30.0)
    b = geo.row_bounds(grid, all_parents(grid))
    got = [*b[:, :3].min(axis=0), *b[:, 3:].max(axis=0)]
    assert np.abs(np.asarray(got) - np.asarray(geo.extent(grid))).max() < 1e-9


def test_variable_step_edges_and_no_sub_blocks():
    t = {"x": [1.0, 2.0, 4.0], "y": [3.0, 1.0], "z": [0.5, 0.5, 2.0]}
    grid = grid_of(counts=(3, 2, 3), corner=(10.0, 10.0, 0.0), tensors=t)
    p = all_parents(grid)
    assert geo.row_local_centres(grid, p[:3]).tolist() == [[0.5, 1.5, 0.25], [2.0, 1.5, 0.25], [5.0, 1.5, 0.25]]
    assert geo.row_boxes(grid, p[2:3]).tolist() == [[3.0, 0.0, 0.0, 7.0, 3.0, 0.5]]
    assert geo.row_volumes(grid, p[:3]).tolist() == [1.5, 3.0, 6.0]
    s, w = np.zeros((1, 3), dtype=np.uint8), np.ones((1, 3), dtype=np.uint8)
    with pytest.raises(ApiError) as e:
        geo.row_boxes(grid, p[:1], s, w)
    assert e.value.code == "variable_grid"


# --- volumes --------------------------------------------------------------------------------------------------


def test_t8_7_1_cube_volume_exact():
    grid = grid_of(counts=(10, 10, 10), size=(10.0, 10.0, 10.0), corner=(0.0, 0.0, 0.0))
    p = all_parents(grid)
    vol = geo.row_volumes(grid, p, None, None, np.ones(len(p)))
    assert len(vol) == 1000 and geo.volume_sum(vol) == (1_000_000.0, 0)
    assert geo.parent_volume(grid) == 1_000_000.0


def test_sub_blocks_volume_sums_to_the_parent():
    grid = grid_of(counts=(3, 3, 2), size=(8.0, 6.0, 4.0), rotation=30.0, div=(4, 2, 2))
    # one parent split: a 2×1×2 block, two 1×1×1 blocks and the rest by single sub-cells — a complete partition
    cells = [(sx, sy, sz) for sx in range(4) for sy in range(2) for sz in range(2)]
    p = np.zeros(len(cells), dtype=np.uint64)
    s = np.array(cells, dtype=np.uint8)
    w = np.ones_like(s)
    vol = geo.row_volumes(grid, p, s, w)
    assert np.all(vol == 8.0 * 6.0 * 4.0 / 16)
    assert abs(vol.sum() - 192.0) < 192.0 * 1e-12
    # merged blocks: (sx 0…1) × full y × full z + two 1-wide columns — same total
    s2 = np.array([[0, 0, 0], [2, 0, 0], [3, 0, 0]], dtype=np.uint8)
    w2 = np.array([[2, 2, 2], [1, 2, 2], [1, 2, 2]], dtype=np.uint8)
    v2 = geo.row_volumes(grid, np.zeros(3, dtype=np.uint64), s2, w2)
    assert v2.tolist() == [96.0, 48.0, 48.0]
    assert abs(v2.sum() - 192.0) < 192.0 * 1e-12
    # the sub-blocks of all rows never exceed the parent (no volume invented by the turn)
    pr, sr, wr = random_rows(grid, 100, seed=2)
    assert np.all(geo.row_volumes(grid, pr, sr, wr) <= 192.0 * (1 + 1e-12))


def test_frac_gap_is_nan_not_zero():
    grid = grid_of(counts=(2, 2, 1), size=(10.0, 10.0, 10.0), corner=(0.0, 0.0, 0.0))
    p = all_parents(grid)
    frac = np.array([1.0, 0.25, np.nan, 0.0])
    vol = geo.row_volumes(grid, p, None, None, frac)
    assert vol[0] == 1000.0 and vol[1] == 250.0 and vol[3] == 0.0
    assert math.isnan(vol[2])  # a gap is a gap, not a zero
    assert geo.volume_sum(vol) == (1250.0, 1)
    assert geo.volume_sum(np.array([np.nan, np.nan])) == (0.0, 2)
    with pytest.raises(ApiError):
        geo.row_volumes(grid, p, None, None, frac[:3])


# --- the row ↔ cell index -------------------------------------------------------------------------------------


def test_index_roundtrip_and_ranges():
    grid = grid_of(counts=(7, 5, 4))
    i, j, k = np.meshgrid(np.arange(7), np.arange(5), np.arange(4), indexing="ij")
    p = geo.parent_p(grid, i.ravel(), j.ravel(), k.ravel())
    assert np.array_equal(p, bs.idx_of(i.ravel(), j.ravel(), k.ravel(), grid.counts))
    ii, jj, kk = geo.row_ijk(grid, p)
    assert np.array_equal(ii, i.ravel()) and np.array_equal(jj, j.ravel()) and np.array_equal(kk, k.ravel())
    assert geo.parent_p(grid, [6], [4], [3]).tolist() == [7 * 5 * 4 - 1]
    for bad in (lambda: geo.row_ijk(grid, np.array([140], dtype=np.uint64)), lambda: geo.parent_p(grid, [7], [0], [0])):
        with pytest.raises(ApiError) as e:
            bad()
        assert e.value.status == 422 and e.value.message and e.value.hint
    # off-sub-cell rows are refused with the blocks named in the text
    g2 = grid_of(div=(2, 2, 2))
    with pytest.raises(ApiError):
        geo.row_boxes(
            g2,
            np.array([0], dtype=np.uint64),
            np.array([[1, 0, 0]], dtype=np.uint8),
            np.array([[2, 1, 1]], dtype=np.uint8),
        )


def test_row_keys_follow_the_file_order_and_find_rows():
    grid = grid_of(div=(4, 4, 4))
    p, s, w = random_rows(grid, 400, seed=9)
    keep = np.r_[True, np.diff(geo.row_keys(p, s).astype(np.float64)) != 0]
    p, s, w = p[keep], s[keep], w[keep]
    assert bs.ascending(p, s)
    keys = geo.row_keys(p, s)
    assert bool((keys[1:] > keys[:-1]).all())
    assert np.array_equal(geo.find_rows(p, s, p, s), np.arange(len(p)))
    absent = (np.array([grid.cells - 1], dtype=np.uint64), np.array([[3, 3, 3]], dtype=np.uint8))
    has = bool(((p == absent[0][0]) & (s == 3).all(axis=1)).any())
    assert (geo.find_rows(p, s, *absent)[0] >= 0) == has
    assert geo.find_rows(p[:0], s[:0], p[:2], s[:2]).tolist() == [-1, -1]


@pytest.mark.parametrize("rotation", [0.0, 30.0, 90.0, -45.0])
def test_locate_finds_the_row_of_its_own_centre(rotation):
    grid = grid_of(rotation=rotation, div=(4, 2, 4))
    # a partition of the parents 0…5 into merged sub-blocks; drop some to make gaps
    rows = []
    for parent in range(6):
        rows.append((parent, (0, 0, 0), (2, 2, 4)))
        rows.append((parent, (2, 0, 0), (2, 1, 4)))
        rows.append((parent, (2, 1, 0), (1, 1, 2)))
        rows.append((parent, (3, 1, 0), (1, 1, 2)))
    p = np.array([r[0] for r in rows], dtype=np.uint64)
    s = np.array([r[1] for r in rows], dtype=np.uint8)
    w = np.array([r[2] for r in rows], dtype=np.uint8)
    order = bs.sort_rows(p, s)
    p, s, w = p[order], s[order], w[order]
    got = geo.locate_rows(p, s, w, grid, geo.centres(grid, p, s, w))
    assert np.array_equal(got, np.arange(len(p)))
    # the centre of the block (parent 6) has no row: a gap; far outside the grid: also no row
    far = geo.centres(grid, np.array([6, 0], dtype=np.uint64))
    assert geo.locate_rows(p, s, w, grid, far)[0] == -1
    assert geo.locate_rows(p, s, w, grid, grid.to_project([[-1.0, 1.0, 1.0], [1e9, 0, 0]])).tolist() == [-1, -1]
    # sub-cell (sx 3, sy 1, sz 2…3) of parent 0 is a gap (the top of the last column is not in any row)
    pts = grid.to_project(np.array([[3.5 * 5.0 / 4, 7.5, 2.0 * 2.5 / 4 * 3]]))
    assert geo.locate_rows(p, s, w, grid, pts)[0] == -1
    # and a model without sub-blocks: P only
    g1 = grid_of(rotation=rotation)
    p1 = all_parents(g1)[::3]
    assert np.array_equal(geo.locate_rows(p1, None, None, g1, geo.centres(g1, p1)), np.arange(len(p1)))


def test_locate_half_open_boundaries():
    grid = grid_of(counts=(4, 4, 2), size=(10.0, 10.0, 5.0), corner=(100.0, 200.0, 0.0), div=(2, 2, 1))
    pts = np.array(
        [
            [100.0, 200.0, 0.0],
            [110.0, 200.0, 0.0],
            [105.0, 205.0, 4.999],
            [140.0, 200.0, 1.0],
            [139.999, 239.9, 9.9],
            [99.99, 200.0, 0.0],
        ]
    )
    valid, p, s = geo.locate(grid, pts)
    assert valid.tolist() == [True, True, True, False, True, False]
    assert p[:3].tolist() == [0, 1, 0] and s[:3].tolist() == [[0, 0, 0], [0, 0, 0], [1, 1, 0]]
    assert p[4] == grid.cells - 1 and s[4].tolist() == [1, 1, 0]
    # a turned grid: the corner is the origin of the turn; a point 1 m along the local x of a 90° grid
    g90 = grid_of(counts=(2, 2, 1), size=(10.0, 10.0, 5.0), corner=(0.0, 0.0, 0.0), rotation=90.0)
    valid, p, _ = geo.locate(g90, np.array([[-5.0, 15.0, 1.0], [5.0, 5.0, 1.0]]))
    assert valid.tolist() == [True, False] and p[0] == 1


# --- the intersection with a rectangle or a box ----------------------------------------------------------------


def convex_overlap(a: np.ndarray, b: np.ndarray, touch: bool) -> bool:
    """Generic separating-axes test of two convex polygons (n×2), a different code from `intersects_box`."""
    for poly in (a, b):
        for t in range(len(poly)):
            e = poly[(t + 1) % len(poly)] - poly[t]
            ax = np.array([-e[1], e[0]])
            pa, pb = a @ ax, b @ ax
            if (
                (pa.max() < pb.min() - 1e-12 or pb.max() < pa.min() - 1e-12)
                if touch
                else (pa.max() <= pb.min() + 1e-12 or pb.max() <= pa.min() + 1e-12)
            ):
                return False
    return True


@pytest.mark.parametrize("rotation", [0.0, 30.0, 90.0, 45.0, -120.0])
def test_intersects_box_against_generic_polygon_test(rotation):
    grid = grid_of(counts=(6, 6, 3), size=(10.0, 10.0, 5.0), corner=(0.0, 0.0, 0.0), rotation=rotation, div=(2, 2, 1))
    p, s, w = random_rows(grid, 120, seed=11)
    rng = np.random.default_rng(7)
    cor = geo.row_corners(grid, p, s, w)
    for _ in range(25):
        lo = rng.uniform(-70, 40, 2)
        hi = lo + rng.uniform(0.5, 60, 2)
        zlo = rng.uniform(-3, 12)
        box = [lo[0], lo[1], zlo, hi[0], hi[1], zlo + rng.uniform(0.5, 9)]
        rect = np.array([[lo[0], lo[1]], [hi[0], lo[1]], [hi[0], hi[1]], [lo[0], hi[1]]])
        got = geo.intersects_box(grid, p, s, w, box)
        for r in range(len(p)):
            foot = cor[r, :4, :2]
            zok = cor[r, 0, 2] < box[5] and box[2] < cor[r, 4, 2]
            assert bool(got[r]) == (convex_overlap(foot, rect, False) and zok)
        # a rectangle (4 numbers) is a box with no limit along Z
        r4 = geo.intersects_box(grid, p, s, w, [box[0], box[1], box[3], box[4]])
        assert np.array_equal(r4, np.array([convex_overlap(cor[r, :4, :2], rect, False) for r in range(len(p))]))


def test_intersects_box_diamond_corner_and_touch():
    # a 1 m square turned 45° about its corner (0, 0): the box of the diamond meets the rectangle, the diamond does not
    grid = grid_of(counts=(1, 1, 1), size=(1.0, 1.0, 1.0), corner=(0.0, 0.0, 0.0), rotation=45.0)
    p = np.array([0], dtype=np.uint64)
    assert geo.row_bounds(grid, p)[0, 3] == pytest.approx(math.sqrt(0.5))
    assert not geo.intersects_box(grid, p, None, None, [0.6, 0.0, 2.0, 0.5])[0]
    assert geo.intersects_box(grid, p, None, None, [0.4, 0.0, 2.0, 0.5])[0]
    # a face shared with the box: only when touching counts
    g = grid_of(counts=(1, 1, 1), size=(10.0, 10.0, 10.0), corner=(0.0, 0.0, 0.0))
    assert not geo.intersects_box(g, p, None, None, [10.0, 0.0, 0.0, 20.0, 10.0, 10.0])[0]
    assert geo.intersects_box(g, p, None, None, [10.0, 0.0, 0.0, 20.0, 10.0, 10.0], touch=True)[0]
    with pytest.raises(ApiError):
        geo.intersects_box(g, p, None, None, [5.0, 0.0, 0.0, 1.0, 10.0, 10.0])
    with pytest.raises(ApiError):
        geo.intersects_box(g, p, None, None, [1.0, 2.0, 3.0])


def test_centres_in_box_and_parent_ranges():
    grid = grid_of(counts=(10, 10, 2), size=(10.0, 10.0, 5.0), corner=(0.0, 0.0, 0.0), rotation=90.0)
    p = all_parents(grid)
    c = geo.centres(grid, p)
    box = [-45.0, 15.0, 0.0, -5.0, 35.0, 10.0]
    mask = geo.centres_in_box(grid, p, None, None, box)
    want = (c[:, 0] >= -45) & (c[:, 0] <= -5) & (c[:, 1] >= 15) & (c[:, 1] <= 35)
    assert np.array_equal(mask, want) and mask.sum() == 5 * 3 * 2
    # the parents that may meet the box: a superset of the real intersection, in a small range
    rng = geo.parent_ranges(grid, box)
    assert rng is not None
    (i0, j0, k0), (i1, j1, k1) = rng
    ii, jj, kk = geo.row_ijk(grid, p)
    inside = (ii >= i0) & (ii <= i1) & (jj >= j0) & (jj <= j1) & (kk >= k0) & (kk <= k1)
    assert np.all(inside[geo.intersects_box(grid, p, None, None, box)])
    assert inside.sum() < len(p)
    assert geo.parent_ranges(grid, [1e6, 1e6, 1e6 + 1, 1e6 + 1]) is None
    whole = geo.parent_ranges(grid, [-1e6, -1e6, 1e6, 1e6])
    assert whole == ((0, 0, 0), (9, 9, 1))


def test_sub_spec_untouched_and_levels():
    # the contract of 17.1 is used as it is: levels and div come from SubblockSpec
    assert SubblockSpec.of_levels([2, 1, 3]).div == (4, 2, 8)
    grid = grid_of(div=(4, 2, 8))
    assert grid.subcell() == (1.25, 5.0, 0.3125)


@pytest.mark.parametrize("rotation", [0.0, 30.0, 90.0])
def test_locate_agrees_with_the_boxes(rotation):
    grid = grid_of(rotation=rotation, div=(4, 2, 8))
    rng = np.random.default_rng(4)
    q = rng.uniform([0, 0, 0], list(grid.lengths()), (500, 3))
    valid, p, s = geo.locate(grid, grid.to_project(q))
    assert valid.all()
    box = geo.row_boxes(grid, p, s, np.ones_like(s))
    assert np.all(q >= box[:, :3] - 1e-9) and np.all(q <= box[:, 3:] + 1e-9)
