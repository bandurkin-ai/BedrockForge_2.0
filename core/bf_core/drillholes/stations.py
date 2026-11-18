"""Survey rows → stations of the trajectories: the kernels behind `desurvey.desurvey` (ТЗ 7.11; 7.35 Д-16).

A survey row is a station when it has a depth and either angles in range or coordinates. A station with
coordinates x, y, z (a vertex of a trench, ADR 0011 §1) wins over the desurvey method: the path goes through
it exactly — its position is the stored float64, not a sum of course displacements — and the course that ends
at it is the straight segment from the station above, linear in depth.

Every consumer of `Trajectories` (`positions`, `sample_paths`, the 3D paths of `viz.arcs`) computes a course
from the directions at its two ends, and equal directions give a straight course whatever the method. So a
straight course is stored as two equal directions: its chord divided by its depth range (a unit vector when
depth is the 3D length along the polyline, as the trench import writes it). A station between two courses of
which at least one is such a segment therefore takes two entries with the same depth and position: the first
closes the course above, the second opens the course below. Holes without coordinates in their surveys get
exactly the stations they had before.

Directions of a station with coordinates for the neighbouring courses computed by the method: its own angles
when it has them, else the direction it was reached with. Below the last station the path goes on with the
last direction up to TD.
"""

from __future__ import annotations

import math

import numpy as np

from bf_core.jit import njit

from .desurvey import SOURCE_COLLAR, SOURCE_SURVEY, SOURCE_VERTICAL, _collar_has_angles, _delta, _unit


@njit(cache=True, nogil=True)
def group_surveys(hole, depth, azimuth, dip, sx, sy, sz, n_holes):  # type: ignore[no-untyped-def]
    """Valid stations grouped by hole, ascending by depth, without repeated depths (the first row wins).

    Returns start (n_holes + 1), depth, azimuth and dip (NaN: no usable angles), x, y and z (NaN: no
    coordinates), «hole has survey rows», invalid rows, repeated depths.
    """
    m = hole.shape[0]
    start = np.zeros(n_holes + 1, dtype=np.int64)
    seen = np.zeros(n_holes, dtype=np.bool_)
    kind = np.zeros(m, dtype=np.uint8)  # 0: not a station; 1: angles; 2: coordinates; 3: both
    for i in range(m):
        h = hole[i]
        seen[h] = True
        d = depth[i]
        if 0.0 <= d < np.inf:  # NaN fails
            k = 1 if 0.0 <= azimuth[i] <= 360.0 and -90.0 <= dip[i] <= 90.0 else 0
            if math.isfinite(sx[i]) and math.isfinite(sy[i]) and math.isfinite(sz[i]):
                k += 2
            if k:
                kind[i] = k
                start[h + 1] += 1
    for h in range(n_holes):
        start[h + 1] += start[h]
    n = start[n_holes]
    g = np.empty((6, n), dtype=np.float64)  # depth, azimuth, dip, x, y, z
    fill = start[:n_holes].copy()
    for i in range(m):  # counting sort by hole: stable, O(rows)
        k = kind[i]
        if k:
            j = fill[hole[i]]
            g[0, j] = depth[i] + 0.0  # -0.0 becomes 0.0
            g[1, j] = azimuth[i] if k & 1 else np.nan
            g[2, j] = dip[i] if k & 1 else np.nan
            g[3, j] = sx[i] if k & 2 else np.nan
            g[4, j] = sy[i] if k & 2 else np.nan
            g[5, j] = sz[i] if k & 2 else np.nan
            fill[hole[i]] = j + 1
    w = 0
    for h in range(n_holes):
        a = start[h]
        b = start[h + 1]
        for k in range(a + 1, b):
            if g[0, k] < g[0, k - 1]:  # rarely: rows of a hole usually come ordered
                order = np.argsort(g[0, a:b], kind="mergesort")  # stable
                for c in range(6):
                    g[c, a:b] = g[c, a:b][order]
                break
        start[h] = w
        for k in range(a, b):
            if w > start[h] and g[0, k] == g[0, w - 1]:
                continue
            for c in range(6):
                g[c, w] = g[c, k]
            w += 1
    start[n_holes] = w
    return start, g[0, :w], g[1, :w], g[2, :w], g[3, :w], g[4, :w], g[5, :w], seen, m - n, n - w


@njit(cache=True, nogil=True)
def count_stations(collar_row, cx, cy, cz, ctd, caz, cdip, start, gd, gx, counts, source):  # type: ignore[no-untyped-def]
    for h in range(counts.shape[0]):
        r = collar_row[h]
        if r < 0 or not (math.isfinite(cx[r]) and math.isfinite(cy[r]) and math.isfinite(cz[r])):
            continue  # no coordinates: no trajectory
        a = start[h]
        b = start[h + 1]
        if b > a:
            source[h] = SOURCE_SURVEY
            lead = gd[a] > 0.0
            n = b - a + (1 if lead else 0)
            for s in range(a if lead else a + 1, b - 1):  # stations between two courses
                if math.isfinite(gx[s]) or math.isfinite(gx[s + 1]):  # one of them is a straight segment
                    n += 1
            last = gd[b - 1]
        else:
            source[h] = SOURCE_COLLAR if _collar_has_angles(caz[r], cdip[r]) else SOURCE_VERTICAL
            n = 1
            last = 0.0
        if math.isfinite(ctd[r]) and ctd[r] > last:
            n += 1
        counts[h] = n


@njit(cache=True, nogil=True)
def _collar_dir(azimuth, dip):  # type: ignore[no-untyped-def]
    if _collar_has_angles(azimuth, dip):
        return _unit(azimuth, dip)
    return _unit(0.0, -90.0)


@njit(cache=True, nogil=True)
def fill_stations(method, h0, h1, collar_row, cx, cy, cz, ctd, caz, cdip, start, gd, ga, gp, gx, gy, gz, offsets, md,
                  xyz, dirs):  # type: ignore[no-untyped-def]  # fmt: skip
    """Entries of the holes h0…h1-1 (module docstring). An entry whose direction is not known yet holds NaN
    until the course below it is built: the entry at the collar above a first station, a first station that has
    coordinates but no angles."""
    for h in range(h0, h1):
        o = offsets[h]
        end = offsets[h + 1]
        if end == o:
            continue
        r = collar_row[h]
        a = start[h]
        b = start[h + 1]
        k = o
        if b > a and gd[a] > 0.0:  # the first station is below the collar: an entry at the collar
            md[k] = 0.0
            xyz[k, 0], xyz[k, 1], xyz[k, 2] = cx[r], cy[r], cz[r]
            dirs[k, 0] = np.nan
            k += 1
        for s in range(a, b):
            fixed = math.isfinite(gx[s])
            angles = math.isfinite(ga[s])
            above = k > o  # there is a course into this station, from the entry k - 1
            md[k] = gd[s]
            if fixed:
                xyz[k, 0], xyz[k, 1], xyz[k, 2] = gx[s], gy[s], gz[s]
            if not above:  # the station at the collar
                if not fixed:
                    xyz[k, 0], xyz[k, 1], xyz[k, 2] = cx[r], cy[r], cz[r]
                if angles:
                    dirs[k, 0], dirs[k, 1], dirs[k, 2] = _unit(ga[s], gp[s])
                else:
                    dirs[k, 0] = np.nan
            elif fixed:  # a straight segment: both its ends hold the chord per metre of depth
                seg = md[k] - md[k - 1]
                for c in range(3):
                    dirs[k, c] = (xyz[k, c] - xyz[k - 1, c]) / seg
                    dirs[k - 1, c] = dirs[k, c]
            else:  # a course of the method between the direction above and the angles of this station
                dirs[k, 0], dirs[k, 1], dirs[k, 2] = _unit(ga[s], gp[s])
                if math.isnan(dirs[k - 1, 0]):
                    dirs[k - 1] = dirs[k]
                seg = md[k] - md[k - 1]
                dx, dy, dz = _delta(method, seg, seg, dirs[k - 1, 0], dirs[k - 1, 1], dirs[k - 1, 2],
                                    dirs[k, 0], dirs[k, 1], dirs[k, 2])  # fmt: skip
                xyz[k, 0] = xyz[k - 1, 0] + dx
                xyz[k, 1] = xyz[k - 1, 1] + dy
                xyz[k, 2] = xyz[k - 1, 2] + dz
            k += 1
            if above and s < b - 1 and (fixed or math.isfinite(gx[s + 1])):
                # the second entry of the station: the direction the course below starts with
                md[k] = md[k - 1]
                xyz[k] = xyz[k - 1]
                if angles:
                    dirs[k, 0], dirs[k, 1], dirs[k, 2] = _unit(ga[s], gp[s])
                else:  # no angles (so it has coordinates): the direction it was reached with
                    norm = math.sqrt(dirs[k - 1, 0] ** 2 + dirs[k - 1, 1] ** 2 + dirs[k - 1, 2] ** 2)
                    if norm > 0.0:
                        for c in range(3):
                            dirs[k, c] = dirs[k - 1, c] / norm
                    else:
                        dirs[k, 0], dirs[k, 1], dirs[k, 2] = _unit(0.0, -90.0)
                k += 1
        if b == a:  # no stations: the collar orientation, else vertical
            md[k] = 0.0
            xyz[k, 0], xyz[k, 1], xyz[k, 2] = cx[r], cy[r], cz[r]
            dirs[k, 0], dirs[k, 1], dirs[k, 2] = _collar_dir(caz[r], cdip[r])
            k += 1
        elif math.isnan(dirs[k - 1, 0]):  # the only station has coordinates and no angles
            dirs[k - 1, 0], dirs[k - 1, 1], dirs[k - 1, 2] = _collar_dir(caz[r], cdip[r])
        if k < end:  # extension to TD with the last orientation
            md[k] = ctd[r]
            dirs[k] = dirs[k - 1]
            seg = md[k] - md[k - 1]
            dx, dy, dz = _delta(method, seg, seg, dirs[k - 1, 0], dirs[k - 1, 1], dirs[k - 1, 2],
                                dirs[k, 0], dirs[k, 1], dirs[k, 2])  # fmt: skip
            xyz[k, 0] = xyz[k - 1, 0] + dx
            xyz[k, 1] = xyz[k - 1, 1] + dy
            xyz[k, 2] = xyz[k - 1, 2] + dz
