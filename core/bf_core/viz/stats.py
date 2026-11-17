"""Statistics of a field over all rows of a table for the display forms (ADR 0007 §1 `field-stats`,
`class-counts`): «Шкала», «Цвет», the class editor — by all the data, not by the parts loaded in 3D.

Numbers: count, missing (NaN — never 0), min, max, p02, p50, p98, p99 (linear interpolation), histograms of 48
bins, linear over [min; max] and logarithmic (log10) over the positive values. Text: the categories with
their count and length (Σ «до» − «от»), most frequent first, at most 1000. Events have no length (null).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from bf_core.api.errors import ApiError

from .keys import FieldRef, TableRef

BINS = 48
MAX_CATEGORIES = 1000
MAX_BOUNDS = 1000
QUANTILES = {"p02": 2.0, "p50": 50.0, "p98": 98.0, "p99": 99.0}


def _lengths(snap: Any, table: TableRef, rows: np.ndarray) -> np.ndarray | None:
    if table.kind == "event":
        return None
    return snap.data["to"][rows] - snap.data["from"][rows]  # NaN where a depth is missing


def _hist(values: np.ndarray) -> dict[str, Any] | None:
    if not len(values):
        return None
    lo, hi = float(values.min()), float(values.max())
    counts, _edges = np.histogram(values, bins=BINS, range=(lo, hi) if hi > lo else (lo - 0.5, hi + 0.5))
    return {"lo": lo, "hi": hi, "counts": counts.tolist()}


def field_stats(snap: Any, table: TableRef, field: FieldRef) -> dict[str, Any]:
    rows = np.flatnonzero(snap.alive)
    values = snap.data[field.key][rows]
    out: dict[str, Any] = {
        "table": table.name,
        "field": field.name,
        "kind": field.kind,
        "unit": field.unit,
        "rows": int(len(rows)),
    }
    if field.kind == "number":
        present = values[~np.isnan(values)]
        out.update(count=int(len(present)), missing=int(len(values) - len(present)))
        if len(present):
            q = np.percentile(present, list(QUANTILES.values()))
            out.update(min=float(present.min()), max=float(present.max()))
            out.update({k: float(v) for k, v in zip(QUANTILES, q.tolist(), strict=True)})
        else:
            out.update(min=None, max=None, **dict.fromkeys(QUANTILES))
        positive = present[present > 0]
        out["hist"] = _hist(present)
        out["hist_log"] = _hist(np.log10(positive))
        out["nonpositive"] = int(len(present) - len(positive))
        return out
    from .intervals import codes

    idx, cats = codes(values)
    known = idx >= 0
    counts = np.bincount(idx[known], minlength=len(cats))
    lengths = _lengths(snap, table, rows)
    by_length = None
    if lengths is not None:
        ok = known & ~np.isnan(lengths)
        by_length = np.bincount(idx[ok], weights=lengths[ok], minlength=len(cats))
    top = sorted(range(len(cats)), key=lambda i: (-int(counts[i]), cats[i]))[:MAX_CATEGORIES]
    out.update(
        count=int(known.sum()),
        missing=int((~known).sum()),
        unique=len(cats),
        truncated=len(cats) > MAX_CATEGORIES,
        categories=[
            {
                "value": cats[i],
                "count": int(counts[i]),
                "length": None if by_length is None else float(by_length[i]),
            }
            for i in top
        ],
    )
    return out


def check_bounds(bounds: list[float]) -> np.ndarray:
    b = np.asarray(bounds, dtype=np.float64)
    if len(b) > MAX_BOUNDS:
        raise ApiError(422, "too_many_bounds", f"Границ классов больше {MAX_BOUNDS}.", "Уменьшите число классов.")
    if not np.all(np.isfinite(b)) or np.any(np.diff(b) < 0):
        raise ApiError(
            422, "bad_bounds", "Границы классов должны быть числами по возрастанию.", "Исправьте границы классов."
        )
    return b


def class_counts(snap: Any, table: TableRef, field: FieldRef, bounds: np.ndarray) -> dict[str, Any]:
    """Classes (−∞; b0), [b0; b1) … [b(n−1); +∞): a value equal to a bound belongs to the upper class (BF
    `classCounts`); empty values are in no class and are counted in `missing`."""
    if field.kind != "number":
        raise ApiError(
            422, "not_number", f"Поле «{field.name}» не числовое.", "Классы по границам строятся для числовых полей."
        )
    rows = np.flatnonzero(snap.alive)
    values = snap.data[field.key][rows]
    present = ~np.isnan(values)
    cls = np.searchsorted(bounds, values[present], side="right")
    counts = np.bincount(cls, minlength=len(bounds) + 1)
    lengths = _lengths(snap, table, rows)
    out_len = None
    if lengths is not None:
        lp = lengths[present]
        ok = ~np.isnan(lp)
        out_len = np.bincount(cls[ok], weights=lp[ok], minlength=len(bounds) + 1).tolist()
    return {
        "counts": counts.tolist(),
        "lengths": out_len,
        "missing": int((~present).sum()),
        "rows": int(len(rows)),
    }
