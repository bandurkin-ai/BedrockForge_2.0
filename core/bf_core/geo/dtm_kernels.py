"""numba kernels of «Построить ЦТМ» (`geo/dtm.py`): coincident points, touching and crossing constraint
segments, point location in a triangulation, groups of triangles, peeling of long triangles.

Ported from BF `geo/surfaces.py` without changes to the algorithms. Everything works in plan (X, Y) on
coordinates shifted to a local whole origin; indices are int64. The kernels release the GIL.
"""

from __future__ import annotations

import math

import numpy as np

from bf_core.jit import njit

# --- coincident points -----------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _find(parent, i):  # type: ignore[no-untyped-def]
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


@njit(cache=True, nogil=True)
def cluster(xs, ys, order, tol):  # type: ignore[no-untyped-def]
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


# --- crossings of constraints ----------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _on_segment(px, py, ax, ay, bx, by, tol):  # type: ignore[no-untyped-def]
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


@njit(cache=True, nogil=True)
def segment_events(cx, cy, sa, sb, order, xmin, xmax, tol, cap):  # type: ignore[no-untyped-def]
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
            if ((d1 > 0.0 and d2 < 0.0) or (d1 < 0.0 and d2 > 0.0)) and (
                (d3 > 0.0 and d4 < 0.0) or (d3 < 0.0 and d4 > 0.0)
            ):
                if nc < cap:
                    c_i[nc] = i
                    c_j[nc] = j
                    c_ti[nc] = d3 / (d3 - d4)
                    c_tj[nc] = d1 / (d1 - d2)
                nc += 1
    return nt, nc, t_seg, t_vtx, t_par, c_i, c_j, c_ti, c_tj


# --- triangulation helpers -------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def locate(px, py, tri, nbr, qx, qy):  # type: ignore[no-untyped-def]
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


@njit(cache=True, nogil=True)
def nearest_on_edges(px, py, pz, ea, eb, qx, qy):  # type: ignore[no-untyped-def]
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


@njit(cache=True, nogil=True)
def components(nbr, fixed, area2):  # type: ignore[no-untyped-def]
    """Groups of triangles connected through non-fixed edges → (group of every triangle, the largest
    triangle of every group)."""
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


@njit(cache=True, nogil=True)
def points_in_polygon(qx, qy, rx, ry):  # type: ignore[no-untyped-def]
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


@njit(cache=True, nogil=True)
def peel_long(nbr, alive, long_, fixed):  # type: ignore[no-untyped-def]
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
