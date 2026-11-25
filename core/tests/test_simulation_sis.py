"""SIS (ТЗ 8.8.1; ADR 0024 §9; Д-23; Э18б, 18.33). Т18б-С1 (5 thresholds, 20 realizations, a fixed seed: the mean
share of the class ≤ t_k is within 0.03 of the declustered share of the samples, the indicator variogram of the
realizations on the first 5 lags is within ±15 % of the model, the samples at the nodes are reproduced exactly),
Т18б-С2 (categories: the shares ±0.03, Σ p_k = 1 to 1e−12 at every node), Т18б-С4 (the seed and 1, 2, 4, 8 threads: the
same bits); one realization equals a plain NumPy sequential simulation (the per-threshold and the median models, the
tails)."""

from __future__ import annotations

import dataclasses
import threading

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.geostat.estimate.indicator import order_relations, proportions
from bf_core.geostat.estimate.samples import RowSamples, prepare
from bf_core.geostat.estimate.search import Ellipsoid, Searcher
from bf_core.geostat.estimate.spec_parts import Pass
from bf_core.geostat.simulation import nodes as nd
from bf_core.geostat.simulation import sgs, sis
from bf_core.geostat.simulation.path import realization_path
from bf_core.geostat.simulation.sis_kernel import sis_steps
from bf_core.geostat.variogram.model import Model, Structure

QUANTILES = (0.1, 0.3, 0.5, 0.7, 0.9)


def make_samples(
    xyz: np.ndarray, z: np.ndarray, w: np.ndarray | None = None, dom: np.ndarray | None = None
) -> RowSamples:
    n = xyz.shape[0]
    d = np.zeros(n, np.int16) if dom is None else dom
    return prepare(xyz, z, None, w, np.zeros(n, np.int32), ["h"], d, np.arange(n, dtype=np.int64), np.zeros((n, 2)),
                   origin=np.zeros(3))  # fmt: skip


def field_samples(seed: int, ns: int, side: float = 100.0, cluster: int = 0, rng_len: float = 10.0):
    """Samples of a correlated field y ~ N(0, C) (spherical, 10 cells); z = exp(1 + 0.6·y); `cluster` of them are put in
    a corner (the declustering weights are the cell ones, 10 cells). Returns (samples, y)."""
    rng = np.random.default_rng(seed)
    xyz = np.zeros((ns, 3))
    xyz[:, :2] = rng.uniform(0.0, side, (ns, 2))
    if cluster:
        xyz[:cluster, :2] = rng.uniform(0.0, 0.35 * side, (cluster, 2))
    model = Model(0.1, (Structure("spherical", 0.9, rng_len, rng_len, rng_len),))
    c = model.cov(xyz[:, None, :] - xyz[None, :, :])
    y = np.linalg.cholesky(c + 1e-9 * np.eye(ns)) @ rng.standard_normal(ns)
    cell = (xyz[:, 0] // 10).astype(int) * 100 + (xyz[:, 1] // 10).astype(int)
    _, inv, cnt = np.unique(cell, return_inverse=True, return_counts=True)
    w = 1.0 / cnt[inv] if cluster else None
    s = make_samples(xyz, np.exp(1.0 + 0.6 * y), w)
    return s, y[s.row_id]  # `prepare` puts the samples in the canonical order: y follows it


def block(counts, share: float = 1.0, seed: int = 0) -> nd.NodeGrid:
    cells = counts[0] * counts[1] * counts[2]
    p = np.arange(cells, dtype=np.uint64)
    if share < 1.0:
        p = np.sort(np.random.default_rng(seed).permutation(p)[: int(cells * share)])
    return nd.NodeGrid.of(counts, p)


def indicator_model(p: float, nugget: float = 0.3, rng_len: float = 10.0, kind: str = "exponential") -> Model:
    """The model of an indicator of the share p: the sill p(1 − p), the nugget a part of it."""
    s = p * (1.0 - p)
    return Model(nugget * s, (Structure(kind, (1.0 - nugget) * s, rng_len, rng_len, rng_len),))


def ellipsoid(rng_len: float = 10.0) -> Ellipsoid:
    return Ellipsoid.of((0.0, 0.0, 0.0), (rng_len, rng_len, rng_len))


def continuous(s: RowSamples, **kw) -> sis.SisParams:
    th = tuple(float(v) for v in np.quantile(s.z, QUANTILES))
    p = proportions(s.z, s.w, th)
    models = tuple(indicator_model(float(pk)) for pk in p)
    kw.setdefault("steps", (1.0, 1.0, 1.0))
    return sis.SisParams(models, ellipsoid(), thresholds=th, **kw)


def variogram_ratio(ind: np.ndarray, model: Model, lags=(1, 2, 3, 4, 5)) -> list[float]:
    """The experimental indicator variogram of the realizations [n_real, nx, ny] (x and y together) / the model."""
    out = []
    for lag in lags:
        g = np.mean([0.5 * np.mean((r[lag:, :] - r[:-lag, :]) ** 2) for r in ind]
                    + [0.5 * np.mean((r[:, lag:] - r[:, :-lag]) ** 2) for r in ind])  # fmt: skip
        out.append(float(g / model.gamma(np.array([[lag, 0.0, 0.0]]))[0]))
    return out


# --- one realization = NumPy, step by step ----------------------------------------------------------------------


def reference(par: sis.SisParams, s: RowSamples, grid: nd.NodeGrid, centres: np.ndarray, r: int,
              cat: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:  # fmt: skip
    """The simulation in the plainest form: a Python loop over the path, the neighbourhood from the modules of 18.6 and
    18.30 (tested apart), the SK of every indicator by `numpy.linalg.solve` with `Model.cov`, the correction and the
    draw written out from the module text. Returns (values or classes [n], probabilities [n, K])."""
    scls, prop, _ = sis.sample_classes(par, s, cat)
    sr = Searcher(s, par.ell, (Pass(1.0, 0, par.n_max, 0, 0, 0, 0),))
    cidx, cn = nd.sample_neighbours(sr, centres)
    tpl = nd.spiral_template(par.steps, par.ell, sis._stack_models(par.models)[3], shape=grid.shape)
    path, rng = realization_path(par.seed, par.n_real, r, grid.n)
    u = rng.random(grid.n)
    kk = par.k
    cls = np.full(grid.n, -1)
    val = np.full(grid.n, np.nan)
    prob = np.full((grid.n, kk), np.nan)
    simulated = np.zeros(grid.n, dtype=bool)
    th = np.asarray(par.thresholds if par.thresholds is not None else [0.0])
    zd = s.z[s.dom == 0]
    zlo = par.zmin if par.zmin is not None else min(float(zd.min()), th[0])
    zhi = par.zmax if par.zmax is not None else max(float(zd.max()), th[-1])
    for step, node in enumerate(path):
        sidx = np.array([i for i in cidx[node, : cn[node]] if scls[i] >= 0], dtype=np.int64)
        near = nd.nearest_nodes(grid, simulated, tpl, int(node), par.nodes)
        pts = np.vstack((s.local[sidx], centres[near]))
        cd = np.concatenate((scls[sidx], cls[near]))
        f = prop.copy()
        if pts.shape[0]:
            for q in range(kk):
                if not 0.0 < prop[q] < 1.0:
                    continue
                m = par.models[q if len(par.models) > 1 else 0]
                lam = np.linalg.solve(m.cov(pts[:, None, :] - pts[None, :, :]), m.cov(pts - centres[node]))
                ind = (cd == q) if par.categorical else (cd <= q)
                f[q] = prop[q] + float(lam @ (ind.astype(float) - prop[q]))
        if par.categorical:
            f = np.clip(f, 0.0, 1.0)
            f = f / f.sum()
            k = int(np.searchsorted(np.cumsum(f), u[step], side="right"))
            cls[node] = min(k, kk - 1)
        else:
            order_relations(f)
            k = next((q for q in range(kk) if u[step] <= f[q]), kk)
            cls[node] = k
            if k == 0:
                share = u[step] / f[0] if f[0] > 0 else 0.0
                g = min(share, 1.0) ** (1.0 / par.ltpar if par.ltail == "power" else 1.0)
                val[node] = zlo + (th[0] - zlo) * g
            elif k < kk:
                d = f[k] - f[k - 1]
                t = (u[step] - f[k - 1]) / d if d > 0 else 0.5
                val[node] = th[k - 1] * (1 - t) + th[k] * t
            else:
                share = (u[step] - f[-1]) / (1.0 - f[-1])
                g = share ** (1.0 / par.utpar if par.utail == "power" else 1.0)
                val[node] = th[-1] * (1 - g) + zhi * g
        prob[node] = f
        simulated[node] = True
    return (cls if par.categorical else val), prob


def check_against_reference(par, s, grid, c, cat=None) -> None:
    res = sis.simulate(par, s, grid, c, cat=cat, keep_p=True)
    for r in range(par.n_real):
        want, prob = reference(par, s, grid, c, r, cat)
        assert np.allclose(res.p[r], prob, rtol=0, atol=1e-9), np.abs(res.p[r] - prob).max()
        if par.categorical:
            assert np.array_equal(res.real[r], np.asarray(par.codes, dtype=np.uint8)[want.astype(int)])
        else:
            assert np.allclose(res.real[r], want, rtol=0, atol=1e-9), np.abs(res.real[r] - want).max()


def small_world(seed: int = 3):
    rng = np.random.default_rng(seed)
    xyz = rng.uniform(0.0, 9.0, (40, 3)) * [1, 1, 0.4]
    z = rng.lognormal(1.0, 0.6, 40)
    s = make_samples(xyz, z, rng.uniform(0.5, 2.0, 40))
    grid = block((9, 8, 3), share=0.85, seed=2)
    return s, grid, sgs.grid_centres(grid, (1.0, 1.1, 1.3))


@pytest.mark.parametrize("median", [False, True])
def test_realization_equals_a_numpy_loop(median: bool) -> None:
    s, grid, c = small_world()
    th = tuple(float(v) for v in np.quantile(s.z, [0.15, 0.4, 0.6, 0.85]))
    p = proportions(s.z, s.w, th)
    models = (indicator_model(0.25, 0.3, 6.0, "spherical"),) if median else tuple(
        indicator_model(float(pk), 0.2, 5.0 + 2.0 * i, "spherical") for i, pk in enumerate(p))  # fmt: skip
    par = sis.SisParams(models, ellipsoid(8.0), (1.0, 1.1, 1.3), thresholds=th, seed=99, n_real=3, n_max=7, nodes=9)
    check_against_reference(par, s, grid, c)
    own = dataclasses.replace(par, zmin=0.2 * s.z.min(), zmax=3.0 * s.z.max(), ltail="power", ltpar=2.5, utail="power",
                              utpar=0.7)  # fmt: skip
    check_against_reference(own, s, grid, c)


@pytest.mark.parametrize("median", [False, True])
def test_categories_equal_a_numpy_loop(median: bool) -> None:
    s, grid, c = small_world(5)
    cat = np.digitize(s.z, np.quantile(s.z, [0.3, 0.7]))  # 0, 1, 2
    codes = (4, 9, 200)
    code_of = np.asarray(codes)[cat]
    models = (indicator_model(0.25, 0.3, 6.0, "spherical"),) if median else tuple(
        indicator_model(float(pk), 0.2, 6.0, "spherical") for pk in (0.3, 0.4, 0.3))  # fmt: skip
    par = sis.SisParams(models, ellipsoid(8.0), (1.0, 1.1, 1.3), codes=codes, seed=7, n_real=3, n_max=7, nodes=9)
    check_against_reference(par, s, grid, c, code_of)


def test_median_model_is_the_per_threshold_ones_when_they_are_scaled_copies() -> None:
    s, grid, c = small_world(8)
    th = tuple(float(v) for v in np.quantile(s.z, [0.2, 0.5, 0.8]))
    p = proportions(s.z, s.w, th)
    per = sis.SisParams(tuple(indicator_model(float(pk), 0.2, 6.0) for pk in p), ellipsoid(8.0), (1.0, 1.1, 1.3),
                        thresholds=th, seed=4, n_real=2, n_max=7, nodes=9)  # fmt: skip
    med = dataclasses.replace(per, models=(indicator_model(0.25, 0.2, 6.0),))
    a, b = sis.simulate(per, s, grid, c, keep_p=True), sis.simulate(med, s, grid, c, keep_p=True)
    assert a.p is not None and b.p is not None and np.allclose(a.p, b.p, rtol=0, atol=1e-10)


# --- Т18б-С1: the continuous variable by thresholds --------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_t18b_s1_shares_and_variograms(seed: int) -> None:
    s, _ = field_samples(seed, 320, cluster=120)
    assert s.w is not None
    par = continuous(s, seed=seed + 100, n_real=20)
    th = np.asarray(par.thresholds)
    declus = proportions(s.z, s.w, th)
    assert np.abs(declus - proportions(s.z, None, th)).max() > 0.02  # the declustering matters (clusters)
    grid = block((100, 100, 1))
    res = sis.simulate(par, s, grid, sgs.grid_centres(grid, par.steps), threads=4)
    assert res.real.shape == (20, 10000) and res.real.dtype == np.float64 and np.isfinite(res.real).all()
    assert np.allclose(res.prop, declus, rtol=0, atol=1e-15)
    got = np.array([(res.real <= t).mean() for t in th])
    assert np.abs(got - declus).max() <= 0.03, (got, declus)
    for k, t in enumerate(th):
        ratio = variogram_ratio((res.real <= t).reshape(20, 100, 100).astype(float), par.models[k])
        assert all(abs(x - 1.0) <= 0.15 for x in ratio), (k, ratio)
    assert res.real.min() >= s.z.min() * (1 - 1e-12) and res.real.max() <= s.z.max() * (1 + 1e-12)  # the default tails


def test_t18b_s1_samples_at_the_nodes_are_reproduced_exactly() -> None:
    grid = block((12, 12, 1))
    c = sgs.grid_centres(grid, (1.0, 1.0, 1.0))
    at = np.array([5, 30, 31, 77, 100, 143])
    rng = np.random.default_rng(8)
    away = rng.uniform(0.0, 12.0, (25, 3)) * [1, 1, 0] + [0, 0, 0.5]
    z = rng.lognormal(1.0, 0.7, 31)
    s = make_samples(np.vstack((c[at], away)), z)
    par = continuous(s, seed=5, n_real=4, nodes=12)
    res = sis.simulate(par, s, grid, c, keep_p=True)
    where = np.array([int(np.argmin(np.abs(s.local - c[a]).sum(axis=1))) for a in at])
    assert np.array_equal(res.real[:, at], np.tile(s.z[where], (4, 1)))  # exactly, bit for bit
    assert (res.counts[:, sgs.C_ON_SAMPLE] == len(at)).all() and (res.counts[:, sgs.C_DEGENERATE] == 0).all()
    assert res.p is not None
    th = np.asarray(par.thresholds)
    assert np.array_equal(
        res.p[0, at], (s.z[where][:, None] <= th[None, :]).astype(float)
    )  # the indicators of the sample
    rest = np.setdiff1d(np.arange(grid.n), at)
    assert np.isfinite(res.real).all() and not np.array_equal(res.real[0, rest], res.real[1, rest])


# --- Т18б-С2: the categories ---------------------------------------------------------------------------------------


def category_world(seed: int):
    s, y = field_samples(seed, 320, cluster=120)
    cut = (float(np.quantile(y, 0.3)), float(np.quantile(y, 0.7)))
    codes = (3, 5, 9)
    cat = np.asarray(codes)[np.digitize(y, cut)]
    return s, cat, codes


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("median", [False, True])
def test_t18b_s2_categories(seed: int, median: bool) -> None:
    s, cat, codes = category_world(seed)
    assert s.w is not None
    ind = np.stack([(cat == c).astype(float) for c in codes], axis=1)
    want = np.array([float(np.sum(s.w * ind[:, k]) / np.sum(s.w)) for k in range(3)])  # the declustered shares
    models = (indicator_model(0.25),) if median else tuple(indicator_model(float(p)) for p in want)
    par = sis.SisParams(models, ellipsoid(), (1.0, 1.0, 1.0), codes=codes, seed=seed + 50, n_real=20)
    grid = block((100, 100, 1))
    res = sis.simulate(par, s, grid, sgs.grid_centres(grid, par.steps), cat=cat, threads=4)
    assert res.real.dtype == np.uint8 and set(np.unique(res.real)) <= set(codes)
    assert np.allclose(res.prop, want, rtol=0, atol=1e-12) and abs(res.prop.sum() - 1.0) < 1e-12
    got = np.array([(res.real == c).mean() for c in codes])
    assert np.abs(got - want).max() <= 0.03, (got, want)
    if not median:  # the structure of every category, ±15 % of its model on the first 5 lags
        for k, c in enumerate(codes):
            ratio = variogram_ratio((res.real == c).reshape(20, 100, 100).astype(float), models[k])
            assert all(abs(x - 1.0) <= 0.15 for x in ratio), (k, ratio)


@pytest.mark.parametrize("median", [False, True])
def test_t18b_s2_probabilities_sum_to_one(median: bool) -> None:
    s, cat, codes = category_world(4)
    models = (indicator_model(0.25),) if median else tuple(indicator_model(p) for p in (0.3, 0.4, 0.3))
    par = sis.SisParams(models, ellipsoid(), (1.0, 1.0, 1.0), codes=codes, seed=3, n_real=3)
    grid = block((30, 30, 1), share=0.9, seed=1)
    res = sis.simulate(par, s, grid, sgs.grid_centres(grid, par.steps), cat=cat, keep_p=True)
    assert res.p is not None and res.p.shape == (3, grid.n, 3)
    assert np.abs(res.p.sum(axis=2) - 1.0).max() <= 1e-12 and res.p.min() >= 0.0 and res.p.max() <= 1.0


def test_categories_a_sample_at_a_node_and_foreign_codes() -> None:
    grid = block((10, 10, 1))
    c = sgs.grid_centres(grid, (1.0, 1.0, 1.0))
    at = np.array([11, 45, 78])
    rng = np.random.default_rng(2)
    xyz = np.vstack((c[at], rng.uniform(0.0, 10.0, (30, 3)) * [1, 1, 0] + [0, 0, 0.5]))
    s = make_samples(xyz, rng.uniform(1.0, 2.0, 33))
    cat = np.concatenate(([9, 3, 5], rng.choice([3, 5, 9, 77], 30)))[s.row_id]  # 77: not a category, no datum
    par = sis.SisParams((indicator_model(0.3),), ellipsoid(5.0), (1.0, 1.0, 1.0), codes=(3, 5, 9), seed=1, n_real=4)
    res = sis.simulate(par, s, grid, c, cat=cat)
    assert (res.real[:, at] == np.array([9, 3, 5])).all()
    assert any("вне списка" in line for line in res.notes)
    assert (res.counts[:, sgs.C_ON_SAMPLE] == 3).all() and set(np.unique(res.real)) <= {3, 5, 9}
    no = dataclasses.replace(s, dom=np.ones(s.n, np.int16))  # none in D
    with pytest.raises(ApiError) as ei:
        sis.simulate(par, no, grid, c, cat=cat)
    assert ei.value.code == "no_samples" and ei.value.status == 422
    all77 = np.full(s.n, 77)
    with pytest.raises(ApiError) as ei2:
        sis.simulate(par, s, grid, c, cat=all77)
    assert ei2.value.code == "no_samples"
    with pytest.raises(ApiError) as ei3:
        sis.simulate(par, s, grid, c)  # categories without the codes of the samples
    assert ei3.value.extra["param"] == "cat"


# --- Т18б-С4: the seed, the threads ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small():
    s, y = field_samples(4, 80, side=30.0, cluster=30)
    grid = block((30, 30, 1), share=0.9, seed=1)
    c = sgs.grid_centres(grid, (1.0, 1.0, 1.0))
    par = continuous(s, seed=2024, n_real=8, nodes=10)
    cat = np.asarray((3, 5, 9))[np.digitize(y, np.quantile(y, [0.3, 0.7]))]
    cpar = sis.SisParams((indicator_model(0.3), indicator_model(0.4), indicator_model(0.3)), ellipsoid(),
                         (1.0, 1.0, 1.0), codes=(3, 5, 9), seed=2024, n_real=8)  # fmt: skip
    return par, cpar, s, cat, grid, c


@pytest.mark.parametrize("kind", ["thresholds", "codes"])
def test_t18b_s4_seed_repeats_bit_for_bit(small, kind: str) -> None:
    par, cpar, s, cat, grid, c = small
    p = par if kind == "thresholds" else cpar
    a = sis.simulate(p, s, grid, c, cat=cat, keep_p=True)
    b = sis.simulate(p, s, grid, c, cat=cat, keep_p=True)
    assert np.array_equal(a.real, b.real) and np.array_equal(a.counts, b.counts) and np.array_equal(a.p, b.p)
    other = sis.simulate(dataclasses.replace(p, seed=2025), s, grid, c, cat=cat)
    assert not np.array_equal(a.real, other.real) and not np.array_equal(a.real[0], a.real[1])


@pytest.mark.parametrize("kind", ["thresholds", "codes"])
@pytest.mark.parametrize("nodes", [10, 0])
def test_t18b_s4_threads_give_the_same_bits(small, kind: str, nodes: int) -> None:
    par, cpar, s, cat, grid, c = small
    p = dataclasses.replace(par if kind == "thresholds" else cpar, nodes=nodes)
    base = sis.simulate(p, s, grid, c, cat=cat, threads=1, keep_p=True)
    for threads in (2, 4, 8, 16):
        got = sis.simulate(p, s, grid, c, cat=cat, threads=threads, keep_p=True)
        assert np.array_equal(got.real, base.real) and np.array_equal(got.p, base.p), threads
        assert np.array_equal(got.counts, base.counts)


def test_a_realization_does_not_depend_on_the_others(small) -> None:
    par, _, s, _, grid, c = small
    eight = sis.simulate(par, s, grid, c)
    three = sis.simulate(dataclasses.replace(par, n_real=3), s, grid, c)
    assert np.array_equal(eight.real[:3], three.real)


# --- the tails, the constant indicators, the gaps ------------------------------------------------------------------


def test_tails_and_the_values_stay_in_their_classes() -> None:
    s, _ = field_samples(6, 200, side=40.0)
    grid = block((40, 40, 1))
    c = sgs.grid_centres(grid, (1.0, 1.0, 1.0))
    base = continuous(s, seed=1, n_real=4)
    th = np.asarray(base.thresholds)
    zmin, zmax = 0.5 * s.z.min(), 3.0 * s.z.max()
    lim = sis.simulate(dataclasses.replace(base, zmin=zmin, zmax=zmax), s, grid, c)
    assert lim.real.min() >= zmin and lim.real.max() <= zmax
    assert (lim.real < s.z.min()).any() and (lim.real > s.z.max()).any()  # the tails reach beyond the data
    none = sis.simulate(dataclasses.replace(base, ltail=None, utail=None), s, grid, c)
    assert none.real.min() == th[0] and none.real.max() == th[-1]  # no tail: the end threshold itself
    assert (none.real == th[0]).any() and (none.real == th[-1]).any()
    hyp = sis.simulate(dataclasses.replace(base, utail="hyperbolic", utpar=1.5), s, grid, c)
    assert hyp.real.max() > s.z.max() and np.isfinite(hyp.real).all()  # no upper limit
    for res in (lim, none, hyp):
        mid = (res.real > th[0]) & (res.real < th[-1])
        assert mid.any()


def test_a_constant_indicator_is_not_kriged() -> None:
    """A threshold below every sample (p = 0) and one above (p = 1): F_k = p_k at every node, no value beyond them."""
    s, _ = field_samples(2, 120, side=30.0)
    lo, hi = float(s.z.min()) * 0.5, float(s.z.max()) * 2.0
    mid = tuple(float(v) for v in np.quantile(s.z, [0.3, 0.7]))
    th = (lo, *mid, hi)
    p = proportions(s.z, None, th)
    assert p[0] == 0.0 and p[-1] == 1.0
    models = tuple(indicator_model(float(v)) for v in p[1:-1])
    par = sis.SisParams((indicator_model(0.4), *models, indicator_model(0.4)), ellipsoid(), (1.0, 1.0, 1.0),
                        thresholds=th, seed=3, n_real=3, ltail="linear", zmin=lo)  # fmt: skip
    grid = block((30, 30, 1))
    res = sis.simulate(par, s, grid, sgs.grid_centres(grid, par.steps), keep_p=True)
    assert res.p is not None and (res.p[..., 0] == 0.0).all() and (res.p[..., -1] == 1.0).all()
    assert np.isfinite(res.real).all() and res.real.min() > lo and res.real.max() <= hi
    assert res.counts[:, sgs.C_DEGENERATE].sum() == 0


def test_no_datum_is_the_marginal_not_a_zero() -> None:
    """The ellipsoid is smaller than a cell: no sample, no node in a neighbourhood — a draw from the proportions p."""
    rng = np.random.default_rng(1)
    z = rng.lognormal(0.5, 0.6, 200)
    s = make_samples(
        np.column_stack((rng.uniform(1000.0, 1100.0, 200), rng.uniform(1000.0, 1100.0, 200), np.zeros(200))), z
    )
    th = tuple(float(v) for v in np.quantile(z, QUANTILES))
    grid = block((60, 60, 1))
    par = sis.SisParams((indicator_model(0.4, 0.1, 0.4),), ellipsoid(0.4), (1.0, 1.0, 1.0), thresholds=th, seed=1,
                        n_real=5)  # fmt: skip
    res = sis.simulate(par, s, grid, sgs.grid_centres(grid, par.steps))
    assert (res.counts[:, sgs.C_FREE] == grid.n).all() and np.isfinite(res.real).all() and not (res.real == 0.0).any()
    got = np.array([(res.real <= t).mean() for t in th])
    assert np.abs(got - np.asarray(QUANTILES)).max() < 0.02  # the shares of the samples


def test_gaps_are_never_zeros_or_classes() -> None:
    s, grid, c = small_world(2)
    foreign = dataclasses.replace(s, dom=np.where(np.arange(s.n) % 5 == 0, 1, 0).astype(np.int16),
                                  z=np.where(np.arange(s.n) % 5 == 0, np.nan, s.z))  # fmt: skip
    par = continuous(s, seed=1, n_real=3, nodes=6)
    res = sis.simulate(par, foreign, grid, c)  # a sample out of D with no value is no datum
    assert np.isfinite(res.real).all()
    scls, prop, _ = sis.sample_classes(par, foreign)
    assert (scls[::5] == -1).all() and (scls[np.arange(s.n) % 5 != 0] >= 0).all() and np.isfinite(prop).all()
    bad_d = dataclasses.replace(s, z=np.where(np.arange(s.n) == 3, np.nan, s.z))
    with pytest.raises(ApiError) as ei:
        sis.simulate(par, bad_d, grid, c)
    assert ei.value.code == "no_samples" and ei.value.status == 422


def test_degenerate_model_stays_finite() -> None:
    """The gaussian covariance without a nugget on a fine grid: near-singular systems; the threshold takes its share p
    (counted), never a NaN or a 0, and the run does not depend on the threads."""
    rng = np.random.default_rng(1)
    s = make_samples(rng.uniform(0.0, 1.25, (40, 3)) * [1, 1, 0], rng.lognormal(0.0, 0.5, 40))
    th = tuple(float(v) for v in np.quantile(s.z, [0.25, 0.5, 0.75]))
    m = Model(0.0, (Structure("gaussian", 0.25, 8.0, 8.0, 8.0),))
    par = sis.SisParams((m,), ellipsoid(8.0), (0.05, 0.05, 0.05), thresholds=th, seed=9, n_real=4)
    grid = block((25, 25, 1))
    c = sgs.grid_centres(grid, par.steps)
    a, b = sis.simulate(par, s, grid, c, threads=1), sis.simulate(par, s, grid, c, threads=4)
    assert np.isfinite(a.real).all() and np.array_equal(a.real, b.real) and np.array_equal(a.counts, b.counts)
    assert a.counts[:, sgs.C_DEGENERATE].sum() > 0 and any("Вырожденных" in line for line in a.notes)


# --- the cancel, the signatures, the checks ------------------------------------------------------------------------


class Stop(Exception):
    pass


@pytest.mark.parametrize("threads", [1, 4])
def test_tick_cancels(small, threads: int) -> None:
    par, _, s, _, grid, c = small
    seen: list[tuple[float | None, str | None]] = []

    def tick(p: float | None, label: str | None) -> None:
        seen.append((p, label))
        if label and label.startswith("Реализации") and p is not None and p > 0.3:
            raise Stop

    before = threading.active_count()
    with pytest.raises(Stop):
        sis.simulate(par, s, grid, c, threads=threads, tick=tick)
    assert threading.active_count() <= before + 1
    assert seen[-1][1] is not None and seen[-1][1].startswith("Реализации")


def test_one_realization_is_cancelled_and_reported_inside(small, monkeypatch: pytest.MonkeyPatch) -> None:
    par, _, s, _, grid, c = small
    monkeypatch.setattr(sis, "STEP", 100)  # a portion of the path: the tick goes between portions
    monkeypatch.setattr(sis, "POLL", 0.0)
    one = dataclasses.replace(par, n_real=1)
    seen: list[float] = []

    def tick(p: float | None, label: str | None) -> None:
        if p is not None and label and label.startswith("Реализации"):
            seen.append(p)
            if 0.0 < p < 1.0 and len(seen) >= 3:
                raise Stop

    with pytest.raises(Stop):
        sis.simulate(one, s, grid, c, threads=1, tick=tick)
    assert len(seen) >= 3 and seen == sorted(seen) and all(0.0 < p < 1.0 for p in seen)  # inside the realization


def test_tick_reports_every_realization(small) -> None:
    par, _, s, _, grid, c = small
    labels: list[str] = []
    prog: list[float] = []

    def tick(p: float | None, label: str | None) -> None:
        if label and label.startswith("Реализации"):
            labels.append(label)
            prog.append(float(p or 0.0))

    sis.simulate(par, s, grid, c, tick=tick)
    assert labels[-1] == "Реализации 8 / 8" and prog == sorted(prog) and prog[-1] == pytest.approx(1.0)


def test_read_only_inputs_add_no_signature(small) -> None:
    par, cpar, s, cat, grid, c = small
    sis.simulate(par, s, grid, c)
    sis.simulate(cpar, s, grid, c, cat=cat)
    before = len(sis_steps.signatures)
    ro = [c, grid.index, grid.cell, s.local, s.z, cat]
    for a in ro:
        a.setflags(write=False)
    got = sis.simulate(par, s, grid, c)
    got_c = sis.simulate(cpar, s, grid, c, cat=cat)
    assert len(sis_steps.signatures) == before == 1
    for a in ro:
        a.setflags(write=True)
    assert np.array_equal(got.real, sis.simulate(par, s, grid, c).real)
    assert np.array_equal(got_c.real, sis.simulate(cpar, s, grid, c, cat=cat).real)
    sis.warm_sis()
    assert len(sis_steps.signatures) == 1


def test_bad_input_is_refused_in_russian() -> None:
    s, grid, c = small_world()
    ok = continuous(s, steps=(1.0, 1.1, 1.3))
    th = ok.thresholds
    cases = (({"seed": -1}, "seed"), ({"n_real": 0}, "n_real"), ({"n_real": 101}, "n_real"), ({"nodes": 65}, "nodes"),
             ({"n_max": 0}, "n_max"), ({"n_max": 129}, "n_max"), ({"thresholds": th[::-1]}, "thresholds"),
             ({"thresholds": (1.0, float("nan"))}, "thresholds"), ({"thresholds": tuple(range(21))}, "thresholds"),
             ({"codes": (1, 2)}, "thresholds"), ({"models": ok.models[:2]}, "models"), ({"models": ()}, "models"),
             ({"utail": "power"}, "utpar"), ({"ltail": "cubic"}, "ltail"), ({"zmin": th[0] + 1.0}, "zmin"),
             ({"zmax": th[-1] - 1.0}, "zmax"), ({"utail": "hyperbolic", "utpar": 1.5, "thresholds": (-3.0, -2.0)},
                                                  "utail"))  # fmt: skip
    for kw, name in cases:
        with pytest.raises(ApiError) as ei:
            sis.simulate(dataclasses.replace(ok, **kw), s, grid, c)
        assert ei.value.status == 422 and ei.value.extra["param"] == name and ei.value.hint, kw
    cat_ok = sis.SisParams((indicator_model(0.3),), ok.ell, ok.steps, codes=(1, 2))
    for codes in ((1,), (1, 1), (1, 300), (-1, 2), tuple(range(21))):
        with pytest.raises(ApiError) as ei:
            sis.simulate(dataclasses.replace(cat_ok, codes=codes), s, grid, c, cat=np.ones(s.n, int))
        assert ei.value.extra["param"] == "codes", codes
    nan_c = c.copy()
    nan_c[3, 1] = np.nan
    with pytest.raises(ApiError) as ei2:
        sis.simulate(ok, s, grid, nan_c)
    assert ei2.value.extra["param"] == "centres"
    empty = nd.NodeGrid.of((3, 3, 1), np.zeros(0, dtype=np.uint64))
    res = sis.simulate(ok, s, empty, np.zeros((0, 3)))
    assert res.real.shape == (20, 0)
