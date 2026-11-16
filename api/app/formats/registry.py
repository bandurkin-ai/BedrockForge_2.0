"""Which kind of project object a file holds, decided from its name and first bytes.

Used by the import wizard (routers/jobs.py) to suggest the object kind; the user may override it.
Drillhole tables (DAT, CSV, Excel) go through the column-mapping wizard (routers/imports.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.formats.rasters import IMAGE_SUFFIXES, SIDECAR_SUFFIXES

# "omf" is a container: one file gives wireframes, lines and block models (importing/omf.py).
OBJECT_KINDS = ("wireframes", "lines", "rasters", "blocks", "omf")
TABLE_SUFFIXES = (".dat", ".csv", ".txt", ".xlsx", ".xlsm", ".xls")

# DXF entity names that hold triangles (wireframes) rather than lines.
_DXF_FACE_MARKERS = (b"3DFACE", b"AcDbPolyFaceMesh", b"AcDbPolygonMesh", b"AcDbSubDMesh")
_SCAN_BYTES = 64 << 20


@dataclass(frozen=True)
class Detected:
    kind: str  # wireframes | lines | rasters | blocks | omf | sidecar | drillholes | unknown
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
    if suffix == ".omf":
        from app.formats.omf import MAGIC, VERSION

        if path is not None:
            with path.open("rb") as f:
                head = f.read(36)
            if head[:4] != MAGIC:
                if head[:2] == b"PK":
                    return Detected("unknown", "OMF 2 (ZIP)", "Файл OMF 2 (архив ZIP) не поддерживается: сохраните проект в OMF 1 (Leapfrog: OMF 0.9)")
                return Detected("unknown", "OMF", "Нет сигнатуры OMF в начале файла: файл повреждён или это другой формат")
            if not head[4:36].startswith(VERSION):
                version = head[4:36].rstrip(b"\x00").decode("ascii", "replace")
                return Detected("unknown", f"OMF {version}", "Версия OMF не поддерживается: читается OMF v1 (OMF-v0.9.0)")
        return Detected("omf", "OMF v1", "Контейнер: каркасы, линии и блочные модели загрузятся отдельными наборами")
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
        if suffix == ".dat" and names is None:
            from app.formats.micromine import MicromineFormatError, read_header

            try:
                read_header(path)
            except (MicromineFormatError, OSError, ValueError) as exc:
                return Detected("drillholes", "DAT", f"Заголовок Micromine не прочитан: {exc}")
        if names:
            return Detected("drillholes", suffix.lstrip(".").upper(),
                            f"Таблица (поля: {', '.join(names[:12])}{' …' if len(names) > 12 else ''}): импорт через мастер скважин. "
                            "Блочной модели нужны поля центра (X, Y, Z) и размера (_X, _Y, _Z)")
    if suffix in TABLE_SUFFIXES:
        return Detected("drillholes", suffix.lstrip(".").upper(), "Таблица: импорт через мастер скважин")
    return Detected("unknown", suffix.lstrip(".").upper() or "без расширения", "Формат не поддерживается")
