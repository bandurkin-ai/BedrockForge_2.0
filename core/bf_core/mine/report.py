"""The pit-by-pit report of the nested shells (подшаг 21.8; ТЗ 8.11.4; ADR 0028 decisions 18, 19, 22, 34).

    rep = build_report(model, inp, restrictor, tab, pit, rf, chunk=, tick=, threads=)
    rep.data        dict name → array [N]: the increments ΔC_k = C_k \\ C_(k−1) and the cumulative C_k (`data` of the
                    scenario, one Parquet column each)
    rep.benches     dict name → array: the worst case of every n (`sidecar:benches`)
    rep.increments()  → (O, W, CF) [N] — the best case of n is the first n of them in order
    rep.totals()    → the JSON-ready total over C_N;  rep.strip()  → W/O [N] (NaN where O = 0)
    Report.from_columns(elements, data, benches)    the same object from the two stored tables (the calendar of
                    `npv.py` is a view of them: it recomputes from `data` and `benches`, nothing else)

`pit` is the number of the smallest shell holding a node (0 — outside the pit), int [nodes of `tab`]; the shells are
numbered 1…N by RF ascending, `rf` [N] are their factors. The report is taken at the BASE price RF = 1 whatever the RF
of the shell (decision 18): a block is ore when R − C_p > 0 at RF = 1 (the field ORE of 21.9), its revenue, the cost of
processing, the selling cost and the royalty are those of RF = 1, a waste block costs only the mining.

Columns of `data` (a name, the increment; `cum_<name>` — the same cumulative over C_k, the shells in order):
`k` (1…N), `rf`, `blocks` (parents), `ore_t`, `waste_t`, per element `<field>:grade` (the mean grade of the ore by the
tonnes of the blocks WITH a value; NaN where there are none — a gap is not a zero), `<field>:metal` (the metal in the
ore, t·g·k_e: ounces for g/t, ppm, ppb, tonnes for %), `<field>:rec_metal` (the recoverable one; NaN where the ore has
tonnes and no block with the grade), then `revenue` (before the selling cost and the royalty), `cost_mine` (every block
of the increment), `cost_process` (the ore), `cost_sales`, `royalty`, `cf` = Σ of CF of the blocks = revenue −
cost_sales − royalty − cost_mine − cost_process (the same number up to rounding). W/O is `strip()`: O = 0 → NaN (empty,
not 0 and not ∞). `cum_k`, `cum_rf` are not stored.

The worst case of n (decision 18): C_n mined bench by bench from the top of the model down, a bench = a level of the
model (the layer of the parents, decision 122 — a level index k grows upward). `benches`: `n` (1…N), `level` (the
index of the layer, 0 — the lowest), `ore_t`, `waste_t`, `cf` of C_n at that level; only the levels that hold a block of
C_n, the rows of an n from the top level down. The best case of n is ΔC_1 … ΔC_n in order.

Reproducibility (decision 16): the sums are Neumaier's in the order of the rows of the file, one thread
(`report_kernels.accumulate`); the cumulative ones — in the order of the shells. The same bits with a portion of 1, 7 or
2²⁰ rows and 1–8 threads of the economics. Errors — {code, message, hint} in Russian.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks.model import CHUNK, Model
from bf_core.blocks.restrict import Restrictor

from . import report_kernels as rk
from .econ import Inputs, Tick, _noop, _w, bad_param, iter_rows, row_terms
from .econ_table import Table

SCALARS = ("ore_t", "waste_t")  # the order of the columns after `blocks`
COSTS = ("revenue", "cost_mine", "cost_process", "cost_sales", "royalty", "cf")
_SLOT = {"ore_t": rk.S_ORE, "waste_t": rk.S_WASTE, "revenue": rk.S_REV, "cost_mine": rk.S_MINE,
         "cost_process": rk.S_PROC, "cost_sales": rk.S_SELL, "royalty": rk.S_ROY, "cf": rk.S_CF}  # fmt: skip
BENCH_COLUMNS = ("n", "level", "ore_t", "waste_t", "cf")


def internal(message: str) -> ApiError:
    return ApiError(500, "pit_internal", message, "Сообщите разработчикам: приложите журнал.")


def elem_names(name: str) -> tuple[str, str, str]:
    return f"{name}:grade", f"{name}:metal", f"{name}:rec_metal"


def column_names(elements: list[str]) -> list[str]:
    """The names of the columns of `data` in their order (the increments, then the cumulative ones)."""
    inc = ["k", "rf", "blocks", *SCALARS]
    for e in elements:
        inc.extend(elem_names(e))
    inc.extend(COSTS)
    return [*inc, *(f"cum_{c}" for c in inc if c not in ("k", "rf"))]


def _elem_columns(ore: np.ndarray, gt: np.ndarray, gtg: np.ndarray, metal: np.ndarray, rec: np.ndarray
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:  # fmt: skip
    """grade, metal, recoverable metal [N]: no tonnes with a grade → the grade is NaN, and the metals are NaN when there
    is ore (its metal is unknown, not 0); no ore → the metals are 0."""
    unknown = gt <= 0.0
    with np.errstate(invalid="ignore", divide="ignore"):
        grade = np.where(unknown, np.nan, gtg / np.where(unknown, 1.0, gt))
    miss = unknown & (ore > 0.0)
    return grade, np.where(miss, np.nan, metal), np.where(miss, np.nan, rec)


@dataclass
class Report:
    """The two tables of a result (module docstring). `elements` — the grade fields in the order of the columns."""

    elements: list[str]
    data: dict[str, np.ndarray]
    benches: dict[str, np.ndarray] = field(default_factory=dict)

    @classmethod
    def from_columns(cls, elements: list[str], data: dict[str, Any], benches: dict[str, Any]) -> Report:
        """The report of the stored tables (Parquet columns read back): a missing column is an internal error."""
        want = column_names(elements)
        miss = [c for c in want if c not in data] + [c for c in BENCH_COLUMNS if c not in benches]
        if miss:
            raise internal(f"В таблицах отчёта нет столбцов: {', '.join(miss)}.")
        dt = {"k": np.int32, "blocks": np.int64, "cum_blocks": np.int64}
        out = {c: np.asarray(data[c], dtype=dt.get(c, np.float64)) for c in want}
        bt = {"n": np.int32, "level": np.int32}
        return cls(list(elements), out, {c: np.asarray(benches[c], dtype=bt.get(c, np.float64))
                                         for c in BENCH_COLUMNS})  # fmt: skip

    @property
    def n_shells(self) -> int:
        return int(self.data["k"].shape[0])

    def increments(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(O, W, CF) [N] of the shells in order: the best case of n is the first n of them."""
        return self.data["ore_t"], self.data["waste_t"], self.data["cf"]

    def strip(self, *, cum: bool = False) -> np.ndarray:
        """W/O [N] of the increments (or of C_k): NaN where O = 0 — empty, not 0 and not ∞."""
        p = "cum_" if cum else ""
        o, w = self.data[p + "ore_t"], self.data[p + "waste_t"]
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(o > 0.0, w / np.where(o > 0.0, o, 1.0), np.nan)

    def totals(self) -> dict[str, Any]:
        """The total over C_N (the last cumulative row): JSON-ready floats, NaN → None."""

        def num(x: Any) -> float | None:
            x = float(x)
            return x if np.isfinite(x) else None

        n = self.n_shells
        if not n:
            return {"blocks": 0, "ore_t": 0.0, "waste_t": 0.0, "strip": None, "elements": {}}
        d = self.data
        out: dict[str, Any] = {"blocks": int(d["cum_blocks"][-1]), "ore_t": num(d["cum_ore_t"][-1]),
                               "waste_t": num(d["cum_waste_t"][-1]),
                               "strip": num(self.strip(cum=True)[-1])}  # fmt: skip
        for c in COSTS:
            out[c] = num(d[f"cum_{c}"][-1])
        out["elements"] = {e: {"grade": num(d[f"cum_{e}:grade"][-1]), "metal": num(d[f"cum_{e}:metal"][-1]),
                               "rec_metal": num(d[f"cum_{e}:rec_metal"][-1])} for e in self.elements}  # fmt: skip
        return out

    def bench_of(self, n: int) -> dict[str, np.ndarray]:
        """The rows of `benches` of the worst case of n (top level first): `level`, `ore_t`, `waste_t`, `cf`."""
        b = self.benches
        sel = b["n"] == n
        return {c: b[c][sel] for c in ("level", "ore_t", "waste_t", "cf")}


def _check_inputs(tab: Table, pit: Any, rf: Any, model: Model) -> tuple[np.ndarray, np.ndarray]:
    rf = np.asarray(rf, dtype=np.float64).reshape(-1)
    if not rf.size:
        raise bad_param("rf", "Нет ни одной оболочки для отчёта.", "Задайте хотя бы один RF.")
    pit = np.asarray(pit)
    if pit.shape != (tab.n_nodes,) or pit.dtype.kind not in "iu":
        raise internal("Массив PIT не совпадает с узлами графа.")
    if tab.node_of_row.shape[0] != model.rows:
        raise internal("Таблица узлов построена по другой модели.")
    if pit.size and (int(pit.min()) < 0 or int(pit.max()) > rf.size):
        raise internal("Номер оболочки в PIT выходит за список оболочек.")
    return pit.astype(np.int32), rf


def _nodes_of(tab: Table, rc: Any) -> np.ndarray:
    """int32 [n]: the node of every row of a portion (−1: not in the graph). A cell absent from the file is found by
    its cell number."""
    if rc.start >= 0:
        return np.asarray(tab.node_of_row[rc.start : rc.start + rc.n], dtype=np.int32)
    pos = np.searchsorted(tab.p, rc.p)
    ok = pos < tab.p.shape[0]
    pos = np.minimum(pos, max(tab.p.shape[0] - 1, 0))
    if not tab.p.shape[0]:
        return np.full(rc.n, -1, dtype=np.int32)
    return np.where(ok & (tab.p[pos] == rc.p), pos, -1).astype(np.int32)


def build_report(
    model: Model, inp: Inputs, r: Restrictor, tab: Table, pit: Any, rf: Any, *, chunk: int = CHUNK,
    tick: Tick = _noop, threads: int = 1, span: tuple[float, float] = (0.0, 1.0),
) -> Report:  # fmt: skip
    """The report of the shells `pit` (module docstring): one pass over the rows of `model` (`econ.iter_rows`), the
    sums in the order of the rows. `tab` — the table of the nodes of the same model, inputs and restrictor."""
    pit32, rf = _check_inputs(tab, pit, rf, model)
    n_sh, ne = int(rf.size), len(inp.elements)
    counts = model.grid.counts
    nxy, nlev = int(counts[0]) * int(counts[1]), int(counts[2])
    inc = np.zeros((rk.S_ELEM + rk.PER_ELEM * ne, n_sh + 1, 2))
    ben = np.zeros((3, (n_sh + 1) * nlev, 2))
    for rc in iter_rows(model, inp, r, chunk=chunk, tick=tick, threads=threads, span=span):
        e, tm = rc.econ, row_terms(rc, inp, 1.0, threads=threads)
        lev = (rc.p // np.uint64(nxy)).astype(np.int32)
        rk.accumulate(rc.n, _nodes_of(tab, rc), pit32, lev, nlev, e.state, e.ore, _w(e.t), _w(e.cm), _w(e.cp),
                      _w(rc.g), tm.m, tm.mrec, tm.gross, tm.selling, tm.royalty, inc, ben)  # fmt: skip
    return _finish(inp, pit32, rf, inc, ben, nlev, (tab.p // np.uint64(nxy)).astype(np.int64))


def _finish(inp: Inputs, pit: np.ndarray, rf: np.ndarray, inc: np.ndarray, ben: np.ndarray, nlev: int,
            node_level: np.ndarray) -> Report:  # fmt: skip
    """From the accumulators to the two tables."""
    n_sh = int(rf.size)
    names = [e.field for e in inp.elements]
    cum = np.empty(inc.shape[:2])
    rk.cumulate(inc, cum)
    val = inc[:, 1:, 0] + inc[:, 1:, 1]  # the increments [slots, N]
    cm = cum[:, 1:]
    blocks = np.bincount(pit[pit > 0], minlength=n_sh + 1)[1:].astype(np.int64)
    data: dict[str, np.ndarray] = {"k": np.arange(1, n_sh + 1, dtype=np.int32), "rf": rf.copy(), "blocks": blocks,
                                   "cum_blocks": np.cumsum(blocks)}  # fmt: skip
    for pre, src in (("", val), ("cum_", cm)):
        for c in SCALARS:
            data[pre + c] = src[_SLOT[c]].copy()
        for i, name in enumerate(names):
            b = rk.S_ELEM + rk.PER_ELEM * i
            g, m, rc = _elem_columns(src[_SLOT["ore_t"]], src[b], src[b + 1], src[b + 2], src[b + 3])
            for col, a in zip(elem_names(name), (g, m, rc), strict=True):
                data[pre + col] = a
        for c in COSTS:
            data[pre + c] = src[_SLOT[c]].copy()
    order = column_names(names)
    data = {c: data[c] for c in order}

    # the benches: C_n at every level, cumulative over the shells
    bcum = np.empty((3, n_sh + 1, nlev))
    rk.cumulate_benches(ben, nlev, bcum)
    return Report(names, data, _bench_table(pit, node_level, nlev, bcum, n_sh))


def _bench_table(pit: np.ndarray, node_level: np.ndarray, nlev: int, bcum: np.ndarray, n_sh: int
                 ) -> dict[str, np.ndarray]:  # fmt: skip
    """`benches` (module docstring): for every n the levels that hold a node of C_n, from the top level down."""
    inside = pit > 0
    cnt = np.bincount(pit[inside].astype(np.int64) * nlev + node_level[inside], minlength=(n_sh + 1) * nlev)
    cnt = np.cumsum(cnt.reshape(n_sh + 1, nlev), axis=0)  # the nodes of C_n by level
    ns, lv, o, w, cf = [], [], [], [], []
    for n in range(1, n_sh + 1):
        levels = np.flatnonzero(cnt[n])[::-1]
        ns.append(np.full(levels.size, n, dtype=np.int32))
        lv.append(levels.astype(np.int32))
        o.append(bcum[rk.B_ORE, n, levels])
        w.append(bcum[rk.B_WASTE, n, levels])
        cf.append(bcum[rk.B_CF, n, levels])

    def cat(parts: list[np.ndarray], dtype: Any) -> np.ndarray:
        return np.concatenate(parts).astype(dtype) if parts else np.empty(0, dtype=dtype)

    return {"n": cat(ns, np.int32), "level": cat(lv, np.int32), "ore_t": cat(o, np.float64),
            "waste_t": cat(w, np.float64), "cf": cat(cf, np.float64)}  # fmt: skip
