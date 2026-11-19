"""Keys of the project view settings (ТЗ 7.0.10, ADR 0007 §2) and their normalization.

Reading is lenient: values are clamped into their limits, junk is dropped, unknown keys are ignored — an old
project never breaks. Writing is strict: a value that does not fit raises BadValue (422), except the size
limits of lists and maps, which keep the newest entries (as the interface does).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable
from typing import Any

SCOPE = "view"
MAX_BYTES = 512 * 1024
VEXAG = (1, 2, 3, 5)
HIDDEN_MAX = 2000
DISPLAYS_MAX = 200
SWATHS_MAX = 50
STYLES_MAX = 200
COLUMNS_TABLES = 200
COLUMNS_PER_TABLE = 500
SERIES_K_MAX = 5_000  # sections in a series (ADR 0016 §1)
SERIES_SEG_MAX = 1_000  # segments of a polyline section

#: `set:3`, `set:3:collars`, `wf:12`… (ТЗ 7.0.10). The id is an object id (renumbered by `.bfggis`).
HIDDEN_RE = re.compile(r"^(set|wf|ln|rs|bm):(\d{1,12})(?::(collars|paths|intervals))?$")
SET_TABLE_RE = re.compile(r"^(\d{1,12}):([^:]{1,64})$")


class BadValue(ValueError):
    """A value that cannot be stored; the text is in Russian and names the setting."""


def _fail(strict: bool, msg: str) -> None:
    if strict:
        raise BadValue(msg)


def _bool(v: Any, default: bool) -> bool:
    return v if isinstance(v, bool) else default


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _scene(v: Any, strict: bool) -> dict[str, Any]:
    d: dict[str, Any] = {"perspective": False, "grid": True, "vexag": 1, "wireframe_opacity": 0.7}
    if not isinstance(v, dict):
        _fail(strict, "Оформление сцены: ожидается набор параметров.")
        return d
    for k in ("perspective", "grid"):
        if k in v and not isinstance(v[k], bool):
            _fail(strict, f"Оформление сцены: «{k}» — да или нет.")
        d[k] = _bool(v.get(k), d[k])
    if "vexag" in v:
        if v["vexag"] in VEXAG and not isinstance(v["vexag"], bool):
            d["vexag"] = int(v["vexag"])
        else:
            _fail(strict, "Преувеличение по вертикали: 1, 2, 3 или 5.")
    if "wireframe_opacity" in v:
        op = _num(v["wireframe_opacity"])
        if op is not None and 0.1 <= op <= 1:
            d["wireframe_opacity"] = op
        else:
            _fail(strict, "Непрозрачность каркасов: от 0,1 до 1.")
    return d


def _line(v: Any, strict: bool) -> dict[str, Any] | None:
    if v is None:
        return None
    if not isinstance(v, dict):
        _fail(strict, "Линия разреза: ожидаются x0, y0, az.")
        return None
    x0, y0, az = _num(v.get("x0")), _num(v.get("y0")), _num(v.get("az"))
    if x0 is None or y0 is None or az is None:
        _fail(strict, "Линия разреза: x0, y0 и азимут — числа.")
        return None
    length = v.get("length")
    if length is not None:
        length = _num(length)
        if length is None or length <= 0:
            _fail(strict, "Линия разреза: длина больше нуля или без ограничения.")
            length = None
    out: dict[str, Any] = {"x0": x0, "y0": y0, "az": az % 360.0, "length": length}
    for k in ("towards", "away"):
        w = _num(v.get(k, 25.0))
        if w is None or w < 0:
            _fail(strict, "Окно разреза: «от» и «до» — не меньше нуля.")
            w = 25.0
        out[k] = w
    return out


def _id(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 10**12 else None


def _series(v: Any, strict: bool) -> dict[str, Any] | None:
    """The active series of sections (ADR 0016 §4): {id, k, seg} — the object id, the section from 0 and the
    segment of a polyline section (null for the others)."""
    if v is None:
        return None
    sid = _id(v.get("id")) if isinstance(v, dict) else None
    if sid is None:
        _fail(strict, "Серия разрезов: ожидается номер серии id.")
        return None
    k, seg = v.get("k", 0), v.get("seg")
    if isinstance(k, bool) or not isinstance(k, int) or not 0 <= k < SERIES_K_MAX:
        _fail(strict, "Серия разрезов: номер разреза k — целое от 0.")
        k = 0
    if seg is not None and (isinstance(seg, bool) or not isinstance(seg, int) or not 0 <= seg < SERIES_SEG_MAX):
        _fail(strict, "Серия разрезов: сегмент ломаного разреза — целое от 0.")
        seg = None
    return {"id": sid, "k": k, "seg": seg}


def _section(v: Any, strict: bool) -> dict[str, Any]:
    """{line, clip[, series]}: `line` — the last free section line, `series` — the active series, written only
    when there is one (a key of Э7 reads as it was)."""
    if not isinstance(v, dict):
        _fail(strict, "Разрез: ожидаются line и clip.")
        return {"line": None, "clip": False}
    line = _line(v.get("line"), strict)
    series = _series(v.get("series"), strict)
    clip = _bool(v.get("clip"), False) and (line is not None or series is not None)
    out: dict[str, Any] = {"line": line, "clip": bool(clip)}
    if series is not None:
        out["series"] = series
    return out


def _section_ids(value: dict[str, Any]) -> tuple[int, ...]:
    series = value.get("series")
    return (series["id"],) if series is not None else ()


def _section_map(value: dict[str, Any], new_id: Callable[[int], int | None]) -> dict[str, Any]:
    """The series renumbered or forgotten (a series that is gone: the free line stays; no line — no clip)."""
    series = value.get("series")
    if series is None:
        return value
    n = new_id(series["id"])
    if n is not None:
        return {**value, "series": {**series, "id": n}}
    out = {k: v for k, v in value.items() if k != "series"}
    out["clip"] = bool(out["clip"] and out["line"] is not None)
    return out


def _hidden(v: Any, strict: bool) -> list[str]:
    if not isinstance(v, list):
        _fail(strict, "Скрытые слои: ожидается список ключей.")
        return []
    out: list[str] = []
    seen: set[str] = set()
    for k in reversed(v):  # the newest are last; keep them when the list is cut
        if not isinstance(k, str) or not HIDDEN_RE.match(k):
            _fail(strict, f"Скрытые слои: неизвестный ключ «{str(k)[:40]}».")
            continue
        if k not in seen:
            seen.add(k)
            out.append(k)
        if len(out) == HIDDEN_MAX:
            break
    return out[::-1]


def _hide_all(v: Any, strict: bool) -> dict[str, Any]:
    """«Очистить окно» (ТЗ 7.35 Д-04, ADR 0008): while `on`, only the keys in `shown` are visible."""
    if not isinstance(v, dict):
        _fail(strict, "Очистка окна: ожидаются on и shown.")
        return {"on": False, "shown": []}
    if "on" in v and not isinstance(v["on"], bool):
        _fail(strict, "Очистка окна: «on» — да или нет.")
    on = _bool(v.get("on"), False)
    shown = _hidden(v.get("shown", []), strict) if on else []
    return {"on": on, "shown": shown}


def _displays(v: Any, strict: bool) -> list[dict[str, Any]]:
    if not isinstance(v, list):
        _fail(strict, "Отображения по стволу: ожидается список.")
        return []
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for d in v:
        chart = d.get("chart") if isinstance(d, dict) else None
        if isinstance(chart, bool) or not isinstance(chart, int) or chart <= 0:
            _fail(strict, "Отображения по стволу: у каждого — номер сохранённого отображения.")
            continue
        if chart not in seen:
            seen.add(chart)
            out.append({"chart": chart, "visible": _bool(d.get("visible"), True)})
    return out[-DISPLAYS_MAX:]


def _swaths(v: Any, strict: bool) -> list[Any]:
    if not isinstance(v, list):
        _fail(strict, "Swath plot: ожидается список.")
        return []
    return v[-SWATHS_MAX:]


def _styles(v: Any, strict: bool) -> dict[str, Any]:
    """{"<set id>": style}: the style body is checked by the interface (BF collarStyle/traceStyle)."""
    if not isinstance(v, dict):
        _fail(strict, "Стили: ожидается набор «набор → стиль».")
        return {}
    out: dict[str, Any] = {}
    for k, style in v.items():
        if not (isinstance(k, str) and k.isdigit() and len(k) <= 12) or not isinstance(style, dict):
            _fail(strict, f"Стили: неверная запись «{str(k)[:40]}».")
            continue
        out[k] = style
    return dict(list(out.items())[-STYLES_MAX:])  # insertion order: the oldest go first


def _columns(v: Any, strict: bool) -> dict[str, list[str]]:
    if not isinstance(v, dict):
        _fail(strict, "Открытые колонки: ожидается набор «набор:таблица → колонки».")
        return {}
    out: dict[str, list[str]] = {}
    for k, cols in v.items():
        if not isinstance(k, str) or not SET_TABLE_RE.match(k) or not isinstance(cols, list):
            _fail(strict, f"Открытые колонки: неверная запись «{str(k)[:40]}».")
            continue
        out[k] = [c for c in cols if isinstance(c, str) and len(c) <= 200][:COLUMNS_PER_TABLE]
    return dict(list(out.items())[-COLUMNS_TABLES:])


def _active(v: Any, strict: bool) -> dict[str, Any]:
    d: dict[str, Any] = {"set": None, "hole": None, "table": None}
    if not isinstance(v, dict):
        _fail(strict, "Активный набор: ожидаются set, hole, table.")
        return d
    s = v.get("set")
    d["set"] = s if isinstance(s, int) and not isinstance(s, bool) and s > 0 else None
    for k in ("hole", "table"):
        t = v.get(k)
        d[k] = t if isinstance(t, str) and 0 < len(t) <= 200 else None
    return d


NORMALIZERS: dict[str, Callable[[Any, bool], Any]] = {
    "scene": _scene,
    "section": _section,
    "hidden": _hidden,
    "hide_all": _hide_all,
    "displays": _displays,
    "swaths": _swaths,
    "collar_styles": _styles,
    "trace_styles": _styles,
    "block_styles": _styles,
    "cloud_styles": _styles,  # {"<layer id>": {color, hidden: [codes]}} (ADR 0014; body checked by the interface)
    "columns": _columns,
    "active": _active,
}


def normalize(key: str, value: Any, *, strict: bool) -> Any:
    fn = NORMALIZERS.get(key)
    if fn is None:
        raise BadValue(f"Неизвестная настройка вида «{key[:40]}».")
    if strict and len(json.dumps(value, ensure_ascii=False)) > MAX_BYTES:
        raise BadValue("Настройка вида больше 512 КБ.")
    return fn(value, strict)


def drop_missing(key: str, value: Any, alive: Callable[[int], bool]) -> Any:
    """Forget ids of deleted objects (read side): hidden keys, displays, styles, columns, the active set, the
    active series of sections."""
    if key == "hidden":
        return [k for k in value if alive(int(HIDDEN_RE.match(k).group(2)))]  # type: ignore[union-attr]
    if key == "hide_all":
        return {**value, "shown": drop_missing("hidden", value["shown"], alive)}
    if key == "displays":
        return [d for d in value if alive(d["chart"])]
    if key in ("collar_styles", "trace_styles", "block_styles", "cloud_styles"):
        return {k: s for k, s in value.items() if alive(int(k))}
    if key == "columns":
        return {k: c for k, c in value.items() if alive(int(k.split(":", 1)[0]))}
    if key == "active" and value["set"] is not None and not alive(value["set"]):
        return {"set": None, "hole": None, "table": None}
    if key == "section":
        return _section_map(value, lambda i: i if alive(i) else None)
    return value


def ids_in(key: str, value: Any) -> Iterable[int]:
    if key == "hidden":
        return (int(HIDDEN_RE.match(k).group(2)) for k in value)  # type: ignore[union-attr]
    if key == "hide_all":
        return ids_in("hidden", value["shown"])
    if key == "displays":
        return (d["chart"] for d in value)
    if key in ("collar_styles", "trace_styles", "block_styles", "cloud_styles"):
        return (int(k) for k in value)
    if key == "columns":
        return (int(k.split(":", 1)[0]) for k in value)
    if key == "active" and value["set"] is not None:
        return (value["set"],)
    if key == "section":
        return _section_ids(value)
    return ()


def map_ids(key: str, value: Any, new_id: Callable[[int], int | None]) -> Any:
    """Renumber object ids inside a normalized value (`.bfggis` import); ids without a new one are dropped."""

    def hidden(k: str) -> str | None:
        m = HIDDEN_RE.match(k)
        assert m is not None
        n = new_id(int(m.group(2)))
        return None if n is None else f"{m.group(1)}:{n}" + (f":{m.group(3)}" if m.group(3) else "")

    if key == "hidden":
        return [h for h in map(hidden, value) if h is not None]
    if key == "hide_all":
        return {**value, "shown": [h for h in map(hidden, value["shown"]) if h is not None]}
    if key == "displays":
        return [{**d, "chart": n} for d in value if (n := new_id(d["chart"])) is not None]
    if key in ("collar_styles", "trace_styles", "block_styles", "cloud_styles"):
        return {str(n): s for k, s in value.items() if (n := new_id(int(k))) is not None}
    if key == "columns":
        out: dict[str, Any] = {}
        for k, c in value.items():
            set_id, table = k.split(":", 1)
            n = new_id(int(set_id))
            if n is not None:
                out[f"{n}:{table}"] = c
        return out
    if key == "active" and value["set"] is not None:
        n = new_id(value["set"])
        return {**value, "set": n} if n is not None else {"set": None, "hole": None, "table": None}
    if key == "section":
        return _section_map(value, new_id)
    return value
