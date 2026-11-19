"""Georeference of raster images: world files, MapInfo TAB, Micromine GRF, GeoTIFF tags (ТЗ 7.19, 9.7; ADR 0011 §1–2).

A georeference is an affine transform of **pixel-corner** coordinates, float64:
    X = a·col + b·row + c,   Y = d·col + e·row + f,   and off the plan   Z = g·col + h·row + i,
where (col, row) = (0, 0) is the top-left corner of the image and (width, height) the bottom-right one; the centre
of the top-left pixel is (0.5, 0.5). `Georef.affine` = (a, b, c, d, e, f), `affine_z` = (g, h, i) or None. A raster
with `affine_z` stands in a vertical or inclined plane (a scanned section); a plan raster lies at `elevation`
(None — below the lowest object, ТЗ 7.19; never 0). `corners3d` gives the corners top-left, top-right, bottom-right,
bottom-left as float64 4×3 (Z = NaN for a plan raster without an elevation).

Files:
  * world file (`.jgw .jpgw .pgw .pngw .tfw .tifw .bpw .bmpw .wld`): six numbers A, D, B, E, C, F; C and F are the
    **centre** of the top-left pixel, so c = C − (A + B) / 2 and f = F − (D + E) / 2;
  * MapInfo TAB (`Type "RASTER"`): control points `(X, Y) (col, row) Label "…"` (pixel corners) → least squares;
    `CoordSys` → CRS (`mif_crs`);
  * Micromine GRF: lines 1–3 — X, Y, Z of the corner, 4–5 — the pixel size, 6–11 — a 2×3 rotation matrix, then
    control points `col row X Y Z flag`. Control points win over the header; points at different heights make the
    raster vertical;
  * GeoTIFF: ModelPixelScale + ModelTiepoint or ModelTransformation, GeoKeys (PixelIsArea / PixelIsPoint, EPSG);
  * `.prj` next to the image gives the CRS.
Order of preference: GeoTIFF → world → TAB → GRF (`PRIORITY`). The elevation and the CRS may come from another file
of the same image; control points of every file give deviations from the chosen transform (`checks`, metres).
A file that cannot be read is a warning when another one gives the georeference.

Ported from BF `formats/rasters.py`. Differences: fits on centred data and sums without accumulated rounding
(1e−9 m on UTM), GeoTIFF tags through tifffile (PixelIsPoint, EPSG, elevation), `.prj`, limits on the size of the
files, `FormatError {code, message, hint}`; deviations are not rounded. Error codes: bad_world, not_raster_tab,
few_points, collinear, bad_grf, degenerate, too_large, no_elevation, unrepresentable (`world_text`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.formats.errors import FormatError

if TYPE_CHECKING:
    from bf_core.crs import CrsInfo

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")
WORLD_SUFFIXES = (".jgw", ".jpgw", ".pgw", ".pngw", ".tfw", ".tifw", ".bpw", ".bmpw", ".wld")
SIDECAR_SUFFIXES = (*WORLD_SUFFIXES, ".tab", ".grf", ".prj")
#: World files of the image's own type come first, then the generic `.wld`.
_OWN_WORLD = {
    ".jpg": (".jgw", ".jpgw"),
    ".jpeg": (".jgw", ".jpgw"),
    ".png": (".pgw", ".pngw"),
    ".tif": (".tfw", ".tifw"),
    ".tiff": (".tfw", ".tifw"),
    ".bmp": (".bpw", ".bmpw"),
}

GEOTIFF, WORLD, TAB, GRF, SECTION, MANUAL = "geotiff", "world", "tab", "grf", "section", "manual"
PRIORITY = (GEOTIFF, WORLD, TAB, GRF, SECTION, MANUAL)
SOURCE_LABELS = {
    GEOTIFF: "GeoTIFF",
    WORLD: "World-файл",
    TAB: "MapInfo TAB",
    GRF: "Micromine GRF",
    SECTION: "По линии разреза",
    MANUAL: "Вручную",
}
MAX_SIDECAR_BYTES = 1 << 20  # a georeference file is a few lines
MAX_POINTS = 10_000
FLAT_TOL = 1e-3  # control points within this height are at one level, m
_NUMBER = re.compile(r"[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][-+]?\d+)?")
_TAB_POINT = re.compile(
    r"\(\s*([-+\d.eE]+)\s*,\s*([-+\d.eE]+)\s*\)\s*\(\s*([-+\d.eE]+)\s*,\s*([-+\d.eE]+)\s*\)"
    r"\s*(?:Label\s+\"([^\"]*)\")?",
    re.IGNORECASE,
)
_HINT = "Проверьте файл привязки или загрузите изображение с другим файлом привязки (.jgw/.tfw, .tab, .grf)."

Affine = tuple[float, float, float, float, float, float]
AffineZ = tuple[float, float, float]


def _fail(code: str, message: str, hint: str = _HINT) -> FormatError:
    return FormatError(code, message, hint)


@dataclass
class ControlPoint:
    col: float
    row: float
    x: float
    y: float
    label: str = ""
    z: float | None = None


@dataclass
class Georef:
    affine: Affine  # a, b, c, d, e, f — pixel corners
    source: str  # GEOTIFF | WORLD | TAB | GRF | SECTION | MANUAL
    elevation: float | None = None
    points: list[ControlPoint] = field(default_factory=list)
    crs: CrsInfo | None = None
    affine_z: AffineZ | None = None  # g, h, i: Z of a raster off the plan
    warnings: list[str] = field(default_factory=list)


@dataclass
class GeorefRead:
    georef: Georef | None  # None: no file gave a georeference
    checks: list[dict[str, Any]] = field(default_factory=list)  # deviations at the control points of every file
    warnings: list[str] = field(default_factory=list)
    used: dict[str, Path] = field(default_factory=dict)  # suffix → the files that were read


# ---------------------------------------------------------------- the transform


def apply(affine: Sequence[float], col: float, row: float) -> tuple[float, float]:
    """Project coordinates of a point of the image; the sums are exactly rounded (`math.fsum`)."""
    a, b, c, d, e, f = affine
    return math.fsum((a * col, b * row, c)), math.fsum((d * col, e * row, f))


def apply_z(affine_z: Sequence[float], col: float, row: float) -> float:
    g, h, i = affine_z
    return math.fsum((g * col, h * row, i))


def corners(affine: Sequence[float], width: int, height: int) -> list[tuple[float, float]]:
    """Top-left, top-right, bottom-right, bottom-left in project coordinates."""
    return [apply(affine, c, r) for c, r in ((0, 0), (width, 0), (width, height), (0, height))]


def corners3d(g: Georef, width: int, height: int, elevation: float | None = None) -> np.ndarray:
    """float64 4×3: the corners with Z. A plan raster is at `elevation` (given, else its own); unknown — NaN."""
    out = np.empty((4, 3), dtype=np.float64)
    level = elevation if elevation is not None else g.elevation
    for k, (c, r) in enumerate(((0, 0), (width, 0), (width, height), (0, height))):
        out[k, :2] = apply(g.affine, c, r)
        out[k, 2] = apply_z(g.affine_z, c, r) if g.affine_z else (math.nan if level is None else level)
    return out


def is_vertical(g: Georef, width: int, height: int) -> bool:
    """True when the image does not lie in a horizontal plane."""
    if not g.affine_z:
        return False
    return float(np.ptp(corners3d(g, width, height)[:, 2])) > FLAT_TOL


def pixel_size(affine: Sequence[float], affine_z: Sequence[float] | None = None) -> float:
    a, b, _, d, e, _ = affine
    g, h = (affine_z[0], affine_z[1]) if affine_z else (0.0, 0.0)
    return (math.sqrt(a * a + d * d + g * g) + math.sqrt(b * b + e * e + h * h)) / 2


def rotation_deg(affine: Sequence[float]) -> float:
    """Rotation of the image's rows from the east axis, degrees counter-clockwise."""
    return math.degrees(math.atan2(affine[3], affine[0]))


def plane_azimuth(affine: Sequence[float]) -> float:
    """Azimuth of the image's left-to-right direction, degrees clockwise from north."""
    return math.degrees(math.atan2(affine[0], affine[3])) % 360.0


def shift_z(g: Georef, dz: float) -> Georef:
    """The raster moved by `dz` along Z: every corner of a vertical one, the level of a plan one (ТЗ 7.19)."""
    if g.affine_z:
        gz, hz, iz = g.affine_z
        return replace(g, affine_z=(gz, hz, iz + dz))
    if g.elevation is None:
        raise _fail(
            "no_elevation",
            "У растра не задана высота: сдвигать по Z нечего.",
            "Задайте высоту растра в свойствах, затем сдвиньте его.",
        )
    return replace(g, elevation=g.elevation + dz)


def world_text(g: Georef) -> str:
    """The six lines of a world file for a plan raster: A, D, B, E and the centre of the top-left pixel."""
    if g.affine_z:
        raise _fail(
            "unrepresentable",
            "Привязку вертикального растра нельзя записать в world-файл: он описывает растр только в плане.",
            "Экспортируйте исходный файл растра.",
        )
    a, b, c, d, e, f = g.affine
    values = (a, d, b, e, math.fsum((c, a / 2, b / 2)), math.fsum((f, d / 2, e / 2)))
    return "".join(f"{v!r}\n" for v in values)


def _check(affine: Sequence[float], affine_z: Sequence[float] | None = None) -> None:
    """The two edges of the image must span a plane; all numbers finite."""
    values = [*affine, *(affine_z or ())]
    if not all(math.isfinite(v) for v in values):
        raise _fail("degenerate", "В привязке есть нечисловые значения.")
    a, b, _, d, e, _ = affine
    g, h = (affine_z[0], affine_z[1]) if affine_z else (0.0, 0.0)
    u, v = np.array([a, d, g]), np.array([b, e, h])
    lu, lv = float(np.linalg.norm(u)), float(np.linalg.norm(v))
    if lu == 0.0 or lv == 0.0 or float(np.linalg.norm(np.cross(u, v))) <= 1e-12 * lu * lv:
        raise _fail("degenerate", "Привязка вырождена: размер пикселя нулевой или стороны изображения совпадают.")


def _fit(points: Sequence[ControlPoint], values: Sequence[float]) -> AffineZ:
    """Least squares value = g·col + h·row + i on centred data: rounding stays at the level of the last digit
    of the coordinates (1e−9 m on UTM), not of their squares."""
    cols, rows = [p.col for p in points], [p.row for p in points]
    n = len(points)
    cm, rm, vm = math.fsum(cols) / n, math.fsum(rows) / n, math.fsum(values) / n
    m = np.column_stack([np.array(cols) - cm, np.array(rows) - rm])
    if np.linalg.matrix_rank(m) < 2:
        raise _fail(
            "collinear",
            "Опорные точки лежат на одной прямой: привязку вычислить нельзя.",
            "Нужны не меньше трёх опорных точек, не лежащих на одной прямой.",
        )
    (g, h), *_ = np.linalg.lstsq(m, np.array(values) - vm, rcond=None)
    return float(g), float(h), math.fsum((vm, -float(g) * cm, -float(h) * rm))


def fit_affine(points: Sequence[ControlPoint]) -> Affine:
    """Least-squares affine transform through three or more control points."""
    if len(points) < 3:
        raise _fail(
            "few_points",
            "Для привязки по опорным точкам нужно не меньше трёх точек.",
            "Добавьте опорные точки в исходной программе или используйте world-файл.",
        )
    if len(points) > MAX_POINTS:
        raise _fail("too_large", f"В файле привязки больше {MAX_POINTS} опорных точек.")
    if not all(math.isfinite(v) for p in points for v in (p.col, p.row, p.x, p.y)):
        raise _fail("degenerate", "В координатах опорных точек есть нечисловые значения.")
    a, b, c = _fit(points, [p.x for p in points])
    d, e, f = _fit(points, [p.y for p in points])
    return a, b, c, d, e, f


def fit_z(points: Sequence[ControlPoint]) -> AffineZ:
    """Least-squares Z = g·col + h·row + i through control points that carry Z."""
    return _fit(points, [float(p.z) for p in points if p.z is not None])


def deviations(g: Georef, points: Sequence[ControlPoint]) -> list[dict[str, Any]]:
    """Per control point: the transform minus the point, metres (`dz` only for a vertical raster)."""
    out: list[dict[str, Any]] = []
    for p in points:
        x, y = apply(g.affine, p.col, p.row)
        row: dict[str, Any] = {"label": p.label, "col": p.col, "row": p.row, "dx": x - p.x, "dy": y - p.y}
        dz = 0.0
        if g.affine_z and p.z is not None:
            dz = row["dz"] = apply_z(g.affine_z, p.col, p.row) - p.z
        row["d"] = math.sqrt(row["dx"] ** 2 + row["dy"] ** 2 + dz**2)
        out.append(row)
    return out


def vertical_georef(
    width: int, height: int, x1: float, y1: float, x2: float, y2: float, z_top: float, z_bottom: float,
    source: str = SECTION,
) -> Georef:  # fmt: skip
    """An image standing in a vertical plane: its left edge at (x1, y1), the right one at (x2, y2), from `z_top`
    down to `z_bottom`."""
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2, z_top, z_bottom)) or width < 1 or height < 1:
        raise _fail("degenerate", "Края растра заданы не числами.", "Укажите координаты краёв и высоты числами.")
    if math.hypot(x2 - x1, y2 - y1) < 1e-6:
        raise _fail("degenerate", "Левый и правый края растра совпадают в плане.", "Задайте разные края растра.")
    if abs(z_top - z_bottom) < 1e-6:
        raise _fail("degenerate", "Верх и низ растра на одной высоте.", "Задайте разные высоты верха и низа.")
    affine = ((x2 - x1) / width, 0.0, x1, (y2 - y1) / width, 0.0, y1)
    return Georef(affine, source, affine_z=(0.0, (z_bottom - z_top) / height, z_top))


# ---------------------------------------------------------------- georeference files


def _numbers(text: str) -> list[float]:
    return [float(t.replace(",", ".")) for t in _NUMBER.findall(text)]


def read_world(text: str) -> Georef:
    """Six lines: A, D, B, E, C, F; C and F are the centre of the top-left pixel."""
    hint = "World-файл — это шесть чисел, по одному в строке: A, D, B, E, C, F."
    lines = [line.strip() for line in text.lstrip("﻿").splitlines() if line.strip()]
    if len(lines) < 6:
        raise _fail("bad_world", "В world-файле меньше шести чисел.", hint)
    values: list[float] = []
    for line in lines[:6]:
        found = _NUMBER.fullmatch(line)
        value = float(line.replace(",", ".")) if found else math.nan
        if not math.isfinite(value):
            raise _fail("bad_world", f"Строка world-файла «{line[:40]}» — не число.", hint)
        values.append(value)
    a, d, b, e, c, f = values
    # From the centre of the top-left pixel to its corner, each with one rounding.
    affine = (a, b, math.fsum((c, -a / 2, -b / 2)), d, e, math.fsum((f, -d / 2, -e / 2)))
    _check(affine)
    return Georef(affine, WORLD)


def read_tab(text: str) -> Georef:
    """MapInfo raster TAB: control points `(X, Y) (col, row) Label "…"`, pixel coordinates of corners."""
    if 'type "raster"' not in " ".join(text.lower().split()):
        raise _fail(
            "not_raster_tab",
            'TAB-файл не описывает растр (нет строки Type "RASTER").',
            "Векторную таблицу MapInfo сохраните как MIF/MID; для растра нужен TAB с опорными точками.",
        )
    found = _TAB_POINT.findall(text, 0, MAX_SIDECAR_BYTES)
    if len(found) > MAX_POINTS:
        raise _fail("too_large", f"В TAB-файле больше {MAX_POINTS} опорных точек.")
    try:
        points = [ControlPoint(float(m[2]), float(m[3]), float(m[0]), float(m[1]), m[4] or "") for m in found]
    except ValueError as exc:
        raise _fail("few_points", "Координаты опорных точек TAB-файла не читаются.") from exc
    out = Georef(fit_affine(points), TAB, points=points)
    _check(out.affine)
    clause = next((s.strip() for s in text.splitlines() if s.strip().lower().startswith("coordsys")), None)
    if clause:
        from bf_core.formats.mif_crs import parse_coordsys

        parsed = parse_coordsys(clause)
        out.crs = parsed.crs
        if parsed.warning:
            out.warnings.append(parsed.warning.replace("объект загружен", "растр загружен"))
    return out


def read_grf(text: str) -> Georef:
    """Micromine GRF: corner X, Y, Z; pixel size; a 2×3 rotation matrix; then `col row X Y Z flag` control points.
    The transform is fitted through the control points; without them the header is used."""
    hint = "Сохраните привязку растра в Micromine заново (файл .grf) или используйте world-файл."
    lines = [line for line in text.lstrip("﻿").splitlines() if line.strip()]
    head = [_numbers(line) for line in lines[:11]]
    if len(head) < 5 or any(not h for h in head[:5]):
        raise _fail(
            "bad_grf", "Структура GRF-файла не распознана: в начале должны быть X, Y, Z и размер пикселя.", hint
        )
    x0, y0, z0 = head[0][0], head[1][0], head[2][0]
    points: list[ControlPoint] = []
    for line in lines[11 : 11 + MAX_POINTS + 1]:
        v = _numbers(line)
        if len(v) >= 4:
            points.append(ControlPoint(v[0], v[1], v[2], v[3], str(len(points) + 1), v[4] if len(v) >= 5 else None))
    if len(points) > MAX_POINTS:
        raise _fail("too_large", f"В GRF-файле больше {MAX_POINTS} опорных точек.")
    if len(points) >= 3:
        zs = [p.z for p in points if p.z is not None]
        heights = len(zs) == len(points)
        if heights and max(zs) - min(zs) > FLAT_TOL:
            # Control points at different heights: a section scan standing in a vertical plane.
            out = Georef(fit_affine(points), GRF, points=points, affine_z=fit_z(points))
            _check(out.affine, out.affine_z)
            return out
        try:
            out = Georef(fit_affine(points), GRF, elevation=zs[0] if heights else z0, points=points)
            _check(out.affine)
            return out
        except FormatError:
            pass  # the points do not give a transform: the header does
    sx, sy = head[3][0], head[4][0]
    matrix = [h[0] for h in head[5:11]] if len(head) >= 11 and all(head[5:11]) else [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    r11, r12, _, r21, r22, _ = matrix
    # Rows go down the image, so Y decreases with the row number.
    affine = (sx * r11, -sy * r12, x0, sx * r21, -sy * r22, y0)
    _check(affine)
    return Georef(affine, GRF, elevation=z0, points=points)


# ---------------------------------------------------------------- GeoTIFF tags

_SCALE, _TIEPOINT, _MATRIX, _KEYS, _DOUBLES, _ASCII = 33550, 33922, 34264, 34735, 34736, 34737
KEY_MODEL_TYPE, KEY_RASTER_TYPE, KEY_CITATION, KEY_GEOGRAPHIC, KEY_PROJECTED = 1024, 1025, 1026, 2048, 3072
PIXEL_IS_AREA, PIXEL_IS_POINT, USER_DEFINED = 1, 2, 32767
TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


def _geokeys(tags: Any) -> dict[int, int]:
    """Short values of the GeoKey directory: {key: value} (keys stored in other tags are left out)."""
    raw = tags.get(_KEYS)
    if raw is None:
        return {}
    v = [int(x) for x in np.atleast_1d(raw.value)]
    count = min(v[3], (len(v) - 4) // 4) if len(v) >= 4 else 0
    return {v[4 + 4 * k]: v[7 + 4 * k] for k in range(count) if v[5 + 4 * k] == 0}


def read_geotiff_tags(path: str | Path) -> Georef | None:
    """ModelTransformation or ModelPixelScale + ModelTiepoint of the first page; None when the file has none
    (or is not a TIFF). Several tie points without a scale are control points."""
    import tifffile

    try:
        with Path(path).open("rb") as stream:
            if stream.read(4) not in TIFF_MAGIC:
                return None
        with tifffile.TiffFile(path) as tif:
            tags = tif.pages.first.tags
            values = {}
            for code in (_SCALE, _TIEPOINT, _MATRIX):
                tag = tags.get(code)
                values[code] = [] if tag is None else [float(x) for x in np.atleast_1d(tag.value)]
            keys = _geokeys(tags)
    except (OSError, ValueError, TypeError, IndexError, KeyError, AttributeError, tifffile.TiffFileError):
        return None
    half = 0.5 if keys.get(KEY_RASTER_TYPE) == PIXEL_IS_POINT else 0.0  # the raster space is of pixel centres
    scale, tie, t = values[_SCALE], values[_TIEPOINT], values[_MATRIX]
    points: list[ControlPoint] = []
    level = 0.0
    try:
        if len(t) >= 16:
            affine: Affine = (t[0], t[1], t[3], t[4], t[5], t[7])
            level = t[11]
        elif len(scale) >= 2 and len(tie) >= 6:
            i, j, _, x, y, level = tie[:6]
            sx, sy = scale[0], scale[1]
            affine = (sx, 0.0, math.fsum((x, -i * sx)), 0.0, -sy, math.fsum((y, j * sy)))
        elif len(tie) >= 18:  # tie points only: control points, in corner coordinates from here on
            for k in range(0, len(tie) - 5, 6):
                points.append(ControlPoint(tie[k] + half, tie[k + 1] + half, tie[k + 3], tie[k + 4], str(k // 6 + 1)))
            affine, half = fit_affine(points), 0.0
        else:
            return None
        if half:
            a, b, c, d, e, f = affine
            affine = (a, b, math.fsum((c, -a * half, -b * half)), d, e, math.fsum((f, -d * half, -e * half)))
        _check(affine)
    except FormatError:
        return None
    out = Georef(affine, GEOTIFF, points=points)
    if math.isfinite(level) and level != 0.0:
        out.elevation = level  # 0 is what every writer puts there: not a level
    code = keys.get(KEY_PROJECTED) or keys.get(KEY_GEOGRAPHIC) or 0
    if code and code != USER_DEFINED:
        from bf_core.crs import CrsError
        from bf_core.crs import describe as describe_crs

        try:
            out.crs = describe_crs(f"EPSG:{code}")
        except CrsError:
            out.warnings.append(f"Система координат GeoTIFF (EPSG:{code}) не распознана: растр загружен без неё")
    elif code == USER_DEFINED or keys.get(KEY_MODEL_TYPE) == USER_DEFINED:
        out.warnings.append("Система координат GeoTIFF задана параметрами, без кода EPSG: растр загружен без неё")
    return out


# ---------------------------------------------------------------- choosing among the files


def find_sidecars(image: str | Path) -> dict[str, Path]:
    """Georeference files next to the image: the same name (any case) or the full name of the image plus a suffix
    (`map.jpg.tab`). {suffix: path}."""
    image = Path(image)
    stems = {image.stem.casefold(), image.name.casefold()}
    out: dict[str, Path] = {}
    try:
        names = sorted(image.parent.iterdir())
    except OSError:
        return out
    for p in names:
        suffix = p.suffix.lower()
        if suffix in SIDECAR_SUFFIXES and p.stem.casefold() in stems and p.is_file() and p != image:
            out.setdefault(suffix, p)
    return out


def _text(source: Path | str | bytes) -> str:
    """The text of a georeference file given as a path, bytes or text; a big or binary file is refused."""
    if isinstance(source, str):
        return source
    if isinstance(source, Path):
        with source.open("rb") as f:
            source = f.read(MAX_SIDECAR_BYTES + 1)
    if len(source) > MAX_SIDECAR_BYTES:
        raise _fail("too_large", "Файл привязки больше 1 МБ: это не файл привязки растра.")
    if b"\x00" in source and not source.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise _fail("bad_world", "Файл привязки двоичный, а должен быть текстовым.")
    for codec in ("utf-8-sig", "utf-16" if source.startswith((b"\xff\xfe", b"\xfe\xff")) else "cp1251"):
        try:
            return source.decode(codec)
        except UnicodeDecodeError:
            continue
    return source.decode("latin-1")


def resolve(candidates: Sequence[Georef]) -> tuple[Georef, list[dict[str, Any]]]:
    """The georeference to use (by `PRIORITY`) and the deviations of the control points of every file from it.
    The elevation and the CRS are taken from the first file that has them."""
    ordered = sorted(candidates, key=lambda g: PRIORITY.index(g.source))
    chosen = ordered[0]
    if chosen.elevation is None and not chosen.affine_z:
        chosen.elevation = next((g.elevation for g in ordered if g.elevation is not None), None)
    if chosen.crs is None:
        chosen.crs = next((g.crs for g in ordered if g.crs is not None), None)
    checks = []
    for g in ordered:
        if g.points:
            devs = deviations(chosen, g.points)
            checks.append({"source": g.source, "max": max(d["d"] for d in devs), "points": devs})
    return chosen, checks


def read_georef(
    image: str | Path,
    width: int,
    height: int,
    sidecars: Mapping[str, Path | str | bytes] | None = None,
    *,
    geotiff: bool = True,
    fallback: Callable[[int, int], Georef] | None = None,
) -> GeorefRead:
    """The georeference of an image from its tags and the files next to it. `sidecars` {suffix: path or content}
    replaces the search by name. `fallback(width, height)` places an image that has none (on the section line)."""
    image = Path(image)
    files = dict(find_sidecars(image) if sidecars is None else sidecars)
    out = GeorefRead(None)
    candidates: list[Georef] = []
    tags = read_geotiff_tags(image) if geotiff else None  # told by the content: a stored file may have no suffix
    if tags is not None:
        candidates.append(tags)
    own = _OWN_WORLD.get(image.suffix.lower(), ())
    worlds = sorted((s for s in files if s in WORLD_SUFFIXES), key=lambda s: (s not in own, s != ".wld", s))
    for suffix in worlds[1:]:
        out.warnings.append(f"Файл привязки {suffix} не использован: взят {worlds[0]}")
    readers: list[tuple[str, Callable[[str], Georef]]] = [(s, read_world) for s in worlds[:1]]
    readers += [(s, r) for s, r in ((".tab", read_tab), (".grf", read_grf)) if s in files]
    for suffix, reader in readers:
        try:
            candidates.append(reader(_text(files[suffix])))
        except (FormatError, OSError) as exc:
            reason = exc.message if isinstance(exc, FormatError) else "файл не читается"
            out.warnings.append(f"Файл привязки {suffix} не использован: {reason}")
            continue
        source = files[suffix]
        if isinstance(source, Path):
            out.used[suffix] = source
    if not candidates and fallback is not None:
        candidates.append(fallback(width, height))
    if not candidates:
        return out
    out.georef, out.checks = resolve(candidates)
    if out.georef.crs is None and ".prj" in files:
        from bf_core.crs import CrsError, read_prj
        from bf_core.crs import describe as describe_crs

        prj = files[".prj"]
        try:
            out.georef.crs = read_prj(prj) if isinstance(prj, Path) else describe_crs(_text(prj))
            if isinstance(prj, Path):
                out.used[".prj"] = prj
        except (CrsError, OSError) as exc:
            out.warnings.append(f"Файл .prj не использован: {exc}")
    for g in candidates:
        out.warnings += g.warnings
    return out


def describe(g: Georef, width: int, height: int, checks: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    """The georeference as the project keeps it (`rasters.georef`, ADR 0011 §1): plain JSON, an unknown Z is null."""
    box = corners3d(g, width, height)
    return {
        "affine": list(g.affine),
        "affine_z": list(g.affine_z) if g.affine_z else None,
        "source": g.source,
        "crs": g.crs.wkt if g.crs else None,
        "crs_name": g.crs.name if g.crs else None,
        "epsg": g.crs.epsg if g.crs else None,
        "pixel": pixel_size(g.affine, g.affine_z),
        "rotation": rotation_deg(g.affine),
        "azimuth": plane_azimuth(g.affine),
        "vertical": is_vertical(g, width, height),
        "corners3d": [[None if math.isnan(v) else v for v in row] for row in box.tolist()],
        "checks": list(checks),
    }
