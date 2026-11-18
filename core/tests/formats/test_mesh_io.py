"""What the mesh readers share: the tokens of a text file against `str.split`, numbers against `float` and `int`,
polygons against a Python fan; damaged text files never crash a reader (ADR 0013 §2, substeps 8.5–8.6)."""

from __future__ import annotations

import datetime as dt
import random
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.formats import _meshkernel as k
from bf_core.formats import mesh_io
from bf_core.formats.dtm_surpac import read_dtm, write_dtm
from bf_core.formats.errors import FormatError
from bf_core.formats.mesh_io import TextBlocks, tidy, unique_name, word
from bf_core.formats.meshset import MeshSet
from bf_core.formats.obj import read_obj, write_obj
from bf_core.formats.stl import read_stl, write_stl

from ..meshutil import cube, icosphere

NUMBERS = [
    "0", "-0.0", "1", "+1.5", "-2.5E+2", "1e3", ".5", "5.", "466570.58600000001", "4584699.697000001",
    "0.30000000000000004", "1.7976931348623157e308", "5e-324", "2.2250738585072014e-308", "1e-400",
    "123456789012345678", "nan", "inf", "-inf", "abc", "1.2.3", "--1", "1e", "0x10", "1,5", "", "９",
]  # fmt: skip


def _tokens(tmp_path: Path, text: bytes, comma: bool = False, block: int | None = None) -> list[Any]:
    path = tmp_path / "t.txt"
    path.write_bytes(text)
    return list(TextBlocks(path, comma=comma, block_bytes=block))


@pytest.mark.parametrize("block", [16, 50, 1 << 20])
@pytest.mark.parametrize("comma", [False, True])
def test_tokens_are_those_of_split(tmp_path: Path, block: int, comma: bool) -> None:
    rnd = random.Random(3)
    parts = [
        "v",
        "f",
        "1/2/3",
        "-7//2",
        "OBJECT,",
        "1,",
        "0.5",
        "x" * 40,
        "",
        "\t",
        " ",
        "\r",
        "a,b,,c",
        "Тело",
        "\x00",
    ]
    lines = [" ".join(rnd.choice(parts) for _ in range(rnd.randrange(0, 7))) for _ in range(300)]
    for text in ("\n".join(lines), "\r\n".join(lines) + "\r\n", "", "\n\n", "one"):
        raw = text.encode("utf-8")
        blocks = _tokens(tmp_path, raw, comma, block)
        found = [
            [b.raw[b.start[t] : b.end[t]] for t in range(b.first[i], b.first[i + 1])]
            for b in blocks
            for i in range(b.lines)
        ]
        table = bytes.maketrans(b",\x00", b"  ") if comma else bytes.maketrans(b"\x00", b" ")
        want = [line.translate(table).split() for line in raw.split(b"\n")]
        if want and not want[-1] and (raw.endswith(b"\n") or not raw):
            want.pop()  # no line after the last line feed
        assert found == want and [b.base for b in blocks] == np.cumsum([0] + [b.lines for b in blocks])[:-1].tolist()


def test_numbers_are_those_of_python(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    exact = [repr(float(x)) for x in np.concatenate([rng.random(500) * 1e7, rng.standard_normal(500) * 1e-5])]
    for texts in (exact, NUMBERS + exact[:50]):  # the second one has texts Arrow refuses: the way of float()
        (b,) = _tokens(tmp_path, " ".join(t for t in texts if t).encode("utf-8"))
        texts = [t for t in texts if t]
        got = b.floats(np.arange(len(texts)))
        for text, value in zip(texts, got.tolist(), strict=True):
            try:
                want = float(text.encode("utf-8"))  # the bytes of the file: a digit outside ASCII is not one
            except ValueError:
                want = float("nan")
            assert (value != value and want != want) or np.float64(value).tobytes() == np.float64(want).tobytes(), text
    whole = [
        "0",
        "7",
        "-7",
        "+12",
        "123456789012345678",
        "1234567890123456789",
        "12/5/7",
        "-3//2",
        "1.5",
        "x",
        "-",
        "1e3",
    ]
    (b,) = _tokens(tmp_path, " ".join(whole).encode())
    plain, corner = b.ints(np.arange(len(whole))).tolist(), b.ints(np.arange(len(whole)), slash=True).tolist()
    assert plain == [0, 7, -7, 12, 123456789012345678, k.MISSING, k.MISSING, k.MISSING, k.MISSING, k.MISSING, k.MISSING,
                     k.MISSING]  # fmt: skip
    assert corner[6:8] == [12, -3] and corner[:6] == plain[:6] and corner[8:] == plain[8:]
    assert b.words(np.arange(3)).tolist() == [word(b"0"), word(b"7"), word(b"-7")]
    (b,) = _tokens(tmp_path, b"Vertex ENDSOLID trisolation TRISOLAT v")
    assert b.words(np.arange(5)).tolist() == [word(b"vertex"), word(b"endsolid"), word(b"TRISOLATION")] * 1 + [
        word(b"trisolation"), word(b"v")
    ]  # fmt: skip
    assert b.text(1, 3) == b"ENDSOLID trisolation TRISOLAT"


def test_fan_against_a_python_loop() -> None:
    rnd = random.Random(1)
    polygons = [[rnd.randrange(-1, 9) for _ in range(rnd.randrange(0, 8))] for _ in range(2000)]
    polygons += [[5, 5, 5], [1, 2, 3, 1], [1, 1, 2, 3, 3, 1, 1], [4, 5, 4, 5], [7, 7, 7, 7]]
    want: list[tuple[int, int, int]] = []
    owners: list[int] = []
    bad = 0
    for i, p in enumerate(polygons):
        if len(p) < 3 or min(p) < 0:
            bad += 1
            continue
        if len(p) > 3:
            p = [c for j, c in enumerate(p) if j == 0 or c != p[j - 1]]
            while len(p) > 1 and p[-1] == p[0]:
                p.pop()
        made = [(p[0], p[j], p[j + 1]) for j in range(1, len(p) - 1)]
        bad += not made
        want += made
        owners += [i] * len(made)
    counts = np.array([len(p) for p in polygons], dtype=np.int64)
    corners = np.array([c for p in polygons for c in p], dtype=np.int64)
    tri = np.empty((int(np.maximum(counts - 2, 0).sum()), 3), dtype=np.int64)
    owner = np.empty(len(tri), dtype=np.int64)
    n, left = k.fan(corners, counts, tri, owner)
    assert (int(n), int(left)) == (len(want), bad) and tri[:n].tolist() == [list(t) for t in want]
    assert owner[:n].tolist() == owners
    # the face list of a DXF MESH
    listed = np.array([3, 0, 1, 2, 4, 2, 3, 9, 1.5, 2, 0, 1], dtype=np.float64)
    counts, corners = np.empty(len(listed), dtype=np.int64), np.empty(len(listed), dtype=np.int64)
    assert k.face_list(listed, 5, counts, corners) == (3, 9, True)
    assert counts[:3].tolist() == [3, 4, 2] and corners[:9].tolist() == [0, 1, 2, 2, 3, -1, -1, 0, 1]
    assert k.face_list(listed[:6], 5, counts, corners) == (1, 3, False)  # the list ends inside a face
    assert k.face_list(np.array([0.0, 1, 2]), 5, counts, corners) == (0, 0, False)
    assert k.face_list(np.array([np.nan, 1, 2]), 5, counts, corners) == (0, 0, False)


def test_tidy_and_names() -> None:
    xyz = np.array([[0, 0, 0], [9, 9, 9], [1, 0, 0], [0, 1, 0], [np.nan, 0, 0], [-0.0, 0, 0], [0, 0, 1]], dtype=float)
    tri = np.array([[0, 2, 3], [5, 3, 6], [4, 2, 3], [6, 3, 2]])
    notes: list[str] = []
    v, t = tidy(xyz, tri, notes)
    assert v.tolist() == [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]] and t.tolist() == [
        [0, 1, 2],
        [0, 2, 3],
        [3, 2, 1],
    ]
    assert (
        v.dtype == np.float64
        and t.dtype == np.int32
        and notes
        == [
            "Треугольников на вершинах без координат: 1 — пропущены (вершин без координат: 1)",
            "Совпадающих вершин сшито: 1",
        ]
    )
    taken: set[str] = set()
    assert [unique_name(n, taken) for n in ("Тело", "тело", "ТЕЛО", "Тело (2)", "x")] == [
        "Тело", "тело (2)", "ТЕЛО (3)", "Тело (2) (2)", "x"
    ]  # fmt: skip
    assert unique_name("a" * 200, {"a" * 200}) == "a" * 196 + " (2)"


def _damage(
    tmp_path: Path, source: Path, read: Callable[[Path], Any], codes: set[str], seed: int, binary: bool = False
) -> dict[str, int]:
    """Random damage to a file: the reader gives wireframes or a FormatError, nothing else."""
    data = source.read_bytes()
    rnd = random.Random(seed)
    junk = [b"", b"abc", b"\x00\xff", b"f 1 2", b"v 1 2", b"nan", b"1e999", b"-1", b"0", b"99999999999999999999",
            b"endsolid", b"solid x", b"END", b"OBJECT, 2,", b"TRISOLATION", b"f 1 2 3 4 5 -1"]  # fmt: skip
    outcomes: dict[str, int] = {}
    path = tmp_path / ("damaged" + source.suffix)
    for _ in range(150):
        if binary:
            damaged_bytes = bytearray(data)
            for _ in range(rnd.choice([1, 2, 20])):
                damaged_bytes[rnd.randrange(len(damaged_bytes))] = rnd.randrange(256)
            if rnd.random() < 0.3:
                damaged_bytes = damaged_bytes[: rnd.randrange(len(damaged_bytes))]
            path.write_bytes(bytes(damaged_bytes))
        else:
            lines = data.split(b"\n")
            for _ in range(rnd.choice([1, 1, 2, 5, 30])):
                i = rnd.randrange(len(lines)) if lines else 0
                if not lines:
                    break
                roll = rnd.random()
                if roll < 0.5:
                    lines[i] = rnd.choice(junk)
                elif roll < 0.7:
                    del lines[i]
                elif roll < 0.9:
                    words = lines[i].split()
                    if words:
                        words[rnd.randrange(len(words))] = rnd.choice(junk)
                    lines[i] = b" ".join(words)
                else:
                    lines = lines[: max(i, 1)]
            path.write_bytes(b"\n".join(lines))
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            try:
                meshes = list(read(path))
            except FormatError as error:
                assert error.message and error.hint and error.code in codes | {"empty_file"}, error.code
                outcomes[error.code] = outcomes.get(error.code, 0) + 1
                continue
        outcomes["ok"] = outcomes.get("ok", 0) + 1
        for mesh in meshes:
            assert np.isfinite(mesh.xyz).all() and mesh.triangle_count > 0
            assert mesh.tri.min() >= 0 and mesh.tri.max() < mesh.point_count
            assert len(np.unique(mesh.tri)) == mesh.point_count
    return outcomes


def test_damaged_files_never_crash_a_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mesh_io, "BLOCK_BYTES", 4096)  # several blocks a file
    meshes = [MeshSet("A", *cube(3, 5.0)), MeshSet("B", *icosphere(2, 4.0))]
    obj, text, binary, dtm = (tmp_path / n for n in ("a.obj", "t.stl", "b.stl", "d.dtm"))
    write_obj(obj, meshes)
    write_stl(text, meshes, binary=False)
    write_stl(binary, meshes)
    write_dtm(dtm, meshes, date=dt.date(2026, 11, 18))
    found = _damage(tmp_path, obj, read_obj, {"no_meshes", "not_obj", "too_large"}, 1)
    assert found["ok"] > 100
    found = _damage(tmp_path, text, read_stl, {"truncated", "broken", "no_meshes", "not_stl", "too_large"}, 2)
    assert found["ok"] > 30 and found.get("truncated", 0) + found.get("broken", 0) > 10
    found = _damage(tmp_path, binary, read_stl, {"truncated", "broken", "no_meshes", "not_stl", "too_large"}, 3, True)
    assert found["ok"] > 50 and found["truncated"] > 10

    def pair(path: Path) -> Any:
        return read_dtm(path, strings=tmp_path / "d.str")

    found = _damage(tmp_path, dtm, pair, {"truncated", "no_meshes", "unsupported", "too_large"}, 4)
    assert found["ok"] > 60 and found["truncated"] > 5
