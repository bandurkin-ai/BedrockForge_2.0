"""The routes of the simulations (подшаг 18.32; ADR 0024 §9, §11; docs/progress/e18-api.md §9): the rows (CRUD,
404 / 409 / 422 / 423, the events `geostat.changed`), `POST /simulate/run` (refused before the job: 404, 409, 422; then
the real job in a worker), `GET /simulations/{id}/summary`, `POST /simulations/{id}/post` and `POST /simulate/post`.
The routers and the two job modules are included here until the lines of the orchestrator (`geostat/router.py`,
`jobs/registry.py`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from bf_core.jobs import registry
from tests import estapi_util as U
from tests.estapi_util import API, GS, finish, ok, read_only, refused
from tests.estapi_world import MODEL
from tests.estutil import vmodel
from tests.test_estimate_api_answers import SKIP, answer, diff, job_answer

reg, ea = U.reg, U.ea  # the fixtures
SIM = f"{GS}/simulations"
MODULES = "bf_core.geostat.simulation.job,bf_core.geostat.simulation.post"


@pytest.fixture()
def sim_reg(reg, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]  # noqa: ARG001
    """The router and the handlers of 18.32 until the lines of the orchestrator name them (before the app is made)."""
    from bf_core.geostat import router as gr
    from bf_core.geostat.simulation import routes

    if not any(getattr(r, "path", "").endswith("/simulations") for r in gr.router.routes):
        gr.router.include_router(routes.router)
    missing = [m for m in MODULES.split(",") if m not in registry.HANDLER_MODULES]
    if missing:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", ",".join(missing))
    registry.load_handlers()


@pytest.fixture()
def sa(sim_reg, ea):  # type: ignore[no-untyped-def]  # noqa: ARG001
    """A core with an open project, the world of the estimation and a variogram model in normal scores."""
    ea.w.vy = vmodel(ea.w, "AU · Y сим", transform="nscore", model=MODEL)
    return ea


def sim_body(w: Any, label: str = "окисл", **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model_id": w.model, "target": {"domain_field": "ZONE", "label": label}, "n_real": 4, "nodes": 6, "seed": 99,
        "samples": {"set_id": w.set_id, "table": str(w.a), "field": "AU",
                    "domain": {"table": str(w.b), "field": "ZONE", "values": [label]}},
        "variogram": {"model_id": w.vy}}  # fmt: skip
    out.update(over)
    return out


def events(c: Any, kind: str | None = None) -> list[dict[str, Any]]:
    return [d for n, d in c.events if n == "geostat.changed" and (kind is None or d["kind"] == kind)]


def model_version(c: Any) -> int:
    return int(c.get(f"{API}/blocks/{c.w.model}").json()["version"])


def make_sim(c: Any, **over: Any) -> dict[str, Any]:
    return ok(c.post(SIM, json=sim_body(c.w, **over)))  # type: ignore[no-any-return]


# --- the rows ----------------------------------------------------------------------------------------------------


def test_row_crud_and_events(sa) -> None:  # type: ignore[no-untyped-def]
    made = make_sim(sa)
    assert made == {"id": made["id"], "name": "AU · SGS · окисл", "version": 1}
    assert events(sa, "simulation") == [{"kind": "simulation", "id": made["id"]}]
    got = ok(sa.get(f"{SIM}/{made['id']}"))
    assert got["meta"]["kind"] == "sgs" and got["meta"]["variogram"]["scale"] == "y" and got["file"] is None
    assert (got["stale"], got["source_lost"]) == (False, False) and got["meta"]["origin"]
    listed = ok(sa.get(SIM))["simulations"]
    assert [(r["id"], r["kind"], r["label"], r["field"], r["n_real"], r["last_run"]) for r in listed] == [
        (made["id"], "sgs", "окисл", "AU", 4, None)]  # fmt: skip
    assert ok(sa.get(SIM, params={"model_id": sa.w.model + 1}))["simulations"] == []
    put = ok(sa.put(f"{SIM}/{made['id']}", json={"expected_version": 1, "n_real": 5, "name": "Моя SGS"}))
    assert put == {"id": made["id"], "name": "Моя SGS", "version": 2}
    assert ok(sa.get(f"{SIM}/{made['id']}"))["meta"]["n_real"] == 5
    assert refused(sa.put(f"{SIM}/{made['id']}", json={"expected_version": 1}), 409, "version_conflict")["version"] == 2
    assert ok(sa.delete(f"{SIM}/{made['id']}")) == {"deleted": made["id"]}
    assert {"kind": "simulation", "id": made["id"], "deleted": True} in events(sa)
    refused(sa.get(f"{SIM}/{made['id']}"), 404, "simulation_not_found")
    refused(sa.delete(f"{SIM}/{made['id']}"), 404, "simulation_not_found")
    refused(sa.put(f"{SIM}/{made['id']}", json={"expected_version": 1}), 404, "simulation_not_found")


def test_the_order_of_the_checks_and_a_read_only_project(sa) -> None:  # type: ignore[no-untyped-def]
    made = make_sim(sa)
    refused(sa.post(SIM, json=[1]), 422, "bad_request")
    refused(sa.post(SIM, json=sim_body(sa.w, n_real=0)), 422, "bad_param", "n_real")
    refused(sa.post(SIM, json=sim_body(sa.w, kind="sis")), 422, "not_ready", "kind")
    refused(sa.post(SIM, json=sim_body(sa.w, variogram={"model_id": sa.w.vz})), 422, "variogram_scale",
            "variogram.model_id")  # fmt: skip
    refused(sa.post(SIM, json=sim_body(sa.w, model_id=sa.w.model + 99)), 404, "block_model_not_found", "model_id")
    refused(sa.post(SIM, json=sim_body(sa.w, name="au · sgs · ОКИСЛ")), 409, "name_taken", "name")
    ok(sa.post(SIM, json=sim_body(sa.w, name="Другая")))
    back = read_only(sa)
    try:
        refused(sa.post(SIM, json=sim_body(sa.w)), 423, "project_read_only")
        refused(sa.put(f"{SIM}/{made['id']}", json={"expected_version": 9}), 423, "project_read_only")
        refused(sa.delete(f"{SIM}/{made['id']}"), 423, "project_read_only")
        refused(sa.post(f"{GS}/simulate/run", json={"simulation_id": made["id"]}), 423, "project_read_only")
        refused(sa.post(f"{SIM}/{made['id']}/post", json={"expected_version": 1}), 423, "project_read_only")
        assert ok(sa.get(f"{SIM}/{made['id']}"))["id"] == made["id"]  # reading works
        assert len(ok(sa.get(SIM))["simulations"]) == 2
    finally:
        back()
    assert len(events(sa, "simulation")) == 2  # nothing was published by the refusals


# --- the run and the post-processing through the jobs ------------------------------------------------------------


def test_run_summary_and_post_through_the_jobs(sa) -> None:  # type: ignore[no-untyped-def]
    ident = make_sim(sa)["id"]
    refused(sa.get(f"{SIM}/{ident}/summary"), 409, "no_realizations")
    refused(sa.post(f"{SIM}/{ident}/post", json={"expected_version": model_version(sa)}), 409, "no_realizations")
    started = ok(sa.post(f"{GS}/simulate/run", json={"simulation_id": ident, "expected_version": 1}))
    job = finish(sa, started)
    assert job["status"] == "done", job
    res = job["result"]
    assert (res["simulation_id"], res["nodes"], res["realizations"]) == (ident, 200, 4)
    assert res["events"] == [["geostat.changed", {"kind": "simulation", "id": ident}]]
    assert {"kind": "simulation", "id": ident} in events(sa, "simulation")[1:]  # the planner published the event
    got = ok(sa.get(f"{SIM}/{ident}"))
    assert got["file"]["columns"] == ["p", "dom", "r001", "r002", "r003", "r004"] and got["file"]["rows"] == 200
    assert got["meta"]["last_run"]["job"] == started["id"] and got["stale"] is False
    s = ok(sa.get(f"{SIM}/{ident}/summary"))
    assert (s["nodes"], s["realizations"]) == (200, 4) and len(s["summary"]["per"]) == 4
    assert s["summary"]["per"][0]["n"] == 200 and s["counts"]["free"] == 0 and s["cond"]["max"] <= 24
    # the post-processing: the checks go before the job
    version = model_version(sa)
    refused(sa.post(f"{SIM}/{ident}/post", json={"expected_version": version + 5}), 409, "version_conflict")
    refused(sa.post(f"{SIM}/{ident}/post", json={}), 422, "bad_param", "expected_version")
    refused(sa.post(f"{SIM}/{ident}/post", json={"expected_version": version, "stats": ["prob"]}), 422, "bad_param",
            "thresholds")  # fmt: skip
    refused(sa.post(f"{SIM}/{ident + 99}/post", json={"expected_version": version}), 404, "simulation_not_found")
    body = {"expected_version": version, "thresholds": [1.0]}
    post = finish(sa, ok(sa.post(f"{SIM}/{ident}/post", json=body)))
    assert post["status"] == "done", post
    assert post["result"]["fields"] == ["AU_SGS_MEAN", "AU_SGS_STD", "AU_SGS_P10", "AU_SGS_P50", "AU_SGS_P90",
                                        "AU_SGS_PROB_GT_1"]  # fmt: skip
    assert post["result"]["version"] == version + 1 and post["result"]["events"][0][0] == "objects.changed"
    fields = {f["name"]: f for f in ok(sa.get(f"{API}/blocks/{sa.w.model}"))["fields"]}
    assert fields["AU_SGS_MEAN"]["source"]["op"] == "simulate" and fields["AU_SGS_PROB_GT_1"]["dtype"] == "f4"
    refused(sa.post(f"{SIM}/{ident}/post", json={**body, "expected_version": version + 1}), 409, "field_exists")
    alias = {"simulation_id": ident, "expected_version": version + 1, "replace": True, "stats": ["mean"]}
    again = finish(sa, ok(sa.post(f"{GS}/simulate/post", json=alias)))
    assert again["status"] == "done" and again["result"]["fields"] == ["AU_SGS_MEAN"]


def test_a_body_run_and_the_refusals_before_the_job(sa) -> None:  # type: ignore[no-untyped-def]
    run = f"{GS}/simulate/run"
    refused(sa.post(run, json={}), 422, "bad_param", "simulation_id")
    refused(sa.post(run, json={"simulation_id": 999}), 404, "simulation_not_found")
    refused(sa.post(run, json={"simulation": sim_body(sa.w, n_real=101)}), 422, "bad_param", "n_real")
    refused(sa.post(run, json={"simulation": sim_body(sa.w, kind="sis")}), 422, "not_ready", "kind")
    refused(sa.post(run, json={"simulation": sim_body(sa.w, variogram={"model_id": sa.w.vz})}), 422,
            "variogram_scale", "variogram.model_id")  # fmt: skip
    ident = make_sim(sa)["id"]
    refused(sa.post(run, json={"simulation_id": ident, "expected_version": 4}), 409, "version_conflict")
    refused(sa.post(run, json={"simulation": sim_body(sa.w, name="AU · sgs · окисл")}), 409, "name_taken")
    job = finish(sa, ok(sa.post(run, json={"simulation": sim_body(sa.w, label="перех", name="Из тела")})))
    assert job["status"] == "done", job
    made = job["result"]["simulation_id"]
    assert made != ident and ok(sa.get(f"{SIM}/{made}"))["name"] == "Из тела"
    assert {"kind": "simulation", "id": made} in events(sa, "simulation")
    # the model is gone: «источник удалён», the run is refused before the job
    assert sa.delete(f"{API}/blocks/{sa.w.model}").status_code == 200
    refused(sa.post(run, json={"simulation_id": ident}), 409, "source_lost")
    assert ok(sa.get(f"{SIM}/{ident}"))["source_lost"] is True


# --- the answers of the core the windows are read against ---------------------------------------------------------

DATA = Path(__file__).parent / "data" / "simulate" / "answers.json"
REGEN = os.environ.get("BF_REGEN_SIMULATE") == "1"
NOTE = ("Ответы настоящего ядра (BF_REGEN_SIMULATE=1 uv run pytest -q tests/test_simulation_api.py, 18.32) на "
        "синтетическом проекте tests/estapi_world.py (25 скважин, блочная модель из трёх доменов, модель вариограммы в "
        "нормальных оценках «AU · Y сим»). Формы: docs/progress/e18-api.md §9. Ключи — как у "
        "core/tests/data/estimate/contract_answers.json: {status, body}.")  # fmt: skip
MASK = (*SKIP, "job", "at")


def masked(x: Any) -> Any:
    """The answer without what differs from run to run: the times, the fingerprints, the hashes, the ids of the jobs."""
    if isinstance(x, dict):
        return {k: masked(v) for k, v in x.items() if k not in MASK}
    if isinstance(x, list):
        return [masked(v) for v in x]
    return x


def build(c: Any) -> dict[str, Any]:
    """The answers of the routes of 18.32, in the order a window meets them."""
    w = c.w
    out: dict[str, Any] = {}
    first = c.post(SIM, json=sim_body(w, name="AU · SGS · окисл"))
    out["simulation_post"] = answer(first)
    ident = first.json()["id"]
    out["simulation_name_taken"] = answer(c.post(SIM, json=sim_body(w, name="au · sgs · ОКИСЛ")))
    out["bad_param"] = answer(c.post(SIM, json=sim_body(w, n_real=101)))
    out["bad_param_search"] = answer(c.post(SIM, json=sim_body(w, search={"passes": [{"n_max": 129}]})))
    out["variogram_scale"] = answer(c.post(SIM, json=sim_body(w, variogram={"model_id": w.vz}, name="В Z")))
    out["not_ready"] = answer(c.post(SIM, json=sim_body(w, kind="sis", name="SIS")))
    out["simulation_empty"] = answer(c.get(f"{SIM}/{ident}"))  # before the first run: `file` null
    out["summary_empty"] = answer(c.get(f"{SIM}/{ident}/summary"))
    assert c.put(f"{SIM}/{ident}", json={"expected_version": 1, "n_real": 5}).status_code == 200
    out["simulation_put_conflict"] = answer(c.put(f"{SIM}/{ident}", json={"expected_version": 1, "n_real": 6}))
    run = {"simulation_id": ident, "expected_version": 2}
    out["run_submit"], out["run_result"], _ = job_answer(c, c.post(f"{GS}/simulate/run", json=run))
    out["run_version_conflict"] = answer(c.post(f"{GS}/simulate/run", json={**run, "expected_version": 1}))
    out["simulation"] = answer(c.get(f"{SIM}/{ident}"))
    out["simulations"] = answer(c.get(SIM))
    out["summary"] = answer(c.get(f"{SIM}/{ident}/summary"))
    version = model_version(c)
    body = {"expected_version": version, "thresholds": [1.0]}
    stale = {**body, "expected_version": version + 1}
    out["post_version_conflict"] = answer(c.post(f"{SIM}/{ident}/post", json=stale))
    out["post_submit"], out["post_result"], _ = job_answer(c, c.post(f"{SIM}/{ident}/post", json=body))
    out["post_field_exists"] = answer(c.post(f"{SIM}/{ident}/post", json=stale))
    inline = sim_body(w, label="перех", name="AU · SGS · перех")
    out["run_body_result"] = job_answer(c, c.post(f"{GS}/simulate/run", json={"simulation": inline}))[1]
    out["simulation_delete"] = answer(c.delete(f"{SIM}/{ident}"))
    return json.loads(json.dumps(out, ensure_ascii=False))  # type: ignore[no-any-return]


def test_the_answers_are_what_the_core_answers(sa) -> None:  # type: ignore[no-untyped-def]
    built = build(sa)
    old = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(json.dumps({"_note": NOTE, **built}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        old = {"_note": NOTE, **built}
    for key, value in built.items():
        assert key in old, f"нет ответа «{key}» в answers.json: BF_REGEN_SIMULATE=1"
        found = diff(masked(old[key]), masked(value), f"/{key}")
        assert found is None, found
    assert sorted(set(old) - {"_note"}) == sorted(built)
    # what the windows rely on, whatever the numbers
    assert built["simulation_post"]["status"] == 200 and built["simulation_name_taken"]["status"] == 409
    assert built["simulation_put_conflict"]["body"]["code"] == "version_conflict"
    assert built["bad_param"]["body"]["param"] == "n_real"
    assert built["bad_param_search"]["body"]["param"] == "search.passes.0.n_max"
    assert (built["variogram_scale"]["status"], built["variogram_scale"]["body"]["code"]) == (422, "variogram_scale")
    assert (built["not_ready"]["status"], built["not_ready"]["body"]["code"]) == (422, "not_ready")
    assert built["simulation_empty"]["body"]["file"] is None and built["summary_empty"]["status"] == 409
    assert built["run_result"]["body"]["realizations"] == 5 and built["simulation"]["body"]["file"]["rows"] > 0
    assert built["post_result"]["body"]["fields"][0] == "AU_SGS_MEAN"
    assert built["post_version_conflict"]["body"]["code"] == "version_conflict"
    assert built["post_field_exists"]["body"]["code"] == "field_exists" and built["post_field_exists"]["body"]["field"]
    assert built["summary"]["body"]["summary"]["per"] and built["run_body_result"]["body"]["simulation_id"]
