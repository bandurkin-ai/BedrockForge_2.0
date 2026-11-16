"""Measures of a line layer from its exact float64 arrays: length, closed lines, polygon areas."""

from __future__ import annotations

import math

import numpy as np


def polygon_area(points: np.ndarray) -> float:
    """Area of a closed polygon in its own plane: half the length of the Newell vector
    (exact for a planar polygon however it is tilted)."""
    if len(points) < 3:
        return 0.0
    p = points - points.mean(axis=0)
    return float(0.5 * np.linalg.norm(np.cross(p, np.roll(p, -1, axis=0)).sum(axis=0)))


def plan_area(points: np.ndarray) -> float:
    """Area of the polygon's projection to the plan (shoelace)."""
    if len(points) < 3:
        return 0.0
    p = points[:, :2] - points[:, :2].mean(axis=0)
    q = np.roll(p, -1, axis=0)
    return float(0.5 * abs((p[:, 0] * q[:, 1] - q[:, 0] * p[:, 1]).sum()))


def line_length(points: np.ndarray, closed: bool, plan: bool = False) -> float:
    if len(points) < 2:
        return 0.0
    p = points[:, :2] if plan else points
    d = np.diff(p, axis=0)
    total = float(np.sqrt((d * d).sum(axis=1)).sum())
    if closed:
        total += float(np.linalg.norm(p[0] - p[-1]))
    return total


def layer_measures(vertices: np.ndarray, offsets: np.ndarray, closed: np.ndarray) -> dict:
    """Totals of a layer. `length` and the areas are kept unrounded: they are measurements."""
    lengths, plans, areas, plan_areas = [], [], [], []
    points = 0
    flat = len(offsets) > 1
    for i in range(len(offsets) - 1):
        p = np.asarray(vertices[offsets[i]:offsets[i + 1]], dtype=np.float64)
        is_closed = bool(closed[i])
        if len(p) == 1:
            points += 1
        lengths.append(line_length(p, is_closed))
        plans.append(line_length(p, is_closed, plan=True))
        if is_closed:
            areas.append(polygon_area(p))
            plan_areas.append(plan_area(p))
        if len(p) and np.ptp(p[:, 2]) != 0:
            flat = False
    return {
        "length": math.fsum(lengths),
        "plan_length": math.fsum(plans),
        "closed": int(np.count_nonzero(closed)),
        "points": points,
        "flat": flat,
        "area": math.fsum(areas),
        "plan_area": math.fsum(plan_areas),
    }
