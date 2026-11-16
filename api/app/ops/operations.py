"""The operations: what each needs, what it checks before running and what it builds.

An operation never changes its sources. It makes a new entry of the project tree (a wireframe
file or a line file) next to the first source; `stats["operation"]` of the result keeps the
operation, its sources, parameters and tolerances, so the result can be reproduced.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.formats.lines import Polyline
from app.formats.wireframes import mesh_stats
from app.importing.objects import PALETTE, add_line_layer, add_wireframe, queue_derive, used_colors
from app.models import Collar, Dataset, LineLayer, Wireframe
from app.storage.geometry import line_arrays, mesh_arrays

ALGORITHM = 1  # bumped when results of an operation change for the same input

Progress = Callable[[float, str], None]


class OpError(ValueError):
    """The operation cannot run on these objects; the message tells what is wrong and which tool fixes it."""


@dataclass
class OpOutput:
    """What an operation produced; `finish` turns it into objects of the tree."""

    name: str
    # (name, vertices, triangles, colour | None, extra stats)
    meshes: list[tuple[str, np.ndarray, np.ndarray, str | None, dict]] = field(default_factory=list)
    # (layer name, [Polyline], colour | None)
    layers: list[tuple[str, list[Polyline], str | None]] = field(default_factory=list)
    report: dict = field(default_factory=dict)
    folder: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class Sources:
    """The source objects of one run: read once, described in the result."""

    session: Session
    project_id: int
    used: list[dict] = field(default_factory=list)
    folder: str | None = None
    color: str | None = None

    def _dataset(self, dataset_id: int, what: str) -> Dataset:
        d = self.session.get(Dataset, dataset_id)
        if d is None or d.project_id != self.project_id:
            raise OpError(f"{what} не из этого проекта")
        if self.folder is None:
            self.folder = d.folder or ""
        return d

    def wireframe(self, wid) -> Wireframe:
        w = self.session.get(Wireframe, int(wid)) if wid is not None else None
        if w is None:
            raise OpError(f"Каркас {wid} не найден: возможно, он удалён")
        self._dataset(w.dataset_id, f"Каркас «{w.name}»")
        self.used.append({"kind": "wireframe", "id": w.id, "name": w.name, "triangles": w.triangle_count,
                          "sha": [w.blobs["vertices"][:16], w.blobs["triangles"][:16]]})
        self.color = self.color or w.color
        return w

    def layer(self, lid) -> LineLayer:
        layer = self.session.get(LineLayer, int(lid)) if lid is not None else None
        if layer is None:
            raise OpError(f"Слой линий {lid} не найден: возможно, он удалён")
        self._dataset(layer.dataset_id, f"Слой «{layer.name}»")
        self.used.append({"kind": "lines", "id": layer.id, "name": layer.name, "version": layer.version,
                          "sha": [layer.blobs["vertices"][:16]]})
        self.color = self.color or layer.color
        return layer


def mesh_of(w: Wireframe) -> tuple[np.ndarray, np.ndarray]:
    v, t = mesh_arrays(w.blobs)
    return np.asarray(v), np.asarray(t)


def lines_of(layer: LineLayer, picked: list[int] | None = None) -> list[tuple[int, np.ndarray, bool, dict]]:
    """(index, vertices without the repeated closing vertex, closed, attributes) of the layer's lines."""
    v, o, c = line_arrays(layer.blobs)
    attrs = layer.line_attributes or []
    out = []
    for i in (range(len(c)) if picked is None else picked):
        if i < 0 or i >= len(c):
            raise OpError(f"В слое «{layer.name}» нет линии {i + 1}")
        p = np.array(v[o[i]:o[i + 1]], dtype=np.float64)
        closed = bool(c[i])
        if closed and len(p) > 1 and np.array_equal(p[0], p[-1]):
            p = p[:-1]
        out.append((i, p, closed, attrs[i] if i < len(attrs) else {}))
    return out


def require_closed(w: Wireframe, role: str = "Каркас") -> None:
    s = w.stats or {}
    if not s.get("closed"):
        detail = []
        if s.get("open_edges"):
            detail.append(f"открытых рёбер {s['open_edges']}")
        if s.get("nonmanifold_edges"):
            detail.append(f"рёбер с тремя и более треугольниками {s['nonmanifold_edges']}")
        raise OpError(f"{role} «{w.name}» не замкнут" + (f" ({', '.join(detail)})" if detail else "")
                      + ". Операция работает только с замкнутыми телами — сначала примените «Исправить каркас»")
    if (s.get("self_intersections") or {}).get("pairs"):
        raise OpError(f"{role} «{w.name}» имеет самопересечения ({s['self_intersections']['pairs']}): результат был бы неверным. "
                      "Посмотрите их командой «Показать самопересечения» и исправьте треугольники вручную")


def require_open(w: Wireframe, role: str = "Поверхность") -> None:
    if (w.stats or {}).get("closed"):
        raise OpError(f"{role} «{w.name}» — замкнутое тело, а нужна незамкнутая поверхность (ЦМР, разлом)")


def _polyline(points: np.ndarray, closed: bool, attributes: dict | None = None) -> Polyline:
    """A line in the stored form: a closed one repeats its first vertex."""
    p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if closed and len(p) > 2 and not np.array_equal(p[0], p[-1]):
        p = np.vstack([p, p[:1]])
    return Polyline(p, bool(closed) and len(p) > 3, dict(attributes or {}))


def _polygon_of(src: Sources, ref: dict | None, what: str = "Полигон") -> np.ndarray | None:
    """A closed line picked as {"layer_id", "line"} (0-based; default: the layer's only closed line)."""
    if not ref:
        return None
    layer = src.layer(ref.get("layer_id"))
    lines = [x for x in lines_of(layer, None if ref.get("line") is None else [int(ref["line"])]) if x[2]]
    if len(lines) != 1:
        raise OpError(f"{what}: в слое «{layer.name}» должна быть выбрана одна замкнутая линия (найдено {len(lines)})")
    return lines[0][1]


def _num(params: dict, key: str, default: float | None = None, *, positive: bool = False) -> float | None:
    value = params.get(key, default)
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise OpError(f"Параметр «{key}» должен быть числом") from None
    if not np.isfinite(value) or (positive and value <= 0):
        raise OpError(f"Параметр «{key}» должен быть " + ("положительным числом" if positive else "числом"))
    return value


def _vec(params: dict, key: str) -> np.ndarray | None:
    value = params.get(key)
    if value is None:
        return None
    a = np.asarray(value, dtype=np.float64)
    if a.shape != (3,) or not np.isfinite(a).all():
        raise OpError(f"Параметр «{key}» — три числа X, Y, Z")
    return a


# ---------------------------------------------------------------- part C: solids from contours


def _selected_lines(src: Sources, refs) -> list[tuple[LineLayer, int, np.ndarray, bool, dict]]:
    if not refs:
        raise OpError("Не выбраны контуры: укажите слой линий или линии в нём")
    out = []
    for ref in refs:
        layer = src.layer(ref.get("layer_id"))
        picked = ref.get("lines")
        for i, p, closed, attrs in lines_of(layer, None if picked is None else [int(k) for k in picked]):
            out.append((layer, i, p, closed, attrs))
    return out


def is_marked_tie(closed: bool, attrs: dict) -> bool:
    """An open line marked ROLE = TIE by the editor's tie-line tool."""
    return not closed and str(attrs.get("ROLE", "")).upper() == "TIE"


# An unmarked two-point line counts as a tie line when both its ends sit on contour vertices (m).
TIE_SNAP = 1e-3


def op_stitch(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.stitch import StitchError, stitch_contours

    lines = _selected_lines(src, params.get("contours"))
    contours = [(layer, i, p) for layer, i, p, closed, _ in lines if closed and len(p) >= 3]
    ties, skipped = [], 0
    if contours:
        from scipy.spatial import cKDTree

        tree = cKDTree(np.vstack([p for _, _, p in contours]))
        for _, _, p, closed, attrs in lines:
            if closed:
                continue
            # The editor's tie tool marks its lines; a plain two-point line joining two contour vertices is one too.
            if is_marked_tie(closed, attrs) or (len(p) == 2 and float(tree.query(p)[0].max()) <= TIE_SNAP):
                ties.append(p[[0, -1]])
            else:
                skipped += 1
    if len(contours) < 2:
        raise OpError(f"Для сшивки нужно не меньше двух замкнутых контуров, выбрано {len(contours)}. "
                      "Замкните линии в редакторе («Замкнуть») или выберите другие")
    caps = {k: str(params.get(k, "flat")) for k in ("start_cap", "end_cap")}
    for k, v in caps.items():
        if v not in ("none", "flat", "point", "line", "extend"):
            raise OpError(f"Неизвестный способ замыкания торца «{v}»")
    try:
        result = stitch_contours([p for _, _, p in contours], ties, start_cap=caps["start_cap"], end_cap=caps["end_cap"],
                                 cap_distance=_num(params, "cap_distance", 0.5, positive=True), progress=progress)
    except StitchError as exc:
        between = getattr(exc, "between", None)
        where = ""
        if between:
            names = [f"«{contours[k][0].name}», линия {contours[k][1] + 1}" for k in _section_contours(exc, between)]
            where = f" ({'; '.join(names)})" if names else ""
        raise OpError(f"{exc}{where}") from exc
    names = list(dict.fromkeys(layer.name for layer, _, _ in contours))
    out = OpOutput(name=f"Тело: {', '.join(names)}"[:255])
    report = dict(result.report)
    if skipped:
        out.warnings.append(f"Незамкнутых линий пропущено: {skipped} (тай-линия — линия из двух вершин, концы которой стоят на вершинах контуров)")
    out.warnings += list(report.get("warnings", []))
    out.meshes.append((out.name, result.vertices, result.triangles, None, {}))
    out.report = report
    return out


def _section_contours(exc, between) -> list[int]:
    sections = getattr(exc, "sections", None) or []
    try:
        return [c for k in between for c in sections[k]["contours"]][:6]
    except (IndexError, KeyError, TypeError):
        return []


def op_extrude(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.stitch import extrude_polygon

    lines = [x for x in _selected_lines(src, params.get("contours")) if x[3] and len(x[2]) >= 3]
    if not lines:
        raise OpError("Для выдавливания нужен замкнутый полигон: выбранные линии не замкнуты")
    distance = _num(params, "distance")
    if not distance:
        raise OpError("Расстояние выдавливания не задано")
    vector = _vec(params, "vector")
    out = OpOutput(name=f"Выдавливание: {lines[0][0].name}"[:255])
    for n, (layer, i, p, _, attrs) in enumerate(lines):
        progress(n / len(lines), f"Полигон {n + 1} из {len(lines)}")
        v, t = extrude_polygon(p, distance=distance, vector=vector, both_sides=bool(params.get("both_sides")))
        name = str(attrs.get("NAME") or f"{layer.name} {i + 1}")
        out.meshes.append((name[:255], v, t, None, {}))
    out.report = {"distance": distance, "vector": None if vector is None else vector.tolist(), "polygons": len(lines)}
    return out


def op_edit_triangles(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.stitch import edit_triangles

    w = src.wireframe(params.get("wireframe_id"))
    v, t = mesh_of(w)
    remove = np.asarray(params.get("remove") or [], dtype=np.int64)
    add = np.asarray(params.get("add") or [], dtype=np.float64).reshape(-1, 3, 3)
    if not len(remove) and not len(add):
        raise OpError("Правка пуста: не выбраны треугольники для удаления и не добавлено новых")
    if len(remove) and (remove.min() < 0 or remove.max() >= len(t)):
        raise OpError("Выбранного треугольника в каркасе нет: каркас изменился, выберите треугольники заново")
    nv, nt = edit_triangles(v, t, remove, add)
    out = OpOutput(name=f"{w.name} (правка)"[:255])
    out.meshes.append((out.name, nv, nt, w.color, {"attributes": (w.stats or {}).get("attributes")}))
    out.report = {"removed": int(len(remove)), "added": int(len(add))}
    return out


# ---------------------------------------------------------------- part D: repair, simplify, smooth, merge, split


def op_repair(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.repair import repair

    w = src.wireframe(params.get("wireframe_id"))
    v, t = mesh_of(w)
    flags = {k: bool(params.get(k, True)) for k in ("drop_degenerate", "drop_duplicate", "orient", "fill_holes", "fix_nonmanifold")}
    weld = _num(params, "weld", 1e-6) if params.get("weld_on", True) else None
    if weld is not None and weld < 0:
        raise OpError("Допуск сшивки вершин не может быть отрицательным")
    nv, nt, report = repair(v, t, weld=weld, progress=progress, **flags)
    out = OpOutput(name=f"{w.name} (исправлен)"[:255])
    out.meshes.append((out.name, nv, nt, w.color, {"attributes": (w.stats or {}).get("attributes")}))
    out.report = report
    left = report.get("after", {})
    if left.get("self_intersections"):
        out.warnings.append(f"Самопересечения остались ({left['self_intersections']}): они не исправляются автоматически — "
                            "команда «Показать самопересечения» отметит их, правьте треугольники вручную")
    if not left.get("closed") and (report.get("before") or {}).get("open_edges"):
        out.warnings.append("Каркас остался незамкнутым: " + "; ".join(
            str(s.get("note")) for s in report.get("steps", []) if s.get("note")) or "Каркас остался незамкнутым")
    return out


def op_simplify(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.repair import simplify_mesh

    w = src.wireframe(params.get("wireframe_id"))
    target, tolerance = params.get("target_triangles"), _num(params, "tolerance", positive=True)
    if (target is None) == (tolerance is None):
        raise OpError("Задайте одно из двух: число треугольников или допуск по отклонению")
    if target is not None and not 4 <= int(target) < w.triangle_count:
        raise OpError(f"Число треугольников должно быть от 4 до {w.triangle_count - 1}")
    v, t = mesh_of(w)
    nv, nt, report = simplify_mesh(v, t, target_triangles=None if target is None else int(target), tolerance=tolerance,
                                   progress=progress)
    out = OpOutput(name=f"{w.name} (упрощён)"[:255])
    out.meshes.append((out.name, nv, nt, w.color, {"attributes": (w.stats or {}).get("attributes")}))
    out.report = report
    return out


def op_smooth(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.repair import smooth_mesh

    w = src.wireframe(params.get("wireframe_id"))
    iterations = int(_num(params, "iterations", 10, positive=True))
    strength = _num(params, "strength", 0.5, positive=True)
    if iterations > 500 or strength > 1:
        raise OpError("Сглаживание: не больше 500 проходов, сила от 0 до 1")
    v, t = mesh_of(w)
    progress(0.1, "Сглаживание")
    nv, nt, report = smooth_mesh(v, t, iterations=iterations, strength=strength, keep_boundary=bool(params.get("keep_boundary", True)))
    out = OpOutput(name=f"{w.name} (сглажен)"[:255])
    out.meshes.append((out.name, nv, nt, w.color, {"attributes": (w.stats or {}).get("attributes")}))
    out.report = report
    return out


def op_merge(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.repair import merge_meshes

    ids = list(dict.fromkeys(params.get("wireframe_ids") or []))
    if len(ids) < 2:
        raise OpError("Для объединения в один объект выберите не меньше двух каркасов")
    ws = [src.wireframe(i) for i in ids]
    v, t = merge_meshes([mesh_of(w) for w in ws])
    out = OpOutput(name=("Объединение: " + " + ".join(w.name for w in ws))[:255])
    out.meshes.append((out.name, v, t, ws[0].color, {}))
    out.report = {"parts": len(ws)}
    return out


def op_split(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.repair import split_components

    w = src.wireframe(params.get("wireframe_id"))
    parts = split_components(*mesh_of(w))
    if len(parts) < 2:
        raise OpError(f"Каркас «{w.name}» состоит из одной связной части: делить нечего")
    limit = int(params.get("max_parts") or 200)
    out = OpOutput(name=f"{w.name} (части)"[:255])
    for n, (v, t) in enumerate(parts[:limit], start=1):
        out.meshes.append((f"{w.name} — часть {n}"[:255], v, t, None, {}))
    if len(parts) > limit:
        out.warnings.append(f"Частей {len(parts)}: показаны {limit} самых больших")
    out.report = {"parts": len(parts)}
    return out


# ---------------------------------------------------------------- part E: booleans and clipping

_BOOL_SIGN = {"union": "∪", "intersection": "∩", "difference": "−"}
_BOOL_TITLE = {"union": "Объединение", "intersection": "Пересечение", "difference": "Вычитание"}


def op_boolean(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.solids import SolidError, boolean_many

    kind = str(params.get("op"))
    if kind not in _BOOL_SIGN:
        raise OpError(f"Неизвестная булева операция «{kind}»")
    ids = [params.get("a"), *(params.get("b") if isinstance(params.get("b"), list) else [params.get("b")])]
    if len(set(ids)) != len(ids) or len(ids) < 2 or None in ids:
        raise OpError("Для булевой операции выберите два разных каркаса")
    ws = [src.wireframe(i) for i in ids]
    for w in ws:
        require_closed(w)
    progress(0.05, _BOOL_TITLE[kind])
    try:
        v, t, report = boolean_many([mesh_of(w) for w in ws], kind)
    except SolidError as exc:
        raise OpError(str(exc)) from exc
    name = f" {_BOOL_SIGN[kind]} ".join(w.name for w in ws)[:255]
    if report.get("empty") or len(t) == 0:
        raise OpError(f"Результат «{name}» пуст: " + ("тела не пересекаются" if kind == "intersection" else "от первого тела ничего не осталось"))
    out = OpOutput(name=name)
    out.meshes.append((name, v, t, ws[0].color, {}))
    out.report = report
    return out


def _keep(params: dict, allowed: tuple[str, ...], default: str = "both") -> str:
    keep = str(params.get("keep", default))
    if keep not in allowed:
        raise OpError(f"Что оставить: одно из {', '.join(allowed)}")
    return keep


def _parts(out: OpOutput, w: Wireframe, result: dict, titles: dict[str, str]) -> None:
    for key, title in titles.items():
        part = result.get(key)
        if part is not None and len(part[1]):
            out.meshes.append((f"{w.name}: {title}"[:255], part[0], part[1], None, {}))
    out.report = result.get("report", {})
    if out.report.get("warning"):
        out.warnings.append(str(out.report["warning"]))
    if not out.meshes:
        raise OpError("После обрезки ничего не осталось: секущая не пересекает объект или выбрана пустая сторона")


def op_clip_plane(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.solids import SolidError, clip_by_plane

    w = src.wireframe(params.get("wireframe_id"))
    point, normal = _vec(params, "point"), _vec(params, "normal")
    if point is None or normal is None or not np.any(normal):
        raise OpError("Плоскость не задана: нужны точка и нормаль (или уровень Z, или линия разреза)")
    keep = _keep(params, ("positive", "negative", "both"))
    progress(0.05, "Обрезка плоскостью")
    try:
        result = clip_by_plane(mesh_of(w), point, normal, keep=keep)
    except SolidError as exc:
        raise OpError(str(exc)) from exc
    horizontal = abs(normal[2]) > 0.999 * np.linalg.norm(normal)
    titles = {"positive": "выше уровня" if horizontal else "перед плоскостью", "negative": "ниже уровня" if horizontal else "за плоскостью"}
    out = OpOutput(name=f"{w.name}: обрезка плоскостью"[:255])
    _parts(out, w, result, titles)
    return out


def op_clip_polygon(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.solids import SolidError, clip_by_polygon

    w = src.wireframe(params.get("wireframe_id"))
    polygon = _polygon_of(src, params.get("polygon"))
    if polygon is None:
        raise OpError("Не выбран полигон обрезки: нужна замкнутая линия")
    keep = _keep(params, ("inside", "outside", "both"), "inside")
    progress(0.05, "Обрезка полигоном")
    try:
        result = clip_by_polygon(mesh_of(w), polygon[:, :2], keep=keep)
    except SolidError as exc:
        raise OpError(str(exc)) from exc
    out = OpOutput(name=f"{w.name}: обрезка полигоном"[:255])
    _parts(out, w, result, {"inside": "внутри полигона", "outside": "вне полигона"})
    return out


def op_clip_surface(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.solids import SolidError, clip_by_surface

    w = src.wireframe(params.get("wireframe_id"))
    s = src.wireframe(params.get("surface_id"))
    if w.id == s.id:
        raise OpError("Тело и секущая поверхность — один и тот же каркас")
    require_closed(w, "Тело")
    require_open(s)
    keep = _keep(params, ("above", "below", "both"))
    progress(0.05, "Обрезка поверхностью")
    try:
        result = clip_by_surface(mesh_of(w), mesh_of(s), keep=keep)
    except SolidError as exc:
        raise OpError(str(exc)) from exc
    out = OpOutput(name=f"{w.name}: обрезка «{s.name}»"[:255])
    _parts(out, w, result, {"above": f"выше «{s.name}»", "below": f"ниже «{s.name}»"})
    return out


def op_intersection_lines(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.solids import intersection_lines

    a, b = src.wireframe(params.get("a")), src.wireframe(params.get("b"))
    if a.id == b.id:
        raise OpError("Выберите два разных каркаса")
    progress(0.05, "Линии пересечения")
    polylines = intersection_lines(mesh_of(a), mesh_of(b))
    if not polylines:
        raise OpError(f"Каркасы «{a.name}» и «{b.name}» не пересекаются")
    name = f"Пересечение: {a.name} × {b.name}"[:255]
    out = OpOutput(name=name)
    out.layers.append((name, [_polyline(p["points"], p["closed"]) for p in polylines], None))
    out.report = {"lines": len(polylines), "closed": sum(1 for p in polylines if p["closed"])}
    return out


# ---------------------------------------------------------------- part B: surfaces


def op_dtm(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.surfaces import SurfaceError, build_dtm

    use_breaklines = bool(params.get("breaklines", True))
    points: list[np.ndarray] = []
    breaklines: list[np.ndarray] = []
    names = []
    for lid in params.get("layers") or []:
        layer = src.layer(lid)
        names.append(layer.name)
        for _, p, closed, _ in lines_of(layer):
            if use_breaklines and len(p) >= 2:
                breaklines.append(np.vstack([p, p[:1]]) if closed else p)
            else:
                points.append(p)
    for set_id in params.get("set_ids") or []:
        d = src._dataset(int(set_id), "Набор скважин")
        rows = src.session.execute(select(Collar.x, Collar.y, Collar.z).where(
            Collar.set_id == d.id, Collar.x.is_not(None), Collar.y.is_not(None), Collar.z.is_not(None))).all()
        if rows:
            points.append(np.asarray(rows, dtype=np.float64))
            names.append(f"устья «{d.name}»")
            src.used.append({"kind": "drillholes", "id": d.id, "name": d.name, "collars": len(rows)})
    if not points and not breaklines:
        raise OpError("Не из чего строить поверхность: выберите слои линий или точек, или устья скважин")
    boundary = _polygon_of(src, params.get("boundary"), "Граница ЦМР")
    try:
        v, t, report = build_dtm(np.vstack(points) if points else None, breaklines, boundary=boundary,
                                 max_edge=_num(params, "max_edge", positive=True), progress=progress)
    except SurfaceError as exc:
        raise OpError(str(exc)) from exc
    if len(t) == 0:
        raise OpError("Поверхность пуста: слишком мало точек или все треугольники отброшены по длине ребра")
    out = OpOutput(name=f"ЦМР: {', '.join(names)}"[:255])
    out.meshes.append((out.name, v, t, None, {}))
    out.report = report
    return out


def op_contours(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.surfaces import contours

    w = src.wireframe(params.get("wireframe_id"))
    interval = _num(params, "interval", positive=True)
    if interval is None:
        raise OpError("Шаг изолиний не задан")
    lo, hi = w.extent["min"][2], w.extent["max"][2]
    if (hi - lo) / interval > 20_000:
        raise OpError(f"Слишком мелкий шаг: получилось бы {int((hi - lo) / interval)} уровней. Увеличьте шаг")
    major_every = int(_num(params, "major_every", 5, positive=True))
    v, t = mesh_of(w)
    found = contours(v, t, interval=interval, base=_num(params, "base", 0.0), major_every=major_every, progress=progress)
    if not found:
        raise OpError(f"В пределах высот каркаса ({lo:g} … {hi:g}) нет ни одного уровня с шагом {interval:g}")
    out = OpOutput(name=f"Изолинии: {w.name}"[:255])
    for major, title in ((True, "Основные"), (False, "Дополнительные")):
        lines = [_polyline(c["points"], c["closed"], {"ELEV": float(c["z"])}) for c in found if bool(c["major"]) == major]
        if lines:
            out.layers.append((f"{title} {interval * (major_every if major else 1):g} м", lines, None))
    out.report = {"levels": len({float(c["z"]) for c in found}), "lines": len(found), "interval": interval, "major_every": major_every}
    return out


def op_volume_between(src: Sources, params: dict, progress: Progress) -> OpOutput:
    from app.geo.surfaces import SurfaceError, volume_between, volume_to_level

    top = src.wireframe(params.get("top"))
    require_open(top, "Верхняя поверхность")
    polygon = _polygon_of(src, params.get("polygon"))
    polygon = None if polygon is None else polygon[:, :2]
    progress(0.05, "Объём между поверхностями")
    try:
        if params.get("level") is not None:
            level = _num(params, "level")
            report = volume_to_level(mesh_of(top), level, polygon=polygon)
            title = f"Объём: «{top.name}» и уровень {level:g}"
        else:
            bottom = src.wireframe(params.get("bottom"))
            if bottom.id == top.id:
                raise OpError("Выберите две разные поверхности")
            require_open(bottom, "Нижняя поверхность")
            report = volume_between(mesh_of(top), mesh_of(bottom), polygon=polygon)
            title = f"Объём: «{top.name}» и «{bottom.name}»"
    except SurfaceError as exc:
        raise OpError(str(exc)) from exc
    return OpOutput(name=title[:255], report=report)


@dataclass(frozen=True)
class Operation:
    title: str
    run: Callable[[Sources, dict, Progress], OpOutput]


OPERATIONS: dict[str, Operation] = {
    "stitch": Operation("Сшивка контуров в тело", op_stitch),
    "extrude": Operation("Выдавливание полигона", op_extrude),
    "edit_triangles": Operation("Ручная правка треугольников", op_edit_triangles),
    "repair": Operation("Исправление каркаса", op_repair),
    "simplify": Operation("Упрощение каркаса", op_simplify),
    "smooth": Operation("Сглаживание каркаса", op_smooth),
    "merge": Operation("Объединение каркасов в один объект", op_merge),
    "split": Operation("Разделение каркаса на части", op_split),
    "boolean": Operation("Булева операция", op_boolean),
    "clip_plane": Operation("Обрезка плоскостью", op_clip_plane),
    "clip_polygon": Operation("Обрезка полигоном", op_clip_polygon),
    "clip_surface": Operation("Обрезка поверхностью", op_clip_surface),
    "intersection_lines": Operation("Линии пересечения каркасов", op_intersection_lines),
    "dtm": Operation("ЦМР из точек и линий", op_dtm),
    "contours": Operation("Изолинии", op_contours),
    "volume_between": Operation("Объём между поверхностями", op_volume_between),
}


def _no_progress(_fraction: float, _message: str) -> None:
    pass


def precheck(session: Session, project_id: int, op: str, params: dict) -> None:
    """Cheap checks before a job is queued: the objects exist, are of the right kind and closedness.
    Raises OpError with what is wrong. The job repeats them: the objects may change meanwhile."""
    if op not in OPERATIONS:
        raise OpError(f"Неизвестная операция «{op}»")
    src = Sources(session, project_id)
    for key in ("wireframe_id", "surface_id", "a", "top", "bottom"):
        if params.get(key) is not None:
            src.wireframe(params[key])
    b = params.get("b")
    for wid in (b if isinstance(b, list) else [b] if b is not None else []) + list(params.get("wireframe_ids") or []):
        src.wireframe(wid)
    for ref in list(params.get("contours") or []) + [r for r in (params.get("polygon"), params.get("boundary")) if r]:
        src.layer(ref.get("layer_id"))
    for lid in params.get("layers") or []:
        src.layer(lid)
    ws = {u["id"]: session.get(Wireframe, u["id"]) for u in src.used if u["kind"] == "wireframe"}
    if op == "boolean":
        for w in ws.values():
            require_closed(w)
    elif op in ("clip_surface",):
        require_closed(ws[int(params["wireframe_id"])], "Тело")
        require_open(ws[int(params["surface_id"])])
    elif op == "volume_between":
        for key in ("top", "bottom"):
            if params.get(key) is not None:
                require_open(ws[int(params[key])])


def run_operation(session: Session, project_id: int, op: str, params: dict, *, name: str | None = None,
                  folder: str | None = None, progress: Progress = _no_progress) -> dict:
    """Runs the operation and adds its result to the tree. Returns the job result."""
    if op not in OPERATIONS:
        raise OpError(f"Неизвестная операция «{op}»")
    src = Sources(session, project_id)
    out = OPERATIONS[op].run(src, params, lambda f, m="": progress(0.9 * f, m))
    title = (name or out.name).strip()[:255] or out.name
    provenance = {"op": op, "title": OPERATIONS[op].title, "params": params, "sources": src.used,
                  "algorithm": ALGORITHM, "name": title}
    result: dict = {"op": op, "title": title, "dataset_ids": [], "report": _jsonable(out.report), "warnings": out.warnings}
    target_folder = folder if folder is not None else (src.folder or "")
    used = used_colors(session, project_id)
    progress(0.92, "Запись результата")
    if out.meshes:
        dataset = Dataset(project_id=project_id, kind="wireframes", name=title, folder=target_folder,
                          meta={"format": "Операция", "operation": provenance}, blobs={})
        session.add(dataset)
        session.flush()
        single = len(out.meshes) == 1
        for n, (mesh_name, v, t, color, extra) in enumerate(out.meshes):
            stats = mesh_stats(v, t)
            stats["operation"] = provenance
            stats["report"] = _jsonable(out.report)
            stats.update({k: val for k, val in extra.items() if val})
            add_wireframe(session, dataset.id, title if single and name else mesh_name,
                          color or src.color or PALETTE[(used + n) % len(PALETTE)], v, t, stats)
        session.flush()
        queue_derive(session, project_id, dataset)
        result["dataset_ids"].append(dataset.id)
        result["kind"] = "wireframes"
        result["wireframe_ids"] = list(session.scalars(select(Wireframe.id).where(Wireframe.dataset_id == dataset.id).order_by(Wireframe.id)))
    if out.layers:
        dataset = Dataset(project_id=project_id, kind="lines", name=title, folder=target_folder,
                          meta={"format": "Операция", "operation": provenance}, blobs={})
        session.add(dataset)
        session.flush()
        for n, (layer_name, lines, color) in enumerate(out.layers):
            add_line_layer(session, dataset.id, layer_name, color or PALETTE[(used + n) % len(PALETTE)], lines,
                           action="operation", note=OPERATIONS[op].title)
        result["dataset_ids"].append(dataset.id)
        result.setdefault("kind", "lines")
        result["layer_ids"] = list(session.scalars(select(LineLayer.id).where(LineLayer.dataset_id == dataset.id).order_by(LineLayer.id)))
    return result


def _jsonable(value):
    """A report as plain JSON: numpy numbers and arrays converted, non-finite numbers dropped to None."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    return value
