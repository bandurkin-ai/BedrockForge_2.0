"""«Упростить», «Сгладить», «В один объект», «На части» (ТЗ 7.23 `simplify`, `smooth`, `merge`, `split`;
ADR 0015 §7).

    simplify_mesh(xyz, tri, ...)     to a triangle count or to a largest deviation; the report of the change
    smooth_mesh(xyz, tri, ...)       Taubin λ/μ smoothing, the volume of a closed mesh kept
    merge_meshes([(xyz, tri), ...])  several meshes as one, nothing welded
    split_components(xyz, tri)       connected parts (shared vertices), the largest first

Everything works on float64 coordinates relative to a whole-metre origin at the centre of the extent and returns
project coordinates; a source vertex that reaches the result is bit for bit the source one (ADR 0015 §7): the
untouched vertices of smoothing are copied, the vertices of a simplified mesh that coincide with source ones
(to 1e-9 of the size of the mesh: the border pyfqmr keeps, the subset manifold3d keeps) get their bits back.

Simplification. To a count — quadric edge collapses (pyfqmr, free borders kept; a mesh above
`QUADRIC_WHOLE_LIMIT` triangles goes by parts along the Z-order curve, each keeping its border, joined back
exactly). To a tolerance — a bisection over the count of the same decimation, every candidate measured against
the source (`deviation.two_sided`: source vertices → result, result vertices and centroids → source; exact
point–triangle distances); a closed source must stay closed; for a closed source the result of manifold3d's
`simplify(tolerance)` competes too and the smaller mesh wins. If nothing passes, the source is returned as it is
(method "none"). pyfqmr always gets writable copies of its own (ТЗ 17 п. 6).

Smoothing. Every iteration moves vertices towards the mean of their neighbours (λ = `strength`) and then back
out (μ < −λ, pass band 0.1), which removes roughness without the shrinkage of plain Laplacian smoothing (G. Taubin,
«A signal processing approach to fair surface design», 1995). A closed mesh is finally scaled about the centroid
of its vertices so that its volume equals the source's (two passes: the second removes the rounding of the
first). Triangles are returned unchanged; with `keep_boundary` the vertices on free and non-manifold edges stay.

Ported from BF `geo/repair.py` (`simplify_mesh`, `_quadric`, `_manifold_simplify`, `smooth_mesh`,
`merge_meshes`, `split_components`). Differences: errors are `MeshError` {code, message, hint}; the bits of
surviving vertices are given back for both methods (BF: manifold3d only); the parts of a huge mesh are joined
back (BF left them unwelded); progress may raise (cancellation) between the steps of the search and the
iterations. numba, scipy, pyfqmr and manifold3d are imported on the first call.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np

from bf_core.geo.meshes import MeshError, check_mesh, edge_summary

QUADRIC_WHOLE_LIMIT = 4_000_000  # above this many triangles quadric decimation goes by spatial parts (memory)
QUADRIC_PART = 1_000_000  # triangles of one such part
TOLERANCE_SEARCH_STEPS = 8  # steps of the bisection over the triangle count when simplifying to a tolerance
TAUBIN_PASS_BAND = 0.1  # 1/λ + 1/μ = k
EXACT_REL = 1e-9  # a result vertex this close to a source one (of the size of the mesh) is that vertex
MAX_ITERATIONS = 10_000
SLICE = 1 << 20

Progress = Callable[[float], None] | None


def _arrays(xyz: Any, tri: Any) -> tuple[np.ndarray, np.ndarray]:
    t = np.asarray(tri)
    if t.size == 0:
        return np.ascontiguousarray(np.asarray(xyz, dtype=np.float64).reshape(-1, 3)), np.zeros((0, 3), np.int32)
    return check_mesh(xyz, t)


def _origin(v: np.ndarray) -> np.ndarray:
    """The centre of the extent rounded to a whole metre (exact differences for UTM-sized coordinates)."""
    if len(v) == 0:
        return np.zeros(3)
    return np.round((v.min(axis=0) + v.max(axis=0)) / 2.0) + 0.0  # type: ignore[no-any-return]


def _tick(progress: Progress, value: float) -> None:
    if progress:
        progress(float(min(max(value, 0.0), 1.0)))


def signed_volume(vc: np.ndarray, t: np.ndarray) -> float:
    """The signed volume of the tetrahedra from the origin of `vc` (by slices, summed with `math.fsum`)."""
    parts = []
    for start in range(0, len(t), SLICE):
        tri = t[start : start + SLICE]
        a, b, c = (vc[tri[:, k]] for k in range(3))
        parts.append(float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()))
    return math.fsum(parts) / 6.0


def is_closed(t: np.ndarray, n: int) -> bool:
    return len(t) > 0 and edge_summary(t, n) == (0, 0)


def compact(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    from bf_core.geo.repair import compact as drop_unused

    return drop_unused(v, t)


def exact_bits(out_vc: np.ndarray, source_vc: np.ndarray, source_v: np.ndarray, origin: np.ndarray) -> np.ndarray:
    """Project coordinates of result vertices; those within `EXACT_REL` of the size of the mesh of a source vertex
    are that vertex bit for bit."""
    from scipy.spatial import cKDTree

    out = out_vc + origin
    if len(out) == 0 or len(source_vc) == 0:
        return out
    dist, idx = cKDTree(source_vc).query(out_vc)
    exact = dist <= EXACT_REL * max(float(np.abs(source_vc).max()), 1.0)
    out[exact] = source_v[idx[exact]]
    return out


# ---------------------------------------------------------------- simplification


def _decimate(vc: np.ndarray, t: np.ndarray, target: int) -> tuple[np.ndarray, np.ndarray]:
    import pyfqmr

    s = pyfqmr.Simplify()
    # pyfqmr writes into the arrays it is given: copies of our own (ТЗ 17 п. 6 — the input may be a read-only map)
    s.setMesh(np.array(vc, dtype=np.float64, order="C", copy=True), np.array(t, dtype=np.int32, order="C", copy=True))
    s.simplify_mesh(target_count=max(int(target), 1), aggressiveness=7, preserve_border=True, verbose=False)
    out_v, out_t, _normals = s.getMesh()
    return np.asarray(out_v, dtype=np.float64).reshape(-1, 3), np.asarray(out_t, dtype=np.int32).reshape(-1, 3)


def _weld_exact(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vertices with the same bits joined (the borders of the parts of a huge mesh); the first one stays."""
    rows = np.ascontiguousarray(v).view(np.dtype((np.void, 24))).reshape(-1)
    _, first, inverse = np.unique(rows, return_index=True, return_inverse=True)
    order = np.argsort(first)
    rank = np.empty(len(order), dtype=np.int64)
    rank[order] = np.arange(len(order))
    return np.ascontiguousarray(v[np.sort(first)]), np.ascontiguousarray(rank[inverse.reshape(-1)][t], dtype=np.int32)


def quadric(vc: np.ndarray, t: np.ndarray, target: int, progress: Progress = None) -> tuple[np.ndarray, np.ndarray]:
    """Quadric edge-collapse decimation (pyfqmr) of a mesh (relative to its origin) to about `target` triangles."""
    m = len(t)
    if m > QUADRIC_WHOLE_LIMIT:
        from bf_core.geo.meshes import morton_order

        order = t[morton_order(vc, t)]
        out_v, out_t, base = [], [], 0
        starts = range(0, m, QUADRIC_PART)
        for k, start in enumerate(starts):
            tri = order[start : start + QUADRIC_PART]
            used, local = np.unique(tri, return_inverse=True)
            share = max(round(target * len(tri) / m), 4)
            pv, pt = _decimate(vc[used], local.reshape(-1, 3), share)
            out_v.append(pv)
            out_t.append(pt + base)
            base += len(pv)
            _tick(progress, (k + 1) / len(starts))
        v2, t2 = _weld_exact(np.concatenate(out_v), np.concatenate(out_t))
    else:
        v2, t2 = _decimate(vc, t, target)
        _tick(progress, 1.0)
    keep = (t2[:, 0] != t2[:, 1]) & (t2[:, 1] != t2[:, 2]) & (t2[:, 0] != t2[:, 2])
    return compact(v2, np.ascontiguousarray(t2[keep]))


def _manifold(vc: np.ndarray, t: np.ndarray, tolerance: float) -> tuple[np.ndarray, np.ndarray] | None:
    """manifold3d: collapses within the tolerance, keeps a subset of the vertices; None when the library does
    not take the mesh for a manifold."""
    import manifold3d

    try:
        mesh = manifold3d.Mesh64(
            vert_properties=np.array(vc, dtype=np.float64, order="C"), tri_verts=np.array(t, dtype=np.uint64)
        )
        solid = manifold3d.Manifold(mesh)
        if solid.status() != manifold3d.Error.NoError or solid.is_empty():
            return None
        out = solid.simplify(float(tolerance)).to_mesh64()
    except Exception:  # noqa: BLE001 — any refusal of the library means «use the other method»
        return None
    v2 = np.asarray(out.vert_properties, dtype=np.float64)[:, :3]
    t2 = np.asarray(out.tri_verts).astype(np.int32)
    if len(t2) == 0:
        return None
    return compact(np.ascontiguousarray(v2), np.ascontiguousarray(t2))


def simplify_mesh(
    xyz: Any,
    tri: Any,
    *,
    target_triangles: int | None = None,
    tolerance: float | None = None,
    threads: int = 1,
    progress: Progress = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Fewer triangles: down to `target_triangles`, or as few as keep the surface within `tolerance` metres of the
    source (with both, not fewer than `target_triangles`). The methods: module docstring.

    report: triangles_before/after, volume_before/after (closed meshes, else None), volume_change_percent,
    max_deviation (the sampled symmetric one, metres), closed_before/after, method ("quadric", "manifold",
    "none"). `MeshError` — neither or a wrong limit given, or not a mesh."""
    from bf_core.geo.deviation import Surface, two_sided

    if target_triangles is None and tolerance is None:
        raise MeshError(
            "bad_simplify", "Укажите число треугольников или допуск по отклонению.", "Задайте одно из двух значений."
        )
    if tolerance is not None and not tolerance > 0:
        raise MeshError(
            "bad_simplify", "Допуск по отклонению должен быть больше нуля.", "Укажите допуск в метрах, например 0,1."
        )
    if target_triangles is not None and target_triangles < 1:
        raise MeshError(
            "bad_simplify", "Число треугольников должно быть положительным.", "Укажите целое число больше нуля."
        )
    v, t = _arrays(xyz, tri)
    m = len(t)
    origin = _origin(v)
    vc = v - origin
    closed_before = is_closed(t, len(v))
    volume_before = abs(signed_volume(vc, t)) if closed_before else None
    best_v, best_t, method, deviation = vc, t, "none", 0.0
    steps = TOLERANCE_SEARCH_STEPS + 1

    if m and tolerance is None:
        assert target_triangles is not None
        if target_triangles < m:
            best_v, best_t = quadric(vc, t, target_triangles, lambda f: _tick(progress, 0.5 * f))
            method = "quadric"
            deviation = two_sided(Surface(vc, t), best_v, best_t, threads=threads)
    elif m:
        assert tolerance is not None
        source = Surface(vc, t)

        def passes(cv: np.ndarray, ct: np.ndarray) -> float | None:
            if len(ct) == 0 or (closed_before and not is_closed(ct, len(cv))):
                return None
            d = two_sided(source, cv, ct, threads=threads)
            return d if d <= tolerance else None

        if closed_before:
            got = _manifold(vc, t, tolerance)
            if got is not None and len(got[1]) < m:
                d = passes(*got)
                if d is not None:
                    best_v, best_t, method, deviation = got[0], got[1], "manifold", d
        _tick(progress, 1.0 / steps)
        # a bisection over the logarithm of the count: `high` is the smallest count known to pass
        low = float(max(target_triangles or 4, 4))
        high = float(len(best_t))
        for i in range(TOLERANCE_SEARCH_STEPS):
            if high <= low * 1.1:
                break
            count = round(math.sqrt(low * high))
            cv, ct = quadric(vc, t, count)
            d = passes(cv, ct) if len(ct) < len(best_t) else None
            if d is not None:
                best_v, best_t, method, deviation = cv, ct, "quadric", d
                high = float(min(count, len(ct)))
            else:
                low = float(count)
            _tick(progress, (i + 2) / steps)

    if method == "none":
        out_v, out_t = v, t
    else:
        used = np.unique(t)
        out_v = exact_bits(best_v, vc[used], v[used], origin)
        out_t = np.ascontiguousarray(best_t, dtype=np.int32)
    closed_after = is_closed(out_t, len(out_v))
    volume_after = abs(signed_volume(out_v - origin, out_t)) if closed_after else None
    change = None
    if volume_before and volume_after is not None:
        change = (volume_after - volume_before) / volume_before * 100.0
    _tick(progress, 1.0)
    return out_v, out_t, {
        "triangles_before": m, "triangles_after": int(len(out_t)),
        "volume_before": volume_before, "volume_after": volume_after, "volume_change_percent": change,
        "max_deviation": float(deviation), "closed_before": closed_before, "closed_after": closed_after,
        "method": method,
    }  # fmt: skip


# ---------------------------------------------------------------- smoothing


def smooth_mesh(
    xyz: Any,
    tri: Any,
    *,
    iterations: int = 10,
    strength: float = 0.5,
    keep_boundary: bool = True,
    threads: int = 1,
    progress: Progress = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Taubin λ/μ smoothing (module docstring). `strength` is λ (0…0.95), `iterations` — λ/μ pairs (0…10 000).

    report: volume_before/after (closed meshes, else None), volume_change_percent, max_deviation (source
    vertices → result and result vertices → source, metres), iterations, scale (of the volume correction)."""
    from scipy.sparse import coo_matrix, diags

    from bf_core.geo.deviation import Surface
    from bf_core.geo.repair import adjacency, kernels

    v, t = _arrays(xyz, tri)
    n, m = len(v), len(t)
    iterations = min(max(int(iterations), 0), MAX_ITERATIONS)
    lam = min(max(float(strength), 0.0), 0.95)
    origin = _origin(v)
    vc = v - origin
    closed = is_closed(t, n)
    volume_before = signed_volume(vc, t) if closed else None
    x = vc.copy()
    scale = 1.0
    movable = np.zeros(n, dtype=bool)
    if m and iterations and lam > 0:
        mu = 1.0 / (TAUBIN_PASS_BAND - 1.0 / lam)
        keys = np.unique(kernels().edge_keys(t, n))
        a, b = keys // n, keys % n
        ones = np.ones(len(a))
        graph = coo_matrix(
            (np.concatenate([ones, ones]), (np.concatenate([a, b]), np.concatenate([b, a]))), shape=(n, n)
        ).tocsr()
        degree = np.asarray(graph.sum(axis=1)).ravel()
        movable = degree > 0
        if keep_boundary:
            adj = adjacency(t, n)
            edge = np.flatnonzero(adj < 0)
            movable[t[edge // 3, edge % 3]] = False
            movable[t[edge // 3, (edge % 3 + 1) % 3]] = False
        weights = (diags(np.where(movable, 1.0 / np.maximum(degree, 1.0), 0.0)) @ graph).tocsr()
        step = movable.astype(np.float64)[:, None]
        for i in range(iterations):
            for factor in (lam, mu):
                x += factor * step * (weights @ x - x)
            _tick(progress, 0.8 * (i + 1) / iterations)
        if closed and volume_before:
            for _ in range(2):  # the second pass removes the rounding of the first
                now = signed_volume(x, t)
                if now * volume_before <= 0:
                    break
                k = (volume_before / now) ** (1.0 / 3.0)
                centroid = x[np.unique(t)].mean(axis=0)
                x = centroid + (x - centroid) * k
                scale *= k
            movable = degree > 0
    out = x + origin
    out[~movable] = v[~movable]  # vertices that did not move keep their bits
    volume_after = signed_volume(out - origin, t) if closed else None
    deviation = 0.0
    if m and movable.any():
        used = np.unique(t)
        tick = None if progress is None else (lambda f: _tick(progress, 0.8 + 0.2 * f))
        moved = out - origin
        deviation = max(
            float(Surface(moved, t).distances(vc[used], threads=threads).max()),
            float(Surface(vc, t).distances(moved[used], threads=threads, progress=tick).max()),
        )
    change = None
    if volume_before and volume_after is not None:
        change = (abs(volume_after) - abs(volume_before)) / abs(volume_before) * 100.0
    _tick(progress, 1.0)
    return out, t, {
        "volume_before": abs(volume_before) if volume_before is not None else None,
        "volume_after": abs(volume_after) if volume_after is not None else None,
        "volume_change_percent": change, "max_deviation": deviation, "iterations": iterations, "scale": float(scale),
    }  # fmt: skip


# ---------------------------------------------------------------- merge and split


def merge_meshes(meshes: list[tuple[Any, Any]]) -> tuple[np.ndarray, np.ndarray]:
    """Several meshes as one object: vertices concatenated (bit for bit), indices offset; nothing is welded."""
    verts, tris, base = [], [], 0
    for xyz, tri in meshes:
        v, t = _arrays(xyz, tri)
        verts.append(v)
        tris.append(t.astype(np.int64) + base)
        base += len(v)
    if not verts:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32)
    if base > np.iinfo(np.int32).max:
        raise MeshError(
            "too_many_vertices",
            "В объединённом каркасе больше вершин, чем хранит 32-битный номер вершины.",
            "Объедините меньше каркасов за раз.",
        )
    return np.ascontiguousarray(np.concatenate(verts)), np.ascontiguousarray(np.concatenate(tris), dtype=np.int32)


def split_components(xyz: Any, tri: Any) -> list[tuple[np.ndarray, np.ndarray]]:
    """Connected parts (triangles sharing a vertex are connected), the one with most triangles first (ties — the
    one with the lowest vertex first); each part gets its own vertices without unused ones, in the order of the
    input and bit for bit, and its triangles in the order of the input."""
    from bf_core.geo.repair import kernels

    v, t = _arrays(xyz, tri)
    if len(t) == 0:
        return []
    roots = kernels().vertex_roots(t, len(v))
    tri_root = roots[t[:, 0]]
    uniq, tri_label, counts = np.unique(tri_root, return_inverse=True, return_counts=True)
    if len(uniq) == 1:
        return [compact(v, t)]
    rank = np.empty(len(uniq), dtype=np.int64)
    rank[np.argsort(-counts, kind="stable")] = np.arange(len(uniq))
    tri_rank = rank[tri_label]
    used = np.zeros(len(v), dtype=bool)
    used[t.reshape(-1)] = True
    label_of_root = np.full(len(v), -1, dtype=np.int64)
    label_of_root[uniq] = rank
    vert_rank = label_of_root[roots]
    # vertices grouped by part (input order inside a part), triangles likewise
    vert_ids = np.flatnonzero(used)
    vert_ids = vert_ids[np.argsort(vert_rank[vert_ids], kind="stable")]
    vert_counts = np.bincount(vert_rank[vert_ids], minlength=len(uniq))
    vert_start = np.concatenate([[0], np.cumsum(vert_counts)])
    local = np.empty(len(v), dtype=np.int64)
    local[vert_ids] = np.arange(len(vert_ids)) - vert_start[vert_rank[vert_ids]]
    tri_order = np.argsort(tri_rank, kind="stable")
    tri_start = np.concatenate([[0], np.cumsum(np.bincount(tri_rank, minlength=len(uniq)))])
    local_t = local[t[tri_order]].astype(np.int32)
    return [
        (
            np.ascontiguousarray(v[vert_ids[vert_start[i] : vert_start[i + 1]]]),
            np.ascontiguousarray(local_t[tri_start[i] : tri_start[i + 1]]),
        )
        for i in range(len(uniq))
    ]
