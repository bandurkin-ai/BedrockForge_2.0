"""Swath plot: means of block models and samples in slices across a direction.

A swath is a slab of a given width across the direction: X, Y, Z or a horizontal azimuth.
A block belongs to the swath its centre is in; a sample, to the swath of the middle of its
interval. The mean of a swath is Σ(value × weight) / Σ(weight) over what is in it: blocks are
weighted by volume or tonnage, samples by length, by a field, or not at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

MAX_SWATHS = 2000
# The default width: whole parent blocks, as few as give no more swaths than this.
DEFAULT_SWATHS = 50


@dataclass(frozen=True)
class Frame:
    """The axes of the swaths: `u` across them (the axis of the plot), `v` and `w` along them."""

    direction: str
    azimuth: float
    u: np.ndarray
    v: np.ndarray
    w: np.ndarray

    @property
    def axes(self) -> np.ndarray:
        return np.array([self.u, self.v, self.w])


def frame(direction: str, azimuth: float = 0.0) -> Frame:
    """x, y, z: the coordinate axes; azimuth: a horizontal direction, degrees clockwise from north."""
    if direction == "x":
        return Frame("x", 90.0, np.array([1.0, 0, 0]), np.array([0.0, 1, 0]), np.array([0.0, 0, 1]))
    if direction == "y":
        return Frame("y", 0.0, np.array([0.0, 1, 0]), np.array([1.0, 0, 0]), np.array([0.0, 0, 1]))
    if direction == "z":
        return Frame("z", 0.0, np.array([0.0, 0, 1]), np.array([1.0, 0, 0]), np.array([0.0, 1, 0]))
    if direction != "azimuth":
        raise ValueError(f"Неизвестное направление «{direction}»")
    a = math.radians(azimuth % 360.0)
    s, c = math.sin(a), math.cos(a)
    return Frame("azimuth", azimuth % 360.0, np.array([s, c, 0.0]), np.array([c, -s, 0.0]), np.array([0.0, 0, 1]))


def bounds(f: Frame, centres: np.ndarray, sizes: np.ndarray | None = None) -> np.ndarray:
    """Extent of points (or of boxes with these sizes) along u, v, w: rows (min, max)."""
    out = np.empty((3, 2))
    for i, axis in enumerate(f.axes):
        p = centres @ axis
        half = 0.5 * (sizes @ np.abs(axis)) if sizes is not None else 0.0
        out[i] = (p - half).min(), (p + half).max()
    return out


def nice(value: float) -> float:
    """1, 2, 2.5 or 5 times a power of ten, not below the value."""
    if value <= 0 or not math.isfinite(value):
        return 1.0
    power = 10.0 ** math.floor(math.log10(value))
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if m * power >= value * (1 - 1e-9):
            return m * power
    return 10.0 * power


def default_width(span: float, block: float | None) -> float:
    """Width when none is given: whole blocks (the fewest that keep the plot readable), or a round
    number near 1/25 of the extent when there is no block model."""
    if span <= 0:
        return block or 1.0
    if block and block > 0:
        return block * max(1, math.ceil(span / block / DEFAULT_SWATHS - 1e-9))
    return nice(span / 25.0)


def layout(lo: float, hi: float, width: float, anchor: float | None = None) -> tuple[float, int]:
    """Start of the first swath and the number of swaths that cover lo..hi. A boundary passes
    through `anchor`; without it the first swath starts at lo."""
    if width <= 0:
        raise ValueError("Ширина полосы должна быть больше нуля")
    tol = 1e-9 * max(1.0, abs(lo), abs(hi), width)
    start = lo if anchor is None else anchor + math.floor((lo - anchor + tol) / width) * width
    n = max(1, math.ceil((hi - start - tol) / width))
    if n > MAX_SWATHS:
        raise ValueError(f"Слишком много полос ({n}, максимум {MAX_SWATHS}): увеличьте ширину")
    return start, n


def swath_index(t: np.ndarray, start: float, width: float, n: int) -> np.ndarray:
    """Swath number of every coordinate, -1 outside. The far boundary of the last swath is in it."""
    tol = 1e-9 * max(1.0, abs(start), width)
    idx = np.floor((t - start) / width).astype(np.int64)
    idx[(idx == n) & (t <= start + n * width + tol)] = n - 1
    idx[(idx == -1) & (t >= start - tol)] = 0
    idx[(idx < 0) | (idx >= n) | ~np.isfinite(t)] = -1
    return idx


def swath_means(t: np.ndarray, values: np.ndarray, weights: np.ndarray, start: float, width: float, n: int) -> dict:
    """Weighted mean, count and weight in every swath (mean NaN where a swath is empty), the same
    over all swaths, and how many values fell outside them."""
    idx = swath_index(t, start, width, n)
    ok = (idx >= 0) & np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    i, v, w = idx[ok], values[ok], weights[ok]
    weight = np.bincount(i, weights=w, minlength=n)
    total = np.bincount(i, weights=v * w, minlength=n)
    count = np.bincount(i, minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(weight > 0, total / weight, np.nan)
    wsum = float(w.sum())
    return {
        "mean": mean, "count": count, "weight": weight,
        "total": {"mean": float((v * w).sum() / wsum) if wsum > 0 else None, "count": int(len(v)), "weight": wsum},
        "outside": int((idx < 0).sum()),
    }


def points_in_blocks(points: np.ndarray, centres: np.ndarray, sizes: np.ndarray, origin, parent) -> np.ndarray:
    """True for every point inside a block. Blocks are looked up by the cell of the parent grid
    (corner `origin`, cell `parent`) their centre is in: sub-blocks lie inside their parent."""
    inside = np.zeros(len(points), dtype=bool)
    if not len(points) or not len(centres):
        return inside
    origin = np.asarray(origin, dtype=np.float64)
    parent = np.maximum(np.asarray(parent, dtype=np.float64), 1e-9)
    cb = np.floor((centres - origin) / parent).astype(np.int64)
    cp = np.floor((points - origin) / parent).astype(np.int64)
    lo = np.minimum(cb.min(axis=0), cp.min(axis=0))
    cb -= lo
    cp -= lo
    span = np.maximum(cb.max(axis=0), cp.max(axis=0)) + 1
    key_b = (cb[:, 0] * span[1] + cb[:, 1]) * span[2] + cb[:, 2]
    key_p = (cp[:, 0] * span[1] + cp[:, 1]) * span[2] + cp[:, 2]
    order = np.argsort(key_b, kind="stable")
    key_b = key_b[order]
    first = np.searchsorted(key_b, key_p, side="left")
    last = np.searchsorted(key_b, key_p, side="right")
    half = 0.5 * sizes
    tol = 1e-6
    todo = np.flatnonzero(last > first)
    k = 0
    # Blocks of a cell one by one: as many passes as the most finely split parent has sub-blocks.
    while len(todo):
        b = order[first[todo] + k]
        hit = (np.abs(points[todo] - centres[b]) <= half[b] + tol).all(axis=1)
        inside[todo[hit]] = True
        k += 1
        todo = todo[~hit]
        todo = todo[first[todo] + k < last[todo]]
    return inside
