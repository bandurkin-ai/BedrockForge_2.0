"""Jobs and API of composites (ADR 0018 §2–3, §11, решения 18, 24, 26; подшаг 12.20): the demo → «Композиты 2 м»
with the fields of 8.2.3 and the metadata (a job in a `spawn` worker); «Перестроить» with the same hash does not
compute, after an edit of the source it does; the column operations on a replaced table are repeated; a cancel
leaves nothing; values out of the ranges — 422 {code, message, hint}; «только просмотр» — 423; the preview of one
hole and the summary of the set; result tables are read only (cells and rows — 422, columns may be added).

Domains by a lithology table: no composite crosses a change of the code, DOMAIN is the code; benches: no composite
crosses a level. TODO(12.7): domains by wireframes — 422 `not_ready`."""

from __future__ import annotations

import itertools
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.composites import jobs as cj
from bf_core.composites import sink
from bf_core.db import dialect
from bf_core.db.schema import dh_results, dh_tables
from bf_core.drillholes.store import ColumnSpec
from bf_core.jobs.context import JobCancelled
from bf_core.projects.service import projects_of
from tests.test_import_job import API, wait_job

C = f"{API}/composites"
COLUMNS = ["LENGTH", "X", "Y", "Z", "AU", "CU", "DENSITY", "SAMPLED_LEN", "N_SAMPLES", "DOMAIN", "FLAG"]
_ids = itertools.count(90_001)


@pytest.fixture()
def demo(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from bf_core.jobs.registry import HANDLER_MODULES

    if "bf_core.composites.jobs" not in HANDLER_MODULES:  # until the line of the orchestrator (12.20)
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.composites.jobs")
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    sets = pclient.get(f"{API}/drillholes/sets").json()["sets"]
    set_id = sets[0]["id"]
    table = next(
        t for t in pclient.get(f"{API}/drillholes/sets/{set_id}/tables").json()["tables"] if t["name"] == "Пробы"
    )
    return pclient, project, set_id, table["table"]


def body(set_id: int, table: str, **kw: Any) -> dict[str, Any]:
    return {"set_id": set_id, "table": table, "fields": ["AU", "CU"], "method": "length", "length": 2, **kw}


def run_here(
    project: Any, params: dict[str, Any], cancel_at: int | None = None, seen: list[int] | None = None
) -> dict[str, Any]:
    """The handler in this process (as the worker runs it); `cancel_at` — the tick that cancels; `seen` counts."""
    seen = [0] if seen is None else seen

    def tick(_p: float | None = None, _m: str | None = None) -> None:
        seen[0] += 1
        if cancel_at is not None and seen[0] >= cancel_at:
            raise JobCancelled()

    return cj.run(project.engine, project.folder, params, job_id=next(_ids), tick=tick)


def tables(c: Any, set_id: int) -> dict[str, dict[str, Any]]:
    return {t["name"]: t for t in c.get(f"{API}/drillholes/sets/{set_id}/tables").json()["tables"]}


def rows_of(c: Any, set_id: int, table: str) -> dict[str, Any]:
    """All rows of a table through the API of the «Таблица» panel (pages of 1 000)."""
    out: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    while True:
        r = c.get(f"{API}/drillholes/sets/{set_id}/tables/{table}?limit=1000&offset={len(rows)}")
        assert r.status_code == 200, r.text
        out = r.json()
        rows += out["rows"]
        if len(out["rows"]) < 1000:
            return {**out, "rows": rows}


def test_demo_composites_in_a_worker_rebuild_and_stale(demo) -> None:  # type: ignore[no-untyped-def]
    c, _project, set_id, table = demo
    r = c.post(f"{C}/run", json=body(set_id, table))
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=180)
    assert job["status"] == "done", job
    res = job["result"]
    assert ["composites.changed", {"set_id": set_id}] in res["events"]
    t = tables(c, set_id)["Композиты 2 м"]
    assert t["kind"] == "interval" and t["origin"] == {"result_id": res["result_id"], "op": "length", "stale": False,
                                                       "reason": None}  # fmt: skip
    got = rows_of(c, set_id, t["table"])
    names = [col["name"] for col in got["columns"]]
    assert all(n in names for n in COLUMNS), names
    unit = {col["name"]: col["unit"] for col in got["columns"]}
    assert unit["AU"] == "г/т" and unit["CU"] == "%" and unit["LENGTH"] == "м"
    key = {col["name"]: col["key"] for col in got["columns"]}
    lengths = np.array([row[key["LENGTH"]] for row in got["rows"]], dtype=float)
    assert len(lengths) == res["rows"] and np.all(lengths > 0) and np.all(lengths <= 3.0 + 1e-9)
    xyz = np.array([[row[key[k]] for k in "XYZ"] for row in got["rows"]], dtype=float)
    assert np.isfinite(xyz).all() and (xyz[:, 0] > 4900).all()  # the middles on the trajectories of the demo
    rec = next(x for x in c.get(f"{C}/results?set_id={set_id}").json()["results"] if x["id"] == res["result_id"])
    assert rec["params"] == body(set_id, table) and rec["algo"] == cj.ALGO["length"] and len(rec["sha256"]) == 64
    rep = rec["report"]
    assert rep["edits"] == [] and rep["seconds"] > 0 and rep["kernel"]["length"] == 2.0
    assert rep["summary"]["after"]["count"] == res["rows"] and set(rep["summary"]["before"]["fields"]) == {"AU", "CU"}
    assert rep["versions"]["numpy"] and rec["stale"] is False

    # «Перестроить» with the same inputs: no computation, the same table
    r = c.post(f"{C}/results/{res['result_id']}/rebuild", json={})
    assert r.status_code == 200, r.text
    again = wait_job(c, r.json()["source"], r.json()["id"], timeout=180)
    assert again["status"] == "done" and again["result"]["fresh"] is True, again
    assert tables(c, set_id)["Композиты 2 м"]["table"] == t["table"]

    # an edit of the source: «устарел», «Перестроить» computes a new table of the same name
    src = rows_of(c, set_id, table)
    au = next(col["key"] for col in src["columns"] if col["name"] == "AU")
    first = src["rows"][0]
    r = c.patch(f"{API}/drillholes/sets/{set_id}/tables/{table}/rows/{first['id']}", json={"column": au, "value": 99.5})
    assert r.status_code == 200, r.text
    stale = tables(c, set_id)["Композиты 2 м"]["origin"]
    assert stale["stale"] is True and "Пробы" in stale["reason"]
    r = c.post(f"{C}/results/{res['result_id']}/rebuild", json={})
    built = wait_job(c, r.json()["source"], r.json()["id"], timeout=180)
    assert built["status"] == "done" and built["result"]["fresh"] is False, built
    now = tables(c, set_id)["Композиты 2 м"]
    assert now["table"] != t["table"] and now["origin"]["stale"] is False
    assert [x["name"] for x in c.get(f"{C}/results?set_id={set_id}").json()["results"]] == ["Композиты 2 м"]


def test_refusals_say_why(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table = demo
    cases = [("length", 0.01), ("p_min", 2), ("remainder", "half"), ("origin", 5)]
    for param, value in cases:
        bad = body(set_id, table, **{param: value})
        r = c.post(f"{C}/run", json=bad)
        assert r.status_code == 422 and r.json()["code"] == "bad_param" and r.json()["param"] == param, r.text
        assert r.json()["message"] and r.json()["hint"]
    r = c.post(f"{C}/run", json=body(set_id, table, method="domain", domain={"wireframe_ids": [1]}))  # TODO(12.7)
    assert r.status_code == 422 and r.json()["code"] == "not_ready" and r.json()["hint"]
    r = c.post(f"{C}/run", json=body(set_id, table, method="domain"))
    assert r.status_code == 422 and r.json()["code"] == "domain_missing" and r.json()["param"] == "domain"
    r = c.post(f"{C}/run", json=body(set_id, table, method="bench", bench=60))
    assert r.status_code == 422 and r.json()["param"] == "bench"
    r = c.post(f"{C}/run", json=body(set_id, table, fields=["НЕТ"]))
    assert r.status_code == 422 and r.json()["code"] == "field_not_found"
    r = c.post(f"{C}/run", json=body(set_id, "999999"))
    assert r.status_code == 404 and r.json()["code"] == "table_not_found"
    r = c.post(f"{C}/run", json=body(set_id, table, name="Пробы"))
    assert r.status_code == 422 and r.json()["code"] == "name_is_source"
    for path in ("/merge", "/coords"):  # TODO(12.5, 12.6)
        assert c.post(f"{C}{path}", json={"set_id": set_id}).json()["code"] == "not_ready"
    project.read_only = True  # opened in another window (ТЗ 5.7)
    try:
        r = c.post(f"{C}/run", json=body(set_id, table))
        assert r.status_code == 423 and r.json()["hint"], r.text
    finally:
        project.read_only = False


def test_name_taken_replace_and_dependents_repeated(demo, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """409 `table_exists` with the free name; «Заменить» and «Перестроить» repeat the top cut on the new table and
    move its record there (решение 18; the top cut itself is 12.21 — a stand-in here)."""
    c, project, set_id, table = demo
    first = run_here(project, {"body": body(set_id, table)})
    r = c.post(f"{C}/run", json=body(set_id, table))
    assert r.status_code == 409 and r.json()["code"] == "table_exists", r.text
    assert (
        r.json()["name"] == "Композиты 2 м" and r.json()["suggested"] == "Композиты 2 м (2)" and r.json()["lost"] == []
    )

    # a top cut on the composites: AU_CUT written in place by a column operation
    tid = first["table_id"]
    out = rows_of(c, set_id, str(tid))
    key = {col["name"]: col["key"] for col in out["columns"]}
    ids = np.array([row["id"] for row in out["rows"]], dtype=np.int64)
    au = np.array([np.nan if row[key["AU"]] is None else row[key["AU"]] for row in out["rows"]], dtype=float)
    with dialect.read_tx(project.engine) as conn:
        from bf_core.composites import results

        inputs = results.make_inputs(conn, set_id, [(tid, ["AU"])])
    sink.commit(project.engine, sink.Commit(set_id=set_id, op="topcut", name="Урезка · AU", params={"value": 1.0},
                                            inputs=inputs, sha256="0" * 64, algo="topcut-test", report={},
                                            columns=[sink.ColumnsOut(tid, ids, [(ColumnSpec("AU_CUT", "number", "г/т"),
                                                                                 np.minimum(au, 1.0))])]))  # fmt: skip
    r = c.post(f"{C}/run", json=body(set_id, table))
    assert [x["title"] for x in r.json()["lost"]] == ["Урезка"]  # «Заменить?» lists what goes

    def again(new: sink.OutTable, rec: dict[str, Any]) -> cj.Again:
        k = next(i for i, s in enumerate(new.columns) if s.name == "AU")
        cut = np.minimum(np.asarray(new.values[k], dtype=float), rec["params"]["value"])
        return cj.Again([(ColumnSpec("AU_CUT", "number", "г/т"), cut)], ["AU"])

    monkeypatch.setitem(cj.DEPENDENTS, "topcut", again)
    replaced = run_here(project, {"body": body(set_id, table, p_min=0.9, replace=True)})
    new = replaced["table_id"]
    assert new != tid and replaced["replaced"] == [tid]
    got = rows_of(c, set_id, str(new))
    key = {col["name"]: col["key"] for col in got["columns"]}
    vals = np.array([np.nan if row[key["AU"]] is None else row[key["AU"]] for row in got["rows"]], dtype=float)
    cut = np.array([np.nan if row[key["AU_CUT"]] is None else row[key["AU_CUT"]] for row in got["rows"]], dtype=float)
    np.testing.assert_array_equal(cut, np.minimum(vals, 1.0))  # NaN stays NaN
    listed = {x["op"]: x for x in c.get(f"{C}/results?set_id={set_id}").json()["results"]}
    assert listed["topcut"]["table_id"] == new and listed["topcut"]["stale"] is False
    assert listed["topcut"]["written"][0]["columns"] == ["AU_CUT"] and listed["length"]["stale"] is False

    # «Перестроить» of a result whose top cut cannot be repeated: 409 with the list, then with drop_lost
    monkeypatch.delitem(cj.DEPENDENTS, "topcut")
    r = c.post(f"{C}/results/{listed['length']['id']}/rebuild", json={})
    assert r.status_code == 409 and r.json()["code"] == "dependents_lost" and r.json()["lost"][0]["op"] == "topcut"


def test_cancel_leaves_nothing(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table = demo
    before = sorted(tables(c, set_id))
    seen = [0]
    done = run_here(project, {"body": body(set_id, table)}, seen=seen)
    assert c.delete(f"{C}/results/{done['result_id']}").status_code == 200
    assert seen[0] >= 4
    for at in range(1, seen[0] + 1):
        with pytest.raises(JobCancelled):
            run_here(project, {"body": body(set_id, table)}, cancel_at=at)
        with dialect.read_tx(project.engine) as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(dh_results)).scalar() == 0
            assert conn.execute(sa.select(sa.func.count()).where(dh_tables.c.state == "building")).scalar() == 0
        assert sorted(tables(c, set_id)) == before


@pytest.mark.windows
def test_cancel_in_a_worker_leaves_nothing(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table = demo
    r = c.post(f"{C}/run", json=body(set_id, table, length=0.1))
    c.post(f"{API}/jobs/{r.json()['source']}/{r.json()['id']}/cancel")
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=180)
    if job["status"] == "cancelled":
        assert "Композиты 0,1 м" not in tables(c, set_id)
        with dialect.read_tx(project.engine) as conn:
            assert conn.execute(sa.select(sa.func.count()).where(dh_tables.c.state == "building")).scalar() == 0


def test_preview_of_one_hole_is_the_job_and_the_set_summary(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table = demo
    hole = c.get(f"{API}/drillholes/sets/{set_id}/tables/collar?limit=1").json()["rows"][0]
    params = body(set_id, table, name="не важно")
    r = c.post(f"{C}/length/preview", json={"params": params, "hole_id": hole["hole_id"]})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["hole"] == hole["hole"] and len(a["samples"]["from"]) == len(a["samples"]["values"]["AU"]) > 0
    res = a["result"]
    assert all(n in res["values"] for n in COLUMNS) and len(res["from"]) == len(res["values"]["LENGTH"]) > 0
    assert a["summary"]["before"]["count"] == len(a["samples"]["from"]) and a["set_summary"] is not None
    assert isinstance(a["notes"], list)
    done = run_here(project, {"body": body(set_id, table)})
    got = rows_of(c, set_id, str(done["table_id"]))
    key = {col["name"]: col["key"] for col in got["columns"]}
    mine = [row for row in got["rows"] if row["hole"] == hole["hole"]]
    assert [row[key["AU"]] for row in mine] == res["values"]["AU"]  # the preview is the job, missing — null
    assert a["set_summary"]["after"]["count"] == done["rows"]
    rec = c.get(f"{C}/results?set_id={set_id}").json()["results"][0]
    assert rec["report"]["summary"] == a["set_summary"]
    r = c.post(f"{C}/length/preview", json={"params": {**params, "length": 500}, "hole_id": hole["hole_id"]})
    assert r.status_code == 422 and r.json()["param"] == "length"
    r = c.post(f"{C}/length/preview", json={"params": params, "hole_id": 10**9})
    assert r.status_code == 404 and r.json()["code"] == "hole_not_found"
    assert (
        c.post(f"{C}/domain/preview", json={"params": params, "hole_id": hole["hole_id"]}).json()["code"] == "not_ready"
    )


def test_result_tables_are_read_only(demo) -> None:  # type: ignore[no-untyped-def]
    """ADR 0018 решение 24 (`drillholes/edit.py`): cells and rows of a result — 422 with the reason; columns may be
    added; the source stays editable."""
    c, project, set_id, table = demo
    done = run_here(project, {"body": body(set_id, table)})
    tid = str(done["table_id"])
    got = rows_of(c, set_id, tid)
    row = got["rows"][0]
    au = next(col["key"] for col in got["columns"] if col["name"] == "AU")
    base = f"{API}/drillholes/sets/{set_id}/tables/{tid}"
    for r in (
        c.patch(f"{base}/rows/{row['id']}", json={"column": au, "value": 1.0}),
        c.post(f"{base}/rows", json={"values": {"hole": row["hole"], "from": 900.0, "to": 901.0}}),
        c.delete(f"{base}/rows/{row['id']}"),
        c.post(
            f"{base}/replace", json={"column": au, "find": "" if row[au] is None else repr(row[au]), "replace": "1"}
        ),
    ):
        assert r.status_code == 422 and r.json()["code"] == "result_read_only" and "перестройте" in r.json()["hint"], (
            r.text
        )
    assert rows_of(c, set_id, tid)["rows"] == got["rows"]
    r = c.post(f"{base}/columns", json={"name": "AU2", "expression": "AU * 2"})
    assert r.status_code == 200, r.text
    src = rows_of(c, set_id, table)
    key = next(col["key"] for col in src["columns"] if col["name"] == "CU")
    r = c.patch(
        f"{API}/drillholes/sets/{set_id}/tables/{table}/rows/{src['rows'][0]['id']}",
        json={"column": key, "value": 0.25},
    )
    assert r.status_code == 200, r.text


def add_lithology(project: Any, set_id: int) -> tuple[str, list[float]]:
    """«Литология»: гранит 0–30, диорит 30–75, гранит 75–TD in every hole; one hole without it."""
    from bf_core.db.schema import dh_holes
    from bf_core.drillholes import store

    with dialect.read_tx(project.engine) as conn:
        holes = conn.execute(sa.select(dh_holes.c.name, dh_holes.c.td).where(dh_holes.c.set_id == set_id)).all()
    rows = [(h.name, a, b, lith) for h in holes[1:] for a, b, lith in ((0.0, 30.0, "гранит"), (30.0, 75.0, "диорит"),
                                                                        (75.0, float(h.td), "гранит"))]  # fmt: skip
    data = store.TableData("Литология", "interval", [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows],
                           [ColumnSpec("LITH", "text")], [[r[3] for r in rows]])  # fmt: skip
    out = store.write_table(project.engine, set_id, data, job_id=None, tick=lambda _p, _m=None: None)
    return str(out["table_id"]), [30.0, 75.0]


def test_domains_by_lithology_and_benches(demo) -> None:  # type: ignore[no-untyped-def]
    from bf_core.drillholes.trajectory import interval_xyz, trajectories

    c, project, set_id, table = demo
    litho, cuts = add_lithology(project, set_id)
    dom = body(set_id, table, method="domain", domain={"table": litho, "field": "LITH"}, length=4)
    done = run_here(project, {"body": dom})
    got = rows_of(c, set_id, str(done["table_id"]))
    key = {col["name"]: col["key"] for col in got["columns"]}
    assert done["name"] == "Композиты по доменам"
    first_hole = sorted({r["hole"] for r in got["rows"]})[0]
    for row in got["rows"]:
        a, b, code = row["from"], row["to"], row[key["DOMAIN"]]
        assert not any(a + 1e-6 < z < b - 1e-6 for z in cuts), row  # no composite crosses a change of LITH
        if code is not None:
            assert code == ("диорит" if 30.0 <= (a + b) / 2 < 75.0 else "гранит")
    assert any(r[key["DOMAIN"]] is None for r in got["rows"] if r["hole"] == first_hole)  # no lithology there
    rec = next(x for x in c.get(f"{C}/results?set_id={set_id}").json()["results"] if x["op"] == "domain")
    assert len(rec["inputs"]["tables"]) == 2 and rec["params"]["domain"] == {"table": litho, "field": "LITH"}
    hole = c.get(f"{API}/drillholes/sets/{set_id}/tables/collar?limit=3").json()["rows"][2]
    a = c.post(f"{C}/domain/preview", json={"params": dom, "hole_id": hole["hole_id"]}).json()
    mine = [r for r in got["rows"] if r["hole"] == hole["hole"]]
    assert a["result"]["values"]["DOMAIN"] == [r[key["DOMAIN"]] for r in mine] and a["set_summary"] is not None

    bench = body(set_id, table, method="bench", bench=10)
    done = run_here(project, {"body": bench})
    assert done["name"] == "Композиты по уступам 10 м"
    got = rows_of(c, set_id, str(done["table_id"]))
    rec = next(x for x in c.get(f"{C}/results?set_id={set_id}").json()["results"] if x["op"] == "bench")
    top = rec["report"]["top"]
    assert top % 10 == 0 and rec["report"]["height"] == 10
    st = trajectories(project, set_id)
    with dialect.read_tx(project.engine) as conn:
        from bf_core.db.schema import dh_holes

        ids = dict(conn.execute(sa.select(dh_holes.c.name, dh_holes.c.id).where(dh_holes.c.set_id == set_id)).all())
    hole_ids = np.array([ids[r["hole"]] for r in got["rows"]], dtype=np.int64)
    za, zb = interval_xyz(
        st, hole_ids, np.array([r["from"] for r in got["rows"]]), np.array([r["to"] for r in got["rows"]])
    )
    ka, kb = (top - za[:, 2]) / 10.0, (top - zb[:, 2]) / 10.0
    lo, hi = np.minimum(ka, kb), np.maximum(ka, kb)
    assert not np.any(np.floor(hi - 1e-6) > lo + 1e-6), "a composite crosses a bench level"
    a = c.post(f"{C}/bench/preview", json={"params": bench, "hole_id": hole["hole_id"]}).json()
    key = {col["name"]: col["key"] for col in got["columns"]}
    assert a["result"]["values"]["Z"] == [r[key["Z"]] for r in got["rows"] if r["hole"] == hole["hole"]]
