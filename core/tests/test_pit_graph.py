"""The closure graph of the pit optimiser (подшаг 21.4; ADR 0028 decision 13; ТЗ 8.11.3).

The reference is a naive Python loop over cells with the convention «an offset (Δi, Δj, m) points to the predecessor
(i + Δi, j + Δj, k + m): levels grow UP»; the tests use hand-made tables (the template of 21.3 has the same
convention)."""

from __future__ import annotations

import numba
import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.mine import flow_ref as F
from bf_core.mine import graph as G
from bf_core.mine import template as tp
from tests.ref.pit_cone import full_cone

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


# --- bridges (review 21.21б: a chain of the reduced template through an «air» cell) ----------------------------------

CUBES = (1.0, 1.0, 1.0)


def reduced(phi, k, steps=CUBES):
    return tp.build_template(*steps, tp.angle_table([(0.0, phi)]), k=k).tables[0].offsets


def reach_of(g, keep):
    """{P of a node of `keep`: the set of the P of the nodes of `keep` that it needs (the closure of the arcs)}."""
    indptr, indices = G.predecessors(g)
    p = G.parent_index(g)
    out = {}
    for u in range(g.n):
        if not keep[p[u]]:
            continue
        seen, stack = {u}, [u]
        while stack:
            x = stack.pop()
            for y in indices[indptr[x] : indptr[x + 1]]:
                if int(y) not in seen:
                    seen.add(int(y))
                    stack.append(int(y))
        out[int(p[u])] = frozenset(int(p[x]) for x in seen if keep[p[x]])
    return out


def valley_case():
    """The scenario of the review: 5×1×3, cubes, 45°, K = 2; «air» at (1, 0, 1); +100 at (0, 0, 0), −10 at (2, 0, 2)."""
    dims = (5, 1, 3)
    air = np.zeros(15, bool)
    air[1 + 5 * 1] = True
    value = np.zeros(15, np.int64)
    value[0], value[2 + 5 * 2] = 100, -10
    return dims, air, value


def test_valley_the_chain_through_air_is_kept_by_a_bridge():
    dims, air, value = valley_case()
    off = reduced(45.0, 2)
    assert (2, 0, 2) not in {tuple(r) for r in off.tolist()}  # the offset of the border of the cone is a sum
    plain = G.build_graph(dims, value, air, None, [off])
    g = G.build_graph(dims, value, air, None, [off], bridges=True)
    top = 2 + 5 * 2
    # without the bridge the node (0, 0, 0) does not need (2, 0, 2): the border of the cone is lost
    assert top not in reach_of(plain, ~air)[0]
    assert (g.n, g.n_real) == (15, 14) and g.real is not None and int((~g.real).sum()) == 1
    p = G.parent_index(g)
    assert p[~g.real].tolist() == [1 + 5 * 1]  # the bridge is the «air» cell (1, 0, 1)
    assert g.value[~g.real].tolist() == [0] and g.tpl[~g.real].tolist() == [0]
    assert top in reach_of(g, ~air)[0]
    # the reference: the whole cone, «air» as a node of value 0
    ref = G.build_graph(dims, value, np.zeros(15, bool), None, [full_cone(1, 1, 1, 45.0, 2)])
    assert reach_of(g, ~air) == reach_of(ref, ~air)
    # the optimum: +100 needs the −10 behind the air, only the bridge says so
    mined = F.solve(g)
    assert bool(mined[np.flatnonzero(p == top)[0]])
    assert not bool(F.solve(plain)[np.flatnonzero(G.parent_index(plain) == top)[0]])  # the old answer lost the border
    assert np.array_equal(mined[g.real], F.solve(ref)[~air])


def test_bridge_interface_expand_real_pit_real_view_masks():
    dims, air, value = valley_case()
    g = G.build_graph(dims, value, air, None, [reduced(45.0, 2)], bridges=True)
    assert g.n_real == int((~air).sum())
    vals = np.arange(1, g.n_real + 1, dtype=np.int64)
    full = g.expand(vals)
    assert full.shape == (g.n,) and full[~g.real].tolist() == [0] and np.array_equal(full[g.real], vals)
    assert np.array_equal(g.real_pit(full), vals) and full.dtype == np.int64
    with pytest.raises(ValueError):
        g.expand(vals[:-1])
    with pytest.raises(ValueError):
        g.real_pit(vals)  # one entry per real node is not one per node
    g2 = g.with_values(full)
    assert g2.real is g.real
    bad = full.copy()
    bad[~g.real] = 5
    with pytest.raises(ValueError):
        g.with_values(bad)  # the value of a bridge is 0
    assert np.array_equal(G.parent_index(g, real_only=True), np.flatnonzero(~air))
    view = g.real_view()
    assert view.n == g.n_real and view.real is None and np.array_equal(G.parent_index(view), np.flatnonzero(~air))
    # a mask over the nodes goes back to the parent grid without the bridge
    assert np.array_equal(G.expand_mask(g, np.ones(g.n, bool)), ~air)
    # a subgraph keeps the flag (and loses it when no bridge is left)
    sub, _ = G.restrict_graph(g, np.ones(g.n, bool))
    assert sub.real is not None and np.array_equal(sub.real, g.real)
    assert G.restrict_graph(g, g.real)[0].real is None
    # the cone of the positive node passes through the bridge
    cone = G.cone_mask(g)
    assert cone[~g.real].all() and cone[g.real].sum() >= 2


def test_flat_relief_no_bridges_graph_is_bit_for_bit_the_old_one():
    """«Воздух» above all the blocks (a flat relief, a trimmed box): nothing to bridge, the same arrays, `real` None."""
    rng = np.random.default_rng(3)
    dims = (9, 7, 8)
    nx, ny, nz = dims
    air = np.repeat(np.arange(nz), nx * ny) >= 6  # the top two levels are «воздух»: outside the box
    value = rng.integers(-9, 10, air.size).astype(np.int64)
    off = reduced(45.0, 3)
    a = G.build_graph(dims, value, air, None, [off])
    b = G.build_graph(dims, value, air, None, [off], bridges=True)
    assert b.real is None and b.n == b.n_real == a.n
    for name in ("pos", "index", "value", "tpl", "tab_ptr", "tab_off", "level_ptr"):
        assert getattr(a, name).tobytes() == getattr(b, name).tobytes(), name
    assert (a.dims, a.origin, a.shape) == (b.dims, b.origin, b.shape)
    c = G.build_graph(dims, value, np.zeros(air.size, bool), None, [off], bridges=True)  # no «air» in the box at all
    assert c.real is None and c.n == air.size


def random_terrain(rng, dims, rough):
    nx, ny, nz = dims
    kk = np.repeat(np.arange(nz), nx * ny).reshape(nz, ny, nx)
    top = rng.integers(max(1, nz - rough), nz + 1, (ny, nx))
    return (kk >= top[None]).ravel()


@pytest.mark.parametrize("seed", range(12))
def test_bridges_reproduce_the_full_cone_over_all_cells(seed):
    """The reduced template + bridges needs exactly what the whole cone needs when «air» is a node of value 0: random
    «воздух» (holes), a rough relief, steps of 1 and 2, angles 30…60°, K = 2…4."""
    rng = np.random.default_rng(100 + seed)
    phi = float(rng.choice([30.0, 45.0, 50.0, 60.0]))
    k = int(rng.integers(2, 5))
    dx, dy = (float(x) for x in rng.choice([1.0, 1.0, 2.0], 2))
    dims = tuple(int(x) for x in rng.integers(4, 8, 3))
    total = int(np.prod(dims))
    air = random_terrain(rng, dims, 4) if seed % 2 else rng.random(total) < rng.choice([0.15, 0.35])
    if air.all():
        air[0] = False
    t = tp.build_template(dx, dy, 1.0, tp.angle_table([(0.0, phi)]), k=k)
    value = rng.integers(-5, 6, total).astype(np.int64)
    g = G.build_graph(dims, value, air, None, [t.tables[0].offsets], bridges=True)
    ref = G.build_graph(dims, value, np.zeros(total, bool), None, [full_cone(dx, dy, 1.0, phi, t.k)])
    assert reach_of(g, ~air) == reach_of(ref, ~air)
    assert g.n_real == int((~air).sum())


def test_bridges_are_deterministic_and_each_has_an_arc():
    rng = np.random.default_rng(8)
    dims = (10, 8, 9)
    air = random_terrain(rng, dims, 5)
    value = rng.integers(-9, 10, air.size).astype(np.int64)
    off = reduced(45.0, 4)
    a = G.build_graph(dims, value, air, None, [off], bridges=True)
    b = G.build_graph(dims, value, air, None, [off], bridges=True)
    assert a.n > a.n_real  # a rough relief has valleys
    for name in ("pos", "index", "value", "tpl", "level_ptr", "real"):
        assert getattr(a, name).tobytes() == getattr(b, name).tobytes(), name
    assert np.all(np.diff(G.parent_index(a)) > 0)  # the nodes are in the order of P, the bridges among them
    indptr, _ = G.predecessors(a)
    assert (np.diff(indptr)[~a.real] > 0).all()  # a bridge without a predecessor is not a bridge


def test_bridges_with_domains_use_the_table_given_for_them():
    """With several tables a bridge takes `bridge_table` (the general one by default): the assumption of the review."""
    rng = np.random.default_rng(5)
    dims = (8, 6, 7)
    air = random_terrain(rng, dims, 4)
    tpl = rng.integers(0, 2, air.size).astype(np.uint8)
    value = rng.integers(-9, 10, air.size).astype(np.int64)
    tabs = [reduced(45.0, 3), reduced(35.0, 3)]
    g0 = G.build_graph(dims, value, air, tpl, tabs, bridges=True)
    g1 = G.build_graph(dims, value, air, tpl, tabs, bridges=True, bridge_table=1)
    assert g0.n_real == g1.n_real == int((~air).sum())
    assert (g0.tpl[~g0.real] == 0).all() and (g1.tpl[~g1.real] == 1).all()
    assert np.array_equal(g0.tpl[g0.real], tpl[~air]) and np.array_equal(g1.value[g1.real], value[~air])
    with pytest.raises(ValueError):
        G.build_graph(dims, value, air, tpl, tabs, bridges=True, bridge_table=2)


# --- the nesting check of all the shells in one pass -----------------------------------------------------------------


def test_nesting_violations_one_pass():
    dims, value = rows_to_grid(np.arange(15).reshape(3, 5))
    g = G.build_graph(dims, value, np.zeros(15, bool), None, [T2D])
    level = (np.searchsorted(g.level_ptr, np.arange(g.n), side="right") - 1).astype(np.int16)
    lab = (level.max() + 1 - level).astype(np.int16)  # the top level is in shell 1, the next in 2, …: nested, closed
    assert G.nesting_violations(g, lab) == (0, -1, -1, 0, 0)
    assert G.nesting_violations(g, np.zeros(g.n, np.int16)) == (0, -1, -1, 0, 0)  # nothing mined
    bad = lab.copy()  # a node mined in shell 1 whose predecessors come only in shell 3 (the shell is not closed)
    bad[0] = 1
    count, u, _q, lu, lq = G.nesting_violations(g, bad)
    assert (u, lu) == (0, 1) and count > 0 and lq > lu
    bad = lab.copy()  # a predecessor that is in no shell
    bad[g.index[5 + 1]] = 0
    count, _u, _q, lu, lq = G.nesting_violations(g, bad)
    assert count > 0 and lq == 0 and lu > 0
    with pytest.raises(ValueError):
        G.nesting_violations(g, lab[:-1])
