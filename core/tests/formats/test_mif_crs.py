"""`CoordSys` of MapInfo MIF → coordinate system: UTM, Gauss–Krüger of Pulkovo 1942, longitude / latitude, and
everything that is not recognised (ТЗ 7.35 Д-13; ADR 0011 §2).

Reference: the EPSG registry of pyproj — the code found must carry the expected name, and the WKT returned must
resolve to the same code; parameters of a system without a code are compared exactly.
"""

from __future__ import annotations

import pytest

from bf_core.formats.mif_crs import parse_coordsys


@pytest.mark.parametrize(
    ("clause", "epsg", "name"),
    [
        ('CoordSys Earth Projection 8, 104, "m", 39, 0, 0.9996, 500000, 0', 32637, "WGS 84 / UTM zone 37N"),
        ('CoordSys Earth Projection 8, 104, "m", 39, 0, 0.9996, 500000, 10000000', 32737, "WGS 84 / UTM zone 37S"),
        ('CoordSys Earth Projection 8, 104, "m", -177, 0, 0.9996, 500000, 0', 32601, "WGS 84 / UTM zone 1N"),
        ('CoordSys Earth Projection 8, 1001, "m", 39, 0, 1, 7500000, 0', 28407, "Pulkovo 1942 / Gauss-Kruger zone 7"),
        ('CoordSys Earth Projection 8, 1001, "m", 135, 0, 1, 23500000, 0', 28423,
         "Pulkovo 1942 / Gauss-Kruger zone 23"),
        ('CoordSys Earth Projection 8, 1001, "m", -171, 0, 1, 32500000, 0', 28432,
         "Pulkovo 1942 / Gauss-Kruger zone 32"),
        ('coordsys earth projection 2008, 1001, "m", 39, 0, 1, 500000, 0 Bounds (0, 0) (1, 1)', 2497,
         "Pulkovo 1942 / Gauss-Kruger CM 39E"),
        ('CoordSys Earth Projection 8, 1001, "m", 42, 0, 1, 14500000, 0', 2530,
         "Pulkovo 1942 / 3-degree Gauss-Kruger zone 14"),
        ('CoordSys Earth Projection 8, 1001, "m", 42, 0, 1, 500000, 0', 2589,
         "Pulkovo 1942 / 3-degree Gauss-Kruger CM 42E"),
        ("CoordSys Earth Projection 1, 104", 4326, "WGS 84"),
        ("Earth Projection 1, 1001", 4284, "Pulkovo 1942"),
    ],
)  # fmt: skip
def test_coordsys_with_an_epsg_code(clause: str, epsg: int, name: str) -> None:
    from pyproj import CRS

    system = parse_coordsys(clause)
    assert system.kind == "earth" and system.warning is None and system.crs is not None
    assert (system.crs.epsg, system.crs.name) == (epsg, name)
    assert CRS.from_wkt(system.crs.wkt).to_epsg() == epsg and CRS.from_epsg(epsg).name == name  # the registry agrees


def test_coordsys_without_an_epsg_code_keeps_its_parameters() -> None:
    from pyproj import CRS

    system = parse_coordsys('CoordSys Earth Projection 8, 1001, "m", 38.5, 0, 1, 1250000, -5412900.566')
    assert system.crs is not None and system.crs.epsg is None and system.warning is None
    assert system.crs.name == "Pulkovo 1942 / Transverse Mercator CM 38.5E" and system.crs.projected
    crs = CRS.from_wkt(system.crs.wkt)
    assert crs.geodetic_crs is not None and crs.geodetic_crs.to_epsg() == 4284 and crs.coordinate_operation is not None
    assert {p.name: p.value for p in crs.coordinate_operation.params} == {
        "Latitude of natural origin": 0.0,
        "Longitude of natural origin": 38.5,
        "Scale factor at natural origin": 1.0,
        "False easting": 1250000.0,
        "False northing": -5412900.566,
    }
    # a zone number in the easting that is not the zone of the meridian: not an EPSG system
    wrong = parse_coordsys('CoordSys Earth Projection 8, 1001, "m", 39, 0, 1, 8500000, 0')
    assert wrong.crs is not None and wrong.crs.epsg is None
    assert parse_coordsys('CoordSys Earth Projection 8, 104, "m", 37, 0, 0.9996, 500000, 0').crs.epsg is None  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("clause", "kind", "reason"),
    [
        ('CoordSys NonEarth Units "m" Bounds (0, 0) (1000, 1000)', "nonearth", "условная (NonEarth)"),
        ('CoordSys Earth Projection 3, 74, "m", -96, 23, 29.5, 45.5, 0, 0', "earth", "проекция 3"),
        ('CoordSys Earth Projection 8, 74, "m", -96, 0, 0.9996, 500000, 0', "earth", "датум 74"),
        ('CoordSys Earth Projection 8, 9999, 3, 23.57, -140.95, -79.8, 0, -0.35, -0.79, -0.22, 0, "m", 38.48333, 0, 1, '
         "1250000, -5412900.566", "earth", "пользовательский датум"),
        ('CoordSys Earth Projection 8, 104, "ft", 39, 0, 0.9996, 500000, 0', "earth", "единицы «ft»"),
        ('CoordSys Earth Projection 1008, 104, "m", 39, 0, 0.9996, 500000, 0', "earth", "аффинное преобразование"),
        ('CoordSys Earth Projection 8, 104, "m", 39, 0, 0.9996, 500000, 0 Affine Units "m", 1, 0, 0, 0, 1, 0', "earth",
         "аффинное преобразование"),
        ('CoordSys Earth Projection 8, 104, "m", 39', "earth", "не хватает параметров"),
        ('CoordSys Earth Projection 8, 104, "m", x, 0, 1, 0, 0', "earth", "не читаются"),
        ("CoordSys Earth Projection", "other", "не распознана"),
        ('CoordSys Layout Units "in"', "other", "не распознана"),
        ("", "other", "не указана"),
        (None, "other", "не указана"),
    ],
)  # fmt: skip
def test_unknown_coordsys_is_a_warning_not_an_error(clause: str | None, kind: str, reason: str) -> None:
    system = parse_coordsys(clause)
    assert system.kind == kind and system.crs is None and system.warning is not None
    assert reason in system.warning and system.warning.endswith("объект загружен без системы координат")
