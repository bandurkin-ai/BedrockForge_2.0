"""Native reader for Micromine extended data files (.DAT).

File layout (verified on Collar, Survey and Assay files of a real project):

* bytes 0..4095     XML metadata header; ``codepage`` attribute gives text encoding
* from byte 4096    ``N VARIABLES`` block: N lines ``NAME TYPE WIDTH DECIMALS``
* then              fixed-width records, each followed by CRLF
                    TYPE ``C`` = text padded with spaces, TYPE ``R`` = IEEE double, little-endian

Known quirk: in files whose header declares ``BOCU-1`` the text bytes (and field
names) are stored as ASCII + 0x50. Decoding subtracts 0x50 from bytes >= 0x50.
Bytes below 0x50 (spaces, control bytes) are kept as-is.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

MAGIC = b"THIS IS MICROMINE EXTENDED DATA FILE"
HEADER_SIZE = 4096
VARIABLES_TAG = b"VARIABLES\r\n"
RECORD_TRAILER = b"\r\n"
_CODEPAGE_RE = re.compile(rb'codepage="([^"]*)"')


class MicromineFormatError(ValueError):
    """Raised when a file is not a readable Micromine extended data file."""


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # "C" (text) or "R" (real)
    width: int
    decimals: int


@dataclass
class MicromineTable:
    path: str
    codepage: str
    fields: list[Field]
    frame: pd.DataFrame

    @property
    def record_count(self) -> int:
        return len(self.frame)


def _decode_text(raw: bytes, codepage: str) -> str:
    if codepage.upper() == "BOCU-1":
        raw = bytes(c - 0x50 if c >= 0x50 else c for c in raw)
    # TODO: non-ASCII text (ibm-5347 / Cyrillic) is not used by the sample files yet.
    return raw.decode("ascii", errors="replace").rstrip()


def _parse_header(data: bytes) -> tuple[str, list[Field], int]:
    """Return (codepage, fields, offset of the first record)."""
    if not data.startswith(MAGIC):
        raise MicromineFormatError("file is not a Micromine extended data file")

    codepage_match = _CODEPAGE_RE.search(data[:HEADER_SIZE])
    codepage = codepage_match.group(1).decode("ascii") if codepage_match else "ascii"

    vars_pos = data.find(VARIABLES_TAG, HEADER_SIZE)
    if vars_pos < 0:
        raise MicromineFormatError("VARIABLES block not found")

    count_token = data[HEADER_SIZE:vars_pos].split()
    if not count_token or not count_token[0].isdigit():
        raise MicromineFormatError("field count not found before VARIABLES")
    count = int(count_token[0])

    pos = vars_pos + len(VARIABLES_TAG)
    fields: list[Field] = []
    for _ in range(count):
        end = data.find(b"\r\n", pos)
        if end < 0:
            raise MicromineFormatError("truncated VARIABLES block")
        line = data[pos:end]
        pos = end + 2

        parts = line.split()
        if len(parts) < 4:
            raise MicromineFormatError(f"bad variable line: {line!r}")
        kind_token, width_token, decimals_token = parts[-3], parts[-2], parts[-1]
        if kind_token not in (b"C", b"R"):
            raise MicromineFormatError(f"unknown field type: {kind_token!r}")
        name_raw = line[: line.rfind(kind_token)].rstrip()
        fields.append(
            Field(
                name=_decode_text(name_raw, codepage),
                kind=kind_token.decode("ascii"),
                width=int(width_token),
                decimals=int(decimals_token),
            )
        )

    # Records are not flagged in the header: they start right after the last
    # variable line (the 'pos' value after the loop).
    records_start = pos
    return codepage, fields, records_start


def _unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for name in names:
        if name in seen:
            seen[name] += 1
            result.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 1
            result.append(name)
    return result


def read_micromine(path: str | Path) -> MicromineTable:
    """Read a Micromine extended DAT file into a pandas DataFrame.

    Text fields are decoded according to the file's codepage, real fields are
    returned as float64. Missing numeric values stay NaN.
    """
    path = Path(path)
    data = path.read_bytes()
    codepage, fields, start = _parse_header(data)

    record_len = sum(f.width for f in fields) + len(RECORD_TRAILER)
    body_len = len(data) - start
    if body_len % record_len != 0:
        raise MicromineFormatError(
            f"data length {body_len} is not a multiple of record length {record_len}"
        )

    fmt = "<" + "".join(
        f"{f.width}s" if f.kind == "C" else "d" for f in fields
    )
    # Real fields are 8 bytes wide by definition; enforce it before unpacking.
    for f in fields:
        if f.kind == "R" and f.width != 8:
            raise MicromineFormatError(f"real field {f.name} must be 8 bytes, got {f.width}")
    fmt += f"{len(RECORD_TRAILER)}x"
    if struct.calcsize(fmt) != record_len:
        raise MicromineFormatError("internal record size mismatch")

    rows = []
    for raw_row in struct.iter_unpack(fmt, data[start:]):
        row = []
        for value, f in zip(raw_row, fields):
            if f.kind == "C":
                row.append(_decode_text(value, codepage))
            else:
                row.append(value)
        rows.append(row)

    columns = _unique_names([f.name for f in fields])
    frame = pd.DataFrame(rows, columns=columns)
    for f, col in zip(fields, columns):
        if f.kind == "R":
            frame[col] = frame[col].astype("float64")

    return MicromineTable(path=str(path), codepage=codepage, fields=fields, frame=frame)
