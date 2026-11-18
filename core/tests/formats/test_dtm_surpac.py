"""Surpac DTM with its string file: points by their numbers in the STR, objects and trisolations, the cycle
write → read bit for bit, a missing or cut file, limits (ТЗ 9.12; ADR 0013 §2, substep 8.6). No sample of the
format was at hand: the files here follow its description."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.formats import mesh_io
from bf_core.formats.dtm_surpac import inspect_dtm, read_dtm, write_dtm
from bf_core.formats.errors import FormatError
from bf_core.formats.meshset import MeshSet
from bf_core.formats.str_surpac import read_surpac

from ..meshutil import cube, grid, icosphere

DAY = dt.date(2026, 11, 18)
# string 1: a closed ring of four points (the first one repeated), string 2: two spot heights, a segment of
# string 1 again; the points are numbered 1…9 through the file whatever string they are in
STR = """pit,18-Nov-26,surface,
0, 0.000, 0.000, 0.000, 0.000, 0.000, 0.000
1, 4584700.250, 466570.500, 412.300, crest
1, 4584700.250, 466580.500, 412.300,
1, 4584710.250, 466580.500, 413.125,
1, 4584710.250, 466570.500, 412.300,
1, 4584700.250, 466570.500, 412.300,
0, 0.000, 0.000, 0.000,
2, 4584705.250, 466575.500, 420.000, spot
0, 0.000, 0.000, 0.000,
2, 4584705.250, 466575.500, , no height
0, 0.000, 0.000, 0.000,
1, 4584720.250, 466570.500, 412.300,
1, 4584720.250, 466580.500, 412.300,
0, 0.000, 0.000, 0.000,
0, 0.000, 0.000, 0.000, END
"""
DTM = """pit.str,ssi_checksum=12345;
0, 0.000, 0.000, 0.000, END
OBJECT, 1,
TRISOLATION, 1, neighbours=no,validated=true,closed=no
1, 1, 2, 6, 0, 0, 0,
2, 2, 3, 6, 1, 0, 0,
3, 3, 4, 6, 2, 0, 0,
4, 4, 5, 6, 3, 0, 0,
TRISOLATION, 2, neighbours=no,validated=true,closed=no
1, 4, 3, 9, 0, 0, 0,
2, 4, 9, 8, 0, 0, 0,
OBJECT, 7,
TRISOLATION, 1, neighbours=no,validated=false,closed=no
1, 1, 2, 3, 0, 0, 0,
2, 1, 3, 7, 0, 0, 0,
3, 1, 3, 12, 0, 0, 0,
this line is not a triangle
END
"""
P = [  # the points of STR by their numbers from 1: x, y, z
    None,
    (466570.5, 4584700.25, 412.3), (466580.5, 4584700.25, 412.3), (466580.5, 4584710.25, 413.125),
    (466570.5, 4584710.25, 412.3), (466570.5, 4584700.25, 412.3), (466575.5, 4584705.25, 420.0), None,
    (466570.5, 4584720.25, 412.3), (466580.5, 4584720.25, 412.3),
]  # fmt: skip


def _pair(tmp_path: Path, dtm: str = DTM, strings: str | None = STR, stem: str = "pit") -> Path:
    path = tmp_path / f"{stem}.dtm"
    path.write_bytes(dtm.replace("\n", "\r\n").encode("cp1251"))
    if strings is not None:
        (tmp_path / "PIT.STR").write_bytes(strings.replace("\n", "\r\n").encode("cp1251"))
    return path


def _fails(path: Path, call: Any = read_dtm, **options: Any) -> FormatError:
    with pytest.raises(FormatError) as info:
        found = call(path, **options)
        if call is read_dtm:
            list(found)
    error = info.value
    assert error.message and error.hint and any("а" <= ch <= "я" for ch in error.message.lower())
    return error


def test_points_are_named_by_their_numbers_in_the_string_file(tmp_path: Path) -> None:
    path = _pair(tmp_path)
    first, second, third = read_dtm(path)
    assert [m.name for m in (first, second, third)] == [
        "pit: объект 1, часть 1",
        "pit: объект 1, часть 2",
        "pit: объект 7",
    ]
    want = [[P[a], P[b], P[c]] for a, b, c in ((1, 2, 6), (2, 3, 6), (3, 4, 6), (4, 5, 6))]
    assert first.xyz[first.tri].tolist() == np.array(want).tolist()
    # point 5 repeats point 1 (the ring is closed): welded, the first of them stays
    assert first.tri.tolist() == [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]] and first.point_count == 5
    assert second.xyz[second.tri].tolist() == np.array([[P[4], P[3], P[9]], [P[4], P[9], P[8]]]).tolist()
    assert third.tri.tolist() == [[0, 1, 2]]  # the triangles on a point without a height and beyond the file are out
    assert first.warnings == [
        "Строк DTM, которые не читаются как треугольники: 1 — пропущены",
        "Файл стрингов «PIT.STR»: 1 координат не являются числами: прочитаны как пустые",
        "Совпадающих вершин сшито: 1",
    ]
    assert second.warnings == [] and third.warnings == [
        "Треугольников на точках, которых нет в файле стрингов: 1 — пропущены",
        "Треугольников на вершинах без координат: 1 — пропущены (вершин без координат: 1)",
    ]
    assert third.source == {
        "format": "surpac_dtm", "name": None, "as": "pit: объект 7", "object": 7, "trisolation": 1, "strings": "PIT.STR"
    }  # fmt: skip
    assert first.xyz.dtype == np.float64 and first.tri.dtype == np.int32 and first.color is None
    entries, warnings = inspect_dtm(path)
    assert [(e.key, e.name, e.points, e.triangles) for e in entries] == [
        (0, first.name, None, 4), (1, second.name, None, 2), (2, third.name, None, 3)
    ]  # fmt: skip
    assert warnings == first.warnings[:1]
    assert [m.name for m in read_dtm(path, keys=[2], name="Карьер")] == ["Карьер: объект 7"]
    # the string file given by its path; a DTM of one part is named after the file
    one = "x.str,\n0, 0.000, 0.000, 0.000, END\n1, 1, 2, 3, 0, 0, 0,\nEND\n"
    alone = _pair(tmp_path / "sub" if (tmp_path / "sub").mkdir() is None else tmp_path, one, None, "Поверхность")
    (mesh,) = read_dtm(alone, strings=tmp_path / "PIT.STR")
    assert mesh.name == "Поверхность" and mesh.tri.tolist() == [[0, 1, 2]] and mesh.source["object"] == 1


def test_cycle_is_bit_for_bit(tmp_path: Path) -> None:
    rng = np.random.default_rng(4)
    v, t = cube(3, 10.0)
    v = v + rng.random(v.shape)  # 16–17 significant digits on UTM coordinates
    v[0, 2] = -0.0
    meshes = [
        MeshSet("Тело", v, t, color="#ff0000", attrs=[{"name": "Сорт", "value": 1}], stats={"closed": True}),
        MeshSet("Рельеф", *grid(15, 11, lambda x, y: np.sin(x) + y / 3.0)),
        MeshSet("Сфера", *icosphere(1, 7.0)),
    ]
    path = tmp_path / "out.dtm"
    seen: list[float] = []
    written = write_dtm(path, iter(meshes), lambda f, m=None: seen.append(f), total=3, date=DAY)
    assert written.files == [path, tmp_path / "out.str"] and (written.meshes, written.triangles) == (3, 108 + 330 + 80)
    assert written.points == sum(m.point_count for m in meshes)
    assert written.size == path.stat().st_size + (tmp_path / "out.str").stat().st_size
    assert (
        len(written.warnings) == 2 and "Атрибуты" in written.warnings[0] and "Названия и цвета" in written.warnings[1]
    )
    assert seen == sorted(seen) and seen[0] == 0.0 and seen[-1] == 1.0 and not list(tmp_path.glob("*.part"))
    text = path.read_bytes().decode("ascii")
    lines = text.split("\r\n")
    assert lines[:5] == [
        "out.str,", "0, 0.000, 0.000, 0.000, END", "OBJECT, 1,",
        "TRISOLATION, 1, neighbours=no,validated=false,closed=yes", "1, {}, {}, {}, 0, 0, 0,".format(*(t[0] + 1)),
    ]  # fmt: skip
    assert lines[-2:] == ["END", ""] and "TRISOLATION, 1, neighbours=no,validated=false,closed=no" in lines
    # the string file is one the Э7 reader takes as its own: every vertex a point of the string of its object
    strings = read_surpac(tmp_path / "out.str")
    assert strings.header.exact and strings.warnings == [] and strings.records == written.points
    assert [layer.name for layer in strings.layers] == ["Стринг 1", "Стринг 2", "Стринг 3"]
    assert strings.layers[0].xyz.tobytes() == v.tobytes()
    back = list(read_dtm(path))
    assert [m.name for m in back] == ["out: объект 1", "out: объект 2", "out: объект 3"]
    for a, b in zip(meshes, back, strict=True):
        assert a.xyz.tobytes() == b.xyz.tobytes() and a.tri.tobytes() == b.tri.tobytes() and b.warnings == []
    # written again from what was read: the same bytes, no warnings; the numbers of the objects stay
    again = tmp_path / "again.dtm"
    rewritten = write_dtm(again, [back[2], back[0]], strings=tmp_path / "points.str", date=DAY)
    assert rewritten.warnings == [] and rewritten.files == [again, tmp_path / "points.str"]
    assert [m.source["object"] for m in read_dtm(again)] == [3, 1]  # found by the name in the first line
    write_dtm(tmp_path / "out2.dtm", back, date=DAY)
    assert (tmp_path / "out2.dtm").read_bytes() == path.read_bytes().replace(b"out.str", b"out2.str")
    assert (tmp_path / "out2.str").read_bytes() == (tmp_path / "out.str").read_bytes()


def test_blocks_do_not_change_the_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "s.dtm"
    write_dtm(path, [MeshSet("A", *icosphere(2, 5.0)), MeshSet("B", *cube(4, 3.0))], date=DAY)
    whole = list(read_dtm(path))
    monkeypatch.setattr(mesh_io, "BLOCK_BYTES", 100)
    cut = list(read_dtm(path))
    for a, b in zip(whole, cut, strict=True):
        assert a.xyz.tobytes() == b.xyz.tobytes() and a.tri.tobytes() == b.tri.tobytes() and a.source == b.source
    assert [e.triangles for e in inspect_dtm(path)[0]] == [320, 192]


def test_files_that_cannot_be_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _pair(tmp_path, strings=None)
    error = _fails(path)
    assert error.code == "no_strings" and "pit.str" in error.message and ".str" in error.hint
    assert _fails(path, inspect_dtm).code == "no_strings"
    path = _pair(tmp_path)
    (tmp_path / "empty.dtm").write_bytes(b"")
    assert _fails(tmp_path / "empty.dtm").code == "empty_file"
    (tmp_path / "bin.dtm").write_bytes(b"\x00\x01\x02binary dtm" * 30)
    assert _fails(tmp_path / "bin.dtm").code == "unsupported"
    cut = DTM[: DTM.index("OBJECT, 7")]
    assert _fails(_pair(tmp_path, cut)).code == "truncated"  # no END line
    assert _fails(_pair(tmp_path, cut), inspect_dtm).code == "truncated"
    none = "pit.str,\n0, 0.000, 0.000, 0.000, END\nOBJECT, 1,\nTRISOLATION, 1, closed=no\nEND\n"
    assert _fails(_pair(tmp_path, none)).code == "no_meshes"
    far = "pit.str,\n0, 0.000, 0.000, 0.000, END\n1, 100, 200, 300, 0, 0, 0,\nEND\n"
    assert _fails(_pair(tmp_path, far)).code == "no_meshes"  # every triangle is beyond the string file
    # the string file is cut short: its own error
    (tmp_path / "PIT.STR").write_bytes(STR[: STR.index("0, 0.000, 0.000, 0.000, END")].encode("cp1251"))
    assert _fails(_pair(tmp_path, strings=None)).code == "truncated"
    _pair(tmp_path)
    monkeypatch.setattr("bf_core.formats.dtm_surpac.MAX_TRIANGLES", 5)
    assert _fails(path).code == "too_large"


def test_writing_failures_and_cancellation(tmp_path: Path) -> None:
    path = tmp_path / "a.dtm"
    with pytest.raises(FormatError) as info:
        write_dtm(path, [])
    assert info.value.code == "no_meshes" and not list(tmp_path.iterdir())
    v, t = cube(2, 1.0)

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None = None) -> None:
        if fraction > 0.6:
            raise Cancelled

    path.write_bytes(b"old")
    (tmp_path / "a.str").write_bytes(b"old points")
    with pytest.raises(Cancelled):
        write_dtm(path, [MeshSet("A", v, t), MeshSet("B", v, t)], cancel)
    assert path.read_bytes() == b"old" and (tmp_path / "a.str").read_bytes() == b"old points"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.dtm", "a.str"]
    write_dtm(path, [MeshSet("A", v, t)])
    with pytest.raises(Cancelled):
        list(read_dtm(path, progress=cancel))
