"""The lattice of the sub-cells of a parent (ADR 0030 decisions 1–4, подшаг 27.4а): the denominators of the fractions of
a parent, the common division `div` = lcm of them (any whole 1…128, not only 2^L), the candidates of the parent
(k·max) and of the corner, the tolerance of a column by its type.

The reference is arithmetic written by hand (10/5 = 2, 10/3 = 3.333…, lcm(3, 4) = 12), not the code under test."""

from __future__ import annotations

import numpy as np
import pytest

from bf_core.blocks import grid_lattice as gl
from bf_core.blocks.schema import SubblockSpec


def pairs(rows: list[tuple[float, float, int]]) -> gl.PairSet:
    """A PairSet of (low edge, size, count) triples, fed as blocks."""
    ps = gl.PairSet()
    lo = np.concatenate([np.full(n, a) for a, _, n in rows])
    sz = np.concatenate([np.full(n, b) for _, b, n in rows])
    ps.add(lo, sz)
    return ps


def test_fractions_find_the_smallest_denominator():
    v = np.array([0.0, 5.0, 10.0, 10 / 3, 20 / 3, 2.0, 4.0, 2.5, 7.0])
    q = gl.denominators(v, 10.0, 1e-6)
    assert q.tolist() == [1, 2, 1, 3, 3, 5, 5, 4, 10]


def test_fractions_beyond_128_have_no_denominator():
    q = gl.denominators(np.array([1 / 7, 1 / 19, 0.5]), 1.0, 1e-9)
    assert q.tolist() == [7, 19, 2]
    q = gl.denominators(np.array([1 / 131, 0.123456]), 1.0, 1e-9)
    assert q.tolist() == [0, 0]  # 131 > 128; no small denominator


def test_lcm_of_denominators():
    assert gl.common_div([2, 5]) == 10
    assert gl.common_div([3, 4]) == 12
    assert gl.common_div([1, 1]) == 1
    assert gl.common_div([2, 4, 8]) == 8  # a power of two stays a power of two
    assert gl.common_div([7, 19]) is None  # 133 > 128
    assert gl.common_div([]) == 1


def test_a_value_off_every_fraction_is_not_on_the_lattice():
    div, rest = gl.lattice_of(np.array([3.3333333]), np.array([1]), 10.0, 1e-6)
    assert div == 3 and rest.tolist() == [True]
    div, rest = gl.lattice_of(np.array([0.5, 0.123456]), np.array([5, 1]), 1.0, 1e-9)
    assert div == 2 and rest.tolist() == [True, False]  # the frequent value keeps the lattice, the odd one is off


def test_tolerance_follows_the_type_of_the_column():
    assert gl.column_tol("R") == 1e-6 and gl.column_tol("csv") == 1e-6
    assert gl.column_tol("N", decimals=2) == pytest.approx(0.005)
    assert gl.column_tol("N", decimals=0) == pytest.approx(0.5)
    assert gl.column_tol("N", decimals=9) == 1e-6  # finer than the floor
    # f4 at 7 million: the spacing of the float32 there is 0.5 m, half of it is the quantum
    assert gl.column_tol("F", max_abs=7_000_000.0) == pytest.approx(0.25)
    assert gl.column_tol("F", max_abs=100.0) == pytest.approx(np.spacing(np.float32(100.0)) / 2)


def test_a_number_with_two_decimals_is_a_third():
    assert gl.denominators(np.array([3.33, 6.67]), 10.0, 0.005).tolist() == [3, 3]
    assert gl.denominators(np.array([3.33, 6.67]), 10.0, 1e-6).tolist() != [3, 3]  # exact data: 3.33 is no third


def test_the_tolerance_never_exceeds_a_quarter_of_the_sub_cell():
    # a 5 m quantum on a 10 m parent: 3.0 is 3.0 off the line 0 — over a quarter of the 10 m sub-cell (2.5), so it is
    # not the corner of the parent; it is the third (3.33), 0.33 off, within a quarter of 3.33 (0.83)
    assert gl.denominators(np.array([3.0]), 10.0, 5.0).tolist() == [3]
    assert gl.denominators(np.array([2.0]), 10.0, 5.0).tolist() == [1]  # 2.0 ≤ 2.5 from the line 0: the same edge


def test_pair_set_counts_and_grows():
    ps = gl.PairSet()
    lo = np.tile(np.arange(5000, dtype=np.float64), 3)
    sz = np.tile(np.array([1.0, 2.0, 3.0, 1.0, 2.0]), 3000)[: len(lo)]
    ps.add(lo, sz)
    got_lo, got_sz, got_n = ps.items()
    assert len(got_lo) == len(set(zip(lo.round(6).tolist(), sz.tolist(), strict=True)))
    assert int(got_n.sum()) == len(lo)
    assert not ps.overflow


def test_pair_set_overflow_is_a_flag_not_an_error():
    ps = gl.PairSet(max_pairs=64)
    ps.add(np.arange(1000, dtype=np.float64), np.ones(1000))
    assert ps.overflow
    lo, _, n = ps.items()
    assert len(lo) <= 64 and int(n.sum()) <= 1000


def test_axis_fit_the_parent_of_the_whole_blocks():
    # parent 10, sub-cells of 2 (div 5): blocks of 10 and of 2, 4, 6 inside
    ps = pairs([(0.0, 10.0, 7), (10.0, 4.0, 3), (14.0, 6.0, 2), (20.0, 2.0, 5)])
    fit = gl.fit_axis(*ps.items(), parent=10.0, origin=0.0, tol=1e-6)
    assert fit.div == 5 and fit.aligned == fit.total == 17 and fit.crosses == fit.before == 0


def test_axis_fit_with_a_third_and_a_half_gives_six():
    ps = pairs([(0.0, 10.0, 4), (10.0, 10 / 3, 4), (10 + 10 / 3, 20 / 3, 4), (20.0, 5.0, 4)])
    fit = gl.fit_axis(*ps.items(), parent=10.0, origin=0.0, tol=1e-6)
    assert fit.div == 6 and fit.aligned == fit.total


def test_axis_fit_counts_the_blocks_across_a_border_and_before_the_corner():
    ps = pairs([(0.0, 10.0, 5), (6.0, 6.0, 2), (-4.0, 2.0, 3)])  # 6…12 crosses 10; -4 is before the corner
    fit = gl.fit_axis(*ps.items(), parent=10.0, origin=0.0, tol=1e-6)
    assert fit.crosses == 2 and fit.before == 3 and fit.aligned == 5 and fit.total == 10


def test_axis_fit_keeps_the_frequent_fractions_when_the_lattice_is_too_fine():
    ps = pairs([(0.0, 10.0, 90), (0.0, 5.0, 8), (5.0, 1 / 131 * 10, 2)])
    fit = gl.fit_axis(*ps.items(), parent=10.0, origin=0.0, tol=1e-9)
    assert fit.div == 2 and fit.aligned == 98 and fit.off == 2


def test_choose_axis_takes_the_biggest_block_when_it_fits():
    ps = pairs([(0.0, 10.0, 5), (10.0, 5.0, 4), (15.0, 5.0, 4)])
    pick = gl.choose_axis(*ps.items(), biggest=10.0, tol=1e-6, hint_parent=None, hint_origin=None)
    assert pick.parent == 10.0 and pick.origin == 0.0 and pick.div == 2 and pick.complete and pick.k == 1


def test_choose_axis_takes_twice_the_biggest_block_when_thirds_cross_its_border():
    # parent 20 divided by 3 (6.667) and by 2 (10); the biggest block is 10, but 6.667…13.333 crosses 10
    third = 20 / 3
    ps = pairs([(0.0, 20.0, 3), (20.0, 10.0, 3), (30.0, 10.0, 3), (40.0, third, 3), (40 + third, third, 3),
                (40 + 2 * third, third, 3)])  # fmt: skip
    pick = gl.choose_axis(*ps.items(), biggest=20.0, tol=1e-6, hint_parent=None, hint_origin=None)
    assert pick.k == 1 and pick.parent == 20.0 and pick.div == 6
    # the biggest block is 10 here: parents of 20 — the second candidate
    ps = pairs([(0.0, 10.0, 3), (10.0, 10.0, 3), (20.0, third, 3), (20 + third, third, 3),
                (20 + 2 * third, third, 3), (40.0, 10.0, 3), (50.0, 10.0, 3)])  # fmt: skip
    pick = gl.choose_axis(*ps.items(), biggest=10.0, tol=1e-6, hint_parent=None, hint_origin=None)
    assert pick.complete and pick.parent == 20.0 and pick.k == 2 and pick.div == 6 and pick.origin == 0.0


def test_choose_axis_origin_by_the_mode_of_the_residues():
    # the model starts with a sub-block in the middle of its parent: whole parents sit at 3, 13, 23 (parent 10)
    ps = pairs([(3.0, 10.0, 4), (13.0, 10.0, 4), (23.0, 10.0, 4), (0.0, 3.0, 2)])
    pick = gl.choose_axis(*ps.items(), biggest=10.0, tol=1e-6, hint_parent=None, hint_origin=None)
    assert pick.complete and pick.parent == 10.0 and pick.origin == pytest.approx(-7.0) and pick.div == 10


def test_a_hinted_parent_is_the_only_candidate():
    ps = pairs([(0.0, 5.0, 4), (5.0, 5.0, 4)])
    pick = gl.choose_axis(*ps.items(), biggest=5.0, tol=1e-6, hint_parent=10.0, hint_origin=None)
    assert pick.parent == 10.0 and pick.div == 2 and pick.k == 1


def test_a_given_div_is_checked_not_searched():
    ps = pairs([(0.0, 10.0, 3), (10.0, 2.0, 3)])
    fit = gl.fit_axis(*ps.items(), parent=10.0, origin=0.0, tol=1e-6, div=5)
    assert fit.div == 5 and fit.aligned == fit.total
    fit = gl.fit_axis(*ps.items(), parent=10.0, origin=0.0, tol=1e-6, div=4)  # 2 m is not a whole of 2.5 m
    assert fit.aligned == 3 and fit.off == 3


def test_the_subblock_spec_takes_any_division_up_to_128():
    assert SubblockSpec.of([5, 5, 3]).div == (5, 5, 3)
    assert SubblockSpec.of([5, 5, 3]).levels is None
    assert SubblockSpec.of([4, 8, 1]).levels == (2, 3, 0)
    assert SubblockSpec.of([128, 1, 100]).sub
    for bad in ([129, 1, 1], [0, 1, 1], [1, 2.5, 1], [1, 1, -2], [True, 1, 1]):
        with pytest.raises(Exception) as err:
            SubblockSpec.of(bad)
        assert getattr(err.value, "code", "") == "subblock_div", bad
