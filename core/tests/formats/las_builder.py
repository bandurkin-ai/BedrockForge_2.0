"""LAS files packed with `struct` straight from the tables of ASPRS LAS 1.4 R15: the reference of the format
for the tests of `formats.las` and `formats.las_write`. Nothing of the reader or the writer is used here."""

from __future__ import annotations

import random
import struct
from collections.abc import Sequence
from dataclasses import dataclass, field

FORMAT_SIZE = {0: 20, 1: 28, 2: 26, 3: 34, 4: 57, 5: 63, 6: 30, 7: 36, 8: 38, 9: 59, 10: 67}
HEADER_SIZE = {(1, 0): 227, (1, 1): 227, (1, 2): 227, (1, 3): 235, (1, 4): 375, (2, 0): 375}
VERSIONS = ((1, 2), (1, 3), (1, 4))
PROJECTION = b"LASF_Projection"
WKT_UTM = (
    'PROJCS["WGS 84 / UTM zone 37N",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
    'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",39],PARAMETER["scale_factor",0.9996],'
    'PARAMETER["false_easting",500000],PARAMETER["false_northing",0],UNIT["metre",1],AUTHORITY["EPSG","32637"]]'
)
WKT_DEGREES = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],'
    'UNIT["degree",0.0174532925199433],AUTHORITY["EPSG","4326"]]'
)


@dataclass
class Pt:
    """One point record by the names of the specification."""

    X: int
    Y: int
    Z: int
    intensity: int = 0
    ret: int = 1  # return number
    nret: int = 1  # number of returns
    scan_dir: int = 0
    edge: int = 0
    cls: int = 0
    synthetic: int = 0
    keypoint: int = 0
    withheld: int = 0
    overlap: int = 0  # formats 6–10 only
    channel: int = 0  # formats 6–10 only
    angle: int = 0  # a char in formats 0–5, a short in 6–10
    user: int = 0
    source: int = 0
    gps: float = 0.0
    rgb: tuple[int, int, int] = (0, 0, 0)
    nir: int = 0
    wave: tuple[int, int, int, float, float, float, float] = (0, 0, 0, 0.0, 0.0, 0.0, 0.0)
    extra: bytes = b""


def point_bytes(p: Pt, fmt: int) -> bytes:
    if fmt <= 5:  # tables 7, 10–13 and 1.3 formats 4, 5
        out = struct.pack(
            "<iiiHBBbBH",
            p.X,
            p.Y,
            p.Z,
            p.intensity,
            (p.ret & 7) | (p.nret & 7) << 3 | p.scan_dir << 6 | p.edge << 7,
            (p.cls & 31) | p.synthetic << 5 | p.keypoint << 6 | p.withheld << 7,
            p.angle,
            p.user,
            p.source,
        )
        if fmt in (1, 3, 4, 5):
            out += struct.pack("<d", p.gps)
        if fmt in (2, 3, 5):
            out += struct.pack("<3H", *p.rgb)
        if fmt in (4, 5):
            out += struct.pack("<BQIffff", *p.wave)
    else:  # table 14 and formats 7–10
        out = struct.pack(
            "<iiiHBBBBhHd",
            p.X,
            p.Y,
            p.Z,
            p.intensity,
            (p.ret & 15) | (p.nret & 15) << 4,
            p.synthetic | p.keypoint << 1 | p.withheld << 2 | p.overlap << 3 | (p.channel & 3) << 4 | p.scan_dir << 6
            | p.edge << 7,
            p.cls,
            p.user,
            p.angle,
            p.source,
            p.gps,
        )
        if fmt in (7, 8, 10):
            out += struct.pack("<3H", *p.rgb)
        if fmt in (8, 10):
            out += struct.pack("<H", p.nir)
        if fmt in (9, 10):
            out += struct.pack("<BQIffff", *p.wave)
    assert len(out) == FORMAT_SIZE[fmt]
    return out + p.extra


def vlr(user_id: bytes, record_id: int, data: bytes, description: bytes = b"", reserved: int = 0) -> bytes:
    return struct.pack("<H16sHH32s", reserved, user_id, record_id, len(data), description) + data


def evlr(user_id: bytes, record_id: int, data: bytes, description: bytes = b"", reserved: int = 0) -> bytes:
    return struct.pack("<H16sHQ32s", reserved, user_id, record_id, len(data), description) + data


def wkt_data(wkt: str) -> bytes:
    return wkt.encode("utf-8") + b"\x00"


def geokeys(epsg: int, *, projected: bool = True, name: str = "", doubles: Sequence[float] = ()) -> list[bytes]:
    """VLRs 34735 (directory), 34736 (doubles), 34737 (texts) of a CRS given by its EPSG code."""
    text = (name + "|").encode("ascii") if name else b""
    keys = [(1024, 0, 1, 1 if projected else 2), (1025, 0, 1, 1)]
    if name:
        keys.append((1026, 34737, len(text), 0))
    if doubles:
        keys.append((2057, 34736, 1, 0))  # GeogSemiMajorAxisGeoKey
    keys.append((3072 if projected else 2048, 0, 1, epsg))
    if projected:
        keys.append((3076, 0, 1, 9001))
    directory = struct.pack("<4H", 1, 1, 0, len(keys)) + b"".join(struct.pack("<4H", *k) for k in sorted(keys))
    out = [vlr(PROJECTION, 34735, directory, b"GeoKeyDirectoryTag")]
    if doubles:
        out.append(vlr(PROJECTION, 34736, struct.pack(f"<{len(doubles)}d", *doubles), b"GeoDoubleParamsTag"))
    if name:
        out.append(vlr(PROJECTION, 34737, text + b"\x00", b"GeoAsciiParamsTag"))
    return out


@dataclass
class Spec:
    """Everything a file is made of; `None` — the value that follows from the points."""

    version: tuple[int, int] = (1, 2)
    fmt: int = 0
    scale: tuple[float, float, float] = (0.001, 0.001, 0.001)
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    vlrs: list[bytes] = field(default_factory=list)
    evlrs: list[bytes] = field(default_factory=list)
    extra: int = 0  # extra bytes of a record
    signature: bytes = b"LASF"
    fmt_byte: int | None = None  # the format as written (bit 7 — LAZ)
    record_length: int | None = None
    header_size: int | None = None
    declared: int | None = None  # the number of point records in the header
    legacy_declared: int | None = None
    global_encoding: int = 0
    source_id: int = 0
    guid: bytes = bytes(16)
    system_id: bytes = b""
    software: bytes = b""
    day: int = 1
    year: int = 2026
    wave_start: int = 0
    gap: bytes = b""  # bytes between the VLRs and the points
    bbox: tuple[float, float, float, float, float, float] | None = None  # max x, min x, max y, min y, max z, min z


def coordinate(i: int, scale: float, offset: float) -> float:
    """X of the specification: `X_record * X_scale + X_offset` in doubles."""
    return i * scale + offset


def header_bytes(spec: Spec, points: Sequence[Pt], offset_to_points: int, evlr_start: int) -> bytes:
    n = len(points) if spec.declared is None else spec.declared
    slots = [0] * 15
    for p in points:
        if 1 <= p.ret <= 15:
            slots[p.ret - 1] += 1
    extended = spec.fmt >= 6 and spec.version >= (1, 4)  # the legacy counters of 1.4 are zero for formats 6–10
    legacy_n = (0 if extended or n > 0xFFFFFFFF else n) if spec.legacy_declared is None else spec.legacy_declared
    legacy_slots = [0] * 5 if extended else slots[:5]
    if spec.bbox is not None:
        box = list(spec.bbox)
    elif points:
        box = []
        for k, name in enumerate("XYZ"):
            values = [coordinate(getattr(p, name), spec.scale[k], spec.offset[k]) for p in points]
            box += [max(values), min(values)]
    else:
        box = [0.0] * 6
    record = FORMAT_SIZE[spec.fmt] + spec.extra if spec.record_length is None else spec.record_length
    out = struct.pack(
        "<4sHH16sBB32s32sHHHIIBHI5I",
        spec.signature,
        spec.source_id,
        spec.global_encoding,
        spec.guid,
        spec.version[0],
        spec.version[1],
        spec.system_id,
        spec.software,
        spec.day,
        spec.year,
        HEADER_SIZE[spec.version] if spec.header_size is None else spec.header_size,
        offset_to_points,
        len(spec.vlrs),
        spec.fmt if spec.fmt_byte is None else spec.fmt_byte,
        record,
        legacy_n,
        *legacy_slots,
    )
    out += struct.pack("<12d", *spec.scale, *spec.offset, *box)
    assert len(out) == 227
    if spec.version >= (1, 3):
        out += struct.pack("<Q", spec.wave_start)
    if spec.version >= (1, 4):
        out += struct.pack("<QIQ15Q", evlr_start if spec.evlrs else 0, len(spec.evlrs), n, *slots)
    assert len(out) == HEADER_SIZE[spec.version]
    return out


def las_bytes(points: Sequence[Pt], spec: Spec | None = None) -> bytes:
    spec = spec or Spec()
    header = HEADER_SIZE[spec.version] if spec.header_size is None else spec.header_size
    body = b"".join(point_bytes(p, spec.fmt) for p in points)
    offset_to_points = header + sum(len(v) for v in spec.vlrs) + len(spec.gap)
    head = header_bytes(spec, points, offset_to_points, offset_to_points + len(body))
    return head.ljust(header, b"\x00") + b"".join(spec.vlrs) + spec.gap + body + b"".join(spec.evlrs)


def random_points(n: int, fmt: int, seed: int, extra: int = 0) -> list[Pt]:
    """Points with every field filled with noise: a reader that takes a field from a wrong place is caught."""
    rng = random.Random(seed)
    extended = fmt >= 6
    out = []
    for _ in range(n):
        nret = rng.randint(1, 15 if extended else 7)
        out.append(
            Pt(
                X=rng.randint(-(2**31), 2**31 - 1),
                Y=rng.randint(-(2**31), 2**31 - 1),
                Z=rng.randint(-(2**31), 2**31 - 1),
                intensity=rng.randint(0, 65535),
                ret=rng.randint(1, nret),
                nret=nret,
                scan_dir=rng.randint(0, 1),
                edge=rng.randint(0, 1),
                cls=rng.randint(0, 255 if extended else 31),
                synthetic=rng.randint(0, 1),
                keypoint=rng.randint(0, 1),
                withheld=int(rng.random() < 0.1),
                overlap=int(extended and rng.random() < 0.2),
                channel=rng.randint(0, 3) if extended else 0,
                angle=rng.randint(-30000, 30000) if extended else rng.randint(-90, 90),
                user=rng.randint(0, 255),
                source=rng.randint(0, 65535),
                gps=rng.uniform(1e8, 4e8),
                rgb=(rng.randint(0, 65535), rng.randint(0, 65535), rng.randint(0, 65535)),
                nir=rng.randint(0, 65535),
                wave=(rng.randint(0, 255), rng.randrange(2**64), rng.randrange(2**32), 1.5, -2.25, 0.5, 8.0),
                extra=rng.randbytes(extra),
            )
        )
    return out


def ret_byte(p: Pt) -> int:
    """`ret` of a cloud: the return number in the low four bits, the number of returns in the high four."""
    return p.ret | p.nret << 4
