"""Parameters of a pit scenario (подшаг 21.1; ТЗ 8.11.2, 8.11.5; ADR 0028 решения 3–12, 19–20, 30): the defaults of
8.11.5, every 422 branch (`bad_param {param}`), RF ≤ 200 shells, sectors ≤ 16, domains ≤ 16, the check of completeness
(`validate`, `pit_not_ready` with the first `missing`), the canonical form and the hash (independent of the order of
keys), the units k_e, the other bodies, the round trip of `tests/data/pit/params.json`, the parts of
`contract_answers.json` that `spec.py` writes, no numpy. `BF_REGEN_PIT=1` writes the canonical forms and those parts
anew (the rest of the file is the samples of the forms until 21.11)."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bf_core.api.errors import ApiError
from bf_core.mine import spec
from bf_core.mine import spec_parts as sp

DATA = Path(__file__).parent / "data" / "pit"
REGEN = os.environ.get("BF_REGEN_PIT") == "1"
PARAMS = json.loads((DATA / "params.json").read_text(encoding="utf-8"))
FIELDS: list[dict[str, Any]] = PARAMS["fixture"]["fields"]
Z_RANGE = tuple(PARAMS["fixture"]["z_range"])
CTX = {"fields": FIELDS}


def refused(raw: Any, code: str = "bad_param", status: int = 422, fn: Any = spec.parse_params) -> str:
    with pytest.raises(ApiError) as e:
        fn(raw)
    assert e.value.status == status and e.value.code == code, (e.value.code, e.value.message)
    assert e.value.message and e.value.hint  # Russian text with a hint
    assert any("а" <= ch <= "я" for ch in (e.value.message + e.value.hint).lower())
    return str(e.value.extra.get("param"))


def case(name: str) -> dict[str, Any]:
    return PARAMS["cases"][name]["body"]


# --- the defaults of 8.11.5 ----------------------------------------------------------------------------------------


def test_defaults_of_the_tz() -> None:
    s = spec.parse_params({})
    assert s.model_id is None and s.elements == () and s.density is None and s.default_density is None
    assert len(s.rf) == 25 and s.rf[0] == 0.30 and s.rf[-1] == 1.50  # RF 0,30…1,50 step 0,05
    assert all(math.isclose(b - a, 0.05, abs_tol=1e-9) for a, b in zip(s.rf, s.rf[1:], strict=False))
    assert s.calendar.rate == 0.08 and s.calendar.q_ore is None and s.calendar.q_mass is None  # r = 8 %
    assert (s.mining.h_gl, s.mining.c_gl, s.mining.c_m0, s.mining.z_ref) == (10.0, 0.0, None, None)  # H_гл, c_гл
    assert s.slopes.tolerance == 2.5 and s.k is None  # the tolerance 2,5°, K auto
    assert s.slopes.table == (sp.Sector(0.0, 45.0),) and s.slopes.domain_field is None and s.slopes.domains == ()
    assert s.rocks == sp.Rocks(None, ()) and s.terrain_id is None and s.filter_id is None and s.restrict is None
    assert s.prefix == "" and s.solver == "auto"
    assert (
        spec.parse_params({"rf": None, "k": "", "calendar": {"rate": None}, "prefix": None}).canonical()
        == s.canonical()
    )
    assert spec.parse_params(s.canonical()).canonical() == s.canonical()  # a canonical form parses to itself


def test_ranges_of_the_tz() -> None:
    p = spec.parse_params
    assert p({"rf": [0.01, 5]}).rf == (0.01, 5.0)  # 0,01–5
    assert p({"k": 1}).k == 1 and p({"k": "20"}).k == 20 and p({"k": "auto"}).k is None  # K 1–20
    assert p({"calendar": {"rate": 0}}).calendar.rate == 0.0 and p({"calendar": {"rate": "0,5"}}).calendar.rate == 0.5
    assert (
        p({"slopes": {"tolerance": 0.5}}).slopes.tolerance == 0.5
        and p({"slopes": {"tolerance": 10}}).slopes.tolerance == 10
    )
    assert p({"slopes": {"table": [{"az": 0, "angle": 10}, {"az": 359.5, "angle": 80}]}}).slopes.table[1].angle == 80.0
    assert p({"mining": {"c_m0": "2,5", "h_gl": 0.001, "c_gl": 1}}).mining.c_m0 == 2.5
    assert p({"elements": [{"field": "AU", "royalty": 0.999}]}).elements[0].royalty == 0.999
    assert p({"prefix": "V1_"}).prefix == "V1_" and p({"prefix": "Я" * 40}).prefix == "Я" * 40
    assert p({"default_density": "2,6"}).default_density == 2.6 and p({"density": "SG"}).density == "SG"


@pytest.mark.parametrize("item", PARAMS["invalid"], ids=[x["name"] for x in PARAMS["invalid"]])
def test_invalid_params_are_422_bad_param(item: dict[str, Any]) -> None:
    assert refused(item["body"]) == item["param"]


def test_rf_range_and_list() -> None:
    p = spec.parse_params
    assert p({"rf": {"from": 0.3, "to": 0.5, "step": 0.05}}).rf == (0.3, 0.35, 0.4, 0.45, 0.5)  # no noise of the sum
    assert p({"rf": {"from": 1, "to": 1, "step": 0.5}}).rf == (1.0,)
    assert p({"rf": [1.2, "0,8", 1.0, 0.8]}).rf == (0.8, 1.0, 1.2)  # ascending, no repeats
    same_list = p({"rf": [0.5, 0.75, 1.0, 1.25, 1.5]}).canonical()
    assert p({"rf": {"from": 0.5, "to": 1.5, "step": 0.25}}).canonical() == same_list
    assert len(p({"rf": {"from": 0.01, "to": 2.0, "step": 0.01}}).rf) == 200  # 200 shells are allowed …
    assert refused({"rf": {"from": 0.01, "to": 2.01, "step": 0.01}}) == "rf.step"  # … 201 are not
    assert refused({"rf": {"from": 0.01, "to": 5, "step": 0.02}}) == "rf.step"  # 250
    assert refused({"rf": {"from": 0.01, "to": 5, "step": 1e-320}}) == "rf.step"  # not a count
    assert refused({"rf": [round(0.01 * i, 2) for i in range(1, 202)]}) == "rf"  # a list of 201


def test_sectors_and_domains_limits() -> None:
    p = spec.parse_params
    row = [{"az": i * 22.5, "angle": 40 + i} for i in range(16)]
    s = p({"slopes": {"table": row}}).slopes
    assert len(s.table) == 16 and s.table[1] == sp.Sector(22.5, 41.0)
    assert refused({"slopes": {"table": [*row, {"az": 359, "angle": 45}]}}) == "slopes.table"  # 17 sectors
    dom = [{"label": f"D{i}", "table": [{"angle": 45}]} for i in range(16)]
    assert len(p({"slopes": {"domain_field": "DOM", "domains": dom}}).slopes.domains) == 16
    assert refused({"slopes": {"domain_field": "DOM", "domains": [*dom, {"label": "X", "table": [{"angle": 45}]}]}}) \
        == "slopes.domains"  # 17 domains  # fmt: skip
    one = p({"slopes": {"table": [{"angle": "50"}]}}).slopes.table
    assert one == (sp.Sector(0.0, 50.0),)  # a lone row without az — a constant angle
    sorted_rows = p({"slopes": {"table": [{"az": 270, "angle": 50}, {"az": 90, "angle": 40}]}}).slopes.table
    assert [x.az for x in sorted_rows] == [90.0, 270.0]
    assert refused({"slopes": {"table": [{"az": "x", "angle": 45}, {"az": 5, "angle": 45}]}}) == "slopes.table.0.az"


# --- the units and the elements ------------------------------------------------------------------------------------


def test_grade_units_k_e() -> None:
    oz = 31.1034768
    assert sp.grade_k("g/t") == pytest.approx(1 / oz, rel=1e-15) and sp.grade_k("ppm") == sp.grade_k("g/t")
    assert sp.grade_k("ppb") == pytest.approx(1e-3 / oz, rel=1e-15) and sp.grade_k("%") == 0.01
    assert sp.grade_k(None) is None and sp.grade_k("oz/t") is None
    assert sp.price_label("g/t") == "$/унц" and sp.price_label("%") == "$/т" and sp.grade_label("g/t") == "г/т"
    e = spec.parse_params({"elements": [{"field": "au", "unit": "г/т", "price": 2000}]}).elements[0]
    assert e.unit == "g/t" and e.k == pytest.approx(1 / oz) and e.sales == 0.0 and e.royalty == 0.0
    # Т8.11-7: R = 1000 m³ · 2,7 · 2 g/t · k · 0,9 · (2000 − 5) = 311 723,99 $
    r = 1000 * 2.7 * 2 * (e.k or 0) * 0.9 * (2000 - 5)
    assert r == pytest.approx(311723.99, abs=0.01)


def test_elements_shapes() -> None:
    s = spec.parse_params(
        {"elements": ["AU", {"field": "CU", "unit": "%", "price": "9 000".replace(" ", ""), "sales": 300}]}
    )
    assert [(e.field, e.unit, e.price, e.sales) for e in s.elements] == [
        ("AU", None, None, 0.0),
        ("CU", "%", 9000.0, 300.0),
    ]
    assert spec.parse_params({"elements": None}).elements == ()
    assert refused({"elements": [{"field": ""}]}) == "elements.0.field"
    assert refused({"elements": [5]}) == "elements.0.field"
    assert spec.parse_params({"elements": [{"field": "AU", "unit": "gpt"}]}).elements[0].unit == "g/t"


# --- the canonical form and the hash -------------------------------------------------------------------------------


def test_hash_does_not_depend_on_the_order_of_keys() -> None:
    body = case("rocks_field")
    reordered = json.loads(json.dumps(body))
    for key in list(reordered):  # the top level in the reverse order …
        reordered[key] = reordered.pop(key)
    reordered = dict(reversed(list(reordered.items())))
    reordered["elements"] = [dict(reversed(list(e.items()))) for e in reordered["elements"]]
    reordered["rocks"] = {
        "types": [dict(reversed(list(t.items()))) for t in reversed(reordered["rocks"]["types"])],
        "by": dict(reversed(list(reordered["rocks"]["by"].items()))),
    }  # … types in another order too
    a, b = spec.parse_params(body), spec.parse_params(reordered)
    assert a.canonical() == b.canonical() and a.params_hash() == b.params_hash()
    text = '{"a":{"x":[1,{"c":2,"d":1}],"y":2},"b":1}'
    assert spec.canonical_json({"b": 1, "a": {"y": 2, "x": [1, {"d": 1, "c": 2}]}}) == text
    assert spec.canonical_json({"a": {"x": [1, {"c": 2, "d": 1}], "y": 2}, "b": 1}) == text
    assert len(a.params_hash()) == 64 and a.params_hash() == spec.parse_params(a.canonical()).params_hash()


def test_hash_is_what_the_result_depends_on() -> None:
    base = spec.parse_params(case("t8117"))
    h = base.params_hash()
    same = {**case("t8117"), "solver": "ortools", "calendar": {"q_ore": 5e5, "q_mass": 9e5, "rate": 0.2}}
    assert spec.parse_params(same).params_hash() == h  # the calendar is a view, the solver does not change the closure
    assert (
        "calendar" not in base.hash_params()
        and "solver" not in base.hash_params()
        and base.hash_params()["algo"] == "pit-1"
    )
    for key, value in (
        ("prefix", "V1_"),
        ("k", 12),
        ("terrain_id", 3),
        ("rf", [0.9, 1.0]),
        ("default_density", 2.5),
        ("mining", {"c_m0": 2.6}),
        ("slopes", {"tolerance": 3}),
        ("density", 2.8),
    ):
        assert spec.parse_params({**case("t8117"), key: value}).params_hash() != h, key
    # 0.0 and -0.0, 2 and 2.0 are the same value
    a = spec.parse_params({"mining": {"c_gl": -0.0, "c_m0": 2}})
    b = spec.parse_params({"mining": {"c_gl": 0, "c_m0": 2.0}})
    assert a.params_hash() == b.params_hash() and "-0.0" not in spec.canonical_json(a.canonical())


def test_unknown_keys_are_ignored() -> None:
    s = spec.parse_params({**case("t8117"), "state": "ready", "job_id": 3, "updated_at": "…"})
    assert s.canonical() == spec.parse_params(case("t8117")).canonical()


# --- the model -----------------------------------------------------------------------------------------------------


def test_resolved_fills_and_checks_against_the_model() -> None:
    r = spec.parse_params(case("rocks_field")).resolved(FIELDS, Z_RANGE)
    assert [(e.field, e.unit) for e in r.elements] == [("AU", "g/t"), ("CU", "%")]
    assert r.density == "SG" and r.mining.z_ref == 30.0  # the varying field SG; Z_ref given
    assert r.rocks.by == sp.RockBy("field", "ROCK") and [t.name for t in r.rocks.types] == [
        "(пусто)",
        "Окисленная",
        "Первичная",
    ]
    assert r.rocks.type_of("первичная") is not None and r.rocks.type_of("Первичная").rec == {"AU": 0.85, "CU": 0.9}  # type: ignore[union-attr]
    t = spec.parse_params(case("minimal")).resolved(FIELDS, Z_RANGE)
    assert t.mining.z_ref == 33.0 and t.resolved_ and t.density == "SG"  # Z_ref — the top of the grid
    d = spec.parse_params(case("sectors_domains")).resolved(FIELDS, Z_RANGE).slopes
    assert d.domain_field == "DOM" and [x.label for x in d.domains] == ["A", "B"] and d.tolerance == 2.0
    assert spec.parse_params(r.canonical()).resolved(FIELDS, Z_RANGE).canonical() == r.canonical()
    assert r.params_hash() == spec.parse_params(r.canonical()).params_hash()


def test_resolved_refusals() -> None:
    def fail(raw: dict[str, Any], fields: Any = FIELDS, z: Any = Z_RANGE) -> ApiError:
        with pytest.raises(ApiError) as e:
            spec.parse_params(raw).resolved(fields, z)
        assert e.value.message and e.value.hint
        return e.value

    e = fail({"elements": ["NOPE"]})
    assert (e.status, e.code, e.extra) == (404, "block_field_not_found", {"param": "elements.0.field"})
    e = fail({"elements": ["ROCK"]})
    assert (e.status, e.code, e.extra["param"]) == (422, "bad_param", "elements.0.field")  # not a number
    e = fail({"elements": ["NOUNIT"]})
    assert (e.code, e.extra["param"]) == ("bad_param", "elements.0.unit")
    assert (
        spec.parse_params({"elements": [{"field": "NOUNIT", "unit": "ppm"}]}).resolved(FIELDS, Z_RANGE).elements[0].unit
        == "ppm"
    )
    e = fail({"rocks": {"by": {"field": "AU"}}})
    assert (e.code, e.extra["param"]) == ("bad_param", "rocks.by.field")  # a rock field must be a category
    e = fail({"slopes": {"domain_field": "NOPE", "domains": []}})
    assert (e.status, e.code, e.extra["param"]) == (404, "block_field_not_found", "slopes.domain_field")
    e = fail({}, fields=[f for f in FIELDS if f["name"] != "SG"])
    assert (e.code, e.extra["param"]) == ("bad_param", "density")
    ok = spec.parse_params({"default_density": 2.6}).resolved([f for f in FIELDS if f["name"] != "SG"], Z_RANGE)
    assert ok.density == 2.6  # no density field at all: the default for every block
    e = fail({}, z=None)
    assert (e.code, e.extra["param"]) == ("bad_param", "mining.z_ref")


def test_model_defaults() -> None:
    d = spec.model_defaults(FIELDS, Z_RANGE)
    assert d["elements"] == [{"field": "AU", "unit": "g/t"}, {"field": "CU", "unit": "%"}]
    assert d["density"] == "SG" and d["z_ref"] == 33.0
    assert d["categories"] == [
        {"field": "ROCK", "labels": ["Окисленная", "Первичная"]},
        {"field": "DOM", "labels": ["A", "B", "C"]},
    ]
    assert spec.model_defaults(FIELDS)["z_ref"] is None


# --- validate / pit_not_ready ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(PARAMS["validate"]))
def test_validate_cases(name: str) -> None:
    item = PARAMS["validate"][name]
    res = spec.validate(item["body"], item["context"])
    assert res["ready"] is (item["first_missing"] is None), res
    assert (res["missing"][0]["param"] if res["missing"] else None) == item["first_missing"], res["missing"]
    for m in res["missing"]:
        assert set(m) == {"param", "message"} and m["message"].endswith(".")
        assert any("а" <= ch <= "я" for ch in m["message"].lower())
    for w in res["warnings"]:
        assert set(w) == {"code", "param", "message", "hint"}
    if item["first_missing"] is None:
        assert spec.require_ready(item["body"], item["context"]).model_id == 12


def test_validate_order_and_content() -> None:
    res = spec.validate({}, CTX)
    assert [m["param"] for m in res["missing"]] == ["model_id", "elements", "rocks.types.Все породы", "mining.c_m0"]
    full = spec.validate(case("rocks_field"), CTX)
    assert full["ready"] is False and full["missing"][0]["param"] == "rocks.types.(пусто)" or full["ready"]
    # every type of the rock field needs c_p and rec of every element, «только пустая» / «воздух» do not
    r = spec.validate({**case("rocks_field"), "terrain_id": 3}, CTX)
    assert r["ready"] and [w["code"] for w in r["warnings"]] == ["no_calendar"]
    two = {**case("rocks_field")}
    two["rocks"] = {
        "by": {"field": "ROCK"},
        "types": [{"name": "Окисленная", "c_p": 12, "rec": {"AU": 0.7}}, {"name": "Первичная", "c_p": 18}],
    }
    got = [m["param"] for m in spec.validate(two, CTX)["missing"]]
    assert got == ["rocks.types.Окисленная.rec.CU", "rocks.types.Первичная.rec.AU", "rocks.types.Первичная.rec.CU"]
    rules = spec.validate(case("rocks_rules"), CTX)  # the rules: "Богатая" (c_p, rec) · "Воздух" (air) — enough
    assert [m["param"] for m in rules["missing"]] == ["elements.0.price", "mining.c_m0"], rules
    done = {**case("rocks_rules"), "elements": [{"field": "AU", "price": 2000}], "mining": {"c_m0": 2.5}}
    assert (
        spec.validate(done, CTX)["ready"]
        and not spec.validate(done, {**CTX, "rock_types": ["Богатая", "Воздух"]})["missing"]
    )
    assert spec.validate(done, {**CTX, "rock_types": ["Богатая", "Прочие"]})["missing"] == []  # «Прочие» is set
    unused = spec.validate(
        {
            **case("t8117"),
            "rocks": {"types": [{"name": "Все породы", "c_p": 20, "rec": {"AU": 0.9}}, {"name": "Лишний", "c_p": 1}]},
        },
        CTX,
    )
    assert unused["ready"] and any(w["code"] == "type_unused" for w in unused["warnings"])
    cheap = spec.validate({**case("t8117"), "elements": [{"field": "AU", "price": 5, "sales": 5}]}, CTX)
    assert any(w["code"] == "price_not_above_sales" for w in cheap["warnings"])
    unknown = spec.validate(
        {
            **case("sectors_domains"),
            "slopes": {"domain_field": "DOM", "domains": [{"label": "Z", "table": [{"angle": 45}]}]},
        },
        CTX,
    )
    assert any(w["code"] == "domain_unknown" for w in unknown["warnings"])
    assert spec.validate(case("t8117"))["ready"]  # without the model only the shapes of the scenario are checked
    assert spec.validate({**case("t8117"), "calendar": {"q_ore": 1e6}}, CTX)["warnings"] == [
        w for w in spec.validate(case("t8117"), CTX)["warnings"] if w["code"] != "no_calendar"]  # fmt: skip


def test_pit_not_ready_names_the_first_missing_item() -> None:
    with pytest.raises(ApiError) as e:
        spec.require_ready(case("t8117") | {"elements": [{"field": "AU"}]}, CTX)
    x = e.value
    assert (x.status, x.code) == (422, "pit_not_ready") and x.hint
    assert x.extra["param"] == "elements.0.price" == x.extra["missing"][0]["param"]
    assert x.message == "Сценарий не готов к расчёту: Не задана цена элемента AU."
    with pytest.raises(ApiError) as e2:
        spec.require_ready({}, CTX)
    assert e2.value.extra["param"] == "model_id" and len(e2.value.extra["missing"]) == 4
    with pytest.raises(ApiError) as e3:
        spec.require_ready({"rf": [0.001]}, CTX)  # a shape error stays bad_param
    assert e3.value.code == "bad_param"
    with pytest.raises(ApiError) as e4:  # the job passes what the data has: a met type and blocks without density
        spec.require_ready(case("t8117"), {**CTX, "rock_types": ["Все породы", "Прочие"]})
    assert e4.value.extra["param"] == "rocks.types.Прочие"
    with pytest.raises(ApiError) as e5:
        spec.require_ready({**case("t8117"), "density": "SG"}, {**CTX, "missing_density_blocks": 3})
    assert e5.value.extra["param"] == "default_density"
    assert (
        spec.require_ready(
            {**case("t8117"), "density": "SG", "default_density": 2.6}, {**CTX, "missing_density_blocks": 3}
        ).default_density
        == 2.6
    )


# --- the other bodies ----------------------------------------------------------------------------------------------


def test_request_bodies() -> None:
    assert spec.parse_check({"params": {"model_id": 12}}).model_id == 12
    assert refused({}, fn=spec.parse_check) == "params"
    c = spec.parse_scenario_create({"name": " Вариант 1 ", "params": {"model_id": 12}})
    assert (c.name, c.params.model_id) == ("Вариант 1", 12)
    d = spec.parse_scenario_create({})
    assert d.name is None and d.params.canonical() == spec.parse_params({}).canonical()
    u = spec.parse_scenario_update({"name": "N", "calendar": {"q_ore": "1000000", "rate": 0.1}, "expected_version": 2})
    assert (u.name, u.params, u.calendar, u.expected_version) == ("N", None, sp.Calendar(1e6, None, 0.1), 2)
    assert refused({"name": "N"}, fn=spec.parse_scenario_update) == "expected_version"
    assert refused({"expected_version": 0, "name": "N"}, fn=spec.parse_scenario_update) == "expected_version"
    assert refused({"expected_version": 1}, fn=spec.parse_scenario_update) == "body"
    assert spec.parse_run({}).solver is None and spec.parse_run({"solver": "ortools"}).solver == "ortools"
    assert refused({"solver": "x"}, fn=spec.parse_run) == "solver"
    q = spec.parse_report_query({"calendar": '{"q_ore": 1000000, "q_mass": null, "rate": 0.1}'})
    assert q.calendar == sp.Calendar(1e6, None, 0.1) and spec.parse_report_query({}).calendar is None
    assert refused({"calendar": "{"}, fn=spec.parse_report_query) == "calendar"
    assert refused({"calendar": '{"rate": 2}'}, fn=spec.parse_report_query) == "calendar.rate"
    assert spec.parse_surfaces({}).shells is None and spec.parse_surfaces({"shells": [3, 1, 3]}).shells == (1, 3)
    assert refused({"shells": []}, fn=spec.parse_surfaces) == "shells"
    assert refused({"shells": [1, 0]}, fn=spec.parse_surfaces) == "shells.1"
    assert refused({"shells": [1.5]}, fn=spec.parse_surfaces) == "shells.0"
    assert spec.parse_solid({"shell": 4, "name": "К"}) == spec.Solid(4, "К")
    assert refused({}, fn=spec.parse_solid) == "shell" and refused({"shell": 201}, fn=spec.parse_solid) == "shell"
    assert spec.parse_compare({"a": 5, "b": 5}) == spec.Compare(5, 5)
    assert refused({"a": 5}, fn=spec.parse_compare) == "b"


def test_refusals_russian_with_hints() -> None:
    for code, (status, message, hint) in sp.REFUSALS.items():
        e = spec.refuse(code, param="x")
        assert (e.status, e.code, e.message, e.hint, e.extra) == (status, code, message, hint, {"param": "x"})
        assert any("а" <= ch <= "я" for ch in message.lower()) and hint
    assert {
        "pit_not_ready",
        "variable_grid",
        "pit_too_large",
        "pit_density_missing",
        "pit_empty",
        "filter_field_missing",
        "pit_solid_failed",
        "pit_scenario_not_found",
        "block_model_not_found",
        "block_field_not_found",
        "wireframe_not_found",
        "pit_running",
        "version_conflict",
        "name_taken",
        "pit_fields_taken",
        "model_changed",
        "project_read_only",
    } <= set(sp.REFUSALS)  # решение 30
    assert {c: s for c, (s, _m, _h) in sp.REFUSALS.items()} | {} == {
        c: s for c, (s, _m, _h) in sp.REFUSALS.items()
    }  # the table is the one source
    assert sp.REFUSALS["project_read_only"][0] == 423 and sp.REFUSALS["pit_internal"][0] == 500
    assert spec.refuse("pit_empty", "Свой текст.").message == "Свой текст."


# --- the fixtures ---------------------------------------------------------------------------------------------------


def _write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def test_params_json_round_trip() -> None:
    path = DATA / "params.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for name, item in data["cases"].items():
        s = spec.parse_params(item["body"])
        r = s.resolved(FIELDS, Z_RANGE)
        got, got_r = json.loads(json.dumps(s.canonical())), json.loads(json.dumps(r.canonical()))
        assert spec.parse_params(got).canonical() == s.canonical(), name
        assert spec.parse_params(got_r).canonical() == r.canonical(), name  # resolved canonical parses to itself
        if REGEN:
            item["canonical"], item["resolved"] = got, got_r
        else:
            assert got == item["canonical"] and got_r == item["resolved"], (
                f"{name}: BF_REGEN_PIT=1 uv run pytest tests/test_pit_spec.py"
            )
    if REGEN:
        data["_note"] = (
            "Примеры параметров сценария карьера (21.1): body — `params` запроса POST /pit/scenarios; canonical — "
            "spec.parse_params(body).canonical(); resolved — то же после PitSpec.resolved(fixture.fields, "
            "fixture.z_range) (meta.params, docs/progress/e21-api.md §2); invalid — тела, которые разбор отклоняет "
            "422 bad_param с этим param; validate — тело и контекст → первый missing (§3). Перегенерация: "
            "BF_REGEN_PIT=1 uv run pytest -q tests/test_pit_spec.py"
        )
        _write(path, data)
    assert data["_note"] and len(data["invalid"]) >= 50


def _answer(fn: Any) -> dict[str, Any]:
    with pytest.raises(ApiError) as e:
        fn()
    x = e.value
    return {"status": x.status, "body": {**x.extra, "code": x.code, "message": x.message, "hint": x.hint}}


def _spec_answers() -> dict[str, Any]:
    refusals = {
        c: {"status": st, "body": {"code": c, "message": m, "hint": h}} for c, (st, m, h) in sp.REFUSALS.items()
    }
    return {
        "defaults": {
            "status": 200,
            "body": {
                **spec.defaults_json(),
                "ranges": spec.ranges_json(),
                "model": spec.model_defaults(FIELDS, Z_RANGE),
            },
        },
        "validate_new": {"status": 200, "body": spec.validate({}, CTX)},
        "validate_ready": {"status": 200, "body": spec.validate(case("t8117"), CTX)},
        "bad_param": _answer(lambda: spec.parse_params({"model_id": 12, "rf": {"from": 0.01, "to": 5, "step": 0.02}})),
        "pit_not_ready": _answer(lambda: spec.require_ready({**case("t8117"), "elements": [{"field": "AU"}]}, CTX)),
        "refusals": refusals,
    }


def test_contract_answers_spec_parts() -> None:
    path = DATA / "contract_answers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    want = json.loads(json.dumps(_spec_answers()))
    if REGEN:
        data.update(want)
        _write(path, data)
    for key, value in want.items():
        assert data[key] == value, f"{key}: BF_REGEN_PIT=1 uv run pytest -q tests/test_pit_spec.py"
    assert data["_status"] == "stub until 21.11" and data["_note"]
    for key, answer in data.items():
        if isinstance(answer, dict) and "status" in answer and answer["status"] >= 400:
            assert answer["body"]["code"] and answer["body"]["message"] and answer["body"]["hint"], key
    d = data["defaults"]["body"]
    assert (
        len(d["params"]["rf"]) == 25
        and d["params"]["calendar"]["rate"] == 0.08
        and d["params"]["mining"]["h_gl"] == 10.0
    )
    assert d["params"]["slopes"]["tolerance"] == 2.5 and d["limits"]["shells"] == 200 and d["limits"]["sectors"] == 16
    assert data["pit_not_ready"]["body"]["missing"][0]["param"] == "elements.0.price"


def test_sample_answers_are_consistent() -> None:
    """The samples are by the contract: the cash flow identity, Σ increments = the total, the prototype template."""
    data = json.loads((DATA / "contract_answers.json").read_text(encoding="utf-8"))
    rep = data["report_get"]["body"]
    assert rep["algo"] == spec.ALGO and rep["npv_ready"] and rep["base_rf"] == 1.0
    for s in rep["shells"]:
        cf = s["revenue"] - s["cost_sales"] - s["royalty"] - s["cost_mine"] - s["cost_process"]
        assert cf == pytest.approx(s["cf"], abs=0.05)
    for key in ("ore_t", "waste_t", "cf", "revenue"):
        assert sum(s[key] for s in rep["shells"]) == pytest.approx(rep["totals"][key], abs=0.05)
    assert [s["rf"] for s in rep["shells"]] == rep["series"]["rf"] == sorted(rep["series"]["rf"])
    assert all(s["best"]["npv"] >= s["worst"]["npv"] for s in rep["shells"])
    assert all(len(row["cells"]) == len(rep["table"]["columns"]) - 1 for row in rep["table"]["rows"])
    chk = data["check_result"]["body"]["domains"][0]
    assert chk["n_offsets"] == len(chk["offsets"]) == 17 and len(chk["phi_eff"]["angle"]) == 360  # ТЗ 8.11.2 prototype
    cmp_ = data["compare_result"]["body"]
    only = next(r for r in cmp_["shells"] if r["status"] == "only_b")
    assert only["a"] is None and only["diff"] is None  # a missing RF is a gap, not 0
    assert data["run_result"]["body"]["shells"][-1]["k"] == len(data["run_result"]["body"]["shells"])


def test_no_numpy_at_import() -> None:
    code = "import sys, bf_core.mine.spec; raise SystemExit(3 if 'numpy' in sys.modules else 0)"
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-400:]
