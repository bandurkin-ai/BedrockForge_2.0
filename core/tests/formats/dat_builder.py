"""Helpers that build small synthetic Micromine DAT files for tests (independent of the writer)."""

from __future__ import annotations

import struct

HEADER_XML = (
    '<?xml version="1.0" encoding="utf-8"?><metadata version="{version}">'
    '<meta-object type="title" version="1.0" compulsory="true"><data title="{title}" description=""/></meta-object>'
    '<meta-object type="encoding" version="1.0" compulsory="true">'
    '<data codepage="{codepage}"/></meta-object>{extra}</metadata>'
)
MAGIC_LINE = b"THIS IS MICROMINE EXTENDED DATA FILE!\x07\x1a\x01\r\n"
_PACK = {"R": "<d", "F": "<f", "L": "<i", "S": "<h", "B": "<B"}


def header(codepage: str | None, pages: int = 1, title: str = "", version: str = "1.0") -> bytes:
    xml = HEADER_XML.format(codepage=codepage, title=title, version=version, extra="")
    if codepage is None:
        xml = xml.replace('<data codepage="None"/>', "<data/>")
    if pages > 1:  # block model exports: a long XML that pushes the code page beyond the first page
        filler = (
            '<meta-object type="field-class" version="1.0"><field name="F"/><data field-class="Generic"/></meta-object>'
        )
        extra = filler * (4096 * (pages - 1) // len(filler) + 1)
        xml = xml.replace('<meta-object type="encoding"', extra + '<meta-object type="encoding"')
    return (MAGIC_LINE + xml.encode("utf-8")).ljust(4096 * pages, b"\x00")


def encode_bocu(text: str) -> bytes:
    """ASCII text as BOCU-1 writes it in a fresh field (checked against ICU): printable
    characters are byte + 0x50, space and controls are themselves."""
    return bytes(ord(c) + 0x50 if 0x20 < ord(c) < 0x80 else ord(c) for c in text)


def build(
    codepage: str | None,
    variables: list[tuple],  # type: ignore[type-arg]
    records: list[list],  # type: ignore[type-arg]
    *,
    pages: int = 1,
    standard: bool = False,
    title: str = "",
    version: str = "1.0",
) -> bytes:
    """variables: (raw_name, kind, width, decimals[, raw description]); records: values per field
    (C and N fields as text or bytes)."""
    out = bytearray(b"" if standard else header(codepage, pages, title, version))
    out += f"{len(variables):<4}VARIABLES\r\n".encode("ascii")
    for raw_name, kind, width, dec, *rest in variables:
        glue = b"" if len(raw_name) >= 10 else b" " * (10 - len(raw_name))
        out += raw_name + glue + f"{kind} {width:>2} {dec:>2}".encode("ascii")
        if rest:
            out += b"|" + rest[0]
        out += b"\r\n"
    for rec in records:
        for value, (_, kind, width, *_rest) in zip(rec, variables, strict=True):
            if kind in _PACK:
                out += struct.pack(_PACK[kind], value)
            else:
                encoded = value if isinstance(value, bytes) else value.encode("ascii")
                out += encoded.ljust(width, b" ")[:width]
        out += b"\r\n"
    return bytes(out)
