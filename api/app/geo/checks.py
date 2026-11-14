"""Wireframe checks that need more than counting: normal orientation and self-intersections.

    orientation_conflicts(t, n)    edges walked the same way by two triangles (flipped normals)
    self_intersections(v, t)       pairs of triangles (sharing no vertex) whose interiors cross

Self-intersections: a uniform grid of cells about twice the typical triangle size; every
triangle is listed in the cells its bounding box covers; pairs within a cell are tested
exactly (edges of one against the other, float64 relative to the mesh centre), and a pair is
counted only in the cell holding the lower corner of the two boxes' overlap, so never twice.
Coplanar overlaps are not detected (they have no crossing edge) — reported as a limitation.
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

MAX_CELLS_PER_TRIANGLE = 512
SAMPLES = 200


def orientation_conflicts(triangles: np.ndarray, vertex_count: int) -> int:
    """Directed edges used twice: neighbouring triangles with opposite windings."""
    t = np.asarray(triangles)
    m = len(t)
    n = max(int(vertex_count), 1)
    keys = np.empty(3 * m, dtype=np.int64)
    for k, (i, j) in enumerate(((0, 1), (1, 2), (2, 0))):
        keys[k * m:(k + 1) * m] = t[:, i].astype(np.int64) * n + t[:, j]
    keys.sort()
    return int(np.count_nonzero(keys[1:] == keys[:-1]))


@njit(cache=True, inline="always")
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


@njit(cache=True, inline="always")
def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


@njit(cache=True, inline="always")
def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


@njit(cache=True)
def _segment_hits_triangle(p, q, a, b, c, eps):
    """Möller–Trumbore: the open segment p→q pierces the closed triangle abc.

    The segment must cross the plane strictly between its ends (a triangle merely touching
    another with a corner is not a crossing), but the crossing point may lie on the other
    triangle's edge: two meshes passing through each other along a grid line are caught.
    A segment lying in the triangle's plane never counts (det = 0)."""
    d = _sub(q, p)
    e1 = _sub(b, a)
    e2 = _sub(c, a)
    h = _cross(d, e2)
    det = _dot(e1, h)
    if abs(det) < 1e-300:
        return False
    inv = 1.0 / det
    s = _sub(p, a)
    u = _dot(s, h) * inv
    if u < -eps or u > 1.0 + eps:
        return False
    qq = _cross(s, e1)
    v = _dot(d, qq) * inv
    if v < -eps or u + v > 1.0 + eps:
        return False
    t = _dot(e2, qq) * inv
    return eps < t < 1.0 - eps


@njit(cache=True)
def _crosses(v, ti, tj, eps):
    a = (v[ti[0], 0], v[ti[0], 1], v[ti[0], 2])
    b = (v[ti[1], 0], v[ti[1], 1], v[ti[1], 2])
    c = (v[ti[2], 0], v[ti[2], 1], v[ti[2], 2])
    p = (v[tj[0], 0], v[tj[0], 1], v[tj[0], 2])
    q = (v[tj[1], 0], v[tj[1], 1], v[tj[1], 2])
    r = (v[tj[2], 0], v[tj[2], 1], v[tj[2], 2])
    # Quick reject: all corners of one strictly on one side of the other's plane.
    n1 = _cross(_sub(b, a), _sub(c, a))
    dp, dq, dr = _dot(n1, _sub(p, a)), _dot(n1, _sub(q, a)), _dot(n1, _sub(r, a))
    if (dp > 0 and dq > 0 and dr > 0) or (dp < 0 and dq < 0 and dr < 0):
        return False
    n2 = _cross(_sub(q, p), _sub(r, p))
    da, db, dc = _dot(n2, _sub(a, p)), _dot(n2, _sub(b, p)), _dot(n2, _sub(c, p))
    if (da > 0 and db > 0 and dc > 0) or (da < 0 and db < 0 and dc < 0):
        return False
    return (_segment_hits_triangle(a, b, p, q, r, eps) or _segment_hits_triangle(b, c, p, q, r, eps)
            or _segment_hits_triangle(c, a, p, q, r, eps) or _segment_hits_triangle(p, q, a, b, c, eps)
            or _segment_hits_triangle(q, r, a, b, c, eps) or _segment_hits_triangle(r, p, a, b, c, eps))


@njit(cache=True)
def _cell_counts(lo, hi, cell, gmin):
    m = lo.shape[0]
    out = np.empty(m, dtype=np.int64)
    for i in range(m):
        n = 1
        for k in range(3):
            n *= int((hi[i, k] - gmin[k]) // cell) - int((lo[i, k] - gmin[k]) // cell) + 1
        out[i] = n
    return out


@njit(cache=True)
def _fill_cells(lo, hi, cell, gmin, dims, starts, keys, ids, big):
    for i in range(lo.shape[0]):
        if big[i]:
            continue
        x0 = int((lo[i, 0] - gmin[0]) // cell)
        y0 = int((lo[i, 1] - gmin[1]) // cell)
        z0 = int((lo[i, 2] - gmin[2]) // cell)
        x1 = int((hi[i, 0] - gmin[0]) // cell)
        y1 = int((hi[i, 1] - gmin[1]) // cell)
        z1 = int((hi[i, 2] - gmin[2]) // cell)
        p = starts[i]
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    keys[p] = (x * dims[1] + y) * dims[2] + z
                    ids[p] = i
                    p += 1


@njit(cache=True)
def _pair_owned(i, j, lo, hi, cell, gmin, key, dims):
    """True if the pair's boxes overlap and the overlap's lower corner lies in this cell."""
    c = np.empty(3, dtype=np.int64)
    for k in range(3):
        a = max(lo[i, k], lo[j, k])
        b = min(hi[i, k], hi[j, k])
        if a > b:
            return False
        c[k] = int((a - gmin[k]) // cell)
    return (c[0] * dims[1] + c[1]) * dims[2] + c[2] == key


@njit(cache=True)
def _share_vertex(t, i, j):
    for a in range(3):
        for b in range(3):
            if t[i, a] == t[j, b]:
                return True
    return False


@njit(parallel=True, cache=True)
def _count_runs(v, t, lo, hi, cell, gmin, dims, keys, ids, runs, eps):
    found = np.zeros(len(runs) - 1, dtype=np.int64)
    for r in prange(len(runs) - 1):
        s, e = runs[r], runs[r + 1]
        key = keys[s]
        n = 0
        for x in range(s, e):
            i = ids[x]
            for y in range(x + 1, e):
                j = ids[y]
                if _share_vertex(t, i, j) or not _pair_owned(i, j, lo, hi, cell, gmin, key, dims):
                    continue
                if _crosses(v, t[i], t[j], eps):
                    n += 1
        found[r] = n
    return found


@njit(cache=True)
def _samples_in_run(v, t, lo, hi, cell, gmin, dims, keys, ids, s, e, eps, out, filled):
    key = keys[s]
    for x in range(s, e):
        i = ids[x]
        for y in range(x + 1, e):
            j = ids[y]
            if filled >= out.shape[0]:
                return filled
            if _share_vertex(t, i, j) or not _pair_owned(i, j, lo, hi, cell, gmin, key, dims):
                continue
            if _crosses(v, t[i], t[j], eps):
                out[filled, 0] = i
                out[filled, 1] = j
                filled += 1
    return filled


@njit(parallel=True, cache=True)
def _big_against_all(v, t, lo, hi, big_ids, is_big, eps):
    """Triangles too large for the grid, tested against every triangle whose box they touch."""
    found = np.zeros(len(big_ids), dtype=np.int64)
    m = t.shape[0]
    for b in prange(len(big_ids)):
        i = big_ids[b]
        n = 0
        for j in range(m):
            if j == i or _share_vertex(t, i, j):
                continue
            # A pair of two big triangles is counted once (by the lower id).
            if is_big[j] and j < i:
                continue
            ok = True
            for k in range(3):
                if lo[i, k] > hi[j, k] or lo[j, k] > hi[i, k]:
                    ok = False
                    break
            if ok and _crosses(v, t[i], t[j], eps):
                n += 1
        found[b] = n
    return found


def self_intersections(vertices: np.ndarray, triangles: np.ndarray) -> dict:
    """{"pairs": n, "samples": [[x, y, z], ...], "big_triangles": n}; points are centroids of crossings."""
    m = len(triangles)
    if m < 2:
        return {"pairs": 0, "samples": [], "big_triangles": 0}
    centre = (vertices.min(axis=0) + vertices.max(axis=0)) / 2.0
    v = np.ascontiguousarray(vertices - centre, dtype=np.float64)
    t = np.ascontiguousarray(triangles, dtype=np.int32)
    lo = v[t[:, 0]]
    hi = lo.copy()
    for k in (1, 2):
        corner = v[t[:, k]]
        np.minimum(lo, corner, out=lo)
        np.maximum(hi, corner, out=hi)
    del corner
    size = np.median((hi - lo).max(axis=1))
    span = float((hi.max(axis=0) - lo.min(axis=0)).max())
    cell = max(float(size) * 2.0, span / 2**20, 1e-9)
    eps = 1e-9
    gmin = lo.min(axis=0)
    dims = (np.floor((hi.max(axis=0) - gmin) / cell).astype(np.int64) + 1)
    counts = _cell_counts(lo, hi, cell, gmin)
    big = counts > MAX_CELLS_PER_TRIANGLE
    counts[big] = 0
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    total = int(counts.sum())
    keys = np.empty(total, dtype=np.int64)
    ids = np.empty(total, dtype=np.int32)
    _fill_cells(lo, hi, cell, gmin, dims, starts, keys, ids, big)
    order = np.argsort(keys)
    keys = keys[order]
    ids = ids[order]
    del order
    boundaries = np.flatnonzero(np.concatenate([[True], keys[1:] != keys[:-1]]))
    runs = np.append(boundaries, total).astype(np.int64)
    found = _count_runs(v, t, lo, hi, cell, gmin, dims, keys, ids, runs, eps)
    pairs = int(found.sum())
    samples = np.empty((SAMPLES, 2), dtype=np.int64)
    filled = 0
    for r in np.flatnonzero(found):
        filled = _samples_in_run(v, t, lo, hi, cell, gmin, dims, keys, ids, runs[r], runs[r + 1], eps, samples, filled)
        if filled >= SAMPLES:
            break
    big_ids = np.flatnonzero(big).astype(np.int64)
    if len(big_ids):
        pairs += int(_big_against_all(v, t, lo, hi, big_ids, big, eps).sum())
    pts = [((v[t[i]].mean(axis=0) + v[t[j]].mean(axis=0)) / 2 + centre).round(3).tolist() for i, j in samples[:filled]]
    return {"pairs": pairs, "samples": pts, "big_triangles": int(len(big_ids))}
