"""The shell of a block model by faces: what can be seen from outside (ТЗ 8.7.6, ADR 0023 §11, decision 16).
Ported from BF `blocks/shell.py` (`exposed_mask` — blocks → faces, sub-blocks of any aligned box, a variable step).

    f = exposed(grid, p, s, w, passed, klass, shell=True, threads=1, tick=None) -> Faces
    m = face_mask(grid, p, s, w, passed, shell=True, threads=1, tick=None) -> uint8 [n]    bit 2·axis + side
    n = count_faces(m) -> int                  (LOD, 17.23: how many faces there will be, before they are built)

Input — the rows of a model as `Model.iter` gives them: `p` uint64 (P = i + nx·(j + ny·k)), `s`, `w` uint8 n×3
(offset and size in sub-cells; None — a model without sub-blocks, s = 0, w = div), `passed` bool [n] (the mask of
the filter / restrictor — a row that does not pass is not drawn and does not hide anything), `klass` uint16 [n]
(the class of a row for the colour, 17.23). Rows in any order; the faces name them by `row` of the input.

The rule «full neighbour» (BF, ADR 0023 §11). A parent is FULL when its rows cover it exactly (Σ of the volumes of
the sub-blocks = the volume of the parent: a whole parent is one row) and every one of them passes. A face of a
passed block is HIDDEN when the neighbour behind it is full:
  · the face lies inside its parent — the neighbour is the parent itself: hidden when the parent is full;
  · the face lies on the boundary of its parent — the neighbour is the parent across it (any level of sub-blocks
    on either side — a full parent closes a face of a block of any size): hidden when that parent is full;
    behind the grid there is nothing, the face is shown.
Everything else is shown: extra faces are drawn, a visible face is never lost. A parent that is not full (the
boundary of a body, a filter that cut a part of it, a hole) shows all its faces, also those between two of its
own passed sub-blocks. Overlapping rows make the volume of a parent more than whole: such a parent is not full.
`shell=False` — «all blocks»: the six faces of every passed block.

A face: axis a (0 X, 1 Y, 2 Z), side 0 — normal −a, 1 — +a, `a` = 2·axis + side; the plane axes u = (a + 1) mod 3,
v = (a + 2) mod 3. Its place is given twice: whole numbers `g` = (plane, u0, u1, v0, v1) — in sub-cells of the
whole grid, counted from the corner (i·div + s …) — and float64 `c`, `h` — the centre and the half sizes in the
local axes. Every coordinate is a function of one whole number (g / div · d, or the edge of a variable step), so
two faces that touch have equal doubles and equal integers: the merge of 17.23 can compare either. The faces go
by (a, plane, class, v, u) — the order of the merge.

The same bits with any number of threads (rows are independent, the strips are fixed — `composites.strips`);
the kernels are numba `cache=True, nogil=True` (the cache — `bf_core.paths`), float64 where there is geometry.
Errors — `block_model_invalid` 422 {code, message, hint} in Russian.

The bitmap of full parents is used while nx·ny·nz ≤ BITMAP_CELLS (a bit a cell: 62 MB at 5·10⁸), above — a
binary search over the sorted P of full parents; the answers are the same.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.blocks.schema import GridV2, bad_model
from bf_core.composites import strips
from bf_core.composites.strips import Tick
from bf_core.jit import njit

BITMAP_CELLS = 500_000_000  # ADR 0023 §11: a bitmap of parents up to 5·10⁸ cells, then a search by P
STRIP_ROWS = 1 << 18  # rows of a strip: fixed, so the result does not depend on the number of threads
UV = ((1, 2), (2, 0), (0, 1))  # plane axes (u, v) of a face by the axis a
_POP = np.array([bin(i).count("1") for i in range(64)], dtype=np.int64)
ALL_FACES = 63


@dataclass(frozen=True)
class Faces:
    """Faces of the shell, sorted by (a, plane, k, v, u); every array has m rows (module docstring)."""

    c: np.ndarray  # float64 [m, 3] centre of the face, local axes
    h: np.ndarray  # float64 [m, 2] half sizes along u and v
    a: np.ndarray  # uint8 [m] 2·axis + side
    k: np.ndarray  # uint16 [m] class of the block
    row: np.ndarray  # int64 [m] row of the input the face belongs to
    g: np.ndarray  # int64 [m, 5] plane, u0, u1, v0, v1 — whole sub-cells from the corner of the grid

    def __len__(self) -> int:
        return int(self.a.shape[0])


@njit(cache=True, nogil=True)
def _parents(p, s, w, has_sub, passed, div, cells, rowfull):  # type: ignore[no-untyped-def]
    """Marks the rows of full parents; returns 0 or a code of the first fault (1 P out of the grid, 2 rows not by
    P, 3 a sub-block off its parent)."""
    n = p.shape[0]
    total = div[0] * div[1] * div[2]
    bad = 0
    prev = np.int64(-1)
    r = 0
    while r < n:
        P = np.int64(p[r])
        if P >= cells and bad == 0:
            bad = 1
        if P < prev and bad == 0:
            bad = 2
        prev = P
        e = r
        vol = np.int64(0)
        allp = True
        while e < n and np.int64(p[e]) == P:
            if has_sub:
                vol_e = np.int64(1)
                for a in range(3):
                    wa = np.int64(w[e, a])
                    sa = np.int64(s[e, a])
                    if wa < 1 or sa + wa > div[a]:
                        if bad == 0:
                            bad = 3
                    vol_e *= wa
                vol += vol_e
            else:
                vol += total
            if not passed[e]:
                allp = False
            e += 1
        full = 1 if (allp and vol == total) else 0
        for t in range(r, e):
            rowfull[t] = full
        r = e
    return bad


@njit(cache=True, nogil=True)
def _collect(p, rowfull, bits, fullp, use_bits):  # type: ignore[no-untyped-def]
    """The P of full parents (once each): into the bitmap, or — a sorted list; returns how many."""
    n = p.shape[0]
    m = 0
    for r in range(n):
        if rowfull[r] == 0 or (r > 0 and p[r - 1] == p[r]):
            continue
        P = np.int64(p[r])
        if use_bits:
            bits[P >> 3] |= np.uint8(1 << (P & 7))
        else:
            fullp[m] = p[r]
        m += 1
    return m


@njit(cache=True, nogil=True)
def _is_full(P, bits, fullp, nfull, use_bits):  # type: ignore[no-untyped-def]
    if use_bits:
        return (bits[P >> 3] >> (P & 7)) & 1 == 1
    lo = 0
    hi = nfull
    while lo < hi:  # the first element ≥ P
        mid = (lo + hi) >> 1
        if np.int64(fullp[mid]) < P:
            lo = mid + 1
        else:
            hi = mid
    return lo < nfull and np.int64(fullp[lo]) == P


@njit(cache=True, nogil=True)
def _mask(  # type: ignore[no-untyped-def]
    lo, hi, p, s, w, has_sub, passed, rowfull, counts, div, bits, fullp, nfull, use_bits, out
):
    nx = counts[0]
    ny = counts[1]
    nz = counts[2]
    stride = (np.int64(1), nx, nx * ny)
    for r in range(lo, hi):
        if not passed[r]:
            out[r] = 0
            continue
        P = np.int64(p[r])
        ijk = (P % nx, (P // nx) % ny, P // (nx * ny))
        lim = (nx, ny, nz)
        bits6 = 0
        for a in range(3):
            if has_sub:
                sa = np.int64(s[r, a])
                wa = np.int64(w[r, a])
            else:
                sa = np.int64(0)
                wa = div[a]
            for side in range(2):
                if side == 0:
                    inside = sa > 0
                    nb = ijk[a] - 1
                else:
                    inside = sa + wa < div[a]
                    nb = ijk[a] + 1
                if inside:
                    show = rowfull[r] == 0
                elif nb < 0 or nb >= lim[a]:
                    show = True
                else:
                    q = P + stride[a] if side == 1 else P - stride[a]
                    show = not _is_full(q, bits, fullp, nfull, use_bits)
                if show:
                    bits6 |= 1 << (2 * a + side)
        out[r] = bits6
    return 0


@njit(cache=True, nogil=True)
def _starts(mask, pop, starts):  # type: ignore[no-untyped-def]
    """starts[r] = faces before row r (a running sum of the popcounts); returns the total."""
    total = np.int64(0)
    for r in range(mask.shape[0]):
        starts[r] = total
        total += pop[mask[r]]
    starts[mask.shape[0]] = total
    return total


@njit(cache=True, nogil=True)
def _coord(a, g, tens, edges, size, div):  # type: ignore[no-untyped-def]
    """The local coordinate of the sub-cell boundary g on the axis a: one function of one whole number."""
    if tens:
        return edges[a, g]
    return (np.float64(g) / np.float64(div[a])) * size[a]


@njit(cache=True, nogil=True)
def _fill(  # type: ignore[no-untyped-def]
    lo, hi, p, s, w, has_sub, mask, klass, starts, counts, div, tens, edges, size, g, ac, row, kk, c, h
):
    nx = counts[0]
    ny = counts[1]
    for r in range(lo, hi):
        m = mask[r]
        if m == 0:
            continue
        P = np.int64(p[r])
        ijk = (P % nx, (P // nx) % ny, P // (nx * ny))
        g0 = np.zeros(3, np.int64)
        g1 = np.zeros(3, np.int64)
        for a in range(3):
            if has_sub:
                g0[a] = ijk[a] * div[a] + np.int64(s[r, a])
                g1[a] = g0[a] + np.int64(w[r, a])
            else:
                g0[a] = ijk[a] * div[a]
                g1[a] = g0[a] + div[a]
        o = starts[r]
        for b in range(6):
            if (m >> b) & 1 == 0:
                continue
            a = b >> 1
            u = (a + 1) % 3
            v = (a + 2) % 3
            plane = g1[a] if (b & 1) else g0[a]
            g[o, 0] = plane
            g[o, 1] = g0[u]
            g[o, 2] = g1[u]
            g[o, 3] = g0[v]
            g[o, 4] = g1[v]
            ac[o] = b
            row[o] = r
            kk[o] = klass[r]
            c[o, a] = _coord(a, plane, tens, edges, size, div)
            ulo = _coord(u, g0[u], tens, edges, size, div)
            uhi = _coord(u, g1[u], tens, edges, size, div)
            vlo = _coord(v, g0[v], tens, edges, size, div)
            vhi = _coord(v, g1[v], tens, edges, size, div)
            c[o, u] = (ulo + uhi) / 2
            c[o, v] = (vlo + vhi) / 2
            h[o, 0] = (uhi - ulo) / 2
            h[o, 1] = (vhi - vlo) / 2
            o += 1
    return 0


def _fault(code: int) -> Exception:
    what = {
        1: ("Блок за пределами сетки модели.", "Проверьте сетку: номер родителя должен быть меньше числа ячеек."),
        2: ("Строки модели идут не по возрастанию номера родителя.", "Модель повреждена: импортируйте её заново."),
        3: ("Субблок выходит за свой родительский блок.", "Модель повреждена: невыровненные субблоки не допускаются."),
    }[code]
    return bad_model(*what)


def _rows(grid: GridV2, p: Any, s: Any, w: Any, passed: Any) -> tuple[Any, ...]:
    pp = np.ascontiguousarray(p, dtype=np.uint64).reshape(-1)
    n = pp.shape[0]
    ok = np.ascontiguousarray(passed, dtype=np.bool_).reshape(-1)
    if ok.shape[0] != n:
        raise bad_model("Маска строк не совпадает с числом строк модели.", "Передайте по одному значению на строку.")
    has_sub = s is not None and w is not None
    if has_sub:
        ss = np.ascontiguousarray(s, dtype=np.uint8).reshape(-1, 3)
        ww = np.ascontiguousarray(w, dtype=np.uint8).reshape(-1, 3)
        if ss.shape[0] != n or ww.shape[0] != n:
            raise bad_model("Субблоки не совпадают с числом строк модели.", "Передайте s и w по строке на строку.")
    else:
        ss = ww = np.zeros((0, 3), dtype=np.uint8)
    order = None
    if n > 1 and not bool((pp[1:] >= pp[:-1]).all()):  # rows go by P in a stored model; a caller's may not
        order = np.argsort(pp, kind="stable")
        pp, ok = pp[order], ok[order]
        if has_sub:
            ss, ww = ss[order], ww[order]
    return pp, ss, ww, has_sub, ok, order


def face_mask(
    grid: GridV2,
    p: Any,
    s: Any,
    w: Any,
    passed: Any,
    *,
    shell: bool = True,
    threads: int = 1,
    tick: Tick | None = None,
) -> np.ndarray:
    """uint8 [n] per row of the input: bit 2·axis + side is set when that face is shown (module docstring)."""
    mask, _ = _mask_sorted(grid, p, s, w, passed, shell, threads, tick)
    return mask


def count_faces(mask: Any) -> int:
    """The number of faces of a `face_mask`."""
    return int(_POP[np.asarray(mask, dtype=np.uint8)].sum())


def _mask_sorted(
    grid: GridV2, p: Any, s: Any, w: Any, passed: Any, shell: bool, threads: int, tick: Tick | None
) -> tuple[np.ndarray, tuple[Any, ...]]:
    """(the mask in the order of the input, the rows as the kernels want them — by P)."""
    rows = _rows(grid, p, s, w, passed)
    pp, ss, ww, has_sub, ok, order = rows
    n = pp.shape[0]
    mask = np.zeros(n, dtype=np.uint8)
    if n:
        div = np.asarray(grid.sub.div, dtype=np.int64)
        counts = np.asarray(grid.counts, dtype=np.int64)
        if shell:
            rowfull = np.zeros(n, dtype=np.uint8)
            bad = _parents(pp, ss, ww, has_sub, ok, div, grid.cells, rowfull)
            if bad:
                raise _fault(int(bad))
            use_bits = grid.cells <= BITMAP_CELLS
            bits = np.zeros((grid.cells + 7) // 8 if use_bits else 0, dtype=np.uint8)
            fullp = np.zeros(0 if use_bits else int(rowfull.sum() or 0), dtype=np.uint64)
            nfull = int(_collect(pp, rowfull, bits, fullp, use_bits))
            strips.run(lambda lo, hi: _mask(lo, hi, pp, ss, ww, has_sub, ok, rowfull, counts, div, bits, fullp,
                                            nfull, use_bits, mask),
                       n, threads=threads, tick=tick, size=STRIP_ROWS)  # fmt: skip
        else:
            bad = _parents(pp, ss, ww, has_sub, ok, div, grid.cells, np.zeros(n, dtype=np.uint8))
            if bad:
                raise _fault(int(bad))
            mask[ok] = ALL_FACES
    if order is not None:
        back = np.empty_like(mask)
        back[order] = mask
        return back, rows
    return mask, rows


def _axes(grid: GridV2) -> tuple[bool, np.ndarray, np.ndarray]:
    """(variable step?, edges [3, max + 1] of a variable step, sizes of the regular step) for `_coord`."""
    if grid.tensors is not None:
        top = max(grid.counts) + 1
        edges = np.zeros((3, top), dtype=np.float64)
        for a in range(3):
            edges[a, 1 : grid.counts[a] + 1] = np.cumsum(np.asarray(grid.tensors[a], dtype=np.float64))
        return True, edges, np.zeros(3, dtype=np.float64)
    return False, np.zeros((3, 1), dtype=np.float64), np.asarray(grid.regular(), dtype=np.float64)


def _sort_key(grid: GridV2, f_a: np.ndarray, g: np.ndarray, k: np.ndarray) -> np.ndarray:
    """Positions that put the faces by (a, plane, k, v, u): one word when the grid is up to 32 767 sub-cells a
    side (3 + 3·15 + 16 bits), else the keys of `lexsort`."""
    top = max(c * d for c, d in zip(grid.counts, grid.sub.div, strict=True)) + 1
    bits = int(top).bit_length()
    u64 = np.uint64
    plane, v0, u0 = g[:, 0].astype(u64), g[:, 3].astype(u64), g[:, 1].astype(u64)
    if bits <= 15:
        key = (f_a.astype(u64) << u64(bits)) | plane
        key = (key << u64(16)) | k.astype(u64)
        key = (key << u64(bits)) | v0
        key = (key << u64(bits)) | u0
        return np.argsort(key)
    head = (f_a.astype(u64) << u64(bits + 16)) | (plane << u64(16)) | k.astype(u64)  # ≤ 3 + 38 + 16 bits
    if bits <= 32:  # v and u in one word
        return np.lexsort(((v0 << u64(32)) | u0, head))
    return np.lexsort((u0, v0, head))


def exposed(
    grid: GridV2,
    p: Any,
    s: Any,
    w: Any,
    passed: Any,
    klass: Any = None,
    *,
    shell: bool = True,
    threads: int = 1,
    tick: Tick | None = None,
) -> Faces:
    """The faces of the blocks that pass and can be seen from outside (module docstring)."""
    mask, rows = _mask_sorted(grid, p, s, w, passed, shell, threads, tick)
    pp, ss, ww, has_sub, _ok, order = rows
    n = pp.shape[0]
    cls = np.zeros(n, dtype=np.uint16) if klass is None else np.ascontiguousarray(klass, dtype=np.uint16).reshape(-1)
    if cls.shape[0] != n:
        raise bad_model("Классы строк не совпадают с числом строк модели.", "Передайте по одному классу на строку.")
    m_sorted = mask if order is None else mask[order]  # the kernels work on the rows by P
    cls_sorted = cls if order is None else cls[order]
    starts = np.empty(n + 1, dtype=np.int64)
    m = int(_starts(m_sorted, _POP, starts))
    g = np.empty((m, 5), dtype=np.int64)
    ac = np.empty(m, dtype=np.uint8)
    row = np.empty(m, dtype=np.int64)
    cls_rows = np.empty(m, dtype=np.uint16)
    c = np.empty((m, 3), dtype=np.float64)
    h = np.empty((m, 2), dtype=np.float64)
    if m:
        div = np.asarray(grid.sub.div, dtype=np.int64)
        counts = np.asarray(grid.counts, dtype=np.int64)
        tens, edges, size = _axes(grid)
        strips.run(lambda lo, hi: _fill(lo, hi, pp, ss, ww, has_sub, m_sorted, cls_sorted, starts, counts, div,
                                        tens, edges, size, g, ac, row, cls_rows, c, h),
                   n, threads=threads, tick=tick, size=STRIP_ROWS)  # fmt: skip
        if order is not None:
            row = order[row]
        pos = _sort_key(grid, ac, g, cls_rows)
        c, h, ac, cls_rows, row, g = c[pos], h[pos], ac[pos], cls_rows[pos], row[pos], g[pos]
    return Faces(c=c, h=h, a=ac, k=cls_rows, row=row, g=g)


def faces_of_block(grid: GridV2, p: int, s: Sequence[int] | None = None, w: Sequence[int] | None = None) -> Faces:
    """The six faces of one block (a helper for tests and for the pick of 17.24): no neighbours looked at."""
    ss = None if s is None else np.asarray([s], dtype=np.uint8)
    ww = None if w is None else np.asarray([w], dtype=np.uint8)
    return exposed(grid, np.asarray([p], dtype=np.uint64), ss, ww, np.ones(1, dtype=bool), shell=False)
