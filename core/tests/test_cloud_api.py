"""A point cloud through the API (ADR 0014 §1 «Атрибуты точки», §3 «LAS, запись», «CSV, запись», §6): a LAS of
1 млн points imported by the wizard's job in a worker, its properties and attribute pages from the blobs, and the
export cycle — LAS «по исходнику» (the source byte for byte, only the bits of the class differ after a
classification), a new LAS (the coordinates bit for bit, the classes by record), CSV back to a cloud."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.formats.las import open_las
from bf_core.formats.las_write import geokey_vlrs, write_las
from bf_core.lines import cloud as lc
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_import_cloud import scene
from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows


def _job(c: Any, r: Any) -> dict[str, Any]:
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=120)
    assert job["status"] == "done", job
    return job["result"]  # type: ignore[no-any-return]


def _cloud(c: Any, layer_id: int) -> lc.Cloud:
    project = projects_of(c.core).require_current()
    with dialect.read_tx(project.engine) as conn:
        return lc.open_cloud(conn, BlobStore(project.folder), layer_id)


def _classify(c: Any, cloud: lc.Cloud, **values: np.ndarray) -> lc.Cloud:
    project = projects_of(c.core).require_current()
    store = BlobStore(project.folder)
    staged = lc.stage_values(store, cloud, values)
    with dialect.write_tx(project.engine) as conn:
        lc.replace_values(conn, store, cloud, staged, ground={"params": {"cr": 1.0}})
    return _cloud(c, cloud.layer_id)


@pytest.fixture()
def imported(pclient: Any, tmp_path: Path) -> tuple[Any, Path, int]:
    folder = tmp_path / "облака"
    folder.mkdir()
    xyz, cls, ret = scene(1_000_000, seed=11)
    path = folder / "съёмка.las"
    write_las(path, xyz, cls, ret, vlrs=geokey_vlrs(32637))
    pclient.post(f"{API}/projects", json={"name": "Облако"})
    seen = pclient.post(f"{API}/import/objects/inspect", json={"files": [str(path)]})
    assert seen.status_code == 200 and seen.json()["files"][0]["kind"] == "cloud", seen.text
    out = _job(pclient, pclient.post(f"{API}/import/objects", json={"files": [str(path)]}))
    return pclient, path, out["cloud_ids"][0]


def test_properties_and_attribute_pages_from_the_blobs(imported: tuple[Any, Path, int]) -> None:
    c, _path, cloud_id = imported
    tree = c.get(f"{API}/lines/files").json()["files"]
    layer = tree[0]["layers"][0]
    assert layer["id"] == cloud_id and layer["points_only"] and layer["point_count"] == 1_000_000
    card = c.get(f"{API}/lines/layers/{cloud_id}").json()["cloud"]
    assert card["modes"] == ["class"] and card["parts"] >= 2 and len(card["levels"]) == 5
    assert set(card["classes"]) == {"1", "2", "5"} and card["source"]["kind"] == "las"
    assert card["source"]["las"]["epsg"] == 32637 and card["source"]["las"]["crs"] is True
    assert "wkt" not in card["source"]["las"] and card["ground"] is None
    cloud = _cloud(c, cloud_id)
    r = c.get(f"{API}/lines/layers/{cloud_id}/attrs", params={"offset": 999_990, "limit": 50})
    assert r.status_code == 200
    page = r.json()
    assert page["total"] == 1_000_000 and len(page["rows"]) == 10
    assert [f["name"] for f in page["fields"]] == ["Класс", "Возврат", "Число возвратов"]
    ret = cloud.read("ret", 999_990, 10)
    assert [row[0] for row in page["rows"]] == cloud.read("class", 999_990, 10).tolist()
    assert [row[1:] for row in page["rows"]] == [[int(v & 15), int(v >> 4)] for v in ret]
    assert (
        page["xyz"] == cloud.read("xyz", 999_990, 10).tolist()
        and page["seq"] == cloud.read("seq", 999_990, 10).tolist()
    )
    again = c.get(f"{API}/lines/layers/{cloud_id}/attrs", params={"offset": 999_990, "limit": 50},
                  headers={"If-None-Match": r.headers["ETag"]})  # fmt: skip
    assert again.status_code == 304
    vertex = c.get(f"{API}/lines/layers/{cloud_id}/attrs", params={"scope": "vertex"}).json()
    assert vertex["fields"] == [] and vertex["rows"] == []
    # after «Фильтр земли»: `h` with its gaps as null, a new ETag
    h = np.full(cloud.count, np.nan, dtype=np.float32)
    h[::2] = 0.25
    _classify(c, cloud, h=h)
    page = c.get(f"{API}/lines/layers/{cloud_id}/attrs", params={"offset": 0, "limit": 4},
                 headers={"If-None-Match": r.headers["ETag"]}).json()  # fmt: skip
    assert (
        page["fields"][-1]["name"] == "Высота над землёй, м" and [row[-1] for row in page["rows"]] == [0.25, None] * 2
    )
    assert c.get(f"{API}/lines/layers/{cloud_id}").json()["cloud"]["modes"] == ["class", "h"]


def test_las_export_cycle_by_the_source_and_as_a_new_file(imported: tuple[Any, Path, int], tmp_path: Path) -> None:
    c, path, cloud_id = imported
    out = tmp_path / "выгрузка"
    out.mkdir()
    same = _job(c, c.post(f"{API}/lines/export", json={"path": str(out / "как было"), "format": "las",
                                                      "layer_ids": [cloud_id]}))  # fmt: skip
    assert same["las"] == "source" and same["changed"] == 0 and same["points"] == 1_000_000
    assert Path(same["path"]).read_bytes() == path.read_bytes()  # nothing changed: the source byte for byte
    cloud = _cloud(c, cloud_id)
    fresh = ((cloud.read("seq") % 7) + 1).astype(np.uint8)  # new classes by the record of the source
    cloud = _classify(c, cloud, **{"class": fresh})
    by_source = _job(c, c.post(f"{API}/lines/export", json={"path": str(out / "по исходнику.las"), "format": "las",
                                                           "layer_ids": [cloud_id], "las": "source"}))  # fmt: skip
    before, after = open_las(path), open_las(by_source["path"])
    a, b = before.records(), after.records()
    assert b.dtype == a.dtype and by_source["changed"] > 0
    assert np.array_equal(b["classification"] & 0x1F, (np.arange(len(a)) % 7 + 1).astype(np.uint8))
    for name in a.dtype.names or ():
        if name != "classification":
            assert np.array_equal(a[name], b[name]), name
    assert np.array_equal(a["classification"] & 0xE0, b["classification"] & 0xE0)  # the flags beside the class
    raw_a, raw_b = path.read_bytes(), Path(by_source["path"]).read_bytes()
    assert raw_a[: before.header.offset_to_points] == raw_b[: before.header.offset_to_points]  # header and VLRs

    new = _job(c, c.post(f"{API}/lines/export", json={"path": str(out / "новый.las"), "format": "las",
                                                     "layer_ids": [cloud_id], "las": "new"}))  # fmt: skip
    assert new["las"] == "new" and new["version"] == "1.2" and new["point_format"] == 0
    back = open_las(new["path"])
    read = back.read()
    assert np.array_equal(read.xyz, cloud.read("xyz")) and np.array_equal(read.cls, cloud.read("class"))  # bit for bit
    assert np.array_equal(read.ret, cloud.read("ret")) and back.header.epsg == 32637
    assert back.header.scale == before.header.scale and back.header.offset == before.header.offset
    again = _job(c, c.post(f"{API}/import/objects", json={"files": [new["path"]]}))
    cloud2 = _cloud(c, again["cloud_ids"][0])
    order = np.argsort(cloud2.read("seq"))
    assert np.array_equal(cloud2.read("xyz")[order], cloud.read("xyz")) and cloud2.classes == cloud.classes


def test_csv_export_back_to_a_cloud_and_the_refusals(imported: tuple[Any, Path, int], tmp_path: Path) -> None:
    c, path, cloud_id = imported
    cloud = _cloud(c, cloud_id)
    h = np.where(np.arange(cloud.count) % 3 == 0, np.nan, 0.125).astype(np.float32)
    cloud = _classify(c, cloud, h=h)
    done = _job(c, c.post(f"{API}/lines/export", json={"path": str(tmp_path / "облако"), "format": "csv",
                                                      "layer_ids": [cloud_id], "decimal": ","}))  # fmt: skip
    target = Path(done["path"])
    assert target.suffix == ".csv" and done["points"] == cloud.count and done["las"] is None
    with target.open(encoding="utf-8") as f:
        assert f.readline() == "X;Y;Z;класс;h\n" and f.readline().count(";") == 4
    options = {str(target): {"cloud": True}}
    out = _job(c, c.post(f"{API}/import/objects", json={"files": [str(target)], "options": options}))
    back = _cloud(c, out["cloud_ids"][0])
    order = np.argsort(back.read("seq"))
    assert np.array_equal(back.read("xyz")[order], cloud.read("xyz"))  # the storage order of the export, bit for bit
    assert np.array_equal(back.read("class")[order], cloud.read("class"))

    def refused(body: dict[str, Any]) -> str:
        r = c.post(f"{API}/lines/export", json={"path": str(tmp_path / "x"), **body})
        assert r.status_code == 422 and r.json()["hint"], r.text
        return str(r.json()["code"])

    other = out["cloud_ids"][0]
    assert refused({"format": "dxf", "layer_ids": [cloud_id]}) == "cloud_format"
    assert refused({"format": "csv", "layer_ids": [cloud_id, other]}) == "cloud_alone"
    assert refused({"format": "las", "layer_ids": [other], "las": "source"}) == "no_las_source"
    plain = tmp_path / "пикеты.csv"
    plain.write_text("X;Y;Z\n1;2;3\n4;5;6\n", encoding="utf-8")
    points = _job(c, c.post(f"{API}/import/objects", json={"files": [str(plain)]}))
    layer = c.get(f"{API}/lines/files/{points['objects'][0]}").json()["layers"][0]
    assert layer["cloud"] is None
    assert refused({"format": "las", "layer_ids": [layer["id"]]}) == "las_cloud_only"
    as_new = _job(c, c.post(f"{API}/lines/export", json={"path": str(tmp_path / "из csv.las"), "format": "las",
                                                        "layer_ids": [other]}))  # fmt: skip
    assert as_new["las"] == "new" and open_las(as_new["path"]).point_count == cloud.count
    # a new file of a cloud of a table: within half a step of the scale 0,001
    assert np.abs(open_las(as_new["path"]).read().xyz - back.read("xyz")).max() <= 0.0005
    del path
