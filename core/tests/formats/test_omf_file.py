"""OMF v1 file (ТЗ 7.24, 9.8; ADR 0017 §1): header, JSON, blocks inflated within the limits of S4, composition.

Reference values: the v1 sample of omf-python (`tests/data/ext/omf/v1`, MIT) and its summary
`expected/test_file.v1.json`, made by `tools/gen/ref_omf.py` without this reader (Д-21). Arrays are compared with
`zlib.decompress` of the raw block (`omf_builder.sample_array`). Negative cases: OMF 2 (ZIP: the v2 sample and
McLaughlin), a cut file, a foreign signature, another version, a broken zlib stream, a block outside the file, an
unknown dtype, inflating more than 1 032 × the block or than the size known in advance, oversized and broken JSON.
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.formats import omf_compose as oc
from bf_core.formats import omf_file as of
from bf_core.formats.errors import FormatError
from tests.formats.omf_builder import EXPECTED, SAMPLE, V1, RawOmf, sample_array, sample_objects

RU = "абвгдежзийклмнопрстуфхцчшщъыьэюя"


def fails(fn: Any, *args: Any, **kw: Any) -> FormatError:
    with pytest.raises(FormatError) as info:
        out = fn(*args, **kw)
        if hasattr(out, "__next__"):
            list(out)
    err = info.value
    assert err.message and err.hint and set(err.as_dict()) == {"code", "message", "hint"}
    assert any(ch in RU for ch in err.message.lower()) and any(ch in RU for ch in err.hint.lower())
    return err


def read_block(path: Path, uid_of: str, rows: int | None = None) -> np.ndarray:
    with of.open_omf(path) as f:
        block = f.block_of(uid_of, "массив")
        assert isinstance(block, of.Block)
        return f.read(block, "массив", rows)


# --- the sample -------------------------------------------------------------------------------------------


def test_header_and_json_of_the_sample() -> None:
    with of.open_omf(V1) as f:
        assert f.version == "OMF-v0.9.0" and f.json_start == sample_objects()[1]
        assert f.project["name"] == "Test project" and f.origin() == (0.0, 0.0, 0.0)
        assert [e["name"] for _u, e in f.elements()] == ["Random Points", "Random Line", "trisurf", "gridsurf", "vol"]
        assert f.read_bytes == 0  # no block read yet


def test_composition_of_the_sample_matches_expected_without_reading_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the composition must not inflate blocks")

    monkeypatch.setattr(of.OmfFile, "inflate", never)
    expected = json.loads((EXPECTED / "test_file.v1.json").read_text(encoding="utf-8"))
    c = oc.describe(V1)
    assert c.project == expected["project"] and c.version == expected["file_version"] and c.crs is None
    assert len(c.elements) == len(expected["elements"])
    for e, x in zip(c.elements, expected["elements"], strict=True):
        assert (e.cls, e.name, e.geometry, e.textures) == (x["class"], x["name"], x["geometry"], x["textures"])
        assert [(d.name, d.cls, d.location) for d in e.data] == [
            (d["name"], d["class"], d["location"]) for d in x["data"]
        ]
    points, line, tri, grid, vol = c.elements
    assert points.kind == "points" and points.skip and points.as_ is None
    assert (line.as_, line.color, line.points, line.triangles) == ("слой линий", "#0000ff", None, None)
    assert [d.loads for d in line.data] == [False, None]  # vertex data no; segment data — when constant
    assert (tri.as_, tri.color, tri.points, tri.triangles) == ("каркас", "#64c8c8", None, None)
    assert [d.loads for d in tri.data] == [False, False]
    assert (grid.counts, grid.points, grid.triangles) == ([10, 15], 11 * 16, 2 * 10 * 15)
    assert (vol.as_, vol.counts, vol.cells, [d.loads for d in vol.data]) == (
        "блочная модель",
        [10, 15, 20],
        3000,
        [True],
    )
    # What will not load, by names: the points, 3 textures (2 + 1), data on vertices and faces.
    out = c.left_out()
    assert {(x["element"], x["what"], x["count"]) for x in out if x["what"] in ("элемент", "текстуры")} == {
        ("Random Points", "элемент", 1),
        ("Random Points", "текстуры", 2),
        ("gridsurf", "текстуры", 1),
    }
    assert sum(x["count"] for x in out if x["what"] == "текстуры") == 3
    assert {(x["element"], x["what"]) for x in out if x["what"].startswith("данные")} == {
        ("Random Line", "данные «rand vert data»"), ("trisurf", "данные «rand vert data»"),
        ("trisurf", "данные «rand face data»"), ("gridsurf", "данные «rand vert data»"),
        ("gridsurf", "данные «rand face data»")}  # fmt: skip
    text = "\n".join(c.warnings())
    assert "Точки «Random Points»: элемент — точки не переносятся" in text and "текстуры (2)" in text
    assert "Система координат в файле OMF не указана" in text
    assert c.summary().startswith("Проект «Test project», элементов 5") and "не загрузятся: 1" in c.summary()
    d = c.as_dict()
    assert json.loads(json.dumps(d, ensure_ascii=False)) == d and d["elements"][4]["as"] == "блочная модель"


def test_blocks_of_the_sample_equal_zlib_decompress() -> None:
    _raw, _o, objs = sample_objects()
    tri = next(v for v in objs.values() if v.get("name") == "trisurf")
    geom = objs[tri["geometry"]]
    v = read_block(V1, geom["vertices"])
    t = read_block(V1, geom["triangles"], rows=50)
    assert v.shape == (100, 3) and v.dtype == np.dtype("<f8") and t.shape == (50, 3)
    assert v.tobytes() == sample_array("trisurf", "vertices").tobytes()
    assert (t == sample_array("trisurf", "triangles")).all()
    with of.open_omf(V1) as f:
        line = next(v for v in objs.values() if v.get("name") == "Random Line")
        seg = f.array(objs[line["geometry"]]["segments"], "сегменты")
        assert isinstance(seg, np.ndarray) and seg.shape == (50, 2)


def test_portions_and_progress_inside_a_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(of, "CHUNK", 4096)
    values = np.random.default_rng(3).uniform(-1e6, 1e6, (50_000, 3))
    raw = RawOmf()
    uid = raw.array("Vector3Array", values, "<f8")
    path = raw.write(tmp_path / "big.omf")
    seen: list[int] = []
    with of.open_omf(path) as f:
        f.tick = seen.append
        block = f.block_of(uid, "вершины")
        assert isinstance(block, of.Block)
        parts = list(f.inflate(block, "вершины"))
        assert max(len(p) for p in parts) <= 4096 and len(parts) >= values.nbytes // 4096
        assert len(seen) >= 2 and seen == sorted(seen) and seen[-1] == block.length == f.read_bytes
        got = f.read(block, "вершины", rows=50_000)
    assert got.tobytes() == values.tobytes()

    class Stop(Exception):
        pass

    def stop(_n: int) -> None:
        raise Stop

    with of.open_omf(path) as f, pytest.raises(Stop):
        f.tick = stop
        f.array(uid, "вершины")


def test_zeros_at_the_deflate_bound_are_read(tmp_path: Path) -> None:
    raw = RawOmf()
    uid = raw.add("ScalarArray", array=raw.raw_block(zlib.compress(bytes(16 << 20), 9), "|u1"))
    got = read_block(raw.write(tmp_path / "z.omf"), uid)
    assert len(got) == 16 << 20 and not got.any()


# --- refused files ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("rel", ["v2/test_file.omf", "minelib/McLaughlin_tutorial.omf"])
def test_omf2_zip_is_refused_with_a_hint(rel: str) -> None:
    err = fails(oc.describe, SAMPLE / rel)
    assert err.code == "omf2_not_supported" and "OMF 2" in err.message
    assert "OMF v1" in err.hint and "Leapfrog" in err.hint
    assert of.sniff((SAMPLE / rel).read_bytes()[:4]) == "omf2" and of.sniff(V1.read_bytes()[:4]) == "v1"


def test_cut_foreign_and_other_version(tmp_path: Path) -> None:
    good = V1.read_bytes()
    cases = {
        "empty.omf": (b"", "omf_damaged", "пуст"),
        "text.omf": (b"hello world, not a file" * 10, "omf_damaged", "сигнатуры"),
        "head.omf": (good[:30], "omf_damaged", "заголовок"),
        "cut.omf": (good[:5000], "omf_damaged", "JSON"),
        "nojson.omf": (good[:-200], "omf_damaged", "JSON"),
        "v2.omf": (good[:4] + struct.pack("<32s", b"OMF-v2.0.0") + good[36:], "omf_version", "OMF-v2.0.0"),
        "noversion.omf": (good[:4] + bytes(32) + good[36:], "omf_version", "не указана"),
    }
    for name, (data, code, part) in cases.items():
        p = tmp_path / name
        p.write_bytes(data)
        err = fails(oc.describe, p)
        assert err.code == code and part in err.message, (name, err.as_dict())
    assert "OMF v1" in fails(oc.describe, tmp_path / "v2.omf").hint


def test_broken_zlib_is_found_when_the_block_is_read(tmp_path: Path) -> None:
    _raw, _o, objs = sample_objects()
    geom = objs[next(v for v in objs.values() if v.get("name") == "trisurf")["geometry"]]
    start = objs[geom["vertices"]]["array"]["start"]
    broken = bytearray(V1.read_bytes())
    broken[start + 60 : start + 100] = b"\x00" * 40
    p = tmp_path / "zlib.omf"
    p.write_bytes(bytes(broken))
    assert oc.describe(p).elements[2].loads  # the JSON is intact
    err = fails(read_block, p, geom["vertices"])
    assert err.code == "omf_damaged" and ("повреждён" in err.message or "обрезан" in err.message)


def _one_block(tmp_path: Path, desc: dict[str, Any], cls: str = "ScalarArray", data: bytes | None = None) -> Any:
    raw = RawOmf()
    if data is not None:
        raw.raw_block(data, "<f8")
    uid = raw.add(cls, array=desc)
    return raw.write(tmp_path / "b.omf"), uid


@pytest.mark.parametrize(
    "desc, part",
    [
        ({"start": 60, "length": 10_000, "dtype": "<f8"}, "за пределами"),
        ({"start": 10, "length": 8, "dtype": "<f8"}, "за пределами"),
        ({"start": 60, "length": 0, "dtype": "<f8"}, "за пределами"),
        ({"start": 60, "length": 8, "dtype": ">f8"}, "не поддерживается"),
        ({"start": 60, "length": 8, "dtype": "object"}, "не поддерживается"),
        ({"start": "60", "length": 8, "dtype": "<f8"}, "неверное описание"),
        ({"start": True, "length": 8, "dtype": "<f8"}, "неверное описание"),
        ("60:8", "нет ссылки"),
    ],
)
def test_bad_block_descriptors(tmp_path: Path, desc: Any, part: str) -> None:
    path, uid = _one_block(tmp_path, desc, data=zlib.compress(np.arange(3.0).tobytes()))
    err = fails(read_block, path, uid)
    assert err.code == "omf_damaged" and part in err.message


def test_unknown_array_class_and_truncated_stream(tmp_path: Path) -> None:
    path, uid = _one_block(tmp_path, {"start": 60, "length": 11, "dtype": "<f8"}, cls="Vector9Array",
                           data=zlib.compress(np.arange(3.0).tobytes()))  # fmt: skip
    assert "неизвестный тип массива" in fails(read_block, path, uid).message
    whole = zlib.compress(np.arange(1000.0).tobytes())
    path, uid = _one_block(tmp_path, {"start": 60, "length": len(whole) // 2, "dtype": "<f8"}, data=whole)
    assert "обрезан" in fails(read_block, path, uid).message
    odd = zlib.compress(b"\x00" * 12)
    path, uid = _one_block(tmp_path, {"start": 60, "length": len(odd), "dtype": "<f8"}, data=odd)
    assert "не кратен" in fails(read_block, path, uid).message


def test_inflating_beyond_the_ratio_is_refused_early(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A «bomb»: the limit is checked portion by portion — the reader never holds more than the limit + a portion."""
    bomb = zlib.compress(bytes(64 << 20), 9)  # ≈ 65 KB that inflate to 64 MiB
    raw = RawOmf()
    uid = raw.add("ScalarArray", array=raw.raw_block(bomb, "|u1"))
    path = raw.write(tmp_path / "bomb.omf")
    monkeypatch.setattr(of, "MAX_RATIO", 100)
    monkeypatch.setattr(of, "CHUNK", 1 << 20)
    got = 0
    with of.open_omf(path) as f:
        block = f.block_of(uid, "данные")
        assert isinstance(block, of.Block)
        with pytest.raises(FormatError) as info:
            for part in f.inflate(block, "данные"):
                got += len(part)
    assert info.value.code == "omf_too_large" and "100 раз" in info.value.message and "бомба" in info.value.message
    assert got <= 100 * len(bomb)
    monkeypatch.setattr(of, "MAX_VALUES", 1000)
    monkeypatch.setattr(of, "MAX_RATIO", 1032)
    assert fails(read_block, path, uid).code == "omf_too_large"


def test_size_known_in_advance_must_match(tmp_path: Path) -> None:
    raw = RawOmf()
    uid = raw.array("ScalarArray", np.arange(24.0), "<f8")
    texts = raw.strings(["a", "b"])
    path = raw.write(tmp_path / "n.omf")
    assert (read_block(path, uid, rows=24) == np.arange(24.0)).all()
    for rows in (23, 25, 0):
        err = fails(read_block, path, uid, rows=rows)
        assert err.code == "omf_damaged" and "значений" in err.message
    with of.open_omf(path) as f:
        assert f.array(texts, "подписи", 2) == ["a", "b"]
        assert fails(f.array, texts, "подписи", 5).code == "omf_damaged"


@pytest.mark.parametrize(
    "js, code, part",
    [
        (b"{not json", "omf_damaged", "повреждено"),
        (b"[1, 2, 3]", "omf_damaged", "структуру"),
        (b'{"a": 1}', "omf_damaged", "структуру"),
        (b"\xff\xfe{}", "omf_damaged", "повреждено"),
        (b"[" * 200_000 + b"]" * 200_000, "omf_damaged", "повреждено"),
        (b'{"a": {"__class__": "ScalarArray"}}', "omf_damaged", "не найден проект"),
    ],
)
def test_broken_json(tmp_path: Path, js: bytes, code: str, part: str) -> None:
    p = tmp_path / "j.omf"
    p.write_bytes(of.MAGIC + struct.pack("<32s", of.VERSION) + bytes(16) + struct.pack("<Q", 60) + js)
    err = fails(oc.describe, p)
    assert err.code == code and part in err.message


def test_json_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = RawOmf()
    for k in range(20):
        raw.surface(f"s{k}", np.zeros((3, 3)), [[0, 1, 2]])
    path = raw.write(tmp_path / "many.omf")
    assert len(oc.describe(path).elements) == 20
    monkeypatch.setattr(of, "MAX_OBJECTS", 50)
    assert "объектов" in fails(oc.describe, path).message
    monkeypatch.setattr(of, "MAX_OBJECTS", 1_000_000)
    monkeypatch.setattr(of, "MAX_DICTS", 50)
    assert fails(oc.describe, path).code == "omf_too_large"
    monkeypatch.setattr(of, "MAX_DICTS", 4_000_000)
    monkeypatch.setattr(of, "MAX_JSON", 1000)
    err = fails(oc.describe, path)
    assert err.code == "omf_too_large" and "МиБ" in err.message


def test_broken_elements_are_described_with_their_error(tmp_path: Path) -> None:
    raw = RawOmf()
    raw.surface("good", np.zeros((3, 3)), [[0, 1, 2]], color="#ABCDEF")
    raw.element("SurfaceElement", "no geometry", "missing-uid")
    raw.element("PlanarElement", "alien", "x")
    g = raw.add("VolumeGridGeometry", origin=[0, 0, 0], tensor_u=[1.0], tensor_v=[1.0, -1.0], tensor_w=[1.0])
    raw.element("VolumeElement", "bad tensor", g)
    r = 0.7071067811865476
    raw.volume("rotated", ([1.0], [1.0], [1.0]), axes=([r, r, 0.0], [-r, r, 0.0], [0.0, 0.0, 1.0]))
    raw.volume("swapped", ([2.0], [1.0, 1.0], [1.0]), axes=([0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]))
    c = oc.describe(raw.write(tmp_path / "e.omf"))
    good, nogeom, alien, bad, rotated, swapped = c.elements
    assert good.loads and good.color == "#abcdef"
    assert nogeom.error and nogeom.error["code"] == "omf_damaged" and "отсутствующий" in nogeom.error["message"]
    assert alien.skip and "не поддерживается" in alien.skip and alien.kind == "other"
    assert bad.error and "положительными" in bad.error["message"]
    assert rotated.skip == "модель повёрнута: оси сетки не вдоль X, Y, Z" and rotated.cells == 1
    assert swapped.loads and swapped.cells == 2
    assert {x["element"] for x in c.left_out() if x["what"] == "элемент"} == {
        "no geometry",
        "alien",
        "bad tensor",
        "rotated",
    }


def test_volume_and_grid_over_the_limits_are_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sizes the JSON alone declares (tensor lists): never built when over the limit."""
    raw = RawOmf()
    raw.volume("big", ([1.0] * 10, [1.0] * 10, [1.0] * 10))
    grid = raw.add("SurfaceGridGeometry", origin=[0, 0, 0], tensor_u=[1.0] * 100, tensor_v=[1.0] * 100)
    raw.element("SurfaceElement", "grid", grid)
    monkeypatch.setattr(of, "MAX_VOLUME_CELLS", 999)
    monkeypatch.setattr(of, "MAX_GRID_TRIANGLES", 19_999)
    vol, surf = oc.describe(raw.write(tmp_path / "v.omf")).elements
    assert vol.skip == "1 000 ячеек — больше предела 999" and vol.as_ is None
    assert surf.skip == "20 000 треугольников — больше предела 19 999" and surf.as_ is None


def test_descriptions_of_bf2() -> None:
    desc = "Проект участка\nСистема координат: WGS 84 / UTM zone 37N (EPSG:32637)\n"
    assert of.parse_crs(desc) == {"name": "WGS 84 / UTM zone 37N", "epsg": 32637}
    assert of.parse_crs("Система координат: Местная") == {"name": "Местная", "epsg": None}
    assert of.parse_crs("Система координат: (EPSG:28407)") == {"name": "EPSG:28407", "epsg": 28407}
    assert of.parse_crs("Система координат:  ") is None and of.parse_crs(None) is None
    assert of.is_dtm("Рельеф\nBedrockForge: ЦТМ") and not of.is_dtm("BedrockForge: ЦТМ участка")
    assert of.unit_of("Единицы: g/t") == "g/t" and of.unit_of("г/т") is None
    assert of.plain_description(desc) == "Проект участка"
    assert of.color_hex([255, 0, 16]) == "#ff0010" and of.color_hex("#ABCDEF") == "#abcdef"
    assert of.color_hex([256, 0, 0]) is None and of.color_hex([1.5, 0, 0]) is None and of.color_hex("red") is None
    assert of.axis_of([0.0, 0.0, -2.0]) == (2, -1.0) and of.axis_of([1.0, 1e-3, 0.0]) is None


def test_reading_the_composition_does_not_load_numpy() -> None:
    code = (
        "import sys; from bf_core.formats import omf_compose as oc;"
        f"c = oc.describe({str(V1)!r}); assert len(c.elements) == 5;"
        "print('numpy' in sys.modules)"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "False"
