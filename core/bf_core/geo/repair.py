"""«Исправить каркас» (ТЗ 7.23 `repair`; ADR 0015 §7): the fixes of a triangle mesh and the report «было → стало».

    repair(xyz, tri, ...)       weld · duplicate · degenerate · nonmanifold · holes · orient;
                                (vertices, triangles, report «было → стало»)
    repair_checks(xyz, tri)     every check of a wireframe in one dictionary (`checks.mesh_checks` + repeated
                                triangles + connected parts): the «было» and «стало» of the report

Everything works on float64 coordinates relative to the centre of the extent and returns the vertices of the
input: a vertex that survives is bit for bit the input one, the survivors keep the order of the input, vertices
no triangle uses are dropped. Heavy passes are numba kernels (`repair_kernels.py`) or whole-array numpy.

The steps, in the order they run (each is switched on separately):

    weld         vertices on the same point of a grid of the tolerance, then neighbours closer than the
                 tolerance (KD-tree over one vertex of every grid point) form a cluster; its first vertex (in
                 the order of the input) represents it. 0 — only exactly equal vertices; a tolerance below what
                 the coordinates can tell apart is taken as 0 on the grid (the KD-tree still uses it).
    duplicate    triangles with the same three vertices (any rotation or winding): the first stays.
    degenerate   a repeated corner → dropped. Zero area with three distinct corners (a vertex on the opposite
                 edge) → the longest edge is flipped with the neighbour across it, which removes the triangle
                 without opening the surface and without changing the volume; where no flip is possible (a free
                 or non-manifold edge, the new edge exists already) the triangle is dropped.
    nonmanifold  edges with three or more triangles: fins and extra sheets are removed, bodies touching along an
                 edge are separated (`repair_nonmanifold.py`).
    holes        boundary loops closed without new vertices; the outer boundary of an open surface stays open
                 (`repair_holes.py`). Patches over collinear vertices are flipped, never dropped.
    orient       windings are spread over manifold edges; closed parts are turned outward (positive signed
                 volume), open parts keep the winding most of their triangles had.

Self-intersections are counted in the report, never fixed (ТЗ 7.23).

Ported from BF `geo/repair.py` (`repair`, `_weld`, `_fix_degenerate`, the duplicates, `mesh_checks`).
Differences: the checks of the report are `checks.mesh_checks` of Э8 (the self-intersections of ADR 0013) plus
the two counts BF had there; `weld_on`; a negative tolerance is a `MeshError`; the representative of a grid
point is read from the stable sort, not looked for; `progress(fraction)` may raise (cancellation) and is called
between the steps and inside the long ones. numba and scipy are imported on the first call.

Known limits (as in BF): every closed part is turned outward on its own, so an inner shell meant as a cavity
becomes a body; vertices where parts touch in a point are not a defect here and stay.
"""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import Any

import numpy as np

from bf_core.geo.checks import mesh_checks
from bf_core.geo.meshes import MeshError, check_mesh

DEGENERATE_AREA = 1e-9  # m²: below it a triangle is degenerate (as `meshes.mesh_stats`)
FLIP_PASSES = 5  # passes of edge flips over zero-area triangles (a flip may leave another sliver behind)
GRID_STEPS = 2.0**50  # a grid of the weld tolerance finer than this many steps over the extent is not built

TITLES = {
    "weld": "Сшивка совпадающих вершин",
    "duplicate": "Удаление повторных треугольников",
    "degenerate": "Удаление вырожденных треугольников",
    "nonmanifold": "Устранение неманифолдных рёбер",
    "holes": "Закрытие отверстий",
    "orient": "Согласование нормалей",
}
NOTE_NONMANIFOLD = (
    "Часть неманифолдных рёбер оставлена: поверхности сходятся на ребре так, что лишнюю нельзя выбрать "
    "однозначно — исправьте вручную"
)

Progress = Callable[[float], None] | None


def kernels() -> ModuleType:
    from bf_core.geo import repair_kernels

    return repair_kernels


# ---------------------------------------------------------------- small helpers


def centre_of(v: np.ndarray) -> np.ndarray:
    if len(v) == 0:
        return np.zeros(3)
    return (v.min(axis=0) + v.max(axis=0)) / 2.0  # type: ignore[no-any-return]


def compact(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Drops vertices no triangle uses; the order (and the bits) of the others is kept."""
    used = np.zeros(len(v), dtype=bool)
    used[t.reshape(-1)] = True
    if used.all():
        return v, t
    remap = np.cumsum(used, dtype=np.int64) - 1
    return np.ascontiguousarray(v[used]), np.ascontiguousarray(remap[t], dtype=np.int32)


def adjacency(t: np.ndarray, n: int) -> np.ndarray:
    """The twin of every half-edge (`repair_kernels.link`)."""
    k = kernels()
    keys = k.edge_keys(t, max(n, 1))
    return k.link(keys, np.argsort(keys))  # type: ignore[no-any-return]


def duplicate_mask(t: np.ndarray) -> np.ndarray:
    if len(t) < 2:
        return np.zeros(len(t), dtype=bool)
    k = kernels()
    hashes = k.triangle_hashes(t)
    return k.mark_duplicates(t, hashes, np.argsort(hashes, kind="stable"))  # type: ignore[no-any-return]


def component_count(t: np.ndarray, n: int) -> int:
    """Connected parts: triangles sharing a vertex are connected."""
    if len(t) == 0:
        return 0
    roots = kernels().vertex_roots(t, n)
    return int(len(np.unique(roots[t[:, 0]])))


def _within(progress: Progress, start: float, stop: float) -> Progress:
    if progress is None:
        return None
    return lambda fraction: progress(start + (stop - start) * min(max(float(fraction), 0.0), 1.0))


def repair_checks(xyz: Any, tri: Any, *, intersections: bool = True, progress: Progress = None) -> dict[str, Any]:
    """`checks.mesh_checks` (triangles, vertices, area, plan_area, volume and orientation of a closed solid,
    closed, open_edges, nonmanifold_edges, degenerate, orientation_conflicts, self_intersections) and two more
    counts: `duplicate` — triangles repeating an earlier one (any rotation or winding), `components` — parts
    connected through shared vertices."""
    out = mesh_checks(xyz, tri, intersections=intersections, progress=progress)
    t = np.asarray(tri)
    if len(t) == 0:
        return {**out, "duplicate": 0, "components": 0}
    v, t = check_mesh(xyz, t)
    out["duplicate"] = int(np.count_nonzero(duplicate_mask(t)))
    out["components"] = component_count(t, len(v))
    return out


# ---------------------------------------------------------------- steps


def weld_vertices(v: np.ndarray, t: np.ndarray, tolerance: float, progress: Progress = None) -> tuple[np.ndarray, int]:
    """Triangles re-indexed to the first vertex of every cluster of coincident vertices (module docstring).

    Returns (triangles, merged vertices): how many used vertices are no longer used. The vertices themselves
    are not touched (`compact` drops the unused ones)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    n = len(v)
    vc = v - centre_of(v)
    span = float(np.abs(vc).max()) if n else 0.0
    on_grid = tolerance > 0 and span / tolerance < GRID_STEPS
    key = np.round(vc / tolerance).astype(np.int64) if on_grid else vc
    order = np.lexsort((key[:, 2], key[:, 1], key[:, 0]))  # stable: equal keys keep the order of the input
    ks = key[order]
    starts = np.empty(n, dtype=bool)
    starts[0] = True
    np.any(ks[1:] != ks[:-1], axis=1, out=starts[1:])
    del ks, key
    group = np.cumsum(starts) - 1
    first = order[starts]  # the lowest vertex of every grid point
    of_vertex = np.empty(n, dtype=np.int64)
    of_vertex[order] = group
    groups = len(first)
    del order, group, starts
    if progress:
        progress(0.4)
    if tolerance > 0 and groups > 1:
        # neighbours on two sides of a grid line: clusters of points closer than the tolerance
        tree = cKDTree(vc[first], balanced_tree=False, compact_nodes=False)
        pairs = tree.query_pairs(tolerance, output_type="ndarray")
        del tree
        if progress:
            progress(0.8)
        if len(pairs):
            graph = coo_matrix((np.ones(len(pairs), dtype=np.int8), (pairs[:, 0], pairs[:, 1])), shape=(groups, groups))
            clusters, label = connected_components(graph, directed=False)
            lowest = np.full(clusters, n, dtype=np.int64)
            np.minimum.at(lowest, label, first)
            first = lowest[label]
    rep = first[of_vertex]
    used_before = np.zeros(n, dtype=bool)
    used_before[t.reshape(-1)] = True
    welded = np.ascontiguousarray(rep[t], dtype=np.int32)
    used_after = np.zeros(n, dtype=bool)
    used_after[welded.reshape(-1)] = True
    if progress:
        progress(1.0)
    return welded, int(np.count_nonzero(used_before) - np.count_nonzero(used_after))


def fix_degenerate(
    vc: np.ndarray, t: np.ndarray, n: int, drop: bool = True, progress: Progress = None
) -> tuple[np.ndarray, int, int]:
    """(triangles, dropped, flipped): see the module docstring. With drop=False only flips are made (a patch
    over collinear vertices of a hole must not be dropped: that would open it again)."""
    k = kernels()
    kind = k.degenerate_kinds(vc, t, DEGENERATE_AREA)
    dropped = flipped = 0
    if drop and np.any(kind == 1):
        keep = kind != 1
        dropped += int(len(t) - np.count_nonzero(keep))
        t, kind = t[keep], kind[keep]
    t = np.array(t, dtype=np.int32, order="C")
    for p in range(FLIP_PASSES):
        bad = np.flatnonzero(kind == 2)
        if len(bad) == 0:
            break
        keys = k.edge_keys(t, max(n, 1))
        order = np.argsort(keys)
        adj = k.link(keys, order)
        plan = k.plan_flips(vc, t, adj, bad, kind, np.zeros(len(t), dtype=np.bool_))
        can = plan[:, 1] >= 0
        # a flip must not create an edge the mesh has already (it would become non-manifold)
        c, d = np.minimum(plan[:, 2], plan[:, 3]), np.maximum(plan[:, 2], plan[:, 3])
        new_keys = c * max(n, 1) + d
        sorted_keys = keys[order]
        pos = np.clip(np.searchsorted(sorted_keys, new_keys), 0, len(sorted_keys) - 1)
        exists = sorted_keys[pos] == new_keys
        approved = can & ~exists
        k.apply_flips(t, plan, approved)
        flipped += int(np.count_nonzero(approved))
        gone = (plan[:, 1] == -1) | (can & exists)
        progressed = bool(approved.any())
        if drop and gone.any():
            keep = np.ones(len(t), dtype=bool)
            keep[plan[gone, 0]] = False
            dropped += int(np.count_nonzero(gone))
            t = np.ascontiguousarray(t[keep])
            progressed = True
        if progress:
            progress((p + 1) / FLIP_PASSES)
        if not progressed:
            break
        kind = k.degenerate_kinds(vc, t, DEGENERATE_AREA)
    if drop:
        left = kind == 2  # what the flips could not resolve (chains of slivers) is dropped after all
        if left.any():
            dropped += int(np.count_nonzero(left))
            t = t[~left]
    return np.ascontiguousarray(t, dtype=np.int32), dropped, flipped


def orient_parts(
    vc: np.ndarray, t: np.ndarray, adj: np.ndarray, flip: np.ndarray, comp: np.ndarray, parts: int, originals: int
) -> tuple[np.ndarray, dict[str, Any]]:
    """After `propagate` + `flip_linked`: closed parts outward, open parts as most of their source triangles
    (those below `originals`) were. `t` is changed in place; returns (flipped per source triangle, counts)."""
    k = kernels()
    flipped = np.asarray(flip[:originals], dtype=np.bool_)
    vol, free, nflip, count = k.part_stats(vc, t, adj, comp, parts, flipped, originals)
    turn = np.where(free == 0, vol < 0, 2 * nflip > count)
    if turn.any():
        whole = turn[comp]
        k.flip_triangles(t, whole)
        flipped = flipped ^ whole[:originals]
    closed = free == 0
    info = {
        "parts": int(parts),
        "closed_parts": int(np.count_nonzero(closed)),
        "turned_outward": int(np.count_nonzero(turn & closed)),
    }
    return flipped, info


# ---------------------------------------------------------------- the whole repair


def repair(
    xyz: Any,
    tri: Any,
    *,
    weld: float | None = 1e-6,
    weld_on: bool = True,
    drop_duplicate: bool = True,
    drop_degenerate: bool = True,
    fix_nonmanifold: bool = True,
    fill_holes: bool = True,
    orient: bool = True,
    max_hole_edges: int | None = None,
    keep_outer_boundary: bool = True,
    intersections: bool = True,
    progress: Progress = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Repairs a wireframe; every fix is switched on separately (the algorithms: module docstring).

    Returns (vertices float64 n×3, triangles int32 m×3, report): report["before"] / report["after"] are
    `repair_checks`, report["steps"] lists what every step that ran changed (`step`, `changed`, a Russian
    `title`, the counts of the step and a `note` where something was left alone on purpose),
    report["tolerance"] — the weld tolerance or None. The inputs are never changed (a read-only memory map
    will do). `weld` — the tolerance in metres (0 — exactly equal vertices only; None or `weld_on=False` — no
    welding); `max_hole_edges` leaves longer boundary loops open; `keep_outer_boundary=False` closes even a loop
    spanning its whole part (by default the outer boundary of an open surface); `intersections=False` skips the
    slow count of self-intersections in both checks. Surviving vertices come first, in the order of the input;
    the copies made by `nonmanifold` and the centroids of `holes` (loops too long to close otherwise) follow
    them. `MeshError` —
    the mesh is not one (`meshes.check_mesh`) or the tolerance is negative; `progress(fraction)` may raise."""
    if weld is not None and not (float(weld) >= 0.0 and np.isfinite(weld)):
        raise MeshError(
            "bad_tolerance",
            "Допуск сшивки вершин должен быть неотрицательным числом.",
            "Укажите допуск в метрах, например 0,000001, или выключите сшивку вершин.",
        )
    tolerance = float(weld) if weld is not None and weld_on else None
    share = 0.25 if intersections else 0.05  # of the progress: each of the two checks
    tick = _within(progress, 0.0, 1.0) or (lambda fraction: None)
    t0 = np.asarray(tri)
    if len(t0) == 0:
        before = repair_checks(xyz, t0, intersections=intersections)
        tick(1.0)
        return (
            np.ascontiguousarray(np.asarray(xyz, dtype=np.float64).reshape(-1, 3)),
            t0.astype(np.int32).reshape(-1, 3),
            {"before": before, "after": before, "steps": [], "tolerance": tolerance},
        )
    v, t = check_mesh(xyz, t0)
    before = repair_checks(v, t, intersections=intersections, progress=_within(progress, 0.0, share))
    t = np.array(t, dtype=np.int32, order="C")  # a copy of our own: the kernels write into it
    steps: list[dict[str, Any]] = []
    plan = [tolerance is not None, drop_duplicate, drop_degenerate, fix_nonmanifold, fill_holes, orient]
    weight = (1.0 - 2.0 * share) / max(sum(plan), 1)
    done = [share]

    def part(name: str) -> Progress:
        start = done[0]
        done[0] += weight
        return _within(progress, start, start + weight)

    def record(name: str, changed: int, **extra: Any) -> None:
        steps.append({"step": name, "title": TITLES[name], "changed": int(changed), **extra})
        tick(done[0])

    if tolerance is not None:
        t, merged = weld_vertices(v, t, tolerance, part("weld"))
        record("weld", merged)
    v, t = compact(v, t)
    vc = np.ascontiguousarray(v - centre_of(v))

    if drop_duplicate:
        part("duplicate")
        dup = duplicate_mask(t)
        if dup.any():
            t = np.ascontiguousarray(t[~dup])
        record("duplicate", int(np.count_nonzero(dup)))

    if drop_degenerate:
        t, dropped, flips = fix_degenerate(vc, t, len(v), progress=part("degenerate"))
        record("degenerate", dropped + flips, dropped=dropped, flipped=flips)

    k = kernels()
    adj = adjacency(t, len(v)) if (fix_nonmanifold or fill_holes or orient) and len(t) else None
    if fix_nonmanifold:
        from bf_core.geo.repair_nonmanifold import fix_nonmanifold as separate

        part("nonmanifold")
        found: dict[str, Any] = {"removed_triangles": 0, "removed_area": 0.0, "duplicated_vertices": 0}
        if adj is not None and np.any(adj == -2):
            v, t, found = separate(v, t)
            vc = np.ascontiguousarray(v - centre_of(v))
            adj = adjacency(t, len(v))
        remaining = int(np.count_nonzero(adj == -2)) if adj is not None else 0
        extra = dict(found, remaining_halfedges=remaining)
        if remaining:
            extra["note"] = NOTE_NONMANIFOLD
        record("nonmanifold", found["removed_triangles"] + found["duplicated_vertices"], **extra)

    originals = len(t)
    flipped = np.zeros(originals, dtype=np.bool_)
    comp = np.zeros(0, dtype=np.int32)
    parts = conflicts = 0
    if (orient or fill_holes) and adj is not None:
        flip, comp, parts, conflicts = k.propagate(t, adj)
        if orient:
            k.flip_linked(t, adj, flip)
            flipped = flip

    if fill_holes:
        from bf_core.geo.repair_holes import fill_holes as close

        holes = part("holes")
        count = len(t)
        filled: dict[str, Any] = {
            "filled": 0, "skipped_outer": 0, "skipped_large": 0, "skipped_complex": 0, "added_vertices": 0,
        }  # fmt: skip
        if adj is not None:
            v, t, filled = close(v, vc, t, adj, comp, int(parts), max_hole_edges, keep_outer_boundary, holes)
            vc = np.ascontiguousarray(v - centre_of(v))
        added = len(t) - count
        if added and drop_degenerate:
            t, _, _ = fix_degenerate(vc, t, len(v), drop=False)
        notes = []
        if filled["skipped_outer"]:
            notes.append(
                f"Наружная граница незамкнутой поверхности оставлена открытой (контуров: {filled['skipped_outer']})"
            )
        if filled["skipped_large"]:
            notes.append(f"Пропущены отверстия длиннее {max_hole_edges} рёбер (контуров: {filled['skipped_large']})")
        if filled["skipped_complex"]:
            notes.append(
                f"Не закрыты контуры, проходящие через неманифолдные рёбра (контуров: {filled['skipped_complex']})"
            )
        extra = dict(filled)
        if notes:
            extra["note"] = "; ".join(notes)
        record("holes", added, **extra)
        if added and orient:
            adj = adjacency(t, len(v))
            flip, comp, parts, conflicts = k.propagate(t, adj)
            k.flip_linked(t, adj, flip)
            flipped = flipped ^ flip[:originals]

    if orient:
        part("orient")
        info: dict[str, Any] = {"parts": 0, "closed_parts": 0, "turned_outward": 0}
        if adj is not None:
            flipped, info = orient_parts(vc, t, adj, flipped, comp, int(parts), originals)
        if conflicts:
            info["note"] = f"Поверхность неориентируема: не согласовано рёбер — {int(conflicts)}"
        record("orient", int(np.count_nonzero(flipped)), **info)

    v, t = compact(v, np.ascontiguousarray(t, dtype=np.int32))
    after = repair_checks(v, t, intersections=intersections, progress=_within(progress, 1.0 - share, 1.0))
    tick(1.0)
    return v, t, {"before": before, "after": after, "steps": steps, "tolerance": tolerance}
