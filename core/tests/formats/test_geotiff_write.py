"""GeoTIFF export: pixels without loss, georeference tags that read back to the same corners (ADR 0011 §2, 7.11)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PIL import Image

from bf_core.crs import describe
from bf_core.formats import georef as gr
from bf_core.formats.errors import FormatError
from bf_core.formats.geotiff_write import write_geotiff

TOL = 1e-9
X0, Y0 = 466570.586, 4584699.697


def picture(width: int, height: int, bands: int = 3, seed: int = 5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    shape = (height, width) if bands == 1 else (height, width, bands)
    return rng.integers(0, 255, shape, dtype=np.uint8)


def north_up(pixel: float = 2.02) -> gr.Georef:
    return gr.Georef((pixel, 0.0, X0, 0.0, -pixel, Y0), gr.WORLD)


def turned(pixel: float = 2.02, degrees: float = 0.2) -> gr.Georef:
    t = math.radians(degrees)
    return gr.Georef(
        (pixel * math.cos(t), pixel * math.sin(t), X0, pixel * math.sin(t), -pixel * math.cos(t), Y0), gr.TAB
    )


def tags_of(path: Path) -> dict[int, object]:
    with tifffile.TiffFile(path) as tif:
        return {tag.code: tag.value for tag in tif.pages.first.tags}


def test_cycle_of_a_north_up_raster(tmp_path: Path) -> None:
    pixels = picture(700, 500)
    source = tmp_path / "ortho.png"
    Image.fromarray(pixels).save(source)
    georef = north_up()
    georef.crs = describe("EPSG:32638")
    georef.elevation = 420.5
    seen: list[float] = []
    written = write_geotiff(tmp_path / "out.tif", source, georef, progress=lambda f, m: seen.append(f))
    assert (written.width, written.height, written.bands, written.dtype) == (700, 500, 3, "uint8")
    assert written.path == tmp_path / "out.tif" and written.size == written.path.stat().st_size and not written.warnings
    assert seen[0] == 0.0 and seen[-1] == 1.0 and seen == sorted(seen)
    assert np.array_equal(np.asarray(Image.open(written.path)), pixels)  # Pillow reads it, nothing is lost
    assert np.array_equal(tifffile.imread(written.path), pixels)
    tags = tags_of(written.path)
    assert 33550 in tags and 33922 in tags and 34264 not in tags  # scale + tie point: the usual north-up form
    back = gr.read_geotiff_tags(written.path)
    assert back is not None and back.source == gr.GEOTIFF
    assert np.abs(gr.corners3d(back, 700, 500) - gr.corners3d(georef, 700, 500)).max() <= TOL
    assert back.affine == georef.affine  # bit for bit
    assert back.crs is not None and back.crs.epsg == 32638 and back.elevation == 420.5
    assert not list(tmp_path.glob("*.part"))


def test_cycle_of_a_rotated_raster(tmp_path: Path) -> None:
    pixels = picture(333, 211)
    georef = turned()
    written = write_geotiff(tmp_path / "turned.tif", pixels, georef)
    tags = tags_of(written.path)
    assert 34264 in tags and 33550 not in tags  # a rotation needs the transformation matrix
    back = gr.read_geotiff_tags(written.path)
    assert back is not None and back.affine == georef.affine and back.elevation is None and back.crs is None
    assert np.abs(gr.corners3d(back, 333, 211)[:, :2] - gr.corners3d(georef, 333, 211)[:, :2]).max() <= TOL
    assert gr.rotation_deg(back.affine) == pytest.approx(0.2, abs=1e-12)
    assert np.array_equal(tifffile.imread(written.path), pixels)


def test_world_file_to_geotiff_keeps_the_pixel_centre(tmp_path: Path) -> None:
    """JGW → GeoTIFF → tags: the centre of the top-left pixel stays where the world file puts it (1e−9)."""
    world = (2.02, 0.0, 0.0, -2.02, X0, Y0)
    georef = gr.read_world("\n".join(repr(v) for v in world))
    written = write_geotiff(tmp_path / "w.tif", picture(64, 48), georef)
    back = gr.read_geotiff_tags(written.path)
    assert back is not None
    x, y = gr.apply(back.affine, 0.5, 0.5)
    assert abs(x - X0) <= TOL and abs(y - Y0) <= TOL
    x, y = gr.apply(back.affine, 63.5, 47.5)
    assert abs(x - (X0 + 63 * 2.02)) <= TOL and abs(y - (Y0 - 47 * 2.02)) <= TOL


@pytest.mark.parametrize(
    ("name", "array", "mode"),
    [
        ("grey", picture(130, 90, 1), "L"),
        ("rgba", picture(130, 90, 4), "RGBA"),
        ("u16", np.arange(130 * 90, dtype=np.uint16).reshape(90, 130) * 5, "I;16"),
        ("f32", np.linspace(-5, 5, 130 * 90, dtype=np.float32).reshape(90, 130), "F"),
    ],
)
def test_depth_and_bands_are_kept(tmp_path: Path, name: str, array: np.ndarray, mode: str) -> None:
    written = write_geotiff(tmp_path / f"{name}.tif", array, north_up())
    assert np.array_equal(tifffile.imread(written.path), array)
    opened = Image.open(written.path)
    assert opened.mode == mode and np.array_equal(np.asarray(opened), array)
    assert written.dtype == str(array.dtype) and written.bands == (1 if array.ndim == 2 else array.shape[2])
    # The same through a file on disk: what open_pixels gives is what is written.
    source = tmp_path / f"{name}_src.tif"
    tifffile.imwrite(
        source, array, **({"extrasamples": ["unassalpha"], "photometric": "rgb"} if name == "rgba" else {})
    )
    again = write_geotiff(tmp_path / f"{name}_again.tif", source, north_up())
    assert np.array_equal(tifffile.imread(again.path), array)


def test_palette_and_bilevel_sources_become_colours(tmp_path: Path) -> None:
    rgb = picture(120, 80)
    palette = Image.fromarray(rgb).quantize(32)
    palette.save(tmp_path / "p.png")
    written = write_geotiff(tmp_path / "p.tif", tmp_path / "p.png", north_up())
    assert np.array_equal(tifffile.imread(written.path), np.asarray(palette.convert("RGB")))


def test_tiles_and_bands_of_a_bigger_image(tmp_path: Path) -> None:
    pixels = picture(1300, 1100)
    written = write_geotiff(tmp_path / "big.tif", pixels, north_up(), tile=256)
    with tifffile.TiffFile(written.path) as tif:
        page = tif.pages.first
        assert page.is_tiled and (page.tilewidth, page.tilelength) == (256, 256)
        assert int(page.compression) == 8 and int(page.predictor) == 2
    assert np.array_equal(np.asarray(Image.open(written.path)), pixels)
    plain = write_geotiff(tmp_path / "plain.tif", pixels, north_up(), compression="none")
    assert np.array_equal(tifffile.imread(plain.path), pixels) and plain.size > written.size * 0.9


def test_coordinate_system_without_an_epsg_code_is_named(tmp_path: Path) -> None:
    from pyproj import CRS
    from pyproj.crs.coordinate_operation import TransverseMercatorConversion

    local = CRS.from_epsg(4284)
    conversion = TransverseMercatorConversion(0.0, 44.55, 300000.0, 0.0, 1.0)
    from pyproj.crs import ProjectedCRS

    crs = describe(ProjectedCRS(conversion=conversion, geodetic_crs=local, name="MSK local").to_wkt())
    assert crs.epsg is None
    georef = north_up()
    georef.crs = crs
    written = write_geotiff(tmp_path / "local.tif", picture(40, 30), georef)
    assert any("EPSG" in w for w in written.warnings)
    with tifffile.TiffFile(written.path) as tif:
        meta = tif.geotiff_metadata
    assert meta["GTCitationGeoKey"].startswith("MSK local") and int(meta["ProjectedCSTypeGeoKey"]) == 32767
    back = gr.read_geotiff_tags(written.path)
    assert back is not None and back.crs is None and back.affine == georef.affine and back.warnings

    geographic = gr.Georef((1e-5, 0.0, 44.5, 0.0, -1e-5, 41.7), gr.WORLD, crs=describe("EPSG:4326"))
    written = write_geotiff(tmp_path / "ll.tif", picture(40, 30), geographic)
    back = gr.read_geotiff_tags(written.path)
    assert back is not None and back.crs is not None and back.crs.epsg == 4326


def test_a_vertical_raster_cannot_be_a_geotiff(tmp_path: Path) -> None:
    georef = gr.vertical_georef(40, 30, 0.0, 0.0, 30.0, 40.0, 450.0, 300.0)
    with pytest.raises(FormatError) as info:
        write_geotiff(tmp_path / "v.tif", picture(40, 30), georef)
    assert info.value.code == "unrepresentable" and info.value.hint
    assert not (tmp_path / "v.tif").exists()


def test_bad_sources(tmp_path: Path) -> None:
    with pytest.raises(FormatError) as info:
        write_geotiff(tmp_path / "x.tif", np.zeros((4, 5, 7), dtype=np.uint8), north_up())
    assert info.value.code == "unsupported"
    (tmp_path / "empty.png").write_bytes(b"")
    with pytest.raises(FormatError) as info:
        write_geotiff(tmp_path / "x.tif", tmp_path / "empty.png", north_up())
    assert info.value.code == "empty_file"
    with pytest.raises(FormatError) as info:
        write_geotiff(tmp_path / "x.tif", picture(4, 5), north_up(), compression="lzw")
    assert info.value.code == "unsupported" and "Deflate" in info.value.hint
    assert not (tmp_path / "x.tif").exists() and not list(tmp_path.glob("*.part"))


def test_writing_can_be_cancelled_and_leaves_no_file(tmp_path: Path) -> None:
    class Cancelled(Exception):
        pass

    calls = 0

    def progress(fraction: float, message: str | None) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise Cancelled

    target = tmp_path / "cancelled.tif"
    target.write_bytes(b"old")  # an existing file stays as it was
    with pytest.raises(Cancelled):
        write_geotiff(target, picture(900, 900), north_up(), tile=128, progress=progress)
    assert target.read_bytes() == b"old" and not list(tmp_path.glob("*.part"))
