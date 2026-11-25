"""The nodes of a simulation (ТЗ 8.8.1; ADR 0024 §9; Д-23; Э18б, 18.30): the spiral template equals the full enumeration
of the offsets inside the search ellipsoid (the order by the anisotropic distance or by C, equal keys by the offset),
the search of the simulated nodes equals the full enumeration over the simulated nodes (limit 0–64, only simulated
targets), the grid of the targets, the conditioning samples of the nodes equal `Searcher.gather`, read-only inputs
add no signature of a kernel (the lesson of 17.39б), the time of a search."""

from __future__ import annotations

import time

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.geostat.estimate import cov as cv
from bf_core.geostat.estimate import search as se
from bf_core.geostat.estimate.samples import prepare
from bf_core.geostat.estimate.spec_parts import Pass
from bf_core.geostat.simulation import nodes as nd
from bf_core.geostat.variogram.model import Model, Structure

TIE = 1e-12


def ellipsoids() -> dict[str, se.Ellipsoid]:
    return {
        "isotropic": se.Ellipsoid.of((90.0, 0.0, 0.0), (7.0, 7.0, 7.0)),
        "axes": se.Ellipsoid.of((90.0, 0.0, 0.0), (9.0, 5.0, 3.0)),
        "rotated": se.Ellipsoid.of((35.0, -20.0, 15.0), (10.0, 6.0, 3.5)),
        "flat": se.Ellipsoid.of((60.0, 10.0, 0.0), (12.0, 8.0, 0.5)),
    }


def chain_groups(key: np.ndarray) -> np.ndarray:
    """The reference of the equal keys: sorted keys, a new group where the step exceeds TIE (a loop, on purpose)."""
    o = np.argsort(key, kind="stable")
    g = np.zeros(key.size, dtype=np.int64)
    cur = 0
    for a in range(1, key.size):
        if key[o[a]] - key[o[a - 1]] > TIE * max(1.0, abs(key[o[a]])):
            cur += 1
        g[o[a]] = cur
    return g


def ref_template(steps, ell: se.Ellipsoid, keyf=None, reach: int = 40) -> np.ndarray:
    """Every offset of a box far larger than the ellipsoid; inside = h_s ≤ r1 (the slack of the search), sorted by
    (key, di, dj, dk). `h_s` by the matrix product of NumPy, per offset (not the code of the module)."""
    off = np.array([(i, j, k) for i in range(-reach, reach + 1) for j in range(-reach, reach + 1)
                    for k in range(-reach, reach + 1)])  # fmt: skip
    off = off[np.any(off != 0, axis=1)]
    d = off * np.asarray(steps, dtype=np.float64)
    h = np.array([np.linalg.norm(ell.S @ row) for row in d])
    inside = h <= ell.r1 * (1.0 + 1e-9) + 1e-9
    off, d, h = off[inside], d[inside], h[inside]
    key = h / ell.r1 if keyf is None else keyf(d)
    g = chain_groups(key)
    return off[np.lexsort((off[:, 2], off[:, 1], off[:, 0], g))]


@pytest.mark.parametrize("name", ["isotropic", "axes", "rotated", "flat"])
@pytest.mark.parametrize("steps", [(1.0, 1.0, 1.0), (1.0, 0.5, 2.0)])
def test_template_equals_full_enumeration(name: str, steps) -> None:
    ell = ellipsoids()[name]
    got = nd.spiral_template(steps, ell)
    want = ref_template(steps, ell, reach=26)
    assert got.dtype == np.int32 and got.flags.c_contiguous and got.flags.writeable
    assert np.array_equal(got, want)


def test_template_is_symmetric_sorted_without_the_centre() -> None:
    ell = ellipsoids()["rotated"]
    st = np.array((1.0, 1.3, 0.8))
    t = nd.spiral_template(st, ell)
    assert t.shape[0] > 100
    assert not np.any(np.all(t == 0, axis=1))
    assert len({tuple(r) for r in t.tolist()}) == t.shape[0]
    assert {tuple(r) for r in t.tolist()} == {tuple((-t).tolist()[a]) for a in range(t.shape[0])}
    h = ell.h(t * st)
    assert np.all(np.diff(h) >= -TIE * np.maximum(1.0, h[1:]))  # by the anisotropic distance
    assert np.all(h <= ell.r1 * (1.0 + 1e-9) + 1e-9)


def test_equal_distances_go_by_the_offset() -> None:
    """Isotropic, a unit step: the four neighbours of one distance are (−1,0,0), (0,−1,0), (0,1,0), (1,0,0) — the
    key is the offset (di, dj, dk) ascending — then the diagonals; a plain sort would not give it (ties to 1e−16)."""
    t = nd.spiral_template((1.0, 1.0, 1.0), ellipsoids()["isotropic"])
    assert t[:6].tolist() == [[-1, 0, 0], [0, -1, 0], [0, 0, -1], [0, 0, 1], [0, 1, 0], [1, 0, 0]]
    assert t[6:18].tolist()[:3] == [[-1, -1, 0], [-1, 0, -1], [-1, 0, 1]]


def test_template_by_covariance() -> None:
    """With a model the order is by C descending (GSLIB); the set is that of the ellipsoid."""
    model = Model(0.1, (Structure("spherical", 0.5, 8.0, 4.0, 4.0, 0.0, 0.0, 0.0),
                        Structure("exponential", 0.4, 3.0, 6.0, 3.0, 40.0, 0.0, 0.0)))  # fmt: skip
    cm = cv.cov_model(model)
    ell = ellipsoids()["axes"]
    st = (1.0, 1.0, 1.0)
    got = nd.spiral_template(st, ell, cm)

    def keyf(d: np.ndarray) -> np.ndarray:
        return -np.array([cv.cov_c(*cm.args, *row) for row in d]) / cm.sill

    want = ref_template(st, ell, keyf, reach=12)
    assert np.array_equal(got, want)
    by_h = nd.spiral_template(st, ell)
    assert {tuple(r) for r in got.tolist()} == {tuple(r) for r in by_h.tolist()}
    assert not np.array_equal(got, by_h)
    c = np.array([cv.cov_c(*cm.args, *row) for row in got.astype(np.float64)])
    assert np.all(np.diff(c) <= 1e-12)


def test_template_shape_cuts_the_box() -> None:
    ell = ellipsoids()["isotropic"]
    full = nd.spiral_template((1.0, 1.0, 1.0), ell)
    cut = nd.spiral_template((1.0, 1.0, 1.0), ell, shape=(5, 4, 1))
    keep = (np.abs(full[:, 0]) <= 4) & (np.abs(full[:, 1]) <= 3) & (full[:, 2] == 0)
    assert np.array_equal(cut, full[keep])


def test_template_limit_and_box() -> None:
    small = se.Ellipsoid.of((90.0, 0.0, 0.0), (25.0, 25.0, 25.0))
    full = nd.spiral_template((1.0, 1.0, 1.0), small)
    assert np.array_equal(nd.spiral_template((1.0, 1.0, 1.0), small, limit=1000), full[:1000])
    # an ellipsoid of a million cells: the box is shrunk to fit, the nearest offsets are the same
    huge = se.Ellipsoid.of((90.0, 0.0, 0.0), (1e6, 1e6, 1e6))
    t = nd.spiral_template((1.0, 1.0, 1.0), huge, limit=1000)
    assert t.shape == (1000, 3)
    assert np.array_equal(t, full[:1000])


def test_template_bad_input() -> None:
    ell = ellipsoids()["isotropic"]
    for st in ((0.0, 1.0, 1.0), (1.0, 1.0), (1.0, np.nan, 1.0), (-1.0, 1.0, 1.0)):
        with pytest.raises(ApiError) as e:
            nd.spiral_template(st, ell)
        assert e.value.code == "bad_param" and e.value.extra["param"] == "steps"
    with pytest.raises(ApiError):
        nd.spiral_template((1.0, 1.0, 1.0), ell, limit=0)


# --- the grid -------------------------------------------------------------------------------------


def targets(counts, share: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    p = np.flatnonzero(rng.random(int(np.prod(counts))) < share)
    rng.shuffle(p)  # the order of the model is not the order of the cells
    return p.astype(np.uint64)


def test_grid_maps_parents_to_nodes() -> None:
    counts = (7, 5, 3)
    p = targets(counts, 0.6, 1)
    g = nd.NodeGrid.of(counts, p)
    assert g.n == p.size and g.index.dtype == np.int32 and g.cell.dtype == np.int64
    assert np.array_equal(g.index[p.astype(np.int64)], np.arange(p.size))
    assert int((g.index >= 0).sum()) == p.size
    i, j, k = g.ijk()
    assert np.array_equal(i + 7 * (j + 5 * k), p.astype(np.int64))
    assert i.max() < 7 and j.max() < 5 and k.max() < 3


def test_grid_refuses() -> None:
    for counts, p, code in (((3, 3, 3), [0, 1, 27], "bad_param"), ((3, 3, 3), [0, 1, 1], "bad_param"),
                            ((3, 0, 3), [0], "bad_param"), ((2**20, 2**20, 4), [0], "grid_too_large"),
                            ((1000, 1000, 301), [0], "grid_too_large")):  # fmt: skip
        with pytest.raises(ApiError) as e:
            nd.NodeGrid.of(counts, np.array(p, dtype=np.uint64))
        assert e.value.status == 422 and e.value.code == code and e.value.hint
    nd.check_cells(nd.MAX_CELLS)  # the limit itself passes (nothing is allocated here)
    assert nd.NodeGrid.of((2, 2, 2), np.array([], dtype=np.uint64)).n == 0


# --- the search of the simulated nodes ------------------------------------------------------------


def ref_nearest(grid: nd.NodeGrid, ell: se.Ellipsoid, steps, simulated: np.ndarray, node: int, n: int) -> list[int]:
    """The full enumeration: every simulated node, its offset, inside the ellipsoid, by (h_s, offset)."""
    ci, cj, ck = (a[node] for a in grid.ijk())
    si, sj, sk = (a[simulated] for a in grid.ijk())
    ids = np.flatnonzero(simulated)
    off = np.stack((si - ci, sj - cj, sk - ck), axis=1)
    h = np.array([np.linalg.norm(ell.S @ (row * np.asarray(steps))) for row in off])
    inside = (h <= ell.r1 * (1.0 + 1e-9) + 1e-9) & np.any(off != 0, axis=1)
    ids, off, h = ids[inside], off[inside], h[inside]
    g = chain_groups(h / ell.r1)
    order = np.lexsort((off[:, 2], off[:, 1], off[:, 0], g))
    return [int(v) for v in ids[order][:n]]


@pytest.mark.parametrize("name", ["isotropic", "axes", "rotated"])
def test_nearest_nodes_equal_full_enumeration(name: str) -> None:
    counts, steps = (13, 11, 6), (1.0, 1.0, 1.5)
    ell = ellipsoids()[name]
    grid = nd.NodeGrid.of(counts, targets(counts, 0.7, 4))
    tpl = nd.spiral_template(steps, ell, shape=counts)
    rng = np.random.default_rng(5)
    order = rng.permutation(grid.n)
    for n_nodes in (0, 1, 12, 64):
        nodes, cnt = nd.replay_neighbours(grid, order, tpl, n_nodes)
        assert nodes.shape == (grid.n, n_nodes)
        sim = np.zeros(grid.n, dtype=bool)
        for s, node in enumerate(order):
            if s % 7 == 0 or s > grid.n - 3:  # a sample of the steps: the reference is slow
                want = ref_nearest(grid, ell, steps, sim, int(node), n_nodes)
                assert nodes[s, : cnt[s]].tolist() == want, (name, n_nodes, s)
                assert cnt[s] == len(want)
                got = nd.nearest_nodes(grid, sim, tpl, int(node), n_nodes)
                assert got.tolist() == want and got.dtype == np.int64
            assert np.all(nodes[s, cnt[s] :] == -1)
            sim[node] = True


def test_only_simulated_targets_and_the_limit() -> None:
    counts = (9, 9, 1)
    grid = nd.NodeGrid.of(counts, targets(counts, 0.5, 8))
    tpl = nd.spiral_template((1.0, 1.0, 1.0), se.Ellipsoid.of((90.0, 0.0, 0.0), (20.0, 20.0, 20.0)), shape=counts)
    sim = np.zeros(grid.n, dtype=bool)
    node = int(grid.n // 2)
    assert nd.nearest_nodes(grid, sim, tpl, node, 12).size == 0  # nothing simulated yet
    sim[:] = True
    sim[node] = False
    for n in (0, 1, 5, 12, 64):
        got = nd.nearest_nodes(grid, sim, tpl, node, n)
        assert got.size == min(n, grid.n - 1) and node not in got and sim[got].all()
        assert len(set(got.tolist())) == got.size
    sim[: grid.n // 2] = False  # the half simulated: only those are found
    got = nd.nearest_nodes(grid, sim, tpl, node, 64)
    assert sim[got].all() and got.size == min(64, int(sim.sum()))


def test_node_without_the_template_around_finds_none() -> None:
    counts = (30, 30, 1)
    grid = nd.NodeGrid.of(counts, np.array([0, 29 + 30 * 29], dtype=np.uint64))  # two far corners
    tpl = nd.spiral_template((1.0, 1.0, 1.0), se.Ellipsoid.of((90.0, 0.0, 0.0), (5.0, 5.0, 5.0)), shape=counts)
    sim = np.array([True, False])
    assert nd.nearest_nodes(grid, sim, tpl, 1, 12).size == 0


@pytest.mark.parametrize("bad", [-1, 65, True, 1.5, None, "12"])
def test_nodes_limit_is_checked(bad: object) -> None:
    with pytest.raises(ApiError) as e:
        nd.check_nodes(bad)
    assert e.value.status == 422 and e.value.code == "bad_param" and e.value.extra["param"] == "nodes"
    assert [nd.check_nodes(v) for v in (0, 12, 64, np.int64(7))] == [0, 12, 64, 7]


def test_bad_node_or_path() -> None:
    grid = nd.NodeGrid.of((3, 3, 1), np.arange(9, dtype=np.uint64))
    tpl = nd.spiral_template((1.0, 1.0, 1.0), ellipsoids()["isotropic"], shape=grid.shape)
    with pytest.raises(ApiError):
        nd.nearest_nodes(grid, np.zeros(9, bool), tpl, 9, 12)
    with pytest.raises(ApiError):
        nd.nearest_nodes(grid, np.zeros(8, bool), tpl, 0, 12)
    with pytest.raises(ApiError):
        nd.replay_neighbours(grid, np.array([0, 9]), tpl, 12)


# --- the signatures -------------------------------------------------------------------------------


def frozen(a: np.ndarray) -> np.ndarray:
    out = np.array(a)
    out.setflags(write=False)
    return out


def test_read_only_inputs_add_no_signature() -> None:
    nd.warm_nodes()
    kernels = (nd.find_nodes, nd.replay, nd._cov_keys)
    before = [len(k.signatures) for k in kernels]
    assert before == [1, 1, 1]
    counts = (6, 5, 4)
    grid = nd.NodeGrid.of(counts, targets(counts, 0.8, 2))
    ell = ellipsoids()["rotated"]
    model = Model(0.1, (Structure("spherical", 0.9, 6.0, 4.0, 3.0, 20.0, 0.0, 0.0),))
    cm = cv.cov_model(model)
    ro_cm = cv.CovModel(frozen(cm.kinds), frozen(cm.pars), frozen(cm.head))
    t_rw = nd.spiral_template((1.0, 1.0, 1.0), ell, cm, shape=counts)
    t_ro = nd.spiral_template((1.0, 1.0, 1.0), ell, ro_cm, shape=counts)
    assert np.array_equal(t_rw, t_ro)
    ro_grid = nd.NodeGrid(grid.shape, frozen(grid.cell), frozen(grid.index))
    order = np.random.default_rng(3).permutation(grid.n)
    sim = np.zeros(grid.n, dtype=bool)
    sim[order[:20]] = True
    a = nd.nearest_nodes(grid, sim, t_rw, int(order[-1]), 12)
    b = nd.nearest_nodes(ro_grid, frozen(sim), frozen(t_ro), int(order[-1]), 12)
    assert a.tolist() == b.tolist() and a.size > 0
    r1 = nd.replay_neighbours(grid, order, t_rw, 12)
    r2 = nd.replay_neighbours(ro_grid, frozen(order), frozen(t_ro), 12)
    assert all(np.array_equal(x, y) for x, y in zip(r1, r2, strict=True))
    assert [len(k.signatures) for k in kernels] == before


# --- the conditioning samples ---------------------------------------------------------------------


def searcher(n: int = 200, n_max: int = 9, seed: int = 1) -> se.Searcher:
    rng = np.random.default_rng(seed)
    xyz = rng.uniform(0.0, 40.0, (n, 3))
    s = prepare(xyz, rng.uniform(0.0, 5.0, n), None, None, rng.integers(0, 12, n).astype(np.int32),
                [f"h{i}" for i in range(12)], np.zeros(n, np.int16), np.arange(n, dtype=np.int64),
                np.zeros((n, 2)), origin=np.zeros(3))  # fmt: skip
    return se.Searcher(s, se.Ellipsoid.of((30.0, 5.0, 0.0), (15.0, 10.0, 6.0)), (Pass(1.0, 1, n_max, 0, 0, 0, 0),))


def test_sample_neighbours_equal_gather_by_portions() -> None:
    sr = searcher()
    c = np.random.default_rng(2).uniform(-5.0, 45.0, (500, 3))
    c[7] = np.nan
    ref = sr.gather(c, 0)
    ticks: list[float | None] = []
    idx, n = nd.sample_neighbours(sr, c, portion=64, tick=lambda p, m: ticks.append(p))
    assert np.array_equal(idx, ref.idx) and np.array_equal(n, ref.n)
    assert idx.dtype == np.int32 and n.dtype == np.int16 and idx.shape == (500, 9)
    assert n[7] == 0 and np.all(idx[7] == -1)
    assert 0 < n.min() or (n == 0).any()
    assert ticks[-1] == 1.0 and ticks == sorted(ticks) and len(ticks) == 8
    idx1, n1 = nd.sample_neighbours(sr, c)  # one portion — the same rows
    assert np.array_equal(idx1, idx) and np.array_equal(n1, n)


def test_sample_neighbours_cancel() -> None:
    class Cancel(Exception):
        pass

    def tick(p: float | None, m: str | None) -> None:
        raise Cancel

    with pytest.raises(Cancel):
        nd.sample_neighbours(searcher(), np.zeros((10, 3)), portion=4, tick=tick)


# --- the time -------------------------------------------------------------------------------------


def test_search_time_per_node() -> None:
    """µs a node on 100×100×1 and 60×60×30 with 12 nodes (the figure of the report; a loose bound)."""
    nd.warm_nodes()
    for counts, r in (((100, 100, 1), 30.0), ((60, 60, 30), 12.0)):
        grid = nd.NodeGrid.of(counts, np.arange(int(np.prod(counts)), dtype=np.uint64))
        ell = se.Ellipsoid.of((90.0, 0.0, 0.0), (r, r * 0.7, r * 0.5))
        tpl = nd.spiral_template((1.0, 1.0, 1.0), ell, shape=counts)
        order = np.random.default_rng(1).permutation(grid.n)
        t0 = time.perf_counter()
        nodes, cnt = nd.replay_neighbours(grid, order, tpl, 12)
        us = (time.perf_counter() - t0) / grid.n * 1e6
        print(f"\nsearch of nodes {counts}: template {tpl.shape[0]}, {us:.2f} µs a node, mean found {cnt.mean():.2f}")
        assert us < 50.0
        assert cnt[-1] == 12 and cnt[0] == 0


def test_numba_comes_from_the_one_point() -> None:
    """The kernels use `bf_core.jit.njit` (the cache locator of the .exe), never `numba` directly."""
    import inspect
    import pathlib

    import bf_core.geostat.simulation as pkg

    for path in pathlib.Path(inspect.getfile(pkg)).parent.glob("*.py"):
        assert "from numba" not in path.read_text(encoding="utf-8"), path.name
