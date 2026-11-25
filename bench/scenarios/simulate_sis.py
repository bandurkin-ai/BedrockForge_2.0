"""The kernel of the sequential indicator simulation, SIS (ТЗ 8.8.1, № 84; ADR 0024 «Замеры»; Э18б, 18.36): full — 1 млн
узлов × 20 реализаций на 4 ядрах (непрерывная по 5 порогам; категориальная — 250 тыс. × 20 с прогнозом), ci — 100×100×1
× 20 на 2 ядрах (Т18б-С1, С2).

The deposit of `simulate_common`, n_max 24, 12 simulated nodes. Two runs:
  continuous  5 thresholds (the 10, 30, 50, 70, 90 % quantiles of the samples), an exponential indicator model with a
              nugget (0.3 of the sill p(1 − p)) per threshold (`models: per`), linear tails (Т18б-С1);
  categorical 3 categories (the codes 3, 5, 9; the shares 0.3 / 0.4 / 0.3), the same models per category (Т18б-С2).
  sim_s, cat_s     the two calls (`cpu_s`, `cat_cpu_s` — their CPU seconds);  us_per_node, forecast_1m_s (and
                   `cat_` of them) — as `simulate_sgs`
  share_err, cat_share_err   the largest |share of the class ≤ t_k (of the category) in the realizations − the
                   declustered share of the samples|;  gamma_ratio, cat_gamma_ratio — the indicator variogram of the
                   realizations on the lags 1…5 over the model, the worst of the classes
  cancel_s         the cancel in the middle of the continuous run
What is checked (a failure stops the run): the values are finite, inside the samples' range (the default tails), the
codes are in the list; the shares within 0.03 of the samples' (Т18б-С1, С2); the indicator variogram within 15 % of its
model on the lags 1…5; Σ p_k = 1 to 1e−12 at every node of a small run with `keep_p` (categories); the bits: a
100×100×1 × 4 run on 1 thread and on `threads` are equal (Т18б-С4), for both kinds; the cancel returns within 5 s."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import simulate_common as sc

CANCEL_MAX_S = 5.0
SHARE_TOL = 0.03
LAGS = (1, 2, 3, 4, 5)


def prepare(params: dict[str, Any], work: Path) -> None:
    sc.World.build(work, params)


def load() -> None:
    from bf_core.geostat.simulation.sis import warm_sis

    warm_sis()


def continuous(s: Any, n_real: int, seed: int) -> Any:
    from bf_core.geostat.estimate.indicator import proportions
    from bf_core.geostat.simulation.sis import SisParams

    th = tuple(float(v) for v in np.quantile(s.z, sc.QUANTILES))
    p = proportions(s.z, s.w, th)
    models = tuple(sc.indicator_model(float(x)) for x in p)
    return SisParams(models, sc.ellipsoid(), sc.STEPS, thresholds=th, seed=seed, n_real=n_real)


def categorical(w: Any, s: Any, n_real: int, seed: int) -> tuple[Any, np.ndarray]:
    from bf_core.geostat.simulation.sis import SisParams

    cat = w.cat(s)
    share = [float(np.mean(cat == c)) for c in sc.CODES]
    models = tuple(sc.indicator_model(p) for p in share)
    return SisParams(models, sc.ellipsoid(), sc.STEPS, codes=sc.CODES, seed=seed, n_real=n_real), cat


def worst(ratios: list[list[float]]) -> list[float]:
    """The ratio farthest from 1 at every lag, over the classes."""
    return [max((r[i] for r in ratios), key=lambda x: abs(x - 1.0)) for i in range(len(ratios[0]))]


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.geostat.simulation import sgs, sis

    threads = sc.limit_threads(params)
    n_real = int(params.get("n_real", 20))
    w = sc.World.open(work)
    s = w.samples()
    grid = w.grid()
    c = sc.centres(grid)
    side = w.nx
    # --- continuous
    p = continuous(s, n_real, 12345)
    th = np.asarray(p.thresholds)
    res, sim_s, cpu_s = sc.timed(lambda: sis.simulate(p, s, grid, c, threads=threads))
    peak = sc.peak_mb()
    assert res.real.shape == (n_real, grid.n) and res.real.dtype == np.float64 and bool(np.isfinite(res.real).all())
    assert res.real.min() >= s.z.min() * (1 - 1e-12) and res.real.max() <= s.z.max() * (1 + 1e-12)
    info: dict[str, Any] = {"threads": threads, "samples": s.n, "nodes": grid.n, "n_real": n_real,
                            "sim_s": round(sim_s, 3), "cpu_s": round(cpu_s, 3), **sc.cost(sim_s, grid.n, n_real),
                            "degenerate": int(res.counts[:, sgs.C_DEGENERATE].sum()),
                            "free": int(res.counts[:, sgs.C_FREE].sum()), "peak_mb_run": peak}  # fmt: skip
    got = np.array([(res.real <= t).mean() for t in th])
    err = float(np.abs(got - res.prop).max())
    ratios = [sc.gamma_ratio(((r <= t).reshape(side, side) for r in res.real), sc.gamma_of(p.models[k]), LAGS)
              for k, t in enumerate(th)]  # fmt: skip
    ratio = worst(ratios)
    info.update(share_err=round(err, 4), gamma_ratio=[round(x, 3) for x in ratio])
    assert err <= SHARE_TOL, (got, res.prop)
    assert all(abs(x - 1.0) <= sc.GAMMA_TOL for x in ratio), ratios
    info["cancel_s"] = round(sc.cancel_seconds(lambda tick: sis.simulate(p, s, grid, c, threads=threads, tick=tick)), 3)
    assert info["cancel_s"] <= CANCEL_MAX_S, f"the cancel took {info['cancel_s']} s"
    del res
    # --- the bits of the continuous one: 1 thread = `threads`
    sub = sc.SMALL
    ss, gs = w.samples(sub), w.grid(sub)
    cs = sc.centres(gs)
    ps = continuous(ss, 4, 99)
    one = sis.simulate(ps, ss, gs, cs, threads=1)
    many = sis.simulate(ps, ss, gs, cs, threads=threads)
    sc.assert_same(one.real, many.real, f"continuous {sub}×{sub}×1, 1 and {threads} threads")
    # --- categorical: its own grid of `cat_rows` nodes (the samples inside it)
    cside = sc.side_of(params, "cat_rows") if "cat_rows" in params else side
    cs_s, cg = (s, grid) if cside == side else (w.samples(cside), w.grid(cside))
    cc = c if cside == side else sc.centres(cg)
    cp, cat = categorical(w, cs_s, n_real, 777)
    cres, cat_s, cat_cpu = sc.timed(lambda: sis.simulate(cp, cs_s, cg, cc, cat=cat, threads=threads))
    assert cres.real.dtype == np.uint8 and set(np.unique(cres.real)) <= set(sc.CODES)
    cgot = np.array([(cres.real == code).mean() for code in sc.CODES])
    cerr = float(np.abs(cgot - cres.prop).max())
    cratios = [sc.gamma_ratio(((r == code).reshape(cside, cside) for r in cres.real), sc.gamma_of(cp.models[k]), LAGS)
               for k, code in enumerate(sc.CODES)]  # fmt: skip
    cratio = worst(cratios)
    info.update({"cat_nodes": cg.n, "cat_s": round(cat_s, 3), "cat_cpu_s": round(cat_cpu, 3),
                 "cat_share_err": round(cerr, 4), "cat_gamma_ratio": [round(x, 3) for x in cratio],
                 **{f"cat_{k}": v for k, v in sc.cost(cat_s, cg.n, n_real).items()}})  # fmt: skip
    assert cerr <= SHARE_TOL, (cgot, cres.prop)
    assert all(abs(x - 1.0) <= sc.GAMMA_TOL for x in cratio), cratios
    del cres
    # Σ p_k = 1 at every node and the bits of the categorical one on a small grid
    sc_s = w.samples(sub)
    cp2, cat2 = categorical(w, sc_s, 4, 5)
    a = sis.simulate(cp2, sc_s, gs, cs, cat=cat2, threads=1, keep_p=True)
    b = sis.simulate(cp2, sc_s, gs, cs, cat=cat2, threads=threads)
    sc.assert_same(a.real, b.real, f"categorical {sub}×{sub}×1, 1 and {threads} threads")
    assert a.p is not None and np.abs(a.p.sum(axis=2) - 1.0).max() <= 1e-12
    info["p_sum_err"] = float(np.abs(a.p.sum(axis=2) - 1.0).max())
    return info
