"""Micromine DAT header: field lines without a space before the width (`C255  0`), a tolerant detector and
the reasons given when a `.dat` is refused (Э27.1, Д-40 А1 / Д-46 А7). Files are synthetic (customer files are
not available): `raw_file` writes the lines of the VARIABLES block byte by byte."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from bf_core.formats.errors import FormatError
from bf_core.formats.inspect import inspect_table
from bf_core.formats.micromine import read_dat, write_dat
from bf_core.formats.micromine_header import Field, build_header, parse_header
from bf_core.formats.registry import detect, read_table
from bf_core.formats.table import Column
from tests.formats.dat_builder import MAGIC_LINE, header

CP = "ibm-5347_P100-1998"
RU = "Тип руды".encode("cp1251")


def raw_file(
    lines: list[bytes],
    record: bytes,
    *,
    codepage: str | None = CP,
    standard: bool = False,
    eol: bytes = b"\r\n",
    tag: bytes = b"VARIABLES",
    lead: bytes = b"",
) -> bytes:
    """A DAT whose VARIABLES lines are given as they are in the file; one record per call is `record`."""
    out = bytearray(lead)
    if not standard:
        out += header(codepage)
    out += f"{len(lines):<4}".encode("ascii") + tag + eol
    for line in lines:
        out += line + eol
    out += record + b"\r\n"
    return bytes(out)


def _write(tmp_path: Path, data: bytes, name: str = "t.DAT") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# ------------------------------------------------------------------------------ field lines (А7)
def test_width_glued_to_type_is_read(tmp_path: Path) -> None:
    """The 422 of the customer: `Тип руды  C255  0` (name 10 bytes, `C255` glued)."""
    lines = [RU.ljust(10) + b"C255  0", b"Au".ljust(10) + b"R  8  0"]
    record = b"oxide".ljust(255) + struct.pack("<d", 1.25)
    path = _write(tmp_path, raw_file(lines, record))
    head = parse_header(path.read_bytes())
    assert [(f.name, f.kind, f.width, f.decimals) for f in head.fields] == [
        ("Тип руды", "C", 255, 0),
        ("Au", "R", 8, 0),
    ]
    table = read_dat(path)
    assert table.column("Тип руды").to_pylist() == ["oxide"] and table.column("Au").values.tolist() == [1.25]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (b"RUN_AU_CUT" + b"N 12 8", ("RUN_AU_CUT", "N", 12, 8)),  # the sample of real block models
        (b"Au 5      " + b"C 10  0", ("Au 5", "C", 10, 0)),  # name with a digit and a space
        (b"ABCDEFGH12" + b"C255  0", ("ABCDEFGH12", "C", 255, 0)),  # name ends in digits, width glued
        (b"Hole 1 B  " + b"N  7  2", ("Hole 1 B", "N", 7, 2)),  # a letter inside the name
        (b"X".ljust(10) + b"R  8  0|East", ("X", "R", 8, 0)),  # description after "|"
        (b"X R 8 0", ("X", "R", 8, 0)),  # a name that is not padded to 10: the fallback expression
        (b"AU 5R  8  0", ("AU 5", "R", 8, 0)),  # not padded, a digit before the type letter
        (b"Cu_pct  " + b"C100  3", ("Cu_pct", "C", 100, 3)),  # shorter pad, glued width, 3 digits
    ],
)
def test_ambiguous_field_lines(line: bytes, expected: tuple[str, str, int, int]) -> None:
    head = parse_header(raw_file([line], b""), complete=True)
    f = head.fields[0]
    assert (f.name, f.kind, f.width, f.decimals) == expected


def test_description_survives_a_glued_width() -> None:
    line = b"Grade".ljust(10) + b"C255  0|" + "Рудный тип".encode("cp1251")
    head = parse_header(raw_file([line], b""))
    assert head.fields[0].width == 255 and head.fields[0].description == "Рудный тип"


def test_error_names_the_line_shows_bytes_in_the_codepage_and_hints_encoding() -> None:
    good = b"Au".ljust(10) + b"R  8  0"
    bad = RU.ljust(10) + b"C2x5  0"  # a width that is not a number: the line cannot be read
    with pytest.raises(FormatError) as info:
        parse_header(raw_file([good, bad], b""))
    err = info.value
    assert err.code == "broken"
    assert "Тип руды" in err.message and "Òèï" not in err.message  # decoded with the code page, not latin-1
    assert "2" in err.message and "VARIABLES" in err.message  # the number of the line
    assert RU.hex() in err.message.replace(" ", "")  # the bytes
    assert "кодировк" in err.hint.lower()


def test_error_uses_chosen_encoding_over_the_header() -> None:
    bad = "Тип руды".encode("cp1251").ljust(10) + b"C2x5  0"
    with pytest.raises(FormatError) as info:
        parse_header(raw_file([bad], b"", codepage="utf-8"), encoding="cp1251")
    assert "Тип руды" in info.value.message


def test_build_header_writes_the_micromine_form_and_reads_back() -> None:
    fields = [Field("Тип руды", "C", 255, 0), Field("Au", "R", 8, 0), Field("N1", "N", 12, 8)]
    data = build_header(fields, "cp1251", None)
    assert b"C255  0" in data and b"R  8  0" in data and b"N 12  8" in data
    back = parse_header(data)
    assert [(f.name, f.kind, f.width, f.decimals) for f in back.fields] == [
        (f.name, f.kind, f.width, f.decimals) for f in fields
    ]


def test_round_trip_through_write_dat(tmp_path: Path) -> None:
    columns = [
        Column("Тип руды", "text", pa.chunked_array([pa.array(["окисленная", "сульфидная"])]), source="C"),
        Column("Au", "number", np.array([1.5, np.nan]), source="R"),
    ]
    fields = [Field("Тип руды", "C", 255, 0), Field("Au", "R", 8, 0)]
    path = tmp_path / "out.dat"
    write_dat(path, columns, fields=fields, codepage=CP)
    assert b"C255  0" in path.read_bytes()[:9000]
    back = read_dat(path)
    assert back.info.header.fields == fields
    assert back.column("Тип руды").to_pylist() == ["окисленная", "сульфидная"]
    assert np.isnan(back.column("Au").values[1])


# ------------------------------------------------------------------------------ detector (А1)
@pytest.mark.parametrize(
    "lead", [b"\xef\xbb\xbf", b"  ", b"\x00\x00\x00", b"\r\n\t", b"\xef\xbb\xbf\r\n"], ids=lambda b: b.hex()
)
def test_detect_and_read_with_junk_before_the_header(tmp_path: Path, lead: bytes) -> None:
    lines = [b"BHID".ljust(10) + b"C  6  0", b"X".ljust(10) + b"R  8  0"]
    record = b"A-1".ljust(6) + struct.pack("<d", 5.5)
    path = _write(tmp_path, raw_file(lines, record, lead=lead))
    assert detect(path) == "dat"
    table = read_table(path)
    assert table.column("BHID").to_pylist() == ["A-1"] and table.column("X").values.tolist() == [5.5]
    standard = _write(tmp_path, raw_file(lines, record, standard=True, lead=lead), "std.dat")
    assert detect(standard) == "dat" and read_table(standard).rows == 1


@pytest.mark.parametrize(
    ("tag", "eol", "lead"),
    [
        (b"VARIABLES", b"\n", b""),
        (b"variables", b"\r\n", b""),
        (b"Variables", b"\n", b"   "),
        (b"VARIABLES ", b"\r\n", b""),
    ],
)
def test_standard_header_variants(tmp_path: Path, tag: bytes, eol: bytes, lead: bytes) -> None:
    lines = [b"BHID".ljust(10) + b"C  6  0", b"X".ljust(10) + b"R  8  0"]
    record = b"A-1".ljust(6) + struct.pack("<d", 5.5)
    data = raw_file(lines, record, standard=True, tag=tag, eol=eol, lead=lead)
    path = _write(tmp_path, data)
    assert detect(path) == "dat"
    head = parse_header(data)
    assert [f.name for f in head.fields] == ["BHID", "X"] and head.data_offset == data.index(b"A-1")


def test_extended_header_with_lf_and_lower_case_tag(tmp_path: Path) -> None:
    lines = [b"BHID".ljust(10) + b"C  6  0"]
    data = raw_file(lines, b"A-1".ljust(6), tag=b"variables", eol=b"\n")
    path = _write(tmp_path, data)
    assert detect(path) == "dat"
    assert read_table(path).column("BHID").to_pylist() == ["A-1"]


def test_text_dat_goes_the_csv_way(tmp_path: Path) -> None:
    path = _write(tmp_path, "BHID;X\nС-1;1,5\nС-2;2,5\n".encode("cp1251"), "DH_collars.DAT")
    assert detect(path) == "csv"
    preview = inspect_table(path)
    assert preview.kind == "csv" and preview.rows[1][0] == "С-1"


def test_header_with_four_digit_count_and_no_space(tmp_path: Path) -> None:
    lines = [f"F{i:<9}".encode() + b"C  1  0" for i in range(1000)]
    data = raw_file(lines, b"x" * 1000)
    assert len(parse_header(data).fields) == 1000 and detect(_write(tmp_path, data)) == "dat"


# ------------------------------------------------------------------------------ reasons of a refusal
def test_refusal_names_the_first_bytes_and_what_was_not_found(tmp_path: Path) -> None:
    junk = b"\x01\x02MM\x00\x10" + "Скважина".encode("cp1251") + b"\x00" * 200
    path = _write(tmp_path, junk, "DH_collars.DAT")
    assert detect(path) == "unknown"
    for call in (lambda: inspect_table(path), lambda: read_table(path)):
        with pytest.raises(FormatError) as info:
            call()
        err = info.value
        assert err.code == "unsupported"
        assert junk[:8].hex(" ") in err.message  # the first bytes in hex
        assert "THIS IS MICROMINE" in err.message and "VARIABLES" in err.message  # what was looked for
        assert "8 КБ" in err.hint and "CSV" in err.hint


def test_refusal_of_another_extension_stays_short_but_concrete(tmp_path: Path) -> None:
    path = _write(tmp_path, b"MZ\x90\x00" + b"\x00" * 100, "tool.exe")
    with pytest.raises(FormatError) as info:
        inspect_table(path)
    message = info.value.message
    assert info.value.code == "unsupported" and ".exe" in message and "4d5a9000" in message.replace(" ", "")


def test_variant_of_the_magic_line_is_a_dat(tmp_path: Path) -> None:
    data = bytearray(raw_file([b"X".ljust(10) + b"R  8  0"], struct.pack("<d", 2.0)))
    data[: len(MAGIC_LINE)] = MAGIC_LINE.replace(b"EXTENDED DATA", b"extended data")
    path = _write(tmp_path, bytes(data))
    assert detect(path) == "dat"
    assert read_dat(path).column("X").values.tolist() == [2.0]
