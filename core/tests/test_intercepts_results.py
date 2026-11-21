"""Results of «Истинная мощность» and «Рудные пересечения» (ADR 0021 §8, §10; подшаг 15.10): the list with «устарел»,
deletion (fields hidden, table and display gone), «Перестроить» on the current ids, the routes `tt` / `run` up to the
job, 423, the cycle `.bfggis` (ТЗ 10.5 п. 5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.charts import service as charts
from bf_core.composites import results as res
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_rows, objects
from bf_core.drillholes import actions
from bf_core.drillholes import store as dh
from bf_core.intercepts import display
from bf_core.intercepts import router as ir
from bf_core.intercepts import spec as sp
from bf_core.projects.service import projects_of
from tests.test_import_job import wait_job

API = "/api/v1"
IC = API + "/intercepts"
GLOBAL = [{"source": "global", "alpha": 30, "dipdir": 120}]


def noop(_p: float | None, _m: str | None) -> None:
    pass


@pytest.fixture()
def cc(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A core with an open project; the ops and the router are added here until the lines of the orchestrator
    (`composites/results.py` OP_TITLES / TABLE_OPS, `api/modules.py`) are in."""
    if "intercepts" not in res.OP_TITLES:
        monkeypatch.setitem(res.OP_TITLES, "tt", "Истинная мощность")
        monkeypatch.setitem(res.OP_TITLES, "intercepts", "Рудные пересечения")
        monkeypatch.setattr(res, "TABLE_OPS", res.TABLE_OPS | {"intercepts"})
    if not any(str(getattr(r, "path", "")).startswith(IC) for r in pclient.app.routes):
        pclient.app.include_router(ir.router, prefix=API)
    assert pclient.post(API + "/projects", json={"name": "Пересечения"}).status_code == 200
    return pclient


def table(name: str, kind: str, cols: list[tuple[str, str]], values: list[list[Any]]) -> dh.TableData:
    return dh.TableData(name=name, kind=kind, holes=["A", "A", "B"], from_m=[0.0, 1.0, 0.0], to_m=[1.0, 2.0, 2.0],
                        columns=[dh.ColumnSpec(n, k, None, "value") for n, k in cols], values=values)  # fmt: skip


def fill(c) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Samples with the fields of an earlier «Истинная мощность», a table «Рудные пересечения» with its display and
    the two records, as the jobs of 15.11–15.12 leave them."""
    p = projects_of(c.core).require_current()
    set_id = c.post(API + "/drillholes/sets", json={"name": "Скважины"}).json()["id"]
    cols = [("AU", "number"), ("TT", "number"), ("TT_FLAG", "number"), ("TT_SRC", "text")]
    vals = [[1.0, None, 2.0], [0.9, 0.8, 1.7], [0, 0, 0], ["глобальная"] * 3]
    src = dh.write_table(p.engine, set_id, table("Пробы", "assay", cols, vals), job_id=None, tick=noop)["table_id"]
    made = table("Рудные пересечения", "interval", [("KIND", "text"), ("LABEL", "text")],
                 [["основное"] * 3, ["1,0 м @ 1,00 г/т"] * 3])  # fmt: skip
    icpt = dh.write_table(p.engine, set_id, made, job_id=None, tick=noop)["table_id"]
    with dialect.read_tx(p.engine) as conn:
        base = {"set_id": set_id, "table": str(src), "orientation": GLOBAL}
        tts = sp.parse_tt(conn, {**base, "angles": False})
        run = sp.parse_run(conn, {**base, "field": "AU", "cutoff": 1})
    with dialect.write_tx(p.engine) as conn:
        tt_id = res.record(conn, set_id=set_id, table_id=src, op="tt", name="Истинная мощность · Пробы",
                           params=tts.body, inputs=res.make_inputs(conn, set_id, tts.tables(), trajectories=True),
                           sha256="1" * 64, algo="tt-1",
                           report={"written": [{"table": "пробы", "columns": tts.columns()}]})  # fmt: skip
        run_id = res.record(conn, set_id=set_id, table_id=icpt, op="intercepts", name="Рудные пересечения",
                            params=run.body, inputs=res.make_inputs(conn, set_id, run.tables(), trajectories=True),
                            sha256="2" * 64, algo="icpt-1", report={"table_columns": ["KIND", "LABEL"]})  # fmt: skip
        chart = display.put_display(conn, set_id, "Рудные пересечения")
    return {"set_id": set_id, "src": src, "icpt": icpt, "tt": tt_id, "run": run_id, "chart": chart, "project": p}


def results_of(c, set_id: int, where: str = IC) -> dict[str, dict[str, Any]]:  # type: ignore[no-untyped-def]
    r = c.get(where + "/results", params={"set_id": set_id})
    assert r.status_code == 200, r.text
    return {x["op"]: x for x in r.json()["results"]}


def test_list_with_stale(cc) -> None:  # type: ignore[no-untyped-def]
    f = fill(cc)
    got = results_of(cc, f["set_id"])
    assert set(got) == {"tt", "intercepts"} and not any(x["stale"] for x in got.values())
    assert got["tt"]["op_title"] == "Истинная мощность" and got["tt"]["makes_table"] is False
    assert got["tt"]["written"] == [{"table": "пробы", "columns": ["TT", "TT_FLAG", "TT_SRC"]}]
    assert got["intercepts"]["makes_table"] is True and got["intercepts"]["table"] == "Рудные пересечения"
    assert got["intercepts"]["params"]["orientation"] == [{"source": "global", "alpha": 30.0, "dipdir": 120.0}]
    assert set(results_of(cc, f["set_id"], API + "/composites")) == {"tt", "intercepts"}  # the common list too
    tabs = {t["table"]: t for t in cc.get(f"{API}/drillholes/sets/{f['set_id']}/tables").json()["tables"]}
    assert tabs[str(f["icpt"])]["origin"]["op"] == "intercepts" and tabs[str(f["src"])]["origin"] is None

    with dialect.read_tx(f["project"].engine) as conn:
        q = sa.select(dh_rows.c.id).where(dh_rows.c.table_id == f["src"]).order_by(dh_rows.c.id)
        row = conn.execute(q).scalar()
        c = dh_columns.c
        au = conn.execute(sa.select(c.id).where(c.table_id == f["src"], c.name == "AU"))
        au_key = f"c{au.scalar_one()}"
    actions.update_cell(f["project"], f["set_id"], str(f["src"]), int(row), au_key, 3.5, None)
    got = results_of(cc, f["set_id"])
    assert got["tt"]["stale"] and got["tt"]["reason"] == "изменена таблица «Пробы»"
    assert got["intercepts"]["stale"] and got["intercepts"]["reason"] == "изменена таблица «Пробы»"
    with dialect.write_tx(f["project"].engine) as conn:  # collars or surveys: TT reads the trajectories
        res.mark_fresh(conn, f["tt"], res.make_inputs(conn, f["set_id"], [(f["src"], [])], trajectories=True))
        conn.execute(sa.update(objects).where(objects.c.id == f["set_id"]).values(version=objects.c.version + 1))
    assert results_of(cc, f["set_id"])["tt"]["reason"] == "изменены устья или инклинометрия набора"


def test_delete_hides_fields_or_drops_table_and_display(cc) -> None:  # type: ignore[no-untyped-def]
    f = fill(cc)
    cc.core.events._recent.clear()
    r = cc.delete(f"{IC}/results/{f['run']}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["deleted"] == f["run"] and out["deleted_tables"] == [f["icpt"]] and out["deleted_charts"] == [f["chart"]]
    events = [(n, json.loads(d)) for _i, n, d in cc.core.events._recent]
    assert ("objects.changed", {"kind": "chart", "set_id": f["set_id"], "id": f["chart"], "deleted": True}) in events
    assert ("composites.changed", {"set_id": f["set_id"]}) in events
    with dialect.read_tx(f["project"].engine) as conn:
        assert display.chart_of(conn, f["set_id"], "Рудные пересечения") is None
    r = cc.delete(f"{IC}/results/{f['tt']}")
    assert r.status_code == 200 and r.json()["changed"][0]["table"] == str(f["src"]), r.text
    with dialect.read_tx(f["project"].engine) as conn:
        hidden = dict(conn.execute(sa.select(dh_columns.c.name, dh_columns.c.deleted).where(
            dh_columns.c.table_id == f["src"])).tuples().all())  # fmt: skip
    assert hidden == {"AU": False, "TT": True, "TT_FLAG": True, "TT_SRC": True}
    assert results_of(cc, f["set_id"]) == {}
    r = cc.delete(f"{IC}/results/{f['tt']}")
    assert r.status_code == 404 and r.json()["code"] == "result_not_found" and r.json()["hint"]
    with dialect.write_tx(f["project"].engine) as conn:  # another op: not through this router
        other = res.record(conn, set_id=f["set_id"], table_id=f["src"], op="topcut", name="Урезка", params={},
                           inputs={}, sha256="0", algo="1", report={})  # fmt: skip
    assert cc.delete(f"{IC}/results/{other}").status_code == 404
    assert cc.post(f"{IC}/results/{other}/rebuild").status_code == 404


def captured(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any], str]]:
    calls: list[tuple[str, dict[str, Any], str]] = []

    def submit(_s: Any, kind: str, params: dict[str, Any], title: str) -> dict[str, Any]:
        calls.append((kind, params, title))
        return {"source": "p", "id": len(calls)}

    monkeypatch.setattr(ir, "_submit", submit)
    return calls


def test_rebuild_on_current_tables(cc, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    f = fill(cc)
    r = cc.post(f"{IC}/results/{f['tt']}/rebuild")
    assert r.status_code == 422 and r.json()["code"] == "not_ready", r.text  # the jobs come in 15.11–15.12
    calls = captured(monkeypatch)
    assert cc.post(f"{IC}/results/{f['tt']}/rebuild").json() == {"source": "p", "id": 1}
    assert cc.post(f"{IC}/results/{f['run']}/rebuild", json={}).status_code == 200
    (k1, p1, t1), (k2, p2, t2) = calls
    assert k1 == "intercept.tt" and p1["rebuild"] == f["tt"] and p1["body"]["table"] == str(f["src"])
    assert p1["body"]["angles"] is False and t1 == "Перестроить: Истинная мощность (Пробы)"
    assert k2 == "intercept.run" and p2["rebuild"] == f["run"] and p2["body"]["replace"] is True
    assert p2["body"]["name"] == "Рудные пересечения" and p2["body"]["field"] == "AU" and p2["rows"] == 3
    with dialect.write_tx(f["project"].engine) as conn:  # the source gone: the tt record goes with it (CASCADE)
        conn.execute(sa.delete(res.dh_tables).where(res.dh_tables.c.id == f["src"]))
    r = cc.post(f"{IC}/results/{f['run']}/rebuild")
    assert r.status_code == 409 and r.json()["code"] == "source_gone" and r.json()["hint"]
    assert cc.post(f"{IC}/results/{f['tt']}/rebuild").status_code == 404
    again = table("Пробы", "assay", [("AU", "number")], [[3.0, 2.0, 1.0]])  # a new table of that name: another id
    new_src = dh.write_table(f["project"].engine, f["set_id"], again, job_id=None, tick=noop)["table_id"]
    assert new_src != f["src"] and results_of(cc, f["set_id"])["intercepts"]["reason"] == "заменена таблица «Пробы»"
    assert cc.post(f"{IC}/results/{f['run']}/rebuild").status_code == 200
    assert calls[-1][1]["body"]["table"] == str(new_src)


def test_tt_and_run_routes_up_to_the_job(cc) -> None:  # type: ignore[no-untyped-def]
    f = fill(cc)
    base = {"set_id": f["set_id"], "table": str(f["src"])}
    r = cc.post(IC + "/tt", json={**base, "orientation": GLOBAL})
    assert r.status_code == 422 and r.json()["code"] == "not_ready" and r.json()["hint"]  # own fields: no 409
    r = cc.post(IC + "/tt", json={**base, "orientation": GLOBAL, "eps": 0.6})
    assert (r.status_code, r.json()["code"], r.json()["param"]) == (422, "bad_param", "eps")
    assert "от 0,01 до 0,5" in r.json()["message"]
    r = cc.post(IC + "/tt", json={**base, "orientation": []})
    assert (r.status_code, r.json()["code"], r.json()["param"]) == (422, "no_orientation", "orientation")
    p = f["project"]
    other = dh.write_table(p.engine, f["set_id"], table("Композиты", "interval", [("ALPHA", "number")], [[1, 2, 3]]),
                           job_id=None, tick=noop)["table_id"]  # fmt: skip
    r = cc.post(IC + "/tt", json={**base, "table": str(other), "orientation": GLOBAL})
    assert (r.status_code, r.json()["code"], r.json()["name"]) == (409, "column_exists", "ALPHA")
    run = {**base, "field": "AU", "cutoff": 1}
    r = cc.post(IC + "/run", json=run)
    assert r.status_code == 409 and r.json()["code"] == "table_exists", r.text
    assert r.json()["name"] == "Рудные пересечения" and r.json()["suggested"] == "Рудные пересечения (2)"
    assert cc.post(IC + "/run", json={**run, "replace": True}).json()["code"] == "not_ready"
    assert cc.post(IC + "/run", json={**run, "name": "Пересечения 2"}).json()["code"] == "not_ready"
    assert cc.post(IC + "/run", json=[1]).json()["code"] == "bad_request"


def test_read_only_project_is_423(cc) -> None:  # type: ignore[no-untyped-def]
    f = fill(cc)
    project = f["project"]
    project.read_only = True  # opened in another window (ТЗ 5.7)
    try:
        base = {"set_id": f["set_id"], "table": str(f["src"])}
        for r in (cc.post(IC + "/tt", json={**base, "orientation": GLOBAL}),
                  cc.post(IC + "/run", json={**base, "field": "AU", "cutoff": 1, "name": "Другие"}),
                  cc.post(f"{IC}/results/{f['run']}/rebuild"), cc.delete(f"{IC}/results/{f['run']}")):  # fmt: skip
            assert r.status_code == 423 and r.json()["hint"], r.text
        assert set(results_of(cc, f["set_id"])) == {"tt", "intercepts"}  # reading goes on
    finally:
        project.read_only = False


def test_bfggis_roundtrip_keeps_results_fresh(cc, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    f = fill(cc)
    r = cc.post(API + "/projects/export", json={"path": str(tmp_path / "Пересечения")}).json()
    job = wait_job(cc, r["source"], r["id"])
    assert job["status"] == "done", job
    r = cc.post(API + "/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(cc, r["source"], r["id"])
    assert job["status"] == "done", job
    maps = job["result"]["id_maps"]
    assert cc.post(f"{API}/projects/{job['result']['uid']}/open").status_code == 200
    set_id = maps["objects"][str(f["set_id"])]
    got = results_of(cc, set_id)
    assert set(got) == {"tt", "intercepts"} and not any(x["stale"] for x in got.values())
    assert got["tt"]["table_id"] == maps["dh_tables"][str(f["src"])]
    assert got["intercepts"]["table_id"] == maps["dh_tables"][str(f["icpt"])]
    assert got["intercepts"]["sha256"] == "2" * 64 and got["tt"]["algo"] == "tt-1"
    project = projects_of(cc.core).require_current()
    with dialect.read_tx(project.engine) as conn:
        cid = display.chart_of(conn, set_id, "Рудные пересечения")
        assert cid is not None and charts.get(conn, cid)["name"] == "Рудные пересечения"
    calls = captured(monkeypatch)  # «Перестроить» finds the renumbered tables
    assert cc.post(f"{IC}/results/{got['intercepts']['id']}/rebuild").status_code == 200
    assert cc.post(f"{IC}/results/{got['tt']['id']}/rebuild").status_code == 200
    new_src = str(maps["dh_tables"][str(f["src"])])
    assert [c[1]["body"]["table"] for c in calls] == [new_src, new_src]
