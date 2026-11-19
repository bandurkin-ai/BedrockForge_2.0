"""Synthetic solids and surfaces for the tests of `geo/solids.py`, `clip.py`, `crossing.py`, `contours.py`,
`volumes.py` (ported from BF `tests/test_solids.py`): bodies at a typical UTM position, known answers."""

from __future__ import annotations

from typing import Any

import manifold3d
import numpy as np

from bf_core.geo.checks import orientation_conflicts, self_intersections
from bf_core.geo.meshes import mesh_stats
from bf_core.geo.solids import is_closed_solid

# A typical UTM position: every body is built here, not at the origin.
OFFSET = np.array([512345.678, 6789012.345, 250.0])

CUBE_T = np.array(
    [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
     [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]],
    dtype=np.int32,
)  # fmt: skip

L_SHAPE = [[0, 0], [10, 0], [10, 4], [4, 4], [4, 10], [0, 10]]  # area 64, not convex


def box(lo: Any, hi: Any, offset: Any = OFFSET) -> tuple[np.ndarray, np.ndarray]:
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = np.array(
        [
            [x0, y0, z0],
            [x1, y0, z0],
            [x1, y1, z0],
            [x0, y1, z0],
            [x0, y0, z1],
            [x1, y0, z1],
            [x1, y1, z1],
            [x0, y1, z1],
        ],
        dtype=np.float64,
    )
    return v + offset, CUBE_T.copy()


def sphere(radius: float, n_lat: int, n_lon: int, centre: Any = (0.0, 0.0, 0.0), offset: Any = OFFSET) -> Any:
    """UV sphere, outward; symmetric about x = 0, y = 0 (n_lon divisible by 4) and z = 0 (n_lat even)."""
    theta = np.pi * np.arange(1, n_lat) / n_lat
    phi = 2 * np.pi * np.arange(n_lon) / n_lon
    ring = np.stack(
        [
            np.outer(np.sin(theta), np.cos(phi)),
            np.outer(np.sin(theta), np.sin(phi)),
            np.outer(np.cos(theta), np.ones(n_lon)),
        ],
        axis=-1,
    ).reshape(-1, 3)
    v = np.concatenate([[[0.0, 0.0, 1.0]], ring, [[0.0, 0.0, -1.0]]]) * radius + np.asarray(centre) + offset
    j = np.arange(n_lon)
    jn = (j + 1) % n_lon
    top = np.stack([np.zeros(n_lon, dtype=np.int64), 1 + j, 1 + jn], axis=1)
    i = np.arange(n_lat - 2)[:, None]
    a, b = 1 + i * n_lon + j, 1 + i * n_lon + jn
    c, d = a + n_lon, b + n_lon
    quads = np.concatenate([np.stack([a, c, d], axis=-1).reshape(-1, 3), np.stack([a, d, b], axis=-1).reshape(-1, 3)])
    last = 1 + (n_lat - 2) * n_lon
    bottom = np.stack([np.full(n_lon, len(v) - 1), last + jn, last + j], axis=1)
    return v, np.concatenate([top, quads, bottom]).astype(np.int32)


def prism(polygon: Any, z0: float, z1: float, offset: Any = OFFSET) -> tuple[np.ndarray, np.ndarray]:
    """Vertical prism on a counter-clockwise contour (k, 2)."""
    polygon = np.asarray(polygon, dtype=np.float64)
    k = len(polygon)
    tri = np.asarray(manifold3d.triangulate([polygon]), dtype=np.int64)
    i = np.arange(k)
    j = (i + 1) % k
    v = np.concatenate([np.column_stack([polygon, np.full(k, z0)]), np.column_stack([polygon, np.full(k, z1)])])
    t = np.concatenate([tri[:, ::-1], tri + k, np.stack([i, j, j + k], axis=1), np.stack([i, j + k, i + k], axis=1)])
    return v + offset, t.astype(np.int32)


def grid(nx: int, ny: int, x: Any, y: Any, z: Any = lambda x, y: 0.0 * x, offset: Any = OFFSET) -> Any:
    """Open surface z(x, y) over a rectangle, nx × ny cells, normals up."""
    xs, ys = np.linspace(*x, nx + 1), np.linspace(*y, ny + 1)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    v = np.column_stack([gx.ravel(), gy.ravel(), np.asarray(z(gx, gy), dtype=np.float64).ravel()]) + offset
    i = (np.arange(nx)[:, None] * (ny + 1) + np.arange(ny)[None, :]).ravel()
    t = np.concatenate([np.stack([i, i + ny + 1, i + ny + 2], axis=1), np.stack([i, i + ny + 2, i + 1], axis=1)])
    return v, t.astype(np.int32)


def volume(mesh: Any) -> float:
    """Exact float64 volume of a closed mesh (tetrahedra from its mean vertex)."""
    v, t = mesh
    p = v - v.mean(axis=0)
    return float(np.einsum("ij,ij->i", p[t[:, 0]], np.cross(p[t[:, 1]], p[t[:, 2]])).sum()) / 6.0


def area(mesh: Any) -> float:
    v, t = mesh
    return float(np.linalg.norm(np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]]), axis=1).sum()) / 2.0


def assert_good_solid(mesh: Any) -> None:
    """Closed, outward, consistent normals, no self-intersections; float64 vertices and int32 triangles."""
    v, t = mesh
    assert v.dtype == np.float64 and t.dtype == np.int32
    stats = mesh_stats(v, t)
    assert stats["closed"], stats
    assert stats["orientation"] == "outward"
    assert orientation_conflicts(t, len(v)) == 0
    assert self_intersections(v, t).pairs == 0
    assert is_closed_solid(v, t) == (True, "")


def length(points: np.ndarray, closed: bool) -> float:
    p = np.vstack([points, points[:1]]) if closed else points
    return float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum())
