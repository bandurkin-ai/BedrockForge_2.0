"""API of wireframes and DTMs (ТЗ 7.20; ADR 0011 §3): the tree and the properties, rename and colour with 409,
attributes of the user, `wireframe.dtm` (settings, a plan raster on the relief, a vertical one → 422, «вернуть в
каркасы» clears them), a raster in use is not deleted (422 with the DTMs), deleting a wireframe with its levels
of detail; the job `model.dtm` end to end — the surface passes through the source points bit for bit, lines are
edges, the boundary cuts; a `.bfggis` cycle renumbers the drape reference and keeps the geometry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_holes, object_refs, objects, wireframes
from bf_core.formats.lineset import from_lines, from_points
from bf_core.lines import store as ls_store
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from bf_core.viz import meshes as vm
from bf_core.wireframes import store as ws
from tests.test_archive import export
from tests.test_dtm import assert_breakline_is_edges, plan_areas
from tests.test_import_job import API, wait_job
from tests.test_viz_mesh import grid, hills
from tests.test_wireframes_store import add_raster

pytestmark = pytest.mark.windows

UTM = np.array([512_345.678_901, 6_234_567.123_456, 412.345_678])
DEFAULTS = {"raster_id": None, "clip": True, "tint": "#ffffff", "opacity": 1.0, "shade": True}


@pytest.fixture(scope="module")
def numba_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("numba")


@pytest.fixture()
def api(pclient, numba_cache: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A project with a relief, a small solid, two plan rasters and a vertical one. The workers of the jobs
    share one numba cache: the kernels are compiled once for the module."""
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(numba_cache))
    r = pclient.post(f"{API}/projects", json={"name": "ЦТМ"})
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    v, t = grid(60, 50, hills, offset=UTM)
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        ids = {
            "relief": ws.put_wireframe(conn, store, v, t, name="Рельеф"),
            "body": ws.put_wireframe(conn, store, v[:3], np.array([[0, 1, 2]], dtype=np.int32), name="Тело"),
            "plan": add_raster(conn, "Ортофото"),
            "map": add_raster(conn, "Карта"),
            "scan": add_raster(conn, "Разрез 12", vertical=True),
        }
    vm.forget()
    return pclient, project, ids


def ok(r: Any) -> Any:
    assert r.status_code == 200, r.text
    return r.json()


def refused(r: Any, status: int, code: str) -> dict[str, Any]:
    body: dict[str, Any] = r.json()
    assert r.status_code == status and body["code"] == code, r.text
    assert body["message"] and body["hint"]
    return body


def events(c: Any) -> list[tuple[str, dict[str, Any]]]:
    return [(name, json.loads(data)) for _id, name, data in c.core.events._recent]


# --- the tree and the properties -------------------------------------------------------------------------


def test_tree_properties_rename_and_colour(api) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = api
    listed = ok(c.get(f"{API}/wireframes"))["wireframes"]
    assert [(w["id"], w["name"], w["dtm"]) for w in listed] == [
        (ids["relief"], "Рельеф", None),
        (ids["body"], "Тело", None),
    ]
    w = ok(c.get(f"{API}/wireframes/{ids['relief']}"))
    assert w == listed[0] and w["version"] == 1 and w["triangle_count"] == 6000 and w["point_count"] == 61 * 51
    assert w["stats"]["plan_area"] == pytest.approx(3000.0, rel=1e-12) and not w["stats"]["closed"]
    assert w["attrs"] == [] and w["color"] is None and len(w["extent"]) == 6
    url = f"{API}/wireframes/{ids['relief']}"
    w = ok(c.patch(url, json={"expected_version": 1, "name": " Рельеф 2026 ", "color": "#8C9A6B"}))
    assert (w["name"], w["color"], w["version"]) == ("Рельеф 2026", "#8c9a6b", 2)
    assert ("objects.changed", {"kind": "wireframe", "id": ids["relief"]}) in events(c)
    stale = refused(c.patch(url, json={"expected_version": 1, "name": "Другое"}), 409, "version_conflict")
    assert stale["wireframe"]["name"] == "Рельеф 2026" and stale["wireframe"]["version"] == 2
    refused(c.patch(url, json={"expected_version": 2, "name": "тело"}), 409, "name_taken")
    refused(c.patch(url, json={"expected_version": 2, "name": "  "}), 422, "name_empty")
    refused(c.patch(url, json={"expected_version": 2, "color": "зелёный"}), 422, "bad_color")
    assert ok(c.patch(url, json={"expected_version": 2, "color": None}))["color"] is None
    refused(c.get(f"{API}/wireframes/999999"), 404, "wireframe_not_found")
    refused(c.patch(f"{API}/wireframes/999999", json={"expected_version": 1, "name": "Х"}), 404, "wireframe_not_found")
    assert c.patch(url, json={"name": "Без версии"}).status_code == 422


def test_attributes_of_the_user(api) -> None:  # type: ignore[no-untyped-def]
    c, _project, ids = api
    url = f"{API}/wireframes/{ids['body']}/attrs"
    w = ok(c.patch(url, json={"expected_version": 1, "set": {"Участок": "Север", "Год": 2026, "Утверждён": False}}))
    assert w["attrs"] == [
        {"name": "Участок", "value": "Север"},
        {"name": "Год", "value": 2026},
        {"name": "Утверждён", "value": False},
    ]
    body = {
        "expected_version": 2,
        "rename": {"Год": "Год отчёта"},
        "remove": ["Утверждён"],
        "set": {"участок": "Юг", "Плотность": 2.7, "Комментарий": None},
    }
    w = ok(c.patch(url, json=body))
    assert w["version"] == 3 and w["attrs"] == [
        {"name": "Участок", "value": "Юг"},
        {"name": "Год отчёта", "value": 2026},
        {"name": "Плотность", "value": 2.7},
        {"name": "Комментарий", "value": None},
    ]
    assert ok(c.get(f"{API}/wireframes/{ids['body']}"))["attrs"] == w["attrs"]
    refused(c.patch(url, json={"expected_version": 3, "remove": ["Нет такого"]}), 404, "attr_not_found")
    refused(c.patch(url, json={"expected_version": 3, "rename": {"Участок": "плотность"}}), 422, "attr_exists")
    refused(c.patch(url, json={"expected_version": 3, "set": {" ": 1}}), 422, "attr_name_empty")
    refused(c.patch(url, json={"expected_version": 2, "set": {"Т": 1}}), 409, "version_conflict")
    assert c.patch(url, json={"expected_version": 3, "set": {"Т": [1]}}).status_code == 422
    assert ok(c.get(f"{API}/wireframes/{ids['body']}"))["version"] == 3


# --- DTM -------------------------------------------------------------------------------------------------


def test_dtm_settings_raster_and_back_to_wireframes(api) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = api
    url = f"{API}/wireframes/{ids['relief']}/dtm"
    # settings without `on` move the wireframe to the DTMs; a number may come as text with a comma
    w = ok(c.put(url, json={"expected_version": 1, "opacity": "0,5"}))
    assert w["dtm"] == {**DEFAULTS, "opacity": 0.5} and w["version"] == 2
    w = ok(c.put(url, json={"expected_version": 2, "raster_id": ids["plan"], "tint": "#FFEEDD", "shade": False}))
    assert w["dtm"] == {"raster_id": ids["plan"], "clip": True, "tint": "#ffeedd", "opacity": 0.5, "shade": False}
    tree = {x["id"]: x["dtm"] for x in ok(c.get(f"{API}/wireframes"))["wireframes"]}
    assert tree[ids["relief"]] == w["dtm"] and tree[ids["body"]] is None  # «ЦТМ» and «Каркасы»
    # a vertical raster is not put on a relief, and the refused request changes nothing
    e = refused(
        c.put(url, json={"expected_version": 3, "raster_id": ids["scan"], "opacity": 0.1}), 422, "drape_vertical_raster"
    )
    assert "Разрез 12" in e["message"]
    refused(c.put(url, json={"expected_version": 3, "raster_id": 999_999}), 404, "raster_not_found")
    refused(c.put(url, json={"expected_version": 3, "opacity": 1.5}), 422, "bad_opacity")
    refused(c.put(url, json={"expected_version": 3, "opacity": "много"}), 422, "not_a_number")
    refused(c.put(url, json={"expected_version": 3, "tint": "red"}), 422, "bad_color")
    refused(c.put(url, json={"expected_version": 2, "clip": False}), 409, "version_conflict")
    assert ok(c.get(f"{API}/wireframes/{ids['relief']}")) == w
    # a raster in use is not deleted: the answer lists the DTMs
    e = refused(c.delete(f"{API}/rasters/{ids['plan']}"), 422, "raster_in_use")
    assert e["wireframes"] == [{"id": ids["relief"], "name": "Рельеф"}] and "Рельеф" in e["message"]
    assert ok(c.delete(f"{API}/rasters/{ids['map']}")) == {"deleted": ids["map"]}  # a free one goes
    # the manifest of the 3D view carries the settings; the parts keep their ETags
    r = c.get(f"{API}/viz/wireframes/{ids['relief']}")
    head = binary.decode(r.content)[0]
    assert head["dtm"] == w["dtm"] and head["version"] == 3
    tags = head["parts"][0]["etags"]
    # 0 takes the raster off, the other settings stay
    w = ok(c.put(url, json={"expected_version": 3, "raster_id": 0}))
    assert w["dtm"] == {"raster_id": None, "clip": True, "tint": "#ffeedd", "opacity": 0.5, "shade": False}
    w = ok(c.put(url, json={"expected_version": 4, "raster_id": ids["plan"], "on": True}))
    assert w["dtm"]["raster_id"] == ids["plan"]
    # «Вернуть в каркасы»: the settings and the raster are cleared
    w = ok(c.put(url, json={"expected_version": 5, "on": False}))
    assert w["dtm"] is None and w["version"] == 6
    with dialect.read_tx(project.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(object_refs)).scalar_one() == 0
        assert conn.execute(sa.select(wireframes.c.dtm).where(wireframes.c.object_id == ids["relief"])).scalar() is None
    head = binary.decode(c.get(f"{API}/viz/wireframes/{ids['relief']}").content)[0]
    assert head["dtm"] is None and head["parts"][0]["etags"] == tags
    assert ok(c.put(url, json={"expected_version": 6, "on": True}))["dtm"] == DEFAULTS  # the defaults again
    assert ok(c.delete(f"{API}/rasters/{ids['plan']}")) == {"deleted": ids["plan"]}  # nobody uses it now
    refused(c.put(f"{API}/wireframes/999999/dtm", json={"expected_version": 1}), 404, "wireframe_not_found")


def test_delete_wireframe_with_its_levels_of_detail(api) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = api
    ok(c.put(f"{API}/wireframes/{ids['relief']}/dtm", json={"expected_version": 1, "raster_id": ids["plan"]}))
    assert c.get(f"{API}/viz/wireframes/{ids['relief']}/parts/0", params={"lod": 1}).status_code == 200
    with dialect.read_tx(project.engine) as conn:
        folder = vm.cache_dir(project.folder, vm.mesh_state(conn, ids["relief"]).cache_key())
    assert sorted(f.name for f in folder.iterdir()) == ["0-1.bfa", "0-2.bfa"]
    assert ok(c.delete(f"{API}/wireframes/{ids['relief']}")) == {"deleted": ids["relief"]}
    assert ("objects.changed", {"kind": "wireframe", "id": ids["relief"], "deleted": True}) in events(c)
    assert not folder.exists()
    refused(c.get(f"{API}/wireframes/{ids['relief']}"), 404, "wireframe_not_found")
    refused(c.get(f"{API}/viz/wireframes/{ids['relief']}"), 404, "wireframe_not_found")
    refused(c.delete(f"{API}/wireframes/{ids['relief']}"), 404, "wireframe_not_found")
    assert ok(c.delete(f"{API}/rasters/{ids['plan']}")) == {"deleted": ids["plan"]}  # the drape went with the DTM
    assert [w["name"] for w in ok(c.get(f"{API}/wireframes"))["wireframes"]] == ["Тело"]


# --- «Построить ЦТМ» -------------------------------------------------------------------------------------


def survey(project: Any) -> dict[str, Any]:
    """Sources of a DTM: survey points (two without a height), a ridge and a line with a gap in its heights,
    the outline of the area, collars of a drillhole set (one without coordinates)."""
    rng = np.random.default_rng(20)
    xy = rng.random((1500, 2)) * 100
    pts = np.column_stack([xy, 400 + 0.2 * xy[:, 0] + 5 * np.sin(xy[:, 1] / 9)]) + [UTM[0], UTM[1], 0.0]
    pts[[7, 900], 2] = np.nan
    ridge = np.array([[5.0, 6.0, 430.0], [33.0, 41.5, 436.0], [61.0, 58.0, 433.0], [96.0, 93.0, 431.0]])
    gap = np.array([[10.0, 90.0, 425.0], [30.0, 80.0, np.nan], [50.0, 75.0, 427.0], [70.0, 71.0, 426.0]])
    ridge[:, :2] += UTM[:2]
    gap[:, :2] += UTM[:2]
    outline = np.array([[10.0, 10.0, 0.0], [90.0, 10.0, 0.0], [90.0, 90.0, 0.0], [50.0, 55.0, 0.0], [10.0, 90.0, 0.0]])
    outline[:, :2] += UTM[:2]
    collars = np.array([[40.0, 20.0, 409.5], [70.0, 30.0, 415.25]]) + [UTM[0], UTM[1], 0.0]
    now = dialect.utcnow()
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        file_id = ls_store.create_file(conn, store, name="съёмка.str", meta={"format": "micromine_str"})
        out = {
            "points": ls_store.put_layer(conn, store, from_points("Пикеты", pts), file_id=file_id),
            "lines": ls_store.put_layer(conn, store, from_lines("Бровки", [ridge, gap]), file_id=file_id),
            "outline": ls_store.put_layer(conn, store, from_lines("Контур", [outline], closed=[True]), file_id=file_id),
        }
        set_id = conn.execute(
            sa.insert(objects).values(
                kind="drillhole_set",
                name="Разведка",
                name_key="разведка",
                sort_key=0.0,
                state="ready",
                version=1,
                meta={},
                created_at=now,
                updated_at=now,
            )  # fmt: skip
        ).inserted_primary_key[0]
        for i, (x, y, z) in enumerate(collars.tolist()):
            conn.execute(sa.insert(dh_holes).values(set_id=set_id, name=f"С-{i + 1}", x=x, y=y, z=z, has_collar=True))
        conn.execute(sa.insert(dh_holes).values(set_id=set_id, name="С-3", has_collar=False))
    return {**out, "set": int(set_id), "pts": pts, "ridge": ridge, "gap": gap, "collars": collars}


def build(c: Any, body: dict[str, Any]) -> dict[str, Any]:
    started = ok(c.post(f"{API}/wireframes/build-dtm", json=body))
    job: dict[str, Any] = wait_job(c, started["source"], started["id"], timeout=180)
    job["log"] = [row["text"] for row in ok(c.get(f"{API}/jobs/{started['source']}/{started['id']}/log"))["lines"]]
    return job


def rows_of(a: np.ndarray) -> set[bytes]:
    return {r.tobytes() for r in np.ascontiguousarray(a)}


def test_build_dtm_passes_through_the_source_points(api) -> None:  # type: ignore[no-untyped-def]
    c, project, _ids = api
    s = survey(project)
    job = build(c, {"layer_ids": [s["points"], s["lines"]], "set_ids": [s["set"]], "max_edge": "40,5"})
    assert job["status"] == "done", job
    res = job["result"]
    w = ok(c.get(f"{API}/wireframes/{res['wireframe_id']}"))
    assert w["name"] == res["name"] == "ЦТМ: Пикеты, Бровки, устья «Разведка»"
    assert w["dtm"] == DEFAULTS  # «Сразу в раздел ЦТМ»
    assert w["built"]["kind"] == "dtm" and "CDT" in w["built"]["method"]
    assert [u["name"] for u in w["built"]["sources"]] == ["Пикеты", "Бровки", "Разведка"]
    assert w["built"]["params"]["max_edge"] == 40.5 and w["built"]["report"]["breakline_segments"] == 4
    with dialect.read_tx(project.engine) as conn:
        v, t = ws.read_wireframe(conn, BlobStore(project.folder), res["wireframe_id"])
    # the surface passes through every source point that has a height: tolerance 0, bit for bit
    finite = s["pts"][np.isfinite(s["pts"][:, 2])]
    source = rows_of(finite) | rows_of(s["ridge"]) | rows_of(s["gap"][[0, 2, 3]]) | rows_of(s["collars"])
    assert rows_of(v) == source and len(v) == 1498 + 4 + 3 + 2 == res["points"] == w["point_count"]
    assert res["skipped_empty"] == 4  # two points, a vertex of a line, a collar without coordinates — never Z = 0
    assert not np.isnan(v).any() and v[:, 2].min() > 380
    assert (plan_areas(v, t) > 0).all() and res["triangles"] == len(t) == w["triangle_count"]
    # structural lines are edges; the line with a gap is cut at the vertex without a height
    assert_breakline_is_edges(v, t, s["ridge"])
    assert_breakline_is_edges(v, t, s["gap"][2:])
    assert res["area_plan"] == pytest.approx(w["stats"]["plan_area"], rel=1e-9)
    assert any("без высоты: 4" in line for line in job["log"]) and any("Создан каркас" in line for line in job["log"])
    # the levels of detail are there before the first frame
    with dialect.read_tx(project.engine) as conn:
        state = vm.mesh_state(conn, res["wireframe_id"])
    folder = vm.cache_dir(project.folder, state.cache_key())
    assert sorted(f.name for f in folder.iterdir()) == ["0-1.bfa", "0-2.bfa"]
    r = c.get(f"{API}/viz/wireframes/{res['wireframe_id']}/parts/0", params={"lod": 1})
    assert r.status_code == 200 and r.content == (folder / "0-1.bfa").read_bytes()
    assert res["lod_triangles"][0] == len(t) > res["lod_triangles"][1] > res["lod_triangles"][2] > 0
    assert BlobStore(project.folder).tmp_dir.exists() and not list(BlobStore(project.folder).tmp_dir.iterdir())

    # points only (lines as plain vertices), a boundary, into «Каркасы», a name of the user; the name is free
    body = {
        "name": "ЦТМ: Пикеты, Бровки, устья «Разведка»",
        "layer_ids": [s["points"], s["lines"]],
        "breaklines": False,
        "boundary": {"layer_id": s["outline"]},
        "to_dtm": False,
        "color": "#8C9A6B",
    }
    job = build(c, body)
    assert job["status"] == "done", job
    w2 = ok(c.get(f"{API}/wireframes/{job['result']['wireframe_id']}"))
    assert w2["name"] == "ЦТМ: Пикеты, Бровки, устья «Разведка» (2)" and w2["dtm"] is None and w2["color"] == "#8c9a6b"
    # the outline 80 × 80 without the notch (80 × 35 / 2): its own heights (0) are not taken
    assert job["result"]["area_plan"] == pytest.approx(6400.0 - 1400.0, rel=1e-9)
    with dialect.read_tx(project.engine) as conn:
        v2, _t2 = ws.read_wireframe(conn, BlobStore(project.folder), w2["id"])
    # four corners of the outline are new vertices with the height of the surface; the fifth, (10, 90), is the
    # end of a line of the sources and keeps the height of that vertex
    assert v2[:, 2].min() > 380 and len(rows_of(v2) & source) == len(v2) - 4
    assert rows_of(s["gap"][:1]) <= rows_of(v2)


def test_build_dtm_refusals_leave_nothing(api) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = api
    s = survey(project)
    url = f"{API}/wireframes/build-dtm"
    refused(c.post(url, json={}), 422, "no_sources")
    refused(c.post(url, json={"layer_ids": [999_999]}), 404, "line_layer_not_found")
    refused(c.post(url, json={"layer_ids": [ids["relief"]]}), 404, "line_layer_not_found")  # not a layer
    refused(c.post(url, json={"set_ids": [s["points"]]}), 404, "set_not_found")
    refused(c.post(url, json={"layer_ids": [s["points"]], "max_edge": 0}), 422, "bad_max_edge")
    refused(c.post(url, json={"layer_ids": [s["points"]], "max_edge": "длинное"}), 422, "not_a_number")
    refused(c.post(url, json={"layer_ids": [s["points"]], "color": "red"}), 422, "bad_color")
    refused(c.post(url, json={"layer_ids": [s["points"]], "boundary": {"layer_id": s["points"]}}), 422, "bad_boundary")
    refused(c.post(url, json={"layer_ids": [s["points"]], "boundary": {"layer_id": s["lines"]}}), 422, "bad_boundary")
    # a failure inside the job: the same place at two heights is never resolved silently
    clash = s["pts"][:3].copy()
    clash[:, 2] += 2.5
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        file_id = ls_store.create_file(conn, store, name="повтор.csv", meta={"format": "csv"})
        again = ls_store.put_layer(conn, store, from_points("Повтор", clash), file_id=file_id)
    before = {w["id"] for w in ok(c.get(f"{API}/wireframes"))["wireframes"]}
    job = build(c, {"layer_ids": [s["points"], again]})
    assert job["status"] == "failed" and job["error"]["code"] == "z_conflict", job
    assert "3 шт" in job["error"]["message"] and "не выбирается автоматически" in job["error"]["hint"]
    job = build(c, {"layer_ids": [s["points"]], "boundary": {"layer_id": s["outline"], "line": 3}})
    assert job["status"] == "failed" and job["error"]["code"] == "bad_boundary", job
    job = build(c, {"set_ids": [s["set"]]})  # two collars: not a surface
    assert job["status"] == "failed" and job["error"]["code"] == "too_few_points", job
    assert {w["id"] for w in ok(c.get(f"{API}/wireframes"))["wireframes"]} == before
    with dialect.read_tx(project.engine) as conn:
        kinds = conn.execute(sa.select(sa.func.count()).select_from(objects).where(objects.c.kind == "wireframe"))
        assert kinds.scalar_one() == len(before)
    tmp = BlobStore(project.folder).tmp_dir
    assert not tmp.exists() or not list(tmp.iterdir())
    mesh_cache = project.folder / "cache" / "mesh"
    assert not mesh_cache.exists() or not list(mesh_cache.iterdir())


@pytest.mark.parametrize(
    "stage", ["Триангуляция", "Площадь и объём", "Запись каркаса", "Уровни детализации", "Сохранение каркаса в проект"]
)
def test_cancelled_build_leaves_nothing(api, stage: str) -> None:  # type: ignore[no-untyped-def]
    """The handler itself, in this process: a cancel that arrives at any step before the write transaction
    removes the temp files of the blobs and the folder of the levels of detail."""
    import threading

    from bf_core.jobs.context import JobCancelled, JobContext
    from bf_core.wireframes import jobs as dtm_jobs

    c, project, _ids = api
    s = survey(project)
    with dialect.read_tx(project.engine) as conn:
        params = dtm_jobs.request(conn, layer_ids=[s["points"], s["lines"]], set_ids=[s["set"]])
    assert params["points"] == 1500 + 8 + 3 and dtm_jobs._memory(params) >= 512
    seen: list[str] = []

    class Ctx(JobContext):
        def tick(self, progress: float | None = None, message: str | None = None) -> None:
            seen.append(str(message))
            if message == stage:
                raise JobCancelled()

    ctx = Ctx({"id": 1, "params": params, "project_folder": str(project.folder)}, lambda _m: None, threading.Event())
    try:
        with pytest.raises(JobCancelled):
            dtm_jobs.model_dtm(ctx)
    finally:
        ctx.close()
    assert stage in seen and "Готово" not in seen
    tmp = BlobStore(project.folder).tmp_dir
    assert not tmp.exists() or not list(tmp.iterdir())
    mesh_cache = project.folder / "cache" / "mesh"
    assert not mesh_cache.exists() or not list(mesh_cache.iterdir())
    assert [w["name"] for w in ok(c.get(f"{API}/wireframes"))["wireframes"]] == ["Рельеф", "Тело"]


# --- .bfggis ---------------------------------------------------------------------------------------------


def test_bfggis_cycle_renumbers_the_drape_and_keeps_the_geometry(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c, project, ids = api
    ok(c.delete(f"{API}/wireframes/{ids['body']}"))  # a gap in the ids: nothing may rely on them staying
    ok(c.delete(f"{API}/rasters/{ids['map']}"))
    url = f"{API}/wireframes/{ids['relief']}"
    ok(c.put(f"{url}/dtm", json={"expected_version": 1, "raster_id": ids["plan"], "tint": "#ddeeff", "opacity": 0.75}))
    ok(c.patch(f"{url}/attrs", json={"expected_version": 2, "set": {"Участок": "Север", "Год": 2026}}))
    source = ok(c.get(url))
    with dialect.read_tx(project.engine) as conn:
        v, t = ws.read_wireframe(conn, BlobStore(project.folder), ids["relief"])
    parts = [c.get(f"{API}/viz/wireframes/{ids['relief']}/parts/0", params={"lod": lod}).content for lod in range(3)]
    job = export(c, tmp_path / "цтм.bfggis")
    r = c.post(f"{API}/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(c, r["source"], r["id"])
    assert job["status"] == "done", job
    new = {int(k): v for k, v in job["result"]["id_maps"]["objects"].items()}
    assert new[ids["plan"]] != ids["plan"]  # the raster has a new id: a reference kept as a number would dangle
    ok(c.post(f"{API}/projects/{job['result']['uid']}/open"))
    copy = projects_of(c.core).require_current()
    assert copy.uid != project.uid
    (w,) = ok(c.get(f"{API}/wireframes"))["wireframes"]
    assert w["id"] == new[ids["relief"]]
    assert w["dtm"] == {**source["dtm"], "raster_id": new[ids["plan"]]}  # the drape follows the raster
    for key in ("name", "attrs", "stats", "extent", "origin", "point_count", "triangle_count", "color", "version"):
        assert w[key] == source[key], key
    with dialect.read_tx(copy.engine) as conn:
        v2, t2 = ws.read_wireframe(conn, BlobStore(copy.folder), w["id"])
        ref = conn.execute(sa.select(object_refs).where(object_refs.c.role == "drape")).mappings().one()
    assert (ref["src_id"], ref["dst_id"]) == (new[ids["relief"]], new[ids["plan"]])
    assert v2.tobytes() == v.tobytes() and t2.tobytes() == t.tobytes()  # float64 and the order, bit for bit
    # the levels of detail are not in the archive: they are made again, the same
    assert not (copy.folder / "cache" / "mesh").exists()
    for lod in range(3):
        r = c.get(f"{API}/viz/wireframes/{w['id']}/parts/0", params={"lod": lod})
        assert r.status_code == 200 and r.content == parts[lod]
    e = refused(c.delete(f"{API}/rasters/{new[ids['plan']]}"), 422, "raster_in_use")
    assert e["wireframes"] == [{"id": w["id"], "name": "Рельеф"}]
