"""OR-Tools in the mode «explicit arcs» on a million blocks (ТЗ 8.11.5: «OR-Tools, 1 млн блоков, явные дуги» ≤ 5 min
and ≤ 6 GB; ADR 0028 decisions 14, 31, «Замеры», подшаг 21.6).

The deposit is the one of `pit_spike` (`pit_common.synth`: 45°, K = 8, RF_max = 1,5). The arcs are the CSR of the
predecessors (`graph.predecessors`, the same lists as of a MineLib `.prec`) and go to `SimpleMaxFlow` by portions
(`flow_ref.solve_arcs`). Timed (the building of the graphs and of the economics is not):
  single_s, single_arcs_s   the full graph at RF_max: the arcs and the flow (the arcs are part of single_s)
  shells_s                  25 solutions at RF = 0,30 … 1,50 step 0,05, each on the graph of the cones (the way the
                            shells of 21.7 are solved when OR-Tools is the solver); the arcs and the flow, summed
  shell_max_s               the longest of the 25
  peak_mb                   the peak of the whole process, which goes through the 25 solutions one after another
  criterion_tz              single_s ≤ 300 s, shells_s ≤ 300 s and peak_mb ≤ 6144 MB (the goals of ТЗ; a million only)
What is checked (a failure stops the run): the sets are closed (the kernel of the graph and an independent numpy
check), HPF gives the same value and the same set as the single solution (it runs after the measure of the peak), the
25 sets are nested (a bigger RF never takes a block off), none is empty from RF = 0,3, and the solution on the cones
is the solution of the full graph (RF_max).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc
from . import pit_common as pc

GOAL_S, GOAL_MB = 300.0, 6144.0  # ТЗ 8.11.5: 5 min, 6 GB
NODES_GOAL = 900_000  # `criterion_tz` is asked of a million (a smaller graph passes trivially)


def load() -> None:
    """The kernels compiled or loaded from NUMBA_CACHE_DIR and OR-Tools imported (not timed)."""
    pc._warm()


def prepare(params: dict[str, Any], work: Path) -> None:
    """Nothing is made here: the deposit is a seed and 0,2 s, the graphs are made in `run` and not timed."""


def explicit(g: Any) -> tuple[np.ndarray, float, float, int]:
    """(mined, the seconds of the arcs, of the whole solution, the arcs) of OR-Tools on the CSR of the predecessors."""
    from bf_core.mine import flow_ref as F
    from bf_core.mine import graph as G

    t0 = time.perf_counter()
    indptr, indices = G.predecessors(g)
    arcs_s = time.perf_counter() - t0
    mined = F.solve_arcs(g.value, indptr, indices)
    return mined, arcs_s, time.perf_counter() - t0, int(indices.shape[0])


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.mine import graph as G
    from bf_core.mine import hpf as H

    bc.limit_threads(params)
    s = pc.synth(int(params["nodes"]))
    tab = pc.offsets()
    g, _ = pc.graph(s, pc.RF_MAX)
    info: dict[str, Any] = {"nodes": g.n, "dims": list(g.dims), "offsets": int(tab.shape[0])}
    mined, arcs_s, single_s, arcs = explicit(g)
    single_mb = bc.peak_mb()
    assert G.is_closed(g, mined) and pc.check_closed(g, tab, mined) == 0, "the single pit is not closed"
    value = pc.value_of(g, mined)
    info.update(single_s=round(single_s, 3), single_arcs_s=round(arcs_s, 3), single_mb=single_mb)
    info.update(mined=int(mined.sum()), arcs=arcs)
    hpf = H.solve(g)  # the cross-check; its memory is under the peak of OR-Tools
    assert np.array_equal(hpf, mined) and pc.value_of(g, hpf) == value, "HPF and OR-Tools differ"
    info.update(hpf_equal=True)
    del hpf
    # 25 shells: the graphs of the cones, nested sets
    packed: list[np.ndarray] = []
    total = longest = 0.0
    build_s = 0.0
    for rf in pc.rfs():
        t0 = time.perf_counter()
        sub, ids = pc.graph(s, rf, cones=True)
        build_s += time.perf_counter() - t0
        m, _, solve_s, _ = explicit(sub)
        total += solve_s
        longest = max(longest, solve_s)
        assert G.is_closed(sub, m) and pc.check_closed(sub, tab, m) == 0, f"RF {rf}: not closed"
        if rf >= 0.3:
            assert m.any(), f"RF {rf}: an empty pit"
        full = np.zeros(g.n, dtype=bool)
        full[ids] = m
        if rf == pc.RF_MAX:
            assert np.array_equal(full, mined), "the cones changed the answer"
        packed.append(np.packbits(full))
    wrong = 0
    for lo, hi in zip(packed, packed[1:], strict=False):
        wrong += int(np.count_nonzero(lo & ~hi))  # a block of RF_a that RF_b > RF_a lacks
    assert wrong == 0, f"the shells are not nested: {wrong} bytes of blocks fall out"
    info.update(shells_s=round(total, 3), shell_max_s=round(longest, 3), shells_build_s=round(build_s, 3))
    info.update(nested=True)
    peak = bc.peak_mb()
    info["peak_mb"] = peak
    if g.n >= NODES_GOAL:
        ok = single_s <= GOAL_S and total <= GOAL_S and peak is not None and peak <= GOAL_MB
        info["criterion_tz"] = bool(ok)
    return info
