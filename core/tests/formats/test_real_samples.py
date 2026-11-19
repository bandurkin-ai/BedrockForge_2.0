"""The owner's samples of 2026-11-18 against the readers of Э7 and Э8 (ТЗ 7.35 Д-13, 9.6, 9.12; ADR 0009 № 4,
ADR 0013 §2). The archives of `tests/data/real` are read only; a test is skipped when its archive is not there.

  * `Datamine_block_model.zip` — `optimizedpits_2tr.dm`: not a block model but the triangle table of a wireframe
    written by Datamine Studio NPVS (extended precision), without its points table `optimizedpits_2pt.dm`;
  * `Datamine_wireframe.zip` — `FXDRIVES_tr.dmx` + `FXDRIVES_pt.dmx`: the compressed tables of Datamine Studio,
    not read by this version: the files must be told for what they are;
  * `Surpac string.zip` — a text string file (9 vertices) and a binary one (7 vertices of another line).

References, apart from the readers: the size and the header words of the `.dm` (1 087 pages; 9 words a record,
last page 1 087 with 40 records → 1 085 × 56 + 40 = 60 800 records), its records decoded here straight from the
pages, three of them written out by hand from a dump, and `TRIANGLE` running 1…60 800 — a record taken from a
wrong place of a page would break the run. The Surpac vertices are typed from the text of the file (Y first).
Tolerance: none, doubles bit for bit.
"""

from __future__ import annotations

import shutil
import struct
import time
import tracemalloc
import zipfile
from pathlib import Path

import numpy as np
import pytest

from bf_core.formats.datamine import read_dm, read_header
from bf_core.formats.datamine_dmx import DMX_MAGIC, is_dmx
from bf_core.formats.datamine_objects import find_pair, open_dm_wireframes, split_fields
from bf_core.formats.errors import FormatError
from bf_core.formats.registry import detect_object, group_files, read_objects
from bf_core.formats.str_surpac import is_surpac, is_surpac_binary, read_surpac, write_surpac
from tests.formats.dm_builder import F, build_numbers

REAL = Path(__file__).resolve().parents[3] / "tests" / "data" / "real"
TR_FIELDS = ["PID1", "PID2", "PID3", "TRIANGLE", "Elevatio", "Benches", "Benche~2", "InPitLim", "Pit"]
ROWS, PAGE, PER_PAGE = 60_800, 4096, 56
#: records written out from a dump of the file: the first, the first of the second data page, the last
BY_HAND = {
    0: (15201.0, 15297.0, 1.0, 1.0, 215.0, -1.0, -1.0, 0.0, 73.0),
    56: (15215.0, 15311.0, 15.0, 57.0, 220.0, -1.0, -1.0, 0.0, 73.0),
    ROWS - 1: (30560.0, 30559.0, 15200.0, 60800.0, 240.0, -1.0, -1.0, 0.0, 73.0),
}
#: the text sample, as typed in the file: `1, Y, X, Z,`
SURPAC_YX = [
    (-0.051, -1.702), (0.124, -1.389), (0.061, -0.575), (-0.218, -0.502), (-0.542, -1.130),
    (-0.556, -1.648), (-0.336, -2.093), (-0.059, -2.155), (0.146, -2.037),
]  # fmt: skip


def unpack(archive: str, folder: Path) -> list[Path]:
    """The files of an archive of `tests/data/real` in `folder`, under the last part of their names."""
    source = REAL / archive
    if not source.is_file():
        pytest.skip(f"tests/data/real/{archive} is not here")
    out: list[Path] = []
    with zipfile.ZipFile(source) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            out.append(folder / Path(info.filename).name)
            with z.open(info) as src, out[-1].open("wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    return sorted(out)


def fails(call: object, *args: object, **kwargs: object) -> FormatError:
    with pytest.raises(FormatError) as info:
        call(*args, **kwargs)  # type: ignore[operator]
    err = info.value
    assert err.message and err.hint and set(err.as_dict()) == {"code", "message", "hint"}
    return err


def bits(values: object) -> np.ndarray:
    return np.ascontiguousarray(values, dtype=np.float64).view(np.uint64)


@pytest.fixture(scope="module")
def triangles(tmp_path_factory: pytest.TempPathFactory) -> Path:
    (path,) = unpack("Datamine_block_model.zip", tmp_path_factory.mktemp("dm"))
    assert path.name == "optimizedpits_2tr.dm"
    return path


@pytest.fixture(scope="module")
def dmx(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return {p.name: p for p in unpack("Datamine_wireframe.zip", tmp_path_factory.mktemp("dmx"))}


@pytest.fixture(scope="module")
def surpac(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """The two string files: "text" and "binary" (their names in the archive are not readable)."""
    files = unpack("Surpac string.zip", tmp_path_factory.mktemp("surpac"))
    assert len(files) == 2 and all(p.suffix == ".str" for p in files)
    return {"binary" if b"\x00" in p.read_bytes() else "text": p for p in files}


def pages_of(path: Path) -> np.ndarray:
    """The records of the `.dm` taken straight from its pages (9 words each, 56 to a page), not by the reader."""
    raw = np.fromfile(path, dtype="<f8").reshape(-1, 512)
    assert raw.shape[0] == 1087 and tuple(raw[0, 25:28]) == (9.0, 1087.0, 40.0)
    return raw[1:, : PER_PAGE * 9].reshape(-1, 9)[:ROWS]


# ---------------------------------------------------------------- Datamine .dm of Studio: triangles without points


def test_dm_of_studio_header(triangles: Path) -> None:
    assert triangles.stat().st_size == 1087 * PAGE
    h = read_header(triangles)
    assert (h.precision, h.word, h.page, h.layout) == ("extended", 8, PAGE, "dm")
    assert (h.name, h.description, h.owner) == ("", "Created from Studio NPVS", "SYSTEM")
    assert h.names == TR_FIELDS and all(f.kind == "N" and f.stored for f in h.fields)
    assert (h.record_words, h.per_page, h.data_page, h.records) == (9, PER_PAGE, 1, ROWS)
    assert h.records == (1087 - 2) * PER_PAGE + 40  # the pages of the header and what it says of the last one
    assert h.kind == "triangles" and h.warnings == [] and h.constants == {}
    # the default of a geometry field is 0, of an attribute — «absent» (−1e30 in the file)
    assert [f.default for f in h.fields[:4]] == [0.0] * 4 and all(f.default != f.default for f in h.fields[4:])
    assert split_fields(h) == TR_FIELDS[4:]


def test_dm_of_studio_records_bit_for_bit(triangles: Path) -> None:
    expected = pages_of(triangles)
    for row, values in BY_HAND.items():
        assert tuple(expected[row]) == values, row
    started = time.perf_counter()
    read = read_dm(triangles)
    seconds = time.perf_counter() - started
    assert read.rows == ROWS and read.names == TR_FIELDS and read.warnings == []
    for k, column in enumerate(read.columns):
        assert column.kind == "number" and (bits(column.values) == bits(expected[:, k])).all(), column.name
    t = {c.name: c.values for c in read.columns}
    assert (t["TRIANGLE"] == np.arange(1, ROWS + 1)).all()  # no record is lost or taken twice at a page end
    assert not any(np.isnan(v).any() for v in t.values())  # −1 of «Benches» is data, not «absent»
    corners = np.column_stack([t["PID1"], t["PID2"], t["PID3"]])
    assert (corners == np.floor(corners)).all() and (corners.min(), corners.max()) == (1.0, 30656.0)
    assert len(np.unique(corners)) == 30_656  # every number 1…30 656 is a corner of some triangle
    # the surface of NPVS: a grid of 15 200 cells, four triangles around the centre of each
    assert (t["PID3"] == (np.arange(ROWS) // 4) + 1).all() and t["PID1"].min() == 15201.0
    assert (t["Elevatio"].min(), t["Elevatio"].max()) == (45.0, 240.0) and (t["Pit"] == 73.0).all()
    tracemalloc.start()
    again = read_dm(triangles)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    result = sum(c.values.nbytes for c in again.columns)
    print(f"\n.dm 4,2 МБ, {ROWS} записей × 9 полей: {seconds:.3f} с, пик памяти {peak / 2**20:.1f} МБ")
    assert seconds < 2.0 and peak < result + (12 << 20)


def test_dm_triangles_without_points_say_which_file_is_needed(triangles: Path) -> None:
    assert detect_object(triangles).key == "datamine" and find_pair(triangles) is None
    for inspect in (True, False):
        err = fails(lambda inspect=inspect: list(read_objects(triangles, inspect=inspect).meshes or []))
        assert err.code == "pair_missing" and "optimizedpits_2tr.dm" in err.message
        assert "таблица точек" in err.message and "optimizedpits_2pt.dm" in err.hint
    groups, rest = group_files([triangles.name])
    assert [(g.main, g.sidecars) for g in groups] == [(triangles.name, {})] and rest == []


def test_dm_triangles_of_studio_with_a_points_table(triangles: Path, tmp_path: Path) -> None:
    """The real triangles and a points table made here (the owner's one is not sent yet): the pair is found by
    the name, the numbers of points are matched, the fields of Studio are attributes or divide the file."""
    tr = tmp_path / triangles.name
    shutil.copyfile(triangles, tr)
    pid = np.arange(1, 30_657, dtype=np.float64)
    xyz = np.column_stack([466_000.0 + 0.5 * pid, 4_585_000.0 + 3.0 * (pid % 97), 100.0 + pid % 7])
    points = tmp_path / "optimizedpits_2pt.dm"
    points.write_bytes(build_numbers([F("PID"), F("XP"), F("YP"), F("ZP")], np.column_stack([pid, xyz])))
    assert find_pair(tr) == points
    expected = pages_of(tr)
    with open_dm_wireframes(tr) as f:
        (entry,) = f.entries
        assert (entry.name, entry.triangles, entry.points) == ("optimizedpits_2", ROWS, 30_656)
        assert entry.attrs == [{"name": "Pit", "value": 73.0}]  # the only field with one value in the file
        assert any("Elevatio" in w and "InPitLim" in w for w in entry.warnings)
        mesh = f.read(entry)
    assert (mesh.triangle_count, mesh.point_count) == (ROWS, 30_656)
    corners = expected[:, :3].astype(np.int64) - 1
    assert (bits(mesh.xyz[mesh.tri]) == bits(xyz[corners])).all()  # every corner is the point of its number
    with open_dm_wireframes(tr, split_by="Elevatio") as f:
        levels = [(e.name, e.triangles) for e in f.entries]
    values, counts = np.unique(expected[:, 4], return_counts=True)
    assert sorted(levels) == sorted((f"Elevatio {int(v)}", int(c)) for v, c in zip(values, counts, strict=True))
    assert len(levels) == 40 and sum(c or 0 for _, c in levels) == ROWS


# ---------------------------------------------------------------- Datamine .dmx: told, not read


def test_dmx_is_told_for_what_it_is(dmx: dict[str, Path], tmp_path: Path) -> None:
    assert sorted(dmx) == ["FXDRIVES_pt.dmx", "FXDRIVES_tr.dmx"]
    for name, path in dmx.items():
        assert is_dmx(path) and path.read_bytes()[-25:-21] == DMX_MAGIC
        for err in (fails(detect_object, path), fails(read_objects, path, inspect=True), fails(read_header, path)):
            assert err.code == "unsupported" and name in err.message and "DMX" in err.message
            assert ".dm" in err.hint and "…tr.dm" in err.hint and "…pt.dm" in err.hint
    # under the name of a `.dm` the content tells; a stored upload is told by its original name
    renamed = tmp_path / "FXDRIVES_tr.dm"
    shutil.copyfile(dmx["FXDRIVES_tr.dmx"], renamed)
    for err in (fails(detect_object, renamed), fails(read_dm, renamed)):
        assert err.code == "unsupported" and "FXDRIVES_tr.dm" in err.message and "DMX" in err.message
    assert "каркас.dmx" in fails(detect_object, tmp_path / "upload-7", filename="каркас.dmx").message
    groups, rest = group_files(sorted(dmx))
    assert groups == [] and rest == sorted(dmx)  # nothing to load: each file gets the message above


def test_dmx_footer_alone_does_not_make_a_table(tmp_path: Path) -> None:
    """Synthetic: the 25 bytes a `.dmx` ends with — «DMX», 1, the place and the sizes of its directory, a
    checksum, a flag."""
    foot = DMX_MAGIC + struct.pack("<QIIIB", 64, 10, 20, 0x1234, 1)
    path = tmp_path / "table.dm"
    path.write_bytes(bytes(64) + b"0123456789" + foot)
    assert is_dmx(path) and fails(read_header, path).code == "unsupported"
    path.write_bytes(bytes(64) + b"0123456789" + foot + b"\x00")  # the mark is not at its place
    assert not is_dmx(path) and fails(read_header, path).code == "not_datamine"
    path.write_bytes(DMX_MAGIC)  # shorter than a footer
    assert not is_dmx(path) and fails(read_header, path).code == "not_datamine"
    assert not is_dmx(tmp_path / "нет такого.dmx")


# ---------------------------------------------------------------- Surpac strings: text and binary


def test_surpac_text_sample(surpac: dict[str, Path], tmp_path: Path) -> None:
    path = surpac["text"]
    assert is_surpac(path.read_bytes()) and not is_surpac_binary(path.read_bytes())
    assert detect_object(path).key == "surpac_str"
    read = read_surpac(path)
    assert (read.format, read.encoding, read.records, read.warnings) == ("surpac_str", "cp1251", 9, [])
    (layer,) = read.layers
    assert (layer.name, layer.line_count, layer.point_count) == ("Стринг 1", 1, 9)
    assert layer.closed is not None and layer.closed.tolist() == [0]  # the last vertex is not the first one
    expected = np.array([(x, y, 0.0) for y, x in SURPAC_YX])
    assert (bits(layer.xyz) == bits(expected)).all()
    assert read.header.title == "surpac_string_текстовый,18-Nov-26,,SSI_STYLES:styles.ssi"
    assert read.header.exact and read.header.descriptions == 0
    back = write_surpac(tmp_path / "back.str", read.layers, like=read.header)
    assert back.path.read_bytes() == path.read_bytes()  # written back byte for byte
    assert [layer.name for layer in read_objects(path).layers] == ["Стринг 1"]


def test_surpac_binary_sample_is_told(surpac: dict[str, Path]) -> None:
    path = surpac["binary"]
    data = path.read_bytes()
    assert is_surpac_binary(data) and not is_surpac(data)
    for err in (fails(detect_object, path), fails(read_objects, path)):
        assert err.code == "unsupported" and "двоичные стринги Surpac" in err.message and path.name in err.message
        assert "текстовый" in err.hint
    err = fails(read_surpac, path)
    assert err.code == "unsupported" and "Двоичный" in err.message and "текстовый" in err.hint
    # what the file holds, by the layout seen in it (not read by the program): two text lines, five zero bytes,
    # then records «number (int32), Y, X, Z (float64), description ending with a zero byte», big-endian
    body = data[data.index(b"\n", data.index(b"\n") + 1) + 1 :]
    assert body[:5] == bytes(5) and body.endswith(b"END\x00") and len(body) == 5 + 7 * 29 + 29 + 32
    numbers = [struct.unpack_from(">i", body, 5 + 29 * k)[0] for k in range(8)]
    assert numbers == [1] * 7 + [0]
    first = struct.unpack_from(">3d", body, 9)
    assert first == pytest.approx((0.2599435550, -1.9722055197, 0.0), abs=5e-11)


def test_surpac_binary_is_told_from_other_binary_files(tmp_path: Path) -> None:
    """Synthetic: the binary layout needs the two text lines of a string file before its records."""
    head = b"pit,18-Nov-26,,\r\n0,           0.000,           0.000,           0.000\r\n"
    record = struct.pack(">i3d", 1, 4585061.988, 466570.123, 100.0) + b"\x00"
    end = struct.pack(">i3d", 0, 0.0, 0.0, 0.0) + b"END\x00"
    assert is_surpac_binary(head + bytes(5) + record * 3 + end)
    assert not is_surpac_binary(bytes(range(256)) * 8) and not is_surpac_binary(b"")
    assert not is_surpac_binary(head + b"1, 4585061.988, 466570.123, 100.000,\r\n0, 0.000, 0.000, 0.000, END\r\n")
    assert not is_surpac_binary(b"pit,,,\r\n1, 2, 3, 4\r\n" + bytes(40))  # line 2 is not an axis record
    path = tmp_path / "bin.str"
    path.write_bytes(head + bytes(5) + record * 3 + end)
    assert "двоичные стринги Surpac" in fails(detect_object, path).message
    path.write_bytes(bytes(range(256)) * 8)
    assert "не похож" in fails(detect_object, path).message
