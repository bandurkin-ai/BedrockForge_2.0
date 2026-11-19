"""The registry of operations and their preconditions (ТЗ 7.23; ADR 0015 §6).

`precheck(conn, store, op, params)` runs in the endpoint (a 422 before a job is queued) and again in the job,
inside its read transaction (the objects may have changed meanwhile): the objects are there and ready, of the
right kind, closed where the operation needs a solid (by `wireframes.stats`), the lines picked exist, the
parameters are numbers in their ranges. It gives a `Checked`: the parameters as the run takes them, the
sources (`Sources.used`), the size of the input and the job to queue — `geometry.operation` in the quick queue
when the input has at most `QUICK_TRIANGLES` triangles, else `geometry.operation.heavy` (decision 1); «Построить
ЦТМ» (`dtm`) is the job `model.dtm` (decision 2).

A wireframe that is not closed where a solid is needed is a 422 `wireframe_open` that names it and its open
edges, with `fix` {op "repair", wireframe_id}: the window shows the button «Исправить каркас».
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.storage.blobs import BlobStore

from . import params as P
from .schema import CAPS as CAP_CHOICES
from .schema import OPERATIONS
from .sources import Sources, Wf

QUICK_TRIANGLES = 2_000_000
JOB = "geometry.operation"
HEAVY_JOB = "geometry.operation.heavy"
FIX_HINT = "Запустите «Исправить каркас» и повторите."
MAX_CONTOUR_VERTICES = 20_000_000
MAX_LEVELS = 20_000  # as geo/contours.py (not imported here: the endpoint stays light)
CAPS = tuple(c["value"] for c in CAP_CHOICES)


@dataclass
class Checked:
    op: str
    title: str
    params: dict[str, Any]
    sources: list[dict[str, Any]]
    folder: str | None
    triangles: int = 0  # of the wireframes read
    vertices: int = 0  # of the lines read
    job: str = JOB
    job_params: dict[str, Any] = field(default_factory=dict)  # `model.dtm` only

    @property
    def size(self) -> int:
        """Triangles of the input, the lines counted as the triangles a solid from them has (about two a vertex)."""
        return self.triangles + 2 * self.vertices

    @property
    def queue_job(self) -> str:
        return self.job if self.job != JOB or self.size <= QUICK_TRIANGLES else HEAVY_JOB


def unknown(op: Any) -> ApiError:
    return ApiError(
        422, "unknown_operation", f"Неизвестная операция «{str(op)[:60]}».", "Выберите операцию из списка.",
        {"operations": list(OPERATIONS)},
    )  # fmt: skip


def require_closed(wf: Wf, what: str = "Каркас") -> None:
    s = wf.stats
    if s.get("closed"):
        return
    opened, nonmanifold = int(s.get("open_edges") or 0), int(s.get("nonmanifold_edges") or 0)
    if opened:
        detail = f"открытых рёбер — {opened}"
    elif nonmanifold:
        detail = f"рёбер с тремя и более треугольниками — {nonmanifold}"
    else:
        detail = "нет треугольников" if not wf.triangles else "каркас не проверен"
    raise ApiError(
        422, "wireframe_open", f"{what} «{wf.name}» не замкнут: {detail}.", FIX_HINT,
        {"wireframe_id": wf.id, "name": wf.name, "open_edges": opened, "nonmanifold_edges": nonmanifold,
         "fix": {"op": "repair", "wireframe_id": wf.id}},
    )  # fmt: skip


def require_open(wf: Wf, what: str = "Поверхность") -> None:
    if wf.stats.get("closed"):
        raise ApiError(
            422, "wireframe_closed", f"{what} «{wf.name}» — замкнутое тело, а нужна незамкнутая поверхность.",
            "Выберите ЦТМ или поверхность разлома.", {"wireframe_id": wf.id},
        )  # fmt: skip


def _distinct(a: Wf, b: Wf, message: str) -> None:
    if a.id == b.id:
        raise P.bad("same_source", message, "Выберите разные объекты.")


# --- the operations ---------------------------------------------------------------------------------------

Prepare = Callable[[Sources, dict[str, Any]], dict[str, Any]]


def _contours(src: Sources, raw: dict[str, Any], label: str) -> tuple[list[dict[str, Any]], int, int]:
    """[{layer_id, lines}] → the same checked; (refs, lines picked, closed among them)."""
    refs = raw.get("contours")
    if not isinstance(refs, list) or not refs:
        raise P.bad("no_contours", f"Не выбраны {label}.", "Укажите слой линий или линии в нём.")
    if len(refs) > P.MAX_REFS:
        raise P.bad("too_many_sources", f"Слоёв больше {P.MAX_REFS}.", "Выберите меньше слоёв.")
    out, lines, closed = [], 0, 0
    for k, ref in enumerate(refs):
        if not isinstance(ref, dict):
            raise P.bad("bad_ref", f"{label}: неверная ссылка на слой.", "Выберите слой заново.")
        lids = P.lid_list(ref.get("lines"), label)
        layer = src.layer(ref.get("layer_id"), f"contours[{k}]", lids)
        out.append({"layer_id": layer.id, "lines": lids})
        lines += len(layer.picked)
        closed += int(layer.closed.sum())
    return out, lines, closed


def _polygon(src: Sources, raw: Any, role: str, what: str) -> dict[str, Any] | None:
    """{layer_id, line}: one closed line (`line` — its `lid`; null — the only closed line of the layer)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise P.bad("bad_ref", f"{what}: неверная ссылка на линию.", "Выберите полигон заново.")
    line = raw.get("line")
    lids = None if line is None else P.lid_list([line], what)
    layer = src.layer(raw.get("layer_id"), role, lids)
    shut = int(layer.closed.sum())
    if shut != 1:
        found = "нет замкнутой линии" if not shut else f"замкнутых линий {shut}"
        raise P.bad(
            "bad_polygon", f"{what}: в слое «{layer.name}» должна быть выбрана одна замкнутая линия ({found}).",
            "Выберите одну замкнутую линию — полигон.",
        )  # fmt: skip
    return {"layer_id": layer.id, "line": int(layer.lids[layer.closed.astype(bool)][0])}


def _stitch(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    refs, _lines, closed = _contours(src, raw, "контуры")
    if closed < 2:
        raise P.bad(
            "too_few_contours", f"Для сшивки нужно не меньше двух замкнутых контуров, выбрано {closed}.",
            "Замкните линии в редакторе («Замкнуть») или выберите другие.",
        )  # fmt: skip
    return {
        "contours": refs,
        "start_cap": P.choice(raw, "start_cap", "Начальный торец", CAPS, "flat"),
        "end_cap": P.choice(raw, "end_cap", "Конечный торец", CAPS, "flat"),
        "cap_distance": P.number(raw, "cap_distance", "Длина торца", 0.5, positive=True),
    }


def _extrude(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    refs, _lines, closed = _contours(src, raw, "полигоны")
    if not closed:
        raise P.bad(
            "no_polygon", "Для выдавливания нужен замкнутый полигон: выбранные линии не замкнуты.",
            "Замкните линию в редакторе или выберите другую.",
        )  # fmt: skip
    vector = P.vector(raw, "vector", "Вектор", nonzero=True)
    default = "vector" if vector is not None else "vertical" if raw.get("vertical") else "normal"
    direction = P.choice(raw, "direction", "Направление", ("normal", "vertical", "vector"), default)
    if direction == "vector" and vector is None:
        raise P.bad("param_missing", "Не задан вектор выдавливания.", "Введите три числа X; Y; Z.", field="vector")
    return {
        "contours": refs,
        "distance": P.number(raw, "distance", "Расстояние", required=True, nonzero=True),
        "direction": direction,
        "vector": vector if direction == "vector" else None,
        "both_sides": P.flag(raw, "both_sides", False),
    }


def _edit_triangles(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    wf = src.wireframe(raw.get("wireframe_id"), "wireframe")
    remove = raw.get("remove") or []
    add = raw.get("add") or []
    if not isinstance(remove, list) or not isinstance(add, list) or len(remove) + len(add) > 1_000_000:
        raise P.bad("bad_edit", "Неверный список треугольников.", "Выберите треугольники заново.")
    if not remove and not add:
        raise P.bad("empty_edit", "Правка пуста: треугольники не отмечены и не добавлены.", "Отметьте треугольники.")
    drop = sorted({P.integer({"i": i}, "i", "Номер треугольника", lo=0) or 0 for i in remove})
    if drop and drop[-1] >= wf.triangles:
        raise P.bad(
            "stale_triangles", f"В каркасе «{wf.name}» нет выбранного треугольника: каркас изменился.",
            "Выберите треугольники заново.",
        )  # fmt: skip
    corners: list[list[list[float]]] = []
    for t in add:
        if not isinstance(t, list) or len(t) != 3:
            raise P.bad("bad_edit", "Новый треугольник — три вершины X, Y, Z.", "Добавьте треугольник заново.")
        corners.append([P.vector({"v": c}, "v", "Вершина треугольника") or [] for c in t])
    return {"wireframe_id": wf.id, "remove": drop, "add": corners}


def _repair(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    wf = src.wireframe(raw.get("wireframe_id"), "wireframe")
    out: dict[str, Any] = {"wireframe_id": wf.id, "weld_on": P.flag(raw, "weld_on", True)}
    out["weld"] = P.number(raw, "weld", "Допуск сшивки", 1e-6, nonnegative=True)
    for key in ("drop_duplicate", "drop_degenerate", "fix_nonmanifold", "fill_holes", "orient"):
        out[key] = P.flag(raw, key, True)
    return out


def _simplify(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    wf = src.wireframe(raw.get("wireframe_id"), "wireframe")
    hi = max(4, wf.triangles - 1)
    target = P.integer(raw, "target_triangles", "Число треугольников", lo=4, hi=hi)
    tolerance = P.number(raw, "tolerance", "Допуск отклонения", positive=True)
    if target is None and tolerance is None:
        raise P.bad(
            "param_missing", "Задайте число треугольников или допуск отклонения.", "Заполните одно из двух полей."
        )
    return {"wireframe_id": wf.id, "target_triangles": target, "tolerance": tolerance}


def _smooth(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    wf = src.wireframe(raw.get("wireframe_id"), "wireframe")
    strength = P.number(raw, "strength", "Сила", 0.5, positive=True)
    if strength is not None and strength > 0.95:
        raise P.bad("bad_param", "«Сила» должна быть от 0 до 0,95.", "Введите число не больше 0,95.", field="strength")
    return {
        "wireframe_id": wf.id,
        "iterations": P.integer(raw, "iterations", "Проходов", 10, lo=1, hi=10_000),
        "strength": strength,
        "keep_boundary": P.flag(raw, "keep_boundary", True),
    }


def _merge(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    ids = P.id_list(raw.get("wireframe_ids"), "Каркасы", least=2)
    wfs = [src.wireframe(i, f"wireframes[{k}]") for k, i in enumerate(ids)]
    return {"wireframe_ids": [w.id for w in wfs]}


def _split(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    wf = src.wireframe(raw.get("wireframe_id"), "wireframe")
    return {"wireframe_id": wf.id, "max_parts": P.integer(raw, "max_parts", "Не больше частей", 200, lo=2, hi=2000)}


def _boolean(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    kind = P.choice(raw, "op", "Действие", ("union", "intersection", "difference"), "difference")
    b = raw.get("b")
    many = isinstance(b, list)
    rest: list[Any] = list(b) if isinstance(b, list) else [b]
    ids = P.id_list([raw.get("a"), *rest], "Тела", least=2)
    wfs = [src.wireframe(i, "a" if k == 0 else (f"b[{k - 1}]" if many else "b"), "тело") for k, i in enumerate(ids)]
    for w in wfs:
        require_closed(w)
    return {"op": kind, "a": wfs[0].id, "b": [w.id for w in wfs[1:]] if many else wfs[1].id}


_KEEP_PLANE = ("positive", "negative", "both")


def _clip_plane(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    wf = src.wireframe(raw.get("wireframe_id"), "wireframe")
    point = P.vector(raw, "point", "Точка плоскости")
    normal = P.vector(raw, "normal", "Нормаль", nonzero=True)
    if point is None or normal is None:
        raise P.bad("param_missing", "Плоскость не задана: нужны точка и нормаль.", "Введите точку и нормаль.")
    return {"wireframe_id": wf.id, "point": point, "normal": normal,
            "keep": P.choice(raw, "keep", "Оставить", _KEEP_PLANE, "both")}  # fmt: skip


def _clip_polygon(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    wf = src.wireframe(raw.get("wireframe_id"), "wireframe")
    polygon = _polygon(src, raw.get("polygon"), "polygon", "Полигон обрезки")
    if polygon is None:
        raise P.bad("param_missing", "Не выбран полигон обрезки.", "Выберите замкнутую линию.", field="polygon")
    keep = P.choice(raw, "keep", "Оставить", ("inside", "outside", "both"), "inside")
    return {"wireframe_id": wf.id, "polygon": polygon, "keep": keep}


def _clip_surface(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    wf = src.wireframe(raw.get("wireframe_id"), "wireframe")
    surface = src.wireframe(raw.get("surface_id"), "surface", "поверхность")
    _distinct(wf, surface, "Каркас и секущая поверхность — один и тот же объект.")
    require_open(surface)
    return {
        "wireframe_id": wf.id,
        "surface_id": surface.id,
        "keep": P.choice(raw, "keep", "Оставить", ("above", "below", "both"), "both"),
        "direction": P.choice(raw, "direction", "Направление", ("auto", "normal"), "auto"),
    }


def _intersection_lines(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    a, b = src.wireframe(raw.get("a"), "a"), src.wireframe(raw.get("b"), "b")
    _distinct(a, b, "Выберите два разных каркаса.")
    return {"a": a.id, "b": b.id}


def _contour_lines(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    wf = src.wireframe(raw.get("wireframe_id"), "wireframe", "поверхность")
    interval = P.number(raw, "interval", "Шаг", required=True, positive=True)
    assert interval is not None
    if wf.extent and wf.extent[2] is not None and wf.extent[5] is not None:
        levels = (float(wf.extent[5]) - float(wf.extent[2])) / interval
        if levels > MAX_LEVELS:
            raise P.bad(
                "too_many_levels", f"Слишком мелкий шаг: уровней получилось бы {int(levels)}.",
                f"Увеличьте шаг: уровней должно быть не больше {MAX_LEVELS}.", field="interval",
            )  # fmt: skip
    return {
        "wireframe_id": wf.id,
        "interval": interval,
        "base": P.number(raw, "base", "Отсчёт от", 0.0),
        "major_every": P.integer(raw, "major_every", "Основная — каждая", 5, lo=0, hi=100_000),
    }


def _volume(src: Sources, raw: dict[str, Any]) -> dict[str, Any]:
    top = src.wireframe(raw.get("top"), "top", "верхняя поверхность")
    require_open(top, "Верхняя поверхность")
    level = P.number(raw, "level", "Уровень Z")
    mode = P.choice(raw, "mode", "Снизу", ("surface", "level"), "level" if level is not None else "surface")
    out: dict[str, Any] = {"top": top.id, "bottom": None, "level": None}
    if mode == "level":
        if level is None:
            raise P.bad("param_missing", "Не задан уровень Z.", "Введите отметку уровня.", field="level")
        out["level"] = level
    else:
        bottom = src.wireframe(raw.get("bottom"), "bottom", "нижняя поверхность")
        _distinct(top, bottom, "Выберите две разные поверхности.")
        require_open(bottom, "Нижняя поверхность")
        out["bottom"] = bottom.id
    out["polygon"] = _polygon(src, raw.get("polygon"), "polygon", "Полигон")
    out["report_only"] = P.flag(raw, "report_only", False)
    return out


PREPARE: dict[str, Prepare] = {
    "stitch": _stitch,
    "extrude": _extrude,
    "edit_triangles": _edit_triangles,
    "repair": _repair,
    "simplify": _simplify,
    "smooth": _smooth,
    "merge": _merge,
    "split": _split,
    "boolean": _boolean,
    "clip_plane": _clip_plane,
    "clip_polygon": _clip_polygon,
    "clip_surface": _clip_surface,
    "intersection_lines": _intersection_lines,
    "contours": _contour_lines,
    "volume_between": _volume,
}


# --- «Построить ЦТМ»: the job model.dtm -------------------------------------------------------------------


def _dtm(conn: sa.Connection, store: BlobStore, raw: dict[str, Any], name: str | None) -> Checked:
    from bf_core.wireframes import jobs as dtm_jobs

    src = Sources(conn, store)
    boundary = raw.get("boundary")
    ref = None
    if boundary is not None:  # `line` is a `lid` here, a position in the job
        ref = _polygon(src, boundary, "boundary", "Граница")
        assert ref is not None
        pos = int(src.layers["boundary"].picked[src.layers["boundary"].closed.astype(bool)][0])
        ref = {"layer_id": ref["layer_id"], "line": pos, "use_z": bool(boundary.get("use_z"))}
    job = dtm_jobs.request(
        conn, name=name, layer_ids=raw.get("layers", raw.get("layer_ids")), set_ids=raw.get("set_ids"),
        clouds=raw.get("clouds"), breaklines=P.flag(raw, "breaklines", True), boundary=ref,
        max_edge=P.number(raw, "max_edge", "Наибольшая длина ребра", positive=True),
        to_dtm=P.flag(raw, "to_dtm", True), color=raw.get("color"), dz=P.flag(raw, "dz", True),
    )  # fmt: skip
    return Checked("dtm", OPERATIONS["dtm"]["title"], job, src.used, src.folder, job="model.dtm", job_params=job)


# --- the check ---------------------------------------------------------------------------------------------


def precheck(
    conn: sa.Connection,
    store: BlobStore,
    op: Any,
    params: Any,
    *,
    known: Mapping[str, str] | None = None,
    name: str | None = None,
) -> Checked:
    """The operation `op` with `params` checked (see the module docstring); ApiError 422 otherwise. `known` —
    names of the sources by role (a rebuild: a source gone is named)."""
    if not isinstance(op, str) or op not in OPERATIONS:
        raise unknown(op)
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise P.bad("bad_params", "Параметры операции заданы неверно.", "Откройте окно операции заново.")
    if op == "dtm":
        return _dtm(conn, store, params, name)
    src = Sources(conn, store, known)
    checked = PREPARE[op](src, params)
    # vertices of the lines picked: the share of the vertices of the layer (the offsets are not read here)
    vertices = sum(
        int(layer.point_count * len(layer.picked) / max(1, layer.line_count)) for layer in src.layers.values()
    )
    out = Checked(op, OPERATIONS[op]["title"], checked, src.used, src.folder)
    out.triangles = sum(int(u.get("triangles") or 0) for u in src.used if u["kind"] == "wireframe")
    out.vertices = vertices
    if out.vertices > MAX_CONTOUR_VERTICES:
        raise P.bad("too_large", "Слишком много вершин в выбранных линиях.", "Выберите меньше линий.")
    return out


def memory_mb(params: dict[str, Any]) -> int:
    """About 1 KB a triangle of the input at the peak (booleans 2 × 1 million — 1.2 GB, volume — 2 GB)."""
    size = int(params.get("size") or 0)
    factor = 1100 if params.get("op") in ("volume_between", "clip_surface", "clip_polygon") else 700
    return max(256, int(size * factor / 2**20) + 200)
