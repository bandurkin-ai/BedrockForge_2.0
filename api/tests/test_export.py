"""Writers of formats/export.py: the round trip «import → export → import» is lossless.

Tolerances: none. Coordinates are compared with `np.array_equal` (bit for bit) in every format:
Micromine STR stores binary doubles, DXF and OBJ store repr() text.
"""

import subprocess
import time
from pathlib import Path

import ezdxf
import numpy as np
import pytest

from app.formats.export import ExportError, write_dxf_lines, write_dxf_mesh, write_micromine_str, write_obj
from app.formats.lines import LineLayer, Polyline, read_lines
from app.formats.micromine import read_micromine
from app.formats.wireframes import mesh_stats, read_wireframes

REPO = Path(__file__).resolve().parents[2]
REAL_BRANCH = "origin/real_data"

# UTM-like coordinates with more digits than a decimal field of a few places would keep.
E0, N0 = 512345.678901234, 6789012.345678901


def utm(points) -> np.ndarray:
    p = np.array(points, dtype=np.float64)
    return p + np.array([E0, N0, 0.0]) + np.array([1 / 3, 1 / 7, 1 / 11])


def sample_layers() -> list[LineLayer]:
    square = utm([(0, 0, 100), (10, 0, 100.5), (10, 10, 101), (0, 10, 100.25)])
    closed_repeated = np.vstack([square + 50, square[:1] + 50])  # as the readers return a closed line
    return [
        LineLayer("Контуры участков", [
            Polyline(square, True, {"Участок": "Восточный", "Запасы": 12.5, "Год": 2024, "Содержание": 1 / 3}),
            Polyline(closed_repeated, True, {"Участок": None, "Запасы": None, "Год": 2025, "Содержание": 2.5e-7}),
            Polyline(utm([(0, 0, 5), (1e-3, 2e-3, 5), (7, 7, 7.125)]), False,
                     {"Участок": "Цители-Сопели №2", "Запасы": 0.125, "Год": None, "Содержание": -1e300}),
        ], "#ff7a2a"),
        LineLayer("FAULTS", [
            Polyline(utm([(100, 100, 0), (200, 150, -35.75)]), False, {"Участок": "Fault A"}),
            Polyline(utm([(300, 300, 1)]), False, {"Участок": "одна точка"}),  # a single-vertex line
        ], "#00aa55"),
    ]


def closed_as_read(line: Polyline) -> np.ndarray:
    """Vertices the readers return: a closed line repeats its first vertex at the end."""
    v = np.asarray(line.vertices, dtype=np.float64)
    if line.closed and not np.array_equal(v[0], v[-1]):
        v = np.vstack([v, v[:1]])
    return v


def assert_same_geometry(written: list[LineLayer], read: list[LineLayer]) -> None:
    """Same layers, line order, closed flags and vertices bit for bit, single points included."""
    expected = [(layer.name, list(layer.lines)) for layer in written]
    assert [layer.name for layer in read] == [name for name, _ in expected]
    for layer, (_, lines) in zip(read, expected):
        assert len(layer.lines) == len(lines)
        for got, want in zip(layer.lines, lines):
            assert got.closed == want.closed
            assert got.vertices.dtype == np.float64
            assert np.array_equal(got.vertices, closed_as_read(want))


# ---------------------------------------------------------------- Micromine STR


def test_str_round_trip_is_bit_exact_with_attributes(tmp_path):
    layers = sample_layers()
    path = tmp_path / "lines.STR"

    result = write_micromine_str(path, layers)

    assert result["lines"] == 5 and result["points"] == 5 + 5 + 3 + 2 + 1
    assert result["coordinate_tolerance"] == 0.0
    assert result["fields"] == ["EAST", "NORTH", "RL", "JOIN", "STRING", "Участок", "Запасы", "Год", "Содержание"]
    assert result["single_point_lines"] == 1

    kind, read = read_lines(path, ".str")
    assert kind == "Micromine STR"
    assert_same_geometry(layers, read)

    # Attributes: text and numbers come back equal; a missing number is None, a missing text is
    # an empty string (a C field has no other «empty»); JOIN is the number of the line.
    first, second, third = read[0].lines
    assert first.attributes == {"Участок": "Восточный", "Запасы": 12.5, "Год": 2024, "Содержание": 1 / 3, "JOIN": 1}
    assert second.attributes == {"Участок": "", "Запасы": None, "Год": 2025, "Содержание": 2.5e-7, "JOIN": 2}
    assert third.attributes == {"Участок": "Цители-Сопели №2", "Запасы": 0.125, "Год": None, "Содержание": -1e300, "JOIN": 3}
    assert read[1].lines[0].attributes == {"Участок": "Fault A", "Запасы": None, "Год": None, "Содержание": None, "JOIN": 4}

    # The single-vertex line is in the file (one record with its own JOIN) and is read back as a point.
    assert len(read[1].lines[-1].vertices) == 1
    frame = read_micromine(path).frame
    assert len(frame) == result["points"]
    assert frame["JOIN"].tolist()[-1] == 5 and frame["Участок"].tolist()[-1] == "одна точка"

    # A second round trip writes the same bytes.
    again = tmp_path / "again.STR"
    write_micromine_str(again, read)
    kind, reread = read_lines(again, ".str")
    assert_same_geometry(read, reread)
    assert [line.attributes for layer in reread for line in layer.lines] == [line.attributes for layer in read for line in layer.lines]


def test_str_layout_matches_micromine_samples(tmp_path):
    path = tmp_path / "layout.STR"
    line = Polyline(np.array([[1.5, 2.5, 3.5], [4.5, 5.5, 6.5]]), False, {"Участок": "А", "N": 7, "X": 0.25})
    write_micromine_str(path, [LineLayer("Без кода", [line])])
    data = path.read_bytes()

    assert data.startswith(b'THIS IS MICROMINE EXTENDED DATA FILE!\x07\x1a\x01\r\n<?xml version="1.0" encoding="utf-8"?>')
    assert b'<data codepage="ibm-5347_P100-1998"/>' in data[:4096]
    assert b'<field name="EAST"/><data field-class="Generic"/>' in data[:4096]
    assert data[:4096].rstrip(b"\x00").endswith(b"</metadata>")
    variables = (b"8   VARIABLES\r\n"
                 b"EAST      R  8  6\r\nNORTH     R  8  6\r\nRL        R  8  6\r\n"
                 b"JOIN      N 12  0\r\nSTRING    C 20  0\r\n"
                 + "Участок   C  1  0\r\n".encode("cp1251") + b"N         N  1  0\r\nX         R  8  6\r\n")
    assert data[4096:4096 + len(variables)] == variables
    body = data[4096 + len(variables):]
    record = 8 * 3 + 12 + 20 + 1 + 1 + 8 + 2
    assert len(body) == 2 * record
    # Doubles, JOIN left-aligned in 12 characters, an empty STRING for the layer without a code.
    assert np.frombuffer(body[:24], dtype="<f8").tolist() == [1.5, 2.5, 3.5]
    assert body[24:24 + 32] == b"1" + b" " * 31
    assert body[56:58] == "А".encode("cp1251") + b"7" and body[record - 2:record] == b"\r\n"


def test_str_keeps_join_numbers_and_renumbers_clashes(tmp_path):
    def line(x, join):
        return Polyline(np.array([[x, 0.0, 0.0], [x + 1, 1.0, 0.0]]), False, {"JOIN": join} if join is not None else {})

    layers = [LineLayer("A", [line(0, 7), line(10, 7), line(20, 3), line(30, None), line(40, "x")]), LineLayer("B", [line(50, 3)])]
    path = tmp_path / "join.STR"
    write_micromine_str(path, layers)
    _, read = read_lines(path, ".str")
    assert [ln.attributes["JOIN"] for ln in read[0].lines] == [7, 8, 3, 9, 10]  # a clash and the lines without a number get new ones
    assert [ln.attributes["JOIN"] for ln in read[1].lines] == [3]  # another STRING: the same number is still a new line
    assert_same_geometry(layers, read)


def test_str_many_long_named_fields(tmp_path):
    attributes = {f"Очень длинное имя атрибута {i}": f"значение {i}" for i in range(40)}
    attributes["east"] = "reserved"  # would shadow the coordinate field in the reader
    layers = [LineLayer("L", [Polyline(utm([(0, 0, 0), (1, 1, 1)]), False, attributes)])]
    path = tmp_path / "wide.STR"
    result = write_micromine_str(path, layers)

    assert result["skipped_attributes"] == ["east"]
    names = result["fields"][5:]
    assert len(names) == 40 and len(set(n.upper() for n in names)) == 40 and all(len(n) <= 10 for n in names)
    assert result["renamed_fields"]["Очень длинное имя атрибута 0"] == names[0]
    assert path.read_bytes()[4096:8192].rstrip(b"\x00").endswith(b"</metadata>")  # the header takes two blocks
    _, read = read_lines(path, ".str")
    assert_same_geometry(layers, read)
    got = read[0].lines[0].attributes
    assert [got[n] for n in names] == [f"значение {i}" for i in range(40)]


def test_str_errors_are_reported_in_russian(tmp_path):
    path = tmp_path / "bad.STR"
    with pytest.raises(ExportError, match="Нет линий"):
        write_micromine_str(path, [LineLayer("L", [])])
    with pytest.raises(ExportError, match="Windows-1251"):
        write_micromine_str(path, [LineLayer("L", [Polyline(np.zeros((2, 3)), False, {"a": "≥ 5 г/т"})])])
    with pytest.raises(ExportError, match="координатах"):
        write_micromine_str(path, [LineLayer("L", [Polyline(np.array([[0.0, np.nan, 0.0], [1.0, 1.0, 0.0]]), False)])])
    with pytest.raises(ExportError, match="длиннее 255"):
        write_micromine_str(path, [LineLayer("L", [Polyline(np.zeros((2, 3)), False, {"a": "я" * 300})])])
    assert not path.exists() and not list(tmp_path.iterdir())  # no half-written files


# ---------------------------------------------------------------- DXF lines


def test_dxf_lines_round_trip_is_bit_exact(tmp_path):
    layers = sample_layers()
    path = tmp_path / "lines.dxf"

    result = write_dxf_lines(path, layers)

    assert result["lines"] == 4 and result["single_points"] == 1
    assert result["layers"] == ["Контуры участков", "FAULTS"]

    kind, read = read_lines(path, ".dxf")
    assert kind == "DXF"
    assert_same_geometry(layers, read)
    assert [layer.color for layer in read] == ["#ff7a2a", "#00aa55"]  # true colour, exact
    assert all(line.attributes == {"entity": "POINT" if len(line.vertices) == 1 else "POLYLINE"} for layer in read for line in layer.lines)

    # A valid drawing of the sample's version: every entity where it belongs, nothing for the audit to fix.
    doc = ezdxf.readfile(path)
    assert doc.dxfversion == "AC1018" and doc.header["$DWGCODEPAGE"] == "ANSI_1251"
    assert doc.audit().errors == []
    kinds = [e.dxftype() for e in doc.modelspace()]
    assert kinds == ["POLYLINE"] * 4 + ["POINT"]
    assert [e.is_closed for e in doc.modelspace() if e.dxftype() == "POLYLINE"] == [True, True, False, False]
    point = doc.modelspace()[-1]
    assert tuple(point.dxf.location) == tuple(layers[1].lines[1].vertices[0])
    assert point.dxf.layer == "FAULTS" and doc.layers.get("FAULTS").rgb == (0, 0xAA, 0x55)
    assert b"\r\n" in path.read_bytes()[:20]


def test_dxf_lines_layer_names_and_errors(tmp_path):
    path = tmp_path / "names.dxf"
    line = Polyline(np.array([[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]]), False)
    result = write_dxf_lines(path, [LineLayer("a/b:c", [line]), LineLayer("A_B_C", [line]), LineLayer("0", [line])])
    assert result["layers"] == ["a_b_c", "A_B_C (2)", "0"]  # characters DXF forbids; names differ without regard to case
    _, read = read_lines(path, ".dxf")
    assert [layer.name for layer in read] == result["layers"]
    with pytest.raises(ExportError, match="Нет линий"):
        write_dxf_lines(path, [])
    with pytest.raises(ExportError, match="координатах"):
        write_dxf_lines(path, [LineLayer("L", [Polyline(np.array([[np.inf, 0.0, 0.0], [1.0, 1.0, 0.0]]), False)])])


# ---------------------------------------------------------------- meshes


def torus(nu: int, nv: int, offset=(E0, N0, 250.0), seed: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """A closed solid (2·nu·nv triangles) at UTM coordinates, vertices jittered to use every digit."""
    u, v = np.meshgrid(np.arange(nu) * 2 * np.pi / nu, np.arange(nv) * 2 * np.pi / nv, indexing="ij")
    r = 100 + 30 * np.cos(v)
    vertices = np.column_stack([(r * np.cos(u)).ravel(), (r * np.sin(u)).ravel(), (30 * np.sin(v)).ravel()])
    vertices += np.random.default_rng(seed).uniform(-0.01, 0.01, vertices.shape) + np.array(offset)
    i, j = np.meshgrid(np.arange(nu), np.arange(nv), indexing="ij")
    a, b = (i * nv + j).ravel(), (((i + 1) % nu) * nv + j).ravel()
    c, d = (((i + 1) % nu) * nv + (j + 1) % nv).ravel(), (i * nv + (j + 1) % nv).ravel()
    return vertices, np.vstack([np.column_stack([a, b, c]), np.column_stack([a, c, d])]).astype(np.int32)


def sorted_rows(a: np.ndarray) -> np.ndarray:
    return a[np.lexsort(a.T[::-1])]


def triangle_set(vertices: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Triangles as coordinate triples in a canonical order: each starts at its smallest corner
    (the winding is kept), and the triangles are sorted. Independent of vertex numbering."""
    corners = vertices[triangles]  # (m, 3, 3)
    m = len(corners)
    keys = np.lexsort((corners[:, :, 2].ravel(), corners[:, :, 1].ravel(), corners[:, :, 0].ravel(),
                       np.repeat(np.arange(m), 3))).reshape(m, 3)
    first = keys[:, 0] - np.arange(m) * 3
    rolled = corners[np.arange(m)[:, None], (first[:, None] + np.arange(3)) % 3]
    return sorted_rows(rolled.reshape(m, 9))


def test_dxf_mesh_round_trip(tmp_path):
    big = torus(150, 100)  # 30 000 triangles: three polyfaces
    small = torus(12, 8, offset=(E0 + 500, N0 - 500, -40.5), seed=2)
    meshes = [("Рудное тело 1", *big, "#ff7a2a"), ("Fault/2", *small, None)]
    path = tmp_path / "mesh.dxf"

    result = write_dxf_mesh(path, meshes)

    assert result["triangles"] == 30_000 + 192 and result["polyfaces"] == 3 + 1
    assert result["layers"] == ["Рудное тело 1", "Fault_2"]
    kind, read = read_wireframes(path, ".dxf")
    assert kind == "DXF" and [m.name for m in read] == result["layers"]
    assert [m.color for m in read] == ["#ff7a2a", None]
    for got, (_, vertices, triangles, _) in zip(read, meshes):
        assert np.array_equal(sorted_rows(got.vertices), sorted_rows(vertices))  # bit-exact, re-welded order
        assert np.array_equal(triangle_set(got.vertices, got.triangles), triangle_set(vertices, triangles))
        stats = mesh_stats(vertices, triangles)
        assert stats["closed"] and stats["volume"] > 0
        assert mesh_stats(got.vertices, got.triangles) == stats

    doc = ezdxf.readfile(path)
    assert doc.dxfversion == "AC1018" and doc.audit().errors == []
    polylines = list(doc.modelspace())
    assert len(polylines) == 4 and all(p.is_poly_face_mesh for p in polylines)
    assert max(p.dxf.m_count for p in polylines) <= 32767 and max(p.dxf.n_count for p in polylines) <= 32767


def test_obj_round_trip(tmp_path):
    big = torus(60, 40)
    small = torus(12, 8, offset=(E0 + 500, N0 - 500, -40.5), seed=2)
    meshes = [("Рудное тело 1", *big, "#ff7a2a"), ("Fault 2", *small, None)]
    path = tmp_path / "mesh.obj"

    result = write_obj(path, meshes)

    assert result["vertices"] == 2400 + 96 and result["triangles"] == 4800 + 192
    kind, read = read_wireframes(path, ".obj")
    assert kind == "OBJ" and [m.name for m in read] == ["Рудное тело 1", "Fault 2"]
    for got, (_, vertices, triangles, _) in zip(read, meshes):
        assert np.array_equal(got.vertices, vertices)  # same order, bit-exact
        assert np.array_equal(got.triangles, triangles)
        assert mesh_stats(got.vertices, got.triangles) == mesh_stats(vertices, triangles)
    text = path.read_text(encoding="utf-8")
    assert "o Рудное тело 1\n# color #ff7a2a\n" in text
    assert text.splitlines()[-1] == "f {} {} {}".format(*(small[1][-1] + 2400 + 1))  # numbers count the whole file


def test_mesh_errors(tmp_path):
    v, t = torus(4, 4)
    for writer in (write_dxf_mesh, write_obj):
        path = tmp_path / "bad.out"
        with pytest.raises(ExportError, match="Нет каркасов"):
            writer(path, [])
        with pytest.raises(ExportError, match="вне диапазона"):
            writer(path, [("m", v, t + 1, None)])
        with pytest.raises(ExportError, match="координатах"):
            writer(path, [("m", np.where(v > v.max() - 1e-9, np.nan, v), t, None)])
        assert not list(tmp_path.iterdir())


# ---------------------------------------------------------------- real files of the user


def real_file(tmp_path: Path, suffix: str) -> list[Path]:
    """Files of docs/ on the real-data branch with this suffix, extracted to the temporary
    directory; the test is skipped when git or the branch is not available."""
    def git(*args: str) -> bytes:
        try:
            done = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            pytest.skip(f"git is not available: {exc}")
        if done.returncode != 0:
            pytest.skip(f"{REAL_BRANCH} is not available")
        return done.stdout

    names = [n for n in git("ls-tree", "--name-only", "-z", REAL_BRANCH, "docs/").decode("utf-8").split("\0")
             if n.lower().endswith(suffix)]
    if not names:
        pytest.skip(f"no {suffix} files on {REAL_BRANCH}")
    source = tmp_path / "real"
    source.mkdir()
    paths = []
    for name in names:
        path = source / Path(name).name
        path.write_bytes(git("show", f"{REAL_BRANCH}:{name}"))
        paths.append(path)
    return paths


def test_real_str_files_round_trip(tmp_path):
    for n, source in enumerate(real_file(tmp_path, ".str")):
        _, layers = read_lines(source, ".str")
        target = tmp_path / f"out{n}.STR"
        result = write_micromine_str(target, layers)

        _, read = read_lines(target, ".str")
        assert_same_geometry(layers, read)
        assert [line.attributes for layer in read for line in layer.lines] == [line.attributes for layer in layers for line in layer.lines]
        assert result["coordinate_tolerance"] == 0.0
        # These files have no attributes and no single-point lines: the export is the same file.
        assert target.read_bytes() == source.read_bytes()

        # The same lines through DXF.
        dxf = tmp_path / f"out{n}.dxf"
        write_dxf_lines(dxf, layers)
        _, read = read_lines(dxf, ".dxf")
        assert_same_geometry(layers, read)


def test_real_dxf_wireframe_round_trip(tmp_path):
    sources = [p for p in real_file(tmp_path, ".dxf") if p.name == "TST-FAULTS.DXF"]
    if not sources:
        pytest.skip("TST-FAULTS.DXF is not on the branch")
    _, meshes = read_wireframes(sources[0], ".dxf")
    items = [(m.name, m.vertices, m.triangles, m.color) for m in meshes]
    assert sum(len(m.triangles) for m in meshes) == 7417

    for suffix, writer in ((".dxf", write_dxf_mesh), (".obj", write_obj)):
        target = tmp_path / f"faults{suffix}"
        writer(target, items)
        _, read = read_wireframes(target, suffix)
        assert [m.name for m in read] == [m.name for m in meshes]
        for got, want in zip(read, meshes):
            assert np.array_equal(sorted_rows(got.vertices), sorted_rows(want.vertices))
            assert np.array_equal(triangle_set(got.vertices, got.triangles), triangle_set(want.vertices, want.triangles))
            assert mesh_stats(got.vertices, got.triangles) == mesh_stats(want.vertices, want.triangles)
            if suffix == ".dxf":
                assert got.color == want.color


# ---------------------------------------------------------------- speed


def test_speed_one_million_triangles(tmp_path, capsys):
    vertices, triangles = torus(1000, 500)  # 1 000 000 triangles, 500 000 vertices
    meshes = [("Каркас", vertices, triangles, "#3366cc")]
    report = []
    for suffix, writer, limit in ((".obj", write_obj, 30), (".dxf", write_dxf_mesh, 90)):
        path = tmp_path / f"million{suffix}"
        started = time.perf_counter()
        result = writer(path, meshes)
        seconds = time.perf_counter() - started
        assert result["triangles"] == 1_000_000 and result["bytes"] == path.stat().st_size
        assert seconds < limit
        report.append(f"{suffix}: {seconds:.1f} s, {result['bytes'] / 1e6:.0f} MB")

    # The OBJ is read back in full; the DXF is checked by its record counts (reading a million
    # faces with the pure-Python stream reader takes far longer than writing them).
    _, read = read_wireframes(tmp_path / "million.obj", ".obj")
    assert np.array_equal(read[0].vertices, vertices) and np.array_equal(read[0].triangles, triangles)
    data = (tmp_path / "million.dxf").read_bytes()
    assert data.count(b"AcDbFaceRecord") == 1_000_000 and data.rstrip().endswith(b"EOF")
    assert data.count(b"AcDbPolyFaceMeshVertex") == result["vertices"]
    with capsys.disabled():
        print("\n1M triangles — " + "; ".join(report))
