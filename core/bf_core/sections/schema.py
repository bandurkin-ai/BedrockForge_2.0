"""The parameters of a series of sections (ТЗ 8.1.1–8.1.2; ADR 0016 §1): `objects.meta` of kind `section_series`.

`check(params, alive_layer)` turns what the window sends into the stored meta (`v: 1`) or raises `SeriesError`
(422 `bad_series`, Russian text and hint). Only the parameters are stored: the number of sections K, their
positions s_k, origins O_k and names are computed (`sections/geometry.py`, `sections/names.py`).

| field | meaning |
|---|---|
| `number` | the number of the series in the project (1, 2, …), set once by `store.create` and never changed |
| `type` | `vertical`, `plan`, `inclined`, `polyline` |
| `p1` [x, y, z]; `p2` [x, y] or `az` | P1 and the direction A of the line (from P1 to P2, or the azimuth) |
| `phi` | the dip of the plane: 90° for vertical and polyline ones, 0–90° for inclined ones, null for plans |
| `s0`, `s1`, `step` | the range and the step Δ along v̂; a plan: `p1.z` is the top level, `s1` = top − bottom |
| `towards`, `away` | the window of every section, w_к and w_от |
| `overrides` | `{"k": {towards?, away?}}` — own windows of sections (k from 0) |
| `template` | the template of the names or null (the default of 8.1.2) |
| `polyline`, `corridor` | the polyline P0…Pm in plan and the width of its corridor (a series of one section) |
| `shadow` | `none`, `prev`, `next`, `both` |
| `contour_layer` | the line layer of the contours of the series or null (renumbered by `.bfggis`) |
| `layout` | the print layout — null until Э22 |
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

KIND = "section_series"
TYPES = ("vertical", "plan", "inclined", "polyline")
SHADOWS = ("none", "prev", "next", "both")
MAX_K = 5_000  # sections in a series (ТЗ 8.1.2)
MAX_OVERRIDES = 5_000
MAX_POLYLINE = 1_000  # points of a polyline section
MAX_TEMPLATE = 200
MIN_LEN, MAX_LEN = 0.01, 10_000.0  # step, windows and corridor, metres
MAX_COORD = 1e9  # |coordinate| and |s|, metres: beyond it float64 loses the millimetre
MIN_SPAN = 1e-6  # P2 ≠ P1, segments of a polyline
DEFAULT_WINDOW = 25.0
DEFAULT_CORRIDOR = 25.0
#: every field of the stored meta, in the order of the table above; `number` and `v` are set by the store
FIELDS = (
    "type", "p1", "p2", "az", "phi", "s0", "s1", "step", "towards", "away", "overrides", "template",
    "polyline", "corridor", "shadow", "contour_layer", "layout",
)  # fmt: skip


class SeriesError(ValueError):
    """422 `bad_series`: what is wrong (Russian) and what to do."""

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.hint = hint


def _number(v: Any, what: str, hint: str = "Введите число.") -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        raise SeriesError(f"{what}: ожидается число.", hint)
    f = float(v) + 0.0  # −0.0 → 0.0
    if abs(f) > MAX_COORD:
        raise SeriesError(
            f"{what}: слишком большое значение.", "Проверьте единицы: координаты и расстояния — в метрах."
        )
    return f


def _length(v: Any, what: str) -> float:
    f = _number(v, what, "Введите расстояние в метрах, например 25.")
    if not MIN_LEN <= f <= MAX_LEN:
        raise SeriesError(f"{what}: от 0,01 до 10 000 м.", "Введите расстояние в этих пределах.")
    return f


def _point(v: Any, size: tuple[int, ...], what: str) -> list[float]:
    if not isinstance(v, list | tuple) or len(v) not in size:
        n = " или ".join(str(s) for s in size)
        raise SeriesError(f"{what}: ожидается {n} координаты.", "Укажите X, Y" + (" и Z." if 3 in size else "."))
    return [_number(x, what) for x in v]


def _p1(v: Any) -> list[float]:
    p = _point(v, (2, 3), "Точка P1")
    return p if len(p) == 3 else [*p, 0.0]


def _direction(params: dict[str, Any], p1: list[float]) -> tuple[list[float] | None, float | None]:
    """(p2, az): exactly one of them; the azimuth in [0°; 360°)."""
    p2, az = params.get("p2"), params.get("az")
    if (p2 is None) == (az is None):
        raise SeriesError("Направление серии: задайте точку P2 или азимут.", "Укажите что-то одно.")
    if p2 is not None:
        q = _point(p2, (2, 3), "Точка P2")[:2]
        if math.hypot(q[0] - p1[0], q[1] - p1[1]) <= MIN_SPAN:
            raise SeriesError("Точки P1 и P2 совпадают в плане.", "Укажите вторую точку линии разреза в стороне от P1.")
        return q, None
    a = _number(az, "Азимут", "Введите азимут в градусах, от 0 до 360.") % 360.0
    return None, (a if a < 360.0 else 0.0) + 0.0  # −1e−20 % 360 is 360.0


def _phi(kind: str, v: Any) -> float | None:
    if kind == "plan":
        return None
    if kind in ("vertical", "polyline"):
        if v is not None and _number(v, "Наклон плоскости") != 90.0:
            raise SeriesError(
                "Наклон плоскости: у вертикальной серии — 90°.", "Для другого наклона выберите тип «Наклонная»."
            )
        return 90.0
    phi = _number(90.0 if v is None else v, "Наклон плоскости", "Введите угол в градусах, от 0 до 90.")
    if not 0.0 <= phi <= 90.0:
        raise SeriesError("Наклон плоскости: от 0 до 90°.", "90° — вертикальная плоскость, 0° — горизонтальная.")
    return phi


def _polyline(params: dict[str, Any]) -> tuple[list[list[float]], float]:
    pts = params.get("polyline")
    if not isinstance(pts, list | tuple) or not 2 <= len(pts) <= MAX_POLYLINE:
        raise SeriesError("Ломаный разрез: от 2 до 1 000 точек.", "Укажите вершины ломаной в плане по порядку.")
    out = [_point(p, (2, 3), "Вершина ломаной")[:2] for p in pts]
    for i in range(1, len(out)):
        if math.hypot(out[i][0] - out[i - 1][0], out[i][1] - out[i - 1][1]) <= MIN_SPAN:
            raise SeriesError(f"Ломаный разрез: вершины {i} и {i + 1} совпадают.", "Удалите повторную вершину.")
    corridor = params.get("corridor")
    return out, _length(DEFAULT_CORRIDOR if corridor is None else corridor, "Ширина коридора")


def _overrides(v: Any, count: int) -> dict[str, dict[str, float]]:
    if v is None:
        return {}
    if not isinstance(v, dict) or len(v) > MAX_OVERRIDES:
        raise SeriesError("Свои окна разрезов: ожидается набор «номер → окно».", "Не больше 5 000 записей.")
    out: dict[str, dict[str, float]] = {}
    for key, w in sorted(v.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else -1):
        k = str(key)
        if not k.isdigit() or len(k) > 6 or not isinstance(w, dict):
            raise SeriesError(f"Свои окна разрезов: неверная запись «{k[:20]}».", "Номер разреза — целое от 0.")
        if int(k) >= count:
            raise SeriesError(
                f"Своё окно задано разрезу {int(k) + 1}, а в серии их {count}.",
                "Удалите свои окна разрезов за пределами серии или расширьте её.",
            )
        extra = set(w) - {"towards", "away"}
        if extra or not w:
            raise SeriesError(f"Своё окно разреза {int(k) + 1}: ожидаются «к себе» и «от себя».", "Проверьте окно.")
        out[str(int(k))] = {name: _length(x, f"Окно разреза {int(k) + 1}") for name, x in w.items()}
    return out


def _template(v: Any) -> str | None:
    if v is None or v == "":
        return None
    if not isinstance(v, str) or len(v) > MAX_TEMPLATE:
        raise SeriesError("Шаблон имён: строка до 200 символов.", "Например «{Y:.0f}N» или «Р-{k}».")
    return v


def _layer(v: Any, alive_layer: Callable[[int], bool]) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0 or not alive_layer(v):
        raise SeriesError("Слой контуров серии не найден.", "Выберите существующий слой линий или создайте новый.")
    return v


def check(
    params: Any,
    alive_layer: Callable[[int], bool],
    count: Callable[[float, float, float], int],
) -> dict[str, Any]:
    """The normalized meta of a series (without `v` and `number`); `count(s0, s1, step)` is K of the geometry."""
    if not isinstance(params, dict):
        raise SeriesError("Параметры серии: ожидается набор полей.", "Заполните окно «Новая серия».")
    unknown = sorted(set(params) - set(FIELDS) - {"v", "number"})
    if unknown:
        raise SeriesError(f"Параметры серии: неизвестное поле «{unknown[0][:40]}».", "Проверьте окно серии.")
    kind = params.get("type")
    if kind not in TYPES:
        raise SeriesError("Тип серии: вертикальная, план, наклонная или ломаная.", "Выберите тип серии.")
    meta: dict[str, Any] = {"type": kind}
    towards = _length(params.get("towards", DEFAULT_WINDOW), "Окно «к себе»")
    away = _length(params.get("away", DEFAULT_WINDOW), "Окно «от себя»")
    step = params.get("step")
    step = _length(towards + away if step is None else step, "Шаг серии")
    if kind == "polyline":
        polyline, corridor = _polyline(params)
        p1 = _p1(params["p1"]) if params.get("p1") is not None else [*polyline[0], 0.0]
        meta.update(p1=p1, p2=None, az=None, polyline=polyline, corridor=corridor)
        s0 = s1 = 0.0  # one section: the development of the whole polyline
    else:
        if params.get("polyline") is not None:
            raise SeriesError("Ломаная задаётся только у ломаного разреза.", "Выберите тип «Ломаная» или уберите её.")
        if params.get("p1") is None:
            raise SeriesError("Не задана точка P1.", "Укажите начало линии разреза (для плана — и верхнюю отметку).")
        p1 = _p1(params["p1"])
        if kind == "plan" and params.get("p2") is None and params.get("az") is None:
            params = {**params, "az": 90.0}  # plans: north up on the screen (8.1.2)
        p2, az = _direction(params, p1)
        meta.update(p1=p1, p2=p2, az=az, polyline=None, corridor=None)
        s0 = _number(params.get("s0", 0.0), "Начало серии s0")
        s1 = _number(params.get("s1", s0), "Конец серии s1")
        if s1 < s0:
            raise SeriesError("Конец серии раньше начала.", "s1 должен быть не меньше s0 (для плана — верх выше низа).")
    k = count(s0, s1, step)
    if k > MAX_K:
        raise SeriesError(
            f"В серии получается {k} разрезов — больше 5 000.",
            f"Увеличьте шаг (не меньше {math.ceil((s1 - s0) / (MAX_K - 1) * 100) / 100:g} м) или сократите пределы.",
        )
    shadow = params.get("shadow", "prev")
    if shadow not in SHADOWS:
        raise SeriesError("Тень: нет, предыдущий, следующий или оба.", "Выберите вариант тени.")
    if params.get("layout") is not None:
        raise SeriesError("Макет печати серии появится в версии с печатью.", "Оставьте поле макета пустым.")
    meta.update(
        phi=_phi(kind, params.get("phi")),
        s0=s0,
        s1=s1,
        step=step,
        towards=towards,
        away=away,
        overrides=_overrides(params.get("overrides"), k),
        template=_template(params.get("template")),
        shadow=shadow,
        contour_layer=_layer(params.get("contour_layer"), alive_layer),
        layout=None,
    )
    return {f: meta[f] for f in FIELDS}


def merge(current: dict[str, Any], change: Any) -> dict[str, Any]:
    """The parameters after a PATCH: the fields sent replace the stored ones; P2 and the azimuth exclude each
    other, so sending one of them clears the other."""
    if not isinstance(change, dict):
        raise SeriesError("Параметры серии: ожидается набор полей.", "Проверьте окно серии.")
    out = {f: current.get(f) for f in FIELDS}
    out.update(change)
    if "az" in change and "p2" not in change:
        out["p2"] = None
    if "p2" in change and "az" not in change:
        out["az"] = None
    return out


def params_of(meta: Any) -> dict[str, Any]:
    """What the window gets: the stored fields (an older or broken meta reads with the defaults)."""
    m = meta if isinstance(meta, dict) else {}
    return {"number": m.get("number"), **{f: m.get(f) for f in FIELDS}}


def remap_meta(meta: Any, new_id: Callable[[int], int | None]) -> Any:
    """`.bfggis` import: the contour layer renumbered; a layer the archive does not have — null. The number of
    the series stays as it is (the contours keep it in `SECTION_SET`)."""
    if not isinstance(meta, dict) or meta.get("contour_layer") is None:
        return meta
    old = meta["contour_layer"]
    new = new_id(old) if isinstance(old, int) and not isinstance(old, bool) else None
    return {**meta, "contour_layer": new}
