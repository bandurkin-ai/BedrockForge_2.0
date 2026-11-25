"""Stored «Моделирование» (подшаг 18.32; ADR 0024 §2, §9; ADR 0025; e18-api.md §9): create / read / update of a row, the
copy of the variogram model in normal scores (`scale: y`, Д-38) and 422 for a model in the units of the field, the
checks against the project, 409 `name_taken` / `version_conflict`, «устарел» by the inputs and by the parameters, the
graph, `.bfggis` (the ids of the meta renumbered), PostgreSQL (`-m db`), the parameters (spec) and the light imports."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.blocks import store as bm
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, dh_tables, objects
from bf_core.geostat import remap
from bf_core.geostat.simulation import spec as sp
from bf_core.geostat.simulation import store as st
from bf_core.geostat.simulation import store_check as sc
from bf_core.geostat.variogram import exchange
from bf_core.geostat.variogram import store as ms
from tests import test_vario_store as vs
from tests.simutil import SW, body, get, make, put, refused, world
from tests.test_vario_store import API, MODEL


@pytest.fixture()
def sw(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


def rd(sw: SW, fn: Any, *args: Any) -> Any:
    with dialect.read_tx(sw.project.engine) as conn:
        return fn(conn, *args)


# --- the parameters ----------------------------------------------------------------------------------------------


def test_defaults_and_the_canonical_form(sw: SW) -> None:
    spec = sp.parse_simulation(body(sw, n_real=None, nodes=None, seed=None))
    assert (spec.kind, spec.seed, spec.n_real, spec.nodes) == ("sgs", 12345, 20, 12)
    assert spec.nscore.canonical() == {"ltail": "linear", "utail": "linear", "ltpar": None, "utpar": None}
    assert len(spec.search.passes) == 1 and spec.search.passes[0].n_max == 24 and spec.search.source == "model"
    canon = spec.canonical()
    assert list(canon) == ["name", "kind", "model_id", "target", "samples", "variogram", "variograms", "method",
                           "search", "nodes", "seed", "n_real", "secondary", "nscore"]  # fmt: skip
    assert canon["variograms"] is None and canon["method"] is None and canon["secondary"] is None
    assert sp.parse_simulation({**canon, "n_real": 20}).canonical() == canon  # the round trip is the same row


@pytest.mark.parametrize(("over", "param"), [
    ({"n_real": 0}, "n_real"), ({"n_real": 101}, "n_real"), ({"n_real": 2.5}, "n_real"), ({"n_real": True}, "n_real"),
    ({"seed": -1}, "seed"), ({"seed": 2**63}, "seed"), ({"nodes": 65}, "nodes"), ({"nodes": -1}, "nodes"),
    ({"search": {"passes": [{"n_max": 129}]}}, "search.passes.0.n_max"),
    ({"search": {"passes": [{}, {}]}}, "search.passes"), ({"search": {"passes": []}}, "search.passes"),
    ({"search": {"from": "trend"}}, "trend_id"),
    ({"nscore": {"ltail": "gauss"}}, "nscore.ltail"), ({"nscore": {"utail": "power"}}, "nscore.utpar"),
    ({"nscore": {"utail": "hyperbolic", "utpar": 0.5}}, "nscore.utpar"),
    ({"nscore": {"ltail": "power", "ltpar": 0}}, "nscore.ltpar"), ({"variogram": None}, "variogram.model_id"),
    ({"secondary": {"field": "CU"}}, "secondary"),
])  # fmt: skip
def test_the_ranges_of_the_parameters(sw: SW, over: dict[str, Any], param: str) -> None:
    if "secondary" in over:  # a secondary for the SGS of the kind sgs is a mistake; without `kind` it is co-SGS
        refused(lambda: sp.parse_simulation(body(sw, **over, kind="sgs")), 422, "bad_param", param)
        refused(lambda: sp.parse_simulation(body(sw, **over)), 422, "not_ready", "kind")
        return
    refused(lambda: sp.parse_simulation(body(sw, **over)), 422, "bad_param", param)


def test_the_kinds_that_are_not_connected_yet(sw: SW) -> None:
    refused(lambda: sp.parse_simulation(body(sw, kind="sis")), 422, "not_ready", "kind")
    refused(lambda: sp.parse_simulation(body(sw, kind="cosgs", secondary={"field": "CU", "rho": 0.5})), 422,
            "not_ready", "kind")  # fmt: skip
    refused(lambda: sp.parse_simulation(body(sw, kind="gauss")), 422, "bad_param", "kind")
    refused(lambda: sp.parse_simulation(body(sw, secondary={"field": "CU", "rho": 1.5}, kind="cosgs")), 422,
            "bad_param", "secondary.rho")  # fmt: skip


def test_the_run_and_the_post_bodies() -> None:
    run = sp.parse_run({"simulation_id": 3, "expected_version": 2})
    assert (run.simulation, run.simulation_id, run.expected_version) == (None, 3, 2)
    assert sp.parse_run({"simulation": {"a": 1}}).simulation == {"a": 1}
    for bad in ({}, {"simulation_id": 1, "simulation": {}}, {"simulation": "x"}):
        refused(lambda bad=bad: sp.parse_run(bad), 422, "bad_param")  # type: ignore[misc]
    refused(lambda: sp.parse_run({"simulation_id": 1, "expected_version": 0}), 422, "bad_param", "expected_version")
    post = sp.parse_post({"expected_version": 4})
    assert (post.stats, post.thresholds, post.prefix, post.replace) == (sp.BASE_STATS, (), None, False)
    post = sp.parse_post({"expected_version": 4, "thresholds": [1, "2,5", 1.0, -0.5]})
    assert post.stats[-1] == "prob" and post.thresholds == (1.0, 2.5, -0.5)  # a repeat goes
    assert [c["name"] for c in sp.post_columns("AU_SGS_", post.stats, post.thresholds, "г/т")] == [
        "AU_SGS_MEAN", "AU_SGS_STD", "AU_SGS_P10", "AU_SGS_P50", "AU_SGS_P90", "AU_SGS_PROB_GT_1",
        "AU_SGS_PROB_GT_2_5", "AU_SGS_PROB_GT_-0_5"]  # fmt: skip
    only = sp.parse_post({"expected_version": 1, "stats": ["p90", "mean", "p90"], "thresholds": [3]})
    assert only.stats == ("p90", "mean") and only.thresholds == ()  # no `prob`: the thresholds are not used
    cases = [({"expected_version": None}, "expected_version"), ({"expected_version": 1.5}, "expected_version"),
             ({"prefix": "A B"}, "prefix"), ({"stats": ["max"]}, "stats.0"), ({"stats": []}, "stats"),
             ({"stats": ["prob"]}, "thresholds"), ({"replace": "yes"}, "replace"),
             ({"thresholds": ["x"]}, "thresholds.0"), ({"thresholds": list(range(21))}, "thresholds")]
    for over, param in cases:
        refused(lambda over=over: sp.parse_post({"expected_version": 1, **over}),  # type: ignore[misc]
                422, "bad_param", param)  # fmt: skip


# --- the row: create, read, list ---------------------------------------------------------------------------------


def test_create_reads_the_project_and_keeps_a_copy(sw: SW) -> None:
    ident = make(sw)
    got = get(sw, ident)
    meta = got["meta"]
    assert (got["name"], got["version"], got["stale"], got["source_lost"], got["file"]) == (
        "AU · SGS · руда", 1, False, False, None)  # fmt: skip
    assert meta["v"] == 1 and meta["algo"] == "sim-1" and meta["last_run"] is None and meta["name"] == got["name"]
    assert (meta["kind"], meta["n_real"], meta["seed"], meta["nodes"]) == ("sgs", 4, 777, 8)
    assert meta["samples"]["table"] == str(sw.a) and meta["samples"]["unit"] == "г/т"
    with dialect.read_tx(sw.project.engine) as conn:
        _name, vmeta = ms.meta_of(conn, sw.vy)
    v = meta["variogram"]
    assert (v["model_id"], v["sha"], v["scale"], v["share"]) == (sw.vy, vmeta["sha256"], "y", None)
    assert v["snapshot"]["structures"][0]["a1"] == 40.0
    s = meta["search"]  # `from: model`: the structure with the largest a1
    assert (s["from"], s["r1"], s["r2"], s["r3"]) == ("model", 40.0, 40.0, 25.0) and len(s["passes"]) == 1
    assert len(meta["origin"]) == 3 and set(meta["versions"]) == {"numpy", "scipy", "numba"}
    kinds = sorted({i["kind"] for i in meta["inputs"]})
    assert kinds == sorted({"dh_table", "dh_set", "object:variogram_model", "object:block_model", "bm_field"})
    [row] = rd(sw, st.list_rows)
    assert (row["id"], row["kind"], row["label"], row["field"], row["n_real"], row["last_run"]) == (
        ident, "sgs", "руда", "AU", 4, None)  # fmt: skip
    assert rd(sw, st.list_rows, sw.model + 99) == [] and len(rd(sw, st.list_rows, sw.model)) == 1


def test_names(sw: SW) -> None:
    a = make(sw)
    b = make(sw)  # the default name taken: a number is added
    assert get(sw, b)["name"] == "AU · SGS · руда (2)"
    refused(lambda: make(sw, name="au · sgs · РУДА"), 409, "name_taken", "name")
    assert get(sw, make(sw, name="  Моя   модель "))["name"] == "Моя модель"
    assert get(sw, make(sw, target={}))["name"] == "AU · SGS"  # no domain of the blocks
    assert a != b


def test_the_checks_against_the_project(sw: SW) -> None:
    samples = body(sw)["samples"]

    def smp(**kw: Any) -> dict[str, Any]:
        return {**samples, **kw}

    def bad(status: int, code: str, param: str, **over: Any) -> None:
        refused(lambda: make(sw, **over), status, code, param)

    bad(404, "block_model_not_found", "model_id", model_id=sw.model + 999)
    bad(422, "field_not_found", "target.domain_field", target={"domain_field": "НЕТ", "label": "x"})
    bad(422, "domain_unmapped", "target.label", target={"domain_field": "DOM", "label": "золото"})
    bad(404, "filter_not_found", "target.filter_id", target={"filter_id": 9999})
    bad(404, "set_not_found", "samples.set_id", samples=smp(set_id=sw.set_id + 999))
    bad(404, "table_not_found", "samples.table", samples=smp(table="Нет такой"))
    bad(422, "field_not_found", "samples.field", samples=smp(field="PB"))
    bad(404, "model_not_found", "variogram.model_id", variogram={"model_id": sw.vy + 999})
    bad(422, "domain_unmapped", "samples.domain.values",
        samples=smp(domain={"table": str(sw.b), "field": "ZONE", "values": ["нет"]}))  # fmt: skip
    with dialect.read_tx(sw.project.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(objects).where(
            objects.c.kind == "simulation")).scalar() == 0  # the refusals left nothing


def test_a_grid_with_tensors_is_not_simulated(sw: SW) -> None:
    with dialect.write_tx(sw.project.engine) as conn:
        grid = {"corner": [0.0, 0.0, 0.0], "tensors": {"x": [1.0, 2.0] * 15, "y": [3.0] * 20, "z": [0.5] * 20}}
        var = bm.put_block_model(conn, sw.store, grid, [{"name": "AU", "kind": "number"}],
                                 np.arange(5, dtype=np.uint64), {"AU": np.ones(5)}, name="Переменный")  # fmt: skip
    err = refused(lambda: make(sw, model_id=var, target={}), 422, "variable_grid", "model_id")
    assert "моделирование" in err.message


# --- the copy of the variogram model (Д-38) ----------------------------------------------------------------------


def test_the_scale_of_the_model_and_a_model_that_is_gone(sw: SW) -> None:
    z = sw.vz  # the model in the units of the field
    err = refused(lambda: make(sw, variogram={"model_id": z}), 422, "variogram_scale", "variogram.model_id")
    assert "нормальных оценках" in err.message
    ident = make(sw, name="В Y")
    sha = get(sw, ident)["meta"]["variogram"]["sha"]
    new = {**MODEL, "structures": [{**MODEL["structures"][0], "a1": 200, "a2": 100}]}
    kind, parsed = exchange.api_model(new)
    with dialect.write_tx(sw.project.engine) as conn:  # a new version of the model: «устарел» until `PUT` takes it
        ms.update(conn, sw.store, sw.vy, expected_version=1, model=(kind, exchange.model_form(kind, parsed)))
    got = get(sw, ident)
    assert (got["stale"], got["stale_reason"], got["source_lost"]) == (
        True, "изменена модель вариограммы «AU · Y»", False)  # fmt: skip
    assert put(sw, ident, 1)["version"] == 2
    again = get(sw, ident)
    assert again["stale"] is False and again["meta"]["variogram"]["sha"] != sha
    assert again["meta"]["search"]["r1"] == 200.0 and again["meta"]["search"]["r2"] == 100.0
    assert [u["id"] for u in rd(sw, st.usage_of_variogram, sw.vy)] == [ident]
    with dialect.write_tx(sw.project.engine) as conn:  # the model deleted: the row keeps its copy, «устарел»
        ms.delete(conn, sw.vy)
    gone = get(sw, ident)
    assert (gone["stale"], gone["stale_reason"], gone["source_lost"]) == (
        True, "модель вариограммы «AU · Y» удалена", False)  # fmt: skip
    assert put(sw, ident, 2, name="Без модели")["name"] == "Без модели"
    assert get(sw, ident)["meta"]["variogram"]["snapshot"]["structures"][0]["a1"] == 200.0


# --- «устарел» and the graph -------------------------------------------------------------------------------------


def test_freshness_by_the_inputs_and_by_the_parameters(sw: SW) -> None:
    ident = make(sw)

    def state() -> tuple[bool, str | None, bool]:
        g = get(sw, ident)
        return g["stale"], g["stale_reason"], g["source_lost"]

    def bump(table: Any, key: Any, **vals: Any) -> None:
        with dialect.write_tx(sw.project.engine) as conn:
            conn.execute(sa.update(table).where(table.c.id == key).values(**vals))

    assert state() == (False, None, False)
    bump(dh_tables, sw.b, rev=dh_tables.c.rev + 1)
    assert state() == (True, "изменена таблица «Литология»", False)
    bump(dh_tables, sw.b, rev=dh_tables.c.rev - 1)
    assert state() == (False, None, False)
    bump(objects, sw.set_id, version=objects.c.version + 1)
    assert state() == (True, "изменены траектории", False)
    bump(objects, sw.set_id, version=objects.c.version - 1)
    # the parameters against the last run
    meta = get(sw, ident)["meta"]
    with dialect.write_tx(sw.project.engine) as conn:
        staged = (sw.store.tmp_dir / "x", "a" * 64, 0)
        sw.store.tmp_dir.mkdir(parents=True, exist_ok=True)
        staged[0].write_bytes(b"")
        st.commit_run(conn, sw.store, ident, staged, {"at": "2026-11-25T10:00:00+00:00", "job": "j-1", "nodes": 5,
                                                       "realizations": 4}, meta["inputs"], meta["origin"])  # fmt: skip
    got = get(sw, ident)
    assert (got["version"], got["stale"], got["meta"]["last_run"]["job"]) == (1, False, "j-1")  # no version bump
    assert got["file"] == {"role": "real", "sha": "a" * 64, "rows": 5,
                           "columns": ["p", "dom", "r001", "r002", "r003", "r004"]}  # fmt: skip
    assert rd(sw, st.list_rows)[0]["last_run"] == {"at": "2026-11-25T10:00:00+00:00", "nodes": 5, "realizations": 4}
    put(sw, ident, 1, seed=1)
    assert state() == (True, "изменены параметры", False)
    with dialect.write_tx(sw.project.engine) as conn:  # a source gone
        bm.delete_block_model(conn, sw.model)
    assert state() == (True, "источник удалён", True)
    assert rd(sw, st.list_rows)[0]["source_lost"] is True


def test_the_graph_gets_the_row_and_delete_takes_the_blob(sw: SW) -> None:
    from bf_core import lineage
    from bf_core.db.schema import lineage as lineage_t

    ident = make(sw)
    with dialect.read_tx(sw.project.engine) as conn:
        rows = conn.execute(sa.select(lineage_t.c.src_kind).where(
            lineage_t.c.dst_kind == "object:simulation", lineage_t.c.dst_id == ident)).all()  # fmt: skip
        state = lineage.status(conn, [lineage.Node("object:simulation", ident)])
    assert sorted({r.src_kind for r in rows}) == sorted({"dh_table", "dh_set", "object:variogram_model",
                                                         "object:block_model", "bm_field"})  # fmt: skip
    assert [s.state for s in state.values()] == ["current"]
    with dialect.write_tx(sw.project.engine) as conn:
        assert st.delete(conn, ident) == {"deleted": ident}
    refused(lambda: get(sw, ident), 404, "simulation_not_found")
    with dialect.read_tx(sw.project.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(lineage_t).where(
            lineage_t.c.dst_kind == "object:simulation")).scalar() == 0
        assert conn.execute(sa.select(sa.func.count()).select_from(blob_owners).where(
            blob_owners.c.owner_id == ident, blob_owners.c.role == "real")).scalar() == 0


# --- update: the version, the keys, the origin -------------------------------------------------------------------


def test_update_keys_conflict_and_origin(sw: SW, monkeypatch: pytest.MonkeyPatch) -> None:
    ident = make(sw)
    reads: list[Any] = []
    real = sc.sc.scan
    monkeypatch.setattr(sc.sc, "scan", lambda *a, **k: (reads.append(a[1]), real(*a, **k))[1])
    assert put(sw, ident, 1, seed=5, n_real=7) == {"id": ident, "name": "AU · SGS · руда", "version": 2}
    meta = get(sw, ident)["meta"]
    assert (meta["seed"], meta["n_real"], meta["nodes"], meta["target"]["label"]) == (5, 7, 8, "руда")
    assert reads == []  # the samples are the same: the file is not read again, the origin stays
    soft = {**meta["samples"], "soft": {"domains": ["перв"], "dist": 5}}
    put(sw, ident, 2, samples=soft, name="Мягкая")
    assert len(reads) == 1 and get(sw, ident)["name"] == "Мягкая"
    err = refused(lambda: put(sw, ident, 1, seed=1), 409, "version_conflict")
    assert err.extra == {"version": 3}
    for bad in (None, 0, True, "2"):
        refused(lambda bad=bad: put(sw, ident, bad), 422, "bad_param", "expected_version")  # type: ignore[misc]
    other = make(sw, name="Другая")
    refused(lambda: put(sw, ident, 3, name="ДРУГАЯ"), 409, "name_taken", "name")
    refused(lambda: put(sw, ident, 3, n_real=101), 422, "bad_param", "n_real")
    refused(lambda: put(sw, other + 99, 1), 404, "simulation_not_found")
    assert get(sw, ident)["version"] == 3  # the refusals changed nothing
    with dialect.write_tx(sw.project.engine) as conn:  # the version guard of the UPDATE: a write between
        row = sc.stored(conn, ident)
        conn.execute(sa.update(objects).where(objects.c.id == ident).values(version=9))
        err = refused(lambda: st.update(conn, sw.project, ident, {}, int(row.version)), 409, "version_conflict")
        assert err.extra == {"version": 9}


def test_the_store_imports_nothing_heavy() -> None:
    """The core reports BF-READY before numpy: the store, the spec, the job and the post load none of the heavy modules
    (the process of the API registers the two handlers)."""
    heavy = ("numba", "numpy", "pandas", "pyarrow", "duckdb", "scipy")
    for module in ("bf_core.geostat.simulation.store", "bf_core.geostat.simulation.spec",
                   "bf_core.geostat.simulation.job", "bf_core.geostat.simulation.post",
                   "bf_core.geostat.simulation.routes"):  # fmt: skip
        code = f"import sys; import {module};print(','.join(m for m in {heavy!r} if m in sys.modules))"
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        assert "numba" not in r.stdout, f"{module} imported numba: {r.stdout.strip()}"


# --- `.bfggis` ---------------------------------------------------------------------------------------------------


def test_remap_of_the_meta_and_a_lost_reference(sw: SW) -> None:
    ident = make(sw)
    meta = get(sw, ident)["meta"]
    objs = {sw.model: 70, sw.set_id: 30, sw.vy: 210}
    tabs = {sw.a: 120, sw.b: 140}
    new = remap.estimation_meta(meta, objs.get, tabs.get)
    assert (new["model_id"], new["samples"]["set_id"], new["variogram"]["model_id"]) == (70, 30, 210)
    assert (new["samples"]["table"], new["samples"]["domain"]["table"]) == ("120", "140")
    assert [i["fp"] for i in new["inputs"]] == [i["fp"] for i in meta["inputs"]]  # the states survive the import
    lost = remap.estimation_meta(meta, {}.get, {}.get)
    assert (lost["model_id"], lost["samples"]["set_id"], lost["variogram"]["model_id"]) == (None, None, None)
    with dialect.write_tx(sw.project.engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == ident).values(meta=lost))
    got = get(sw, ident)  # the row reads, the source is lost
    assert (got["stale"], got["stale_reason"], got["source_lost"]) == (True, "источник удалён", True)
    # the kind is renumbered by `remap_geostat` with the rows of the estimation
    with dialect.write_tx(sw.project.engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == ident).values(meta=meta))
        n = remap.remap_geostat(conn, objs, tabs)
    assert n >= 1 and get(sw, ident)["meta"]["model_id"] == 70


# --- the same on SQLite and PostgreSQL ---------------------------------------------------------------------------


@pytest.mark.db
def test_store_on_the_database(db_url: str, tmp_path: Path) -> None:
    """The store on SQLite and PostgreSQL: create, update with the version, names, delete, «устарел» (`-m db`;
    BF_TEST_PG)."""
    w = world(tmp_path, db_url)
    try:
        ident = make(w)
        other = make(w, name="Порода", target={"domain_field": "DOM", "label": "порода"})
        assert put(w, ident, 1, seed=3)["version"] == 2
        refused(lambda: put(w, ident, 1), 409, "version_conflict")
        refused(lambda: make(w, name="порода"), 409, "name_taken")
        assert [r["name"] for r in rd(w, st.list_rows)] == ["AU · SGS · руда", "Порода"]
        with dialect.write_tx(w.project.engine) as conn:
            conn.execute(sa.update(dh_tables).where(dh_tables.c.id == w.a).values(rev=dh_tables.c.rev + 1))
        assert get(w, ident)["stale_reason"] == "изменена таблица «Пробы»"
        with dialect.write_tx(w.project.engine) as conn:
            assert st.delete(conn, other) == {"deleted": other}
        refused(lambda: get(w, other), 404, "simulation_not_found")
    finally:
        w.project.engine.dispose()


# --- `.bfggis` there and back ----------------------------------------------------------------------------------------

vc = vs.vc  # the core with an open project and the set of `test_vario_store`


def test_bfggis_roundtrip_keeps_realizations_and_fields(vc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """A project with a simulation that ran and a post-processing → `.bfggis` → another project: the row is renumbered
    (model, set, tables, variogram), the file of the realizations comes with it (the same SHA-256, the same rows), the
    row and its fields are «актуален», the post-processing runs in the new project."""
    from bf_core import lineage
    from bf_core.blocks.model import Model
    from bf_core.geostat.simulation import job, post, real
    from bf_core.projects.service import projects_of
    from bf_core.storage.blobs import BlobStore
    from tests.estutil import body as estimate_body
    from tests.estutil import fill_world, vmodel
    from tests.test_import_job import wait_job

    w = fill_world(vc.project, vc.set_id, vc.a, vc.b)
    vy = vmodel(w, "Золото Y", transform="nscore")
    raw = {**estimate_body(w), "variogram": {"model_id": vy}, "n_real": 3, "nodes": 6, "name": "Мод"}
    with dialect.write_tx(w.project.engine) as conn:
        ident = int(st.create(conn, w.project, sp.parse_simulation(raw))["id"])
    out = job.run_simulate(w.project.engine, w.project.folder, {"simulation_id": ident}, threads=2)
    with dialect.read_tx(w.project.engine) as conn:
        version = Model.open(conn, w.store, w.model).version
    post.run_post(w.project.engine, w.project.folder, {"simulation_id": ident, "expected_version": version,
                                                       "stats": ["mean"]})  # fmt: skip
    before = get(w, ident)
    sha = rd(w, st.real_sha, ident)
    assert sha is not None and before["stale"] is False
    r = vc.post(API + "/projects/export", json={"path": str(tmp_path / "Моделирование")}).json()
    job_ = wait_job(vc, r["source"], r["id"])
    assert job_["status"] == "done", job_
    r = vc.post(API + "/projects/import", json={"path": job_["result"]["path"], "mode": "copy"}).json()
    job_ = wait_job(vc, r["source"], r["id"])
    assert job_["status"] == "done", job_
    maps = job_["result"]["id_maps"]
    objs = {int(k): int(v) for k, v in maps["objects"].items()}
    tabs = {int(k): int(v) for k, v in maps["dh_tables"].items()}
    assert vc.post(f"{API}/projects/{job_['result']['uid']}/open").status_code == 200
    project = projects_of(vc.core).require_current()
    n = SW(project, BlobStore(project.folder), objs[vc.set_id], tabs[vc.a], tabs[vc.b], objs[w.model], 0, objs[w.vz])
    got = get(n, objs[ident])
    m = got["meta"]
    assert got["name"] == "Мод" and (got["stale"], got["source_lost"]) == (False, False), got
    assert (m["model_id"], m["samples"]["set_id"], m["samples"]["table"], m["variogram"]["model_id"]) == (
        n.model, n.set_id, str(n.a), objs[vy])  # fmt: skip
    assert m["variogram"]["sha"] == before["meta"]["variogram"]["sha"] and m["last_run"]["nodes"] == out["nodes"]
    assert [(i["kind"], i["fp"]) for i in m["inputs"]] == [(i["kind"], i["fp"]) for i in before["meta"]["inputs"]]
    assert got["file"] == {**before["file"]}  # the same SHA-256, the same rows and columns: the blob came with it
    after = real.Reals(n.store, got["file"]["sha"])
    assert (after.n, after.n_real) == (out["nodes"], 3) and np.array_equal(after.p(), real.Reals(w.store, sha).p())
    assert np.array_equal(after.rows(0, after.n), real.Reals(w.store, sha).rows(0, after.n))
    with dialect.read_tx(project.engine) as conn:
        for node in (lineage.Node("object:simulation", objs[ident]),
                     lineage.Node("bm_field", n.model, "au_sgs_mean")):  # fmt: skip
            assert lineage.status(conn, [node])[node].state == "current", node
        assert [u["id"] for u in st.usage_of_variogram(conn, objs[vy])] == [objs[ident]]
        version = Model.open(conn, n.store, n.model).version
    again = post.run_post(project.engine, project.folder, {"simulation_id": objs[ident], "expected_version": version,
                                                           "stats": ["std"]})  # fmt: skip
    assert again["fields"] == ["AU_SGS_STD"] and again["model_id"] == n.model
