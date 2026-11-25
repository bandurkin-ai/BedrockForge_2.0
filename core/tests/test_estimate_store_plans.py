"""The plans of the estimation, the graph and the cycle `.bfggis` of the stored rows (подшаг 18.11; ADR 0024 §2,
решение 10; ADR 0025; e18-api.md §2.2, §4): the compatibility of the rows of one prefix, a row deleted leaves its
plans, the hook `lineage.record`, the ids renumbered by `geostat/remap.py` and the states kept, the same on
SQLite and PostgreSQL (`-m db`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.blocks.filters import store as fst
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects
from bf_core.geostat import remap
from bf_core.geostat import trend_store as ts
from bf_core.geostat.estimate import spec as es
from bf_core.geostat.estimate import store as st
from bf_core.geostat.estimate import store_base as sb
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests import test_vario_store as vs
from tests.estutil import W, fill_world, get, make, put, rd, refused, world
from tests.test_import_job import wait_job
from tests.test_vario_store import API

vc = vs.vc  # the core with an open project and the set of `test_vario_store`


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


# --- the plans ------------------------------------------------------------------------------------------------------


def test_plans_compatibility_and_a_row_deleted_leaves_its_plans(w: W) -> None:
    oxide = make(w, name="Окисл")["id"]
    primary = make(w, name="Перв", target={"domain_field": "DOM", "label": "порода"})["id"]  # one prefix, other domain
    twin = make(w, name="Двойник", method={"kind": "nn"}, variogram=None, search={"r1": 50},
                outputs={"prefix": "AU_OK_"})["id"]  # the same prefix, another method  # fmt: skip
    same = make(w, name="Тот же домен", disc=[2, 2, 2])["id"]
    free = make(w, name="Свой префикс", outputs={"prefix": "AUZ_"})["id"]

    def plan(*items: int, **over: Any) -> dict[str, Any]:
        with dialect.write_tx(w.project.engine) as conn:
            return st.plan_create(conn, es.parse_plan({"model_id": w.model, "items": list(items), **over}))

    created = plan(oxide, primary, free)
    assert created == {"id": created["id"], "name": "План · Модель", "version": 1}
    refused(lambda: plan(oxide, twin), 422, "bad_param", "items.1")  # the same prefix, other method
    refused(lambda: plan(oxide, same), 422, "bad_param", "items.1")  # the same prefix, the same domain
    refused(lambda: plan(oxide, primary, same), 422, "bad_param", "items.2")
    err = refused(lambda: plan(oxide, oxide + 999), 404, "estimation_not_found", "items.1")
    assert "items.1" == err.extra["param"]
    refused(lambda: plan(oxide, name="План · Модель"), 409, "name_taken", "name")
    assert plan(oxide)["name"] == "План · Модель (2)"
    refused(lambda: plan(oxide, model_id=w.model + 99), 404, "block_model_not_found", "model_id")
    with dialect.read_tx(w.project.engine) as conn:
        got = st.plan_get(conn, created["id"])
        assert [(i["id"], i["method"], i["prefix"], i["label"], i["stale"]) for i in got["items"]] == [
            (oxide, "ok", "AU_OK_", "руда", False), (primary, "ok", "AU_OK_", "порода", False),
            (free, "ok", "AUZ_", "руда", False)]  # fmt: skip
        assert (got["name"], got["model_id"], got["version"]) == ("План · Модель", w.model, 1)
        assert {"created_at", "updated_at"} <= set(got)
        assert [p["id"] for p in st.plan_list(conn, w.model)] == [created["id"], created["id"] + 1]
        assert st.plan_list(conn, w.model + 1) == []
        rows = {r["id"]: r["plans"] for r in st.list_rows(conn)}
        assert rows[oxide] == [created["id"], created["id"] + 1] and rows[twin] == []
    # PUT of the plan: the version, the items, the name; the model stays
    with dialect.write_tx(w.project.engine) as conn:
        out = st.plan_update(conn, created["id"], {"expected_version": 1, "items": [primary, oxide], "name": "Порядок"})
        assert out == {"id": created["id"], "name": "Порядок", "version": 2}
        refused(lambda: st.plan_update(conn, created["id"], {"expected_version": 1}), 409, "version_conflict")
        refused(lambda: st.plan_update(conn, created["id"], {"items": []}), 422, "bad_param", "expected_version")
        refused(lambda: st.plan_update(conn, created["id"], {"expected_version": 2, "items": [oxide, same, primary]}),
                422, "bad_param", "items.1")  # fmt: skip
        refused(lambda: st.plan_update(conn, created["id"], {"expected_version": 2, "model_id": w.model + 1}),
                422, "bad_param", "model_id")  # fmt: skip
        refused(lambda: st.plan_update(conn, created["id"] + 99, {"expected_version": 1}), 404, "plan_not_found")
    assert rd(w, st.plan_get, created["id"])["items"][0]["id"] == primary
    # the row deleted leaves the plans: their `items` and `version`; the other rows stay
    with dialect.write_tx(w.project.engine) as conn:
        got = st.delete(conn, oxide)  # the plans by name: «План · Модель (2)» goes before «Порядок»
        assert got == {"deleted": oxide, "plans": [created["id"] + 1, created["id"]]}
        refused(lambda: st.delete(conn, oxide), 404, "estimation_not_found")
    with dialect.read_tx(w.project.engine) as conn:
        left = st.plan_get(conn, created["id"])
        assert [i["id"] for i in left["items"]] == [primary] and left["version"] == 3
        assert st.plan_get(conn, created["id"] + 1)["items"] == []
        # the plan deleted leaves its rows
    with dialect.write_tx(w.project.engine) as conn:
        assert st.plan_delete(conn, created["id"]) == {"deleted": created["id"]}
        refused(lambda: st.plan_get(conn, created["id"]), 404, "plan_not_found")
    assert get(w, primary)["plans"] == []
    # `check_plan` of a run by rows: the same model, the same checks, the params named by the caller
    with dialect.read_tx(w.project.engine) as conn:
        assert [s.method.kind for s in st.check_plan(conn, [primary, twin][:1])] == ["ok"]
        refused(lambda: st.check_plan(conn, [primary, twin], None, "estimation_ids"), 422, "bad_param",
                "estimation_ids.1")  # fmt: skip


# --- the hook of the graph (ADR 0025) -------------------------------------------------------------------------------


def test_the_graph_gets_the_row(w: W) -> None:
    """create, update and `record_run` write the inputs of the row into the graph (ADR 0025): the same kinds as
    `meta.inputs`, a field of the model as `bm_field` with its name in `sub`; the row is «актуален»."""
    from bf_core import lineage
    from bf_core.db.schema import lineage as lineage_t

    ident = make(w)["id"]
    put(w, ident, 1, disc=[2, 2, 2])
    with dialect.write_tx(w.project.engine) as conn:
        st.record_run(conn, ident, sb.load_spec(get(w, ident)["meta"]), {"at": "x"}, get(w, ident)["meta"]["inputs"])
    with dialect.read_tx(w.project.engine) as conn:
        rows = conn.execute(sa.select(lineage_t.c.src_kind, lineage_t.c.src_sub).where(
            lineage_t.c.dst_kind == "object:estimation", lineage_t.c.dst_id == ident)).all()  # fmt: skip
        state = lineage.status(conn, [lineage.Node("object:estimation", ident)])
    kinds = sorted({r.src_kind for r in rows})
    assert kinds == sorted({"dh_table", "dh_set", "object:variogram_model", "object:block_model", "bm_field"})
    assert all(r.src_sub == r.src_sub.casefold() for r in rows if r.src_kind == "bm_field")
    assert [s.state for s in state.values()] == ["current"]


# --- the same on SQLite and PostgreSQL ------------------------------------------------------------------------------


@pytest.mark.db
def test_store_on_the_database(db_url: str, tmp_path: Path) -> None:
    """The store without the API on SQLite and PostgreSQL: create, update with the version, refs and 409, a plan,
    delete, «устарел» (`-m db`; BF_TEST_PG)."""
    w = world(tmp_path, db_url)
    try:
        ident = make(w, trend_id=w.trend, target={"domain_field": "DOM", "label": "руда", "filter_id": w.flt})["id"]
        other = make(w, name="Порода", target={"domain_field": "DOM", "label": "порода"})["id"]
        assert put(w, ident, 1, disc=[2, 2, 2])["version"] == 2
        refused(lambda: put(w, ident, 1), 409, "version_conflict")
        with dialect.write_tx(w.project.engine) as conn:
            refused(lambda: ts.delete(conn, w.trend), 409, "in_use")
            plan = st.plan_create(conn, es.parse_plan({"model_id": w.model, "items": [ident, other]}))
        assert get(w, ident)["plans"] == [plan["id"]] and [r["name"] for r in rd(w, st.list_rows)] == [
            "AU · OK · руда", "Порода"]  # fmt: skip
        with dialect.write_tx(w.project.engine) as conn:
            conn.execute(sa.update(dh_tables).where(dh_tables.c.id == w.a).values(rev=dh_tables.c.rev + 1))
        assert get(w, ident)["stale_reason"] == "изменена таблица «Пробы»"
        with dialect.write_tx(w.project.engine) as conn:
            assert st.delete(conn, ident) == {"deleted": ident, "plans": [plan["id"]]}
            ts.delete(conn, w.trend)
            fst.delete(conn, w.flt)
        assert rd(w, st.plan_get, plan["id"])["items"][0]["id"] == other
    finally:
        w.project.engine.dispose()


# --- `.bfggis` ------------------------------------------------------------------------------------------------------


def test_remap_of_the_metas_and_a_lost_reference() -> None:
    meta = {"model_id": 7, "trend_id": 5, "target": {"domain_field": "DOM", "label": "a", "filter_id": 9,
                                                     "rules": {"wireframe": {"id": 3, "side": "inside"}}},
            "samples": {"set_id": 3, "table": "12", "domain": {"table": "14", "field": "ZONE", "values": ["a"]}},
            "variogram": {"model_id": 21, "sha": "ab"}, "variograms": [{"model_id": 21}, {"model_id": 22}],
            "inputs": [{"kind": "dh_table", "id": 12, "fp": "t:1", "name": "П"},
                       {"kind": "dh_set", "id": 3, "fp": "traj:1", "name": "С"},
                       {"kind": "bm_field", "id": "7:ZONE", "fp": "cells:aa", "name": "М · ZONE"},
                       {"kind": "object:trend", "id": 5, "fp": "1", "name": "Т"}],
            "last_run": {"job": "j-1"}}  # fmt: skip
    objs, tabs = {7: 70, 5: 50, 9: 90, 3: 30, 21: 210, 22: 220}, {12: 120, 14: 140}
    new = remap.estimation_meta(meta, objs.get, tabs.get)
    assert (new["model_id"], new["trend_id"], new["target"]["filter_id"]) == (70, 50, 90)
    assert new["target"]["rules"] == {"wireframe": {"id": 30, "side": "inside"}}
    assert new["samples"] == {"set_id": 30, "table": "120", "domain": {"table": "140", "field": "ZONE",
                                                                      "values": ["a"]}}  # fmt: skip
    assert (new["variogram"], [v["model_id"] for v in new["variograms"]]) == ({"model_id": 210, "sha": "ab"},
                                                                              [210, 220])  # fmt: skip
    assert [i["id"] for i in new["inputs"]] == [120, 30, "70:ZONE", 50] and new["last_run"] == {"job": "j-1"}
    assert [i["fp"] for i in new["inputs"]] == [i["fp"] for i in meta["inputs"]]  # the states survive the import
    lost = remap.estimation_meta(meta, {}.get, {}.get)
    assert (lost["model_id"], lost["samples"]["set_id"], lost["variogram"]["model_id"]) == (None, None, None)
    assert (lost["samples"]["table"], lost["samples"]["domain"]["table"]) == ("0", "0")
    assert [i["id"] for i in lost["inputs"]] == [None, None, None, None]
    assert remap.plan_meta({"model_id": 7, "items": [1, 2, 3]}, {7: 70, 1: 10, 3: 30}.get) == {
        "model_id": 70, "items": [10, 30]}  # fmt: skip
    assert remap.plan_meta({"model_id": 7, "items": [1]}, {}.get) == {"model_id": None, "items": []}


def test_a_row_with_lost_ids_reads_and_shows_the_source_gone(w: W) -> None:
    ident = make(w, trend_id=w.trend, target={"domain_field": "DOM", "label": "руда", "filter_id": w.flt})["id"]
    with dialect.write_tx(w.project.engine) as conn:
        meta = get(w, ident)["meta"]
        gone = remap.estimation_meta(meta, {}.get, {}.get)
        conn.execute(sa.update(objects).where(objects.c.id == ident).values(meta=gone))
    got = get(w, ident)
    assert (got["stale"], got["stale_reason"], got["source_lost"]) == (True, "источник удалён", True)
    assert [c["key"] for c in got["columns"]][:2] == ["STATUS", "EST"]  # the parameters still parse
    assert rd(w, st.list_rows)[0]["source_lost"] is True


def test_bfggis_roundtrip_renumbers_the_rows_and_keeps_them_fresh(vc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    w = fill_world(vc.project, vc.set_id, vc.a, vc.b)
    ident = make(w, trend_id=w.trend, target={"domain_field": "DOM", "label": "руда", "filter_id": w.flt},
                 name="Руда")["id"]  # fmt: skip
    other = make(w, name="Порода", target={"domain_field": "DOM", "label": "порода"})["id"]
    with dialect.write_tx(w.project.engine) as conn:
        plan = st.plan_create(conn, es.parse_plan({"model_id": w.model, "items": [other, ident]}))["id"]
    before = {r["name"]: get(w, r["id"]) for r in rd(w, st.list_rows)}
    r = vc.post(API + "/projects/export", json={"path": str(tmp_path / "Оценка")}).json()
    job = wait_job(vc, r["source"], r["id"])
    assert job["status"] == "done", job
    r = vc.post(API + "/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(vc, r["source"], r["id"])
    assert job["status"] == "done", job
    maps = job["result"]["id_maps"]
    objs = {int(k): int(v) for k, v in maps["objects"].items()}
    tabs = {int(k): int(v) for k, v in maps["dh_tables"].items()}
    assert vc.post(f"{API}/projects/{job['result']['uid']}/open").status_code == 200
    project = projects_of(vc.core).require_current()
    n = W(project, BlobStore(project.folder), objs[vc.set_id], tabs[vc.a], tabs[vc.b], objs[w.model], objs[w.vz],
          objs[w.trend], objs[w.flt])  # fmt: skip
    after = {r["name"]: get(n, r["id"]) for r in rd(n, st.list_rows)}
    assert set(after) == set(before) == {"Руда", "Порода"}
    got, old = after["Руда"], before["Руда"]
    assert got["id"] == objs[ident] and (got["stale"], got["source_lost"]) == (False, False), got
    m = got["meta"]
    assert (m["model_id"], m["trend_id"], m["target"]["filter_id"]) == (n.model, n.trend, n.flt)
    assert (m["samples"]["set_id"], m["samples"]["table"], m["samples"]["domain"]["table"]) == (
        n.set_id, str(n.a), str(n.b))  # fmt: skip
    assert m["variogram"]["model_id"] == n.vz and m["variogram"]["sha"] == old["meta"]["variogram"]["sha"]
    assert m["variogram"]["snapshot"] == old["meta"]["variogram"]["snapshot"] and m["origin"] == old["meta"]["origin"]
    assert [(i["kind"], i["fp"], i["name"]) for i in m["inputs"]] == [
        (i["kind"], i["fp"], i["name"]) for i in old["meta"]["inputs"]]  # fmt: skip
    fields = [i["id"] for i in m["inputs"] if i["kind"] == "bm_field"]
    assert fields == [f"{n.model}:DOM", f"{n.model}:AU"]  # the domain and the field of the filter
    with dialect.read_tx(project.engine) as conn:
        assert [(u["id"], u["name"]) for u in ts.usage(conn, n.trend)] == [(got["id"], "Руда")]  # `object_refs` came
        assert [u["id"] for u in fst.usage(conn, n.flt)] == [got["id"]]
        assert [u["id"] for u in st.usage_of_variogram(conn, n.vz)] == [after["Порода"]["id"], got["id"]]
        left = st.plan_get(conn, objs[plan])
        assert [i["id"] for i in left["items"]] == [after["Порода"]["id"], got["id"]]
        refused(lambda: ts.delete(conn, n.trend), 409, "in_use")
    assert after["Руда"]["plans"] == [objs[plan]]
