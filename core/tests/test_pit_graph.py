"""The closure graph of the pit optimiser (подшаг 21.4; ADR 0028 decision 13; ТЗ 8.11.3).

The reference is a naive Python loop over cells with the convention «an offset (Δi, Δj, m) points to the predecessor
(i + Δi, j + Δj, k + m): levels grow UP»; the tests use hand-made tables (the template of 21.3 has the same
convention)."""

from __future__ import annotations

import numba
import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.mine import graph as G

T2D = np.array([[-1, 0, 1], [0, 0, 1], [1, 0, 1]], dtype=np.int32)  # the block above and its two neighbours above
T3D_A = np.array([[0, 0, 1], [1, 0, 1], [-1, 0, 1], [0, 1, 1], [0, -1, 1], [1, 1, 2], [-2, 0, 2]], dtype=np.int32)
T3D_B = np.array([[0, 0, 1], [0, 0, 2], [1, -1, 1]], dtype=np.int32)


def naive_arcs(dims, air, tpl, tables):
    """{P: [predecessor P in the order of the table]} for the non-air cells (nodes numbered by P)."""
    nx, ny, nz = dims
    arcs = {}
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                p = i + nx * (j + ny * k)
                if air[p]:
                    continue
                lst = []
                for di, dj, m in tables[0 if tpl is None else int(tpl[p])]:
                    ii, jj, kk = i + int(di), j + int(dj), k + int(m)
                    if 0 <= ii < nx and 0 <= jj < ny and 0 <= kk < nz:
                        q = ii + nx * (jj + ny * kk)
                        if not air[q]:
                            lst.append(q)
                arcs[p] = lst
    return arcs


def random_case(seed, dims=(7, 5, 6), p_air=0.25):
    rng = np.random.default_rng(seed)
    total = int(np.prod(dims))
    value = rng.integers(-9, 10, total).astype(np.int64)
    air = rng.random(total) < p_air
    tpl = rng.integers(0, 2, total).astype(np.uint8)
    return value, air, tpl, [T3D_A, T3D_B]


def graph_arcs_by_p(g):
    indptr, indices = G.predecessors(g)
    p = G.parent_index(g)
    return {int(p[u]): [int(p[q]) for q in indices[indptr[u] : indptr[u + 1]]] for u in range(g.n)}


# --- structure -------------------------------------------------------------------------------------------------------


def rows_to_grid(rows):
    """2D rows from the top → (dims, value) of a grid (C, 1, R) with k = 0 at the bottom."""
    r = np.asarray(rows, dtype=np.int64)
    nrows, ncols = r.shape
    return (ncols, 1, nrows), np.ascontiguousarray(r[::-1]).ravel()


def test_2d_template_structure():
    rows = np.arange(40).reshape(5, 8) - 20
    dims, value = rows_to_grid(rows)
    g = G.build_graph(dims, value, np.zeros(40, dtype=bool), None, [T2D])
    assert (g.n, g.shape, g.origin) == (40, (8, 1, 5), (0, 0, 0))
    assert g.level_ptr.tolist() == [0, 8, 16, 24, 32, 40]
    indptr, indices = G.predecessors(g)
    assert len(indices) == 4 * (3 * 8 - 2)  # the top row has no predecessors; two edge columns lose one arc per level
    # the node of the column 3 at the bottom level 0 → columns 2, 3, 4 of the level 1, in the order of the table
    assert indices[indptr[3] : indptr[4]].tolist() == [8 + 2, 8 + 3, 8 + 4]
    assert indices[indptr[0] : indptr[1]].tolist() == [8, 9]  # no column −1
    assert indptr[40] - indptr[32] == 0  # the top level
    assert g.value.tolist() == value.tolist()


def test_convention_levels_grow_up():
    """(0, 0, 1) is the block ABOVE: k + 1. A block at the top level has no predecessor."""
    dims = (1, 1, 4)
    up = np.array([[0, 0, 1]], np.int32)
    g = G.build_graph(dims, np.arange(4, dtype=np.int64), np.zeros(4, dtype=bool), None, [up])
    indptr, indices = G.predecessors(g)
    assert [indices[indptr[u] : indptr[u + 1]].tolist() for u in range(4)] == [[1], [2], [3], []]


@pytest.mark.parametrize("seed", range(6))
def test_arcs_equal_naive_3d(seed):
    dims = (7, 5, 6)
    value, air, tpl, tables = random_case(seed, dims)
    g = G.build_graph(dims, value, air, tpl, tables)
    assert graph_arcs_by_p(g) == naive_arcs(dims, air, tpl, tables)
    assert g.n == int((~air).sum())
    assert np.array_equal(G.parent_index(g), np.flatnonzero(~air))
    nodes = np.flatnonzero(~air)
    assert np.array_equal(g.value, value[nodes])
    assert np.array_equal(g.tpl, tpl[nodes])


def test_air_trimmed_box_and_index():
    dims = (6, 5, 4)
    total = 120
    air = np.ones(total, dtype=bool)
    cells = [(1, 1, 1), (3, 2, 1), (4, 3, 2)]
    for i, j, k in cells:
        air[i + 6 * (j + 5 * k)] = False
    value = np.arange(total, dtype=np.int64)
    g = G.build_graph(dims, value, air, None, [np.array([[1, 1, 1]], np.int32)])
    assert g.origin == (1, 1, 1) and g.shape == (4, 3, 2) and g.n == 3
    assert g.index.shape == (24,) and int((g.index >= 0).sum()) == 3
    assert g.index[(1 - 1) + 4 * ((1 - 1) + 3 * 0)] == 0
    assert g.index[(4 - 1) + 4 * ((3 - 1) + 3 * 1)] == 2
    assert g.pos.dtype == np.int32 and g.index.dtype == np.int32
    # the predecessor of (3, 2, 1) at (4, 3, 2) is a node, the one of (1, 1, 1) at (2, 2, 2) is empty (not a node)
    assert graph_arcs_by_p(g) == {1 + 6 * (1 + 5): [], 3 + 6 * (2 + 5): [4 + 6 * (3 + 10)], 4 + 6 * (3 + 10): []}
    mask = np.array([True, False, True])
    full = G.expand_mask(g, mask)
    assert full.shape == (total,) and np.flatnonzero(full).tolist() == [1 + 6 * (1 + 5), 4 + 6 * (3 + 10)]


def test_predecessor_in_air_is_no_arc_even_behind_it():
    """An «air» block is free: the block under it has no arc to it, and nothing is inherited through it."""
    dims = (1, 1, 3)
    air = np.array([False, True, False])
    g = G.build_graph(dims, np.array([5, 0, -2], np.int64), air, None, [np.array([[0, 0, 1], [0, 0, 2]], np.int32)])
    indptr, indices = G.predecessors(g)
    assert indices[indptr[0] : indptr[1]].tolist() == [1]  # only the offset m = 2 reaches the node above the air
    assert g.n == 2


def test_empty_graph_and_arguments():
    g = G.build_graph((3, 2, 2), np.zeros(12, np.int64), np.ones(12, bool), None, [T2D])
    assert g.n == 0 and G.predecessors(g)[1].size == 0 and G.cone_mask(g).size == 0
    with pytest.raises(ValueError):
        G.build_graph((2, 2, 2), np.zeros(8, np.float64), np.zeros(8, bool), None, [T2D])  # v′ is an integer
    with pytest.raises(ValueError):
        flat = np.array([[0, 0, 0]], np.int32)  # m = 0 does not point up
        G.build_graph((2, 2, 2), np.zeros(8, np.int64), np.zeros(8, bool), None, [flat])
    with pytest.raises(ValueError):
        G.build_graph((2, 2, 2), np.zeros(8, np.int64), np.zeros(8, bool), np.full(8, 3, np.uint8), [T2D])


# --- limits ----------------------------------------------------------------------------------------------------------


def test_pit_too_large_limits():
    G.check_size(G.MAX_NODES, G.MAX_INDEX_BYTES // 4)  # exactly at the limits
    for nodes, cells in [(G.MAX_NODES + 1, 10), (10, G.MAX_INDEX_BYTES // 4 + 1), (10, 2**31)]:
        with pytest.raises(ApiError) as e:
            G.check_size(nodes, cells)
        assert (e.value.status, e.value.code) == (422, "pit_too_large")
        assert "регуляризац" in e.value.hint and "Модель" in e.value.message
        assert e.value.extra == {"nodes": nodes, "box_cells": cells}


def test_pit_too_large_by_the_box_and_nodes_in_build(monkeypatch):
    dims = (10, 10, 10)
    air = np.ones(1000, dtype=bool)
    air[0] = air[999] = False  # the box is the whole grid
    monkeypatch.setattr(G, "MAX_INDEX_BYTES", 3999)
    with pytest.raises(ApiError) as e:
        G.build_graph(dims, np.zeros(1000, np.int64), air, None, [T2D])
    assert e.value.code == "pit_too_large"
    monkeypatch.setattr(G, "MAX_INDEX_BYTES", 4000)
    assert G.build_graph(dims, np.zeros(1000, np.int64), air, None, [T2D]).n == 2
    monkeypatch.setattr(G, "MAX_NODES", 5)
    with pytest.raises(ApiError):
        G.build_graph(dims, np.zeros(1000, np.int64), np.zeros(1000, bool), None, [T2D])
    sub, _ = G.restrict_graph(G.build_graph(dims, np.zeros(1000, np.int64), air, None, [T2D]), np.array([True, False]))
    assert sub.n == 1


# --- cones -----------------------------------------------------------------------------------------------------------


def naive_cone(g, seed):
    indptr, indices = G.predecessors(g)
    seen = seed.copy()
    stack = list(np.flatnonzero(seed))
    while stack:
        u = stack.pop()
        for q in indices[indptr[u] : indptr[u + 1]]:
            if not seen[q]:
                seen[q] = True
                stack.append(int(q))
    return seen


@pytest.mark.parametrize("seed", range(5))
def test_cone_mask_equals_closure_of_positive_nodes(seed):
    dims = (9, 8, 10)
    value, air, tpl, tables = random_case(seed, dims, 0.2)
    value = np.where(np.random.default_rng(seed + 100).random(value.size) < 0.85, -np.abs(value), np.abs(value))
    g = G.build_graph(dims, value, air, tpl, tables)
    cone = G.cone_mask(g)
    assert cone.dtype == np.bool_ and cone.shape == (g.n,)
    assert np.array_equal(cone, naive_cone(g, g.value > 0))
    assert 0 < int(cone.sum()) < g.n
    assert G.is_closed(g, cone)
    assert np.array_equal(G.cone_mask(g, np.zeros(g.n, bool)), np.zeros(g.n, bool))
    one = np.zeros(g.n, bool)
    one[g.n // 2] = True
    assert np.array_equal(G.cone_mask(g, one), naive_cone(g, one))


def test_cone_mask_without_positive_values_is_empty():
    g = G.build_graph((3, 1, 3), -np.ones(9, np.int64), np.zeros(9, bool), None, [T2D])
    assert not G.cone_mask(g).any()


def test_cone_mask_bitwise_on_any_number_of_threads():
    dims = (40, 30, 20)
    rng = np.random.default_rng(5)
    value = np.where(rng.random(24000) < 0.9, -3, 7).astype(np.int64)
    g = G.build_graph(dims, value, rng.random(24000) < 0.1, rng.integers(0, 2, 24000).astype(np.uint8), [T3D_A, T3D_B])
    saved = numba.get_num_threads()
    try:
        res = []
        for th in (1, 2, 3, 4):
            numba.set_num_threads(min(th, numba.config.NUMBA_NUM_THREADS))
            res.append(G.cone_mask(g).tobytes())
    finally:
        numba.set_num_threads(saved)
    assert len(set(res)) == 1 and any(res[0])


def test_restrict_graph_to_the_cone_keeps_arcs_and_maps_back():
    dims = (9, 8, 10)
    value, air, tpl, tables = random_case(3, dims, 0.2)
    value = np.where(np.random.default_rng(1).random(value.size) < 0.85, -np.abs(value), np.abs(value))
    g = G.build_graph(dims, value, air, tpl, tables)
    cone = G.cone_mask(g)
    sub, ids = G.restrict_graph(g, cone)
    assert sub.n == int(cone.sum()) and np.array_equal(ids, np.flatnonzero(cone))
    assert np.array_equal(sub.value, g.value[ids]) and np.array_equal(sub.tpl, g.tpl[ids])
    assert np.array_equal(G.parent_index(sub), G.parent_index(g)[ids])
    # the cone is closed: no arc of a kept node is lost
    full = graph_arcs_by_p(g)
    assert graph_arcs_by_p(sub) == {p: full[p] for p in G.parent_index(sub).tolist()}
    assert sub.shape[0] * sub.shape[1] * sub.shape[2] <= g.shape[0] * g.shape[1] * g.shape[2]
    # dropping nodes that are needed removes arcs (they are «air» now)
    keep = np.ones(g.n, bool)
    keep[::3] = False
    sub2, _ = G.restrict_graph(g, keep)
    assert graph_arcs_by_p(sub2) == naive_arcs(dims, ~np.isin(np.arange(air.size), G.parent_index(sub2)), tpl, tables)


def test_with_values_shares_structure():
    g = G.build_graph((3, 1, 3), np.arange(9, dtype=np.int64), np.zeros(9, bool), None, [T2D])
    g2 = g.with_values(-g.value)
    assert g2.index is g.index and g2.pos is g.pos and np.array_equal(g2.value, -g.value)
    with pytest.raises(ValueError):
        g.with_values(np.zeros(3, np.int64))


# --- closure check ---------------------------------------------------------------------------------------------------


def test_closure_violations():
    dims, value = rows_to_grid(np.arange(15).reshape(3, 5))
    g = G.build_graph(dims, value, np.zeros(15, bool), None, [T2D])
    none = np.zeros(g.n, bool)
    assert G.closure_violations(g, none) == (0, -1, -1) and G.is_closed(g, none)
    assert G.is_closed(g, np.ones(g.n, bool))
    # the node of the bottom level 0, column 2 alone: three predecessors on the level 1 are not mined
    bad = np.zeros(g.n, bool)
    bad[2] = True
    count, u, q = G.closure_violations(g, bad)
    assert (count, u, q) == (3, 2, 5 + 1)
    # with the cone of the node it is closed
    assert G.is_closed(g, G.cone_mask(g, bad))
    with pytest.raises(ValueError):
        G.is_closed(g, np.zeros(3, bool))
