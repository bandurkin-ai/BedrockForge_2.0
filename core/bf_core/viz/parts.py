"""Parts of a drillhole set for 3D (ADR 0007 §1 «Части», ADR 0004 §2).

Holes are ordered by the Morton code of the collar on a 50 m grid (ties by the hole id), so neighbours on the
ground are neighbours in the order, and cut into runs of at most 2000 holes. A run wider than 16 km along any
axis (by the extent of its paths) is halved again, in the same order, until it fits or holds one hole. The
origin of a part is the centre of its extent rounded to a whole metre (float64): the float32 offsets from it
stay below 8000.5 m, a step of 2⁻¹¹ m ≈ 0.49 mm.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CELL_M = 50.0
MAX_HOLES = 2000
MAX_SPAN_M = 16_000.0


@dataclass(frozen=True)
class Part:
    k: int
    start: int  # holes order[start:stop]
    stop: int
    bbox: tuple[float, float, float, float, float, float]  # xmin, ymin, zmin, xmax, ymax, zmax
    origin: tuple[float, float, float]

    @property
    def n(self) -> int:
        return self.stop - self.start


def _spread(v: np.ndarray) -> np.ndarray:
    """Bits of uint32 values moved to the even positions of uint64 (b0 → 0, b1 → 2, …)."""
    x = v.astype(np.uint64)
    x = (x | (x << np.uint64(16))) & np.uint64(0x0000FFFF0000FFFF)
    x = (x | (x << np.uint64(8))) & np.uint64(0x00FF00FF00FF00FF)
    x = (x | (x << np.uint64(4))) & np.uint64(0x0F0F0F0F0F0F0F0F)
    x = (x | (x << np.uint64(2))) & np.uint64(0x3333333333333333)
    x = (x | (x << np.uint64(1))) & np.uint64(0x5555555555555555)
    return x


def morton(x: np.ndarray, y: np.ndarray, cell: float = CELL_M) -> np.ndarray:
    """Morton (Z-order) code of points on a grid of `cell` metres from the lower-left point (uint64)."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if not len(x):
        return np.zeros(0, dtype=np.uint64)
    top = float(np.iinfo(np.uint32).max)
    cx = np.clip(np.floor((x - x.min()) / cell), 0.0, top).astype(np.uint32)
    cy = np.clip(np.floor((y - y.min()) / cell), 0.0, top).astype(np.uint32)
    return _spread(cx) | (_spread(cy) << np.uint64(1))


def order(x: np.ndarray, y: np.ndarray, ids: np.ndarray) -> np.ndarray:
    """Indices of the holes in Morton order of their collars, ties by id."""
    return np.lexsort((np.asarray(ids), morton(x, y)))


def _box(bounds: np.ndarray) -> tuple[float, ...]:
    return (*bounds[:, :3].min(axis=0).tolist(), *bounds[:, 3:].max(axis=0).tolist())


def _origin(box: tuple[float, ...]) -> tuple[float, float, float]:
    c = np.round((np.array(box[:3]) + np.array(box[3:])) * 0.5) + 0.0  # -0.0 → 0.0
    return float(c[0]), float(c[1]), float(c[2])


def split(
    bounds: np.ndarray, *, max_holes: int = MAX_HOLES, max_span: float = MAX_SPAN_M
) -> list[tuple[int, int, tuple[float, ...]]]:
    """Runs (start, stop, bbox) of holes already in order; `bounds` (n, 6) — the extent of every hole."""
    out: list[tuple[int, int, tuple[float, ...]]] = []
    stack = [(a, min(a + max_holes, len(bounds))) for a in range(0, len(bounds), max_holes)][::-1]
    while stack:
        a, b = stack.pop()
        box = _box(bounds[a:b])
        wide = max(box[3] - box[0], box[4] - box[1], box[5] - box[2]) > max_span
        if wide and b - a > 1:
            mid = (a + b) // 2
            stack.extend(((mid, b), (a, mid)))  # the lower half first: the order is kept
            continue
        out.append((a, b, box))
    return out


def make_parts(bounds: np.ndarray, *, max_holes: int = MAX_HOLES, max_span: float = MAX_SPAN_M) -> list[Part]:
    """Parts of holes already in order (see `split`) with their origins."""
    runs = split(np.asarray(bounds, dtype=np.float64), max_holes=max_holes, max_span=max_span)
    return [
        Part(k, a, b, (box[0], box[1], box[2], box[3], box[4], box[5]), _origin(box))
        for k, (a, b, box) in enumerate(runs)
    ]
