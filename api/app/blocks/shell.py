"""The outer shell of a block model: blocks that can be seen from outside.

A big model (millions of blocks) cannot be drawn whole in a browser; the blocks closed on every side
by their neighbours are never seen anyway. Parent cells whose sub-blocks fill them completely are
"full". A block of a full cell is hidden when each of its faces is either inside its cell (covered
by its siblings) or on the cell's boundary next to another full cell. Blocks of cells that are not
full, and blocks that cross their cell, are always shown.
"""

from __future__ import annotations

import numpy as np


def exposed_mask(centres: np.ndarray, sizes: np.ndarray, origin, parent, counts) -> np.ndarray:
    """uint8 per block: 1 — can be seen from outside, 0 — closed on every side."""
    n = len(centres)
    if n == 0:
        return np.zeros(0, np.uint8)
    origin = np.asarray(origin, np.float64)
    parent = np.asarray(parent, np.float64)
    counts = np.asarray(counts, np.int64).clip(min=1)
    tol = 1e-6 * np.maximum(parent, 1)
    out = np.ones(n, np.uint8)
    step = 1 << 21
    # Pass 1: which cells are full (sub-block volumes add up to the parent volume).
    lin_all = np.full(n, -1, np.int64)
    cell_all = np.zeros((n, 3), np.int64)
    for a in range(0, n, step):
        c = centres[a:a + step].astype(np.float64)
        s = sizes[a:a + step].astype(np.float64)
        lo, hi = c - s / 2, c + s / 2
        i0 = np.floor((lo - origin + tol) / parent).astype(np.int64)
        i1 = np.floor((hi - origin - tol) / parent).astype(np.int64)
        ok = (i0 == i1).all(axis=1) & (i0 >= 0).all(axis=1) & (i0 < counts).all(axis=1)
        cell_all[a:a + step] = i0
        lin_all[a:a + step] = np.where(ok, (i0[:, 0] * counts[1] + i0[:, 1]) * counts[2] + i0[:, 2], -1)
    good = lin_all >= 0
    if not good.any():
        return out
    vol = np.prod(sizes.astype(np.float64), axis=1)
    cells, inverse = np.unique(lin_all[good], return_inverse=True)
    filled = np.bincount(inverse, weights=vol[good])
    full_cells = cells[filled >= np.prod(parent) * (1 - 1e-6)]
    if not len(full_cells):
        return out

    def is_full(lin: np.ndarray) -> np.ndarray:
        pos = np.searchsorted(full_cells, lin).clip(max=len(full_cells) - 1)
        return full_cells[pos] == lin

    # Pass 2: blocks of full cells, face by face.
    for a in range(0, n, step):
        lin = lin_all[a:a + step]
        inside_full = (lin >= 0) & is_full(np.where(lin >= 0, lin, -1))
        if not inside_full.any():
            continue
        c = centres[a:a + step].astype(np.float64)
        s = sizes[a:a + step].astype(np.float64)
        lo, hi = c - s / 2, c + s / 2
        cell = cell_all[a:a + step]
        cell_lo = origin + cell * parent
        cell_hi = cell_lo + parent
        seen = np.zeros(len(lin), bool)
        for axis in range(3):
            for side, touches in ((-1, np.abs(lo[:, axis] - cell_lo[:, axis]) <= tol[axis]),
                                  (1, np.abs(hi[:, axis] - cell_hi[:, axis]) <= tol[axis])):
                nb = cell.copy()
                nb[:, axis] += side
                inside_grid = (nb >= 0).all(axis=1) & (nb < counts).all(axis=1)
                nb_lin = (nb[:, 0] * counts[1] + nb[:, 1]) * counts[2] + nb[:, 2]
                nb_full = inside_grid & is_full(np.where(inside_grid, nb_lin, -1))
                seen |= touches & ~nb_full
        out[a:a + step] = np.where(inside_full, seen, True).astype(np.uint8)
    return out
