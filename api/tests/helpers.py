"""Shared helpers for API tests."""


def new_set(client, name: str = "Проект", set_name: str = "Скважины") -> int:
    """Create a project with one empty drillhole set; returns the set id."""
    project = client.post("/api/projects", json={"name": name})
    assert project.status_code == 201, project.text
    created = client.post(f"/api/projects/{project.json()['id']}/sets", json={"name": set_name})
    assert created.status_code == 201, created.text
    return created.json()["id"]
