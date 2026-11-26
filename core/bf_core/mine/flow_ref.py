"""The reference and the fallback solver of the maximum closure: OR-Tools `SimpleMaxFlow` (подшаг 21.4; ТЗ 8.11.3;
ADR 0028 decisions 14, 31 and finding 4). Explicit arcs, int64 capacities, up to ~1 million blocks in the graph.

The network (ТЗ 8.11.3): s → b with the capacity v′_b for v′_b > 0, b → t with −v′_b for v′_b < 0, b → each
predecessor of b with the «infinity» Σ_(v′>0) v′ + 1. The pit is the set of nodes reachable from s in the RESIDUAL
network (`get_source_side_min_cut`): the SMALLEST optimal closure, unique, so it is the same as the answer of HPF and
of the references of the tests. The optimum is Σ v′ over it (= Σ_(v′>0) v′ − the maximum flow).

Input is the same for the grid graph and for «explicit arcs» (MineLib `.prec`): `value` (int64 per node) and the CSR of
the predecessors (`indptr` int64 (n + 1), `indices` int32). The arcs go to OR-Tools by portions (`chunk` arcs), so the
Python-side arrays never exceed one portion. Errors of the solver are RuntimeError (a bug or an overflow, never data).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from bf_core.mine.graph import PitGraph, predecessors

CHUNK = 2_000_000  # arcs given to OR-Tools per call
MAX_ABS_SUM = 2**62  # Σ|v′| above this could overflow int64 in the solver


def infinity(value: NDArray[np.int64]) -> int:
    """The capacity of a precedence arc: Σ_(v′>0) v′ + 1 (more than any cut that does not use such an arc)."""
    v = np.asarray(value, dtype=np.int64)
    return int(v[v > 0].sum()) + 1


def solve_arcs(
    value: NDArray[np.int64],
    indptr: NDArray[np.int64],
    indices: NDArray[np.int32],
    *,
    chunk: int = CHUNK,
) -> NDArray[np.bool_]:
    """The smallest optimal closure (bool per node) of the arcs u → `indices[indptr[u]:indptr[u + 1]]`."""
    from ortools.graph.python import max_flow

    v = np.asarray(value)
    if v.ndim != 1 or not np.issubdtype(v.dtype, np.integer):
        raise ValueError("value must be a 1-D integer array")
    v = v.astype(np.int64, copy=False)
    n = len(v)
    if len(indptr) != n + 1 or int(indptr[-1]) != len(indices):
        raise ValueError("indptr does not match value and indices")
    if n == 0 or not (v > 0).any():
        return np.zeros(n, dtype=bool)
    if int(np.abs(v).sum()) >= MAX_ABS_SUM:
        raise ValueError("the sum of |v′| is too large for int64 capacities")
    s, t = n, n + 1
    inf = infinity(v)
    mf = max_flow.SimpleMaxFlow()
    pos = np.flatnonzero(v > 0)
    neg = np.flatnonzero(v < 0)
    mf.add_arcs_with_capacity(np.full(len(pos), s, dtype=np.int32), pos.astype(np.int32), v[pos])
    mf.add_arcs_with_capacity(neg.astype(np.int32), np.full(len(neg), t, dtype=np.int32), -v[neg])
    mf.add_arc_with_capacity(t, s, 0)  # t must exist as a node even when no v′ < 0 (else the sink is unknown)
    lo = 0
    while lo < n:  # the nodes [lo; hi) hold about `chunk` arcs
        hi = int(np.searchsorted(indptr, indptr[lo] + max(1, chunk), side="right")) - 1
        hi = min(n, max(hi, lo + 1))
        a, b = int(indptr[lo]), int(indptr[hi])
        if b > a:
            tails = np.repeat(np.arange(lo, hi, dtype=np.int32), np.diff(indptr[lo : hi + 1]))
            heads = np.asarray(indices[a:b], dtype=np.int32)
            mf.add_arcs_with_capacity(tails, heads, np.full(b - a, inf, dtype=np.int64))
        lo = hi
    status = mf.solve(s, t)
    if status != max_flow.SimpleMaxFlow.Status.OPTIMAL:
        raise RuntimeError(f"OR-Tools SimpleMaxFlow ended with {status}")
    out = np.zeros(n + 2, dtype=bool)
    out[np.asarray(mf.get_source_side_min_cut(), dtype=np.int64)] = True
    return out[:n]


def solve(g: PitGraph, *, chunk: int = CHUNK) -> NDArray[np.bool_]:
    """The smallest optimal closure of a grid graph (bool per node); arcs are made from the offset tables."""
    indptr, indices = predecessors(g)
    return solve_arcs(g.value, indptr, indices, chunk=chunk)


def optimum(value: NDArray[np.int64], mined: NDArray[np.bool_]) -> int:
    """Σ v′ over the pit (exact, int64)."""
    return int(np.asarray(value, dtype=np.int64)[np.asarray(mined, dtype=bool)].sum())
