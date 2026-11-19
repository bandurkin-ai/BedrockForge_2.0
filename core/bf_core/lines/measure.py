"""Measures of a line layer from its exact float64 arrays (ТЗ 7.22 `lines.measure`; ADR 0015 §1).

Ported from BF `lines/measure.py` (`polygon_area`, `plan_area`, `line_length`, `layer_measures`), vectorised over
the lines of a layer (a layer of a million vertices takes milliseconds, not a Python loop per line).

* Length in 3D and in plan; a segment with an empty Z (NaN) counts in plan in 3D too — its Z is unknown, not 0
  (as `LineSet.lengths`).
* The area of a closed line in its own plane — half the length of the Newell vector (exact for a planar polygon
  however it is tilted) — over the vertices that have a Z; in plan — the shoelace over all its vertices.
* Coordinates are taken relative to the first vertex of every line: UTM-sized numbers do not lose the digits of
  a 10 × 10 square.

A closed line is in the storage form (its first vertex repeated at the end): the closing edge is the last one.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _rel(xyz: np.ndarray, offsets: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vertices relative to the first vertex of their line, and the line of every vertex."""
    counts = np.diff(offsets)
    line = np.repeat(np.arange(len(counts)), counts)
    return xyz - xyz[offsets[:-1]][line], line


def _sum_by_line(values: np.ndarray, line: np.ndarray, lines: int) -> np.ndarray:
    return np.bincount(line, weights=values, minlength=lines) if len(values) else np.zeros(lines)


def line_lengths(xyz: np.ndarray, offsets: np.ndarray, *, plan: bool = False) -> np.ndarray:
    """Length of every line (the storage form of a closed line already has its closing edge)."""
    lines = len(offsets) - 1
    if lines <= 0 or len(xyz) < 2:
        return np.zeros(max(lines, 0))
    d = np.diff(xyz, axis=0)
    dz = np.zeros(len(d)) if plan else np.where(np.isnan(d[:, 2]), 0.0, d[:, 2])
    seg = np.sqrt(d[:, 0] ** 2 + d[:, 1] ** 2 + dz**2)
    inside = np.ones(len(seg), dtype=bool)
    inside[offsets[1:-1] - 1] = False  # the step from the last vertex of a line to the first of the next one
    line = np.repeat(np.arange(lines), np.diff(offsets))[:-1]
    return _sum_by_line(np.where(inside, seg, 0.0), line, lines)


def line_areas(xyz: np.ndarray, offsets: np.ndarray, closed: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(area in its plane, area in plan) of every line; 0 for an open line."""
    lines = len(offsets) - 1
    area = np.zeros(max(lines, 0))
    plan = np.zeros(max(lines, 0))
    if lines <= 0 or not np.any(closed):
        return area, plan
    p, line = _rel(np.asarray(xyz, dtype=np.float64), offsets)
    nxt = np.ones(len(p), dtype=bool)
    nxt[offsets[1:] - 1] = False  # an edge k → k + 1 inside one line
    k = np.flatnonzero(nxt & np.asarray(closed, dtype=bool)[line])
    a, b = p[k], p[k + 1]
    lk = line[k]
    shoe = a[:, 0] * b[:, 1] - b[:, 0] * a[:, 1]
    plan = np.abs(_sum_by_line(shoe, lk, lines)) * 0.5
    # In its plane: only vertices with a Z; a line whose Z are all known is exact, one with gaps uses the rest.
    known = ~np.isnan(p[:, 2])
    if known.all():
        cross = np.cross(a, b)
        newell = np.stack([_sum_by_line(cross[:, i], lk, lines) for i in range(3)], axis=1)
        area = 0.5 * np.sqrt((newell**2).sum(axis=1))
    else:
        for i in np.flatnonzero(closed):
            pts = xyz[int(offsets[i]) : int(offsets[i + 1])]
            area[i] = polygon_area(pts[~np.isnan(pts[:, 2])])
    return np.where(closed, area, 0.0), np.where(closed, plan, 0.0)


def polygon_area(points: np.ndarray) -> float:
    """Area of a closed polygon in its own plane (BF): half the length of the Newell vector."""
    if len(points) < 3:
        return 0.0
    p = np.asarray(points, dtype=np.float64) - points[0]
    return float(0.5 * np.linalg.norm(np.cross(p, np.roll(p, -1, axis=0)).sum(axis=0)))


def plan_area(points: np.ndarray) -> float:
    """Area of the projection of a polygon to the plan (shoelace, BF)."""
    if len(points) < 3:
        return 0.0
    p = np.asarray(points, dtype=np.float64)[:, :2] - points[0, :2]
    q = np.roll(p, -1, axis=0)
    return float(0.5 * abs((p[:, 0] * q[:, 1] - q[:, 0] * p[:, 1]).sum()))


def layer_measures(xyz: np.ndarray, offsets: np.ndarray | None, closed: np.ndarray | None) -> dict[str, Any]:
    """Totals of a layer, unrounded: they are measurements. A layer of points (`offsets` None) has no lengths."""
    xyz = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
    if offsets is None or closed is None:
        return {
            "lines": 0, "vertices": int(len(xyz)), "points": int(len(xyz)), "closed": 0, "length": 0.0,
            "plan_length": 0.0, "area": 0.0, "plan_area": 0.0,
        }  # fmt: skip
    offsets = np.asarray(offsets, dtype=np.int64)
    closed = np.asarray(closed, dtype=np.uint8)
    area, plan = line_areas(xyz, offsets, closed)
    return {
        "lines": int(len(offsets) - 1),
        "vertices": int(len(xyz)),
        "points": int(np.count_nonzero(np.diff(offsets) == 1)),
        "closed": int(np.count_nonzero(closed)),
        "length": math.fsum(line_lengths(xyz, offsets).tolist()),
        "plan_length": math.fsum(line_lengths(xyz, offsets, plan=True).tolist()),
        "area": math.fsum(area.tolist()),
        "plan_area": math.fsum(plan.tolist()),
    }
