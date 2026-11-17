"""Style library and saved displays (ТЗ 7.16, ADR 0007 §3): checks, CRUD, versions, «Где используется»,
deleting a linked style keeps the look, .bfstyle.json between projects, .bfggis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bf_core.styles.merge import unlink
from tests.test_import_job import API, wait_job

FIXTURES = sorted((Path(__file__).parent / "data" / "styles").glob("*.json"))


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_unlink_rule_shared_with_the_interface(path: Path) -> None:
    case = json.loads(path.read_text(encoding="utf-8"))
    st = case["style"]
    assert unlink(case["settings"], st["uid"], st["level"], st["body"]) == case["expected"]
    assert unlink(case["expected"], st["uid"], st["level"], st["body"]) is None  # nothing left to unlink


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects/demo").status_code == 200
    set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    return pclient, set_id


LAYER = {"name": "Золото линия", "level": "layer", "data_kind": "number", "body": {"type": "line", "width": 40}}
CLASSES = {"kind": "classes", "classes": [{"color": "#00ff00"}, {"from": 1, "color": "#ff0000"}]}


def test_crud_checks_and_versions(demo) -> None:  # type: ignore[no-untyped-def]
    c, _ = demo
    r = c.post(f"{API}/styles", json={**LAYER, "default_for": ["AU*"]})
    assert r.status_code == 200, r.text
    s = r.json()
    assert len(s["uid"]) == 32 and s["version"] == 1 and s["default_for"] == ["AU*"]
    assert c.post(f"{API}/styles", json={**LAYER, "name": " золото ЛИНИЯ "}).json()["code"] == "name_taken"
    bad = {
        "name": "Плохая",
        "level": "palette",
        "body": {"kind": "classes", "classes": [{"from": 2, "color": "#00ff00"}, {"from": 1, "color": "#ff0000"}]},
    }
    r = c.post(f"{API}/styles", json=bad)
    assert r.status_code == 422 and "возрастать" in r.json()["message"]
    r = c.post(f"{API}/styles", json={"name": "Цвет", "level": "palette", "body": {"catColors": {"GR": "red"}}})
    assert r.status_code == 422 and "#rrggbb" in r.json()["message"]
    r = c.patch(f"{API}/styles/{s['id']}", json={"body": {"type": "steps"}, "expected_version": 1})
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["body"] == {"type": "steps"}
    r = c.patch(f"{API}/styles/{s['id']}", json={"name": "Другое", "expected_version": 1})
    assert r.status_code == 409 and r.json()["style"]["version"] == 2
    assert [x["name"] for x in c.get(f"{API}/styles", params={"level": "layer"}).json()["styles"]] == ["Золото линия"]
    assert c.get(f"{API}/styles", params={"level": "palette"}).json()["styles"] == []


def test_charts_usage_and_delete_keeps_the_copy(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    layer = c.post(f"{API}/styles", json=LAYER).json()
    palette = c.post(f"{API}/styles", json={"name": "Градации AU", "level": "palette", "body": CLASSES}).json()
    ref = {"uid": layer["uid"], "name": layer["name"], "linked": True}
    pref = {"uid": palette["uid"], "name": palette["name"], "linked": True}
    settings = {
        "v": 1,
        "tracks": [
            {"id": "a", "table": "Пробы", "field": "AU", "type": "bars", "style": ref},
            {"id": "b", "table": "Пробы", "field": "CU", "type": "bars", "palette": pref},
        ],
    }
    r = c.post(f"{API}/charts", json={"set_id": set_id, "name": "Золото", "settings": settings})
    assert r.status_code == 200, r.text
    chart = r.json()
    assert chart["set_id"] == set_id and chart["kind"] == "striplog" and chart["version"] == 1
    assert c.post(f"{API}/charts", json={"set_id": set_id, "name": "золото", "settings": {}}).status_code == 409
    too_many = {"tracks": [{"id": str(i)} for i in range(33)]}
    assert c.post(f"{API}/charts", json={"set_id": set_id, "name": "Много", "settings": too_many}).status_code == 422
    assert [x["name"] for x in c.get(f"{API}/charts", params={"set_id": set_id}).json()["charts"]] == ["Золото"]

    use = c.get(f"{API}/styles/{layer['id']}/usage").json()["usage"]
    assert use == [{"chart_id": chart["id"], "name": "Золото", "set_id": set_id, "linked": True, "tracks": ["AU"]}]

    r = c.delete(f"{API}/styles/{layer['id']}")
    assert r.status_code == 200 and r.json()["charts"] == [chart["id"]]
    got = c.get(f"{API}/charts/{chart['id']}").json()
    assert got["version"] == 2
    a, b = got["settings"]["tracks"]
    assert a["type"] == "line" and a["width"] == 40 and "style" not in a  # the look of the deleted style stays
    assert b["palette"] == pref  # another style is not touched
    assert c.get(f"{API}/styles/{layer['id']}/usage").status_code == 404

    r = c.put(f"{API}/charts/{chart['id']}", json={"settings": settings, "expected_version": 1})
    assert r.status_code == 409 and r.json()["chart"]["version"] == 2
    assert c.put(f"{API}/charts/{chart['id']}", json={"name": "Au", "expected_version": 2}).json()["version"] == 3
    assert c.delete(f"{API}/charts/{chart['id']}").status_code == 200
    assert c.get(f"{API}/charts").json()["charts"] == []


def test_bfstyle_file_between_projects_and_bfggis(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    layer = c.post(f"{API}/styles", json={**LAYER, "default_for": ["AU*"]}).json()
    c.post(f"{API}/styles", json={"name": "Градации AU", "level": "palette", "body": CLASSES})
    settings = {"v": 1, "tracks": [{"id": "a", "field": "AU", "style": {"uid": layer["uid"], "linked": True}}]}
    c.post(f"{API}/charts", json={"set_id": set_id, "name": "Золото", "settings": settings})
    file = c.post(f"{API}/styles/export", json={"ids": None}).json()["file"]
    assert file["format"] == "bedrockforge-styles" and len(file["styles"]) == 2

    # .bfggis: styles and the display come along, the link still points to the style
    r = c.post(f"{API}/projects/export", json={"path": str(tmp_path / "st"), "password": None}).json()
    job = wait_job(c, r["source"], r["id"])
    r = c.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(c, r["source"], r["id"])
    assert job["status"] == "done", job
    c.post(f"{API}/projects/{job['result']['uid']}/open")
    assert {s["uid"] for s in c.get(f"{API}/styles").json()["styles"]} == {s["uid"] for s in file["styles"]}
    charts = c.get(f"{API}/charts").json()["charts"]
    assert len(charts) == 1 and charts[0]["settings"] == settings
    assert c.get(
        f"{API}/styles/{c.get(f'{API}/styles', params={'level': 'layer'}).json()['styles'][0]['id']}/usage"
    ).json()["usage"]

    # another (empty) project: the file gives the same styles
    c.post(f"{API}/projects", json={"name": "Другой"})
    preview = c.post(f"{API}/styles/import", params={"preview": True}, json={"file": file}).json()
    assert preview == {"styles": 2, "conflicts": []}
    r = c.post(f"{API}/styles/import", json={"file": file}).json()
    assert r["created"] == 2
    got = {s["name"]: s for s in r["styles"]}
    assert got["Золото линия"]["body"] == LAYER["body"] and got["Золото линия"]["default_for"] == ["AU*"]
    assert got["Золото линия"]["uid"] == layer["uid"]
    # again: a clash per name — skip, replace, keep both
    preview = c.post(f"{API}/styles/import", params={"preview": True}, json={"file": file}).json()
    assert sorted(preview["conflicts"]) == ["layer:Золото линия", "palette:Градации AU"]
    r = c.post(
        f"{API}/styles/import",
        json={"file": file, "conflicts": {"layer:Золото линия": "keep_both"}, "default": "skip"},
    ).json()
    assert r["created"] == 1 and r["skipped"] == 1 and r["renamed"][0]["to"] == "Золото линия (2)"
    assert len({s["uid"] for s in r["styles"]}) == 3
    bad = c.post(f"{API}/styles/import", json={"file": {"format": "x"}})
    assert bad.status_code == 422 and bad.json()["code"] == "bad_style_file"
