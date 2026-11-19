"""Volumes between two surfaces and between a surface and a level, in their common outline or in a polygon
(ТЗ 7.23 «Объём»; ADR 0015 §7, decision 5).

    volume_between(top, bottom, polygon=, bodies=, tick=)        cut / fill between two DTMs
    volume_to_level(surface, level, polygon=, bodies=, tick=)    cut / fill against the plane z = level
    under_solid(v, t, base_z)                                    closed solid between a surface and a level
    surface_footprint(v, t)                                      outline loops of a surface in plan
    upward(v, t, name)                                           a surface single-valued in Z, turned upward

Method (exact for two TINs, no sampling): A↓ is the solid under `top` down to a floor below both surfaces,
B↑ the solid above `bottom` up to a ceiling above both; cut = V(A↓ ∩ B↑), fill = V(B↓ ∩ A↑). The intersections
exist only over the common footprint, so the common outline comes by itself; a polygon is a vertical prism
intersected with A↓ and A↑; a level is a horizontal surface. area = the floor face of A↓ ∩ B↓. The booleans
are manifold3d on coordinates shifted to a whole-number origin; the vertices of the surfaces keep their bits.
`bodies=True` also returns the solids «Выемка» (A↓ ∩ B↑) and «Насыпь» (B↓ ∩ A↑) in project coordinates
(decision 5: they become objects with the volumes in their «Свойства»).

volume_to_level without a polygon and without bodies is prismatic: every triangle is a prism over its plan
projection split by the level plane analytically — exact for the TIN, no booleans, in slices with `tick`.

Single-valued in Z (our check, ADR 0015 §7): no vertical triangles, no triangles turned over against the
others, no parts lying above one another in plan (a centroid strictly inside another triangle).

Ported from BF `geo/surfaces.py` (`_upward`, `surface_footprint`, `_column`, `under_solid`, `volume_between`,
`volume_to_level`). Errors: `contours.SurfaceError` {code, message, hint} in Russian.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from manifold3d import Manifold

from bf_core.geo.clip import polygon_ring, prism
from bf_core.geo.contours import SurfaceError, boundary_edges, surface_mesh, used_vertices
from bf_core.geo.meshes import morton_order
from bf_core.geo.solids import SLICE, SolidError, Tick, from_manifold, tick_at, to_manifold, to_project

_VERTICAL = 1e-9  # |nz| / |n| below this: the triangle is vertical
VOLUME_METHOD = "manifold3d: пересечения тела под одной поверхностью с телом над другой (точно для двух TIN)"
LEVEL_METHOD = "призмы: каждый треугольник рассечён плоскостью уровня аналитически (точно для TIN)"
CUT, FILL = "Выемка", "Насыпь"


def _fmt(value: float) -> str:
    return f"{value:.3f}".replace(".", ",")


def upward(vertices: Any, triangles: Any, name: str = "Поверхность") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A surface single-valued in Z, triangles turned counter-clockwise in plan → (vertices float64,
    triangles int64, doubled plan areas). Triangles degenerate in 3D are kept and ignored by the check."""
    v, t = surface_mesh(vertices, triangles, name)
    p = v[used_vertices(t, len(v))]
    o = np.floor((p.min(axis=0) + p.max(axis=0)) / 2.0)
    a, b, c = (v[t[:, i]] - o for i in range(3))
    u, w = b - a, c - a
    nz = u[:, 0] * w[:, 1] - u[:, 1] * w[:, 0]
    nx = u[:, 1] * w[:, 2] - u[:, 2] * w[:, 1]
    ny = u[:, 2] * w[:, 0] - u[:, 0] * w[:, 2]
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    longest = np.maximum(np.einsum("ij,ij->i", u, u), np.einsum("ij,ij->i", w, w))
    solid = norm > 1e-12 * longest
    vertical = solid & (np.abs(nz) <= _VERTICAL * norm)
    up = solid & ~vertical & (nz > 0)
    down = solid & ~vertical & (nz < 0)
    n_vertical, n_up, n_down = (int(np.count_nonzero(m)) for m in (vertical, up, down))
    if n_vertical or (n_up and n_down):
        parts = []
        if n_vertical:
            parts.append(f"вертикальных треугольников: {n_vertical}")
        if n_up and n_down:
            parts.append(f"перевёрнутых (нависающих) треугольников: {min(n_up, n_down)}")
        k = int(np.nonzero(vertical if n_vertical else (down if n_down <= n_up else up))[0][0])
        q = v[t[k]].mean(axis=0)
        raise SurfaceError(
            "not_single_valued",
            f"Поверхность «{name}» не однозначна по Z ({', '.join(parts)}; например, около X={_fmt(q[0])} "
            f"Y={_fmt(q[1])} Z={_fmt(q[2])}).",
            "Объём считается только для поверхностей без вертикальных стенок и нависаний (ЦТМ).",
        )
    if n_down:
        t = t[:, [0, 2, 1]]
        nz = -nz
    from bf_core.geo.crossing import overlapping_centroids

    overlaps = overlapping_centroids(v - o, t[solid])
    if overlaps:
        raise SurfaceError(
            "not_single_valued",
            f"Поверхность «{name}» не однозначна по Z: её части лежат одна над другой (треугольников — {overlaps}).",
            "Объём считается только для поверхностей без перекрытий в плане (ЦТМ): разделите поверхность.",
        )
    return v, t, nz


def _loops(ea: np.ndarray, eb: np.ndarray) -> list[np.ndarray]:
    """Boundary half-edges (start → end, the surface on the left) walked into loops of vertex numbers."""
    outgoing: dict[int, list[int]] = {}
    for k, s in enumerate(ea.tolist()):
        outgoing.setdefault(s, []).append(k)
    used = np.zeros(len(ea), bool)
    ends, starts = eb.tolist(), ea.tolist()
    loops = []
    for k0 in range(len(ea)):
        if used[k0]:
            continue
        loop = []
        k = k0
        while True:
            used[k] = True
            loop.append(starts[k])
            nxt = next((c for c in outgoing.get(ends[k], ()) if not used[c]), None)
            if nxt is None:
                break
            k = nxt
        loops.append(np.array(loop, dtype=np.int64))
    return loops


def surface_footprint(vertices: Any, triangles: Any) -> list[np.ndarray]:
    """Outline loops of a surface in plan, each (k, 2) float64 without the repeated closing point: the outer
    loops counter-clockwise, holes clockwise; sorted by signed area, largest first."""
    v, t, _ = upward(vertices, triangles)
    ea, eb = boundary_edges(t, len(v))
    if len(ea) == 0:
        raise SurfaceError("surface_closed", "Поверхность замкнута — у неё нет контура в плане.", "")
    loops = [v[loop, :2].copy() for loop in _loops(ea, eb)]
    o = np.floor(v[ea, :2].mean(axis=0))

    def signed(p: np.ndarray) -> float:
        q = p - o
        return 0.5 * float(np.sum(q[:, 0] * np.roll(q[:, 1], -1) - np.roll(q[:, 0], -1) * q[:, 1]))

    return sorted(loops, key=signed, reverse=True)


def _column(v: np.ndarray, t: np.ndarray, base_z: float, name: str) -> tuple[np.ndarray, np.ndarray]:
    """Closed solid between an upward surface and the plane z = base_z, oriented outward. Vertices: the used
    vertices of the surface (bit for bit), then the copies of its outline on the plane. The floor is the
    outline triangulated in plan (holes and separate parts included), not a copy of every triangle: a million
    coplanar triangles would make manifold3d slow."""
    import manifold3d

    used = used_vertices(t, len(v))
    index = np.full(len(v), -1, np.int64)
    index[used] = np.arange(len(used))
    top = index[t]
    n = len(used)
    ea, eb = boundary_edges(top, n, name)
    if len(ea) == 0:
        raise SurfaceError("surface_closed", f"Поверхность «{name}» замкнута — тело под ней построить нельзя.", "")
    zs = v[used, 2]
    if base_z > zs.min():
        if base_z < zs.max():
            raise SurfaceError(
                "level_crosses",
                f"Уровень {_fmt(base_z)} пересекает поверхность «{name}» (отметки от {_fmt(zs.min())} до "
                f"{_fmt(zs.max())}).",
                "Тело строится только до уровня ниже или выше всей поверхности.",
            )
    elif base_z == zs.min():
        raise SurfaceError(
            "level_crosses",
            f"Уровень {_fmt(base_z)} касается поверхности «{name}».",
            "Задайте уровень ниже её наименьшей отметки.",
        )
    loops = _loops(ea, eb)
    ring = np.concatenate(loops)  # the outline vertices, loop by loop: the floor vertices n, n + 1, …
    starts = np.cumsum([0] + [len(loop) for loop in loops])
    floor_of = n + np.arange(len(ring))
    follow = np.arange(len(ring)) + 1
    follow[starts[1:] - 1] = starts[:-1]  # the last vertex of a loop is followed by its first
    a, b = ring, ring[follow]
    fa, fb = floor_of, floor_of[follow]
    walls = np.concatenate([np.column_stack([a, fa, fb]), np.column_stack([a, fb, b])])
    flat = v[used[ring], :2]
    o = np.floor(flat.min(axis=0))
    pieces = [np.ascontiguousarray(flat[starts[i] : starts[i + 1]] - o) for i in range(len(loops))]
    floor = np.asarray(manifold3d.triangulate(pieces), dtype=np.int64).reshape(-1, 3) + n
    tris = np.concatenate([top, floor[:, ::-1], walls])
    if base_z > zs.min():
        tris = tris[:, [0, 2, 1]]
    bottom = v[used[ring]].copy()
    bottom[:, 2] = base_z
    return np.concatenate([v[used], bottom]), tris


def under_solid(vertices: Any, triangles: Any, base_z: float) -> tuple[np.ndarray, np.ndarray]:
    """Closed solid between a surface and the plane z = base_z (below the whole surface, or above it — then
    the solid above the surface): (vertices float64, triangles int32), oriented outward."""
    v, t, _ = upward(vertices, triangles)
    sv, st = _column(v, t, float(base_z), "Поверхность")
    return sv, st.astype(np.int32)


def _floor_area(solid: Manifold, z_floor: float, eps: float) -> float:
    """Plan area of the faces of a solid lying on its floor plane."""
    v, t = from_manifold(solid.to_mesh64())
    if len(t) == 0:
        return 0.0
    flat = (np.abs(v[t, 2] - z_floor) <= eps).all(axis=1)
    a, b, c = v[t[flat, 0]], v[t[flat, 1]], v[t[flat, 2]]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    return 0.5 * math.fsum(np.abs(cross).tolist())


def _manifold(sv: np.ndarray, st: np.ndarray, origin: np.ndarray, name: str) -> Manifold:
    try:
        return to_manifold(sv - origin, st, name)
    except SolidError as exc:
        raise SurfaceError(
            "solid_rejected",
            f"Поверхность «{name}»: тело под ней не построено ({exc.message})",
            "Проверьте поверхность: совпадающие вершины, вырожденные и повторные треугольники — «Исправить каркас».",
        ) from exc


def _body(solid: Manifold, origin: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    v, t = from_manifold(solid.to_mesh64())
    return to_project(v, t, origin) if len(t) else None


def volume_between(
    top: Any, bottom: Any, *, polygon: Any = None, bodies: bool = False, tick: Tick = None
) -> dict[str, Any]:
    """Cut and fill between two surfaces single-valued in Z (DTMs), each (vertices, triangles), inside their
    common outline (and inside `polygon`, a simple contour (k, 2), when given): {"cut": where `top` is above
    `bottom`, "fill": where `bottom` is above `top`, "net": cut − fill, "area": plan area of the common region,
    "tolerance", "method"} and, with `bodies`, {"bodies": {"Выемка": (v, t) | None, "Насыпь": …}}."""
    tick_at(tick, 0.0, "Объём между поверхностями")
    va, ta, _ = upward(top[0], top[1], "Верхняя поверхность")
    vb, tb, _ = upward(bottom[0], bottom[1], "Нижняя поверхность")
    # Triangles along a Z-order curve: manifold3d builds and intersects spatially coherent meshes faster.
    ta, tb = ta[morton_order(va, ta)], tb[morton_order(vb, tb)]
    pa, pb = va[used_vertices(ta, len(va))], vb[used_vertices(tb, len(vb))]
    lo = np.minimum(pa.min(axis=0), pb.min(axis=0))
    hi = np.maximum(pa.max(axis=0), pb.max(axis=0))
    origin = np.floor((lo + hi) / 2.0)
    origin[2] = math.floor(lo[2])
    pad = max(1.0, math.ceil(0.05 * (hi[2] - lo[2])))
    z_floor = origin[2] - pad
    z_ceil = math.ceil(hi[2]) + pad
    tick_at(tick, 0.1, "Объём между поверхностями")

    def solid(v: np.ndarray, t: np.ndarray, level: float, name: str) -> Manifold:
        return _manifold(*_column(v, t, level, name), origin, name)

    a_down = solid(va, ta, z_floor, "Верхняя поверхность")
    a_up = solid(va, ta, z_ceil, "Верхняя поверхность")
    b_down = solid(vb, tb, z_floor, "Нижняя поверхность")
    b_up = solid(vb, tb, z_ceil, "Нижняя поверхность")
    if polygon is not None:
        ring = polygon_ring(polygon, "Ограничивающий полигон") - origin[:2]
        try:
            cutter = prism(ring, z_floor - origin[2] - 1.0, z_ceil - origin[2] + 1.0, "Ограничивающий полигон")
        except SolidError as exc:
            raise SurfaceError(exc.code, exc.message, exc.hint) from exc
        a_down = a_down ^ cutter
        a_up = a_up ^ cutter
    tick_at(tick, 0.3, "Объём между поверхностями")
    common = a_down ^ b_down
    common.status()  # evaluates the intersection (one call, not cancellable)
    tolerance = max(s.get_tolerance() for s in (a_down, a_up, b_down, b_up, common))
    area = _floor_area(common, z_floor - origin[2], max(1e-9, 10 * tolerance))
    if not area > 0:
        where = " в пределах заданного полигона" if polygon is not None else ""
        raise SurfaceError(
            "no_overlap",
            f"Поверхности не перекрываются в плане{where} — общего контура для подсчёта объёма нет.",
            "Выберите поверхности, которые лежат одна над другой.",
        )
    tick_at(tick, 0.5, "Объём между поверхностями")
    cut_solid = a_down ^ b_up
    cut = float(cut_solid.volume())
    tick_at(tick, 0.7, "Объём между поверхностями")
    fill_solid = b_down ^ a_up
    fill = float(fill_solid.volume())
    out: dict[str, Any] = {
        "cut": cut,
        "fill": fill,
        "net": cut - fill,
        "area": area,
        "tolerance": float(tolerance),
        "method": VOLUME_METHOD,
    }
    if bodies:
        out["bodies"] = {CUT: _body(cut_solid, origin), FILL: _body(fill_solid, origin)}
    tick_at(tick, 1.0, "Объём между поверхностями")
    return out


def _prismatic(v: np.ndarray, t: np.ndarray, nz: np.ndarray, level: float, tick: Tick) -> tuple[float, float]:
    """Cut and fill of the prisms of every triangle split by the level plane, in slices summed with fsum."""
    cuts, fills = [], []
    for start in range(0, len(t), SLICE):
        tri, area = t[start : start + SLICE], 0.5 * np.abs(nz[start : start + SLICE])
        h = v[tri, 2] - level
        n_pos, n_neg = (h > 0).sum(axis=1), (h < 0).sum(axis=1)
        whole = area * h.sum(axis=1) / 3.0
        mixed = (n_pos > 0) & (n_neg > 0)
        cut = float(whole[~mixed & (n_pos > 0)].sum())
        fill = float(-whole[~mixed & (n_neg > 0)].sum())
        if mixed.any():
            hm = h[mixed]
            lone_pos = n_pos[mixed] == 1
            # The lone vertex: the only one above the level, otherwise the only one below it.
            lone = np.where(lone_pos, np.argmax(hm > 0, axis=1), np.argmax(hm < 0, axis=1))
            rows = np.arange(len(hm))
            h0, h1, h2 = hm[rows, lone], hm[rows, (lone + 1) % 3], hm[rows, (lone + 2) % 3]
            # The corner cut off by the level: a similar triangle with heights (h0, 0, 0).
            corner = area[mixed] * (h0 / (h0 - h1)) * (h0 / (h0 - h2)) * np.abs(h0) / 3.0
            wm = whole[mixed]
            cut += float(np.where(lone_pos, corner, corner + wm).sum())
            fill += float(np.where(lone_pos, corner - wm, corner).sum())
        cuts.append(cut)
        fills.append(fill)
        tick_at(tick, (start + len(tri)) / len(t), "Объём до уровня")
    return math.fsum(cuts), math.fsum(fills)


def volume_to_level(
    surface: Any, level: float, *, polygon: Any = None, bodies: bool = False, tick: Tick = None
) -> dict[str, Any]:
    """Volumes between a surface (vertices, triangles) and the plane z = level: cut — the surface above the
    level, fill — below it, net = cut − fill, area — plan area of the surface (inside `polygon` when given).
    With a polygon or `bodies` — `volume_between` with a horizontal surface at the level."""
    level = float(level)
    if not math.isfinite(level):
        raise SurfaceError("bad_parameter", "Уровень должен быть числом.", "Задайте отметку уровня.")
    tick_at(tick, 0.0, "Объём до уровня")
    v, t, nz = upward(*surface)
    if polygon is not None or bodies:
        p = v[used_vertices(t, len(v))]
        lo, hi = p[:, :2].min(axis=0) - 1.0, p[:, :2].max(axis=0) + 1.0
        if polygon is not None:
            ring = polygon_ring(polygon, "Ограничивающий полигон")
            lo, hi = np.minimum(lo, ring.min(axis=0) - 1.0), np.maximum(hi, ring.max(axis=0) + 1.0)
        flat = np.array([[lo[0], lo[1], level], [hi[0], lo[1], level], [hi[0], hi[1], level], [lo[0], hi[1], level]])
        return volume_between(
            (v, t), (flat, np.array([[0, 1, 2], [0, 2, 3]])), polygon=polygon, bodies=bodies, tick=tick
        )
    cut, fill = _prismatic(v, t, nz, level, tick)
    extent = float(np.ptp(v[used_vertices(t, len(v))], axis=0).max())
    return {
        "cut": cut,
        "fill": fill,
        "net": cut - fill,
        "area": 0.5 * math.fsum(np.abs(nz).tolist()),
        "tolerance": float(np.finfo(np.float64).eps * max(extent, 1.0)),
        "method": LEVEL_METHOD,
    }
