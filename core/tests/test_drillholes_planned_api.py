"""Проектные скважины, маршруты и проект (подшаг 19.37; ТЗ 7.35 Д-34 ч. 2; e19-api.md §10): `/planned_holes` — the
rows, the events, the refusals (404 / 409 / 422 / 423), `evaluate`, `compare`, `export`; the object is not a hole of a
set; the graph (`object:planned_hole`, the fingerprint is the version); `.bfggis`; the imports without numba."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core import lineage
from bf_core.archive import importer
from bf_core.db import dialect
from bf_core.db.schema import dh_holes, objects
from bf_core.drillholes import planned as pl
from bf_core.drillholes import planned_remap as pr
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as ws
from tests import test_vario_store as vs
from tests.meshutil import cube
from tests.test_drillholes_planned import BASE, URL
from tests.test_model_store import _roles  # noqa: F401 - the autouse fixture: the roles of the files

vc = vs.vc  # the core with an open project and the set of `test_vario_store`: A-1 (0, 0), B-2 (100, 0), C-3 (0, 100)
API = "/api/v1"
NODE = "object:planned_hole"
DOWN: dict[str, Any] = {"collar": [0.0, 0.0, 100.0], "azimuth": 0.0, "dip": -90.0, "length": 20.0}  # on A-1


def make(c: Any, name: str | None = "ПС-1", **over: Any) -> dict[str, Any]:
    r = c.post(URL, json={**({} if name is None else {"name": name}), **BASE, **over})
    assert r.status_code == 200, r.text
    return r.json()


def refused(r: Any, status: int, code: str, param: str | None = None) -> dict[str, Any]:
    assert r.status_code == status and r.json()["code"] == code, r.text
    assert r.json()["message"] and r.json()["hint"], r.text
    if param is not None:
        assert r.json()["param"] == param, r.text
    return r.json()  # type: ignore[no-any-return]


def cube_over_a1(c: Any) -> int:
    """A cube z = 85…105 round the collar of A-1: its top 15 m of the hole are inside."""
    mesh = cube(2, 20.0, (-10.0, -10.0, 85.0), seed=None)
    with dialect.write_tx(c.project.engine) as conn:
        return ws.put_wireframe(conn, BlobStore(c.project.folder), mesh[0], mesh[1], name="Куб")


# --- the rows -----------------------------------------------------------------------------------------------------


def test_the_rows_make_read_change_and_delete_with_events_and_versions(vc) -> None:  # type: ignore[no-untyped-def]
    made = make(vc, set_id=vc.set_id)
    assert set(made) >= {"id", "name", "version", "meta", "trajectory", "target_depth"}
    assert (made["name"], made["version"], made["target_depth"]) == ("ПС-1", 1, None)
    assert made["meta"] == pl.parse({**BASE, "set_id": vc.set_id}).meta() and made["meta"]["v"] == 1
    path = made["trajectory"]
    assert len(path["depth"]) == 251 and path["depth"][:2] == [0.0, 1.0] and path["depth"][-1] == 250.0
    assert [path[k][0] for k in "xyz"] == [100.0, 200.0, 50.0]  # the path starts at the collar
    assert vc.events[-1] == ("implicit.changed", {"kind": "planned_hole", "id": made["id"]})
    with dialect.read_tx(vc.project.engine) as conn:
        row = conn.execute(sa.select(objects).where(objects.c.id == made["id"])).one()
    assert (row.kind, row.state, row.parent_id) == ("planned_hole", "ready", None) and len(row.extent) == 6
    assert row.extent[2] < 50.0 < row.extent[5] + 1e-9 and row.extent[5] == 50.0  # the box of the path: it goes down
    same = vc.get(f"{URL}/{made['id']}").json()
    assert same == made
    listed = vc.get(URL).json()["planned_holes"]
    assert [(h["id"], h["version"]) for h in listed] == [(made["id"], 1)] and "trajectory" not in listed[0]
    with_path = vc.get(URL, params={"trajectory": "true"}).json()["planned_holes"]
    assert with_path[0]["trajectory"] == path

    second = make(vc, "ПС-2", azimuth=10.0)
    change = {"azimuth": 2.0, "dip": 1.0}
    r = vc.put(f"{URL}/{made['id']}", json={"expected_version": 1, "length": 300.0, "change": change})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["version"] == 2 and got["meta"]["length"] == 300.0 and got["meta"]["azimuth"] == 37.5
    assert len(got["trajectory"]["depth"]) == 301 and got["meta"]["change"] == {"azimuth": 2.0, "dip": 1.0}
    assert vc.events[-1] == ("implicit.changed", {"kind": "planned_hole", "id": made["id"]})
    aim = {"expected_version": 2, "name": "Новое имя", "target": [100.0, 200.0, -50.0]}
    r = vc.put(f"{URL}/{made['id']}", json=aim)
    assert r.status_code == 200 and r.json()["meta"]["change"] is None and r.json()["meta"]["dip"] == -90.0  # target
    assert r.json()["meta"]["length"] == 300.0 and r.json()["target_depth"] == 100.0 and r.json()["name"] == "Новое имя"

    body = refused(vc.put(f"{URL}/{made['id']}", json={"expected_version": 1, "length": 10.0}), 409, "version_conflict")
    assert body["version"] == 3
    refused(vc.put(f"{URL}/{made['id']}", json={"length": 10.0}), 422, "bad_param", "expected_version")
    refused(vc.put(f"{URL}/{made['id']}", json={"expected_version": 3, "name": "ПС-2"}), 409, "name_taken", "name")
    refused(vc.put(f"{URL}/{made['id']}", json={"expected_version": 3, "length": 1.0}), 422, "target_beyond_length",
            "length")  # fmt: skip
    refused(vc.put(f"{URL}/{made['id']}", json={"expected_version": 3, "set_id": 99999}), 404, "set_not_found")
    refused(vc.put(f"{URL}/424242", json={"expected_version": 1}), 404, "planned_hole_not_found")
    refused(vc.get(f"{URL}/424242"), 404, "planned_hole_not_found")
    refused(vc.delete(f"{URL}/424242"), 404, "planned_hole_not_found")
    refused(vc.post(URL, json={"name": "пс-2", **BASE}), 409, "name_taken", "name")  # the key ignores case
    first, third = make(vc, None), make(vc, None)
    assert (first["name"], third["name"]) == ("Проектная скважина", "Проектная скважина (2)")
    refused(vc.post(URL, json=[1, 2]), 422, "bad_request")
    refused(vc.post(URL, json={**BASE, "azimuth": 360.0}), 422, "bad_param", "azimuth")
    refused(vc.post(URL, json={**BASE, "set_id": 99999}), 404, "set_not_found")
    assert len(vc.get(URL).json()["planned_holes"]) == 4

    assert vc.delete(f"{URL}/{second['id']}").json() == {"deleted": second["id"]}
    assert vc.events[-1] == ("implicit.changed", {"kind": "planned_hole", "id": second["id"], "deleted": True})
    refused(vc.get(f"{URL}/{second['id']}"), 404, "planned_hole_not_found")
    assert [h["name"] for h in vc.get(URL).json()["planned_holes"]] == ["Новое имя", "Проектная скважина",
                                                                        "Проектная скважина (2)"]  # fmt: skip


def test_td34_3_through_the_route_and_the_object_is_not_a_hole_of_a_set(vc) -> None:  # type: ignore[no-untyped-def]
    with dialect.read_tx(vc.project.engine) as conn:
        before = conn.execute(sa.select(sa.func.count()).select_from(dh_holes)).scalar()
    sets_before = vc.get(f"{API}/drillholes/sets").json()
    target = [123.456, -78.9, -210.5]
    made = make(vc, "Цель", collar=[10.0, 20.0, 30.0], azimuth=None, dip=None, length=None, target=target)
    depth = made["target_depth"]
    assert made["meta"]["target"] == target and made["meta"]["change"] is None and made["meta"]["length"] == depth
    end = [made["trajectory"][k][-1] for k in "xyz"]
    assert max(abs(a - b) for a, b in zip(end, target, strict=True)) <= 1e-6  # the target is the end of the path
    assert made["trajectory"]["depth"][-1] == depth
    assert 0.0 <= made["meta"]["azimuth"] < 360.0 and -90.0 <= made["meta"]["dip"] <= 90.0  # solved, stored
    longer = vc.put(f"{URL}/{made['id']}", json={"expected_version": 1, "length": depth + 20.0}).json()
    i = longer["trajectory"]["depth"].index(round(depth)) if depth == round(depth) else None
    assert i is None or abs(longer["trajectory"]["x"][i] - target[0]) <= 1e-6
    # the set is as it was: no hole, no row, no table (the statistics and the estimation read the sets only)
    with dialect.read_tx(vc.project.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(dh_holes)).scalar() == before
    assert vc.get(f"{API}/drillholes/sets").json() == sets_before


def test_a_project_for_viewing_only_refuses_the_writes_and_still_reads(vc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    made = make(vc, set_id=vc.set_id)
    vc.project.read_only = True  # opened in another window (ТЗ 5.7)
    try:
        refused(vc.post(URL, json={"name": "Ещё", **BASE}), 423, "project_read_only")
        refused(vc.put(f"{URL}/{made['id']}", json={"expected_version": 1, "length": 10.0}), 423, "project_read_only")
        refused(vc.delete(f"{URL}/{made['id']}"), 423, "project_read_only")
        assert vc.get(f"{URL}/{made['id']}").json() == made
        assert vc.get(URL).status_code == 200
        r = vc.post(f"{URL}/{made['id']}/evaluate", json={"set_id": vc.set_id})
        assert r.status_code == 200 and len(r.json()["nearest"]) == 3, r.text
        r = vc.post(f"{URL}/{made['id']}/export", json={"format": "csv", "path": str(tmp_path)})
        assert r.status_code == 200 and (tmp_path / "survey.csv").is_file(), r.text
    finally:
        vc.project.read_only = False
    assert vc.get(f"{URL}/{made['id']}").json()["version"] == 1  # nothing was written


# --- evaluate, compare, export ------------------------------------------------------------------------------------


def test_evaluate_gives_the_forecast_and_the_nearest_holes_at_the_target(vc) -> None:  # type: ignore[no-untyped-def]
    ball = cube_over_a1(vc)
    made = make(vc, "Над A-1", set_id=vc.set_id, **DOWN)
    r = vc.post(f"{URL}/{made['id']}/evaluate", json={"mesh_ids": [ball]})
    assert r.status_code == 200, r.text
    out = r.json()
    cuts = [(i["from"], i["to"], i["code"]) for i in out["intervals"]]
    assert len(cuts) == 2 and cuts[0][2] == "Куб" and cuts[1][2] is None
    assert abs(cuts[0][1] - 15.0) <= 1e-9 and cuts[0][0] == 0.0 and cuts[1][1] == 20.0  # the top 15 m, then the gap
    assert [n["hole"] for n in out["nearest"]] == ["A-1", "B-2", "C-3"]  # the set of the hole: the end of the hole
    first = out["nearest"][0]
    assert first["distance"] == pytest.approx(0.0, abs=1e-9) and first["depth"] == pytest.approx(20.0)
    assert [round(n["distance"], 6) for n in out["nearest"][1:]] == [100.0, 100.0]
    assert out["target"]["point"] == pytest.approx([0.0, 0.0, 80.0], abs=1e-9) and out["target"]["depth"] == 20.0
    assert out["set_id"] == vc.set_id
    assert set(first) == {"hole", "hole_id", "distance", "depth"}
    # a point of the request; no bodies: no forecast; no set and no bodies: nothing to check
    near = vc.post(f"{URL}/{made['id']}/evaluate", json={"point": [30.0, 0.0, 90.0]}).json()
    assert near["intervals"] is None and near["target"]["depth"] is None
    first_near = near["nearest"][0]
    assert (first_near["hole"], first_near["distance"], first_near["depth"]) == ("A-1", 30.0, 10.0)
    bare = make(vc, "Без набора", **DOWN)
    refused(vc.post(f"{URL}/{bare['id']}/evaluate", json={}), 422, "bad_param", "model_id")
    assert vc.post(f"{URL}/{bare['id']}/evaluate", json={"mesh_ids": [ball]}).json()["nearest"] is None
    # through a target: the nearest holes are looked for at the target
    aim = make(vc, "Цель 2", collar=[0.0, 0.0, 120.0], azimuth=None, dip=None, length=None, target=[10.0, 0.0, 90.0],
               set_id=vc.set_id)  # fmt: skip
    got = vc.post(f"{URL}/{aim['id']}/evaluate", json={}).json()
    assert got["target"]["point"] == [10.0, 0.0, 90.0] and got["target"]["depth"] == pytest.approx(aim["target_depth"])
    assert got["nearest"][0]["hole"] == "A-1" and got["nearest"][0]["distance"] == pytest.approx(10.0)
    refused(vc.post(f"{URL}/{made['id']}/evaluate", json={"model_id": 424242}), 404, "model_not_found")
    refused(vc.post(f"{URL}/{made['id']}/evaluate", json={"mesh_ids": [424242]}), 422, "wireframe_not_found")
    refused(vc.post(f"{URL}/{made['id']}/evaluate", json={"set_id": 99999}), 404, "set_not_found")
    refused(vc.post(f"{URL}/{made['id']}/evaluate", json={"set_id": "x"}), 422, "bad_param", "set_id")
    refused(vc.post(f"{URL}/{made['id']}/evaluate", json={"set_id": vc.set_id, "point": [1, 2]}), 422, "bad_param",
            "point")  # fmt: skip
    refused(vc.post(f"{URL}/424242/evaluate", json={"set_id": vc.set_id}), 404, "planned_hole_not_found")


def test_compare_gives_the_deviation_of_a_hole_of_the_set_every_metre(vc) -> None:  # type: ignore[no-untyped-def]
    same = make(vc, "Как A-1", set_id=vc.set_id, **DOWN)
    r = vc.post(f"{URL}/{same['id']}/compare", json={"hole": "A-1"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["max"] == 0.0 and out["hole"] == "A-1" and out["set_id"] == vc.set_id and len(out["rows"]) == 21
    assert out["rows"][0] == {"depth": 0.0, "dx": 0.0, "dy": 0.0, "dz": 0.0, "d": 0.0}
    skew = make(vc, "Косая", collar=[2.0, 0.0, 100.0], azimuth=90.0, dip=-90.0, length=20.0, set_id=vc.set_id)
    got = vc.post(f"{URL}/{skew['id']}/compare", json={"hole_id": out["hole_id"], "set_id": vc.set_id}).json()
    assert got["max"] == pytest.approx(2.0) and got["rows"][3]["dx"] == pytest.approx(-2.0)  # fact − plan
    refused(vc.post(f"{URL}/{same['id']}/compare", json={"hole": "Z-9"}), 404, "hole_not_found")
    refused(vc.post(f"{URL}/{same['id']}/compare", json={}), 422, "bad_param", "hole")
    refused(vc.post(f"{URL}/{same['id']}/compare", json={"hole": "A-1", "set_id": 99999}), 404, "set_not_found")
    none = make(vc, "Без набора", **DOWN)
    refused(vc.post(f"{URL}/{none['id']}/compare", json={"hole": "A-1"}), 404, "set_not_found")


def test_export_writes_the_files_and_refuses_what_is_wrong(vc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    made = make(vc, "Экспорт", change={"azimuth": 2.0, "dip": 1.0})
    r = vc.post(f"{URL}/{made['id']}/export", json={"format": "csv", "path": str(tmp_path)})
    assert r.status_code == 200 and r.json()["files"] == [str(tmp_path / "collar.csv"), str(tmp_path / "survey.csv")]
    assert r.json()["points"] == 26 and "Экспорт" in (tmp_path / "survey.csv").read_text(encoding="utf-8-sig")
    refused(vc.post(f"{URL}/{made['id']}/export", json={"format": "csv", "path": str(tmp_path)}), 409, "files_exist")
    r = vc.post(f"{URL}/{made['id']}/export", json={"format": "dxf", "path": str(tmp_path / "ПС")})
    assert r.status_code == 200 and (tmp_path / "ПС.dxf").is_file() and r.json()["points"] == 251
    refused(vc.post(f"{URL}/{made['id']}/export", json={"format": "obj", "path": str(tmp_path)}), 422, "bad_format")
    refused(vc.post(f"{URL}/{made['id']}/export", json={"format": "csv", "path": str(tmp_path / "нет")}), 404,
            "folder_not_found")  # fmt: skip
    refused(vc.post(f"{URL}/{made['id']}/export", json={"format": "csv"}), 422, "path_not_absolute")


# --- the graph, .bfggis, the imports -------------------------------------------------------------------------------


def test_the_node_is_a_source_that_changes_with_its_version(vc) -> None:  # type: ignore[no-untyped-def]
    from bf_core.lineage import providers

    assert NODE in providers.registered() and providers.label_of(NODE) == "проектная скважина"
    made = make(vc)
    node = lineage.Node(NODE, made["id"])
    with dialect.read_tx(vc.project.engine) as conn:
        assert lineage.fingerprint(conn, node) == "1"
        assert lineage.status(conn, [node])[node].state == "current"
    assert vc.put(f"{URL}/{made['id']}", json={"expected_version": 1, "length": 260.0}).status_code == 200
    with dialect.read_tx(vc.project.engine) as conn:
        assert lineage.fingerprint(conn, node) == "2"
    vc.delete(f"{URL}/{made['id']}")
    with dialect.read_tx(vc.project.engine) as conn:
        assert lineage.fingerprint(conn, node) is None  # gone


def test_remap_renumbers_the_set_and_marks_the_one_the_archive_lacks() -> None:
    maps = {7: 70}
    assert pr.remap_meta({"set_id": 7, "length": 5.0}, maps) == {"set_id": 70, "length": 5.0}
    assert pr.remap_meta({"set_id": None}, maps) == {"set_id": None}
    assert pr.remap_meta({"set_id": 8}, maps) == {"set_id": pr.DEAD_ID}
    assert pr.remap_meta({"set_id": "x"}, maps) == {"set_id": pr.DEAD_ID} and pr.remap_meta({}, maps) == {}


def test_bfggis_keeps_the_planned_holes_and_renumbers_their_set(tmp_path: Path, vc) -> None:  # type: ignore[no-untyped-def]
    from tests.test_import_job import wait_job

    assert hasattr(importer, "remap_planned"), "importer.py: line of the orchestrator (remap_planned)"
    a = make(vc, "С набором", set_id=vc.set_id, change={"azimuth": 3.0, "dip": -1.0})
    aimed = {"collar": [0.0, 0.0, 0.0], "azimuth": None, "dip": None, "length": None, "target": [30.0, 40.0, -50.0]}
    b = make(vc, "Через цель", **aimed)
    r = vc.post(API + "/projects/export", json={"path": str(tmp_path / "Проектные")}).json()
    job = wait_job(vc, r["source"], r["id"])
    assert job["status"] == "done", job
    r = vc.post(API + "/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(vc, r["source"], r["id"])
    assert job["status"] == "done", job
    objs = {int(k): int(v) for k, v in job["result"]["id_maps"]["objects"].items()}
    assert vc.post(f"{API}/projects/{job['result']['uid']}/open").status_code == 200
    opened = projects_of(vc.core).require_current()
    assert opened.uid != vc.project.uid
    for old in (a, b):
        new = vc.get(f"{URL}/{objs[old['id']]}")
        assert new.status_code == 200, new.text
        got = new.json()
        assert (got["name"], got["version"], got["trajectory"]) == (old["name"], old["version"], old["trajectory"])
        want_set = None if old["meta"]["set_id"] is None else objs[vc.set_id]
        assert got["meta"] == {**old["meta"], "set_id": want_set}
    out = vc.post(f"{URL}/{objs[a['id']]}/evaluate", json={}).json()  # the new set is the one it names
    assert out["set_id"] == objs[vc.set_id] and len(out["nearest"]) == 3


def test_the_modules_do_not_load_numba_with_their_import() -> None:
    """The API process includes the router and registers the provider: numba comes with the first path."""
    code = (
        "import sys, bf_core.drillholes.planned, bf_core.drillholes.planned_eval, bf_core.drillholes.planned_export, "
        "bf_core.drillholes.planned_store, bf_core.drillholes.planned_remap, bf_core.drillholes.routes_planned, "
        "bf_core.drillholes.router, bf_core.lineage.adapt_planned\n"
        "bad = [m for m in ('numba', 'manifold3d') if m in sys.modules]\n"
        "raise SystemExit(3 if bad else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-500:]
