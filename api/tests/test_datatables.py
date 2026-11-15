"""Tables of a drillhole set: pages with sorting, statistics of every field, hand corrections, the edit log and undo."""

import pytest
from sqlalchemy.orm import Session

from app.models import Assay, Collar, Component, IntervalRow, IntervalTable, Survey


@pytest.fixture
def dataset(db_client, db_engine):
    pid = db_client.post("/api/projects", json={"name": "Таблицы"}).json()["id"]
    sid = db_client.post(f"/api/projects/{pid}/sets", json={"name": "Скважины"}).json()["id"]
    with Session(db_engine) as s:
        s.add_all([
            Collar(set_id=sid, hole_id="H-1", x=10.0, y=20.0, z=300.0, td=20.0, status="done", attributes={"RIG": "A1"}),
            Collar(set_id=sid, hole_id="H-2", x=11.0, y=21.0, z=301.0, td=12.0, status="done", attributes={}),
            Survey(set_id=sid, hole_id="H-1", depth=0.0, azimuth=90.0, dip=-60.0),
            Assay(set_id=sid, hole_id="H-1", from_m=0.0, to_m=1.0, analytes={"AU": 0.5}),
            Assay(set_id=sid, hole_id="H-1", from_m=1.0, to_m=2.0, analytes={"AU": 4.5}),
            Assay(set_id=sid, hole_id="H-2", from_m=0.0, to_m=1.0, analytes={}),
            Component(set_id=sid, name="AU", unit="g/t"),
        ])
        t = IntervalTable(set_id=sid, name="Литология", columns=[{"name": "LITH", "kind": "text", "unit": ""}, {"name": "RQD", "kind": "number", "unit": "%"}], row_count=3)
        ev = IntervalTable(set_id=sid, name="События", kind="event", columns=[{"name": "TYPE", "kind": "text", "unit": ""}], row_count=1)
        s.add_all([t, ev])
        s.flush()
        s.add_all([
            IntervalRow(table_id=t.id, hole_id="H-1", from_m=0.0, to_m=5.0, values={"LITH": "Туф", "RQD": 40}),
            IntervalRow(table_id=t.id, hole_id="H-1", from_m=5.0, to_m=9.0, values={"LITH": "Андезит", "RQD": 90}),
            IntervalRow(table_id=t.id, hole_id="H-2", from_m=0.0, to_m=4.0, values={"LITH": "Туф", "RQD": "n/a"}),
            IntervalRow(table_id=ev.id, hole_id="H-1", from_m=7.0, to_m=7.0, values={"TYPE": "Вода"}),
        ])
        s.commit()
        return db_client, sid, str(t.id), str(ev.id)


def test_tables_of_a_set_and_pages_with_sorting(dataset):
    client, sid, lith, ev = dataset
    tabs = client.get(f"/api/sets/{sid}/datatables").json()
    assert [(t["key"], t["rows"]) for t in tabs] == [("collar", 2), ("survey", 1), ("assay", 3), (lith, 3), (ev, 1)]

    collars = client.get(f"/api/sets/{sid}/datatables/collar").json()
    assert [c["name"] for c in collars["columns"]][:2] == ["hole_id", "x"] and collars["columns"][-1]["name"] == "RIG"
    assert collars["rows"][0]["RIG"] == "A1"
    assays = client.get(f"/api/sets/{sid}/datatables/assay", params={"sort": "AU", "desc": True}).json()
    assert [r["AU"] for r in assays["rows"]] == [4.5, 0.5, None]  # empty values last
    rows = client.get(f"/api/sets/{sid}/datatables/{lith}", params={"sort": "RQD", "hole_id": "H-1"}).json()
    assert rows["total"] == 2 and [r["RQD"] for r in rows["rows"]] == [40, 90]
    events = client.get(f"/api/sets/{sid}/datatables/{ev}").json()
    assert [c["name"] for c in events["columns"]] == ["hole_id", "depth", "TYPE"] and events["rows"][0]["depth"] == 7.0
    assert client.get(f"/api/sets/{sid}/datatables/assay", params={"sort": "NOPE"}).status_code == 422
    assert client.get(f"/api/sets/{sid}/datatables/999999").status_code == 404


def test_statistics_of_every_field(dataset):
    client, sid, lith, _ = dataset
    st = {f["name"]: f for f in client.get(f"/api/sets/{sid}/datatables/{lith}/stats").json()["fields"]}

    assert st["RQD"]["count"] == 2 and st["RQD"]["empty"] == 1  # "n/a" is not a number
    assert (st["RQD"]["min"], st["RQD"]["max"], st["RQD"]["mean"], st["RQD"]["median"]) == (40, 90, 65, 65)
    assert st["LITH"]["unique"] == 2 and st["LITH"]["top"][0] == ["Туф", 2]
    assert st["hole_id"]["unique"] == 2
    au = {f["name"]: f for f in client.get(f"/api/sets/{sid}/datatables/assay/stats", params={"hole_id": "H-1"}).json()["fields"]}["AU"]
    assert au["count"] == 2 and au["sum"] == 5.0


def test_edit_insert_delete_and_undo_with_the_log(dataset):
    client, sid, lith, ev = dataset
    base = f"/api/sets/{sid}/datatables"
    row = client.get(f"{base}/{lith}", params={"hole_id": "H-2"}).json()["rows"][0]

    changed = client.patch(f"{base}/{lith}/rows/{row['_id']}", json={"values": {"RQD": "55,5", "LITH": " Дацит "}}).json()
    assert changed["row"]["RQD"] == 55.5 and changed["row"]["LITH"] == "Дацит" and set(changed["changed"]) == {"RQD", "LITH"}
    assert client.patch(f"{base}/{lith}/rows/{row['_id']}", json={"values": {"RQD": "abc"}}).status_code == 422
    assert client.patch(f"{base}/{lith}/rows/{row['_id']}", json={"values": {"to": -1}}).status_code == 422  # to < from
    assert client.patch(f"{base}/{lith}/rows/{row['_id']}", json={"values": {"hole_id": ""}}).status_code == 422
    assert client.patch(f"{base}/{lith}/rows/{row['_id']}", json={"values": {"BOGUS": 1}}).status_code == 422
    cleared = client.patch(f"{base}/{lith}/rows/{row['_id']}", json={"values": {"LITH": ""}}).json()["row"]
    assert cleared["LITH"] is None

    new = client.post(f"{base}/{ev}/rows", json={"values": {"hole_id": "H-2", "depth": "3,5", "TYPE": "Разлом"}}).json()["row"]
    assert new["depth"] == 3.5
    assert client.post(f"{base}/{ev}/rows", json={"values": {"hole_id": "H-2"}}).status_code == 422  # no depth
    assert [t["rows"] for t in client.get(f"/api/sets/{sid}/datatables").json() if t["key"] == ev] == [2]
    collar = client.get(f"{base}/collar").json()["rows"][1]
    assert client.patch(f"{base}/collar/rows/{collar['_id']}", json={"values": {"hole_id": "H-1"}}).status_code == 409
    assert client.delete(f"{base}/{lith}/rows/{row['_id']}").status_code == 204
    assert client.delete(f"{base}/{lith}/rows/{row['_id']}").status_code == 404

    log = client.get(f"/api/sets/{sid}/edits").json()
    assert [e["action"] for e in log] == ["delete", "insert", "update", "update"]
    assert log[3]["changes"]["RQD"] == ["n/a", 55.5]

    # undo the deletion: the row is back (as a new row) with the values it had
    client.post(f"/api/edits/{log[0]['id']}/undo")
    back = client.get(f"{base}/{lith}", params={"hole_id": "H-2"}).json()["rows"]
    assert len(back) == 1 and back[0]["RQD"] == 55.5 and back[0]["LITH"] is None
    assert client.post(f"/api/edits/{log[0]['id']}/undo").status_code == 409
    # undo the insert: the event is gone again
    client.post(f"/api/edits/{log[1]['id']}/undo")
    assert client.get(f"{base}/{ev}").json()["total"] == 1
    # undo the first change of the row (now a new row: the old id is gone) -> not found
    assert client.post(f"/api/edits/{log[3]['id']}/undo").status_code == 404


def test_a_correction_makes_the_audit_stale(dataset):
    client, sid, _, _ = dataset
    client.post(f"/api/sets/{sid}/audit", json={})
    assert client.get(f"/api/sets/{sid}/audit").json()["stale"] is False
    row = client.get(f"/api/sets/{sid}/datatables/survey").json()["rows"][0]

    client.patch(f"/api/sets/{sid}/datatables/survey/rows/{row['_id']}", json={"values": {"dip": -61}})

    assert client.get(f"/api/sets/{sid}/audit").json()["stale"] is True
