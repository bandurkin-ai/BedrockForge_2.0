"""Where drillholes cross wireframes and enter or leave block models (the marks on the hole traces).

Holes are polylines along the minimum-curvature trajectory (a point every `PATH_STEP` metres and at
every survey station). Wireframes: every crossing of a segment with a triangle (Möller–Trumbore on
the exact float64 vertices), triangles first culled by the bounding box of the hole. Block models:
the depths where the hole goes from outside every block to inside one and back, found on a regular
step along the hole and refined by bisection to a millimetre.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numba import njit

from app.geo.trajectory import Trajectory, sample_path

PATH_STEP = 2.0
# Two hits of one wireframe closer than this along the hole are one crossing (an edge shared by two triangles).
SAME_HIT_M = 0.01
MAX_SAMPLES = 5_000_000


@dataclass
class Hit:
    depth: float
    xyz: tuple[float, float, float]
    # "cross" for a wireframe, "in" / "out" for a block model
    event: str


@njit(cache=True)
def _segment_triangle_hits(seg, lo_s, hi_s, tri, lo_t, hi_t, eps):
    """(segment index, parameter along it 0..1) of every crossing of a segment with a triangle.
    seg (S, 6): a and b; tri (C, 9): three corners; lo_*/hi_*: bounding boxes."""
    out_s = []
    out_t = []
    for s in range(seg.shape[0]):
        ax, ay, az = seg[s, 0], seg[s, 1], seg[s, 2]
        dx, dy, dz = seg[s, 3] - ax, seg[s, 4] - ay, seg[s, 5] - az
        for i in range(tri.shape[0]):
            if lo_t[i, 0] > hi_s[s, 0] + eps or hi_t[i, 0] < lo_s[s, 0] - eps:
                continue
            if lo_t[i, 1] > hi_s[s, 1] + eps or hi_t[i, 1] < lo_s[s, 1] - eps:
                continue
            if lo_t[i, 2] > hi_s[s, 2] + eps or hi_t[i, 2] < lo_s[s, 2] - eps:
                continue
            a0, a1, a2 = tri[i, 0], tri[i, 1], tri[i, 2]
            e1x, e1y, e1z = tri[i, 3] - a0, tri[i, 4] - a1, tri[i, 5] - a2
            e2x, e2y, e2z = tri[i, 6] - a0, tri[i, 7] - a1, tri[i, 8] - a2
            px = dy * e2z - dz * e2y
            py = dz * e2x - dx * e2z
            pz = dx * e2y - dy * e2x
            det = e1x * px + e1y * py + e1z * pz
            if abs(det) < 1e-14:
                continue
            inv = 1.0 / det
            sx, sy, sz = ax - a0, ay - a1, az - a2
            u = (sx * px + sy * py + sz * pz) * inv
            if u < -1e-9 or u > 1.0 + 1e-9:
                continue
            qx = sy * e1z - sz * e1y
            qy = sz * e1x - sx * e1z
            qz = sx * e1y - sy * e1x
            w = (dx * qx + dy * qy + dz * qz) * inv
            if w < -1e-9 or u + w > 1.0 + 1e-9:
                continue
            t = (e2x * qx + e2y * qy + e2z * qz) * inv
            if t < -1e-9 or t > 1.0 + 1e-9:
                continue
            out_s.append(s)
            out_t.append(min(1.0, max(0.0, t)))
    return out_s, out_t


@njit(cache=True)
def _inside_blocks(points, origin, parent, counts, keys, order, lo, hi):
    """1 where a point is inside a block. Blocks are found through the parent cell of their centre
    (`keys` sorted, `order` the block of each key)."""
    n = points.shape[0]
    out = np.zeros(n, np.uint8)
    for k in range(n):
        c0 = math.floor((points[k, 0] - origin[0]) / parent[0])
        c1 = math.floor((points[k, 1] - origin[1]) / parent[1])
        c2 = math.floor((points[k, 2] - origin[2]) / parent[2])
        if c0 < 0 or c1 < 0 or c2 < 0 or c0 >= counts[0] or c1 >= counts[1] or c2 >= counts[2]:
            continue
        key = (c0 * counts[1] + c1) * counts[2] + c2
        j = np.searchsorted(keys, key)
        while j < keys.shape[0] and keys[j] == key:
            b = order[j]
            if (lo[b, 0] <= points[k, 0] <= hi[b, 0] and lo[b, 1] <= points[k, 1] <= hi[b, 1]
                    and lo[b, 2] <= points[k, 2] <= hi[b, 2]):
                out[k] = 1
                break
            j += 1
    return out


def hole_segments(traj: Trajectory) -> tuple[np.ndarray, np.ndarray]:
    """Points (n, 3) and measured depths (n,) of the hole as a polyline."""
    path = sample_path(traj, PATH_STEP)
    return path[:, :3].astype(np.float64), path[:, 3].astype(np.float64)


class MeshTarget:
    """A wireframe ready for many holes: corners and bounding boxes of its triangles."""

    def __init__(self, vertices: np.ndarray, triangles: np.ndarray):
        v = np.asarray(vertices, np.float64)
        t = np.asarray(triangles, np.int64)
        self.corners = np.ascontiguousarray(v[t].reshape(-1, 9)) if len(t) else np.zeros((0, 9))
        c = self.corners.reshape(-1, 3, 3)
        self.lo = c.min(axis=1) if len(c) else np.zeros((0, 3))
        self.hi = c.max(axis=1) if len(c) else np.zeros((0, 3))
        self.box = (self.lo.min(axis=0), self.hi.max(axis=0)) if len(c) else None

    def hits(self, points: np.ndarray, md: np.ndarray) -> list[Hit]:
        if self.box is None or len(points) < 2:
            return []
        plo, phi = points.min(axis=0), points.max(axis=0)
        if (plo > self.box[1] + 1e-6).any() or (phi < self.box[0] - 1e-6).any():
            return []
        near = ((self.lo <= phi + 1e-6) & (self.hi >= plo - 1e-6)).all(axis=1)
        if not near.any():
            return []
        seg = np.ascontiguousarray(np.hstack([points[:-1], points[1:]]))
        lo_s = np.minimum(points[:-1], points[1:])
        hi_s = np.maximum(points[:-1], points[1:])
        idx, ts = _segment_triangle_hits(seg, lo_s, hi_s, self.corners[near], self.lo[near], self.hi[near], 1e-6)
        out: list[Hit] = []
        for s, t in sorted(zip(idx, ts), key=lambda st: md[st[0]] + st[1] * (md[st[0] + 1] - md[st[0]])):
            depth = float(md[s] + t * (md[s + 1] - md[s]))
            if out and depth - out[-1].depth < SAME_HIT_M:
                continue
            p = points[s] + t * (points[s + 1] - points[s])
            out.append(Hit(depth, (float(p[0]), float(p[1]), float(p[2])), "cross"))
        return out


class BlockTarget:
    """A block model ready for many holes: blocks sorted by the parent cell of their centre."""

    def __init__(self, centres: np.ndarray, sizes: np.ndarray, origin, parent, counts):
        c = np.asarray(centres, np.float64)
        s = np.asarray(sizes, np.float64)
        self.origin = np.asarray(origin, np.float64)
        self.parent = np.maximum(np.asarray(parent, np.float64), 1e-9)
        self.counts = np.maximum(np.asarray(counts, np.int64), 1)
        self.lo = np.ascontiguousarray(c - s / 2)
        self.hi = np.ascontiguousarray(c + s / 2)
        cell = np.floor((c - self.origin) / self.parent).astype(np.int64)
        cell = np.clip(cell, 0, self.counts - 1)
        keys = (cell[:, 0] * self.counts[1] + cell[:, 1]) * self.counts[2] + cell[:, 2]
        self.order = np.argsort(keys, kind="stable").astype(np.int64)
        self.keys = np.ascontiguousarray(keys[self.order])
        self.box = (self.lo.min(axis=0), self.hi.max(axis=0)) if len(c) else None
        # Step along the hole: half the smallest block (never finer than 25 cm).
        self.step = max(0.25, float(s.min()) / 2) if len(s) else 1.0

    def inside(self, points: np.ndarray) -> np.ndarray:
        return _inside_blocks(np.ascontiguousarray(points, np.float64), self.origin, self.parent, self.counts,
                              self.keys, self.order, self.lo, self.hi)

    def hits(self, traj: Trajectory, budget: list[int]) -> list[Hit]:
        if self.box is None or traj.length <= 0:
            return []
        points, md = hole_segments(traj)
        if (points.min(axis=0) > self.box[1]).any() or (points.max(axis=0) < self.box[0]).any():
            return []
        count = int(math.ceil(traj.length / self.step)) + 1
        if count > budget[0]:
            budget[1] += 1
            return []
        budget[0] -= count
        depths = np.linspace(0.0, traj.length, count)
        state = self.inside(traj.positions(depths))
        change = np.nonzero(state[1:] != state[:-1])[0]
        if not len(change):
            return []
        a, b = depths[change].copy(), depths[change + 1].copy()
        first = state[change]
        for _ in range(40):
            if (b - a).max() < 1e-3:
                break
            mid = (a + b) / 2
            m = self.inside(traj.positions(mid))
            same = m == first
            a = np.where(same, mid, a)
            b = np.where(same, b, mid)
        depth = (a + b) / 2
        xyz = traj.positions(depth)
        return [Hit(float(d), (float(p[0]), float(p[1]), float(p[2])), "out" if f else "in") for d, p, f in zip(depth, xyz, first)]


def new_budget() -> list[int]:
    """[samples left, holes skipped] for the block models of one request (a guard against tiny
    blocks times many long holes)."""
    return [MAX_SAMPLES, 0]
