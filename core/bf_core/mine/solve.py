"""One entry point of the maximum-closure solvers (подшаг 21.7; ТЗ 8.11.3; ADR 0028 decisions 14, 15): `solve(graph)` →
`bool[n]`, the SMALLEST optimal closure, whichever solver answers.

`solver="auto"` is HPF (`hpf.solve`, the main solver); `"hpf"` is the same without the fall-back; `"ortools"` is
`flow_ref.solve` (OR-Tools `SimpleMaxFlow`: the scenario asks for it, or it is the reserve). In `auto` mode a failure of
the optimality certificate of HPF (a RuntimeError — a bug of the solver, never of the data) is logged and the graph is
solved again by OR-Tools: the user gets the same set, only slower. Errors of the user's data (`ApiError`: memory, size)
are never swallowed. Both solvers give the same set of blocks — the smallest optimum is unique (Т8.11-3).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np
from numpy.typing import NDArray

from bf_core.mine import flow_ref, hpf
from bf_core.mine.graph import PitGraph

SOLVERS = ("auto", "hpf", "ortools")
log = logging.getLogger("bf_core.mine.solve")

Tick = Callable[[float], None]


def solve(
    g: PitGraph,
    *,
    solver: str = "auto",
    tick: Tick | None = None,
    portion: int | None = hpf.PORTION,
    max_bytes: int | None = None,
    stats: dict[str, Any] | None = None,
) -> NDArray[np.bool_]:
    """The smallest optimal closure of `g` (bool per node, values — `g.value`).

    `tick(fraction)` is called between the portions of HPF (it may raise to cancel the job) and once at the end for
    OR-Tools (that solver cannot be stopped in the middle). `stats`, if given, gets the counters of HPF and `solver` —
    the one that really answered."""
    if solver not in SOLVERS:
        raise ValueError(f"solver must be one of {SOLVERS}")
    if g.n == 0 or not bool((g.value > 0).any()):  # nothing to mine — no solver is needed
        if stats is not None:
            stats.update(solver="none", nodes=g.n)
        if tick is not None:
            tick(1.0)
        return np.zeros(g.n, dtype=bool)
    if solver != "ortools":
        try:
            mined = hpf.solve(g, portion=portion, tick=tick, max_bytes=max_bytes, stats=stats)
            if stats is not None:
                stats["solver"] = "hpf"
            return mined
        except RuntimeError as e:
            if solver == "hpf":
                raise
            log.error("HPF failed (%s): the graph of %d nodes is solved by OR-Tools", e, g.n)
    mined = flow_ref.solve(g)
    if stats is not None:
        stats.update(solver="ortools", nodes=g.n)
    if tick is not None:
        tick(1.0)
    return mined
