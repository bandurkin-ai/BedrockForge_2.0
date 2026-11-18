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


def _cells(table: Any, offset: int, limit: int) -> list[list[Any]]:
    """The reference page: rows of the table the layer was made from; an empty number is null."""
    part = table.slice(offset, limit)
    columns = [part.column(name).to_pylist() for name in part.column_names]
    return [[None if isinstance(v, float) and v != v else v for v in row] for row in zip(*columns, strict=True)]


def _attributes(n: int, seed: int) -> Any:
    """`n` rows of ten attributes: numbers with gaps, repeated codes, Cyrillic text with gaps."""
    import pyarrow as pa
    import pyarrow.compute as pc

    rng = np.random.default_rng(seed)
    idx = np.arange(n)
    grade = rng.random(n)
    grade[::11] = np.nan
    columns: dict[str, Any] = {"N": pa.array(idx.astype(np.float64)), "AU": pa.array(grade)}
    columns["Код"] = pc.binary_join_element_wise(pa.scalar("К"), pc.cast(pa.array(idx % 977), pa.string()), "-")
    note = pc.binary_join_element_wise(pa.scalar("проба"), pc.cast(pa.array(idx), pa.string()), " № ")
    columns["Примечание"] = pc.if_else(pa.array(idx % 7 != 0), note, pa.scalar(None, pa.string()))
    for k in range(6):
        columns[f"E{k}"] = pa.array(rng.normal(size=n))
    return pa.table(columns)


def test_attribute_pages_read_only_their_row_groups(lines, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """Pages at every kind of offset are the rows of the source table (Э7 review: a page used to read the
    whole table): across the seams of row groups, by vertices, of a layer in a pack, of a table in one group."""
    import pyarrow.parquet as pq

    c = lines["client"]
    project = projects_of(c.core).require_current()
    store = BlobStore(project.folder)
    n = 3 * ls.ROW_GROUP + 1234
    attrs = _attributes(n, 3)
    xyz = UTM + np.column_stack([np.arange(n) % 1000, np.arange(n) // 1000, np.zeros(n)])
    k = 2 * ls.ROW_GROUP // 5 + 7  # lines of five vertices: the vertex attributes span two groups and a bit
    vattrs = _attributes(5 * k, 4).select(["N", "Примечание"])
    polylines = [OPEN[[0, 1, 2, 1, 0]] + [float(i), 0.0, 0.0] for i in range(k)]
    with dialect.write_tx(project.engine) as conn:
        big = ls.put_layer(conn, store, from_points("Пробы", xyz, attrs=attrs), file_id=lines["file"])
        by_vertex = ls.put_layer(conn, store, from_lines("Профили", polylines, vattrs=vattrs), file_id=lines["file"])
        monkeypatch.setattr(ls, "ROW_GROUP", 1 << 20)  # as files were written before: everything in one group
        old = ls.put_layer(conn, store, from_points("Пробы, один блок", xyz, attrs=attrs), file_id=lines["file"])
        monkeypatch.undo()
        small = [from_points(f"Малый {i}", xyz[:500], attrs=attrs.slice(700 * i, 500)) for i in range(3)]
        packed = ls.commit_layers(conn, store, ls.stage_layers(store, small), file_id=lines["file"])
        roles = {i: set(ls.layer_blobs(conn, i)) for i in (big, by_vertex, old, *packed)}
    assert "attrs" in roles[big] and "vattrs" in roles[by_vertex]  # big layers: files of their own
    assert [roles[i] for i in packed] == [{"pack:0"}, {"pack:1"}, {"pack:2"}]  # small ones share a blob

    asked: list[list[int]] = []
    read_groups = pq.ParquetFile.read_row_groups

    def spy(self: Any, row_groups: Any, *args: Any, **kwargs: Any) -> Any:
        asked.append(list(row_groups))
        return read_groups(self, row_groups, *args, **kwargs)

    def whole(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a page of attributes must not read the whole table")

    monkeypatch.setattr(pq.ParquetFile, "read_row_groups", spy)
    monkeypatch.setattr(pq, "read_table", whole)
    monkeypatch.setattr(ls.LayerFiles, "table", whole)
    g = ls.ROW_GROUP
    pages = [(0, 200, [0]), (g - 1, 1, [0]), (g - 1, 2, [0, 1]), (g, 200, [1]), (2 * g - 100, 5000, [1, 2]),
             (3 * g, 200, [3]), (n - 1, 200, [3]), (n - 300, 5000, [3]), (g + 7, 1, [1])]  # fmt: skip
    for offset, limit, groups in pages:
        asked.clear()
        page = c.get(f"{API}/lines/layers/{big}/attrs", params={"offset": offset, "limit": limit}).json()
        assert page["rows"] == _cells(attrs, offset, limit), (offset, limit)
        assert asked == [groups] and page["total"] == n and len(page["rows"]) == min(limit, n - offset)
        assert [f["name"] for f in page["fields"]] == attrs.column_names
        legacy = c.get(f"{API}/lines/layers/{old}/attrs", params={"offset": offset, "limit": limit}).json()
        assert legacy["rows"] == page["rows"] and asked[-1] == [0], (offset, limit)  # right, only not as fast
    asked.clear()
    assert c.get(f"{API}/lines/layers/{big}/attrs", params={"offset": n, "limit": 10}).json()["rows"] == []
    assert asked == []  # past the end: nothing is read
    for offset, limit in ((0, 3), (g - 2, 5), (5 * k - 4, 200), (g + 1, 5000)):
        url = f"{API}/lines/layers/{by_vertex}/attrs"
        page = c.get(url, params={"offset": offset, "limit": limit, "scope": "vertex"}).json()
        assert page["rows"] == _cells(vattrs, offset, limit) and page["total"] == 5 * k, (offset, limit)
    assert c.get(f"{API}/lines/layers/{by_vertex}/attrs").json()["rows"] == []  # its lines have no attributes
    # the table of a layer in a pack is a slice of the blob: read in place, the rows of that layer and no other
    for i, layer in enumerate(packed):
        for offset, limit in ((0, 200), (499, 5), (120, 300)):
            page = c.get(f"{API}/lines/layers/{layer}/attrs", params={"offset": offset, "limit": limit}).json()
            assert page["rows"] == _cells(attrs.slice(700 * i, 500), offset, limit), (i, offset, limit)
            assert page["total"] == 500


def test_a_page_of_a_million_rows_takes_milliseconds(lines) -> None:  # type: ignore[no-untyped-def]
    """Budget of the Э7 review: a page of 200 rows of a 1 000 000-row table (ten attributes) ≤ 100 ms at any
    offset — the endpoint as the table of the interface calls it, every page a new one (no 304)."""
    import time

    c = lines["client"]
    project = projects_of(c.core).require_current()
    n = 1_000_000
    attrs = _attributes(n, 5)
    xyz = UTM + np.column_stack([np.arange(n) % 1000, np.arange(n) // 1000, np.zeros(n)])
    with dialect.write_tx(project.engine) as conn:
        layer = ls.put_layer(
            conn, BlobStore(project.folder), from_points("Миллион", xyz, attrs=attrs), file_id=lines["file"]
        )
    url = f"{API}/lines/layers/{layer}/attrs"
    c.get(url, params={"offset": 10, "limit": 200})  # the first call imports pyarrow.parquet
    times = []
    for offset in (0, 65_500, 250_000, 499_990, 750_123, 934_000, 999_700, 999_990):
        t0 = time.perf_counter()
        r = c.get(url, params={"offset": offset, "limit": 200})
        times.append(time.perf_counter() - t0)
        assert r.status_code == 200 and r.json()["rows"] == _cells(attrs, offset, 200), offset
    assert max(times) <= 0.1, [round(t * 1000, 1) for t in times]
    print(
        f"attrs page of 1e6 rows: max {max(times) * 1000:.1f} ms, median {sorted(times)[len(times) // 2] * 1000:.1f} ms"
    )


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
