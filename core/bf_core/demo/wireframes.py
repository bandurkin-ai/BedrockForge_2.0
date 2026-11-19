"""Demo part of Э8 (ТЗ 7.5, 7.21; ADR 0013 «Демо»): wireframes of «Каркасы» around the holes of «Скважины 2025».

  «Рудное тело 1»  the closed solid of the ore lens: the strings of «Рудное тело» (one on every section line
                   Y = 8000 … 8250, `geo.ore_strings`) joined from one to the next and closed at both ends by a
                   smaller ring 15 m out and a point 25 m out — every hole of the grid enters it;
  «Рудное тело 2»  a flat closed lens above it: an ellipsoid of 150 × 140 × 14 m some 40 m under the collars of
                   the middle rows, turned 15° — the holes of the middle columns pass through it;
  «Разлом»         an open surface east of the grid, steep to the west: it cuts the holes of the eastern columns
                   and the east end of the ore lens.

Closed solids look outward (their volume is that of the solid); the fault is open, without a volume. Nothing is
taken from the random stream of the demo: the holes and the parts of Э7 stay as they were. Coordinates are
float64 in centimetres, as the strings are.
"""

from __future__ import annotations

import math
import random

import numpy as np
import sqlalchemy as sa

from bf_core.db.schema import objects
from bf_core.storage.blobs import BlobStore

from .geo import LINES_FILE, ORE, ORE_COLOR

BODY_1, BODY_2, FAULT = "Рудное тело 1", "Рудное тело 2", "Разлом"
BODY_2_COLOR, FAULT_COLOR = "#f0a04b", "#7a8fa6"
LENS = (5215.0, 8150.0, 381.0)  # the centre of «Рудное тело 2»: 45 m down a hole of the middle rows
LENS_AXES = (75.0, 70.0, 7.0)  # its semi-axes, m
LENS_TURN = 15.0  # degrees, about the vertical
FAULT_TOP = (5328.0, 405.0)  # x and z of its upper edge (under the ground, which is at 414–426 m there)
FAULT_DIP = 75.0  # degrees, to the west
FAULT_Y = (7960.0, 8290.0)
FAULT_BOTTOM = 250.0


def _outward(xyz: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """The triangles of a closed solid turned to look outward (positive signed volume)."""
    o = xyz.mean(axis=0)
    a, b, c = (xyz[tri[:, k]] - o for k in range(3))
    signed = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum())
    return tri if signed > 0 else tri[:, [0, 2, 1]]


def _cap(n: int, base: int, tip: int, reverse: bool) -> list[list[int]]:
    """Triangles from the ring of `n` vertices that starts at vertex `base` to the vertex `tip`."""
    out = []
    for j in range(n):
        a, b = base + j, base + (j + 1) % n
        out.append([a, tip, b] if reverse else [a, b, tip])
    return out


def loft(rings: list[np.ndarray], step: float = 25.0) -> tuple[np.ndarray, np.ndarray]:
    """A closed solid through rings of the same number of vertices (open, k × 3, along Y): bands between
    neighbours, and at each end a ring shrunk to 0.6 about its centre `0.6 · step` out and a point `step` out."""
    n = len(rings[0])
    sign = 1.0 if rings[-1][0, 1] > rings[0][0, 1] else -1.0

    def end(ring: np.ndarray, out: float) -> tuple[np.ndarray, np.ndarray]:
        centre = ring.mean(axis=0)
        inner = centre + 0.6 * (ring - centre)
        inner[:, 1] = ring[:, 1] + out * 0.6 * step
        point = centre.copy()
        point[1] = ring[0, 1] + out * step
        return np.round(inner, 2), np.round(point, 2)

    first_ring, first_point = end(rings[0], -sign)
    last_ring, last_point = end(rings[-1], sign)
    chain = [first_ring, *rings, last_ring]
    xyz = np.vstack([*chain, first_point, last_point]).astype(np.float64)
    tri: list[list[int]] = []
    for r in range(len(chain) - 1):
        for j in range(n):
            a, b = r * n + j, r * n + (j + 1) % n
            c, d = a + n, b + n
            tri += [[a, b, d], [a, d, c]]
    tips = len(chain) * n
    tri += _cap(n, 0, tips, reverse=True) + _cap(n, (len(chain) - 1) * n, tips + 1, reverse=False)
    t = np.asarray(tri, dtype=np.int64)
    return xyz, _outward(xyz, t)


def ellipsoid(
    centre: tuple[float, float, float], axes: tuple[float, float, float], turn: float, rings: int = 10, around: int = 24
) -> tuple[np.ndarray, np.ndarray]:
    """A closed ellipsoid: `rings` parallels of `around` vertices and the two poles, turned `turn`° about Z."""
    c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))
    pts = []
    for i in range(1, rings + 1):
        lat = math.pi * i / (rings + 1) - math.pi / 2
        for j in range(around):
            lon = 2 * math.pi * j / around
            u, v = axes[0] * math.cos(lat) * math.cos(lon), axes[1] * math.cos(lat) * math.sin(lon)
            w = axes[2] * math.sin(lat)
            pts.append((centre[0] + c * u - s * v, centre[1] + s * u + c * v, centre[2] + w))
    pts += [(centre[0], centre[1], centre[2] - axes[2]), (centre[0], centre[1], centre[2] + axes[2])]
    xyz = np.round(np.array(pts, dtype=np.float64), 2)
    tri: list[list[int]] = []
    for i in range(rings - 1):
        for j in range(around):
            a, b = i * around + j, i * around + (j + 1) % around
            tri += [[a, b, b + around], [a, b + around, a + around]]
    south, north, top = rings * around, rings * around + 1, (rings - 1) * around
    for j in range(around):
        tri += [[south, (j + 1) % around, j], [top + j, top + (j + 1) % around, north]]
    t = np.asarray(tri, dtype=np.int64)
    return xyz, _outward(xyz, t)


def fault_surface(columns: int = 12, rows: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """The open surface of the fault: a grid along the strike (Y) and down the dip, gently waved."""
    cot = 1.0 / math.tan(math.radians(FAULT_DIP))
    pts = []
    for i in range(rows + 1):
        z = FAULT_TOP[1] - (FAULT_TOP[1] - FAULT_BOTTOM) * i / rows
        for j in range(columns + 1):
            y = FAULT_Y[0] + (FAULT_Y[1] - FAULT_Y[0]) * j / columns
            x = FAULT_TOP[0] - (FAULT_TOP[1] - z) * cot + 3.0 * math.sin((y - FAULT_Y[0]) / 60.0)
            pts.append((x, y, z))
    xyz = np.round(np.array(pts, dtype=np.float64), 2)
    w = columns + 1
    tri = [
        q
        for i in range(rows)
        for j in range(columns)
        for q in ([i * w + j, i * w + j + 1, (i + 1) * w + j + 1], [i * w + j, (i + 1) * w + j + 1, (i + 1) * w + j])
    ]
    return xyz, np.asarray(tri, dtype=np.int64)


def ore_rings(conn: sa.Connection, store: BlobStore) -> list[np.ndarray]:
    """The strings of «Рудное тело» as stored (closed: the last vertex repeats the first), as open rings."""
    from bf_core.lines import store as ls

    file_id = conn.execute(
        sa.select(objects.c.id).where(objects.c.kind == ls.LINE_FILE, objects.c.name == LINES_FILE)
    ).scalar_one()
    layer = next(
        layer
        for layer in (ls.read_layer(conn, store, i) for i in ls.layer_ids(conn, int(file_id)))
        if layer.name == ORE
    )
    rings = []
    for i in range(layer.line_count):
        ring = np.asarray(layer.line(i), dtype=np.float64)
        rings.append(ring[:-1] if ring[0].tobytes() == ring[-1].tobytes() else ring)
    return rings


def add_wireframes(conn: sa.Connection, store: BlobStore, _rng: random.Random) -> None:
    from bf_core.wireframes import store as wf

    v, t = loft(ore_rings(conn, store))
    wf.put_wireframe(
        conn, store, v, t, name=BODY_1, color=ORE_COLOR, sort_key=1.0,
        attrs=[{"name": "Тип", "value": "Руда"}, {"name": "Плотность", "value": 2.7}],
    )  # fmt: skip
    v, t = ellipsoid(LENS, LENS_AXES, LENS_TURN)
    wf.put_wireframe(
        conn, store, v, t, name=BODY_2, color=BODY_2_COLOR, sort_key=2.0,
        attrs=[{"name": "Тип", "value": "Руда"}, {"name": "Плотность", "value": 2.65}],
    )  # fmt: skip
    v, t = fault_surface()
    wf.put_wireframe(
        conn, store, v, t, name=FAULT, color=FAULT_COLOR, sort_key=3.0, attrs=[{"name": "Тип", "value": "Разлом"}]
    )
