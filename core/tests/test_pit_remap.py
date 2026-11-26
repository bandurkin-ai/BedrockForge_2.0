"""Ids inside the `meta` of a pit scenario on a `.bfggis` import (подшаг 21.10; ADR 0028 решение 24; ADR 0025 §11):
the model, the terrain, the filter, the wireframes of the restriction and of the rock rules, the DTMs the run made, the
inputs, the draft of the parameters, the id of the scenario in the fields PIT / ORE; an id the archive does not have is
`DEAD_ID` (a leaf of the rules — null); `params_hash` made again; the cycle export → import keeps a current scenario
current. The line of `archive/importer.py` is the orchestrator's: until it is there, the test calls `remap_pit`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core import lineage
from bf_core.archive import importer
from bf_core.blocks import meta as block_meta
from bf_core.blocks import schema as block_schema
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects
from bf_core.mine import dtm_write as dw
from bf_core.mine import remap, spec
from bf_core.mine import store as st
from bf_core.mine import store_data as sd
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests import test_vario_store as vs
from tests.estutil import W, fill_world, world
from tests.pitutil import AU_EST, BASE, ROCKS_EST, est_field, make, pit_field, rd, shells_of
from tests.test_import_job import wait_job
from tests.test_model_store import solid
from tests.test_vario_store import API

vc = vs.vc  # the core with an open project and the set of `test_vario_store`
NODE = "object:pit_scenario"
OBJS = {5: 50, 4: 40, 7: 70, 9: 90, 8: 80, 3: 30, 12: 120, 17: 170}


@pytest.fixture(autouse=True)
def pit_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """`FIELD_OPS` += "pit" is a line of the orchestrator (`blocks/schema.py`): until it is there, the test puts it."""
    for mod in (block_schema, block_meta):
        if "pit" not in mod.FIELD_OPS:
            monkeypatch.setattr(mod, "FIELD_OPS", (*mod.FIELD_OPS, "pit"))


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


WIRE = {"wireframe": {"id": 9, "side": "inside", "by": "frac"}}
RULES = {"by": {"kind": "rules", "rules": [{"name": "Зона", "rules": {"wireframe": {"id": 8, "side": "inside",
                                                                                   "by": "frac"}}}]},
         "types": [{"name": "Зона", "c_p": 20.0, "rec": {"AU": 0.9}}]}  # fmt: skip


def a_params(**over: Any) -> dict[str, Any]:
    body = {**BASE, "model_id": 5, "terrain_id": 4, "filter_id": 7, "restrict": WIRE, "rocks": RULES, **over}
    return spec.parse_params(body).canonical()


def a_meta(**over: Any) -> dict[str, Any]:
    params = a_params()
    draft = a_params(mining={"c_m0": 4.0})
    return {"v": 1, "algo": "pit-1", "params": params, "params_hash": spec.parse_params(params).params_hash(),
            "draft": {"params": draft, "hash": spec.parse_params(draft).params_hash()},
            "model": {"id": 5, "name": "М", "cells_sha": "ab", "grid": {}}, "fields": {"AU": "cells|None"},
            "inputs": [{"kind": "object:block_model", "id": 5, "fp": "ab", "name": "М"},
                       {"kind": "bm_field", "id": "5:AU", "fp": "cells|None", "name": "М.AU"},
                       {"kind": "object:filter", "id": 7, "fp": "1", "name": "Ф"},
                       {"kind": "wireframe", "id": 4, "fp": "1", "name": "Рельеф"},
                       {"kind": "wireframe", "id": 9, "fp": "1", "name": "К"}],
            "outputs": {"fields": ["PIT", "ORE"], "wireframes": [12, 17]}, "shells": [{"k": 1, "rf": 1.0}],
            "totals": None, "control": {"closed": True}, "state": "ready", **over}  # fmt: skip


def test_remap_renumbers_every_id_of_the_meta() -> None:
    meta = a_meta()
    new = remap.remap_meta(meta, OBJS.get)
    p = new["params"]
    assert (p["model_id"], p["terrain_id"], p["filter_id"]) == (50, 40, 70)
    assert p["restrict"]["wireframe"]["id"] == 90 and new["model"]["id"] == 50
    assert p["rocks"]["by"]["rules"][0]["rules"]["wireframe"]["id"] == 80  # the rock rules read wireframes too
    assert [i["id"] for i in new["inputs"]] == [50, "50:AU", 70, 40, 90]
    assert [i["fp"] for i in new["inputs"]] == [i["fp"] for i in meta["inputs"]]  # the states stay
    assert new["outputs"] == {"fields": ["PIT", "ORE"], "wireframes": [120, 170]}  # the DTMs and the solid
    for key in ("fields", "shells", "totals", "control"):
        assert new[key] == meta[key]
    assert new["params_hash"] != meta["params_hash"] and new["params_hash"] == spec.parse_params(p).params_hash()
    d = new["draft"]  # the parameters waiting for a run are renumbered with their hash
    assert d["params"]["model_id"] == 50 and d["hash"] == spec.parse_params(d["params"]).params_hash()
    assert d["hash"] != meta["draft"]["hash"] and new["params"]["calendar"] == meta["params"]["calendar"]
    assert meta["params"]["model_id"] == 5 and meta["outputs"]["wireframes"] == [12, 17]  # the old meta is not touched


def test_a_reference_the_archive_did_not_have_is_dead_not_another_object() -> None:
    new = remap.remap_meta(a_meta(), {5: 50, 12: 120}.get)  # the filter, the wireframes, a DTM are not in the archive
    p = new["params"]
    assert p["filter_id"] == p["terrain_id"] == remap.DEAD_ID
    assert p["restrict"]["wireframe"]["id"] is None  # a leaf of the rules: «недоступен», as in a filter
    assert p["rocks"]["by"]["rules"][0]["rules"]["wireframe"]["id"] is None
    assert [i["id"] for i in new["inputs"]] == [50, "50:AU", remap.DEAD_ID, remap.DEAD_ID, remap.DEAD_ID]
    assert new["outputs"]["wireframes"] == [120, remap.DEAD_ID]
    gone = remap.remap_meta(a_meta(), {}.get)
    assert gone["params"]["model_id"] == gone["model"]["id"] == remap.DEAD_ID
    assert gone["inputs"][1]["id"].endswith(":AU")
    spec.parse_params(gone["params"])  # still reads: the scenario shows «источник удалён»
    none = remap.remap_meta(a_meta(params=a_params(terrain_id=None, filter_id=None, restrict=None, rocks=None)),
                            OBJS.get)  # fmt: skip
    assert none["params"]["filter_id"] is None and none["params"]["restrict"] is None
    assert none["params"]["terrain_id"] is None and none["draft"]["params"]["terrain_id"] == 40


def test_a_scenario_without_a_model_a_result_or_a_draft_is_left_as_it_is() -> None:
    fresh = {"v": 1, "algo": "pit-1", "params": spec.parse_params({}).canonical(), "state": "ready", "model": {}}
    new = remap.remap_meta(fresh, OBJS.get)
    assert new["params"] == fresh["params"] and new["params_hash"] == spec.parse_params(fresh["params"]).params_hash()
    nomodel = {**fresh, "model": {"id": None, "name": None}}
    assert remap.remap_meta(nomodel, OBJS.get)["model"] == {"id": None, "name": None}  # null stays null, not DEAD_ID


def test_a_mark_running_is_not_carried_over() -> None:
    new = remap.remap_meta(a_meta(state="building", run_job=12), OBJS.get)
    assert new["state"] == "ready" and "run_job" not in new  # its job is not a job of the new project


def test_the_scenario_of_a_field_is_renumbered_and_a_lost_one_is_dead() -> None:
    meta = {"fields": [{"name": "AU", "source": {"op": "import"}},
                       {"name": "PIT", "source": {"op": "pit", "scenario_id": 17}},
                       {"name": "ORE", "source": {"op": "pit", "scenario_id": 18}}], "rows": 5}  # fmt: skip
    new = remap.remap_fields(meta, OBJS.get)
    assert [f["source"].get("scenario_id") for f in new["fields"]] == [None, 170, remap.DEAD_ID]
    assert new["rows"] == 5 and meta["fields"][1]["source"]["scenario_id"] == 17


def test_the_params_of_a_field_and_the_scenario_of_a_dtm_are_renumbered() -> None:
    """21.21б-2: `source.params` of PIT / ORE name the model, the relief, the filter; the DTMs and solids a scenario
    made name the scenario in `meta.source` — `dtm_write.outputs_of` finds them by it."""
    src = {"op": "pit", "scenario_id": 17, "params": a_params(), "inputs": ["ab"], "algo": "pit-1"}
    new = remap.remap_fields({"fields": [{"name": "PIT", "source": src}]}, OBJS.get)["fields"][0]["source"]
    assert new["scenario_id"] == 170 and new["inputs"] == ["ab"]
    p = new["params"]
    assert (p["model_id"], p["terrain_id"], p["filter_id"]) == (50, 40, 70) and p["restrict"]["wireframe"]["id"] == 90
    assert src["params"]["model_id"] == 5  # the old dict is not touched
    for op in ("pit_surface", "pit_solid"):
        meta = {"color": "#fff", "source": {"op": op, "scenario_id": 17, "shell": 2, "scenario": "С"}}
        got = remap.remap_wireframe(meta, OBJS.get)
        assert got["source"] == {**meta["source"], "scenario_id": 170} and got["color"] == "#fff"
        assert remap.remap_wireframe(meta, {}.get)["source"]["scenario_id"] == remap.DEAD_ID  # not in the archive
    for other in ({"source": {"op": "import", "scenario_id": 17}}, {"source": {"op": "pit_surface"}}, {}):
        assert remap.remap_wireframe(other, OBJS.get) == other  # the wireframes of other makers stay as they are


def test_remap_rows(w: W) -> None:
    ident = of_estimate_scenario(w)
    pit_field(w, ident)
    with dialect.write_tx(w.project.engine) as conn:
        assert remap.remap_pit(conn, {w.model: w.model + 100, ident: ident + 100}) == 2  # the scenario and the model
    got = rd(w, st.info, ident)
    assert got["params"]["model_id"] == got["model"]["id"] == w.model + 100
    assert [i["id"] for i in got["meta"]["inputs"]][:2] == [w.model + 100, f"{w.model + 100}:AU_EST"]
    assert got["params_hash"] == spec.parse_params(got["params"]).params_hash()
    assert got["meta"]["params_hash"] == got["params_hash"]
    fields = rd(w, lambda c: c.execute(sa.select(objects.c.meta).where(objects.c.id == w.model)).scalar())["fields"]
    assert [f["source"].get("scenario_id") for f in fields if f["name"] == "PIT"] == [ident + 100]
    with dialect.write_tx(w.project.engine) as conn:  # an id the map has not is DEAD_ID: that is a change too
        assert remap.remap_pit(conn, {}) == 2
    with dialect.write_tx(w.project.engine) as conn:
        assert remap.remap_pit(conn, {}) == 0  # DEAD_ID stays DEAD_ID: nothing is touched again


def of_estimate_scenario(w: W, name: str = "Сценарий ЮЗ") -> int:
    est_field(w)
    return make(w, name, elements=AU_EST, rocks=ROCKS_EST)


def test_bfggis_roundtrip_renumbers_the_scenarios_and_keeps_them_current(  # type: ignore[no-untyped-def]
        vc, tmp_path: Path) -> None:
    w = fill_world(vc.project, vc.set_id, vc.a, vc.b)
    terrain, pit = solid(w, "Рельеф"), solid(w, "Карьер")
    dtm = solid(w, "Сценарий — RF 1,00")
    restrict = {"wireframe": {"id": pit, "side": "inside", "by": "frac"}}
    est_field(w)
    ident = make(w, "Сценарий ЮЗ", wireframes=(dtm,), elements=AU_EST, rocks=ROCKS_EST, terrain_id=terrain,
                 filter_id=w.flt, restrict=restrict)  # fmt: skip
    other = make(w, "Второй")
    pit_field(w, ident)
    src = {"op": dw.OP_SURFACE, "scenario_id": ident, "shell": 1, "rf": 1.0, "version": 2, "scenario": "Сценарий ЮЗ"}
    with dialect.write_tx(w.project.engine) as conn:  # the DTM as `pit.surfaces` writes it
        meta = dict(conn.execute(sa.select(objects.c.meta).where(objects.c.id == dtm)).scalar() or {})
        conn.execute(sa.update(objects).where(objects.c.id == dtm).values(meta={**meta, "source": src}))
        lineage.record(conn, lineage.Node("wireframe", dtm), [lineage.Node(NODE, ident)])
    assert rd(w, dw.outputs_of, ident, dw.OP_SURFACE) == {1: (dtm, 1)}
    before = rd(w, st.info, ident, store=w.store)
    assert before["graph"]["stale"] is False
    shas = rd(w, st.data_sha, ident)
    r = vc.post(API + "/projects/export", json={"path": str(tmp_path / "Карьер")}).json()
    done = wait_job(vc, r["source"], r["id"])
    assert done["status"] == "done", done
    r = vc.post(API + "/projects/import", json={"path": done["result"]["path"], "mode": "copy"}).json()
    done = wait_job(vc, r["source"], r["id"])
    assert done["status"] == "done", done
    maps = done["result"]["id_maps"]
    objs = {int(k): int(v) for k, v in maps["objects"].items()}
    assert vc.post(f"{API}/projects/{done['result']['uid']}/open").status_code == 200
    project = projects_of(vc.core).require_current()
    n = W(project, BlobStore(project.folder), objs[vc.set_id], int(maps["dh_tables"][str(vc.a)]),
          int(maps["dh_tables"][str(vc.b)]), objs[w.model], objs[w.vz], objs[w.trend], objs[w.flt])  # fmt: skip
    assert objs != {k: k for k in objs}, objs  # the import renumbered the objects: the test would not see the bug else
    if not hasattr(importer, "remap_pit"):  # the line of the orchestrator is not in `archive/importer.py` yet
        with dialect.write_tx(project.engine) as conn:
            remap.remap_pit(conn, objs)
    got = rd(n, st.info, objs[ident], store=n.store)
    assert [r["id"] for r in rd(n, st.list_scenarios)] == sorted([objs[ident], objs[other]], key=lambda i: (  # by name
        "Второй" if i == objs[other] else "Сценарий ЮЗ"))  # fmt: skip
    p = got["params"]
    assert (p["model_id"], p["terrain_id"], p["filter_id"]) == (objs[w.model], objs[terrain], objs[w.flt])
    assert p["restrict"]["wireframe"]["id"] == objs[pit] and got["model"]["id"] == objs[w.model]
    assert got["params_hash"] == spec.parse_params(p).params_hash() and got["params_hash"] != before["params_hash"]
    ids = [i["id"] for i in got["meta"]["inputs"]]
    assert ids[:2] == [objs[w.model], f"{objs[w.model]}:AU_EST"] and objs[w.flt] in ids and objs[pit] in ids
    assert objs[terrain] in ids
    assert [i["fp"] for i in got["meta"]["inputs"]] == [i["fp"] for i in before["meta"]["inputs"]]  # states came
    assert got["meta"]["outputs"]["wireframes"] == [objs[dtm]]
    # the DTM of the shell is found as an output of the scenario of the new project: a second «Поверхности» replaces it
    # in place (a copy is made when `outputs_of` does not find it) — `meta.source.scenario_id` is renumbered too
    assert rd(n, dw.outputs_of, objs[ident], dw.OP_SURFACE) == {1: (objs[dtm], 1)}
    dtm_meta = rd(n, lambda c: c.execute(sa.select(objects.c.meta).where(objects.c.id == objs[dtm])).scalar())
    assert dtm_meta["source"] == {**src, "scenario_id": objs[ident]}
    # the file came bit for bit, the tables read the same, and the scenario is as current as it was
    assert rd(n, st.data_sha, objs[ident]) == shas
    assert sd.table_shells(rd(n, st.read_data, n.store, objs[ident])) == shells_of()
    assert rd(n, st.read_control, n.store, objs[ident]) is not None and rd(n, st.read_zgrid, n.store, objs[ident])
    assert got["graph"] == {"stale": False, "stale_reason": None, "source_lost": False, "pinned": False}, got["graph"]
    node = lineage.Node(NODE, objs[ident])
    with dialect.read_tx(project.engine) as conn:
        assert lineage.fingerprint(conn, node) == rd(w, lineage.fingerprint, lineage.Node(NODE, ident))
        refs = lineage.store.inputs_of(conn, [node])[node]
        assert {r.node.id for r in refs if r.node.kind == "bm_field"} == {objs[w.model]}
        assert lineage.status(conn, [node])[node].state == "current"
        fields = conn.execute(sa.select(objects.c.meta).where(objects.c.id == objs[w.model])).scalar()["fields"]
    assert [f["source"]["scenario_id"] for f in fields if f["name"] == "PIT"] == [objs[ident]]  # the maker of PIT
    pit_node = lineage.Node("bm_field", objs[w.model], "pit")
    with dialect.read_tx(project.engine) as conn:  # the field PIT is built from the scenario of the new project
        assert [r.node for r in lineage.store.inputs_of(conn, [pit_node])[pit_node]] == [node]
        assert lineage.status(conn, [pit_node], store=n.store)[pit_node].state == "current"
    # the new project knows its own chain: the table of the samples edited there → the scenario and PIT are stale
    with dialect.write_tx(project.engine) as conn:
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == n.a).values(rev=dh_tables.c.rev + 1))
    after = rd(n, st.info, objs[ident], store=n.store)
    assert after["graph"]["stale"] is True and "таблица" in after["graph"]["stale_reason"]
    with dialect.read_tx(project.engine) as conn:
        assert lineage.status(conn, [pit_node], store=n.store)[pit_node].state == "stale"
    assert rd(n, st.info, objs[other], store=n.store)["graph"]["stale"] is False
