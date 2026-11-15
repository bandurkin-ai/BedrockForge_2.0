"""Which kind of project object a file holds, decided from its name and first bytes.

Used by the import wizard (routers/jobs.py) to suggest the object kind; the user may override it.
Drillhole tables (DAT, CSV, Excel) go through the column-mapping wizard (routers/imports.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.formats.rasters import IMAGE_SUFFIXES, SIDECAR_SUFFIXES

OBJECT_KINDS = ("wireframes", "lines", "rasters", "blocks")
TABLE_SUFFIXES = (".dat", ".csv", ".txt", ".xlsx", ".xlsm", ".xls")

# DXF entity names that hold triangles (wireframes) rather than lines.
_DXF_FACE_MARKERS = (b"3DFACE", b"AcDbPolyFaceMesh", b"AcDbPolygonMesh", b"AcDbSubDMesh")
_SCAN_BYTES = 64 << 20


@dataclass(frozen=True)
class Detected:
    kind: str  # wireframes | lines | rasters | blocks | sidecar | drillholes | unknown
    format: str
    note: str = ""


def _dxf_has_faces(path: Path) -> bool:
    """Streams the start of the file for face entities; cheap even for a 1 GB DXF."""
    tail = b""
    read = 0
    with path.open("rb") as f:
        while read < _SCAN_BYTES:
            chunk = f.read(1 << 20)
            if not chunk:
                return False
            read += len(chunk)
            data = tail + chunk
            if any(m in data for m in _DXF_FACE_MARKERS):
                return True
            tail = data[-32:]
    return False


def detect(filename: str, path: Path | None = None) -> Detected:
    suffix = Path(filename).suffix.lower()
    if suffix == ".tridb":
        return Detected("wireframes", "Micromine TRIDB")
    if suffix == ".obj":
        return Detected("wireframes", "OBJ")
    if suffix == ".stl":
        return Detected("wireframes", "STL", "Координаты в STL — float32: на UTM точность около 0,5 м")
    if suffix == ".str":
        return Detected("lines", "STR (Micromine / Surpac)")
    if suffix == ".dxf":
        if path is not None and _dxf_has_faces(path):
            return Detected("wireframes", "DXF", "В файле есть грани (3DFACE / POLYFACE / MESH)")
        return Detected("lines", "DXF", "Граней не найдено: файл читается как линии")
    if suffix in IMAGE_SUFFIXES:
        return Detected("rasters", suffix.lstrip(".").upper())
    if suffix in SIDECAR_SUFFIXES:
        return Detected("sidecar", suffix.lstrip(".").upper(), "Файл привязки: загружайте вместе с изображением")
    if suffix in (".dat", ".csv", ".txt") and path is not None:
        from app.formats.blockmodel import header_fields, looks_like_block_model

        names = header_fields(path, filename)
        if names and looks_like_block_model(names):
            return Detected("blocks", "Micromine DAT" if suffix == ".dat" else "CSV",
                            "Блочная модель: центр и размер блоков")
    if suffix in TABLE_SUFFIXES:
        return Detected("drillholes", suffix.lstrip(".").upper(), "Таблица: импорт через мастер скважин")
    return Detected("unknown", suffix.lstrip(".").upper() or "без расширения", "Формат не поддерживается")
