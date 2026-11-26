"""Parameters of a pit scenario (подшаг 21.1; ТЗ 8.11.2, 8.11.5; ADR 0028 решения 3–12, 19–20, 30, 34; contract —
docs/progress/e21-api.md §2–§3, §14). Pure: no database, no numpy; the fields of the model come as plain dicts
(`Model.fields`), so the job, the store and the routes share one reading of the body.

    spec = parse_params(raw)                      # PitSpec; ApiError 422 `bad_param` {param}; incomplete is allowed
    spec = spec.resolved(model.fields, (z_lo, z_hi))   # defaults filled, fields and units checked against the model
    meta = spec.canonical()                       # `objects(kind="pit_scenario").meta["params"]`
    sha  = spec.params_hash()                     # sha256 of the canonical JSON + ALGO, without `calendar`, `solver`
    validate(raw_or_spec, context) -> {ready, missing, warnings}     # one source for «Запустить» and 422 pit_not_ready
    require_ready(raw_or_spec, context) -> PitSpec                   # raises 422 `pit_not_ready {missing}`

The readers of the parts are in `spec_parse.py`, the parts in `spec_parts.py`, the bodies of the other routes in
`spec_bodies.py`; all are re-exported here.
A key the body leaves out (or null, or "") takes its default: `rf` — 0,30…1,50 step 0,05; `slopes.table` — 45°
everywhere, `slopes.tolerance` 2,5°; `mining.h_gl` 10 m, `c_gl` 0; `calendar.rate` 0,08; `k` — auto; `prefix` — none;
`solver` — auto. A key the body does not know is ignored (the stored `meta` parses as it is). A value that is «not
set yet» (`model_id`, `price`, `c_p`, `c_m0`, `rec`) is `null` — the form of a new scenario; `validate` names it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from bf_core.blocks.filters.schema import normalize_rules
from bf_core.geostat.estimate.spec_parts import id_of, obj
from bf_core.geostat.variogram.spec import bad
from bf_core.resources.spec_parts import default_density, default_elements

from .spec_bodies import (
    Compare,
    ReportQuery,
    Run,
    ScenarioCreate,
    ScenarioUpdate,
    Solid,
    Surfaces,
    parse_check,
    parse_compare,
    parse_report_query,
    parse_run,
    parse_scenario_create,
    parse_scenario_update,
    parse_solid,
    parse_surfaces,
)
from .spec_parse import (
    parse_calendar,
    parse_density,
    parse_elements,
    parse_mining,
    parse_rf,
    parse_rocks,
    parse_slopes,
    parse_solver,
)
from .spec_parts import (
    ALGO,
    ALL_LABEL,
    ANGLE_MAX,
    ANGLE_MIN,
    EMPTY_LABEL,
    GRADE_UNITS,
    K_MAX,
    K_MIN,
    KIND,
    MAX_DOMAINS,
    MAX_ELEMENTS,
    MAX_RULES,
    MAX_SCENARIOS,
    MAX_SECTORS,
    MAX_SHELLS,
    MAX_TYPES,
    MONEY_MAX,
    NAME_MAX,
    OTHER_LABEL,
    PREFIX_MAX,
    RATE_MAX,
    REFUSALS,
    RF_DEFAULT,
    RF_MAX,
    RF_MIN,
    ROCK_BY_KINDS,
    SOLVERS,
    TOL_MAX,
    TOL_MIN,
    VERSION,
    Calendar,
    Domain,
    Element,
    Mining,
    RockBy,
    Rocks,
    RockType,
    Sector,
    Slopes,
    grade_k,
    grade_label,
    grade_unit,
    integer,
    prefix_of,
    price_label,
    refuse,
)

__all__ = ["ALGO", "ALL_LABEL", "Calendar", "Compare", "Domain", "EMPTY_LABEL", "Element", "KIND", "Mining",
           "OTHER_LABEL", "PitSpec", "REFUSALS", "ReportQuery", "RockBy", "RockType", "Rocks", "Run",
           "ScenarioCreate", "ScenarioUpdate", "Sector", "Slopes", "Solid", "Surfaces", "VERSION", "canonical_json",
           "defaults_json", "grade_k", "grade_label", "model_defaults", "parse_check", "parse_compare", "parse_params",
           "parse_report_query", "parse_run", "parse_scenario_create", "parse_scenario_update", "parse_solid",
           "parse_surfaces", "price_label", "ranges_json", "refuse", "require_ready", "validate"]  # fmt: skip


def canonical_json(value: Any) -> str:
    """The canonical text: keys sorted, no spaces, Russian text as is — the same dict in any key order gives the same
    text."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class PitSpec:
    model_id: int | None
    elements: tuple[Element, ...]
    density: str | float | None  # a field, a number > 0, None — by default (`resolved`)
    default_density: float | None  # for the blocks with no density (№ 118)
    rocks: Rocks
    mining: Mining
    rf: tuple[float, ...]  # ascending, 1…200 values of 0,01…5
    slopes: Slopes
    k: int | None  # None — auto (решение 11)
    terrain_id: int | None  # the wireframe of the surface; None — no terrain
    filter_id: int | None
    restrict: dict[str, Any] | None
    calendar: Calendar
    prefix: str
    solver: str
    resolved_: bool = False

    # --- the canonical form -----------------------------------------------------------------------

    def canonical(self) -> dict[str, Any]:
        """`meta["params"]`: JSON-able, the same defaults ⇒ the same dict; `parse_params(canonical)` is `self`."""
        return {
            "model_id": self.model_id,
            "elements": [e.canonical() for e in self.elements],
            "density": self.density,
            "default_density": self.default_density,
            "rocks": self.rocks.canonical(),
            "mining": self.mining.canonical(),
            "rf": list(self.rf),
            "slopes": self.slopes.canonical(),
            "k": self.k,
            "terrain_id": self.terrain_id,
            "filter_id": self.filter_id,
            "restrict": self.restrict,
            "calendar": self.calendar.canonical(),
            "prefix": self.prefix,
            "solver": self.solver,
        }

    def hash_params(self) -> dict[str, Any]:
        """What the result depends on: the canonical form without the calendar (a view of the result, решение 19) and
        the solver (every solver gives the same smallest closure, решение 16) + ALGO."""
        out = self.canonical()
        del out["calendar"], out["solver"]
        out["algo"] = ALGO
        return out

    def params_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.hash_params()).encode("utf-8")).hexdigest()

    # --- the model ---------------------------------------------------------------------------------

    def resolved(self, fields: Sequence[Mapping[str, Any]], z_range: tuple[float, float] | None = None) -> PitSpec:
        """The defaults filled and everything checked against the model: `fields` — `Model.fields` (name, kind, unit,
        role, labels, stats), `z_range` — the extent of the block centres ± half a block in Z (Z_ref is its top).
        404 `block_field_not_found {param}`, 422 `bad_param {param}`."""
        by_name = {str(f["name"]).casefold(): f for f in fields}

        def find(name: str, param: str, kind: str) -> Mapping[str, Any]:
            f = by_name.get(name.casefold())
            if f is None:
                raise refuse("block_field_not_found", f"Поле «{name[:80]}» не найдено.", param=param)
            if f["kind"] != kind:
                what = "числовое" if kind == "number" else "категорийное"
                raise bad("bad_param", f"Поле «{f['name']}» не {what}.", f"Выберите {what} поле.", param)
            return f

        elements: list[Element] = []
        for i, e in enumerate(self.elements):
            f = find(e.field, f"elements.{i}.field", "number")
            unit = e.unit or grade_unit(f.get("unit"), f"elements.{i}.unit")
            if unit is None:
                raise bad("bad_param", f"У поля «{f['name']}» не задана единица содержания.",
                          "Укажите единицу элемента: г/т, ppm, ppb или %.", f"elements.{i}.unit")  # fmt: skip
            elements.append(replace(e, field=str(f["name"]), unit=unit))
        renamed = {e.field.casefold(): n.field for e, n in zip(self.elements, elements, strict=True)}
        density: str | float | None = self.density
        if isinstance(density, str):
            density = str(find(density, "density", "number")["name"])
        elif density is None:
            density = default_density(fields)
            if density is None and self.default_density is not None:
                density = self.default_density  # no field of density at all: one number for every block
            if density is None:
                raise bad("bad_param", "Не найдена плотность: нет меняющегося поля SG или поля с ролью «плотность».",
                          "Укажите поле плотности или число, например 2,7.", "density")  # fmt: skip
        rocks = self.rocks
        types = tuple(replace(t, rec={renamed[k.casefold()]: v for k, v in t.rec.items()}) for t in rocks.types)
        by = rocks.by
        if by is not None and by.kind == "field":
            assert by.field is not None
            g = find(by.field, "rocks.by.field", "category")
            by = RockBy("field", str(g["name"]))
            labels = {str(x).casefold(): str(x) for x in g.get("labels") or ()} | {EMPTY_LABEL.casefold(): EMPTY_LABEL}
            types = tuple(replace(t, name=labels.get(t.name.casefold(), t.name)) for t in types)
        slopes = self.slopes
        if slopes.domain_field is not None:
            g = find(slopes.domain_field, "slopes.domain_field", "category")
            labels = {str(x).casefold(): str(x) for x in g.get("labels") or ()}
            slopes = replace(slopes, domain_field=str(g["name"]), domains=tuple(
                replace(d, label=labels.get(d.label.casefold(), d.label)) for d in slopes.domains))  # fmt: skip
        mining = self.mining
        if mining.z_ref is None:
            if z_range is None:
                raise bad("bad_param", "Z_ref: неизвестен верх сетки.", "Задайте отметку Z_ref.", "mining.z_ref")
            mining = replace(mining, z_ref=z_range[1] + 0.0)
        return replace(self, elements=tuple(elements), density=density,
                       rocks=Rocks(by, tuple(sorted(types, key=lambda t: t.name.casefold()))), slopes=slopes,
                       mining=mining, resolved_=True)  # fmt: skip


def _k_of(raw: Any) -> int | None:
    if raw is None or raw == "" or raw == "auto":
        return None
    return integer(raw, "Число уровней K", "k", K_MIN, K_MAX)


def parse_params(raw: Any) -> PitSpec:
    """`params` of a scenario (docs/progress/e21-api.md §2) checked for its shapes and ranges, not for completeness
    (`validate`) and not against the model (`PitSpec.resolved`). A canonical form parses to itself."""
    b = obj(raw, "Параметры сценария", "params")
    elements = parse_elements(b.get("elements"))
    quick = b.get("restrict")
    if quick is not None and not isinstance(quick, Mapping):
        raise bad("bad_param", "Ограничитель: нужен объект правил.", "Соберите правила в окне сценария.", "restrict")
    dens = b.get("default_density")
    return PitSpec(
        model_id=id_of(b.get("model_id"), "блочная модель", "model_id", required=False),
        elements=elements,
        density=parse_density(b.get("density")),
        default_density=None if dens in (None, "") else parse_density_value(dens),
        rocks=parse_rocks(b.get("rocks"), elements),
        mining=parse_mining(b.get("mining")),
        rf=parse_rf(b.get("rf")),
        slopes=parse_slopes(b.get("slopes")),
        k=_k_of(b.get("k")),
        terrain_id=id_of(b.get("terrain_id"), "рельеф", "terrain_id", required=False),
        filter_id=id_of(b.get("filter_id"), "фильтр", "filter_id", required=False),
        restrict=None if quick is None else normalize_rules(dict(quick), "restrict"),
        calendar=parse_calendar(b.get("calendar")),
        prefix=prefix_of(b.get("prefix")),
        solver=parse_solver(b.get("solver")),
    )  # fmt: skip


def parse_density_value(raw: Any) -> float:
    """`default_density`: a number > 0 only (a field is not a default)."""
    v = parse_density(raw, "default_density")
    if isinstance(v, str):
        raise bad("bad_param", "Плотность по умолчанию: нужно число.", "Например 2,7.", "default_density")
    assert v is not None
    return v


# --- the form of the window -----------------------------------------------------------------------------------------


def defaults_json() -> dict[str, Any]:
    """The defaults and the limits the window fills its form with (`GET /pit/defaults` → `params`, `choices`,
    `limits`); the default `params` is `parse_params({}).canonical()` (a new scenario)."""
    return {
        "params": parse_params({}).canonical(),
        "choices": {"solvers": list(SOLVERS), "rock_by": list(ROCK_BY_KINDS), "grade_units": list(GRADE_UNITS),
                    "grade_labels": {u: grade_label(u) for u in GRADE_UNITS},
                    "price_labels": {u: price_label(u) for u in GRADE_UNITS},
                    "rf_default": {"from": RF_DEFAULT[0], "to": RF_DEFAULT[1], "step": RF_DEFAULT[2]}},
        "limits": {"elements": MAX_ELEMENTS, "rock_types": MAX_TYPES, "rock_rules": MAX_RULES,
                   "sectors": MAX_SECTORS, "domains": MAX_DOMAINS, "shells": MAX_SHELLS, "scenarios": MAX_SCENARIOS,
                   "name": NAME_MAX, "prefix": PREFIX_MAX},
    }  # fmt: skip


def ranges_json() -> dict[str, Any]:
    """The ranges for the form: key → {lo, hi, lo_open, hi_open, integer, unit}."""

    def r(lo: float, hi: float, unit: str, *, lo_open: bool = False, hi_open: bool = False,
          integer: bool = False) -> dict[str, Any]:  # fmt: skip
        return {"lo": lo, "hi": hi, "lo_open": lo_open, "hi_open": hi_open, "integer": integer, "unit": unit}

    return {
        "rf": r(RF_MIN, RF_MAX, "×"), "k": r(K_MIN, K_MAX, "уровней", integer=True),
        "slopes.angle": r(ANGLE_MIN, ANGLE_MAX, "°"), "slopes.az": r(0.0, 360.0, "°", hi_open=True),
        "slopes.tolerance": r(TOL_MIN, TOL_MAX, "°"), "calendar.rate": r(0.0, RATE_MAX, "доля в год"),
        "calendar.q": r(0.0, MONEY_MAX, "т/год", lo_open=True), "mining.h_gl": r(0.0, 1e6, "м", lo_open=True),
        "recovery": r(0.0, 1.0, "доля"), "royalty": r(0.0, 1.0, "доля", hi_open=True),
        "money": r(0.0, MONEY_MAX, "$"), "density": r(0.0, 1e6, "т/м³", lo_open=True),
    }  # fmt: skip


def model_defaults(fields: Sequence[Mapping[str, Any]], z_range: tuple[float, float] | None = None) -> dict[str, Any]:
    """What `GET /pit/defaults?model_id=` adds for the model: the elements of the fields `*_CUT` / of the role `grade`
    (with the unit of the field when it is one of `GRADE_UNITS`), the field of density by default, the category
    fields (rocks, domains) with their labels, Z_ref (the top of the grid)."""
    by_name = {str(f["name"]): f for f in fields}
    elements = []
    for name in default_elements(fields):
        unit = None
        try:
            unit = grade_unit(by_name[name].get("unit"), "elements")
        except Exception:  # an unknown unit is shown as «не задана»
            unit = None
        elements.append({"field": name, "unit": unit})
    cats = [{"field": str(f["name"]), "labels": [str(x) for x in f.get("labels") or ()]}
            for f in fields if f["kind"] == "category"]  # fmt: skip
    return {"elements": elements, "density": default_density(fields), "categories": cats,
            "z_ref": None if z_range is None else z_range[1] + 0.0}  # fmt: skip


# --- completeness (решение 4): «Запустить» and 422 `pit_not_ready` ------------------------------------------------


def _required_types(spec: PitSpec, ctx: Mapping[str, Any], by_name: Mapping[str, Any] | None) -> list[str]:
    """The rock types that need an economics: the types the data meets (`context["rock_types"]`, the job reads them),
    else the labels of the rock field / the names of the rules / one type for every block."""
    met = ctx.get("rock_types")
    if met is not None:
        return [str(x) for x in met]
    by = spec.rocks.by
    if by is None:
        return [ALL_LABEL]
    if by.kind == "rules":
        return [r.name for r in by.rules]
    f = None if by_name is None or by.field is None else by_name.get(by.field.casefold())
    return [] if f is None else [str(x) for x in f.get("labels") or ()]


def validate(params: Any, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """`{ready, missing: [{param, message}], warnings: [{code, message, hint, param?}]}` — the one source of the button
    «Запустить» and of 422 `pit_not_ready`. `params` — a body or a `PitSpec` (a shape error is 422 `bad_param`).
    `context` (all keys optional; without `fields` only the shapes of the scenario are checked): `fields` —
    `Model.fields`; `rock_types` — the labels of the rock types the data meets (a type met needs c_p and rec_e of
    every element, or «только пустая» / «воздух»; blocks with no grade at all are waste and are not met);
    `missing_density_blocks` — the number of blocks with no density.
    `missing[].param` is a path with the NAME of a type: "elements.0.price", "rocks.types.Окисленная.rec.AU"."""
    spec = params if isinstance(params, PitSpec) else parse_params(params)
    ctx: Mapping[str, Any] = context or {}
    fields = ctx.get("fields")
    by_name = None if fields is None else {str(f["name"]).casefold(): f for f in fields}
    missing: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def miss(param: str, message: str) -> None:
        missing.append({"param": param, "message": message})

    def warn(code: str, param: str, message: str, hint: str) -> None:
        warnings.append({"code": code, "param": param, "message": message, "hint": hint})

    if spec.model_id is None:
        miss("model_id", "Не выбрана блочная модель.")
    if not spec.elements:
        miss("elements", "Не заданы элементы: добавьте хотя бы один элемент с ценой.")
    for i, e in enumerate(spec.elements):
        f = None if by_name is None else by_name.get(e.field.casefold())
        if by_name is not None and (f is None or f["kind"] != "number"):
            miss(f"elements.{i}.field", f"Поле содержания «{e.field}» не найдено в модели.")
        elif f is not None and e.unit is None:
            try:
                unit = grade_unit(f.get("unit"), f"elements.{i}.unit")
            except Exception:
                unit = None
            if unit is None:
                miss(f"elements.{i}.unit", f"Не задана единица содержания элемента {e.field}.")
        if e.price is None:
            miss(f"elements.{i}.price", f"Не задана цена элемента {e.field}.")
        elif e.price <= e.sales:
            warn("price_not_above_sales", f"elements.{i}.price", f"Цена {e.field} не больше затрат на сбыт.",
                 "Элемент не даст выручки при RF = 1.")  # fmt: skip
    if by_name is not None:
        dens = spec.density
        if isinstance(dens, str):
            if dens.casefold() not in by_name:
                miss("density", f"Поле плотности «{dens}» не найдено в модели.")
        elif dens is None and default_density(fields or []) is None and spec.default_density is None:
            miss("density", "Не задана плотность: нет поля плотности и числа по умолчанию.")
        if (
            (ctx.get("missing_density_blocks") or 0) > 0
            and spec.default_density is None
            and not isinstance(dens, float)
        ):
            miss("default_density", f"У {ctx['missing_density_blocks']} блоков нет плотности: задайте плотность по "
                 "умолчанию.")  # fmt: skip
        by = spec.rocks.by
        if by is not None and by.kind == "field" and by.field is not None:
            g = by_name.get(by.field.casefold())
            if g is None or g["kind"] != "category":
                miss("rocks.by.field", f"Категорийное поле пород «{by.field}» не найдено в модели.")
        sd = spec.slopes.domain_field
        if sd is not None:
            g = by_name.get(sd.casefold())
            if g is None or g["kind"] != "category":
                miss("slopes.domain_field", f"Категорийное поле доменов «{sd}» не найдено в модели.")
            else:
                known = {str(x).casefold() for x in g.get("labels") or ()}
                for d in spec.slopes.domains:
                    if d.label.casefold() not in known:
                        warn(
                            "domain_unknown",
                            f"slopes.domains.{d.label}",
                            f"Домена «{d.label}» нет среди подписей поля.",
                            "Его таблица не будет использована.",
                        )
    need = _required_types(spec, ctx, by_name)
    used: set[str] = set()
    for name in need:
        t = spec.rocks.type_of(name)
        if t is None and spec.rocks.by is None and name == ALL_LABEL and len(spec.rocks.types) == 1:
            t = spec.rocks.types[0]  # one type for every block may be named as the user likes
        if t is not None:
            used.add(t.name.casefold())
        if t is None:
            miss(f"rocks.types.{name}", f"Не заданы параметры типа породы «{name}».")
        elif not (t.air or t.waste_only):
            p = f"rocks.types.{t.name}"
            if t.c_p is None:
                miss(f"{p}.c_p", f"Не заданы затраты на переработку типа «{t.name}».")
            for e in spec.elements:
                if e.field not in t.rec:
                    miss(f"{p}.rec.{e.field}", f"Не задано извлечение {e.field} для типа «{t.name}».")
    if "rock_types" in ctx or by_name is not None:
        for t in spec.rocks.types:
            if t.name.casefold() not in used and t.name not in (EMPTY_LABEL, OTHER_LABEL):
                warn("type_unused", f"rocks.types.{t.name}", f"Тип породы «{t.name}» в модели не встречается.",
                     "Его параметры не используются.")  # fmt: skip
    if spec.mining.c_m0 is None:
        miss("mining.c_m0", "Не заданы затраты на добычу c_m0.")
    if spec.terrain_id is None:
        warn("no_terrain", "terrain_id", "Рельеф не задан: блоки не обрезаются по поверхности.",
             "Выберите каркас рельефа, если модель выходит за поверхность.")  # fmt: skip
    if spec.calendar.q_ore is None:
        warn("no_calendar", "calendar.q_ore", "Не задана переработка Q_р: NPV не будет рассчитан.",
             "Задайте календарь в окне «Календарь».")  # fmt: skip
    return {"ready": not missing, "missing": missing, "warnings": warnings}


def require_ready(params: Any, context: Mapping[str, Any] | None = None) -> PitSpec:
    """The scenario of a run: the same check as `validate`, 422 `pit_not_ready {missing, param}` with the first
    missing item in the message."""
    spec = params if isinstance(params, PitSpec) else parse_params(params)
    res = validate(spec, context)
    if not res["ready"]:
        first = res["missing"][0]
        raise refuse("pit_not_ready", f"Сценарий не готов к расчёту: {first['message']}", missing=res["missing"],
                     param=first["param"])  # fmt: skip
    return spec
