"""Audit and statistics on a chosen sample table: the built-in assays or an assay table of its own."""

import numpy as np
import pytest
from sqlalchemy.orm import Session

from app.models import Assay, Collar, Component, IntervalRow, IntervalTable
from tests.helpers import new_set


@pytest.fixture
def two_tables(db_client, db_engine):
    """36 vertical holes: built-in assays with AU ≈ 1, and a table «Композиты» with AU ≈ 10 and a text field."""
    sid = new_set(db_client, "Tables")
    rng = np.random.default_rng(3)
    with Session(db_engine) as s:
        t = IntervalTable(set_id=sid, name="Композиты", kind="interval", row_count=0,
                          columns=[{"name": "AU", "kind": "number", "unit": "g/t"}, {"name": "ZONE", "kind": "text", "unit": ""}])
        s.add(t)
        s.flush()
        n = 0
        for k in range(36):
            hole = f"H{k:02d}"
            s.add(Collar(set_id=sid, hole_id=hole, x=100.0 * (k % 6), y=100.0 * (k // 6), z=100.0, td=20.0, azimuth=0.0, dip=-90.0))
            for i in range(2):
                s.add(Assay(set_id=sid, hole_id=hole, from_m=10.0 * i, to_m=10.0 * (i + 1), analytes={"AU": float(rng.lognormal(0, 0.2))}))
                s.add(IntervalRow(table_id=t.id, hole_id=hole, from_m=10.0 * i, to_m=10.0 * (i + 1),
                                  values={"AU": float(10 * rng.lognormal(0, 0.2)), "ZONE": "A"}))
                n += 1
        t.row_count = n
        s.add(Component(set_id=sid, name="AU", unit="g/t"))
        s.commit()
        return db_client, sid, str(t.id)


def test_tables_fields_and_histogram_follow_the_chosen_table(two_tables):
    client, sid, key = two_tables
    tables = client.get(f"/api/sets/{sid}/stats/tables").json()
    assert [t["name"] for t in tables["tables"]] == ["Пробы", "Композиты"] and tables["default"] == "assay"

    fields = client.get(f"/api/sets/{sid}/stats/fields", params={"table": key}).json()
    assert [(f["id"], f["unit"]) for f in fields] == [("comp:AU", "g/t"), ("length", "м")]  # the text field is not a number

    base = client.post(f"/api/sets/{sid}/stats/histogram", json={"field": "comp:AU"}).json()
    comp = client.post(f"/api/sets/{sid}/stats/histogram", json={"field": "comp:AU", "table": key}).json()
    assert base["stats"]["mean"] < 2 < 8 < comp["stats"]["mean"]

    dec = client.post(f"/api/sets/{sid}/stats/declustering", json={"component": "AU", "table": key})
    assert dec.status_code == 200, dec.text
    assert dec.json()["naive_mean"] > 8
    assert client.post(f"/api/sets/{sid}/stats/declustering", json={"component": "ZONE", "table": key}).status_code == 404
    assert client.get(f"/api/sets/{sid}/stats/fields", params={"table": "999999"}).status_code == 404


def test_audit_checks_the_chosen_table_and_says_which(two_tables, db_engine):
    client, sid, key = two_tables
    run = client.post(f"/api/sets/{sid}/audit", json={"table": key})
    assert run.status_code == 200, run.text
    assert run.json()["config"]["table"] == key
    default = client.post(f"/api/sets/{sid}/audit").json()
    assert default["config"]["table"] == "assay"


def test_without_built_in_assays_the_first_table_is_the_default(db_client, db_engine):
    sid = new_set(db_client, "Only tables")
    with Session(db_engine) as s:
        t = IntervalTable(set_id=sid, name="Опробование", kind="interval", row_count=1,
                          columns=[{"name": "AG", "kind": "number", "unit": "g/t"}])
        s.add(t)
        s.flush()
        s.add(Collar(set_id=sid, hole_id="H1", x=0.0, y=0.0, z=0.0, td=10.0, azimuth=0.0, dip=-90.0))
        s.add(IntervalRow(table_id=t.id, hole_id="H1", from_m=0.0, to_m=2.0, values={"AG": 3.5}))
        s.commit()
        key = str(t.id)
    assert client_default(db_client, sid) == key
    h = db_client.post(f"/api/sets/{sid}/stats/histogram", json={"field": "comp:AG"}).json()
    assert h["stats"]["mean"] == pytest.approx(3.5)
    assert db_client.post(f"/api/sets/{sid}/audit").json()["config"]["table"] == key


def client_default(client, sid):
    return client.get(f"/api/sets/{sid}/stats/tables").json()["default"]
