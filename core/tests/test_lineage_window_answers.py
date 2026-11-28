"""The answers of the core the interface of the graph of dependencies is read against (подшаг 18.Г3; ADR 0025 §5, §7–10;
Д-35): the routes `/lineage/status`, `impact`, `origin`, `pin`, `rebuild` through the real app, over a chain of stub
objects (the states, the influence, the origin, the pin, the plan and the run of «Перестроить с зависимыми», the
refusals) and over real results (a result of composites and its table → a model of variography).

The file `tests/data/lineage/answers.json` is read by vitest (`ui/test/lineage/*.test.ts`).
Regenerate: `BF_REGEN_LINEAGE=1 uv run pytest -q tests/test_lineage_window_answers.py`. What changes from run to run
(the project id of a job, the times) is not stored; the floats are compared to 9 digits."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core import lineage
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.lineage.store import Node
from tests.golden import assert_same
from tests.lineage_stub_jobs import NODE
from tests.test_blocks_fields_window_answers import answer, masked
from tests.test_import_job import API, wait_job
from tests.test_lineage_api import (  # noqa: F401
    Chain,
    L,
    add,
    chain,
    real_modules,
    stubs,
    until,
)

DATA = Path(__file__).parent / "data" / "lineage" / "answers.json"
REGEN = os.environ.get("BF_REGEN_LINEAGE") == "1"
JOB_KEYS = ("kind", "title", "queue", "status", "message", "error", "result")


def post(c: Any, path: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"request": body, **answer(c.post(f"{L}/{path}", json=body))}


def get(c: Any, path: str, **params: Any) -> dict[str, Any]:
    return {"request": params, **answer(c.get(f"{L}/{path}", params=params))}


def status(c: Any, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return post(c, "status", {"nodes": nodes})


def started(r: Any, request: dict[str, Any]) -> dict[str, Any]:
    """An answer that starts a job: the refusal as it is, or the 202 without what varies (the id of the project)."""
    if r.status_code != 202:
        return {"request": request, **answer(r)}
    body = r.json()
    assert body["job_id"] == body["id"] and isinstance(body["source"], str)
    return {"request": request, "status": 202, "body": {**body, "source": "<project>", "job_id": 0, "id": 0}}


def follow(c: Any, r: Any) -> dict[str, Any]:
    body = r.json()
    job = wait_job(c, body["source"], body["id"], timeout=300)
    return {k: job.get(k) for k in JOB_KEYS}


def stub_scenario(ch: Chain) -> dict[str, Any]:
    c = ch.c
    with dialect.write_tx(ch.engine) as conn:  # a report of a kind without a maker, built from the node 3
        report = add(conn, "Отчёт", 0.0, kind="report_stub")
        lineage.record(conn, Node("object:report_stub", report), [Node(NODE, ch.n3)])
    ids = {"s0": ch.s0, "n1": ch.n1, "n2": ch.n2, "m": ch.m, "n3": ch.n3, "n4": ch.n4,
           "report": report}  # fmt: skip
    every = [ch.node(i) for i in (ch.s0, ch.n1, ch.n2, ch.m, ch.n3, ch.n4)]
    every.append({"kind": "object:report_stub", "id": report})
    out: dict[str, Any] = {"ids": ids, "nodes": every}
    out["status_current"] = status(c, every)
    out["status_deleted"] = status(c, [{"kind": NODE, "id": 987_654}, ch.node(ch.s0)])
    out["status_bad_kind"] = status(c, [{"kind": "table", "id": 1}])
    out["status_bad_body"] = post(c, "status", {"nodes": "x"})
    out["impact_n1"] = get(c, "impact", kind=NODE, id=ch.n1)
    out["impact_s0"] = get(c, "impact", kind=NODE, id=ch.s0)
    out["impact_leaf"] = get(c, "impact", kind=NODE, id=ch.n4)
    out["impact_missing"] = get(c, "impact", kind=NODE, id=424_242)
    out["origin_n2_current"] = get(c, "origin", kind=NODE, id=ch.n2)
    out["origin_s0"] = get(c, "origin", kind=NODE, id=ch.s0)
    out["origin_missing"] = get(c, "origin", kind=NODE, id=424_242)

    ch.edit_source(5.0)
    out["status_stale"] = status(c, every)
    out["origin_n2_stale"] = get(c, "origin", kind=NODE, id=ch.n2)
    out["pin_n1"] = post(c, "pin", {"node": ch.node(ch.n1), "pinned": True})
    out["status_pinned"] = status(c, every)
    out["rebuild_pinned"] = {"request": {"node": ch.node(ch.n1)},
                             **answer(ch.start())}  # fmt: skip
    out["unpin_n1"] = post(c, "pin", {"node": ch.node(ch.n1), "pinned": False})
    out["pin_missing"] = post(c, "pin", {"node": {"kind": NODE, "id": 424_242}, "pinned": True})
    out["rebuild_missing"] = post(c, "rebuild", {"node": {"kind": NODE, "id": 424_242}})
    out["rebuild_unsupported"] = post(c, "rebuild", {"node": {"kind": "object:report_stub", "id": report}})
    out["dry_run"] = post(c, "rebuild", {"node": ch.node(ch.n1), "dry_run": True})
    out["dry_run_alone"] = post(c, "rebuild", {"node": ch.node(ch.n1), "dry_run": True, "with_dependents": False})

    ch.mark(ch.n2, hold=True)  # the job of the node 2 waits: the node is «считается»... only for the job
    r = ch.start()
    out["rebuild_started"] = started(r, {"node": ch.node(ch.n1)})
    until(lambda: ch.meta(ch.n2).get("started") is True, 60)
    out["status_while_running"] = status(c, every)
    cancel = c.post(f"{API}/jobs/{r.json()['source']}/{r.json()['id']}/cancel")
    assert cancel.status_code == 200, cancel.text
    out["job_cancelled"] = follow(c, r)
    out["status_after_cancel"] = status(c, every)
    ch.mark(ch.n2, hold=False)
    r = ch.start()
    out["rebuild_run"] = started(r, {"node": ch.node(ch.n1)})
    out["job_done"] = follow(c, r)
    out["status_after_rebuild"] = status(c, every)

    with dialect.write_tx(ch.engine) as conn:  # the source is deleted
        conn.execute(sa.delete(objects).where(objects.c.id == ch.s0))
    out["status_source_lost"] = status(c, every)
    out["origin_n1_lost"] = get(c, "origin", kind=NODE, id=ch.n1)
    out["rebuild_dry_lost"] = post(c, "rebuild", {"node": ch.node(ch.n2), "dry_run": True})

    from bf_core.projects.service import projects_of

    project = projects_of(c.core).require_current()
    project.read_only = True
    try:
        out["pin_read_only"] = post(c, "pin", {"node": ch.node(ch.n1), "pinned": True})
        out["rebuild_read_only"] = post(c, "rebuild", {"node": ch.node(ch.n1)})
    finally:
        project.read_only = False
    return out


def real_scenario(c: Any) -> dict[str, Any]:
    """The samples → composites (a result and its table) → a model of variography, as in `test_lineage_api`."""
    from bf_core.geostat.variogram import jobs as vj
    from bf_core.projects.service import projects_of
    from tests.test_composites_jobs import rows_of, tables
    from tests.test_vario_api import GS, MODEL, fill

    assert c.post(API + "/projects", json={"name": "Цепочка"}).status_code == 200
    project = projects_of(c.core).require_current()
    set_id = fill(project.engine)
    src = tables(c, set_id)["Пробы"]["table"]
    run = {"set_id": set_id, "table": src, "fields": ["AU"], "method": "length", "length": 2}
    r = c.post(f"{API}/composites/run", json=run)
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=300)
    assert job["status"] == "done", job
    result_id = job["result"]["result_id"]
    exp = {"set_id": set_id, "table": "Композиты 2 м", "field": "AU", "axes": {"ang1": 0, "ang2": -90, "ang3": 0},
           "nlag": 8, "min_pairs": 5}  # fmt: skip
    out = vj.run_experimental(project.engine, project.folder, {"body": exp})
    r = c.post(GS + "/models", json={"model": MODEL, "exp_key": out["key"], "name": "Модель AU"})
    assert r.status_code == 200, r.text
    model_id = r.json()["id"]
    comp = int(tables(c, set_id)["Композиты 2 м"]["table"])
    nodes = [{"kind": "dh_result", "id": result_id}, {"kind": "dh_table", "id": comp},
             {"kind": "object:variogram_model", "id": model_id}, {"kind": "dh_table", "id": int(src)}]  # fmt: skip
    res: dict[str, Any] = {"ids": {"result": result_id, "composites": comp, "model": model_id, "samples": int(src)},
                           "nodes": nodes}  # fmt: skip
    res["status_current"] = status(c, nodes)
    res["impact_samples"] = get(c, "impact", kind="dh_table", id=int(src))
    res["origin_model"] = get(c, "origin", kind="object:variogram_model", id=model_id)
    rows = rows_of(c, set_id, src)  # an edit of one value of the samples
    au = next(col["key"] for col in rows["columns"] if col["name"] == "AU")
    r = c.patch(f"{API}/drillholes/sets/{set_id}/tables/{src}/rows/{rows['rows'][0]['id']}",
                json={"column": au, "value": 99.5})  # fmt: skip
    assert r.status_code == 200, r.text
    res["status_stale"] = status(c, nodes)
    res["origin_model_stale"] = get(c, "origin", kind="object:variogram_model", id=model_id)
    res["dry_run"] = post(c, "rebuild", {"node": {"kind": "dh_result", "id": result_id}, "dry_run": True})
    return res


def untimed(x: Any) -> Any:
    """The token of a table holds the time of its change (`66114c790e7dc:1`): the revision stays, the token does not."""
    if isinstance(x, dict):
        return {k: untimed(v) for k, v in x.items()}
    if isinstance(x, list):
        return [untimed(v) for v in x]
    if isinstance(x, str) and re.fullmatch(r"[0-9a-f]{10,}:\d+", x):
        return "<token>:" + x.split(":")[1]
    return x


def build(ch: Chain) -> dict[str, Any]:
    return {"stub": stub_scenario(ch),
            "rules": "ответы настоящего ядра через маршруты /lineage (test_lineage_window_answers.py)"}  # fmt: skip


def test_stub_chain_answers_are_what_the_core_answers(chain: Chain) -> None:  # noqa: F811
    built = json.loads(json.dumps(build(chain), ensure_ascii=False))
    _check(built, "stub")


def test_real_chain_answers_are_what_the_core_answers(real_modules, pclient) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    built = json.loads(json.dumps({"real": untimed(real_scenario(pclient))}, ensure_ascii=False))
    _check(built, "real")


def _check(built: dict[str, Any], part: str) -> None:
    """Writes (BF_REGEN_LINEAGE=1) or compares one part of the file; the other part stays as it is."""
    have = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        have = {**have, part: built[part], "rules": built.get("rules", have.get("rules"))}
        DATA.write_text(json.dumps(have, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    a = json.loads(DATA.read_text(encoding="utf-8"))
    assert_same(masked(a[part]), masked(built[part]), f"({part})")
    check_shapes(a[part], part)


def check_shapes(a: dict[str, Any], part: str) -> None:
    """What the interface relies on: the form of the answers (the states, the order, the fields)."""
    states = {"current", "stale", "source_lost", "pinned", "building", "deleted"}
    keys = ("changed", "id", "kind", "name", "reason", "state", "sub")
    for name, case in a.items():
        if not name.startswith("status_") or case["status"] != 200:
            continue
        items = case["body"]["items"]
        assert [i["id"] for i in items] == [n["id"] for n in case["request"]["nodes"]], name  # the order asked
        assert all(sorted(i) == list(keys) and i["state"] in states for i in items), name
    if part == "stub":
        assert [i["state"] for i in a["status_current"]["body"]["items"]] == ["current"] * 7
        assert {i["state"] for i in a["status_stale"]["body"]["items"][1:]} == {"stale"}
        assert a["job_done"]["status"] == "done" and a["job_cancelled"]["status"] == "cancelled"
        assert a["rebuild_run"]["status"] == 202 and a["rebuild_pinned"]["body"]["code"] == "lineage_node_pinned"
        assert a["status_source_lost"]["body"]["items"][0]["state"] == "deleted"
        assert "source_lost" in {i["state"] for i in a["status_source_lost"]["body"]["items"]}
    else:
        assert [i["state"] for i in a["status_current"]["body"]["items"]] == ["current", "current", "current",
                                                                               "current"]  # fmt: skip
        assert [i["state"] for i in a["status_stale"]["body"]["items"]][:3] == ["stale"] * 3
