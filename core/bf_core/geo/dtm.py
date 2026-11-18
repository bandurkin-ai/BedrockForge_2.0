"""«Построить ЦТМ»: a digital terrain model from points and lines (ТЗ 7.20, 7.23; ADR 0011 §3 `model.dtm`).

    build_dtm(points, breaklines, boundary=, max_edge=, progress=)   constrained Delaunay TIN in plan

Ported from BF `geo/surfaces.py::build_dtm` (the algorithm and its tolerances are unchanged; the kernels are in
`geo/dtm_kernels.py`). PythonCDT 2.0.2 — the binding of artem-ogre/CDT (MPL-2.0): constrained Delaunay
triangulation with exact predicates; one million points take about 2 s.

Coordinates are float64 project coordinates (UTM-size numbers). Every calculation runs on coordinates shifted
to a local whole origin; the vertices of the result that come from the input keep their X, Y, Z bit for bit,
so the surface passes through the source points exactly. An empty height is refused, never taken for 0.

Errors are `DtmError` {code, message, hint} in Russian. `progress(fraction)` is called between the steps and
may raise to cancel (the triangulation itself is one call of the library: about 2 s a million points).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from . import dtm_kernels as k

# Plan tolerance relative to the extent of the data: points closer than this coincide.
REL_TOLERANCE = 1e-9
DTM_METHOD = "PythonCDT 2.0.2 (artem-ogre/CDT, MPL-2.0): триангуляция Делоне с ограничениями в плане"
FIX_DATA = "Исправьте исходные данные — отметка не выбирается автоматически."

Progress = Callable[[float], None] | None


class DtmError(ValueError):
    """A surface cannot be built: {code, message, hint} in Russian, shown to the user."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


def _tick(progress: Progress, value: float) -> None:
    if progress:
        progress(float(value))


def _fmt(value: float) -> str:
    return f"{value:.3f}".replace(".", ",")


def _as_xyz(array: Any, name: str, columns: tuple[int, ...] = (3,)) -> np.ndarray:
    a = np.asarray(array, dtype=np.float64)
    if a.ndim != 2 or a.shape[1] not in columns:
        raise DtmError(
            "bad_input",
            f"{name}: ожидается массив координат из {' или '.join(str(c) for c in columns)} столбцов.",
            "Проверьте исходные данные.",
        )
    return a


# --- coincident points -----------------------------------------------------------------------------------


def _merge(x, y, z, cx, cy, tol, ztol, code, message):  # type: ignore[no-untyped-def]
    """Clusters of vertices coinciding in plan → representative ids; a Z conflict raises.

    A vertex without Z (NaN, from a plan-only boundary) takes the Z of its cluster.
    Returns (rep, number of merged vertices).
    """
    n = len(cx)
    order = np.lexsort((cy, cx))
    rep = k.cluster(np.ascontiguousarray(cx[order]), np.ascontiguousarray(cy[order]), order, tol)
    members = np.nonzero(rep != np.arange(n))[0]
    if len(members) == 0:
        return rep, 0
    zlo = z.copy()
    zhi = z.copy()
    np.fmin.at(zlo, rep[members], z[members])
    np.fmax.at(zhi, rep[members], z[members])
    bad = np.nonzero(zhi - zlo > ztol)[0]
    if len(bad):
        examples = []
        for b in bad[:5]:
            zs = np.unique(z[np.nonzero(rep == b)[0]])
            zs = zs[~np.isnan(zs)]
            examples.append(f"X={_fmt(x[b])} Y={_fmt(y[b])}: Z = " + "; ".join(_fmt(v) for v in zs))
        raise DtmError(code, f"{message}: {len(bad)} шт. Примеры: " + " | ".join(examples) + ".", FIX_DATA)
    roots = np.unique(rep[members])
    roots = roots[np.isnan(z[roots])]
    z[roots] = zlo[roots]
    return rep, len(members)


# --- triangulation ---------------------------------------------------------------------------------------


def _cdt(cx, cy, edges, tol):  # type: ignore[no-untyped-def]
    """Constrained Delaunay triangulation → (triangles (m,3) int64 CCW, neighbours (m,3) int64, fixed edges).

    neighbours[t, e] is the triangle across the edge (t[e], t[e+1]); -1 on the hull.
    """
    import pythoncdt as cdt

    tri = cdt.Triangulation(cdt.VertexInsertionOrder.AUTO, cdt.IntersectingConstraintEdges.NOT_ALLOWED, float(tol))
    try:
        tri.insert_vertices(np.ascontiguousarray(np.column_stack([cx, cy])))
        if len(edges):
            tri.insert_edges(np.ascontiguousarray(edges, dtype=np.uint32))
    except RuntimeError as exc:
        raise DtmError(
            "triangulation_failed",
            "Не удалось построить триангуляцию: структурные линии или граница пересекаются так, что пересечение "
            f"не удалось разрешить ({str(exc).splitlines()[0]}).",
            "Проверьте линии на самопересечения и наложения.",
        ) from exc
    tri.erase_super_triangle()
    arr = tri.triangles_array()
    t = arr["vertices"].astype(np.int64)
    nb = arr["neighbors"].astype(np.int64)
    nb[nb == int(cdt.NO_NEIGHBOR)] = -1
    if len(edges):
        fixed = np.array([(e.v1, e.v2) for e in tri.fixed_edges_iter()], dtype=np.int64).reshape(-1, 2)
    else:
        fixed = np.zeros((0, 2), np.int64)
    if len(t):
        # CDT returns counter-clockwise triangles; keep the code independent of that.
        a, b, c = t[:, 0], t[:, 1], t[:, 2]
        area2 = (cx[b] - cx[a]) * (cy[c] - cy[a]) - (cy[b] - cy[a]) * (cx[c] - cx[a])
        flip = area2 < 0
        if flip.any():
            t[flip] = t[flip][:, [0, 2, 1]]
            nb[flip] = nb[flip][:, [2, 1, 0]]
    return t, nb, fixed


# --- DTM -------------------------------------------------------------------------------------------------


def build_dtm(
    points: Any,
    breaklines: Sequence[Any] = (),
    *,
    boundary: Any = None,
    max_edge: float | None = None,
    progress: Progress = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Digital terrain model: constrained Delaunay triangulation in plan (XY).

    points      (n,3) or None.
    breaklines  list of (k,3) polylines; a closed line repeats its first vertex at the end.
                Every segment becomes an edge of the result (or a chain of edges when other
                vertices lie on it), no triangle edge crosses it. Where two lines cross at the
                same Z the crossing point becomes a vertex.
    boundary    None → convex hull. (k,2) or (k,3) polygon in plan: its edges are constraints and
                the triangles outside it are removed. With Z (k,3) it is structural like a
                breakline. Without Z (k,2) every vertex takes the Z of the surface built from the
                points and breaklines alone (linear inside a triangle; outside their convex hull —
                the Z of the nearest point of the hull), and a crossing with a breakline takes
                the Z of the breakline.
    max_edge    a triangle is dropped when it has a plan edge longer than max_edge AND it touches
                the outside by an edge — the outside being the convex hull or a triangle already
                dropped. So long triangles are peeled from the outside inward, and a long triangle
                surrounded by short ones stays (no holes are opened). Peeling never passes through
                a constraint edge: breaklines and the boundary polygon are kept as drawn, so with
                a boundary polygon (the whole outline is a constraint) max_edge removes nothing.

    Vertices coinciding in plan (closer than `tolerance` = 1e-9 of the plan extent in X and in Y):
    with the same Z (within `tolerance_z`) they merge silently, the first one is kept; with
    different Z → DtmError with their number and up to 5 examples. The same for breaklines
    crossing at different Z. A point lying on a breakline (within tolerance) without being its
    vertex splits the breakline there and keeps its own Z.

    Returns (vertices (m,3) float64, triangles (t,3) int32, report). Vertices keep the input order
    (points, then breakline vertices, then boundary vertices, then crossing points) without the
    merged and the unused ones; the coordinates of input vertices are bit-identical.
    Triangles are counter-clockwise in plan (normal up).
    """
    _tick(progress, 0.0)
    parts = []
    if points is not None and len(points):
        parts.append(_as_xyz(points, "Точки"))
    n_cloud = len(parts[0]) if parts else 0
    seg_a, seg_b, seg_hard = [], [], []
    offset = n_cloud
    for line in breaklines or ():
        line = _as_xyz(line, "Структурная линия")
        if len(line) == 0:
            continue
        parts.append(line)
        ids = np.arange(offset, offset + len(line))
        seg_a.append(ids[:-1])
        seg_b.append(ids[1:])
        seg_hard.append(np.ones(len(line) - 1, bool))
        offset += len(line)
    ring = None
    hard_ring = False
    if boundary is not None:
        ring_xyz = _as_xyz(boundary, "Граница", columns=(2, 3))
        if len(ring_xyz) < 3:
            raise DtmError(
                "bad_boundary",
                "Граница: нужен полигон не менее чем из трёх вершин.",
                "Выберите замкнутую линию границы.",
            )
        hard_ring = ring_xyz.shape[1] == 3
        if not hard_ring:
            ring_xyz = np.column_stack([ring_xyz, np.full(len(ring_xyz), np.nan)])
        if not np.isfinite(ring_xyz[:, :2]).all() or (hard_ring and not np.isfinite(ring_xyz).all()):
            raise DtmError(
                "bad_coordinates",
                "Граница содержит пустые или бесконечные координаты.",
                "Исправьте линию границы: у каждой вершины должны быть X и Y.",
            )
        parts.append(ring_xyz)
        ring = np.arange(offset, offset + len(ring_xyz))
        seg_a.append(ring)
        seg_b.append(np.roll(ring, -1))
        seg_hard.append(np.full(len(ring), hard_ring))
        offset += len(ring_xyz)
    if not parts:
        raise DtmError(
            "no_data",
            "Нет данных для построения поверхности: не заданы ни точки, ни линии.",
            "Выберите слои точек или линий, или устья скважин.",
        )
    xyz = np.concatenate(parts)
    n_data = offset - (len(ring) if ring is not None else 0)
    if not np.isfinite(xyz[:n_data]).all():
        bad = int(np.count_nonzero(~np.isfinite(xyz[:n_data]).all(axis=1)))
        raise DtmError(
            "bad_coordinates",
            f"Исходные данные содержат пустые или бесконечные координаты: {bad} точек.",
            "У каждой точки должны быть X, Y и Z: пустая отметка не считается нулём.",
        )
    if max_edge is not None and not max_edge > 0:
        raise DtmError(
            "bad_max_edge", "Наибольшая длина ребра должна быть положительной.", "Введите число больше нуля."
        )

    x, y, z = (np.ascontiguousarray(xyz[:, i]) for i in range(3))
    lo = np.array([x.min(), y.min()])
    hi = np.array([x.max(), y.max()])
    extent = float((hi - lo).max())
    if not extent > 0:
        raise DtmError(
            "too_few_points",
            "Все точки совпадают в плане — поверхность построить нельзя.",
            "Нужны хотя бы три точки, не лежащие на одной прямой.",
        )
    origin = np.floor((lo + hi) / 2.0)
    cx = x - origin[0]
    cy = y - origin[1]
    tol = REL_TOLERANCE * extent
    zr = z[np.isfinite(z)]
    ztol = REL_TOLERANCE * max(extent, float(zr.max() - zr.min()) if len(zr) else 0.0)
    input_points = n_data

    sa = np.concatenate(seg_a) if seg_a else np.zeros(0, np.int64)
    sb = np.concatenate(seg_b) if seg_b else np.zeros(0, np.int64)
    hard = np.concatenate(seg_hard) if seg_hard else np.zeros(0, bool)
    n_breakline_segments = int(np.count_nonzero(hard)) - (len(ring) if ring is not None and hard_ring else 0)

    def compact(rep: np.ndarray) -> None:
        nonlocal x, y, z, cx, cy, sa, sb, ring
        keep = rep == np.arange(len(rep))
        index = np.cumsum(keep) - 1
        new = index[rep]
        x, y, z, cx, cy = x[keep], y[keep], z[keep], cx[keep], cy[keep]
        sa, sb = new[sa], new[sb]
        if ring is not None:
            ring = new[ring]

    # ---- coincident points
    rep, merged = _merge(
        x, y, z, cx, cy, tol, ztol, "z_conflict", "Точки совпадают в плане, но имеют разную отметку Z"
    )
    if merged:
        compact(rep)
    _tick(progress, 0.1)

    if ring is not None:
        ring = ring[np.concatenate([[True], ring[1:] != ring[:-1]])]
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if len(np.unique(ring)) < 3:
            raise DtmError(
                "bad_boundary",
                "Граница: нужен полигон не менее чем из трёх различных вершин.",
                "Выберите замкнутую линию границы.",
            )

    # ---- constraint segments: touching and crossing
    live = sa != sb
    sa, sb, hard = sa[live], sb[live], hard[live]
    crossings = 0
    if len(sa) > 1:
        xmin = np.minimum(cx[sa], cx[sb])
        xmax = np.maximum(cx[sa], cx[sb])
        order = np.argsort(xmin, kind="stable")
        cap = max(1024, len(sa))
        while True:
            nt, nc, t_seg, t_vtx, t_par, c_i, c_j, c_ti, c_tj = k.segment_events(
                cx, cy, sa, sb, order, xmin, xmax, tol, cap
            )
            if nt <= cap and nc <= cap:
                break
            cap = max(nt, nc)
        t_seg, t_vtx, t_par = t_seg[:nt], t_vtx[:nt], t_par[:nt]
        c_i, c_j, c_ti, c_tj = c_i[:nc], c_j[:nc], c_ti[:nc], c_tj[:nc]
        conflicts = []
        if nt:
            hard_vertex = np.zeros(len(cx), bool)
            hard_vertex[sa[hard]] = True
            hard_vertex[sb[hard]] = True
            on_hard = hard[t_seg]
            zs = z[sa[t_seg]] + t_par * (z[sb[t_seg]] - z[sa[t_seg]])
            zv = z[t_vtx]
            differ = on_hard & hard_vertex[t_vtx] & (np.abs(zv - zs) > ztol)
            for i in np.nonzero(differ)[0]:
                conflicts.append((x[t_vtx[i]], y[t_vtx[i]], zv[i], zs[i]))
            fill = on_hard & np.isnan(zv)
            z[t_vtx[fill]] = zs[fill]
        ev_seg, ev_par, ev_vtx = [t_seg], [t_par], [t_vtx]
        if nc:
            if np.any(~hard[c_i] & ~hard[c_j]):
                i = int(np.nonzero(~hard[c_i] & ~hard[c_j])[0][0])
                a, b = sa[c_i[i]], sb[c_i[i]]
                raise DtmError(
                    "bad_boundary",
                    f"Граница самопересекается (около X={_fmt(x[a] + c_ti[i] * (x[b] - x[a]))} "
                    f"Y={_fmt(y[a] + c_ti[i] * (y[b] - y[a]))}).",
                    "Задайте границу простым полигоном.",
                )
            ai, bi, aj, bj = sa[c_i], sb[c_i], sa[c_j], sb[c_j]
            ncx = cx[ai] + c_ti * (cx[bi] - cx[ai])
            ncy = cy[ai] + c_ti * (cy[bi] - cy[ai])
            zi = np.where(hard[c_i], z[ai] + c_ti * (z[bi] - z[ai]), np.nan)
            zj = np.where(hard[c_j], z[aj] + c_tj * (z[bj] - z[aj]), np.nan)
            differ = np.abs(zi - zj) > ztol
            for i in np.nonzero(differ)[0]:
                conflicts.append((ncx[i] + origin[0], ncy[i] + origin[1], zi[i], zj[i]))
            nz = np.where(np.isnan(zi), zj, np.where(np.isnan(zj), zi, 0.5 * (zi + zj)))
            new_ids = len(cx) + np.arange(nc)
            ev_seg += [c_i, c_j]
            ev_par += [c_ti, c_tj]
            ev_vtx += [new_ids, new_ids]
        if conflicts:
            # A vertex shared by two segments of a line reports the same place twice.
            conflicts = list({(round(c[0], 6), round(c[1], 6)): c for c in conflicts}.values())
            examples = " | ".join(
                f"X={_fmt(px)} Y={_fmt(py)}: Z = {_fmt(z1)} и {_fmt(z2)}" for px, py, z1, z2 in conflicts[:5]
            )
            raise DtmError(
                "breakline_z_conflict",
                "Структурные линии пересекаются или примыкают друг к другу на разной отметке Z: "
                f"{len(conflicts)} шт. Примеры: {examples}.",
                "Исправьте линии — отметка не выбирается автоматически.",
            )
        if nc:
            x = np.concatenate([x, ncx + origin[0]])
            y = np.concatenate([y, ncy + origin[1]])
            z = np.concatenate([z, nz])
            cx = np.concatenate([cx, ncx])
            cy = np.concatenate([cy, ncy])
        if nt or nc:
            # Split every segment at its events: nodes of a segment = a, events by t, b.
            e_seg = np.concatenate(ev_seg)
            e_par = np.concatenate(ev_par)
            e_vtx = np.concatenate(ev_vtx)
            by = np.lexsort((e_par, e_seg))
            e_seg, e_vtx = e_seg[by], e_vtx[by]
            m = len(sa)
            counts = np.bincount(e_seg, minlength=m)
            starts = np.concatenate([[0], np.cumsum(counts + 2)])
            nodes = np.empty(starts[-1], np.int64)
            owner = np.repeat(np.arange(m), counts + 2)
            nodes[starts[:-1]] = sa
            nodes[starts[1:] - 1] = sb
            rank = np.arange(len(e_seg)) - np.repeat(np.cumsum(counts) - counts, counts)
            nodes[starts[e_seg] + 1 + rank] = e_vtx
            pair = owner[1:] == owner[:-1]
            sa, sb, hard = nodes[:-1][pair], nodes[1:][pair], hard[owner[:-1][pair]]
        if nc:
            crossings = nc
            rep, again = _merge(
                x, y, z, cx, cy, tol, ztol, "z_conflict",
                "Пересечение структурных линий совпадает в плане с точкой другой отметки Z",
            )  # fmt: skip
            if again:
                crossings -= int(np.count_nonzero(rep[-nc:] != np.arange(len(rep) - nc, len(rep))))
                compact(rep)
    nv = len(cx)
    if len(sa):
        live = sa != sb
        sa, sb, hard = sa[live], sb[live], hard[live]
        key = np.minimum(sa, sb) * nv + np.maximum(sa, sb)
        key, first, inverse = np.unique(key, return_index=True, return_inverse=True)
        any_hard = np.zeros(len(key), bool)
        np.logical_or.at(any_hard, inverse, hard)
        sa, sb, hard = sa[first], sb[first], any_hard
    edges = np.column_stack([sa, sb]) if len(sa) else np.zeros((0, 2), np.int64)
    _tick(progress, 0.2)

    # ---- Z of plan-only boundary vertices: from the surface of the data alone
    unknown = np.nonzero(np.isnan(z))[0]
    extrapolated = 0
    if len(unknown):
        known = np.nonzero(~np.isnan(z))[0]
        index = np.full(nv, -1, np.int64)
        index[known] = np.arange(len(known))
        kx, ky, kz = cx[known], cy[known], z[known]
        he = edges[hard]
        if len(known) >= 3:
            t1, nb1, _ = _cdt(kx, ky, index[he], tol)
        else:
            t1, nb1 = np.zeros((0, 3), np.int64), np.zeros((0, 3), np.int64)
        if len(t1) == 0:
            raise DtmError(
                "too_few_points",
                "Недостаточно точек с отметками для построения поверхности.",
                "Нужны хотя бы три точки с высотой, не лежащие на одной прямой.",
            )
        _tick(progress, 0.35)
        where = k.locate(kx, ky, t1, nb1, cx[unknown], cy[unknown])
        inside = where >= 0
        tq = t1[where[inside]]
        qx, qy = cx[unknown[inside]], cy[unknown[inside]]
        ax, ay = kx[tq[:, 0]], ky[tq[:, 0]]
        bx, by_ = kx[tq[:, 1]], ky[tq[:, 1]]
        dx, dy = kx[tq[:, 2]], ky[tq[:, 2]]
        den = (bx - ax) * (dy - ay) - (by_ - ay) * (dx - ax)
        with np.errstate(divide="ignore", invalid="ignore"):
            wb = ((qx - ax) * (dy - ay) - (qy - ay) * (dx - ax)) / den
            wc = ((bx - ax) * (qy - ay) - (by_ - ay) * (qx - ax)) / den
        zq = kz[tq[:, 0]] + wb * (kz[tq[:, 1]] - kz[tq[:, 0]]) + wc * (kz[tq[:, 2]] - kz[tq[:, 0]])
        zq = np.where(np.isfinite(zq), zq, kz[tq[:, 0]])
        z[unknown[inside]] = zq
        if not inside.all():
            hull_t, hull_e = np.nonzero(nb1 < 0)
            ea = t1[hull_t, hull_e]
            eb = t1[hull_t, (hull_e + 1) % 3]
            out = unknown[~inside]
            z[out] = k.nearest_on_edges(kx, ky, kz, ea, eb, cx[out], cy[out])
            extrapolated = len(out)
    _tick(progress, 0.45 if len(unknown) else 0.25)

    # ---- triangulation
    tri, nbr, fixed = _cdt(cx, cy, edges, tol)
    if len(tri) == 0:
        raise DtmError(
            "too_few_points",
            "Недостаточно точек для построения поверхности.",
            "Нужны хотя бы три точки, не лежащие на одной прямой.",
        )
    _tick(progress, 0.8)
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    alive = np.ones(len(tri), bool)
    is_fixed = np.zeros((len(tri), 3), bool)
    if len(fixed) and (ring is not None or max_edge is not None):
        fkey = np.sort(np.minimum(fixed[:, 0], fixed[:, 1]) * nv + np.maximum(fixed[:, 0], fixed[:, 1]))
        for e in range(3):
            u, w = tri[:, e], tri[:, (e + 1) % 3]
            ekey = np.minimum(u, w) * nv + np.maximum(u, w)
            pos = np.minimum(np.searchsorted(fkey, ekey), len(fkey) - 1)
            is_fixed[:, e] = fkey[pos] == ekey
    if ring is not None:
        area2 = (cx[b] - cx[a]) * (cy[c] - cy[a]) - (cy[b] - cy[a]) * (cx[c] - cx[a])
        comp, best = k.components(nbr, is_fixed, area2)
        bt = tri[best]
        qx = cx[bt].mean(axis=1)
        qy = cy[bt].mean(axis=1)
        inside = k.points_in_polygon(qx, qy, np.ascontiguousarray(cx[ring]), np.ascontiguousarray(cy[ring]))
        alive = inside[comp]
        if not alive.any():
            raise DtmError(
                "boundary_outside",
                "Внутри границы нет ни одного треугольника: граница не охватывает исходные данные.",
                "Выберите границу, внутри которой есть точки или линии.",
            )
    dropped_long = 0
    if max_edge is not None:
        limit = float(max_edge) ** 2
        long_ = np.zeros(len(tri), bool)
        for e in range(3):
            u, w = tri[:, e], tri[:, (e + 1) % 3]
            long_ |= (cx[u] - cx[w]) ** 2 + (cy[u] - cy[w]) ** 2 > limit
        dropped_long = int(k.peel_long(nbr, alive, long_, is_fixed))
        if not alive.any():
            raise DtmError(
                "all_long",
                f"Все треугольники длиннее заданного ребра ({max_edge:g}".replace(".", ",") + " м) — поверхность пуста.",
                "Увеличьте наибольшую длину ребра.",
            )
    tri = tri[alive]
    used = np.zeros(nv, bool)
    used[tri.ravel()] = True
    index = np.cumsum(used) - 1
    tri = index[tri]
    vertices = np.column_stack([x[used], y[used], z[used]])
    lx, ly = cx[used], cy[used]
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    ux, uy, uz = lx[b] - lx[a], ly[b] - ly[a], vertices[b, 2] - vertices[a, 2]
    wx, wy, wz = lx[c] - lx[a], ly[c] - ly[a], vertices[c, 2] - vertices[a, 2]
    nzc = ux * wy - uy * wx
    area_plan = 0.5 * float(nzc.sum())
    area = 0.5 * float(np.sqrt((uy * wz - uz * wy) ** 2 + (uz * wx - ux * wz) ** 2 + nzc**2).sum())
    report = {
        "points": len(vertices),
        "breakline_segments": n_breakline_segments,
        "triangles": len(tri),
        "merged_duplicates": int(merged),
        "dropped_long": dropped_long,
        "area_plan": area_plan,
        "area": area,
        "method": DTM_METHOD,
        "tolerance": tol,
        # Extra information for the record of the operation.
        "tolerance_z": ztol,
        "input_points": int(input_points),
        "crossings_added": int(crossings),
        "unused_points": int(nv - len(vertices)),
        "boundary_extrapolated": int(extrapolated),
    }
    _tick(progress, 1.0)
    return vertices, tri.astype(np.int32), report
