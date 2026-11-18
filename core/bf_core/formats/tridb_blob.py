"""The geometry BLOB of a Micromine TRIDB (ТЗ 9.4; ADR 0013 §2): its layout, reading in portions, the bytes
a writer starts it with.

Little-endian: `FF FF FF FF`, version `00 00 05 00`, int64 length of the GUID (36), int64 9, the GUID in ASCII,
then blocks «int64 length of the data, int64 number, data»: 1, 2, 3 — X, Y, Z of the vertices as doubles, 4 —
the triangles as int32 triples of vertex numbers from 0. Every real file has exactly these four blocks, in this
order (121 wireframes of `tests/data/real`).

Ported from BF `formats/wireframes.py::read_tridb_geometry` (`_block_table`, `_read_into`). Differences: one
convention of the block length (the one of the real files: without the 16 bytes of the block header), the
version is checked, the head and the block headers a writer needs.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.formats.errors import FormatError

MAGIC = b"\xff\xff\xff\xff"
VERSION = b"\x00\x00\x05\x00"
TAG = 9
HEAD = 24
GUID_BYTES = 36
MAX_GUID = 256
READ = 16 << 20  # bytes of a BLOB read or written at once
HINT = "Откройте файл в Micromine и сохраните каркасы заново или возьмите исправную копию файла."


def fail(code: str, message: str, hint: str = HINT) -> FormatError:
    return FormatError(code, message, hint)


def blob_head(guid: str) -> bytes:
    """The first bytes of a version 5 BLOB: the mark, the version, the length of the GUID, 9, the GUID."""
    raw = guid.encode("ascii")
    return MAGIC + VERSION + struct.pack("<qq", len(raw), TAG) + raw


def block_head(number: int, size: int) -> bytes:
    return struct.pack("<qq", size, number)


def blob_size(points: int, triangles: int, guid_bytes: int = GUID_BYTES) -> int:
    """Bytes of a version 5 BLOB with these counts."""
    return HEAD + guid_bytes + 4 * 16 + 24 * points + 12 * triangles


@dataclass(frozen=True)
class Layout:
    version: bytes
    tag: int
    guid: str | None  # None — not ASCII
    blocks: dict[int, tuple[int, int]]  # number → (offset of the data, bytes)

    @property
    def standard(self) -> bool:
        return self.version == VERSION and self.tag == TAG

    @property
    def points(self) -> int:
        return self.blocks[1][1] // 8

    @property
    def triangles(self) -> int:
        return self.blocks[4][1] // 12


def _at(f: Any, offset: int, size: int) -> bytes:
    f.seek(offset)
    return bytes(f.read(size)) if size else b""


def blob_layout(f: Any, total: int, name: str = "") -> Layout:
    """The block table of a geometry BLOB (`f` — sqlite3.Blob or a binary file of `total` bytes). Nothing is
    allocated by the lengths the BLOB declares: they must fill it exactly."""
    who = f"Каркас «{name}»: " if name else ""
    head = _at(f, 0, HEAD)
    if len(head) < HEAD or head[:4] != MAGIC:
        raise fail("broken", f"{who}неизвестная структура геометрии TRIDB.")
    version = head[4:8]
    guid_len, tag = struct.unpack_from("<qq", head, 8)

    def bad(message: str, code: str = "broken") -> FormatError:
        if version != VERSION or tag != TAG:
            shown = int.from_bytes(version[2:], "little")
            return fail(
                "unsupported",
                f"{who}версия геометрии TRIDB {shown} не поддерживается (проверена версия 5).",
                "Сохраните каркасы в текущей версии Micromine или экспортируйте их в DXF.",
            )
        return fail(code, who + message)

    if not 0 <= guid_len <= MAX_GUID or HEAD + guid_len > total:
        raise bad("неизвестная структура геометрии TRIDB.")
    raw = _at(f, HEAD, guid_len)
    blocks: dict[int, tuple[int, int]] = {}
    pos = HEAD + guid_len
    while pos < total:
        header = _at(f, pos, 16) if pos + 16 <= total else b""
        length, number = struct.unpack("<qq", header) if len(header) == 16 else (-1, 0)
        if length < 0 or pos + 16 + length > total:
            raise bad("геометрия обрывается раньше времени (блоки длиннее самих данных).", "truncated")
        if number in blocks or len(blocks) >= 64:
            raise bad("блоки геометрии повторяются.")
        blocks[number] = (pos + 16, length)
        pos += 16 + length
    sizes = [blocks[k][1] if k in blocks else -1 for k in (1, 2, 3, 4)]
    if min(sizes) < 0 or len(set(sizes[:3])) != 1 or sizes[0] % 8 or sizes[3] % 12:
        raise bad("в геометрии нет блоков вершин и треугольников нужного размера.")
    return Layout(version, tag, raw.decode("ascii") if raw.isascii() else None, blocks)


def quick_counts(f: Any, total: int) -> tuple[int | None, int | None]:
    """(vertices, triangles) by the head of a BLOB and its size, without walking its blocks; None — unknown."""
    head = _at(f, 0, HEAD)
    if len(head) < HEAD or head[:4] != MAGIC:
        raise fail("broken", "Неизвестная структура геометрии TRIDB.")
    guid_len = struct.unpack_from("<q", head, 8)[0]
    if not 0 <= guid_len <= MAX_GUID or HEAD + guid_len + 16 > total:
        return None, None
    length, number = struct.unpack("<qq", _at(f, HEAD + guid_len, 16))
    rest = total - HEAD - guid_len - 4 * 16 - 3 * length
    if number != 1 or length < 0 or length % 8:
        return None, None
    return length // 8, (rest // 12 if rest >= 0 and rest % 12 == 0 else None)


def fill(f: Any, at: tuple[int, int], out: np.ndarray, step: Any) -> None:
    """The bytes of a block into the 1-D array `out` (any item size, possibly strided), `READ` at a time."""
    offset, size = at
    item = out.dtype.itemsize
    dtype = out.dtype.newbyteorder("<")
    f.seek(offset)
    done = 0
    while done < size:
        want = min(READ - READ % item, size - done)
        chunk = f.read(want)
        if len(chunk) != want:
            raise fail("truncated", "Геометрия TRIDB обрывается раньше времени.")
        out[done // item : (done + want) // item] = np.frombuffer(chunk, dtype=dtype)
        done += want
        step(want)
