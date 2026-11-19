"""Writing LAS: a new file, and a copy of the source file with other classes (ТЗ 7.35 Д-22, 9.12; ADR 0014 §3).

A new file — `write_las(path, xyz, cls, ret)` for arrays in memory, `LasWriter` for a cloud that comes in parts:
  * LAS 1.2 with the point format 0 — what any program reads; classes above 31 or returns above 7 need LAS 1.4
    with the format 6 (`pick_layout`). Any of the formats 0–10 in the versions 1.2–1.4 can be asked for: the
    fields a cloud does not keep (intensity, colour, GPS time …) are zeros;
  * `X = rint((x − offset) / scale)`: the scale is 0,001 unless given, the offset — the whole numbers under the
    least coordinates (of the first part when the cloud comes in parts). A coordinate is kept within half a
    scale; a file read by `las.open_las` and written again with its scale and offset has the same integers, and
    read once more — the same doubles bit for bit;
  * `cls` — the class 0–255 (0–31 in the formats 0–5), 0 when not given; `ret` — the return number in the low four
    bits and the number of returns in the high four (three and three in the formats 0–5), «1 of 1» when not given;
  * nothing is written approximately: a point without coordinates (NaN — a gap is not a zero), coordinates beyond
    the int32 of the scale, a class or a return the format has no room for — `unrepresentable`;
  * the header is completed at the end: the number of points, by returns, the extent (of the written integers).
    `LasWriter.write_records` takes raw records of the format (`las.LasFile.records`) with every field.
  * `vlrs`, `evlrs` — `Vlr` records; `wkt_vlr`, `geokey_vlrs` make those of a CRS.
By the source — `rewrite_classes(source, target, seq, cls)`: the source file byte for byte, but in the records
`seq` the bits of the class are those of `cls` (five bits in the formats 0–5 — the flags beside them stay; the byte
in 6–10). The colour, the GPS time, the VLRs, the CRS and whatever else the file has are kept.
The file appears under its name only when it is complete; `progress` may raise to cancel.
Error codes: unrepresentable, no_points, source_mismatch, truncated, and those of the reader for the source.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO

import numpy as np

from bf_core.formats.errors import FormatError
from bf_core.formats.las import CHUNK, open_las
from bf_core.formats.las_header import (
    ASCII_RECORD,
    ENCODING_WKT,
    FORMAT_LENGTH,
    HEAD,
    HEAD14,
    HEADER_SIZES,
    KEYS_RECORD,
    LAST_FORMAT,
    PROJECTION,
    SIGNATURE,
    WKT_RECORD,
    n_text,
    point_dtype,
)
from bf_core.formats.table import Progress, no_progress

SOFTWARE = "BedrockForge 2"
SYSTEM_ID = "OTHER"  # of the specification: the file is not the output of a sensor
SCALE = 0.001
INT_MIN, INT_MAX = -(2**31), 2**31 - 1
COPY_BLOCK = 1 << 20
_HINT_14 = "Сохраните облако новым файлом LAS 1.4 с форматом точек 6."


@dataclass(frozen=True)
class Vlr:
    """A variable length record to write; as an EVLR it holds more than 65 535 bytes."""

    user_id: str
    record_id: int
    data: bytes
    description: str = ""

    def pack(self, extended: bool = False) -> bytes:
        user = self.user_id.encode("ascii")
        if len(user) > 16 or not 0 <= self.record_id <= 0xFFFF:
            raise ValueError("a VLR: a user id of 16 ASCII characters and a record id of 16 bits are expected")
        if not extended and len(self.data) > 0xFFFF:
            raise ValueError("a VLR holds 65 535 bytes: write the record as an EVLR of LAS 1.4")
        text = self.description.encode("ascii", "replace")[:32]
        head = struct.pack("<H16sHQ32s" if extended else "<H16sHH32s", 0, user, self.record_id, len(self.data), text)
        return head + self.data


def wkt_vlr(wkt: str) -> Vlr:
    """The CRS as WKT (record 2112); sets bit 4 of the global encoding of a LAS 1.4 file."""
    return Vlr(PROJECTION, WKT_RECORD, wkt.encode("utf-8") + b"\x00", "OGC COORDINATE SYSTEM WKT")


def geokey_vlrs(epsg: int, *, geographic: bool = False, name: str = "") -> list[Vlr]:
    """The CRS as GeoTIFF keys by its EPSG code — what the formats 0–5 keep it in."""
    if not 0 < epsg < 32767:
        raise ValueError("an EPSG code of a GeoTIFF key is 1…32766")
    text = (name.replace("|", " ") + "|").encode("ascii", "replace") if name else b""
    keys = [(1024, 0, 1, 2 if geographic else 1)]  # GTModelType: projected or geographic
    if text:
        keys.append((1026, ASCII_RECORD, len(text), 0))  # GTCitation
    keys.append((2048 if geographic else 3072, 0, 1, epsg))
    words = [1, 1, 0, len(keys), *(word for key in keys for word in key)]
    out = [Vlr(PROJECTION, KEYS_RECORD, struct.pack(f"<{len(words)}H", *words), "GeoKeyDirectoryTag")]
    return out + [Vlr(PROJECTION, ASCII_RECORD, text + b"\x00", "GeoAsciiParamsTag")] * bool(text)


def pick_layout(max_class: int = 0, max_return: int = 0) -> tuple[tuple[int, int], int]:
    """The version and the point format of a new file: LAS 1.2, format 0; LAS 1.4, format 6 for classes above 31
    or returns above 7 (`max_return` — the greater of the return number and the number of returns)."""
    return ((1, 4), 6) if max_class > 31 or max_return > 7 else ((1, 2), 0)


def return_limit(ret: np.ndarray) -> int:
    """The greatest return number or number of returns of `ret` (for `pick_layout`)."""
    return int(max((ret & 15).max(), (ret >> 4).max())) if len(ret) else 0


@dataclass
class LasWritten:
    path: Path
    points: int
    version: tuple[int, int]
    point_format: int
    size: int
    changed: int = 0  # by the source: the records whose class is not what it was
    warnings: list[str] = field(default_factory=list)


def _unfit(message: str, hint: str) -> FormatError:
    return FormatError("unrepresentable", message, hint)


def _gap() -> FormatError:
    return _unfit(
        "В облаке есть точки без координат (пропуски или бесконечные значения): в LAS их записать нельзя.",
        "Удалите такие точки или экспортируйте облако в CSV.",
    )


def _triple(value: float | Sequence[float], what: str, zero: bool) -> tuple[float, float, float]:
    x, y, z = (float(value),) * 3 if isinstance(value, (int, float)) else (float(v) for v in value)
    if not all(math.isfinite(v) and (zero or v != 0.0) for v in (x, y, z)):
        raise ValueError(f"{what}: finite numbers{'' if zero else ' other than zero'} are expected")
    return (x, y, z)


def _column(values: Any, n: int, what: str) -> np.ndarray | None:
    if values is None:
        return None
    out = np.asarray(values)
    if out.shape != (n,) or out.dtype.kind not in "ui" or (n and (int(out.min()) < 0 or int(out.max()) > 255)):
        raise ValueError(f"{what}: {n} whole numbers 0…255 are expected")
    return out.astype(np.uint8, copy=False)


class LasWriter:
    """A new LAS file written in parts (see the module docstring): `write` — points, `write_records` — raw
    records, `close` → `LasWritten` (also `result`). As a context manager it closes itself, and on an error
    leaves no file (`abort`)."""

    def __init__(
        self,
        path: str | Path,
        *,
        version: tuple[int, int] = (1, 2),
        point_format: int = 0,
        scale: float | Sequence[float] = SCALE,
        offset: Sequence[float] | None = None,
        extra_bytes: int = 0,
        vlrs: Sequence[Vlr] = (),
        evlrs: Sequence[Vlr] = (),
        system_id: str = SYSTEM_ID,
        software: str = SOFTWARE,
        created: tuple[int, int] | None = None,  # year, day of the year; today by default
        global_encoding: int | None = None,
        file_source_id: int = 0,
        guid: bytes = bytes(16),
    ) -> None:
        self.version = (int(version[0]), int(version[1]))
        if self.version not in ((1, 2), (1, 3), (1, 4)) or point_format not in FORMAT_LENGTH or extra_bytes < 0:
            raise ValueError("LAS 1.2–1.4 and a point format 0–10 are expected")
        if evlrs and self.version != (1, 4):
            raise ValueError("an EVLR needs LAS 1.4")
        self.path, self.point_format = Path(path), point_format
        self.scale = _triple(scale, "scale", zero=False)
        self.offset = None if offset is None else _triple(offset, "offset", zero=True)
        self.dtype = point_dtype(point_format, FORMAT_LENGTH[point_format] + extra_bytes)
        if self.dtype.itemsize > 0xFFFF:
            raise ValueError("a point record holds 65 535 bytes")
        records = [*vlrs, *evlrs]
        wkt = any(v.user_id == PROJECTION and v.record_id == WKT_RECORD for v in records)
        modern = self.version == (1, 4) and (point_format >= 6 or wkt)  # the CRS of such a file is WKT
        self._encoding = (ENCODING_WKT if modern else 0) if global_encoding is None else global_encoding
        today = dt.date.today()
        self._created = created or (today.year, today.timetuple().tm_yday)
        self._names = (system_id.encode("ascii", "replace"), software.encode("ascii", "replace"))
        self._ids = (file_source_id, bytes(guid))
        self._vlrs, self._evlrs = [v.pack() for v in vlrs], [v.pack(True) for v in evlrs]
        self._start = HEADER_SIZES[self.version[1]] + sum(len(v) for v in self._vlrs)
        self._points = 0
        self._returns = np.zeros(16, dtype=np.int64)
        self._lo, self._hi = [INT_MAX] * 3, [INT_MIN] * 3  # of the integers of the records
        self.result: LasWritten | None = None
        self.warnings: list[str] = []
        if point_format > LAST_FORMAT[self.version[1]]:
            self.warnings.append(f"Формат точек {point_format} не предусмотрен версией LAS {self.version[0]}."
                                 f"{self.version[1]}: не всякая программа прочитает такой файл.")  # fmt: skip
        self._part = self.path.with_name(self.path.name + ".part")
        self._file: BinaryIO | None = self._part.open("wb")
        self._file.write(bytes(HEADER_SIZES[self.version[1]]) + b"".join(self._vlrs))

    def __enter__(self) -> LasWriter:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        if kind is None:
            self.close()
        else:
            self.abort()

    def write(self, xyz: np.ndarray, cls: np.ndarray | None = None, ret: np.ndarray | None = None) -> None:
        """Points: `xyz` n×3 doubles, `cls` and `ret` — n bytes each or None (see the module docstring)."""
        xyz = np.asarray(xyz, dtype=np.float64)
        if xyz.ndim != 2 or xyz.shape[1] != 3:
            raise ValueError("xyz: an array n×3 is expected")
        n = len(xyz)
        cls, ret = _column(cls, n, "cls"), _column(ret, n, "ret")
        if n and self.offset is None:
            lowest = xyz.min(axis=0)
            if not np.isfinite(lowest).all():
                raise _gap()
            self.offset = tuple(float(v) for v in np.floor(lowest))  # type: ignore[assignment]
        for a in range(0, n, CHUNK):  # the working arrays are those of one chunk
            b = a + CHUNK
            self._append(self._records(xyz[a:b], None if cls is None else cls[a:b], None if ret is None else ret[a:b]))

    def _records(self, xyz: np.ndarray, cls: np.ndarray | None, ret: np.ndarray | None) -> np.ndarray:
        assert self.offset is not None
        rec = np.zeros(len(xyz), dtype=self.dtype)
        fmt = self.point_format
        for k, name in enumerate("XYZ"):
            node = xyz[:, k] - self.offset[k]
            node /= self.scale[k]
            np.rint(node, out=node)  # the nearest node of the grid, ties to even
            if not (node.min() >= INT_MIN and node.max() <= INT_MAX):  # NaN compares as false
                if not np.isfinite(xyz[:, k]).all():
                    raise _gap()
                raise _unfit(
                    f"Координаты {name} не помещаются в LAS с масштабом {self.scale[k]:g} и смещением "
                    f"{self.offset[k]:g}: точки дальше 2 147 483 647 шагов масштаба от смещения.",
                    "Увеличьте масштаб (например, 0,01) или задайте смещение у самого облака.",
                )
            rec[name] = node
        if cls is not None:
            if fmt <= 5 and len(cls) and cls.max() > 31:
                raise _unfit(f"Класс {int(cls.max())} не помещается в формат точек {fmt}: в форматах 0–5 классы — "
                             "от 0 до 31.", _HINT_14)  # fmt: skip
            rec["classification"] = cls
        if ret is None:
            rec["returns"] = 0x09 if fmt <= 5 else 0x11  # the return 1 of 1
        elif fmt >= 6:
            rec["returns"] = ret
        else:
            if (ret & 0x88).any():
                raise _unfit(f"Номер или число возвратов больше 7 не помещается в формат точек {fmt}.", _HINT_14)
            rec["returns"] = (ret & 7) | ((ret >> 1) & 0x38)
        return rec

    def write_records(self, records: np.ndarray) -> None:
        """Raw records of the format of the file (`point_dtype`), every field as given."""
        if records.dtype != self.dtype or records.ndim != 1:
            raise ValueError(f"records of {self.dtype} are expected")
        if self.offset is None:
            raise ValueError("raw records are integers of a grid: give the writer its offset")
        self._append(np.ascontiguousarray(records))

    def _append(self, rec: np.ndarray) -> None:
        if self._file is None:
            raise ValueError("the writer is closed")
        if not len(rec):
            return
        for k, name in enumerate("XYZ"):
            self._lo[k] = min(self._lo[k], int(rec[name].min()))
            self._hi[k] = max(self._hi[k], int(rec[name].max()))
        self._returns += np.bincount(rec["returns"] & (7 if self.point_format <= 5 else 15), minlength=16)
        self._file.write(rec.view(np.uint8))  # type: ignore[arg-type]
        self._points += len(rec)

    def _header(self, evlr_start: int) -> bytes:
        n, minor = self._points, self.version[1]
        if n > 0xFFFFFFFF and minor < 4:
            raise _unfit(f"В LAS 1.{minor} не помещается {n_text(n)} точек.", "Сохраните облако как LAS 1.4.")
        counted = [int(v) for v in self._returns[1:]]
        modern = minor >= 4 and self.point_format >= 6  # the legacy counters of such a file are zeros
        legacy = [0] * 6 if modern or n > 0xFFFFFFFF else [n, *counted[:5]]
        assert self.offset is not None  # there are points
        box: list[float] = []
        for k in range(3):  # max, min of each axis; a negative scale swaps them
            ends = sorted(i * self.scale[k] + self.offset[k] for i in (self._lo[k], self._hi[k]))
            box += [ends[1], ends[0]]
        head = HEAD.pack(
            SIGNATURE, self._ids[0], self._encoding, self._ids[1], *self.version, *self._names, self._created[1],
            self._created[0], HEADER_SIZES[minor], self._start, len(self._vlrs), self.point_format, self.dtype.itemsize,
            *legacy, *self.scale, *self.offset, *box,
        )  # fmt: skip
        if minor >= 3:
            head += struct.pack("<Q", 0)  # no wave packets
        if minor >= 4:
            head += HEAD14.pack(evlr_start if self._evlrs else 0, len(self._evlrs), n, *counted)
        return head

    def close(self) -> LasWritten:
        """Complete the header and put the file under its name."""
        if self.result is not None:
            return self.result
        if self._file is None:
            raise ValueError("the writer is closed")
        try:
            if not self._points:
                raise FormatError("no_points", "В облаке нет точек: файл LAS не записан.", "Выберите облако с точками.")
            evlr_start = self._file.tell()
            self._file.write(b"".join(self._evlrs))
            size = self._file.tell()
            self._file.seek(0)
            self._file.write(self._header(evlr_start))
            self._file.close()
            self._file = None
            os.replace(self._part, self.path)
        except BaseException:
            self.abort()
            raise
        self.result = LasWritten(self.path, self._points, self.version, self.point_format, size, 0, self.warnings)
        return self.result

    def abort(self) -> None:
        """Drop what is written: no file is left."""
        if self._file is not None:
            self._file.close()
            self._file = None
        self._part.unlink(missing_ok=True)


def write_las(
    path: str | Path,
    xyz: np.ndarray,
    cls: np.ndarray | None = None,
    ret: np.ndarray | None = None,
    *,
    progress: Progress | None = None,
    chunk: int = CHUNK,
    version: tuple[int, int] | None = None,
    point_format: int | None = None,
    offset: Sequence[float] | None = None,
    **options: Any,
) -> LasWritten:
    """Write a cloud that is in memory as a new LAS file (see the module docstring). `version` and `point_format`
    follow from the classes and the returns unless given; `options` — those of `LasWriter` (`scale`, `vlrs` …)."""
    tick = progress or no_progress
    xyz = np.asarray(xyz, dtype=np.float64)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or chunk < 1:
        raise ValueError("xyz: an array n×3 is expected; chunk: a positive number")
    n = len(xyz)
    cls, ret = _column(cls, n, "cls"), _column(ret, n, "ret")
    if point_format is not None and point_format not in FORMAT_LENGTH:
        raise ValueError("a point format 0–10 is expected")
    if version is None and point_format is None:
        top = int(cls.max()) if cls is not None and n else 0
        version, point_format = pick_layout(top, 0 if ret is None else return_limit(ret))
    elif point_format is None:
        point_format = 6 if tuple(version or ()) == (1, 4) else 0
    elif version is None:
        version = next((1, minor) for minor in (2, 3, 4) if point_format <= LAST_FORMAT[minor])
    assert version is not None
    if not n:
        raise FormatError("no_points", "В облаке нет точек: файл LAS не записан.", "Выберите облако с точками.")
    if offset is None:
        lowest = xyz.min(axis=0)
        if not np.isfinite(lowest).all():
            raise _gap()
        offset = [float(v) for v in np.floor(lowest)]
    with LasWriter(path, version=version, point_format=point_format, offset=offset, **options) as writer:
        for a in range(0, n, chunk):
            tick(a / n, f"Запись точек: {n_text(a)} из {n_text(n)}")
            b = a + chunk
            writer.write(xyz[a:b], None if cls is None else cls[a:b], None if ret is None else ret[a:b])
        tick(1.0, f"Записано точек: {n_text(n)}")
    assert writer.result is not None
    return writer.result


# ---------------------------------------------------------------- by the source


def _copy(src: BinaryIO, out: BinaryIO, count: int | None) -> None:
    """`count` bytes (all that is left when None) from one file to the other."""
    while count is None or count > 0:
        block = src.read(COPY_BLOCK if count is None else min(COPY_BLOCK, count))
        if not block:
            break
        out.write(block)
        count = None if count is None else count - len(block)


def rewrite_classes(
    source: str | Path,
    target: str | Path,
    seq: np.ndarray,
    cls: np.ndarray,
    progress: Progress | None = None,
    *,
    chunk: int = CHUNK,
) -> LasWritten:
    """A copy of the LAS file `source` in which the record `seq[i]` has the class `cls[i]` and nothing else
    differs (see the module docstring). `target` may be the source itself. Memory: two bytes a record of the
    source and the records of one chunk."""
    tick = progress or no_progress
    source, target = Path(source), Path(target)
    cloud = open_las(source)
    h, n = cloud.header, cloud.point_count
    seq, cls = np.asarray(seq), np.asarray(cls)
    if seq.ndim != 1 or seq.shape != cls.shape or seq.dtype.kind not in "ui" or cls.dtype.kind not in "ui" or chunk < 1:
        raise ValueError("seq and cls: whole numbers, one class for each record number, are expected")
    if len(seq) and not 0 <= int(seq.min()) <= int(seq.max()) < n:
        raise FormatError(
            "source_mismatch",
            f"Исходный файл LAS не соответствует облаку: в нём {n_text(n)} записей, а облако ссылается на запись "
            f"№ {n_text(int(seq.max()))}.",
            "Экспортируйте облако новым файлом LAS.",
        )
    legacy = h.point_format <= 5
    top = int(cls.max()) if len(cls) else 0
    if top > (31 if legacy else 255):
        raise _unfit(f"Класс {top} не помещается в формат точек {h.point_format} исходного файла: в форматах 0–5 "
                     "классы — от 0 до 31.", "Экспортируйте облако новым файлом LAS 1.4 (формат точек 6).")  # fmt: skip
    fresh, given = np.zeros(n, dtype=np.uint8), np.zeros(n, dtype=bool)
    fresh[seq], given[seq] = cls, True
    kept = 0xE0 if legacy else 0x00  # formats 0–5: synthetic, key-point, withheld share the byte with the class
    part = target.with_name(target.name + ".part")
    changed = 0
    try:
        with source.open("rb") as src, part.open("wb") as out:
            _copy(src, out, h.offset_to_points)  # the header and the VLRs
            buffer = np.empty(min(chunk, n), dtype=cloud.dtype)
            for a in range(0, n, chunk):
                tick(a / n, f"Запись точек: {n_text(a)} из {n_text(n)}")
                b = min(n, a + chunk)
                rec = buffer[: b - a]
                raw = rec.view(np.uint8)
                if src.readinto(raw) != len(raw):  # type: ignore[attr-defined]
                    raise FormatError("truncated", "Исходный файл LAS изменился во время записи: он стал короче.",
                                      "Повторите экспорт.")  # fmt: skip
                byte = rec["classification"]
                other = np.where(given[a:b], (byte & kept) | fresh[a:b], byte)
                changed += int(np.count_nonzero(other != byte))
                rec["classification"] = other
                out.write(raw)  # type: ignore[arg-type]
            _copy(src, out, None)  # EVLRs and whatever else follows the points
            tick(1.0, f"Записано точек: {n_text(n)}")
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)
    return LasWritten(target, n, h.version, h.point_format, target.stat().st_size, changed)


__all__ = [
    "LasWriter", "LasWritten", "Vlr", "geokey_vlrs", "pick_layout", "return_limit", "rewrite_classes", "wkt_vlr",
    "write_las",
]  # fmt: skip
