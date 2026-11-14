from io import BytesIO

from openpyxl import load_workbook
from sqlalchemy.orm import Session

from app.audit.rules import RULES
from app.models import Assay, Collar, Component, Survey


from tests.helpers import new_set as _project


def _seed(engine, project_id: int, *, with_problems: bool = True) -> None:
    """Three holes; with_problems adds a gap, an orphan survey and an empty status."""
    with Session(engine) as s:
        for i, hole in enumerate(["A1", "A2", "A3"]):
            s.add(Collar(
                set_id=project_id, hole_id=hole, x=500000.0 + 50 * i, y=4500000.0, z=100.0, td=10.0,
                azimuth=0.0, dip=-90.0, status="" if (with_problems and hole == "A3") else "measured",
            ))
            s.add(Survey(set_id=project_id, hole_id=hole, depth=0.0, azimuth=0.0, dip=-90.0))
            for j in range(3):
                from_m = float(j) + (1.0 if (with_problems and hole == "A1" and j == 2) else 0.0)
                s.add(Assay(
                    set_id=project_id, hole_id=hole, from_m=from_m, to_m=from_m + 1, sample_id=f"{hole}-{j}",
                    analytes={"AU": 0.01 * (j + 1)},
                ))
        if with_problems:
            s.add(Survey(set_id=project_id, hole_id="GHOST", depth=0.0, azimuth=0.0, dip=-90.0))
        s.add(Component(set_id=project_id, name="AU", unit="g/t"))
        s.commit()


def test_latest_is_null_before_first_run(db_client):
    pid = _project(db_client)

    assert db_client.get(f"/api/sets/{pid}/audit").json() is None
    assert db_client.get(f"/api/sets/{pid}/audit/findings").json() == {"total": 0, "items": []}
    assert db_client.get(f"/api/sets/{pid}/audit/export.xlsx").status_code == 404


def test_run_reports_counts_and_every_rule(db_client, db_engine):
    pid = _project(db_client)
    _seed(db_engine, pid)

    run = db_client.post(f"/api/sets/{pid}/audit").json()

    by_code = {r["code"]: r for r in run["rules"]}
    assert len(by_code) == len(RULES)  # passed rules are listed too
    assert by_code["SURVEY_ORPHAN_HOLE"]["findings"] == 1
    assert by_code["ASSAY_GAP"]["findings"] == 1
    assert by_code["COLLAR_EMPTY_STATUS"]["findings"] == 1
    assert by_code["COLLAR_MISSING_COORDS"]["findings"] == 0
    assert run["by_severity"] == {"error": 1, "warning": 1, "info": 1}


def test_clean_project_has_no_findings(db_client, db_engine):
    pid = _project(db_client)
    _seed(db_engine, pid, with_problems=False)

    run = db_client.post(f"/api/sets/{pid}/audit").json()

    assert run["by_severity"] == {"error": 0, "warning": 0, "info": 0}


def test_findings_filters_and_order(db_client, db_engine):
    pid = _project(db_client)
    _seed(db_engine, pid)
    db_client.post(f"/api/sets/{pid}/audit")
    url = f"/api/sets/{pid}/audit/findings"

    everything = db_client.get(url).json()
    assert [f["severity"] for f in everything["items"]] == ["error", "warning", "info"]
    assert db_client.get(url, params={"severity": "warning"}).json()["total"] == 1
    assert db_client.get(url, params={"table": "survey"}).json()["items"][0]["hole_id"] == "GHOST"
    assert db_client.get(url, params={"code": "ASSAY_GAP"}).json()["items"][0]["details"]["examples"][0]["prev_to"] == 2.0
    assert db_client.get(url, params={"hole_id": "a3"}).json()["total"] == 1  # case-insensitive substring
    assert db_client.get(url, params={"hole": "A"}).json()["total"] == 0  # exact match only
    assert db_client.get(url, params={"hole": "A3"}).json()["total"] == 1
    assert db_client.get(url, params={"limit": 1, "offset": 2}).json()["items"][0]["severity"] == "info"


def test_rerun_replaces_previous_result_and_uses_config(db_client, db_engine):
    pid = _project(db_client)
    _seed(db_engine, pid)
    first = db_client.post(f"/api/sets/{pid}/audit").json()["run_id"]

    second = db_client.post(f"/api/sets/{pid}/audit", json={"depth_tolerance": 5.0}).json()

    assert second["run_id"] != first
    assert second["config"]["depth_tolerance"] == 5.0
    assert second["rules"][[r["code"] for r in second["rules"]].index("ASSAY_GAP")]["findings"] == 0  # gap of 1 m < 5 m
    assert db_client.get(f"/api/sets/{pid}/audit").json()["run_id"] == second["run_id"]


def test_audit_is_isolated_per_project_and_404_for_unknown(db_client, db_engine):
    first, second = _project(db_client, "A"), _project(db_client, "B")
    _seed(db_engine, first)

    db_client.post(f"/api/sets/{first}/audit")
    db_client.post(f"/api/sets/{second}/audit")

    assert db_client.get(f"/api/sets/{first}/audit/findings").json()["total"] == 3
    assert db_client.get(f"/api/sets/{second}/audit/findings").json()["total"] == 0
    assert db_client.post("/api/sets/9999/audit").status_code == 404


def test_deleting_set_removes_its_audit(db_client, db_engine):
    pid = _project(db_client)
    _seed(db_engine, pid)
    db_client.post(f"/api/sets/{pid}/audit")

    assert db_client.delete(f"/api/datasets/{pid}").status_code == 204
    assert db_client.get(f"/api/sets/{pid}/audit").status_code == 404

    with db_engine.connect() as conn:
        from sqlalchemy import text
        assert conn.execute(text("select count(*) from audit_finding")).scalar() == 0


def test_excel_export_has_three_sheets_with_findings(db_client, db_engine):
    pid = _project(db_client, "Тест")
    _seed(db_engine, pid)
    db_client.post(f"/api/sets/{pid}/audit")

    response = db_client.get(f"/api/sets/{pid}/audit/export.xlsx")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert response.headers["content-disposition"] == f'attachment; filename="audit_{pid}.xlsx"'
    wb = load_workbook(BytesIO(response.content))
    assert wb.sheetnames == ["Сводка", "По правилам", "Находки"]
    summary = {row[0].value: row[1].value for row in wb["Сводка"].iter_rows(min_row=2)}
    assert summary["Проект / набор"] == "Тест / Скважины"
    assert summary["Критично (находок)"] == 1
    assert wb["По правилам"].max_row == len(RULES) + 1  # header + rules
    findings = [[c.value for c in row] for row in wb["Находки"].iter_rows(min_row=2)]
    assert [f[0] for f in findings] == ["Критично", "Предупреждение", "Информация"]
    assert findings[0][3] == "GHOST"


def test_audit_becomes_stale_after_a_new_import(db_client, db_engine):
    from tests.samples import collar_dat

    pid = _project(db_client)
    _seed(db_engine, pid)
    assert db_client.post(f"/api/sets/{pid}/audit").json()["stale"] is False

    up = db_client.post("/api/import/upload", files={"file": ("collar.DAT", collar_dat())}).json()
    db_client.post("/api/import/commit", json={
        "set_id": pid, "file_id": up["file_id"], "filename": "collar.DAT", "kind": "collar",
        "mapping": up["suggestions"]["collar"]["mapping"],
    })

    assert db_client.get(f"/api/sets/{pid}/audit").json()["stale"] is True
    assert db_client.post(f"/api/sets/{pid}/audit").json()["stale"] is False
