"""The ground filter, stages 6–8 (`geo/ground.py`, ADR 0014 §4): the growth of the ground from the seeds along
the slope, the surface of the ground, the height of every point above it and its class, the steep faces.

Growth (Д-22, idea 3). The stiff cloth passes metres under a crest, a berm, the top of a dump — the ground is
returned from the seeds over the grid of the lowest returns. A cell taken as ground carries a plane fitted to
the ground returns of the 5 × 5 cells around it; a neighbour joins when its return is not above that plane by
more than CT along the normal and the plane does not rise towards it steeper than α. Down — any drop: a
crest, a face, a foot; up — gently only: a wall of a building, a crown, a face steeper than α are not climbed.
The order is fixed (a queue from the seeds in the order of the cells): one thread, the same result every run.

Smooth areas at the edge of the survey. A flat behind a high crest that ends at the edge of the data (the usual
survey of a pit: a strip around it) and a steep wall cut by that edge are never touched by the cloth and cannot
be climbed. A connected smooth area of lowest returns that the growth did not reach is ground when it is at
least `min_cells` large and lies along the edge of the data for `min_edge` cells — the terrain continues
beyond the survey; a roof or a crown inside the survey does neither.

Surface. Every ground cell gets its plane again when the growth is over: the ground returns around it that
agree with it, then without those above it (low vegetation taken on the way). A point is measured against the
planes of the ground cells around its cell (3 × 3, then 5 × 5), the closest wins: next to a vertical wall the
cell holds two levels, and the crest is ground by the plane of the upper one. No ground cell near — the cloth.

Faces (Д-22, idea 4). Points that are not ground on a locally flat patch steeper than `face` (the centres of
cells of c × c × c of such points within 1.5·CR lie on a plane) are ground when the plane meets ground both
above and below them within R_б in plan: a wall of a bench has a crest and a foot, a facade and a crown do not.
"""

from __future__ import annotations

import math

import numpy as np

from bf_core.jit import njit

from .ground_kernels import fit_plane

# --- growth ----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _plane_at(a, want, cmin, mx, my, mz, state, gxa, gya, ncx, ncy, c, ct, bx, by, bz, use):  # type: ignore[no-untyped-def]
    """The plane of the ground at cell `a`: z = mz[a] + o + gx·dx + gy·dy (dx, dy from the return of `a`),
    least squares over the returns of the cells in state `want` around it (5 × 5) that lie within 2·CT, then CT
    of the plane so far along its normal. Fewer than three such returns — the plane stays.
    → (n, o, gx, gy); bx, by, bz hold the n returns around, use — those of the last fit."""
    aj = a // ncx
    ai = a - aj * ncx
    n = 0
    for dj in range(-2, 3):
        jj = aj + dj
        if jj < 0 or jj >= ncy:
            continue
        for di in range(-2, 3):
            ii = ai + di
            if ii < 0 or ii >= ncx:
                continue
            b = jj * ncx + ii
            if state[b] != want or cmin[b] < 0:
                continue
            bx[n] = di * c + mx[b] - mx[a]
            by[n] = dj * c + my[b] - my[a]
            bz[n] = mz[b] - mz[a]
            n += 1
    o = 0.0
    for step in range(2):
        band = ct * (2.0 - step)
        norm = math.sqrt(1.0 + gxa * gxa + gya * gya)
        for k in range(n):
            use[k] = 1 if abs(bz[k] - (o + gxa * bx[k] + gya * by[k])) <= band * norm else 0
        ok, a0, b0, c0 = fit_plane(bx, by, bz, use, n)
        if not ok:
            break
        o = a0
        gxa = b0
        gya = c0
    return n, o, gxa, gya


@njit(cache=True, nogil=True)
def _spread(queue, head, tail, want, smooth, cmin, mx, my, mz, state, gx, gy, ncx, ncy, c, ct, tan_up, bx, by, bz, use):  # type: ignore[no-untyped-def]
    """Breadth-first growth from queue[head:tail]; the cells that join get state `want` and are appended.

    want = 1 — the ground: any cell that is not ground yet may join (down — any drop, up — not steeper than
    tan_up). want = 2 — a smooth area on trial (`smooth`): untouched cells only, within CT on both sides.
    """
    while head < tail:
        a = queue[head]
        head += 1
        aj = a // ncx
        ai = a - aj * ncx
        _n, o, gxa, gya = _plane_at(a, want, cmin, mx, my, mz, state, gx[a], gy[a], ncx, ncy, c, ct, bx, by, bz, use)
        gx[a] = gxa
        gy[a] = gya
        norm = math.sqrt(1.0 + gxa * gxa + gya * gya)
        for dj in range(-2, 3):
            jj = aj + dj
            if jj < 0 or jj >= ncy:
                continue
            for di in range(-2, 3):
                ii = ai + di
                if ii < 0 or ii >= ncx:
                    continue
                b = jj * ncx + ii
                if cmin[b] < 0:
                    continue
                sb = state[b]
                if sb == 1 or sb == want or (smooth and sb != 0):
                    continue
                dx = di * c + mx[b] - mx[a]
                dy = dj * c + my[b] - my[a]
                rise = gxa * dx + gya * dy
                over = (mz[b] - mz[a] - o - rise) / norm
                if over > ct:
                    continue
                if smooth:
                    if over < -ct:
                        continue
                elif rise > tan_up * math.sqrt(dx * dx + dy * dy):
                    continue
                state[b] = want
                if over >= -ct:  # the same surface goes on; a drop to another level keeps the slope of the cloth
                    gx[b] = gxa
                    gy[b] = gya
                queue[tail] = b
                tail += 1
    return tail


@njit(cache=True, nogil=True)
def grow(cmin, mx, my, mz, state, gx, gy, edge, ncx, ncy, c, ct, tan_up, min_cells, min_edge):  # type: ignore[no-untyped-def]
    """Ground from the seeds (state 1) over the grid of the lowest returns; gx, gy — the slope at every ground
    cell (in: the slope of the cloth). `edge` marks the cells at the edge of the data; min_cells = 0 switches
    the smooth areas at the edge off. Returns (ground cells, smooth areas taken, their cells)."""
    ncell = ncx * ncy
    queue = np.empty(ncell, np.int32)
    bx = np.empty(25)
    by = np.empty(25)
    bz = np.empty(25)
    use = np.empty(25, np.uint8)
    tail = 0
    for cell in range(ncell):
        if state[cell] == 1:
            queue[tail] = cell
            tail += 1
    tail = _spread(queue, 0, tail, 1, False, cmin, mx, my, mz, state, gx, gy, ncx, ncy, c, ct, tan_up, bx, by, bz, use)
    areas = 0
    area_cells = 0
    if min_cells > 0:
        left = 0
        for cell in range(ncell):
            if state[cell] == 0 and cmin[cell] >= 0:
                left += 1
        trial = np.empty(left, np.int32)
        for cell in range(ncell):
            if state[cell] != 0 or cmin[cell] < 0:
                continue
            state[cell] = 2
            trial[0] = cell
            n = _spread(trial, 0, 1, 2, True, cmin, mx, my, mz, state, gx, gy, ncx, ncy, c, ct, tan_up, bx, by, bz, use)
            at_edge = 0
            for k in range(n):
                at_edge += edge[trial[k]]
            if n >= min_cells and at_edge >= min_edge:
                head = tail
                for k in range(n):
                    state[trial[k]] = 1
                    queue[tail] = trial[k]
                    tail += 1
                tail = _spread(
                    queue, head, tail, 1, False, cmin, mx, my, mz, state, gx, gy, ncx, ncy, c, ct, tan_up, bx, by, bz, use
                )
                areas += 1
                area_cells += n
            else:
                for k in range(n):
                    state[trial[k]] = 3
        for cell in range(ncell):
            if state[cell] == 3:
                state[cell] = 0
    return tail, areas, area_cells


@njit(cache=True, nogil=True)
def edge_rows(cmin, mask, ncx, row0, row1, edge):  # type: ignore[no-untyped-def]
    """edge[cell] = 1 for the cells of rows [row0, row1) that hold a return and stand at the edge of the data:
    one of the 4 × 4 particles around the cell is outside the system."""
    npy, npx = mask.shape
    for cj in range(row0, row1):
        pj = cj // 2
        for ci in range(ncx):
            cell = cj * ncx + ci
            edge[cell] = 0
            if cmin[cell] < 0:
                continue
            pi = ci // 2
            for j in range(pj - 1, pj + 3):
                if j < 0 or j >= npy:
                    continue
                for i in range(pi - 1, pi + 3):
                    if i >= 0 and i < npx and not mask[j, i]:
                        edge[cell] = 1


# --- surface and classes ---------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def plane_rows(cmin, mx, my, mz, state, gx, gy, ncx, ncy, row0, row1, c, ct, band, g, gn):  # type: ignore[no-untyped-def]
    """The plane of every ground cell of rows [row0, row1): g — its height at the centre of the cell, gx, gy —
    its slope (rewritten), gn — 1 / √(1 + gx² + gy²). Not ground — g is NaN."""
    bx = np.empty(25)
    by = np.empty(25)
    bz = np.empty(25)
    use = np.empty(25, np.uint8)
    for cj in range(row0, row1):
        for ci in range(ncx):
            a = cj * ncx + ci
            if state[a] != 1:
                g[a] = np.nan
                gn[a] = 1.0
                continue
            n, o, gxa, gya = _plane_at(a, 1, cmin, mx, my, mz, state, gx[a], gy[a], ncx, ncy, c, ct, bx, by, bz, use)
            # without the returns above the plane: low vegetation taken on the way lifts it
            for _ in range(2):
                for k in range(n):
                    if use[k] and bz[k] - (o + gxa * bx[k] + gya * by[k]) > band:
                        use[k] = 0
                ok, a0, b0, c0 = fit_plane(bx, by, bz, use, n)
                if not ok:
                    break
                o = a0
                gxa = b0
                gya = c0
            gx[a] = gxa
            gy[a] = gya
            g[a] = mz[a] + o - gxa * mx[a] - gya * my[a]
            gn[a] = 1.0 / math.sqrt(1.0 + gxa * gxa + gya * gya)


@njit(cache=True, nogil=True)
def class_of(h, ct):  # type: ignore[no-untyped-def]
    """ASPRS class by the height above the ground along the normal: 2 within CT, 1 below, 3 / 4 / 5 above."""
    if h < -ct:
        return 1
    if h <= ct:
        return 2
    if h <= 0.5:
        return 3
    if h <= 2.0:
        return 4
    return 5


@njit(cache=True, nogil=True)
def classify_rows(xyz, order, start, state, g, gx, gy, gn, u, mask, ncx, ncy, row0, row1, ox, oy, c, cr, cx0, cy0, ct, pcls, ph):  # type: ignore[no-untyped-def]
    """pcls, ph for the points of rows [row0, row1): the height above the ground along the normal and the
    class. Noise keeps class 7 (its height is still measured). No ground cell and no cloth near — NaN, class 1."""
    npy, npx = u.shape
    for cj in range(row0, row1):
        for ci in range(ncx):
            cell = cj * ncx + ci
            s = start[cell]
            e = start[cell + 1]
            if s == e:
                continue
            for k in range(s, e):
                i = order[k]
                x = xyz[i, 0] - ox - cx0 * c
                y = xyz[i, 1] - oy - cy0 * c
                z = xyz[i, 2]
                best = 0.0
                found = False
                for reach in range(1, 3):
                    for dj in range(-reach, reach + 1):
                        jj = cj + dj
                        if jj < 0 or jj >= ncy:
                            continue
                        for di in range(-reach, reach + 1):
                            ii = ci + di
                            if ii < 0 or ii >= ncx:
                                continue
                            b = jj * ncx + ii
                            if state[b] != 1:
                                continue
                            d = (z - (g[b] + gx[b] * (x - (ii + 0.5) * c) + gy[b] * (y - (jj + 0.5) * c))) * gn[b]
                            if not found or abs(d) < abs(best):
                                best = d
                                found = True
                    if found:
                        break
                if found:
                    ph[k] = best
                    if pcls[k] != 7:
                        pcls[k] = class_of(best, ct)
                    continue
                # no ground cell near: the height above the cloth (a roof, a crown), never ground
                px = x / cr
                py = y / cr
                pi = min(max(int(math.floor(px)), 0), npx - 2)
                pj = min(max(int(math.floor(py)), 0), npy - 2)
                if not (mask[pj, pi] and mask[pj, pi + 1] and mask[pj + 1, pi] and mask[pj + 1, pi + 1]):
                    ph[k] = np.nan
                    if pcls[k] != 7:
                        pcls[k] = 1
                    continue
                tx = px - pi
                ty = py - pj
                u00 = u[pj, pi]
                u10 = u[pj, pi + 1]
                u01 = u[pj + 1, pi]
                u11 = u[pj + 1, pi + 1]
                ub = (u00 * (1.0 - tx) + u10 * tx) * (1.0 - ty) + (u01 * (1.0 - tx) + u11 * tx) * ty
                sx = ((u10 - u00) * (1.0 - ty) + (u11 - u01) * ty) / cr
                sy = ((u01 - u00) * (1.0 - tx) + (u11 - u10) * tx) / cr
                d = (z - ub) / math.sqrt(1.0 + sx * sx + sy * sy)
                ph[k] = d
                if pcls[k] != 7:
                    cl = class_of(d, ct)
                    pcls[k] = 1 if cl == 2 else cl
