"""OMF v1 writer (ТЗ 7.24; ADR 0017 §4): written → read back bit for bit; the v1 sample → BF2 → OMF → BF2 the same.

Reference: the reader of `omf_read` / `omf_volume` (its own tests check it against the sample of omf-python and
`zlib.decompress`), and the raw JSON of the written file parsed with the standard library.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.formats import omf_read as orr
from bf_core.formats import omf_volume as ov
from bf_core.formats.lineset import LineSet, from_lines
from bf_core.formats.meshset import MeshSet
from bf_core.formats.omf_write import OmfWriter
from tests.formats.omf_builder import V1
from tests.formats.test_omf_volume import Sink

CRS = {"name": "WGS 84 / UTM zone 37N", "epsg": 32637}


def read_back(path: Path) -> tuple[orr.OmfReader, list[Any], list[ov.VolumeRead]]:
    with orr.open_reader(path) as r:
        items = list(r.elements())
        vols = [ov.read_volume(r.file, v, Sink, tmp_dir=path.parent) for v in items if isinstance(v, orr.OmfVolume)]
    return r, [x for x in items if not isinstance(x, orr.OmfVolume)], vols


def cells_of(idx: Any, values: dict[str, Any], batch: int = 2) -> Any:
    def cells(name: str) -> Any:
        return [(idx[s : s + batch], values[name][s : s + batch]) for s in range(0, len(idx), batch)]

    return cells


def test_written_file_reads_back_bit_for_bit(tmp_path: Path) -> None:
    rng = np.random.default_rng(7)
    v = rng.uniform(0, 1, (50, 3)) + [512345.678901234, 4512345.12345678, 812.5]
    v[0] = [-0.0, 4512345.0, 0.0]
    t = rng.integers(0, 50, (80, 3))
    body = MeshSet("Рудное тело", v, t, "#e8873a", [{"name": "Описание", "value": "Зона 1"}], {})
    dtm = MeshSet("Рельеф", v[:3], [[0, 1, 2]], None, [], {})
    square = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [0, 0, 0]], dtype=np.float64) + [5e5, 4.5e6, 1]
    near = square.copy()
    near[-1] += [1e-7, 0.0, 0.0]  # closed within the tolerance, not bit for bit: kept as it is
    open_line = rng.uniform(0, 100, (4, 3))
    nan_z = open_line.copy()
    nan_z[2, 2] = np.nan
    layer = from_lines(
        "Контуры", [square, open_line, open_line[:1], nan_z, near],
        attrs={"Участок": ["Восточный", "Западный", None, "Ю", None], "N": [1, 2.5, 3, None, None]},
        color="#4f9dde", units={"N": "м"},
    )  # fmt: skip
    assert layer.closed.tolist() == [1, 0, 0, 0, 1]
    points = LineSet("Устья", rng.uniform(size=(3, 3)))
    counts = [3, 2, 2]
    idx = np.array([0, 1, 5, 11])
    values = {"AU": np.array([0.5, np.nan, 2.0, 3.25]), "ZONE": np.array([1, 0, -1, 1], dtype=np.int32)}
    fields = [{"name": "AU", "kind": "number", "unit": "g/t"},
              {"name": "ZONE", "kind": "category", "unit": None, "labels": ["окисл.", "первич."]}]  # fmt: skip
    grid = {"corner": [100.0, 200.0, 300.0], "counts": counts, "size": [5.0, 5.0, 2.5], "order": "F"}
    tgrid = {"corner": [-1.5, 0.0, 0.0], "counts": [2, 1, 1], "tensors": {"x": [1.0, 2.0], "y": [3.0], "z": [0.5]},
             "order": "F"}  # fmt: skip
    path = tmp_path / "rt.omf"
    with OmfWriter(path, "Проект", "описание", CRS) as w:
        w.add_mesh(body)
        w.add_mesh(dtm, dtm=True)
        notes = w.add_lines(layer)
        notes += w.add_lines(points)
        w.add_volume("Модель", grid, fields, cells_of(idx, values))
        w.add_volume("Тензоры", tgrid, [], cells_of(idx[:0], {}), color="#102030")
        assert w.target.name == "rt.omf.part" and w.target.exists() and not path.exists()
    assert path.exists() and not (tmp_path / "rt.omf.part").exists()
    text = "\n".join(notes)
    assert "из одной точки 1" in text and "без Z 1" in text and "«Устья»: слой точек" in text

    r, items, (vol, tvol) = read_back(path)
    assert r.crs == CRS and r.composition.project == "Проект" and r.composition.description == "описание"
    m1, m2, ls = items
    assert (m1.name, m1.color, m1.attrs) == ("Рудное тело", "#e8873a", [{"name": "Описание", "value": "Зона 1"}])
    assert m1.xyz.tobytes() == v.tobytes() and np.signbit(m1.xyz[0, 0]) and (m1.tri == t).all()
    assert "dtm" not in m1.source and m2.source["dtm"] is True and m2.color == "#808080"
    assert isinstance(ls, LineSet) and (ls.name, ls.color, ls.units) == ("Контуры", "#4f9dde", {"N": "м"})
    assert ls.closed.tolist() == [1, 0, 1] and ls.offsets.tolist() == [0, 5, 9, 14]
    assert ls.xyz.tobytes() == np.vstack([square, open_line, near]).tobytes()
    attrs = ls.attrs.to_pydict()
    assert list(attrs) == ["Участок", "N"] and attrs["Участок"] == ["Восточный", "Западный", None]
    assert attrs["N"][:2] == [1.0, 2.5] and np.isnan(attrs["N"][2])
    sink = vol.staged
    got_idx, got = sink.all()
    assert sink.grid == grid and (got_idx == idx).all() and vol.cells == 4
    assert got["AU"].tobytes() == values["AU"].tobytes() and (got["ZONE"] == values["ZONE"]).all()
    assert vol.fields == fields
    assert tvol.grid == tgrid and tvol.cells == 2 and tvol.color == "#102030"


def test_file_layout_is_omf_v1(tmp_path: Path) -> None:
    path = tmp_path / "x.omf"
    with OmfWriter(path, "П") as w:
        w.add_surface("s", np.eye(3), [[0, 1, 2]], "#010203")
    raw = path.read_bytes()
    assert raw[:4] == bytes.fromhex("84838281") and raw[4:36].rstrip(b"\0") == b"OMF-v0.9.0"
    (offset,) = struct.unpack("<Q", raw[52:60])
    objs = json.loads(raw[offset:])
    classes = sorted(o["__class__"] for o in objs.values())
    assert classes == ["Int3Array", "Project", "SurfaceElement", "SurfaceGeometry", "Vector3Array"]
    project = next(o for o in objs.values() if o["__class__"] == "Project")
    assert project["units"] == "m" and project["origin"] == [0.0, 0.0, 0.0] and project["description"] == ""
    element = objs[project["elements"][0]]
    assert element["color"] == [1, 2, 3] and element["subtype"] == "surface" and element["textures"] == []
    tri = objs[objs[element["geometry"]]["triangles"]]["array"]
    assert tri["dtype"] == "<i8" and tri["start"] >= 60


def test_sample_to_bf2_to_omf_to_bf2_is_the_same(tmp_path: Path) -> None:
    _r, first, (vol,) = read_back(V1)
    path = tmp_path / "again.omf"
    idx, values = vol.staged.all()
    with OmfWriter(path, "Test project") as w:
        for item in first:
            w.add_mesh(item) if isinstance(item, MeshSet) else w.add_lines(item)
        w.add_volume(vol.name, vol.grid, vol.fields, cells_of(idx, values, 1000), vol.color)
    _r2, second, (vol2,) = read_back(path)
    assert [type(x).__name__ for x in second] == [type(x).__name__ for x in first]
    for a, b in zip(first, second, strict=True):
        assert (a.name, a.color) == (b.name, b.color) and a.xyz.tobytes() == b.xyz.tobytes()
        if isinstance(a, MeshSet):
            assert a.tri.tobytes() == b.tri.tobytes() and a.attrs == b.attrs
        else:
            assert a.offsets.tolist() == b.offsets.tolist() and a.closed.tolist() == b.closed.tolist()
    idx2, values2 = vol2.staged.all()
    assert vol2.grid == vol.grid and vol2.fields == vol.fields and (idx2 == idx).all()
    assert values2["Random Data"].tobytes() == values["Random Data"].tobytes()


def test_a_cancel_or_a_failure_leaves_no_file(tmp_path: Path) -> None:
    class Stop(Exception):
        pass

    def stop(written: int) -> None:
        if written > 1 << 20:
            raise Stop

    path = tmp_path / "big.omf"
    rng = np.random.default_rng(1)
    with pytest.raises(Stop), OmfWriter(path, "П", tick=stop) as w:
        w.add_surface("Тело", rng.uniform(size=(300_000, 3)), rng.integers(0, 300_000, (600_000, 3)))
    assert not list(tmp_path.iterdir())
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [4, 1, 1], "size": [1.0, 1.0, 1.0]}
    with pytest.raises(ValueError, match="ascend"), OmfWriter(path, "П") as w:
        w.add_volume("m", grid, [{"name": "A"}], lambda _n: [(np.array([2, 1]), np.array([1.0, 2.0]))])
    with pytest.raises(ValueError, match="ascend"), OmfWriter(path, "П") as w:
        w.add_volume("m", grid, [{"name": "A"}], lambda _n: [(np.array([4]), np.array([1.0]))])
    assert not list(tmp_path.iterdir())


def test_dense_portions_cover_every_cell(tmp_path: Path) -> None:
    from bf_core.formats import omf_write

    idx = np.array([0, 3, 4, 9, 17, 18, 29])
    parts = list(omf_write._dense(30, np.nan, np.float64, [(idx[:3], idx[:3] * 1.0), (idx[3:], idx[3:] * 1.0)],
                                  portion=4))  # fmt: skip
    assert [len(p) for p in parts] == [4] * 7 + [2]
    # each part is the same buffer refilled: copy them as they come
    got = np.concatenate(list(p.copy() for p in omf_write._dense(30, np.nan, np.float64, [(idx, idx * 1.0)], 4)))
    expected = np.full(30, np.nan)
    expected[idx] = idx
    assert got.tobytes() == expected.tobytes()
