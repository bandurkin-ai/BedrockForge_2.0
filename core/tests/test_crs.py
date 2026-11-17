"""ESRI .prj (WKT) read and write (ТЗ 9.12)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bf_core.crs import CrsError, describe, read_prj, same, write_prj

PULKOVO_GK7 = (
    'PROJCS["Pulkovo_1942_GK_Zone_7",GEOGCS["GCS_Pulkovo_1942",DATUM["D_Pulkovo_1942",'
    'SPHEROID["Krasovsky_1940",6378245.0,298.3]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Gauss_Kruger"],PARAMETER["False_Easting",7500000.0],PARAMETER["False_Northing",0.0],'
    'PARAMETER["Central_Meridian",39.0],PARAMETER["Scale_Factor",1.0],PARAMETER["Latitude_Of_Origin",0.0],'
    'UNIT["Meter",1.0]]'
)


def test_esri_prj_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "a.prj"
    src.write_text(PULKOVO_GK7, encoding="cp1251")
    info = read_prj(src)
    assert info.projected and info.units in ("metre", "Meter") and "Pulkovo" in info.name
    out = tmp_path / "b.prj"
    write_prj(out, info.wkt)
    assert same(read_prj(out).wkt, info.wkt)


def test_epsg_code_and_bad_text() -> None:
    info = describe("EPSG:32637")
    assert info.epsg == 32637 and info.projected
    assert not same(info.wkt, describe("EPSG:4326").wkt)
    assert same(None, None) and not same(None, info.wkt)
    with pytest.raises(CrsError, match="не удалось|Не удалось"):
        describe("это не система координат")
