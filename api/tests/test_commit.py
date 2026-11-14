import pytest
from fastapi.testclient import TestClient

from tests.samples import assay_dat, collar_dat, survey_dat


def _project(client: TestClient, name: str = "Проект") -> int:
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 201
    return resp.json()["id"]


def _import(client: TestClient, project_id: int, filename: str, data: bytes, kind: str | None = None, **extra):
    up = client.post("/api/import/upload", files={"file": (filename, data)}).json()
    kind = kind or up["detected_kind"]
    sug = up["suggestions"][kind]
    body = {
        "project_id": project_id,
        "file_id": up["file_id"],
        "filename": filename,
        "kind": kind,
        "mapping": sug["mapping"],
        "components": sug["components"],
        "attributes": [],
    }
    body.update(extra)
    return client.post("/api/import/commit", json=body)


def test_project_crud_and_duplicate_name(db_client):
    pid = _project(db_client, "Альфа")

    assert [p["name"] for p in db_client.get("/api/projects").json()] == ["Альфа"]
    assert db_client.post("/api/projects", json={"name": "Альфа"}).status_code == 409
    assert db_client.delete(f"/api/projects/{pid}").status_code == 204
    assert db_client.get("/api/projects").json() == []
    assert db_client.get(f"/api/projects/{pid}/summary").status_code == 404


def test_commit_all_three_tables_and_summary(db_client):
    pid = _project(db_client)

    assert _import(db_client, pid, "collar.DAT", collar_dat()).json()["inserted"] == 2
    assert _import(db_client, pid, "survey.DAT", survey_dat()).json()["inserted"] == 2
    assay = _import(db_client, pid, "assay.DAT", assay_dat(), attributes=["H", "TYPE"])
    assert assay.status_code == 200
    assert assay.json()["inserted"] == 2

    summary = db_client.get(f"/api/projects/{pid}/summary").json()
    assert summary["counts"] == {"collar": 2, "survey": 2, "assay": 2}
    assert summary["components"] == [{"name": "AU", "unit": "g/t"}, {"name": "CU", "unit": "%"}]
    assert [j["kind"] for j in summary["imports"]] == ["assay", "survey", "collar"]


def test_table_page_returns_data_with_nan_as_null(db_client):
    pid = _project(db_client)
    up =db_client.post("/api/import/upload", files={"file": ("survey.DAT", survey_dat())}).json()
    mapping = dict(up["suggestions"]["survey"]["mapping"], azimuth="BRG")  # BRG has a NaN
    resp = db_client.post("/api/import/commit", json={
        "project_id": pid, "file_id": up["file_id"], "filename": "survey.DAT",
        "kind": "survey", "mapping": mapping,
    })
    assert resp.status_code == 200
    assert [i["code"] for i in resp.json()["issues"]] == ["empty_values"]

    page = db_client.get(f"/api/projects/{pid}/tables/survey").json()

    assert page["total"] == 2
    assert page["columns"] == ["hole_id", "depth", "azimuth", "dip"]
    assert page["rows"][0] == {"hole_id": "H1", "depth": 0.0, "azimuth": 177.6, "dip": -89.7}
    assert page["rows"][1]["azimuth"] is None


def test_assay_table_expands_components_and_stores_attributes(db_client):
    pid = _project(db_client)
    _import(db_client, pid, "assay.DAT", assay_dat(), attributes=["H", "TYPE"])

    page = db_client.get(f"/api/projects/{pid}/tables/assay").json()

    assert page["columns"] == ["hole_id", "from_m", "to_m", "sample_id", "AU", "CU"]
    assert page["rows"][0] == {
        "hole_id": "H1", "from_m": 0.0, "to_m": 1.0, "sample_id": "H1-01", "AU": 0.007, "CU": 0.0003,
    }


def test_reimport_replaces_rows_and_updates_unit(db_client):
    pid = _project(db_client)
    _import(db_client, pid, "assay.DAT", assay_dat())

    again = _import(
        db_client, pid, "assay.DAT", assay_dat(),
        components=[{"column": "AU", "unit": "ppm"}, {"column": "CU", "unit": "%"}],
    )

    assert again.json()["replaced"] == 2
    summary = db_client.get(f"/api/projects/{pid}/summary").json()
    assert summary["counts"]["assay"] == 2  # replaced, not appended
    assert {"name": "AU", "unit": "ppm"} in summary["components"]


def test_projects_are_isolated(db_client):
    first, second = _project(db_client, "A"), _project(db_client, "B")
    _import(db_client, first, "collar.DAT", collar_dat())

    assert db_client.get(f"/api/projects/{first}/summary").json()["counts"]["collar"] == 2
    assert db_client.get(f"/api/projects/{second}/summary").json()["counts"]["collar"] == 0


def test_commit_rejects_invalid_mapping_and_unknown_project(db_client):
    pid = _project(db_client)
    up = db_client.post("/api/import/upload", files={"file": ("collar.DAT", collar_dat())}).json()
    mapping = dict(up["suggestions"]["collar"]["mapping"])
    del mapping["td"]
    base = {"file_id": up["file_id"], "filename": "c.DAT", "kind": "collar", "mapping": mapping}

    bad = db_client.post("/api/import/commit", json={**base, "project_id": pid})
    assert bad.status_code == 422
    assert bad.json()["detail"]["issues"][0]["code"] == "missing_required"
    assert db_client.get(f"/api/projects/{pid}/summary").json()["counts"]["collar"] == 0

    missing = db_client.post("/api/import/commit", json={**base, "project_id": 9999})
    assert missing.status_code == 404


def test_duplicate_hole_ids_in_collar_are_an_error(db_client):
    from tests.dat_builder import build

    data = build(
        "ibm-5347_P100-1998",
        [(b"BHID", "C", 15, 0), (b"X", "R", 8, 3), (b"Y", "R", 8, 3), (b"Z", "R", 8, 3), (b"TDEPTH", "R", 8, 2)],
        [["H1", 1.0, 2.0, 3.0, 10.0], ["H1", 1.0, 2.0, 3.0, 20.0]],
    )
    pid = _project(db_client)

    resp = _import(db_client, pid, "collar.DAT", data)

    assert resp.status_code == 422
    assert resp.json()["detail"]["issues"][0]["code"] == "duplicate_hole_id"


@pytest.mark.parametrize("kind", ["collar", "survey", "assay"])
def test_table_endpoint_pagination(db_client, kind):
    pid = _project(db_client)
    data = {"collar": collar_dat, "survey": survey_dat, "assay": assay_dat}[kind]()
    _import(db_client, pid, f"{kind}.DAT", data)

    page = db_client.get(f"/api/projects/{pid}/tables/{kind}", params={"limit": 1, "offset": 1}).json()

    assert page["total"] == 2
    assert len(page["rows"]) == 1


def test_table_hole_filter_and_holes_list_with_counts(db_client):
    pid = _project(db_client)
    _import(db_client, pid, "collar.DAT", collar_dat())
    _import(db_client, pid, "survey.DAT", survey_dat())
    _import(db_client, pid, "assay.DAT", assay_dat())

    holes = db_client.get(f"/api/projects/{pid}/holes").json()

    assert [h["hole_id"] for h in holes] == ["H1", "H2"]
    h1, h2 = holes
    assert (h1["survey_count"], h1["assay_count"]) == (2, 2)
    assert (h2["survey_count"], h2["assay_count"]) == (0, 0)
    assert h1["x"] == 466570.5 and h1["status"] == "measured"
    assert (h1["error"], h1["warning"], h1["info"]) == (0, 0, 0)  # no audit yet

    page = db_client.get(f"/api/projects/{pid}/tables/survey", params={"hole_id": "H1"}).json()
    assert page["total"] == 2
    assert db_client.get(f"/api/projects/{pid}/tables/survey", params={"hole_id": "H2"}).json()["total"] == 0


def test_holes_are_naturally_sorted_and_carry_audit_counts(db_client, db_engine):
    from sqlalchemy.orm import Session

    from app.models import Collar

    pid = _project(db_client)
    with Session(db_engine) as s:
        for hole in ["H-10", "H-2", "H-1"]:
            s.add(Collar(project_id=pid, hole_id=hole, x=1.0, y=1.0, z=1.0, td=10.0, status=""))
        s.commit()
    db_client.post(f"/api/projects/{pid}/audit")

    holes = db_client.get(f"/api/projects/{pid}/holes").json()

    assert [h["hole_id"] for h in holes] == ["H-1", "H-2", "H-10"]
    assert all(h["info"] >= 1 for h in holes)  # empty status + no assay are info findings
    assert all(h["warning"] >= 1 for h in holes)  # no survey is a warning


def test_hole_detail_lists_its_findings_and_404s(db_client, db_engine):
    from sqlalchemy.orm import Session

    from app.models import Collar

    pid = _project(db_client)
    with Session(db_engine) as s:
        s.add(Collar(project_id=pid, hole_id="A", x=1.0, y=1.0, z=1.0, td=10.0, azimuth=90.0, dip=-60.0,
                     status="", attributes={"rig": "R1"}))
        s.commit()
    db_client.post(f"/api/projects/{pid}/audit")

    detail = db_client.get(f"/api/projects/{pid}/holes/A").json()

    assert detail["azimuth"] == 90.0 and detail["dip"] == -60.0 and detail["attributes"] == {"rig": "R1"}
    assert [f["severity"] for f in detail["findings"]] == ["warning", "info", "info"]
    assert db_client.get(f"/api/projects/{pid}/holes/NOPE").status_code == 404
