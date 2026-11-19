"""A point cloud in the project (ADR 0014 §1; `lines/cloud.py`, `lines/cloud_build.py`, `geo/cloud_kernels.py`):
the storage order against Morton codes written here bit by bit; the levels of a part against a thinning
written here the slow way (one point in every cell, the first in the order of the part) — nested, as prefixes;
`seq` gives the order of the source back bit for bit; parts are squares of the grid; slices are read from the
disk; new classes are new blobs with a new version, and a stale calculation is refused (`cloud_changed`)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect, migrate
from bf_core.db.schema import blob_owners, line_layers, objects, valid_blob_role
from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import from_points
from bf_core.geo import cloud_kernels as ck
from bf_core.lines import cloud as lc
from bf_core.lines import cloud_build as cb
from bf_core.lines import store as ls_store
from bf_core.storage.blobs import BlobStore
from bf_core.storage.gc import collect
from tests.dbutil import make_engine

pytestmark = pytest.mark.windows

UTM = np.array([512_345.678_901, 6_234_567.123_456, 412.345_678])
F32 = 2.5e-4  # float32 rounding of an offset below 8192 m, per axis


# --- references written the slow way -----------------------------------------------------------------------


def ref_codes(xyz: np.ndarray) -> np.ndarray:
    """The Morton codes of the 1 m cells of points: the bits of the two cell numbers, one after another."""
    cx = np.floor(xyz[:, 0]).astype(np.int64) + (1 << 30)
    cy = np.floor(xyz[:, 1]).astype(np.int64) + (1 << 30)
    code = np.zeros(len(xyz), dtype=np.int64)
    for bit in range(31):
        code |= ((cx >> bit) & 1) << (2 * bit)
        code |= ((cy >> bit) & 1) << (2 * bit + 1)
    return code


def ref_thin(xyz: np.ndarray, cell: float) -> list[int]:
    """One point in every cell of `cell` metres of the project grid (X, Y, Z): the first in the order given."""
    seen: set[tuple[int, ...]] = set()
    out = []
    for i, p in enumerate(xyz.tolist()):
        key = tuple(math.floor(v / cell) for v in p)
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def scene(seed: int, n: int, size: float = 200.0) -> lc.CloudChunk:
    """The scene of Т-З1 in small: a plane with noise, «buildings» 5 m high, «trees», single outliers; the
    points in the order of flight lines; classes and returns as a file would give them."""
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0.0, size, (n, 2))
    xy = xy[np.lexsort((xy[:, 0], np.floor(xy[:, 1] / 40.0)))]  # strips along X, 40 m wide
    z = rng.normal(0.0, 0.02, n)
    for x0, y0, w, h in ((20, 30, 10, 10), (60, 120, 20, 30), (150, 40, 15, 25)):
        roof = (xy[:, 0] >= x0) & (xy[:, 0] < x0 + w) & (xy[:, 1] >= y0) & (xy[:, 1] < y0 + h)
        z[roof] += 5.0
    trees = rng.uniform(0.0, size, (40, 2))
    near = np.min(np.linalg.norm(xy[:, None, :] - trees[None, :, :], axis=2), axis=1) < 2.5
    z[near] += rng.uniform(0.5, 9.0, int(near.sum()))
    stray = rng.choice(n, 50, replace=False)
    z[stray] += rng.choice([-40.0, 60.0], 50)
    xyz = UTM + np.column_stack([xy, z])
    cls = rng.choice(np.array([0, 1, 2, 5, 7, 64, 255], dtype=np.uint8), n)
    ret = lc.pack_returns(rng.integers(1, 5, n), rng.integers(1, 6, n))
    return lc.CloudChunk(xyz, cls, ret)


# --- kernels -----------------------------------------------------------------------------------------------


def test_codes_are_morton_codes_of_1m_cells_and_points_without_a_place_have_none() -> None:
    rng = np.random.default_rng(1)
    xyz = np.vstack(
        [
            UTM + rng.uniform(-3000, 3000, (500, 3)),
            rng.uniform(-70, 70, (300, 3)),  # around zero: the cells of negative coordinates
            [[0.0, 0.0, 0.0], [-0.0, -1e-12, 0.0], [1.0, 1.0 - 1e-12, 0.0], [-1.0, -1.0, 0.0]],
            [[1e9, -1e9, 1e9], [-1e9, 1e9, -1e9]],  # the last place there is
        ]
    )
    bad = np.array(
        [
            [math.nan, 1.0, 1.0],
            [1.0, math.inf, 1.0],
            [1.0, 1.0, -math.inf],
            [1.0000001e9, 0.0, 0.0],
            [0.0, -1e30, 0.0],
            [0.0, 0.0, 1e300],
            [0.0, 0.0, math.nan],
        ]
    )
    both = np.vstack([xyz[:400], bad, xyz[400:]])
    codes = np.empty(len(both), dtype=np.int64)
    box = np.array([np.inf] * 3 + [-np.inf] * 3)
    assert ck.morton_codes(both, codes, box) == len(xyz)
    assert codes[400 : 400 + len(bad)].tolist() == [-1] * len(bad)
    got = np.delete(codes, np.arange(400, 400 + len(bad)))
    assert np.array_equal(got, ref_codes(xyz)) and got.min() >= 0 and got.max() < 1 << ck.CODE_BITS
    assert box.tolist() == [*xyz.min(axis=0).tolist(), *xyz.max(axis=0).tolist()]  # only the points with a place
    # a square of the grid is a run of codes: the 64 m tile of a point is its code without the low 12 bits
    tile = np.floor(xyz[:, :2] / 64.0)
    for a, b in ((0, 1), (10, 500), (801, 802)):
        assert (got[a] >> 12 == got[b] >> 12) == bool(np.array_equal(tile[a], tile[b]))


@pytest.mark.parametrize("seed", [3, 4])
def test_ranks_are_the_first_points_of_nested_cells(seed: int) -> None:
    rng = np.random.default_rng(seed)
    pts = np.vstack(
        [
            UTM + rng.uniform(0, 90, (3000, 3)) * [1, 1, 0.05],  # a plane: dense cells
            UTM + rng.uniform(0, 3, (800, 3)) + [40, 40, 0],  # a clump finer than the finest cell
            rng.uniform(-40, 40, (600, 3)),  # around zero: floor of negative numbers
            np.repeat(UTM[None, :] + [7.25, 7.25, 1.0], 50, axis=0),  # the same point 50 times
            [[32.0, 64.0, -32.0], [32.0 - 1e-9, 64.0, -32.0], [-0.0, 0.0, 0.5], [0.5, 2.0, 8.0], [-0.5, -2.0, -8.0]],
        ]
    )
    pts = pts[rng.permutation(len(pts))]
    rank = np.empty(len(pts), dtype=np.uint8)
    ck.level_ranks(pts, 0.5, 4, rank)
    firsts = [set(range(len(pts)))] + [set(ref_thin(pts, cell)) for cell in (0.5, 2.0, 8.0, 32.0)]
    for j in range(1, 5):
        assert set(np.flatnonzero(rank >= j).tolist()) == firsts[j]
        assert firsts[j] <= firsts[j - 1]  # the levels nest
    assert len(firsts[4]) < len(firsts[3]) < len(firsts[2]) < len(firsts[1]) < len(pts)
    order = np.empty(len(pts), dtype=np.int64)
    sizes = np.empty(5, dtype=np.int64)
    ck.rank_order(rank, 4, order, sizes)
    assert sizes.tolist() == [len(f) for f in firsts]
    assert sorted(order.tolist()) == list(range(len(pts)))
    for j in range(5):  # level j is a prefix; among equals the order given is kept
        assert set(order[: sizes[j]].tolist()) == firsts[j]
        band = order[(sizes[j + 1] if j < 4 else 0) : sizes[j]]
        assert np.all(np.diff(band) > 0) and np.all(rank[band] == j)


def test_ranks_of_nothing_and_of_one_point() -> None:
    rank = np.empty(0, dtype=np.uint8)
    ck.level_ranks(np.empty((0, 3)), 0.5, 4, rank)
    one = np.empty(1, dtype=np.uint8)
    ck.level_ranks(np.array([[1.0, 2.0, 3.0]]), 0.5, 4, one)
    assert one.tolist() == [4]


# --- parts -------------------------------------------------------------------------------------------------


def _plan_box(part: lc.CloudPart) -> tuple[float, float]:
    return part.bbox[3] - part.bbox[0], part.bbox[4] - part.bbox[1]


def test_parts_are_squares_of_the_grid_joined_while_they_fit() -> None:
    """A common density of 0.76 points/m² and parts of 8192 points: a 64 m square holds 3100 points, so a part
    is two neighbouring 64 m squares of one 128 m square — never a run that jumps across the cloud."""
    rng = np.random.default_rng(5)
    n, side, limit = 200_000, 512.0, 8192
    xyz = np.array([4096.0, 8192.0, 100.0]) + rng.uniform(0, side, (n, 3)) * [1, 1, 0.01]
    data = cb.build_cloud([lc.CloudChunk(xyz)], part_points=limit)
    assert sum(p.n[0] for p in data.parts) == n
    assert all(0 < p.n[0] <= limit for p in data.parts)
    assert len(data.parts) == 32  # 64 squares in pairs: the parts are 3/4 full
    for p in data.parts:
        w, h = _plan_box(p)
        assert w <= 128.0 and h <= 128.0 and min(w, h) <= 64.0
        assert math.floor(p.bbox[0] / 128) == math.floor(p.bbox[3] / 128)  # inside one 128 m square of the grid
        assert math.floor(p.bbox[1] / 128) == math.floor(p.bbox[4] / 128)


def test_cut_holds_the_limit_and_never_joins_across_squares() -> None:
    rng = np.random.default_rng(6)
    for _ in range(30):
        m = int(rng.integers(1, 400))
        cells = np.unique(rng.integers(0, 1 << int(rng.integers(4, 40)), m).astype(np.int64))
        counts = rng.integers(1, 60, len(cells))
        if rng.random() < 0.3:
            counts[rng.integers(0, len(cells))] = 500  # one code with more than a part takes
        cum = np.concatenate([[0], np.cumsum(counts)])
        limit = int(rng.integers(40, 300))
        runs = cb.cut(cells, cum, limit)
        assert [a for a, _b in runs] == [0, *[b for _a, b in runs[:-1]]] and runs[-1][1] == len(cells)  # a cover
        for a, b in runs:
            total = int(cum[b] - cum[a])
            assert total <= limit or b - a == 1
            # the run lies in a square of at most 16 384 m, and is the only thing its points share a square with
            assert b - a == 1 or (int(cells[a]) ^ int(cells[b - 1])).bit_length() <= cb.MAX_NODE_BITS
        rows = cb.part_rows(cells, cum, limit)
        assert rows[0][0] == 0 and rows[-1][1] == cum[-1] and all(0 < b - a <= limit for a, b in rows)
        assert [a for a, _b in rows[1:]] == [b for _a, b in rows[:-1]]


# --- a cloud built in memory -------------------------------------------------------------------------------


def _check(data: cb.CloudData, source: np.ndarray, cls: np.ndarray, ret: np.ndarray, limit: int) -> None:
    """Everything the storage promises, against the points of the source that have a place."""
    codes = np.empty(len(source), dtype=np.int64)
    valid = ck.morton_codes(source, codes, np.array([np.inf] * 3 + [-np.inf] * 3))
    keep = np.flatnonzero(codes >= 0)
    n = data.count
    assert n == valid == len(keep) and data.skipped == len(source) - n
    # `seq` gives the order of the source back, bit for bit, with the class and the return of every point
    back = np.argsort(data.seq, kind="stable")
    assert np.array_equal(data.seq[back], keep)
    assert data.xyz[back].tobytes() == source[keep].tobytes()
    assert np.array_equal(data.cls[back], cls[keep]) and np.array_equal(data.ret[back], ret[keep])
    assert data.bbox == (*source[keep].min(axis=0).tolist(), *source[keep].max(axis=0).tolist())
    assert data.classes == {int(c): int(k) for c, k in zip(*np.unique(cls[keep], return_counts=True), strict=True)}
    stored = ref_codes(data.xyz)
    at = 0
    last = -1
    for p in data.parts:
        assert p.start == at and 0 < p.n[0] <= limit and len(p.n) == len(lc.LEVELS)
        pts = data.xyz[at : at + p.n[0]]
        own = stored[at : at + p.n[0]]
        assert own.min() >= last  # the parts go in the order of the codes
        last = int(own.max())
        assert p.bbox == (*pts.min(axis=0).tolist(), *pts.max(axis=0).tolist())
        assert all(float(v).is_integer() for v in p.origin)
        if p.bbox[5] - p.bbox[2] <= cb.MAX_SPAN_M:
            assert np.abs((pts - p.origin).astype(np.float32).astype(np.float64) + p.origin - pts).max() <= F32
        # a level is a prefix: one point in every cell, the first in the order of the part; the levels nest
        assert list(p.n) == sorted(p.n, reverse=True)
        for j in range(1, len(lc.LEVELS)):
            assert ref_thin(pts, lc.LEVELS[j]) == list(range(p.n[j]))
        # among the points of one rank: the storage order — the codes, then the order of the source
        for a, b in zip([0, *p.n[:0:-1]], p.n[::-1], strict=True):
            band = np.lexsort((data.seq[at + a : at + b], own[a:b]))
            assert np.array_equal(band, np.arange(b - a))
        at += p.n[0]
    assert at == n


@pytest.mark.parametrize("limit", [lc.PART_POINTS, 4096])
def test_cloud_of_a_scene_keeps_every_promise(limit: int) -> None:
    chunk = scene(11, 100_000)
    pieces = [lc.CloudChunk(chunk.xyz[a : a + 30_000], chunk.cls[a : a + 30_000], chunk.ret[a : a + 30_000])
              for a in range(0, 100_000, 30_000)]  # fmt: skip
    data = cb.build_cloud(pieces, part_points=limit)
    assert chunk.cls is not None and chunk.ret is not None
    _check(data, chunk.xyz, chunk.cls, chunk.ret, limit)
    assert len(data.parts) == 1 if limit == lc.PART_POINTS else 25 <= len(data.parts) <= 64
    sizes = np.array([p.n for p in data.parts]).sum(axis=0)
    assert sizes[0] == 100_000 and sizes[4] < sizes[3] < sizes[2] < sizes[1] < sizes[0]  # every level is lighter


def test_hard_clouds_keep_every_promise() -> None:
    rng = np.random.default_rng(12)
    spot = np.repeat(UTM[None, :], 3000, axis=0) + rng.uniform(0, 0.4, (3000, 3))  # 3000 points in one cell
    same = np.repeat(UTM[None, :] + [5.0, 5.0, 0.0], 700, axis=0)  # one point 700 times
    far = UTM + rng.uniform(-1, 1, (40, 3)) * [3e6, 3e6, 5e4]  # strays thousands of kilometres away
    zero = rng.uniform(-200, 200, (2000, 3))  # across the axes of the grid
    junk = np.array([[math.nan, 0, 0], [0, math.inf, 0], [1e12, 1, 1], [1, 1, -1e300]])
    source = np.vstack([spot, junk[:2], same, far, zero, junk[2:]])
    source = source[rng.permutation(len(source))]
    cls = rng.integers(0, 256, len(source)).astype(np.uint8)
    ret = rng.integers(0, 256, len(source)).astype(np.uint8)
    data = cb.build_cloud([lc.CloudChunk(source, cls, ret)], part_points=512)
    assert data.skipped == 4
    _check(data, source, cls, ret, 512)
    # the spot is cut in the order of the source; a stray far away never shares a part with the cloud
    assert sum(1 for p in data.parts if p.n[0] == 512) >= 5
    for p in data.parts:
        assert max(_plan_box(p)) <= cb.MAX_SPAN_M
    # one point; record numbers given by the source
    one = cb.build_cloud([lc.CloudChunk(np.array([[1.5, -2.5, 3.0]]), seq=np.array([41], dtype=np.uint32))])
    assert one.count == 1 and one.seq.tolist() == [41] and one.parts[0].n == (1, 1, 1, 1, 1)
    assert one.classes == {0: 1} and one.ret.tolist() == [0] and one.parts[0].origin == (2.0, -2.0, 3.0)


def test_a_stray_height_does_not_take_the_precision_of_a_part() -> None:
    rng = np.random.default_rng(13)
    xyz = UTM + rng.uniform(0, 50, (5000, 3)) * [1, 1, 0.1]
    xyz[17, 2] = 9.0e8  # a return from nowhere
    data = cb.build_cloud([lc.CloudChunk(xyz)])
    (part,) = data.parts
    assert abs(part.origin[2] - UTM[2]) <= 6.0
    shown = (data.xyz - part.origin).astype(np.float32).astype(np.float64) + part.origin
    real = data.xyz[:, 2] < 1e6
    assert np.abs(shown - data.xyz)[real].max() <= F32


def test_a_source_without_points_is_refused() -> None:
    for chunks in ([], [lc.CloudChunk(np.empty((0, 3)))], [lc.CloudChunk(np.full((5, 3), math.nan))]):
        with pytest.raises(FormatError) as err:
            cb.build_cloud(chunks)
        assert err.value.code == "no_points" and err.value.hint
    with pytest.raises(ValueError, match="triples"):
        cb.build_cloud([lc.CloudChunk(np.zeros((4, 2)))])
    with pytest.raises(ValueError, match="class"):
        cb.build_cloud([lc.CloudChunk(np.zeros((4, 3)), cls=np.zeros(3, dtype=np.uint8))])


def test_returns_are_packed_in_one_byte() -> None:
    ret = lc.pack_returns([1, 2, 15, 40, 0], [1, 5, 15, 99, 0])
    assert ret.dtype == np.uint8 and ret.tolist() == [0x11, 0x52, 0xFF, 0xFF, 0]
    number, count = lc.unpack_returns(ret)
    assert number.tolist() == [1, 2, 15, 15, 0] and count.tolist() == [1, 5, 15, 15, 0]


# --- storage -----------------------------------------------------------------------------------------------


@pytest.fixture()
def project(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    yield engine, BlobStore(tmp_path)
    engine.dispose()


@pytest.fixture()
def stored(project):  # type: ignore[no-untyped-def]
    """A cloud of the scene in a project: (engine, store, the cloud in memory, file id, layer id)."""
    engine, store = project
    chunk = scene(21, 60_000)
    data = cb.build_cloud([chunk])
    with dialect.write_tx(engine) as conn:
        file_id = ls_store.create_file(conn, store, name="съёмка.las", meta={"format": "las"}, crs="EPSG:32642")
        staged = cb.stage_data(store, data)
        layer_id = lc.commit_cloud(
            conn, store, staged, file_id=file_id, name="  Съёмка   борта ", color="#FF8800",
            source={"kind": "las", "every": 1}, meta={"source": {"las": {"version": "1.2"}}, "cloud": "нет"},
        )  # fmt: skip
    assert not staged.files and not list(store.tmp_dir.iterdir())
    return engine, store, data, file_id, layer_id


def test_roles_of_a_cloud_are_registered() -> None:
    assert all(valid_blob_role(role) for role in lc.ROLES)
    assert set(lc.BASE_ROLES) | set(lc.VALUE_ROLES) == set(lc.ROLES)


def test_cloud_is_a_layer_of_points_read_by_slices(stored) -> None:  # type: ignore[no-untyped-def]
    engine, store, data, file_id, layer_id = stored
    with dialect.read_tx(engine) as conn:
        cloud = lc.open_cloud(conn, store, layer_id)
        info = ls_store.layer_info(conn, layer_id)
        meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == layer_id)).scalar_one()
        file_extent = conn.execute(sa.select(objects.c.extent).where(objects.c.id == file_id)).scalar_one()
    assert (info["name"], info["file_id"], info["crs"], info["version"]) == ("Съёмка борта", file_id, "EPSG:32642", 1)
    assert info["points_only"] and (info["line_count"], info["point_count"]) == (0, 60_000)
    assert (info["color"], info["flat"], info["length"]) == ("#ff8800", False, 0.0)
    assert info["extent"] == list(data.bbox) == file_extent
    assert info["origin"] == [float(np.round((data.bbox[c] + data.bbox[c + 3]) / 2)) for c in range(3)]
    assert [(f["name"], f["store"], f.get("part")) for f in info["attr_schema"]] == [
        ("Класс", "class", None),
        ("Возврат", "ret", "number"),
        ("Число возвратов", "ret", "count"),
    ]
    assert all(f["kind"] == "number" and f["scope"] == "feature" for f in info["attr_schema"])
    assert meta["source"] == {"las": {"version": "1.2"}}  # other keys of the layer; `cloud` is the cloud's own
    assert meta["cloud"]["format"] == 1 and meta["cloud"]["levels"] == [0, 0.5, 2, 8, 32]
    assert meta["cloud"]["source"] == {"kind": "las", "every": 1}
    assert meta["cloud"]["ground"] is None and meta["cloud"]["dz"] is None
    assert meta["cloud"]["classes"] == {str(k): v for k, v in data.classes.items()}
    assert set(meta["cloud"]["parts"][0]) == {"start", "n", "bbox", "origin"}
    assert (cloud.layer_id, cloud.file_id, cloud.name, cloud.version, cloud.count) == (
        layer_id, file_id, "Съёмка борта", 1, 60_000,
    )  # fmt: skip
    assert cloud.levels == lc.LEVELS and list(cloud.parts) == data.parts and cloud.bbox == data.bbox
    assert cloud.classes == data.classes and set(cloud.shas) == set(lc.BASE_ROLES)
    assert cloud.has("class") and not cloud.has("h") and not cloud.has("dz")
    # whole columns and slices, bit for bit
    for role, want in (("xyz", data.xyz), ("seq", data.seq), ("class", data.cls), ("ret", data.ret)):
        got = cloud.read(role)
        assert got.dtype == np.dtype(lc.ROLES[role]) and got.tobytes() == want.tobytes()
        assert cloud.read(role, 12_345, 777).tobytes() == want[12_345 : 12_345 + 777].tobytes()
        assert len(cloud.read(role, 60_000, 0)) == 0 and len(cloud.read(role, 59_999)) == 1
        mapped = cloud.memmap(role)
        assert mapped.shape == want.shape and np.array_equal(mapped[40_000:40_100], want[40_000:40_100])
        del mapped
    start, count = cloud.part_rows(0, 4)
    assert (start, count) == (0, data.parts[0].n[4]) and cloud.read("xyz", start, count).shape == (count, 3)
    for bad in ((-1, 5), (59_990, 11), (0, -1)):
        with pytest.raises(ValueError, match="out of"):
            cloud.read("xyz", *bad)
    with pytest.raises(ValueError, match="no column"):
        cloud.read("h")
    with pytest.raises(ValueError, match="unknown"):
        cloud.read("attrs")
    # the attributes of a point are read from the columns
    number, total = lc.unpack_returns(data.ret[100:140])
    fields = info["attr_schema"]
    assert np.array_equal(lc.attr_column(cloud, fields[0], 100, 40), data.cls[100:140].astype(np.float64))
    assert np.array_equal(lc.attr_column(cloud, fields[1], 100, 40), number.astype(np.float64))
    assert np.array_equal(lc.attr_column(cloud, fields[2], 100, 40), total.astype(np.float64))


def test_a_layer_that_is_not_a_cloud_and_a_damaged_cloud_are_told_apart(stored) -> None:  # type: ignore[no-untyped-def]
    engine, store, _data, file_id, layer_id = stored
    with dialect.write_tx(engine) as conn:
        plain = ls_store.put_layer(conn, store, from_points("Пикеты", UTM + np.zeros((3, 3))), file_id=file_id)
        conn.execute(sa.update(objects).where(objects.c.id == plain).values(state="building"))
    with dialect.read_tx(engine) as conn:
        for layer, ready, code, status in (
            (plain, False, "not_a_cloud", 409),
            (plain, True, "line_layer_not_found", 404),
            (file_id, True, "line_layer_not_found", 404),
            (10_000, True, "line_layer_not_found", 404),
        ):
            with pytest.raises(ApiError) as err:
                lc.open_cloud(conn, store, layer, ready=ready)
            assert (err.value.code, err.value.status) == (code, status) and err.value.hint
        cloud = lc.open_cloud(conn, store, layer_id)
    path = cloud.paths["ret"]
    whole = path.read_bytes()
    path.write_bytes(whole[:-1])  # a column a byte short
    with dialect.read_tx(engine) as conn, pytest.raises(ApiError) as err:
        lc.open_cloud(conn, store, layer_id)
    assert (err.value.code, err.value.status) == ("cloud_broken", 500)
    with pytest.raises(ApiError) as err:
        cloud.read("ret")  # a cloud opened before the damage: the slice is not there
    assert err.value.code == "cloud_broken"
    path.write_bytes(whole)
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_id == layer_id, blob_owners.c.role == "seq"))
    with dialect.read_tx(engine) as conn, pytest.raises(ApiError) as err:
        lc.open_cloud(conn, store, layer_id)
    assert err.value.code == "cloud_broken"


def test_new_classes_are_new_blobs_and_a_new_version(stored) -> None:  # type: ignore[no-untyped-def]
    engine, store, data, _file_id, layer_id = stored
    rng = np.random.default_rng(31)
    with dialect.read_tx(engine) as conn:
        cloud = lc.open_cloud(conn, store, layer_id)
    cls = rng.choice(np.array([1, 2, 3, 7]), cloud.count)  # int64, as a calculation gives it
    h = rng.normal(0, 1, cloud.count)
    h[::9] = math.nan
    ticks = []
    staged = lc.stage_values(store, cloud, {"class": cls, "h": h}, tick=lambda: ticks.append(1))
    assert ticks and staged.classes == {
        int(c): int(k) for c, k in zip(*np.unique(cls, return_counts=True), strict=True)
    }
    ground = {"params": {"cr": 1.0, "ri": 2}, "counts": {"2": 5}, "algo": "csf-1", "at": "2026-11-19T10:00:00Z"}
    with dialect.write_tx(engine) as conn:
        # the layer was renamed while the calculation ran: its points are the same — not a conflict
        conn.execute(sa.update(objects).where(objects.c.id == layer_id).values(name="Борт", version=2))
        assert lc.replace_values(conn, store, cloud, staged, ground=ground) == 3
    assert not staged.files and not list(store.tmp_dir.iterdir())
    with dialect.read_tx(engine) as conn:
        after = lc.open_cloud(conn, store, layer_id)
        schema = conn.execute(
            sa.select(line_layers.c.attr_schema).where(line_layers.c.object_id == layer_id)
        ).scalar_one()
        roles = sorted(conn.execute(sa.select(blob_owners.c.role).where(blob_owners.c.owner_id == layer_id)).scalars())
    assert roles == ["class", "h", "ret", "seq", "xyz"]  # one blob a role: the old classes are not the layer's
    assert (after.version, after.name) == (3, "Борт") and after.classes == staged.classes
    assert after.meta["ground"] == ground and after.meta["dz"] is None
    assert list(after.parts) == data.parts and after.bbox == data.bbox  # the levels are not touched
    assert after.shas["xyz"] == cloud.shas["xyz"] and after.shas["seq"] == cloud.shas["seq"]
    assert after.shas["class"] != cloud.shas["class"] and after.shas["ret"] == cloud.shas["ret"]
    assert np.array_equal(after.read("class"), cls.astype(np.uint8)) and after.read("class").dtype == np.uint8
    got = after.read("h", 0, 100)
    assert got.dtype == np.float32 and np.array_equal(got, h[:100].astype(np.float32), equal_nan=True)
    assert [f["name"] for f in schema] == ["Класс", "Возврат", "Число возвратов", "Высота над землёй, м"]
    assert math.isnan(lc.attr_column(after, schema[3], 0, 1)[0]) and lc.attr_column(after, schema[3], 1, 1)[0] == got[1]
    # the old classes are gone with the next collection; the points stay
    old = store.path_of(cloud.shas["class"])
    assert old.is_file()
    report = collect(engine, store)
    assert report.blobs == 1 and not old.exists() and store.path_of(after.shas["xyz"]).is_file()

    # a calculation that read the cloud before the change does not write over it
    stale = lc.stage_values(store, cloud, {"class": np.full(cloud.count, 2, dtype=np.uint8)})
    with pytest.raises(ApiError) as err, dialect.write_tx(engine) as conn:
        lc.replace_values(conn, store, cloud, stale)
    assert (err.value.code, err.value.status) == ("cloud_changed", 409) and err.value.hint
    stale.discard()
    assert not list(store.tmp_dir.iterdir())
    with dialect.read_tx(engine) as conn:
        same = lc.open_cloud(conn, store, layer_id)
    assert same.version == 3 and same.shas == after.shas and same.classes == after.classes  # nothing was written

    # the difference to a DTM comes later, the classes stay; `ground` not given — kept
    dz = lc.stage_values(store, after, {"dz": np.zeros(after.count, dtype=np.float32)})
    assert dz.classes is None
    with dialect.write_tx(engine) as conn:
        assert lc.replace_values(conn, store, after, dz, dz={"wireframe_id": 7, "name": "ЦТМ"}) == 4
    with dialect.read_tx(engine) as conn:
        last = lc.open_cloud(conn, store, layer_id)
    assert last.has("dz") and last.meta["dz"] == {"wireframe_id": 7, "name": "ЦТМ"} and last.meta["ground"] == ground
    assert last.classes == after.classes and last.shas["class"] == after.shas["class"]

    # the layer is gone while a calculation ran
    gone = lc.stage_values(store, last, {"class": np.zeros(last.count, dtype=np.uint8)})
    with dialect.write_tx(engine) as conn:
        ls_store.delete_file(conn, last.file_id)
    with pytest.raises(ApiError) as err, dialect.write_tx(engine) as conn:
        lc.replace_values(conn, store, last, gone)
    assert err.value.code == "cloud_changed"
    gone.discard()


def test_values_of_a_wrong_shape_are_refused_and_leave_no_files(stored) -> None:  # type: ignore[no-untyped-def]
    engine, store, _data, _file_id, layer_id = stored
    with dialect.read_tx(engine) as conn:
        cloud = lc.open_cloud(conn, store, layer_id)
    ok = np.zeros(cloud.count, dtype=np.uint8)
    for values, what in (
        ({"class": ok[:-1]}, "values for"),
        ({"class": ok, "h": np.zeros((cloud.count, 1))}, "values for"),
        ({"class": np.full(cloud.count, 256)}, "0…255"),
        ({"class": np.full(cloud.count, -1)}, "0…255"),
        ({"class": np.full(cloud.count, 2.0)}, "0…255"),
        ({"h": np.zeros(cloud.count), "xyz": np.zeros(cloud.count)}, "not replaced"),
    ):
        with pytest.raises(ValueError, match=what):
            lc.stage_values(store, cloud, values)
        assert not list(store.tmp_dir.iterdir())

    def cancel() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        lc.stage_values(store, cloud, {"h": np.zeros(cloud.count), "class": ok}, tick=cancel)
    assert not list(store.tmp_dir.iterdir())
