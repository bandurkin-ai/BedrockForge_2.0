"""The local uncertainty of the tab «Статистика» (`local`; ТЗ 7.29; ADR 0022 §11): how much an inverse distance
estimate in plan depends on which holes were drilled — the port of BF `api/app/stats/estimation.py` (`idw`),
`uncertainty.py` (`resample_groups`, `local_*`) and the route `local-uncertainty` of `routers/stats.py`.

    tab_local(points, groups, *, grid=None, k=8, power=2, radius=None, realizations=50, seed=None,
              threshold=None, threads=1, tick=None) -> dict
        a realization takes the holes with replacement (whole holes, as many as there are; the samples of a hole
        are not independent) and estimates the nodes of a grid in plan by IDW; over the realizations a node has
        the mean, σ (ddof 0), P10, P90 and, with `threshold`, the share of the realizations ≥ it. → {count,
        holes, realizations, k, power, radius, seed, threshold, grid {nx, ny, x, y}, mean, sd, p10, p90, prob
        (None without a threshold), empty, counts}; the maps are [row j by y][column i by x], a node no
        realization reached (no datum within the radius) — None, never 0; `empty` — how many such nodes
    groups — the hole of every row given to `tab.tab_points` (or of every point): `Columns.hole_of()`, names
    idw(coords, values, targets, k=8, power=2, radius=None, multiplicity=None) -> float64[m]   (BF `idw`)
    resample_groups(groups, n_boot, seed=None) -> [int64 index arrays]                     (BF, the same draws)
    group_counts(groups, n_boot, seed=None) -> (inverse int64[n], counts int64[n_boot, holes])
    local_expectation(r), local_std(r), local_percentile(r, p), local_prob_exceed(r, t)    (BF, axis 0)
    warm() -> seconds      the kernel compiled into NUMBA_CACHE_DIR (for `stats/warm.py`)

The grid (BF): nx × ny nodes (2–100 each, 30 by default) — the centres of the cells x0 + (i + ½)·w / nx, w =
max(x1 − x0, span / nx), the same along y, span — the larger extent of the points; the radius by default 25 % of
it; k 1–64 (8), power (0; 10] (2), 5–300 realizations (50). The seed: `numpy.random.default_rng(seed)` draws
`integers(0, H, H)` holes a realization, as BF — one seed, one answer bit for bit (with any number of threads);
without a seed one is drawn and returned.

IDW of a node (BF `idw`): the k data nearest in plan with d < radius (fewer if there are fewer), weights
1 / max(d, 1e−9)^power; a datum closer than 1e−9 m makes the node the mean of such data; none — NaN. A
realization is the points with the multiplicity of their hole (a hole drawn twice is its data twice, as BF
concatenates them). Ties: the data at the k-th distance (equal within 1e−9 × max(1, d)) share what is left of k —
each counts (k − m) / M of itself, m the copies nearer, M those at that distance. BF takes whichever of them the
tree gives; a vertical hole (one place in plan for all its samples) would then stand for k samples chosen by the
order of the rows, here it stands for their mean. Without ties (and with the copies of one drawn hole, which are
equal) it is BF within 1e−12. The kernel: the points by cells of a grid in plan (about k a cell); the candidates
of a node — the rings of cells around it, sorted by distance once — serve all the realizations (only the
multiplicities differ), a ring is added while one of them has its k-th distance or its ties beyond the rings (or
until the radius or the grid is passed); numba, `nogil`, strips of `STRIP` nodes on the threads of
`composites.strips` (`tick` after every strip; the answer does not depend on the threads).

Time (16.10; Linux, 2 threads, 20 000 composites of 1000 inclined holes in 2 × 2 km): 30 × 30 × 50 — 0,02 s;
100 × 100 × 300 — 0,5 s (k = 8), 1,0 s (k = 64), of it P10 and P90 ≈ 0,1 s; the process 0,23 GB.

Errors — `StatsError {code, message, hint}` with `param`: `bad_param` (grid.nx, grid.ny, k, power, radius,
realizations, seed, threshold), `too_many_samples`, `too_few_values` (fewer than 3 points or 3 holes),
`no_spread` (all points in one place in plan).
"""

from __future__ import annotations

import math
import time
import warnings
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from bf_core.jit import njit

from .tab import TabPoints
from .tab_vario import bad, check_limit, number, to_float, whole
from .weighted import StatsError, Tick

NODES = (2, 100)  # nodes of the grid along a side (BF `LocalRequest.nx`, `ny`)
K = (1, 64)
POWER_MAX = 10.0
REALIZATIONS = (5, 300)
RADIUS_SHARE = 0.25  # of the larger extent in plan (BF)
ZERO = 1e-9  # m: a datum this close to a node is on it (BF)
TIE = 1e-9  # distances equal within TIE × max(1, d) are one distance
GRID_SIDE = 1024  # cells of the search grid along a side, at most
STRIP = 128  # nodes of a strip
_INT = np.int64


# --- BF: resampling and the statistics of the realizations ------------------------------------------------------


def _inverse(groups: Any) -> np.ndarray:
    g = np.asarray(groups)
    if g.ndim != 1 or g.size == 0:
        raise ValueError("groups must be a non-empty 1D array")
    return np.unique(g, return_inverse=True)[1].astype(_INT)


def _draws(holes: int, n_boot: int, seed: Any) -> np.ndarray:
    """int64[n_boot, holes]: the holes of every realization — BF `rng.integers(0, H, H)` in turn."""
    if n_boot < 1:
        raise ValueError("n_boot must be >= 1")
    rng = np.random.default_rng(seed)
    return np.stack([rng.integers(0, holes, holes) for _ in range(int(n_boot))]).astype(_INT)


def resample_groups(groups: Any, n_boot: int, seed: Any = None) -> list[np.ndarray]:
    """BF: `n_boot` index arrays, each of whole groups drawn with replacement (as many as there are)."""
    inverse = _inverse(groups)
    members = [np.flatnonzero(inverse == g) for g in range(int(inverse.max()) + 1)]
    return [np.concatenate([members[g] for g in row]) for row in _draws(len(members), n_boot, seed)]


def group_counts(groups: Any, n_boot: int, seed: Any = None) -> tuple[np.ndarray, np.ndarray]:
    """The draws of `resample_groups` as counts: (the dense group of every row, how many times every group is
    drawn in every realization)."""
    inverse = _inverse(groups)
    holes = int(inverse.max()) + 1
    draws = _draws(holes, n_boot, seed)
    counts = np.zeros((draws.shape[0], holes), dtype=_INT)
    for r, row in enumerate(draws):
        counts[r] = np.bincount(row, minlength=holes)
    return inverse, counts


def _stack(realizations: Any) -> np.ndarray:
    r = np.asarray(realizations, dtype=np.float64)
    if r.ndim < 1 or r.shape[0] == 0:
        raise ValueError("realizations must have a leading axis of at least 1 realization")
    return r


def _quiet(fn: Any, *args: Any, **kwargs: Any) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # «all-NaN slice» is a legal, NaN result here
        return np.asarray(fn(*args, **kwargs), dtype=np.float64)


def local_expectation(realizations: Any) -> np.ndarray:
    """The mean over the realizations (axis 0), NaN skipped."""
    return _quiet(np.nanmean, _stack(realizations), axis=0)


def local_std(realizations: Any) -> np.ndarray:
    """σ over the realizations (axis 0, ddof 0), NaN skipped."""
    return _quiet(np.nanstd, _stack(realizations), axis=0)


def local_percentile(realizations: Any, p: float) -> np.ndarray:
    """The p-th percentile (0–100, linear) over the realizations (axis 0), NaN skipped."""
    if not 0 <= p <= 100:
        raise ValueError("p must be between 0 and 100")
    return _quiet(np.nanpercentile, _stack(realizations), p, axis=0)


def local_prob_exceed(realizations: Any, threshold: float) -> np.ndarray:
    """The share of the realizations ≥ threshold (axis 0) among those with a value; none — NaN."""
    r = _stack(realizations)
    valid = np.isfinite(r).sum(axis=0)
    hit = (np.where(np.isfinite(r), r, -np.inf) >= threshold).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.asarray(np.where(valid > 0, hit / valid, np.nan), dtype=np.float64)


# --- the kernel ---------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _scan(px, py, cstart, cell, tx, ty, radius, cd, cp, nc):  # type: ignore[no-untyped-def]
    """The points of a cell with d < radius → the candidates cd (distance), cp (point) from `nc`."""
    for p in range(cstart[cell], cstart[cell + 1]):
        dx = px[p] - tx
        dy = py[p] - ty
        d = math.sqrt(dx * dx + dy * dy)
        if d < radius:
            cd[nc] = d
            cp[nc] = p
            nc += 1
    return nc


@njit(cache=True, nogil=True)
def _ring(px, py, cstart, gx, gy, ix, iy, ring, tx, ty, radius, cd, cp, nc):  # type: ignore[no-untyped-def]
    """The cells at the Chebyshev distance `ring` from the cell (ix, iy) of the node, those of the grid."""
    ylo, yhi, xlo, xhi = iy - ring, iy + ring, ix - ring, ix + ring
    for cy in range(max(ylo, 0), min(yhi, gy - 1) + 1):
        if cy == ylo or cy == yhi:
            for cx in range(max(xlo, 0), min(xhi, gx - 1) + 1):
                nc = _scan(px, py, cstart, cy * gx + cx, tx, ty, radius, cd, cp, nc)
        else:
            if 0 <= xlo < gx:
                nc = _scan(px, py, cstart, cy * gx + xlo, tx, ty, radius, cd, cp, nc)
            if xhi != xlo and 0 <= xhi < gx:
                nc = _scan(px, py, cstart, cy * gx + xhi, tx, ty, radius, cd, cp, nc)
    return nc


@njit(cache=True, nogil=True)
def _estimate(sd, sw, sv, sg, ns, counts, r, k, safe):  # type: ignore[no-untyped-def]
    """IDW of the realization r over the candidates sorted by distance [0, ns): (done, value). Not done — the
    k-th distance or its ties may lie beyond `safe` (the candidates are complete below it; ∞ — all of them)."""
    cum = 0
    j = 0
    while j < ns and cum < k:
        cum += counts[r, sg[j]]
        j += 1
    lo = j  # [0, lo) count whole; [lo, hi) — the ties at the k-th distance share what is left of k
    hi = j
    rest = 0.0
    tied = 0.0
    if cum < k:
        if safe < math.inf:
            return False, math.nan
    else:
        dk = sd[j - 1]
        tol = TIE * max(1.0, dk)
        if dk + tol >= safe:
            return False, math.nan
        lo = j - 1
        while lo > 0 and sd[lo - 1] >= dk - tol:
            lo -= 1
        while hi < ns and sd[hi] <= dk + tol:
            hi += 1
        full = 0
        for q in range(lo):
            full += counts[r, sg[q]]
        for q in range(lo, hi):
            tied += counts[r, sg[q]]
        rest = k - full
    zs = 0.0
    zv = 0.0
    ws = 0.0
    wv = 0.0
    for q in range(hi):
        m = counts[r, sg[q]]
        if m == 0:
            continue
        share = m if q < lo else m * rest / tied
        if sd[q] < ZERO:
            zs += share
            zv += share * sv[q]
        w = share * sw[q]
        ws += w
        wv += w * sv[q]
    if zs > 0.0:
        return True, zv / zs
    if ws > 0.0:
        return True, wv / ws
    return True, math.nan


@njit(cache=True, nogil=True)
def _nodes(px, py, pv, pg, cstart, gx, gy, x0, y0, c, tx, ty, counts, k, power, radius, t0, t1, out):  # type: ignore[no-untyped-def]
    """The nodes t0 ≤ t < t1 in every realization → out[r, t]. The candidates of a node — the points of the rings
    of cells around it, sorted by distance — serve all the realizations; a ring is added when one needs it."""
    n = px.size
    cd = np.empty(n, dtype=np.float64)
    cp = np.empty(n, dtype=np.int64)
    sd = np.empty(n, dtype=np.float64)
    sw = np.empty(n, dtype=np.float64)
    sv = np.empty(n, dtype=np.float64)
    sg = np.empty(n, dtype=np.int64)
    for t in range(t0, t1):
        x, y = tx[t], ty[t]
        ix = int(math.floor((x - x0) / c))
        iy = int(math.floor((y - y0) / c))
        rmax = max(max(ix, gx - 1 - ix), max(iy, gy - 1 - iy))  # this ring covers the grid
        nc = 0
        ns = 0
        ring = -1
        safe = -math.inf
        for r in range(counts.shape[0]):
            while True:
                done, value = _estimate(sd, sw, sv, sg, ns, counts, r, k, safe)
                if done:
                    out[r, t] = value
                    break
                ring += 1
                nc = _ring(px, py, cstart, gx, gy, ix, iy, ring, x, y, radius, cd, cp, nc)
                bound = ring * c  # a point outside the rings is farther than this
                safe = math.inf if (ring >= rmax or bound >= radius) else bound - TIE * max(1.0, bound)
                order = np.argsort(cd[:nc], kind="mergesort")
                for q in range(nc):
                    o = order[q]
                    sd[q] = cd[o]
                    sv[q] = pv[cp[o]]
                    sg[q] = pg[cp[o]]
                    sw[q] = 1.0 / max(cd[o], ZERO) ** power
                ns = nc


class _Grid:
    """The points by the cells of the search grid: sorted by (cell, row), CSR offsets of the cells."""

    def __init__(self, x: np.ndarray, y: np.ndarray, v: np.ndarray, g: np.ndarray, k: int) -> None:
        n = x.size
        self.x0, self.y0 = float(x.min()), float(y.min())
        sx, sy = float(x.max()) - self.x0, float(y.max()) - self.y0
        span = max(sx, sy)
        area = max(sx * sy, span * span / n)  # a line of points: about k a cell along it
        c = math.sqrt(area * max(k, 1) / n) if area > 0.0 else 1.0
        self.c = max(c, span / GRID_SIDE) if span > 0.0 else 1.0
        self.gx, self.gy = int(sx // self.c) + 1, int(sy // self.c) + 1
        ix = np.minimum(np.floor((x - self.x0) / self.c).astype(_INT), self.gx - 1)
        iy = np.minimum(np.floor((y - self.y0) / self.c).astype(_INT), self.gy - 1)
        cell = iy * self.gx + ix
        order = np.argsort(cell, kind="stable")
        self.px, self.py = np.ascontiguousarray(x[order]), np.ascontiguousarray(y[order])
        self.pv, self.pg = np.ascontiguousarray(v[order]), np.ascontiguousarray(g[order], dtype=_INT)
        start = np.zeros(self.gx * self.gy + 1, dtype=_INT)
        np.cumsum(np.bincount(cell, minlength=self.gx * self.gy), out=start[1:])
        self.cstart = start

    def run(self, tx: np.ndarray, ty: np.ndarray, counts: np.ndarray, k: int, power: float, radius: float,
            threads: int = 1, tick: Tick | None = None) -> np.ndarray:  # fmt: skip
        """float64[realizations, nodes]: IDW of every node in every realization (`counts` [r, group]); the nodes
        in strips of `STRIP` on the threads, `tick` after every strip."""
        from bf_core.composites import strips

        out = np.full((counts.shape[0], tx.size), np.nan)
        tx, ty = np.ascontiguousarray(tx, dtype=np.float64), np.ascontiguousarray(ty, dtype=np.float64)
        counts = np.ascontiguousarray(counts, dtype=_INT)

        def part(t0: int, t1: int) -> None:
            _nodes(self.px, self.py, self.pv, self.pg, self.cstart, self.gx, self.gy, self.x0, self.y0, self.c,
                   tx, ty, counts, int(k), float(power), float(radius), t0, t1, out)  # fmt: skip

        strips.run(part, tx.size, threads=max(1, int(threads)), tick=tick, size=STRIP)
        return out


def idw(
    coords: Any, values: Any, targets: Any, k: int = 8, power: float = 2.0, radius: float | None = None,
    multiplicity: Any = None,
) -> np.ndarray:  # fmt: skip
    """BF `idw`: the estimate at `targets` (m, 2) by the k nearest data within `radius` (None — any distance),
    NaN where none; `multiplicity` — how many times every datum counts (a resampled hole; 0 — absent)."""
    xy = np.asarray(coords, dtype=np.float64)
    v = np.asarray(values, dtype=np.float64)
    t = np.asarray(targets, dtype=np.float64)
    if xy.ndim != 2 or xy.shape[1] != 2 or t.ndim != 2 or t.shape[1] != 2:
        raise ValueError("coords and targets must have shape (n, 2)")
    if xy.shape[0] != v.shape[0] or xy.shape[0] == 0:
        raise ValueError("coords and values must be non-empty and of equal length")
    if k < 1 or power <= 0 or (radius is not None and radius <= 0):
        raise ValueError("require k >= 1, power > 0, radius > 0")
    n = xy.shape[0]
    mult = np.ones(n, dtype=_INT) if multiplicity is None else np.asarray(multiplicity, dtype=_INT)
    if mult.shape != (n,) or (mult < 0).any():
        raise ValueError("multiplicity: one count >= 0 per datum")
    grid = _Grid(xy[:, 0], xy[:, 1], v, np.arange(n, dtype=_INT), int(k))
    return grid.run(t[:, 0], t[:, 1], mult[None, :], int(k), float(power), math.inf if radius is None else radius)[0]


# --- the tab ------------------------------------------------------------------------------------------------------


def _groups(points: TabPoints, groups: Any) -> np.ndarray:
    """The hole of every point: `groups` of the rows given to `tab_points` (indexed by `points.rows`) or of the
    points themselves."""
    g = np.asarray(groups)
    if g.ndim == 1 and g.shape[0] == points.counts.get("rows", -1):
        return np.asarray(g[points.rows])
    if g.ndim != 1 or g.shape[0] != points.n:
        raise ValueError("groups: the hole of every row of the table or of every point")
    return g


def _grid_of(raw: Any) -> tuple[int, int]:
    if raw is None:
        return 30, 30
    if not isinstance(raw, Mapping):
        raise bad("grid", "Сетка карты задана неверно.", "Ожидается объект с полями nx и ny.")
    lo, hi = NODES
    return tuple(  # type: ignore[return-value]
        whole(raw.get(a), f"grid.{a}", f"Узлов сетки по {a[1].upper()}", 30, lo, hi) for a in ("nx", "ny")
    )


def _seed(raw: Any) -> int:
    """The seed given (a whole number 0 … 2⁶³ − 1) or a new one."""
    if raw is None:
        return int(np.random.SeedSequence().generate_state(1, np.uint64)[0] >> np.uint64(1))
    if isinstance(raw, bool) or not isinstance(raw, int | np.integer) or not 0 <= raw < 2**63:
        raise bad("seed", "Зерно генератора — целое число не меньше нуля.",
                  "Задайте, например, 1, или оставьте пустым.")  # fmt: skip
    return int(raw)


def _threshold(raw: Any) -> float | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    x = to_float(raw)
    if not math.isfinite(x):
        raise bad("threshold", "Порог: нужно число.", "Введите значение компонента, например 0,5, или оставьте пустым.")
    return x + 0.0


def _centres(lo: float, hi: float, span: float, count: int) -> np.ndarray:
    """BF: the centres of `count` cells from `lo` over max(hi − lo, span / count)."""
    return np.asarray(lo + (np.arange(count) + 0.5) * max(hi - lo, span / count) / count, dtype=np.float64)


def _rows(a: np.ndarray) -> list[list[Any]]:
    return [[None if not math.isfinite(x) else float(x) for x in row] for row in a.tolist()]


def percentiles(realizations: np.ndarray, qs: Sequence[float]) -> np.ndarray:
    """`local_percentile` of every q at once → [len(qs), …]: the same numbers, faster — the nodes every
    realization reached by `np.percentile` (what `nanpercentile` does without NaN, ×6 slower), the others by
    `nanpercentile`, both q in one sort."""
    r = _stack(realizations)
    flat = r.reshape(r.shape[0], -1)
    out = np.full((len(qs), flat.shape[1]), np.nan)
    whole_ = np.isfinite(flat).all(axis=0)
    if whole_.any():
        out[:, whole_] = np.percentile(flat[:, whole_], list(qs), axis=0)
    if not whole_.all():
        out[:, ~whole_] = _quiet(np.nanpercentile, flat[:, ~whole_], list(qs), axis=0)
    return out.reshape((len(qs), *r.shape[1:]))


def tab_local(
    points: TabPoints,
    groups: Sequence[Any] | np.ndarray,
    *,
    grid: Any = None,
    k: Any = None,
    power: Any = None,
    radius: Any = None,
    realizations: Any = None,
    seed: Any = None,
    threshold: Any = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> dict[str, Any]:
    """The local uncertainty of the tab (see the module): BF `local-uncertainty` on the kernel of the module."""
    check_limit(points)
    g = _groups(points, groups)
    nx, ny = _grid_of(grid)
    kk = whole(k, "k", "Число соседей", 8, *K)
    pw = number(power, "power", "Степень обратного расстояния", 2.0, hi=POWER_MAX, unit="")
    nreal = whole(realizations, "realizations", "Число реализаций", 50, *REALIZATIONS)
    used = _seed(seed)
    level = _threshold(threshold)
    if points.n < 3:
        raise StatsError(
            "too_few_values",
            f"Недостаточно данных для карты неопределённости: интервалов — {points.n}, нужно не меньше 3.",
            "Проверьте компонент и траектории скважин.",
        )
    inverse = _inverse(g)
    holes = int(inverse.max()) + 1
    if holes < 3:
        raise StatsError(
            "too_few_values",
            f"Нужно не менее 3 скважин с данными, есть {holes}.",
            "Карта неопределённости выбирает скважины с возвратом: при 1–2 скважинах разброса нет.",
        )
    x0, x1, y0, y1 = float(points.x.min()), float(points.x.max()), float(points.y.min()), float(points.y.max())
    span = max(x1 - x0, y1 - y0)
    if not span > 0.0:
        raise StatsError("no_spread", "Все интервалы в одной точке плана: карта неопределённости невозможна.",
                         "Нужны пробы скважин, разнесённых в плане.")  # fmt: skip
    rad = number(radius, "radius", "Радиус поиска", RADIUS_SHARE * span)
    cx, cy = _centres(x0, x1, span, nx), _centres(y0, y1, span, ny)
    _, counts = group_counts(g, nreal, used)
    search = _Grid(points.x, points.y, points.v, inverse, kk)
    stack = search.run(np.tile(cx, ny), np.repeat(cy, nx), counts, kk, pw, rad, threads, tick).reshape(nreal, ny, nx)
    mean = local_expectation(stack)
    p10, p90 = percentiles(stack, (10.0, 90.0))
    return {
        "count": points.n,
        "holes": holes,
        "realizations": nreal,
        "k": kk,
        "power": pw,
        "radius": rad,
        "seed": used,
        "threshold": level,
        "grid": {"nx": nx, "ny": ny, "x": cx.tolist(), "y": cy.tolist()},
        "mean": _rows(mean),
        "sd": _rows(local_std(stack)),
        "p10": _rows(p10),
        "p90": _rows(p90),
        "prob": None if level is None else _rows(local_prob_exceed(stack, level)),
        "empty": int(np.isnan(mean).sum()),
        "counts": dict(points.counts),
    }


def warm() -> float:
    """The kernel once on a few points (as `tab_local` and `idw` call it) → seconds."""
    t0 = time.perf_counter()
    xy = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0], [10.0, 10.0], [5.0, 5.0], [5.0, 5.0]])
    v = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    idw(xy, v, np.array([[5.0, 5.0], [2.0, 7.0], [40.0, 40.0]]), k=3, radius=20.0)
    return time.perf_counter() - t0
