"""The header of a LAS 1.2–1.4 file: the public header block, VLR / EVLR, the CRS as metadata, the layouts of
the point records of formats 0–10 (ТЗ 7.35 Д-22, 9.12; ADR 0014 §3). The format is ASPRS LAS 1.4 R15.

`read_header(path)` → `LasHeader`, checked:
  * the signature `LASF`; versions 1.2–1.4 (1.1 is read as 1.2 with a warning; 1.0, 2.0 are refused); the length
    of the header and of a point record against the version and the point format; a finite scale that is not
    zero and a finite offset; LAZ (the extension `.laz`, bit 7 of the point format, a VLR `laszip encoded`) —
    `laz_not_supported`;
  * the number of records against the size of the file: a header that declares billions allocates nothing, a cut
    file gives its whole records and a warning, a header without a counter is counted by the size; EVLRs and
    internal wave packets after the points are not taken for points;
  * VLRs and EVLRs are listed (`LasVlr`); damaged ones are skipped with a warning. The payload is read only of the
    projection records of `LASF_Projection`: 2112 (WKT), 34735–34737 (GeoTIFF keys) → `wkt` (up to 32 КБ),
    `geokeys`, `epsg` (keys 3072 / 2048), `geographic` (degrees: a warning). Nothing is reprojected.
`point_dtype(point_format, record_length)` — the packed record as a structured dtype of numpy; bytes beyond the
format («extra bytes») are the field `extra`. `LasHeader.as_meta()` — `meta.source.las` of a cloud.
Error codes: laz_not_supported, empty_file, not_las, unsupported_version, unsupported_format, truncated, broken,
bad_record_length, bad_scale, bad_offset, no_points.
"""

from __future__ import annotations

import math
import re
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from bf_core.formats.errors import FormatError

MAX_RECORDS = 0xFFFFFFFF  # `seq` is a uint32
MAX_WKT = 32 * 1024  # bytes of a WKT kept in the metadata
MAX_PAYLOAD = 1 << 20  # bytes of a projection record that are read
MAX_VLRS = 4096
SIGNATURE = b"LASF"
PROJECTION = "LASF_Projection"
WKT_RECORD, KEYS_RECORD, DOUBLES_RECORD, ASCII_RECORD = 2112, 34735, 34736, 34737
HEADER_SIZES = {1: 227, 2: 227, 3: 235, 4: 375}  # by the minor version; 1.1 is read as 1.2
LAST_FORMAT = {1: 3, 2: 3, 3: 5, 4: 10}  # the formats a version provides for
ENCODING_WKT = 16  # global encoding, bit 4: the CRS is WKT

_CORE = [("X", "<i4"), ("Y", "<i4"), ("Z", "<i4"), ("intensity", "<u2"), ("returns", "u1")]
_LEGACY = _CORE + [("classification", "u1"), ("scan_angle", "i1"), ("user_data", "u1"), ("point_source_id", "<u2")]
_EXTENDED = _CORE + [("flags", "u1"), ("classification", "u1"), ("user_data", "u1"), ("scan_angle", "<i2"),
                     ("point_source_id", "<u2"), ("gps_time", "<f8")]  # fmt: skip
_GPS = [("gps_time", "<f8")]
_RGB = [("red", "<u2"), ("green", "<u2"), ("blue", "<u2")]
_NIR = [("nir", "<u2")]
_WAVE = [("wave_index", "u1"), ("wave_offset", "<u8"), ("wave_size", "<u4"), ("wave_location", "<f4"),
         ("wave_xt", "<f4"), ("wave_yt", "<f4"), ("wave_zt", "<f4")]  # fmt: skip
_FIELDS = {
    0: _LEGACY, 1: _LEGACY + _GPS, 2: _LEGACY + _RGB, 3: _LEGACY + _GPS + _RGB, 4: _LEGACY + _GPS + _WAVE,
    5: _LEGACY + _GPS + _RGB + _WAVE, 6: _EXTENDED, 7: _EXTENDED + _RGB, 8: _EXTENDED + _RGB + _NIR,
    9: _EXTENDED + _WAVE, 10: _EXTENDED + _RGB + _NIR + _WAVE,
}  # fmt: skip
FORMAT_LENGTH = {k: np.dtype(v).itemsize for k, v in _FIELDS.items()}  # 20, 28, 26, 34, 57, 63, 30, 36, 38, 59, 67
HEAD = struct.Struct("<4sHH16sBB32s32sHHHIIBHI5I12d")  # 227 bytes: the header of LAS 1.2
HEAD14 = struct.Struct("<QIQ15Q")  # from byte 235: the first EVLR, their number, 64-bit counters
_HINT = "Сохраните облако заново как LAS 1.2–1.4 без сжатия (например, в CloudCompare) и загрузите снова."
_HINT_COPY = "Скопируйте файл заново: копирование или загрузка прервались."


def point_dtype(point_format: int, record_length: int | None = None) -> np.dtype:
    """The packed record of a point format; bytes beyond it («extra bytes») are the field `extra`."""
    fields = list(_FIELDS[point_format])
    extra = (record_length or FORMAT_LENGTH[point_format]) - FORMAT_LENGTH[point_format]
    if extra < 0:
        raise ValueError("record_length is shorter than the point format")
    return np.dtype(fields + [("extra", f"V{extra}")] if extra else fields)


def n_text(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def fail(code: str, message: str, hint: str = _HINT) -> FormatError:
    return FormatError(code, message, hint)


def laz_error() -> FormatError:
    return FormatError(
        "laz_not_supported",
        "Сжатые облака LAZ не читаются.",
        "Сохраните облако как LAS без сжатия (CloudCompare: «Сохранить как» → LAS; LAStools: "
        "laszip -i облако.laz -o облако.las) и загрузите снова.",
    )


def _text(raw: bytes) -> str:
    raw = raw.split(b"\x00")[0]
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return raw.decode("cp1251", "replace").strip()


@dataclass(frozen=True)
class LasVlr:
    """A variable length record as it lies in the file (its payload is not kept)."""

    user_id: str
    record_id: int
    length: int  # of the payload
    description: str = ""
    extended: bool = False  # an EVLR: after the points
    offset: int = 0  # of the payload in the file

    def as_dict(self) -> dict[str, Any]:
        return {"user_id": self.user_id, "record_id": self.record_id, "length": self.length,
                "description": self.description, "extended": self.extended}  # fmt: skip


@dataclass(frozen=True)
class LasHeader:
    version: tuple[int, int]  # as written: (1, 1) for a file read as 1.2
    point_format: int
    record_length: int
    extra_bytes: int  # of a record beyond its format
    header_size: int
    offset_to_points: int
    point_count: int  # the records that are in the file
    declared_count: int  # … and that the header declares
    scale: tuple[float, float, float]
    offset: tuple[float, float, float]
    bbox: tuple[float, float, float, float, float, float] | None  # x, y, z min, x, y, z max of the header; None — unfit
    global_encoding: int = 0
    file_source_id: int = 0
    guid: bytes = bytes(16)
    system_id: str = ""
    software: str = ""
    created: tuple[int, int] = (0, 0)  # year, day of the year
    points_by_return: tuple[int, ...] = ()
    vlrs: tuple[LasVlr, ...] = ()
    wkt: str | None = None
    geokeys: dict[str, Any] | None = None  # GeoKey id → a number, numbers or a text
    epsg: int | None = None  # of the GeoKeys 3072 (projected) or 2048 (geographic)
    geographic: bool = False  # coordinates in degrees
    warnings: tuple[str, ...] = ()

    def crs_text(self) -> str | None:
        """What `crs.describe` takes: the WKT, or «EPSG:…» of the GeoTIFF keys."""
        return self.wkt or (f"EPSG:{self.epsg}" if self.epsg else None)

    def crs_name(self) -> str | None:
        if self.wkt:
            found = re.match(r'\s*\w+\s*\[\s*"([^"]*)"', self.wkt)
            return found.group(1).strip() if found and found.group(1).strip() else "WKT"
        for key in ("3073", "1026", "2049"):
            if self.geokeys and isinstance(self.geokeys.get(key), str) and self.geokeys[key]:
                return str(self.geokeys[key])
        return f"EPSG:{self.epsg}" if self.epsg else None

    def not_loaded(self) -> list[str]:
        """What a point of the file has and a cloud does not take («не загружается» of the wizard)."""
        names = point_dtype(self.point_format).names or ()
        out = ["интенсивность"]
        out += ["цвет RGB"] * ("red" in names) + ["ближний инфракрасный канал"] * ("nir" in names)
        out += ["время GPS"] * ("gps_time" in names)
        out += ["угол сканирования", "пользовательские данные", "номер источника"]
        out += ["волновые пакеты"] * ("wave_index" in names)
        return out + [f"дополнительные байты ({self.extra_bytes} Б на точку)"] * bool(self.extra_bytes)

    def as_meta(self) -> dict[str, Any]:
        """`meta.source.las` of a cloud: plain JSON."""
        return {
            "version": f"{self.version[0]}.{self.version[1]}",
            "point_format": self.point_format,
            "record_length": self.record_length,
            "extra_bytes": self.extra_bytes,
            "point_count": self.point_count,
            "scale": list(self.scale),
            "offset": list(self.offset),
            "global_encoding": self.global_encoding,
            "system_id": self.system_id,
            "software": self.software,
            "created": list(self.created),
            "wkt": self.wkt,
            "geokeys": self.geokeys,
            "epsg": self.epsg,
            "vlrs": [v.as_dict() for v in self.vlrs],
        }

# ---------------------------------------------------------------- the header


def _scan(
    f: BinaryIO, at: int, stop: int, count: int, extended: bool, warnings: list[str]
) -> list[tuple[LasVlr, bytes | None]]:
    """The VLRs from `at` (EVLRs when `extended`) that fit before `stop`; the payload of projection records."""
    head, label = (60, "EVLR") if extended else (54, "VLR")
    out: list[tuple[LasVlr, bytes | None]] = []
    damaged = False
    for _ in range(min(count, MAX_VLRS)):
        f.seek(at)
        raw = f.read(head) if at + head <= stop else b""
        if len(raw) < head:
            break
        user = _text(raw[2:18])
        record_id, length = struct.unpack_from("<HQ" if extended else "<HH", raw, 18)
        if user.lower() == "laszip encoded":
            raise laz_error()
        if at + head + length > stop:
            warnings.append(f"Запись {label} «{user}» № {record_id} не помещается в файл: служебные записи "
                            "за ней не разобраны.")  # fmt: skip
            damaged = True
            break
        wanted = user == PROJECTION and record_id in (WKT_RECORD, KEYS_RECORD, DOUBLES_RECORD, ASCII_RECORD)
        data = f.read(min(length, MAX_PAYLOAD + 1)) if wanted else None
        out.append((LasVlr(user, record_id, length, _text(raw[head - 32 :]), extended, at + head), data))
        at += head + length
    if len(out) < count and not damaged:
        warnings.append(f"В заголовке указано записей {label}: {n_text(count)}, найдено: {n_text(len(out))}.")
    return out


def _geokeys(directory: bytes, doubles: bytes, texts: bytes) -> dict[str, Any] | None:
    words = struct.unpack(f"<{len(directory) // 2}H", directory[: len(directory) // 2 * 2])
    if len(words) < 4 or words[0] != 1:
        return None
    numbers = struct.unpack(f"<{len(doubles) // 8}d", doubles[: len(doubles) // 8 * 8])
    text = texts.decode("latin-1")
    out: dict[str, Any] = {}
    for k in range(min(words[3], (len(words) - 4) // 4)):
        key, place, n, value = words[4 + 4 * k : 8 + 4 * k]
        if place == 0:
            out[str(key)] = value
        elif place == DOUBLES_RECORD and n and value + n <= len(numbers):
            part = [x for x in numbers[value : value + n] if math.isfinite(x)]
            if len(part) == n:
                out[str(key)] = part[0] if n == 1 else part
        elif place == ASCII_RECORD and value + n <= len(text):
            out[str(key)] = text[value : value + n].rstrip("|\x00").strip()
    return out or None


def _geographic(wkt: str | None, keys: dict[str, Any] | None) -> bool:
    if wkt:
        head = wkt.upper()
        if re.search(r"\b(PROJCS|PROJCRS|PROJECTEDCRS)\s*\[", head):
            return False
        geodetic = re.search(r"\b(GEODCRS|GEODETICCRS)\s*\[", head) and "ELLIPSOIDAL" in head
        return bool(re.search(r"\b(GEOGCS|GEOGCRS|GEOGRAPHICCRS)\s*\[", head) or geodetic)
    if keys:
        return keys.get("1024") == 2 or ("2048" in keys and "3072" not in keys and keys.get("1024") != 1)
    return False


def _crs(
    found: Sequence[tuple[LasVlr, bytes | None]], warnings: list[str]
) -> tuple[str | None, dict[str, Any] | None]:
    data = {v.record_id: raw for v, raw in reversed(found) if raw is not None}  # the first of a kind wins
    wkt = None
    if WKT_RECORD in data:
        raw = data[WKT_RECORD].split(b"\x00")[0].strip()
        if len(raw) > MAX_WKT:
            warnings.append(f"Описание системы координат (WKT) длиннее {MAX_WKT // 1024} КБ: в сведения об облаке "
                            "оно не записано.")  # fmt: skip
        else:
            wkt = _text(raw) or None
    keys = None
    if KEYS_RECORD in data:
        keys = _geokeys(data[KEYS_RECORD], data.get(DOUBLES_RECORD, b""), data.get(ASCII_RECORD, b""))
    return wkt, keys


def _bbox(box: Sequence[float]) -> tuple[float, float, float, float, float, float] | None:
    hi, lo = box[0::2], box[1::2]  # the header keeps max x, min x, max y, min y, max z, min z
    if not all(map(math.isfinite, box)) or any(a > b for a, b in zip(lo, hi, strict=True)) or not any(box):
        return None
    return (lo[0], lo[1], lo[2], hi[0], hi[1], hi[2])


def _count(declared: int, size: int, start: int, record: int, marks: Sequence[int], warnings: list[str]) -> int:
    """The number of records by the header and the size of the file; `marks` — where something else begins
    after the points (EVLRs, wave packets)."""
    have = (size - start) // record
    if 0 < declared <= have:
        after = start + declared * record
        end = min([m for m in marks if after <= m <= size], default=size)
        if end - after >= record:
            warnings.append(f"После {n_text(declared)} точек в файле остались непрочитанные данные: "
                            f"{n_text(end - after)} Б.")  # fmt: skip
        return declared
    end = min([m for m in marks if start <= m <= size], default=size)
    have = (end - start) // record
    if declared == 0:
        if have == 0:
            raise fail("no_points", "В облаке LAS нет ни одной точки.", "Выберите файл с точками.")
        warnings.append(f"В заголовке LAS не указано число точек: по размеру файла их {n_text(have)}.")
    elif have == 0:
        raise fail("truncated", f"Файл LAS обрезан: в заголовке {n_text(declared)} точек, в файле нет ни одной целой "
                   "записи.", _HINT_COPY)  # fmt: skip
    else:
        warnings.append(f"Файл обрезан: в заголовке {n_text(declared)} точек, в файле {n_text(have)} целых записей — "
                        "загружены они.")  # fmt: skip
    return have


def read_header(path: str | Path) -> LasHeader:
    """The header of a LAS file, its VLRs and EVLRs, checked (see the module docstring)."""
    path = Path(path)
    if path.suffix.lower() == ".laz":
        raise laz_error()
    size = path.stat().st_size
    if size == 0:
        raise fail("empty_file", "Файл пуст.", "Проверьте, что файл скопирован полностью.")
    with path.open("rb") as f:
        head = f.read(max(HEADER_SIZES.values()))
        if head[:4] != SIGNATURE:
            raise fail("not_las", "Файл не является облаком LAS: в его начале нет подписи «LASF».",
                       "Выберите файл .las; текстовое облако загружается как XYZ, CSV или TXT.")  # fmt: skip
        if len(head) < 26:
            raise fail("truncated", f"Файл LAS обрезан: от заголовка осталось {len(head)} Б.", _HINT_COPY)
        major, minor = head[24], head[25]
        if major != 1 or minor not in HEADER_SIZES:
            raise fail("unsupported_version", f"Версия LAS {major}.{minor} не поддерживается: читаются версии "
                       "1.1–1.4.", "Сохраните облако как LAS 1.2 или 1.4 (CloudCompare, LAStools).")  # fmt: skip
        need = HEADER_SIZES[minor]
        if len(head) < need:
            raise fail("truncated", f"Файл LAS обрезан: заголовок версии {major}.{minor} занимает {need} Б, в файле "
                       f"{len(head)} Б.", _HINT_COPY)  # fmt: skip
        (_, source_id, encoding, guid, _, _, system_id, software, day, year, header_size, to_points, n_vlrs,
         fmt, record, declared, *rest) = HEAD.unpack_from(head)  # fmt: skip
        returns, scale, offset, box = tuple(rest[:5]), tuple(rest[5:8]), tuple(rest[8:11]), rest[11:17]
        if fmt & 0xC0:  # bit 7 (and 6 of old versions) is set by LASzip
            raise laz_error()
        if fmt not in FORMAT_LENGTH:
            raise fail("unsupported_format", f"Формат точек {fmt} не поддерживается: читаются форматы 0–10.")
        if header_size < need:
            raise fail("broken", f"Заголовок LAS повреждён: его длина указана как {header_size} Б, а в версии "
                       f"{major}.{minor} она не меньше {need} Б.")  # fmt: skip
        if to_points < header_size:
            raise fail("broken", f"Заголовок LAS повреждён: точки начинаются с байта {n_text(to_points)}, внутри "
                       f"заголовка длиной {header_size} Б.")  # fmt: skip
        if record < FORMAT_LENGTH[fmt]:
            raise fail("bad_record_length", f"Длина записи точки в заголовке — {record} Б, а формат точек {fmt} "
                       f"занимает {FORMAT_LENGTH[fmt]} Б.")  # fmt: skip
        for axis, s, o in zip("XYZ", scale, offset, strict=True):
            if not math.isfinite(s) or s == 0.0:
                raise fail("bad_scale", f"Масштаб координаты {axis} в заголовке LAS равен {s:g}: координаты точек "
                           "по нему не вычислить.")  # fmt: skip
            if not math.isfinite(o):
                raise fail("bad_offset", f"Смещение координаты {axis} в заголовке LAS равно {o:g}: координаты "
                           "точек по нему не вычислить.")  # fmt: skip
        if to_points > size:
            raise fail("truncated", f"Файл LAS обрезан: точки начинаются с байта {n_text(to_points)}, а в файле "
                       f"{n_text(size)} Б.", _HINT_COPY)  # fmt: skip
        warnings: list[str] = []
        if minor == 1:
            warnings.append("Версия LAS 1.1: файл прочитан как LAS 1.2.")
        if fmt > LAST_FORMAT[minor]:
            warnings.append(f"Формат точек {fmt} не предусмотрен версией LAS {major}.{minor}: точки прочитаны как "
                            f"записи формата {fmt}.")  # fmt: skip
        marks = [struct.unpack_from("<Q", head, 227)[0]] if minor >= 3 else []  # internal wave packets
        evlr_start = n_evlrs = 0
        if minor >= 4:
            evlr_start, n_evlrs, declared64, *returns64 = HEAD14.unpack_from(head, 235)
            if declared64:  # zero in a file whose writer only raised the version: the legacy fields count
                declared, returns = declared64, tuple(returns64)
        found = _scan(f, header_size, to_points, n_vlrs, False, warnings)
        if n_evlrs and to_points <= evlr_start <= size:
            marks.append(evlr_start)
            found += _scan(f, evlr_start, size, n_evlrs, True, warnings)
        elif n_evlrs:
            warnings.append(f"Записи EVLR начинаются с байта {n_text(evlr_start)}, за концом файла: они не прочитаны.")
    count = _count(declared, size, to_points, record, [m for m in marks if m], warnings)
    wkt, keys = _crs(found, warnings)
    codes = [keys.get(k) for k in ("3072", "2048")] if keys else []  # 32767 — «user-defined», 0 — «undefined»
    epsg = next((c for c in codes if isinstance(c, int) and 0 < c < 32767), None)
    geographic = _geographic(wkt, keys)
    if geographic:
        warnings.append("Координаты облака — в градусах (географическая система координат): фильтр земли считает "
                        "в метрах.")  # fmt: skip
    return LasHeader(
        (major, minor), fmt, record, record - FORMAT_LENGTH[fmt], header_size, to_points, count, declared,
        scale, offset, _bbox(box), encoding, source_id, guid, _text(system_id), _text(software), (year, day),
        returns, tuple(v for v, _ in found), wkt, keys, epsg, geographic, tuple(warnings),
    )  # fmt: skip


__all__ = [
    "ASCII_RECORD", "DOUBLES_RECORD", "ENCODING_WKT", "FORMAT_LENGTH", "HEAD", "HEAD14", "HEADER_SIZES",
    "KEYS_RECORD", "LAST_FORMAT", "MAX_RECORDS", "MAX_WKT", "PROJECTION", "SIGNATURE", "WKT_RECORD", "LasHeader",
    "LasVlr", "fail", "laz_error", "n_text", "point_dtype", "read_header",
]  # fmt: skip
