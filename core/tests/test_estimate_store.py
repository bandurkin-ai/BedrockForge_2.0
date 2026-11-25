"""Stored «Параметры оценки» (подшаг 18.11; ADR 0024 §2, решения 2–3; ADR 0025; e18-api.md §2, §4): create / read /
update of a row, the copy of the variogram model with `sha` and the scale of Д-38, the refs to the trend and the
filter (409 `in_use`, 422 `filter_in_use`, «Где используется»), the checks against the project, «устарел» by the
inputs and by the parameters. The plans, the graph, `.bfggis` and PostgreSQL — `test_estimate_store_plans.py`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.blocks import store as bm
from bf_core.blocks.filters import store as fst
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, object_refs, objects
from bf_core.drillholes import store as dh
from bf_core.geostat import trend_store as ts
from bf_core.geostat.estimate import spec as es
from bf_core.geostat.estimate import store as st
from bf_core.geostat.estimate import store_base as sb
from bf_core.geostat.estimate import store_check as sc
from bf_core.geostat.variogram import exchange, points
from bf_core.geostat.variogram import spec as vspec
from bf_core.geostat.variogram import store as ms
from tests.estutil import BACK, W, body, get, make, mk_refused, put, rd, refused, vmodel, world
from tests.test_vario_store import MODEL


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


# --- the row: create, read, list ------------------------------------------------------------------------------------


def test_create_reads_the_project_and_keeps_a_copy(w: W) -> None:
    made = make(w)
    assert made == {"id": made["id"], "name": "AU · OK · руда", "version": 1}
    got = get(w, made["id"])
    meta = got["meta"]
    assert (got["name"], got["version"], got["plans"], got["stale"], got["source_lost"]) == (
        "AU · OK · руда", 1, [], False, False)  # fmt: skip
    assert meta["v"] == 1 and meta["algo"] == "est-1" and meta["last_run"] is None and meta["name"] == got["name"]
    assert set(meta["versions"]) == {"numpy", "scipy", "numba"} and meta["created_at"]
    # the samples as the project has them: ids as text, the stored spelling, the unit of the field
    assert meta["samples"]["table"] == str(w.a) and meta["samples"]["unit"] == "г/т"
    assert meta["samples"]["domain"] == {"table": str(w.b), "field": "ZONE", "values": ["окисл"]}
    # the copy of the variogram model: snapshot, sha, scale (Д-38); the search comes from its first structure
    with dialect.read_tx(w.project.engine) as conn:
        _name, vmeta = ms.meta_of(conn, w.vz)
    v = meta["variogram"]
    assert (v["model_id"], v["sha"], v["scale"], v["share"]) == (w.vz, vmeta["sha256"], "z", None)
    assert v["snapshot"]["c0"] == 0.2 and v["snapshot"]["structures"][0]["a1"] == 120.0
    s = meta["search"]
    assert (s["from"], s["r1"], s["r2"], s["r3"], s["ang1"], s["ang2"]) == ("model", 120.0, 80.0, 20.0, 30.0, -10.0)
    # the origin — the centre of the extent of the samples of the domain, as Э16 finds it
    data = vspec.Data(w.set_id, w.a, "Пробы", "AU", "г/т", vspec.DomainSel(w.b, "Литология", "ZONE", "text",
                                                                           ("окисл",)), "DECL_W", "none")  # fmt: skip
    assert meta["origin"] == points.load(w.project, data).origin.tolist()
    assert [c["name"] for c in got["columns"]] == [f"AU_OK_{k}" for k in
                                                   ("STATUS", "EST", "KV", "NS", "NH", "AD", "PASS", "SLOPE", "KE")]
    assert got["fields_exist"] == []
    # the inputs of the graph (ADR 0025): the form, the order, the fingerprints
    kinds = [i["kind"] for i in meta["inputs"]]
    assert kinds == ["dh_table", "dh_table", "dh_set", "object:variogram_model", "object:block_model", "bm_field"]
    by = {i["kind"]: i for i in meta["inputs"]}
    tables = [i for i in meta["inputs"] if i["kind"] == "dh_table"]
    assert [(t["id"], t["name"]) for t in tables] == [(w.a, "Пробы"), (w.b, "Литология")]
    assert all(t["fp"].endswith(":1") for t in tables)
    assert by["dh_set"]["fp"].startswith("traj:") and by["object:variogram_model"]["fp"] == vmeta["sha256"]
    assert by["object:block_model"]["fp"].startswith("cells:") and by["bm_field"]["id"] == f"{w.model}:DOM"
    assert all(set(i) == {"kind", "id", "fp", "name"} for i in meta["inputs"])
    listed = rd(w, st.list_rows)
    assert [(r["id"], r["method"], r["prefix"], r["label"], r["plans"], r["last_run"]) for r in listed] == [
        (made["id"], "ok", "AU_OK_", "руда", [], None)]  # fmt: skip
    assert rd(w, st.list_rows, w.model + 1) == []


def test_names_and_soft_samples_and_domain_free_rows(w: W) -> None:
    first = make(w)
    assert make(w)["name"] == "AU · OK · руда (2)"  # the default name is made free
    r = refused(lambda: make(w, name=" au · ok · РУДА "), 409, "name_taken", "name")
    assert r.status == 409
    free = make(w, target={}, samples={**body(w)["samples"], "domain": None}, name="Все блоки")
    assert get(w, free["id"])["meta"]["target"]["label"] is None
    assert make(w, target={}, samples={**body(w)["samples"], "domain": None})["name"] == "AU · OK"
    # the soft boundary: S joins the extent of the samples (Д-28 / 8.8.2)
    soft = {**body(w)["samples"], "soft": {"domains": ["перв"], "dist": 5}}
    ident = make(w, samples=soft, name="С мягкой")["id"]
    both = vspec.DomainSel(w.b, "Литология", "ZONE", "text", ("окисл", "перв"))
    want = points.load(w.project, vspec.Data(w.set_id, w.a, "Пробы", "AU", "г/т", both, "DECL_W", "none")).origin
    assert get(w, ident)["meta"]["origin"] == want.tolist()
    assert get(w, ident)["meta"]["origin"] != get(w, first["id"])["meta"]["origin"]


def test_checks_against_the_project(w: W) -> None:
    samples = body(w)["samples"]

    def smp(**kw: Any) -> dict[str, Any]:
        return {**samples, **kw}

    mk_refused(w, 404, "block_model_not_found", "model_id", model_id=w.model + 999)
    mk_refused(w, 422, "field_not_found", "target.domain_field", target={"domain_field": "НЕТ", "label": "x"})
    mk_refused(w, 422, "domain_unmapped", "target.label", target={"domain_field": "DOM", "label": "золото"})
    mk_refused(w, 422, "domain_unmapped", "target.label", target={"domain_field": "N", "label": "много"})
    mk_refused(w, 404, "filter_not_found", "target.filter_id", target={"filter_id": w.flt + 999})
    mk_refused(w, 404, "set_not_found", "samples.set_id", samples=smp(set_id=w.set_id + 999))
    mk_refused(w, 404, "table_not_found", "samples.table", samples=smp(table="Нет такой"))
    mk_refused(w, 422, "field_not_found", "samples.field", samples=smp(field="PB"))
    mk_refused(w, 422, "bad_param", "samples.field", samples=smp(field="ROCK"))  # a text field
    mk_refused(w, 422, "field_not_found", "samples.domain.field",
               samples=smp(domain={"table": str(w.b), "field": "НЕТ", "values": ["окисл"]}))  # fmt: skip
    mk_refused(w, 422, "field_not_found", "samples.topcut.field", samples=smp(topcut={"field": "AU_CUT"}))
    mk_refused(w, 404, "model_not_found", "variogram.model_id", variogram={"model_id": w.vz + 999})
    mk_refused(w, 404, "trend_not_found", "trend_id", trend_id=w.trend + 999)
    # none of the values of the domain is in the table ≠ the samples of the domain are all empty
    err = mk_refused(w, 422, "domain_unmapped", "samples.domain.values",
                     samples=smp(domain={"table": str(w.b), "field": "ZONE", "values": ["нет"]}))  # fmt: skip
    assert "ZONE" in err.message
    dh.write_table(w.project.engine, w.set_id, dh.TableData(
        name="Пусто", kind="assay", holes=["A-1", "B-2"], from_m=[0.0, 0.0], to_m=[2.0, 2.0],
        columns=[dh.ColumnSpec("AU", "number", "г/т", "component")], values=[[None, None]]), job_id=None,
        tick=lambda *_: None)  # fmt: skip
    err = mk_refused(w, 422, "no_samples", "samples", samples={"set_id": w.set_id, "table": "Пусто", "field": "AU"})
    assert err.extra["counts"] == {"rows": 2, "missing": 2, "no_position": 0, "outside_domain": 0, "used": 0,
                                   "soft": 0}  # fmt: skip
    # a grid with the tensors is not estimated
    with dialect.write_tx(w.project.engine) as conn:
        grid = {"corner": [0.0, 0.0, 0.0], "tensors": {"x": [1.0, 2.0] * 15, "y": [3.0] * 20, "z": [0.5] * 20}}
        var = bm.put_block_model(conn, w.store, grid, [{"name": "AU", "kind": "number"}],
                                 np.arange(5, dtype=np.uint64), {"AU": np.ones(5)}, name="Переменный")  # fmt: skip
    mk_refused(w, 422, "variable_grid", "model_id", model_id=var, target={})
    with dialect.read_tx(w.project.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(objects).where(
            objects.c.kind == "estimation")).scalar() == 0  # the refusals left nothing


# --- the copy of the variogram model (Д-38) -------------------------------------------------------------------------


def test_scale_of_the_model_and_a_model_that_is_gone(w: W) -> None:
    y = vmodel(w, "Золото Y", transform="nscore")  # a model in normal scores without its back transform
    err = mk_refused(w, 422, "variogram_scale", "variogram.model_id", variogram={"model_id": y})
    assert err.message == "Модель построена в нормальных оценках" and "обратное преобразование" in err.hint
    back = vmodel(w, "Золото Y→Z", transform="nscore", back=BACK)
    row = make(w, variogram={"model_id": back})
    v = get(w, row["id"])["meta"]["variogram"]
    assert (v["scale"], v["share"], v["snapshot"]) == ("back", 0.97, {**BACK["model"], "type": "semivariogram"})
    ident = make(w, name="В Z")["id"]
    sha = get(w, ident)["meta"]["variogram"]["sha"]
    # a new version of the model: the row is «устарел» until it takes the copy again (PUT)
    new = {**MODEL, "structures": [{**MODEL["structures"][0], "a1": 200, "a2": 100}]}
    kind, parsed = exchange.api_model(new)
    with dialect.write_tx(w.project.engine) as conn:
        ms.update(conn, w.store, w.vz, expected_version=1, model=(kind, exchange.model_form(kind, parsed)))
    got = get(w, ident)
    assert (got["stale"], got["stale_reason"], got["source_lost"]) == (True, "изменена модель вариограммы «Золото Z»",
                                                                       False)  # fmt: skip
    assert got["meta"]["variogram"]["sha"] == sha
    assert put(w, ident, 1)["version"] == 2  # nothing given: the same row, the copy taken again
    again = get(w, ident)
    assert again["stale"] is False and again["meta"]["variogram"]["sha"] != sha
    assert again["meta"]["search"]["r1"] == 200.0 and again["meta"]["search"]["r2"] == 100.0  # `from: model`
    more = make(w, name="Ещё")["id"]
    assert [u["id"] for u in rd(w, st.usage_of_variogram, w.vz)] == [ident, more]  # by name
    # the model deleted: the row keeps its copy — it counts, but «устарел»; it is not «источник удалён»
    with dialect.write_tx(w.project.engine) as conn:
        ms.delete(conn, w.vz)
    gone = get(w, ident)
    assert (gone["stale"], gone["stale_reason"], gone["source_lost"]) == (True, "модель вариограммы «Золото Z» удалена",
                                                                          False)  # fmt: skip
    assert put(w, ident, 2, name="Без модели")["name"] == "Без модели"
    assert get(w, ident)["meta"]["variogram"]["snapshot"]["structures"][0]["a1"] == 200.0  # the copy stays
    assert put(w, ident, 3, variogram={"model_id": w.vz})["version"] == 4  # the same model: the copy is kept
    refused(lambda: put(w, ident, 4, variogram={"model_id": w.vz + 999}), 404, "model_not_found",
            "variogram.model_id")  # fmt: skip
    assert rd(w, st.usage_of_variogram, w.vz)[0]["role"] == "variogram"


# --- the trend and the filter: references, 409, «Где используется» --------------------------------------------------


def test_references_409_and_where_used(w: W) -> None:
    row = make(w, trend_id=w.trend, target={"domain_field": "DOM", "label": "руда", "filter_id": w.flt},
               name="С тренд и фильтром")  # fmt: skip
    ident = row["id"]
    refs = sa.select(object_refs.c.dst_id, object_refs.c.role).where(object_refs.c.src_id == ident)
    with dialect.read_tx(w.project.engine) as conn:
        assert sorted(conn.execute(refs).all()) == sorted([(w.trend, "trend"), (w.flt, "filter")])
        use = ts.usage(conn, w.trend)
        assert use == [{"id": ident, "kind": "estimation", "name": "С тренд и фильтром", "role": "trend"}]
        assert fst.usage(conn, w.flt) == [{"kind": "object", "id": ident, "name": "С тренд и фильтром",
                                           "role": "filter"}]  # fmt: skip
    with dialect.write_tx(w.project.engine) as conn:
        err = refused(lambda: ts.delete(conn, w.trend), 409, "in_use")
        assert err.extra["usage"] == use and "«С тренд и фильтром»" in err.message
        refused(lambda: fst.delete(conn, w.flt), 422, "filter_in_use")
    # the search `from: trend`: the angles of the trend, r1 = its size, r2, r3 by its ratios
    meta = get(w, ident)["meta"]
    assert meta["trend_id"] == w.trend and meta["target"]["filter_id"] == w.flt
    nn = make(w, method={"kind": "nn"}, variogram=None, trend_id=w.trend, name="NN по тренду")
    s = get(w, nn["id"])["meta"]["search"]
    assert (s["from"], s["ang1"], s["r1"]) == ("trend", 45.0, 120.0) and s["r2"] == s["r3"] == 120.0
    # PUT takes the references away: the trend and the filter are free again (the other row still holds the trend)
    put(w, ident, 1, trend_id=None, target={"domain_field": "DOM", "label": "руда"}, search={"from": "model"})
    with dialect.read_tx(w.project.engine) as conn:
        assert conn.execute(refs).all() == []
        assert fst.usage(conn, w.flt) == [] and [u["id"] for u in ts.usage(conn, w.trend)] == [nn["id"]]
    with dialect.write_tx(w.project.engine) as conn:
        fst.delete(conn, w.flt)
        refused(lambda: ts.delete(conn, w.trend), 409, "in_use")
    with dialect.write_tx(w.project.engine) as conn:
        assert st.delete(conn, nn["id"]) == {"deleted": nn["id"], "plans": []}
        ts.delete(conn, w.trend)  # the row is gone and its references with it


# --- «устарел» ------------------------------------------------------------------------------------------------------


def test_freshness_by_the_inputs_and_by_the_parameters(w: W) -> None:
    ident = make(w, trend_id=w.trend, target={"domain_field": "DOM", "label": "руда", "filter_id": w.flt})["id"]
    assert get(w, ident)["stale"] is False

    def state() -> tuple[bool, str | None, bool]:
        g = get(w, ident)
        return g["stale"], g["stale_reason"], g["source_lost"]

    def bump(table: Any, key: Any, **vals: Any) -> None:
        with dialect.write_tx(w.project.engine) as conn:
            conn.execute(sa.update(table).where(table.c.id == key).values(**vals))

    bump(dh_tables, w.b, rev=dh_tables.c.rev + 1)
    assert state() == (True, "изменена таблица «Литология»", False)
    bump(dh_tables, w.b, rev=dh_tables.c.rev - 1)
    assert state() == (False, None, False)
    bump(objects, w.set_id, version=objects.c.version + 1)
    assert state() == (True, "изменены траектории", False)
    bump(objects, w.set_id, version=objects.c.version - 1)
    with dialect.write_tx(w.project.engine) as conn:
        ts.update(conn, w.trend, {"expected_version": 1, "ang1": 50})
    assert state() == (True, "изменён тренд «Тренд СВ»", False)
    put(w, ident, 1)  # taken again: fresh
    assert state() == (False, None, False)
    with dialect.write_tx(w.project.engine) as conn:
        fst.update(conn, w.flt, {"expected_version": 1, "expr": "AU > 0.2"})
    assert state() == (True, "изменён фильтр «Руда»", False)
    put(w, ident, 2)
    # the parameters against the last run (`last_run.params`) — a PUT that changes them makes the row «устарел»
    with dialect.write_tx(w.project.engine) as conn:
        spec = sb.load_spec(get(w, ident)["meta"])
        st.record_run(conn, ident, spec, {"at": "2026-11-25T10:00:00+00:00", "job": "j-1", "version": 4,
                                          "counts": {"by_status": [4, 1, 0, 0]}}, get(w, ident)["meta"]["inputs"])
    got = get(w, ident)
    assert (got["version"], got["stale"], got["meta"]["last_run"]["job"]) == (3, False, "j-1")  # no version bump
    assert rd(w, st.list_rows)[0]["last_run"] == {"at": "2026-11-25T10:00:00+00:00", "by_status": [4, 1, 0, 0]}
    put(w, ident, 3, disc=[2, 2, 1])
    assert state() == (True, "изменены параметры", False)
    # a source gone: «источник удалён»
    with dialect.write_tx(w.project.engine) as conn:
        bm.delete_block_model(conn, w.model)
    assert state() == (True, "источник удалён", True)
    assert rd(w, st.list_rows)[0]["source_lost"] is True and get(w, ident)["columns"]


# --- update: the version, the keys, the origin ----------------------------------------------------------------------


def test_update_keys_conflict_and_origin(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    ident = make(w)["id"]
    other = make(w, name="Другая", target={"domain_field": "DOM", "label": "порода"})["id"]
    reads: list[Any] = []
    real = sc.scan
    monkeypatch.setattr(sc, "scan", lambda *a, **k: (reads.append(a[1]), real(*a, **k))[1])
    assert put(w, ident, 1, disc=[2, 2, 2], sub="own") == {"id": ident, "name": "AU · OK · руда", "version": 2}
    meta = get(w, ident)["meta"]
    assert (meta["disc"], meta["sub"], meta["target"]["label"]) == ([2, 2, 2], "own", "руда")  # the others as they were
    assert reads == []  # the samples are the same: the file is not read again, the origin stays
    origin = meta["origin"]
    soft = {**meta["samples"], "soft": {"domains": ["перв"], "dist": 5}}
    put(w, ident, 2, samples=soft, name="Мягкая")
    assert len(reads) == 1 and get(w, ident)["meta"]["origin"] != origin and get(w, ident)["name"] == "Мягкая"
    # the errors: the version (also another window between), the name, a key out of range, the row, the project
    err = refused(lambda: put(w, ident, 1, disc=[1, 1, 1]), 409, "version_conflict")
    assert err.extra == {"version": 3}
    for bad in (None, 0, True, "2"):
        refused(lambda bad=bad: put(w, ident, bad), 422, "bad_param", "expected_version")  # type: ignore[misc]
    refused(lambda: put(w, ident, 3, name="ДРУГАЯ"), 409, "name_taken", "name")
    refused(lambda: put(w, ident, 3, disc=[11, 1, 1]), 422, "bad_param", "disc.0")
    refused(lambda: put(w, other + 99, 1), 404, "estimation_not_found")
    assert get(w, ident)["version"] == 3  # the refusals changed nothing
    with dialect.write_tx(w.project.engine) as conn:  # the version guard of the UPDATE: a write between
        row = sb.stored(conn, sb.KIND, ident)
        conn.execute(sa.update(objects).where(objects.c.id == ident).values(version=9))
        err = refused(lambda: st.update(conn, w.store, w.project, ident, {}, int(row.version)), 409, "version_conflict")
        assert err.extra == {"version": 9}


def test_a_row_in_a_plan_keeps_its_block_model(w: W) -> None:
    a = make(w)["id"]
    with dialect.write_tx(w.project.engine) as conn:
        st.plan_create(conn, es.parse_plan({"model_id": w.model, "items": [a]}))
    with dialect.write_tx(w.project.engine) as conn:
        spare = bm.put_block_model(conn, w.store, {"corner": [0, 0, 0], "counts": [2, 2, 2], "size": [1, 1, 1]},
                                   [{"name": "DOM", "kind": "category", "labels": ["руда"]}],
                                   np.arange(3, dtype=np.uint64), {"DOM": np.zeros(3, dtype=np.int32)}, name="Вторая")
    refused(lambda: put(w, a, 1, model_id=spare), 422, "bad_param", "model_id")


def test_the_stores_import_nothing_heavy() -> None:
    """The core reports BF-READY before numpy: the store, the plans and the remap load none of the heavy modules."""
    heavy = ("numba", "numpy", "pandas", "pyarrow", "duckdb", "scipy")
    code = ("import sys; import bf_core.geostat.estimate.store, bf_core.geostat.remap;"
            f"print(','.join(m for m in {heavy!r} if m in sys.modules))")  # fmt: skip
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"imported by the store: {r.stdout.strip()}"
