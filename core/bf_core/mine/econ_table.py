"""The table of the nodes of the pit graph and the integers of the solver (подшаг 21.2; ТЗ 8.11.2; ADR 0028 № 7, 8,
16): the part of `econ.py` that turns the pass over the model into what the shells (21.7) and the solver (21.5) use.

    tab = build_table(model, inp, restrictor, chunk=, tick=, threads=)    # one pass over the model
    v = tab.values(rf, threads=)                    # float64 [nodes] at the factor RF: no model, three numbers a row
    iv = to_int(v)                                  # iv.v int64 [nodes], iv.q, iv.error = N·q/2, iv.infinity

The nodes are the parents that are not «воздух», in the order of P; a node of sub-blocks sums its rows. A cell absent
from a sparse model (`Inputs.fill_empty`) is a node of its own. Bit for bit the same with any portion and any thread
count (`econ.py`, `econ_kernels.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks.model import CHUNK, Model
from bf_core.blocks.restrict import Restrictor
from bf_core.composites import strips

from . import econ_kernels as ek
from .econ import ROW_STRIP, Control, Inputs, Tick, _noop, _w, iter_rows

OBJECTIVE_SCALE = 1e16  # Σ |v'| ≤ 10¹⁶ + N (decision 8)
MIN_QUANTUM = 0.01  # the smallest q, $
MAX_NODES = 2**31 - 1  # `Table.node_of_row` is int32


# --- the table of the nodes ---------------------------------------------------------------------------------------


@dataclass
class Table:
    """The nodes (parents not «воздух», in the order of P) and their rows, ready for any RF.

    `p` uint64 [N]: the cell number of each node; `seg` int64 [N + 1]: the rows of the node n are `seg[n]:seg[n+1]` of
    `a`, `d`, `cm`, `ore` [M] (A, D = B + C_p, C_m of the row, ore at RF = 1; a cell absent from the file is a row of
    its own). `node_of_row` int32 [model.rows]: the node of each FILE row, −1 — the row is not in the graph (air, no
    volume); `ore_row` uint8 [model.rows]: the row is ore at RF = 1 (the field ORE, 21.9). `control` — «Контроль»."""

    p: np.ndarray
    seg: np.ndarray
    a: np.ndarray
    d: np.ndarray
    cm: np.ndarray
    ore: np.ndarray
    node_of_row: np.ndarray
    ore_row: np.ndarray
    control: Control

    @property
    def n_nodes(self) -> int:
        return int(self.p.shape[0])

    def values(self, rf: float, *, threads: int = 1) -> np.ndarray:
        """float64 [N]: v of every node at the factor `rf` — the sum of the values of its rows, Neumaier in the order
        of the rows; the same bits with any number of threads."""
        out = np.empty(self.n_nodes)

        def part(lo: int, hi: int) -> None:
            ek.node_values(lo, hi, self.seg, self.a, self.d, self.cm, float(rf), out)

        strips.run(part, self.n_nodes, threads=threads, size=ROW_STRIP)
        return out

    def node_ore(self) -> np.ndarray:
        """bool [N]: the node has an ore row at RF = 1."""
        out = np.zeros(self.n_nodes, dtype=np.bool_)
        if self.ore.shape[0]:
            node = np.repeat(np.arange(self.n_nodes), np.diff(self.seg))
            out[node[self.ore != 0]] = True
        return out


def build_table(
    model: Model, inp: Inputs, r: Restrictor, *, chunk: int = CHUNK, tick: Tick = _noop, threads: int = 1
) -> Table:  # fmt: skip
    """The table of the nodes of `model` (one pass, `iter_rows`). The rows of a parent are consecutive in the file; a
    cell absent from it is merged in the order of P. 422 `pit_too_large` for more nodes than an int32 holds."""
    ctl = Control.new(inp)
    parts: list[tuple[np.ndarray, ...]] = []
    ore_row = np.zeros(model.rows, dtype=np.uint8)
    for rc in iter_rows(model, inp, r, control=ctl, chunk=chunk, tick=tick, threads=threads, span=(0.0, 0.95)):
        e = rc.econ
        if rc.start >= 0:
            ore_row[rc.start : rc.start + rc.n] = e.ore
        idx = np.flatnonzero(e.counted)
        if idx.size:
            row = idx + rc.start if rc.start >= 0 else np.full(idx.size, -1, dtype=np.int64)
            parts.append((rc.p[idx], e.a[idx], e.b[idx] + e.cp[idx], e.cm[idx], e.ore[idx], row.astype(np.int64)))
    cat = [np.concatenate([x[i] for x in parts]) if parts else np.empty(0) for i in range(6)]
    del parts
    p, a, d, cm = cat[0].astype(np.uint64), cat[1], cat[2], cat[3]
    ore, row = cat[4].astype(np.uint8), cat[5].astype(np.int64)
    if p.size > 1 and not bool(np.all(p[1:] >= p[:-1])):  # absent cells merge in; a sorted file keeps its order
        order = np.argsort(p, kind="stable")
        p, a, d, cm, ore, row = p[order], a[order], d[order], cm[order], ore[order], row[order]
    first = np.ones(p.size, dtype=np.bool_)
    if p.size > 1:
        first[1:] = p[1:] != p[:-1]
    n_nodes = int(first.sum())
    if n_nodes > MAX_NODES:
        raise ApiError(422, "pit_too_large", "В модели слишком много блоков для оптимизации карьера.",
                       "Укрупните модель регуляризацией.")  # fmt: skip
    seg = np.append(np.flatnonzero(first), p.size).astype(np.int64)
    node_of_row = np.full(model.rows, -1, dtype=np.int32)
    in_file = row >= 0
    node_of_row[row[in_file]] = (np.cumsum(first) - 1)[in_file].astype(np.int32)
    tick(1.0, None)
    return Table(p[first], seg, a, d, cm, ore, node_of_row, ore_row, ctl)


# --- the integers of the solver -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class IntValues:
    """`v` int64 [N] = round(value/q); `q` — the quantum ($); `error` = N·q/2 — the bound of |Σ value − q·Σ v| over
    any set of the N nodes (`meta.objective_error`)."""

    v: np.ndarray
    q: float
    error: float

    @property
    def infinity(self) -> int:
        """The capacity of an arc to a predecessor: Σ over v > 0 plus 1 (it fits int64: Σ|v| ≤ 10¹⁶ + N)."""
        return int(self.v[self.v > 0].sum()) + 1


def to_int(v: np.ndarray, *, threads: int = 1) -> IntValues:
    """The integers of the solver (decision 8): q = max(0,01; Σ|v|/10¹⁶), v' = round(v/q) (half to even)."""
    v = _w(v)
    if v.size and not bool(np.all(np.isfinite(v))):
        raise ApiError(500, "pit_internal", "Стоимость блока оказалась не числом.", "Сообщите разработчикам.")
    q = max(MIN_QUANTUM, float(ek.abs_sum(v)) / OBJECTIVE_SCALE)
    out = np.empty(v.shape[0], dtype=np.int64)
    strips.run(lambda lo, hi: ek.scale_round(lo, hi, v, q, out), v.shape[0], threads=threads, size=ROW_STRIP)
    return IntValues(out, q, v.shape[0] * q / 2.0)


def memory_mb(_params: Any = None) -> int:
    """A portion of 2²⁰ rows: the columns, the volumes, the grades and the outputs of the kernels."""
    return 800
