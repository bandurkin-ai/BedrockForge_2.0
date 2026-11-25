"""`.bfggis` of the implicit models (подшаг 19.11; ADR 0026 решение 26; e19-api.md §2.4): the ids inside the `meta` of a
model renumbered (`model/remap.py`), a reference the archive did not have, the hash of the last run made again, the
outputs of a model named by its new id, and the whole cycle export → import with the files, the outputs, the trend
reference and the states kept."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.archive import importer
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.geostat import trend_store as ts
from bf_core.model import remap
from bf_core.model import store as st
from bf_core.model import store_base as sb
from bf_core.model import store_blobs as bl
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests import test_vario_store as vs
from tests.estutil import W, fill_world, world
from tests.test_import_job import wait_job
from tests.test_model_store import (
    _roles,  # noqa: F401 - the autouse fixture: the roles of the files until `db/schema.py` has them
    get,
    layer,
    make,
    output,
    rd,
    run,
    solid,
    surface,
    write_result,
)
from tests.test_vario_store import API

vc = vs.vc  # the core with an open project and the set of `test_vario_store`


# --- the metas ------------------------------------------------------------------------------------------------------

META: dict[str, Any] = {
    "v": 1, "name": "М", "algo": "rbf-1", "boundary": {"box": None, "wireframe_id": 7, "dtm_id": 8}, "h": 5.0,
    "levels": 2, "kernel": "linear", "kernel_radius": None, "smoothing": {"mode": "manual", "s": None},
    "trend_id": 5, "anisotropy": None, "pou": {"n_patch": 1000, "n_min": 100, "delta": 0.25},
    "data": {"set_id": 3, "table": "12", "field": "ZONE", "d_max": 30.0, "step": 5.0},
    "groups": [{"id": 1, "name": "Д", "type": "domain", "codes": ["a"], "relation": "conformable", "faults": [],
                "params": {}},
               {"id": 2, "name": "Au", "type": "shell", "codes": [], "relation": "conformable", "faults": [],
                "params": {"source": "composites", "result_id": 31, "set_id": None, "table": None, "field": "AU",
                           "transform": "linear", "mode": "numeric", "thresholds": [0.5], "p": None}},
               {"id": 3, "name": "Au2", "type": "shell", "codes": [], "relation": "conformable", "faults": [],
                "params": {"source": "table", "result_id": None, "set_id": 3, "table": "14", "field": "AU",
                           "transform": "linear", "mode": "numeric", "thresholds": [0.5], "p": None}},
               {"id": 4, "name": "Рост", "type": "expand", "codes": [], "relation": "conformable", "faults": [],
                "params": {"wireframe_id": 9, "delta": 5.0}}],
    "manual": [{"layer_id": 20, "role": "contact", "group": 1}],
    "post": {"v_min": 0.0, "simplify": False, "clip": "wireframe", "clip_id": 9},
    "extent": [0, 0, 0, 1, 1, 1], "state": "ready", "error": None, "versions": {"numpy": "2"},
    "outputs": [{"wireframe_id": 50, "kind": "body", "group": 1, "wing": None, "name": "М · Д"}],
    "stats": {"points": 3}, "created_at": "2026-11-25T10:00:00",
    "inputs": [{"kind": "dh_set", "id": 3, "fp": "traj:1", "name": "С"},
               {"kind": "dh_table", "id": 12, "fp": "t:1", "name": "Лит"},
               {"kind": "dh_result", "id": 31, "fp": "sha", "name": "Комп"},
               {"kind": "object:line_layer", "id": 20, "fp": "1", "name": "Слой"},
               {"kind": "object:trend", "id": 5, "fp": "1", "name": "Т"},
               {"kind": "wireframe", "id": 7, "fp": "g", "name": "Гр"}],
    "last_run": {"at": "x", "job": 1, "params": None},
}  # fmt: skip
OBJS = {7: 70, 8: 80, 5: 50, 3: 30, 20: 200, 9: 90, 50: 500}
TABS = {12: 120, 14: 140}
RESULTS = {31: 310}


def test_remap_renumbers_every_id_of_the_meta() -> None:
    new = remap.remap_meta(META, remap.Remap(OBJS, TABS, RESULTS))
    assert new["boundary"] == {"box": None, "wireframe_id": 70, "dtm_id": 80} and new["trend_id"] == 50
    assert new["data"] == {"set_id": 30, "table": "120", "field": "ZONE", "d_max": 30.0, "step": 5.0}
    shell, table_shell, expand = (new["groups"][i]["params"] for i in (1, 2, 3))
    assert (shell["result_id"], shell["set_id"], shell["table"]) == (310, None, None)  # nothing named stays null
    assert (table_shell["set_id"], table_shell["table"]) == (30, "140")
    assert expand == {"wireframe_id": 90, "delta": 5.0}
    assert new["manual"] == [{"layer_id": 200, "role": "contact", "group": 1}] and new["post"]["clip_id"] == 90
    assert [o["wireframe_id"] for o in new["outputs"]] == [500]
    assert [i["id"] for i in new["inputs"]] == [30, 120, 310, 200, 50, 70]
    assert [i["fp"] for i in new["inputs"]] == [i["fp"] for i in META["inputs"]]  # the states survive the import
    assert [g["id"] for g in new["groups"]] == [1, 2, 3, 4] and new["manual"][0]["group"] == 1  # the model's own
    assert META["boundary"]["wireframe_id"] == 7  # the source is not touched


def test_a_lost_reference_is_a_dead_id_and_the_model_still_parses() -> None:
    lost = remap.remap_meta(META, remap.Remap({}, {}, {}))
    dead = sb.DEAD_ID
    assert (lost["boundary"]["wireframe_id"], lost["boundary"]["dtm_id"], lost["trend_id"]) == (dead, dead, dead)
    assert (lost["data"]["set_id"], lost["data"]["table"]) == (dead, sb.DEAD_TABLE)
    assert lost["groups"][1]["params"]["result_id"] == dead and lost["groups"][3]["params"]["wireframe_id"] == dead
    assert [i["id"] for i in lost["inputs"]] == [dead] * 6
    spec = sb.load_spec(lost)  # no `None` where an id is required: the parameters read as they are
    assert spec.boundary.wireframe_id == dead and spec.data is not None and spec.data.table == sb.DEAD_TABLE


def test_the_hash_of_the_last_run_is_made_again_only_for_a_model_that_was_fresh() -> None:
    meta = {**META, "boundary": {"box": None, "wireframe_id": 7, "dtm_id": None}}
    old = sb.params_hash(sb.load_spec(meta))
    ids = remap.Remap(OBJS, TABS, RESULTS)
    fresh = remap.remap_meta({**meta, "last_run": {"job": 1, "params": old}}, ids)
    assert fresh["last_run"]["params"] == sb.params_hash(sb.load_spec(fresh)) != old
    stale = remap.remap_meta({**meta, "last_run": {"job": 1, "params": "0" * 64}}, ids)
    assert stale["last_run"]["params"] == "0" * 64  # «устарела» before, «устарела» after
    never = remap.remap_meta({**meta, "last_run": None}, ids)
    assert never["last_run"] is None


def test_the_hash_of_the_graph_does_not_hold_the_ids() -> None:
    a = sb.graph_fingerprint(sb.load_spec(META).hash_params(), "p", "s")
    renumbered = remap.remap_meta(META, remap.Remap(OBJS, TABS, RESULTS))
    assert sb.graph_fingerprint(sb.load_spec(renumbered).hash_params(), "p", "s") == a
    assert sb.graph_fingerprint(sb.load_spec(META).hash_params(), "p", "other") != a
    assert sb.graph_fingerprint({**sb.load_spec(META).hash_params(), "levels": 3}, "p", "s") != a


def test_remap_rows_and_the_outputs_name_their_new_model(w_rows: W) -> None:
    w = w_rows
    ident = make(w, trend_id=w.trend)["id"]
    out = output(w, ident, "Тело", 1)
    run(w, ident, outputs=[out])
    with dialect.write_tx(w.project.engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == out["wireframe_id"]).values(
            meta={"source": {"op": "implicit", "model_id": 999, "group": 1}}))  # the number of the archive's model
        n = remap.remap_implicit(conn, {w.trend: w.trend + 100, w.set_id: w.set_id + 100}, {w.b: w.b + 100})
    assert n == 2  # the model and its output
    got = get(w, ident)
    assert (got["meta"]["trend_id"], got["meta"]["data"]["set_id"]) == (w.trend + 100, w.set_id + 100)
    src = rd(w, lambda c: c.execute(sa.select(objects.c.meta).where(objects.c.id == out["wireframe_id"])).scalar())
    assert src["source"]["model_id"] == ident  # the parent, not the number of the archive
    assert (got["stale"], got["source_lost"]) == (True, True)  # those ids are not in this project: «источник удалён»


@pytest.fixture()
def w_rows(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


# --- the cycle ------------------------------------------------------------------------------------------------------


def test_bfggis_roundtrip_renumbers_the_models_and_keeps_them_fresh(
    vc, tmp_path: Path  # type: ignore[no-untyped-def]
) -> None:
    from bf_core import lineage
    from bf_core.lineage.adapt_implicit import fingerprint

    # the import runs in the process of the job: the line of the orchestrator must be in `archive/importer.py`
    assert hasattr(importer, "remap_implicit"), "importer.py: from bf_core.model.remap import remap_implicit"
    w = fill_world(vc.project, vc.set_id, vc.a, vc.b)
    wire, expand = solid(w, "Граница"), solid(w, "Расширение", 100.0)
    dtm, lay = surface(w, "ЦТМ", dtm=True), layer(w)
    ident = make(w, name="Руда", boundary={"wireframe_id": wire, "dtm_id": dtm}, trend_id=w.trend,
                 groups=[{"name": "Окисленные", "type": "domain", "codes": ["окисл"]},
                         {"name": "Ручная", "type": "domain", "id": 7},
                         {"name": "Рост", "type": "expand", "params": {"wireframe_id": expand, "delta": 5}}],
                 manual=[{"layer_id": lay, "role": "contact", "group": 7}])["id"]  # fmt: skip
    shas = write_result(w, ident)
    first, second = output(w, ident, "Руда · Окисленные", 1), output(w, ident, "Руда · Прочее", None)
    run(w, ident, outputs=[first, second])
    other = make(w, name="Пустая", groups=[])["id"]
    before = get(w, ident)
    with dialect.read_tx(w.project.engine) as conn:
        fp_before = lineage.fingerprint(conn, lineage.Node("object:implicit_model", ident))
        child_before = lineage.status(conn, [lineage.Node("wireframe", first["wireframe_id"])])
    assert before["stale"] is False and fp_before is not None
    assert [s.state for s in child_before.values()] == ["current"]

    r = vc.post(API + "/projects/export", json={"path": str(tmp_path / "Модель")}).json()
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
    rows = {r["name"]: r for r in rd(n, st.list_rows)}
    assert set(rows) == {"Руда", "Пустая"}
    assert (rows["Руда"]["id"], rows["Пустая"]["id"]) == (objs[ident], objs[other])
    got = get(n, objs[ident])
    assert (got["stale"], got["stale_reason"], got["source_lost"]) == (False, None, False), got
    m = got["meta"]
    assert (m["boundary"]["wireframe_id"], m["boundary"]["dtm_id"], m["trend_id"]) == (objs[wire], objs[dtm], n.trend)
    assert (m["data"]["set_id"], m["data"]["table"]) == (n.set_id, str(n.b))
    assert [g["params"].get("wireframe_id") for g in m["groups"]] == [None, None, objs[expand]]
    assert m["manual"] == [{"layer_id": objs[lay], "role": "contact", "group": 7}]
    assert [(i["kind"], i["fp"], i["name"]) for i in m["inputs"]] == [
        (i["kind"], i["fp"], i["name"]) for i in before["meta"]["inputs"]]  # fmt: skip
    assert [i["id"] for i in m["inputs"] if i["kind"] == "wireframe"] == [objs[wire], objs[expand], objs[dtm]]
    # the files came bit for bit; the outputs are the children of the new model, named by it
    assert rd(n, bl.shas_of, objs[ident]) == shas and len(rd(n, bl.load_fields, n.store, objs[ident])) == 2
    assert [o["wireframe_id"] for o in got["outputs"]] == [objs[first["wireframe_id"]], objs[second["wireframe_id"]]]
    with dialect.read_tx(project.engine) as conn:
        kids = conn.execute(sa.select(objects.c.id, objects.c.parent_id, objects.c.meta).where(
            objects.c.parent_id == objs[ident]).order_by(objects.c.id)).all()  # fmt: skip
        assert [k.id for k in kids] == [o["wireframe_id"] for o in got["outputs"]]
        assert all(k.meta["source"]["model_id"] == objs[ident] for k in kids)
        # the trend is still referenced by the model: 409 `in_use` after the import too
        assert [(u["id"], u["name"]) for u in ts.usage(conn, n.trend)] == [(objs[ident], "Руда")]
        with pytest.raises(Exception) as e:
            ts.delete(conn, n.trend)
        assert getattr(e.value, "code", "") == "in_use"
        # the graph: the same fingerprint (the ids are not in it), the outputs «актуален»
        node = lineage.Node("object:implicit_model", objs[ident])
        assert lineage.fingerprint(conn, node) == fp_before
        assert fingerprint(m, 1, {sb.ROLE_POINTS: shas[sb.ROLE_POINTS], sb.ROLE_SOLUTION: shas[sb.ROLE_SOLUTION]}) == (
            fingerprint(before["meta"], 1, {sb.ROLE_POINTS: shas[sb.ROLE_POINTS],
                                            sb.ROLE_SOLUTION: shas[sb.ROLE_SOLUTION]}))  # fmt: skip
        states = lineage.status(conn, [lineage.Node("wireframe", k.id) for k in kids] + [node])
        assert {s.state for s in states.values()} == {"current"}, states
