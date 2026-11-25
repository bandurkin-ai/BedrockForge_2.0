"""The routes of the graph and «Перестроить с зависимыми» (ADR 0025 §7–10, ТД35-5; 18.Г2): `/lineage/status`,
`impact`, `origin`, `pin`, `rebuild` and the job `lineage.rebuild` in a `spawn` worker over a chain of stubs (the
order, the cancel on the second node, a failing node, a node that cannot be rebuilt) and over real results (composites
→ a model of variography: the rebuilt chain is current and bit for bit what a calculation from scratch gives).

The router and the job are registered here until the lines of the orchestrator (`api/modules.py`,
`jobs/registry.py`)."""

from __future__ import annotations

import importlib
import time
from collections.abc import Callable
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core import lineage
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.lineage.store import Node
from bf_core.projects.service import projects_of
from tests.lineage_stub_jobs import NODE, compute
from tests.test_import_job import API, wait_job

L = f"{API}/lineage"
STUB_MODULES = ("bf_core.lineage.job", "tests.lineage_stub_jobs")


def register(monkeypatch: pytest.MonkeyPatch, modules: tuple[str, ...]) -> None:
    """The router `lineage`, and the handlers of `modules` here and in the worker (until the orchestrator's lines)."""
    from bf_core.api import app as app_mod
    from bf_core.jobs.registry import HANDLER_MODULES

    if "lineage" not in app_mod.ROUTER_MODULES:
        monkeypatch.setattr(app_mod, "ROUTER_MODULES", (*app_mod.ROUTER_MODULES, "lineage"))
    extra = [m for m in modules if m not in HANDLER_MODULES]
    if extra:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", ",".join(extra))
    for m in modules:
        importlib.import_module(m)


@pytest.fixture()
def stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    register(monkeypatch, STUB_MODULES)


def add(conn: sa.Connection, name: str, value: float, inputs: tuple[int, ...] = (), kind: str = "stub") -> int:
    now = dialect.utcnow()
    res = conn.execute(
        sa.insert(objects).values(
            kind=kind, name=name, name_key=dialect.name_key(name), sort_key=0.0, state="ready", version=1,
            meta={"value": value, "builds": 0}, created_at=now, updated_at=now,
        )
    )  # fmt: skip
    oid = int(res.inserted_primary_key[0])  # type: ignore[index]
    if inputs:
        lineage.record(conn, Node(f"object:{kind}", oid), [Node(NODE, i) for i in inputs])
    return oid


def chain_values(source: float) -> dict[str, float]:
    """Every node as a calculation from scratch gives it for the value `source` of the source."""
    n1 = compute([source])
    n2 = compute([n1])
    n3 = compute([n2])
    return {"n1": n1, "n2": n2, "m": compute([n1]), "n3": n3, "n4": compute([n3])}


class Chain:
    """S0 → N1 → {N2 → N3 → N4, M}: the value of a node is `compute` of the values of its inputs."""

    def __init__(self, c: Any) -> None:
        self.c = c
        self.engine = projects_of(c.core).require_current().engine
        v = chain_values(1.0)
        with dialect.write_tx(self.engine) as conn:
            self.s0 = add(conn, "Источник", 1.0)
            self.n1 = add(conn, "Узел 1", v["n1"], (self.s0,))
            self.n2 = add(conn, "Узел 2", v["n2"], (self.n1,))
            self.m = add(conn, "Ветка M", v["m"], (self.n1,))
            self.n3 = add(conn, "Узел 3", v["n3"], (self.n2,))
            self.n4 = add(conn, "Узел 4", v["n4"], (self.n3,))

    def edit_source(self, value: float) -> None:
        with dialect.write_tx(self.engine) as conn:
            self.patch(conn, self.s0, value=value)

    @staticmethod
    def patch(conn: sa.Connection, oid: int, **meta: Any) -> None:
        row = conn.execute(sa.select(objects.c.meta, objects.c.version).where(objects.c.id == oid)).one()
        values = {"meta": {**row.meta, **meta}, "version": row.version + 1}
        conn.execute(sa.update(objects).where(objects.c.id == oid).values(**values))

    def mark(self, oid: int, **meta: Any) -> None:
        with dialect.write_tx(self.engine) as conn:
            self.patch(conn, oid, **meta)

    def node(self, oid: int) -> dict[str, Any]:
        return {"kind": NODE, "id": oid}

    def meta(self, oid: int) -> dict[str, Any]:
        with dialect.read_tx(self.engine) as conn:
            return dict(conn.execute(sa.select(objects.c.meta).where(objects.c.id == oid)).scalar_one())

    def states(self) -> dict[int, str]:
        ids = [self.s0, self.n1, self.n2, self.n3, self.n4, self.m]
        r = self.c.post(f"{L}/status", json={"nodes": [self.node(i) for i in ids]})
        assert r.status_code == 200, r.text
        return {i["id"]: i["state"] for i in r.json()["items"]}

    def start(self, **body: Any) -> Any:
        return self.c.post(f"{L}/rebuild", json={"node": self.node(self.n1), **body})


@pytest.fixture()
def chain(stubs, pclient) -> Chain:  # type: ignore[no-untyped-def]
    assert pclient.post(API + "/projects", json={"name": "Граф"}).status_code == 200
    return Chain(pclient)


def until(pred: Callable[[], bool], timeout: float = 30.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("timeout")


def current(states: dict[int, str]) -> bool:
    return set(states.values()) == {"current"}


# --- the routes ------------------------------------------------------------------------------------------------------


def test_status_batch_follows_the_order_and_tells_stale_nodes(chain: Chain) -> None:
    c = chain.c
    assert current(chain.states())
    chain.edit_source(5.0)
    got = chain.states()
    assert got[chain.s0] == "current"
    assert {got[i] for i in (chain.n1, chain.n2, chain.n3, chain.n4, chain.m)} == {"stale"}
    nodes = [chain.node(chain.n4), {"kind": NODE, "id": 987_654}, chain.node(chain.s0)]
    items = c.post(f"{L}/status", json={"nodes": nodes}).json()["items"]
    assert [i["state"] for i in items] == ["stale", "deleted", "current"]  # the order asked; no node is not an error
    assert items[0]["reason"].endswith("изменено: object:stub «Источник»") or "«Источник»" in items[0]["reason"]
    assert c.post(f"{L}/status", json={"nodes": []}).json() == {"items": []}


def test_status_refuses_bad_requests_in_russian(chain: Chain) -> None:
    c = chain.c
    r = c.post(f"{L}/status", json={"nodes": [{"kind": "table", "id": 1}]})
    assert r.status_code == 422 and r.json()["code"] == "lineage_bad_kind" and r.json()["hint"]
    for body, param in (({"nodes": "x"}, "nodes"), ({"nodes": [{"id": 1}]}, "nodes.0.kind"),
                        ({"nodes": [{"kind": "dh_table", "id": "1"}]}, "nodes.0.id"),
                        ({"nodes": [{"kind": "dh_table", "id": True}]}, "nodes.0.id")):  # fmt: skip
        r = c.post(f"{L}/status", json=body)
        assert r.status_code == 422 and r.json()["code"] == "bad_param" and r.json()["param"] == param, r.text
    r = c.post(f"{L}/status", json={"nodes": [{"kind": "dh_table", "id": i} for i in range(5001)]})
    assert r.status_code == 422 and r.json()["code"] == "too_many_nodes"


def test_impact_get_and_post_and_origin(chain: Chain) -> None:
    c = chain.c
    r = c.get(f"{L}/impact", params={"kind": NODE, "id": chain.n1})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["total"] == 4 and got["by_kind"] == {NODE: 4} and not got["truncated"]
    assert [n["depth"] for n in got["nodes"]] == [1, 1, 2, 3] and got["nodes"][0]["name"]
    assert c.post(f"{L}/impact", json={"kind": NODE, "id": chain.n1}).json() == got
    assert c.post(f"{L}/impact", json={"node": chain.node(chain.n1)}).json() == got
    assert c.post(f"{L}/impact", json={"node": chain.node(chain.n4)}).json()["total"] == 0
    r = c.get(f"{L}/impact", params={"kind": NODE, "id": 424_242})
    assert r.status_code == 404 and r.json()["code"] == "lineage_node_not_found" and r.json()["hint"]
    chain.edit_source(7.0)
    r = c.get(f"{L}/origin", params={"kind": NODE, "id": chain.n2})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["node"]["state"] == "stale" and [i["id"] for i in out["inputs"]] == [chain.n1]
    assert out["inputs"][0]["state"] == "stale" and out["inputs"][0]["name"] == "Узел 1"
    assert c.get(f"{L}/origin", params={"kind": NODE, "id": 424_242}).status_code == 404


def test_impact_of_an_object_nothing_is_built_from_in_an_empty_graph(stubs, pclient) -> None:  # type: ignore[no-untyped-def]
    """No rows in `lineage` (a project saved before the graph): the first `impact` makes the index from what the
    results keep (`reindex`) and answers; here there is nothing to index."""
    assert pclient.post(API + "/projects", json={"name": "Старый"}).status_code == 200
    engine = projects_of(pclient.core).require_current().engine
    with dialect.write_tx(engine) as conn:
        oid = add(conn, "Одинокий", 1.0)
    r = pclient.get(f"{L}/impact", params={"kind": NODE, "id": oid})
    assert r.status_code == 200 and r.json() == {"total": 0, "by_kind": {}, "nodes": [], "truncated": False}


def test_pin_holds_and_releases_and_refuses(chain: Chain) -> None:
    c = chain.c
    chain.edit_source(5.0)
    r = c.post(f"{L}/pin", json={"node": chain.node(chain.n1), "pinned": True})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "pinned" and r.json()["changed"] is True and "Источник" in r.json()["reason"]
    got = chain.states()
    assert got[chain.n1] == "pinned" and got[chain.n2] == "current"  # what is built from a pinned node is held (№ 62)
    r = c.post(f"{L}/pin", json={"node": chain.node(chain.n1), "pinned": False})
    assert r.json()["state"] == "stale"
    r = c.post(f"{L}/pin", json={"kind": NODE, "id": 424_242, "pinned": True})
    assert r.status_code == 404 and r.json()["code"] == "lineage_node_not_found"
    r = c.post(f"{L}/pin", json={"node": chain.node(chain.n1), "pinned": "да"})
    assert r.status_code == 422 and r.json()["param"] == "pinned"


def test_rebuild_refuses_a_pinned_unsupported_missing_and_wrong_node(chain: Chain) -> None:
    c = chain.c
    chain.edit_source(5.0)
    c.post(f"{L}/pin", json={"node": chain.node(chain.n1), "pinned": True})
    r = chain.start()
    assert r.status_code == 409 and r.json()["code"] == "lineage_node_pinned" and r.json()["hint"]
    c.post(f"{L}/pin", json={"node": chain.node(chain.n1), "pinned": False})
    with dialect.write_tx(chain.engine) as conn:  # a kind that has no maker
        trend = add(conn, "Тренд", 1.0, kind="trend")
    r = c.post(f"{L}/rebuild", json={"node": {"kind": "object:trend", "id": trend}})
    assert r.status_code == 422 and r.json()["code"] == "lineage_rebuild_unsupported" and r.json()["hint"]
    r = c.post(f"{L}/rebuild", json={"node": {"kind": NODE, "id": 424_242}})
    assert r.status_code == 404 and r.json()["code"] == "lineage_node_not_found"
    r = c.post(f"{L}/rebuild", json={"node": {"kind": "table", "id": 1}})
    assert r.status_code == 422 and r.json()["code"] == "lineage_bad_kind"
    r = chain.start(with_dependents="да")
    assert r.status_code == 422 and r.json()["param"] == "with_dependents"
    assert not [j for j in c.get(f"{API}/jobs").json()["jobs"] if j["kind"] == "lineage.rebuild"]  # no job started


def test_dry_run_lists_the_order_and_what_is_left_out(chain: Chain) -> None:
    c = chain.c
    with dialect.write_tx(chain.engine) as conn:  # a report of a kind without a maker, built from the node 3
        report = add(conn, "Отчёт", 0.0, kind="report_stub")
        lineage.record(conn, Node("object:report_stub", report), [Node(NODE, chain.n3)])
    chain.edit_source(5.0)
    r = chain.start(dry_run=True)
    assert r.status_code == 200, r.text
    plan = r.json()
    names = [i["name"] for i in plan["plan"]]
    assert names[0] == "Узел 1" and sorted(names) == ["Ветка M", "Узел 1", "Узел 2", "Узел 3", "Узел 4"]
    pos = {i["id"]: n for n, i in enumerate(plan["plan"])}
    assert pos[chain.n1] < pos[chain.n2] < pos[chain.n3] < pos[chain.n4] and pos[chain.n1] < pos[chain.m]
    assert all(i["action"] == "rebuild" and i["state"] == "stale" for i in plan["plan"])
    assert [(s["name"], s["state"]) for s in plan["skipped"]] == [("Отчёт", "unsupported")]
    assert "нельзя перестроить" in plan["skipped"][0]["reason"]
    alone = chain.start(dry_run=True, with_dependents=False).json()
    assert [i["id"] for i in alone["plan"]] == [chain.n1] and alone["skipped"] == []
    assert not [j for j in c.get(f"{API}/jobs").json()["jobs"] if j["kind"] == "lineage.rebuild"]  # nothing started


def test_makers_of_the_modules_answer_a_missing_object_with_its_own_404(stubs, pclient) -> None:  # type: ignore[no-untyped-def]
    """Every built-in maker calls the «Перестроить» route of its module (no copy of its rules): a missing object is
    the 404 of that module, not an error of the glue."""
    from bf_core.api.errors import ApiError
    from bf_core.lineage import rebuild

    assert pclient.post(API + "/projects", json={"name": "Мастера"}).status_code == 200
    svc = projects_of(pclient.core)
    kinds = ["dh_result", "object:variogram_model", "object:qaqc_data", "object:qaqc_report", "object:block_model"]
    assert set(kinds) | {"dh_table", "bm_field"} <= set(rebuild.MAKERS)
    for kind in kinds:
        with pytest.raises(ApiError) as e:
            rebuild.prepare_step(svc, Node(kind, 999_999), {})
        assert e.value.status == 404 and e.value.hint, (kind, e.value.code)


# --- the job ---------------------------------------------------------------------------------------------------------


def test_td35_5_rebuild_makes_the_chain_current_and_equal_to_a_calculation_from_scratch(chain: Chain) -> None:
    c = chain.c
    chain.edit_source(5.0)
    assert chain.states()[chain.n4] == "stale"
    r = chain.start()
    assert r.status_code == 202, r.text
    started = r.json()
    assert started["job_id"] == started["id"] and len(started["plan"]) == 5 and started["skipped"] == []
    job = wait_job(c, started["source"], started["id"], timeout=120)
    assert job["status"] == "done", job
    res = job["result"]
    assert [n["outcome"] for n in res["nodes"]].count("done") == 5 and res["done"] == 5
    assert {n["state_after"] for n in res["nodes"]} == {"current"}
    events = {e[0]: e[1] for e in res["events"]}
    assert {n["id"] for n in events["lineage.changed"]["nodes"]} == {i["id"] for i in started["plan"]}
    assert current(chain.states())
    got = {name: chain.meta(oid)["value"] for name, oid in (("n1", chain.n1), ("n2", chain.n2), ("n3", chain.n3),
                                                            ("n4", chain.n4), ("m", chain.m))}  # fmt: skip
    assert got == chain_values(5.0) and got["n4"] == 95.0  # bit for bit: 2·(2·(2·(2·5+1)+1)+1)+1
    assert [chain.meta(i)["builds"] for i in (chain.n1, chain.n2, chain.n3, chain.n4, chain.m)] == [1] * 5
    # nothing is stale now: a second rebuild of the head runs the head only
    again = chain.start().json()
    assert [i["id"] for i in again["plan"]] == [chain.n1]
    wait_job(c, again["source"], again["id"], timeout=60)


def test_td35_5_cancel_on_the_second_node_does_not_start_the_third_and_the_fourth(chain: Chain) -> None:
    c = chain.c
    chain.edit_source(5.0)
    chain.mark(chain.n2, hold=True)  # the job of the node 2 waits for a cancel
    started = chain.start().json()
    order = [i["id"] for i in started["plan"]]
    assert order.index(chain.n2) < order.index(chain.n3) < order.index(chain.n4)
    until(lambda: chain.meta(chain.n2).get("started") is True, 60)
    r = c.post(f"{API}/jobs/{started['source']}/{started['id']}/cancel")
    assert r.status_code == 200, r.text
    job = wait_job(c, started["source"], started["id"], timeout=60)
    assert job["status"] == "cancelled", job
    assert chain.meta(chain.n1)["builds"] == 1  # the first node was finished and stays
    for oid in (chain.n3, chain.n4):  # the third and the fourth were not started
        assert chain.meta(oid).get("started") is None and chain.meta(oid)["builds"] == 0
    assert chain.meta(chain.n2)["builds"] == 0
    got = chain.states()
    assert got[chain.n1] == "current" and {got[i] for i in (chain.n2, chain.n3, chain.n4)} == {"stale"}


def test_a_failed_node_stops_what_is_built_from_it_and_the_others_go_on(chain: Chain) -> None:
    c = chain.c
    chain.edit_source(5.0)
    chain.mark(chain.n2, fail=True)
    started = chain.start().json()
    job = wait_job(c, started["source"], started["id"], timeout=120)
    assert job["status"] == "failed", job
    err = job["error"]
    assert err["code"] == "lineage_rebuild_failed" and "«Узел 2»" in err["message"]
    assert "Заглушка не смогла" in err["message"]
    assert "Не запущено зависимых: 2" in err["message"] and err["hint"]
    assert chain.meta(chain.n1)["builds"] == 1 and chain.meta(chain.m)["builds"] == 1  # the branch M is independent
    for oid in (chain.n3, chain.n4):
        assert chain.meta(oid).get("started") is None  # built from the failed node: not started
    got = chain.states()
    assert got[chain.n1] == got[chain.m] == "current"
    assert {got[i] for i in (chain.n2, chain.n3, chain.n4)} == {"stale"}


def test_rebuild_without_dependents_runs_the_node_only(chain: Chain) -> None:
    c = chain.c
    chain.edit_source(5.0)
    started = chain.start(with_dependents=False).json()
    assert [i["id"] for i in started["plan"]] == [chain.n1]
    job = wait_job(c, started["source"], started["id"], timeout=60)
    assert job["status"] == "done" and job["title"].startswith("Перестроить: ")
    got = chain.states()
    assert got[chain.n1] == "current" and {got[i] for i in (chain.n2, chain.n3, chain.n4, chain.m)} == {"stale"}


def test_a_node_that_cannot_be_rebuilt_is_left_with_what_is_built_from_it(chain: Chain) -> None:
    c = chain.c
    with dialect.write_tx(chain.engine) as conn:
        report = add(conn, "Отчёт", 0.0, kind="report_stub")
        lineage.record(conn, Node("object:report_stub", report), [Node(NODE, chain.n3)])
        tail = add(conn, "Хвост", 0.0, kind="report_stub")
        lineage.record(conn, Node("object:report_stub", tail), [Node("object:report_stub", report)])
    chain.edit_source(5.0)
    started = chain.start().json()
    assert sorted(s["name"] for s in started["skipped"]) == ["Отчёт", "Хвост"]
    job = wait_job(c, started["source"], started["id"], timeout=120)
    assert job["status"] == "done", job
    got = chain.states()
    assert current(got)
    r = c.post(f"{L}/status", json={"nodes": [{"kind": "object:report_stub", "id": report}]}).json()["items"][0]
    assert r["state"] == "stale"  # left as it was: the user rebuilds it where it is made


# --- real results: composites → a model of variography --------------------------------------------------------

REAL_MODULES = ("bf_core.lineage.job", "bf_core.composites.jobs", "bf_core.geostat.variogram.jobs",
                "bf_core.geostat.variogram.jobs_map")  # fmt: skip


@pytest.fixture()
def real_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    from bf_core.db import schema
    from bf_core.geostat.variogram import store as ms

    if not {ms.VARIO, ms.NSCORE} <= schema.BLOB_ROLES:  # until the line of the orchestrator (16.x)
        monkeypatch.setattr(schema, "BLOB_ROLES", schema.BLOB_ROLES | {ms.VARIO, ms.NSCORE})
    register(monkeypatch, REAL_MODULES)


def table_values(c: Any, set_id: int, name: str) -> list[list[Any]]:
    from tests.test_composites_jobs import rows_of, tables

    got = rows_of(c, set_id, tables(c, set_id)[name]["table"])
    keys = [col["key"] for col in got["columns"]]
    return [[row[k] for k in keys] for row in got["rows"]]


def test_td35_5_real_chain_equals_a_calculation_from_scratch(real_modules, pclient) -> None:  # type: ignore[no-untyped-def]
    """The samples → composites (a result and its table) → a model of variography. An edit of one value makes the
    three «устарел»; «Перестроить с зависимыми» of the result: the composites are computed again (a new result, a new
    table), the model takes the points of the new table; all three are «актуален» and the table and the points are
    those a calculation from scratch gives (the same job on the same samples under another name)."""
    from bf_core.geostat.variogram import jobs as vj
    from tests.test_composites_jobs import rows_of, tables
    from tests.test_vario_api import GS, MODEL, fill

    c = pclient
    assert c.post(API + "/projects", json={"name": "Цепочка"}).status_code == 200
    project = projects_of(c.core).require_current()
    set_id = fill(project.engine)
    src = tables(c, set_id)["Пробы"]["table"]
    run = {"set_id": set_id, "table": src, "fields": ["AU"], "method": "length", "length": 2}
    r = c.post(f"{API}/composites/run", json=run)
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=300)
    assert job["status"] == "done", job
    result_id = job["result"]["result_id"]
    exp = {"set_id": set_id, "table": "Композиты 2 м", "field": "AU", "axes": {"ang1": 0, "ang2": -90, "ang3": 0},
           "nlag": 8, "min_pairs": 5}  # fmt: skip
    out = vj.run_experimental(project.engine, project.folder, {"body": exp})
    r = c.post(GS + "/models", json={"model": MODEL, "exp_key": out["key"], "name": "Модель AU"})
    assert r.status_code == 200, r.text
    model_id = r.json()["id"]

    def watched() -> list[dict[str, Any]]:
        comp = int(tables(c, set_id)["Композиты 2 м"]["table"])
        results = c.get(f"{API}/composites/results?set_id={set_id}").json()["results"]
        nodes = [{"kind": "dh_result", "id": results[0]["id"]}, {"kind": "dh_table", "id": comp},
                 {"kind": "object:variogram_model", "id": model_id}]  # fmt: skip
        return c.post(f"{L}/status", json={"nodes": nodes}).json()["items"]

    assert [i["state"] for i in watched()] == ["current"] * 3
    rows = rows_of(c, set_id, src)  # an edit of one value of the samples
    au = next(col["key"] for col in rows["columns"] if col["name"] == "AU")
    r = c.patch(f"{API}/drillholes/sets/{set_id}/tables/{src}/rows/{rows['rows'][0]['id']}",
                json={"column": au, "value": 99.5})  # fmt: skip
    assert r.status_code == 200, r.text
    before = watched()
    assert [i["state"] for i in before] == ["stale"] * 3 and all(i["reason"] for i in before)

    r = c.post(f"{L}/rebuild", json={"node": {"kind": "dh_result", "id": result_id}, "dry_run": True})
    plan = r.json()["plan"]
    assert [(i["kind"], i["action"]) for i in plan] == [("dh_result", "rebuild"), ("dh_table", "covered"),
                                                        ("object:variogram_model", "rebuild")]  # fmt: skip
    r = c.post(f"{L}/rebuild", json={"node": {"kind": "dh_result", "id": result_id}})
    assert r.status_code == 202, r.text
    started = r.json()
    job = wait_job(c, started["source"], started["id"], timeout=600)
    assert job["status"] == "done", job
    nodes = job["result"]["nodes"]
    assert [n["outcome"] for n in nodes] == ["done", "covered", "done"]
    assert [n.get("job_kind") for n in nodes] == ["composite.run", None, "variography.rebuild"]
    assert [n["state_after"] for n in nodes] == ["current", "replaced", "current"]  # the table is new
    assert [i["state"] for i in watched()] == ["current"] * 3

    # a calculation from scratch: the same job on the same samples under another name
    r = c.post(f"{API}/composites/run", json={**run, "name": "Эталон"})
    assert wait_job(c, r.json()["source"], r.json()["id"], timeout=300)["status"] == "done"
    assert table_values(c, set_id, "Композиты 2 м") == table_values(c, set_id, "Эталон")
    ref = vj.run_experimental(project.engine, project.folder, {"body": {**exp, "table": "Эталон"}})
    got = c.get(f"{GS}/models/{model_id}").json()
    assert got["points"]["directions"] == ref["directions"]  # (the new table changed the set: `stale` is not asked)
