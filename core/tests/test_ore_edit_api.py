"""Manual edit of ore intervals through the API (ТЗ 7.35 Д-26 «Место»; ADR 0018 §12, решения 19, 24; подшаг 12.23):
`POST /composites/results/{id}/edit` writes the hole in one transaction — its rows of «Рудные интервалы» (MANUAL),
ORE_ID / ORE_CLS of its samples, `report.edits` — past the read-only check of result tables; the result stays
fresh, its protocol shows the edit; a refused edit writes nothing; «Перестроить» loses the edits."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_results, dh_tables
from tests.test_composites_jobs import rows_of
from tests.test_import_job import API, wait_job
from tests.test_ore_job import C, body, cols, gkz, ore_here  # noqa: F401 - the fixture

SPLIT = {"action": "split", "from": 3.0, "to": 7.0}


def hole_rows(got: dict[str, Any], hole: str) -> list[dict[str, Any]]:
    k = cols(got)
    return [{"from": r["from"], "to": r["to"], **{n: r[k[n]] for n in ("CLASS", "NESTED", "MANUAL", "ORE_ID", "M")}}
            for r in got["rows"] if r["hole"] == hole]  # fmt: skip


def answer_rows(a: dict[str, Any]) -> list[dict[str, Any]]:
    v = a["values"]
    return [{"from": f, "to": t, **{n: v[n][i] for n in ("CLASS", "NESTED", "MANUAL", "ORE_ID", "M")}}
            for i, (f, t) in enumerate(zip(a["from"], a["to"], strict=True))]  # fmt: skip


def record(c: Any, set_id: int) -> dict[str, Any]:
    return next(x for x in c.get(f"{C}/results?set_id={set_id}").json()["results"] if x["op"] == "ore")


def test_split_writes_the_hole_and_stays_fresh(gkz) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, holes = gkz
    done = ore_here(project, {"body": body(set_id, table)})
    rid, tid = done["result_id"], str(done["table_id"])
    before = rows_of(c, set_id, tid)
    with dialect.read_tx(project.engine) as conn:
        count0 = conn.execute(sa.select(dh_tables.c.row_count).where(dh_tables.c.id == int(tid))).scalar()

    r = c.post(f"{C}/results/{rid}/edit", json={"hole": holes[1], **SPLIT})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["hole"] == holes[1] and len(a["report"]["edits"]) == 1
    assert a["report"]["edits"][0] == {"hole": holes[1], **SPLIT}
    rows = answer_rows(a["rows"])
    top = [x for x in rows if x["NESTED"] == 0 and x["CLASS"] in ("ore", "subgrade")]
    assert [(x["from"], x["to"]) for x in top][:2] == [(3.0, 7.0), (7.0, 11.0)]
    assert all(x["MANUAL"] == 1 for x in top[:2]) and top[2]["MANUAL"] == 0  # 17–22 m is the calculation's
    assert {"LABEL", "MODE", "AU", "CU", "DOMAIN"} <= set(a["rows"]["values"]) and "X" not in a["rows"]["values"]
    assert isinstance(a["violations"], list)

    after = rows_of(c, set_id, tid)  # the rows of the hole replaced, the other holes as they were
    assert hole_rows(after, holes[1]) == rows
    for h in (holes[0], holes[2]):
        assert hole_rows(after, h) == hole_rows(before, h)
    k = cols(after)
    assert all(r[k["X"]] is not None and r[k["MODE"]] == "strict_excl" for r in after["rows"] if r["hole"] == holes[1])
    with dialect.read_tx(project.engine) as conn:
        count1 = conn.execute(sa.select(dh_tables.c.row_count).where(dh_tables.c.id == int(tid))).scalar()
    assert count1 == count0 + len(rows) - len(hole_rows(before, holes[1])) == len(after["rows"])

    s = rows_of(c, set_id, table)  # ORE_ID / ORE_CLS of the samples of the hole: the two intervals 1 and 2
    ks = cols(s)
    for row in s["rows"]:
        d = row["from"]
        if row["hole"] == holes[1]:
            want = 1 if 3 <= d < 7 else 2 if 7 <= d < 11 else 3 if 17 <= d < 22 else 4 if 28 <= d < 33 else None
        else:
            want = 1 if 3 <= d < 11 else 2 if 17 <= d < 22 else 3 if 28 <= d < 33 else None
        assert row[ks["ORE_ID"]] == want, (row["hole"], d)

    rec = record(c, set_id)  # fresh: the protocol opens and shows the edit
    assert rec["stale"] is False and rec["report"]["edits"] == a["report"]["edits"]
    ore = {x["cls"]: x for x in rec["report"]["summary"]["classes"]}["ore"]
    assert ore["count"] == 10 and abs(ore["m"] - 54.0) < 1e-9
    q = c.get(f"{C}/results/{rid}/protocol", params={"hole": holes[1]})
    assert q.status_code == 200, q.text
    p = q.json()
    assert p["result"] == a["rows"] and p["protocol"]["edits"] == [SPLIT]
    assert any(e["step"] == 6 or "ручная" in e.get("text", "") for e in p["protocol"]["events"])
    assert p["samples"]["values"]["ORE_ID"][3:11] == [1, 1, 1, 1, 2, 2, 2, 2]

    r = c.post(f"{C}/results/{rid}/edit", json={"hole": holes[1], "action": "merge", "from": 3.0, "to": 11.0})
    assert r.status_code == 200, r.text  # an edit on top of the stored one
    assert len(r.json()["report"]["edits"]) == 2
    top = [x for x in answer_rows(r.json()["rows"]) if x["NESTED"] == 0 and x["CLASS"] == "ore"]
    assert (top[0]["from"], top[0]["to"]) == (3.0, 11.0)


def test_refused_edit_writes_nothing(gkz) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, holes = gkz
    done = ore_here(project, {"body": body(set_id, table)})
    rid, tid = done["result_id"], str(done["table_id"])
    before, samples = rows_of(c, set_id, tid), rows_of(c, set_id, table)
    cases = [
        ({"hole": holes[0], "action": "split", "from": 3.0, "to": 5.0}, 422, "ore_edit_refused"),  # waste at 4–5 m
        ({"hole": holes[0], "action": "turn", "from": 3.0, "to": 5.0}, 422, "ore_bad_param"),
        ({"hole": "нет такой", **SPLIT}, 404, "hole_not_found"),
    ]
    for req, status, code in cases:
        r = c.post(f"{C}/results/{rid}/edit", json=req)
        assert r.status_code == status and r.json()["code"] == code, (req, r.text)
        assert r.json()["message"] and r.json()["hint"]
    assert rows_of(c, set_id, tid)["rows"] == before["rows"] and rows_of(c, set_id, table)["rows"] == samples["rows"]
    assert record(c, set_id)["report"]["edits"] == []
    with dialect.write_tx(project.engine) as conn:  # the samples edited: the result is out of date, no edit
        from bf_core.composites import results

        results.bump_table(conn, set_id, int(table))
    r = c.post(f"{C}/results/{rid}/edit", json={"hole": holes[0], **SPLIT})
    assert r.status_code == 409 and r.json()["code"] == "stale", r.text


def test_rebuild_loses_the_edits(gkz) -> None:  # type: ignore[no-untyped-def]
    c, project, set_id, table, holes = gkz
    done = ore_here(project, {"body": body(set_id, table)})
    rid = done["result_id"]
    assert c.post(f"{C}/results/{rid}/edit", json={"hole": holes[2], **SPLIT}).status_code == 200
    r = c.post(f"{C}/results/{rid}/rebuild", json={})
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=240)
    assert job["status"] == "done" and job["result"]["fresh"] is False, job  # computed again, not «the same hash»
    rec = record(c, set_id)
    assert rec["report"]["edits"] == [] and rec["stale"] is False
    got = rows_of(c, set_id, str(rec["table_id"]))
    assert all(r[cols(got)["MANUAL"]] == 0 for r in got["rows"])
    with dialect.read_tx(project.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).where(dh_results.c.op == "ore")).scalar() == 1


def test_contacts_have_no_edit(gkz) -> None:  # type: ignore[no-untyped-def]
    from tests.test_ore_job import add_samples

    c, project, set_id, _table, holes = gkz
    table, _h = add_samples(project, set_id, domain=True, name="Пробы ГКЗ · домен")
    req = body(set_id, table, mode="contacts", domain={"table": table, "field": "DOM"}, contacts_values=[1])
    done = ore_here(project, {"body": req})
    r = c.post(f"{C}/results/{done['result_id']}/edit", json={"hole": holes[0], **SPLIT})
    assert r.status_code == 422 and r.json()["code"] == "ore_edit_refused", r.text
    assert "контакт" in r.json()["message"]
    assert c.get(f"{API}/drillholes/sets/{set_id}/tables").status_code == 200
