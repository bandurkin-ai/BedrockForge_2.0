"""Raster images: size check before decoding, display copies of two levels, depth and alpha (ТЗ 7.19, 9.12).

References: the pictures are synthetic (ramps and rectangles with known edges), so every pixel of a display copy
has an expected value; JPEG copies are compared with a tolerance of a few grey levels, PNG copies exactly.
"""

from __future__ import annotations

import io
import struct
import time
import zlib
from pathlib import Path

import numpy as np
import psutil
import pytest
import tifffile
from PIL import Image

from bf_core.formats import georef as gr
from bf_core.formats import raster_image as ri
from bf_core.formats.errors import FormatError


def ramp(width: int, height: int) -> np.ndarray:
    """RGB: red grows to the right, green grows down, blue is constant."""
    x = np.linspace(0, 255, width).round().astype(np.uint8)
    y = np.linspace(0, 255, height).round().astype(np.uint8)
    out = np.empty((height, width, 3), dtype=np.uint8)
    out[..., 0] = x[None, :]
    out[..., 1] = y[:, None]
    out[..., 2] = 128
    return out


def decode(copy: ri.DisplayCopy) -> np.ndarray:
    image = Image.open(io.BytesIO(copy.data))
    assert image.size == (copy.width, copy.height)
    assert Image.MIME[image.format] == copy.mime
    return np.asarray(image)


def save(path: Path, array: np.ndarray, **options: object) -> Path:
    Image.fromarray(array).save(path, **options)
    return path


def png_header(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", b"") + chunk(b"IEND", b"")


def bmp_header(width: int, height: int) -> bytes:
    return b"BM" + struct.pack("<IHHI", 54, 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, 0, 0, 0, 0, 0)


def tiff_header(width: int, height: int) -> bytes:
    entries = [(256, 4, 1, width), (257, 4, 1, height), (258, 3, 1, 8), (259, 3, 1, 1), (262, 3, 1, 1),
               (273, 4, 1, 8), (277, 3, 1, 1), (278, 4, 1, height), (279, 4, 1, width * height)]  # fmt: skip
    body = struct.pack("<H", len(entries)) + b"".join(struct.pack("<HHII", *e) for e in entries) + struct.pack("<I", 0)
    return b"II*\x00" + struct.pack("<I", 8) + body


# ---------------------------------------------------------------- header and limits


def test_probe_reads_the_header(tmp_path: Path) -> None:
    picture = ramp(300, 200)
    files = {
        "a.jpg": ("JPEG", "RGB", 3, 8),
        "a.png": ("PNG", "RGB", 3, 8),
        "a.bmp": ("BMP", "RGB", 3, 8),
        "a.tif": ("TIFF", "RGB", 3, 8),
    }
    for name, expected in files.items():
        info = ri.probe(save(tmp_path / name, picture))
        assert (info.width, info.height) == (300, 200), name
        assert (info.format, info.mode, info.bands, info.bits) == expected, name
        assert info.reader == "pillow"
    grey = ri.probe(save(tmp_path / "g.png", picture[..., 0]))
    assert (grey.mode, grey.bands) == ("L", 1)


@pytest.mark.parametrize(("name", "maker"), [("b.png", png_header), ("b.bmp", bmp_header), ("b.tif", tiff_header)])
def test_a_401_megapixel_image_is_refused_without_decoding(tmp_path: Path, name: str, maker) -> None:
    path = tmp_path / name
    path.write_bytes(maker(20050, 20000))
    process = psutil.Process()
    before, start = process.memory_info().rss, time.perf_counter()
    for call in (ri.probe, ri.display_copies, ri.read_raster):
        with pytest.raises(FormatError) as info:
            call(path)
        assert info.value.code == "too_large"
        assert "401" in info.value.message and "400" in info.value.message and info.value.hint
    assert time.perf_counter() - start < 1.0
    assert process.memory_info().rss - before < 64 << 20
    # 400 megapixels is still allowed: the header is read, nothing is decoded.
    path.write_bytes(maker(20000, 20000))
    info_ok = ri.probe(path)
    assert (info_ok.width, info_ok.height) == (20000, 20000)
    # Far above the limit (Pillow's own guard) the answer is the same.
    path.write_bytes(maker(40000, 40000))
    with pytest.raises(FormatError) as info:
        ri.probe(path)
    assert info.value.code == "too_large" and info.value.hint


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("empty.jpg", b"", "empty_file"),
        ("text.jpg", b"X;Y;Z\n1;2;3\n", "not_image"),
        ("anim.png", b"GIF89a" + b"\x01\x00\x01\x00\x80\x00\x00" + b"\x00" * 32, "not_image"),
        ("noise.tif", bytes(range(256)) * 16, "not_image"),
        ("cut.tif", b"II*\x00\x08\x00\x00\x00\x05\x00", "broken"),
    ],
)
def test_files_that_are_not_images(tmp_path: Path, name: str, data: bytes, code: str) -> None:
    path = tmp_path / name
    path.write_bytes(data)
    with pytest.raises(FormatError) as info:
        ri.probe(path)
    assert info.value.code == code and info.value.message and info.value.hint


@pytest.mark.parametrize("name", ["cut.jpg", "cut.png", "cut.tif"])
def test_a_truncated_image_is_an_error_not_a_grey_picture(tmp_path: Path, name: str) -> None:
    whole = save(tmp_path / ("whole" + Path(name).suffix), ramp(800, 600))
    path = tmp_path / name
    path.write_bytes(whole.read_bytes()[: whole.stat().st_size * 6 // 10])
    assert ri.probe(path).width == 800  # the header is there
    with pytest.raises(FormatError) as info:
        ri.display_copies(path)
    assert info.value.code in ("truncated", "broken") and info.value.hint


# ---------------------------------------------------------------- display copies


def test_display_levels_of_a_big_jpeg(tmp_path: Path) -> None:
    picture = ramp(6000, 3000)
    path = save(tmp_path / "big.jpg", picture, quality=95)
    info, copies, warnings = ri.display_copies(path)
    assert (info.width, info.height) == (6000, 3000)
    assert [(c.level, c.width, c.height, c.mime) for c in copies] == [
        (0, 4096, 2048, "image/jpeg"),
        (1, 1024, 512, "image/jpeg"),
    ]
    for copy in copies:
        got = decode(copy).astype(np.int16)
        expected = ramp(copy.width, copy.height).astype(np.int16)
        assert np.abs(got - expected).max() <= 6, copy.level  # the same picture, not flipped or shifted
    assert any("4096" in w for w in warnings)


def test_a_small_image_is_not_enlarged(tmp_path: Path) -> None:
    picture = ramp(300, 200)
    info, copies, warnings = ri.display_copies(save(tmp_path / "small.png", picture))
    assert [(c.width, c.height) for c in copies] == [(300, 200), (300, 200)]
    assert np.abs(decode(copies[0]).astype(np.int16) - picture).max() <= 6
    assert copies[0].data == copies[1].data and warnings == []


@pytest.mark.parametrize("suffix", [".png", ".jpg"])
def test_the_copy_covers_exactly_the_extent_of_the_image(tmp_path: Path, suffix: str) -> None:
    """Odd sizes: an edge at source column x must be at x · (copy width / width), within a pixel of the copy."""
    width, height = 9001, 4603
    x1, x2, y1, y2 = 3001, 8990, 1001, 4590
    picture = np.zeros((height, width), dtype=np.uint8)
    picture[y1:y2, x1:x2] = 255
    path = save(tmp_path / f"edges{suffix}", picture, **({"quality": 95} if suffix == ".jpg" else {}))
    _, copies, _ = ri.display_copies(path)
    for copy in copies:
        got = decode(copy).astype(np.float64)
        assert (copy.width, copy.height) == (ri.DISPLAY_SIZES[copy.level], round(height * ri.DISPLAY_SIZES[copy.level] / width))
        sx, sy = copy.width / width, copy.height / height
        row = got[round((y1 + y2) / 2 * sy)]
        col = got[:, round((x1 + x2) / 2 * sx)]
        # The area under the profile is the width of the rectangle; its centre of mass is its middle.
        for profile, lo, hi, scale in ((row, x1, x2, sx), (col, y1, y2, sy)):
            mass = profile.sum() / 255.0
            centre = (profile * (np.arange(len(profile)) + 0.5)).sum() / profile.sum()
            assert mass == pytest.approx((hi - lo) * scale, abs=0.75), (copy.level, suffix)
            assert centre == pytest.approx((lo + hi) / 2 * scale, abs=0.5), (copy.level, suffix)


def test_bands_give_the_same_copies_as_the_whole_image(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    noise = rng.integers(0, 255, (1300, 5000, 4), dtype=np.uint8)
    noise[..., 3] = np.where(np.arange(5000)[None, :] < 2500, 0, 255)
    path = save(tmp_path / "noise.png", noise, compress_level=1)
    whole = ri.display_copies(path, sizes=(1024, 256), band_pixels=1 << 40)[1]
    banded = ri.display_copies(path, sizes=(1024, 256), band_pixels=5000 * 7)[1]  # 7 rows: not a multiple of 4
    assert [(c.width, c.height) for c in whole] == [(1024, 266), (256, 67)]
    assert [c.data for c in whole] == [c.data for c in banded]
    # An independent reference on a smooth picture: the whole image resized at once.
    smooth = np.dstack([ramp(5000, 1300), noise[..., 3]])
    copy = ri.display_copies(save(tmp_path / "smooth.png", smooth), sizes=(1024, 256), band_pixels=5000 * 7)[1][0]
    reference = np.asarray(Image.fromarray(smooth).resize((1024, 266), Image.Resampling.LANCZOS)).astype(np.int16)
    got = decode(copy).astype(np.int16)
    inside = np.ones((266, 1024), dtype=bool)
    inside[:, 508:516] = False  # the edge of the transparent half
    assert got.shape == (266, 1024, 4) and np.abs(got - reference)[inside].max() <= 3


def test_alpha_is_kept_and_an_opaque_alpha_is_dropped(tmp_path: Path) -> None:
    picture = np.dstack([ramp(400, 300), np.full((300, 400), 255, dtype=np.uint8)])
    picture[:, :200, 3] = 0
    _, copies, _ = ri.display_copies(save(tmp_path / "alpha.png", picture))
    got = decode(copies[0])
    assert copies[0].mime == "image/png" and got.shape == (300, 400, 4)
    assert (got[:, :200, 3] == 0).all() and (got[:, 200:, 3] == 255).all()
    assert np.array_equal(got[:, 200:, :3], picture[:, 200:, :3])  # PNG: exact
    picture[..., 3] = 255
    _, copies, _ = ri.display_copies(save(tmp_path / "opaque.png", picture))
    assert copies[0].mime == "image/jpeg" and decode(copies[0]).shape == (300, 400, 3)


def test_palette_bilevel_grey_and_cmyk(tmp_path: Path) -> None:
    picture = ramp(256, 128)
    palette = Image.fromarray(picture).quantize(64)
    palette.save(tmp_path / "p.png")
    palette.save(tmp_path / "pt.png", transparency=int(np.asarray(palette)[0, 0]))
    Image.fromarray(picture).convert("1", dither=Image.Dither.NONE).save(tmp_path / "one.png")
    Image.fromarray(picture).convert("CMYK").save(tmp_path / "cmyk.jpg", quality=95)
    Image.fromarray(picture[..., 0]).save(tmp_path / "grey.bmp")

    got = decode(ri.display_copies(tmp_path / "p.png")[1][0]).astype(np.int16)
    assert got.shape == (128, 256, 3) and np.abs(got - np.asarray(palette.convert("RGB"))).max() <= 12
    transparent = decode(ri.display_copies(tmp_path / "pt.png")[1][0])
    assert transparent.shape == (128, 256, 4) and transparent[0, 0, 3] == 0 and transparent[-1, -1, 3] == 255
    one = decode(ri.display_copies(tmp_path / "one.png")[1][0]).astype(np.int16)
    expected = np.where(np.asarray(Image.open(tmp_path / "one.png")), 255, 0)
    assert one.ndim == 2 and np.abs(one - expected).mean() < 4  # hard edges of a bilevel image ring in a JPEG
    cmyk = decode(ri.display_copies(tmp_path / "cmyk.jpg")[1][0]).astype(np.int16)
    assert cmyk.shape == (128, 256, 3) and np.abs(cmyk - picture).mean() < 12
    grey = decode(ri.display_copies(tmp_path / "grey.bmp")[1][0]).astype(np.int16)
    assert grey.ndim == 2 and np.abs(grey - picture[..., 0]).max() <= 4


def test_exif_orientation_does_not_turn_the_pixel_grid(tmp_path: Path) -> None:
    """A world file refers to the pixels as stored: the copy must not be rotated by the EXIF tag."""
    picture = ramp(400, 200)
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.fromarray(picture).save(tmp_path / "turned.jpg", quality=95, exif=exif)
    info, copies, _ = ri.display_copies(tmp_path / "turned.jpg")
    assert (info.width, info.height) == (400, 200) and (copies[0].width, copies[0].height) == (400, 200)
    assert Image.open(io.BytesIO(copies[0].data)).getexif().get(0x0112) is None
    assert np.abs(decode(copies[0]).astype(np.int16) - picture).max() <= 6


# ---------------------------------------------------------------- depth: 16 bits and floats


def stretched(data: np.ndarray, valid: np.ndarray | None = None) -> np.ndarray:
    values = data[valid] if valid is not None else data.ravel()
    lo, hi = np.percentile(values.astype(np.float64), [2, 98])
    return np.clip(np.rint((data.astype(np.float64) - lo) / (hi - lo) * 255), 0, 255)


def test_16_bit_is_stretched_between_the_2nd_and_98th_percentiles(tmp_path: Path) -> None:
    x = np.linspace(1000, 5000, 600)
    data = (x[None, :] + np.linspace(0, 800, 400)[:, None]).astype(np.uint16)
    tifffile.imwrite(tmp_path / "u16.tif", data)
    info, copies, warnings = ri.display_copies(tmp_path / "u16.tif")
    assert (info.bits, info.bands) == (16, 1) and any("8 бит" in w for w in warnings)
    got = decode(copies[0]).astype(np.float64)
    assert got.shape == (400, 600) and np.abs(got - stretched(data)).max() <= 3
    assert got.min() == 0 and got.max() == 255


def test_float_with_gaps_keeps_the_gaps_transparent(tmp_path: Path) -> None:
    data = (np.linspace(-3.5, 12.25, 500)[None, :] * np.linspace(1, 2, 300)[:, None]).astype(np.float32)
    gaps = np.zeros(data.shape, dtype=bool)
    gaps[100:150, 200:260] = True
    with_nan = data.copy()
    with_nan[gaps] = np.nan
    tifffile.imwrite(tmp_path / "f32.tif", with_nan)
    _, copies, _ = ri.display_copies(tmp_path / "f32.tif")
    got = decode(copies[0])
    assert copies[0].mime == "image/png" and got.shape == (300, 500, 2)
    assert (got[..., 1][gaps] == 0).all() and (got[..., 1][~gaps] == 255).all()
    assert np.abs(got[..., 0][~gaps].astype(np.float64) - stretched(data, ~gaps)[~gaps]).max() <= 1
    # The same picture with the gaps marked by the GDAL «no data» value.
    with_code = data.copy()
    with_code[gaps] = -9999.0
    tifffile.imwrite(tmp_path / "nodata.tif", with_code, extratags=[(42113, "s", 0, "-9999", True)])
    again = decode(ri.display_copies(tmp_path / "nodata.tif")[1][0])
    assert np.array_equal(again, got)


def test_a_flat_deep_image_does_not_divide_by_zero(tmp_path: Path) -> None:
    tifffile.imwrite(tmp_path / "flat.tif", np.full((50, 60), 1234, dtype=np.uint16))
    got = decode(ri.display_copies(tmp_path / "flat.tif")[1][0])
    assert got.shape == (50, 60) and np.ptp(got) <= 1


# ---------------------------------------------------------------- TIFF variants (ADR 0011: no imagecodecs)


def _pillow(compression: str):
    return lambda path, picture: Image.fromarray(picture).save(path, compression=compression, quality=95)


def _tifffile(**options: object):
    return lambda path, picture: tifffile.imwrite(path, picture, **options)


def _planar(path: Path, picture: np.ndarray) -> None:
    tifffile.imwrite(path, np.ascontiguousarray(np.moveaxis(picture, 2, 0)), planarconfig="separate", photometric="rgb")


TIFFS = {
    "lzw": _pillow("tiff_lzw"),
    "jpeg": _pillow("jpeg"),
    "deflate": _pillow("tiff_adobe_deflate"),
    "packbits": _pillow("packbits"),
    "tiles": _tifffile(tile=(128, 128)),
    "tiles_deflate_predictor": _tifffile(tile=(256, 256), compression="zlib", predictor=True),
    "bigtiff": _tifffile(bigtiff=True),
    "planar": _planar,
}


@pytest.mark.parametrize("kind", sorted(TIFFS))
def test_tiff_variants_are_read(tmp_path: Path, kind: str) -> None:
    picture = ramp(700, 500)
    path = tmp_path / f"{kind}.tif"
    TIFFS[kind](path, picture)
    info, copies, _ = ri.display_copies(path)
    assert (info.width, info.height, info.format) == (700, 500, "TIFF")
    got = decode(copies[0]).astype(np.int16)
    assert got.shape == (500, 700, 3) and np.abs(got - picture).max() <= 8, kind


def test_tiff_that_pillow_cannot_read_goes_through_tifffile(tmp_path: Path) -> None:
    x = np.linspace(0.0, 1.0, 300)
    f64 = x[None, :] * np.ones((200, 1))
    tifffile.imwrite(tmp_path / "f64.tif", f64)
    info, copies, warnings = ri.display_copies(tmp_path / "f64.tif")
    assert info.reader == "tifffile" and (info.bits, info.bands) == (64, 1) and any("8 бит" in w for w in warnings)
    assert np.abs(decode(copies[0]).astype(np.float64) - stretched(f64)).max() <= 3

    rgb16 = (ramp(300, 200).astype(np.uint16) * 16) + 100  # 12 bits of data in 16: dark without a stretch
    tifffile.imwrite(tmp_path / "rgb16.tif", rgb16, photometric="rgb", compression="zlib")
    info, copies, _ = ri.display_copies(tmp_path / "rgb16.tif")
    assert info.reader == "tifffile" and (info.bits, info.bands) == (16, 3)
    got = decode(copies[0]).astype(np.float64)
    assert got.shape == (200, 300, 3) and np.abs(got - stretched(rgb16)).max() <= 8

    rgba16 = np.dstack([rgb16, np.where(np.arange(300)[None, :] < 150, 0, 65535).astype(np.uint16) * np.ones((200, 1), np.uint16)])
    tifffile.imwrite(tmp_path / "rgba16.tif", rgba16, photometric="rgb", extrasamples=["unassalpha"])
    got = decode(ri.display_copies(tmp_path / "rgba16.tif")[1][0])
    assert got.shape == (200, 300, 4) and (got[:, :150, 3] == 0).all() and (got[:, 150:, 3] == 255).all()


# ---------------------------------------------------------------- a raster with its georeference


def test_jpeg_with_a_world_file(tmp_path: Path) -> None:
    """ТЗ 7.19: «JPG + JGW встаёт точно по левому верхнему пикселю»."""
    path = save(tmp_path / "Карта.jpg", ramp(40, 20))
    (tmp_path / "карта.JGW").write_text("2.0\n0.0\n0.0\n-2.0\n1001.0\n2999.0\n")
    seen: list[float] = []
    out = ri.read_raster(path, lambda fraction, message: seen.append(fraction))
    assert out.format == "raster" and out.name == "Карта" and (out.width, out.height) == (40, 20)
    assert out.georef is not None and out.georef.source == gr.WORLD
    assert gr.apply(out.georef.affine, 0.5, 0.5) == (1001.0, 2999.0)
    assert gr.corners3d(out.georef, 40, 20)[:, :2].tolist() == [[1000, 3000], [1080, 3000], [1080, 2960], [1000, 2960]]
    assert [c.level for c in out.displays] == [0, 1] and list(out.sidecars) == [".jgw"]
    assert seen[0] == 0.0 and seen[-1] == 1.0 and seen == sorted(seen)


def test_an_image_without_a_georeference(tmp_path: Path) -> None:
    path = save(tmp_path / "scan.png", ramp(40, 20))
    with pytest.raises(FormatError) as info:
        ri.read_raster(path)
    assert info.value.code == "no_georef" and ".jgw" in info.value.hint and "scan" in info.value.message
    bare = ri.read_raster(path, require_georef=False)
    assert bare.georef is None and len(bare.displays) == 2
    placed = ri.read_raster(path, fallback=lambda w, h: gr.vertical_georef(w, h, 0.0, 0.0, 30.0, 40.0, 450.0, 300.0))
    assert placed.georef is not None and placed.georef.source == gr.SECTION
    # A broken sidecar is named in the message.
    (tmp_path / "scan.pgw").write_text("1\n2\n")
    with pytest.raises(FormatError) as info:
        ri.read_raster(path)
    assert info.value.code == "no_georef" and ".pgw" in info.value.message


def test_reading_can_be_cancelled(tmp_path: Path) -> None:
    class Cancelled(Exception):
        pass

    path = save(tmp_path / "big.png", ramp(3000, 2000))
    (tmp_path / "big.pgw").write_text("2.0\n0.0\n0.0\n-2.0\n1001.0\n2999.0\n")
    calls = 0

    def progress(fraction: float, message: str | None) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise Cancelled

    with pytest.raises(Cancelled):
        ri.read_raster(path, progress, band_pixels=3000 * 64)
    assert calls == 4


def test_pixels_are_read_band_by_band(tmp_path: Path) -> None:
    picture = ramp(321, 123)
    path = save(tmp_path / "bands.png", picture)
    with ri.open_pixels(path) as pixels:
        assert (pixels.width, pixels.height, pixels.channels, pixels.alpha, pixels.dtype) == (321, 123, 3, False, np.uint8)
        parts = [pixels.band(y, min(123, y + 50)) for y in range(0, 123, 50)]
    assert np.array_equal(np.vstack(parts), picture)
    data = np.arange(60 * 40, dtype=np.uint16).reshape(40, 60)
    tifffile.imwrite(tmp_path / "u16.tif", data)
    with ri.open_pixels(tmp_path / "u16.tif") as pixels:
        assert (pixels.channels, pixels.dtype) == (1, np.uint16)
        assert np.array_equal(pixels.band(10, 30)[..., 0], data[10:30])
