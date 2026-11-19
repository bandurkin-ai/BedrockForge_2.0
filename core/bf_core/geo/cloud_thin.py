"""Kernel of the thinning of a point cloud before a DTM is built from it (ADR 0014 §5): one point in every cell
of the plan — the lower median of the cell.

The points of a cell are ordered by (key, number): the key is the height above the ground (or Z), the number is
the place of the point in the cloud, so the order is total and the answer does not depend on how the points of a
cell came. The representative is the point of rank `(m − 1) // 2` among the m points of the cell — the lower
median; with equal keys the smaller number goes first. It is one of the source points, never a mean.

float64 or float32 keys as given, no `fastmath`; the kernel releases the GIL.
"""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit

SMALL = 32  # cells with more points than this are sorted by numpy's merge sort


@njit(cache=True, nogil=True)
def cell_medians(cell, key, order, start, stop, out, count):  # type: ignore[no-untyped-def]
    """The lower medians of the cells that begin in `order[start:stop]`.

    `cell[p]`, `key[p]` — the cell and the key of point p (keys are numbers: no NaN); `order` — the points
    grouped by cell: the points of one cell stand together, in any order. A cell that begins before `stop` is
    finished even when it ends after it. The representatives go to `out[count:]`. Returns (the place in
    `order` to go on from, the new count)."""
    n = len(order)
    buf = np.empty(SMALL, np.int64)
    a = start
    while a < n and a < stop:
        c = cell[order[a]]
        b = a + 1
        while b < n and cell[order[b]] == c:
            b += 1
        m = b - a
        want = (m - 1) // 2
        if m == 1:
            out[count] = order[a]
        elif m <= SMALL:
            for i in range(m):
                p = order[a + i]
                kp = key[p]
                j = i
                while j > 0:
                    q = buf[j - 1]
                    kq = key[q]
                    if kq > kp or (kq == kp and q > p):
                        buf[j] = q
                        j -= 1
                    else:
                        break
                buf[j] = p
            out[count] = buf[want]
        else:
            sub = np.sort(order[a:b])  # by number: the stable sort below keeps it among equal keys
            rank = np.argsort(key[sub], kind="mergesort")
            out[count] = sub[rank[want]]
        count += 1
        a = b
    return a, count
