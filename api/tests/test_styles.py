"""The styles library of a project: palettes, layer styles, display templates; their file exchange and migration."""

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from tests.helpers import new_set
from tests.test_migration_store import _config, scratch_db  # noqa: F401  (a fixture)

CLASSES = [{"from": None, "color": "#9aa3b5", "label": "пусто"}, {"from": 0.3, "color": "#5fbf6a"},
           {"from": 1, "color": "#e05d6f", "hidden": True}]
PALETTE = {"kind": "classes", "classes": CLASSES}


def _project(client, name="P") -> int:
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _put(client, project, name="Au борта", level="palette", body=None, **more):
    return client.put(f"/api/projects/{project}/styles", json={"name": name, "level": level, "body": PALETTE if body is None else body, **more})


def _names(client, project, level=None):
    url = f"/api/projects/{project}/styles" + (f"?level={level}" if level else "")
    return [(s["level"], s["name"]) for s in client.get(url).json()]


def test_styles_save_list_patch_delete(db_client):
    project = _project(db_client)
    saved = _put(db_client, project, default_for=["AU", "AU_*"])
    assert saved.status_code == 200, saved.text
    s = saved.json()
    assert set(s) == {"id", "name", "level", "data_kind", "body", "default_for", "created_at", "updated_at"}
    assert s["level"] == "palette" and s["data_kind"] == "number" and s["default_for"] == ["AU", "AU_*"]
    assert s["body"] == PALETTE  # stored as it came, unknown keys of the classes and of the body too

    layer = _put(db_client, project, name="Литология", level="layer", data_kind="text",
                 body={"type": "color", "catColors": {"Туф": "#aabbcc"}, "anything": {"deep": [1, 2]}})
    assert layer.status_code == 200 and layer.json()["body"]["anything"] == {"deep": [1, 2]}
    assert _put(db_client, project, name="Шаблон", level="display", data_kind="mixed", body={"tracks": []}).status_code == 200
    assert _names(db_client, project) == [("display", "Шаблон"), ("layer", "Литология"), ("palette", "Au борта")]
    assert _names(db_client, project, "layer") == [("layer", "Литология")]
    assert db_client.get(f"/api/projects/{project}/styles?level=nope").status_code == 422
    assert db_client.get("/api/projects/999999/styles").status_code == 404

    patched = db_client.patch(f"/api/styles/{s['id']}", json={"name": " Au 2 ", "default_for": [], "data_kind": "mixed",
                                                             "body": {"kind": "classes", "classes": CLASSES[:2]}})
    assert patched.status_code == 200, patched.text
    p = patched.json()
    assert (p["id"], p["name"], p["default_for"], p["data_kind"], len(p["body"]["classes"])) == (s["id"], "Au 2", [], "mixed", 2)
    # Only what is sent changes.
    assert db_client.patch(f"/api/styles/{s['id']}", json={"default_for": ["CU"]}).json()["body"] == p["body"]

    assert db_client.delete(f"/api/styles/{s['id']}").status_code == 204
    assert db_client.delete(f"/api/styles/{s['id']}").status_code == 404
    assert db_client.patch(f"/api/styles/{s['id']}", json={"name": "x"}).status_code == 404
    assert _names(db_client, project, "palette") == []
    # The styles go with their project.
    assert db_client.delete(f"/api/projects/{project}").status_code in (200, 204)


def test_a_style_is_unique_by_level_and_name(db_client):
    project = _project(db_client)
    first = _put(db_client, project).json()
    again = _put(db_client, project, body={"kind": "classes", "classes": CLASSES[:2]}, data_kind="mixed").json()
    assert again["id"] == first["id"] and len(again["body"]["classes"]) == 2 and again["data_kind"] == "mixed"
    # The same name on another level, or in another project, is another style.
    layer = _put(db_client, project, level="layer", body={}).json()
    assert layer["id"] != first["id"]
    assert _put(db_client, _project(db_client, "Q")).json()["id"] not in (first["id"], layer["id"])
    assert _names(db_client, project) == [("layer", "Au борта"), ("palette", "Au борта")]

    other = _put(db_client, project, name="Cu").json()
    clash = db_client.patch(f"/api/styles/{other['id']}", json={"name": "Au борта"})
    assert clash.status_code == 409 and "уже есть" in clash.json()["detail"]
    assert db_client.patch(f"/api/styles/{other['id']}", json={"name": "Cu"}).status_code == 200  # its own name
    assert db_client.patch(f"/api/styles/{layer['id']}", json={"name": "Cu"}).status_code == 200  # free on its level


@pytest.mark.parametrize("body", [
    {"classes": [{"from": 2, "color": "#000000"}, {"from": 1, "color": "#ffffff"}]},  # descending
    {"classes": [{"from": 1, "color": "#000000"}, {"from": 1, "color": "#ffffff"}]},  # not strictly ascending
    {"classes": [{"from": 1, "color": "#000000"}, {"from": None, "color": "#ffffff"}]},  # null not first
    {"classes": [{"from": 1, "color": "red"}]},
    {"classes": [{"from": 1}]},
    {"classes": [{"from": "1", "color": "#000000"}]},
    {"classes": [{"from": True, "color": "#000000"}]},
    {"classes": [{"from": 1, "color": "#000000", "label": "x" * 65}]},
    {"classes": [{"from": 1, "color": "#000000", "hidden": "yes"}]},
    {"classes": [{"from": i, "color": "#000000"} for i in range(41)]},
    {"classes": {"from": 1}},
    {"classes": ["x"]},
    {"catColors": {"Туф": "#12345"}},
    {"catColors": ["#123456"]},
    {"tracks": [{"field": "AU"}, {"classes": [{"from": 2, "color": "#000000"}, {"from": 1, "color": "#ffffff"}]}]},
    {"tracks": [{"catColors": {"a": "blue"}}]},
    {"blob": "x" * (256 * 1024)},
])
def test_a_wrong_body_is_refused(db_client, body):
    project = _project(db_client)
    r = _put(db_client, project, body=body)
    assert r.status_code == 422, r.text
    assert isinstance(r.json()["detail"], str)
    good = _put(db_client, project, name="ok").json()
    assert db_client.patch(f"/api/styles/{good['id']}", json={"body": body}).status_code == 422
    file = {"format": "bedrockforge-styles", "version": 1, "styles": [{"name": "Плохой", "level": "layer", "body": body}]}
    imported = db_client.post(f"/api/projects/{project}/styles/import", json={"file": file})
    assert imported.status_code == 422 and "«Плохой»" in imported.json()["detail"]
    assert _names(db_client, project) == [("palette", "ok")]


def test_what_a_body_may_hold(db_client):
    project = _project(db_client)
    forty = [{"from": i, "color": "#ABCDEF"} for i in range(40)]
    ok = {"classes": forty, "catColors": {}, "tracks": [{"classes": CLASSES, "catColors": {"a": "#000000"}}, {"type": "line"}, "?"]}
    assert _put(db_client, project, level="display", body=ok).status_code == 200
    assert _put(db_client, project, name="пусто", body={}).status_code == 200
    for bad in ([], "x", None):
        assert db_client.put(f"/api/projects/{project}/styles", json={"name": "n", "level": "palette", "body": bad}).status_code == 422
    assert db_client.put(f"/api/projects/{project}/styles", json={"name": "n", "level": "palette"}).status_code == 422
    assert _put(db_client, project, name="  ").status_code == 422
    assert _put(db_client, project, name="n", level="other").status_code == 422
    assert _put(db_client, project, name="n", data_kind="other").status_code == 422
    assert _put(db_client, project, name="n", default_for=["x"] * 51).status_code == 422
    assert _put(db_client, project, name="n", default_for=["x" * 65]).status_code == 422
    assert _put(db_client, project, name="n", default_for=[1]).status_code == 422
    assert _put(db_client, project, name="n", default_for=["x" * 64] * 50).status_code == 200


def test_usage_lists_the_displays_that_refer_to_a_style(db_client):
    sid = new_set(db_client)
    project = db_client.get("/api/projects").json()[0]["id"]
    pal = _put(db_client, project).json()["id"]
    layer = _put(db_client, project, name="Слой", level="layer", body={}).json()["id"]
    template = _put(db_client, project, name="Шаблон", level="display", body={}).json()["id"]
    unused = _put(db_client, project, name="Ничей").json()["id"]

    def chart(name, settings, kind="downhole", set_id=sid):
        r = db_client.post(f"/api/sets/{set_id}/charts", json={"kind": kind, "name": name, "settings": settings})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    a = chart("A", {"template": {"id": template, "name": "Шаблон", "linked": True}, "tracks": [
        {"field": "AU", "palette": {"id": pal, "name": "Au борта", "linked": False}},
        {"field": "CU", "palette": {"id": pal, "name": "Au борта", "linked": True}, "style": {"id": layer, "name": "Слой", "linked": False}},
        {"field": "LITH"},
    ]})
    b = chart("B", {"template": {"id": template, "name": "Шаблон", "linked": False, "modified": True},
                    "tracks": [{"field": "AU", "style": {"id": pal, "linked": False}}, "junk", {"style": None}]})
    chart("no references", {"form": "grade"})
    chart("a strip log", {"tracks": [{"field": "AU", "palette": {"id": pal, "linked": True}}]}, kind="striplog")
    # A display of another project that happens to hold the same id.
    other_set = new_set(db_client, name="Другой")
    chart("foreign", {"template": {"id": template, "linked": True}}, set_id=other_set)

    assert db_client.get(f"/api/styles/{pal}/usage").json() == [
        {"chart_id": a, "name": "A", "set_id": sid, "linked": True, "tracks": ["AU", "CU"]},
        {"chart_id": b, "name": "B", "set_id": sid, "linked": False, "tracks": ["AU"]},
    ]
    assert db_client.get(f"/api/styles/{layer}/usage").json() == [
        {"chart_id": a, "name": "A", "set_id": sid, "linked": False, "tracks": ["CU"]}]
    assert db_client.get(f"/api/styles/{template}/usage").json() == [
        {"chart_id": a, "name": "A", "set_id": sid, "linked": True, "tracks": []},
        {"chart_id": b, "name": "B", "set_id": sid, "linked": False, "tracks": []},
    ]
    assert db_client.get(f"/api/styles/{unused}/usage").json() == []
    assert db_client.get("/api/styles/999999/usage").status_code == 404


def _fill(client, project):
    _put(client, project, default_for=["AU*"])
    _put(client, project, name="Литология", level="layer", data_kind="text", body={"type": "color", "catColors": {"Туф": "#aabbcc"}})
    _put(client, project, name="Шаблон", level="display", data_kind="mixed", body={"tracks": [{"field": "AU", "classes": CLASSES}]})


def _content(styles):
    return [{k: s[k] for k in ("name", "level", "data_kind", "body", "default_for")} for s in styles]


def test_export_then_import_into_another_project(db_client):
    source, target = _project(db_client, "Откуда"), _project(db_client, "Куда")
    _fill(db_client, source)
    r = db_client.get(f"/api/projects/{source}/styles/export")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="styles.bfstyle.json"'
    file = r.json()
    assert file["format"] == "bedrockforge-styles" and file["version"] == 1
    assert all(set(e) == {"name", "level", "data_kind", "body", "default_for"} for e in file["styles"])

    done = db_client.post(f"/api/projects/{target}/styles/import", json={"file": file})
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["created"], body["replaced"], body["skipped"], body["renamed"]) == (3, 0, 0, [])
    listed = db_client.get(f"/api/projects/{target}/styles").json()
    assert body["styles"] == listed
    assert _content(listed) == _content(db_client.get(f"/api/projects/{source}/styles").json())
    assert {s["id"] for s in listed}.isdisjoint(s["id"] for s in db_client.get(f"/api/projects/{source}/styles").json())

    # Only the chosen ones.
    ids = [s["id"] for s in listed if s["level"] != "display"]
    part = db_client.get(f"/api/projects/{target}/styles/export?ids={ids[0]},{ids[1]}").json()
    assert [(e["level"], e["name"]) for e in part["styles"]] == [("layer", "Литология"), ("palette", "Au борта")]
    assert db_client.get(f"/api/projects/{target}/styles/export?ids=x").status_code == 422
    foreign = db_client.get(f"/api/projects/{source}/styles").json()[0]["id"]
    assert db_client.get(f"/api/projects/{target}/styles/export?ids={foreign}").status_code == 404
    assert db_client.get(f"/api/projects/{_project(db_client, 'Пустой')}/styles/export").json()["styles"] == []


def test_import_conflicts_replace_keep_both_skip(db_client):
    project = _project(db_client)
    _fill(db_client, project)
    before = {(s["level"], s["name"]): s for s in db_client.get(f"/api/projects/{project}/styles").json()}
    file = db_client.get(f"/api/projects/{project}/styles/export").json()
    for e in file["styles"]:
        e["default_for"] = ["NEW"]
    file["styles"].append({"name": "Новый", "level": "palette", "body": {"gradient": "viridis"}})
    url = f"/api/projects/{project}/styles/import"

    # Without decisions the existing styles stay as they are.
    r = db_client.post(url, json={"file": file}).json()
    assert (r["created"], r["replaced"], r["skipped"], r["renamed"]) == (1, 0, 3, [])
    assert all(s["default_for"] != ["NEW"] for s in r["styles"])
    new = next(s for s in r["styles"] if s["name"] == "Новый")
    assert new["data_kind"] == "number" and new["default_for"] == []

    r = db_client.post(url, json={"file": file, "default": "skip", "conflicts": {
        "palette:Au борта": "replace", "layer:Литология": "keep_both", "display:Шаблон": "skip", "palette:Новый": "keep_both"}}).json()
    assert (r["created"], r["replaced"], r["skipped"]) == (2, 1, 1)
    assert r["renamed"] == [{"from": "Литология", "to": "Литология (2)", "level": "layer"},
                            {"from": "Новый", "to": "Новый (2)", "level": "palette"}]
    after = {(s["level"], s["name"]): s for s in r["styles"]}
    assert after["palette", "Au борта"]["id"] == before["palette", "Au борта"]["id"]
    assert after["palette", "Au борта"]["default_for"] == ["NEW"]
    assert after["layer", "Литология"]["default_for"] == [] and after["layer", "Литология (2)"]["default_for"] == ["NEW"]
    assert after["display", "Шаблон"]["default_for"] == []
    assert len(after) == 6

    # The next copy takes the first free number; "default" decides for the styles not named.
    r = db_client.post(url, json={"file": {**file, "styles": [file["styles"][1]]}, "default": "keep_both"}).json()
    assert r["renamed"] == [{"from": "Литология", "to": "Литология (3)", "level": "layer"}]
    r = db_client.post(url, json={"file": file, "default": "replace", "conflicts": {"palette:Новый": "skip"}}).json()
    assert (r["created"], r["replaced"], r["skipped"]) == (0, 3, 1)
    assert db_client.post(url, json={"file": file, "default": "merge"}).status_code == 422
    # Two entries of one file with the same name meet each other.
    twice = {**file, "styles": [{"name": "Дважды", "level": "layer", "body": {}}] * 2}
    r = db_client.post(url, json={"file": twice, "default": "keep_both"}).json()
    assert r["created"] == 2 and r["renamed"] == [{"from": "Дважды", "to": "Дважды (2)", "level": "layer"}]


def test_import_refuses_what_is_not_a_styles_file(db_client):
    project = _project(db_client)
    url = f"/api/projects/{project}/styles/import"
    good = {"name": "a", "level": "palette", "body": {}}
    r = db_client.post(url, json={"file": {"format": "bedrockforge-styles", "version": 2, "styles": [good]}})
    assert r.status_code == 422 and r.json()["detail"] == "Неизвестная версия файла стилей: 2"
    assert db_client.post(url, json={"file": {"format": "bedrockforge-styles", "styles": [good]}}).status_code == 422
    r = db_client.post(url, json={"file": {"format": "something-else", "version": 1, "styles": [good]}})
    assert r.status_code == 422 and "не файл стилей" in r.json()["detail"]
    for file in ([good], "x", None, {"format": "bedrockforge-styles", "version": 1},
                 {"format": "bedrockforge-styles", "version": 1, "styles": ["x"]}):
        assert db_client.post(url, json={"file": file}).status_code == 422
    # One wrong entry refuses the whole file.
    for wrong in ({"name": "b", "level": "nope", "body": {}}, {"name": "b", "level": "layer"}, {"level": "layer", "body": {}},
                  {"name": "b", "level": "layer", "body": {}, "data_kind": "?"}, {"name": "b", "level": "layer", "body": {}, "default_for": "AU"}):
        r = db_client.post(url, json={"file": {"format": "bedrockforge-styles", "version": 1, "styles": [good, wrong]}})
        assert r.status_code == 422, r.text
    assert db_client.get(f"/api/projects/{project}/styles").json() == []
    assert db_client.post("/api/projects/999999/styles/import", json={"file": {}}).status_code == 404


def test_unknown_keys_of_a_file_entry_survive_a_round_trip(db_client):
    source, target = _project(db_client, "A"), _project(db_client, "B")
    entry = {"name": "Au", "level": "palette", "data_kind": "number", "body": PALETTE, "default_for": ["AU"],
             "description": "борта 2026", "future": {"a": [1, 2]}, "id": 77, "created_at": "x"}
    file = {"format": "bedrockforge-styles", "version": 1, "styles": [entry]}
    r = db_client.post(f"/api/projects/{source}/styles/import", json={"file": file})
    assert r.status_code == 200, r.text
    assert "description" not in r.json()["styles"][0] and r.json()["styles"][0]["id"] != 77
    out = db_client.get(f"/api/projects/{source}/styles/export").json()
    expected = {k: v for k, v in entry.items() if k not in ("id", "created_at")}
    assert out["styles"] == [expected]
    # Editing the style keeps them; so does a second hop.
    sid = r.json()["styles"][0]["id"]
    db_client.patch(f"/api/styles/{sid}", json={"default_for": []})
    _put(db_client, source, name="Au", default_for=["CU"])
    out = db_client.get(f"/api/projects/{source}/styles/export").json()
    assert out["styles"] == [{**expected, "default_for": ["CU"]}]
    db_client.post(f"/api/projects/{target}/styles/import", json={"file": out})
    assert db_client.get(f"/api/projects/{target}/styles/export").json() == out
    # Replacing on import takes the extras of the file.
    out["styles"][0] = {k: v for k, v in out["styles"][0].items() if k != "future"}
    db_client.post(f"/api/projects/{target}/styles/import", json={"file": out, "default": "replace"})
    assert db_client.get(f"/api/projects/{target}/styles/export").json() == out


def test_migration_0010_turns_palettes_into_styles_and_back(scratch_db):  # noqa: F811
    config = _config(scratch_db)
    command.upgrade(config, "0009")
    engine = create_engine(scratch_db)
    classes = '[{"from": null, "color": "#9aa3b5", "label": "пусто"}, {"from": 0.3, "color": "#5fbf6a", "label": ""}]'
    with engine.begin() as c:
        c.execute(text("INSERT INTO project (id, name) VALUES (1, 'old'), (2, 'other')"))
        c.execute(text("INSERT INTO palette (id, project_id, name, classes, created_at) VALUES "
                       "(3, 1, 'Au', CAST(:c AS jsonb), '2026-01-02T03:04:05+00'), (7, 1, 'Cu', '[]'::jsonb, now()), "
                       "(8, 2, 'Au', CAST(:c AS jsonb), now())"), {"c": classes})

    command.upgrade(config, "head")
    with engine.begin() as c:
        tables = {r[0] for r in c.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"))}
        assert "style" in tables and "palette" not in tables
        rows = c.execute(text("SELECT id, project_id, name, level, data_kind, body, default_for, extra, "
                              "created_at = '2026-01-02T03:04:05+00' AS kept FROM style ORDER BY id")).mappings().all()
        assert [(r["id"], r["project_id"], r["name"], r["level"], r["data_kind"]) for r in rows] == [
            (3, 1, "Au", "palette", "number"), (7, 1, "Cu", "palette", "number"), (8, 2, "Au", "palette", "number")]
        assert rows[0]["body"] == {"kind": "classes", "classes": [{"from": None, "color": "#9aa3b5", "label": "пусто"},
                                                                 {"from": 0.3, "color": "#5fbf6a", "label": ""}]}
        assert rows[0]["kept"] and rows[0]["default_for"] == [] and rows[0]["extra"] == {} and rows[1]["body"]["classes"] == []
        # New styles get ids after the migrated ones.
        new = c.execute(text("INSERT INTO style (project_id, name, level, body) VALUES (1, 'Слой', 'layer', '{}'::jsonb) RETURNING id")).scalar()
        assert new == 9
        c.execute(text("INSERT INTO style (project_id, name, level, body) VALUES "
                       "(1, 'Градиент', 'palette', '{\"kind\": \"gradient\"}'::jsonb), "
                       "(2, 'Новая', 'palette', '{\"kind\": \"classes\", \"classes\": [{\"from\": 1, \"color\": \"#000000\"}]}'::jsonb)"))

    command.downgrade(config, "0009")
    with engine.begin() as c:
        tables = {r[0] for r in c.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"))}
        assert "palette" in tables and "style" not in tables
        rows = c.execute(text("SELECT id, project_id, name, classes FROM palette ORDER BY id")).all()
        assert [(r[0], r[1], r[2]) for r in rows] == [(3, 1, "Au", ), (7, 1, "Cu"), (8, 2, "Au"), (11, 2, "Новая")]
        assert rows[0][3][1] == {"from": 0.3, "color": "#5fbf6a", "label": ""} and rows[3][3] == [{"from": 1, "color": "#000000"}]
        assert c.execute(text("INSERT INTO palette (project_id, name) VALUES (1, 'next') RETURNING id")).scalar() == 12

    # And up again over what the downgrade left; an empty database migrates too.
    command.upgrade(config, "head")
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM style")).scalar() == 5
    engine.dispose()
