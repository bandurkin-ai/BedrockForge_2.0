"""Numba kernels of the (low edge, size) pair table of `grid_lattice.PairSet` (ADR 0030). A module of its own so
that importing `grid_lattice` (and the import chain of every importer) does not load numba (~50 MB of RSS)."""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit


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
