"""Elements of OMF v1 as BF2 objects (ТЗ 7.24, 9.8; ADR 0017 §2).

Reference values: the v1 sample of omf-python (`tests/data/ext`, Д-21) — its arrays inflated with
`zlib.decompress` (`omf_builder.sample_array`, independent of the reader) and the counts of
`expected/test_file.v1.json`; hand-made files of `omf_builder.RawOmf`. Coordinates must equal the array of the
file + (origin of the project + origin of the geometry) bit for bit; with zero origins — the array itself (−0.0
too). Grids: nodes in Fortran order (u fastest), two triangles a cell; cells of a volume 2 × 3 × 4 — value n at
cell i + 2·(j + 3·k).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.formats import omf_read as orr
from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet
from bf_core.formats.meshset import MeshSet
from tests.formats.omf_builder import EXPECTED, V1, RawOmf, sample_array

PO = (512_000.25, 4_512_000.125, -3.5)  # project origin
GO = (0.1, 0.2, 0.3)  # geometry origin: 0.1 + 512000.25 is not exact — the order of the sums matters


def read_all(path: Path, **kw: Any) -> tuple[orr.OmfReader, list[Any]]:
    with orr.open_reader(path) as r:
        return r, list(r.elements(**kw))


# --- the sample -------------------------------------------------------------------------------------------


def test_sample_gives_two_wireframes_a_layer_and_a_volume() -> None:
    expected = {e["name"]: e for e in json.loads((EXPECTED / "test_file.v1.json").read_text("utf-8"))["elements"]}
    r, out = read_all(V1)
    assert [type(o).__name__ for o in out] == ["LineSet", "MeshSet", "MeshSet", "OmfVolume"]
    layer, tri, grid, vol = out
    # trisurf: the vertices and triangles of the file as they are, bit for bit (origins are zero)
    assert isinstance(tri, MeshSet) and (tri.name, tri.color) == ("trisurf", "#64c8c8")
    assert (tri.point_count, tri.triangle_count) == (expected["trisurf"]["vertices"], expected["trisurf"]["triangles"])
    assert tri.xyz.tobytes() == sample_array("trisurf", "vertices").tobytes()
    assert (tri.tri == sample_array("trisurf", "triangles")).all()
    assert tri.source == {"format": "omf", "name": "trisurf", "element": r.composition.elements[2].key}
    # gridsurf: 10 × 15 cells — 11 × 16 nodes, two triangles a cell; offset_w moves the nodes along u × v = −Y
    assert isinstance(grid, MeshSet) and grid.name == "gridsurf" and grid.color == "#c7c7c7"
    nu, nv = expected["gridsurf"]["tensor_u"], expected["gridsurf"]["tensor_v"]
    assert (grid.point_count, grid.triangle_count) == ((nu + 1) * (nv + 1), 2 * nu * nv)
    w = sample_array("gridsurf", "offset_w")
    assert grid.xyz[:, 0].min() == 50 and grid.xyz[:, 0].max() == 60 and grid.xyz[:, 2].max() == 65
    assert (grid.xyz[:, 1] == 50.0 - w).all() and grid.xyz[:, 1].min() < 50
    # the line: 100 vertices and 50 segments in the file; two segments follow each other — 49 lines
    assert isinstance(layer, LineSet) and (layer.name, layer.color) == ("Random Line", "#0000ff")
    assert len(sample_array("Random Line", "vertices")) == expected["Random Line"]["vertices"] == 100
    assert layer.line_count == 49 and layer.point_count - layer.line_count == expected["Random Line"]["segments"]
    assert isinstance(vol, orr.OmfVolume) and vol.name == "vol" and vol.entry.counts == expected["vol"]["tensor"]
    assert [d.name for d, _obj in vol.data] == ["Random Data"] and vol.origin.tolist() == [10.0, 10.0, -10.0]
    # not carried over, by names: the points, 3 textures, data on vertices and faces, changing segment data
    text = "\n".join(r.warnings)
    assert "Точки «Random Points»: элемент — точки не переносятся" in text
    assert "Точки «Random Points»: текстуры (2)" in text and "Поверхность «gridsurf»: текстуры (1)" in text
    assert sum(x["count"] for x in r.left_out if x["what"] == "текстуры") == 3
    for element, datum in [("trisurf", "rand face data"), ("gridsurf", "rand vert data"),
                           ("Random Line", "rand vert data"), ("Random Line", "rand segment data")]:  # fmt: skip
        assert any(x["element"] == element and x["what"] == f"данные «{datum}»" for x in r.left_out), datum
    assert "меняются вдоль линии" in text and "вершин без сегментов не загружены" in text
    assert r.unread == [] and r.crs is None


def test_kinds_and_keys_choose_elements_and_progress_goes_up() -> None:
    seen: list[tuple[float, str | None]] = []
    with orr.open_reader(V1, lambda f, m: seen.append((f, m))) as r:
        meshes = list(r.surfaces())
        assert [m.name for m in meshes] == ["trisurf", "gridsurf"]
        key = r.composition.elements[3].key
        assert [m.name for m in r.surfaces(keys={key})] == ["gridsurf"]
        assert [v.name for v in r.volumes()] == ["vol"] and [ls.name for ls in r.line_layers()] == ["Random Line"]
    fractions = [f for f, _m in seen]
    assert fractions[-1] == 1.0 and all(0.0 <= f <= 1.0 for f in fractions)
    assert any(m and "«trisurf»" in m for _f, m in seen)


# --- coordinates and order --------------------------------------------------------------------------------


def test_coordinates_are_the_array_plus_the_origins_bit_for_bit(tmp_path: Path) -> None:
    rng = np.random.default_rng(5)
    v = rng.uniform(-1000, 1000, (40, 3))
    raw = RawOmf()
    raw.surface("s", v, rng.integers(0, 40, (30, 3)), origin=GO)
    raw.lines("l", v, [[0, 1], [1, 2], [5, 6]], origin=GO)
    _r, (mesh, layer) = read_all(raw.write(tmp_path / "o.omf", origin=PO))
    origin = np.asarray(PO) + np.asarray(GO)
    assert mesh.xyz.tobytes() == (v + origin).tobytes()
    assert layer.xyz.tobytes() == (v[[0, 1, 2, 5, 6]] + origin).tobytes()


def test_zero_origins_keep_the_array_and_negative_zero(tmp_path: Path) -> None:
    v = np.array([[-0.0, 1.0, 2.0], [3.0, -0.0, 5.0], [6.0, 7.0, -0.0]])
    raw = RawOmf()
    raw.surface("s", v, [[0, 1, 2]])
    raw.surface("f32", v.astype("<f4"), [[0, 1, 2]])
    _r, (mesh, m32) = read_all(raw.write(tmp_path / "z.omf"))
    assert mesh.xyz.tobytes() == v.tobytes() and np.signbit(mesh.xyz[0, 0])
    assert m32.xyz.dtype == np.float64 and m32.xyz.tobytes() == v.astype("<f4").astype(np.float64).tobytes()


def test_grid_nodes_in_fortran_order(tmp_path: Path) -> None:
    """2 × 3 cells: node n = i + 3·j at origin + u_i·axis_u + v_j·axis_v + w_n·(axis_u × axis_v)."""
    raw = RawOmf()
    w = np.arange(12.0) / 10
    geom = raw.add("SurfaceGridGeometry", origin=[100.0, 200.0, 300.0], tensor_u=[1.0, 2.0], tensor_v=[1.0, 1.0, 3.0],
                   axis_u=[2.0, 0.0, 0.0], axis_v=[0.0, 0.0, 1.0],
                   offset_w=raw.array("ScalarArray", w, "<f8"))  # fmt: skip
    raw.element("SurfaceElement", "g", geom)
    _r, (mesh,) = read_all(raw.write(tmp_path / "g.omf"))
    u, vv = [0.0, 1.0, 3.0], [0.0, 1.0, 2.0, 5.0]
    for j in range(4):
        for i in range(3):
            n = i + 3 * j
            assert mesh.xyz[n].tolist() == [100.0 + u[i], 200.0 - w[n], 300.0 + vv[j]]
    assert mesh.triangle_count == 2 * 2 * 3
    a = np.arange(6).reshape(3, 2)  # cells (j, i) → corner a = i + 3·j
    corners = (a % 2 + 3 * (a // 2)).reshape(-1)
    assert (mesh.tri[:6, 0] == corners).all() and (mesh.tri[:6, 1] == corners + 1).all()
    assert (mesh.tri[:6, 2] == corners + 4).all() and (mesh.tri[6:, 2] == corners + 3).all()


def test_grid_nodes_without_height_drop_their_triangles(tmp_path: Path) -> None:
    raw = RawOmf()
    geom = raw.add("SurfaceGridGeometry", origin=[0, 0, 0], tensor_u=[1.0], tensor_v=[1.0],
                   offset_w=raw.array("ScalarArray", [np.nan, 0, 0, 0], "<f8"))  # fmt: skip
    raw.element("SurfaceElement", "holes", geom)  # one cell, both triangles on node 0
    geom2 = raw.add("SurfaceGridGeometry", origin=[0, 0, 0], tensor_u=[1.0, 1.0], tensor_v=[1.0],
                    offset_w=raw.array("ScalarArray", [np.nan, 0, 0, 0, 0, 0], "<f8"))  # fmt: skip
    raw.element("SurfaceElement", "part", geom2)
    r, out = read_all(raw.write(tmp_path / "h.omf"))
    (part,) = out  # «holes» lost every triangle
    assert np.isfinite(part.xyz).all() and part.triangle_count == 2 and part.point_count == 4
    text = "\n".join(r.warnings)
    assert "«holes»: элемент — в поверхности нет треугольников" in text and "узлов грида без высоты: 1" in text


def test_volume_cell_data_in_fortran_order_2x3x4(tmp_path: Path) -> None:
    raw = RawOmf()
    n = raw.scalar("N", np.arange(24), "cells", dtype="<i8")
    zone = raw.mapped("ZONE", np.arange(24) % 3 - 1, ["а", "б"], "cells")
    raw.volume("v", ([1.0, 2.0], [1.0, 1.0, 3.0], [0.5] * 4), origin=(1.0, 2.0, 3.0), data=[n, zone])
    with orr.open_reader(raw.write(tmp_path / "v.omf", origin=(1000.0, 0.0, 0.0))) as r:
        (vol,) = r.volumes()
        assert vol.entry.counts == [2, 3, 4] and vol.entry.cells == 24 and vol.origin.tolist() == [1001.0, 2.0, 3.0]
        (_d, num), (_z, cat) = vol.data
        kind, values, labels = orr.column(r.file, num, 24, "N")
        assert kind == "number" and values.dtype == np.float64 and labels is None
        grid = values.reshape((2, 3, 4), order="F")
        for i in range(2):
            for j in range(3):
                for k in range(4):
                    assert grid[i, j, k] == i + 2 * (j + 3 * k)
        kind, codes, labels = orr.column(r.file, cat, 24, "ZONE")
        assert kind == "category" and labels == ["а", "б"] and codes.tolist()[:4] == [-1, 0, 1, -1]
        with pytest.raises(FormatError) as info:
            orr.column(r.file, num, 25, "N")
        assert info.value.code == "omf_damaged"


# --- lines ------------------------------------------------------------------------------------------------


def test_lines_chain_close_and_take_constant_segment_data(tmp_path: Path) -> None:
    v = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [50, 50, 5], [60, 50, 5], [70, 50, np.nan]], float)
    seg = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 4]]
    raw = RawOmf()
    au = raw.scalar("AU", [1.5, 1.5, 1.5, 1.5, np.nan, np.nan, np.nan], "segments", description="Единицы: g/t")
    code = raw.mapped("Код", [1, 1, 1, 1, 0, 0, 0], ["руда", "порода"], "segments")
    text = raw.data("StringData", "Участок", raw.strings(["В", "В", "В", "В", "", "", ""]), "segments")
    when = raw.data("DateTimeData", "Дата", raw.strings(["2026-01-02T00:00:00Z"] * 7, "DateTimeArray"), "segments")
    vary = raw.scalar("Глубина", np.arange(7.0), "segments")
    colours = raw.mapped("Цвет", np.zeros(7), None, "segments", colours=True)
    vdata = raw.scalar("V", np.arange(7.0), "vertices")
    dup = raw.scalar("au", [2.0] * 7, "segments")
    raw.lines("Контуры", v, seg, data=[au, code, text, when, vary, colours, vdata, dup], color="#4F9DDE")
    raw.lines("Контуры", v[:2], [[0, 1]])
    r, (layer, second) = read_all(raw.write(tmp_path / "l.omf"))
    assert layer.line_count == 2 and layer.closed.tolist() == [1, 1] and layer.color == "#4f9dde"
    assert layer.xyz.tobytes() == v[[0, 1, 2, 3, 0, 4, 5, 6, 4]].tobytes()  # closed: with the repeat
    assert layer.offsets.tolist() == [0, 5, 9] and np.isnan(layer.xyz[7, 2])  # an empty Z stays empty
    attrs = layer.attrs.to_pydict()
    assert list(attrs) == ["AU", "Код", "Участок", "Дата", "au (2)"]
    assert attrs["AU"][0] == 1.5 and np.isnan(attrs["AU"][1])
    assert attrs["Код"] == ["порода", "руда"] and attrs["Участок"] == ["В", None]
    assert attrs["Дата"] == ["2026-01-02T00:00:00Z"] * 2 and layer.units == {"AU": "g/t"}
    assert second.name == "Контуры (2)" and second.line_count == 1 and second.closed.tolist() == [0]
    text_ = "\n".join(r.warnings)
    assert "«Глубина» — значения меняются вдоль линии" in text_ and "«V» — данные на вершинах линий" in text_
    assert "«Цвет» — у данных нет легенды с подписями" in text_


def test_lines_without_segments_and_bad_numbers(tmp_path: Path) -> None:
    raw = RawOmf()
    raw.lines("empty", np.zeros((2, 3)), np.zeros((0, 2)))
    raw.lines("bad", np.zeros((2, 3)), [[0, 5]])
    raw.lines("good", np.ones((2, 3)), [[0, 1]])
    path = raw.write(tmp_path / "b.omf")
    with orr.open_reader(path) as r, pytest.raises(FormatError) as info:
        list(r.elements())
    assert info.value.code == "omf_damaged" and "вне списка вершин" in info.value.message
    r, out = read_all(path, skip_bad=True)
    assert [ls.name for ls in out] == ["good"]
    assert [(u["name"], u["error"]["code"]) for u in r.unread] == [("bad", "omf_damaged")]
    assert any("«empty»: элемент — в элементе нет сегментов" in w for w in r.warnings)
    assert any("«bad»: элемент — не загружен:" in w for w in r.warnings)


# --- descriptions, colours, damaged elements --------------------------------------------------------------


def test_crs_dtm_description_and_colours(tmp_path: Path) -> None:
    raw = RawOmf()
    raw.surface("Рельеф", np.eye(3), [[0, 1, 2]], description="Съёмка 2026\nBedrockForge: ЦТМ", color=[1, 2, 3])
    raw.surface("Тело", np.eye(3), [[0, 1, 2]], color="red")
    desc = "Система координат: Pulkovo 1942 / Gauss-Kruger zone 7 (EPSG:28407)"
    r, (dtm, body) = read_all(raw.write(tmp_path / "c.omf", description=desc))
    assert r.crs == {"name": "Pulkovo 1942 / Gauss-Kruger zone 7", "epsg": 28407}
    assert not any("Система координат" in w for w in r.warnings)
    assert dtm.source["dtm"] is True and dtm.attrs == [{"name": "Описание", "value": "Съёмка 2026"}]
    assert dtm.color == "#010203" and body.color is None and "dtm" not in body.source and body.attrs == []


def test_broken_block_of_an_element(tmp_path: Path) -> None:
    raw = RawOmf()
    raw.surface("first", np.eye(3), [[0, 1, 2]])
    raw.surface("second", np.eye(3), [[0, 1, 2]])
    geom = raw.add("SurfaceGeometry", origin=[0, 0, 0], vertices=raw.array("Vector3Array", np.eye(3), "<f8"),
                   triangles=raw.array("Int3Array", [[0.0, 1.0, 2.0]], "<f8"))  # fmt: skip
    raw.element("SurfaceElement", "float triangles", geom)
    path = raw.write(tmp_path / "x.omf")
    data = bytearray(path.read_bytes())
    second = raw.objects[raw.objects[raw.elements[1]]["geometry"]]["vertices"]
    blk = raw.objects[second]["array"]
    data[blk["start"] + 2 : blk["start"] + blk["length"]] = b"\xff" * (blk["length"] - 2)
    path.write_bytes(bytes(data))
    r, out = read_all(path, skip_bad=True)
    assert [m.name for m in out] == ["first"]
    assert {u["name"]: u["error"]["code"] for u in r.unread} == {
        "second": "omf_damaged",
        "float triangles": "omf_damaged",
    }
    assert "целыми" in r.unread[1]["error"]["message"]


def test_a_big_surface_reports_progress_inside_and_can_stop(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    raw = RawOmf()
    raw.surface("Тело", rng.uniform(0, 1, (300_000, 3)), rng.integers(0, 300_000, (600_000, 3)))
    path = raw.write(tmp_path / "big.omf")
    seen: list[float] = []

    def note(f: float, m: str | None) -> None:
        if m and "«Тело»" in m:
            seen.append(f)

    with orr.open_reader(path, note) as r, pytest.MonkeyPatch.context() as mp:
        from bf_core.formats import omf_file

        mp.setattr(omf_file, "CHUNK", 1 << 20)
        (mesh,) = list(r.surfaces())
    assert mesh.triangle_count == 600_000
    inside = [f for f in seen if 0 < f < 1]
    assert len(inside) >= 2 and inside == sorted(inside)

    class Stop(Exception):
        pass

    def stop(f: float, _m: str | None) -> None:
        if 0 < f < 1:
            raise Stop

    with orr.open_reader(path, stop) as r, pytest.raises(Stop):
        list(r.surfaces())
