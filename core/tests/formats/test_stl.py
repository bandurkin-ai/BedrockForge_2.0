"""STL, binary and text: detection by size, float32 kept as it is, solids of a text file, the cycle write → read
bit for bit, cut and broken files, limits (ТЗ 7.21, 9.12; ADR 0013 §2, substep 8.6)."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.formats import mesh_io
from bf_core.formats.dxf_write_mesh import in_order_of_use
from bf_core.formats.errors import FormatError
from bf_core.formats.meshset import MeshSet
from bf_core.formats.stl import MARK, RECORD, inspect_stl, read_stl, write_stl

from ..meshutil import UTM, cube, grid, icosphere


def by_use(xyz: np.ndarray, tri: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    used, local = in_order_of_use(tri)
    return xyz[used], (local - 1).astype(np.int32)


def binary_file(corners: np.ndarray, header: bytes = b"", count: int | None = None) -> bytes:
    """A binary STL by `struct`: the reference of the format."""
    tri = np.asarray(corners, dtype=np.float32).reshape(-1, 3, 3)
    out = [header.ljust(80, b"\x00")[:80], struct.pack("<I", len(tri) if count is None else count)]
    for t in tri:
        out.append(struct.pack("<12fH", 0.0, 0.0, 1.0, *t.reshape(-1).tolist(), 0))
    return b"".join(out)


def _write(tmp_path: Path, content: bytes | str, name: str = "a.stl") -> Path:
    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return path


def _fails(path: Path, call: Any = read_stl) -> FormatError:
    with pytest.raises(FormatError) as info:
        found = call(path)
        if call is read_stl:
            list(found)
    error = info.value
    assert error.message and error.hint and any("а" <= ch <= "я" for ch in error.message.lower())
    return error


def test_binary_is_told_by_its_size_and_float32_stays_as_it_is(tmp_path: Path) -> None:
    v, t = cube(2, 10.0, offset=(1000.5, 2000.25, 30.125))
    soup = v[t].astype(np.float32)  # what another program wrote
    # the header begins with «solid», as many binary files do
    path = _write(tmp_path, binary_file(soup, b"solid exported by some program"), "Тело 5.stl")
    (mesh,) = read_stl(path)
    assert mesh.name == "Тело 5" and mesh.triangle_count == len(t) and mesh.point_count == len(v)
    assert mesh.xyz.dtype == np.float64 and mesh.xyz[mesh.tri].tobytes() == soup.astype(np.float64).tobytes()
    assert mesh.source == {
        "format": "stl", "name": None, "as": "Тело 5", "precision": "float32",
        "header": "solid exported by some program",
    }  # fmt: skip
    assert mesh.warnings == [] and mesh.color is None
    first_use = np.unique(mesh.tri.reshape(-1), return_index=True)[1]
    assert (np.diff(first_use) > 0).all()  # welded in the order of use
    entries, warnings = inspect_stl(path)
    assert [(e.key, e.name, e.points, e.triangles) for e in entries] == [(0, "Тело 5", None, len(t))]
    assert warnings == [] and list(read_stl(path, keys=[1])) == []
    assert [m.name for m in read_stl(path, name="Загруженный")] == ["Загруженный"]
    # coordinates of UTM: the step of float32 is told
    far = _write(tmp_path, binary_file(soup + np.float32(6_000_000.0)), "utm.stl")
    (mesh,) = read_stl(far)
    assert len(mesh.warnings) == 1 and "float32" in mesh.warnings[0] and "0,500 м" in mesh.warnings[0]
    # −0.0 is 0.0, a NaN is not a coordinate
    odd = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [-0.0, 0, -0.0], [0, 1, 0], [-1, 0, 0],
                    [np.nan, 0, 0], [1, 0, 0], [0, 1, 0], [np.inf, 0, 0], [1, 1, 1], [2, 2, 2]])  # fmt: skip
    (mesh,) = read_stl(_write(tmp_path, binary_file(odd), "odd.stl"))
    assert mesh.tri.tolist() == [[0, 1, 2], [0, 2, 3]] and "не являются числами: 2 — пропущены" in mesh.warnings[0]


def test_binary_cycle_is_bit_for_bit(tmp_path: Path) -> None:
    v, t = by_use(*icosphere(2, 40.0, centre=(100.0, 200.0, 50.0)))
    exact = MeshSet("Сфера № 1", v.astype(np.float32).astype(np.float64), t, attrs=[{"name": "a", "value": 1}])
    path = tmp_path / "s.stl"
    seen: list[float] = []
    written = write_stl(path, [exact], lambda f, m=None: seen.append(f))
    assert (written.meshes, written.points, written.triangles) == (1, len(v), len(t))
    assert written.warnings == ["Атрибуты каркасов в STL не записываются: каркасов с атрибутами 1"]
    assert written.size == path.stat().st_size == 84 + 50 * len(t) and seen[0] == 0.0 and seen[-1] == 1.0
    raw = path.read_bytes()
    assert raw[:80].rstrip(b"\x00") == MARK + "Сфера № 1".encode() and not raw.startswith(b"solid")
    (back,) = read_stl(path)
    assert back.name == "Сфера № 1" and back.xyz.tobytes() == exact.xyz.tobytes()
    assert back.tri.tobytes() == exact.tri.tobytes() and back.warnings == []
    assert back.source == {"format": "stl", "name": "Сфера № 1", "precision": "float32"}
    again = tmp_path / "again.stl"
    write_stl(again, [back])
    assert again.read_bytes() == raw
    # the normals are unit vectors along the right-hand rule; the attribute bytes are zero
    data = np.frombuffer(raw, dtype=RECORD, offset=84)
    a, b, c = (data["v"][:, i].astype(np.float64) for i in range(3))
    n = np.cross(b - a, c - a)
    n /= np.linalg.norm(n, axis=1)[:, None]
    assert np.abs(data["n"] - n).max() < 1e-6 and not data["a"].any()


def test_binary_rounds_float64_and_says_so(tmp_path: Path) -> None:
    v, t = by_use(*cube(2, 10.0))  # on UTM coordinates
    path = tmp_path / "utm.stl"
    written = write_stl(path, [MeshSet("Куб", v, t), MeshSet("Куб 2", v + 20.0, t)])
    assert written.meshes == 2 and len(written.warnings) == 2
    assert written.warnings[0] == "Двоичный STL хранит одно тело: каркасы (2) записаны как один"
    assert "float32" in written.warnings[1] and "шаг координат — до 0,500 м" in written.warnings[1]
    (back,) = read_stl(path)
    assert back.triangle_count == 2 * len(t) and back.name == "Куб"
    want = np.concatenate([v[t], (v + 20.0)[t]]).astype(np.float32).astype(np.float64)
    assert back.xyz[back.tri].tobytes() == want.tobytes()
    worst = float(np.abs(want - np.concatenate([v[t], (v + 20.0)[t]])).max())
    assert 0.01 < worst <= 0.25 and f"{worst:.3f}".replace(".", ",") in written.warnings[1]
    huge = MeshSet("H", np.array([[0.0, 0, 0], [1e39, 0, 0], [0, 1, 0]]), np.array([[0, 1, 2]], dtype=np.int32))
    with pytest.raises(FormatError) as info:
        write_stl(path, [huge])
    assert info.value.code == "unrepresentable" and len(list(read_stl(path))) == 1  # the file before stays
    long = MeshSet("Очень длинное название каркаса, которое не помещается в заголовок", v, t)
    written = write_stl(path, [long])
    assert "сокращено" in written.warnings[0]
    assert next(read_stl(path)).name == "Очень длинное название каркаса,"  # 60 bytes of UTF-8, whole characters


def test_text_cycle_keeps_float64(tmp_path: Path) -> None:
    rng = np.random.default_rng(2)
    v, t = by_use(*cube(3, 10.0))
    v = v + rng.random(v.shape)  # 16–17 significant digits on UTM coordinates
    relief = by_use(*grid(12, 9, lambda x, y: np.sin(x) + y / 3.0))
    meshes = [MeshSet("Тело 1", v, t, color="#ff0000"), MeshSet("Relief  A", *relief)]
    path = tmp_path / "t.stl"
    written = write_stl(path, iter(meshes), total=2, binary=False)
    assert (written.meshes, written.triangles, written.warnings) == (2, len(t) + len(relief[1]), [])
    text = path.read_text(encoding="utf-8")
    assert text.startswith("solid Тело 1\nfacet normal ") and text.endswith("endsolid Relief A\n")
    back = list(read_stl(path))
    assert [m.name for m in back] == ["Тело 1", "Relief A"]
    for a, b in zip(meshes, back, strict=True):
        assert a.xyz.tobytes() == b.xyz.tobytes() and a.tri.tobytes() == b.tri.tobytes()
        assert b.warnings == [] and b.source == {"format": "stl", "name": b.name}
    again = tmp_path / "again.stl"
    write_stl(again, back, binary=False)
    assert again.read_bytes() == path.read_bytes()
    entries, _ = inspect_stl(path)
    assert [(e.key, e.name, e.triangles) for e in entries] == [(0, "Тело 1", len(t)), (1, "Relief A", len(relief[1]))]
    assert [m.name for m in read_stl(path, keys=[1])] == ["Relief A"]


TEXT = """solid first
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 1.5e0 0 0
      vertex 0 +2.5 0
    endloop
  endfacet
  FACET NORMAL 0 0 1
    OUTER LOOP
      VERTEX 1.5 0 0
      VERTEX 1.5 2.5 -0.0
      VERTEX 0 2.5 0
    ENDLOOP
  ENDFACET
endsolid first
solid
facet normal 0 0 0
outer loop
vertex 1 1 1
vertex 2 1 nan
vertex 1 2 1
endloop
endfacet
facet normal 0 0 0
outer loop
vertex 1 1 1
vertex 2 1 1
vertex 1 2 1
endloop
endfacet
endsolid
solid empty
endsolid empty
"""


def test_text_solids_blocks_and_damage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, TEXT.replace("\n", "\r\n"), "Модель.stl")
    for size in (None, 32, 200):
        if size:
            monkeypatch.setattr(mesh_io, "BLOCK_BYTES", size)
        first, second = read_stl(path)
        assert (first.name, second.name) == ("first", "Модель")
        assert first.tri.tolist() == [[0, 1, 2], [1, 3, 2]]
        assert first.xyz.tolist() == [[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [0.0, 2.5, 0.0], [1.5, 2.5, -0.0]]
        assert second.tri.tolist() == [[0, 1, 2]] and second.warnings == [
            "Треугольников с координатами, которые не являются числами: 1 — пропущены"
        ]
        assert [(e.key, e.name, e.triangles) for e in inspect_stl(path)[0]] == [(0, "first", 2), (1, "Модель", 2)]
    monkeypatch.undo()
    # cut inside a facet, and after one: no «endsolid»
    for cut in (TEXT[: TEXT.index("VERTEX 1.5 2.5")], TEXT[: TEXT.index("  FACET")]):
        error = _fails(_write(tmp_path, cut))
        assert error.code == "truncated" and "endsolid" in error.message
        assert _fails(_write(tmp_path, cut), inspect_stl).code == "truncated"
    # a facet of two vertices
    broken = TEXT.replace("      vertex 0 +2.5 0\n", "")
    error = _fails(_write(tmp_path, broken))
    assert error.code == "broken" and "граней 2" in error.message and "вершин 5" in error.message
    assert _fails(_write(tmp_path, "solid nothing\nendsolid nothing\n")).code == "no_meshes"
    assert _fails(_write(tmp_path, "solid nothing\nendsolid nothing\n"), inspect_stl).code == "no_meshes"


def test_text_keys_build_only_the_solids_asked_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`keys` of a text STL: the solids that are not asked for are counted and checked, but their numbers are not
    read, their corners are not welded and no wireframe is made of them."""
    from bf_core.formats import stl

    rng = np.random.default_rng(5)
    meshes = [
        MeshSet("A", *by_use(*cube(3, 10.0))),
        MeshSet("B", *by_use(*icosphere(2, 7.0))),
        MeshSet("C", *by_use(*grid(9, 7, lambda x, y: np.sin(x) + y / 3.0))),
        MeshSet("D", *by_use(*cube(2, 1.0))),
    ]
    for mesh in meshes:
        mesh.xyz += rng.random(mesh.xyz.shape)  # 16–17 significant digits on UTM coordinates
    path = tmp_path / "four.stl"
    write_stl(path, meshes, binary=False)
    # a solid that is not asked for has a triangle on a height that is not a number: nothing is said of it
    text = path.read_bytes().decode("ascii")
    at = text.index("vertex", text.index("solid B"))
    end = text.index("\n", at)
    path.write_bytes((text[:at] + "vertex 1.0 2.0 высота" + text[end:]).encode("utf-8"))
    whole = list(read_stl(path))
    assert [m.name for m in whole] == ["A", "B", "C", "D"] and len(whole[1].warnings) == 1

    added: list[int] = []
    built: list[str] = []
    add, mesh_of = stl._Solid.add, stl._Solid.mesh
    monkeypatch.setattr(stl._Solid, "add", lambda self, corners: (added.append(len(corners)), add(self, corners))[1])
    monkeypatch.setattr(
        stl._Solid,
        "mesh",
        lambda self, name, source, warnings: (built.append(name), mesh_of(self, name, source, warnings))[1],
    )
    monkeypatch.setattr(mesh_io, "BLOCK_BYTES", 4096)  # the solids span many blocks
    for keys in ([2], [3, 0], [1], [], [7]):
        added.clear(), built.clear()
        got = list(read_stl(path, keys=keys))
        want = [whole[k] for k in sorted(keys) if k < len(whole)]
        assert built == [m.name for m in want] and [m.name for m in got] == built
        assert sum(added) == 3 * sum(m.triangle_count for m in want) + 3 * (1 in keys)  # corners read: only theirs
        for a, b in zip(got, want, strict=True):
            assert a.xyz.tobytes() == b.xyz.tobytes() and a.tri.tobytes() == b.tri.tobytes()
            assert a.source == b.source and a.warnings == b.warnings
    added.clear(), built.clear()
    assert [m.name for m in read_stl(path)] == ["A", "B", "C", "D"] and built == ["A", "B", "C", "D"]
    added.clear(), built.clear()
    assert [(e.key, e.triangles) for e in inspect_stl(path)[0]] == [(k, m.triangle_count) for k, m in enumerate(meshes)]
    assert added == [] and built == []  # the composition reads no geometry at all
    # a solid that is not asked for is still checked: a file cut inside it is an error, not half an answer
    cut = _write(tmp_path, path.read_bytes()[: path.read_bytes().index(b"endsolid D")], "cut.stl")
    with pytest.raises(FormatError) as info:
        list(read_stl(cut, keys=[0]))
    assert info.value.code == "truncated"


def test_files_that_are_not_stl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    soup = np.arange(27, dtype=np.float32).reshape(-1, 3)
    whole = binary_file(soup, b"solid with a header like a text file")
    assert _fails(_write(tmp_path, b"")).code == "empty_file"
    assert _fails(_write(tmp_path, b""), inspect_stl).code == "empty_file"
    # a binary file cut short — with «solid» in its header — is not taken for a text one
    error = _fails(_write(tmp_path, whole[:-30]))
    assert error.code == "truncated" and "3 треугольников" in error.message and "помещается 2" in error.message
    assert _fails(_write(tmp_path, whole[:-30]), inspect_stl).code == "truncated"
    assert _fails(_write(tmp_path, whole[:60])).code == "not_stl"  # cut inside the header: nothing tells an STL
    assert _fails(_write(tmp_path, whole + b"\x00" * 7)).code == "broken"  # more bytes than the header tells
    assert _fails(_write(tmp_path, binary_file(soup[:0]))).code == "no_meshes"
    assert _fails(_write(tmp_path, b"\x00\x01" * 20)).code == "not_stl"
    assert _fails(_write(tmp_path, "v 1 2 3\nf 1 2 3\n" * 20)).code == "not_stl"
    assert _fails(_write(tmp_path, "o\n")).code == "not_stl"
    # a header that tells four billion triangles: nothing is allocated for them
    assert _fails(_write(tmp_path, binary_file(soup, count=0xFFFFFFFF))).code == "truncated"
    monkeypatch.setattr("bf_core.formats.stl.MAX_TRIANGLES", 2)
    assert _fails(_write(tmp_path, whole)).code == "too_large"
    assert _fails(_write(tmp_path, TEXT)).code == "too_large"


def test_writing_failures_and_cancellation(tmp_path: Path) -> None:
    path = tmp_path / "a.stl"
    for binary in (True, False):
        with pytest.raises(FormatError) as info:
            write_stl(path, [], binary=binary)
        assert info.value.code == "no_meshes" and not list(tmp_path.iterdir())
    v, t = by_use(*icosphere(3, 5.0, centre=(0.0, 0.0, 0.0)))

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None = None) -> None:
        if fraction > 0.6:
            raise Cancelled

    path.write_bytes(b"old")
    with pytest.raises(Cancelled):
        write_stl(path, [MeshSet("A", v, t), MeshSet("B", v, t)], cancel)
    assert path.read_bytes() == b"old" and [p.name for p in tmp_path.iterdir()] == ["a.stl"]
    write_stl(path, [MeshSet("A", v + UTM, t)])
    with pytest.raises(Cancelled):
        list(read_stl(path, progress=cancel))
