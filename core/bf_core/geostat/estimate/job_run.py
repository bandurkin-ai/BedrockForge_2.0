"""The pieces of a run of `estimate.run` (подшаг 18.12; ADR 0024 §7; docs/progress/e18-api.md §2.1–2.2, §6): the model
is read by chunks, a chunk is cut into pieces of `portion` rows, a piece is estimated by every row of the plan on the
blocks of its domain that the restrictor lets pass, and the results of the rows of one prefix are put together into the
columns of the fields. The pieces go to the pool in order and come back in order (the writer needs the rows in the
order of the model); the result does not depend on the number of threads or on the cut (Т8.8-13: a block is computed
alone, in a fixed order).

    env = Env(plan, portion)
    for out in ordered(pieces(env, tick, chunk), env.compute, threads, tick): …   # `Out.values` — one write

A block (a row of the model) of a group of one prefix is in one of three states: in the domain of a row of the run
(the result of the row — STATUS 1 when the restrictor does not let it pass), in the domain of a sibling the run does
not repeat (the values the fields have now, when there are any), or in none (STATUS 1, the rest empty — never 0).

`sub: parent` — a block (P, D) gets the estimate of the centre of its parent P with the discretization of P (the
kernel gets every parent once); `own` — the centre and the size of the sub-block, the discretization and C̄_VV by
size. A model without sub-blocks has no difference.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.blocks import geometry
from bf_core.blocks.model import CHUNK

from . import kernel as kn
from .job_plan import CHUNK_ROWS, PORTION, Plan, RowRun, domain_mask

if TYPE_CHECKING:
    from .job_plan import Group

Tick = Callable[[float | None, str | None], None]


@dataclass
class Piece:
    start: int
    p: np.ndarray
    s: np.ndarray | None
    w: np.ndarray | None
    act: list[np.ndarray]  # per row of the run: the block is in its domain and passes the restrictor
    sib: dict[int, np.ndarray] = field(default_factory=dict)  # per group: blocks of a sibling's domain, not repeated
    carry: dict[int, dict[str, np.ndarray]] = field(default_factory=dict)  # per group: name → the current values

    @property
    def n(self) -> int:
        return int(self.p.shape[0])


@dataclass
class RowOut:
    by_status: np.ndarray
    by_pass: np.ndarray
    singular: np.ndarray
    neg_kv: int
    last_pass: int  # the number (from 1) of the last pass that worked on a block of the piece, 0 — none


@dataclass
class Out:
    start: int
    n: int
    values: dict[str, np.ndarray]  # field name → f8 [n]
    rows: list[RowOut]


class Env:
    """What the workers share: the grid, the plan, the size of a piece. Read only."""

    def __init__(self, plan: Plan, portion: int = PORTION) -> None:
        self.plan, self.model, self.grid = plan, plan.model, plan.model.grid
        self.sub = plan.model.sub
        self.portion = int(portion)

    # --- a row on a piece -----------------------------------------------------------------------------------

    def _estimate(self, r: RowRun, pc: Piece, idx: np.ndarray) -> tuple[kn.ChunkResult, np.ndarray | None]:
        """The kernel on the blocks `idx` of the piece: (the result, the map block → computed block or None)."""
        assert r.rk is not None
        p = pc.p[idx]
        if not self.sub:
            return kn.estimate(r.rk, geometry.centres(self.grid, p)), None
        s, w = pc.s[idx], pc.w[idx]  # type: ignore[index]
        if r.spec.sub == "parent":
            up, back = np.unique(p, return_inverse=True)
            return kn.estimate(r.rk, geometry.centres(self.grid, up)), np.asarray(back).reshape(-1)
        centres = geometry.centres(self.grid, p, s, w)
        return kn.estimate(r.rk, centres, sizes=geometry.row_widths(self.grid, p, s, w)), None

    def _row(self, r: RowRun, pc: Piece, arrs: dict[str, np.ndarray], status_name: str) -> RowOut:
        n = pc.n
        npass = r.rk.n_passes if r.rk is not None else 0
        by_pass, singular = np.zeros(npass, np.int64), np.zeros(npass, np.int64)
        status = np.ones(n, np.int8)
        neg, last = 0, 0
        idx = np.flatnonzero(pc.act[r.index])
        if idx.size:
            res, back = self._estimate(r, pc, idx)
            st = res.status if back is None else res.status[back]
            status[idx] = st
            arrs[status_name][idx] = st
            for c in r.columns:
                v = res.values.get(c["key"])
                if v is not None and c["name"] != status_name:
                    arrs[c["name"]][idx] = v if back is None else v[back]
            by_pass, singular, neg = res.by_pass, res.singular, int(res.neg_kv)
            worked = np.flatnonzero((by_pass + singular) > 0)
            last = int(worked[-1]) + 1 if worked.size else 0
        return RowOut(np.bincount(status, minlength=4).astype(np.int64), by_pass, singular, neg, last)

    def compute(self, pc: Piece) -> Out:
        """One piece: the columns of every group and the tallies of every row."""
        n = pc.n
        values: dict[str, np.ndarray] = {}
        rows: list[RowOut | None] = [None] * len(self.plan.rows)
        for gi, g in enumerate(self.plan.groups):
            status_name = g.columns[0]["name"]
            arrs = {c["name"]: np.full(n, np.nan) for c in g.columns}
            arrs[status_name][:] = 1.0
            sib = pc.sib.get(gi)
            if sib is not None and sib.any():
                for name, old in pc.carry.get(gi, {}).items():
                    arrs[name][sib] = old[sib]
                st = arrs[status_name]
                st[sib & np.isnan(st)] = 1.0  # a field the old file lacks: «вне домена», not a gap
            for r in g.rows:
                rows[r.index] = self._row(r, pc, arrs, status_name)
            values.update(arrs)
        return Out(pc.start, n, values, [x for x in rows if x is not None])


# --- the pieces of the model ------------------------------------------------------------------------------------


def _masks(env: Env, ch: Any) -> tuple[list[np.ndarray], list[np.ndarray], dict[int, np.ndarray]]:
    """Per row: the domain, the domain and the restrictor; per group with siblings: their blocks not in a domain of
    the run (the siblings of one group have different domains of one field, as the rows do: `check_plan`)."""
    model, n = env.model, ch.n
    dom, act = [], []
    for r in env.plan.rows:
        t = r.spec.target
        d = domain_mask(model, t.domain_field, t.label, ch.cols, n)
        dom.append(d)
        act.append(d & r.restrictor.mask(ch))
    sib: dict[int, np.ndarray] = {}
    for gi, g in enumerate(env.plan.groups):
        if not g.siblings:
            continue
        taken = np.zeros(n, dtype=np.bool_)
        for r in g.rows:
            taken |= dom[r.index]
        other = np.zeros(n, dtype=np.bool_)
        for s in g.siblings:
            other |= domain_mask(model, s.field, s.label, ch.cols, n)
        sib[gi] = other & ~taken
    return dom, act, sib


def pieces(env: Env, tick: Tick, chunk: int = CHUNK_ROWS) -> Iterator[Piece]:
    """The model by chunks, every chunk cut into pieces of `env.portion` rows."""
    names = env.plan.read
    groups: list[Group] = env.plan.groups
    for ch in env.model.iter(names, chunk):
        tick(None, None)
        dom, act, sib = _masks(env, ch)
        for a in range(0, ch.n, env.portion):
            cut = slice(a, min(a + env.portion, ch.n))
            pc = Piece(ch.start + a, ch.p[cut], None if ch.s is None else ch.s[cut],
                       None if ch.w is None else ch.w[cut], [m[cut] for m in act])  # fmt: skip
            for gi, g in enumerate(groups):
                if gi in sib:
                    pc.sib[gi] = sib[gi][cut]
                    pc.carry[gi] = {name: ch.cols[old][cut] for name, old in g.carry.items()}
            yield pc


def ordered[T, R](items: Iterable[T], fn: Callable[[T], R], threads: int, tick: Tick) -> Iterator[R]:
    """`fn` over `items` on `threads` threads, the results in the order of the items; at most 2·threads ahead (the
    memory is bounded). A cancel (`tick(None, None)` raises) is checked before a piece goes out and after every result;
    the pending pieces are dropped, the running ones finish."""
    if threads <= 1:
        for it in items:
            tick(None, None)
            out = fn(it)
            tick(None, None)
            yield out
        return
    pool = ThreadPoolExecutor(int(threads), thread_name_prefix="bf-estimate-run")
    queue: deque[Any] = deque()
    ahead = 2 * int(threads)
    try:
        for it in items:
            tick(None, None)
            queue.append(pool.submit(fn, it))
            if len(queue) >= ahead:
                out = queue.popleft().result()
                tick(None, None)
                yield out
        while queue:
            out = queue.popleft().result()
            tick(None, None)
            yield out
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


__all__ = ["CHUNK", "CHUNK_ROWS", "PORTION", "Env", "Out", "Piece", "RowOut", "ordered", "pieces"]
