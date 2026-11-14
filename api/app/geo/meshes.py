"""Triangle meshes for display and calculation: spatial order, parts, levels of detail.

    morton_order(v, t)       triangles sorted along a Z-order curve of their centroids
    chunk_table(v, t, n)     parts of n consecutive triangles with their bounding boxes
    simplify(v, t, target)   quadric decimation for distant views (pyfqmr)

The exact float64 geometry is never replaced by a simplified one; levels of detail are only
drawn, and every calculation (volume, sections, measurements) uses the original triangles.
"""

from __future__ import annotations

import numpy as np

CHUNK_TRIANGLES = 32768
# Levels of detail are built only above this size; smaller meshes are drawn whole.
LOD_MIN_TRIANGLES = 200_000
# Each level keeps this share of the previous one.
LOD_RATIO = 8
LOD_FLOOR = 2_000


def _spread3(x: np.ndarray) -> np.ndarray:
    """Spreads the low 21 bits of x so that two zero bits follow every bit (for Morton codes)."""
    x = x.astype(np.uint64) & np.uint64(0x1FFFFF)
    x = (x | (x << np.uint64(32))) & np.uint64(0x1F00000000FFFF)
    x = (x | (x << np.uint64(16))) & np.uint64(0x1F0000FF0000FF)
    x = (x | (x << np.uint64(8))) & np.uint64(0x100F00F00F00F00F)
    x = (x | (x << np.uint64(4))) & np.uint64(0x10C30C30C30C30C3)
    x = (x | (x << np.uint64(2))) & np.uint64(0x1249249249249249)
    return x


def morton_order(vertices: np.ndarray, triangles: np.ndarray, slice_size: int = 1 << 21) -> np.ndarray:
    """Permutation of triangles along a Z-order curve: neighbours in space are neighbours in the
    array, so consecutive triangles form compact parts (culling, partial loading, sections)."""
    m = len(triangles)
    if m == 0:
        return np.zeros(0, dtype=np.int64)
    lo = vertices.min(axis=0)
    span = np.maximum(vertices.max(axis=0) - lo, 1e-9)
    codes = np.empty(m, dtype=np.uint64)
    scale = (2**21 - 1) / span
    for start in range(0, m, slice_size):
        tri = triangles[start:start + slice_size]
        c = (vertices[tri[:, 0]] + vertices[tri[:, 1]] + vertices[tri[:, 2]]) / 3.0
        q = ((c - lo) * scale).astype(np.int64)
        codes[start:start + len(tri)] = _spread3(q[:, 0]) | (_spread3(q[:, 1]) << np.uint64(1)) | (_spread3(q[:, 2]) << np.uint64(2))
    return np.argsort(codes, kind="stable")


def chunk_table(vertices: np.ndarray, triangles: np.ndarray, origin: np.ndarray,
                size: int = CHUNK_TRIANGLES) -> list[list[float]]:
    """[first triangle, count, min x, y, z, max x, y, z] per part; bounds relative to `origin`."""
    out = []
    for start in range(0, len(triangles), size):
        tri = np.asarray(triangles[start:start + size])
        p = vertices[tri.reshape(-1)] - origin
        lo, hi = p.min(axis=0), p.max(axis=0)
        out.append([start, len(tri), *np.round(lo, 3).tolist(), *np.round(hi, 3).tolist()])
    return out


# Triangles simplified at once: bounds the memory of the decimation (~0.5 GB per million).
SIMPLIFY_PART = 1 << 20


def _simplify_one(vertices: np.ndarray, triangles: np.ndarray, target: int) -> tuple[np.ndarray, np.ndarray]:
    import pyfqmr

    o = (vertices.min(axis=0) + vertices.max(axis=0)) / 2.0
    s = pyfqmr.Simplify()
    # pyfqmr needs writable arrays (stored ones are read-only memory maps).
    s.setMesh(np.array(vertices - o, dtype=np.float64, order="C"), np.array(triangles, dtype=np.int32, order="C"))
    s.simplify_mesh(target_count=int(target), aggressiveness=7, preserve_border=True, verbose=False)
    v, t, _ = s.getMesh()
    return np.asarray(v, dtype=np.float64) + o, np.asarray(t, dtype=np.int32)


def simplify(vertices: np.ndarray, triangles: np.ndarray, target: int, part: int = SIMPLIFY_PART,
             progress=None) -> tuple[np.ndarray, np.ndarray]:
    """Quadric edge-collapse decimation to about `target` triangles.

    `triangles` should be in spatial order (morton_order): the mesh is decimated in consecutive
    parts of `part` triangles, each keeping its border, so parts join without cracks and the
    memory stays bounded whatever the mesh size.
    """
    m = len(triangles)
    if m <= part:
        return _simplify_one(vertices, triangles, target)
    out_v, out_t, base = [], [], 0
    parts = range(0, m, part)
    for i, start in enumerate(parts):
        tri = np.asarray(triangles[start:start + part])
        used, local = np.unique(tri, return_inverse=True)
        share = max(int(round(target * len(tri) / m)), 4)
        v, t = _simplify_one(vertices[used], local.reshape(-1, 3), share)
        out_v.append(v)
        out_t.append(t + base)
        base += len(v)
        if progress:
            progress((i + 1) / len(parts))
    return np.concatenate(out_v), np.concatenate(out_t).astype(np.int32)


def lod_targets(triangle_count: int) -> list[int]:
    """Triangle counts of the levels of detail (coarser and coarser), none for small meshes."""
    if triangle_count < LOD_MIN_TRIANGLES:
        return []
    targets = []
    n = triangle_count
    while len(targets) < 2:
        n //= LOD_RATIO
        if n < LOD_FLOOR:
            break
        targets.append(n)
    return targets
