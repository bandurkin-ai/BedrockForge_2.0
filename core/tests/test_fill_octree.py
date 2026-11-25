"""Filling by wireframes, stages «Дробление» and «Слияние» (подшаг 17.18; ТЗ 8.7.3, Т8.7-2, Т8.7-3, Т8.7-5; ADR 0023
§6): boundary parents split to the level L by the triangle–box test, FRAC by m³ points, merging of children,
priorities of several wireframes, the modes, the same bits with 1, 2, 4, 8 threads."""

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
from bf_core.blocks import geometry as geo  # noqa: E402
from bf_core.blocks import schema  # noqa: E402
from bf_core.blocks.fill import boundary as fb  # noqa: E402
from bf_core.blocks.fill import columns as fc  # noqa: E402
from bf_core.blocks.fill import frac as ff  # noqa: E402
from bf_core.blocks.fill import octree as fo  # noqa: E402
from bf_core.blocks.schema import GridV2, SubblockSpec  # noqa: E402


def grid_of(
    counts: tuple[int, int, int],
    size: float = 10.0,
    levels: tuple[int, int, int] = (0, 0, 0),
    rotation: float = 0.0,
) -> GridV2:
    return GridV2(
        corner=(0.0, 0.0, 0.0),
        counts=counts,
        size=(size, size, size),
        rotation=rotation,
        sub=SubblockSpec.of_levels(levels),
    )


def box(lo: tuple[float, ...], hi: tuple[float, ...]) -> tuple[np.ndarray, np.ndarray]:
    """An axis-aligned box, outward normals."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = np.array(
        [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]
    )
    t = [[0, 3, 2], [0, 2, 1], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2],
         [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]]  # fmt: skip
    return v, np.array(t, dtype=np.int32)


def rotated_cube(side: float, centre: tuple[float, float, float], deg: float) -> tuple[np.ndarray, np.ndarray]:
    h = side / 2.0
    v, t = box((-h, -h, -h), (h, h, h))
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    v = v @ np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])
    return v + np.asarray(centre), t


def mesh_of(grid: GridV2, vt: tuple[np.ndarray, np.ndarray], name: str = "Тело") -> fc.Mesh:
    return fc.Mesh.of_project(grid, vt[0], vt[1], name)


def sphere(grid: GridV2, n: int, radius: float, centre: tuple[float, float, float], name: str = "Шар") -> fc.Mesh:
    v, t = uv_sphere(n, radius=radius, centre=(0.0, 0.0, 0.0))
    return mesh_of(grid, (v + np.asarray(centre), t), name)


def sha(*arrays: np.ndarray) -> str:
    h = hashlib.sha256()
    for a in arrays:
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def fill_hash(f: fo.Fill) -> str:
    return sha(f.p, f.s, f.w, f.code, f.frac)


def volume_of(grid: GridV2, f: fo.Fill, code: int | None = None) -> float:
    """Σ V·FRAC of the rows (of a code)."""
    sel = np.ones(len(f.p), bool) if code is None else f.code == code
    return float(geo.row_volumes(grid, f.p[sel], f.s[sel], f.w[sel], f.frac[sel]).sum())


def cell_volume(grid: GridV2, f: fo.Fill, code: int | None = None) -> float:
    """Σ V of the rows (FRAC ignored)."""
    sel = np.ones(len(f.p), bool) if code is None else f.code == code
    return float(geo.row_volumes(grid, f.p[sel], f.s[sel], f.w[sel]).sum())


# --- Т8.7-2, Т8.7-3 ----------------------------------------------------------------------------------------


def test_t872_cube_turned_30_degrees_on_a_grid_without_turn() -> None:
    """A cube of 100 m turned by 30° about Z, a model of 5 m without a turn, L = 3, m = 4: the volume ±0,5 %."""
    grid = grid_of((40, 40, 40), size=5.0, levels=(3, 3, 3))
    mesh = mesh_of(grid, rotated_cube(100.0, (100.0, 100.0, 100.0), 30.0))
    f = fo.fill_grid(grid, [mesh], m=4)
    err = volume_of(grid, f) / 1e6 - 1.0
    print(f"T8.7-2: {len(f.p)} rows, {f.boundary} boundary parents, error {err:+.3%}")
    assert abs(err) < 0.005
    assert f.frac.min() > 0.0 and f.frac.max() == 1.0 and (f.code == 0).all()
    # the same cube, no sub-blocks, m = 10: the sample alone
    plain = grid_of((40, 40, 40), size=5.0)
    f0 = fo.fill_grid(plain, [mesh_of(plain, rotated_cube(100.0, (100.0, 100.0, 100.0), 30.0))], m=10)
    assert abs(volume_of(plain, f0) / 1e6 - 1.0) < 0.005


def test_t873_sphere_with_sub_blocks_and_without() -> None:
    """A sphere R = 50 m, blocks of 10 m: L = 3 (m = 4) and without sub-blocks (m = 10), the volume ±1 % of 4/3·π·R³."""
    exact = 4.0 / 3.0 * math.pi * 50.0**3
    grid = grid_of((14, 14, 14), levels=(3, 3, 3))
    f = fo.fill_grid(grid, [sphere(grid, 80_000, 50.0, (70.0, 70.0, 70.0))], m=4)
    err = volume_of(grid, f) / exact - 1.0
    print(f"T8.7-3 L=3: {len(f.p)} rows, error {err:+.3%}")
    assert abs(err) < 0.01
    plain = grid_of((14, 14, 14))
    f0 = fo.fill_grid(plain, [sphere(plain, 80_000, 50.0, (70.0, 70.0, 70.0))], m=10)
    err0 = volume_of(plain, f0) / exact - 1.0
    print(f"T8.7-3 no sub-blocks: {len(f0.p)} rows, error {err0:+.3%}")
    assert abs(err0) < 0.01
    assert (f0.w == 1).all() and (f0.s == 0).all()
    assert f0.frac[f0.frac < 1.0].min() >= 1e-3 - 1e-12 and len(f0.p) == len(np.unique(f0.p))


# --- Т8.7-5 ------------------------------------------------------------------------------------------------


def covers(grid: GridV2, f: fo.Fill) -> tuple[np.ndarray, np.ndarray]:
    """(parents, how many times each sub-cell of each parent is covered): n_parents × (dx·dy·dz)."""
    dx, dy, dz = grid.sub.div
    parents, inv = np.unique(f.p, return_inverse=True)
    cov = np.zeros((len(parents), dz, dy, dx), np.int32)
    for wx, wy, wz in {tuple(int(x) for x in r) for r in np.unique(f.w, axis=0)}:
        sel = np.flatnonzero((f.w == (wx, wy, wz)).all(axis=1))
        for ox, oy, oz in itertools.product(range(wx), range(wy), range(wz)):
            np.add.at(cov, (inv[sel], f.s[sel, 2] + oz, f.s[sel, 1] + oy, f.s[sel, 0] + ox), 1)
    return parents, cov


@pytest.mark.parametrize("mode", ["inside", "outside", "both"])
def test_t875_sub_blocks_do_not_overlap_and_do_not_exceed_the_parent(mode: str) -> None:
    grid = grid_of((8, 8, 8), levels=(3, 2, 1))
    f = fo.fill_grid(grid, [sphere(grid, 4000, 33.0, (40.0, 41.0, 39.0))], m=3, mode=mode)
    assert len(f.p) > 500 and schema.ascending(f.p, f.s)
    assert not grid.sub.bad_rows(f.s, f.w).any()
    parents, cov = covers(grid, f)
    assert cov.max() == 1  # nowhere twice
    whole = np.prod(grid.sub.div)
    vol = geo.row_volumes(grid, f.p, f.s, f.w)
    per_parent = np.bincount(np.searchsorted(parents, f.p), weights=vol, minlength=len(parents))
    assert (per_parent <= 1000.0 * (1.0 + 1e-12)).all()
    if mode == "both":  # nothing is left out: every parent is covered whole
        assert len(parents) == grid.cells and (cov.sum(axis=(1, 2, 3)) == whole).all()
        assert np.allclose(per_parent, 1000.0, rtol=1e-12)
    else:
        assert cov.sum(axis=(1, 2, 3)).min() < whole


# --- the reference: every sub-cell by its points, then merging, in plain Python -------------------------------


def reference_rows(
    grid: GridV2, mesh: fc.Mesh, parents: np.ndarray, m: int, mode: str, split: bool = True
) -> set[tuple]:
    """The rows of the boundary parents by the rules of 8.7.3 without any pruning: all sub-cells of a parent, the
    class by the centre ray, FRAC by m³ points of `inside_points`, then the merge of siblings. One body: the code 0,
    outside — 1. Rows are (P, sx, sy, sz, wx, wy, wz, code, FRAC)."""
    out: set = set()
    for pp in parents:
        out |= set(_parent_rows(grid, mesh, int(pp), m, mode, split))
    return out


def _decide(allow: dict[int, bool], inside_centre: bool, n_in: int, m3: int) -> tuple[int, float] | None:
    cnt = {0: n_in, 1: m3 - n_in}
    cc = 0 if inside_centre else 1
    if allow[cc]:
        return cc, cnt[cc] / m3
    best, most = None, 0
    for q in (0, 1):
        if allow[q] and cnt[q] > most:
            best, most = q, cnt[q]
    return None if best is None else (best, most / m3)


def _leaf(grid: GridV2, mesh: fc.Mesh, ijk: tuple[int, int, int], s: tuple[int, int, int], m: int, mode: str):  # type: ignore[no-untyped-def]
    d, div = grid.regular(), grid.sub.div
    allow = {0: mode in ("inside", "both"), 1: mode in ("outside", "both")}
    lo = [(ijk[a] + s[a] / div[a]) * d[a] for a in range(3)]
    hi = [(ijk[a] + (s[a] + 1) / div[a]) * d[a] for a in range(3)]
    cen = [(ijk[a] + (s[a] + 0.5) / div[a]) * d[a] for a in range(3)]
    if m == 1:
        pts = np.array([cen])
    else:
        h = [(hi[a] - lo[a]) / m for a in range(3)]
        pts = np.array(
            [
                [lo[0] + (a + 0.5) * h[0], lo[1] + (b + 0.5) * h[1], lo[2] + (c + 0.5) * h[2]]
                for a in range(m)
                for b in range(m)
                for c in range(m)
            ]  # fmt: skip
        )
    n_in = int(fc.inside_points(mesh, pts).sum())
    return _decide(allow, bool(fc.inside_points(mesh, np.array([cen]))[0]), n_in, m**3)


def _build(
    grid: GridV2, mesh: fc.Mesh, pp: int, ijk: tuple[int, int, int], s: tuple[int, int, int], w: tuple[int, int, int],
    m: int, mode: str, split: bool,
) -> list[tuple]:  # fmt: skip
    if w == (1, 1, 1) or not split:
        r = _leaf(grid, mesh, ijk, s, m, mode)
        return [] if r is None else [(pp, *s, *w, r[0], r[1])]
    h = [x // 2 for x in w]
    axes = [[(s[a], h[a]), (s[a] + h[a], w[a] - h[a])] if w[a] >= 2 else [(s[a], w[a])] for a in range(3)]
    rows: list[tuple] = []
    for cx, cy, cz in itertools.product(*axes):
        rows += _build(grid, mesh, pp, ijk, (cx[0], cy[0], cz[0]), (cx[1], cy[1], cz[1]), m, mode, split)
    if len(rows) >= 2:
        first = rows[0]
        same = all(r[7] == first[7] and r[8] == first[8] for r in rows) and first[8] in (0.0, 1.0)
        if same and sum(r[4] * r[5] * r[6] for r in rows) == w[0] * w[1] * w[2]:
            return [(pp, *s, *w, first[7], first[8])]
    return rows


def _parent_rows(grid: GridV2, mesh: fc.Mesh, pp: int, m: int, mode: str, split: bool) -> list[tuple]:
    nx, ny, _ = grid.counts
    ijk = (pp % nx, (pp // nx) % ny, pp // (nx * ny))
    return _build(grid, mesh, pp, ijk, (0, 0, 0), grid.sub.div, m, mode, split)


def rows_of(f: fo.Fill | fo.Rows, parents: np.ndarray) -> set[tuple]:
    sel = np.isin(f.p, parents)
    return {
        (int(p), *(int(x) for x in s), *(int(x) for x in w), int(c), float(fr))
        for p, s, w, c, fr in zip(f.p[sel], f.s[sel], f.w[sel], f.code[sel], f.frac[sel], strict=True)
    }


@pytest.mark.parametrize(
    ("levels", "m", "mode", "rotation"),
    [
        ((2, 2, 2), 3, "inside", 0.0),
        ((2, 2, 2), 1, "inside", 0.0),  # FRAC ∈ {0; 1}: the children merge a lot
        ((3, 1, 0), 2, "both", 0.0),  # the axes are exhausted at different levels
        ((2, 1, 2), 4, "outside", -37.0),
        ((0, 0, 0), 5, "inside", 12.0),  # no sub-blocks
    ],
)
def test_rows_equal_the_brute_force_with_the_merge(
    levels: tuple[int, int, int], m: int, mode: str, rotation: float
) -> None:
    """Every row of the octree with the pruning by triangles and the merge on the fly is the one of the brute force:
    all sub-cells by points, then merging the siblings."""
    grid = grid_of((7, 7, 7), size=10.0, levels=levels, rotation=rotation)
    mesh = (
        sphere(grid, 3000, 27.0, (33.0, 36.0, 35.0))
        if rotation == 0.0
        else mesh_of(
            grid, (rotated_cube(40.0, (30.0, 30.0, 30.0), 17.0)[0], rotated_cube(40.0, (30.0, 30.0, 30.0), 17.0)[1])
        )
    )
    f = fo.fill_grid(grid, [mesh], m=m, mode=mode)
    parents = np.unique(fb.boundary_parents(grid, mesh).p)
    assert len(parents) > 20
    want = reference_rows(grid, mesh, parents, m, mode)
    got = rows_of(f, parents)
    assert got == want
    assert len(want) >= len(parents) and (len(want) > len(parents) or levels == (0, 0, 0))


def test_merging_children_on_a_face_through_the_sub_cells() -> None:
    """A wall at x = 2.5 m on sub-cells of 2.5 m: the face is in the middle of a level-1 child (it splits), the child
    on the far side of the face is whole; nothing merges into the parent."""
    grid = grid_of((4, 4, 4), levels=(2, 2, 2))
    mesh = mesh_of(grid, box((2.5, 0.0, 0.0), (37.5, 40.0, 40.0)))
    f = fo.fill_grid(grid, [mesh], m=2)
    first = f.p == 0
    assert f.w[first].tolist().count([1, 1, 1]) == 16 and f.w[first].tolist().count([2, 2, 2]) == 4
    assert (f.s[first][(f.w[first] == 1).all(axis=1)][:, 0] == 1).all()  # the leaves: the column x = 1 of sub-cells
    assert volume_of(grid, f) == 35.0 * 40.0 * 40.0
    assert (f.frac == 1.0).all()
    # the parents past the face: one row each, merged up to the whole parent
    mid = f.p == 1
    assert mid.sum() == 1 and f.w[mid].tolist() == [[4, 4, 4]]
    # a box on the borders of the sub-cells of one parent: all eight children inside → one row of the parent
    one = grid_of((2, 2, 2), levels=(1, 1, 1))
    inner = mesh_of(one, box((-1.0, -1.0, -1.0), (21.0, 21.0, 21.0)))
    g = fo.fill_grid(one, [inner], m=2)
    assert len(g.p) == 8 and (g.w == 2).all() and (g.frac == 1.0).all()


# --- priorities, modes ---------------------------------------------------------------------------------------


def two_cubes(grid: GridV2, priorities: list[int | None] | None) -> fo.Fill:
    host = mesh_of(grid, box((0.0, 0.0, 0.0), (40.0, 40.0, 40.0)), "Вмещающая")
    ore = mesh_of(grid, box((10.0, 10.0, 10.0), (20.0, 20.0, 20.0)), "Руда")
    return fo.fill_grid(grid, [host, ore], priorities=priorities, mode="inside", m=2)


def test_priority_the_higher_one_takes_the_overlap() -> None:
    grid = grid_of((4, 4, 4), levels=(2, 2, 2))
    f = two_cubes(grid, [0, 5])  # ore is the main one
    assert volume_of(grid, f, 1) == 1000.0 and volume_of(grid, f, 0) == 64_000.0 - 1000.0
    assert (f.frac == 1.0).all() and cell_volume(grid, f) == 64_000.0  # nothing twice
    f = two_cubes(grid, [5, 0])  # the host is the main one: the ore gets nothing
    assert volume_of(grid, f, 1) == 0.0 and volume_of(grid, f, 0) == 64_000.0
    # equal or no priorities: the earlier in the list wins
    f = two_cubes(grid, None)
    assert volume_of(grid, f, 0) == 64_000.0 and volume_of(grid, f, 1) == 0.0
    g = fo.fill_grid(
        grid,
        [mesh_of(grid, box((10.0, 10.0, 10.0), (20.0, 20.0, 20.0)), "Руда"),
         mesh_of(grid, box((0.0, 0.0, 0.0), (40.0, 40.0, 40.0)), "Вмещающая")],
        m=2,
    )  # fmt: skip
    assert volume_of(grid, g, 0) == 1000.0 and volume_of(grid, g, 1) == 63_000.0


def test_priority_with_cut_boundaries_keeps_the_volumes() -> None:
    """Two overlapping bodies cut through the sub-cells: Σ FRAC·V of the codes is the union and the overlap goes to
    the main one (±0,5 %)."""
    grid = grid_of((8, 8, 8), size=5.0, levels=(2, 2, 2))
    a = mesh_of(grid, rotated_cube(24.0, (18.0, 20.0, 20.0), 20.0), "A")
    b = mesh_of(grid, rotated_cube(24.0, (26.0, 20.0, 20.0), -15.0), "B")
    lone_a = fo.fill_grid(grid, [a], m=4)
    lone_b = fo.fill_grid(grid, [b], m=4)
    both = fo.fill_grid(grid, [a, b], priorities=[1, 2], m=4)
    va, vb = volume_of(grid, lone_a), volume_of(grid, lone_b)
    assert abs(va / 24.0**3 - 1.0) < 0.03 and 0.5 * 24.0**3 < vb < 1.03 * 24.0**3  # (B is cut by the grid)
    # B is the main one: its volume is whole, A keeps what B does not take
    assert abs(volume_of(grid, both, 1) / vb - 1.0) < 0.05
    union = volume_of(grid, both, 0) + volume_of(grid, both, 1)
    assert union < va + vb and union > max(va, vb) * 1.2
    assert schema.ascending(both.p, both.s)


def test_modes_inside_outside_both() -> None:
    grid = grid_of((4, 4, 4), levels=(2, 2, 2))
    cube = mesh_of(grid, box((7.5, 7.5, 7.5), (22.5, 22.5, 22.5)))
    fin = fo.fill_grid(grid, [cube], mode="inside", m=2)
    fout = fo.fill_grid(grid, [cube], mode="outside", m=2)
    fboth = fo.fill_grid(grid, [cube], mode="both", m=2)
    assert (fin.code == 0).all() and (fout.code == 1).all() and fout.outside == 1
    assert volume_of(grid, fin) == 15.0**3 and cell_volume(grid, fin) == 15.0**3
    assert volume_of(grid, fout) == 64_000.0 - 15.0**3
    assert cell_volume(grid, fboth) == 64_000.0 and set(fboth.code.tolist()) == {0, 1}
    assert volume_of(grid, fboth, 0) == 15.0**3 and volume_of(grid, fboth, 1) == 64_000.0 - 15.0**3
    assert len(fboth.p) == len(fin.p) + len(fout.p)  # the faces are on sub-cells: the three runs agree
    assert schema.ascending(fboth.p, fboth.s) and schema.ascending(fout.p, fout.s)


def test_a_cut_body_inside_plus_outside_is_the_whole_grid() -> None:
    """A body that cuts the sub-cells: the volume of "inside" and of "outside" add up to the grid exactly (every
    sub-cell with a point of a class is taken with that share)."""
    grid = grid_of((6, 6, 6), levels=(2, 2, 2))
    mesh = sphere(grid, 3000, 21.0, (31.0, 29.0, 30.0))
    vin = volume_of(grid, fo.fill_grid(grid, [mesh], mode="inside", m=3))
    vout = volume_of(grid, fo.fill_grid(grid, [mesh], mode="outside", m=3))
    assert abs((vin + vout) / 216_000.0 - 1.0) < 1e-12


def test_a_row_without_a_wanted_class_is_kept_with_no_code_when_coding_a_model() -> None:
    grid = grid_of((4, 4, 4), levels=(2, 2, 2))
    mesh = mesh_of(grid, box((7.5, 7.5, 7.5), (22.5, 22.5, 22.5)))
    bodies = ff.Bodies.of(grid, [mesh])
    b = fb.boundary_parents(grid, mesh)
    joint = fo.join_boundaries(bodies, [b])
    p = np.arange(grid.cells, dtype=np.uint64)  # a model of whole parents
    keep = fo.split_rows(grid, bodies, joint, p, m=2, mode="inside", split=False, keep=True)
    assert len(keep.p) == grid.cells and (keep.src == np.arange(grid.cells)).all()
    inside_p = {1 + 4 * (1 + 4 * 1)}  # parent (1, 1, 1): centre (15, 15, 15), whole inside
    assert keep.code[list(inside_p)[0]] == 0 and keep.frac[list(inside_p)[0]] == 1.0
    assert keep.code[0] == -1 and np.isnan(keep.frac[0])  # far away: no code, no FRAC (not zero)
    boundary = keep.code >= 0
    assert boundary.sum() == 8 and (keep.frac[boundary] > 0.0).all()
    # the same by splitting: the children keep the number of their row
    cut = fo.split_rows(grid, bodies, joint, p, m=2, mode="inside", split=True, keep=True)
    assert len(cut.p) > len(keep.p) and set(cut.src.tolist()) == set(range(grid.cells))
    assert (cut.code == -1).sum() > 0 and np.isnan(cut.frac[cut.code == -1]).all()
    vol = geo.row_volumes(grid, cut.p, cut.s, cut.w, np.where(cut.code >= 0, cut.frac, np.nan))
    assert np.nansum(vol) == 15.0**3 and np.isnan(vol).any()
    # without keep: the rows with nothing wanted are not there
    drop = fo.split_rows(grid, bodies, joint, p, m=2, mode="inside", split=True, keep=False)
    assert (drop.code == 0).all() and len(drop.p) == (cut.code >= 0).sum()


# --- points ----------------------------------------------------------------------------------------------------


def test_class_points_and_box_codes_follow_inside_points() -> None:
    grid = grid_of((6, 6, 6), levels=(2, 2, 2))
    big = mesh_of(grid, box((5.0, 5.0, 5.0), (55.0, 55.0, 55.0)), "Большой")
    small = sphere(grid, 2000, 12.0, (30.0, 30.0, 30.0), "Малый")
    one = ff.Bodies.of(grid, [small])
    rng = np.random.default_rng(7)
    q = rng.uniform(0.0, 60.0, (3000, 3))
    want = fc.inside_points(small, q)
    assert (ff.class_points(one, q) == np.where(want, 0, 1)).all() and want.sum() > 20
    two = ff.Bodies.of(grid, [big, small], priorities=[0, 1])  # the small one is the main one inside the big one
    cls = ff.class_points(two, q)
    inb, ins = fc.inside_points(big, q), fc.inside_points(small, q)
    assert (cls == np.where(ins, 1, np.where(inb, 0, 2))).all() and set(cls.tolist()) == {0, 1, 2}
    # boxes: the share of m³ points; a box far from everything is outside whole
    boxes = np.array(
        [[0.0, 0.0, 0.0, 2.0, 2.0, 2.0], [28.0, 28.0, 28.0, 32.0, 32.0, 32.0], [5.0, 5.0, 5.0, 28.0, 28.0, 28.0]]
    )
    code, frac = ff.box_codes(two, boxes, m=3, mode="both")
    assert code.tolist() == [2, 1, 0] and frac[0] == 1.0 and frac[1] == 1.0 and 0.0 < frac[2] < 1.0
    with pytest.raises(ApiError) as e:
        ff.box_codes(two, boxes, m=17)
    assert e.value.status == 422 and e.value.code == "fill_m" and e.value.extra["param"] == "m"
    with pytest.raises(ApiError) as e:
        ff.box_codes(two, boxes, mode="снаружи")
    assert e.value.code == "fill_mode" and e.value.extra["param"] == "mode" and "Режим" in e.value.message


def test_bad_arguments_are_422_in_russian() -> None:
    grid = grid_of((4, 4, 4), levels=(1, 1, 1))
    mesh = mesh_of(grid, box((5.0, 5.0, 5.0), (25.0, 25.0, 25.0)))
    for kw, code in (
        ({"m": 0}, "fill_m"),
        ({"m": 2.5}, "fill_m"),
        ({"m": True}, "fill_m"),
        ({"mode": "x"}, "fill_mode"),
    ):
        with pytest.raises(ApiError) as e:
            fo.fill_grid(grid, [mesh], **kw)  # type: ignore[arg-type]
        assert e.value.status == 422 and e.value.code == code and e.value.hint
    with pytest.raises(ApiError) as e:
        ff.Bodies.of(grid, [])
    assert e.value.code == "fill_wireframes"
    tensors = GridV2(corner=(0.0, 0.0, 0.0), counts=(2, 2, 2), tensors=((5.0, 5.0), (5.0, 5.0), (5.0, 5.0)))
    with pytest.raises(ApiError) as e:
        ff.Bodies.of(tensors, [mesh])
    assert e.value.code == "variable_grid"


def test_an_unclosed_wireframe_is_refused_before_the_fill() -> None:
    grid = grid_of((4, 4, 4), levels=(1, 1, 1))
    v, t = box((5.0, 5.0, 5.0), (25.0, 25.0, 25.0))
    with pytest.raises(ApiError) as e:
        mesh_of(grid, (v, t[:-1]), "Открытый")
    assert e.value.status == 422 and e.value.code == "wireframe_open" and "Открытый" in e.value.message


# --- threads -----------------------------------------------------------------------------------------------------


def test_one_hash_with_1_2_4_and_8_threads() -> None:
    grid = grid_of((10, 10, 10), levels=(2, 2, 2))
    a = sphere(grid, 4000, 30.0, (50.0, 50.0, 50.0), "A")
    b = mesh_of(grid, rotated_cube(35.0, (45.0, 55.0, 50.0), 25.0), "B")
    ref = fo.fill_grid(grid, [a, b], priorities=[0, 1], mode="both", m=3)
    assert len(ref.p) > 2000
    for threads in (1, 2, 4, 8):
        assert fill_hash(fo.fill_grid(grid, [a, b], priorities=[0, 1], mode="both", m=3, threads=threads)) == fill_hash(
            ref
        )
    # the chunks of the roots are fixed whatever their size: one root per chunk gives the same rows
    bodies = ff.Bodies.of(grid, [a, b], [0, 1])
    joint = fo.join_boundaries(bodies, [fb.boundary_parents(grid, a), fb.boundary_parents(grid, b)])
    r1 = fo.split_rows(grid, bodies, joint, joint.p, m=3, mode="both")
    r8 = fo.split_rows(grid, bodies, joint, joint.p, m=3, mode="both", threads=8, chunk=1)
    assert sha(r1.p, r1.s, r1.w, r1.code, r1.frac) == sha(r8.p, r8.s, r8.w, r8.code, r8.frac)


def test_ticks_go_up_to_one() -> None:
    grid = grid_of((6, 6, 6), levels=(1, 1, 1))
    seen: list[float] = []
    fo.fill_grid(grid, [sphere(grid, 1000, 20.0, (30.0, 30.0, 30.0))], tick=seen.append)
    assert seen and seen[-1] == 1.0 and all(0.0 <= x <= 1.0 for x in seen)
    assert all(b >= a - 1e-12 for a, b in zip(seen, seen[1:], strict=False))


# --- measure -----------------------------------------------------------------------------------------------------


@pytest.mark.skipif(os.environ.get("BF_MEASURE_FILL") != "1", reason="a measurement: BF_MEASURE_FILL=1 pytest -s")
def test_measure_the_split_1e6_triangles_20e6_parents() -> None:  # pragma: no cover
    """The whole fill of a sphere of 10⁶ triangles in a grid of 20 million parents (400 × 400 × 125, 10 m), L = 3,
    m = 4: seconds per stage and the rows."""
    import resource
    import time

    grid = grid_of((400, 400, 125), size=10.0, levels=(3, 3, 3))
    v, t = uv_sphere(1_000_000, radius=600.0, centre=(2000.0, 2000.0, 620.0))
    mesh = fc.Mesh.of(grid, v, t, "Шар")
    for threads in (1, 4):
        t0 = time.perf_counter()
        f = fo.fill_grid(grid, [mesh], threads=threads)
        print(
            f"threads={threads}: total {time.perf_counter() - t0:.1f} s, stages "
            f"{ {k: round(x, 1) for k, x in f.stages.items()} }; rows {len(f.p)}, boundary parents {f.boundary}, "
            f"whole {f.whole}; RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6:.2f} GB; "
            f"volume error {volume_of(grid, f) / (4 / 3 * math.pi * 600.0**3) - 1.0:+.3%}"
        )
