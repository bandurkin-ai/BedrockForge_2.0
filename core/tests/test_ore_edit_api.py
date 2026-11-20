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


def test_split_writes_the_hole_and_stays_fresh(gkz) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
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


def test_refused_edit_writes_nothing(gkz) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
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


def test_rebuild_loses_the_edits(gkz) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
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


def test_contacts_have_no_edit(gkz) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from tests.test_ore_job import add_samples

    c, project, set_id, _table, holes = gkz
    table, _h = add_samples(project, set_id, domain=True, name="Пробы ГКЗ · домен")
    req = body(set_id, table, mode="contacts", domain={"table": table, "field": "DOM"}, contacts_values=[1])
    done = ore_here(project, {"body": req})
    r = c.post(f"{C}/results/{done['result_id']}/edit", json={"hole": holes[0], **SPLIT})
    assert r.status_code == 422 and r.json()["code"] == "ore_edit_refused", r.text
    assert "контакт" in r.json()["message"]
    assert c.get(f"{API}/drillholes/sets/{set_id}/tables").status_code == 200


# --- the review of 12.33, part 1 (подшаг 12.34) ---------------------------------------------------------------


def _samples(c: Any, set_id: int, table: str) -> list[tuple[Any, ...]]:
    s = rows_of(c, set_id, table)
    k = cols(s)
    return [(r["hole"], r["from"], r[k["ORE_ID"]], r[k["ORE_CLS"]]) for r in s["rows"]]


def _two(c: Any, project: Any, set_id: int, table: str) -> tuple[dict[str, Any], dict[str, Any], list[Any]]:
    """Two ore results on one table of samples: the second («строгий») wrote ORE_ID / ORE_CLS last."""
    one = ore_here(project, {"body": body(set_id, table)})
    first = _samples(c, set_id, table)
    two = ore_here(project, {"body": body(set_id, table, name="Рудные · строгий", mode="strict")})
    now = _samples(c, set_id, table)
    assert now != first  # the fields hold the values of the second one
    return one, two, now


def test_two_ore_results_delete_the_first(gkz) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    """№ 3: deleting the earlier result keeps ORE_ID / ORE_CLS of the later one (it wrote them last); deleting the
    later one hides them."""
    c, project, set_id, table, _holes = gkz
    one, two, now = _two(c, project, set_id, table)
    r = c.delete(f"{C}/results/{one['result_id']}")
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == []
    assert _samples(c, set_id, table) == now
    rec = next(x for x in c.get(f"{C}/results?set_id={set_id}").json()["results"] if x["op"] == "ore")
    assert rec["id"] == two["result_id"] and rec["stale"] is False
    assert c.delete(f"{C}/results/{two['result_id']}").status_code == 200
    assert not {"ORE_ID", "ORE_CLS"} & set(cols(rows_of(c, set_id, table)))


def test_two_ore_results_edit_the_first(gkz) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    """№ 3: an edit of the earlier result writes its own table, not ORE_ID / ORE_CLS of the later one."""
    c, project, set_id, table, holes = gkz
    one, two, now = _two(c, project, set_id, table)
    before = rows_of(c, set_id, str(one["table_id"]))
    r = c.post(f"{C}/results/{one['result_id']}/edit", json={"hole": holes[1], **SPLIT})
    assert r.status_code == 200, r.text
    assert rows_of(c, set_id, str(one["table_id"]))["rows"] != before["rows"]  # the edit is in its table
    assert _samples(c, set_id, table) == now
    assert all(e[0] != "drillholes.changed" or e[1]["table"] != table for e in r.json().get("events", []))
    recs = {x["id"]: x for x in c.get(f"{C}/results?set_id={set_id}").json()["results"]}
    assert recs[one["result_id"]]["stale"] is False and recs[two["result_id"]]["stale"] is False
    r = c.post(f"{C}/results/{two['result_id']}/edit", json={"hole": holes[1], **SPLIT})  # the later one writes
    assert r.status_code == 200, r.text
    assert _samples(c, set_id, table) != now


def test_user_columns_survive_on_unchanged_rows(gkz) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    """№ 10: a field the user added to the result table keeps its values on the rows of the edited hole that the
    edit did not change; the changed rows lose them — said in `warnings`."""
    from bf_core.composites import results
    from bf_core.db.schema import dh_columns, dh_rows
    from bf_core.drillholes.snapshot import snapshots
    from bf_core.drillholes.store import pack, unpack

    c, project, set_id, table, holes = gkz
    done = ore_here(project, {"body": body(set_id, table)})
    rid, tid = done["result_id"], int(done["table_id"])
    with dialect.write_tx(project.engine) as conn:
        slots = [int(s) for s in conn.execute(sa.select(dh_columns.c.slot).where(dh_columns.c.table_id == tid,
                                                                                  dh_columns.c.kind == "number")).scalars()]
        slot = max(slots) + 1
        conn.execute(sa.insert(dh_columns).values(table_id=tid, name="ПРИМ", kind="number", slot=slot, role="value",
                                                  deleted=False, position=999))  # fmt: skip
        for r in conn.execute(sa.select(dh_rows.c.id, dh_rows.c.num).where(dh_rows.c.table_id == tid)).all():
            nums = unpack(r.num, slot + 1)
            nums[slot] = float(r.id)
            conn.execute(sa.update(dh_rows).where(dh_rows.c.id == r.id).values(num=pack(list(nums))))
        results.bump_table(conn, set_id, tid)
    snapshots.forget(set_id)
    old = rows_of(c, set_id, str(tid))
    k = cols(old)
    mark = {(r["hole"], r["from"], r["to"]): r[k["ПРИМ"]] for r in old["rows"]}
    assert all(v is not None for v in mark.values())
    r = c.post(f"{C}/results/{rid}/edit", json={"hole": holes[1], **SPLIT})
    assert r.status_code == 200, r.text
    new = rows_of(c, set_id, str(tid))
    kept = lost = 0
    for row in new["rows"]:
        span = (row["hole"], row["from"], row["to"])
        if span in mark:
            assert row[k["ПРИМ"]] == mark[span], span
            kept += row["hole"] == holes[1]
        else:
            assert row["hole"] == holes[1] and row[k["ПРИМ"]] is None, span
            lost += 1
    assert kept >= 2 and lost >= 2
    warnings = r.json()["warnings"]
    assert len(warnings) == 1 and "ПРИМ" in warnings[0]
