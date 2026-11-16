"""Query by click: the first triangle of a mesh hit by a ray, from the exact float64 arrays."""

from __future__ import annotations

import math

import numpy as np
from numba import njit, prange


@njit(parallel=True, cache=True)
def _ray_triangles(v, t, o, d, first, count, eps):
    """Möller–Trumbore over triangles [first, first + count): (distance along the ray, index) of
    the nearest hit, (inf, -1) when there is none. Split into blocks for the threads."""
    blocks = max(1, min(64, count // 4096))
    best_t = np.full(blocks, np.inf)
    best_i = np.full(blocks, -1, dtype=np.int64)
    size = (count + blocks - 1) // blocks
    for b in prange(blocks):
        lo = first + b * size
        hi = min(first + count, lo + size)
        bt = np.inf
        bi = -1
        for i in range(lo, hi):
            a0, a1, a2 = v[t[i, 0], 0], v[t[i, 0], 1], v[t[i, 0], 2]
            e1x, e1y, e1z = v[t[i, 1], 0] - a0, v[t[i, 1], 1] - a1, v[t[i, 1], 2] - a2
            e2x, e2y, e2z = v[t[i, 2], 0] - a0, v[t[i, 2], 1] - a1, v[t[i, 2], 2] - a2
            px = d[1] * e2z - d[2] * e2y
            py = d[2] * e2x - d[0] * e2z
            pz = d[0] * e2y - d[1] * e2x
            det = e1x * px + e1y * py + e1z * pz
            if abs(det) < eps:
                continue
            inv = 1.0 / det
            sx, sy, sz = o[0] - a0, o[1] - a1, o[2] - a2
            u = (sx * px + sy * py + sz * pz) * inv
            if u < -1e-12 or u > 1.0 + 1e-12:
                continue
            qx = sy * e1z - sz * e1y
            qy = sz * e1x - sx * e1z
            qz = sx * e1y - sy * e1x
            w = (d[0] * qx + d[1] * qy + d[2] * qz) * inv
            if w < -1e-12 or u + w > 1.0 + 1e-12:
                continue
            dist = (e2x * qx + e2y * qy + e2z * qz) * inv
            if dist >= 0.0 and dist < bt:
                bt = dist
                bi = i
        best_t[b] = bt
        best_i[b] = bi
    k = np.argmin(best_t)
    return best_t[k], best_i[k]


def _ray_box(o: np.ndarray, inv: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float | None:
    """Entry distance of the ray into the box, or None when it misses (slab test)."""
    with np.errstate(invalid="ignore"):
        t1, t2 = (lo - o) * inv, (hi - o) * inv
    near = np.nanmax(np.minimum(t1, t2))
    far = np.nanmin(np.maximum(t1, t2))
    return float(max(near, 0.0)) if far >= max(near, 0.0) else None


def ray_pick(vertices: np.ndarray, triangles: np.ndarray, origin, direction, *, chunks: list | None = None,
             mesh_origin=None) -> dict | None:
    """The nearest triangle crossed by the ray `origin + s * direction`, s >= 0 (project coordinates).

    `chunks` is the display structure of a stored wireframe ([first, count, min xyz, max xyz]
    relative to `mesh_origin`): only the parts whose box the ray enters are tested, nearest first.
    Returns the triangle's index, its exact corners, the hit point, unit normal, area, dip and
    dip direction (degrees), or None.
    """
    o = np.asarray(origin, dtype=np.float64)
    d = np.asarray(direction, dtype=np.float64)
    length = float(np.linalg.norm(d))
    if not length or not np.isfinite(o).all():
        return None
    d = d / length
    v = np.asarray(vertices)
    t = np.asarray(triangles)
    eps = 1e-300
    spans: list[tuple[float, int, int]] = []
    if chunks:
        mo = np.asarray(mesh_origin if mesh_origin is not None else [0.0, 0.0, 0.0], dtype=np.float64)
        with np.errstate(divide="ignore"):
            inv = 1.0 / d
        for c in chunks:
            lo, hi = np.asarray(c[2:5], dtype=np.float64) + mo - 1e-3, np.asarray(c[5:8], dtype=np.float64) + mo + 1e-3
            # A ray parallel to a slab and outside it misses; inside, that axis does not limit it.
            if np.any((d == 0) & ((o < lo) | (o > hi))):
                continue
            entry = _ray_box(o, inv, lo, hi)
            if entry is not None:
                spans.append((entry, int(c[0]), int(c[1])))
        spans.sort()
    else:
        spans.append((0.0, 0, len(t)))
    best_s, best_i = math.inf, -1
    for entry, first, count in spans:
        if entry > best_s:
            break  # every later part starts behind the hit already found
        s, i = _ray_triangles(v, t, o, d, first, count, eps)
        if s < best_s:
            best_s, best_i = float(s), int(i)
    if best_i < 0:
        return None
    corners = np.asarray(v[t[best_i]], dtype=np.float64)
    n = np.cross(corners[1] - corners[0], corners[2] - corners[0])
    area = 0.5 * float(np.linalg.norm(n))
    unit = n / (2 * area) if area else np.zeros(3)
    up = unit if unit[2] >= 0 else -unit
    dip = math.degrees(math.acos(min(1.0, abs(float(unit[2]))))) if area else 0.0
    dip_direction = (math.degrees(math.atan2(up[0], up[1])) + 360.0) % 360.0 if area and dip > 1e-9 else 0.0
    return {
        "triangle": best_i, "distance": best_s, "point": (o + d * best_s).tolist(),
        "vertices": corners.tolist(), "indices": [int(x) for x in t[best_i]],
        "normal": unit.tolist(), "area": area, "dip": dip, "dip_direction": dip_direction,
    }
