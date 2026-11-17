"""Number parsing: decimal comma, digit groups, detection limits, empty marks; kernel = reference."""

from __future__ import annotations

import math
import struct

import numpy as np
import pytest

from bf_core.formats import numbers
from bf_core.formats.numbers import BAD, BELOW, EMPTY, IDLIKE, MARKER, NUMBER, classify, parse_numbers, parse_value


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12.5", (12.5, "number")),
        ("12,5", (12.5, "number")),
        ("-0,003", (-0.003, "number")),
        ("4 584 699,70", (4584699.7, "number")),
        ("4 584 699.7", (4584699.7, "number")),
        ("4\u00a0584\u202f699,70", (4584699.7, "number")),
        ("1e-3", (0.001, "number")),
        ("  7.25  ", (7.25, "number")),
        ("12.", (12.0, "number")),
        (",5", (0.5, "number")),
        ("<0.005", (0.0025, "below")),
        ("< 0,01", (0.005, "below")),
        (">1000", (1000.0, "above")),
        ("> 1 000", (1000.0, "above")),
        ("н/д", (None, "missing")),
        ("Н.О.", (None, "missing")),
        ("NA", (None, "missing")),
        ("N/A", (None, "missing")),
        ("#N/A", (None, "missing")),
        ("", (None, "missing")),
        ("   ", (None, "missing")),
        ("-", (None, "missing")),
        ("—", (None, "missing")),
        (None, (None, "missing")),
        (float("nan"), (None, "missing")),
        (7, (7.0, "number")),
        (np.int64(7), (7.0, "number")),
        (np.float32(0.5), (0.5, "number")),
        (True, (None, "bad")),
        ("abc", (None, "bad")),
        ("12.5.3", (None, "bad")),
        ("1 2345", (None, "bad")),
        ("1 234e5", (None, "bad")),
        ("<", (None, "bad")),
        ("<<5", (None, "bad")),
        ("−5", (None, "bad")),  # U+2212 is not a minus sign for the reader (as in BF)
        ("1e999", (None, "bad")),  # overflow is not a number
        ("inf", (None, "bad")),
    ],
)
def test_parse_value(raw: object, expected: tuple[float | None, str]) -> None:
    value, kind = parse_value(raw)
    assert kind == expected[1]
    if expected[0] is None:
        assert value is None
    else:
        assert value == expected[0]  # exact: the same double as float() of the cleaned text


def test_missing_is_nan_never_zero() -> None:
    parsed = parse_numbers(["1", "<0.2", "x", None, "н.о.", "-", "0"])
    assert (parsed.parsed, parsed.bad, parsed.below_limit, parsed.above_limit) == (3, 1, 1, 0)
    assert parsed.bad_examples == ["x"]
    assert parsed.values.dtype == np.float64
    assert parsed.values[1] == 0.1 and parsed.values[6] == 0.0
    assert [math.isnan(v) for v in parsed.values] == [False, False, True, True, True, True, False]


def test_parse_numbers_takes_arrays_and_arrow() -> None:
    import pyarrow as pa

    assert parse_numbers(np.array([1.5, np.nan, 3.0])).parsed == 2
    assert parse_numbers(np.array([1, 2, 3], dtype=np.int32)).values.tolist() == [1.0, 2.0, 3.0]
    chunked = pa.chunked_array([pa.array(["1,5", None]), pa.array([">7", "нет", "?"])])
    parsed = parse_numbers(chunked)
    assert parsed.values[0] == 1.5 and parsed.values[2] == 7.0
    assert (parsed.parsed, parsed.bad, parsed.above_limit, parsed.bad_examples) == (2, 1, 1, ["?"])


def test_identifier_like_numbers_are_flagged() -> None:
    cells = ["007", "0", "12345678901234567890", "1234567890123456", "0.50", "00", "-01", "1e3", "2E10", "1e-3", "1.e3"]
    values, kinds = classify(cells)
    assert kinds.tolist() == [IDLIKE, NUMBER, IDLIKE, IDLIKE, NUMBER, IDLIKE, IDLIKE, IDLIKE, IDLIKE, NUMBER, NUMBER]
    expected = [7.0, 0.0, 1.2345678901234567e19, 1234567890123456.0, 0.5, 0.0, -1.0, 1000.0, 2e10, 0.001, 1000.0]
    assert values.tolist() == expected
    assert parse_value("1e3") == (1000.0, "number")
    assert parse_numbers(["007", "12"]).parsed == 2  # still numbers when a numeric field is asked for


def test_number_text_keeps_whole_numbers_whole() -> None:
    assert numbers.number_text(12.0) == "12"
    assert numbers.number_text(-3.0) == "-3"
    assert numbers.number_text(0.1) == "0.1"
    assert numbers.number_text(1e22) == "1e+22"
    assert numbers.number_text(float("nan")) == ""


HARD = [
    "",
    " ",
    "\t",
    "0",
    "-0",
    "+0",
    "0.0",
    "-0.0",
    "1",
    "-1",
    "+1",
    "1.",
    ".1",
    "-.1",
    "+,1",
    ".",
    ",",
    "+",
    "-",
    "1e5",
    "1E5",
    "1e+5",
    "1e-5",
    "1e",
    "1e+",
    "e5",
    "1.5e3",
    "1,5e-3",
    ".5e1",
    "5.e1",
    "1e05",
    "1e0005",
    "1e22",
    "1e23",
    "1e-22",
    "1e-23",
    "123456789012345",
    "1234567890123456",
    "9007199254740992",
    "9007199254740993",
    "12345678901234567890",
    "123456789012345678901234567890",
    "0.1",
    "0.10",
    "0.1000000000000000000000",
    "0.30000000000000004",
    "2.2250738585072014e-308",
    "4.9e-324",
    "1.7976931348623157e308",
    "1e309",
    "-1e309",
    "0.000000000000000000000000000001",
    "123456789.123456789",
    "1.0000000000000002",
    "8.5",
    "1 000",
    "1 000 000",
    "1 000,5",
    "1 000.25",
    "12 345",
    "123 456",
    "1234 567",
    "1 23",
    "1 2345",
    "1  000",
    "1 000 ",
    " 1 000",
    "1\u00a0000",
    "1\u202f000,5",
    "1\u2009000",
    "1 000 000 000 000 000 000",
    "1 000e3",
    "1 000.",
    "-1 000",
    "+1 000,0",
    "<5",
    "< 5",
    "<  5",
    "<\t5",
    "<5 ",
    "<-5",
    "<+5",
    "<.5",
    "<5.",
    "<1 000",
    "<1e-3",
    "<0",
    "<",
    "< ",
    "<<5",
    "<>5",
    ">5",
    "> 1 000,5",
    ">abc",
    "<abc",
    "<0,005",
    "<007",
    "<\u20095",
    "<\n5",
    "<5\n6",
    "007",
    "00",
    "000",
    "0001.5",
    "-007",
    "+007",
    "0e0",
    "0e999",
    "000e5",
    "na",
    "NA",
    "Na",
    "n/a",
    "N/A",
    "#N/A",
    "nan",
    "NaN",
    "null",
    "NULL",
    "none",
    "None",
    "nd",
    "n.d.",
    "N.D.",
    "н/д",
    "Н/Д",
    "нд",
    "НД",
    "н.д.",
    "н.о.",
    "Н.О.",
    "но",
    "НО",
    "нет",
    "Нет",
    "НЕТ",
    "-",
    "—",
    "–",
    " - ",
    "--",
    "abc",
    "12abc",
    "abc12",
    "1.2.3",
    "1,2,3",
    "1,234,567",
    "1.234.567",
    "12-34",
    "1+1",
    "1e5e5",
    "0x10",
    "1_000",
    "inf",
    "-inf",
    "Infinity",
    "С-1",
    "Скв-12",
    "ГЩ-12",
    "12 м",
    "5%",
    "№5",
    "5°",
    "²",
    "١٢٣",
    "１２",
    "1\x002",
    "\u00a05\u00a0",
    "\u30005",
    "5\u2028",
    "\x1c5",
    "\x855",
    "x" * 70,
    "1" * 70,
    "0." + "0" * 60 + "1",
    " " * 70,
]


def _bits(x: float) -> bytes:
    return struct.pack("<d", x)


def test_kernel_matches_reference_on_hard_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    cells = HARD * 2
    ref_values, ref_kinds = numbers.classify_reference(cells)
    monkeypatch.setattr(numbers, "KERNEL_MIN", 1)
    values, kinds = classify(cells)
    assert kinds.tolist() == ref_kinds.tolist()
    for cell, a, b in zip(cells, values, ref_values, strict=True):
        assert _bits(a) == _bits(b) or (math.isnan(a) and math.isnan(b)), cell


def test_kernel_matches_reference_on_random_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(20261117)
    alphabet = list("0123456789" * 4 + "....,,,   ++--eE<>") + ["\u00a0", "\u202f", "a", "н", "/", "#", "\t", "д"]
    cells = []
    for _ in range(60_000):
        n = int(rng.integers(0, 14))
        cells.append("".join(alphabet[int(i)] for i in rng.integers(0, len(alphabet), n)))
    for _ in range(40_000):  # well-formed numbers in every notation
        x = float(rng.normal()) * 10.0 ** int(rng.integers(-12, 13))
        text = repr(x) if rng.random() < 0.5 else f"{x:.{int(rng.integers(0, 9))}f}"
        if rng.random() < 0.3:
            text = text.replace(".", ",")
        if rng.random() < 0.1:
            text = "<" + text
        cells.append(text)
    ref_values, ref_kinds = numbers.classify_reference(cells)
    monkeypatch.setattr(numbers, "KERNEL_MIN", 1)
    values, kinds = classify(cells)
    assert np.array_equal(kinds, ref_kinds)
    assert np.array_equal(values.view(np.uint64), ref_values.view(np.uint64))  # bit-exact, NaN included
    assert int((kinds == NUMBER).sum()) > 30_000 and int((kinds == BAD).sum()) > 10_000


def test_kernel_character_classes_agree_with_unicode() -> None:
    from bf_core.formats import _numkernel as k

    spaces = [c for c in range(0x110000) if chr(c).isspace()]
    assert [c for c in range(0x110000) if k.is_space(c)] == spaces
    marker_chars = set("".join(numbers.MISSING))
    for c in range(0x80, 0x110000):
        if k.sure_char(c):
            ch = chr(c)
            assert not ch.isdecimal() and not ch.isspace(), hex(c)
            low = ch.lower()
            mine = chr(k.lower(c))
            assert mine == low or (not (set(low) & marker_chars) and mine not in marker_chars), hex(c)


@pytest.mark.parametrize("kernel", [False, True])
def test_mixed_cells_of_a_spreadsheet(monkeypatch: pytest.MonkeyPatch, kernel: bool) -> None:
    monkeypatch.setattr(numbers, "KERNEL_MIN", 1 if kernel else 10**9)
    monkeypatch.setattr(numbers, "_kernel_loaded", kernel)
    cells = [1, 2.5, None, "3,5", True, float("nan"), "<1", "н/д", 2**60, "x"]
    values, kinds = classify(cells)
    assert kinds.tolist() == [NUMBER, NUMBER, EMPTY, NUMBER, BAD, EMPTY, BELOW, MARKER, IDLIKE, BAD]
    assert values[:2].tolist() == [1.0, 2.5] and values[3] == 3.5 and values[6] == 0.5
    with_none = ["1,5", None, "x", "", "н/д"] * 3
    assert classify(with_none)[1].tolist() == [NUMBER, EMPTY, BAD, EMPTY, MARKER] * 3


def test_cells_the_kernel_cannot_take_go_to_the_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    cells = ["1", "a" + chr(0) + "b", chr(0), "2" + chr(0), chr(0xD800) + "5", "7", ""]  # NUL inside, a lone surrogate
    expected = numbers.classify_reference(cells)
    monkeypatch.setattr(numbers, "KERNEL_MIN", 1)
    values, kinds = classify(cells)
    assert kinds.tolist() == expected[1].tolist() == [NUMBER, BAD, BAD, BAD, BAD, NUMBER, EMPTY]
    assert values[[0, 5]].tolist() == [1.0, 7.0]
