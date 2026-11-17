"""Micromine DAT writer: read → write → read is lossless (bit-exact numbers), real files byte for byte."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from bf_core.formats.errors import FormatError
from bf_core.formats.micromine import Field, read_dat, write_dat
from bf_core.formats.table import Column, TableRead
from tests.formats.dat_builder import build, encode_bocu

REAL = Path(__file__).resolve().parents[3] / "tests" / "data" / "real"
CP = "ibm-5347_P100-1998"


def _same(a: TableRead, b: TableRead) -> None:
    assert a.names == b.names and a.rows == b.rows
    for x, y in zip(a.columns, b.columns, strict=True):
        assert x.kind == y.kind and x.source == y.source
        if x.kind == "number":
            assert x.values.tobytes() == y.values.tobytes(), x.name  # bit for bit, NaN and -0.0 included
        else:
            assert x.to_pylist() == y.to_pylist(), x.name
    ha, hb = a.info.header, b.info.header
    assert ha.fields == hb.fields and (ha.extended, ha.codepage, ha.encoding) == (hb.extended, hb.codepage, hb.encoding)
    assert (ha.title, ha.description, ha.xml) == (hb.title, hb.description, hb.xml)


def _hard_file(tmp_path: Path, codepage: str | None = CP, **kw: object) -> Path:
    enc = (lambda s: s.encode("cp1251")) if codepage != "BOCU-1" else None
    if enc is None:
        from bf_core.formats.bocu1 import encode as enc  # type: ignore[assignment]
    variables = [
        (enc("Скважина"), "C", 12, 0, enc("Номер скважины")),
        (enc("RUN_AU_CUT"), "N", 14, 8),
        (enc("AU"), "R", 8, 3, enc("Золото, г/т | Au")),
        (enc("F4"), "F", 4, 2),
        (enc("L4"), "L", 4, 0),
        (enc("S2"), "S", 2, 0),
        (enc("B1"), "B", 1, 0),
        (enc("JOIN"), "N", 6, 0),
        (enc("AU"), "R", 8, 3),  # a repeated name
    ]
    num = encode_bocu if codepage == "BOCU-1" else (lambda s: s)
    records = [
        [enc("С-1"), num("0.12345678"), 1.5, 0.25, -7, -3, 200, num("1"), 0.1 + 0.2],
        [b"", b"", math.nan, 1e10, 2**31 - 1, -(2**15), 0, num("  42  "), 5e-324],
        [enc("  Руда ёЁ"), num("-1e-3"), -0.0, -1.5, 0, 0, 255, num("-7"), 1.7976931348623157e308],
        [enc("x" * 12), num("1234567.5"), 466570.586, 3.0, 1, 1, 1, b"", -123456789.123456789],
    ]
    path = tmp_path / "hard.DAT"
    path.write_bytes(build(codepage, variables, records, title="Пробы &lt;2025&gt;", **kw))  # type: ignore[arg-type]
    return path


@pytest.mark.parametrize("kw", [{}, {"pages": 3}, {"standard": True}], ids=["extended", "8192+", "standard"])
def test_read_write_read_is_lossless(tmp_path: Path, kw: dict[str, object]) -> None:
    source = read_dat(_hard_file(tmp_path, None if kw.get("standard") else CP, **kw), encoding="cp1251")
    assert source.rows == 4 and len(source.columns) == 9
    out = tmp_path / "out.DAT"
    done = write_dat(out, source.columns, like=source.info.header)
    assert done.rows == 4 and done.warnings == [] and done.size == out.stat().st_size
    again = read_dat(out, encoding="cp1251")
    _same(source, again)
    assert again.info.header.fields[2].description == "Золото, г/т | Au"
    # The second generation is the same file byte for byte.
    out2 = tmp_path / "out2.DAT"
    write_dat(out2, again.columns, like=again.info.header)
    assert out2.read_bytes() == out.read_bytes()


def test_bocu1_round_trip(tmp_path: Path) -> None:
    source = read_dat(_hard_file(tmp_path, "BOCU-1"))
    assert source.info.encoding == "bocu-1" and source.column("Скважина").to_pylist()[2] == "  Руда ёЁ"
    out = tmp_path / "out.DAT"
    write_dat(out, source.columns, like=source.info.header)
    _same(source, read_dat(out))


@pytest.mark.parametrize("name", ["Без имени.STR", "Восточный Цители-Сопели.STR"])
def test_real_files_are_rewritten_byte_for_byte(tmp_path: Path, name: str) -> None:
    table = read_dat(REAL / name)
    out = tmp_path / "copy.STR"
    write_dat(out, table.columns, like=table.info.header)
    assert out.read_bytes() == (REAL / name).read_bytes()


def _columns() -> list[Column]:
    return [
        Column("DrillholeID", "text", pa.chunked_array([pa.array(["С-1", None, "Скв-22"])])),
        Column("DrillholeIDx", "text", pa.chunked_array([pa.array(["a"]), pa.array(["b", "c"])])),
        Column("From", "number", np.array([0.0, 1.5, np.nan])),
        Column("Au|ppm\n", "number", np.array([0.123, 1e-7, 12345.678])),
    ]


def test_fields_are_derived_from_columns_and_long_names_are_reported(tmp_path: Path) -> None:
    out = tmp_path / "new.DAT"
    done = write_dat(out, _columns(), title="Пробы", description="тест")
    assert [(f.name, f.kind, f.width) for f in done.fields] == [
        ("DrillholeI", "C", 6),
        ("Drillhole2", "C", 1),
        ("From", "R", 8),
        ("Au ppm", "R", 8),
    ]
    assert done.fields[2].decimals == 1 and done.fields[3].decimals == 7
    assert len(done.warnings) == 3 and all("DrillholeI" in w or "Au ppm" in w for w in done.warnings)
    table = read_dat(out)
    assert table.info.header.title == "Пробы" and table.info.header.description == "тест"
    assert table.info.header.codepage == CP and table.warnings == []
    assert table.column("DrillholeI").to_pylist() == ["С-1", None, "Скв-22"]
    assert table.column("From").values.tobytes() == np.array([0.0, 1.5, np.nan]).tobytes()
    assert table.column("Au ppm").values.tolist() == [0.123, 1e-7, 12345.678]
    data = out.read_bytes()
    assert (
        data[:42] == b"THIS IS MICROMINE EXTENDED DATA FILE!\x07\x1a\x01\r\n"
        and data[4096:4111] == b"4   VARIABLES\r\n"
    )

    standard = tmp_path / "std.DAT"
    write_dat(standard, _columns(), extended=False)
    assert standard.read_bytes().startswith(b"4   VARIABLES\r\nDrillholeIC  6  0\r\n")
    assert read_dat(standard).column("DrillholeI").to_pylist() == ["С-1", None, "Скв-22"]


def test_number_as_text_fields_parse_back_to_the_same_doubles(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    values = np.concatenate([rng.normal(size=500) * 10.0 ** rng.integers(-6, 7, 500), [0.1, 1e-7, np.nan, -0.0, 2.5]])
    fields = [Field("V", "N", 24, 3), Field("K", "N", 6, 0), Field("ID", "C", 6, 0)]
    whole = np.arange(len(values), dtype=np.float64)
    whole[7] = np.nan
    ids = Column("ID", "number", whole.copy())  # a number column written into a text field: 12.0 → "12"
    out = tmp_path / "n.DAT"
    write_dat(out, [Column("V", "number", values), Column("K", "number", whole), ids], fields=fields)
    table = read_dat(out)
    assert table.column("V").values.tobytes() == values.tobytes()
    assert table.column("K").values.tobytes() == whole.tobytes()
    assert table.column("ID").to_pylist()[:9] == ["0", "1", "2", "3", "4", "5", "6", None, "8"]
    row = out.read_bytes()[out.stat().st_size - 38 :]
    assert row == b"2.500".ljust(24) + b"504".ljust(6) + b"504".ljust(6) + b"\r\n"


def test_what_cannot_be_written_is_an_error_not_a_loss(tmp_path: Path) -> None:
    out = tmp_path / "bad.DAT"
    text = Column("T", "text", pa.chunked_array([pa.array(["日本語"])]))
    number = Column("N", "number", np.array([1.5, np.nan]))

    def code(columns: list[Column], **kw: object) -> str:
        with pytest.raises(FormatError) as info:
            write_dat(out, columns, **kw)  # type: ignore[arg-type]
        assert not out.exists() and not list(tmp_path.glob("*.part"))
        return info.value.code

    assert code([text]) == "unrepresentable"  # no such characters in Windows-1251
    assert write_dat(out, [text], codepage="utf-8").rows == 1 and read_dat(out).column("T").to_pylist() == ["日本語"]
    out.unlink()
    assert code([text], codepage="utf-8", fields=[Field("T", "C", 4, 0)]) == "unrepresentable"  # 9 bytes into 4
    assert code([number], fields=[Field("N", "L", 4, 0)]) == "unrepresentable"  # 1.5 and NaN as int32
    assert code([number], fields=[Field("N", "N", 2, 0)]) == "unrepresentable"  # "1.5" into 2 characters
    assert code([number], fields=[Field("N", "R", 4, 0)]) == "broken"
    assert code([number], fields=[Field("ДлинноеИмяПоля", "R", 8, 0)]) == "unrepresentable"
    assert code([number], fields=[]) == "broken"
    assert code([text], fields=[Field("T", "R", 8, 0)]) == "unrepresentable"
    assert code([number], codepage="ibm-0000") == "unknown_encoding"
    assert code([number, Column("M", "number", np.zeros(3))]) == "broken"  # columns of different lengths


def test_big_table_is_written_in_chunks_and_cancel_leaves_nothing(tmp_path: Path) -> None:
    rows = 300_000
    rng = np.random.default_rng(1)
    x = rng.normal(size=rows)
    x[::1000] = np.nan
    holes = pa.chunked_array([pa.array([f"Скв-{i % 500}" for i in range(rows)])])
    cols = [Column("HOLE", "text", holes), Column("X", "number", x), Column("J", "number", np.arange(rows) % 7.0)]
    fields = [Field("HOLE", "C", 8, 0), Field("X", "R", 8, 3), Field("J", "N", 3, 0)]
    out = tmp_path / "big.DAT"
    seen: list[float] = []
    done = write_dat(out, cols, fields=fields, progress=lambda f, m: seen.append(f))
    assert done.rows == rows and out.stat().st_size == 4096 + 15 + 3 * 19 + rows * 21
    assert len(seen) > 3 and seen == sorted(seen) and seen[-1] == 1.0
    table = read_dat(out)
    assert table.column("X").values.tobytes() == x.tobytes() and table.column("HOLE").values.equals(holes)
    assert table.column("J").values.tolist() == (np.arange(rows) % 7.0).tolist()

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None) -> None:
        if fraction > 0.4:
            raise Cancelled

    other = tmp_path / "cancelled.DAT"
    with pytest.raises(Cancelled):
        write_dat(other, cols, fields=fields, progress=cancel)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["big.DAT"]
