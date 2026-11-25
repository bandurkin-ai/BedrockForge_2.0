"""What a SIS or a co-SGS reads besides the samples of the value (подшаг 18.32б; ТЗ 8.8.1, Д-23; ADR 0024 §9;
docs/progress/e18-api.md §9): the secondary variable at the nodes and the categories of the samples.

    v = node_values(model, p, name, tick)          f8 [n]: the field `name` of the block model at the nodes `p`
    cat = sample_codes(project, sel, samples)      int64 [ns]: the category code of every sample (−1 — no category)

`node_values` — the nodes are the parents `p` (ascending) of the targets; the value of a node is the mean of the finite
values of the rows of its parent weighted by the volume of the row (the sub-blocks of a parent: `Chunk.w`), NaN — no
row of the parent has a value (a gap is never a 0). `sample_codes` — the numeric field of the samples read again from
the table and aligned to the samples of the job by `samples.row_id` (the sample is the first row of its group of
coincident points: the categories of the others are not averaged, a mean of codes is no code); a value that is not a
whole number or is a gap has code −1 (no datum for the simulation). Light: numpy and the readers, no numba.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.api.errors import ApiError

if TYPE_CHECKING:
    from bf_core.blocks.model import Model

    from .spec import SimulationSpec

Tick = Callable[[float | None, str | None], None]
CHUNK_ROWS = 1 << 18


def node_values(model: Model, p: np.ndarray, name: str, tick: Tick | None = None) -> np.ndarray:
    """The field `name` at the nodes `p` (module docstring); 404 `block_field_not_found`, 422 `field_not_number`."""
    from . import targets

    spec = model.field(name)
    if spec.get("kind") != "number":
        raise ApiError(422, "field_not_number", f"Поле «{spec['name']}» — категориальное.",
                       "Выберите числовое поле модели, например плотность.", {"param": "secondary.field"})
    n = int(p.size)
    total = np.zeros(n)
    weight = np.zeros(n)
    for ch in model.iter([str(spec["name"])], CHUNK_ROWS):
        if tick is not None:
            tick(None, None)
        pos, found = targets.locate(p, ch.p)
        x = np.asarray(ch.cols[str(spec["name"])], dtype=np.float64)
        vol = np.ones(ch.n) if ch.w is None else np.prod(np.asarray(ch.w, dtype=np.float64), axis=1)
        ok = found & np.isfinite(x)
        if bool(ok.any()):
            total += np.bincount(pos[ok], weights=(x * vol)[ok], minlength=n)
            weight += np.bincount(pos[ok], weights=vol[ok], minlength=n)
    out = np.full(n, np.nan)
    has = weight > 0.0
    out[has] = total[has] / weight[has]
    return out


def sample_codes(project: Any, spec: SimulationSpec, samples: Any) -> np.ndarray:
    """The category code of every sample of `samples` (module docstring)."""
    from bf_core.db import dialect
    from bf_core.stats import source

    sel = spec.samples
    with dialect.read_tx(project.engine) as conn:
        table = source.find_table(conn, sel.set_id, sel.table)
    cols = source.read(project, sel.set_id, table.id, [sel.field])
    x = np.asarray(cols.number(sel.field), dtype=np.float64)
    ids = np.asarray(cols.row_ids, dtype=np.int64)
    order = np.argsort(ids, kind="stable")
    at = np.minimum(np.searchsorted(ids[order], samples.row_id), max(ids.size - 1, 0))
    if ids.size == 0 or not bool((ids[order][at] == samples.row_id).all()):
        raise ApiError(409, "samples_changed", "Таблица проб изменилась, пока шёл расчёт.",
                       "Запустите моделирование ещё раз.")  # fmt: skip
    v = x[order[at]]
    whole = np.isfinite(v) & (v == np.floor(np.where(np.isfinite(v), v, 0.0)))
    return np.where(whole, v, -1.0).astype(np.int64)
