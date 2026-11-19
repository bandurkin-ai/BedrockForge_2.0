"""Points XYZ: CSV / TXT / XYZ / DAT → a layer of points, and back (ТЗ 9.12; ADR 0011 §2, 7.9).

The cycle read → write → read must give the same doubles (the task asks for 1e−12; `repr` gives bit for bit).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import from_lines, from_points
from bf_core.formats.points import read_points, write_points, write_points_dat
from bf_core.formats.table import ReadOptions

ROWS = [[466570.586, 4584699.697, 557.95], [466580.5, 4584710.25, 560.0], [466590.0, 4584720.125, 561.5]]


def put(folder: Path, name: str, text: str, encoding: str = "utf-8") -> Path:
    path = folder / name
    path.write_bytes(text.encode(encoding))
    return path


def texts(layer, name: str) -> list[str | None]:
    return layer.attrs.column(name).to_pylist()


def numbers(layer, name: str) -> np.ndarray:
    return layer.attrs.column(name).to_numpy()


# ---------------------------------------------------------------- reading


def test_csv_with_a_header_decimal_commas_and_empty_cells(tmp_path: Path) -> None:
    text = (
        "X;Y;Z;Имя;Au\r\n"
        "466570,586;4584699,697;557,95;Т-1;1,25\r\n"
        "466580,5;4584710,25;;Т-2;\r\n"
        "466590;4584720,125;561,5;;0,5\r\n"
    )
    out = read_points(put(tmp_path, "Точки съёмки.csv", text, "cp1251"))
    (layer,) = out.layers
    assert out.format == "points_csv" and out.records == 3 and out.encoding == "cp1251"
    assert out.header.delimiter == ";" and out.header.header_row == 1
    assert out.fields == {"x": "X", "y": "Y", "z": "Z"}
    assert layer.name == "Точки съёмки" and layer.points_only and layer.offsets is None
    expected = np.array(ROWS)
    expected[1, 2] = np.nan  # an empty Z is NaN, never 0
    assert layer.xyz.dtype == np.float64 and np.array_equal(layer.xyz, expected, equal_nan=True)
    assert layer.seq.tolist() == [0, 1, 2]
    assert layer.attrs.column_names == ["Имя", "Au"]
    assert texts(layer, "Имя") == ["Т-1", "Т-2", None]
    assert np.array_equal(numbers(layer, "Au"), [1.25, np.nan, 0.5], equal_nan=True)
    assert out.warnings == []


@pytest.mark.parametrize(
    ("name", "text", "delimiter"),
    [
        ("a.xyz", "466570.586 4584699.697 557.95\n466580.5 4584710.25 560\n466590 4584720.125 561.5\n", "whitespace"),
        ("b.txt", "466570,586 4584699,697 557,95\n466580,5 4584710,25 560\n466590 4584720,125 561,5\n", "whitespace"),
        ("c.txt", "466570.586\t4584699.697\t557.95\n466580.5\t4584710.25\t560\n466590\t4584720.125\t561.5\n", "\t"),
        ("d.csv", "466570.586,4584699.697,557.95\n466580.5,4584710.25,560\n466590,4584720.125,561.5\n", ","),
        ("e.csv", "466570,586;4584699,697;557,95\n466580,5;4584710,25;560\n466590;4584720,125;561,5\n", ";"),
        (
            "f.xyz",
            "   466570.586   4584699.697    557.95\n\n  466580.5 4584710.25 560\n466590 4584720.125 561.5",
            "whitespace",
        ),
    ],
)
def test_files_without_a_header(tmp_path: Path, name: str, text: str, delimiter: str) -> None:
    out = read_points(put(tmp_path, name, text))
    assert out.header.delimiter == delimiter and out.header.header_row == 0
    assert np.array_equal(out.layers[0].xyz, np.array(ROWS))
    assert out.layers[0].attrs is None and out.records == 3


@pytest.mark.parametrize(
    ("text", "delimiter", "header_row"),
    [
        ("100,5 200,5 300,5\n110,5 210,5 310,5\n", "whitespace", 0),  # `5 200` is not the number 5200
        ("100.5, 200.5, 300.5\n110.5, 210.5, 310.5\n", ",", 0),
        ("100.5\t200.5 300.5\n110.5\t210.5 310.5\n", "whitespace", 0),  # tabs and spaces mixed
        ("X;Y;Z;Имя\n100,5;200,5;300,5;Точка 1, север\n110,5;210,5;310,5;Точка 2\n", ";", 1),
        ("X,Y,Z,Name\n100.5,200.5,300.5,Point A\n110.5,210.5,310.5,Point B north\n", ",", 1),
        ("X\tY\tZ\tName\n100,5\t200,5\t300,5\tPoint A\n110,5\t210,5\t310,5\tB\n", "\t", 1),
        ("Протокол съёмки № 5\nX;Y;Z\n100,5;200,5;300,5\n110,5;210,5;310,5\n", ";", 2),
    ],
)
def test_the_delimiter_is_told_from_the_decimal_comma(
    tmp_path: Path, text: str, delimiter: str, header_row: int
) -> None:
    options = ReadOptions(header_row=header_row) if header_row == 2 else None
    out = read_points(put(tmp_path, "local.txt", text), options=options)
    assert out.header.delimiter == delimiter and out.header.header_row == header_row
    assert out.layers[0].xyz.tolist() == [[100.5, 200.5, 300.5], [110.5, 210.5, 310.5]]


def test_numbers_with_digit_groups(tmp_path: Path) -> None:
    text = "466 570,586\t4 584 699,697\t557,95\n466 580,5\t4 584 710,25\t560\n"
    out = read_points(put(tmp_path, "grouped.txt", text))
    assert out.header.delimiter == "\t" and np.array_equal(out.layers[0].xyz, np.array(ROWS[:2]))
    text = "X;Y;Z\n466 570,586;4 584 699,697;557,95\n466 580,5;4 584 710,25;560\n"
    out = read_points(put(tmp_path, "grouped.csv", text))
    assert out.header.delimiter == ";" and np.array_equal(out.layers[0].xyz, np.array(ROWS[:2]))


def test_extra_columns_without_a_header_are_attributes(tmp_path: Path) -> None:
    out = read_points(put(tmp_path, "p.xyz", "1.5 2.5 3.5 12 gold\n4.5 5.5 6.5 13 ore\n"))
    (layer,) = out.layers
    assert layer.xyz.tolist() == [[1.5, 2.5, 3.5], [4.5, 5.5, 6.5]]
    assert layer.attrs.column_names == ["Колонка 4", "Колонка 5"]
    assert numbers(layer, "Колонка 4").tolist() == [12.0, 13.0] and texts(layer, "Колонка 5") == ["gold", "ore"]


def test_two_columns_are_points_without_heights(tmp_path: Path) -> None:
    out = read_points(put(tmp_path, "xy.txt", "X Y\n1.5 2.5\n4.5 5.5\n"))
    assert np.array_equal(out.layers[0].xyz, [[1.5, 2.5, np.nan], [4.5, 5.5, np.nan]], equal_nan=True)
    assert out.fields["z"] is None and any("Z" in w for w in out.warnings)


@pytest.mark.parametrize(
    ("header", "fields"),
    [
        ("EAST;NORTH;RL", ("EAST", "NORTH", "RL")),
        ("Восток;Север;Отметка", ("Восток", "Север", "Отметка")),
        ("Х, м;У, м;Z, м", ("Х, м", "У, м", "Z, м")),  # Cyrillic Х and У
        ("Easting;Northing;Elevation", ("Easting", "Northing", "Elevation")),
        ("x;y;z", ("x", "y", "z")),
        ("XPT;YPT;ZPT", ("XPT", "YPT", "ZPT")),
    ],
)
def test_coordinate_columns_are_found_by_their_names(tmp_path: Path, header: str, fields: tuple[str, str, str]) -> None:
    text = f"N;{header};Код\n1;466570,586;4584699,697;557,95;A\n2;466580,5;4584710,25;560;B\n"
    out = read_points(put(tmp_path, "named.csv", text))
    assert out.fields == dict(zip("xyz", fields, strict=True))
    assert np.array_equal(out.layers[0].xyz, np.array(ROWS[:2]))
    assert out.layers[0].attrs.column_names == ["N", "Код"]


def test_columns_chosen_by_hand(tmp_path: Path) -> None:
    path = put(tmp_path, "survey.csv", "A;B;H;Z\n4584699,697;466570,586;557,95;1\n4584710,25;466580,5;560;2\n")
    out = read_points(path, x="B", y="A", z="H", name="Съёмка")
    assert out.layers[0].name == "Съёмка" and np.array_equal(out.layers[0].xyz, np.array(ROWS[:2]))
    assert out.layers[0].attrs.column_names == ["Z"]
    flat = read_points(path, x="B", y="A", z="")
    assert np.isnan(flat.layers[0].xyz[:, 2]).all() and flat.layers[0].attrs.column_names == ["H", "Z"]
    with pytest.raises(FormatError) as info:
        read_points(path, x="Нет такой")
    assert info.value.code == "no_column" and "Нет такой" in info.value.message and "A, B, H, Z" in info.value.hint


def test_rows_without_x_or_y_are_left_out_and_counted(tmp_path: Path) -> None:
    text = "X;Y;Z;Проба\n1;2;3;a\n;5;6;b\n7;;9;c\n10;11;;d\nн/д;14;15;e\nabc;17;18;f\n"
    out = read_points(put(tmp_path, "gaps.csv", text))
    (layer,) = out.layers
    assert np.array_equal(layer.xyz, [[1.0, 2.0, 3.0], [10.0, 11.0, np.nan]], equal_nan=True)
    assert layer.seq.tolist() == [0, 3] and texts(layer, "Проба") == ["a", "d"]
    assert out.records == 6
    assert any("4" in w and "пропущен" in w for w in out.warnings)
    assert any("abc" in w for w in out.warnings)  # what could not be read is named


def test_attribute_names_that_differ_only_by_case(tmp_path: Path) -> None:
    out = read_points(put(tmp_path, "case.csv", "X;Y;Z;Au;AU;au\n1,5;2,5;3,5;4;5;6\n"))
    (layer,) = out.layers
    assert layer.attrs.column_names == ["Au", "AU_2", "au_3"]
    assert [numbers(layer, n).tolist() for n in layer.attrs.column_names] == [[4.0], [5.0], [6.0]]
    assert sum("AU_2" in w or "au_3" in w for w in out.warnings) == 2


def test_micromine_dat_with_points(tmp_path: Path) -> None:
    layer = from_points("p", np.array(ROWS), attrs={"CODE": ["a", None, "c"], "AU": [1.25, np.nan, 0.5]})
    written = write_points_dat(tmp_path / "pts.DAT", [layer])
    assert written.rows == 3 and [f.name for f in written.fields][:3] == ["EAST", "NORTH", "RL"]
    out = read_points(written.path)
    assert out.format == "points_dat" and out.fields == {"x": "EAST", "y": "NORTH", "z": "RL"}
    assert np.array_equal(out.layers[0].xyz, layer.xyz)
    assert texts(out.layers[0], "CODE") == ["a", None, "c"]
    assert np.array_equal(numbers(out.layers[0], "AU"), [1.25, np.nan, 0.5], equal_nan=True)


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("empty.csv", b"", "empty_file"),
        ("blank.xyz", b"\n\n  \n", "empty_file"),
        ("header.csv", b"X;Y;Z\n", "no_points"),
        ("names.csv", "Скважина;Глубина\nС-1;10\n".encode(), "no_coordinates"),
        ("text.csv", b"X;Y;Z\nabc;def;1\n", "no_points"),
        ("binary.xyz", bytes(range(256)) * 64, "unsupported"),
        ("image.csv", b"\x89PNG\r\n\x1a\n" + b"\x00" * 200, "unsupported"),
        ("wide.csv", (";".join(["1"] * 5000) + "\n").encode(), "too_large"),
        ("one.txt", b"12345\n67890\n", "no_coordinates"),
    ],
)
def test_files_that_are_not_points(tmp_path: Path, name: str, data: bytes, code: str) -> None:
    path = tmp_path / name
    path.write_bytes(data)
    with pytest.raises(FormatError) as info:
        read_points(path)
    assert info.value.code == code and info.value.message and info.value.hint


def test_options_and_progress(tmp_path: Path) -> None:
    text = "Отчёт о съёмке\nX|Y|Z\n1,5|2,5|3,5\n4,5|5,5|6,5\n"
    path = put(tmp_path, "report.txt", text, "cp866")
    seen: list[float] = []
    out = read_points(path, lambda f, m: seen.append(f), options=ReadOptions(encoding="cp866", header_row=2))
    assert out.layers[0].xyz.tolist() == [[1.5, 2.5, 3.5], [4.5, 5.5, 6.5]] and out.header.delimiter == "|"
    assert seen[0] == 0.0 and seen[-1] == 1.0 and seen == sorted(seen)
    stored = path.rename(tmp_path / "upload-0001")
    named = read_points(stored, options=ReadOptions(encoding="cp866", header_row=2), filename="report.txt")
    assert named.layers[0].name == "report"


def test_reading_can_be_cancelled(tmp_path: Path) -> None:
    class Cancelled(Exception):
        pass

    def progress(fraction: float, message: str | None) -> None:
        if fraction > 0.3:
            raise Cancelled

    path = put(tmp_path, "many.csv", "X;Y;Z\n" + "1,5;2,5;3,5\n" * 5000)
    with pytest.raises(Cancelled):
        read_points(path, progress)


# ---------------------------------------------------------------- the cycle


def hard_points(n: int, seed: int = 11) -> np.ndarray:
    rng = np.random.default_rng(seed)
    xyz = np.column_stack([rng.uniform(3e5, 7e5, n), rng.uniform(4e6, 8e6, n), rng.uniform(-500, 4000, n)])
    xyz[0] = [0.1 + 0.2, -0.0, 5e-324]  # doubles that need all 17 digits, a negative zero, the smallest number
    xyz[1] = [123456789012345.67, -1.7976931348623157e308, 1e-7]
    xyz[2] = [1e22, 2.5e-10, np.nan]
    xyz[3::7, 2] = np.nan
    return xyz


@pytest.mark.parametrize(
    ("delimiter", "decimal", "count"),
    [(";", ".", 5000), (";", ",", 5000), ("\t", ",", 60), (",", ".", 60), (" ", ".", 60), ("|", ",", 60)],
)
def test_cycle_keeps_every_double(tmp_path: Path, delimiter: str, decimal: str, count: int) -> None:
    xyz = hard_points(count)
    rng = np.random.default_rng(5)
    grade = rng.uniform(0, 30, count)
    grade[::5] = np.nan
    attrs: dict[str, object] = {"Au": grade}
    if delimiter != " ":  # a text with spaces cannot be kept between spaces
        attrs["Имя"] = [None if k % 9 == 0 else f'Т-{k}; "проба", {k}|x' for k in range(count)]
    layer = from_points("Точки", xyz, attrs=attrs)
    written = write_points(tmp_path / "cycle.csv", [layer], delimiter=delimiter, decimal=decimal)
    assert written.rows == count and written.columns[:3] == ["X", "Y", "Z"] and not written.warnings
    assert not list(tmp_path.glob("*.part"))
    back = read_points(written.path).layers[0]
    assert back.points_only and back.point_count == count
    both = np.isfinite(xyz) & np.isfinite(back.xyz)
    assert np.array_equal(np.isnan(xyz), np.isnan(back.xyz))
    assert np.abs(xyz[both] - back.xyz[both]).max() <= 1e-12
    assert np.array_equal(xyz, back.xyz, equal_nan=True)  # in fact bit for bit
    assert np.array_equal(numbers(back, "Au"), grade, equal_nan=True)
    if delimiter != " ":
        assert texts(back, "Имя") == attrs["Имя"]
    # Once more: the second file is the same as the first, byte for byte.
    again = write_points(tmp_path / "again.csv", [back], delimiter=delimiter, decimal=decimal)
    assert again.path.read_bytes() == written.path.read_bytes()


def test_cycle_through_dat_keeps_every_double(tmp_path: Path) -> None:
    xyz = hard_points(300)
    layer = from_points("p", xyz, attrs={"GRADE": np.linspace(0, 1, 300)})
    back = read_points(write_points_dat(tmp_path / "c.dat", [layer]).path).layers[0]
    assert np.array_equal(back.xyz, xyz, equal_nan=True)
    assert np.array_equal(numbers(back, "GRADE"), np.linspace(0, 1, 300))


def test_vertices_of_lines_and_several_layers(tmp_path: Path) -> None:
    lines = from_lines("Линии", [np.array(ROWS), np.array(ROWS[:2]) + 1.0], attrs={"Код": ["a", "b"]})
    points = from_points("Точки", np.array(ROWS[:1]) + 5.0, attrs={"Au": [2.5]})
    written = write_points(tmp_path / "all.csv", [lines, points])
    assert written.rows == 6 and written.columns == ["X", "Y", "Z", "LAYER", "Код", "Au"]
    back = read_points(written.path).layers[0]
    assert np.array_equal(back.xyz, np.vstack([ROWS, np.array(ROWS[:2]) + 1.0, np.array(ROWS[:1]) + 5.0]))
    assert texts(back, "LAYER") == ["Линии"] * 5 + ["Точки"]
    assert texts(back, "Код") == ["a", "a", "a", "b", "b", None]
    assert np.array_equal(numbers(back, "Au"), [np.nan] * 5 + [2.5], equal_nan=True)


def test_what_cannot_be_written_is_an_error_or_a_warning(tmp_path: Path) -> None:
    layer = from_points("p", np.array(ROWS), attrs={"Имя": ["а б", "в\nг", "д"], "V": [1.0, np.inf, 3.0]})
    for options in ({"delimiter": ",", "decimal": ","}, {"delimiter": " "}, {"encoding": "ascii"}, {"delimiter": "#"}):
        with pytest.raises(FormatError) as info:
            write_points(tmp_path / "bad.csv", [layer], **options)
        assert info.value.code in ("unrepresentable", "unknown_delimiter") and info.value.hint
        assert not (tmp_path / "bad.csv").exists() and not list(tmp_path.glob("*.part"))
    written = write_points(tmp_path / "ok.csv", [layer], encoding="cp1251")
    assert len(written.warnings) == 2  # a line break inside a text, an infinite number
    back = read_points(written.path, encoding="cp1251").layers[0]  # too few letters to tell cp1251 from cp866
    assert texts(back, "Имя") == ["а б", "в г", "д"]
    assert np.array_equal(numbers(back, "V"), [1.0, np.nan, 3.0], equal_nan=True)
    with pytest.raises(FormatError) as info:
        write_points(tmp_path / "none.csv", [])
    assert info.value.code == "no_points"


def test_writing_can_be_cancelled_and_leaves_no_file(tmp_path: Path) -> None:
    class Cancelled(Exception):
        pass

    def progress(fraction: float, message: str | None) -> None:
        if fraction > 0.2:
            raise Cancelled

    layer = from_points("p", hard_points(200_000))
    with pytest.raises(Cancelled):
        write_points(tmp_path / "big.csv", [layer], progress=progress)
    assert list(tmp_path.iterdir()) == []
