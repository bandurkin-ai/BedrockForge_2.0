"""The readers of the parts of a pit scenario (подшаг 21.1; ТЗ 8.11.2, 8.11.5; ADR 0028 решения 3–6, 10, 19): shapes
and ranges only, nothing is checked against a model (that is `PitSpec.resolved`) or for completeness (that is
`validate`). Pure: no database, no numpy. `param` of an error is the path inside `params`: "elements.1.price",
"rocks.types.0.rec.AU", "slopes.table.2.angle", "rf.step", "calendar.rate".

    parse_elements(raw) · parse_density(raw) · parse_rf(raw) · parse_rocks(raw, elements) · parse_slopes(raw)
    parse_mining(raw) · parse_calendar(raw) · parse_solver(raw)
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from bf_core.blocks.filters.schema import normalize_rules
from bf_core.geostat.estimate.spec_parts import list_of, obj
from bf_core.geostat.variogram.spec import bad

from .spec_parts import (
    ANGLE_MAX,
    ANGLE_MIN,
    C_GL_DEFAULT,
    H_GL_DEFAULT,
    MAX_DOMAINS,
    MAX_ELEMENTS,
    MAX_RULES,
    MAX_SECTORS,
    MAX_SHELLS,
    MAX_TYPES,
    MONEY_MAX,
    OTHER_LABEL,
    RATE_DEFAULT,
    RATE_MAX,
    RF_DEFAULT,
    RF_MAX,
    RF_MIN,
    ROCK_BY_KINDS,
    SOLVERS,
    TOL_DEFAULT,
    TOL_MAX,
    TOL_MIN,
    Calendar,
    Domain,
    Element,
    Mining,
    RockBy,
    RockRule,
    Rocks,
    RockType,
    Sector,
    Slopes,
    clean,
    flag,
    grade_unit,
    name_of,
    num,
    num_or,
    opt_num,
)

# --- elements, density -----------------------------------------------------------------------------------------------


def parse_elements(raw: Any) -> tuple[Element, ...]:
    """≤ 20 elements: a field name or {field, unit?, price?, sales?, royalty?}; `price` — P_e (None — not set yet),
    `sales` — S_e (default 0), `royalty` — roy_e ∈ [0; 1) (default 0); a repeat of a field is refused."""
    if raw is None:
        return ()
    out: list[Element] = []
    for i, it in enumerate(list_of(raw, "Элементы", "elements", MAX_ELEMENTS)):
        p = f"elements.{i}"
        b: Mapping[str, Any] = it if isinstance(it, Mapping) else {"field": it}
        name = name_of(b.get("field"), "Элемент", f"{p}.field")
        if any(e.field.casefold() == name.casefold() for e in out):
            raise bad("bad_param", f"Элемент «{name}» повторяется.", "Уберите повтор.", p)
        out.append(Element(
            name,
            grade_unit(b.get("unit"), f"{p}.unit"),
            opt_num(b.get("price"), "Цена P", f"{p}.price", 0.0, MONEY_MAX),
            num_or(b.get("sales"), 0.0, "Сбыт S", f"{p}.sales", 0.0, MONEY_MAX),
            num_or(b.get("royalty"), 0.0, "Роялти", f"{p}.royalty", 0.0, 1.0, hi_open=True,
                   hint="Задайте долю от 0 до 1 (не включая 1), например 0,03."),
        ))  # fmt: skip
    return tuple(out)


def parse_density(raw: Any, param: str = "density") -> str | float | None:
    """A field name, a number > 0 or None (the field of the role `density` is taken by `validate` / `resolved`)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, str):
        try:
            raw = float(raw.strip().replace(",", "."))
        except ValueError:
            return name_of(raw, "Плотность", param)
    return num(raw, "Плотность", param, 0.0, 1e6, lo_open=True, hint="Например 2,7 или имя поля плотности.")


# --- RF --------------------------------------------------------------------------------------------------------------


def parse_rf(raw: Any) -> tuple[float, ...]:
    """A list (≤ 200 of 0,01…5) or {from, to, step}; the canonical form is the list ascending without repeats
    (the noise of the sum is removed: 0,3 + 3·0,05 → 0,45); none — 0,30…1,50 step 0,05 (25 shells)."""
    if raw is None:
        raw = {"from": RF_DEFAULT[0], "to": RF_DEFAULT[1], "step": RF_DEFAULT[2]}
    if isinstance(raw, Mapping):
        lo = num(raw.get("from"), "RF «от»", "rf.from", RF_MIN, RF_MAX)
        hi = num(raw.get("to"), "RF «до»", "rf.to", RF_MIN, RF_MAX)
        step = num(raw.get("step"), "Шаг RF", "rf.step", 0.0, RF_MAX, lo_open=True)
        if hi < lo:
            raise bad("bad_param", "RF: «до» меньше «от».", "Задайте «до» не меньше «от».", "rf.to")
        ratio = (hi - lo) / step * (1 + 1e-12) + 1e-9
        if not math.isfinite(ratio) or math.floor(ratio) + 1 > MAX_SHELLS:
            raise bad("bad_param", f"Оболочек больше {MAX_SHELLS}.", "Увеличьте шаг или сузьте диапазон RF.", "rf.step")
        return tuple(dict.fromkeys(clean(lo + i * step) for i in range(math.floor(ratio) + 1)))
    if not isinstance(raw, list) or not raw or len(raw) > MAX_SHELLS:
        raise bad("bad_param", f"RF: список из 1–{MAX_SHELLS} чисел или диапазон {{от, до, шаг}}.",
                  "Например [0,8; 1; 1,2] или {от: 0,3, до: 1,5, шаг: 0,05}.", "rf")  # fmt: skip
    values = (clean(num(v, "RF", f"rf.{i}", RF_MIN, RF_MAX)) for i, v in enumerate(raw))
    return tuple(sorted(dict.fromkeys(values)))


# --- rocks -----------------------------------------------------------------------------------------------------------


def _rock_by(raw: Any) -> RockBy | None:
    if raw is None:
        return None
    b = obj(raw, "Источник пород", "rocks.by")
    kind = b.get("kind") or ("field" if b.get("field") is not None else "rules" if b.get("rules") is not None else None)
    if kind not in ROCK_BY_KINDS:
        raise bad("bad_param", "Породы: задайте поле или правила.", "Выберите категорийное поле или составьте правила.",
                  "rocks.by")  # fmt: skip
    if kind == "field":
        return RockBy("field", name_of(b.get("field"), "Поле пород", "rocks.by.field"))
    rules: list[RockRule] = []
    for i, it in enumerate(list_of(b.get("rules"), "Правила пород", "rocks.by.rules", MAX_RULES)):
        p = f"rocks.by.rules.{i}"
        r = obj(it, "Правило породы", p)
        name = name_of(r.get("name"), "Имя типа породы", f"{p}.name")
        if any(x.name.casefold() == name.casefold() for x in rules) or name.casefold() == OTHER_LABEL.casefold():
            raise bad("bad_param", f"Тип «{name}» повторяется или совпадает с «{OTHER_LABEL}».",
                      "Дайте типу уникальное имя.", f"{p}.name")  # fmt: skip
        if not isinstance(r.get("rules"), Mapping):
            raise bad("bad_param", "Правило породы: нужно дерево условий.", "Соберите условия в окне «Породы».",
                      f"{p}.rules")  # fmt: skip
        rules.append(RockRule(name, normalize_rules(dict(r["rules"]), f"{p}.rules")))
    if not rules:
        raise bad("bad_param", "Правила пород: список пуст.", "Добавьте хотя бы одно правило.", "rocks.by.rules")
    return RockBy("rules", None, tuple(rules))


def _rec(raw: Any, elements: tuple[Element, ...], param: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in obj(raw, "Извлечения", param).items():
        el = next((e for e in elements if e.field.casefold() == str(key).casefold()), None)
        if el is None:
            raise bad("bad_param", f"Извлечение задано для «{key}», такого элемента нет.",
                      "Добавьте элемент или уберите извлечение.", f"{param}.{key}")  # fmt: skip
        out[el.field] = num(value, f"Извлечение {el.field}", f"{param}.{key}", 0.0, 1.0,
                            hint="Задайте долю от 0 до 1, например 0,9.")  # fmt: skip
    return out


def parse_rocks(raw: Any, elements: tuple[Element, ...]) -> Rocks:
    """`{by, types}`: `by` — {kind: "field", field} / {kind: "rules", rules: [{name, rules}]} / None (one type for every
    block); a type — {name, c_p?, c_mr?, rec?: {element: 0…1}, waste_only?, air?}. A key the type leaves out stays
    «not set» (`validate`), except c_mr (0), `waste_only` and `air` (false)."""
    b = obj(raw, "Породы", "rocks")
    types: list[RockType] = []
    items = [] if b.get("types") is None else list_of(b["types"], "Типы пород", "rocks.types", MAX_TYPES)
    for i, it in enumerate(items):
        p = f"rocks.types.{i}"
        t = obj(it, "Тип породы", p)
        name = name_of(t.get("name"), "Имя типа породы", f"{p}.name")
        if any(x.name.casefold() == name.casefold() for x in types):
            raise bad("bad_param", f"Тип «{name}» повторяется.", "Уберите повтор.", p)
        waste, air = (
            flag(t.get("waste_only"), False, "«Только пустая»", f"{p}.waste_only"),
            flag(t.get("air"), False, "«Воздух»", f"{p}.air"),
        )
        if waste and air:
            raise bad("bad_param", f"Тип «{name}»: «воздух» и «только пустая» вместе.",
                      "Оставьте одну из отметок.", f"{p}.air")  # fmt: skip
        types.append(RockType(
            name,
            opt_num(t.get("c_p"), "Затраты на переработку c_p", f"{p}.c_p", 0.0, MONEY_MAX),
            num_or(t.get("c_mr"), 0.0, "Доплата за добычу руды c_mр", f"{p}.c_mr", 0.0, MONEY_MAX),
            _rec(t.get("rec"), elements, f"{p}.rec"),
            waste,
            air,
        ))  # fmt: skip
    return Rocks(_rock_by(b.get("by")), tuple(sorted(types, key=lambda x: x.name.casefold())))


# --- slopes ----------------------------------------------------------------------------------------------------------


def parse_table(raw: Any, param: str) -> tuple[Sector, ...]:
    """The table φ(az): 1–16 rows {az, angle}; az ∈ [0; 360) (a lone row may leave it out — a constant angle),
    angle ∈ [10; 80]°; the rows are sorted by the azimuth, a repeat of an azimuth is refused."""
    items = list_of(raw, "Таблица углов", param, MAX_SECTORS)
    if not items:
        raise bad("bad_param", "Таблица углов пуста.", "Задайте хотя бы одну строку: азимут и угол.", param)
    out: list[Sector] = []
    for i, it in enumerate(items):
        p = f"{param}.{i}"
        r = obj(it, "Сектор", p)
        az = 0.0 if len(items) == 1 and r.get("az") is None else num(
            r.get("az"), "Азимут", f"{p}.az", 0.0, 360.0, hi_open=True,
            hint="Азимут центра сектора от севера по часовой, от 0 до 360 (не включая 360).")  # fmt: skip
        angle = num(r.get("angle"), "Угол борта", f"{p}.angle", ANGLE_MIN, ANGLE_MAX,
                    hint=f"Задайте угол от {ANGLE_MIN:g} до {ANGLE_MAX:g} градусов.")  # fmt: skip
        if any(s.az == clean(az) for s in out):
            raise bad("bad_param", f"Азимут {clean(az):g}° повторяется.", "Уберите повтор или измените азимут.",
                      f"{p}.az")  # fmt: skip
        out.append(Sector(clean(az), clean(angle)))
    return tuple(sorted(out, key=lambda s: s.az))


def parse_slopes(raw: Any) -> Slopes:
    """`{table, domain_field, domains: [{label, table}], tolerance}`; none — 45° everywhere, tolerance 2,5°."""
    b = obj(raw, "Углы бортов", "slopes")
    table = parse_table(b["table"], "slopes.table") if b.get("table") is not None else (Sector(0.0, 45.0),)
    field = (
        None
        if b.get("domain_field") in (None, "")
        else name_of(b["domain_field"], "Поле доменов", "slopes.domain_field")
    )
    domains: list[Domain] = []
    items = [] if b.get("domains") is None else list_of(b["domains"], "Домены", "slopes.domains", MAX_DOMAINS)
    for i, it in enumerate(items):
        p = f"slopes.domains.{i}"
        d = obj(it, "Домен", p)
        label = name_of(d.get("label"), "Подпись домена", f"{p}.label")
        if any(x.label.casefold() == label.casefold() for x in domains):
            raise bad("bad_param", f"Домен «{label}» повторяется.", "Уберите повтор.", p)
        domains.append(Domain(label, parse_table(d.get("table"), f"{p}.table")))
    if domains and field is None:
        raise bad("bad_param", "Домены заданы без поля доменов.", "Выберите категорийное поле доменов.",
                  "slopes.domain_field")  # fmt: skip
    tol = num_or(b.get("tolerance"), TOL_DEFAULT, "Допуск угла", "slopes.tolerance", TOL_MIN, TOL_MAX,
                 hint=f"Задайте допуск от {TOL_MIN:g} до {TOL_MAX:g} градусов.")  # fmt: skip
    return Slopes(table, field, tuple(sorted(domains, key=lambda x: x.label.casefold())), tol)


# --- mining cost, calendar, solver ----------------------------------------------------------------------------------


def parse_mining(raw: Any) -> Mining:
    b = obj(raw, "Затраты на добычу", "mining")
    return Mining(
        opt_num(b.get("c_m0"), "Затраты на добычу c_m0", "mining.c_m0", 0.0, MONEY_MAX),
        opt_num(b.get("z_ref"), "Отметка Z_ref", "mining.z_ref", -MONEY_MAX, MONEY_MAX),
        num_or(b.get("h_gl"), H_GL_DEFAULT, "Шаг глубины H_гл", "mining.h_gl", 0.0, 1e6, lo_open=True),
        num_or(b.get("c_gl"), C_GL_DEFAULT, "Надбавка за глубину c_гл", "mining.c_gl", 0.0, MONEY_MAX),
    )  # fmt: skip


def parse_calendar(raw: Any, param: str = "calendar") -> Calendar:
    """`{q_ore, q_mass, rate}`: Q_р and Q_гм > 0 (t/year; none — not set / no limit), r ∈ [0; 0,5] (8 % = 0,08)."""
    b = obj(raw, "Календарь", param)
    return Calendar(
        opt_num(b.get("q_ore"), "Переработка Q_р", f"{param}.q_ore", 0.0, MONEY_MAX, lo_open=True),
        opt_num(b.get("q_mass"), "Горная масса Q_гм", f"{param}.q_mass", 0.0, MONEY_MAX, lo_open=True),
        num_or(b.get("rate"), RATE_DEFAULT, "Ставка дисконтирования r", f"{param}.rate", 0.0, RATE_MAX,
               hint="Задайте долю от 0 до 0,5 (8 % — 0,08)."),
    )  # fmt: skip


def parse_solver(raw: Any, param: str = "solver") -> str:
    if raw is None or raw == "":
        return "auto"
    if raw not in SOLVERS:
        raise bad("bad_param", "Решатель: auto, hpf или ortools.", f"Допустимо: {', '.join(SOLVERS)}.", param)
    return str(raw)
