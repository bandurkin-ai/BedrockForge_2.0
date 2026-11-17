"""BFA1 — the binary container of 3D data (ADR 0007 §1, ADR 0004 §4).

Layout (little-endian):

    b"BFA1"  u32 L  header (L bytes of UTF-8 JSON)  zeros up to a multiple of 8  arrays…

The header is an object with `arrays: [[name, dtype, count, offset], …]` plus the fields of the kind
(`kind`, `format`, …). `count` is the number of elements (an f8×3 array of n points has count 3n), `offset`
is from the start of the buffer and a multiple of 8, so a Worker takes `new Float64Array(buf, offset, count)`
without copying. Arrays are C-ordered; between them are zeros up to the next multiple of 8. dtypes: `<f8 <f4
<i4 <u4 <u2 <u1`. The header is valid JSON: no NaN in it (missing values are null).
"""

from __future__ import annotations

import json
import struct
from collections.abc import Sequence
from typing import Any

import numpy as np

MAGIC = b"BFA1"
FORMAT = 1
DTYPES = ("<f8", "<f4", "<i4", "<u4", "<u2", "<u1")
MAX_HEADER = 64 << 20  # a decoder refuses a longer header (a damaged buffer)


def _align(n: int) -> int:
    return (n + 7) & ~7


def _prepare(name: str, values: Any, dtype: str) -> np.ndarray:
    if dtype not in DTYPES:
        raise ValueError(f"BFA1: dtype {dtype!r} of {name!r} is not one of {', '.join(DTYPES)}")
    return np.ascontiguousarray(np.asarray(values), dtype=np.dtype(dtype)).reshape(-1)


def encode(header: dict[str, Any], arrays: Sequence[tuple[str, Any, str]]) -> bytes:
    """`arrays` — (name, values, dtype); values are converted to the dtype (little-endian) and flattened."""
    if "arrays" in header:
        raise ValueError("BFA1: the header key 'arrays' is reserved")
    prepared = [(name, _prepare(name, values, dtype), dtype) for name, values, dtype in arrays]
    names = [p[0] for p in prepared]
    if len(set(names)) != len(names):
        raise ValueError("BFA1: array names repeat")
    # Offsets are absolute, so the header length depends on them: iterate to the fixed point (a longer
    # header only moves the data further, the sequence grows and stops within a few rounds).
    start = 0
    while True:
        table: list[tuple[str, str, int, int]] = []
        at = start
        for name, arr, dtype in prepared:
            table.append((name, dtype, int(arr.size), at))
            at = _align(at + arr.nbytes)
        text = json.dumps({**header, "arrays": table}, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        head = text.encode("utf-8")
        need = _align(8 + len(head))
        if need <= start:  # never below: the offsets only grow
            break
        start = need
    out = bytearray(at)
    out[0:4] = MAGIC
    out[4:8] = struct.pack("<I", len(head))
    out[8 : 8 + len(head)] = head
    for (_name, arr, _dtype), (_n, _d, _count, offset) in zip(prepared, table, strict=True):
        out[offset : offset + arr.nbytes] = arr.tobytes()
    return bytes(out)


class FormatError(ValueError):
    pass


def decode(data: bytes | bytearray | memoryview) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Header and arrays (read-only views of `data`): the reference reader for tests and tools."""
    buf = memoryview(data).cast("B")
    if len(buf) < 8 or bytes(buf[:4]) != MAGIC:
        raise FormatError("BFA1: no magic")
    (length,) = struct.unpack("<I", bytes(buf[4:8]))
    if length > MAX_HEADER or 8 + length > len(buf):
        raise FormatError("BFA1: header length out of the buffer")
    header = json.loads(bytes(buf[8 : 8 + length]).decode("utf-8"))
    start = _align(8 + length)
    if any(buf[8 + length : min(start, len(buf))]):
        raise FormatError("BFA1: header padding is not zero")
    arrays: dict[str, np.ndarray] = {}
    for name, dtype, count, offset in header.pop("arrays"):
        if dtype not in DTYPES:
            raise FormatError(f"BFA1: unknown dtype {dtype!r}")
        size = np.dtype(dtype).itemsize * int(count)
        if offset % 8 or offset < start or offset + size > len(buf):
            raise FormatError(f"BFA1: array {name!r} is out of place")
        arrays[name] = np.frombuffer(buf, dtype=np.dtype(dtype), count=int(count), offset=int(offset))
    return header, arrays
