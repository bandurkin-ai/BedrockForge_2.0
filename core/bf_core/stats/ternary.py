"""Ternary diagram of three fields (ТЗ 8.3; ADR 0019 §4): every row as the shares of its three values in
their sum.

    shares(a, b, c, tick=None) -> Shares
        a, b, c (float64 — the shares of the rows taken, a + b + c = 1), rows (int64 — the numbers of the rows
        taken, ascending), counts {rows, missing, negative, zero_sum, used}; .n; .require()
    plane(s) -> (x, y)            the points in the plane of the triangle: x = b + c / 2, y = c·√3 / 2
    ternary_chart(a, b, c, color=None, row_ids=None, max_points=200_000, cells=100, tick=None) -> dict
        the answer of `POST /stats/ternary` but the names: {counts, mode, points {a, b, c, row, color} | None,
        density {x_edges, y_edges, counts, max} | None, means {a, b, c}, conventions}
    warm()

A row takes part when all three values are there (NaN, ±inf — missing), none of them is below zero and their
sum is above zero: its point is (a, b, c) / (a + b + c). Rows with a missing value, with a value < 0 (a point
outside the triangle) and with a sum ≤ 0 are counted (`missing`, `negative`, `zero_sum`) and not drawn.
A missing value never becomes 0.

The triangle: the corner of the first field is (0, 0), of the second — (1, 0), of the third — (1 / 2, √3 / 2);
a row lies at x = b + c / 2, y = c·√3 / 2 of its shares. Up to `max_points` (200 000) rows are sent as points
(the shares, `row` — the number of the row or `row_ids[row]`, `color` — the value of the colour field);
more — as the numbers of rows in cells × cells (100 × 100) equal cells of [0; 1] × [0; √3 / 2] (`density`, the
form of the scatter plot; cells outside the triangle are empty), Σ = the rows taken. `means` — the mean shares.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.jit import njit

from .scatter import CELLS, MAX_POINTS, grid_counts
from .weighted import StatsError, Tick

HEIGHT = math.sqrt(3.0) / 2.0  # of the triangle with the side 1
CONVENTIONS = (
    "Точка — доли трёх полей в их сумме: (a, b, c) / (a + b + c). Строки с пропуском, со значением меньше нуля "
    "или с суммой ≤ 0 не показаны."
)
_BLOCK = 1 << 20  # rows between two calls of `tick`


@dataclass(frozen=True)
class Shares:
    """The rows a ternary diagram takes (see the module docstring)."""

    a: np.ndarray
    b: np.ndarray
    c: np.ndarray
    rows: np.ndarray
    counts: dict[str, int]

    @property
    def n(self) -> int:
        return int(self.a.shape[0])

    def require(self) -> Shares:
        """The shares themselves, or the refusal `no_values` whose hint says what the rows were left out for."""
        if self.n:
            return self
        c = self.counts
        parts = [f"строк — {c['rows']}"]
        parts += [f"{title} — {c[key]}" for key, title in (("missing", "с пропуском"), ("negative", "со значением "
                  "меньше нуля"), ("zero_sum", "с суммой ≤ 0")) if c[key]]  # fmt: skip
        raise StatsError(
            "no_values", "Нет строк для тернарной диаграммы.", f"Отбор: {', '.join(parts)}. Проверьте три поля."
        )


@njit(cache=True, nogil=True)
def _take(a, b, c, lo, hi, k, oa, ob, oc, orow, cnt):  # type: ignore[no-untyped-def]
    """Rows lo … hi − 1 into shares; the rows taken are written from position k on. Returns the new k."""
    for i in range(lo, hi):
        x = a[i]
        y = b[i]
        z = c[i]
        if not (np.isfinite(x) and np.isfinite(y) and np.isfinite(z)):
            cnt[0] += 1
            continue
        if x < 0.0 or y < 0.0 or z < 0.0:
            cnt[1] += 1
            continue
        s = x + y + z
        if not (s > 0.0 and np.isfinite(s)):
            cnt[2] += 1
            continue
        oa[k] = x / s
        ob[k] = y / s
        oc[k] = z / s
        orow[k] = i
        k += 1
    return k


def shares(a: Any, b: Any, c: Any, *, tick: Tick | None = None) -> Shares:
    """The rows of three columns of one length as shares of their sum (see the module docstring)."""
    cols = [np.ascontiguousarray(col, dtype=np.float64) for col in (a, b, c)]
    n = cols[0].shape[0]
    if any(col.ndim != 1 or col.shape[0] != n for col in cols):
        raise ValueError("a, b and c must be 1D of one length")
    oa, ob, oc, orow = np.empty(n), np.empty(n), np.empty(n), np.empty(n, dtype=np.int64)
    cnt = np.zeros(3, dtype=np.int64)
    k = 0
    for lo in range(0, n, _BLOCK):
        hi = min(lo + _BLOCK, n)
        k = _take(cols[0], cols[1], cols[2], lo, hi, k, oa, ob, oc, orow, cnt)
        if tick is not None:
            tick(hi / n)
    if n == 0 and tick is not None:
        tick(1.0)
    counts = {"rows": n, "missing": int(cnt[0]), "negative": int(cnt[1]), "zero_sum": int(cnt[2]), "used": int(k)}
    return Shares(oa[:k], ob[:k], oc[:k], orow[:k], counts)


def plane(s: Shares) -> tuple[np.ndarray, np.ndarray]:
    """The points of the shares in the plane of the triangle (0, 0) — (1, 0) — (1 / 2, √3 / 2)."""
    return np.asarray(s.b + 0.5 * s.c), np.asarray(s.c * HEIGHT)


def _column(a: np.ndarray) -> list[Any]:
    """A column of the answer: numbers, None for what is not finite; whole numbers (codes) as they are."""
    if a.dtype.kind in "iub":
        return [int(x) for x in a.tolist()]
    return [x if math.isfinite(x) else None for x in np.asarray(a, dtype=np.float64).tolist()]


def ternary_chart(
    a: Any, b: Any, c: Any, *, color: Any = None, row_ids: Any = None, max_points: int = MAX_POINTS,
    cells: int = CELLS, tick: Tick | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The answer of a ternary diagram for three columns of one length (NaN — missing); `color` — the column of
    the colour field (numbers, or the codes of a text field), `row_ids` — the ids of the rows for `points.row`
    (None — the numbers of the rows). StatsError `no_values` — no row to draw."""
    step = (lambda f: None) if tick is None else tick
    s = shares(a, b, c, tick=lambda f: step(0.6 * f)).require()
    points = cloud = None
    if s.n <= max_points:
        points = {
            "a": s.a.tolist(),
            "b": s.b.tolist(),
            "c": s.c.tolist(),
            "row": (s.rows if row_ids is None else np.asarray(row_ids)[s.rows]).tolist(),
            "color": None if color is None else _column(np.asarray(color)[s.rows]),
        }
    else:
        x, y = plane(s)
        counts = grid_counts(x, y, 0.0, 1.0, 0.0, HEIGHT, cells, cells, tick=lambda f: step(0.6 + 0.4 * f))
        cloud = {
            "x_edges": np.linspace(0.0, 1.0, cells + 1).tolist(),
            "y_edges": np.linspace(0.0, HEIGHT, cells + 1).tolist(),
            "counts": counts.tolist(),
            "max": int(counts.max()),
        }
    step(1.0)
    return {
        "counts": s.counts,
        "mode": "points" if points is not None else "density",
        "points": points,
        "density": cloud,
        "means": {"a": float(s.a.mean()), "b": float(s.b.mean()), "c": float(s.c.mean())},
        "conventions": CONVENTIONS,
    }


def warm() -> None:
    """Compile the kernels on a few rows (the `stats` step of the warm-up)."""
    rows = np.array([1.0, 3.0, 2.0, np.nan])
    ternary_chart(rows, rows + 1.0, rows * 2.0, max_points=2)
