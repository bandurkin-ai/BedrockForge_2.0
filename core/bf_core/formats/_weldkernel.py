"""numba kernels of `formats.meshset`: exact welding of coincident vertices with an open-addressing hash table.

A vertex is its three float64 seen as uint64 (compared bit for bit, −0.0 as +0.0). The table holds the number of
the vertex kept in every used slot (−1 — free), its size is a power of two; a new vertex takes the next number,
so the vertices kept are in the order of their first appearance and stay as they were given. Imported lazily:
numba is heavy.
"""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit

_SIGN = np.uint64(0x8000000000000000)  # the bits of −0.0
_ZERO = np.uint64(0)
_ONE = np.uint64(1)
_M1 = np.uint64(0x9E3779B97F4A7C15)
_M2 = np.uint64(0xC2B2AE3D27D4EB4F)
_M3 = np.uint64(0x165667B19E3779F9)
_S29 = np.uint64(29)
_S32 = np.uint64(32)


@njit(cache=True)
def _slot(x, y, z, mask):  # type: ignore[no-untyped-def]
    """The first slot of a vertex: its 192 bits mixed down (multiply and fold, as the finishers of xxHash)."""
    h = x * _M1
    h ^= h >> _S32
    h = (h ^ y) * _M2
    h ^= h >> _S29
    h = (h ^ z) * _M3
    h ^= h >> _S32
    return h & mask


@njit(cache=True)
def weld_chunk(bits, table, verts, count, out):  # type: ignore[no-untyped-def]
    """Number the rows of `bits` (uint64 k×3): `out[i]` — the vertex of row i among `verts[:count]`, a row not
    seen before is appended to `verts`. Returns the new count. The caller makes room: `len(verts) ≥ count + k`
    and `len(table) ≥ 2 · (count + k)`, so a free slot is always found."""
    mask = np.uint64(len(table) - 1)
    for i in range(len(bits)):
        x, y, z = bits[i, 0], bits[i, 1], bits[i, 2]
        kx = _ZERO if x == _SIGN else x
        ky = _ZERO if y == _SIGN else y
        kz = _ZERO if z == _SIGN else z
        slot = _slot(kx, ky, kz, mask)
        while True:
            j = table[slot]
            if j < 0:
                table[slot] = count
                verts[count, 0], verts[count, 1], verts[count, 2] = x, y, z  # as given: −0.0 stays −0.0
                out[i] = count
                count += 1
                break
            vx, vy, vz = verts[j, 0], verts[j, 1], verts[j, 2]
            if (
                (kx == vx or (kx == _ZERO and vx == _SIGN))
                and (ky == vy or (ky == _ZERO and vy == _SIGN))
                and (kz == vz or (kz == _ZERO and vz == _SIGN))
            ):
                out[i] = j
                break
            slot = (slot + _ONE) & mask
    return count


@njit(cache=True)
def rehash(table, verts, count):  # type: ignore[no-untyped-def]
    """Fill an empty `table` (all −1) with the vertices `verts[:count]`, which are all different."""
    mask = np.uint64(len(table) - 1)
    for j in range(count):
        x, y, z = verts[j, 0], verts[j, 1], verts[j, 2]
        slot = _slot(_ZERO if x == _SIGN else x, _ZERO if y == _SIGN else y, _ZERO if z == _SIGN else z, mask)
        while table[slot] >= 0:
            slot = (slot + _ONE) & mask
        table[slot] = j
