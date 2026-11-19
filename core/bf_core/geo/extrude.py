"""Extrusion of polygons and manual edits of triangles (ТЗ 7.23; ADR 0015 §7).

    extrude_polygon(polygon, distance, vector, vertical, both_sides)   polygon → closed prism
    extrude_polygons(polygons, ...)                                    several prisms in one mesh
    edit_triangles(v, t, remove, add)                                  drop triangles by number, add by corners

Extrusion. The polygon is moved by `distance` along the normal of its best-fit plane (Newell's vector), along
the vertical or along `vector` (only its direction is used); with `both_sides` half the distance each way.
Caps: the triangulation of `geo/polygon.py` (earcut, every vertex used); walls: two triangles an edge. The
volume is the area of the polygon times the distance along its normal: area × distance for the normal, the
plan area × distance for the vertical (exact up to float64 rounding). Normals look outward for any winding and
sign of the distance. Vertices: the polygon as given (bit-identical, unless `both_sides`), then its copy.

Edits («Править треугольники»): triangles are dropped by number and added by their corners (k, 3, 3); a corner
that coincides bit for bit with a vertex (−0.0 = 0.0) reuses the first such vertex, otherwise it becomes a new
vertex at the end. Vertices left without triangles are dropped; the others keep their order and coordinates.
The corners are looked up among the vertices with the same X (one sorted pass), not one scan per corner.

Ported from BF `geo/stitch.py` (`extrude_polygon`, `edit_triangles`). Differences: the vertical, several
polygons, the look-up of corners, errors with a code and a hint.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from bf_core.geo.polygon import StitchError, area_vector, cap_triangles, clean_polygon

UP = np.array([0.0, 0.0, 1.0])
EXTRUDE_HINT = "Проверьте полигон и параметры выдавливания."
EDIT_HINT = "Выберите треугольники каркаса заново."


def _direction(normal: np.ndarray, vector: Any, vertical: bool) -> np.ndarray:
    if vertical and vector is not None:
        raise StitchError(
            "Выдавливание: задайте либо вертикаль, либо вектор.", code="bad_parameter", hint=EXTRUDE_HINT
        )
    if vertical:
        direction = UP
    elif vector is not None:
        direction = np.asarray(vector, dtype=np.float64).reshape(-1)
        size = float(np.linalg.norm(direction)) if direction.shape == (3,) else 0.0
        if not (size > 0 and math.isfinite(size)):
            raise StitchError(
                "Вектор выдавливания должен быть ненулевым вектором из трёх чисел.",
                code="bad_parameter", hint=EXTRUDE_HINT,
            )  # fmt: skip
        direction = direction / size
    else:
        return normal
    if abs(float(direction @ normal)) < 1e-9:
        raise StitchError(
            "Вектор выдавливания лежит в плоскости полигона: тело получается плоским.",
            code="bad_parameter", hint="Выдавите полигон по нормали или задайте другой вектор.",
        )  # fmt: skip
    return np.asarray(direction)


def extrude_polygon(
    polygon: Any,
    *,
    distance: float,
    vector: Any = None,
    vertical: bool = False,
    both_sides: bool = False,
    what: str = "Полигон",
) -> tuple[np.ndarray, np.ndarray]:
    """Closed solid (vertices float64, triangles int32): the polygon moved by `distance` along its plane normal,
    the vertical or `vector`. Vertices: the base polygon (as given, unless `both_sides`), then its moved copy."""
    p = clean_polygon(polygon, what)
    if not (math.isfinite(distance) and distance != 0):
        raise StitchError(
            "Расстояние выдавливания должно быть отличным от нуля числом.", code="bad_parameter", hint=EXTRUDE_HINT
        )
    area = area_vector(p)
    length = float(np.linalg.norm(area))
    if length <= 1e-12 * max(float(np.ptp(p, axis=0).max()) ** 2, 1e-300):
        raise StitchError(f"{what} вырожден: у него нулевая площадь.", code="degenerate_polygon", hint=EXTRUDE_HINT)
    normal = area / length
    direction = _direction(normal, vector, vertical)
    move = direction * float(distance)
    base, top = (p - move / 2.0, p + move / 2.0) if both_sides else (p, p + move)
    k = len(p)
    cap = cap_triangles(p).astype(np.int64)
    i = np.arange(k)
    j = (i + 1) % k
    tris = np.vstack([cap[:, ::-1], cap + k, np.column_stack([i, j, i + k]), np.column_stack([j, j + k, i + k])])
    if float(move @ normal) < 0:  # the copy went below the polygon: everything is inside out
        tris = tris[:, ::-1]
    return np.vstack([base, top]), np.ascontiguousarray(tris, dtype=np.int32)


def extrude_polygons(
    polygons: Sequence[Any],
    *,
    distance: float,
    vector: Any = None,
    vertical: bool = False,
    both_sides: bool = False,
    progress: Any = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Every polygon extruded (`extrude_polygon`), the prisms in one mesh in the order given (not welded).
    `progress(fraction, message)` is called between the polygons and may raise to cancel."""
    if not len(polygons):
        raise StitchError("Для выдавливания нужен хотя бы один полигон.", code="bad_parameter", hint=EXTRUDE_HINT)
    vs: list[np.ndarray] = []
    ts: list[np.ndarray] = []
    base = 0
    for number, polygon in enumerate(polygons, start=1):
        if progress is not None:
            progress((number - 1) / len(polygons), f"Выдавливание: {number} из {len(polygons)}")
        v, t = extrude_polygon(
            polygon, distance=distance, vector=vector, vertical=vertical, both_sides=both_sides,
            what=f"Полигон {number}",
        )  # fmt: skip
        vs.append(v)
        ts.append(t.astype(np.int64) + base)
        base += len(v)
    if base > np.iinfo(np.int32).max:
        raise StitchError("Слишком много вершин для одного каркаса.", code="too_many_vertices", hint=EXTRUDE_HINT)
    if progress is not None:
        progress(1.0, "Готово")
    return np.vstack(vs), np.ascontiguousarray(np.vstack(ts), dtype=np.int32)


def _corner_ids(v: np.ndarray, corners: np.ndarray) -> tuple[np.ndarray, list[np.ndarray]]:
    """Vertex number of every corner: the first vertex equal to it bit for bit, otherwise a new one (numbered
    from len(v) in the order of first appearance); the new vertices."""
    ids = np.empty(len(corners), dtype=np.int64)
    candidates = np.flatnonzero(np.isin(v[:, 0], np.unique(corners[:, 0]))) if len(v) else np.zeros(0, np.int64)
    known: dict[bytes, int] = {}
    fresh: list[np.ndarray] = []
    cx, cy, cz = v[candidates, 0], v[candidates, 1], v[candidates, 2]
    for n, row in enumerate(corners):
        key = row.tobytes()
        if key not in known:
            same = candidates[(cx == row[0]) & (cy == row[1]) & (cz == row[2])]
            if len(same):
                known[key] = int(same[0])
            else:
                known[key] = len(v) + len(fresh)
                fresh.append(row)
        ids[n] = known[key]
    return ids, fresh


def edit_triangles(vertices: Any, triangles: Any, remove: Any, add: Any) -> tuple[np.ndarray, np.ndarray]:
    """Manual repair: drops triangles by index and appends triangles given by corner coordinates.

    `add` is (k, 3, 3) float64. A corner that coincides exactly (bit for bit) with an existing
    vertex reuses it, otherwise a new vertex is appended. Vertices left without triangles are
    dropped; the others keep their order and exact coordinates.
    """
    v = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    t = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    if len(t) and (t.min() < 0 or t.max() >= len(v)):
        raise StitchError(
            "Номера вершин треугольников выходят за пределы списка вершин.", code="bad_mesh", hint=EDIT_HINT
        )
    drop = np.asarray(remove if remove is not None else [], dtype=np.int64).reshape(-1)
    if len(drop) and (drop.min() < 0 or drop.max() >= len(t)):
        raise StitchError("Удаляемый треугольник: номер вне каркаса.", code="bad_triangle", hint=EDIT_HINT)
    extra = np.asarray(add if add is not None else [], dtype=np.float64)
    if extra.size == 0:
        extra = np.zeros((0, 3, 3))
    if extra.ndim != 3 or extra.shape[1:] != (3, 3) or not np.isfinite(extra).all():
        raise StitchError(
            "Добавляемые треугольники: ожидается массив координат (k, 3, 3).", code="bad_triangle", hint=EDIT_HINT
        )
    keep = np.ones(len(t), dtype=bool)
    keep[drop] = False
    t = t[keep]
    if len(extra):
        ids, fresh = _corner_ids(v, extra.reshape(-1, 3) + 0.0)  # −0.0 and 0.0 are the same coordinate
        new = ids.reshape(-1, 3)
        if ((new[:, 0] == new[:, 1]) | (new[:, 1] == new[:, 2]) | (new[:, 0] == new[:, 2])).any():
            raise StitchError(
                "Добавляемый треугольник вырожден: у него совпадают вершины.", code="bad_triangle",
                hint="Укажите три разные вершины.",
            )  # fmt: skip
        if fresh:
            v = np.vstack([v, np.asarray(fresh)])
        t = np.vstack([t, new])
    if len(t) == 0:
        raise StitchError("После правки в каркасе не остаётся треугольников.", code="empty_mesh", hint=EDIT_HINT)
    used = np.unique(t)
    remap = np.full(len(v), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    if len(used) > np.iinfo(np.int32).max:
        raise StitchError("Слишком много вершин для одного каркаса.", code="too_many_vertices", hint=EDIT_HINT)
    return v[used], np.ascontiguousarray(remap[t], dtype=np.int32)
