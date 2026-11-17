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


def test_replaced_table_is_found_by_name_and_archive_keeps_the_audit(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from tests.test_import_job import _check

    c, set_id = demo
    st = c.get(f"{API}/audit/sets/{set_id}").json()
    old_table = st["settings"]["table"]
    assert (
        c.put(f"{API}/audit/sets/{set_id}/settings", json={"config": {"z_jump": 40}, "table": old_table}).status_code
        == 200
    )
    # «Заменить» the samples: a new table id; the saved choice follows the name «Пробы»
    f = tmp_path / "assay.csv"
    f.write_text("Hole_ID,From,To,AU,CU,SampleID\nDH-001,0,2,0.5,0.1,N1\nDH-001,2,4,0.7,0.2,N2\n", encoding="utf-8")
    chk = _check(c, f, kind="assay")
    spec = {
        "path": str(f),
        "kind": "assay",
        "mapping": chk["mapping"],
        "components": chk["components"],
        "table_name": "Пробы",
    }
    r = c.post(f"{API}/drillholes/import", json={"set_id": set_id, "files": [spec]}).json()
    assert wait_job(c, r["source"], r["id"])["status"] == "done"
    st = c.get(f"{API}/audit/sets/{set_id}").json()
    assert st["settings"]["table"] != old_table and st["settings"]["config"]["z_jump"] == 40
    assert st["result"]["stale"] is True  # the checked table was replaced
    res = _run(c, set_id)["result"]
    assert res["table_name"] == "Пробы" and res["stale"] is False

    # .bfggis round trip: the result, its table and the thresholds come along, not «устарел»
    r = c.post(f"{API}/projects/export", json={"path": str(tmp_path / "out"), "password": None}).json()
    job = wait_job(c, r["source"], r["id"])
    r = c.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(c, r["source"], r["id"])
    assert job["status"] == "done", job
    c.post(f"{API}/projects/{job['result']['uid']}/open")
    new_set = c.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    st = c.get(f"{API}/audit/sets/{new_set}").json()
    assert st["result"] is not None and st["result"]["stale"] is False and st["result"]["table_name"] == "Пробы"
    assert st["settings"]["config"]["z_jump"] == 40 and st["settings"]["table"] == st["result"]["table"]
    assert c.get(f"{API}/audit/sets/{new_set}/findings").json()["total"] > 0


def test_findings_by_hole_prefix(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    all_ = c.get(f"{API}/audit/sets/{set_id}/findings", params={"hole_prefix": "DH-01"}).json()["findings"]
    assert all_ and all(f["hole"].startswith("DH-01") for f in all_)
    assert (
        c.get(f"{API}/audit/sets/{set_id}/findings", params={"hole_prefix": "D_"}).json()["total"] == 0
    )  # _ is literal
