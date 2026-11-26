"""Parameters of a resource report (подшаг 20.1; ТЗ 8.10; 7.35 Д-36, Д-37; ADR 0027 решения 3–11, 14, 17, 22, 30): the
defaults, the range of cut-offs, ≤ 3 keys of a group, every 422 branch (`bad_param {param}`, `group_numeric`), the
bench, the units, the other bodies, the round trip of `tests/data/resources/params.json`, the parts of
`contract_answers.json` that `spec.py` writes (`defaults`, `bad_param`, `group_numeric`, `refusals`), no numpy.
`BF_REGEN_RESOURCES=1` writes the canonical forms and those parts anew (the rest of the file is samples until 20.7)."""

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
from bf_core.resources import spec
from bf_core.resources import spec_parts as sp

DATA = Path(__file__).parent / "data" / "resources"
REGEN = os.environ.get("BF_REGEN_RESOURCES") == "1"
FIX = json.loads((DATA / "params.json").read_text(encoding="utf-8"))["fixture"]
FIELDS: list[dict[str, Any]] = FIX["fields"]
Z_RANGE = tuple(FIX["z_range"])


def refused(b: Any, code: str = "bad_param", status: int = 422, fn: Any = spec.parse_params) -> str:
    with pytest.raises(ApiError) as e:
        fn(b)
    assert e.value.status == status and e.value.code == code, (e.value.code, e.value.message)
    assert e.value.message and e.value.hint  # Russian text with a hint
    return str(e.value.extra.get("param"))


def resolved(over: dict[str, Any] | None = None, fields: Any = None, z: Any = Z_RANGE) -> spec.ReportSpec:
    return spec.parse_params({"model_id": 12, **(over or {})}).resolved(fields or FIELDS, z)


def test_defaults() -> None:
    s = spec.parse_params({"model_id": 12})
    assert s.elements == () and s.cutoff_field is None and s.cutoffs == (None,) and s.balance is None
    assert s.density is None and s.filter_id is None and s.restrict is None and s.keys == () and s.bench is None
    assert s.curve == 100 and s.template_id is None
    assert spec.parse_params({"model_id": 12, "cutoffs": None, "curve": ""}).curve == 100
    r = s.resolved(FIELDS, Z_RANGE)
    assert [(e.field, e.unit) for e in r.elements] == [("AU", "g/t"), ("CU", "%")]  # the fields of the role grade
    assert r.cutoff_field == "AU" and r.density == "SG"  # the varying SG; the constant DENSITY is skipped
    assert r.group_count() == 1 and r.group_labels(0) == []


def test_defaults_prefer_cut_fields_and_varying_density() -> None:
    f = [*FIELDS, {"name": "AU_CUT", "kind": "number", "unit": "г/т", "role": None}]
    assert spec.default_elements(f) == ["AU_CUT"]
    assert spec.default_density(f) == "SG"
    only_const = [x for x in FIELDS if x["name"] != "SG"]
    assert spec.default_density(only_const) is None  # a constant field is not taken by default
    with pytest.raises(ApiError) as e:
        resolved(fields=only_const)
    assert e.value.code == "bad_param" and e.value.extra["param"] == "density"
    assert resolved({"density": "2,7"}, only_const).density == 2.7
    with pytest.raises(ApiError) as e:
        resolved(fields=[x for x in FIELDS if x["role"] != "grade"])
    assert e.value.extra["param"] == "elements"


def test_model_id_required() -> None:
    assert refused({}) == "model_id"
    assert refused({"model_id": 0}) == "model_id"
    assert refused({"model_id": True}) == "model_id"
    assert refused([1]) == "params"


def test_cutoffs_list_and_range() -> None:
    assert spec.parse_params({"model_id": 1, "cutoffs": [0.5, "1,0", 0.5, None, None]}).cutoffs == (0.5, 1.0, None)
    assert spec.parse_params({"model_id": 1, "cutoffs": [0]}).cutoffs == (0.0,)  # 0 is a cut-off, not «без борта»
    r = spec.parse_params({"model_id": 1, "cutoffs": {"from": 0.1, "to": 0.5, "step": 0.1}}).cutoffs
    assert r == (0.1, 0.2, 0.3, 0.4, 0.5)  # the noise of the sum is removed (0.1 + 2·0.1 = 0.30000000000000004)
    assert spec.parse_params({"model_id": 1, "cutoffs": {"from": "0,5", "to": 0.5, "step": 1}}).cutoffs == (0.5,)
    assert len(spec.parse_params({"model_id": 1, "cutoffs": {"from": 0, "to": 199, "step": 1}}).cutoffs) == 200
    same_list = spec.parse_params({"model_id": 1, "cutoffs": [0.1, 0.2, 0.3, 0.4, 0.5]})
    same_range = spec.parse_params({"model_id": 1, "cutoffs": {"from": 0.1, "to": 0.5, "step": 0.1}})
    assert same_list.canonical() == same_range.canonical() and same_list.params_hash() == same_range.params_hash()


@pytest.mark.parametrize(
    ("cutoffs", "param"),
    [
        ([], "cutoffs"), ("0,5", "cutoffs"), (list(range(201)), "cutoffs"), ([0.5, "x"], "cutoffs.1"),
        ([True], "cutoffs.0"), ([1e13], "cutoffs.0"), ([math.nan], "cutoffs.0"),
        ({"from": 0, "to": 200, "step": 1}, "cutoffs.step"), ({"from": 0, "to": 10, "step": 0}, "cutoffs.step"),
        ({"from": 0, "to": 10, "step": -1}, "cutoffs.step"), ({"from": 5, "to": 1, "step": 1}, "cutoffs.to"),
        ({"from": 0, "to": 1}, "cutoffs.step"), ({"to": 1, "step": 1}, "cutoffs.from"),
        ({"from": 0, "to": "x", "step": 1}, "cutoffs.to"),
    ],
)  # fmt: skip
def test_cutoffs_refused(cutoffs: Any, param: str) -> None:
    assert refused({"model_id": 1, "cutoffs": cutoffs}) == param


def test_balance() -> None:
    b = spec.parse_params({"model_id": 1, "balance": {"cutoff": "1,0", "off_cutoff": 0.5}}).balance
    assert b == spec.Balance(1.0, 0.5)
    assert spec.parse_params({"model_id": 1, "balance": {"cutoff": 1}}).balance == spec.Balance(1.0, None)
    assert refused({"model_id": 1, "balance": {"off_cutoff": 0.5}}) == "balance.cutoff"
    assert refused({"model_id": 1, "balance": {"cutoff": 1, "off_cutoff": 1}}) == "balance.off_cutoff"
    assert refused({"model_id": 1, "balance": {"cutoff": 1, "off_cutoff": 2}}) == "balance.off_cutoff"
    assert refused({"model_id": 1, "balance": [1]}) == "balance"


def test_up_to_three_group_keys() -> None:
    keys = [{"kind": "bench"}, "CLASS", {"kind": "field", "field": "DOM"}]
    r = resolved({"groups": keys, "bench": {"h": 10}})
    assert [(k.kind, k.field) for k in r.keys] == [("bench", None), ("field", "CLASS"), ("field", "DOM")]
    assert r.radices() == (7, 4, 4) and r.group_count() == 112  # 6 benches + «(пусто)»
    assert refused({"model_id": 1, "groups": [*keys, "AU"]}) == "groups"  # a fourth key
    assert refused({"model_id": 1, "groups": "CLASS"}) == "groups"
    assert refused({"model_id": 1, "groups": ["CLASS", "class"]}) == "groups.1"  # a repeat (case aside)
    assert refused({"model_id": 1, "groups": [{"kind": "bench"}, {"kind": "bench"}]}) == "groups.1"
    assert refused({"model_id": 1, "groups": [{"kind": "wires"}]}) == "groups.0"
    assert refused({"model_id": 1, "groups": [5]}) == "groups.0"
    assert refused({"model_id": 1, "groups": [{"kind": "field"}]}) == "groups.0.field"


def test_numeric_group_key_is_group_numeric() -> None:
    with pytest.raises(ApiError) as e:
        resolved({"groups": ["CLASS", "AU"]})
    x = e.value
    assert (x.status, x.code, x.extra) == (422, "group_numeric", {"param": "groups.1"})
    assert "AU" in x.message and x.hint  # Russian text with a hint (№ 112: translate into categories)
    with pytest.raises(ApiError) as e:
        resolved({"groups": ["NOPE"]})
    assert (e.value.status, e.value.code, e.value.extra) == (404, "block_field_not_found", {"param": "groups.0"})
    with pytest.raises(ApiError) as e:
        resolved({"groups": ["CLASS", "class"]})  # the same field in another spelling is a repeat even before the model
    assert e.value.code == "bad_param"


def test_group_codes_are_mixed_radix_with_empty_zero() -> None:
    r = resolved({"groups": ["CLASS", "DOM"]})
    assert r.radices() == (4, 4) and r.group_count() == 16
    assert r.group_labels(0) == ["(пусто)", "(пусто)"]
    assert r.group_labels(1) == ["(пусто)", "A"] and r.group_labels(4) == ["Измеренные", "(пусто)"]
    assert r.group_labels(4 * 3 + 2) == ["Предполагаемые", "B"]
    assert [r.decode(c) for c in (0, 5, 15)] == [(0, 0), (1, 1), (3, 3)]
    assert spec.EMPTY_LABEL == "(пусто)"


def test_too_many_combinations() -> None:
    big = [{"name": f"C{i}", "kind": "category", "unit": None, "role": None, "labels": [f"v{n}" for n in range(60)]}
           for i in range(3)]
    spec.parse_params({"model_id": 1, "groups": ["C0", "C1"]}).resolved([*FIELDS, *big], Z_RANGE)  # 61² = 3721
    with pytest.raises(ApiError) as e:
        spec.parse_params({"model_id": 1, "groups": ["C0", "C1", "C2"]}).resolved([*FIELDS, *big], Z_RANGE)  # 226 981
    assert (e.value.status, e.value.code) == (422, "report_too_big") and e.value.hint


def test_bench_defaults_and_labels() -> None:
    r = resolved({"groups": [{"kind": "bench"}]})
    assert r.bench == spec.Bench(10.0, -20.0, -12.0, 33.0, 0, 6)  # Z0 — a multiple of H under the bottom
    assert r.keys[0].labels == ("-20…-10", "-10…0", "0…+10", "+10…+20", "+20…+30", "+30…+40")
    assert r.bench is not None and r.bench.label(2) == "0…+10"
    b = resolved({"groups": [{"kind": "bench"}], "bench": {"h": "12,5", "z0": -25}}).bench
    assert b == spec.Bench(12.5, -25.0, -12.0, 33.0, 1, 4)  # benches j = 1…4 touch [-12; 33]
    assert b is not None and b.labels() == ("-12,5…0", "0…+12,5", "+12,5…+25", "+25…+37,5")
    assert spec.bench_label(250, 260) == "+250…+260" and spec.bench_label(-0.0, 5) == "0…+5"
    z = resolved({"groups": [{"kind": "bench"}], "bench": {"h": 10, "z_min": 0, "z_max": 20}}).bench
    assert z == spec.Bench(10.0, 0.0, 0.0, 20.0, 0, 2)  # the end of the range on a boundary adds no bench
    assert resolved({"bench": {"h": 3}}).bench is None  # `bench` without the key «Уступ» is dropped
    on_edge = spec.resolve_bench(spec.Bench(5.0), (0.0, 10.0))
    assert (on_edge.z0, on_edge.j0, on_edge.n) == (0.0, 0, 2)  # Т8.10-4: a block Z 0…10, the boundary at 5


@pytest.mark.parametrize(
    ("bench", "param"),
    [
        ({"h": 0}, "bench.h"), ({"h": -1}, "bench.h"), ({"h": 1001}, "bench.h"), ({"h": "x"}, "bench.h"),
        ({"z0": 1e8}, "bench.z0"), ({"z_min": "x"}, "bench.z_min"), ({"z_min": 10, "z_max": 10}, "bench.z_max"),
        ({"z_min": 20, "z_max": 10}, "bench.z_max"), ([1], "bench"),
    ],
)  # fmt: skip
def test_bench_refused(bench: Any, param: str) -> None:
    assert refused({"model_id": 1, "groups": [{"kind": "bench"}], "bench": bench}) == param


def test_bench_over_a_thousand() -> None:
    with pytest.raises(ApiError) as e:
        resolved({"groups": [{"kind": "bench"}], "bench": {"h": 0.01}})  # 4 500 benches of 0,01 m
    assert (e.value.code, e.value.extra) == ("bad_param", {"param": "bench.h"})
    with pytest.raises(ApiError) as e:
        resolved({"groups": [{"kind": "bench"}]}, z=None)
    assert e.value.extra["param"] == "bench"


def test_elements_and_units() -> None:
    s = spec.parse_params({"model_id": 1, "elements": ["AU", {"field": "CU", "unit": "%"}, {"field": "ZN"}]})
    assert [(e.field, e.unit) for e in s.elements] == [("AU", None), ("CU", "%"), ("ZN", None)]
    r = resolved({"elements": [{"field": "cu", "unit": " ppm "}, "AU"]})
    assert [(e.field, e.unit) for e in r.elements] == [("CU", "ppm"), ("AU", "g/t")]  # the unit of the field is taken
    assert r.cutoff_field == "CU"  # the first element
    assert resolved({"elements": ["AU"], "cutoff_field": "cu"}).cutoff_field == "CU"  # g_б outside the elements
    for text, canon in [("g/t", "g/t"), ("г/т", "g/t"), ("GPT", "g/t"), (" ppm ", "ppm"), ("%", "%"), ("ppb", "ppb"),
                        ("oz/t", "oz/t"), ("", None), (None, None)]:  # fmt: skip
        assert spec.norm_unit(text, "u") == canon
    assert (spec.metal_family("g/t"), spec.metal_family("ppm"), spec.metal_family("ppb"), spec.metal_family("%")) == (
        "g", "g", "g", "t")  # fmt: skip
    assert (spec.metal_k("g/t"), spec.metal_k("ppm"), spec.metal_k("ppb"), spec.metal_k("%")) == (1.0, 1.0, 1e-3, 0.01)
    assert spec.metal_k("oz/t") is None and spec.metal_family(None) is None  # no unit — no metal (never 0)
    assert spec.unit_label("g/t") == "г/т" and spec.unit_label("oz/t") == "oz/t"
    assert math.isclose(31_250 / spec.TROY_OUNCE_G / 1000, 1.004711, rel_tol=1e-6)  # Т8.10-1: 31,25 кг


@pytest.mark.parametrize(
    ("over", "param"),
    [
        ({"elements": []}, "elements"), ({"elements": "AU"}, "elements"), ({"elements": [f"E{i}" for i in range(21)]},
                                                                         "elements"),
        ({"elements": ["AU", "au"]}, "elements.1"), ({"elements": [{"unit": "g/t"}]}, "elements.0.field"),
        ({"elements": [{"field": "AU", "unit": 5}]}, "elements.0.unit"),
        ({"elements": [{"field": "AU", "unit": "x" * 21}]}, "elements.0.unit"),
        ({"elements": [3]}, "elements.0"), ({"cutoff_field": 3}, "cutoff_field"),
        ({"density": 0}, "density"), ({"density": -2.5}, "density"), ({"density": math.inf}, "density"),
        ({"density": True}, "density"), ({"curve": 1}, "curve"), ({"curve": 501}, "curve"), ({"curve": -1}, "curve"),
        ({"curve": 2.5}, "curve"), ({"curve": True}, "curve"), ({"filter_id": 0}, "filter_id"),
        ({"filter_id": "x"}, "filter_id"), ({"restrict": [1]}, "restrict"), ({"template_id": "a b"}, "template_id"),
        ({"template_id": 5}, "template_id"), ({"template_id": "x" * 65}, "template_id"),
    ],
)  # fmt: skip
def test_shapes_refused_with_param(over: dict[str, Any], param: str) -> None:
    assert refused({"model_id": 1, **over}) == param


def test_fields_checked_against_the_model() -> None:
    for over, code, status, param in [
        ({"elements": ["ZN"]}, "block_field_not_found", 404, "elements.0.field"),
        ({"cutoff_field": "ZN"}, "block_field_not_found", 404, "cutoff_field"),
        ({"density": "ZN"}, "block_field_not_found", 404, "density"),
        ({"elements": ["CLASS"]}, "bad_param", 422, "elements.0.field"),  # a category is not a grade
        ({"cutoff_field": "DOM"}, "bad_param", 422, "cutoff_field"),
        ({"density": "CLASS"}, "bad_param", 422, "density"),
    ]:  # fmt: skip
        with pytest.raises(ApiError) as e:
            resolved(over)
        assert (e.value.status, e.value.code, e.value.extra["param"]) == (status, code, param), over
        assert e.value.message and e.value.hint


def test_density_forms() -> None:
    assert spec.parse_params({"model_id": 1, "density": 2.7}).density == 2.7
    assert spec.parse_params({"model_id": 1, "density": "2,7"}).density == 2.7
    assert spec.parse_params({"model_id": 1, "density": " SG "}).density == "SG"
    assert resolved({"density": "sg"}).density == "SG"


def test_restrictor_is_a_normalized_rule_tree() -> None:
    tree = {"op": "and", "items": [{"cond": {"field": "DOM", "op": "=", "value": "A"}}, {"expr": "AU > 0.1"}]}
    s = spec.parse_params({"model_id": 1, "filter_id": 5, "restrict": tree})
    assert s.filter_id == 5 and s.restrict is not None and s.restrict["op"] == "and"
    assert spec.parse_params({"model_id": 1, "restrict": s.restrict}).restrict == s.restrict  # canonical is stable
    with pytest.raises(ApiError) as e:
        spec.parse_params({"model_id": 1, "restrict": {"op": "xor", "items": []}})
    assert e.value.status == 422 and e.value.message and e.value.hint


def test_canonical_parses_to_itself_and_hash_ignores_the_template() -> None:
    for name, case in json.loads((DATA / "params.json").read_text(encoding="utf-8"))["cases"].items():
        for s in (spec.parse_params(case["body"]), spec.parse_params(case["body"]).resolved(FIELDS, Z_RANGE)):
            got = json.loads(json.dumps(s.canonical()))
            assert spec.parse_params(got).canonical() == s.canonical(), name
    a = resolved({"template_id": "jorc"})
    b = resolved({"template_id": "gkz"})
    assert a.canonical() != b.canonical() and a.params_hash() == b.params_hash()  # a template is a view (№ 17)
    assert "template_id" not in a.hash_params() and a.hash_params()["algo"] == spec.ALGO == "resources-1"
    assert resolved({"curve": 50}).params_hash() != a.params_hash()
    assert resolved({"cutoffs": [1]}).params_hash() != a.params_hash()
    assert len(a.params_hash()) == 64
    meta = {**a.canonical(), "v": 1, "algo": spec.ALGO, "totals": {}, "files": []}  # the stored meta parses as it is
    assert spec.parse_params(meta).canonical() == a.canonical()


def test_request() -> None:
    r = spec.parse_request({"params": {"model_id": 12}})
    assert r.save is False and r.name is None and r.params.model_id == 12  # a preview by default
    r = spec.parse_request({"params": {"model_id": 12}, "name": " Ресурсы Q4 ", "save": True})
    assert (r.name, r.save) == ("Ресурсы Q4", True)
    assert refused({}, fn=spec.parse_request) == "params"
    assert refused({"params": {"model_id": 12}, "save": "да"}, fn=spec.parse_request) == "save"
    assert refused({"params": {"model_id": 12}, "name": "x" * 201}, fn=spec.parse_request) == "name"
    assert refused({"params": {}}, fn=spec.parse_request) == "model_id"


def test_other_bodies() -> None:
    assert spec.parse_rename({"name": " Новое ", "expected_version": 2}) == ("Новое", 2)
    assert refused({"name": "x"}, fn=spec.parse_rename) == "expected_version"
    assert refused({"name": "", "expected_version": 1}, fn=spec.parse_rename) == "name"
    q = spec.parse_query({"template": "jorc", "cutoff": "1,0", "page": "2"})
    assert (q.template, q.cutoff, q.page, q.size) == ("jorc", 1.0, 2, 500)
    assert spec.parse_query({}) == spec.Query(None, None, 1, 500)
    assert refused({"size": 5001}, fn=spec.parse_query) == "size"
    d = spec.parse_drill({"report_id": 17, "group": ["Измеренные"], "cutoff": 1.0})
    assert (d.report_id, d.group, d.cutoff, d.kind) == (17, ("Измеренные",), 1.0, "cutoff")
    assert spec.parse_drill({"report_id": 17, "group": [], "cutoff": None}).kind == "all"
    assert spec.parse_drill({"report_id": 17, "group": [], "cutoff": None}).group is None  # the total
    b = spec.parse_drill({"report_id": 17, "group": ["A", "(пусто)"], "cutoff": None, "kind": "balance"})
    assert b.kind == "balance" and b.group == ("A", "(пусто)")
    assert refused({"group": []}, fn=spec.parse_drill) == "report_id"
    assert refused({"report_id": 1, "kind": "x"}, fn=spec.parse_drill) == "kind"
    assert refused({"report_id": 1, "kind": "cutoff"}, fn=spec.parse_drill) == "cutoff"
    assert refused({"report_id": 1, "kind": "all", "cutoff": 1}, fn=spec.parse_drill) == "cutoff"
    assert refused({"report_id": 1, "group": ["a", "b", "c", "d"]}, fn=spec.parse_drill) == "group"
    r = spec.parse_drill_rows({"report_id": 17, "group": None, "cutoff": 1.0})
    assert (r.page, r.size, r.csv) == (1, 1000, False)
    assert refused({"report_id": 1, "size": 1001}, fn=spec.parse_drill_rows) == "size"
    assert refused({"report_id": 1, "csv": 1}, fn=spec.parse_drill_rows) == "csv"
    x = spec.parse_export({"format": "xlsx", "path": "C:\\Отчёты\\r.xlsx", "template": "gkz", "cutoff": 1})
    assert (x.format, x.template, x.cutoff, x.overwrite) == ("xlsx", "gkz", 1.0, False)
    assert refused({"format": "pdf", "path": "x"}, fn=spec.parse_export) == "format"  # PDF — Э22
    assert refused({"format": "csv"}, fn=spec.parse_export) == "path"
    assert spec.parse_compare_reports({"report_a": 17, "report_b": 17}) == (17, 17)  # A = B is allowed (ТД37-1)
    assert refused({"report_a": 1}, fn=spec.parse_compare_reports) == "report_b"
    c = spec.parse_field_compare(12, {"field_a": "AU_OK_EST", "field_b": "AU_ID_EST", "domain": "DOM"})
    assert (c.model_id, c.field_a, c.field_b, c.domain, c.out) == (12, "AU_OK_EST", "AU_ID_EST", "DOM", None)
    assert refused({"field_a": "A"}, fn=lambda b: spec.parse_field_compare(1, b)) == "field_b"


def test_refusals_russian_with_hints() -> None:
    for code, (status, message, hint) in sp.REFUSALS.items():
        e = spec.refuse(code, param="x")
        assert (e.status, e.code, e.message, e.hint, e.extra) == (status, code, message, hint, {"param": "x"})
        assert any("а" <= ch <= "я" for ch in message.lower() + hint.lower())
    assert {"report_too_big", "filter_field_missing", "group_numeric", "resource_report_not_found",
            "block_field_not_found", "filter_not_found", "template_not_found", "report_building", "version_conflict",
            "files_exist", "project_read_only"} == set(sp.REFUSALS)  # решение 30
    assert spec.refuse("group_numeric", "Свой текст.").message == "Свой текст."
    assert {code: st for code, (st, _m, _h) in sp.REFUSALS.items()}["project_read_only"] == 423


def _write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def test_params_json_round_trip() -> None:
    path = DATA / "params.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for name, case in data["cases"].items():
        s = spec.parse_params(case["body"])
        r = s.resolved(FIELDS, Z_RANGE)
        got, got_r = json.loads(json.dumps(s.canonical())), json.loads(json.dumps(r.canonical()))
        assert spec.parse_params(got_r).canonical() == r.canonical(), name  # resolved canonical parses to itself
        if REGEN:
            case["canonical"], case["resolved"] = got, got_r
        else:
            assert got == case["canonical"] and got_r == case["resolved"], name
    if REGEN:
        data["_note"] = ("Примеры параметров отчёта по ресурсам (20.1): body — `params` запроса "
                         "POST /resources/reports; canonical — spec.parse_params(body).canonical(); "
                         "resolved — то же после "
                         "ReportSpec.resolved(fixture.fields, fixture.z_range) (meta.params, docs/progress/e20-api.md "
                         "§2). Перегенерация: BF_REGEN_RESOURCES=1 uv run pytest -q tests/test_resources_spec.py")
        _write(path, data)
    assert data["_note"]


def _bad_param_answer() -> dict[str, Any]:
    with pytest.raises(ApiError) as e:
        spec.parse_params({"model_id": 12, "cutoffs": {"from": 0, "to": 10, "step": 0.01}})
    x = e.value
    return {"status": x.status, "body": {**x.extra, "code": x.code, "message": x.message, "hint": x.hint}}


def _group_numeric_answer() -> dict[str, Any]:
    with pytest.raises(ApiError) as e:
        resolved({"groups": ["CLASS", "AU"]})
    x = e.value
    return {"status": x.status, "body": {**x.extra, "code": x.code, "message": x.message, "hint": x.hint}}


def _spec_answers() -> dict[str, Any]:
    refusals = {code: {"status": st, "body": {"code": code, "message": m, "hint": h}}
                for code, (st, m, h) in sp.REFUSALS.items()}  # fmt: skip
    return {"defaults": {"status": 200, "body": {"params": spec.defaults_json(), "ranges": spec.ranges_json()}},
            "bad_param": _bad_param_answer(), "group_numeric": _group_numeric_answer(), "refusals": refusals}


def test_contract_answers_spec_parts() -> None:
    path = DATA / "contract_answers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    want = json.loads(json.dumps(_spec_answers()))
    if REGEN:
        data.update(want)
        _write(path, data)
    for key, value in want.items():
        assert data[key] == value, f"{key}: BF_REGEN_RESOURCES=1 uv run pytest -q tests/test_resources_spec.py"
    assert "ОБРАЗЦЫ" in data["_note"]
    for key, answer in data.items():
        if key != "_note" and isinstance(answer, dict) and "status" in answer and answer["status"] >= 400:
            assert answer["body"]["code"] and answer["body"]["message"] and answer["body"]["hint"], key
    defaults = data["defaults"]["body"]["params"]
    assert defaults["curve"] == 100 and defaults["cutoffs"] == [None]


def test_sample_report_is_consistent() -> None:
    """The samples are by the contract: Т8.10-1 numbers, Σ groups = the total, the form is the rows."""
    data = json.loads((DATA / "contract_answers.json").read_text(encoding="utf-8"))
    res = data["report_result"]["body"]
    assert res["algo"] == spec.ALGO and res["rows_total"] == len(res["rows"]) and not res["truncated"]
    by_kind: dict[tuple[str, float | None], dict[str, list[dict[str, Any]]]] = {}
    for row in res["rows"]:
        part = by_kind.setdefault((row["kind"], row["cutoff"]), {"total": [], "groups": []})
        part["total" if row["total"] else "groups"].append(row)
    for (_kind, _c), part in by_kind.items():
        (total,) = part["total"]
        for key in ("blocks", "volume", "tonnes"):
            assert sum(r[key] for r in part["groups"]) == total[key]
        assert sum(r["elements"]["AU"]["sum"] for r in part["groups"]) == total["elements"]["AU"]["sum"]
    t = by_kind[("cutoff", 1.0)]["total"][0]  # Т8.10-1
    assert (t["tonnes"], t["elements"]["AU"]["grade"], t["elements"]["AU"]["metal"]) == (12500.0, 2.5, 31250.0)
    assert res["totals"]["tonnes"] == 12500.0
    tonnes = res["curve"]["tonnes"]
    assert all(a >= b for a, b in zip(tonnes, tonnes[1:], strict=False))  # T does not grow with the cut-off
    form = data["report_get"]["body"]["form"]
    total_cells = form["sections"][0]["rows"][-1]["cells"]
    assert total_cells["tonnes"]["value"] == 12.5 and total_cells["grade:AU"]["text"] == "2,50"
    assert math.isclose(total_cells["metal:AU"]["value"], 1.004711, rel_tol=1e-6)
    for code in ("template_get", "template_gkz"):
        assert data[code]["body"]["template"]["order"]


def test_no_numpy_at_import() -> None:
    code = "import sys, bf_core.resources.spec; raise SystemExit(3 if 'numpy' in sys.modules else 0)"
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-400:]
