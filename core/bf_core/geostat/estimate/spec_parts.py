"""The parts of the parameters of an estimation (подшаг 18.1; ТЗ 8.8.2; 7.35 Д-27, Д-29, Д-38; ADR 0024 §2–6;
contract — docs/progress/e18-api.md §3): the samples, the blocks, the method, the variogram models, the search with
its passes and the output fields, each with its defaults and ranges. Pure: no database, no numpy. The whole body —
`spec.parse_estimation`; `S/` (Э18б) reuses `parse_samples`, `parse_target`, `parse_vario`, `parse_search`.

    ranged(raw, key, param) -> float | None          RANGES[key]; 422 `bad_param` {param}
    parse_samples(raw) -> SampleSel   parse_target(raw) -> Target   parse_method(raw) -> Method
    parse_vario(raw, param, t=None, *, simulation=False) -> VarioRef      422 `variogram_scale` (Д-38)
    parse_search(raw, has_model=…, trend_id=…) -> Search       search_from_model(snapshot), search_from_trend(…)
    parse_outputs(raw, kind, field, hy=False) -> Outputs       default_prefix(field, kind) -> «AU_OK_»

Domain values and block labels are kept as text: a number by `format(x, ".15g")` (as Э16); the store compares
them with the values of the field as Э16 does.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bf_core.blocks.filters.schema import normalize_rules
from bf_core.blocks.schemes import MAX_PREFIX, PREFIX_RE  # one rule of a field prefix, as in the categories
from bf_core.geostat.variogram.spec import (  # the common helpers of the geostat bodies
    LEN_MAX,
    Range,
    _number,
    _span,
    bad,
    choice,
    parse_model,
)
from bf_core.geostat.variogram.spec import RANGES as VARIO_RANGES

METHODS = ("nn", "idw", "sk", "ok", "ik")
SEARCH_FROM = ("model", "trend", "manual")
MEAN_MODES = ("declus", "value")
IK_MODELS = ("per", "median")
CLASS_MEANS = ("declus",)
HY_MODES = ("drop", "cap")  # Д-29: «отбросить» | «урезать до t»
#: the scale of a variogram model (Д-38): «z» — the model in the units of the field, «back» — `back.model` of a
#: model in normal scores, «y» — the model in normal scores itself (SGS only)
SCALES = ("z", "back", "y")
#: the output fields in their canonical order (8.8.2, Д-27, Д-29); "P" of IK stands for P1…PK
OUTPUTS = ("STATUS", "EST", "KV", "NS", "NH", "NHY", "AD", "DMIN", "PASS", "SLOPE", "KE", "LAGR", "SNW", "NOCT",
           "P", "ETYPE")  # fmt: skip
_NEAR = ("NS", "NH", "NHY", "AD", "DMIN", "PASS")
ALLOWED: dict[str, tuple[str, ...]] = {
    "nn": ("STATUS", "EST", *_NEAR, "NOCT"),
    "idw": ("STATUS", "EST", *_NEAR, "NOCT"),
    "sk": ("STATUS", "EST", "KV", *_NEAR, "SLOPE", "KE", "SNW", "NOCT"),  # SK: μ = 0, no LAGR
    "ok": OUTPUTS[:14],
    "ik": ("STATUS", *_NEAR, "NOCT", "P", "ETYPE"),
}
DEFAULT_FIELDS: dict[str, tuple[str, ...]] = {
    "nn": ("STATUS", "EST", "DMIN", "PASS"),
    "idw": ("STATUS", "EST", "NS", "AD", "PASS"),
    "sk": ("STATUS", "EST", "KV", "NS", "NH", "AD", "PASS", "SLOPE"),
    "ok": ("STATUS", "EST", "KV", "NS", "NH", "AD", "PASS", "SLOPE", "KE"),
    "ik": ("STATUS", "NS", "PASS", "P", "ETYPE"),
}
#: вопрос № 81 + Д-27, Д-29: EST f8; KV, SLOPE, KE, LAGR, SNW, AD, DMIN f4; NS, NH, NHY i2; PASS, NOCT, STATUS i1;
#: P_k f4, ETYPE f8
DTYPES = {"STATUS": "i1", "EST": "f8", "KV": "f4", "NS": "i2", "NH": "i2", "NHY": "i2", "AD": "f4", "DMIN": "f4",
          "PASS": "i1", "SLOPE": "f4", "KE": "f4", "LAGR": "f4", "SNW": "f4", "NOCT": "i1", "P": "f4",
          "ETYPE": "f8"}  # fmt: skip
#: Д-27: the codes of STATUS (0 — the block is estimated; 1–3 — EST is NaN)
STATUS_LABELS = ("оценён", "вне домена", "мало проб", "вырожденная система")
PASSES_MAX = 5
PASS_FACTORS = (1.0, 2.0, 3.0)  # the default passes
N_MIN, N_MAX = 4, 24
POWER = 2.0
THRESHOLDS_MAX = 20
DOMAIN_VALUES_MAX = 1000
NAME_MAX = 200
PREFIX_MAX = MAX_PREFIX  # 40 (+ the longest key (6) ≤ 100 — the limit of a field name of a block model)
_HEX = re.compile(r"[0-9a-f]{64}")

#: 8.8.2, Д-29: key → range (the angles — those of the variography)
RANGES: dict[str, Range] = {
    "power": Range(0.5, 5.0, "Степень IDW p"),
    "disc": Range(1, 10, "Дискретизация", integer=True),
    "factor": Range(0.0, 100.0, "Множитель радиусов прохода", lo_open=True),
    "n_min": Range(1, 128, "Наименьшее число проб n_min", integer=True),
    "n_max": Range(1, 128, "Наибольшее число проб n_max", integer=True),
    "n_hole": Range(0, 64, "Проб из одной скважины n_скв (0 — без ограничения)", integer=True),
    "n_oct": Range(0, 16, "Проб в октанте n_окт (0 — без октантов)", integer=True),
    "k_oct": Range(0, 8, "Заполненных октантов k_окт (0 — без ограничения)", integer=True),
    "n_holes_min": Range(0, 128, "Наименьшее число скважин (0 — без ограничения)", integer=True),
    "r1": Range(0.0, LEN_MAX, "Радиус поиска r1", " м", lo_open=True),
    "r2": Range(0.0, LEN_MAX, "Радиус поиска r2", " м", lo_open=True),
    "r3": Range(0.0, LEN_MAX, "Радиус поиска r3", " м", lo_open=True),
    "ang1": VARIO_RANGES["ang1"],
    "ang2": VARIO_RANGES["ang2"],
    "ang3": VARIO_RANGES["ang3"],
    "soft_dist": Range(0.0, LEN_MAX, "Расстояние мягкой границы d_м", " м"),
    "topcut": Range(-math.inf, math.inf, "Порог урезки"),
    "mean": Range(-math.inf, math.inf, "Среднее простого кригинга m"),
    "threshold": Range(-math.inf, math.inf, "Порог индикатора"),
    "hy_t": Range(-math.inf, math.inf, "Порог ураганных проб t"),
    "hy_q": Range(0.0, 1.0, "Доля радиуса для ураганных проб q", lo_open=True),
}


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


def int_of(raw: Any, key: str, default: int, param: str) -> int:
    v = ranged(raw, key, param)
    return default if v is None else int(v)


def _angle(raw: Any, key: str, param: str) -> float | None:
    v = ranged(raw, key, param)
    if v is not None and key == "ang1" and v == 360.0:
        return 0.0
    if v is not None and key == "ang3" and v == -180.0:
        return 180.0
    return v


def obj(raw: Any, label: str, param: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise bad("bad_param", f"{label}: ожидается набор параметров.", "Проверьте форму запроса.", param)
    return dict(raw)


def list_of(raw: Any, label: str, param: str, most: int) -> list[Any]:
    if not isinstance(raw, list):
        raise bad("bad_param", f"{label}: ожидается список.", "Проверьте форму запроса.", param)
    if len(raw) > most:
        raise bad("bad_param", f"{label}: больше {most}.", f"Оставьте не больше {most}.", param)
    return raw


def id_of(raw: Any, label: str, param: str, *, required: bool = True) -> int | None:
    if raw is None or raw == "":
        if required:
            raise bad("bad_param", f"Не выбрано: {label}.", f"Выберите {label}.", param)
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float) or raw != int(raw) or raw < 1:
        raise bad("bad_param", f"{label}: неверный номер объекта.", "Обновите дерево проекта и выберите заново.", param)
    return int(raw)


def text_of(raw: Any, label: str, param: str, most: int = NAME_MAX, *, required: bool = True) -> str | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if required:
            raise bad("bad_param", f"{label}: не задано.", f"Задайте {label.lower()}.", param)
        return None
    if isinstance(raw, bool) or not isinstance(raw, str | int | float):
        raise bad("bad_param", f"{label}: ожидается текст.", "Проверьте форму запроса.", param)
    out = str(raw).strip()
    if len(out) > most:
        raise bad("bad_param", f"{label}: длиннее {most} знаков.", f"Сократите до {most} знаков.", param)
    return out


def _value(raw: Any, label: str, param: str) -> str:
    """A domain value or a block label: text as given (trimmed); a number by `format(x, ".15g")` (as Э16)."""
    if isinstance(raw, bool) or not isinstance(raw, str | int | float) or not str(raw).strip():
        raise bad("bad_param", f"{label}: пустое значение.", "Выберите значение из списка.", param)
    if isinstance(raw, int | float):
        return format(_number(raw, label, param), ".15g")
    return raw.strip()


def _values(raw: Any, label: str, param: str) -> tuple[str, ...]:
    given = list_of(raw, label, param, DOMAIN_VALUES_MAX)
    return tuple(dict.fromkeys(_value(v, label, f"{param}.{i}") for i, v in enumerate(given)))


# --- samples ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DomainRef:
    table: str | None  # id as text or name; None — the table of the samples
    field: str
    values: tuple[str, ...]

    def canonical(self) -> dict[str, Any]:
        return {"table": self.table, "field": self.field, "values": list(self.values)}


@dataclass(frozen=True)
class SampleSel:
    set_id: int
    table: str  # id as text or name (the store keeps the id as text)
    field: str
    unit: str | None  # filled by the store from the field
    domain: DomainRef | None
    soft: tuple[tuple[str, ...], float] | None  # (domains S, d_м); None — the hard boundary
    topcut: tuple[str, str | float] | None  # ("field", "AU_CUT") | ("value", 30.0); None — none
    weights: str | bool | None  # None — DECL_W when the table has it; False — none; a field name

    def canonical(self) -> dict[str, Any]:
        soft = None if self.soft is None else {"domains": list(self.soft[0]), "dist": self.soft[1]}
        top = None if self.topcut is None else {self.topcut[0]: self.topcut[1]}
        return {"set_id": self.set_id, "table": self.table, "field": self.field, "unit": self.unit,
                "domain": None if self.domain is None else self.domain.canonical(), "soft": soft, "topcut": top,
                "weights": self.weights}  # fmt: skip


def _soft(raw: Any, domain: DomainRef | None, param: str) -> tuple[tuple[str, ...], float]:
    so = obj(raw, "Мягкая граница", param)
    doms = _values(so.get("domains") or [], "Домены мягкой границы", f"{param}.domains")
    dist = ranged(so.get("dist"), "soft_dist", f"{param}.dist") or 0.0
    if domain is None and (doms or dist > 0.0):
        raise bad("bad_param", "Мягкая граница без домена проб.", "Выберите домен проб или уберите мягкую границу.",
                  param)  # fmt: skip
    if dist > 0.0 and not doms:
        raise bad("bad_param", "Мягкая граница: не выбраны соседние домены.",
                  "Отметьте домены, пробы которых допускаются у границы, или задайте расстояние 0.",
                  f"{param}.domains")  # fmt: skip
    if domain is not None and set(doms) & set(domain.values):
        raise bad("bad_param", "Домен мягкой границы совпадает с доменом проб.", "Уберите его из соседних доменов.",
                  f"{param}.domains")  # fmt: skip
    return doms, dist


def _topcut(raw: Any, param: str) -> tuple[str, str | float] | None:
    t = obj(raw, "Урезка", param)
    has_f, has_v = t.get("field") not in (None, ""), t.get("value") not in (None, "")
    if has_f and has_v:
        raise bad("bad_param", "Урезка: задано и поле, и порог.", "Оставьте что-то одно.", param)
    if has_f:
        return "field", str(text_of(t["field"], "Поле урезки", f"{param}.field", 100))
    if has_v:
        v = ranged(t["value"], "topcut", f"{param}.value")
        assert v is not None
        return "value", v
    return None


def parse_samples(raw: Any, param: str = "samples") -> SampleSel:
    s = obj(raw, "Пробы", param)
    set_id = id_of(s.get("set_id"), "набор скважин", f"{param}.set_id")
    assert set_id is not None
    table = text_of(s.get("table"), "Таблица проб", f"{param}.table")
    field = text_of(s.get("field"), "Поле содержания", f"{param}.field", 100)
    unit = text_of(s.get("unit"), "Единица", f"{param}.unit", 40, required=False)
    domain = None
    if s.get("domain") not in (None, ""):
        d = obj(s["domain"], "Домен проб", f"{param}.domain")
        values = _values(d.get("values"), "Значения домена", f"{param}.domain.values")
        if not values:
            raise bad("bad_param", "Домен проб: не выбраны значения.", "Отметьте одно или несколько значений.",
                      f"{param}.domain.values")  # fmt: skip
        dt = text_of(d.get("table"), "Таблица домена", f"{param}.domain.table", required=False)
        domain = DomainRef(dt, str(text_of(d.get("field"), "Поле домена", f"{param}.domain.field", 100)), values)
    soft = None if s.get("soft") in (None, "") else _soft(s["soft"], domain, f"{param}.soft")
    topcut = None if s.get("topcut") in (None, "") else _topcut(s["topcut"], f"{param}.topcut")
    w = s.get("weights")
    weights: str | bool | None = None
    if w is False or w == "":
        weights = False
    elif w is not None:
        weights = text_of(w, "Поле весов", f"{param}.weights", 100)
    return SampleSel(set_id, str(table), str(field), unit, domain, soft, topcut, weights)


# --- blocks, method, variogram models ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    domain_field: str | None  # a field of the block model (category or number); None — every block
    label: str | None  # its value of the row (8.8.2 «домен D»)
    filter_id: int | None  # the restrictor: a stored filter (object_refs role "filter") …
    rules: dict[str, Any] | None  # … or its rules (blocks/filters/schema.py)

    def canonical(self) -> dict[str, Any]:
        return {"domain_field": self.domain_field, "label": self.label, "filter_id": self.filter_id,
                "rules": self.rules}  # fmt: skip


def parse_target(raw: Any, param: str = "target") -> Target:
    t = obj(raw, "Блоки оценки", param)
    field = text_of(t.get("domain_field"), "Поле домена модели", f"{param}.domain_field", 100, required=False)
    label = None if t.get("label") in (None, "") else _value(t["label"], "Домен блоков", f"{param}.label")
    if field is not None and label is None:
        raise bad("bad_param", "Не выбран домен блоков.", "Выберите значение поля домена модели.", f"{param}.label")
    if field is None and label is not None:
        raise bad("bad_param", "Домен блоков без поля домена.", "Выберите поле домена модели.", f"{param}.domain_field")
    filter_id = id_of(t.get("filter_id"), "фильтр", f"{param}.filter_id", required=False)
    rules = None
    if t.get("rules") is not None:
        if filter_id is not None:
            raise bad("bad_param", "Ограничитель задан и фильтром, и правилами.", "Оставьте что-то одно.",
                      f"{param}.rules")  # fmt: skip
        rules = normalize_rules(t["rules"], f"{param}.rules")
    return Target(field, label, filter_id, rules)


@dataclass(frozen=True)
class Method:
    kind: str  # nn | idw | sk | ok | ik
    power: float  # IDW p
    mean_mode: str  # SK: declus | value
    mean_value: float | None
    thresholds: tuple[float, ...]  # IK, ascending; () for the others
    ik_models: str  # per | median
    class_means: str  # declus (m_k — the declustered means of the classes, ТЗ 8.8.1)

    def canonical(self) -> dict[str, Any]:
        ik = None if self.kind != "ik" else {"thresholds": list(self.thresholds), "models": self.ik_models,
                                             "class_means": self.class_means}  # fmt: skip
        return {"kind": self.kind, "power": self.power, "mean": {"mode": self.mean_mode, "value": self.mean_value},
                "ik": ik}  # fmt: skip


def _thresholds(raw: Any, p: str) -> tuple[float, ...]:
    given = list_of(raw, "Пороги", p, THRESHOLDS_MAX) if raw is not None else []
    out: list[float] = []
    for i, x in enumerate(given):
        v = ranged(x, "threshold", f"{p}.{i}")
        if v is None:
            raise bad("bad_param", "Порог индикатора: пусто.", "Задайте число.", f"{p}.{i}")
        if out and not v > out[-1]:
            raise bad("bad_param", "Пороги должны строго возрастать.", "Упорядочьте пороги по возрастанию.", f"{p}.{i}")
        out.append(v)
    if not out:
        raise bad("bad_param", "Не заданы пороги индикаторов.", f"Задайте от 1 до {THRESHOLDS_MAX} порогов.", p)
    return tuple(out)


def parse_method(raw: Any, param: str = "method") -> Method:
    m = obj(raw, "Метод", param)
    kind = choice(m.get("kind"), METHODS, "ok", "Метод оценки", f"{param}.kind")
    power = ranged(m.get("power"), "power", f"{param}.power")
    mean = obj(m.get("mean"), "Среднее SK", f"{param}.mean")
    mode = choice(mean.get("mode"), MEAN_MODES, "declus", "Среднее SK", f"{param}.mean.mode")
    value = ranged(mean.get("value"), "mean", f"{param}.mean.value") if mode == "value" else None
    if mode == "value" and value is None:
        raise bad("bad_param", "Среднее простого кригинга не задано.",
                  "Задайте число или выберите декластерированное среднее домена.", f"{param}.mean.value")  # fmt: skip
    ik = obj(m.get("ik"), "Индикаторы", f"{param}.ik")
    models = choice(ik.get("models"), IK_MODELS, "per", "Модели индикаторов", f"{param}.ik.models")
    means = choice(ik.get("class_means"), CLASS_MEANS, "declus", "Средние классов", f"{param}.ik.class_means")
    thresholds = _thresholds(ik.get("thresholds"), f"{param}.ik.thresholds") if kind == "ik" else ()
    return Method(kind, POWER if power is None else power, mode, value, thresholds, models, means)


@dataclass(frozen=True)
class VarioRef:
    model_id: int
    snapshot: dict[str, Any] | None  # the canonical model (`variogram.spec.parse_model`): copied by the store
    sha: str | None  # `meta.sha256` of the variogram model when it was copied
    scale: str | None  # z | back | y (Д-38): set by the store from `meta.transform` and `meta.back`
    share: float | None = None  # the Hermite share of `back` (< 0,95 — the warning of the wizard)
    t: float | None = None  # the threshold of a model of IK (`models: per`)

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {"model_id": self.model_id, "snapshot": self.snapshot, "sha": self.sha,
                               "scale": self.scale, "share": self.share}  # fmt: skip
        return out if self.t is None else {"t": self.t, **out}


def variogram_scale(param: str) -> Exception:
    """Д-38: a model in normal scores without its back transform in SK, OK, IK."""
    return bad("variogram_scale", "Модель построена в нормальных оценках",
               "Выполните обратное преобразование модели или выберите модель в исходных единицах", param)  # fmt: skip


def parse_vario(raw: Any, param: str, t: float | None = None, *, simulation: bool = False) -> VarioRef:
    """`{model_id, snapshot?, sha?, scale?, share?, t?}`. `scale: "y"` is a refusal 422 `variogram_scale` but in a
    simulation (Д-38: the model in normal scores goes only through its `back`)."""
    v = obj(raw, "Модель вариограммы", param)
    model_id = id_of(v.get("model_id"), "модель вариограммы", f"{param}.model_id")
    assert model_id is not None
    snap = None if v.get("snapshot") is None else parse_model(v["snapshot"], f"{param}.snapshot")
    sha = v.get("sha")
    if sha is not None and not (isinstance(sha, str) and _HEX.fullmatch(sha)):
        raise bad("bad_param", "Модель вариограммы: неверная контрольная сумма.", "Выберите модель заново.",
                  f"{param}.sha")  # fmt: skip
    scale = None if v.get("scale") in (None, "") else choice(v["scale"], SCALES, "z", "Шкала модели", f"{param}.scale")
    if scale == "y" and not simulation:
        raise variogram_scale(f"{param}.model_id")
    share = None if v.get("share") is None else _number(v["share"], "Доля Эрмита", f"{param}.share")
    if t is not None and v.get("t") is not None and ranged(v["t"], "threshold", f"{param}.t") != t:
        raise bad("bad_param", "Порог модели не совпадает с порогом индикатора.", "Выберите модели в порядке порогов.",
                  f"{param}.t")  # fmt: skip
    return VarioRef(model_id, snap, sha, scale, share, t)


# --- search and passes ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Hy:
    """Д-29: a sample above `t` takes part as it is only at h_s ≤ q·r1 of the pass; farther — dropped or capped."""

    t: float
    q: float
    mode: str  # drop | cap

    def canonical(self) -> dict[str, Any]:
        return {"t": self.t, "q": self.q, "mode": self.mode}


@dataclass(frozen=True)
class Pass:
    factor: float
    n_min: int = N_MIN
    n_max: int = N_MAX
    n_hole: int = 0
    n_oct: int = 0
    k_oct: int = 0
    n_holes_min: int = 0
    hy: Hy | None = None  # off by default (Д-29)

    def canonical(self) -> dict[str, Any]:
        return {"factor": self.factor, "n_min": self.n_min, "n_max": self.n_max, "n_hole": self.n_hole,
                "n_oct": self.n_oct, "k_oct": self.k_oct, "n_holes_min": self.n_holes_min,
                "hy": None if self.hy is None else self.hy.canonical()}  # fmt: skip


def _hy(raw: Any, param: str) -> Hy | None:
    h = obj(raw, "Ураганные пробы", param)
    t = ranged(h.get("t"), "hy_t", f"{param}.t")
    if t is None:
        return None  # t not given — off
    q = ranged(h.get("q"), "hy_q", f"{param}.q")
    if q is None:
        raise bad("bad_param", "Ураганные пробы: не задана доля радиуса q.", f"Задайте {_span(RANGES['hy_q'])}.",
                  f"{param}.q")  # fmt: skip
    return Hy(t, q, choice(h.get("mode"), HY_MODES, "drop", "Режим ураганных проб", f"{param}.mode"))


def _pass(raw: Any, i: int, param: str) -> Pass:
    p = obj(raw, "Проход", param)
    factor = ranged(p.get("factor"), "factor", f"{param}.factor")
    ints = {k: int_of(p.get(k), k, getattr(Pass, k), f"{param}.{k}")
            for k in ("n_min", "n_max", "n_hole", "n_oct", "k_oct", "n_holes_min")}  # fmt: skip
    if ints["n_min"] > ints["n_max"]:
        raise bad("bad_param", "n_min больше n_max.", f"Задайте n_min не больше {ints['n_max']}.", f"{param}.n_min")
    hy = None if p.get("hy") in (None, "") else _hy(p["hy"], f"{param}.hy")
    return Pass(float(i + 1) if factor is None else factor, **ints, hy=hy)


@dataclass(frozen=True)
class Search:
    source: str  # model | trend | manual
    angles: tuple[float, float, float] | None  # ang1–ang3; None — not resolved yet
    radii: tuple[float, float, float] | None  # r1–r3 of the first pass (× factor in the others)
    passes: tuple[Pass, ...]

    def canonical(self) -> dict[str, Any]:
        a, r = self.angles or (None, None, None), self.radii or (None, None, None)
        return {"from": self.source, "ang1": a[0], "ang2": a[1], "ang3": a[2], "r1": r[0], "r2": r[1], "r3": r[2],
                "passes": [p.canonical() for p in self.passes]}  # fmt: skip


def parse_search(raw: Any, *, has_model: bool, trend_id: int | None, param: str = "search") -> Search:
    s = obj(raw, "Поиск", param)
    default = "model" if has_model else ("trend" if trend_id is not None else "manual")
    source = choice(s.get("from"), SEARCH_FROM, default, "Эллипсоид поиска", f"{param}.from")
    if source == "model" and not has_model:
        raise bad("bad_param", "Эллипсоид по модели вариограммы: модель не выбрана.",
                  "Выберите модель вариограммы или задайте радиусы вручную.", f"{param}.from")  # fmt: skip
    if source == "trend" and trend_id is None:
        raise bad("bad_param", "Эллипсоид по тренду: тренд не выбран.", "Выберите тренд или задайте углы вручную.",
                  "trend_id")  # fmt: skip
    ang = tuple(_angle(s.get(k), k, f"{param}.{k}") for k in ("ang1", "ang2", "ang3"))
    rad = tuple(ranged(s.get(k), k, f"{param}.{k}") for k in ("r1", "r2", "r3"))
    angles: tuple[float, float, float] | None = None
    radii: tuple[float, float, float] | None = None
    if source == "manual":
        if rad[0] is None:
            raise bad("bad_param", "Радиус поиска r1 не задан.", f"Задайте {_span(RANGES['r1'])}.", f"{param}.r1")
        angles = (ang[0] or 0.0, ang[1] or 0.0, ang[2] or 0.0)
        radii = (rad[0], rad[1] or rad[0], rad[2] or rad[0])
    elif source == "trend" and rad[0] is not None:
        radii = (rad[0], rad[0], rad[0])  # r1 kept; r2, r3 — from the ratios of the trend (`resolved`)
    p = f"{param}.passes"
    given = list_of(s["passes"], "Проходы", p, PASSES_MAX) if s.get("passes") is not None else None
    if given is not None and not given:
        raise bad("bad_param", "Не задано ни одного прохода.", f"Задайте от 1 до {PASSES_MAX} проходов.", p)
    if given is None:
        return Search(source, angles, radii, tuple(Pass(f) for f in PASS_FACTORS))
    return Search(source, angles, radii, tuple(_pass(x, i, f"{p}.{i}") for i, x in enumerate(given)))


def search_from_model(snapshot: Mapping[str, Any], param: str = "search.from") -> tuple[tuple[float, ...], ...]:
    """(angles, radii) of the structure with the largest a1 (the first of equal ones) — «дальности модели»."""
    structures = list(snapshot.get("structures") or [])
    if not structures:
        raise bad("no_structures", "У модели вариограммы нет структур — радиусы поиска не из чего взять.",
                  "Задайте радиусы вручную или добавьте структуру в модель.", param)  # fmt: skip
    best = max(range(len(structures)), key=lambda i: (float(structures[i]["a1"]), -i))
    s = structures[best]
    return (float(s["ang1"]), float(s["ang2"]), float(s["ang3"])), (float(s["a1"]), float(s["a2"]), float(s["a3"]))


def search_from_trend(trend: Mapping[str, Any], r1: float | None) -> tuple[tuple[float, ...], ...]:
    """(angles, radii) of a trend (the form of `GET /trends/{id}`): its angles, r1 — the given one or its `size`,
    r2 = r1·ratio₂, r3 = r1·ratio₃."""
    ratios = trend.get("ratios") or [1.0, 1.0, 1.0]
    a = float(trend.get("size") or 0.0) if r1 is None else r1
    if not a > 0.0:
        raise bad("bad_param", "Тренд без размера: радиус r1 не из чего взять.", "Задайте радиус r1.", "search.r1")
    angles = (float(trend.get("ang1", 0.0)), float(trend.get("ang2", 0.0)), float(trend.get("ang3", 0.0)))
    return angles, (a, a * float(ratios[1]), a * float(ratios[2]))


# --- output fields -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Outputs:
    prefix: str
    fields: tuple[str, ...]  # keys of OUTPUTS in the canonical order, STATUS first; "P" — P1…PK of IK

    def canonical(self) -> dict[str, Any]:
        return {"prefix": self.prefix, "fields": list(self.fields)}


def default_prefix(field: str, kind: str) -> str:
    """«AU_OK_»: the field in upper case, every character that is not a letter, a digit or «_» — «_»."""
    base = re.sub(r"\W", "_", field.upper())[: PREFIX_MAX - len(kind) - 2]
    return f"{base}_{kind.upper()}_"


def parse_outputs(raw: Any, kind: str, field: str, *, hy: bool = False, param: str = "outputs") -> Outputs:
    """STATUS is always written (Д-27); NHY joins the default fields when a pass has the rule of Д-29 (`hy`)."""
    o = obj(raw, "Выходные поля", param)
    prefix = text_of(o.get("prefix"), "Префикс полей", f"{param}.prefix", PREFIX_MAX, required=False)
    prefix = default_prefix(field, kind) if prefix is None else prefix
    if not PREFIX_RE.fullmatch(prefix):
        raise bad("bad_param", "Префикс полей: допустимы буквы, цифры и «_».", "Например, AU_OK_.", f"{param}.prefix")
    allowed = ALLOWED[kind]
    if o.get("fields") is None:
        keys = set(DEFAULT_FIELDS[kind]) | ({"NHY"} if hy else set())
    else:
        keys = {"STATUS"}
        for i, x in enumerate(list_of(o["fields"], "Выходные поля", f"{param}.fields", len(OUTPUTS))):
            key = x.strip().upper() if isinstance(x, str) else ""
            if key not in allowed:
                raise bad("bad_param", f"Поле «{str(x)[:20]}» недоступно для метода {kind.upper()}.",
                          f"Допустимо: {', '.join(allowed)}.", f"{param}.fields.{i}")  # fmt: skip
            keys.add(key)
    return Outputs(prefix, tuple(k for k in allowed if k in keys))
