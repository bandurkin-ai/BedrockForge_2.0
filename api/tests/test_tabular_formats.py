"""Reading CSV/TXT and Excel files: encodings, delimiters, numbers, headers."""

import math
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.formats.numbers import parse_numbers, parse_value
from app.formats.tabular import ReadOptions
from app.formats.text import detect_delimiter, detect_encoding
from app.importing.service import read_table

import pandas as pd


@pytest.mark.parametrize("raw, expected", [
    ("12.5", (12.5, "number")),
    ("12,5", (12.5, "number")),
    ("-0,003", (-0.003, "number")),
    ("4 584 699,70", (4584699.7, "number")),
    ("4 584 699.7", (4584699.7, "number")),
    ("1e-3", (0.001, "number")),
    ("<0.005", (0.0025, "below")),
    ("< 0,01", (0.005, "below")),
    (">1000", (1000.0, "above")),
    ("н/д", (None, "missing")),
    ("", (None, "missing")),
    ("-", (None, "missing")),
    (7, (7.0, "number")),
    ("abc", (None, "bad")),
    ("12.5.3", (None, "bad")),
])
def test_parse_value(raw, expected):
    value, kind = parse_value(raw)
    assert kind == expected[1]
    assert value == pytest.approx(expected[0]) if expected[0] is not None else value is None


def test_parse_numbers_counts_kinds():
    parsed = parse_numbers(pd.Series(["1", "<0.2", "x", None, "н.о."]))

    assert (parsed.parsed, parsed.bad, parsed.below_limit) == (2, 1, 1)
    assert parsed.bad_examples == ["x"]
    assert math.isnan(parsed.values[2]) and parsed.values[1] == pytest.approx(0.1)


def _write(tmp_path: Path, name: str, text: str, encoding: str = "utf-8") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def test_russian_cp1251_semicolon_decimal_comma(tmp_path):
    path = _write(tmp_path, "collar.csv",
                  "Скважина;X;Y;Z;Глубина;Статус\r\nС-1;466570,586;4584699,697;557,95;300,3;пробурена\r\n"
                  "С-2;466543,962;4584688,653;561,438;250;план\r\n", "cp1251")

    table = read_table(path)

    assert table.format == "text"
    assert table.detected["encoding"] == "cp1251" and table.detected["delimiter"] == ";"
    assert list(table.frame.columns) == ["Скважина", "X", "Y", "Z", "Глубина", "Статус"]
    assert table.frame.loc[0, "X"] == pytest.approx(466570.586)
    assert table.frame["Глубина"].dtype == "float64"
    assert table.frame.loc[1, "Статус"] == "план"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp866", "koi8-r"])
def test_encodings_are_detected(tmp_path, encoding):
    text = "Скважина,Глубина,Азимут,Наклон\nСкв-1,0,10,-60\nСкв-1,50,12,-61\n"
    path = _write(tmp_path, "survey.csv", text, encoding)

    table = read_table(path)

    assert table.frame.columns[0] == "Скважина"
    assert table.frame.loc[0, "Скважина"] == "Скв-1"


def test_tab_whitespace_and_quoted_delimiters(tmp_path):
    tab = read_table(_write(tmp_path, "a.txt", "HOLE\tDEPTH\tAZ\tDIP\nA\t0\t10\t-60\n"))
    spaces = read_table(_write(tmp_path, "b.txt", "HOLE   DEPTH   AZ  DIP\nA      0       10  -60\nB      5       11  -61\n"))
    quoted = read_table(_write(tmp_path, "c.csv", 'HOLE,NOTE,AU\nA,"core, broken",0.5\n'))

    assert tab.detected["delimiter"] == "\t" and list(tab.frame.columns) == ["HOLE", "DEPTH", "AZ", "DIP"]
    assert spaces.detected["delimiter"] == "whitespace" and spaces.frame.loc[1, "DIP"] == -61
    assert quoted.frame.loc[0, "NOTE"] == "core, broken" and quoted.frame.loc[0, "AU"] == 0.5


def test_detect_delimiter_prefers_consistent_field_count():
    lines = ["A;B;C", "1,5;2,5;3", "4;5,1;6"]  # commas are decimals here
    assert detect_delimiter(lines) == ";"
    assert detect_encoding("abc".encode()) == "utf-8"


def test_header_row_title_lines_and_no_header(tmp_path):
    path = _write(tmp_path, "assay.csv", "Лаборатория ЦЛ, протокол 15\n\nBHID,FROM,TO,AU\nA,0,1,0.5\nA,1,2,<0.01\n")

    table = read_table(path, ReadOptions(header_row=2))
    headerless = read_table(path, ReadOptions(header_row=0, delimiter=","))

    assert list(table.frame.columns) == ["BHID", "FROM", "TO", "AU"]
    assert table.frame["AU"].tolist() == [0.5, 0.005]
    assert any("<x" in n and "AU" in n for n in table.notes)
    assert list(headerless.frame.columns)[:2] == ["Колонка 1", "Колонка 2"]
    with pytest.raises(ValueError):
        read_table(path, ReadOptions(header_row=50))


def test_mixed_column_stays_text_with_a_note_and_names_are_cleaned(tmp_path):
    rows = "HOLE,AU,AU,,AG\n" + "".join(f"A,{i / 10},{i},x,{i}\n" for i in range(30)) + "A,ошибка,1,y,2\n"
    table = read_table(_write(tmp_path, "m.csv", rows))

    assert list(table.frame.columns) == ["HOLE", "AU", "AU_2", "Колонка 4", "AG"]
    assert str(table.frame["AU"].dtype) == "string"
    assert any("AU" in n and "ошибка" in n for n in table.notes)
    assert table.frame["AG"].dtype == "float64"


def _workbook(tmp_path: Path) -> Path:
    wb = Workbook()
    wb.active.title = "Пусто"
    sheet = wb.create_sheet("Устья")
    sheet.append(["Сводка по скважинам"])
    sheet.append(["Скважина", "X, м", "Y, м", "Z, м", "Глубина (м)", "Дата", None])
    sheet.append(["С-1", 466570.586, 4584699.697, 557.95, 300.3, datetime(2024, 5, 17), None])
    sheet.append(["С-2", "466543,962", 4584688.653, 561.438, 250, datetime(2024, 6, 1, 14, 30), None])
    sheet.append([None] * 7)
    other = wb.create_sheet("Замеры")
    other.append(["Скважина", "Глубина", "Азимут", "Наклон"])
    other.append(["С-1", 0, 10, -60])
    path = tmp_path / "book.xlsx"
    wb.save(path)
    return path


def test_excel_first_sheet_with_data_header_row_dates_and_mixed_cells(tmp_path):
    path = _workbook(tmp_path)

    table = read_table(path, ReadOptions(header_row=2))

    assert table.format == "excel"
    assert table.sheets == ["Пусто", "Устья", "Замеры"] and table.detected["sheet"] == "Устья"
    assert list(table.frame.columns) == ["Скважина", "X, м", "Y, м", "Z, м", "Глубина (м)", "Дата"]
    assert table.frame["X, м"].tolist() == pytest.approx([466570.586, 466543.962])
    assert table.frame["Дата"].tolist() == ["2024-05-17", "2024-06-01 14:30:00"]
    assert len(table.frame) == 2  # empty trailing row dropped


def test_excel_sheet_choice_and_errors(tmp_path):
    path = _workbook(tmp_path)

    survey = read_table(path, ReadOptions(sheet="Замеры"))

    assert survey.detected["sheet"] == "Замеры" and survey.frame.loc[0, "Наклон"] == -60
    with pytest.raises(ValueError, match="нет листа"):
        read_table(path, ReadOptions(sheet="Нет такого"))
