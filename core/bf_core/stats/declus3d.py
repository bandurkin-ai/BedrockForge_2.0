"""Cell declustering in 3D with anisotropy — GSLIB declus (ТЗ 8.2.1; ADR 0018 §7) — and the weights `DECL_W`
of a table by any of the three methods.

    cell_declus_3d(x, y, z, v, cmin, cmax, ncell=100, noff=8, minmax="min", anisy=1.0, anisz=1.0,
                   threads=1, tick=None) -> (weights, cell_sizes, means)        # the returns of BF `cell_declus`
    p = DeclusParams.of({"method": "cell3d", "cmin": 10, "cmax": 200, "ncell": 24, "noff": 8, "minmax": "min",
                         "anisy": 1, "anisz": 0.2})                              # the keys of `composite.declus`
    d = declus_weights(x, y, z, v, p, threads=…, tick=…) -> Declus
        d.weights  float64[n]: NaN where a row has no coordinates or no value; the rest sums to their number
        d.report   method, n, n_missing, naive_mean, mean, uniform, best_size, sizes, means (the curve
                   «размер → среднее»), min_weight, max_weight, n_zero, notes
    warm() — compiles the kernels (the `composites` step of the warm-up)

The method (GSLIB `declus.f`). For every cell size cs of cmin … cmax (ncell + 1 sizes) the cell is
(cs, cs·anisy, cs·anisz); the grid starts at (min − 0,01) of every axis and is shifted `noff` times by
min(cell / noff, half the extent). A sample weighs 1 / (samples in its cell) / (occupied cells), summed over
the shifts. The size with the least ("min") or the greatest ("max") weighted mean wins — the rule of BF
(`declustering.pick_size`); when none beats the naive mean, all weights are 1. Origin, shifts and the rule are
those of BF `cell_declus`: data with one z and any anisz (z = None — anisz = ∞) give its weights (Т8.2-11),
and both give the weights of GeostatsPy `declus` (`tests/data/ref/geostatspy`, ADR 0018 решение 28).

No matrix «sizes × points»: the first pass keeps only the mean of every size, the second one computes the
weights of the chosen size again. A size is one task: the samples of a shift are counted in a hash table of
cells (int64 key of the cell → count), `noff` times; sizes run on threads (`nogil`), each with its own
buffers, and a mean is a pairwise sum in the order of the rows — the result is the same bit for bit with 1,
2, 4 and 8 threads. Memory of a thread: 12 B per slot of the table (2–4 slots a sample) and 12 B per sample;
threads are held back so that the buffers of all of them stay within ~1 GB.

Points are the middles of the intervals in the coordinates of the project; the grid is local to the data
(x − origin), so no reference point is subtracted beforehand.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
from queue import SimpleQueue
from typing import Any

import numpy as np

from bf_core.jit import njit

from .declustering import check_cells, coords, pick_size, polygonal_declus
from .weighted import StatsError, Tick, pair_sums

METHODS = ("cell", "polygon", "cell3d")  # ячейки в плане / полигоны в плане / ячейки в 3D
_HASH = -7046029254386353131  # 2**64 / φ as int64: Fibonacci hashing of the cell key
_MAX_CELLS = 2.0**62  # cells of the finest grid: the key of a cell is one int64
_SCRATCH = 1 << 30  # bytes of the buffers of all the threads
POLYGON_GRID = (200, 4000)  # nodes along an axis of the grid of polygons: 4·√(points), within these


@njit(cache=True, nogil=True)
def _cell_weights(x, y, z, lo, hi, xcs, ycs, zcs, noff, keys, counts, slot, wt):  # type: ignore[no-untyped-def]
    """wt[i] = Σ over `noff` shifts of the grid of 1 / (samples in the cell of i) / (occupied cells).
    `keys` (int64, a power of two ≥ 2n), `counts` (int32, the same length), `slot` (int32[n]) — buffers."""
    n = x.shape[0]
    cap = keys.shape[0]
    mask = cap - 1
    bits = 0
    while (1 << bits) < cap:
        bits += 1
    shift = 64 - bits
    xfac = min(xcs / noff, 0.5 * (hi[0] - lo[0]))
    yfac = min(ycs / noff, 0.5 * (hi[1] - lo[1]))
    zfac = min(zcs / noff, 0.5 * (hi[2] - lo[2]))
    for i in range(n):
        wt[i] = 0.0
    for o in range(noff):
        xo = (lo[0] - 0.01) - o * xfac
        yo = (lo[1] - 0.01) - o * yfac
        zo = (lo[2] - 0.01) - o * zfac
        mx = np.int64((hi[0] - xo) / xcs) + 1  # cells along x and y: the strides of the key
        my = np.int64((hi[1] - yo) / ycs) + 1
        for s in range(cap):
            keys[s] = -1
        occupied = 0
        for i in range(n):
            ix = np.int64((x[i] - xo) / xcs)
            iy = np.int64((y[i] - yo) / ycs)
            iz = np.int64((z[i] - zo) / zcs)
            key = ix + mx * (iy + my * iz)
            s = ((key * _HASH) >> shift) & mask
            while True:
                k = keys[s]
                if k == key:
                    counts[s] += 1
                    break
                if k == -1:
                    keys[s] = key
                    counts[s] = 1
                    occupied += 1
                    break
                s = (s + 1) & mask
            slot[i] = s
        share = 1.0 / occupied
        for i in range(n):
            wt[i] += (1.0 / counts[slot[i]]) * share


def _run[T](fn: Callable[[int], T], n: int, threads: int, tick: Tick | None, top: float) -> list[T]:
    """fn(k) for k < n on threads, the results in order; `tick` gets the share done (up to `top`) in the
    calling thread and may raise: tasks not yet started are dropped."""
    out: list[T] = []

    def done(k: int) -> None:
        if tick is not None:
            tick(top * (k + 1) / n)

    if threads <= 1 or n < 2:
        for k in range(n):
            out.append(fn(k))
            done(k)
        return out
    pool = ThreadPoolExecutor(min(threads, n), thread_name_prefix="bf-declus")
    try:
        for k, res in enumerate(pool.map(fn, range(n))):
            out.append(res)
            done(k)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return out


def cell_declus_3d(
    x: Any,
    y: Any,
    z: Any,
    v: Any,
    cmin: float,
    cmax: float,
    ncell: int = 100,
    noff: int = 8,
    minmax: str = "min",
    anisy: float = 1.0,
    anisz: float = 1.0,
    *,
    threads: int = 1,
    tick: Tick | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cell declustering in 3D (see the module docstring). `z` None — in plan (one layer of cells). Returns
    (weights summing to n, cell sizes with 0 first, means with the naive mean first) — as BF `cell_declus`."""
    if z is None:
        x, y, v = (np.ascontiguousarray(a) for a in coords(x, y, v))
        z, anisz = x, math.inf  # one layer of cells whatever the coordinate
        lo = np.array([x.min(), y.min(), 0.0])
        hi = np.array([x.max(), y.max(), 0.0])
    else:
        x, y, z, v = (np.ascontiguousarray(a) for a in coords(x, y, z, v))
        lo = np.array([x.min(), y.min(), z.min()])
        hi = np.array([x.max(), y.max(), z.max()])
    check_cells(cmin, cmax, ncell, noff, minmax)
    if not (0.0 < anisy < math.inf and anisz > 0.0):  # anisz = ∞ — one layer of cells
        raise StatsError(
            "bad_param", "Анизотропия ячейки должна быть больше нуля.",
            "Задайте отношение размера ячейки по Y и по Z к размеру по X, например 1 и 0,2.",
            param="anisy" if not 0.0 < anisy < math.inf else "anisz",
        )  # fmt: skip
    n = x.shape[0]
    sizes = cmin + np.arange(ncell + 1) * (cmax - cmin) / ncell
    cells = 1.0
    for a, ratio in enumerate((1.0, anisy, anisz)):
        cells *= (hi[a] - lo[a] + 0.01) / (float(sizes[0]) * ratio) + 2.0
    if not cells < _MAX_CELLS:
        raise StatsError(
            "bad_param", "Размер ячейки слишком мал для охвата данных: ячеек больше, чем помещается в расчёт.",
            "Увеличьте «Размер ячейки от».", param="cmin",
        )  # fmt: skip
    cap = 16
    while cap < 2 * n:
        cap *= 2
    threads = max(1, min(int(threads), _SCRATCH // (12 * cap + 12 * n)))
    scratch: SimpleQueue[tuple[np.ndarray, ...]] = SimpleQueue()
    for _ in range(threads):
        scratch.put((np.empty(cap, np.int64), np.empty(cap, np.int32), np.empty(n, np.int32), np.empty(n)))
    uniq, back = np.unique(sizes, return_inverse=True)  # cmin = cmax — one size, counted once

    def weigh(cs: float, buf: tuple[np.ndarray, ...]) -> None:
        keys, counts, slot, wt = buf
        _cell_weights(x, y, z, lo, hi, cs, cs * anisy, cs * anisz, int(noff), keys, counts, slot, wt)

    def mean_of(k: int) -> float:
        buf = scratch.get()
        try:
            weigh(float(uniq[k]), buf)
            total, metal = pair_sums(buf[3], v)
            return float(metal / total)
        finally:
            scratch.put(buf)

    means = np.array(_run(mean_of, uniq.size, threads, tick, 0.9))[back]
    naive = float(v.mean())
    best = pick_size(means, naive, minmax, ncell)
    if best is None:
        wopt = np.ones(n)
    else:
        buf = scratch.get()
        weigh(float(sizes[best]), buf)
        wopt = buf[3] * (n / pair_sums(buf[3], buf[3])[0])
    if tick is not None:
        tick(1.0)
    return wopt, np.r_[0.0, sizes], np.r_[naive, means]


# --- the weights of a table --------------------------------------------------------------------------------


def _number(raw: Any, lo: float, hi: float, label: str, param: str, whole: bool = False) -> float:
    try:
        if isinstance(raw, bool):
            raise TypeError
        value = float(raw.replace(" ", "").replace(",", ".")) if isinstance(raw, str) else float(raw)
    except (TypeError, ValueError):
        value = math.nan
    if not lo <= value <= hi or (whole and value != int(value)):
        span = f"от {lo:g} до {hi:g}".replace(".", ",")
        kind = "целое число" if whole else "число"
        raise StatsError("bad_param", f"{label}: нужно {kind} {span}.", f"Задайте {kind} {span}.", param=param)
    return value


@dataclass(frozen=True)
class DeclusParams:
    """Parameters of declustering; the names and values are those of the command `composite.declus`."""

    method: str = "cell"  # cell | polygon | cell3d
    cmin: float | None = None  # the least and the greatest cell size, m — required for cells
    cmax: float | None = None
    ncell: int = 20  # steps of the cell size: ncell + 1 sizes
    noff: int = 8  # shifts of the origin of the grid
    minmax: str = "min"  # min | max of the declustered mean
    anisy: float = 1.0  # cell3d: the cell along Y and Z in sizes along X
    anisz: float = 1.0
    nx: int | None = None  # polygon: nodes of the grid; None — by the number of points
    ny: int | None = None

    @classmethod
    def of(cls, raw: Mapping[str, Any]) -> DeclusParams:
        """From the parameters of a request: absent, None or "" — the default; checked (StatsError)."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known and v is not None and v != ""}).checked()

    def checked(self) -> DeclusParams:
        if self.method not in METHODS:
            raise StatsError(
                "bad_param", f"Метод декластеризации «{self.method}» неизвестен.",
                "Допустимо: cell — ячейки в плане, polygon — полигоны в плане, cell3d — ячейки в 3D.",
                param="method",
            )  # fmt: skip
        if self.method == "polygon":
            grid = [None if g is None else int(_number(g, 1, 20000, "Сетка полигонов", key, True))
                    for key, g in (("nx", self.nx), ("ny", self.ny))]  # fmt: skip
            return DeclusParams("polygon", nx=grid[0], ny=grid[1])
        for key, value in (("cmin", self.cmin), ("cmax", self.cmax)):
            if value is None:
                raise StatsError(
                    "bad_param", "Не задан размер ячейки.",
                    "Укажите «Размер ячейки от» и «до», м: обычно от шага самой плотной сети до шага редкой.",
                    param=key,
                )  # fmt: skip
        cmin = _number(self.cmin, 0.01, 1e7, "Размер ячейки от, м", "cmin")
        cmax = _number(self.cmax, 0.01, 1e7, "Размер ячейки до, м", "cmax")
        if cmin > cmax:
            raise StatsError(
                "bad_param", "«Размер ячейки от» больше «до».", "Задайте «от» не больше «до».", param="cmin"
            )
        if self.minmax not in ("min", "max"):
            check_cells(cmin, cmax, 1, 1, str(self.minmax))
        solid = self.method == "cell3d"
        return DeclusParams(
            self.method, cmin, cmax,
            int(_number(self.ncell, 1, 500, "Число размеров", "ncell", True)),
            int(_number(self.noff, 1, 64, "Сдвигов начала", "noff", True)),
            self.minmax,
            _number(self.anisy, 0.01, 100, "Анизотропия Y", "anisy") if solid else 1.0,
            _number(self.anisz, 0.01, 1e6, "Анизотропия Z", "anisz") if solid else 1.0,
        )  # fmt: skip


@dataclass(frozen=True)
class Declus:
    weights: np.ndarray
    report: dict[str, Any]


def polygon_grid(points: int) -> int:
    """Nodes along an axis of the grid of polygons for a number of distinct points: about 16 nodes a point."""
    return int(min(POLYGON_GRID[1], max(POLYGON_GRID[0], math.ceil(4.0 * math.sqrt(points)))))


def declus_weights(
    x: Any, y: Any, z: Any, v: Any, params: DeclusParams, *, threads: int = 1, tick: Tick | None = None
) -> Declus:
    """`DECL_W` of the rows of a table: x, y, z — the middles of the intervals, v — the field. A row without a
    coordinate or without a value gets NaN; the weights of the rest sum to their number. `z` is read by
    "cell3d" only (None — an error there)."""
    p = params.checked()
    cols = [np.asarray(a, dtype=np.float64) for a in ((x, y, v) if p.method != "cell3d" else (x, y, v, z))]
    if p.method == "cell3d" and z is None:
        raise StatsError("no_data", "Для ячеек в 3D нужны координаты Z проб.", "Проверьте траектории скважин.")
    if any(c.ndim != 1 or c.shape != cols[0].shape for c in cols):
        raise StatsError("bad_input", "Координаты и значения разной длины.", "Передайте колонки одной длины.")
    ok = np.logical_and.reduce([np.isfinite(c) for c in cols])
    n = int(ok.sum())
    if n == 0:
        raise StatsError(
            "no_data", "Нет проб с координатами и значением поля.",
            "Проверьте траектории скважин и выбранное поле.",
        )  # fmt: skip
    px, py, pv = (np.ascontiguousarray(c[ok]) for c in cols[:3])
    report: dict[str, Any] = {"method": p.method, "n": n, "n_missing": int(ok.size - n), "notes": []}
    sizes = means = np.zeros(0)
    best: float | None = None
    if p.method == "polygon":
        side = polygon_grid(np.unique(px + 1j * py).size)
        nx, ny = p.nx or side, p.ny or side
        w = polygonal_declus(px, py, nx=nx, ny=ny, workers=max(1, int(threads)), tick=tick)
        report.update({"nx": nx, "ny": ny})
        uniform = False
    else:
        pz = np.ascontiguousarray(cols[3][ok]) if p.method == "cell3d" else None
        assert p.cmin is not None and p.cmax is not None
        w, sizes, means = cell_declus_3d(px, py, pz, pv, p.cmin, p.cmax, p.ncell, p.noff, p.minmax,
                                         p.anisy, p.anisz, threads=threads, tick=tick)  # fmt: skip
        k = pick_size(means[1:], float(means[0]), p.minmax, p.ncell)
        uniform = k is None
        best = None if k is None else float(sizes[1 + k])
        if uniform:
            report["notes"].append("Ни один размер ячейки не изменил среднее в нужную сторону — веса равны 1.")
    total, metal = pair_sums(w, pv)
    n_zero = int((w == 0.0).sum())
    if n_zero:
        report["notes"].append(f"Проб с нулевым весом: {n_zero} — их полигоны меньше ячейки сетки {nx} × {ny}.")
    report.update({
        "naive_mean": float(pv.mean()),
        "mean": float(metal / total),
        "uniform": uniform,
        "best_size": best,
        "sizes": [float(s) for s in sizes[1:]],
        "means": [float(m) for m in means[1:]],
        "min_weight": float(w.min()),
        "max_weight": float(w.max()),
        "n_zero": n_zero,
    })  # fmt: skip
    out = np.full(ok.size, np.nan)
    out[ok] = w
    return Declus(out, report)


def warm() -> None:
    """Compile the kernels of declustering on a few points (first compilation — in the background)."""
    pts = np.array([0.0, 1.0, 2.0, 5.0])
    cell_declus_3d(pts, pts[::-1], pts, pts, 1.0, 2.0, ncell=1, noff=2)
