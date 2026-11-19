"""The LAS writer: a new file against files packed with `struct`, the cycles write → read → write and
read → write → read bit for bit in formats 0–10 of versions 1.2–1.4, a copy of the source with other classes
(only the class bits differ), refusals (ТЗ 7.35 Д-22, 9.12; ADR 0014 §3, 7.1.5)."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.formats.errors import FormatError
from bf_core.formats.las import open_las
from bf_core.formats.las_write import (
    LasWriter,
    Vlr,
    geokey_vlrs,
    pick_layout,
    rewrite_classes,
    wkt_vlr,
    write_las,
)

from .las_builder import (
    FORMAT_SIZE,
    HEADER_SIZE,
    PROJECTION,
    VERSIONS,
    WKT_UTM,
    Pt,
    Spec,
    coordinate,
    evlr,
    las_bytes,
    random_points,
    ret_byte,
    vlr,
    wkt_data,
)

pytestmark = pytest.mark.windows
GRIDS = [((0.001, 0.001, 0.001), (5e6, 5e6, 0.0)), ((0.01, 0.01, 0.01), (5e6, 5e6, 5e6))]
STAMP: dict[str, Any] = {"system_id": "T", "software": "struct", "created": (2026, 1)}
STAMP_BYTES: dict[str, Any] = {"system_id": b"T", "software": b"struct"}


def _arrays(points: list[Pt], spec: Spec) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xyz = [[coordinate(getattr(p, a), spec.scale[k], spec.offset[k]) for k, a in enumerate("XYZ")] for p in points]
    cls = np.array([p.cls for p in points], dtype=np.uint8)
    return np.array(xyz, dtype=np.float64).reshape(-1, 3), cls, np.array([ret_byte(p) for p in points], dtype=np.uint8)


def _plain(n: int, fmt: int, seed: int, spread: int = 2**31) -> list[Pt]:
    """Points with nothing but the coordinates, the class and the returns — what a cloud keeps."""
    return [
        Pt(p.X % (2 * spread) - spread, p.Y % (2 * spread) - spread, p.Z % (2 * spread) - spread, cls=p.cls, ret=p.ret,
           nret=p.nret)  # fmt: skip
        for p in random_points(n, fmt, seed)
    ]


def _encoding(version: tuple[int, int], fmt: int) -> int:
    return 16 if version == (1, 4) and fmt >= 6 else 0  # formats 6–10 of LAS 1.4 keep their CRS as WKT


def _fails(call: Any, code: str) -> FormatError:
    with pytest.raises(FormatError) as info:
        call()
    error = info.value
    assert error.code == code, error.as_dict()
    assert error.message and error.hint and any("а" <= ch <= "я" for ch in error.message.lower())
    return error


def _files(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


# ---------------------------------------------------------------- a new file


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("fmt", range(11))
@pytest.mark.parametrize("grid", GRIDS)
def test_new_file_is_the_reference_bit_for_bit(
    tmp_path: Path, fmt: int, version: tuple[int, int], grid: tuple[tuple[float, ...], tuple[float, ...]]
) -> None:
    points = _plain(200, fmt, seed=fmt + 100 * version[1])
    encoding = _encoding(version, fmt)
    spec = Spec(version=version, fmt=fmt, scale=grid[0], offset=grid[1], global_encoding=encoding, **STAMP_BYTES)
    xyz, cls, ret = _arrays(points, spec)
    path = tmp_path / "new.las"
    done = write_las(path, xyz, cls, ret, version=version, point_format=fmt, scale=grid[0], offset=grid[1], **STAMP)
    assert path.read_bytes() == las_bytes(points, spec)  # the header, the counters, the extent, every record
    assert (done.path, done.points, done.version, done.point_format) == (path, 200, version, fmt)
    assert done.size == path.stat().st_size == HEADER_SIZE[version] + 200 * FORMAT_SIZE[fmt]
    assert _files(tmp_path) == ["new.las"]
    # … and it reads back to the same doubles: read → write → read is bit for bit
    got = open_las(path).read()
    assert got.xyz.tobytes() == xyz.tobytes()
    assert got.cls.tobytes() == cls.tobytes() and got.ret.tobytes() == ret.tobytes()


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("fmt", range(11))
def test_cycle_keeps_integers_and_bytes(tmp_path: Path, fmt: int, version: tuple[int, int]) -> None:
    """A file of another program (every field is noise, extra bytes, VLRs) → read → write → read → write."""
    points = random_points(300, fmt, seed=fmt + 7 * version[1], extra=3)
    scale, offset = (0.001, 0.01, 0.002), (5e6, -3e5, 100.5)
    theirs = tmp_path / "theirs.las"
    theirs.write_bytes(las_bytes(points, Spec(version=version, fmt=fmt, scale=scale, offset=offset, extra=3)))
    source = open_las(theirs)
    first = source.read()
    options: dict[str, Any] = {"version": version, "point_format": fmt, "scale": scale, "offset": offset, **STAMP}
    ours = tmp_path / "ours.las"
    write_las(ours, first.xyz, first.cls, first.ret, **options)
    cloud = open_las(ours)
    second = cloud.read()
    assert second.xyz.tobytes() == first.xyz.tobytes()  # read → write → read: the same doubles
    assert second.cls.tobytes() == first.cls.tobytes() and second.ret.tobytes() == first.ret.tobytes()
    raw, kept = cloud.records(), source.records()
    for name in "XYZ":
        assert np.array_equal(raw[name], kept[name])  # the integers of the records
    again = tmp_path / "again.las"
    write_las(again, second.xyz, second.cls, second.ret, **options)
    assert again.read_bytes() == ours.read_bytes()  # write → read → write: the same bytes
    # every field as it was: the raw records written back give the records of the source byte for byte
    copy = tmp_path / "copy.las"
    with LasWriter(copy, extra_bytes=3, **options) as writer:
        writer.write_records(kept[:100])
        writer.write_records(kept[100:])
    body = slice(HEADER_SIZE[version], None)
    assert copy.read_bytes()[body] == theirs.read_bytes()[body]
    assert copy.read_bytes()[:HEADER_SIZE[version]] == las_bytes(
        points, Spec(version=version, fmt=fmt, scale=scale, offset=offset, extra=3,
                     global_encoding=_encoding(version, fmt), **STAMP_BYTES)
    )[: HEADER_SIZE[version]]  # fmt: skip


@pytest.mark.parametrize(("scale", "offset"), [(0.001, 5e6), (0.01, 5e6), (0.001, None), (0.0001, None), (0.25, 0.0)])
def test_coordinates_within_a_scale_and_stable_from_then_on(tmp_path: Path, scale: float, offset: float | None) -> None:
    rng = np.random.default_rng(20261119)
    n = 300_000
    xyz = np.column_stack(
        [rng.uniform(5e6 - 1500, 5e6 + 1500, n), rng.uniform(5e6 - 2000, 5e6 + 2000, n), rng.normal(-150, 120, n)]
    )
    xyz[:5] = [[5e6, 5e6, 0.0], [5e6 + scale / 2, 5e6 - scale / 2, scale / 2], [5e6 + 1.5 * scale, 5e6, -1.5 * scale],
               [4999999.9995, 5000000.0005, -0.0005], [5e6 - 1500, 5e6 + 2000, 1e3]]  # fmt: skip
    cls = rng.integers(0, 32, n).astype(np.uint8)
    ret = (rng.integers(1, 8, n) | rng.integers(1, 8, n) << 4).astype(np.uint8)
    path = tmp_path / "a.las"
    grid: dict[str, Any] = {} if offset is None else {"offset": (offset, offset, 0.0)}
    grid["created"] = (2026, 1)
    done = write_las(path, xyz, cls, ret, scale=scale, **grid)
    assert (done.version, done.point_format, done.points) == ((1, 2), 0, n)
    cloud = open_las(path)
    h = cloud.header
    assert h.scale == (scale,) * 3 and not cloud.warnings
    if offset is None:  # a whole origin: the floor of the least coordinates
        assert h.offset == tuple(float(np.floor(v)) for v in xyz.min(axis=0))
    got = cloud.read()
    # the reference: the nearest node of the grid, ties to even; the tolerance — half a scale and the rounding
    # of doubles near 5·10⁶ (the ADR allows a whole scale)
    node = np.rint((xyz - np.array(h.offset)) / scale)
    assert np.array_equal(cloud.records()["X"], node[:, 0].astype(np.int32))
    assert got.xyz.tobytes() == (node * scale + np.array(h.offset)).tobytes()
    assert np.abs(got.xyz - xyz).max() <= 0.5 * scale + 1e-8
    assert got.cls.tobytes() == cls.tobytes() and got.ret.tobytes() == ret.tobytes()
    assert h.bbox is not None and np.array_equal(h.bbox, [*got.xyz.min(axis=0), *got.xyz.max(axis=0)])
    assert h.points_by_return == tuple(int(((ret & 15) == k).sum()) for k in range(1, 6))
    assert cloud.classes == {int(k): int(v) for k, v in zip(*np.unique(cls, return_counts=True), strict=True)}
    again = tmp_path / "b.las"
    write_las(again, got.xyz, got.cls, got.ret, scale=scale, offset=h.offset, chunk=70_001, created=(2026, 1))
    assert again.read_bytes() == path.read_bytes()  # … in other chunks: the same file


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("fmt", range(11))
def test_reader_reads_what_the_writer_writes(tmp_path: Path, fmt: int, version: tuple[int, int]) -> None:
    """The files of the ADR table: both scales with the offset 5·10⁶, classes 0–31 and 0–255, extra bytes, the
    CRS as WKT in an EVLR (LAS 1.4) or as GeoTIFF keys."""
    rng = np.random.default_rng(fmt + 31 * version[1])
    n, wide = 2000, fmt >= 6
    for scale, extra in ((0.001, 0), (0.01, 6)):
        xyz = np.rint(rng.uniform(-1500, 1500, (n, 3)) / scale) * scale + 5e6
        cls = rng.integers(0, 256 if wide else 32, n).astype(np.uint8)
        top = 15 if wide else 7
        ret = (rng.integers(1, top + 1, n) | rng.integers(1, top + 1, n) << 4).astype(np.uint8)
        crs: dict[str, Any] = {"evlrs": [wkt_vlr(WKT_UTM)]} if version == (1, 4) else {"vlrs": geokey_vlrs(32637)}
        path = tmp_path / f"{scale}.las"
        write_las(path, xyz, cls, ret, version=version, point_format=fmt, scale=scale, offset=(5e6,) * 3,
                  extra_bytes=extra, chunk=700, **crs)  # fmt: skip
        cloud = open_las(path)
        h = cloud.header
        assert (h.version, h.point_format) == (version, fmt)
        assert (h.record_length, h.extra_bytes) == (FORMAT_SIZE[fmt] + extra, extra)
        assert (h.point_count, h.scale, h.offset) == (n, (scale,) * 3, (5e6,) * 3)
        assert (h.wkt, h.epsg) == ((WKT_UTM, None) if version == (1, 4) else (None, 32637)) and not h.geographic
        got = cloud.read()
        assert np.abs(got.xyz - xyz).max() <= scale  # the ADR: within a scale; in fact a rounding of doubles
        assert got.cls.tobytes() == cls.tobytes() and got.ret.tobytes() == ret.tobytes()
        raw = cloud.records()
        assert not raw["intensity"].any() and (not extra or raw["extra"].tobytes() == bytes(extra * n))
        again = tmp_path / "again.las"
        write_las(again, got.xyz, got.cls, got.ret, version=version, point_format=fmt, scale=scale, offset=h.offset)
        assert open_las(again).read().xyz.tobytes() == got.xyz.tobytes()  # read → write → read: bit for bit


def test_ends_of_int32(tmp_path: Path) -> None:
    path = tmp_path / "ends.las"
    lo, hi = -(2**31), 2**31 - 1
    xyz = np.array([[lo, hi, 0.0], [hi, lo, -0.0], [lo - 0.4, hi + 0.4, 0.5], [-1.5, 2.5, -0.5]])
    write_las(path, xyz, scale=1.0, offset=(0.0, 0.0, 0.0))
    raw = open_las(path).records()
    assert raw["X"].tolist() == [lo, hi, lo, -2] and raw["Y"].tolist() == [hi, lo, hi, 2]  # ties go to even
    assert raw["Z"].tolist() == [0, 0, 0, 0]
    for bad in ([lo - 0.6, 0.0, 0.0], [0.0, hi + 0.6, 0.0], [0.0, 0.0, 2.0**31]):
        beyond = np.array([[0.0, 0.0, 0.0], bad])
        _fails(lambda beyond=beyond: write_las(path, beyond, scale=1.0, offset=(0.0,) * 3), "unrepresentable")
    assert _files(tmp_path) == ["ends.las"]
    with pytest.raises(ValueError), LasWriter(tmp_path / "raw.las") as writer:
        writer.write_records(raw)  # integers without the offset of their grid
    assert _files(tmp_path) == ["ends.las"]


def test_defaults_of_a_new_file(tmp_path: Path) -> None:
    xyz = np.array([[512345.678, 6123456.789, 101.5], [512300.0, 6123400.25, -7.125], [512399.999, 6123499.5, 0.0]])
    path = tmp_path / "Облако 1.las"
    done = write_las(path, xyz)
    cloud = open_las(path)
    h = cloud.header
    assert (h.version, h.point_format, h.scale, h.offset) == ((1, 2), 0, (0.001,) * 3, (512300.0, 6123400.0, -8.0))
    assert (h.software, h.system_id, h.global_encoding, h.vlrs) == ("BedrockForge 2", "OTHER", 0, ())
    assert 2026 <= h.created[0] < 2100 and 1 <= h.created[1] <= 366 and not cloud.warnings and not done.warnings
    got = cloud.read()
    assert np.abs(got.xyz - xyz).max() <= 0.0005 and got.cls.tolist() == [0, 0, 0] and got.ret.tolist() == [0x11] * 3
    assert h.points_by_return == (3, 0, 0, 0, 0)
    # classes above 31 and returns above 7 need LAS 1.4, format 6
    assert pick_layout(31, 7) == ((1, 2), 0) and pick_layout(32, 1) == ((1, 4), 6) and pick_layout(2, 8) == ((1, 4), 6)
    done = write_las(path, xyz, np.array([2, 64, 255], dtype=np.uint8))
    h = open_las(path).header
    assert (done.version, done.point_format) == (h.version, h.point_format) == ((1, 4), 6) and h.global_encoding == 16
    assert h.points_by_return == (3,) + (0,) * 14 and open_las(path).read().cls.tolist() == [2, 64, 255]
    done = write_las(path, xyz, None, np.array([0x11, 0xF9, 0x22], dtype=np.uint8))
    assert (done.version, done.point_format) == ((1, 4), 6) and open_las(path).read().ret.tolist() == [0x11, 0xF9, 0x22]
    # a version or a format alone: the other follows
    assert [write_las(path, xyz, version=(1, minor)).point_format for minor in (2, 3, 4)] == [0, 0, 6]
    assert [write_las(path, xyz, point_format=f).version[1] for f in (0, 3, 4, 5, 6, 8, 10)] == [2, 2, 3, 3, 4, 4, 4]
    assert _files(tmp_path) == ["Облако 1.las"]


def test_writer_in_parts_with_progress(tmp_path: Path) -> None:
    points = _plain(1000, 7, seed=3, spread=10**6)
    spec = Spec(version=(1, 4), fmt=7, global_encoding=16, **STAMP_BYTES)
    xyz, cls, ret = _arrays(points, spec)
    options: dict[str, Any] = {"version": (1, 4), "point_format": 7, "offset": (0.0, 0.0, 0.0), **STAMP}
    path = tmp_path / "parts.las"
    with LasWriter(path, **options) as writer:
        for a in range(0, 1000, 130):
            writer.write(xyz[a : a + 130], cls[a : a + 130], ret[a : a + 130])
            assert not path.exists() and _files(tmp_path) == ["parts.las.part"]  # under its name only when complete
    assert writer.result is not None and writer.result.points == 1000 and _files(tmp_path) == ["parts.las"]
    assert path.read_bytes() == las_bytes(points, spec)
    seen: list[tuple[float, str | None]] = []
    write_las(tmp_path / "one.las", xyz, cls, ret, progress=lambda f, m: seen.append((f, m)), chunk=400, **options)
    assert (tmp_path / "one.las").read_bytes() == path.read_bytes()
    assert [f for f, _ in seen] == [0.0, 0.4, 0.8, 1.0] and seen[1][1] == "Запись точек: 400 из 1 000"
    assert seen[-1][1] == "Записано точек: 1 000"


def test_vlrs_and_the_crs(tmp_path: Path) -> None:
    points = _plain(50, 6, seed=4, spread=10**6)
    theirs = vlr(b"Somebody", 7, b"\x01\x02\x03", b"three bytes")
    in_vlr = vlr(PROJECTION, 2112, wkt_data(WKT_UTM), b"WKT")
    in_evlr = evlr(b"Somebody", 8, bytes(range(200)) * 400, b"long")  # too long for a VLR
    spec = Spec(version=(1, 4), fmt=6, global_encoding=16, vlrs=[theirs, in_vlr], evlrs=[in_evlr], **STAMP_BYTES)
    xyz, cls, ret = _arrays(points, spec)
    path = tmp_path / "crs.las"
    mine = [Vlr("Somebody", 7, b"\x01\x02\x03", "three bytes"), Vlr("LASF_Projection", 2112, wkt_data(WKT_UTM), "WKT")]
    long = Vlr("Somebody", 8, bytes(range(200)) * 400, "long")
    options: dict[str, Any] = {"version": (1, 4), "point_format": 6, "offset": (0.0, 0.0, 0.0), **STAMP}
    write_las(path, xyz, cls, ret, vlrs=mine, evlrs=[long], **options)
    assert path.read_bytes() == las_bytes(points, spec)
    # the helpers: a WKT record sets bit 4 of the global encoding in any format of 1.4; GeoTIFF keys do not
    assert wkt_vlr(WKT_UTM) == Vlr("LASF_Projection", 2112, wkt_data(WKT_UTM), "OGC COORDINATE SYSTEM WKT")
    write_las(path, xyz, version=(1, 4), point_format=1, vlrs=[wkt_vlr(WKT_UTM)])
    h = open_las(path).header
    assert h.wkt == WKT_UTM and h.global_encoding == 16 and h.crs_text() == WKT_UTM
    write_las(path, xyz, vlrs=geokey_vlrs(32637, name="WGS 84 / UTM zone 37N"))
    h = open_las(path).header
    assert (h.version, h.epsg, h.geographic, h.global_encoding) == ((1, 2), 32637, False, 0)
    assert h.crs_name() == "WGS 84 / UTM zone 37N"
    assert h.geokeys == {"1024": 1, "1026": "WGS 84 / UTM zone 37N", "3072": 32637}
    write_las(path, xyz, vlrs=geokey_vlrs(4326, geographic=True))
    h = open_las(path).header
    assert (h.epsg, h.geographic, h.geokeys) == (4326, True, {"1024": 2, "2048": 4326})
    with pytest.raises(ValueError):
        write_las(path, xyz, evlrs=[long])  # an EVLR needs LAS 1.4
    with pytest.raises(ValueError):
        write_las(path, xyz, vlrs=[Vlr("Somebody", 8, bytes(70000))])  # a VLR holds 65 535 bytes


def test_refusals_leave_nothing_behind(tmp_path: Path) -> None:
    xyz = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    path = tmp_path / "out.las"
    path.write_bytes(b"what was here before")

    def gap() -> None:
        write_las(path, np.array([[1.0, 2.0, 3.0], [4.0, float("nan"), 6.0]]))

    assert "без координат" in _fails(gap, "unrepresentable").message  # a gap is not written as zero
    error = _fails(lambda: write_las(path, xyz * 1e9, scale=0.001, offset=(0.0, 0.0, 0.0)), "unrepresentable")
    assert "0.001" in error.message and "масштаб" in error.hint.lower()
    _fails(lambda: write_las(path, np.array([[0.0, 0.0, 0.0], [float("inf"), 0.0, 0.0]])), "unrepresentable")
    high = np.array([2, 32], dtype=np.uint8)
    assert "32" in _fails(lambda: write_las(path, xyz, high, point_format=0), "unrepresentable").message
    wide = np.array([0x11, 0x98], dtype=np.uint8)
    _fails(lambda: write_las(path, xyz, None, wide, version=(1, 3)), "unrepresentable")
    _fails(lambda: write_las(path, np.empty((0, 3))), "no_points")
    wrong: list[dict[str, Any]] = [{"scale": 0.0}, {"scale": float("nan")}, {"offset": (0.0, float("inf"), 0.0)}]
    wrong += [{"point_format": 11}, {"version": (1, 5)}, {"cls": np.zeros(3, dtype=np.uint8)}]
    for options in wrong:
        with pytest.raises(ValueError):
            write_las(path, xyz, **options)
    with pytest.raises(ValueError):
        write_las(path, np.zeros((3, 2)))

    class Cancelled(Exception):
        pass

    def tick(fraction: float, message: str | None = None) -> None:
        if fraction > 0.5:
            raise Cancelled

    with pytest.raises(Cancelled):
        write_las(path, np.zeros((1000, 3)), progress=tick, chunk=100)
    with pytest.raises(RuntimeError), LasWriter(path) as writer:
        writer.write(xyz)
        raise RuntimeError("the caller failed")
    assert writer.result is None
    assert _files(tmp_path) == ["out.las"] and path.read_bytes() == b"what was here before"


# ---------------------------------------------------------------- by the source


def _class_bits(fmt: int) -> tuple[int, int]:
    return (15, 0x1F) if fmt <= 5 else (16, 0xFF)  # the byte of a record and the bits of the class in it


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("fmt", range(11))
def test_by_the_source_only_the_class_bits_differ(tmp_path: Path, fmt: int, version: tuple[int, int]) -> None:
    n, extra = 500, 4
    points = random_points(n, fmt, seed=fmt + 13 * version[1], extra=extra)
    spec = Spec(version=version, fmt=fmt, scale=(0.001, 0.01, 0.002), offset=(5e6, -3e5, 100.5), extra=extra,
                vlrs=[vlr(b"Somebody", 7, b"\x01\x02\x03"), vlr(PROJECTION, 2112, wkt_data(WKT_UTM))],
                evlrs=[evlr(b"Somebody", 8, bytes(range(256)))] if version == (1, 4) else [],
                gap=b"between", software=b"Their scanner", guid=bytes(range(16)), source_id=77)  # fmt: skip
    data = las_bytes(points, spec)
    source = tmp_path / "source.las"
    source.write_bytes(data)
    before = open_las(source).read()
    rng = np.random.default_rng(fmt)
    seq = rng.permutation(n)[: n * 2 // 3].astype(np.uint32)  # a cloud keeps its points in another order
    cls = rng.integers(0, 32 if fmt <= 5 else 256, len(seq)).astype(np.uint8)
    target = tmp_path / "target.las"
    done = rewrite_classes(source, target, seq, cls)
    out = target.read_bytes()
    assert len(out) == len(data) and source.read_bytes() == data and _files(tmp_path) == ["source.las", "target.las"]
    start, size = spec_start(spec), FORMAT_SIZE[fmt] + extra
    stop = start + n * size
    assert out[:start] == data[:start] and out[stop:] == data[stop:]  # the header, the VLRs, the EVLRs
    was = np.frombuffer(data[start:stop], dtype=np.uint8).reshape(n, size)
    now = np.frombuffer(out[start:stop], dtype=np.uint8).reshape(n, size)
    byte, bits = _class_bits(fmt)
    differ = was ^ now
    assert not differ[:, :byte].any() and not differ[:, byte + 1 :].any()  # nothing but the byte of the class
    assert not (differ[:, byte] & ~np.uint8(bits)).any()  # … and in it nothing but the bits of the class
    expected = before.cls.copy()
    expected[seq] = cls
    assert np.array_equal(now[:, byte] & bits, expected)
    untouched = np.setdiff1d(np.arange(n), seq)
    assert not differ[untouched].any()
    after = open_las(target).read()
    assert after.cls.tobytes() == expected.tobytes() and after.xyz.tobytes() == before.xyz.tobytes()
    assert after.ret.tobytes() == before.ret.tobytes()
    assert (done.points, done.version, done.point_format, done.size) == (n, version, fmt, len(data))
    assert done.changed == int((expected != before.cls).sum())
    # the same classes: the same file
    same = rewrite_classes(source, tmp_path / "same.las", np.arange(n, dtype=np.uint32), before.cls)
    assert (tmp_path / "same.las").read_bytes() == data and same.changed == 0


def spec_start(spec: Spec) -> int:
    return HEADER_SIZE[spec.version] + sum(len(v) for v in spec.vlrs) + len(spec.gap)


def test_by_the_source_in_chunks_in_place_and_cancelled(tmp_path: Path) -> None:
    n = 1000
    points = random_points(n, 3, seed=21)
    data = las_bytes(points, Spec(fmt=3))
    cut = data[: len(data) - 34 * 100 - 9]  # a cut file: 899 whole records and a piece of one
    source = tmp_path / "source.las"
    source.write_bytes(cut)
    cloud = open_las(source)
    assert cloud.point_count == 899
    before = cloud.read()
    seq = np.arange(0, 899, 3, dtype=np.uint32)
    cls = np.full(len(seq), 2, dtype=np.uint8)
    expected = before.cls.copy()
    expected[seq] = 2
    seen: list[float] = []
    target = tmp_path / "target.las"
    done = rewrite_classes(source, target, seq, cls, lambda f, m: seen.append(f), chunk=128)
    assert seen == sorted(seen) and seen[0] == 0.0 and seen[-1] == 1.0 and len(seen) == 9
    out = target.read_bytes()
    assert len(out) == len(cut) and out[:227] == cut[:227] and out[-9:] == cut[-9:] and done.points == 899
    assert open_las(target).read().cls.tobytes() == expected.tobytes()
    assert rewrite_classes(source, tmp_path / "whole.las", seq, cls).size == len(cut)
    assert (tmp_path / "whole.las").read_bytes() == out  # the chunks do not show

    class Cancelled(Exception):
        pass

    def tick(fraction: float, message: str | None = None) -> None:
        if fraction > 0.5:
            raise Cancelled

    (tmp_path / "whole.las").unlink()
    with pytest.raises(Cancelled):
        rewrite_classes(source, tmp_path / "stopped.las", seq, cls, tick, chunk=128)
    assert _files(tmp_path) == ["source.las", "target.las"] and source.read_bytes() == cut
    rewrite_classes(source, source, seq, cls)  # over the source itself
    assert source.read_bytes() == out and _files(tmp_path) == ["source.las", "target.las"]


def test_by_the_source_refusals(tmp_path: Path) -> None:
    source = tmp_path / "source.las"
    source.write_bytes(las_bytes(random_points(20, 1, seed=5), Spec(fmt=1)))
    target = tmp_path / "target.las"
    seq = np.arange(20, dtype=np.uint32)
    high = np.full(20, 40, dtype=np.uint8)
    error = _fails(lambda: rewrite_classes(source, target, seq, high), "unrepresentable")
    assert "40" in error.message and "1.4" in error.hint
    beyond = np.array([3, 20], dtype=np.uint32)
    error = _fails(lambda: rewrite_classes(source, target, beyond, np.zeros(2, dtype=np.uint8)), "source_mismatch")
    assert "20" in error.message
    with pytest.raises(ValueError):
        rewrite_classes(source, target, seq, np.zeros(19, dtype=np.uint8))
    packed = tmp_path / "packed.las"
    packed.write_bytes(las_bytes(random_points(20, 1, seed=5), Spec(fmt=1, fmt_byte=129)))
    _fails(lambda: rewrite_classes(packed, target, seq, np.zeros(20, dtype=np.uint8)), "laz_not_supported")
    assert _files(tmp_path) == ["packed.las", "source.las"]


def test_header_of_1_4_with_legacy_formats_fills_both_counters(tmp_path: Path) -> None:
    xyz = np.zeros((7, 3))
    ret = np.array([0x11, 0x21, 0x22, 0x31, 0x32, 0x33, 0x55], dtype=np.uint8)
    path = tmp_path / "a.las"
    write_las(path, xyz, None, ret, version=(1, 4), point_format=3)
    head = path.read_bytes()[:375]
    assert struct.unpack_from("<I5I", head, 107) == (7, 3, 2, 1, 0, 1)
    assert struct.unpack_from("<Q15Q", head, 247) == (7, 3, 2, 1, 0, 1) + (0,) * 10
    assert struct.unpack_from("<HIIBH", head, 94) == (375, 375, 0, 3, 34)
    write_las(path, xyz, None, ret, version=(1, 4), point_format=6)
    head = path.read_bytes()[:375]
    assert struct.unpack_from("<I5I", head, 107) == (0,) * 6  # formats 6–10: the legacy counters stay empty
    assert struct.unpack_from("<Q15Q", head, 247) == (7, 3, 2, 1, 0, 1) + (0,) * 10
