"""Kernels of `stats.source`: the rows of another table of the set for the rows of a table (ADR 0019 §3; 13.35).

    cover(starts, mid, lo, hi, b_from, b_to, out)       the interval of the other table that holds the middle
    match(starts, fa, ta, lo, hi, fb, tb, out)          the row of the other table with the same sample key
    hole_ranges(a_holes, b_holes, b_starts) -> lo, hi   the rows of the other table of every hole of the table
    warm()

Both tables are «lines» (`composites.source`): their rows go hole by hole, inside a hole by FROM, so the rows of
a hole of the other table are one stretch `lo[h] … hi[h] − 1` with FROM ascending — a binary search per row,
no sort of the two tables together (5 million samples against 1 million intervals: 0.03 s for 1.1 s of the
`lexsort`). The answers are those of that sort: among the rows that fit the last one in line order is taken.
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


def hole_ranges(a_holes: Sequence[str], b_holes: Sequence[str], b_starts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For every hole of a — the stretch [lo; hi) of the rows of b of the hole of that name (0, 0 — b has none)."""
    at = {name: j for j, name in enumerate(b_holes)}
    j = np.array([at.get(name, -1) for name in a_holes], dtype=np.int64)
    has = j >= 0
    starts = np.ascontiguousarray(b_starts, dtype=np.int64)
    lo = np.where(has, starts[np.where(has, j, 0)], 0) if len(b_holes) else np.zeros(len(a_holes), dtype=np.int64)
    hi = np.where(has, starts[np.where(has, j, 0) + 1], 0) if len(b_holes) else np.zeros(len(a_holes), dtype=np.int64)
    return np.ascontiguousarray(lo, dtype=np.int64), np.ascontiguousarray(hi, dtype=np.int64)


def warm() -> None:
    """Compile the kernels on a few rows (the `stats` step of the warm-up)."""
    starts = np.array([0, 2, 3], dtype=np.int64)
    lo, hi = hole_ranges(["a", "b"], ["b"], np.array([0, 2], dtype=np.int64))
    f, t = np.array([0.0, 1.0]), np.array([1.0, 2.0])
    out = np.empty(3, dtype=np.int64)
    cover(starts, np.array([0.5, 1.5, 1.5]), lo, hi, f, t, out)
    k = np.array([0, 1, 1], dtype=np.int64)
    match(starts, k, k + 1, lo, hi, k[:2], k[:2] + 1, out)
