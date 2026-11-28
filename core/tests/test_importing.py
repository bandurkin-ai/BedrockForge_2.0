"""Import of drillhole tables: mapping, kind, limits, force (ТЗ 7.7 acceptance)."""

from __future__ import annotations

import math
from pathlib import Path

from bf_core.formats.registry import read_table
from bf_core.importing.service import detect_kind, normalize, suggest_components, suggest_mapping

ASSAY_CP1251 = (
    "Hole_ID;From_m;To_m;Au_ppm;Литология\nC1;0;1,5;<0,01;гранит\nC1;1,5;3;4 584,70;диорит\nC2;0;2;abc;кварц\n"
)


def _csv(tmp_path: Path, text: str, enc: str = "cp1251") -> Path:
    p = tmp_path / "a.csv"
    p.write_bytes(text.encode(enc))
    return p


def test_cp1251_semicolon_comma_read_and_mapped_without_settings(tmp_path: Path) -> None:
    t = read_table(_csv(tmp_path, ASSAY_CP1251))
    assert t.info.encoding and "1251" in t.info.encoding and t.info.delimiter == ";"
    assert detect_kind(t.names) == "assay"
    m = suggest_mapping("assay", t.names)
    assert m == {"hole_id": "Hole_ID", "from": "From_m", "to": "To_m"}
    comps = suggest_components(t, m)
    assert comps == [{"column": "Au_ppm", "unit": "ppm"}]  # text because of «abc», still mostly numbers


def test_limits_and_force_keep_the_original_text(tmp_path: Path) -> None:
    t = read_table(_csv(tmp_path, ASSAY_CP1251))
    m = suggest_mapping("assay", t.names)
    comps = [{"column": "Au_ppm", "unit": "ppm"}]
    res, issues = normalize(t, "assay", m, comps, ["Литология"])
    assert res is None and any(i.code == "non_numeric" and i.severity == "error" for i in issues)
    res, issues = normalize(t, "assay", m, comps, ["Литология"], force=True)
    assert res is not None and res.table is not None
    tab = res.table
    names = [c.name for c in tab.columns]
    au = tab.values[names.index("Au_ppm")]
    assert au[0] == 0.005 and au[1] == 4584.7 and math.isnan(au[2])
    assert tab.values[names.index("Au_ppm (исходное)")] == [None, None, "abc"]
    assert tab.columns[names.index("Au_ppm")].unit == "ppm" and tab.columns[names.index("Au_ppm")].role == "component"
    assert any(i.code == "below_limit" and i.count == 1 for i in issues)
    assert res.counts["below_limit"] == 1 and list(tab.from_m) == [0.0, 1.5, 0.0]


def test_collar_duplicates_and_empty_hole_with_force(tmp_path: Path) -> None:
    t = read_table(_csv(tmp_path, "HOLEID,EAST,NORTH,RL,DEPTH\nA,1,2,3,50\nA,4,5,6,60\n,7,8,9,70\n", "utf-8"))
    assert detect_kind(t.names) == "collar"
    m = suggest_mapping("collar", t.names)
    res, issues = normalize(t, "collar", m)
    assert res is None and {i.code for i in issues} >= {"duplicate_hole_id", "empty_hole_id"}
    res, _ = normalize(t, "collar", m, force=True)
    assert res is not None
    assert [c.name for c in res.collars] == ["A", "A (повтор 2)", "БЕЗ НОМЕРА (строка 4)"]
    assert res.collars[1].x == 4.0 and res.collars[0].td == 50.0


def test_survey_and_interval_kinds(tmp_path: Path) -> None:
    t = read_table(_csv(tmp_path, "BHID\tAT\tBRG\tDIP\nA\t0\t90\t-60\nA\t30\t91\t-59\n", "utf-8"))
    assert detect_kind(t.names) == "survey"
    res, _ = normalize(t, "survey", suggest_mapping("survey", t.names))
    assert res is not None and res.surveys == [("A", 0.0, 90.0, -60.0, None), ("A", 30.0, 91.0, -59.0, None)]
    t = read_table(_csv(tmp_path, "Скважина;От;До;Порода\nA;0;5;гранит\n"))
    assert detect_kind(t.names) == "interval"
    res, _ = normalize(t, "interval", suggest_mapping("interval", t.names), attributes=["Порода"])
    assert res is not None and res.table is not None and res.table.values == [["гранит"]]
    assert not math.isnan(res.table.to_m[0] or math.nan)


def test_component_unit_from_the_name(tmp_path: Path) -> None:
    t = read_table(_csv(tmp_path, "Hole_ID,From_m,To_m,Au_ppm,Cu_pct,Comments\nA,0,1,0.5,0.1,x\n", "utf-8"))
    comps = suggest_components(t, suggest_mapping("assay", t.names))
    assert comps == [{"column": "Au_ppm", "unit": "ppm"}, {"column": "Cu_pct", "unit": "%"}]


def test_interval_table_with_xyz_is_not_a_collar() -> None:
    """Д-41 А2: From–To wins over X/Y/Z (the coordinates of the middle of the sample)."""
    cols = ["Hole-ID", "Sample", "From", "To", "Length", "Cu_%", "Ag_G/T", "X", "Y", "Z"]
    assert detect_kind(cols) == "assay"
    assert detect_kind(["BHID", "FROM", "TO", "X", "Y", "Z", "Литология"]) == "interval"
    assert detect_kind(["Скважина", "От", "До", "Au, г/т", "восток", "север", "отметка"]) == "assay"


def test_kinds_by_field_sets() -> None:
    assert detect_kind(["Hole-ID", "X", "Y", "Z", "Length"]) == "collar"  # TD by Length, no From/To
    assert detect_kind(["HoleID", "East", "North", "RL", "Depth"]) == "collar"
    assert detect_kind(["HoleID", "From", "To", "Lith"]) == "interval"
    assert detect_kind(["HoleID", "From", "To", "Sample"]) == "assay"
    assert detect_kind(["HoleID", "Depth", "Event"]) == "event"
    assert detect_kind(["HoleID", "Depth", "Azimuth", "Dip"]) == "survey"
    assert detect_kind(["foo", "bar"]) is None


def test_assay_with_xyz_imports_without_duplicate_holes(tmp_path: Path) -> None:
    text = "Hole-ID,Sample,From,To,Length,Cu_%,Ag_G/T,X,Y,Z\n" + "".join(
        f"H1,S{i},{i},{i + 1},1,0.{i},{i},10,20,30\n" for i in range(5)
    )
    t = read_table(_csv(tmp_path, text, "utf-8"))
    kind = detect_kind(t.names)
    assert kind == "assay"
    m = suggest_mapping(kind, t.names)
    _, issues = normalize(t, kind, m, suggest_components(t, m))
    assert not [i for i in issues if i.code == "duplicate_hole_id"]
