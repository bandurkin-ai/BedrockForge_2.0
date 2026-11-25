"""Co-SGS with a collocated secondary variable (ТЗ 8.8.1; ADR 0024 §9; Д-23; Э18б, 18.34): one realization equals a
plain NumPy sequential simulation with the MM1 system of n + 1 rows; Т18б-С3 (ρ = 0 is the SGS bit for bit, ρ = ±0.95 —
the correlation of the realizations with the secondary within ρ ± 0.05, ρ = 1 — the realizations are the secondary),
Т18б-С4 (the seed and 1, 2, 4, 8 threads — the same bits); the gaps of the secondary are not zeros; ρ computed from
the samples; the cancel, the signatures, the checks."""

from __future__ import annotations

import dataclasses
import math
import threading

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.geostat.estimate.cov import cov_model
from bf_core.geostat.estimate.samples import RowSamples
from bf_core.geostat.estimate.search import Ellipsoid, Searcher
from bf_core.geostat.estimate.spec_parts import Pass
from bf_core.geostat.simulation import cosgs, sgs
from bf_core.geostat.simulation import nodes as nd
from bf_core.geostat.simulation.path import realization_path
from bf_core.geostat.variogram.model import Model, Structure
from tests.test_simulation_sgs import block, iso, make_samples, params


def joint(side: int, rho: float, seed: int, ns: int, jitter: float = 0.4):
    """A primary y₁ = ρ·y₂ + √(1 − ρ²)·w and a secondary y₂ (w, y₂ — independent fields of the model: C₁₂ = ρ·C, the
    MM1 pair); `ns` samples near the nodes; the secondary as a field of a skewed value (exp). → par, samples, grid,
    centres, secondary, [y₁; y₂] at the nodes [2, n]."""
    model, ell = iso(8.0, 0.1)
    grid = block((side, side, 1))
    par = params(model, ell, seed=seed, n_real=20)
    c = sgs.grid_centres(grid, par.steps)
    rng = np.random.default_rng(seed)
    chol = np.linalg.cholesky(model.cov(c[:, None, :] - c[None, :, :]) + 1e-9 * np.eye(grid.n))
    y2 = chol @ rng.standard_normal(grid.n)
    y1 = rho * y2 + math.sqrt(1.0 - rho * rho) * (chol @ rng.standard_normal(grid.n))
    idx = rng.choice(grid.n, ns, replace=False)
    xyz = c[idx] + rng.uniform(-jitter, jitter, (ns, 3)) * [1, 1, 0]
    return par, make_samples(xyz, np.exp(1.0 + 0.6 * y1[idx])), grid, c, np.exp(0.5 + 0.3 * y2), np.vstack((y1, y2))


# --- one realization = NumPy, step by step ----------------------------------------------------------------------


def reference_y(par: sgs.SgsParams, s: RowSamples, grid: nd.NodeGrid, centres: np.ndarray, sec: np.ndarray,
                rho: float, r: int) -> np.ndarray:  # fmt: skip
    """The co-SGS in the plainest form: a Python loop over the path; the neighbourhood from the modules of 18.6 and
    18.30 (tested apart); the system of n + 1 rows by `numpy.linalg.solve` with `Model.cov` (MM1: C₁₂ = ρ·C)."""
    sy, _ = sgs.sample_scores(s, par)
    sr = Searcher(s, par.ell, (Pass(1.0, 0, par.n_max, 0, 0, 0, 0),))
    cidx, cn = nd.sample_neighbours(sr, centres)
    tpl = nd.spiral_template(par.steps, par.ell, cov_model(par.model), shape=grid.shape)
    path, rng = realization_path(par.seed, par.n_real, r, grid.n)
    draws = rng.standard_normal(grid.n)
    y = np.full(grid.n, np.nan)
    simulated = np.zeros(grid.n, dtype=bool)
    c0 = par.model.sill
    sec_y = cosgs.secondary_scores(sec) * math.sqrt(c0)  # the scores are the module's own (tested apart)
    for step, node in enumerate(path):
        sidx = cidx[node, : cn[node]].astype(np.int64)
        d_hit = np.abs(s.local[sidx] - centres[node]).max(axis=1) if sidx.size else np.zeros(0)
        if (d_hit <= 1e-6).any():  # a sample at the node is the node
            y[node] = sy[sidx[int(np.argmax(d_hit <= 1e-6))]]
            simulated[node] = True
            continue
        near = nd.nearest_nodes(grid, simulated, tpl, int(node), par.nodes)
        if sidx.size:  # a node that is a conditioning sample is that sample
            dn = np.linalg.norm(centres[near][:, None, :] - s.local[sidx][None, :, :], axis=2)
            near = near[~(dn <= 1e-6).any(axis=1)]
        pts = np.vstack((s.local[sidx], centres[near]))
        val = np.concatenate((sy[sidx], y[near]))
        has = bool(np.isfinite(sec_y[node]))
        k0 = par.model.cov(pts - centres[node]) if pts.shape[0] else np.zeros(0)
        if has:
            kmat = np.zeros((pts.shape[0] + 1,) * 2)
            if pts.shape[0]:
                kmat[:-1, :-1] = par.model.cov(pts[:, None, :] - pts[None, :, :])
            kmat[:-1, -1] = kmat[-1, :-1] = rho * k0
            kmat[-1, -1] = c0
            rhs = np.append(k0, rho * c0)
            lam = np.linalg.solve(kmat, rhs)
            est = float(lam[:-1] @ val) + lam[-1] * sec_y[node]
            var = c0 - float(lam @ rhs)
        elif pts.shape[0]:
            lam = np.linalg.solve(par.model.cov(pts[:, None, :] - pts[None, :, :]), k0)
            est, var = float(lam @ val), c0 - float(lam @ k0)
        else:
            est, var = 0.0, c0
        y[node] = est + math.sqrt(max(var, 0.0)) * draws[step]
        simulated[node] = True
    return y


def test_realization_equals_a_numpy_loop() -> None:
    model = Model(0.15, (Structure("spherical", 0.55, 9.0, 6.0, 4.0, 30.0, 10.0, 5.0),
                         Structure("exponential", 0.3, 14.0, 10.0, 7.0, 30.0, 10.0, 5.0)))  # fmt: skip
    ell = Ellipsoid.of((30.0, 10.0, 5.0), (14.0, 10.0, 7.0))
    grid = block((7, 6, 4), share=0.85, seed=2)
    rng = np.random.default_rng(5)
    c = sgs.grid_centres(grid, (1.0, 1.1, 1.3))
    at = c[[4, 20]]  # two samples exactly at the nodes
    s = make_samples(np.vstack((at, rng.uniform(0.0, 8.0, (28, 3)))), rng.lognormal(1.0, 0.5, 30))
    sec = rng.lognormal(0.0, 0.4, grid.n)
    sec[10:30] = np.nan  # a gap: the nodes simulated as SGS
    sec[[2, 3]] = 0.0  # a true zero is a value
    par = params(model, ell, seed=99, n_real=3, n_max=7, nodes=9, steps=(1.0, 1.1, 1.3))
    for rho in (0.6, -0.8):
        res = cosgs.simulate(par, s, grid, c, sec, rho, keep_y=True)
        assert res.y is not None and res.rho == rho and res.rho_pairs == 0 and res.n_gap == 20
        for r in range(par.n_real):
            want = reference_y(par, s, grid, c, sec, rho, r)
            assert np.allclose(res.y[r], want, rtol=0, atol=1e-8), (rho, np.abs(res.y[r] - want).max())
        assert (res.counts[:, cosgs.C_GAP] >= 18).all() and (res.counts[:, cosgs.C_ON_SAMPLE] == 2).all()


# --- Т18б-С3: ρ = 0 is the SGS; ρ = ±0.95; ρ = ±1 -------------------------------------------------------------


@pytest.mark.parametrize("threads", [1, 4])
def test_t18b_s3_rho_zero_is_the_sgs_bit_for_bit(threads: int) -> None:
    par, s, grid, c, sec, _ = joint(20, 0.5, 3, 40)
    sec[7:11] = np.nan
    base = sgs.simulate(par, s, grid, c, threads=threads, keep_y=True)
    for zero_as_sgs in (True, False):  # the delegation and the kernel of n + 1 rows (a row of zeros) alike
        got = cosgs.simulate(par, s, grid, c, sec, 0.0, threads=threads, keep_y=True, zero_as_sgs=zero_as_sgs)
        assert got.rho == 0.0 and got.counts.shape == (par.n_real, cosgs.COUNTS)
        assert got.real.tobytes() == base.real.tobytes(), zero_as_sgs
        assert got.y is not None and base.y is not None and got.y.tobytes() == base.y.tobytes()
        assert np.array_equal(got.counts[:, : sgs.COUNTS], base.counts)
        assert np.array_equal(got.cond, base.cond)
    other = cosgs.simulate(par, s, grid, c, sec, 1e-9)
    assert not np.array_equal(other.real, base.real) and np.allclose(other.real, base.real, rtol=1e-6)  # continuity


def mean_correlation(res: cosgs.CosgsResult) -> float:
    assert res.y is not None and res.secondary_y is not None
    return float(np.mean([np.corrcoef(r, res.secondary_y)[0, 1] for r in res.y]))


@pytest.mark.parametrize("rho", [0.95, -0.95])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_t18b_s3_correlation_with_the_secondary(rho: float, seed: int) -> None:
    par, s, grid, c, sec, _ = joint(30, rho, seed, 80)
    res = cosgs.simulate(par, s, grid, c, sec, rho, threads=4, keep_y=True)
    assert res.rho == rho and res.secondary_y is not None and np.isfinite(res.secondary_y).all()
    got = mean_correlation(res)
    assert abs(got - rho) <= 0.05, (rho, got)
    assert abs(res.y.mean()) < 0.15 and 0.8 < res.y.var() < 1.2  # a normal field (the variance of MM1 is not exact)


def test_correlation_grows_with_rho() -> None:
    par, s, grid, c, sec, _ = joint(30, 0.5, 5, 60)
    got = [mean_correlation(cosgs.simulate(par, s, grid, c, sec, rho, threads=4, keep_y=True))
           for rho in (-0.7, 0.0, 0.3, 0.7)]  # fmt: skip
    assert got == sorted(got) and got[0] < -0.5 and abs(got[1]) < 0.3 and got[3] > 0.6, got


@pytest.mark.parametrize("rho", [1.0, -1.0])
def test_t18b_s3_rho_one_is_the_secondary(rho: float) -> None:
    par, s, grid, c, sec, _ = joint(20, 0.9, 4, 30)
    par = dataclasses.replace(par, n_real=3)
    res = cosgs.simulate(par, s, grid, c, sec, rho, keep_y=True, threads=2)
    assert res.y is not None and res.secondary_y is not None
    rest = np.arange(grid.n)  # no sample is at a centre (the jitter): every node is the secondary
    assert (res.counts[:, cosgs.C_ON_SAMPLE] == 0).all()
    for r in res.y:
        assert np.allclose(r[rest], rho * res.secondary_y[rest], rtol=0, atol=1e-6)
    assert np.isfinite(res.real).all() and (res.counts[:, cosgs.C_DEGENERATE] == 0).all()


def test_a_sample_at_a_node_is_the_node_whatever_the_secondary() -> None:
    par, _, grid, c, sec, _ = joint(12, 0.7, 6, 5)
    rng = np.random.default_rng(2)
    at = np.array([5, 30, 31, 77])
    s = make_samples(np.vstack((c[at], rng.uniform(0.0, 12.0, (20, 3)) * [1, 1, 0])), rng.lognormal(1.0, 0.7, 24))
    sy, _ = sgs.sample_scores(s, par)
    where = np.array([int(np.argmin(np.abs(s.local - c[a]).sum(axis=1))) for a in at])  # the samples are re-ordered
    for rho in (0.7, 1.0):
        res = cosgs.simulate(par, s, grid, c, sec, rho, keep_y=True)
        assert res.y is not None
        assert np.array_equal(res.y[:, at], np.tile(sy[where], (par.n_real, 1)))  # the score, bit for bit
        assert (res.counts[:, cosgs.C_ON_SAMPLE] == 4).all() and (res.counts[:, cosgs.C_DEGENERATE] == 0).all()


def test_rho_is_computed_from_the_colocated_scores() -> None:
    par, s, grid, c, sec, ys = joint(30, 0.8, 7, 250, jitter=0.45)
    res = cosgs.simulate(par, s, grid, c, sec, None, threads=4, keep_y=True)
    node = np.array([int(np.argmin(((c - p) ** 2).sum(axis=1))) for p in s.local])
    realised = float(np.corrcoef(ys[0, node], ys[1, node])[0, 1])  # the correlation of the very fields at the samples
    assert res.rho_pairs == s.n and abs(res.rho - realised) < 0.06, (res.rho, realised, res.rho_pairs)
    assert any("пар проба–узел" in line for line in res.notes)
    again = cosgs.simulate(par, s, grid, c, sec, res.rho, threads=1)
    assert np.array_equal(again.real, res.real)  # the computed ρ is the given one
    sy, _ = sgs.sample_scores(s, par)
    assert cosgs.colocated_rho(s, sy, c, par.steps, res.secondary_y)[0] == res.rho  # type: ignore[arg-type]
    # an exact reference: the weighted Pearson of the pairs (the nearest node, the weights of the row)
    w = np.where(np.arange(s.n) % 3 == 0, 3.0, 1.0)
    x, t = sy, res.secondary_y[node]  # type: ignore[index]
    ww = w / w.sum()
    want = (ww @ ((x - ww @ x) * (t - ww @ t))) / math.sqrt((ww @ (x - ww @ x) ** 2) * (ww @ (t - ww @ t) ** 2))
    got, pairs = cosgs.colocated_rho(dataclasses.replace(s, w=w), sy, c, par.steps, res.secondary_y)  # type: ignore[arg-type]
    assert pairs == s.n and got == pytest.approx(want, abs=1e-12)


def test_rho_without_pairs_is_refused() -> None:
    par, s, grid, c, sec, _ = joint(12, 0.5, 1, 30)
    far = dataclasses.replace(s, local=s.local + 1000.0)
    with pytest.raises(ApiError) as ei:
        cosgs.simulate(par, far, grid, c, sec, None)
    assert ei.value.status == 422 and ei.value.extra["param"] == "rho" and "вручную" in ei.value.hint
    few = s.take(np.arange(6))  # fewer than 10 pairs
    with pytest.raises(ApiError) as ei2:
        cosgs.simulate(par, few, grid, c, sec, None)
    assert ei2.value.extra["param"] == "rho"
    assert np.isfinite(cosgs.simulate(par, few, grid, c, sec, 0.4).real).all()  # given — no pairs needed


# --- Т18б-С4: the seed, the threads ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small():
    par, s, grid, c, sec, _ = joint(24, 0.7, 11, 50)
    sec[100:140] = np.nan
    return dataclasses.replace(par, seed=2024, n_real=8), s, grid, c, sec


def test_t18b_s4_seed_repeats_bit_for_bit(small) -> None:
    par, s, grid, c, sec = small
    a, b = cosgs.simulate(par, s, grid, c, sec, 0.7), cosgs.simulate(par, s, grid, c, sec, 0.7)
    assert a.real.shape == (8, grid.n) and a.real.dtype == np.float64 and np.isfinite(a.real).all()
    assert np.array_equal(a.real, b.real) and np.array_equal(a.counts, b.counts)
    other = cosgs.simulate(dataclasses.replace(par, seed=2025), s, grid, c, sec, 0.7)
    assert not np.array_equal(a.real, other.real) and not np.array_equal(a.real[0], a.real[1])


@pytest.mark.parametrize("rho", [0.7, -1.0])
def test_t18b_s4_threads_give_the_same_bits(small, rho: float) -> None:
    par, s, grid, c, sec = small
    base = cosgs.simulate(par, s, grid, c, sec, rho, threads=1, keep_y=True)
    for threads in (2, 4, 8):
        got = cosgs.simulate(par, s, grid, c, sec, rho, threads=threads, keep_y=True)
        assert got.real.tobytes() == base.real.tobytes() and got.y is not None and base.y is not None
        assert got.y.tobytes() == base.y.tobytes() and np.array_equal(got.counts, base.counts)


def test_a_realization_does_not_depend_on_the_others(small) -> None:
    par, s, grid, c, sec = small
    eight = cosgs.simulate(par, s, grid, c, sec, 0.7)
    three = cosgs.simulate(dataclasses.replace(par, n_real=3), s, grid, c, sec, 0.7)
    assert np.array_equal(eight.real[:3], three.real)


# --- the gaps of the secondary -----------------------------------------------------------------------------------


def test_a_gap_of_the_secondary_is_an_sgs_node_not_a_zero(small) -> None:
    par, s, grid, c, sec = small
    res = cosgs.simulate(par, s, grid, c, sec, 0.7, keep_y=True)
    gap = np.flatnonzero(np.isnan(sec))
    assert res.n_gap == gap.size == 40 and res.secondary_y is not None
    assert np.isnan(res.secondary_y[gap]).all() and np.isfinite(res.secondary_y[~np.isnan(sec)]).all()
    assert np.isfinite(res.real).all() and np.isfinite(res.y).all()  # type: ignore[arg-type]
    assert not (res.real[:, gap] == 0.0).any() and not (res.y[:, gap] == 0.0).any()  # type: ignore[index]
    assert (res.counts[:, cosgs.C_GAP] == 40).all()  # no sample at a gap node
    assert any("Узлов без вторичной переменной" in line for line in res.notes)
    inf = sec.copy()
    inf[gap[0]] = np.inf  # ±∞ is a gap too
    assert cosgs.simulate(par, s, grid, c, inf, 0.7).n_gap == 40
    # a gap node takes the neighbours alone: a gap node with no neighbour at all is a free draw
    far = nd.NodeGrid.of((6, 6, 1), np.arange(36, dtype=np.uint64))
    fc = sgs.grid_centres(far, par.steps)
    tiny = dataclasses.replace(par, ell=Ellipsoid.of((0.0, 0.0, 0.0), (0.3, 0.3, 0.3)), n_real=2)
    fsec = np.linspace(1.0, 2.0, 36)
    fsec[:5] = np.nan
    free = cosgs.simulate(tiny, make_samples(fc[[0, 35]] + 500.0, np.array([1.0, 2.0])), far, fc, fsec, 0.5)
    assert (free.counts[:, cosgs.C_FREE] == 5).all() and (free.counts[:, cosgs.C_GAP] == 5).all()


def test_a_true_zero_and_ties_are_values() -> None:
    sec = np.where(np.arange(40) % 2 == 0, 0.0, 2.7)
    sec[3] = np.nan
    t = cosgs.secondary_scores(sec)
    assert np.isnan(t[3]) and np.isfinite(np.delete(t, 3)).all()
    zeros, rest = t[sec == 0.0], t[sec == 2.7]
    assert np.ptp(zeros) == 0.0 and np.ptp(rest) == 0.0 and zeros[0] < 0.0 < rest[0]  # ties — one score, in order
    fin = t[np.isfinite(t)]
    assert abs(fin.mean()) < 1e-12 and fin.std() == pytest.approx(1.0, abs=1e-12)
    cont = cosgs.secondary_scores(np.random.default_rng(1).lognormal(0.0, 1.0, 2000))
    assert np.all(np.diff(cont[np.argsort(np.random.default_rng(1).lognormal(0.0, 1.0, 2000))]) > 0.0)  # monotone
    assert abs(cont.mean()) < 1e-12 and cont.std() == pytest.approx(1.0, abs=1e-12)
    for bad_sec in (np.full(10, 3.0), np.array([1.0, 2.0, np.nan, np.nan]), np.full(10, np.nan)):
        with pytest.raises(ApiError) as ei:
            cosgs.secondary_scores(bad_sec)
        assert ei.value.status == 422 and ei.value.extra["param"] == "secondary" and ei.value.hint


# --- the cancel, the signatures, the checks ------------------------------------------------------------------------


class Stop(Exception):
    pass


@pytest.mark.parametrize("threads", [1, 4])
def test_tick_cancels(small, threads: int) -> None:
    par, s, grid, c, sec = small
    seen: list[tuple[float | None, str | None]] = []

    def tick(p: float | None, label: str | None) -> None:
        seen.append((p, label))
        if label and label.startswith("Реализации") and p is not None and p > 0.3:
            raise Stop

    before = threading.active_count()
    with pytest.raises(Stop):
        cosgs.simulate(par, s, grid, c, sec, 0.7, threads=threads, tick=tick)
    assert threading.active_count() <= before + 1
    assert seen[-1][1] is not None and seen[-1][1].startswith("Реализации")


def test_one_realization_is_cancelled_and_reported_inside(small, monkeypatch: pytest.MonkeyPatch) -> None:
    par, s, grid, c, sec = small
    monkeypatch.setattr(cosgs, "STEP", 100)  # a portion of the path: the tick goes between portions
    monkeypatch.setattr(cosgs, "POLL", 0.0)
    one = dataclasses.replace(par, n_real=1)
    seen: list[float] = []

    def tick(p: float | None, label: str | None) -> None:
        if p is not None and label and label.startswith("Реализации"):
            seen.append(p)
            if 0.0 < p < 1.0 and len(seen) >= 3:
                raise Stop

    with pytest.raises(Stop):
        cosgs.simulate(one, s, grid, c, sec, 0.7, threads=1, tick=tick)
    assert len(seen) >= 3 and seen == sorted(seen) and all(0.0 < p < 1.0 for p in seen)  # inside the realization


def test_tick_reports_every_realization(small) -> None:
    par, s, grid, c, sec = small
    labels: list[str] = []
    prog: list[float] = []

    def tick(p: float | None, label: str | None) -> None:
        if label and label.startswith("Реализации"):
            labels.append(label)
            prog.append(float(p or 0.0))

    cosgs.simulate(par, s, grid, c, sec, 0.7, tick=tick)
    assert labels[-1] == "Реализации 8 / 8" and prog == sorted(prog) and prog[-1] == pytest.approx(1.0)


def test_read_only_inputs_add_no_signature() -> None:
    par, s, grid, c, sec, _ = joint(8, 0.5, 1, 20)
    par = dataclasses.replace(par, n_real=2)
    cosgs.simulate(par, s, grid, c, sec, 0.5, zero_as_sgs=False)
    before = len(cosgs.cosgs_steps.signatures)
    ro = [c, grid.index, grid.cell, s.local, s.z, sec]
    for a in ro:
        a.setflags(write=False)
    got = cosgs.simulate(par, s, grid, c, sec, 0.5, zero_as_sgs=False)
    assert len(cosgs.cosgs_steps.signatures) == before == 1
    for a in ro:
        a.setflags(write=True)
    assert np.array_equal(got.real, cosgs.simulate(par, s, grid, c, sec, 0.5, zero_as_sgs=False).real)
    cosgs.warm_cosgs()
    assert len(cosgs.cosgs_steps.signatures) == 1


def test_bad_input_is_refused_in_russian() -> None:
    par, s, grid, c, sec, _ = joint(8, 0.5, 1, 20)
    for rho in (1.5, -1.01, float("nan"), float("inf"), True, "0.5"):
        with pytest.raises(ApiError) as ei:
            cosgs.simulate(par, s, grid, c, sec, rho)  # type: ignore[arg-type]
        assert ei.value.status == 422 and ei.value.extra["param"] == "rho" and ei.value.hint, rho
    with pytest.raises(ApiError) as ei2:
        cosgs.simulate(dataclasses.replace(par, n_real=0), s, grid, c, sec, 0.5)
    assert ei2.value.extra["param"] == "n_real"
    with pytest.raises(ApiError) as ei3:
        cosgs.simulate(par, s, grid, c, sec[:5], 0.5)
    assert ei3.value.extra["param"] == "secondary"
    with pytest.raises(ApiError) as ei4:
        cosgs.simulate(par, s, grid, c, np.full(grid.n, 2.0), 0.5)
    assert ei4.value.extra["param"] == "secondary"
    nan_c = c.copy()
    nan_c[3, 1] = np.nan
    with pytest.raises(ApiError) as ei5:
        cosgs.simulate(par, s, grid, nan_c, sec, 0.5)
    assert ei5.value.extra["param"] == "centres"
    empty = nd.NodeGrid.of((3, 3, 1), np.zeros(0, dtype=np.uint64))
    res = cosgs.simulate(par, s, empty, np.zeros((0, 3)), np.zeros(0), 0.5)
    assert res.real.shape == (20, 0)
