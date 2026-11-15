"""Native reader for Micromine extended data files (.DAT).

File layout (verified on Collar, Survey and Assay files of a real project):

* bytes 0..4095     XML metadata header; ``codepage`` attribute gives text encoding
* from byte 4096    ``N VARIABLES`` block: N lines ``NAME TYPE WIDTH DECIMALS``
* then              fixed-width records, each followed by CRLF
                    TYPE ``C`` = text padded with spaces, TYPE ``R`` = IEEE double, little-endian,
                    TYPE ``N`` = a number written as text (STR files use it for JOIN)

In files whose header declares ``BOCU-1`` field names and the values of C and N fields are
BOCU-1 text, each field encoded on its own (app/formats/bocu1.py); ASCII then reads as
byte + 0x50, Cyrillic as multi-byte sequences. The XML header may be padded to 4096 or 8192
bytes. Other field types (F, L, S, B: float32, int32, int16, byte) are read by size; they have
not been seen in real files yet.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from app.formats import bocu1
from app.formats.tabular import unique_names

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
    kind: str  # "C" (text), "R" (real), "N" (number stored as text); F, L, S, B by size
    width: int
    decimals: int


# Binary field types and their numpy types; C and N are text of WIDTH bytes.
BINARY_TYPES = {"R": "<f8", "F": "<f4", "L": "<i4", "S": "<i2", "B": "u1"}
FIELD_TYPES = ("C", "N", *BINARY_TYPES)
# NAME is 10 wide and the type follows it directly: a 10-character name has no space before it
# ("RUN_AU_CUTN 12 8"), a shorter one is padded with spaces. In BOCU-1 files printable characters of
# the name are bytes >= 0x80, so the type letter (0x41-0x5A) cannot be mistaken for a name byte.
# "NAME  T WIDTH DEC", optionally followed by "|description" (newer Micromine versions write the
# field's description there, e.g. "ЗОЛОТО    C 1 0|Золото, г/т").
_VARIABLE_RE = re.compile(rb"^(.*?)\s*([A-Z])\s+(\d+)\s+(\d+)\s*(?:\|.*)?$", re.DOTALL)


@dataclass
class MicromineTable:
    path: str
    codepage: str
    fields: list[Field]
    frame: pd.DataFrame

    @property
    def record_count(self) -> int:
        return len(self.frame)


# Micromine codepage names -> Python codecs. ibm-5347 is Windows-1251 (Cyrillic).
_CODECS = {
    "ibm-5347": "cp1251", "ibm-1251": "cp1251", "windows-1251": "cp1251", "cp1251": "cp1251",
    "ibm-5348": "cp1252", "windows-1252": "cp1252", "utf-8": "utf-8", "ascii": "ascii",
}


def codec_of(codepage: str) -> str:
    """Python codec for a Micromine codepage name; ICU names carry a suffix: "ibm-5347_P100-1998"."""
    name = codepage.lower().strip()
    return _CODECS.get(name) or _CODECS.get(name.split("_")[0]) or "ascii"


def is_bocu(codepage: str) -> bool:
    return codepage.upper() == "BOCU-1"


def _decode_text(raw: bytes, codepage: str) -> str:
    if is_bocu(codepage):
        return bocu1.decode(raw.rstrip(b" \x00"), errors="replace").rstrip(" \x00")
    return raw.decode(codec_of(codepage), errors="replace").rstrip(" \x00")


def _parse_header(data: bytes) -> tuple[str, list[Field], int]:
    """Return (codepage, fields, offset of the first record)."""
    if not data.startswith(MAGIC):
        raise MicromineFormatError("file is not a Micromine extended data file")

    vars_pos = data.find(VARIABLES_TAG, HEADER_SIZE)
    if vars_pos < 0:
        raise MicromineFormatError("VARIABLES block not found")
    # The XML header may be longer than 4096 bytes (then padded to 8192): look in all of it.
    codepage_match = _CODEPAGE_RE.search(data[:vars_pos])
    codepage = codepage_match.group(1).decode("ascii", "replace") if codepage_match else None

    # "<N>   VARIABLES": the header before it is padded with zero bytes (to 4096 or 8192).
    count_token = data[HEADER_SIZE:vars_pos].replace(b"\x00", b" ").split()
    if not count_token or not count_token[-1].isdigit():
        raise MicromineFormatError("field count not found before VARIABLES")
    count = int(count_token[-1])

    pos = vars_pos + len(VARIABLES_TAG)
    fields: list[Field] = []
    raw_names: list[bytes] = []
    for _ in range(count):
        end = data.find(b"\r\n", pos)
        if end < 0:
            raise MicromineFormatError("truncated VARIABLES block")
        line = data[pos:end]
        pos = end + 2

        m = _VARIABLE_RE.match(line)
        if not m:
            raise MicromineFormatError(f"bad variable line: {line!r}")
        name_raw, kind_token, width_token, decimals_token = m.groups()
        raw_names.append(name_raw)
        if kind_token.decode("ascii") not in FIELD_TYPES:
            raise MicromineFormatError(f"unknown field type: {kind_token!r}")
        fields.append(
            Field(
                name=_decode_text(name_raw, codepage or "ascii"),
                kind=kind_token.decode("ascii"),
                width=int(width_token),
                decimals=int(decimals_token),
            )
        )

    if codepage is None:
        # No codepage in the header: BOCU-1 names are ASCII + 0x50 (every byte above 0x70).
        raws = [r for r in raw_names if r.strip()]
        codepage = "BOCU-1" if raws and all(all(b > 0x70 or b == 0x20 for b in r.strip()) for r in raws) else "ascii"
        fields = [Field(_decode_text(r, codepage), f.kind, f.width, f.decimals) for r, f in zip(raw_names, fields)]

    # Records are not flagged in the header: they start right after the last
    # variable line (the 'pos' value after the loop).
    records_start = pos
    return codepage, fields, records_start


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

    codes = {"R": "d", "F": "f", "L": "i", "S": "h", "B": "B"}
    fmt = "<" + "".join(codes.get(f.kind, f"{f.width}s") for f in fields)
    # Binary fields have a fixed size; enforce it before unpacking.
    for f in fields:
        if f.kind in BINARY_TYPES and f.width != np.dtype(BINARY_TYPES[f.kind]).itemsize:
            raise MicromineFormatError(f"field {f.name} of type {f.kind} must be "
                                       f"{np.dtype(BINARY_TYPES[f.kind]).itemsize} bytes, got {f.width}")
    fmt += f"{len(RECORD_TRAILER)}x"
    if struct.calcsize(fmt) != record_len:
        raise MicromineFormatError("internal record size mismatch")

    rows = []
    for raw_row in struct.iter_unpack(fmt, data[start:]):
        row = []
        for value, f in zip(raw_row, fields):
            if f.kind not in BINARY_TYPES:
                row.append(_decode_text(value, codepage))
            else:
                row.append(value)
        rows.append(row)

    columns = unique_names([f.name for f in fields])
    frame = pd.DataFrame(rows, columns=columns)
    for f, col in zip(fields, columns):
        if f.kind in BINARY_TYPES:
            frame[col] = frame[col].astype("float64")
        elif f.kind == "N":
            frame[col] = pd.to_numeric(frame[col].str.strip().str.replace(",", ".", regex=False), errors="coerce")

    return MicromineTable(path=str(path), codepage=codepage, fields=fields, frame=frame)


# --------------------------------------------------------------------------------------------
# Column reader for large files (block models): the records are a memory map, numbers are
# converted a chunk at a time and text columns become category codes. Nothing per row in Python.
# --------------------------------------------------------------------------------------------
@dataclass
class Column:
    name: str
    kind: str  # "number" | "category"
    values: np.ndarray  # float64, or int32 codes (−1 = empty) for categories
    labels: list[str] | None = None
    source: str = ""  # field type in the file
    unparsed: int = 0  # N values that are not numbers (become NaN)


@dataclass
class MicromineColumns:
    codepage: str
    fields: list[Field]
    rows: int
    columns: list[Column]
    trailing_bytes: int = 0


def read_header(path: Path) -> tuple[str, list[Field], int]:
    """Codepage, fields and the offset of the first record, reading only the start of the file."""
    size = 1 << 16
    with path.open("rb") as f:
        while True:
            head = f.read(size)
            f.seek(0)
            try:
                return _parse_header(head)
            except MicromineFormatError:
                if len(head) < size or size >= 64 << 20:
                    raise
                size *= 4


def _text_to_float(text: np.ndarray) -> tuple[np.ndarray, int]:
    """Fixed-width ASCII numbers (an S-dtype array) to float64; blanks and junk become NaN."""
    blank = np.char.strip(text) == b""
    filled = np.where(blank, b"nan", text)
    try:
        return filled.astype(np.float64), 0
    except ValueError:
        values = pd.to_numeric(pd.Series(np.char.decode(filled, "ascii", "replace")).str.strip()
                               .str.replace(",", ".", regex=False), errors="coerce").to_numpy(np.float64)
        return values, int((np.isnan(values) & ~blank).sum())


def read_columns(path: str | Path, progress=None, chunk: int = 1 << 20) -> MicromineColumns:
    """All fields of a Micromine DAT as columns (see `Column`), via a memory map."""
    path = Path(path)
    codepage, fields, start = read_header(path)
    dtype = np.dtype({
        "names": [f"f{i}" for i in range(len(fields))] + ["eol"],
        "formats": [BINARY_TYPES.get(f.kind, f"S{f.width}") if f.kind != "C" and f.kind != "N" else f"V{f.width}"
                    for f in fields] + ["V2"],
    })
    for f in fields:
        if f.kind in BINARY_TYPES and f.width != np.dtype(BINARY_TYPES[f.kind]).itemsize:
            raise MicromineFormatError(f"поле {f.name} типа {f.kind}: ширина {f.width}")
    size = path.stat().st_size
    rows, trailing = divmod(size - start, dtype.itemsize)
    records = np.memmap(path, dtype=dtype, mode="r", offset=start, shape=(rows,)) if rows else np.zeros(0, dtype)
    bocu = is_bocu(codepage)
    names = unique_names([f.name for f in fields])
    out: list[Column] = []
    for n, (f, name) in enumerate(zip(fields, names)):
        if progress:
            progress(n / max(len(fields), 1), f"Поле {name}")
        raw = records[f"f{n}"]
        if f.kind in BINARY_TYPES:
            values = np.asarray(raw, dtype=np.float64)
            if f.kind != "R" and f.kind != "F":
                values = values.astype(np.float64)
            out.append(Column(name, "number", values, source=f.kind))
            continue
        if f.kind == "N":
            values = np.empty(rows, dtype=np.float64)
            unparsed = 0
            for a in range(0, rows, chunk):
                b = min(rows, a + chunk)
                part = np.frombuffer(np.ascontiguousarray(raw[a:b]).tobytes(), dtype=np.uint8).reshape(b - a, f.width)
                if bocu:
                    part = bocu1.number_bytes(part)
                text = np.ascontiguousarray(part).view(f"S{f.width}").ravel()
                values[a:b], bad = _text_to_float(text)
                unparsed += bad
            out.append(Column(name, "number", values, source="N", unparsed=unparsed))
            continue
        # C: decode each distinct value once.
        text = np.frombuffer(np.ascontiguousarray(raw).tobytes(), dtype=f"S{f.width}") if rows else np.zeros(0, f"S{f.width}")
        uniques, inverse = np.unique(text, return_inverse=True)
        decoded = [_decode_text(u, codepage) for u in uniques]
        labels: list[str] = []
        index: dict[str, int] = {}
        remap = np.empty(len(decoded), dtype=np.int32)
        for i, label in enumerate(decoded):
            if label == "":
                remap[i] = -1
                continue
            if label not in index:
                index[label] = len(labels)
                labels.append(label)
            remap[i] = index[label]
        codes = remap[inverse.ravel()] if len(decoded) else np.zeros(0, np.int32)
        out.append(Column(name, "category", codes.astype(np.int32), labels=labels, source="C"))
    return MicromineColumns(codepage=codepage, fields=fields, rows=int(rows), columns=out, trailing_bytes=int(trailing))
