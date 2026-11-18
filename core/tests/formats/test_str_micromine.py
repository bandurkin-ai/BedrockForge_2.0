"""Micromine STR and `.PIT`: lines from records, byte-for-byte rewriting, negative cases (ТЗ 9.3, 9.12; ADR 0011).

Reference values: the real files of `tests/data/real` are checked against `read_dat` (the table reader of Э3,
an independent path) — every vertex bit for bit, in the order of the file; synthetic files come from
`dat_builder` (independent of the writer). Tolerance: none (coordinates are binary doubles).
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
import pytest

from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet
from bf_core.formats.micromine import read_dat
from bf_core.formats.str_micromine import read_str, write_str
from tests.formats.dat_builder import build

REAL = Path(__file__).resolve().parents[3] / "tests" / "data" / "real"
CP = "ibm-5347_P100-1998"
STR_FIELDS = [(b"EAST", "R", 8, 6), (b"NORTH", "R", 8, 6), (b"RL", "R", 8, 6), (b"JOIN", "N", 12, 0)]
STR_FIELDS += [(b"STRING", "C", 20, 0)]
E0, N0 = 466570.123456789, 4585061.987654321


def _write(tmp_path: Path, data: bytes, name: str = "t.STR") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _error(path: Path) -> FormatError:
    with pytest.raises(FormatError) as info:
        read_str(path)
    err = info.value
    assert err.message and err.hint and set(err.as_dict()) == {"code", "message", "hint"}
    assert any("а" <= ch <= "я" for ch in err.message.lower())  # Russian text for the user
    return err


# ---------------------------------------------------------------- real files


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Без имени.STR", [("Без кода", [2, 2, 8], [0, 0, 1])]),
        (
            "Восточный Цители-Сопели.STR",
            [("TS-1", [8], [1]), ("TS-2", [6], [1]), ("TS-5", [9], [1]), ("TS-3", [7], [1]), ("TS-4", [7], [1])],
        ),
    ],
)
def test_real_files_lines_and_byte_for_byte(tmp_path: Path, name: str, expected: list[tuple]) -> None:  # type: ignore[type-arg]
    source = REAL / name
    read = read_str(source)
    assert read.format == "micromine_str" and read.encoding == "cp1251" and not read.warnings
    got = [(ls.name, np.diff(ls.offsets).tolist(), ls.closed.tolist()) for ls in read.layers]
    assert got == expected
    # Every vertex as the table reader sees it, bit for bit, in the order of the file.
    table = read_dat(source)
    ref = np.column_stack([table.column(c).values for c in ("EAST", "NORTH", "RL")])
    seq = np.concatenate([ls.seq for ls in read.layers])
    xyz = np.concatenate([ls.xyz for ls in read.layers])
    assert sorted(seq.tolist()) == list(range(table.rows)) and xyz.tobytes() == ref[seq].tobytes()
    joins = np.concatenate([ls.attrs.column("JOIN").to_numpy() for ls in read.layers])
    assert joins.tolist() == sorted(set(table.column("JOIN").values.tolist()))

    same = tmp_path / "like.STR"
    written = write_str(same, read.layers, like=read.header)
    assert same.read_bytes() == source.read_bytes() and not written.warnings
    # Without the source header the layout is Micromine's own: these files come out the same as well.
    fresh = tmp_path / "fresh.STR"
    write_str(fresh, read.layers)
    assert fresh.read_bytes() == source.read_bytes()


# ---------------------------------------------------------------- synthetic files


def _records() -> list[list]:  # type: ignore[type-arg]
    """Layer A: a closed square (JOIN 1) and a point (JOIN 3); layer B in between (JOIN 2); the Участок field
    is constant along each line, Road changes along the square."""
    sq = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    out = [[E0 + x, N0 + y, 100.0 + (i % 4) / 8, "1", "A", "Восток", str(i % 2)] for i, (x, y) in enumerate(sq)]
    out += [[E0 + 50 + i / 3, N0 - 1 / 7, math.nan, "2", "B", "Сопели №2", "1"] for i in range(3)]
    out += [[E0 - 5.5, N0 + 5.25, -2.5, "3", "A", "", ""]]
    return out


def _pit(tmp_path: Path, suffix: str = ".PIT") -> Path:
    fields = [*STR_FIELDS, ("Участок".encode("cp1251"), "C", 16, 0), (b"Road", "N", 2, 0)]
    rows = [[*r[:6], r[6]] for r in _records()]
    rows = [[*r[:5], r[5].encode("cp1251"), r[6]] for r in rows]
    return _write(tmp_path, build(CP, fields, rows), "pit" + suffix)


def test_pit_lines_attributes_per_line_and_per_vertex(tmp_path: Path) -> None:
    path = _pit(tmp_path)
    read = read_str(path)
    assert read.format == "micromine_pit" and read.records == 9 and not read.warnings
    a, b = read.layers
    assert (a.name, a.line_count, a.point_count, a.closed.tolist()) == ("A", 2, 6, [1, 0])
    assert (b.name, b.line_count, b.closed.tolist()) == ("B", 1, [0])
    assert a.seq.tolist() == [0, 1, 2, 3, 4, 8] and b.seq.tolist() == [5, 6, 7]
    # The closed square repeats its first vertex, as in the file; Z of layer B is empty (NaN), not 0.
    assert np.array_equal(a.line(0)[0], a.line(0)[-1]) and np.isnan(b.xyz[:, 2]).all()
    assert a.xyz[0].tolist() == [E0, N0, 100.0] and a.line(1).tolist() == [[E0 - 5.5, N0 + 5.25, -2.5]]
    assert a.attrs.column_names == ["JOIN", "Участок"] and b.attrs.column("Участок").to_pylist() == ["Сопели №2"]
    assert a.attrs.column("Участок").to_pylist() == ["Восток", None]  # blank C → null, never ""
    assert a.vattrs.column_names == ["Road"]
    road = a.vattrs.column("Road").to_numpy()
    assert road[:5].tolist() == [0, 1, 0, 1, 0] and math.isnan(road[5])  # blank N → NaN, never 0

    out = tmp_path / "copy.PIT"
    write_str(out, read.layers, like=read.header)
    assert out.read_bytes() == path.read_bytes()
    assert read_str(_pit(tmp_path, ".STR")).format == "micromine_str"


def test_new_layout_round_trip_is_bit_exact(tmp_path: Path) -> None:
    """Layers built by hand: the Micromine layout, attributes typed by their values, a second cycle is identical."""
    rng = np.random.default_rng(7)
    sq = np.array([[0, 0, 1], [3, 0, 1], [3, 3, 1.5], [0, 3, 2], [0, 0, 1]], dtype=float) + [E0, N0, 0]
    tail = rng.normal(size=(4, 3)) * 1e3 + [E0, N0, 500]
    first = LineSet(
        "Контуры",
        np.vstack([sq, tail]),
        offsets=[0, 5, 9],
        closed=[1, 0],
        attrs={"Год": [2024, None], "Запасы": [12.5, math.nan], "Участок": ["Цители, №2", None]},
    )
    second = LineSet("FAULTS", tail[:2] - 7, offsets=[0, 2], closed=[0], attrs={"Доля": [1 / 3]})
    path = tmp_path / "new.STR"
    written = write_str(path, [first, second])
    kinds = {f.name: (f.kind, f.width, f.decimals) for f in written.fields}
    assert [f.name for f in written.fields][:5] == ["EAST", "NORTH", "RL", "JOIN", "STRING"]
    assert kinds["Год"] == ("N", 4, 0) and kinds["Запасы"] == ("N", 4, 1) and kinds["Доля"] == ("R", 8, 6)
    assert kinds["Участок"] == ("C", 10, 0) and kinds["STRING"] == ("C", 20, 0)
    read = read_str(path)
    assert [ls.name for ls in read.layers] == ["Контуры", "FAULTS"]
    for got, want in zip(read.layers, [first, second], strict=True):
        assert got.xyz.tobytes() == want.xyz.tobytes() and got.closed.tolist() == want.closed.tolist()
    attrs = read.layers[0].attrs
    assert attrs.column("Год").to_pylist()[0] == 2024 and math.isnan(attrs.column("Год").to_pylist()[1])
    assert attrs.column("Участок").to_pylist() == ["Цители, №2", None]
    assert read.layers[1].attrs.column("Доля").to_pylist() == [1 / 3]
    again = tmp_path / "again.STR"
    write_str(again, read.layers, like=read.header)
    assert again.read_bytes() == path.read_bytes()


def test_join_is_kept_or_renumbered(tmp_path: Path) -> None:
    def line(x: float) -> np.ndarray:
        return np.array([[x, 0.0, 0.0], [x + 1, 1.0, 0.0]])

    layer = LineSet("A", np.vstack([line(0), line(10), line(20)]), [0, 2, 4, 6], [0, 0, 0], attrs={"JOIN": [7, 7, 2]})
    other = LineSet("B", line(50), [0, 2], [0], attrs={"JOIN": [7.5]})
    path = tmp_path / "join.STR"
    written = write_str(path, [layer, other])
    assert any("JOIN" in w for w in written.warnings)
    read = read_str(path)
    assert [ls.line_count for ls in read.layers] == [3, 1]  # no two lines merged
    assert read.layers[0].attrs.column("JOIN").to_pylist() == [7, 8, 2]
    assert read.layers[1].attrs.column("JOIN").to_pylist() == [9]


def test_fields_needed_to_tell_lines_apart_are_added(tmp_path: Path) -> None:
    source = _write(tmp_path, build(CP, STR_FIELDS[:3], [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]))
    read = read_str(source)
    assert [ls.name for ls in read.layers] == ["Без кода"] and read.layers[0].line_count == 1
    assert any("STRING" in w for w in read.warnings)
    layer = read.layers[0]
    two = LineSet("Без кода", np.vstack([layer.xyz, layer.xyz + 1]), [0, 2, 4], [0, 0])
    named = LineSet("Новый", layer.xyz + 5, [0, 2], [0])
    out = tmp_path / "out.STR"
    written = write_str(out, [two, named], like=read.header)
    assert [f.name for f in written.fields] == ["EAST", "NORTH", "RL", "STRING", "JOIN"]
    assert any("Добавлены поля STRING, JOIN" in w for w in written.warnings)
    back = read_str(out)
    assert [(ls.name, ls.line_count) for ls in back.layers] == [("Без кода", 2), ("Новый", 1)]


def test_vertices_without_coordinates_and_missing_rl(tmp_path: Path) -> None:
    rows = [[1.0, 2.0, "1", "A"], [math.nan, 3.0, "1", "A"], [4.0, 5.0, "1", "A"], [6.0, 7.0, "2", "A"]]
    fields = [STR_FIELDS[0], STR_FIELDS[1], STR_FIELDS[3], STR_FIELDS[4]]
    read = read_str(_write(tmp_path, build(CP, fields, rows)))
    (layer,) = read.layers
    assert layer.line_count == 2 and np.diff(layer.offsets).tolist() == [2, 1] and layer.seq.tolist() == [0, 2, 3]
    assert np.isnan(layer.xyz[:, 2]).all()  # no RL: Z is empty, never 0
    assert any("RL" in w for w in read.warnings) and any("1 вершин без координаты" in w for w in read.warnings)


# ---------------------------------------------------------------- negative cases


def test_rejects_foreign_broken_and_bomb_files(tmp_path: Path) -> None:
    assert _error(_write(tmp_path, b"")).code == "empty_file"
    assert _error(_write(tmp_path, b"EAST;NORTH\n1;2\n")).code == "not_micromine"
    good = build(CP, STR_FIELDS, [r[:5] for r in _records()])
    assert _error(_write(tmp_path, good[:-7])).code == "truncated"
    assert _error(_write(tmp_path, good[:3000])).code == "truncated"
    assert _error(_write(tmp_path, good.replace(b"5   VARIABLES", b"999999999 VARIABLES"))).code == "too_large"
    assert _error(_write(tmp_path, good.replace(b"C 20  0", b"C 999999999  0"))).code == "too_large"
    assert _error(_write(tmp_path, build(CP, [(b"A", "R", 8, 0), (b"B", "R", 8, 0)], [[1.0, 2.0]]))).code == (
        "no_coordinates"
    )
    assert _error(_write(tmp_path, build(CP, STR_FIELDS, []))).code == "no_lines"
    with pytest.raises(FormatError) as info:
        write_str(tmp_path / "none.STR", [LineSet("A", np.zeros((0, 3)), [0], [])])
    assert info.value.code == "no_lines"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["t.STR"]  # nothing half-written is left


def test_progress_cancel_and_speed(tmp_path: Path) -> None:
    rows = 300_000
    body = np.zeros(rows, dtype=np.dtype([("xyz", "<f8", 3), ("join", "S12"), ("code", "S20"), ("eol", "S2")]))
    body["xyz"] = np.random.default_rng(3).normal(size=(rows, 3)) * 1e3 + [E0, N0, 0]
    body["join"] = np.char.ljust((np.arange(rows) // 50).astype("S12"), 12)
    body["code"] = np.char.ljust(np.array([b"L%d" % (i % 7) for i in range(rows // 50 + 1)])[np.arange(rows) // 50], 20)
    body["eol"] = b"\r\n"
    path = _write(tmp_path, build(CP, STR_FIELDS, []) + body.tobytes(), "big.STR")
    seen: list[float] = []
    started = time.perf_counter()
    read = read_str(path, progress=lambda f, m: seen.append(f), chunk_rows=1 << 15)
    seconds = time.perf_counter() - started
    assert read.line_count == rows // 50 and len(read.layers) == 7 and read.point_count == rows
    assert len(seen) >= 8 and seen == sorted(seen) and seen[-1] == 1.0
    out = tmp_path / "out.STR"
    write_str(out, read.layers, like=read.header)
    assert out.read_bytes() == path.read_bytes()
    assert seconds < 10  # ADR 0011: 1 млн вершин ≤ 10 с; here 0,3 млн with a wide margin for CI

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None) -> None:
        if fraction > 0.3:
            raise Cancelled

    with pytest.raises(Cancelled):
        read_str(path, progress=cancel, chunk_rows=1 << 15)
    with pytest.raises(Cancelled):
        write_str(tmp_path / "cancelled.STR", read.layers, progress=cancel)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["big.STR", "out.STR"]
    path.unlink()  # the memory map is closed (matters on Windows)
