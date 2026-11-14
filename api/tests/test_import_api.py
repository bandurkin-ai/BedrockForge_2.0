import dataclasses
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.importing.service import detect_kind, suggest_mapping
from app.main import app
from app.routers import imports
from tests.dat_builder import build, encode_bocu

CP = "ibm-5347_P100-1998"


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(
        imports, "settings", dataclasses.replace(imports.settings, upload_dir=tmp_path / "uploads")
    )
    return TestClient(app)


def _collar() -> bytes:
    return build(
        CP,
        [
            (b"BHID", "C", 15, 0), (b"X", "R", 8, 3), (b"Y", "R", 8, 3), (b"Z", "R", 8, 3),
            (b"TDEPTH", "R", 8, 2), (b"AZ", "R", 8, 2), (b"DIP", "R", 8, 2),
            (b"STATUS", "C", 20, 0),
        ],
        [["H1", 466570.5, 4584699.7, 557.9, 300.3, 0.0, -90.0, "measured"],
         ["H2", 466543.9, 4584688.6, 561.4, 250.0, 165.0, -60.0, ""]],
    )


def _survey() -> bytes:
    return build(
        "BOCU-1",
        [
            (encode_bocu("BHID"), "C", 15, 0), (encode_bocu("DEPTH"), "R", 8, 3),
            (encode_bocu("BRG"), "R", 8, 3), (encode_bocu("BRG_TRUE"), "R", 8, 3),
            (encode_bocu("DIP"), "R", 8, 3),
        ],
        [[encode_bocu("H1"), 0.0, 177.6, 184.6, -89.7],
         [encode_bocu("H1"), 10.0, math.nan, 184.6, -89.7]],
    )


def _assay() -> bytes:
    return build(
        CP,
        [
            (b"BHID", "C", 15, 0), (b"SAMPID", "C", 20, 0), (b"FROM", "R", 8, 3), (b"TO", "R", 8, 3),
            (b"H", "R", 8, 3), (b"AU", "R", 8, 4), (b"CU", "R", 8, 5), (b"TYPE", "C", 20, 0),
        ],
        [["H1", "H1-01", 0.0, 1.0, 1.0, 0.007, 0.0003, "HOLES"],
         ["H1", "H1-02", 1.0, 2.0, 1.0, 0.012, 0.00005, "HOLES"]],
    )


def _upload(client: TestClient, name: str, data: bytes):
    return client.post("/api/import/upload", files={"file": (name, data)})


def test_schemas_endpoint_lists_three_tables(client):
    body = client.get("/api/import/schemas").json()

    assert set(body) == {"collar", "survey", "assay"}
    required = [f["key"] for f in body["collar"]["fields"] if f["required"]]
    assert required == ["hole_id", "x", "y", "z", "td"]


def test_upload_collar_detects_kind_and_suggests_mapping(client):
    resp = _upload(client, "collar.DAT", _collar())

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
    body = _upload(client, "survey.DAT", _survey()).json()

    assert body["detected_kind"] == "survey"
    assert body["suggestions"]["survey"]["mapping"]["azimuth"] == "BRG_TRUE"
    assert body["preview"][1]["BRG"] is None  # NaN must be valid JSON


def test_upload_assay_suggests_components_with_units(client):
    body = _upload(client, "assay.DAT", _assay()).json()

    assert body["detected_kind"] == "assay"
    assert body["suggestions"]["assay"]["components"] == [
        {"column": "AU", "unit": "g/t"},
        {"column": "CU", "unit": "%"},
    ]


def test_upload_rejects_unsupported_and_broken_files(client):
    assert _upload(client, "data.xlsx", b"x").status_code == 415
    assert _upload(client, "bad.DAT", b"not micromine").status_code == 422


def test_validate_collar_ok(client):
    body = _upload(client, "collar.DAT", _collar()).json()

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
    body = _upload(client, "collar.DAT", _collar()).json()
    mapping = dict(body["suggestions"]["collar"]["mapping"])
    del mapping["td"]

    result = client.post("/api/import/validate", json={
        "file_id": body["file_id"], "kind": "collar", "mapping": mapping,
    }).json()

    assert result["ok"] is False
    assert [i["code"] for i in result["issues"]] == ["missing_required"]
    assert result["issues"][0]["field"] == "td"


def test_validate_survey_user_can_pick_magnetic_azimuth_and_gets_empty_warning(client):
    body = _upload(client, "survey.DAT", _survey()).json()
    mapping = dict(body["suggestions"]["survey"]["mapping"], azimuth="BRG")

    result = client.post("/api/import/validate", json={
        "file_id": body["file_id"], "kind": "survey", "mapping": mapping,
    }).json()

    assert result["ok"] is True
    warnings = [i for i in result["issues"] if i["severity"] == "warning"]
    assert [(w["code"], w["field"], w["count"]) for w in warnings] == [("empty_values", "azimuth", 1)]


def test_validate_assay_requires_units_and_carries_attributes(client):
    body = _upload(client, "assay.DAT", _assay()).json()
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
    body = _upload(client, "collar.DAT", _collar()).json()
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
