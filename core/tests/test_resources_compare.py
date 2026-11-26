# ruff: noqa: F811 - the fixture `ra` is imported, and the tests take it by its name
"""«Сравнить отчёты» — `resources.compare` (подшаг 20.10; ТЗ 07-35 Д-37; ADR 0027 решение 26, № 109):
**ТД37-1** (A = B → every difference is 0: the same report, two reports of the same parameters), **ТД37-2** (the set of
№ 109: three blocks of 50 m³, D1 — Au 2 and 2 g/t at ρ = 2,5, D2 — Au 4 g/t at ρ = 3 against 2,5 → ΔT = 25 t, ḡ 2,75
and 2,6667, Δḡ = 2,75 − 2,6667, ΔM — to 1e−12), «сетки различаются» (another model), a row that only one report has — a
gap and the mark `only_in`, not 0, the warnings (the keys, the cut-off field, the density, «устарел»), the two curves,
the head (the model, the inputs with their fingerprints, the date), 404 / 409 / 422 in Russian."""

from __future__ import annotations

from typing import Any

import numpy as np

from bf_core.db import dialect
from bf_core.resources import compare as rc
from bf_core.resources import store as st
from tests.estapi_util import read_only, refused
from tests.resapi_world import RS, model, ra, run  # noqa: F401 - the fixtures
from tests.test_blocks_fields_store import add_fields
from tests.test_blocks_report import put

GRID = {"corner": [0.0, 0.0, 0.0], "counts": [3, 1, 1], "size": [5.0, 5.0, 2.0]}  # a block of 50 m³
FIELDS = [
    {"name": "DOM", "dtype": "cat", "labels": ["D1", "D2"], "role": "code"},
    {"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade"},
    {"name": "CU", "dtype": "f8", "unit": "%", "role": "grade"},
    {"name": "RHO", "dtype": "f8", "unit": "t/m3", "role": "density"},
]
P = np.arange(3, dtype=np.uint64)
VALUES = {"DOM": np.array([0, 0, 1], dtype=np.int32), "AU": np.array([2.0, 2.0, 4.0]), "CU": np.array([1.0, 3.0, 2.0]),
          "RHO": np.array([2.5, 2.5, 3.0])}  # fmt: skip
BASE = {"elements": ["AU"], "groups": ["DOM"], "curve": 0}


def td37(c: Any, name: str = "Модель № 109") -> int:
    return put(c, GRID, FIELDS, P, VALUES, name=name)


def saved(c: Any, mid: int, name: str, **over: Any) -> int:
    res = run(c, {"model_id": mid, **BASE, **over}, save=True, name=name)
    return int(res["report_id"])


def compare(c: Any, a: int, b: int) -> dict[str, Any]:
    r = c.post(f"{RS}/compare", json={"report_a": a, "report_b": b})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def find(out: dict[str, Any], g: Any = "total", kind: str = "all", cutoff: float | None = None) -> dict[str, Any]:
    got = [r for r in out["rows"] if r["kind"] == kind and r["cutoff"] == cutoff
           and ((g == "total" and r["total"]) or r["g"] == g)]  # fmt: skip
    assert len(got) == 1, (g, kind, cutoff, len(got))
    return got[0]  # type: ignore[no-any-return]


def close(a: float, b: float, rel: float = 1e-12) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b))


def test_td37_2_the_set_of_question_109(ra) -> None:
    mid = td37(ra)
    a = saved(ra, mid, "A: плотность по блокам", density="RHO")
    b = saved(ra, mid, "B: плотность 2,5", density=2.5)
    out = compare(ra, a, b)
    total = find(out)
    assert total["only_in"] is None and out["grids_differ"] is False
    assert (total["a"]["tonnes"], total["b"]["tonnes"]) == (400.0, 375.0)
    assert close(total["delta"]["tonnes"], 25.0) and total["delta"]["tonnes"] == 25.0  # ΔT = 25 t at the full share
    ga, gb = total["a"]["elements"]["AU"]["grade"], total["b"]["elements"]["AU"]["grade"]
    assert close(ga, 2.75) and close(gb, 8 / 3)  # 1100 / 400 and 1000 / 375
    assert close(total["delta"]["elements"]["AU"]["grade"], 2.75 - 8 / 3)
    assert close(total["delta"]["elements"]["AU"]["metal"], 100.0)  # ΔM: 1100 g against 1000 g
    assert total["delta"]["blocks"] == 0 and total["delta"]["volume"] == 0.0
    d2 = find(out, ["D2"])
    assert d2["delta"]["tonnes"] == 25.0 and d2["delta"]["elements"]["AU"]["grade"] == 0.0  # ḡ of D2 is 4 in both
    d1 = find(out, ["D1"])
    assert d1["delta"]["tonnes"] == 0.0 and d1["delta"]["elements"]["AU"]["metal"] == 0.0
    assert [w["code"] for w in out["warnings"]] == ["density_differs"]  # only the density differs


def test_td37_1_a_equals_b_is_zero_in_every_row(ra) -> None:
    mid = td37(ra)
    a = saved(ra, mid, "Отчёт", density="RHO", cutoffs=[None, 1.0, 3.0], curve=4)
    same = compare(ra, a, a)  # the same report
    twin = compare(ra, a, saved(ra, mid, "Близнец", density="RHO", cutoffs=[None, 1.0, 3.0], curve=4))  # same params
    for out in (same, twin):
        assert out["rows"] and all(r["only_in"] is None for r in out["rows"])
        for r in out["rows"]:
            d = r["delta"]
            assert d["blocks"] == 0 and d["volume"] == 0.0 and d["tonnes"] == 0.0, r
            assert all(v["grade"] in (0.0, None) and v["metal"] in (0.0, None) for v in d["elements"].values()), r
        assert out["grids_differ"] is False and out["warnings"] == []
        assert out["curves"]["a"] == out["curves"]["b"] and out["curves"]["a"] is not None
    assert same["a"]["id"] == same["b"]["id"] == a and twin["a"]["id"] != twin["b"]["id"]


def test_a_row_that_only_one_report_has_is_a_gap_not_zero(ra) -> None:
    mid = td37(ra)
    a = saved(ra, mid, "Борта 1 и 2", density="RHO", cutoffs=[1.0, 2.0])
    b = saved(ra, mid, "Борта 1 и 3", density="RHO", cutoffs=[1.0, 3.0])
    out = compare(ra, a, b)
    both = find(out, kind="cutoff", cutoff=1.0)
    assert both["only_in"] is None and both["delta"]["tonnes"] == 0.0
    only_a = find(out, kind="cutoff", cutoff=2.0)
    assert only_a["only_in"] == "a" and only_a["b"] is None and only_a["delta"] is None and only_a["a"]["tonnes"] > 0
    only_b = find(out, kind="cutoff", cutoff=3.0)
    assert only_b["only_in"] == "b" and only_b["a"] is None and only_b["delta"] is None
    assert out["rows"][-1]["only_in"] == "b"  # the rows of B only come after the rows of A


def test_the_grids_differ_when_the_models_differ(ra) -> None:
    a = saved(ra, td37(ra, "Модель A"), "Отчёт A", density="RHO")
    b = saved(ra, td37(ra, "Модель B"), "Отчёт B", density="RHO")
    out = compare(ra, a, b)
    assert out["grids_differ"] is True and "grids_differ" in [w["code"] for w in out["warnings"]]
    assert out["a"]["model"]["name"] == "Модель A" and out["b"]["model"]["name"] == "Модель B"
    assert find(out)["delta"]["tonnes"] == 0.0  # the rows still match: the numbers are compared, not the blocks


def test_the_warnings_of_the_keys_the_cutoff_field_and_the_stale_report(ra) -> None:
    mid = td37(ra)
    a = saved(ra, mid, "С доменами", density="RHO", cutoffs=[1.0])
    b = saved(ra, mid, "Без доменов, борт по CU", density="RHO", cutoffs=[1.0], groups=[], cutoff_field="CU",
              elements=["AU", "CU"])  # fmt: skip
    out = compare(ra, a, b)
    codes = [w["code"] for w in out["warnings"]]
    assert "keys_differ" in codes and "cutoff_field_differs" in codes
    assert out["elements"] == ["AU", "CU"]  # the elements of A, then those of B only
    total = find(out, kind="cutoff", cutoff=1.0)
    assert total["delta"]["elements"]["CU"] is None  # A has no CU: no difference, not 0
    groups = [r["only_in"] for r in out["rows"] if r["g"] in (["D1"], ["D2"])]
    assert groups and set(groups) == {"a"}  # B has no keys: the rows of the groups are in A only
    # a changed input makes both reports «устарел»
    add_fields(ra.project, ra.store, mid, [{"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade",
                                           "replace": True}], {"AU": VALUES["AU"] + 1.0})  # fmt: skip
    again = compare(ra, a, a)
    assert again["a"]["stale"] is True and "stale" in [w["code"] for w in again["warnings"]]


def test_the_two_curves_and_the_head(ra) -> None:
    mid = td37(ra)
    a = saved(ra, mid, "Кривая A", density="RHO", curve=5)
    b = saved(ra, mid, "Кривая B", density=2.5)  # no curve
    out = compare(ra, a, b)
    assert out["curves"]["a"] is not None and out["curves"]["b"] is None
    assert len(out["curves"]["a"]["cutoffs"]) == 5 and out["curves"]["a"]["field"] == "AU"
    head = out["a"]
    assert head["id"] == a and head["name"] == "Кривая A" and head["stale"] is False and head["created_at"]
    assert head["model"]["id"] == mid and head["model"]["cells_sha"]
    kinds = {i["kind"] for i in head["inputs"]}
    assert {"object:block_model", "bm_field"} <= kinds
    assert all(i["fp"] and i["name"] for i in head["inputs"])  # the name and the fingerprint of every input
    assert out["keys"] == [{"kind": "field", "field": "DOM"}]


def test_the_function_answers_the_same_as_the_route_and_does_not_write(ra) -> None:
    mid = td37(ra)
    a = saved(ra, mid, "Функция A", density="RHO")
    b = saved(ra, mid, "Функция B", density=2.5)
    with dialect.read_tx(ra.project.engine) as conn:
        direct = rc.compare_reports(conn, ra.store, a, b)
    import json

    assert json.dumps(direct, sort_keys=True, default=str) == json.dumps(compare(ra, a, b), sort_keys=True, default=str)
    back = read_only(ra)  # nothing is written: a project «только просмотр» answers too
    try:
        assert compare(ra, a, b)["rows"]
    finally:
        back()


def test_the_refusals_are_in_russian_with_a_hint(ra) -> None:
    mid = td37(ra)
    a = saved(ra, mid, "Есть", density="RHO")
    refused(ra.post(f"{RS}/compare", json={"report_a": a, "report_b": 99999}), 404, "resource_report_not_found")
    refused(ra.post(f"{RS}/compare", json={"report_a": 99999, "report_b": a}), 404, "resource_report_not_found")
    refused(ra.post(f"{RS}/compare", json={"report_a": a}), 422, "bad_param", "report_b")
    refused(ra.post(f"{RS}/compare", json={"report_a": 0, "report_b": a}), 422, "bad_param", "report_a")
    with dialect.write_tx(ra.project.engine) as conn:
        st.mark_building(conn, a)
    refused(ra.post(f"{RS}/compare", json={"report_a": a, "report_b": a}), 409, "report_building")
    with dialect.write_tx(ra.project.engine) as conn:
        st.settle(conn, a)
    assert compare(ra, a, a)["rows"]


def test_the_model_of_t8_10_1_a_report_against_itself(ra) -> None:
    mid = model(ra)  # 8 blocks, CLASS: the report of the contract
    a = saved(ra, mid, "Т8.10-1", density=2.5, groups=["CLASS"], cutoffs=[1.0], elements=["AU"])
    out = compare(ra, a, a)
    total = find(out, kind="cutoff", cutoff=1.0)
    assert total["a"]["tonnes"] == 12_500.0 and total["delta"]["tonnes"] == 0.0
