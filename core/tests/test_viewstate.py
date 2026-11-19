"""Project view settings (ТЗ 7.0.10, ADR 0007 §2): normalization, limits, versions, `.bfggis` renumbering."""

from __future__ import annotations

from pathlib import Path

import pytest

from bf_core.archive.view_remap import remap_setting
from bf_core.viewstate.keys import BadValue, drop_missing, ids_in, map_ids, normalize
from tests.test_import_job import API, wait_job


def test_lenient_read_clamps_and_drops() -> None:
    assert normalize("scene", {"vexag": 4, "grid": "да", "wireframe_opacity": 9}, strict=False) == {
        "perspective": False,
        "grid": True,
        "vexag": 1,
        "wireframe_opacity": 0.7,
    }
    assert normalize("section", {"line": None, "clip": True}, strict=False) == {"line": None, "clip": False}
    hidden = normalize("hidden", ["set:3", "мусор", "set:3", "wf:7", *[f"ln:{i}" for i in range(2500)]], strict=False)
    assert len(hidden) == 2000 and hidden[-1] == "ln:2499" and "мусор" not in hidden
    styles = normalize("collar_styles", {str(i): {"symbol": "circle"} for i in range(1, 260)}, strict=False)
    assert len(styles) == 200 and "1" not in styles and "259" in styles  # the oldest go first


def test_strict_write_rejects() -> None:
    for key, value in (
        ("scene", {"vexag": 4}),
        ("scene", {"wireframe_opacity": 0.05}),
        ("hidden", ["wireframe:12"]),
        ("section", {"line": {"x0": 1, "y0": 2, "az": 30, "towards": -1}, "clip": True}),
        ("nope", {}),
    ):
        with pytest.raises(BadValue):
            normalize(key, value, strict=True)
    line = normalize("section", {"line": {"x0": 1, "y0": 2, "az": 390, "length": None}, "clip": True}, strict=True)
    assert line["line"]["az"] == 30 and line["line"]["towards"] == 25 and line["clip"] is True


def test_map_ids() -> None:
    m = {3: 30, 7: 70}.get
    assert map_ids("hidden", ["set:3:collars", "wf:7", "ln:9"], m) == ["set:30:collars", "wf:70"]
    assert map_ids("displays", [{"chart": 7, "visible": False}, {"chart": 8, "visible": True}], m) == [
        {"chart": 70, "visible": False}
    ]
    assert map_ids("trace_styles", {"3": {"w": 2}, "4": {}}, m) == {"30": {"w": 2}}
    assert map_ids("columns", {"3:assay": ["AU"]}, m) == {"30:assay": ["AU"]}
    assert map_ids("active", {"set": 4, "hole": "DH-1", "table": "assay"}, m) == {
        "set": None,
        "hole": None,
        "table": None,
    }


def test_remap_setting_rows() -> None:
    objs = {3: 30}
    assert remap_setting({"scope": "code_links", "key": "3:litho:t0", "value": "Породы"}, objs, True) == {
        "scope": "code_links",
        "key": "30:litho:t0",
        "value": "Породы",
    }
    assert remap_setting({"scope": "code_links", "key": "4:litho:t0", "value": "x"}, objs, True) is None
    assert remap_setting({"scope": "codes", "key": "Породы", "value": {"items": []}}, objs, False) is not None
    assert remap_setting({"scope": "view", "key": "hidden", "value": ["set:3"]}, objs, False) is None
    assert remap_setting({"scope": "view", "key": "hidden", "value": ["set:3"]}, objs, True)["value"] == ["set:30"]  # type: ignore[index]
    assert remap_setting({"scope": "view", "key": "camera", "value": {}}, objs, True) is None


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects/demo").status_code == 200
    set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    return pclient, set_id


def test_api_versions_conflict_and_deleted_objects(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    assert c.get(f"{API}/view").json()["values"] == {}
    r = c.put(f"{API}/view/scene", json={"value": {"vexag": 3}, "expected_version": 0})
    assert r.status_code == 200 and r.json() == {
        "value": {"perspective": False, "grid": True, "vexag": 3, "wireframe_opacity": 0.7},
        "version": 1,
    }
    r = c.put(f"{API}/view/scene", json={"value": {"vexag": 5}, "expected_version": 0})
    assert r.status_code == 409 and r.json()["code"] == "version_conflict"
    assert r.json()["version"] == 1 and r.json()["value"]["vexag"] == 3
    r = c.put(f"{API}/view/scene", json={"value": {"vexag": 4}, "expected_version": 1})
    assert r.status_code == 422 and r.json()["code"] == "bad_view_setting" and "1, 2, 3 или 5" in r.json()["message"]
    assert c.put(f"{API}/view/whatever", json={"value": 1, "expected_version": 0}).status_code == 422
    late = c.put(f"{API}/view/scene", json={"value": {}, "expected_version": 1, "project": "0" * 32})
    assert late.status_code == 410 and late.json()["code"] == "project_changed"  # a write of another project

    hidden = [f"set:{set_id}:collars", "wf:999999"]
    assert c.put(f"{API}/view/hidden", json={"value": hidden, "expected_version": 0}).status_code == 200
    active = {"set": set_id, "hole": "DH-001", "table": "assay"}
    assert c.put(f"{API}/view/active", json={"value": active, "expected_version": 0}).status_code == 200
    got = c.get(f"{API}/view").json()["values"]
    assert got["hidden"]["value"] == [f"set:{set_id}:collars"]  # the missing object is forgotten on read
    assert got["active"]["value"] == active and got["scene"]["version"] == 1


def test_bfggis_keeps_code_lists_and_renumbers_view(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    c.put(f"{API}/view/hidden", json={"value": [f"set:{set_id}:paths"], "expected_version": 0})
    c.put(f"{API}/view/trace_styles", json={"value": {str(set_id): {"width": 3}}, "expected_version": 0})
    r = c.put(f"{API}/drillholes/codes", json={"name": "Породы", "items": [{"code": "GR", "title": "Гранит"}]})
    assert r.status_code == 200, r.text

    def roundtrip(include_view: bool) -> dict:  # type: ignore[type-arg]
        out = tmp_path / f"view-{include_view}"
        r = c.post(f"{API}/projects/export", json={"path": str(out), "password": None, "include_view": include_view})
        job = wait_job(c, r.json()["source"], r.json()["id"])
        r = c.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
        job = wait_job(c, r["source"], r["id"])
        assert job["status"] == "done", job
        c.post(f"{API}/projects/{job['result']['uid']}/open")
        return job["result"]["id_maps"]["objects"]

    maps = roundtrip(include_view=True)
    new_set = maps[str(set_id)]
    got = c.get(f"{API}/view").json()["values"]
    assert got["hidden"]["value"] == [f"set:{new_set}:paths"]
    assert got["trace_styles"]["value"] == {str(new_set): {"width": 3}}
    assert "Породы" in {x["name"] for x in c.get(f"{API}/drillholes/codes").json()["lists"]}

    roundtrip(include_view=False)  # without view settings the code lists still come along
    assert c.get(f"{API}/view").json()["values"] == {}
    assert "Породы" in {x["name"] for x in c.get(f"{API}/drillholes/codes").json()["lists"]}


def test_hide_all_shared_fixtures_and_ids() -> None:
    """«Очистить окно» (ТЗ 7.35 Д-04, ADR 0008): the same cases as the interface (ui/test/hideAll.test.ts)."""
    import json

    cases = json.loads((Path(__file__).parent / "data" / "view" / "hide_all.json").read_text(encoding="utf-8"))
    for case in cases["cases"]:
        assert normalize("hide_all", case["in"], strict=False) == case["out"], case
    with pytest.raises(BadValue):
        normalize("hide_all", {"on": True, "shown": ["junk"]}, strict=True)
    many = {"on": True, "shown": [f"set:{i}" for i in range(1, 2501)]}
    shown = normalize("hide_all", many, strict=True)["shown"]
    assert len(shown) == 2000 and shown[-1] == "set:2500"  # the newest are kept
    m = {3: 30}.get
    assert map_ids("hide_all", {"on": True, "shown": ["set:3", "wf:7"]}, m) == {"on": True, "shown": ["set:30"]}
    assert list(ids_in("hide_all", {"on": True, "shown": ["set:3:paths", "wf:7"]})) == [3, 7]
    assert drop_missing("hide_all", {"on": True, "shown": ["set:3", "wf:7"]}, lambda i: i == 3) == {
        "on": True,
        "shown": ["set:3"],
    }


def test_hide_all_api_and_bfggis(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    r = c.put(
        f"{API}/view/hide_all",
        json={"value": {"on": True, "shown": [f"set:{set_id}", "wf:999999"]}, "expected_version": 0},
    )
    assert r.status_code == 200, r.text
    got = c.get(f"{API}/view").json()["values"]["hide_all"]["value"]
    assert got == {"on": True, "shown": [f"set:{set_id}"]}  # the missing object is forgotten on read
    out = tmp_path / "hide-all"
    r = c.post(f"{API}/projects/export", json={"path": str(out), "password": None, "include_view": True})
    job = wait_job(c, r.json()["source"], r.json()["id"])
    r = c.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(c, r["source"], r["id"])
    assert job["status"] == "done", job
    c.post(f"{API}/projects/{job['result']['uid']}/open")
    new_set = job["result"]["id_maps"]["objects"][str(set_id)]
    got = c.get(f"{API}/view").json()["values"]["hide_all"]["value"]
    assert got == {"on": True, "shown": [f"set:{new_set}"]}


def test_cloud_styles_keep_a_style_per_layer_and_follow_the_ids() -> None:
    from bf_core.viewstate.keys import drop_missing, map_ids

    value = normalize("cloud_styles", {"12": {"color": "class", "hidden": [7]}, "x": {}}, strict=False)
    assert value == {"12": {"color": "class", "hidden": [7]}}
    assert drop_missing("cloud_styles", value, lambda i: i != 12) == {}
    renumbered = map_ids("cloud_styles", value, lambda i: 40 if i == 12 else None)
    assert renumbered == {"40": {"color": "class", "hidden": [7]}}
