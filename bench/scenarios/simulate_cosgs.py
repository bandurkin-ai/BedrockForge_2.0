"""The kernel of the co-SGS with a collocated secondary variable, MM1 (ТЗ 8.8.1, № 84; ADR 0024 «Замеры»; Э18б, 18.36):
full — 1 млн узлов × 20 реализаций на 4 ядрах (ориентир ТЗ № 84: ≤ 10 мин), ci — 100×100×1 × 20 на 2 ядрах (Т18б-С3).

The deposit of `simulate_common`: the primary and the secondary are fields of the same model with the correlation
0.7 (`RHO`); the secondary is a field of a skewed value at every node, 1 % of the nodes are gaps (NaN, as the SGS).
ρ is computed from the samples (`rho` None, as the job does by default).
  sim_s, cpu_s, us_per_node, forecast_1m_s   the call, as `simulate_sgs`;  rho, rho_pairs — the coefficient used
  corr             the mean correlation of the realizations (in Y) with the secondary scores where it has a value
  sgs_s            the SGS of the same data (the same call without the secondary);  cost_ratio — the cost of the
                   second variable: the CPU seconds of the co-SGS over those of the SGS
  cancel_s         the cancel in the middle of the run
What is checked (a failure stops the run): the values are finite; the computed ρ within 0.1 of the ρ of the data; the
correlation of the realizations within 0.1 of the ρ used (Т18б-С3 holds ±0.05 for the exact model, the sample ρ is a
sample); the mean of Y within 0.15 of 0 and its variance in 0.8–1.2 (the variance of MM1 is not exact); the nodes of
the gaps are counted; ρ = 0 is the SGS bit for bit (Т18б-С3), on 100×100×1 × 4 the bits of 1 thread and of `threads`
are equal (Т18б-С4); the cancel returns within 5 s."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import simulate_common as sc

CANCEL_MAX_S = 5.0
GAP_SHARE = 0.01
RHO_TOL = 0.1
CORR_TOL = 0.1


def prepare(params: dict[str, Any], work: Path) -> None:
    sc.World.build(work, params)


def load() -> None:
    from bf_core.geostat.simulation.cosgs import warm_cosgs
    from bf_core.geostat.simulation.sgs import warm_sgs

    warm_sgs()  # the delegation of ρ = 0 and the SGS of the comparison
    warm_cosgs()


def par(n_real: int, seed: int) -> Any:
    from bf_core.geostat.simulation.sgs import SgsParams

    return SgsParams(sc.model_y(), sc.ellipsoid(), sc.STEPS, seed=seed, n_real=n_real)


def with_gaps(sec: np.ndarray, seed: int = 3) -> np.ndarray:
    out = np.array(sec)
    out[np.random.default_rng(seed).random(out.size) < GAP_SHARE] = np.nan
    return out


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.geostat.simulation import cosgs, sgs

    threads = sc.limit_threads(params)
    n_real = int(params.get("n_real", 20))
    w = sc.World.open(work)
    s = w.samples()
    grid = w.grid()
    c = sc.centres(grid)
    sec = with_gaps(w.secondary(w.nx))
    p = par(n_real, 12345)
    res, sim_s, cpu_s = sc.timed(lambda: cosgs.simulate(p, s, grid, c, sec, None, threads=threads, keep_y=True))
    peak = sc.peak_mb()
    assert res.y is not None and res.secondary_y is not None
    assert res.real.shape == (n_real, grid.n) and bool(np.isfinite(res.real).all()), "a gap or a NaN in a realization"
    have = np.isfinite(res.secondary_y)
    assert res.n_gap == int((~have).sum()) > 0 and res.rho_pairs > 0
    assert res.counts[:, cosgs.C_GAP].min() > 0, "the nodes of the gaps are not counted"
    corr = float(np.mean([np.corrcoef(r[have], res.secondary_y[have])[0, 1] for r in res.y]))
    mean_y, var_y = float(np.mean(res.y)), float(np.var(res.y))
    info: dict[str, Any] = {"threads": threads, "samples": s.n, "nodes": grid.n, "n_real": n_real,
                            "sim_s": round(sim_s, 3), "cpu_s": round(cpu_s, 3), **sc.cost(sim_s, grid.n, n_real),
                            "rho": round(res.rho, 4), "rho_pairs": res.rho_pairs, "corr": round(corr, 4),
                            "mean_y": round(mean_y, 4), "var_y": round(var_y, 4), "gap_nodes": res.n_gap,
                            "degenerate": int(res.counts[:, sgs.C_DEGENERATE].sum()), "peak_mb_run": peak}  # fmt: skip
    assert abs(res.rho - sc.RHO) <= RHO_TOL, (res.rho, sc.RHO)
    assert abs(corr - res.rho) <= CORR_TOL, (corr, res.rho)
    assert abs(mean_y) < 0.15 and 0.8 < var_y < 1.2, (mean_y, var_y)
    del res
    # the cost of the second variable: the SGS of the same data
    base, sgs_s, sgs_cpu = sc.timed(lambda: sgs.simulate(p, s, grid, c, threads=threads))
    info.update(sgs_s=round(sgs_s, 3), cost_ratio=round(cpu_s / max(sgs_cpu, 1e-9), 2))  # of the CPU seconds
    del base
    info["cancel_s"] = round(sc.cancel_seconds(
        lambda tick: cosgs.simulate(p, s, grid, c, sec, None, threads=threads, tick=tick)), 3)  # fmt: skip
    assert info["cancel_s"] <= CANCEL_MAX_S, f"the cancel took {info['cancel_s']} s"
    # the bits: 1 thread = `threads`; ρ = 0 is the SGS
    sub = sc.SMALL
    ss, gs = w.samples(sub), w.grid(sub)
    cs = sc.centres(gs)
    ssec = with_gaps(w.secondary(sub))
    ps = par(4, 99)
    one = cosgs.simulate(ps, ss, gs, cs, ssec, 0.6, threads=1)
    many = cosgs.simulate(ps, ss, gs, cs, ssec, 0.6, threads=threads)
    sc.assert_same(one.real, many.real, f"{sub}×{sub}×1, 1 and {threads} threads")
    zero = cosgs.simulate(ps, ss, gs, cs, ssec, 0.0, threads=threads)
    sc.assert_same(zero.real, sgs.simulate(ps, ss, gs, cs, threads=threads).real, "ρ = 0 and the SGS")
    return info
