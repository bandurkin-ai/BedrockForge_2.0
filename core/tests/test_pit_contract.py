"""The answers of the core the windows of the pit optimisation are read against (подшаг 21.11; e21-api.md §2–§13): the
real routes on a synthetic model of cubes with an ore body (`tests/pitworld.py`), the jobs followed to their end — one
file, `core/tests/data/pit/contract_answers.json`, the same keys as the samples of the contract (`{status, body}`),
read by vitest (`ui/test/pit/*`).

Regenerate: `BF_REGEN_PIT=1 uv run pytest -q tests/test_pit_contract.py` — the keys of this test replace the
samples, the others stay as they are: `defaults`, `validate_new`, `validate_ready`, `bad_param`, `pit_not_ready`,
`refusals` are written by `spec.py` (`test_pit_spec.py`); `surfaces_result` and `solid_result` are the answers of the
jobs `pit.surfaces` (every shell) and `pit.solid` (the shell 2), real since 21.21б-2. Without the variable the test
checks that the core still answers what the file holds; the times, the fingerprints, the versions of the libraries and
the numbers of the jobs are left out, the floats
are compared to 9 digits.

The ids are those of the samples of the contract (the model 12, the scenario 17 and the second 18; the first check is
the job 1, the second — 2, the run — 3): the world has 11 objects before its model and 4 between the model and the first
scenario (a project of a user has others), so the ids come from the core itself. `check_post` and `run_post` are
written by hand as `source: "project"` (the real source is the uid of the project)."""

# ruff: noqa: F811 - the fixture `pw` is imported, and the tests take it by its name
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.mine import store as st
from tests.pitworld import PIT, finish, make_model, params, pw  # noqa: F401 - the fixture

DATA = Path(__file__).parent / "data" / "pit" / "contract_answers.json"
REGEN = os.environ.get("BF_REGEN_PIT") == "1"
NOTE = (
    "ЧАСТЬ КЛЮЧЕЙ — ответы настоящего ядра (BF_REGEN_PIT=1 uv run pytest -q tests/test_pit_contract.py, 21.11, "
    "модель из кубов 10 м с рудным телом: tests/pitworld.py; идентификаторы — как в образцах: модель 12, сценарии 17 и "
    "18, задания 1–3; surfaces_result, solid_result — задания pit.surfaces (все оболочки) и pit.solid (оболочка 2), "
    "21.21б-2); defaults, validate_new, validate_ready, bad_param, pit_not_ready, refusals пишет spec.py "
    "(BF_REGEN_PIT=1 uv run pytest -q tests/test_pit_spec.py). Интерфейс принимается только на настоящих ответах."
)
#: the keys this test writes, in the order of the file (the new ones follow the old ones)
KEYS = (
    "check_post", "check_result", "scenario_post", "scenarios_list", "scenario_get", "scenario_put",
    "scenario_delete", "run_post", "run_result", "template_get", "report_get", "compare_result", "defaults_model",
    "version_conflict", "name_taken", "pit_running", "pit_no_result", "scenario_not_found", "pit_not_ready_run",
    "surfaces_result", "solid_result",
)  # fmt: skip
SKIP = ("seconds", "created_at", "updated_at", "cells_sha", "versions", "job_id", "at", "stages")
CALENDAR = {"q_ore": 1_000_000.0, "q_mass": None, "rate": 0.1}


def answer(r: Any) -> dict[str, Any]:
    return {"status": r.status_code, "body": r.json()}


def result_of(c: Any, started: Any) -> dict[str, Any]:
    assert started.status_code == 200, started.text
    job = finish(c, started.json(), timeout=300)
    assert job["status"] == "done", job
    return {"status": 200, "body": job["result"]}


def filler(c: Any, n: int, first: int) -> list[int]:
    """`n` small wireframes (the other objects of a project): the ids of the next objects are those of the samples."""
    from bf_core.wireframes import store as wf

    xyz = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    tri = np.array([[0, 1, 2]], dtype=np.int32)
    out = []
    with dialect.write_tx(c.project.engine) as conn:
        for i in range(n):
            out.append(wf.put_wireframe(conn, c.store, xyz + [i, 0.0, 0.0], tri, name=f"Каркас {first + i}"))
    return out


def build(c: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    filler(c, 11, 1)
    mid = make_model(c, name="Рудное тело")
    assert mid == 12, mid
    filler(c, 4, 12)
    out["defaults_model"] = answer(c.get(f"{PIT}/defaults", params={"model_id": mid}))
    rf = [0.8, 1.0, 1.2]
    body = params(mid, rf=rf, calendar=CALENDAR)
    warm = c.post(f"{PIT}/check", json={"params": {"model_id": mid, "slopes": {"table": [{"az": 0, "angle": 50}]}}})
    result_of(c, warm)  # the first check is the job 1: the next one is the job 2, the run — 3
    started = c.post(f"{PIT}/check", json={"params": {"model_id": mid}})
    out["check_post"] = {"status": 200, "body": {"source": "project", "id": 2}}
    out["check_result"] = result_of(c, started)
    out["scenario_post"] = answer(c.post(f"{PIT}/scenarios", json={"params": body}))
    sid = out["scenario_post"]["body"]["id"]
    assert sid == 17, sid
    started = c.post(f"{PIT}/scenarios/{sid}/run", json={})
    assert started.status_code == 200 and sorted(started.json()) == ["id", "source"], started.text
    out["run_post"] = {"status": 200, "body": {"source": "project", "id": 3}}
    out["run_result"] = result_of(c, started)
    out["scenarios_list"] = answer(c.get(f"{PIT}/scenarios"))
    out["scenario_get"] = answer(c.get(f"{PIT}/scenarios/{sid}"))
    out["template_get"] = answer(c.get(f"{PIT}/scenarios/{sid}/template"))
    out["report_get"] = answer(c.get(f"{PIT}/scenarios/{sid}/report"))
    # the second scenario: one more RF, another price and its own fields — the comparison has a shell only in B
    price = [{"field": "AU", "unit": "g/t", "price": 2200.0, "sales": 5.0, "royalty": 0.02}]
    other = params(mid, rf=[*rf, 1.3], calendar=CALENDAR, elements=price, prefix="B_")
    b = c.post(f"{PIT}/scenarios", json={"name": "Вариант 2", "params": other}).json()["id"]
    result_of(c, c.post(f"{PIT}/scenarios/{b}/run", json={}))
    out["compare_result"] = answer(c.post(f"{PIT}/compare", json={"a": sid, "b": b}))
    # the outputs (21.12): the DTMs of all the shells and the solid of the second one, the real answers of the jobs
    out["surfaces_result"] = result_of(c, c.post(f"{PIT}/scenarios/{sid}/surfaces", json={}))
    out["solid_result"] = result_of(c, c.post(f"{PIT}/scenarios/{sid}/solid", json={"shell": 2}))
    # the refusals of the routes the windows tell in their places
    out["version_conflict"] = answer(c.put(f"{PIT}/scenarios/{sid}", json={"name": "Х", "expected_version": 1}))
    out["name_taken"] = answer(c.put(f"{PIT}/scenarios/{sid}", json={"name": "вариант 2", "expected_version": 2}))
    spare = c.post(f"{PIT}/scenarios", json={}).json()["id"]  # a new scenario: no model, nothing set
    out["pit_not_ready_run"] = answer(c.post(f"{PIT}/scenarios/{spare}/run", json={}))
    out["pit_no_result"] = answer(c.get(f"{PIT}/scenarios/{spare}/report"))
    with dialect.write_tx(c.project.engine) as conn:
        st.mark_running(conn, spare, None)
    out["pit_running"] = answer(c.put(f"{PIT}/scenarios/{spare}", json={"name": "Другое", "expected_version": 1}))
    with dialect.write_tx(c.project.engine) as conn:
        st.settle(conn, spare)
    out["scenario_not_found"] = answer(c.get(f"{PIT}/scenarios/99999"))
    out["scenario_put"] = answer(c.put(f"{PIT}/scenarios/{sid}", json={"name": "Вариант 1", "expected_version": 2}))
    out["scenario_delete"] = answer(c.delete(f"{PIT}/scenarios/{sid}"))
    ordered = {k: out[k] for k in KEYS}
    return json.loads(json.dumps(ordered, ensure_ascii=False))  # type: ignore[no-any-return]


def masked(x: Any) -> Any:
    """The floats to 9 significant digits; what varies from run to run is left out."""
    if isinstance(x, dict):
        return {k: masked(v) for k, v in x.items() if k not in SKIP}
    if isinstance(x, list):
        return [masked(v) for v in x]
    if isinstance(x, float):
        return float(f"{x:.9g}")
    return x


def write(data: dict[str, Any]) -> None:
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def test_answers_are_what_the_core_answers(pw) -> None:  # type: ignore[no-untyped-def]
    built = build(pw)
    stored = json.loads(DATA.read_text(encoding="utf-8"))
    if REGEN:
        theirs = {k: v for k, v in stored.items() if k not in KEYS and k not in ("_status", "_note")}
        old = {k: stored[k] for k in KEYS if k in stored}
        write({"_note": NOTE, **{k: built[k] for k in KEYS if k in old}, **theirs,
               **{k: built[k] for k in KEYS if k not in old}})  # fmt: skip
        stored = json.loads(DATA.read_text(encoding="utf-8"))
    for key in KEYS:
        assert masked(built[key]) == masked(stored[key]), f"{key}: BF_REGEN_PIT=1, {Path(__file__).name}"
    assert "настоящ" in stored["_note"] and "_status" not in stored


def test_the_answers_are_real_in_russian_with_a_hint_and_hold_the_contract() -> None:
    stored = json.loads(DATA.read_text(encoding="utf-8"))
    for key in KEYS:
        a = stored[key]
        if a["status"] >= 400:
            assert a["body"]["code"] and a["body"]["message"] and a["body"]["hint"], key
            assert any("а" <= ch.lower() <= "я" for ch in a["body"]["message"]), key
    run = stored["run_result"]["body"]
    assert run["algo"] == "pit-1" and run["shells"][-1]["k"] == len(run["shells"]) == 3
    assert [s["rf"] for s in run["shells"]] == [0.8, 1.0, 1.2] and run["outputs"]["fields"] == ["PIT", "ORE"]
    assert run["objective_error"] == pytest.approx(run["nodes"]["graph"] * run["q"] / 2.0)
    assert run["control"]["closed"] is True and run["control"]["nested"] is True
    chk = stored["check_result"]["body"]["domains"][0]  # ТЗ 8.11.2: 45°, cubes, K = 8 — 17 offsets, 1,44°
    assert chk["n_offsets"] == len(chk["offsets"]) == 17 and chk["error"]["max"] == pytest.approx(1.44, abs=0.01)
    assert stored["scenario_get"]["body"]["id"] == stored["scenarios_list"]["body"]["scenarios"][0]["id"] == 17
    assert stored["scenario_get"]["body"]["meta"]["params_hash"] == stored["run_result"]["body"]["params_hash"]
    rep = stored["report_get"]["body"]
    assert rep["calendar"] == CALENDAR and rep["npv_ready"] is True and len(rep["shells"]) == 3
    only = [s for s in stored["compare_result"]["body"]["shells"] if s["status"] == "only_b"]
    assert len(only) == 1 and only[0]["a"] is None and only[0]["diff"] is None  # a missing RF is a gap, not 0
    assert stored["pit_not_ready_run"]["body"]["missing"][0]["param"] == "model_id"
    assert stored["scenario_delete"]["body"] == {"id": 17, "deleted": True}
