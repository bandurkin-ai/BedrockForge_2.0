"""Suggested column mapping: names of Micromine, Datamine, Surpac, Vulcan, Leapfrog and Russian tables."""

from __future__ import annotations

import pytest

from app.formats.blockmodel import BlockModelError, read_block_model
from app.importing.names import canon, match_column
from app.importing.service import detect_kind, suggest_mapping


def test_canonical_names_drop_units_and_separators():
    assert canon("Depth_m") == "DEPTH"
    assert canon("Dip (deg)") == "DIP"
    assert canon("Глубина, м") == "ГЛУБИНА"
    assert canon("Hole ID") == "HOLE_ID"
    assert canon("Х") == "X"  # Cyrillic
    assert canon("M") == "M"  # a lone unit word stays a name


def test_micromine_names_cut_to_ten_characters():
    cols = ["DrillholeI", "Depth_m", "AzimuthMag", "AngleZenit", "GridBearin", "Dip_deg", "Comment"]
    assert detect_kind(cols) == "survey"
    # The grid azimuth is preferred to the magnetic one; the zenith angle is not taken for the dip.
    assert suggest_mapping("survey", cols) == {"hole_id": "DrillholeI", "depth": "Depth_m", "azimuth": "GridBearin", "dip": "Dip_deg"}


@pytest.mark.parametrize("cols, expected", [
    (["BHID", "XCOLLAR", "YCOLLAR", "ZCOLLAR", "LENGTH"], ["BHID", "XCOLLAR", "YCOLLAR", "ZCOLLAR", "LENGTH"]),  # Datamine
    (["hole_id", "x", "y", "z", "max_depth"], ["hole_id", "x", "y", "z", "max_depth"]),  # Leapfrog
    (["HOLE_ID", "EAST", "NORTH", "RL", "EOH"], ["HOLE_ID", "EAST", "NORTH", "RL", "EOH"]),  # acQuire
    (["Hole Name", "Easting (m)", "Northing (m)", "Elevation (m)", "Total Depth"], ["Hole Name", "Easting (m)", "Northing (m)", "Elevation (m)", "Total Depth"]),
    (["Скважина", "Х", "У", "Отметка, м", "Глубина, м"], ["Скважина", "Х", "У", "Отметка, м", "Глубина, м"]),
])
def test_collars_of_different_systems(cols, expected):
    m = suggest_mapping("collar", cols)
    assert [m.get(k) for k in ("hole_id", "x", "y", "z", "td")] == expected


def test_intervals_and_samples():
    m = suggest_mapping("assay", ["BHID", "SAMPFROM", "SAMPTO", "SAMPLE", "AU_PPM"])
    assert m == {"hole_id": "BHID", "from": "SAMPFROM", "to": "SAMPTO", "sample_id": "SAMPLE"}
    assert suggest_mapping("interval", ["Скв", "Кровля", "Подошва", "Порода"]) == {"hole_id": "Скв", "from": "Кровля", "to": "Подошва"}


def test_a_column_is_used_once_and_short_names_are_not_guessed():
    assert match_column(["HOLE", "HOLE_DEPTH"], ("HOLE_ID", "HOLE"), {"HOLE"}) is None
    assert match_column(["AZ"], ("AZIMUTH",)) is None  # too short to be a cut name


def _csv(tmp_path, header, rows):
    p = tmp_path / "bm.csv"
    p.write_text(",".join(header) + "\n" + "\n".join(",".join(map(str, r)) for r in rows) + "\n", encoding="utf-8")
    return p


def test_block_model_fields_of_other_systems_and_the_users_choice(tmp_path):
    rows = [[5, 5, 2.5, 10, 10, 5, 1.2], [15, 5, 2.5, 10, 10, 5, 0.7]]
    p = _csv(tmp_path, ["Centroid X", "Centroid Y", "Centroid Z", "Size X", "Size Y", "Size Z", "Au"], rows)
    t = read_block_model(p, "bm.csv")
    assert t.roles == {"x": "Centroid X", "y": "Centroid Y", "z": "Centroid Z", "dx": "Size X", "dy": "Size Y", "dz": "Size Z"}

    # The user's choice wins: here the sizes are left out (a regular model by the spacing of centres).
    p = _csv(tmp_path, ["A", "B", "C", "Au"], [[5, 5, 2.5, 1.2], [15, 5, 2.5, 0.7], [5, 15, 2.5, 0.1]])
    with pytest.raises(BlockModelError):
        read_block_model(p, "bm.csv")
    t = read_block_model(p, "bm.csv", roles={"x": "A", "y": "B", "z": "C", "dx": "", "dy": "", "dz": ""})
    assert t.roles["x"] == "A" and len(t.centres) == 3
    with pytest.raises(BlockModelError, match="нет"):
        read_block_model(p, "bm.csv", roles={"x": "Q"})


def test_data_errors_can_be_loaded_to_fix_in_bedrockforge():
    import pandas as pd

    from app.importing.service import DATA_ERRORS, apply_mapping

    df = pd.DataFrame({"HOLE": ["A", "", "A", "B"], "X": ["1", "2", "3", "x?"], "Y": ["1", "2", "3", "4"],
                       "Z": ["1", "2", "3", "4"], "TD": ["10", "20", "30", "40"]})
    mapping = {"hole_id": "HOLE", "x": "X", "y": "Y", "z": "Z", "td": "TD"}
    out, issues = apply_mapping(df, "collar", mapping, [], [])
    errors = {i.code for i in issues if i.severity == "error"}
    assert errors == {"empty_hole_id", "duplicate_hole_id", "non_numeric"} and errors <= DATA_ERRORS

    out, issues = apply_mapping(df, "collar", mapping, [], [], force=True)
    assert not [i for i in issues if i.severity == "error"]
    assert all("исправьте в BedrockForge" in i.message for i in issues if i.code in DATA_ERRORS)
    assert list(out["hole_id"]) == ["A", "БЕЗ НОМЕРА (строка 3)", "A (повтор 2)", "B"]
    assert pd.isna(out["x"].iloc[3]) and out["attr:X (исходное)"].iloc[3] == "x?"
    assert pd.isna(out["attr:X (исходное)"].iloc[0])

    # A mistake in the mapping is never taken.
    out, issues = apply_mapping(df, "collar", {"hole_id": "HOLE"}, [], [], force=True)
    assert out is None and any(i.code == "missing_required" for i in issues)


def test_a_collar_table_with_units_in_names_is_not_a_block_model():
    from app.formats.blockmodel import looks_like_block_model

    assert not looks_like_block_model(["DrillholeI", "East_m", "North_m", "RL_m", "TDepth"])
    assert not looks_like_block_model(["Скважина", "X", "Y", "Z", "Глубина"])
    assert looks_like_block_model(["X", "Y", "Z", "AU", "CU"])
    assert looks_like_block_model(["XC", "YC", "ZC", "XINC", "YINC", "ZINC", "HOLE"])  # sizes decide
