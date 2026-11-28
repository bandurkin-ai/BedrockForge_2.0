"""Inference of the grid of a model with sub-blocks of ANY division of the parent — 5, 3, 2·3 … not only 2^L (ADR 0030,
подшаг 27.4а; замечание А6: Micromine «Геомет+1;2 Участок_Январь_2026.DAT», родитель 10 × 10 × 10, 626 791 блок «не по
подъячейкам 2^L»).

The model is built from its cells (i, j, k, s, w) by hand — the ore body cut out of the parents, the parents on its
boundary split into sub-blocks of random widths (every axis its own composition of the division), the rest whole —
and the cells inferred from the centres and sizes alone are compared with the ones built. The reference is the
formula of 8.7.2 written out here (O + (n + (s + w/2)/div)·d), not the code under test."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry as geo
from bf_core.blocks.grid_infer import GridHint, GridScan, Unaligned, infer
from bf_core.blocks.grid_lattice import edge_tol
from bf_core.blocks.schema import GridV2

ORIGIN = (466167.5, 4584527.5, 202.5)
PARENT = (10.0, 10.0, 10.0)


def compositions(rng: np.random.Generator, total: int, parts: int) -> list[int]:
    """`total` sub-cells cut into 1…`parts` pieces of random widths: [2, 3], [5], [1, 1, 3] …"""
    k = int(rng.integers(1, min(parts, total) + 1))
    cuts = np.sort(rng.choice(np.arange(1, total), size=k - 1, replace=False)) if k > 1 else np.empty(0, dtype=int)
    edges = [0, *cuts.tolist(), total]
    return [b - a for a, b in zip(edges[:-1], edges[1:], strict=True)]


def ore_model(div, counts=(30, 30, 20), seed=1, parent=PARENT, parts=3, depth=0.22):
    """Cells (i, j, k, s, w) of an ore body (an ellipsoid of parents): whole parents inside, parents on its
    boundary (the outer `depth` of the radius) split into sub-blocks by tensor-product compositions of `div`."""
    rng = np.random.default_rng(seed)
    cells = []
    c = np.array(counts) / 2.0
    for i in range(counts[0]):
        for j in range(counts[1]):
            for k in range(counts[2]):
                r = math.sqrt(sum(((n + 0.5 - c[a]) / c[a]) ** 2 for a, n in enumerate((i, j, k))))
                if r > 1.0:
                    continue
                if r < 1.0 - depth:
                    cells.append((i, j, k, (0, 0, 0), tuple(div)))
                    continue
                axes = [compositions(rng, div[a], parts) for a in range(3)]
                offs = [np.concatenate([[0], np.cumsum(ax)[:-1]]).astype(int) for ax in axes]
                for ix, wx in enumerate(axes[0]):
                    for iy, wy in enumerate(axes[1]):
                        for iz, wz in enumerate(axes[2]):
                            cells.append((i, j, k, (offs[0][ix], offs[1][iy], offs[2][iz]), (wx, wy, wz)))
    return cells


def project_of(cells, div, origin=ORIGIN, phi=0.0, parent=PARENT):
    """Centres and sizes of the cells in the project axes, p = O + R_z(φ)·q, written out by hand."""
    n = len(cells)
    ijk = np.array([c[:3] for c in cells], dtype=float)
    s = np.array([c[3] for c in cells], dtype=float)
    w = np.array([c[4] for c in cells], dtype=float)
    q = (ijk + (s + w / 2) / np.asarray(div)) * np.asarray(parent)
    cos, sin = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    x = origin[0] + cos * q[:, 0] - sin * q[:, 1]
    y = origin[1] + sin * q[:, 0] + cos * q[:, 1]
    return np.column_stack([x, y, origin[2] + q[:, 2]]), w * np.asarray(parent) / np.asarray(div), n


def expected(cells, counts):
    p = np.array([i + counts[0] * (j + counts[1] * k) for i, j, k, _, _ in cells], dtype=np.uint64)
    return p, np.array([c[3] for c in cells], dtype=np.uint8), np.array([c[4] for c in cells], dtype=np.uint8)


def check_model(div, *, counts=(30, 30, 20), seed=1, hint=None, round_to=None, phi=0.0, tol=1e-6, parts=3):
    cells = ore_model(div, counts, seed, parts=parts)
    c, d, n = project_of(cells, div, phi=phi)
    quanta = None
    if round_to is not None:
        c, d = np.round(c, round_to), np.round(d, round_to)
        quanta = (edge_tol(0.5 * 10.0**-round_to, 0.5 * 10.0**-round_to),) * 3
    res = infer(c, d, hint or GridHint(rotation=phi), quanta=quanta)
    p, s, w = expected(cells, counts)
    g = res.grid
    assert g.size == PARENT and g.sub.div == tuple(div), (g.size, g.sub.div)
    assert np.abs(np.array(g.corner) - ORIGIN).max() <= tol
    assert res.valid is None and res.dropped == 0
    # the cells are the ones built (the rows of the rows_of in the order of the input)
    assert np.array_equal(res.p, p) and np.array_equal(res.s, s) and np.array_equal(res.w, w)
    # the centres of the model are the centres of the file (1e-6 m; the quantum of the column for a text)
    made = geo.centres(g, res.p, res.s, res.w)
    assert np.abs(made - c).max() <= max(tol, 1e-9)
    # the volume of the model equals the sum of the sizes (1e-12 relative)
    vol = float(geo.row_volumes(g, res.p, res.s, res.w).sum()) if hasattr(geo, "row_volumes") else None
    if vol is not None and round_to is None:
        assert vol == pytest.approx(float(np.prod(d, axis=1).sum()), rel=1e-12)
    assert res.check3.ok and res.check3.dist <= max(tol, 1e-9)
    return res, c, d, cells


def test_the_model_of_the_customer_parent_10_division_5_5_3():
    res, c, d, cells = check_model((5, 5, 3))
    assert len(cells) >= 20_000 and res.grid.sub.levels is None and res.grid.sub.sub
    assert any("взят по самому большому блоку" in w for w in res.warnings)  # the parent is the biggest block: said


def test_division_2_3_5():
    check_model((2, 3, 5), seed=2)


def test_division_with_a_third_and_a_half_is_six():
    # blocks of a half and of a third in one axis: lcm(2, 3) = 6 sub-cells (1.667 m)
    cells = ore_model((6, 6, 6), seed=3, parts=3)
    c, d, _ = project_of(cells, (6, 6, 6))
    res = infer(c, d)
    assert res.grid.sub.div == (6, 6, 6) and res.valid is None


def test_a_power_of_two_division_is_still_a_power_of_two():
    res, *_ = check_model((4, 8, 2), seed=4)
    assert res.grid.sub.levels == (2, 3, 1)


def test_the_hinted_parent_gives_the_same_model():
    check_model((5, 5, 3), hint=GridHint(size=PARENT, corner=ORIGIN), seed=5)


def test_the_hinted_division_is_checked_not_searched():
    cells = ore_model((5, 5, 3), seed=6)
    c, d, _ = project_of(cells, (5, 5, 3))
    res = infer(c, d, GridHint(size=PARENT, corner=ORIGIN, div=(5, 5, 3)))
    assert res.grid.sub.div == (5, 5, 3)
    with pytest.raises(ApiError) as e:  # 4 is no division of 2-m blocks: they are refused with their rows
        infer(c, d, GridHint(size=PARENT, corner=ORIGIN, div=(4, 5, 3)))
    assert e.value.code == "blocks_unaligned" and e.value.extra["count"] > 0


def test_a_number_written_with_two_decimals_lies_on_the_lattice_within_its_quantum():
    # 10/3 = 3.33 in the file: not on the lattice at 1e-6, on it at the half of the last place
    res, c, d, cells = check_model((5, 5, 3), seed=7, round_to=2, tol=0.0075)
    with pytest.raises(ApiError) as e:
        infer(c, d)  # the same numbers as exact: refused
    assert e.value.code == "blocks_unaligned"
    assert res.inferred.tol == pytest.approx((0.0075, 0.0075, 0.0075))


def test_blocks_moved_to_the_lattice_are_counted_for_the_warning():
    cells = ore_model((5, 5, 3), seed=8)
    c, d, _ = project_of(cells, (5, 5, 3))
    c, d = np.round(c, 2), np.round(d, 2)
    scan = GridScan(GridHint(), quanta=(0.0075, 0.0075, 0.0075))
    scan.add(c, d)
    found = scan.finish()
    lost = Unaligned()
    lost.add(0, found.rows_of(c, d))
    assert lost.count == 0 and lost.shifted > 0 and 0 < lost.max_shift <= 0.0075


def test_the_wizard_tolerance_is_at_most_a_quarter_of_a_sub_cell():
    cells = ore_model((5, 5, 3), seed=9)
    c, d, _ = project_of(cells, (5, 5, 3))
    res = infer(c, d, GridHint(size=PARENT, corner=ORIGIN, tol=0.4))  # 2 m sub-cells (¼ = 0.5), 3.33 m (¼ = 0.83)
    assert res.grid.sub.div == (5, 5, 3)
    with pytest.raises(ApiError) as e:
        infer(c, d, GridHint(size=PARENT, corner=ORIGIN, tol=1.0))
    assert e.value.code == "bad_param" and e.value.extra["param"] == "grid.tol"
    with pytest.raises(ApiError) as e:
        infer(c, d, GridHint(tol=0.0))
    assert e.value.code == "bad_param" and e.value.extra["param"] == "grid.tol"


def test_a_turned_model_with_a_division_of_three():
    check_model((5, 5, 3), seed=10, phi=30.0, tol=1e-6)


def test_the_parent_twice_the_biggest_block_is_taken_with_a_warning():
    # parent 20 along X divided into halves (10) and thirds (6.667): no whole 20 m block, the biggest is 10 m and the
    # thirds cross the border of a 10 m parent
    rng = np.random.default_rng(11)
    rows = []
    for i in range(6):
        for j in range(4):
            cut = [(0, 10), (10, 20)] if (i + j) % 2 == 0 else [(0, 20 / 3), (20 / 3, 40 / 3), (40 / 3, 20)]
            for lo, hi in cut:
                rows.append((ORIGIN[0] + 20 * i + (lo + hi) / 2, ORIGIN[1] + 10 * j + 5, ORIGIN[2] + 5, hi - lo))
    a = np.array(rows)
    c, d = a[:, :3], np.column_stack([a[:, 3], np.full(len(a), 10.0), np.full(len(a), 10.0)])
    res = infer(c, d)
    assert res.grid.size[0] == 20.0 and res.grid.sub.div[0] == 6 and res.grid.sub.div[1:] == (1, 1)
    assert any("по X взят 20" in w and "2 ×" in w for w in res.warnings)
    assert rng is not None


def test_a_model_off_every_lattice_is_refused_as_before():
    cells = ore_model((5, 5, 3), seed=12, counts=(10, 10, 8))
    c, d, _ = project_of(cells, (5, 5, 3))
    d = d.copy()
    d[::7, 2] *= 0.37  # heights no fraction of 10 m up to 128 sub-cells: the sizes are random
    with pytest.raises(ApiError) as e:
        infer(c, d)
    assert e.value.code == "blocks_unaligned" and e.value.extra["count"] > 0


def test_the_grid_of_a_model_with_a_division_is_a_grid_of_meta_v2():
    res, *_ = check_model((5, 5, 3), seed=13, counts=(12, 12, 8))
    again = GridV2.of(res.grid.to_meta())
    assert again.sub.div == (5, 5, 3) and again == res.grid
