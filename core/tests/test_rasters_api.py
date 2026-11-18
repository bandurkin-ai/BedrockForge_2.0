"""/api/v1/rasters (ТЗ 7.19; ADR 0011 §3): the tree and properties, `raster.set` with `expected_version` (409),
«Сдвинуть» (−5 lowers all four corners of a vertical raster; no elevation → 422 `no_elevation`), «На текущий
разрез», the image with ETag / 304, deleting (a raster draped on a DTM → 422 with the list), the export job."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa
from PIL import Image

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, object_refs, objects
from bf_core.formats.raster_image import read_raster
from bf_core.projects.service import projects_of
from bf_core.rasters import store as rs
from bf_core.storage.blobs import BlobStore
from tests.formats.test_georef import grf_text
from tests.test_import_job import API, wait_job

pytestmark = pytest.mark.windows

WORLD = (0.5, 0.0, 0.0, -0.5, 512_000.25, 5_912_000.75)
SCAN = np.array([[1000.0, 5000.0, 600.0], [1300.0, 5400.0, 600.0], [1300.0, 5400.0, 350.0], [1000.0, 5000.0, 350.0]])
# UTM: a vertical scan whose corners are not round numbers (a shift must not move them in plan by a bit)
DEEP = np.array(
    [
        [512_345.678_901_234, 5_912_345.123_456_789, 1234.567_891],
        [512_645.678_901_234, 5_912_745.123_456_789, 1234.567_891],
        [512_645.678_901_234, 5_912_745.123_456_789, 984.567_891],
        [512_345.678_901_234, 5_912_345.123_456_789, 984.567_891],
    ]
)


def _picture(width: int, height: int) -> np.ndarray:
    x, y = np.meshgrid(np.arange(width, dtype=np.uint8), np.arange(height, dtype=np.uint8))
    return np.dstack([x * 3, y * 5, np.full_like(x, 128)]).astype(np.uint8)


def _vertical(folder: Path, name: str, corners: np.ndarray, width: int = 40, height: int = 20) -> Path:
    path = folder / f"{name}.png"
    Image.fromarray(_picture(width, height)).save(path)
    pixels = [(0.0, 0.0), (float(width), 0.0), (float(width), float(height)), (0.0, float(height))]
    points = [(c, r, *xyz, 1.0) for (c, r), xyz in zip(pixels, corners.tolist(), strict=True)]
    path.with_suffix(".grf").write_text(grf_text([0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0], points))
    return path


def add_raster(project: Any, path: Path, **kw: Any) -> int:
    """A raster as the import writes it: display copies, the object, the source file."""
    store = BlobStore(project.folder)
    read = read_raster(path)
    staged = rs.stage_displays(store, read.displays)
    with dialect.write_tx(project.engine) as conn:
        raster_id = rs.create_raster(
            conn, store, name=read.name, width=read.width, height=read.height, georef=read.georef,
            checks=read.checks, displays=staged, meta={"format": read.info.format, "filename": path.name}, **kw,
        )  # fmt: skip
        rs.attach(conn, store, raster_id, "source", store.stage_file(path), rs.IMAGE_MEDIA[read.info.format])
    return raster_id


@pytest.fixture()
def rasters(pclient, tmp_path: Path):  # type: ignore[no-untyped-def]
    """A project with a plan raster without a height (JPG + JGW), a vertical scan (PNG + GRF) and one more
    vertical scan in UTM; a raster an import is still writing."""
    pclient.post(f"{API}/projects", json={"name": "Растры"})
    project = projects_of(pclient.core).require_current()
    plan = tmp_path / "map.jpg"
    Image.fromarray(_picture(64, 48)).save(plan, quality=95)
    plan.with_suffix(".jgw").write_text("".join(f"{v!r}\n" for v in WORLD))
    ids = {
        "plan": add_raster(project, plan),
        "scan": add_raster(project, _vertical(tmp_path, "scan", SCAN)),
        "deep": add_raster(project, _vertical(tmp_path, "deep", DEEP)),
        "busy": add_raster(project, _vertical(tmp_path, "busy", SCAN), state="building", job_id=5),
    }
    return {"client": pclient, "project": project, **ids}


def error_of(r: Any, status: int, code: str) -> dict[str, Any]:
    body = r.json()
    assert r.status_code == status and body["code"] == code, r.text
    assert body["message"] and body["hint"]  # Russian text with a hint, every time
    return body


def corners(raster: dict[str, Any]) -> np.ndarray:
    return np.array(raster["georef"]["corners3d"], dtype=np.float64)


def test_tree_and_properties(rasters) -> None:  # type: ignore[no-untyped-def]
    c = rasters["client"]
    shown = c.get(f"{API}/rasters").json()["rasters"]
    assert [r["name"] for r in shown] == ["map", "scan", "deep"]  # the one being imported is not in the tree
    plan = c.get(f"{API}/rasters/{rasters['plan']}").json()
    assert plan == shown[0]
    assert (plan["width"], plan["height"], plan["version"], plan["opacity"]) == (64, 48, 1, 1.0)
    assert plan["elevation"] is None and not plan["vertical"] and not plan["has_elevation"]
    g = plan["georef"]
    assert g["source"] == "world" and g["pixel"] == 0.5 and g["rotation"] == 0.0 and g["checks"] == []
    a, b, cc, d, e, f = g["affine"]  # the centre of the top-left pixel is where the world file puts it
    assert abs(a * 0.5 + b * 0.5 + cc - WORLD[4]) <= 1e-9 and abs(d * 0.5 + e * 0.5 + f - WORLD[5]) <= 1e-9
    assert g["corners3d"][0] == [512000.0, 5912001.0, None] and g["corners3d"][2] == [512032.0, 5911977.0, None]
    assert plan["displays"][0]["width"] == 64 and set(plan["images"]) == {"display0", "display1"}
    scan = c.get(f"{API}/rasters/{rasters['scan']}").json()
    assert scan["vertical"] and scan["has_elevation"] and scan["elevation"] is None
    assert np.abs(corners(scan) - SCAN).max() <= 1e-9 and scan["georef"]["azimuth"] == pytest.approx(36.869_897_6)
    error_of(c.get(f"{API}/rasters/999"), 404, "raster_not_found")
    error_of(c.get(f"{API}/rasters/{rasters['busy']}"), 404, "raster_not_found")


def test_raster_set_with_expected_version(rasters) -> None:  # type: ignore[no-untyped-def]
    c = rasters["client"]
    url = f"{API}/rasters/{rasters['plan']}"
    out = c.patch(url, json={"expected_version": 1, "elevation": "250,5", "opacity": 0.4, "name": " Ортофото  2026 "})
    assert out.status_code == 200, out.text
    r = out.json()
    assert (r["name"], r["elevation"], r["opacity"], r["version"]) == ("Ортофото 2026", 250.5, 0.4, 2)
    assert [p[2] for p in r["georef"]["corners3d"]] == [250.5] * 4 and r["extent"][2] == r["extent"][5] == 250.5
    assert r["has_elevation"] and c.get(url).json() == r

    stale = error_of(c.patch(url, json={"expected_version": 1, "opacity": 0.9}), 409, "version_conflict")
    assert stale["raster"]["version"] == 2 and stale["raster"]["opacity"] == 0.4  # the current raster comes back
    error_of(c.patch(url, json={"opacity": 0.9}), 422, "bad_request")  # no version at all
    for bad in (0.01, 1.5, "много"):
        code = "not_a_number" if isinstance(bad, str) else "bad_opacity"
        error_of(c.patch(url, json={"expected_version": 2, "opacity": bad}), 422, code)
    error_of(c.patch(url, json={"expected_version": 2, "elevation": "высоко"}), 422, "not_a_number")
    error_of(c.patch(url, json={"expected_version": 2, "name": "  "}), 422, "name_empty")
    error_of(c.patch(url, json={"expected_version": 2, "name": "SCAN"}), 409, "name_taken")
    assert c.get(url).json() == r  # a refused change changes nothing

    cleared = c.patch(url, json={"expected_version": 2, "elevation": None}).json()  # under the lowest object again
    assert cleared["elevation"] is None and cleared["georef"]["corners3d"][0][2] is None and cleared["version"] == 3
    assert cleared["opacity"] == 0.4 and cleared["extent"][2] is None  # never 0

    # a vertical raster has no single elevation; its edges and heights are set together
    scan = f"{API}/rasters/{rasters['scan']}"
    error_of(c.patch(scan, json={"expected_version": 1, "elevation": 100}), 422, "vertical_raster")
    edges = {"x1": 2000, "y1": "6000,5", "x2": 2100, "y2": 6000.5, "z_top": "700", "z_bottom": 650}
    moved = c.patch(scan, json={"expected_version": 1, "vertical": edges}).json()
    want = [[2000.0, 6000.5, 700.0], [2100.0, 6000.5, 700.0], [2100.0, 6000.5, 650.0], [2000.0, 6000.5, 650.0]]
    assert np.abs(corners(moved) - want).max() <= 1e-9 and moved["georef"]["source"] == "manual"
    assert (
        moved["vertical"]
        and moved["version"] == 2
        and moved["extent"] == [2000.0, 6000.5, 650.0, 2100.0, 6000.5, 700.0]
    )
    flat = {**edges, "z_bottom": 700}
    error_of(c.patch(scan, json={"expected_version": 2, "vertical": flat}), 422, "degenerate")
    error_of(c.patch(f"{API}/rasters/999", json={"expected_version": 1, "opacity": 1}), 404, "raster_not_found")


def test_shift_lowers_every_corner_of_a_vertical_raster(rasters) -> None:  # type: ignore[no-untyped-def]
    """ТЗ 7.19, приёмка: «Сдвиг −5 опускает все углы вертикального растра на 5 м»."""
    c = rasters["client"]
    for key, was in (("scan", SCAN), ("deep", DEEP)):
        url = f"{API}/rasters/{rasters[key]}"
        before = corners(c.get(url).json())
        r = c.post(f"{url}/shift", json={"dz": -5, "expected_version": 1})
        assert r.status_code == 200, r.text
        after = corners(r.json())
        assert np.abs(after[:, 2] - (was[:, 2] - 5.0)).max() <= 1e-9  # all four corners, 5 m down
        assert after[:, :2].tobytes() == before[:, :2].tobytes()  # nothing moves in plan, not by a bit
        assert r.json()["version"] == 2 and r.json()["vertical"] and r.json()["elevation"] is None
        assert r.json()["extent"][2] == pytest.approx(was[:, 2].min() - 5.0, abs=1e-9)
        back = c.post(f"{url}/shift", json={"dz": "+5,0", "expected_version": 2}).json()  # text with a comma
        assert np.abs(corners(back) - was).max() <= 1e-9 and back["version"] == 3
        error_of(c.post(f"{url}/shift", json={"dz": -5, "expected_version": 2}), 409, "version_conflict")
        assert np.abs(corners(c.get(url).json()) - was).max() <= 1e-9  # a stale shift is not applied twice


def test_shift_needs_an_elevation(rasters) -> None:  # type: ignore[no-untyped-def]
    c = rasters["client"]
    url = f"{API}/rasters/{rasters['plan']}"
    err = error_of(c.post(f"{url}/shift", json={"dz": -5, "expected_version": 1}), 422, "no_elevation")
    assert "высота" in err["message"] and "свойствах" in err["hint"]
    same = c.get(url).json()
    assert same["version"] == 1 and same["elevation"] is None  # nothing moved, and not to −5 from «0»
    c.patch(url, json={"expected_version": 1, "elevation": 250.5})
    down = c.post(f"{url}/shift", json={"dz": "−5", "expected_version": 2}).json()  # the typographic minus
    assert down["elevation"] == 245.5 and [p[2] for p in down["georef"]["corners3d"]] == [245.5] * 4
    assert down["version"] == 3 and down["georef"]["affine"] == same["georef"]["affine"]
    error_of(c.post(f"{url}/shift", json={"dz": "вниз", "expected_version": 3}), 422, "not_a_number")
    error_of(c.post(f"{url}/shift", json={"dz": "", "expected_version": 3}), 422, "not_a_number")
    error_of(c.post(f"{url}/shift", json={"expected_version": 3}), 422, "bad_request")
    error_of(c.post(f"{API}/rasters/999/shift", json={"dz": 1, "expected_version": 1}), 404, "raster_not_found")


def test_place_on_the_section_line(rasters) -> None:  # type: ignore[no-untyped-def]
    c = rasters["client"]
    url = f"{API}/rasters/{rasters['plan']}"  # a plan raster goes onto the section too (ТЗ 7.19)
    error_of(c.post(f"{url}/place-on-section", json={"z_top": 500, "expected_version": 1}), 422, "no_section")
    line = {"x0": 1000.0, "y0": 5000.0, "az": 90.0, "length": 320.0, "towards": 25.0, "away": 25.0}
    put = c.put(f"{API}/view/section", json={"value": {"line": line, "clip": False}, "expected_version": 0})
    assert put.status_code == 200, put.text
    # no data with heights in the project yet: the top edge has to be given
    error_of(c.post(f"{url}/place-on-section", json={"expected_version": 1}), 422, "no_heights")
    r = c.post(f"{url}/place-on-section", json={"z_top": "500,5", "expected_version": 1})
    assert r.status_code == 200, r.text
    placed = r.json()
    # 64 × 48 px along 320 m due east: 5 m a pixel, so the bottom is 240 m below the top
    want = [[1000.0, 5000.0, 500.5], [1320.0, 5000.0, 500.5], [1320.0, 5000.0, 260.5], [1000.0, 5000.0, 260.5]]
    assert np.abs(corners(placed) - want).max() <= 1e-9
    assert placed["vertical"] and placed["elevation"] is None and placed["version"] == 2
    assert placed["georef"]["source"] == "section" and placed["georef"]["pixel"] == pytest.approx(5.0)
    error_of(c.post(f"{url}/place-on-section", json={"z_top": 1, "expected_version": 1}), 409, "version_conflict")

    # a line without a length runs within the data; without `z_top` the raster spans the heights of the data
    from bf_core.formats.lineset import from_lines
    from bf_core.lines import store as ls

    project = rasters["project"]
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        file_id = ls.create_file(conn, store, name="data.str", meta={})
        ls.put_layer(conn, store, from_lines("L", [[[900.0, 4900.0, 100.0], [1500.0, 5100.0, 420.0]]]), file_id=file_id)
    put = c.put(
        f"{API}/view/section", json={"value": {"line": {**line, "length": None}, "clip": False}, "expected_version": 1}
    )
    assert put.status_code == 200, put.text
    spans = c.post(f"{url}/place-on-section", json={"expected_version": 2}).json()
    want = [[900.0, 5000.0, 420.0], [1500.0, 5000.0, 420.0], [1500.0, 5000.0, 100.0], [900.0, 5000.0, 100.0]]
    assert np.abs(corners(spans) - want).max() <= 1e-9 and spans["version"] == 3


def test_image_with_etag(rasters) -> None:  # type: ignore[no-untyped-def]
    c = rasters["client"]
    url = f"{API}/rasters/{rasters['plan']}/image"
    r = c.get(url)
    assert r.status_code == 200 and r.headers["Content-Type"] == "image/jpeg"
    assert r.headers["Cache-Control"] == "no-cache" and int(r.headers["Content-Length"]) == len(r.content)
    info = c.get(f"{API}/rasters/{rasters['plan']}").json()
    tag = r.headers["ETag"]
    assert tag == f'"{info["images"]["display0"]}"'
    assert Image.open(io.BytesIO(r.content)).size == (64, 48)
    again = c.get(url, headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.content == b"" and again.headers["ETag"] == tag
    assert c.get(url, headers={"If-None-Match": '"0123abcd"'}).status_code == 200  # another tag: the image again
    small = c.get(url, params={"level": 1})
    assert small.status_code == 200 and small.headers["ETag"] == f'"{info["images"]["display1"]}"'
    # the picture does not change with the properties: a shift keeps the ETag
    c.patch(f"{API}/rasters/{rasters['plan']}", json={"expected_version": 1, "opacity": 0.5})
    assert c.get(url, headers={"If-None-Match": tag}).status_code == 304
    png = c.get(f"{API}/rasters/{rasters['scan']}/image")
    assert png.status_code == 200 and png.headers["Content-Type"] in ("image/jpeg", "image/png")
    error_of(c.get(url, params={"level": 2}), 422, "bad_level")
    error_of(c.get(f"{API}/rasters/999/image"), 404, "raster_not_found")
    error_of(c.get(f"{API}/rasters/{rasters['busy']}/image"), 404, "raster_not_found")


def test_delete_and_a_raster_draped_on_a_dtm(rasters) -> None:  # type: ignore[no-untyped-def]
    c = rasters["client"]
    project = rasters["project"]
    now = dialect.utcnow()
    with dialect.write_tx(project.engine) as conn:  # two DTMs wear the plan raster
        dtms = []
        for name in ("Рельеф 2026", "Рельеф карьера"):
            values = dict(kind="wireframe", name=name, name_key=dialect.name_key(name), sort_key=0.0, state="ready",
                          version=1, meta={}, created_at=now, updated_at=now)  # fmt: skip
            dtm = int(conn.execute(sa.insert(objects).values(**values)).inserted_primary_key[0])
            conn.execute(sa.insert(object_refs).values(src_id=dtm, dst_id=rasters["plan"], role=rs.DRAPE))
            dtms.append(dtm)
    url = f"{API}/rasters/{rasters['plan']}"
    err = error_of(c.delete(url), 422, "raster_in_use")
    assert err["wireframes"] == [{"id": dtms[0], "name": "Рельеф 2026"}, {"id": dtms[1], "name": "Рельеф карьера"}]
    assert "«Рельеф 2026»" in err["message"] and "«Рельеф карьера»" in err["message"]
    assert c.get(url).status_code == 200  # still there, with its files
    with dialect.write_tx(project.engine) as conn:
        conn.execute(sa.delete(object_refs).where(object_refs.c.dst_id == rasters["plan"]))
    assert c.delete(url).json() == {"deleted": rasters["plan"]}
    error_of(c.get(url), 404, "raster_not_found")
    error_of(c.delete(url), 404, "raster_not_found")
    error_of(c.delete(f"{API}/rasters/{rasters['busy']}"), 404, "raster_not_found")  # an import is writing it
    assert [r["name"] for r in c.get(f"{API}/rasters").json()["rasters"]] == ["scan", "deep"]
    with dialect.read_tx(project.engine) as conn:
        owners = set(conn.execute(sa.select(blob_owners.c.owner_id)).scalars())
        assert rasters["plan"] not in owners and rasters["scan"] in owners  # its files go with the next cleanup


def test_export_job_from_the_api(rasters, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c = rasters["client"]
    url = f"{API}/rasters/{rasters['plan']}/export"
    target = tmp_path / "out" / "map.jpg"
    error_of(c.post(url, json={"path": str(target), "format": "source"}), 404, "folder_not_found")
    target.parent.mkdir()
    error_of(c.post(url, json={"path": str(target), "format": "bmp"}), 422, "bad_format")
    error_of(c.post(f"{API}/rasters/999/export", json={"path": str(target)}), 404, "raster_not_found")
    r = c.post(url, json={"path": str(target), "format": "source"})
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"])
    assert job["status"] == "done" and job["title"] == "Экспорт растра (исходный файл)", job
    assert target.is_file() and target.with_suffix(".jgw").is_file()
