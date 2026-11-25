"""The targets of a simulation on a block model (ADR 0024 §9; вопрос № 83; подшаг 18.32): the parents (P) of the
blocks that are in the domain of the row and pass its restrictor, in the order of the model (P ascending); the nodes
of the simulation are those parents, the sub-blocks of a parent take its value (`row_values`). A model with a variable
step is 422 `variable_grid` (`store_check.block_model`).

    plan = TargetPlan.of(conn, store, spec, model)       the restrictor of the target and the fields it reads
    mask = plan.mask(chunk)                              bool [chunk.n]: the block is in the domain and passes it
    p = find(model, plan, tick)                          uint64 [n]: the distinct parents of the blocks of `mask`
    node, ok = locate(p, chunk_p)                        the position of every row's parent among the nodes, found?

Light: numpy and the model readers, no numba.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.blocks import calc_job, restrict
from bf_core.geostat.estimate import job_plan as jp
from bf_core.geostat.estimate import store_check as sc

if TYPE_CHECKING:
    from bf_core.blocks.model import Chunk, Model
    from bf_core.storage.blobs import BlobStore

    from .spec import SimulationSpec

Tick = Callable[[float | None, str | None], None]
CHUNK_ROWS = 1 << 18  # rows of the model read at a time


@dataclass
class TargetPlan:
    """What decides whether a block of the model is a target of the row: its domain (a field of the model and the
    label) and the restrictor (a filter or rules); `read` — the fields a chunk must hold."""

    model: Model
    domain_field: str | None
    label: str | None
    restrictor: restrict.Restrictor
    tree: dict[str, Any] | None

    @classmethod
    def of(cls, conn: sa.Connection, store: BlobStore, spec: SimulationSpec, model: Model) -> TargetPlan:
        """The restrictor of the target prepared in the caller's read transaction; the field of the domain is as the
        model stores it (422 `field_not_found`, `domain_unmapped`, `filter_*` as the checks of the store name them)."""
        _name, mmeta = sc.block_model(conn, spec.model_id)
        target = sc.check_target(conn, spec, mmeta)  # type: ignore[arg-type]  # `.target` only
        tree = calc_job.rules_tree(conn, target.filter_id, target.rules)
        restrictor = restrict.prepare(model, tree, conn=conn, store=store)
        return cls(model, target.domain_field, target.label, restrictor, tree)

    @property
    def read(self) -> list[str]:
        out: dict[str, None] = {}
        if self.domain_field is not None:
            out[self.domain_field] = None
        out.update(dict.fromkeys(self.restrictor.fields))
        return list(out)

    def mask(self, ch: Chunk) -> np.ndarray:
        d = jp.domain_mask(self.model, self.domain_field, self.label, ch.cols, ch.n)
        return d & self.restrictor.mask(ch)  # type: ignore[no-any-return]


def find(plan: TargetPlan, tick: Tick | None = None, chunk: int = CHUNK_ROWS) -> np.ndarray:
    """The parents of the target blocks, distinct, ascending (the model's order); `tick(None, None)` between the
    chunks raises to cancel."""
    parts: list[np.ndarray] = []
    for ch in plan.model.iter(plan.read, chunk):
        if tick is not None:
            tick(None, None)
        m = plan.mask(ch)
        if m.any():
            parts.append(np.unique(ch.p[m]))
    if not parts:
        return np.empty(0, dtype=np.uint64)
    return np.unique(np.concatenate(parts)).astype(np.uint64, copy=False)


def locate(p: np.ndarray, rows_p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(position among the nodes `p` (ascending), found) of the parent of every row `rows_p`; a parent that is not a
    node is not found (its position is clipped, use `found`)."""
    if p.size == 0:
        return np.zeros(rows_p.shape[0], dtype=np.int64), np.zeros(rows_p.shape[0], dtype=np.bool_)
    pos = np.minimum(np.searchsorted(p, rows_p), p.size - 1)
    return pos.astype(np.int64, copy=False), np.asarray(p[pos] == rows_p)
