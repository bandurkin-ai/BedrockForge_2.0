"""Pages, sorting, filters and field statistics of a table snapshot (ТЗ 7.10 «Просмотр», «Сортировка»,
«Выбор скважины», «Σ Статистика»; 7.34 п. 21).

Sorting: ascending → descending → off (the caller sends none); missing values last in both directions;
text sorts case-insensitively, holes by name. Sort orders are cached per snapshot, so turning pages of a
5 million-row table only cuts a slice.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .expr import evaluate_filter
from .snapshot import Snapshot

MAX_LIMIT = 1000


def resolver(snap: Snapshot) -> Any:
    """Column by its name (case-insensitive) or key, for expressions."""
    by_name = {c.name.casefold(): c for c in snap.columns}
    by_key = {c.key.casefold(): c for c in snap.columns}  # fixed columns also by key: FROM, TO, HOLE, X…

    def resolve(name: str) -> tuple[str, np.ndarray]:
        c = by_name.get(name.casefold()) or by_key.get(name.casefold())
        if c is None:
            raise KeyError(name)
        return c.kind, snap.values(c.key)

    return resolve


def _order(snap: Snapshot, key: str, desc: bool) -> np.ndarray:
    """Indices of all rows sorted by a column, missing last; stable (ties keep the row order)."""
    cached = snap.orders.get((key, desc))
    if cached is not None:
        return cached
    col = snap.column(key)
    n = len(snap.ids)
    if key == "hole":
        names = sorted(set(snap.hole_names.values()), key=lambda s: (s.casefold(), s))
        rank = {name: i for i, name in enumerate(names)}
        values = np.array([rank.get(snap.hole_names.get(int(h), ""), -1) for h in snap.hole_ids], dtype=float)
        values[values < 0] = np.nan
    elif col.kind == "number":
        values = snap.data[key]
    else:
        raw = snap.data[key]
        folded = sorted({str(v).casefold() for v in raw if v not in (None, "")})
        rank = {v: i for i, v in enumerate(folded)}  # case-insensitive: equal ranks keep the row order
        values = np.array([rank[str(v).casefold()] if v not in (None, "") else -1 for v in raw], dtype=float)
        values[values < 0] = np.nan
    missing = np.isnan(values)
    keyed = np.where(missing, 0.0, -values if desc else values)
    order = np.lexsort((np.arange(n), keyed, missing))  # last key is primary: present first, then value
    snap.orders[(key, desc)] = order
    return order


def mask(snap: Snapshot, hole: int | None = None, where: str | None = None) -> np.ndarray:
    m = snap.alive.copy()
    if hole is not None:
        m &= snap.hole_ids == hole
    if where:
        m &= evaluate_filter(where, resolver(snap), len(snap.ids))
    return m


def _cell(v: Any) -> Any:
    if isinstance(v, float | np.floating):
        return None if math.isnan(v) else float(v)
    return v


def page(
    snap: Snapshot,
    *,
    offset: int = 0,
    limit: int = 100,
    sort: str | None = None,
    desc: bool = False,
    hole: int | None = None,
    where: str | None = None,
) -> dict[str, Any]:
    limit = max(1, min(limit, MAX_LIMIT))
    m = mask(snap, hole, where)
    if sort:
        order = _order(snap, sort, desc)
        idx = order[m[order]]
    else:
        idx = np.flatnonzero(m)
    total = len(idx)
    cut = idx[offset : offset + limit]
    keys = [c.key for c in snap.columns]
    holes = snap.hole_names
    rows = []
    for i in cut.tolist():
        row: dict[str, Any] = {"id": int(snap.ids[i]), "hole_id": int(snap.hole_ids[i])}
        for k in keys:
            row[k] = holes.get(int(snap.hole_ids[i])) if k == "hole" else _cell(snap.data[k][i])
        rows.append(row)
    return {"total": total, "offset": offset, "rows": rows}


def stats(snap: Snapshot, key: str, *, hole: int | None = None, where: str | None = None) -> dict[str, Any]:
    """Σ of one field over the rows shown (selected hole and filter)."""
    col = snap.column(key)
    m = mask(snap, hole, where)
    values = snap.values(key)[m]
    n = len(values)
    if col.kind == "number":
        present = values[~np.isnan(values)]
        out: dict[str, Any] = {"kind": "number", "rows": n, "filled": int(len(present)), "empty": n - len(present)}
        if len(present):
            out.update(
                min=float(present.min()),
                max=float(present.max()),
                mean=float(present.mean()),
                median=float(np.median(present)),
                std=float(present.std(ddof=1)) if len(present) > 1 else 0.0,
            )
        return out
    filled = [v for v in values if v not in (None, "")]
    counts: dict[Any, int] = {}
    for v in filled:
        counts[v] = counts.get(v, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[:10]
    return {
        "kind": "text",
        "rows": n,
        "filled": len(filled),
        "empty": n - len(filled),
        "unique": len(counts),
        "top": [{"value": v, "count": c} for v, c in top],
    }
