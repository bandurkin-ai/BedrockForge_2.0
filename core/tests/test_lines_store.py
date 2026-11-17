"""Layer of lines/points (formats/lineset.py) and its storage in the project (lines/store.py), ADR 0011 §1."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
import sqlalchemy as sa

from bf_core.db import dialect, migrate
from bf_core.db.schema import blob_owners, line_layers, objects, valid_blob_role
from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet, LineSetBuilder, close, detect_closed, from_lines, from_points
from bf_core.lines import store as ls_store
from bf_core.storage.blobs import BlobStore, add_owner
from bf_core.storage.gc import collect
from tests.dbutil import make_engine

UTM = np.array([512_345.678_901_234, 6_234_567.123_456_789, 412.345_678_9])


def _square(z: float = 100.0, n: float = 10.0) -> np.ndarray:
    return UTM + np.array([[0, 0, z], [n, 0, z], [n, n, z], [0, n, z]], dtype=np.float64)


# --- model ---------------------------------------------------------------------------------------------


def test_closed_line_repeats_first_vertex_once() -> None:
    sq = _square()
    layer = from_lines("Контур", [sq, np.vstack([sq, sq[:1]]), sq[:2]], closed=[True, True, True])
    assert layer.line_count == 3 and layer.point_count == 5 + 5 + 2
    for i in (0, 1):
        line = layer.line(i)
        assert len(line) == 5 and line[-1].tobytes() == line[0].tobytes()  # bit for bit, appended once
    assert layer.closed is not None and layer.closed.tolist() == [1, 1, 0]  # two vertices cannot be closed
    assert layer.closed_count == 2
    assert layer.lengths()[0] == pytest.approx(40.0, abs=1e-9)
    # a closed flag without the repeat is refused by the model itself
    with pytest.raises(FormatError) as err:
        LineSet("x", sq, [0, 4], [1])
    assert err.value.code == "bad_geometry" and "повторять первую вершину" in err.value.message


def test_detect_closed_is_exact_by_default() -> None:
    sq = _square()
    xyz = np.vstack([sq, sq[:1], sq[:3], sq[:1] + [1e-7, 0, 0]])
    offsets = np.array([0, 5, 9])
    assert detect_closed(xyz, offsets).tolist() == [1, 0]
    assert detect_closed(xyz, offsets, tol=1e-6).tolist() == [1, 1]
    layer = from_lines("auto", [xyz[:5], xyz[5:]])  # closed=None: detected, nothing appended
    assert layer.closed is not None and layer.closed.tolist() == [1, 0] and layer.point_count == 9


def test_close_carries_vertex_attributes() -> None:
    sq = _square()
    layer = from_lines("M", [sq, sq[:2]], closed=[True, False], vattrs={"M": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]})
    assert layer.vattrs is not None
    assert layer.vattrs.column("M").to_pylist() == [1.0, 2.0, 3.0, 4.0, 1.0, 5.0, 6.0]
    xyz, offsets, flags, take = close(sq, np.array([0, 4]), [True])
    assert take is not None and take.tolist() == [0, 1, 2, 3, 0] and offsets.tolist() == [0, 5]
    assert flags.tolist() == [1] and xyz[-1].tobytes() == sq[0].tobytes()


def test_empty_values_stay_nan_and_null_not_zero() -> None:
    xyz = np.array([[1.0, 2.0, math.nan], [3.0, 4.0, math.nan], [5.0, 6.0, 7.0], [8.0, 9.0, 10.0]])
    attrs = pa.table(
        {
            "AU": pa.array([None, 1.5], type=pa.float64()),
            "N": pa.array([None, 3], type=pa.int32()),
            "CODE": pa.array([None, "B"]),
            "EMPTY": pa.nulls(2),
        }
    )
    layer = LineSet("Профиль", xyz, [0, 2, 4], [0, 0], None, attrs)
    assert np.isnan(layer.xyz[:2, 2]).all()  # no elevation is not 0
    a = layer.attrs
    assert a is not None and a.column("AU").type == pa.float64() and a.column("N").type == pa.float64()
    assert math.isnan(a.column("AU")[0].as_py()) and math.isnan(a.column("N")[0].as_py())
    assert a.column("N")[1].as_py() == 3.0
    assert a.column("CODE").to_pylist() == [None, "B"] and a.column("EMPTY").to_pylist() == [None, None]
    st = layer.stats()
    assert st["length"] == pytest.approx(math.hypot(2, 2) + math.sqrt(9 + 9 + 9), abs=1e-12)  # NaN Z: in plan
    assert st["flat"] is False
    assert layer.extent() == [1.0, 2.0, 7.0, 8.0, 9.0, 10.0]
    nz = LineSet("Без высот", xyz[:2], [0, 2], [0])
    assert nz.extent() == [1.0, 2.0, None, 3.0, 4.0, None] and nz.flat() and nz.origin() == [2.0, 3.0, 0.0]
    schema = {f["name"]: f["kind"] for f in layer.attr_schema()}
    assert schema == {"AU": "number", "N": "number", "CODE": "text", "EMPTY": "text"}


def test_points_have_no_offsets_or_closed() -> None:
    pts = from_points("Точки", UTM + np.arange(15.0).reshape(5, 3), attrs={"NAME": ["a", None, "c", "d", "e"]})
    assert pts.points_only and pts.offsets is None and pts.closed is None
    assert (pts.line_count, pts.point_count, pts.feature_count, pts.closed_count) == (0, 5, 5, 0)
    assert pts.seq is not None and pts.seq.dtype == np.dtype("<u4") and pts.seq.tolist() == [0, 1, 2, 3, 4]
    assert pts.stats()["length"] == 0.0
    with pytest.raises(FormatError):
        LineSet("x", pts.xyz, [0, 5], None)  # offsets without closed flags


@pytest.mark.parametrize(
    ("xyz", "offsets", "closed", "what"),
    [
        ([[math.nan, 1.0, 2.0]], None, None, "X или Y"),
        ([[0.0, 1.0, math.inf]], None, None, "бесконечные"),
        ([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], [0, 0, 2], [0, 0], "пустых линий"),
        ([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], [0, 3], [0], "Начала линий"),
        ([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], [0, 2], [0, 0], "Число линий"),
    ],
)
def test_bad_geometry_is_refused(xyz: list, offsets: list | None, closed: list | None, what: str) -> None:  # type: ignore[type-arg]
    with pytest.raises(FormatError) as err:
        LineSet("x", np.array(xyz), offsets, closed)
    assert err.value.code == "bad_geometry" and what in err.value.message and err.value.hint


def test_attrs_rows_must_match_features() -> None:
    with pytest.raises(FormatError, match="Число строк атрибутов"):
        from_points("x", np.zeros((3, 3)), attrs={"A": [1.0, 2.0]})
    with pytest.raises(FormatError, match="повторяется"):
        from_points("x", np.zeros((1, 3)), attrs=pa.table({"a": [1.0], "A ": [2.0]}))


def test_builder_streams_lines_with_attributes() -> None:
    b = LineSetBuilder("Стринги")
    sq = _square()
    b.add(sq, closed=True, seq=7, attrs={"JOIN": np.int64(1), "NOTE": "кровля"}, vattrs={"D": ["a", "b", "c", "d"]})
    b.add(sq[:2], seq=3, attrs={"JOIN": 2.0})
    b.add(np.zeros((0, 3)))  # an empty line is not stored
    b.add(sq[:1], seq=9)  # a point in a line layer: one vertex
    b.add(sq[:3], closed=True)  # a triangle: the repeat makes four vertices
    b.add(sq[:2], closed=True)  # two vertices flagged closed: stays open
    layer = b.build()
    assert layer.line_count == 5 and layer.point_count == 5 + 2 + 1 + 4 + 2
    assert layer.closed is not None and layer.closed.tolist() == [1, 0, 0, 1, 0]
    assert layer.seq is not None and layer.seq.tolist() == [7, 3, 9, 3, 4]
    a, v = layer.attrs, layer.vattrs
    assert a is not None and v is not None
    j = a.column("JOIN").to_pylist()
    assert j[:2] == [1.0, 2.0] and all(math.isnan(x) for x in j[2:])
    assert a.column("NOTE").to_pylist() == ["кровля", None, None, None, None]
    assert v.column("D").to_pylist() == ["a", "b", "c", "d", "a", *[None] * 9]
    pb = LineSetBuilder("P", points=True)
    pb.add(UTM, attrs={"Z0": 1.0})
    with pytest.raises(ValueError):
        pb.add(sq)
    assert pb.build().points_only


# --- storage -------------------------------------------------------------------------------------------


@pytest.fixture()
def project(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    yield engine, BlobStore(tmp_path)
    engine.dispose()


def _file(conn: sa.Connection, store: BlobStore, tmp_path: Path, name: str = "pit.str") -> int:
    src = tmp_path / name
    src.write_bytes(b"source file")
    meta = {"format": "micromine_str", "encoding": "cp1251", "fields": [{"name": "EAST", "type": "R"}]}
    return ls_store.create_file(conn, store, name=name, meta=meta, source=src, crs="EPSG:32642")


def test_layer_roundtrip_bit_exact(project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    engine, store = project
    rng = np.random.default_rng(7)
    lines = [UTM + rng.normal(0, 500, (k, 3)) for k in (2, 17, 1, 300)]
    lines[1][3, 2] = math.nan
    lines[3][0] = [-0.0, 7_000_000.000_000_001, -0.0]
    layer = from_lines(
        "Горизонт +400",
        [*lines, _square()],
        closed=[False, False, False, False, True],
        attrs={"STRING": ["1", "1", "2", "2", "3"], "AU": [1.0, None, 2.5, math.nan, 3.0]},
        vattrs={"M": rng.normal(size=sum(len(x) for x in lines) + 4)},
        seq=[4, 0, 1, 3, 2],
        color="#FF8800",
        units={"AU": "г/т"},
    )
    with dialect.write_tx(engine) as conn:
        file_id = _file(conn, store, tmp_path)
        layer_id = ls_store.put_layer(conn, store, layer, file_id=file_id)
    assert not list(store.tmp_dir.iterdir())
    with dialect.read_tx(engine) as conn:
        back = ls_store.read_layer(conn, store, layer_id)
        info = ls_store.layer_info(conn, layer_id)
        roles = ls_store.layer_blobs(conn, layer_id)
        file_row = conn.execute(sa.select(objects).where(objects.c.id == file_id)).mappings().one()
    assert back.xyz.tobytes() == layer.xyz.tobytes()  # float64 bit for bit, NaN and -0.0 included
    assert back.offsets is not None and layer.offsets is not None and back.offsets.tolist() == layer.offsets.tolist()
    assert back.closed is not None and back.closed.tolist() == [0, 0, 0, 0, 1]
    assert back.seq is not None and back.seq.tolist() == [4, 0, 1, 3, 2]
    assert back.attrs is not None and back.attrs.schema == layer.attrs.schema
    assert back.attrs.column("STRING").to_pylist() == ["1", "1", "2", "2", "3"]
    assert back.attrs.column("AU").to_numpy().tobytes() == np.array([1.0, math.nan, 2.5, math.nan, 3.0]).tobytes()
    assert (
        back.vattrs is not None
        and back.vattrs.column("M").to_numpy().tobytes() == layer.vattrs.column("M").to_numpy().tobytes()
    )
    assert back.color == "#ff8800" and back.units == {"AU": "г/т"} and back.name == "Горизонт +400"
    assert set(roles) == {"xyz", "offsets", "closed", "seq", "attrs", "vattrs"} and all(map(valid_blob_role, roles))
    st = layer.stats()
    assert info["line_count"] == 5 and info["point_count"] == st["point_count"] and info["closed_count"] == 1
    assert info["length"] == st["length"] and info["points_only"] is False and info["file_id"] == file_id
    assert info["origin"] == layer.origin() and all(float(c).is_integer() for c in info["origin"])
    assert {"name": "AU", "kind": "number", "unit": "г/т", "scope": "feature"} in info["attr_schema"]
    assert file_row["extent"] == layer.extent() and file_row["meta"]["encoding"] == "cp1251"


def test_points_layer_stores_no_offsets(project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    engine, store = project
    pts = from_points("Устья", UTM + np.arange(30.0).reshape(10, 3), attrs={"NAME": [f"C{i}" for i in range(10)]})
    with dialect.write_tx(engine) as conn:
        file_id = _file(conn, store, tmp_path, "pts.dat")
        layer_id = ls_store.put_layer(conn, store, pts, file_id=file_id)
    with dialect.read_tx(engine) as conn:
        roles = ls_store.layer_blobs(conn, layer_id)
        back = ls_store.read_layer(conn, store, layer_id)
        row = conn.execute(sa.select(line_layers).where(line_layers.c.object_id == layer_id)).mappings().one()
    assert set(roles) == {"xyz", "seq", "attrs"}
    assert back.points_only and back.xyz.tobytes() == pts.xyz.tobytes() and back.attrs is not None
    assert row["points_only"] is True and row["line_count"] == 0 and row["point_count"] == 10


def test_delete_file_takes_layers_and_their_blobs(project, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    engine, store = project
    with dialect.write_tx(engine) as conn:
        file_id = _file(conn, store, tmp_path)
        a = ls_store.put_layer(conn, store, from_lines("1", [_square()]), file_id=file_id)
        b = ls_store.put_layer(conn, store, from_points("2", UTM), file_id=file_id)
        other = _file(conn, store, tmp_path, "other.str")
        kept = ls_store.put_layer(conn, store, from_lines("3", [_square(z=5.0)]), file_id=other)
    with dialect.read_tx(engine) as conn:
        assert ls_store.layer_ids(conn, file_id) == [a, b]
    with dialect.write_tx(engine) as conn:
        assert sorted(ls_store.delete_file(conn, file_id)) == sorted([file_id, a, b])
    with dialect.read_tx(engine) as conn:
        left = set(conn.execute(sa.select(objects.c.id)).scalars())
        assert left == {other, kept}
        assert set(conn.execute(sa.select(line_layers.c.object_id)).scalars()) == {kept}
        owners = set(conn.execute(sa.select(blob_owners.c.owner_id)).scalars())
        assert owners == {other, kept}
        with pytest.raises(Exception) as err:
            ls_store.read_layer(conn, store, a)
        assert getattr(err.value, "status", None) == 404
    report = collect(engine, store)
    assert report.blobs > 0  # the square of the deleted layer differs from the kept one (z)
    with dialect.read_tx(engine) as conn:
        assert ls_store.read_layer(conn, store, kept).point_count == 4


def test_staged_layer_discard_leaves_no_temp_files(project) -> None:  # type: ignore[no-untyped-def]
    engine, store = project
    staged = ls_store.stage_layer(store, from_lines("1", [_square()], attrs={"A": [1.0]}))
    assert set(staged.files) == {"xyz", "offsets", "closed", "seq", "attrs"}
    staged.discard()
    assert not list(store.tmp_dir.iterdir())


def test_blob_roles_are_registered(project) -> None:  # type: ignore[no-untyped-def]
    engine, _store = project
    for role in ("source", "sidecar:jgw", "display0", "display1", "xyz", "tri", "vattrs"):
        assert valid_blob_role(role), role
    for role in ("sidecar:", "sidecar:JGW", "sidecar:../x", "lod1", "XYZ"):
        assert not valid_blob_role(role), role
    with pytest.raises(ValueError, match="BLOB_ROLES"), dialect.write_tx(engine) as conn:
        add_owner(conn, "a" * 64, "object", 1, "lod1")
