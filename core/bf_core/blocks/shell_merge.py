"""Merge of the faces of a block shell into big rectangles, and the 256 buckets of the legend (подшаг 17.23;
ADR 0023 §11, decision 16; ТЗ 8.7.6, Т8.7-9; docs/progress/e17-api.md §3).

    m = merge_faces(grid, faces) -> Faces              coplanar neighbours of one class → rectangles
    k = scale_class(values, lo, hi, log=False) -> uint16 [n]     the bucket 0…255 of a value (legend `scale`)
    bins = scale_bins(lo, hi, log=False) -> [[from, to]] × 256   the legend: the range of every bucket
    k = break_class(values, breaks) -> uint16 [n]                the class of `classes`: the number of breaks ≤ v
    bins = break_bins(breaks) -> [[None, b1], [b1, b2], …, [bn, None]]

The merge goes by the whole numbers `g` of `Faces` (plane, u0, u1, v0, v1 — sub-cells of the whole grid), so the
joints are exact: two faces join when they have the same side `a`, the same plane, the same class `k`, the same
extent across and one ends where the other begins. It is greedy and runs twice — first along u (runs of faces
that follow each other in the order of the shell), then along v (the faces re-sorted by (a, plane, k, u0, u1,
v0)) — as ТЗ 8.7.6 says: «прогоны по u, затем по v». Nothing in it depends on the number of threads: the same
bits at any `threads` of `exposed`. The area is kept exactly (whole numbers are added up, nothing is dropped, an
overlapping pair of faces is not joined); classes never mix; the order of the result is that of the shell,
(a, plane, k, v, u). A merged face keeps the `row` of its first (lowest v, u) face; its `c`, `h` are computed
from `g` by the same function as the shell's, so a face that joined nothing keeps its bits.

The bucket of the legend. `scale`: t = (v − from)/(to − from) (`log` — by ln, from > 0), k = clamp(⌊256·t⌋, 0,
255); `log` and v ≤ 0 → 0; no value (NaN) → NO_CLASS (0xFFFF: it is not drawn into the legend and is not given
a value by averaging, a missing value is never turned into 0). `classes`: k = the number of breaks ≤ v.
The kernels are numba `cache=True, nogil=True` (the cache — `bf_core.paths`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from bf_core.blocks.schema import GridV2, bad_model
from bf_core.blocks.shell import Faces, _axes, _coord
from bf_core.jit import njit

NO_CLASS = 0xFFFF  # no value / no category (ADR 0023 §11)
SCALE_BUCKETS = 256


@njit(cache=True, nogil=True)
def _runs(a, k, g, src, chg, s, o, out_g, out_src, out_chg):  # type: ignore[no-untyped-def]
    """Joins the neighbours that follow each other: same a, plane, k and extent across (columns o, o + 1 of g),
    one ends where the next begins along (columns s, s + 1). Writes the survivors (the first face of every run,
    with the extent of the run), the index `src` of that first face and `chg` — 1 when the run joined anything
    (also in an earlier run of the faces it carries); returns how many."""
    m = src.shape[0]
    n = 0
    for i in range(m):
        if n > 0:
            j = n - 1
            q = out_src[j]
            if (
                a[q] == a[src[i]]
                and k[q] == k[src[i]]
                and out_g[j, 0] == g[i, 0]
                and out_g[j, o] == g[i, o]
                and out_g[j, o + 1] == g[i, o + 1]
                and out_g[j, s + 1] == g[i, s]
            ):
                out_g[j, s + 1] = g[i, s + 1]
                out_chg[j] = 1
                continue
        out_src[n] = src[i]
        out_chg[n] = chg[i]
        for t in range(5):
            out_g[n, t] = g[i, t]
        n += 1
    return n


@njit(cache=True, nogil=True)
def _geometry(g, ac, which, tens, edges, size, div, c, h):  # type: ignore[no-untyped-def]
    """c, h of the faces `which` from the whole numbers g: the same function of one whole number as
    `shell._fill`, so a face that joined nothing keeps its bits."""
    for t in range(which.shape[0]):
        o = which[t]
        a = ac[o] >> 1
        u = (a + 1) % 3
        v = (a + 2) % 3
        c[o, a] = _coord(a, g[o, 0], tens, edges, size, div)
        ulo = _coord(u, g[o, 1], tens, edges, size, div)
        uhi = _coord(u, g[o, 2], tens, edges, size, div)
        vlo = _coord(v, g[o, 3], tens, edges, size, div)
        vhi = _coord(v, g[o, 4], tens, edges, size, div)
        c[o, u] = (ulo + uhi) / 2
        c[o, v] = (vlo + vhi) / 2
        h[o, 0] = (uhi - ulo) / 2
        h[o, 1] = (vhi - vlo) / 2
    return 0


def _order_across(grid: GridV2, a: np.ndarray, g: np.ndarray, k: np.ndarray) -> np.ndarray:
    """Positions that put the faces by (a, plane, k, u0, u1, v0): the order of the second run (along v). One
    64-bit word when it fits (the classes present are numbered densely), else the keys of `lexsort`."""
    top = max(c * d for c, d in zip(grid.counts, grid.sub.div, strict=True)) + 1
    bits = int(top).bit_length()
    u64 = np.uint64
    present = np.bincount(k, minlength=65536) > 0
    rank = (np.cumsum(present) - 1).astype(u64)[k]
    kbits = max(1, int(present.sum() - 1).bit_length())
    u0, u1, v0 = g[:, 1].astype(u64), g[:, 2].astype(u64), g[:, 3].astype(u64)
    if 3 + kbits + 4 * bits <= 64:
        key = (a.astype(u64) << u64(kbits + 4 * bits)) | (rank << u64(4 * bits))
        key |= (g[:, 0].astype(u64) << u64(3 * bits)) | (u0 << u64(2 * bits)) | (u1 << u64(bits)) | v0
        return np.argsort(key)
    head = (a.astype(u64) << u64(bits + 16)) | (g[:, 0].astype(u64) << u64(16)) | k.astype(u64)
    return np.lexsort((v0, u1, u0, head))


def _join(a: np.ndarray, k: np.ndarray, g: np.ndarray, src: np.ndarray, chg: np.ndarray, s: int, o: int) -> Any:
    """(g, src, chg) of the survivors of one run of `_runs`; `a`, `k` are those of the ORIGINAL faces, by src."""
    out_g = np.empty_like(g)
    out_src = np.empty_like(src)
    out_chg = np.empty_like(chg)
    n = int(_runs(a, k, g, src, chg, s, o, out_g, out_src, out_chg))
    return out_g[:n], out_src[:n], out_chg[:n]


def merge_faces(grid: GridV2, f: Faces) -> Faces:
    """Merges coplanar neighbours of one class into rectangles (module docstring); the faces must be in the
    order of the shell, (a, plane, k, v, u), as `exposed` and `faces_of_block` give them."""
    m = len(f)
    if m < 2:
        return f
    zero = np.zeros(m, dtype=np.uint8)
    g, src, chg = _join(f.a, f.k, f.g, np.arange(m, dtype=np.int64), zero, 1, 3)  # along u: survivors stay in order
    pos = _order_across(grid, f.a[src], g, f.k[src])
    g, src, chg = _join(f.a, f.k, g[pos], src[pos], chg[pos], 3, 1)  # along v
    if src.shape[0] == m:  # nothing joined
        return f
    # A rectangle has the key (a, plane, k, v0, u0) of its first face, so the order of the shell is the order of
    # the faces they grew from: no sort, the faces that stay are picked by a mask over the input.
    keep = np.zeros(m, dtype=np.bool_)
    keep[src] = True
    out_g = f.g[keep]
    where = np.cumsum(keep, dtype=np.int64) - 1
    ch = np.flatnonzero(chg)
    out_g[where[src[ch]]] = g[ch]
    c, h, ac = f.c[keep], f.h[keep], f.a[keep]
    if ch.shape[0]:
        tens, edges, size = _axes(grid)
        _geometry(out_g, ac, where[src[ch]], tens, edges, size, np.asarray(grid.sub.div, dtype=np.int64), c, h)
    return Faces(c=c, h=h, a=ac, k=f.k[keep], row=f.row[keep], g=out_g)


# --- the legend: 256 buckets of a scale, classes by breaks ---------------------------------------------------------


def _range(lo: float, hi: float, log: bool) -> tuple[float, float]:
    lo, hi = float(lo), float(hi)
    if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
        raise bad_model("Границы шкалы заданы неверно: «от» должно быть меньше «до».",
                        "Задайте конечные границы шкалы или возьмите минимум и максимум поля.")  # fmt: skip
    if log and lo <= 0:
        raise bad_model("Логарифмическая шкала требует положительной нижней границы.",
                        "Задайте границу «от» больше нуля или выключите логарифмическую шкалу.")  # fmt: skip
    return lo, hi


def scale_class(values: Any, lo: float, hi: float, *, log: bool = False) -> np.ndarray:
    """uint16 [n]: the bucket 0…255 of every value (module docstring); NaN → NO_CLASS, `log` and v ≤ 0 → 0."""
    lo, hi = _range(lo, hi, log)
    v = np.asarray(values, dtype=np.float64).reshape(-1)
    nan = np.isnan(v)
    with np.errstate(invalid="ignore", divide="ignore"):
        if log:
            t = (np.log(np.where(v > 0, v, 1.0)) - np.log(lo)) / (np.log(hi) - np.log(lo))
            t = np.where(v > 0, t, 0.0)
        else:
            t = (v - lo) / (hi - lo)
        k = np.clip(np.floor(np.where(nan, 0.0, t) * SCALE_BUCKETS), 0, SCALE_BUCKETS - 1)
    return np.where(nan, NO_CLASS, k).astype(np.uint16)


def scale_bins(lo: float, hi: float, *, log: bool = False) -> list[list[float]]:
    """The legend of `scale`: [[from, to]] of the 256 buckets; the first starts at `lo`, the last ends at `hi`."""
    lo, hi = _range(lo, hi, log)
    i = np.arange(SCALE_BUCKETS + 1, dtype=np.float64) / SCALE_BUCKETS
    e = np.exp(np.log(lo) + (np.log(hi) - np.log(lo)) * i) if log else lo + (hi - lo) * i
    e[0], e[-1] = lo, hi
    return [[float(e[j]), float(e[j + 1])] for j in range(SCALE_BUCKETS)]


def _breaks(breaks: Sequence[float]) -> np.ndarray:
    b = np.asarray(breaks, dtype=np.float64).reshape(-1)
    if not 1 <= b.shape[0] <= 255 or not np.isfinite(b).all() or (np.diff(b) <= 0).any():
        raise bad_model("Границы градаций заданы неверно: нужно от 1 до 255 границ по возрастанию.",
                        "Задайте границы градаций строго по возрастанию.")  # fmt: skip
    return b


def break_class(values: Any, breaks: Sequence[float]) -> np.ndarray:
    """uint16 [n]: the number of `breaks` that are ≤ the value (`classes`); NaN → NO_CLASS."""
    b = _breaks(breaks)
    v = np.asarray(values, dtype=np.float64).reshape(-1)
    k = np.searchsorted(b, np.where(np.isnan(v), 0.0, v), side="right")
    return np.where(np.isnan(v), NO_CLASS, k).astype(np.uint16)


def break_bins(breaks: Sequence[float]) -> list[list[float | None]]:
    """The legend of `classes`: [[None, b1], [b1, b2], …, [bn, None]]."""
    b = [float(x) for x in _breaks(breaks)]
    edges: list[float | None] = [None, *b, None]
    return [[edges[j], edges[j + 1]] for j in range(len(edges) - 1)]
