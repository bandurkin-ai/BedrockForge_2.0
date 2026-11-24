"""The routes of the operations on a block model: the prototype, the regularization, the density, the categories
(подшаг 17.29; ТЗ 8.7.3, 8.7.5; ADR 0023 §6, §9; docs/progress/e17-api.md §6, §9, §15).

The request is checked before the job starts (404, 409, 422 with `param`), the answer is `{source, id}`, the job runs
in the worker and its result is the one of `jobs.py` / `jobs_fields.py`; a project «только просмотр» refuses
everything that writes (423); «Перестроить» of a regularized model through `POST /blocks/{id}/rebuild`."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa
from fastapi import APIRouter

from bf_core.blocks import prototype as pr
from bf_core.blocks import routes_fields, routes_ops
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.projects.service import projects_of
from tests.test_archive import wait_job
from tests.test_blocks_fields_store import make_model
from tests.test_blocks_fill_jobs import P, add_field, body, model, plain_model, prototype
from tests.test_blocks_ops import ORE

API = "/api/v1"
BL = API + "/blocks"
MODULES = "bf_core.blocks.jobs,bf_core.blocks.jobs_fields,bf_core.blocks.calc_job,bf_core.blocks.fill.jobs"


def setup_core(pclient: Any, monkeypatch: pytest.MonkeyPatch, name: str = "Операции") -> Any:
    """A core with an open project and a model of 350 rows (AU, CU, N, DOM); the routes and the jobs of 17.29 are
    mounted here until the lines of the orchestrator name them (`blocks/router.py`, `jobs/registry.py`)."""
    from bf_core.jobs import registry

    monkeypatch.setenv("BF_EXTRA_JOB_MODULES", MODULES)
    registry.load_handlers()  # the API process knows the kinds (the worker imports them by the same variable)
    paths = {getattr(r, "path", "") for r in pclient.app.routes}
    mount = APIRouter(prefix="/blocks")
    if f"{BL}/prototype" not in paths:
        mount.include_router(routes_ops.router)
    if f"{BL}/{{bm_id}}/fields" not in paths:
        mount.include_router(routes_fields.router)
    pclient.app.include_router(mount, prefix=API)
    assert pclient.post(f"{API}/projects", json={"name": name}).status_code == 200
    project = projects_of(pclient.core).require_current()
    pclient.p = P(project.engine, project.folder)
    pclient.project = project
    pclient.mid = make_model(pclient.p, pclient.p.store)
    return pclient


@pytest.fixture()
def oc(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    return setup_core(pclient, monkeypatch)


def done(c: Any, r: Any, timeout: float = 120) -> dict[str, Any]:
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=timeout)
    assert job["status"] == "done", (job, c.get(f"{API}/jobs/{r.json()['source']}/{r.json()['id']}/log").text)
    return dict(job["result"])


def refused(r: Any, status: int, code: str, param: str | None = None) -> dict[str, Any]:
    assert (r.status_code, r.json()["code"]) == (status, code), r.text
    if param is not None:
        assert r.json()["param"] == param, r.text
    return dict(r.json())


def opened(c: Any, mid: int) -> Model:
    return model(c.p, mid)


# --- the prototype ----------------------------------------------------------------------------------------------------

PROTO = {"size": [10, 10, "5,0"], "extent": [0, 0, 0, 95, 45, 20], "pad": "5", "rotation": 30, "levels": [1, 1, 0]}


def test_the_prototype_preview_and_the_job(oc) -> None:  # type: ignore[no-untyped-def]
    r = oc.post(f"{BL}/prototype/preview", json=PROTO)
    assert r.status_code == 200, r.text
    plan = pr.make_plan([10, 10, 5], [0, 0, 0, 95, 45, 20], pad=5, rotation=30, levels=[1, 1, 0])
    assert r.json() == plan.preview() and len(r.json()["frame"]) == 8
    before = opened(oc, oc.mid).version
    out = done(oc, oc.post(f"{BL}/prototype", json={**PROTO, "name": "Сетка"}))
    assert (out["name"], out["parents"], out["grid"]) == ("Сетка", plan.parents, plan.grid.to_meta())
    m = opened(oc, out["model_id"])
    assert (m.rows, m.meta["source"]["op"], m.grid.cells) == (0, "prototype", plan.parents)
    assert opened(oc, oc.mid).version == before
    wf_id = body(oc.p, *ORE, name="Руда")  # the box of an object
    r = oc.post(f"{BL}/prototype/preview", json={"size": 10, "object_ids": [wf_id], "pad": 10})
    assert r.status_code == 200 and r.json()["parents"] > 0 and r.json()["extent"][0] <= ORE[0][0] - 10 + 1e-9
    named = done(oc, oc.post(f"{BL}/prototype", json={"size": 10, "object_ids": [wf_id]}))
    assert named["name"] == "Прототип" and opened(oc, named["model_id"]).meta["source"]["params"]["extent"][0] == 12.5


def test_the_prototype_refusals_come_before_the_job(oc) -> None:  # type: ignore[no-untyped-def]
    cases = [
        ({**PROTO, "object_ids": [1]}, 422, "bad_param", "object_ids"),
        ({"size": [10, 10, 10]}, 422, "bad_param", "object_ids"),
        ({"size": [10, 10, 10], "object_ids": [999_999]}, 404, "object_not_found", "object_ids.0"),
        ({**PROTO, "size": [10, 0, 5]}, 422, "bad_param", "size"),
        ({**PROTO, "size": "x"}, 422, "bad_param", "size"),
        ({**PROTO, "extent": [0, 0, 0, 5, 5]}, 422, "bad_param", "extent"),
        ({**PROTO, "pad": -1}, 422, "bad_param", "pad"),
        ({**PROTO, "levels": [1, 9, 0]}, 422, "subblock_div", None),
        ({**PROTO, "size": [0.001, 0.001, 0.001], "extent": [0, 0, 0, 5000, 5000, 5000]}, 422,
         "block_model_too_large", None),
    ]  # fmt: skip
    for payload, status, code, param in cases:
        for path in ("/prototype/preview", "/prototype"):
            refused(oc.post(BL + path, json=payload), status, code, param)
    refused(oc.post(f"{BL}/prototype", json={**PROTO, "name": 5}), 422, "bad_param", "name")
    refused(oc.post(f"{BL}/prototype", json=[1]), 422, "bad_request")
    assert opened(oc, oc.mid) and not [i for i in _models(oc) if i != oc.mid]  # nothing was made


def _models(c: Any) -> list[int]:
    with dialect.read_tx(c.project.engine) as conn:
        return [int(i) for i in conn.execute(sa.select(objects.c.id).where(objects.c.kind == "block_model")).scalars()]


def test_a_read_only_project_refuses_to_write_but_previews(oc) -> None:  # type: ignore[no-untyped-def]
    oc.project.read_only = True
    try:
        assert oc.post(f"{BL}/prototype/preview", json=PROTO).status_code == 200
        v = opened(oc, oc.mid).version
        for path, payload in (
            ("/prototype", PROTO),
            (f"/{oc.mid}/regularize", {"size": [20, 20, 20]}),
            (f"/{oc.mid}/density", {"expected_version": v, "method": "constant", "value": 2.7}),
            (f"/{oc.mid}/classify", {"expected_version": v, "rules": [{"label": "A", "expr": "AU > 1"}]}),
        ):
            refused(oc.post(BL + path, json=payload), 423, "project_read_only")
    finally:
        oc.project.read_only = False


# --- the chain over HTTP: the prototype, the fill, the regularization, «Перестроить» -----------------------


def test_the_chain_over_http_and_rebuild_of_the_regularized_model(oc) -> None:  # type: ignore[no-untyped-def]
    proto = done(
        oc, oc.post(f"{BL}/prototype", json={"size": 10, "extent": [0, 0, 0, 60, 50, 40], "levels": [1, 1, 1]})
    )
    wid = body(oc.p, *ORE, name="Руда")
    fill = done(oc, oc.post(f"{BL}/fill", json={"prototype_id": proto["model_id"], "wireframes": [{"id": wid}]}))
    reg = done(
        oc, oc.post(f"{BL}/{fill['model_id']}/regularize", json={"size": ["20", "20,0", 20], "name": "Блоки 20"})
    )
    rid = reg["model_id"]
    assert reg["name"] == "Блоки 20" and reg["totals"]["after"]["volume"] == pytest.approx(10000.0, rel=1e-12)
    assert opened(oc, rid).meta["source"]["params"]["size"] == [20.0, 20.0, 20.0]
    # «Перестроить»: the same job on the same params, the model in place
    add_field(oc.p, rid, "X", np.zeros(opened(oc, rid).rows))
    v = opened(oc, rid).version
    r = oc.post(f"{BL}/{rid}/rebuild", json={"expected_version": v})
    out = done(oc, r)
    assert (out["model_id"], out["version"], out["dropped"], out["name"]) == (rid, v + 1, ["X"], "Блоки 20")
    refused(oc.post(f"{BL}/{rid}/rebuild", json={"expected_version": v}), 409, "version_conflict")
    assert refused(oc.post(f"{BL}/{rid}/rebuild", json={"expected_version": v}), 409, "version_conflict")[
        "version"
    ] == (v + 1)
    # the source is deleted: nothing to rebuild from
    with dialect.write_tx(oc.project.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == fill["model_id"]))
    refused(oc.post(f"{BL}/{rid}/rebuild", json={"expected_version": v + 1}), 409, "source_lost")


def test_the_refusals_of_the_regularization_come_before_the_job(oc) -> None:  # type: ignore[no-untyped-def]
    mid, v = oc.mid, opened(oc, oc.mid).version
    cases = [
        ({"size": [0, 1, 1]}, 422, "bad_param", "size"),
        ({}, 422, "bad_param", "size"),
        ({"size": [20, 20, 20], "grades": [{"field": "NOPE"}]}, 404, "block_field_not_found", None),
        ({"size": [20, 20, 20], "grades": [{"field": "DOM"}]}, 422, "bad_param", "grades.0.field"),
        ({"size": [20, 20, 20], "grades": [{"field": "AU", "weight": "tonnes"}]}, 422, "bad_param", "density"),
        ({"size": [20, 20, 20], "categories": ["AU"]}, 422, "bad_param", "categories.0"),
        ({"size": [20, 20, 20], "expected_version": v + 5}, 409, "version_conflict", None),
        ({"size": [20, 20, 20], "name": ["x"]}, 422, "bad_param", "name"),
    ]
    for payload, status, code, param in cases:
        refused(oc.post(f"{BL}/{mid}/regularize", json=payload), status, code, param)
    refused(oc.post(f"{BL}/777777/regularize", json={"size": [20, 20, 20]}), 404, "block_model_not_found")
    proto = prototype(oc.p)  # no rows
    refused(oc.post(f"{BL}/{proto}/regularize", json={"size": [20, 20, 20]}), 422, "block_model_empty")
    var = plain_model(oc.p, name="Модель 2")
    out = done(oc, oc.post(f"{BL}/{var}/regularize", json={"size": [20, 20, 20], "expected_version": 1}))
    assert out["rows"] > 0 and out["name"] == "Модель 2 (регуляризация)"


# --- the density and the categories -------------------------------------------------------------------------


def test_the_density_route_checks_the_request_then_the_job_writes(oc) -> None:  # type: ignore[no-untyped-def]
    mid = oc.mid
    base = {"expected_version": 1, "method": "field", "field": "CU", "default": 2.5, "restrict": {"expr": "N > 10"}}
    out = done(oc, oc.post(f"{BL}/{mid}/density", json=base))
    assert (out["fields"], out["version"], out["rows"]) == (["DENS", "DENS_SRC"], 2, 350)
    f = opened(oc, mid).field("DENS")
    assert f["role"] == "density" and f["source"]["params"]["restrict"] == {"expr": "N > 10"}
    two = {**base, "expected_version": 2, "out": "D2", "src_field": "S2"}
    cases = [
        ({**two, "expected_version": 1}, 409, "version_conflict", None),
        ({**base, "expected_version": 2}, 409, "field_exists", None),
        ({**two, "field": "NOPE"}, 422, "density_field_missing", "field"),
        ({**two, "field": "DOM"}, 422, "bad_param", "field"),
        ({**two, "method": "weird"}, 422, "bad_param", "method"),
        ({**two, "method": "constant"}, 422, "bad_param", "value"),
        ({**two, "default": -1}, 422, "bad_param", "default"),
        ({**two, "src_field": "d2"}, 422, "bad_param", "src_field"),
        ({**two, "restrict": {"expr": "NOPE > 1"}}, 422, "filter_field_missing", None),
        ({**two, "filter_id": 999}, 404, "filter_not_found", None),
        ({**two, "replace": "yes"}, 422, "bad_param", "replace"),
        ({**two, "expected_version": 0}, 422, "bad_param", "expected_version"),
        ({**two, "method": "formula", "expr": "AU +"}, 422, "expr_syntax", None),
    ]  # fmt: skip
    for payload, status, code, param in cases:
        refused(oc.post(f"{BL}/{mid}/density", json=payload), status, code, param)
    refused(oc.post(f"{BL}/777777/density", json={**base}), 404, "block_model_not_found")
    proto = prototype(oc.p)
    refused(oc.post(f"{BL}/{proto}/density", json={**base, "expected_version": 1}), 422, "block_model_empty")
    assert opened(oc, mid).version == 2  # none of the refusals wrote anything
    done(oc, oc.post(f"{BL}/{mid}/density", json={**base, "expected_version": 2, "replace": True}))
    assert opened(oc, mid).version == 3


def test_the_classify_route_with_a_scheme_a_custom_scheme_and_the_refusals(oc) -> None:  # type: ignore[no-untyped-def]
    mid = oc.mid
    refused(oc.post(f"{BL}/{mid}/classify", json={"expected_version": 1, "scheme": "gkz"}), 422, "scheme_incomplete",
            "rules.0.expr")  # fmt: skip
    refused(oc.post(f"{BL}/{mid}/classify", json={"expected_version": 1, "scheme": "nope"}), 404,
            "scheme_not_found")  # fmt: skip
    refused(oc.post(f"{BL}/{mid}/classify", json={"expected_version": 1, "scheme": "jorc"}), 422,
            "classify_field_missing")  # fmt: skip
    refused(oc.post(f"{BL}/{mid}/classify", json={"expected_version": 1}), 422, "bad_param", "rules")
    rules = [{"label": "Руда", "expr": "AU >= 1"}, {"label": "Бедная", "expr": "AU >= 0"}]
    put = oc.put(f"{BL}/schemes/my", json={"name": "Моя", "rules": rules})
    assert put.status_code == 200, put.text
    out = done(oc, oc.post(f"{BL}/{mid}/classify", json={"expected_version": 1, "scheme": "my", "out": "K"}))
    assert out["field"] == "K" and set(out["counts"]) == {"Руда", "Бедная"} and out["version"] == 2
    f = opened(oc, mid).field("K")
    assert f["labels"] == ["Руда", "Бедная"] and f["source"]["params"]["scheme"] == "my"
    assert f["source"]["params"]["rules"] == rules
    cases = [
        ({"expected_version": 2, "rules": rules, "out": "K"}, 409, "field_exists", None),
        ({"expected_version": 1, "rules": rules, "out": "K2"}, 409, "version_conflict", None),
        ({"expected_version": 2, "rules": rules, "out": "K2", "majority": 11}, 422, "bad_param", "majority"),
        ({"expected_version": 2, "rules": rules, "out": "K2", "estimate": "NOPE"}, 422, "classify_field_missing", None),
        ({"expected_version": 2, "rules": [{"label": "A", "expr": "NOPE > 1"}], "out": "K2"}, 422,
         "classify_field_missing", None),
        ({"expected_version": 2, "rules": rules, "out": "K2", "overrides": [{"wireframe_id": 999, "label": "Руда"}]},
         404, "wireframe_not_found", "overrides.0.wireframe_id"),
        ({"expected_version": 2, "rules": rules, "out": "K2", "overrides": [{"wireframe_id": 1, "label": "?"}]},
         422, "bad_param", "overrides.0.label"),
        ({"expected_version": 2, "rules": rules, "out": "K2", "restrict": {"expr": "NOPE > 1"}}, 422,
         "filter_field_missing", None),
    ]  # fmt: skip
    for payload, status, code, param in cases:
        refused(oc.post(f"{BL}/{mid}/classify", json=payload), status, code, param)
    wid = body(oc.p, (0.0, 0.0, 0.0), (1000.0, 1000.0, 1000.0), name="Зона")
    out = done(oc, oc.post(f"{BL}/{mid}/classify", json={
        "expected_version": 2, "rules": rules, "out": "K2", "majority": 1,
        "overrides": [{"wireframe_id": wid, "label": "Бедная"}]}))  # fmt: skip
    assert out["field"] == "K2" and out["version"] == 3
    assert opened(oc, mid).field("K2")["source"]["params"]["overrides"] == [{"wireframe_id": wid, "label": "Бедная"}]
