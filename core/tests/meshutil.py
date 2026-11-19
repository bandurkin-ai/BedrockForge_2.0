"""Synthetic meshes for the tests of the exact queries on wireframes (`geo/section.py`, `geo/pick.py`,
`geo/intersect.py`): on UTM-sized coordinates, in any triangle order, and as the store keeps them."""

from __future__ import annotations

from typing import Any

import numpy as np

from bf_core.geo import meshes as gm

UTM = np.array([512_345.678_901, 6_234_567.123_456, 412.345_678])


def grid(nx: int, ny: int, f: Any, step: float = 1.0, offset: Any = UTM, seed: int | None = 0) -> Any:
    """A surface z = f(x, y) on nx × ny cells (2 triangles each, normals up); `seed` shuffles the triangles."""
    x, y = np.meshgrid(np.arange(nx + 1) * step, np.arange(ny + 1) * step)
    v = np.column_stack([x.ravel(), y.ravel(), f(x.ravel(), y.ravel())]) + np.asarray(offset, dtype=float)
    i = (np.arange(ny)[:, None] * (nx + 1) + np.arange(nx)[None, :]).ravel()
    t = np.concatenate([np.column_stack([i, i + 1, i + nx + 2]), np.column_stack([i, i + nx + 2, i + nx + 1])])
    if seed is not None:
        t = t[np.random.default_rng(seed).permutation(len(t))]
    return v, t.astype(np.int32)


def cube(n: int, side: float, offset: Any = UTM, seed: int | None = 0) -> Any:
    """A closed cube of `side` with n × n cells a face, vertices welded, normals outward; its low corner is
    `offset` and its vertices are `offset + k · side / n` exactly as numpy rounds them."""
    faces_v, faces_t = [], []
    a, b = np.meshgrid(np.arange(n + 1), np.arange(n + 1))
    a, b = a.ravel(), b.ravel()
    i = (np.arange(n)[:, None] * (n + 1) + np.arange(n)[None, :]).ravel()
    quad = np.concatenate([np.column_stack([i, i + 1, i + n + 2]), np.column_stack([i, i + n + 2, i + n + 1])])
    zero, full = np.zeros_like(a), np.full_like(a, n)
    for axis, value, flip in ((2, zero, True), (2, full, False), (0, zero, True), (0, full, False),
                              (1, zero, False), (1, full, True)):  # fmt: skip
        cols = {0: (value, a, b), 1: (a, value, b), 2: (a, b, value)}[axis]
        faces_t.append((quad[:, ::-1] if flip else quad) + sum(len(p) for p in faces_v))
        faces_v.append(np.column_stack(cols))
    cells = np.concatenate(faces_v)  # whole numbers of cells: welding is exact
    t = np.concatenate(faces_t)
    uniq, inverse = np.unique(cells, axis=0, return_inverse=True)
    t = inverse.reshape(-1)[t]
    if seed is not None:
        t = t[np.random.default_rng(seed).permutation(len(t))]
    return uniq * (side / n) + np.asarray(offset, dtype=float), t.astype(np.int32)


def icosphere(level: int, radius: float, centre: Any = UTM) -> Any:
    """A closed sphere of 20 · 4^level triangles, normals outward (vertices on the sphere to rounding)."""
    g = (1.0 + 5.0**0.5) / 2.0
    v = [(-1, g, 0), (1, g, 0), (-1, -g, 0), (1, -g, 0), (0, -1, g), (0, 1, g), (0, -1, -g), (0, 1, -g),
         (g, 0, -1), (g, 0, 1), (-g, 0, -1), (-g, 0, 1)]  # fmt: skip
    t = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6),
         (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10),
         (8, 6, 7), (9, 8, 1)]  # fmt: skip
    points = [np.array(p, dtype=float) / np.linalg.norm(p) for p in v]
    tris = [tuple(x) for x in t]
    for _ in range(level):
        mid: dict[tuple[int, int], int] = {}

        def middle(i: int, j: int) -> int:
            key = (min(i, j), max(i, j))
            if key not in mid:  # noqa: B023 - the dictionary of this level
                m = points[i] + points[j]
                points.append(m / np.linalg.norm(m))
                mid[key] = len(points) - 1  # noqa: B023
            return mid[key]  # noqa: B023

        nxt = []
        for a, b, c in tris:
            ab, bc, ca = middle(a, b), middle(b, c), middle(c, a)
            nxt += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        tris = nxt
    return np.array(points) * radius + np.asarray(centre, dtype=float), np.array(tris, dtype=np.int32)


def stored(v: Any, t: Any, part_triangles: int = gm.PART_TRIANGLES) -> Any:
    """(xyz, tri, parts) as `wireframes/store.py` keeps a wireframe: the triangles along the Z-order curve and
    the rows of `wireframes.render["parts"]` — [first, count, xmin, ymin, zmin, xmax, ymax, zmax]."""
    v, t = gm.check_mesh(v, t)
    t = np.ascontiguousarray(t[gm.morton_order(v, t)])
    parts = [[p.first, p.count, *p.bbox] for p in gm.parts_table(v, t, max_triangles=part_triangles)]
    return v, t, parts


def rows(a: Any) -> list[tuple[float, ...]]:
    return sorted(map(tuple, np.asarray(a).tolist()))
