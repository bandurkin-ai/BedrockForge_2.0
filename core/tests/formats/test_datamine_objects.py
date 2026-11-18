"""Datamine wireframes (`*tr.dm` + `*pt.dm`) and strings on synthetic tables (ТЗ 7.35 Д-13, 9.12; ADR 0013 §2, 8.8).

There are no samples of Datamine wireframes or strings (question № 4 to the owner), so every file here is built
byte by byte from the description of the format (`dm_builder`, independent of the reader) and the reader is
«предварительно». References and tolerances:
  * geometry — the arrays the tables were built from, bit for bit (no tolerance);
  * a cube of side 10 on UTM-sized coordinates: volume 1000 and area 600 within 1e-9 relative (ТЗ 5.4);
  * strings — the vertices given, bit for bit; an absent Z stays NaN.
"""

from __future__ import annotations

import time
import tracemalloc
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.formats.datamine_objects import (
    PRELIMINARY,
    find_pair,
    inspect_dm_wireframes,
    open_dm_wireframes,
    read_dm_strings,
    read_dm_wireframes,
    split_fields,
)
from bf_core.formats.errors import FormatError
from bf_core.geo.meshes import mesh_stats
from tests.formats.dm_builder import ABSENT, F, build, build_numbers
from tests.meshutil import UTM, cube, grid

PT = [F("PID"), F("XP"), F("YP"), F("ZP")]
TR = [F("TRIANGLE"), F("PID1"), F("PID2"), F("PID3")]
STR = [F("PTN"), F("PVALUE"), F("XP"), F("YP"), F("ZP")]


def bits(a: Any) -> np.ndarray:
    return np.ascontiguousarray(a, dtype=np.float64).view(np.uint64)


def write_pair(
    folder: Path,
    v: np.ndarray,
    t: np.ndarray,
    *,
    names: tuple[str, str] = ("oretr.dm", "orept.dm"),
    pids: np.ndarray | None = None,
    fields: tuple[F, ...] = (),
    values: list[list[Any]] | None = None,
    word: int = 8,
) -> tuple[Path, Path]:
    """The two tables of a wireframe: `pids` — the number of every point (1…N by default); `fields` and `values`
    — more fields of the triangle table and their values per triangle."""
    folder.mkdir(parents=True, exist_ok=True)
    pid = np.arange(1, len(v) + 1) if pids is None else np.asarray(pids)
    points = [[float(pid[i]), *map(float, v[i])] for i in range(len(v))]
    extra = values or [[] for _ in t]
    triangles = [[float(k + 1), *(float(pid[c]) for c in t[k]), *extra[k]] for k in range(len(t))]
    tr, pt = folder / names[0], folder / names[1]
    tr.write_bytes(build([*TR, *fields], triangles, word=word))
    pt.write_bytes(build(PT, points, word=word))
    return tr, pt


def error(call: Any, *args: Any, **kwargs: Any) -> FormatError:
    with pytest.raises(FormatError) as info:
        out = call(*args, **kwargs)
        list(out) if hasattr(out, "__next__") else None
    err = info.value
    assert err.message and err.hint and set(err.as_dict()) == {"code", "message", "hint"}
    return err


# ---------------------------------------------------------------- wireframes


def test_cube_1000_bit_for_bit(tmp_path: Path) -> None:
    v, t = cube(4, 10.0)  # on UTM-sized coordinates, the triangles shuffled
    tr, _pt = write_pair(tmp_path, v, t)
    (mesh,) = list(read_dm_wireframes(tr))
    assert mesh.name == "ore" and mesh.source["format"] == "datamine" and mesh.source["points_file"] == "orept.dm"
    assert np.array_equal(bits(mesh.xyz), bits(v)) and np.array_equal(mesh.tri, t) and mesh.tri.dtype == np.int32
    stats = mesh_stats(mesh.xyz, mesh.tri)
    assert stats["closed"] and stats["orientation"] == "outward" and stats["degenerate"] == 0
    assert stats["volume"] == pytest.approx(1000.0, rel=1e-9) and stats["area"] == pytest.approx(600.0, rel=1e-9)
    assert PRELIMINARY and any("предварительно" in w for w in mesh.warnings)

    entries, warnings = inspect_dm_wireframes(tr)
    assert [(e.key, e.name, e.points, e.triangles, e.error) for e in entries] == [(0, "ore", len(v), len(t), None)]
    assert any("предварительно" in w for w in warnings)


@pytest.mark.parametrize(
    "names", [("oretr.dm", "orept.dm"), ("ORE_TR.DM", "ORE_PT.DM"), ("Руда-Tr.dm", "Руда-Pt.dm"), ("tr.dm", "pt.dm")]
)
def test_pair_by_name_from_either_file(tmp_path: Path, names: tuple[str, str]) -> None:
    v, t = cube(2, 10.0)
    tr, pt = write_pair(tmp_path, v, t, names=names)
    write_pair(tmp_path, v + 500.0, t, names=("othertr.dm", "otherpt.dm"))  # another pair of the folder
    assert find_pair(tr) == pt and find_pair(pt) == tr
    for given in (tr, pt):
        (mesh,) = list(read_dm_wireframes(given))
        assert np.array_equal(bits(mesh.xyz), bits(v)) and np.array_equal(mesh.tri, t)
        assert mesh.source["file"] == names[0] and mesh.source["points_file"] == names[1]
    assert mesh.name == (Path(names[0]).stem[:-2].rstrip("_-") or "tr")


def test_pair_by_folder_explicit_and_missing(tmp_path: Path) -> None:
    v, t = cube(2, 10.0)
    tr, pt = write_pair(tmp_path / "one", v, t, names=("walls.dm", "nodes.dm"))
    (tmp_path / "one" / "notes.dm").write_bytes(b"not a table at all")
    (tmp_path / "one" / "model.dm").write_bytes(build([F("XC"), F("XINC")], [[1.0, 2.0]]))
    with open_dm_wireframes(tr) as f:  # the only table of points of the folder
        assert f.points_path == pt and any("nodes.dm" in w and "папк" in w for w in f.warnings)
        assert np.array_equal(bits(f.read(f.entries[0]).xyz), bits(v))
    assert find_pair(pt) == tr and find_pair(tmp_path / "one" / "model.dm") is None

    other = tmp_path / "two" / "any.dm"  # an explicit file of points wins over the names
    other.parent.mkdir()
    other.write_bytes(pt.read_bytes())
    for first, second in ((tr, other), (other, tr)):  # in either order
        (mesh,) = list(read_dm_wireframes(first, second))
        assert mesh.source["points_file"] == "any.dm" and np.array_equal(bits(mesh.xyz), bits(v))
    assert error(read_dm_wireframes, other, pt).code == "pair_mismatch"  # two tables of points

    (tmp_path / "one" / "more.dm").write_bytes(pt.read_bytes())  # two tables of points: which one?
    err = error(read_dm_wireframes, tr)
    assert err.code == "pair_ambiguous" and "more.dm" in err.message and "nodes.dm" in err.message
    alone = tmp_path / "three" / "soltr.dm"
    alone.parent.mkdir()
    alone.write_bytes(tr.read_bytes())
    err = error(read_dm_wireframes, alone)
    assert err.code == "pair_missing" and "solpt.dm" in err.hint
    assert error(read_dm_wireframes, tr, tmp_path / "one" / "model.dm").code == "pair_mismatch"
    assert error(read_dm_wireframes, tr, tmp_path / "nowhere.dm").code == "pair_missing"


def two_cubes() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A cube of 1000 and a cube of 8 in one table, their triangles interleaved; `part` — 0 or 1 per triangle."""
    (va, ta), (vb, tb) = cube(3, 10.0), cube(2, 2.0, UTM + [100.0, 0.0, 0.0])
    v = np.concatenate([va, vb])
    t = np.concatenate([ta, tb + len(va)])
    part = np.concatenate([np.zeros(len(ta), dtype=int), np.ones(len(tb), dtype=int)])
    order = np.random.default_rng(5).permutation(len(t))
    return v, t[order], part[order]


def test_split_by_a_field(tmp_path: Path) -> None:
    v, t, part = two_cubes()
    fields = (F("SURFACE"), F("COLOUR"), F("ZONE", "A", 8), F("GRADE"), F("LINK", implicit=True, default=7.0))
    zone = ["РУДА", "ПОРОДА"]
    values = [[float(10 + 5 * p), 3.0, zone[p], float(k)] for k, p in enumerate(part)]
    tr, _pt = write_pair(tmp_path, v, t, fields=fields, values=values)
    with open_dm_wireframes(tr) as f:
        assert split_fields(f.header) == ["SURFACE", "LINK", "COLOUR", "ZONE", "GRADE"]
        assert [e.name for e in f.entries] == ["ore"]  # no field chosen: one wireframe (ADR 0013 §2)
        whole = f.read(0)
        assert whole.triangle_count == len(t) and np.array_equal(bits(whole.xyz), bits(v))
        assert whole.attrs == [{"name": "COLOUR", "value": 3.0}, {"name": "LINK", "value": 7.0}]
        assert any("SURFACE" in w and "ZONE" in w and "GRADE" in w for w in whole.warnings)  # they change inside

    first = int(part[0])  # the wireframes come in the order of their first triangle
    for field, keys, names in (
        ("SURFACE", [10 + 5 * first, 15 - 5 * first], [f"SURFACE {10 + 5 * first}", f"SURFACE {15 - 5 * first}"]),
        ("zone", [zone[first], zone[1 - first]], [zone[first], zone[1 - first]]),
    ):
        entries, _ = inspect_dm_wireframes(tr, split_by=field)
        assert [e.key for e in entries] == keys and [e.name for e in entries] == names
        assert [e.triangles for e in entries] == [int((part == p).sum()) for p in (first, 1 - first)]
        assert all(e.points is None and e.error is None for e in entries)  # not known without the geometry
        meshes = list(read_dm_wireframes(tr, split_by=field))
        assert [m.name for m in meshes] == names
        for mesh, p in zip(meshes, (first, 1 - first), strict=True):
            stats = mesh_stats(mesh.xyz, mesh.tri)
            assert stats["closed"] and stats["volume"] == pytest.approx((1000.0, 8.0)[p], rel=1e-9)
            used = np.unique(t[part == p])
            assert np.array_equal(bits(mesh.xyz), bits(v[used]))  # its points, in the order of the file
            assert np.array_equal(bits(mesh.xyz[mesh.tri]), bits(v[t[part == p]]))  # triangles in file order
            attrs = {a["name"]: a["value"] for a in mesh.attrs}
            assert attrs == {"SURFACE": 10.0 + 5 * p, "COLOUR": 3.0, "ZONE": zone[p], "LINK": 7.0}
            assert mesh.source["split"] == {"field": field.upper(), "value": attrs[field.upper()]}
            assert any("GRADE" in w for w in mesh.warnings) and not any("ZONE" in w for w in mesh.warnings)
        assert [e.attrs for e in entries] == [m.attrs for m in meshes]
        assert sum(any("предварительно" in w for w in m.warnings) for m in meshes) == 1  # once a file

    (only,) = list(read_dm_wireframes(tr, split_by="LINK"))  # an implicit field: one value for every record
    assert only.name == "LINK 7" and only.triangle_count == len(t)
    (second,) = list(read_dm_wireframes(tr, split_by="ZONE", keys=[zone[1]]))
    assert second.name == zone[1] and second.triangle_count == int((part == 1).sum())
    err = error(read_dm_wireframes, tr, split_by="NOFIELD")
    assert err.code == "no_field" and "SURFACE" in err.hint
    assert error(read_dm_wireframes, tr, split_by="PID1").code == "no_field"
    err = error(read_dm_wireframes, tr, split_by="GRADE", max_meshes=20)
    assert err.code == "too_many_meshes" and "GRADE" in err.message


def test_split_with_blank_values(tmp_path: Path) -> None:
    v, t, part = two_cubes()
    values = [[ABSENT if p else 4.0, "" if p else "A"] for p in part]
    tr, _pt = write_pair(tmp_path, v, t, fields=(F("GROUP"), F("CODE", "A", 4)), values=values)
    for field, named in (("GROUP", "GROUP 4"), ("CODE", "A")):
        entries, _ = inspect_dm_wireframes(tr, split_by=field)
        assert sorted(e.name for e in entries) == sorted([named, f"{field} — пусто"])
        blank = next(m for m in read_dm_wireframes(tr, split_by=field) if m.name.endswith("пусто"))
        assert blank.triangle_count == int(part.sum())
        assert {a["name"]: a["value"] for a in blank.attrs} == {"GROUP": None, "CODE": None}  # empty, never 0


def test_point_numbers_in_any_order_with_gaps_and_repeats(tmp_path: Path) -> None:
    v, t = cube(3, 10.0)
    rng = np.random.default_rng(11)
    pids = rng.permutation(len(v)) * 10 + 7  # not 1…N, not in order
    tr, pt = write_pair(tmp_path, v, t, pids=pids)
    (mesh,) = list(read_dm_wireframes(tr))
    assert np.array_equal(bits(mesh.xyz), bits(v)) and np.array_equal(mesh.tri, t)

    # a repeated number: the first point is taken; an unused point (even without coordinates) is left out
    points = [[float(pids[i]), *map(float, v[i])] for i in range(len(v))]
    points += [[float(pids[0]), 1.0, 2.0, 3.0], [float(pids[1]), *map(float, v[1])], [5.0, ABSENT, 0.0, 0.0]]
    pt.write_bytes(build(PT, points))
    (mesh,) = list(read_dm_wireframes(tr))
    assert np.array_equal(bits(mesh.xyz), bits(v)) and np.array_equal(mesh.tri, t)
    assert any("повторя" in w and "1" in w for w in mesh.warnings)
    assert any("не входят" in w for w in mesh.warnings)

    # two points at one place under different numbers are welded exactly; the triangles keep their order
    twin = np.concatenate([v, v[:5]])
    t2 = t.copy()
    t2[t2 < 5] += len(v) * (rng.random(np.count_nonzero(t2 < 5)) < 0.5)
    tr2, _ = write_pair(tmp_path / "twin", twin, t2)
    (mesh,) = list(read_dm_wireframes(tr2))
    assert mesh.point_count == len(v) and mesh_stats(mesh.xyz, mesh.tri)["closed"]
    assert np.array_equal(bits(mesh.xyz[mesh.tri]), bits(twin[t2]))
    assert any("совпадающие" in w.lower() for w in mesh.warnings)


def test_missing_points_and_absent_coordinates(tmp_path: Path) -> None:
    v, t = cube(2, 10.0)
    tr, pt = write_pair(tmp_path, v, t)
    points = [[float(i + 1), *map(float, v[i])] for i in range(len(v))]
    pt.write_bytes(build(PT, points[:-1]))  # the last point is not in the file
    err = error(read_dm_wireframes, tr)
    assert err.code == "bad_mesh" and str(int((t == len(v) - 1).any(axis=1).sum())) in err.message
    assert "orept.dm" in err.message

    broken = [row[:] for row in points]
    broken[3][2] = ABSENT  # a used point without Y: refused, never 0
    pt.write_bytes(build(PT, broken))
    assert error(read_dm_wireframes, tr).code == "bad_coordinates"

    rows = [[1.0, 1.0, 2.0, ABSENT]] + [[float(k + 2), *(float(c + 1) for c in t[k])] for k in range(len(t) - 1)]
    tr.write_bytes(build(TR, rows))  # a triangle without a point number
    pt.write_bytes(build(PT, points))
    assert error(read_dm_wireframes, tr).code == "bad_mesh"

    tr.write_bytes(build(TR, []))
    assert error(read_dm_wireframes, tr).code == "no_meshes"


def test_a_bad_wireframe_does_not_lose_the_others(tmp_path: Path) -> None:
    v, t, part = two_cubes()
    tr, pt = write_pair(tmp_path, v, t, fields=(F("GROUP"),), values=[[float(p)] for p in part])
    keep = np.unique(t[part == 0])
    pt.write_bytes(build(PT, [[float(i + 1), *map(float, v[i])] for i in keep]))  # the points of cube 0 only
    with open_dm_wireframes(tr, split_by="GROUP") as f:
        by_key = {e.key: e for e in f.entries}
        good = f.read(by_key[0])
        assert mesh_stats(good.xyz, good.tri)["volume"] == pytest.approx(1000.0, rel=1e-9)
        with pytest.raises(FormatError) as info:
            f.read(by_key[1])
        assert info.value.code == "bad_mesh" and "GROUP 1" in info.value.message


def test_single_precision(tmp_path: Path) -> None:
    v, t = cube(2, 10.0, offset=(1000.0, 2000.0, 300.0))
    tr, _pt = write_pair(tmp_path, v, t, word=4)
    (mesh,) = list(read_dm_wireframes(tr))
    assert np.array_equal(mesh.xyz, v.astype(np.float32).astype(np.float64)) and np.array_equal(mesh.tri, t)
    assert mesh.source["precision"] == "float32" and any("одинарной точности" in w for w in mesh.warnings)


def test_wrong_tables(tmp_path: Path) -> None:
    model = tmp_path / "model.dm"
    model.write_bytes(build([F("XC"), F("YC"), F("ZC"), F("XINC")], [[1.0, 2.0, 3.0, 4.0]]))
    err = error(read_dm_wireframes, model)
    assert err.code == "unsupported" and "блочная модель" in err.message
    assert error(read_dm_strings, model).code == "unsupported"
    table = tmp_path / "assay.dm"
    table.write_bytes(build([F("BHID", "A", 8), F("FROM"), F("TO")], [["DH1", 0.0, 1.0]]))
    assert error(read_dm_wireframes, table).code == "not_wireframe"
    assert error(read_dm_strings, table).code == "not_strings"
    strings = tmp_path / "lines.dm"
    strings.write_bytes(build(STR, [[1.0, 1.0, 0.0, 0.0, 0.0], [2.0, 1.0, 1.0, 0.0, 0.0]]))
    err = error(read_dm_wireframes, strings)
    assert err.code == "not_wireframe" and "стринг" in err.message
    v, t = cube(1, 1.0)
    tr, pt = write_pair(tmp_path, v, t)
    assert error(read_dm_strings, tr).code == "not_strings" and error(read_dm_strings, pt).code == "not_strings"
    junk = tmp_path / "junk.dm"
    junk.write_bytes(b"\x00" * 5000)
    assert error(read_dm_wireframes, junk).code == "not_datamine"


def test_progress_and_cancel(tmp_path: Path) -> None:
    v, t = grid(60, 60, lambda x, y: np.sin(x / 7.0) + y / 9.0)
    tr = tmp_path / "surftr.dm"
    tr.write_bytes(build_numbers(TR, np.column_stack([np.arange(1, len(t) + 1), t + 1])))
    (tmp_path / "surfpt.dm").write_bytes(build_numbers(PT, np.column_stack([np.arange(1, len(v) + 1), v])))
    seen: list[float] = []
    (mesh,) = list(read_dm_wireframes(tr, progress=lambda f, _m=None: seen.append(f)))
    assert np.array_equal(bits(mesh.xyz), bits(v)) and np.array_equal(mesh.tri, t)
    assert seen and seen == sorted(seen) and 0.0 <= seen[0] and seen[-1] == 1.0

    class Cancelled(Exception):
        pass

    for after in (0, 1, 3):
        calls = 0

        def cancel(_f: float, _m: str | None = None, after: int = after) -> None:
            nonlocal calls
            calls += 1
            if calls > after:
                raise Cancelled

        with pytest.raises(Cancelled):
            list(read_dm_wireframes(tr, progress=cancel))
    tr.unlink()  # nothing keeps the file open after a cancelled reading


def test_builder_of_numbers_gives_the_bytes_of_the_record_builder() -> None:
    rows = [[float(k), k * 0.5, -1.0 * k] for k in range(400)]
    fields = [F("A"), F("B"), F("K", implicit=True, default=2.0), F("C")]
    for word in (8, 4):
        assert build_numbers(fields, rows, word=word) == build(fields, rows, word=word)  # type: ignore[arg-type]


def test_a_million_triangles_time_and_memory(tmp_path: Path) -> None:
    v, t = grid(708, 708, lambda x, y: np.sin(x / 50.0) * 20.0 + y * 0.1, seed=None)  # 1 002 528 triangles
    part = (np.arange(len(t)) * 7 // len(t)).astype(np.float64)  # seven wireframes
    tr = tmp_path / "bigtr.dm"
    tr.write_bytes(build_numbers([*TR, F("SURFACE")], np.column_stack([np.arange(1, len(t) + 1), t + 1, part])))
    (tmp_path / "bigpt.dm").write_bytes(build_numbers(PT, np.column_stack([np.arange(1, len(v) + 1), v])))

    tracemalloc.start()
    entries, _ = inspect_dm_wireframes(tr, split_by="SURFACE")
    peak_inspect = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert len(entries) == 7 and sum(e.triangles or 0 for e in entries) == len(t)
    assert peak_inspect < 40 << 20  # the composition holds no geometry: 4 bytes a triangle and one chunk of pages

    tracemalloc.start()
    started = time.perf_counter()
    triangles = points = 0
    for mesh in read_dm_wireframes(tr, split_by="SURFACE"):
        triangles += mesh.triangle_count
        points += mesh.point_count
    seconds = time.perf_counter() - started
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert triangles == len(t) and points >= len(v)
    print(f"\n1M triangles in 7 wireframes: {seconds:.2f} s (under tracemalloc), peak {peak / 2**20:.0f} MB")
    assert seconds < 15.0 and peak < 200 << 20  # 12 B a triangle + 24 B a point + one wireframe at a time


def test_many_small_wireframes_do_not_cost_a_pass_each(tmp_path: Path) -> None:
    v, t = grid(150, 100, lambda x, y: x * 0.0, seed=None)  # 30 000 triangles
    group = np.arange(len(t)) // 2  # 15 000 wireframes of two triangles
    tr = tmp_path / "manytr.dm"
    tr.write_bytes(build_numbers([*TR, F("GROUP")], np.column_stack([np.arange(1, len(t) + 1), t + 1, group])))
    (tmp_path / "manypt.dm").write_bytes(build_numbers(PT, np.column_stack([np.arange(1, len(v) + 1), v])))
    started = time.perf_counter()
    meshes = list(read_dm_wireframes(tr, split_by="GROUP"))
    seconds = time.perf_counter() - started
    assert len(meshes) == 15_000 and all(m.triangle_count == 2 for m in meshes)
    assert [m.name for m in meshes[:2]] == ["GROUP 0", "GROUP 1"] and meshes[-1].attrs[0]["value"] == 14_999.0
    assert np.array_equal(bits(meshes[7].xyz[meshes[7].tri]), bits(v[t[14:16]]))
    assert seconds < 20.0  # a pass over the file per wireframe would take minutes


# ---------------------------------------------------------------- strings


def string_rows() -> tuple[list[list[Any]], list[np.ndarray]]:
    """Records of a strings table and the lines they describe. PVALUE — the number of the string, PTN — the
    number of the point in it; COLOUR changes from string to string, TAG along the third one."""
    square = np.array([[0, 0, 5], [10, 0, 5], [10, 10, 5], [0, 10, 5], [0, 0, 5]], dtype=float) + UTM
    open_line = np.array([[0, 20, 1], [5, 21, 2], [9, 23, np.nan]], dtype=float) + UTM
    again = np.array([[50, 50, 0], [60, 55, 0]], dtype=float) + UTM  # the same PVALUE as the line before
    single = np.array([[70.0, 70.0, 7.0]]) + UTM
    lines = [square, open_line, again, single]
    meta = [(1.0, 2.0, "A"), (2.0, 3.0, "B"), (2.0, 3.0, "C"), (9.0, 2.0, "A")]
    rows: list[list[Any]] = []
    for line, (pvalue, colour, tag) in zip(lines, meta, strict=True):
        for k, p in enumerate(line):
            z = ABSENT if np.isnan(p[2]) else float(p[2])
            vertex_tag = f"{tag}{k}" if tag == "C" else tag
            rows.append([float(k + 1), pvalue, float(p[0]), float(p[1]), z, colour, vertex_tag])
    return rows, lines


STR_FIELDS = [*STR, F("COLOUR"), F("TAG", "A", 4)]


def test_strings(tmp_path: Path) -> None:
    rows, lines = string_rows()
    path = tmp_path / "Контуры.dm"
    path.write_bytes(build(STR_FIELDS, rows))
    read = read_dm_strings(path)
    assert read.format == "datamine_str" and read.records == len(rows) and read.header.kind == "strings"
    assert any("предварительно" in w for w in read.warnings)
    (layer,) = read.layers
    assert layer.name == "Контуры" and layer.line_count == 4 and layer.point_count == len(rows)
    for k, line in enumerate(lines):
        assert np.array_equal(bits(layer.line(k)), bits(line))  # bit for bit, the absent Z is NaN
    assert layer.closed.tolist() == [1, 0, 0, 0]  # the first string repeats its first point
    assert layer.seq.tolist() == list(range(len(rows)))
    attrs = layer.attrs.to_pydict()
    assert attrs == {"PVALUE": [1.0, 2.0, 2.0, 9.0], "COLOUR": [2.0, 3.0, 3.0, 2.0]}
    tags = layer.vattrs.to_pydict()["TAG"]  # it changes along the third string: kept per vertex
    assert tags[len(lines[0]) + len(lines[1]) :][:2] == ["C0", "C1"] and tags[0] == "A"
    assert "PTN" not in attrs and "PTN" not in layer.vattrs.column_names


def test_strings_by_layer_field_and_without_coordinates(tmp_path: Path) -> None:
    rows, lines = string_rows()
    rows.insert(2, [2.5, 1.0, ABSENT, 5.0, 5.0, 2.0, "A"])  # a record without X is left out and reported
    path = tmp_path / "lines.dm"
    path.write_bytes(build(STR_FIELDS, rows))
    read = read_dm_strings(path, layer_field="colour")
    assert [layer.name for layer in read.layers] == ["COLOUR 2", "COLOUR 3"]  # in the order of the first line
    assert [layer.line_count for layer in read.layers] == [2, 2]
    assert any("без X или Y" in w and "1" in w for w in read.warnings)
    first = read.layers[0]
    assert np.array_equal(bits(first.line(0)), bits(lines[0])) and np.array_equal(bits(first.line(1)), bits(lines[3]))
    assert first.seq[:3].tolist() == [0, 1, 3]  # the numbers of the records of the file
    assert "COLOUR" not in (first.attrs.column_names if first.attrs is not None else [])

    by_tag = read_dm_strings(path, layer_field="TAG")  # a text field; it changes along a string: the string is cut
    assert [layer.name for layer in by_tag.layers] == ["A", "B", "C0", "C1"]
    assert error(read_dm_strings, path, layer_field="NOFIELD").code == "no_field"
    assert error(read_dm_strings, path, layer_field="XP").code == "no_field"

    blank = tmp_path / "blank.dm"
    blank.write_bytes(build(STR_FIELDS, [[1.0, 1.0, 0.0, 0.0, 0.0, ABSENT, ""], [2.0, 1.0, 1.0, 0.0, 0.0, ABSENT, ""]]))
    assert [layer.name for layer in read_dm_strings(blank, layer_field="COLOUR").layers] == ["Без кода"]
    empty = tmp_path / "empty.dm"
    empty.write_bytes(build(STR_FIELDS, []))
    assert error(read_dm_strings, empty).code == "no_lines"
    nowhere = tmp_path / "nowhere.dm"
    nowhere.write_bytes(build(STR, [[1.0, 1.0, ABSENT, ABSENT, 0.0]]))
    assert error(read_dm_strings, nowhere).code == "no_lines"


def test_strings_progress_cancel_and_size(tmp_path: Path) -> None:
    n, per = 300_000, 100
    k = np.arange(n)
    xyz = np.column_stack([k * 0.5, np.sin(k / 30.0) * 40.0, (k // per) * 1.0]) + UTM
    table = np.column_stack([k % per + 1, k // per + 1, xyz])
    path = tmp_path / "many.dm"
    path.write_bytes(build_numbers(STR, table))
    seen: list[float] = []
    started = time.perf_counter()
    read = read_dm_strings(path, progress=lambda f, _m=None: seen.append(f))
    seconds = time.perf_counter() - started
    (layer,) = read.layers
    assert layer.line_count == n // per and np.array_equal(bits(layer.xyz), bits(xyz))
    assert seen == sorted(seen) and seen[-1] == 1.0 and seconds < 5.0

    class Cancelled(Exception):
        pass

    def cancel(f: float, _m: str | None = None) -> None:
        if f > 0.3:
            raise Cancelled

    with pytest.raises(Cancelled):
        read_dm_strings(path, progress=cancel)
    path.unlink()
