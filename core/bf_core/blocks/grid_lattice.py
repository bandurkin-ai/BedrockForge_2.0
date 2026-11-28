"""The lattice of the sub-cells of a parent block (ТЗ 7.31, 8.7.2; ADR 0030 decisions 1–4; Д-45 А6).

A model of Micromine or Datamine may divide a parent by any whole number along an axis — 10 m by 5 (2 m), by 3
(3.333… m), by 2 and 3 together (6 sub-cells of 1.667 m) — not only by 2^L. The division `div` of an axis is the lowest
common multiple of the denominators of the fractions of the parent that occur in the file: the sizes of the blocks and
the offsets of their low edges inside their parents (lcm(2, 3) = 6; 2^L is the special case). `div` ≤ 128 (the format
of the rows holds s, w < 128; `geometry.KEY_SHIFT`).

* `PairSet` — the distinct (low edge, size) pairs of one axis with their counts, collected while the file is read
  (one hash table, a numba kernel: a chunk of a million rows costs milliseconds, no sort); at most 1 000 000 pairs,
  more — `overflow`, the axis is then left without a lattice (the rows are refused later with their reason);
* `denominators` — the smallest q ≤ 128 at which a value is a whole number of d/q, within the tolerance;
* `lattice_of`, `fit_axis` — the division an axis needs and how many blocks lie on it, cross the border of their
  parent, lie before the corner;
* `choose_axis` — the parent and the corner of an axis: the hint, else the biggest block; if the blocks do not lie
  with it — 2×, 3×, 4× the biggest block, and the corner by the modes of the residues of the whole blocks;
* `column_tol` — the tolerance of a column by its type (a number written as text with d decimals is d-rounded:
  3.33 for 10/3).

The tolerance at the denominator q is at most ¼ of the sub-cell d/q: a bigger quantum never pulls a block into a
neighbouring cell. Float64 throughout; an edge is kept in units of 1e-9 m in the table (≪ the 1e-6 m floor).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from numba import njit

__all__ = [
    "FLOOR",
    "MAX_DIV",
    "MAX_PAIRS",
    "UNIT",
    "AxisFit",
    "AxisPick",
    "PairSet",
    "choose_axis",
    "column_tol",
    "common_div",
    "denominators",
    "edge_tol",
    "fit_axis",
    "lattice_of",
]

MAX_DIV = 128  # sub-cells of a parent along an axis
MAX_PAIRS = 1_000_000  # distinct (low edge, size) pairs of an axis
MAX_VALUES = 65_536  # distinct fractions examined per axis
MAX_K = 4  # the parent is up to 4 × the biggest block
MAX_MODES = 6  # corners tried per parent
FLOOR = 1e-6  # m: the tolerance of an exact column
UNIT = 1e-9  # m: the unit of an edge in the table
_SCALE = 1.0 / UNIT


# --- the table of (low edge, size) pairs -----------------------------------------------------------------------


@njit(cache=True)
def _put(ka, kb, cnt, mask, a, b, w, used, limit):  # type: ignore[no-untyped-def]
    """One pair into the open-addressing table: (full?, used). A pair of the table is counted `w` times more."""
    h = (np.uint64(a) * np.uint64(11400714819323198485)) ^ (np.uint64(b) * np.uint64(14029467366897019727))
    h ^= h >> np.uint64(31)
    slot = h & mask
    while True:
        c = cnt[slot]
        if c == 0:
            if used >= limit:
                return True, used
            ka[slot] = a
            kb[slot] = b
            cnt[slot] = w
            return False, used + 1
        if ka[slot] == a and kb[slot] == b:
            cnt[slot] = c + w
            return False, used
        slot = (slot + np.uint64(1)) & mask


@njit(cache=True)
def _add_edges(ka, kb, cnt, mask, lo, size, start, used, limit, scale):  # type: ignore[no-untyped-def]
    n = lo.shape[0]
    i = start
    while i < n:
        full, used = _put(ka, kb, cnt, mask, np.int64(np.rint(lo[i] * scale)), np.int64(np.rint(size[i] * scale)),
                          np.int64(1), used, limit)  # fmt: skip
        if full:
            return i, used
        i += 1
    return n, used


@njit(cache=True)
def _add_keys(ka, kb, cnt, mask, a, b, w, used, limit):  # type: ignore[no-untyped-def]
    for i in range(a.shape[0]):
        full, used = _put(ka, kb, cnt, mask, a[i], b[i], w[i], used, limit)
        if full:
            return False, used
    return True, used


class PairSet:
    """The distinct (low edge, size) pairs of one axis and how many blocks have each (units of 1e-9 m). The table
    grows by doubling up to `max_pairs` pairs; past that `overflow` is set and nothing more is added."""

    def __init__(self, max_pairs: int = MAX_PAIRS) -> None:
        self.max_pairs = max_pairs
        self.overflow = False
        self.used = 0
        self._cap = 1 << 12
        self._new(self._cap)

    def _new(self, cap: int) -> None:
        self._cap = cap
        self._ka = np.zeros(cap, dtype=np.int64)
        self._kb = np.zeros(cap, dtype=np.int64)
        self._n = np.zeros(cap, dtype=np.int64)
        self._mask = np.uint64(cap - 1)

    def _grow(self) -> bool:
        if self.used >= self.max_pairs:
            return False
        ka, kb, n = self._ka, self._kb, self._n
        at = n > 0
        a, b, w = ka[at], kb[at], n[at]
        self._new(min(self._cap * 2, 1 << 22))
        _add_keys(self._ka, self._kb, self._n, self._mask, a, b, w, 0, self._limit())
        return True

    def _limit(self) -> int:
        return min(self._cap // 2, self.max_pairs)

    def add(self, lo: np.ndarray, size: np.ndarray) -> None:
        """A chunk: the low edges and the sizes of its blocks along the axis (float64, same length)."""
        if self.overflow or not len(lo):
            return
        lo, size = np.ascontiguousarray(lo, dtype=np.float64), np.ascontiguousarray(size, dtype=np.float64)
        at = 0
        while at < len(lo):
            at, self.used = _add_edges(self._ka, self._kb, self._n, self._mask, lo, size, at, self.used,
                                       self._limit(), _SCALE)  # fmt: skip
            if at < len(lo) and not self._grow():
                self.overflow = True
                return

    def items(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(low edges, sizes, counts) of the distinct pairs, ascending by low edge then size."""
        at = self._n > 0
        a, b, n = self._ka[at], self._kb[at], self._n[at]
        order = np.lexsort((b, a))
        return a[order] * UNIT, b[order] * UNIT, n[order]


# --- fractions of a parent ---------------------------------------------------------------------------------------


def column_tol(kind: str, decimals: int | None = None, max_abs: float | None = None) -> float:
    """The tolerance of a column, m: R (f8) and a CSV — exact (1e-6); N (text with `decimals` places) — half a unit of
    the last place; F (f4) — half a unit of the last bit at `max_abs`. Never below 1e-6."""
    k = kind.upper()
    if k == "N" and decimals is not None:
        return max(FLOOR, 0.5 * 10.0 ** -max(int(decimals), 0))
    if k == "F" and max_abs is not None and math.isfinite(max_abs):
        return max(FLOOR, float(np.spacing(np.float32(abs(max_abs)))) / 2)
    return FLOOR


def edge_tol(centre: float, size: float = 0.0) -> float:
    """The tolerance of the edges of a block of an axis from the tolerances of its centre column and its size column:
    an edge is centre ∓ size/2, so it is off by the centre's error plus half of the size's. Never below 1e-6."""
    return max(FLOOR, float(centre) + 0.5 * float(size))


def denominators(values: np.ndarray, parent: float, tol: float, qmax: int = MAX_DIV) -> np.ndarray:
    """The smallest q ≤ `qmax` at which every value is a whole number of parent/q, within min(tol, ¼ · parent/q); 0 —
    none (the value is on no lattice of the parent)."""
    v = np.asarray(values, dtype=np.float64).reshape(-1)
    out = np.zeros(len(v), dtype=np.int64)
    todo = np.arange(len(v))
    for q in range(1, qmax + 1):
        if not len(todo):
            break
        x = v[todo] * (q / parent)
        hit = np.abs(x - np.rint(x)) * (parent / q) <= min(tol, 0.25 * parent / q)
        out[todo[hit]] = q
        todo = todo[~hit]
    return out


def common_div(qs: Sequence[int] | np.ndarray) -> int | None:
    """lcm of the denominators; > 128 — None."""
    out = 1
    for q in qs:
        out = out * int(q) // math.gcd(out, int(q))
        if out > MAX_DIV:
            return None
    return out


def lattice_of(
    values: np.ndarray, weights: np.ndarray, parent: float, tol: float, div: int | None = None
) -> tuple[int, np.ndarray]:
    """(div, on) — the division the values need and which of them lie on it. With no `div` the values are taken by
    count, the most frequent first, as long as the lcm of their denominators stays ≤ 128 (so a few odd blocks do not
    spoil the lattice of the rest); with `div` the values are only checked against it."""
    v = np.asarray(values, dtype=np.float64).reshape(-1)
    w = np.asarray(weights).reshape(-1)
    if div is not None:
        x = v * (div / parent)
        return int(div), np.abs(x - np.rint(x)) * (parent / div) <= min(tol, 0.25 * parent / div)
    on = np.zeros(len(v), dtype=bool)
    order = np.argsort(-w, kind="stable")[:MAX_VALUES]
    q = denominators(v[order], parent, tol)
    cur = 1
    for i, qi in zip(order.tolist(), q.tolist(), strict=True):
        if qi == 0:
            continue
        nxt = cur * qi // math.gcd(cur, qi)
        if nxt <= MAX_DIV:
            cur, on[i] = nxt, True
    return cur, on


@dataclass
class AxisFit:
    """How the blocks of an axis lie on a parent `parent` with the corner `origin`: `div` — the division; the blocks
    (counts) `total`, `aligned` (on the lattice, in one parent, after the corner), `before` (before the corner),
    `crosses` (over the border of their parent), `off` (an edge off the lattice)."""

    parent: float
    origin: float
    div: int
    total: int
    aligned: int
    before: int
    crosses: int
    off: int

    @property
    def share(self) -> float:
        return self.aligned / self.total if self.total else 1.0


def fit_axis(
    lo: np.ndarray, size: np.ndarray, n: np.ndarray, *, parent: float, origin: float, tol: float,
    div: int | None = None,
) -> AxisFit:  # fmt: skip
    """The fit of the pairs (low edge, size, count) of an axis to the lattice of `parent` from `origin`."""
    lo, size, n = np.asarray(lo, dtype=np.float64), np.asarray(size, dtype=np.float64), np.asarray(n, dtype=np.int64)
    total = int(n.sum())
    rel = lo - origin
    before = rel < -tol
    rho = np.maximum(rel - np.floor((rel + tol) / parent) * parent, 0.0)
    crosses = ~before & (rho + size > parent + 2 * tol)  # the low edge and the size are each off by up to `tol`
    keep = ~before & ~crosses
    k = np.flatnonzero(keep)
    if not len(k):
        return AxisFit(parent, origin, div or 1, total, 0, int(n[before].sum()), int(n[crosses].sum()), 0)
    values = np.concatenate([np.round(rho[k], 7), np.round(size[k], 7)])
    weights = np.concatenate([n[k], n[k]])
    uniq, inverse = np.unique(values, return_inverse=True)
    wsum = np.bincount(inverse, weights=weights, minlength=len(uniq))
    got, on = lattice_of(uniq, wsum, parent, tol, div)
    both = on[inverse[: len(k)]] & on[inverse[len(k) :]]
    aligned = int(n[k][both].sum())
    return AxisFit(parent, origin, got, total, aligned, int(n[before].sum()), int(n[crosses].sum()),
                   int(n[k][~both].sum()))  # fmt: skip


# --- the parent and the corner of an axis ----------------------------------------------------------------------


@dataclass
class AxisPick:
    """What `choose_axis` took: the fit of the parent, its multiple `k` of the biggest block (1 — the biggest block
    itself or the hint), `complete` — every block lies; `tried` — (parent, origin, share) of the candidates in the
    order tried (for the diagnosis)."""

    fit: AxisFit
    k: int
    complete: bool
    tried: list[tuple[float, float, float]] = field(default_factory=list)

    @property
    def parent(self) -> float:
        return self.fit.parent

    @property
    def origin(self) -> float:
        return self.fit.origin

    @property
    def div(self) -> int:
        return self.fit.div


def _snap(value: float, places: int = 6, eps: float = 1e-8) -> float:
    r = round(value, places)
    return r if abs(r - value) <= eps else value


def _corners(lo: np.ndarray, size: np.ndarray, n: np.ndarray, biggest: float, parent: float, tol: float) -> list[float]:
    """The corners (local axes) a parent can start from, the likeliest first: the residues of the low edges of the
    biggest blocks inside the parent, the most frequent ones; the corner is put at or below the lowest edge."""
    low = float(lo.min())
    whole = size >= biggest - max(tol, FLOOR)
    r = np.mod(lo[whole] - low, parent)
    r[r > parent - tol] = 0.0
    values, inverse = np.unique(np.round(r, 9), return_inverse=True)
    count = np.bincount(inverse, weights=n[whole], minlength=len(values))
    group = np.concatenate([[0], np.cumsum(np.diff(values) > tol)]).astype(np.intp)  # residues within tol: one node
    total = np.bincount(group, weights=count)
    by_group = np.lexsort((values, -count, group))  # per node: its most frequent residue, the lowest on a tie
    first = np.concatenate([[True], np.diff(group[by_group]) > 0])
    heaviest = by_group[first]  # one residue per node, nodes in ascending order
    out: list[float] = []
    for g in np.argsort(-total, kind="stable")[:MAX_MODES]:
        m = float(values[heaviest[g]])
        out.append(_snap(low + m - (parent if m > tol else 0.0)))  # a node of the lattice, at or below the lowest edge
    return out


def choose_axis(
    lo: np.ndarray, size: np.ndarray, n: np.ndarray, *, biggest: float, tol: float, hint_parent: float | None,
    hint_origin: float | None, div: int | None = None,
) -> AxisPick:  # fmt: skip
    """The parent and the corner of an axis from its pairs. The parent: the hint, else k × the biggest block, k = 1, 2,
    3, 4 — the first at which every block lies (no border crossed, none before the corner, all on the lattice); the
    corner: the hint (`hint_origin`, in the local axes), else the likeliest ones by the residues of the biggest blocks.
    None complete — the candidate with the most blocks on it (the first one on a tie)."""
    parents = [hint_parent] if hint_parent is not None else [biggest * k for k in range(1, MAX_K + 1)]
    best: AxisPick | None = None
    tried: list[tuple[float, float, float]] = []
    for k, parent in enumerate(parents, start=1):
        corners = [hint_origin] if hint_origin is not None else _corners(lo, size, n, biggest, parent, tol)
        for origin in corners:
            fit = fit_axis(lo, size, n, parent=parent, origin=origin, tol=tol, div=div)
            tried.append((parent, origin, fit.share))
            pick = AxisPick(fit, k, fit.aligned == fit.total, tried)
            if pick.complete:
                return pick
            if best is None or fit.aligned > best.fit.aligned:
                best = pick
    assert best is not None
    return best
