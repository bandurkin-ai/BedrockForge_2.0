"""Synthetic data of known size and exact answers, for benchmarks and accuracy tests.

    subdivided_cube(n)   closed solid, 12·n² triangles, exact volume L³ and area 6·L²
    wavy_surface(n)      open terrain surface, 2·n² triangles
    write_tridb(path)    a Micromine TRIDB file (layout of docs/FORMATS.md)
    block_model(n)       centres, sizes and grades of n regular blocks (stage 12)
"""

from __future__ import annotations

import sqlite3
import struct
import uuid
from pathlib import Path

import numpy as np

UTM = np.array([512000.0, 4512000.0, 0.0])


def _grid_faces(n: int) -> np.ndarray:
    """Two triangles per cell of an (n+1)×(n+1) vertex grid, counter-clockwise seen from +normal."""
    i, j = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    a = (i * (n + 1) + j).ravel()
    b, c, d = a + 1, a + n + 1, a + n + 2
    return np.concatenate([np.column_stack([a, c, d]), np.column_stack([a, d, b])]).astype(np.int64)


def subdivided_cube(n: int, size: float = 1000.0, origin: np.ndarray = UTM) -> tuple[np.ndarray, np.ndarray]:
    """A cube with every face split into n×n squares; vertices are shared along edges (welded)."""
    u = np.linspace(0.0, size, n + 1)
    gu, gv = np.meshgrid(u, u, indexing="ij")
    gu, gv = gu.ravel(), gv.ravel()
    zero, full = np.zeros_like(gu), np.full_like(gu, size)
    faces = _grid_faces(n)
    # (x, y, z) of every face grid and whether its winding must be flipped for outward normals.
    planes = [
        (np.column_stack([gu, gv, zero]), True),   # bottom, normal -z
        (np.column_stack([gu, gv, full]), False),  # top
        (np.column_stack([gu, zero, gv]), False),  # y = 0, normal -y
        (np.column_stack([gu, full, gv]), True),
        (np.column_stack([zero, gu, gv]), True),   # x = 0, normal -x
        (np.column_stack([full, gu, gv]), False),
    ]
    verts, tris = [], []
    base = 0
    for pts, flip in planes:
        verts.append(pts)
        f = faces[:, ::-1] if flip else faces
        tris.append(f + base)
        base += len(pts)
    v = np.concatenate(verts)
    t = np.concatenate(tris)
    # Weld the shared edges exactly: grid coordinates are exact multiples, so rounding is safe.
    key = np.round(v / size * n).astype(np.int64)
    packed = (key[:, 0] * (n + 1) + key[:, 1]) * (n + 1) + key[:, 2]
    uniq, first, inverse = np.unique(packed, return_index=True, return_inverse=True)
    return v[first] + origin, inverse[t].astype(np.int32)


def wavy_surface(n: int, size: float = 4000.0, origin: np.ndarray = UTM) -> tuple[np.ndarray, np.ndarray]:
    u = np.linspace(0.0, size, n + 1)
    gx, gy = np.meshgrid(u, u, indexing="ij")
    z = 50.0 * np.sin(gx / 300.0) * np.cos(gy / 410.0) + 400.0
    v = np.column_stack([gx.ravel(), gy.ravel(), z.ravel()]) + origin
    return v, _grid_faces(n).astype(np.int32)


def tridb_geometry(vertices: np.ndarray, triangles: np.ndarray) -> bytes:
    guid = str(uuid.uuid4()).encode()
    parts = [b"\xff\xff\xff\xff\x00\x00\x05\x00", struct.pack("<qq", len(guid), 9), guid]
    for number, data in enumerate(
        [np.ascontiguousarray(vertices[:, k], dtype="<f8").tobytes() for k in range(3)]
        + [np.ascontiguousarray(triangles, dtype="<i4").tobytes()],
        start=1,
    ):
        parts += [struct.pack("<qq", len(data), number), data]
    return b"".join(parts)


def write_tridb(path: Path, meshes: list[tuple[str, np.ndarray, np.ndarray]]) -> None:
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA encoding = "UTF-16le"')
    conn.execute("CREATE TABLE GeneralInformation (ID INTEGER PRIMARY KEY, Name TEXT, Colour INTEGER, Volume REAL, SurfaceArea REAL)")
    conn.execute("CREATE TABLE Geometry (ID INTEGER PRIMARY KEY, Geometry BLOB)")
    conn.execute("CREATE TABLE GeometryProperties (ID INTEGER PRIMARY KEY, GUID TEXT, PointCount INTEGER, TriangleCount INTEGER)")
    for i, (name, v, t) in enumerate(meshes, start=1):
        conn.execute("INSERT INTO GeneralInformation VALUES (?, ?, ?, NULL, NULL)", (i, name, 0x0080FF))
        conn.execute("INSERT INTO Geometry VALUES (?, ?)", (i, tridb_geometry(v, t)))
        conn.execute("INSERT INTO GeometryProperties VALUES (?, ?, ?, ?)", (i, "g", len(v), len(t)))
    conn.commit()
    conn.close()


def cube_n_for(triangles: int) -> int:
    """Subdivision giving at least `triangles` triangles (12·n²)."""
    return int(np.ceil(np.sqrt(triangles / 12.0)))


def block_model(n_blocks: int, block: float = 5.0, seed: int = 0) -> dict[str, np.ndarray]:
    """Regular blocks on a cube-ish grid with a log-normal grade; for stage 12 readers and queries."""
    side = int(np.ceil(n_blocks ** (1 / 3)))
    idx = np.arange(n_blocks)
    i, j, k = idx % side, (idx // side) % side, idx // (side * side)
    rng = np.random.default_rng(seed)
    return {
        "EAST": UTM[0] + (i + 0.5) * block, "NORTH": UTM[1] + (j + 0.5) * block, "RL": 200.0 + (k + 0.5) * block,
        "_EAST": np.full(n_blocks, block), "_NORTH": np.full(n_blocks, block), "_RL": np.full(n_blocks, block),
        "AU": rng.lognormal(-1.0, 1.0, n_blocks), "DENSITY": np.full(n_blocks, 2.7),
    }
