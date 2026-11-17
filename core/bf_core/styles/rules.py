"""Checks of a style (ТЗ 7.16; ported from BF routers/styles.py). The interface owns the shape of `body`; the
core checks sizes, the classes and the category colours in it. Texts are in Russian."""

from __future__ import annotations

import json
import math
import re
from typing import Any

LEVELS = ("palette", "layer", "display")
DATA_KINDS = ("number", "text", "event", "mixed")
LEVEL_TITLES = {"palette": "Раскраска", "layer": "Стиль слоя", "display": "Шаблон отображения"}
MAX_NAME = 255
MAX_CLASSES = 40
MAX_LABEL = 64
MAX_BODY_BYTES = 256 * 1024
MAX_EXTRA_BYTES = 64 * 1024
MAX_DEFAULT_FOR = 50
MAX_FIELD_NAME = 64
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _check_classes(classes: Any, where: str) -> str | None:
    """Colours, at most 40 classes, ascending bounds; only the first class may go without a lower bound."""
    if not isinstance(classes, list):
        return f"{where}: классы должны быть списком"
    if len(classes) > MAX_CLASSES:
        return f"{where}: не больше {MAX_CLASSES} классов"
    bounds = []
    order = f"{where}: границы классов должны возрастать; без нижней границы может быть только первый класс"
    for i, c in enumerate(classes):
        n = i + 1
        if not isinstance(c, dict):
            return f"{where}: класс {n} должен быть объектом"
        bound = c.get("from")
        if bound is not None and not _is_number(bound):
            return f"{where}: граница класса {n} должна быть числом"
        color = c.get("color")
        if not isinstance(color, str) or not _COLOR.match(color):
            return f"{where}: цвет класса {n} должен быть в виде #rrggbb"
        label = c.get("label")
        if label is not None and (not isinstance(label, str) or len(label) > MAX_LABEL):
            return f"{where}: подпись класса {n} должна быть строкой не длиннее {MAX_LABEL} символов"
        if "hidden" in c and not isinstance(c["hidden"], bool):
            return f"{where}: признак «скрыт» класса {n} должен быть true или false"
        if bound is None and i > 0:
            return order
        if bound is not None:
            bounds.append(bound)
    if any(b <= a for a, b in zip(bounds, bounds[1:], strict=False)):
        return order
    return None


def _check_cat_colors(colors: Any, where: str) -> str | None:
    if not isinstance(colors, dict):
        return f"{where}: цвета категорий должны быть объектом"
    for key, color in colors.items():
        if not isinstance(color, str) or not _COLOR.match(color):
            return f"{where}: цвет категории «{key}» должен быть в виде #rrggbb"
    return None


def _check_part(part: dict[str, Any], where: str) -> str | None:
    if "classes" in part and (error := _check_classes(part["classes"], where)):
        return error
    if "catColors" in part and (error := _check_cat_colors(part["catColors"], where)):
        return error
    return None


def body_error(body: Any) -> str | None:
    """What is wrong with the body of a style, or None."""
    if not isinstance(body, dict):
        return "содержимое стиля должно быть объектом"
    if json_size(body) > MAX_BODY_BYTES:
        return f"стиль слишком большой (больше {MAX_BODY_BYTES // 1024} КБ)"
    if error := _check_part(body, "раскраска"):
        return error
    tracks = body.get("tracks")
    if isinstance(tracks, list):
        for i, track in enumerate(tracks):
            if isinstance(track, dict) and (error := _check_part(track, f"слой {i + 1}")):
                return error
    return None


def default_for_error(value: Any) -> str | None:
    if not isinstance(value, list):
        return "список полей по умолчанию должен быть списком строк"
    if len(value) > MAX_DEFAULT_FOR:
        return f"не больше {MAX_DEFAULT_FOR} полей по умолчанию"
    if any(not isinstance(v, str) or not v.strip() or len(v) > MAX_FIELD_NAME for v in value):
        return f"имена полей по умолчанию должны быть непустыми строками не длиннее {MAX_FIELD_NAME} символов"
    return None


def name_error(name: Any) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return "нужно название стиля"
    if len(name.strip()) > MAX_NAME:
        return f"название стиля не длиннее {MAX_NAME} символов"
    return None
