"""The nested pit shells by the revenue factor RF (подшаг 21.7; ТЗ 8.11.3, «оболочки RF», Т8.11-5, Т8.11-9; ADR 0028
decisions 13, 16, 17): one maximum closure per RF, found by bisection of the interval of the factors instead of N solves
on the whole graph.

    res = shells(graph, table_or_values, rfs, solver="auto", tick=tick, threads=4)
    res.pit          # int16 [REAL nodes]: the number 1…N of the SMALLEST shell holding the node, 0 — outside
    res.rf, res.q    # the sorted factors, the quantum of the scenario ($)

BRIDGES (`graph.py`). The graph may hold bridges (nodes of v′ = 0 that carry a chain of the reduced template through an
«air» cell). `values` give the values of the REAL nodes only (the order of `Table`), the run expands them with 0 at the
bridges; the solvers and the closure checks see all nodes; everything the caller gets (`pit`, `blocks`, `mask`,
`objective`) is about the real nodes — bridges are not blocks and never appear in a result.

THE ALGORITHM. The factors go up: RF_0 < … < RF_(N−1). C(RF) is the smallest optimal closure at RF. The value of a block
never falls when RF grows (v = max(A·RF − D; 0) − C_m), so the smallest optima are NESTED, C(RF_k) ⊆ C(RF_(k+1))
(parametric maximum closure); every step below uses it.

 1. The preprocessing: the union of the cones over the blocks with v′ > 0 at RF_max (`graph.cone_mask`). A block outside
    it is needed by nothing that pays at any RF ≤ RF_max, so the graph is cut to it.
 2. C(RF_max) on that graph; the graph is cut to C(RF_max) — every later shell lives inside it.
 3. C(RF_min) on C(RF_max).
 4. Inside an interval (RF_a; RF_b) with C(RF_a) and C(RF_b) known, the graph is C(RF_b) ∖ C(RF_a): the blocks of
    C(RF_a) are «already mined» (the arcs to them are free, their values are taken out of the problem — the same as to
    merge them into the source s), the blocks outside C(RF_b) are not needed by anything inside it. The middle RF is
    solved on that graph, S is the answer, C(RF_mid) = C(RF_a) ∪ S, and the two halves of the interval are solved the
    same way on S (left) and on C(RF_b) ∖ C(RF_mid) = the graph ∖ S (right) — the interval graphs only shrink.
    The result is the same set as the solution «from scratch» at that RF: C(RF_a) ⊆ C* ⊆ C(RF_b), and
    C* ∖ C(RF_a) is the smallest optimum of the cut problem (a smaller one would give a smaller optimum of the whole).

THE INTEGERS. One quantum q for the whole scenario: q = max of the quanta of RF_min and of RF_max; v′(RF) =
round(v(RF)/q), half to even. No other RF needs a larger q: v_b(rf) = max(A·rf − D; 0) − C_m does not fall with rf, so
for RF_min ≤ rf ≤ RF_max the value lies between the two and |v_b(rf)| ≤ max(|v_b(RF_min)|, |v_b(RF_max)|) ≤
|v_b(RF_min)| + |v_b(RF_max)|; with Σ|v_b(RF_x)| ≤ 10¹⁶·q_x ≤ 10¹⁶·q that gives Σ|v′(rf)| ≤ 2·10¹⁶ + N < 2⁶² (the
bound the solvers check, `flow_ref.MAX_ABS_SUM`). Rounding with ONE q is monotone in v, so the nesting stays true for
the integers; with a q per solution the shells could cross by a rounding. The objective error of the scenario is
N_blocks·q/2 (`Shells.error`) for every shell.

THE CHECKS. After every solution the set must be closed on its own graph; at the end every shell is checked on the
WHOLE graph (Т8.11-5: not one mined block without its predecessor): a violation is a 500 `pit_internal` with the shell,
the arc and a line in the log — never a result. A scenario with no block of v′ > 0 at RF_max is a 422 `pit_empty`.

Memory: the pit is one int16 array; the masks live only along the path of the recursion (depth ≈ log₂ N).
Progress and cancellation: `tick(done, total, text)` — «Оболочки k/N» — after each shell, and between the portions of
HPF (the same numbers: only the cancellation matters there; it raises `JobCancelled` from the caller).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from bf_core.api.errors import ApiError
from bf_core.mine import hpf
from bf_core.mine.econ_table import to_int
from bf_core.mine.graph import (
    PitGraph,
    closure_violations,
    cone_mask,
    nesting_violations,
    parent_index,
    restrict_graph,
)
from bf_core.mine.solve import solve

MAX_SHELLS = 200  # the limit of the contract (ТЗ 8.11.5); the label is int16
log = logging.getLogger("bf_core.mine.shells")

Values = Callable[[float], NDArray[np.float64]]  # RF → float64 [real nodes of the graph]
Tick = Callable[[int, int, str], None]  # (done, total, text)


@dataclass(frozen=True, eq=False)
class Shells:
    """The answer of `shells`. All arrays are per shell (`rf` order) or per node of the graph."""

    rf: NDArray[np.float64]  # (N,) ascending, no repeats
    pit: NDArray[np.int16]  # (n_real,) number 1…N of the smallest shell with the block, 0 — outside of C(RF_max)
    q: float  # the quantum of the scenario, $
    error: float  # n_real·q/2 — |Σ value − q·Σ v′| over any set of blocks (`meta.objective_error`)
    blocks: NDArray[np.int64]  # (N,) blocks (real nodes) in C(RF_k)
    objective: NDArray[np.int64]  # (N,) Σ v′(RF_k) over C(RF_k) — the optimum of the solver, in quanta
    solver: str  # the solver that answered: "hpf", "ortools" or "mixed" (a fall-back inside the run)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def n_shells(self) -> int:
        return int(self.rf.shape[0])

    def mask(self, k: int) -> NDArray[np.bool_]:
        """The blocks of C(RF_k), k = 0…N−1: bool per real node."""
        return (self.pit > 0) & (self.pit <= k + 1)


# --- helpers -----------------------------------------------------------------------------------------------


def scale(v: NDArray[np.float64], q: float) -> NDArray[np.int64]:
    """v′ = round(v / q), half to even — the rounding of `econ_table.to_int` with a given quantum."""
    return np.rint(np.asarray(v, dtype=np.float64) / q).astype(np.int64)


def sorted_rf(rfs: Sequence[float] | NDArray[np.float64]) -> NDArray[np.float64]:
    """The factors ascending, repeats removed (a shell per distinct RF)."""
    a = np.asarray(rfs, dtype=np.float64).ravel()
    if a.size == 0 or not bool(np.all(np.isfinite(a))) or float(a.min()) <= 0.0:
        raise ValueError("rf must be a non-empty list of finite positive numbers")
    out: NDArray[np.float64] = np.unique(a)
    if out.size > MAX_SHELLS:
        raise ValueError(f"at most {MAX_SHELLS} shells")
    return out


def pit_on_grid(g: PitGraph, pit: NDArray[np.int16]) -> NDArray[np.int16]:
    """The labels of the blocks (`Shells.pit`, one per REAL node) on the grid of parents (nx·ny·nz, order of P); a cell
    without a block («воздух», a bridge) — 0."""
    out = np.zeros(g.dims[0] * g.dims[1] * g.dims[2], dtype=np.int16)
    out[parent_index(g, real_only=True)] = pit
    return out


def _fmt(rf: float) -> str:
    return f"{rf:g}".replace(".", ",")


def _internal(what: str, **extra: Any) -> ApiError:
    log.error("pit shells: %s %s", what, extra)
    return ApiError(
        500,
        "pit_internal",
        f"Внутренняя ошибка оптимизации карьера: {what}.",
        "Расчёт не сохранён. Повторите расчёт; если ошибка остаётся, сообщите разработчикам "
        "(журнал содержит номер оболочки и блоки).",
        extra,
    )


def _check_closed(g: PitGraph, mined: NDArray[np.bool_], where: str, **extra: Any) -> None:
    """Т8.11-5: no mined block with an unmined predecessor (the arcs of `g`); else 500 `pit_internal`."""
    bad, u, p = closure_violations(g, mined)
    if bad:
        raise _internal(
            f"в оболочке {where} есть вынутый блок без вынутого предшественника ({bad} дуг)",
            arcs=bad, node=u, predecessor=p, **extra,
        )  # fmt: skip


def _values_of(source: Any, threads: int) -> Values:
    """`Table` (has `.values(rf, threads=)`) or a function RF → float64 [nodes]."""
    if callable(source):
        return source  # type: ignore[no-any-return]
    if hasattr(source, "values"):
        return lambda rf: source.values(rf, threads=threads)  # type: ignore[no-any-return]
    raise TypeError("values must be a Table or a function of RF")


# --- the run -------------------------------------------------------------------------------------------------


class _Run:
    """The state of one `shells` call: the quantum, the labels, the counters. Methods are the steps of the algorithm."""

    def __init__(self, graph: PitGraph, get: Values, rf: NDArray[np.float64], solver: str, tick: Tick | None,
                 threads: int, portion: int | None, max_bytes: int | None) -> None:  # fmt: skip
        self.graph, self.get, self.rf, self.solver = graph, get, rf, solver
        self.tick, self.threads, self.portion, self.max_bytes = tick, threads, portion, max_bytes
        self.n, self.total = graph.n, int(rf.shape[0])
        self.done = 0
        self.used: set[str] = set()
        self.solves: list[dict[str, Any]] = []
        self.sec = dict.fromkeys(("values", "cones", "solve", "check"), 0.0)
        self.q = 0.01
        self.iv_lo = np.empty(0, dtype=np.int64)
        self.iv_hi = np.empty(0, dtype=np.int64)
        self.lab = np.zeros(self.n, dtype=np.int16)

    # progress ------------------------------------------------------------------------------------------------------

    def say(self, done: int | None = None) -> None:
        if done is not None:
            self.done = done
        if self.tick is not None:
            self.tick(self.done, self.total, f"Оболочки {self.done}/{self.total}")

    # values --------------------------------------------------------------------------------------------------------

    def raw(self, k: int) -> NDArray[np.float64]:
        t0 = time.perf_counter()
        v = np.asarray(self.get(float(self.rf[k])), dtype=np.float64)
        self.sec["values"] += time.perf_counter() - t0
        if v.shape != (self.graph.n_real,):
            raise ValueError("values(rf) must return one float64 per real node of the graph")
        return v

    def ints(self, k: int) -> NDArray[np.int64]:
        """v′ of the shell k at the quantum of the scenario, for ALL nodes of the graph (0 at the bridges)."""
        if k == self.total - 1:
            return self.iv_hi
        if k == 0:
            return self.iv_lo
        return self.graph.expand(scale(self.raw(k), self.q))

    def quantum(self) -> None:
        """One q for the scenario; v′ at RF_max and RF_min are kept (they are used all the time)."""
        v_hi = self.raw(self.total - 1)
        i_hi = to_int(v_hi, threads=self.threads)
        q = i_hi.q
        v_lo = None
        if self.total > 1:
            v_lo = self.raw(0)
            q = max(q, to_int(v_lo, threads=self.threads).q)
        self.q = q
        self.iv_hi = self.graph.expand(i_hi.v if q == i_hi.q else scale(v_hi, q))
        self.iv_lo = self.iv_hi if v_lo is None else self.graph.expand(scale(v_lo, q))

    # one solution --------------------------------------------------------------------------------------------------

    def run(self, g: PitGraph, where: int, k: int) -> NDArray[np.bool_]:
        """One solution on `g` (the values are inside) and the check of its closure on `g`."""
        t0 = time.perf_counter()
        one: dict[str, Any] = {}
        mined = solve(g, solver=self.solver, tick=lambda _f: self.say(), portion=self.portion,
                      max_bytes=self.max_bytes, stats=one)  # fmt: skip
        dt = time.perf_counter() - t0
        self.sec["solve"] += dt
        used = str(one.get("solver"))
        self.used.add(used)
        self.solves.append({"shell": where, "rf": float(self.rf[k]), "nodes": g.n, "seconds": dt, "solver": used})
        t0 = time.perf_counter()
        _check_closed(g, mined, str(where), rf=float(self.rf[k]))
        self.sec["check"] += time.perf_counter() - t0
        return mined

    # the interval --------------------------------------------------------------------------------------------------

    def split(self, g: PitGraph, gids: NDArray[np.int64], lo: int, hi: int) -> None:
        """The shells lo < k < hi inside `g` = C(RF_hi) ∖ C(RF_lo) (`gids` — its nodes in the graph of the run)."""
        if hi - lo <= 1:
            return
        if g.n == 0:  # nothing between the two shells: they are the same set
            self.say(self.done + hi - lo - 1)
            return
        mid = (lo + hi) // 2
        s = self.run(g.with_values(self.ints(mid)[gids]), mid + 1, mid)
        self.lab[gids[s]] = mid + 1
        self.say(self.done + 1)
        if mid - lo > 1:  # C(RF_mid) ∖ C(RF_lo) = S
            left, il = restrict_graph(g, s)
            self.split(left, gids[il], lo, mid)
        if hi - mid > 1:  # C(RF_hi) ∖ C(RF_mid) = the graph without S
            right, ir = restrict_graph(g, ~s)
            self.split(right, gids[ir], mid, hi)

    def go(self) -> None:
        total = self.total
        self.quantum()
        if self.n == 0 or not bool((self.iv_hi > 0).any()):
            raise ApiError(
                422,
                "pit_empty",
                f"При RF = {_fmt(float(self.rf[-1]))} нет ни одного блока с положительной ценностью: карьер пуст.",
                "Проверьте цены, содержания, извлечение и затраты на экономическом шаге или увеличьте наибольший RF.",
                {"rf": float(self.rf[-1])},
            )
        self.say(0)
        t0 = time.perf_counter()  # 1. the cones at RF_max
        g_hi = self.graph.with_values(self.iv_hi)
        cone = cone_mask(g_hi)
        if bool(cone.all()):
            g0, ids0 = g_hi, np.arange(self.n, dtype=np.int64)
        else:
            g0, ids0 = restrict_graph(g_hi, cone)
        del cone, g_hi
        self.sec["cones"] += time.perf_counter() - t0
        c_max = self.run(g0, total, total - 1)  # 2. C(RF_max), then the graph is cut to it
        self.say(1)
        w, idw = restrict_graph(g0, c_max)
        ids = ids0[idw]
        del c_max, g0, ids0, idw
        self.lab[ids] = total  # every node of C(RF_max) is in the last shell until a smaller one takes it
        if total == 1:
            return
        c_min = self.run(w.with_values(self.iv_lo[ids]), 1, 0)  # 3. C(RF_min) on C(RF_max)
        self.lab[ids[c_min]] = 1
        self.say(2)
        if total > 2:  # 4. the interval (RF_min; RF_max): C(RF_max) ∖ C(RF_min)
            g_int, i_int = restrict_graph(w, ~c_min)
            self.split(g_int, ids[i_int], 0, total - 1)

    # the final checks ----------------------------------------------------------------------------------------------

    def finish(self) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
        """Every shell on the WHOLE graph: closed and nested (Т8.11-5) — ONE pass over the arcs for all the shells
        (`graph.nesting_violations`); the counts and the objective of each (in quanta)."""
        blocks = np.zeros(self.total, dtype=np.int64)
        objective = np.zeros(self.total, dtype=np.int64)
        lab = self.lab
        real = self.graph.real
        t0 = time.perf_counter()
        bad, u, q, lu, lq = nesting_violations(self.graph, lab)
        self.sec["check"] += time.perf_counter() - t0
        if bad:
            raise _internal(
                f"в оболочке {lu} есть вынутый блок без вынутого предшественника ({bad} дуг)",
                arcs=bad, node=u, predecessor=q, shell=lu, predecessor_shell=lq,
                rf=float(self.rf[lu - 1]),
            )  # fmt: skip
        for k in range(self.total):
            self.say(self.total)  # (cancellation point; the work is the counts)
            m = (lab > 0) & (lab <= k + 1)
            blocks[k] = int(m.sum()) if real is None else int(np.count_nonzero(m & real))
            objective[k] = int(self.ints(k)[m].sum())
            if k and blocks[k] < blocks[k - 1]:
                raise _internal(f"оболочка {k + 1} меньше предыдущей", shell=k + 1)
        return blocks, objective


def shells(
    graph: PitGraph,
    values: Any,
    rfs: Sequence[float] | NDArray[np.float64],
    *,
    solver: str = "auto",
    tick: Tick | None = None,
    threads: int = 1,
    portion: int | None = hpf.PORTION,
    max_bytes: int | None = None,
    stats: dict[str, Any] | None = None,
) -> Shells:
    """The nested shells of `graph` for the factors `rfs` (any order; sorted here, repeats merged).

    `graph` — the structure (nodes, offset tables; its own `value` is ignored); `values` — an `econ_table.Table` or a
    function RF → float64 [REAL nodes of the graph] in the order of the nodes (the bridges, if any, are 0); `solver`
    — "auto" (HPF, OR-Tools as the reserve) / "hpf" / "ortools"; `tick(done, total, text)` — progress and cancellation
    (it may raise); `threads` — for the values only (the answer does not depend on it); `max_bytes` — the memory limit
    of HPF; `stats` — gets the counters (also in `Shells.stats`).
    422 `pit_empty` — no block with v′ > 0 at RF_max; 422 `pit_too_large` — from HPF; 500 `pit_internal` — a check
    failed."""
    t_all = time.perf_counter()
    rf = sorted_rf(rfs)
    r = _Run(graph, _values_of(values, threads), rf, solver, tick, threads, portion, max_bytes)
    r.go()
    blocks, objective = r.finish()
    used = {u for u in r.used if u != "none"}
    n_real = graph.n_real
    kind = "mixed" if len(used) > 1 else (next(iter(used)) if used else "hpf")
    st: dict[str, Any] = {"solves": r.solves, "seconds": {**r.sec, "total": time.perf_counter() - t_all},
                          "nodes": graph.n, "bridges": graph.n - n_real}  # fmt: skip
    if stats is not None:
        stats.update(st)
    return Shells(rf, graph.real_pit(r.lab), r.q, n_real * r.q / 2.0, blocks, objective, kind, st)
