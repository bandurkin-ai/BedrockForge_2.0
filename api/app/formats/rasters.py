"""Georeferenced images: JPG, PNG, TIFF / GeoTIFF, BMP with a world file, MapInfo TAB or Micromine GRF.

Georeference is kept as an affine transform of pixel-corner coordinates:
    X = a·col + b·row + c,   Y = d·col + e·row + f,   and for rasters off the plan  Z = g·col + h·row + i
where (col, row) = (0, 0) is the top-left corner of the image and (width, height) the bottom-right one.
A raster with a Z transform stands in a vertical (or inclined) plane: a scanned section, for example.
Formats are described in docs/FORMATS.md. Order of preference: GeoTIFF tags → world file → TAB → GRF;
control points of the other files are used to report deviations.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")
WORLD_SUFFIXES = (".jgw", ".jpgw", ".pgw", ".pngw", ".tfw", ".tifw", ".bpw", ".wld")
SIDECAR_SUFFIXES = WORLD_SUFFIXES + (".tab", ".grf")
# Browsers reliably handle textures up to 4096 px; larger images get a display copy.
DISPLAY_MAX = 4096


class RasterFormatError(ValueError):
    """The image or its georeference cannot be read; the message is shown to the user."""


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
    affine: list[float]  # a, b, c, d, e, f (pixel corners)
    source: str  # "GeoTIFF", "World-файл", "MapInfo TAB", "Micromine GRF"
    elevation: float | None = None
    points: list[ControlPoint] = field(default_factory=list)
    crs: str | None = None
    # g, h, i: Z of a raster that is not in plan; None for plan rasters (they lie at `elevation`).
    affine_z: list[float] | None = None


@dataclass
class RasterImage:
    name: str
    width: int
    height: int
    image: bytes  # display copy
    mime: str
    georef: Georef
    # Deviations of control points of every file from the chosen georeference, m.
    checks: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def apply(affine: list[float], col: float, row: float) -> tuple[float, float]:
    a, b, c, d, e, f = affine
    return a * col + b * row + c, d * col + e * row + f


def corners(affine: list[float], width: int, height: int) -> list[tuple[float, float]]:
    """Top-left, top-right, bottom-right, bottom-left in project coordinates."""
    return [apply(affine, c, r) for c, r in ((0, 0), (width, 0), (width, height), (0, height))]


def fit_affine(points: list[ControlPoint]) -> list[float]:
    """Least-squares affine transform through 3 or more control points."""
    if len(points) < 3:
        raise RasterFormatError("Для привязки по опорным точкам нужно не меньше трёх точек")
    m = np.array([[p.col, p.row, 1.0] for p in points])
    if np.linalg.matrix_rank(m) < 3:
        raise RasterFormatError("Опорные точки лежат на одной прямой — привязку вычислить нельзя")
    (a, b, c), *_ = np.linalg.lstsq(m, np.array([p.x for p in points]), rcond=None)
    (d, e, f), *_ = np.linalg.lstsq(m, np.array([p.y for p in points]), rcond=None)
    return [float(v) for v in (a, b, c, d, e, f)]


def fit_z(points: list[ControlPoint]) -> list[float]:
    """Least-squares Z = g·col + h·row + i through control points that carry Z."""
    m = np.array([[p.col, p.row, 1.0] for p in points])
    (g, h, i), *_ = np.linalg.lstsq(m, np.array([p.z for p in points]), rcond=None)
    return [float(g), float(h), float(i)]


def apply_z(affine_z: list[float], col: float, row: float) -> float:
    g, h, i = affine_z
    return g * col + h * row + i


def deviations(affine: list[float], points: list[ControlPoint], affine_z: list[float] | None = None) -> list[dict]:
    out = []
    for p in points:
        x, y = apply(affine, p.col, p.row)
        dz = apply_z(affine_z, p.col, p.row) - p.z if affine_z and p.z is not None else 0.0
        row = {"label": p.label, "col": p.col, "row": p.row, "dx": round(x - p.x, 3), "dy": round(y - p.y, 3)}
        if affine_z and p.z is not None:
            row["dz"] = round(dz, 3)
        row["d"] = round(float(np.sqrt((x - p.x) ** 2 + (y - p.y) ** 2 + dz**2)), 3)
        out.append(row)
    return out


def vertical_georef(width: int, height: int, x1: float, y1: float, x2: float, y2: float, z_top: float, z_bottom: float) -> Georef:
    """An image standing in a vertical plane: its left edge at (x1, y1), right edge at (x2, y2), from z_top down to z_bottom."""
    if np.hypot(x2 - x1, y2 - y1) < 1e-6:
        raise RasterFormatError("Левый и правый края растра совпадают в плане")
    if abs(z_top - z_bottom) < 1e-6:
        raise RasterFormatError("Верх и низ растра на одной высоте")
    return Georef(
        [(x2 - x1) / width, 0.0, x1, (y2 - y1) / width, 0.0, y1],
        "Вручную по разрезу",
        affine_z=[0.0, (z_bottom - z_top) / height, z_top],
    )


def corners3d(g: Georef, width: int, height: int) -> list[list[float]] | None:
    """Corners with Z (top-left, top-right, bottom-right, bottom-left) for a raster off the plan."""
    if not g.affine_z:
        return None
    return [[*apply(g.affine, c, r), apply_z(g.affine_z, c, r)] for c, r in ((0, 0), (width, 0), (width, height), (0, height))]


def is_vertical(g: Georef, width: int, height: int) -> bool:
    """True when the image does not lie in a horizontal plane."""
    pts = corners3d(g, width, height)
    return bool(pts) and float(np.ptp([p[2] for p in pts])) > 1e-3


def pixel_size(affine: list[float], affine_z: list[float] | None = None) -> float:
    a, b, _, d, e, _ = affine
    g, h = (affine_z[0], affine_z[1]) if affine_z else (0.0, 0.0)
    return float((np.sqrt(a * a + d * d + g * g) + np.sqrt(b * b + e * e + h * h)) / 2)


def plane_azimuth(affine: list[float]) -> float:
    """Azimuth of the image's left-to-right direction, degrees clockwise from north."""
    a, _, _, d, _, _ = affine
    return float(np.degrees(np.arctan2(a, d)) % 360)


def rotation_deg(affine: list[float]) -> float:
    a, _, _, d, _, _ = affine
    return float(np.degrees(np.arctan2(d, a)))


# ---------------------------------------------------------------- georeference files


def _numbers(text: str) -> list[float]:
    return [float(t.replace(",", ".")) for t in re.findall(r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?", text)]


def read_world(text: str) -> Georef:
    """Six lines: A, D, B, E, C, F; C and F are the centre of the top-left pixel."""
    values = [v for v in (_numbers(line) for line in text.splitlines()) if v]
    if len(values) < 6 or any(len(v) != 1 for v in values[:6]):
        raise RasterFormatError("World-файл должен содержать шесть чисел, по одному в строке")
    a, d, b, e, c, f = (v[0] for v in values[:6])
    # Shift the reference from the pixel centre to its corner.
    return Georef([a, b, c - (a + b) / 2, d, e, f - (d + e) / 2], "World-файл")


_TAB_POINT = re.compile(
    r"\(\s*([-+\d.eE]+)\s*,\s*([-+\d.eE]+)\s*\)\s*\(\s*([-+\d.eE]+)\s*,\s*([-+\d.eE]+)\s*\)\s*(?:Label\s+\"([^\"]*)\")?",
    re.IGNORECASE,
)


def read_tab(text: str) -> Georef:
    """MapInfo raster TAB: control points `(X, Y) (col, row) Label "..."`, pixel coordinates of corners."""
    if 'type "raster"' not in text.lower():
        raise RasterFormatError("TAB-файл не описывает растр (нет Type \"RASTER\")")
    points = [ControlPoint(col=float(m[3]), row=float(m[4]), x=float(m[1]), y=float(m[2]), label=m[5] or "") for m in _TAB_POINT.finditer(text)]
    crs = next((line.strip() for line in text.splitlines() if line.strip().lower().startswith("coordsys")), None)
    return Georef(fit_affine(points), "MapInfo TAB", points=points, crs=crs)


def read_grf(text: str) -> Georef:
    """Micromine GRF: corner X, Y, Z; pixel size; a 2×3 rotation matrix; then `col row X Y Z flag` control points.
    The transform is fitted through the control points; without them the header is used."""
    lines = [line for line in text.splitlines() if line.strip()]
    head = [_numbers(line) for line in lines[:11]]
    if len(head) < 5 or any(not h for h in head[:5]):
        raise RasterFormatError("Неизвестная структура GRF-файла")
    x0, y0, z0 = head[0][0], head[1][0], head[2][0]
    points = []
    for line in lines[11:]:
        values = _numbers(line)
        if len(values) >= 4:
            z = values[4] if len(values) >= 5 else None
            points.append(ControlPoint(col=values[0], row=values[1], x=values[2], y=values[3], label=f"{len(points) + 1}", z=z))
    if len(points) >= 3:
        try:
            g = Georef(fit_affine(points), "Micromine GRF", elevation=z0, points=points)
        except RasterFormatError:
            g = None
        zs = [p.z for p in points]
        if all(z is not None for z in zs) and float(np.ptp(zs)) > 1e-3:
            # Control points at different heights: a section scan standing in a vertical plane.
            m = np.array([[p.col, p.row, 1.0] for p in points])
            if np.linalg.matrix_rank(m) < 3:
                raise RasterFormatError("Опорные точки GRF лежат на одной прямой — привязку вычислить нельзя")
            xy = [float(v) for v in np.linalg.lstsq(m, np.array([[p.x, p.y] for p in points]), rcond=None)[0].T.ravel()]
            return Georef([xy[0], xy[1], xy[2], xy[3], xy[4], xy[5]], "Micromine GRF", affine_z=fit_z(points), points=points)
        if g is not None:
            return g
    sx, sy = head[3][0], head[4][0]
    matrix = [h[0] for h in head[5:11]] if len(head) >= 11 and all(head[5:11]) else [1, 0, 0, 0, 1, 0]
    r11, r12, _, r21, r22, _ = matrix
    # Rows go down the image, so Y decreases with the row number.
    return Georef([sx * r11, -sy * r12, x0, sx * r21, -sy * r22, y0], "Micromine GRF", elevation=z0, points=points)


def read_geotiff_tags(image) -> Georef | None:
    """ModelPixelScale + ModelTiepoint or ModelTransformation from TIFF tags (PixelIsArea)."""
    tags = getattr(image, "tag_v2", None)
    if not tags:
        return None
    transform = tags.get(34264)
    if transform and len(transform) >= 16:
        t = [float(v) for v in transform]
        return Georef([t[0], t[1], t[3], t[4], t[5], t[7]], "GeoTIFF")
    scale, tie = tags.get(33550), tags.get(33922)
    if scale and tie and len(scale) >= 2 and len(tie) >= 6:
        i, j, _, x, y, _ = (float(v) for v in tie[:6])
        sx, sy = float(scale[0]), float(scale[1])
        return Georef([sx, 0.0, x - i * sx, 0.0, -sy, y + j * sy], "GeoTIFF")
    return None


# ---------------------------------------------------------------- images


def _display_copy(image) -> tuple[bytes, str, list[str]]:
    """An 8-bit RGB(A) image no larger than DISPLAY_MAX on a side, as JPEG or PNG (with transparency)."""
    from PIL import Image

    notes = []
    if image.mode in ("I;16", "I;16B", "I", "F"):
        data = np.asarray(image, dtype=np.float64)
        finite = data[np.isfinite(data)]
        lo, hi = (np.percentile(finite, [2, 98]) if finite.size else (0, 1))
        data = np.clip((data - lo) / max(hi - lo, 1e-9) * 255, 0, 255).astype(np.uint8)
        image = Image.fromarray(data, "L")
        notes.append("Изображение с высоким динамическим диапазоном переведено в 8 бит (2–98 перцентиль)")
    alpha = image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)
    image = image.convert("RGBA" if alpha else "RGB")
    if max(image.size) > DISPLAY_MAX:
        scale = DISPLAY_MAX / max(image.size)
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
        notes.append(f"Для показа уменьшено до {image.width}×{image.height} пикселей")
    out = io.BytesIO()
    if alpha:
        image.save(out, "PNG", optimize=True)
        return out.getvalue(), "image/png", notes
    image.save(out, "JPEG", quality=90)
    return out.getvalue(), "image/jpeg", notes


def read_raster(image_path: Path, name: str, sidecars: dict[str, str], fallback=None) -> RasterImage:
    """`sidecars` maps a suffix (".jgw", ".tab", ".grf"…) to the file text.
    `fallback(width, height) -> Georef` places an image that has no georeference (e.g. on the current section)."""
    from PIL import Image, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = 400_000_000
    try:
        image = Image.open(image_path)
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise RasterFormatError(f"Изображение «{name}» не читается: {exc}") from exc
    width, height = image.size

    candidates: list[Georef] = []
    errors: list[str] = []
    tiff = read_geotiff_tags(image)
    if tiff:
        candidates.append(tiff)
    for suffix, text in sidecars.items():
        try:
            if suffix in WORLD_SUFFIXES:
                candidates.append(read_world(text))
            elif suffix == ".tab":
                candidates.append(read_tab(text))
            elif suffix == ".grf":
                candidates.append(read_grf(text))
        except RasterFormatError as exc:
            errors.append(f"{suffix}: {exc}")
    if not candidates and fallback is not None:
        try:
            candidates.append(fallback(width, height))
        except RasterFormatError as exc:
            raise RasterFormatError(f"«{name}»: {exc}") from exc
    if not candidates:
        detail = f" ({'; '.join(errors)})" if errors else ""
        raise RasterFormatError(
            f"Для «{name}» нет привязки{detail}. Загрузите вместе с изображением файл привязки: .jgw/.pgw/.tfw, .tab или .grf, "
            "или проведите линию разреза — тогда изображение встанет в её плоскость"
        )
    order = {"GeoTIFF": 0, "World-файл": 1, "MapInfo TAB": 2, "Micromine GRF": 3, "Вручную по разрезу": 4}
    candidates.sort(key=lambda g: order[g.source])
    chosen = candidates[0]
    # Elevation and coordinate system may come from another file of the same image.
    chosen.elevation = next((g.elevation for g in candidates if g.elevation is not None), None)
    chosen.crs = next((g.crs for g in candidates if g.crs), None)

    checks = []
    for g in candidates:
        if g.points:
            devs = deviations(chosen.affine, g.points, chosen.affine_z)
            checks.append({"source": g.source, "max": max(d["d"] for d in devs), "points": devs})

    data, mime, notes = _display_copy(image)
    notes = errors + notes
    return RasterImage(name=name, width=width, height=height, image=data, mime=mime, georef=chosen, checks=checks, notes=notes)


def pair_files(names: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Groups uploaded files by stem: {image name: [sidecar names]}; also returns files that matched nothing."""
    images = [n for n in names if Path(n).suffix.lower() in IMAGE_SUFFIXES]
    sidecars = [n for n in names if Path(n).suffix.lower() in SIDECAR_SUFFIXES]
    groups: dict[str, list[str]] = {n: [] for n in images}
    used = set(images)
    for s in sidecars:
        stem = Path(s).stem.lower()
        for image in images:
            # "map.jpg" pairs with "map.jgw", "map.tab" and also "map.jpg.aux"-style "map.jpg.tab" names.
            if Path(image).stem.lower() == stem or image.lower() == stem:
                groups[image].append(s)
                used.add(s)
                break
    return groups, [n for n in names if n not in used]
