"""Intervals of one part for 3D: `GET /viz/sets/{id}/intervals/{k}?table=&fields=` (ADR 0007 §1).

BFA1 `intervals`: the rows of the holes of part k in order (slot, «от»): `slot u2`, `from f8`, `to f8` (NaN
for events), and per field `v:<name> f8` (NaN — empty, never 0) or `c:<name> i4` — the index in the
dictionary of the part `cats {name: [values]}` of the header, −1 — empty. The rows come from the column
snapshot of the table (`drillholes.snapshot`) through its index of rows by hole; rows without «от» are left
out (they cannot be placed on a hole).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from bf_core.api.errors import ApiError

from . import binary
from .holes import layout
from .keys import FORMAT, FieldRef, SetState, TableRef
from .paths import part_not_found

MAX_FIELDS = 16


def snapshot(project: Any, set_id: int, table: TableRef) -> Any:
    from bf_core.drillholes.router import _snapshot

    snap = _snapshot(project, set_id, str(table.id))
    return snap


def data_changed() -> ApiError:
    return ApiError(409, "data_changed", "Данные таблицы изменились во время запроса.", "Повторите запрос.")


def rows_of_holes(snap: Any, hole_ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Snapshot rows of the holes in their order (by «от» within a hole) and the position of each row's hole."""
    rows, holes, starts = snap.hole_index()
    if not len(holes) or not len(hole_ids):
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    pos = np.searchsorted(holes, hole_ids).clip(0, len(holes) - 1)
    found = holes[pos] == hole_ids
    a = np.where(found, starts[pos], 0)
    counts = np.where(found, starts[pos + 1] - starts[pos], 0)
    total = int(counts.sum())
    first = np.cumsum(counts) - counts  # where each hole's rows start in the output
    take = np.repeat(a - first, counts) + np.arange(total)
    return rows[take], np.repeat(np.arange(len(hole_ids)), counts)


def codes(values: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """Text values → indices in the sorted dictionary of their distinct values; None and "" → −1."""
    import pandas as pd

    try:
        idx, uniq = pd.factorize(values, sort=True)  # None → −1
    except TypeError:  # values of several types do not sort: as text
        idx, uniq = pd.factorize(np.array([None if v is None else str(v) for v in values], dtype=object), sort=True)
    cats = [str(v) for v in uniq]
    out = idx.astype(np.int32)
    if "" in cats:
        e = cats.index("")
        out[out == e] = -1
        out[out > e] -= 1
        cats.pop(e)
    return out, cats


def part_intervals(
    project: Any, state: SetState, method: str, table: TableRef, fields: list[FieldRef], k: int
) -> bytes:
    lay = layout(project, state.set_id, method)
    if not 0 <= k < len(lay.parts):
        raise part_not_found(k)
    snap = snapshot(project, state.set_id, table)
    rows, slot = rows_of_holes(snap, lay.st.hole_ids[lay.holes_of(k)])
    start = snap.data["from"][rows]
    end = np.full(len(rows), np.nan) if table.kind == "event" else snap.data["to"][rows]
    arrays: list[tuple[str, Any, str]] = [("slot", slot, "<u2"), ("from", start, "<f8"), ("to", end, "<f8")]
    cats: dict[str, list[str]] = {}
    for f in fields:
        col = snap.data.get(f.key)
        if col is None:
            raise data_changed()
        if f.kind == "number":
            arrays.append((f"v:{f.name}", col[rows], "<f8"))
        else:
            c, cats[f.name] = codes(col[rows])
            arrays.append((f"c:{f.name}", c, "<i4"))
    header = {
        "kind": "intervals",
        "format": FORMAT,
        "set": state.set_id,
        "version": state.version,
        "method": method,
        "k": k,
        "n": lay.parts[k].n,
        "rows": int(len(rows)),
        "table": {"name": table.name, "kind": table.kind, "rev": table.rev},
        "fields": [{"name": f.name, "kind": f.kind, "unit": f.unit} for f in fields],
        "cats": cats,
    }
    return binary.encode(header, arrays)
