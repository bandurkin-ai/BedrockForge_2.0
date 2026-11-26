"""The other bodies of the pit routes (подшаг 21.1; ADR 0028 «API», решения 19, 25–27; docs/progress/e21-api.md §4–§12).
Pure: no database, no numpy.

    parse_check(body) -> PitSpec                   POST /pit/validate, POST /pit/check: {params}
    parse_scenario_create(body) -> ScenarioCreate  POST /pit/scenarios: {name?, params?}
    parse_scenario_update(body) -> ScenarioUpdate  PUT /pit/scenarios/{id}: {name?, params?, calendar?, version}
    parse_run(body) -> Run                         POST /pit/scenarios/{id}/run: {solver?}
    parse_report_query(q) -> ReportQuery           GET /pit/scenarios/{id}/report?calendar=<json>
    parse_surfaces(body) -> Surfaces               POST …/surfaces: {shells?}
    parse_solid(body) -> Solid                     POST …/solid: {shell, name?}
    parse_compare(body) -> Compare                 POST /pit/compare: {a, b}
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bf_core.geostat.estimate.spec_parts import id_of, list_of, obj, text_of
from bf_core.geostat.variogram.spec import bad

from .spec_parse import parse_calendar, parse_solver
from .spec_parts import MAX_SHELLS, NAME_MAX, Calendar, integer

if TYPE_CHECKING:
    from .spec import PitSpec


def _version(b: Mapping[str, Any]) -> int:
    v = b.get("expected_version")
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise bad("bad_param", "Не задана версия объекта.", "Обновите вкладку и повторите.", "expected_version")
    return v


def parse_check(body: Any) -> PitSpec:
    """`{params}` of `validate` and `check`: the params parsed (shapes and ranges)."""
    from .spec import parse_params  # the module spec imports this one

    b = obj(body, "Запрос", "body")
    if b.get("params") is None:
        raise bad("bad_param", "Не заданы параметры сценария.", "Передайте params.", "params")
    return parse_params(b["params"])


@dataclass(frozen=True)
class ScenarioCreate:
    name: str | None  # None — «Сценарий карьера» (a taken name — a suffix « (2)»)
    params: PitSpec  # none given — the defaults of a new scenario


def parse_scenario_create(body: Any) -> ScenarioCreate:
    from .spec import parse_params

    b = obj(body, "Запрос", "body")
    return ScenarioCreate(text_of(b.get("name"), "Имя сценария", "name", NAME_MAX, required=False),
                          parse_params(b.get("params") or {}))  # fmt: skip


@dataclass(frozen=True)
class ScenarioUpdate:
    name: str | None  # None — unchanged
    params: PitSpec | None  # None — unchanged
    calendar: Calendar | None  # None — unchanged; a view: it does not make the result stale
    expected_version: int


def parse_scenario_update(body: Any) -> ScenarioUpdate:
    from .spec import parse_params

    b = obj(body, "Запрос", "body")
    if all(b.get(k) is None for k in ("name", "params", "calendar")):
        raise bad("bad_param", "Нечего менять: не задано ни имя, ни параметры, ни календарь.",
                  "Передайте name, params или calendar.", "body")  # fmt: skip
    return ScenarioUpdate(
        text_of(b.get("name"), "Имя сценария", "name", NAME_MAX, required=False),
        None if b.get("params") is None else parse_params(b["params"]),
        None if b.get("calendar") is None else parse_calendar(b["calendar"]),
        _version(b),
    )  # fmt: skip


@dataclass(frozen=True)
class Run:
    solver: str | None  # None — the solver of the scenario


def parse_run(body: Any) -> Run:
    b = obj(body, "Запрос", "body")
    return Run(None if b.get("solver") in (None, "") else parse_solver(b["solver"]))


@dataclass(frozen=True)
class ReportQuery:
    calendar: Calendar | None  # None — the calendar of the scenario


def parse_report_query(q: Mapping[str, Any]) -> ReportQuery:
    """`calendar` — a JSON text `{"q_ore": 1e6, "q_mass": null, "rate": 0.1}` (a query string) or an object."""
    raw = q.get("calendar")
    if raw is None or raw == "":
        return ReportQuery(None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise bad("bad_param", "Календарь: неверный JSON.", "Передайте {q_ore, q_mass, rate}.",
                      "calendar") from None  # fmt: skip
    return ReportQuery(parse_calendar(raw))


@dataclass(frozen=True)
class Surfaces:
    shells: tuple[int, ...] | None  # numbers 1…N of the shells ascending; None — every shell (№ 124)


def parse_surfaces(body: Any) -> Surfaces:
    b = obj(body, "Запрос", "body")
    if b.get("shells") is None:
        return Surfaces(None)
    items = list_of(b["shells"], "Оболочки", "shells", MAX_SHELLS)
    if not items:
        raise bad("bad_param", "Оболочки: список пуст.", "Выберите оболочки или уберите ключ — будут все.", "shells")
    nums = [integer(v, "Номер оболочки", f"shells.{i}", 1, MAX_SHELLS) for i, v in enumerate(items)]
    return Surfaces(tuple(sorted(set(nums))))


@dataclass(frozen=True)
class Solid:
    shell: int
    name: str | None


def parse_solid(body: Any) -> Solid:
    b = obj(body, "Запрос", "body")
    if b.get("shell") is None:
        raise bad("bad_param", "Не выбрана оболочка.", "Выберите оболочку для каркаса.", "shell")
    return Solid(integer(b["shell"], "Номер оболочки", "shell", 1, MAX_SHELLS),
                 text_of(b.get("name"), "Имя каркаса", "name", NAME_MAX, required=False))  # fmt: skip


@dataclass(frozen=True)
class Compare:
    a: int
    b: int


def parse_compare(body: Any) -> Compare:
    """A = B is allowed (Δ = 0)."""
    x = obj(body, "Запрос", "body")
    a = id_of(x.get("a"), "сценарий A", "a")
    b = id_of(x.get("b"), "сценарий B", "b")
    assert a is not None and b is not None
    return Compare(a, b)
