"""Georeference of rasters: world files, MapInfo TAB, Micromine GRF, GeoTIFF tags, vertical rasters (ТЗ 7.19, 9.7).

References are computed independently: exact rational arithmetic (`fractions.Fraction`) for the world-file
definition, hand-made corner coordinates for the control-point files. Tolerance 1e−9 m at UTM magnitudes.
"""

from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
import tifffile

from bf_core.formats import georef as gr
from bf_core.formats.errors import FormatError
from bf_core.formats.georef import ControlPoint, Georef

TOL = 1e-9
# The owner's sample (ТЗ 9.7): UTM 38N, pixel about 2.02 m, rotation about 0.2°.
X0, Y0, PIXEL, TURN = 466570.586, 4584699.697, 2.02, math.radians(0.2)
WORLD = (
    PIXEL * math.cos(TURN),  # A
    PIXEL * math.sin(TURN),  # D
    PIXEL * math.sin(TURN),  # B
    -PIXEL * math.cos(TURN),  # E
    X0,  # C: centre of the top-left pixel
    Y0,  # F
)


UTM38 = 'CoordSys Earth Projection 8, 104, "m", 45, 0, 0.9996, 500000, 0'


def world_text(values: tuple[float, ...], newline: str = "\n") -> str:
    return newline.join(repr(v) for v in values) + newline


def exact_world(values: tuple[float, ...], col: int, row: int) -> tuple[float, float]:
    """The definition of a world file in exact arithmetic: (col, row) number pixels, C and F are a pixel centre."""
    a, d, b, e, c, f = (Fraction(v) for v in values)
    return float(a * col + b * row + c), float(d * col + e * row + f)


def tab_text(points: list[tuple[float, float, float, float]], coordsys: str | None = None) -> str:
    lines = ["!table", "!version 300", "!charset WindowsCyrillic", "", "Definition Table", '  File "map.jpg"']
    lines.append('  Type "RASTER"')
    for k, (x, y, col, row) in enumerate(points):
        tail = "," if k < len(points) - 1 else ""
        lines.append(f'  ({x!r},{y!r}) ({col!r},{row!r}) Label "Pt {k + 1}"{tail}')
    if coordsys:
        lines.append(f"  {coordsys}")
    lines.append('  Units "m"')
    return "\n".join(lines) + "\n"


def grf_text(head: list[float], points: list[tuple[float, ...]]) -> str:
    return "\n".join([*(repr(v) for v in head), *(" ".join(repr(v) for v in p) for p in points)]) + "\n"


# ---------------------------------------------------------------- world files


def test_world_file_refers_to_the_centre_of_the_top_left_pixel() -> None:
    g = gr.read_world(world_text(WORLD))
    assert g.source == gr.WORLD and g.affine_z is None and g.elevation is None and g.points == []
    width, height = 9001, 4603
    worst = 0.0
    for col, row in [(0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1), (1234, 3210), (4500, 2301)]:
        x, y = gr.apply(g.affine, col + 0.5, row + 0.5)  # the centre of the pixel in corner coordinates
        ex, ey = exact_world(WORLD, col, row)
        worst = max(worst, abs(x - ex), abs(y - ey))
    assert worst <= TOL, worst
    # The corner of the image is half a pixel up and to the left of the first centre.
    x, y = gr.apply(g.affine, 0.0, 0.0)
    a, d, b, e, c, f = (Fraction(v) for v in WORLD)
    assert abs(x - float(c - (a + b) / 2)) <= TOL and abs(y - float(f - (d + e) / 2)) <= TOL


def test_world_file_simple_numbers_are_exact() -> None:
    g = gr.read_world("2.0\n0.0\n0.0\n-2.0\n1001.0\n2999.0\n")
    assert g.affine == (2.0, 0.0, 1000.0, 0.0, -2.0, 3000.0)
    assert gr.apply(g.affine, 0.5, 0.5) == (1001.0, 2999.0)
    assert gr.corners(g.affine, 40, 20) == [(1000.0, 3000.0), (1080.0, 3000.0), (1080.0, 2960.0), (1000.0, 2960.0)]
    assert gr.pixel_size(g.affine) == 2.0 and gr.rotation_deg(g.affine) == 0.0
    box = gr.corners3d(g, 40, 20)
    assert box.dtype == np.float64 and box.shape == (4, 3) and np.isnan(box[:, 2]).all()  # no elevation: not 0
    assert gr.corners3d(g, 40, 20, elevation=420.5)[:, 2].tolist() == [420.5] * 4


@pytest.mark.parametrize(
    "text",
    [
        "2,0\r\n0,0\r\n0,0\r\n-2,0\r\n1001,0\r\n2999,0\r\n",  # decimal commas, CRLF
        "﻿2.0\n\n0\n0\n  -2.0  \n1001\n2999\n\n",  # BOM, blank lines, spaces
        "2.0\n0\n0\n-2.0\n1001\n2999\n7\n8\n",  # extra lines are ignored
        "2.0E+00\n0\n0\n-.2e1\n1.001e3\n2999\n",  # exponents, a leading point
    ],
)
def test_world_file_spellings(text: str) -> None:
    assert gr.read_world(text).affine == (2.0, 0.0, 1000.0, 0.0, -2.0, 3000.0)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("1\n2\n3\n", "bad_world"),  # truncated
        ("", "bad_world"),
        ("2.0\n0\n0\nabc\n1001\n2999\n", "bad_world"),  # not a number
        ("2.0 0 0\n-2.0\n1001\n2999\n1\n1\n", "bad_world"),  # several numbers in a line
        ("0\n0\n0\n0\n1001\n2999\n", "degenerate"),  # the pixel has no size
        ("2\n4\n1\n2\n1001\n2999\n", "degenerate"),  # the axes of the image coincide
        ("nan\n0\n0\n-2\n1\n1\n", "bad_world"),
        ("1e999\n0\n0\n-2\n1\n1\n", "bad_world"),
    ],
)
def test_world_file_errors(text: str, code: str) -> None:
    with pytest.raises(FormatError) as info:
        gr.read_world(text)
    assert info.value.code == code and info.value.message and info.value.hint


def test_rotation_and_pixel_size() -> None:
    angle = math.radians(30.0)
    g = gr.read_world(world_text((0.5 * math.cos(angle), 0.5 * math.sin(angle), 0.5 * math.sin(angle),
                                  -0.5 * math.cos(angle), 5e5, 6e6)))  # fmt: skip
    assert gr.rotation_deg(g.affine) == pytest.approx(30.0, abs=1e-12)
    assert gr.pixel_size(g.affine) == pytest.approx(0.5, abs=1e-15)
    # A rotated image keeps right angles and its size on the ground.
    tl, tr, br, bl = (np.array(p) for p in gr.corners(g.affine, 800, 600))
    assert np.linalg.norm(tr - tl) == pytest.approx(400.0, abs=TOL)
    assert np.linalg.norm(bl - tl) == pytest.approx(300.0, abs=TOL)
    assert float(np.dot(tr - tl, bl - tl)) == pytest.approx(0.0, abs=1e-6)
    assert gr.plane_azimuth(g.affine) == pytest.approx(60.0, abs=1e-12)  # left → right: 30° north of east


# ---------------------------------------------------------------- MapInfo TAB


def test_tab_fits_control_points() -> None:
    # Exactly representable numbers: the fit must give the transform back.
    true = (2.0, 0.25, 466570.5, 0.25, -2.0, 4584699.75)
    pixels = [(0.0, 0.0), (4000.0, 0.0), (4000.0, 3000.0), (0.0, 3000.0), (1000.0, 2000.0)]
    points = [(*gr.apply(true, c, r), c, r) for c, r in pixels]
    g = gr.read_tab(tab_text(points, UTM38))
    assert g.source == gr.TAB and len(g.points) == 5 and g.points[3].label == "Pt 4"
    assert np.abs(np.array(g.affine) - np.array(true)).max() <= TOL
    for (x, y), (c, r) in zip(gr.corners(g.affine, 4000, 3000), pixels[:4], strict=True):
        ex, ey = gr.apply(true, c, r)
        assert abs(x - ex) <= TOL and abs(y - ey) <= TOL
    assert g.crs is not None and g.crs.epsg == 32638
    assert max(p["d"] for p in gr.deviations(g, g.points)) <= TOL


def test_tab_residuals_of_an_overdetermined_fit() -> None:
    """Seven points that no affine transform fits: the residuals match an independent least-squares solution."""
    rng = np.random.default_rng(7)
    pix = np.array([[0, 0], [5000, 0], [5000, 4000], [0, 4000], [2500, 2000], [1000, 3500], [4200, 600]], float)
    xy = np.column_stack([X0 + 2.02 * pix[:, 0], Y0 - 2.02 * pix[:, 1]]) + rng.uniform(-4.6, 4.6, (7, 2))
    g = gr.read_tab(tab_text([(x, y, c, r) for (x, y), (c, r) in zip(xy.tolist(), pix.tolist(), strict=True)]))
    # Reference: an independent least-squares solution on centred data (well conditioned).
    m = np.column_stack([pix - pix.mean(axis=0), np.ones(7)])
    centre = xy.mean(axis=0)
    fitted = m @ np.linalg.lstsq(m, xy - centre, rcond=None)[0] + centre
    devs = gr.deviations(g, g.points)
    assert np.allclose([p["dx"] for p in devs], fitted[:, 0] - xy[:, 0], atol=1e-7, rtol=0)
    assert np.allclose([p["dy"] for p in devs], fitted[:, 1] - xy[:, 1], atol=1e-7, rtol=0)
    assert all(p["d"] == pytest.approx(math.hypot(p["dx"], p["dy"]), abs=1e-12) for p in devs)
    assert 0.5 < max(p["d"] for p in devs) < 7.0
    assert g.crs is None  # no CoordSys clause


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ('!table\nDefinition Table\n  Type NATIVE Charset "WindowsCyrillic"\n', "not_raster_tab"),
        (tab_text([(1.0, 2.0, 0.0, 0.0), (3.0, 2.0, 10.0, 0.0)]), "few_points"),
        (tab_text([(0.0, 0.0, 0.0, 0.0), (1.0, 1.0, 10.0, 10.0), (2.0, 2.0, 20.0, 20.0)]), "collinear"),
        ("", "not_raster_tab"),
    ],
)
def test_tab_errors(text: str, code: str) -> None:
    with pytest.raises(FormatError) as info:
        gr.read_tab(text)
    assert info.value.code == code and info.value.hint


def test_tab_without_a_known_coordinate_system_is_loaded_with_a_warning() -> None:
    points = [(0.0, 0.0, 0.0, 0.0), (10.0, 0.0, 10.0, 0.0), (0.0, -10.0, 0.0, 10.0)]
    g = gr.read_tab(tab_text(points, 'CoordSys NonEarth Units "m"'))
    assert g.crs is None and any("систем" in w.lower() for w in g.warnings)


# ---------------------------------------------------------------- Micromine GRF

GRF_HEAD = [1000.0, 3000.0, 250.0, 2.0, 2.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]


def test_grf_header_only() -> None:
    g = gr.read_grf(grf_text(GRF_HEAD, []))
    assert g.source == gr.GRF and g.elevation == 250.0 and g.affine_z is None
    assert g.affine == (2.0, 0.0, 1000.0, 0.0, -2.0, 3000.0)  # the header gives the corner, rows go down
    turned = [1000.0, 3000.0, 250.0, 2.0, 2.0, math.cos(0.1), -math.sin(0.1), 0.0, math.sin(0.1), math.cos(0.1), 0.0]
    t = gr.read_grf(grf_text(turned, []))
    assert gr.rotation_deg(t.affine) == pytest.approx(math.degrees(0.1), abs=1e-12)
    assert gr.pixel_size(t.affine) == pytest.approx(2.0, abs=1e-15)


def test_grf_control_points_give_the_transform_and_the_elevation() -> None:
    points = [(0.0, 0.0, 1000.0, 3000.0, 250.0, 1.0), (40.0, 0.0, 1080.0, 3000.0, 250.0, 1.0),
              (40.0, 20.0, 1080.0, 2960.0, 250.0, 1.0), (0.0, 20.0, 1000.0, 2960.0, 250.0, 1.0)]  # fmt: skip
    g = gr.read_grf(grf_text([0.0, 0.0, 250.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0], points))
    assert np.abs(np.array(g.affine) - np.array((2.0, 0.0, 1000.0, 0.0, -2.0, 3000.0))).max() <= TOL
    assert g.elevation == 250.0 and g.affine_z is None and len(g.points) == 4 and g.points[0].z == 250.0
    assert not gr.is_vertical(g, 40, 20)


def test_grf_with_heights_is_a_vertical_raster() -> None:
    """A section scan: 400 × 200 px, from (1000, 5000) to (1300, 5400) in plan, from 600 m down to 350 m."""
    expected = np.array([[1000.0, 5000.0, 600.0], [1300.0, 5400.0, 600.0], [1300.0, 5400.0, 350.0],
                         [1000.0, 5000.0, 350.0]])  # fmt: skip
    pixels = [(0.0, 0.0), (400.0, 0.0), (400.0, 200.0), (0.0, 200.0)]
    points = [(c, r, *xyz, 1.0) for (c, r), xyz in zip(pixels, expected.tolist(), strict=True)]
    g = gr.read_grf(grf_text([0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0], points))
    assert g.source == gr.GRF and g.affine_z is not None and g.elevation is None
    box = gr.corners3d(g, 400, 200)
    assert box.dtype == np.float64 and box.shape == (4, 3)
    assert np.abs(box - expected).max() <= TOL
    assert gr.is_vertical(g, 400, 200)
    assert gr.plane_azimuth(g.affine) == pytest.approx(math.degrees(math.atan2(300.0, 400.0)), abs=1e-9)
    assert gr.pixel_size(g.affine, g.affine_z) == pytest.approx(1.25, abs=1e-12)
    # «Сдвиг −5» lowers every corner by 5 m and moves nothing in plan (ТЗ 7.19).
    down = gr.shift_z(g, -5.0)
    moved = gr.corners3d(down, 400, 200)
    assert np.abs(moved[:, 2] - (expected[:, 2] - 5.0)).max() <= TOL and np.array_equal(moved[:, :2], box[:, :2])
    assert gr.corners3d(g, 400, 200)[0, 2] == pytest.approx(600.0, abs=TOL)  # the source is not changed


def test_shift_of_a_plan_raster_needs_an_elevation() -> None:
    g = gr.read_world("2.0\n0\n0\n-2.0\n1001\n2999\n")
    with pytest.raises(FormatError) as info:
        gr.shift_z(g, -5.0)
    assert info.value.code == "no_elevation" and info.value.hint
    g.elevation = 420.5
    assert gr.shift_z(g, -20.5).elevation == 400.0


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("1000\n3000\n", "bad_grf"),
        ("x\ny\nz\n1\n1\n", "bad_grf"),
        (grf_text([0.0, 0.0, 0.0, 0.0, 0.0], []), "degenerate"),
    ],
)
def test_grf_errors(text: str, code: str) -> None:
    with pytest.raises(FormatError) as info:
        gr.read_grf(text)
    assert info.value.code == code and info.value.hint


# ---------------------------------------------------------------- manual vertical placement


def test_vertical_georef_on_a_section_line() -> None:
    g = gr.vertical_georef(400, 200, 0.0, 0.0, 30.0, 40.0, 450.0, 300.0)
    assert g.source == gr.SECTION and gr.is_vertical(g, 400, 200)
    expected = [[0.0, 0.0, 450.0], [30.0, 40.0, 450.0], [30.0, 40.0, 300.0], [0.0, 0.0, 300.0]]
    assert np.abs(gr.corners3d(g, 400, 200) - np.array(expected)).max() <= TOL
    assert gr.plane_azimuth(g.affine) == pytest.approx(math.degrees(math.atan2(30.0, 40.0)), abs=1e-12)
    for args in ((0.0, 0.0, 0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 10.0, 0.0, 5.0, 5.0)):
        with pytest.raises(FormatError) as info:
            gr.vertical_georef(400, 200, *args)
        assert info.value.code == "degenerate" and info.value.hint


# ---------------------------------------------------------------- GeoTIFF tags


def _tiff(path: Path, extratags: list[tuple], shape: tuple[int, int] = (20, 40)) -> Path:
    tifffile.imwrite(path, np.zeros(shape, dtype=np.uint8), extratags=extratags)
    return path


def _keys(*entries: tuple[int, int]) -> tuple[int, str, int, tuple[int, ...], bool]:
    flat = [1, 1, 0, len(entries)]
    for key, value in entries:
        flat += [key, 0, 1, value]
    return (34735, "H", len(flat), tuple(flat), True)


def test_geotiff_scale_and_tiepoint(tmp_path: Path) -> None:
    tags = [
        (33550, "d", 3, (PIXEL, PIXEL, 0.0), True),
        (33922, "d", 6, (0.0, 0.0, 0.0, X0, Y0, 0.0), True),
        _keys((1024, 1), (1025, 1), (3072, 32638)),
    ]
    g = gr.read_geotiff_tags(_tiff(tmp_path / "a.tif", tags))
    assert g is not None and g.source == gr.GEOTIFF and g.elevation is None
    assert g.affine == (PIXEL, 0.0, X0, 0.0, -PIXEL, Y0)  # PixelIsArea: the tie point is a corner
    assert g.crs is not None and g.crs.epsg == 32638


def test_geotiff_pixel_is_point_and_a_shifted_tiepoint(tmp_path: Path) -> None:
    tags = [
        (33550, "d", 3, (2.0, 2.0, 0.0), True),
        (33922, "d", 6, (10.0, 5.0, 0.0, 1021.0, 2989.0, 0.0), True),  # pixel (10, 5) is at (1021, 2989)
        _keys((1025, 2)),
    ]
    g = gr.read_geotiff_tags(_tiff(tmp_path / "p.tif", tags))
    # The centre of pixel (0, 0) is at (1001, 2999): the same raster as the world file of the tests above.
    assert g is not None and g.affine == (2.0, 0.0, 1000.0, 0.0, -2.0, 3000.0) and g.crs is None


def test_geotiff_transformation_matrix_and_elevation(tmp_path: Path) -> None:
    a, b, c, d, e, f = 1.5, 0.5, 466570.5, 0.5, -1.5, 4584699.75
    matrix = (a, b, 0.0, c, d, e, 0.0, f, 0.0, 0.0, 0.0, 420.5, 0.0, 0.0, 0.0, 1.0)
    g = gr.read_geotiff_tags(_tiff(tmp_path / "m.tif", [(34264, "d", 16, matrix, True), _keys((1025, 1))]))
    assert g is not None and g.affine == (a, b, c, d, e, f) and g.elevation == 420.5
    assert gr.rotation_deg(g.affine) == pytest.approx(math.degrees(math.atan2(0.5, 1.5)), abs=1e-12)


def test_geotiff_without_tags_and_files_that_are_not_tiff(tmp_path: Path) -> None:
    assert gr.read_geotiff_tags(_tiff(tmp_path / "plain.tif", [])) is None
    other = tmp_path / "x.jpg"
    other.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    assert gr.read_geotiff_tags(other) is None
    broken = tmp_path / "cut.tif"
    broken.write_bytes(b"II*\x00\x08\x00\x00\x00\x05\x00")
    assert gr.read_geotiff_tags(broken) is None
    assert gr.read_geotiff_tags(tmp_path / "missing.tif") is None


def test_geotiff_with_an_unknown_epsg_code_keeps_the_transform(tmp_path: Path) -> None:
    tags = [
        (33550, "d", 3, (1.0, 1.0, 0.0), True),
        (33922, "d", 6, (0.0, 0.0, 0.0, 100.0, 200.0, 0.0), True),
        _keys((1024, 1), (1025, 1), (3072, 64999)),
    ]
    g = gr.read_geotiff_tags(_tiff(tmp_path / "u.tif", tags))
    assert g is not None and g.affine == (1.0, 0.0, 100.0, 0.0, -1.0, 200.0)
    assert g.crs is None and any("64999" in w for w in g.warnings)


# ---------------------------------------------------------------- choosing among the files


def _image(folder: Path, name: str = "map.jpg") -> Path:
    path = folder / name
    path.write_bytes(b"\xff\xd8\xff\xe0")  # only the name matters here
    return path


def test_world_file_wins_over_tab_and_tab_deviations_are_reported(tmp_path: Path) -> None:
    """ТЗ 7.19: «При TFW и TAB используется TFW, отклонения по TAB — в свойствах»."""
    image = _image(tmp_path, "map.tif")
    (tmp_path / "map.tfw").write_text("2.0\n0.0\n0.0\n-2.0\n1001.0\n2999.0\n")
    points = [(1000.0, 3000.0, 0.0, 0.0), (1080.0, 3000.0, 40.0, 0.0), (1080.0, 2960.0, 40.0, 20.0),
              (1000.5, 2958.75, 0.0, 20.0)]  # the last point is off by (−0.5, +1.25)  # fmt: skip
    (tmp_path / "MAP.TAB").write_text(tab_text(points, UTM38))
    out = gr.read_georef(image, 40, 20)
    assert out.georef is not None and out.georef.source == gr.WORLD
    assert out.georef.affine == (2.0, 0.0, 1000.0, 0.0, -2.0, 3000.0)
    assert out.georef.crs is not None and out.georef.crs.epsg == 32638  # the CRS comes from the other file
    assert sorted(out.used) == [".tab", ".tfw"]
    (check,) = out.checks
    assert check["source"] == gr.TAB and len(check["points"]) == 4
    assert [p["d"] for p in check["points"][:3]] == [0.0, 0.0, 0.0]
    last = check["points"][3]
    assert last["label"] == "Pt 4" and last["dx"] == pytest.approx(-0.5, abs=TOL)
    assert last["dy"] == pytest.approx(1.25, abs=TOL)
    assert check["max"] == pytest.approx(math.hypot(0.5, 1.25), abs=TOL)


def test_tfw_and_tab_that_agree(tmp_path: Path) -> None:
    """ADR 0011 7.10: «TFW + TAB совпадают» — the two files of one raster give the same corners."""
    image = _image(tmp_path, "ortho.tif")
    (tmp_path / "ortho.tfw").write_text(world_text((2.0, 0.25, 0.25, -2.0, 466571.625, 4584698.875)))
    affine = gr.read_world((tmp_path / "ortho.tfw").read_text()).affine
    pixels = [(0.0, 0.0), (6000.0, 0.0), (6000.0, 4000.0), (0.0, 4000.0)]
    (tmp_path / "ortho.tab").write_text(tab_text([(*gr.apply(affine, c, r), c, r) for c, r in pixels]))
    both = gr.read_georef(image, 6000, 4000)
    assert both.georef is not None and both.georef.source == gr.WORLD and both.checks[0]["max"] <= TOL
    only_tab = gr.read_georef(image, 6000, 4000, {".tab": tmp_path / "ortho.tab"})
    assert only_tab.georef is not None and only_tab.georef.source == gr.TAB
    a = gr.corners3d(both.georef, 6000, 4000)[:, :2]
    b = gr.corners3d(only_tab.georef, 6000, 4000)[:, :2]
    assert np.abs(a - b).max() <= TOL


def test_priority_geotiff_world_tab_grf(tmp_path: Path) -> None:
    tags = [(33550, "d", 3, (1.0, 1.0, 0.0), True), (33922, "d", 6, (0.0, 0.0, 0.0, 100.0, 200.0, 0.0), True)]
    image = _tiff(tmp_path / "s.tif", tags)
    (tmp_path / "s.tfw").write_text("2.0\n0\n0\n-2.0\n1001\n2999\n")
    (tmp_path / "s.grf").write_text(grf_text(GRF_HEAD, []))
    out = gr.read_georef(image, 40, 20)
    assert out.georef is not None and out.georef.source == gr.GEOTIFF
    assert out.georef.elevation == 250.0  # the height comes from the GRF
    assert out.checks == []  # none of the other files has control points
    world = gr.read_georef(image, 40, 20, {".tfw": tmp_path / "s.tfw", ".grf": tmp_path / "s.grf"}, geotiff=False)
    assert world.georef is not None and world.georef.source == gr.WORLD and world.georef.elevation == 250.0
    grf = gr.read_georef(image, 40, 20, {".grf": tmp_path / "s.grf"}, geotiff=False)
    assert grf.georef is not None and grf.georef.source == gr.GRF


def test_a_broken_sidecar_is_a_warning_when_another_file_gives_the_georeference(tmp_path: Path) -> None:
    image = _image(tmp_path)
    (tmp_path / "map.jgw").write_text("1\n2\n3\n")
    (tmp_path / "map.tab").write_text(tab_text([(0.0, 0.0, 0.0, 0.0), (10.0, 0.0, 10.0, 0.0), (0.0, -10.0, 0.0, 10.0)]))
    out = gr.read_georef(image, 10, 10)
    assert out.georef is not None and out.georef.source == gr.TAB
    assert any(".jgw" in w for w in out.warnings)
    nothing = gr.read_georef(_image(tmp_path, "bare.png"), 10, 10)
    assert nothing.georef is None and nothing.checks == [] and nothing.used == {}
    on_section = lambda w, h: gr.vertical_georef(w, h, 0, 0, 5, 0, 9, 1)  # noqa: E731
    placed = gr.read_georef(tmp_path / "bare.png", 10, 10, fallback=on_section)
    assert placed.georef is not None and placed.georef.source == gr.SECTION


def test_sidecars_are_found_by_name_whatever_the_case(tmp_path: Path) -> None:
    image = _image(tmp_path, "Карта 1.JPG")
    for name in ("карта 1.jgw", "Карта 1.TAB", "Карта 1.jpg.grf", "Карта 1.prj", "other.jgw", "Карта 1.wld"):
        (tmp_path / name).write_text("x")
    found = gr.find_sidecars(image)
    assert {k: v.name for k, v in found.items()} == {
        ".jgw": "карта 1.jgw", ".tab": "Карта 1.TAB", ".grf": "Карта 1.jpg.grf", ".prj": "Карта 1.prj",
        ".wld": "Карта 1.wld",
    }  # fmt: skip
    # The world file of the image's own type is preferred over the generic .wld.
    (tmp_path / "карта 1.jgw").write_text("2.0\n0\n0\n-2.0\n1001\n2999\n")
    (tmp_path / "Карта 1.wld").write_text("3.0\n0\n0\n-3.0\n1\n1\n")
    for name in ("Карта 1.TAB", "Карта 1.jpg.grf", "Карта 1.prj"):
        (tmp_path / name).unlink()
    out = gr.read_georef(image, 40, 20)
    assert out.georef is not None and out.georef.affine[0] == 2.0
    assert any(".wld" in w for w in out.warnings)


def test_prj_gives_the_coordinate_system(tmp_path: Path) -> None:
    from pyproj import CRS

    image = _image(tmp_path)
    (tmp_path / "map.jgw").write_text("2.0\n0\n0\n-2.0\n1001\n2999\n")
    (tmp_path / "map.prj").write_text(CRS.from_epsg(32638).to_wkt("WKT1_ESRI"))
    out = gr.read_georef(image, 40, 20)
    assert out.georef is not None and out.georef.crs is not None and out.georef.crs.epsg == 32638
    (tmp_path / "map.prj").write_text("not a coordinate system")
    out = gr.read_georef(image, 40, 20)
    assert out.georef is not None and out.georef.crs is None and any(".prj" in w for w in out.warnings)


def test_huge_and_binary_sidecars_do_not_hang(tmp_path: Path) -> None:
    image = _image(tmp_path)
    (tmp_path / "map.jgw").write_bytes(b"1\n" * (3 << 20))  # a 6 MB "world file"
    (tmp_path / "map.tab").write_bytes(bytes(range(256)) * 64)
    out = gr.read_georef(image, 10, 10)
    assert out.georef is None and len(out.warnings) == 2
    many = tab_text([(float(k), float(k * k % 97), float(k), float(k % 13)) for k in range(20_000)])
    with pytest.raises(FormatError) as info:
        gr.read_tab(many)
    assert info.value.code == "too_large"


def test_world_text_reads_back_to_the_same_transform() -> None:
    """Georeference → world file → georeference: the corners stay within 1e−9 (export of a raster with its file)."""
    g = gr.read_world(world_text(WORLD))
    text = gr.world_text(g)
    assert len(text.splitlines()) == 6 and text.endswith("\n")
    back = gr.read_world(text)
    assert np.abs(gr.corners3d(back, 9001, 4603)[:, :2] - gr.corners3d(g, 9001, 4603)[:, :2]).max() <= TOL
    assert [float(v) for v in text.split()[:4]] == [WORLD[0], WORLD[1], WORLD[2], WORLD[3]]
    assert abs(float(text.split()[4]) - X0) <= TOL and abs(float(text.split()[5]) - Y0) <= TOL
    simple = gr.read_world("2.0\n0.0\n0.0\n-2.0\n1001.0\n2999.0\n")
    assert gr.world_text(simple) == "2.0\n0.0\n0.0\n-2.0\n1001.0\n2999.0\n"
    with pytest.raises(FormatError) as info:
        gr.world_text(gr.vertical_georef(400, 200, 0.0, 0.0, 30.0, 40.0, 450.0, 300.0))
    assert info.value.code == "unrepresentable" and info.value.hint


def test_geotiff_tags_of_a_file_stored_without_its_extension(tmp_path: Path) -> None:
    tags = [(33550, "d", 3, (2.0, 2.0, 0.0), True), (33922, "d", 6, (0.0, 0.0, 0.0, 1000.0, 3000.0, 0.0), True)]
    stored = _tiff(tmp_path / "ortho.tif", tags).rename(tmp_path / "upload-0007")
    out = gr.read_georef(stored, 40, 20, {})
    assert out.georef is not None and out.georef.source == gr.GEOTIFF
    assert out.georef.affine == (2.0, 0.0, 1000.0, 0.0, -2.0, 3000.0)


def test_description_for_the_project(tmp_path: Path) -> None:
    g = gr.read_world("2.0\n0\n0\n-2.0\n1001\n2999\n")
    d = gr.describe(g, 40, 20)
    assert d["affine"] == [2.0, 0.0, 1000.0, 0.0, -2.0, 3000.0] and d["affine_z"] is None
    assert d["source"] == "world" and d["pixel"] == 2.0 and d["rotation"] == 0.0 and d["vertical"] is False
    assert d["corners3d"] == [[1000.0, 3000.0, None], [1080.0, 3000.0, None], [1080.0, 2960.0, None],
                              [1000.0, 2960.0, None]]  # fmt: skip
    assert d["crs"] is None and d["checks"] == []
    import json

    json.dumps(d)  # no NaN, nothing that is not JSON
    v = gr.describe(gr.vertical_georef(400, 200, 0.0, 0.0, 30.0, 40.0, 450.0, 300.0), 400, 200)
    assert v["vertical"] is True and v["corners3d"][2] == [30.0, 40.0, 300.0] and v["source"] == "section"


def test_control_points_and_georef_are_plain_data() -> None:
    p = ControlPoint(1.0, 2.0, 3.0, 4.0, "A")
    g = Georef((1.0, 0.0, 0.0, 0.0, -1.0, 0.0), gr.MANUAL, points=[p])
    assert g.points[0].z is None and g.affine_z is None and g.warnings == []
    assert set(gr.WORLD_SUFFIXES) >= {".jgw", ".pgw", ".tfw", ".wld", ".jpgw", ".pngw", ".tifw", ".bpw"}
    assert gr.SOURCE_LABELS[gr.WORLD] and set(gr.SOURCE_LABELS) == set(gr.PRIORITY)
