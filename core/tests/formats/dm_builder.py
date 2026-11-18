"""Builds synthetic Datamine tables (`.dm`, `.fbm`) byte by byte for tests — independent of the reader.

The layout follows the owner's analysis of the block model files (`tests/data/real/blockmodels-dm-fbm-dat.md` §1–2,
ADR 0009): pages of 512 words (a word is 8 bytes — extended precision, or 4 — single), page 1 holds the names of
the file, 28 header words and a 7-word descriptor per field word; data pages hold whole records from their start.
`build` takes any fields record by record; `build_numbers` gives the same bytes for number fields from an array.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

WORDS = 512
ABSENT = -1.0e30  # the "absent data" number of Datamine
TRACE = 1.0e-30  # "TR": a trace


@dataclass(frozen=True)
class F:
    """A field: a number, or a text of `size` characters (a multiple of 4). `implicit` — not stored in records:
    `default` is its value for every record."""

    name: str
    kind: str = "N"
    size: int = 4
    default: float | str = 0.0
    implicit: bool = False


def _text(value: str, chars: int, word: int, encoding: str) -> bytes:
    """`chars` characters, four in a word; a word of 8 bytes is padded with spaces."""
    raw = value.encode(encoding).ljust(chars)[:chars]
    pad = b" " * (word - 4)
    return b"".join(raw[i : i + 4] + pad for i in range(0, chars, 4))


def _number(value: float, word: int) -> bytes:
    return struct.pack("<d" if word == 8 else "<f", value)


def build(
    fields: list[F],
    records: list[list[float | str]],  # values of the stored fields, in their order
    *,
    word: int = 8,
    layout: str = "dm",
    capacity: int = WORDS,  # words of a data page that hold records (512, or 508 with 4 spare words)
    header: str = "stream",  # more than 69 descriptors: "stream" — they run on, "whole" — none is split by a page
    name: str = "TEST",
    owner: str = "SYSTEM",
    description: str = "SYNTHETIC FILE",
    encoding: str = "cp1251",
    slack: bytes = b"\x00",
    swn_shift: int = 0,  # breaks the stored word numbers (negative tests)
) -> bytes:
    page = WORDS * word
    descriptors: list[bytes] = []
    stored = 0  # words of a record so far
    layout_of: list[tuple[F, int]] = []  # stored fields with their first word
    for f in fields:
        pieces = 1 if f.kind == "N" else f.size // 4
        if not f.implicit:
            layout_of.append((f, stored))
        for k in range(pieces):
            swn = 0 if f.implicit else stored + k + 1 + swn_shift
            if f.kind == "N":
                default = _number(float(f.default), word)
            else:
                default = _text(str(f.default)[4 * k : 4 * k + 4], 4, word, encoding)
            descriptors.append(
                _text(f.name, 8, word, encoding)
                + _text(f.kind, 4, word, encoding)
                + _number(float(swn), word)
                + _number(float(k + 1), word)
                + _number(0.0, word)
                + default
            )
        if not f.implicit:
            stored += pieces
    per_page = capacity // stored if stored else 0
    data_pages = -(-len(records) // per_page) if records else 0
    last_records = len(records) - (data_pages - 1) * per_page if records else 0

    if header == "stream":
        tail = b"".join(descriptors)
        header_pages = -(-(28 * word + len(tail)) // page)
    else:
        first, rest = descriptors[:69], descriptors[69:]
        tail = b"".join(first).ljust((WORDS - 28) * word, b"\x00")
        for a in range(0, len(rest), 73):
            tail += b"".join(rest[a : a + 73]).ljust(page, b"\x00")
        header_pages = 1 + -(-len(rest) // 73)
    last_page = header_pages + data_pages

    head = _text(name, 8, word, encoding) + _text("", 8, word, encoding) + _text(description, 64, word, encoding)
    head += _text(owner, 8, word, encoding)
    numbers = [float(len(descriptors)), float(last_page), float(last_records)]
    if layout == "dm":
        head += b"".join(_number(v, word) for v in (0.0, 2.0, 456789.0, *numbers))
    else:  # .fbm: the marker 42, no date word, two float32 (42, 1) in word 27
        head += b"".join(_number(v, word) for v in (42.0, 2.0, *numbers))
        head += struct.pack("<ff", 42.0, 1.0) if word == 8 else struct.pack("<f", 42.0)
    assert len(head) == 28 * word
    out = bytearray((head + tail).ljust(header_pages * page, b"\x00"))

    for a in range(0, len(records), per_page or 1):
        body = bytearray()
        for row in records[a : a + per_page]:
            assert len(row) == len(layout_of)
            for (f, _first), value in zip(layout_of, row, strict=True):
                if f.kind == "N":
                    body += _number(float(value), word)
                else:
                    body += _text(str(value), f.size, word, encoding)
        out += bytes(body).ljust(page, slack)
    return bytes(out)


def build_numbers(fields: list[F], table: Any, *, word: int = 8, **header: Any) -> bytes:
    """The bytes `build` gives for a table of number fields, made with numpy: for tables of a million records.
    `table` — records × stored fields; the implicit fields of `fields` only get their descriptors."""
    import numpy as np

    data = np.asarray(table, dtype=np.float64)
    stored = [f for f in fields if not f.implicit]
    assert all(f.kind == "N" for f in fields) and data.ndim == 2 and data.shape[1] == len(stored)
    assert header.get("layout", "dm") == "dm" and header.get("capacity", WORDS) == WORDS
    head = bytearray(build(fields, [], word=word, **header))  # the header pages of an empty table
    rows, n = data.shape
    if rows == 0:
        return bytes(head)
    per = WORDS // n
    pages = -(-rows // per)
    block = np.zeros((pages * per, n), dtype=f"<f{word}")
    block[:rows] = data
    body = np.zeros((pages, WORDS), dtype=f"<f{word}")
    body[:, : per * n] = block.reshape(pages, per * n)
    code = "<d" if word == 8 else "<f"
    struct.pack_into(code, head, 26 * word, float(len(head) // (WORDS * word) + pages))  # the last page
    struct.pack_into(code, head, 27 * word, float(rows - (pages - 1) * per))  # the records in it
    return bytes(head) + body.tobytes()
