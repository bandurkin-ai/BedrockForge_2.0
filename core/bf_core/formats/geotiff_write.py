"""Writing a GeoTIFF: the pixels of a raster without loss and its georeference in tags (ТЗ 9.12; ADR 0011 §2).

`write_geotiff(path, source, georef)` → `GeoTiffWritten`. `source` is an image file (read band by band through
`raster_image.open_pixels`: its bands and depth are kept; a palette or bilevel image becomes colours) or a numpy
array (rows × width, or rows × width × 1…4 samples; 2 and 4 samples end with an alpha).
  * The file is tiled (512 px), compressed with Deflate (zlib of the standard library, ADR 0011 §6: no
    imagecodecs) with the horizontal predictor for whole numbers; BigTIFF when the pixels take more than 3.9 GB.
    Tiles are compressed here, a row of tiles at a time in a few threads, and handed to tifffile as bytes: memory
    is one row of tiles (plus the decoded source image) and progress follows the rows.
  * Georeference (pixel corners, `PixelIsArea`): `ModelPixelScale` + `ModelTiepoint` for a north-up raster,
    `ModelTransformation` for a rotated or mirrored one. The doubles are stored as they are: the tags read back
    (`georef.read_geotiff_tags`) to the same transform bit for bit. The elevation of a plan raster is the Z of the
    tie point (0 when there is none).
  * GeoKeys: the EPSG code of the CRS (`ProjectedCSTypeGeoKey` / `GeographicTypeGeoKey`) and its name; a CRS
    without a code is written as user-defined with its name only and reported in `warnings`.
  * A vertical raster has no place in a GeoTIFF (`unrepresentable`): export its source file instead.
The file appears under its name only when it is complete; `progress` may raise to cancel.
"""

from __future__ import annotations

import os
import zlib
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.formats import georef as gr
from bf_core.formats.errors import FormatError
from bf_core.formats.raster_image import ImageInfo, Pixels, open_pixels
from bf_core.formats.table import Progress, no_progress

if TYPE_CHECKING:
    from bf_core.crs import CrsInfo

TILE = 512
BIGTIFF_BYTES = int(3.9 * (1 << 30))
DEFLATE_LEVEL = 4  # of zlib: five times faster than 6 for files a tenth bigger
MODEL_PROJECTED, MODEL_GEOGRAPHIC = 1, 2
MAX_SHORT = 65535
_DTYPES = ("uint8", "uint16", "int16", "uint32", "int32", "float32", "float64")


@dataclass
class GeoTiffWritten:
    path: Path
    width: int
    height: int
    bands: int
    dtype: str
    size: int
    warnings: list[str] = field(default_factory=list)


class _ArrayPixels(Pixels):
    """A numpy array as a pixel source."""

    def __init__(self, array: np.ndarray) -> None:
        if array.ndim == 2:
            array = array[..., None]
        if array.ndim != 3 or not 1 <= array.shape[2] <= 4 or str(array.dtype) not in _DTYPES:
            raise FormatError(
                "unsupported",
                "Массив пикселей такого вида нельзя записать в GeoTIFF.",
                "Нужен массив «строки × ширина» или «строки × ширина × 1–4 канала» целых или вещественных чисел.",
            )
        self._array = array
        self.height, self.width, samples = array.shape
        self.channels = 3 if samples >= 3 else 1
        self.alpha = samples in (2, 4)
        self.dtype = array.dtype
        self.info = ImageInfo(self.width, self.height, "array", str(array.dtype), samples, array.dtype.itemsize * 8)

    def band(self, y0: int, y1: int) -> np.ndarray:
        return self._array[y0:y1]


def geotiff_tags(
    georef: gr.Georef, crs: CrsInfo | None = None, elevation: float | None = None
) -> tuple[list[tuple[int, str, int, Any, bool]], list[str]]:
    """The georeference as TIFF tags for `tifffile.imwrite(extratags=…)`, and notes for the operation log."""
    if georef.affine_z:
        raise FormatError(
            "unrepresentable",
            "Вертикальный растр нельзя записать в GeoTIFF: формат хранит привязку только в плане.",
            "Экспортируйте исходный файл растра.",
        )
    warnings: list[str] = []
    a, b, c, d, e, f = (float(v) for v in georef.affine)
    level = elevation if elevation is not None else georef.elevation
    z = 0.0 if level is None else float(level)
    tags: list[tuple[int, str, int, Any, bool]]
    if b == 0.0 and d == 0.0 and a > 0.0 and e < 0.0:
        tags = [(33550, "d", 3, (a, -e, 0.0), True), (33922, "d", 6, (0.0, 0.0, 0.0, c, f, z), True)]
    else:
        matrix = (a, b, 0.0, c, d, e, 0.0, f, 0.0, 0.0, 0.0, z, 0.0, 0.0, 0.0, 1.0)
        tags = [(34264, "d", 16, matrix, True)]
    crs = crs if crs is not None else georef.crs
    keys: list[tuple[int, int, int, int]] = []  # key, tag that holds the value (0: here), count, value or offset
    ascii_params = ""
    if crs is not None:
        code = crs.epsg if crs.epsg is not None and 0 < crs.epsg < gr.USER_DEFINED else None
        if code is None:
            warnings.append(
                f"У системы координат «{crs.name}» нет кода EPSG: в GeoTIFF записано только её название — "
                "приложите к файлу описание системы координат (.prj)"
            )
        name = crs.name.encode("ascii", "replace").decode("ascii").replace("|", " ") + "|"
        keys.append((gr.KEY_MODEL_TYPE, 0, 1, MODEL_PROJECTED if crs.projected else MODEL_GEOGRAPHIC))
        keys.append((gr.KEY_RASTER_TYPE, 0, 1, gr.PIXEL_IS_AREA))
        keys.append((gr.KEY_CITATION, 34737, len(name), 0))
        keys.append((gr.KEY_PROJECTED if crs.projected else gr.KEY_GEOGRAPHIC, 0, 1, code or gr.USER_DEFINED))
        ascii_params = name
    else:
        keys.append((gr.KEY_RASTER_TYPE, 0, 1, gr.PIXEL_IS_AREA))
    directory = [1, 1, 0, len(keys)] + [v for key in sorted(keys) for v in key]
    tags.append((34735, "H", len(directory), tuple(directory), True))
    if ascii_params:
        tags.append((34737, "s", 0, ascii_params, True))
    return tags, warnings


def _pack(tile: np.ndarray, level: int, predictor: bool) -> bytes:
    """One tile as its Deflate stream; the horizontal predictor stores the difference from the pixel to the left."""
    if predictor:
        out = tile.copy()
        out[:, 1:] = tile[:, 1:] - tile[:, :-1]  # wraps around, as the TIFF predictor does
        tile = out
    return zlib.compress(tile.tobytes(), level)


def _tiles(
    px: Pixels, tile: int, samples: int, pack: Callable[[np.ndarray], bytes] | None, tick: Progress
) -> Iterator[Any]:
    """Tiles of the image in row-major order, each padded to the full tile; with `pack` — compressed, a row of
    tiles at a time in threads (zlib releases the GIL), so memory is one row of tiles and progress is real."""
    shape = (tile, tile) if samples == 1 else (tile, tile, samples)
    with ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 1), thread_name_prefix="bf-geotiff") as pool:
        for y0 in range(0, px.height, tile):
            tick(y0 / px.height, "Запись GeoTIFF")
            band = px.band(y0, min(px.height, y0 + tile))
            if samples == 1:
                band = band[..., 0]
            row = []
            for x0 in range(0, px.width, tile):
                part = band[:, x0 : x0 + tile]
                if part.shape[:2] != (tile, tile):
                    full = np.zeros(shape, dtype=band.dtype)
                    full[: part.shape[0], : part.shape[1]] = part
                    part = full
                row.append(np.ascontiguousarray(part))
            yield from row if pack is None else pool.map(pack, row)


def write_geotiff(
    path: str | Path,
    source: str | Path | np.ndarray,
    georef: gr.Georef,
    *,
    crs: CrsInfo | None = None,
    elevation: float | None = None,
    compression: str = "deflate",
    tile: int = TILE,
    progress: Progress | None = None,
) -> GeoTiffWritten:
    """Write a raster as a GeoTIFF (see the module docstring). `crs` and `elevation` default to those of
    `georef`; `compression` — "deflate" or "none"."""
    import tifffile

    path = Path(path)
    tick = progress or no_progress
    if compression not in ("deflate", "none"):
        raise FormatError(
            "unsupported",
            f"Сжатие «{compression}» для записи GeoTIFF не поддерживается.",
            "Выберите Deflate или «без сжатия».",
        )
    if tile < 16 or tile % 16:
        raise ValueError("tile: a multiple of 16 is expected")
    tags, warnings = geotiff_tags(georef, crs, elevation)
    tick(0.0, "Запись GeoTIFF")
    opened: AbstractContextManager[Pixels]
    if isinstance(source, np.ndarray):
        opened = nullcontext(_ArrayPixels(source))
    else:
        opened = open_pixels(source, lambda fraction, message: tick(0.0, message))
    part = path.with_name(path.name + ".part")
    try:
        with opened as px:
            samples = px.channels + (1 if px.alpha else 0)
            dtype = np.dtype(px.dtype)
            if str(dtype) not in _DTYPES:
                raise FormatError(
                    "unsupported", f"Пиксели типа {dtype} нельзя записать в GeoTIFF.", "Сохраните растр в 8 или 16 бит."
                )
            options: dict[str, Any] = {}
            pack = None
            if compression == "deflate":
                predictor = dtype.kind in "ui"
                options = {"compression": "zlib", "predictor": predictor}
                pack = partial(_pack, level=DEFLATE_LEVEL, predictor=predictor)
            if px.alpha:
                options["extrasamples"] = ("unassalpha",)
            tifffile.imwrite(
                part,
                _tiles(px, tile, samples, pack, lambda fraction, message: tick(0.02 + 0.97 * fraction, message)),
                shape=(px.height, px.width) if samples == 1 else (px.height, px.width, samples),
                dtype=dtype,
                byteorder="<",
                tile=(tile, tile),
                photometric="rgb" if px.channels == 3 else "minisblack",
                bigtiff=px.width * px.height * samples * dtype.itemsize > BIGTIFF_BYTES,
                extratags=tags,
                metadata=None,
                **options,
            )
            width, height = px.width, px.height
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    tick(1.0, f"GeoTIFF записан: {width} × {height} пикселей")
    return GeoTiffWritten(path, width, height, samples, str(dtype), path.stat().st_size, warnings)


__all__ = ["GeoTiffWritten", "geotiff_tags", "write_geotiff"]
