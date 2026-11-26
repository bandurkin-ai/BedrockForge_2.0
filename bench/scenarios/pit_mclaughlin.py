"""The real deposit: McLaughlin (MineLib) as a block model of 2,8 million blocks (2,1 million of rock) — the time and
memory of the pit optimiser on it (ТЗ 8.11.5, 7.35 Д-21; ADR 0028 «Замеры», подшаг 21.13). Only the profile `full`
(ext-data: `tests/data/ext/omf/minelib/McLaughlin_tutorial.omf`; the file is read with `core/tests/ref/mclaughlin.py`,
which belongs to the tests). The price of gold and the unit of `au` are ASSUMPTIONS (question № 116, the scenario file
`core/tests/data/pit/mclaughlin.json`): the numbers of the time are real, the sizes of the pits are those of the
assumptions. The reference of the goals is the one of 5 million blocks: ≤ 20 min and ≤ 4 GB.

Timed (the economics of the blocks is part of the stages that need it):
  read_s         the OMF 2 read (zip, Parquet) and the arrays of the model — not a goal, a size of the work
  graph_s        the full graph of the 2,1 million nodes at RF = 1 (the integers of `to_int`, the offsets table)
  single_s       HPF on that graph, one solution (the single pit; `hpf.solve`)
  cones_graph_s  the same on the cones of the ore (the way the shells are solved) and cones_nodes
  shells_s       the REAL `shells.shells` over the 61 factors of the scenario (0,2 … 1,7 step 0,025) on the full graph
  ortools_s      OR-Tools, explicit arcs, on the cones at RF = 1 (the cross-check; its arcs are part of it)
  peak_mb        the peak of the whole process; single_mb — after the single solution; shells_mb — after the shells
  criterion_ref  shells_s ≤ 1200 s and peak_mb ≤ 4096 MB (the goals of 5 million blocks)
What is checked (a failure stops the run): the pit of the single solution is closed (the kernel of the graph and an
independent numpy check); the shells are nested and closed (`shells` checks every one on the whole graph and fails
with `pit_internal`); the cones do not change the answer (HPF on the cones, put back, is the single pit); HPF and
OR-Tools give the same set and the same value on the cones.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

from . import blocks_common as bc
from . import pit_common as pc

GOAL_S, GOAL_MB = 1200.0, 4096.0  # as for 5 million blocks
ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / "core" / "tests" / "ref" / "mclaughlin.py"
RF_SINGLE = 1.0


def _ref() -> ModuleType:
    """`core/tests/ref/mclaughlin.py` by its path (`tests` is not a package of the bench's `sys.path`)."""
    name = "bf_mclaughlin_ref"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REF)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load() -> None:
    """The kernels compiled or loaded from NUMBA_CACHE_DIR and OR-Tools imported (not timed)."""
    pc._warm()


def prepare(params: dict[str, Any], work: Path) -> None:
    """Nothing is made: the model is read from the file in `run` (its time is a stage of the report)."""
    m = _ref()
    if not m.OMF.is_file():
        raise FileNotFoundError(f"нет файла McLaughlin: {m.OMF} (tests/data/ext/omf/minelib)")


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.mine import flow_ref as F
    from bf_core.mine import graph as G
    from bf_core.mine import hpf as H
    from bf_core.mine import shells as SH

    threads = bc.limit_threads(params)
    M = _ref()
    t0 = time.perf_counter()
    model = M.read_omf2()
    M.sanity(model)
    sc = M.scenario()
    read_s = time.perf_counter() - t0
    air = model.air
    rock = ~air
    tab = M.offsets(model, sc)
    info: dict[str, Any] = {"blocks": model.blocks, "nodes": int(rock.sum()), "dims": list(model.dims),
                            "offsets": int(tab.shape[0]), "threads": threads, "read_s": round(read_s, 3)}  # fmt: skip

    t0 = time.perf_counter()
    full, _ = M.graph(model, sc, RF_SINGLE, cones=False)
    info["graph_s"] = round(time.perf_counter() - t0, 3)
    t0 = time.perf_counter()
    single = H.solve(full)
    info["single_s"] = round(time.perf_counter() - t0, 3)
    info["single_mb"] = bc.peak_mb()
    assert G.is_closed(full, single) and pc.check_closed(full, tab, single) == 0, "the single pit is not closed"
    value = pc.value_of(full, single)
    info.update(single_mined=int(single.sum()), single_value=value)

    t0 = time.perf_counter()
    cones, ids = M.graph(model, sc, RF_SINGLE, cones=True)
    info.update(cones_graph_s=round(time.perf_counter() - t0, 3), cones_nodes=cones.n)
    t0 = time.perf_counter()
    on_cones = H.solve(cones)
    info["cones_hpf_s"] = round(time.perf_counter() - t0, 3)
    back = np.zeros(full.n, dtype=bool)
    back[ids] = on_cones
    assert np.array_equal(back, single), "the cones changed the answer"
    del back, single

    # the real shells over the 61 factors of the scenario, on the full graph (values: a function RF → the nodes)
    rfs = M.rfs(sc)
    stats: dict[str, Any] = {}
    t0 = time.perf_counter()
    res = SH.shells(full, lambda rf: M.cell_values(model, sc, rf)[rock], rfs, solver="hpf", threads=threads,
                    stats=stats)  # fmt: skip
    info["shells_s"] = round(time.perf_counter() - t0, 3)
    info["shells_mb"] = bc.peak_mb()
    solves = stats.get("solves")
    info.update(shells=res.n_shells, shells_solves=len(solves) if isinstance(solves, list) else solves, shells_q=res.q)
    sizes = res.blocks
    assert bool(np.all(np.diff(sizes) >= 0)), "the shells are not nested"
    assert int(sizes[-1]) > int(sizes[0]) > 0, "the shells do not grow with RF"
    info.update(shell_min_blocks=int(sizes[0]), shell_max_blocks=int(sizes[-1]), nested=True)
    del res

    if params.get("ortools", True):
        t0 = time.perf_counter()
        indptr, indices = G.predecessors(cones)
        arcs_s = time.perf_counter() - t0
        ref = F.solve_arcs(cones.value, indptr, indices)
        info.update(ortools_s=round(time.perf_counter() - t0, 3), ortools_arcs_s=round(arcs_s, 3),
                    ortools_arcs=int(indices.shape[0]))  # fmt: skip
        assert np.array_equal(ref, on_cones) and pc.value_of(cones, ref) == pc.value_of(cones, on_cones), (
            "HPF and OR-Tools differ"
        )
        info["hpf_equal"] = True
    peak = bc.peak_mb()
    info["peak_mb"] = peak
    info["criterion_ref"] = bool(info["shells_s"] <= GOAL_S and peak is not None and peak <= GOAL_MB)
    return info
