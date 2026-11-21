"""Swath plot: a series of a regular block model (ТЗ 7.30, 7.35 Д-01; ADR 0019 §5; Э13, 13.11) — written anew
(BF kept dense centres and sizes of the blocks; BF2 keeps the numbers of the cells only, `blocks/schema.py`).
The columns of the model are read by the caller (the Parquet file by row groups, or the cache of columns);
nothing of the size of the model is built here: no centres, no sizes, no masks.

    block_bounds(f, grid, idx, codes=None, keep=None, threads=1, tick=None) -> 3×2
        the extent of the cells of the series along u, v, w (rows (min, max); NaN — no cells): what `swath.cuboid`
        takes as `models`. A cell reaches half of its sides from its centre: Σ |e_i|·size_i / 2 along the axis e
    block_series(grid, idx, values, f, cuboid, weight="volume", density=None, codes=None, keep=None,
                 threads=1, tick=None) -> swath.SeriesResult
        the mean, the number of cells and the weight by swath, the totals, the counts
    BlockExtent(f, grid, keep=None, threads=1).add(idx, codes=None) … .result()
    BlockSums(grid, f, cuboid, weight=, keep=, threads=, tick=, rows=).add(idx, values, density=None, codes=None)
        … .result() — the same two by portions of the columns (the row groups of the file, in its order)
    keep_of(labels, chosen) -> bool[len(labels)]    the labels of a category field that the series takes
    selected_idx(idx, codes=None, keep=None) -> idx of the cells taken (for `swath.cells_of`: samples «inside
        the blocks» of the series see its selection) — both live in `swath_cells`, named here too
    warm()

`grid` — `meta.grid` of the model; `idx` — the column of the numbers of its occupied cells (uint64 of the file,
uint32 of the cache of columns, int64), `values` — the field (float64, NaN — empty), `density` — the field of
the density for the tonnage, `codes` — a category field (int32 codes of its labels, −1 — empty) with `keep`.

The cell of a row: idx → (i, j, k), i = idx mod nx … — one division per row of cells along X (the rows of the
file are ascending; any order is read right, only slower). Its centre along an axis e of the frame is
t = (a[i] + b[j]) + c[k] — three arrays of the projections of the centres of the grid along X, Y and Z (a term
of a zero component of e is 0): the bits of `swath_frame.project` of the dense centre, so along X it is the
coordinate itself and an azimuth of 90° is X bit for bit (ТД01-5). The swath of a cell is that of its centre,
by the rule of the samples (`swath._locate`): inside the cuboid `limits` with its tolerance,
k = ⌊(t_u − start) / width⌋ kept within 0 … n − 1.

The weight of a cell: its volume dx[i]·dy[j]·dz[k] (the variable step of OMF tensors — the sizes of its own
cell), the tonnage — the volume × the density, or 1. A cell takes part when it is in the selection, has a
value and a weight > 0 and its centre is inside the cuboid; the rest is counted, nothing becomes 0:

    counts: rows — the cells of the model, filtered_out — not in the selection by categories (or without a
    category), missing — no value, bad_weight — no density or a density ≤ 0, selected — the rest,
    outside_box — of them outside the cuboid, used — in the swaths

Sums. The rows are cut into groups of `GROUP` rows counted from the first row of the model — however the
columns are handed over. A group is summed by one thread in the order of its rows: Σ w and Σ w·v of a swath are
`weighted.pair_sums` of its rows of the group. The sums of the groups are added pairwise in the order of the
groups (a binary counter). So the answer is the same bit for bit with 1, 2, 4 and 8 threads and for the whole
columns and their portions; a model of one group gives `pair_sums` of the cells of every swath. The extent is
a minimum and a maximum — the same for any cut. Kernels: numba `cache=True, nogil=True`, no `prange`, no
`fastmath`; the threads are a pool over the groups. Memory above the columns: the state of a group per thread
(swaths × 17 levels × 16 B) and the sums of the groups (24 B a swath each) — a few MB.

Errors: `StatsError` `bad_param` (the weight, the tonnage without a density, numbers of cells that are not of
the grid); a ValueError — columns of different lengths or types (a mistake of the caller).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np

from bf_core.jit import njit

from .swath import Cuboid, SeriesResult, series_result
from .swath_cells import as_codes, as_column, as_keep, as_numbers, keep_of, selected_idx
from .swath_frame import Frame
from .weighted import StatsError, Tick

GROUP = 1 << 18  # rows of a group: the unit of the threads and of the pairwise sums
WEIGHTS = ("volume", "tonnage", "none")
_FILTERED, _MISSING, _BAD_WEIGHT, _OUTSIDE, _BAD_CELL = range(5)
_NO_NUMBERS = np.zeros(0, dtype=np.float64)
_NO_CODES = np.zeros(0, dtype=np.int32)
_NO_KEEP = np.zeros(0, dtype=np.bool_)
Columns = tuple[np.ndarray, ...]

__all__ = [
    "GROUP",
    "WEIGHTS",
    "BlockExtent",
    "BlockSums",
    "block_bounds",
    "block_series",
    "keep_of",
    "selected_idx",
    "warm",
]

# --- kernels ----------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _sums(  # type: ignore[no-untyped-def]
    idx, v, dens, codes, keep, has_codes, mode, nx, ny, cells, pa, pb, pc, dx, dy, dz, lim, tol, start, width, n
):
    """A group of rows into the sums of its swaths: (Σ w, Σ w·v, the number of cells) by swath and the counts
    of what was left out. `pa`, `pb`, `pc` — 3 × (nx, ny, nz): the projections of the centres of the grid along
    X, Y, Z onto u, v, w. Per swath — the cascade of `weighted.pair_sums`."""
    rows = idx.shape[0]
    levels = 1
    m = rows >> 3
    while m > 0:
        levels += 1
        m >>= 1
    st = np.empty((n, levels, 2))
    depth = np.zeros(n, dtype=np.int64)
    s0 = np.zeros(n)
    s1 = np.zeros(n)
    c = np.zeros(n, dtype=np.int64)
    cnt = np.zeros(5, dtype=np.int64)
    labels = keep.shape[0]
    last = float(n - 1)
    u0, u1 = lim[0, 0] - tol[0], lim[0, 1] + tol[0]
    v0, v1 = lim[1, 0] - tol[1], lim[1, 1] + tol[1]
    w0, w1 = lim[2, 0] - tol[2], lim[2, 1] + tol[2]
    row_lo = row_hi = np.int64(0)  # the cells of the current row along X: row_lo … row_hi − 1
    ub = uc = vb = vc = wb = wc = ys = zs = 0.0
    for r in range(rows):
        cell = np.int64(idx[r])
        if cell < row_lo or cell >= row_hi:
            if cell < 0 or cell >= cells:
                cnt[4] += 1
                continue
            row = cell // nx
            row_lo = row * nx
            row_hi = row_lo + nx
            j = row % ny
            kz = row // ny
            ub, vb, wb, ys = pb[0, j], pb[1, j], pb[2, j], dy[j]
            uc, vc, wc, zs = pc[0, kz], pc[1, kz], pc[2, kz], dz[kz]
        if has_codes:
            code = codes[r]
            if code < 0 or code >= labels or not keep[code]:
                cnt[0] += 1
                continue
        x = v[r]
        if not np.isfinite(x):
            cnt[1] += 1
            continue
        i = cell - row_lo
        if mode == 0:
            w = dx[i] * ys * zs
        elif mode == 1:
            w = dx[i] * ys * zs * dens[r]
        else:
            w = 1.0
        if not (w > 0.0 and w < np.inf):
            cnt[2] += 1
            continue
        tu = (pa[0, i] + ub) + uc
        if not (tu >= u0 and tu <= u1):
            cnt[3] += 1
            continue
        t = (pa[1, i] + vb) + vc
        if not (t >= v0 and t <= v1):
            cnt[3] += 1
            continue
        t = (pa[2, i] + wb) + wc
        if not (t >= w0 and t <= w1):
            cnt[3] += 1
            continue
        q = np.floor((tu - start) / width)
        if q < 0.0:
            q = 0.0
        elif q > last:
            q = last
        s = int(q)
        s0[s] += w
        s1[s] += w * x
        m = c[s] + 1
        c[s] = m
        if (m & 7) == 0:
            m >>= 3
            d = depth[s]
            a0 = s0[s]
            a1 = s1[s]
            while (m & 1) == 0:
                d -= 1
                a0 = st[s, d, 0] + a0
                a1 = st[s, d, 1] + a1
                m >>= 1
            st[s, d, 0] = a0
            st[s, d, 1] = a1
            depth[s] = d + 1
            s0[s] = 0.0
            s1[s] = 0.0
    weight = np.empty(n)
    wsum = np.empty(n)
    for s in range(n):
        a0 = s0[s]
        a1 = s1[s]
        d = depth[s]
        while d > 0:
            d -= 1
            a0 = st[s, d, 0] + a0
            a1 = st[s, d, 1] + a1
        weight[s] = a0
        wsum[s] = a1
    return weight, wsum, c, cnt


@njit(cache=True, nogil=True)
def _extent(idx, codes, keep, has_codes, nx, ny, cells, la, lb, lc, ha, hb, hc):  # type: ignore[no-untyped-def]
    """The extent of the cells of the rows taken: 3×2 (min, max) along u, v, w (+inf, −inf — none), the number
    of them and of the numbers that are not of the grid. `la` … `hc` — 3 × (nx, ny, nz): the low and the high
    side of a cell of the grid along X, Y, Z in the projection onto u, v, w."""
    out = np.empty((3, 2))
    out[:, 0] = np.inf
    out[:, 1] = -np.inf
    labels = keep.shape[0]
    taken = 0
    bad = 0
    row_lo = row_hi = np.int64(0)
    low = np.zeros(3)
    high = np.zeros(3)
    for r in range(idx.shape[0]):
        cell = np.int64(idx[r])
        if cell < row_lo or cell >= row_hi:
            if cell < 0 or cell >= cells:
                bad += 1
                continue
            row = cell // nx
            row_lo = row * nx
            row_hi = row_lo + nx
            j = row % ny
            kz = row // ny
            for e in range(3):
                low[e] = lb[e, j] + lc[e, kz]
                high[e] = hb[e, j] + hc[e, kz]
        if has_codes:
            code = codes[r]
            if code < 0 or code >= labels or not keep[code]:
                continue
        i = cell - row_lo
        taken += 1
        for e in range(3):
            t = la[e, i] + low[e]
            if t < out[e, 0]:
                out[e, 0] = t
            t = ha[e, i] + high[e]
            if t > out[e, 1]:
                out[e, 1] = t
    return out, taken, bad


# --- the grid in the frame, the columns ---------------------------------------------------------------------------


class _Axes:
    """The grid along X, Y, Z in the frame: the projections of the centres of its cells onto u, v, w (the
    centres of `schema.centers`, the terms of `swath_frame.project`), the low and the high sides, the steps."""

    def __init__(self, grid: Mapping[str, Any], f: Frame) -> None:
        from bf_core.blocks import schema

        self.nx, self.ny, self.nz = (int(c) for c in grid["counts"])
        self.cells = self.nx * self.ny * self.nz
        axes = f.axes
        self.steps: list[np.ndarray] = []
        self.proj: list[np.ndarray] = []
        self.low: list[np.ndarray] = []
        self.high: list[np.ndarray] = []
        for a in range(3):
            e = np.asarray(schema.edges(grid, a), dtype=np.float64)
            mid = (e[:-1] + e[1:]) * 0.5
            step = np.ascontiguousarray(schema.steps(grid, a), dtype=np.float64)
            p = np.zeros((3, mid.size))
            for k in range(3):
                if axes[k, a] != 0.0:
                    p[k] += mid * axes[k, a]
            half = 0.5 * np.abs(axes[:, a])[:, None] * step[None, :]
            self.steps.append(step)
            self.proj.append(p)
            self.low.append(p - half)
            self.high.append(p + half)


def _bad_cells() -> StatsError:
    return StatsError(
        "bad_param",
        "Номера ячеек модели не соответствуют её сетке.",
        "Обновите модель: её ячейки и сетка записаны несогласованно.",
        param="series",
    )


def _run[T, R](fn: Callable[[T], R], tasks: Sequence[T], threads: int, done: Callable[[R], None]) -> None:
    """fn(task) of every task on threads; `done(result)` in the order of the tasks, in the calling thread — it
    may raise (a cancel through `tick`): the tasks not yet started are dropped."""
    if threads <= 1 or len(tasks) < 2:
        for task in tasks:
            done(fn(task))
        return
    pool = ThreadPoolExecutor(min(threads, len(tasks)), thread_name_prefix="bf-swath")
    try:
        for res in pool.map(fn, tasks):
            done(res)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


# --- the extent ---------------------------------------------------------------------------------------------------


class BlockExtent:
    """The extent of the cells of a model series along u, v, w by portions of its columns."""

    def __init__(self, f: Frame, grid: Mapping[str, Any], *, keep: Any = None, threads: int = 1) -> None:
        self._ax = _Axes(grid, f)
        self._keep = as_keep(keep)
        self._threads = max(1, int(threads))
        self._out = np.column_stack([np.full(3, np.inf), np.full(3, -np.inf)])
        self._taken = 0

    def add(self, idx: Any, codes: Any = None, *, tick: Tick | None = None) -> None:
        """A portion of the rows: `idx` and, with a selection, the codes of its category field."""
        numbers = as_numbers(idx)
        n = int(numbers.shape[0])
        cd = as_codes(codes, self._keep, n)
        ax, keep = self._ax, self._keep if self._keep is not None else _NO_KEEP
        (la, lb, lc), (ha, hb, hc) = ax.low, ax.high
        done = 0

        def part(a: int) -> Any:
            return _extent(
                numbers[a : a + GROUP], _NO_CODES if cd is None else cd[a : a + GROUP], keep, cd is not None,
                ax.nx, ax.ny, ax.cells, la, lb, lc, ha, hb, hc,
            )  # fmt: skip

        def fold(res: Any) -> None:
            nonlocal done
            out, taken, bad = res
            if bad:
                raise _bad_cells()
            np.minimum(self._out[:, 0], out[:, 0], out=self._out[:, 0])
            np.maximum(self._out[:, 1], out[:, 1], out=self._out[:, 1])
            self._taken += int(taken)
            done += 1
            if tick is not None:
                tick(min(1.0, done * GROUP / n))

        _run(part, range(0, n, GROUP), self._threads, fold)

    def result(self) -> np.ndarray:
        """3×2: (min, max) along u, v, w; NaN — the series has no cells."""
        return self._out.copy() if self._taken else np.full((3, 2), np.nan)


def block_bounds(
    f: Frame,
    grid: Mapping[str, Any],
    idx: Any,
    *,
    codes: Any = None,
    keep: Any = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> np.ndarray:
    """The extent of the cells of a model series (after its selection) along u, v, w — see the module
    docstring. Cells without a value are cells of the series too (as BF)."""
    box = BlockExtent(f, grid, keep=keep, threads=threads)
    box.add(idx, codes, tick=tick)
    if tick is not None:
        tick(1.0)
    return box.result()


# --- the series ---------------------------------------------------------------------------------------------------


class BlockSums:
    """A model series by swath, by portions of its columns in the order of the file (see the module
    docstring). `rows` — the rows to come in all, for `tick`."""

    def __init__(
        self,
        grid: Mapping[str, Any],
        f: Frame,
        cuboid: Cuboid,
        *,
        weight: str = "volume",
        keep: Any = None,
        threads: int = 1,
        tick: Tick | None = None,
        rows: int | None = None,
    ) -> None:
        if weight not in WEIGHTS:
            raise StatsError(
                "bad_param",
                f"Вес блоков «{weight}» неизвестен.",
                "Допустимо: volume — объём, tonnage — тоннаж (объём × плотность), none — без веса.",
                param="weight",
            )
        self._ax = _Axes(grid, f)
        self._mode = WEIGHTS.index(weight)
        self._keep = as_keep(keep)
        self._lim = np.ascontiguousarray(cuboid.limits, dtype=np.float64)
        self._tol = np.ascontiguousarray(cuboid.tol, dtype=np.float64)
        self._start, self._width, self._n = float(cuboid.start), float(cuboid.width), int(cuboid.count)
        self._threads = max(1, int(threads))
        self._tick, self._total = tick, rows
        self._group = GROUP
        self._held: list[Columns] = []  # the rows of a group not yet whole
        self._held_rows = 0
        self._stack: list[tuple[int, np.ndarray, np.ndarray, np.ndarray]] = []  # the binary counter of the groups
        self._cnt = np.zeros(5, dtype=np.int64)
        self._rows = 0
        self._done = 0

    def add(self, idx: Any, values: Any, density: Any = None, codes: Any = None) -> None:
        """A portion of the rows: the columns `idx`, the field, the density (for the tonnage) and the codes of
        the category field (with a selection)."""
        numbers = as_numbers(idx)
        n = int(numbers.shape[0])
        if self._mode == 1 and density is None:
            raise StatsError(
                "bad_param",
                "Для веса по тоннажу не задано поле плотности.",
                "Выберите поле плотности модели или вес «объём».",
                param="density",
            )
        cd = as_codes(codes, self._keep, n)
        cols: Columns = (
            numbers,
            as_column(values, n, np.float64, "values"),
            as_column(density, n, np.float64, "density") if self._mode == 1 else _NO_NUMBERS,
            _NO_CODES if cd is None else cd,
        )
        self._rows += n
        self._sum_groups(self._cut(cols, n))

    def result(self) -> SeriesResult:
        if self._held_rows:
            self._sum_groups([self._join()])
        if self._tick is not None:
            self._tick(1.0)
        if self._stack:
            _, weight, wsum, count = self._stack.pop()
            while self._stack:
                _, w, s, c = self._stack.pop()
                weight, wsum, count = w + weight, s + wsum, c + count
        else:
            weight, wsum, count = np.zeros(self._n), np.zeros(self._n), np.zeros(self._n, dtype=np.int64)
        left = [int(x) for x in self._cnt]
        counts = {
            "rows": self._rows,
            "filtered_out": left[_FILTERED],
            "missing": left[_MISSING],
            "bad_weight": left[_BAD_WEIGHT],
            "selected": self._rows - left[_FILTERED] - left[_MISSING] - left[_BAD_WEIGHT],
            "outside_box": left[_OUTSIDE],
        }
        return series_result(weight, wsum, count, counts)

    # The groups: `GROUP` rows each, counted from the first row of the model.

    def _cut(self, cols: Columns, n: int) -> list[Columns]:
        """The whole groups that the portion completes; its tail is held for the next portion (a copy)."""
        group, out, pos = self._group, [], 0
        if self._held_rows:
            pos = min(group - self._held_rows, n)
            self._hold(cols, 0, pos)
            if self._held_rows == group:
                out.append(self._join())
        while n - pos >= group:
            out.append(tuple(c[pos : pos + group] if c.size else c for c in cols))
            pos += group
        if pos < n:
            self._hold(cols, pos, n)
        return out

    def _hold(self, cols: Columns, a: int, b: int) -> None:
        self._held.append(tuple(c[a:b].copy() if c.size else c for c in cols))
        self._held_rows += b - a

    def _join(self) -> Columns:
        held, self._held, self._held_rows = self._held, [], 0
        return held[0] if len(held) == 1 else tuple(np.concatenate(parts) for parts in zip(*held, strict=True))

    def _sum_groups(self, groups: Sequence[Columns]) -> None:
        ax, keep = self._ax, self._keep if self._keep is not None else _NO_KEEP

        (pa, pb, pc), (dx, dy, dz) = ax.proj, ax.steps

        def sums(cols: Columns) -> Any:
            idx, v, dens, codes = cols
            return _sums(
                idx, v, dens, codes, keep, self._keep is not None, self._mode, ax.nx, ax.ny, ax.cells,
                pa, pb, pc, dx, dy, dz, self._lim, self._tol, self._start, self._width, self._n,
            )  # fmt: skip

        def fold(res: Any) -> None:
            weight, wsum, count, cnt = res
            if cnt[_BAD_CELL]:
                raise _bad_cells()
            self._cnt += cnt
            level = 0
            while self._stack and self._stack[-1][0] == level:
                _, w, s, c = self._stack.pop()
                weight, wsum, count = w + weight, s + wsum, c + count
                level += 1
            self._stack.append((level, weight, wsum, count))
            self._done += self._group
            if self._tick is not None and self._total:
                self._tick(min(1.0, self._done / self._total))

        _run(sums, groups, self._threads, fold)


def block_series(
    grid: Mapping[str, Any],
    idx: Any,
    values: Any,
    f: Frame,
    cuboid: Cuboid,
    *,
    weight: str = "volume",
    density: Any = None,
    codes: Any = None,
    keep: Any = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> SeriesResult:
    """A model series by swath on its columns in memory (see the module docstring): `values` — the field,
    `weight` — volume | tonnage (with `density`) | none, `codes` and `keep` — the selection by categories."""
    numbers = as_numbers(idx)
    acc = BlockSums(grid, f, cuboid, weight=weight, keep=keep, threads=threads, tick=tick, rows=int(numbers.shape[0]))
    acc.add(numbers, values, density=density, codes=codes)
    return acc.result()


def warm() -> None:
    """Compile the kernels for the two types of `idx` (the file and the cache of columns) on a few cells."""
    from . import swath_frame as sf
    from .swath import cuboid

    grid = {"corner": [0.0, 0.0, 0.0], "counts": [3, 2, 2], "size": [5.0, 5.0, 2.0], "order": "F"}
    f = sf.frame(30.0, -10.0, 5.0)
    codes, keep = np.array([0, 1, 0, -1], dtype=np.int32), np.array([True, False])
    values, density = np.array([1.0, 2.0, np.nan, 4.0]), np.ones(4)
    for dtype in (np.uint64, np.uint32):
        idx = np.array([0, 4, 7, 11], dtype=dtype)
        c = cuboid(f, models=[block_bounds(f, grid, idx, codes=codes, keep=keep)], width=2.0)
        block_series(grid, idx, values, f, c, weight="tonnage", density=density, codes=codes, keep=keep)
