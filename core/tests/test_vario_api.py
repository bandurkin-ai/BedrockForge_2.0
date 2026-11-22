"""The map, the fit, the back-transform, the curve, «Перестроить» (подшаг 16.15; ADR 0022 §4–5, §11–16, решения
11–16, 24; e16-api.md): the job `variography.map` — its stages in order, the form = the kernel on the same points,
the record of the cache and the repeat by the key (with the lag given — nothing read), a cancel at any tick leaves
nothing; `/fit`, `/back`, `/curve` synchronously = the kernels; a key of the map is not a calc of the axes; the
rebuild of a stale model in a `spawn` worker — new points, the model unchanged, the residual of the new points;
`source_lost` 409; the defaults of the weights (16.21); one hash at 1 and 4 threads (`-m windows`); the map job on
SQLite and PostgreSQL (`-m db`)."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect, migrate, schema
from bf_core.db.schema import dh_tables
from bf_core.drillholes import store as dh
from bf_core.geostat.variogram import exchange, fit, hermite, points, spec, varmap
from bf_core.geostat.variogram import jobs as vj
from bf_core.geostat.variogram import jobs_map as vm
from bf_core.geostat.variogram import store as ms
from bf_core.jobs.context import JobCancelled
from bf_core.projects.service import projects_of
from tests.dbutil import make_engine
from tests.test_import_job import wait_job

API = "/api/v1"
GS = API + "/geostat/variography"
MODULES = ("bf_core.geostat.variogram.jobs", "bf_core.geostat.variogram.jobs_map")
MAP_STAGES = ["Чтение таблицы", "Траектории", "Пары: веер", "Пары: карта", "Запись"]
MODEL = {"type": "semivariogram", "c0": 0.05, "common_axes": True,
         "structures": [{"type": "spherical", "c": 0.3, "ang1": 0, "ang2": -90, "ang3": 0, "a1": 60, "a2": 40,
                         "a3": 40}]}  # fmt: skip


def noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


@pytest.fixture(autouse=True)
def _roles(monkeypatch: pytest.MonkeyPatch) -> None:
    if not {ms.VARIO, ms.NSCORE} <= schema.BLOB_ROLES:
        monkeypatch.setattr(schema, "BLOB_ROLES", schema.BLOB_ROLES | {ms.VARIO, ms.NSCORE})


def fill(engine: sa.Engine, side: int = 4, per_hole: int = 40, seed: int = 3) -> int:
    """side² vertical holes 20 m apart, `per_hole` samples of 2 m (a trend with depth and a noise), DECL_W."""
    rng = np.random.Generator(np.random.PCG64(seed))
    names = [f"С-{i:03d}" for i in range(side * side)]
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
        dh.replace_collars(conn, set_id, [dh.CollarRow(h, x=500_000.0 + 20.0 * (i % side),
                                                       y=6_700_000.0 + 20.0 * (i // side), z=300.0,
                                                       td=2.0 * per_hole, azimuth=0.0, dip=-90.0)
                                          for i, h in enumerate(names)])  # fmt: skip
    holes = [h for h in names for _ in range(per_hole)]
    depth = np.tile(np.arange(per_hole) * 2.0, side * side)
    au = np.exp(np.sin(depth / 17.0) + 0.5 * rng.standard_normal(depth.size))
    w = rng.uniform(0.5, 1.5, depth.size)
    dh.write_table(engine, set_id, dh.TableData(
        name="Пробы", kind="assay", holes=holes, from_m=depth.tolist(), to_m=(depth + 2.0).tolist(),
        columns=[dh.ColumnSpec("AU", "number", "г/т"), dh.ColumnSpec("DECL_W", "number")],
        values=[au.tolist(), w.tolist()]), job_id=None, tick=noop)  # fmt: skip
    return set_id


@pytest.fixture()
def proj(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    set_id = fill(engine)
    yield SimpleNamespace(engine=engine, uid=f"test:{tmp_path}", folder=tmp_path), set_id
    engine.dispose()


def body(set_id: int, **kw: Any) -> dict[str, Any]:
    return {"set_id": set_id, "table": "Пробы", "field": "AU", **kw}


def run_map(project: Any, req: dict[str, Any], cancel_at: int | None = None,
            seen: list[str | None] | None = None, **kw: Any) -> dict[str, Any]:  # fmt: skip
    seen = [] if seen is None else seen

    def tick(_p: float | None = None, m: str | None = None) -> None:
        seen.append(m)
        if cancel_at is not None and len(seen) >= cancel_at:
            raise JobCancelled()

    return vm.run_map(project.engine, project.folder, {"body": req}, tick=tick, **kw)


def stages_of(seen: list[str | None]) -> list[str]:
    return [m for k, m in enumerate(seen) if m is not None and m not in seen[:k]]


def cached(folder: Path) -> list[str]:
    d = ms.cache_dir(folder)
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


# --- the map -----------------------------------------------------------------------------------------------------


def test_map_stages_form_record_and_repeat(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id = proj
    req = body(set_id, plane={"ang1": 0, "ang2": -90, "ang3": 0}, nlag=6, fan={"n": 8},
               map={"nu": 5, "nv": 5, "min_pairs": 2})  # fmt: skip
    seen: list[str | None] = []
    out = run_map(project, req, seen=seen)
    assert stages_of(seen) == MAP_STAGES and out["fresh"] is False
    assert set(out["stages"]) == {"read", "trajectories", "fan", "map", "write", "total"}
    with dialect.read_tx(project.engine) as conn:
        raw = spec.parse_map(conn, req)
        inputs = vj.inputs_of(conn, raw.data)
    p = points.load(project, raw.data)
    s = raw.resolved(spec.defaults_for(p.median_length))
    ref = ms._plain(varmap.calculate(p, s).to_json())
    assert out["key"] == vj.key_of(s, inputs) and out["params"] == s.body and out["params"]["lag"] == 10.0
    assert {k: out[k] for k in ref} == ref
    assert out["counts"] == p.counts and out["sigma2"] == p.sigma2 and out["origin"] == p.origin.tolist()
    assert len(out["fan"]["directions"]) == 8 and len(out["rose"]["rays"]) == 8
    assert len(out["map"]["u"]) == 11 and len(out["map"]["gamma"]) == 11 and out["map"]["thickness"] == 20.0
    assert cached(project.folder) == [f"{out['key']}.json"]
    again = run_map(project, req)
    assert again["fresh"] is True and {k: v for k, v in again.items() if k not in ("fresh", "stages")} == {
        k: v for k, v in out.items() if k not in ("fresh", "stages")}  # fmt: skip
    # the lag given: the key before the table, a repeat reads nothing
    req2 = {**req, "lag": 10}
    first = run_map(project, req2)
    seen2: list[str | None] = []
    second = run_map(project, req2, seen=seen2)
    assert second["fresh"] is True and second["key"] == first["key"] and seen2 == [] and set(second["stages"]) == {
        "total"}  # fmt: skip
    # the same parameters in an experimental calc — another key (op)
    exp = vj.run_experimental(project.engine, project.folder, {"body": body(set_id, nlag=6)})
    assert exp["key"] != out["key"]


def test_map_only_and_nscore(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id = proj
    seen: list[str | None] = []
    out = run_map(project, body(set_id, what=["map"], transform="nscore", map={"nu": 3, "nv": 3}), seen=seen)
    assert stages_of(seen) == ["Чтение таблицы", "Траектории", "Нормальные оценки", "Пары: карта", "Запись"]
    assert out["fan"] is None and out["rose"] is None and out["sigma2"] == 1.0 and out["transform"] == "nscore"
    assert set(out["stages"]) == {"read", "trajectories", "nscore", "map", "write", "total"}


def test_map_cancel_leaves_nothing(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id = proj
    req = body(set_id, nlag=4, fan={"n": 4}, map={"nu": 3, "nv": 3})
    seen: list[str | None] = []
    run_map(project, req, seen=seen)
    total = len(seen)
    for f in project.folder.glob("cache/variography/*"):
        f.unlink()
    for at in range(1, total + 1):
        with pytest.raises(JobCancelled):
            run_map(project, req, cancel_at=at)
        assert cached(project.folder) == [], at


def test_map_refusals(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id = proj
    for req, code, param in ((body(set_id, fan={"n": 3}), "bad_param", "fan.n"),
                             (body(set_id, map={"thickness": "∞", "cell": 0}), "bad_param", "map.cell"),
                             (body(set_id, field="НЕТ"), "field_not_found", "field")):  # fmt: skip
        with pytest.raises(ApiError) as e:
            run_map(project, req)
        assert (e.value.code, e.value.extra.get("param")) == (code, param) and e.value.hint


# --- the routes ----------------------------------------------------------------------------------------------------


@pytest.fixture()
def va(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A core with an open project and the set; the jobs registered here and in the worker until the line of the
    orchestrator (`jobs/registry.py`)."""
    import importlib

    from bf_core.jobs.registry import HANDLER_MODULES

    extra = [m for m in MODULES if m not in HANDLER_MODULES]
    if extra:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", ",".join(extra))
    for m in MODULES:
        importlib.import_module(m)
    assert pclient.post(API + "/projects", json={"name": "Вариография"}).status_code == 200
    project = projects_of(pclient.core).require_current()
    pclient.set_id = fill(project.engine)
    pclient.project = project
    return pclient


def exp(c, **kw: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """A calc of the axes in the process, into the cache of the open project."""
    req = {**body(c.set_id), "axes": {"ang1": 0, "ang2": -90, "ang3": 0}, "nlag": 8, "min_pairs": 5, **kw}
    return vj.run_experimental(c.project.engine, c.project.folder, {"body": req})


def test_map_job_in_a_worker(va) -> None:  # type: ignore[no-untyped-def]
    c = va
    req = {**body(c.set_id), "nlag": 4, "fan": {"n": 4}, "map": {"nu": 3, "nv": 3}}
    r = c.post(GS + "/map", json=req)
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=240)
    assert job["status"] == "done", job
    res = job["result"]
    assert res["fresh"] is False and res["plane"] == {"ang1": 0.0, "ang2": 0.0, "ang3": 0.0}
    assert ms.result_path(c.project.folder, res["key"]).is_file()
    r = c.post(GS + "/map", json={**req, "fan": {"n": 100}})
    assert r.status_code == 422 and r.json()["param"] == "fan.n" and r.json()["hint"]
    r = c.post(GS + "/map", json={**req, "what": ["нет"]})
    assert r.status_code == 422 and r.json()["param"] == "what"
    # a key of the map is not a calc of the axes: neither a fit nor a model
    r = c.post(GS + "/fit", json={"key": res["key"], "model": MODEL})
    assert r.status_code == 422 and r.json()["param"] == "key"
    r = c.post(GS + "/models", json={"model": MODEL, "exp_key": res["key"]})
    assert r.status_code == 422 and r.json()["param"] == "exp_key"


def test_fit_route_is_the_kernel(va) -> None:  # type: ignore[no-untyped-def]
    c = va
    out = exp(c)
    form = {k: v for k, v in out.items() if k not in ("fresh", "stages")}
    req = {"key": out["key"], "type": "semivariogram", "model": MODEL, "weights": "n_h", "fix": ["c0"]}
    r = c.post(GS + "/fit", json=req)
    assert r.status_code == 200, r.text
    got = r.json()
    ref = fit.fit_variogram(form, MODEL, kind="semivariogram", weights="n_h", fix=["c0"])
    assert got == json.loads(json.dumps(ref)) and got["model"]["c0"] == 0.05 and got["fixed"] == ["c0"]
    assert set(got) >= {"model", "residual", "n", "starts", "warnings"}
    r = c.post(GS + "/fit", json={**req, "key": "0" * 64})
    assert r.status_code == 404 and r.json()["code"] == "result_not_found" and r.json()["param"] == "key"
    r = c.post(GS + "/fit", json={**req, "key": "нет"})
    assert r.status_code == 422 and r.json()["param"] == "key"
    r = c.post(GS + "/fit", json={**req, "weights": "w"})
    assert r.status_code == 422 and r.json()["param"] == "weights"
    bad_model = {**MODEL, "structures": [{**MODEL["structures"][0], "a2": -1}]}
    r = c.post(GS + "/fit", json={**req, "model": bad_model})
    assert r.status_code == 422 and r.json()["param"] == "model.structures.0.a2" and r.json()["hint"]
    r = c.post(GS + "/fit", json={**req, "type": "nscore"})
    assert r.status_code == 422 and r.json()["param"] == "type"


def test_back_route_is_the_kernel_with_the_table_of_the_record(va) -> None:  # type: ignore[no-untyped-def]
    c = va
    out = exp(c, transform="nscore")
    form = {k: v for k, v in out.items() if k not in ("fresh", "stages")}
    with dialect.read_tx(c.project.engine) as conn:
        data = spec.parse_data(conn, {**body(c.set_id), "transform": "nscore"})
    p = points.load(c.project, data)
    table = vm.table_of(c.project.folder, out["key"])
    assert np.array_equal(table.values, p.transform.values) and np.array_equal(table.scores, p.transform.scores)
    assert (table.zmin, table.zmax) == (p.transform.zmin, p.transform.zmax)
    y = {"type": "nscore", "c0": 0.2, "structures": [{**MODEL["structures"][0], "c": 0.8}]}
    r = c.post(GS + "/back", json={"key": out["key"], "model": y})
    assert r.status_code == 200, r.text
    got = r.json()
    ref = hermite.back_variogram(form, y, p.transform)
    assert got == json.loads(json.dumps(ms._plain(ref)))
    assert got["model"]["type"] == "semivariogram" and len(got["curves"]) == 3 and 0 < got["share"] <= 1
    plain = exp(c)
    r = c.post(GS + "/back", json={"key": plain["key"], "model": y})
    assert r.status_code == 422 and r.json()["param"] == "key"
    r = c.post(GS + "/back", json={"key": out["key"], "model": {**y, "type": "pairwise"}})
    assert r.status_code == 422 and r.json()["param"] == "model.type"


def test_curve(va) -> None:  # type: ignore[no-untyped-def]
    c = va
    r = c.post(GS + "/curve", json={"model": MODEL})
    assert r.status_code == 200, r.text
    dirs = r.json()["directions"]
    _kind, m = exchange.api_model(MODEL)
    assert [d["axis"] for d in dirs] == [1, 2, 3] and (dirs[0]["az"], dirs[0]["plunge"]) == (0.0, 90.0)
    for d in dirs:
        h = np.asarray(d["h"])
        assert h.size == 100 and h[0] == 0.0 and h[-1] == 90.0  # 1,5 × the largest range 60
        assert d["gamma"] == m.gamma_along(h, d["az"], d["plunge"]).tolist() and d["gamma"][0] == 0.0
        assert np.allclose(np.asarray(d["cov"]) + np.asarray(d["gamma"]), m.sill)
    r = c.post(GS + "/curve", json={"model": MODEL, "directions": [{"az": "45", "plunge": "0,5"}], "h": [0, 10, 20]})
    d = r.json()["directions"][0]
    assert d["axis"] is None and (d["az"], d["plunge"]) == (45.0, 0.5) and d["h"] == [0.0, 10.0, 20.0]
    r = c.post(GS + "/curve", json={"model": MODEL, "h_max": 30, "n": 4})
    assert r.json()["directions"][0]["h"] == [0.0, 10.0, 20.0, 30.0]
    nugget = {"c0": 0.5, "structures": []}
    r = c.post(GS + "/curve", json={"model": nugget})
    assert r.status_code == 422 and r.json()["param"] == "h_max" and r.json()["hint"]
    r = c.post(GS + "/curve", json={"model": nugget, "h_max": 10, "n": 2})
    assert r.json()["directions"][0]["gamma"] == [0.0, 0.5]
    for extra, param in (
        ({"n": 1}, "n"),
        ({"h": [-1]}, "h"),
        ({"h_max": 0}, "h_max"),
        ({"directions": [{"az": 400}]}, "directions.0.az"),
        ({"directions": []}, "directions"),
    ):
        r = c.post(GS + "/curve", json={"model": MODEL, **extra})
        assert r.status_code == 422 and r.json()["param"] == param, (extra, r.text)
    r = c.post(GS + "/curve", json={"model": {**MODEL, "c0": -1}})
    assert r.status_code == 422 and r.json()["param"] == "model.c0"


def test_defaults_of_the_weights(va) -> None:  # type: ignore[no-untyped-def]
    """16.21: `weights` absent — DECL_W when the table has it; `weights=` (empty) — no weights; a name — that field."""
    c = va
    q = {"set_id": c.set_id, "table": "Пробы", "field": "AU"}
    assert c.get(GS + "/defaults", params=q).json()["weights"] == "DECL_W"
    got = c.get(GS + "/defaults", params={**q, "weights": ""}).json()
    assert got["weights"] is None and got["has_decl_w"] is True
    assert c.get(GS + "/defaults", params={**q, "weights": "DECL_W"}).json()["weights"] == "DECL_W"
    r = c.get(GS + "/defaults", params={**q, "weights": "нет"})
    assert r.status_code == 422 and r.json()["param"] == "weights"


def test_rebuild_a_stale_model_in_a_worker(va) -> None:  # type: ignore[no-untyped-def]
    c = va
    out = exp(c, transform="nscore")
    y = {"type": "nscore", "c0": 0.2, "structures": [{**MODEL["structures"][0], "c": 0.8}]}
    fitted = c.post(GS + "/fit", json={"key": out["key"], "model": y, "weights": "n_h"}).json()
    back = {k: v for k, v in c.post(GS + "/back", json={"key": out["key"], "model": fitted["model"]}).json().items()
            if k != "curves"}  # fmt: skip
    fit_meta = {"weights": "n_h", "residual": fitted["residual"], "n": fitted["n"], "fixed": []}
    r = c.post(GS + "/models", json={"model": fitted["model"], "exp_key": out["key"], "fit": fit_meta, "back": back,
                                     "name": "Модель"})  # fmt: skip
    mid = r.json()["id"]
    with dialect.read_tx(c.project.engine) as conn:
        meta = ms.meta_of(conn, mid)[1]
        tid = int(meta["source"]["table"])
    residual, n = vm.residual_of({k: v for k, v in out.items() if k not in ("fresh", "stages")}, meta, "n_h")
    assert n == fitted["n"] and math.isclose(residual or 0.0, fitted["residual"], rel_tol=1e-9)
    with dialect.write_tx(c.project.engine) as conn:  # an edit of the table: the model is stale
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == tid).values(rev=dh_tables.c.rev + 1))
    assert c.get(f"{GS}/models/{mid}").json()["stale"] is True
    r = c.post(f"{GS}/models/{mid}/rebuild")
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=240)
    assert job["status"] == "done", job
    res = job["result"]
    assert res["model_id"] == mid and res["key"] != out["key"] and res["fresh"] is False
    assert res["events"] == [["geostat.changed", {"kind": "variogram_model", "id": mid}]]
    got = c.get(f"{GS}/models/{mid}").json()
    assert got["stale"] is False and got["meta"]["exp_key"] == res["key"] and got["version"] == 2
    m = got["meta"]
    assert (m["c0"], m["structures"], m["type"]) == (meta["c0"], meta["structures"], meta["type"])
    assert m["fit"]["n"] == fitted["n"] and math.isclose(m["fit"]["residual"], fitted["residual"], rel_tol=1e-9)
    assert m["back"]["model"] == back["model"] and "curves" not in m["back"]
    assert got["points"]["directions"] == out["directions"]  # the same values: the same points
    # no model — 404; the source lost — 409
    assert c.post(f"{GS}/models/999/rebuild").status_code == 404
    assert c.delete(f"{API}/drillholes/sets/{c.set_id}").status_code == 200
    r = c.post(f"{GS}/models/{mid}/rebuild")
    assert r.status_code == 409 and r.json()["code"] == "source_lost" and r.json()["hint"]


def test_rebuild_body_keeps_the_parameters_and_finds_the_tables(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id = proj
    req = body(set_id, directions=[{"az": 0, "plunge": 90}], lag=4, dh_lag=2, nlag=3, weights="")
    out = vj.run_experimental(project.engine, project.folder, {"body": req})
    record = ms.read_result(project.folder, out["key"])
    meta = {"source": record["source"], "calc": record["calc"], "inputs": record["inputs"],
            "transform": out["transform"]}  # fmt: skip
    with dialect.read_tx(project.engine) as conn:
        b = vm.rebuild_body(conn, meta)
        s = spec.parse_experimental(conn, b)
    assert "axes" not in b and b["weights"] == "" and s.data.weights is None and len(s.directions) == 1
    again = vj.run_experimental(project.engine, project.folder, {"body": b})
    assert again["key"] == out["key"] and again["fresh"] is True
    lost = {**meta, "inputs": {**meta["inputs"], "tables": [{"id": 9999, "name_key": "нет"}]}}
    with dialect.read_tx(project.engine) as conn, pytest.raises(ApiError) as e:
        vm.rebuild_body(conn, lost)
    assert (e.value.code, e.value.status) == ("source_lost", 409)


def _stored_model(project: Any, set_id: int) -> tuple[int, str]:
    from bf_core.storage.blobs import BlobStore

    out = vj.run_experimental(project.engine, project.folder, {"body": body(set_id, axes={"ang1": 0}, nlag=4)})
    kind, model = exchange.api_model(MODEL)
    blobs = BlobStore(project.folder)
    staged = ms.stage(blobs, project.folder, out["key"])
    try:
        with dialect.write_tx(project.engine) as conn:
            mid = ms.create(conn, blobs, staged, (kind, exchange.model_form(kind, model)), name="М")["id"]
    finally:
        staged.discard()
    return int(mid), str(out["key"])


def test_rebuild_writes_over_the_version_it_read(proj, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """A model saved while «Перестроить» computes the points: 409 `version_conflict`, the save kept, nothing
    written over it (ТЗ раздел 17 п. 11)."""
    from bf_core.storage.blobs import BlobStore

    project, set_id = proj
    mid, key = _stored_model(project, set_id)
    real = vj.run_experimental

    def meanwhile(*a: Any, **kw: Any) -> dict[str, Any]:
        out = real(*a, **kw)
        with dialect.write_tx(project.engine) as conn:  # another window renames the model
            ms.update(conn, BlobStore(project.folder), mid, expected_version=1, name="Переименована")
        return out

    monkeypatch.setattr(vj, "run_experimental", meanwhile)
    with pytest.raises(ApiError) as e:
        vm.run_rebuild(project.engine, project.folder, {"model_id": mid})
    assert (e.value.status, e.value.code) == (409, "version_conflict") and "Перестроить" in e.value.hint
    with dialect.read_tx(project.engine) as conn:
        got = ms.get(conn, BlobStore(project.folder), mid)
    assert (got["name"], got["version"], got["meta"]["exp_key"]) == ("Переименована", 2, key)
    monkeypatch.setattr(vj, "run_experimental", real)
    out = vm.run_rebuild(project.engine, project.folder, {"model_id": mid})  # again: the version 2 read, 3 written
    with dialect.read_tx(project.engine) as conn:
        assert ms.versioned(conn, mid)[2] == 3 and out["model_id"] == mid
        with pytest.raises(ApiError) as e:  # the store itself: `UPDATE … WHERE version = …`
            ms.update(conn, BlobStore(project.folder), mid, expected_version=2, name="Старая")
    assert (e.value.code, e.value.extra["version"]) == ("version_conflict", 3)


@pytest.mark.parametrize("op", ["experimental", "map"])
def test_the_key_follows_the_inputs_actually_read(proj, monkeypatch: pytest.MonkeyPatch, op: str) -> None:  # type: ignore[no-untyped-def]
    """The table changed between the early look (`inputs_of`) and `points.load`: the key and the record follow
    what was read — a later calc on the same data finds it in the cache."""
    project, set_id = proj
    req = body(set_id, axes={"ang1": 0}, nlag=4) if op == "experimental" else body(
        set_id, plane={"ang1": 0, "ang2": 0, "ang3": 0}, nlag=4, fan={"n": 4}, map={"nu": 3, "nv": 3})  # fmt: skip
    run = vj.run_experimental if op == "experimental" else vm.run_map
    real = vj.inputs_of

    def early(conn: Any, data: Any) -> dict[str, Any]:
        got = real(conn, data)
        return {**got, "tables": [{**t, "rev": int(t["rev"]) - 1} for t in got["tables"]]}  # before the edit

    monkeypatch.setattr(vj, "inputs_of", early)
    first = run(project.engine, project.folder, {"body": req})
    monkeypatch.setattr(vj, "inputs_of", real)
    again = run(project.engine, project.folder, {"body": req})
    assert again["fresh"] is True and again["key"] == first["key"]
    parse = spec.parse_map if op == "map" else spec.parse_experimental
    with dialect.read_tx(project.engine) as conn:
        assert ms.read_result(project.folder, first["key"])["inputs"] == real(conn, parse(conn, req).data)


# --- threads and databases -------------------------------------------------------------------------------------


def _digest(folder: Path, key: str) -> str:
    return hashlib.sha256(ms.cache_dir(folder).joinpath(f"{key}.json").read_bytes()).hexdigest()


@pytest.mark.windows
def test_map_one_hash_at_1_and_4_threads(tmp_path: Path) -> None:
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    try:
        set_id = fill(engine, side=8, per_hole=60)
        project = SimpleNamespace(engine=engine, uid="t", folder=tmp_path)
        req = body(set_id, plane={"ang1": 30, "ang2": -20, "ang3": 10}, nlag=8, map={"nu": 10, "nv": 10})
        for limit in (spec.PAIRS_MAX, 2e5):
            digests = []
            for threads in (1, 4):
                folder = tmp_path / f"t{threads}-{int(limit)}"
                out = vm.run_map(project.engine, folder, {"body": req}, threads=threads, limit=limit)
                digests.append(_digest(folder, out["key"]))
            assert digests[0] == digests[1], limit
            assert (out["sampled"] is None) == (limit == spec.PAIRS_MAX)
    finally:
        engine.dispose()


@pytest.mark.db
def test_map_job_on_the_database(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    try:
        migrate.upgrade(engine, "project")
        set_id = fill(engine, side=3, per_hole=20)
        req = body(set_id, nlag=4, fan={"n": 4}, map={"nu": 3, "nv": 3})
        out = vm.run_map(engine, tmp_path, {"body": req})
        assert out["fresh"] is False and out["counts"]["used"] == 180 and len(out["fan"]["directions"]) == 4
        again = vm.run_map(engine, tmp_path, {"body": req})
        assert again["fresh"] is True and again["key"] == out["key"]
    finally:
        engine.dispose()
