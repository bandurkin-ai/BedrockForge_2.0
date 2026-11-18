"""Saved views (ТЗ 7.35 Д-09, ADR 0008): save, apply, limits, versions, `.bfggis` renumbering."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.projects.service import projects_of
from bf_core.viewstate import views
from tests.test_import_job import API, wait_job

CAM = {
    "position": [5000.123456789012, 8000.987654321098, 650.5],
    "target": [5010.5, 8010.25, 300.0],
    "up": [0, 0, 1],
    "zoom": 1.5,
    "ortho": True,
    "fov": 45,
}


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects/demo").status_code == 200
    set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    return pclient, set_id


def put(c, key: str, value) -> None:  # type: ignore[no-untyped-def]
    cur = c.get(f"{API}/view").json()["values"].get(key, {}).get("version", 0)
    r = c.put(f"{API}/view/{key}", json={"value": value, "expected_version": cur})
    assert r.status_code == 200, r.text


def test_save_apply_roundtrip(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    put(c, "scene", {"vexag": 3, "grid": False})
    put(c, "hidden", [f"set:{set_id}:collars"])
    r = c.post(f"{API}/views", json={"camera": CAM})
    assert r.status_code == 200, r.text
    v1 = r.json()
    assert v1["name"] == "Вид 1" and v1["version"] == 1
    assert v1["camera"]["position"] == CAM["position"]  # float64 as sent
    assert v1["keys"]["scene"]["vexag"] == 3 and v1["keys"]["hidden"] == [f"set:{set_id}:collars"]
    assert c.post(f"{API}/views", json={"camera": CAM}).json()["name"] == "Вид 2"

    # the view changes; applying returns every key at once
    put(c, "scene", {"vexag": 1})
    put(c, "hidden", [f"set:{set_id}"])
    put(c, "hide_all", {"on": True, "shown": []})
    r = c.post(f"{API}/views/{v1['id']}/apply")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["camera"] == v1["camera"]
    assert got["values"]["scene"]["value"]["vexag"] == 3
    assert got["values"]["hidden"]["value"] == [f"set:{set_id}:collars"]
    assert got["values"]["hide_all"]["value"] == {"on": False, "shown": []}
    now = c.get(f"{API}/view").json()["values"]
    assert now["scene"]["value"]["grid"] is False and now["hidden"]["version"] == got["values"]["hidden"]["version"]

    listed = c.get(f"{API}/views").json()
    assert [v["name"] for v in listed["views"]] == ["Вид 1", "Вид 2"] and listed["limit"] == 200
    assert "keys" not in listed["views"][0]


def test_rename_overwrite_conflict_delete(demo) -> None:  # type: ignore[no-untyped-def]
    c, _ = demo
    v = c.post(f"{API}/views", json={"camera": CAM, "name": "Северный борт"}).json()
    r = c.put(f"{API}/views/{v['id']}", json={"expected_version": 1, "name": "Борт С", "folder": " Разрезы / Север "})
    assert r.status_code == 200 and r.json()["name"] == "Борт С" and r.json()["folder"] == "Разрезы/Север"
    stale = c.put(f"{API}/views/{v['id']}", json={"expected_version": 1, "name": "x"})
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict" and stale.json()["version"] == 2
    put(c, "scene", {"vexag": 5})
    cam = {**CAM, "zoom": 3.0}
    r = c.put(f"{API}/views/{v['id']}", json={"expected_version": 2, "camera": cam})
    assert r.json()["camera"]["zoom"] == 3.0 and r.json()["keys"]["scene"]["vexag"] == 5
    assert r.json()["folder"] == "Разрезы/Север"  # untouched without the field
    assert c.delete(f"{API}/views/{v['id']}").status_code == 200
    assert c.post(f"{API}/views/{v['id']}/apply").status_code == 404


def test_bad_values_and_limit(demo) -> None:  # type: ignore[no-untyped-def]
    c, _ = demo
    for cam in ({**CAM, "position": [1, 2]}, {**CAM, "zoom": 0}, {**CAM, "fov": 200}, {**CAM, "ortho": "yes"}, "x"):
        r = c.post(f"{API}/views", json={"camera": cam})
        assert r.status_code == 422 and r.json()["code"] == "bad_view" and r.json()["hint"], cam
    assert c.post(f"{API}/views", json={"camera": CAM, "name": "  "}).status_code == 422
    project = projects_of(c.core).require_current()
    now = dialect.utcnow()
    with dialect.write_tx(project.engine) as conn:
        conn.execute(
            sa.insert(objects),
            [
                dict(
                    kind="view",
                    name=f"v{i}",
                    name_key=f"v{i}",
                    sort_key=0.0,
                    state="ready",
                    version=1,
                    meta={"v": 1},
                    created_at=now,
                    updated_at=now,
                )
                for i in range(views.MAX_VIEWS)
            ],
        )
    r = c.post(f"{API}/views", json={"camera": CAM})
    assert r.status_code == 422 and "200 видов" in r.json()["message"]


def test_apply_drops_deleted_objects(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    put(c, "hidden", [f"set:{set_id}", "wf:999999"])
    v = c.post(f"{API}/views", json={"camera": CAM}).json()
    assert v["keys"]["hidden"] == [f"set:{set_id}"]  # the snapshot is read normalized
    project = projects_of(c.core).require_current()
    with dialect.write_tx(project.engine) as conn:
        conn.execute(
            sa.update(objects)
            .where(objects.c.id == v["id"])
            .values(meta={**v, "v": 1, "camera": CAM, "keys": {"hidden": [f"set:{set_id}", "wf:999999"]}})
        )
    got = c.post(f"{API}/views/{v['id']}/apply").json()
    assert got["values"]["hidden"]["value"] == [f"set:{set_id}"]
    assert got["values"]["scene"]["value"]["vexag"] == 1  # a key the view lacks: its default


def test_bfggis_renumbers_views(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    put(c, "hidden", [f"set:{set_id}:paths"])
    put(c, "hide_all", {"on": True, "shown": [f"set:{set_id}"]})
    v = c.post(f"{API}/views", json={"camera": CAM, "name": "Для печати"}).json()
    # without the view settings the views still come along (they are objects)
    out = tmp_path / "views"
    r = c.post(f"{API}/projects/export", json={"path": str(out), "password": None, "include_view": False})
    job = wait_job(c, r.json()["source"], r.json()["id"])
    r = c.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(c, r["source"], r["id"])
    assert job["status"] == "done", job
    c.post(f"{API}/projects/{job['result']['uid']}/open")
    new_set = job["result"]["id_maps"]["objects"][str(set_id)]
    new_view = job["result"]["id_maps"]["objects"][str(v["id"])]
    got = c.get(f"{API}/views/{new_view}").json()
    assert got["name"] == "Для печати" and got["camera"] == v["camera"]
    assert got["keys"]["hidden"] == [f"set:{new_set}:paths"]
    assert got["keys"]["hide_all"] == {"on": True, "shown": [f"set:{new_set}"]}


def test_remap_meta_charts_and_unknown_keys() -> None:
    meta = {
        "v": 1,
        "camera": CAM,
        "keys": {"displays": [{"chart": 7, "visible": True}], "hidden": ["wf:8"], "later": 1},
    }
    got = views.remap_meta(meta, {7: 70}.get)
    assert got["keys"] == {"displays": [{"chart": 70, "visible": True}], "hidden": []}


def test_apply_under_50ms(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    put(c, "hidden", [f"set:{set_id}:collars"])
    v = c.post(f"{API}/views", json={"camera": CAM}).json()
    c.post(f"{API}/views/{v['id']}/apply")
    t0 = time.perf_counter()
    for _ in range(5):
        assert c.post(f"{API}/views/{v['id']}/apply").status_code == 200
    assert (time.perf_counter() - t0) / 5 < 0.05
