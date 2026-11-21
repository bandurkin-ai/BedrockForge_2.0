"""The job «Истинная мощность» `intercept.tt` and its preview (ADR 0021 §8, §10, решения 13, 15–18, 20, 21; подшаг
15.11): the samples of Т8.2-12 in three holes of the demo set → TT, TT_FLAG, TT_SRC, ALPHA, DIPDIR, HOLE_THETA,
HOLE_AZ in place and the record `tt` with the summary (a job in a `spawn` worker); the fields are those of the
kernel on the same reading; a repeat overwrites its fields and its record; «Перестроить» with the same hash does
not compute, an edited source makes the record «устарел» and the rebuild computes again; a cancel at any tick
leaves nothing; the preview of a hole is the job's rows (the form of e15-api.md) and writes nothing; Д-26 takes TT
as the thickness field (M = Σ TT); on SQLite and PostgreSQL (`-m db`) a vertical hole has TT = l·cos α."""

from __future__ import annotations

import itertools
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_results, dh_tables
from bf_core.jobs.context import JobCancelled
from bf_core.projects.service import projects_of
from tests.test_composites_jobs import rows_of
from tests.test_import_job import API, wait_job
from tests.test_ore_job import N, add_samples

IC = f"{API}/intercepts"
C = f"{API}/composites"
MODULE = "bf_core.intercepts.jobs"
GLOBAL = [{"source": "global", "alpha": 30, "dipdir": 120}]
FIELDS = ["TT", "TT_FLAG", "TT_SRC", "ALPHA", "DIPDIR", "HOLE_THETA", "HOLE_AZ"]
_ids = itertools.count(151_001)


@pytest.fixture()
def tj(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """The demo set with «Пробы ГКЗ»; `intercept.tt` registered here and in the worker until the line of the
    orchestrator (`jobs/registry.py`)."""
    import importlib

    from bf_core.jobs.registry import HANDLER_MODULES

    if MODULE not in HANDLER_MODULES:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", MODULE)
    importlib.import_module(MODULE)  # in this process too: the route checks the kind is registered
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    table, holes = add_samples(project, set_id)
    return pclient, project, set_id, table, holes


def body(set_id: int, table: str, **kw: Any) -> dict[str, Any]:
    return {"set_id": set_id, "table": table, "orientation": GLOBAL, **kw}


def tt_here(project: Any, params: dict[str, Any], cancel_at: int | None = None,
            seen: list[int] | None = None) -> dict[str, Any]:  # fmt: skip
    from bf_core.intercepts import jobs

    seen = [0] if seen is None else seen

    def tick(_p: float | None = None, _m: str | None = None) -> None:
        seen[0] += 1
        if cancel_at is not None and seen[0] >= cancel_at:
            raise JobCancelled()

    return jobs.run_tt(project.engine, project.folder, params, job_id=next(_ids), tick=tick)


def col(got: dict[str, Any], name: str) -> list[Any]:
    key = next(c["key"] for c in got["columns"] if c["name"] == name)
    return [r[key] for r in got["rows"]]


def records(c: Any, set_id: int, op: str = "tt") -> list[dict[str, Any]]:
    return [x for x in c.get(f"{C}/results?set_id={set_id}").json()["results"] if x["op"] == op]


def kernel(project: Any, set_id: int, req: dict[str, Any]) -> Any:
    """The kernel on the same reading as the job (`jobs.read`, `jobs.compute`): the reference of the fields."""
    from bf_core.composites import source
    from bf_core.intercepts import jobs

    with dialect.read_tx(project.engine) as conn:
        spec = jobs.parse(conn, req)
    lines, tables = jobs.read(project.engine, spec)
    return lines, jobs.compute(spec, lines, tables, source.trajectories(project, set_id), project)


def test_tt_in_a_worker_fields_summary_and_record(tj) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, holes = tj
    req = body(set_id, table)
    lines, ref = kernel(project, set_id, req)
    r = c.post(f"{IC}/tt", json=req)
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=240)
    assert job["status"] == "done", job
    res = job["result"]
    assert res["columns"] == FIELDS and res["rows"] == 3 * N and res["fresh"] is False
    assert res["name"] == "Истинная мощность · Пробы ГКЗ" and res["table_id"] == int(table)
    assert ["composites.changed", {"set_id": set_id}] in res["events"]
    assert any(e[0] == "drillholes.changed" and e[1]["table"] == table for e in res["events"])
    got = rows_of(c, set_id, table)
    unit = {x["name"]: x["unit"] for x in got["columns"]}
    assert [n for n in unit if n in FIELDS] == FIELDS and unit["TT"] == "м" and unit["ALPHA"] == "°"
    by_id = {row["id"]: i for i, row in enumerate(got["rows"])}
    at = [by_id[int(i)] for i in lines.row_ids.tolist()]  # the rows in the line order of the kernel
    for name, _kind, want in ref.columns(True):
        have = [col(got, name)[i] for i in at]
        if name == "TT_SRC":
            assert have == list(want) and set(have) == {"глобальная"}
        else:
            np.testing.assert_array_equal(np.array([np.nan if v is None else v for v in have], dtype=float),
                                          np.asarray(want, dtype=float), err_msg=name)  # fmt: skip
    tt = np.array(col(got, "TT"), dtype=float)
    length = np.array([row["to"] - row["from"] for row in got["rows"]])
    assert np.isfinite(tt).all() and (tt <= length + 1e-12).all() and (tt > 0).all()  # the demo holes have paths
    assert set(col(got, "TT_FLAG")) <= {0.0, 1.0}

    rec = records(c, set_id)
    assert len(rec) == 1 and rec[0]["stale"] is False and rec[0]["algo"] == "tt-1", rec
    rec = rec[0]
    assert rec["table_id"] == int(table) and rec["name"] == res["name"] and rec["job_id"] == r.json()["id"]
    assert rec["params"] == {"set_id": set_id, "table": table, "orientation": [{"source": "global", "alpha": 30.0,
                             "dipdir": 120.0}], "eps": 0.1, "step": 1.0, "angles": True}  # fmt: skip
    assert rec["written"] == [{"table": rec["inputs"]["tables"][0]["key"], "columns": FIELDS}]
    rep = rec["report"]
    along = int(sum(1 for v in col(got, "TT_FLAG") if v == 1.0))
    assert rep["counts"] == {"src": {"глобальная": 3 * N}, "flags": {"1": along, "2": 0, "4": 0, "8": 0}}
    assert rep["rows"] == 3 * N and rep["with_tt"] == 3 * N and rep["excluded"] == 0 and rep["skipped"] == 0
    assert math.isclose(rep["length"], 3.0 * N, rel_tol=1e-12)
    assert math.isclose(rep["tt"], math.fsum(tt.tolist()), rel_tol=1e-12)
    assert rep["absent"] == [] and rep["seconds"] > 0 and set(rep["versions"]) == {"numpy", "numba"}
    assert {"src", "flags"}.isdisjoint(rep) and res["summary"]["counts"] == rep["counts"]


def test_repeat_rebuild_and_stale(tj) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, _holes = tj
    first = tt_here(project, {"body": body(set_id, table)})
    tt1 = col(rows_of(c, set_id, table), "TT")
    again = tt_here(project, {"body": body(set_id, table, orientation=[{"source": "global", "alpha": 70,
                                                                        "dipdir": 10}])})  # fmt: skip
    got = rows_of(c, set_id, table)
    assert col(got, "TT") != tt1 and [x["name"] for x in got["columns"]].count("TT") == 1
    rec = records(c, set_id)
    assert [x["id"] for x in rec] == [again["result_id"]] == [first["result_id"]], rec  # its own record, rewritten
    assert rec[0]["params"]["orientation"][0]["alpha"] == 70.0 and rec[0]["stale"] is False

    rid = again["result_id"]
    fresh = tt_here(project, {"body": rec[0]["params"], "rebuild": rid})  # the same hash: nothing computed
    assert fresh["fresh"] is True and fresh["result_id"] == rid and "stages" in fresh
    assert col(rows_of(c, set_id, table), "TT") == col(got, "TT")

    first_row = got["rows"][0]
    r = c.patch(f"{API}/drillholes/sets/{set_id}/tables/{table}/rows/{first_row['id']}",
                json={"column": "to", "value": first_row["to"] + 0.5})  # fmt: skip
    assert r.status_code == 200, r.text
    rec = records(c, set_id)[0]
    assert rec["stale"] is True and rec["reason"], rec
    r = c.post(f"{IC}/results/{rid}/rebuild")
    assert r.status_code == 200, r.text  # the route starts the job
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=240)
    assert job["status"] == "done", job
    built = job["result"]
    assert built["fresh"] is False
    rec = records(c, set_id)
    assert len(rec) == 1 and rec[0]["stale"] is False and rec[0]["id"] == built["result_id"]
    now = rows_of(c, set_id, table)
    k = next(i for i, row in enumerate(now["rows"]) if row["id"] == first_row["id"])
    assert col(now, "TT")[k] > col(got, "TT")[k]  # computed again on the longer interval
    assert math.isclose(rec[0]["report"]["length"], 3.0 * N + 0.5, rel_tol=1e-12)

    short = tt_here(project, {"body": {**rec[0]["params"], "angles": False}})  # its own fields: no 409
    assert short["columns"] == FIELDS[:3]


def test_cancel_leaves_nothing(tj) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, _holes = tj
    req = {"body": body(set_id, table)}
    seen = [0]
    done = tt_here(project, req, seen=seen)
    assert c.delete(f"{IC}/results/{done['result_id']}").status_code == 200
    assert seen[0] >= 4
    for at in range(1, seen[0] + 1):
        with pytest.raises(JobCancelled):
            tt_here(project, req, cancel_at=at)
        with dialect.read_tx(project.engine) as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(dh_results)).scalar() == 0
            live = set(conn.execute(sa.select(dh_columns.c.name).where(
                dh_columns.c.table_id == int(table), dh_columns.c.deleted.is_(False))).scalars())  # fmt: skip
            assert live.isdisjoint(FIELDS), live
            assert conn.execute(sa.select(sa.func.count()).select_from(dh_tables)
                                .where(dh_tables.c.state != "ready")).scalar() == 0  # fmt: skip


def hole_id_of(c: Any, set_id: int, hole: str) -> int:
    rows = c.get(f"{API}/drillholes/sets/{set_id}/tables/collar?limit=100").json()["rows"]
    return int(next(h["hole_id"] for h in rows if h["hole"] == hole))


def test_preview_is_the_job_and_writes_nothing(tj) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, holes = tj
    wf = {w["name"]: w["id"] for w in c.get(f"{API}/wireframes").json()["wireframes"]}
    mesh = {"source": "mesh", "wireframe_ids": [wf["Рудное тело 1"]]}
    req = body(set_id, table, orientation=[mesh, *GLOBAL])
    hid = hole_id_of(c, set_id, holes[1])
    r = c.post(f"{IC}/tt/preview", json={"params": req, "hole_id": hid})
    assert r.status_code == 200, r.text
    p = r.json()
    assert set(p) == {"hole", "rows", "counts", "mesh_skipped", "set_summary", "notes"} and p["hole"] == holes[1]
    rows = p["rows"]
    assert list(rows) == ["from", "to", "length", *FIELDS[:1], "TT_REL", *FIELDS[1:]]
    assert all(len(v) == N for v in rows.values()) and rows["from"] == [float(i) for i in range(N)]
    mesh_src = {"каркас: пересечение", "каркас: ближайшая"}  # the body of the demo (< 2 million triangles)
    assert p["mesh_skipped"] is False and set(rows["TT_SRC"]) <= {*mesh_src, "глобальная"}
    assert set(rows["TT_SRC"]) & mesh_src, rows["TT_SRC"]
    assert sum(p["counts"]["src"].values()) == N and p["counts"]["flags"]["2"] == 0
    assert sum(p["set_summary"]["src"].values()) == 3 * N
    for tt, rel, length in zip(rows["TT"], rows["TT_REL"], rows["length"], strict=True):
        assert length == 1.0 and rel == tt / length
    assert records(c, set_id) == [] and "TT" not in [x["name"] for x in rows_of(c, set_id, table)["columns"]]

    tt_here(project, {"body": req})
    got = rows_of(c, set_id, table)
    mine = [i for i, row in enumerate(got["rows"]) if row["hole"] == holes[1]]
    for name in FIELDS:
        assert rows[name] == [col(got, name)[i] for i in mine], name
    rec = records(c, set_id)[0]
    assert rec["report"]["absent"] == [] and [m["name"] for m in rec["inputs"]["meshes"]] == ["Рудное тело 1"]

    for hole_id, code in ((10**9, 404), (None, 422)):
        r = c.post(f"{IC}/tt/preview", json={"params": req, "hole_id": hole_id})
        assert r.status_code == code and r.json()["code"] == "hole_not_found" and r.json()["hint"], r.text
    r = c.post(f"{IC}/tt/preview", json={"params": {**req, "eps": 0.9}, "hole_id": hid})
    assert (r.status_code, r.json()["code"], r.json()["param"]) == (422, "bad_param", "eps")
    assert c.post(f"{IC}/tt/preview", json={"params": [], "hole_id": hid}).json()["code"] == "bad_request"


def test_ore_intervals_take_tt_as_the_thickness(tj) -> None:  # type: ignore[no-untyped-def]
    """Д-26 (решение 16): TT is a numeric field of the samples — M of an ore interval is Σ TT of its samples;
    a new TT makes the result of Д-26 «устарел»."""
    from tests.test_ore_job import body as ore_body
    from tests.test_ore_job import cols, ore_here

    c, project, set_id, table, holes = tj
    tt_here(project, {"body": body(set_id, table)})
    samples = rows_of(c, set_id, table)
    made = ore_here(project, {"body": ore_body(set_id, table, thickness={"source": "field", "field": "TT"})})
    got = rows_of(c, set_id, str(made["table_id"]))
    k, ks = cols(got), cols(samples)
    ores = [row for row in got["rows"] if row[k["CLASS"]] == "ore"]
    assert ores
    for row in ores:
        inside = [s[ks["TT"]] for s in samples["rows"]
                  if s["hole"] == row["hole"] and row["from"] <= s["from"] and s["to"] <= row["to"]]  # fmt: skip
        assert inside and math.isclose(row[k["M"]], math.fsum(inside), rel_tol=1e-12, abs_tol=1e-12), row
        assert row[k["M"]] < row[k["LENGTH"]]  # TT < l: the holes are not normal to the plane
    assert records(c, set_id, "ore")[0]["stale"] is False
    tt_here(project, {"body": body(set_id, table, orientation=[{"source": "global", "alpha": 60, "dipdir": 0}])})
    assert records(c, set_id, "ore")[0]["stale"] is True


# --- SQLite and PostgreSQL --------------------------------------------------------------------------------------


@pytest.mark.db
def test_vertical_hole_on_both_databases(db_url: str, tmp_path: Path) -> None:
    """A vertical hole: TT = l·cos α; a hole without a collar — TT empty, bit 8; TO ≤ FROM — out (bit 2)."""
    from bf_core.db import migrate
    from bf_core.drillholes import store as dh
    from bf_core.intercepts import jobs
    from tests.dbutil import make_engine

    engine = make_engine(db_url, tmp_path)
    try:
        migrate.upgrade(engine, "project")
        with dialect.write_tx(engine) as conn:
            set_id = dh.create_set(conn, "Скважины")
            dh.replace_collars(conn, set_id, [dh.CollarRow("В-1", 100.0, 200.0, 50.0, 40.0, 0.0, -90.0)])
        holes = ["В-1", "В-1", "В-1", "Б-2"]
        data = dh.TableData("Пробы", "assay", holes, [0.0, 2.0, 5.0, 0.0], [2.0, 5.0, 5.0, 1.0],
                            [dh.ColumnSpec("AU", "number", "г/т")], [[1.0, 2.0, 3.0, 4.0]])  # fmt: skip
        table = dh.write_table(engine, set_id, data, job_id=None, tick=lambda _p, _m=None: None)["table_id"]
        params = {"body": {"set_id": set_id, "table": str(table), "orientation": GLOBAL}, "rows": 4}
        out = jobs.run_tt(engine, tmp_path, params, job_id=1, tick=lambda _p=None, _m=None: None)
        assert out["rows"] == 4 and set(out["stages"]) == {"read", "trajectories", "hash", "kernel", "write"}
        with dialect.read_tx(engine) as conn:
            rec = conn.execute(sa.select(dh_results)).mappings().one()
            live = sa.select(dh_columns).where(dh_columns.c.table_id == table, dh_columns.c.deleted.is_(False))
            names = {r.name: (r.kind, r.unit) for r in conn.execute(live)}
        assert names["TT"] == ("number", "м") and names["TT_SRC"] == ("text", None)
        assert rec["op"] == "tt" and rec["algo"] == "tt-1"
        rep = rec["report"]
        assert rep["counts"] == {"src": {"глобальная": 3, "нет": 1}, "flags": {"1": 0, "2": 1, "4": 0, "8": 1}}
        assert rep["excluded"] == 1 and rep["rows"] == 4 and math.isclose(rep["length"], 6.0, rel_tol=1e-15)
        from bf_core.composites import source

        got = source.read(engine, set_id, int(table), ["TT", "TT_FLAG", "TT_SRC", "HOLE_THETA"])
        src = [None if k < 0 else got.labels["TT_SRC"][k] for k in got.codes["TT_SRC"].tolist()]
        at = {(got.holes[int(h)], float(f)): i for i, (h, f) in enumerate(zip(got.hole_of(), got.from_m,
                                                                               strict=True))}  # fmt: skip
        cos30 = math.cos(math.radians(30.0))
        for depth, length in ((0.0, 2.0), (2.0, 3.0)):
            i = at[("В-1", depth)]
            assert math.isclose(got.numbers["TT"][i], length * cos30, rel_tol=1e-12), (depth, got.numbers["TT"][i])
            assert got.numbers["TT_FLAG"][i] == 0 and src[i] == "глобальная"
        i = at[("В-1", 5.0)]
        assert math.isnan(got.numbers["TT"][i]) and got.numbers["TT_FLAG"][i] == 2
        i = at[("Б-2", 0.0)]
        assert math.isnan(got.numbers["TT"][i]) and got.numbers["TT_FLAG"][i] == 8
        assert math.isnan(got.numbers["HOLE_THETA"][i])
        again = jobs.run_tt(engine, tmp_path, {**params, "rebuild": rec["id"]}, job_id=2,
                            tick=lambda _p=None, _m=None: None)  # fmt: skip
        assert again["fresh"] is True
    finally:
        engine.dispose()
