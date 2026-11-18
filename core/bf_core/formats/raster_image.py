"""Raster images: the size before decoding, pixels band by band, display copies of two levels (ТЗ 7.19, 9.7, 9.12;
ADR 0011 §2).

  * `probe(path)` → `ImageInfo` from the header only. An image of more than `MAX_PIXELS` (400 million) is refused
    here, before anything is decoded (`too_large`): a "bomb" costs no memory and no time.
  * `open_pixels(path)` → `Pixels`: the decoded picture read in bands of rows, as numpy arrays (rows, width,
    channels): 1 or 3 colour channels and an optional alpha, uint8 or the depth of the file (uint16, int32, float).
  * `display_copies(path)` → the copies the 3D view shows: `display0` (longer side ≤ 4096 px) and `display1`
    (≤ 1024 px), 8 bits, JPEG, or PNG when there is transparency. A JPEG is decoded at a reduced size (`draft`);
    other images are reduced by a whole factor band by band (`reduce`, the same pixels as for the whole image),
    then resized once (Lanczos) with the exact extent of the source, so a copy covers the image edge to edge.
    16-bit, 32-bit and float pictures become 8 bits by a stretch between the 2nd and 98th percentiles; NaN,
    infinities and the GDAL «no data» value are gaps: transparent, never 0. A small image is not enlarged.
  * `read_raster(path)` → `RasterRead`: the header, the georeference (`georef.read_georef`) and the copies.
Pixels come from Pillow (JPEG, PNG, BMP, TIFF with LZW, JPEG, Deflate, PackBits, tiles, BigTIFF); a TIFF Pillow
does not read, or reads with a loss (float64, several samples of 16 bits or floats), goes through tifffile when
it is not compressed or compressed with Deflate, PackBits or LZMA (ADR 0011 §6: no imagecodecs).
The EXIF orientation is ignored: a world file refers to the pixels as they are stored.
Memory: the decoded image in its own mode (Pillow cannot decode a part) plus one band and the copies.
`Image.MAX_IMAGE_PIXELS` of Pillow is raised to `MAX_PIXELS` for the process on first use.

Ported from BF `formats/rasters.py::_display_copy` / `read_raster`. Differences: the size check before decoding,
two levels, bands, the exact extent, gaps, tifffile for what Pillow cannot read, progress and cancellation
(`progress` may raise; decoding runs in a thread so that it is called at least five times a second).
Error codes: empty_file, not_image, too_large, truncated, broken, unsupported, no_georef.
"""

from __future__ import annotations

import io
import math
import struct
import threading
import warnings
import zlib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.formats import georef as gr
from bf_core.formats.errors import FormatError
from bf_core.formats.table import Progress, no_progress

MAX_PIXELS = 400_000_000
DISPLAY_SIZES = (4096, 1024)  # display0, display1: the longer side, px
BAND_PIXELS = 1 << 22  # source pixels handled at once
SAMPLE = 4_000_000  # pixels the percentiles of a deep image are taken from
JPEG_QUALITY = 90
FORMATS = ("JPEG", "PNG", "TIFF", "BMP")  # an MPO (several JPEG frames) is opened as JPEG
GDAL_NODATA = 42113
#: TIFF compressions tifffile decodes without imagecodecs: none, Deflate (two codes), PackBits, LZMA.
TIFFFILE_CODECS = frozenset({1, 8, 32946, 32773, 34925})
_MODES = {"L": "L", "1": "L", "LA": "LA", "La": "LA", "RGB": "RGB", "RGBA": "RGBA", "RGBa": "RGBA", "PA": "RGBA"}
_HINT = "Поддерживаются изображения JPG, PNG, TIFF и BMP. Откройте файл в графическом редакторе и сохраните заново."
_HINT_BROKEN = "Скопируйте файл заново или сохраните изображение ещё раз в исходной программе."


def _fail(code: str, message: str, hint: str = _HINT) -> FormatError:
    return FormatError(code, message, hint)


@dataclass
class ImageInfo:
    width: int
    height: int
    format: str  # "JPEG" | "PNG" | "TIFF" | "BMP"
    mode: str  # Pillow mode ("RGB", "I;16", "F" …) or the sample type of a TIFF read by tifffile ("float64")
    bands: int
    bits: int  # per sample
    compression: str | None = None
    reader: str = "pillow"  # "pillow" | "tifffile"
    nodata: float | None = None  # GDAL «no data» value of a TIFF


@dataclass
class DisplayCopy:
    level: int  # 0: ≤ 4096 px, 1: ≤ 1024 px
    width: int
    height: int
    data: bytes
    mime: str  # "image/jpeg" | "image/png"


@dataclass
class RasterRead:
    format: str  # "raster"
    name: str
    path: Path
    info: ImageInfo
    georef: gr.Georef | None
    checks: list[dict[str, Any]] = field(default_factory=list)  # deviations at control points (georef.resolve)
    displays: list[DisplayCopy] = field(default_factory=list)
    sidecars: dict[str, Path] = field(default_factory=dict)  # georeference files that were read
    warnings: list[str] = field(default_factory=list)

    @property
    def width(self) -> int:
        return self.info.width

    @property
    def height(self) -> int:
        return self.info.height


# ---------------------------------------------------------------- header


def _pil() -> Any:
    from PIL import Image

    if Image.MAX_IMAGE_PIXELS is not None and Image.MAX_IMAGE_PIXELS < MAX_PIXELS:
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow then refuses above 2 × MAX_PIXELS; the exact limit is ours
    return Image


def _too_large(width: int = 0, height: int = 0) -> FormatError:
    size = f" {width} × {height} пикселей ({math.ceil(width * height / 1e6)} Мпикс)" if width else ""
    return _fail(
        "too_large",
        f"Изображение{size} больше допустимых {MAX_PIXELS // 10**6} Мпикс.",
        "Уменьшите изображение или разрежьте его на части в графическом редакторе или ГИС.",
    )


def _nodata(value: Any) -> float | None:
    try:
        number = float(str(value).strip().strip("\x00"))
    except ValueError:
        return None
    return None if math.isnan(number) else number


def _tiff_info(path: Path) -> tuple[ImageInfo, bool]:
    """The first page of a TIFF as tifffile sees it, and whether tifffile can decode it without imagecodecs."""
    import tifffile

    try:
        with tifffile.TiffFile(path) as tif:
            page = tif.pages.first
            dtype, samples = page.dtype, int(page.samplesperpixel)
            tag = page.tags.get(GDAL_NODATA)
            bits = 0 if dtype is None else dtype.itemsize * 8
            codec = str(getattr(page.compression, "name", "")).lower()
            nodata = None if tag is None else _nodata(tag.value)
            size = int(page.imagewidth), int(page.imagelength)
            info = ImageInfo(*size, "TIFF", str(dtype), samples, bits, codec, "tifffile", nodata)
            readable = dtype is not None and int(page.compression) in TIFFFILE_CODECS
            readable = readable and page.axes in ("YX", "YXS", "SYX")
    except (
        OSError,
        ValueError,
        TypeError,
        IndexError,
        KeyError,
        AttributeError,
        struct.error,
        tifffile.TiffFileError,
    ) as exc:
        raise _fail("broken", "Файл TIFF повреждён или имеет неизвестное строение.", _HINT_BROKEN) from exc
    return info, bool(readable)


def _inspect(path: Path) -> tuple[ImageInfo, Any]:
    """The header: the description of the image and the opened Pillow image (None: tifffile reads the pixels)."""
    from PIL import UnidentifiedImageError

    if path.stat().st_size == 0:
        raise _fail("empty_file", "Файл изображения пуст.", "Проверьте, что файл скопирован полностью.")
    image_module = _pil()
    with path.open("rb") as f:
        tiff = f.read(4) in gr.TIFF_MAGIC
    im = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # the size is checked below; a damaged header is an error anyway
            im = image_module.open(path, formats=FORMATS)
    except image_module.DecompressionBombError as exc:
        raise _too_large() from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, struct.error, EOFError) as exc:
        if not tiff:
            raise _fail("not_image", "Файл не является изображением JPG, PNG, TIFF или BMP.") from exc
    if im is None:
        info, readable = _tiff_info(path)
        if info.width * info.height > MAX_PIXELS:
            raise _too_large(info.width, info.height)
        if not readable:
            raise _fail(
                "unsupported",
                f"TIFF такого вида не читается (тип значений {info.mode}, сжатие {info.compression}).",
                "Сохраните изображение как TIFF 8 бит, либо без сжатия или со сжатием Deflate.",
            )
        return info, None
    width, height = im.size
    if width * height > MAX_PIXELS:
        im.close()
        raise _too_large(width, height)
    mode = im.mode
    bits = 1 if mode == "1" else 16 if mode.startswith("I;16") else 32 if mode in ("I", "F") else 8
    kind = "JPEG" if im.format == "MPO" else str(im.format)
    info = ImageInfo(width, height, kind, mode, len(im.getbands()), bits, im.info.get("compression"))
    if tiff:
        deep, readable = _tiff_info(path)
        info.nodata = deep.nodata
        if readable and deep.bands >= 2 and deep.bits > 8:  # Pillow would keep only the high byte of every sample
            im.close()
            return deep, None
    return info, im


def probe(path: str | Path) -> ImageInfo:
    """The size and the kind of an image from its header; nothing is decoded."""
    info, im = _inspect(Path(path))
    if im is not None:
        im.close()
    return info


# ---------------------------------------------------------------- pixels


def _wait[T](work: Callable[[], T], tick: Progress, fraction: float, message: str) -> T:
    """Run a call that cannot report progress (a Pillow decode, resize or encode) in a thread and tick meanwhile.
    When `tick` raises (cancel) the call is left to finish on its own: its result is dropped."""
    done: list[T] = []
    failed: list[BaseException] = []

    def run() -> None:
        try:
            done.append(work())
        except BaseException as exc:  # noqa: BLE001 — handed over to the caller's thread
            failed.append(exc)

    thread = threading.Thread(target=run, name="bf-raster", daemon=True)
    thread.start()
    while thread.is_alive():
        thread.join(0.2)
        tick(fraction, message)
    if failed:
        raise failed[0]
    return done[0]


def _decode_error(exc: BaseException) -> FormatError:
    text = str(exc).lower()
    if "imagecodecs" in text:
        return _fail(
            "unsupported",
            "Сжатие этого TIFF не поддерживается.",
            "Сохраните изображение как TIFF без сжатия или со сжатием Deflate либо LZW (8 бит).",
        )
    if "truncated" in text or "not enough" in text or isinstance(exc, EOFError):
        return _fail("truncated", "Файл изображения обрезан: прочитать его целиком не удалось.", _HINT_BROKEN)
    return _fail("broken", "Изображение повреждено: его не удалось прочитать.", _HINT_BROKEN)


_DECODE_ERRORS = (OSError, ValueError, SyntaxError, EOFError, IndexError, TypeError, struct.error, zlib.error)


class Pixels:
    """A decoded picture read in bands of rows: `band(y0, y1)` → array (rows, width, channels [+ alpha])."""

    info: ImageInfo
    width: int  # of the decoded picture
    height: int
    scale: int = 1  # source pixels per decoded pixel (a JPEG decoded at a reduced size)
    channels: int = 3  # colour channels: 1 or 3
    alpha: bool = False
    dtype: Any = np.uint8
    invert: bool = False  # 0 is white (TIFF MinIsWhite)

    def band(self, y0: int, y1: int) -> np.ndarray:
        raise NotImplementedError

    def close(self) -> None:
        return None


class _PillowPixels(Pixels):
    def __init__(self, im: Any, info: ImageInfo, tick: Progress, draft: tuple[int, int] | None) -> None:
        self.info, self._im = info, im
        if draft is not None and im.format in ("JPEG", "MPO"):
            im.draft(None, draft)  # the decoder gives 1/2, 1/4 or 1/8 of the size, not less than `draft`
        try:
            _wait(im.load, tick, 0.0, "Чтение изображения")
        except _DECODE_ERRORS as exc:
            raise _decode_error(exc) from exc
        self.width, self.height = im.size
        self.scale = next((s for s in (1, 2, 4, 8) if -(-info.width // s) == self.width), 1)
        mode = im.mode
        if mode.startswith("I;16") or mode in ("I", "F"):
            self._target, self.channels = mode, 1
            self.dtype = np.dtype({"I": np.int32, "F": np.float32}.get(mode, np.uint16))
        else:
            fallback = "RGBA" if mode == "P" and "transparency" in im.info else "RGB"
            self._target = _MODES.get(mode, fallback)
            self.channels = 1 if self._target in ("L", "LA") else 3
            self.alpha = self._target in ("LA", "RGBA")

    def band(self, y0: int, y1: int) -> np.ndarray:
        part = self._im.crop((0, y0, self.width, y1))
        if part.mode != self._target:
            part = part.convert(self._target)
        arr = np.asarray(part)
        if arr.dtype != self.dtype:
            arr = arr.astype(self.dtype)  # I;16B comes big-endian
        return arr[..., None] if arr.ndim == 2 else arr

    def close(self) -> None:
        self._im.close()


class _TiffPixels(Pixels):
    def __init__(self, path: Path, info: ImageInfo, tick: Progress) -> None:
        import tifffile

        self.info = info
        self._tif = tifffile.TiffFile(path)
        try:
            page = self._tif.pages.first
            big = info.width * info.height * info.bands * info.bits // 8 > 256 << 20
            self._arr = _wait(lambda: page.asarray(out="memmap" if big else None), tick, 0.0, "Чтение изображения")
            self._first = page.axes == "SYX"  # planar: samples come first
            samples = 1 if page.axes == "YX" else int(page.samplesperpixel)
            extra = [int(e) for e in page.extrasamples]
            self.invert = int(page.photometric) == 0
        except (*_DECODE_ERRORS, tifffile.TiffFileError) as exc:
            self._tif.close()
            raise _decode_error(exc) from exc
        self.width, self.height, self.dtype = info.width, info.height, self._arr.dtype
        self.channels = 3 if samples >= 3 else 1
        self.alpha = samples > self.channels and bool(extra) and extra[0] in (1, 2)
        self._take = list(range(self.channels + (1 if self.alpha else 0)))

    def band(self, y0: int, y1: int) -> np.ndarray:
        arr = self._arr
        if arr.ndim == 2:
            return np.asarray(arr[y0:y1])[..., None]
        part = np.moveaxis(arr[:, y0:y1], 0, -1) if self._first else arr[y0:y1]
        return np.ascontiguousarray(part[..., self._take])

    def close(self) -> None:
        del self._arr
        self._tif.close()


@contextmanager
def open_pixels(
    path: str | Path, progress: Progress | None = None, *, draft: tuple[int, int] | None = None
) -> Iterator[Pixels]:
    """The decoded pixels of an image. `draft` (width, height): a JPEG may be decoded at a smaller size, not
    less than this one (`Pixels.scale` says how many times smaller)."""
    path = Path(path)
    tick = progress or no_progress
    info, im = _inspect(path)
    try:
        pixels: Pixels = _TiffPixels(path, info, tick) if im is None else _PillowPixels(im, info, tick, draft)
    except BaseException:
        if im is not None:
            im.close()
        raise
    try:
        yield pixels
    finally:
        pixels.close()


# ---------------------------------------------------------------- display copies


@dataclass
class _Stretch:
    lo: float
    hi: float
    gaps: bool  # some pixels have no value


def _valid(colour: np.ndarray, nodata: float | None) -> np.ndarray | None:
    """Per sample: it has a value (finite, not the «no data» code); None when every sample has one."""
    ok = None
    if colour.dtype.kind == "f":
        finite = np.isfinite(colour)
        if not finite.all():
            ok = finite
    if nodata is not None:
        real = colour != nodata
        if not real.all():
            ok = real if ok is None else ok & real
    return ok


def _stretch(px: Pixels, rows: int, tick: Progress) -> _Stretch:
    """The 2nd and 98th percentiles of the colour samples (of every `step`-th pixel of a big image)."""
    step = max(1, math.ceil(math.sqrt(px.width * px.height / SAMPLE)))
    samples: list[np.ndarray] = []
    gaps = False
    for y0 in range(0, px.height, rows):
        colour = px.band(y0, min(px.height, y0 + rows))[..., : px.channels]
        ok = _valid(colour, px.info.nodata)
        part = colour[(-y0) % step :: step, ::step]
        if ok is not None:
            gaps = True
            part = part[ok[(-y0) % step :: step, ::step]]
        samples.append(np.asarray(part, dtype=np.float64).ravel())
        tick(y0 / px.height, "Оценка диапазона значений")
    values = np.concatenate(samples) if samples else np.empty(0)
    lo, hi = (float(v) for v in np.percentile(values, [2, 98])) if values.size else (0.0, 1.0)
    if not hi > lo and values.size:
        lo, hi = float(values.min()), float(values.max())
    return _Stretch(lo, hi if hi > lo else lo + 1.0, gaps)


def _image8(arr: np.ndarray, px: Pixels, stretch: _Stretch | None) -> Any:
    """A band as an 8-bit Pillow image: L, LA, RGB or RGBA."""
    image_module = _pil()
    colour = arr[..., : px.channels]
    alpha = arr[..., px.channels] if px.alpha else None
    if stretch is not None:
        ok = _valid(colour, px.info.nodata)
        scaled = (colour.astype(np.float64) - stretch.lo) * (255.0 / (stretch.hi - stretch.lo))
        if ok is not None:
            scaled[~ok] = 0.0
        colour = np.rint(np.clip(scaled, 0.0, 255.0)).astype(np.uint8)
        if alpha is not None:  # an alpha of the same depth as the colours
            top = 1.0 if alpha.dtype.kind == "f" else float(np.iinfo(alpha.dtype).max)
            alpha = np.rint(np.clip(alpha.astype(np.float64) / top, 0.0, 1.0) * 255.0).astype(np.uint8)
        elif stretch.gaps:
            alpha = np.full(colour.shape[:2], 255, dtype=np.uint8)
        if ok is not None and alpha is not None:
            alpha = np.where(ok.all(axis=-1), alpha, 0).astype(np.uint8)
    if px.invert:
        colour = 255 - colour
    out = colour if alpha is None else np.concatenate([colour, alpha[..., None]], axis=-1)
    return image_module.fromarray(np.ascontiguousarray(out[..., 0] if out.shape[-1] == 1 else out))


def _fit(width: int, height: int, limit: int) -> tuple[int, int]:
    """The size of a copy whose longer side is at most `limit`; an image within the limit keeps its size."""
    longer = max(width, height)
    if longer <= limit:
        return width, height
    return max(1, round(width * limit / longer)), max(1, round(height * limit / longer))


def _encode(image: Any) -> tuple[bytes, str]:
    if image.mode in ("LA", "RGBA") and image.getchannel("A").getextrema()[0] == 255:
        image = image.convert(image.mode[:-1])  # nothing is transparent
    out = io.BytesIO()
    if image.mode in ("LA", "RGBA"):
        image.save(out, "PNG", compress_level=4)
        return out.getvalue(), "image/png"
    image.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue(), "image/jpeg"


def _scaled(progress: Progress, start: float, stop: float) -> Progress:
    def tick(fraction: float, message: str | None = None) -> None:
        progress(start + (stop - start) * min(max(fraction, 0.0), 1.0), message)

    return tick


def display_copies(
    path: str | Path,
    progress: Progress | None = None,
    *,
    sizes: tuple[int, ...] = DISPLAY_SIZES,
    band_pixels: int = BAND_PIXELS,
) -> tuple[ImageInfo, list[DisplayCopy], list[str]]:
    """The copies of an image for the 3D view, the biggest first (see the module docstring), and notes for the
    operation log. `sizes` — the longer side of every level; `band_pixels` — source pixels handled at once."""
    image_module = _pil()
    tick = progress or no_progress
    notes: list[str] = []
    tick(0.0, "Чтение изображения")
    header = probe(path)
    targets = [_fit(header.width, header.height, limit) for limit in sizes]
    w0, h0 = targets[0]
    with open_pixels(path, _scaled(tick, 0.0, 0.45), draft=(w0, h0)) as px:
        info = px.info
        factor = max(1, min(px.width // w0, px.height // h0))
        deep = np.dtype(px.dtype) != np.uint8
        rows = max(1, (band_pixels // (4 if deep else 1)) // px.width // factor) * factor
        stretch = _stretch(px, rows, _scaled(tick, 0.45, 0.55)) if deep else None
        if deep:
            notes.append(
                f"Изображение с глубиной {info.bits} бит переведено в 8 бит для показа "
                "(растяжка между 2-м и 98-м перцентилями значений)"
            )
        transparent = px.alpha or (stretch is not None and stretch.gaps)
        mode = ("L" if px.channels == 1 else "RGB") + ("A" if transparent else "")
        small = image_module.new(mode, (-(-px.width // factor), -(-px.height // factor)))
        for y0 in range(0, px.height, rows):
            tick(0.55 + 0.25 * y0 / px.height, "Подготовка копии для показа")
            part = _image8(px.band(y0, min(px.height, y0 + rows)), px, stretch)
            small.paste(part.reduce(factor) if factor > 1 else part, (0, y0 // factor))
        shrink = px.scale * factor
    copies: list[DisplayCopy] = []
    lanczos = image_module.Resampling.LANCZOS
    current = small
    for level, (w, h) in enumerate(targets):
        at = 0.8 + 0.2 * level / len(targets)
        if current.size != (w, h):
            box = None
            if level == 0:  # the reduced picture may end with a part of a pixel: the box is the extent of the source
                box = (0.0, 0.0, info.width / shrink, info.height / shrink)
            current = _wait(partial(current.resize, (w, h), lanczos, box=box), tick, at, "Копия для показа")
        if copies and copies[-1].width == w and copies[-1].height == h:
            data, mime = copies[-1].data, copies[-1].mime
        else:
            data, mime = _wait(partial(_encode, current), tick, at, "Копия для показа")
        copies.append(DisplayCopy(level, w, h, data, mime))
    if (w0, h0) != (info.width, info.height):
        notes.append(
            f"Для показа изображение уменьшено до {w0} × {h0} пикселей (исходное — {info.width} × {info.height})"
        )
    tick(1.0, "Копии для показа готовы")
    return info, copies, notes


# ---------------------------------------------------------------- a raster with its georeference


def read_raster(
    path: str | Path,
    progress: Progress | None = None,
    *,
    sidecars: Mapping[str, Path | str | bytes] | None = None,
    fallback: Callable[[int, int], gr.Georef] | None = None,
    require_georef: bool = True,
    displays: bool = True,
    name: str | None = None,
    band_pixels: int = BAND_PIXELS,
) -> RasterRead:
    """Read an image with its georeference. `sidecars` {suffix: path or content} replaces the search for the
    files next to the image; `fallback(width, height)` places an image without a georeference (on the section
    line); `require_georef=False` returns such an image with `georef=None`; `displays=False` skips the copies."""
    path = Path(path)
    tick = progress or no_progress
    tick(0.0, "Чтение изображения")
    info = probe(path)
    title = name or path.stem
    found = gr.read_georef(path, info.width, info.height, sidecars, fallback=fallback)
    if found.georef is None and require_georef:
        detail = f" ({'; '.join(found.warnings)})" if found.warnings else ""
        raise _fail(
            "no_georef",
            f"Для изображения «{title}» нет привязки{detail}.",
            "Загрузите вместе с изображением файл привязки того же имени: .jgw / .pgw / .tfw / .wld, .tab или .grf — "
            "либо проведите линию разреза: тогда изображение встанет в её плоскость.",
        )
    out = RasterRead("raster", title, path, info, found.georef, found.checks, [], found.used, list(found.warnings))
    if displays:
        out.info, out.displays, notes = display_copies(path, _scaled(tick, 0.02, 1.0), band_pixels=band_pixels)
        out.warnings += notes
    tick(1.0, f"Изображение прочитано: {info.width} × {info.height} пикселей")
    return out


__all__ = [
    "BAND_PIXELS",
    "DISPLAY_SIZES",
    "MAX_PIXELS",
    "DisplayCopy",
    "ImageInfo",
    "Pixels",
    "RasterRead",
    "display_copies",
    "open_pixels",
    "probe",
    "read_raster",
]
