"""A pack — one blob with the files of several small line layers (ADR 0011 §1; debt of 7.30).

A file of thousands of small layers (a STR with a STRING code per line) used to cost five blobs a layer: a
temporary file, an fsync, a move and four rows each. The layers of one portion of an import that are small
(`lines/store.py::PACK_LIMIT`) now go into one blob; a layer refers to it by the role `pack:<n>` — n is the
number of its entry. A big layer keeps its own blobs (`xyz`, `offsets`, …), so nothing changes for it.

Layout (little-endian):

    b"BFLP"  u32 L  header (L bytes of UTF-8 JSON)  zeros up to a multiple of 8  files…

The header is `{"format": 1, "entries": [{role: [offset, size], …}, …]}`: `offset` is from the start of the
data (the first multiple of 8 after the header) and itself a multiple of 8, `size` is in bytes. The files of an
entry are what the separate blobs of a layer would hold, byte for byte: raw arrays `xyz <f8`, `offsets <i8`,
`closed u1`, `seq <u4` and the Parquet tables `attrs`, `vattrs`. Between the files are zeros.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path

MAGIC = b"BFLP"
FORMAT = 1
PREFIX = "pack:"
MAX_HEADER = 16 << 20  # a reader refuses a longer header (a damaged file)

Slices = dict[str, tuple[int, int]]  # role → (offset from the start of the file, size in bytes)


class PackError(ValueError):
    """The file is not a pack or is damaged."""


def _align(n: int) -> int:
    return (n + 7) & ~7


def role(entry: int) -> str:
    return f"{PREFIX}{entry}"


def entry_of(blob_role: str) -> int | None:
    """The entry number of a `pack:<n>` role; None — the role is not one of a pack."""
    rest = blob_role[len(PREFIX) :] if blob_role.startswith(PREFIX) else ""
    return int(rest) if rest.isascii() and rest.isdigit() else None


def encode(entries: Sequence[Mapping[str, bytes | bytearray | memoryview]]) -> bytes:
    """The pack of `entries`: every entry is the files of one layer, role → bytes."""
    table: list[dict[str, list[int]]] = []
    at = 0
    for entry in entries:
        row: dict[str, list[int]] = {}
        for name, data in entry.items():
            row[name] = [at, len(data)]
            at = _align(at + len(data))
        table.append(row)
    head = json.dumps({"format": FORMAT, "entries": table}, separators=(",", ":")).encode("utf-8")
    start = _align(8 + len(head))
    out = bytearray(start + at)
    out[0:4] = MAGIC
    out[4:8] = struct.pack("<I", len(head))
    out[8 : 8 + len(head)] = head
    for entry, row in zip(entries, table, strict=True):
        for name, data in entry.items():
            offset = start + row[name][0]
            out[offset : offset + len(data)] = data
    return bytes(out)


@lru_cache(maxsize=256)
def _directory(path: str) -> tuple[Slices, ...]:
    with open(path, "rb") as fh:
        head = fh.read(8)
        if len(head) < 8 or head[:4] != MAGIC:
            raise PackError("no magic")
        (length,) = struct.unpack("<I", head[4:8])
        if length > MAX_HEADER:
            raise PackError("header too long")
        raw = fh.read(length)
        fh.seek(0, 2)
        total = fh.tell()
    if len(raw) != length:
        raise PackError("header cut")
    try:
        header = json.loads(raw.decode("utf-8"))
        if header["format"] != FORMAT:
            raise PackError(f"unknown format {header['format']!r}")
        start = _align(8 + length)
        out: list[Slices] = []
        for entry in header["entries"]:
            slices: Slices = {}
            for name, (offset, size) in entry.items():
                offset, size = int(offset), int(size)
                if offset < 0 or size < 0 or offset % 8 or start + offset + size > total:
                    raise PackError(f"{name!r} is out of the file")
                slices[str(name)] = (start + offset, size)
            out.append(slices)
    except PackError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise PackError(f"bad header: {exc}") from exc
    return tuple(out)


def directory(path: str | Path) -> tuple[Slices, ...]:
    """Where the files of every entry are. A blob never changes, so the answer is kept by its path."""
    return _directory(str(path))


def forget() -> None:
    _directory.cache_clear()
