"""The kernel of the sequential Gaussian simulation, SGS (ТЗ 8.8.1, № 84; ADR 0024 «Замеры»; Э18б, 18.36): full — 1 млн
узлов × 20 реализаций на 4 ядрах (ориентир ТЗ № 84: ≤ 10 мин), ci — 100×100×1 × 20 на 2 ядрах (Т8.8-14).

A synthetic deposit of `simulate_common` (a grid of one layer, `samples` samples, a spherical model of the range 10
cells with the nugget 0.1 in Y, n_max 24, 12 simulated nodes, tails linear — the defaults of Д-23), `sgs.simulate` with
`threads` cores and `keep_y` (the scores are checked).
  sim_s, cpu_s     the call, whole: the search of the samples of the nodes, the template, 20 realizations, the
                   back-transform; the wall seconds and the CPU seconds of all threads (what it costs on a quiet
                   machine: the bench machines are shared);  us_per_node — a node of a realization (wall);
                   forecast_1m_s — the straight line to 1 млн × 20 (the target of ТЗ № 84)
  cancel_s         the cancel in the middle (the tick raises at the first progress of the realizations): the seconds
                   from the raise to the return of the call
  ks_z, ks_y       the mean Kolmogorov–Smirnov distance of the realizations to the distribution of the samples / N(0, 1)
  gamma_ratio      the variogram of the realizations in Y on the lags 1, 3, 5, 7 over the model (x and y together)
  degenerate, free the counts of the kernel
What is checked (a failure stops the run): every value is finite and inside the tails of the samples; Т8.8-14 — the
mean K-S ≤ 0.05, the mean of Y within 0.1 of 0, its variance within 0.1 of 1, the variogram within 15 % of the model;
the bits: 100×100×1 × 4 on 1 thread and on `threads` are equal bit for bit (Т8.8-13), and where the run is small
(≤ 20 тыс. узлов) the run itself equals the same call on 1 thread (it costs ≈ 2 s); the cancel returns within 5 s."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import simulate_common as sc

CANCEL_MAX_S = 5.0


def prepare(params: dict[str, Any], work: Path) -> None:
    sc.World.build(work, params)


def load() -> None:
    from bf_core.geostat.simulation.sgs import warm_sgs

    warm_sgs()


def par(n_real: int, seed: int) -> Any:
    from bf_core.geostat.simulation.sgs import SgsParams

    return SgsParams(sc.model_y(), sc.ellipsoid(), sc.STEPS, seed=seed, n_real=n_real)  # n_max 24, nodes 12


def statistics(res: Any, s: Any, side: int) -> dict[str, Any]:
    """Т8.8-14 on the realizations of the run → the numbers (asserted)."""
    from scipy.stats import norm

    assert res.y is not None
    zs = np.sort(s.z)
    ecdf = (np.arange(zs.size) + 0.5) / zs.size
    ks_z = float(np.mean([sc.ks_distance(r, lambda x: np.interp(x, zs, ecdf)) for r in res.real]))
    ks_y = float(np.mean([sc.ks_distance(r, norm.cdf) for r in res.y]))
    mean_y, var_y = float(np.mean(res.y)), float(np.var(res.y))
    ratio = sc.gamma_ratio((r.reshape(side, side) for r in res.y), sc.gamma_of(sc.model_y()))
    assert ks_z <= sc.KS_MAX and ks_y <= sc.KS_MAX, (ks_z, ks_y)
    assert abs(mean_y) < 0.1 and abs(var_y - 1.0) < 0.1, (mean_y, var_y)
    assert all(abs(x - 1.0) <= sc.GAMMA_TOL for x in ratio), ratio
    return {"ks_z": round(ks_z, 4), "ks_y": round(ks_y, 4), "mean_y": round(mean_y, 4), "var_y": round(var_y, 4),
            "gamma_ratio": [round(x, 3) for x in ratio]}  # fmt: skip


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.geostat.simulation import sgs

    threads = sc.limit_threads(params)
    n_real = int(params.get("n_real", 20))
    w = sc.World.open(work)
    s = w.samples()
    grid = w.grid()
    c = sc.centres(grid)
    p = par(n_real, 12345)
    res, sim_s, cpu_s = sc.timed(lambda: sgs.simulate(p, s, grid, c, threads=threads, keep_y=True))
    peak = sc.peak_mb()
    assert res.real.shape == (n_real, grid.n) and bool(np.isfinite(res.real).all()), "a gap or a NaN in a realization"
    assert res.real.min() >= s.z.min() * (1 - 1e-12) and res.real.max() <= s.z.max() * (1 + 1e-12)
    info: dict[str, Any] = {"threads": threads, "samples": s.n, "nodes": grid.n, "n_real": n_real,
                            "sim_s": round(sim_s, 3), "cpu_s": round(cpu_s, 3), **sc.cost(sim_s, grid.n, n_real),
                            "degenerate": int(res.counts[:, sgs.C_DEGENERATE].sum()),
                            "free": int(res.counts[:, sgs.C_FREE].sum()), "peak_mb_run": peak}  # fmt: skip
    info.update(statistics(res, s, w.nx))
    # the bits: any number of threads gives the same file (Т8.8-13)
    sub = sc.SMALL
    ss, gs = w.samples(sub), w.grid(sub)
    cs = sc.centres(gs)
    ps = par(4, 99)
    one = sgs.simulate(ps, ss, gs, cs, threads=1)
    many = sgs.simulate(ps, ss, gs, cs, threads=threads)
    sc.assert_same(one.real, many.real, f"{sub}×{sub}×1, 1 and {threads} threads")
    if grid.n <= 20_000:
        again = sgs.simulate(p, s, grid, c, threads=1)
        sc.assert_same(again.real, res.real, "the run and 1 thread")
        info["bits_vs_1_thread"] = True
    # the cancel: the tick raises at the first progress of the realizations
    info["cancel_s"] = round(sc.cancel_seconds(lambda tick: sgs.simulate(p, s, grid, c, threads=threads, tick=tick)), 3)
    assert info["cancel_s"] <= CANCEL_MAX_S, f"the cancel took {info['cancel_s']} s"
    return info
