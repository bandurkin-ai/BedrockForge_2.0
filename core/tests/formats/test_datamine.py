"""Datamine tables (`.dm`, `.fbm`): pages, the dictionary of fields, single and extended precision, text and number
fields, implicit fields, absent data, negative cases, speed and memory (ТЗ 7.35 Д-13, 9.12; ADR 0009, ADR 0013 §2).

References:
  * the block model of the owner (`tests/data/real/Для BF.zip`: `.dm`, `.fbm` and the Micromine `.DAT` of the same
    model) with the values of the owner's analysis (`blockmodels-dm-fbm-dat.md`); the `.DAT` is read by the reader
    of Э3 and gives the same blocks — an independent reference. Tolerance: none (doubles, bit for bit); the rounded
    values quoted in the analysis are compared within their last digit;
  * synthetic files built byte by byte from the description of the format (`dm_builder`), not by the reader.
"""

from __future__ import annotations

import math
import shutil
import time
import tracemalloc
import zipfile
from pathlib import Path

import numpy as np
import pytest

from bf_core.formats.datamine import DmHeader, iter_chunks, read_dm, read_header, table_kind
from bf_core.formats.errors import FormatError
from bf_core.formats.micromine import read_dat
from tests.formats.dm_builder import ABSENT, TRACE, F, build

REAL = Path(__file__).resolve().parents[3] / "tests" / "data" / "real" / "Для BF.zip"
STORED = ["IJK", "XC", "YC", "ZC", "XINC", "YINC", "ZINC", "Fe_m_pc", "Fe_o_pc", "1 - Insi", "1 Filter"]
ORIGIN = {"XMORIG": 23436265.92, "YMORIG": 5988122.53, "ZMORIG": -157.14}
ROWS = 169_070


@pytest.fixture(scope="module")
def model(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """The three files of the owner's block model, by extension."""
    if not REAL.is_file():
        pytest.skip("tests/data/real/Для BF.zip is not here")
    folder = tmp_path_factory.mktemp("datamine")
    out: dict[str, Path] = {}
    with zipfile.ZipFile(REAL) as archive:
        for info in archive.infolist():
            suffix = Path(info.filename).suffix.lower()
            out[suffix] = folder / f"model{suffix}"
            with archive.open(info) as src, out[suffix].open("wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    return out


def bits(values: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(values, dtype=np.float64).view(np.uint64)


def error(path: Path, **options: object) -> FormatError:
    with pytest.raises(FormatError) as info:
        read_dm(path, **options)  # type: ignore[arg-type]
    err = info.value
    assert err.message and err.hint and set(err.as_dict()) == {"code", "message", "hint"}
    return err


def save(tmp_path: Path, data: bytes, name: str = "таблица.dm") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def ijk(table: dict[str, np.ndarray], header: DmHeader) -> np.ndarray:
    """IJK by the formula of the owner's analysis (§1.5): Z runs fastest."""
    c = header.constants
    i, j, k = (
        np.floor((table[f"{a}C"] - c[f"{a}MORIG"]) / table[f"{a}INC"])  # type: ignore[operator]
        for a in "XYZ"
    )
    return (i * c["NY"] + j) * c["NZ"] + k  # type: ignore[no-any-return, operator]


# ---------------------------------------------------------------- the block model of the owner


def test_real_dm_header(model: dict[str, Path]) -> None:
    h = read_header(model[".dm"])
    assert (h.precision, h.word, h.page, h.layout) == ("extended", 8, 4096, "dm")
    assert len(h.fields) == 20 and h.records == ROWS
    assert [f.name for f in h.stored] == STORED and h.record_words == 11 and h.per_page == 46
    assert all(f.kind == "N" for f in h.fields)
    same = {"X0": ORIGIN["XMORIG"], "Y0": ORIGIN["YMORIG"], "Z0": ORIGIN["ZMORIG"]}
    assert h.constants == {**ORIGIN, **same, "NX": 443.0, "NY": 558.0, "NZ": 102.0}
    assert h.names == [*STORED, *ORIGIN, "X0", "Y0", "Z0", "NX", "NY", "NZ"]
    assert (h.name, h.owner, h.encoding) == ("блочная", "SYSTEM", "cp1251")
    assert h.description.split() == "FILE EXPORTED BY MICROMINE".split()
    assert h.field("xinc").default == 10.0 and h.field("XC").default == 0.0  # the parent cell is the default
    assert h.kind == "block_model" and h.warnings == []


def test_real_dm_values(model: dict[str, Path]) -> None:
    read = read_dm(model[".dm"])
    assert read.rows == ROWS and read.names == STORED and read.info.format == "dm" and read.warnings == []
    assert all(c.kind == "number" and len(c) == ROWS for c in read.columns)
    t = {c.name: c.values for c in read.columns}
    # the first block of the analysis (§4): centre exactly, grades as quoted there
    assert [t[n][0] for n in ("IJK", "XC", "YC", "ZC")] == [1081.0, 23436270.92, 5988227.53, 457.86]
    assert t["Fe_m_pc"][0] == pytest.approx(9.4867, abs=5e-5)
    assert t["Fe_o_pc"][0] == pytest.approx(13.473, abs=5e-4)
    assert t["1 - Insi"][0] == pytest.approx(0.5024, abs=5e-5)
    assert (t["Fe_m_pc"][0], t["Fe_o_pc"][0]) == (9.486722407325884, 13.472983828333746)  # the doubles of the file
    assert (t["XC"].min(), t["XC"].max()) == (23436270.92, 23440690.92)
    assert (t["YC"].min(), t["YC"].max()) == (5988127.53, 5993697.53)
    assert (t["ZC"].min(), t["ZC"].max()) == (-152.14, 857.86)
    for name in ("XINC", "YINC", "ZINC"):
        assert (t[name] == 10.0).all()
    assert (t["1 Filter"] == 1.0).all()
    assert (t["1 - Insi"].min(), t["1 - Insi"].max()) == (pytest.approx(0.039, abs=5e-4), 1.0)
    # −99 is the empty value of this model, not of the format: it stays a number (the importer decides, Э17)
    assert int((t["Fe_m_pc"] == -99.0).sum()) == 90 and int((t["Fe_o_pc"] == -99.0).sum()) == 90
    assert not any(np.isnan(v).any() for v in t.values())
    header = read.info.header
    assert (np.diff(t["IJK"]) > 0).all() and (ijk(t, header) == t["IJK"]).all()  # every record is where it must be


def test_real_fbm_gives_the_same_blocks(model: dict[str, Path]) -> None:
    h = read_header(model[".fbm"])
    assert (h.precision, h.layout, len(h.fields), h.records) == ("extended", "fbm", 17, ROWS)
    assert h.constants == {**ORIGIN, "NX": 445.0, "NY": 560.0, "NZ": 104.0} and h.kind == "block_model"
    fbm, dm = read_dm(model[".fbm"]), read_dm(model[".dm"])
    assert fbm.names == dm.names == STORED and fbm.rows == ROWS
    for name in STORED[1:]:
        assert (bits(fbm.column(name).values) == bits(dm.column(name).values)).all(), name
    t = {c.name: c.values for c in fbm.columns}
    assert t["IJK"][0] == 1101.0 and (ijk(t, h) == t["IJK"]).all()  # the grid of the .fbm is wider: another IJK


def test_real_micromine_dat_gives_the_same_blocks(model: dict[str, Path]) -> None:
    dat, dm = read_dat(model[".dat"]), read_dm(model[".dm"])
    assert dat.rows == dm.rows == ROWS and len(dat.columns) == 10
    for mine, theirs in zip(dm.columns[1:], dat.columns, strict=True):  # XC ↔ EAST … «1 Filter»
        assert (bits(mine.values) == bits(theirs.values)).all(), (mine.name, theirs.name)


def test_real_speed_and_memory(model: dict[str, Path]) -> None:
    read_header(model[".dm"])
    started = time.perf_counter()
    read = read_dm(model[".dm"])
    seconds = time.perf_counter() - started
    result = sum(c.values.nbytes for c in read.columns)
    del read
    tracemalloc.start()
    again = read_dm(model[".dm"])
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    print(f"\n.dm 15 МБ, {ROWS} записей × 11 полей: {seconds:.3f} с, пик памяти {peak / 2**20:.1f} МБ")
    assert again.rows == ROWS and seconds < 5.0
    assert peak < result + (12 << 20)  # the result and the columns of one chunk: the file is mapped, not loaded


# ---------------------------------------------------------------- synthetic tables

HOLES = [
    F("BHID", "A", 12),
    F("FROM"),
    F("TO"),
    F("AU"),
    F("ПОРОДА", "A", 8),
    F("ZONE", "A", 4),
    F("NX", default=443.0, implicit=True),
    F("ROCK", "A", 8, "ГРАНИТ", implicit=True),
    F("DENS", default=ABSENT, implicit=True),
    F("NOTE", "A", 8, "", implicit=True),
]


def hole_rows(count: int) -> list[list[float | str]]:
    rows: list[list[float | str]] = []
    for i in range(count):
        au = ABSENT if i % 7 == 3 else TRACE if i % 11 == 5 else 0.01 * i + 1 / 3
        rock = "" if i % 5 == 2 else ("ГРАНИТ", "СЛАНЕЦ", "КВАРЦИТЫ")[i % 3]
        rows.append([f"СКВ-{i % 40:03d}/А", i * 1.5, i * 1.5 + 1.5, au, rock, "" if i % 9 == 0 else f"Z{i % 3}"])
    return rows


def check_holes(path: Path, rows: list[list[float | str]], word: int) -> None:
    read = read_dm(path, chunk_rows=64)
    assert read.rows == len(rows) and read.names == ["BHID", "FROM", "TO", "AU", "ПОРОДА", "ZONE"]
    assert [c.kind for c in read.columns] == ["text", "number", "number", "number", "text", "text"]
    cast = np.float64 if word == 8 else np.float32  # a float32 of the file becomes the double of the same value
    for k in (1, 2):
        want = np.array([cast(r[k]) for r in rows], dtype=np.float64)
        assert (bits(read.columns[k].values) == bits(want)).all()
    au = read.column("AU").values
    for got, row in zip(au.tolist(), rows, strict=True):
        if row[3] == ABSENT:
            assert math.isnan(got)  # absent data is empty, never 0 and never −1e30
        else:
            assert got == float(cast(row[3])) and got != 0.0
    assert read.column("BHID").to_pylist() == [r[0] for r in rows]
    assert read.column("ПОРОДА").to_pylist() == [r[4] or None for r in rows]  # a blank text is empty
    assert read.column("ZONE").to_pylist() == [r[5] or None for r in rows]
    h = read.info.header
    assert h.record_words == 9 and [f.name for f in h.stored] == read.names
    assert [(f.name, f.kind, f.length, f.stored) for f in h.fields[4:8]] == [
        ("ПОРОДА", "A", 8, True),
        ("ZONE", "A", 4, True),
        ("NX", "N", 0, False),
        ("ROCK", "A", 8, False),
    ]
    constants = h.constants
    assert constants["NX"] == 443.0 and constants["ROCK"] == "ГРАНИТ" and constants["NOTE"] is None
    assert math.isnan(constants["DENS"])  # type: ignore[arg-type]
    traces = sum(1 for r in rows if r[3] == TRACE)
    assert any("AU" in w and str(traces) in w and "1e-30" in w for w in read.warnings), read.warnings


def test_extended_text_numbers_constants_absent(tmp_path: Path) -> None:
    rows = hole_rows(300)  # 56 records of 9 words to a page: 6 pages, the last one is not full
    path = save(tmp_path, build(HOLES, rows, name="СКВАЖИНЫ", owner="ГЕОЛОГ", description="Пробы"))
    h = read_header(path)
    assert (h.precision, h.page, h.records, h.per_page) == ("extended", 4096, 300, 56)
    assert (h.name, h.owner, h.description, h.kind) == ("СКВАЖИНЫ", "ГЕОЛОГ", "Пробы", "table")
    assert path.stat().st_size == 7 * 4096
    check_holes(path, rows, 8)
    assert not any("float32" in w for w in read_dm(path).warnings)


def test_single_precision(tmp_path: Path) -> None:
    rows = hole_rows(300)
    path = save(tmp_path, build(HOLES, rows, word=4))
    h = read_header(path)
    assert (h.precision, h.word, h.page, h.records) == ("single", 4, 2048, 300)
    assert path.stat().st_size == 7 * 2048
    check_holes(path, rows, 4)
    assert any("float32" in w for w in h.warnings)  # the precision of the file is told to the user


@pytest.mark.parametrize("word", [8, 4])
def test_fbm_layout(tmp_path: Path, word: int) -> None:
    rows = hole_rows(120)
    path = save(tmp_path, build(HOLES, rows, word=word, layout="fbm"), "модель.fbm")
    assert read_header(path).layout == "fbm"
    check_holes(path, rows, word)


@pytest.mark.parametrize("stored", [1, 2, 3, 4, 5, 7, 10, 11])
@pytest.mark.parametrize("capacity", [512, 508])
def test_page_capacity(tmp_path: Path, stored: int, capacity: int) -> None:
    """Records fill 512 words of a page, or 508 with four spare ones: both are read (the sample cannot tell)."""
    fields = [F(f"V{k}") for k in range(stored)]
    count = 3 * (512 // stored) + 5
    rows: list[list[float | str]] = [[float(i * stored + k + 1) for k in range(stored)] for i in range(count)]
    path = save(tmp_path, build(fields, rows, capacity=capacity))
    read = read_dm(path)
    assert read.rows == count and read.info.header.per_page == capacity // stored
    got = np.column_stack([c.values for c in read.columns])
    assert (got == np.array(rows)).all()


@pytest.mark.parametrize("mode", ["stream", "whole"])
def test_fields_over_several_header_pages(tmp_path: Path, mode: str) -> None:
    fields = [F(f"F{k:03d}") for k in range(150)] + [F("NAME", "A", 16), F("K", default=7.0, implicit=True)]
    rows: list[list[float | str]] = [
        [*(float(i * 1000 + k) for k in range(150)), f"имя {i:02d} длинное"] for i in range(9)
    ]
    path = save(tmp_path, build(fields, rows, header=mode))
    read = read_dm(path)
    h = read.info.header
    assert len(h.fields) == 152 and h.record_words == 154 and h.per_page == 3 and h.data_page == 3 and read.rows == 9
    assert (np.column_stack([c.values for c in read.columns[:150]]) == np.array([r[:150] for r in rows])).all()
    assert read.column("NAME").to_pylist() == [r[150] for r in rows] and h.constants == {"K": 7.0}
    assert any("нескольк" in w for w in read.warnings)  # read by the description only: the user is told


def test_fields_selection_chunks_and_limit(tmp_path: Path) -> None:
    rows = hole_rows(300)
    path = save(tmp_path, build(HOLES, rows))
    whole = read_dm(path)
    part = read_dm(path, fields=("au", "BHID", "NX", "ROCK"), chunk_rows=50)
    assert part.names == ["AU", "BHID", "NX", "ROCK"] and part.rows == 300
    assert (bits(part.column("AU").values) == bits(whole.column("AU").values)).all()
    assert part.column("BHID").to_pylist() == whole.column("BHID").to_pylist()
    assert (part.column("NX").values == 443.0).all() and len(part.column("NX")) == 300  # an implicit field, asked for
    assert part.column("ROCK").to_pylist() == ["ГРАНИТ"] * 300
    with pytest.raises(KeyError):
        read_dm(path, fields=("AU", "нет такого"))
    first = read_dm(path, max_rows=70)
    assert first.rows == 70 and first.column("BHID").to_pylist() == [r[0] for r in rows[:70]]
    assert read_dm(path, max_rows=0).rows == 0
    starts, total = [], 0
    for chunk in iter_chunks(path, fields=("FROM", "ZONE"), chunk_rows=100):
        starts.append(chunk.start)
        assert set(chunk.columns) == {"FROM", "ZONE"} and len(chunk.columns["FROM"]) == chunk.rows
        assert chunk.columns["FROM"].tolist() == [r[1] for r in rows[chunk.start : chunk.start + chunk.rows]]
        total += chunk.rows
    assert total == 300 and starts == sorted(starts) and starts[0] == 0 and len(starts) >= 3


@pytest.mark.windows
def test_progress_and_cancel(tmp_path: Path) -> None:
    path = save(tmp_path, build(HOLES, hole_rows(2000)), "holes_cancel.dm")
    seen: list[float] = []
    read_dm(path, lambda fraction, message=None: seen.append(fraction), chunk_rows=128)
    assert seen[0] == 0.0 and seen[-1] == 1.0 and seen == sorted(seen) and len(seen) > 10

    class Cancelled(Exception):
        pass

    def tick(fraction: float, message: str | None = None) -> None:
        if fraction > 0.3:
            raise Cancelled

    with pytest.raises(Cancelled) as caught:
        read_dm(path, tick, chunk_rows=128)
    # the file is let go at once, not when the traceback of the cancellation dies: Windows cannot delete or
    # replace a file that is still mapped
    maps = Path("/proc/self/maps")
    assert caught.traceback and (
        not maps.exists() or path.name not in maps.read_text(encoding="utf-8", errors="replace")
    )
    path.unlink()
    assert not path.exists()


def test_kinds(tmp_path: Path) -> None:
    assert table_kind(["TRIANGLE", "PID1", "PID2", "PID3", "COLOUR"]) == "triangles"
    assert table_kind(["pid", "xp", "yp", "zp"]) == "points"
    assert table_kind(["XP", "YP", "ZP", "PTN", "PVALUE", "COLOUR"]) == "strings"
    assert table_kind(["IJK", "XC", "YC", "ZC", "XINC", "YINC", "ZINC"]) == "block_model"
    assert table_kind(["BHID", "FROM", "TO", "AU"]) == "table"
    path = save(tmp_path, build([F("XP"), F("YP"), F("ZP"), F("PID")], [[1.0, 2.0, 3.0, 1.0]]), "тело_pt.dm")
    assert read_header(path).kind == "points"


def test_empty_table_and_repeated_names(tmp_path: Path) -> None:
    none = read_dm(save(tmp_path, build(HOLES, [])))
    assert none.rows == 0 and [len(c) for c in none.columns] == [0] * 6 and none.info.header.records == 0
    path = save(tmp_path, build([F("AU"), F("au"), F("AU", "A", 8)], [[1.0, 2.0, "x"]]), "повтор.dm")
    read = read_dm(path)
    assert read.names == ["AU", "au_2", "AU_3"] and read.columns[1].values.tolist() == [2.0]
    assert sum("повторяется" in w for w in read.warnings) == 2


def test_data_after_the_last_page_is_reported(tmp_path: Path) -> None:
    rows = hole_rows(60)
    path = save(tmp_path, build(HOLES, rows) + b"\x01" * 4096)
    read = read_dm(path)
    assert read.rows == 60 and any("после последней страницы" in w for w in read.warnings)


# ---------------------------------------------------------------- negative cases (ТЗ 9.12)


def test_empty_file(tmp_path: Path) -> None:
    assert error(save(tmp_path, b"")).code == "empty_file"


@pytest.mark.parametrize(
    "data",
    [
        b"x" * 100,
        bytes(range(256)) * 64,
        b"XC,YC,ZC\r\n1,2,3\r\n" * 600,
        b"\x00" * 8192,
        b"THIS IS MICROMINE EXTENDED DATA FILE!\x07\x1a\x01\r\n".ljust(8192, b"\x00"),
    ],
    ids=["short", "binary", "csv", "zeros", "micromine"],
)
def test_not_datamine(tmp_path: Path, data: bytes) -> None:
    assert error(save(tmp_path, data)).code == "not_datamine"


@pytest.mark.parametrize("word", [8, 4])
def test_truncated(tmp_path: Path, word: int) -> None:
    data = build(HOLES, hole_rows(300), word=word)
    page = 512 * word
    err = error(save(tmp_path, data[: 3 * page + 100]))
    assert err.code == "truncated" and "300" in err.message  # says how many records the header promises
    assert error(save(tmp_path, data[: page - 8], "заголовок.dm")).code in ("truncated", "not_datamine")
    whole = read_dm(save(tmp_path, data[: 6 * page + 20 * 9 * word], "без добивки.dm"))  # cut after the last record
    assert whole.rows == 300 and whole.column("BHID").to_pylist()[-1] == "СКВ-019/А"


def test_header_of_many_fields_cut(tmp_path: Path) -> None:
    data = build([F(f"F{k:03d}") for k in range(150)], [[float(k) for k in range(150)]])
    assert error(save(tmp_path, data[:4096])).code == "truncated"


def test_broken_dictionary(tmp_path: Path) -> None:
    err = error(save(tmp_path, build(HOLES, hole_rows(10), swn_shift=1)))  # stored words 2…10: no word 1
    assert err.code == "broken"
    assert error(save(tmp_path, build([F("AU", "X")], [[1.0]]), "тип.dm")).code == "not_datamine"


def test_bombs(tmp_path: Path) -> None:
    import struct

    data = bytearray(build(HOLES, hole_rows(10)))
    huge = bytearray(data)
    huge[26 * 8 : 27 * 8] = struct.pack("<d", 2_000_000_000.0)  # the last page: 8 ТБ of pages in a file of 8 КБ
    err = error(save(tmp_path, bytes(huge)))
    assert err.code == "truncated"
    many = bytearray(data)
    many[25 * 8 : 26 * 8] = struct.pack("<d", 1.0e9)  # a milliard of fields
    assert error(save(tmp_path, bytes(many), "поля.dm")).code == "not_datamine"
    wide = bytearray(data)
    wide[27 * 8 : 28 * 8] = struct.pack("<d", 5000.0)  # more records on the last page than a page holds
    assert error(save(tmp_path, bytes(wide), "записи.dm")).code in ("broken", "not_datamine")
