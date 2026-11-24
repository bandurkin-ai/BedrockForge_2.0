"""The slice and the point of a block model (подшаг 17.24; ТЗ 8.7.5–8.7.6, ADR 0023 decisions 18–19; docs/progress/
e17-api.md §12): the raster of a plane (texture of cells, sub-cells ≤ 4096², sampled for an oblique plane, the mask `m`,
the filter, hidden categories, the half-open rule on an edge), the polygons of the section (the area is exact), the row
under a point on a face (a boundary of sub-blocks and of parents, a turned model, a gap) and the answer of `/at`
(NaN → null, a category → its label), also for a model read from its Parquet files."""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry, pick, restrict
from bf_core.blocks import schema as bs
from bf_core.blocks import slice as sl
from bf_core.blocks.schema import GridV2
from bf_core.blocks.slice_poly import Sectioner, section_polygons
from bf_core.db import dialect
from bf_core.storage.blobs import BlobStore
from tests.test_blocks_fields_store import make_model, project
from tests.test_blocks_model import make_sub, open_model


@pytest.fixture()
def blocks_router(monkeypatch: pytest.MonkeyPatch) -> None:
    import bf_core.api.app as app_module

    if "blocks" not in app_module.ROUTER_MODULES:
        monkeypatch.setattr(app_module, "ROUTER_MODULES", (*app_module.ROUTER_MODULES, "blocks"))


@pytest.fixture()
def bproj(blocks_router, pclient):  # type: ignore[no-untyped-def]
    assert pclient.post("/api/v1/projects", json={"name": "Блоки"}).status_code == 200
    return pclient


def grid_of(counts: Any = (6, 5, 4), size: Any = (10.0, 8.0, 5.0), div: Any = (4, 2, 2), rotation: float = 30.0,
            corner: Any = (1000.5, 2000.25, -300.0)) -> GridV2:  # fmt: skip
    return GridV2.of({"corner": list(corner), "counts": list(counts), "size": list(size), "div": list(div),
                      "rotation": rotation, "order": "F"})  # fmt: skip


def sub_rows(grid: GridV2, seed: int = 5, gaps: float = 0.12) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rows of a sparse model: every parent whole, split in halves along one axis or in octants, or absent; the rows
    in the order of the file."""
    rng = np.random.default_rng(seed)
    div = grid.sub.div
    items: list[tuple[int, tuple[int, int, int], tuple[int, int, int]]] = []
    for par in range(grid.cells):
        if rng.random() < gaps:
            continue
        kind = int(rng.integers(0, 5))
        if kind == 0:
            items.append((par, (0, 0, 0), div))
            continue
        axes = [int(rng.integers(0, 3))] if kind < 4 else [0, 1, 2]
        cut = [div[a] // 2 if a in axes else div[a] for a in range(3)]
        for off in itertools.product(*[(0, div[a] // 2) if a in axes else (0,) for a in range(3)]):
            items.append((par, off, tuple(cut)))  # type: ignore[arg-type]
    p = np.array([t[0] for t in items], dtype=np.uint64)
    s = np.array([t[1] for t in items], dtype=np.uint8)
    w = np.array([t[2] for t in items], dtype=np.uint8)
    order = bs.sort_rows(p, s)
    return p[order], s[order], w[order]


def whole(grid: GridV2) -> tuple[np.ndarray, None, None]:
    return np.arange(grid.cells, dtype=np.uint64), None, None


def reference(
    grid: GridV2, p: Any, s: Any, w: Any, passed: Any, values: Any, r: sl.Raster
) -> tuple[np.ndarray, np.ndarray]:
    """What the raster must hold: the row that holds the centre of every pixel, found by `geometry.locate_rows`."""
    lay = r.layout
    cu = (np.arange(lay.width) + 0.5) * lay.du
    cv = (np.arange(lay.height) + 0.5) * lay.dv
    pts = lay.origin + lay.u * cu[None, :, None] + lay.v * cv[:, None, None]
    row = geometry.locate_rows(p, s, w, grid, pts.reshape(-1, 3))
    ok = row >= 0
    ok[ok] = passed[row[ok]] if passed is not None else True
    m = np.zeros(len(row), dtype=np.uint8)
    val = np.full(len(row), np.nan, dtype=np.float32)
    if values is None:
        m[ok] = 1
    else:
        v = np.asarray(values)[row[ok]].astype(np.float64)
        gap = np.isnan(v) if r.kind == "number" else v < 0
        m[ok] = np.where(gap, 2, 1)
        val[ok] = np.where(gap, np.nan, v).astype(np.float32)
    return val, m


def same(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.array_equal(a, b, equal_nan=True))


def raster_matches(grid: GridV2, p: Any, s: Any, w: Any, r: sl.Raster, passed: Any = None, values: Any = None) -> None:
    val, m = reference(grid, p, s, w, passed, values, r)
    assert same(r.m, m), f"{int((r.m != m).sum())} of {len(m)} pixels differ"
    assert same(r.value, val)


def model_data(grid: GridV2, seed: int = 5) -> dict[str, Any]:
    p, s, w = sub_rows(grid, seed)
    rng = np.random.default_rng(seed + 1)
    n = len(p)
    au = rng.lognormal(0.0, 1.0, n)
    au[rng.random(n) < 0.1] = np.nan
    dom = rng.integers(-1, 3, n).astype(np.int32)
    return {"p": p, "s": s, "w": w, "au": au, "dom": dom, "passed": rng.random(n) < 0.8}


# --- the raster: the plane ⟂ an axis ------------------------------------------------------------------------


def test_the_texture_of_a_plain_model_is_one_pixel_a_cell() -> None:
    grid = grid_of(div=(1, 1, 1), rotation=0.0, corner=(100.0, 200.0, -50.0), counts=(4, 3, 2), size=(10.0, 10.0, 5.0))
    p, s, w = whole(grid)
    au = np.arange(24, dtype=np.float64)
    au[5] = np.nan
    r = sl.slice_rows(grid, sl.plane_of(grid, [100, 200, -47.5], [0, 0, 1]), p, s, w, None, au, "number", sub=False)
    assert (r.layout.kind, r.width, r.height, r.passed) == ("cells", 4, 3, 12)
    assert r.value.dtype == np.float32 and r.m.dtype == np.uint8
    head = r.header()
    assert head["frame"] == {
        "origin": [100.0, 200.0, -47.5],
        "u": [1.0, 0.0, 0.0],
        "v": [0.0, 1.0, 0.0],
        "du": 10.0,
        "dv": 10.0,
    }
    assert head["raster"] == "cells" and head["kind"] == "number" and head["plane"]["normal"] == [0.0, 0.0, 1.0]
    want = au[:12].astype(np.float32)
    assert same(r.value, want)  # row j (bottom up), column i — the layer k = 0
    assert r.m.tolist() == [1] * 5 + [2] + [1] * 6  # a gap in the field is 2 and NaN, never 0
    top = sl.slice_rows(grid, sl.plane_of(grid, [100, 200, -42.5], [0, 0, 1]), p, s, w, None, au, "number", sub=False)
    assert same(top.value, au[12:].astype(np.float32))


def test_axes_other_than_z_and_the_frame_of_the_faces() -> None:
    grid = grid_of(div=(1, 1, 1), rotation=0.0, corner=(0.0, 0.0, 0.0), counts=(4, 3, 2), size=(10.0, 20.0, 5.0))
    p, _, _ = whole(grid)
    au = np.arange(24, dtype=np.float64)
    ry = sl.slice_rows(grid, sl.plane_of(grid, [0, 30, 0], [0, 1, 0]), p, None, None, None, au, "number", sub=False)
    assert (ry.layout.kind, ry.width, ry.height) == ("cells", 2, 4)  # Y: u = Z, v = X
    assert ry.layout.u.tolist() == [0.0, 0.0, 1.0] and ry.layout.v.tolist() == [1.0, 0.0, 0.0]
    assert (ry.layout.du, ry.layout.dv) == (5.0, 10.0)
    assert same(ry.value.reshape(4, 2), au.reshape(2, 3, 4)[:, 1, :].T.astype(np.float32))
    rx = sl.slice_rows(grid, sl.plane_of(grid, [25, 0, 0], [-1, 0, 0]), p, None, None, None, au, "number", sub=False)
    assert (rx.width, rx.height) == (3, 2)  # X: u = Y, v = Z; the sign of the normal is the side only
    assert same(rx.value.reshape(2, 3), au.reshape(2, 3, 4)[:, :, 2].astype(np.float32))


def test_a_plane_on_an_edge_takes_the_cell_above_it_and_the_top_is_empty() -> None:
    grid = grid_of(div=(1, 1, 1), rotation=0.0, corner=(0.0, 0.0, 0.0), counts=(3, 2, 4), size=(10.0, 10.0, 5.0))
    p, _, _ = whole(grid)
    au = np.arange(24, dtype=np.float64)
    on = sl.slice_rows(grid, sl.plane_of(grid, [0, 0, 10], [0, 0, 1]), p, None, None, None, au, "number", sub=False)
    above = sl.slice_rows(grid, sl.plane_of(grid, [0, 0, 11], [0, 0, 1]), p, None, None, None, au, "number", sub=False)
    assert same(on.value, above.value) and same(on.value, au[12:18].astype(np.float32))
    for z in (20.0, 25.0, -0.001):  # the top boundary is not in the model ([lo; hi)), nor is anything beyond
        r = sl.slice_rows(grid, sl.plane_of(grid, [0, 0, z], [0, 0, 1]), p, None, None, None, au, "number", sub=False)
        assert (r.width, r.height, len(r.m), r.passed) == (0, 0, 0, 0)


def test_the_filter_hidden_categories_and_a_field_less_slice() -> None:
    grid = grid_of(div=(1, 1, 1), rotation=0.0, corner=(0.0, 0.0, 0.0), counts=(3, 2, 2), size=(10.0, 10.0, 5.0))
    p, _, _ = whole(grid)
    pl = sl.plane_of(grid, [0, 0, 2.5], [0, 0, 1])
    passed = np.array([1, 0, 1, 1, 1, 0] + [1] * 6, dtype=bool)
    codes = np.array([0, 1, 2, -1, 1, 2] + [0] * 6, dtype=np.int32)
    r = sl.slice_rows(grid, pl, p, None, None, passed, codes, "category", sub=False, hidden=[2])
    assert r.m.tolist() == [1, 0, 0, 2, 1, 0]  # filtered (f = 0) → 0; hidden code → 0; code −1 → 2
    assert r.value[0] == 0.0 and r.value[4] == 1.0 and np.isnan(r.value[3]) and r.passed == 3
    none = sl.slice_rows(grid, pl, p, None, None, passed, None, None, sub=False)
    assert none.kind is None and none.m.tolist() == [1, 0, 1, 1, 1, 0] and np.isnan(none.value).all()
    with pytest.raises(ValueError):
        sl.Slicer(grid, pl, kind="number").add(p, None, None, None, None)


def test_subcells_of_a_turned_model_on_every_axis_match_the_cell_that_holds_each_pixel() -> None:
    grid = grid_of()
    d = model_data(grid)
    p, s, w, au, passed = d["p"], d["s"], d["w"], d["au"], d["passed"]
    assert len(p) > grid.cells
    for origin, normal in (([1010, 2010, -290.3], [0, 0, 1]), ([1010, 2010, -290.3], [0, 0, -1]),
                           ([1011.7, 2018.2, -290], [np.cos(np.radians(30)), np.sin(np.radians(30)), 0]),
                           ([1013.1, 2016.4, -290], [-np.sin(np.radians(30)), np.cos(np.radians(30)), 0])):  # fmt: skip
        pl = sl.plane_of(grid, origin, normal)
        assert pl.axis >= 0
        r = sl.slice_rows(grid, pl, p, s, w, passed, au, "number", sub=True)
        assert r.layout.kind == "subcells" and r.width * r.height > 0 and r.m.any()
        raster_matches(grid, p, s, w, r, passed, au)
    dom = d["dom"]
    pl = sl.plane_of(grid, [1010, 2010, -290.3], [0, 0, 1])
    r = sl.slice_rows(grid, pl, p, s, w, passed, dom, "category", hidden=[0])
    assert not (r.value[r.m == 1] == 0).any() and (r.m == 2).any()  # code 0 is hidden, code −1 is a gap
    visible = passed & (dom != 0)
    raster_matches(grid, p, s, w, r, visible, dom)
    raster_matches(grid, p, s, w, sl.slice_rows(grid, pl, p, s, w, None, None, None), None, None)  # no field


def test_a_turn_by_a_right_angle_keeps_the_frame_exact() -> None:
    grid = grid_of(rotation=90.0, div=(1, 1, 1), counts=(3, 2, 2), size=(10.0, 10.0, 5.0), corner=(0.0, 0.0, 0.0))
    pl = sl.plane_of(grid, [0, 0, 2.5], [0, 0, 1])
    lay = sl.layout(grid, pl, sub=False)
    assert (
        lay.u.tolist() == [0.0, 1.0, 0.0]
        and lay.v.tolist() == [-1.0, 0.0, 0.0]
        and lay.origin.tolist() == [0.0, 0.0, 2.5]
    )
    px = sl.plane_of(grid, [0, 0, 0], [0, 1, 0])  # the local X axis of the model is the project Y axis
    assert px.axis == 0 and sl.layout(grid, px, sub=False).kind == "cells"


def test_a_big_raster_is_cut_to_max_px_by_a_coarser_pixel() -> None:
    grid = grid_of(counts=(600, 600, 1), div=(8, 8, 1), size=(1.0, 1.0, 1.0), rotation=0.0, corner=(0.0, 0.0, 0.0))
    n = grid.cells
    p = np.arange(n, dtype=np.uint64)
    s = np.zeros((n, 3), dtype=np.uint8)
    w = np.tile(np.array([8, 8, 1], dtype=np.uint8), (n, 1))
    val = (p % 97).astype(np.float64)
    pl = sl.plane_of(grid, [0, 0, 0.5], [0, 0, 1])
    r = sl.slice_rows(grid, pl, p, s, w, None, val, "number", sub=True, max_px=4096)
    assert (r.width, r.height, r.layout.kind) == (2400, 2400, "sampled")  # 4800 sub-cells a side, every second one
    assert r.layout.du == r.layout.dv == 0.25
    fine = sl.slice_rows(grid, pl, p, s, w, None, val, "number", sub=False)  # whole parents: 600 × 600
    assert (fine.width, fine.height, fine.layout.kind, fine.layout.du) == (600, 600, "cells", 1.0)
    big = sl.layout(grid_of(counts=(4000, 4000, 1), div=(1, 1, 1), size=(1.0, 1.0, 1.0), rotation=0.0), pl, sub=False)
    assert (big.width, big.height, big.kind) == (4000, 4000, "cells")  # up to 4096² cells, no coarsening
    raster_matches(grid, p, s, w, r, None, val)
    small = sl.slice_rows(grid, pl, p, s, w, None, val, "number", sub=True, max_px=256)
    assert (small.width, small.height) == (253, 253) and small.layout.kind == "sampled"
    raster_matches(grid, p, s, w, small, None, val)
    for bad in (63, 4097, "x"):
        with pytest.raises(ApiError) as e:
            sl.layout(grid, pl, max_px=bad)  # type: ignore[arg-type]
        assert e.value.status == 422 and e.value.extra["param"] == "max_px"


def test_a_variable_grid_is_sampled_on_the_smallest_step() -> None:
    tensors = {"x": [10.0, 20.0, 5.0], "y": [10.0, 30.0], "z": [4.0, 8.0]}
    grid = GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": [3, 2, 2], "tensors": tensors,
                      "rotation": 0.0, "order": "F"})  # fmt: skip
    p, _, _ = whole(grid)
    au = np.arange(12, dtype=np.float64)
    for origin, normal in (([0, 0, 2.0], [0, 0, 1]), ([12.0, 0, 0], [1, 0, 0]), ([5, 5, 5], [1, 2, 3])):
        r = sl.slice_rows(grid, sl.plane_of(grid, origin, normal), p, None, None, None, au, "number", sub=False)
        assert r.layout.kind == "sampled" and r.m.any()
        raster_matches(grid, p, None, None, r, None, au)


# --- the raster: an oblique plane ---------------------------------------------------------------------------


def test_an_oblique_plane_is_sampled_from_the_cell_under_each_pixel() -> None:
    grid = grid_of()
    d = model_data(grid, seed=11)
    p, s, w, au, passed = d["p"], d["s"], d["w"], d["au"], d["passed"]
    rng = np.random.default_rng(2)
    centre = grid.to_project([np.asarray(grid.lengths()) / 2.0 + 0.137])[0]
    for _ in range(4):
        normal = rng.normal(size=3)
        pl = sl.plane_of(grid, centre + rng.normal(size=3), normal)
        assert pl.axis == -1
        r = sl.slice_rows(grid, pl, p, s, w, passed, au, "number", sub=True)
        lay = r.layout
        assert lay.kind == "sampled" and r.m.any() and lay.width <= 4096 and lay.height <= 4096
        assert abs(lay.du - lay.dv) == 0.0 and abs(float(np.dot(lay.u, lay.v))) < 1e-12
        assert abs(float(np.dot(np.cross(lay.u, lay.v), pl.normal)) - 1.0) < 1e-12  # u × v = n
        assert lay.du >= sl.min_subcell(grid) - 1e-12
        raster_matches(grid, p, s, w, r, passed, au)


def test_the_raster_is_the_same_for_any_portions_of_rows() -> None:
    grid = grid_of()
    d = model_data(grid, seed=3)
    p, s, w, au, passed = d["p"], d["s"], d["w"], d["au"], d["passed"]
    for origin, normal in (([1010, 2010, -290.3], [0, 0, 1]), ([1010, 2010, -290.3], [1, 2, 3]),
                           ([1010, 2010, -290.3], [np.cos(np.radians(30)), np.sin(np.radians(30)), 0])):  # fmt: skip
        pl = sl.plane_of(grid, origin, normal)
        one = sl.slice_rows(grid, pl, p, s, w, passed, au, "number")
        for step in (1, 7, 100):
            sv = sl.Slicer(grid, pl, kind="number")
            for a in range(0, len(p), step):
                b = a + step
                sv.add(p[a:b], s[a:b], w[a:b], passed[a:b], au[a:b])
            got = sv.finish()
            assert same(got.value, one.value) and same(got.m, one.m) and got.passed == one.passed


def test_a_plane_across_z_reads_one_layer_of_parents() -> None:
    grid = grid_of()
    d = model_data(grid)
    p = d["p"]
    sv = sl.Slicer(grid, sl.plane_of(grid, [1010, 2010, -290.3], [0, 0, 1]))
    lo, hi = sv.p_range() or (0, 0)
    layer = grid.counts[0] * grid.counts[1]
    assert (lo, hi) == (layer, 2 * layer)  # z = 9.7 m above the corner, 5 m a layer: k = 1
    sv = sl.Slicer(grid, sl.plane_of(grid, [1010, 2010, -288.0], [0, 0, 1]))
    assert sv.p_range() == (2 * layer, 3 * layer)  # z = 12 m above the corner, 5 m a layer: k = 2
    assert sl.Slicer(grid, sl.plane_of(grid, [1010, 2010, -290.3], [1, 0, 0])).p_range() is None
    far = sl.Slicer(grid, sl.plane_of(grid, [0, 0, 1e6], [0, 0, 1]))
    assert far.p_range() is None and far.layout.width == 0 and p.size > 0 and not far.m.size


def test_a_bad_plane_is_422_with_a_param() -> None:
    grid = grid_of()
    cases = [([0, 0, 0], [0, 0, 0], "plane.normal"), ([0, 0, 0], [1, np.nan, 0], "plane.normal"),
             ([0, 0], [0, 0, 1], "plane.origin"), (["a", 0, 0], [0, 0, 1], "plane.origin")]  # fmt: skip
    for origin, normal, param in cases:
        with pytest.raises(ApiError) as e:
            sl.plane_of(grid, origin, normal)  # type: ignore[arg-type]
        assert (e.value.status, e.value.code, e.value.extra["param"]) == (422, "bad_param", param) and e.value.hint


# --- the polygons -------------------------------------------------------------------------------------------


def ring_area(ring: np.ndarray, n: np.ndarray) -> float:
    v = ring - ring[0]
    return 0.5 * abs(float(np.dot(np.cross(v[1:-1], v[2:]).sum(axis=0), n)))


def ccw(ring: np.ndarray, n: np.ndarray) -> bool:
    k = len(ring)
    turn = [
        float(np.dot(np.cross(ring[(i + 1) % k] - ring[i], ring[(i + 2) % k] - ring[(i + 1) % k]), n)) for i in range(k)
    ]
    return all(t > -1e-9 for t in turn) and sum(turn) > 0


def test_the_hexagon_of_a_cube_has_the_exact_area() -> None:
    side = 12.0
    for div, rotation in (((1, 1, 1), 0.0), ((2, 2, 2), 30.0), ((4, 2, 2), -75.0)):
        grid = grid_of(counts=(3, 3, 3), size=(4.0, 4.0, 4.0), div=div, rotation=rotation, corner=(10.0, -5.0, 3.0))
        p, s, w = whole(grid) if div == (1, 1, 1) else sub_rows(grid, 1, gaps=0.0)
        centre = grid.to_project([[side / 2] * 3])[0]
        n_loc = np.array([1.0, 1.0, 1.0])
        sn, cs = grid.sin_cos()
        normal = np.array([cs * n_loc[0] - sn * n_loc[1], sn * n_loc[0] + cs * n_loc[1], n_loc[2]])
        pl = sl.plane_of(grid, centre, normal)
        poly = section_polygons(grid, pl, p, s, w, None, sub=div != (1, 1, 1))
        want = 3.0 * math.sqrt(3.0) / 4.0 * side * side  # the regular hexagon x + y + z = 3L/2
        assert abs(poly.total - want) <= 1e-12 * want
        assert abs(float(poly.area.sum()) - want) <= 1e-12 * want and not poly.truncated and poly.count == len(poly)
        assert len(poly) >= 19 and set(poly.n.tolist()) <= {3, 4, 5, 6}
        for i in range(0, len(poly), max(len(poly) // 40, 1)):
            ring = poly.vertices(i)
            assert np.abs((ring - centre) @ pl.normal).max() < 1e-9
            assert abs(ring_area(ring, pl.normal) - poly.area[i]) <= 1e-9 * max(poly.area[i], 1.0)
            assert ccw(ring, pl.normal)


def test_polygons_of_a_plane_across_an_axis_and_the_side_of_the_normal() -> None:
    grid = grid_of(counts=(4, 3, 2), size=(10.0, 8.0, 5.0), div=(1, 1, 1), rotation=0.0, corner=(0.0, 0.0, 0.0))
    p, _, _ = whole(grid)
    for normal in ([0, 0, 1], [0, 0, -1]):
        pl = sl.plane_of(grid, [0, 0, 7.5], normal)
        poly = section_polygons(grid, pl, p, None, None, None, sub=False)
        assert poly.total == 4 * 3 * 80.0 == float(poly.area.sum()) and poly.count == 12 and set(poly.n.tolist()) == {4}
        assert (poly.row == np.arange(12, 24)).all()  # the layer k = 1
        assert all(ccw(poly.vertices(i), np.array(normal, dtype=float)) for i in range(12))
        assert (poly.ring[:, :, 2] == 7.5).all()
    on = section_polygons(grid, sl.plane_of(grid, [0, 0, 5.0], [0, 0, 1]), p, None, None, None, sub=False)
    assert on.count == 12 and (on.row == np.arange(12, 24)).all()  # on the shared face: the layer above, once
    top = section_polygons(grid, sl.plane_of(grid, [0, 0, 10.0], [0, 0, 1]), p, None, None, None, sub=False)
    assert top.count == 0 and top.total == 0.0 and len(top) == 0
    touch = section_polygons(grid, sl.plane_of(grid, [10, 8, 5], [1, 1, 0]), p, None, None, None, sub=False)
    assert all(v >= 3 for v in touch.n) and touch.total > 0  # only the cells the plane crosses, not those it touches
    corner = section_polygons(grid, sl.plane_of(grid, [0, 0, 0], [1, 1, 1]), p, None, None, None, sub=False)
    assert corner.count == 0  # through the corner of the model only


def test_the_filter_the_limit_and_the_portions_of_polygons() -> None:
    grid = grid_of()
    d = model_data(grid, seed=9)
    p, s, w, passed = d["p"], d["s"], d["w"], d["passed"]
    pl = sl.plane_of(grid, grid.to_project([np.asarray(grid.lengths()) / 2.0 + 0.3])[0], [1, 2, 3])
    full = section_polygons(grid, pl, p, s, w, None)
    some = section_polygons(grid, pl, p, s, w, passed)
    assert 0 < some.total < full.total and passed[some.row].all() and some.count == int(passed[full.row].sum())
    cut = section_polygons(grid, pl, p, s, w, None, limit=10)
    assert cut.truncated and len(cut) == 10 and cut.count == full.count and cut.total == full.total
    assert (cut.row == full.row[:10]).all() and np.array_equal(cut.ring, full.ring[:10])
    for step in (1, 13, 200):
        sec = Sectioner(grid, pl)
        for a in range(0, len(p), step):
            sec.add(p[a : a + step], s[a : a + step], w[a : a + step], None, first=a)
        got = sec.finish()
        assert got.total == full.total and (got.row == full.row).all() and np.array_equal(got.ring, full.ring)
    for bad in (0, sl.MAX_LIMIT + 1):
        with pytest.raises(ApiError) as e:
            Sectioner(grid, pl, limit=bad)
        assert e.value.extra["param"] == "limit"
    # the area of the section is the area of the raster: pixels × du × dv, up to the pixel at the edges
    r = sl.slice_rows(grid, pl, p, s, w, None, None, None)
    assert abs(int((r.m > 0).sum()) * r.layout.du * r.layout.dv - full.total) < 0.05 * full.total


# --- the point ----------------------------------------------------------------------------------------------


def face_points(grid: GridV2, p: np.ndarray, s: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The centre of every face of every row (project axes) and the outward normal there, and the row."""
    box = geometry.row_boxes(grid, p, s, w)
    pts, nrm, rows = [], [], []
    sn, cs = grid.sin_cos()
    for a in range(3):
        for side in (0, 1):
            c = (box[:, :3] + box[:, 3:]) / 2.0
            c[:, a] = box[:, a + 3 * side]
            n = np.zeros(3)
            n[a] = 1.0 if side else -1.0
            pts.append(grid.to_project(c))
            nrm.append(np.tile([cs * n[0] - sn * n[1], sn * n[0] + cs * n[1], n[2]], (len(p), 1)))
            rows.append(np.arange(len(p)))
    return np.concatenate(pts), np.concatenate(nrm), np.concatenate(rows)


def test_a_point_on_any_face_with_its_normal_is_in_the_block_that_owns_the_face() -> None:
    grid = grid_of()
    p, s, w = sub_rows(grid, seed=21)
    pts, nrm, rows = face_points(grid, p, s, w)
    wrong = 0
    for k in range(0, len(pts), 1):
        hit = pick.find(grid, p, s, w, pts[k], nrm[k])
        wrong += hit is None or hit.row != rows[k]
    assert wrong == 0  # neighbours of every size, boundaries of sub-blocks and of parents, the model turned 30°
    # the answer describes the row: the numbers of geometry
    k = 17
    hit = pick.find(grid, p, s, w, pts[k], nrm[k])
    assert hit is not None
    r = hit.row
    assert hit.p == int(p[r]) and hit.sub == (*map(int, s[r]), *map(int, w[r]))
    assert np.allclose(hit.centre, geometry.centres(grid, p[r : r + 1], s[r : r + 1], w[r : r + 1])[0], atol=1e-12)
    assert np.allclose(hit.size, geometry.row_widths(grid, p[r : r + 1], s[r : r + 1], w[r : r + 1])[0], atol=1e-12)
    i, j, kk = geometry.row_ijk(grid, p[r : r + 1])
    assert hit.ijk == (int(i[0]), int(j[0]), int(kk[0]))


def test_a_point_on_an_edge_without_a_normal_is_in_the_cell_above_and_a_gap_or_outside_is_none() -> None:
    grid = grid_of(counts=(3, 2, 2), size=(10.0, 10.0, 5.0), div=(1, 1, 1), rotation=0.0, corner=(0.0, 0.0, 0.0))
    p = np.array([0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11], dtype=np.uint64)  # the parent 4 is missing
    assert pick.find(grid, p, None, None, [10.0, 0.0, 0.0], sub=False).row == 1  # type: ignore[union-attr]
    assert pick.find(grid, p, None, None, [10.0, 0.0, 0.0], [1, 0, 0], sub=False).row == 0  # type: ignore[union-attr]
    assert pick.find(grid, p, None, None, [15.0, 15.0, 2.0], sub=False) is None  # the gap, P = 4
    for out in ([-0.1, 5, 2], [30.0, 5, 2], [5, 5, 10.0], [5, 20.0, 2]):
        assert pick.find(grid, p, None, None, out, sub=False) is None
    for bad in ([1, 2], [1, 2, np.nan], ["a", 1, 2]):
        with pytest.raises(ApiError) as e:
            pick.find(grid, p, None, None, bad, sub=False)  # type: ignore[arg-type]
        assert e.value.status == 422 and e.value.extra["param"] == "point"
    same_pt = pick.find(grid, p, None, None, [5, 5, 2], [0, 0, 0], sub=False)  # a zero normal moves nothing
    assert same_pt is not None and same_pt.point == (5.0, 5.0, 2.0)


def test_a_point_is_moved_by_a_micron_of_the_smallest_subcell_against_the_normal() -> None:
    grid = grid_of(rotation=0.0, corner=(0.0, 0.0, 0.0), counts=(2, 2, 2), size=(8.0, 8.0, 4.0), div=(4, 2, 2))
    got = pick.shifted(grid, [8.0, 4.0, 2.0], [2.0, 0.0, 0.0])
    assert np.allclose(got, [8.0 - 2e-6, 4.0, 2.0], rtol=0, atol=1e-15)  # the smallest sub-cell: 2 × 4 × 2 → 2 m
    assert pick.shifted(grid, [8.0, 4.0, 2.0], None).tolist() == [8.0, 4.0, 2.0]


# --- a model read from its files ----------------------------------------------------------------------------


def test_the_row_of_a_stored_model_is_found_from_one_row_group_and_described(bproj) -> None:  # type: ignore[no-untyped-def]
    pr = project(bproj)
    store = BlobStore(pr.folder)
    mid, p, s, w, au = make_sub(pr, store)
    model = open_model(pr, store, mid)
    assert model.sub and len(p) > 250
    grid = model.grid
    pts, nrm, rows = face_points(grid, p.astype(np.uint64), s.astype(np.uint8), w.astype(np.uint8))
    pick_rows = list(range(0, len(pts), 11))
    for k in pick_rows:
        hit = pick.find_in_model(model, pts[k], nrm[k])
        assert hit is not None and hit.row == rows[k]
        whole_hit = pick.find(grid, p.astype(np.uint64), s.astype(np.uint8), w.astype(np.uint8), pts[k], nrm[k])
        assert whole_hit == hit
    r = len(p) // 2
    hit = pick.find_in_model(
        model, geometry.centres(grid, p[r : r + 1].astype(np.uint64), s[r : r + 1], w[r : r + 1])[0]
    )
    assert hit is not None and hit.row == r
    chunk = pick.fetch_row(model, hit, ["AU"])
    assert chunk.start == r and int(chunk.p[0]) == int(p[r]) and chunk.s is not None and chunk.cols["AU"].shape == (1,)
    body = pick.describe(model, hit, chunk, 0.25)
    assert body["found"] and body["row"] == r and body["p"] == int(p[r]) and body["f"] == 0.25 and body["passed"]
    assert body["sub"] == [*map(int, s[r]), *map(int, w[r])]
    want = None if np.isnan(au[r]) else float(au[r])
    assert body["values"] == {"AU": want}
    cached = pick.chunk_of(hit, {"AU": au, "X": au * 2}, ["AU"])  # the same from whole columns (the cache of a route)
    assert cached.start == r and cached.p.tolist() == chunk.p.tolist() and cached.s.tolist() == chunk.s.tolist()  # type: ignore[union-attr]
    assert same(cached.cols["AU"], chunk.cols["AU"]) and list(pick.chunk_of(hit, {"AU": au, "X": au}).cols) == [
        "AU",
        "X",
    ]
    nan_row = int(np.flatnonzero(np.isnan(au))[0])
    hit = pick.find_in_model(
        model,
        geometry.centres(
            grid, p[nan_row : nan_row + 1].astype(np.uint64), s[nan_row : nan_row + 1], w[nan_row : nan_row + 1]
        )[0],
    )
    assert hit is not None and pick.describe(model, hit, pick.fetch_row(model, hit, ["AU"]))["values"] == {"AU": None}
    assert pick.describe(model, hit)["values"] == {} and pick.describe(model, None) == {"found": False}
    assert pick.describe(model, hit, pick.fetch_row(model, hit, ["AU"]), 0.0)["passed"] is False
    outside = grid.to_project([[1e5, 0.0, 0.0]])[0]
    assert pick.find_in_model(model, outside) is None
    with pytest.raises(ApiError) as e:
        pick.fields_of(model, ["нет такого"])
    assert e.value.status == 404 and e.value.code == "block_field_not_found"
    assert pick.fields_of(model, None) == ["AU"] and pick.fields_of(model, ["au"]) == ["AU"]


def test_the_categories_and_the_filter_of_a_stored_model_in_the_answer(bproj) -> None:  # type: ignore[no-untyped-def]
    pr = project(bproj)
    store = BlobStore(pr.folder)
    mid = make_model(pr, store)
    model = open_model(pr, store, mid)
    cols = model.columns(["AU", "CU", "N", "DOM"])
    centres = model.centres()
    for row in (0, 7, 100, 349):
        hit = pick.find_in_model(model, centres[row])
        assert hit is not None and hit.row == row and hit.sub is None
        names = pick.fields_of(model, None)
        chunk = pick.fetch_row(model, hit, names)
        with dialect.read_tx(pr.engine) as conn:
            rs = restrict.prepare(model, {"cond": {"field": "AU", "op": ">", "value": 1}}, conn=conn, store=store)
        f = float(rs.weights(pick.fetch_row(model, hit, rs.fields))[0])
        body = pick.describe(model, hit, chunk, f)
        assert list(body["values"]) == ["AU", "CU", "N", "DOM"] and body["sub"] is None
        assert body["passed"] == bool(cols["AU"][row] > 1) and body["f"] == (1.0 if cols["AU"][row] > 1 else 0.0)
        code = int(cols["DOM"][row])
        assert body["values"]["DOM"] == (None if code < 0 else model.labels("DOM")[code])
        for name in ("N", "CU"):
            x = float(cols[name][row])
            assert body["values"][name] == (None if np.isnan(x) else pytest.approx(x))
        if np.isnan(cols["AU"][row]):
            assert body["values"]["AU"] is None and not body["passed"]  # a gap is not a zero


def test_the_slice_of_a_stored_model_by_portions_of_iter(bproj) -> None:  # type: ignore[no-untyped-def]
    pr = project(bproj)
    store = BlobStore(pr.folder)
    mid, p, s, w, au = make_sub(pr, store)
    model = open_model(pr, store, mid)
    pl = sl.plane_of(model.grid, model.centres()[3] + 0.01, [0, 0, 1])
    sv = sl.Slicer(model.grid, pl, sub=model.sub, kind="number")
    for ch in model.iter(["AU"], chunk=64):
        sv.add(ch.p, ch.s, ch.w, None, ch.cols["AU"])
    got = sv.finish()
    whole_raster = sl.slice_rows(
        model.grid, pl, p.astype(np.uint64), s.astype(np.uint8), w.astype(np.uint8), None, au, "number"
    )
    assert same(got.value, whole_raster.value) and same(got.m, whole_raster.m) and got.m.any()
    raster_matches(model.grid, p.astype(np.uint64), s.astype(np.uint8), w.astype(np.uint8), got, None, au)
