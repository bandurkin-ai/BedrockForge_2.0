"""The rows of a block file in the order of the model (`object_blockfile.py`): pass 2 — the cells (P, s, w) of every
block by the grid inferred (a file the grid cannot hold is refused with its reason and the choices, `blocks.grid_diag`)
and the sort of the rows with the repeated ones left out and counted (ТЗ 7.31, 8.7.2; ADR 0023 §4, ADR 0030 §5–6).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.blocks import geometry as geo
from bf_core.blocks import schema as bs
from bf_core.blocks.grid_diag import Sample, choices_of, diagnose
from bf_core.blocks.grid_infer import OK, GridScan, Unaligned

from .blockfile_parts import Resolved, Spool, Tick, _err, busy

PORTION = bs.ROW_GROUP_V2  # rows written and taken from the spool at a time


@dataclass
class Cells:
    """Pass 2: the rows (P, s, w) of the blocks in the order of the file; `valid` — the raw rows that are blocks."""

    p: np.ndarray
    s: np.ndarray | None
    w: np.ndarray | None
    valid: np.ndarray  # the raw row numbers (ascending) of the rows that are blocks
    invalid: int
    check3: Any
    shifted: int = 0  # blocks whose edges were moved to the lattice by more than 1e-6 m (ADR 0030 decision 4)
    max_shift: float = 0.0


def _cells(scan: GridScan, inferred: Any, spool: Spool, rows: int, res: Resolved, tick: Tick,
           columns: Mapping[str, str] | None = None) -> Cells:  # fmt: skip
    """Pass 2 over the spool by portions. Blocks the grid cannot hold → 422 `blocks_unaligned` with `diag` (from
    the statistics of `scan` and an equal-step sample of the rows) and `choices`."""
    sub = inferred.sub
    sample = Sample(rows)
    p = np.empty(rows, dtype=np.uint64)
    bad = np.empty(rows, dtype=np.int8)
    s = np.empty((rows, 3), dtype=np.uint8) if sub else None
    w = np.empty((rows, 3), dtype=np.uint8) if sub else None
    lost = Unaligned()
    names = ("x", "y", "z", "dx", "dy", "dz") if res.sized else ("x", "y", "z")

    def geometry(a: int, b: int) -> tuple[np.ndarray, np.ndarray | None]:
        c = np.column_stack([spool.read(k, a, b) for k in names[:3]])
        return c, (np.column_stack([spool.read(k, a, b) for k in names[3:]]) if res.sized else None)

    for a in range(0, rows, PORTION):
        b = min(rows, a + PORTION)
        c, d = geometry(a, b)
        sample.add(a, c, d)
        cells = inferred.rows_of(c, d)
        lost.add(a, cells)
        p[a:b], bad[a:b] = cells.p, cells.bad
        if s is not None and w is not None and cells.s is not None and cells.w is not None:
            s[a:b], w[a:b] = cells.s, cells.w
        tick(0.45 + 0.05 * b / rows, "Сетка и ячейки")
    if lost.count:
        diag = diagnose(scan, inferred, *sample.arrays(), rows=rows, columns=columns, lost=lost)
        lost.raise_if(diag, choices_of(diag, inferred.grid.rotation))
    valid = np.flatnonzero(bad == OK)
    if not len(valid):
        raise _err("block_model_invalid", "В файле нет ни одного блока с центром и размером.",
                   "Проверьте столбцы центров и размеров.")  # fmt: skip
    three = sorted({int(valid[0]), int(valid[len(valid) // 2]), int(valid[-1])})
    rows3 = [geometry(r, r + 1) for r in three]
    centres3 = np.vstack([c for c, _ in rows3])  # type: ignore[misc]
    sizes3 = np.vstack([d for _, d in rows3]) if res.sized else None  # type: ignore[misc]
    check3 = inferred.check3(centres3, sizes3, three)
    if len(valid) != rows:  # rows without a centre or a size are not blocks
        p, s, w = p[valid], None if s is None else s[valid], None if w is None else w[valid]
    return Cells(p, s, w, valid, lost.invalid, check3, lost.shifted, lost.max_shift)


# --- sorting, repeated rows ---------------------------------------------------------------------------------------


@dataclass
class Arranged:
    """The blocks in the order of the model: `sel` — their raw rows; the repeated ones are left out and counted."""

    sel: np.ndarray
    p: np.ndarray
    s: np.ndarray | None
    w: np.ndarray | None
    counts: dict[str, int]
    examples: dict[str, list[dict[str, Any]]]


def _arrange(cells: Cells, rows: int, tick: Tick) -> Arranged:
    p, s, w = cells.p, cells.s, cells.w
    order = busy(lambda: bs.sort_rows(p, s), tick, 0.5, "Сортировка")
    sel = order if len(cells.valid) == rows else cells.valid[order]
    p, s, w = p[order], None if s is None else s[order], None if w is None else w[order]
    del order
    key = geo.row_keys(p, s)
    same = np.zeros(len(key), dtype=bool)
    same[1:] = key[1:] == key[:-1]
    del key
    counts = {"duplicate": 0, "overlap": 0}
    examples: dict[str, list[dict[str, Any]]] = {"duplicate": [], "overlap": []}
    if same.any():
        head = np.maximum.accumulate(np.where(same, 0, np.arange(len(same))))
        exact = same if w is None else same & (w == w[head]).all(axis=1)
        for kind, mask in (("duplicate", exact), ("overlap", same & ~exact)):
            at = np.flatnonzero(mask)
            counts[kind] = len(at)
            for i in at[: bs.MAX_EXAMPLES]:
                sub = None if s is None or w is None else [*map(int, s[i]), *map(int, w[i])]
                examples[kind].append({"check": kind, "row": int(sel[i]), "p": int(p[i]),
                                       "other_row": int(sel[head[i]]), "sub": sub})  # fmt: skip
        keep = ~same
        sel, p, s, w = sel[keep], p[keep], None if s is None else s[keep], None if w is None else w[keep]
    return Arranged(sel, p, s, w, counts, examples)


