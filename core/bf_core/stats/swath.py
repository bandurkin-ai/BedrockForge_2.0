"""Swath plot by samples and the common accounting of a series (ТЗ 7.30, 7.35 Д-01; ADR 0019 §5) — the port
of BF `stats/swath.py` (`swath_means`, `inside`, `points_in_blocks`) and of the cuboid of BF `routers/swath.py`
onto the frame of three angles (`swath_frame`). The series of a block model is `swath_blocks` (13.11); it
ends with the same `series_result`.

    check_series(n)                              1 … MAX_SERIES (12) series, else `too_many_series` (Т7.30-1)
    cuboid(f, models=, samples=, limits=, width=, anchor=, block=) -> Cuboid
        the cuboid of the comparison and its swaths: around the extents of the model series (rows (min, max)
        along u, v, w; `swath_frame.bounds`), without a model — around the samples (`sample_bounds`); along u it
        is widened to whole swaths. `limits` — the cuboid given by hand ({"u": [lo, hi], "v": …, "w": …} or
        3×2): it is taken as it is, the swaths start at its low side (`box` may stick out by a part of the last
        swath). `width` None — whole blocks of side `block` or a round number (`swath_frame.auto_width`)
    sample_bounds(f, points, sel=None) -> 3×2    the extent of the middles of the samples taken
    sample_series(points, sel, f, cuboid, cells=None, tick=None) -> SeriesResult
        `points` — the middles of the intervals of every row of the table (n×3), `sel` — `select.Selection`
        (the weight, values < 0, the cuts); a sample belongs to the swath of its middle. In the cuboid: every
        sample inside `cuboid.limits`; `cells` — only those inside the cells of a model series (Т7.30-2)
    cells_of(grid, idx) -> Cells                 the occupied cells of a model series after its selection
    points_in_cells(points, cells) -> bool[n]    point → (i, j, k) → `idx` → a lookup among the occupied cells
    swath_means(t, values, weights, start, width, n) -> SeriesResult    the same accounting by coordinates along u
    series_result(weight, wsum, count, counts) -> SeriesResult          mean, total — for `swath_blocks` too
    deviation(mean, base) -> %                   (m − m₁) / m₁ · 100 from the first series; NaN where either is empty
    locate(points, f, cuboid, rows=None) -> int32[n]: the swath, OUTSIDE (−1), NO_POSITION (−2)
    inside(points, f, limits) -> bool[n]         width_note(cuboid) -> str | None        warm()

Numbers. The swath of a point: inside the cuboid (the tolerance of a side is 1e−9·max(1, |lo|, |hi|)),
k = ⌊(p·u − start) / width⌋ kept within 0 … n − 1; p·u — the rule of `swath_frame.project`, bit for bit. The
weight Σ w and Σ w·v of a swath are pairwise sums of its rows in the order of the rows — `weighted.pair_sums`
of the rows of the swath, the same bits however the rows are cut into blocks; the totals are exactly rounded
sums over the swaths (`math.fsum`). A swath without rows has no mean (NaN; None in the answer), never 0; a row
without a value, a weight or a position takes no part and is counted.

`counts` of a sample series: those of the selection (rows, missing, bad_weight, negative, zero_excluded,
bottom_capped, bottom_excluded, capped, top_excluded), selected — rows taken by it, no_position — of them
without coordinates, outside_box, outside_blocks, used — rows in the swaths.

The lookup of the cells is `swath_cells` (a binary search in `idx` or a bit map of the occupied cells). One
pass of numba kernels over blocks of 2**20 rows, `tick(fraction)` between the blocks (it may raise to cancel).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.jit import njit

from . import swath_frame as sf
from .select import Selection
from .swath_cells import BLOCK, FACE_TOL, NO_ROWS, Cells, as_points, cells_of, inside_cells, points_in_cells
from .swath_frame import Frame
from .weighted import StatsError, Tick

MAX_SERIES = 12  # ТЗ 7.30
OUTSIDE, NO_POSITION, OUTSIDE_BLOCKS = -1, -2, -3  # what `locate` writes instead of the number of a swath

__all__ = [
    "BLOCK", "FACE_TOL", "MAX_SERIES", "NO_POSITION", "OUTSIDE", "OUTSIDE_BLOCKS", "Cells", "Cuboid", "SeriesResult",
    "cells_of", "check_series", "cuboid", "deviation", "inside", "locate", "points_in_cells", "sample_bounds",
    "sample_series", "series_result", "swath_means", "warm", "width_note",
]  # fmt: skip


# --- kernels ----------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _index(t, start, width, n, tol, a, b, k, cnt):  # type: ignore[no-untyped-def]
    """Rows a … b − 1: the swath of a coordinate along u — the rule of `swath_frame.swath_index`."""
    end = start + n * width
    for r in range(a, b):
        x = t[r]
        j = -1
        if np.isfinite(x):
            q = np.floor((x - start) / width)
            if q >= 0.0 and q < n:
                j = int(q)
            elif q == n and x <= end + tol:
                j = n - 1
            elif q == -1.0 and x >= start - tol:
                j = 0
        if j < 0:
            cnt[1] += 1
        k[r] = j


@njit(cache=True, nogil=True)
def _locate(p, rows, has_rows, axes, lim, tol, start, width, n, a, b, k, cnt):  # type: ignore[no-untyped-def]
    """Rows a … b − 1 (of `rows`, or the points themselves): the swath of a point inside the cuboid, −1
    outside it, −2 without a position. cnt[0] — without a position, cnt[1] — outside."""
    last = float(n - 1)
    for r in range(a, b):
        i = rows[r] if has_rows else r
        x = p[i, 0]
        y = p[i, 1]
        z = p[i, 2]
        if not (np.isfinite(x) and np.isfinite(y) and np.isfinite(z)):
            k[r] = -2
            cnt[0] += 1
            continue
        tu = 0.0
        ok = True
        for j in range(3):
            t = 0.0
            e = axes[j, 0]
            if e != 0.0:
                t += x * e
            e = axes[j, 1]
            if e != 0.0:
                t += y * e
            e = axes[j, 2]
            if e != 0.0:
                t += z * e
            if not (t >= lim[j, 0] - tol[j] and t <= lim[j, 1] + tol[j]):
                ok = False
                break
            if j == 0:
                tu = t
        if not ok:
            k[r] = -1
            cnt[1] += 1
            continue
        q = np.floor((tu - start) / width)
        if q < 0.0:
            q = 0.0
        elif q > last:
            q = last
        k[r] = int(q)


@njit(cache=True, nogil=True)
def _extent(p, rows, has_rows, axes, out):  # type: ignore[no-untyped-def]
    """(min, max) of the projections of the points with a position onto the three axes; the number of them."""
    n = rows.shape[0] if has_rows else p.shape[0]
    m = 0
    for r in range(n):
        i = rows[r] if has_rows else r
        x = p[i, 0]
        y = p[i, 1]
        z = p[i, 2]
        if not (np.isfinite(x) and np.isfinite(y) and np.isfinite(z)):
            continue
        m += 1
        for j in range(3):
            t = 0.0
            e = axes[j, 0]
            if e != 0.0:
                t += x * e
            e = axes[j, 1]
            if e != 0.0:
                t += y * e
            e = axes[j, 2]
            if e != 0.0:
                t += z * e
            if m == 1 or t < out[j, 0]:
                out[j, 0] = t
            if m == 1 or t > out[j, 1]:
                out[j, 1] = t
    return m


@njit(cache=True, nogil=True)
def _sums(k, v, w, a, b, st, depth, s0, s1, c):  # type: ignore[no-untyped-def]
    """Rows a … b − 1 into the sums of their swaths: per swath the cascade of `weighted.pair_sums` — blocks of
    8 rows left to right, the blocks combined as a binary counter. The state lives between the calls."""
    for r in range(a, b):
        j = k[r]
        if j < 0:
            continue
        x = w[r]
        y = v[r]
        if not (np.isfinite(y) and np.isfinite(x) and x > 0.0):
            continue
        s0[j] += x
        s1[j] += x * y
        m = c[j] + 1
        c[j] = m
        if (m & 7) == 0:
            m >>= 3
            d = depth[j]
            a0 = s0[j]
            a1 = s1[j]
            while (m & 1) == 0:
                d -= 1
                a0 = st[j, d, 0] + a0
                a1 = st[j, d, 1] + a1
                m >>= 1
            st[j, d, 0] = a0
            st[j, d, 1] = a1
            depth[j] = d + 1
            s0[j] = 0.0
            s1[j] = 0.0


@njit(cache=True, nogil=True)
def _close(st, depth, s0, s1, weight, wsum):  # type: ignore[no-untyped-def]
    for j in range(weight.shape[0]):
        a0 = s0[j]
        a1 = s1[j]
        d = depth[j]
        while d > 0:
            d -= 1
            a0 = st[j, d, 0] + a0
            a1 = st[j, d, 1] + a1
        weight[j] = a0
        wsum[j] = a1


# --- the series -------------------------------------------------------------------------------------------------


def check_series(n: int) -> None:
    """A swath has 1 … 12 series; the 13th is refused (Т7.30-1)."""
    if n > MAX_SERIES:
        raise StatsError(
            "too_many_series",
            f"В swath не больше {MAX_SERIES} рядов: рядов {n}.",
            "Удалите ненужный ряд или постройте ещё один swath.",
            param="series",
        )
    if n < 1:
        raise StatsError("bad_param", "В swath нет ни одного ряда.", "Добавьте ряд модели или проб.", param="series")


@dataclass(frozen=True, eq=False)
class SeriesResult:
    """One series by swath. `mean` is NaN where a swath has no rows (None in `as_dict`), never 0."""

    mean: np.ndarray  # float64[n]
    count: np.ndarray  # int64[n]: rows in the swath
    weight: np.ndarray  # float64[n]: Σ w of the swath (the volume or tonnage of blocks, the length of samples)
    total: dict[str, Any]  # over all the swaths: {"mean": float | None, "count": int, "weight": float}
    counts: dict[str, int]  # what was taken and what was left out

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready: an empty swath has `mean` None."""
        return {
            "mean": [float(x) if math.isfinite(x) else None for x in self.mean],
            "count": [int(x) for x in self.count],
            "weight": [float(x) for x in self.weight],
            "total": dict(self.total),
            "counts": dict(self.counts),
        }


def series_result(weight: Any, wsum: Any, count: Any, counts: Mapping[str, int]) -> SeriesResult:
    """The series of the sums of its swaths: Σ w, Σ w·v and the number of rows. `used` of `counts` is set to
    the rows in the swaths."""
    w = np.asarray(weight, dtype=np.float64)
    s = np.asarray(wsum, dtype=np.float64)
    c = np.asarray(count, dtype=np.int64)
    mean = np.full(w.shape, np.nan)
    np.divide(s, w, out=mean, where=(c > 0) & (w > 0.0))
    total_w, used = math.fsum(w.tolist()), int(c.sum())
    total = {"mean": math.fsum(s.tolist()) / total_w if total_w > 0.0 else None, "count": used, "weight": total_w}
    return SeriesResult(mean, c, w, total, {**counts, "used": used})


def deviation(mean: Any, base: Any) -> np.ndarray:
    """The deviation from the first series, %: (m − m₁) / m₁ · 100; NaN where either is empty or m₁ is 0."""
    m = np.asarray(np.nan if mean is None else mean, dtype=np.float64)
    b = np.asarray(np.nan if base is None else base, dtype=np.float64)
    out = np.full(np.broadcast(m, b).shape, np.nan)
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        np.divide((m - b) * 100.0, b, out=out, where=np.isfinite(m) & np.isfinite(b) & (b != 0.0))
    return out


def _accumulate(k: np.ndarray, v: np.ndarray, w: np.ndarray, n: int, tick: Tick | None, lo: float = 0.0) -> Any:
    """Σ w, Σ w·v and the number of rows of every swath for the rows with k ≥ 0."""
    rows = int(k.shape[0])
    depth_max = max(1, rows.bit_length())
    st = np.zeros((n, depth_max, 2))
    depth, c = np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.int64)
    s0, s1 = np.zeros(n), np.zeros(n)
    for a in range(0, rows, BLOCK):
        b = min(a + BLOCK, rows)
        _sums(k, v, w, a, b, st, depth, s0, s1, c)
        if tick is not None:
            tick(lo + (1.0 - lo) * b / rows)
    if rows == 0 and tick is not None:
        tick(1.0)
    weight, wsum = np.empty(n), np.empty(n)
    _close(st, depth, s0, s1, weight, wsum)
    return weight, wsum, c


def swath_means(
    t: Any, values: Any, weights: Any, start: float, width: float, n: int, *, tick: Tick | None = None
) -> SeriesResult:
    """The accounting of a series by the coordinates of its rows along u (BF `swath_means`): weighted mean,
    count and weight by swath, the totals; `counts`: rows, outside_box — rows outside the swaths, used."""
    x = np.ascontiguousarray(t, dtype=np.float64)
    v = np.ascontiguousarray(values, dtype=np.float64)
    w = np.ascontiguousarray(weights, dtype=np.float64)
    if x.ndim != 1 or v.shape != x.shape or w.shape != x.shape:
        raise ValueError("t, values and weights must be 1D of one length")
    rows = int(x.shape[0])
    k = np.empty(rows, dtype=np.int32)
    cnt = np.zeros(2, dtype=np.int64)
    tol = sf.TOL * max(1.0, abs(start), width)
    for a in range(0, rows, BLOCK):
        b = min(a + BLOCK, rows)
        _index(x, float(start), float(width), int(n), tol, a, b, k, cnt)
        if tick is not None:
            tick(0.5 * b / rows)
    weight, wsum, c = _accumulate(k, v, w, int(n), tick, 0.5)
    return series_result(weight, wsum, c, {"rows": rows, "outside_box": int(cnt[1])})


# --- the cuboid -------------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Cuboid:
    """The cuboid of the comparison in the frame and its swaths."""

    limits: np.ndarray  # 3×2: (min, max) along u, v, w — what is outside takes no part
    box: np.ndarray  # 3×2: the same with u on whole swaths (what the 3D window draws)
    start: float
    width: float
    count: int
    custom: bool  # the limits are given by hand
    block: float | None  # the side of the parent block of the first model along the swaths

    @property
    def tol(self) -> np.ndarray:
        """The tolerance of every side of `limits`."""
        return sf.TOL * np.maximum(1.0, np.abs(self.limits).max(axis=1))

    @property
    def centres(self) -> np.ndarray:
        return sf.centres(self.start, self.width, self.count)

    def as_dict(self) -> dict[str, Any]:
        def rows(a: np.ndarray) -> dict[str, list[float]]:
            return {name: [float(a[i, 0]), float(a[i, 1])] for i, name in enumerate("uvw")}

        return {
            "box": rows(self.box),
            "limits": rows(self.limits),
            "custom_limits": self.custom,
            "start": self.start,
            "width": self.width,
            "count": self.count,
            "centres": [float(x) for x in self.centres],
            "block": self.block,
        }


def _limits(raw: Any) -> np.ndarray:
    """The cuboid given by hand as 3×2, checked."""
    bad = StatsError(
        "bad_param",
        "Границы кубоида: по каждой оси (u, v, w) нужны два числа, минимум меньше максимума.",
        "Исправьте границы или уберите их — кубоид построится по данным.",
        param="limits",
    )
    try:
        rows = [raw[name] for name in "uvw"] if isinstance(raw, Mapping) else raw
        lim = np.array(rows, dtype=np.float64)
    except (KeyError, TypeError, ValueError):
        raise bad from None
    if lim.shape != (3, 2) or not np.isfinite(lim).all() or not (lim[:, 1] > lim[:, 0]).all():
        raise bad
    return lim


def cuboid(
    f: Frame,
    *,
    models: Sequence[Any] = (),
    samples: Sequence[Any] = (),
    limits: Any = None,
    width: float | None = None,
    anchor: float | None = None,
    block: float | None = None,
) -> Cuboid:
    """The cuboid and the swaths (see the module docstring). `models`, `samples` — the extents of the series
    in the frame `f` (3×2; NaN — a series without data); `block` — the side of the parent block of the first
    model (`swath_frame.block_side`). StatsError: `no_data`, `bad_param`, `too_many_swaths`."""
    custom = limits is not None
    if custom:
        lim = _limits(limits)
    else:
        for group in (models, samples):
            ext = [e for e in (np.asarray(x, dtype=np.float64).reshape(3, 2) for x in group) if np.isfinite(e).all()]
            if ext:
                break
        else:
            raise StatsError(
                "no_data",
                "Нет данных с координатами: кубоид полос не построить.",
                "Проверьте ряды: у модели должны быть ячейки, у проб — значения и положение на скважине.",
            )
        every = np.array(ext)
        lim = np.column_stack([every[:, :, 0].min(axis=0), every[:, :, 1].max(axis=0)])
    lo, hi = float(lim[0, 0]), float(lim[0, 1])
    side = float(block) if block is not None and math.isfinite(block) and block > 0.0 else None
    w = sf.auto_width(lo, hi, side, anchor) if width is None else width
    start, n = sf.layout(lo, hi, w, anchor)
    box = lim.copy()
    box[0] = start, start + n * float(w)
    return Cuboid(lim if custom else box.copy(), box, start, float(w), n, custom, side)


def width_note(c: Cuboid) -> str | None:
    """The note of the answer when a swath is narrower than a block (BF)."""
    if c.block is None or c.width >= c.block * (1.0 - sf.TOL):
        return None
    return (
        f"Полоса ({c.width:g} м) уже блока ({c.block:g} м): блок целиком идёт в полосу своего центра, "
        "часть полос пуста."
    )


def inside(points: Any, f: Frame, limits: Any) -> np.ndarray:
    """True for every point inside the cuboid `limits` (rows (min, max) along u, v, w), its sides included with
    the tolerance 1e−9·max(1, |lo|, |hi|); a point without a coordinate is outside."""
    lim = np.asarray(limits, dtype=np.float64).reshape(3, 2)
    tol = sf.TOL * np.maximum(1.0, np.abs(lim).max(axis=1))
    p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    local = sf.to_frame(f, p)
    with np.errstate(invalid="ignore"):
        ok = ((local >= lim[:, 0] - tol) & (local <= lim[:, 1] + tol)).all(axis=1)
    return np.asarray(ok & np.isfinite(p).all(axis=1))


# --- samples ----------------------------------------------------------------------------------------------------


def _rows(rows: Any) -> tuple[np.ndarray, bool]:
    if rows is None:
        return NO_ROWS, False
    return np.ascontiguousarray(rows, dtype=np.int64), True


def sample_bounds(f: Frame, points: Any, sel: Selection | None = None) -> np.ndarray:
    """The extent along u, v, w of the middles of the samples taken by `sel` (None — of every point); NaN when
    none of them has a position."""
    p = as_points(points)
    rows, has_rows = _rows(None if sel is None else sel.rows)
    out = np.full((3, 2), np.nan)
    _extent(p, rows, has_rows, np.ascontiguousarray(f.axes), out)
    return out


def locate(
    points: Any, f: Frame, c: Cuboid, rows: Any = None, *, tick: Tick | None = None, counts: Any = None
) -> np.ndarray:
    """The swath of every point (of the rows `rows` of `points`, when given): 0 … count − 1 inside the cuboid,
    OUTSIDE, NO_POSITION. `counts` — int64[2] that receives the numbers of the two."""
    p = as_points(points)
    r, has_rows = _rows(rows)
    n = int(r.shape[0]) if has_rows else int(p.shape[0])
    k = np.empty(n, dtype=np.int32)
    cnt = np.zeros(2, dtype=np.int64) if counts is None else counts
    axes, lim, tol = np.ascontiguousarray(f.axes), np.ascontiguousarray(c.limits), np.ascontiguousarray(c.tol)
    for a in range(0, n, BLOCK):
        b = min(a + BLOCK, n)
        _locate(p, r, has_rows, axes, lim, tol, c.start, c.width, c.count, a, b, k, cnt)
        if tick is not None:
            tick(b / n)
    return k


def sample_series(
    points: Any, sel: Selection, f: Frame, c: Cuboid, *, cells: Cells | None = None, tick: Tick | None = None
) -> SeriesResult:
    """A sample series by swath (see the module docstring): `points` — the middles of the intervals of every
    row the selection was made of; `cells` — «inside the blocks» of that model series."""
    p = as_points(points)
    if int(p.shape[0]) != int(sel.counts["rows"]):
        raise ValueError("points must hold a row for every row of the selection")
    step = (lambda _: None) if tick is None else tick
    cnt = np.zeros(2, dtype=np.int64)
    k = locate(p, f, c, sel.rows, tick=lambda x: step(0.4 * x), counts=cnt)
    outside_blocks = 0
    if cells is not None:
        rows, _ = _rows(sel.rows)
        hit = inside_cells(p, rows, True, k, cells, None, step, 0.4, 0.7)
        miss = (k >= 0) & ~hit
        outside_blocks = int(miss.sum())
        k[miss] = OUTSIDE_BLOCKS
    weight, wsum, n = _accumulate(k, sel.values, sel.weights, c.count, step, 0.7)
    counts = {key: value for key, value in sel.counts.items() if key != "used"}
    counts.update(selected=sel.n, no_position=int(cnt[0]), outside_box=int(cnt[1]), outside_blocks=outside_blocks)
    return series_result(weight, wsum, n, counts)


def warm() -> None:
    """Compile the kernels on a few rows (the `stats` step of the warm-up)."""
    from .select import select_values

    f = sf.frame(30.0, -10.0, 5.0)
    pts = np.array([[0.0, 0.0, 0.0], [3.0, 4.0, 1.0], [np.nan, 0.0, 0.0], [9.0, 9.0, 2.0]])
    sel = select_values(np.array([1.0, 2.0, 3.0, np.nan]))
    c = cuboid(f, samples=[sample_bounds(f, pts, sel)], width=2.0)
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [2, 2, 1], "size": [5.0, 5.0, 2.0], "order": "F"}
    for dtype in (np.uint64, np.uint32):
        cells = cells_of(grid, np.array([0, 3], dtype=dtype))
        sample_series(pts, sel, f, c, cells=cells)
        points_in_cells(pts, cells, method="search")
    swath_means(np.array([0.5, 1.5]), np.ones(2), np.ones(2), 0.0, 1.0, 2)
