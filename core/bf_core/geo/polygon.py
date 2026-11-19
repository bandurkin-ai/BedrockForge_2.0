"""Planar polygons in 3D for the operations of Э9 (ADR 0015 §7): best-fit plane, triangulation of a cap.

    area_vector(points)        Newell's vector: the normal of the best-fit plane times the area
    plane_axes(normal)         unit u, v with u × v = normal
    signed_area(q), centroid(q)    of a 2D polygon
    clean_polygon(p, what)     (k, 3) float64 without a repeated last vertex; StitchError otherwise
    triangulate(q)             triangles of a simple 2D polygon, wound like it, every vertex used
    cap_triangles(polygon)     triangulation of a (non-convex) 3D polygon in its best-fit plane
    StitchError                the error of stitching, extrusion and triangle edits: {code, message, hint}
                               in Russian and `between` — the pair of sections where the problem is

Triangulation. The strips next to a cap use every vertex of its outline, collinear and repeated ones too, so
a cap must have exactly k − 2 triangles that walk every outline edge once. mapbox-earcut (ISC) is tried
first; the vertices it leaves out (it drops a vertex equal to its neighbour) lie on an edge of one of its
triangles and are put back by a fan from that triangle's third corner. If its answer still does not cover
the outline (a self-touching outline), the ear clipping below is used: it always gives k − 2 triangles —
where no true ear exists it cuts the least harmful corner, so the cap stays watertight even where it is
degenerate. Ear clipping is O(k²); it is the rare path.

Ported from BF `geo/stitch.py` (planes and polygons, `_ear_clip`, `_covers_outline`, `cap_triangles`).
Differences: earcut instead of the triangulator of manifold3d (ADR 0015 §7), the fan that restores dropped
vertices, errors with a code and a hint. The kernel releases the GIL; numba is imported with this module.
"""

from __future__ import annotations

import numpy as np

from bf_core.geo.meshes import MeshError
from bf_core.jit import njit

HINT = "Проверьте контуры и параметры операции."


class StitchError(MeshError):
    """Message in Russian, shown to the user; `code` and `hint` as in every error of bf-core.

    `between` = (section a, section b): zero-based indices of the sections (as in `report["sections"]`)
    where the problem is; (k, k) means the cap of section k; None if unknown.
    """

    def __init__(
        self, message: str, between: tuple[int, int] | None = None, *, code: str = "stitch_failed", hint: str = HINT
    ) -> None:
        super().__init__(code, message, hint)
        self.between = between


# --- planes and polygons -----------------------------------------------------------------------------------


def area_vector(points: np.ndarray) -> np.ndarray:
    """Newell's formula: the normal of the best-fit plane scaled by the polygon's area."""
    p = points - points.mean(axis=0)
    return np.asarray(0.5 * np.cross(p, np.roll(p, -1, axis=0)).sum(axis=0))


def plane_axes(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Unit u, v with u × v = normal: a polygon counter-clockwise in (u, v) turns about the normal."""
    k = int(np.argmin(np.abs(normal)))
    seed = np.zeros(3)
    seed[k] = 1.0
    u = np.cross(seed, normal)
    u /= np.linalg.norm(u)
    return u, np.cross(normal, u)


def signed_area(q: np.ndarray) -> float:
    x, y = q[:, 0], q[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def centroid(q: np.ndarray) -> np.ndarray:
    """Area centroid of a 2D polygon (mean of the vertices if it has no area)."""
    o = q.mean(axis=0)
    p = q - o
    n = np.roll(p, -1, axis=0)
    w = p[:, 0] * n[:, 1] - n[:, 0] * p[:, 1]
    total = w.sum()
    scale = np.abs(p).max() ** 2
    if abs(total) <= 1e-12 * scale or scale == 0.0:
        return np.asarray(o)
    return np.asarray(o + ((p + n) * w[:, None]).sum(axis=0) / (3.0 * total))


def clean_polygon(polygon: object, what: str) -> np.ndarray:
    """(k, 3) float64, k ≥ 3, finite; a repeated last vertex (bit for bit) is dropped."""
    p = np.asarray(polygon, dtype=np.float64)
    if p.ndim != 2 or p.shape[1] != 3:
        raise StitchError(f"{what}: ожидается массив координат (k, 3).", code="bad_polygon")
    if not np.isfinite(p).all():
        raise StitchError(
            f"{what}: в координатах есть пропуски.", code="bad_polygon", hint="Задайте Z всем вершинам контура."
        )
    if len(p) > 1 and np.array_equal(p[0], p[-1]):
        p = p[:-1]
    if len(p) < 3:
        raise StitchError(f"{what}: нужно не меньше трёх вершин.", code="bad_polygon")
    return p


# --- triangulation -----------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def ear_clip(xy):  # type: ignore[no-untyped-def]
    """Ear clipping of a counter-clockwise polygon; always returns n − 2 triangles.

    An ear is a strictly convex corner with no other vertex inside or on it. When there is none
    (collinear runs, a damaged outline) the least harmful corner is cut, so the outline's edges
    are still each used once — the cap stays watertight even where it is degenerate.
    """
    n = len(xy)
    out = np.empty((n - 2, 3), dtype=np.int64)
    nxt = np.empty(n, dtype=np.int64)
    prv = np.empty(n, dtype=np.int64)
    for i in range(n):
        nxt[i] = (i + 1) % n
        prv[i] = (i - 1) % n
    left = n
    cur = 0
    k = 0
    while left > 3:
        found = -1
        fallback = -1
        fallback_cross = -np.inf
        b = cur
        for _ in range(left):
            a, c = prv[b], nxt[b]
            cross = (xy[b, 0] - xy[a, 0]) * (xy[c, 1] - xy[a, 1]) - (xy[b, 1] - xy[a, 1]) * (xy[c, 0] - xy[a, 0])
            if cross > 0.0:
                blocked = False
                p = nxt[c]
                while p != a:
                    px, py = xy[p, 0], xy[p, 1]
                    d1 = (xy[b, 0] - xy[a, 0]) * (py - xy[a, 1]) - (xy[b, 1] - xy[a, 1]) * (px - xy[a, 0])
                    d2 = (xy[c, 0] - xy[b, 0]) * (py - xy[b, 1]) - (xy[c, 1] - xy[b, 1]) * (px - xy[b, 0])
                    d3 = (xy[a, 0] - xy[c, 0]) * (py - xy[c, 1]) - (xy[a, 1] - xy[c, 1]) * (px - xy[c, 0])
                    if d1 >= 0.0 and d2 >= 0.0 and d3 >= 0.0:
                        same = (
                            (px == xy[a, 0] and py == xy[a, 1])
                            or (px == xy[b, 0] and py == xy[b, 1])
                            or (px == xy[c, 0] and py == xy[c, 1])
                        )
                        if not same:
                            blocked = True
                            break
                    p = nxt[p]
                if not blocked:
                    found = b
                    break
            # Without a true ear: prefer a flat corner (zero-area triangle), then the least reflex.
            score = -abs(cross) if cross <= 0.0 else -np.inf
            if score > fallback_cross:
                fallback_cross = score
                fallback = b
            b = nxt[b]
        if found < 0:
            found = fallback if fallback >= 0 else cur
        a, c = prv[found], nxt[found]
        out[k, 0], out[k, 1], out[k, 2] = a, found, c
        k += 1
        nxt[a] = c
        prv[c] = a
        cur = c
        left -= 1
    out[k, 0], out[k, 1], out[k, 2] = prv[cur], cur, nxt[cur]
    return out


def covers_outline(tris: np.ndarray, n: int) -> bool:
    """n − 2 triangles that use every outline edge i→i+1 once and no directed edge twice."""
    if tris.shape != (n - 2, 3) or tris.min() < 0 or tris.max() >= n:
        return False
    t = tris.astype(np.int64)
    keys = np.concatenate([t[:, 0] * n + t[:, 1], t[:, 1] * n + t[:, 2], t[:, 2] * n + t[:, 0]])
    if len(np.unique(keys)) != len(keys):
        return False
    i = np.arange(n, dtype=np.int64)
    return bool(np.isin(i * n + (i + 1) % n, keys).all())


def _restore_dropped(tris: np.ndarray, n: int) -> np.ndarray | None:
    """The vertices earcut left out put back by fans: a run x₁…x_k skipped between kept a and b lies on the
    edge a→b of one triangle (a, b, c), which becomes (a, x₁, c), (x₁, x₂, c), …, (x_k, b, c)."""
    kept = np.unique(tris)
    if len(kept) < 3:
        return None
    rows = [list(map(int, t)) for t in tris]
    where: dict[tuple[int, int], int] = {}
    for r, (x, y, z) in enumerate(rows):
        for a, b in ((x, y), (y, z), (z, x)):
            where[(a, b)] = r
    out: list[list[int]] = []
    replaced: set[int] = set()
    for k, a in enumerate(kept.tolist()):
        b = int(kept[(k + 1) % len(kept)])
        run = list(range(a + 1, b if b > a else b + n))
        if not run:
            continue
        hit = where.get((a, b))
        if hit is None or hit in replaced:
            return None
        replaced.add(hit)
        x, y, z = rows[hit]
        c = z if (x, y) == (a, b) else x if (y, z) == (a, b) else y
        chain = [a, *(i % n for i in run), b]
        out += [[chain[i], chain[i + 1], c] for i in range(len(chain) - 1)]
    out += [t for r, t in enumerate(rows) if r not in replaced]
    return np.asarray(out, dtype=np.int64).reshape(-1, 3)


def _earcut(p: np.ndarray) -> np.ndarray | None:
    import mapbox_earcut

    n = len(p)
    got = mapbox_earcut.triangulate_float64(p, np.array([n], dtype=np.uint32))
    tris = np.asarray(got, dtype=np.int64).reshape(-1, 3)
    if len(tris) == 0:
        return None
    if covers_outline(tris, n):
        return tris
    fixed = _restore_dropped(tris, n)
    return fixed if fixed is not None and covers_outline(fixed, n) else None


def triangulate(q: np.ndarray) -> np.ndarray:
    """Triangles (int64 indices into q) of a simple 2D polygon, wound like the polygon itself, every vertex of
    the outline used (see the module docstring)."""
    n = len(q)
    if n == 3:
        return np.array([[0, 1, 2]], dtype=np.int64)
    ccw = signed_area(q) >= 0.0
    p = np.ascontiguousarray(q if ccw else q[::-1], dtype=np.float64)
    p = np.ascontiguousarray(p - p.mean(axis=0))
    tris = _earcut(p)
    if tris is None:
        tris = np.asarray(ear_clip(p), dtype=np.int64)
    if not ccw:
        tris = (n - 1 - tris)[:, ::-1]
    return np.ascontiguousarray(tris)


def cap_triangles(polygon: np.ndarray) -> np.ndarray:
    """(k − 2, 3) int32 indices into `polygon`: its triangulation in the best-fit plane.

    Works for non-convex polygons; the triangles are wound like the polygon. A repeated last
    vertex must not be passed (indices refer to the array as given).
    """
    p = np.asarray(polygon, dtype=np.float64)
    if p.ndim != 2 or p.shape[1] != 3 or len(p) < 3:
        raise StitchError("Полигон: нужно не меньше трёх вершин (k, 3).", code="bad_polygon")
    if not np.isfinite(p).all():
        raise StitchError("Полигон: в координатах есть пропуски.", code="bad_polygon")
    area = area_vector(p)
    length = float(np.linalg.norm(area))
    if length == 0.0:
        raise StitchError("Полигон вырожден: у него нулевая площадь.", code="degenerate_polygon")
    normal = area / length
    u, v = plane_axes(normal)
    c = p - p.mean(axis=0)
    return triangulate(np.column_stack([c @ u, c @ v])).astype(np.int32)
