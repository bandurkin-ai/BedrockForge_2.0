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
    A partition (every neighbour abuts): the candidate floor((a − lo_0)/Δ) moved while the inequalities say
    so. Otherwise: the nearest plane to (a + b)/2 among the windows that hold [a, b], the smaller k on a tie."""
    last = offs.shape[0] - 1
    lo0 = lo[0]
    if partition:
        for i in range(a.shape[0]):
            x, y = a[i], b[i]
            k = -1
            if x >= lo_min and y < hi_max and x <= y:
                c = math.floor((x - lo0) / step)
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
    for i in range(a.shape[0]):
        x, y = a[i], b[i]
        best = -1
        if x >= lo_min and y < hi_max and x <= y:
            m = 0.5 * x + 0.5 * y
            j0 = int(max(math.ceil((y - r - s0) / step), 0.0))
            j1 = int(min(math.floor((x + r - s0) / step), float(last)))
            gap = math.inf
            for j in range(j0, j1 + 1):
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
