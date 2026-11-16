"""Operations on closed solids and open surfaces (docs/prompts/strings-wireframes-editing.md, 6.4).

    is_closed_solid(v, t)                 cheap precondition of the boolean operations
    boolean(a, b, op)                     union / intersection / difference of two closed solids
    boolean_many(meshes, op)              the same for several solids at once
    clip_by_plane(mesh, point, normal)    a solid (closed parts) or an open surface cut by a plane
    clip_by_polygon(mesh, polygon_xy)     ... by a vertical prism through a polygon in plan
    sweep_solid(surface, direction, l)    closed solid between a sheet and its shifted copy
    clip_by_surface(solid, surface)       a solid cut by an open surface into "above" and "below"
    intersection_lines(a, b)              polylines where two meshes cross
    expand(solid, distance)               not implemented (see the function)

Meshes are `(vertices (n, 3) float64, triangles (m, 3) int)` in project coordinates.

Boolean operations are done by manifold3d (Apache-2.0): guaranteed manifold output, symbolic
perturbation for coincident and nearly coincident faces. Its tolerance scales with the size of
the coordinates, so every operation works relative to an integer origin near the centre of
the inputs (the shift is exact in float64) and adds it back at the end.

An open surface cannot take part in a boolean operation directly: it is swept along a
direction in which it is a single-valued sheet into a closed solid whose triangles keep a
mark of their origin (surface / shifted copy / walls), so after the operation the triangles
that came from the surface can be picked out again (clip_by_polygon for a surface) and walls
left in the result tell that the surface did not cover the solid (clip_by_surface).

intersection_lines: a uniform grid over the common part of the two bounding boxes (the
same broad phase as geo/checks.py), the exact segment of every crossing pair of triangles
(Möller's interval method; a point on an edge is computed from the edge's ends in a fixed
order, so neighbouring pairs get it bit for bit equal), then welding and chaining. Coplanar
pairs give no segment.

Messages of SolidError are for the user (Russian).
"""

from __future__ import annotations

import math

import manifold3d
import numpy as np
from manifold3d import Manifold, Mesh64
from numba import njit, prange

from app.formats.wireframes import edge_summary
from app.geo.checks import orientation_conflicts

MAX_CELLS_PER_TRIANGLE = 512
_SLICE = 1 << 20
_FIX_TOOL = "«Исправление каркаса»"
_OPS = {
    "union": manifold3d.OpType.Add,
    "intersection": manifold3d.OpType.Intersect,
    "difference": manifold3d.OpType.Subtract,
}
_OP_NAMES = {"union": "Объединение", "intersection": "Пересечение", "difference": "Вычитание"}


class SolidError(ValueError):
    """An input the operation cannot work with; the message is shown to the user."""


# ------------------------------------------------------------------ arrays and measures


def _arrays(mesh, name: str) -> tuple[np.ndarray, np.ndarray]:
    try:
        vertices, triangles = mesh
        v = np.ascontiguousarray(vertices, dtype=np.float64)
        t = np.ascontiguousarray(triangles, dtype=np.int64)
    except (TypeError, ValueError) as exc:
        raise SolidError(f"{name}: ожидаются массивы вершин и треугольников") from exc
    if v.ndim != 2 or v.shape[1] != 3 or t.ndim != 2 or t.shape[1] != 3:
        raise SolidError(f"{name}: ожидаются вершины (n, 3) и треугольники (m, 3)")
    if len(t) and (t.min() < 0 or t.max() >= len(v)):
        raise SolidError(f"{name}: треугольники ссылаются на несуществующие вершины")
    if len(v) and not np.isfinite(v).all():
        raise SolidError(f"{name}: в координатах вершин есть нечисловые значения")
    return v, t


def _local_origin(*vertex_arrays: np.ndarray) -> np.ndarray:
    """An integer point near the centre of the common extent."""
    arrays = [v for v in vertex_arrays if len(v)]
    lo = np.min([v.min(axis=0) for v in arrays], axis=0)
    hi = np.max([v.max(axis=0) for v in arrays], axis=0)
    return np.round((lo + hi) / 2.0)


def _signed_volume(v: np.ndarray, t: np.ndarray) -> float:
    """Signed volume (tetrahedra from the origin); `v` should be in local coordinates."""
    parts = []
    for start in range(0, len(t), _SLICE):
        tri = t[start:start + _SLICE]
        parts.append(float(np.einsum("ij,ij->i", v[tri[:, 0]], np.cross(v[tri[:, 1]], v[tri[:, 2]])).sum()))
    return math.fsum(parts) / 6.0


def _area(v: np.ndarray, t: np.ndarray) -> float:
    parts = []
    for start in range(0, len(t), _SLICE):
        tri = t[start:start + _SLICE]
        a = v[tri[:, 0]]
        c = np.cross(v[tri[:, 1]] - a, v[tri[:, 2]] - a)
        parts.append(float(np.sqrt(np.einsum("ij,ij->i", c, c)).sum()))
    return math.fsum(parts) / 2.0


def _compact(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Drops the vertices no triangle uses."""
    used = np.zeros(len(v), dtype=bool)
    used[t.reshape(-1)] = True
    if used.all():
        return v, t
    remap = np.cumsum(used) - 1
    return v[used], remap[t]


def _part_count(t: np.ndarray, vertex_count: int) -> int:
    """Number of connected parts (triangles joined through shared vertices)."""
    if len(t) == 0:
        return 0
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    rows = np.concatenate([t[:, 0], t[:, 1]])
    cols = np.concatenate([t[:, 1], t[:, 2]])
    graph = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(vertex_count, vertex_count))
    _, labels = connected_components(graph, directed=False)
    return len(np.unique(labels[t[:, 0]]))


def _watertight(v: np.ndarray, t: np.ndarray) -> bool:
    if len(t) == 0:
        return False
    open_edges, nonmanifold = edge_summary(t, len(v))
    return open_edges == 0 and nonmanifold == 0


def is_closed_solid(vertices, triangles) -> tuple[bool, str]:
    """(True, "") for a closed, consistently oriented manifold mesh; else (False, reason).

    The reason is a Russian phrase for a message ("не замкнут: 12 открытых рёбер")."""
    t = np.asarray(triangles)
    if len(t) == 0 or len(vertices) == 0:
        return False, "пустой каркас (нет треугольников)"
    n = len(vertices)
    open_edges, nonmanifold = edge_summary(t, n)
    reasons = []
    if open_edges:
        reasons.append(f"не замкнут: открытых рёбер — {open_edges}")
    if nonmanifold:
        reasons.append(f"неманифолдных рёбер (общих для трёх и более треугольников) — {nonmanifold}")
    if not reasons:
        conflicts = orientation_conflicts(t, n)
        if conflicts:
            reasons.append(f"нормали соседних треугольников не согласованы (рёбер — {conflicts})")
    return (not reasons), "; ".join(reasons)


# ------------------------------------------------------------------ manifold3d bridge


def _check_status(man: Manifold, name: str) -> None:
    status = man.status()
    if status != manifold3d.Error.NoError:
        raise SolidError(
            f"{name}: библиотека булевых операций не приняла каркас ({status.name}). "
            f"Исправьте его инструментом {_FIX_TOOL}."
        )


def _manifold(v_local: np.ndarray, t: np.ndarray, name: str, **runs) -> Manifold:
    try:
        man = Manifold(Mesh64(vert_properties=np.ascontiguousarray(v_local, dtype=np.float64),
                              tri_verts=np.ascontiguousarray(t, dtype=np.uint64), **runs))
    except Exception as exc:  # the binding raises on malformed arrays
        raise SolidError(f"{name}: библиотека булевых операций не приняла каркас ({exc}). "
                         f"Исправьте его инструментом {_FIX_TOOL}.") from exc
    _check_status(man, name)
    return man


def _solid(v: np.ndarray, t: np.ndarray, origin: np.ndarray, name: str, what: str) -> tuple[Manifold, dict]:
    """A closed solid as a Manifold in local coordinates; inward orientation is turned outward."""
    ok, reason = is_closed_solid(v, t)
    if not ok:
        raise SolidError(f"{name}: {reason}. {what} выполняется только для замкнутых тел; "
                         f"исправьте каркас инструментом {_FIX_TOOL}.")
    local = v - origin
    volume = _signed_volume(local, t)
    flipped = volume < 0
    if flipped:
        t = t[:, ::-1]
    return _manifold(local, t, name), {"volume": abs(volume), "flipped": bool(flipped)}


def _mesh_arrays(mesh: Mesh64) -> tuple[np.ndarray, np.ndarray]:
    """Vertices (local) and triangles of a manifold3d mesh, welded along its merge vectors."""
    v = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
    t = np.asarray(mesh.tri_verts).astype(np.int64)
    src = np.asarray(mesh.merge_from_vert)
    if len(src):
        remap = np.arange(len(v), dtype=np.int64)
        remap[src.astype(np.int64)] = np.asarray(mesh.merge_to_vert).astype(np.int64)
        v, t = _compact(v, remap[t])
    return v, t


def _output(v_local: np.ndarray, t: np.ndarray, origin: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return v_local + origin, t.astype(np.int32)


def _run_triangles(mesh: Mesh64, t: np.ndarray, run_id: int) -> np.ndarray:
    """Triangles of the result that came from the input run marked `run_id`."""
    ids = np.asarray(mesh.run_original_id)
    bounds = np.asarray(mesh.run_index).astype(np.int64) // 3
    picked = [t[bounds[i]:bounds[i + 1]] for i in np.flatnonzero(ids == run_id)]
    return np.concatenate(picked) if picked else np.zeros((0, 3), dtype=np.int64)


def _report_progress(progress, fraction: float, message: str) -> None:
    if progress is not None:
        progress(fraction, message)


# ------------------------------------------------------------------ boolean operations


def boolean_many(meshes: list, op: str, *, names: list[str] | None = None,
                 progress=None) -> tuple[np.ndarray, np.ndarray, dict]:
    """Union / intersection of several closed solids; "difference" = the first minus the rest.

    Returns (vertices, triangles, report). The result is closed and oriented outward
    (manifold3d guarantees a manifold); an empty result has no triangles and report["empty"];
    report["flat"] marks a result without thickness (two bodies touching along a face).
    `progress(fraction, message)` is called between the stages (it may raise to cancel)."""
    if op not in _OPS:
        raise SolidError(f"Неизвестная операция «{op}»: ожидается union, intersection или difference")
    if len(meshes) < 2:
        raise SolidError("Для булевой операции нужно не меньше двух тел")
    names = list(names) if names else [f"Тело {i + 1}" for i in range(len(meshes))]
    _report_progress(progress, 0.0, "Проверка тел")
    arrays = [_arrays(mesh, name) for mesh, name in zip(meshes, names, strict=True)]
    for (v, _), name in zip(arrays, names, strict=True):
        if len(v) == 0:
            raise SolidError(f"{name}: пустой каркас (нет треугольников)")
    origin = _local_origin(*[v for v, _ in arrays])
    solids, infos = [], []
    for (v, t), name in zip(arrays, names, strict=True):
        man, info = _solid(v, t, origin, name, _OP_NAMES[op])
        solids.append(man)
        infos.append(info)
    _report_progress(progress, 0.2, _OP_NAMES[op])
    result = Manifold.batch_boolean(solids, _OPS[op])
    _check_status(result, "Результат")
    v, t = _mesh_arrays(result.to_mesh64())
    _report_progress(progress, 0.9, "Сборка результата")
    volume = abs(_signed_volume(v, t))
    tolerance = float(result.get_tolerance())
    report = {
        "op": op,
        "volumes": [info["volume"] for info in infos],
        "volume": volume,
        "triangles": len(t),
        "closed": True,
        "tolerance": tolerance,
        "origin": origin.tolist(),
        "empty": len(t) == 0,
        # Bodies that only touch along a face leave a closed skin without thickness.
        "flat": len(t) > 0 and volume <= tolerance * _area(v, t),
        "parts": _part_count(t, len(v)),
        "flipped": [name for name, info in zip(names, infos, strict=True) if info["flipped"]],
    }
    _report_progress(progress, 1.0, "Готово")
    return (*_output(v, t, origin), report)


def boolean(a, b, op: str, *, names: tuple[str, str] | None = None,
            progress=None) -> tuple[np.ndarray, np.ndarray, dict]:
    """Union, intersection or difference (a − b) of two closed solids.

    Any orientation is accepted: a solid turned inside out is flipped and named in
    report["flipped"]. Raises SolidError for an input that is not a closed manifold."""
    v, t, report = boolean_many([a, b], op, names=list(names) if names else ["Тело A", "Тело B"],
                                progress=progress)
    report["volume_a"], report["volume_b"] = report["volumes"]
    return v, t, report


# ------------------------------------------------------------------ plane


def _unit(vector, what: str) -> np.ndarray:
    n = np.asarray(vector, dtype=np.float64).reshape(-1)
    if n.shape != (3,) or not np.isfinite(n).all():
        raise SolidError(f"{what}: ожидается вектор из трёх чисел")
    length = float(np.linalg.norm(n))
    if length == 0:
        raise SolidError(f"{what}: вектор нулевой длины")
    return n / length


def _keep(keep: str, options: tuple[str, str]) -> tuple[bool, bool]:
    if keep not in (*options, "both"):
        raise SolidError(f"Неизвестный вариант «{keep}»: ожидается {options[0]}, {options[1]} или both")
    return keep in (options[0], "both"), keep in (options[1], "both")


def _cut_surface(v: np.ndarray, t: np.ndarray, point: np.ndarray, normal: np.ndarray):
    """An open surface cut by a plane: ((v+, t+), (v−, t−)), each possibly without triangles.

    Every crossed edge gets one new vertex, computed from its two ends in a fixed order, so
    the triangles on both sides of the cut share it; a vertex lying on the plane (within
    1e-12 of the size of the surface) is used as it is: no new vertex, no zero-area triangle."""
    d = (v - point) @ normal
    # Rounding noise of the distance is not a side: such a vertex is on the plane.
    d[np.abs(d) <= 1e-12 * max(float(np.abs(v).max()), 1.0)] = 0.0
    positive = d >= 0
    side = positive[t]
    count = side.sum(axis=1)
    crossing = (count > 0) & (count < 3)
    tc, sc = t[crossing], side[crossing]
    rows = np.arange(len(tc))
    lone_positive = sc.sum(axis=1) == 1
    # The corner alone on its side, then the other two in the triangle's own order.
    k = np.where(lone_positive, sc.argmax(axis=1), (~sc).argmax(axis=1))
    a, b, c = tc[rows, k], tc[rows, (k + 1) % 3], tc[rows, (k + 2) % 3]
    nv = len(v)
    lo = np.concatenate([np.minimum(a, b), np.minimum(a, c)])
    hi = np.concatenate([np.maximum(a, b), np.maximum(a, c)])
    keys, inverse = np.unique(lo * nv + hi, return_inverse=True)
    e_lo, e_hi = keys // nv, keys % nv
    f = d[e_lo] / (d[e_lo] - d[e_hi])
    new = v[e_lo] + (v[e_hi] - v[e_lo]) * f[:, None]
    node = nv + np.arange(len(keys))
    node = np.where(d[e_lo] == 0, e_lo, np.where(d[e_hi] == 0, e_hi, node))
    p, q = node[inverse[:len(tc)]], node[inverse[len(tc):]]
    lone = np.stack([a, p, q], axis=1)
    rest = np.concatenate([np.stack([p, b, c], axis=1), np.stack([p, c, q], axis=1)])
    rest_positive = np.concatenate([~lone_positive, ~lone_positive])
    allv = np.concatenate([v, new])
    out = []
    for whole, lone_mask, rest_mask in ((count == 3, lone_positive, rest_positive),
                                       (count == 0, ~lone_positive, ~rest_positive)):
        tri = np.concatenate([t[whole], lone[lone_mask], rest[rest_mask]])
        # A cut through a vertex leaves triangles with a repeated corner: they are empty.
        tri = tri[(tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 2] != tri[:, 0])]
        out.append(_compact(allv, tri))
    return out[0], out[1]


def _part(v: np.ndarray, t: np.ndarray, origin: np.ndarray, wanted: bool):
    return _output(v, t, origin) if wanted and len(t) else None


def clip_by_plane(mesh, point, normal, *, keep: str = "both", name: str = "Каркас") -> dict:
    """Cuts a mesh by the plane through `point` with `normal`.

    A closed solid gives closed parts (the cut is capped); an open surface is cut without
    caps, its new vertices lie on the plane. Returns {"positive": (v, t) | None,
    "negative": (v, t) | None, "report"}: a part is None when it was not asked for or is
    empty. report["kind"] is "solid" or "surface"; volumes (areas for a surface) of the
    source and of both parts and their sum error are always reported."""
    want_pos, want_neg = _keep(keep, ("positive", "negative"))
    v, t = _arrays(mesh, name)
    if len(t) == 0:
        raise SolidError(f"{name}: пустой каркас (нет треугольников)")
    n = _unit(normal, "Нормаль плоскости")
    p = np.asarray(point, dtype=np.float64).reshape(-1)
    if p.shape != (3,) or not np.isfinite(p).all():
        raise SolidError("Точка плоскости: ожидаются три числа")
    origin = _local_origin(v)
    if _watertight(v, t):
        man, info = _solid(v, t, origin, name, "Обрезка тела")
        pos, neg = man.split_by_plane(tuple(n.tolist()), float(n @ (p - origin)))
        parts = [_mesh_arrays(m.to_mesh64()) for m in (pos, neg)]
        volumes = [abs(_signed_volume(pv, pt)) for pv, pt in parts]
        report = {
            "kind": "solid", "closed": True, "volume": info["volume"],
            "volume_positive": volumes[0], "volume_negative": volumes[1],
            "sum_error": abs(volumes[0] + volumes[1] - info["volume"]),
            "tolerance": float(man.get_tolerance()), "flipped": info["flipped"],
        }
    else:
        local = v - origin
        parts = _cut_surface(local, t, p - origin, n)
        area = _area(local, t)
        areas = [_area(pv, pt) for pv, pt in parts]
        report = {
            "kind": "surface", "closed": False, "area": area,
            "area_positive": areas[0], "area_negative": areas[1],
            "sum_error": abs(areas[0] + areas[1] - area),
        }
    report["origin"] = origin.tolist()
    report["triangles_positive"] = len(parts[0][1])
    report["triangles_negative"] = len(parts[1][1])
    return {
        "positive": _part(*parts[0], origin, want_pos),
        "negative": _part(*parts[1], origin, want_neg),
        "report": report,
    }


# ------------------------------------------------------------------ sweeping a sheet into a solid


def _perpendicular_basis(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    axis = np.zeros(3)
    axis[int(np.argmin(np.abs(d)))] = 1.0
    e1 = np.cross(d, axis)
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(d, e1)


def _orient_sheet(v: np.ndarray, t: np.ndarray, d: np.ndarray) -> tuple[np.ndarray | None, str]:
    """Triangles turned so that every normal looks along `d`, if the surface is a single-valued
    sheet seen along `d`; otherwise (None, reason in Russian)."""
    if len(t) == 0:
        return None, "в поверхности нет треугольников"
    a = v[t[:, 0]]
    normal = np.cross(v[t[:, 1]] - a, v[t[:, 2]] - a)
    size = np.sqrt(np.einsum("ij,ij->i", normal, normal))
    degenerate = int(np.count_nonzero(size == 0))
    if degenerate:
        return None, f"в ней вырожденные треугольники ({degenerate}); уберите их инструментом {_FIX_TOOL}"
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
        return None, f"в ней неманифолдные рёбра ({nonmanifold}); уберите их инструментом {_FIX_TOOL}"
    overlaps = _projected_overlaps(v, oriented, d)
    if overlaps:
        return None, f"её части перекрывают друг друга в проекции (треугольников — {overlaps})"
    return oriented, ""


def _projected_overlaps(v: np.ndarray, t: np.ndarray, d: np.ndarray) -> int:
    """Triangles whose centroid, projected along `d`, lies strictly inside another triangle."""
    e1, e2 = _perpendicular_basis(d)
    p = np.zeros((len(v), 3))
    p[:, 0], p[:, 1] = v @ e1, v @ e2
    lo, hi = _boxes(p, t)
    cell, gmin, dims = _grid_frame(lo, hi, lo.min(axis=0), hi.max(axis=0))
    ukeys, runs, ids, big = _grid(lo, hi, cell, gmin, dims)
    return int(_centroids_inside(p, t, cell, gmin, dims, ukeys, runs, ids, big))


def _default_direction(v: np.ndarray, t: np.ndarray, prefer_up: bool = True) -> np.ndarray:
    """+Z for a sheet single-valued in Z, else the dominant normal of the surface."""
    up = np.array([0.0, 0.0, 1.0])
    if prefer_up and _orient_sheet(v, t, up)[0] is not None:
        return up
    a = v[t[:, 0]]
    normal = np.cross(v[t[:, 1]] - a, v[t[:, 2]] - a)
    size = np.sqrt(np.einsum("ij,ij->i", normal, normal))
    good = size > 0
    if not good.any():
        return up
    normal, size = normal[good], size[good]
    # The axis the normals agree on whatever their signs: principal axis of sum(n n^T / |n|).
    _, vectors = np.linalg.eigh((normal / size[:, None]).T @ normal)
    axis = vectors[:, -1]
    along = float(normal.sum(axis=0) @ axis)
    if abs(along) > 1e-9 * float(size.sum()):
        return axis if along > 0 else -axis
    return axis if axis[int(np.argmax(np.abs(axis)))] > 0 else -axis


def _sweep_arrays(v: np.ndarray, t: np.ndarray, direction: np.ndarray, length: float):
    """Solid between a sheet (normals against `direction`) and its copy shifted by
    direction × length: (vertices, triangles, (surface, copy, wall) triangle counts)."""
    v, t = _compact(v, t)
    nv = len(v)
    a = np.concatenate([t[:, 0], t[:, 1], t[:, 2]])
    b = np.concatenate([t[:, 1], t[:, 2], t[:, 0]])
    keys = np.minimum(a, b) * nv + np.maximum(a, b)
    _, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    boundary = counts[inverse] == 1
    a, b = a[boundary], b[boundary]
    walls = np.concatenate([np.stack([b, a, a + nv], axis=1), np.stack([b, a + nv, b + nv], axis=1)])
    vertices = np.concatenate([v, v + direction * length])
    triangles = np.concatenate([t, t[:, ::-1] + nv, walls])
    return vertices, triangles, (len(t), len(t), len(walls))


def _sweep_manifold(v: np.ndarray, t: np.ndarray, direction: np.ndarray, length: float,
                    name: str) -> tuple[Manifold, int]:
    """The swept solid with its triangles marked by origin: ids first (surface), +1 (copy), +2 (walls)."""
    vertices, triangles, counts = _sweep_arrays(v, t, direction, length)
    first = Manifold.reserve_ids(3)
    run_index = (np.concatenate([[0], np.cumsum(counts)]) * 3).astype(np.uint64)
    man = _manifold(vertices, triangles, name, run_index=run_index,
                    run_original_id=np.arange(first, first + 3, dtype=np.uint32),
                    # Every triangle of the surface is its own face: coplanar neighbours are
                    # not merged, the triangulation of the surface survives the operation.
                    face_id=np.arange(len(triangles), dtype=np.uint64))
    return man, first


def _not_a_sheet(name: str, reason: str) -> SolidError:
    return SolidError(
        f"{name}: поверхность не является однозначной вдоль направления обрезки — {reason}. "
        "Выберите другое направление или разделите поверхность на части."
    )


def sweep_solid(surface, direction, length: float, *, name: str = "Поверхность") -> tuple[np.ndarray, np.ndarray]:
    """Closed solid made of an open surface, its copy shifted by direction × length and walls
    along the boundary loops. The surface must be a single-valued sheet seen along
    `direction`, else SolidError says why."""
    v, t = _arrays(surface, name)
    d = _unit(direction, "Направление")
    if not (np.isfinite(length) and length > 0):
        raise SolidError("Длина вытягивания должна быть положительной")
    if len(t) == 0:
        raise SolidError(f"{name}: пустой каркас (нет треугольников)")
    origin = _local_origin(v)
    local = v - origin
    oriented, reason = _orient_sheet(local, t, -d)
    if oriented is None:
        raise _not_a_sheet(name, reason)
    vertices, triangles, _ = _sweep_arrays(local, oriented, d, float(length))
    _manifold(vertices, triangles, name)
    return _output(vertices, triangles, origin)


# ------------------------------------------------------------------ polygon in plan


def _prism(polygon: np.ndarray, z0: float, z1: float) -> Manifold:
    """Vertical prism through a simple polygon (local coordinates), built on the polygon's
    own vertices (manifold3d's CrossSection would round them)."""
    x, y = polygon[:, 0], polygon[:, 1]
    signed = 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    if signed < 0:
        polygon = polygon[::-1]
    polygon = np.ascontiguousarray(polygon)
    broken = SolidError("Полигон обрезки вырожден или самопересекается: нужен простой замкнутый контур")
    if signed == 0:
        raise broken
    try:
        tri = np.asarray(manifold3d.triangulate([polygon]), dtype=np.int64).reshape(-1, 3)
    except Exception as exc:
        raise broken from exc
    a, b, c = (polygon[tri[:, i]] for i in range(3))
    areas = 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    # A simple polygon is tiled by its triangles: no negative ones, the areas add up.
    if len(tri) == 0 or areas.min() < -1e-9 * abs(signed) or abs(areas.sum() - abs(signed)) > 1e-9 * abs(signed) \
            or abs(np.abs(areas).sum() - abs(signed)) > 1e-9 * abs(signed):
        raise broken
    k = len(polygon)
    i = np.arange(k)
    j = (i + 1) % k
    vertices = np.concatenate([np.column_stack([polygon, np.full(k, z0)]), np.column_stack([polygon, np.full(k, z1)])])
    triangles = np.concatenate([tri[:, ::-1], tri + k, np.stack([i, j, j + k], axis=1), np.stack([i, j + k, i + k], axis=1)])
    try:
        return _manifold(vertices, triangles, "Полигон обрезки")
    except SolidError as exc:
        raise broken from exc


def clip_by_polygon(mesh, polygon_xy, *, keep: str = "inside", name: str = "Каркас") -> dict:
    """Cuts a mesh by the vertical prism through a polygon in plan (k, 2), over the whole Z range.

    A closed solid gives closed parts. An open surface gives open parts: it has to be a
    single-valued sheet along Z or along its dominant normal (else SolidError); its
    triangulation away from the cut is kept. Returns {"inside", "outside", "report"}; a part
    is None when it was not asked for or is empty."""
    want_in, want_out = _keep(keep, ("inside", "outside"))
    v, t = _arrays(mesh, name)
    if len(t) == 0:
        raise SolidError(f"{name}: пустой каркас (нет треугольников)")
    try:
        polygon = np.asarray(polygon_xy, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise SolidError("Полигон обрезки: ожидается массив точек (k, 2)") from exc
    if polygon.ndim != 2 or polygon.shape[1] < 2 or not np.isfinite(polygon[:, :2]).all():
        raise SolidError("Полигон обрезки: ожидается массив точек (k, 2)")
    polygon = polygon[:, :2]
    if len(polygon) > 1 and np.array_equal(polygon[0], polygon[-1]):
        polygon = polygon[:-1]
    if len(polygon) < 3:
        raise SolidError("Полигон обрезки: нужно не меньше трёх точек")
    origin = _local_origin(v)
    local = v - origin
    z0, z1 = float(local[:, 2].min()), float(local[:, 2].max())
    margin = 1.0 + 0.5 * (z1 - z0)
    prism = _prism(polygon - origin[:2], z0 - margin, z1 + margin)
    if _watertight(v, t):
        man, info = _solid(v, t, origin, name, "Обрезка тела")
        parts = [_mesh_arrays(m.to_mesh64()) for m in man.split(prism)]
        volumes = [abs(_signed_volume(pv, pt)) for pv, pt in parts]
        report = {
            "kind": "solid", "closed": True, "volume": info["volume"],
            "volume_inside": volumes[0], "volume_outside": volumes[1],
            "sum_error": abs(volumes[0] + volumes[1] - info["volume"]),
            "tolerance": float(man.get_tolerance()), "flipped": info["flipped"],
        }
    else:
        direction = _default_direction(local, t)
        oriented, reason = _orient_sheet(local, t, direction)
        if oriented is None:
            raise _not_a_sheet(name, reason)
        span = float(np.linalg.norm(local.max(axis=0) - local.min(axis=0)))
        man, first = _sweep_manifold(local, oriented, -direction, max(span, 1.0), name)
        parts = []
        for m in man.split(prism):
            out = m.to_mesh64()
            pv, pt = _mesh_arrays(out)
            parts.append(_compact(pv, _run_triangles(out, pt, first)))
        area = _area(local, t)
        areas = [_area(pv, pt) for pv, pt in parts]
        report = {
            "kind": "surface", "closed": False, "area": area,
            "area_inside": areas[0], "area_outside": areas[1],
            "sum_error": abs(areas[0] + areas[1] - area),
            "direction": direction.tolist(), "tolerance": float(man.get_tolerance()),
        }
    report["origin"] = origin.tolist()
    report["triangles_inside"] = len(parts[0][1])
    report["triangles_outside"] = len(parts[1][1])
    return {
        "inside": _part(*parts[0], origin, want_in),
        "outside": _part(*parts[1], origin, want_out),
        "report": report,
    }


# ------------------------------------------------------------------ open surface


def _has_run(mesh: Mesh64, run_id: int) -> bool:
    ids = np.asarray(mesh.run_original_id)
    bounds = np.asarray(mesh.run_index)
    return bool(np.any((ids == run_id) & (bounds[1:] > bounds[:-1])))


def _outside_footprint(points: np.ndarray, v: np.ndarray, t: np.ndarray, d: np.ndarray) -> bool:
    """True if any of the points, projected along `d`, misses every triangle of the surface."""
    e1, e2 = _perpendicular_basis(d)
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


def _part_representatives(v: np.ndarray, t: np.ndarray) -> np.ndarray:
    """One vertex of every connected part."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    rows = np.concatenate([t[:, 0], t[:, 1]])
    cols = np.concatenate([t[:, 1], t[:, 2]])
    graph = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(len(v), len(v)))
    _, labels = connected_components(graph, directed=False)
    _, first = np.unique(labels[t[:, 0]], return_index=True)
    return v[t[first, 0]]


def clip_by_surface(solid, surface, *, keep: str = "both", direction=None,
                    names: tuple[str, str] = ("Тело", "Поверхность")) -> dict:
    """Cuts a closed solid by an open surface (a DTM, a fault) into the part on the positive
    side of `direction` ("above") and the other ("below").

    `direction` defaults to +Z when the surface is single-valued in Z, else to its dominant
    normal; "normal" asks for the dominant normal outright (a steep fault is single-valued
    in Z too, but covers little in plan). The surface must be a single-valued sheet along it. below = solid ∩ (the surface
    swept against the direction beyond the solid), above = solid − that sweep. Where the
    surface does not cover the solid along the direction the solid is not cut: that part
    goes to "above", report["covers"] is False and report["warning"] says so.

    Returns {"above": (v, t) | None, "below": (v, t) | None, "report"}; a part is None when
    it was not asked for or is empty."""
    want_above, want_below = _keep(keep, ("above", "below"))
    sv, st = _arrays(solid, names[0])
    fv, ft = _arrays(surface, names[1])
    if len(ft) == 0:
        raise SolidError(f"{names[1]}: пустой каркас (нет треугольников)")
    if len(st) == 0:
        raise SolidError(f"{names[0]}: пустой каркас (нет треугольников)")
    origin = _local_origin(sv)
    man, info = _solid(sv, st, origin, names[0], "Обрезка тела")
    local = fv - origin
    if direction is None or (isinstance(direction, str) and direction == "normal"):
        d = _default_direction(local, ft, prefer_up=direction is None)
    else:
        d = _unit(direction, "Направление обрезки")
    oriented, reason = _orient_sheet(local, ft, d)
    if oriented is None:
        raise _not_a_sheet(names[1], reason)
    heights = np.concatenate([(sv - origin) @ d, local @ d])
    extent = float(heights.max() - heights.min())
    length = extent * 1.1 + 1.0
    sweep, first = _sweep_manifold(local, oriented, -d, length, names[1])
    below, above = man.split(sweep)
    meshes = [above.to_mesh64(), below.to_mesh64()]
    parts = [_mesh_arrays(m) for m in meshes]
    volumes = [abs(_signed_volume(pv, pt)) for pv, pt in parts]
    report = {
        "volume": info["volume"], "volume_above": volumes[0], "volume_below": volumes[1],
        "sum_error": abs(volumes[0] + volumes[1] - info["volume"]),
        "direction": d.tolist(), "length": length, "tolerance": float(man.get_tolerance()),
        "origin": origin.tolist(), "flipped": info["flipped"], "closed": True,
        "triangles_above": len(parts[0][1]), "triangles_below": len(parts[1][1]),
        "covers": True, "uncovered_volume": 0.0,
    }
    # Walls of the sweep inside a part mean the edge of the surface passed through the solid;
    # a part of the solid entirely beside the surface is found by one of its vertices.
    suspect = any(_has_run(m, first + 2) for m in meshes)
    if not suspect and len(parts[0][1]):
        suspect = _outside_footprint(_part_representatives(*parts[0]), local, oriented, d)
    if suspect:
        column, _ = _sweep_manifold(local, oriented[:, ::-1], d, length, names[1])
        uncovered = float((above - column).volume())
        if uncovered > 1e-9 * max(info["volume"], 1e-300):
            report["covers"] = False
            report["uncovered_volume"] = uncovered
            report["warning"] = (
                f"{names[1]} не покрывает {names[0].lower()} полностью вдоль направления обрезки: "
                f"непокрытая часть (объём {uncovered:.2f}) не разрезана и отнесена к части «выше». "
                "Для крутой поверхности (разлома) задайте направление обрезки по её нормали."
            )
    return {
        "above": _part(*parts[0], origin, want_above),
        "below": _part(*parts[1], origin, want_below),
        "report": report,
    }


# ------------------------------------------------------------------ grid broad phase (numba)


@njit(cache=True, inline="always")
def _cell_index(x, g, cell, n):
    c = int((x - g) // cell)
    if c < 0:
        return 0
    if c >= n:
        return n - 1
    return c


@njit(cache=True)
def _grid_counts(lo, hi, cell, gmin, dims):
    m = lo.shape[0]
    out = np.empty(m, dtype=np.int64)
    for i in range(m):
        n = 1
        for k in range(3):
            n *= _cell_index(hi[i, k], gmin[k], cell, dims[k]) - _cell_index(lo[i, k], gmin[k], cell, dims[k]) + 1
        out[i] = n
    return out


@njit(cache=True)
def _grid_fill(lo, hi, cell, gmin, dims, starts, keys, ids, big):
    for i in range(lo.shape[0]):
        if big[i]:
            continue
        x0 = _cell_index(lo[i, 0], gmin[0], cell, dims[0])
        y0 = _cell_index(lo[i, 1], gmin[1], cell, dims[1])
        z0 = _cell_index(lo[i, 2], gmin[2], cell, dims[2])
        x1 = _cell_index(hi[i, 0], gmin[0], cell, dims[0])
        y1 = _cell_index(hi[i, 1], gmin[1], cell, dims[1])
        z1 = _cell_index(hi[i, 2], gmin[2], cell, dims[2])
        p = starts[i]
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    keys[p] = (x * dims[1] + y) * dims[2] + z
                    ids[p] = i
                    p += 1


def _boxes(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lo = v[t[:, 0]]
    hi = lo.copy()
    for k in (1, 2):
        corner = v[t[:, k]]
        np.minimum(lo, corner, out=lo)
        np.maximum(hi, corner, out=hi)
    return lo, hi


def _grid_frame(lo: np.ndarray, hi: np.ndarray, box_lo: np.ndarray, box_hi: np.ndarray,
                size: float | None = None) -> tuple[float, np.ndarray, np.ndarray]:
    """Cell size (twice the typical triangle), lower corner and dimensions of the grid over a box."""
    if size is None:
        size = float(np.median((hi - lo).max(axis=1)))
    span = float((box_hi - box_lo).max())
    cell = max(size * 2.0, span / 2**20, 1e-9)
    dims = np.floor((box_hi - box_lo) / cell).astype(np.int64) + 1
    return cell, np.ascontiguousarray(box_lo, dtype=np.float64), dims


def _grid(lo, hi, cell, gmin, dims):
    """Triangles listed by the cells their boxes cover: (cell keys, run bounds, ids, big ids).

    Triangles covering more than MAX_CELLS_PER_TRIANGLE cells are not listed ("big")."""
    counts = _grid_counts(lo, hi, cell, gmin, dims)
    big = counts > MAX_CELLS_PER_TRIANGLE
    counts[big] = 0
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]]).astype(np.int64)
    total = int(counts.sum())
    keys = np.empty(total, dtype=np.int64)
    ids = np.empty(total, dtype=np.int64)
    _grid_fill(lo, hi, cell, gmin, dims, starts, keys, ids, big)
    order = np.argsort(keys, kind="stable")
    keys, ids = keys[order], ids[order]
    first = np.flatnonzero(np.concatenate([[True], keys[1:] != keys[:-1]])) if total else np.zeros(0, dtype=np.int64)
    return keys[first], np.append(first, total).astype(np.int64), ids, np.flatnonzero(big).astype(np.int64)


@njit(cache=True)
def _strictly_inside(x, y, p, tri):
    ax, ay = p[tri[0], 0], p[tri[0], 1]
    bx, by = p[tri[1], 0], p[tri[1], 1]
    cx, cy = p[tri[2], 0], p[tri[2], 1]
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if area == 0.0:
        return False
    w0 = ((bx - ax) * (y - ay) - (by - ay) * (x - ax)) / area
    w1 = ((cx - bx) * (y - by) - (cy - by) * (x - bx)) / area
    w2 = ((ax - cx) * (y - cy) - (ay - cy) * (x - cx)) / area
    return w0 > 1e-9 and w1 > 1e-9 and w2 > 1e-9


@njit(parallel=True, cache=True)
def _centroids_inside(p, t, cell, gmin, dims, ukeys, runs, ids, big):
    """Number of triangles whose centroid lies strictly inside another triangle (2D, z = 0)."""
    m = t.shape[0]
    found = np.zeros(m, dtype=np.int64)
    for i in prange(m):
        x = (p[t[i, 0], 0] + p[t[i, 1], 0] + p[t[i, 2], 0]) / 3.0
        y = (p[t[i, 0], 1] + p[t[i, 1], 1] + p[t[i, 2], 1]) / 3.0
        key = (_cell_index(x, gmin[0], cell, dims[0]) * dims[1] + _cell_index(y, gmin[1], cell, dims[1])) * dims[2]
        hit = False
        r = np.searchsorted(ukeys, key)
        if r < ukeys.shape[0] and ukeys[r] == key:
            for q in range(runs[r], runs[r + 1]):
                j = ids[q]
                if j != i and _strictly_inside(x, y, p, t[j]):
                    hit = True
                    break
        if not hit:
            for q in range(big.shape[0]):
                j = big[q]
                if j != i and _strictly_inside(x, y, p, t[j]):
                    hit = True
                    break
        if hit:
            found[i] = 1
    return found.sum()


# ------------------------------------------------------------------ triangle–triangle segment (numba)


@njit(cache=True, inline="always")
def _edge_point(v, i, j, di, dj):
    """Point of the edge i–j on the plane (di, dj: signed distances of its ends), computed
    from the lower vertex id to the higher: the same bits whichever triangle asks."""
    if i > j:
        i, j, di, dj = j, i, dj, di
    if di == 0.0:
        return (v[i, 0], v[i, 1], v[i, 2])
    if dj == 0.0:
        return (v[j, 0], v[j, 1], v[j, 2])
    f = di / (di - dj)
    return (v[i, 0] + (v[j, 0] - v[i, 0]) * f, v[i, 1] + (v[j, 1] - v[i, 1]) * f, v[i, 2] + (v[j, 2] - v[i, 2]) * f)


@njit(cache=True, inline="always")
def _same_side(a, b):
    return (a > 0.0 and b > 0.0) or (a < 0.0 and b < 0.0)


@njit(cache=True)
def _plane_chord(v, tri, d0, d1, d2):
    """Ends of the piece of the plane inside the triangle: (found, P, Q)."""
    i0, i1, i2 = tri[0], tri[1], tri[2]
    zero = (0.0, 0.0, 0.0)
    if _same_side(d0, d1):
        return True, _edge_point(v, i2, i0, d2, d0), _edge_point(v, i2, i1, d2, d1)
    if _same_side(d0, d2):
        return True, _edge_point(v, i1, i0, d1, d0), _edge_point(v, i1, i2, d1, d2)
    if _same_side(d1, d2) or d0 != 0.0:
        return True, _edge_point(v, i0, i1, d0, d1), _edge_point(v, i0, i2, d0, d2)
    if d1 != 0.0:
        return True, _edge_point(v, i1, i0, d1, d0), _edge_point(v, i1, i2, d1, d2)
    if d2 != 0.0:
        return True, _edge_point(v, i2, i0, d2, d0), _edge_point(v, i2, i1, d2, d1)
    return False, zero, zero


@njit(cache=True)
def _pair_segment(va, ta, vb, tb):
    """The segment two triangles have in common: (found, x0, y0, z0, x1, y1, z1)."""
    none = (False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    a0x, a0y, a0z = va[ta[0], 0], va[ta[0], 1], va[ta[0], 2]
    b0x, b0y, b0z = vb[tb[0], 0], vb[tb[0], 1], vb[tb[0], 2]
    # Plane of B, distances of A's corners.
    ux, uy, uz = vb[tb[1], 0] - b0x, vb[tb[1], 1] - b0y, vb[tb[1], 2] - b0z
    wx, wy, wz = vb[tb[2], 0] - b0x, vb[tb[2], 1] - b0y, vb[tb[2], 2] - b0z
    nbx, nby, nbz = uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx
    da0 = nbx * (a0x - b0x) + nby * (a0y - b0y) + nbz * (a0z - b0z)
    da1 = nbx * (va[ta[1], 0] - b0x) + nby * (va[ta[1], 1] - b0y) + nbz * (va[ta[1], 2] - b0z)
    da2 = nbx * (va[ta[2], 0] - b0x) + nby * (va[ta[2], 1] - b0y) + nbz * (va[ta[2], 2] - b0z)
    if (da0 > 0.0 and da1 > 0.0 and da2 > 0.0) or (da0 < 0.0 and da1 < 0.0 and da2 < 0.0):
        return none
    ux, uy, uz = va[ta[1], 0] - a0x, va[ta[1], 1] - a0y, va[ta[1], 2] - a0z
    wx, wy, wz = va[ta[2], 0] - a0x, va[ta[2], 1] - a0y, va[ta[2], 2] - a0z
    nax, nay, naz = uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx
    db0 = nax * (b0x - a0x) + nay * (b0y - a0y) + naz * (b0z - a0z)
    db1 = nax * (vb[tb[1], 0] - a0x) + nay * (vb[tb[1], 1] - a0y) + naz * (vb[tb[1], 2] - a0z)
    db2 = nax * (vb[tb[2], 0] - a0x) + nay * (vb[tb[2], 1] - a0y) + naz * (vb[tb[2], 2] - a0z)
    if (db0 > 0.0 and db1 > 0.0 and db2 > 0.0) or (db0 < 0.0 and db1 < 0.0 and db2 < 0.0):
        return none
    ok, p1, q1 = _plane_chord(va, ta, da0, da1, da2)
    if not ok:
        return none
    ok, p2, q2 = _plane_chord(vb, tb, db0, db1, db2)
    if not ok:
        return none
    # Both chords lie on the line of the two planes; their overlap along it is the segment.
    dx, dy, dz = nay * nbz - naz * nby, naz * nbx - nax * nbz, nax * nby - nay * nbx
    s1p = dx * p1[0] + dy * p1[1] + dz * p1[2]
    s1q = dx * q1[0] + dy * q1[1] + dz * q1[2]
    s2p = dx * p2[0] + dy * p2[1] + dz * p2[2]
    s2q = dx * q2[0] + dy * q2[1] + dz * q2[2]
    if s1p > s1q:
        s1p, s1q, p1, q1 = s1q, s1p, q1, p1
    if s2p > s2q:
        s2p, s2q, p2, q2 = s2q, s2p, q2, p2
    if s1p >= s2p:
        lo, start = s1p, p1
    else:
        lo, start = s2p, p2
    if s1q <= s2q:
        hi, end = s1q, q1
    else:
        hi, end = s2q, q2
    if lo >= hi:
        return none
    return (True, start[0], start[1], start[2], end[0], end[1], end[2])


@njit(cache=True, inline="always")
def _boxes_touch(loa, hia, i, lob, hib, j):
    for k in range(3):
        if loa[i, k] > hib[j, k] or lob[j, k] > hia[i, k]:
            return False
    return True


@njit(cache=True, inline="always")
def _owned(loa, hia, i, lob, hib, j, cell, gmin, dims, key):
    """The boxes overlap and the lower corner of their overlap lies in the cell `key`:
    a pair listed in several cells is taken in one of them only."""
    c0 = 0
    c1 = 0
    c2 = 0
    for k in range(3):
        a = max(loa[i, k], lob[j, k])
        b = min(hia[i, k], hib[j, k])
        if a > b:
            return False
        c = _cell_index(a, gmin[k], cell, dims[k])
        if k == 0:
            c0 = c
        elif k == 1:
            c1 = c
        else:
            c2 = c
    return (c0 * dims[1] + c1) * dims[2] + c2 == key


@njit(parallel=True, cache=True)
def _scan_pairs(va, ta, loa, hia, vb, tb, lob, hib, cell, gmin, dims, ukeys, runs, ids, big_b,
                offsets, segments, fill):
    """Crossing pairs of triangles of A (rows of `ta`) and B (listed in the grid).

    fill = False: returns the number of segments per triangle of A; fill = True: writes
    them to `segments` from `offsets[i]` on (the same order as when counting)."""
    m = ta.shape[0]
    nb = tb.shape[0]
    counts = np.zeros(m, dtype=np.int64)
    for i in prange(m):
        n = 0
        base = offsets[i] if fill else 0
        x0 = _cell_index(loa[i, 0], gmin[0], cell, dims[0])
        y0 = _cell_index(loa[i, 1], gmin[1], cell, dims[1])
        z0 = _cell_index(loa[i, 2], gmin[2], cell, dims[2])
        x1 = _cell_index(hia[i, 0], gmin[0], cell, dims[0])
        y1 = _cell_index(hia[i, 1], gmin[1], cell, dims[1])
        z1 = _cell_index(hia[i, 2], gmin[2], cell, dims[2])
        if (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1) > MAX_CELLS_PER_TRIANGLE:
            # Too large for the grid: against every triangle of B whose box it touches.
            for j in range(nb):
                if _boxes_touch(loa, hia, i, lob, hib, j):
                    ok, ax, ay, az, bx, by, bz = _pair_segment(va, ta[i], vb, tb[j])
                    if ok:
                        if fill:
                            s = segments[base + n]
                            s[0], s[1], s[2], s[3], s[4], s[5] = ax, ay, az, bx, by, bz
                        n += 1
        else:
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    for z in range(z0, z1 + 1):
                        key = (x * dims[1] + y) * dims[2] + z
                        r = np.searchsorted(ukeys, key)
                        if r >= ukeys.shape[0] or ukeys[r] != key:
                            continue
                        for q in range(runs[r], runs[r + 1]):
                            j = ids[q]
                            if not _owned(loa, hia, i, lob, hib, j, cell, gmin, dims, key):
                                continue
                            ok, ax, ay, az, bx, by, bz = _pair_segment(va, ta[i], vb, tb[j])
                            if ok:
                                if fill:
                                    s = segments[base + n]
                                    s[0], s[1], s[2], s[3], s[4], s[5] = ax, ay, az, bx, by, bz
                                n += 1
            for q in range(big_b.shape[0]):
                j = big_b[q]
                if _boxes_touch(loa, hia, i, lob, hib, j):
                    ok, ax, ay, az, bx, by, bz = _pair_segment(va, ta[i], vb, tb[j])
                    if ok:
                        if fill:
                            s = segments[base + n]
                            s[0], s[1], s[2], s[3], s[4], s[5] = ax, ay, az, bx, by, bz
                        n += 1
        counts[i] = n
    return counts


# ------------------------------------------------------------------ intersection lines


def _weld_points(points: np.ndarray, tolerance: float) -> np.ndarray:
    """Group id of every point: points closer than about `tolerance` share a group.

    Two grids shifted by half a step: points split by a cell border of one grid fall into
    one cell of the other; the groups of both grids are then joined."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(points)
    scaled = points / tolerance
    labels = []
    for shift in (0.0, 0.5):
        cells = np.floor(scaled + shift).astype(np.int64)
        _, inverse = np.unique(cells, axis=0, return_inverse=True)
        labels.append(inverse.reshape(-1))
    groups_a = int(labels[0].max()) + 1
    groups_b = int(labels[1].max()) + 1
    # Bipartite graph "group of grid A — group of grid B", one edge per point.
    size = groups_a + groups_b
    graph = coo_matrix((np.ones(n, dtype=np.int8), (labels[0], labels[1] + groups_a)), shape=(size, size))
    _, component = connected_components(graph, directed=False)
    _, group = np.unique(component[labels[0]], return_inverse=True)
    return group.reshape(-1)


def _chain(nodes: np.ndarray, links: np.ndarray) -> list[tuple[bool, np.ndarray]]:
    """Joins segments (pairs of node ids) into polylines: (closed, node ids).

    A line runs through nodes of degree 2 and ends at any other node (a free end or a
    junction); what is left afterwards are loops."""
    count = len(nodes)
    degree = np.bincount(links.reshape(-1), minlength=count)
    ends = links.reshape(-1)
    order = np.argsort(ends, kind="stable")
    incident = (order // 2).tolist()
    start = np.concatenate([[0], np.cumsum(degree)]).tolist()
    pairs = links.tolist()
    deg = degree.tolist()
    used = [False] * len(pairs)

    def walk(node: int, seg: int) -> list[int]:
        line = [node]
        while True:
            used[seg] = True
            u, w = pairs[seg]
            node = w if u == node else u
            line.append(node)
            if deg[node] != 2 or node == line[0]:
                return line
            s0, s1 = incident[start[node]], incident[start[node] + 1]
            seg = s1 if used[s0] else s0
            if used[seg]:
                return line

    lines = []
    for node in np.flatnonzero((degree != 2) & (degree > 0)).tolist():
        for k in range(start[node], start[node + 1]):
            if not used[incident[k]]:
                lines.append(walk(node, incident[k]))
    for seg in range(len(pairs)):
        if not used[seg]:
            lines.append(walk(pairs[seg][0], seg))
    out = []
    for line in lines:
        closed = len(line) > 3 and line[0] == line[-1]
        out.append((closed, np.asarray(line[:-1] if closed else line, dtype=np.int64)))
    return out


def intersection_lines(a, b, *, tolerance: float | None = None) -> list[dict]:
    """Polylines where two meshes (open or closed) cross: [{"closed": bool, "points": (k, 3)}].

    Points are float64 in project coordinates; a closed line does not repeat its first
    point. Ends of segments closer than `tolerance` (default: 1e-9 of the size of the common
    bounding box) are welded before chaining. Coplanar triangles give no line."""
    va, ta = _arrays(a, "Каркас A")
    vb, tb = _arrays(b, "Каркас B")
    if len(ta) == 0 or len(tb) == 0:
        return []
    origin = _local_origin(va, vb)
    va, vb = va - origin, vb - origin
    box_lo = np.maximum(va.min(axis=0), vb.min(axis=0))
    box_hi = np.minimum(va.max(axis=0), vb.max(axis=0))
    if np.any(box_lo > box_hi):
        return []
    loa, hia = _boxes(va, ta)
    keep_a = np.all((loa <= box_hi) & (hia >= box_lo), axis=1)
    lob, hib = _boxes(vb, tb)
    keep_b = np.all((lob <= box_hi) & (hib >= box_lo), axis=1)
    if not keep_a.any() or not keep_b.any():
        return []
    ta, loa, hia = ta[keep_a], loa[keep_a], hia[keep_a]
    tb, lob, hib = tb[keep_b], lob[keep_b], hib[keep_b]
    # Cells fit the coarser mesh: its triangles are few per cell, so the work stays
    # proportional to the number of triangles of the finer one.
    size = max(float(np.median((hia - loa).max(axis=1))), float(np.median((hib - lob).max(axis=1))))
    cell, gmin, dims = _grid_frame(lob, hib, box_lo, box_hi, size)
    ukeys, runs, ids, big_b = _grid(lob, hib, cell, gmin, dims)
    empty = np.zeros((0, 6))
    counts = _scan_pairs(va, ta, loa, hia, vb, tb, lob, hib, cell, gmin, dims, ukeys, runs, ids, big_b,
                         np.zeros(0, dtype=np.int64), empty, False)
    total = int(counts.sum())
    if total == 0:
        return []
    offsets = np.concatenate([[0], np.cumsum(counts)[:-1]]).astype(np.int64)
    segments = np.empty((total, 6))
    _scan_pairs(va, ta, loa, hia, vb, tb, lob, hib, cell, gmin, dims, ukeys, runs, ids, big_b,
                offsets, segments, True)
    points = segments.reshape(-1, 3)
    if tolerance is None:
        tolerance = max(1e-9 * float(np.linalg.norm(box_hi - box_lo)), 1e-12)
    group = _weld_points(points, float(tolerance))
    nodes = np.empty((int(group.max()) + 1, 3))
    nodes[group[::-1]] = points[::-1]  # the first point of every group represents it
    links = group.reshape(-1, 2)
    links = links[links[:, 0] != links[:, 1]]
    if len(links) == 0:
        return []
    links = np.unique(np.sort(links, axis=1), axis=0)
    return [{"closed": closed, "points": nodes[line] + origin} for closed, line in _chain(nodes, links)]


# ------------------------------------------------------------------ offset


def expand(solid, distance: float):
    """Shell / offset of a solid: not implemented.

    manifold3d offers only a Minkowski sum with a sphere, which decomposes a non-convex
    solid per triangle (far too slow for real wireframes) and leaves a faceted result; it
    is not a reliable offset, so nothing is pretended here."""
    raise SolidError("Расширение (оболочка) тела на заданное расстояние не реализовано")
