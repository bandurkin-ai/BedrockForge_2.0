"""The OR-Tools reference solver of the maximum closure against the enumeration of profiles and scipy (подшаг 21.4;
ТЗ 8.11.3, 8.11.6; Т8.11-1, Т8.11-2; ADR 0028 decision 14, findings 4, 5)."""

from __future__ import annotations

import numpy as np
import pytest

from bf_core.mine import flow_ref as F
from bf_core.mine import graph as G
from tests.ref import pit_brute as B

T2D = np.array([[-1, 0, 1], [0, 0, 1], [1, 0, 1]], dtype=np.int32)

EPIT1 = np.array(
    [
        [-1, -1, -1, -1, -1, -1, -1, -1],
        [-2, -2, -2, 3, -2, -2, -2, -2],
        [-3, -3, 4, 6, 5, -3, -3, -3],
        [-4, -4, -4, 10, 8, -4, -4, -4],
        [-5, -5, -5, -5, 20, -5, -5, -5],
    ],
    dtype=np.int64,
)


def graph_of_rows(rows):
    """A 2D model (rows from the top) as a grid graph (C, 1, R), k = 0 at the bottom."""
    r = np.asarray(rows, dtype=np.int64)
    nrows, ncols = r.shape
    flat = np.ascontiguousarray(r[::-1]).ravel()
    return G.build_graph((ncols, 1, nrows), flat, np.zeros(flat.size, dtype=bool), None, [T2D])


def mask_rows(g, mined, nrows, ncols):
    """The mined mask of a graph of `graph_of_rows` as (nrows, ncols), rows from the top."""
    full = G.expand_mask(g, mined)
    return full.reshape(nrows, ncols)[::-1]


# --- Т8.11-1: Э-ПИТ-1 -------------------------------------------------------------------------------------------------


def test_epit1_brute_force_enumeration():
    res = B.brute_profiles(EPIT1)
    assert res.n_feasible == 7442
    assert res.best == 26 and res.unique
    assert res.smallest.tolist() == [1, 2, 3, 4, 5, 4, 3, 2]


def test_epit1_ortools_value_and_set_exact():
    g = graph_of_rows(EPIT1)
    mined = F.solve(g)
    assert F.optimum(g.value, mined) == 26
    m = mask_rows(g, mined, 5, 8)
    assert B.depths_of(m).tolist() == [1, 2, 3, 4, 5, 4, 3, 2]
    assert int(m.sum()) == 24 and int(EPIT1[m].sum()) == 26
    assert np.array_equal(m, B.profile_mask(B.brute_profiles(EPIT1).smallest, 5))
    assert G.is_closed(g, mined)


def test_epit1_scipy_agrees_and_cones_do_not_change_the_optimum():
    g = graph_of_rows(EPIT1)
    indptr, indices = G.predecessors(g)
    val, mined_sp = B.scipy_closure(g.value, indptr, indices)
    assert val == 26 and np.array_equal(mined_sp, F.solve(g))
    cone = G.cone_mask(g)
    assert 0 < cone.sum() < g.n  # the cones cut something (the left and right top corners are in none)
    sub, ids = G.restrict_graph(g, cone)
    sub_mined = F.solve(sub)
    full = np.zeros(g.n, bool)
    full[ids[sub_mined]] = True
    assert np.array_equal(full, F.solve(g)) and F.optimum(sub.value, sub_mined) == 26


# --- Т8.11-2: 200 random models 4×6 -----------------------------------------------------------------------------------


def random_models(count=200):
    rng = np.random.Generator(np.random.PCG64(7))
    return [rng.integers(-6, 7, size=(6, 4)).astype(np.int64) for _ in range(count)]  # 6 rows from the top, 4 columns


def test_random_models_value_equals_brute_force_profiles():
    models = random_models()
    seen_ties = 0
    for rows in models:
        res = B.brute_profiles(rows)
        g = graph_of_rows(rows)
        mined = F.solve(g)
        assert F.optimum(g.value, mined) == res.best
        m = mask_rows(g, mined, 6, 4)
        # the SMALLEST optimal closure (the intersection of all the optimal pits) at equal optima
        assert np.array_equal(B.depths_of(m), res.smallest)
        assert G.is_closed(g, mined)
        seen_ties += not res.unique
    assert seen_ties > 0  # the seeds really have ties (zeros, equal pits)


def test_random_models_cones_do_not_change_the_optimum_nor_the_set():
    for rows in random_models():
        g = graph_of_rows(rows)
        full = F.solve(g)
        sub, ids = G.restrict_graph(g, G.cone_mask(g))
        back = np.zeros(g.n, bool)
        back[ids[F.solve(sub)]] = True
        assert np.array_equal(back, full)


def test_random_models_scipy_agrees():
    for rows in random_models(60):
        g = graph_of_rows(rows)
        indptr, indices = G.predecessors(g)
        val, mined = B.scipy_closure(g.value, indptr, indices)
        assert val == B.brute_profiles(rows).best and np.array_equal(mined, F.solve(g))


def test_smallest_closure_at_equal_optima():
    # a zero block under the gain: the pit with it has the same value, the answer is the smaller one
    g = graph_of_rows([[0, 5, 0], [0, 0, 0]])
    mined = F.solve(g)
    assert int(mined.sum()) == 1 and F.optimum(g.value, mined) == 5
    # two ways of equal value: the mined block 4 needs the block above (−4); with 4 − 4 = 0 the pit stays empty
    h = graph_of_rows([[0, -4, 0], [0, 4, 0]])
    assert not F.solve(h).any()
    # nothing positive, nothing to mine
    assert not F.solve(graph_of_rows(-np.ones((3, 3), np.int64))).any()


def test_no_negative_value_at_all_the_sink_still_exists():
    """No arc b → t: the sink must still be a node of the network (a regression of the first draft: empty pit)."""
    g = graph_of_rows([[0, 5, 0], [0, 0, 0]])
    assert F.solve(g).tolist() == [False] * 4 + [True, False]
    h = graph_of_rows([[3, 0, 0], [0, 7, 0]])  # 7 needs the 0 and the 3 above it (the 0 is a predecessor, not value)
    assert int(F.solve(h).sum()) == 4 and F.optimum(h.value, F.solve(h)) == 10


def test_zero_values_never_enter_the_pit_alone():
    assert not F.solve(graph_of_rows(np.zeros((3, 3), np.int64))).any()


# --- values above 2³¹, explicit arcs, portions -----------------------------------------------------------------------


def test_large_integers_ortools_exact_and_scipy_refuses():
    scale = 10**12  # v′ up to 6·10¹² > 2³¹: scipy would truncate the capacities to int32 (ADR 0028 finding 5)
    for rows in random_models(25):
        g = graph_of_rows(rows * scale)
        mined = F.solve(g)
        assert F.optimum(g.value, mined) == B.brute_profiles(rows).best * scale
    g = graph_of_rows(EPIT1 * scale)
    indptr, indices = G.predecessors(g)
    with pytest.raises(ValueError, match="int32"):
        B.scipy_closure(g.value, indptr, indices)
    assert B.scipy_closure(*(lambda gg: (gg.value, *G.predecessors(gg)))(graph_of_rows(EPIT1)))[0] == 26


def test_value_range_near_the_int32_limit_in_scipy():
    # Σ(v′ > 0) + 1 = 2³¹ − 1 is the largest «infinity» scipy keeps whole
    v = np.array([2**31 - 2, -5, 0], dtype=np.int64)
    indptr = np.array([0, 1, 1, 1], dtype=np.int64)
    indices = np.array([1], dtype=np.int32)
    val, mined = B.scipy_closure(v, indptr, indices)
    assert val == 2**31 - 2 - 5 and mined.tolist() == [True, True, False]
    with pytest.raises(ValueError):
        B.scipy_closure(np.array([2**31 - 1, -5, 0], dtype=np.int64), indptr, indices)


def test_explicit_arcs_mode_and_portions():
    # a chain 0 → 1 → 2 → 3 (a predecessor must be mined first) and a lone node
    value = np.array([4, -1, -2, 10, -3], dtype=np.int64)
    indptr = np.array([0, 1, 2, 3, 3, 3], dtype=np.int64)
    indices = np.array([1, 2, 3], dtype=np.int32)
    mined = F.solve_arcs(value, indptr, indices)
    assert mined.tolist() == [True, True, True, True, False] and F.optimum(value, mined) == 11
    for chunk in (1, 2, 3, 100):
        assert np.array_equal(F.solve_arcs(value, indptr, indices, chunk=chunk), mined)
    # the grid graph in portions of 7 arcs = in one portion
    rows = random_models(1)[0]
    g = graph_of_rows(rows)
    assert np.array_equal(F.solve(g, chunk=7), F.solve(g))
    with pytest.raises(ValueError):
        F.solve_arcs(value, indptr[:-1], indices)


def test_infinity_and_degenerate_inputs():
    assert F.infinity(np.array([3, -4, 5, 0], dtype=np.int64)) == 9
    assert F.solve_arcs(np.zeros(0, np.int64), np.zeros(1, np.int64), np.zeros(0, np.int32)).size == 0
    only_cost = F.solve_arcs(np.array([-1, -2], np.int64), np.zeros(3, np.int64), np.zeros(0, np.int32))
    assert not only_cost.any()
    with pytest.raises(ValueError):
        F.solve_arcs(np.array([1.5, -1.0]), np.zeros(3, np.int64), np.zeros(0, np.int32))


def test_3d_ortools_scipy_and_closure_agree():
    rng = np.random.default_rng(11)
    dims = (8, 7, 6)
    total = 8 * 7 * 6
    tab = np.array([[0, 0, 1], [1, 0, 1], [-1, 0, 1], [0, 1, 1], [0, -1, 1], [1, 1, 1], [-1, -1, 1], [1, -1, 1],
                    [-1, 1, 1]], dtype=np.int32)  # fmt: skip
    value = rng.integers(-30, 25, total).astype(np.int64)
    g = G.build_graph(dims, value, rng.random(total) < 0.1, None, [tab])
    indptr, indices = G.predecessors(g)
    mined = F.solve(g)
    val, mined_sp = B.scipy_closure(g.value, indptr, indices)
    assert F.optimum(g.value, mined) == val and np.array_equal(mined, mined_sp) and G.is_closed(g, mined)
    assert mined.any()


@pytest.mark.skip(
    reason="the Lerchs–Grossmann 1965 example is not reproduced from memory (вопрос № 115): a source fixture"
)
def test_lerchs_grossmann_1965_example():
    raise AssertionError("the example is added from the primary source")  # pragma: no cover
