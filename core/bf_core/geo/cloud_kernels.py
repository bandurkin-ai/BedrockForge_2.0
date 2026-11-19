"""Kernels of the storage order and of the levels of detail of a point cloud (ADR 0014 §1).

Order. A point lies in a cell of 1 m of the project grid (`floor` of its X and Y). The place of a point in a cloud
is the Morton code of its cell — the bits of the two cell numbers interleaved, both counted from −2³⁰ m, so the
code does not depend on the extent of the cloud — and, among the points of one cell, the order of the source.
The upper bits of a code are the square of 2, 4, … 2ᵏ metres the cell lies in: a run of codes with a common
prefix is a square of the grid, which is what the builder cuts the cloud into (`lines/cloud_build.py`).
A point that has no place — a coordinate that is not a number or is beyond ±`MAX_ABS` metres — gets the code −1.

Levels. Level j ≥ 1 of a part keeps one point in every cell of `cell0 · 4^(j−1)` metres of the project grid (X, Y
and Z): the first in the order of the part. The sizes are powers of two, so `floor(v / size)` is exact and the
cells nest exactly: the first point of a coarse cell is also the first of every finer cell it lies in. The rank
of a point is the coarsest level it stands for (0 — only the full level); level j is the points of rank ≥ j, and
a part ordered by falling rank has every level as a prefix.

float64 throughout, no `fastmath`; the kernels release the GIL.
"""

from __future__ import annotations

import math

import numpy as np

from bf_core.jit import njit

#: A coordinate beyond this many metres is not a place on the ground: such a point is not stored (the float32
#: offsets of the display and the 31-bit cell numbers of the order both rely on it).
MAX_ABS = 1.0e9
CELL_BASE = 1 << 30  # cells are counted from −2³⁰ m: numbers 0 … 2³¹ − 1, a code of 62 bits
CODE_BITS = 62


@njit(cache=True, nogil=True, inline="always")
def _spread(v):  # type: ignore[no-untyped-def]
    """The 31 bits of v moved to the even positions (b0 → 0, b1 → 2, …)."""
    x = v & 0x7FFFFFFF
    x = (x | (x << 16)) & 0x0000FFFF0000FFFF
    x = (x | (x << 8)) & 0x00FF00FF00FF00FF
    x = (x | (x << 4)) & 0x0F0F0F0F0F0F0F0F
    x = (x | (x << 2)) & 0x3333333333333333
    x = (x | (x << 1)) & 0x5555555555555555
    return x


@njit(cache=True, nogil=True)
def morton_codes(xyz, codes, box):  # type: ignore[no-untyped-def]
    """`codes[i]` — the Morton code of the 1 m cell of point i (int64, ≥ 0), −1 for a point without a place.
    `box` (6: the lows, then the highs) grows to hold the points that have one. Returns how many do."""
    n = 0
    for i in range(len(xyz)):
        x = xyz[i, 0]
        y = xyz[i, 1]
        z = xyz[i, 2]
        # a NaN fails every comparison: written so that it fails the test
        if not (abs(x) <= MAX_ABS and abs(y) <= MAX_ABS and abs(z) <= MAX_ABS):
            codes[i] = -1
            continue
        cx = np.int64(math.floor(x)) + CELL_BASE
        cy = np.int64(math.floor(y)) + CELL_BASE
        codes[i] = _spread(cx) | (_spread(cy) << 1)
        n += 1
        if x < box[0]:
            box[0] = x
        if y < box[1]:
            box[1] = y
        if z < box[2]:
            box[2] = z
        if x > box[3]:
            box[3] = x
        if y > box[4]:
            box[4] = y
        if z > box[5]:
            box[5] = z
    return n


@njit(cache=True, nogil=True, inline="always")
def _slot(a, b, c, mask):  # type: ignore[no-untyped-def]
    """A slot of the table for the cell (a, b, c). Every constant fits int64 (a larger one would turn the
    products into float64); int64 arithmetic wraps, the mask keeps the result ≥ 0."""
    h = a * 0x5851F42D4C957F2D + b * 0x42B2AE3D27D4EB4F + c * 0x165667B19E3779F9
    h ^= h >> 31
    h *= 0x2545F4914F6CDD1D
    h ^= h >> 29
    return h & mask


@njit(cache=True, nogil=True)
def level_ranks(xyz, cell0, levels, rank):  # type: ignore[no-untyped-def]
    """`rank[i]` (uint8) — the coarsest of `levels` levels point i stands for, 0 — none: level j ≥ 1 has cells of
    `cell0 · 4^(j−1)` metres (X, Y, Z of the project grid) and keeps the first point of a cell in the order
    given. `cell0` is a power of two. One pass: a table per level holds the first point of every cell met."""
    n = len(xyz)
    cap = 16
    while cap < 2 * n:
        cap *= 2
    mask = cap - 1
    table = np.full((levels, cap), -1, dtype=np.int32)
    cell = np.empty((n, 3), dtype=np.int64)
    for i in range(n):
        for c in range(3):
            v = xyz[i, c] / cell0
            if not abs(v) <= 4.0e18:  # NaN or beyond int64: one cell for all of them (the builder stores none)
                cell[i, c] = -(1 << 62)
            else:
                cell[i, c] = np.int64(math.floor(v))
    for i in range(n):
        a = cell[i, 0]
        b = cell[i, 1]
        c = cell[i, 2]
        r = 0
        for lev in range(levels):
            shift = 2 * lev
            if lev:
                a >>= 2
                b >>= 2
                c >>= 2
            h = _slot(a, b, c, mask)
            first = True
            while True:
                j = table[lev, h]
                if j < 0:
                    table[lev, h] = i
                    break
                if (cell[j, 0] >> shift) == a and (cell[j, 1] >> shift) == b and (cell[j, 2] >> shift) == c:
                    first = False
                    break
                h = (h + 1) & mask
            if not first:
                break  # not the first of this cell: not the first of any coarser one either
            r = lev + 1
        rank[i] = r


@njit(cache=True, nogil=True)
def rank_order(rank, levels, order, counts):  # type: ignore[no-untyped-def]
    """`order` — the points by falling rank, the order given kept among equals; `counts[j]` — how many points
    have a rank ≥ j (`counts[0]` — all of them): the sizes of the levels."""
    n = len(rank)
    for j in range(levels + 1):
        counts[j] = 0
    for i in range(n):
        counts[rank[i]] += 1
    at = np.empty(levels + 1, dtype=np.int64)
    total = 0
    for j in range(levels, -1, -1):  # the place of the first point of rank j
        at[j] = total
        total += counts[j]
        counts[j] = total
    for i in range(n):
        r = rank[i]
        order[at[r]] = i
        at[r] += 1
