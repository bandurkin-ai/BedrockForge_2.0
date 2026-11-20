"""numba kernels of the series of sections (ADR 0016 §2): the depths of points, the depth ranges of lines, the
section of a depth range, the segment of a polyline section. Imported lazily by `geometry.py` and
`polyline.py`, never before BF-READY.

The arithmetic is that of the numpy functions of `geometry.py` term by term (no fused multiply-add: numba does
not contract without fastmath), so `depths` and `SeriesGeometry.depth` give the same τ bit for bit.
Measured on 10⁶ random points (ADR 0016 «Замеры»): P1 and v̂ come as scalars (read from arrays in the loop they
cost ten times the loop); the depths and the search are two kernels (the branches of the depth inside the loop
of the search double it); the search is written out in its loop (an inlined helper costs twice as much).
"""

from __future__ import annotations

import math

from bf_core.jit import njit


@njit(cache=True, nogil=True)
def depths(xyz, x1, y1, z1, vx, vy, vz, out):  # type: ignore[no-untyped-def]
    """out[i] = (p_i − P1)·v̂ as `geometry.dot`: from 0.0, the terms x, y, z, a zero component skipped."""
    for i in range(xyz.shape[0]):
        t = 0.0
        if vx != 0.0:
            t += (xyz[i, 0] - x1) * vx
        if vy != 0.0:
            t += (xyz[i, 1] - y1) * vy
        if vz != 0.0:
            t += (xyz[i, 2] - z1) * vz
        out[i] = t


@njit(cache=True, nogil=True)
def depth_ranges(xyz, offsets, x1, y1, z1, vx, vy, vz, mn, mx):  # type: ignore[no-untyped-def]
    """[mn, mx] of the depths of the vertices of every line offsets[i]:offsets[i+1] (absolute vertex numbers);
    NaN — the line is empty or a depth is NaN."""
    for li in range(offsets.shape[0] - 1):
        lo, hi = math.inf, -math.inf
        for i in range(offsets[li], offsets[li + 1]):
            t = 0.0
            if vx != 0.0:
                t += (xyz[i, 0] - x1) * vx
            if vy != 0.0:
                t += (xyz[i, 1] - y1) * vy
            if vz != 0.0:
                t += (xyz[i, 2] - z1) * vz
            if t != t:
                lo = math.nan
                break
            lo = min(lo, t)
            hi = max(hi, t)
        if lo != lo or offsets[li + 1] <= offsets[li]:
            mn[li] = math.nan
            mx[li] = math.nan
        else:
            mn[li] = lo
            mx[li] = hi


@njit(cache=True, nogil=True)
def find_sections(a, b, s0, step, offs, lo, hi, lo_min, hi_max, reach, partition, out):  # type: ignore[no-untyped-def]
    """out[i] — the section whose window lo_k ≤ τ < hi_k holds the depths [a[i], b[i]], −1 — none (NaN — none).
    A partition (every neighbour abuts): the candidate floor((a − lo_1)/Δ) + 1 moved while the inequalities say
    so — from lo_1, not lo_0: an own window of section 0 «к себе» may be up to 10 000 m, and the faces lo_1, lo_2,
    … are one per Δ. Otherwise: the nearest plane to (a + b)/2 among the windows that hold [a, b], the smaller k on
    a tie — the planes are visited from the nearest one outwards (each side in the order of the distance, the
    lower one first on a tie) up to the first window that holds the range, then those as near are compared as by
    the full scan: windows ≫ Δ cost a few steps, not all the series; a range wider than every window — none."""
    last = offs.shape[0] - 1
    if partition:
        base = lo[1] if last >= 1 else lo[0]
        shift = 1.0 if last >= 1 else 0.0
        for i in range(a.shape[0]):
            x, y = a[i], b[i]
            k = -1
            if x >= lo_min and y < hi_max and x <= y:
                c = math.floor((x - base) / step) + shift
                k = int(min(max(c, 0.0), float(last)))
                while k > 0 and x < lo[k]:
                    k -= 1
                while k < last and x >= hi[k]:
                    k += 1
                if not (lo[k] <= x and y < hi[k]):
                    k = -1
            out[i] = k
        return
    r = reach + step  # a window is inside [s_j − reach, s_j + reach] up to the rounding of its faces
    wide = 0.0
    for j in range(last + 1):
        wide = max(wide, hi[j] - lo[j])
    for i in range(a.shape[0]):
        x, y = a[i], b[i]
        best = -1
        if x >= lo_min and y < hi_max and x <= y and not (y - x > wide):
            m = 0.5 * x + 0.5 * y
            j0 = int(max(math.ceil((y - r - s0) / step), 0.0))
            j1 = int(min(math.floor((x + r - s0) / step), float(last)))
            if j0 <= j1:
                # c — the last plane at or below m in [j0, j1] (j0 when all are above)
                c = int(min(max(math.floor((m - s0) / step), float(j0)), float(j1)))
                while c > j0 and offs[c] > m:
                    c -= 1
                while c < j1 and offs[c + 1] <= m:
                    c += 1
                jl, jh = c, c + 1
                gap = math.inf
                while jl >= j0 or jh <= j1:
                    dl = abs(m - offs[jl]) if jl >= j0 else math.inf
                    dh = abs(m - offs[jh]) if jh <= j1 else math.inf
                    if dl <= dh:
                        j, d = jl, dl
                        jl -= 1
                    else:
                        j, d = jh, dh
                        jh += 1
                    if lo[j] <= x and y < hi[j]:
                        gap = d
                        break
                if gap < math.inf:
                    # every plane as near as the first window found, compared in the order of the full scan
                    p0, p1 = c, c + 1
                    while p0 >= j0 and abs(m - offs[p0]) <= gap:
                        p0 -= 1
                    while p1 <= j1 and abs(m - offs[p1]) <= gap:
                        p1 += 1
                    gap = math.inf
                    for j in range(p0 + 1, p1):
                        if lo[j] <= x and y < hi[j]:
                            d = abs(m - offs[j])
                            if d < gap:
                                gap = d
                                best = j
        out[i] = best


@njit(cache=True, nogil=True)
def polyline_segments(xyz, pts, sx, sy, vx, vy, lengths, corridor2, out):  # type: ignore[no-untyped-def]
    """The segment of every point: the nearest in plan (squared distance to the segment: the smaller of the
    distances to its ends and, when the projection falls inside it, to its line), the smaller number on a
    tie — a point at a bend or in its outer wedge is at the same distance from both segments, computed by the
    same formula; −1 — farther than the corridor, or x, y not finite."""
    m = lengths.shape[0]
    for i in range(xyz.shape[0]):
        px, py = xyz[i, 0], xyz[i, 1]
        out[i] = -1
        if not (math.isfinite(px) and math.isfinite(py)):
            continue
        best = -1
        gap = math.inf
        for j in range(m):
            dx = px - pts[j, 0]
            dy = py - pts[j, 1]
            d2 = dx * dx + dy * dy
            ex = px - pts[j + 1, 0]
            ey = py - pts[j + 1, 1]
            e2 = ex * ex + ey * ey
            if e2 < d2:
                d2 = e2
            along = dx * sx[j] + dy * sy[j]
            if along > 0.0 and along < lengths[j]:
                t = dx * vx[j] + dy * vy[j]
                if t * t < d2:
                    d2 = t * t
            if d2 < gap:
                gap = d2
                best = j
        if gap <= corridor2:
            out[i] = best
