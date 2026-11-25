"""Trends (подшаг 16.16; ТЗ 7.35 Д-15; ADR 0022 §10, решения 15, 20): ТД15-1 the angles and ratios ↔ the matrix A
(1e−12), ТД15-2 a trend of a model = the axes of the model (a copy), ТД15-3 «Где используется» and 409 `in_use`,
ТД15-4 the cycle `.bfggis` (the ids of `origin.model_id`, the consumers and the view key `ellipsoids` renumbered);
the refusals, 423 in a read-only project, the view key `ellipsoids`."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import object_refs, objects
from bf_core.geostat import remap, rotation
from bf_core.geostat import trend as tr
from bf_core.geostat import trend_store as ts
from bf_core.geostat.variogram import model as vm
from bf_core.projects.service import projects_of
from bf_core.viewstate import keys as vk
from tests import test_vario_store as vs
from tests.test_import_job import wait_job
from tests.test_vario_store import API, GS, calc, save

vc = vs.vc  # the core with an open project and the set of `test_vario_store`
_roles = vs._roles  # autouse: the roles `vario`, `nscore`

TR = API + "/geostat/trends"
TOL = 1e-12
CAMERA = {"position": [0.0, 0.0, 10.0], "target": [0.0, 0.0, 0.0], "up": [0.0, 1.0, 0.0], "zoom": 1.0,
          "ortho": True, "fov": 45.0}  # fmt: skip
TWO = {"type": "semivariogram", "c0": 0.1, "common_axes": False,
       "structures": [{"type": "spherical", "c": 0.5, "ang1": 30, "ang2": -10, "ang3": 5, "a1": 40, "a2": 30, "a3": 8},
                      {"type": "exponential", "c": 0.6, "ang1": 112.5, "ang2": -35, "ang3": -20, "a1": 250, "a2": 90,
                       "a3": 12.5}]}  # fmt: skip


@pytest.fixture()
def tc(vc):  # type: ignore[no-untyped-def]  # noqa: F811
    return vc


def consumer(engine: sa.Engine, trend_id: int, name: str = "Оценка AU") -> int:
    """An object of a later stage (the estimation wizard of Э18) that refers to the trend: `object_refs` role trend."""
    now = dialect.utcnow()
    with dialect.write_tx(engine) as conn:
        oid = int(conn.execute(sa.insert(objects).values(
            kind="estimation", name=name, name_key=dialect.name_key(name), parent_id=None, sort_key=0.0,
            state="ready", version=1, meta={}, created_at=now, updated_at=now)).inserted_primary_key[0])  # type: ignore[index]  # fmt: skip
        conn.execute(sa.insert(object_refs).values(src_id=oid, dst_id=trend_id, role=ts.ROLE))
    return oid


# --- ТД15-1 -----------------------------------------------------------------------------------------------------------


def test_td15_1_angles_and_ratios_to_the_matrix_and_back() -> None:
    rng = np.random.default_rng(20261122)
    for _ in range(500):
        a1, a2, a3 = rng.uniform(0, 360), rng.uniform(-89.9, 89.9), rng.uniform(-179.9, 180)
        r2, r3 = rng.uniform(0.01, 1.0), rng.uniform(0.001, 1.0)
        s = tr.shape(a1, a2, a3, [1, r2, r3])
        a = tr.matrix(s)
        r = rotation.rotation_matrix(a1, a2, a3)
        assert np.abs(a - np.diag([1.0, 1 / r2, 1 / r3]) @ r).max() <= TOL  # A = diag(1, a1/a2, a1/a3)·R (8.6.3)
        assert np.abs(a - rotation.anisotropy_matrix(a1, a2, a3, 1.0, r2, r3)).max() <= TOL
        back = tr.shape_of(a, a1)
        assert np.abs(np.array(back.ratios) - [1.0, r2, r3]).max() <= TOL
        assert np.abs(tr.matrix(back) - a).max() <= TOL  # the same trend (the same lines of the axes)
        d = rng.normal(size=3) * 100
        u = r @ d
        assert abs(np.linalg.norm(a @ d) - np.sqrt(u[0] ** 2 + (u[1] / r2) ** 2 + (u[2] / r3) ** 2)) <= TOL * 1e3
    zero = tr.matrix({"ang1": 0, "ang2": 0, "ang3": 0, "ratios": [1, 0.5, 0.25]})
    assert zero.tolist() == [[0.0, 1.0, 0.0], [-2.0, 0.0, 0.0], [0.0, 0.0, 4.0]]  # Т8.6-3: the axes N, W, up
    assert tr.matrix({"ang1": 0}).tolist() == rotation.rotation_matrix(0).tolist()  # ratios absent — 1 : 1 : 1


def test_td15_1_the_api_gives_the_matrix_of_the_core(tc) -> None:  # type: ignore[no-untyped-def]
    body = {"name": "Простирание СВ", "ang1": "42,5", "ang2": -15, "ang3": 7, "ratios": [1, "0,4", 0.1], "size": 300,
            "centre": [5_000_123.25, 7_200_456.5, 312.125]}  # fmt: skip
    r = tc.post(TR, json=body)
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    got = tc.get(f"{TR}/{tid}").json()
    assert (got["ang1"], got["ang2"], got["ang3"], got["ratios"]) == (42.5, -15.0, 7.0, [1.0, 0.4, 0.1])
    assert (got["size"], got["centre"], got["kind"], got["origin"]) == (300.0, body["centre"], "global",
                                                                        {"type": "manual"})  # fmt: skip
    assert got["matrix"] == tr.matrix(tr.shape(42.5, -15, 7, [1, 0.4, 0.1])).tolist()  # bit for bit
    with dialect.read_tx(tc.project.engine) as conn:
        assert ts.matrix_of(conn, tid).tolist() == got["matrix"]
    listed = tc.get(TR).json()["trends"]
    assert [t["id"] for t in listed] == [tid] and listed[0]["used"] is False and listed[0]["color"] == "#f46a1a"
    assert tc.events[-1] == ("geostat.changed", {"kind": "trend", "id": tid})


# --- ТД15-2 -----------------------------------------------------------------------------------------------------------


def test_td15_2_a_trend_of_a_model_is_a_copy_of_its_axes(tc) -> None:  # type: ignore[no-untyped-def]
    key, _ = calc(tc.project, tc.set_id, tc.a)
    mid = save(tc, key, model=TWO, name="Золото").json()["id"]
    r = tc.post(TR, json={"model_id": mid})
    assert r.status_code == 200, r.text
    tid, name = r.json()["id"], r.json()["name"]
    assert name == "Тренд · Золото"
    got = tc.get(f"{TR}/{tid}").json()
    big = TWO["structures"][1]  # the largest a1 by default
    assert got["origin"] == {"type": "model", "model_id": mid, "structure": 2}
    assert (got["ang1"], got["ang2"], got["ang3"], got["size"]) == (112.5, -35.0, -20.0, 250.0)
    assert got["ratios"] == [1.0, 90 / 250, 12.5 / 250]
    ani = rotation.anisotropy_matrix(112.5, -35, -20, 250, 90, 12.5)
    assert np.abs(np.array(got["matrix"]) - ani).max() <= TOL
    s = vm.Structure(big["type"], big["c"], big["a1"], big["a2"], big["a3"], big["ang1"], big["ang2"], big["ang3"])
    d = np.random.default_rng(7).normal(size=(50, 3)) * 200
    h_model = vm.reduced_distance(s, d) * big["a1"]  # the distance of the model along its axes, m
    h_trend = np.linalg.norm(d @ np.array(got["matrix"]).T, axis=1)
    assert np.abs(h_model - h_trend).max() <= TOL * 1e3
    first = tc.post(TR, json={"model_id": mid, "structure": 1, "size": 75}).json()
    one = tc.get(f"{TR}/{first['id']}").json()
    assert first["name"] == "Тренд · Золото (2)" and one["origin"]["structure"] == 1 and one["size"] == 75.0
    assert (one["ang1"], one["ang2"], one["ang3"], one["ratios"]) == (30.0, -10.0, 5.0, [1.0, 0.75, 0.2])
    # the model edited: the trend stays («копия»); «Обновить из модели» — PUT with model_id
    edited = {**TWO, "structures": [TWO["structures"][0], {**big, "ang1": 200.0, "a2": 125.0}]}
    assert tc.put(f"{GS}/models/{mid}", json={"model": edited, "expected_version": 1}).status_code == 200
    assert tc.get(f"{TR}/{tid}").json()["ang1"] == 112.5
    assert tc.put(f"{TR}/{tid}", json={"model_id": mid, "expected_version": 1}).status_code == 200
    again = tc.get(f"{TR}/{tid}").json()
    assert (again["ang1"], again["ratios"], again["name"]) == (200.0, [1.0, 0.5, 0.05], "Тренд · Золото")
    # the model deleted: the trend stays, its origin has no model
    assert tc.delete(f"{GS}/models/{mid}").status_code == 200
    gone = tc.get(f"{TR}/{tid}").json()
    assert gone["origin"] == {"type": "model", "model_id": None, "structure": 2} and gone["ang1"] == 200.0


def test_trend_refusals(tc) -> None:  # type: ignore[no-untyped-def]
    key, _ = calc(tc.project, tc.set_id, tc.a)
    mid = save(tc, key).json()["id"]
    cases = [
        ({"kind": "structural", "ang1": 0}, 422, "bad_param", "source"),  # Э19: allowed, but needs a source
        ({"kind": "plane", "ang1": 0}, 422, "bad_param", "kind"),
        ({}, 422, "bad_param", "ang1"),
        ({"ang1": 0, "ang2": 95}, 422, "bad_param", "ang2"),
        ({"ang1": 0, "ratios": [1, 0.5]}, 422, "bad_param", "ratios"),
        ({"ang1": 0, "ratios": [2, 0.5, 0.5]}, 422, "bad_param", "ratios.0"),
        ({"ang1": 0, "ratios": [1, 0, 0.5]}, 422, "bad_param", "ratios.1"),
        ({"ang1": 0, "ratios": [1, 0.5, "x"]}, 422, "bad_param", "ratios.2"),
        ({"ang1": 0, "size": -1}, 422, "bad_param", "size"),
        ({"ang1": 0, "centre": [1, 2]}, 422, "bad_param", "centre"),
        ({"ang1": 0, "centre": [1, 2, None]}, 422, "bad_param", "centre.2"),
        ({"ang1": 0, "color": "red"}, 422, "bad_param", "color"),
        ({"ang1": 0, "structure": 1}, 422, "bad_param", "structure"),
        ({"ang1": 0, "origin": {"type": "model"}}, 422, "bad_param", "origin"),
        ({"model_id": mid, "structure": 2}, 422, "bad_param", "structure"),
        ({"model_id": mid + 99}, 404, "model_not_found", None),
        ({"model_id": "1"}, 422, "bad_param", "model_id"),
    ]
    for body, status, code, param in cases:
        r = tc.post(TR, json=body)
        assert (r.status_code, r.json()["code"], r.json().get("param")) == (status, code, param), (body, r.text)
        assert r.json()["message"] and r.json()["hint"]
    assert tc.post(TR, json=[1]).json()["code"] == "bad_request"
    ok = tc.post(TR, json={"ang1": 10, "name": "Т", "origin": {"type": "ellipsoid"}}).json()
    assert tc.get(f"{TR}/{ok['id']}").json()["origin"] == {"type": "ellipsoid"}
    assert tc.post(TR, json={"ang1": 0, "name": "т"}).json()["code"] == "name_taken"
    assert tc.post(TR, json={"ang1": 0}).json()["name"] == "Тренд"
    assert tc.post(TR, json={"ang1": 0}).json()["name"] == "Тренд (2)"
    assert tc.put(f"{TR}/{ok['id']}", json={"name": "Тренд", "expected_version": 1}).json()["code"] == "name_taken"
    r = tc.put(f"{TR}/{ok['id']}", json={"ratios": [1, 0.5, 0.5], "color": "#1F6BFF"})  # the version is required
    assert (r.status_code, r.json()["param"]) == (422, "expected_version"), r.text
    r = tc.put(f"{TR}/{ok['id']}", json={"ratios": [1, 0.5, 0.5], "color": "#1F6BFF", "expected_version": 1})
    assert r.json() == {"id": ok["id"], "name": "Т", "version": 2}, r.text
    r = tc.put(f"{TR}/{ok['id']}", json={"ang1": 45, "expected_version": 1})  # another window with the version 1
    assert (r.status_code, r.json()["code"], r.json()["version"]) == (409, "version_conflict", 2), r.text
    assert r.json()["message"] == "Тренд изменён в другом окне." and r.json()["hint"]
    got = tc.get(f"{TR}/{ok['id']}").json()
    assert (got["ang1"], got["ratios"], got["color"], got["name"], got["version"]) == (
        10.0, [1.0, 0.5, 0.5], "#1f6bff", "Т", 2)  # fmt: skip
    for r in (
        tc.get(f"{TR}/9999"),
        tc.put(f"{TR}/9999", json={"ang1": 1, "expected_version": 1}),
        tc.delete(f"{TR}/9999"),
        tc.get(f"{TR}/9999/usage"),
        tc.get(f"{TR}/{mid}"),
    ):  # fmt: skip — a model is not a trend
        assert (r.status_code, r.json()["code"]) == (404, "trend_not_found"), r.text


def test_read_only_project_is_423(tc) -> None:  # type: ignore[no-untyped-def]
    tid = tc.post(TR, json={"ang1": 0}).json()["id"]
    tc.project.read_only = True
    try:
        for r in (tc.post(TR, json={"ang1": 0}), tc.put(f"{TR}/{tid}", json={"ang1": 5, "expected_version": 1}),
                  tc.delete(f"{TR}/{tid}")):  # fmt: skip
            assert (r.status_code, r.json()["code"]) == (423, "project_read_only"), r.text
        assert tc.get(TR).status_code == 200 and tc.get(f"{TR}/{tid}/usage").json() == {"usage": []}
    finally:
        tc.project.read_only = False


# --- ТД15-3 -----------------------------------------------------------------------------------------------------------


def test_td15_3_usage_and_409_in_use(tc) -> None:  # type: ignore[no-untyped-def]
    tid = tc.post(TR, json={"ang1": 45, "name": "Тренд СВ"}).json()["id"]
    free = tc.post(TR, json={"ang1": 0, "name": "Свободный"}).json()["id"]
    assert tc.get(f"{TR}/{tid}/usage").json() == {"usage": []}
    a = consumer(tc.project.engine, tid, "Оценка AU")
    b = consumer(tc.project.engine, tid, "RBF зона 1")
    usage = tc.get(f"{TR}/{tid}/usage").json()["usage"]
    assert usage == [{"id": b, "kind": "estimation", "name": "RBF зона 1", "role": "trend"},
                     {"id": a, "kind": "estimation", "name": "Оценка AU", "role": "trend"}]  # fmt: skip — by name
    assert {t["id"]: t["used"] for t in tc.get(TR).json()["trends"]} == {tid: True, free: False}
    r = tc.delete(f"{TR}/{tid}")
    assert (r.status_code, r.json()["code"]) == (409, "in_use"), r.text
    assert r.json()["usage"] == usage and "«Оценка AU»" in r.json()["message"] and r.json()["hint"]
    assert tc.get(f"{TR}/{tid}").status_code == 200  # nothing deleted
    with dialect.write_tx(tc.project.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id.in_([a, b])))  # the consumers gone: their rows with them
    assert tc.delete(f"{TR}/{tid}").json() == {"deleted": tid}
    assert tc.events[-1] == ("geostat.changed", {"kind": "trend", "id": tid, "deleted": True})
    assert [t["id"] for t in tc.get(TR).json()["trends"]] == [free]


# --- the view key `ellipsoids` ------------------------------------------------------------------------------------


def test_view_key_ellipsoids() -> None:
    raw = [{"object": 5}, {"object": 7, "visible": False}, {"object": 5, "visible": False}, {"x": 1}, 3]
    assert vk.normalize("ellipsoids", raw, strict=False) == [{"object": 5, "visible": True},
                                                             {"object": 7, "visible": False}]  # fmt: skip
    for bad in ({"object": 1}, [{"object": 0}], [{"object": 1, "visible": "да"}]):
        with pytest.raises(vk.BadValue):
            vk.normalize("ellipsoids", bad, strict=True)
    many = [{"object": i} for i in range(1, 80)]
    assert [d["object"] for d in vk.normalize("ellipsoids", many, strict=True)] == list(range(30, 80))
    value = vk.normalize("ellipsoids", raw, strict=False)
    assert list(vk.ids_in("ellipsoids", value)) == [5, 7]
    assert vk.drop_missing("ellipsoids", value, lambda i: i == 7) == [{"object": 7, "visible": False}]
    assert vk.map_ids("ellipsoids", value, {5: 50}.get) == [{"object": 50, "visible": True}]


# --- ТД15-4 -----------------------------------------------------------------------------------------------------------


def test_td15_4_bfggis_roundtrip(tc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.archive import importer

    key, _ = calc(tc.project, tc.set_id, tc.a)
    mid = save(tc, key, model=TWO, name="Золото").json()["id"]
    tid = tc.post(TR, json={"model_id": mid, "centre": [5_000_123.25, 7_200_456.5, 312.125]}).json()["id"]
    hand = tc.post(TR, json={"name": "Вручную", "ang1": 17, "ang2": -3, "ratios": [1, 0.3, 0.07]}).json()["id"]
    user = consumer(tc.project.engine, tid)
    shown = [{"object": tid, "visible": True}, {"object": mid, "visible": False}, {"object": hand, "visible": True}]
    r = tc.put(API + "/view/ellipsoids", json={"value": shown, "expected_version": 0})
    assert r.status_code == 200, r.text
    assert tc.post(API + "/views", json={"camera": CAMERA, "name": "С трендами"}).status_code == 200
    before = {t["name"]: t for t in tc.get(TR).json()["trends"]}
    r = tc.post(API + "/projects/export", json={"path": str(tmp_path / "Тренды")}).json()
    job = wait_job(tc, r["source"], r["id"])
    assert job["status"] == "done", job
    r = tc.post(API + "/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(tc, r["source"], r["id"])
    assert job["status"] == "done", job
    maps = {int(k): int(v) for k, v in job["result"]["id_maps"]["objects"].items()}
    assert tc.post(f"{API}/projects/{job['result']['uid']}/open").status_code == 200
    project = projects_of(tc.core).require_current()
    if not hasattr(importer, "remap_geostat"):  # until the line of the orchestrator in `importer._restore` is in
        with dialect.write_tx(project.engine) as conn:
            remap.remap_geostat(conn, maps, {})
    after = {t["name"]: t for t in tc.get(TR).json()["trends"]}
    assert set(after) == set(before) == {"Тренд · Золото", "Вручную"}
    for name, old in before.items():
        new = after[name]
        assert new["id"] == maps[old["id"]]
        for k in ("ang1", "ang2", "ang3", "ratios", "size", "centre", "color", "matrix", "kind", "used"):
            assert new[k] == old[k], (name, k)  # bit for bit
    assert after["Тренд · Золото"]["origin"] == {"type": "model", "model_id": maps[mid], "structure": 2}
    assert after["Вручную"]["origin"] == {"type": "manual"}
    new_tid = maps[tid]
    assert tc.get(f"{TR}/{new_tid}/usage").json()["usage"] == [
        {"id": maps[user], "kind": "estimation", "name": "Оценка AU", "role": "trend"}]  # fmt: skip
    assert tc.delete(f"{TR}/{new_tid}").json()["code"] == "in_use"
    expect = [{"object": maps[tid], "visible": True}, {"object": maps[mid], "visible": False},
              {"object": maps[hand], "visible": True}]  # fmt: skip
    assert tc.get(API + "/view").json()["values"]["ellipsoids"]["value"] == expect
    view = tc.get(API + "/views").json()["views"][0]
    assert tc.get(f"{API}/views/{view['id']}").json()["keys"]["ellipsoids"] == expect


def test_remap_of_a_trend_meta() -> None:
    meta = {"v": 1, "ang1": 1.0, "origin": {"type": "model", "model_id": 4, "structure": 1}}
    assert remap.trend_meta(meta, {4: 40}.get)["origin"] == {"type": "model", "model_id": 40, "structure": 1}
    assert remap.trend_meta(meta, {}.get)["origin"]["model_id"] is None  # not in the archive
    assert remap.trend_meta({"origin": {"type": "manual"}}, {}.get) == {"origin": {"type": "manual"}}
