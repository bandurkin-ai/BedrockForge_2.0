"""/api/v1/lines (ТЗ 7.18; ADR 0011 §3): the tree of files and layers, properties, renaming and colour with
`expected_version` (409), attributes by pages with ETag / 304, deleting a file with its layers, the export job."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, line_layers, objects
from bf_core.formats.lineset import from_lines, from_points
from bf_core.lines import store as ls
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows

UTM = np.array([512_345.678_901_234, 6_234_567.123_456_789, 412.345_678_9])
SQUARE = UTM + np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [0, 0, 0]], dtype=np.float64)
OPEN = UTM + np.array([[20, 0, 5], [23, 4, 5], [23, 4, 17]], dtype=np.float64)


@pytest.fixture()
def lines(pclient):  # type: ignore[no-untyped-def]
    """A project with one file of two layers (lines with attributes; points) and a file still being imported."""
    pclient.post(f"{API}/projects", json={"name": "Линии"})
    project = projects_of(pclient.core).require_current()
    store = BlobStore(project.folder)
    contours = from_lines(
        "Горизонтали",
        [SQUARE, OPEN, SQUARE + [50.0, 0.0, 0.0]],
        attrs={"RL": [412.5, float("nan"), 300.0], "Тип": ["контур", None, "бровка"]},
        color="#E8873A",
    )
    marks = from_points("Реперы", UTM + [[1, 1, 0], [2, 2, float("nan")]], attrs={"NAME": ["Р-1", "Р-2"]})
    with dialect.write_tx(project.engine) as conn:
        meta = {"format": "micromine_str", "label": "Стринги Micromine", "filename": "survey.str", "encoding": "cp1251"}
        file_id = ls.create_file(conn, store, name="survey.str", meta=meta)
        ids = [
            ls.put_layer(conn, store, layer, file_id=file_id, sort_key=float(k))
            for k, layer in enumerate((contours, marks))
        ]
        busy = ls.create_file(conn, store, name="идёт импорт.dxf", meta={}, state="building", job_id=77)
        hidden = ls.put_layer(conn, store, from_lines("в работе", [OPEN]), file_id=busy)
    return {"client": pclient, "engine": project.engine, "file": file_id, "layers": ids, "busy": busy, "hidden": hidden}


def error_of(r: Any, status: int, code: str) -> dict[str, Any]:
    body = r.json()
    assert r.status_code == status and body["code"] == code, r.text
    assert body["message"] and body["hint"]  # Russian text with a hint, every time
    return body


def test_tree_and_properties(lines) -> None:  # type: ignore[no-untyped-def]
    c = lines["client"]
    files = c.get(f"{API}/lines/files").json()["files"]
    assert [f["name"] for f in files] == ["survey.str"]  # what an import is still building is not in the tree
    f = files[0]
    assert f["id"] == lines["file"] and f["format"] == "micromine_str" and f["encoding"] == "cp1251"
    assert [x["name"] for x in f["layers"]] == ["Горизонтали", "Реперы"]
    assert c.get(f"{API}/lines/files/{lines['file']}").json() == f

    contours, marks = (c.get(f"{API}/lines/layers/{i}").json() for i in lines["layers"])
    assert contours == f["layers"][0] and contours["file_id"] == lines["file"] and contours["version"] == 1
    assert (contours["line_count"], contours["point_count"], contours["closed_count"]) == (3, 13, 2)
    assert contours["closed"] == "some" and contours["color"] == "#e8873a" and not contours["points_only"]
    assert contours["length"] == pytest.approx(40.0 + 5.0 + 12.0 + 40.0, abs=1e-9)
    assert contours["extent"][:3] == [UTM[0], UTM[1], UTM[2]] and contours["origin"] == [512_376.0, 6_234_572.0, 421.0]
    assert [(a["name"], a["kind"], a["scope"]) for a in contours["attr_schema"]] == [
        ("RL", "number", "feature"),
        ("Тип", "text", "feature"),
    ]
    assert marks["points_only"] and marks["line_count"] == 0 and marks["closed"] == "none" and marks["color"] is None

    error_of(c.get(f"{API}/lines/layers/999"), 404, "line_layer_not_found")
    error_of(c.get(f"{API}/lines/layers/{lines['hidden']}"), 404, "line_layer_not_found")
    error_of(c.get(f"{API}/lines/files/{lines['busy']}"), 404, "line_layer_not_found")
    error_of(c.get(f"{API}/lines/layers/{lines['file']}"), 404, "line_layer_not_found")  # a file is not a layer


def test_rename_and_colour_with_expected_version(lines) -> None:  # type: ignore[no-untyped-def]
    c = lines["client"]
    layer, other = lines["layers"]
    url = f"{API}/lines/layers/{layer}"
    tag = c.get(f"{API}/viz/lines/{layer}").headers["ETag"]

    out = c.patch(url, json={"expected_version": 1, "name": "  Горизонтали   5 м ", "color": "#00AAff"})
    assert out.status_code == 200, out.text
    assert out.json()["name"] == "Горизонтали 5 м" and out.json()["color"] == "#00aaff" and out.json()["version"] == 2
    assert c.get(url).json() == out.json()
    assert c.get(f"{API}/viz/lines/{layer}").headers["ETag"] != tag  # the 3D manifest carries the name and colour

    stale = error_of(c.patch(url, json={"expected_version": 1, "color": "#111111"}), 409, "version_conflict")
    assert stale["layer"]["version"] == 2 and stale["layer"]["color"] == "#00aaff"  # the current layer comes back
    assert c.get(url).json()["color"] == "#00aaff"

    error_of(c.patch(url, json={"expected_version": 2, "name": "реперы"}), 409, "name_taken")  # any case
    error_of(c.patch(url, json={"expected_version": 2, "name": "   "}), 422, "name_empty")
    error_of(c.patch(url, json={"expected_version": 2, "color": "red"}), 422, "bad_color")
    error_of(c.patch(url, json={"name": "без версии"}), 422, "bad_request")
    assert c.get(url).json()["version"] == 2  # a refused change changes nothing

    cleared = c.patch(url, json={"expected_version": 2, "color": None}).json()
    assert cleared["color"] is None and cleared["name"] == "Горизонтали 5 м" and cleared["version"] == 3
    same = c.patch(f"{API}/lines/layers/{other}", json={"expected_version": 1, "name": "Реперы"}).json()
    assert same["name"] == "Реперы" and same["version"] == 2  # its own name is not «taken»
    error_of(c.patch(f"{API}/lines/layers/999", json={"expected_version": 1, "name": "x"}), 404, "line_layer_not_found")


def test_attributes_by_pages_with_etag(lines) -> None:  # type: ignore[no-untyped-def]
    c = lines["client"]
    layer, marks = lines["layers"]
    url = f"{API}/lines/layers/{layer}/attrs"
    r = c.get(url)
    page = r.json()
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-cache"
    assert (page["total"], page["offset"], page["limit"], page["scope"]) == (3, 0, 200, "feature")
    assert [f["name"] for f in page["fields"]] == ["RL", "Тип"]
    assert page["rows"] == [[412.5, "контур"], [None, None], [300.0, "бровка"]]  # empty is null, never 0

    tag = r.headers["ETag"]
    again = c.get(url, headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.content == b"" and again.headers["ETag"] == tag
    part = c.get(url, params={"offset": 1, "limit": 1})
    assert part.json()["rows"] == [[None, None]] and part.headers["ETag"] != tag
    assert c.get(url, params={"offset": 3}).json()["rows"] == []
    assert (
        c.get(
            url, params={"offset": 1, "limit": 1}, headers={"If-None-Match": f'W/{part.headers["ETag"]}, "x"'}
        ).status_code
        == 304
    )

    c.patch(f"{API}/lines/layers/{layer}", json={"expected_version": 1, "color": "#123456"})
    changed = c.get(url, headers={"If-None-Match": tag})  # a new version of the layer: the page is sent again
    assert changed.status_code == 200 and changed.headers["ETag"] != tag and changed.json()["version"] == 2

    points = c.get(f"{API}/lines/layers/{marks}/attrs").json()  # a layer of points: a row per point
    assert points["total"] == 2 and points["rows"] == [["Р-1"], ["Р-2"]]
    empty = c.get(f"{API}/lines/layers/{marks}/attrs", params={"scope": "vertex"}).json()
    assert empty["fields"] == [] and empty["rows"] == [] and empty["total"] == 2
    error_of(c.get(url, params={"limit": 0}), 422, "bad_request")
    error_of(c.get(url, params={"scope": "все"}), 422, "bad_request")
    error_of(c.get(f"{API}/lines/layers/999/attrs"), 404, "line_layer_not_found")


def test_delete_file_with_its_layers(lines) -> None:  # type: ignore[no-untyped-def]
    c = lines["client"]
    error_of(c.delete(f"{API}/lines/files/{lines['busy']}"), 409, "file_busy")  # an import is writing it
    error_of(c.delete(f"{API}/lines/files/999"), 404, "line_layer_not_found")
    gone = c.delete(f"{API}/lines/files/{lines['file']}").json()["deleted"]
    assert gone == [lines["file"], *lines["layers"]]
    assert c.get(f"{API}/lines/files").json()["files"] == []
    error_of(c.get(f"{API}/lines/layers/{lines['layers'][0]}"), 404, "line_layer_not_found")
    error_of(c.get(f"{API}/viz/lines/{lines['layers'][0]}"), 404, "line_layer_not_found")
    with dialect.read_tx(lines["engine"]) as conn:
        left = set(conn.execute(sa.select(objects.c.id)).scalars())
        assert left == {lines["busy"], lines["hidden"]}  # the other file is not touched
        assert conn.execute(sa.select(sa.func.count()).select_from(line_layers)).scalar_one() == 1
        owners = set(conn.execute(sa.select(blob_owners.c.owner_id)).scalars())
        assert owners == {lines["hidden"]}  # the files of the deleted layers have no owner: the cleanup takes them


def test_export_job_from_the_api(lines, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c = lines["client"]
    target = tmp_path / "out.dxf"
    error_of(
        c.post(f"{API}/lines/export", json={"path": str(target), "format": "shp", "file_id": lines["file"]}),
        422,
        "bad_format",
    )
    error_of(
        c.post(f"{API}/lines/export", json={"path": str(target), "format": "dxf", "layer_ids": [999]}),
        404,
        "line_layer_not_found",
    )
    r = c.post(f"{API}/lines/export", json={"path": str(target), "format": "dxf", "file_id": lines["file"]})
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"])
    assert job["status"] == "done" and job["title"] == "Экспорт линий (DXF)", job
    assert target.is_file() and target.stat().st_size > 0
