"""«Исправить каркас», step `nonmanifold` (ADR 0015 §7): edges with three or more triangles.

The mesh is cut into patches — groups of triangles connected through edges with exactly two triangles. Patches
that meet on an edge with more than two triangles are removed greedily, dangling ones (with free edges) first,
then by increasing area, while every non-manifold edge of the patch keeps at least two triangles. A closed patch
that uses each of its non-manifold edges twice is a separate body touching another one along an edge: it is
never removed — its vertices on that edge are duplicated instead (the largest patch keeps the vertex, every
other one gets a copy appended to the vertices), so the bodies become separate. Anything else is left as it is
and reported (`remaining_halfedges`).

Ported from BF `geo/repair.py` (`_patches`, `_fix_nonmanifold`, `_greedy_patches`) without changes to the
algorithm. scipy and numba are imported on the first call.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from bf_core.geo.repair import centre_of, kernels


def triangle_areas(vc: np.ndarray, t: np.ndarray) -> np.ndarray:
    cross = np.cross(vc[t[:, 1]] - vc[t[:, 0]], vc[t[:, 2]] - vc[t[:, 0]])
    return 0.5 * np.sqrt(np.einsum("ij,ij->i", cross, cross))  # type: ignore[no-any-return]


def patches(t: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """(edge of every half-edge, uses of every edge, patch of every triangle, patches)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    m = len(t)
    keys = kernels().edge_keys(t, max(n, 1))
    order = np.argsort(keys)
    ks = keys[order]
    start = np.concatenate([[True], ks[1:] != ks[:-1]])
    del ks, keys
    edge_sorted = np.cumsum(start) - 1
    uses = np.bincount(edge_sorted)
    edge = np.empty(3 * m, dtype=np.int64)
    edge[order] = edge_sorted
    pos = np.flatnonzero(start)[uses == 2]
    a, b = order[pos] // 3, order[pos + 1] // 3
    graph = coo_matrix((np.ones(len(a), dtype=np.int8), (a, b)), shape=(m, m))
    count, patch = connected_components(graph, directed=False)
    return edge, uses, patch, int(count)


def fix_nonmanifold(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Removes fins and extra sheets, separates bodies touching along an edge (module docstring).

    Returns (vertices — the input ones and the copies after them, triangles, {removed_triangles, removed_area,
    duplicated_vertices})."""
    info: dict[str, Any] = {"removed_triangles": 0, "removed_area": 0.0, "duplicated_vertices": 0}
    n = len(v)
    vc = v - centre_of(v)
    edge, uses, patch, count = patches(t, n)
    if not np.any(uses > 2):
        return v, t, info
    half_uses = uses[edge]
    area = triangle_areas(vc, t)
    patch_area = np.bincount(patch, weights=area, minlength=count)
    free = np.bincount(patch[np.flatnonzero(half_uses == 1) // 3], minlength=count)
    nm = np.flatnonzero(half_uses > 2)
    inc_patch, inc_edge = patch[nm // 3], edge[nm]
    o = np.lexsort((inc_edge, inc_patch))
    inc_patch, inc_edge = inc_patch[o], inc_edge[o]
    cand, starts = np.unique(inc_patch, return_index=True)
    starts = np.append(starts, len(inc_patch)).astype(np.int64)
    # dangling patches first, then the smaller ones
    priority = np.lexsort((patch_area[cand], free[cand] == 0)).astype(np.int64)
    removed = kernels().greedy_patches(
        priority, starts, np.ascontiguousarray(inc_edge), uses.astype(np.int64), free[cand].astype(np.int64)
    )
    if removed.any():
        gone = np.zeros(count, dtype=bool)
        gone[cand[removed]] = True
        drop = gone[patch]
        info["removed_triangles"] = int(np.count_nonzero(drop))
        info["removed_area"] = float(area[drop].sum())
        t = np.ascontiguousarray(t[~drop])
        edge, uses, patch, count = patches(t, n)
        if not np.any(uses > 2):
            return v, t, info
        half_uses = uses[edge]
        patch_area = np.bincount(patch, weights=triangle_areas(vc, t), minlength=count)
        nm = np.flatnonzero(half_uses > 2)

    # what is left: patches using an edge exactly twice are bodies of their own touching there
    pair = edge[nm] * count + patch[nm // 3]
    _, inverse, mult = np.unique(pair, return_inverse=True, return_counts=True)
    own = nm[mult[inverse] == 2]
    if len(own) == 0:
        return v, t, info
    tri, k = own // 3, own % 3
    pv = np.concatenate([t[tri, k], t[tri, (k + 1) % 3]]).astype(np.int64)
    pp = np.concatenate([patch[tri], patch[tri]]).astype(np.int64)
    ukey = np.unique(pv * count + pp)
    uv, up = ukey // count, ukey % count
    # the largest patch at a vertex keeps it; every other one gets its own copy
    o = np.lexsort((up, -patch_area[up], uv))
    keeper = np.zeros(len(ukey), dtype=bool)
    keeper[o[np.concatenate([[True], uv[o][1:] != uv[o][:-1]])]] = True
    new_id = uv.copy()
    copies = np.flatnonzero(~keeper)
    new_id[copies] = n + np.arange(len(copies))
    corner_key = t.astype(np.int64) * count + patch[:, None]
    pos = np.clip(np.searchsorted(ukey, corner_key), 0, len(ukey) - 1)
    hit = ukey[pos] == corner_key
    t = np.array(t, dtype=np.int32, order="C")
    t[hit] = new_id[pos[hit]]
    info["duplicated_vertices"] = int(len(copies))
    return np.ascontiguousarray(np.concatenate([v, v[uv[copies]]])), t, info
