"""Points XYZ of a cloud as a stream (`formats/points_stream.py`, ADR 0014 §3): the head of a file, the body by
blocks — the same points whatever the size of a block, cells that are not plain numbers, «каждая k-я запись» —
and the CSV writer of a cloud: the shortest text that reads back to the same double, empty `h` as an empty cell."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from bf_core.formats.errors import FormatError
from bf_core.formats.points_stream import XyzCloud, inspect_xyz, open_xyz, write_cloud_csv

pytestmark = pytest.mark.windows


def _all(cloud: XyzCloud) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    parts = list(cloud.chunks())
    cls = None if parts[0].cls is None else np.concatenate([p.cls for p in parts])
    return np.concatenate([p.xyz for p in parts]), np.concatenate([p.seq for p in parts]), cls


def test_blocks_of_any_size_give_the_same_points(tmp_path: Path) -> None:
    rng = np.random.default_rng(4)
    xyz = np.round(rng.uniform([4.5e5, 6.1e6, 50.0], [4.6e5, 6.2e6, 400.0], (5000, 3)), 4)
    lines = [f"{x!r};{y!r};{z!r}" for x, y, z in xyz.tolist()]
    lines[100] = "4 584 699,7;6150000,25;120,5"  # digit groups and a decimal comma: the slow path of Э3
    xyz[100] = [4_584_699.7, 6_150_000.25, 120.5]
    path = tmp_path / "точки.csv"
    path.write_text("EAST;NORTH;RL\n" + "\r\n".join(lines) + "\r\n", encoding="utf-8")
    head = inspect_xyz(path)
    assert head.fields == {"x": "EAST", "y": "NORTH", "z": "RL", "class": None} and head.header
    whole = _all(XyzCloud(path, head))
    for block in (1, 333, 4096):
        cut = XyzCloud(path, head, block=block)
        got = _all(cut)
        assert np.array_equal(got[0], whole[0]) and np.array_equal(got[1], whole[1]), block
    assert np.array_equal(whole[0], xyz) and np.array_equal(whole[1], np.arange(5000)) and whole[2] is None
    every = _all(open_xyz(path, every=4))
    assert np.array_equal(every[1], np.arange(0, 5000, 4)) and np.array_equal(every[0], xyz[::4])


def test_head_refusals(tmp_path: Path) -> None:
    empty = tmp_path / "пусто.xyz"
    empty.write_bytes(b"")
    flat = tmp_path / "плоско.csv"
    flat.write_text("X;Y\n1;2\n", encoding="utf-8")
    other = tmp_path / "иное.csv"
    other.write_text("A;B;C\n1;2;3\n", encoding="utf-8")
    for path, code in ((empty, "empty_file"), (flat, "no_column"), (other, "no_coordinates")):
        with pytest.raises(FormatError) as info:
            inspect_xyz(path)
        assert info.value.code == code and info.value.hint, path.name
    with pytest.raises(FormatError) as info:
        inspect_xyz(other, x="A", y="B", z="Нет")
    assert info.value.code == "no_column"


def test_cloud_csv_reads_back_bit_for_bit(tmp_path: Path) -> None:
    rng = np.random.default_rng(9)
    xyz = rng.normal([5e5, 6e6, 300.0], [1e3, 1e3, 50.0], (3000, 3))
    xyz[0] = [0.1 + 0.2, -0.0, 1e-7]
    cls = rng.integers(0, 256, 3000).astype(np.uint8)
    h = rng.normal(0, 3, 3000).astype(np.float32)
    h[::5] = np.nan
    for delimiter, decimal in ((";", "."), (" ", "."), ("\t", ",")):
        target = tmp_path / f"облако{ord(delimiter)}.csv"
        portions = [(xyz[a : a + 700], cls[a : a + 700], h[a : a + 700]) for a in range(0, 3000, 700)]
        out = write_cloud_csv(target, portions, total=3000, with_h=True, delimiter=delimiter, decimal=decimal)
        assert (
            out.rows == 3000
            and out.columns == ["X", "Y", "Z", "класс", "h"]
            and not target.with_suffix(".csv.part").exists()
        )
        first = target.read_text(encoding="utf-8").splitlines()[1].split(delimiter)
        assert first[4] == ("nan" if delimiter == " " else "")  # an empty h is not a zero
        head = inspect_xyz(target)
        assert head.fields["class"] == "класс" and head.decimal == decimal
        got = _all(XyzCloud(target, head))
        assert np.array_equal(got[0], xyz) and np.array_equal(got[2], cls), delimiter
    with pytest.raises(FormatError) as info:
        write_cloud_csv(tmp_path / "x.csv", [], total=0, delimiter=",", decimal=",")
    assert info.value.code == "unrepresentable"


def test_cloud_csv_writes_h_as_the_shortest_text_of_its_float32(tmp_path: Path) -> None:
    """7.1.18: `h` of a cloud is a float32; taken through float64 it was written as `0.12999999523162842`.
    The text is the shortest that reads back to the same float32, bit for bit."""
    rng = np.random.default_rng(10)
    h = np.concatenate([[0.13, -2.5, 1e-7, 123456.79, 1 / 3, np.nan, -0.0], rng.normal(0, 3, 2993)]).astype(np.float32)
    xyz = rng.normal([5e5, 6e6, 300.0], [1e3, 1e3, 50.0], (len(h), 3))
    cls = np.full(len(h), 2, dtype=np.uint8)
    for decimal in (".", ","):
        target = tmp_path / f"h{ord(decimal)}.csv"
        portions = [(xyz[a : a + 1000], cls[a : a + 1000], h[a : a + 1000]) for a in range(0, len(h), 1000)]
        write_cloud_csv(target, portions, total=len(h), with_h=True, decimal=decimal)
        texts = [line.split(";")[4].replace(",", ".") for line in target.read_text(encoding="utf-8").splitlines()[1:]]
        assert texts[:7] == ["0.13", "-2.5", "1e-7", "123456.79", "0.33333334", "", "-0"], decimal
        back = np.array([float(t) if t else np.nan for t in texts]).astype(np.float32)
        assert back.tobytes() == h.tobytes(), decimal  # NaN and −0 included
        assert max(len(t) for t in texts) <= 15  # 9 significant digits, a sign, a point, an exponent
        assert np.array_equal(_all(XyzCloud(target, inspect_xyz(target)))[0], xyz)  # X, Y, Z: float64 as before
    # heights that come as float64 keep every digit of theirs
    exact = tmp_path / "f8.csv"
    write_cloud_csv(exact, [(xyz[:3], cls[:3], np.array([0.1 + 0.2, 1.0, np.nan]))], total=3, with_h=True)
    assert [line.split(";")[4] for line in exact.read_text(encoding="utf-8").splitlines()[1:]] == [
        "0.30000000000000004", "1", "",
    ]  # fmt: skip
