"""Closed solids: the manifold3d bridge and the boolean operations (ТЗ 7.23 «Булевы операции», 5.3, 17 п. 5;
ADR 0015 §7).

    is_closed_solid(v, t)                (True, "") or (False, reason): the precondition of every solid operation
    boolean(a, b, op, names=, tick=)     union / intersection / difference (a − b) of two closed solids
    boolean_many(meshes, op, ...)        the same for several solids ("difference" — the first minus the rest)
    canonical(v, t)                      the canonical form of a mesh: the same bytes whatever the order of a result
    expand(solid, distance)              not done (ADR 0015 decision 8): SolidError

The bridge (`to_manifold`, `from_manifold`, `local_origin`, measures) is shared by `clip.py`, `clip_sheet.py`
and `volumes.py`. Meshes are `(vertices (n, 3) float64, triangles (m, 3) int)` in project coordinates; results
are float64 vertices and int32 triangles in project coordinates.

manifold3d 3.5.4 (Apache-2.0) does the booleans: a manifold result is guaranteed, coincident and nearly
coincident faces are resolved by symbolic perturbation. Its tolerance scales with the size of the coordinates,
so every operation works relative to a whole-number origin near the centre of the inputs (ТЗ 17 п. 5; the shift
is exact in float64 on UTM numbers) and adds it back at the end. A solid turned inside out is turned outward
and named in the report.

Progress and cancellation. `tick(fraction, message)` is called between the stages and may raise to cancel.
The boolean itself is one call: manifold3d 3.5.4 holds the GIL while it evaluates (checked in 9.11: an
`ExecutionContext` attached with `with_context` cannot be polled nor cancelled from another Python thread), so
the call is indivisible — about 0.6 s for two spheres of a million triangles each.

Errors are `SolidError` {code, message, hint} in Russian, for the user. Ported from BF `geo/solids.py`
(the boolean half; the clipping is in `clip.py`, the sweeps in `clip_sheet.py`, the crossing lines in
`crossing.py`).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

import manifold3d
import numpy as np
from manifold3d import Manifold, Mesh64

from bf_core.geo.checks import orientation_conflicts
from bf_core.geo.meshes import edge_summary

Tick = Callable[[float, str], None] | None

SLICE = 1 << 20  # triangles measured at once: bounds the temporary memory
FIX_HINT = "Запустите «Исправить каркас» и повторите."
OPS = {
    "union": manifold3d.OpType.Add,
    "intersection": manifold3d.OpType.Intersect,
    "difference": manifold3d.OpType.Subtract,
}
OP_NAMES = {"union": "Объединение", "intersection": "Пересечение", "difference": "Вычитание"}

# manifold3d statuses (manifold3d.Error) in Russian.
_STATUS = {
    "NonFiniteVertex": "в координатах вершин есть нечисловые значения",
    "NotManifold": "каркас не замкнут или у него есть рёбра, общие для трёх и более треугольников",
    "VertexOutOfBounds": "треугольник ссылается на несуществующую вершину",
    "InvalidConstruction": "библиотека не смогла построить тело",
    "ResultTooLarge": "результат слишком велик для библиотеки",
    "Cancelled": "операция отменена",
}
_STATUS_INTERNAL = "данные переданы в библиотеку с ошибкой (внутренняя ошибка программы)"


class SolidError(ValueError):
    """An input a solid or surface operation cannot work with: {code, message, hint} in Russian."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint
        self.status = ""  # manifold3d status, for the log

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


def tick_at(tick: Tick, fraction: float, message: str) -> None:
    if tick is not None:
        tick(float(min(max(fraction, 0.0), 1.0)), message)


def within(tick: Tick, start: float, stop: float) -> Tick:
    """`tick` of a stage that takes the share [start, stop] of the whole."""
    if tick is None:
        return None
    outer = tick
    return lambda fraction, message: outer(start + (stop - start) * min(max(float(fraction), 0.0), 1.0), message)


# --- arrays and measures ---------------------------------------------------------------------------------


def mesh_arrays(mesh: Any, name: str) -> tuple[np.ndarray, np.ndarray]:
    """float64 n×3 vertices and int64 m×3 triangles of `(vertices, triangles)`, or SolidError."""
    try:
        vertices, triangles = mesh
        v = np.ascontiguousarray(vertices, dtype=np.float64)
        t = np.ascontiguousarray(triangles, dtype=np.int64)
    except (TypeError, ValueError) as exc:
        raise SolidError("bad_mesh", f"«{name}»: ожидаются массивы вершин и треугольников.", FIX_HINT) from exc
    if v.size == 0:
        v = v.reshape(0, 3)
    if t.size == 0:
        t = t.reshape(0, 3)
    if v.ndim != 2 or v.shape[1] != 3 or t.ndim != 2 or t.shape[1] != 3:
        raise SolidError("bad_mesh", f"«{name}»: ожидаются вершины (n, 3) и треугольники (m, 3).", FIX_HINT)
    if len(t) and (t.min() < 0 or t.max() >= len(v)):
        raise SolidError("bad_mesh", f"«{name}»: треугольники ссылаются на несуществующие вершины.", FIX_HINT)
    if len(v) and not np.isfinite(v).all():
        raise SolidError("bad_mesh", f"«{name}»: в координатах вершин есть нечисловые значения.", FIX_HINT)
    return v, t


def empty_error(name: str) -> SolidError:
    return SolidError("empty_mesh", f"Каркас «{name}» пуст: в нём нет треугольников.", "Выберите другой каркас.")


def local_origin(*vertex_arrays: np.ndarray) -> np.ndarray:
    """A whole-number point near the centre of the common extent (ТЗ 17 п. 5)."""
    arrays = [v for v in vertex_arrays if len(v)]
    if not arrays:
        return np.zeros(3)
    lo = np.min([v.min(axis=0) for v in arrays], axis=0)
    hi = np.max([v.max(axis=0) for v in arrays], axis=0)
    return np.round((lo + hi) / 2.0)


def signed_volume(v: np.ndarray, t: np.ndarray) -> float:
    """Signed volume (tetrahedra from the origin, slices summed with `math.fsum`); `v` in local coordinates."""
    parts = []
    for start in range(0, len(t), SLICE):
        tri = t[start : start + SLICE]
        parts.append(float(np.einsum("ij,ij->i", v[tri[:, 0]], np.cross(v[tri[:, 1]], v[tri[:, 2]])).sum()))
    return math.fsum(parts) / 6.0


def surface_area(v: np.ndarray, t: np.ndarray) -> float:
    parts = []
    for start in range(0, len(t), SLICE):
        tri = t[start : start + SLICE]
        a = v[tri[:, 0]]
        c = np.cross(v[tri[:, 1]] - a, v[tri[:, 2]] - a)
        parts.append(float(np.sqrt(np.einsum("ij,ij->i", c, c)).sum()))
    return math.fsum(parts) / 2.0


def compact(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Drops the vertices no triangle uses (the order of the others is kept)."""
    used = np.zeros(len(v), dtype=bool)
    used[t.reshape(-1)] = True
    if used.all():
        return v, t
    remap = np.cumsum(used) - 1
    return v[used], remap[t]


def part_labels(t: np.ndarray, vertex_count: int) -> np.ndarray:
    """Connected part of every vertex (triangles joined through shared vertices)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    rows = np.concatenate([t[:, 0], t[:, 1]])
    cols = np.concatenate([t[:, 1], t[:, 2]])
    graph = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(vertex_count, vertex_count))
    return np.asarray(connected_components(graph, directed=False)[1])


def part_count(t: np.ndarray, vertex_count: int) -> int:
    if len(t) == 0:
        return 0
    return len(np.unique(part_labels(t, vertex_count)[t[:, 0]]))


def watertight(v: np.ndarray, t: np.ndarray) -> bool:
    if len(t) == 0:
        return False
    open_edges, nonmanifold = edge_summary(t, len(v))
    return open_edges == 0 and nonmanifold == 0


def _defects(t: np.ndarray, n: int) -> tuple[str, str]:
    """(code, Russian reason) of the first defect that keeps a mesh from being a closed solid, or ("", "")."""
    if len(t) == 0 or n == 0:
        return "empty_mesh", "пуст: в нём нет треугольников"
    open_edges, nonmanifold = edge_summary(t, n)
    reasons = []
    if open_edges:
        reasons.append(f"не замкнут: открытых рёбер — {open_edges}")
    if nonmanifold:
        reasons.append(f"рёбер, общих для трёх и более треугольников, — {nonmanifold}")
    if reasons:
        return ("wireframe_open" if open_edges else "wireframe_nonmanifold"), "; ".join(reasons)
    conflicts = orientation_conflicts(t, n)
    if conflicts:
        return "wireframe_orientation", f"нормали соседних треугольников не согласованы (рёбер — {conflicts})"
    return "", ""


def is_closed_solid(vertices: Any, triangles: Any) -> tuple[bool, str]:
    """(True, "") for a closed, consistently oriented manifold mesh; else (False, a Russian reason such as
    «не замкнут: открытых рёбер — 12»)."""
    t = np.asarray(triangles)
    code, reason = _defects(t.reshape(-1, 3) if t.size else t.reshape(0, 3), len(vertices))
    return not code, reason


# --- the manifold3d bridge -------------------------------------------------------------------------------


def check_status(man: Manifold, name: str) -> None:
    status = man.status()
    if status != manifold3d.Error.NoError:
        text = _STATUS.get(status.name, _STATUS_INTERNAL)
        code = "cancelled" if status.name == "Cancelled" else "solid_rejected"
        error = SolidError(code, f"Библиотека булевых операций не приняла «{name}»: {text}.", FIX_HINT)
        error.status = status.name  # for the log, not for the user
        raise error


def to_manifold(v_local: np.ndarray, t: np.ndarray, name: str, **runs: Any) -> Manifold:
    """A Manifold of local float64 vertices and triangles (`runs`: run_index, run_original_id, face_id)."""
    try:
        mesh = Mesh64(
            vert_properties=np.ascontiguousarray(v_local, dtype=np.float64),
            tri_verts=np.ascontiguousarray(t, dtype=np.uint64),
            **runs,
        )
        man = Manifold(mesh)
    except (TypeError, ValueError, RuntimeError) as exc:  # the binding raises on malformed arrays
        raise SolidError("solid_rejected", f"Библиотека булевых операций не приняла «{name}».", FIX_HINT) from exc
    check_status(man, name)
    return man


def closed_solid(v: np.ndarray, t: np.ndarray, origin: np.ndarray, name: str, what: str) -> tuple[Manifold, dict]:
    """A closed solid as a Manifold in local coordinates; an inside-out solid is turned outward.
    → (manifold, {"volume", "flipped"}); SolidError names the defect («не замкнут: открытых рёбер — 14»)."""
    code, reason = _defects(t, len(v))
    if code == "empty_mesh":
        raise empty_error(name)
    if code:
        raise SolidError(code, f"Каркас «{name}» {reason}. «{what}» выполняется только для замкнутых тел.", FIX_HINT)
    local = v - origin
    volume = signed_volume(local, t)
    flipped = volume < 0
    if flipped:
        t = t[:, ::-1]
    return to_manifold(local, t, name), {"volume": abs(volume), "flipped": bool(flipped)}


def from_manifold(mesh: Mesh64) -> tuple[np.ndarray, np.ndarray]:
    """Local vertices and int64 triangles of a manifold3d mesh, welded along its merge vectors."""
    v = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
    t = np.asarray(mesh.tri_verts).astype(np.int64).reshape(-1, 3)
    src = np.asarray(mesh.merge_from_vert)
    if len(src):
        remap = np.arange(len(v), dtype=np.int64)
        remap[src.astype(np.int64)] = np.asarray(mesh.merge_to_vert).astype(np.int64)
        v, t = compact(v, remap[t])
    return np.ascontiguousarray(v), t


def to_project(v_local: np.ndarray, t: np.ndarray, origin: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return np.ascontiguousarray(v_local + origin), np.ascontiguousarray(t, dtype=np.int32)


def run_triangles(mesh: Mesh64, t: np.ndarray, run_id: int) -> np.ndarray:
    """Triangles of a result that came from the input run marked `run_id` (`run_original_id`)."""
    ids = np.asarray(mesh.run_original_id)
    bounds = np.asarray(mesh.run_index).astype(np.int64) // 3
    picked = [t[bounds[i] : bounds[i + 1]] for i in np.flatnonzero(ids == run_id)]
    return np.concatenate(picked) if picked else np.zeros((0, 3), dtype=np.int64)


def has_run(mesh: Mesh64, run_id: int) -> bool:
    ids = np.asarray(mesh.run_original_id)
    bounds = np.asarray(mesh.run_index)
    return bool(np.any((ids == run_id) & (bounds[1:] > bounds[:-1])))


# --- boolean operations ----------------------------------------------------------------------------------


def boolean_many(
    meshes: Sequence[Any], op: str, *, names: Sequence[str] | None = None, tick: Tick = None
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Union / intersection of several closed solids; "difference" — the first minus the rest.

    → (vertices float64, triangles int32, report). The result is closed and oriented outward (manifold3d
    guarantees a manifold); an empty result has no triangles and `report["empty"]`; `report["flat"]` marks a
    result without thickness (bodies touching along a face). Inputs are read, never changed."""
    if op not in OPS:
        raise SolidError(
            "bad_operation", f"Неизвестная операция «{op}».", "Ожидается union, intersection или difference."
        )
    if len(meshes) < 2:
        raise SolidError("bad_operation", "Для булевой операции нужно не меньше двух тел.", "Выберите два тела.")
    names = list(names) if names else [f"Тело {i + 1}" for i in range(len(meshes))]
    tick_at(tick, 0.0, "Проверка тел")
    arrays = [mesh_arrays(mesh, name) for mesh, name in zip(meshes, names, strict=True)]
    for (_, t), name in zip(arrays, names, strict=True):
        if len(t) == 0:
            raise empty_error(name)
    origin = local_origin(*[v for v, _ in arrays])
    solids, infos = [], []
    for k, ((v, t), name) in enumerate(zip(arrays, names, strict=True)):
        man, info = closed_solid(v, t, origin, name, OP_NAMES[op])
        solids.append(man)
        infos.append(info)
        tick_at(tick, 0.3 * (k + 1) / len(arrays), "Проверка тел")
    tick_at(tick, 0.3, OP_NAMES[op])
    result = Manifold.batch_boolean(solids, OPS[op])
    check_status(result, "Результат")  # evaluates the operation: one call, not cancellable
    tick_at(tick, 0.85, "Сборка результата")
    v, t = from_manifold(result.to_mesh64())
    volume = abs(signed_volume(v, t))
    tolerance = float(result.get_tolerance())
    area = surface_area(v, t) if len(t) else 0.0
    report = {
        "op": op,
        "volumes": [info["volume"] for info in infos],
        "volume": volume,
        "area": area,
        "triangles": len(t),
        "closed": True,
        "tolerance": tolerance,
        "origin": origin.tolist(),
        "empty": len(t) == 0,
        "flat": bool(len(t) > 0 and volume <= tolerance * area),
        "parts": part_count(t, len(v)),
        "flipped": [name for name, info in zip(names, infos, strict=True) if info["flipped"]],
    }
    tick_at(tick, 1.0, "Готово")
    return (*to_project(v, t, origin), report)


def boolean(
    a: Any, b: Any, op: str, *, names: Sequence[str] | None = None, tick: Tick = None
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Union, intersection or difference (a − b) of two closed solids; any orientation is accepted (a solid
    turned inside out is flipped and named in `report["flipped"]`). SolidError for an input that is not a
    closed manifold: the message names the solid and the defect, the hint — «Исправить каркас»."""
    v, t, report = boolean_many([a, b], op, names=list(names) if names else ["Тело A", "Тело B"], tick=tick)
    report["volume_a"], report["volume_b"] = report["volumes"]
    return v, t, report


def canonical(vertices: Any, triangles: Any) -> tuple[np.ndarray, np.ndarray]:
    """The canonical form of a mesh (ADR 0026 решение 14): the same bytes for the same surface whatever the order in
    which manifold3d (its threads) produced the vertices and the triangles. Vertices unused by a triangle are
    dropped and the rest sorted by (x, y, z) (−0.0 is 0.0); a triangle is turned cyclically to start with its
    smallest vertex number (the orientation is kept), the triangles are sorted by (a, b, c). → (float64 v×3, int32
    t×3). Vertices with equal coordinates keep their input order (a welded result — every boolean here — has none)."""
    v, t = mesh_arrays((vertices, triangles), "Результат")
    if len(t) == 0:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32)
    used = np.flatnonzero(np.bincount(t.reshape(-1), minlength=len(v)) > 0)
    pts = v[used] + 0.0
    order = np.lexsort((pts[:, 2], pts[:, 1], pts[:, 0]))  # stable
    rank = np.empty(len(v), dtype=np.int64)
    rank[used[order]] = np.arange(len(used))
    new = rank[t]
    first = np.argmin(new, axis=1)
    new = np.take_along_axis(new, (first[:, None] + np.arange(3)) % 3, axis=1)
    rows = np.lexsort((new[:, 2], new[:, 1], new[:, 0]))
    return np.ascontiguousarray(pts[order]), np.ascontiguousarray(new[rows], dtype=np.int32)


def expand(solid: Any, distance: float) -> None:
    """Shell (offset) of a solid: not done (ADR 0015 decision 8, ТЗ 7.23 «оболочки нет»). manifold3d offers
    only a Minkowski sum with a sphere, which decomposes a non-convex solid per triangle and leaves facets."""
    raise SolidError(
        "not_supported",
        "Расширение (оболочка) тела на заданное расстояние не выполняется.",
        "Постройте оболочку другими средствами: в этой версии её нет (ограничения 7.23).",
    )
