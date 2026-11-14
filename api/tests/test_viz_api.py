import math

import pytest
from sqlalchemy.orm import Session

from app.models import Assay, Collar, Component, Survey
from tests.helpers import new_set


def _project(client) -> int:
    return new_set(client, "Viz")


@pytest.fixture
def seeded(db_client, db_engine):
    """V: vertical hole with assays; I: inclined hole (survey east, -60); X: collar without coordinates."""
    pid = _project(db_client)
    with Session(db_engine) as s:
        s.add_all([
            Collar(set_id=pid, hole_id="V", x=1000.0, y=2000.0, z=500.0, td=40.0, azimuth=0.0, dip=-90.0),
            Collar(set_id=pid, hole_id="I", x=1100.0, y=2000.0, z=500.0, td=40.0, azimuth=90.0, dip=-60.0),
            Collar(set_id=pid, hole_id="X", x=None, y=None, z=None, td=10.0),
        ])
        s.add_all([
            Survey(set_id=pid, hole_id="I", depth=0.0, azimuth=90.0, dip=-60.0),
            Survey(set_id=pid, hole_id="I", depth=40.0, azimuth=90.0, dip=-60.0),
        ])
        for i, value in enumerate([0.01, 0.1, 1.0, 10.0]):
            s.add(Assay(set_id=pid, hole_id="V", from_m=10.0 * i, to_m=10.0 * (i + 1), sample_id=f"V-{i}",
                        analytes={"AU": value, "CU": None if i == 0 else 0.5}))
        s.add(Assay(set_id=pid, hole_id="I", from_m=0.0, to_m=10.0, sample_id="I-0", analytes={"AU": 2.0}))
        s.add(Assay(set_id=pid, hole_id="X", from_m=0.0, to_m=10.0, sample_id="X-0", analytes={"AU": 3.0}))
        s.add_all([Component(set_id=pid, name="AU", unit="g/t"), Component(set_id=pid, name="CU", unit="%")])
        s.commit()
    return db_client, pid


def test_trajectories_paths_extent_and_skipped(seeded):
    client, pid = seeded

    body = client.get(f"/api/sets/{pid}/trajectories", params={"step": 10}).json()

    assert body["skipped"] == ["X"]
    holes = {h["hole_id"]: h for h in body["holes"]}
    assert set(holes) == {"I", "V"}
    v = holes["V"]["path"]
    assert v[0] == [1000.0, 2000.0, 500.0, 0.0]
    assert v[-1] == [1000.0, 2000.0, 460.0, 40.0]  # vertical: z drops 40 m
    assert [p[3] for p in v] == [0.0, 10.0, 20.0, 30.0, 40.0]
    i_end = holes["I"]["path"][-1]
    assert i_end[0] == pytest.approx(1100 + 40 * math.cos(math.radians(60)), abs=0.01)
    assert i_end[2] == pytest.approx(500 - 40 * math.sin(math.radians(60)), abs=0.01)
    assert body["extent"]["min"] == [1000.0, 2000.0, 460.0]  # the vertical hole reaches the lowest point
    assert body["extent"]["max"][0] == pytest.approx(i_end[0], abs=0.01)
    assert body["extent"]["max"][2] == 500.0


def test_trajectories_step_controls_density(seeded):
    client, pid = seeded

    coarse = client.get(f"/api/sets/{pid}/trajectories", params={"step": 100}).json()
    fine = client.get(f"/api/sets/{pid}/trajectories", params={"step": 2}).json()

    n = lambda body: len(next(h for h in body["holes"] if h["hole_id"] == "V")["path"])  # noqa: E731
    assert n(coarse) == 2 and n(fine) == 21
    assert client.get(f"/api/sets/{pid}/trajectories", params={"step": 0.1}).status_code == 422


def test_intervals_positions_values_and_stats(seeded):
    client, pid = seeded

    body = client.get(f"/api/sets/{pid}/intervals", params={"component": "AU"}).json()

    assert body["unit"] == "g/t" and set(body["holes"]) == {"V", "I"}  # X has no trajectory
    first = body["holes"]["V"][0]
    assert first == [0.0, 10.0, 0.01, 1000.0, 2000.0, 500.0, 1000.0, 2000.0, 490.0]
    assert body["holes"]["V"][3][8] == 460.0
    assert body["holes"]["I"][0][5:] == pytest.approx([500.0, 1100 + 10 * 0.5, 2000.0, 500 - 10 * math.sin(math.radians(60))], abs=0.01)
    assert body["stats"]["count"] == 5 and body["stats"]["min"] == 0.01 and body["stats"]["max"] == 10.0
    assert body["stats"]["p05"] < body["stats"]["p50"] < body["stats"]["p95"]


def test_intervals_skip_empty_values_and_reject_unknown_component(seeded):
    client, pid = seeded

    cu = client.get(f"/api/sets/{pid}/intervals", params={"component": "CU"}).json()

    assert [row[0] for row in cu["holes"]["V"]] == [10.0, 20.0, 30.0]  # the empty CU of the first interval is skipped
    assert client.get(f"/api/sets/{pid}/intervals", params={"component": "ZN"}).status_code == 404
    assert client.get(f"/api/sets/{pid}/intervals").status_code == 422


def test_hole_log_has_survey_intervals_and_components(seeded):
    client, pid = seeded

    log = client.get(f"/api/sets/{pid}/holes/I/log").json()

    assert log["td"] == 40.0 and log["truncated"] is False
    assert [c["name"] for c in log["components"]] == ["AU", "CU"]
    assert [s["depth"] for s in log["survey"]] == [0.0, 40.0]
    assert log["intervals"] == [{"from_m": 0.0, "to_m": 10.0, "sample_id": "I-0", "values": {"AU": 2.0}}]
    assert client.get(f"/api/sets/{pid}/holes/NOPE/log").status_code == 404


def test_empty_project_returns_empty_collections(db_client):
    pid = _project(db_client)

    body = db_client.get(f"/api/sets/{pid}/trajectories").json()

    assert body == {"holes": [], "skipped": [], "extent": None}
    assert db_client.get("/api/sets/9999/trajectories").status_code == 404
