"""The cloth of the ground filter, stages 3–5 (`geo/ground.py`, ADR 0014 §4): the obstacle under every
particle, the rest position of the cloth, the seeds of the ground.

The cloud is not turned over: the cloth is pressed to the ground from below. A particle (i, j) of a block
stands at the corner of four cells, at (cx0·c + i·CR, cy0·c + j·CR) from the origin of the cloud.

Obstacle. The height of a plane through the lowest returns of the cells within 1.5·CR of the particle, taken at
the particle (Д-22, idea 2: on a 70° face the nearest return is a metre away from the surface under the
particle). The plane is fitted to the lower returns (`ground_kernels.lower_plane`): under vegetation half of
the cells hold no ground. No return within 2·CR — the particle is outside the system.

Rest position (Д-22, idea 1: solved, not dropped step by step). A membrane under a load p with the one-sided
constraint u ≤ obstacle:  u = min(h, (Σ neighbours + p·CR²) / number of neighbours)  at every particle; a
missing neighbour (the edge of the data) is left out — a free edge. The system is solved on a cascade of
grids, from at most 64 × 64 up to CR: the coarse solution starts the next finer one, every level is relaxed by
red-black sweeps with projection (and over-relaxation) until a sweep moves no particle by more than `tol`.
A red particle reads black ones only, so the strips of one colour may run on any number of threads — the
result is the same bit for bit. The depth of a pit does not enter: there are no time steps.

Seeds. The cells whose lowest return is not above the cloth by more than CT along the normal to the cloth.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np

from bf_core.jit import njit

from .ground_kernels import lower_plane

OMEGA = 1.9  # over-relaxation of the sweeps: free spans of the cloth are tens of particles wide
COARSEST = 64  # the cascade starts from a grid of at most this many particles a side
MAX_SWEEPS = 20_000
CHUNK = 16  # sweeps between two calls of `tick` (cancel)
THREAD_NODES = 1 << 17  # smaller grids are swept on the calling thread

Rows = Callable[[Callable[[int, int], Any], int], list[Any]] | None

# --- obstacle --------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def obstacle_rows(cmin, mx, my, mz, ncx, ncy, npx, row0, row1, c, cr, band, hob, mask):  # type: ignore[no-untyped-def]
    """hob, mask for the particles of rows [row0, row1): the obstacle under the particle and whether the
    particle is in the system (a return within 2·CR)."""
    reach = int(math.ceil(2.0 * cr / c))
    size = 4 * reach * reach
    r_near = (1.5 * cr) ** 2
    r_far = (2.0 * cr) ** 2
    bx = np.empty(size)
    by = np.empty(size)
    bz = np.empty(size)
    fx = np.empty(size)
    fy = np.empty(size)
    fz = np.empty(size)
    use = np.empty(size, np.uint8)
    for pj in range(row0, row1):
        for pi in range(npx):
            near = 0
            far = 0
            for cj in range(2 * pj - reach, 2 * pj + reach):
                if cj < 0 or cj >= ncy:
                    continue
                for ci in range(2 * pi - reach, 2 * pi + reach):
                    if ci < 0 or ci >= ncx:
                        continue
                    cell = cj * ncx + ci
                    if cmin[cell] < 0:
                        continue
                    dx = (ci + 0.5 - 2 * pi) * c + mx[cell]
                    dy = (cj + 0.5 - 2 * pj) * c + my[cell]
                    d2 = dx * dx + dy * dy
                    if d2 <= r_near:
                        bx[near] = dx
                        by[near] = dy
                        bz[near] = mz[cell]
                        near += 1
                    elif d2 <= r_far:
                        fx[far] = dx
                        fy[far] = dy
                        fz[far] = mz[cell]
                        far += 1
            if near + far == 0:
                mask[pj, pi] = 0
                hob[pj, pi] = 0.0
                continue
            mask[pj, pi] = 1
            ok = False
            a = 0.0
            if near >= 3:
                ok, a, _b, _c = lower_plane(bx, by, bz, use, near, band)
            if not ok:
                # few returns near the particle: the wider ring joins them
                for k in range(far):
                    bx[near + k] = fx[k]
                    by[near + k] = fy[k]
                    bz[near + k] = fz[k]
                total = near + far
                if total >= 3:
                    ok, a, _b, _c = lower_plane(bx, by, bz, use, total, band)
                if not ok:
                    a = 0.0
                    for k in range(total):
                        a += bz[k]
                    a /= total
            hob[pj, pi] = a


# --- cascade ---------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def restrict(h, mask):  # type: ignore[no-untyped-def]
    """The next coarser grid: node (J, I) stands on the fine node (2J, 2I) and takes the lowest obstacle of the
    fine nodes around it (3 × 3); it is in the system when one of them is."""
    ny, nx = h.shape
    my = ny // 2 + 1
    mx = nx // 2 + 1
    hc = np.zeros((my, mx))
    mc = np.zeros((my, mx), np.uint8)
    for jj in range(my):
        for ii in range(mx):
            best = 0.0
            any_ = False
            for j in range(max(2 * jj - 1, 0), min(2 * jj + 2, ny)):
                for i in range(max(2 * ii - 1, 0), min(2 * ii + 2, nx)):
                    if mask[j, i]:
                        if not any_ or h[j, i] < best:
                            best = h[j, i]
                        any_ = True
            if any_:
                hc[jj, ii] = best
                mc[jj, ii] = 1
    return hc, mc


@njit(cache=True, nogil=True)
def prolong(uc, mc, u, h, mask):  # type: ignore[no-untyped-def]
    """The coarse solution carried to the fine grid (mean of the coarse nodes around), not above the obstacle."""
    ny, nx = h.shape
    for j in range(ny):
        j0 = j // 2
        j1 = j0 + (j & 1)
        for i in range(nx):
            if not mask[j, i]:
                u[j, i] = 0.0
                continue
            i0 = i // 2
            i1 = i0 + (i & 1)
            s = 0.0
            n = 0
            if mc[j0, i0]:
                s += uc[j0, i0]
                n += 1
            if i1 != i0 and mc[j0, i1]:
                s += uc[j0, i1]
                n += 1
            if j1 != j0:
                if mc[j1, i0]:
                    s += uc[j1, i0]
                    n += 1
                if i1 != i0 and mc[j1, i1]:
                    s += uc[j1, i1]
                    n += 1
            v = h[j, i]
            if n > 0 and s / n < v:
                v = s / n
            u[j, i] = v


@njit(cache=True, nogil=True)
def sweep_rows(u, h, mask, row0, row1, color, load, omega):  # type: ignore[no-untyped-def]
    """One colour of a projected sweep over rows [row0, row1) → the largest move of a particle."""
    ny, nx = h.shape
    moved = 0.0
    for j in range(row0, row1):
        for i in range((j + color) & 1, nx, 2):
            if not mask[j, i]:
                continue
            s = 0.0
            n = 0
            if i > 0 and mask[j, i - 1]:
                s += u[j, i - 1]
                n += 1
            if i + 1 < nx and mask[j, i + 1]:
                s += u[j, i + 1]
                n += 1
            if j > 0 and mask[j - 1, i]:
                s += u[j - 1, i]
                n += 1
            if j + 1 < ny and mask[j + 1, i]:
                s += u[j + 1, i]
                n += 1
            old = u[j, i]
            new = h[j, i]
            if n > 0:
                v = old + omega * ((s + load) / n - old)
                if v < new:
                    new = v
            d = abs(new - old)
            if d > moved:
                moved = d
            u[j, i] = new
    return moved


@njit(cache=True, nogil=True)
def sweeps(u, h, mask, load, omega, tol, limit):  # type: ignore[no-untyped-def]
    """Up to `limit` full sweeps on the calling thread → (sweeps done, the largest move of the last one)."""
    ny = h.shape[0]
    moved = 0.0
    done = 0
    while done < limit:
        a = sweep_rows(u, h, mask, 0, ny, 0, load, omega)
        b = sweep_rows(u, h, mask, 0, ny, 1, load, omega)
        moved = max(a, b)
        done += 1
        if moved < tol:
            break
    return done, moved


def solve(
    hob: np.ndarray,
    mask: np.ndarray,
    load: float,
    *,
    tol: float = 1e-4,
    omega: float = OMEGA,
    rows: Rows = None,
    tick: Callable[[], None] | None = None,
) -> tuple[np.ndarray, list[int]]:
    """Rest position of the cloth over the obstacle `hob` (ny, nx) of the particles `mask`; `load` = p·CR².

    Returns (u, sweeps of every level from the coarsest to the finest). `rows(fn, ny)` runs fn(row0, row1) over
    strips of rows on threads (None — on the calling thread); `tick()` is called every few sweeps and may raise.
    """
    levels = [(np.ascontiguousarray(hob, np.float64), np.ascontiguousarray(mask, np.uint8))]
    while max(levels[-1][0].shape) > COARSEST:
        levels.append(restrict(*levels[-1]))
    counts: list[int] = []
    u = levels[-1][0].copy()  # the coarsest level starts on the obstacle
    for depth in range(len(levels) - 1, -1, -1):
        h, m = levels[depth]
        if depth < len(levels) - 1:
            fine = np.empty_like(h)
            prolong(u, levels[depth + 1][1], fine, h, m)
            u = fine
        step_load = load * 4.0**depth
        threaded = rows is not None and h.size >= THREAD_NODES
        done = 0
        while done < MAX_SWEEPS:
            if threaded and rows is not None:
                moved = 0.0
                for _ in range(CHUNK):
                    for color in (0, 1):
                        moved_c = max(rows(lambda a, b, c=color: sweep_rows(u, h, m, a, b, c, step_load, omega), h.shape[0]))
                        moved = moved_c if color == 0 else max(moved, moved_c)
                    done += 1
                    if moved < tol:
                        break
            else:
                n, moved = sweeps(u, h, m, step_load, omega, tol, CHUNK)
                done += n
            if tick:
                tick()
            if moved < tol:
                break
        counts.append(done)
    return u, counts


# --- seeds -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def seed_rows(cmin, mx, my, mz, u, ncx, row0, row1, c, cr, ct, state, gx, gy):  # type: ignore[no-untyped-def]
    """state[cell] = 1 for the cells of rows [row0, row1) whose lowest return is not above the cloth by more
    than CT along its normal; gx, gy — the slope of the cloth there (the first guess of the slope of the ground).
    Returns the number of seeds."""
    seeds = 0
    for cj in range(row0, row1):
        for ci in range(ncx):
            cell = cj * ncx + ci
            state[cell] = 0
            gx[cell] = 0.0
            gy[cell] = 0.0
            if cmin[cell] < 0:
                continue
            # the four particles around the return (all of them are in the system: it is within 2·CR of each)
            x = ((ci + 0.5) * c + mx[cell]) / cr
            y = ((cj + 0.5) * c + my[cell]) / cr
            pi = int(math.floor(x))
            pj = int(math.floor(y))
            if pi > u.shape[1] - 2:
                pi = u.shape[1] - 2
            if pj > u.shape[0] - 2:
                pj = u.shape[0] - 2
            tx = x - pi
            ty = y - pj
            u00 = u[pj, pi]
            u10 = u[pj, pi + 1]
            u01 = u[pj + 1, pi]
            u11 = u[pj + 1, pi + 1]
            ub = (u00 * (1.0 - tx) + u10 * tx) * (1.0 - ty) + (u01 * (1.0 - tx) + u11 * tx) * ty
            sx = ((u10 - u00) * (1.0 - ty) + (u11 - u01) * ty) / cr
            sy = ((u01 - u00) * (1.0 - tx) + (u11 - u10) * tx) / cr
            gx[cell] = sx
            gy[cell] = sy
            if (mz[cell] - ub) / math.sqrt(1.0 + sx * sx + sy * sy) <= ct:
                state[cell] = 1
                seeds += 1
    return seeds
