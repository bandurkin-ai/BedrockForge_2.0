"""Wavefront OBJ: faces of every form, wireframes by `o` / `g`, the cycle write → read bit for bit, damaged files,
limits (ТЗ 7.21, 9.12; ADR 0013 §2, substep 8.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.formats import mesh_io
from bf_core.formats.errors import FormatError
from bf_core.formats.meshset import MeshSet
from bf_core.formats.obj import inspect_obj, read_obj, write_obj

from ..meshutil import UTM, cube, grid, icosphere

X0, Y0, Z0 = 466570.58600000001, 4584699.697000001, 412.30000000000001  # every digit of a double in use


def _write(tmp_path: Path, text: str | bytes, name: str = "a.obj") -> Path:
    path = tmp_path / name
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return path


def _fails(path: Path, call: Any = read_obj) -> FormatError:
    with pytest.raises(FormatError) as info:
        found = call(path)
        if call is read_obj:
            list(found)
    error = info.value
    assert error.message and error.hint and any("а" <= ch <= "я" for ch in error.message.lower())
    return error


def _reference(text: str) -> dict[str | None, list[list[tuple[float, float, float]]]]:
    """A Python loop over the lines (BF `read_obj`): the triangles of every wireframe as corner coordinates."""
    v: list[tuple[float, float, float]] = []
    out: dict[str | None, list[list[tuple[float, float, float]]]] = {}
    current: str | None = None
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v":
            v.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif parts[0] in ("o", "g"):
            current = line.strip()[1:].strip() or None
        elif parts[0] == "f":
            idx = [int(tok.split("/")[0]) for tok in parts[1:]]
            idx = [i - 1 if i > 0 else len(v) + i for i in idx]
            if len(idx) > 3:  # repeats of the corner before are left out
                idx = [c for k, c in enumerate(idx) if k == 0 or c != idx[k - 1]]
                while len(idx) > 1 and idx[-1] == idx[0]:
                    idx.pop()
            for k in range(1, len(idx) - 1):
                out.setdefault(current, []).append([v[idx[0]], v[idx[k]], v[idx[k + 1]]])
    return out


def test_faces_of_every_form_and_wireframes_by_name(tmp_path: Path) -> None:
    text = "\r\n".join(
        [
            "# made by hand",
            "mtllib a.mtl",
            f"v {X0!r} {Y0!r} {Z0!r}",
            f"v {X0 + 1.5!r} {Y0!r} {Z0!r} 1.0",  # a weight after the coordinates
            f"v {X0 + 1.5!r} {Y0 + 2.5!r} {Z0 + 0.1!r} 0.5 0.5 0.5",  # a colour
            f"v {X0!r} {Y0 + 2.5!r} {Z0!r}",
            "vt 0.5 0.5",
            "vn 0 0 1",
            "f 1 2 3",  # before any name: the wireframe of the file
            "o Рудное тело 1",
            "# color #FF792A",
            "usemtl ore",
            "s off",
            "f 1/1 2/1 3/1 4/1",  # a quad: two triangles
            "g Разлом",
            "f -4//1 -3//1 -1//1",  # counted back from the last vertex
            "v 1e3 -2.5E+2 +7",
            "v .5 5. -0.0",
            "f 1/1/1 5/1/1 6/1/1 3/1/1 2/1/1",  # a polygon: a fan
            "g",  # no name: the wireframe of the file again
            "f -1 -2 -6",
            "o Рудное тело 1",  # met again: goes on
            "f 4 3 2",
            "l 1 2 3",
            "p 4",
            "",
        ]
    )
    path = _write(tmp_path, b"\xef\xbb\xbf" + text.encode("utf-8"), "Карьер.obj")
    meshes = list(read_obj(path))
    assert [m.name for m in meshes] == ["Карьер", "Рудное тело 1", "Разлом"]
    want = _reference(text)
    for mesh, raw in zip(meshes, (None, "Рудное тело 1", "Разлом"), strict=True):
        soup = np.array(want[raw])
        assert mesh.xyz[mesh.tri].tobytes() == soup.tobytes()
        assert mesh.source == {"format": "obj", "name": raw, **({"as": "Карьер"} if raw is None else {})}
        assert mesh.xyz.dtype == np.float64 and mesh.tri.dtype == np.int32 and mesh.attrs == []
    file_mesh, ore, fault = meshes
    assert file_mesh.tri.tolist() == [[0, 1, 2], [4, 3, 0]] and file_mesh.point_count == 5
    assert file_mesh.xyz[3].tolist() == [1000.0, -250.0, 7.0] and file_mesh.xyz[4].tolist() == [0.5, 5.0, -0.0]
    assert np.signbit(file_mesh.xyz[4, 2])
    assert ore.tri.tolist() == [[0, 1, 2], [0, 2, 3], [3, 2, 1]] and ore.color == "#ff792a"
    assert fault.triangle_count == 1 + 3 and fault.color is None and fault.point_count == 6
    # the vertices of a wireframe are those its faces use, in the order of the file
    assert fault.xyz.tolist() == [list(map(float, line.split()[1:4])) for line in text.splitlines() if line[:2] == "v "]
    assert file_mesh.warnings == ["Линии и точки OBJ (строки «l» и «p») не загружаются: 2"]
    assert ore.warnings == [] and fault.warnings == []
    entries, warnings = inspect_obj(path)
    assert [(e.key, e.name, e.points, e.triangles, e.color) for e in entries] == [
        (0, "Карьер", None, 2, None), (1, "Рудное тело 1", None, 3, "#ff792a"), (2, "Разлом", None, 4, None)
    ]  # fmt: skip
    assert warnings == file_mesh.warnings
    assert [m.name for m in read_obj(path, keys=[2, 0], name="Свой")] == ["Свой", "Разлом"]


def test_cycle_is_bit_for_bit(tmp_path: Path) -> None:
    rng = np.random.default_rng(5)
    v, t = cube(3, 10.0)
    v = v + rng.random(v.shape)  # 16–17 significant digits
    v[0] = [-0.0, 1e-300, -1.7976931348623157e308]
    relief = grid(30, 20, lambda x, y: np.sin(x) * 3.0 + y / 7.0)
    meshes = [
        MeshSet("Тело  №1\tруда", v, t, color="#123abc", attrs=[{"name": "Сорт", "value": 2}]),
        MeshSet("Relief", *relief),
        MeshSet("Relief", *icosphere(1, 5.0)),  # the same name: written under another
    ]
    path = tmp_path / "out.obj"
    seen: list[float] = []
    written = write_obj(path, iter(meshes), lambda f, m=None: seen.append(f), total=3)
    assert (written.meshes, written.files) == (3, [path]) and written.size == path.stat().st_size
    assert written.points == sum(m.point_count for m in meshes)
    assert written.triangles == sum(m.triangle_count for m in meshes)
    assert len(written.warnings) == 2 and "Атрибуты" in written.warnings[0]
    assert "«Relief» → «Relief (2)»" in written.warnings[1]
    assert seen == sorted(seen) and seen[0] == 0.0 and seen[-1] == 1.0 and not list(tmp_path.glob("*.part"))
    back = list(read_obj(path))
    assert [m.name for m in back] == ["Тело №1 руда", "Relief", "Relief (2)"]
    for a, b in zip(meshes, back, strict=True):
        assert a.xyz.tobytes() == b.xyz.tobytes() and a.tri.tobytes() == b.tri.tobytes()
        assert a.color == b.color and b.warnings == []
    again = tmp_path / "again.obj"
    write_obj(again, back)
    assert again.read_bytes() == path.read_bytes()
    assert path.read_bytes().startswith(b"# BedrockForge 2\no \xd0\xa2")  # UTF-8, line feeds
    # a mesh with repeated and unused vertices: the triangles come back on the same coordinates
    loose = MeshSet("L", np.vstack([v, v[:5], UTM]), np.vstack([t, t[:3] + 0]), color=None)
    loose.tri[-1] = [len(v), len(v) + 1, len(v) + 2]
    write_obj(path, [loose])
    (found,) = read_obj(path)
    assert found.xyz[found.tri].tobytes() == loose.xyz[loose.tri].tobytes() and found.point_count == len(v)
    assert found.warnings == ["Совпадающих вершин сшито: 3"]


def test_against_a_python_loop_and_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(9)
    lines: list[str] = []
    count = 0
    for step in range(400):
        for _ in range(int(rng.integers(1, 6))):
            x, y, z = (rng.random(3) * 1000.0 + [X0, Y0, Z0]).tolist()
            lines.append(f"v {x!r} {y!r} {z!r}")
            count += 1
        if step % 37 == 0:
            lines.append(f"{'og'[step % 2]} group {step % 5}")
        if count >= 6:
            for _ in range(int(rng.integers(0, 4))):
                k = int(rng.integers(3, 7))
                idx = rng.choice(count, k, replace=False)
                tokens = [str(int(i) + 1) if rng.random() < 0.6 else str(int(i) - count) for i in idx]
                form = int(rng.integers(0, 4))
                tokens = [(t, f"{t}/1", f"{t}/1/2", f"{t}//2")[form] for t in tokens]
                lines.append("f " + " ".join(tokens))
    text = "\n".join(lines)  # no line feed at the end
    path = _write(tmp_path, text)
    want = _reference(text)
    whole = list(read_obj(path))
    assert len(whole) == len(want) == 5 and sum(m.triangle_count for m in whole) > 1000
    for mesh in whole:
        assert mesh.xyz[mesh.tri].tobytes() == np.array(want[mesh.source["name"]]).tobytes()
        assert mesh.warnings == []
    for size in (64, 1000):
        monkeypatch.setattr(mesh_io, "BLOCK_BYTES", size)
        cut = list(read_obj(path))
        for a, b in zip(whole, cut, strict=True):
            assert a.xyz.tobytes() == b.xyz.tobytes() and a.tri.tobytes() == b.tri.tobytes() and a.name == b.name
        assert [e.triangles for e in inspect_obj(path)[0]] == [m.triangle_count for m in whole]


def test_faces_that_cannot_be_read_are_left_out_and_counted(tmp_path: Path) -> None:
    text = "\n".join(
        [
            "v 0 0 0", "v 1 0 0", "v 0 1 0", "v 0 0 abc", "v 5 5",  # the last two have no coordinates
            "o A",
            "f 1 2 3", "f 1 2 4", "f 1 2 5", "f 1 2", "f 1 2 9", "f 1 2 0", "f 1 x 3", "f 1 1 2 2", "f -9 1 2",
            "f 3 2 1 1",
            "o B",
            "f 1 2 4",
            "o C",
            "f 2 3 1",
            "f 1 2 3 99999999999999999999",
        ]
    )  # fmt: skip
    path = _write(tmp_path, text)
    a, c = read_obj(path)
    assert (a.name, c.name) == ("A", "C") and a.tri.tolist() == [[0, 1, 2], [2, 1, 0]] and c.tri.tolist() == [[1, 2, 0]]
    assert a.xyz.tolist() == [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert a.warnings == [
        "Граней с номерами вне списка вершин или меньше чем с тремя разными вершинами: 6 — пропущены",
        "Треугольников на вершинах без координат: 2 — пропущены (вершин без координат: 2)",
    ]
    assert len(c.warnings) == 1 and ": 1 — пропущены" in c.warnings[0]
    assert [(e.name, e.triangles) for e in inspect_obj(path)[0]] == [("A", 11), ("B", 1), ("C", 3)]  # as written
    only_bad = _write(tmp_path, "v 0 0 0\nv 1 1 1\nf 1 2\nf 1 2 7\n", "bad.obj")
    assert _fails(only_bad).code == "no_meshes"
    assert _fails(_write(tmp_path, "v 0 0 0\nv 1 1 1\nf 1 2\n", "two.obj")).code == "no_meshes"


def test_files_that_are_not_obj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _fails(_write(tmp_path, b"")).code == "empty_file"
    assert _fails(_write(tmp_path, b""), inspect_obj).code == "empty_file"
    assert _fails(_write(tmp_path, b"solid\x00\x00\x01binary" * 10)).code == "not_obj"
    assert _fails(_write(tmp_path, "v 1 2 3\n".encode("utf-16"))).code == "not_obj"
    assert _fails(_write(tmp_path, "v 1 2 3\nv 4 5 6\n# no faces\n")).code == "no_meshes"
    assert _fails(_write(tmp_path, "just a text\nwith lines\n"), inspect_obj).code == "no_meshes"
    # a file cut short: the last face is broken — the rest is read, the loss is told
    whole = "v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\nf 1 2 3\nf 2 4 3\n"
    (mesh,) = read_obj(_write(tmp_path, whole[:-4]))
    assert mesh.triangle_count == 1 and "1 — пропущены" in mesh.warnings[0]
    # a name that is not UTF-8 is read as Windows-1251
    (mesh,) = read_obj(_write(tmp_path, whole.encode() + "o Тело\nf 1 2 3\n".encode("cp1251")), keys=[1])
    assert mesh.name == "Тело" and mesh.warnings == [
        "Названия каркасов записаны не в UTF-8: прочитаны как Windows-1251"
    ]
    # limits: triangles, a line without an end
    many = "v 0 0 0\nv 1 0 0\nv 0 1 0\n" + "f 1 2 3\n" * 50
    monkeypatch.setattr("bf_core.formats.obj.MAX_TRIANGLES", 49)
    error = _fails(_write(tmp_path, many))
    assert error.code == "too_large" and "треугольников" in error.message
    assert _fails(_write(tmp_path, "v 0 0 0\nv 1 0 0\nv 0 1 0\nf " + "1 2 3 " * 18), inspect_obj).code == "too_large"
    monkeypatch.setattr(mesh_io, "MAX_LINE_BYTES", 100)
    monkeypatch.setattr(mesh_io, "BLOCK_BYTES", 64)
    assert _fails(_write(tmp_path, "v 0 0 0\nf " + "1 " * 400)).code == "too_large"


def test_writing_failures_and_cancellation(tmp_path: Path) -> None:
    path = tmp_path / "a.obj"
    with pytest.raises(FormatError) as info:
        write_obj(path, [])
    assert info.value.code == "no_meshes" and not path.exists() and not list(tmp_path.iterdir())
    v, t = cube(2, 1.0)

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None = None) -> None:
        if fraction > 0.6:
            raise Cancelled

    path.write_bytes(b"old")
    with pytest.raises(Cancelled):
        write_obj(path, [MeshSet("A", v, t), MeshSet("B", v, t)], cancel)
    assert path.read_bytes() == b"old" and [p.name for p in tmp_path.iterdir()] == ["a.obj"]
    write_obj(path, [MeshSet("A", v, t)])
    with pytest.raises(Cancelled):
        list(read_obj(path, progress=cancel))
