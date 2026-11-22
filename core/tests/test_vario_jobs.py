"""The job `variography.experimental` and the preview (подшаг 16.14; ADR 0022 §4, §12, решения 6, 13, 14, 24, 25;
e16-api.md): the stages of the job in order, the form = the kernels on the same points, the record of the cache
(`<key>.json`, `.sums.parquet`, `.nscore.parquet`), the repeat by the key (nothing computed, with both lags given —
nothing read), an edit of the table → a new key, a cancel at any tick leaves nothing, the preview over ≤ 20 000
points by the seed (`sampled`) and writing nothing, the routes (a job in a `spawn` worker, `/defaults`, the
refusals), one hash at 1 and 4 threads (`-m windows`), the job on SQLite and PostgreSQL (`-m db`)."""

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
from bf_core.db import dialect, migrate
from bf_core.db.schema import dh_tables
from bf_core.drillholes import store as dh
from bf_core.geostat.variogram import downhole, experimental, points, spec
from bf_core.geostat.variogram import jobs as vj
from bf_core.geostat.variogram import store as ms
from bf_core.jobs.context import JobCancelled
from bf_core.projects.service import projects_of
from tests.dbutil import make_engine
from tests.test_import_job import wait_job
from tests.test_vario_spec import make_project
from tests.test_vario_store import fill

API = "/api/v1"
GS = API + "/geostat/variography"
MODULE = "bf_core.geostat.variogram.jobs"
STAGES = ["Чтение таблицы", "Траектории", "Пары: оси", "Пары: вдоль скважин", "Запись"]


def noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


@pytest.fixture()
def proj(tmp_path: Path):  # type: ignore[no-untyped-def]
    project, set_id, a, b = make_project(tmp_path)
    yield project, set_id, a, b
    project.engine.dispose()


def body(set_id: int, **kw: Any) -> dict[str, Any]:
    return {"set_id": set_id, "table": "Пробы", "field": "AU", **kw}


def run(project: Any, req: dict[str, Any], cancel_at: int | None = None, seen: list[str | None] | None = None,
        **kw: Any) -> dict[str, Any]:  # fmt: skip
    seen = [] if seen is None else seen

    def tick(_p: float | None = None, m: str | None = None) -> None:
        seen.append(m)
        if cancel_at is not None and len(seen) >= cancel_at:
            raise JobCancelled()

    return vj.run_experimental(project.engine, project.folder, {"body": req}, tick=tick, **kw)


def stages_of(seen: list[str | None]) -> list[str]:
    return [m for k, m in enumerate(seen) if m is not None and m not in seen[:k]]


def cached(folder: Path) -> list[str]:
    d = ms.cache_dir(folder)
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def reference(project: Any, req: dict[str, Any]) -> tuple[Any, Any, Any]:
    """The kernels over the points of the job (`points.load`, `experimental.calculate`, `downhole.calculate`)."""
    with dialect.read_tx(project.engine) as conn:
        raw = spec.parse_experimental(conn, req)
    p = points.load(project, raw.data)
    s = raw.resolved(spec.defaults_for(p.median_length))
    e = experimental.calculate(p, vj.directions_of(s), s.lags.lag, s.lags.nlag, s.lags.tol, seed=s.seed)
    d = downhole.calculate(p, s.dh.lag, s.dh.nlag, s.dh.tol) if s.downhole else None
    return s, p, (e, d)


# --- the job in the process ----------------------------------------------------------------------------------


def test_stages_form_and_record(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, a, _ = proj
    req = body(set_id, axes={"ang1": 30, "ang2": -10, "ang3": 0}, nlag=4)
    seen: list[str | None] = []
    out = run(project, req, seen=seen)
    assert stages_of(seen) == STAGES and out["fresh"] is False
    assert set(out["stages"]) == {"read", "trajectories", "pairs", "downhole", "write", "total"}
    s, p, (e, d) = reference(project, req)
    key = out["key"]
    assert len(key) == 64 and set(key) <= set("0123456789abcdef")
    with dialect.read_tx(project.engine) as conn:
        inputs = vj.inputs_of(conn, s.data)
    assert inputs == p.inputs and key == vj.key_of(s, inputs)  # the key is that of the points' own inputs
    assert out["directions"] == ms._plain(e.to_json(30, axis=True)) and out["downhole"] == ms._plain(d.to_json(30))
    assert [x["axis"] for x in out["directions"]] == [1, 2, 3]
    assert (out["directions"][0]["az"], out["directions"][0]["plunge"]) == (30.0, 10.0)
    assert out["params"] == s.body and out["params"]["lag"] == 10.0 and out["params"]["dh_lag"] == 2.0
    assert out["counts"] == p.counts and out["sigma2"] == p.sigma2 and out["sampled"] is None
    assert out["set_id"] == set_id and out["table"] == str(a) and out["unit"] == "г/т" and out["transform"] == "none"
    assert out["origin"] == p.origin.tolist() and out["warnings"] == []
    n = out["directions"][0]["n"]
    assert all((g is None) == (k == 0) for g, k in zip(out["directions"][0]["gamma"], n, strict=True))  # null, not 0
    assert cached(project.folder) == [f"{key}.json", f"{key}.sums.parquet"]
    record = ms.read_result(project.folder, key)
    form = {k: v for k, v in out.items() if k not in ("fresh", "stages")}
    assert record["result"] == form and record["inputs"] == inputs
    assert record["source"] == {k: v for k, v in s.data.canonical().items() if k != "transform"}
    assert record["calc"] == {"params": s.body, "sampled": None}
    import pyarrow.parquet as pq

    sums = pq.read_table(vj.sums_path(project.folder, key)).to_pydict()
    assert len(sums["n"]) == 3 * 4 + 15 and set(sums["part"]) == {"axis", "downhole"}
    assert sums["n"][:4] == n and sums["s_d2"][0] == float(e.sums.sums[0, 0, 1])


def test_directions_nscore_and_its_table(proj) -> None:  # type: ignore[no-untyped-def]
    import pyarrow.parquet as pq

    project, set_id, _, _ = proj
    req = body(set_id, transform="nscore", directions=[{"az": 0, "plunge": 0}, {"az": 90, "az_tol": 45}],
               downhole=False)  # fmt: skip
    seen: list[str | None] = []
    out = run(project, req, seen=seen)
    assert stages_of(seen) == ["Чтение таблицы", "Траектории", "Нормальные оценки", "Пары: направления", "Запись"]
    assert out["downhole"] is None and out["sigma2"] == 1.0 and [x["axis"] for x in out["directions"]] == [None] * 2
    assert out["directions"][1]["az_tol"] == 45.0 and out["directions"][0]["az_tol"] == 22.5
    key = out["key"]
    assert cached(project.folder) == [f"{key}.json", f"{key}.nscore.parquet", f"{key}.sums.parquet"]
    table = pq.read_table(ms.nscore_path(project.folder, key)).to_pydict()
    _, p, _ = reference(project, req)
    assert table["value"] == p.z.tolist() and table["score"] == p.values.tolist()
    assert math.isnan(table["weight"][2]) and table["weight"][3] == 0.5  # no weight: in the pairs, not in the table


def test_repeat_by_the_key(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, a, _ = proj
    first = run(project, body(set_id))
    seen: list[str | None] = []
    again = run(project, body(set_id), seen=seen)
    assert again["fresh"] is True and again["key"] == first["key"]
    assert stages_of(seen) == ["Чтение таблицы", "Траектории"]  # the points for the lag; no pairs, no writing
    assert {k: v for k, v in again.items() if k not in ("fresh", "stages")} == \
        {k: v for k, v in first.items() if k not in ("fresh", "stages")}  # fmt: skip
    seen.clear()
    given = run(project, body(set_id, lag="10", dh_lag=2, lag_tol="5,0"), seen=seen)  # = the defaults
    assert given["fresh"] is True and given["key"] == first["key"] and stages_of(seen) == []  # nothing read
    other = run(project, body(set_id, seed=7))
    assert other["fresh"] is False and other["key"] != first["key"]
    with dialect.write_tx(project.engine) as conn:  # an edit of the table: its revision → new inputs, a new key
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == a).values(rev=dh_tables.c.rev + 1))
    edited = run(project, body(set_id))
    assert edited["fresh"] is False and edited["key"] != first["key"]
    ms.result_path(project.folder, edited["key"]).unlink()
    assert run(project, body(set_id))["fresh"] is False  # the cache cleared: computed again


def test_cancel_leaves_nothing(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, _, _ = proj
    seen: list[str | None] = []
    run(project, body(set_id, transform="nscore"), seen=seen)
    for f in ms.cache_dir(project.folder).iterdir():
        f.unlink()
    for at in range(1, len(seen) + 1):
        with pytest.raises(JobCancelled):
            run(project, body(set_id, transform="nscore"), cancel_at=at)
        assert cached(project.folder) == [], at


def test_warm_runs_the_kernels() -> None:
    assert set(vj.warm()) == {"pairs", "downhole"}  # the step `geostat` of `ops/warm.py`


def test_refusals_of_the_points(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, _, _ = proj
    with pytest.raises(ApiError) as e:
        run(project, body(set_id, domain={"field": "ROCK", "values": ["нет такой"]}))
    assert e.value.code == "no_values" and e.value.extra["counts"]["outside_domain"] == 8
    with pytest.raises(ApiError) as e:
        run(project, body(set_id, axes={"ang1": 0}, directions=[{"az": 0}]))
    assert (e.value.code, e.value.extra["param"]) == ("bad_param", "directions")
    with pytest.raises(ApiError) as e:
        run(project, body(set_id, lag=10, lag_tol=11))
    assert (e.value.code, e.value.extra["param"]) == ("bad_param", "lag_tol")
    assert cached(project.folder) == []


# --- the preview ---------------------------------------------------------------------------------------------


def big_project(tmp_path: Path, side: int = 10, per_hole: int = 250, seed: int = 3) -> tuple[Any, int]:
    """side² vertical holes 20 m apart, `per_hole` samples of 2 m with a smooth trend and a noise."""
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
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
    au = np.exp(np.sin(depth / 37.0) + 0.5 * rng.standard_normal(depth.size))
    dh.write_table(engine, set_id, dh.TableData(name="Пробы", kind="assay", holes=holes, from_m=depth.tolist(),
                                                to_m=(depth + 2.0).tolist(),
                                                columns=[dh.ColumnSpec("AU", "number", "г/т")],
                                                values=[au.tolist()]), job_id=None, tick=noop)  # fmt: skip
    return SimpleNamespace(engine=engine, uid=f"test:{tmp_path}", folder=tmp_path), set_id


def test_preview_samples_20000_by_the_seed_and_writes_nothing(tmp_path: Path) -> None:
    import time

    project, set_id = big_project(tmp_path, side=10, per_hole=250)  # 25 000 points
    try:
        req = body(set_id, axes={"ang1": 0, "ang2": 0, "ang3": 0}, nlag=10)
        vj.preview(project, req)  # the kernels and the snapshot of the table ready (ADR «Трактовка целей»)
        t0 = time.perf_counter()
        got = vj.preview(project, req)
        took = time.perf_counter() - t0
        assert got["key"] is None and got["sampled"] == {"n": spec.PREVIEW_MAX, "of": 25_000, "seed": spec.SEED}
        assert cached(project.folder) == []
        with dialect.read_tx(project.engine) as conn:
            raw = spec.parse_experimental(conn, req)
        p = points.load(project, raw.data)
        s = raw.resolved(spec.defaults_for(p.median_length))
        q, sampled = points.sample(p, spec.PREVIEW_MAX, spec.SEED)
        e = experimental.calculate(q, vj.directions_of(s), s.lags.lag, s.lags.nlag, s.lags.tol)
        d = downhole.calculate(q, s.dh.lag, s.dh.nlag, s.dh.tol)
        assert got["directions"] == ms._plain(e.to_json(30)) and got["downhole"] == ms._plain(d.to_json(30))
        assert got["counts"]["used"] == 25_000 and got["sigma2"] == p.sigma2  # σ² of all the points
        assert sum(got["downhole"]["n"]) < sum(downhole.calculate(p, 2.0, 15).n.tolist())  # the same sample
        other = vj.preview(project, {**req, "seed": 99})
        assert other["sampled"]["seed"] == 99 and other["directions"] != got["directions"]
        assert took < 5.0, took  # the target ≤ 1 s is the bench's (16.28); here a guard against a full run
    finally:
        project.engine.dispose()


def test_preview_of_few_points_is_the_job_without_a_key(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, _, _ = proj
    got = vj.preview(project, body(set_id))
    out = run(project, body(set_id))
    assert got["key"] is None and got["sampled"] is None
    assert got == {**{k: v for k, v in out.items() if k not in ("fresh", "stages")}, "key": None}


# --- the routes ------------------------------------------------------------------------------------------------


@pytest.fixture()
def vr(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A core with an open project and the set of `test_vario_store`; the job registered here and in the worker
    until the line of the orchestrator (`jobs/registry.py`)."""
    import importlib

    from bf_core.jobs.registry import HANDLER_MODULES

    if MODULE not in HANDLER_MODULES:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", MODULE)
    importlib.import_module(MODULE)
    assert pclient.post(API + "/projects", json={"name": "Вариография"}).status_code == 200
    project = projects_of(pclient.core).require_current()
    pclient.set_id, pclient.a, pclient.b = fill(project.engine)
    pclient.project = project
    return pclient


def test_job_in_a_worker_repeat_preview_and_defaults(vr) -> None:  # type: ignore[no-untyped-def]
    c = vr
    req = {"set_id": c.set_id, "table": str(c.a), "field": "AU", "axes": {"ang1": 30, "ang2": -10}, "nlag": 4}
    r = c.post(GS + "/experimental", json=req)
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=240)
    assert job["status"] == "done", job
    res = job["result"]
    assert res["fresh"] is False and len(res["directions"]) == 3 and res["downhole"]["lag"] == 2.0
    assert ms.result_path(c.project.folder, res["key"]).is_file()
    r = c.post(GS + "/experimental", json=req)
    again = wait_job(c, r.json()["source"], r.json()["id"], timeout=120)
    assert again["status"] == "done" and again["result"]["fresh"] is True and again["result"]["key"] == res["key"]
    pv = c.post(GS + "/preview", json=req)
    assert pv.status_code == 200, pv.text
    pv = pv.json()
    assert pv["key"] is None and pv["directions"] == res["directions"] and pv["downhole"] == res["downhole"]
    d = c.get(GS + "/defaults", params={"set_id": c.set_id, "table": "Пробы", "field": "AU"})
    assert d.status_code == 200, d.text
    d = d.json()
    assert d["defaults"]["lag"] == 10.0 and d["weights"] == "DECL_W" and d["counts"]["used"] == 8
    d = c.get(GS + "/defaults", params={"set_id": c.set_id, "table": "Пробы", "field": "AU", "weights": "",
                                        "domain_table": "Литология", "domain_field": "ZONE",
                                        "domain_values": "окисл;перв"})  # fmt: skip
    assert d.status_code == 200 and d.json()["weights"] is None and d.json()["counts"]["used"] == 7, d.text


def test_route_refusals(vr) -> None:  # type: ignore[no-untyped-def]
    c = vr
    base = {"set_id": c.set_id, "table": "Пробы", "field": "AU"}
    r = c.post(GS + "/experimental", json={**base, "set_id": 999})
    assert r.status_code == 404 and r.json()["code"] == "set_not_found"
    r = c.post(GS + "/experimental", json={**base, "axes": {"ang1": 0}, "directions": [{"az": 0}]})
    assert r.status_code == 422 and r.json()["param"] == "directions" and r.json()["hint"]
    r = c.post(GS + "/preview", json={**base, "nlag": 0})
    assert r.status_code == 422 and r.json()["param"] == "nlag"
    r = c.post(GS + "/preview", json={**base, "domain": {"field": "ROCK", "values": ["нет такой"]}})
    assert r.status_code == 422 and r.json()["code"] == "no_values" and r.json()["counts"]["used"] == 0
    r = c.post(GS + "/preview", json=[1])
    assert r.status_code == 422 and r.json()["code"] == "bad_request"
    r = c.post(GS + "/experimental", json={**base, "domain": {"field": "ROCK", "values": ["нет такой"]}})
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=120)
    assert job["status"] == "failed" and job["error"]["code"] == "no_values", job  # known only from the points
    r = c.get(GS + "/defaults", params={"set_id": c.set_id, "table": "Нет", "field": "AU"})
    assert r.status_code == 404 and r.json()["code"] == "table_not_found"


# --- threads and databases -----------------------------------------------------------------------------------------


def _digest(folder: Path, key: str) -> str:
    h = hashlib.sha256()
    for name in (f"{key}.json", f"{key}.sums.parquet"):
        h.update(ms.cache_dir(folder).joinpath(name).read_bytes())
    return h.hexdigest()


@pytest.mark.windows
def test_one_hash_at_1_and_4_threads(tmp_path: Path) -> None:
    """The record of the job bit for bit at 1 and 4 threads, with and without the sample (ADR §4, решение 25)."""
    (tmp_path / "data").mkdir()
    project, set_id = big_project(tmp_path / "data", side=8, per_hole=120)  # 7 680 points
    try:
        req = body(set_id, axes={"ang1": 45, "ang2": -20, "ang3": 10}, nlag=12, band_h="inf")
        for limit in (spec.PAIRS_MAX, 2e6):  # 2·10⁶ checks: the sample
            digests, forms = [], []
            for threads in (1, 4):
                folder = tmp_path / f"t{threads}-{int(limit)}"
                out = vj.run_experimental(project.engine, folder, {"body": req}, threads=threads, limit=limit)
                digests.append(_digest(folder, out["key"]))
                forms.append(json.dumps({k: v for k, v in out.items() if k != "stages"}, sort_keys=True))
            assert digests[0] == digests[1] and forms[0] == forms[1], limit
            sampled = json.loads(forms[0])["sampled"]
            assert (sampled is None) == (limit == spec.PAIRS_MAX), sampled
    finally:
        project.engine.dispose()


@pytest.mark.db
def test_job_on_the_database(db_url: str, tmp_path: Path) -> None:
    """SQLite and PostgreSQL: the counts of the set, the form, the repeat by the key."""
    engine = make_engine(db_url, tmp_path)
    try:
        migrate.upgrade(engine, "project")
        set_id, a, _b = fill(engine)
        req = {"set_id": set_id, "table": str(a), "field": "AU", "domain": {"table": "Литология", "field": "ZONE",
                                                                            "values": ["окисл"]}}  # fmt: skip
        out = vj.run_experimental(engine, tmp_path, {"body": req})
        assert out["counts"] == {"rows": 10, "missing": 1, "no_position": 1, "outside_domain": 3, "used": 5,
                                 "no_weight": 0}  # fmt: skip
        assert out["fresh"] is False and len(out["directions"]) == 3 and out["downhole"]["n"][0] > 0
        again = vj.run_experimental(engine, tmp_path, {"body": req})
        assert again["fresh"] is True and again["key"] == out["key"]
    finally:
        engine.dispose()
