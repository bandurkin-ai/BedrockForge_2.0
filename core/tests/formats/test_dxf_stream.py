"""DXF tokenizer: pairs, numbers, records, sections, code pages; hostile files (ТЗ 9.5, 9.12; ADR 0011 §2)."""

from __future__ import annotations

import random
import struct
from pathlib import Path

import numpy as np
import pytest

from bf_core.formats import dxf_stream as ds
from bf_core.formats.dxf_stream import Block, DxfStream, codec_of
from bf_core.formats.errors import FormatError

from .dxf_builder import drawing, tags

REAL = Path(__file__).resolve().parents[3] / "tests" / "data" / "real" / "TST-FAULTS.DXF"


def _write(tmp_path: Path, content: str | bytes, name: str = "a.dxf", encoding: str = "cp1251") -> Path:
    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode(encoding))
    return path


def _reference(data: bytes) -> list[tuple[int, bytes]]:
    """The pairs of a file the plain way: lines two by two up to the EOF record."""
    lines = data.split(b"\n")
    out: list[tuple[int, bytes]] = []
    for i in range(0, len(lines) - 1, 2):
        code, value = int(lines[i]), lines[i + 1].rstrip(b"\r")
        out.append((code, value))
        if code == 0 and value.strip() == b"EOF":
            break
    return out


def _pairs(path: Path, **options: int) -> tuple[list[tuple[int, bytes]], list[Block]]:
    blocks = list(DxfStream(path, **options))
    found = [(int(c), b.value(i)) for b in blocks for i, c in enumerate(b.code.tolist())]
    return found, blocks


def _sample(eol: str = "\r\n") -> str:
    entities = "".join(
        tags(0, "LINE", 5, f"{i:X}", 8, "Слой 1" if i % 3 else "0", 62, i % 7, 10, i * 1.25, 20, -i / 3, 30, 1e-5 * i,
             11, 4.584699697000001e6, 21, 0.1 + 0.2, 31, 1e300, eol=eol)
        for i in range(40)
    )  # fmt: skip
    return drawing(entities + tags(0, "POINT", 8, "P", 10, " 12.5 ", 20, "1E+2", 30, "-.5", eol=eol), eol=eol)


@pytest.mark.parametrize("eol", ["\r\n", "\n"])
@pytest.mark.parametrize("compiled", [False, True])
@pytest.mark.parametrize("block", [64, 300, 4096, ds.BLOCK_BYTES])
def test_pairs_match_the_plain_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, eol: str, compiled: bool, block: int
) -> None:
    monkeypatch.setattr(ds, "SMALL_BYTES", 0 if compiled else 1 << 30)
    data = _sample(eol).encode("cp1251")
    found, blocks = _pairs(_write(tmp_path, data), block_bytes=block)
    expected = _reference(data)
    # text values are kept as they are; names of records and numbers are stripped
    assert [(c, v.strip() if c == 0 or 10 <= c <= 99 else v) for c, v in expected] == found
    for b in blocks:
        assert b.code[0] == 0 and (b.code[b.z] == 0).all() and len(b.z) == int((b.code == 0).sum())
    assert sum(len(b) for b in blocks) == sum(1 for c, _ in expected if c == 0)
    if block == 64:
        assert len(blocks) > 20  # really cut into blocks, each of whole records


def test_blocks_do_not_change_the_columns(tmp_path: Path) -> None:
    path = _write(tmp_path, _sample())
    whole = list(DxfStream(path))
    parts = list(DxfStream(path, block_bytes=128))
    assert len(whole) == 1 and len(parts) > 10
    for name in ("code", "num", "bad", "kind", "section"):
        joined = np.concatenate([getattr(b, name) for b in parts])
        assert np.array_equal(joined, getattr(whole[0], name), equal_nan=True), name
    assert [b.first for b in parts] == np.cumsum([0] + [len(b) for b in parts])[:-1].tolist()
    assert parts[3].line(0) == 2 * parts[3].pair0 + 2


@pytest.mark.parametrize("compiled", [False, True])
def test_numbers_are_the_same_doubles_as_float(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compiled: bool) -> None:
    monkeypatch.setattr(ds, "SMALL_BYTES", 0 if compiled else 1 << 30)
    rnd = random.Random(7)
    values = [rnd.uniform(-1e7, 1e7) for _ in range(300)]
    values += [struct.unpack("<d", struct.pack("<Q", rnd.getrandbits(64)))[0] for _ in range(300)]
    values = [v for v in values if v == v and abs(v) != float("inf")]
    texts = [repr(v) for v in values]
    texts += ["5e-324", "1.7976931348623157e308", "0.1", "-0.0", "9007199254740993", "1.", ".5", "+2.5E-3", "007"]
    texts += ["466570.58600000001", "1.000000000000000E+20", "123456789012345678901234567890", "1e400"]
    body = "".join(tags(0, "POINT", 10, t, 20, f"  {t}\t", 30, 0.0) for t in texts)
    blocks = list(DxfStream(_write(tmp_path, drawing(body)), block_bytes=512))
    x = np.concatenate([b.num[b.pairs(10, b.kind == ds.K_POINT)] for b in blocks])
    y = np.concatenate([b.num[b.pairs(20, b.kind == ds.K_POINT)] for b in blocks])
    expected = np.array([float(t) for t in texts])
    assert x.tobytes() == expected.tobytes() and y.tobytes() == expected.tobytes()  # bit for bit, −0.0 included


def test_values_that_are_not_numbers(tmp_path: Path) -> None:
    body = tags(0, "POINT", 10, "1.#QNAN", 20, "abc", 30, "", 0, "POINT", 10, "nan", 20, "1e", 30, "1.5.2")
    (b,) = DxfStream(_write(tmp_path, drawing(body)))
    p = np.flatnonzero((b.code >= 10) & (b.code <= 30) & (b.section[b.rec] == ds.S_ENTITIES))
    assert len(p) == 6 and b.bad[p].all() and np.isnan(b.num[p]).all()
    assert not b.bad[b.code == 0].any() and np.isnan(b.values(np.array([-1]))).all()
    assert b.values(np.array([-1, p[0]]), 7.0).tolist()[0] == 7.0


def test_records_sections_and_columns(tmp_path: Path) -> None:
    blocks_section = tags(0, "BLOCK", 2, "B1", 0, "LINE", 8, "in block", 10, 1.0, 20, 2.0, 0, "ENDBLK")
    body = tags(0, "LINE", 8, "A", 10, 1.0, 20, 2.0, 11, 3.0, 21, 4.0, 0, "CIRCLE", 10, 5.0, 40, 2.0,
                0, "MTEXT", 8, "T", 10, 9.0, 1, "text", 101, "Embedded Object", 10, 77.0, 0, "HATCH", 8,
                "A")  # fmt: skip
    stream = DxfStream(_write(tmp_path, drawing(body, layers=[("A", 1), ("T", 3)], blocks=blocks_section)))
    (b,) = stream
    names = [ds.KINDS[k] if k >= 0 else b.value(int(p)).decode() for k, p in zip(b.kind.tolist(), b.z, strict=True)]
    assert names == ["SECTION", "ENDSEC", "SECTION", "TABLE", "LAYER", "LAYER", "ENDTAB", "ENDSEC", "SECTION", "BLOCK",
                     "LINE", "ENDBLK", "ENDSEC", "SECTION", "LINE", "CIRCLE", "MTEXT", "HATCH", "ENDSEC",
                     "EOF"]  # fmt: skip
    s = ds
    assert b.section.tolist() == [s.S_HEADER, 0, s.S_TABLES, s.S_TABLES, s.S_TABLES, s.S_TABLES, s.S_TABLES, 0,
                                  s.S_BLOCKS, s.S_BLOCKS, s.S_BLOCKS, s.S_BLOCKS, 0,
                                  s.S_ENTITIES, s.S_ENTITIES, s.S_ENTITIES, s.S_ENTITIES, s.S_ENTITIES, 0,
                                  0]  # fmt: skip
    x = b.values(b.col(10))
    assert x[14] == 1.0 and x[15] == 5.0 and x[10] == 1.0 and np.isnan(x[17])
    assert x[16] == 9.0  # the group after "Embedded Object" is not the entity's own
    table: dict[bytes, int] = {}
    ids = b.intern(b.col(8)[[14, 15, 16, 17]], table, b"0")
    assert ids.tolist() == [0, 2, 1, 0] and table == {b"A": 0, b"T": 1, b"0": 2}
    assert (stream.version, stream.codepage, stream.encoding, stream.records) == ("AC1018", "ANSI_1251", "cp1251", 20)
    assert stream.ended and stream.text("Слой".encode("cp1251")) == "Слой"


def test_comments_before_the_first_section_and_a_tail_after_eof(tmp_path: Path) -> None:
    text = tags(999, "made by a test", 999, "second") + drawing(tags(0, "POINT", 10, 1.0, 20, 2.0))
    path = _write(tmp_path, b"\xef\xbb\xbf" + text.encode() + b"\r\n\r\n\x1a garbage after EOF\r\n")
    for size in (40, ds.BLOCK_BYTES):
        blocks = list(DxfStream(path, block_bytes=size))
        assert blocks[0].kind[0] == ds.K_SECTION and blocks[0].pair0 == 2
        assert sum(int((b.kind == ds.K_POINT).sum()) for b in blocks) == 1
    last = drawing(tags(0, "POINT", 10, 1.0, 20, 2.0)).rstrip("\r\n")  # no line end after EOF
    assert sum(len(b) for b in DxfStream(_write(tmp_path, last, "b.dxf"))) == 10


def test_code_pages(tmp_path: Path) -> None:
    assert codec_of("AC1018", "ANSI_1251") == "cp1251" and codec_of("AC1009", "ansi_1252") == "cp1252"
    assert codec_of("AC1021", "ANSI_1251") == "utf-8" and codec_of("AC1032", None) == "utf-8"
    assert codec_of("AC1015", "DOS866") == "cp866" and codec_of(None, "ANSI_936") == "gbk"
    assert codec_of("AC1015", "ANSI_99999") is None and codec_of(None, None) is None and codec_of("AC1015", "") is None
    body = tags(0, "TEXT", 10, 0.0, 20, 0.0, 1, "x")
    known = DxfStream(_write(tmp_path, drawing(body), "k.dxf"))
    list(known)
    assert known.encoding_known and known.text(b"\xd1\xea\xe2 \\U+2116 5") == "Скв № 5"
    assert known.text(b"\x98") == "�" and known.replaced == 1  # 0x98 is not a character of cp1251
    unknown = DxfStream(_write(tmp_path, drawing(body, version=None, codepage=None), "u.dxf"))
    list(unknown)
    assert not unknown.encoding_known and unknown.encoding == "cp1251" and unknown.version is None
    assert unknown.text("Слой".encode()) == "Слой" and unknown.text("Слой".encode("cp1251")) == "Слой"
    utf = DxfStream(_write(tmp_path, drawing(body, version="AC1027"), "n.dxf", "utf-8"))
    list(utf)
    assert utf.encoding == "utf-8" and utf.text("Скв".encode()) == "Скв"


def test_the_real_drawing(tmp_path: Path) -> None:
    data = REAL.read_bytes()
    stream = DxfStream(REAL, block_bytes=1 << 18)
    blocks = list(stream)
    expected = _reference(data)
    assert len(blocks) > 5 and sum(len(b.code) for b in blocks) == len(expected) == stream.pairs
    assert (stream.version, stream.codepage, stream.encoding) == ("AC1018", "ANSI_1251", "cp1251")
    kinds = np.concatenate([b.kind for b in blocks])
    sections = np.concatenate([b.section for b in blocks])
    entities = sections == ds.S_ENTITIES
    assert int(((kinds == ds.K_POLYLINE) & entities).sum()) == 1
    vertex = (kinds == ds.K_VERTEX) & entities
    flags = np.concatenate([b.values(b.col(70)) for b in blocks])[vertex]
    assert int((flags == 192).sum()) == 7444 and int((flags == 128).sum()) == 7417  # vertices and faces of the polyface
    codes = np.concatenate([b.code for b in blocks])
    assert codes.tolist() == [c for c, _ in expected]
    x = np.concatenate([b.num[b.code == 10] for b in blocks])
    assert len(x) > 7444 and x.tolist() == [float(v) for c, v in expected if c == 10]


# --- hostile files ------------------------------------------------------------------------------------


def _error(path: Path, **options: int) -> FormatError:
    with pytest.raises(FormatError) as info:
        list(DxfStream(path, **options))
    error = info.value
    assert error.message and error.hint and set(error.as_dict()) == {"code", "message", "hint"}
    assert any("а" <= ch <= "я" for ch in error.message.lower()) and any("а" <= ch <= "я" for ch in error.hint.lower())
    return error


def test_not_a_dxf(tmp_path: Path) -> None:
    assert _error(_write(tmp_path, b"")).code == "empty_file"
    assert _error(_write(tmp_path, b"\r\n \r\n\r\n")).code == "not_dxf"
    assert _error(_write(tmp_path, b"Hello, world\r\nthis is not a drawing\r\n")).code == "not_dxf"
    assert _error(_write(tmp_path, b"X,Y,Z\r\n1,2,3\r\n")).code == "not_dxf"
    assert _error(_write(tmp_path, tags(0, "LINE", 10, 1.0, 0, "EOF"))).code == "not_dxf"  # no SECTION
    assert _error(_write(tmp_path, tags(5, "AB", 0, "SECTION", 2, "ENTITIES", 0, "ENDSEC", 0, "EOF"))).code == "not_dxf"
    assert _error(_write(tmp_path, "0\r\nSECTION\r\n".encode("utf-16"))).code == "not_dxf"
    assert _error(_write(tmp_path, b"AC1018\x00\x00\x00\x00\x00\x00binary dwg")).code == "unsupported"
    assert "DWG" in _error(_write(tmp_path, b"AC1032\x00\x00\x00")).message
    assert _error(_write(tmp_path, ds.BINARY_MAGIC + b"\x00\x00SECTION\x00")).code == "binary"


@pytest.mark.parametrize("compiled", [False, True])
def test_a_bad_group_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compiled: bool) -> None:
    monkeypatch.setattr(ds, "SMALL_BYTES", 0 if compiled else 1 << 30)
    good = drawing(tags(0, "LINE", 8, "A", 10, 1.0, 20, 2.0, 11, 3.0, 21, 4.0))
    lines = good.split("\r\n")
    at = lines.index("LINE") + 3  # the line of the group code 10
    for bad in ("1O", "ten", "-5", "1.5", "99999", "", "10 20"):
        broken = "\r\n".join(lines[:at] + [bad] + lines[at + 1 :])
        for size in (50, ds.BLOCK_BYTES):
            error = _error(_write(tmp_path, broken), block_bytes=size)
            assert error.code == "broken" and f"Строка {at + 1}:" in error.message.replace("\xa0", " "), (bad, size)
    # an odd number of lines shifts codes and values: the value "LINE" stands where a code is expected
    shifted = "\r\n".join(lines[: at - 1] + lines[at:])
    assert _error(_write(tmp_path, shifted)).code == "broken"


def test_truncated(tmp_path: Path) -> None:
    good = drawing("".join(tags(0, "LINE", 8, "A", 10, float(i), 20, 2.0, 11, 3.0, 21, 4.0) for i in range(50)))
    data = good.encode()
    for cut in (len(data) - 5, len(data) - 12, len(data) // 2, len(data) // 2 + 3, 200):
        for size in (97, ds.BLOCK_BYTES):
            error = _error(_write(tmp_path, data[:cut]), block_bytes=size)
            assert error.code == "truncated" and "обрезан" in error.message, (cut, size)
    whole = list(DxfStream(_write(tmp_path, data), block_bytes=97))
    assert sum(int((b.kind == ds.K_LINE).sum()) for b in whole) == 50


def test_a_record_that_is_too_long(tmp_path: Path) -> None:
    huge = tags(0, "LWPOLYLINE", 8, "A", 90, 5000) + "".join(tags(10, float(i), 20, 0.5) for i in range(5000))
    path = _write(tmp_path, drawing(huge))
    error = _error(path, block_bytes=1024, max_record_bytes=20_000)
    assert error.code == "too_large" and "МБ" in error.message
    (big,) = [b for b in DxfStream(path, block_bytes=1024) if (b.kind == ds.K_LWPOLYLINE).any()]
    assert len(big.pairs(10, big.kind == ds.K_LWPOLYLINE)) == 5000  # the block grows to hold the record


def test_progress_and_cancellation(tmp_path: Path) -> None:
    path = _write(tmp_path, _sample())
    seen: list[float] = []
    list(DxfStream(path, lambda f, m=None: seen.append(f), block_bytes=256))
    assert len(seen) > 10 and seen == sorted(seen) and seen[0] == 0.0 and seen[-1] == 1.0

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, message: str | None = None) -> None:
        if fraction > 0.3:
            raise Cancelled

    with pytest.raises(Cancelled):
        list(DxfStream(path, cancel, block_bytes=256))
