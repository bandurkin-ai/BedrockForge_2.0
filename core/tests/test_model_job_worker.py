"""The jobs `implicit.build` / `implicit.update` in a spawn worker on the demo project (подшаг 19.12; e19-api §6): the
shells of the grades of the demo holes built through the job queue, the same answer the body gives in the process, the
update through the queue takes the patches of the build, a refused model fails the job with the reason and puts the
model into `error`. The module is added to the worker by `BF_EXTRA_JOB_MODULES` until the line of `jobs/registry.py`
(the orchestrator) is there, with `tests/model_roles.py` that registers the roles of the files (`db/schema.py`)."""

from __future__ import annotations

import json
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.model import job, spec_parts
from bf_core.model import store as st
from bf_core.model import store_base as sb
from bf_core.model.spec import parse_model
from tests.test_model_job import _roles  # noqa: E402, F401 - the roles of the files in this process

API = "/api/v1"
BOX = [4950.0, 7950.0, 200.0, 5400.0, 8300.0, 440.0]


@pytest.fixture()
def demo(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from bf_core.jobs.registry import HANDLER_MODULES
    from bf_core.projects.service import projects_of

    extra = ["tests.model_roles"]  # the roles of the files in the worker, until the line of `db/schema.py`
    if job.__name__ not in HANDLER_MODULES:  # until the line of the orchestrator (`jobs/registry.py`)
        extra.append(job.__name__)
    monkeypatch.setenv("BF_EXTRA_JOB_MODULES", ",".join(extra))
    assert pclient.post(f"{API}/projects/demo").status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    tables = pclient.get(f"{API}/drillholes/sets/{pclient.set_id}/tables").json()["tables"]
    pclient.assays = next(t["table"] for t in tables if t["name"] == "Пробы")
    return pclient


def make(c: Any, name: str, thresholds: list[float]) -> dict[str, Any]:
    group = {"id": 1, "name": "Au", "type": "shell", "codes": [], "params": {
        "source": "table", "set_id": c.set_id, "table": str(c.assays), "field": "AU", "thresholds": thresholds}}
    body = {"name": name, "boundary": {"box": BOX}, "h": 8.0, "groups": [group]}
    with dialect.write_tx(c.project.engine) as conn:
        return st.create(conn, None, c.project, parse_model(body))


def state(c: Any, mid: int) -> dict[str, Any]:
    with dialect.read_tx(c.project.engine) as conn:
        return dict(sb.stored(conn, mid).meta)


def test_the_job_in_a_worker_gives_what_the_body_gives_and_the_update_takes_its_patches(demo) -> None:  # type: ignore[no-untyped-def]
    from bf_core.jobs.service import jobs_of
    from tests.test_import_job import wait_job

    c = demo
    m = make(c, "Оболочки демо", [0.5, 2.0])
    params = {"model_id": m["id"], "expected_version": 1, "threads": 2}
    key, job_id = jobs_of(c.core).submit(job.KIND_BUILD, params, title="Построение")
    done = wait_job(c, key, job_id, timeout=300)
    assert done["status"] == "done", done
    res = done["result"]
    assert res["state"] == "ready" and res["mode"] == "build" and res["reused"] == {"patches": 0, "bricks": 0}
    bodies = [o for o in res["outputs"] if o["group"] == 1]
    assert [o["threshold"] for o in bodies] == [0.5, 2.0] and bodies[0]["volume"] >= bodies[1]["volume"] > 0
    assert res["events"][0] == ["implicit.changed", {"kind": "implicit_model", "id": m["id"]}]
    assert state(c, m["id"])["last_run"]["job"] == job_id and state(c, m["id"])["state"] == "ready"
    key2, job2 = jobs_of(c.core).submit(job.KIND_UPDATE, {**params, "expected_version": 1}, title="Обновление")
    upd = wait_job(c, key2, job2, timeout=300)
    assert upd["status"] == "done", upd
    assert upd["result"]["mode"] == "update" and upd["result"]["reused"]["patches"] == res["stats"]["patches"] > 0
    assert [(o["name"], o["volume"], o["triangles"]) for o in upd["result"]["outputs"]] == [
        (o["name"], o["volume"], o["triangles"]) for o in res["outputs"]]  # fmt: skip
    json.dumps(upd["result"])  # the answer is a plain JSON


def test_a_refused_model_fails_the_job_with_the_reason(demo) -> None:  # type: ignore[no-untyped-def]
    from bf_core.jobs.service import jobs_of
    from tests.test_import_job import wait_job

    c = demo
    m = make(c, "Пустая граница", [0.5])
    with dialect.write_tx(c.project.engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == m["id"]).values(
            meta={**sb.stored(conn, m["id"]).meta, "groups": [{**sb.stored(conn, m["id"]).meta["groups"][0],
                                                              "relation": "erode"}]}))  # fmt: skip
    key, job_id = jobs_of(c.core).submit(job.KIND_BUILD, {"model_id": m["id"], "expected_version": 1}, title="Ошибка")
    failed = wait_job(c, key, job_id, timeout=120)
    assert failed["status"] == "failed" and failed["error"]["code"] == "not_ready" and failed["error"]["hint"]
    meta = state(c, m["id"])
    assert meta["state"] == "error" and meta["error"]["code"] == "not_ready" and meta["outputs"] == []
    assert spec_parts.REFUSALS["not_built"][0] == 409  # the route's answer for a model that has no result
