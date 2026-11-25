"""SGS (ТЗ 8.8.1; ADR 0024 §9; Д-23; Э18б, 18.31): one realization equals a plain NumPy sequential simulation (the same
path, the same draws, `numpy.linalg.solve` of the system of the neighbourhood), a seed repeats bit for bit and does not
depend on the number of threads or of realizations (Т8.8-13), the statistics of 100×100×1 × 20 (Т8.8-14: the mean
Kolmogorov–Smirnov distance ≤ 0.05, the variogram of the realizations in Y within 15 % of the model on 3–5 lags),
the samples at the nodes are reproduced, a gap is never a zero, the cancel, read-only inputs add no signature."""

from __future__ import annotations

import dataclasses
import threading

import numpy as np
import pytest
from scipy.stats import norm

from bf_core.api.errors import ApiError
from bf_core.geostat.estimate.samples import RowSamples, prepare
from bf_core.geostat.estimate.search import Ellipsoid, Searcher
from bf_core.geostat.estimate.spec_parts import Pass
from bf_core.geostat.simulation import nodes as nd
from bf_core.geostat.simulation import sgs
from bf_core.geostat.simulation.path import realization_path
from bf_core.geostat.variogram.model import Model, Structure
from bf_core.stats.transforms import backtr, nscore


def make_samples(xyz: np.ndarray, z: np.ndarray, w: np.ndarray | None = None) -> RowSamples:
    n = xyz.shape[0]
    return prepare(xyz, z, None, w, np.zeros(n, np.int32), ["h"], np.zeros(n, np.int16), np.arange(n, dtype=np.int64),
                   np.zeros((n, 2)), origin=np.zeros(3))  # fmt: skip


def lognormal_samples(seed: int, ns: int, model: Model, side: float = 100.0) -> tuple[RowSamples, np.ndarray]:
    """Samples of a correlated field: y ~ N(0, C) by the model, z = exp(1 + 0.6·y) — skewed, not normal."""
    rng = np.random.default_rng(seed)
    xyz = np.zeros((ns, 3))
    xyz[:, :2] = rng.uniform(0.0, side, (ns, 2))
    c = model.cov(xyz[:, None, :] - xyz[None, :, :])
    y = np.linalg.cholesky(c + 1e-9 * np.eye(ns)) @ rng.standard_normal(ns)
    return make_samples(xyz, np.exp(1.0 + 0.6 * y)), y


def block(counts, share: float = 1.0, seed: int = 0) -> nd.NodeGrid:
    cells = counts[0] * counts[1] * counts[2]
    p = np.arange(cells, dtype=np.uint64)
    if share < 1.0:
        p = np.sort(np.random.default_rng(seed).permutation(p)[: int(cells * share)])
    return nd.NodeGrid.of(counts, p)


def iso(rng_len: float, nugget: float = 0.1) -> tuple[Model, Ellipsoid]:
    return (Model(nugget, (Structure("spherical", 1.0 - nugget, rng_len, rng_len, rng_len),)),
            Ellipsoid.of((0.0, 0.0, 0.0), (rng_len, rng_len, rng_len)))  # fmt: skip


def params(model: Model, ell: Ellipsoid, **kw) -> sgs.SgsParams:
    kw.setdefault("steps", (1.0, 1.0, 1.0))
    return sgs.SgsParams(model, ell, **kw)


# --- one realization = NumPy, step by step ----------------------------------------------------------------------


def reference_y(par: sgs.SgsParams, s: RowSamples, grid: nd.NodeGrid, centres: np.ndarray, r: int) -> np.ndarray:
    """The simulation in the plainest form: a Python loop over the path, the neighbourhood from the modules of 18.6 and
    18.30 (tested apart), the system by `numpy.linalg.solve` with `Model.cov`; the draws as the module takes them."""
    sy, _ = sgs.sample_scores(s, par)
    sr = Searcher(s, par.ell, (Pass(1.0, 0, par.n_max, 0, 0, 0, 0),))
    cidx, cn = nd.sample_neighbours(sr, centres)
    from bf_core.geostat.estimate.cov import cov_model

    tpl = nd.spiral_template(par.steps, par.ell, cov_model(par.model), shape=grid.shape)
    path, rng = realization_path(par.seed, par.n_real, r, grid.n)
    draws = rng.standard_normal(grid.n)
    y = np.full(grid.n, np.nan)
    simulated = np.zeros(grid.n, dtype=bool)
    sill = par.model.sill
    for step, node in enumerate(path):
        sidx = cidx[node, : cn[node]].astype(np.int64)
        near = nd.nearest_nodes(grid, simulated, tpl, int(node), par.nodes)
        pts = np.vstack((s.local[sidx], centres[near]))
        val = np.concatenate((sy[sidx], y[near]))
        if pts.shape[0] == 0:
            y[node] = draws[step]
        else:
            kmat = par.model.cov(pts[:, None, :] - pts[None, :, :])
            k0 = par.model.cov(pts - centres[node])
            lam = np.linalg.solve(kmat, k0)
            sigma = np.sqrt(max(sill - float(lam @ k0), 0.0))
            y[node] = float(lam @ val) + sigma * draws[step]
        simulated[node] = True
    return y


def test_realization_equals_a_numpy_loop() -> None:
    model = Model(0.15, (Structure("spherical", 0.55, 9.0, 6.0, 4.0, 30.0, 10.0, 5.0),
                         Structure("exponential", 0.3, 14.0, 10.0, 7.0, 30.0, 10.0, 5.0)))  # fmt: skip
    ell = Ellipsoid.of((30.0, 10.0, 5.0), (14.0, 10.0, 7.0))
    grid = block((7, 6, 4), share=0.85, seed=2)
    rng = np.random.default_rng(5)
    s = make_samples(rng.uniform(0.0, 8.0, (30, 3)), rng.lognormal(1.0, 0.5, 30))
    par = params(model, ell, seed=99, n_real=3, n_max=7, nodes=9, steps=(1.0, 1.1, 1.3))
    centres = sgs.grid_centres(grid, par.steps)
    res = sgs.simulate(par, s, grid, centres, keep_y=True)
    assert res.y is not None and res.counts.sum() == 0 + int(res.counts[:, sgs.C_FREE].sum())
    for r in range(par.n_real):
        want = reference_y(par, s, grid, centres, r)
        assert np.allclose(res.y[r], want, rtol=0, atol=1e-8), np.abs(res.y[r] - want).max()
        _, tr = nscore(s.z)
        assert np.allclose(res.real[r], backtr(res.y[r], tr), rtol=1e-12)


# --- Т8.8-13: the seed, the threads --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small() -> tuple[sgs.SgsParams, RowSamples, nd.NodeGrid, np.ndarray]:
    model, ell = iso(6.0)
    s, _ = lognormal_samples(4, 60, model, side=30.0)
    grid = block((30, 30, 1), share=0.9, seed=1)
    par = params(model, ell, seed=2024, n_real=8)
    return par, s, grid, sgs.grid_centres(grid, par.steps)


def test_t8_8_13_seed_repeats_bit_for_bit(small) -> None:
    par, s, grid, c = small
    a, b = sgs.simulate(par, s, grid, c), sgs.simulate(par, s, grid, c)
    assert a.real.shape == (8, grid.n) and a.real.dtype == np.float64 and np.isfinite(a.real).all()
    assert np.array_equal(a.real, b.real) and np.array_equal(a.counts, b.counts)
    other = sgs.simulate(dataclasses.replace(par, seed=2025), s, grid, c)
    assert not np.array_equal(a.real, other.real)
    assert not np.array_equal(a.real[0], a.real[1])  # the realizations differ


@pytest.mark.parametrize("nodes", [12, 0])
def test_t8_8_13_threads_give_the_same_bits(small, nodes: int) -> None:
    par, s, grid, c = small
    par = dataclasses.replace(par, nodes=nodes)
    base = sgs.simulate(par, s, grid, c, threads=1, keep_y=True)
    for threads in (2, 4, 8, 16):
        got = sgs.simulate(par, s, grid, c, threads=threads, keep_y=True)
        assert np.array_equal(got.real, base.real) and np.array_equal(got.y, base.y), threads
        assert np.array_equal(got.counts, base.counts)


def test_a_realization_does_not_depend_on_the_others(small) -> None:
    """The stream of the realization r is (seed, r): the same with 3 or with 8 realizations asked for."""
    par, s, grid, c = small
    eight = sgs.simulate(par, s, grid, c)
    three = sgs.simulate(dataclasses.replace(par, n_real=3), s, grid, c)
    # the stream is the child r of SeedSequence(seed): spawn(3)[r] and spawn(8)[r] are one state
    assert np.array_equal(eight.real[:3], three.real)


# --- Т8.8-14: the statistics -----------------------------------------------------------------------------------


def ks_distance(values: np.ndarray, cdf) -> float:
    a = np.sort(values.ravel())
    n = a.size
    f = cdf(a)
    return float(max(np.max(np.arange(1, n + 1) / n - f), np.max(f - np.arange(n) / n)))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_t8_8_14_distribution_and_variogram(seed: int) -> None:
    model, ell = iso(10.0, 0.1)
    s, _ = lognormal_samples(seed, 300, model)
    grid = block((100, 100, 1))
    par = params(model, ell, seed=seed + 100, n_real=20)
    res = sgs.simulate(par, s, grid, sgs.grid_centres(grid, par.steps), threads=4, keep_y=True)
    assert res.y is not None
    zs = np.sort(s.z)
    ecdf = (np.arange(zs.size) + 0.5) / zs.size
    ks_z = [ks_distance(r, lambda x: np.interp(x, zs, ecdf)) for r in res.real]  # the samples' distribution
    ks_y = [ks_distance(r, norm.cdf) for r in res.y]  # the normal scores
    assert np.mean(ks_z) <= 0.05 and np.mean(ks_y) <= 0.05, (np.mean(ks_z), np.mean(ks_y))
    assert abs(np.mean(res.y)) < 0.1 and abs(np.var(res.y) - 1.0) < 0.1
    y = res.y.reshape(20, 100, 100)
    for lag in (1, 3, 5, 7):  # 3–5 lags of the 10-cell range: x and y together
        g = np.mean([0.5 * np.mean((r[lag:, :] - r[:-lag, :]) ** 2) for r in y]
                    + [0.5 * np.mean((r[:, lag:] - r[:, :-lag]) ** 2) for r in y])  # fmt: skip
        want = float(model.gamma(np.array([[lag, 0.0, 0.0]]))[0])
        assert abs(g / want - 1.0) <= 0.15, (lag, g, want)


# --- the samples at the nodes, the gaps ------------------------------------------------------------------------


def test_a_sample_at_a_node_is_reproduced_exactly() -> None:
    model, ell = iso(5.0, 0.2)
    grid = block((12, 12, 1))
    par = params(model, ell, seed=5, n_real=4)
    c = sgs.grid_centres(grid, par.steps)
    at = np.array([5, 30, 31, 77, 100, 143])  # nodes with a sample exactly at the centre (31 next to 30)
    rng = np.random.default_rng(8)
    away = rng.uniform(0.0, 12.0, (25, 3)) * [1, 1, 0] + [0, 0, 0.5]
    z = rng.lognormal(1.0, 0.7, 31)
    s = make_samples(np.vstack((c[at], away)), z)
    res = sgs.simulate(par, s, grid, c, keep_y=True)
    where = np.array([int(np.argmin(np.abs(s.local - c[a]).sum(axis=1))) for a in at])
    sy, tr = sgs.sample_scores(s, par)
    assert res.y is not None
    assert np.array_equal(res.y[:, at], np.tile(sy[where], (4, 1)))  # the score, bit for bit
    assert np.allclose(res.real[:, at], np.tile(s.z[where], (4, 1)), rtol=1e-12)
    assert (res.counts[:, sgs.C_ON_SAMPLE] == len(at)).all()
    assert (res.counts[:, sgs.C_DEGENERATE] == 0).all()  # the node of a sample is not a datum twice
    rest = np.setdiff1d(np.arange(grid.n), at)
    assert np.isfinite(res.real).all() and not np.array_equal(res.real[0, rest], res.real[1, rest])


def test_no_datum_is_a_free_draw_not_a_zero() -> None:
    """The ellipsoid is smaller than a cell: no sample, no node in a neighbourhood — N(0, 1) of the stream."""
    model, ell = iso(0.4, 0.0)
    grid = block((60, 60, 1))
    s = make_samples(np.array([[1000.0, 1000.0, 0.0], [1001.0, 1000.0, 0.0], [1000.0, 1001.0, 0.0]]),
                     np.array([1.0, 2.0, 4.0]))  # fmt: skip
    par = params(model, ell, seed=1, n_real=5)
    res = sgs.simulate(par, s, grid, sgs.grid_centres(grid, par.steps), keep_y=True)
    assert res.y is not None and (res.counts[:, sgs.C_FREE] == grid.n).all()
    assert np.isfinite(res.real).all() and not (res.real == 0.0).any() and not (res.y == 0.0).any()
    assert abs(res.y.mean()) < 0.03 and abs(res.y.std() - 1.0) < 0.03  # N(0, 1)
    assert res.real.min() >= s.z.min() and res.real.max() <= s.z.max() * 1.0000001  # the linear tails end at the data


def test_samples_without_a_value_or_weight_are_not_zeros() -> None:
    model, ell = iso(5.0)
    rng = np.random.default_rng(3)
    xyz = rng.uniform(0.0, 10.0, (40, 3)) * [1, 1, 0]
    z = rng.lognormal(0.5, 0.6, 40)
    w = rng.uniform(0.5, 2.0, 40)
    par = params(model, ell, seed=3, n_real=2)
    base = make_samples(xyz, z, w)
    assert base.w is not None
    holey = dataclasses.replace(base, w=np.where(np.arange(base.n) % 4 == 0, np.nan, base.w))
    zero = dataclasses.replace(base, w=np.where(np.arange(base.n) % 4 == 0, 0.0, base.w))
    ya, _ = sgs.sample_scores(holey, par)
    yb, tb = sgs.sample_scores(zero, par)
    assert np.array_equal(ya, yb) and np.isfinite(ya).all()  # NaN weight = no weight: left out of the table
    assert int(tb.values.size) == base.n - int((np.arange(base.n) % 4 == 0).sum())
    assert np.all(np.diff(tb.scores) > 0.0)
    nan_all = dataclasses.replace(base, w=np.full(base.n, np.nan))
    yn, tn = sgs.sample_scores(nan_all, par)
    assert tn.values.size == base.n and np.isfinite(yn).all()  # no weight at all: equal weights
    bad = dataclasses.replace(base, z=np.where(np.arange(base.n) == 7, np.nan, base.z))
    with pytest.raises(ApiError) as ei:
        sgs.sample_scores(bad, par)
    assert ei.value.code == "no_samples" and ei.value.status == 422
    none = dataclasses.replace(base, dom=np.ones(base.n, np.int16))  # no sample of D
    with pytest.raises(ApiError) as ei2:
        sgs.sample_scores(none, par)
    assert ei2.value.code == "no_samples"


def test_a_gaussian_model_without_nugget_stays_finite() -> None:
    """The gaussian covariance without a nugget on a fine grid: the systems are near singular; a degenerate system is a
    free draw (counted), never a NaN or a zero, and the run does not depend on the threads."""
    model = Model(0.0, (Structure("gaussian", 1.0, 8.0, 8.0, 8.0),))
    ell = Ellipsoid.of((0.0, 0.0, 0.0), (8.0, 8.0, 8.0))
    grid = block((25, 25, 1))
    rng = np.random.default_rng(1)
    s = make_samples(rng.uniform(0.0, 1.25, (40, 3)) * [1, 1, 0], rng.lognormal(0.0, 0.5, 40))
    par = params(model, ell, seed=9, n_real=4, steps=(0.05, 0.05, 0.05))
    a = sgs.simulate(par, s, grid, sgs.grid_centres(grid, par.steps), threads=1)
    b = sgs.simulate(par, s, grid, sgs.grid_centres(grid, par.steps), threads=4)
    assert np.isfinite(a.real).all() and np.array_equal(a.real, b.real) and np.array_equal(a.counts, b.counts)
    assert a.counts[:, sgs.C_DEGENERATE].sum() > 0
    assert any("Вырожденных" in line for line in a.notes)


def test_tails_and_declustering_weights_reach_the_back_transform() -> None:
    model, ell = iso(6.0)
    s, _ = lognormal_samples(6, 80, model, side=20.0)
    grid = block((20, 20, 1))
    par = params(model, ell, seed=1, n_real=2, utail="power", utpar=2.0, ltail=None)
    c = sgs.grid_centres(grid, par.steps)
    res = sgs.simulate(par, s, grid, c, keep_y=True)
    assert res.y is not None
    for r in range(2):
        assert np.allclose(res.real[r], backtr(res.y[r], res.transform, None, "power", None, 2.0), rtol=1e-13)
    assert res.real.min() >= s.z.min() * (1 - 1e-12)
    w = np.where(s.z > np.median(s.z), 0.2, 1.0)
    weighted = sgs.simulate(par, dataclasses.replace(s, w=w), grid, c)
    assert weighted.real.shape == res.real.shape and not np.array_equal(weighted.real, res.real)
    assert weighted.transform.values.size == s.n


# --- the cancel, the signatures, the checks ------------------------------------------------------------------------


class Stop(Exception):
    pass


@pytest.mark.parametrize("threads", [1, 4])
def test_tick_cancels(small, threads: int) -> None:
    par, s, grid, c = small
    seen: list[tuple[float | None, str | None]] = []

    def tick(p: float | None, label: str | None) -> None:
        seen.append((p, label))
        if label and label.startswith("Реализации") and p is not None and p > 0.3:
            raise Stop

    before = threading.active_count()
    with pytest.raises(Stop):
        sgs.simulate(par, s, grid, c, threads=threads, tick=tick)
    assert threading.active_count() <= before + 1  # the pool is gone (a thread of the cancel may be finishing)
    assert any(p is not None and 0.0 < p <= 1.0 for p, _ in seen)
    assert seen[-1][1] is not None and seen[-1][1].startswith("Реализации")


def test_one_realization_is_cancelled_and_reported_inside(small, monkeypatch: pytest.MonkeyPatch) -> None:
    par, s, grid, c = small
    monkeypatch.setattr(sgs, "STEP", 100)  # a portion of the path: the tick goes between portions
    monkeypatch.setattr(sgs, "POLL", 0.0)
    one = dataclasses.replace(par, n_real=1)
    seen: list[float] = []

    def tick(p: float | None, label: str | None) -> None:
        if p is not None and label and label.startswith("Реализации"):
            seen.append(p)
            if 0.0 < p < 1.0 and len(seen) >= 3:
                raise Stop

    with pytest.raises(Stop):
        sgs.simulate(one, s, grid, c, threads=1, tick=tick)
    assert len(seen) >= 3 and seen == sorted(seen) and all(0.0 < p < 1.0 for p in seen)  # inside the realization


def test_a_sill_far_from_one_is_noted_in_the_journal(small) -> None:
    par, s, grid, c = small
    one = dataclasses.replace(par, n_real=1)

    def noted(sill: float) -> list[str]:
        model = Model(0.1, (Structure("spherical", sill - 0.1, 6.0, 6.0, 6.0),))
        res = sgs.simulate(dataclasses.replace(one, model=model), s, grid, c)
        return [line for line in res.notes if line.startswith("Порог модели")]

    assert noted(1.0) == [] and noted(1.09) == [] and noted(0.92) == []  # within 0,1: nothing
    for sill in (1.5, 0.6):
        (line,) = noted(sill)
        assert f"{sill:.3f}".replace(".", ",") in line and "1" in line and "не пересчитывается" in line


def test_tick_reports_every_realization(small) -> None:
    par, s, grid, c = small
    labels: list[str] = []
    prog: list[float] = []

    def tick(p: float | None, label: str | None) -> None:
        if label and label.startswith("Реализации"):
            labels.append(label)
            prog.append(float(p or 0.0))

    sgs.simulate(par, s, grid, c, tick=tick)
    assert labels[-1] == "Реализации 8 / 8" and prog == sorted(prog) and prog[-1] == pytest.approx(1.0)


def test_read_only_inputs_add_no_signature() -> None:
    model, ell = iso(4.0)
    rng = np.random.default_rng(1)
    xyz = rng.uniform(0.0, 8.0, (20, 3)) * [1, 1, 0]
    s = make_samples(xyz, rng.lognormal(0.0, 0.4, 20))
    grid = block((8, 8, 1))
    par = params(model, ell, seed=1, n_real=2)
    c = sgs.grid_centres(grid, par.steps)
    sgs.simulate(par, s, grid, c)
    before = len(sgs.sgs_steps.signatures)
    ro = [c, grid.index, grid.cell, s.local, s.z]
    for a in ro:
        a.setflags(write=False)
    got = sgs.simulate(par, s, grid, c)
    assert len(sgs.sgs_steps.signatures) == before == 1
    for a in ro:
        a.setflags(write=True)
    assert np.array_equal(got.real, sgs.simulate(par, s, grid, c).real)
    sgs.warm_sgs()


def test_bad_input_is_refused_in_russian() -> None:
    model, ell = iso(4.0)
    s = make_samples(np.array([[1.0, 1.0, 0.0], [2.0, 3.0, 0.0], [4.0, 2.0, 0.0]]), np.array([1.0, 2.0, 3.0]))
    grid = block((4, 4, 1))
    c = sgs.grid_centres(grid, (1.0, 1.0, 1.0))
    ok = params(model, ell)
    for kw, name in (({"seed": -1}, "seed"), ({"n_real": 0}, "n_real"), ({"n_real": 101}, "n_real"),
                     ({"nodes": 65}, "nodes"), ({"n_max": 0}, "n_max"), ({"n_max": 129}, "n_max"),
                     ({"utail": "power"}, "utpar"), ({"ltail": "cubic"}, "ltail")):  # fmt: skip
        with pytest.raises(ApiError) as ei:
            sgs.simulate(dataclasses.replace(ok, **kw), s, grid, c)
        assert ei.value.status == 422 and ei.value.extra["param"] == name and ei.value.hint, kw
    nan_c = c.copy()
    nan_c[3, 1] = np.nan  # a gap in the centre of a node is refused, not simulated as a zero
    with pytest.raises(ApiError) as ei:
        sgs.simulate(ok, s, grid, nan_c)
    assert ei.value.extra["param"] == "centres"
    with pytest.raises(ApiError):
        sgs.simulate(ok, s, grid, c[:5])
    empty = nd.NodeGrid.of((3, 3, 1), np.zeros(0, dtype=np.uint64))
    res = sgs.simulate(ok, s, empty, np.zeros((0, 3)))
    assert res.real.shape == (20, 0)
