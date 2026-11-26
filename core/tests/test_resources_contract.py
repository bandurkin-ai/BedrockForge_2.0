# ruff: noqa: F811 - the fixture `ra` is imported, and the tests take it by its name
"""The answers of the core the windows of the resource reports are read against (подшаг 20.7; e20-api.md §3, §5, §14):
the real routes on the model of Т8.10-1 (8 blocks 10×10×10 m, ρ = 2,5, Au 0,2…5,0 g/t, the class of each block), the
jobs followed to their end — one file, `core/tests/data/resources/contract_answers.json`, with the same keys as the
samples of the contract (`{status, body}`), read by vitest (`ui/test/resources/*`).

Regenerate: `BF_REGEN_RESOURCES=1 uv run pytest -q tests/test_resources_contract.py` — the keys of this test replace
the samples, the others stay as they are: `defaults`, `bad_param`, `group_numeric`, `refusals` are written by `spec.py`
(`test_resources_spec.py`); `export_*`, `files_exist` (20.5) and `drill*` (20.9) are samples of the contract until
their routes write them; `compare_reports`, `compare_post`, `compare_result` and `estimate_compare` (20.10) are real.
Without the variable the test checks that the core still answers what the file
holds; the times, the fingerprints, the versions of the libraries and the numbers of the jobs are left out, the floats
are compared to 9 digits."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.resources import job as rj
from bf_core.resources import store as st
from tests.estapi_util import finish, read_only
from tests.resapi_world import API, BODY, RS, many_groups, model, ra  # noqa: F401 - the fixtures
from tests.test_blocks_fields_store import add_fields

DATA = Path(__file__).parent / "data" / "resources" / "contract_answers.json"
REGEN = os.environ.get("BF_REGEN_RESOURCES") == "1"
NOTE = ("ЧАСТЬ КЛЮЧЕЙ — ответы настоящего ядра (BF_REGEN_RESOURCES=1 uv run pytest -q "
        "tests/test_resources_contract.py, "
        "20.7, модель Т8.10-1: 8 блоков 10×10×10 м, ρ = 2,5, Au 0,2…5,0 г/т, борт 1,0 → 12 500 т, 2,5 г/т, 31,25 кг; "
        "tests/resapi_world.py); export_*, files_exist (20.5), drill* (20.9) — ОБРАЗЦЫ форм "
        "(docs/progress/e20-api.md §8–§10); defaults, bad_param, group_numeric, refusals пишет spec.py "
        "(BF_REGEN_RESOURCES=1 uv run pytest -q tests/test_resources_spec.py). Интерфейс принимается только на "
        "настоящих ответах.")  # fmt: skip
#: the keys this test writes, in the order of the file (the samples of the others follow)
KEYS = ("report_post", "preview_result", "report_result", "reports_list", "report_get", "report_put", "report_rebuilt",
        "report_delete", "templates", "template_get", "template_gkz", "template_put", "template_delete",
        "report_not_found", "template_not_found", "field_not_found", "filter_not_found", "report_building",
        "version_conflict", "name_taken", "filter_field_missing", "report_too_big", "read_only", "compare_reports",
        "compare_post", "compare_result", "estimate_compare")  # fmt: skip
SKIP = ("seconds", "created_at", "updated_at", "cells_sha", "versions", "job_id", "at", "stages")


def answer(r: Any) -> dict[str, Any]:
    return {"status": r.status_code, "body": r.json()}


def result_of(c: Any, started: Any) -> dict[str, Any]:
    assert started.status_code == 200, started.text
    job = finish(c, started.json(), timeout=120)
    assert job["status"] == "done", job
    return {"status": 200, "body": job["result"]}


def start(c: Any, params: dict[str, Any], **top: Any) -> Any:
    return c.post(f"{RS}/reports", json={"params": params, **top})


def build(c: Any) -> dict[str, Any]:
    mid = model(c)
    base = {"model_id": mid, **BODY, "cutoffs": [1.0, 2.0], "template_id": "jorc"}
    out: dict[str, Any] = {}
    started = start(c, base)
    assert started.status_code == 200 and sorted(started.json()) == ["id", "source"]
    out["report_post"] = {"status": 200, "body": {"source": "project", "id": 1}}
    out["preview_result"] = result_of(c, started)
    saved = result_of(c, start(c, {**base, "curve": 5}, save=True, name="Отчёт по ресурсам"))
    out["report_result"] = saved
    rid = saved["body"]["report_id"]
    out["reports_list"] = answer(c.get(f"{RS}/reports"))
    out["report_get"] = answer(c.get(f"{RS}/reports/{rid}", params={"template": "jorc", "cutoff": 1.0}))
    out["report_put"] = answer(c.put(f"{RS}/reports/{rid}", json={"name": "Ресурсы ЮЗ", "expected_version": 1}))
    with dialect.read_tx(c.project.engine) as conn:
        again = st.rebuild_params(conn, rid)
    out["report_rebuilt"] = {"status": 200, "body": rj.run_report(c.project.engine, c.project.folder, again)}
    other = result_of(c, start(c, base, save=True, name="Второй"))["body"]["report_id"]
    out["report_delete"] = answer(c.delete(f"{RS}/reports/{other}"))
    out["templates"] = answer(c.get(f"{RS}/templates"))
    out["template_get"] = answer(c.get(f"{RS}/templates/jorc"))
    out["template_gkz"] = answer(c.get(f"{RS}/templates/gkz"))
    mine = {"name": "Свой", "labels": ["Руда", "Порода"], "subtotals": [], "total": "Всего"}
    out["template_put"] = answer(c.put(f"{RS}/templates/svoy", json={"template": mine}))
    out["template_delete"] = answer(c.delete(f"{RS}/templates/svoy", params={"expected_version": 1}))
    out["report_not_found"] = answer(c.get(f"{RS}/reports/99999"))
    out["template_not_found"] = answer(c.get(f"{RS}/templates/nope"))
    out["field_not_found"] = answer(start(c, {**base, "groups": ["NOPE"]}))
    out["filter_not_found"] = answer(start(c, {**base, "filter_id": 99_999}))
    with dialect.write_tx(c.project.engine) as conn:
        st.mark_building(conn, rid)
    out["report_building"] = answer(c.put(f"{RS}/reports/{rid}", json={"name": "Х", "expected_version": 2}))
    with dialect.write_tx(c.project.engine) as conn:
        st.settle(conn, rid)
    out["version_conflict"] = answer(c.put(f"{RS}/reports/{rid}", json={"name": "Х", "expected_version": 7}))
    second = result_of(c, start(c, base, save=True, name="Третий"))["body"]
    out["name_taken"] = answer(c.put(f"{RS}/reports/{second['report_id']}", json={"name": "ресурсы юз",
                                                                                 "expected_version": 1}))  # fmt: skip
    pit = {"cond": {"field": "PIT", "op": "<=", "value": 3}}
    out["filter_field_missing"] = answer(start(c, {**base, "restrict": pit}))
    big = {"model_id": many_groups(c, 3000), "elements": ["AU"], "density": 2.5, "groups": ["DOM"], "curve": 0,
           "cutoffs": [0.01 * i for i in range(150)]}  # fmt: skip
    out["report_too_big"] = answer(start(c, big))
    back = read_only(c)
    try:
        out["read_only"] = answer(start(c, base, save=True))
    finally:
        back()
    out.update(compare_answers(c, mid, base))
    assert tuple(out) == KEYS, tuple(out)
    return json.loads(json.dumps(out, ensure_ascii=False))  # type: ignore[no-any-return]


def compare_answers(c: Any, mid: int, base: dict[str, Any]) -> dict[str, Any]:
    """Д-37 (20.10): two reports of one model with other cut-offs and density (a row only in one), and two estimates
    of the model (OK against ID, with their STATUS) compared by the job of the route."""
    a = result_of(c, start(c, base, save=True, name="Сравнение A"))["body"]["report_id"]
    other = {**base, "density": 2.7, "cutoffs": [1.0, 3.0]}
    b = result_of(c, start(c, other, save=True, name="Сравнение B"))["body"]["report_id"]
    out: dict[str, Any] = {"compare_reports": answer(c.post(f"{RS}/compare", json={"report_a": a, "report_b": b}))}
    au = np.array([0.2, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0])
    est = {"op": "estimate", "params": {}, "inputs": [], "algo": "estimate-1"}
    fields = [{"name": "OK_EST", "dtype": "f8", "unit": "g/t"}, {"name": "OK_STATUS", "dtype": "i1"},
              {"name": "ID_EST", "dtype": "f8", "unit": "g/t"}, {"name": "ID_STATUS", "dtype": "i1"}]  # fmt: skip
    ok = np.where(au > 0.3, au, np.nan)
    idw = np.where(au < 4.0, au * 1.1 - 0.05, np.nan)
    status = {"OK_STATUS": np.where(np.isfinite(ok), 0, 1).astype(float),
              "ID_STATUS": np.where(np.isfinite(idw), 0, 1).astype(float)}  # fmt: skip
    add_fields(c.project, c.store, mid, fields, {"OK_EST": ok, "ID_EST": idw, **status}, source=est, step=3)
    url = f"{API}/blocks/{mid}/compare"
    started = c.post(url, json={"field_a": "OK_EST", "field_b": "ID_EST", "domain": "CLASS"})
    assert started.status_code == 200 and sorted(started.json()) == ["id", "source"], started.text
    out["compare_post"] = {"status": 200, "body": {"source": "project", "id": 1}}
    out["compare_result"] = result_of(c, started)
    out["estimate_compare"] = out["compare_result"]
    return out


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


@pytest.fixture(autouse=True)
def _job_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """`estimate.compare` is in the workers (until the line of `jobs/registry.py`): before the core starts."""
    monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.blocks.compare_job")


def test_answers_are_what_the_core_answers(ra) -> None:  # type: ignore[no-untyped-def]
    built = build(ra)
    stored = json.loads(DATA.read_text(encoding="utf-8"))
    if REGEN:
        theirs = {k: v for k, v in stored.items() if k not in KEYS and k != "_note"}
        write({"_note": NOTE, **built, **theirs})
        stored = json.loads(DATA.read_text(encoding="utf-8"))
    for key in KEYS:
        assert masked(built[key]) == masked(stored[key]), f"{key}: BF_REGEN_RESOURCES=1, {Path(__file__).name}"
    assert "настоящ" in stored["_note"]


def test_the_answers_are_in_russian_with_a_hint_and_the_numbers_are_t8_10_1() -> None:
    stored = json.loads(DATA.read_text(encoding="utf-8"))
    for key in KEYS:
        a = stored[key]
        if a["status"] >= 400:
            assert a["body"]["code"] and a["body"]["message"] and a["body"]["hint"], key
            assert any("а" <= ch.lower() <= "я" for ch in a["body"]["message"]), key
    res = stored["report_result"]["body"]
    assert res["saved"] is True and res["rows_total"] == len(res["rows"]) and res["algo"] == "resources-1"
    assert res["totals"]["tonnes"] == 12_500.0 and res["totals"]["elements"]["AU"]["metal"] == 31_250.0
    assert stored["preview_result"]["body"]["saved"] is False and stored["preview_result"]["body"]["report_id"] is None
    assert stored["report_get"]["body"]["form"]["sections"][0]["rows"][-1]["cells"]["grade:AU"]["text"] == "2,50"
