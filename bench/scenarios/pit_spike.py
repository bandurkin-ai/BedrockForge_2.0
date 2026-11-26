"""The early measure of the pit solvers: ONE solution at RF_max on the full graph of about a million blocks, HPF
against OR-Tools (ADR 0028 decision 15, «Замеры», подшаг 21.6; ТЗ 8.11.3, 8.11.5).

The deposit is `pit_common.synth`: an ellipsoidal body, lognormal grades, ρ 2,7, cubes of 10 m, 45°, K = 8 (the real
template of 21.3, 17 offsets), one rock and «air» above a hilly relief, RF_max = 1,5. Each solver runs in a CHILD
process of its own (its peak memory is its own); the graph is built and written in `prepare` (not timed).
  hpf_s, ortools_s       the solution (OR-Tools: the arcs of `predecessors` and the flow; `ortools_arcs_s` — the arcs)
  hpf_mb, ortools_mb     the peak resident set of the child, with the Python, numpy and numba of ≈ 250 MB
  hpf_extra_mb, …        the same less the set before the solution (the graph is in): what the solver itself needs
  hpf_small_s, …         the same on a graph of a fifth of the nodes: the exponent of the time, the forecast of 5 m
  forecast_5m_s / _mb    HPF at five times the nodes: the time by the power law of the two sizes, the memory — linear
  forecast_25_s          HPF, 25 shells: the time of HPF on the graphs of the cones at RF = 0,3 … 1,5 (five points,
                         linear between), summed over the 25 factors 0,30 … 1,50; no warm start, no cones in the sum
  forecast_25_ortools_s  25 × the OR-Tools solution (an upper bound: the lower RF are smaller graphs)
What is checked (a failure stops the run): HPF and OR-Tools give the SAME value and the SAME set of blocks (the smallest
optimal closure is unique), the set is closed — by the kernel of the graph and by an independent numpy check — and its
value is the one recomputed here from the graph; the pit is not cut by the edge of the model (< 5 % of its blocks on the
rim, the bottom is free) and is not trivial (5…60 % of the blocks); the cones give the same set as the full graph;
every graph of the shell forecast is solved to a closed set. `params["hard"]` — kinds of `pit_common.KINDS`
(marginal, nugget, noise): the same checks and the times of both solvers on harder data (`criterion_15_hard`).
Criterion of the decision 15: HPF ≤ 20 s and ≤ 0,8 GB on a million and = OR-Tools → HPF is the main solver
(`criterion_15`, only for 900 thousand nodes and more).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc
from . import pit_common as pc

SAMPLES = [0.30, 0.60, 0.90, 1.20, 1.50]  # the factors of the forecast of 25 shells (RF_max last)
SMALL = 5  # the small graph has 1/SMALL of the nodes
HPF_S, HPF_MB = 20.0, 800.0  # the criterion of the decision 15 (a million nodes)


def load() -> None:
    """The kernels compiled or loaded from NUMBA_CACHE_DIR (not timed); the children load them again, untimed."""
    pc._warm()


def prepare(params: dict[str, Any], work: Path) -> None:
    bc.limit_threads(params)
    n = int(params["nodes"])
    s = pc.synth(n)
    g, _ = pc.graph(s, pc.RF_MAX)
    pc.save_graph(work / "full.npz", g)
    small, _ = pc.graph(pc.synth(max(1000, n // SMALL)), pc.RF_MAX)
    pc.save_graph(work / "small.npz", small)
    for rf in SAMPLES:
        sub, ids = pc.graph(s, rf, cones=True)
        pc.save_graph(work / f"shell_{rf:.2f}.npz", sub, ids)
    for kind in params.get("hard", []):  # «hard» deposits of the same size: `pit_common.KINDS`
        for size, tag in ((n, ""), (max(1000, n // SMALL), "_small")):
            hard, _ = pc.graph(pc.synth(size, kind=kind), pc.RF_MAX)
            pc.save_graph(work / f"hard_{kind}{tag}.npz", hard)


def interpolated_sum(times: dict[float, float]) -> float:
    """Σ over the 25 factors of the time linearly interpolated between the sampled factors."""
    xs = sorted(times)
    return float(sum(np.interp(rf, xs, [times[x] for x in xs]) for rf in pc.rfs()))


def hard_deposits(params: dict[str, Any], work: Path, threads: int, info: dict[str, Any]) -> bool:
    """HPF and OR-Tools on the «hard» deposits (`params["hard"]`, kinds of `pit_common.KINDS`), each at the full size
    and at a fifth of it: the same value and set, closed; the times, the exponent, the forecast of 5 million (HPF) and
    the upper bound of 25 shells (25 × the solution at RF_max) go to `info`. True: HPF within the criterion."""
    kinds = list(params.get("hard", []))
    if not kinds:
        return True
    files = [work / f"hard_{k}{t}.npz" for t in ("", "_small") for k in kinds]
    hh = pc.solve_files("hpf", files, work, threads=threads, tag="_hard")
    oo = pc.solve_files("ortools", files, work, threads=threads, tag="_hard")
    ok = hh["peak_mb"] <= HPF_MB
    for n, (a, b) in enumerate(zip(hh["results"], oo["results"], strict=True)):
        ma, _ = pc.load_mask(hh["mask"], n)
        mb, _ = pc.load_mask(oo["mask"], n)
        assert np.array_equal(ma, mb) and a["value"] == b["value"], f"HPF and OR-Tools differ on {a['file']}"
        assert a["violations"] == a["violations_numpy"] == b["violations"] == b["violations_numpy"] == 0, a["file"]
    for n, kind in enumerate(kinds):
        a, b, small = hh["results"][n], oo["results"][n], hh["results"][n + len(kinds)]
        exp = math.log(max(a["seconds"], 1e-3) / max(small["seconds"], 1e-3)) / math.log(a["nodes"] / small["nodes"])
        info[f"hard_{kind}_hpf_s"] = round(a["seconds"], 3)
        info[f"hard_{kind}_ortools_s"] = round(b["seconds"], 3)
        info[f"hard_{kind}_mined_share"] = round(a["mined"] / a["nodes"], 4)
        info[f"hard_{kind}_exponent"] = round(exp, 2)
        info[f"hard_{kind}_forecast_5m_s"] = round(a["seconds"] * 5.0**exp, 1)
        info[f"hard_{kind}_upper_25_s"] = round(25.0 * a["seconds"], 1)
        ok = ok and a["seconds"] <= HPF_S
    info["hard_hpf_mb"], info["hard_ortools_mb"] = hh["peak_mb"], oo["peak_mb"]
    info["peak_mb"] = max(info.get("peak_mb", 0.0), oo["peak_mb"])
    return bool(ok)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.mine import graph as G

    threads = bc.limit_threads(params)
    g, _ = pc.load_graph(work / "full.npz")
    tab = np.ascontiguousarray(g.tab_off)
    info: dict[str, Any] = {"nodes": g.n, "dims": list(g.dims), "k": pc.K, "offsets": int(tab.shape[0])}
    hpf = pc.solve_files("hpf", [work / "full.npz"], work, threads=threads)
    ort = pc.solve_files("ortools", [work / "full.npz"], work, threads=threads, tag="")
    h, o = hpf["results"][0], ort["results"][0]
    mh, _ = pc.load_mask(hpf["mask"])
    mo, _ = pc.load_mask(ort["mask"])
    assert np.array_equal(mh, mo), f"HPF and OR-Tools differ in {int(np.count_nonzero(mh != mo))} blocks"
    assert h["value"] == o["value"] == pc.value_of(g, mh), (h["value"], o["value"])
    assert h["violations"] == o["violations"] == h["violations_numpy"] == o["violations_numpy"] == 0, (h, o)
    assert pc.check_closed(g, tab, mh) == 0 and G.is_closed(g, mh)
    share = float(np.count_nonzero(mh)) / g.n
    assert 0.05 < share < 0.60, f"a trivial problem: {share:.1%} of the blocks mined"
    nx, ny, nz = g.dims
    p = G.parent_index(g)[mh]
    i, j, k = p % nx, (p // nx) % ny, p // (nx * ny)
    rim = (i == 0) | (j == 0) | (i == nx - 1) | (j == ny - 1)  # lone shallow ore of the halo may lie there
    edge = float(np.count_nonzero(rim)) / max(1, p.size)
    assert edge < 0.05, f"the pit is cut by the edge of the model: {edge:.1%} of its blocks on the rim"
    assert not np.any(k == 0), "the pit reaches the bottom of the model"
    info.update(mined=h["mined"], mined_share=round(share, 4), edge_share=round(edge, 4), value=h["value"])
    info.update(equal=True, closed=True)
    info.update(hpf_s=round(h["seconds"], 3), hpf_mb=hpf["peak_mb"])
    info.update(hpf_extra_mb=round(hpf["peak_mb"] - hpf["rss_before_mb"], 1))
    info.update(ortools_s=round(o["seconds"], 3), ortools_arcs_s=round(o["arcs_s"], 3), ortools_mb=ort["peak_mb"])
    info.update(ortools_extra_mb=round(ort["peak_mb"] - ort["rss_before_mb"], 1), ortools_arcs=o["stats"]["arcs"])
    info["hpf_work"] = h["stats"].get("work")
    # the same on a fifth of the nodes: the exponent of the time and the forecast of 5 million
    sh = pc.solve_files("hpf", [work / "small.npz"], work, threads=threads, tag="_small")
    so = pc.solve_files("ortools", [work / "small.npz"], work, threads=threads, tag="_small")
    a, b = sh["results"][0], so["results"][0]
    ma, _ = pc.load_mask(sh["mask"])
    mb, _ = pc.load_mask(so["mask"])
    assert np.array_equal(ma, mb) and a["value"] == b["value"], "HPF and OR-Tools differ on the small graph"
    assert a["violations"] == a["violations_numpy"] == b["violations"] == b["violations_numpy"] == 0
    ratio = g.n / a["nodes"]
    exp_h = math.log(max(h["seconds"], 1e-3) / max(a["seconds"], 1e-3)) / math.log(ratio)
    exp_o = math.log(max(o["seconds"], 1e-3) / max(b["seconds"], 1e-3)) / math.log(ratio)
    slope_mb = (hpf["peak_mb"] - sh["peak_mb"]) / (g.n - a["nodes"])  # MB per node of HPF
    slope_o = (ort["peak_mb"] - so["peak_mb"]) / (g.n - a["nodes"])
    info.update(hpf_small_s=round(a["seconds"], 3), ortools_small_s=round(b["seconds"], 3), small_nodes=a["nodes"])
    info.update(hpf_exponent=round(exp_h, 2), ortools_exponent=round(exp_o, 2))
    info.update(forecast_5m_s=round(h["seconds"] * 5.0**exp_h, 1))
    info.update(forecast_5m_mb=round(hpf["peak_mb"] + slope_mb * 4 * g.n, 0))
    info.update(forecast_5m_ortools_s=round(o["seconds"] * 5.0**exp_o, 1))
    info.update(forecast_5m_ortools_mb=round(ort["peak_mb"] + slope_o * 4 * g.n, 0))
    # the forecast of 25 shells: HPF on the graphs of the cones at five factors
    files = [work / f"shell_{rf:.2f}.npz" for rf in SAMPLES]
    sm = pc.solve_files("hpf", files, work, threads=threads, tag="_shells")
    times: dict[float, float] = {}
    for n, (rf, r) in enumerate(zip(SAMPLES, sm["results"], strict=True)):
        assert r["violations"] == r["violations_numpy"] == 0, (rf, r)
        times[rf] = r["seconds"]
        info[f"shell_{rf:.2f}_s"] = round(r["seconds"], 3)
        info[f"shell_{rf:.2f}_nodes"] = r["nodes"]
        if rf == pc.RF_MAX:  # the cones do not change the optimum: the same set as on the full graph
            ms, ids = pc.load_mask(sm["mask"], n)
            back = np.zeros(g.n, dtype=bool)
            back[ids] = ms
            assert np.array_equal(back, mh), "the cones changed the answer"
            info["cones_equal"] = True
    info["forecast_25_s"] = round(interpolated_sum(times), 1)
    info["forecast_25_ortools_s"] = round(25.0 * o["seconds"], 1)
    info["shells_mb"] = sm["peak_mb"]
    info["peak_mb"] = max(hpf["peak_mb"], ort["peak_mb"])
    hard_ok = hard_deposits(params, work, threads, info)
    if g.n >= 900_000:
        info["criterion_15"] = bool(h["seconds"] <= HPF_S and hpf["peak_mb"] <= HPF_MB)
        if params.get("hard"):
            info["criterion_15_hard"] = hard_ok
    return info
