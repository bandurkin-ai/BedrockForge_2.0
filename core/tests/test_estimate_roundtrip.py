"""A row of the estimation that has run, through the project archive and back (подшаг 18.32б; ADR 0025; e18-api §2.3):
the numbers of the objects change on import, and neither the row (`last_run.params` is the hash of the parameters with
the numbers) nor its fields (the fingerprint of `object:estimation` includes them) become «устарел» for that."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bf_core import lineage
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.geostat import remap
from bf_core.geostat.estimate import job
from bf_core.geostat.estimate import store as st
from bf_core.geostat.estimate import store_base as sb
from bf_core.lineage import adapt_estimate
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests import test_vario_store as vs
from tests.estutil import W, fill_world, get, make, rd
from tests.test_import_job import wait_job
from tests.test_vario_store import API

vc = vs.vc  # the core with an open project and the set of `test_vario_store`


def a_row(**over: Any) -> dict[str, Any]:
    """The stored `meta` of a row of the estimation that names objects by their numbers."""
    from bf_core.geostat.estimate import spec as es

    raw = {"model_id": 7, "target": {"domain_field": "DOM", "label": "a", "filter_id": 9},
           "samples": {"set_id": 3, "table": "12", "field": "AU",
                       "domain": {"table": "14", "field": "ZONE", "values": ["a"]}},
           "variogram": {"model_id": 21, "sha": "ab" * 32}, "trend_id": 5, "search": {"from": "manual", "r1": 50},
           **over}  # fmt: skip
    meta = es.parse_estimation(raw).canonical()
    meta["inputs"] = [{"kind": "dh_table", "id": 12, "fp": "t:1", "name": "П"},
                      {"kind": "object:variogram_model", "id": 21, "fp": "ab", "name": "М"}]  # fmt: skip
    return meta


def test_the_fingerprint_does_not_name_the_objects() -> None:
    meta = a_row()
    old = adapt_estimate.fingerprint(meta, 4)
    assert old.startswith("p:") and old == adapt_estimate.fingerprint(json.loads(json.dumps(meta)), 9)  # no version
    objs, tabs = {7: 70, 5: 50, 9: 90, 3: 30, 21: 210}, {12: 120, 14: 140}
    new = remap.estimation_meta(meta, objs.get, tabs.get)
    assert [i["id"] for i in new["inputs"]] == [120, 210] and new["model_id"] == 70 and new["trend_id"] == 50
    assert adapt_estimate.fingerprint(new, 4) == old  # renumbered: the same state
    refined = {**new, "inputs": [new["inputs"][0], {**new["inputs"][1], "fp": "cd"}]}
    assert adapt_estimate.fingerprint(refined, 4) != old  # another content of an input: another state
    other = a_row(search={"from": "manual", "r1": 80})
    assert adapt_estimate.fingerprint(other, 4) != old  # other parameters


def test_swapped_tables_of_one_kind_are_another_state() -> None:
    """The samples and the domain tables swapped: the ids are not in the fingerprint, the order of the inputs (the role)
    must be."""
    meta = a_row()
    two = [{"kind": "dh_table", "id": 12, "fp": "t:1", "name": "П"},
           {"kind": "dh_table", "id": 14, "fp": "t:2", "name": "Д"}]  # fmt: skip
    a = {**meta, "inputs": two}
    b = {**meta, "inputs": two[::-1]}
    assert adapt_estimate.fingerprint(a, 4) != adapt_estimate.fingerprint(b, 4)
    renamed = {**a, "inputs": [{**two[0], "name": "Другая"}, two[1]]}  # a name is no state
    assert adapt_estimate.fingerprint(renamed, 4) == adapt_estimate.fingerprint(a, 4)


def test_the_hash_of_the_last_run_is_made_again_for_the_new_ids() -> None:
    meta = a_row()
    meta["last_run"] = {"job": 1, "params": sb.params_hash(sb.load_spec(meta))}
    objs, tabs = {7: 70, 3: 30, 21: 210, 5: 50, 9: 90}, {12: 120, 14: 140}
    new = remap.estimation_meta(meta, objs.get, tabs.get)
    assert new["last_run"]["params"] != meta["last_run"]["params"]  # the ids are in the hash
    assert new["last_run"]["params"] == sb.params_hash(sb.load_spec(new))  # and it is that of the row now
    stale = {**meta, "search": {**meta["search"], "r1": 99.0}}  # a row that was «устарел» by its parameters stays so
    kept = remap.estimation_meta(stale, objs.get, tabs.get)
    assert kept["last_run"]["params"] == meta["last_run"]["params"]
    assert remap.estimation_meta({**meta, "last_run": None}, objs.get, tabs.get)["last_run"] is None


def test_bfggis_roundtrip_of_a_row_that_ran_keeps_it_and_its_fields_current(  # type: ignore[no-untyped-def]
        vc, tmp_path: Path) -> None:
    w = fill_world(vc.project, vc.set_id, vc.a, vc.b)
    ident = make(w, name="Руда", search={"r1": 60})["id"]
    with dialect.read_tx(w.project.engine) as conn:
        version = Model.open(conn, w.store, w.model).version
    out = job.run_estimate(w.project.engine, w.project.folder, {"estimation_ids": [ident],
                                                                "expected_version": version}, threads=1)
    assert out["fields"] and get(w, ident)["stale"] is False
    field = lineage.Node("bm_field", w.model, out["fields"][1].casefold())  # the EST field
    est = lineage.Node("object:estimation", ident)
    with dialect.read_tx(w.project.engine) as conn:
        assert lineage.status(conn, [field, est])[field].state == "current"
    r = vc.post(API + "/projects/export", json={"path": str(tmp_path / "Оценка")}).json()
    done = wait_job(vc, r["source"], r["id"])
    assert done["status"] == "done", done
    r = vc.post(API + "/projects/import", json={"path": done["result"]["path"], "mode": "copy"}).json()
    done = wait_job(vc, r["source"], r["id"])
    assert done["status"] == "done", done
    maps = done["result"]["id_maps"]
    objs = {int(k): int(v) for k, v in maps["objects"].items()}
    tabs = {int(k): int(v) for k, v in maps["dh_tables"].items()}
    assert vc.post(f"{API}/projects/{done['result']['uid']}/open").status_code == 200
    project = projects_of(vc.core).require_current()
    n = W(project, BlobStore(project.folder), objs[vc.set_id], tabs[vc.a], tabs[vc.b], objs[w.model], objs[w.vz],
          objs[w.trend], objs[w.flt])  # fmt: skip
    assert objs != {k: k for k in objs}, objs  # the import renumbered the objects: the test would not see the bug else
    row = get(n, objs[ident])
    assert (row["stale"], row["stale_reason"], row["source_lost"]) == (False, None, False), row
    new_field = lineage.Node("bm_field", n.model, field.sub)
    new_est = lineage.Node("object:estimation", objs[ident])
    with dialect.read_tx(project.engine) as conn:
        got = lineage.status(conn, [new_field, new_est])
        assert (got[new_est].state, got[new_field].state) == ("current", "current"), got
    assert [r["id"] for r in rd(n, st.list_rows)] == [objs[ident]]
    assert sb.params_hash(sb.load_spec(row["meta"])) == row["meta"]["last_run"]["params"]
