"""Micromine DAT: native reader (memory map, all field types, code pages, BOCU-1) and negative cases.

Synthetic files are built in memory by `dat_builder`; the real STR files of `tests/data/real` are the
same format and are read as tables. Set MICROMINE_SAMPLE_DIR to a folder with the real Collar/Survey/Assay
files to run the checks against them (as in BF).
"""

from __future__ import annotations

import math
import os
import struct
from pathlib import Path

import numpy as np
import pytest

from bf_core.formats.errors import FormatError
from bf_core.formats.micromine import read_dat, read_header
from tests.formats.dat_builder import MAGIC_LINE, build, encode_bocu

REAL = Path(__file__).resolve().parents[3] / "tests" / "data" / "real"
CP = "ibm-5347_P100-1998"


def _write(tmp_path: Path, data: bytes, name: str = "t.DAT") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


@pytest.fixture
def collar(tmp_path: Path) -> Path:
    data = build(
        CP,
        [(b"BHID", "C", 15, 0), (b"X", "R", 8, 3), (b"DIP", "R", 8, 2)],
        [["BHTSE-1", 466570.586, -90.0], ["BHTSE-2", math.nan, -60.0]],
    )
    return _write(tmp_path, data, "collar.DAT")


def test_reads_fields_types_and_values(collar: Path) -> None:
    table = read_dat(collar)
    header = table.info.header
    assert table.info.format == "dat" and table.info.encoding == "cp1251" and header.codepage == CP
    assert header.extended and header.data_offset == 4096 + 15 + 3 * 19
    assert [(f.name, f.kind, f.width, f.decimals) for f in header.fields] == [
        ("BHID", "C", 15, 0),
        ("X", "R", 8, 3),
        ("DIP", "R", 8, 2),
    ]
    assert table.names == ["BHID", "X", "DIP"] and table.rows == 2
    assert [c.kind for c in table.columns] == ["text", "number", "number"]
    assert table.column("BHID").to_pylist() == ["BHTSE-1", "BHTSE-2"]
    x = table.column("X").values
    assert x.dtype == np.float64 and x[0] == 466570.586  # little-endian double, bit for bit
    assert math.isnan(x[1])  # a missing value stays NaN, never 0
    assert table.column("DIP").values.tolist() == [-90.0, -60.0]
    assert table.warnings == []


def test_every_field_type_cyrillic_names_descriptions_and_glued_type(tmp_path: Path) -> None:
    variables = [
        ("Скважина".encode("cp1251"), "C", 12, 0, "Номер скважины".encode("cp1251")),
        (b"RUN_AU_CUT", "N", 12, 8),  # ten characters: the type follows without a space
        (b"AU", "R", 8, 3, "Золото, г/т".encode("cp1251")),
        (b"F4", "F", 4, 2),
        (b"L4", "L", 4, 0),
        (b"S2", "S", 2, 0),
        (b"B1", "B", 1, 0),
        (b"JOIN", "N", 6, 0),
    ]
    records = [
        ["С-1".encode("cp1251"), "0.12345678", 1.5, 0.25, -7, -3, 200, "1"],
        ["", "", math.nan, 1e10, 2**31 - 1, -(2**15), 0, "  42  "],
        ["Руда ёЁ".encode("cp1251"), "-1e-3", -0.0, -1.5, 0, 0, 255, "x"],
    ]
    path = _write(tmp_path, build(CP, variables, records))
    assert b"RUN_AU_CUTN 12  8\r\n" in path.read_bytes()

    table = read_dat(path)
    assert table.names == ["Скважина", "RUN_AU_CUT", "AU", "F4", "L4", "S2", "B1", "JOIN"]
    fields = table.info.header.fields
    assert fields[0].description == "Номер скважины" and fields[2].description == "Золото, г/т"
    assert fields[1].description == "" and (fields[1].kind, fields[1].width, fields[1].decimals) == ("N", 12, 8)
    assert table.column("Скважина").to_pylist() == ["С-1", None, "Руда ёЁ"]
    run = table.column("RUN_AU_CUT").values
    assert run[0] == 0.12345678 and math.isnan(run[1]) and run[2] == -0.001
    assert table.column("F4").values.tolist() == [0.25, float(np.float32(1e10)), -1.5]
    assert table.column("L4").values.tolist() == [-7.0, 2147483647.0, 0.0]
    assert table.column("S2").values.tolist() == [-3.0, -32768.0, 0.0]
    assert table.column("B1").values.tolist() == [200.0, 0.0, 255.0]
    assert math.copysign(1.0, table.column("AU").values[2]) == -1.0  # -0.0 kept
    join = table.column("JOIN").values
    assert join[:2].tolist() == [1.0, 42.0] and math.isnan(join[2])
    # A number field with text in it: the value is empty and the log says so.
    assert any("JOIN" in w and "«x»" in w and "1" in w for w in table.warnings), table.warnings


def test_bocu1_names_text_and_numbers(tmp_path: Path) -> None:
    from bf_core.formats.bocu1 import encode

    data = build(
        "BOCU-1",
        [
            (encode_bocu("BHID"), "C", 15, 0),
            (encode_bocu("DIP"), "R", 8, 2),
            (encode_bocu("JOIN"), "N", 12, 0),
            (encode("Порода"), "C", 20, 0),
        ],
        [[encode_bocu("BHTSE-2-17"), -59.65, encode_bocu("  466170.000"), encode("Руда окисл. ёЁ")]],
    )
    table = read_dat(_write(tmp_path, data, "survey.DAT"))
    assert table.info.encoding == "bocu-1"
    assert table.names == ["BHID", "DIP", "JOIN", "Порода"]
    assert table.column("BHID").to_pylist() == ["BHTSE-2-17"]
    assert table.column("JOIN").values.tolist() == [466170.0]
    assert table.column("Порода").to_pylist() == ["Руда окисл. ёЁ"]


def test_bocu1_is_recognised_without_a_code_page(tmp_path: Path) -> None:
    data = build(
        None, [(encode_bocu("BHID"), "C", 8, 0), (encode_bocu("X_NEW"), "R", 8, 2)], [[encode_bocu("A-1"), 1.0]]
    )
    table = read_dat(_write(tmp_path, data))
    assert table.info.encoding == "bocu-1" and table.names == ["BHID", "X_NEW"]
    assert table.column("BHID").to_pylist() == ["A-1"]


def test_header_longer_than_one_page(tmp_path: Path) -> None:
    data = build(
        CP, [(b"X", "R", 8, 3), ("Тип".encode("cp1251"), "C", 4, 0)], [[1.0, "руда".encode("cp1251")]], pages=2
    )
    assert data[4096:4100] != b"2   "  # the VARIABLES block starts at 8192
    table = read_dat(_write(tmp_path, data))
    assert table.info.header.data_offset > 8192 and table.info.encoding == "cp1251"
    assert table.names == ["X", "Тип"] and table.column("Тип").to_pylist() == ["руда"]


def test_standard_header_without_xml_pages(tmp_path: Path) -> None:
    data = build(None, [(b"HOLE", "C", 6, 0), (b"DEPTH", "R", 8, 2)], [["A1", 10.5], ["A2", 20.0]], standard=True)
    assert data.startswith(b"2   VARIABLES\r\n")
    table = read_dat(_write(tmp_path, data))
    assert not table.info.header.extended and table.info.header.codepage == ""
    assert table.column("HOLE").to_pylist() == ["A1", "A2"] and table.column("DEPTH").values.tolist() == [10.5, 20.0]
    assert table.warnings == []


def test_duplicate_field_names_are_made_unique(tmp_path: Path) -> None:
    table = read_dat(_write(tmp_path, build(CP, [(b"X", "R", 8, 3), (b"X", "R", 8, 3)], [[1.0, 2.0]])))
    assert table.names == ["X", "X_2"]
    assert [f.name for f in table.info.header.fields] == ["X", "X"]


def test_empty_table_and_title(tmp_path: Path) -> None:
    table = read_dat(_write(tmp_path, build(CP, [(b"X", "R", 8, 3), (b"T", "C", 3, 0)], [], title="Устья &amp; пробы")))
    assert table.rows == 0 and table.column("X").values.shape == (0,) and table.column("T").to_pylist() == []
    assert table.info.header.title == "Устья & пробы"


def test_real_str_files_are_micromine_tables() -> None:
    table = read_dat(REAL / "Без имени.STR")
    assert table.names == ["EAST", "NORTH", "RL", "JOIN", "STRING"] and table.rows == 12
    assert [f.kind for f in table.info.header.fields] == ["R", "R", "R", "N", "C"]
    assert table.column("EAST").values[0] == struct.unpack("<d", bytes.fromhex("8db10a41317a1c41"))[0]
    assert table.column("JOIN").values[:5].tolist() == [1.0, 1.0, 2.0, 2.0, 3.0]
    assert table.column("STRING").to_pylist() == [None] * 12 and table.warnings == []
    named = read_dat(REAL / "Восточный Цители-Сопели.STR")
    assert named.rows == 37 and sorted(set(named.column("STRING").to_pylist())) == [f"TS-{i}" for i in range(1, 6)]
    assert named.column("RL").values[0] == 1000.0


# ------------------------------------------------------------------------------- warnings, not silent loss
def test_unknown_code_page_is_reported_and_read_as_cp1251(tmp_path: Path) -> None:
    data = build("ibm-99999_X", [("Имя".encode("cp1251"), "C", 6, 0)], [["Руда".encode("cp1251")]])
    table = read_dat(_write(tmp_path, data))
    assert table.names == ["Имя"] and table.column("Имя").to_pylist() == ["Руда"]
    assert any("ibm-99999_X" in w and "Windows-1251" in w for w in table.warnings)
    forced = read_dat(_write(tmp_path, data), encoding="cp866")
    assert forced.info.encoding == "cp866" and forced.names != ["Имя"]


def test_unknown_field_type_is_skipped_with_a_warning(tmp_path: Path) -> None:
    data = build(CP, [(b"A", "R", 8, 3), (b"Q", "Q", 5, 0), (b"T", "C", 2, 0)], [[1.5, "?????", "ab"]])
    table = read_dat(_write(tmp_path, data))
    assert table.names == ["A", "T"] and table.column("A").values.tolist() == [1.5]
    assert table.column("T").to_pylist() == ["ab"]
    assert any("«Q»" in w and "пропущено" in w for w in table.warnings)
    assert [f.kind for f in table.info.header.fields] == ["R", "Q", "C"]


def test_unknown_header_version_is_read_with_a_warning(tmp_path: Path) -> None:
    data = build(CP, [(b"X", "R", 8, 3)], [[2.5]], version="7.3")
    table = read_dat(_write(tmp_path, data))
    assert table.column("X").values.tolist() == [2.5]
    assert any("7.3" in w for w in table.warnings)
    odd = bytearray(build(CP, [(b"X", "R", 8, 3)], [[2.5]]))
    odd[39] = 0x09  # another byte in the magic line
    table = read_dat(_write(tmp_path, bytes(odd)))
    assert table.column("X").values.tolist() == [2.5] and any("заголов" in w.lower() for w in table.warnings)


def test_broken_text_is_counted(tmp_path: Path) -> None:
    data = build("utf-8", [(b"T", "C", 4, 0)], [[b"ok"], [b"\xff\xfe!"], [b"\xff\xfe!"]])
    table = read_dat(_write(tmp_path, data))
    assert table.column("T").to_pylist()[0] == "ok" and "\ufffd" in table.column("T").to_pylist()[1]
    assert any("«T»" in w and "2" in w for w in table.warnings)


# --------------------------------------------------------------------------------------------- negative
def _error(path: Path) -> FormatError:
    with pytest.raises(FormatError) as info:
        read_dat(path)
    assert info.value.message and info.value.hint
    return info.value


def test_rejects_empty_and_foreign_files(tmp_path: Path) -> None:
    assert _error(_write(tmp_path, b"")).code == "empty_file"
    assert _error(_write(tmp_path, b"BHID,X\nA,1\n")).code == "not_micromine"
    assert _error(_write(tmp_path, b"\x00" * 5000)).code == "not_micromine"


def test_rejects_truncated_files(tmp_path: Path) -> None:
    data = build(CP, [(b"X", "R", 8, 3), (b"T", "C", 4, 0)], [[1.0, "a"], [2.0, "b"]])
    assert _error(_write(tmp_path, data[:-5])).code == "truncated"  # inside the last record
    assert _error(_write(tmp_path, data[:2000])).code == "truncated"  # inside the XML page
    assert _error(_write(tmp_path, data[: 4096 + 20])).code == "truncated"  # inside the VARIABLES block
    assert _error(_write(tmp_path, MAGIC_LINE)).code == "truncated"
    assert read_dat(_write(tmp_path, data[:-14])).rows == 1  # cut at a record boundary: nothing to notice


def test_rejects_bombs(tmp_path: Path) -> None:
    head = build(CP, [(b"X", "R", 8, 3)], [])
    many = head.replace(b"1   VARIABLES", b"999999999 VARIABLES")
    assert _error(_write(tmp_path, many)).code == "too_large"
    wide = build(CP, [(b"T", "C", 99, 0)], []).replace(b"C 99  0", b"C 999999999  0")
    assert _error(_write(tmp_path, wide)).code == "too_large"
    zero = build(CP, [(b"T", "C", 99, 0)], []).replace(b"C 99  0", b"C  0  0")
    assert _error(_write(tmp_path, zero)).code == "broken"
    bad_width = build(CP, [(b"X", "R", 4, 0)], [])
    assert _error(_write(tmp_path, bad_width)).code == "broken"
    garbage = head[:4096] + b"7   VARIABLES\r\n" + os.urandom(3000)
    assert _error(_write(tmp_path, garbage)).code in ("broken", "truncated")


def test_records_without_crlf_are_rejected_or_reported(tmp_path: Path) -> None:
    good = build(CP, [(b"X", "R", 8, 3)], [[1.0], [2.0], [3.0]])
    first = bytearray(good)
    first[-30 + 8 : -30 + 10] = b"zz"
    assert _error(_write(tmp_path, bytes(first))).code == "broken"
    later = bytearray(good)
    later[-2:] = b"zz"
    table = read_dat(_write(tmp_path, bytes(later)))
    assert table.rows == 3 and any("CRLF" in w for w in table.warnings)


def test_progress_is_reported_and_cancels(tmp_path: Path) -> None:
    rows = 50_000
    body = np.zeros(rows, dtype=np.dtype([("x", "<f8"), ("t", "S6"), ("eol", "S2")]))
    body["x"] = np.arange(rows)
    body["t"] = b"AB    "
    body["eol"] = b"\r\n"
    path = _write(tmp_path, build(CP, [(b"X", "R", 8, 3), (b"T", "C", 6, 0)], []) + body.tobytes())
    seen: list[float] = []
    table = read_dat(path, progress=lambda f, m: seen.append(f), chunk_rows=8192)
    assert table.rows == rows and table.column("X").values[-1] == rows - 1
    assert len(seen) >= 7 and seen == sorted(seen) and 0.0 <= seen[0] and seen[-1] == 1.0

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None) -> None:
        if fraction > 0.3:
            raise Cancelled

    with pytest.raises(Cancelled):
        read_dat(path, progress=cancel, chunk_rows=8192)
    path.unlink()  # the memory map is closed: the file can be deleted (matters on Windows)


def test_read_header_alone(collar: Path) -> None:
    header = read_header(collar)
    assert header.record_size == 15 + 8 + 8 + 2 and header.names == ["BHID", "X", "DIP"]
    assert header.xml.startswith("<?xml") and header.xml.endswith("</metadata>")


@pytest.mark.skipif(
    not os.environ.get("MICROMINE_SAMPLE_DIR"), reason="set MICROMINE_SAMPLE_DIR for the real project files"
)
class TestRealSampleFiles:
    """Expected values come from the project files reviewed on 2026-11-14 (BF)."""

    @staticmethod
    def _find(token: str) -> Path:
        folder = Path(os.environ["MICROMINE_SAMPLE_DIR"])
        matches = [p for p in folder.iterdir() if token in p.name and p.suffix.upper() == ".DAT"]
        assert len(matches) == 1, f"expected one *{token}*.DAT, found {matches}"
        return matches[0]

    def test_collar_survey_assay(self) -> None:
        collar = read_dat(self._find("Collar"))
        survey = read_dat(self._find("Survey"))
        assay = read_dat(self._find("Assay"))
        holes = collar.column("BHID").to_pylist()
        assert collar.rows == 101 and len(set(holes)) == 101
        assert survey.rows == 911 and set(survey.column("BHID").to_pylist()) <= set(holes)
        assert assay.rows == 22663 and {"BHID", "FROM", "TO", "AU", "AG", "CU", "PB", "ZN"} <= set(assay.names)
