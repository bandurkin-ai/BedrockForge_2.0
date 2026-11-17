"""Helpers that build synthetic shapefiles for tests byte by byte from the ESRI Shapefile Technical Description
(1998) and the dBASE III layout — independent of the reader and of pyshp."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

NO_DATA = -1e39  # below −1e38: «no data» of the format
X0, Y0 = 466570.123, 5085061.988  # UTM-sized coordinates with a fraction


def shp_bytes(shape_type: int, records: list[bytes], declared: int | None = None) -> bytes:
    body = b"".join(struct.pack(">2i", i + 1, len(c) // 2) + c for i, c in enumerate(records))
    words = (100 + len(body)) // 2 if declared is None else declared
    return struct.pack(">7i", 9994, 0, 0, 0, 0, 0, words) + struct.pack("<2i8d", 1000, shape_type, *[0.0] * 8) + body


def point(shape_type: int, *values: float) -> bytes:
    return struct.pack(f"<i{len(values)}d", shape_type, *values)


def poly(
    shape_type: int, parts: list[int], xy: list[tuple[float, float]], z: list[float] | None = None,
    m: list[float] | None = None,
) -> bytes:  # fmt: skip
    """PolyLine / Polygon (with `parts`) or MultiPoint (`parts` empty) record of any of the Z and M flavours."""
    n = len(xy)
    out = struct.pack("<i4d", shape_type, 0.0, 0.0, 0.0, 0.0)
    if shape_type % 10 != 8:
        out += struct.pack("<i", len(parts))
    out += struct.pack("<i", n)
    if shape_type % 10 != 8:
        out += struct.pack(f"<{len(parts)}i", *parts)
    out += struct.pack(f"<{2 * n}d", *[v for p in xy for v in p])
    for values in (z, m):
        if values is not None:
            out += struct.pack(f"<{2 + n}d", 0.0, 0.0, *values)
    return out


NULL = struct.pack("<i", 0)


def dbf_bytes(
    fields: list[tuple[bytes, str, int, int]], rows: list[list[bytes]], ldid: int = 0, deleted: tuple[int, ...] = (),
    declared: int | None = None,
) -> bytes:  # fmt: skip
    """A dBASE III table; numbers are right-aligned, everything else is left-aligned, as writers do."""
    record = 1 + sum(w for _, _, w, _ in fields)
    count = len(rows) if declared is None else declared
    out = struct.pack("<B3BIHH17xB2x", 3, 126, 11, 17, count, 32 + 32 * len(fields) + 1, record, ldid)
    for name, kind, width, decimals in fields:
        out += name.ljust(11, b"\x00") + kind.encode() + bytes(4) + bytes([width, decimals]) + bytes(14)
    out += b"\r"
    for i, row in enumerate(rows):
        out += b"*" if i in deleted else b" "
        for (_, kind, width, _), cell in zip(fields, row, strict=True):
            out += cell.rjust(width) if kind in "NF" else cell.ljust(width)
    return out + b"\x1a"


def write_set(tmp_path: Path, shp: bytes, dbf: bytes | None = None, name: str = "layer", **more: bytes) -> Path:
    path = tmp_path / f"{name}.shp"
    path.write_bytes(shp)
    if dbf is not None:
        (tmp_path / f"{name}.dbf").write_bytes(dbf)
    for suffix, data in more.items():
        (tmp_path / f"{name}.{suffix}").write_bytes(data)
    return path


def ids(n: int) -> bytes:
    """A `.dbf` with the field ID = 1…n."""
    return dbf_bytes([(b"ID", "N", 6, 0)], [[str(i + 1).encode()] for i in range(n)])


def big_points(tmp_path: Path, n: int) -> tuple[Path, np.ndarray]:
    """n PointZ records and a .dbf with a name, a grade and a number — written as numpy tables."""
    rng = np.random.default_rng(7)
    xyz = rng.normal(size=(n, 3)) * 1e4 + [X0, Y0, 300.0]
    records = np.zeros(n, dtype=[("number", ">i4"), ("length", ">i4"), ("type", "<i4"), ("xyzm", "<f8", 4)])
    records["number"], records["length"], records["type"] = np.arange(1, n + 1), 18, 11
    records["xyzm"][:, :3], records["xyzm"][:, 3] = xyz, NO_DATA
    path = tmp_path / "big.shp"
    path.write_bytes(shp_bytes(11, [], declared=(100 + records.nbytes) // 2) + records.tobytes())
    table = np.zeros(n, dtype=[("del", "S1"), ("NAME", "S12"), ("AU", "S10"), ("ID", "S8")])
    number = np.arange(n)
    table["del"] = b" "
    table["NAME"] = np.char.add(b"BH-", np.char.mod(b"%d", number % 5000))
    table["AU"] = np.char.rjust(np.char.mod(b"%.3f", number * 0.001), 10)
    table["ID"] = np.char.rjust(np.char.mod(b"%d", number), 8)
    fields = [(b"NAME", "C", 12, 0), (b"AU", "N", 10, 3), (b"ID", "N", 8, 0)]
    (tmp_path / "big.dbf").write_bytes(dbf_bytes(fields, [], declared=n)[:-1] + table.tobytes() + b"\x1a")
    return path, xyz
