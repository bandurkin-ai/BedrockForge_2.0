"""`CoordSys` clause of a MapInfo MIF file → a coordinate system (ТЗ 7.35 Д-13; ADR 0009, ADR 0011 §2).

`parse_coordsys(clause)` → `CoordSys(kind, crs, warning)`; `crs` is a `bf_core.crs.CrsInfo` (WKT, name, EPSG).
What is recognised (the table of ADR 0011; other systems wait for the samples of the owner):
  * `Earth Projection 1, datum` — longitude / latitude;
  * `Earth Projection 8, datum, "m", lon0, lat0, scale, false easting, false northing` — transverse Mercator:
    UTM zones of WGS 84 and the Gauss–Krüger zones of Pulkovo 1942 (6° and 3°, with and without the zone
    number in the easting) get their EPSG codes — the code is taken only when the parameters of the EPSG
    system are the same; any other set of parameters gives a WKT without a code;
  * datums: 104 — WGS 84, 1001 — Pulkovo 1942;
  * `Bounds (…) (…)` is ignored, projection numbers + 2000 (explicit bounds) are the same projections.
Everything else — other projections and datums, custom datums (999, 9999), affine transformations (+ 1000,
+ 3000, `Affine Units`), units other than metres, `NonEarth`, `Layout`, `Table`, `Window` — gives no CRS and a
`warning` in Russian: the object is loaded without a coordinate system, never refused (a CRS is a label in v1,
nothing is reprojected). A file without the clause is longitude / latitude of an unknown datum: no CRS either.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bf_core.crs import CrsInfo

EARTH, NON_EARTH, OTHER = "earth", "nonearth", "other"
LONGLAT, TRANSVERSE_MERCATOR = 1, 8
#: MapInfo datum number → EPSG code of the geographic system
DATUMS = {104: 4326, 1001: 4284}
WGS84, PULKOVO42 = 4326, 4284
_NO_CRS = "объект загружен без системы координат"
_CUT = re.compile(r"\b(?:bounds|affine)\b.*$", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class CoordSys:
    kind: str  # "earth" | "nonearth" | "other"
    crs: CrsInfo | None
    warning: str | None = None  # why there is no CRS (Russian, for the operation log)


def _unknown(reason: str) -> CoordSys:
    return CoordSys(EARTH, None, f"Система координат MapInfo не распознана ({reason}): {_NO_CRS}")


def _whole(value: float) -> int | None:
    nearest = round(value)
    return nearest if abs(value - nearest) < 1e-9 else None


def _meridian(lon0: float) -> str:
    return f"{abs(lon0):g}{'W' if lon0 < 0 else 'E'}"


def _names(geo: int, lon0: float, lat0: float, scale: float, east: float, north: float) -> list[str]:
    """Names of the EPSG systems a transverse Mercator with these parameters may be, the preferred first."""
    names: list[str] = []
    if lat0 != 0.0:
        return names
    if geo == WGS84 and scale == 0.9996 and east == 500000.0 and north in (0.0, 10000000.0):
        zone = _whole((lon0 + 183.0) / 6.0)
        if zone is not None and 1 <= zone <= 60:
            names.append(f"WGS 84 / UTM zone {zone}{'N' if north == 0.0 else 'S'}")
    if geo == PULKOVO42 and scale == 1.0 and north == 0.0:
        turn = lon0 % 360.0  # zones are counted eastwards from Greenwich
        six, three = _whole((turn + 3.0) / 6.0), _whole(turn / 3.0)
        if six is not None and east == six * 1e6 + 5e5:
            names.append(f"Pulkovo 1942 / Gauss-Kruger zone {six}")
        if three is not None and east == three * 1e6 + 5e5:
            names.append(f"Pulkovo 1942 / 3-degree Gauss-Kruger zone {three}")
        if east == 5e5:
            if six is not None:
                names.append(f"Pulkovo 1942 / Gauss-Kruger CM {_meridian(lon0)}")
            if three is not None:
                names.append(f"Pulkovo 1942 / 3-degree Gauss-Kruger CM {_meridian(lon0)}")
    return names


def _transverse(geo: int, lon0: float, lat0: float, scale: float, east: float, north: float) -> CrsInfo:
    from pyproj import CRS
    from pyproj.crs import ProjectedCRS
    from pyproj.crs.coordinate_operation import TransverseMercatorConversion
    from pyproj.exceptions import CRSError

    from bf_core.crs import describe

    wanted = {
        "Latitude of natural origin": lat0,
        "Longitude of natural origin": lon0,
        "Scale factor at natural origin": scale,
        "False easting": east,
        "False northing": north,
    }
    for name in _names(geo, lon0, lat0, scale, east, north):
        try:
            found = CRS.from_user_input(name)
        except CRSError:
            continue  # no such zone in the EPSG registry
        operation, base = found.coordinate_operation, found.geodetic_crs
        code = found.to_epsg()
        if operation is None or base is None or code is None or base.to_epsg() != geo:
            continue
        have = {p.name: float(p.value) for p in operation.params if not isinstance(p.value, str)}
        if operation.method_name == "Transverse Mercator" and all(
            math.isclose(have.get(key, math.nan), value, rel_tol=0.0, abs_tol=1e-9) for key, value in wanted.items()
        ):
            return describe(f"EPSG:{code}")
    base_crs = CRS.from_epsg(geo)
    conversion = TransverseMercatorConversion(
        latitude_natural_origin=lat0,
        longitude_natural_origin=lon0,
        false_easting=east,
        false_northing=north,
        scale_factor_natural_origin=scale,
    )
    title = f"{base_crs.name} / Transverse Mercator CM {_meridian(lon0)}"
    return describe(ProjectedCRS(conversion=conversion, geodetic_crs=base_crs, name=title).to_wkt())


def parse_coordsys(clause: str | None) -> CoordSys:
    """The `CoordSys …` clause of a MIF header (with or without the keyword) → `CoordSys`."""
    text = " ".join((clause or "").split())
    body = re.sub(r"^coordsys\s+", "", text, flags=re.IGNORECASE)
    if not body:
        return CoordSys(OTHER, None, f"В файле не указана система координат: {_NO_CRS}")
    if body.lower().startswith("nonearth"):
        return CoordSys(NON_EARTH, None, f"Система координат файла условная (NonEarth): {_NO_CRS}")
    match = re.match(r"earth\s+projection\s+(.*)$", body, re.IGNORECASE)
    if match is None:
        return CoordSys(OTHER, None, f"Система координат «{body[:60]}» не распознана: {_NO_CRS}")
    params = [p.strip().strip('"').strip() for p in _CUT.sub("", match.group(1)).split(",")]
    try:
        projection, datum = int(params[0]), int(params[1]) if len(params) > 1 else None
    except ValueError:
        return _unknown("параметры проекции не читаются")
    modifier, projection = divmod(projection, 1000)
    if modifier in (1, 3) or re.search(r"\baffine\b", match.group(1), re.IGNORECASE):
        return _unknown("аффинное преобразование координат")
    if projection not in (LONGLAT, TRANSVERSE_MERCATOR):
        return _unknown(f"проекция {projection}")
    if datum is None:
        return _unknown("датум не указан")
    if datum in (999, 9999):
        return _unknown("пользовательский датум")
    geo = DATUMS.get(datum)
    if geo is None:
        return _unknown(f"датум {datum}")

    from bf_core.crs import describe

    if projection == LONGLAT:
        return CoordSys(EARTH, describe(f"EPSG:{geo}"))
    if len(params) < 8:
        return _unknown("не хватает параметров проекции")
    if params[2].lower() not in ("m", "meter", "metre"):
        return _unknown(f"единицы «{params[2]}»")
    try:
        lon0, lat0, scale, east, north = (float(p) for p in params[3:8])
    except ValueError:
        return _unknown("параметры проекции не читаются")
    if not all(math.isfinite(v) for v in (lon0, lat0, scale, east, north)) or scale <= 0 or abs(lon0) > 360:
        return _unknown("параметры проекции не читаются")
    return CoordSys(EARTH, _transverse(geo, lon0, lat0, scale, east, north))


__all__ = ["DATUMS", "CoordSys", "parse_coordsys"]
