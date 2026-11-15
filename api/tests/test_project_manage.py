"""Project management: rename, description, stats."""



def test_rename_describe_and_stats(db_client):
    pid = db_client.post("/api/projects", json={"name": "Участок", "description": " Лицензия 123 "}).json()["id"]
    db_client.post("/api/projects", json={"name": "Другой"})

    renamed = db_client.patch(f"/api/projects/{pid}", json={"name": "  Северный  "}).json()
    assert renamed["name"] == "Северный" and renamed["description"] == "Лицензия 123"
    assert db_client.patch(f"/api/projects/{pid}", json={"description": "Разведка 2026"}).json()["name"] == "Северный"
    clash = db_client.patch(f"/api/projects/{pid}", json={"name": "Другой"})
    assert clash.status_code == 409
    assert db_client.patch(f"/api/projects/{pid}", json={"name": "   "}).status_code == 422
    assert db_client.patch("/api/projects/999999", json={"name": "x"}).status_code == 404

    sid = db_client.post(f"/api/projects/{pid}/sets", json={"name": "Скважины"}).json()["id"]
    stats = db_client.get(f"/api/projects/{pid}/stats").json()
    assert stats["datasets"]["drillholes"] == 1 and stats["holes"] == 0 and stats["chats"] == 0
    assert stats["updated_at"]
    assert sid


def test_demo_project_stats(db_client):
    pid = db_client.post("/api/projects/demo").json()["id"]

    s = db_client.get(f"/api/projects/{pid}/stats").json()

    assert s["datasets"]["drillholes"] == 1 and s["datasets"]["rasters"] == 2 and s["holes"] == 48 and s["assays"] > 0 and s["wireframes"] > 0
