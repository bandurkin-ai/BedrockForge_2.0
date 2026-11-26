"""The routes of the pit optimisation (подшаг 21.11; ТЗ 8.11; ADR 0028 «API», решения 20, 28, 30; docs/progress/
e21-api.md §2–§13): the whole way through the routes (defaults → validate → check → scenario → run → report →
template → compare → rename → delete) with the jobs in their worker, the 404 / 409 / 422 / 423 of every route in
Russian (`pit_running`, `pit_not_ready`, `pit_fields_taken`, `variable_grid`, `version_conflict`, a project «только
просмотр»), the report under another calendar (a view: nothing is written), the comparison (A = A → Δ = 0), the stubs
of the surfaces and of the solid (501 until their jobs of 21.12 are registered), «Перестроить» through the graph, the
registration of the jobs, the events."""

# ruff: noqa: F811 - the fixture `pw` is imported, and the tests take it by its name
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.mine import store as st
from tests.pitjob import columns, field_of, sha_of, terrain
from tests.pitworld import (  # noqa: F401 - the fixture
    PIT,
    blocks,
    finish,
    get,
    make_model,
    new,
    ok,
    params,
    pw,
    read_only,
    refused,
    run,
)
from tests.test_import_job import API, wait_job

SMALL = (9, 8, 6)


def kinds_of(c: Any, name: str) -> list[Any]:
    return [d for n, d in c.events if n == name]


def job_of(c: Any, started: dict[str, Any]) -> dict[str, Any]:
    job = finish(c, started, timeout=300)
    assert job["status"] == "done", job
    return job["result"]  # type: ignore[no-any-return]


# --- the form and the readiness --------------------------------------------------------------------------------


def test_defaults_without_a_project(pclient) -> None:  # type: ignore[no-untyped-def]
    got = ok(pclient.get(f"{PIT}/defaults"))
    assert got["params"]["rf"][0] == 0.3 and len(got["params"]["rf"]) == 25 and "model" not in got
    assert got["limits"]["shells"] == 200 and got["choices"]["solvers"] == ["auto", "hpf", "ortools"]
    assert got["ranges"]["rf"]["lo"] == 0.01 and got["ranges"]["calendar.rate"]["hi"] == 0.5


def test_defaults_of_a_model_lists_the_terrains_the_categories_and_k(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL)
    dtm = terrain(pw, 40.0)
    got = ok(pw.get(f"{PIT}/defaults", params={"model_id": mid}))["model"]
    assert got["elements"] == [{"field": "AU", "unit": "g/t"}] and got["density"] == "SG"
    assert got["categories"] == [{"field": "ROCK", "labels": ["Руда", "Порода"]}] and got["z_ref"] == 60.0
    assert got["k_auto"] == 8 and got["terrains"] == [{"id": dtm, "name": "Рельеф"}]
    assert got["grid"]["counts"] == list(SMALL) and got["grid"]["size"] == [10.0, 10.0, 10.0]
    refused(pw.get(f"{PIT}/defaults", params={"model_id": 9999}), 404, "block_model_not_found")


def test_validate_names_what_is_missing_and_is_the_one_source_of_the_run(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL)
    new_ = ok(pw.post(f"{PIT}/validate", json={"params": {}}))
    assert new_["ready"] is False and new_["missing"][0] == {
        "param": "model_id",
        "message": "Не выбрана блочная модель.",
    }
    ready = ok(pw.post(f"{PIT}/validate", json={"params": params(mid)}))
    assert ready["ready"] is True and ready["missing"] == []
    assert [w["code"] for w in ready["warnings"]] == ["no_terrain"]  # the calendar is set
    no_price = params(mid, elements=[{"field": "AU", "unit": "g/t"}])
    got = ok(pw.post(f"{PIT}/validate", json={"params": no_price}))
    assert got["ready"] is False and got["missing"][0]["param"] == "elements.0.price"
    refused(
        pw.post(f"{PIT}/validate", json={"params": {"rf": {"from": 0.01, "to": 5, "step": 0.02}}}), 422, "bad_param"
    )
    refused(pw.post(f"{PIT}/validate", json={}), 422, "bad_param", "params")
    refused(pw.post(f"{PIT}/validate", json={"params": params(9999)}), 404, "block_model_not_found")


# --- the scenarios ----------------------------------------------------------------------------------------------


def test_the_scenario_through_the_routes_create_edit_rename_delete(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL)
    first = ok(pw.post(f"{PIT}/scenarios", json={}))
    assert first["version"] == 1 and first["name"] == "Сценарий карьера"
    second = new(pw, mid)  # a taken name gets a suffix
    assert second["name"] == "Сценарий карьера (2)"
    assert kinds_of(pw, "pit.changed")[-1] == {"kind": "pit_scenario", "id": second["id"]}
    sid = second["id"]
    got = get(pw, sid)
    assert got["state"] == "ready" and got["has_result"] is False and got["params_changed"] is False
    assert got["params"]["model_id"] == mid and got["params"]["mining"]["z_ref"] == 60.0  # resolved against the model
    assert got["params_hash"] and got["meta"]["model"]["name"] == "Рудное тело" and got["graph"]["stale"] is False
    listed = ok(pw.get(f"{PIT}/scenarios"))["scenarios"]
    assert [s["id"] for s in listed] == sorted([first["id"], sid], key=lambda i: {first["id"]: 0, sid: 1}[i])
    assert listed[1]["model"] == {"id": mid, "name": "Рудное тело"} and listed[0]["model"] == {"id": None, "name": None}
    # an edit: the version goes up; a calendar alone is a view
    put = ok(pw.put(f"{PIT}/scenarios/{sid}", json={"name": "Вариант 2", "expected_version": 1}))
    assert put == {"id": sid, "version": 2, "name": "Вариант 2", "params_changed": False}
    cal = ok(pw.put(f"{PIT}/scenarios/{sid}", json={"calendar": {"q_ore": 2e6, "rate": 0.1}, "expected_version": 2}))
    assert cal["version"] == 3 and get(pw, sid)["params"]["calendar"] == {"q_ore": 2e6, "q_mass": None, "rate": 0.1}
    refused(pw.put(f"{PIT}/scenarios/{sid}", json={"name": "x", "expected_version": 1}), 409, "version_conflict")
    assert refused(pw.put(f"{PIT}/scenarios/{sid}", json={"name": "x", "expected_version": 1}), 409,
                   "version_conflict")["version"] == 3  # fmt: skip
    refused(pw.put(f"{PIT}/scenarios/{sid}", json={"name": "сценарий карьера", "expected_version": 3}), 409,
            "name_taken", "name")  # fmt: skip
    refused(pw.put(f"{PIT}/scenarios/{sid}", json={"name": "x", "expected_version": 0}), 422, "bad_param",
            "expected_version")  # fmt: skip
    refused(pw.put(f"{PIT}/scenarios/{sid}", json={"expected_version": 3}), 422, "bad_param", "body")
    refused(pw.put(f"{PIT}/scenarios/{sid}", json={"params": {"rf": [0.0001]}, "expected_version": 3}), 422,
            "bad_param", "rf.0")  # fmt: skip
    refused(pw.put(f"{PIT}/scenarios/999", json={"name": "x", "expected_version": 1}), 404, "pit_scenario_not_found")
    refused(pw.put(f"{PIT}/scenarios/{sid}", json={"params": {"model_id": 9999}, "expected_version": 3}), 404,
            "block_model_not_found")  # fmt: skip
    refused(pw.get(f"{PIT}/scenarios/999"), 404, "pit_scenario_not_found")
    assert ok(pw.delete(f"{PIT}/scenarios/{sid}")) == {"id": sid, "deleted": True}
    assert {"kind": "pit_scenario", "id": sid, "deleted": True} in kinds_of(pw, "pit.changed")
    refused(pw.delete(f"{PIT}/scenarios/{sid}"), 404, "pit_scenario_not_found")
    refused(pw.post(f"{PIT}/scenarios", json={"params": {"model_id": 9999}}), 404, "block_model_not_found")
    refused(pw.post(f"{PIT}/scenarios", json={"params": {"slopes": {"table": [{"az": 0, "angle": 3}]}}}), 422,
            "bad_param", "slopes.table.0.angle")  # fmt: skip


def test_new_params_of_a_scenario_with_a_result_wait_for_a_run(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    sid = new(pw, mid)["id"]
    out = run(pw, sid)
    assert out["version"] == 2 and get(pw, sid)["has_result"] is True
    body = params(mid, elements=[{"field": "AU", "unit": "g/t", "price": 2200.0, "sales": 5.0, "royalty": 0.02}])
    put = ok(pw.put(f"{PIT}/scenarios/{sid}", json={"params": body, "expected_version": 2}))
    assert put["params_changed"] is True and get(pw, sid)["params_changed"] is True
    assert get(pw, sid)["params"]["elements"][0]["price"] == 2200.0  # the draft is what the window shows
    out2 = run(pw, sid)  # the run takes the draft
    assert out2["params"]["elements"][0]["price"] == 2200.0 and out2["params_hash"] != out["params_hash"]
    assert get(pw, sid)["params_changed"] is False and out2["version"] == 4
    same = ok(pw.put(f"{PIT}/scenarios/{sid}", json={"params": body, "expected_version": 4}))
    assert same["params_changed"] is False  # the same effective parameters: no run is needed


# --- the whole way: check, run, the report, the template, compare --------------------------------------------------


def test_the_way_through_the_routes_check_run_report_template_compare(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    started = pw.post(f"{PIT}/check", json={"params": {"model_id": mid}})
    assert started.status_code == 200 and sorted(started.json()) == ["id", "source"]
    chk = job_of(pw, started.json())
    assert chk["k"] == 8 and chk["domains"][0]["n_offsets"] == 17 and chk["model"]["id"] == mid
    a = new(pw, mid, "A")["id"]
    refused(pw.get(f"{PIT}/scenarios/{a}/report"), 409, "pit_no_result")
    refused(pw.get(f"{PIT}/scenarios/{a}/template"), 409, "pit_no_result")
    res = run(pw, a)
    assert res["name"] == "A" and res["version"] == 2 and res["solver"] == "hpf" and len(res["shells"]) == 4
    assert {"kind": "pit_scenario", "id": a} in kinds_of(pw, "pit.changed")
    assert {"kind": "block_model", "id": mid} in kinds_of(pw, "objects.changed")
    # the job is in «Задания» with its stages and the result
    sc = get(pw, a)
    assert sc["has_result"] and sc["state"] == "ready" and sc["job_id"] and sc["meta"]["totals"]["ore_t"] > 0
    tpl = ok(pw.get(f"{PIT}/scenarios/{a}/template"))["template"]
    assert tpl["k"] == 8 and tpl["domains"][0]["n_offsets"] == 17 and "model" not in tpl
    rep = ok(pw.get(f"{PIT}/scenarios/{a}/report"))
    assert rep["scenario_id"] == a and rep["base_rf"] == 1.0 and rep["npv_ready"] is True and rep["algo"] == "pit-1"
    assert [s["k"] for s in rep["shells"]] == [1, 2, 3, 4] and rep["series"]["rf"] == [0.4, 0.7, 1.0, 1.3]
    assert rep["elements"] == [{"field": "AU", "unit": "g/t", "unit_label": "г/т", "metal_unit": "унц",
                                "price_label": "$/унц"}]  # fmt: skip
    for s in rep["shells"]:
        assert s["best"]["npv"] is not None and s["worst"]["npv"] is not None and s["best"]["npv"] >= s["worst"]["npv"]
        cf = s["revenue"] - s["cost_sales"] - s["royalty"] - s["cost_mine"] - s["cost_process"]
        assert cf == pytest.approx(s["cf"], rel=1e-9, abs=1e-3)
    assert rep["totals"]["ore_t"] == pytest.approx(sum(s["ore_t"] for s in rep["shells"]), rel=1e-9)
    assert rep["shells"][-1]["cum"]["cf"] == pytest.approx(rep["totals"]["cf"], rel=1e-12)
    cols = [c["key"] for c in rep["table"]["columns"]]
    assert cols[:2] == ["k", "rf"] and "best_npv" in cols and "AU:grade" in cols
    for row, shell in zip(rep["table"]["rows"], rep["shells"], strict=True):
        assert len(row["cells"]) == len(cols) - 1 and row["k"] == shell["k"]
        assert row["cells"]["ore_t"]["value"] == shell["ore_t"] and "\u00a0" in row["cells"]["ore_t"]["text"]
        assert "," in row["cells"]["rf"]["text"] and row["cells"]["strip"]["text"]
    # the same result under another calendar: a view (nothing is written)
    sha, version = sha_of(pw, a), get(pw, a)["version"]
    cal = json.dumps({"q_ore": 100000.0, "q_mass": 300000.0, "rate": 0.0})
    other = ok(pw.get(f"{PIT}/scenarios/{a}/report", params={"calendar": cal}))
    assert other["calendar"] == {"q_ore": 100000.0, "q_mass": 300000.0, "rate": 0.0}
    assert other["shells"][-1]["best"]["npv"] == pytest.approx(rep["totals"]["cf"], rel=1e-9)  # r = 0: the sum of CF
    assert other["shells"][-1]["best"]["npv"] != rep["shells"][-1]["best"]["npv"]
    assert [s["cf"] for s in other["shells"]] == [s["cf"] for s in rep["shells"]]
    assert sha_of(pw, a) == sha and get(pw, a)["version"] == version
    nocal = ok(pw.get(f"{PIT}/scenarios/{a}/report", params={"calendar": json.dumps({"q_ore": None, "rate": 0.08})}))
    assert nocal["npv_ready"] is False and nocal["series"]["npv_best"] == [None] * 4  # a gap, not 0
    assert (
        nocal["warnings"][0]["code"] == "no_calendar" and nocal["table"]["rows"][0]["cells"]["best_npv"]["text"] == "—"
    )
    refused(pw.get(f"{PIT}/scenarios/{a}/report", params={"calendar": "{"}), 422, "bad_param", "calendar")
    refused(pw.get(f"{PIT}/scenarios/{a}/report", params={"calendar": json.dumps({"q_ore": -1})}), 422, "bad_param")
    refused(pw.get(f"{PIT}/scenarios/999/report"), 404, "pit_scenario_not_found")
    # the comparison
    b = new(pw, mid, "B", prefix="B_", elements=[{"field": "AU", "unit": "g/t", "price": 2400.0, "sales": 5.0,
                                                  "royalty": 0.02}])["id"]  # fmt: skip
    run(pw, b)
    same = ok(pw.post(f"{PIT}/compare", json={"a": a, "b": a}))
    assert same["same_grid"] is True and same["params_diff"] == []
    assert all(
        s["status"] == "both" and all(v == 0 for v in s["diff"].values() if v is not None) for s in same["shells"]
    )
    diff = ok(pw.post(f"{PIT}/compare", json={"a": a, "b": b}))
    assert {d["param"] for d in diff["params_diff"]} >= {"elements.0.price", "prefix"}
    assert diff["a"]["id"] == a and diff["b"]["id"] == b and diff["shells"][0]["a"]["blocks"] is not None
    refused(pw.post(f"{PIT}/compare", json={"a": a, "b": 999}), 404, "pit_scenario_not_found")
    refused(pw.post(f"{PIT}/compare", json={"a": a}), 422, "bad_param", "b")
    c = new(pw, mid, "C")["id"]
    refused(pw.post(f"{PIT}/compare", json={"a": a, "b": c}), 409, "pit_no_result")
    # the fields of the model: the first scenario has PIT and ORE, the second — B_PIT and B_ORE
    assert field_of(pw, mid, "PIT")["source"]["scenario_id"] == a and field_of(pw, mid, "B_PIT")
    assert int(np.asarray(columns(pw, mid, "PIT")["PIT"]).max()) == 4


def test_the_surfaces_and_the_solid_are_checked_and_wait_for_21_12(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    sid = new(pw, mid)["id"]
    refused(pw.post(f"{PIT}/scenarios/{sid}/surfaces", json={}), 409, "pit_no_result")
    refused(pw.post(f"{PIT}/scenarios/{sid}/solid", json={"shell": 1}), 409, "pit_no_result")
    run(pw, sid)
    refused(pw.post(f"{PIT}/scenarios/{sid}/surfaces", json={"shells": [1, 9]}), 422, "bad_param", "shells.1")
    refused(pw.post(f"{PIT}/scenarios/{sid}/surfaces", json={"shells": []}), 422, "bad_param", "shells")
    refused(pw.post(f"{PIT}/scenarios/{sid}/solid", json={"shell": 9}), 422, "bad_param", "shell")
    refused(pw.post(f"{PIT}/scenarios/{sid}/solid", json={}), 422, "bad_param", "shell")
    refused(pw.post(f"{PIT}/scenarios/999/solid", json={"shell": 1}), 404, "pit_scenario_not_found")
    from bf_core.jobs.registry import KINDS

    for kind in ("pit.surfaces", "pit.solid"):
        if kind not in KINDS:  # the jobs are 21.12's: the routes answer 501 in Russian until they are registered
            path = "surfaces" if kind == "pit.surfaces" else "solid"
            body = {} if path == "surfaces" else {"shell": 1}
            r = pw.post(f"{PIT}/scenarios/{sid}/{path}", json=body)
            refused(r, 501, "not_implemented")


def test_the_job_kinds_are_registered_with_their_queues() -> None:
    from bf_core.jobs.registry import HANDLER_MODULES, KINDS, load_handlers

    assert "bf_core.mine.job" in HANDLER_MODULES
    load_handlers()
    assert KINDS["pit.run"].queue == "heavy" and KINDS["pit.check"].queue == "quick"
    assert KINDS["pit.run"].max_attempts == 1 and KINDS["pit.run"].estimate_memory({}) >= 1000
    assert KINDS["pit.run"].title and KINDS["pit.check"].title


# --- the refusals before the job -----------------------------------------------------------------------------------


def test_the_run_is_refused_before_the_job_with_the_reason(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    start = f"{PIT}/scenarios"
    bare = ok(pw.post(start, json={}))["id"]  # no model, nothing set
    r = refused(pw.post(f"{start}/{bare}/run", json={}), 422, "pit_not_ready")
    assert r["param"] == "model_id" and r["missing"][0]["param"] == "model_id"
    no_price = new(pw, mid, "Без цены", elements=[{"field": "AU", "unit": "g/t"}])["id"]
    r = refused(pw.post(f"{start}/{no_price}/run", json={}), 422, "pit_not_ready")
    assert r["param"] == "elements.0.price" and "Сценарий не готов к расчёту" in r["message"]
    refused(pw.post(f"{start}/999/run", json={}), 404, "pit_scenario_not_found")
    refused(pw.post(f"{start}/{no_price}/run", json={"solver": "x"}), 422, "bad_param", "solver")
    for over, status, code in (({"terrain_id": 999}, 404, "wireframe_not_found"),
                               ({"filter_id": 999}, 404, "filter_not_found"),
                               ({"density": "NOPE"}, 404, "block_field_not_found"),
                               ({"restrict": {"cond": {"field": "NOFIELD", "op": ">", "value": 1}}}, 422,
                                "filter_field_missing")):  # fmt: skip
        sid = new(pw, mid, f"Ошибка {code}", **over)["id"]
        refused(pw.post(f"{start}/{sid}/run", json={}), status, code)
    # a field of another source is never replaced (409 before the job); a prefix is the way out
    from tests.test_blocks_fields_store import add_fields

    src = {"op": "calc", "params": {}, "inputs": [], "algo": "calc-1"}
    rows = SMALL[0] * SMALL[1] * SMALL[2]
    add_fields(pw.project, pw.store, mid, [{"name": "ORE", "dtype": "f8"}], {"ORE": np.ones(rows)},
               source=src)  # fmt: skip
    taken = new(pw, mid, "Занято")["id"]
    e = refused(pw.post(f"{start}/{taken}/run", json={}), 409, "pit_fields_taken")
    assert e["field"] == "ORE" and "префикс" in e["hint"] and e["param"] == "prefix"
    # a running scenario can neither be edited, deleted nor started again
    with dialect.write_tx(pw.project.engine) as conn:
        st.mark_running(conn, taken, None)
    for r in (
        pw.post(f"{start}/{taken}/run", json={}),
        pw.delete(f"{start}/{taken}"),
        pw.put(f"{start}/{taken}", json={"name": "Другое", "expected_version": 1}),
    ):
        refused(r, 409, "pit_running")
    assert get(pw, taken)["state"] == "running" and [
        s["state"] for s in ok(pw.get(start))["scenarios"] if s["id"] == taken
    ] == ["running"]
    with dialect.write_tx(pw.project.engine) as conn:
        st.settle(conn, taken)
    assert get(pw, taken)["state"] == "ready"


def test_the_check_is_refused_before_the_job(pw) -> None:  # type: ignore[no-untyped-def]
    from tests.test_blocks_report import put

    mid = make_model(pw, SMALL)
    refused(pw.post(f"{PIT}/check", json={"params": {}}), 422, "bad_param", "model_id")
    refused(pw.post(f"{PIT}/check", json={"params": {"model_id": 9999}}), 404, "block_model_not_found")
    refused(pw.post(f"{PIT}/check", json={"params": {"model_id": mid, "slopes": {"tolerance": 99}}}), 422, "bad_param")
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [2, 1, 1], "tensors": {"x": [10.0, 20.0], "y": [10.0], "z": [10.0]}}
    fields = [{"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade"}]
    var = put(pw, grid, fields, np.arange(2, dtype=np.uint64), {"AU": np.array([1.0, 2.0])})
    e = refused(pw.post(f"{PIT}/check", json={"params": {"model_id": var}}), 422, "variable_grid")
    assert "Регуляризуйте" in e["hint"]
    sid = new(pw, var)["id"]
    refused(pw.post(f"{PIT}/scenarios/{sid}/run", json={}), 422, "variable_grid")


def test_a_density_gap_and_an_empty_pit_come_from_the_job_as_errors_of_the_job(pw) -> None:  # type: ignore[no-untyped-def]
    from tests.pitworld import FIELDS, grid_of
    from tests.test_blocks_report import put

    b = blocks(SMALL, seed=5)
    b["SG"][[7, 8]] = np.nan
    mid = put(pw, grid_of(SMALL), FIELDS, b["p"], {k: v for k, v in b.items() if k != "p"}, name="Без плотности")
    sid = new(pw, mid)["id"]
    started = pw.post(f"{PIT}/scenarios/{sid}/run", json={})
    assert started.status_code == 200
    job = finish(pw, started.json(), timeout=300)
    assert job["status"] == "failed" and job["error"]["code"] == "pit_density_missing" and job["error"]["hint"]
    got = get(pw, sid)
    assert got["state"] == "error" and got["error"]["code"] == "pit_density_missing" and not got["has_result"]
    with dialect.read_tx(pw.project.engine) as conn:
        assert [r.name for r in conn.execute(sa.text("select name from objects where kind = 'pit_scenario'"))] == [
            "Сценарий карьера"]  # fmt: skip
    ok(pw.put(f"{PIT}/scenarios/{sid}", json={"params": params(mid, default_density=2.7), "expected_version": 1}))
    out = run(pw, sid)  # the way out: a default density
    assert out["control"]["default_density"]["blocks"] == 2 and get(pw, sid)["state"] == "ready"


# --- a project «только просмотр» -------------------------------------------------------------------------------


def test_a_project_for_reading_refuses_writes_but_shows_and_compares(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    a = new(pw, mid, "A")["id"]
    run(pw, a)
    back = read_only(pw)
    try:
        for r in (pw.post(f"{PIT}/scenarios", json={}), pw.put(f"{PIT}/scenarios/{a}", json={"name": "Х",
                                                                                           "expected_version": 2}),
                  pw.delete(f"{PIT}/scenarios/{a}"), pw.post(f"{PIT}/scenarios/{a}/run", json={}),
                  pw.post(f"{PIT}/scenarios/{a}/surfaces", json={}), pw.post(f"{PIT}/scenarios/{a}/solid",
                                                                           json={"shell": 1})):  # fmt: skip
            refused(r, 423, "project_read_only")
        assert ok(pw.post(f"{PIT}/validate", json={"params": params(mid)}))["ready"] is True
        assert ok(pw.get(f"{PIT}/scenarios"))["scenarios"][0]["id"] == a and get(pw, a)["has_result"]
        assert ok(pw.get(f"{PIT}/scenarios/{a}/report"))["npv_ready"] is True
        assert ok(pw.get(f"{PIT}/scenarios/{a}/template"))["template"]["k"] == 8
        assert ok(pw.post(f"{PIT}/compare", json={"a": a, "b": a}))["same_grid"] is True
        assert ok(pw.get(f"{PIT}/defaults", params={"model_id": mid}))["model"]["k_auto"] == 8
    finally:
        back()


def test_a_project_really_opened_for_reading_has_no_scheduler_and_the_job_says_so() -> None:
    from bf_core.api.errors import ApiError
    from bf_core.mine import routes

    class NoScheduler:
        def submit(self, *_a: Any, **_k: Any) -> Any:
            raise LookupError("no project")

    with pytest.raises(ApiError) as e:
        routes.submit(NoScheduler(), "pit.check", {}, "Проверка")
    assert (e.value.status, e.value.code) == (423, "project_read_only") and "Откройте проект" in e.value.hint


def test_no_project_is_409(pclient) -> None:  # type: ignore[no-untyped-def]
    refused(pclient.get(f"{PIT}/scenarios"), 409, "no_project")
    refused(pclient.post(f"{PIT}/scenarios", json={}), 409, "no_project")
    refused(pclient.post(f"{PIT}/validate", json={"params": {"model_id": 1}}), 409, "no_project")


# --- «Перестроить» through the graph ---------------------------------------------------------------------------------


def test_rebuild_through_the_graph_gives_the_same_file(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    sid = new(pw, mid)["id"]
    run(pw, sid)
    sha, pit = sha_of(pw, sid), np.asarray(columns(pw, mid, "PIT")["PIT"]).tobytes()
    r = pw.post(f"{API}/lineage/rebuild", json={"node": {"kind": "object:pit_scenario", "id": sid},
                                                "with_dependents": False})  # fmt: skip
    assert r.status_code == 202, r.text
    job = wait_job(pw, r.json()["source"], r.json()["id"], timeout=300)
    assert job["status"] == "done", job
    got = get(pw, sid)
    assert got["version"] == 3 and got["state"] == "ready" and sha_of(pw, sid) == sha  # the same bytes of `data`
    assert np.asarray(columns(pw, mid, "PIT")["PIT"]).tobytes() == pit
    assert ["pit.changed", {"kind": "pit_scenario", "id": sid}] in job["result"]["events"] or any(
        "pit_scenario" in json.dumps(e) for e in job["result"].get("events", []))  # fmt: skip
