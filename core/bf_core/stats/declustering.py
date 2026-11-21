"""Declustering weights in plan: by cells and by polygons (nearest sample) — the port of BF
`api/app/stats/declustering.py` (ТЗ 8.2.1, ADR 0018 §7), behaviour and tests kept.

    cell_declus(x, y, v, cmin, cmax, ncell=100, noff=8, minmax="min") -> (weights, cell_sizes, means)
        GSLIB declus in 2D with isotropic cells, vectorized with numpy. It holds a matrix «sizes × points» and
        sorts the cells of every offset, so it is the reference for small sets; a table goes through
        `declus3d.cell_declus_3d` — the same weights without the matrix (Т8.2-11).
    polygonal_declus(x, y, bounds=None, nx=200, ny=200, workers=1, tick=None) -> weights
        the area of the polygon of every sample: the centres of an nx × ny grid over `bounds` go to their
        nearest sample (`cKDTree`)
    pick_size(means, naive, minmax, ncell) -> index | None      the rule that chooses the cell size

All weights sum to n (the mean weight is 1). Inputs must be finite (`declus3d.declus_weights` leaves out the
rows without coordinates or a value first). Errors — `StatsError` {code, message, hint}, a ValueError as in BF.

Differences from BF, none of them on its tests: samples at one point in plan (the samples of a vertical hole)
share the count of their polygon equally — BF gave it all to one of them, whichever the tree met first; the
grid is queried by rows, so a fine grid does not hold all its nodes in memory.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .weighted import StatsError, Tick

_NODES = 1 << 18  # grid nodes of one query of `polygonal_declus`


def as_1d(a: Any, name: str = "array") -> np.ndarray:
    """A 1D float64 array or StatsError (BF `_utils.as_1d`)."""
    out = np.asarray(a, dtype=np.float64)
    if out.ndim != 1:
        raise StatsError("bad_input", f"Массив {name} должен быть одномерным.", "Передайте колонку значений.")
    return out


def coords(*arrays: Any) -> list[np.ndarray]:
    """Columns of one length, not empty, all finite (BF `_coords`) — or StatsError."""
    out = [as_1d(a) for a in arrays]
    if len({a.shape[0] for a in out}) != 1:
        raise StatsError("bad_input", "Координаты и значения разной длины.", "Передайте колонки одной длины.")
    if out[0].size == 0:
        raise StatsError(
            "no_data", "Нет точек для декластеризации.", "Проверьте, что у проб есть координаты и значение поля."
        )
    if not all(bool(np.all(np.isfinite(a))) for a in out):
        raise StatsError(
            "bad_input", "В координатах или значениях есть пропуски.", "Исключите пробы без координат и значения."
        )
    return out


def check_cells(cmin: float, cmax: float, ncell: int, noff: int, minmax: str) -> None:
    """The parameters of cell declustering as BF checks them."""
    if not (cmin > 0 and cmax >= cmin):
        raise StatsError(
            "bad_param", "Размер ячейки: нужно 0 < «от» ≤ «до».",
            "Задайте размеры ячейки больше нуля, «от» — не больше «до».", param="cmin",
        )  # fmt: skip
    if ncell < 1 or noff < 1:
        raise StatsError(
            "bad_param", "Число размеров и число сдвигов начала должны быть не меньше 1.",
            "Задайте, например, 20 размеров и 8 сдвигов.", param="ncell" if ncell < 1 else "noff",
        )  # fmt: skip
    if minmax not in ("min", "max"):
        raise StatsError(
            "bad_param", f"Критерий «{minmax}» неизвестен.", "Допустимо: min — минимум среднего, max — максимум.",
            param="minmax",
        )  # fmt: skip


def pick_size(means: np.ndarray, naive: float, minmax: str, ncell: int) -> int | None:
    """The index of the cell size whose declustered mean is the least ("min") or the greatest ("max"), the
    first of equal ones; None — no size beats the naive mean by more than 1e−12, the weights are uniform (as
    in GSLIB). With ncell = 1 the best of the two sizes is taken anyway."""
    cand = means if minmax == "min" else -means
    best = int(np.argmin(cand))
    ref = naive if minmax == "min" else -naive
    improves = cand[best] < ref - 1e-12 * max(1.0, abs(ref))
    return best if (improves or ncell == 1) else None


def cell_declus(
    x: Any, y: Any, v: Any, cmin: float, cmax: float, ncell: int = 100, noff: int = 8, minmax: str = "min"
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cell declustering (GSLIB declus, 2D, isotropic cells).

    For each of ``ncell + 1`` cell sizes in [cmin, cmax] and ``noff`` grid origin offsets, every datum gets a
    weight inversely proportional to the number of data in its cell. The cell size whose declustered mean is
    the minimum (``minmax="min"``, for preferentially clustered high values) or maximum (``"max"``) is
    selected. If no size improves on the naive mean, uniform weights are returned (as in GSLIB).

    :return: (weights, cell_sizes, means); ``cell_sizes[0] = 0`` holds the naive mean, followed by the
        ``ncell + 1`` tried sizes
    """
    x, y, v = coords(x, y, v)
    check_cells(cmin, cmax, ncell, noff, minmax)

    n = x.size
    xo1, yo1 = x.min() - 0.01, y.min() - 0.01  # "lower" origin as in GSLIB
    xspan, yspan = x.max() - x.min(), y.max() - y.min()
    sizes = cmin + np.arange(ncell + 1) * (cmax - cmin) / ncell

    all_wts = np.empty((ncell + 1, n))
    for k, cs in enumerate(sizes):
        xfac = min(cs / noff, 0.5 * xspan)
        yfac = min(cs / noff, 0.5 * yspan)
        wt = np.zeros(n)
        for o in range(noff):
            ix = np.floor((x - (xo1 - o * xfac)) / cs).astype(np.int64)
            iy = np.floor((y - (yo1 - o * yfac)) / cs).astype(np.int64)
            key = ix * (iy.max() + 1) + iy
            _, inv, counts = np.unique(key, return_inverse=True, return_counts=True)
            w = 1.0 / counts[inv]
            wt += w / w.sum()  # each offset sums to 1
        all_wts[k] = wt

    means = all_wts @ v / all_wts.sum(axis=1)
    naive = v.mean()
    best = pick_size(means, float(naive), minmax, ncell)
    wopt = np.ones(n) if best is None else all_wts[best]
    wopt = wopt * n / wopt.sum()
    return wopt, np.r_[0.0, sizes], np.r_[naive, means]


def polygonal_declus(
    x: Any,
    y: Any,
    bounds: Any = None,
    nx: int = 200,
    ny: int = 200,
    *,
    workers: int = 1,
    tick: Tick | None = None,
) -> np.ndarray:
    """Polygonal (nearest-sample / Voronoi) declustering weights.

    The area of each sample's Voronoi polygon, clipped to ``bounds``, is estimated by assigning the centers of
    an ``nx`` x ``ny`` grid to their nearest sample. Samples whose polygon contains no grid center get weight
    0, so increase ``nx``/``ny`` for very small polygons. Samples at one point share their polygon equally.

    :param bounds: (xmin, xmax, ymin, ymax); default: data extent
    :param workers: threads of the nearest-sample query; the weights do not depend on it
    :return: weights, length n, summing to n
    """
    from scipy.spatial import cKDTree

    x, y = coords(x, y)
    if bounds is None:
        bounds = (x.min(), x.max(), y.min(), y.max())
    x0, x1, y0, y1 = map(float, bounds)
    if not (x1 > x0 and y1 > y0):
        raise StatsError(
            "bad_param", "Полигоны в плане: охват по X и по Y должен быть больше нуля.",
            "Нужны точки, разнесённые по обеим осям; для одной скважины или профиля используйте ячейки.",
            param="bounds",
        )  # fmt: skip
    if nx < 1 or ny < 1:
        raise StatsError("bad_param", "Сетка полигонов: nx и ny должны быть не меньше 1.", "Задайте, например, 200.")

    n = x.size
    pts = np.column_stack([x, y])
    spot, inv, dup = np.unique(x + 1j * y, return_inverse=True, return_counts=True)
    shared = spot.size < n  # samples at one point in plan
    tree = cKDTree(np.column_stack([spot.real, spot.imag]) if shared else pts)
    counts = np.zeros(spot.size if shared else n)
    gx = x0 + (np.arange(nx) + 0.5) * (x1 - x0) / nx
    gy = y0 + (np.arange(ny) + 0.5) * (y1 - y0) / ny
    rows = max(1, _NODES // nx)
    for j0 in range(0, ny, rows):
        part = gy[j0 : j0 + rows]
        nodes = np.column_stack([np.tile(gx, part.size), np.repeat(part, nx)])
        _, nearest = tree.query(nodes, workers=workers)
        counts += np.bincount(nearest, minlength=counts.size)
        if tick is not None:
            tick(min(1.0, (j0 + rows) / ny))
    if shared:
        counts = counts[inv] / dup[inv]
    return counts * n / counts.sum()
