"""Triangle meshes: spatial order, parts, levels of detail, measures (ADR 0004 §2–3, ADR 0011 §1, §3).

Ported from BF `geo/meshes.py` (`morton_order`, `chunk_table`, `simplify`, `lod_targets`) and
`formats/wireframes.py` (`mesh_stats`, `edge_summary`). Pure geometry: no database, no files.

    check_mesh(xyz, tri)        float64 n×3 and int32 m×3, or MeshError {code, message, hint}
    morton_order(v, t)          triangles along a Z-order curve of their centroids
    parts_table(v, t)           runs of at most 64 000 consecutive triangles, none wider than 16 km
    part_mesh(v, t, part)       the vertices of a part from its origin (exact float64) and its triangles
    levels(local, tri)          the part at 1, 1/8 and 1/64 of its triangles (pyfqmr, the border kept)
    mesh_stats(v, t)            area, volume of a closed solid, open and non-manifold edges

Differences from BF. The Morton code uses one scale for the three axes (BF scaled every axis to its own span,
which cut a flat terrain into elevation bands). Levels of detail are made per part, not per mesh: every part
keeps the vertices of its border bit for bit at every level, so neighbours drawn at different levels still
join without cracks (ADR 0004 §3 «на каждую часть 3 уровня»).

The exact float64 geometry is never replaced by a simplified one: levels of detail are only drawn.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

PART_TRIANGLES = 64_000
MAX_SPAN_M = 16_000.0  # float32 offsets from the origin of a part stay below 8000.5 m (ADR 0004 §2)
LOD_RATIO = 8  # every level keeps this share of the one before
LOD_LEVELS = 3  # 1, 1/8, 1/64
LOD_MIN_TRIANGLES = 64  # a part with fewer triangles is drawn as it is at every level
SLICE = 1 << 20  # triangles handled at once: bounds the temporary memory (≈ 200 MB)
_BITS = 21

Progress = Callable[[float], None] | None


class MeshError(ValueError):
    """A mesh that cannot be stored or shown: {code, message, hint} in Russian."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def check_mesh(xyz: Any, tri: Any) -> tuple[np.ndarray, np.ndarray]:
    """Vertices float64 n×3 (C order) and triangles int32 m×3 of a mesh that can be stored."""
    v = np.asarray(xyz)
    t = np.asarray(tri)
    if v.ndim != 2 or v.shape[1] != 3 or t.ndim != 2 or t.shape[1] != 3 or t.dtype.kind not in "iu":
        raise MeshError(
            "bad_mesh",
            "Неверная структура каркаса: нужны вершины (n × 3) и треугольники (m × 3).",
            "Проверьте исходный файл каркаса.",
        )
    if len(t) == 0:
        raise MeshError("empty_mesh", "В каркасе нет треугольников.", "Проверьте исходные данные каркаса.")
    if len(v) > np.iinfo(np.int32).max:
        raise MeshError(
            "too_many_vertices",
            f"В каркасе {_n(len(v))} вершин — больше, чем хранит 32-битный номер вершины.",
            "Разделите каркас на несколько объектов.",
        )
    if int(t.min()) < 0 or int(t.max()) >= len(v):
        raise MeshError(
            "bad_mesh",
            "Номера вершин треугольников выходят за пределы списка вершин.",
            "Проверьте исходный файл каркаса.",
        )
    v = np.ascontiguousarray(v, dtype=np.float64)
    bad = 0
    for start in range(0, len(v), 3 * SLICE):
        bad += int(np.count_nonzero(~np.isfinite(v[start : start + 3 * SLICE]).all(axis=1)))
    if bad:
        raise MeshError(
            "bad_coordinates",
            f"Координаты вершин каркаса содержат пустые или бесконечные значения: {_n(bad)} шт.",
            "Исправьте исходные данные: у каждой вершины каркаса должны быть X, Y и Z.",
        )
    return v, np.ascontiguousarray(t, dtype=np.int32)


# --- spatial order ---------------------------------------------------------------------------------------


def _spread3(x: np.ndarray) -> np.ndarray:
    """The low 21 bits of x with two zero bits after every bit (for Morton codes)."""
    x = x.astype(np.uint64) & np.uint64(0x1FFFFF)
    x = (x | (x << np.uint64(32))) & np.uint64(0x1F00000000FFFF)
    x = (x | (x << np.uint64(16))) & np.uint64(0x1F0000FF0000FF)
    x = (x | (x << np.uint64(8))) & np.uint64(0x100F00F00F00F00F)
    x = (x | (x << np.uint64(4))) & np.uint64(0x10C30C30C30C30C3)
    x = (x | (x << np.uint64(2))) & np.uint64(0x1249249249249249)
    return x


def morton_order(v: np.ndarray, t: np.ndarray, progress: Progress = None) -> np.ndarray:
    """The permutation of the triangles along a Z-order curve of their centroids: neighbours in space are
    neighbours in the array, so consecutive triangles form compact parts. Ties keep the order given."""
    m = len(t)
    if m == 0:
        return np.zeros(0, dtype=np.int64)
    lo = np.min(v, axis=0)
    span = float(np.max(np.max(v, axis=0) - lo))
    scale = ((1 << _BITS) - 1) / max(span, 1e-9)  # one scale for X, Y and Z: cells are cubes
    top = (1 << _BITS) - 1
    codes = np.empty(m, dtype=np.uint64)
    for start in range(0, m, SLICE):
        tri = np.asarray(t[start : start + SLICE])
        c = (v[tri[:, 0]] + v[tri[:, 1]] + v[tri[:, 2]]) / 3.0
        q = np.clip(((c - lo) * scale).astype(np.int64), 0, top)
        codes[start : start + len(tri)] = (
            _spread3(q[:, 0]) | (_spread3(q[:, 1]) << np.uint64(1)) | (_spread3(q[:, 2]) << np.uint64(2))
        )
        if progress:
            progress(min(1.0, (start + len(tri)) / m))
    return np.argsort(codes, kind="stable")


# --- parts -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MeshPart:
    k: int
    first: int  # triangles first : first + count of the mesh as stored
    count: int
    bbox: tuple[float, float, float, float, float, float]  # xmin, ymin, zmin, xmax, ymax, zmax
    origin: tuple[float, float, float]  # the centre of the extent rounded to a whole metre


def origin_of(box: tuple[float, ...]) -> tuple[float, float, float]:
    c = np.round((np.array(box[:3]) + np.array(box[3:])) * 0.5) + 0.0  # -0.0 → 0.0
    return float(c[0]), float(c[1]), float(c[2])


def _box(v: np.ndarray, tri: np.ndarray) -> tuple[float, float, float, float, float, float]:
    p = v[np.asarray(tri).reshape(-1)]
    lo, hi = p.min(axis=0), p.max(axis=0)
    return float(lo[0]), float(lo[1]), float(lo[2]), float(hi[0]), float(hi[1]), float(hi[2])


def make_part(k: int, first: int, count: int, box: Any) -> MeshPart:
    b = tuple(float(x) for x in box)
    return MeshPart(k, int(first), int(count), (b[0], b[1], b[2], b[3], b[4], b[5]), origin_of(b))


def parts_table(
    v: np.ndarray,
    t: np.ndarray,
    *,
    max_triangles: int = PART_TRIANGLES,
    max_span: float = MAX_SPAN_M,
    progress: Progress = None,
) -> list[MeshPart]:
    """Parts of the triangles in the order given (see `morton_order`): runs of at most `max_triangles`; a run
    wider than `max_span` along any axis is halved until it fits or holds one triangle."""
    if max_triangles < 1 or not max_span > 0:
        raise ValueError("meshes: a part holds at least one triangle and has a positive span")
    m = len(t)
    stack = [(a, min(a + max_triangles, m)) for a in range(0, m, max_triangles)][::-1]
    out: list[MeshPart] = []
    while stack:
        a, b = stack.pop()
        box = _box(v, t[a:b])
        if b - a > 1 and max(box[3] - box[0], box[4] - box[1], box[5] - box[2]) > max_span:
            mid = (a + b) // 2
            stack.extend(((mid, b), (a, mid)))  # the lower half first: the order is kept
            continue
        out.append(make_part(len(out), a, b - a, box))
        if progress:
            progress(min(1.0, b / m))
    return out


def extent_of(parts: list[MeshPart]) -> list[float] | None:
    """[xmin, ymin, zmin, xmax, ymax, zmax] of the triangles of all the parts."""
    if not parts:
        return None
    box = np.array([p.bbox for p in parts])
    return [*box[:, :3].min(axis=0).tolist(), *box[:, 3:].max(axis=0).tolist()]


def part_mesh(v: np.ndarray, t: np.ndarray, part: MeshPart) -> tuple[np.ndarray, np.ndarray]:
    """The vertices a part uses, from its origin (float64 k×3; the difference from a whole-metre origin is
    exact), in the order of the mesh, and its triangles over them (int32 n×3)."""
    tri = np.asarray(t[part.first : part.first + part.count])
    used, inverse = np.unique(tri, return_inverse=True)
    local = np.asarray(v[used], dtype=np.float64) - np.array(part.origin)
    return local, np.ascontiguousarray(inverse.reshape(-1, 3), dtype=np.int32)


# --- levels of detail ------------------------------------------------------------------------------------


def simplify(local: np.ndarray, tri: np.ndarray, target: int) -> tuple[np.ndarray, np.ndarray]:
    """Quadric edge-collapse decimation to about `target` triangles (pyfqmr). The vertices on the border of
    the mesh stay where they are, bit for bit, so the count cannot go below what the border needs. A mesh that
    is small already, or that pyfqmr cannot make smaller, comes back as it is (the same arrays)."""
    if len(tri) < LOD_MIN_TRIANGLES or len(tri) <= max(int(target), 1):
        return local, tri
    import pyfqmr

    s = pyfqmr.Simplify()
    # pyfqmr needs writable arrays of its own (ТЗ 17.3.6): the input may be a read-only view of a file
    s.setMesh(np.array(local, dtype=np.float64, order="C"), np.array(tri, dtype=np.int32, order="C"))
    s.simplify_mesh(target_count=max(int(target), 1), aggressiveness=7, preserve_border=True, verbose=False)
    out_v, out_t, _normals = s.getMesh()
    v = np.ascontiguousarray(out_v, dtype=np.float64).reshape(-1, 3)
    t = np.ascontiguousarray(out_t, dtype=np.int32).reshape(-1, 3)
    if len(t) == 0 or len(t) >= len(tri) or not np.isfinite(v).all() or int(t.max()) >= len(v) or int(t.min()) < 0:
        return local, tri
    return v, t


def levels(local: np.ndarray, tri: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """A part at every level of detail: [0] — as given, [i] — about 1/8ⁱ of its triangles, made from the level
    before. A level that could not be made smaller is the level before it (the same arrays). A new vertex
    that the decimation put outside the extent of the part (a quadric overshoots on a curved surface) is
    brought back to it, so the box of a part holds the part at every level."""
    out = [(local, tri)]
    lo, hi = (local.min(axis=0), local.max(axis=0)) if len(local) else (None, None)
    for _ in range(1, LOD_LEVELS):
        v, t = out[-1]
        sv, st = simplify(v, t, len(t) // LOD_RATIO)
        if sv is not v:
            np.clip(sv, lo, hi, out=sv)
        out.append((sv, st))
    return out


# --- measures --------------------------------------------------------------------------------------------


def edge_summary(t: np.ndarray, vertex_count: int) -> tuple[int, int]:
    """(open edges, non-manifold edges): edges used by one triangle, and by more than two.
    One int64 key per edge (both directions alike), sorted: about 11 bytes of memory per edge."""
    m = len(t)
    n = max(int(vertex_count), 1)
    keys = np.empty(3 * m, dtype=np.int64)
    for k, (i, j) in enumerate(((0, 1), (1, 2), (2, 0))):
        a = t[:, i].astype(np.int64)
        b = t[:, j].astype(np.int64)
        seg = keys[k * m : (k + 1) * m]
        np.minimum(a, b, out=seg)
        seg *= n
        seg += np.maximum(a, b)
    keys.sort()
    same_next = np.zeros(len(keys), dtype=bool)
    np.equal(keys[1:], keys[:-1], out=same_next[:-1])
    same_prev = np.zeros(len(keys), dtype=bool)
    same_prev[1:] = same_next[:-1]
    open_edges = int(np.count_nonzero(~same_prev & ~same_next))
    # a run of three or more equal keys: its first element has equal keys one and two places ahead
    same_next2 = np.zeros(len(keys), dtype=bool)
    if len(keys) > 2:
        np.equal(keys[2:], keys[:-2], out=same_next2[:-2])
    return open_edges, int(np.count_nonzero(~same_prev & same_next2))


def mesh_stats(v: np.ndarray, t: np.ndarray, progress: Progress = None) -> dict[str, Any]:
    """Area, plan area, volume (of a closed solid), closedness, orientation, degenerate triangles.

    Volume: signed tetrahedra from the centre of the extent (keeps the precision of UTM coordinates), summed
    per slice with the pairwise sum of numpy and across slices with `math.fsum`. A missing volume is None."""
    m = len(t)
    if m == 0:
        return {"area": 0.0, "plan_area": 0.0, "volume": None, "closed": False, "degenerate": 0, "open_edges": 0}
    o = (np.min(v, axis=0) + np.max(v, axis=0)) / 2.0
    areas, plans, volumes, degenerate = [], [], [], 0
    for start in range(0, m, SLICE):
        tri = np.asarray(t[start : start + SLICE])
        a, b, c = (v[tri[:, i]] - o for i in range(3))
        cross = np.cross(b - a, c - a)
        area = 0.5 * np.sqrt(np.einsum("ij,ij->i", cross, cross))
        degenerate += int(np.count_nonzero(area < 1e-9))
        areas.append(float(area.sum()))
        plans.append(float(np.abs(cross[:, 2]).sum()) * 0.5)
        volumes.append(float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()))
        if progress:
            progress(min(1.0, 0.7 * (start + len(tri)) / m))
    open_edges, nonmanifold = edge_summary(t, len(v))
    closed = open_edges == 0 and nonmanifold == 0
    volume = orientation = None
    if closed:
        signed = math.fsum(volumes) / 6.0
        volume = abs(signed)
        orientation = "outward" if signed > 0 else "inward"
    if progress:
        progress(1.0)
    return {
        "area": math.fsum(areas),
        "plan_area": math.fsum(plans),
        "volume": volume,
        "closed": closed,
        "orientation": orientation,
        "degenerate": degenerate,
        "open_edges": open_edges,
        "nonmanifold_edges": nonmanifold,
    }
