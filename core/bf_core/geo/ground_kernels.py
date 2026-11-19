"""numba kernels of the ground filter, stages 1–2 (`geo/ground.py`, ADR 0014 §4): the index of plan cells,
isolated points (noise), the lowest return of a cell, and the plane fits shared by the later stages.

Grid of a block. The plan is cut into cells of `c` = CR/2 on a lattice anchored to the whole origin (ox, oy)
of the cloud: global cell (floor((x − ox)/c), floor((y − oy)/c)). A block owns the cells
[cx0, cx0 + ncx) × [cy0, cy0 + ncy); its cell number is (gy − cy0)·ncx + (gx − cx0). Points are addressed by
their number in the cloud (int32); `order` lists them cell by cell, `start[cell] : start[cell + 1]` is the
slice of a cell. Everything a kernel computes is taken from float64 coordinates minus the origin.

Threads. A kernel that takes `row0, row1` (or `lo, hi`) handles that strip only and writes only the outputs of
its own rows, reading shared inputs — strips run on Python threads with the GIL released, and the result does
not depend on how they are scheduled (ADR 0014: «потоки Python над ядрами nogil по фиксированным полосам»).
No `fastmath`, no `parallel=True`.
"""

from __future__ import annotations

import math

import numpy as np

from bf_core.jit import njit

# --- index -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def extent(xyz, lo, hi):  # type: ignore[no-untyped-def]
    """(xmin, xmax, ymin, ymax, count) of the finite points lo ≤ i < hi; count = 0 — none (the rest is 0)."""
    count = 0
    xmin = 0.0
    xmax = 0.0
    ymin = 0.0
    ymax = 0.0
    for i in range(lo, hi):
        x = xyz[i, 0]
        y = xyz[i, 1]
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(xyz[i, 2])):
            continue
        if count == 0:
            xmin = x
            xmax = x
            ymin = y
            ymax = y
        else:
            if x < xmin:
                xmin = x
            if x > xmax:
                xmax = x
            if y < ymin:
                ymin = y
            if y > ymax:
                ymax = y
        count += 1
    return xmin, xmax, ymin, ymax, count


@njit(cache=True, nogil=True)
def block_keys(xyz, lo, hi, ox, oy, c, span, nbx, nby, key):  # type: ignore[no-untyped-def]
    """key[i] for i in [lo, hi): the block (of `span` cells a side) whose core holds point i; -1 — not finite."""
    for i in range(lo, hi):
        x = xyz[i, 0]
        y = xyz[i, 1]
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(xyz[i, 2])):
            key[i] = -1
            continue
        bx = int(math.floor((x - ox) / c)) // span
        by = int(math.floor((y - oy) / c)) // span
        if bx < 0 or by < 0 or bx >= nbx or by >= nby:
            key[i] = -1
        else:
            key[i] = by * nbx + bx


@njit(cache=True, nogil=True)
def cell_ids(xyz, cand, lo, hi, ox, oy, c, cx0, cy0, ncx, ncy, cid):  # type: ignore[no-untyped-def]
    """cid[k] for k in [lo, hi): the cell of point cand[k] in the block; -1 — outside it or not finite."""
    for k in range(lo, hi):
        i = cand[k]
        x = xyz[i, 0]
        y = xyz[i, 1]
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(xyz[i, 2])):
            cid[k] = -1
            continue
        gx = int(math.floor((x - ox) / c)) - cx0
        gy = int(math.floor((y - oy) / c)) - cy0
        if gx < 0 or gy < 0 or gx >= ncx or gy >= ncy:
            cid[k] = -1
        else:
            cid[k] = gy * ncx + gx


@njit(cache=True, nogil=True)
def counting_sort(key, ids, nkeys):  # type: ignore[no-untyped-def]
    """Stable counting sort of ids by key (key < 0 is dropped) → (start (nkeys + 1) int32, order int32)."""
    start = np.zeros(nkeys + 1, np.int32)
    for k in range(len(key)):
        c = key[k]
        if c >= 0:
            start[c + 1] += 1
    for c in range(nkeys):
        start[c + 1] += start[c]
    order = np.empty(start[nkeys], np.int32)
    fill = start[:nkeys].copy()
    for k in range(len(key)):
        c = key[k]
        if c >= 0:
            order[fill[c]] = ids[k]
            fill[c] += 1
    return start, order


# --- how full the lattice is -----------------------------------------------------------------------------
# The share of the cells that hold a point, known right after the index — before the noise, the obstacle and the
# cloth (`ground._early_fill`). It follows the share counted later (`ground_cloth.obstacle_rows` — a particle is
# in the system with a return within 2·CR, `ground_grow.edge_rows` — a cell is inside the data when the 6 × 6
# particles around it are), with what the index alone knows: every point counts, noise too, and a point stands
# at the centre of its cell.


@njit(cache=True, nogil=True)
def near_rows(start, ncx, ncy, row0, row1, near):  # type: ignore[no-untyped-def]
    """near[pj, pi] = 1 for the particles of rows [row0, row1) with a cell that holds a point within 2·CR
    (4 cells) of the particle, by the centre of the cell."""
    npx = ncx // 2 + 1
    for pj in range(row0, row1):
        for pi in range(npx):
            hit = 0
            for cj in range(max(2 * pj - 4, 0), min(2 * pj + 4, ncy)):
                dy = cj + 0.5 - 2 * pj
                for ci in range(max(2 * pi - 4, 0), min(2 * pi + 4, ncx)):
                    cell = cj * ncx + ci
                    if start[cell + 1] > start[cell]:
                        dx = ci + 0.5 - 2 * pi
                        if dx * dx + dy * dy <= 16.0:
                            hit = 1
                            break
                if hit == 1:
                    break
            near[pj, pi] = hit


@njit(cache=True, nogil=True)
def fill_rows(start, near, ncx, row0, row1):  # type: ignore[no-untyped-def]
    """(cells inside the data, those of them that hold a point) for the cells of the rows [2·row0, 2·row1):
    the four cells between particles (pi, pj) and (pi + 1, pj + 1) are inside when the 6 × 6 particles around
    them are all `near` a point; the border of the grid is not an edge of the data (as in `edge_rows`)."""
    npy, npx = near.shape
    inside = 0
    filled = 0
    for pj in range(row0, row1):
        for pi in range(ncx // 2):
            ok = True
            for j in range(max(pj - 2, 0), min(pj + 4, npy)):
                for i in range(max(pi - 2, 0), min(pi + 4, npx)):
                    if near[j, i] == 0:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                continue
            for cj in range(2 * pj, 2 * pj + 2):
                for ci in range(2 * pi, 2 * pi + 2):
                    cell = cj * ncx + ci
                    inside += 1
                    if start[cell + 1] > start[cell]:
                        filled += 1
    return inside, filled


# --- noise -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def noise_rows(xyz, order, start, ncx, ncy, row0, row1, ox, oy, c, cx0, cy0, radius, k_min, pcls):  # type: ignore[no-untyped-def]
    """pcls[k] = 7 for the points of rows [row0, row1) with fewer than k_min neighbours within `radius` (3D).

    The own cell is searched first, then rings of cells outward; the search stops at the k_min-th neighbour,
    so a point of a dense cloud costs a few comparisons. Returns the number of noise points.
    """
    r2 = radius * radius
    reach = int(math.ceil(radius / c))
    found_noise = 0
    for cj in range(row0, row1):
        for ci in range(ncx):
            cell = cj * ncx + ci
            s = start[cell]
            e = start[cell + 1]
            if s == e:
                continue
            x_lo = (cx0 + ci) * c  # the cell in coordinates minus the origin
            y_lo = (cy0 + cj) * c
            for k in range(s, e):
                i = order[k]
                x = xyz[i, 0] - ox
                y = xyz[i, 1] - oy
                z = xyz[i, 2]
                found = 0
                for kk in range(s, e):
                    if kk == k:
                        continue
                    j = order[kk]
                    dx = xyz[j, 0] - ox - x
                    dy = xyz[j, 1] - oy - y
                    dz = xyz[j, 2] - z
                    if dx * dx + dy * dy + dz * dz <= r2:
                        found += 1
                        if found >= k_min:
                            break
                ring = 1
                while found < k_min and ring <= reach:
                    for dj in range(-ring, ring + 1):
                        jj = cj + dj
                        if jj < 0 or jj >= ncy:
                            continue
                        # distance in Y from the point to the row of cells
                        gy = y_lo + dj * c
                        ddy = 0.0
                        if y < gy:
                            ddy = gy - y
                        elif y > gy + c:
                            ddy = y - gy - c
                        if ddy * ddy > r2:
                            continue
                        step = 1
                        if dj != -ring and dj != ring:
                            step = 2 * ring
                        for di in range(-ring, ring + 1, step):
                            ii = ci + di
                            if ii < 0 or ii >= ncx:
                                continue
                            gx = x_lo + di * c
                            ddx = 0.0
                            if x < gx:
                                ddx = gx - x
                            elif x > gx + c:
                                ddx = x - gx - c
                            if ddx * ddx + ddy * ddy > r2:
                                continue
                            other = jj * ncx + ii
                            for kk in range(start[other], start[other + 1]):
                                j = order[kk]
                                dx = xyz[j, 0] - ox - x
                                dy = xyz[j, 1] - oy - y
                                dz = xyz[j, 2] - z
                                if dx * dx + dy * dy + dz * dz <= r2:
                                    found += 1
                                    if found >= k_min:
                                        break
                            if found >= k_min:
                                break
                        if found >= k_min:
                            break
                    ring += 1
                if found < k_min:
                    pcls[k] = 7
                    found_noise += 1
    return found_noise


# --- the lowest return of a cell -------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def minima_rows(xyz, ret, use_ret, order, start, pcls, ncx, row0, row1, ox, oy, c, cx0, cy0, cmin, mx, my, mz):  # type: ignore[no-untyped-def]
    """The lowest point of every cell of rows [row0, row1), noise aside: cmin (its place in `order`, -1 — the
    cell is empty), mx, my (float32 offsets from the centre of the cell, m), mz (float64).

    With returns (`use_ret`) a last return of its pulse is preferred to any other return of the cell: a first
    return of several is the top of something. Equal heights — the smaller point number.
    """
    for cj in range(row0, row1):
        for ci in range(ncx):
            cell = cj * ncx + ci
            best = -1
            best_z = 0.0
            best_last = False
            best_id = 0
            for k in range(start[cell], start[cell + 1]):
                if pcls[k] == 7:
                    continue
                i = order[k]
                z = xyz[i, 2]
                last = True
                if use_ret:
                    r = ret[i]
                    count = r >> 4
                    last = count == 0 or (r & 15) >= count
                better = best < 0
                if not better:
                    if last != best_last:
                        better = last
                    elif z != best_z:
                        better = z < best_z
                    else:
                        better = i < best_id
                if better:
                    best = k
                    best_z = z
                    best_last = last
                    best_id = i
            cmin[cell] = best
            if best >= 0:
                i = order[best]
                mx[cell] = xyz[i, 0] - ox - (cx0 + ci + 0.5) * c
                my[cell] = xyz[i, 1] - oy - (cy0 + cj + 0.5) * c
                mz[cell] = best_z


# --- planes ----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def fit_plane_stats(dx, dy, z, use, n):  # type: ignore[no-untyped-def]
    """Least squares z = a + b·dx + c·dy over the entries with use[k] != 0
    → (ok, a, b, c, count, mean dx, mean dy, sxx, sxy, syy) — the last five are the centred sums of the plan
    positions: the leverage of a place (ex, ey from the mean) is 1/count + (syy·ex² − 2·sxy·ex·ey + sxx·ey²) /
    (sxx·syy − sxy²), the variance of the fitted height there in units of the variance of a return.

    Not ok: fewer than 3 points, or points on a line (the narrow extent under a tenth of the wide one) —
    the slope across such a set is not defined.
    """
    cnt = 0
    sx = 0.0
    sy = 0.0
    sz = 0.0
    for k in range(n):
        if use[k]:
            cnt += 1
            sx += dx[k]
            sy += dy[k]
            sz += z[k]
    if cnt < 3:
        return False, 0.0, 0.0, 0.0, cnt, 0.0, 0.0, 0.0, 0.0, 0.0
    sx /= cnt
    sy /= cnt
    sz /= cnt
    sxx = 0.0
    sxy = 0.0
    syy = 0.0
    sxz = 0.0
    syz = 0.0
    for k in range(n):
        if use[k]:
            ax = dx[k] - sx
            ay = dy[k] - sy
            az = z[k] - sz
            sxx += ax * ax
            sxy += ax * ay
            syy += ay * ay
            sxz += ax * az
            syz += ay * az
    det = sxx * syy - sxy * sxy
    trace = sxx + syy
    if det <= 0.01 * 0.25 * trace * trace:
        return False, 0.0, 0.0, 0.0, cnt, 0.0, 0.0, 0.0, 0.0, 0.0
    b = (sxz * syy - syz * sxy) / det
    cc = (syz * sxx - sxz * sxy) / det
    return True, sz - b * sx - cc * sy, b, cc, cnt, sx, sy, sxx, sxy, syy


@njit(cache=True, nogil=True)
def fit_plane(dx, dy, z, use, n):  # type: ignore[no-untyped-def]
    """Least squares z = a + b·dx + c·dy over the entries with use[k] != 0 → (ok, a, b, c); see above."""
    ok, a, b, cc, _n, _sx, _sy, _sxx, _sxy, _syy = fit_plane_stats(dx, dy, z, use, n)
    return ok, a, b, cc


@njit(cache=True, nogil=True)
def lower_plane(dx, dy, z, use, n, band):  # type: ignore[no-untyped-def]
    """A plane through the lower returns of the set: least squares over all → over those not above it → over
    those not above that one by more than `band` (twice). Returns (ok, a, b, c); `use` holds the last set.

    Clean ground comes back as plain least squares (every point is within the band of the lower half);
    ground mixed with vegetation — as the plane of the ground while at least three ground points remain.
    """
    for k in range(n):
        use[k] = 1
    ok, a, b, cc = fit_plane(dx, dy, z, use, n)
    if not ok:
        return False, 0.0, 0.0, 0.0
    for step in range(3):
        limit = 0.0
        if step > 0:
            limit = band
        for k in range(n):
            use[k] = 1 if z[k] - (a + b * dx[k] + cc * dy[k]) <= limit else 0
        ok2, a2, b2, c2 = fit_plane(dx, dy, z, use, n)
        if not ok2:
            break
        a = a2
        b = b2
        cc = c2
    return True, a, b, cc


# --- result ----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def store_rows(order, start, pcls, ph, ncx, row0, row1, col0, col1, cls, h):  # type: ignore[no-untyped-def]
    """The classes and heights of the points of the cells [row0, row1) × [col0, col1) (the core of a block)
    go to the arrays of the cloud. Returns the number of points written."""
    count = 0
    for cj in range(row0, row1):
        for k in range(start[cj * ncx + col0], start[cj * ncx + col1]):
            i = order[k]
            cls[i] = pcls[k]
            h[i] = ph[k]
            count += 1
    return count
