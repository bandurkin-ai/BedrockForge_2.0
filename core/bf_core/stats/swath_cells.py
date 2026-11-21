"""Swath plot: which samples are inside the blocks of a model series (ТЗ 7.30, Т7.30-2; ADR 0019 §5) — BF
`points_in_blocks` on the cells of a regular model: point → (i, j, k) → `idx` → a lookup among the occupied
cells; no centres or sizes of the cells are built. A part of `stats/swath.py` (13.10) kept apart for its size;
`swath` re-exports the names. The selection of a model series by categories (13.11) is here too: the columns
of a model as the kernels take them, the labels chosen, the cells taken.

    cells_of(grid, idx) -> Cells                 the occupied cells of a model series after its selection
    points_in_cells(points, cells, method=None) -> bool[n]
    inside_cells(points, rows, has_rows, k, cells, …) -> bool[n]    the same for the rows of a selection
    keep_of(labels, chosen) -> bool[len(labels)]    the labels of a category field that a series takes
    selected_idx(idx, codes=None, keep=None) -> idx of the cells taken (what `cells_of` is given)
    as_numbers(idx), as_column(values, n, dtype, name), as_keep(keep), as_codes(codes, keep, n) — the columns
        of a model checked (a ValueError — a mistake of the caller)

A point is inside a cell with its faces (`FACE_TOL` = 1e−6 m, as BF): on a face between two cells either may
hold it, so up to 2 × 2 × 2 cells are looked at. The cell of a coordinate comes from a table of equal slots of
the axis (the cells themselves for a regular step, 8 slots per cell for the variable step of OMF tensors) and
is then moved by the boundaries themselves (`blocks.schema.edges` — the centres of the model come from the same
numbers); near a face and beyond the grid — a binary search in the boundaries.

The lookup of a cell among the occupied ones is a binary search in `idx` (ascending) or, for many points, a
bit map of the occupied cells (cells / 8 bytes: ≤ 25 MB at the limit of 200 million cells, built once per
`Cells`) — the same answer, about ten times faster on 5 million samples against 20 million cells. `idx` may be
uint64 (the Parquet column), uint32 (the cache of columns) or int64. Blocks of 2**20 rows, `tick` between them.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np

from bf_core.jit import njit

from .weighted import StatsError, Tick

FACE_TOL = 1e-6  # m: a point this near a face of a cell is on it (BF `points_in_blocks`)
BLOCK = 1 << 20  # rows between two calls of `tick`
SEARCH_SHARE = 64  # fewer points than cells / 64 — the binary search, otherwise the bit map
SLOTS = 8  # slots of the table of a variable axis per cell
NO_ROWS = np.zeros(0, dtype=np.int64)


def as_points(points: Any) -> np.ndarray:
    p = np.ascontiguousarray(points, dtype=np.float64)
    if p.ndim != 2 or p.shape[1] != 3:
        raise ValueError("points are an n×3 array of x, y, z")
    return p


# --- the columns of a model and the selection by categories -----------------------------------------------------


def as_numbers(idx: Any) -> np.ndarray:
    """The column `idx` of a model: 1D, integer (uint64 of the file, uint32 of the cache of columns, int64)."""
    numbers = np.ascontiguousarray(idx)
    if numbers.ndim != 1 or numbers.dtype.kind not in "iu":
        raise ValueError("idx is a 1D integer array")
    return numbers


def as_column(values: Any, n: int, dtype: Any, name: str) -> np.ndarray:
    out = np.ascontiguousarray(values, dtype=dtype)
    if out.shape != (n,):
        raise ValueError(f"{name} must hold a value for every row of idx")
    return out


def as_keep(keep: Any) -> np.ndarray | None:
    return None if keep is None else np.ascontiguousarray(keep, dtype=np.bool_).reshape(-1)


def as_codes(codes: Any, keep: np.ndarray | None, n: int) -> np.ndarray | None:
    """The column of a selection (int32 codes of the labels, −1 — empty); with `keep` and without, the two go
    together."""
    if (codes is None) != (keep is None):
        raise ValueError("a selection is the column `codes` and the labels `keep` together")
    return None if codes is None else as_column(codes, n, np.int32, "codes")


def keep_of(labels: Sequence[str], chosen: Iterable[str]) -> np.ndarray:
    """Which labels of a category field (`meta.fields[…].labels`) the series takes: bool by the code of the
    label. A chosen label that the field does not have (any more) takes nothing."""
    names = set(chosen)
    return np.array([label in names for label in labels], dtype=np.bool_)


def selected_idx(idx: Any, codes: Any = None, keep: Any = None) -> np.ndarray:
    """The numbers of the cells that the selection takes, in the type, the order and read-only as `idx` (all
    of them without a selection) — the cells of the series for `cells_of`. A cell without a category (−1, or a code
    beyond the labels) is not taken."""
    numbers = as_numbers(idx)
    flags = as_keep(keep)
    cd = as_codes(codes, flags, int(numbers.shape[0]))
    if cd is None or flags is None:
        return numbers
    parts = []
    for a in range(0, int(numbers.shape[0]), BLOCK):
        part = cd[a : a + BLOCK]
        ok = (part >= 0) & (part < flags.size)
        ok[ok] = flags[part[ok]]
        parts.append(numbers[a : a + BLOCK][ok])
    if not parts:
        return numbers
    out = np.concatenate(parts)
    if not numbers.flags.writeable:  # as the column: one specialization of `_in_cells`, `_mark` for the two
        out.setflags(write=False)
    return out


# --- the lookup of a point among the cells ----------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _axis(x, e, tol):  # type: ignore[no-untyped-def]
    """The cells of one axis that hold x with their faces: (the first, how many); (0, 0) — beyond the grid.
    `e` — the boundaries of the cells."""
    n = e.shape[0] - 1
    if not (x >= e[0] - tol and x <= e[n] + tol):  # NaN too
        return 0, 0
    lo = 0  # the last boundary that is not above x: the cell of x
    hi = n
    while hi - lo > 1:
        mid = (lo + hi) >> 1
        if e[mid] <= x:
            lo = mid
        else:
            hi = mid
    first = lo
    count = 1
    if lo > 0 and x - e[lo] <= tol:
        first = lo - 1
        count = 2
    if lo < n - 1 and e[lo + 1] - x <= tol:
        count += 1
    return first, count


@njit(cache=True, nogil=True)
def _mark(idx, cells, bits):  # type: ignore[no-untyped-def]
    """The bit of every occupied cell."""
    for r in range(idx.shape[0]):
        c = np.int64(idx[r])
        if c >= 0 and c < cells:
            bits[c >> 3] |= np.uint8(1 << (c & 7))


@njit(cache=True, nogil=True)
def _find(cell, idx):  # type: ignore[no-untyped-def]
    """Is the cell among the occupied ones: a binary search in `idx`."""
    lo = 0
    hi = idx.shape[0]
    while lo < hi:
        mid = (lo + hi) >> 1
        if np.int64(idx[mid]) < cell:
            lo = mid + 1
        else:
            hi = mid
    return lo < idx.shape[0] and np.int64(idx[lo]) == cell


@njit(cache=True, nogil=True)
def _near_faces(x, y, z, ex, ey, ez, idx, bits, use_bits, tol):  # type: ignore[no-untyped-def]
    """A point near a face of a cell or beyond the grid: every cell that holds it with its faces."""
    fx, cx = _axis(x, ex, tol)
    if cx == 0:
        return False
    fy, cy = _axis(y, ey, tol)
    if cy == 0:
        return False
    fz, cz = _axis(z, ez, tol)
    if cz == 0:
        return False
    nx = ex.shape[0] - 1
    ny = ey.shape[0] - 1
    for kk in range(fz, fz + cz):
        for jj in range(fy, fy + cy):
            for ii in range(fx, fx + cx):
                cell = ii + nx * (jj + ny * kk)
                if use_bits:
                    if ((bits[cell >> 3] >> (cell & 7)) & 1) == 1:
                        return True
                elif _find(cell, idx):
                    return True
    return False


@njit(cache=True, nogil=True)
def _in_cells(  # type: ignore[no-untyped-def]
    p, rows, has_rows, k, has_k, ex, ey, ez, tx, ty, tz, inv, idx, bits, use_bits, tol, a, b, out
):
    """Rows a … b − 1: is the point inside an occupied cell (rows with k < 0 are not looked at). The cell of
    a coordinate: its slot of the table of the axis, moved up by the boundaries; a point away from the faces
    of its cell — the usual case — is looked up at once."""
    nx = ex.shape[0] - 1
    ny = ey.shape[0] - 1
    nz = ez.shape[0] - 1
    mx = tx.shape[0]
    my = ty.shape[0]
    mz = tz.shape[0]
    x0 = ex[0] - tol
    x1 = ex[nx] + tol
    y0 = ey[0] - tol
    y1 = ey[ny] + tol
    z0 = ez[0] - tol
    z1 = ez[nz] + tol
    for r in range(a, b):
        if has_k and k[r] < 0:
            out[r] = False
            continue
        i = rows[r] if has_rows else r
        x = p[i, 0]
        y = p[i, 1]
        z = p[i, 2]
        if not (x >= x0 and x <= x1 and y >= y0 and y <= y1 and z >= z0 and z <= z1):  # beyond the grid, NaN
            out[r] = False
            continue
        qx = (x - ex[0]) * inv[0]
        qy = (y - ey[0]) * inv[1]
        qz = (z - ez[0]) * inv[2]
        if qx > 0.0 and qx < mx and qy > 0.0 and qy < my and qz > 0.0 and qz < mz:
            ci = tx[int(qx)]
            while ci < nx - 1 and x >= ex[ci + 1]:
                ci += 1
            cj = ty[int(qy)]
            while cj < ny - 1 and y >= ey[cj + 1]:
                cj += 1
            ck = tz[int(qz)]
            while ck < nz - 1 and z >= ez[ck + 1]:
                ck += 1
            if (
                x - ex[ci] > tol
                and ex[ci + 1] - x > tol
                and y - ey[cj] > tol
                and ey[cj + 1] - y > tol
                and z - ez[ck] > tol
                and ez[ck + 1] - z > tol
            ):
                cell = ci + nx * (cj + ny * ck)
                if use_bits:
                    out[r] = ((bits[cell >> 3] >> (cell & 7)) & 1) == 1
                else:
                    out[r] = _find(cell, idx)
                continue
        out[r] = _near_faces(x, y, z, ex, ey, ez, idx, bits, use_bits, tol)


def _slots(e: np.ndarray, regular: bool) -> tuple[np.ndarray, float]:
    """The table of an axis: the cell that holds the start of every slot (equal parts of the axis: the cells
    themselves for a regular step, `SLOTS` per cell for a variable one) and the number of slots per metre."""
    n = int(e.shape[0]) - 1
    m = n if regular else SLOTS * n
    span = float(e[n] - e[0])
    if regular:
        return np.arange(n, dtype=np.int64), m / span
    starts = e[0] + np.arange(m, dtype=np.float64) * (span / m)
    return np.clip(np.searchsorted(e, starts, side="right") - 1, 0, n - 1).astype(np.int64), m / span


class Cells:
    """The occupied cells of a model series: the boundaries of the cells of the grid along X, Y, Z and the
    numbers `idx` (i + nx·(j + ny·k), ascending) of the cells that are in the series after its selection."""

    __slots__ = ("edges", "tables", "inv", "idx", "cells", "_bits")

    def __init__(self, edges: tuple[np.ndarray, ...], regular: bool, idx: np.ndarray):
        self.edges, self.idx = edges, idx
        slots = [_slots(e, regular) for e in edges]
        self.tables = tuple(t for t, _ in slots)
        self.inv = np.array([per_metre for _, per_metre in slots])
        self.cells = math.prod(int(e.shape[0]) - 1 for e in edges)
        self._bits: np.ndarray | None = None

    def bits(self) -> np.ndarray:
        """The bit map of the occupied cells, built once."""
        if self._bits is None:
            bits = np.zeros(self.cells // 8 + 1, dtype=np.uint8)
            _mark(self.idx, self.cells, bits)
            self._bits = bits
        return self._bits


def cells_of(grid: Mapping[str, Any], idx: Any) -> Cells:
    """The cells `idx` (an integer array, ascending — as the column `idx` of the model or a part of it) of
    the grid `meta.grid` (blocks/schema.py)."""
    from bf_core.blocks import schema

    edges = tuple(np.asarray(schema.edges(grid, a), dtype=np.float64) for a in range(3))
    numbers = np.ascontiguousarray(idx)
    if numbers.ndim != 1 or numbers.dtype.kind not in "iu":
        raise ValueError("idx is a 1D integer array")
    cells = Cells(edges, "tensors" not in grid, numbers)
    if numbers.shape[0] and (int(numbers[0]) < 0 or int(numbers[-1]) >= cells.cells):
        raise StatsError(
            "bad_param",
            "Номера ячеек модели не соответствуют её сетке.",
            "Обновите модель: её ячейки и сетка записаны несогласованно.",
            param="within_series",
        )
    return cells


def inside_cells(
    p: np.ndarray, rows: np.ndarray, has_rows: bool, k: np.ndarray | None, cells: Cells, method: str | None,
    tick: Tick | None, lo: float = 0.0, hi: float = 1.0,
) -> np.ndarray:  # fmt: skip
    """Is the point of every row (of `rows`, or of `p` itself) inside an occupied cell; rows with k < 0 are
    not looked at. `tick` gets lo … hi."""
    n = int(rows.shape[0]) if has_rows else int(p.shape[0])
    out = np.zeros(n, dtype=np.bool_)
    if method is None:
        method = "search" if n * SEARCH_SHARE < int(cells.idx.shape[0]) else "bits"
    use_bits = method == "bits"
    bits = cells.bits() if use_bits else np.zeros(1, dtype=np.uint8)
    has_k = k is not None
    kk = k if k is not None else np.zeros(0, dtype=np.int32)
    ex, ey, ez = cells.edges
    tx, ty, tz = cells.tables
    inv, idx = cells.inv, cells.idx
    for a in range(0, n, BLOCK):
        b = min(a + BLOCK, n)
        _in_cells(p, rows, has_rows, kk, has_k, ex, ey, ez, tx, ty, tz, inv, idx, bits, use_bits, FACE_TOL, a, b, out)
        if tick is not None:
            tick(lo + (hi - lo) * b / n)
    return out


def points_in_cells(points: Any, cells: Cells, *, method: str | None = None, tick: Tick | None = None) -> np.ndarray:
    """True for every point inside an occupied cell (its faces included, 1e−6 m). `method`: "search" — the
    binary search in `idx`, "bits" — the bit map; None — by the numbers of points and cells."""
    if method not in (None, "search", "bits"):
        raise ValueError("method is 'search', 'bits' or None")
    return inside_cells(as_points(points), NO_ROWS, False, None, cells, method, tick)
