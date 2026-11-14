from fastapi.testclient import TestClient

from app.importing.service import detect_kind, suggest_mapping
from tests.samples import assay_dat, collar_dat, survey_dat


def _upload(client: TestClient, name: str, data: bytes):
    return client.post("/api/import/upload", files={"file": (name, data)})


def test_schemas_endpoint_lists_three_tables(client):
    body = client.get("/api/import/schemas").json()

    assert set(body) == {"collar", "survey", "assay"}
    required = [f["key"] for f in body["collar"]["fields"] if f["required"]]
    assert required == ["hole_id", "x", "y", "z", "td"]


def test_upload_collar_detects_kind_and_suggests_mapping(client):
    resp = _upload(client, "collar.DAT", collar_dat())

    assert resp.status_code == 200
    body = resp.json()
    assert body["format"] == "micromine"
    assert body["row_count"] == 2
    assert body["detected_kind"] == "collar"
    assert body["suggestions"]["collar"]["mapping"] == {
        "hole_id": "BHID", "x": "X", "y": "Y", "z": "Z", "td": "TDEPTH",
        "azimuth": "AZ", "dip": "DIP", "status": "STATUS",
    }
    assert body["preview"][0]["BHID"] == "H1"


def test_upload_survey_defaults_to_true_azimuth_and_serializes_nan(client):
    body = _upload(client, "survey.DAT", survey_dat()).json()

    assert body["detected_kind"] == "survey"
    assert body["suggestions"]["survey"]["mapping"]["azimuth"] == "BRG_TRUE"
    assert body["preview"][1]["BRG"] is None  # NaN must be valid JSON


def test_upload_assay_suggests_components_with_units(client):
    body = _upload(client, "assay.DAT", assay_dat()).json()

    assert body["detected_kind"] == "assay"
    assert body["suggestions"]["assay"]["components"] == [
        {"column": "AU", "unit": "g/t"},
        {"column": "CU", "unit": "%"},
    ]


def test_upload_rejects_unsupported_and_broken_files(client):
    assert _upload(client, "notes.docx", b"x").status_code == 415
    old_excel = _upload(client, "data.xls", b"x")
    assert old_excel.status_code == 415 and ".xlsx" in old_excel.json()["detail"]
    assert _upload(client, "bad.DAT", b"not micromine").status_code == 422
    assert _upload(client, "broken.xlsx", b"not a zip").status_code == 422


def test_validate_collar_ok(client):
    body = _upload(client, "collar.DAT", collar_dat()).json()

    resp = client.post("/api/import/validate", json={
        "file_id": body["file_id"],
        "kind": "collar",
        "mapping": body["suggestions"]["collar"]["mapping"],
    })

    result = resp.json()
    assert result["ok"] is True
    assert result["row_count"] == 2
    assert result["columns"][:5] == ["hole_id", "x", "y", "z", "td"]
    assert result["preview"][0]["hole_id"] == "H1"


def test_validate_reports_missing_required_field(client):
    body = _upload(client, "collar.DAT", collar_dat()).json()
    mapping = dict(body["suggestions"]["collar"]["mapping"])
    del mapping["td"]

    result = client.post("/api/import/validate", json={
        "file_id": body["file_id"], "kind": "collar", "mapping": mapping,
    }).json()

    assert result["ok"] is False
    assert [i["code"] for i in result["issues"]] == ["missing_required"]
    assert result["issues"][0]["field"] == "td"


def test_validate_survey_user_can_pick_magnetic_azimuth_and_gets_empty_warning(client):
    body = _upload(client, "survey.DAT", survey_dat()).json()
    mapping = dict(body["suggestions"]["survey"]["mapping"], azimuth="BRG")

    result = client.post("/api/import/validate", json={
        "file_id": body["file_id"], "kind": "survey", "mapping": mapping,
    }).json()

    assert result["ok"] is True
    warnings = [i for i in result["issues"] if i["severity"] == "warning"]
    assert [(w["code"], w["field"], w["count"]) for w in warnings] == [("empty_values", "azimuth", 1)]


def test_validate_assay_requires_units_and_carries_attributes(client):
    body = _upload(client, "assay.DAT", assay_dat()).json()
    sug = body["suggestions"]["assay"]
    base = {"file_id": body["file_id"], "kind": "assay", "mapping": sug["mapping"]}

    no_unit = client.post("/api/import/validate", json={
        **base, "components": [{"column": "AU", "unit": " "}],
    }).json()
    assert no_unit["ok"] is False
    assert no_unit["issues"][0]["code"] == "missing_unit"

    good = client.post("/api/import/validate", json={
        **base, "components": sug["components"], "attributes": ["H", "TYPE"],
    }).json()
    assert good["ok"] is True
    assert good["columns"][-4:] == ["comp:AU", "comp:CU", "attr:H", "attr:TYPE"]


def test_validate_rejects_duplicate_column_and_unknown_file(client):
    body = _upload(client, "collar.DAT", collar_dat()).json()
    mapping = dict(body["suggestions"]["collar"]["mapping"], y="X")

    result = client.post("/api/import/validate", json={
        "file_id": body["file_id"], "kind": "collar", "mapping": mapping,
    }).json()
    assert result["ok"] is False
    assert any(i["code"] == "duplicate_column" for i in result["issues"])

    missing = client.post("/api/import/validate", json={
        "file_id": "0" * 32, "kind": "collar", "mapping": {},
    })
    assert missing.status_code == 404
    bad_id = client.post("/api/import/validate", json={
        "file_id": "../etc/passwd", "kind": "collar", "mapping": {},
    })
    assert bad_id.status_code == 400


def test_detect_kind_and_mapping_ignore_case_and_spaces():
    cols = ["hole id", "from", "to", "Sample-ID"]

    assert detect_kind(["Hole_ID", "From", "To"]) == "assay"
    assert detect_kind(["foo", "bar"]) is None
    assert suggest_mapping("assay", ["hole_id", "from", "to", "Sample-ID"])["sample_id"] == "Sample-ID"
    assert suggest_mapping("assay", cols)["hole_id"] == "hole id"  # spaces are treated as underscores
