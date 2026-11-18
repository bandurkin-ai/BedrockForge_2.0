"""Polyline simplification with a tolerance: Douglas–Peucker in 3D (ADR 0011 §3 «LOD — Дуглас–Пекер 4 и 32 м»).

`keep_mask(xyz, offsets, tol, closed)` marks the vertices of every line that stay; `simplify` returns the lines
themselves. Float64 throughout: the tolerance is checked on the coordinates of the project, before any float32.

What is kept, besides the vertices the tolerance asks for:
  * the first and the last vertex of every line, so the ends do not move;
  * a closed line (`closed[i]`, its last vertex repeats the first) stays a closed line: the vertex farthest from
    the first one and the vertex farthest from the two chords it makes are kept, so at least three corners and the
    repeat remain however small the ring is;
  * both vertices where «Z is known» changes to «Z is empty» (NaN): an empty Z is never interpolated or dropped
    between two heights. A run of vertices without Z is simplified in plan;
  * every `MAX_RUN`-th vertex of a long line: the worst case of the method is quadratic in the length of a run
    (a comb of teeth that get shorter), so a run is at most `MAX_RUN` segments — the time is O(N · MAX_RUN) for
    any data and the cost is one vertex in `MAX_RUN`.

The guarantee: every dropped vertex lies within `tol` of the **segment** between the kept vertices around it
(the distance to the segment, not to its infinite line: a spur that runs past the end of a chord is kept).
Ties go to the first vertex, so the result does not depend on the platform.

The idea of the method is the one of BF `web/src/edit/ops.ts::douglasPeucker`; this is a kernel of its own:
an explicit stack instead of recursion, all lines of a layer in one call, NaN heights, bounded worst case.
numba is imported with this module: import it lazily, never before BF-READY.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from bf_core.jit import njit

MAX_RUN = 1024  # segments simplified as one run; every MAX_RUN-th vertex of a line is kept


@njit(cache=True, nogil=True)
def _dist2(xyz, i, a, b, flat):  # type: ignore[no-untyped-def]
    """Squared distance from vertex i to the segment a–b (in plan when `flat`: the heights are empty)."""
    ax = xyz[a, 0]
    ay = xyz[a, 1]
    dx = xyz[b, 0] - ax
    dy = xyz[b, 1] - ay
    px = xyz[i, 0] - ax
    py = xyz[i, 1] - ay
    if flat:
        dz = 0.0
        pz = 0.0
    else:
        az = xyz[a, 2]
        dz = xyz[b, 2] - az
        pz = xyz[i, 2] - az
    len2 = dx * dx + dy * dy + dz * dz
    if len2 > 0.0:
        t = (px * dx + py * dy + pz * dz) / len2
        if t > 1.0:
            t = 1.0
        if t > 0.0:
            px -= t * dx
            py -= t * dy
            pz -= t * dz
    return px * px + py * py + pz * pz


@njit(cache=True, nogil=True)
def _farthest(xyz, lo, hi, flat):  # type: ignore[no-untyped-def]
    """The inner vertex of lo…hi farthest from the segment lo–hi (the first of equals) and its squared distance."""
    best = -1.0
    at = -1
    for i in range(lo + 1, hi):
        d = _dist2(xyz, i, lo, hi, flat)
        if d > best:
            best = d
            at = i
    return at, best


@njit(cache=True, nogil=True)
def _run(xyz, lo, hi, tol2, flat, keep, stack):  # type: ignore[no-untyped-def]
    """Douglas–Peucker between the kept vertices lo and hi (hi − lo ≤ len(stack))."""
    top = 0
    stack[0, 0] = lo
    stack[0, 1] = hi
    while top >= 0:
        s = stack[top, 0]
        e = stack[top, 1]
        top -= 1
        at, best = _farthest(xyz, s, e, flat)
        if best > tol2:
            keep[at] = True
            if at - s >= 2:
                top += 1
                stack[top, 0] = s
                stack[top, 1] = at
            if e - at >= 2:
                top += 1
                stack[top, 0] = at
                stack[top, 1] = e


@njit(cache=True, nogil=True)
def _keep(xyz, offsets, closed, tol, max_run, depth, keep):  # type: ignore[no-untyped-def]
    tol2 = tol * tol
    stack = np.empty((depth + 2, 2), dtype=np.int64)  # a run of m segments never holds more than m chords
    for line in range(len(offsets) - 1):
        a = offsets[line]
        b = offsets[line + 1] - 1
        if b < a:
            continue
        keep[a] = True
        keep[b] = True
        if b - a < 2:
            continue
        whole = True  # every Z of the line is known, or every Z is empty
        for i in range(a + 1, b + 1):
            if math.isnan(xyz[i, 2]) != math.isnan(xyz[i - 1, 2]):
                keep[i - 1] = True
                keep[i] = True
                whole = False
        for i in range(a + max_run, b, max_run):
            keep[i] = True
        if closed[line] != 0 and b - a >= 3:
            flat = (not whole) or math.isnan(xyz[a, 2])
            far = a + 1
            best = -1.0
            for i in range(a + 1, b):
                d = _dist2(xyz, i, a, a, flat)
                if d > best:
                    best = d
                    far = i
            keep[far] = True
            m1, d1 = _farthest(xyz, a, far, flat)
            m2, d2 = _farthest(xyz, far, b, flat)
            if m1 >= 0 and (m2 < 0 or d1 >= d2):
                keep[m1] = True
            elif m2 >= 0:
                keep[m2] = True
        prev = a
        for i in range(a + 1, b + 1):
            if keep[i]:
                if i - prev >= 2:
                    _run(xyz, prev, i, tol2, math.isnan(xyz[prev, 2]), keep, stack)
                prev = i


def _check(xyz: Any, offsets: Any, tol: float, closed: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pts = np.ascontiguousarray(np.asarray(xyz, dtype=np.float64))
    if pts.size == 0:
        pts = pts.reshape(0, 3)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError("simplify: xyz must be N×3")
    if not np.isfinite(pts[:, :2]).all() or np.isinf(pts[:, 2]).any():
        raise ValueError("simplify: X and Y must be finite, Z finite or NaN (empty)")
    off = np.ascontiguousarray(np.asarray(offsets, dtype=np.int64))
    if off.ndim != 1 or len(off) < 1 or off[0] != 0 or off[-1] != len(pts) or np.any(np.diff(off) < 0):
        raise ValueError("simplify: offsets must run from 0 to the number of vertices without going back")
    try:
        ok = math.isfinite(float(tol)) and float(tol) >= 0.0
    except (TypeError, ValueError):
        ok = False
    if not ok:
        raise ValueError("simplify: the tolerance must be a finite number, zero or more")
    lines = len(off) - 1
    flags = np.zeros(lines, dtype=np.uint8) if closed is None else np.ascontiguousarray(closed, dtype=np.uint8)
    if flags.shape != (lines,):
        raise ValueError("simplify: one closed flag per line is expected")
    return pts, off, flags


def keep_mask(xyz: Any, offsets: Any, tol: float, closed: Any = None, *, max_run: int = MAX_RUN) -> np.ndarray:
    """bool per vertex: True — the vertex stays (see the module docstring). `offsets` — lines + 1 entries."""
    pts, off, flags = _check(xyz, offsets, tol, closed)
    if max_run < 2:
        raise ValueError("simplify: max_run must be at least 2")
    keep = np.zeros(len(pts), dtype=np.bool_)
    longest = int(np.diff(off).max()) if len(off) > 1 else 0
    _keep(pts, off, flags, float(tol), int(max_run), min(int(max_run), longest), keep)
    return keep


def compact(offsets: Any, keep: np.ndarray) -> np.ndarray:
    """Offsets of the lines after the vertices with `keep` False are taken out (int64, lines + 1)."""
    off = np.asarray(offsets, dtype=np.int64)
    csum = np.concatenate([np.zeros(1, dtype=np.int64), np.cumsum(keep, dtype=np.int64)])
    return np.asarray(csum[off], dtype=np.int64)


def simplify(xyz: Any, offsets: Any, tol: float, closed: Any = None) -> tuple[np.ndarray, np.ndarray]:
    """(xyz, offsets) of the simplified lines: every line keeps its ends and stays within `tol` of itself."""
    keep = keep_mask(xyz, offsets, tol, closed)
    return np.asarray(xyz, dtype=np.float64).reshape(-1, 3)[keep], compact(offsets, keep)


__all__ = ["MAX_RUN", "compact", "keep_mask", "simplify"]
