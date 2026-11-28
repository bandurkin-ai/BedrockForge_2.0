"""Kernels of a list of blocks (`blocks/list_layout.py`; ADR 0030 decisions 8, 12): numba `cache=True, nogil=True` (the
cache — bf_core.paths). Float64 throughout; a box is its centre `c` and size `d` (local axes) or its edges `lo`, `hi`.

* `cell_index` — the cell of the enclosing grid that holds the centre of every block (P = i + nx·(j + ny·k));
* `mark_duplicates` — in rows sorted by (idx, cz, cy, cx): a row with the same centre AND the same size as an earlier
  row of its run repeats it (the first stays);
* `test_cells` and `overlap_pairs` — the overlaps: every box is entered into the cells of a uniform test grid it touches
  (a cell of twice the median size), the entries sorted by cell, and the pairs of one cell are tested; a pair is
  reported in the cell that holds the low corner of its common part, so it is found once.
"""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit

EPS = 1e-9  # m: two boxes overlap when their common part is thicker than this on every axis
BITS = 21  # a cell index of the test grid takes 21 bits an axis (2 097 152 cells)


@njit(cache=True, nogil=True)
def cell_index(c, origin, size, counts):  # type: ignore[no-untyped-def]
    """P of the cell of every centre (n×3) in the grid `origin`, `size`, `counts`; a centre outside is clamped."""
    n = c.shape[0]
    out = np.empty(n, dtype=np.uint64)
    for r in range(n):
        idx = np.int64(0)
        mul = np.int64(1)
        for a in range(3):
            i = np.int64(np.floor((c[r, a] - origin[a]) / size[a]))
            if i < 0:
                i = 0
            elif i >= counts[a]:
                i = counts[a] - 1
            idx += i * mul
            mul *= counts[a]
        out[r] = np.uint64(idx)
    return out


@njit(cache=True, nogil=True)
def _size_slot(d, r, mask):  # type: ignore[no-untyped-def]
    """The first slot of the table of sizes for row r: the three doubles of the size mixed into an int64."""
    h = np.int64(1469598103934665603)
    for a in range(3):
        h = (h ^ np.int64(np.rint(d[r, a] * 1e6))) * np.int64(1099511628211)
    return (h ^ (h >> 29)) & mask


@njit(cache=True, nogil=True)
def mark_duplicates(idx, c, d):  # type: ignore[no-untyped-def]
    """`dup[r]` — row r repeats an earlier one (the same idx, centre and size); `first[r]` — that earlier row (−1 if
    none). The rows go by (idx, cz, cy, cx): the rows of one centre are neighbours. The sizes of a run of one centre are
    looked up in a small table (open addressing), so a long series of blocks with one centre costs its length, not the
    square of it."""
    n = idx.shape[0]
    dup = np.zeros(n, dtype=np.bool_)
    first = np.full(n, -1, dtype=np.int64)
    r = 0
    while r < n:
        e = r + 1
        while e < n and idx[e] == idx[r] and c[e, 0] == c[r, 0] and c[e, 1] == c[r, 1] and c[e, 2] == c[r, 2]:
            e += 1
        if e - r > 1:
            size = 4
            while size < 2 * (e - r):
                size <<= 1
            mask = np.int64(size - 1)
            table = np.full(size, -1, dtype=np.int64)
            for j in range(r, e):
                at = _size_slot(d, j, mask)
                while True:
                    k = table[at]
                    if k < 0:
                        table[at] = j
                        break
                    if d[k, 0] == d[j, 0] and d[k, 1] == d[j, 1] and d[k, 2] == d[j, 2]:
                        dup[j] = True
                        first[j] = k
                        break
                    at = (at + 1) & mask
        r = e
    return dup, first


@njit(cache=True, nogil=True)
def _span(c, d, gmin, step, a, r):  # type: ignore[no-untyped-def]
    i0 = np.int64(np.floor((c[r, a] - d[r, a] * 0.5 - gmin[a]) / step[a]))
    i1 = np.int64(np.floor((c[r, a] + d[r, a] * 0.5 - gmin[a] - EPS) / step[a]))
    if i1 < i0:
        i1 = i0
    return i0, i1


@njit(cache=True, nogil=True)
def count_entries(c, d, gmin, step):  # type: ignore[no-untyped-def]
    """The number of (box, test cell) entries."""
    total = np.int64(0)
    for r in range(c.shape[0]):
        k = np.int64(1)
        for a in range(3):
            i0, i1 = _span(c, d, gmin, step, a, r)
            k *= i1 - i0 + 1
        total += k
    return total


@njit(cache=True, nogil=True)
def fill_entries(c, d, gmin, step, keys, boxes):  # type: ignore[no-untyped-def]
    """The entries: `keys` — the cell of the test grid packed into one int64, `boxes` — the row of the box."""
    o = 0
    m = np.int64(1) << BITS
    for r in range(c.shape[0]):
        x0, x1 = _span(c, d, gmin, step, 0, r)
        y0, y1 = _span(c, d, gmin, step, 1, r)
        z0, z1 = _span(c, d, gmin, step, 2, r)
        for z in range(z0, z1 + 1):
            for y in range(y0, y1 + 1):
                for x in range(x0, x1 + 1):
                    keys[o] = (z * m + y) * m + x
                    boxes[o] = r
                    o += 1


@njit(cache=True, nogil=True)
def overlap_pairs(c, d, gmin, step, keys, boxes, flag, pairs, cap):  # type: ignore[no-untyped-def]
    """The overlapping pairs among the boxes of the entries sorted by `keys`: `flag[r]` — the box takes part in one;
    `pairs` [cap, 2] — the first pairs met (row a < row b); returns (the number of pairs, how many were kept)."""
    n = keys.shape[0]
    m = np.int64(1) << BITS
    found = np.int64(0)
    kept = 0
    s = 0
    while s < n:
        e = s + 1
        while e < n and keys[e] == keys[s]:
            e += 1
        key = keys[s]
        cx = key % m
        cy = (key // m) % m
        cz = key // (m * m)
        for i in range(s, e):
            a = boxes[i]
            for j in range(i + 1, e):
                b = boxes[j]
                if a == b:
                    continue
                hit = True
                for ax in range(3):
                    la, lb = c[a, ax] - d[a, ax] * 0.5, c[b, ax] - d[b, ax] * 0.5
                    top = la if la > lb else lb
                    ha, hb = c[a, ax] + d[a, ax] * 0.5, c[b, ax] + d[b, ax] * 0.5
                    bot = ha if ha < hb else hb
                    if not top < bot - EPS:
                        hit = False
                        break
                if not hit:
                    continue
                # the cell of the low corner of the common part: the pair belongs to it
                own = True
                for ax in range(3):
                    la, lb = c[a, ax] - d[a, ax] * 0.5, c[b, ax] - d[b, ax] * 0.5
                    top = la if la > lb else lb
                    cell = np.int64(np.floor((top - gmin[ax]) / step[ax]))
                    want = cx if ax == 0 else (cy if ax == 1 else cz)
                    if cell != want:
                        own = False
                        break
                if not own:
                    continue
                flag[a] = True
                flag[b] = True
                found += 1
                if kept < cap:
                    pairs[kept, 0] = a if a < b else b
                    pairs[kept, 1] = b if a < b else a
                    kept += 1
        s = e
    return found, kept


# --- the faces of a list of blocks (`list_shell.py`) ----------------------------------------------------------------

QUANT = 1e6  # the faces of two blocks meet when their planes and extents agree to a micron
PRIME = np.int64(1099511628211)  # FNV-1a prime, 64 bit; int64 products wrap


@njit(cache=True, nogil=True)
def _hash(c, d, r, a, hi):  # type: ignore[no-untyped-def]
    """The key of the face of the block r on the axis a, low or high (`hi`): the plane and the two extents of the
    face, in microns, mixed into one int64. A low face of one block and the high face of another that meet have equal
    keys."""
    u = (a + 1) % 3
    v = (a + 2) % 3
    plane = c[r, a] + (d[r, a] * 0.5 if hi else -d[r, a] * 0.5)
    h = np.int64(a) + np.int64(1469598103934665603)
    h = (h ^ np.int64(np.rint(plane * QUANT))) * PRIME
    h = (h ^ np.int64(np.rint((c[r, u] - d[r, u] * 0.5) * QUANT))) * PRIME
    h = (h ^ np.int64(np.rint((c[r, u] + d[r, u] * 0.5) * QUANT))) * PRIME
    h = (h ^ np.int64(np.rint((c[r, v] - d[r, v] * 0.5) * QUANT))) * PRIME
    h = (h ^ np.int64(np.rint((c[r, v] + d[r, v] * 0.5) * QUANT))) * PRIME
    return h ^ (h >> 29)


@njit(cache=True, nogil=True)
def face_hashes(c, d, hl):  # type: ignore[no-untyped-def]
    """`hl[3r + a]` — the key of the low face of block r on the axis a (the keys of the high faces are made on the fly:
    no second array of them)."""
    for r in range(c.shape[0]):
        for a in range(3):
            hl[3 * r + a] = _hash(c, d, r, a, False)


@njit(cache=True, nogil=True)
def _same(c, d, r1, r2, a):  # type: ignore[no-untyped-def]
    """The high face of r1 and the low face of r2 on the axis a are one face to a micron."""
    u = (a + 1) % 3
    v = (a + 2) % 3
    p1 = c[r1, a] + d[r1, a] * 0.5
    p2 = c[r2, a] - d[r2, a] * 0.5
    if np.rint(p1 * QUANT) != np.rint(p2 * QUANT):
        return False
    for ax in (u, v):
        if np.rint((c[r1, ax] - d[r1, ax] * 0.5) * QUANT) != np.rint((c[r2, ax] - d[r2, ax] * 0.5) * QUANT):
            return False
        if np.rint((c[r1, ax] + d[r1, ax] * 0.5) * QUANT) != np.rint((c[r2, ax] + d[r2, ax] * 0.5) * QUANT):
            return False
    return True


@njit(cache=True, nogil=True)
def hide_faces(c, d, hl, hide):  # type: ignore[no-untyped-def]
    """Matches the high faces (their keys made here) with the low faces `hl[3r + a]` of equal key (a table of the face
    numbers, int32, open addressing; the key of the face in a slot is read from `hl`); when two faces really are one
    (no collision of the hash) both are hidden — bit 2·axis + 1 and 2·axis of `hide[row]`. Returns the pairs."""
    n = hl.shape[0]
    size = 1
    while size < 2 * n + 2:
        size <<= 1
    mask = size - 1
    table = np.full(size, -1, dtype=np.int32)
    for f in range(n):  # the first low face of a key keeps the slot
        k = hl[f]
        at = (k ^ (k >> 32)) & mask
        while table[at] >= 0 and hl[table[at]] != k:
            at = (at + 1) & mask
        if table[at] < 0:
            table[at] = f
    pairs = 0
    for f1 in range(n):
        a = f1 % 3
        r1 = f1 // 3
        k = _hash(c, d, r1, a, True)
        at = (k ^ (k >> 32)) & mask
        while table[at] >= 0 and hl[table[at]] != k:
            at = (at + 1) & mask
        if table[at] < 0:
            continue
        f2 = np.int64(table[at])
        r2 = f2 // 3
        if f2 % 3 == a and r1 != r2 and _same(c, d, r1, r2, a):
            hide[r1] |= np.uint8(1 << (2 * a + 1))
            hide[r2] |= np.uint8(1 << (2 * a))
            pairs += 1
    return pairs


@njit(cache=True, nogil=True)
def face_starts(hide, starts):  # type: ignore[no-untyped-def]
    """`starts[r]` — the first face of block r when the hidden faces (`hide`, one bit each) are left out; returns the
    count of faces."""
    o = 0
    for r in range(hide.shape[0]):
        starts[r] = o
        m = np.int64(hide[r]) ^ 63
        while m:
            o += m & 1
            m >>= 1
    starts[hide.shape[0]] = o
    return o


@njit(cache=True, nogil=True)
def build_faces(c, d, klass, hide, starts, fc, fh, fa, fk, fp):  # type: ignore[no-untyped-def]
    """The faces: the centre `fc` (local axes), the half sizes along u and v `fh`, `fa` = 2·axis + side, the class
    `fk`; `fp` — the coordinate of the plane (the key of the order)."""
    for r in range(c.shape[0]):
        o = starts[r]
        for b in range(6):
            if (hide[r] >> b) & 1:
                continue
            a = b >> 1
            u = (a + 1) % 3
            v = (a + 2) % 3
            plane = c[r, a] + (d[r, a] * 0.5 if (b & 1) else -d[r, a] * 0.5)
            fc[o, a] = plane
            fc[o, u] = c[r, u]
            fc[o, v] = c[r, v]
            fh[o, 0] = d[r, u] * 0.5
            fh[o, 1] = d[r, v] * 0.5
            fa[o] = b
            fk[o] = klass[r]
            fp[o] = plane
            o += 1
