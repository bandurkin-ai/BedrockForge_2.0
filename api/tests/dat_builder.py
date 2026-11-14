"""Helpers that build small synthetic Micromine DAT files for tests."""

import struct

HEADER_XML = (
    '<?xml version="1.0" encoding="utf-8"?><metadata version="1.0">'
    '<meta-object type="encoding" version="1.0" compulsory="true">'
    '<data codepage="{codepage}"/></meta-object></metadata>'
)


def header(codepage: str) -> bytes:
    magic = b"THIS IS MICROMINE EXTENDED DATA FILE!\x07\x1a\x01\r\n"
    xml = HEADER_XML.format(codepage=codepage).encode("utf-8")
    return (magic + xml).ljust(4096, b"\x00")


def encode_bocu(text: str) -> bytes:
    """Encode text the way BOCU-1-declared Micromine files store it (ASCII + 0x50)."""
    return bytes(ord(c) + 0x50 if ord(c) >= 0x50 else ord(c) for c in text)


def build(codepage: str, variables: list[tuple[bytes, str, int, int]], records: list[list]) -> bytes:
    """variables: (raw_name, kind, width, decimals); records: values per field."""
    out = bytearray(header(codepage))
    out += f"{len(variables)} VARIABLES\r\n".encode("ascii")
    for raw_name, kind, width, dec in variables:
        out += raw_name + f" {kind} {width:>2} {dec:>2}".encode("ascii") + b"\r\n"
    for rec in records:
        for value, (_, kind, width, _) in zip(rec, variables):
            if kind == "C":
                encoded = value if isinstance(value, bytes) else value.encode("ascii")
                out += encoded.ljust(width, b" ")[:width]
            else:
                out += struct.pack("<d", value)
        out += b"\r\n"
    return bytes(out)
