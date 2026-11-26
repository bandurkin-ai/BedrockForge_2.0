"""The provider `object:resource_report` of the graph and the `.bfggis` of the reports (подшаг 20.6; ADR 0025; ADR 0027
решение 21; ТЗ Д-35 — **ТД35-1 up to the report**): the fingerprint is the SHA-256 of `data` and ALGO, the inputs are
the model, the fields it reads, the filter and the wireframes, a table of samples edited → the field of the estimation
is «устарел» → the report says so with the chain, «Закрепить», «источник удалён», «Перестроить» (the maker of the
kind), and the cycle export → import with the ids renumbered. The rows are made by hand (`result.py` is 20.3)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

import bf_core.lineage.adapt_resources as adapt  # registers the kind until `lineage/providers.py` imports it
from bf_core import lineage
from bf_core.archive import importer
from bf_core.blocks.filters import store as fst
from bf_core.db import dialect
from bf_core.db.schema import dh_tables
from bf_core.lineage import providers, rebuild
from bf_core.projects.service import projects_of
from bf_core.resources import remap, spec
from bf_core.resources import store as st
from bf_core.resources import store_data as sd
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as ws
from tests import test_vario_store as vs
from tests.estutil import W, fill_world, world
from tests.test_blocks_fields_store import add_fields
from tests.test_import_job import wait_job
from tests.test_model_store import solid
from tests.test_resources_store import ROWS, built, make, rd, refused
from tests.test_vario_store import API

vc = vs.vc  # the core with an open project and the set of `test_vario_store`
NODE = "object:resource_report"
EST = {"op": "estimate", "params": {}, "inputs": [], "algo": "estimate-1"}


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


def est_field(w: W, name: str = "AU_EST", value: float = 1.5, replace: bool = False) -> None:
    """The field an estimation row writes (`geostat/estimate/job.py`): a new file of the model and the table of the
    samples as the input of the field in the graph."""
    add_fields(w.project, w.store, w.model, [{"name": name, "dtype": "f8", "unit": "г/т", "replace": replace}],
               {name: np.full(350, value)}, source=EST)  # fmt: skip
    with dialect.write_tx(w.project.engine) as conn:
        lineage.record(conn, lineage.Node("bm_field", w.model, name.casefold()), [lineage.Node("dh_table", w.a)])


def edit_table(w: W) -> None:
    with dialect.write_tx(w.project.engine) as conn:
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == w.a).values(rev=dh_tables.c.rev + 1))


def state(w: W, ident: int, store: Any = None) -> lineage.NodeStatus:
    node = lineage.Node(NODE, ident)
    with dialect.read_tx(w.project.engine) as conn:
        return lineage.status(conn, [node], store=store)[node]


def fp(w: W, ident: int) -> str | None:
    return rd(w, lineage.fingerprint, lineage.Node(NODE, ident))


def of_estimate(w: W, name: str = "Ресурсы ЮЗ", **kw: Any) -> int:
    """A report that reads the field of an estimation."""
    est_field(w)
    over = {"elements": [{"field": "AU_EST", "unit": "g/t"}, {"field": "CU", "unit": "%"}], **kw.pop("over", {})}
    return int(make(w, name, fields=("AU_EST", "CU", "DOM"), **over, **kw)["id"])


# --- the provider ---------------------------------------------------------------------------------------------------


def test_the_kind_has_a_provider_with_a_russian_label() -> None:
    assert NODE in providers.registered()
    assert providers.label_of(NODE) == "отчёт по ресурсам"
    assert st.NODE == NODE and adapt.JOB_KIND == spec.KIND == "resources.report"


def test_a_report_is_current_and_its_inputs_are_the_model_and_the_fields(w: W) -> None:
    ident = int(make(w)["id"])
    assert state(w, ident).state == "current" and fp(w, ident)
    node = lineage.Node(NODE, ident)
    with dialect.read_tx(w.project.engine) as conn:
        refs = lineage.store.inputs_of(conn, [node])[node]
    assert [(r.node.kind, r.node.id, r.node.sub) for r in refs] == [
        ("object:block_model", w.model, ""), ("bm_field", w.model, "au"), ("bm_field", w.model, "cu"),
        ("bm_field", w.model, "dom")]  # fmt: skip
    assert all(r.fp for r in refs) and refs[1].name == "Модель.AU"


def test_the_fingerprint_is_the_data_and_the_algo_not_the_name_or_the_version(w: W) -> None:
    ident = int(make(w, "Ресурсы")["id"])
    first = fp(w, ident)
    assert first is not None and first.startswith("r:") and fp(w, ident) == first
    with dialect.write_tx(w.project.engine) as conn:
        st.rename(conn, ident, "Другое имя", 1)  # a rename, an export, a book: the numbers are the same
        st.put_xlsx(conn, w.store, ident, w.store.stage_bytes(b"PK"))
        st.record_file(conn, ident, {"format": "xlsx", "path": "a.xlsx", "sha256": "ab", "at": "t"})
    assert fp(w, ident) == first and state(w, ident).state == "current"
    with dialect.write_tx(w.project.engine) as conn:  # a rebuild with the same rows: version + 1, the same fingerprint
        st.replace(conn, w.store, ident, built(w))
    assert rd(w, st.info, ident)["version"] == 3 and fp(w, ident) == first
    with dialect.write_tx(w.project.engine) as conn:  # other numbers: another state
        st.replace(conn, w.store, ident, built(w, rows=ROWS[:-1]))
    assert fp(w, ident) != first
    assert st.fingerprint("ab" * 32, "resources-1", 1) != st.fingerprint("ab" * 32, "resources-2", 1)
    assert st.fingerprint(None, "resources-1", 4) == "4"  # a report without a file: its version


def test_a_report_that_is_being_rebuilt_is_building(w: W) -> None:
    ident = int(make(w)["id"])
    with dialect.write_tx(w.project.engine) as conn:
        st.mark_building(conn, ident)
    assert state(w, ident).state == "building"
    with dialect.write_tx(w.project.engine) as conn:
        st.settle(conn, ident)
    assert state(w, ident).state == "current"


# --- ТД35-1 up to the report -----------------------------------------------------------------------------------------


def test_td35_1_a_table_of_samples_edited_makes_the_report_stale_with_the_chain(w: W) -> None:
    ident = of_estimate(w)
    assert state(w, ident, w.store).state == "current"
    edit_table(w)
    got = state(w, ident, w.store)
    assert got.state == "stale"
    # report ← the field of the model ← the table: the whole way, as the window «Происхождение» shows it
    assert got.reason == "поле блочной модели «Модель.AU_EST» ← изменено: таблица «Пробы»", got.reason
    info = rd(w, st.info, ident, store=w.store)
    assert info["graph"]["stale"] is True and "таблица «Пробы»" in info["graph"]["stale_reason"]
    assert rd(w, st.list_reports)[0]["graph"]["stale"] is True
    with dialect.read_tx(w.project.engine) as conn:
        origin = lineage.origin(conn, lineage.Node(NODE, ident))
    by_kind = {(o["kind"], o["sub"]): o["state"] for o in origin}
    assert by_kind[("bm_field", "au_est")] == "stale" and by_kind[("object:block_model", "")] == "current"
    # the estimation is run again: the field is a new file, the report says «изменено: поле»
    est_field(w, value=2.5, replace=True)
    again = state(w, ident, w.store)
    assert again.state == "stale" and again.reason == "изменено: поле блочной модели «Модель.AU_EST»", again.reason
    # «Перестроить»: the report is written again from the new field and is current
    with dialect.write_tx(w.project.engine) as conn:
        st.replace(conn, w.store, ident, built(w, fields=("AU_EST", "CU", "DOM")))
    assert state(w, ident, w.store).state == "current"
    refreshed = rd(w, st.info, ident)
    assert refreshed["version"] == 2 and refreshed["graph"]["stale"] is False


def test_td35_1_the_others_fields_do_not_make_it_stale(w: W) -> None:
    """A report reads AU, CU and DOM: a new field of the model (written by an estimation) does not touch it."""
    ident = int(make(w)["id"])
    est_field(w)
    edit_table(w)
    assert state(w, ident, w.store).state == "current"
    with dialect.read_tx(w.project.engine) as conn:  # but impact names it for the field it reads
        impact = lineage.impact(conn, lineage.Node("bm_field", w.model, "au_est"))
    assert impact["total"] == 0


def test_impact_of_a_field_names_the_reports_built_from_it(w: W) -> None:
    ident = of_estimate(w)
    with dialect.read_tx(w.project.engine) as conn:
        impact = lineage.impact(conn, lineage.Node("bm_field", w.model, "au_est"))
        assert impact["by_kind"] == {NODE: 1} and impact["nodes"][0]["id"] == ident
        assert impact["nodes"][0]["name"] == "Ресурсы ЮЗ"
        everything = lineage.impact(conn, lineage.Node("object:block_model", w.model))
    assert ident in [n["id"] for n in everything["nodes"] if n["kind"] == NODE]


def test_a_pinned_report_is_not_stale_but_says_it_would_be(w: W) -> None:
    ident = of_estimate(w)
    node = lineage.Node(NODE, ident)
    edit_table(w)
    with dialect.write_tx(w.project.engine) as conn:
        pinned = lineage.pin(conn, node, True)  # «Закрепить»: a report that went out
    assert (pinned.state, pinned.changed) == ("pinned", True) and "таблица «Пробы»" in str(pinned.reason)
    got = rd(w, st.list_reports)[0]["graph"]
    assert got["pinned"] is True and got["stale"] is True  # the window shows both: pinned, and the inputs changed
    with dialect.write_tx(w.project.engine) as conn:
        st.rename(conn, ident, "Выпущен", 1)  # the pin survives a rename
        lineage.pin(conn, node, True)
    assert state(w, ident).state == "pinned"
    with dialect.write_tx(w.project.engine) as conn:  # «Снять»
        assert lineage.pin(conn, node, False).state == "stale"
        refused(lambda: lineage.pin(conn, lineage.Node(NODE, 99_999), True), 404, "lineage_node_not_found")
    with dialect.write_tx(w.project.engine) as conn:
        st.delete(conn, ident)
    with dialect.read_tx(w.project.engine) as conn:  # a deleted report takes its pin with it
        assert lineage.store.pins_of(conn, [node]) == set()


def test_a_deleted_filter_is_a_lost_source_and_the_filter_is_an_input(w: W) -> None:
    ident = int(make(w, filter_id=w.flt, fields=("AU", "CU", "DOM"))["id"])
    with dialect.read_tx(w.project.engine) as conn:
        node = lineage.Node(NODE, ident)
        kinds = [r.node.kind for r in lineage.store.inputs_of(conn, [node])[node]]
    assert "object:filter" in kinds and "bm_field" in kinds
    assert [i["kind"] for i in rd(w, st.info, ident)["inputs"]].count("object:filter") == 1
    with dialect.write_tx(w.project.engine) as conn:
        fst.update(conn, w.flt, {"expr": "AU > 0.5", "expected_version": 1})
    moved = state(w, ident)
    assert moved.state == "stale" and moved.reason.startswith("изменено: ") and "«Руда»" in moved.reason, moved.reason  # type: ignore[union-attr]
    with dialect.write_tx(w.project.engine) as conn:
        fst.delete(conn, w.flt)
    lost = state(w, ident)
    assert lost.state == lineage.LOST and "источник удалён" in str(lost.reason)
    assert rd(w, st.list_reports)[0]["graph"]["source_lost"] is True


def test_the_wireframes_of_the_rules_and_of_the_filter_are_inputs(w: W) -> None:
    pit, cut = solid(w, "Карьер"), solid(w, "Зона", 200.0)
    with dialect.write_tx(w.project.engine) as conn:
        flt = fst.create(conn, {"name": "В зоне", "rules": {"wireframe": {"id": cut, "side": "inside", "by": "frac"}}})
    restrict = {"op": "and", "items": [{"wireframe": {"id": pit, "side": "inside", "by": "frac"}},
                                       {"expr": "N > 10"}]}  # fmt: skip
    with dialect.read_tx(w.project.engine) as conn:
        nodes = st.input_nodes(conn, w.model, ["AU"], flt, restrict)
    got = [(n.kind, n.id, n.sub) for n in nodes]
    assert got == [("object:block_model", w.model, ""), ("bm_field", w.model, "au"), ("bm_field", w.model, "n"),
                   ("object:filter", flt, ""), ("wireframe", pit, ""), ("wireframe", cut, "")]  # fmt: skip
    ident = int(make(w, filter_id=flt, restrict=restrict)["id"])
    assert state(w, ident).state == "current"
    with dialect.write_tx(w.project.engine) as conn:  # the wireframe of the pit is deleted: «источник удалён»
        ws.delete_wireframe(conn, pit)
    gone = state(w, ident)
    assert gone.state == lineage.LOST and "каркас «Карьер»" in str(gone.reason), gone.reason
    # what is not there any more is left out of the nodes (the graph of the report is what it read)
    with dialect.read_tx(w.project.engine) as conn:
        assert [n.kind for n in st.input_nodes(conn, w.model, ["NOPE"], None, None)] == ["object:block_model"]


# --- «Перестроить»: the maker of the kind ------------------------------------------------------------------------


def test_the_maker_puts_the_job_with_the_stored_parameters(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    import bf_core.jobs.registry as reg

    monkeypatch.setitem(rebuild.MAKERS, NODE, rebuild.Maker(adapt.owner, adapt.prepare_rebuild))
    monkeypatch.setitem(reg.KINDS, adapt.JOB_KIND, SimpleNamespace())  # until `jobs/registry.py` registers it
    ident = of_estimate(w)
    edit_table(w)
    svc = SimpleNamespace(require_current=lambda: w.project)
    node = lineage.Node(NODE, ident)
    step = rebuild.prepare_step(svc, node, {})
    assert step.job_kind == "resources.report" and step.title == "Перестроить отчёт «Ресурсы ЮЗ»"
    assert step.params == rd(w, st.rebuild_params, ident) and step.params["save"] is True
    with dialect.read_tx(w.project.engine) as conn:
        plan = rebuild.make_plan(conn, node, with_dependents=False)
    assert [(i.node, i.owner, i.state) for i in plan.items] == [(node, node, "stale")]
    with dialect.write_tx(w.project.engine) as conn:
        st.mark_building(conn, ident)
    refused(lambda: rebuild.prepare_step(svc, node, {}), 409, "report_building")
    refused(lambda: rebuild.prepare_step(svc, lineage.Node(NODE, 99_999), {}), 404, "resource_report_not_found")


# --- `.bfggis` -------------------------------------------------------------------------------------------------------

OBJS = {5: 50, 7: 70, 9: 90, 3: 30}


def a_meta(**over: Any) -> dict[str, Any]:
    params = {"model_id": 5, "elements": [{"field": "AU", "unit": "g/t"}], "cutoff_field": "AU", "cutoffs": [None, 1.0],
              "balance": None, "density": 2.5, "filter_id": 7, "restrict": {"wireframe": {"id": 9, "side": "inside",
                                                                                       "by": "frac"}},
              "groups": [{"kind": "field", "field": "DOM"}], "bench": None, "curve": 100,
              "template_id": None}  # fmt: skip
    return {"v": 1, "algo": "resources-1", "params": params, "params_hash": spec.parse_params(params).params_hash(),
            "model": {"id": 5, "name": "М", "cells_sha": "ab", "grid": {}}, "fields": {"AU": "cells|None"},
            "inputs": [{"kind": "object:block_model", "id": 5, "fp": "ab", "name": "М"},
                       {"kind": "bm_field", "id": "5:AU", "fp": "cells|None", "name": "М.AU"},
                       {"kind": "object:filter", "id": 7, "fp": "1", "name": "Ф"},
                       {"kind": "wireframe", "id": 9, "fp": "1", "name": "К"}],
            "totals": None, "files": [{"format": "xlsx", "path": "a.xlsx", "sha256": "ab", "at": "t"}],
            "state": "ready", **over}  # fmt: skip


def test_remap_renumbers_every_id_of_the_meta() -> None:
    meta = a_meta()
    new = remap.remap_meta(meta, OBJS.get)
    assert (new["params"]["model_id"], new["params"]["filter_id"]) == (50, 70)
    assert new["params"]["restrict"]["wireframe"]["id"] == 90 and new["model"]["id"] == 50
    assert [i["id"] for i in new["inputs"]] == [50, "50:AU", 70, 90]
    assert [i["fp"] for i in new["inputs"]] == [i["fp"] for i in meta["inputs"]]  # the states stay
    assert new["fields"] == meta["fields"] and new["files"] == meta["files"] and new["params"]["groups"] == (
        meta["params"]["groups"])  # fmt: skip
    assert new["params_hash"] != meta["params_hash"] and new["params_hash"] == (
        spec.parse_params(new["params"]).params_hash())  # the ids are in the hash: made again
    assert meta["params"]["model_id"] == 5  # the old meta is not touched


def test_a_reference_the_archive_did_not_have_is_dead_not_another_object() -> None:
    new = remap.remap_meta(a_meta(), {5: 50}.get)  # the filter and the wireframe are not in the archive
    assert new["params"]["filter_id"] == remap.DEAD_ID and new["params"]["restrict"]["wireframe"]["id"] is None
    assert [i["id"] for i in new["inputs"]] == [50, "50:AU", remap.DEAD_ID, remap.DEAD_ID]
    gone = remap.remap_meta(a_meta(), {}.get)
    assert gone["params"]["model_id"] == gone["model"]["id"] == remap.DEAD_ID
    assert gone["inputs"][1]["id"].endswith(":AU")
    spec.parse_params(gone["params"])  # still reads: the report shows «источник удалён»
    none = remap.remap_meta(a_meta(params={**a_meta()["params"], "filter_id": None, "restrict": None}), OBJS.get)
    assert none["params"]["filter_id"] is None and none["params"]["restrict"] is None


def test_a_mark_building_is_not_carried_over() -> None:
    new = remap.remap_meta(a_meta(state="building", job_id=12), OBJS.get)
    assert new["state"] == "ready" and "job_id" not in new  # its job is not a job of the new project


def test_remap_rows(w: W) -> None:
    a = int(make(w)["id"])
    with dialect.write_tx(w.project.engine) as conn:
        assert remap.remap_resources(conn, {w.model: w.model + 100}) == 1
    got = rd(w, st.info, a)
    assert got["params"]["model_id"] == got["model"]["id"] == w.model + 100
    assert [i["id"] for i in got["inputs"]][:2] == [w.model + 100, f"{w.model + 100}:AU"]
    assert got["params_hash"] == spec.parse_params(got["params"]).params_hash()


def test_bfggis_roundtrip_renumbers_the_reports_and_keeps_them_current(  # type: ignore[no-untyped-def]
        vc, tmp_path: Path) -> None:
    w = fill_world(vc.project, vc.set_id, vc.a, vc.b)
    pit = solid(w, "Карьер")
    restrict = {"wireframe": {"id": pit, "side": "inside", "by": "frac"}}
    est_field(w)
    ident = int(make(w, "Ресурсы ЮЗ", filter_id=w.flt, restrict=restrict, fields=("AU_EST", "CU", "DOM"),
                     elements=[{"field": "AU_EST", "unit": "g/t"}, {"field": "CU", "unit": "%"}])["id"])  # fmt: skip
    other = int(make(w, "Второй")["id"])
    before = rd(w, st.info, ident, store=w.store)
    assert before["graph"]["stale"] is False
    sha = rd(w, st.data_sha, ident)
    r = vc.post(API + "/projects/export", json={"path": str(tmp_path / "Ресурсы")}).json()
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
    if not hasattr(importer, "remap_resources"):  # the line of the orchestrator is not in `archive/importer.py` yet
        with dialect.write_tx(project.engine) as conn:
            remap.remap_resources(conn, objs)
    got = rd(n, st.info, objs[ident], store=n.store)
    assert [r["id"] for r in rd(n, st.list_reports)] == sorted([objs[ident], objs[other]], key=lambda i: (  # by name
        "Второй" if i == objs[other] else "Ресурсы ЮЗ"))  # fmt: skip
    p = got["params"]
    assert (p["model_id"], p["filter_id"], p["restrict"]["wireframe"]["id"]) == (objs[w.model], objs[w.flt], objs[pit])
    assert got["model"]["id"] == objs[w.model] and got["params_hash"] == spec.parse_params(p).params_hash()
    assert got["params_hash"] != before["params_hash"]  # the ids are in the hash
    ids = [i["id"] for i in got["inputs"]]
    assert ids[:2] == [objs[w.model], f"{objs[w.model]}:AU_EST"] and objs[w.flt] in ids and objs[pit] in ids
    assert [i["fp"] for i in got["inputs"]] == [i["fp"] for i in before["inputs"]]  # the states came with it
    # the file came bit for bit, the rows read the same, and the report is as current as it was
    assert rd(n, st.data_sha, objs[ident]) == sha
    assert sd.table_rows(rd(n, st.read_table, n.store, objs[ident])) == ROWS
    assert rd(n, st.read_control, n.store, objs[ident]) is not None
    assert got["graph"] == {"stale": False, "stale_reason": None, "source_lost": False, "pinned": False}, got["graph"]
    node = lineage.Node(NODE, objs[ident])
    with dialect.read_tx(project.engine) as conn:
        assert lineage.fingerprint(conn, node) == fp(w, ident)  # the fingerprint has no ids
        refs = lineage.store.inputs_of(conn, [node])[node]
        assert {r.node.id for r in refs if r.node.kind == "bm_field"} == {objs[w.model]}
        assert lineage.status(conn, [node])[node].state == "current"
    # the new project knows its own chain: the table of the samples edited there → the report is stale
    with dialect.write_tx(project.engine) as conn:
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == n.a).values(rev=dh_tables.c.rev + 1))
    after = rd(n, st.info, objs[ident], store=n.store)
    assert after["graph"]["stale"] is True and "таблица" in after["graph"]["stale_reason"]
    assert rd(n, st.info, objs[other], store=n.store)["graph"]["stale"] is False
