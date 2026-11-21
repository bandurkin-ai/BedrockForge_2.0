"""The job «Рудные пересечения» `intercept.run` and its preview (ТЗ 8.5.3, Т8.5-11, Т8.5-12; ADR 0021 §7–8, §10,
решения 10–19, 21; подшаг 15.12): the samples of Т8.5-11 in three holes of the demo set → the table «Рудные
пересечения» (the columns of the contract, the kernel on the same reading), its display and the record
`intercepts` (a job in a `spawn` worker); 409 `table_exists` {name, suggested, lost} and «Заменить»; «Перестроить»
with the same hash does not compute, an edited sample makes the record «устарел»; a cancel at any tick leaves
nothing; the preview of a hole is the job's rows with every candidate and the reason it was dropped; on SQLite and
PostgreSQL (`-m db`) TT of a vertical hole is l·cos α."""

from __future__ import annotations

import itertools
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_results, dh_tables
from bf_core.jobs.context import JobCancelled
from bf_core.projects.service import projects_of
from tests.test_composites_jobs import rows_of, tables
from tests.test_import_job import API, wait_job
from tests.test_intercepts_tt_job import hole_id_of
from tests.test_ore_job import charts

IC = f"{API}/intercepts"
C = f"{API}/composites"
MODULES = ("bf_core.intercepts.jobs", "bf_core.intercepts.jobs_run")
GLOBAL = [{"source": "global", "alpha": 30, "dipdir": 120}]
T11 = [0.1, 1.5, 2.0, 0.2, 0.3, 1.8, 0.1, 0.1, 0.1, 3.0]  # Т8.5-11: samples of 1 m from the collar
N = len(T11)
NAME = "Рудные пересечения"
HEAD = ["HOLE", "FROM", "TO", "LENGTH", "TT", "TT_FLAG", "TT_SRC", "ALPHA", "DIPDIR", "HOLE_THETA", "HOLE_AZ"]
TAIL = ["LEN_G", "TT_G", "WASTE_LEN", "N_INT", "X", "Y", "Z", "INCL", "LABEL", "ID", "KIND", "MISS_LEN"]
_ids = itertools.count(152_001)


def add_t11(project: Any, set_id: int, name: str = "Пробы Т11") -> tuple[str, list[str]]:
    """«Пробы Т11»: AU г/т of Т8.5-11 and CU % (= FROM / 10), 1 m each from 0 m, in three holes of the set."""
    from bf_core.db.schema import dh_holes
    from bf_core.drillholes import store

    with dialect.read_tx(project.engine) as conn:
        holes = [r.name for r in conn.execute(sa.select(dh_holes.c.name).where(dh_holes.c.set_id == set_id))][:3]
    names = [h for h in holes for _ in range(N)]
    frm = [float(i) for _ in holes for i in range(N)]
    cols = [store.ColumnSpec("AU", "number", "г/т", "component"), store.ColumnSpec("CU", "number", "%")]
    data = store.TableData(name, "assay", names, frm, [f + 1.0 for f in frm], cols, [T11 * len(holes),
                           [f / 10.0 for f in frm]])  # fmt: skip
    out = store.write_table(project.engine, set_id, data, job_id=None, tick=lambda _p, _m=None: None)
    return str(out["table_id"]), holes


@pytest.fixture()
def rj(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """The demo set with «Пробы Т11»; `intercept.run` registered here and in the worker until the line of the
    orchestrator (`jobs/registry.py`)."""
    import importlib

    from bf_core.jobs.registry import HANDLER_MODULES

    extra = [m for m in MODULES if m not in HANDLER_MODULES]
    if extra:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", ",".join(extra))
    for m in MODULES:
        importlib.import_module(m)  # in this process too: the route checks the kind is registered
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    table, holes = add_t11(project, set_id)
    return pclient, project, set_id, table, holes


def body(set_id: int, table: str, **kw: Any) -> dict[str, Any]:
    return {"set_id": set_id, "table": table, "field": "AU", "cutoff": 1, "l_min": 2, "waste_max": 2,
            "orientation": GLOBAL, **kw}  # fmt: skip


def run_here(project: Any, params: dict[str, Any], cancel_at: int | None = None,
             seen: list[int] | None = None) -> dict[str, Any]:  # fmt: skip
    from bf_core.intercepts import jobs_run

    seen = [0] if seen is None else seen

    def tick(_p: float | None = None, _m: str | None = None) -> None:
        seen[0] += 1
        if cancel_at is not None and seen[0] >= cancel_at:
            raise JobCancelled()

    return jobs_run.run_intercepts(project.engine, project.folder, params, job_id=next(_ids), tick=tick)


def col(got: dict[str, Any], name: str) -> list[Any]:
    key = next(c["key"] for c in got["columns"] if c["name"] == name)
    return [r[key] for r in got["rows"]]


def records(c: Any, set_id: int) -> list[dict[str, Any]]:
    return [x for x in c.get(f"{IC}/results?set_id={set_id}").json()["results"] if x["op"] == "intercepts"]


def kernel(project: Any, req: dict[str, Any]) -> Any:
    """The kernel on the same reading as the job (`jobs.read`, `jobs_run.compute`): the reference of the table."""
    from bf_core.composites import source
    from bf_core.intercepts import jobs, jobs_run

    with dialect.read_tx(project.engine) as conn:
        spec = jobs_run.parse(conn, req)
    src, tbl = jobs.read(project.engine, spec)
    return jobs_run.compute(spec, src, tbl, source.trajectories(project, spec.set_id), project)[1]


def numbers(values: list[Any]) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=float)


def test_t8_5_11_12_in_a_worker_table_display_and_record(rj) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, holes = rj
    req = body(set_id, table, include_cutoff=1.5)
    ref = kernel(project, req)
    r = c.post(f"{IC}/run", json=req)
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=240)
    assert job["status"] == "done", job
    res = job["result"]
    names = [*HEAD, "AU", "CU", *TAIL]
    assert res["name"] == NAME and res["columns"] == names and res["rows"] == 6 and res["fresh"] is False
    assert res["replaced"] == [] and isinstance(res["chart_id"], int)
    assert set(res["stages"]) == {"read", "trajectories", "hash", "kernel", "write"}
    tid = str(res["table_id"])
    assert ["objects.changed", {"kind": "chart", "set_id": set_id, "id": res["chart_id"]}] in res["events"]
    assert any(e[0] == "drillholes.changed" and e[1]["table"] == tid for e in res["events"])
    got = rows_of(c, set_id, tid)
    assert [x["name"] for x in got["columns"]][3:] == names[3:]
    unit = {x["name"]: x["unit"] for x in got["columns"]}
    assert (unit["AU"], unit["CU"], unit["LEN_G"], unit["TT"], unit["ALPHA"]) == ("г/т", "%", "м·г/т", "м", "°")
    for h in holes:  # Т8.5-11: 1–6 m, 5 m @ 1,16 г/т; Т8.5-12: «в том числе» 1–3 m, 2 m @ 1,75
        mine = [i for i, row in enumerate(got["rows"]) if row["hole"] == h]
        assert [(got["rows"][i]["from"], got["rows"][i]["to"]) for i in mine] == [(1.0, 6.0), (1.0, 3.0)]
        assert [col(got, "KIND")[i] for i in mine] == ["основное", "в том числе"]
        au = [col(got, "AU")[i] for i in mine]
        assert au == pytest.approx([1.16, 1.75], rel=1e-12)
        assert [col(got, "LEN_G")[i] for i in mine] == pytest.approx([5.8, 3.5], rel=1e-12)
        assert [col(got, "INCL")[i] for i in mine] == [0.0, 1.0] and [col(got, "TT_SRC")[i] for i in mine] == [
            "глобальная"] * 2  # fmt: skip
        label = col(got, "LABEL")[mine[0]]
        assert label.startswith("5,0 м @ 1,16 г/т (ИМ ") and col(got, "LABEL")[mine[1]].startswith("в т. ч. 2,0 м")
    for c_ in ref.columns:  # the job is the kernel on the same reading (the rows of a hole in its order)
        have = col(got, c_.name)
        if c_.kind == "text":
            assert have == list(c_.values), c_.name
        else:
            np.testing.assert_array_equal(numbers(have), c_.values, err_msg=c_.name)
    tt = numbers(col(got, "TT"))
    assert np.isfinite(tt).all() and (tt < numbers(col(got, "LENGTH"))).all()

    rec = records(c, set_id)
    assert len(rec) == 1 and rec[0]["stale"] is False and rec[0]["algo"] == "icpt-1", rec
    rec = rec[0]
    assert rec["table_id"] == res["table_id"] and rec["name"] == NAME and rec["makes_table"] is True
    assert rec["params"] == {"set_id": set_id, "table": table, "field": "AU", "cutoff": 1.0, "include_cutoff": 1.5,
                             "l_min": 2.0, "min_by": "length", "waste_max": 2.0, "missing": "waste", "density": None,
                             "fields": ["CU"], "orientation": [{"source": "global", "alpha": 30.0, "dipdir": 120.0}],
                             "eps": 0.1, "step": 1.0, "name": NAME}  # fmt: skip
    rep = rec["report"]
    assert rep["counts"] == {"src": {"глобальная": 6}, "flags": {"1": 0, "2": 0, "4": 0, "8": 0}}
    assert (rep["rows"], rep["intercepts"], rep["included"], rep["excluded"], rep["skipped"]) == (6, 3, 3, 0, 0)
    assert rep["length"] == pytest.approx(15.0, rel=1e-12) and rep["len_g"] == pytest.approx(17.4, rel=1e-12)
    assert rep["mean"] == pytest.approx(1.16, rel=1e-12) and (rep["waste_len"], rep["miss_len"]) == (6.0, 0.0)
    assert rep["tt"] == pytest.approx(math.fsum(tt[np.array(col(got, "KIND")) == "основное"].tolist()), rel=1e-12)
    assert (rep["unit"], rep["field"], rep["edits"]) == ("г/т", "AU", []) and rep["table_columns"] == names[3:]
    assert set(rep["versions"]) == {"numpy", "numba"} and rep["seconds"] > 0 and rep["written"] == []
    shown = next(x for x in charts(c, set_id) if x["id"] == res["chart_id"])
    assert shown["name"] == NAME
    assert shown["settings"]["origin"] == {"op": "intercepts", "table": dialect.name_key(NAME)}


def test_409_replace_and_suggested(rj) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, _holes = rj
    one = run_here(project, {"body": body(set_id, table)})
    r = c.post(f"{IC}/run", json=body(set_id, table))  # the name is taken: «Заменить?»
    assert r.status_code == 409 and r.json()["code"] == "table_exists", r.text
    e = r.json()
    assert (e["name"], e["suggested"], e["lost"]) == (NAME, f"{NAME} (2)", []) and e["hint"]
    two = run_here(project, {"body": body(set_id, table, l_min=6, replace=True)})
    assert two["replaced"] == [one["table_id"]] and two["chart_id"] == one["chart_id"] and two["rows"] == 0
    assert [t for t in tables(c, set_id) if t.startswith(NAME)] == [NAME]
    assert [x["id"] for x in records(c, set_id)] == [two["result_id"]]
    other = run_here(project, {"body": body(set_id, table, name=e["suggested"])})
    assert other["name"] == f"{NAME} (2)" and other["chart_id"] != one["chart_id"]
    assert sorted(t for t in tables(c, set_id) if t.startswith(NAME)) == [NAME, f"{NAME} (2)"]
    r = c.post(f"{IC}/run", json=body(set_id, table, cutoff=0))
    assert (r.status_code, r.json()["code"], r.json()["param"]) == (422, "bad_param", "cutoff")


def test_rebuild_fresh_and_stale(rj) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, _holes = rj
    first = run_here(project, {"body": body(set_id, table)})
    rid = first["result_id"]
    rec = records(c, set_id)[0]
    fresh = run_here(project, {"body": rec["params"], "rebuild": rid})  # the same hash: nothing computed
    assert fresh["fresh"] is True and fresh["result_id"] == rid and fresh["chart_id"] == first["chart_id"]
    samples = rows_of(c, set_id, table)
    row = samples["rows"][9]  # the 9–10 m sample of the first hole: 3,0 → 0,1 — nothing changes the table…
    k = next(x["key"] for x in samples["columns"] if x["name"] == "AU")
    r = c.patch(f"{API}/drillholes/sets/{set_id}/tables/{table}/rows/{row['id']}", json={"column": k, "value": 2.5})
    assert r.status_code == 200, r.text
    rec = records(c, set_id)[0]
    assert rec["stale"] is True and rec["reason"], rec
    r = c.post(f"{IC}/results/{rid}/rebuild")
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=240)
    assert job["status"] == "done", job
    built = job["result"]
    assert built["fresh"] is False and built["replaced"] == [first["table_id"]] and built["name"] == NAME
    rec = records(c, set_id)
    assert len(rec) == 1 and rec[0]["stale"] is False and rec[0]["id"] == built["result_id"]
    got = rows_of(c, set_id, str(built["table_id"]))
    first_hole = got["rows"][0]["hole"]
    mine = [(x["from"], x["to"]) for x in got["rows"] if x["hole"] == first_hole]
    assert mine == [(1.0, 6.0)]  # 9–10 m of 2,5 g/t is 1 m long: still shorter than L_min


def test_cancel_leaves_nothing(rj) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, _holes = rj
    before = sorted(tables(c, set_id))
    seen = [0]
    done = run_here(project, {"body": body(set_id, table)}, seen=seen)
    r = c.delete(f"{IC}/results/{done['result_id']}")
    assert r.status_code == 200 and r.json()["deleted_charts"] == [done["chart_id"]], r.text
    assert charts(c, set_id) == [] and sorted(tables(c, set_id)) == before
    assert seen[0] >= 4
    for at in range(1, seen[0] + 1):
        with pytest.raises(JobCancelled):
            run_here(project, {"body": body(set_id, table)}, cancel_at=at)
        with dialect.read_tx(project.engine) as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(dh_results)).scalar() == 0
            assert conn.execute(sa.select(sa.func.count()).where(dh_tables.c.state != "ready")).scalar() == 0
        assert sorted(tables(c, set_id)) == before and charts(c, set_id) == []


def test_preview_is_the_job_and_writes_nothing(rj) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, holes = rj
    req = body(set_id, table, include_cutoff=1.5)
    hid = hole_id_of(c, set_id, holes[1])
    r = c.post(f"{IC}/run/preview", json={"params": {**req, "name": "x", "replace": True}, "hole_id": hid})
    assert r.status_code == 200, r.text
    p = r.json()
    assert set(p) == {"hole", "unit", "samples", "candidates", "result", "summary", "mesh_skipped", "set_summary",
                      "notes"} and (p["hole"], p["unit"], p["mesh_skipped"]) == (holes[1], "г/т", False)  # fmt: skip
    s = p["samples"]
    assert s["from"] == [float(i) for i in range(N)] and s["values"]["AU"] == T11 and list(s["values"]) == ["AU", "CU"]
    assert s["ore"] == [g >= 1.0 for g in T11]
    cands = p["candidates"]
    assert [(x["from"], x["to"], x["kind"], x["accepted"], x["reason"]) for x in cands] == [
        (1.0, 6.0, "основное", True, None), (1.0, 3.0, "в том числе", True, None),
        (5.0, 6.0, "в том числе", False, "короче L_min"), (9.0, 10.0, "основное", False, "короче L_min"),
    ]  # fmt: skip
    assert cands[0]["grade"] == pytest.approx(1.16, rel=1e-12) and cands[0]["length"] == 5.0
    assert all(x["tt"] is not None and 0 < x["tt"] < x["length"] for x in cands)  # TT of the candidates too
    res = p["result"]
    assert (res["from"], res["to"]) == ([1.0, 1.0], [6.0, 3.0]) and res["values"]["KIND"] == ["основное", "в том числе"]
    assert res["values"]["TT"][0] == cands[0]["tt"]
    assert p["summary"]["intercepts"] == 1 and p["summary"]["counts"]["src"] == {"глобальная": 2}
    assert p["set_summary"]["intercepts"] == 3 and p["set_summary"]["len_g"] == pytest.approx(17.4, rel=1e-12)
    assert records(c, set_id) == [] and not [t for t in tables(c, set_id) if t.startswith(NAME)]

    done = run_here(project, {"body": req})  # the preview is the job's rows of the hole
    got = rows_of(c, set_id, str(done["table_id"]))
    mine = [i for i, row in enumerate(got["rows"]) if row["hole"] == holes[1]]
    for name, values in res["values"].items():
        assert values == [col(got, name)[i] for i in mine], name

    thick = {**req, "min_by": "tt", "l_min": cands[0]["tt"] + 0.01}  # m_min just above TT of 1–6 m
    thin = c.post(f"{IC}/run/preview", json={"params": thick, "hole_id": hid}).json()
    by_tt = [(x["from"], x["to"], x["accepted"], x["reason"]) for x in thin["candidates"] if x["kind"] == "основное"]
    assert by_tt == [(1.0, 6.0, False, "ИМ меньше m_min"), (9.0, 10.0, False, "ИМ меньше m_min")]
    assert thin["result"]["from"] == [] and thin["summary"]["tt"] is None
    bare = c.post(f"{IC}/run/preview", json={"params": {**req, "orientation": []}, "hole_id": hid}).json()
    assert bare["candidates"][0]["tt"] is None and bare["result"]["values"]["TT_SRC"] == ["нет", "нет"]
    for hole_id, code in ((10**9, 404), (None, 422)):
        r = c.post(f"{IC}/run/preview", json={"params": req, "hole_id": hole_id})
        assert r.status_code == code and r.json()["code"] == "hole_not_found" and r.json()["hint"], r.text
    r = c.post(f"{IC}/run/preview", json={"params": {**req, "l_min": 101}, "hole_id": hid})
    assert (r.status_code, r.json()["code"], r.json()["param"]) == (422, "bad_param", "l_min")


# --- SQLite and PostgreSQL --------------------------------------------------------------------------------------


@pytest.mark.db
def test_vertical_hole_on_both_databases(db_url: str, tmp_path: Path) -> None:
    """A vertical hole: TT of the intercept = LENGTH·cos α; a hole without a collar — TT empty, bit 8; the table,
    the display and the record in one transaction; the same hash — fresh."""
    from bf_core.db import migrate
    from bf_core.drillholes import store as dh
    from bf_core.intercepts import jobs_run
    from tests.dbutil import make_engine

    engine = make_engine(db_url, tmp_path)
    try:
        migrate.upgrade(engine, "project")
        with dialect.write_tx(engine) as conn:
            set_id = dh.create_set(conn, "Скважины")
            dh.replace_collars(conn, set_id, [dh.CollarRow("В-1", 100.0, 200.0, 50.0, 40.0, 0.0, -90.0)])
        holes = ["В-1"] * N + ["Б-2"] * N
        frm = [float(i) for i in range(N)] * 2
        data = dh.TableData("Пробы", "assay", holes, frm, [f + 1.0 for f in frm],
                            [dh.ColumnSpec("AU", "number", "г/т")], [T11 * 2])  # fmt: skip
        table = dh.write_table(engine, set_id, data, job_id=None, tick=lambda _p, _m=None: None)["table_id"]
        params = {"body": {"set_id": set_id, "table": str(table), "field": "AU", "cutoff": 1,
                           "orientation": GLOBAL}, "rows": 2 * N}  # fmt: skip
        out = jobs_run.run_intercepts(engine, tmp_path, params, job_id=1, tick=lambda _p=None, _m=None: None)
        assert out["rows"] == 2 and out["name"] == NAME and isinstance(out["chart_id"], int)
        from bf_core.composites import source

        got = source.read(engine, set_id, int(out["table_id"]), ["TT", "TT_FLAG", "LENGTH", "AU"])
        at = {got.holes[int(h)]: i for i, h in enumerate(got.hole_of())}
        i = at["В-1"]
        assert math.isclose(got.numbers["TT"][i], 5.0 * math.cos(math.radians(30.0)), rel_tol=1e-12)
        assert got.numbers["TT_FLAG"][i] == 0 and math.isclose(got.numbers["AU"][i], 1.16, rel_tol=1e-12)
        i = at["Б-2"]
        assert math.isnan(got.numbers["TT"][i]) and got.numbers["TT_FLAG"][i] == 8
        with dialect.read_tx(engine) as conn:
            rec = conn.execute(sa.select(dh_results)).mappings().one()
            kind = conn.execute(sa.select(dh_tables.c.kind).where(dh_tables.c.id == out["table_id"])).scalar()
        assert rec["op"] == "intercepts" and rec["algo"] == "icpt-1" and kind == "interval"
        assert rec["report"]["counts"] == {"src": {"глобальная": 2}, "flags": {"1": 0, "2": 0, "4": 0, "8": 1}}
        again = jobs_run.run_intercepts(engine, tmp_path, {**params, "rebuild": rec["id"]}, job_id=2,
                                        tick=lambda _p=None, _m=None: None)  # fmt: skip
        assert again["fresh"] is True and again["chart_id"] == out["chart_id"]
    finally:
        engine.dispose()
