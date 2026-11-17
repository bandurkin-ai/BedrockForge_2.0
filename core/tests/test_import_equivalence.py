"""ТЗ 7.7 «Эквивалентность»: the same data in CSV (cp1251, «;», decimal comma), Excel and DAT give the same set."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from bf_core.formats.registry import read_table
from bf_core.importing.service import normalize, suggest_components, suggest_mapping

ROWS = [
    ("DH-1", 0.0, 1.5, 0.005, 1.25, "гранит"),
    ("DH-1", 1.5, 3.0, 4584.7, None, "диорит"),
    ("DH-2", 0.0, 2.25, 0.0, 0.01, None),
]
HEADER = ["HOLE_ID", "FROM", "TO", "AU_PPM", "CU_PCT", "LITH"]


def _csv(path: Path) -> None:
    def num(v: float | None) -> str:
        return "" if v is None else repr(v).replace(".", ",")

    lines = [";".join(HEADER)] + [
        ";".join([r[0], num(r[1]), num(r[2]), num(r[3]), num(r[4]), r[5] or ""]) for r in ROWS
    ]
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp1251"))


def _xlsx(path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADER)
    for r in ROWS:
        ws.append(list(r))
    wb.save(path)


def _dat(path: Path, csv_path: Path) -> None:
    from bf_core.formats.micromine import write_dat

    write_dat(path, read_table(csv_path).columns)


def _normalized(path: Path):  # type: ignore[no-untyped-def]
    t = read_table(path)
    m = suggest_mapping("assay", t.names)
    comps = suggest_components(t, m)
    res, issues = normalize(t, "assay", m, comps, ["LITH"], table_name="Пробы")
    assert res is not None and res.table is not None, issues
    tab = res.table
    cols = {c.name: (c.kind, c.unit, c.role) for c in tab.columns}
    vals = {
        c.name: [None if (isinstance(v, float) and math.isnan(v)) else v for v in tab.values[i]]
        for i, c in enumerate(tab.columns)
    }
    return tab.holes, list(tab.from_m), list(tab.to_m), cols, vals


def test_csv_excel_dat_give_the_same_table(tmp_path: Path) -> None:
    csv, xlsx, dat = tmp_path / "a.csv", tmp_path / "a.xlsx", tmp_path / "a.dat"
    _csv(csv)
    _xlsx(xlsx)
    _dat(dat, csv)
    ref = _normalized(csv)
    assert ref[0] == ["DH-1", "DH-1", "DH-2"] and ref[1] == [0.0, 1.5, 0.0] and ref[2] == [1.5, 3.0, 2.25]
    assert ref[3]["AU_PPM"] == ("number", "ppm", "component") and ref[3]["CU_PCT"] == ("number", "%", "component")
    assert ref[4]["AU_PPM"] == [0.005, 4584.7, 0.0] and ref[4]["CU_PCT"] == [1.25, None, 0.01]
    for other in (xlsx, dat):
        got = _normalized(other)
        assert got == ref, other.suffix


@pytest.mark.parametrize("bad", ["", "\x00\x01garbage"])
def test_broken_files_are_refused_with_a_message(tmp_path: Path, bad: str) -> None:
    from bf_core.formats.errors import FormatError

    p = tmp_path / "x.csv"
    p.write_text(bad, encoding="latin-1")
    with pytest.raises(FormatError) as err:
        read_table(p)
    assert err.value.message and err.value.code
