"""The operations as the windows see them (ТЗ 7.23; ADR 0015 §6 «Команды»): `GET /operations` gives this list,
and `modules/model/opForms.ts` builds one window per `dialog` from the fields.

A field: {key, label, type, required?, default?, options? [{value, label}], kinds?, min?, max?, when?, unit?}.
Types: `wireframe` / `wireframes` (an object id / ids; `kinds` — «closed» or «open» limits the choice),
`lines` (contours: [{layer_id, lines}] — `lines` are `lid`s, null — every line of the layer), `line`
({layer_id, line}: one closed line; `line` null — the only closed line of the layer), `layers` and `sets`
(ids), `number`, `integer`, `bool`, `choice`, `vector` (three numbers), `triangles` (picked in the 3D window).
`when` — {key: value}: the field is shown and sent only while that other field has that value. Pure data: no
numpy, no database.
"""

from __future__ import annotations

from typing import Any

CAPS = [
    {"value": "flat", "label": "Плоская крышка"},
    {"value": "point", "label": "В точку"},
    {"value": "line", "label": "В линию (гребень)"},
    {"value": "extend", "label": "Продолжение контура"},
    {"value": "none", "label": "Открыто"},
]

MESH = "mesh"  # makes wireframes
LINES = "lines"  # makes a line file with layers
REPORT = "report"  # a report, and wireframes unless «только отчёт»


def _f(key: str, label: str, type_: str, **more: Any) -> dict[str, Any]:
    return {"key": key, "label": label, "type": type_, **more}


WF = _f("wireframe_id", "Каркас", "wireframe", required=True)

OPERATIONS: dict[str, dict[str, Any]] = {
    "stitch": {
        "title": "Сшить контуры",
        "dialog": "stitch",
        "result": MESH,
        "fields": [
            _f("contours", "Контуры и тай-линии", "lines", required=True),
            _f("start_cap", "Начальный торец", "choice", options=CAPS, default="flat"),
            _f("end_cap", "Конечный торец", "choice", options=CAPS, default="flat"),
            _f("cap_distance", "Длина торца, доля шага разрезов", "number", default=0.5, min=0),
        ],
    },
    "extrude": {
        "title": "Выдавить",
        "dialog": "extrude",
        "result": MESH,
        "fields": [
            _f("contours", "Полигоны", "lines", required=True),
            _f("distance", "Расстояние", "number", required=True, unit="м"),
            _f(
                "direction",
                "Направление",
                "choice",
                default="normal",
                options=[
                    {"value": "normal", "label": "По нормали полигона"},
                    {"value": "vertical", "label": "По вертикали"},
                    {"value": "vector", "label": "По вектору"},
                ],
            ),  # fmt: skip
            _f("vector", "Вектор X; Y; Z", "vector", when={"direction": "vector"}),
            _f("both_sides", "В обе стороны", "bool", default=False),
        ],
    },
    "edit_triangles": {
        "title": "Править треугольники",
        "dialog": None,  # the panel of the edit mode (9.33)
        "result": MESH,
        "fields": [
            WF,
            _f("remove", "Удалить треугольники", "triangles"),
            _f("add", "Добавить треугольники (по три вершины)", "triangles"),
        ],
    },
    "repair": {
        "title": "Исправить каркас",
        "dialog": "repair",
        "result": MESH,
        "fields": [
            WF,
            _f("weld_on", "Сшить совпадающие вершины", "bool", default=True),
            _f("weld", "Допуск сшивки", "number", default=1e-6, min=0, unit="м", when={"weld_on": True}),
            _f("drop_duplicate", "Удалить повторные треугольники", "bool", default=True),
            _f("drop_degenerate", "Удалить вырожденные треугольники", "bool", default=True),
            _f("fix_nonmanifold", "Исправить рёбра с тремя и более треугольниками", "bool", default=True),
            _f("fill_holes", "Закрыть отверстия", "bool", default=True),
            _f("orient", "Согласовать нормали", "bool", default=True),
        ],
    },
    "simplify": {
        "title": "Упростить",
        "dialog": "simplify",
        "result": MESH,
        "fields": [
            WF,
            _f("target_triangles", "Число треугольников", "integer", min=4),
            _f("tolerance", "Допуск отклонения", "number", min=0, unit="м"),
        ],
    },
    "smooth": {
        "title": "Сгладить",
        "dialog": "smooth",
        "result": MESH,
        "fields": [
            WF,
            _f("iterations", "Проходов", "integer", default=10, min=1, max=10_000),
            _f("strength", "Сила", "number", default=0.5, min=0, max=0.95),
            _f("keep_boundary", "Граница неподвижна", "bool", default=True),
        ],
    },
    "merge": {
        "title": "В один объект",
        "dialog": "merge",
        "result": MESH,
        "fields": [_f("wireframe_ids", "Каркасы", "wireframes", required=True, min=2)],
    },
    "split": {
        "title": "На части",
        "dialog": "split",
        "result": MESH,
        "fields": [WF, _f("max_parts", "Не больше частей", "integer", default=200, min=2, max=2000)],
    },
    "boolean": {
        "title": "Булева операция",
        "dialog": "boolean",
        "result": MESH,
        "fields": [
            _f(
                "op",
                "Действие",
                "choice",
                default="difference",
                options=[
                    {"value": "union", "label": "Объединение"},
                    {"value": "intersection", "label": "Пересечение"},
                    {"value": "difference", "label": "Вычитание (A − B)"},
                ],
            ),  # fmt: skip
            _f("a", "Тело A", "wireframe", required=True, kinds=["closed"]),
            _f("b", "Тело B (одно или несколько)", "wireframes", required=True, kinds=["closed"], min=1),
        ],
    },
    "clip_plane": {
        "title": "Обрезать плоскостью",
        "dialog": "clip",
        "result": MESH,
        "fields": [
            WF,
            _f("point", "Точка плоскости X; Y; Z", "vector", required=True),
            _f("normal", "Нормаль X; Y; Z", "vector", required=True, default=[0.0, 0.0, 1.0]),
            _f(
                "keep",
                "Оставить",
                "choice",
                default="both",
                options=[
                    {"value": "positive", "label": "По нормали"},
                    {"value": "negative", "label": "Против нормали"},
                    {"value": "both", "label": "Обе части"},
                ],
            ),  # fmt: skip
        ],
    },
    "clip_polygon": {
        "title": "Обрезать полигоном",
        "dialog": "clip",
        "result": MESH,
        "fields": [
            WF,
            _f("polygon", "Полигон в плане", "line", required=True),
            _f(
                "keep",
                "Оставить",
                "choice",
                default="inside",
                options=[
                    {"value": "inside", "label": "Внутри"},
                    {"value": "outside", "label": "Снаружи"},
                    {"value": "both", "label": "Обе части"},
                ],
            ),  # fmt: skip
        ],
    },
    "clip_surface": {
        "title": "Обрезать поверхностью",
        "dialog": "clip",
        "result": MESH,
        "fields": [
            WF,
            _f("surface_id", "Поверхность (ЦТМ, разлом)", "wireframe", required=True, kinds=["open"]),
            _f(
                "keep",
                "Оставить",
                "choice",
                default="both",
                options=[
                    {"value": "above", "label": "Над поверхностью"},
                    {"value": "below", "label": "Под поверхностью"},
                    {"value": "both", "label": "Обе части"},
                ],
            ),  # fmt: skip
            _f(
                "direction",
                "Направление",
                "choice",
                default="auto",
                options=[
                    {"value": "auto", "label": "Вверх (или по нормали, если поверхность крутая)"},
                    {"value": "normal", "label": "По нормали поверхности"},
                ],
            ),  # fmt: skip
        ],
    },
    "intersection_lines": {
        "title": "Линии пересечения",
        "dialog": "intersection_lines",
        "result": LINES,
        "fields": [
            _f("a", "Каркас A", "wireframe", required=True),
            _f("b", "Каркас B", "wireframe", required=True),
        ],
    },
    "dtm": {
        "title": "Построить ЦТМ",
        "dialog": "dtm",
        "result": MESH,
        "job": "model.dtm",  # the job of «Построить ЦТМ» (ADR 0011, 0014; decision 2)
        "fields": [
            _f("layers", "Слои точек и линий", "layers"),
            _f("set_ids", "Устья наборов скважин", "sets"),
            _f("breaklines", "Линии — структурные", "bool", default=True),
            _f("boundary", "Граница", "line"),
            _f("max_edge", "Наибольшая длина ребра", "number", min=0, unit="м"),
        ],
    },
    "contours": {
        "title": "Изолинии",
        "dialog": "contours",
        "result": LINES,
        "fields": [
            WF,
            _f("interval", "Шаг", "number", required=True, min=0, unit="м"),
            _f("base", "Отсчёт от", "number", default=0.0, unit="м"),
            _f("major_every", "Основная — каждая", "integer", default=5, min=0),
        ],
    },
    "volume_between": {
        "title": "Объём",
        "dialog": "volume_between",
        "result": REPORT,
        "fields": [
            _f("top", "Верхняя поверхность", "wireframe", required=True, kinds=["open"]),
            _f(
                "mode",
                "Снизу",
                "choice",
                default="surface",
                options=[
                    {"value": "surface", "label": "Поверхность"},
                    {"value": "level", "label": "Уровень"},
                ],
            ),  # fmt: skip
            _f("bottom", "Нижняя поверхность", "wireframe", kinds=["open"], when={"mode": "surface"}),
            _f("level", "Уровень Z", "number", unit="м", when={"mode": "level"}),
            _f("polygon", "Полигон в плане", "line"),
            _f("report_only", "Только отчёт", "bool", default=False),
        ],
    },
}


def listing() -> list[dict[str, Any]]:
    """`GET /operations`: [{op, title, dialog, result, job, fields}] in the order of the ribbon."""
    return [{"op": op, "job": spec.get("job", "geometry.operation"), **spec} for op, spec in OPERATIONS.items()]
