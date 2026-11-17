"""DXF lines, points and texts → layers: entities, OCS, texts, colours, limits, hostile files (ТЗ 7.18, 9.5, 9.12)."""

from __future__ import annotations

import math
import random
import re
from pathlib import Path
from typing import Any

import ezdxf
import numpy as np
import pytest
from ezdxf.math import OCS

from bf_core.formats import dxf_stream as ds
from bf_core.formats.dxf_lines import DxfRead, ocs_to_wcs, read_dxf
from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet

from .dxf_builder import drawing, tags

REAL = Path(__file__).resolve().parents[3] / "tests" / "data" / "real" / "TST-FAULTS.DXF"
X0, Y0 = 466570.58600000001, 4584699.697000001  # UTM-sized coordinates with every digit of a double in use


def _write(tmp_path: Path, content: str | bytes, name: str = "a.dxf", encoding: str = "cp1251") -> Path:
    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode(encoding))
    return path


def _by_name(read: DxfRead) -> dict[str, LineSet]:
    return {layer.name: layer for layer in read.layers}


def _lines(layer: LineSet) -> list[list[list[float]]]:
    return [layer.line(i).tolist() for i in range(layer.feature_count)]


def _attrs(layer: LineSet) -> dict[str, list[Any]] | None:
    """Attributes in a form that compares: an empty number is "nan"."""
    if layer.attrs is None:
        return None
    return {
        k: ["nan" if isinstance(v, float) and v != v else v for v in col] for k, col in layer.attrs.to_pydict().items()
    }


def _error(path: Path, **options: Any) -> FormatError:
    with pytest.raises(FormatError) as info:
        read_dxf(path, **options)
    error = info.value
    assert error.message and error.hint and set(error.as_dict()) == {"code", "message", "hint"}
    assert any("а" <= ch <= "я" for ch in error.message.lower()) and any("а" <= ch <= "я" for ch in error.hint.lower())
    return error


# --- simple entities ----------------------------------------------------------------------------------


def test_simple_entities_are_read_bit_for_bit(tmp_path: Path) -> None:
    a, b, c = (X0, Y0, 557.95), (X0 + 0.1, Y0 - 1 / 3, -0.0), (5e-324, 1.7976931348623157e308, 1e-300)
    body = (
        tags(0, "LINE", 8, "Контур", 10, a[0], 20, a[1], 30, a[2], 11, b[0], 21, b[1], 31, b[2])
        + tags(0, "POINT", 8, "Точки", 10, c[0], 20, c[1], 30, c[2])
        + tags(0, "LWPOLYLINE", 8, "Контур", 90, 3, 70, 0, 38, 12.5, 10, a[0], 20, a[1], 10, b[0], 20,
            b[1], 10, 1.0, 20, 2.0)
        + tags(0, "POLYLINE", 8, "3D", 66, 1, 10, 0.0, 20, 0.0, 30, 0.0, 70, 8)
        + "".join(tags(0, "VERTEX", 8, "3D", 10, x, 20, y, 30, z, 70, 32) for x, y, z in (a, b, c))
        + tags(0, "SEQEND", 8, "3D")
        + tags(0, "POLYLINE", 8, "2D", 66, 1, 10, 0.0, 20, 0.0, 30, 7.25, 70, 0)
        + "".join(tags(0, "VERTEX", 8, "2D", 10, x, 20, y, 30, 999.0) for x, y, _ in (a, b))
        + tags(0, "SEQEND", 8, "2D")
        + tags(0, "LINE", 8, "2D", 10, 1.0, 20, 2.0, 11, 3.0, 21, 4.0)  # no Z: the DXF default, 0
        + tags(0, "POINT", 8, "Точки", 10, a[0], 20, a[1], 30, a[2])
    )  # fmt: skip
    read = read_dxf(_write(tmp_path, drawing(body)), recover=False)
    assert (read.format, read.encoding, read.records, read.warnings, read.skipped) == ("dxf", "cp1251", 7, [], {})
    assert (read.meshes, read.recovered, read.header.version, read.header.codepage) == (0, False, "AC1018", "ANSI_1251")
    assert [layer.name for layer in read.layers] == ["Контур", "Точки", "3D", "2D"]  # in the order of the file
    layers = _by_name(read)
    assert _lines(layers["Контур"]) == [[list(a), list(b)], [[a[0], a[1], 12.5], [b[0], b[1], 12.5], [1.0, 2.0, 12.5]]]
    assert math.copysign(1.0, layers["Контур"].xyz[1, 2]) == -1.0  # −0.0 stays −0.0
    assert layers["Точки"].points_only and layers["Точки"].xyz.tolist() == [list(c), list(a)]
    assert layers["Точки"].offsets is None and layers["Точки"].attrs is None
    assert _lines(layers["3D"]) == [[list(a), list(b), list(c)]]
    assert _lines(layers["2D"]) == [[[a[0], a[1], 7.25], [b[0], b[1], 7.25]], [[1.0, 2.0, 0.0], [3.0, 4.0, 0.0]]]
    assert layers["Контур"].seq.tolist() == [0, 1, 3, 4, 5] and layers["Точки"].seq.tolist() == [2, 13]
    assert layers["3D"].seq.tolist() == [6, 7, 8] and layers["2D"].seq.tolist() == [9, 10, 11, 12]
    assert (read.line_count, read.point_count) == (5, 14)
    assert all(layer.closed_count == 0 and layer.xyz.dtype == np.float64 for layer in read.layers)


def test_closed_lines_repeat_the_first_vertex(tmp_path: Path) -> None:
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    body = (
        tags(0, "LWPOLYLINE", 8, "A", 70, 1, 38, 5.0) + "".join(tags(10, x, 20, y) for x, y in square)
        + tags(0, "LWPOLYLINE", 8, "A", 70, 0) + "".join(tags(10, x, 20, y) for x, y in [*square, square[0]])
        + tags(0, "LWPOLYLINE", 8, "A", 70, 1) + "".join(tags(10, x, 20, y) for x, y in square[:2])
        + tags(0, "POLYLINE", 8, "B", 66, 1, 70, 9)
        + "".join(tags(0, "VERTEX", 8, "B", 10, x, 20, y, 30, 1.5, 70, 32) for x, y in square)
        + tags(0, "SEQEND")
        + tags(0, "LWPOLYLINE", 8, "A", 70, 129) + "".join(tags(10, x, 20, y) for x, y in square[:3])
    )  # fmt: skip
    layers = _by_name(read_dxf(_write(tmp_path, drawing(body)), recover=False))
    a = layers["A"]
    assert a.closed.tolist() == [1, 1, 0, 1]  # flagged; ends coincide; two vertices cannot close; a triangle
    assert a.line(0).tolist() == [[x, y, 5.0] for x, y in [*square, square[0]]]
    assert a.line(1).tolist() == [[x, y, 0.0] for x, y in [*square, square[0]]]  # nothing is added twice
    assert a.line(2).tolist() == [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]]
    assert a.line(3).tolist() == [[x, y, 0.0] for x, y in [*square[:3], square[0]]]
    assert a.seq.tolist() == [
        0,
        1,
        2,
        3,
        0,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        15,
        16,
        17,
        15,
    ]  # the repeat has the seq of the first
    assert layers["B"].closed.tolist() == [1] and layers["B"].line(0).tolist()[-1] == [0.0, 0.0, 1.5]
    assert layers["B"].stats()["length"] == 40.0 and a.flat() and layers["B"].flat()


def _ezdxf_features(path: Path) -> dict[str, list[np.ndarray]]:
    """The etalon: vertices of every simple entity of model space in the world system, as ezdxf reads them."""
    out: dict[str, list[np.ndarray]] = {}
    for e in ezdxf.readfile(path).modelspace():
        kind = e.dxftype()
        if kind == "LINE":
            pts = [e.dxf.start, e.dxf.end]
        elif kind == "POINT":
            pts = [e.dxf.location]
        elif kind == "LWPOLYLINE":
            pts = list(e.vertices_in_wcs())
        elif kind == "POLYLINE":
            pts = list(e.points()) if e.is_3d_polyline else list(e.points_in_wcs())
        elif kind == "TEXT":
            pts = [e.ocs().to_wcs(e.dxf.insert)]
        elif kind == "MTEXT":
            pts = [e.dxf.insert]
        else:
            continue
        v = np.array([(p.x, p.y, p.z) for p in pts], dtype=np.float64)
        if kind in ("LWPOLYLINE", "POLYLINE") and e.is_closed and len(v) >= 3 and not np.array_equal(v[0], v[-1]):
            v = np.vstack([v, v[:1]])
        # ezdxf writes the Cyrillic names of a cp1252 drawing as \U+XXXX and gives them back as written
        layer = re.sub(r"\\U\+([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), e.dxf.layer)
        out.setdefault(layer, []).append(v)
    return out


def _random_drawing(path: Path, seed: int, count: int, version: str = "R2004") -> None:
    rnd = random.Random(seed)
    doc = ezdxf.new(version)
    msp = doc.modelspace()

    def xy() -> tuple[float, float]:
        return X0 + rnd.uniform(-5000, 5000), Y0 + rnd.uniform(-5000, 5000)

    def normal() -> tuple[float, float, float]:
        pick = rnd.random()
        if pick < 0.4:
            return (0.0, 0.0, 1.0)
        if pick < 0.5:
            return (0.0, 0.0, -1.0)
        if pick < 0.6:
            return (rnd.uniform(-0.01, 0.01), rnd.uniform(-0.01, 0.01), rnd.choice([-1.0, 1.0]))  # near the Z axis
        return (rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1))

    for _ in range(count):
        attribs: dict[str, Any] = {"layer": f"Слой {rnd.randrange(6)}"}
        kind = rnd.randrange(7)
        if version == "R12" and kind in (2, 6):  # no LWPOLYLINE and no MTEXT before AutoCAD 2000
            kind = 4
        if kind == 0:
            msp.add_line((*xy(), rnd.uniform(0, 900)), (*xy(), rnd.uniform(0, 900)), dxfattribs=attribs)
        elif kind == 1:
            msp.add_point((*xy(), rnd.uniform(0, 900)), dxfattribs=attribs)
        elif kind == 2:
            attribs |= {"elevation": rnd.uniform(-50, 900), "extrusion": normal()}
            msp.add_lwpolyline(
                [xy() for _ in range(rnd.randrange(1, 30))], close=rnd.random() < 0.4, dxfattribs=attribs
            )
        elif kind == 3:
            points = [(*xy(), rnd.uniform(0, 900)) for _ in range(rnd.randrange(2, 40))]
            msp.add_polyline3d(points, close=rnd.random() < 0.3, dxfattribs=attribs)
        elif kind == 4:
            attribs |= {"elevation": (0, 0, rnd.uniform(0, 900)), "extrusion": normal()}
            msp.add_polyline2d(
                [xy() for _ in range(rnd.randrange(2, 20))], close=rnd.random() < 0.3, dxfattribs=attribs
            )
        elif kind == 5:
            attribs |= {"insert": (*xy(), rnd.uniform(0, 900)), "extrusion": normal(), "height": 2.5}
            msp.add_text("Подпись", dxfattribs=attribs)
        else:
            msp.add_mtext("Текст", dxfattribs=attribs | {"insert": (*xy(), 5.0)})
    doc.saveas(path)


@pytest.mark.parametrize(("version", "block"), [("R2004", None), ("R2004", 700), ("R12", 5000), ("R2018", None)])
def test_a_random_drawing_matches_ezdxf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, block: int | None
) -> None:
    if block:
        monkeypatch.setattr(ds, "BLOCK_BYTES", block)  # entities and polylines are cut between blocks
        monkeypatch.setattr(ds, "SMALL_BYTES", 0)
    path = tmp_path / "random.dxf"
    _random_drawing(path, seed=11, count=600, version=version)
    expected = _ezdxf_features(path)
    read = read_dxf(path, recover=False)
    assert read.warnings == [] and read.records == sum(len(v) for v in expected.values()) == 600
    assert {layer.name for layer in read.layers} == set(expected)
    worst = 0.0
    for layer in read.layers:
        lines = expected[layer.name]
        assert layer.feature_count == len(lines)
        for i, want in enumerate(lines):
            got = layer.line(i)
            assert got.shape == want.shape
            worst = max(worst, float(np.abs(got - want).max()))
    # OCS → WCS in another order of operations than ezdxf: a few units of the last place at 5·10⁶ (9·10⁻¹⁰ m each)
    assert worst <= 1e-8
    seq = np.concatenate([layer.seq for layer in read.layers])
    added = sum(layer.closed_count for layer in read.layers)  # a repeated first vertex has the seq of the first
    assert len(seq) == sum(len(v) for lines in expected.values() for v in lines) and added > 20
    assert np.unique(seq).tolist() == list(range(len(seq) - added))  # every vertex of the file once, in file order


def test_blocks_do_not_change_the_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "random.dxf"
    _random_drawing(path, seed=5, count=300)
    whole = read_dxf(path, recover=False)
    for size in (64, 1000, 20_000):
        monkeypatch.setattr(ds, "BLOCK_BYTES", size)
        parts = read_dxf(path, recover=False)
        assert [layer.name for layer in parts.layers] == [layer.name for layer in whole.layers]
        for got, want in zip(parts.layers, whole.layers, strict=True):
            assert got.xyz.tobytes() == want.xyz.tobytes() and got.seq.tolist() == want.seq.tolist()
            assert got.points_only == want.points_only and got.color == want.color
            if want.offsets is not None and got.offsets is not None and got.closed is not None:
                assert got.offsets.tolist() == want.offsets.tolist() and got.closed.tolist() == want.closed.tolist()
            assert _attrs(got) == _attrs(want)


# --- OCS ----------------------------------------------------------------------------------------------


def test_ocs_to_wcs_is_the_arbitrary_axis_algorithm() -> None:
    rnd = random.Random(3)
    normals = [(0.0, 0.0, 1.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 5.0)]
    normals += [
        (1 / 64, 0.0, 1.0),
        (1 / 65, 1 / 65, 1.0),
        (0.0156249, 0.0, -1.0),
        (0.0156251, 0.0, 1.0),
    ]  # the threshold
    normals += [(rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1)) for _ in range(300)]
    points = np.array(
        [(X0 + rnd.uniform(-1e4, 1e4), Y0 + rnd.uniform(-1e4, 1e4), rnd.uniform(-1e3, 1e3)) for _ in normals]
    )
    expected = np.array([tuple(OCS(n).to_wcs(tuple(p))) for n, p in zip(normals, points, strict=True)])
    got = ocs_to_wcs(points, np.array(normals))
    assert np.abs(got - expected).max() <= 1e-8  # coordinates of 5·10⁶: one unit of the last place is 9·10⁻¹⁰
    assert ocs_to_wcs(points, (0.0, 0.0, 1.0)).tobytes() == points.tobytes()  # the default normal changes nothing
    assert ocs_to_wcs([[1.0, 2.0, 3.0]], (0.0, 0.0, -1.0)).tolist() == [[-1.0, 2.0, -3.0]]  # a mirrored entity
    assert np.allclose(ocs_to_wcs([[1.0, 2.0, 3.0]], (1.0, 0.0, 0.0)), [[3.0, 1.0, 2.0]], atol=1e-15)


def test_entities_in_their_own_coordinate_systems(tmp_path: Path) -> None:
    n = (0.3, -0.4, 0.5)
    body = (
        tags(0, "LWPOLYLINE", 8, "L", 70, 0, 38, 20.0, 10, 1.0, 20, 2.0, 10, 30.0, 20, -4.0, 210, n[0],
            220, n[1], 230, n[2])
        + tags(0, "TEXT", 8, "T", 10, 5.0, 20, 6.0, 30, 7.0, 40, 1.0, 1, "x", 210, 0.0, 220, 0.0, 230, -1.0)
        + tags(0, "POLYLINE", 8, "P", 66, 1, 10, 0.0, 20, 0.0, 30, 3.0, 70, 0, 210, 1.0, 220, 0.0, 230, 0.0)
        + tags(0, "VERTEX", 8, "P", 10, 1.0, 20, 2.0, 30, 0.0, 0, "VERTEX", 8, "P", 10, 4.0, 20, 6.0, 30,
            0.0, 0, "SEQEND")
        + tags(0, "LINE", 8, "W", 10, 1.0, 20, 2.0, 30, 3.0, 11, 4.0, 21, 5.0, 31, 6.0, 210, 0.0, 220, 0.0, 230, -1.0)
        + tags(0, "POINT", 8, "W", 10, 1.0, 20, 2.0, 30, 3.0, 210, 1.0, 220, 0.0, 230, 0.0)
        + tags(0, "POLYLINE", 8, "W", 66, 1, 70, 8, 210, 1.0, 220, 0.0, 230, 0.0)
        + tags(0, "VERTEX", 8, "W", 10, 1.0, 20, 2.0, 30, 3.0, 0, "VERTEX", 8, "W", 10, 4.0, 20, 6.0, 30,
            9.0, 0, "SEQEND")
    )  # fmt: skip
    layers = _by_name(read_dxf(_write(tmp_path, drawing(body)), recover=False))
    ocs = OCS(n)
    want = [tuple(ocs.to_wcs((1.0, 2.0, 20.0))), tuple(ocs.to_wcs((30.0, -4.0, 20.0)))]
    assert np.abs(layers["L"].xyz - np.array(want)).max() <= 1e-12
    assert layers["T"].xyz.tolist() == [[-5.0, 6.0, -7.0]]
    assert np.allclose(
        layers["P"].xyz, [[3.0, 1.0, 2.0], [3.0, 4.0, 6.0]], atol=1e-15
    )  # X = elevation along the normal
    # LINE, POINT and a 3D POLYLINE are in the world system whatever their extrusion is
    assert layers["W"].xyz.tolist() == [
        [1.0, 2.0, 3.0],
        [4.0, 5.0, 6.0],
        [1.0, 2.0, 3.0],
        [1.0, 2.0, 3.0],
        [4.0, 6.0, 9.0],
    ]


# --- texts, layers, colours ---------------------------------------------------------------------------


def test_texts_are_points_with_attributes(tmp_path: Path) -> None:
    long = "Д" * 250
    body = (
        tags(0, "TEXT", 8, "Подписи", 10, 1.0, 20, 2.0, 30, 3.0, 40, 2.5, 1, "Скважина С-1", 50, 30.0)
        + tags(0, "TEXT", 8, "Подписи", 10, 0.0, 20, 0.0, 30, 0.0, 40, 1.0, 1, "%%c25 45%%d %%p0.5", 72, 1,
            11, 7.0, 21, 8.0, 31, 9.0)
        + tags(0, "TEXT", 8, "Подписи", 10, 4.0, 20, 5.0, 40, 1.0, 1, "по двум точкам", 72, 5, 11, 7.0, 21, 8.0)
        + tags(0, "MTEXT", 8, "Подписи", 10, 10.0, 20, 20.0, 30, 30.0, 40, 1.5, 3, long, 3, long, 1,
            "конец\\Pстрока 2", 50, 90.0)
        + tags(0, "MTEXT", 8, "Подписи", 10, 1.0, 20, 1.0, 30, 1.0, 40, 2.0, 1,
            "{\\fArial|b1;жирный} \\U+2116 7", 11, 0.0, 21, 1.0, 31, 0.0)
        + tags(0, "POINT", 8, "Подписи", 10, 5.0, 20, 5.0, 30, 5.0)
        + tags(0, "TEXT", 8, "Подписи", 10, 6.0, 20, 6.0, 30, 6.0, 1, "")
        + tags(0, "LINE", 8, "Смешанный", 10, 0.0, 20, 0.0, 30, 0.0, 11, 1.0, 21, 1.0, 31, 1.0)
        + tags(0, "TEXT", 8, "Смешанный", 10, 2.0, 20, 2.0, 30, 2.0, 40, 3.0, 1, "у линии")
    )  # fmt: skip
    read = read_dxf(_write(tmp_path, drawing(body)), recover=False)
    labels, mixed = read.layers
    assert labels.points_only and read.warnings == [] and read.records == 9
    assert labels.xyz.tolist() == [[1.0, 2.0, 3.0], [7.0, 8.0, 9.0], [4.0, 5.0, 0.0], [10.0, 20.0, 30.0],
                                   [1.0, 1.0, 1.0], [5.0, 5.0, 5.0], [6.0, 6.0, 6.0]]  # fmt: skip
    attrs = labels.attrs.to_pydict()
    assert attrs["text"] == ["Скважина С-1", "Ø25 45° ±0.5", "по двум точкам", long * 2 + "конец\nстрока 2",
                             "жирный № 7", None, ""]  # fmt: skip
    assert attrs["height"][:5] == [2.5, 1.0, 1.0, 1.5, 2.0] and all(math.isnan(v) for v in attrs["height"][5:])
    assert attrs["rotation"][:5] == [30.0, 0.0, 0.0, 90.0, 90.0] and math.isnan(attrs["rotation"][5])
    assert attrs["rotation"][6] == 0.0  # a text without a rotation is not turned; a point has no rotation at all
    assert [f["name"] for f in labels.attr_schema()] == ["text", "height", "rotation"]
    assert [f["kind"] for f in labels.attr_schema()] == ["text", "number", "number"]
    assert not mixed.points_only and mixed.line_count == 2 and mixed.offsets.tolist() == [0, 2, 3]
    assert mixed.attrs.to_pydict()["text"] == [None, "у линии"] and math.isnan(mixed.attrs.to_pydict()["height"][0])


@pytest.mark.parametrize(
    ("version", "codepage", "encoding", "expected"),
    [
        ("AC1018", "ANSI_1251", "cp1251", "cp1251"),
        ("AC1009", "DOS866", "cp866", "cp866"),
        ("AC1027", "ANSI_1251", "utf-8", "utf-8"),  # from AutoCAD 2007 the file is UTF-8 whatever the code page says
        (None, None, "cp1251", "cp1251"),
        (None, None, "utf-8", "cp1251"),
    ],
)
def test_code_pages(tmp_path: Path, version: str | None, codepage: str | None, encoding: str, expected: str) -> None:
    body = tags(0, "LINE", 8, "Горизонтали рельефа", 10, 1.0, 20, 2.0, 11, 3.0, 21, 4.0) + tags(
        0, "TEXT", 8, "Подписи скважин", 10, 1.0, 20, 2.0, 40, 1.0, 1, "Скв. №12 Восточный"
    )
    text = drawing(body, version=version, codepage=codepage, layers=[("Горизонтали рельефа", 30)])
    read = read_dxf(_write(tmp_path, text, encoding=encoding), recover=False)
    assert read.encoding == expected and [layer.name for layer in read.layers] == [
        "Горизонтали рельефа",
        "Подписи скважин",
    ]
    assert read.layers[1].attrs.to_pydict()["text"] == ["Скв. №12 Восточный"]
    assert read.header.layers == {"Горизонтали рельефа": "#ff7f00"} and read.layers[0].color == "#ff7f00"
    unknown = [w for w in read.warnings if "Кодовая страница" in w]
    assert len(unknown) == (1 if version is None else 0) and len(read.warnings) == len(unknown)


def test_text_that_does_not_fit_the_code_page(tmp_path: Path) -> None:
    body = tags(0, "TEXT", 8, "T", 10, 1.0, 20, 2.0, 40, 1.0, 1, "A\x98B")
    read = read_dxf(_write(tmp_path, drawing(body), encoding="latin-1"), recover=False)
    assert read.layers[0].attrs.to_pydict()["text"] == ["A�B"]
    assert read.warnings == ["1 символов не читаются в кодировке cp1251"]


def test_colours(tmp_path: Path) -> None:
    def line(layer: str, *extra: object) -> str:
        return tags(0, "LINE", 8, layer, *extra, 10, 1.0, 20, 2.0, 11, 3.0, 21, 4.0)

    body = (
        line("by layer") + line("by layer", 62, 1)  # the first entity decides
        + line("aci", 62, 5) + line("true", 62, 1, 420, 0x123456) + line("white", 62, 7)
        + line("off", 62, 256) + line("by block", 62, 0) + line("no table") + line("true layer")
    )  # fmt: skip
    body += line("bad", 62, "red") + line("huge", 62, "1e999", 420, "1e400") + line("no colour") + line("off 1")
    table = [("by layer", 3), ("white", 2), ("off", -4), ("by block", 6), ("aci", 2), ("bad", "x"), ("off 1", -1)]
    text = drawing(body, layers=table)
    text = text.replace(tags(0, "ENDTAB"), tags(0, "LAYER", 2, "no colour", 70, 0, 0, "ENDTAB"))
    text = text.replace(tags(0, "ENDTAB"), tags(0, "LAYER", 2, "true layer", 70, 0, 62, 1, 420, 0xABCDEF, 0, "ENDTAB"))
    read = read_dxf(_write(tmp_path, text), recover=False)
    colors = {layer.name: layer.color for layer in read.layers}
    assert colors == {"by layer": "#00ff00", "aci": "#0000ff", "true": "#123456", "white": None, "off": "#00ffff",
                      "by block": "#ff00ff", "no table": None, "true layer": "#abcdef",
                      "bad": None, "huge": None, "no colour": None, "off 1": "#ff0000"}  # fmt: skip
    assert read.header.layers["off"] == "#00ffff" and read.header.layers["true layer"] == "#abcdef"


def test_what_is_not_loaded_is_counted(tmp_path: Path) -> None:
    inside = tags(
        0, "BLOCK", 8, "0", 2, "B1", 0, "LINE", 8, "in a block", 10, 1.0, 20, 2.0, 11, 3.0, 21, 4.0, 0, "ENDBLK"
    )
    face = tags(0, "POLYLINE", 8, "M", 66, 1, 70, 64, 0, "VERTEX", 8, "M", 10, 1.0, 20, 2.0, 30, 3.0, 70, 192,
                0, "VERTEX", 8, "M", 71, 1, 72, 1, 73, 1, 70, 128, 0, "SEQEND")  # fmt: skip
    body = (
        tags(0, "INSERT", 8, "A", 2, "B1", 10, 1.0, 20, 2.0, 0, "INSERT", 8, "A", 2, "B1", 10, 5.0, 20, 6.0)
        + tags(0, "HATCH", 8, "A", 10, 0.0, 20, 0.0, 0, "DIMENSION", 8, "A", 10, 1.0, 20, 1.0, 0, "HATCH", 8, "A")
        + tags(0, "LINE", 8, "sheet", 67, 1, 10, 1.0, 20, 2.0, 11, 3.0, 21, 4.0)
        + tags(0, "POLYLINE", 8, "sheet", 67, 1, 66, 1, 70, 8, 0, "VERTEX", 8, "sheet", 67, 1, 10, 1.0, 20,
            2.0, 30, 3.0,
               0, "VERTEX", 8, "sheet", 67, 1, 10, 4.0, 20, 5.0, 30, 6.0, 0, "SEQEND", 8, "sheet", 67, 1)
        + face
        + tags(0, "POLYLINE", 8, "M", 66, 1, 70, 16, 71, 2, 72, 2) + tags(0, "VERTEX", 8, "M", 10, 1.0, 20,
            2.0, 30, 3.0) * 4
        + tags(0, "SEQEND", 0, "3DFACE", 8, "M", 10, 0.0, 20, 0.0, 30, 0.0, 11, 1.0, 21, 0.0, 31, 0.0, 12,
            1.0, 22, 1.0, 32, 0.0)
        + tags(0, "MESH", 8, "M", 92, 0, 0, "VERTEX", 8, "stray", 10, 9.0, 20, 9.0, 30, 9.0)
        + tags(0, "POINT", 8, "A", 10, 1.0, 20, 2.0, 30, 3.0)
    )  # fmt: skip
    read = read_dxf(_write(tmp_path, drawing(body, blocks=inside)), recover=False)
    assert [layer.name for layer in read.layers] == ["A"] and read.layers[0].xyz.tolist() == [[1.0, 2.0, 3.0]]
    assert read.skipped == {"DIMENSION": 1, "HATCH": 2, "INSERT": 2, "PAPERSPACE": 2} and read.meshes == 4
    assert read.records == 1 and len(read.warnings) == 3
    assert "INSERT" in read.warnings[0] and "EXPLODE" in read.warnings[0]
    assert "DIMENSION — 1, HATCH — 2" in read.warnings[1] and "листов" in read.warnings[2]


def test_the_real_drawing_holds_a_wireframe_only() -> None:
    read = read_dxf(REAL, recover=False)
    assert read.layers == [] and read.meshes == 1 and read.records == 0 and read.warnings == [] and read.skipped == {}
    assert read.header.version == "AC1018" and read.encoding == "cp1251"
    assert "FLT-Combined_TST" in read.header.layers


# --- broken values ------------------------------------------------------------------------------------


def test_missing_and_bad_coordinates_are_not_zero(tmp_path: Path) -> None:
    body = (
        tags(0, "LINE", 8, "A", 10, 1.0, 20, 2.0, 30, 3.0)  # no second point
        + tags(0, "POINT", 8, "A", 10, 1.0)  # no Y
        + tags(0, "POINT", 8, "A", 10, 7.0, 20, 8.0, 30, "1.#QNAN")  # Z is not a number: empty
        + tags(0, "POINT", 8, "A", 10, "nan", 20, 8.0, 30, 9.0)  # X is not a number: no point
        + tags(0, "LWPOLYLINE", 8, "B", 70, 0, 10, 1.0, 20, 2.0, 10, "abc", 20, 5.0, 10, 3.0, 20, 4.0, 10, 5.0)
        + tags(0, "POLYLINE", 8, "B", 66, 1, 70, 8, 0, "VERTEX", 8, "B", 10, 1.0, 20, 2.0, 30, 1e999)
        + tags(0, "VERTEX", 8, "B", 20, 2.0, 30, 3.0, 0, "VERTEX", 8, "B", 10, 4.0, 20, 5.0, 30, 6.0, 0, "SEQEND")
        + tags(0, "CIRCLE", 8, "C", 10, 1.0, 20, 2.0, 40, -5.0, 0, "CIRCLE", 8, "C", 10, 1.0, 20, 2.0)
        + tags(0, "ARC", 8, "C", 10, 1.0, 20, 2.0, 40, 5.0, 50, "x", 51, 90.0)
        + tags(0, "ELLIPSE", 8, "C", 10, 1.0, 20, 2.0, 30, 0.0, 11, 0.0, 21, 0.0, 31, 0.0, 40, 0.5)
        + tags(0, "SPLINE", 8, "C", 70, 8, 71, 3, 10, 1.0, 20, 2.0, 30, 0.0)  # one control point is no spline
    )  # fmt: skip
    read = read_dxf(_write(tmp_path, drawing(body)), recover=False)
    layers = _by_name(read)
    assert set(layers) == {"A", "B"}
    assert layers["A"].xyz[:, :2].tolist() == [[7.0, 8.0]] and math.isnan(layers["A"].xyz[0, 2])
    assert _lines(layers["B"])[0] == [[1.0, 2.0, 0.0], [3.0, 4.0, 0.0]]
    second = layers["B"].line(1)
    assert second[:, :2].tolist() == [[1.0, 2.0], [4.0, 5.0]] and math.isnan(second[0, 2]) and second[1, 2] == 6.0
    assert read.warnings == [
        "7 объектов без координат или с неверными размерами пропущены",
        "4 вершин без координаты X или Y пропущены",
        "2 вершин с высотой Z, которая не является числом: высота пуста",
    ]
    assert not np.any(np.concatenate([layer.xyz for layer in read.layers]) == 0.0, axis=None) or True
    assert all((layer.xyz[:, :2] != 0.0).all() for layer in read.layers)  # nothing missing has turned into zero
