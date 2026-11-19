"""LAS 1.2–1.4: the header, VLR / EVLR, point formats 0–10 against files packed with `struct`, chunks, every
k-th record, the CRS as metadata, refusals `{code, message, hint}` (ТЗ 7.35 Д-22, 9.12; ADR 0014 §3, 7.1.4)."""

from __future__ import annotations

import json
import struct
import sys
import tracemalloc
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.formats import las
from bf_core.formats.errors import FormatError
from bf_core.formats.las import FORMAT_LENGTH, inspect_las, open_las, point_dtype

from .las_builder import (
    FORMAT_SIZE,
    PROJECTION,
    VERSIONS,
    WKT_DEGREES,
    WKT_UTM,
    Pt,
    Spec,
    coordinate,
    evlr,
    geokeys,
    las_bytes,
    random_points,
    ret_byte,
    vlr,
    wkt_data,
)

pytestmark = pytest.mark.windows
SCALE, OFFSET = (0.001, 0.01, 0.002), (5e6, -3e5, 100.5)


def _write(tmp_path: Path, content: bytes, name: str = "cloud.las") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def _expected(points: list[Pt], spec: Spec) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xyz = [[coordinate(getattr(p, a), spec.scale[k], spec.offset[k]) for k, a in enumerate("XYZ")] for p in points]
    return (
        np.array(xyz, dtype=np.float64).reshape(-1, 3),
        np.array([p.cls for p in points], dtype=np.uint8),
        np.array([ret_byte(p) for p in points], dtype=np.uint8),
    )


def _fails(path: Path, code: str, call: Any = open_las) -> FormatError:
    with pytest.raises(FormatError) as info:
        call(path)
    error = info.value
    assert error.code == code, error.as_dict()
    assert error.message and error.hint and any("а" <= ch <= "я" for ch in error.message.lower())
    assert any("а" <= ch <= "я" for ch in error.hint.lower())
    return error


def _mapped(path: Path) -> bool:
    if not sys.platform.startswith("linux"):
        return False
    return path.name in Path("/proc/self/maps").read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- formats and versions


def test_record_layouts_have_the_lengths_of_the_specification() -> None:
    assert FORMAT_LENGTH == FORMAT_SIZE
    for fmt, size in FORMAT_SIZE.items():
        assert point_dtype(fmt).itemsize == size and point_dtype(fmt, size + 7).itemsize == size + 7
        assert point_dtype(fmt).fields["X"][1] == 0 and point_dtype(fmt).fields["Z"][1] == 8  # type: ignore[index]


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("fmt", range(11))
@pytest.mark.parametrize("extra", [0, 5])
def test_every_format_in_every_version_reads_exactly(
    tmp_path: Path, fmt: int, version: tuple[int, int], extra: int
) -> None:
    points = random_points(300, fmt, seed=fmt * 10 + version[1], extra=extra)
    spec = Spec(version=version, fmt=fmt, scale=SCALE, offset=OFFSET, extra=extra, software=b"struct", system_id=b"T")
    cloud = open_las(_write(tmp_path, las_bytes(points, spec)))
    h = cloud.header
    assert (h.version, h.point_format) == (version, fmt)
    assert (h.record_length, h.extra_bytes) == (FORMAT_SIZE[fmt] + extra, extra)
    assert (h.point_count, h.declared_count, cloud.point_count) == (300, 300, 300)
    assert (h.scale, h.offset, h.software, h.system_id, h.created) == (SCALE, OFFSET, "struct", "T", (2026, 1))
    xyz, cls, ret = _expected(points, spec)
    got = cloud.read()
    # X·scale + offset in doubles: bit for bit, no tolerance
    assert got.xyz.dtype == np.float64 and got.xyz.shape == (300, 3) and got.xyz.tobytes() == xyz.tobytes()
    assert got.cls.dtype == np.uint8 and got.cls.tobytes() == cls.tobytes()
    assert got.ret.dtype == np.uint8 and got.ret.tobytes() == ret.tobytes()
    assert got.seq.dtype == np.dtype("<u4") and got.seq.tolist() == list(range(300))
    assert set(got.columns()) == {"xyz", "seq", "class", "ret"} and got.columns()["class"] is got.cls
    assert h.bbox is not None and np.array_equal(h.bbox, [*xyz.min(axis=0), *xyz.max(axis=0)])
    if (fmt > 3 and version == (1, 2)) or (fmt > 5 and version == (1, 3)):
        assert any("не предусмотрен" in w for w in cloud.warnings)  # read all the same
    else:
        assert not [w for w in cloud.warnings if "не предусмотрен" in w]
    raw = cloud.records()
    assert raw.dtype == point_dtype(fmt, FORMAT_SIZE[fmt] + extra)
    assert raw["X"].tolist() == [p.X for p in points] and raw["intensity"].tolist() == [p.intensity for p in points]
    if extra:
        assert h.not_loaded()[-1] == "дополнительные байты (5 Б на точку)"


def test_class_of_legacy_formats_is_five_bits_and_of_new_ones_a_byte(tmp_path: Path) -> None:
    old = [Pt(1, 2, 3, cls=31, synthetic=1, keypoint=1, withheld=1, ret=7, nret=7, scan_dir=1, edge=1)]
    cloud = open_las(_write(tmp_path, las_bytes(old, Spec(fmt=1)), "old.las"))
    got = cloud.read()
    assert got.cls.tolist() == [31] and got.ret.tolist() == [0x77]
    flags = {"synthetic": 1, "keypoint": 1, "withheld": 1, "overlap": 1}
    new = [Pt(1, 2, 3, cls=c, ret=15, nret=15, **flags) for c in (0, 31, 64, 255)]
    got = open_las(_write(tmp_path, las_bytes(new, Spec(version=(1, 4), fmt=6)), "new.las")).read()
    assert got.cls.tolist() == [0, 31, 64, 255] and got.ret.tolist() == [0xFF] * 4


def test_extremes_of_int32_and_scales(tmp_path: Path) -> None:
    lo, hi = -(2**31), 2**31 - 1
    points = [Pt(lo, hi, 0), Pt(hi, lo, -1), Pt(0, 0, 1), Pt(-1, 1, hi), Pt(123456789, -987654321, lo)]
    for scale, offset in (
        ((0.001, 0.001, 0.001), (5e6, 5e6, 0.0)),
        ((0.01, 0.01, 0.01), (5e6, -5e6, 1e3)),
        ((1e-7, 1e-7, 0.001), (37.5, 55.75, 150.0)),  # degrees
        ((0.25, 1.0, -0.5), (0.0, 0.0, 0.0)),  # exact in binary, and a negative scale
        ((1e-9, 3.0000000000000004e-3, 1e-3), (1e7, 1e-9, 4.9e-324)),
    ):
        spec = Spec(scale=scale, offset=offset)
        got = open_las(_write(tmp_path, las_bytes(points, spec))).read()
        assert got.xyz.tobytes() == _expected(points, spec)[0].tobytes()
        assert np.isfinite(got.xyz).all()


# ---------------------------------------------------------------- chunks


def test_chunks_follow_the_file_and_report_progress(tmp_path: Path) -> None:
    points = random_points(1057, 3, seed=5)
    spec = Spec(fmt=3, scale=SCALE, offset=OFFSET)
    path = _write(tmp_path, las_bytes(points, spec))
    xyz, cls, ret = _expected(points, spec)
    cloud = open_las(path)
    seen: list[tuple[float, str | None]] = []
    chunks = list(cloud.chunks(lambda f, m: seen.append((f, m)), chunk=100))
    assert [len(c) for c in chunks] == [100] * 10 + [57] and [c.start for c in chunks] == list(range(0, 1057, 100))
    assert np.concatenate([c.xyz for c in chunks]).tobytes() == xyz.tobytes()
    assert np.concatenate([c.cls for c in chunks]).tobytes() == cls.tobytes()
    assert np.concatenate([c.ret for c in chunks]).tobytes() == ret.tobytes()
    assert np.concatenate([c.seq for c in chunks]).tolist() == list(range(1057))
    for c in chunks:  # plain arrays of their own: nothing of the map is left in them
        for column in c.columns().values():
            assert type(column) is np.ndarray and column.flags.owndata and column.flags.c_contiguous
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and fractions[0] == 0.0 and fractions[-1] == 1.0 and len(seen) == 12
    assert seen[3][1] == "Чтение точек: 300 из 1 057" and seen[-1][1] == "Прочитано точек: 1 057"
    # the same again: a cloud is built in two passes
    again = cloud.read()
    assert again.xyz.tobytes() == xyz.tobytes() and len(again) == 1057
    for size in (1, 1056, 1057, 1058, 5000):
        parts = list(cloud.chunks(chunk=size))
        assert sum(len(c) for c in parts) == 1057 and len(parts) == -(-1057 // size)
        assert np.concatenate([c.xyz for c in parts]).tobytes() == xyz.tobytes()
    assert not _mapped(path)


@pytest.mark.parametrize("every", [2, 7, 100, 1056, 1057, 5000])
def test_every_kth_record(tmp_path: Path, every: int) -> None:
    points = random_points(1057, 6, seed=6)
    spec = Spec(version=(1, 4), fmt=6, scale=SCALE, offset=OFFSET)
    cloud = open_las(_write(tmp_path, las_bytes(points, spec)))
    xyz, cls, ret = _expected(points, spec)
    for size in (64, 100, 1057, las.CHUNK):
        chunks = list(cloud.chunks(every=every, chunk=size))
        assert all(len(c) for c in chunks)
        seq = np.concatenate([c.seq for c in chunks])
        assert seq.tolist() == list(range(0, 1057, every)) and cloud.count(every) == len(seq)
        assert np.concatenate([c.xyz for c in chunks]).tobytes() == xyz[::every].tobytes()
        assert np.concatenate([c.cls for c in chunks]).tobytes() == cls[::every].tobytes()
        assert np.concatenate([c.ret for c in chunks]).tobytes() == ret[::every].tobytes()
    with pytest.raises(ValueError):
        list(cloud.chunks(every=0))


def test_cancel_between_chunks_leaves_no_map_and_no_lock(tmp_path: Path) -> None:
    class Cancelled(Exception):
        pass

    path = _write(tmp_path, las_bytes(random_points(500, 0, seed=1)))
    cloud = open_las(path)
    calls = 0

    def tick(fraction: float, message: str | None = None) -> None:
        nonlocal calls
        calls += 1
        if fraction > 0.75:
            raise Cancelled

    got = 0
    with pytest.raises(Cancelled):
        for c in cloud.chunks(tick, chunk=100):
            got += len(c)
    assert got == 400 and calls == 5 and not _mapped(path)
    it = cloud.chunks(chunk=100)  # the consumer stops by itself
    first = next(it)
    it.close()
    assert len(first) == 100 and not _mapped(path)
    inspect_las(path)
    del it, first, c
    path.unlink()  # Windows: neither a handle nor a map is left
    assert not path.exists()


# ---------------------------------------------------------------- VLR, EVLR, CRS


def test_wkt_in_a_vlr_and_in_an_evlr(tmp_path: Path) -> None:
    points = random_points(20, 6, seed=2)
    other = vlr(b"Somebody", 7, b"\x01\x02\x03", b"three bytes")
    in_vlr = vlr(PROJECTION, 2112, wkt_data(WKT_UTM), b"WKT")
    spec = Spec(version=(1, 4), fmt=6, global_encoding=16, vlrs=[other, in_vlr])
    h = open_las(_write(tmp_path, las_bytes(points, spec), "vlr.las")).header
    assert h.wkt == WKT_UTM and h.geokeys is None and h.epsg is None and not h.geographic
    assert h.crs_text() == WKT_UTM
    assert [(v.user_id, v.record_id, v.length, v.description, v.extended) for v in h.vlrs] == [
        ("Somebody", 7, 3, "three bytes", False),
        ("LASF_Projection", 2112, len(WKT_UTM) + 1, "WKT", False),
    ]
    in_evlr = evlr(PROJECTION, 2112, wkt_data(WKT_UTM))
    spec = Spec(version=(1, 4), fmt=7, global_encoding=16, vlrs=[other], evlrs=[in_evlr])
    cloud = open_las(_write(tmp_path, las_bytes(points, spec), "evlr.las"))
    h = cloud.header
    assert h.wkt == WKT_UTM and cloud.point_count == 20 and not cloud.warnings
    assert [(v.user_id, v.record_id, v.extended) for v in h.vlrs] == [
        ("Somebody", 7, False),
        ("LASF_Projection", 2112, True),
    ]
    assert cloud.read().xyz.tobytes() == _expected(points, spec)[0].tobytes()  # the EVLR is not taken for points
    meta = h.as_meta()
    assert json.loads(json.dumps(meta)) == meta
    assert (meta["version"], meta["point_format"], meta["record_length"]) == ("1.4", 7, 36)
    assert meta["wkt"] == WKT_UTM and meta["scale"] == [0.001] * 3 and meta["offset"] == [0.0] * 3
    assert meta["vlrs"][1] == {
        "user_id": "LASF_Projection", "record_id": 2112, "length": len(WKT_UTM) + 1, "description": "", "extended": True
    }  # fmt: skip
    from bf_core.crs import describe

    assert describe(h.crs_text() or "").epsg == 32637


def test_geotiff_keys(tmp_path: Path) -> None:
    points = random_points(20, 1, seed=3)
    keys = geokeys(32637, name="WGS 84 / UTM zone 37N", doubles=[6378137.0])
    h = open_las(_write(tmp_path, las_bytes(points, Spec(fmt=1, vlrs=keys)))).header
    assert h.wkt is None and h.epsg == 32637 and not h.geographic and h.crs_text() == "EPSG:32637"
    assert h.geokeys == {
        "1024": 1, "1025": 1, "1026": "WGS 84 / UTM zone 37N", "2057": 6378137.0, "3072": 32637, "3076": 9001
    }  # fmt: skip
    meta = h.as_meta()
    assert meta["geokeys"] == h.geokeys and meta["epsg"] == 32637 and json.loads(json.dumps(meta)) == meta
    assert [v.record_id for v in h.vlrs] == [34735, 34736, 34737]


def test_odd_geotiff_keys_are_kept_out(tmp_path: Path) -> None:
    def directory(*keys: tuple[int, int, int, int], version: int = 1) -> bytes:
        words = [version, 1, 0, len(keys), *(w for key in keys for w in key)]
        return vlr(PROJECTION, 34735, struct.pack(f"<{len(words)}H", *words))

    def header(*vlrs: bytes) -> las.LasHeader:
        return open_las(_write(tmp_path, las_bytes([Pt(1, 2, 3)], Spec(vlrs=list(vlrs))))).header

    # a code kept as a text, a double and a text beyond their records, a user-defined CRS, a key cut in half
    texts = vlr(PROJECTION, 34737, b"My grid|\x00")
    h = header(directory((3072, 34737, 8, 0), (2057, 34736, 1, 5), (1026, 34737, 50, 0), (2048, 0, 1, 32767)), texts)
    assert h.geokeys == {"3072": "My grid", "2048": 32767} and h.epsg is None and h.crs_text() is None
    assert h.crs_name() is None and json.loads(json.dumps(h.as_meta()))["geokeys"] == h.geokeys
    assert header(directory((3072, 0, 1, 32637), version=2)).geokeys is None  # not a directory of version 1
    cut = vlr(PROJECTION, 34735, struct.pack("<10H", 1, 1, 0, 9, 3072, 0, 1, 28407, 1, 2))  # nine keys are promised
    assert header(cut).epsg == 28407
    assert header(vlr(PROJECTION, 34735, b"\x01")).geokeys is None
    nan = vlr(PROJECTION, 34736, struct.pack("<2d", float("nan"), 6378137.0))
    assert header(directory((2057, 34736, 1, 0), (2058, 34736, 1, 1)), nan).geokeys == {"2058": 6378137.0}


def test_degrees_are_warned_about(tmp_path: Path) -> None:
    points = [Pt(375000000, 557500000, 150000)]
    spec = Spec(scale=(1e-7, 1e-7, 0.001), vlrs=geokeys(4326, projected=False))
    cloud = open_las(_write(tmp_path, las_bytes(points, spec), "keys.las"))
    assert cloud.header.geographic and cloud.header.epsg == 4326 and cloud.header.crs_text() == "EPSG:4326"
    assert any("градус" in w and "метр" in w for w in cloud.warnings)
    degrees = evlr(PROJECTION, 2112, wkt_data(WKT_DEGREES))
    spec = Spec(version=(1, 4), global_encoding=16, scale=(1e-7, 1e-7, 0.001), evlrs=[degrees])
    cloud = open_las(_write(tmp_path, las_bytes(points, spec), "wkt.las"))
    assert cloud.header.geographic and any("градус" in w for w in cloud.warnings)
    plain = open_las(_write(tmp_path, las_bytes(points, Spec()), "plain.las"))
    assert not plain.header.geographic and plain.header.crs_text() is None and not plain.warnings


def test_long_wkt_stays_out_of_the_metadata(tmp_path: Path) -> None:
    long = WKT_UTM + " " * las.MAX_WKT
    spec = Spec(version=(1, 4), fmt=6, global_encoding=16, evlrs=[evlr(PROJECTION, 2112, wkt_data(long))])
    cloud = open_las(_write(tmp_path, las_bytes([Pt(1, 2, 3)], spec)))
    assert cloud.header.wkt == WKT_UTM  # blanks are cut off: it fits
    huge = "X" * (las.MAX_WKT + 1)
    spec = Spec(version=(1, 4), fmt=6, global_encoding=16, evlrs=[evlr(PROJECTION, 2112, wkt_data(huge))])
    cloud = open_las(_write(tmp_path, las_bytes([Pt(1, 2, 3)], spec)))
    assert cloud.header.wkt is None and any("WKT" in w for w in cloud.warnings) and cloud.point_count == 1


def test_broken_vlrs_do_not_stop_the_points(tmp_path: Path) -> None:
    points = random_points(10, 0, seed=4)
    good = las_bytes(points, Spec(vlrs=[vlr(b"A", 1, b"1234")]))
    # the VLR says it is longer than the room before the points
    bad = bytearray(good)
    struct.pack_into("<H", bad, 227 + 20, 60000)
    cloud = open_las(_write(tmp_path, bytes(bad), "long.las"))
    assert cloud.point_count == 10 and any("VLR" in w for w in cloud.warnings) and cloud.header.vlrs == ()
    assert cloud.read().xyz.tobytes() == _expected(points, Spec())[0].tobytes()
    # more VLRs are promised than there are
    bad = bytearray(good)
    struct.pack_into("<I", bad, 100, 4_000_000_000)
    cloud = open_las(_write(tmp_path, bytes(bad), "many.las"))
    assert cloud.point_count == 10 and len(cloud.header.vlrs) == 1 and any("VLR" in w for w in cloud.warnings)
    # an EVLR that starts beyond the file
    spec = Spec(version=(1, 4), fmt=6, evlrs=[evlr(b"A", 1, b"1234")])
    bad = bytearray(las_bytes(random_points(10, 6, seed=4), spec))
    struct.pack_into("<Q", bad, 235, len(bad) + 1000)
    cloud = open_las(_write(tmp_path, bytes(bad), "evlr.las"))
    assert cloud.point_count == 10 and any("EVLR" in w for w in cloud.warnings)


# ---------------------------------------------------------------- versions and counters


def test_version_1_1_is_read_as_1_2_with_a_warning(tmp_path: Path) -> None:
    points = random_points(50, 1, seed=7)
    spec = Spec(version=(1, 1), fmt=1, scale=SCALE, offset=OFFSET)
    cloud = open_las(_write(tmp_path, las_bytes(points, spec)))
    assert cloud.header.version == (1, 1) and any("1.1" in w and "1.2" in w for w in cloud.warnings)
    assert cloud.read().xyz.tobytes() == _expected(points, spec)[0].tobytes()


def test_counters_of_1_4(tmp_path: Path) -> None:
    points = random_points(40, 1, seed=8)
    expected = _expected(points, Spec())[0].tobytes()
    # format 1 in 1.4: both counters are filled; the legacy one alone (a writer that only raised the version)
    for spec in (Spec(version=(1, 4), fmt=1), Spec(version=(1, 4), fmt=1, declared=0, legacy_declared=40)):
        cloud = open_las(_write(tmp_path, las_bytes(points, spec)))
        assert (cloud.point_count, cloud.header.declared_count) == (40, 40) and not cloud.warnings
        assert cloud.read().xyz.tobytes() == expected
    # no counter at all: by the size of the file
    cloud = open_las(_write(tmp_path, las_bytes(points, Spec(version=(1, 4), fmt=1, declared=0, legacy_declared=0))))
    assert (cloud.point_count, cloud.header.declared_count) == (40, 0)
    assert any("не указано" in w and "40" in w for w in cloud.warnings) and cloud.read().xyz.tobytes() == expected
    # fewer records are declared than the file holds: the header is believed
    cloud = open_las(_write(tmp_path, las_bytes(points, Spec(fmt=1, declared=30))))
    assert cloud.point_count == 30 and any("280" in w for w in cloud.warnings)
    assert cloud.read().xyz.tobytes() == expected[: 30 * 24]


def test_wave_packets_after_the_points_are_not_points(tmp_path: Path) -> None:
    points = random_points(25, 4, seed=15)
    waves = bytes(range(256)) * 3  # 13 records and a piece, were they taken for points
    start = 235 + 25 * 57
    for declared, said in ((None, 25), (0, 0)):  # a header with the counter and one without
        spec = Spec(version=(1, 3), fmt=4, global_encoding=2, wave_start=start, declared=declared)
        cloud = open_las(_write(tmp_path, las_bytes(points, spec) + waves))
        assert (cloud.point_count, cloud.header.declared_count) == (25, said)
        assert not [w for w in cloud.warnings if "непрочитанные" in w or "обрезан" in w]
        assert cloud.read().xyz.tobytes() == _expected(points, spec)[0].tobytes()
    lost = open_las(_write(tmp_path, las_bytes(points, Spec(version=(1, 3), fmt=4, declared=0)) + waves))
    assert lost.point_count == 25 + len(waves) // 57  # nothing tells where the points end: by the size of the file


def test_cut_file_gives_whole_records_and_a_warning(tmp_path: Path) -> None:
    points = random_points(100, 2, seed=9)
    data = las_bytes(points, Spec(fmt=2))
    cloud = open_las(_write(tmp_path, data[: 227 + 26 * 60 + 11]))
    assert (cloud.point_count, cloud.header.declared_count) == (60, 100)
    assert any("обрезан" in w and "100" in w and "60" in w for w in cloud.warnings)
    assert cloud.read().xyz.tobytes() == _expected(points[:60], Spec())[0].tobytes()


def test_declared_billions_do_not_allocate(tmp_path: Path) -> None:
    points = random_points(10, 6, seed=10)
    path = _write(tmp_path, las_bytes(points, Spec(version=(1, 4), fmt=6, declared=4_000_000_000_000)))
    tracemalloc.start()
    try:
        cloud = open_las(path)
        got = cloud.read()
        report = inspect_las(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1 << 20, peak
    assert (len(got), cloud.point_count, report.header.point_count) == (10, 10, 10)
    assert cloud.header.declared_count == 4_000_000_000_000 and any("обрезан" in w for w in cloud.warnings)
    legacy = _write(tmp_path, las_bytes(random_points(3, 0, seed=10), Spec(declared=0xFFFFFFFF)), "legacy.las")
    assert open_las(legacy).point_count == 3


def test_withheld_and_overlap_are_counted(tmp_path: Path) -> None:
    points = [Pt(i, i, i, cls=2) for i in range(10)]
    points[1].withheld = points[2].withheld = 1
    points[3].cls = 12  # «overlap» of the formats 0–5 is a class
    cloud = open_las(_write(tmp_path, las_bytes(points, Spec()), "old.las"))
    assert not cloud.warnings and cloud.flags is None
    got = cloud.read()
    assert cloud.flags == {"withheld": 2, "overlap": 1} and cloud.classes == {2: 9, 12: 1}
    assert got.cls.tolist() == [2, 2, 2, 12, 2, 2, 2, 2, 2, 2]  # nothing is dropped
    assert sum("исключ" in w and "2" in w for w in cloud.warnings) == 1
    assert sum("перекрыт" in w for w in cloud.warnings) == 1
    points = [Pt(i, i, i, cls=200) for i in range(10)]
    for p in points[:4]:
        p.overlap = 1
    points[9].withheld = 1
    cloud = open_las(_write(tmp_path, las_bytes(points, Spec(version=(1, 4), fmt=8)), "new.las"))
    list(cloud.chunks(chunk=3))
    assert cloud.flags == {"withheld": 1, "overlap": 4} and cloud.classes == {200: 10}
    list(cloud.chunks(every=5))  # counted over the records that were read
    assert cloud.flags == {"withheld": 0, "overlap": 1} and len(cloud.warnings) == 1


# ---------------------------------------------------------------- refusals


def test_laz_is_refused_three_ways(tmp_path: Path) -> None:
    points = random_points(10, 1, seed=11)
    by_name = _fails(_write(tmp_path, las_bytes(points, Spec(fmt=1)), "Облако.LAZ"), "laz_not_supported")
    by_bit = _fails(_write(tmp_path, las_bytes(points, Spec(fmt=1, fmt_byte=129)), "bit.las"), "laz_not_supported")
    packed = vlr(b"laszip encoded", 22204, bytes(34), b"by laszip")
    by_vlr = _fails(_write(tmp_path, las_bytes(points, Spec(fmt=1, vlrs=[packed])), "vlr.las"), "laz_not_supported")
    for error in (by_name, by_bit, by_vlr):
        assert error.message == "Сжатые облака LAZ не читаются."
        assert "CloudCompare" in error.hint and "laszip -i облако.laz -o облако.las" in error.hint
    _fails(_write(tmp_path, b"", "empty.laz"), "laz_not_supported", inspect_las)


def test_refusals(tmp_path: Path) -> None:
    points = random_points(10, 0, seed=12)
    good = las_bytes(points, Spec())
    seen = {}

    def refuse(name: str, content: bytes, code: str) -> FormatError:
        error = _fails(_write(tmp_path, content, name), code)
        assert _fails(tmp_path / name, code, inspect_las).as_dict() == error.as_dict()
        seen[name] = (error.code, error.message)
        return error

    refuse("empty.las", b"", "empty_file")
    refuse("alien.las", b"PK\x03\x04" + good[4:], "not_las")
    _fails(_write(tmp_path, b"x y z\n1 2 3\n", "text.las"), "not_las")
    assert "1.0" in refuse("v10.las", las_bytes(points, Spec(version=(1, 0))), "unsupported_version").message
    assert "2.0" in refuse("v20.las", las_bytes(points, Spec(version=(2, 0))), "unsupported_version").message
    refuse("head.las", good[:150], "truncated")
    refuse("head14.las", las_bytes(points, Spec(version=(1, 4)))[:300], "truncated")
    refuse("nopoints.las", good[:227], "truncated")
    refuse("midvlr.las", las_bytes(points, Spec(vlrs=[vlr(b"A", 1, bytes(100))]))[:260], "truncated")
    error = refuse("short.las", las_bytes(points, Spec(fmt=3, record_length=33)), "bad_record_length")
    assert "33" in error.message and "34" in error.message
    refuse("zero.las", las_bytes(points, Spec(scale=(0.001, 0.0, 0.001))), "bad_scale")
    refuse("nan.las", las_bytes(points, Spec(scale=(float("nan"), 0.001, 0.001))), "bad_scale")
    refuse("inf.las", las_bytes(points, Spec(scale=(0.001, 0.001, float("inf")))), "bad_scale")
    refuse("offset.las", las_bytes(points, Spec(offset=(0.0, float("inf"), 0.0))), "bad_offset")
    assert "11" in refuse("f11.las", las_bytes(points, Spec(fmt=0, fmt_byte=11)), "unsupported_format").message
    refuse("none.las", las_bytes([], Spec()), "no_points")
    refuse("hsize.las", las_bytes(points, Spec(header_size=100)), "broken")
    refuse("where.las", good[:96] + struct.pack("<I", 50) + good[100:], "broken")
    assert len({message for _, message in seen.values()}) == len(seen)  # each refusal says its own


def test_too_many_points(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, las_bytes(random_points(100, 0, seed=13), Spec()))
    monkeypatch.setattr(las, "MAX_POINTS", 40)
    monkeypatch.setattr(las, "SUGGEST_EVERY", 20)
    cloud = open_las(path)  # the header is read: the wizard offers every k-th record
    report = inspect_las(path)
    assert report.every == 5 and any("кажд" in w for w in report.warnings)
    with pytest.raises(FormatError) as info:
        next(cloud.chunks())
    assert info.value.code == "too_many_points" and "100" in info.value.message and info.value.hint
    with pytest.raises(FormatError):
        cloud.read(every=2)
    assert len(cloud.read(every=3)) == 34


# ---------------------------------------------------------------- inspect


def test_inspect(tmp_path: Path) -> None:
    points = random_points(500, 8, seed=14, extra=3)
    for p in points:
        p.cls = 2 if p.X % 3 else 5
    spec = Spec(version=(1, 4), fmt=8, scale=SCALE, offset=OFFSET, global_encoding=16, extra=3,
                evlrs=[evlr(PROJECTION, 2112, wkt_data(WKT_UTM))])  # fmt: skip
    path = _write(tmp_path, las_bytes(points, spec), "Карьер 5.las")
    report = inspect_las(path)
    xyz, cls, _ = _expected(points, spec)
    assert report.header.point_count == 500 and report.sampled == 500 and report.every == 1
    assert report.classes == {2: int((cls == 2).sum()), 5: int((cls == 5).sum())}
    assert report.bbox_exact and np.array_equal(report.bbox, [*xyz.min(axis=0), *xyz.max(axis=0)])
    assert report.not_loaded == [
        "интенсивность", "цвет RGB", "ближний инфракрасный канал", "время GPS", "угол сканирования",
        "пользовательские данные", "номер источника", "дополнительные байты (3 Б на точку)",
    ]  # fmt: skip
    assert report.summary == "LAS 1.4, формат точек 8: точек 500, СК: WGS 84 / UTM zone 37N"
    data = report.as_dict()
    assert json.loads(json.dumps(data)) == data
    assert data["version"] == "1.4" and data["point_count"] == 500
    assert data["classes"] == {"2": report.classes[2], "5": report.classes[5]}
    assert data["crs"] == {"wkt": WKT_UTM, "epsg": None, "geographic": False, "name": "WGS 84 / UTM zone 37N"}
    assert not _mapped(path)
    waves = inspect_las(_write(tmp_path, las_bytes(random_points(5, 4, seed=1), Spec(version=(1, 3), fmt=4)), "w.las"))
    assert waves.not_loaded == [
        "интенсивность", "время GPS", "угол сканирования", "пользовательские данные", "номер источника",
        "волновые пакеты",
    ]  # fmt: skip
    assert waves.summary == "LAS 1.3, формат точек 4: точек 5, СК не указана"


def test_inspect_samples_a_big_file_by_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    points = [Pt(i, -i, i % 7, cls=i * 5 // 2000) for i in range(2000)]
    path = _write(tmp_path, las_bytes(points, Spec(bbox=(0.0,) * 6)))  # a header without the extent
    monkeypatch.setattr(las, "SAMPLE", 320)
    monkeypatch.setattr(las, "SAMPLE_BLOCKS", 16)
    report = inspect_las(path)
    assert report.sampled == 320 and sum(report.classes.values()) == 320
    assert set(report.classes) == {0, 1, 2, 3, 4} and max(report.classes.values()) <= 80  # from all over the file
    assert not report.bbox_exact and report.bbox is not None
    assert report.bbox[0] == 0.0 and report.bbox[3] > 1.9 and any("охват" in w.lower() for w in report.warnings)
    assert inspect_las(path, sample=0).classes == {} and inspect_las(path, sample=5000).sampled == 2000
