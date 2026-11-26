"""The HPF solver of the maximum closure (подшаг 21.5; ТЗ 8.11.3; ADR 0028 decisions 14–16): Hochbaum's pseudoflow with
a normalised forest and the lowest label first, over a grid graph (`solve`) or over explicit arcs (`solve_arcs`).

The interface is the one of `flow_ref.solve`: a graph (or `value` and the CSR of the predecessors) in, `bool[n]` out —
the SMALLEST optimal closure (the unique one, the same set as the OR-Tools answer and as the references of the tests).
The values v′ are int64; the «infinity» of a precedence arc is implicit (the solver never saturates it).

The arcs are not stored (`hpf_kernels`): the predecessors of a node are built from its offset table and the dense
index of the graph. The memory is ≈ 70 bytes per node on top of the graph (a 1-million-node pit — 70 MB).

Work by portions. The kernel stops after the root it is processing when `portion` units of work are spent and returns
its whole state in the arrays; between two portions `tick(fraction)` is called in the calling thread (it may raise to
cancel the job — `JobCancelled`), the progress is `1 − (the excess still active) / (the total supply)` and the
memory is checked against `max_bytes`. The size of a portion does not change a bit of the answer (the tests: 1, 7, ∞).

`portion=None` is the whole solution in one call. The answer is checked by a label-free CERTIFICATE: the value of the
closure of the strong roots equals the dual bound Σ (excess of the roots) — so it is optimal (and the smallest one);
a failure is a RuntimeError (a bug, never data). `BF_PIT_DEBUG=1` also checks the state (`check_state`): the flow is
kept on the arcs of the forest only and every excess agrees with the flows.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import numpy as np
from numpy.typing import NDArray

from bf_core.api.errors import ApiError
from bf_core.mine.graph import PitGraph

PORTION = 2_000_000  # units of work (nodes visited + arcs pushed along) between two ticks: well under a second
MAX_ABS_SUM = 2**62  # Σ|v′| at or above this could overflow the int64 excess
BYTES_PER_NODE = 76  # the state of the solver: a32 32 + a64 16 + fl 2 + cnt, head, stack, vis, queue 20 + mask 1 + …

Tick = Callable[[float], None]
Mask = NDArray[np.bool_]


def estimate_bytes(n: int) -> int:
    """The memory the solver needs for n nodes on top of the graph and of the values."""
    return int(n + 1) * BYTES_PER_NODE


def _check_memory(n: int, max_bytes: int | None) -> None:
    need = estimate_bytes(n)
    avail = None
    try:
        import psutil

        avail = int(psutil.virtual_memory().available)
    except Exception:  # noqa: BLE001 — psutil is a convenience here, never a reason to fail
        pass
    limit = max_bytes if avail is None else (avail if max_bytes is None else min(max_bytes, avail))
    if limit is not None and need > limit:
        raise ApiError(
            422,
            "pit_too_large",
            f"Для оптимизации карьера не хватает памяти: нужно {need // 2**20} МБ, доступно {limit // 2**20} МБ.",
            "Укрупните модель регуляризацией (Блочные модели → Регуляризация) или закройте другие программы.",
            {"need_bytes": need, "limit_bytes": int(limit)},
        )


def _empty_graph_arrays() -> tuple[Any, ...]:
    return (
        np.zeros(1, dtype=np.int32),  # pos
        np.zeros(1, dtype=np.uint8),  # tpl
        np.zeros(1, dtype=np.int32),  # index
        np.zeros(2, dtype=np.int64),  # tab_ptr
        np.zeros((1, 3), dtype=np.int32),  # tab_off
        1,
        1,
        1,
    )


def solve(
    g: PitGraph,
    *,
    portion: int | None = PORTION,
    tick: Tick | None = None,
    max_bytes: int | None = None,
    stats: dict[str, Any] | None = None,
) -> Mask:
    """The smallest optimal closure of a grid graph (bool per node); `stats`, if given, gets the counters."""
    pos, tpl, index, tab_ptr, tab_off, mx, my, mz = g.kernel_args
    gr = (pos, tpl, index, tab_ptr, tab_off, mx, my, mz, np.zeros(1, dtype=np.int64), np.zeros(1, dtype=np.int32))
    return _solve(g.n, g.value, 0, gr, portion, tick, max_bytes, stats)


def solve_arcs(
    value: NDArray[np.int64],
    indptr: NDArray[np.int64],
    indices: NDArray[np.int32],
    *,
    portion: int | None = PORTION,
    tick: Tick | None = None,
    max_bytes: int | None = None,
    stats: dict[str, Any] | None = None,
) -> Mask:
    """The smallest optimal closure for explicit arcs u → `indices[indptr[u]:indptr[u + 1]]` (MineLib `.prec`)."""
    v = np.asarray(value)
    if v.ndim != 1 or not np.issubdtype(v.dtype, np.integer):
        raise ValueError("value must be a 1-D integer array")
    v = v.astype(np.int64, copy=False)
    n = len(v)
    ip = np.asarray(indptr)
    ix = np.asarray(indices)
    if len(ip) != n + 1 or int(ip[-1]) != len(ix):
        raise ValueError("indptr does not match value and indices")
    bad_ix = len(ix) > 0 and (int(ix.min()) < 0 or int(ix.max()) >= n)
    if n and (bad_ix or int(ip[0]) != 0 or bool((np.diff(ip) < 0).any())):
        raise ValueError("indices or indptr are out of range")
    ip = np.ascontiguousarray(ip, dtype=np.int64)
    ix = np.ascontiguousarray(ix, dtype=np.int32)
    gr = (*_empty_graph_arrays(), ip, ix)
    return _solve(n, v, 1, gr, portion, tick, max_bytes, stats)


def _solve(
    n: int,
    value: NDArray[np.int64],
    mode: int,
    gr: tuple[Any, ...],
    portion: int | None,
    tick: Tick | None,
    max_bytes: int | None,
    stats: dict[str, Any] | None,
) -> Mask:
    from bf_core.mine import hpf_kernels as K  # numba is imported with the first solution, not with the module

    v = np.ascontiguousarray(value, dtype=np.int64)
    if n == 0 or not bool((v > 0).any()):
        if stats is not None:
            stats.update(nodes=n, roots=0, mergers=0, relabels=0, arcs=0, work=0, portions=0, seconds=0.0, flow=0)
        return np.zeros(n, dtype=bool)
    if float(np.abs(v.astype(np.float64)).sum()) >= MAX_ABS_SUM:
        raise ValueError("the sum of |v′| is too large for int64 excess")
    budget = (1 << 62) if portion is None else max(1, int(portion))  # None — «∞»: the whole solution in one call
    _check_memory(n, max_bytes)
    t0 = time.perf_counter()
    a32 = np.empty((n + 1, K.NCOL32), dtype=np.int32)
    a64 = np.empty((n + 1, 2), dtype=np.int64)
    fl = np.empty((n + 1, 2), dtype=np.uint8)
    cnt = np.empty(n + 2, dtype=np.int32)
    head = np.empty(n + 2, dtype=np.int32)
    stack = np.empty(n + 1, dtype=np.int32)
    vis = np.empty(n + 1, dtype=np.int32)
    ctrl = np.zeros(K.NCTRL, dtype=np.int64)
    K.init_state(n, v, a32, a64, fl, cnt, head, ctrl)
    total = int(ctrl[K.C_ACT])
    portions = 0
    while True:
        done = K.run(budget, n, mode, a32, a64, fl, v, cnt, head, stack, vis, ctrl, gr)
        portions += 1
        if done:
            break
        if max_bytes is not None:
            _check_rss(max_bytes)
        if tick is not None:
            tick(min(0.99, max(0.0, 1.0 - int(ctrl[K.C_ACT]) / total)))
    queue = stack  # the DFS stack is free now: the breadth-first queue of the answer
    mask = np.zeros(n, dtype=np.uint8)
    K.extract(n, mode, a32, a64, fl, queue, mask, gr)
    # the certificate of optimality, label-free: the value of the closure of the strong roots equals the upper bound
    # (the excess of all roots), so the closure is optimal — and the smallest one (the chain of equalities of the bound)
    roots = a32[:n, K.PAR] < 0
    if int(v[mask.view(np.bool_)].sum()) != int(a64[:n, K.EX][roots].sum()):
        raise RuntimeError("HPF did not reach the optimum: the value of the closure differs from the dual bound")
    if tick is not None:
        tick(1.0)
    if stats is not None:
        stats.update(
            nodes=n,
            roots=int(ctrl[K.C_ROOTS]),
            mergers=int(ctrl[K.C_MERGE]),
            relabels=int(ctrl[K.C_RELAB]),
            arcs=int(ctrl[K.C_ARCS]),
            work=int(ctrl[K.C_WORK]),
            portions=portions,
            flow=int(ctrl[K.C_TFLOW]),
            gap=int(ctrl[K.C_GAP]),
            bytes=estimate_bytes(n),
            seconds=time.perf_counter() - t0,
        )
    if os.environ.get("BF_PIT_DEBUG") == "1":
        check_state(n, v, mode, gr, a32, a64, fl, mask)
    return mask.view(np.bool_)


def _check_rss(max_bytes: int) -> None:
    try:
        import psutil

        rss = int(psutil.Process().memory_info().rss)
    except Exception:  # noqa: BLE001
        return
    if rss > max_bytes:
        raise ApiError(
            422,
            "pit_too_large",
            f"Оптимизация карьера заняла {rss // 2**20} МБ памяти при пределе {max_bytes // 2**20} МБ.",
            "Укрупните модель регуляризацией (Блочные модели → Регуляризация).",
            {"need_bytes": rss, "limit_bytes": int(max_bytes)},
        )


# --- the debug check (BF_PIT_DEBUG=1) --------------------------------------------------------------------------------


def check_state(
    n: int,
    value: NDArray[np.int64],
    mode: int,
    gr: tuple[Any, ...],
    a32: NDArray[np.int32],
    a64: NDArray[np.int64],
    fl: NDArray[np.uint8],
    mask: NDArray[np.uint8],
) -> None:
    """Assertions on the final state: the flows live on the arcs of the forest only, so the excess recomputed from
    them equals the stored one (a non-root has none), the flows are positive and within the capacities, the sink flow
    is conserved, and the value of the answer equals the upper bound Σ (excess of the roots)."""
    from bf_core.mine import hpf_kernels as K

    par = a32[:, K.PAR].astype(np.int64)
    ex = a64[:, K.EX]
    flow = a64[:, K.FLOW]
    ori = fl[:, K.ORI]
    tsat = fl[:, K.TSAT]
    node = np.flatnonzero(par[:n] >= 0)  # the children of the forest (a node of the graph with a parent)
    assert (par[n] == -1) and (ex[n] >= 0)
    assert (flow[node] > 0).all(), "a tree arc carries no flow"
    sink = node[ori[node] == 2]
    assert (par[sink] == n).all() and (value[sink] < 0).all(), "a sink arc must end in t"
    assert (flow[sink] < -value[sink]).all(), "a saturated sink arc stays in the forest"
    assert ((ori[node] == 2) == (par[node] == n)).all(), "only the sink arc ends in t"
    # the excess recomputed from the flows: supply + inflow − outflow − sink flow
    calc = np.where(value > 0, value, 0).astype(np.int64)
    calc = np.concatenate([calc, [0]])
    for o, sign in ((1, 1), (0, -1)):
        sel = node[ori[node] == o]
        # o == 1: the arc child → parent carries `flow`: the child sends, the parent receives
        np.add.at(calc, sel, -sign * flow[sel])
        np.add.at(calc, par[sel], sign * flow[sel])
    np.add.at(calc, sink, -flow[sink])
    sat = np.flatnonzero((tsat[:n] == 1) & (value < 0))
    assert (par[sat] != n).all(), "a saturated sink arc of a node inside the tree of t"
    np.add.at(calc, sat, value[sat])  # the saturated sink arc sends −v′ out of the node
    calc[n] = int(ex[n])  # t takes what arrives (checked by the conservation below)
    nonroot = par[:n] >= 0
    assert (ex[:n][nonroot] == 0).all(), "a non-root has an excess"
    assert (calc[:n] == ex[:n]).all(), "the excess does not agree with the flows"
    assert (ex[:n] >= 0).all()
    flow_t = int(-value[sat].sum()) + int(flow[sink].sum())
    assert flow_t == int(ex[n]), "the flow into t is not the flow that arrived"
    assert int(value[value > 0].sum()) == int(ex[:n].sum()) + int(ex[n]), "the supply is not conserved"
    # the certificate of optimality (it needs no labels): the closure of the strong roots is closed and its value is the
    # upper bound Σ (the excess of the roots) — so it is optimal; it is the smallest one by the same chain of equalities
    assert int(value[mask.astype(bool)].sum()) == int(ex[:n][par[:n] < 0].sum()), "the closure is not optimal"
