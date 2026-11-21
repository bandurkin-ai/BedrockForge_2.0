"""Scatter plot of two fields (ТЗ 8.3; ADR 0019 §4): the pairs of values, the regressions, the correlations,
the density of a large cloud and the rows inside a frame or a lasso (№ 237).

    pairs(x, y, weights=None, log_x=False, log_y=False, tick=None) -> Pairs
        the rows where both fields have a value: u, v (float64 — the coordinates on the axes: log10 of the
        value on a log axis), w (the weights, None — every row 1), rows (int64 — the numbers of the rows taken,
        ascending), counts {rows, missing, bad_weight, nonpositive, used}; .n; .require() -> self or `no_values`
    regressions(p) -> dict        {n, weight_sum, x {mean, std}, y {mean, std}, pearson, ols {a, b}, rma {a, b},
                                   weighted {a, b, pearson, x {mean, std}, y {mean, std}} | None}
    spearman(p) -> float | None   Pearson of the average ranks (without weights)
    density(p, cells=100) -> dict {x_edges, y_edges (data units), counts [ny][nx], max}; Σ counts = p.n
    scatter_chart(x, y, weights=None, log_x=False, log_y=False, color=None, row_ids=None, with_spearman=True,
                  max_points=200_000, cells=100, tick=None) -> dict
        the answer of `POST /stats/scatter` but the names: {log_x, log_y, weighted, counts, mode, points
        {x, y, row, c} | None, density | None, range {x, y}, stats (`regressions` + spearman), conventions}
    rows_in_rect(p, x0, x1, y0, y1) -> int64     the rows inside a frame (its borders too), data units
    rows_in_polygon(p, polygon, tick=None, threads=None) -> int64
                                                 the rows inside a lasso [[x, y], …] (data units), even-odd rule
    grid_counts(u, v, u0, u1, v0, v1, nx, ny) -> int64 [ny, nx]     (the ternary diagram takes it too)
    warm()

A pair takes part when both values are there (NaN, ±inf — missing: «пара с пропуском не участвует») and, with
weights, the row has a weight > 0; on a log axis values ≤ 0 have no place — they are counted (`nonpositive`)
and left out. A missing value never becomes 0.

Regressions (in the coordinates of the axes: on a log axis x and y are log10 of the values, the line is
y = a + b·x there). МНК (`ols`): b = cov / σ²_x, a = ȳ − b·x̄. RMA (`rma`, reduced major axis): b = sign(r)·σ_y /
σ_x, a = ȳ − b·x̄. Both and `pearson` are without weights; `weighted` — the same МНК and r with the weights of
the rows (None without weights). Spearman — r of the average ranks, without weights. The sums are those of
`correlation.pair_moments` (two passes, compensated): y = 2x + 1 gives b = 2, a = 1 within 1e−12 (Т8.3-1).
What is not defined (fewer than two pairs, no spread of x) is None.

More than `max_points` (200 000) pairs are not sent as points: `mode` is "density" — the numbers of pairs in
cells × cells (100 × 100) equal cells of the axes between the least and the greatest coordinate (a pair on the
upper border belongs to the last cell). Up to that — "points": the values in data units, `row` — the number of
the row (`row_ids[row]` when given: dh_rows.id), `c` — the value of the colour field of the row (None — none).

Inside a lasso (≤ `MAX_VERTICES` = 1000 vertices, else 422 `bad_param` {param: polygon}): the even-odd rule in the
coordinates of the axes (a straight side of the lasso on the screen is straight there), each pair against the
sides of its band of Y only (`BANDS`), a numba kernel over blocks of 2**18 pairs in threads; a point on a border
belongs to the polygon on its lower and left sides (the crossing rule), so two lassos that share a side never
take a point twice.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.jit import njit

from .correlation import average_ranks, pair_moments, run_tasks, thread_count
from .select import bad_param
from .weighted import StatsError, Tick

MAX_POINTS = 200_000
CELLS = 100
MAX_VERTICES = 1_000  # of a lasso (a hand-drawn lasso is thinned by the window to fewer)
BANDS = 256  # bands of Y of a lasso: a pair is tested against the sides of its band only
PARALLEL_FROM = 200_000  # pairs from which the two sides are ranked in two threads
CONVENTIONS = (
    "МНК: b = cov / σ²x, a = ȳ − b·x̄. RMA: b = sign(r)·σy / σx. Взвешенная — МНК с весами строк. "
    "На логарифмической оси расчёт идёт по log10 значений; значения ≤ 0 не участвуют."
)
_BLOCK = 1 << 20  # rows between two calls of `tick`
_LASSO_BLOCK = 1 << 18  # pairs of a task of the lasso
_NONE = np.zeros(0)


@dataclass(frozen=True)
class Pairs:
    """The pairs a scatter plot takes (see the module docstring)."""

    u: np.ndarray
    v: np.ndarray
    w: np.ndarray | None
    rows: np.ndarray
    counts: dict[str, int]
    log_x: bool = False
    log_y: bool = False

    @property
    def n(self) -> int:
        return int(self.u.shape[0])

    def require(self) -> Pairs:
        """The pairs themselves, or the refusal `no_values` whose hint says what the rows were left out for."""
        if self.n:
            return self
        c = self.counts
        parts = [f"строк — {c['rows']}"]
        parts += [f"{title} — {c[key]}" for key, title in (("missing", "без значения X или Y"),
                  ("bad_weight", "без веса"), ("nonpositive", "≤ 0 на логарифмической оси")) if c[key]]  # fmt: skip
        raise StatsError(
            "no_values", "Нет пар значений для графика рассеяния.",
            f"Отбор: {', '.join(parts)}. Проверьте поля, вес и логарифмические оси.",
        )  # fmt: skip


# --- kernels -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _take(x, y, w, has_w, log_x, log_y, a, b, k, ou, ov, ow, orow, cnt):  # type: ignore[no-untyped-def]
    """Rows a … b − 1 through the rules; the pairs taken are written from position k on. Returns the new k."""
    for i in range(a, b):
        p = x[i]
        q = y[i]
        if not (np.isfinite(p) and np.isfinite(q)):
            cnt[0] += 1
            continue
        if has_w:
            t = w[i]
            if not (np.isfinite(t) and t > 0.0):
                cnt[1] += 1
                continue
            ow[k] = t
        if (log_x and p <= 0.0) or (log_y and q <= 0.0):
            cnt[2] += 1
            continue
        if log_x:
            p = np.log10(p)
        if log_y:
            q = np.log10(q)
        ou[k] = p
        ov[k] = q
        orow[k] = i
        k += 1
    return k


@njit(cache=True, nogil=True)
def _grid(u, v, u0, du, v0, dv, nx, ny, a, b, out):  # type: ignore[no-untyped-def]
    """The pairs a … b − 1 into the cells out[ny, nx]; a coordinate outside goes into the nearest cell."""
    for i in range(a, b):
        cx = int((u[i] - u0) / du)
        cy = int((v[i] - v0) / dv)
        if cx >= nx:
            cx = nx - 1
        elif cx < 0:
            cx = 0
        if cy >= ny:
            cy = ny - 1
        elif cy < 0:
            cy = 0
        out[cy, cx] += 1


@njit(cache=True, nogil=True)
def _inside(u, v, px, py, x0, x1, y0, y1, inv, start, sides, a, b, out):  # type: ignore[no-untyped-def]
    """out[i] — is the pair i (a … b − 1) inside the polygon px, py by the even-odd rule: a ray to the right
    crosses its sides an odd number of times. The box x0 … y1 of the polygon is checked first; then only the
    sides of the band of Y of the pair (`_bands`: sides[start[k] : start[k + 1]], the side t from the vertex
    t − 1 to t) — the others cannot cross its ray."""
    m = px.shape[0]
    nb = start.shape[0] - 1
    for i in range(a, b):
        x = u[i]
        y = v[i]
        if x < x0 or x > x1 or y < y0 or y > y1:
            out[i] = False
            continue
        k = int((y - y0) * inv)
        if k > nb - 1:
            k = nb - 1
        odd = False
        for e in range(start[k], start[k + 1]):
            t = sides[e]
            j = t - 1 if t > 0 else m - 1
            yt = py[t]
            yj = py[j]
            if (yt > y) != (yj > y) and x < (px[j] - px[t]) * (y - yt) / (yj - yt) + px[t]:
                odd = not odd
        out[i] = odd


def _bands(py: np.ndarray, y0: float, y1: float) -> tuple[float, np.ndarray, np.ndarray]:
    """The sides of the polygon by bands of Y between y0 and y1: (inv, start [nb + 1], sides). A side crosses the
    ray of y only when min(y_j, y_t) ≤ y < max(y_j, y_t), so it is listed in the bands from that of its lower end
    to that of its upper one — the band of a pair is found by the same expression in `_inside`."""
    m = py.shape[0]
    nb = max(1, min(BANDS, m)) if y1 > y0 else 1
    inv = nb / (y1 - y0) if y1 > y0 else 0.0
    prev = np.roll(py, 1)  # the side t goes from the vertex t − 1
    lo = np.minimum(((np.minimum(py, prev) - y0) * inv).astype(np.int64), nb - 1)
    hi = np.minimum(((np.maximum(py, prev) - y0) * inv).astype(np.int64), nb - 1)
    per = hi - lo + 1
    sides = np.repeat(np.arange(m, dtype=np.int64), per)
    band = np.repeat(lo, per) + (np.arange(sides.shape[0]) - np.repeat(np.cumsum(per) - per, per))
    order = np.argsort(band, kind="stable")
    start = np.zeros(nb + 1, dtype=np.int64)
    np.cumsum(np.bincount(band, minlength=nb), out=start[1:])
    return float(inv), start, np.ascontiguousarray(sides[order])


# --- the pairs ---------------------------------------------------------------------------------------------


def _flag(key: str, value: Any, title: str) -> bool:
    if not isinstance(value, bool):
        raise bad_param(key, f"{title} — это да или нет.", "Передайте true или false.")
    return value


def pairs(
    x: Any, y: Any, weights: Any = None, *, log_x: bool = False, log_y: bool = False, tick: Tick | None = None
) -> Pairs:
    """The rows of two columns of one length that make a point of the plot (see the module docstring)."""
    log_x, log_y = _flag("log_x", log_x, "«Логарифмическая ось X»"), _flag("log_y", log_y, "«Логарифмическая ось Y»")
    xs, ys = np.ascontiguousarray(x, dtype=np.float64), np.ascontiguousarray(y, dtype=np.float64)
    if xs.ndim != 1 or ys.shape != xs.shape:
        raise ValueError("x and y must be 1D of one length")
    has_w = weights is not None
    ws = np.ascontiguousarray(weights, dtype=np.float64) if has_w else _NONE
    if has_w and ws.shape != xs.shape:
        raise ValueError("weights must have the length of x")
    n = xs.shape[0]
    ou, ov, orow = np.empty(n), np.empty(n), np.empty(n, dtype=np.int64)
    ow = np.empty(n) if has_w else _NONE
    cnt = np.zeros(3, dtype=np.int64)
    k = 0
    for a in range(0, n, _BLOCK):
        b = min(a + _BLOCK, n)
        k = _take(xs, ys, ws, has_w, log_x, log_y, a, b, k, ou, ov, ow, orow, cnt)
        if tick is not None:
            tick(b / n)
    if n == 0 and tick is not None:
        tick(1.0)
    counts = {"rows": n, "missing": int(cnt[0]), "bad_weight": int(cnt[1]), "nonpositive": int(cnt[2]), "used": int(k)}
    return Pairs(ou[:k], ov[:k], ow[:k] if has_w else None, orow[:k], counts, log_x, log_y)


# --- regressions, correlations -----------------------------------------------------------------------------


def _num(x: float) -> float | None:
    x = float(x)
    return x if math.isfinite(x) else None


def _fit(p: Pairs, weights: np.ndarray | None) -> dict[str, Any]:
    """The moments of the pairs with the weights (None — without) and the lines that follow from them."""
    m = pair_moments([p.u, p.v], weights, threads=1)
    n, total = int(m.n[0, 1]), float(m.total[0, 1])
    out: dict[str, Any] = {"n": n, "weight_sum": total}
    if n == 0:
        nothing = {"mean": None, "std": None}
        return {**out, "x": nothing, "y": dict(nothing), "pearson": None, "ols": None, "rma": None}
    mx, my = float(m.mean[0, 1]), float(m.mean[1, 0])
    sxx, syy, sxy = float(m.sxx[0, 1]), float(m.sxx[1, 0]), float(m.sxy[0, 1])
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        out["x"] = {"mean": _num(mx), "std": _num(math.sqrt(sxx / total))}
        out["y"] = {"mean": _num(my), "std": _num(math.sqrt(syy / total))}
        den = math.sqrt(sxx * syy)
        r = sxy / den if n >= 2 and den > 0.0 and math.isfinite(den) else math.nan
        out["pearson"] = _num(min(max(r, -1.0), 1.0))
        out["ols"] = out["rma"] = None
        if n >= 2 and sxx > 0.0 and math.isfinite(sxx) and math.isfinite(syy):
            b = sxy / sxx
            out["ols"] = {"a": _num(my - b * mx), "b": _num(b)}
            b = math.copysign(math.sqrt(syy / sxx), sxy) if sxy != 0.0 else 0.0
            out["rma"] = {"a": _num(my - b * mx), "b": _num(b)}
    return out


def regressions(p: Pairs) -> dict[str, Any]:
    """The lines and r of the pairs (see the module docstring): without weights, and `weighted` beside them."""
    out = _fit(p, None)
    out["weighted"] = None
    if p.w is not None:
        w = _fit(p, p.w)
        out["weight_sum"] = w["weight_sum"]
        out["weighted"] = None if w["ols"] is None else {**w["ols"], "pearson": w["pearson"], "x": w["x"], "y": w["y"]}
    return out


def spearman(p: Pairs, *, parallel_from: int = PARALLEL_FROM) -> float | None:
    """Spearman's r of the pairs: Pearson of their average ranks, without weights; None — not defined."""
    if p.n < 2:
        return None
    if p.n >= parallel_from:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="bf-scatter") as pool:
            other = pool.submit(average_ranks, p.v)
            ru = average_ranks(p.u)
            rv = other.result()
    else:
        ru, rv = average_ranks(p.u), average_ranks(p.v)
    m = pair_moments([ru, rv], None, threads=1)
    with np.errstate(invalid="ignore"):
        den = math.sqrt(float(m.sxx[0, 1]) * float(m.sxx[1, 0]))
    return _num(min(max(float(m.sxy[0, 1]) / den, -1.0), 1.0)) if den > 0.0 else None


# --- the density -------------------------------------------------------------------------------------------


def grid_counts(u: Any, v: Any, u0: float, u1: float, v0: float, v1: float, nx: int, ny: int, *,
                tick: Tick | None = None) -> np.ndarray:  # fmt: skip
    """The numbers of points in ny × nx equal cells of [u0; u1] × [v0; v1] (int64 [ny, nx]); a point on the
    upper border — in the last cell. Σ = the number of points."""
    us, vs = np.ascontiguousarray(u, dtype=np.float64), np.ascontiguousarray(v, dtype=np.float64)
    out = np.zeros((ny, nx), dtype=np.int64)
    du, dv = (u1 - u0) / nx, (v1 - v0) / ny
    n = us.shape[0]
    for a in range(0, n, _BLOCK):
        b = min(a + _BLOCK, n)
        _grid(us, vs, u0, du, v0, dv, nx, ny, a, b, out)
        if tick is not None:
            tick(b / n)
    return out


def _span(a: np.ndarray) -> tuple[float, float]:
    """The least and the greatest coordinate; a single value gets a cell of its own width 1 around it."""
    lo, hi = float(a.min()), float(a.max())
    return (lo, hi) if hi > lo else (lo - 0.5, lo + 0.5)


def density(p: Pairs, cells: int = CELLS, *, tick: Tick | None = None) -> dict[str, Any]:
    """The cloud as numbers of pairs in cells × cells cells of the axes (see the module docstring)."""
    p.require()
    (u0, u1), (v0, v1) = _span(p.u), _span(p.v)
    counts = grid_counts(p.u, p.v, u0, u1, v0, v1, cells, cells, tick=tick)
    xe, ye = np.linspace(u0, u1, cells + 1), np.linspace(v0, v1, cells + 1)
    with np.errstate(over="ignore"):
        xe, ye = (10.0**xe if p.log_x else xe), (10.0**ye if p.log_y else ye)
    return {
        "x_edges": [_num(e) for e in xe],
        "y_edges": [_num(e) for e in ye],
        "counts": counts.tolist(),
        "max": int(counts.max()),
    }


# --- the chart ---------------------------------------------------------------------------------------------


def _column(a: np.ndarray) -> list[Any]:
    """A column of the answer: numbers, None for what is not finite; whole numbers (codes) as they are."""
    if a.dtype.kind in "iub":
        return [int(x) for x in a.tolist()]
    values = np.asarray(a, dtype=np.float64)
    if bool(np.isfinite(values).all()):
        return values.tolist()  # type: ignore[no-any-return]
    return [x if math.isfinite(x) else None for x in values.tolist()]


def scatter_chart(
    x: Any, y: Any, weights: Any = None, *, log_x: bool = False, log_y: bool = False, color: Any = None,
    row_ids: Any = None, with_spearman: bool = True, max_points: int = MAX_POINTS, cells: int = CELLS,
    tick: Tick | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The answer of a scatter plot for the columns `x` and `y` of one length (NaN — missing) with the weights
    of the rows (None — every row 1); `color` — the column of the colour field (numbers, or the codes of a text
    field), `row_ids` — the ids of the rows for `points.row` (None — the numbers of the rows). StatsError
    `no_values` — no pair."""
    step = (lambda f: None) if tick is None else tick
    p = pairs(x, y, weights, log_x=log_x, log_y=log_y, tick=lambda f: step(0.3 * f)).require()
    stats = regressions(p)
    step(0.5)
    stats["spearman"] = spearman(p) if with_spearman else None
    step(0.85)
    px, py = np.asarray(x, dtype=np.float64)[p.rows], np.asarray(y, dtype=np.float64)[p.rows]
    span = {"x": [float(px.min()), float(px.max())], "y": [float(py.min()), float(py.max())]}
    points = cloud = None
    if p.n <= max_points:
        points = {
            "x": px.tolist(),
            "y": py.tolist(),
            "row": (p.rows if row_ids is None else np.asarray(row_ids)[p.rows]).tolist(),
            "c": None if color is None else _column(np.asarray(color)[p.rows]),
        }
    else:
        cloud = density(p, cells, tick=lambda f: step(0.85 + 0.15 * f))
    step(1.0)
    return {
        "log_x": p.log_x,
        "log_y": p.log_y,
        "weighted": p.w is not None,
        "counts": p.counts,
        "mode": "points" if points is not None else "density",
        "points": points,
        "density": cloud,
        "range": span,
        "stats": stats,
        "conventions": CONVENTIONS,
    }


# --- the rows inside a frame, a lasso ----------------------------------------------------------------------


def _axis(value: float, log: bool) -> float:
    """A coordinate of the data on its axis: log10 on a log axis (≤ 0 lies below every point)."""
    if not log:
        return value
    return math.log10(value) if value > 0.0 else -math.inf


def rows_in_rect(p: Pairs, x0: float, x1: float, y0: float, y1: float) -> np.ndarray:
    """The numbers of the rows whose point lies inside the frame x0 … x1, y0 … y1 (data units; the borders
    belong to the frame; the corners may come in any order)."""
    bounds = [float(b) for b in (x0, x1, y0, y1)]
    if any(math.isnan(b) for b in bounds):
        raise bad_param("rect", "Рамка выделения задана не числами.", "Выделите область на графике заново.")
    ua, ub = sorted((_axis(bounds[0], p.log_x), _axis(bounds[1], p.log_x)))
    va, vb = sorted((_axis(bounds[2], p.log_y), _axis(bounds[3], p.log_y)))
    return np.asarray(p.rows[(p.u >= ua) & (p.u <= ub) & (p.v >= va) & (p.v <= vb)])


def rows_in_polygon(p: Pairs, polygon: Any, *, tick: Tick | None = None, threads: int | None = None) -> np.ndarray:
    """The numbers of the rows whose point lies inside the lasso `polygon` [[x, y], …] (data units, ≥ 3
    vertices, closed by itself) by the even-odd rule in the coordinates of the axes. Blocks of pairs are checked
    in threads (every pair alone: the answer does not depend on them)."""
    try:
        poly = np.asarray(polygon, dtype=np.float64)
    except (TypeError, ValueError):
        poly = np.zeros((0, 2))
    if poly.ndim != 2 or poly.shape[1] != 2 or poly.shape[0] < 3 or not np.isfinite(poly).all():
        raise bad_param(
            "polygon", "Контур выделения — не меньше трёх точек [x, y] с числами.",
            "Обведите область на графике заново.",
        )  # fmt: skip
    if poly.shape[0] > MAX_VERTICES:
        raise bad_param(
            "polygon", f"В контуре выделения больше {MAX_VERTICES} точек.",
            f"Упростите контур до {MAX_VERTICES} точек или выделите рамкой.",
        )  # fmt: skip
    if (p.log_x and (poly[:, 0] <= 0.0).any()) or (p.log_y and (poly[:, 1] <= 0.0).any()):
        raise bad_param(
            "polygon", "На логарифмической оси точки контура должны быть больше нуля.",
            "Обведите область на графике заново.",
        )  # fmt: skip
    px = np.ascontiguousarray(np.log10(poly[:, 0]) if p.log_x else poly[:, 0])
    py = np.ascontiguousarray(np.log10(poly[:, 1]) if p.log_y else poly[:, 1])
    inside = np.zeros(p.n, dtype=np.bool_)
    box = (float(px.min()), float(px.max()), float(py.min()), float(py.max()))
    bands = _bands(py, box[2], box[3])
    spans = [(a, min(a + _LASSO_BLOCK, p.n)) for a in range(0, p.n, _LASSO_BLOCK)]

    def work(span: tuple[int, int]) -> None:
        _inside(p.u, p.v, px, py, *box, *bands, span[0], span[1], inside)

    def done(i: int, _: None) -> None:
        if tick is not None:
            tick((i + 1) / len(spans))

    run_tasks(spans, work, thread_count(threads, len(spans)), done)
    return np.asarray(p.rows[inside])


def warm() -> None:
    """Compile the kernels on a few rows (the `stats` step of the warm-up)."""
    x = np.array([1.0, 2.0, 3.0, 4.0, np.nan])
    y = np.array([3.0, 5.0, 7.0, 9.5, 1.0])
    scatter_chart(x, y, x, log_x=True, max_points=2)
    p = pairs(x, y)
    rows_in_polygon(p, [[0.0, 0.0], [5.0, 0.0], [5.0, 8.0]])
    rows_in_rect(p, 0.0, 2.5, 0.0, 6.0)
