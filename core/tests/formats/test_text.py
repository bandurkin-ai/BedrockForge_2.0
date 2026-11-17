"""CSV/TXT reader: encodings, delimiters, header row, numbers, ragged and hostile files (ТЗ 7.7, 9.6, 9.12)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from bf_core.formats import numbers, text
from bf_core.formats.errors import FormatError
from bf_core.formats.table import ReadOptions, TableRead
from bf_core.formats.text import detect_delimiter, detect_encoding, read_text


def _write(tmp_path: Path, name: str, content: str | bytes, encoding: str = "utf-8") -> Path:
    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode(encoding))
    return path


def test_russian_cp1251_semicolon_decimal_comma(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "collar.csv",
        "Скважина;X;Y;Z;Глубина;Статус\r\nС-1;466570,586;4584699,697;557,95;300,3;пробурена\r\n"
        "С-2;466543,962;4 584 688,653;561,438;250;план\r\n",
        "cp1251",
    )
    table = read_text(path)
    assert table.info.format == "csv" and table.info.encoding == "cp1251" and table.info.delimiter == ";"
    assert table.info.header_row == 1 and table.rows == 2
    assert table.names == ["Скважина", "X", "Y", "Z", "Глубина", "Статус"]
    assert [c.kind for c in table.columns] == ["text", "number", "number", "number", "number", "text"]
    assert table.column("X").values.tolist() == [466570.586, 466543.962]
    assert table.column("Y").values.tolist() == [4584699.697, 4584688.653]
    assert table.column("Глубина").values.dtype == np.float64
    assert table.column("Статус").to_pylist() == ["пробурена", "план"] and table.warnings == []


@pytest.mark.parametrize(
    "encoding", ["utf-8", "utf-8-sig", "cp1251", "cp866", "koi8-r", "utf-16", "utf-16-le", "utf-16-be"]
)
def test_encodings_are_detected(tmp_path: Path, encoding: str) -> None:
    content = "Скважина,Глубина,Азимут,Наклон\nСкв-1,0,10,-60\nСкв-1,50,12,-61\nСкважина-2,0,0,-90\n"
    table = read_text(_write(tmp_path, "survey.csv", content, encoding))
    assert table.info.encoding == encoding
    assert table.names == ["Скважина", "Глубина", "Азимут", "Наклон"]
    assert table.column("Скважина").to_pylist() == ["Скв-1", "Скв-1", "Скважина-2"]
    assert table.column("Наклон").values.tolist() == [-60.0, -61.0, -90.0] and table.warnings == []


def test_manual_encoding_and_the_latin1_fallback(tmp_path: Path) -> None:
    path = _write(tmp_path, "w.csv", "Hole;Note\nA;café 5°\n", "latin-1")
    assert read_text(path, ReadOptions(encoding="latin-1")).column("Note").to_pylist() == ["café 5°"]
    assert detect_encoding(b"abc") == "utf-8"
    assert detect_encoding("№ пробы;Примечание".encode("cp1251")) == "cp1251"
    with pytest.raises(FormatError) as info:
        read_text(path, ReadOptions(encoding="ebcdic"))
    assert info.value.code == "unknown_encoding"


def test_tab_pipe_whitespace_and_quoted_delimiters(tmp_path: Path) -> None:
    tab = read_text(_write(tmp_path, "a.txt", "HOLE\tDEPTH\tAZ\tDIP\nA\t0\t10\t-60\n"))
    pipe = read_text(_write(tmp_path, "p.txt", "HOLE|DEPTH|AZ\nA|0|10\nB|1,5|11\n"))
    spaces = read_text(
        _write(tmp_path, "b.txt", "HOLE   DEPTH   AZ  DIP\nA      0       10  -60\nB      5       11  -61\n")
    )
    quoted = read_text(
        _write(tmp_path, "c.csv", 'HOLE,NOTE,AU\nA,"core, broken",0.5\nB,"5"" casing",0.7\nC,HQ 3" core,0.9\n')
    )
    assert tab.info.delimiter == "\t" and tab.names == ["HOLE", "DEPTH", "AZ", "DIP"]
    assert pipe.info.delimiter == "|" and pipe.column("DEPTH").values.tolist() == [0.0, 1.5]
    assert spaces.info.delimiter == "whitespace" and spaces.column("DIP").values[1] == -61
    assert quoted.column("NOTE").to_pylist() == ["core, broken", '5" casing', 'HQ 3" core']
    assert quoted.column("AU").values.tolist() == [0.5, 0.7, 0.9]
    with pytest.raises(FormatError) as info:
        read_text(_write(tmp_path, "d.csv", "a,b\n1,2\n"), ReadOptions(delimiter="#"))
    assert info.value.code == "unknown_delimiter"


def test_detect_delimiter_prefers_consistent_field_count() -> None:
    assert detect_delimiter(["A;B;C", "1,5;2,5;3", "4;5,1;6"]) == ";"  # commas are decimals here
    assert detect_delimiter(["a b", "c d"]) == "whitespace" and detect_delimiter([]) == ","


def test_header_row_title_lines_and_no_header(tmp_path: Path) -> None:
    path = _write(tmp_path, "assay.csv", "Лаборатория ЦЛ, протокол 15\n\n  \nBHID,FROM,TO,AU\nA,0,1,0.5\nA,1,2,<0.01\n")
    table = read_text(path, ReadOptions(header_row=2))  # empty lines are not counted
    assert table.names == ["BHID", "FROM", "TO", "AU"]
    au = table.column("AU")
    assert au.values.tolist() == [0.5, 0.005] and (au.below_limit, au.above_limit) == (1, 0)
    assert any("<x" in w and "AU" in w and "1" in w for w in table.warnings)
    headerless = read_text(path, ReadOptions(header_row=0, delimiter=","))
    assert headerless.names[:2] == ["Колонка 1", "Колонка 2"] and headerless.rows == 4
    with pytest.raises(FormatError) as info:
        read_text(path, ReadOptions(header_row=50))
    assert info.value.code == "header_row" and "только 4" in info.value.message


def test_mixed_column_stays_text_with_a_note_and_names_are_cleaned(tmp_path: Path) -> None:
    rows = "HOLE,AU,AU,,AG\n" + "".join(f"A,{i / 10},{i},x,{i}\n" for i in range(30)) + "A,ошибка,1,y,2\n"
    table = read_text(_write(tmp_path, "m.csv", rows))
    assert table.names == ["HOLE", "AU", "AU_2", "Колонка 4", "AG"]
    assert table.column("AU").kind == "text" and table.column("AU").to_pylist()[-2:] == ["2.9", "ошибка"]
    assert any("AU" in w and "ошибка" in w for w in table.warnings)
    assert table.column("AG").kind == "number"
    parsed = numbers.parse_numbers(table.column("AU").values)  # what the importer does for a numeric field
    assert (parsed.parsed, parsed.bad, parsed.bad_examples) == (30, 1, ["ошибка"]) and parsed.values[29] == 2.9


def test_limits_empty_marks_and_never_zero_for_missing(tmp_path: Path) -> None:
    content = "ID;AU;AG;CU\n1;<0.005;>1000;-\n2;н/д;N/A;0\n3;0,3;NA;н.о.\n4;;< 0,2;\n5;-;—;1 000,5\n"
    table = read_text(_write(tmp_path, "lab.csv", content))
    au, ag, cu = table.column("AU"), table.column("AG"), table.column("CU")
    assert (au.kind, ag.kind, cu.kind) == ("number", "number", "number")
    assert au.values[0] == 0.0025 and au.values[2] == 0.3 and np.isnan(au.values[[1, 3, 4]]).all()
    assert ag.values[0] == 1000.0 and ag.values[3] == 0.1 and np.isnan(ag.values[[1, 2, 4]]).all()
    assert cu.values[1] == 0.0 and cu.values[4] == 1000.5 and np.isnan(cu.values[[0, 2, 3]]).all()
    assert (au.below_limit, ag.below_limit, ag.above_limit) == (1, 1, 1)
    assert sum("<x" in w for w in table.warnings) == 2 and sum(">x" in w for w in table.warnings) == 1
    assert table.column("ID").text() == ["1", "2", "3", "4", "5"]  # identifiers out of a number column


def test_identifiers_stay_text(tmp_path: Path) -> None:
    content = "HOLE,SAMPLE,BARCODE,DEPTH\n007,1,12345678901234567890,0010\n012,2,12345678901234567891,0020\n"
    table = read_text(_write(tmp_path, "ids.csv", content))
    assert [c.kind for c in table.columns] == ["text", "number", "text", "text"]
    assert table.column("HOLE").to_pylist() == ["007", "012"]
    assert table.column("BARCODE").to_pylist()[1] == "12345678901234567891" and table.warnings == []
    assert numbers.parse_numbers(table.column("DEPTH").values).values.tolist() == [10.0, 20.0]
    forced = read_text(_write(tmp_path, "ids.csv", content), ReadOptions(text_columns=("SAMPLE",)))
    assert forced.column("SAMPLE").kind == "text" and forced.column("SAMPLE").to_pylist() == ["1", "2"]


def test_ragged_rows_blank_lines_and_line_endings(tmp_path: Path) -> None:
    content = "A;B;C\r1;2;3\r\r\n4;5\n;;\n7;8;9;extra;10\n \t \n11;12;13\r\n"
    table = read_text(_write(tmp_path, "r.csv", content))
    assert table.names == ["A", "B", "C", "Колонка 4", "Колонка 5"] and table.rows == 4
    assert table.column("A").values.tolist() == [1.0, 4.0, 7.0, 11.0]
    c = table.column("C").values
    assert c[0] == 3.0 and math.isnan(c[1]) and c[2:].tolist() == [9.0, 13.0]
    assert table.column("Колонка 4").to_pylist() == [None, None, "extra", None]
    k5 = table.column("Колонка 5").values
    assert k5[2] == 10.0 and np.isnan(k5[[0, 1, 3]]).all()
    only_header = read_text(_write(tmp_path, "h.csv", "A;B\n"))
    assert only_header.names == ["A", "B"] and only_header.rows == 0 and only_header.column("A").kind == "text"


def _big(rows: int) -> str:
    lines = ["Скважина;От;До;AU;Литология;Проба"]
    for i in range(rows):
        au = "<0,005" if i % 97 == 0 else f"{(i % 1000) / 100:.2f}".replace(".", ",")
        lines.append(f"С-{i % 50};{i};{i + 1},5;{au};{'гранит' if i % 3 else 'н/д'};{i:06d}")
    return "\r\n".join(lines) + "\r\n"


def _check_big(table: TableRead, rows: int) -> None:
    assert table.rows == rows and [c.kind for c in table.columns] == [
        "text",
        "number",
        "number",
        "number",
        "text",
        "text",
    ]
    assert table.column("От").values.tolist() == [float(i) for i in range(rows)]
    assert table.column("До").values[rows - 1] == rows - 1 + 1.5
    au = table.column("AU")
    assert au.below_limit == len(range(0, rows, 97)) and au.values[0] == 0.0025 and au.values[199] == 1.99
    lith = table.column("Литология").to_pylist()
    assert lith[0] is None and lith[1] == "гранит" and lith.count(None) == len(range(0, rows, 3))
    assert table.column("Проба").to_pylist()[rows - 1] == f"{rows - 1:06d}"
    assert table.column("Скважина").to_pylist()[51] == "С-1"


@pytest.mark.parametrize("kernel", [False, True])
def test_streaming_in_small_blocks_equals_one_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kernel: bool
) -> None:
    rows = 20_000
    path = _write(tmp_path, "big.csv", _big(rows), "cp1251")
    whole = read_text(path)
    _check_big(whole, rows)
    monkeypatch.setattr(text, "BLOCK", 1777)  # blocks end inside lines, CRLF pairs and numbers
    monkeypatch.setattr(numbers, "KERNEL_MIN", 1 if kernel else 10**9)
    monkeypatch.setattr(numbers, "_kernel_loaded", kernel)
    seen: list[float] = []
    chunked = read_text(path, progress=lambda f, m: seen.append(f))
    _check_big(chunked, rows)
    assert chunked.to_arrow().equals(whole.to_arrow()) and chunked.warnings == whole.warnings
    assert len(seen) > 100 and seen == sorted(seen) and seen[-1] == 1.0
    utf16 = read_text(_write(tmp_path, "big16.csv", _big(rows), "utf-16"))  # multi-byte characters cut by blocks
    assert utf16.to_arrow().equals(whole.to_arrow())


def test_text_found_late_in_a_number_column_keeps_every_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = ["HOLE,AU,NEW"] + [f"A,{i}.50," for i in range(3000)] + ["A,abc,7", "A,0.25,x"] + ["A,1,2,late"]
    path = _write(tmp_path, "late.csv", "\n".join(lines))
    monkeypatch.setattr(text, "BLOCK", 4096)
    table = read_text(path)
    au = table.column("AU")
    assert (
        au.kind == "text" and au.to_pylist()[:2] == ["0.50", "1.50"] and au.to_pylist()[3000:] == ["abc", "0.25", "1"]
    )
    assert any("AU" in w and "abc" in w for w in table.warnings)
    assert table.column("NEW").to_pylist()[2998:] == [None, None, "7", "x", "2"]
    assert table.names == ["HOLE", "AU", "NEW", "Колонка 4"]
    assert table.column("Колонка 4").to_pylist()[-2:] == [None, "late"] and table.rows == 3003


def test_broken_encoding_is_reported_not_hidden(tmp_path: Path) -> None:
    good = "Скважина;Порода\nС-1;гранит\nС-2;диорит\n".encode()
    broken = good.replace("гранит".encode(), b"\xff\xfe" + "гранит".encode())
    table = read_text(_write(tmp_path, "b.csv", broken))
    assert table.info.encoding == "utf-8" and table.column("Скважина").to_pylist() == ["С-1", "С-2"]
    assert table.column("Порода").to_pylist() == ["\ufffd\ufffdгранит", "диорит"]
    assert any("2" in w and "utf-8" in w for w in table.warnings)
    cut = read_text(_write(tmp_path, "c.csv", good[:-11]))  # truncated inside a line: the last row is shorter
    assert cut.rows == 2 and cut.column("Порода").to_pylist()[1] == "д" and cut.info.encoding == "utf-8"
    odd = read_text(_write(tmp_path, "d.csv", good[:-10]))  # … and inside a character
    assert odd.column("Порода").to_pylist()[1] == "д\ufffd" and any("utf-8" in w for w in odd.warnings)
    forced = read_text(_write(tmp_path, "e.csv", good), ReadOptions(encoding="cp866"))
    assert forced.info.encoding == "cp866" and forced.names != ["Скважина", "Порода"]


def test_empty_files_and_bombs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def code(path: Path, options: ReadOptions | None = None) -> str:
        with pytest.raises(FormatError) as info:
            read_text(path, options)
        assert info.value.message and info.value.hint
        return info.value.code

    assert code(_write(tmp_path, "e.csv", b"")) == "empty_file"
    assert code(_write(tmp_path, "s.csv", b"\r\n  \r\n\t\n")) == "empty_file"
    assert code(_write(tmp_path, "h.csv", "a,b\n1,2\n"), ReadOptions(header_row=-1)) == "header_row"
    monkeypatch.setattr(text, "MAX_LINE", 10_000)
    assert code(_write(tmp_path, "l.csv", "a,b\n" + "x" * 50_000)) == "too_large"  # one endless line
    assert code(_write(tmp_path, "w.csv", "a\n" + ",".join("1" * 1 for _ in range(4999)) + "\n")) == "too_large"
    binary = bytes(range(256)) * 40
    table = read_text(_write(tmp_path, "bin.csv", binary))  # garbage in, a table of garbage out — but no crash
    assert table.rows >= 0 and table.warnings


def test_progress_cancels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, "big.csv", _big(5000), "utf-8")
    monkeypatch.setattr(text, "BLOCK", 2048)

    class Cancelled(Exception):
        pass

    calls = []

    def cancel(fraction: float, message: str | None) -> None:
        calls.append(fraction)
        if fraction > 0.5:
            raise Cancelled

    with pytest.raises(Cancelled):
        read_text(path, progress=cancel)
    assert 0.5 < calls[-1] < 0.6
    path.unlink()
