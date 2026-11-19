"""Clipping of wireframes by a plane, a polygon in plan and a surface — for closed solids and for open
surfaces (ТЗ 7.23 «Обрезка»; ADR 0015 §7, decision 10).

    clip_by_plane(mesh, point, normal, keep=)        a solid (closed parts, the cut capped) or an open surface
    clip_by_polygon(mesh, polygon_xy, keep=)         … by the vertical prism through a simple polygon in plan
    clip_by_surface(mesh, surface, keep=, direction=)  … by an open surface (a DTM, a fault): "above" / "below"

Every function returns {part: (vertices float64, triangles int32) | None, …, "report"}: a part is None when it
was not asked for or is empty; the report holds the volumes (areas for an open surface) of the source and of
both parts and the error of their sum. Inputs are read, never changed.

Closed solids go to manifold3d (`split_by_plane`, `split` with a prism or with the swept surface). An open
surface cut by a plane is cut here: every crossed edge gets one new vertex computed from its two ends in a
fixed order (lower vertex number first), so the two parts share the seam bit for bit, and a vertex lying on
the plane is used as it is. By a polygon or a surface an open surface is swept into a marked solid
(`clip_sheet.py`) and the triangles that came from the surface are taken from the boolean result.

A surface that does not cover a solid (or an open wireframe) along the direction of the cut leaves walls of
its sweep in the result: the cut is refused (ADR 0015 §7; BF put the uncovered part «above» with a warning).

Ported from BF `geo/solids.py` (`clip_by_plane`, `_cut_surface`, `clip_by_polygon`, `_prism`,
`clip_by_surface`); the cut of an open wireframe by a surface («разлом по рельефу») is new.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import manifold3d
import numpy as np
from manifold3d import Manifold

from bf_core.geo import clip_sheet as sheet
from bf_core.geo.solids import (
    SolidError,
    Tick,
    closed_solid,
    compact,
    empty_error,
    from_manifold,
    has_run,
    local_origin,
    mesh_arrays,
    signed_volume,
    surface_area,
    tick_at,
    to_manifold,
    to_project,
    watertight,
)

REL = 1e-9  # what is left uncovered, relative to the whole, before a cut is refused


def _keep(keep: str, options: tuple[str, str]) -> tuple[bool, bool]:
    if keep not in (*options, "both"):
        raise SolidError(
            "bad_parameter",
            f"Неизвестный вариант «{keep}».",
            f"Ожидается {options[0]}, {options[1]} или both.",
        )
    return keep in (options[0], "both"), keep in (options[1], "both")


def _part(
    part: tuple[np.ndarray, np.ndarray], origin: np.ndarray, wanted: bool
) -> tuple[np.ndarray, np.ndarray] | None:
    return to_project(*part, origin) if wanted and len(part[1]) else None


def _source(mesh: Any, name: str) -> tuple[np.ndarray, np.ndarray]:
    v, t = mesh_arrays(mesh, name)
    if len(t) == 0:
        raise empty_error(name)
    return v, t


def _solid_report(info: dict, parts: list, names: tuple[str, str], man: Manifold) -> dict[str, Any]:
    volumes = [abs(signed_volume(pv, pt)) for pv, pt in parts]
    return {
        "kind": "solid",
        "closed": True,
        "volume": info["volume"],
        f"volume_{names[0]}": volumes[0],
        f"volume_{names[1]}": volumes[1],
        "sum_error": abs(volumes[0] + volumes[1] - info["volume"]),
        "tolerance": float(man.get_tolerance()),
        "flipped": info["flipped"],
    }


def _surface_report(area: float, parts: list, names: tuple[str, str]) -> dict[str, Any]:
    areas = [surface_area(pv, pt) for pv, pt in parts]
    return {
        "kind": "surface",
        "closed": False,
        "area": area,
        f"area_{names[0]}": areas[0],
        f"area_{names[1]}": areas[1],
        "sum_error": abs(areas[0] + areas[1] - area),
    }


def _result(parts: list, origin: np.ndarray, wants: tuple[bool, bool], names: tuple[str, str], report: dict) -> dict:
    report["origin"] = origin.tolist()
    report[f"triangles_{names[0]}"] = len(parts[0][1])
    report[f"triangles_{names[1]}"] = len(parts[1][1])
    return {
        names[0]: _part(parts[0], origin, wants[0]),
        names[1]: _part(parts[1], origin, wants[1]),
        "report": report,
    }


# --- plane -----------------------------------------------------------------------------------------------


def cut_surface(v: np.ndarray, t: np.ndarray, point: np.ndarray, normal: np.ndarray) -> list:
    """An open surface (local coordinates) cut by a plane: [(v+, t+), (v−, t−)], each possibly empty.

    Every crossed edge gets one new vertex, computed from its two ends in a fixed order, so the triangles on
    both sides share it; a vertex lying on the plane (within 1e-12 of the size of the surface) is used as it
    is: no new vertex, no zero-area triangle."""
    d = (v - point) @ normal
    d[np.abs(d) <= 1e-12 * max(float(np.abs(v).max()), 1.0)] = 0.0  # rounding noise is not a side
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
    inverse = inverse.reshape(-1)
    e_lo, e_hi = keys // nv, keys % nv
    with np.errstate(invalid="ignore", divide="ignore"):
        f = d[e_lo] / (d[e_lo] - d[e_hi])
    new = v[e_lo] + (v[e_hi] - v[e_lo]) * f[:, None]
    node = nv + np.arange(len(keys))
    node = np.where(d[e_lo] == 0, e_lo, np.where(d[e_hi] == 0, e_hi, node))
    p, q = node[inverse[: len(tc)]], node[inverse[len(tc) :]]
    lone = np.stack([a, p, q], axis=1)
    rest = np.concatenate([np.stack([p, b, c], axis=1), np.stack([p, c, q], axis=1)])
    rest_positive = np.concatenate([~lone_positive, ~lone_positive])
    allv = np.concatenate([v, new])
    out = []
    for whole, lone_mask, rest_mask in (
        (count == 3, lone_positive, rest_positive),
        (count == 0, ~lone_positive, ~rest_positive),
    ):
        tri = np.concatenate([t[whole], lone[lone_mask], rest[rest_mask]])
        # A cut through a vertex leaves triangles with a repeated corner: they are empty.
        tri = tri[(tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 2] != tri[:, 0])]
        out.append(compact(allv, tri))
    return out


def clip_by_plane(
    mesh: Any, point: Any, normal: Any, *, keep: str = "both", name: str = "Каркас", tick: Tick = None
) -> dict[str, Any]:
    """Cuts a mesh by the plane through `point` (project coordinates) with `normal`: {"positive", "negative",
    "report"}. A closed solid gives closed parts; an open surface is cut without caps, its new vertices lie on
    the plane and are shared by both parts bit for bit."""
    names = ("positive", "negative")
    wants = _keep(keep, names)
    v, t = _source(mesh, name)
    n = sheet.unit(normal, "Нормаль плоскости")
    p = np.asarray(point, dtype=np.float64).reshape(-1)
    if p.shape != (3,) or not np.isfinite(p).all():
        raise SolidError("bad_parameter", "Точка плоскости: ожидаются три числа.", "Задайте X, Y и Z точки.")
    origin = local_origin(v)
    tick_at(tick, 0.0, "Обрезка плоскостью")
    if watertight(v, t):
        man, info = closed_solid(v, t, origin, name, "Обрезка плоскостью")
        pos, neg = man.split_by_plane(tuple(n.tolist()), float(n @ (p - origin)))
        parts = [from_manifold(m.to_mesh64()) for m in (pos, neg)]
        report = _solid_report(info, parts, names, man)
    else:
        local = v - origin
        parts = cut_surface(local, t, p - origin, n)
        report = _surface_report(surface_area(local, t), parts, names)
    tick_at(tick, 1.0, "Обрезка плоскостью")
    return _result(parts, origin, wants, names, report)


# --- polygon in plan -------------------------------------------------------------------------------------


def polygon_ring(polygon_xy: Any, what: str = "Полигон обрезки") -> np.ndarray:
    """A simple contour (k, 2) without the repeated closing point."""
    try:
        polygon = np.asarray(polygon_xy, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise SolidError("bad_polygon", f"{what}: ожидается массив точек (k, 2).", "Выберите замкнутую линию.") from exc
    if polygon.ndim != 2 or polygon.shape[1] < 2 or not np.isfinite(polygon[:, :2]).all():
        raise SolidError("bad_polygon", f"{what}: ожидается массив точек (k, 2).", "Выберите замкнутую линию.")
    polygon = polygon[:, :2]
    if len(polygon) > 1 and np.array_equal(polygon[0], polygon[-1]):
        polygon = polygon[:-1]
    if len(polygon) < 3:
        raise SolidError("bad_polygon", f"{what}: нужно не меньше трёх точек.", "Выберите замкнутую линию.")
    return np.ascontiguousarray(polygon)


def prism(polygon: np.ndarray, z0: float, z1: float, what: str = "Полигон обрезки") -> Manifold:
    """Vertical prism through a simple polygon (local coordinates), built on the polygon's own vertices
    (manifold3d's CrossSection would round them)."""
    x, y = polygon[:, 0], polygon[:, 1]
    signed = 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    if signed < 0:
        polygon = polygon[::-1]
    polygon = np.ascontiguousarray(polygon)
    broken = SolidError(
        "bad_polygon",
        f"{what} вырожден или самопересекается.",
        "Нужен простой замкнутый контур без отверстий: исправьте линию в редакторе.",
    )
    if signed == 0:
        raise broken
    try:
        tri = np.asarray(manifold3d.triangulate([polygon]), dtype=np.int64).reshape(-1, 3)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise broken from exc
    a, b, c = (polygon[tri[:, i]] for i in range(3))
    areas = 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    whole = abs(signed)
    # A simple polygon is tiled by its triangles: no negative ones, the areas add up.
    if (
        len(tri) == 0
        or areas.min() < -1e-9 * whole
        or abs(areas.sum() - whole) > 1e-9 * whole
        or abs(np.abs(areas).sum() - whole) > 1e-9 * whole
    ):
        raise broken
    k = len(polygon)
    i = np.arange(k)
    j = (i + 1) % k
    vertices = np.concatenate([np.column_stack([polygon, np.full(k, z0)]), np.column_stack([polygon, np.full(k, z1)])])
    triangles = np.concatenate(
        [tri[:, ::-1], tri + k, np.stack([i, j, j + k], axis=1), np.stack([i, j + k, i + k], axis=1)]
    )
    try:
        return to_manifold(vertices, triangles, what)
    except SolidError as exc:
        raise broken from exc


def clip_by_polygon(
    mesh: Any, polygon_xy: Any, *, keep: str = "inside", name: str = "Каркас", tick: Tick = None
) -> dict[str, Any]:
    """Cuts a mesh by the vertical prism through a simple polygon in plan (k, 2), over the whole Z range:
    {"inside", "outside", "report"}. A closed solid gives closed parts. An open surface (a DTM by a licence
    boundary) gives open parts: it must be a single-valued sheet along Z or along its dominant normal; its
    triangulation away from the cut is kept."""
    names = ("inside", "outside")
    wants = _keep(keep, names)
    v, t = _source(mesh, name)
    polygon = polygon_ring(polygon_xy)
    origin = local_origin(v)
    local = v - origin
    z0, z1 = float(local[:, 2].min()), float(local[:, 2].max())
    margin = 1.0 + 0.5 * (z1 - z0)
    tick_at(tick, 0.0, "Обрезка полигоном")
    cutter = prism(polygon - origin[:2], z0 - margin, z1 + margin)
    if watertight(v, t):
        man, info = closed_solid(v, t, origin, name, "Обрезка полигоном")
        tick_at(tick, 0.2, "Обрезка полигоном")
        parts = [from_manifold(m.to_mesh64()) for m in man.split(cutter)]
        report = _solid_report(info, parts, names, man)
    else:
        sheet.check_sheet_input(v, t, name)
        direction, oriented = sheet.sheet_along(local, t, None, name)
        tick_at(tick, 0.2, "Обрезка полигоном")
        span = float(np.linalg.norm(local.max(axis=0) - local.min(axis=0)))
        man, first = sheet.marked_sweep(local, oriented, -direction, max(span, 1.0), name)
        parts = [sheet.sheet_part(m.to_mesh64(), first) for m in man.split(cutter)]
        report = _surface_report(surface_area(local, t), parts, names)
        report["direction"] = direction.tolist()
        report["tolerance"] = float(man.get_tolerance())
    tick_at(tick, 1.0, "Обрезка полигоном")
    return _result(parts, origin, wants, names, report)


# --- surface ---------------------------------------------------------------------------------------------


def _not_covered(names: Sequence[str], what: str, amount: float, unit: str) -> SolidError:
    value = f"{amount:.2f}".replace(".", ",")
    return SolidError(
        "surface_not_covering",
        f"Поверхность «{names[1]}» не перекрывает {what} «{names[0]}» вдоль направления обрезки: "
        f"непокрытая часть — {value} {unit}.",
        "Расширьте поверхность за пределы каркаса; для крутой поверхности (разлома) задайте направление "
        "обрезки по её нормали.",
    )


def clip_by_surface(
    mesh: Any,
    surface: Any,
    *,
    keep: str = "both",
    direction: Any = None,
    names: tuple[str, str] = ("Тело", "Поверхность"),
    tick: Tick = None,
) -> dict[str, Any]:
    """Cuts a closed solid or an open wireframe by an open surface (a DTM, a fault) into the part on the
    positive side of `direction` ("above") and the other ("below"): {"above", "below", "report"}.

    `direction` defaults to +Z when the surface is single-valued in Z, else to its dominant normal; "normal"
    asks for the dominant normal outright (a steep fault is single-valued in Z too, but covers little in
    plan). The surface must be a single-valued sheet along it. Where it does not cover the mesh along the
    direction, SolidError `surface_not_covering` names the uncovered volume (area)."""
    wants = _keep(keep, ("above", "below"))
    mv, mt = _source(mesh, names[0])
    fv, ft = _source(surface, names[1])
    origin = local_origin(mv)
    surf = fv - origin
    d, oriented = sheet.sheet_along(surf, ft, direction, names[1])
    tick_at(tick, 0.1, "Обрезка поверхностью")
    heights = np.concatenate([(mv - origin) @ d, surf @ d])
    length = float(heights.max() - heights.min()) * 1.1 + 1.0
    column, first = sheet.marked_sweep(surf, oriented, -d, length, names[1])  # everything below the surface
    if watertight(mv, mt):
        out = _solid_by_surface(mv, mt, origin, column, first, (surf, oriented, d, length), names, tick)
    else:
        out = _sheet_by_surface(mv, mt, origin, column, first, (surf, oriented, d, length), names, tick)
    parts, report = out
    report.update({"direction": d.tolist(), "length": length, "covers": True})
    tick_at(tick, 1.0, "Обрезка поверхностью")
    return _result(parts, origin, wants, ("above", "below"), report)


def _suspect(meshes: list, first: int, above: tuple, surface: tuple) -> bool:
    """Walls of the sweep inside a part (the edge of the surface passed through the mesh), or a part of the
    mesh entirely beside the surface (found by one of its vertices)."""
    if any(has_run(m, first + sheet.WALLS) for m in meshes):
        return True
    surf, oriented, d, _ = surface
    return bool(len(above[1]) and sheet.outside_footprint(sheet.part_representatives(*above), surf, oriented, d))


def _solid_by_surface(mv, mt, origin, column, first, surface, names, tick):  # type: ignore[no-untyped-def]
    man, info = closed_solid(mv, mt, origin, names[0], "Обрезка поверхностью")
    tick_at(tick, 0.3, "Обрезка поверхностью")
    below, above = man.split(column)
    meshes = [above.to_mesh64(), below.to_mesh64()]
    parts = [from_manifold(m) for m in meshes]
    report = _solid_report(info, parts, ("above", "below"), man)
    tick_at(tick, 0.8, "Обрезка поверхностью")
    if _suspect(meshes, first, parts[0], surface):
        surf, oriented, d, length = surface
        over, _ = sheet.marked_sweep(surf, oriented[:, ::-1], d, length, names[1])  # everything above it
        uncovered = float((above - over).volume())
        if uncovered > REL * max(info["volume"], 1e-300):
            raise _not_covered(names, "тело", uncovered, "м³")
    return parts, report


def _sheet_by_surface(mv, mt, origin, column, first, surface, names, tick):  # type: ignore[no-untyped-def]
    """An open wireframe (a fault, a seam) by a surface: the wireframe is swept into a marked slab and the
    triangles of its own surface are taken from both parts of the slab."""
    sheet.check_sheet_input(mv, mt, names[0])
    local = mv - origin
    own = sheet.dominant_normal(local, mt)
    oriented, reason = sheet.orient_sheet(local, mt, own)
    if oriented is None:
        own = np.array([0.0, 0.0, 1.0])
        oriented, _ = sheet.orient_sheet(local, mt, own)
        if oriented is None:
            raise SolidError(
                "not_a_sheet",
                f"Каркас «{names[0]}» нельзя обрезать поверхностью: он не однозначен ни вдоль своей нормали, "
                f"ни по вертикали — {reason}.",
                "Разделите каркас на части («На части») и обрежьте каждую.",
            )
    span = float(np.linalg.norm(local.max(axis=0) - local.min(axis=0)))
    slab, own_first = sheet.marked_sweep(local, oriented, -own, max(span, 1.0), names[0])
    tick_at(tick, 0.3, "Обрезка поверхностью")
    below, above = slab.split(column)
    meshes = [above.to_mesh64(), below.to_mesh64()]
    parts = [sheet.sheet_part(m, own_first) for m in meshes]
    whole = surface_area(local, mt)
    report = _surface_report(whole, parts, ("above", "below"))
    report["tolerance"] = float(slab.get_tolerance())
    tick_at(tick, 0.8, "Обрезка поверхностью")
    if _suspect(meshes, first, parts[0], surface):
        surf, oriented_s, d, length = surface
        over, _ = sheet.marked_sweep(surf, oriented_s[:, ::-1], d, length, names[1])
        rest = sheet.sheet_part((above - over).to_mesh64(), own_first)
        uncovered = surface_area(*rest)
        if uncovered > REL * max(whole, 1e-300):
            raise _not_covered(names, "каркас", uncovered, "м²")
    return parts, report
