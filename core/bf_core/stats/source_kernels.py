"""Kernels of `stats.source`: the rows of another table of the set for the rows of a table (ADR 0019 §3; 13.35).

    rows_covering(starts, mid, lo, hi, b_from, b_to) -> (rows, none)   the interval of the other table that
                                                                       holds the middle of a sample
    rows_matching(starts, fa, ta, lo, hi, fb, tb) -> (rows, none)      the row with the same sample key
    hole_ranges(a_holes, b_holes, b_starts) -> lo, hi   the rows of the other table of every hole of the table
    warm()

Both tables are «lines» (`composites.source`): their rows go hole by hole, inside a hole by FROM, so the rows of
a hole of the other table are one stretch `lo[h] … hi[h] − 1` with FROM ascending — a binary search per row,
no sort of the two tables together (5 million samples against 1 million intervals: 0.03 s for 1.1 s of the
`lexsort`). The answers are those of that sort: among the rows that fit the last one in line order is taken.
`rows` — int64, the line position in the other table, −1 — none; `none` — how many −1.

The columns are given to the kernels as read-only views, whatever they are (those of the cache of `stats.source`
are read-only, those of a part of the holes are not; numba compiles a kernel anew for either): one
specialization a kernel, the one `warm()` compiles.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from bf_core.jit import njit


@njit(cache=True, nogil=True)
def cover(starts, mid, lo, hi, b_from, b_to, out):  # type: ignore[no-untyped-def]
    """out[i] — the row of b of the same hole whose [FROM; TO) holds mid[i]: the one that starts last among
    those with FROM ≤ mid (the last in line order when they start together); −1 — none. Returns how many −1."""
    none = 0
    for h in range(starts.shape[0] - 1):
        b0 = lo[h]
        b1 = hi[h]
        for i in range(starts[h], starts[h + 1]):
            x = mid[i]
            left = b0
            right = b1
            while left < right:
                m = (left + right) >> 1
                if b_from[m] <= x:
                    left = m + 1
                else:
                    right = m
            if left > b0 and x < b_to[left - 1]:
                out[i] = left - 1
            else:
                out[i] = -1
                none += 1
    return none


@njit(cache=True, nogil=True)
def match(starts, fa, ta, lo, hi, fb, tb, out):  # type: ignore[no-untyped-def]
    """out[i] — the last row (in line order) of b of the same hole with fb == fa[i] and tb == ta[i] (the keys of
    FROM and TO, integers); −1 — none. fb ascends inside a hole, tb may go anyhow. Returns how many −1."""
    none = 0
    for h in range(starts.shape[0] - 1):
        b0 = lo[h]
        b1 = hi[h]
        for i in range(starts[h], starts[h + 1]):
            x = fa[i]
            left = b0
            right = b1
            while left < right:
                m = (left + right) >> 1
                if fb[m] <= x:
                    left = m + 1
                else:
                    right = m
            found = -1
            r = left - 1
            while r >= b0 and fb[r] == x:
                if tb[r] == ta[i]:
                    found = r
                    break
                r -= 1
            out[i] = found
            if found < 0:
                none += 1
    return none


def _view(a: np.ndarray, dtype: type) -> np.ndarray:
    """A read-only C-contiguous view of the column (a copy only when it is not such an array already)."""
    out: np.ndarray = np.ascontiguousarray(a, dtype=dtype).view()
    out.flags.writeable = False
    return out


def rows_covering(starts: np.ndarray, mid: np.ndarray, lo: np.ndarray, hi: np.ndarray, b_from: np.ndarray,
                  b_to: np.ndarray) -> tuple[np.ndarray, int]:  # fmt: skip
    """`cover` for the rows of the holes `starts` with the middles `mid` (module docstring)."""
    out = np.empty(mid.shape[0], dtype=np.int64)
    none = cover(_view(starts, np.int64), _view(mid, np.float64), _view(lo, np.int64), _view(hi, np.int64),
                 _view(b_from, np.float64), _view(b_to, np.float64), out)  # fmt: skip
    return out, int(none)


def rows_matching(starts: np.ndarray, fa: np.ndarray, ta: np.ndarray, lo: np.ndarray, hi: np.ndarray,
                  fb: np.ndarray, tb: np.ndarray) -> tuple[np.ndarray, int]:  # fmt: skip
    """`match` for the rows of the holes `starts` with the keys `fa`, `ta` (module docstring)."""
    out = np.empty(fa.shape[0], dtype=np.int64)
    none = match(_view(starts, np.int64), _view(fa, np.int64), _view(ta, np.int64), _view(lo, np.int64),
                 _view(hi, np.int64), _view(fb, np.int64), _view(tb, np.int64), out)  # fmt: skip
    return out, int(none)


def hole_ranges(a_holes: Sequence[str], b_holes: Sequence[str], b_starts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For every hole of a — the stretch [lo; hi) of the rows of b of the hole of that name (0, 0 — b has none)."""
    at = {name: j for j, name in enumerate(b_holes)}
    j = np.array([at.get(name, -1) for name in a_holes], dtype=np.int64)
    if not len(b_holes):
        return np.zeros(len(a_holes), dtype=np.int64), np.zeros(len(a_holes), dtype=np.int64)
    has = j >= 0
    starts = np.asarray(b_starts, dtype=np.int64)
    at_b = np.where(has, j, 0)
    return np.where(has, starts[at_b], 0), np.where(has, starts[at_b + 1], 0)


def warm() -> None:
    """Compile the kernels on a few rows (the `stats` step of the warm-up), as `stats.source` calls them."""
    starts = np.array([0, 2, 3], dtype=np.int64)
    lo, hi = hole_ranges(["a", "b"], ["b"], np.array([0, 2], dtype=np.int64))
    rows_covering(starts, np.array([0.5, 1.5, 1.5]), lo, hi, np.array([0.0, 1.0]), np.array([1.0, 2.0]))
    k = np.array([0, 1, 1], dtype=np.int64)
    rows_matching(starts, k, k + 1, lo, hi, k[:2], k[:2] + 1)
