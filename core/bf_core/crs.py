"""Coordinate reference systems of objects (ТЗ 9.12: ESRI `.prj` WKT, import and export).

A set or object keeps its CRS as WKT (objects.crs). No reprojection in v1: the CRS is a label checked for
consistency; pyproj only parses and names it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAX_PRJ_BYTES = 64 * 1024


class CrsError(ValueError):
    """The text is not a coordinate system (Russian message for the user)."""


@dataclass(frozen=True)
class CrsInfo:
    wkt: str
    name: str
    epsg: int | None
    units: str | None
    projected: bool

    def as_dict(self) -> dict[str, object]:
        return {"wkt": self.wkt, "name": self.name, "epsg": self.epsg, "units": self.units, "projected": self.projected}


def describe(text: str) -> CrsInfo:
    """WKT (ESRI or OGC), «EPSG:32637» or a PROJ string → its name, EPSG code and units."""
    from pyproj import CRS
    from pyproj.exceptions import CRSError

    text = text.strip().lstrip("﻿")
    if not text:
        raise CrsError("Система координат не задана.")
    try:
        crs = CRS.from_user_input(text)
    except CRSError as exc:
        raise CrsError(
            "Не удалось прочитать систему координат: файл .prj повреждён или имеет неизвестный формат."
        ) from exc
    units = crs.axis_info[0].unit_name if crs.axis_info else None
    return CrsInfo(crs.to_wkt(), crs.name, crs.to_epsg(min_confidence=70), units, crs.is_projected)


def read_prj(path: Path) -> CrsInfo:
    with path.open("rb") as f:
        data = f.read(MAX_PRJ_BYTES + 1)
    if len(data) > MAX_PRJ_BYTES:
        raise CrsError("Файл .prj слишком большой для описания системы координат.")
    for enc in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            return describe(data.decode(enc))
        except UnicodeDecodeError:
            continue
    raise CrsError("Файл .prj не в текстовой кодировке.")


def write_prj(path: Path, wkt: str) -> None:
    """ESRI flavour of WKT: what GIS programs expect in a .prj next to the data."""
    from pyproj import CRS

    path.write_text(CRS.from_wkt(wkt).to_wkt("WKT1_ESRI"), encoding="utf-8")


def same(a: str | None, b: str | None) -> bool:
    """Whether two CRS are the same (both unset counts as the same)."""
    if not a or not b:
        return not a and not b
    from pyproj import CRS

    return bool(CRS.from_user_input(a).equals(CRS.from_user_input(b), ignore_axis_order=True))
