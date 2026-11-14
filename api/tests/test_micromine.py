"""Tests for the native Micromine DAT reader.

Synthetic files are built in-memory, so the suite runs without project data.
Set MICROMINE_SAMPLE_DIR to a folder with the real Collar/Survey/Assay files
to run the integration checks against them.
"""

import math
import os
import struct
from pathlib import Path

import pytest

from app.formats.micromine import MicromineFormatError, read_micromine

HEADER_XML = (
    '<?xml version="1.0" encoding="utf-8"?><metadata version="1.0">'
    '<meta-object type="encoding" version="1.0" compulsory="true">'
    '<data codepage="{codepage}"/></meta-object></metadata>'
)


def _header(codepage: str) -> bytes:
    magic = b"THIS IS MICROMINE EXTENDED DATA FILE!\x07\x1a\x01\r\n"
    xml = HEADER_XML.format(codepage=codepage).encode("utf-8")
    return (magic + xml).ljust(4096, b"\x00")


def _encode_bocu(text: str) -> bytes:
    """Encode text the way BOCU-1-declared Micromine files store it (ASCII + 0x50)."""
    return bytes(ord(c) + 0x50 if ord(c) >= 0x50 else ord(c) for c in text)


def _build(codepage: str, variables: list[tuple[bytes, str, int, int]], records: list[list]) -> bytes:
    """variables: (raw_name, kind, width, decimals); records: values per field."""
    out = bytearray(_header(codepage))
    out += f"{len(variables)} VARIABLES\r\n".encode("ascii")
    for raw_name, kind, width, dec in variables:
        out += raw_name + f" {kind} {width:>2} {dec:>2}".encode("ascii") + b"\r\n"
    for rec in records:
        for value, (_, kind, width, _) in zip(rec, variables):
            if kind == "C":
                encoded = value if isinstance(value, bytes) else value.encode("ascii")
                out += encoded.ljust(width, b" ")[:width]
            else:
                out += struct.pack("<d", value)
        out += b"\r\n"
    return bytes(out)


@pytest.fixture
def ascii_file(tmp_path: Path) -> Path:
    data = _build(
        "ibm-5347_P100-1998",
        [
            (b"BHID", "C", 15, 0),
            (b"X", "R", 8, 3),
            (b"DIP", "R", 8, 2),
        ],
        [
            ["BHTSE-1", 466570.586, -90.0],
            ["BHTSE-2", math.nan, -60.0],
        ],
    )
    path = tmp_path / "collar.DAT"
    path.write_bytes(data)
    return path


def test_reads_fields_and_types(ascii_file: Path):
    table = read_micromine(ascii_file)

    assert table.codepage == "ibm-5347_P100-1998"
    assert [(f.name, f.kind, f.width) for f in table.fields] == [
        ("BHID", "C", 15),
        ("X", "R", 8),
        ("DIP", "R", 8),
    ]
    assert list(table.frame.columns) == ["BHID", "X", "DIP"]
    assert table.record_count == 2


def test_text_is_stripped_and_reals_are_little_endian(ascii_file: Path):
    frame = read_micromine(ascii_file).frame

    assert frame.loc[0, "BHID"] == "BHTSE-1"
    assert frame.loc[0, "X"] == pytest.approx(466570.586)
    assert frame.loc[0, "DIP"] == -90.0


def test_missing_real_value_stays_nan(ascii_file: Path):
    frame = read_micromine(ascii_file).frame

    assert math.isnan(frame.loc[1, "X"])


def test_bocu1_names_and_text_are_decoded(tmp_path: Path):
    data = _build(
        "BOCU-1",
        [
            (_encode_bocu("BHID"), "C", 15, 0),
            (_encode_bocu("DIP"), "R", 8, 2),
        ],
        [[_encode_bocu("BHTSE-2-17"), -59.65]],
    )
    path = tmp_path / "survey.DAT"
    path.write_bytes(data)

    frame = read_micromine(path).frame

    assert list(frame.columns) == ["BHID", "DIP"]
    assert frame.loc[0, "BHID"] == "BHTSE-2-17"


def test_duplicate_field_names_are_made_unique(tmp_path: Path):
    data = _build(
        "ibm-5347_P100-1998",
        [(b"X", "R", 8, 3), (b"X", "R", 8, 3)],
        [[1.0, 2.0]],
    )
    path = tmp_path / "dup.DAT"
    path.write_bytes(data)

    frame = read_micromine(path).frame

    assert list(frame.columns) == ["X", "X_2"]


def test_rejects_non_micromine_file(tmp_path: Path):
    path = tmp_path / "plain.DAT"
    path.write_bytes(b"BHID,X\nA,1\n")

    with pytest.raises(MicromineFormatError, match="not a Micromine"):
        read_micromine(path)


def test_rejects_truncated_record(tmp_path: Path):
    data = _build("ibm-5347_P100-1998", [(b"X", "R", 8, 3)], [[1.0], [2.0]])
    path = tmp_path / "broken.DAT"
    path.write_bytes(data[:-5])

    with pytest.raises(MicromineFormatError, match="multiple of record length"):
        read_micromine(path)


def test_rejects_unknown_field_type(tmp_path: Path):
    data = _build("ibm-5347_P100-1998", [(b"X", "Q", 8, 3)], [])
    path = tmp_path / "badtype.DAT"
    path.write_bytes(data)

    with pytest.raises(MicromineFormatError, match="unknown field type"):
        read_micromine(path)


def _find_sample(token: str) -> Path:
    sample_dir = Path(os.environ["MICROMINE_SAMPLE_DIR"])
    matches = [p for p in sample_dir.iterdir() if token in p.name and p.suffix.upper() == ".DAT"]
    assert len(matches) == 1, f"expected one *{token}*.DAT, found {matches}"
    return matches[0]


@pytest.mark.skipif(
    not os.environ.get("MICROMINE_SAMPLE_DIR"),
    reason="set MICROMINE_SAMPLE_DIR to run against the real project files",
)
class TestRealSampleFiles:
    """Expected values come from the project files reviewed on 2026-11-14."""

    def test_collar(self):
        table = read_micromine(_find_sample("Collar"))
        assert table.record_count == 101
        assert table.frame["BHID"].is_unique

    def test_survey_names_decoded_and_match_collars(self):
        collars = read_micromine(_find_sample("Collar")).frame
        survey = read_micromine(_find_sample("Survey")).frame

        assert survey.shape[0] == 911
        assert set(survey["BHID"]) <= set(collars["BHID"])

    def test_assay(self):
        table = read_micromine(_find_sample("Assay"))
        assert table.record_count == 22663
        assert {"BHID", "FROM", "TO", "AU", "AG", "CU", "PB", "ZN"} <= set(table.frame.columns)
