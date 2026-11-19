"""Э7.1, 7.1.6: synthetic clouds (`tools/gen/cloud.py`) and stages 1–2 of the ground filter — the index of
plan cells, isolated points, the lowest return of a cell (`geo/ground_kernels.py`, ADR 0014 §4)."""

from __future__ import annotations

import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gen import cloud  # noqa: E402

from bf_core.geo import ground_kernels as gk  # noqa: E402


def _digest(scene: cloud.Scene) -> str:
    h = hashlib.sha256()
    for a in (scene.xyz, scene.truth, scene.tag, scene.ret):
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


class Grid:
    """The lattice of one block over a whole cloud, the way `geo/ground.py` lays it."""

    def __init__(self, xyz: np.ndarray, c: float) -> None:
        self.c = c
        ok = np.isfinite(xyz).all(axis=1)
        self.ox = float(np.floor(xyz[ok, 0].min() / c) * c)
        self.oy = float(np.floor(xyz[ok, 1].min() / c) * c)
        self.ncx = int(np.floor((xyz[ok, 0].max() - self.ox) / c)) + 1
        self.ncy = int(np.floor((xyz[ok, 1].max() - self.oy) / c)) + 1
        cand = np.arange(len(xyz), dtype=np.int32)
        self.cid = np.empty(len(xyz), np.int32)
        gk.cell_ids(xyz, cand, 0, len(xyz), self.ox, self.oy, c, 0, 0, self.ncx, self.ncy, self.cid)
        self.start, self.order = gk.counting_sort(self.cid, cand, self.ncx * self.ncy)


# --- scenes ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "make",
    [
        lambda s: cloud.plain(s, side=120.0, buildings=2, trees=30, outliers=10),
        lambda s: cloud.funnel(s, depth=30.0),
        cloud.benches,
        lambda s: cloud.quarry(200_000, s),
    ],
    ids=["plain", "funnel", "benches", "quarry"],
)
def test_scene_is_reproducible_by_seed(make) -> None:  # type: ignore[no-untyped-def]
    a, b, other = make(7), make(7), make(8)
    assert _digest(a) == _digest(b)
    assert _digest(a) != _digest(other)
    assert a.xyz.dtype == np.float64 and a.xyz.flags.c_contiguous and a.xyz.shape == (len(a), 3)
    assert a.truth.dtype == a.tag.dtype == a.ret.dtype == np.uint8
    assert np.isfinite(a.xyz).all()
    assert a.xyz[:, 0].min() > 4e5 and a.xyz[:, 1].min() > 6e6  # project coordinates, not a local frame


def test_plain_scene_matches_tz1() -> None:
    s = cloud.plain(11)
    assert 330_000 < len(s) < 370_000  # 8 points/m² on 200 × 200 m less the footprints, plus the objects
    assert int((s.tag == cloud.T_OUTLIER).sum()) == 50 == int((s.truth == cloud.NOISE).sum())
    ground = s.truth == cloud.GROUND
    z = s.xyz[ground, 2] - s.origin[2]
    assert abs(float(z.std()) - cloud.SIGMA) < 0.002 and abs(float(z.mean())) < 0.001
    roofs = s.xyz[s.tag == cloud.T_ROOF]
    assert np.allclose(roofs[:, 2] - s.origin[2], 5.0, atol=0.15)
    # no ground under a roof (cells of 1 m full of roof points hold no ground); 30 % of it under a crown
    lo = s.xyz[:, :2].min(axis=0)
    roof = np.zeros((202, 202), bool)
    roof[tuple(np.floor(roofs[:, :2] - lo).astype(int).T + 1)] = True
    inner = roof.copy()  # cells of 1 m whose eight neighbours hold roof points too
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            inner &= np.roll(roof, (di, dj), axis=(0, 1))
    assert inner.sum() > 1000
    assert not inner[tuple(np.floor(s.xyz[ground, :2] - lo).astype(int).T + 1)].any()
    under = s.tag == cloud.T_UNDER
    crowns = s.tag == cloud.T_CROWN
    assert 0.17 < under.sum() / crowns.sum() < 0.23  # a crown carries 1.5 of the density: 0.3 / 1.5
    assert set(np.unique(s.ret[crowns])) == {0x21} and set(np.unique(s.ret[under])) == {0x22}
    # an outlier is alone: nothing within 2 m of it
    out = s.xyz[s.tag == cloud.T_OUTLIER]
    rest = s.xyz[s.tag != cloud.T_OUTLIER]
    for p in out:
        near = rest[(np.abs(rest[:, 0] - p[0]) < 2.0) & (np.abs(rest[:, 1] - p[1]) < 2.0)]
        assert not len(near) or float(np.linalg.norm(near - p, axis=1).min()) > 2.0


def test_funnel_scene_matches_tz2() -> None:
    s = cloud.funnel(12)
    assert 520_000 < len(s) < 660_000  # about 0.6 million points
    z = s.xyz[:, 2] - s.origin[2]
    ground = s.truth == cloud.GROUND
    assert z[ground].min() < -299.9 and abs(z[s.tag == cloud.T_RIM].mean()) < 0.01
    wall = np.isin(s.tag, (cloud.T_SLOPE, cloud.T_UNDER))
    # the wall stands at 50°: height against the distance from the axis
    centre = s.xyz[ground, :2].mean(axis=0)
    r = np.hypot(*(s.xyz[wall, :2] - centre).T)
    slope = np.polyfit(r, z[wall], 1)[0]
    assert abs(np.degrees(np.arctan(slope)) - 50.0) < 0.1
    # the deviation of the ground from the true surface along the normal is the noise
    dev = (s.xyz[ground, 2] - s.ground_z(s.xyz[ground, 0], s.xyz[ground, 1])) * s.ground_cos(
        s.xyz[ground, 0], s.xyz[ground, 1]
    )
    assert float(np.sqrt((dev[wall[ground]] ** 2).mean())) < 0.035
    # vegetation: 0.5–3 m above the wall along the vertical, on about 15 % of its area, half of the ground left
    veg = s.tag == cloud.T_BUSH
    over = s.xyz[veg, 2] - s.ground_z(s.xyz[veg, 0], s.xyz[veg, 1])
    assert over.min() >= 0.5 - 1e-9 and over.max() <= 3.0 + 1e-9
    share = (
        (s.tag == cloud.T_UNDER).sum() * 2.0 / ((s.tag == cloud.T_SLOPE).sum() + (s.tag == cloud.T_UNDER).sum() * 2.0)
    )
    assert 0.14 < share < 0.17
    assert 0.9 < veg.sum() / ((s.tag == cloud.T_UNDER).sum() * 2.0) < 1.1


def test_benches_scene_matches_tz3() -> None:
    s = cloud.benches(13)
    x, z = s.xyz[:, 0] - s.origin[0], s.xyz[:, 2] - s.origin[2]
    walls = s.tag == cloud.T_WALL
    assert int(walls.sum()) == 4 * 8 * 15 * 150  # 8 points/m² of wall
    for k, xw in enumerate((20.0, 30.0, 40.0, 50.0)):
        on = walls & (np.abs(x - xw) < 0.2)
        assert int(on.sum()) == 8 * 15 * 150
        assert z[on].min() >= 15.0 * (3 - k) and z[on].max() <= 15.0 * (4 - k)
    berm = s.tag == cloud.T_BERM
    assert set(np.round(z[berm] / 15.0).astype(int)) == {1, 2, 3}
    facade = s.tag == cloud.T_FACADE
    assert int(facade.sum()) == 4 * 8 * 6 * 8 and z[facade].max() <= 6.0
    assert (s.tag == cloud.T_BUSH).sum() > 500


# --- index -----------------------------------------------------------------------------------------------


def test_index_matches_numpy_and_skips_bad_points() -> None:
    rng = np.random.default_rng(5)
    xyz = rng.random((50_000, 3)) * [300.0, 200.0, 40.0] + cloud.ORIGIN
    xyz[[10, 999, 31_415]] = [[np.nan, 1, 1], [1, np.inf, 1], [432_100.0, 6_180_100.0, np.nan]]
    g = Grid(xyz, 0.5)
    ok = np.isfinite(xyz).all(axis=1)
    want = np.full(len(xyz), -1, np.int64)
    want[ok] = np.floor((xyz[ok, 1] - g.oy) / 0.5).astype(np.int64) * g.ncx + np.floor(
        (xyz[ok, 0] - g.ox) / 0.5
    ).astype(np.int64)
    assert np.array_equal(g.cid, want)
    ref = np.argsort(want, kind="stable")
    ref = ref[want[ref] >= 0]
    assert np.array_equal(g.order, ref)  # stable: inside a cell the points keep their order
    assert np.array_equal(g.start, np.searchsorted(want[ref], np.arange(g.ncx * g.ncy + 1)))
    assert g.order.dtype == np.int32 and g.start.dtype == np.int32


def test_block_keys_split_points_between_cores_once() -> None:
    rng = np.random.default_rng(6)
    xyz = rng.random((20_000, 3)) * [500.0, 300.0, 10.0] + cloud.ORIGIN
    ox, oy, c, span = cloud.ORIGIN[0] - 2.0, cloud.ORIGIN[1] - 2.0, 0.5, 256
    key = np.empty(len(xyz), np.int32)
    gk.block_keys(xyz, 0, 10_000, ox, oy, c, span, 4, 3, key)
    gk.block_keys(xyz, 10_000, 20_000, ox, oy, c, span, 4, 3, key)
    bx = np.floor((xyz[:, 0] - ox) / c).astype(int) // span
    by = np.floor((xyz[:, 1] - oy) / c).astype(int) // span
    assert np.array_equal(key, by * 4 + bx)
    # the cell a block sees is the global cell less its corner: a point falls into the same cell in every block
    cid = np.empty(len(xyz), np.int32)
    cand = np.arange(len(xyz), dtype=np.int32)
    gk.cell_ids(xyz, cand, 0, len(xyz), ox, oy, c, 100, 60, 2000, 2000, cid)
    gx, gy = np.floor((xyz[:, 0] - ox) / c).astype(int), np.floor((xyz[:, 1] - oy) / c).astype(int)
    inside = (gx >= 100) & (gy >= 60)
    assert np.array_equal(cid[inside], ((gy - 60) * 2000 + gx - 100)[inside]) and (cid[~inside] == -1).all()


# --- noise -----------------------------------------------------------------------------------------------


def _noise(
    xyz: np.ndarray, g: Grid, radius: float, k_min: int, rows: list[tuple[int, int]] | None = None, threads: int = 1
):  # type: ignore[no-untyped-def]
    pcls = np.zeros(len(g.order), np.uint8)
    rows = rows or [(0, g.ncy)]

    def run(r):  # type: ignore[no-untyped-def]
        return gk.noise_rows(
            xyz, g.order, g.start, g.ncx, g.ncy, r[0], r[1], g.ox, g.oy, g.c, 0, 0, radius, k_min, pcls
        )

    with ThreadPoolExecutor(threads) as ex:
        count = sum(ex.map(run, rows))
    flags = np.zeros(len(xyz), bool)
    flags[g.order[pcls == 7]] = True
    assert count == int(flags.sum())
    return flags


@pytest.mark.parametrize("radius,k_min", [(2.0, 3), (0.7, 1), (3.3, 8)])
def test_noise_equals_brute_force(radius: float, k_min: int) -> None:
    from scipy.spatial import cKDTree

    rng = np.random.default_rng(21)
    # a sparse volume: neighbours counts around k_min, and a wall of points with tall cells
    xyz = np.vstack(
        [
            rng.random((6000, 3)) * [60.0, 40.0, 25.0],
            np.column_stack([np.full(1500, 30.02), rng.random(1500) * 40, rng.random(1500) * 25]),
        ]
    )
    xyz = xyz + cloud.ORIGIN
    flags = _noise(xyz, Grid(xyz, 0.5), radius, k_min)
    local = xyz - xyz.min(axis=0)
    counts = cKDTree(local).query_ball_point(local, radius, return_length=True) - 1
    assert np.array_equal(flags, counts < k_min)
    assert 0 < flags.sum() < len(xyz)


def test_outliers_of_the_plain_become_noise_and_nothing_else() -> None:
    s = cloud.plain(31, side=100.0, buildings=1, trees=40)
    g = Grid(s.xyz, 0.5)
    flags = _noise(s.xyz, g, 2.0, 3)
    assert flags[s.tag == cloud.T_OUTLIER].all()
    assert not flags[s.tag != cloud.T_OUTLIER].any()
    # strips on threads give the same flags as one call
    rows = [(r, min(r + 16, g.ncy)) for r in range(0, g.ncy, 16)]
    for threads in (2, 8):
        assert np.array_equal(_noise(s.xyz, g, 2.0, 3, rows, threads), flags)


# --- the lowest return of a cell -------------------------------------------------------------------------


def _minima(xyz, g: Grid, pcls, ret=None):  # type: ignore[no-untyped-def]
    n = g.ncx * g.ncy
    cmin, mx, my, mz = np.empty(n, np.int32), np.zeros(n, np.float32), np.zeros(n, np.float32), np.zeros(n)
    dummy = np.zeros(1, np.uint8)
    for r in range(0, g.ncy, 7):
        gk.minima_rows(
            xyz,
            dummy if ret is None else ret,
            ret is not None,
            g.order,
            g.start,
            pcls,
            g.ncx,
            r,
            min(r + 7, g.ncy),
            g.ox,
            g.oy,
            g.c,
            0,
            0,
            cmin,
            mx,
            my,
            mz,
        )
    return cmin, mx, my, mz


def test_minima_are_the_lowest_points_noise_aside() -> None:
    rng = np.random.default_rng(41)
    xyz = rng.random((30_000, 3)) * [40.0, 30.0, 5.0] + cloud.ORIGIN
    xyz[:, 2] = np.round(xyz[:, 2], 1)  # equal heights inside a cell: the smaller number wins
    g = Grid(xyz, 0.5)
    pcls = np.zeros(len(g.order), np.uint8)
    pcls[rng.random(len(pcls)) < 0.2] = 7
    cmin, mx, my, mz = _minima(xyz, g, pcls)
    cell_of = np.repeat(np.arange(g.ncx * g.ncy), np.diff(g.start))
    alive = pcls != 7
    ids = g.order[alive].astype(np.int64)
    rank = np.lexsort((ids, xyz[ids, 2], cell_of[alive]))
    first = np.ones(len(rank), bool)
    first[1:] = cell_of[alive][rank][1:] != cell_of[alive][rank][:-1]
    want = np.full(g.ncx * g.ncy, -1, np.int64)
    want[cell_of[alive][rank][first]] = ids[rank][first]
    got = np.where(cmin >= 0, g.order[np.maximum(cmin, 0)], -1)
    assert np.array_equal(got, want)
    has = cmin >= 0
    cells = np.flatnonzero(has)
    cx = g.ox + (cells % g.ncx + 0.5) * g.c + mx[has]
    cy = g.oy + (cells // g.ncx + 0.5) * g.c + my[has]
    assert np.abs(cx - xyz[got[has], 0]).max() < 1e-6 and np.abs(cy - xyz[got[has], 1]).max() < 1e-6
    assert np.array_equal(mz[has], xyz[got[has], 2])
    assert np.abs(mx[has]).max() <= 0.25 + 1e-6 and np.abs(my[has]).max() <= 0.25 + 1e-6


def test_minima_prefer_last_returns() -> None:
    xyz = np.array([[0.1, 0.1, 5.0], [0.2, 0.2, 1.0], [0.3, 0.3, 2.0], [0.7, 0.1, 3.0], [0.8, 0.2, 4.0]]) + cloud.ORIGIN
    ret = np.array([0x11, 0x21, 0x22, 0x31, 0x32], np.uint8)  # the lowest of cell 0 is a first return of two
    g = Grid(xyz, 0.5)
    pcls = np.zeros(len(xyz), np.uint8)
    cmin, _mx, _my, mz = _minima(xyz, g, pcls, ret)
    assert g.order[cmin[0]] == 2 and mz[0] == xyz[2, 2]  # the last return 2 m, not the first return 1 m
    assert g.order[cmin[1]] == 3  # no last return in the cell: the lowest of what there is
    cmin, _mx, _my, _mz = _minima(xyz, g, pcls)
    assert g.order[cmin[0]] == 1


# --- planes ----------------------------------------------------------------------------------------------


def test_lower_plane_is_least_squares_on_clean_ground_and_the_ground_under_vegetation() -> None:
    rng = np.random.default_rng(51)
    n = 28
    dx, dy = rng.random(n) * 3 - 1.5, rng.random(n) * 3 - 1.5
    z = 7.0 + 1.19 * dx - 0.4 * dy + rng.normal(0, 0.02, n)
    use = np.zeros(n, np.uint8)
    ok, a, b, c = gk.lower_plane(dx, dy, z, use, n, 0.1)
    ref = np.linalg.lstsq(np.column_stack([np.ones(n), dx, dy]), z, rcond=None)[0]
    assert ok and np.allclose([a, b, c], ref, atol=1e-9) and use.all()
    high = rng.random(n) < 0.55  # more than half of the cells hold vegetation only
    z2 = np.where(high, z + 0.5 + 2.5 * rng.random(n), z)
    ok, a, b, c = gk.lower_plane(dx, dy, z2, use, n, 0.1)
    assert ok and abs(a - 7.0) < 0.05 and abs(b - 1.19) < 0.05 and abs(c + 0.4) < 0.05
    assert not use[high].any() and use[~high].all()
    # points on a line have no slope across it
    ok, *_ = gk.fit_plane(dx, 2.0 * dx, z, np.ones(n, np.uint8), n)
    assert not ok
    ok, *_ = gk.fit_plane(dx, dy, z, np.array([1, 1] + [0] * (n - 2), np.uint8), n)
    assert not ok
