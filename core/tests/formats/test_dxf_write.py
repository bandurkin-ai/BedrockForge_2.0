"""DXF writer: the cycle write → read is bit for bit; a drawing other programs open (ТЗ 7.18, 9.5; ADR 0011 §2)."""

from __future__ import annotations

import math
import random
import struct
from pathlib import Path

import ezdxf
import numpy as np
import pytest

from bf_core.formats import dxf_write
from bf_core.formats.dxf_lines import read_dxf
from bf_core.formats.dxf_write import layer_names, nearest_aci, write_dxf
from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet, from_lines, from_points

X0, Y0 = 466570.58600000001, 4584699.697000001


def _hard_doubles(rnd: random.Random, count: int) -> np.ndarray:
    """Doubles that lose bits in any careless text: every magnitude, 17 digits, −0.0, the ends of the range."""
    out = [struct.unpack("<d", struct.pack("<Q", rnd.getrandbits(64)))[0] for _ in range(count * 2)]
    out = [v for v in out if math.isfinite(v)][: count - 12]
    out += [-0.0, 0.0, 5e-324, 2.2250738585072014e-308, 1.7976931348623157e308, -1.7976931348623157e308]
    out += [0.1 + 0.2, 1 / 3, X0, Y0, 9007199254740993.0, 1e22]
    return np.array(out, dtype=np.float64)


def _random_layers(seed: int) -> list[LineSet]:
    rnd = random.Random(seed)
    layers: list[LineSet] = []
    for k, name in enumerate(["Контуры карьера", "Горизонтали 5 м", "0", "faults 100%"]):
        lines = []
        closed = []
        for _ in range(40):
            n = rnd.choice([2, 2, 3, 4, 7, 60])
            if k == 2:  # the hardest numbers a double can hold
                v = _hard_doubles(rnd, n * 3).reshape(-1, 3)
            else:
                v = np.array(
                    [
                        (X0 + rnd.uniform(-5e3, 5e3), Y0 + rnd.uniform(-5e3, 5e3), rnd.uniform(-200, 900))
                        for _ in range(n)
                    ]
                )
            lines.append(v)
            closed.append(n >= 3 and rnd.random() < 0.4)
        layers.append(from_lines(name, lines, closed, color=[None, "#ff0000", "#123456", "#00ff7f"][k]))
    points = np.array(
        [(X0 + rnd.uniform(-5e3, 5e3), Y0 + rnd.uniform(-5e3, 5e3), rnd.uniform(0, 900)) for _ in range(500)]
    )
    layers.append(from_points("Устья скважин", points, color="#0000ff"))
    return layers


def _same(got: LineSet, want: LineSet) -> None:
    assert got.name == want.name and got.color == want.color and got.points_only == want.points_only
    assert got.xyz.tobytes() == want.xyz.tobytes()  # float64, bit for bit
    if want.offsets is not None and want.closed is not None:
        assert got.offsets is not None and got.closed is not None
        assert got.offsets.tolist() == want.offsets.tolist() and got.closed.tolist() == want.closed.tolist()


def test_the_cycle_is_bit_for_bit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layers = _random_layers(1)
    monkeypatch.setattr(dxf_write, "CHUNK", 37)  # lines longer and shorter than a portion
    written = write_dxf(tmp_path / "cycle.dxf", layers)
    assert written.warnings == [] and written.size == written.path.stat().st_size
    assert (written.lines, written.points, written.texts) == (160, 500, 0)
    read = read_dxf(written.path, recover=False)
    assert read.warnings == [] and read.encoding == "cp1251" and read.header.version == "AC1018"
    assert len(read.layers) == len(layers)
    for got, want in zip(read.layers, layers, strict=True):
        _same(got, want)
    assert sum(layer.closed_count for layer in layers) > 30
    again = write_dxf(tmp_path / "again.dxf", read.layers)  # read → write gives the same entities, byte for byte

    def entities(path: Path) -> bytes:
        data = path.read_bytes()  # (the header of ezdxf holds the time of saving and identifiers of its own)
        return data[data.index(b"ENTITIES") : data.index(b"OBJECTS")]

    assert entities(again.path) == entities(written.path) and len(entities(again.path)) > 500_000


def test_closed_lines_keep_their_last_vertex(tmp_path: Path) -> None:
    square = np.array([[X0, Y0, 5.0], [X0 + 10, Y0, 5.0], [X0 + 10, Y0 + 10, 5.0], [X0, Y0 + 10, 5.0]])
    nearly = np.vstack([square, square[:1] + [3e-7, -2e-7, 1e-7]])  # ends closer than 1e-6: closed, but not equal
    layer = LineSet(
        "L",
        np.vstack([square, square[:1], nearly, square[:3], square[:1], square[:2]]),
        np.array([0, 5, 10, 14, 16]),
        np.array([1, 1, 1, 0]),
    )
    written = write_dxf(tmp_path / "closed.dxf", [layer])
    assert written.vertices == 4 + 5 + 3 + 2  # an exact repeat is implied by the closed flag, a near one is written
    doc = ezdxf.readfile(written.path)
    flags = [(e.dxf.flags, len(e.vertices)) for e in doc.modelspace()]
    assert flags == [(9, 4), (8, 5), (9, 3), (8, 2)]
    _same(read_dxf(written.path, recover=False).layers[0], layer)


def test_points_texts_and_single_vertex_lines(tmp_path: Path) -> None:
    labels = from_points(
        "Подписи",
        [[1.5, 2.5, 3.5], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0], [1.0, 1.0, 1.0], [2.0, 2.0, 2.0]],
        attrs={
            "text": ["Скв. №12", None, "∑ 5 ≥ 3", "две\nстроки", ""],
            "height": [2.5, math.nan, 1.25, math.nan, 3.0],
            "rotation": [30.0, math.nan, math.nan, 90.0, 0.0],
        },
        color="#ff00ff",
    )
    mixed = from_lines("Смешанный", [[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], [[5.0, 5.0, 5.0]]])
    written = write_dxf(tmp_path / "texts.dxf", [labels, mixed])
    assert (written.lines, written.points, written.texts) == (1, 2, 4)
    assert written.warnings == ["1 текстов без высоты: в DXF записана высота 2,5"]
    read = read_dxf(written.path, recover=False)
    got = read.layers[0]
    assert got.points_only and got.xyz.tobytes() == labels.xyz.tobytes() and got.color == "#ff00ff"
    attrs = got.attrs.to_pydict()
    assert attrs["text"] == ["Скв. №12", None, "∑ 5 ≥ 3", "две строки", ""]  # outside cp1251: \U+XXXX and back
    assert attrs["height"][0] == 2.5 and math.isnan(attrs["height"][1]) and attrs["height"][2:] == [1.25, 2.5, 3.0]
    assert (
        attrs["rotation"][0] == 30.0 and math.isnan(attrs["rotation"][1]) and attrs["rotation"][2:] == [0.0, 90.0, 0.0]
    )
    assert b"\\U+2211 5 \\U+2265 3" in written.path.read_bytes()
    _same(read.layers[1], mixed)
    assert read.layers[1].line(1).tolist() == [[5.0, 5.0, 5.0]]  # a line of one vertex is a POINT and stays a line


def test_other_programs_open_the_drawing(tmp_path: Path) -> None:
    layers = _random_layers(2)
    written = write_dxf(tmp_path / "open.dxf", layers)
    data = written.path.read_bytes()
    assert data.count(b"\r\n") > 1000 and data.count(b"\n") == data.count(b"\r\n")  # CRLF only
    assert b"AC1018" in data[:200] and b"ANSI_1251" in data[:2000] and data.rstrip().endswith(b"EOF")
    doc = ezdxf.readfile(written.path)
    auditor = doc.audit()
    assert auditor.errors == [] and doc.dxfversion == "AC1018" and doc.encoding == "cp1251"
    entities = list(doc.modelspace())
    assert len(entities) == 160 + 500 and {e.dxftype() for e in entities} == {"POLYLINE", "POINT"}
    assert len({e.dxf.handle for e in entities}) == len(entities)  # handles do not repeat …
    seed = int(doc.header["$HANDSEED"], 16)
    assert all(int(v.dxf.handle, 16) < seed for e in entities if e.dxftype() == "POLYLINE" for v in e.vertices)
    table = {layer.dxf.name: (layer.dxf.color, layer.rgb) for layer in doc.layers}
    assert table["Контуры карьера"][1] is None and table["Горизонтали 5 м"] == (1, (255, 0, 0))
    assert table["faults 100%"][1] == (0, 255, 127) and table["Устья скважин"] == (5, (0, 0, 255))
    first = next(e for e in entities if e.dxf.layer == "Горизонтали 5 м")
    assert first.is_3d_polyline and first.dxf.color == 1 and first.rgb == (255, 0, 0)
    want = layers[1].line(0)
    assert [tuple(v.dxf.location) for v in first.vertices] == [tuple(p) for p in want[: len(first.vertices)].tolist()]
    low, high = np.array(doc.header["$EXTMIN"]), np.array(doc.header["$EXTMAX"])
    every = np.vstack([layer.xyz for layer in layers])
    assert np.array_equal(low, every.min(axis=0)) and np.array_equal(high, every.max(axis=0))


def test_what_dxf_cannot_hold(tmp_path: Path) -> None:
    no_z = from_lines("2D", [[[1.0, 2.0, math.nan], [3.0, 4.0, math.nan], [5.0, 6.0, 7.0]]])
    extra = from_lines(
        "A", [[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]], attrs={"JOIN": [5.0], "Тип": ["бровка"]}, vattrs={"M": [0.0, 1.0]}
    )
    written = write_dxf(tmp_path / "lossy.dxf", [no_z, extra])
    assert written.warnings == [
        "2 вершин без высоты Z: в DXF записана высота 0",
        "Атрибуты в DXF не записываются: «JOIN», «Тип», «M»",
    ]
    read = read_dxf(written.path, recover=False)
    assert read.layers[0].xyz.tolist() == [[1.0, 2.0, 0.0], [3.0, 4.0, 0.0], [5.0, 6.0, 7.0]]
    assert read.layers[1].attrs is None
    for nothing in ([], [from_points("пусто", np.empty((0, 3)))]):
        with pytest.raises(FormatError) as info:
            write_dxf(tmp_path / "none.dxf", nothing)
        assert info.value.code == "no_lines" and info.value.message and info.value.hint
        assert not (tmp_path / "none.dxf").exists() and not (tmp_path / "none.dxf.part").exists()


def test_layer_names_and_colours() -> None:
    assert layer_names(["A", "a", "A", "x/y:z", "  ", "Слой", "слой"]) == [
        "A",
        "a_2",
        "A_3",
        "x_y_z",
        "Слой",
        "Слой_2",
        "слой_3",
    ]
    long = layer_names(["я" * 300, "я" * 300])
    assert len(long[0]) == 255 and len(long[1]) == 255 and long[0] != long[1]
    assert nearest_aci((255, 0, 0)) == 1 and nearest_aci((0, 0, 255)) == 5 and nearest_aci((255, 255, 255)) != 7
    assert nearest_aci((254, 2, 1)) == 1


def test_names_survive_the_cycle(tmp_path: Path) -> None:
    names = ["Рудное тело №3", "50% <bad> name", "東京 😀 é", "0", "Defpoints"]
    layers = [from_lines(n, [[[float(i), 0.0, 0.0], [float(i), 1.0, 0.0]]]) for i, n in enumerate(names)]
    read = read_dxf(write_dxf(tmp_path / "names.dxf", layers).path, recover=False)
    assert [layer.name for layer in read.layers] == ["Рудное тело №3", "50% _bad_ name", "東京 😀 é", "0", "Defpoints"]
    assert b"\\U+6771\\U+4EAC \\U+D83D\\U+DE00 \\U+00E9" in (tmp_path / "names.dxf").read_bytes()
    assert ezdxf.readfile(tmp_path / "names.dxf").audit().errors == []


def test_cancellation_leaves_the_old_file(tmp_path: Path) -> None:
    path = tmp_path / "keep.dxf"
    path.write_bytes(b"the old file")
    layers = _random_layers(3)
    seen: list[float] = []

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None = None) -> None:
        seen.append(fraction)
        if fraction > 0.5:
            raise Cancelled

    with pytest.raises(Cancelled):
        write_dxf(path, layers, cancel)
    assert path.read_bytes() == b"the old file" and not path.with_name("keep.dxf.part").exists()
    assert seen == sorted(seen) and seen[0] == 0.0
    done: list[float] = []
    write_dxf(path, layers, lambda f, m=None: done.append(f))
    assert done[-1] == 1.0 and path.read_bytes() != b"the old file"


def test_a_drawing_with_curves_is_stable_after_the_first_reading(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_circle((X0, Y0, 12.5), 80.0, dxfattribs={"layer": "C", "extrusion": (0.1, 0.2, 0.9)})
    msp.add_arc((X0, Y0, 0.0), 33.3, 10.0, 250.0, dxfattribs={"layer": "C"})
    msp.add_ellipse((X0, Y0, 0.0), (120.0, 40.0, 0.0), 0.3, dxfattribs={"layer": "E"})
    msp.add_lwpolyline([(X0, Y0, 0, 0, 0.5), (X0 + 50, Y0, 0, 0, -0.8), (X0 + 50, Y0 + 50, 0, 0, 0)], format="xyseb",
                       close=True, dxfattribs={"layer": "B", "elevation": 300.25})  # fmt: skip
    msp.add_spline(
        [(X0, Y0, 0), (X0 + 40, Y0 + 90, 5), (X0 + 130, Y0 + 20, 9), (X0 + 200, Y0 + 150, 2)], dxfattribs={"layer": "S"}
    )
    source = tmp_path / "curves.dxf"
    doc.saveas(source)
    first = read_dxf(source, recover=False)
    second = read_dxf(write_dxf(tmp_path / "copy.dxf", first.layers).path, recover=False)
    assert [layer.name for layer in second.layers] == ["C", "E", "B", "S"]
    for got, want in zip(second.layers, first.layers, strict=True):
        _same(got, want)  # the polylines the curves became are kept exactly
