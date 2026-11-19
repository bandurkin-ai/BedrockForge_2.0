"""Surpac text string files: segments, END, descriptions, layers by string number, the byte-for-byte cycle,
conversions with Micromine STR and negative cases (ТЗ 9.6, 9.12; ADR 0011 §2).

Reference values are written in the test files by hand (Y before X, as Surpac stores them); coordinates of the
default writer are `repr` texts, so the tolerance is none: every double comes back bit for bit.
"""

from __future__ import annotations

import datetime as dt
import math
import time
from pathlib import Path

import numpy as np
import pytest

from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet
from bf_core.formats.str_micromine import read_str, write_str
from bf_core.formats.str_surpac import is_surpac, read_surpac, write_surpac

REAL = Path(__file__).resolve().parents[3] / "tests" / "data" / "real"
DAY = dt.date(2026, 11, 17)
AXIS = "0,           0.000,           0.000,           0.000,           0.000,           0.000,           0.000"
SURPAC = "\r\n".join(
    [
        "pit2026,17-Nov-26,pit design,ssi_styles:styles.ssi",
        AXIS,
        "1, 4585061.988, 466570.123, 100.000, борт, 1",
        "1, 4585061.988, 466580.123, 100.000,",
        "1, 4585071.988, 466580.123, 100.500,",
        "1, 4585071.988, 466570.123, 101.000,",
        "1, 4585061.988, 466570.123, 100.000,",
        "0, 0.000, 0.000, 0.000,",
        "2, 4585000.000, 466000.000, -35.750, разлом",
        "2, 4585001.250, 466002.500, -36.000, разлом",
        "0, 0.000, 0.000, 0.000,",
        "1, 4585100.000, 466100.000, 0.000,",
        "0, 0.000, 0.000, 0.000,",
        "0, 0.000, 0.000, 0.000, END",
        "",
    ]
)


def _write(tmp_path: Path, data: bytes | str, name: str = "t.str") -> Path:
    path = tmp_path / name
    path.write_bytes(data.encode("cp1251") if isinstance(data, str) else data)
    return path


def _error(path: Path) -> FormatError:
    with pytest.raises(FormatError) as info:
        read_surpac(path)
    err = info.value
    assert err.message and err.hint and set(err.as_dict()) == {"code", "message", "hint"}
    return err


def test_segments_layers_descriptions_and_same_bytes(tmp_path: Path) -> None:
    path = _write(tmp_path, SURPAC)
    read = read_surpac(path)
    assert read.format == "surpac_str" and read.records == 8 and not read.warnings
    header = read.header
    assert header.exact and header.descriptions == 2 and header.title.startswith("pit2026,")
    assert header.style.decimals == 3 and header.style.min_desc == 1 and header.style.newline == "\r\n"
    one, two = read.layers
    assert (one.name, one.line_count, np.diff(one.offsets).tolist(), one.closed.tolist()) == (
        "Стринг 1",
        2,
        [5, 1],
        [1, 0],
    )
    assert (two.name, two.line_count, two.closed.tolist()) == ("Стринг 2", 1, [0])
    assert one.seq.tolist() == [0, 1, 2, 3, 4, 7] and two.seq.tolist() == [5, 6]
    # Surpac stores Y before X.
    assert one.xyz[0].tolist() == [466570.123, 4585061.988, 100.0]
    assert two.xyz[1].tolist() == [466002.5, 4585001.25, -36.0]
    # Descriptions change along the square: they are per vertex; empty ones are null.
    assert one.attrs is None and one.vattrs.column_names == ["D1", "D2"]
    assert one.vattrs.column("D1").to_pylist() == ["борт", None, None, None, None, None]
    assert one.vattrs.column("D2").to_pylist()[:2] == ["1", None]
    assert two.vattrs.column("D1").to_pylist() == ["разлом", "разлом"]

    out = tmp_path / "copy.str"
    write_surpac(out, read.layers, like=header)
    assert out.read_bytes() == path.read_bytes()


def test_aligned_columns_and_lf_line_ends_come_back_the_same(tmp_path: Path) -> None:
    text = "\n".join(
        [
            "test,17-Nov-26,,",
            AXIS,
            "   1,  4585061.988,   466570.123,      100.000",
            "   1,  4585061.988,   466580.123,       -0.000",
            "0, 0.000, 0.000, 0.000",
            "   7,  4585000.000,   466000.000,        5.125",
            "0, 0.000, 0.000, 0.000",
            "0, 0.000, 0.000, 0.000, END",
        ]
    )  # no line end after END
    path = _write(tmp_path, text)
    read = read_surpac(path)
    style = read.header.style
    assert read.header.exact and (style.number_width, style.coord_width, style.decimals) == (4, 13, 3)
    assert style.newline == "\n" and read.header.end_newline == ""
    assert [ls.name for ls in read.layers] == ["Стринг 1", "Стринг 7"]
    assert math.copysign(1, read.layers[0].xyz[1, 2]) == -1  # −0.000 is kept as −0.0
    out = tmp_path / "copy.str"
    write_surpac(out, read.layers, like=read.header)
    assert out.read_bytes() == path.read_bytes()


def test_default_writer_cycle_is_bit_exact(tmp_path: Path) -> None:
    rng = np.random.default_rng(11)
    pts = rng.normal(size=(6, 3)) * [1e3, 1e3, 50] + [466570.123456789, 4585061.987654321, 300]
    ring = np.vstack([pts[:4], pts[:1]])
    first = LineSet(
        "Стринг 5",
        np.vstack([ring, pts[4:]]),
        offsets=[0, 5, 7],
        closed=[1, 0],
        attrs={"Участок": ["Цители, №2", None]},
        vattrs={"D1": ["a", None, "b", None, "a", "c", "c"]},
    )
    second = LineSet("TS-1", pts[:3] + 1 / 3, offsets=[0, 3], closed=[0])
    path = tmp_path / "new.str"
    written = write_surpac(path, [first, second], title="BedrockForge", date=DAY)
    assert written.records == 10 and written.lines == 3
    assert any("«TS-1» → 1" in w for w in written.warnings)
    assert any("«Участок» → D2" in w for w in written.warnings) and any("запятую" in w for w in written.warnings)
    lines = path.read_bytes().decode("cp1251").split("\r\n")
    assert lines[0] == "BedrockForge,17-Nov-26,," and lines[-2:] == ["0, 0.000, 0.000, 0.000, END", ""]
    assert lines[2] == f"5, {float(ring[0, 1])!r}, {float(ring[0, 0])!r}, {float(ring[0, 2])!r}, a, Цители; №2"

    read = read_surpac(path)
    assert read.header.exact and [ls.name for ls in read.layers] == ["Стринг 5", "Стринг 1"]
    for got, want in zip(read.layers, [first, second], strict=True):
        assert got.xyz.tobytes() == want.xyz.tobytes() and got.closed.tolist() == want.closed.tolist()
    assert read.layers[0].attrs.column("D2").to_pylist() == ["Цители; №2", None]  # constant along each line
    assert read.layers[0].vattrs.column("D1").to_pylist() == ["a", None, "b", None, "a", "c", "c"]
    again = tmp_path / "again.str"
    write_surpac(again, read.layers, like=read.header)
    assert again.read_bytes() == path.read_bytes()


def test_irregular_file_is_read_and_rewritten_with_the_same_values(tmp_path: Path) -> None:
    text = SURPAC.replace("1, 4585061.988, 466580.123, 100.000,", "1,4585061.98800,466580.123,1e2")
    text = text.replace("0, 0.000, 0.000, 0.000, END\r\n", "0, 0.000, 0.000, 0.000, END\r\nnotes after the end\r\n")
    read = read_surpac(_write(tmp_path, text))
    assert not read.header.exact and read.header.tail == b"notes after the end\r\n"
    assert any("После записи END" in w for w in read.warnings)
    out = tmp_path / "out.str"
    write_surpac(out, read.layers, like=read.header)
    back = read_surpac(out)
    assert back.header.exact and out.read_bytes().endswith(b"END\r\nnotes after the end\r\n")
    for got, want in zip(back.layers, read.layers, strict=True):
        assert got.xyz.tobytes() == want.xyz.tobytes()


def test_conversions_with_micromine_keep_every_double(tmp_path: Path) -> None:
    for n, name in enumerate(["Без имени.STR", "Восточный Цители-Сопели.STR"]):
        mm = read_str(REAL / name)
        surpac = tmp_path / f"s{n}.str"
        write_surpac(surpac, mm.layers, date=DAY)
        back = read_surpac(surpac)
        assert back.header.exact and len(back.layers) == len(mm.layers)
        for got, want in zip(back.layers, mm.layers, strict=True):
            assert got.xyz.tobytes() == want.xyz.tobytes() and got.closed.tolist() == want.closed.tolist()
        again = tmp_path / f"m{n}.STR"
        write_str(again, back.layers)
        mm2 = read_str(again)
        assert [ls.xyz.tobytes() for ls in mm2.layers] == [ls.xyz.tobytes() for ls in mm.layers]
        assert [ls.name for ls in mm2.layers] == [ls.name for ls in back.layers]


def test_bad_values_are_reported_not_turned_into_zero(tmp_path: Path) -> None:
    text = SURPAC.replace("1, 4585061.988, 466580.123, 100.000,", "1, 4585061.988, 466580.123, н/д,")
    text = text.replace("2, 4585001.250, 466002.500, -36.000, разлом", "2, 4585001.250, ?, -36.000, разлом")
    text = text.replace("0, 0.000, 0.000, 0.000,\r\n1,", "строка не по формату\r\n0, 0.000, 0.000, 0.000,\r\n1,", 1)
    read = read_surpac(_write(tmp_path, text))
    one, two = read.layers
    assert math.isnan(one.xyz[1, 2]) and two.point_count == 1  # Z stays empty; a vertex without X is left out
    assert not read.header.exact
    joined = " | ".join(read.warnings)
    assert "2 координат не являются числами" in joined and "1 вершин без координаты X или Y" in joined
    assert "1 строк не читаются как записи Surpac (например, строка 11)" in joined


def test_rejects_foreign_truncated_and_bomb_files(tmp_path: Path) -> None:
    assert _error(_write(tmp_path, b"")).code == "empty_file"
    assert _error(_write(tmp_path, "pit,17-Nov-26,,\r\n")).code == "truncated"
    assert _error(_write(tmp_path, "pit,,,\r\n1, 2, 3, 4\r\n")).code == "not_surpac"
    assert _error(_write(tmp_path, (REAL / "Без имени.STR").read_bytes())).code == "not_surpac"
    assert _error(_write(tmp_path, b"\x01\x00\x00\x00binary" * 50)).code == "unsupported"
    assert _error(_write(tmp_path, SURPAC.encode("utf-16"))).code == "unsupported"
    cut = SURPAC.split("0, 0.000, 0.000, 0.000, END")[0]
    assert _error(_write(tmp_path, cut)).code == "truncated"
    assert _error(_write(tmp_path, cut[: len(cut) // 2])).code == "truncated"
    assert _error(_write(tmp_path, "pit,,,\r\n" + AXIS + "\r\n0, 0.000, 0.000, 0.000, END\r\n")).code == "no_lines"
    many = "1, 1.0, 2.0, 3.0" + ", d" * 101
    assert _error(_write(tmp_path, SURPAC.replace("1, 4585100.000, 466100.000, 0.000,", many))).code == "too_large"
    long = "pit,,,\r\n" + AXIS + "\r\n1, 1.0, 2.0, 3.0, " + "x" * (2 << 20)
    assert _error(_write(tmp_path, long)).code == "too_large"
    with pytest.raises(FormatError) as info:
        layer = LineSet("A", [[1.0, 2.0, 3.0]], [0, 1], [0], vattrs={"D1": ["Ж"]})
        write_surpac(tmp_path / "x.str", [layer], encoding="ascii")
    assert info.value.code == "unrepresentable"
    assert not (tmp_path / "x.str").exists() and not (tmp_path / "x.str.part").exists()


def test_is_surpac_by_content() -> None:
    assert is_surpac(SURPAC.encode("cp1251")) and is_surpac(b"\xef\xbb\xbf" + SURPAC.encode("utf-8"))
    assert not is_surpac((REAL / "Без имени.STR").read_bytes()[:4096])
    assert not is_surpac(b"EAST,NORTH\r\n1,2\r\n")


def test_bom_utf8_file_is_read_and_written_back(tmp_path: Path) -> None:
    path = _write(tmp_path, b"\xef\xbb\xbf" + SURPAC.encode("utf-8"))
    read = read_surpac(path)
    assert (
        read.encoding == "utf-8" and read.header.bom and read.layers[1].vattrs.column("D1").to_pylist()[0] == "разлом"
    )
    out = tmp_path / "copy.str"
    write_surpac(out, read.layers, like=read.header)
    assert out.read_bytes() == path.read_bytes()


def test_progress_cancel_and_speed(tmp_path: Path) -> None:
    n = 300_000
    xyz = np.random.default_rng(5).normal(size=(n, 3)) * 1e3 + [466570.0, 4585061.0, 300.0]
    layers = [
        LineSet(f"Стринг {k + 1}", xyz[k::3], np.arange(0, n // 3 + 1, 100), np.zeros(n // 300)) for k in range(3)
    ]
    path = tmp_path / "big.str"
    seen: list[float] = []
    started = time.perf_counter()
    write_surpac(path, layers, date=DAY, progress=lambda f, m: seen.append(f))
    write_seconds = time.perf_counter() - started
    assert len(seen) > 3 and seen == sorted(seen) and seen[-1] == 1.0
    seen.clear()
    started = time.perf_counter()
    read = read_surpac(path, progress=lambda f, m: seen.append(f), chunk_bytes=1 << 20)
    read_seconds = time.perf_counter() - started
    assert read.point_count == n and read.line_count == n // 100 and read.header.exact
    assert len(seen) > 8 and seen == sorted(seen) and seen[-1] == 1.0
    assert (
        np.concatenate([ls.xyz for ls in read.layers]).tobytes() == np.concatenate([ls.xyz for ls in layers]).tobytes()
    )
    assert read_seconds < 15 and write_seconds < 15  # 0,3 млн вершин; budgets of ADR 0011 are for 1 млн in bench

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None) -> None:
        if fraction > 0.3:
            raise Cancelled

    with pytest.raises(Cancelled):
        read_surpac(path, progress=cancel, chunk_bytes=1 << 20)
    with pytest.raises(Cancelled):
        write_surpac(tmp_path / "cancelled.str", layers, progress=cancel)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["big.str"]
