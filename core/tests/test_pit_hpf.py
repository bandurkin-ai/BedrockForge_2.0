"""The HPF solver of the maximum closure against the references (подшаг 21.5; ТЗ 8.11.3, 8.11.6; Т8.11-1, Т8.11-2,
Т8.11-3; ADR 0028 decisions 14–16): Э-ПИТ-1 and 200 random 4×6 models against the enumeration of profiles, 3D models of
up to 20 thousand blocks against OR-Tools and scipy (value and the SET of blocks), portions of work, cancellation,
explicit arcs, big values, repeats."""

from __future__ import annotations

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.mine import flow_ref as F
from bf_core.mine import graph as G
from bf_core.mine import hpf as H
from tests.ref import pit_brute as B
from tests.test_pit_flow_ref import EPIT1, graph_of_rows, mask_rows, random_models

T9 = np.array([[0, 0, 1], [1, 0, 1], [-1, 0, 1], [0, 1, 1], [0, -1, 1], [1, 1, 1], [-1, -1, 1], [1, -1, 1],
                [-1, 1, 1]], dtype=np.int32)  # fmt: skip
T17 = np.vstack([T9, np.array([[2, 0, 2], [-2, 0, 2], [0, 2, 2], [0, -2, 2], [1, 1, 2], [-1, -1, 2], [1, -1, 2],
                               [-1, 1, 2]], dtype=np.int32)])  # fmt: skip
T3 = np.array([[0, 0, 1], [0, 0, 2], [1, -1, 1]], dtype=np.int32)


def det_stats(st):
    """The counters that do not depend on the size of a portion nor on the clock."""
    return {k: v for k, v in st.items() if k not in ("seconds", "portions")}


def case_3d(seed, dims, p_air=0.1, kind=1, two_tables=False):
    """A random 3D graph: `kind` 0 — values −3…3 (ties, zeros), 1 — −30…25, 2 — rare rich ore, 3 — 10⁹ × (−5…5)."""
    rng = np.random.default_rng(seed)
    total = dims[0] * dims[1] * dims[2]
    if kind == 0:
        value = rng.integers(-3, 4, total)
    elif kind == 1:
        value = rng.integers(-30, 25, total)
    elif kind == 2:
        value = np.where(rng.random(total) < 0.12, rng.integers(30, 120, total), -rng.integers(1, 6, total))
    else:
        value = rng.integers(-5, 6, total) * 10**9
    air = rng.random(total) < p_air
    tpl = rng.integers(0, 2, total).astype(np.uint8) if two_tables else None
    return G.build_graph(dims, value.astype(np.int64), air, tpl, [T17, T3] if two_tables else [T9])


# --- Т8.11-1: Э-ПИТ-1 ---------------------------------------------------------------------------------------------


def test_epit1_hpf_value_and_set_exact():
    g = graph_of_rows(EPIT1)
    mined = H.solve(g)
    assert F.optimum(g.value, mined) == 26
    m = mask_rows(g, mined, 5, 8)
    assert B.depths_of(m).tolist() == [1, 2, 3, 4, 5, 4, 3, 2]
    assert int(m.sum()) == 24 and int(EPIT1[m].sum()) == 26
    assert np.array_equal(m, B.profile_mask(B.brute_profiles(EPIT1).smallest, 5))
    assert G.is_closed(g, mined)
    assert np.array_equal(mined, F.solve(g))


def test_epit1_scaled_by_1e12_and_cones():
    scale = 10**12
    g = graph_of_rows(EPIT1 * scale)
    mined = H.solve(g)
    assert F.optimum(g.value, mined) == 26 * scale
    assert B.depths_of(mask_rows(g, mined, 5, 8)).tolist() == [1, 2, 3, 4, 5, 4, 3, 2]
    # the cones change neither the value nor the set (the same as for OR-Tools)
    g1 = graph_of_rows(EPIT1)
    sub, ids = G.restrict_graph(g1, G.cone_mask(g1))
    back = np.zeros(g1.n, bool)
    back[ids[H.solve(sub)]] = True
    assert np.array_equal(back, H.solve(g1))


# --- Т8.11-2: 200 random models 4×6 -------------------------------------------------------------------------------


def test_random_models_value_and_set_equal_brute_force_profiles():
    ties = 0
    for rows in random_models():
        res = B.brute_profiles(rows)
        g = graph_of_rows(rows)
        mined = H.solve(g)
        assert F.optimum(g.value, mined) == res.best
        # the SMALLEST optimal closure: the intersection of all the optimal pits
        assert np.array_equal(B.depths_of(mask_rows(g, mined, 6, 4)), res.smallest)
        assert G.is_closed(g, mined)
        ties += not res.unique
    assert ties > 0


def test_random_models_equal_ortools_in_every_portion():
    for k, rows in enumerate(random_models()):
        g = graph_of_rows(rows)
        ref = F.solve(g)
        for portion in (1, 7, None) if k % 10 == 0 else (None,):
            assert np.array_equal(H.solve(g, portion=portion), ref)


def test_hand_made_ties_and_trivial_models():
    # a zero block under the gain: the pit with it has the same value, the answer is the smaller one
    g = graph_of_rows([[0, 5, 0], [0, 0, 0]])
    assert int(H.solve(g).sum()) == 1
    # the block above is −4 and the block below +4: a tie, the pit stays empty
    assert not H.solve(graph_of_rows([[0, -4, 0], [0, 4, 0]])).any()
    assert not H.solve(graph_of_rows(-np.ones((3, 3), np.int64))).any()
    assert not H.solve(graph_of_rows(np.zeros((3, 3), np.int64))).any()
    h = graph_of_rows([[3, 0, 0], [0, 7, 0]])  # 7 needs the 0 and the 3 above it
    assert int(H.solve(h).sum()) == 4 and F.optimum(h.value, H.solve(h)) == 10


# --- Т8.11-3: 3D up to 20 thousand blocks — HPF = OR-Tools = scipy ------------------------------------------------

CASES_3D = [
    (1, (9, 8, 7), 0.0, 0, False),
    (2, (12, 11, 10), 0.1, 1, False),
    (3, (14, 13, 12), 0.2, 2, True),
    (4, (16, 15, 14), 0.0, 0, True),
    (5, (20, 18, 16), 0.1, 1, True),
    (6, (27, 27, 27), 0.1, 1, False),  # 19 683 blocks
    (7, (27, 25, 29), 0.3, 2, True),  # 19 575 cells, a third of them air
]


@pytest.mark.parametrize("seed,dims,p_air,kind,two", CASES_3D)
def test_3d_hpf_equals_ortools_and_scipy(seed, dims, p_air, kind, two):
    g = case_3d(seed, dims, p_air, kind, two)
    assert 0 < g.n <= 20_000
    mined = H.solve(g)
    assert np.array_equal(mined, F.solve(g))
    indptr, indices = G.predecessors(g)
    val, mined_sp = B.scipy_closure(g.value, indptr, indices)
    assert F.optimum(g.value, mined) == val and np.array_equal(mined, mined_sp)
    assert G.is_closed(g, mined) and mined.any()


def test_3d_cones_and_air_do_not_change_the_answer():
    g = case_3d(8, (13, 12, 11), 0.15, 2, True)
    sub, ids = G.restrict_graph(g, G.cone_mask(g))
    back = np.zeros(g.n, bool)
    back[ids[H.solve(sub)]] = True
    assert np.array_equal(back, H.solve(g)) and sub.n < g.n


def test_3d_big_values_equal_ortools():
    for seed in (11, 12, 13):
        g = case_3d(seed, (11, 10, 9), 0.1, 3, True)  # v′ up to 5·10⁹ (> 2³¹: no scipy)
        mined = H.solve(g)
        assert np.array_equal(mined, F.solve(g)) and G.is_closed(g, mined)
    scale = 10**12
    for rows in random_models(20):
        g = graph_of_rows(rows * scale)
        assert F.optimum(g.value, H.solve(g)) == B.brute_profiles(rows).best * scale


# --- portions, repeat, cancellation, memory -----------------------------------------------------------------------


def test_portions_one_seven_and_infinity_give_the_same_bits():
    for seed, dims, kind in ((21, (10, 9, 8), 1), (22, (12, 12, 9), 0), (23, (11, 10, 10), 2)):
        g = case_3d(seed, dims, 0.1, kind, True)
        ref_stats: dict = {}
        ref = H.solve(g, portion=None, stats=ref_stats)
        assert ref_stats["portions"] == 1
        for portion in (1, 7, 1000):
            st: dict = {}
            got = H.solve(g, portion=portion, stats=st)
            assert got.tobytes() == ref.tobytes()
            assert det_stats(st) == det_stats(ref_stats)
        assert st["portions"] >= 1


def test_repeat_is_the_same_bit_for_bit():
    g = case_3d(31, (14, 13, 12), 0.1, 1, True)
    s1: dict = {}
    s2: dict = {}
    a = H.solve(g, stats=s1)
    b = H.solve(g, stats=s2)
    assert a.tobytes() == b.tobytes() and det_stats(s1) == det_stats(s2)
    assert s1["mergers"] > 0 and s1["relabels"] > 0 and s1["arcs"] > 0 and s1["nodes"] == g.n


def test_tick_is_called_between_portions_and_may_cancel():
    g = case_3d(41, (14, 13, 12), 0.1, 1, True)
    fractions: list[float] = []
    mined = H.solve(g, portion=50, tick=fractions.append)
    assert np.array_equal(mined, F.solve(g))
    assert len(fractions) > 3 and fractions[-1] == 1.0
    assert all(0.0 <= f <= 1.0 for f in fractions) and all(f < 1.0 for f in fractions[:-1])

    class Stop(Exception):
        pass

    calls: list[float] = []

    def tick(f: float) -> None:
        calls.append(f)
        if len(calls) == 3:
            raise Stop

    with pytest.raises(Stop):
        H.solve(g, portion=1, tick=tick)
    assert len(calls) == 3  # nothing runs after the cancellation


def test_memory_control_is_a_russian_error_with_a_hint():
    g = case_3d(51, (9, 8, 7), 0.0, 1, False)
    with pytest.raises(ApiError) as e:
        H.solve(g, max_bytes=100)
    assert e.value.status == 422 and e.value.code == "pit_too_large"
    assert "памяти" in e.value.message and e.value.hint
    assert H.estimate_bytes(1_000_000) < 100 * 2**20 and H.estimate_bytes(2000) > H.estimate_bytes(1000)
    assert H.solve(g, max_bytes=10**9).any()


def test_debug_mode_checks_the_state(monkeypatch):
    monkeypatch.setenv("BF_PIT_DEBUG", "1")
    for rows in random_models(30):
        H.solve(graph_of_rows(rows))
    for seed, dims, kind in ((61, (12, 11, 10), 1), (62, (10, 10, 10), 0)):
        g = case_3d(seed, dims, 0.1, kind, True)
        assert np.array_equal(H.solve(g, portion=13), F.solve(g))
    v = np.array([4, -1, -2, 10, -3], dtype=np.int64)
    assert H.solve_arcs(v, np.array([0, 1, 2, 3, 3, 3], dtype=np.int64), np.array([1, 2, 3], dtype=np.int32)).any()


# --- explicit arcs (MineLib) --------------------------------------------------------------------------------------


def random_digraph(rng, n, m):
    tails = np.sort(rng.integers(0, n, m))
    heads = rng.integers(0, n, m).astype(np.int32)
    indptr = np.zeros(n + 1, dtype=np.int64)
    np.add.at(indptr, tails + 1, 1)
    return np.cumsum(indptr), heads


def test_explicit_arcs_equal_ortools_on_random_digraphs():
    rng = np.random.default_rng(3)
    for _ in range(300):
        n = int(rng.integers(1, 90))
        value = rng.integers(-8, 9, n).astype(np.int64)
        indptr, indices = random_digraph(rng, n, int(rng.integers(0, 4 * n)))  # cycles and repeats are allowed
        ref = F.solve_arcs(value, indptr, indices)
        assert np.array_equal(H.solve_arcs(value, indptr, indices), ref)
        assert np.array_equal(H.solve_arcs(value, indptr, indices, portion=1), ref)


def test_explicit_arcs_of_a_grid_graph_equal_the_template_mode():
    g = case_3d(71, (13, 12, 11), 0.1, 2, True)
    indptr, indices = G.predecessors(g)
    a = H.solve(g)
    assert np.array_equal(H.solve_arcs(g.value, indptr, indices), a)
    assert np.array_equal(H.solve_arcs(g.value * 10**9, indptr, indices), a)
    assert F.optimum(g.value, a) == F.optimum(g.value, F.solve_arcs(g.value, indptr, indices))


def test_explicit_arcs_chain_and_bad_input():
    # a chain 0 → 1 → 2 → 3 and a lone node (as in the test of OR-Tools)
    value = np.array([4, -1, -2, 10, -3], dtype=np.int64)
    indptr = np.array([0, 1, 2, 3, 3, 3], dtype=np.int64)
    indices = np.array([1, 2, 3], dtype=np.int32)
    assert H.solve_arcs(value, indptr, indices).tolist() == [True, True, True, True, False]
    with pytest.raises(ValueError):
        H.solve_arcs(value, indptr[:-1], indices)
    with pytest.raises(ValueError):
        H.solve_arcs(value, indptr, np.array([1, 2, 9], dtype=np.int32))
    with pytest.raises(ValueError):
        H.solve_arcs(np.array([1.5, -1.0]), np.zeros(3, np.int64), np.zeros(0, np.int32))


# --- degenerate inputs --------------------------------------------------------------------------------------------


def test_degenerate_inputs():
    empty = G.build_graph((2, 2, 2), np.zeros(8, np.int64), np.ones(8, dtype=bool), None, [T9])
    assert H.solve(empty).shape == (0,)
    only_cost = G.build_graph((3, 3, 3), -np.ones(27, np.int64), np.zeros(27, dtype=bool), None, [T9])
    assert not H.solve(only_cost).any()
    one = G.build_graph((1, 1, 1), np.array([5], np.int64), np.zeros(1, dtype=bool), None, [T9])
    assert H.solve(one).tolist() == [True]
    pos = G.build_graph((4, 4, 4), np.arange(1, 65, dtype=np.int64), np.zeros(64, dtype=bool), None, [T9])
    assert H.solve(pos).all()  # nothing to pay: the whole model
    huge = G.build_graph((2, 1, 1), np.array([2**61, 2**61], np.int64), np.zeros(2, dtype=bool), None, [T9])
    with pytest.raises(ValueError):
        H.solve(huge)  # Σ|v′| ≥ 2⁶²: the excess could overflow


# --- the labels reach n: a chain through every node (review 21.21б, finding 2) -------------------------------------


def csr_of(n, tails, heads):
    o = np.argsort(tails, kind="stable")
    indptr = np.zeros(n + 1, np.int64)
    np.add.at(indptr, np.asarray(tails)[o] + 1, 1)
    return np.cumsum(indptr), np.asarray(heads, dtype=np.int32)[o]


def test_chain_through_every_node_reaches_label_n():
    """v = [3, −1], the arc 0 → 1: the sink is at the distance n = 2 from the node 0 (the label of a node of a network
    of n nodes and the sink can be n); the bound n − 1 left the excess in the tree and the certificate failed."""
    value = np.array([3, -1], dtype=np.int64)
    indptr, indices = csr_of(2, [0], [1])
    for portion in (None, 1):
        assert H.solve_arcs(value, indptr, indices, portion=portion).tolist() == [True, True]
    g = G.build_graph((1, 1, 2), value, np.zeros(2, bool), None, [np.array([[0, 0, 1]], np.int32)])
    assert H.solve(g).tolist() == [True, True] and np.array_equal(H.solve(g), F.solve(g))
    for n in (3, 5, 8, 17):  # a chain 0 → 1 → … → n − 1: the supply at the head, a cost at every other node
        value = np.array([n] + [-1] * (n - 1), dtype=np.int64)
        indptr, indices = csr_of(n, list(range(n - 1)), list(range(1, n)))
        got = H.solve_arcs(value, indptr, indices)
        assert got.all() and np.array_equal(got, F.solve_arcs(value, indptr, indices))
        gz = G.build_graph((1, 1, n), value, np.zeros(n, bool), None, [np.array([[0, 0, 1]], np.int32)])
        assert H.solve(gz).all()
        gp = G.build_graph((1, 1, n), np.array([n - 2] + [-1] * (n - 1), np.int64), np.zeros(n, bool), None,
                           [np.array([[0, 0, 1]], np.int32)])  # fmt: skip
        assert np.array_equal(H.solve(gp), F.solve(gp)) and not H.solve(gp).any()  # the cost n − 1 is more


def test_random_small_graphs_with_cycles_and_repeats_equal_ortools():
    """3000 random graphs of 1…11 nodes (arcs at random, a part of them is a chain through every node; cycles and
    repeated arcs) — HPF in one portion and in portions of one operation equals OR-Tools, set for set."""
    rng = np.random.default_rng(2113)
    for _ in range(3000):
        n = int(rng.integers(1, 12))
        value = rng.integers(-6, 7, n).astype(np.int64)
        if rng.random() < 0.3:
            order = rng.permutation(n)
            extra = int(rng.integers(0, n + 1))
            tails = np.append(order[:-1], rng.integers(0, n, extra))
            heads = np.append(order[1:], rng.integers(0, n, extra))
        else:
            m = int(rng.integers(0, 3 * n + 1))
            tails, heads = rng.integers(0, n, m), rng.integers(0, n, m)
        if rng.random() < 0.5:  # repeated arcs
            tails, heads = np.append(tails, tails[:3]), np.append(heads, heads[:3])
        indptr, indices = csr_of(n, tails, heads)
        ref = F.solve_arcs(value, indptr, indices)
        for portion in (None, 1):
            assert np.array_equal(H.solve_arcs(value, indptr, indices, portion=portion), ref), (n, value.tolist())
