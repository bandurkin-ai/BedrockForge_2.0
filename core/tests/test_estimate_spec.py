"""Parameters of the estimation (подшаг 18.1; ТЗ 8.8.2; 7.35 Д-27, Д-29, Д-30, Д-38; ADR 0024 §2–7): the defaults
of 8.8.2, every range → 422 `bad_param` {param}, the round trip of `tests/data/estimate/params.json` body → spec →
canonical dict (`BF_REGEN_ESTIMATE=1` writes the canonical forms anew), no numpy at import."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bf_core.api.errors import ApiError
from bf_core.geostat.estimate import spec
from bf_core.geostat.estimate import spec_parts as sp

DATA = Path(__file__).parent / "data" / "estimate"
SPH = {"type": "spherical", "c": 0.9, "ang1": 0.0, "ang2": 0.0, "ang3": 0.0, "a1": 30.0, "a2": 30.0, "a3": 30.0}
OK1 = {"type": "semivariogram", "c0": 0.1, "common_axes": True, "structures": [SPH]}
BASE: dict[str, Any] = {
    "model_id": 7,
    "samples": {"set_id": 3, "table": "12", "field": "AU"},
    "variogram": {"model_id": 21, "snapshot": OK1},
}


def body(**over: Any) -> dict[str, Any]:
    out = copy.deepcopy(BASE)
    out.update(over)
    return out


def refused(b: dict[str, Any], code: str = "bad_param") -> str:
    with pytest.raises(ApiError) as e:
        spec.parse_estimation(b)
    assert e.value.status == 422 and e.value.code == code, (e.value.code, e.value.message)
    assert e.value.message and e.value.hint  # Russian text with a hint
    return str(e.value.extra.get("param"))


def test_defaults_8_8_2() -> None:
    s = spec.parse_estimation(BASE)
    assert s.method.kind == "ok" and s.method.power == 2.0 and s.method.mean_mode == "declus"
    assert s.disc == (4, 4, 2) and not s.point and s.sub == "parent"
    assert [p.factor for p in s.search.passes] == [1.0, 2.0, 3.0]
    for p in s.search.passes:
        assert (p.n_min, p.n_max, p.n_hole, p.n_oct, p.k_oct, p.n_holes_min, p.hy) == (4, 24, 0, 0, 0, 0, None)
    # the radii — the ranges of the model (the structure with the largest a1)
    assert s.search.source == "model" and s.search.radii == (30.0, 30.0, 30.0) and s.search.angles == (0, 0, 0)
    assert s.outputs.prefix == "AU_OK_" and s.outputs.fields[0] == "STATUS"
    assert s.outputs.fields == ("STATUS", "EST", "KV", "NS", "NH", "AD", "PASS", "SLOPE", "KE")
    assert s.samples.weights is None and s.samples.soft is None and s.samples.topcut is None
    assert s.target.domain_field is None and s.target.filter_id is None and s.target.rules is None


def test_dtypes_answer_81_and_status() -> None:
    samples = {**BASE["samples"], "unit": "г/т"}
    s = spec.parse_estimation(body(outputs={"fields": list(sp.ALLOWED["ok"])}, samples=samples))
    cols = {c["key"]: c for c in s.columns()}
    want = {"STATUS": "i1", "EST": "f8", "KV": "f4", "NS": "i2", "NH": "i2", "NHY": "i2", "AD": "f4", "DMIN": "f4",
            "PASS": "i1", "SLOPE": "f4", "KE": "f4", "LAGR": "f4", "SNW": "f4", "NOCT": "i1"}  # fmt: skip
    assert {k: c["dtype"] for k, c in cols.items()} == want
    assert cols["EST"]["role"] == "grade" and cols["EST"]["unit"] == "г/т" and cols["AD"]["unit"] == "м"
    assert "1 — вне домена" in cols["STATUS"]["description"] and cols["EST"]["name"] == "AU_OK_EST"


def test_status_always_and_nhy_with_hy() -> None:
    s = spec.parse_estimation(body(outputs={"fields": ["EST"]}))
    assert s.outputs.fields == ("STATUS", "EST")
    hy = {"passes": [{"hy": {"t": 20, "q": 0.5}}]}
    s = spec.parse_estimation(body(search=hy))
    assert "NHY" in s.outputs.fields and s.search.passes[0].hy == sp.Hy(20.0, 0.5, "drop")
    assert spec.parse_estimation(body(search={"passes": [{"hy": {"q": 0.5}}]})).search.passes[0].hy is None


def test_method_defaults_by_kind() -> None:
    idw = spec.parse_estimation(body(method={"kind": "idw"}, variogram=None, search={"r1": 50}))
    assert idw.search.source == "manual" and idw.search.radii == (50.0, 50.0, 50.0)
    assert idw.outputs.fields == ("STATUS", "EST", "NS", "AD", "PASS") and idw.outputs.prefix == "AU_IDW_"
    ik = spec.parse_estimation(body(method={"kind": "ik", "ik": {"thresholds": [1, "2,5"], "models": "median"}}))
    assert ik.method.thresholds == (1.0, 2.5) and [c["name"] for c in ik.columns()] == [
        "AU_IK_STATUS", "AU_IK_NS", "AU_IK_PASS", "AU_IK_P1", "AU_IK_P2", "AU_IK_ETYPE"]  # fmt: skip
    point = spec.parse_estimation(body(disc=[1, 1, 1]))
    assert point.point


RANGE_CASES = [  # (patch of the body, param)
    ({"method": {"kind": "idw", "power": 0.4}}, "method.power"),
    ({"method": {"kind": "idw", "power": "5,1"}}, "method.power"),
    ({"disc": [0, 4, 2]}, "disc.0"),
    ({"disc": [4, 11, 2]}, "disc.1"),
    ({"disc": [4, 4, 2.5]}, "disc.2"),
    ({"disc": [4, 4]}, "disc"),
    ({"search": {"passes": [{"n_min": 0}]}}, "search.passes.0.n_min"),
    ({"search": {"passes": [{"n_max": 129}]}}, "search.passes.0.n_max"),
    ({"search": {"passes": [{}, {"n_min": 30, "n_max": 24}]}}, "search.passes.1.n_min"),
    ({"search": {"passes": [{"n_hole": 65}]}}, "search.passes.0.n_hole"),
    ({"search": {"passes": [{"n_oct": 17}]}}, "search.passes.0.n_oct"),
    ({"search": {"passes": [{"k_oct": 9}]}}, "search.passes.0.k_oct"),
    ({"search": {"passes": [{"n_holes_min": 129}]}}, "search.passes.0.n_holes_min"),
    ({"search": {"passes": [{"factor": 0}]}}, "search.passes.0.factor"),
    ({"search": {"passes": [{}] * 6}}, "search.passes"),
    ({"search": {"passes": []}}, "search.passes"),
    ({"search": {"passes": [{"hy": {"t": 10, "q": 0}}]}}, "search.passes.0.hy.q"),
    ({"search": {"passes": [{"hy": {"t": 10, "q": 1.1}}]}}, "search.passes.0.hy.q"),
    ({"search": {"passes": [{"hy": {"t": 10}}]}}, "search.passes.0.hy.q"),
    ({"search": {"passes": [{"hy": {"t": 10, "q": 1, "mode": "x"}}]}}, "search.passes.0.hy.mode"),
    ({"search": {"from": "manual", "r1": 0}}, "search.r1"),
    ({"search": {"from": "manual", "r1": 100_001}}, "search.r1"),
    ({"search": {"from": "manual"}}, "search.r1"),
    ({"search": {"from": "manual", "r1": 10, "r3": -1}}, "search.r3"),
    ({"search": {"from": "manual", "r1": 10, "ang2": 91}}, "search.ang2"),
    ({"search": {"from": "manual", "r1": 10, "ang1": 361}}, "search.ang1"),
    ({"search": {"from": "trend"}}, "trend_id"),
    ({"search": {"from": "ellipse"}}, "search.from"),
    ({"samples": {"set_id": 3, "table": "12", "field": "AU", "domain": {"field": "Z", "values": ["a"]},
                  "soft": {"domains": ["b"], "dist": -1}}}, "samples.soft.dist"),
    ({"samples": {"set_id": 3, "table": "12", "field": "AU", "soft": {"domains": ["b"], "dist": 5}}}, "samples.soft"),
    ({"samples": {"set_id": 3, "table": "12", "field": "AU", "domain": {"field": "Z", "values": ["a"]},
                  "soft": {"dist": 5}}}, "samples.soft.domains"),
    ({"samples": {"set_id": 3, "table": "12", "field": "AU", "domain": {"field": "Z", "values": []}}},
     "samples.domain.values"),
    ({"samples": {"set_id": 3, "table": "12", "field": "AU", "topcut": {"field": "A", "value": 3}}}, "samples.topcut"),
    ({"samples": {"set_id": 3, "table": "12", "field": "AU", "topcut": {"value": "много"}}}, "samples.topcut.value"),
    ({"samples": {"table": "12", "field": "AU"}}, "samples.set_id"),
    ({"samples": {"set_id": 3, "table": "12"}}, "samples.field"),
    ({"method": {"kind": "sk", "mean": {"mode": "value"}}}, "method.mean.value"),
    ({"method": {"kind": "kriging"}}, "method.kind"),
    ({"method": {"kind": "ik", "ik": {"thresholds": [2, 1]}}}, "method.ik.thresholds.1"),
    ({"method": {"kind": "ik", "ik": {"thresholds": list(range(21))}}}, "method.ik.thresholds"),
    ({"method": {"kind": "ik", "ik": {"models": "median"}}}, "method.ik.thresholds"),
    ({"method": {"kind": "ik", "ik": {"thresholds": [1, 2]}}}, "variograms"),
    ({"method": {"kind": "ik", "ik": {"thresholds": [1, 2]}}, "variograms": [{"model_id": 4}]}, "variograms"),
    ({"variograms": [{"model_id": 4}]}, "variograms"),
    ({"variogram": None}, "variogram.model_id"),
    ({"variogram": {"model_id": 0}}, "variogram.model_id"),
    ({"variogram": {"model_id": 4, "sha": "xyz"}}, "variogram.sha"),
    ({"variogram": {"model_id": 4, "snapshot": {"c0": -1}}}, "variogram.snapshot.c0"),
    ({"variogram": {"model_id": 4, "scale": "w"}}, "variogram.scale"),
    ({"outputs": {"prefix": "AU OK"}}, "outputs.prefix"),
    ({"outputs": {"prefix": "A" * 41}}, "outputs.prefix"),
    ({"outputs": {"fields": ["EST", "LAGR"]}, "method": {"kind": "sk"}}, "outputs.fields.1"),
    ({"outputs": {"fields": ["KV"]}, "method": {"kind": "idw"}, "variogram": None, "search": {"r1": 5}},
     "outputs.fields.0"),
    ({"outputs": {"fields": ["EST"]}, "method": {"kind": "ik", "ik": {"thresholds": [1], "models": "median"}}},
     "outputs.fields.0"),
    ({"sub": "child"}, "sub"),
    ({"model_id": None}, "model_id"),
    ({"trend_id": "x"}, "trend_id"),
    ({"name": "x" * 201}, "name"),
    ({"target": {"domain_field": "ZONE"}}, "target.label"),
    ({"target": {"label": "окисл"}}, "target.domain_field"),
    ({"target": {"filter_id": 3, "rules": {"expr": "AU > 1"}}}, "target.rules"),
]


@pytest.mark.parametrize(("patch", "param"), RANGE_CASES)
def test_ranges_are_422_bad_param(patch: dict[str, Any], param: str) -> None:
    assert refused(body(**patch)) == param


def test_bad_rules_of_the_restrictor() -> None:
    assert refused(body(target={"rules": {"op": "xor", "items": []}}), "filter_rules").startswith("target.rules")


def test_variogram_scale_d38() -> None:
    p = refused(body(variogram={"model_id": 21, "snapshot": OK1, "scale": "y"}), "variogram_scale")
    assert p == "variogram.model_id"
    with pytest.raises(ApiError) as e:
        sp.parse_vario({"model_id": 21, "scale": "y"}, "variogram")
    assert e.value.message == "Модель построена в нормальных оценках"
    assert sp.parse_vario({"model_id": 21, "scale": "y"}, "variogram", simulation=True).scale == "y"
    v = spec.parse_estimation(body(variogram={"model_id": 21, "scale": "back", "share": 0.9})).variogram
    assert v is not None and (v.scale, v.share) == ("back", 0.9)


def test_search_from_model_trend_manual() -> None:
    two = {"type": "semivariogram", "c0": 0.0, "common_axes": True, "structures": [
        {"type": "spherical", "c": 0.5, "ang1": 30.0, "ang2": -5.0, "ang3": 0.0, "a1": 50.0, "a2": 20.0, "a3": 5.0},
        {"type": "spherical", "c": 0.5, "a1": 120.0, "a2": 60.0, "a3": 10.0}]}  # fmt: skip
    s = spec.parse_estimation(body(variogram={"model_id": 2, "snapshot": two}))
    assert s.search.angles == (30.0, -5.0, 0.0) and s.search.radii == (120.0, 60.0, 10.0)  # common axes
    unresolved = spec.parse_estimation(body(variogram={"model_id": 2}))
    assert not unresolved.resolved_search and unresolved.canonical()["search"]["r1"] is None
    trend = {"ang1": 10.0, "ang2": 0.0, "ang3": 0.0, "ratios": [1.0, 0.5, 0.25], "size": 80.0}
    t = spec.parse_estimation(body(trend_id=5, search={"from": "trend"})).resolved(trend=trend)
    assert t.search.angles == (10.0, 0.0, 0.0) and t.search.radii == (80.0, 40.0, 20.0)
    t2 = spec.parse_estimation(body(trend_id=5, search={"from": "trend", "r1": 40})).resolved(trend=trend)
    assert t2.search.radii == (40.0, 20.0, 10.0)
    m = spec.parse_estimation(body(search={"from": "manual", "r1": "12,5", "ang1": 360, "ang3": -180}))
    assert m.search.radii == (12.5, 12.5, 12.5) and m.search.angles == (0.0, 0.0, 180.0)
    with pytest.raises(ApiError) as e:
        spec.parse_estimation(body(variogram={"model_id": 2, "snapshot": {"c0": 1.0, "structures": []}}))
    assert e.value.code == "no_structures"


def _canonical_of(b: dict[str, Any]) -> dict[str, Any]:
    s = spec.parse_estimation(b)
    return {"body": b, "canonical": s.canonical(), "columns": s.columns()}


def test_params_json_round_trip() -> None:
    path = DATA / "params.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data["cases"]) == {"idw", "ok_point", "sk", "ik", "ok_block_passes"}
    if os.environ.get("BF_REGEN_ESTIMATE") == "1":
        data["cases"] = {k: _canonical_of(c["body"]) for k, c in data["cases"].items()}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for name, case in data["cases"].items():
        s = spec.parse_estimation(case["body"])
        assert s.canonical() == case["canonical"], name
        assert s.columns() == case["columns"], name
        again = spec.parse_estimation(case["canonical"])  # canonical → spec → the same dict
        assert again == s and again.canonical() == case["canonical"], name
        meta = {"v": 1, **case["canonical"], "origin": [1.0, 2.0, 3.0], "inputs": {}, "algo": spec.ALGO,
                "last_run": None}  # fmt: skip
        assert spec.parse_estimation(meta) == s, name  # a stored meta parses as it is
        assert json.loads(json.dumps(case["canonical"])) == case["canonical"]
    assert data["cases"]["sk"]["canonical"]["method"]["mean"] == {"mode": "value", "value": 2.5}
    passes = data["cases"]["ok_block_passes"]["canonical"]["search"]["passes"]
    assert passes[0]["hy"] == {"t": 20.0, "q": 0.5, "mode": "cap"} and len(passes) == 3


def test_plan_run_at_inspect() -> None:
    plan = spec.parse_plan({"model_id": 7, "items": [4, 5]})
    assert plan.canonical() == {"name": None, "model_id": 7, "items": [4, 5]}
    with pytest.raises(ApiError) as e:
        spec.parse_plan({"model_id": 7, "items": [4, 4]})
    assert e.value.extra["param"] == "items.1"
    run = spec.parse_run({"plan_id": 9, "expected_version": 3})
    assert (run.plan_id, run.estimation_ids, run.expected_version, run.replace) == (9, (), 3, False)
    for bad_run, param in (({"plan_id": 9, "estimation_ids": [1], "expected_version": 1}, "plan_id"),
                           ({"plan_id": 9}, "expected_version"),
                           ({"estimation_ids": [1], "expected_version": 1, "replace": "да"}, "replace")):  # fmt: skip
        with pytest.raises(ApiError) as e:
            spec.parse_run(bad_run)
        assert (e.value.code, e.value.extra["param"]) == ("bad_param", param)
    assert spec.parse_at({"row": 12}) == {"row": 12}
    assert spec.parse_at({"xyz": ["1,5", 2, 3]}) == {"xyz": [1.5, 2.0, 3.0]}
    assert spec.parse_at({"ijk": [1, 2, 3]}) == {"ijk": [1, 2, 3]}
    for raw, param in (({"row": 1, "xyz": [0, 0, 0]}, "at"), ({"row": -1}, "at.row"), ({"ijk": [1, 2]}, "at.ijk")):
        with pytest.raises(ApiError) as e:
            spec.parse_at(raw)
        assert e.value.extra["param"] == param
    ins = spec.parse_inspect({"model_id": 7, "block": [1, 2, 3], "params_id": 4})
    assert (ins.model_id, ins.at, ins.params_id) == (7, {"ijk": [1, 2, 3]}, 4)
    assert spec.parse_inspect({"model_id": 7, "xyz": [1, 2, 3]}).params_id is None
    for raw, param in (({"model_id": 7}, "block"), ({"model_id": 7, "block": [1, -2, 3]}, "block.1")):
        with pytest.raises(ApiError) as e:
            spec.parse_inspect(raw)
        assert e.value.extra["param"] == param


def test_forms_of_the_window() -> None:
    d = spec.defaults_json()
    assert d["disc"] == [4, 4, 2] and d["method"]["power"] == 2.0 and len(d["search"]["passes"]) == 3
    assert d["search"]["order"] == ["ellipsoid", "hy", "sort_hs", "n_max_hole_octants", "n_min"]
    assert d["outputs"]["status"] == ["оценён", "вне домена", "мало проб", "вырожденная система"]
    r = spec.ranges_json()
    assert r["power"] == {"lo": 0.5, "hi": 5.0, "lo_open": False, "integer": False, "unit": ""}
    assert r["n_max"]["hi"] == 128 and r["k_oct"]["hi"] == 8 and r["topcut"]["lo"] is None
    json.dumps([d, r])


def test_contract_answers_are_consistent() -> None:
    answers = json.loads((DATA / "contract_answers.json").read_text(encoding="utf-8"))
    for name, a in answers.items():
        if name.startswith("_"):
            continue
        assert set(a) == {"status", "body"}, name
        if a["status"] >= 400:
            assert {"code", "message", "hint"} <= set(a["body"]), name
    meta = answers["estimation"]["body"]["meta"]
    s = spec.parse_estimation(meta)  # the meta of the sample is a real canonical form
    assert answers["estimation"]["body"]["columns"] == s.columns()
    assert answers["run_result"]["body"]["fields"] == [c["name"] for c in s.columns()]
    pv = answers["preview"]["body"]  # Э-ОК-1 (Т8.8-1): Σλ = 1
    assert abs(sum(c["lambda"] for c in pv["samples"]) - 1.0) < 1e-6
    reasons = {"selected", "hy_capped", "outside", "hole_limit", "octant_limit", "over_n_max", "other_domain",
               "soft_far", "hy_dropped", "merged"}  # fmt: skip
    assert {c["reason"] for c in pv["samples"]} <= reasons


def test_no_numpy_at_import() -> None:
    code = "import sys, bf_core.geostat.estimate.spec; sys.exit('numpy' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
