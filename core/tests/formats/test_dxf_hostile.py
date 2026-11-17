"""DXF files that are cut, broken, binary or made to exhaust memory; progress and cancellation (ТЗ 9.12)."""

from __future__ import annotations

import random
import subprocess
import sys
import tracemalloc
import warnings
from pathlib import Path
from typing import Any

import ezdxf
import numpy as np
import pytest

from bf_core.formats import dxf_stream as ds
from bf_core.formats.dxf_lines import Limits, read_dxf
from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet

from .dxf_builder import drawing, tags

X0, Y0 = 466570.58600000001, 4584699.697000001  # UTM-sized coordinates with every digit of a double in use


def _write(tmp_path: Path, content: str | bytes, name: str = "a.dxf", encoding: str = "cp1251") -> Path:
    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode(encoding))
    return path


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


# --- hostile files ----------------------------------------------------------------------------------


def _many_lines(count: int) -> str:
    return "".join(
        tags(0, "LINE", 8, "A", 10, float(i), 20, 2.0, 30, 0.5, 11, 3.0, 21, 4.0, 31, 1.5) for i in range(count)
    )


def test_truncated_file(tmp_path: Path) -> None:
    data = drawing(_many_lines(200)).encode()
    for cut in (len(data) * 2 // 3, len(data) * 2 // 3 + 1, len(data) * 2 // 3 + 7, len(data) - 3):
        path = _write(tmp_path, data[:cut])
        error = _error(path, recover=False)
        assert error.code == "truncated" and "обрезан" in error.message
        read = read_dxf(path)  # the default: what is whole is read, and the result says the file was cut
        assert read.recovered and "обрезан" in read.warnings[0] and len(read.warnings) == 1
        (layer,) = read.layers
        assert 100 < layer.line_count <= 200
        assert layer.xyz[0::2].tolist() == [[float(i), 2.0, 0.5] for i in range(layer.line_count)]
        assert layer.xyz[1::2].tolist() == [[3.0, 4.0, 1.5]] * layer.line_count  # the cut entity did not get zeros
    polyline = tags(0, "POLYLINE", 8, "P", 66, 1, 70, 8) + "".join(
        tags(0, "VERTEX", 8, "P", 10, float(i), 20, 2.0, 30, 3.0) for i in range(30)
    )
    cut = _write(tmp_path, drawing(polyline, end=False)[:-30], "polyline.dxf")
    (layer,) = read_dxf(cut).layers
    assert layer.xyz[:, 0].tolist() == [float(i) for i in range(29)]  # the vertices that are whole
    assert _error(_write(tmp_path, data[:300], "head.dxf")).code == "truncated"  # nothing but a header: an error


def test_bad_groups(tmp_path: Path) -> None:
    lines = drawing(_many_lines(20)).split("\r\n")
    at = lines.index("LINE") + 5
    path = _write(tmp_path, "\r\n".join(lines[:at] + ["1O"] + lines[at + 1 :]))
    error = _error(path, recover=False)
    assert error.code == "broken" and f"Строка {at + 1}" in error.message and "1O" in error.message
    garbage = _write(tmp_path, "\r\n".join(lines[:40] + ["@@@ ###"] * 300), "garbage.dxf")
    assert _error(garbage, recover=False).code == "broken"
    assert _error(garbage).code == "broken"  # ezdxf cannot make a drawing out of it either
    assert _error(_write(tmp_path, b"PK\x03\x04 not a drawing at all", "zip.dxf")).code == "not_dxf"
    assert _error(_write(tmp_path, b"", "empty.dxf")).code == "empty_file"
    assert _error(_write(tmp_path, b"AC1027\x00\x00\x00\x00", "dwg.dxf")).code == "unsupported"


def test_bombs_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, drawing(_many_lines(5000)))
    error = _error(path, recover=False, limits=Limits(vertices=9999))
    assert error.code == "too_large" and "9 999" in error.message
    assert read_dxf(path, recover=False, limits=Limits(vertices=10_000)).point_count == 10_000
    assert _error(path, limits=Limits(vertices=9999)).code == "too_large"  # not retried through ezdxf
    # a few bytes that would become billions of vertices: the count is checked before any memory is taken
    for bomb in (
        tags(0, "CIRCLE", 8, "A", 10, 0.0, 20, 0.0, 40, 1e30),
        tags(0, "ARC", 8, "A", 10, 0.0, 20, 0.0, 40, 1e22, 50, 0.0, 51, 359.0),
        tags(0, "ELLIPSE", 8, "A", 10, 0.0, 20, 0.0, 30, 0.0, 11, 1e25, 21, 0.0, 31, 0.0, 40, 0.5),
        tags(0, "LWPOLYLINE", 8, "A", 70, 0, 10, 0.0, 20, 0.0, 42, 1e-9, 10, 1e25, 20, 0.0),
        tags(0, "CIRCLE", 8, "A", 10, 0.0, 20, 0.0, 40, 3e6) * 2000,
        tags(0, "SPLINE", 8, "A", 70, 8, 71, 3, 10, 0.0, 20, 0.0, 30, 0.0, 10, 1e20, 20, 1e22, 30, 0.0,
             10, -1e21, 20, 1e20, 30, 0.0, 10, 1e22, 20, -1e22, 30, 0.0),
    ):  # fmt: skip
        tracemalloc.start()
        error = _error(_write(tmp_path, drawing(bomb), "bomb.dxf"), recover=False)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        assert error.code == "too_large" and "20 000 000" in error.message and peak < 300 << 20
    texts = tags(0, "TEXT", 8, "T", 10, 1.0, 20, 2.0, 40, 1.0, 1, "x") * 11
    assert _error(_write(tmp_path, drawing(texts), "texts.dxf"), limits=Limits(texts=10)).code == "too_large"
    record = tags(0, "LWPOLYLINE", 8, "A", 70, 0) + tags(10, 1.0, 20, 2.0) * 3000
    monkeypatch.setattr(ds, "BLOCK_BYTES", 1024)  # a record may outgrow a block, but not without an end
    assert (
        _error(_write(tmp_path, drawing(record), "record.dxf"), limits=Limits(record_bytes=10_000)).code == "too_large"
    )


def test_binary_dxf_is_read_through_ezdxf(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    points = [(X0 + 0.1 * i, Y0 - i / 3, 100 + i / 7) for i in range(50)]
    msp.add_polyline3d(points, dxfattribs={"layer": "Линия"})
    msp.add_lwpolyline([(X0, Y0), (X0 + 10, Y0), (X0 + 10, Y0 + 10)], close=True, dxfattribs={"layer": "Контур"})
    msp.add_point((X0, Y0, 1 / 3), dxfattribs={"layer": "Точки"})
    msp.add_text("Скв. 1", dxfattribs={"layer": "Точки", "insert": (X0, Y0, 5.0), "height": 2.0})
    msp.add_circle((X0, Y0, 3.0), 25.0, dxfattribs={"layer": "Круг"})
    text, binary = tmp_path / "text.dxf", tmp_path / "binary.dxf"
    doc.saveas(text)
    doc.saveas(binary, fmt="bin")
    assert binary.read_bytes().startswith(b"AutoCAD Binary DXF")
    error = _error(binary, recover=False)
    assert error.code == "binary" and "восстановления" in error.message
    want, got = read_dxf(text, recover=False), read_dxf(binary)
    assert got.recovered and not want.recovered and got.warnings == []
    assert (
        [layer.name for layer in got.layers]
        == [layer.name for layer in want.layers]
        == ["Линия", "Контур", "Точки", "Круг"]
    )
    for a, b in zip(got.layers, want.layers, strict=True):
        assert a.xyz.tobytes() == b.xyz.tobytes()  # the doubles of the binary file, bit for bit
        assert _attrs(a) == _attrs(b)
    assert got.layers[0].xyz.tolist() == [list(p) for p in points]
    forced = read_dxf(text, recover=True)  # a good file through ezdxf gives the same
    assert forced.recovered and forced.layers[0].xyz.tobytes() == want.layers[0].xyz.tobytes()
    assert _error(binary, limits=Limits(recover_bytes=1000)).code == "too_large"
    broken = _write(tmp_path, binary.read_bytes()[:40], "cut.dxf")
    assert _error(broken).code == "broken"


def test_progress_and_cancellation(tmp_path: Path) -> None:
    path = _write(tmp_path, drawing(_many_lines(3000)))
    seen: list[tuple[float, str | None]] = []
    read_dxf(path, lambda f, m=None: seen.append((f, m)))
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and fractions[0] == 0.0 and fractions[-1] == 1.0
    assert seen[-1][1] == "Прочитано объектов: 3 000"

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None = None) -> None:
        if fraction > 0.0:
            raise Cancelled

    with pytest.raises(Cancelled):
        read_dxf(path, cancel)


def test_a_million_vertices_in_bounded_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR 0011, 7.5: memory on 1 million vertices — the result (24 MB of xyz) and a few blocks, not the file."""
    rnd = np.random.default_rng(1)
    count = 1_000_000
    xyz = np.column_stack(
        [X0 + rnd.uniform(-5e3, 5e3, count), Y0 + rnd.uniform(-5e3, 5e3, count), rnd.uniform(0, 900, count)]
    )
    path = tmp_path / "million.dxf"
    record = tags(0, "VERTEX", 8, "Рельеф", 10, "%r", 20, "%r", 30, "%r", 70, 32)
    with path.open("w", encoding="cp1251", newline="") as f:
        f.write(drawing("", end=False))
        for a in range(0, count, 1000):  # 1000 polylines of 1000 vertices
            f.write(tags(0, "POLYLINE", 8, "Рельеф", 66, 1, 70, 8))
            f.write((record * 1000) % tuple(xyz[a : a + 1000].ravel().tolist()))
            f.write(tags(0, "SEQEND", 8, "Рельеф"))
        f.write(tags(0, "ENDSEC", 0, "EOF"))
    size = path.stat().st_size
    assert size > 80 << 20
    read_dxf(
        _write(tmp_path, drawing(_many_lines(3)), "warm.dxf"), recover=False
    )  # imports are not the reader's memory
    tracemalloc.start()
    read = read_dxf(path, recover=False)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    (layer,) = read.layers
    assert layer.xyz.tobytes() == xyz.tobytes() and layer.line_count == 1000 and read.warnings == []
    assert layer.seq.tolist() == list(range(count))
    assert peak < 400 << 20 and peak < size * 3  # ezdxf needs several GB for such a file


def test_a_file_ezdxf_can_mend(tmp_path: Path) -> None:
    lines = drawing(_many_lines(20)).split("\r\n")
    at = lines.index("LINE") + 5
    path = _write(tmp_path, "\r\n".join(lines[:at] + [" 20 ; y"] + lines[at + 1 :]))  # a group code with a tail
    assert _error(path, recover=False).code == "broken"
    read = read_dxf(path)  # the default: ezdxf.recover takes the code out of the line
    assert read.recovered and "повреждён" in read.warnings[0] and f"Строка {at + 1}" in read.warnings[0]
    (layer,) = read.layers
    assert layer.xyz[0::2].tolist() == [[float(i), 2.0, 0.5] for i in range(20)]
    forced = read_dxf(path, recover=True)
    assert forced.recovered and forced.warnings == [] and forced.layers[0].xyz.tobytes() == layer.xyz.tobytes()


def test_nothing_heavy_is_imported_until_it_is_needed(tmp_path: Path) -> None:
    path = _write(tmp_path, drawing(_many_lines(50)))
    code = (
        "import sys, json;"
        "from bf_core.formats import dxf_stream, dxf_lines, dxf_layers, dxf_curves, dxf_write;"
        "heavy = ('numba', 'pyarrow', 'ezdxf', 'pandas', 'scipy');"
        "at_import = [m for m in heavy if m in sys.modules];"
        f"read = dxf_lines.read_dxf({str(path)!r}, recover=False);"
        "print(json.dumps([at_import, [m for m in heavy if m in sys.modules], read.point_count]))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    # a small drawing is scanned in Python: numba is not loaded for it, ezdxf — only for colours, texts, recovery
    assert out.strip() == '[[], ["pyarrow"], 100]'


def _every_entity(path: Path) -> None:
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    for i in range(3):
        msp.add_line((i, 2, 3), (4, 5, 6), dxfattribs={"layer": "Слой"})
        msp.add_lwpolyline(
            [(0, 0, 0, 0, 0.5), (10, 0, 0, 0, 0), (10, 10, 0, 0, -1)],
            format="xyseb",
            close=True,
            dxfattribs={"elevation": 5, "extrusion": (0.1, 0.2, 0.9)},
        )
        msp.add_polyline3d([(0, 0, 1), (1, 1, 2), (2, 0, 3)], close=True)
        msp.add_polyline2d([(0, 0), (5, 0), (5, 5)])
        msp.add_point((7, 8, 9))
        msp.add_text("Скв", dxfattribs={"height": 2.5, "rotation": 30, "insert": (1, 2, 3)})
        msp.add_mtext("Строка 1\\PСтрока 2", dxfattribs={"char_height": 1.5, "insert": (4, 5, 6)})
        msp.add_circle((100, 100, 5), 50)
        msp.add_arc((0, 0, 0), 10, 30, 120, dxfattribs={"extrusion": (0, 0, -1)})
        msp.add_ellipse((0, 0, 0), (10, 0, 0), 0.5)
        msp.add_spline([(0, 0, 0), (10, 20, 0), (30, 10, 0), (50, 40, 5)])
        msp.add_open_spline([(0, 0, 0), (10, 20, 0), (30, 10, 0), (50, 40, 5), (60, 0, 0)])
    doc.saveas(path)


@pytest.mark.parametrize("values_only", [True, False])
def test_damaged_drawings_never_crash_the_reader(tmp_path: Path, values_only: bool) -> None:
    """Random damage to a drawing with every entity: the reader answers with layers or with a FormatError,
    never with another exception, a warning of numpy, or coordinates that are not numbers."""
    source = tmp_path / "source.dxf"
    _every_entity(source)
    data = source.read_bytes()
    eol = b"\r\n" if b"\r\n" in data else b"\n"
    lines = data.split(eol)
    first = data[: data.index(b"ENTITIES")].count(b"\n")
    numbers = b"-1|1e999|nan|0|10|999999999999|1.#QNAN|1e30|-1e30|1e-320|8|64|16|3|255"
    junk = [b"", b"abc", b"\x00\xff", b" 70", b"LINE", b"VERTEX", b"SEQEND", b"EOF", *numbers.split(b"|")]
    rnd = random.Random(5 if values_only else 6)
    outcomes: dict[str, int] = {}
    path = tmp_path / "damaged.dxf"
    for _ in range(150):
        damaged = list(lines)
        for _ in range(rnd.choice([1, 2, 5, 30]) if values_only else rnd.choice([1, 1, 2, 5])):
            k = rnd.randrange(first, len(damaged))
            if values_only:  # a value replaced: the structure of the file stays whole
                damaged[k | 1 if k | 1 < len(damaged) else k - 1] = rnd.choice(junk)
            elif rnd.random() < 0.5:
                damaged[k] = rnd.choice(junk)
            elif rnd.random() < 0.5:
                del damaged[k]
            else:
                damaged = damaged[:k]
        path.write_bytes(eol.join(damaged))
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            try:
                read = read_dxf(path, recover=False, limits=Limits(vertices=50_000))
            except FormatError as error:
                assert error.message and error.hint
                outcomes[error.code] = outcomes.get(error.code, 0) + 1
                continue
        outcomes["ok"] = outcomes.get("ok", 0) + 1
        assert all(np.isfinite(layer.xyz[:, :2]).all() and not np.isinf(layer.xyz).any() for layer in read.layers)
    assert set(outcomes) <= {"ok", "broken", "truncated", "too_large"} and outcomes["ok"] >= (
        100 if values_only else 10
    )
