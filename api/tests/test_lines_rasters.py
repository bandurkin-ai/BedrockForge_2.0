"""Lines (Micromine STR, Surpac STR, DXF) and georeferenced rasters (world file, TAB, GRF, GeoTIFF)."""

import io
import struct

import ezdxf
import numpy as np
import pytest
from PIL import Image

from app.formats.lines import LineFormatError, read_micromine_str, read_surpac_str
from app.formats.rasters import (
    ControlPoint, RasterFormatError, corners, fit_affine, pair_files, read_grf, read_tab, read_world,
)
from tests.dat_builder import build


def micromine_str(records, codepage="ibm-5347") -> bytes:
    """EAST, NORTH, RL as reals, JOIN as a number written as text, STRING and a Cyrillic attribute."""
    variables = [
        (b"EAST", "R", 8, 3), (b"NORTH", "R", 8, 3), (b"RL", "R", 8, 3),
        (b"JOIN", "N", 12, 0), (b"STRING", "C", 20, 0), ("Участок".encode("cp1251"), "C", 20, 0),
    ]
    encoded = [[e, n, z, str(j).rjust(12).encode(), s.encode("cp1251"), a.encode("cp1251")] for e, n, z, j, s, a in records]
    return build(codepage, variables, encoded)


SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]


def _project(client) -> int:
    return client.post("/api/projects", json={"name": "Карта"}).json()["id"]


# ---- readers


def test_micromine_str_splits_by_join_and_string(tmp_path):
    records = [(x, y, 1000.0, 1, "КОНТУР", "Восточный") for x, y in SQUARE]
    records += [(x + 100, y, 1000.0, 2, "КОНТУР", "Западный") for x, y in SQUARE[:3]]
    records += [(0.0, 0.0, 5.0, 2, "", ""), (1.0, 1.0, 6.0, 2, "", "")]  # same JOIN, other STRING: a new line
    path = tmp_path / "areas.STR"
    path.write_bytes(micromine_str(records))

    layers = {layer.name: layer for layer in read_micromine_str(path)}

    assert set(layers) == {"КОНТУР", "Без кода"}
    first, second = layers["КОНТУР"].lines
    assert first.closed and len(first.vertices) == 5 and first.attributes == {"Участок": "Восточный", "JOIN": 1}
    assert not second.closed and second.vertices[0].tolist() == [100.0, 0.0, 1000.0]
    assert layers["Без кода"].lines[0].vertices[:, 2].tolist() == [5.0, 6.0]


def test_surpac_str_reads_y_before_x_and_segments(tmp_path):
    text = "\n".join([
        "pit, 01-Jan-26,,",
        "0, 0.000, 0.000, 0.000, 0.000, 0.000, 0.000",
        "1, 200.0, 100.0, 50.0, bench",
        "1, 210.0, 100.0, 50.0, bench",
        "0, 0.000, 0.000, 0.000,",
        "1, 300.0, 100.0, 40.0,",
        "1, 300.0, 120.0, 40.0,",
        "2, 1.0, 2.0, 3.0,",
        "2, 1.0, 5.0, 3.0,",
        "0, 0.000, 0.000, 0.000, END",
    ])
    path = tmp_path / "pit.str"
    path.write_text(text)

    layers = {layer.name: layer for layer in read_surpac_str(path)}

    assert [len(layer.lines) for layer in layers.values()] == [2, 1]
    assert layers["Стринг 1"].lines[0].vertices[0].tolist() == [100.0, 200.0, 50.0]  # X, Y, Z
    assert layers["Стринг 1"].lines[0].attributes == {"D1": "bench"}
    assert layers["Стринг 2"].lines[0].vertices[:, :2].tolist() == [[2.0, 1.0], [5.0, 1.0]]
    with pytest.raises(LineFormatError):
        empty = tmp_path / "empty.str"
        empty.write_text("a\nb\nc\n")
        read_surpac_str(empty)


def test_world_file_shifts_to_pixel_corner():
    g = read_world("2.0\n0.0\n0.0\n-2.0\n1001.0\n2999.0\n")

    assert g.affine == [2.0, 0.0, 1000.0, 0.0, -2.0, 3000.0]
    assert corners(g.affine, 10, 5) == [(1000.0, 3000.0), (1020.0, 3000.0), (1020.0, 2990.0), (1000.0, 2990.0)]
    with pytest.raises(RasterFormatError):
        read_world("1\n2\n3\n")


def test_tab_fits_control_points_and_rejects_collinear():
    tab = """!table
!version 300
Definition Table
  File "map.jpg"
  Type "RASTER"
  (1000,3000) (0,0) Label "Pt 1",
  (1020,3000) (10,0) Label "Pt 2",
  (1020,2990) (10,5) Label "Pt 3",
  (1000.5,2990) (0,5) Label "Pt 4"
  CoordSys Earth Projection 8, 104, "m", 45, 0, 0.9996, 500000, 0
  Units "m"
"""
    g = read_tab(tab)

    assert g.source == "MapInfo TAB" and len(g.points) == 4 and g.points[3].label == "Pt 4"
    assert g.crs.startswith("CoordSys Earth")
    assert np.allclose(g.affine, [2.0, 0.0, 1000.125, 0.0, -2.0, 3000.0], atol=0.2)
    with pytest.raises(RasterFormatError):
        fit_affine([ControlPoint(0, 0, 0, 0), ControlPoint(1, 1, 1, 1), ControlPoint(2, 2, 2, 2)])
    with pytest.raises(RasterFormatError):
        read_tab('Type "VECTOR"')


def test_grf_uses_control_points_and_elevation():
    grf = "\n".join(["1000", "3000", "250", "2", "2", "1", "0", "0", "0", "1", "0",
                     "0 0 1000 3000 250 1", "10 0 1020 3000 250 1", "10 5 1020 2990 250 1"])
    g = read_grf(grf)

    assert g.elevation == 250.0 and len(g.points) == 3
    assert np.allclose(g.affine, [2, 0, 1000, 0, -2, 3000])


def test_pairing_by_name():
    groups, unmatched = pair_files(["Map.JPG", "map.jgw", "map.tab", "other.png", "lost.grf"])

    assert groups == {"Map.JPG": ["map.jgw", "map.tab"], "other.png": []}
    assert unmatched == ["lost.grf"]


# ---- API


def _jpeg(width=40, height=20) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "#88aa55").save(out, "JPEG")
    return out.getvalue()


def test_upload_lines_str_geometry_patch_delete(db_client, tmp_path):
    pid = _project(db_client)
    data = micromine_str([(x + 500000, y + 4600000, 1000.0, 1, "КОНТУР", "Восточный") for x, y in SQUARE])

    created = db_client.post(f"/api/projects/{pid}/lines", files={"file": ("Контуры.STR", data)})

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "lines" and body["meta"]["format"] == "Micromine STR"
    (layer,) = body["lines"]
    assert (layer["name"], layer["line_count"], layer["point_count"]) == ("КОНТУР", 1, 5)
    assert layer["stats"] == {"length": 40.0, "closed": 1, "flat": True}

    g = db_client.get(f"/api/lines/{layer['id']}/geometry").json()
    vertices = np.frombuffer(db_client.get(g["vertices"]).content, dtype="<f4").reshape(-1, 3) + g["origin"]
    offsets = np.frombuffer(db_client.get(g["offsets"]).content, dtype="<u4")
    assert offsets.tolist() == [0, 5] and db_client.get(g["closed"]).content == b"\x01"
    assert vertices[2].tolist() == [500010.0, 4600010.0, 1000.0]

    assert db_client.patch(f"/api/lines/{layer['id']}", json={"color": "#112233"}).json()["color"] == "#112233"
    assert db_client.delete(f"/api/lines/{layer['id']}").status_code == 204
    assert db_client.get(f"/api/projects/{pid}/datasets").json() == []


def test_upload_dxf_lines_by_layer(db_client, tmp_path):
    pid = _project(db_client)
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    doc.layers.add("ДОРОГИ", color=1)
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], dxfattribs={"layer": "ДОРОГИ"})
    msp.add_line((0, 0, 5), (3, 4, 5), dxfattribs={"layer": "ДОРОГИ"})
    msp.add_circle((50, 50), 5, dxfattribs={"layer": "КОЛЬЦО", "color": 3})
    face = msp.add_polyface(dxfattribs={"layer": "MESH"})  # meshes are not lines
    face.append_face([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
    path = tmp_path / "roads.dxf"
    doc.saveas(path)

    body = db_client.post(f"/api/projects/{pid}/lines", files={"file": ("roads.dxf", path.read_bytes())}).json()

    layers = {x["name"]: x for x in body["lines"]}
    assert set(layers) == {"ДОРОГИ", "КОЛЬЦО"}
    assert layers["ДОРОГИ"]["line_count"] == 2 and layers["ДОРОГИ"]["color"] == "#ff0000"
    assert layers["ДОРОГИ"]["stats"]["length"] == 25.0
    ring = layers["КОЛЬЦО"]
    assert ring["stats"]["closed"] == 1 and ring["stats"]["length"] == pytest.approx(2 * np.pi * 5, rel=0.01)


def test_upload_lines_rejects_bad_files(db_client):
    pid = _project(db_client)

    assert db_client.post(f"/api/projects/{pid}/lines", files={"file": ("a.shp", b"x")}).status_code == 415
    bad = db_client.post(f"/api/projects/{pid}/lines", files={"file": ("a.str", b"a\nb\n")})
    assert bad.status_code == 422


def test_upload_raster_with_world_file_and_tab_check(db_client):
    pid = _project(db_client)
    world = b"2.0\n0.0\n0.0\n-2.0\n1001.0\n2999.0\n"
    tab = 'Type "RASTER"\n(1000,3000) (0,0) Label "A",\n(1080,3000) (40,0) Label "B",\n(1080,2960) (40,20) Label "C"\n'.encode()

    created = db_client.post(f"/api/projects/{pid}/rasters", files=[
        ("files", ("Map.jpg", _jpeg())), ("files", ("map.jgw", world)), ("files", ("map.TAB", tab)),
    ])

    assert created.status_code == 201, created.text
    (dataset,) = created.json()
    (raster,) = dataset["rasters"]
    assert (raster["name"], raster["width"], raster["height"]) == ("Map", 40, 20)
    g = raster["georef"]
    assert g["source"] == "World-файл" and g["pixel_size"] == 2.0 and g["rotation"] == 0.0
    assert g["corners"][0] == [1000.0, 3000.0] and g["corners"][2] == [1080.0, 2960.0]
    assert g["checks"][0]["source"] == "MapInfo TAB" and g["checks"][0]["max"] == 0.0
    assert raster["extent"] == {"min": [1000.0, 2960.0], "max": [1080.0, 3000.0]}
    assert raster["elevation"] is None

    image = db_client.get(f"/api/rasters/{raster['id']}/image")
    assert image.headers["content-type"] == "image/jpeg" and Image.open(io.BytesIO(image.content)).size == (40, 20)
    patched = db_client.patch(f"/api/rasters/{raster['id']}", json={"elevation": 420.5, "opacity": 0.5}).json()
    assert (patched["elevation"], patched["opacity"]) == (420.5, 0.5)
    shifted = db_client.patch(f"/api/rasters/{raster['id']}", json={"z_shift": -20.5}).json()
    assert shifted["elevation"] == 400.0
    cleared = db_client.patch(f"/api/rasters/{raster['id']}", json={"clear_elevation": True}).json()
    assert cleared["elevation"] is None
    assert db_client.patch(f"/api/rasters/{raster['id']}", json={"z_shift": 5}).status_code == 422  # no height to shift
    assert db_client.delete(f"/api/datasets/{dataset['id']}").status_code == 204


def test_upload_geotiff_png_with_alpha_and_large_image(db_client):
    pid = _project(db_client)
    tiff = io.BytesIO()
    # PixelIsArea GeoTIFF: 5 m pixels, top-left corner at (500000, 4600000).
    Image.new("RGB", (5000, 10), "white").save(tiff, "TIFF", tiffinfo={33550: (5.0, 5.0, 0.0), 33922: (0.0, 0.0, 0.0, 500000.0, 4600000.0, 0.0)})
    png = io.BytesIO()
    Image.new("RGBA", (8, 8), (255, 0, 0, 128)).save(png, "PNG")

    body = db_client.post(f"/api/projects/{pid}/rasters", files=[
        ("files", ("dem.tif", tiff.getvalue())),
        ("files", ("logo.png", png.getvalue())),
        ("files", ("logo.pgw", b"1\n0\n0\n-1\n0.5\n7.5\n")),
    ]).json()

    tif, logo = (d["rasters"][0] for d in body)
    assert tif["georef"]["source"] == "GeoTIFF" and tif["georef"]["corners"][2] == [525000.0, 4599950.0]
    assert any("уменьшено" in n for n in tif["georef"]["notes"])  # 5000 px is above the display limit
    shown = Image.open(io.BytesIO(db_client.get(f"/api/rasters/{tif['id']}/image").content))
    assert shown.width == 4096
    assert db_client.get(f"/api/rasters/{logo['id']}/image").headers["content-type"] == "image/png"


def test_upload_raster_errors(db_client):
    pid = _project(db_client)

    def post(*files):
        return db_client.post(f"/api/projects/{pid}/rasters", files=[("files", f) for f in files])

    no_georef = post(("map.jpg", _jpeg()))
    assert no_georef.status_code == 422 and ".jgw" in no_georef.json()["detail"]
    assert post(("map.jgw", b"1\n0\n0\n-1\n0\n0\n")).status_code == 422  # a sidecar without its image
    assert post(("map.jpg", _jpeg()), ("other.jgw", b"1\n0\n0\n-1\n0\n0\n")).status_code == 422
    assert post(("map.jpg", b"not an image"), ("map.jgw", b"1\n0\n0\n-1\n0\n0\n")).status_code == 422
    assert post(("map.shp", b"x")).status_code == 415
    assert db_client.get(f"/api/projects/{pid}/datasets").json() == []


def test_demo_has_lines_and_raster(db_client):
    pid = db_client.post("/api/projects/demo").json()["id"]

    kinds = [d["kind"] for d in db_client.get(f"/api/projects/{pid}/datasets").json()]

    assert kinds == ["drillholes", "wireframes", "lines", "rasters", "rasters"]
    scan = db_client.get(f"/api/projects/{pid}/datasets").json()[-1]["rasters"][0]
    assert scan["georef"]["placement"] == "vertical" and scan["georef"]["azimuth"] == 90.0


# ---- rasters in a vertical plane


def test_grf_with_heights_makes_a_vertical_raster():
    # A 100 × 50 px scan of an east-west section: 1 m per pixel along X, 2 m per pixel down from Z = 500.
    grf = "\n".join(["0", "0", "0", "1", "1", "1", "0", "0", "0", "1", "0",
                     "0 0 1000 2000 500 1", "100 0 1100 2000 500 1", "100 50 1100 2000 400 1", "0 50 1000 2000 400 1"])
    g = read_grf(grf)

    assert g.affine_z is not None
    assert np.allclose(g.affine, [1, 0, 1000, 0, 0, 2000]) and np.allclose(g.affine_z, [0, -2, 500])


def test_vertical_georef_corners_and_errors():
    from app.formats.rasters import corners3d, is_vertical, plane_azimuth, vertical_georef

    g = vertical_georef(200, 100, 0, 0, 0, 100, 450, 350)  # a north-south section, 0.5 m × 1 m pixels

    assert corners3d(g, 200, 100) == [[0, 0, 450], [0, 100, 450], [0, 100, 350], [0, 0, 350]]
    assert is_vertical(g, 200, 100) and plane_azimuth(g.affine) == 0.0
    with pytest.raises(RasterFormatError):
        vertical_georef(10, 10, 5, 5, 5, 5, 1, 0)
    with pytest.raises(RasterFormatError):
        vertical_georef(10, 10, 0, 0, 1, 1, 3, 3)


def test_upload_grf_section_scan_and_place_on_section(db_client):
    pid = _project(db_client)
    grf = "\n".join(["0", "0", "0", "1", "1", "1", "0", "0", "0", "1", "0",
                     "0 0 1000 2000 500 1", "40 0 1040 2000 500 1", "40 20 1040 2000 460 1"]).encode()

    (dataset,) = db_client.post(f"/api/projects/{pid}/rasters", files=[("files", ("scan.jpg", _jpeg())), ("files", ("scan.grf", grf))]).json()

    (r,) = dataset["rasters"]
    g = r["georef"]
    assert g["placement"] == "vertical" and g["azimuth"] == 90.0
    assert g["corners3d"] == [[1000.0, 2000.0, 500.0], [1040.0, 2000.0, 500.0], [1040.0, 2000.0, 460.0], [1000.0, 2000.0, 460.0]]
    assert g["checks"][0]["max"] == 0.0 and g["checks"][0]["points"][2]["dz"] == 0.0
    assert r["extent"] == {"min": [1000.0, 2000.0, 460.0], "max": [1040.0, 2000.0, 500.0]}

    # Re-place it on another section line, from Z 450 down to 300.
    moved = db_client.patch(f"/api/rasters/{r['id']}", json={"vertical": {"x1": 0, "y1": 0, "x2": 30, "y2": 40, "z_top": 450, "z_bottom": 300}}).json()
    assert moved["georef"]["source"] == "Вручную по разрезу" and moved["georef"]["corners3d"][2] == [30.0, 40.0, 300.0]
    up = db_client.patch(f"/api/rasters/{r['id']}", json={"z_shift": 12.5}).json()  # a vertical raster moves with all its corners
    assert up["georef"]["corners3d"][2] == [30.0, 40.0, 312.5] and up["extent"]["max"][2] == 462.5 and up["georef"]["affine_z"][2] == 462.5
    assert moved["georef"]["azimuth"] == pytest.approx(36.87, abs=0.01)
    bad = db_client.patch(f"/api/rasters/{r['id']}", json={"vertical": {"x1": 0, "y1": 0, "x2": 0, "y2": 0, "z_top": 1, "z_bottom": 0}})
    assert bad.status_code == 422


def test_upload_without_georef_stands_on_section(db_client):
    import json

    pid = _project(db_client)
    section = json.dumps({"x1": 500, "y1": 100, "x2": 500, "y2": 300, "z_top": 420, "z_bottom": 220})

    created = db_client.post(f"/api/projects/{pid}/rasters", files=[("files", ("profile.jpg", _jpeg()))], data={"section": section})

    assert created.status_code == 201, created.text
    g = created.json()[0]["rasters"][0]["georef"]
    assert g["source"] == "Вручную по разрезу" and g["placement"] == "vertical"
    assert g["corners3d"][0] == [500.0, 100.0, 420.0] and g["corners3d"][2] == [500.0, 300.0, 220.0]
    assert any("плоскость линии разреза" in n for n in g["notes"])
    # Plan rasters keep working the old way.
    plan = db_client.post(f"/api/projects/{pid}/rasters", files=[("files", ("map.jpg", _jpeg())), ("files", ("map.jgw", b"1\n0\n0\n-1\n0.5\n9.5\n"))], data={"section": section}).json()
    assert plan[0]["rasters"][0]["georef"]["placement"] == "plan"
    assert db_client.post(f"/api/projects/{pid}/rasters", files=[("files", ("x.jpg", _jpeg()))], data={"section": "{bad"}).status_code == 422
