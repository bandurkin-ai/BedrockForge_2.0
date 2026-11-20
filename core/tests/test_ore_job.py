"""The job and the API of ore intervals (ТЗ 7.35 Д-26 «Результат» (1)–(5); ADR 0018 §2–3, §8–11, решения 19–22, 26;
подшаг 12.22): the samples of Т8.2-12 in three holes of the demo set → the table «Рудные интервалы» (LABEL «8,0 м @
2,84 г/т (МС 22,7)», X, Y, Z, MODE, MANUAL), ORE_ID and ORE_CLS in the samples, the summary, the display
«Рудные интервалы» (a job in a `spawn` worker); a repeat overwrites the columns and points the display at the new
table; a cancel at any tick leaves nothing; the preview is the job's rows and writes nothing; the protocol of a
fresh result, 409 `stale` otherwise; «Перестроить»; «по контактам» with `contacts_values`; refusals."""

from __future__ import annotations

import itertools
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.composites import ore_job as oj
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_holes, dh_results, dh_tables, objects
from bf_core.drillholes.store import ColumnSpec
from bf_core.jobs.context import JobCancelled
from bf_core.projects.service import projects_of
from tests.oreutil import EXAMPLES
from tests.test_composites_jobs import rows_of, tables
from tests.test_import_job import API, wait_job

C = f"{API}/composites"
MODULES = ("bf_core.composites.jobs", "bf_core.composites.jobs_prep", "bf_core.composites.ore_job")
LABEL = "8,0 м @ 2,84 г/т (МС 22,7)"
_ids = itertools.count(97_001)
EX = EXAMPLES["T8.2-12"]
GRADES: list[float] = EX["grades"]
N = len(GRADES)


def add_samples(project: Any, set_id: int, *, domain: bool = False, name: str = "Пробы ГКЗ") -> tuple[str, list[str]]:
    """«Пробы ГКЗ» (AU г/т, CU %; DOM 1 at 3–12 m with `domain`): the samples of Т8.2-12, 1 m each from 0 m, in
    three holes of the set."""
    from bf_core.drillholes import store

    with dialect.read_tx(project.engine) as conn:
        holes = [r.name for r in conn.execute(sa.select(dh_holes.c.name).where(dh_holes.c.set_id == set_id))][:3]
    names = [h for h in holes for _ in range(N)]
    frm = [float(i) for _ in holes for i in range(N)]
    cols = [ColumnSpec("AU", "number", "г/т"), ColumnSpec("CU", "number", "%")]
    vals: list[list[Any]] = [GRADES * len(holes), [0.1 * (i % 7) for _ in holes for i in range(N)]]
    if domain:
        cols.append(ColumnSpec("DOM", "number"))
        vals.append([1.0 if 3 <= i < 12 else 0.0 for _ in holes for i in range(N)])
    data = store.TableData(name, "assay", names, frm, [f + 1.0 for f in frm], cols, vals)
    out = store.write_table(project.engine, set_id, data, job_id=None, tick=lambda _p, _m=None: None)
    return str(out["table_id"]), holes


@pytest.fixture()
def gkz(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from bf_core.jobs.registry import HANDLER_MODULES

    extra = [m for m in MODULES if m not in HANDLER_MODULES]  # until the line of the orchestrator (12.22)
    if extra:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", ",".join(extra))
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    table, holes = add_samples(project, set_id)
    return pclient, project, set_id, table, holes


def body(set_id: int, table: str, **kw: Any) -> dict[str, Any]:
    return {"set_id": set_id, "table": table, "field": "AU", "cutoff": 1.0, "m_min": 3.0, "m_waste_max": 5.0,
            "mc_min": 3.0, "mode": "strict_excl", "missing": "break", "thickness": {"source": "length"},
            **kw}  # fmt: skip


def ore_here(project: Any, params: dict[str, Any], cancel_at: int | None = None,
             seen: list[int] | None = None) -> dict[str, Any]:  # fmt: skip
    seen = [0] if seen is None else seen

    def tick(_p: float | None = None, _m: str | None = None) -> None:
        seen[0] += 1
        if cancel_at is not None and seen[0] >= cancel_at:
            raise JobCancelled()

    return oj.run_ore(project.engine, project.folder, params, job_id=next(_ids), tick=tick)


def cols(got: dict[str, Any]) -> dict[str, str]:
    return {c["name"]: c["key"] for c in got["columns"]}


def column(got: dict[str, Any], name: str) -> list[Any]:
    key = cols(got)[name]
    return [r[key] for r in got["rows"]]


def charts(c: Any, set_id: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = c.get(f"{API}/charts?set_id={set_id}").json()["charts"]
    return out


def ore_rows(got: dict[str, Any], hole: str) -> list[tuple[float, float, str | None]]:
    k = cols(got)
    return [(r["from"], r["to"], r[k["LABEL"]]) for r in got["rows"] if r["hole"] == hole and r[k["CLASS"]] == "ore"]


def test_t8_2_12_by_a_set_in_a_worker(gkz) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, holes = gkz
    req = body(set_id, table)
    r = c.post(f"{C}/ore", json=req)
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=240)
    assert job["status"] == "done", job
    res = job["result"]
    assert res["name"] == "Рудные интервалы" and res["set_id"] == set_id and isinstance(res["chart_id"], int)
    assert ["composites.changed", {"set_id": set_id}] in res["events"]
    assert any(e[0] == "drillholes.changed" and e[1]["table"] == table for e in res["events"])  # ORE_ID, ORE_CLS
    tid = str(res["table_id"])
    got = rows_of(c, set_id, tid)
    names = [x["name"] for x in got["columns"] if x["key"] not in ("hole", "from", "to")]
    assert names == ["LENGTH", "M", "C", "MC", "CLASS", "BASIS", "N_SAMPLES", "WASTE_LEN", "NESTED", "ORE_ID", "FLAG",
                     "DOMAIN", "AU", "CU", "MODE", "MANUAL", "X", "Y", "Z", "LABEL"]  # fmt: skip
    unit = {x["name"]: x["unit"] for x in got["columns"]}
    assert unit["C"] == "г/т" and unit["MC"] == "м·г/т" and unit["M"] == "м" and unit["AU"] == "г/т"
    for hole in holes:  # И1, сл. 10–16: ore 3–11, 17–22, 28–33 m; the label of the first
        assert ore_rows(got, hole) == [(3.0, 11.0, LABEL), (17.0, 22.0, "5,0 м @ 2,90 г/т (МС 14,5)"),
                                       (28.0, 33.0, "5,0 м @ 1,16 г/т (МС 5,8)")]  # fmt: skip
    k = cols(got)
    classes = set(column(got, "CLASS"))
    assert {"ore", "interlayer", "waste", "frame"} <= classes, classes
    for row in got["rows"]:
        assert row[k["MODE"]] == "strict_excl" and row[k["MANUAL"]] == 0
        assert all(row[k[a]] is not None for a in "XYZ"), row  # the demo holes have trajectories
        if row[k["CLASS"]] != "ore":
            assert row[k["LABEL"]] is None  # решение 20: LABEL only at `ore`
        if row[k["CLASS"]] in ("frame", "waste_in"):
            assert row[k["NESTED"]] == 1
        elif row[k["CLASS"]] not in ("ore", "subgrade"):
            assert row[k["ORE_ID"]] is None
    first = next(r for r in got["rows"] if r[k["LABEL"]] == LABEL)
    assert first[k["M"]] == 8.0 and abs(first[k["C"]] - 2.8375) < 1e-12 and abs(first[k["AU"]] - 2.8375) < 1e-12

    s = rows_of(c, set_id, table)  # the fields of the samples: ORE_ID 1…3 of the hole, ORE_CLS
    ore_id, ore_cls = column(s, "ORE_ID"), column(s, "ORE_CLS")
    for i, row in enumerate(s["rows"]):
        depth = row["from"]
        want = 1 if 3 <= depth < 11 else 2 if 17 <= depth < 22 else 3 if 28 <= depth < 33 else None
        assert ore_id[i] == want, (row["hole"], depth)
        assert (ore_cls[i] in ("ore", "waste_in")) == (want is not None), (row["hole"], depth, ore_cls[i])
        assert (ore_cls[i] == "waste_in") == (want is not None and GRADES[int(depth)] < 1.0)
    assert ore_cls[column(s, "AU").index(0.15)] == "waste" and "interlayer" in ore_cls

    rec = next(x for x in c.get(f"{C}/results?set_id={set_id}").json()["results"] if x["op"] == "ore")
    assert rec["params"] == req and rec["table_id"] == res["table_id"] and rec["stale"] is False
    assert rec["written"] == [{"table": rec["inputs"]["tables"][0]["key"], "columns": ["ORE_ID", "ORE_CLS"]}]
    summary = {x["cls"]: x for x in rec["report"]["summary"]["classes"]}
    assert summary["ore"]["count"] == 9 and summary["ore"]["m"] == 54.0  # 3 × (8 + 5 + 5) m
    assert abs(summary["ore"]["mc"] - 3 * 43.0) < 1e-9 and summary["frame"]["nested"] is True
    assert rec["report"]["kernel"]["mc_min"] == 3.0 and rec["report"]["edits"] == []

    shown = next(x for x in charts(c, set_id) if x["id"] == res["chart_id"])  # the display «Рудные интервалы»
    assert shown["name"] == "Рудные интервалы" and shown["kind"] == "striplog"
    track = shown["settings"]["tracks"][0]
    assert track["table"] == tid and track["field"] == "CLASS" and track["hidden"] == ["frame"]
    assert track["catColors"]["ore"] == "#f46a1a" and track["label"]["template"] == "{LABEL}"
    assert shown["settings"]["allHoles"] is True

    r = c.post(f"{C}/results/{rec['id']}/rebuild", json={})  # the same data: no computation
    assert r.status_code == 200, r.text
    again = wait_job(c, r.json()["source"], r.json()["id"], timeout=240)
    assert again["status"] == "done" and again["result"]["fresh"] is True, again
    assert again["result"]["chart_id"] == res["chart_id"]


def test_repeat_overwrites_the_columns_and_the_display_follows(gkz) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, holes = gkz
    one = ore_here(project, {"body": body(set_id, table)})
    with dialect.read_tx(project.engine) as conn:
        ids = {r.name: r.id for r in conn.execute(sa.select(dh_columns.c.name, dh_columns.c.id).where(
            dh_columns.c.table_id == int(table)))}  # fmt: skip
    r = c.post(f"{C}/ore", json=body(set_id, table))  # the name is taken: «Заменить?»
    assert r.status_code == 409 and r.json()["code"] == "table_exists", r.text
    assert r.json()["suggested"] == "Рудные интервалы (2)"
    two = ore_here(project, {"body": body(set_id, table, mode="strict", replace=True)})
    assert two["replaced"] == [one["table_id"]] and two["chart_id"] == one["chart_id"]
    with dialect.read_tx(project.engine) as conn:
        now = {r.name: r.id for r in conn.execute(sa.select(dh_columns.c.name, dh_columns.c.id).where(
            dh_columns.c.table_id == int(table)))}  # fmt: skip
        n_ore = conn.execute(sa.select(sa.func.count()).where(dh_results.c.op == "ore")).scalar()
    assert now == ids and n_ore == 1  # the same columns, overwritten; one record
    s = rows_of(c, set_id, table)
    ore_id = column(s, "ORE_ID")
    for i, row in enumerate(s["rows"]):  # «строгий»: ore 3–14 m, 17–22, 28–33
        depth = row["from"]
        assert ore_id[i] == (1 if 3 <= depth < 14 else 2 if 17 <= depth < 22 else 3 if 28 <= depth < 33 else None)
    shown = [x for x in charts(c, set_id) if x["name"].startswith("Рудные интервалы")]
    assert len(shown) == 1 and shown[0]["settings"]["tracks"][0]["table"] == str(two["table_id"])
    other = ore_here(project, {"body": body(set_id, table, name="Рудные · вариант")})  # another name: its display
    assert other["chart_id"] != one["chart_id"] and other["name"] == "Рудные · вариант"


def test_cancel_leaves_nothing(gkz) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, _holes = gkz
    before = sorted(tables(c, set_id))
    seen = [0]
    done = ore_here(project, {"body": body(set_id, table)}, seen=seen)
    assert c.delete(f"{C}/results/{done['result_id']}").status_code == 200
    with dialect.write_tx(project.engine) as conn:  # the display of the deleted result goes too (here: by hand)
        conn.execute(sa.delete(objects).where(objects.c.id == done["chart_id"]))
    assert seen[0] >= 4
    for at in range(1, seen[0] + 1):
        with pytest.raises(JobCancelled):
            ore_here(project, {"body": body(set_id, table)}, cancel_at=at)
        with dialect.read_tx(project.engine) as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(dh_results)).scalar() == 0
            assert conn.execute(sa.select(sa.func.count()).where(dh_tables.c.state == "building")).scalar() == 0
            live = conn.execute(sa.select(dh_columns.c.name).where(
                dh_columns.c.table_id == int(table), dh_columns.c.deleted.is_(False))).scalars().all()  # fmt: skip
        assert "ORE_ID" not in live and "ORE_CLS" not in live
        assert sorted(tables(c, set_id)) == before and charts(c, set_id) == []


def test_preview_is_the_job_and_writes_nothing_protocol_and_stale(gkz) -> None:  # type: ignore[no-untyped-def]
    from bf_core.composites import results

    c, project, set_id, table, holes = gkz
    hole_id = c.get(f"{API}/drillholes/sets/{set_id}/tables/collar?limit=10").json()["rows"]
    hole_id = next(h["hole_id"] for h in hole_id if h["hole"] == holes[1])
    req = body(set_id, table)
    r = c.post(f"{C}/ore/preview", json={"params": req, "hole_id": hole_id})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["hole"] == holes[1] and p["unit"] == "г/т" and p["set_summary"] is not None
    assert len(p["samples"]["from"]) == N and set(p["samples"]["values"]) == {"AU", "ORE_ID", "ORE_CLS"}
    assert p["samples"]["values"]["ORE_CLS"][3] == "ore" and p["samples"]["values"]["ORE_ID"][0] is None
    v = p["result"]["values"]
    assert {"CLASS", "NESTED", "M", "C", "MC", "BASIS", "ORE_ID", "N_SAMPLES", "WASTE_LEN", "FLAG", "LABEL"} <= set(v)
    assert LABEL in v["LABEL"] and "frame" in v["CLASS"]
    ev = p["protocol"]["events"]
    assert ev and all("hole" not in e and 0 <= e["row0"] <= e["row1"] < N for e in ev if e["row0"] >= 0)
    form = p["protocol"]  # the form И1, сл. 17 (12.15): a line per sample, the columns N, IN₀, stages, итог
    assert len(form["rows"]) == N and {"n", "in0", "final"} <= {x["key"] for x in form["columns"]} and form["cells"]
    assert {x["cls"] for x in p["summary"]["classes"]} >= {"ore", "interlayer"}
    assert c.get(f"{C}/results?set_id={set_id}").json()["results"] == []  # nothing written
    assert "ORE_ID" not in cols(rows_of(c, set_id, table))

    done = ore_here(project, {"body": req})
    got = rows_of(c, set_id, str(done["table_id"]))
    k = cols(got)
    mine = [r for r in got["rows"] if r["hole"] == holes[1]]
    assert v["CLASS"] == [r[k["CLASS"]] for r in mine] and p["result"]["from"] == [r["from"] for r in mine]
    assert v["LABEL"] == [r[k["LABEL"]] for r in mine] and p["set_summary"]["rows"] == len(got["rows"])
    q = c.get(f"{C}/results/{done['result_id']}/protocol", params={"hole": holes[1]})
    assert q.status_code == 200, q.text
    assert q.json()["result"] == p["result"] and q.json()["protocol"] == p["protocol"]
    assert c.get(f"{C}/results/{done['result_id']}/protocol", params={"hole": "нет такой"}).status_code == 404
    with dialect.write_tx(project.engine) as conn:  # the samples edited: the result is out of date
        results.bump_table(conn, set_id, int(table))
    q = c.get(f"{C}/results/{done['result_id']}/protocol", params={"hole": holes[1]})
    assert q.status_code == 409 and q.json()["code"] == "stale", q.text


def test_contacts_with_the_body_codes(gkz) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, _table, holes = gkz
    table, _h = add_samples(project, set_id, domain=True, name="Пробы ГКЗ · домен")  # DOM 1 at 3–12 m
    hole_id = next(h["hole_id"] for h in c.get(f"{API}/drillholes/sets/{set_id}/tables/collar?limit=10").json()["rows"]
                   if h["hole"] == holes[0])  # fmt: skip
    req = body(set_id, table, mode="contacts", domain={"table": table, "field": "DOM"}, contacts_values=[1])
    p = c.post(f"{C}/ore/preview", json={"params": req, "hole_id": hole_id}).json()
    v = p["result"]["values"]
    at = [i for i, x in enumerate(v["CLASS"]) if x == "ore"]
    assert len(at) == 1 and (p["result"]["from"][at[0]], p["result"]["to"][at[0]]) == (3.0, 12.0)
    assert v["BASIS"][at[0]] == "mc" and v["DOMAIN"][at[0]] == 1.0 and "frame" not in v["CLASS"]
    none = c.post(f"{C}/ore/preview", json={"params": {**req, "contacts_values": [7]}, "hole_id": hole_id}).json()
    assert "ore" not in none["result"]["values"]["CLASS"] and any("7" in n for n in none["notes"])


def test_refusals_say_why(gkz) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, _holes = gkz
    cases = [
        ({"cutoff": None}, 422, "ore_bad_param", "cutoff"),
        ({"m_waste_max": -1}, 422, "ore_bad_param", "m_waste_max"),
        ({"mode": "nope"}, 422, "ore_bad_param", "mode"),
        ({"mode": "contacts"}, 422, "ore_no_domain", "domain"),
        ({"field": "НЕТ"}, 422, "field_not_found", None),
        ({"thickness": {"source": "field"}}, 422, "bad_param", "thickness"),
        ({"contacts_values": "1, x"}, 422, "bad_param", "contacts_values"),
        ({"name": "Пробы ГКЗ"}, 422, "name_is_source", "name"),
    ]
    for over, status, code, param in cases:
        r = c.post(f"{C}/ore", json=body(set_id, table, **over))
        assert r.status_code == status and r.json()["code"] == code, (over, r.text)
        assert r.json()["message"] and r.json()["hint"]
        if param:
            assert r.json()["param"] == param, (over, r.text)
    with dialect.write_tx(project.engine) as conn:  # a field ORE_ID of the user is never overwritten
        tid = conn.execute(sa.select(dh_tables.c.id).where(dh_tables.c.name == "Пробы ГКЗ")).scalar()
        conn.execute(sa.insert(dh_columns).values(table_id=tid, name="ORE_ID", kind="number", slot=9, role="value",
                                                  deleted=False, position=9))  # fmt: skip
    r = c.post(f"{C}/ore", json=body(set_id, table))
    assert r.status_code == 409 and r.json()["code"] == "column_exists", r.text
    r = c.post(f"{C}/ore/preview", json={"params": body(set_id, table), "hole_id": 10**9})
    assert r.status_code == 404 and r.json()["code"] == "hole_not_found"


def test_algo_is_that_of_the_kernel() -> None:
    from bf_core.composites.ore_intervals import ALGO

    assert oj.ALGO == f"ore-{ALGO}"  # a change of the rules makes the results «устарел» and closes their protocol
