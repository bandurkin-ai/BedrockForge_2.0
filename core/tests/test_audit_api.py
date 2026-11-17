"""Audit of a drillhole set through the API (ТЗ 7.12): the demo errors are found, every rule is reported,
thresholds change the result, «устарел» after an edit, the Excel report has three sheets."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    sets = pclient.get(f"{API}/drillholes/sets").json()["sets"]
    assert [s["name"] for s in sets] == ["Скважины 2025"] and sets[0]["holes"] == 48
    return pclient, sets[0]["id"]


def _run(c, set_id: int) -> dict:  # type: ignore[no-untyped-def]
    r = c.post(f"{API}/audit/sets/{set_id}/run")
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=120)
    assert job["status"] == "done", job
    return c.get(f"{API}/audit/sets/{set_id}").json()


def test_demo_errors_found_and_every_rule_reported(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    before = c.get(f"{API}/audit/sets/{set_id}").json()
    assert before["settings"]["table"] == before["tables"][0]["table"] and before["tables"][0]["name"] == "Пробы"
    res = _run(c, set_id)["result"]
    assert len(res["summary"]) == 27 and res["stale"] is False and res["table_name"] == "Пробы"
    found = {r["code"]: r["rows"] for r in res["summary"]}
    for code in (
        "SURVEY_BEYOND_TD",
        "ASSAY_NEGATIVE_VALUE",
        "ASSAY_OVERLAP",
        "ASSAY_GAP",
        "ASSAY_EMPTY_VALUE",
        "ASSAY_DUPLICATE_SAMPLE",
        "SURVEY_DOGLEG",
    ):
        assert found[code] > 0, code
    assert found["COLLAR_MISSING_COORDS"] == 0  # passed rules are listed too
    f = c.get(f"{API}/audit/sets/{set_id}/findings", params={"code": "SURVEY_BEYOND_TD"}).json()
    assert f["total"] == 1 and f["findings"][0]["hole"] == "DH-006" and f["findings"][0]["examples"]
    errors = c.get(f"{API}/audit/sets/{set_id}/findings", params={"severity": "error"}).json()["findings"]
    assert errors and all(x["severity"] == "error" for x in errors)
    by_hole = c.get(f"{API}/audit/sets/{set_id}/findings", params={"hole": "DH-012"}).json()["findings"]
    assert {x["code"] for x in by_hole} >= {"ASSAY_NEGATIVE_VALUE", "ASSAY_OVERLAP"}


def test_thresholds_stale_and_excel(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import openpyxl

    c, set_id = demo
    res = _run(c, set_id)["result"]
    doglegs = next(r["rows"] for r in res["summary"] if r["code"] == "SURVEY_DOGLEG")
    r = c.put(f"{API}/audit/sets/{set_id}/settings", json={"config": {"dogleg_per_30m": 0.5}, "table": None})
    assert r.status_code == 200 and r.json()["config"]["dogleg_per_30m"] == 0.5
    assert c.put(f"{API}/audit/sets/{set_id}/settings", json={"config": {"dogleg_per_30m": -1}}).status_code == 422
    res = _run(c, set_id)["result"]
    assert next(r["rows"] for r in res["summary"] if r["code"] == "SURVEY_DOGLEG") > doglegs

    # an edit makes the result «устарел»
    collar = c.get(f"{API}/drillholes/sets/{set_id}/tables/collar", params={"limit": 1}).json()["rows"][0]
    c.patch(f"{API}/drillholes/sets/{set_id}/tables/collar/rows/{collar['id']}", json={"column": "z", "value": "500"})
    assert c.get(f"{API}/audit/sets/{set_id}").json()["result"]["stale"] is True

    r = c.post(f"{API}/audit/sets/{set_id}/export", json={"path": str(tmp_path / "аудит")})
    job = wait_job(c, r.json()["source"], r.json()["id"])
    assert job["status"] == "done", job
    wb = openpyxl.load_workbook(job["result"]["path"])
    assert wb.sheetnames == ["Сводка", "По правилам", "Находки"]
    assert wb["По правилам"].max_row == 28 and wb["Находки"].max_row > 1
