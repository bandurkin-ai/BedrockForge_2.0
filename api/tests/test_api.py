from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok():
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_demo_project_is_created_once_with_audit(db_client):
    first = db_client.post("/api/projects/demo")
    second = db_client.post("/api/projects/demo")

    assert first.status_code == 200 and second.json()["id"] == first.json()["id"]
    pid = first.json()["id"]
    tree = db_client.get(f"/api/projects/{pid}/datasets").json()
    assert [d["kind"] for d in tree] == ["drillholes", "wireframes", "lines", "rasters", "rasters"]
    holes, wireframes = tree[:2]
    assert holes["counts"] == {"collar": 48, "survey": 307, "assay": 3916}
    assert holes["audit"]["error"] > 0
    assert db_client.get(f"/api/sets/{holes['id']}/audit").json()["by_severity"]["error"] > 0
    assert [w["name"] for w in wireframes["wireframes"]] == ["Рудное тело", "Ореол"]
    assert all(w["stats"]["closed"] for w in wireframes["wireframes"])
    assert len(db_client.get("/api/projects").json()) == 1
