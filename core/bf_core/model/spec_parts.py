"""The parts of the parameters of an implicit model (подшаг 19.1; ТЗ 8.9.2–8.9.4; 7.35 Д-24, Д-25, Д-31, Д-39;
ADR 0026 решения 4–22, 30; contract — docs/progress/e19-api.md §3–4): the constants and ranges of 8.9.4, the
refusals of решение 30, the boundary, the smoothing, the anisotropy, the partition of unity, the data, the manual
layers, the post-processing and the groups, each with its defaults and ranges. Pure: no database, no numpy. The whole
body — `spec.parse_model`.

    ranged(raw, key, param) -> float | None        RANGES[key]; 422 `bad_param` {param}
    refuse(code, message=None, hint=None, **extra) -> ApiError     REFUSALS[code] (решение 30)
    parse_group(raw, param) -> Group                 `params` by the type: shell, vein, expand, fault, vein_system
    resolve_h(diag) -> h; eta(kernel, s, h_bar) -> η (8.9.2)
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bf_core.api.errors import ApiError
from bf_core.geostat.estimate.spec_parts import id_of, list_of, obj, text_of
from bf_core.geostat.variogram.spec import LEN_MAX, Range, _number, _span, bad, choice

ALGO = "rbf-1"
KERNELS = ("linear", "cubic", "wendland")  # φ = −r, r³, (1 − r/a)⁴₊·(4r/a + 1)
ETA_POWER = {"linear": 1, "cubic": 3, "wendland": 0}  # η = s·h̄^k (8.9.2)
SMOOTHING = ("manual", "auto")  # Д-25
GROUP_TYPES = ("domain", "shell", "vein", "expand", "fault", "vein_system")
STAGE_B_TYPES = ("fault", "vein_system")  # Э19б (Д-24, Д-39)
RELATIONS = ("conformable", "erode", "onlap", "intrusion")  # Д-24, Д-31; «conformable» — хронология 8.9.2
ROLES = ("contact", "inside", "outside")  # a layer of strings given to a group (8.9.2)
POINT_TYPES = ("contact", "inside", "outside", "manual", "value")  # i1 code of a data point = index (решение 4)
TRANSFORMS = ("linear", "log", "nscore")  # shells (решение 6)
SHELL_MODES = ("numeric", "indicator")
SHELL_SOURCES = ("composites", "table")
VEIN_BOUNDS = ("hull", "polygon")
CLIPS = ("none", "boundary", "wireframe", "dtm")
STATES = ("ready", "building", "error")
OUTPUT_KINDS = ("surface", "body", "line")
STAGES = ("Данные", "Участки", "Изоповерхности", "Тела", "Проверки")  # the stages of `implicit.build` / `.update`
CHECK_SORTS = ("residual", "source")
FORMATS = ("json", "csv")

H_DIVISOR = 200.0  # h = diagonal of the extent / 200
LEVELS, N_PATCH, N_MIN, DELTA = 2, 1000, 100, 0.25
D_MAX, STEP, TREND_RADIUS = 30.0, 5.0, 200.0
S_CONTACT, S_GRADE = 0.0, 0.05
COND_TARGET = 1e8
P_INDICATOR = 0.5
SUPPORT_FACTOR = 2.0  # Д-32: D_оп = 2·D_max
MARGIN = 0.10  # the octree covers the extent + 10 % (after A)
PATCH_GROW = 1.25  # R × 1,25 until N_min points
COINCIDE = 1e-9  # m: points that coincide (решение 4)
ZERO_NUDGE = 1e-9  # ·h: a node with f = 0 (8.9.3 п. 3)
MIN_AREA = 1e-12  # ·h²: a smaller triangle is removed (8.9.3 п. 7)
SIMPLIFY_DEV = 0.1  # ·h: the largest deviation after pyfqmr (8.9.3 п. 8)
LANCZOS_MAX, COND_TOL, ETA_MAX_FACTOR = 30, 0.05, 1e-2  # Д-25
SEED = 12345
POINTS_MAX = 2_000_000  # вопрос № 101
GROUPS_MAX, CODES_MAX, THRESHOLDS_MAX, MANUAL_MAX = 50, 1000, 20, 100
POLYGON_MAX, VEINS_MAX, PREVIEW_MAX, PAGE_MAX = 10_000, 20, 2_000_000, 5000
PREVIEW_DEFAULT, PAGE_DEFAULT = 200_000, 500
CELLS_AXIS_MAX = 1 << 20  # fine cells along one axis of the extent: h finer than that is a typing error
#: what a stored `meta` has besides the parameters (written by the store and the job)
META_ONLY = ("v", "algo", "extent", "outputs", "stats", "inputs", "state", "error", "versions", "last_run",
             "created_at", "job_id", "back", "h_used")  # fmt: skip

#: 8.9.4 (and Д-25, Д-32, the parameters of 8.9.3): key → range; the parts of a key path use "_" here.
RANGES: dict[str, Range] = {
    "h": Range(0.1, 100.0, "Шаг h", " м"),
    "levels": Range(0, 4, "Уровни L", integer=True),
    "n_patch": Range(300, 4000, "Точек в листе N_уч", integer=True),
    "n_min": Range(20, 500, "Наименьшее число точек участка N_min", integer=True),
    "delta": Range(0.1, 1.0, "Перекрытие участков δ"),
    "d_max": Range(1.0, 500.0, "Расстояние D_max", " м"),
    "step": Range(0.5, 50.0, "Шаг точек Δ_т", " м"),
    "trend_radius": Range(10.0, 5000.0, "Радиус структурного тренда R_тр", " м"),
    "s": Range(0.0, 10.0, "Сглаживание s"),
    "cond_target": Range(1e4, 1e12, "Цель числа обусловленности"),
    "kernel_radius": Range(0.0, LEN_MAX, "Радиус ядра a", " м", lo_open=True),
    "ang1": Range(0.0, 360.0, "Азимут ang1", "°"),
    "ang2": Range(-90.0, 90.0, "Погружение ang2", "°"),
    "ang3": Range(-180.0, 180.0, "Поворот ang3", "°"),
    "ratio": Range(0.0, 1e6, "Отношение осей", lo_open=True),
    "coord": Range(-math.inf, math.inf, "Координата"),
    "threshold": Range(-math.inf, math.inf, "Порог оболочки"),
    "p": Range(0.3, 0.7, "Вероятность p индикаторной оболочки"),
    "t_min": Range(0.0, 100.0, "Мощность выклинивания t_min", " м"),
    "offset": Range(0.0, 10_000.0, "Отступ границы жилы", " м"),
    "expand": Range(-1000.0, 1000.0, "Расширение Δ", " м"),
    "v_min": Range(0.0, math.inf, "Наименьший объём части V_min", " м³"),
    "fault_radius": Range(0.0, LEN_MAX, "Радиус влияния разлома R_F", " м", lo_open=True),
    "support": Range(0.0, LEN_MAX, "Порог опоры D_оп", " м", lo_open=True),
    "threads": Range(1, 64, "Число потоков", integer=True),
    "limit": Range(1, PREVIEW_MAX, "Точек в предпросмотре", integer=True),
    "page": Range(1, 1_000_000, "Страница", integer=True),
    "size": Range(1, PAGE_MAX, "Строк на странице", integer=True),
}

#: решение 30 (+ the lookups of the routes): code → (status, message, hint)
REFUSALS: dict[str, tuple[int, str, str]] = {
    "rbf_singular": (422, "Система RBF участка вырождена: совпадающие точки с разными значениями или слишком "
                     "густые данные.", "Включите сглаживание «Авто» или задайте s > 0."),
    "too_many_points": (422, "Точек данных больше 2 000 000.", "Увеличьте шаг точек Δ_т или уменьшите D_max."),
    "wireframe_open": (422, "Каркас не замкнут: внутренность не определена.",
                       "Запустите «Исправить каркас» и повторите."),
    "trend_not_found": (404, "Тренд не найден.", "Обновите раздел «Тренды» и выберите тренд заново."),
    "trend_structural": (422, "Структурный тренд в оценке не применяется.",
                         "Выберите глобальный тренд или задайте углы поиска вручную."),
    "no_contacts": (422, "В данных группы нет контактов: поверхность не определена.",
                    "Проверьте коды группы и таблицу или добавьте ручные точки и линии."),
    "boundary_empty": (422, "Граница модели пуста.", "Задайте охват или выберите замкнутый каркас границы."),
    "model_not_found": (404, "Неявная модель не найдена.", "Обновите дерево «Неявные модели»."),
    "group_not_found": (404, "Группа модели не найдена.", "Обновите вкладку модели и выберите группу заново."),
    "model_building": (409, "Модель сейчас строится.", "Дождитесь окончания задания или отмените его."),
    "not_built": (409, "Модель ещё не построена.", "Выполните «Построить» или «Обновить»."),
    "iso_check": (422, "Поверхность не прошла проверки 8.9.3.", "Уменьшите шаг h или проверьте данные группы."),
}  # fmt: skip


def refuse(code: str, message: str | None = None, hint: str | None = None, **extra: Any) -> ApiError:
    """An ApiError of REFUSALS[code]; `message` / `hint` replace the stock text, `extra` — machine fields
    (`param`, `group`, `sources`, `points`, …), never user text."""
    status, msg, tip = REFUSALS[code]
    return ApiError(status, code, message or msg, hint or tip, extra or None)


def ranged(raw: Any, key: str, param: str) -> float | None:
    """`raw` in RANGES[key]; absent, null or "" — None (the default). Integers come back as whole floats."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    r = RANGES[key]
    value = _number(raw, r.label, param)
    if r.integer and value != math.floor(value):
        raise bad("bad_param", f"{r.label}: нужно целое число.", f"Задайте целое число {_span(r)}.", param)
    if not (r.lo < value if r.lo_open else r.lo <= value) or not value <= r.hi:
        raise bad("bad_param", f"{r.label}: значение вне диапазона {_span(r)}.", f"Задайте {_span(r)}.", param)
    return value + 0.0  # −0.0 → 0.0


def _num(raw: Any, key: str, default: float, param: str) -> float:
    v = ranged(raw, key, param)
    return default if v is None else v


def _int(raw: Any, key: str, default: int, param: str) -> int:
    v = ranged(raw, key, param)
    return default if v is None else int(v)


def _flag(raw: Any, default: bool, label: str, param: str) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise bad("bad_param", f"{label}: ожидается «да» или «нет».", "Отметьте или снимите галочку.", param)
    return raw


def _ids(raw: Any, label: str, param: str, most: int) -> tuple[int, ...]:
    given = list_of(raw if raw is not None else [], label, param, most)
    out: list[int] = []
    for i, x in enumerate(given):
        v = id_of(x, label.lower(), f"{param}.{i}")
        assert v is not None
        if v in out:
            raise bad("bad_param", f"{label}: номер повторяется.", "Уберите повтор.", f"{param}.{i}")
        out.append(v)
    return tuple(out)


def _codes(raw: Any, param: str) -> tuple[str, ...]:
    """Codes of a group as text (a number by `format(x, ".15g")`, as Э16), unique, in the given order."""
    given = list_of(raw if raw is not None else [], "Коды группы", param, CODES_MAX)
    out: list[str] = []
    for i, x in enumerate(given):
        p = f"{param}.{i}"
        if isinstance(x, bool) or not isinstance(x, str | int | float) or not str(x).strip():
            raise bad("bad_param", "Код группы: пустое значение.", "Выберите код из списка.", p)
        out.append(format(_number(x, "Код", p), ".15g") if isinstance(x, int | float) else x.strip())
    return tuple(dict.fromkeys(out))


def resolve_h(diag: float) -> float:
    """8.9.4: h = diagonal / 200, clamped to the range of h (0,1–100 м)."""
    r = RANGES["h"]
    return min(max(diag / H_DIVISOR, r.lo), r.hi)


def eta(kernel: str, s: float, h_bar: float) -> float:
    """8.9.2: η = s·h̄ (linear), s·h̄³ (cubic), s (finite radius); h̄ — the median distance to the nearest point."""
    return s * h_bar ** ETA_POWER[kernel]


# --- the parts of the model ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Boundary:
    """Exactly one of `box` (xmin, ymin, zmin, xmax, ymax, zmax) and `wireframe_id`; `dtm_id` cuts either from
    above (вопрос № 100)."""

    box: tuple[float, float, float, float, float, float] | None
    wireframe_id: int | None
    dtm_id: int | None

    def canonical(self) -> dict[str, Any]:
        return {"box": None if self.box is None else list(self.box), "wireframe_id": self.wireframe_id,
                "dtm_id": self.dtm_id}  # fmt: skip


def parse_boundary(raw: Any, param: str = "boundary") -> Boundary:
    b = obj(raw, "Граница модели", param)
    wid = id_of(b.get("wireframe_id"), "каркас границы", f"{param}.wireframe_id", required=False)
    dtm = id_of(b.get("dtm_id"), "ЦТМ", f"{param}.dtm_id", required=False)
    box = None
    if b.get("box") is not None:
        given = b["box"]
        if not isinstance(given, list) or len(given) != 6:
            raise bad("bad_param", "Охват: нужны шесть чисел (X, Y, Z наименьшие и наибольшие).",
                      "Задайте охват или возьмите его из данных.", f"{param}.box")  # fmt: skip
        v = [_num(x, "coord", math.nan, f"{param}.box.{i}") for i, x in enumerate(given)]
        if any(math.isnan(x) for x in v):
            raise bad("bad_param", "Охват: нужны числа.", "Задайте все шесть чисел.", f"{param}.box")
        for i in range(3):
            if not v[i] < v[i + 3]:
                raise refuse("boundary_empty", "Граница модели пуста: наибольшее не больше наименьшего.",
                             param=f"{param}.box.{i + 3}")  # fmt: skip
        box = (v[0], v[1], v[2], v[3], v[4], v[5])
    if (box is None) == (wid is None):
        raise bad("bad_param", "Граница модели: задайте охват или каркас — что-то одно.",
                  "Задайте охват или выберите замкнутый каркас.", param)  # fmt: skip
    return Boundary(box, wid, dtm)


@dataclass(frozen=True)
class Smoothing:
    mode: str  # manual | auto
    s: float | None  # None — by the type of the group (S_CONTACT / S_GRADE); «Авто» starts from it (η₀)
    cond_target: float | None  # auto only

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {"mode": self.mode, "s": self.s}
        if self.mode == "auto":
            out["cond_target"] = self.cond_target
        return out


def parse_smoothing(raw: Any, param: str = "smoothing") -> Smoothing:
    b = obj(raw, "Сглаживание", param)
    mode = choice(b.get("mode"), SMOOTHING, "manual", "Сглаживание", f"{param}.mode")
    s = ranged(b.get("s"), "s", f"{param}.s")
    target = _num(b.get("cond_target"), "cond_target", COND_TARGET, f"{param}.cond_target") if mode == "auto" else None
    return Smoothing(mode, s, target)


@dataclass(frozen=True)
class Anisotropy:
    """By hand — the same shape as a trend (`geostat.trend.matrix(anisotropy.canonical())` is A)."""

    ang1: float
    ang2: float
    ang3: float
    ratios: tuple[float, float, float]  # (1, a2 / a1, a3 / a1)

    def canonical(self) -> dict[str, Any]:
        return {"ang1": self.ang1, "ang2": self.ang2, "ang3": self.ang3, "ratios": list(self.ratios)}


def parse_anisotropy(raw: Any, param: str = "anisotropy") -> Anisotropy | None:
    if raw is None:
        return None
    b = obj(raw, "Анизотропия", param)
    a1 = _num(b.get("ang1"), "ang1", 0.0, f"{param}.ang1")
    a2 = _num(b.get("ang2"), "ang2", 0.0, f"{param}.ang2")
    a3 = _num(b.get("ang3"), "ang3", 0.0, f"{param}.ang3")
    ratios = b.get("ratios")
    r = (1.0, 1.0, 1.0)
    if ratios is not None:
        if not isinstance(ratios, list) or len(ratios) != 3:
            raise bad("bad_param", "Отношения осей — три числа: 1, a2 / a1, a3 / a1.", "Например: 1; 0,5; 0,2.",
                      f"{param}.ratios")  # fmt: skip
        r0, r1, r2 = (_num(x, "ratio", 1.0, f"{param}.ratios.{i}") for i, x in enumerate(ratios))
        if r0 != 1.0:
            raise bad("bad_param", "Первое отношение осей должно быть равно 1.", "Отношения считаются от a1.",
                      f"{param}.ratios.0")  # fmt: skip
        r = (1.0, r1, r2)
    return Anisotropy(0.0 if a1 == 360.0 else a1, a2, 180.0 if a3 == -180.0 else a3, r)


@dataclass(frozen=True)
class Pou:
    n_patch: int
    n_min: int
    delta: float

    def canonical(self) -> dict[str, Any]:
        return {"n_patch": self.n_patch, "n_min": self.n_min, "delta": self.delta}


def parse_pou(raw: Any, param: str = "pou") -> Pou:
    b = obj(raw, "Разбиение единицы", param)
    pou = Pou(_int(b.get("n_patch"), "n_patch", N_PATCH, f"{param}.n_patch"),
              _int(b.get("n_min"), "n_min", N_MIN, f"{param}.n_min"),
              _num(b.get("delta"), "delta", DELTA, f"{param}.delta"))  # fmt: skip
    if pou.n_min > pou.n_patch:
        raise bad("bad_param", "N_min больше N_уч.", "Задайте N_min не больше числа точек в листе.", f"{param}.n_min")
    return pou


@dataclass(frozen=True)
class DataSel:
    """The table of codes of a set (8.9.2): `table` — id as text, `field` — the code field."""

    set_id: int
    table: str
    field: str
    d_max: float
    step: float

    def canonical(self) -> dict[str, Any]:
        return {"set_id": self.set_id, "table": self.table, "field": self.field, "d_max": self.d_max,
                "step": self.step}  # fmt: skip


def parse_data(raw: Any, param: str = "data") -> DataSel | None:
    if raw is None:
        return None
    b = obj(raw, "Данные модели", param)
    set_id = id_of(b.get("set_id"), "набор скважин", f"{param}.set_id")
    table = text_of(b.get("table"), "Таблица кодов", f"{param}.table")
    field = text_of(b.get("field"), "Поле кода", f"{param}.field")
    assert set_id is not None and table is not None and field is not None
    return DataSel(set_id, table, field, _num(b.get("d_max"), "d_max", D_MAX, f"{param}.d_max"),
                   _num(b.get("step"), "step", STEP, f"{param}.step"))  # fmt: skip


@dataclass(frozen=True)
class Manual:
    layer_id: int  # objects(kind="line_layer")
    role: str  # contact | inside | outside
    group: int  # the id of the group

    def canonical(self) -> dict[str, Any]:
        return {"layer_id": self.layer_id, "role": self.role, "group": self.group}


def parse_manual(raw: Any, param: str = "manual") -> tuple[Manual, ...]:
    given = list_of(raw if raw is not None else [], "Ручные данные", param, MANUAL_MAX)
    out = []
    for i, x in enumerate(given):
        b, p = obj(x, "Слой ручных данных", f"{param}.{i}"), f"{param}.{i}"
        layer = id_of(b.get("layer_id"), "слой стрингов", f"{p}.layer_id")
        group = id_of(b.get("group"), "группа", f"{p}.group")
        assert layer is not None and group is not None
        out.append(Manual(layer, choice(b.get("role"), ROLES, "contact", "Роль слоя", f"{p}.role"), group))
    return tuple(out)


@dataclass(frozen=True)
class Post:
    """8.9.3 п. 8: parts below V_min removed (0 — off), pyfqmr (off), the cut of the surfaces."""

    v_min: float
    simplify: bool
    clip: str  # none | boundary | wireframe | dtm
    clip_id: int | None  # wireframe / dtm

    def canonical(self) -> dict[str, Any]:
        return {"v_min": self.v_min, "simplify": self.simplify, "clip": self.clip, "clip_id": self.clip_id}


def parse_post(raw: Any, param: str = "post") -> Post:
    b = obj(raw, "Постобработка", param)
    clip = choice(b.get("clip"), CLIPS, "boundary", "Обрезка", f"{param}.clip")
    by_id = clip in ("wireframe", "dtm")
    clip_id = id_of(b.get("clip_id"), "каркас или ЦТМ обрезки", f"{param}.clip_id", required=by_id)
    return Post(_num(b.get("v_min"), "v_min", 0.0, f"{param}.v_min"),
                _flag(b.get("simplify"), False, "Упрощение", f"{param}.simplify"), clip,
                clip_id if by_id else None)  # fmt: skip


# --- groups ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Group:
    """A group of the stack (решение 15, 22): `params` — the canonical dict of its type (§4 of the contract)."""

    id: int | None  # None — the store numbers it (max + 1)
    name: str
    type: str
    codes: tuple[str, ...]
    relation: str  # carried by the younger group: how it treats the older ones (below)
    faults: tuple[int, ...]  # ids of the groups of type `fault` cutting it (Э19б)
    params: Mapping[str, Any]

    def canonical(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "type": self.type, "codes": list(self.codes),
                "relation": self.relation, "faults": list(self.faults), "params": dict(self.params)}  # fmt: skip


def _thresholds(raw: Any, param: str) -> list[float]:
    given = list_of(raw if raw is not None else [], "Пороги оболочек", param, THRESHOLDS_MAX)
    if not given:
        raise bad("bad_param", "Не заданы пороги оболочек.", "Задайте хотя бы один порог.", param)
    out = [_num(x, "threshold", math.nan, f"{param}.{i}") for i, x in enumerate(given)]
    for i in range(1, len(out)):
        if not out[i] > out[i - 1]:
            raise bad("bad_param", "Пороги оболочек — строго по возрастанию.", "Упорядочьте пороги.", f"{param}.{i}")
    return out


def _shell(b: dict[str, Any], p: str) -> dict[str, Any]:
    source = choice(b.get("source"), SHELL_SOURCES, "composites", "Источник содержаний", f"{p}.source")
    rid = id_of(b.get("result_id"), "композиты", f"{p}.result_id", required=source == "composites")
    sid = id_of(b.get("set_id"), "набор скважин", f"{p}.set_id", required=False)
    table = text_of(b.get("table"), "Таблица проб", f"{p}.table", required=source == "table")
    mode = choice(b.get("mode"), SHELL_MODES, "numeric", "Вид оболочки", f"{p}.mode")
    return {"source": source, "result_id": rid if source == "composites" else None,
            "set_id": sid if source == "table" else None, "table": table if source == "table" else None,
            "field": text_of(b.get("field"), "Поле содержания", f"{p}.field"),
            "transform": choice(b.get("transform"), TRANSFORMS, "linear", "Преобразование", f"{p}.transform"),
            "mode": mode, "thresholds": _thresholds(b.get("thresholds"), f"{p}.thresholds"),
            "p": _num(b.get("p"), "p", P_INDICATOR, f"{p}.p") if mode == "indicator" else None}  # fmt: skip


def _vein(b: dict[str, Any], p: str) -> dict[str, Any]:
    bound = choice(b.get("bound"), VEIN_BOUNDS, "hull", "Граница жилы", f"{p}.bound")
    poly = None
    if bound == "polygon":
        given = list_of(b.get("polygon"), "Полигон границы", f"{p}.polygon", POLYGON_MAX)
        if len(given) < 3:
            raise bad("bad_param", "Полигон границы: нужно не меньше трёх вершин.", "Обведите жилу в плане.",
                      f"{p}.polygon")  # fmt: skip
        poly = []
        for i, v in enumerate(given):
            if not isinstance(v, list) or len(v) != 2:
                raise bad("bad_param", "Вершина полигона: нужны два числа (u, v).", "Проверьте вершину.",
                          f"{p}.polygon.{i}")  # fmt: skip
            poly.append([_number(c, "Координата", f"{p}.polygon.{i}.{k}") for k, c in enumerate(v)])
    return {"bound": bound, "polygon": poly, "offset": _num(b.get("offset"), "offset", 0.0, f"{p}.offset"),
            "t_min": _num(b.get("t_min"), "t_min", 0.0, f"{p}.t_min")}  # fmt: skip


def _expand(b: dict[str, Any], p: str) -> dict[str, Any]:
    wid = id_of(b.get("wireframe_id"), "каркас", f"{p}.wireframe_id")
    d = ranged(b.get("delta"), "expand", f"{p}.delta")
    if d is None or d == 0.0:
        raise bad("bad_param", "Расширение Δ: нужно число, не равное нулю.", "Δ > 0 — расширение, Δ < 0 — сжатие.",
                  f"{p}.delta")  # fmt: skip
    return {"wireframe_id": wid, "delta": d}


def _vein_system(b: dict[str, Any], p: str) -> dict[str, Any]:
    given = list_of(b.get("veins"), "Жилы системы", f"{p}.veins", VEINS_MAX)
    if len(given) < 2:
        raise bad("bad_param", "В системе жил нужно не меньше двух жил.", "Добавьте жилы.", f"{p}.veins")
    veins: list[dict[str, Any]] = []
    for i, v in enumerate(given):
        q = f"{p}.veins.{i}"
        vb = obj(v, "Жила системы", q)
        code = _codes([vb.get("code")], f"{q}.code")[0]
        if any(x["code"] == code for x in veins):
            raise bad("bad_param", "Код жилы повторяется.", "У каждой жилы свой код.", f"{q}.code")
        veins.append({"code": code, "name": text_of(vb.get("name"), "Имя жилы", f"{q}.name", required=False) or code,
                      **_vein(vb, q)})  # fmt: skip
    return {"veins": veins}


def parse_group(raw: Any, param: str = "group") -> Group:
    """One group: `{id?, name, type, codes, relation?, faults?, params?}`; checks within the model — ModelSpec."""
    b = obj(raw, "Группа модели", param)
    gtype = choice(b.get("type"), GROUP_TYPES, "domain", "Вид группы", f"{param}.type")
    name = text_of(b.get("name"), "Имя группы", f"{param}.name")
    assert name is not None
    gid = id_of(b.get("id"), "группа", f"{param}.id", required=False)
    pb, pp = obj(b.get("params"), "Параметры группы", f"{param}.params"), f"{param}.params"
    params: dict[str, Any] = {}
    if gtype == "shell":
        params = _shell(pb, pp)
    elif gtype == "vein":
        params = _vein(pb, pp)
    elif gtype == "expand":
        params = _expand(pb, pp)
    elif gtype == "fault":
        params = {"radius": ranged(pb.get("radius"), "fault_radius", f"{pp}.radius")}  # None — infinite fault
    elif gtype == "vein_system":
        params = _vein_system(pb, pp)
    codes = _codes(b.get("codes"), f"{param}.codes")
    if gtype == "vein_system":
        codes = tuple(v["code"] for v in params["veins"])
    if gtype in ("shell", "expand") and codes:
        raise bad("bad_param", "У оболочки и расширения кодов нет.", "Уберите коды группы.", f"{param}.codes")
    relation = choice(b.get("relation"), RELATIONS, "conformable", "Отношение группы", f"{param}.relation")
    faults = _ids(b.get("faults"), "Разломы группы", f"{param}.faults", GROUPS_MAX)
    if gtype == "fault" and (faults or relation != "conformable"):
        raise bad("bad_param", "У разлома нет отношения и разломов.", "Уберите их у разлома.", f"{param}.faults")
    return Group(gid, name, gtype, codes, relation, faults, params)
