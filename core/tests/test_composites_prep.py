"""Jobs and API of sample preparation (ADR 0018 §3, §7, §11, решения 8, 18, 26; подшаг 12.21): the top cut P 99 by the
domains of a lithology table → AU_CUT, AU_CUT_F in «Пробы» and the report by domain (a job in a `spawn` worker);
the analysis and the preview write nothing (the threshold is applied only by «Применить»); the preview of a hole
is the job's rows; the metal equivalent; DECL_W (Σ = n); the column operations repeated on a rebuilt table of
composites; «Перестроить» with the same hash does not compute; a cancel leaves nothing; refusals {code, message,
hint, param} (StatsError too). Coding by wireframes (`composite.code`, 12.21 + 12.7): the ore body of the demo →
«Пробы — коды» with the samples cut at its boundary (a `spawn` worker); without the division — INSIDE_FRAC; the
preview of a hole is the job's rows; an open wireframe — 422 `wireframe_open` before the job."""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.composites import jobs_prep as jp
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_results
from bf_core.jobs.context import JobCancelled
from bf_core.projects.service import projects_of
from tests.test_composites_jobs import add_lithology, rows_of, run_here, tables
from tests.test_composites_jobs import body as run_body
from tests.test_import_job import API, wait_job

C = f"{API}/composites"
MODULES = ("bf_core.composites.jobs", "bf_core.composites.jobs_prep", "bf_core.composites.jobs_coding")
_ids = itertools.count(95_001)


@pytest.fixture()
def demo(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from bf_core.jobs.registry import HANDLER_MODULES

    extra = [m for m in MODULES if m not in HANDLER_MODULES]  # until the lines of the orchestrator (12.20, 12.21)
    if extra:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", ",".join(extra))
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    return pclient, project, set_id, tables(pclient, set_id)["Пробы"]["table"]


def prep_here(project: Any, params: dict[str, Any], op: str, cancel_at: int | None = None,
              seen: list[int] | None = None) -> dict[str, Any]:  # fmt: skip
    seen = [0] if seen is None else seen

    def tick(_p: float | None = None, _m: str | None = None) -> None:
        seen[0] += 1
        if cancel_at is not None and seen[0] >= cancel_at:
            raise JobCancelled()

    return jp.run_prep(project.engine, project.folder, params, op, job_id=next(_ids), tick=tick)


def col(got: dict[str, Any], name: str) -> np.ndarray:
    key = next(c["key"] for c in got["columns"] if c["name"] == name)
    return np.array([np.nan if r[key] is None else r[key] for r in got["rows"]], dtype=float)


def results(c: Any, set_id: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = c.get(f"{C}/results?set_id={set_id}").json()["results"]
    return out


def test_topcut_p99_by_domains_in_a_worker(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table = demo
    litho, _cuts, bare = add_lithology(project, set_id)
    req = {
        "set_id": set_id,
        "table": table,
        "field": "AU",
        "domain": {"table": litho, "field": "LITH"},
        "percentile": 99,
    }
    r = c.post(f"{C}/topcut", json=req)
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=180)
    assert job["status"] == "done", job
    res = job["result"]
    assert ["composites.changed", {"set_id": set_id}] in res["events"]
    assert any(e[0] == "drillholes.changed" and e[1]["table"] == table for e in res["events"])
    got = rows_of(c, set_id, table)
    unit = {x["name"]: x["unit"] for x in got["columns"]}
    assert unit["AU_CUT"] == "г/т" and unit["AU_CUT_F"] is None
    au, cut, flag = col(got, "AU"), col(got, "AU_CUT"), col(got, "AU_CUT_F")
    np.testing.assert_array_equal(np.isnan(cut), np.isnan(au))  # a missing value stays missing, never 0
    np.testing.assert_array_equal(np.isnan(flag), np.isnan(au))
    have = ~np.isnan(au)
    assert np.all(cut[have] <= au[have]) and np.all((flag[have] == 1) == (au[have] > cut[have]))
    rec = next(x for x in results(c, set_id) if x["op"] == "topcut")
    assert rec["name"] == "Урезка · AU" and rec["params"] == req and rec["table_id"] == int(table)
    assert rec["written"] == [{"table": rec["inputs"]["tables"][0]["key"], "columns": ["AU_CUT", "AU_CUT_F"]}]
    rep = rec["report"]
    assert rep["mode"] == "percentile" and rep["columns"] == ["AU_CUT", "AU_CUT_F"] and rep["edits"] == []
    domains = {d["domain"]: d for d in rep["domains"]}
    assert {"гранит", "диорит", ""} <= set(domains), domains  # «» — the hole the lithology does not have
    thr = rep["thresholds"]
    assert set(cut[flag == 1].tolist()) <= {v for v in thr.values() if v is not None}
    assert rep["total"]["n"] == int(have.sum()) and rep["summary"]["after"]["fields"]["AU"]["metal"] > 0
    bare_rows = [i for i, x in enumerate(got["rows"]) if x["hole"] == bare and not math.isnan(au[i])]
    assert all(cut[i] == min(au[i], thr[""]) for i in bare_rows)
    assert len(rec["inputs"]["tables"]) == 2 and rec["stale"] is False

    # «Перестроить» with the same data: no computation; the record of the operation stays one
    r = c.post(f"{C}/results/{rec['id']}/rebuild", json={})
    assert r.status_code == 200, r.text
    again = wait_job(c, r.json()["source"], r.json()["id"], timeout=180)
    assert again["status"] == "done" and again["result"]["fresh"] is True, again


def test_analysis_and_preview_write_nothing_and_the_preview_is_the_job(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table = demo
    before = rows_of(c, set_id, table)
    src = {"set_id": set_id, "table": table, "field": "AU"}
    r = c.post(f"{C}/topcut/analysis", json=src)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["field"] == "AU" and a["limits"] == {"decile": 40.0, "percentile": 10.0}
    d = a["domains"][0]
    assert len(d["curves"]["p"]) == 100 and len(d["deciles"]) == 10 and len(d["top"]) == 10
    assert d["n"] == int((~np.isnan(col(before, "AU"))).sum())
    hole = c.get(f"{API}/drillholes/sets/{set_id}/tables/collar?limit=1").json()["rows"][0]
    req = {**src, "percentile": 95}
    r = c.post(f"{C}/topcut/preview", json={"params": req, "hole_id": hole["hole_id"]})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["hole"] == hole["hole"] and set(p["result"]["values"]) == {"AU_CUT", "AU_CUT_F"}
    assert p["summary"]["before"]["count"] == len(p["samples"]["from"]) > 0 and p["set_summary"] is not None
    assert set(p["thresholds"]) == {""}
    eq = {**src, "base": "AU", "base_price": 1800, "others": "CU 9000 0,9"}
    r = c.post(f"{C}/equivalent/preview", json={"params": eq, "hole_id": hole["hole_id"]})
    assert r.status_code == 200 and r.json()["result"]["values"]["AUEQ"], r.text
    now = rows_of(c, set_id, table)  # neither the analysis nor the previews wrote a column (решение 8)
    assert [x["name"] for x in now["columns"]] == [x["name"] for x in before["columns"]]
    assert results(c, set_id) == []

    done = prep_here(project, {"body": req}, "topcut")
    got = rows_of(c, set_id, table)
    key = next(x["key"] for x in got["columns"] if x["name"] == "AU_CUT")
    mine = [x[key] for x in got["rows"] if x["hole"] == hole["hole"]]
    assert mine == p["result"]["values"]["AU_CUT"]  # the preview of a hole is the job's rows, missing — null
    rec = next(x for x in results(c, set_id) if x["id"] == done["result_id"])
    assert rec["report"]["summary"] == p["set_summary"]


def test_equivalent_field(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table = demo
    req = {"set_id": set_id, "table": table, "base": "AU", "base_price": 1800, "others": "CU 9000 0,9"}
    done = prep_here(project, {"body": req}, "equivalent")
    got = rows_of(c, set_id, table)
    au, cu, eq = col(got, "AU"), col(got, "CU"), col(got, "AUEQ")
    factor = (9000 * 0.9 * 0.01) / (1800 * 0.9 / 31.1034768)  # % and $/т — 0,01; г/т and $/унц — 1/31,1034768
    np.testing.assert_allclose(eq, au + cu * factor, rtol=1e-12)  # NaN in any metal — NaN
    assert {x["name"]: x["unit"] for x in got["columns"]}["AUEQ"] == "г/т"
    rec = next(x for x in results(c, set_id) if x["id"] == done["result_id"])
    assert rec["name"] == "Эквивалент · AUEQ" and rec["report"]["factors"][0]["factor"] == pytest.approx(factor)
    assert set(rec["report"]["summary"]["after"]["fields"]) == {"AU", "CU", "AUEQ"}
    again = prep_here(project, {"body": {**req, "base_price": 2000}}, "equivalent")  # a repeat overwrites its own
    assert [x["id"] for x in results(c, set_id)] == [again["result_id"]]
    r = c.post(f"{C}/equivalent", json={**req, "name": "Номер пробы"})
    assert r.status_code == 409 and r.json()["code"] == "column_exists" and r.json()["hint"], r.text
    r = c.post(f"{C}/equivalent", json={**req, "base_unit": "кг"})
    assert r.status_code == 422 and r.json()["param"] == "base" and r.json()["message"], r.text


def test_declustering_weights_sum_to_n(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table = demo
    for method, extra in (("cell", {"cmin": 10, "cmax": 200, "ncell": 10}), ("polygon", {}),
                          ("cell3d", {"cmin": 10, "cmax": 200, "ncell": 5, "anisz": 0.5})):  # fmt: skip
        req = {"set_id": set_id, "table": table, "field": "AU", "method": method, **extra}
        done = prep_here(project, {"body": req}, "declus")
        w = col(rows_of(c, set_id, table), jp.DECL_W)
        have = ~np.isnan(w)
        assert math.isclose(float(w[have].sum()), float(have.sum()), rel_tol=1e-9), method
        rec = next(x for x in results(c, set_id) if x["id"] == done["result_id"])
        rep = rec["report"]
        assert rep["method"] == method and rep["n"] == int(have.sum()) and rec["name"] == "Декластеризация · AU"
        if method != "polygon":
            assert len(rep["sizes"]) == len(rep["means"]) == extra["ncell"] + 1  # ncell + 1 sizes
    assert [x["op"] for x in results(c, set_id)] == ["declus"]  # DECL_W of a repeat replaces the earlier record
    r = c.post(f"{C}/declus", json={"set_id": set_id, "table": table, "field": "AU", "method": "cell", "cmax": 100})
    assert r.status_code == 422 and r.json()["code"] == "bad_param" and r.json()["param"] == "cmin", (
        r.text
    )  # StatsError
    assert r.json()["message"] and r.json()["hint"]
    r = c.post(f"{C}/declus/preview", json={"params": {"set_id": set_id, "table": table}, "hole_id": 1})
    assert r.status_code == 422 and r.json()["code"] == "no_preview"


def test_column_operations_follow_a_rebuilt_table(demo) -> None:  # type: ignore[no-untyped-def]
    """Решение 18: composites + top cut + equivalent + DECL_W → «Перестроить» after an edit of the source → the
    columns on the new table, their records moved there."""
    c, project, set_id, table = demo
    first = run_here(project, {"body": run_body(set_id, table)})
    tid = str(first["table_id"])
    ops = {
        "topcut": {"field": "AU", "value": 1.0},
        "equivalent": {"base": "AU", "base_price": 1800, "others": "CU 9000 0,9"},
        "declus": {"field": "AU", "method": "cell", "cmin": 10, "cmax": 100, "ncell": 4},
    }
    for op, extra in ops.items():
        prep_here(project, {"body": {"set_id": set_id, "table": tid, **extra}}, op)
    src = rows_of(c, set_id, table)
    au = next(x["key"] for x in src["columns"] if x["name"] == "AU")
    r = c.patch(
        f"{API}/drillholes/sets/{set_id}/tables/{table}/rows/{src['rows'][0]['id']}", json={"column": au, "value": 77.0}
    )
    assert r.status_code == 200, r.text
    rid = next(x["id"] for x in results(c, set_id) if x["op"] == "length")
    assert c.post(f"{C}/run", json=run_body(set_id, table)).json()["lost"] == []  # all three can be repeated
    built = run_here(project, {"body": {**run_body(set_id, table), "name": "Композиты 2 м"}, "rebuild": rid})
    new = built["table_id"]
    assert str(new) != tid and built["fresh"] is False
    got = rows_of(c, set_id, str(new))
    au_new = col(got, "AU")
    np.testing.assert_array_equal(col(got, "AU_CUT"), np.minimum(au_new, 1.0))
    w = col(got, jp.DECL_W)
    assert math.isclose(float(np.nansum(w)), float((~np.isnan(w)).sum()), rel_tol=1e-9)
    assert np.isfinite(col(got, "AUEQ")).any()
    listed = {x["op"]: x for x in results(c, set_id)}
    for op in ops:
        assert listed[op]["table_id"] == new and listed[op]["params"]["table"] == str(new), op
        assert listed[op]["stale"] is False, listed[op]


def test_cancel_leaves_nothing(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table = demo
    req = {"set_id": set_id, "table": table, "field": "AU", "percentile": 99}
    seen = [0]
    done = prep_here(project, {"body": req}, "topcut", seen=seen)
    assert c.delete(f"{C}/results/{done['result_id']}").status_code == 200
    names = [x["name"] for x in rows_of(c, set_id, table)["columns"]]
    assert "AU_CUT" not in names and seen[0] >= 3
    for at in range(1, seen[0] + 1):
        with pytest.raises(JobCancelled):
            prep_here(project, {"body": req}, "topcut", cancel_at=at)
        with dialect.read_tx(project.engine) as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(dh_results)).scalar() == 0
            live = conn.execute(
                sa.select(dh_columns.c.name).where(dh_columns.c.table_id == int(table), dh_columns.c.deleted.is_(False))
            ).scalars()
            assert "AU_CUT" not in set(live)


def test_refusals_say_why(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table = demo
    src = {"set_id": set_id, "table": table, "field": "AU"}
    for extra, param in (({"percentile": 80}, "percentile"), ({"value": -1}, "value"),
                         ({"thresholds": "ОК 30"}, "thresholds"), ({"field": ""}, "field")):  # fmt: skip
        r = c.post(f"{C}/topcut", json={**src, **extra})
        assert r.status_code == 422 and r.json()["param"] == param and r.json()["hint"], r.text
    r = c.post(f"{C}/topcut", json={**src, "field": "НЕТ"})
    assert r.status_code == 422 and r.json()["code"] == "field_not_found"
    r = c.post(f"{C}/topcut", json={**src, "domain": {"table": table, "field": ""}})
    assert r.status_code == 422 and r.json()["code"] == "domain_missing"
    r = c.post(f"{C}/topcut/analysis", json={**src, "decile_limit": 140})
    assert r.status_code == 422 and r.json()["param"] == "decile_limit"
    wf = wireframe_ids(c)
    r = c.post(f"{C}/topcut", json={**src, "percentile": 99, "domain": {"wireframe_ids": [wf["Рудное тело 1"]]}})
    assert r.status_code == 422 and r.json()["code"] == "not_ready" and "CODE" in r.json()["hint"], r.text
    code = {"set_id": set_id, "table": table}
    for path, extra in (("/coding", {}), ("/coding/preview", None)):
        for ids, want in (([wf["Разлом"]], "wireframe_open"), ([10**9], "wireframe_not_found"), ([], "bad_param"),
                          ([wf["Рудное тело 1"]] * 2, "bad_param")):  # fmt: skip
            req = {**code, "wireframe_ids": ids}
            r = c.post(f"{C}{path}", json=req if extra is not None else {"params": req, "hole_id": 1})
            assert r.status_code == 422 and r.json()["code"] == want, r.text
            assert r.json()["param"] == "wireframe_ids" and r.json()["hint"], r.text
    r = c.post(f"{C}/coding", json={**code, "wireframe_ids": [wf["Рудное тело 1"]], "split": "да"})
    assert r.status_code == 422 and r.json()["param"] == "split"
    r = c.post(f"{C}/coding", json={**code, "wireframe_ids": [wf["Рудное тело 1"]], "name": "Пробы"})
    assert r.status_code == 422 and r.json()["code"] == "name_is_source"
    project.read_only = True  # opened in another window (ТЗ 5.7)
    try:
        r = c.post(f"{C}/topcut", json={**src, "percentile": 99})
        assert r.status_code == 423 and r.json()["hint"], r.text
        assert c.post(f"{C}/topcut/analysis", json=src).status_code == 200  # reading is allowed
    finally:
        project.read_only = False


# --- coding by wireframes ------------------------------------------------------------------------------------


def wireframe_ids(c: Any) -> dict[str, int]:
    return {w["name"]: w["id"] for w in c.get(f"{API}/wireframes").json()["wireframes"]}


def by_hole(got: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(got["rows"], key=lambda r: (r["hole"], r["from"], r["to"])):
        out.setdefault(row["hole"], []).append(row)
    return out


def test_coding_by_the_ore_body_cuts_samples_at_its_boundary_in_a_worker(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table = demo
    body = wireframe_ids(c)["Рудное тело 1"]
    req = {"set_id": set_id, "table": table, "wireframe_ids": [body]}  # the division by default (8.2.2)
    r = c.post(f"{C}/coding", json=req)
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=180)
    assert job["status"] == "done", job
    res = job["result"]
    assert ["composites.changed", {"set_id": set_id}] in res["events"] and not res["fresh"]
    t = tables(c, set_id)["Пробы — коды"]
    assert t["kind"] == "interval" and t["origin"]["op"] == "coding" and t["origin"]["stale"] is False
    got, src = rows_of(c, set_id, t["table"]), rows_of(c, set_id, table)
    names = [col["name"] for col in got["columns"]]
    assert "CODE" in names and "AU" in names and "INSIDE_FRAC" not in names
    key = {col["name"]: col["key"] for col in got["columns"]}
    skey = {col["name"]: col["key"] for col in src["columns"]}
    assert {row[key["CODE"]] for row in got["rows"]} == {"Рудное тело 1", None}
    rec = next(x for x in results(c, set_id) if x["op"] == "coding")
    rep = rec["report"]
    assert rep["split"] is True and rep["cut"] > 0 and rep["rows"] == len(got["rows"]) > rep["rows_in"]
    assert rep["kernel"]["wireframes"] == [{"id": body, "name": "Рудное тело 1"}] and rep["summary"]["after"]
    assert rec["inputs"]["meshes"][0]["name"] == "Рудное тело 1"
    # the parts tile every sample (its FROM and TO bit for bit) and copy its values; codes change only at a cut
    parts, samples = by_hole(got), by_hole(src)
    split_seen = overlapping = 0
    for hole, rows in samples.items():
        mine = parts[hole]
        k, prev_to = 0, -1e9
        for s in rows:
            if s["from"] < prev_to - 1e-6:  # the overlapping row of the demo (for «Аудит») is left out, journalled
                overlapping += 1
                continue
            prev_to, first = s["to"], k
            while k < len(mine) and mine[k]["to"] <= s["to"] + 1e-9:
                assert mine[k][key["AU"]] == s[skey["AU"]]
                k += 1
            assert mine[first]["from"] == s["from"] and mine[k - 1]["to"] == s["to"]
            assert all(mine[i]["to"] == mine[i + 1]["from"] for i in range(first, k - 1))
            if k - first > 1:
                split_seen += 1
                assert len({mine[i][key["CODE"]] for i in range(first, k)}) > 1
        assert k == len(mine)
    assert split_seen == rep["cut"] and overlapping == 1 and any("перекрывающих" in x for x in rep["journal"])
    # «Перестроить» with the same inputs does not compute
    r = c.post(f"{C}/results/{rec['id']}/rebuild", json={})
    assert r.status_code == 200, r.text
    again = run_coding_here(project, {"body": {**rec["params"], "set_id": set_id, "name": t["name"],
                                                "table": table, "replace": True}, "rebuild": rec["id"]})  # fmt: skip
    assert again["fresh"] is True


def run_coding_here(project: Any, params: dict[str, Any]) -> dict[str, Any]:
    from bf_core.composites import jobs_coding as jc

    return jc.run_coding(project.engine, project.folder, params, job_id=next(_ids), tick=lambda *_a: None)


def test_coding_without_division_and_the_preview_is_the_job(demo) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table = demo
    wf = wireframe_ids(c)
    req = {"set_id": set_id, "table": table, "wireframe_ids": [wf["Рудное тело 2"], wf["Рудное тело 1"]],
           "split": False, "name": "Коды по середине"}  # fmt: skip
    done = run_coding_here(project, {"body": req})
    got = rows_of(c, set_id, str(done["table_id"]))
    key = {col["name"]: col["key"] for col in got["columns"]}
    assert len(got["rows"]) == len(rows_of(c, set_id, table)["rows"]) - 1  # the samples (but the overlapping one)
    frac = np.array([np.nan if r[key["INSIDE_FRAC"]] is None else r[key["INSIDE_FRAC"]] for r in got["rows"]])
    assert np.all((frac[~np.isnan(frac)] >= 0) & (frac[~np.isnan(frac)] <= 1))
    assert np.any((frac > 0) & (frac < 1)) and np.any(frac == 1.0)  # samples on the boundary, inside
    codes = {r[key["CODE"]] for r in got["rows"]}
    assert {"Рудное тело 1", "Рудное тело 2"} <= codes
    # the preview of a hole on the boundary: the rows of the job (split: the parts)
    hole = next(r["hole"] for r, f in zip(got["rows"], frac, strict=True) if 0 < f < 1)
    collars = c.get(f"{API}/drillholes/sets/{set_id}/tables/collar?limit=1000").json()["rows"]
    hole_id = next(h["hole_id"] for h in collars if h["hole"] == hole)
    for split in (False, True):
        r = c.post(f"{C}/coding/preview", json={"params": {**req, "split": split}, "hole_id": hole_id})
        assert r.status_code == 200, r.text
        a = r.json()
        assert a["hole"] == hole and a["set_summary"] is None and a["summary"]["before"]
        vals = a["result"]["values"]
        assert ("INSIDE_FRAC" in vals) is (not split) and len(vals["CODE"]) == len(a["result"]["from"])
        if not split:
            mine = [row for row in got["rows"] if row["hole"] == hole]
            assert vals["CODE"] == [row[key["CODE"]] for row in mine]
            assert vals["INSIDE_FRAC"] == [row[key["INSIDE_FRAC"]] for row in mine]
        else:
            assert len(a["result"]["from"]) > len(a["samples"]["from"])
