"""Kinds of saved charts and the check of their settings (ADR 0019 §2, ADR 0007 §3).

Every chart is `objects(kind="chart")` + a `charts` row; no migration. A chart of a drillhole set lies under the
set (`parent_id = <set>`): `striplog` (Э6) and the charts of Э13 — histogram, Q-Q, dendrogram and those of 8.3.
A swath plot is an object of the project (`parent_id = NULL`), at most `MAX_SWATHS` of them.

The core checks the kind, the version `v`, the size, the lists (series ≤ 12, dendrogram fields ≤ 100, holes
≤ 10 000) and the ids of other objects; the shape of the other fields is the interface's (`withDefaults`, as for
`striplog`). A table is referred to by its `name_key`, fields and holes by their names (they live through
a table replaced and a `.bfggis`); other objects — by id: `y_set_id`, `wireframe_id`, `series[].set_id`,
`series[].model_id`. Such an id is a positive integer or null (the source is gone: a series «источник удалён»).
`map_ids` renumbers them on a `.bfggis` import (`archive/chart_remap.py`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..styles.rules import json_size

STRIPLOG = "striplog"
SWATH = "swath"
#: kinds of the charts of a drillhole set (under the set in the tree, «Графики»)
SET_KINDS = (STRIPLOG, "histogram", "qq", "dendrogram", "scatter", "box", "probplot", "contact", "ternary", "domains")
#: kinds of the charts of the project («Аудит и статистика»)
PROJECT_KINDS = (SWATH,)
KINDS = SET_KINDS + PROJECT_KINDS

VERSION = 1
MAX_SETTINGS_BYTES = 64 * 1024
STRIPLOG_MAX_BYTES = 256 * 1024
MAX_TRACKS = 32  # striplog layers
MAX_SERIES = 12
MAX_FIELDS = 100
MAX_HOLES = 10_000
MAX_SWATHS = 200
MAX_NAME = 200  # a name of a table, a field or a hole inside the settings

#: top-level keys holding the id of another object
ID_KEYS = ("y_set_id", "wireframe_id")
#: keys of a series (swath) holding the id of another object
SERIES_ID_KEYS = ("set_id", "model_id", "filter_id")
#: lists of names: key → (limit, what it is called in a message)
NAME_LISTS: dict[str, tuple[int, str]] = {
    "fields": (MAX_FIELDS, "полей"),
    "holes": (MAX_HOLES, "скважин"),
    "y_holes": (MAX_HOLES, "скважин стороны Y"),
}
#: keys holding one name
NAME_KEYS = ("table", "y_table", "domain_table")

TITLES = {
    STRIPLOG: "Отображение по стволу",
    "histogram": "Гистограмма",
    "qq": "Q-Q график",
    "dendrogram": "Дендрограмма",
    "scatter": "Рассеяние",
    "box": "Ящики с усами",
    "probplot": "Лог-вероятностный график",
    "contact": "Контактный анализ",
    "ternary": "Тернарная диаграмма",
    "domains": "Статистика по доменам",
    SWATH: "Swath plot",
}


class KindError(ValueError):
    """422 with a Russian text; `code` — `bad_chart` or `too_many_series`, `param` — the key at fault."""

    def __init__(self, message: str, *, code: str = "bad_chart", param: str | None = None) -> None:
        super().__init__(message)
        self.code, self.param = code, param


def is_id(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 < v < 10**12


def _id(v: Any, param: str) -> None:
    if v is not None and not is_id(v):
        raise KindError(f"Настройки графика: «{param}» — номер объекта или пусто.", param=param)


def _name(v: Any, param: str) -> None:
    if v is not None and not (isinstance(v, str) and 0 < len(v) <= MAX_NAME):
        raise KindError(f"Настройки графика: «{param}» — название до {MAX_NAME} символов.", param=param)


def _names(v: Any, param: str, limit: int, what: str) -> None:
    if v is None:
        return
    if not isinstance(v, list) or any(not (isinstance(x, str) and 0 < len(x) <= MAX_NAME) for x in v):
        raise KindError(f"Настройки графика: «{param}» — список названий.", param=param)
    if len(v) > limit:
        raise KindError(f"Настройки графика: не больше {limit} {what}.", param=param)


def _series(v: Any) -> None:
    if v is None:
        return
    if not isinstance(v, list) or any(not isinstance(s, dict) for s in v):
        raise KindError("Ряды swath plot должны быть списком.", param="series")
    if len(v) > MAX_SERIES:
        raise KindError(f"В swath plot не больше {MAX_SERIES} рядов.", code="too_many_series", param="series")
    for k, s in enumerate(v):
        for key in SERIES_ID_KEYS:
            _id(s.get(key), f"series[{k}].{key}")


def check(kind: str, settings: Any) -> None:
    """Raises KindError when the settings of a chart of `kind` cannot be stored."""
    if kind not in KINDS:
        raise KindError(f"Неизвестный вид графика «{str(kind)[:40]}».", param="kind")
    if kind == STRIPLOG:
        _check_striplog(settings)
        return
    if not isinstance(settings, dict):
        raise KindError("Настройки графика должны быть объектом.", param="settings")
    v = settings.get("v")
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise KindError("Настройки графика: нужна версия v = 1.", param="v")
    if v > VERSION:
        raise KindError("Настройки графика записаны более новой версией BedrockForge: обновите программу.", param="v")
    for key in ID_KEYS:
        _id(settings.get(key), key)
    for key in NAME_KEYS:
        _name(settings.get(key), key)
    for key, (limit, what) in NAME_LISTS.items():
        _names(settings.get(key), key, limit, what)
    if "series" in settings or kind == SWATH:
        _series(settings.get("series"))
    if json_size(settings) > MAX_SETTINGS_BYTES:  # after the lists: a long list is named by its own message
        raise KindError("Настройки графика больше 64 КБ.", param="settings")


def _check_striplog(settings: Any) -> None:
    """The display along holes of Э6 (ADR 0007 §3): as it was — 256 КБ, ≤ 32 layers, no version required."""
    if not isinstance(settings, dict):
        raise KindError("Настройки отображения должны быть объектом.", param="settings")
    if json_size(settings) > STRIPLOG_MAX_BYTES:
        raise KindError("Настройки отображения больше 256 КБ.", param="settings")
    tracks = settings.get("tracks", [])
    if not isinstance(tracks, list) or any(not isinstance(t, dict) for t in tracks):
        raise KindError("Слои отображения должны быть списком.", param="tracks")
    if len(tracks) > MAX_TRACKS:
        raise KindError(f"В отображении не больше {MAX_TRACKS} слоёв.", param="tracks")


def object_ids(settings: Any) -> list[int]:
    """The ids of other objects inside the settings (nulls left out)."""
    if not isinstance(settings, dict):
        return []
    out = [settings[k] for k in ID_KEYS if is_id(settings.get(k))]
    series = settings.get("series")
    if isinstance(series, list):
        out += [s[k] for s in series if isinstance(s, dict) for k in SERIES_ID_KEYS if is_id(s.get(k))]
    return out


def map_ids(settings: Any, new_id: Callable[[int], int | None]) -> Any:
    """The settings with the ids of other objects renumbered (`.bfggis`); an id without a new one becomes null —
    the old number would point at another object of the new project. The rest (names included) stays as it is.
    Returns the settings themselves when nothing changes."""
    if not isinstance(settings, dict):
        return settings
    out = dict(settings)
    changed = False
    for k in ID_KEYS:
        if is_id(out.get(k)):
            n = new_id(out[k])
            changed |= n != out[k]
            out[k] = n
    series = out.get("series")
    if isinstance(series, list):
        new_series: list[Any] = []
        for s in series:
            if isinstance(s, dict) and any(is_id(s.get(k)) for k in SERIES_ID_KEYS):
                s = dict(s)
                for k in SERIES_ID_KEYS:
                    if is_id(s.get(k)):
                        n = new_id(s[k])
                        changed |= n != s[k]
                        s[k] = n
            new_series.append(s)
        out["series"] = new_series
    return out if changed else settings
