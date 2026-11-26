"""The nested pit shells and the single `solve` against the references (подшаг 21.7; ТЗ 8.11.3, 8.11.6; Т8.11-5,
Т8.11-9; ADR 0028 decisions 13, 16, 17): 25 shells of a synthetic 3D model (≈ 25 thousand nodes, two slope tables, a
terrain with «воздух») are nested and closed, and each is the SET that OR-Tools gives from scratch on the whole graph at
the same RF and the same quantum; bit-for-bit answers on repeats, on 1…8 threads and on any portion of HPF; sorted and
repeated factors; one shell; `pit_empty`; `pit_internal` on a broken closure; cancellation; the reserve solver."""

from __future__ import annotations

import numba
import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.mine import flow_ref as F
from bf_core.mine import graph as G
from bf_core.mine import hpf as H
from bf_core.mine import shells as SH
from bf_core.mine import solve as SV
from bf_core.mine import template as tp
from bf_core.mine.econ_table import Table
from tests.ref.pit_cone import full_cone

T9 = np.array([[0, 0, 1], [1, 0, 1], [-1, 0, 1], [0, 1, 1], [0, -1, 1], [1, 1, 1], [-1, -1, 1], [1, -1, 1],
                [-1, 1, 1]], dtype=np.int32)  # fmt: skip
T17 = np.vstack([T9, np.array([[2, 0, 2], [-2, 0, 2], [0, 2, 2], [0, -2, 2], [1, 1, 2], [-1, -1, 2], [1, -1, 2],
                               [-1, 1, 2]], dtype=np.int32)])  # fmt: skip
RFS = np.round(np.arange(0.3, 1.5001, 0.05), 2)  # the 25 factors of the defaults (ТЗ 8.11.5)


class Syn:
    """A synthetic pit: an ellipsoid of lognormal grades under a rolling terrain; v(RF) = max(A·RF − D; 0) − C_m."""

    def __init__(self, dims, seed=1, d=75.0, cm=6.0, two_tables=True, air=True, scale=1.0):
        nx, ny, nz = dims
        rng = np.random.default_rng(seed)
        k, j, i = np.meshgrid(np.arange(nz), np.arange(ny), np.arange(nx), indexing="ij")
        top = nz - 3 - 2 * np.sin(i / 7.0) - 2 * np.cos(j / 5.0)
        self.air = ((k >= top) if air else np.zeros(k.shape, bool)).ravel()
        r = (
            ((i - nx * 0.5) / (nx * 0.3)) ** 2
            + ((j - ny * 0.5) / (ny * 0.3)) ** 2
            + ((k - nz * 0.35) / (nz * 0.28)) ** 2
        )
        a = (450.0 * scale) * (np.exp(rng.normal(0, 0.6, k.shape)) * np.exp(-1.2 * r)).ravel()
        total = nx * ny * nz
        tpl = (rng.integers(0, 2, total).astype(np.uint8)) if two_tables else None
        self.dims = dims
        self.tpl_full = tpl
        self.tables = [T9, T17] if two_tables else [T9]
        self.graph = G.build_graph(dims, np.zeros(total, np.int64), self.air, tpl, self.tables)
        sel = ~self.air
        self.a, self.d, self.cm = a[sel], np.full(int(sel.sum()), d * scale), np.full(int(sel.sum()), cm * scale)

    def values(self, rf):
        return np.maximum(self.a * rf - self.d, 0.0) - self.cm

    def table(self):
        """The same nodes as an `econ_table.Table` of one row per node (the economics of 21.2 is not needed here)."""
        n = self.graph.n
        return Table(np.arange(n, dtype=np.uint64), np.arange(n + 1, dtype=np.int64), self.a, self.d, self.cm,
                     np.zeros(n, np.uint8), np.arange(n, dtype=np.int32), np.zeros(n, np.uint8),
                     None)  # type: ignore[arg-type]  # fmt: skip


def scratch(syn, rf, q):
    """OR-Tools from scratch on the WHOLE graph at this RF with the quantum of the scenario."""
    g = syn.graph.with_values(SH.scale(syn.values(rf), q))
    return F.solve(g), g


@pytest.fixture(scope="module")
def big():
    syn = Syn((40, 30, 24))
    return syn, SH.shells(syn.graph, syn.values, RFS)


# --- Т8.11-5: nested, closed, equal to the solution from scratch ----------------------------------------------------


def test_25_shells_are_nested_closed_and_equal_to_ortools_from_scratch(big):
    syn, res = big
    assert syn.graph.n > 20000 and res.n_shells == 25 and res.pit.dtype == np.int16
    assert res.blocks[0] > 0 and res.blocks[-1] > res.blocks[0]  # the defaults give a real range of pits
    prev = np.zeros(syn.graph.n, bool)
    for k in range(25):
        c = res.mask(k)
        assert not (prev & ~c).any(), f"shell {k + 1} does not hold the shell {k}"  # nested
        assert G.is_closed(syn.graph, c), f"shell {k + 1} is not closed"  # not one mined block without a predecessor
        ref, g = scratch(syn, RFS[k], res.q)
        assert np.array_equal(c, ref), f"shell {k + 1}: the set differs from OR-Tools from scratch"
        assert res.objective[k] == F.optimum(g.value, ref) == int(g.value[c].sum())
        assert res.blocks[k] == int(c.sum())
        prev = c
    assert (np.diff(res.blocks) >= 0).all() and len(set(res.blocks.tolist())) > 10  # the shells really differ
    assert res.pit.min() == 0 and res.pit.max() == 25
    assert np.array_equal(res.pit > 0, res.mask(24))  # outside C(RF_max): 0
    assert res.error == pytest.approx(syn.graph.n * res.q / 2.0)


def test_pit_label_is_the_smallest_shell_with_the_node(big):
    syn, res = big
    for k in (0, 7, 24):
        lower = res.mask(k - 1) if k else np.zeros(syn.graph.n, bool)
        assert np.array_equal(res.pit == k + 1, res.mask(k) & ~lower)
    assert res.stats["nodes"] == syn.graph.n and len(res.stats["solves"]) >= 25 - 5  # empty intervals are not solved


def test_a_table_gives_the_same_shells_as_the_function(big):
    syn, res = big
    tab = syn.table()
    assert np.array_equal(tab.values(1.0), syn.values(1.0))
    for th in (1, 3):
        again = SH.shells(syn.graph, tab, RFS, threads=th)
        assert again.pit.tobytes() == res.pit.tobytes() and again.q == res.q


def test_ortools_solver_gives_the_same_pit():
    small = Syn((24, 18, 14), seed=3)
    a = SH.shells(small.graph, small.values, RFS[::3])
    b = SH.shells(small.graph, small.values, RFS[::3], solver="ortools")
    assert a.solver == "hpf" and b.solver == "ortools" and a.pit.tobytes() == b.pit.tobytes()


def test_the_scenario_with_big_values_has_one_quantum_and_the_same_sets():
    syn = Syn((24, 18, 14), seed=4, scale=1e9)  # Σ|v| ≈ 10¹⁵…10¹⁶ $: q > 0.01
    res = SH.shells(syn.graph, syn.values, RFS[::4])
    assert res.q > 0.01
    for k, rf in enumerate(RFS[::4]):
        ref, g = scratch(syn, rf, res.q)
        assert np.array_equal(res.mask(k), ref)
        assert int(np.abs(g.value).sum()) <= 10**16 + g.n  # the bound of the solver holds at every RF


# --- Т8.11-9: «воздух», repeats, threads, portions ------------------------------------------------------------------


def test_air_blocks_do_not_matter_and_are_never_in_the_pit(big):
    syn, res = big
    assert syn.air.sum() > 1000
    grid = SH.pit_on_grid(syn.graph, res.pit)
    assert grid.shape == (syn.air.size,) and not grid[syn.air].any()  # nothing above the terrain, nothing in «воздух»
    assert (grid[~syn.air] == res.pit).all()
    # a graph built over «воздух» with garbage values and a model cut under the terrain agree: the arcs to air are free
    rng = np.random.default_rng(9)
    total = syn.air.size
    junk = rng.integers(-(10**12), 10**12, total)
    g2 = G.build_graph(syn.dims, junk, syn.air, syn.tpl_full, syn.tables)
    again = SH.shells(g2, syn.values, RFS)
    assert again.pit.tobytes() == res.pit.tobytes()
    # no arc leads into «воздух»: the predecessors are blocks (the cells of «air» are free, no node is behind them)
    ip, ix = G.predecessors(syn.graph)
    assert len(ix) > 0 and not np.isin(G.parent_index(syn.graph)[ix], np.flatnonzero(syn.air)).any()
    # …and a cell of «air» whose neighbour above is a block is NOT a bridge of this graph (no chain goes through it:
    # the table has no reduced offsets here) — the valley tests below are the ones where «air» carries a chain
    assert syn.graph.real is None and syn.graph.n == int((~syn.air).sum())


def test_bitwise_on_repeat_and_on_1_2_4_8_threads_and_any_portion():
    syn = Syn((20, 16, 12), seed=7)
    saved = numba.get_num_threads()
    try:
        base = SH.shells(syn.graph, syn.values, RFS, portion=None)
        outs = []
        for th in (1, 2, 4, 8):
            numba.set_num_threads(min(th, numba.config.NUMBA_NUM_THREADS))
            r = SH.shells(syn.graph, syn.table(), RFS, threads=th)
            outs.append((r.pit.tobytes(), r.blocks.tobytes(), r.objective.tobytes(), r.q))
    finally:
        numba.set_num_threads(saved)
    assert len(set(outs)) == 1 and outs[0][0] == base.pit.tobytes() and outs[0][3] == base.q
    for portion in (1, 7, 2**40):  # the size of a portion of HPF does not change a bit
        r = SH.shells(syn.graph, syn.values, RFS, portion=portion)
        assert r.pit.tobytes() == base.pit.tobytes() and r.objective.tobytes() == base.objective.tobytes()
    again = SH.shells(syn.graph, syn.values, RFS)  # a repeat
    assert again.pit.tobytes() == base.pit.tobytes()


# --- the list of factors --------------------------------------------------------------------------------------------


def test_unsorted_and_repeated_factors_are_sorted_and_merged():
    syn = Syn((20, 16, 12), seed=2)
    base = SH.shells(syn.graph, syn.values, RFS[::5])
    shuffled = np.concatenate([RFS[::5][::-1], RFS[::5][:2]])
    res = SH.shells(syn.graph, syn.values, shuffled.tolist())
    assert res.rf.tolist() == RFS[::5].tolist() and res.pit.tobytes() == base.pit.tobytes()
    for bad in ([], [0.0, 1.0], [float("nan")], [-1.0]):
        with pytest.raises(ValueError):
            SH.shells(syn.graph, syn.values, bad)


def test_one_factor_and_two_factors():
    syn = Syn((20, 16, 12), seed=2)
    one = SH.shells(syn.graph, syn.values, [1.0])
    ref, g = scratch(syn, 1.0, one.q)
    assert one.n_shells == 1 and np.array_equal(one.pit == 1, ref) and set(np.unique(one.pit)) == {0, 1}
    assert one.objective[0] == F.optimum(g.value, ref)
    two = SH.shells(syn.graph, syn.values, [1.2, 0.8])
    for k, rf in enumerate((0.8, 1.2)):
        assert np.array_equal(two.mask(k), scratch(syn, rf, two.q)[0])
    assert two.rf.tolist() == [0.8, 1.2]


def test_shells_with_no_ore_at_the_small_factors_are_empty_but_present():
    syn = Syn((20, 16, 12), seed=2)  # at RF 0.02 and 0.05 a block pays less than its cost: nothing is worth mining
    rfs = np.concatenate([[0.02, 0.05], RFS])
    res = SH.shells(syn.graph, syn.values, rfs)
    assert res.blocks[:2].tolist() == [0, 0] and res.blocks[-1] > 0 and res.n_shells == 27
    assert not res.mask(0).any() and not res.mask(1).any() and res.pit.min() == 0
    for k in (2, 3, 14, 26):
        assert np.array_equal(res.mask(k), scratch(syn, rfs[k], res.q)[0])


def test_empty_pit_at_rf_max_is_422_pit_empty():
    syn = Syn((12, 10, 8), seed=2)
    with pytest.raises(ApiError) as e:
        SH.shells(syn.graph, lambda rf: -np.ones(syn.graph.n), [0.5, 1.0])
    assert (e.value.status, e.value.code) == (422, "pit_empty")
    assert "положительной" in e.value.message and "1" in e.value.message and e.value.hint
    with pytest.raises(ApiError) as e2:
        SH.shells(syn.graph, lambda rf: np.zeros(syn.graph.n), [1.0])  # a zero is not a value
    assert e2.value.code == "pit_empty"


# --- the checks, the progress, the reserve --------------------------------------------------------------------------


def test_a_broken_closure_is_a_500_pit_internal_never_a_result(monkeypatch):
    syn = Syn((16, 12, 10), seed=2)
    real = SV.solve

    def broken(g, **kw):
        m = real(g, **kw)
        low = np.flatnonzero(m)
        if low.size > 20:  # drop the top blocks that the mined ones below still need
            m = m.copy()
            lvl = np.searchsorted(g.level_ptr, low, side="right") - 1
            m[low[lvl == lvl.max()]] = False
        return m

    monkeypatch.setattr(SH, "solve", broken)
    with pytest.raises(ApiError) as e:
        SH.shells(syn.graph, syn.values, RFS[::6])
    assert (e.value.status, e.value.code) == (500, "pit_internal") and e.value.hint and e.value.extra["arcs"] > 0


def test_progress_by_shells_and_cancellation():
    syn = Syn((16, 12, 10), seed=2)
    seen = []
    SH.shells(syn.graph, syn.values, RFS, tick=lambda done, total, text: seen.append((done, total, text)))
    assert seen[0] == (0, 25, "Оболочки 0/25") and seen[-1][0] == 25 and {t for _, t, _ in seen} == {25}
    assert all(b >= a for (a, _, _), (b, _, _) in zip(seen, seen[1:], strict=False))
    assert all(text == f"Оболочки {d}/25" for d, _, text in seen)

    class Cancelled(Exception):
        pass

    def stop(done, total, text):
        if done >= 5:
            raise Cancelled

    with pytest.raises(Cancelled):
        SH.shells(syn.graph, syn.values, RFS, tick=stop, portion=1)  # also between the portions of HPF


def test_solve_chooses_hpf_ortools_and_the_reserve(monkeypatch):
    syn = Syn((14, 10, 9), seed=2)
    g = syn.graph.with_values(SH.scale(syn.values(1.0), 0.01))
    base = F.solve(g)
    for name in ("auto", "hpf", "ortools"):
        st = {}
        assert np.array_equal(SV.solve(g, solver=name, stats=st), base)
        assert st["solver"] == ("ortools" if name == "ortools" else "hpf")
    with pytest.raises(ValueError):
        SV.solve(g, solver="gurobi")
    empty = SV.solve(g.with_values(-np.ones(g.n, np.int64)), tick=lambda f: None)
    assert not empty.any()

    def boom(*a, **k):
        raise RuntimeError("certificate")

    monkeypatch.setattr(H, "solve", boom)
    st = {}
    assert np.array_equal(SV.solve(g, solver="auto", stats=st), base) and st["solver"] == "ortools"  # the reserve
    with pytest.raises(RuntimeError):
        SV.solve(g, solver="hpf")
    res = SH.shells(syn.graph, syn.values, [0.8, 1.0, 1.2])
    assert res.solver == "ortools" and np.array_equal(res.mask(1), scratch(syn, 1.0, res.q)[0])


# --- hard data ------------------------------------------------------------------------------------------------------


def test_ties_and_zero_values_give_the_smallest_sets():
    syn = Syn((16, 12, 10), seed=11)
    rng = np.random.default_rng(3)
    syn.a = rng.integers(0, 40, syn.graph.n).astype(np.float64)  # whole numbers: many exact ties and zero values
    syn.d, syn.cm = np.full(syn.graph.n, 12.0), np.ones(syn.graph.n)
    rfs = np.arange(0.5, 2.01, 0.25)
    res = SH.shells(syn.graph, syn.values, rfs)
    assert len(set(res.blocks.tolist())) > 3
    for k, rf in enumerate(rfs):
        ref, g = scratch(syn, rf, res.q)
        assert np.array_equal(res.mask(k), ref) and res.objective[k] == F.optimum(g.value, ref)


def test_one_node_and_a_column_of_ore_under_waste():
    g1 = G.build_graph((1, 1, 1), np.zeros(1, np.int64), np.zeros(1, bool), None, [T9])
    one = SH.shells(g1, lambda rf: np.array([5.0 * rf - 3.0]), [0.5, 1.0, 2.0])  # v = −0.5, 2, 7: a pit from RF 1
    assert one.pit.tolist() == [2] and one.blocks.tolist() == [0, 1, 1]
    # a column of 5 blocks, the level grows UP and a predecessor is one level up: the ore is the LOWEST block (k = 0)
    # and four blocks of waste (−1 each) must be mined above it — the pit is the whole column or nothing
    g = G.build_graph((1, 1, 5), np.zeros(5, np.int64), np.zeros(5, bool), None, [np.array([[0, 0, 1]], np.int32)])
    ore = np.array([40.0, 0.0, 0.0, 0.0, 0.0])
    res = SH.shells(g, lambda rf: ore * rf - 1.0, [0.05, 0.1, 0.15, 0.2])  # the ore pays 4 + its own cost at RF 0.125
    assert res.blocks.tolist() == [0, 0, 5, 5] and res.pit.tolist() == [3] * 5
    assert G.is_closed(g, res.mask(2))


# --- bridges: «воздух» inside the box (a valley of the relief): the shells against the WHOLE cone (review 21.21б) ----


class Valley:
    """A rough relief with steep walls (valleys between ridges) over an ellipsoid of lognormal grades, 45° cubes,
    K = 3: the graph of the REDUCED template with bridges, and the reference — the whole cone with «air» as nodes of
    value 0."""

    def __init__(self, dims=(18, 14, 12), seed=3, rough=6, bridges=True):
        nx, ny, nz = dims
        rng = np.random.default_rng(seed)
        k, j, i = np.meshgrid(np.arange(nz), np.arange(ny), np.arange(nx), indexing="ij")
        top = rng.integers(nz - rough, nz + 1, (ny, nx))
        self.air = (k >= top[None]).ravel()
        r = ((i - nx * 0.5) / (nx * 0.35)) ** 2 + ((j - ny * 0.5) / (ny * 0.35)) ** 2
        r = r + ((k - nz * 0.4) / (nz * 0.4)) ** 2
        a = (450.0 * np.exp(rng.normal(0, 0.6, k.shape)) * np.exp(-1.0 * r)).ravel()
        self.dims = dims
        total = nx * ny * nz
        self.red = tp.build_template(1.0, 1.0, 1.0, tp.angle_table([(0.0, 45.0)]), k=3).tables[0].offsets
        self.full = full_cone(1.0, 1.0, 1.0, 45.0, 3)
        self.graph = G.build_graph(dims, np.zeros(total, np.int64), self.air, None, [self.red], bridges=bridges)
        sel = ~self.air
        self.a, self.d, self.cm = a[sel], np.full(int(sel.sum()), 70.0), np.full(int(sel.sum()), 6.0)
        self.ref_graph = G.build_graph(dims, np.zeros(total, np.int64), np.zeros(total, bool), None, [self.full])

    def values(self, rf):
        return np.maximum(self.a * rf - self.d, 0.0) - self.cm

    def reference(self, rf, q):
        """OR-Tools on the whole cone, every cell a node, at the quantum of the scenario: the mask of the real nodes."""
        v = np.zeros(self.air.size, np.int64)
        v[~self.air] = SH.scale(self.values(rf), q)
        return F.solve(self.ref_graph.with_values(v))[~self.air]


@pytest.mark.parametrize("solver", ["hpf", "ortools"])
def test_valley_shells_equal_the_whole_cone_from_scratch(solver):
    """The case of the review: «air» in a valley between two ridges is a bridge; the 25 shells of the reduced graph are
    the same SETS as OR-Tools gives on the whole (unreduced) cone from scratch at each RF — and the graph without the
    bridges loses the border of the cone (a different answer)."""
    v = Valley()
    assert v.graph.n > v.graph.n_real and v.graph.n_real == int((~v.air).sum())
    res = SH.shells(v.graph, v.values, RFS, solver=solver)
    assert res.pit.shape == (v.graph.n_real,) and res.stats["bridges"] == v.graph.n - v.graph.n_real
    assert res.blocks[-1] > 100
    for k, rf in enumerate(RFS):
        assert np.array_equal(res.mask(k), v.reference(rf, res.q)), f"RF {rf}"
    assert res.blocks.tolist() == [int(res.mask(k).sum()) for k in range(25)]  # the blocks are the real ones only
    grid = SH.pit_on_grid(v.graph, res.pit)
    assert not grid[v.air].any()  # no bridge and no «air» in the pit on the grid
    assert (grid[~v.air] == res.pit).all()
    plain = Valley(bridges=False)  # the old graph: the border of the cone is lost somewhere in the 25 shells
    old = SH.shells(plain.graph, plain.values, RFS, solver=solver)
    assert any(not np.array_equal(old.mask(k), res.mask(k)) for k in range(25))


def test_valley_flat_relief_graph_and_shells_are_the_old_ones():
    """A flat relief has no bridge: the same arrays, the same shells, bit for bit, with `bridges` or without."""
    on, off = Valley(rough=1, seed=3), Valley(rough=1, seed=3, bridges=False)
    assert on.graph.real is None and on.graph.n == off.graph.n
    a = SH.shells(on.graph, on.values, RFS[::4])
    b = SH.shells(off.graph, off.values, RFS[::4])
    assert a.pit.tobytes() == b.pit.tobytes() and a.q == b.q and a.blocks.tolist() == b.blocks.tolist()


def test_valley_shells_are_bitwise_on_repeat_and_threads():
    v = Valley(seed=5)
    saved = numba.get_num_threads()
    try:
        outs = []
        for th in (1, 2, 4):
            numba.set_num_threads(th)
            outs.append(SH.shells(v.graph, v.values, RFS[::3], threads=th).pit.tobytes())
    finally:
        numba.set_num_threads(saved)
    assert len(set(outs)) == 1


def test_valley_a_function_of_values_is_one_per_real_node():
    v = Valley()
    assert v.graph.n > v.graph.n_real
    with pytest.raises(ValueError):  # a value per node of the graph (bridges included) is not what the caller gives
        SH.shells(v.graph, lambda rf: np.zeros(v.graph.n), [1.0])
    res = SH.shells(v.graph, v.values, [1.0])  # one shell
    assert res.n_shells == 1 and res.error == pytest.approx(v.graph.n_real * res.q / 2)


def test_forged_nesting_is_caught_in_one_pass_as_pit_internal(monkeypatch):
    """The final check of all the shells is ONE pass over the arcs; a forged label of a block (nested, but not closed)
    is a 500 `pit_internal` with the shell and the arc — never a result."""
    syn = Syn((14, 10, 9), seed=6)
    go = SH._Run.go

    def forge(self):
        go(self)
        top = int(self.total)
        have = np.flatnonzero(self.lab == top)
        assert have.size
        self.lab[have[0]] = 1  # a node of the last shell goes into shell 1: its predecessors are in later shells

    monkeypatch.setattr(SH._Run, "go", forge)
    with pytest.raises(ApiError) as e:
        SH.shells(syn.graph, syn.values, RFS[::6])
    assert (e.value.status, e.value.code) == (500, "pit_internal") and e.value.hint
    assert e.value.extra["arcs"] > 0 and e.value.extra["shell"] == 1 and e.value.extra["predecessor_shell"] > 1


@pytest.mark.parametrize("n", [2, 3, 6])
@pytest.mark.parametrize("solver", ["hpf", "auto"])
def test_a_column_of_n_nodes_with_the_hpf_label_n(n, solver):
    """A column of n blocks (the block above is the predecessor): the bottom one pays, every other one costs. The sink is
    at the distance n from the bottom node, and a graph of 2 nodes appears in the shells on its own (finding 2 of the
    review: the labels of HPF reach n). `solver="hpf"` has no reserve, so a RuntimeError of the solver would show."""
    g = G.build_graph((1, 1, n), np.zeros(n, np.int64), np.zeros(n, bool), None, [np.array([[0, 0, 1]], np.int32)])

    def values(rf):
        return np.array([60.0 * rf - 5.0] + [-10.0] * (n - 1))  # the head pays at RF ≥ 0.25·(n − 1) + …

    rfs = [0.3, 0.6, 0.9, 1.2, 1.5]
    res = SH.shells(g, values, rfs, solver=solver)
    ref = SH.shells(g, values, rfs, solver="ortools")
    assert res.pit.tobytes() == ref.pit.tobytes() and res.blocks.tolist() == ref.blocks.tolist()
    for k, rf in enumerate(rfs):
        pays = 60.0 * rf - 5.0 > 10.0 * (n - 1) * (1 + 1e-9)
        assert bool(res.mask(k).all()) == pays and bool(res.mask(k).any()) == pays, (n, rf)
    assert res.blocks[-1] == (n if 60.0 * 1.5 - 5.0 > 10.0 * (n - 1) else 0)
