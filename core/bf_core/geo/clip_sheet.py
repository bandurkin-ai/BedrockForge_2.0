"""An open surface as a closed solid, so that it can take part in a boolean (ТЗ 7.23 «Обрезка»; ADR 0015 §7).

    sweep_solid(surface, direction, length)   closed solid between a sheet and its copy shifted along `direction`
    orient_sheet(v, t, d)                     triangles turned to look along `d`, or why the sheet is not single-valued
    sheet_along(v, t, direction, name)        the direction (+Z or the dominant normal by default) and the
                                              triangles turned to look along it, or why it is not a sheet
    marked_sweep(v, t, direction, length)     the swept solid with its triangles marked by origin
    sheet_part(mesh, first)                   the triangles of a boolean result that came from the surface itself

A surface is swept along a direction in which it is a single-valued sheet (no triangle parallel to it, no
folds, no parts above one another) into a closed solid whose triangles keep a mark of their origin
(`run_original_id`: «поверхность», «копия», «стенки»). After a boolean the triangles marked «поверхность» are
the clipped surface (`clip.clip_by_polygon`, `clip.clip_by_surface` of an open wireframe), and triangles marked
«стенки» left in a part say that a surface did not cover a solid (`clip.clip_by_surface`). Every triangle of the
surface is its own face (`face_id`), so coplanar neighbours are not merged: the triangulation of the surface
away from the cut survives the operation, its vertices bit for bit (local whole-number origin).

Ported from BF `geo/solids.py` (`sweep_solid`, `_orient_sheet`, `_default_direction`, `_projected_overlaps`,
`_sweep_manifold`). Errors: `solids.SolidError` {code, message, hint} in Russian.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from manifold3d import Manifold, Mesh64

from bf_core.geo.checks import orientation_conflicts
from bf_core.geo.meshes import edge_summary
from bf_core.geo.solids import (
    FIX_HINT,
    SolidError,
    Tick,
    compact,
    empty_error,
    from_manifold,
    local_origin,
    mesh_arrays,
    run_triangles,
    to_manifold,
    to_project,
)

SURFACE, COPY, WALLS = 0, 1, 2  # offsets of the marks from the first reserved id


def unit(vector: Any, what: str) -> np.ndarray:
    try:
        n = np.asarray(vector, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise SolidError("bad_parameter", f"{what}: ожидается вектор из трёх чисел.", "Задайте три числа.") from exc
    if n.shape != (3,) or not np.isfinite(n).all():
        raise SolidError("bad_parameter", f"{what}: ожидается вектор из трёх чисел.", "Задайте три числа.")
    length = float(np.linalg.norm(n))
    if length == 0:
        raise SolidError("bad_parameter", f"{what}: вектор нулевой длины.", "Задайте ненулевой вектор.")
    return n / length


def perpendicular_basis(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    axis = np.zeros(3)
    axis[int(np.argmin(np.abs(d)))] = 1.0
    e1 = np.cross(d, axis)
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(d, e1)


def projected_overlaps(v: np.ndarray, t: np.ndarray, d: np.ndarray, tick: Tick = None) -> int:
    """Triangles whose centroid, projected along `d`, lies strictly inside another triangle."""
    from bf_core.geo.crossing import overlapping_centroids

    e1, e2 = perpendicular_basis(d)
    p = np.zeros((len(v), 3))
    p[:, 0], p[:, 1] = v @ e1, v @ e2
    return overlapping_centroids(p, t, tick=tick, message="Проверка поверхности")


def orient_sheet(v: np.ndarray, t: np.ndarray, d: np.ndarray, tick: Tick = None) -> tuple[np.ndarray | None, str]:
    """Triangles turned so that every normal looks along `d`, if the surface is a single-valued sheet seen
    along `d`; otherwise (None, a Russian reason)."""
    if len(t) == 0:
        return None, "в поверхности нет треугольников"
    a = v[t[:, 0]]
    normal = np.cross(v[t[:, 1]] - a, v[t[:, 2]] - a)
    size = np.sqrt(np.einsum("ij,ij->i", normal, normal))
    degenerate = int(np.count_nonzero(size == 0))
    if degenerate:
        return None, f"в ней вырожденные треугольники ({degenerate})"
    cosine = (normal @ d) / size
    steep = int(np.count_nonzero(np.abs(cosine) < 1e-9))
    if steep:
        return None, f"треугольников, параллельных направлению, — {steep}"
    oriented = np.where((cosine < 0)[:, None], t[:, ::-1], t)
    conflicts = orientation_conflicts(oriented, len(v))
    if conflicts:
        return None, f"поверхность образует складки (рёбер перегиба — {conflicts})"
    _, nonmanifold = edge_summary(oriented, len(v))
    if nonmanifold:
        return None, f"в ней рёбра, общие для трёх и более треугольников ({nonmanifold})"
    overlaps = projected_overlaps(v, oriented, d, tick)
    if overlaps:
        return None, f"её части перекрывают друг друга в проекции (треугольников — {overlaps})"
    return oriented, ""


def dominant_normal(v: np.ndarray, t: np.ndarray) -> np.ndarray:
    """The axis the normals of a surface agree on whatever their signs (principal axis of Σ n nᵀ / |n|),
    turned to the side most of them look to."""
    a = v[t[:, 0]]
    normal = np.cross(v[t[:, 1]] - a, v[t[:, 2]] - a)
    size = np.sqrt(np.einsum("ij,ij->i", normal, normal))
    good = size > 0
    if not good.any():
        return np.array([0.0, 0.0, 1.0])
    normal, size = normal[good], size[good]
    _, vectors = np.linalg.eigh((normal / size[:, None]).T @ normal)
    axis = vectors[:, -1]
    along = float(normal.sum(axis=0) @ axis)
    if abs(along) > 1e-9 * float(size.sum()):
        return axis if along > 0 else -axis
    return axis if axis[int(np.argmax(np.abs(axis)))] > 0 else -axis


def sheet_along(
    local: np.ndarray, t: np.ndarray, direction: Any, name: str, tick: Tick = None
) -> tuple[np.ndarray, np.ndarray]:
    """(direction, triangles turned to look along it) of a surface that must be a single-valued sheet.

    `direction` None: +Z when the sheet is single-valued in Z, else its dominant normal; "normal": the
    dominant normal outright; a vector: that one. SolidError `not_a_sheet` says why it is not a sheet."""
    if direction is None:
        up = np.array([0.0, 0.0, 1.0])
        oriented, _ = orient_sheet(local, t, up, tick)
        if oriented is not None:
            return up, oriented
        d = dominant_normal(local, t)
    elif isinstance(direction, str) and direction == "normal":
        d = dominant_normal(local, t)
    else:
        d = unit(direction, "Направление обрезки")
    return d, sheet_or_error(local, t, d, name, tick)


def not_a_sheet(name: str, reason: str) -> SolidError:
    return SolidError(
        "not_a_sheet",
        f"Поверхность «{name}» не однозначна вдоль направления обрезки: {reason}.",
        "Выберите другое направление или разделите поверхность на части; вырожденные треугольники и рёбра "
        "с тремя треугольниками снимает «Исправить каркас».",
    )


def sweep_arrays(
    v: np.ndarray, t: np.ndarray, direction: np.ndarray, length: float
) -> tuple[np.ndarray, np.ndarray, tuple[int, int, int]]:
    """Solid between a sheet (normals against `direction`) and its copy shifted by direction × length:
    (vertices — the sheet's, then the copy's; triangles; counts of (surface, copy, walls) triangles)."""
    v, t = compact(v, t)
    nv = len(v)
    a = np.concatenate([t[:, 0], t[:, 1], t[:, 2]])
    b = np.concatenate([t[:, 1], t[:, 2], t[:, 0]])
    keys = np.minimum(a, b) * nv + np.maximum(a, b)
    _, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    boundary = counts[inverse.reshape(-1)] == 1
    a, b = a[boundary], b[boundary]
    walls = np.concatenate([np.stack([b, a, a + nv], axis=1), np.stack([b, a + nv, b + nv], axis=1)])
    vertices = np.concatenate([v, v + direction * length])
    triangles = np.concatenate([t, t[:, ::-1] + nv, walls])
    return vertices, triangles, (len(t), len(t), len(walls))


def marked_sweep(v: np.ndarray, t: np.ndarray, direction: np.ndarray, length: float, name: str) -> tuple[Manifold, int]:
    """The swept solid (local coordinates) with its triangles marked by origin: first + SURFACE, + COPY, + WALLS."""
    vertices, triangles, counts = sweep_arrays(v, t, direction, length)
    first = int(Manifold.reserve_ids(3))
    run_index = (np.concatenate([[0], np.cumsum(counts)]) * 3).astype(np.uint64)
    man = to_manifold(
        vertices,
        triangles,
        name,
        run_index=run_index,
        run_original_id=np.arange(first, first + 3, dtype=np.uint32),
        face_id=np.arange(len(triangles), dtype=np.uint64),
    )
    return man, first


def sheet_part(mesh: Mesh64, first: int) -> tuple[np.ndarray, np.ndarray]:
    """Local vertices and triangles of a boolean result that came from the swept surface itself."""
    v, t = from_manifold(mesh)
    return compact(v, run_triangles(mesh, t, first + SURFACE))


def sweep_solid(
    surface: Any, direction: Any, length: float, *, name: str = "Поверхность", tick: Tick = None
) -> tuple[np.ndarray, np.ndarray]:
    """Closed solid made of an open surface, its copy shifted by direction × length and walls along the
    boundary loops (project coordinates). The surface must be a single-valued sheet seen along `direction`."""
    v, t = mesh_arrays(surface, name)
    d = unit(direction, "Направление")
    if not (np.isfinite(length) and length > 0):
        raise SolidError("bad_parameter", "Длина вытягивания должна быть положительной.", "Задайте длину больше нуля.")
    if len(t) == 0:
        raise empty_error(name)
    origin = local_origin(v)
    local = v - origin
    oriented, reason = orient_sheet(local, t, -d, tick)
    if oriented is None:
        raise not_a_sheet(name, reason)
    vertices, triangles, _ = sweep_arrays(local, oriented, d, float(length))
    to_manifold(vertices, triangles, name)
    return to_project(vertices, triangles, origin)


def sheet_or_error(local: np.ndarray, t: np.ndarray, d: np.ndarray, name: str, tick: Tick = None) -> np.ndarray:
    oriented, reason = orient_sheet(local, t, d, tick)
    if oriented is None:
        raise not_a_sheet(name, reason)
    return oriented


def outside_footprint(points: np.ndarray, v: np.ndarray, t: np.ndarray, d: np.ndarray) -> bool:
    """True if any of the points, projected along `d`, misses every triangle of the surface."""
    e1, e2 = perpendicular_basis(d)
    px, py = v @ e1, v @ e2
    ax, ay = px[t[:, 0]], py[t[:, 0]]
    bx, by = px[t[:, 1]], py[t[:, 1]]
    cx, cy = px[t[:, 2]], py[t[:, 2]]
    for point in points:
        x, y = float(point @ e1), float(point @ e2)
        w0 = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
        w1 = (cx - bx) * (y - by) - (cy - by) * (x - bx)
        w2 = (ax - cx) * (y - cy) - (ay - cy) * (x - cx)
        inside = ((w0 >= 0) & (w1 >= 0) & (w2 >= 0)) | ((w0 <= 0) & (w1 <= 0) & (w2 <= 0))
        if not inside.any():
            return True
    return False


def part_representatives(v: np.ndarray, t: np.ndarray) -> np.ndarray:
    """One vertex of every connected part."""
    from bf_core.geo.solids import part_labels

    labels = part_labels(t, len(v))
    _, first = np.unique(labels[t[:, 0]], return_index=True)
    return v[t[first, 0]]


def check_sheet_input(v: np.ndarray, t: np.ndarray, name: str) -> None:
    if len(t) == 0:
        raise empty_error(name)
    _, nonmanifold = edge_summary(t, len(v))
    if nonmanifold:
        raise SolidError(
            "wireframe_nonmanifold",
            f"Каркас «{name}»: рёбер, общих для трёх и более треугольников, — {nonmanifold}.",
            FIX_HINT,
        )
