"""The z-grids of the shells (подшаг 21.9; ТЗ 8.11.4; ADR 0028 decision 25): z of a column = the bottom of the LOWEST
mined parent of the shell, otherwise the relief at the centre of the column, no relief — the top of the grid, a centre
outside the plan of the relief — NaN (a gap is not 0); the minimum is kept as the shells grow; a model turned about Z;
all shells in one pass; `sidecar:zgrid` — Parquet (i, j, z1 … zN). Every case against a plain-Python reference."""

from __future__ import annotations

import io
import math
from collections.abc import Callable

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks.schema import GridV2
from bf_core.geo import dz_kernels
from bf_core.mine import surfaces as sf
from bf_core.mine.fields import ShellMap

NAN = float("nan")
Relief = Callable[[float, float], float]


def make_grid(
    counts: tuple[int, int, int] = (5, 4, 6), size: tuple[float, float, float] = (10.0, 20.0, 5.0),
    corner: tuple[float, float, float] = (1000.0, 2000.0, 300.0), rotation: float = 0.0,
) -> GridV2:  # fmt: skip
    return GridV2.of({"corner": list(corner), "counts": list(counts), "size": list(size), "rotation": rotation})


def centre_xy(g: GridV2, i: int, j: int) -> tuple[float, float]:
    """The centre of the column in the project axes by the plain formula R_z(φ)·q + O."""
    a = math.radians(g.rotation)
    sx, sy, _ = g.size or (0.0, 0.0, 0.0)
    qx, qy = (i + 0.5) * sx, (j + 0.5) * sy
    return (g.corner[0] + math.cos(a) * qx - math.sin(a) * qy, g.corner[1] + math.sin(a) * qx + math.cos(a) * qy)


def plane(a: float, b: float, c: float, box: tuple[float, float, float, float]) -> dz_kernels.SurfaceIndex:
    """The relief z = c + a·x + b·y over the box (x0, y0, x1, y1): two triangles."""
    x0, y0, x1, y1 = box
    xyz = np.array([[x, y, c + a * x + b * y] for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))])
    return dz_kernels.surface_index(xyz, np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32))


def reference(g: GridV2, mined: dict[tuple[int, int, int], int], relief: Relief | None, total: int) -> np.ndarray:
    """[total, ny·nx]: the surfaces of the shells 1…total by the definition, column by column."""
    nx, ny, nz = g.counts
    dz = (g.size or (0.0, 0.0, 0.0))[2]
    out = np.full((total, nx * ny), NAN)
    for n in range(1, total + 1):
        for j in range(ny):
            for i in range(nx):
                ks = [k for (a, b, k), s in mined.items() if (a, b) == (i, j) and 0 < s <= n]
                if ks:
                    z = g.corner[2] + min(ks) * dz
                elif relief is None:
                    z = g.corner[2] + nz * dz
                else:
                    z = relief(*centre_xy(g, i, j))
                out[n - 1, i + nx * j] = z
    return out


def smap_of(g: GridV2, mined: dict[tuple[int, int, int], int]) -> ShellMap:
    nx, ny, _ = g.counts
    cells = np.array([i + nx * (j + ny * k) for (i, j, k) in mined], dtype=np.uint64)
    return ShellMap.from_parents(cells, np.array(list(mined.values())))


def random_pit(g: GridV2, n: int, seed: int = 3, share: float = 0.5) -> dict[tuple[int, int, int], int]:
    rng = np.random.default_rng(seed)
    nx, ny, nz = g.counts
    return {(i, j, k): int(rng.integers(1, n + 1)) for i in range(nx) for j in range(ny) for k in range(nz)
            if rng.random() < share}  # fmt: skip


def same(a: np.ndarray, b: np.ndarray, tol: float = 1e-9) -> bool:
    return bool(np.array_equal(np.isnan(a), np.isnan(b)) and np.allclose(a, b, rtol=0.0, atol=tol, equal_nan=True))


# --- z = the bottom of the lowest mined parent, otherwise the relief ---------------------------------------------


def test_bottom_of_lowest_mined_else_relief() -> None:
    g = make_grid()
    mined = {(1, 1, 3): 1, (1, 1, 4): 1, (2, 0, 2): 2, (2, 0, 5): 1, (3, 3, 0): 3}
    relief = plane(0.0, 0.0, 330.0, (900.0, 1900.0, 1200.0, 2200.0))  # a flat relief at z = 330
    zg = sf.z_grids(g, smap_of(g, mined), relief)
    nx = 5
    assert zg.shells == (1, 2, 3) and zg.z.shape == (3, 20)
    # the column (1, 1): the lowest mined parent is k = 3 → its bottom 300 + 3·5 = 315, in every shell
    assert [zg.z[n, 1 + nx * 1] for n in range(3)] == [315.0] * 3
    # the column (2, 0): k = 5 is shell 1, k = 2 joins in shell 2 — the bottom of the lowest one of the pit
    assert zg.z[0, 2] == 300.0 + 5 * 5.0 and zg.z[1, 2] == 300.0 + 2 * 5.0 and zg.z[2, 2] == 310.0
    assert zg.z[0, 3 + nx * 3] == 330.0 and zg.z[2, 3 + nx * 3] == 300.0  # (3, 3): the relief, then the floor k = 0
    assert zg.z[0, 0] == 330.0  # a column no shell reaches: the relief at its centre
    assert same(zg.z, reference(g, mined, lambda x, y: 330.0, 3))


def test_without_relief_is_the_top_of_the_grid() -> None:
    g = make_grid()
    mined = {(0, 0, 2): 1}
    zg = sf.z_grids(g, smap_of(g, mined))
    assert zg.z[0, 0] == 310.0 and (zg.z[0, 1:] == 300.0 + 6 * 5.0).all()  # the top = corner + nz·dz
    assert same(zg.z, reference(g, mined, None, 1))


def test_inclined_relief_is_taken_at_the_centre_of_the_column() -> None:
    g = make_grid()
    mined = random_pit(g, 4, seed=8, share=0.15)

    def f(x: float, y: float) -> float:
        return 340.0 + 0.2 * (x - 1000.0) - 0.1 * (y - 2000.0)

    relief = plane(0.2, -0.1, 340.0 - 0.2 * 1000.0 + 0.1 * 2000.0, (900.0, 1900.0, 1200.0, 2200.0))
    zg = sf.z_grids(g, smap_of(g, mined), relief)
    assert same(zg.z, reference(g, mined, f, 4))


def test_the_minimum_grows_with_the_shells_and_is_nested() -> None:
    g = make_grid((6, 5, 8))
    mined = random_pit(g, 6, seed=21, share=0.35)
    relief = plane(0.0, 0.0, 350.0, (900.0, 1900.0, 1300.0, 2300.0))
    zg = sf.z_grids(g, smap_of(g, mined), relief, n_shells=6)
    assert same(zg.z, reference(g, mined, lambda x, y: 350.0, 6))
    assert (np.diff(zg.z, axis=0) <= 0.0).all()  # a larger shell never lies above a smaller one
    more = sf.z_grids(g, smap_of(g, mined), relief, n_shells=9)  # shells 7…9 add nothing: the same surface
    assert zg.shells == (1, 2, 3, 4, 5, 6) and more.shells == tuple(range(1, 10))
    assert np.array_equal(more.z[6], zg.z[5]) and np.array_equal(more.z[8], zg.z[5])


# --- NaN outside the plan -----------------------------------------------------------------------


def test_nan_outside_the_plan_of_the_relief_only_where_nothing_is_mined() -> None:
    g = make_grid()  # the plan x 1000…1050, y 2000…2080; the centres of the columns x = 1005 … 1045
    relief = plane(0.0, 0.0, 330.0, (900.0, 1900.0, 1021.0, 2200.0))  # reaches the centres x = 1005, 1015 only
    mined = {(4, 2, 1): 1}  # a mined column outside the plan: it has its own z
    zg = sf.z_grids(g, smap_of(g, mined), relief)
    z = zg.z[0].reshape(4, 5)  # [j, i]
    assert (z[:, 0:2] == 330.0).all()
    assert int(np.isnan(z[:, 2:]).sum()) == 4 * 3 - 1 and z[2, 4] == 305.0  # NaN, not 0; the mined column is kept
    assert same(zg.z, reference(g, mined, lambda x, y: 330.0 if x < 1021.0 else NAN, 1))


# --- a model turned about Z -----------------------------------------------------------------------


@pytest.mark.parametrize("angle", [0.0, 90.0, 30.0, -47.5])
def test_turned_model(angle: float) -> None:
    g = make_grid((5, 4, 6), rotation=angle)
    mined = random_pit(g, 3, seed=11, share=0.2)

    def f(x: float, y: float) -> float:
        return 330.0 + 0.05 * (x - 1000.0) + 0.03 * (y - 2000.0)

    relief = plane(0.05, 0.03, 330.0 - 0.05 * 1000.0 - 0.03 * 2000.0, (700.0, 1700.0, 1400.0, 2400.0))
    zg = sf.z_grids(g, smap_of(g, mined), relief)
    assert same(zg.z, reference(g, mined, f, 3))
    xy = sf.centres(g)  # the nodes: the centres of the columns in the project axes
    want = np.array([centre_xy(g, i, j) for j in range(4) for i in range(5)])
    assert np.allclose(xy, want, rtol=0.0, atol=1e-9)
    if angle == 90.0:
        assert np.allclose(xy[0], [1000.0 - 10.0, 2000.0 + 5.0], atol=1e-9)  # exact turn: x = −qy, y = qx


# --- the arguments and the sidecar -----------------------------------------------------------------------


def test_subset_of_shells_and_refusal() -> None:
    g = make_grid()
    sm = smap_of(g, random_pit(g, 5, seed=2))
    full = sf.z_grids(g, sm)
    part = sf.z_grids(g, sm, shells=[4, 2, 2])
    assert part.shells == (2, 4) and np.array_equal(part.of(4), full.of(4)) and np.array_equal(part.of(2), full.of(2))
    for bad in (0, 6, -1):
        with pytest.raises(ApiError) as e:
            sf.z_grids(g, sm, shells=[1, bad])
        assert (e.value.status, e.value.code, e.value.extra["param"]) == (422, "bad_param", "shells")
    nothing = ShellMap.from_parents(np.zeros(0, dtype=np.uint64), np.zeros(0))
    assert sf.z_grids(g, nothing, n_shells=2).z.shape == (2, 20)


def test_parquet_round_trip_keeps_nan() -> None:
    import pyarrow.parquet as pq

    g = make_grid()
    relief = plane(0.0, 0.0, 330.0, (900.0, 1900.0, 1021.0, 2200.0))
    zg = sf.z_grids(g, smap_of(g, random_pit(g, 3, seed=4, share=0.1)), relief)
    data = zg.to_parquet()
    t = pq.read_table(io.BytesIO(data))
    assert t.column_names == ["i", "j", "z1", "z2", "z3"] and t.num_rows == 20
    assert t.column("i").to_pylist()[:6] == [0, 1, 2, 3, 4, 0] and t.column("j").to_pylist()[4:6] == [0, 1]
    assert t.column("z1").null_count == 0  # NaN stays NaN, it is not a null
    back = sf.ZGrids.from_parquet(data)
    assert (back.nx, back.ny, back.shells) == (5, 4, (1, 2, 3)) and np.array_equal(back.z, zg.z, equal_nan=True)
    assert np.isnan(back.z).any()
