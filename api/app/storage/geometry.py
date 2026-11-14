"""Stored geometry of wireframes and line layers: exact arrays and their render copies.

Exact data is float64 and never rounded (docs/ROADMAP.md, 2.3). The render copy is float32
relative to `origin`, an integer point near the centre of the object's extent: an object up
to 16 km across is within 8 km of its origin, where float32 steps are 2^-10 m, so a rendered
vertex is off by at most ~0.5 mm. Calculations (sections, measurements, volumes) use the
float64 arrays, never the render copy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy.orm import Session

from app.storage import blobs

# Largest |coordinate - origin| for which the float32 copy keeps 0.5 mm (half a float32 step at 8 km).
RENDER_SAFE_HALF_EXTENT = 8192.0


def render_origin(vertices: np.ndarray) -> np.ndarray:
    if len(vertices) == 0:
        return np.zeros(3)
    lo, hi = vertices.min(axis=0), vertices.max(axis=0)
    return np.round((lo + hi) / 2.0)


def extent_of(vertices: np.ndarray) -> dict:
    """Exact float64 bounds (no rounding: they are used for fitting and clipping)."""
    if len(vertices) == 0:
        return {"min": [0.0, 0.0, 0.0], "max": [0.0, 0.0, 0.0]}
    return {"min": vertices.min(axis=0).tolist(), "max": vertices.max(axis=0).tolist()}


def _render_copy(vertices: np.ndarray, origin: np.ndarray) -> np.ndarray:
    out = np.empty(vertices.shape, dtype="<f4")
    # In slices, so a 5-million-vertex mesh does not need a second float64 copy in memory.
    step = 1 << 20
    for start in range(0, len(vertices), step):
        out[start:start + step] = vertices[start:start + step] - origin
    return out


@dataclass(frozen=True)
class StoredMesh:
    origin: list[float]
    extent: dict
    blobs: dict[str, str]


def store_mesh(session: Session, vertices: np.ndarray, triangles: np.ndarray) -> StoredMesh:
    v = np.ascontiguousarray(vertices, dtype="<f8").reshape(-1, 3)
    t = np.ascontiguousarray(triangles, dtype="<i4").reshape(-1, 3)
    origin = render_origin(v)
    refs = {
        "vertices": blobs.put_array(session, v, "<f8"),
        "triangles": blobs.put_array(session, t, "<i4"),
        "render_vertices": blobs.put_array(session, _render_copy(v, origin), "<f4"),
    }
    return StoredMesh(origin=origin.tolist(), extent=extent_of(v), blobs=refs)


def mesh_arrays(refs: dict) -> tuple[np.ndarray, np.ndarray]:
    """float64 (n, 3) vertices and int32 (m, 3) triangles, memory-mapped."""
    return blobs.read_array(refs["vertices"], "<f8", 3), blobs.read_array(refs["triangles"], "<i4", 3)


def store_lines(session: Session, vertices: np.ndarray, offsets: np.ndarray, closed: np.ndarray) -> StoredMesh:
    v = np.ascontiguousarray(vertices, dtype="<f8").reshape(-1, 3)
    origin = render_origin(v)
    if len(v) > np.iinfo(np.uint32).max:
        raise ValueError("too many line vertices for 32-bit render offsets")
    refs = {
        "vertices": blobs.put_array(session, v, "<f8"),
        "offsets": blobs.put_array(session, offsets, "<i8"),
        "closed": blobs.put_array(session, closed, "u1"),
        "render_vertices": blobs.put_array(session, _render_copy(v, origin), "<f4"),
        "render_offsets": blobs.put_array(session, offsets, "<u4"),
    }
    return StoredMesh(origin=origin.tolist(), extent=extent_of(v), blobs=refs)


def line_arrays(refs: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (
        blobs.read_array(refs["vertices"], "<f8", 3),
        blobs.read_array(refs["offsets"], "<i8"),
        blobs.read_array(refs["closed"], "u1"),
    )
