"""Line (string) readers: Micromine STR, Surpac STR and DXF.

Every reader returns polylines grouped into layers: the STRING code of an STR file or the
DXF layer. A layer becomes one item of the project tree with its own colour and visibility.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from app.formats.micromine import MAGIC, MicromineFormatError, read_micromine

LINE_SUFFIXES = (".str", ".dxf")
_NO_CODE = "Без кода"


class LineFormatError(ValueError):
    """The file cannot be read as lines; the message is shown to the user."""


@dataclass
class Polyline:
    vertices: np.ndarray  # (N, 3) float64
    closed: bool
    attributes: dict = field(default_factory=dict)


@dataclass
class LineLayer:
    name: str
    lines: list[Polyline]
    color: str | None = None


def _closed(vertices: np.ndarray, tolerance: float = 1e-6) -> bool:
    return len(vertices) > 2 and bool(np.all(np.abs(vertices[0] - vertices[-1]) <= tolerance))


def _layers(groups: dict[str, list[Polyline]], colors: dict[str, str | None] | None = None) -> list[LineLayer]:
    layers = [LineLayer(name, lines, (colors or {}).get(name)) for name, lines in groups.items() if lines]
    if not layers:
        raise LineFormatError("В файле нет линий")
    return layers


def line_stats(lines: list[Polyline]) -> dict:
    length = 0.0
    for line in lines:
        if len(line.vertices) > 1:
            length += float(np.linalg.norm(np.diff(line.vertices, axis=0), axis=1).sum())
    return {
        "length": round(length, 2),
        "closed": sum(1 for line in lines if line.closed),
        "flat": bool(lines) and all(np.ptp(line.vertices[:, 2]) == 0 for line in lines),
    }


# ---------------------------------------------------------------- Micromine STR


def _column(frame: pd.DataFrame, *names: str) -> str | None:
    upper = {str(c).strip().upper(): c for c in frame.columns}
    for name in names:
        if name in upper:
            return upper[name]
    return None


def read_micromine_str(path: Path) -> list[LineLayer]:
    """A Micromine DAT with EAST, NORTH, RL, JOIN and STRING: a change of JOIN or STRING starts a new line."""
    try:
        table = read_micromine(path)
    except MicromineFormatError as exc:
        raise LineFormatError(f"Файл не читается как Micromine STR: {exc}") from exc
    frame = table.frame
    east, north = _column(frame, "EAST", "X"), _column(frame, "NORTH", "Y")
    rl = _column(frame, "RL", "Z", "ELEV")
    join, code = _column(frame, "JOIN"), _column(frame, "STRING")
    if east is None or north is None:
        raise LineFormatError("В STR нет полей EAST и NORTH")
    xyz = np.column_stack([
        frame[east].to_numpy(dtype=float),
        frame[north].to_numpy(dtype=float),
        frame[rl].to_numpy(dtype=float) if rl is not None else np.zeros(len(frame)),
    ])
    xyz[:, 2] = np.nan_to_num(xyz[:, 2])
    extra = [c for c in frame.columns if c not in {east, north, rl, join, code}]

    groups: dict[str, list[Polyline]] = {}
    keys = list(zip(
        frame[join].tolist() if join is not None else [0] * len(frame),
        frame[code].tolist() if code is not None else [""] * len(frame),
    ))
    start = 0
    for i in range(1, len(frame) + 1):
        if i < len(frame) and keys[i] == keys[start]:
            continue
        points = xyz[start:i]
        points = points[np.isfinite(points[:, :2]).all(axis=1)]
        if len(points) >= 2:
            name = str(keys[start][1]).strip() or _NO_CODE
            row = frame.iloc[start]
            attributes = {str(c): (None if pd.isna(row[c]) else row[c].item() if hasattr(row[c], "item") else row[c]) for c in extra}
            if join is not None and not pd.isna(keys[start][0]):
                attributes["JOIN"] = keys[start][0]
            groups.setdefault(name, []).append(Polyline(points, _closed(points), attributes))
        start = i
    return _layers(groups)


# ---------------------------------------------------------------- Surpac STR

_NUMBER = re.compile(r"^-?\d+(\.\d*)?([eE][-+]?\d+)?$")


def read_surpac_str(path: Path) -> list[LineLayer]:
    """Text: a header and an axis line, then `string, Y, X, Z, descriptions…`.
    A record with string number 0 ends a segment; `0, …, END` ends the file."""
    try:
        text = path.read_bytes().decode("cp1251", errors="replace")
    except OSError as exc:
        raise LineFormatError(str(exc)) from exc
    rows = text.splitlines()
    if len(rows) < 3:
        raise LineFormatError("Файл слишком короткий для Surpac STR")
    groups: dict[str, list[Polyline]] = {}
    current: list[tuple[float, float, float]] = []
    current_string = None
    descriptions: dict = {}

    def flush() -> None:
        nonlocal current
        if len(current) >= 2:
            points = np.array(current, dtype=float)
            groups.setdefault(f"Стринг {current_string}", []).append(Polyline(points, _closed(points), descriptions))
        current = []

    for line in rows[2:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4 or not _NUMBER.match(parts[0]):
            continue
        number = int(float(parts[0]))
        if number == 0:
            flush()
            if any(p.upper() == "END" for p in parts[4:]):
                break
            continue
        try:
            y, x, z = float(parts[1]), float(parts[2]), float(parts[3])
        except ValueError:
            continue
        if number != current_string:
            flush()
            current_string = number
            descriptions = {f"D{i}": v for i, v in enumerate(parts[4:], start=1) if v}
        current.append((x, y, z))  # Surpac stores Y before X
    flush()
    return _layers(groups)


def read_str(path: Path) -> tuple[str, list[LineLayer]]:
    with path.open("rb") as f:
        head = f.read(len(MAGIC))
    if head == MAGIC:
        return "Micromine STR", read_micromine_str(path)
    return "Surpac STR", read_surpac_str(path)


# ---------------------------------------------------------------- DXF


def read_dxf_lines(path: Path, flatten: float = 0.1) -> list[LineLayer]:
    """LINE, LWPOLYLINE, POLYLINE (2D/3D), SPLINE, ARC, CIRCLE, ELLIPSE; curves are flattened."""
    import ezdxf
    from ezdxf import path as dxfpath
    from ezdxf import recover

    from app.formats.wireframes import _dxf_color

    try:
        doc, _ = recover.readfile(str(path))
    except (OSError, ezdxf.DXFStructureError) as exc:
        raise LineFormatError(f"Файл не читается как DXF: {exc}") from exc

    groups: dict[str, list[Polyline]] = {}
    colors: dict[str, str | None] = {}
    kinds = {"LINE", "LWPOLYLINE", "POLYLINE", "SPLINE", "ARC", "CIRCLE", "ELLIPSE"}
    for entity in doc.modelspace():
        kind = entity.dxftype()
        if kind not in kinds or (kind == "POLYLINE" and (entity.is_poly_face_mesh or entity.is_polygon_mesh)):
            continue
        try:
            points = np.array([(v.x, v.y, v.z) for v in dxfpath.make_path(entity).flattening(flatten)], dtype=float)
        except (TypeError, ValueError, AttributeError, IndexError):
            continue
        if len(points) < 2:
            continue
        layer = entity.dxf.get("layer", "0")
        closed = _closed(points) or bool(getattr(entity, "is_closed", False)) or kind in ("CIRCLE",)
        if closed and not _closed(points):
            points = np.vstack([points, points[:1]])
        groups.setdefault(layer, []).append(Polyline(points, closed, {"entity": kind}))
        colors.setdefault(layer, _dxf_color(entity, doc))
    if not groups:
        raise LineFormatError("В DXF нет линий: ожидаются LINE, LWPOLYLINE, POLYLINE, SPLINE, ARC, CIRCLE")
    return _layers(groups, colors)


def read_lines(path: Path, suffix: str) -> tuple[str, list[LineLayer]]:
    """Returns (format name, layers)."""
    suffix = suffix.lower()
    if suffix == ".str":
        return read_str(path)
    if suffix == ".dxf":
        return "DXF", read_dxf_lines(path)
    raise LineFormatError(f"Формат {suffix or 'без расширения'} не поддерживается для линий: нужны .str или .dxf")


def pack(lines: list[Polyline]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """All lines of a layer as one vertex array, start offsets (len = lines + 1) and closed flags."""
    offsets = np.zeros(len(lines) + 1, dtype=np.int64)
    offsets[1:] = np.cumsum([len(line.vertices) for line in lines])
    vertices = np.vstack([line.vertices for line in lines]) if lines else np.zeros((0, 3))
    closed = np.array([line.closed for line in lines], dtype=np.uint8)
    return vertices, offsets, closed
