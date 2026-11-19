"""OMF v1 file (ТЗ 7.24, 9.8; ADR 0017 §1): the header, the JSON of the project, binary blocks inflated in
portions within the limits of S4. The composition for the import wizard (JSON only) — `omf_compose.py`.

Layout (little-endian), as omf 0.9 / 1.0 and Leapfrog write it:

    0   4   magic 84 83 82 81
    4   32  version "OMF-v0.9.0", NUL-padded
    36  16  UUID of the project
    52  8   uint64: offset of the JSON
    60  …   binary blocks, each one zlib stream
    J   …   JSON to the end of the file: {uid: {"__class__": …, …}}; objects refer to each other by uid

Numeric arrays (ScalarArray, Vector2/3Array, Int2/3Array) point to a block {start, length, dtype};
StringArray, DateTimeArray and ColorArray keep their values in the JSON; an ImageTexture keeps a PNG, which is
never inflated here. Cells and nodes of grids go in Fortran order (u fastest, then v, then w).

A ZIP («PK») is OMF 2 (omf-python 2, Incline Design): refused with `omf2_not_supported` and a hint.

Limits against «bombs» (S4): the JSON offset within [60, size), the JSON ≤ 256 MiB and ≤ 10⁶ objects; a block
within [60, JSON) with a dtype of the list; a block is inflated by `zlib.decompressobj` in portions of ≤ 16 MiB
(`max_length`): a size known in advance must match exactly, otherwise the output is at most 1 032 × the
compressed length (the bound of deflate) and 2³¹ values. Nothing is allocated for more than the limit.

Errors — `FormatError` {code, message, hint}, Russian texts: omf_damaged, omf2_not_supported, omf_version,
omf_too_large, out_of_memory (disk_full — `omf_volume`).

    sniff(head)          "v1", "omf2" (a ZIP) or None
    open_omf(path)       OmfFile: objects, project, elements(), block_of(), inflate(), read(), array()
    vector, tensor, axis_of, placement      geometry values of the JSON, checked
    parse_crs, is_dtm, unit_of, plain_description   the description lines BF2 writes (ADR 0017 решение 8)

Ported from BF `formats/omf.py` (OmfFile, vector, tensor, _axis_of; lines 1–360). Differences: codes and hints,
the limits, inflating in portions with a callback (progress and cancel), numpy only inside the functions that
need it (ADR 0002: nothing heavy before BF-READY).
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
import uuid
import zlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, TypeGuard

from bf_core.formats.errors import FormatError

if TYPE_CHECKING:
    import numpy as np

FORMAT = "omf"
FORMAT_NAME = "OMF v1"
MAGIC = b"\x84\x83\x82\x81"
VERSION = b"OMF-v0.9.0"
HEADER_SIZE = 60
ZIP_MAGIC = b"PK"

MAX_JSON = 256 << 20
MAX_OBJECTS = 1_000_000
MAX_DICTS = 4 * MAX_OBJECTS  # every JSON object, nested ones too (a block descriptor is one)
CHUNK = 16 << 20  # input and output portions of inflating
MAX_RATIO = 1032  # deflate never inflates more than this (zlib streams stay below it)
MAX_VALUES = 1 << 31
MAX_VOLUME_CELLS = 200_000_000  # ADR 0017 решение 14
MAX_GRID_TRIANGLES = 50_000_000  # of a grid surface (`mesh_io.MAX_TRIANGLES`): its nodes come from the JSON
AXIS_TOL = 1e-9

ARRAY_WIDTH = {"ScalarArray": 1, "Vector2Array": 2, "Vector3Array": 3, "Int2Array": 2, "Int3Array": 3}
LIST_ARRAYS = ("StringArray", "DateTimeArray", "ColorArray")
DTYPES = {
    "<f8": 8, "<f4": 4, "<i8": 8, "<i4": 4, "<i2": 2, "<u8": 8, "<u4": 4, "<u2": 2,
    "|u1": 1, "<u1": 1, "|i1": 1, "<i1": 1, "|b1": 1,
}  # fmt: skip

#: Lines of descriptions BF2 writes (ADR 0017 решение 8) and reads back.
CRS_PREFIX = "Система координат:"
DTM_MARK = "BedrockForge: ЦТМ"
UNIT_PREFIX = "Единицы:"

HINT_V1 = "Сохраните проект в OMF v1 (в Leapfrog — OMF 0.9) и загрузите этот файл."
HINT_DAMAGED = "Файл повреждён или обрезан: сохраните его заново в исходной программе (OMF v1)."
HINT_LARGE = "Разделите проект на несколько файлов OMF."
HINT_MEMORY = "Закройте другие программы или разделите проект на несколько файлов OMF."


Tick = Callable[[int], None]


def damaged(message: str) -> FormatError:
    return FormatError("omf_damaged", message, HINT_DAMAGED)


def too_large(message: str) -> FormatError:
    return FormatError("omf_too_large", message, HINT_LARGE)


def out_of_memory(message: str, hint: str = HINT_MEMORY) -> FormatError:
    return FormatError("out_of_memory", message, hint)


def n_text(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def shown(value: Any, limit: int = 80) -> str:
    """Text of a file put into a message: control characters replaced, cut."""
    s = "".join(c if c.isprintable() else "?" for c in str(value))
    return s if len(s) <= limit else s[: limit - 1] + "…"


def sniff(head: bytes) -> str | None:
    """«v1» — the signature of OMF v1, «omf2» — a ZIP (OMF 2), None — something else."""
    if head[:4] == MAGIC:
        return "v1"
    if head[:2] == ZIP_MAGIC:
        return "omf2"
    return None


def _is_int(x: Any) -> TypeGuard[int]:
    return isinstance(x, int) and not isinstance(x, bool)


def vector(value: Any, what: str, default: Sequence[float] | None = None) -> tuple[float, float, float]:
    """Three finite numbers (a JSON list); None — `default` when given."""
    if value is None and default is not None:
        value = default
    if not isinstance(value, list | tuple) or len(value) != 3:
        raise damaged(f"{what}: нужен вектор из трёх чисел.")
    out: list[float] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
            raise damaged(f"{what}: в векторе должны быть три числа.")
        out.append(float(v))
    return out[0], out[1], out[2]


def tensor(value: Any, what: str) -> list[float]:
    """Steps of a grid: a non-empty JSON list of positive finite numbers."""
    if not isinstance(value, list) or not value:
        raise damaged(f"{what}: нужен список шагов сетки.")
    out: list[float] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, int | float) or not (math.isfinite(v) and v > 0):
            raise damaged(f"{what}: шаги сетки должны быть положительными числами.")
        out.append(float(v))
    return out


def axis_of(v: Sequence[float], tol: float = AXIS_TOL) -> tuple[int, float] | None:
    """(coordinate axis 0–2, sign ±1) when `v` points along X, Y or Z within `tol`, else None."""
    n = math.sqrt(sum(x * x for x in v))
    if n == 0 or not math.isfinite(n):
        return None
    u = [x / n for x in v]
    k = max(range(3), key=lambda i: abs(u[i]))
    if abs(abs(u[k]) - 1.0) > tol:
        return None
    return k, 1.0 if u[k] > 0 else -1.0


_EYE = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def placement(geometry: dict[str, Any], what: str) -> list[tuple[int, float]] | None:
    """(coordinate axis, sign) of the grid axes u, v, w of a VolumeGridGeometry; None — the grid is rotated."""
    axes = [vector(geometry.get(f"axis_{a}"), f"{what}: ось {a}", default=d) for a, d in zip("uvw", _EYE, strict=True)]
    placed = [axis_of(a) for a in axes]
    if any(p is None for p in placed) or len({p[0] for p in placed if p is not None}) != 3:
        return None
    return [p for p in placed if p is not None]


def _lines_of(text: str) -> list[str]:
    return [s.strip() for s in text.splitlines() if s.strip()]


def parse_crs(description: Any) -> dict[str, Any] | None:
    """{name, epsg} of the line «Система координат: ‹имя› (EPSG:…)» BF2 writes into the description of a project;
    None — there is none."""
    for line in _lines_of(str(description or "")):
        if line.startswith(CRS_PREFIX):
            text = line[len(CRS_PREFIX) :].strip()
            m = re.search(r"\(EPSG:\s*(\d{1,9})\)\s*$", text)
            name = text[: m.start()].strip() if m else text
            epsg = int(m.group(1)) if m else None
            if name or epsg is not None:
                return {"name": shown(name, 200) or f"EPSG:{epsg}", "epsg": epsg}
    return None


def is_dtm(description: Any) -> bool:
    """The element is a ЦТМ written by BF2: its description has the line «BedrockForge: ЦТМ»."""
    return DTM_MARK in _lines_of(str(description or ""))


def unit_of(description: Any) -> str | None:
    """The unit of a datum: the line «Единицы: g/t» of its description."""
    for line in _lines_of(str(description or "")):
        if line.startswith(UNIT_PREFIX):
            return shown(line[len(UNIT_PREFIX) :].strip(), 32) or None
    return None


def plain_description(description: Any) -> str:
    """The description without the lines BF2 adds (СК, ЦТМ, единицы)."""
    keep = [s for s in str(description or "").splitlines() if not _bf2_line(s.strip())]
    return "\n".join(keep).strip()


def _bf2_line(line: str) -> bool:
    return line == DTM_MARK or line.startswith(CRS_PREFIX) or line.startswith(UNIT_PREFIX)


class _TooMany(Exception):
    pass


@dataclass(frozen=True)
class Block:
    """A binary block of an array: where it is, its dtype and how many values make a row."""

    start: int
    length: int
    dtype: str
    width: int = 1

    @property
    def itemsize(self) -> int:
        return DTYPES[self.dtype]

    @property
    def row_bytes(self) -> int:
        return self.width * self.itemsize


class OmfFile:
    """An open OMF v1 file: the JSON is read at once, binary blocks when an array is asked for.

    `tick(read)` — called while a block is inflated (after every portion of ≤ 16 MiB) with the compressed bytes
    read since the file was opened: progress, and a chance for a job to stop (it may raise)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.tick: Tick | None = None
        self.read_bytes = 0
        self._f: BinaryIO = self.path.open("rb")
        try:
            self.size = os.fstat(self._f.fileno()).st_size
            self.version, self.uid, self.json_start = self._header()
            self.objects = self._json()
            self.project = self._project()
        except BaseException:
            self._f.close()
            raise

    def __enter__(self) -> OmfFile:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._f.close()

    # --- header and JSON ------------------------------------------------------------------------------

    def _header(self) -> tuple[str, str, int]:
        head = self._f.read(HEADER_SIZE)
        kind = sniff(head)
        if kind == "omf2":
            raise FormatError(
                "omf2_not_supported", "Файл — OMF 2 (архив ZIP): эта версия OMF не загружается, только OMF v1.", HINT_V1
            )
        if not head:
            raise damaged("Файл пуст.")
        if kind != "v1":
            raise damaged("Это не файл OMF: в начале нет сигнатуры OMF.")
        if len(head) < HEADER_SIZE:
            raise damaged("Файл OMF обрезан: заголовок неполный.")
        version = head[4:36].rstrip(b"\x00 ")
        if version != VERSION:
            text = shown(version.decode("ascii", "replace"), 32) or "не указана"
            raise FormatError(
                "omf_version", f"Версия OMF «{text}» не поддерживается: загружается OMF v1 (OMF-v0.9.0).", HINT_V1
            )
        uid = str(uuid.UUID(bytes=head[36:52]))
        (json_start,) = struct.unpack("<Q", head[52:60])
        if not HEADER_SIZE <= json_start < self.size:
            raise damaged("Файл OMF обрезан или повреждён: описание проекта (JSON) за пределами файла.")
        return VERSION.decode(), uid, int(json_start)

    def _json(self) -> dict[str, dict[str, Any]]:
        n = self.size - self.json_start
        if n > MAX_JSON:
            raise too_large(
                f"Описание проекта OMF (JSON) — {n_text(n >> 20)} МиБ, больше предела {MAX_JSON >> 20} МиБ."
            )
        self._f.seek(self.json_start)
        raw = self._f.read(n)
        if len(raw) != n:
            raise damaged("Файл OMF обрезан: описание проекта (JSON) неполное.")
        count = 0

        def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            nonlocal count
            count += 1
            if count > MAX_DICTS:
                raise _TooMany
            return dict(pairs)

        try:
            data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=hook)
        except _TooMany:
            raise too_large(f"В описании проекта OMF больше {n_text(MAX_OBJECTS)} объектов.") from None
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise damaged("Описание проекта OMF (JSON) повреждено: файл обрезан или изменён.") from None
        except MemoryError:
            raise out_of_memory("Не хватило памяти на описание проекта OMF (JSON).") from None
        del raw
        if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, dict) for k, v in data.items()):
            raise damaged("Описание проекта OMF (JSON) имеет неверную структуру.")
        if len(data) > MAX_OBJECTS:
            raise too_large(f"В описании проекта OMF больше {n_text(MAX_OBJECTS)} объектов.")
        return data

    def _project(self) -> dict[str, Any]:
        project = self.objects.get(self.uid)
        if project is None or project.get("__class__") != "Project":  # some writers put another uid in the header
            found = [v for v in self.objects.values() if v.get("__class__") == "Project"]
            if len(found) != 1:
                raise damaged("В файле OMF не найден проект." if not found else "В файле OMF несколько проектов.")
            project = found[0]
        return project

    # --- objects --------------------------------------------------------------------------------------

    def get(self, uid: Any, what: str, cls: str | tuple[str, ...] | None = None) -> dict[str, Any]:
        obj = self.objects.get(uid) if isinstance(uid, str) else None
        if obj is None:
            raise damaged(f"{what}: ссылка на отсутствующий объект.")
        if cls is not None:
            allowed = (cls,) if isinstance(cls, str) else cls
            if obj.get("__class__") not in allowed:
                raise damaged(f"{what}: ожидается {' или '.join(allowed)}, в файле — {shown(obj.get('__class__'))}.")
        return obj

    def elements(self) -> list[tuple[str, dict[str, Any]]]:
        """(uid, element) of the project in its order; a uid listed twice counts once."""
        uids = self.project.get("elements") or []
        if not isinstance(uids, list):
            raise damaged("Список элементов проекта OMF имеет неверную структуру.")
        seen: set[str] = set()
        out: list[tuple[str, dict[str, Any]]] = []
        for k, u in enumerate(uids):
            if isinstance(u, str) and u in seen:
                continue
            out.append((u, self.get(u, f"Элемент {k + 1} проекта OMF")))
            seen.add(u)
        return out

    def origin(self) -> tuple[float, float, float]:
        return vector(self.project.get("origin"), "Начало координат проекта OMF", default=(0.0, 0.0, 0.0))

    # --- arrays ---------------------------------------------------------------------------------------

    def block_of(self, uid: Any, what: str) -> Block | list[Any]:
        """The block of a numeric array, or the values of a String/DateTime/ColorArray (kept in the JSON)."""
        obj = self.get(uid, what)
        cls = obj.get("__class__")
        if cls in LIST_ARRAYS:
            values = obj.get("array")
            if not isinstance(values, list):
                raise damaged(f"{what}: значения массива должны быть списком.")
            return values
        width = ARRAY_WIDTH.get(cls) if isinstance(cls, str) else None
        if width is None:
            raise damaged(f"{what}: неизвестный тип массива «{shown(cls)}».")
        return self.block(obj.get("array"), what, width)

    def block(self, desc: Any, what: str, width: int = 1) -> Block:
        if not isinstance(desc, dict):
            raise damaged(f"{what}: нет ссылки на двоичные данные.")
        raw_start, raw_length, dtype = desc.get("start"), desc.get("length"), desc.get("dtype")
        if not (_is_int(raw_start) and _is_int(raw_length)):
            raise damaged(f"{what}: неверное описание двоичного блока.")
        start, length = int(raw_start), int(raw_length)
        if dtype not in DTYPES:
            raise damaged(f"{what}: тип данных «{shown(dtype, 20)}» не поддерживается.")
        if start < HEADER_SIZE or length < 1 or start + length > self.json_start:
            raise damaged(f"{what}: двоичный блок за пределами данных файла (файл обрезан или повреждён).")
        return Block(start, length, str(dtype), width)

    def inflate(self, block: Block, what: str, rows: int | None = None, portion: int | None = None) -> Iterator[bytes]:
        """The bytes of a block in portions of ≤ `portion` (default `CHUNK`) read and inflated at a time. `rows` —
        the size known in advance: exactly rows × width values; otherwise at most `MAX_RATIO` × the block length
        and `MAX_VALUES` values. Several blocks may be inflated in turn (each keeps its own place in the file)."""
        step = max(int(portion or CHUNK), 1)
        if rows is not None:
            limit = rows * block.row_bytes
        else:
            limit = min(MAX_RATIO * block.length, MAX_VALUES * block.itemsize)
        d = zlib.decompressobj()
        pos, left, tail, out, counted = block.start, block.length, b"", 0, 0
        try:
            while not d.eof:
                if not tail:
                    if not left:
                        break
                    self._f.seek(pos)
                    tail = self._f.read(min(left, step))
                    if not tail:
                        raise damaged(f"{what}: файл обрезан внутри двоичного блока.")
                    left -= len(tail)
                    pos += len(tail)
                part = d.decompress(tail, min(step, limit - out + 1))
                tail = d.unconsumed_tail
                out += len(part)
                if out > limit:
                    raise self._over(what, rows, limit)
                if part:
                    yield part
                consumed = block.length - left - len(tail)
                self.read_bytes += consumed - counted
                counted = consumed
                if self.tick is not None:
                    self.tick(self.read_bytes)
            if not d.eof:
                part = d.flush()  # pending output of the last input: a few hundred bytes at most
                out += len(part)
                if out > limit:
                    raise self._over(what, rows, limit)
                if part:
                    yield part
        except zlib.error:
            raise damaged(f"{what}: двоичный блок повреждён (данные zlib не распаковываются).") from None
        if not d.eof:
            raise damaged(f"{what}: двоичный блок обрезан.")
        self.read_bytes += block.length - counted
        if rows is not None and out != limit:
            raise damaged(f"{what}: в двоичном блоке {n_text(out // block.row_bytes)} значений вместо {n_text(rows)}.")
        if out % block.row_bytes:
            raise damaged(f"{what}: размер данных не кратен типу {block.dtype} × {block.width}.")

    @staticmethod
    def _over(what: str, rows: int | None, limit: int) -> FormatError:
        if rows is not None:
            return damaged(f"{what}: в двоичном блоке больше значений, чем {n_text(rows)}.")
        return too_large(
            f"{what}: двоичный блок распаковывается больше чем в {MAX_RATIO} раз или больше чем на "
            f"{n_text(MAX_VALUES)} значений — файл повреждён или это «бомба»."
        )

    def read(self, block: Block, what: str, rows: int | None = None) -> np.ndarray:
        """The values of a block: (n,) for a width of 1, else (n, width); no copy beyond the inflated bytes."""
        import numpy as np

        try:
            if rows is not None:
                buf = np.empty(rows * block.row_bytes, dtype=np.uint8)
                pos = 0
                for part in self.inflate(block, what, rows):
                    buf[pos : pos + len(part)] = np.frombuffer(part, dtype=np.uint8)
                    pos += len(part)
            else:
                acc = bytearray()
                for part in self.inflate(block, what):
                    acc += part
                buf = np.frombuffer(acc, dtype=np.uint8)
        except MemoryError:
            raise out_of_memory(f"{what}: не хватило памяти на данные.") from None
        values = buf.view(np.dtype(block.dtype))
        return values if block.width == 1 else values.reshape(-1, block.width)

    def array(self, uid: Any, what: str, rows: int | None = None) -> np.ndarray | list[Any]:
        """A numeric array as numpy, or the list of a String/DateTime/ColorArray (`rows` checked too)."""
        ref = self.block_of(uid, what)
        if isinstance(ref, list):
            if rows is not None and len(ref) != rows:
                raise damaged(f"{what}: {n_text(len(ref))} значений вместо {n_text(rows)}.")
            return ref
        return self.read(ref, what, rows)


def open_omf(path: str | Path) -> OmfFile:
    return OmfFile(path)


def name_of(obj: dict[str, Any], fallback: str) -> str:
    """The name of an object as the project shows it: control characters → space, spaces collapsed, ≤ 200."""
    raw = obj.get("name")
    text = " ".join("".join(c if c.isprintable() else " " for c in raw).split()) if isinstance(raw, str) else ""
    return text[:200] or fallback


def color_hex(value: Any) -> str | None:
    """OMF keeps a colour as [r, g, b] (0–255); "#rrggbb" is accepted too."""
    if isinstance(value, str):
        s = value.strip().lower()
        return s if re.fullmatch(r"#[0-9a-f]{6}", s) else None
    if not isinstance(value, list | tuple) or len(value) != 3:
        return None
    if not all(_is_int(c) and 0 <= c <= 255 for c in value):
        return None
    r, g, b = value
    return f"#{r:02x}{g:02x}{b:02x}"
