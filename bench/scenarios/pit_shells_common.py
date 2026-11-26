"""The 25 shells of the pit on a big graph: the real `shells.shells` (ТЗ 8.11.3, 8.11.5: 25 оболочек на 1 млн блоков
≤ 3 мин и ≤ 2 ГБ, на 5 млн ≤ 20 мин и ≤ 4 ГБ; ADR 0028 «Замеры», подшаг 21.14). The body of the scenarios
`pit_shells_1m` and `pit_shells_5m` (they differ in the parameters only).

The deposit is `pit_common.synth` (an ellipsoidal body, lognormal grades, ρ 2,7, cubes of 10 m, 45°, K = 8: the real
template of 21.3, 17 offsets). Its economics is the one of the production: a real `econ_table.Table` (A, D, C_m of a
node; `Table.values` is the kernel `node_values` on strips of threads, the values the pass of `pit.run` gives the
shells) with A = TONNES·PRICE·g, D = TONNES·C_P, C_m = TONNES·(C_M0 + C_M_DEPTH·depth) — the same v as
`pit_common.cell_values`. `prepare` makes the deposit, the table and the graph (not timed, in the parent; the child
only reads the files, so the peak of the run is the peak of the shells and of what they need).
Timed: `shells.shells` with the solver `auto` (HPF) on 25 factors 0,30 … 1,50 step 0,05.
  shells_s                  the call (`limits`); values_s, cones_s, solve_s, check_s — its parts (`Shells.stats`)
  shells_mb, load_mb        the peak resident set after the shells and the set before them (the files are read in):
                            `shells_extra_mb` is what the shells need; the Python, numpy and numba are ≈ 250 MB
  solves, first_nodes       the solutions made (RF_max, RF_min and the bisection) and the nodes of the first (the cones)
  nodes_sum_m               Σ nodes of all the solutions in millions: the work of the bisection (25 solves on the whole
                            graph would be 25 × nodes)
  criterion_tz              shells_s ≤ `goal_s` and shells_mb ≤ `goal_mb` (the goals of ТЗ; the full profile only)
What is checked (a failure stops the run, the checks are not timed): the labels are 1…25, none is empty from RF = 0,3;
C(RF_k) ⊆ C(RF_(k+1)) on the masks; every shell is closed — by the independent numpy check of `pit_common.check_closed`
(it shares no code with the kernels); the pit is neither trivial (5…60 % of the nodes) nor cut by the edge of the model;
the shells `scratch` equal the HPF solutions from scratch on the whole graph (the bisection finds the same sets); with
`ortools` the set and the value of C(RF_max) equal the OR-Tools (explicit arcs, a child process of its own).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc
from . import pit_common as pc


def load() -> None:
    """The kernels compiled or loaded from NUMBA_CACHE_DIR (not timed): the warm-up of the module, then the solvers."""
    from bf_core.mine.warm import warm

    warm()
    pc._warm()


def _control() -> Any:
    """«Контроль» of the table: the shells do not read it."""
    from bf_core.mine import econ

    return econ.Control(["AU"], ["rock"], np.zeros(8, dtype=np.int64), np.zeros((8, 2)),
                        np.zeros((1, 2), dtype=np.int64), np.zeros((2, 2)))  # fmt: skip


def table_of(s: Any) -> Any:
    """`econ_table.Table` of the deposit: one row per node (the non-air cells in the order of P)."""
    from bf_core.mine import econ_table

    keep = ~s.air
    n = int(np.count_nonzero(keep))
    a = s.grade[keep] * (pc.PRICE * pc.TONNES)
    d = np.full(n, pc.C_P * pc.TONNES)
    cm = (pc.C_M0 + pc.C_M_DEPTH * np.maximum(s.depth[keep], 0.0)) * pc.TONNES
    ore = (a > d).astype(np.uint8)
    return econ_table.Table(np.flatnonzero(keep).astype(np.uint64), np.arange(n + 1, dtype=np.int64), a, d, cm, ore,
                            np.arange(n, dtype=np.int32), ore, _control())  # fmt: skip


def prepare(params: dict[str, Any], work: Path) -> None:
    """The graph (its structure; values 0) and the table of the deposit go to files, the way a pass of `pit.run` has
    them in memory when the shells start."""
    from bf_core.mine import graph as G

    s = pc.synth(int(params["nodes"]))
    tab = table_of(s)
    total = int(np.prod(s.dims))
    g = G.build_graph(s.dims, np.zeros(total, dtype=np.int64), s.air, None, [pc.offsets()])
    assert g.n == tab.n_nodes == s.nodes
    pc.save_graph(work / "graph.npz", g)
    np.savez(work / "table.npz", p=tab.p, a=tab.a, d=tab.d, cm=tab.cm, ore=tab.ore)


def load_table(work: Path) -> Any:
    from bf_core.mine import econ_table

    z = np.load(work / "table.npz")
    n = int(z["p"].shape[0])
    return econ_table.Table(z["p"], np.arange(n + 1, dtype=np.int64), z["a"], z["d"], z["cm"], z["ore"],
                            np.arange(n, dtype=np.int32), z["ore"], _control())  # fmt: skip


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.mine import graph as G
    from bf_core.mine import hpf as H
    from bf_core.mine import shells as S

    threads = bc.limit_threads(params)
    g, _ = pc.load_graph(work / "graph.npz")
    tab = load_table(work)
    tab_off = np.ascontiguousarray(g.tab_off)
    rfs = pc.rfs()
    n_sh = int(params.get("shells", len(rfs)))
    rfs = rfs[:n_sh] if n_sh <= len(rfs) else [round(0.30 + 0.05 * i, 2) for i in range(n_sh)]
    info: dict[str, Any] = {"nodes": g.n, "dims": list(g.dims), "shells": n_sh, "offsets": int(tab_off.shape[0]),
                            "threads": threads}  # fmt: skip
    load_mb = pc._rss_mb()[0]
    ticks = [0]

    def tick(_done: int, _total: int, _text: str) -> None:
        ticks[0] += 1

    t0 = time.perf_counter()
    res = S.shells(g, tab, rfs, solver="auto", tick=tick, threads=threads)
    shells_s = time.perf_counter() - t0
    peak_mb = pc._rss_mb()[1]  # the peak of the process: the files read and the shells (the checks come after)
    sec = res.stats["seconds"]
    solves = res.stats["solves"]
    info.update(shells_s=round(shells_s, 3), shells_mb=round(peak_mb, 1), load_mb=round(load_mb, 1),
                shells_extra_mb=round(peak_mb - load_mb, 1), solver=res.solver, ticks=ticks[0])  # fmt: skip
    info.update({f"{k}_s": round(float(v), 3) for k, v in sec.items() if k != "total"})
    info.update(solves=len(solves), first_nodes=int(solves[0]["nodes"]), q=res.q,
                nodes_sum_m=round(sum(int(x["nodes"]) for x in solves) / 1e6, 2),
                longest_solve_s=round(max(float(x["seconds"]) for x in solves), 3),
                blocks_first=int(res.blocks[0]), blocks_last=int(res.blocks[-1]))  # fmt: skip

    # --- the checks (not timed) ---
    assert res.pit.dtype == np.int16 and int(res.pit.max()) == n_sh and res.n_shells == n_sh, "the labels"
    assert int(res.blocks[0]) > 0 and bool(np.all(np.diff(res.blocks) >= 0)), "an empty shell, or one that shrinks"
    prev = np.zeros(g.n, dtype=bool)
    bad = 0
    for k in range(n_sh):
        m = res.mask(k)
        assert not bool((prev & ~m).any()), f"C(RF_{k}) is not inside C(RF_{k + 1})"  # nested
        assert int(m.sum()) == int(res.blocks[k])
        bad += pc.check_closed(g, tab_off, m)  # independent of the kernels
        prev = m
    assert bad == 0, f"{bad} violations of the closure"
    info["closed"] = info["nested"] = True
    last = res.mask(n_sh - 1)
    share = float(last.sum()) / g.n
    assert 0.05 < share < 0.60, f"a trivial problem: {share:.1%} of the nodes mined"
    nx, ny, _ = g.dims
    p = G.parent_index(g)[last]
    i, j, k = p % nx, (p // nx) % ny, p // (nx * ny)
    rim = float(np.count_nonzero((i == 0) | (j == 0) | (i == nx - 1) | (j == ny - 1))) / max(1, p.size)
    assert rim < 0.05 and not np.any(k == 0), f"the pit is cut by the edge of the model ({rim:.1%} on the rim)"
    info.update(mined_share=round(share, 4), rim_share=round(rim, 4))

    # the shells from scratch (HPF on the whole graph at the same integers): the bisection finds the same sets
    for idx in params.get("scratch", []):
        idx = int(idx)
        v = S.scale(tab.values(float(res.rf[idx]), threads=threads), res.q)
        t1 = time.perf_counter()
        got = H.solve(g.with_values(v))
        info[f"scratch_{idx}_s"] = round(time.perf_counter() - t1, 3)
        assert np.array_equal(got, res.mask(idx)), f"the shell {idx + 1} differs from the solution from scratch"
        assert int(v[got].sum()) == int(res.objective[idx]), f"the value of the shell {idx + 1}"
        del v, got
    info["scratch_equal"] = True

    # OR-Tools on C(RF_max): the same value and the same set (explicit arcs, in a child process: its own peak)
    if params.get("ortools"):
        v = S.scale(tab.values(float(res.rf[-1]), threads=threads), res.q)
        gq = g.with_values(v)
        pc.save_graph(work / "rfmax.npz", gq)
        ort = pc.solve_files("ortools", [work / "rfmax.npz"], work, threads=threads, tag="_rfmax")
        mo, _ = pc.load_mask(ort["mask"])
        o = ort["results"][0]
        assert np.array_equal(mo, last), f"OR-Tools differs from RF_max in {int(np.count_nonzero(mo != last))} nodes"
        assert o["value"] == int(res.objective[-1]) == int(v[last].sum()), (
            o["value"],
            int(res.objective[-1]),
        )
        assert o["violations"] == o["violations_numpy"] == 0
        info.update(
            ortools_equal=True,
            ortools_s=round(o["seconds"], 3),
            ortools_mb=ort["peak_mb"],
        )
        del v, gq

    if "goal_s" in params:  # the goals of ТЗ 8.11.5 (a million and five million nodes; the ci profile has none)
        goal_s, goal_mb = float(params["goal_s"]), float(params["goal_mb"])
        info.update(
            goal_s=goal_s,
            goal_mb=goal_mb,
            criterion_tz=bool(shells_s <= goal_s and peak_mb <= goal_mb),
        )
    return info
