"""CSV/TXT and Excel files through the same upload -> options -> validate -> commit pipeline."""

import os
from io import BytesIO

import pytest
from openpyxl import Workbook

COLLAR_CSV = (
    "Протокол привязки устьев\r\n"
    "Скважина;X, м;Y, м;Z, м;Глубина (м);Азимут;Наклон;Статус\r\n"
    "С-1;466570,586;4584699,697;557,95;300,3;0;-90;пробурена\r\n"
    "С-2;466543,962;4584688,653;561,438;250;165;-60;пробурена\r\n"
).encode("cp1251")

ASSAY_CSV = (
    "Скважина,От,До,№ пробы,\"Au, г/т\",Cu %,Комментарий\n"
    "С-1,0,1,П-1,<0.005,0.12,\n"
    "С-1,1,2,П-2,1.25,н/д,керн разрушен\n"
).encode("utf-8")


def _upload(client, name, data):
    return client.post("/api/import/upload", files={"file": (name, data)})


def _xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Survey"
    ws.append(["BHID", "DEPTH", "BRG", "DIP"])
    ws.append(["С-1", 0, 10.5, -60])
    ws.append(["С-1", 50, 11, -61.5])
    wb.create_sheet("Notes").append(["just text"])
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_russian_collar_csv_needs_header_row_then_maps_itself(client):
    first = _upload(client, "устья.csv", COLLAR_CSV).json()
    assert first["format"] == "text"
    assert first["options"]["encoding"] == "cp1251"
    assert first["detected_kind"] is None  # the title line was taken as the header

    fixed = client.post(f"/api/import/{first['file_id']}/options", json={"header_row": 2}).json()

    assert fixed["detected_kind"] == "collar"
    assert fixed["suggestions"]["collar"]["mapping"] == {
        "hole_id": "Скважина", "x": "X, м", "y": "Y, м", "z": "Z, м", "td": "Глубина (м)",
        "azimuth": "Азимут", "dip": "Наклон", "status": "Статус",
    }
    assert fixed["options"] == {"encoding": "cp1251", "delimiter": ";", "header_row": 2, "sheet": None}
    assert fixed["preview"][0]["X, м"] == 466570.586


def test_reread_validates_options_and_keeps_previous_on_error(client):
    up = _upload(client, "c.csv", COLLAR_CSV).json()
    fid = up["file_id"]

    assert client.post(f"/api/import/{fid}/options", json={"header_row": 99}).status_code == 422
    assert client.post(f"/api/import/{fid}/options", json={"encoding": "ebcdic"}).status_code == 422
    assert client.post("/api/import/" + "0" * 32 + "/options", json={}).status_code == 404
    assert client.post("/api/import/nonsense/options", json={}).status_code == 400
    # the stored options are still the working ones
    assert client.post("/api/import/validate", json={
        "file_id": fid, "kind": "collar", "mapping": {},
    }).json()["ok"] is False


def test_assay_csv_components_detection_limit_and_missing_marks(client):
    up = _upload(client, "assay.csv", ASSAY_CSV).json()

    assert up["detected_kind"] == "assay"
    sug = up["suggestions"]["assay"]
    assert sug["mapping"] == {"hole_id": "Скважина", "from": "От", "to": "До", "sample_id": "№ пробы"}
    assert sug["components"] == [{"column": "Au, г/т", "unit": "g/t"}, {"column": "Cu %", "unit": "%"}]
    assert any("<x" in n for n in up["notes"])

    result = client.post("/api/import/validate", json={
        "file_id": up["file_id"], "kind": "assay", "mapping": sug["mapping"],
        "components": sug["components"], "attributes": ["Комментарий"],
    }).json()
    assert result["ok"] is True
    assert result["preview"][0]["comp:Au, г/т"] == 0.0025
    assert result["preview"][1]["comp:Cu %"] is None
    assert result["preview"][1]["attr:Комментарий"] == "керн разрушен"


def test_text_in_numeric_field_is_reported_with_an_example(client):
    data = "BHID,X,Y,Z,TDEPTH\nA,1,2,3,10\nB,1,2,3,ошибка\n".encode()
    up = _upload(client, "c.csv", data).json()

    result = client.post("/api/import/validate", json={
        "file_id": up["file_id"], "kind": "collar", "mapping": up["suggestions"]["collar"]["mapping"],
    }).json()

    assert result["ok"] is False
    issue = next(i for i in result["issues"] if i["code"] == "non_numeric")
    assert issue["field"] == "td" and issue["count"] == 1 and "ошибка" in issue["message"]


def test_excel_survey_upload_sheet_switch_and_commit(db_client):
    pid = db_client.post("/api/projects", json={"name": "Excel"}).json()["id"]
    up = _upload(db_client, "survey.xlsx", _xlsx()).json()

    assert up["format"] == "excel" and up["sheets"] == ["Survey", "Notes"]
    assert up["options"]["sheet"] == "Survey" and up["detected_kind"] == "survey"
    assert up["suggestions"]["survey"]["mapping"]["azimuth"] == "BRG"

    notes = db_client.post(f"/api/import/{up['file_id']}/options", json={"sheet": "Notes"}).json()
    assert notes["columns"][0]["name"] == "just text" and notes["row_count"] == 0
    back = db_client.post(f"/api/import/{up['file_id']}/options", json={"sheet": "Survey"}).json()

    committed = db_client.post("/api/import/commit", json={
        "project_id": pid, "file_id": up["file_id"], "kind": "survey",
        "mapping": back["suggestions"]["survey"]["mapping"],
    })
    assert committed.status_code == 200 and committed.json()["inserted"] == 2
    summary = db_client.get(f"/api/projects/{pid}/summary").json()
    assert summary["imports"][0]["filename"] == "survey.xlsx"  # name taken from the upload
    rows = db_client.get(f"/api/projects/{pid}/tables/survey").json()["rows"]
    assert rows[1] == {"hole_id": "С-1", "depth": 50.0, "azimuth": 11.0, "dip": -61.5}



@pytest.mark.skipif(not os.environ.get("MICROMINE_SAMPLE_DIR"), reason="set MICROMINE_SAMPLE_DIR to run on the real files")
def test_real_files_as_csv_and_excel_give_the_same_project_as_dat(db_client, tmp_path):
    """The sample DAT files re-saved as Russian CSV (cp1251, ';', decimal comma) and as xlsx."""
    from pathlib import Path

    from app.formats.micromine import read_micromine

    folder = Path(os.environ["MICROMINE_SAMPLE_DIR"])
    sources = {
        kind: next(p for p in folder.iterdir() if token in p.name and p.suffix.upper() == ".DAT")
        for kind, token in (("collar", "Collar"), ("survey", "Survey"), ("assay", "Assay"))
    }

    def as_csv(path):
        frame = read_micromine(path).frame
        return frame.to_csv(sep=";", decimal=",", index=False, lineterminator="\r\n").encode("cp1251")

    def as_xlsx(path):
        buffer = BytesIO()
        read_micromine(path).frame.to_excel(buffer, index=False)
        return buffer.getvalue()

    results = {}
    for fmt, convert in (("dat", lambda p: p.read_bytes()), ("csv", as_csv), ("xlsx", as_xlsx)):
        pid = db_client.post("/api/projects", json={"name": fmt}).json()["id"]
        for kind, path in sources.items():
            up = _upload(db_client, f"{kind}.{fmt}", convert(path)).json()
            assert up["detected_kind"] == kind, (fmt, kind)
            sug = up["suggestions"][kind]
            response = db_client.post("/api/import/commit", json={
                "project_id": pid, "file_id": up["file_id"], "kind": kind,
                "mapping": sug["mapping"], "components": sug["components"],
            })
            assert response.status_code == 200, (fmt, kind, response.json())
        audit = db_client.post(f"/api/projects/{pid}/audit").json()
        assay_page = db_client.get(f"/api/projects/{pid}/tables/assay", params={"limit": 3}).json()
        results[fmt] = (
            db_client.get(f"/api/projects/{pid}/summary").json()["counts"],
            audit["by_severity"],
            assay_page["rows"],
        )

    assert results["dat"][0] == {"collar": 101, "survey": 911, "assay": 22663}
    assert results["csv"] == results["dat"]
    assert results["xlsx"] == results["dat"]
