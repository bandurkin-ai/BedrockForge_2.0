"""Several interval tables per drillhole set and the downhole data of the log window."""

from tests.samples import assay_dat, collar_dat
from tests.test_commit import _import
from tests.helpers import new_set

LITH = (
    "BHID,FROM,TO,LITH,ALT,RQD\n"
    "H1,0,2.5,Андезит,Q,80\n"
    "H1,2.5,6,Туф,G,55,5\n"
    "H2,0,4,Андезит,,90\n"
).replace(",55,5", ",55.5")


def _lith(client, sid, name="Литология", data=LITH):
    return _import(client, sid, "lith.csv", data.encode("utf-8"), kind="interval", attributes=["LITH", "ALT", "RQD"],
                   table_name=name)


def test_interval_tables_import_list_replace_rename_delete(db_client):
    sid = new_set(db_client)
    _import(db_client, sid, "collar.DAT", collar_dat())
    _import(db_client, sid, "assay.DAT", assay_dat())
    r = _lith(db_client, sid)
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "interval" and r.json()["inserted"] == 3
    assert _lith(db_client, sid, "Изменения").status_code == 200  # a second table in the same set

    tables = db_client.get(f"/api/sets/{sid}/interval-tables").json()
    assert [t["name"] for t in tables] == ["Пробы", "Литология", "Изменения"]
    assert tables[0]["builtin"] and any(c["name"] == "AU" and c["kind"] == "number" for c in tables[0]["columns"])
    cols = {c["name"]: c["kind"] for c in tables[1]["columns"]}
    assert cols == {"LITH": "text", "ALT": "text", "RQD": "number"}

    # Importing under the same name replaces the rows of that table only.
    again = _lith(db_client, sid, data="BHID,FROM,TO,LITH,ALT,RQD\nH1,0,1,Дайка,Q,70\n")
    assert again.json()["replaced"] == 3 and again.json()["inserted"] == 1
    sets = db_client.get(f"/api/projects/{db_client.get('/api/projects').json()[0]['id']}/datasets").json()
    d = next(x for x in sets if x["id"] == sid)
    assert [(t["name"], t["row_count"]) for t in d["interval_tables"]] == [("Литология", 1), ("Изменения", 3)]
    assert d["counts"]["assay"] == 2  # the assays are untouched

    tid = tables[2]["id"]
    assert db_client.patch(f"/api/interval-tables/{tid}", json={"name": "Литология"}).status_code == 409
    assert db_client.patch(f"/api/interval-tables/{tid}", json={"name": "Изменения 2"}).json()["name"] == "Изменения 2"
    rows = db_client.get(f"/api/interval-tables/{tid}/rows").json()
    assert rows["total"] == 3 and rows["rows"][1] == {"hole_id": "H1", "from": 2.5, "to": 6.0, "LITH": "Туф", "ALT": "G", "RQD": 55.5}
    assert db_client.delete(f"/api/interval-tables/{tid}").status_code == 204
    assert [t["name"] for t in db_client.get(f"/api/sets/{sid}/interval-tables").json()] == ["Пробы", "Литология"]


def test_downhole_data_of_several_tables(db_client):
    sid = new_set(db_client)
    _import(db_client, sid, "collar.DAT", collar_dat())
    _lith(db_client, sid)
    tables = db_client.get(f"/api/sets/{sid}/interval-tables").json()
    key = tables[1]["key"]
    body = db_client.post(f"/api/sets/{sid}/downhole", json={"holes": ["H1", "H2", "NOPE"], "tables": [key]}).json()
    assert [t["name"] for t in body["tables"]] == ["Литология"]
    h1, h2, nope = body["holes"]
    assert [iv["values"]["LITH"] for iv in h1["tables"][key]] == ["Андезит", "Туф"]
    assert h2["tables"][key][0]["values"] == {"LITH": "Андезит", "RQD": 90.0}
    assert not nope["found"] and nope["tables"][key] == []
    assert h1["max_depth"] >= 6.0
    assert db_client.post(f"/api/sets/{sid}/downhole", json={"holes": []}).status_code == 422
    # The 3D view asks for every hole of the set, with only the fields it draws.
    allb = db_client.post(f"/api/sets/{sid}/downhole", json={"all": True, "tables": [key], "fields": {key: ["LITH"]}}).json()
    got = {h["hole_id"]: h for h in allb["holes"]}
    assert set(got) >= {"H1", "H2"} and all(h["found"] for h in allb["holes"])
    assert got["H2"]["tables"][key][0]["values"] == {"LITH": "Андезит"}
    # Saved downhole logs are charts of kind "striplog".
    c = db_client.post(f"/api/sets/{sid}/charts", json={"kind": "striplog", "name": "Колонка H1", "settings": {"holes": ["H1"]}})
    assert c.status_code == 201


EVENTS = "BHID,DEPTH,TYPE,NOTE\nH1,3.5,Вода,приток\nH1,7,Разлом,\nH2,1.2,Вода,\n"


def test_event_tables_have_one_depth_and_feed_the_downhole_data(db_client):
    sid = new_set(db_client)
    _import(db_client, sid, "collar.DAT", collar_dat())
    r = _import(db_client, sid, "events.csv", EVENTS.encode("utf-8"), kind="event", attributes=["TYPE", "NOTE"], table_name="События")
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "event" and r.json()["inserted"] == 3
    tables = db_client.get(f"/api/sets/{sid}/interval-tables").json()
    ev = next(t for t in tables if t["name"] == "События")
    assert ev["kind"] == "event" and tables[0]["kind"] == "interval"
    body = db_client.post(f"/api/sets/{sid}/downhole", json={"all": True, "tables": [ev["key"]]}).json()
    assert body["tables"][0]["kind"] == "event"
    h1 = next(h for h in body["holes"] if h["hole_id"] == "H1")
    assert [(iv["from"], iv["to"], iv["values"]["TYPE"]) for iv in h1["tables"][ev["key"]]] == [(3.5, 3.5, "Вода"), (7.0, 7.0, "Разлом")]
    project = db_client.get("/api/projects").json()[0]["id"]
    d = next(x for x in db_client.get(f"/api/projects/{project}/datasets").json() if x["id"] == sid)
    assert d["interval_tables"][0]["kind"] == "event"
    # displays of data along the holes are charts of kind "downhole"
    assert db_client.post(f"/api/sets/{sid}/charts", json={"kind": "downhole", "name": "AU", "settings": {"form": "grade"}}).status_code == 201


def test_palettes_of_a_project_save_replace_delete(db_client):
    project = db_client.get("/api/projects").json()[0]["id"] if db_client.get("/api/projects").json() else db_client.post("/api/projects", json={"name": "P"}).json()["id"]
    classes = [{"from": None, "color": "#9aa3b5", "label": "пусто"}, {"from": 0.3, "color": "#5fbf6a"}, {"from": 1, "color": "#e05d6f"}]
    saved = db_client.put(f"/api/projects/{project}/palettes", json={"name": "Au борта", "classes": classes})
    assert saved.status_code == 200, saved.text
    assert [c["from"] for c in saved.json()["classes"]] == [None, 0.3, 1.0]
    again = db_client.put(f"/api/projects/{project}/palettes", json={"name": "Au борта", "classes": classes[:2]})
    assert again.json()["id"] == saved.json()["id"] and len(again.json()["classes"]) == 2
    assert [p["name"] for p in db_client.get(f"/api/projects/{project}/palettes").json()] == ["Au борта"]
    bad = db_client.put(f"/api/projects/{project}/palettes", json={"name": "x", "classes": [{"from": 2, "color": "#000000"}, {"from": 1, "color": "#ffffff"}]})
    assert bad.status_code == 422
    assert db_client.delete(f"/api/palettes/{saved.json()['id']}").status_code == 204
    assert db_client.get(f"/api/projects/{project}/palettes").json() == []


def test_more_assay_files_get_tables_of_their_own(db_client):
    sid = new_set(db_client)
    _import(db_client, sid, "collar.DAT", collar_dat())
    assert _import(db_client, sid, "assay.DAT", assay_dat(), table_name="Пробы").status_code == 200
    r = _import(db_client, sid, "comp.DAT", assay_dat(), table_name="Композиты Au")
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "assay" and r.json()["inserted"] > 0
    tables = db_client.get(f"/api/sets/{sid}/interval-tables").json()
    assert [t["name"] for t in tables] == ["Пробы", "Композиты Au"]
    assert tables[0]["row_count"] == tables[1]["row_count"]  # the main assays are not replaced
    units = {c["name"]: c["unit"] for c in tables[1]["columns"]}
    assert units["AU"] == "g/t" and all(c["kind"] == "number" for c in tables[1]["columns"])
    # without a name (or «Пробы») an assay file is the set's main assay table, as before
    again = _import(db_client, sid, "assay.DAT", assay_dat())
    assert again.json()["replaced"] > 0
    assert [t["name"] for t in db_client.get(f"/api/sets/{sid}/interval-tables").json()] == ["Пробы", "Композиты Au"]
