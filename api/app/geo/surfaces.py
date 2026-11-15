"""Surfaces: digital terrain models, contours and volumes (pure geometry, no database).

    build_dtm(points, breaklines, boundary=, max_edge=)   constrained Delaunay TIN in plan
    contours(v, t, interval=, base=, major_every=)        isolines, chained and ordered
    surface_footprint(v, t)                               boundary loops of a surface in plan
    under_solid(v, t, base_z)                             closed solid between a surface and a level
    volume_between(top, bottom, polygon=)                 cut / fill between two surfaces
    volume_to_level(surface, level, polygon=)             cut / fill against a horizontal plane

Libraries (all usable in closed commercial code):
  * PythonCDT 2.0.2 — binding of artem-ogre/CDT (MPL-2.0): constrained Delaunay triangulation
    with exact predicates. One million points take about 2 s. Shewchuk's Triangle is not used.
  * manifold3d (Apache-2.0) — boolean operations for the volumes between surfaces.
  * mapbox_earcut (ISC) — caps of the prism of a limiting polygon.

Coordinates are float64 project coordinates (UTM-size numbers). Every calculation runs on
coordinates shifted to a local integer origin; the vertices of the results that come from the
input keep their X, Y, Z bit for bit.

User-facing messages (SurfaceError) are in Russian.
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit

# Plan tolerance relative to the extent of the data: points closer than this coincide.
REL_TOLERANCE = 1e-9
# Limits that protect from a mistyped contour interval.
MAX_LEVELS = 1_000_000
MAX_CONTOUR_SEGMENTS = 400_000_000
# A triangle whose normal is closer than this to horizontal counts as vertical (relative |nz|/|n|).
_VERTICAL = 1e-9

DTM_METHOD = "PythonCDT 2.0.2 (artem-ogre/CDT, MPL-2.0): constrained Delaunay triangulation in plan"
VOLUME_METHOD = ("manifold3d: intersections of the solid under one surface with the solid above the "
                 "other (exact for the two TINs)")
LEVEL_METHOD = "prismatic: every triangle split by the level plane (exact for the TIN)"


class SurfaceError(ValueError):
    """A surface cannot be built or measured; the message (Russian) is shown to the user."""


def _tick(progress, value: float) -> None:
    if progress:
        progress(float(value))


def _fmt(value: float) -> str:
    return f"{value:.3f}"


# ------------------------------------------------------------------ coincident points


@njit(cache=True)
def _find(parent, i):
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


@njit(cache=True)
def _cluster(xs, ys, order, tol):
    """Union of points closer than tol in both X and Y. xs, ys: sorted by (x, y); order: their ids.

    Returns for every id the smallest id of its cluster.
    """
    n = len(xs)
    parent = np.arange(n)
    for i in range(n):
        j = i + 1
        while j < n and xs[j] - xs[i] <= tol:
            dy = ys[j] - ys[i]
            if xs[j] == xs[i] and dy > tol:
                # The rest of this run of equal X is even further in Y (gridded data: long runs).
                j = j + np.searchsorted(xs[j:], xs[i], side="right")
                continue
            if abs(dy) <= tol:
                ra = _find(parent, order[i])
                rb = _find(parent, order[j])
                if ra < rb:
                    parent[rb] = ra
                elif rb < ra:
                    parent[ra] = rb
            j += 1
    for i in range(n):
        parent[i] = _find(parent, i)
    return parent


def _merge(x, y, z, cx, cy, tol, ztol, message):
    """Clusters of vertices coinciding in plan → representative ids; a Z conflict raises.

    A vertex without Z (NaN, from a plan-only boundary) takes the Z of its cluster.
    Returns (rep, number of merged vertices).
    """
    n = len(cx)
    order = np.lexsort((cy, cx))
    rep = _cluster(np.ascontiguousarray(cx[order]), np.ascontiguousarray(cy[order]), order, tol)
    members = np.nonzero(rep != np.arange(n))[0]
    if len(members) == 0:
        return rep, 0
    zlo = z.copy()
    zhi = z.copy()
    np.fmin.at(zlo, rep[members], z[members])
    np.fmax.at(zhi, rep[members], z[members])
    bad = np.nonzero(zhi - zlo > ztol)[0]
    if len(bad):
        examples = []
        for b in bad[:5]:
            zs = np.unique(z[np.nonzero(rep == b)[0]])
            zs = zs[~np.isnan(zs)]
            examples.append(f"X={_fmt(x[b])} Y={_fmt(y[b])}: Z = " + ", ".join(_fmt(v) for v in zs))
        raise SurfaceError(f"{message}: {len(bad)} шт. Примеры: " + "; ".join(examples)
                           + ". Исправьте исходные данные — отметка не выбирается автоматически.")
    roots = np.unique(rep[members])
    roots = roots[np.isnan(z[roots])]
    z[roots] = zlo[roots]
    return rep, len(members)


# ------------------------------------------------------------------ crossings of constraints


@njit(cache=True)
def _on_segment(px, py, ax, ay, bx, by, tol):
    """Parameter t in (0, 1) when the point lies on the segment within tol, else -1."""
    dx = bx - ax
    dy = by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0.0:
        return -1.0
    t = ((px - ax) * dx + (py - ay) * dy) / l2
    if t <= 0.0 or t >= 1.0:
        return -1.0
    if abs((px - ax) * dy - (py - ay) * dx) > tol * math.sqrt(l2):
        return -1.0
    return t


@njit(cache=True)
def _segment_events(cx, cy, sa, sb, order, xmin, xmax, tol, cap):
    """Pairs of constraint segments that touch or cross (sweep over segments sorted by min X).

    Touch: an end of one segment lies inside another → (segment, vertex, t).
    Cross: two interiors intersect → (segment i, segment j, t on i, t on j).
    Arrays have room for `cap` events; the true counts are returned (retry when larger).
    """
    t_seg = np.empty(cap, np.int64)
    t_vtx = np.empty(cap, np.int64)
    t_par = np.empty(cap, np.float64)
    c_i = np.empty(cap, np.int64)
    c_j = np.empty(cap, np.int64)
    c_ti = np.empty(cap, np.float64)
    c_tj = np.empty(cap, np.float64)
    nt = 0
    nc = 0
    m = len(order)
    for ii in range(m):
        i = order[ii]
        a = sa[i]
        b = sb[i]
        ax = cx[a]
        ay = cy[a]
        bx = cx[b]
        by = cy[b]
        lo_i = min(ay, by) - tol
        hi_i = max(ay, by) + tol
        limit = xmax[i] + tol
        for jj in range(ii + 1, m):
            j = order[jj]
            if xmin[j] > limit:
                break
            c = sa[j]
            d = sb[j]
            px = cx[c]
            py = cy[c]
            qx = cx[d]
            qy = cy[d]
            if min(py, qy) > hi_i or max(py, qy) < lo_i:
                continue
            touched = False
            for side in range(4):
                if side == 0:
                    v = c
                    s = i
                    skip = c == a or c == b
                    t = -1.0 if skip else _on_segment(px, py, ax, ay, bx, by, tol)
                elif side == 1:
                    v = d
                    s = i
                    skip = d == a or d == b
                    t = -1.0 if skip else _on_segment(qx, qy, ax, ay, bx, by, tol)
                elif side == 2:
                    v = a
                    s = j
                    skip = a == c or a == d
                    t = -1.0 if skip else _on_segment(ax, ay, px, py, qx, qy, tol)
                else:
                    v = b
                    s = j
                    skip = b == c or b == d
                    t = -1.0 if skip else _on_segment(bx, by, px, py, qx, qy, tol)
                if t > 0.0:
                    touched = True
                    if nt < cap:
                        t_seg[nt] = s
                        t_vtx[nt] = v
                        t_par[nt] = t
                    nt += 1
            if touched or a == c or a == d or b == c or b == d:
                continue
            d1 = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
            d2 = (bx - ax) * (qy - ay) - (by - ay) * (qx - ax)
            d3 = (qx - px) * (ay - py) - (qy - py) * (ax - px)
            d4 = (qx - px) * (by - py) - (qy - py) * (bx - px)
            if ((d1 > 0.0 and d2 < 0.0) or (d1 < 0.0 and d2 > 0.0)) and \
                    ((d3 > 0.0 and d4 < 0.0) or (d3 < 0.0 and d4 > 0.0)):
                if nc < cap:
                    c_i[nc] = i
                    c_j[nc] = j
                    c_ti[nc] = d3 / (d3 - d4)
                    c_tj[nc] = d1 / (d1 - d2)
                nc += 1
    return nt, nc, t_seg, t_vtx, t_par, c_i, c_j, c_ti, c_tj


# ------------------------------------------------------------------ triangulation helpers


def _cdt(cx, cy, edges, tol):
    """Constrained Delaunay triangulation → (triangles (m,3) int64 CCW, neighbours (m,3) int64, fixed edges).

    neighbours[t, e] is the triangle across the edge (t[e], t[e+1]); -1 on the hull.
    """
    import pythoncdt as cdt

    tri = cdt.Triangulation(cdt.VertexInsertionOrder.AUTO, cdt.IntersectingConstraintEdges.NOT_ALLOWED, float(tol))
    try:
        tri.insert_vertices(np.ascontiguousarray(np.column_stack([cx, cy])))
        if len(edges):
            tri.insert_edges(np.ascontiguousarray(edges, dtype=np.uint32))
    except RuntimeError as exc:
        raise SurfaceError("Не удалось построить триангуляцию: структурные линии или граница пересекаются "
                           "так, что пересечение не удалось разрешить. Проверьте линии на самопересечения "
                           f"и наложения. ({str(exc).splitlines()[0]})") from exc
    tri.erase_super_triangle()
    arr = tri.triangles_array()
    t = arr["vertices"].astype(np.int64)
    nb = arr["neighbors"].astype(np.int64)
    nb[nb == int(cdt.NO_NEIGHBOR)] = -1
    fixed = np.array([(e.v1, e.v2) for e in tri.fixed_edges_iter()], dtype=np.int64).reshape(-1, 2) \
        if len(edges) else np.zeros((0, 2), np.int64)
    if len(t):
        # CDT returns counter-clockwise triangles; keep the code independent of that.
        a, b, c = t[:, 0], t[:, 1], t[:, 2]
        area2 = (cx[b] - cx[a]) * (cy[c] - cy[a]) - (cy[b] - cy[a]) * (cx[c] - cx[a])
        flip = area2 < 0
        if flip.any():
            t[flip] = t[flip][:, [0, 2, 1]]
            nb[flip] = nb[flip][:, [2, 1, 0]]
    return t, nb, fixed


@njit(cache=True)
def _locate(px, py, tri, nbr, qx, qy):
    """Triangle containing every query point (visibility walk, brute force as a fallback); -1 outside the hull."""
    nq = len(qx)
    nt = len(tri)
    out = np.empty(nq, np.int64)
    t = 0
    for q in range(nq):
        x = qx[q]
        y = qy[q]
        found = -2
        steps = 0
        while steps < 4 * nt + 16:
            moved = False
            for kk in range(3):
                e = (kk + steps) % 3
                a = tri[t, e]
                b = tri[t, (e + 1) % 3]
                if (px[b] - px[a]) * (y - py[a]) - (py[b] - py[a]) * (x - px[a]) < 0.0:
                    n = nbr[t, e]
                    if n < 0:
                        found = -1
                    else:
                        t = n
                    moved = True
                    break
            if not moved:
                found = t
                break
            if found == -1:
                break
            steps += 1
        if found == -2:
            found = -1
            for s in range(nt):
                inside = True
                for e in range(3):
                    a = tri[s, e]
                    b = tri[s, (e + 1) % 3]
                    if (px[b] - px[a]) * (y - py[a]) - (py[b] - py[a]) * (x - px[a]) < 0.0:
                        inside = False
                        break
                if inside:
                    found = s
                    break
        out[q] = found
        if found >= 0:
            t = found
    return out


@njit(cache=True)
def _nearest_on_edges(px, py, pz, ea, eb, qx, qy):
    """Z of the nearest point of a set of edges (linear along the edge) for every query point."""
    out = np.empty(len(qx), np.float64)
    for q in range(len(qx)):
        best = np.inf
        zq = np.nan
        for k in range(len(ea)):
            a = ea[k]
            b = eb[k]
            dx = px[b] - px[a]
            dy = py[b] - py[a]
            l2 = dx * dx + dy * dy
            t = 0.0
            if l2 > 0.0:
                t = ((qx[q] - px[a]) * dx + (qy[q] - py[a]) * dy) / l2
                t = min(1.0, max(0.0, t))
            ex = px[a] + t * dx - qx[q]
            ey = py[a] + t * dy - qy[q]
            d = ex * ex + ey * ey
            if d < best:
                best = d
                zq = pz[a] + t * (pz[b] - pz[a])
        out[q] = zq
    return out


@njit(cache=True)
def _components(nbr, fixed, area2):
    """Groups of triangles connected through non-fixed edges → (group of every triangle, largest triangle of every group)."""
    nt = len(nbr)
    comp = np.full(nt, -1, np.int64)
    stack = np.empty(nt, np.int64)
    best = np.empty(nt, np.int64)
    nc = 0
    for s in range(nt):
        if comp[s] >= 0:
            continue
        comp[s] = nc
        best[nc] = s
        top = 0
        stack[0] = s
        top = 1
        while top > 0:
            top -= 1
            t = stack[top]
            if area2[t] > area2[best[nc]]:
                best[nc] = t
            for e in range(3):
                n = nbr[t, e]
                if n >= 0 and comp[n] < 0 and not fixed[t, e]:
                    comp[n] = nc
                    stack[top] = n
                    top += 1
        nc += 1
    return comp, best[:nc]


@njit(cache=True)
def _points_in_polygon(qx, qy, rx, ry):
    out = np.zeros(len(qx), np.bool_)
    n = len(rx)
    for q in range(len(qx)):
        x = qx[q]
        y = qy[q]
        inside = False
        j = n - 1
        for i in range(n):
            if (ry[i] > y) != (ry[j] > y) and x < rx[i] + (y - ry[i]) * (rx[j] - rx[i]) / (ry[j] - ry[i]):
                inside = not inside
            j = i
        out[q] = inside
    return out


@njit(cache=True)
def _peel_long(nbr, alive, long_, fixed):
    """Drops long triangles reachable from the outside through long triangles, never through a
    constraint edge (`fixed`). Returns their number."""
    nt = len(nbr)
    stack = np.empty(nt, np.int64)
    queued = np.zeros(nt, np.bool_)
    top = 0
    for t in range(nt):
        if not alive[t] or not long_[t]:
            continue
        for e in range(3):
            n = nbr[t, e]
            if (n < 0 or not alive[n]) and not fixed[t, e]:
                stack[top] = t
                top += 1
                queued[t] = True
                break
    dropped = 0
    while top > 0:
        top -= 1
        t = stack[top]
        alive[t] = False
        dropped += 1
        for e in range(3):
            n = nbr[t, e]
            if n >= 0 and alive[n] and long_[n] and not queued[n] and not fixed[t, e]:
                queued[n] = True
                stack[top] = n
                top += 1
    return dropped


def _as_xyz(array, name: str, columns=(3,)) -> np.ndarray:
    a = np.asarray(array, dtype=np.float64)
    if a.ndim != 2 or a.shape[1] not in columns:
        raise SurfaceError(f"{name}: ожидается массив координат из {' или '.join(str(c) for c in columns)} столбцов.")
    return a


# ------------------------------------------------------------------ DTM


def build_dtm(points: np.ndarray | None, breaklines=(), *, boundary: np.ndarray | None = None,
              max_edge: float | None = None, progress=None) -> tuple[np.ndarray, np.ndarray, dict]:
    """Digital terrain model: constrained Delaunay triangulation in plan (XY).

    points      (n,3) or None.
    breaklines  list of (k,3) polylines; a closed line repeats its first vertex at the end.
                Every segment becomes an edge of the result (or a chain of edges when other
                vertices lie on it), no triangle edge crosses it. Where two lines cross at the
                same Z the crossing point becomes a vertex.
    boundary    None → convex hull. (k,2) or (k,3) polygon in plan: its edges are constraints and
                the triangles outside it are removed. With Z (k,3) it is structural like a
                breakline. Without Z (k,2) every vertex takes the Z of the surface built from the
                points and breaklines alone (linear inside a triangle; outside their convex hull —
                the Z of the nearest point of the hull), and a crossing with a breakline takes
                the Z of the breakline.
    max_edge    a triangle is dropped when it has a plan edge longer than max_edge AND it touches
                the outside by an edge — the outside being the convex hull or a triangle already
                dropped. So long triangles are peeled from the outside inward, and a long triangle
                surrounded by short ones stays (no holes are opened). Peeling never passes through
                a constraint edge: breaklines and the boundary polygon are kept as drawn, so with
                a boundary polygon (the whole outline is a constraint) max_edge removes nothing.

    Vertices coinciding in plan (closer than `tolerance` = 1e-9 of the plan extent in X and in Y):
    with the same Z (within `tolerance_z`) they merge silently, the first one is kept; with
    different Z → SurfaceError with their number and up to 5 examples. The same for breaklines
    crossing at different Z. A point lying on a breakline (within tolerance) without being its
    vertex splits the breakline there and keeps its own Z.

    Returns (vertices (m,3) float64, triangles (t,3) int32, report). Vertices keep the input order
    (points, then breakline vertices, then boundary vertices, then crossing points) without the
    merged and the unused ones; the coordinates of input vertices are bit-identical.
    Triangles are counter-clockwise in plan (normal up).
    """
    _tick(progress, 0.0)
    parts = []
    if points is not None and len(points):
        parts.append(_as_xyz(points, "Точки"))
    n_cloud = len(parts[0]) if parts else 0
    seg_a, seg_b, seg_hard = [], [], []
    offset = n_cloud
    for line in breaklines or ():
        line = _as_xyz(line, "Структурная линия")
        if len(line) == 0:
            continue
        parts.append(line)
        ids = np.arange(offset, offset + len(line))
        seg_a.append(ids[:-1])
        seg_b.append(ids[1:])
        seg_hard.append(np.ones(len(line) - 1, bool))
        offset += len(line)
    ring = None
    if boundary is not None:
        ring_xyz = _as_xyz(boundary, "Граница", columns=(2, 3))
        if len(ring_xyz) < 3:
            raise SurfaceError("Граница: нужен полигон не менее чем из трёх вершин.")
        hard_ring = ring_xyz.shape[1] == 3
        if not hard_ring:
            ring_xyz = np.column_stack([ring_xyz, np.full(len(ring_xyz), np.nan)])
        if not np.isfinite(ring_xyz[:, :2]).all() or (hard_ring and not np.isfinite(ring_xyz).all()):
            raise SurfaceError("Граница содержит нечисловые координаты.")
        parts.append(ring_xyz)
        ring = np.arange(offset, offset + len(ring_xyz))
        seg_a.append(ring)
        seg_b.append(np.roll(ring, -1))
        seg_hard.append(np.full(len(ring), hard_ring))
        offset += len(ring_xyz)
    if not parts:
        raise SurfaceError("Нет данных для построения поверхности: не заданы ни точки, ни линии.")
    xyz = np.concatenate(parts)
    n_data = offset - (len(ring) if ring is not None else 0)
    if not np.isfinite(xyz[:n_data]).all():
        bad = int(np.count_nonzero(~np.isfinite(xyz[:n_data]).all(axis=1)))
        raise SurfaceError(f"Исходные данные содержат нечисловые координаты (NaN или бесконечность): {bad} точек.")
    if max_edge is not None and not max_edge > 0:
        raise SurfaceError("Наибольшая длина ребра должна быть положительной.")

    x, y, z = (np.ascontiguousarray(xyz[:, i]) for i in range(3))
    lo = np.array([x.min(), y.min()])
    hi = np.array([x.max(), y.max()])
    extent = float((hi - lo).max())
    if not extent > 0:
        raise SurfaceError("Все точки совпадают в плане — поверхность построить нельзя.")
    origin = np.floor((lo + hi) / 2.0)
    cx = x - origin[0]
    cy = y - origin[1]
    tol = REL_TOLERANCE * extent
    zr = z[np.isfinite(z)]
    ztol = REL_TOLERANCE * max(extent, float(zr.max() - zr.min()) if len(zr) else 0.0)
    input_points = n_data

    sa = np.concatenate(seg_a) if seg_a else np.zeros(0, np.int64)
    sb = np.concatenate(seg_b) if seg_b else np.zeros(0, np.int64)
    hard = np.concatenate(seg_hard) if seg_hard else np.zeros(0, bool)
    n_breakline_segments = int(np.count_nonzero(hard)) - (len(ring) if ring is not None and hard_ring else 0)

    def compact(rep):
        nonlocal x, y, z, cx, cy, sa, sb, ring
        keep = rep == np.arange(len(rep))
        index = np.cumsum(keep) - 1
        new = index[rep]
        x, y, z, cx, cy = x[keep], y[keep], z[keep], cx[keep], cy[keep]
        sa, sb = new[sa], new[sb]
        if ring is not None:
            ring = new[ring]

    # ---- coincident points
    rep, merged = _merge(x, y, z, cx, cy, tol, ztol, "Точки совпадают в плане, но имеют разную отметку Z")
    if merged:
        compact(rep)
    _tick(progress, 0.1)

    if ring is not None:
        ring = ring[np.concatenate([[True], ring[1:] != ring[:-1]])]
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if len(np.unique(ring)) < 3:
            raise SurfaceError("Граница: нужен полигон не менее чем из трёх различных вершин.")

    # ---- constraint segments: touching and crossing
    live = sa != sb
    sa, sb, hard = sa[live], sb[live], hard[live]
    crossings = 0
    if len(sa) > 1:
        xmin = np.minimum(cx[sa], cx[sb])
        xmax = np.maximum(cx[sa], cx[sb])
        order = np.argsort(xmin, kind="stable")
        cap = max(1024, len(sa))
        while True:
            nt, nc, t_seg, t_vtx, t_par, c_i, c_j, c_ti, c_tj = _segment_events(
                cx, cy, sa, sb, order, xmin, xmax, tol, cap)
            if nt <= cap and nc <= cap:
                break
            cap = max(nt, nc)
        t_seg, t_vtx, t_par = t_seg[:nt], t_vtx[:nt], t_par[:nt]
        c_i, c_j, c_ti, c_tj = c_i[:nc], c_j[:nc], c_ti[:nc], c_tj[:nc]
        conflicts = []
        if nt:
            hard_vertex = np.zeros(len(cx), bool)
            hard_vertex[sa[hard]] = True
            hard_vertex[sb[hard]] = True
            on_hard = hard[t_seg]
            zs = z[sa[t_seg]] + t_par * (z[sb[t_seg]] - z[sa[t_seg]])
            zv = z[t_vtx]
            differ = on_hard & hard_vertex[t_vtx] & (np.abs(zv - zs) > ztol)
            for k in np.nonzero(differ)[0]:
                conflicts.append((x[t_vtx[k]], y[t_vtx[k]], zv[k], zs[k]))
            fill = on_hard & np.isnan(zv)
            z[t_vtx[fill]] = zs[fill]
        ev_seg, ev_par, ev_vtx = [t_seg], [t_par], [t_vtx]
        if nc:
            if np.any(~hard[c_i] & ~hard[c_j]):
                k = int(np.nonzero(~hard[c_i] & ~hard[c_j])[0][0])
                a, b = sa[c_i[k]], sb[c_i[k]]
                raise SurfaceError("Граница самопересекается (около X="
                                   f"{_fmt(x[a] + c_ti[k] * (x[b] - x[a]))} Y={_fmt(y[a] + c_ti[k] * (y[b] - y[a]))}). "
                                   "Задайте границу простым полигоном.")
            ai, bi, aj, bj = sa[c_i], sb[c_i], sa[c_j], sb[c_j]
            ncx = cx[ai] + c_ti * (cx[bi] - cx[ai])
            ncy = cy[ai] + c_ti * (cy[bi] - cy[ai])
            zi = np.where(hard[c_i], z[ai] + c_ti * (z[bi] - z[ai]), np.nan)
            zj = np.where(hard[c_j], z[aj] + c_tj * (z[bj] - z[aj]), np.nan)
            differ = np.abs(zi - zj) > ztol
            for k in np.nonzero(differ)[0]:
                conflicts.append((ncx[k] + origin[0], ncy[k] + origin[1], zi[k], zj[k]))
            nz = np.where(np.isnan(zi), zj, np.where(np.isnan(zj), zi, 0.5 * (zi + zj)))
            new_ids = len(cx) + np.arange(nc)
            ev_seg += [c_i, c_j]
            ev_par += [c_ti, c_tj]
            ev_vtx += [new_ids, new_ids]
        if conflicts:
            # A vertex shared by two segments of a line reports the same place twice.
            conflicts = list({(round(c[0], 6), round(c[1], 6)): c for c in conflicts}.values())
            examples = "; ".join(f"X={_fmt(px)} Y={_fmt(py)}: Z = {_fmt(z1)} и {_fmt(z2)}"
                                 for px, py, z1, z2 in conflicts[:5])
            raise SurfaceError("Структурные линии пересекаются или примыкают друг к другу на разной отметке Z: "
                               f"{len(conflicts)} шт. Примеры: {examples}. Исправьте линии — отметка не выбирается "
                               "автоматически.")
        if nc:
            x = np.concatenate([x, ncx + origin[0]])
            y = np.concatenate([y, ncy + origin[1]])
            z = np.concatenate([z, nz])
            cx = np.concatenate([cx, ncx])
            cy = np.concatenate([cy, ncy])
        if nt or nc:
            # Split every segment at its events: nodes of a segment = a, events by t, b.
            ev_seg = np.concatenate(ev_seg)
            ev_par = np.concatenate(ev_par)
            ev_vtx = np.concatenate(ev_vtx)
            by = np.lexsort((ev_par, ev_seg))
            ev_seg, ev_vtx = ev_seg[by], ev_vtx[by]
            m = len(sa)
            counts = np.bincount(ev_seg, minlength=m)
            starts = np.concatenate([[0], np.cumsum(counts + 2)])
            nodes = np.empty(starts[-1], np.int64)
            owner = np.repeat(np.arange(m), counts + 2)
            nodes[starts[:-1]] = sa
            nodes[starts[1:] - 1] = sb
            rank = np.arange(len(ev_seg)) - np.repeat(np.cumsum(counts) - counts, counts)
            nodes[starts[ev_seg] + 1 + rank] = ev_vtx
            pair = owner[1:] == owner[:-1]
            sa, sb, hard = nodes[:-1][pair], nodes[1:][pair], hard[owner[:-1][pair]]
        if nc:
            crossings = nc
            rep, again = _merge(x, y, z, cx, cy, tol, ztol,
                                "Пересечение структурных линий совпадает в плане с точкой другой отметки Z")
            if again:
                crossings -= int(np.count_nonzero(rep[-nc:] != np.arange(len(rep) - nc, len(rep))))
                compact(rep)
    nv = len(cx)
    if len(sa):
        live = sa != sb
        sa, sb, hard = sa[live], sb[live], hard[live]
        key = np.minimum(sa, sb) * nv + np.maximum(sa, sb)
        key, first, inverse = np.unique(key, return_index=True, return_inverse=True)
        any_hard = np.zeros(len(key), bool)
        np.logical_or.at(any_hard, inverse, hard)
        sa, sb, hard = sa[first], sb[first], any_hard
    edges = np.column_stack([sa, sb]) if len(sa) else np.zeros((0, 2), np.int64)
    _tick(progress, 0.2)

    # ---- Z of plan-only boundary vertices: from the surface of the data alone
    unknown = np.nonzero(np.isnan(z))[0]
    extrapolated = 0
    if len(unknown):
        known = np.nonzero(~np.isnan(z))[0]
        index = np.full(nv, -1, np.int64)
        index[known] = np.arange(len(known))
        kx, ky, kz = cx[known], cy[known], z[known]
        he = edges[hard]
        t1, nb1, _ = _cdt(kx, ky, index[he], tol) if len(known) >= 3 else (np.zeros((0, 3), np.int64),) * 3
        if len(t1) == 0:
            raise SurfaceError("Недостаточно точек с отметками: нужны хотя бы три точки, не лежащие на одной прямой.")
        where = _locate(kx, ky, t1, nb1, cx[unknown], cy[unknown])
        inside = where >= 0
        tq = t1[where[inside]]
        qx, qy = cx[unknown[inside]], cy[unknown[inside]]
        ax, ay = kx[tq[:, 0]], ky[tq[:, 0]]
        bx, by = kx[tq[:, 1]], ky[tq[:, 1]]
        dx, dy = kx[tq[:, 2]], ky[tq[:, 2]]
        den = (bx - ax) * (dy - ay) - (by - ay) * (dx - ax)
        with np.errstate(divide="ignore", invalid="ignore"):
            wb = ((qx - ax) * (dy - ay) - (qy - ay) * (dx - ax)) / den
            wc = ((bx - ax) * (qy - ay) - (by - ay) * (qx - ax)) / den
        zq = kz[tq[:, 0]] + wb * (kz[tq[:, 1]] - kz[tq[:, 0]]) + wc * (kz[tq[:, 2]] - kz[tq[:, 0]])
        zq = np.where(np.isfinite(zq), zq, kz[tq[:, 0]])
        z[unknown[inside]] = zq
        if not inside.all():
            hull_t, hull_e = np.nonzero(nb1 < 0)
            ea = t1[hull_t, hull_e]
            eb = t1[hull_t, (hull_e + 1) % 3]
            out = unknown[~inside]
            z[out] = _nearest_on_edges(kx, ky, kz, ea, eb, cx[out], cy[out])
            extrapolated = len(out)
    _tick(progress, 0.45 if len(unknown) else 0.25)

    # ---- triangulation
    tri, nbr, fixed = _cdt(cx, cy, edges, tol)
    if len(tri) == 0:
        raise SurfaceError("Недостаточно точек: нужны хотя бы три точки, не лежащие на одной прямой.")
    _tick(progress, 0.8)
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    alive = np.ones(len(tri), bool)
    is_fixed = np.zeros((len(tri), 3), bool)
    if len(fixed) and (ring is not None or max_edge is not None):
        fkey = np.sort(np.minimum(fixed[:, 0], fixed[:, 1]) * nv + np.maximum(fixed[:, 0], fixed[:, 1]))
        for e in range(3):
            u, w = tri[:, e], tri[:, (e + 1) % 3]
            k = np.minimum(u, w) * nv + np.maximum(u, w)
            pos = np.minimum(np.searchsorted(fkey, k), len(fkey) - 1)
            is_fixed[:, e] = fkey[pos] == k
    if ring is not None:
        area2 = (cx[b] - cx[a]) * (cy[c] - cy[a]) - (cy[b] - cy[a]) * (cx[c] - cx[a])
        comp, best = _components(nbr, is_fixed, area2)
        bt = tri[best]
        qx = cx[bt].mean(axis=1)
        qy = cy[bt].mean(axis=1)
        inside = _points_in_polygon(qx, qy, np.ascontiguousarray(cx[ring]), np.ascontiguousarray(cy[ring]))
        alive = inside[comp]
        if not alive.any():
            raise SurfaceError("Внутри границы нет ни одного треугольника: граница не охватывает исходные данные.")
    dropped_long = 0
    if max_edge is not None:
        limit = float(max_edge) ** 2
        long_ = np.zeros(len(tri), bool)
        for e in range(3):
            u, w = tri[:, e], tri[:, (e + 1) % 3]
            long_ |= (cx[u] - cx[w]) ** 2 + (cy[u] - cy[w]) ** 2 > limit
        dropped_long = int(_peel_long(nbr, alive, long_, is_fixed))
        if not alive.any():
            raise SurfaceError(f"Все треугольники длиннее заданного ребра ({max_edge:g}) — поверхность пуста. "
                               "Увеличьте наибольшую длину ребра.")
    tri = tri[alive]
    used = np.zeros(nv, bool)
    used[tri.ravel()] = True
    index = np.cumsum(used) - 1
    tri = index[tri]
    vertices = np.column_stack([x[used], y[used], z[used]])
    lx, ly = cx[used], cy[used]
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    ux, uy, uz = lx[b] - lx[a], ly[b] - ly[a], vertices[b, 2] - vertices[a, 2]
    wx, wy, wz = lx[c] - lx[a], ly[c] - ly[a], vertices[c, 2] - vertices[a, 2]
    nzc = ux * wy - uy * wx
    area_plan = 0.5 * float(nzc.sum())
    area = 0.5 * float(np.sqrt((uy * wz - uz * wy) ** 2 + (uz * wx - ux * wz) ** 2 + nzc ** 2).sum())
    report = {
        "points": len(vertices),
        "breakline_segments": n_breakline_segments,
        "triangles": len(tri),
        "merged_duplicates": int(merged),
        "dropped_long": dropped_long,
        "area_plan": area_plan,
        "area": area,
        "method": DTM_METHOD,
        "tolerance": tol,
        # Extra information for the record of the operation.
        "tolerance_z": ztol,
        "input_points": int(input_points),
        "crossings_added": int(crossings),
        "unused_points": int(nv - len(vertices)),
        "boundary_extrapolated": int(extrapolated),
    }
    _tick(progress, 1.0)
    return vertices, tri.astype(np.int32), report


# ------------------------------------------------------------------ mesh topology


def _twins(tri: np.ndarray, vertex_count: int) -> tuple[np.ndarray, int]:
    """Half-edge 3*t+e runs from tri[t, e] to tri[t, e+1]. → (twin half-edge or -1, non-manifold edges)."""
    t = np.asarray(tri, dtype=np.int64)
    a = t.reshape(-1)
    b = t[:, [1, 2, 0]].reshape(-1)
    key = np.minimum(a, b) * max(int(vertex_count), 1) + np.maximum(a, b)
    order = np.argsort(key, kind="stable")
    ks = key[order]
    same = ks[1:] == ks[:-1]
    twin = np.full(len(key), -1, np.int64)
    i = np.nonzero(same)[0]
    twin[order[i]] = order[i + 1]
    twin[order[i + 1]] = order[i]
    nonmanifold = int(np.count_nonzero(same[1:] & same[:-1]))
    return twin, nonmanifold


def _used(t: np.ndarray, vertex_count: int) -> np.ndarray:
    """Sorted indices of the vertices referenced by triangles (cheaper than np.unique)."""
    return np.flatnonzero(np.bincount(t.reshape(-1), minlength=vertex_count))


def _check_mesh(vertices, triangles) -> tuple[np.ndarray, np.ndarray]:
    v = np.asarray(vertices, dtype=np.float64)
    t = np.asarray(triangles)
    if v.ndim != 2 or v.shape[1] != 3 or t.ndim != 2 or t.shape[1] != 3:
        raise SurfaceError("Поверхность: ожидаются вершины (n,3) и треугольники (m,3).")
    if len(t) == 0:
        raise SurfaceError("Поверхность не содержит треугольников.")
    t = t.astype(np.int64)
    if t.min() < 0 or t.max() >= len(v):
        raise SurfaceError("Поверхность: индексы треугольников выходят за пределы массива вершин.")
    if not np.isfinite(v[_used(t, len(v))]).all():
        raise SurfaceError("Поверхность содержит нечисловые координаты.")
    return v, t


def _upward(vertices, triangles, name: str = "Поверхность") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Checks that the surface is single-valued in Z triangle by triangle and orients it upward.

    → (vertices float64, triangles int64 counter-clockwise in plan, doubled plan areas).
    Triangles degenerate in 3D (zero area) are kept and ignored by the check.
    """
    v, t = _check_mesh(vertices, triangles)
    p = v[_used(t, len(v))]
    o = np.floor((p.min(axis=0) + p.max(axis=0)) / 2.0)
    a, b, c = (v[t[:, i]] - o for i in range(3))
    u = b - a
    w = c - a
    nz = u[:, 0] * w[:, 1] - u[:, 1] * w[:, 0]
    nx = u[:, 1] * w[:, 2] - u[:, 2] * w[:, 1]
    ny = u[:, 2] * w[:, 0] - u[:, 0] * w[:, 2]
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    longest = np.maximum(np.einsum("ij,ij->i", u, u), np.einsum("ij,ij->i", w, w))
    solid = norm > 1e-12 * longest
    vertical = solid & (np.abs(nz) <= _VERTICAL * norm)
    up = solid & ~vertical & (nz > 0)
    down = solid & ~vertical & (nz < 0)
    n_vertical, n_up, n_down = (int(np.count_nonzero(m)) for m in (vertical, up, down))
    if n_vertical or (n_up and n_down):
        parts = []
        if n_vertical:
            parts.append(f"вертикальных треугольников: {n_vertical}")
        if n_up and n_down:
            parts.append(f"перевёрнутых (нависающих) треугольников: {min(n_up, n_down)}")
        k = int(np.nonzero(vertical if n_vertical else (down if n_down <= n_up else up))[0][0])
        p = v[t[k]].mean(axis=0)
        raise SurfaceError(f"{name} не однозначна по Z ({', '.join(parts)}; например, около X={_fmt(p[0])} "
                           f"Y={_fmt(p[1])} Z={_fmt(p[2])}). Объём считается только для поверхностей без "
                           "вертикальных стенок и нависаний (ЦМР).")
    if n_down:
        t = t[:, [0, 2, 1]]
        nz = -nz
    return v, t, nz


def _boundary_edges(t: np.ndarray, vertex_count: int, name: str = "Поверхность") -> tuple[np.ndarray, np.ndarray]:
    twin, nonmanifold = _twins(t, vertex_count)
    if nonmanifold:
        raise SurfaceError(f"{name}: {nonmanifold} рёбер принадлежат более чем двум треугольникам. "
                           "Исправьте каркас (проверка и исправление каркасов).")
    h = np.nonzero(twin < 0)[0]
    return t[h // 3, h % 3], t[h // 3, (h % 3 + 1) % 3]


def surface_footprint(vertices, triangles) -> list[np.ndarray]:
    """Boundary loops of a surface in plan, each (k,2) float64 without a repeated closing point.

    The surface is oriented upward first, so outer loops run counter-clockwise and holes clockwise.
    Loops are sorted by signed area, largest first: the outer loop (of the largest part) comes
    first, holes come last. Raises SurfaceError for surfaces not single-valued in Z.
    """
    v, t, _ = _upward(vertices, triangles)
    ea, eb = _boundary_edges(t, len(v))
    if len(ea) == 0:
        raise SurfaceError("Поверхность замкнута — у неё нет контура в плане.")
    outgoing: dict[int, list[int]] = {}
    for k, s in enumerate(ea.tolist()):
        outgoing.setdefault(s, []).append(k)
    used = np.zeros(len(ea), bool)
    ends = eb.tolist()
    starts = ea.tolist()
    loops = []
    for k0 in range(len(ea)):
        if used[k0]:
            continue
        loop = []
        k = k0
        while True:
            used[k] = True
            loop.append(starts[k])
            nxt = None
            for cand in outgoing.get(ends[k], ()):
                if not used[cand]:
                    nxt = cand
                    break
            if nxt is None:
                break
            k = nxt
        loops.append(v[np.array(loop), :2].copy())
    o = np.floor(v[ea, :2].mean(axis=0))

    def signed(p):
        q = p - o
        return 0.5 * float(np.sum(q[:, 0] * np.roll(q[:, 1], -1) - np.roll(q[:, 0], -1) * q[:, 1]))

    return sorted(loops, key=signed, reverse=True)


# ------------------------------------------------------------------ contours


@njit(cache=True)
def _level_ranges(z, tri, base, interval, k_first, k_last):
    """For every triangle: first level index and number of levels L with zmin < L <= zmax."""
    nt = len(tri)
    k0 = np.empty(nt, np.int64)
    cnt = np.empty(nt, np.int64)
    for t in range(nt):
        z0 = z[tri[t, 0]]
        z1 = z[tri[t, 1]]
        z2 = z[tri[t, 2]]
        lo = min(z0, z1, z2)
        hi = max(z0, z1, z2)
        ka = int(math.floor((lo - base) / interval)) + 1  # noqa: RUF046 (numba)
        while base + (ka - 1) * interval > lo:
            ka -= 1
        while base + ka * interval <= lo:
            ka += 1
        kb = int(math.floor((hi - base) / interval))  # noqa: RUF046 (numba)
        while base + kb * interval > hi:
            kb -= 1
        while base + (kb + 1) * interval <= hi:
            kb += 1
        ka = max(ka, k_first)
        kb = min(kb, k_last)
        k0[t] = ka
        cnt[t] = max(0, kb - ka + 1)
    return k0, cnt


@njit(cache=True)
def _lone(z0, z1, z2, level):
    """The vertex alone on its side of the level → (local index, is it above). Above means z >= level."""
    a0 = z0 >= level
    a1 = z1 >= level
    a2 = z2 >= level
    if a0 != a1 and a0 != a2:
        return 0, a0
    if a1 != a0 and a1 != a2:
        return 1, a1
    return 2, a2


@njit(cache=True)
def _trace(v, tri, twin, k0, cnt, off, base, interval, capacity):
    """Walks every contour from triangle to triangle through the shared edges.

    A triangle crossed by a level holds exactly one segment between two of its edges (a vertex
    exactly on the level counts as above it), so a contour is a simple chain: it starts and ends
    on the boundary of the mesh or closes. Phase 0: open lines from the edge where the line
    enters the mesh (higher ground on the left for upward triangles). Phase 1: the remaining open
    lines (meshes with mixed orientation), walked backward and reversed. Phase 2: loops.
    Consecutive equal points (a line through a vertex) are written once.
    """
    total = off[len(tri)]
    visited = np.zeros(total, np.uint8)
    pts = np.empty((capacity, 3), np.float64)
    lcap = 1024
    l_start = np.empty(lcap, np.int64)
    l_level = np.empty(lcap, np.int64)
    l_closed = np.empty(lcap, np.uint8)
    nl = 0
    n = 0
    nt = len(tri)
    for phase in range(3):
        for t in range(nt):
            for kk in range(cnt[t]):
                if visited[off[t] + kk]:
                    continue
                k = k0[t] + kk
                level = base + k * interval
                j, above = _lone(v[tri[t, 0], 2], v[tri[t, 1], 2], v[tri[t, 2], 2], level)
                if above:
                    e_in = j
                    e_out = (j + 2) % 3
                else:
                    e_in = (j + 2) % 3
                    e_out = j
                if phase == 0:
                    if twin[3 * t + e_in] >= 0:
                        continue
                elif phase == 1:
                    if twin[3 * t + e_out] >= 0:
                        continue
                    e_in, e_out = e_out, e_in
                begin = n
                ct = t
                closed = False
                first = phase < 2
                if first:
                    edge = e_in
                else:
                    # A loop: start with the exit point; the walk comes back to this triangle.
                    edge = e_out
                    visited[off[t] + kk] = 1
                while True:
                    # ---- point where the line crosses `edge` of triangle ct
                    ia = tri[ct, edge]
                    ib = tri[ct, (edge + 1) % 3]
                    if v[ia, 2] >= level:
                        ia, ib = ib, ia
                    if v[ib, 2] == level:
                        px = v[ib, 0]
                        py = v[ib, 1]
                    else:
                        s = (level - v[ia, 2]) / (v[ib, 2] - v[ia, 2])
                        px = v[ia, 0] + s * (v[ib, 0] - v[ia, 0])
                        py = v[ia, 1] + s * (v[ib, 1] - v[ia, 1])
                    if n == begin or pts[n - 1, 0] != px or pts[n - 1, 1] != py:
                        if n >= len(pts):
                            grown = np.empty((2 * len(pts) + 16, 3), np.float64)
                            grown[:n] = pts[:n]
                            pts = grown
                        pts[n, 0] = px
                        pts[n, 1] = py
                        pts[n, 2] = level
                        n += 1
                    if first:
                        # The entry point is written; now cross the triangle.
                        first = False
                        visited[off[ct] + (k - k0[ct])] = 1
                        edge = e_out
                        continue
                    # ---- step over the exit edge
                    h = twin[3 * ct + edge]
                    if h < 0:
                        break
                    nxt = h // 3
                    kk2 = k - k0[nxt]
                    if kk2 < 0 or kk2 >= cnt[nxt]:
                        break
                    if visited[off[nxt] + kk2]:
                        closed = phase == 2 and nxt == t
                        break
                    j2, _ = _lone(v[tri[nxt, 0], 2], v[tri[nxt, 1], 2], v[tri[nxt, 2], 2], level)
                    enter = h % 3
                    if enter == j2:
                        edge = (j2 + 2) % 3
                    elif enter == (j2 + 2) % 3:
                        edge = j2
                    else:
                        break
                    ct = nxt
                    visited[off[ct] + kk2] = 1
                if closed and n - begin >= 2 and pts[n - 1, 0] == pts[begin, 0] and pts[n - 1, 1] == pts[begin, 1]:
                    n -= 1
                if n - begin < (3 if closed else 2):
                    n = begin
                    continue
                if phase == 1:
                    i = begin
                    q = n - 1
                    while i < q:
                        for d in range(2):
                            tmp = pts[i, d]
                            pts[i, d] = pts[q, d]
                            pts[q, d] = tmp
                        i += 1
                        q -= 1
                if nl >= lcap:
                    lcap *= 2
                    g1 = np.empty(lcap, np.int64)
                    g1[:nl] = l_start[:nl]
                    l_start = g1
                    g2 = np.empty(lcap, np.int64)
                    g2[:nl] = l_level[:nl]
                    l_level = g2
                    g3 = np.empty(lcap, np.uint8)
                    g3[:nl] = l_closed[:nl]
                    l_closed = g3
                l_start[nl] = begin
                l_level[nl] = k
                l_closed[nl] = 1 if closed else 0
                nl += 1
    return pts[:n], l_start[:nl], l_level[:nl], l_closed[:nl]


def contours(vertices, triangles, *, interval: float, base: float = 0.0, major_every: int = 5,
             z_min: float | None = None, z_max: float | None = None, progress=None) -> list[dict]:
    """Isolines of a surface: its sections by the horizontal planes z = base + k·interval (k integer).

    Returns [{"z": level, "major": bool, "closed": bool, "points": (n,3) float64}] sorted by level.
    `major` marks every `major_every`-th level counted from `base` (k divisible by major_every;
    0 or less → no major lines). z_min / z_max limit the levels (inclusive).

    A vertex exactly on a level counts as lying above it. Then every triangle holds at most one
    segment of a level, so lines neither double nor break at such vertices; a flat area exactly
    on a level is outlined along the side where the ground falls below it; a summit that only
    touches a level gives no line. A closed line does not repeat its first point. For a surface
    oriented upward the higher ground is on the left of every line.
    """
    _tick(progress, 0.0)
    v, t = _check_mesh(vertices, triangles)
    interval = float(interval)
    base = float(base)
    if not (interval > 0 and math.isfinite(interval) and math.isfinite(base)):
        raise SurfaceError("Шаг изолиний должен быть положительным числом.")
    zs = v[_used(t, len(v)), 2]
    lo = float(zs.min()) if z_min is None else max(float(zs.min()), float(z_min))
    hi = float(zs.max()) if z_max is None else min(float(zs.max()), float(z_max))
    if hi < lo:
        return []
    if (hi - lo) / interval > MAX_LEVELS:
        raise SurfaceError(f"Слишком мелкий шаг изолиний: более {MAX_LEVELS} уровней на перепаде высот "
                           f"{hi - lo:g}. Увеличьте шаг.")
    k_first = math.ceil((lo - base) / interval)
    while base + k_first * interval < lo:
        k_first += 1
    while base + (k_first - 1) * interval >= lo:
        k_first -= 1
    k_last = math.floor((hi - base) / interval)
    while base + k_last * interval > hi:
        k_last -= 1
    while base + (k_last + 1) * interval <= hi:
        k_last += 1
    if k_last < k_first:
        return []
    z = np.ascontiguousarray(v[:, 2])
    k0, cnt = _level_ranges(z, t, base, interval, k_first, k_last)
    off = np.concatenate([[0], np.cumsum(cnt)])
    total = int(off[-1])
    if total == 0:
        return []
    if total > MAX_CONTOUR_SEGMENTS:
        raise SurfaceError(f"Слишком мелкий шаг изолиний: получится {total} отрезков. Увеличьте шаг.")
    _tick(progress, 0.2)
    twin, _ = _twins(t, len(v))
    _tick(progress, 0.5)
    open_edges = int(np.count_nonzero(twin < 0))
    capacity = total + min(total, open_edges * 2) + 16
    pts, l_start, l_level, l_closed = _trace(np.ascontiguousarray(v), t, twin, k0, cnt, off, base, interval, capacity)
    _tick(progress, 0.9)
    ends = np.concatenate([l_start[1:], [len(pts)]])
    order = np.argsort(l_level, kind="stable")
    major_every = int(major_every)
    out = []
    for i in order.tolist():
        k = int(l_level[i])
        out.append({
            "z": base + k * interval,
            "major": bool(major_every > 0 and k % major_every == 0),
            "closed": bool(l_closed[i]),
            "points": pts[l_start[i]:ends[i]].copy(),
        })
    _tick(progress, 1.0)
    return out


# ------------------------------------------------------------------ solids and volumes


def _column(v: np.ndarray, t: np.ndarray, base_z: float, name: str = "Поверхность") -> tuple[np.ndarray, np.ndarray]:
    """Closed solid between an upward surface (v, t) and the plane z = base_z, oriented outward.

    Vertices: the used surface vertices, then their copies on the plane.
    """
    used = _used(t, len(v))
    index = np.full(len(v), -1, np.int64)
    index[used] = np.arange(len(used))
    top = index[t]
    n = len(used)
    ea, eb = _boundary_edges(top, n, name)
    if len(ea) == 0:
        raise SurfaceError(f"{name} замкнута — тело под ней построить нельзя.")
    walls = np.concatenate([np.column_stack([ea, ea + n, eb + n]), np.column_stack([ea, eb + n, eb])])
    tris = np.concatenate([top, top[:, [0, 2, 1]] + n, walls])
    zs = v[used, 2]
    if base_z > zs.min():
        if base_z < zs.max():
            raise SurfaceError(f"Уровень {base_z:g} пересекает поверхность (её отметки от {zs.min():g} до "
                               f"{zs.max():g}); тело строится только до уровня ниже или выше всей поверхности.")
        tris = tris[:, [0, 2, 1]]
    elif base_z == zs.min():
        raise SurfaceError(f"Уровень {base_z:g} касается поверхности; задайте уровень ниже её наименьшей отметки.")
    bottom = v[used].copy()
    bottom[:, 2] = base_z
    return np.concatenate([v[used], bottom]), tris


def under_solid(vertices, triangles, base_z: float) -> tuple[np.ndarray, np.ndarray]:
    """Closed solid between a surface and the horizontal plane z = base_z.

    The surface, its copy on the plane and vertical walls along its boundary loops; triangles are
    oriented outward. base_z must lie below the whole surface (or above the whole surface — then
    the solid above the surface is returned). Returns (vertices float64, triangles int32).
    Raises SurfaceError for a surface with vertical or overturned triangles.
    """
    v, t, _ = _upward(vertices, triangles)
    sv, st = _column(v, t, float(base_z))
    return sv, st.astype(np.int32)


def _manifold(sv: np.ndarray, st: np.ndarray, origin: np.ndarray, name: str):
    import manifold3d as m3

    solid = m3.Manifold(m3.Mesh64(vert_properties=np.ascontiguousarray(sv - origin),
                                  tri_verts=np.ascontiguousarray(st, dtype=np.uint64)))
    status = solid.status()
    if getattr(status, "name", str(status)) != "NoError":
        raise SurfaceError(f"{name}: не удалось построить замкнутое тело ({getattr(status, 'name', status)}). "
                           "Проверьте каркас: совпадающие вершины, вырожденные и дублирующиеся треугольники.")
    return solid


def _polygon_ring(polygon) -> np.ndarray:
    p = np.asarray(polygon, dtype=np.float64)
    if p.ndim != 2 or p.shape[1] < 2 or not np.isfinite(p[:, :2]).all():
        raise SurfaceError("Ограничивающий полигон: ожидается массив координат (k,2).")
    p = p[:, :2]
    if len(p) > 1 and (p[0] == p[-1]).all():
        p = p[:-1]
    if len(p) < 3:
        raise SurfaceError("Ограничивающий полигон: нужно не менее трёх вершин.")
    return p


def _prism(ring: np.ndarray, z0: float, z1: float, origin: np.ndarray):
    """Vertical prism of a simple polygon between z0 and z1 (local coordinates) as a Manifold."""
    import mapbox_earcut as earcut

    q = ring - origin[:2]
    area = 0.5 * float(np.sum(q[:, 0] * np.roll(q[:, 1], -1) - np.roll(q[:, 0], -1) * q[:, 1]))
    if area < 0:
        q = q[::-1]
        area = -area
    k = len(q)
    cap = earcut.triangulate_float64(np.ascontiguousarray(q), np.array([k], dtype=np.uint32)).reshape(-1, 3).astype(np.int64)
    a, b, c = q[cap[:, 0]], q[cap[:, 1]], q[cap[:, 2]]
    signed = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    covered = 0.5 * float(np.abs(signed).sum())
    if not area > 0 or abs(covered - area) > 1e-9 * area:
        raise SurfaceError("Ограничивающий полигон самопересекается или вырожден; задайте простой полигон.")
    cap[signed < 0] = cap[signed < 0][:, [0, 2, 1]]
    i = np.arange(k)
    j = (i + 1) % k
    # Vertices: top ring (0..k-1), bottom ring (k..2k-1).
    walls = np.concatenate([np.column_stack([i, i + k, j + k]), np.column_stack([i, j + k, j])])
    tris = np.concatenate([cap, cap[:, [0, 2, 1]] + k, walls])
    verts = np.concatenate([np.column_stack([q, np.full(k, z1)]), np.column_stack([q, np.full(k, z0)])])
    return _manifold(verts, tris, np.zeros(3), "Ограничивающий полигон")


def _floor_area(solid, z_floor: float, eps: float) -> float:
    """Plan area of the faces of a solid lying on its floor plane."""
    mesh = solid.to_mesh64()
    p = np.asarray(mesh.vert_properties)[:, :3]
    t = np.asarray(mesh.tri_verts).astype(np.int64)
    if len(t) == 0:
        return 0.0
    flat = (np.abs(p[t, 2] - z_floor) <= eps).all(axis=1)
    a, b, c = p[t[flat, 0]], p[t[flat, 1]], p[t[flat, 2]]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    return 0.5 * float(np.abs(cross).sum())


def volume_between(top, bottom, *, polygon: np.ndarray | None = None) -> dict:
    """Cut and fill volumes between two surfaces single-valued in Z (DTMs), each (vertices, triangles).

    The volumes are taken inside the common plan area: the intersection of both footprints,
    further limited by `polygon` (k,2), a simple polygon, when given.
      cut   volume where `top` is above `bottom`
      fill  volume where `bottom` is above `top`
      net   cut − fill
      area  plan area of the common region

    Method (exact for the two TINs, no sampling): with A↓ the solid under `top` down to a floor
    below both surfaces and B↑ the solid above `bottom` up to a ceiling above both,
    cut = volume(A↓ ∩ B↑); symmetrically fill = volume(B↓ ∩ A↑). The intersections exist only over
    the common footprint, so no separate clipping is needed; the polygon is a prism intersected
    with A↓ and A↑. area = the floor face of A↓ ∩ B↓. Booleans: manifold3d on coordinates shifted
    to a local integer origin. `tolerance` is the largest length tolerance manifold3d used.

    A surface with vertical or overturned triangles → SurfaceError. Overlapping separate sheets
    with all triangles facing up are not detected.
    """
    va, ta, _ = _upward(*top, name="Верхняя поверхность")
    vb, tb, _ = _upward(*bottom, name="Нижняя поверхность")
    pa, pb = va[_used(ta, len(va))], vb[_used(tb, len(vb))]
    lo = np.minimum(pa.min(axis=0), pb.min(axis=0))
    hi = np.maximum(pa.max(axis=0), pb.max(axis=0))
    origin = np.floor((lo + hi) / 2.0)
    origin[2] = math.floor(lo[2])
    pad = max(1.0, math.ceil(0.05 * (hi[2] - lo[2])))
    z_floor = origin[2] - pad
    z_ceil = math.ceil(hi[2]) + pad

    def solid(v, t, level, name):
        sv, st = _column(v, t, level, name)
        return _manifold(sv, st, origin, name)

    a_down = solid(va, ta, z_floor, "Верхняя поверхность")
    a_up = solid(va, ta, z_ceil, "Верхняя поверхность")
    b_down = solid(vb, tb, z_floor, "Нижняя поверхность")
    b_up = solid(vb, tb, z_ceil, "Нижняя поверхность")
    if polygon is not None:
        prism = _prism(_polygon_ring(polygon), z_floor - origin[2] - 1.0, z_ceil - origin[2] + 1.0, origin)
        a_down = a_down ^ prism
        a_up = a_up ^ prism
    common = a_down ^ b_down
    tolerance = max(s.get_tolerance() for s in (a_down, a_up, b_down, b_up, common))
    area = _floor_area(common, z_floor - origin[2], max(1e-9, 10 * tolerance))
    if not area > 0:
        raise SurfaceError("Поверхности не перекрываются в плане" + (" в пределах заданного полигона" if polygon is not None else "")
                           + " — общего контура для подсчёта объёма нет.")
    cut = float((a_down ^ b_up).volume())
    fill = float((b_down ^ a_up).volume())
    return {"cut": cut, "fill": fill, "net": cut - fill, "area": area, "tolerance": float(tolerance),
            "method": VOLUME_METHOD}


def volume_to_level(surface, level: float, *, polygon: np.ndarray | None = None) -> dict:
    """Volumes between a surface (vertices, triangles) and the horizontal plane z = level.

    cut = volume of the surface above the level, fill = volume below it, net = cut − fill,
    area = plan area of the surface (inside `polygon` when given).

    Without a polygon: every triangle is a prism over its plan projection, split by the level
    plane analytically — exact for the TIN, no booleans. With a polygon: volume_between with a
    horizontal surface at the level.
    """
    level = float(level)
    v, t, nz = _upward(*surface)
    if polygon is not None:
        p = v[_used(t, len(v))]
        lo = p[:, :2].min(axis=0) - 1.0
        hi = p[:, :2].max(axis=0) + 1.0
        ring = _polygon_ring(polygon)
        lo = np.minimum(lo, ring.min(axis=0) - 1.0)
        hi = np.maximum(hi, ring.max(axis=0) + 1.0)
        flat = np.array([[lo[0], lo[1], level], [hi[0], lo[1], level], [hi[0], hi[1], level], [lo[0], hi[1], level]])
        return volume_between((v, t), (flat, np.array([[0, 1, 2], [0, 2, 3]])), polygon=ring)
    area = 0.5 * np.abs(nz)
    h = v[t, 2] - level
    pos = h > 0
    neg = h < 0
    n_pos = pos.sum(axis=1)
    n_neg = neg.sum(axis=1)
    whole = area * h.sum(axis=1) / 3.0
    mixed = (n_pos > 0) & (n_neg > 0)
    cut = math.fsum([float(whole[~mixed & (n_pos > 0)].sum())])
    fill = math.fsum([float(-whole[~mixed & (n_neg > 0)].sum())])
    if mixed.any():
        hm = h[mixed]
        lone_pos = n_pos[mixed] == 1
        # The lone vertex: the only one above the level, otherwise the only one below it.
        lone = np.where(lone_pos, np.argmax(hm > 0, axis=1), np.argmax(hm < 0, axis=1))
        rows = np.arange(len(hm))
        h0 = hm[rows, lone]
        h1 = hm[rows, (lone + 1) % 3]
        h2 = hm[rows, (lone + 2) % 3]
        # The corner cut off by the level: similar triangle with heights (h0, 0, 0).
        corner = area[mixed] * (h0 / (h0 - h1)) * (h0 / (h0 - h2)) * np.abs(h0) / 3.0
        wm = whole[mixed]
        cut += float(np.where(lone_pos, corner, corner + wm).sum())
        fill += float(np.where(lone_pos, corner - wm, corner).sum())
    extent = float(np.ptp(v[_used(t, len(v))], axis=0).max())
    return {"cut": cut, "fill": fill, "net": cut - fill, "area": float(area.sum()),
            "tolerance": float(np.finfo(np.float64).eps * max(extent, 1.0)), "method": LEVEL_METHOD}
