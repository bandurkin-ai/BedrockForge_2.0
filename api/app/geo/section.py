"""Exact section of a wireframe by a plane, in float64 (docs/ROADMAP.md, 10.5).

Only the parts of the triangle array (stored in spatial order, see geo/meshes.py) whose bounding
box the plane touches are read. Every crossed edge gets one point, computed from its two ends in
a fixed order, so neighbouring triangles share it exactly and the segments chain into polylines;
a vertex lying on the plane counts as being on its positive side (consistent symbolic
perturbation: no double points, no gaps).
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np


def _chunks_hit(chunks: list[list[float]], point: np.ndarray, normal: np.ndarray) -> list[tuple[int, int]]:
    if not chunks:
        return []
    table = np.asarray(chunks, dtype=np.float64)
    lo, hi = table[:, 2:5], table[:, 5:8]
    centre, half = (lo + hi) / 2, (hi - lo) / 2
    dist = (centre - point) @ normal
    reach = np.abs(half) @ np.abs(normal)
    hit = np.abs(dist) <= reach + 1e-9
    return [(int(a), int(n)) for a, n in table[hit, :2]]


def plane_section(vertices: np.ndarray, triangles: np.ndarray, origin, point, normal,
                  chunks: list[list[float]] | None = None) -> list[dict]:
    """Polylines where the plane (point, normal; project coordinates) cuts the mesh.

    Returns [{"closed": bool, "points": [[x, y, z], ...]}] in coordinates relative to `origin`
    (float64). `chunks` (from the stored display structure) limits the triangles read."""
    origin = np.asarray(origin, dtype=np.float64)
    n = np.asarray(normal, dtype=np.float64)
    norm = np.linalg.norm(n)
    if norm == 0:
        raise ValueError("normal is zero")
    n = n / norm
    p = np.asarray(point, dtype=np.float64) - origin
    if chunks:
        ranges = _chunks_hit(chunks, p, n)
        if not ranges:
            return []
        tri = np.concatenate([np.asarray(triangles[a:a + c]) for a, c in ranges]).astype(np.int64)
    else:
        tri = np.asarray(triangles, dtype=np.int64)
    if len(tri) == 0:
        return []
    # Distances of all vertices (one pass over the float64 array; cheaper than collecting the
    # vertices of the selected triangles), then only the crossing triangles go further.
    d = np.empty(len(vertices), dtype=np.float64)
    step = 1 << 21
    for start in range(0, len(vertices), step):
        d[start:start + step] = (np.asarray(vertices[start:start + step], dtype=np.float64) - origin - p) @ n
    side = d >= 0
    s = side[tri]
    crossing = s.any(axis=1) & ~s.all(axis=1)
    if not crossing.any():
        return []
    tri_c = tri[crossing]
    # The two crossed edges of every crossing triangle, as (low, high) vertex ids.
    edges = []
    for i, j in ((0, 1), (1, 2), (2, 0)):
        a, b = tri_c[:, i], tri_c[:, j]
        edges.append((np.minimum(a, b), np.maximum(a, b), side[a] != side[b]))
    first_lo = np.where(edges[0][2], edges[0][0], edges[1][0])
    first_hi = np.where(edges[0][2], edges[0][1], edges[1][1])
    second_lo = np.where(edges[2][2], edges[2][0], edges[1][0])
    second_hi = np.where(edges[2][2], edges[2][1], edges[1][1])
    m = len(vertices)
    k1, k2 = first_lo * m + first_hi, second_lo * m + second_hi
    keys, inverse = np.unique(np.concatenate([k1, k2]), return_inverse=True)
    a, b = keys // m, keys % m
    pa = np.asarray(vertices[a], dtype=np.float64) - origin
    pb = np.asarray(vertices[b], dtype=np.float64) - origin
    t = d[a] / (d[a] - d[b])
    nodes = pa + (pb - pa) * t[:, None]
    links = inverse.reshape(2, -1).T
    return _chain(nodes, links)


def _chain(nodes: np.ndarray, links: np.ndarray) -> list[dict]:
    """Joins segments (pairs of node ids) into polylines; a node of degree 2 continues a line."""
    adjacency: dict[int, list[int]] = defaultdict(list)
    for seg, (u, v) in enumerate(links.tolist()):
        adjacency[u].append(seg)
        adjacency[v].append(seg)
    used = np.zeros(len(links), dtype=bool)
    out = []
    # Open lines first (they start at nodes of degree 1), then the remaining loops.
    starts = [node for node, segs in adjacency.items() if len(segs) == 1] + list(adjacency)
    for start in starts:
        if all(used[s] for s in adjacency[start]):
            continue
        line = [start]
        node = start
        while True:
            nxt = next((s for s in adjacency[node] if not used[s]), None)
            if nxt is None:
                break
            used[nxt] = True
            u, v = links[nxt]
            node = int(v if u == node else u)
            line.append(node)
            if node == start:
                break
        closed = len(line) > 2 and line[0] == line[-1]
        coords = nodes[line[:-1] if closed else line]
        out.append({"closed": bool(closed), "points": np.round(coords, 6).tolist()})
    return out
