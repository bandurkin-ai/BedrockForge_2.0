"""The kernels of the pit slope template (подшаг 21.3; ТЗ 8.11.2; ADR 0028 decisions 10–12): numba `cache=True`,
float64. The Python layer (`template.py`) validates the input, caches equal tables and makes the result.

Conventions (shared with `template.py`):
  * a local offset (Δi, Δj) of a grid with the steps dx, dy turned by φ about Z is the vector (a, b) = (Δi·dx, Δj·dy)
    in the local axes; in the project axes it is (px, py) = (c·a − s·b, s·a + c·b), (s, c) = (sin φ, cos φ) — the same
    as `GridV2.to_project`; the azimuth is clockwise from north: az = atan2(px, py), degrees in [0; 360);
  * the cone of a level m: the offset is in it if hypot(a, b) ≤ m·rh, rh = dz / tan φ(az) (the horizontal reach of one
    level), with the relative slack 1e−12 so that a tie (a Pythagorean triple at 45°) does not depend on the last bit;
  * the dense grids of the offsets are indexed [Δi + wi, Δj + wj]; a composition of template offsets of a level l never
    leaves the grid (its length ≤ l·rh_max ≤ K·rh_max < wi·dx, wj·dy), so a sum is cut only by the grid's edge, never
    wrongly.

The reduction of transitive arcs: the offset of the level m is NOT taken if it is the sum of a template offset of a
smaller level m1 and an offset reachable on m − m1 levels (a composition of template offsets) — so `reach[l]` is the
set of all compositions of the levels that add up to l (the template of the level l and what is excluded from it).
"""

from __future__ import annotations

import math

import numpy as np

from bf_core.jit import njit

SLACK = 1e-12  # a relative slack of the cone test: ties do not depend on the last bit of the angle table


@njit(cache=True, nogil=True)
def phi_at(az, cs, an):  # type: ignore[no-untyped-def]
    """The slope angle (degrees) at the azimuth `az`: linear between the centres `cs` (sorted, [0; 360)) round the
    circle; one row — a constant."""
    n = cs.shape[0]
    if n == 1:
        return an[0]
    a = az % 360.0
    i = n - 1  # before the first centre: the segment from the last one, through north
    for k in range(n):
        if cs[k] <= a:
            i = k
    a0 = cs[i]
    j = i + 1
    if j == n:
        j = 0
        a1 = cs[0] + 360.0
    else:
        a1 = cs[j]
    x = a if a >= a0 else a + 360.0
    return an[i] + (an[j] - an[i]) * (x - a0) / (a1 - a0)


@njit(cache=True, nogil=True)
def phi_row(cs, an):  # type: ignore[no-untyped-def]
    """φ(az) at az = 0°, 1°, … 359° (project azimuths)."""
    out = np.empty(360, dtype=np.float64)
    for d in range(360):
        out[d] = phi_at(float(d), cs, an)
    return out


@njit(cache=True, nogil=True)
def cone_grid(dx, dy, dz, sn, cs_, cs, an, wi, wj):  # type: ignore[no-untyped-def]
    """`dist` (the length of the offset, local axes) and `rh` (dz / tan φ(az) of its project azimuth) over the dense
    grid [Δi + wi, Δj + wj]; the zero offset has the reach of the first row (its distance is 0 anyway)."""
    ni = 2 * wi + 1
    nj = 2 * wj + 1
    dist = np.empty((ni, nj), dtype=np.float64)
    rh = np.empty((ni, nj), dtype=np.float64)
    for a in range(ni):
        u = (a - wi) * dx
        for b in range(nj):
            v = (b - wj) * dy
            dist[a, b] = math.hypot(u, v)
            if u == 0.0 and v == 0.0:
                phi = an[0]
            else:
                az = math.degrees(math.atan2(cs_ * u - sn * v, sn * u + cs_ * v))
                if az < 0.0:
                    az += 360.0
                phi = phi_at(az, cs, an)
            rh[a, b] = dz / math.tan(math.radians(phi))
    return dist, rh


@njit(cache=True, nogil=True)
def build_offsets(dist, rh, k_levels, wi, wj):  # type: ignore[no-untyped-def]
    """The template: `offs` (n, 3) int32 (Δi, Δj, m) in the canonical order (m, Δj, Δi) and `ptr` (K + 1) int32 —
    the level m (1…K) is the rows [ptr[m − 1]; ptr[m])."""
    ni, nj = dist.shape
    reach = np.zeros((k_levels + 1, ni, nj), dtype=np.uint8)
    ptr = np.zeros(k_levels + 1, dtype=np.int32)
    cap = 0
    for m in range(1, k_levels + 1):  # an upper bound: the cone cells of every level
        for a in range(ni):
            for b in range(nj):
                if dist[a, b] <= m * rh[a, b] * (1.0 + SLACK):
                    cap += 1
    offs = np.empty((cap, 3), dtype=np.int32)
    n = 0
    without = np.zeros((ni, nj), dtype=np.uint8)
    for m in range(1, k_levels + 1):
        without[:, :] = 0
        for m1 in range(1, m):  # sum = template offset t of the level m1 + a composition of m − m1 levels
            src = reach[m - m1]
            for r in range(ptr[m1 - 1], ptr[m1]):
                ti = offs[r, 0]
                tj = offs[r, 1]
                for a in range(max(0, -ti), min(ni, ni - ti)):
                    for b in range(max(0, -tj), min(nj, nj - tj)):
                        if src[a, b] != 0:
                            without[a + ti, b + tj] = 1
        for b in range(nj):  # canonical order: Δj, then Δi
            for a in range(ni):
                if without[a, b] == 0 and dist[a, b] <= m * rh[a, b] * (1.0 + SLACK):
                    offs[n, 0] = a - wi
                    offs[n, 1] = b - wj
                    offs[n, 2] = m
                    n += 1
                    reach[m, a, b] = 1
                if without[a, b] != 0:
                    reach[m, a, b] = 1
        ptr[m] = n
    return offs[:n].copy(), ptr


@njit(cache=True, nogil=True)
def effective_angles(offs, ptr, dx, dy, dz, sn, cs_, k_levels):  # type: ignore[no-untyped-def]
    """φ_эфф(az) at az = 0°, 1°, … 359° after M = 2K levels (ADR 0028 decision 12).

    ρ(az) = max over the offsets o that the template reaches in exactly M levels of o·û(az). The max of a sum over the
    compositions is the DP over the levels: H[0] = 0, H[l] = max over the levels m ≤ min(K, l) with a template of
    (S_m(az) + H[l − m]), S_m — the max of o·û over the template of the level m: the same number as the max over the
    breadth-first closure, with no set of offsets (the closure of 40 levels is hundreds of thousands of points).
    φ_эфф = atan(M·dz / ρ), in degrees."""
    m_levels = 2 * k_levels
    n = offs.shape[0]
    px = np.empty(n, dtype=np.float64)
    py = np.empty(n, dtype=np.float64)
    for r in range(n):
        u = offs[r, 0] * dx
        v = offs[r, 1] * dy
        px[r] = cs_ * u - sn * v
        py[r] = sn * u + cs_ * v
    out = np.empty(360, dtype=np.float64)
    s_m = np.empty(k_levels + 1, dtype=np.float64)
    h = np.empty(m_levels + 1, dtype=np.float64)
    neg = -1.0e300
    for d in range(360):
        ux = math.sin(math.radians(d))
        uy = math.cos(math.radians(d))
        for m in range(1, k_levels + 1):
            best = neg
            for r in range(ptr[m - 1], ptr[m]):
                t = px[r] * ux + py[r] * uy
                if t > best:
                    best = t
            s_m[m] = best
        h[0] = 0.0
        for lv in range(1, m_levels + 1):
            best = neg
            for m in range(1, min(k_levels, lv) + 1):
                if s_m[m] > neg and h[lv - m] > neg:
                    t = s_m[m] + h[lv - m]
                    if t > best:
                        best = t
            h[lv] = best
        rho = h[m_levels]
        if rho <= 0.0:
            out[d] = 90.0
        else:
            out[d] = math.degrees(math.atan(m_levels * dz / rho))
    return out
