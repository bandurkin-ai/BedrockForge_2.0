"""«Исправить каркас», step `holes` (ADR 0015 §7): boundary loops closed without new vertices.

Loops of free edges are traced around the fans of their vertices (`repair_kernels.boundary_loops`); a loop that
passes twice through a vertex (two holes touching there) is cut into simple loops. Each loop is triangulated:

  * earcut in the best-fit plane (SVD) when the projection is a simple polygon — the triangles tile its area
    exactly then, which is checked; exact for planar holes, collinear vertices of the loop included (a patch
    triangle without area that earcut may leave is flipped away by the caller);
  * otherwise ear clipping in 3D (the sharpest corner turning the way the loop does is cut first);
  * a fan around a new vertex at the centroid only for loops too long for the ear clipping (`EAR_CLIP_LIMIT`).

The largest loop of a connected part (by the diagonal of its box) that spans at least `OPEN_SURFACE_SPAN` of the
part's diagonal is the outer boundary of an open surface (a DTM, a fault) and is left open, unless asked
otherwise (`keep_outer=False`). Loops longer than `max_hole_edges` and loops through non-manifold edges stay
open too; the report counts each kind.

Ported from BF `geo/repair.py` (`_fill_holes`, `_fill_polygon`, `_split_pinched`, `_boundary_loops`,
`_ear_clip`) without changes to the algorithms; progress between loops.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from bf_core.geo.repair import kernels

OPEN_SURFACE_SPAN = 0.9  # the largest loop of a part is its outer boundary when its diagonal reaches this share
EAR_CLIP_LIMIT = 20_000  # loops longer than this are not ear-clipped in 3D (quadratic time): a centroid fan
TILE_REL = 1e-6  # the triangles of earcut tile the polygon when their areas add up to its area to this share
TICK_LOOPS = 256  # loops closed between two calls of `progress`

Progress = Callable[[float], None] | None


def fill_polygon(pts: np.ndarray) -> tuple[np.ndarray | None, bool]:
    """Triangles (local indices, wound like the polygon) closing a 3D loop; (None, False) when the loop is too
    long for the 3D fallback and needs a centroid fan."""
    import mapbox_earcut

    n = len(pts)
    if n == 3:
        return np.array([[0, 1, 2]], dtype=np.int64), True
    q = pts - pts.mean(axis=0)
    normal = np.cross(q, np.roll(q, -1, axis=0)).sum(axis=0)  # twice the area vector of the loop
    scale = float(np.abs(q).max())
    if scale > 0 and np.linalg.norm(normal) > 1e-12 * scale * scale:
        _, _, axes = np.linalg.svd(q, full_matrices=False)
        xy = np.ascontiguousarray(q @ axes[:2].T)
        tri = mapbox_earcut.triangulate_float64(xy, np.array([n], dtype=np.uint32)).reshape(-1, 3).astype(np.int64)
        if len(tri) == n - 2:
            x, y = xy[:, 0], xy[:, 1]
            polygon = 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
            a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
            signed = 0.5 * ((x[b] - x[a]) * (y[c] - y[a]) - (x[c] - x[a]) * (y[b] - y[a]))
            # the projection is a simple polygon exactly when the triangles tile its area
            if abs(np.abs(signed).sum() - abs(polygon)) <= TILE_REL * abs(polygon):
                wrong = signed * polygon < 0
                tri[wrong] = tri[wrong][:, [0, 2, 1]]
                return tri, True
    if n > EAR_CLIP_LIMIT:
        return None, False
    return kernels().ear_clip(np.ascontiguousarray(q), normal), True


def split_pinched(
    starts: np.ndarray, verts: np.ndarray, ok: np.ndarray, first: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Loops passing twice through a vertex (two holes touching there) cut into simple loops."""
    loops = len(starts) - 1
    loop_of = np.repeat(np.arange(loops), np.diff(starts))
    o = np.lexsort((verts, loop_of))
    repeated = (loop_of[o][1:] == loop_of[o][:-1]) & (verts[o][1:] == verts[o][:-1])
    pinched = np.unique(loop_of[o][1:][repeated])
    if len(pinched) == 0:
        return starts, verts, ok, first
    rings: list[np.ndarray] = []
    flags: list[bool] = []
    firsts: list[int] = []
    is_pinched = np.zeros(loops, dtype=bool)
    is_pinched[pinched] = True
    for i in range(loops):
        ring = verts[starts[i] : starts[i + 1]]
        if not is_pinched[i] or not ok[i]:
            rings.append(ring)
            flags.append(bool(ok[i]))
            firsts.append(int(first[i]))
            continue
        stack: list[int] = []
        on_stack: dict[int, int] = {}
        for x in ring.tolist():
            if x in on_stack:
                at = on_stack[x]
                part = stack[at:]
                for y in part[1:]:
                    del on_stack[y]
                del stack[at + 1 :]
                rings.append(np.array(part, dtype=np.int32))
                flags.append(True)
                firsts.append(int(first[i]))
            else:
                on_stack[x] = len(stack)
                stack.append(x)
        rings.append(np.array(stack, dtype=np.int32))
        flags.append(True)
        firsts.append(int(first[i]))
    new_starts = np.concatenate([[0], np.cumsum([len(r) for r in rings])]).astype(np.int64)
    return new_starts, np.concatenate(rings), np.array(flags), np.array(firsts, dtype=np.int64)


def fill_holes(
    v: np.ndarray,
    vc: np.ndarray,
    t: np.ndarray,
    adj: np.ndarray,
    comp: np.ndarray,
    parts: int,
    max_hole_edges: int | None = None,
    keep_outer: bool = True,
    progress: Progress = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Closes the holes of a mesh (module docstring). `vc` — the vertices from the centre of the extent, `adj` —
    the twins of the half-edges, `comp` and `parts` — the parts of `repair_kernels.propagate`.

    Returns (vertices — the input ones and the centroids after them, triangles — the input ones and the patches
    after them, {filled, skipped_outer, skipped_large, skipped_complex, added_vertices})."""
    k = kernels()
    info = {"filled": 0, "skipped_outer": 0, "skipped_large": 0, "skipped_complex": 0, "added_vertices": 0}
    starts, loop_verts, ok, first = k.boundary_loops(t, adj)
    if len(starts) == 1:
        return v, t, info
    starts, loop_verts, ok, first = split_pinched(starts, loop_verts, ok, first)
    loops = len(starts) - 1
    sizes = np.diff(starts)
    points = vc[loop_verts]
    lo = np.minimum.reduceat(points, starts[:-1], axis=0)
    hi = np.maximum.reduceat(points, starts[:-1], axis=0)
    diag = np.linalg.norm(hi - lo, axis=1)
    part = comp[first // 3]
    outer = np.zeros(loops, dtype=bool)
    if keep_outer:
        plo, phi = k.part_boxes(vc, t, comp, parts)
        part_diag = np.linalg.norm(phi - plo, axis=1)
        o = np.lexsort((-diag, part))  # the largest loop of every part, if it spans the part
        largest = o[np.concatenate([[True], part[o][1:] != part[o][:-1]])]
        outer[largest] = diag[largest] >= OPEN_SURFACE_SPAN * part_diag[part[largest]]
    new_tris: list[np.ndarray] = []
    new_points: list[np.ndarray] = []
    base = len(v)
    for i in range(loops):
        if progress and i % TICK_LOOPS == 0:
            progress(i / loops)
        size = int(sizes[i])
        if outer[i]:
            info["skipped_outer"] += 1
            continue
        if not ok[i] or size < 3:
            info["skipped_complex"] += 1
            continue
        if max_hole_edges is not None and size > max_hole_edges:
            info["skipped_large"] += 1
            continue
        # the mesh walks the loop forwards, so the patch must walk it backwards
        ring = loop_verts[starts[i] : starts[i + 1]][::-1]
        tri, done = fill_polygon(vc[ring])
        if done and tri is not None:
            new_tris.append(ring[tri])
        else:
            centre = base + len(new_points)
            new_points.append(v[ring].mean(axis=0))
            new_tris.append(np.column_stack([ring, np.roll(ring, -1), np.full(size, centre)]))
        info["filled"] += 1
    if new_tris:
        t = np.ascontiguousarray(np.concatenate([t, *new_tris]), dtype=np.int32)
    if new_points:
        v = np.ascontiguousarray(np.concatenate([v, np.array(new_points)]))
        info["added_vertices"] = len(new_points)
    if progress:
        progress(1.0)
    return v, t, info
