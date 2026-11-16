"""Open Mining Format (OMF) v1 — the file format of OMF 0.9 / 1.0, read and written by Leapfrog Geo.

Layout of a file (little-endian):

    0   4   magic 84 83 82 81
    4   32  version, starts with "OMF-v0.9.0", NUL-padded
    36  16  UUID of the project
    52  8   uint64: offset of the JSON
    60  …   binary blocks, each one zlib stream
    J   …   JSON to the end of the file: {uid: {"__class__": ..., ...}}; objects refer to each other by uid

Numeric arrays (ScalarArray, Vector2/3Array, Int2/3Array) point to a block {start, length, dtype};
StringArray, DateTimeArray and ColorArray keep their values in the JSON. Cell and node order of
grids is Fortran (u changes fastest), as in omf / omf-python and omfvista.

The library `omf` is not used: it needs numpy < 2 and the `properties` package, which does not build
on Python 3.12+. Its v1 reader (omf/compat/omf_v1.py of omf-python) is the reference of this one.
"""

from __future__ import annotations

import json
import struct
import uuid
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

import numpy as np

from app.formats.blockmodel import BlockTable, RegularGrid
from app.formats.lines import LineLayer, Polyline
from app.formats.micromine import Column
from app.formats.tabular import unique_names
from app.formats.wireframes import Mesh

Progress = Callable[[float, str], None]

MAGIC = b"\x84\x83\x82\x81"
VERSION = b"OMF-v0.9.0"
HEADER_SIZE = 60
FORMAT_NAME = "OMF v1"

_SHAPES = {"ScalarArray": (-1,), "Vector2Array": (-1, 2), "Vector3Array": (-1, 3), "Int2Array": (-1, 2), "Int3Array": (-1, 3)}
_LIST_ARRAYS = ("StringArray", "DateTimeArray", "ColorArray")
_DTYPES = {"<f8", "<f4", "<i8", "<i4", "<i2", "<u8", "<u4", "<u2", "|u1", "<u1", "|i1", "<i1", "|b1"}
_AXES = np.eye(3)
_UNIT_PREFIX = "Единицы: "
# A plain grid without values or with few attributes is fine; a 2 GB block model gives about this many.
MAX_VOLUME_CELLS = 200_000_000
_CHUNK = 16 << 20

ELEMENT_TITLES = {
    "PointSetElement": "Точки",
    "LineSetElement": "Линии",
    "SurfaceElement": "Поверхность",
    "VolumeElement": "Объём (блоки)",
}


class OmfFormatError(ValueError):
    """The file is not a readable OMF v1 file; the message is shown to the user."""


def _noop(_f: float, _m: str) -> None:
    pass


def is_omf(head: bytes) -> bool:
    return head[:4] == MAGIC


def color_hex(value) -> str | None:
    """OMF keeps a colour as [r, g, b] (0–255); "#rrggbb" strings are accepted too."""
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        return value.lower()
    try:
        r, g, b = (int(v) for v in value)
    except (TypeError, ValueError):
        return None
    if not all(0 <= c <= 255 for c in (r, g, b)):
        return None
    return f"#{r:02x}{g:02x}{b:02x}"


def color_rgb(value: str | None) -> list[int] | None:
    if not value or len(value) != 7 or not value.startswith("#"):
        return None
    try:
        return [int(value[i:i + 2], 16) for i in (1, 3, 5)]
    except ValueError:
        return None


# ------------------------------------------------------------------------------------- reading
class OmfFile:
    """An open OMF v1 file: the JSON is read at once, binary blocks when an array is asked for."""

    def __init__(self, path: Path):
        self.path = Path(path)
        # Called with the share (0..1) of the binary block being read: progress, and a chance for
        # the job to stop (a single block of a big model can take minutes to inflate).
        self.tick: Callable[[float], None] | None = None
        self._f: BinaryIO = self.path.open("rb")
        try:
            self.size = self.path.stat().st_size
            self.project_uid, self.json_start = self._header()
            self.objects = self._json()
            self.project = self._project()
        except BaseException:
            self._f.close()
            raise

    def __enter__(self) -> OmfFile:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._f.close()

    def _header(self) -> tuple[str, int]:
        head = self._f.read(HEADER_SIZE)
        if len(head) < HEADER_SIZE or head[:4] != MAGIC:
            raise OmfFormatError("Это не файл OMF: нет сигнатуры в начале файла")
        version = head[4:36].rstrip(b"\x00")
        if not version.startswith(VERSION):
            text = version.decode("ascii", "replace") or "не указана"
            raise OmfFormatError(f"Версия OMF «{text}» не поддерживается: читается OMF v1 (OMF-v0.9.0, Leapfrog OMF 0.9)")
        project_uid = str(uuid.UUID(bytes=head[36:52]))
        (json_start,) = struct.unpack("<Q", head[52:60])
        if not HEADER_SIZE <= json_start < self.size:
            raise OmfFormatError("Файл OMF обрезан или повреждён: описание проекта (JSON) за пределами файла")
        return project_uid, json_start

    def _json(self) -> dict:
        self._f.seek(self.json_start)
        raw = self._f.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OmfFormatError(f"Описание проекта OMF (JSON) повреждено: {exc}") from None
        if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
            raise OmfFormatError("Описание проекта OMF (JSON) имеет неверную структуру")
        return data

    def _project(self) -> dict:
        project = self.objects.get(self.project_uid)
        if project is None:  # some writers put another uid in the header
            found = [v for v in self.objects.values() if v.get("__class__") == "Project"]
            if len(found) != 1:
                raise OmfFormatError("В файле OMF не найден проект")
            project = found[0]
        if project.get("__class__") != "Project":
            raise OmfFormatError("В файле OMF не найден проект")
        return project

    # -- objects
    def get(self, uid, what: str, cls: str | tuple[str, ...] | None = None) -> dict:
        obj = self.objects.get(uid) if isinstance(uid, str) else None
        if obj is None:
            raise OmfFormatError(f"OMF: ссылка на отсутствующий объект ({what})")
        if cls is not None:
            allowed = (cls,) if isinstance(cls, str) else cls
            if obj.get("__class__") not in allowed:
                raise OmfFormatError(f"OMF: {what} — ожидается {' или '.join(allowed)}, в файле {obj.get('__class__')}")
        return obj

    def elements(self) -> list[tuple[str, dict]]:
        uids = self.project.get("elements") or []
        if not isinstance(uids, list):
            raise OmfFormatError("OMF: список элементов проекта имеет неверную структуру")
        return [(u, self.get(u, "элемент проекта")) for u in uids]

    def origin(self) -> np.ndarray:
        return vector(self.project.get("origin"), "начало координат проекта", default=(0.0, 0.0, 0.0))

    # -- arrays
    def array(self, uid, what: str) -> np.ndarray | list:
        """A numeric array as numpy (shape from its class), or the list of a String/DateTime/ColorArray."""
        obj = self.get(uid, what)
        cls = obj.get("__class__")
        if cls in _LIST_ARRAYS:
            values = obj.get("array")
            if not isinstance(values, list):
                raise OmfFormatError(f"OMF: {what} — значения не список")
            return values
        if cls not in _SHAPES:
            raise OmfFormatError(f"OMF: {what} — неизвестный тип массива {cls}")
        block = obj.get("array")
        if not isinstance(block, dict):
            raise OmfFormatError(f"OMF: {what} — нет ссылки на двоичные данные")
        arr = self._block(block, what)
        try:
            return arr.reshape(_SHAPES[cls])
        except ValueError:
            raise OmfFormatError(f"OMF: {what} — длина массива не делится на {_SHAPES[cls][-1]}") from None

    def _block(self, block: dict, what: str) -> np.ndarray:
        try:
            start, length, dtype = int(block["start"]), int(block["length"]), str(block["dtype"])
        except (KeyError, TypeError, ValueError):
            raise OmfFormatError(f"OMF: {what} — неверное описание двоичного блока") from None
        if dtype not in _DTYPES:
            raise OmfFormatError(f"OMF: {what} — тип данных {dtype} не поддерживается")
        if start < HEADER_SIZE or length < 0 or start + length > self.json_start:
            raise OmfFormatError(f"OMF: {what} — двоичный блок за пределами данных файла (файл обрезан?)")
        self._f.seek(start)
        out = bytearray()
        d = zlib.decompressobj()
        left = length
        try:
            while left > 0:
                chunk = self._f.read(min(left, _CHUNK))
                if not chunk:
                    raise OmfFormatError(f"OMF: {what} — файл обрезан")
                left -= len(chunk)
                out += d.decompress(chunk)
                if self.tick is not None and length:
                    self.tick(1.0 - left / length)
            out += d.flush()
        except zlib.error as exc:
            raise OmfFormatError(f"OMF: {what} — двоичный блок повреждён ({exc})") from None
        if not d.eof:
            raise OmfFormatError(f"OMF: {what} — двоичный блок обрезан")
        dt = np.dtype(dtype)
        if len(out) % dt.itemsize:
            raise OmfFormatError(f"OMF: {what} — размер данных не кратен типу {dtype}")
        return np.frombuffer(out, dtype=dt)  # no copy: a 47-million-value column is 380 MB

    def image_bytes(self, block: dict, what: str) -> bytes:
        return self._block({**block, "dtype": "|u1"}, what).tobytes()


def vector(value, what: str, default=None) -> np.ndarray:
    if value is None and default is not None:
        value = default
    try:
        v = np.asarray(value, dtype=np.float64).reshape(3)
    except (TypeError, ValueError):
        raise OmfFormatError(f"OMF: {what} — нужен вектор из трёх чисел") from None
    if not np.isfinite(v).all():
        raise OmfFormatError(f"OMF: {what} — пустые значения в векторе")
    return v


def tensor(value, what: str) -> np.ndarray:
    try:
        t = np.asarray(value, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError):
        raise OmfFormatError(f"OMF: {what} — нужен список чисел") from None
    if not len(t) or not np.isfinite(t).all() or (t <= 0).any():
        raise OmfFormatError(f"OMF: {what} — шаги сетки должны быть положительными числами")
    return t


@dataclass
class OmfContent:
    project: str
    description: str
    meshes: list[Mesh] = field(default_factory=list)
    line_layers: list[LineLayer] = field(default_factory=list)
    # (name, table, colour, {column name: unit})
    blocks: list[tuple[str, BlockTable, str | None, dict[str, str]]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _name(obj: dict, fallback: str) -> str:
    name = obj.get("name")
    return str(name).strip() if isinstance(name, str) and name.strip() else fallback


def _data(omf: OmfFile, element: dict, label: str) -> list[dict]:
    uids = element.get("data") or []
    if not isinstance(uids, list):
        raise OmfFormatError(f"OMF: {label} — список данных имеет неверную структуру")
    return [omf.get(u, f"данные элемента {label}") for u in uids]


def _skipped_data(items: list[dict], label: str, reason: str) -> str | None:
    if not items:
        return None
    names = ", ".join(f"«{_name(d, d.get('__class__', '?'))}» ({d.get('location', '?')})" for d in items[:10])
    more = f" и ещё {len(items) - 10}" if len(items) > 10 else ""
    return f"{label}: данные {names}{more} не перенесены — {reason}"


def _legend_labels(omf: OmfFile, data: dict, label: str) -> list[str] | None:
    """Labels of MappedData: the first legend that is not colours (numbers and dates become text)."""
    for uid in data.get("legends") or []:
        legend = omf.get(uid, f"легенда данных {label}", "Legend")
        values_obj = omf.get(legend.get("values"), f"значения легенды {label}")
        if values_obj.get("__class__") == "ColorArray":
            continue
        values = omf.array(legend.get("values"), f"значения легенды {label}")
        return [_text(v) for v in (values.tolist() if isinstance(values, np.ndarray) else values)]
    return None


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return "" if not np.isfinite(v) else f"{v:g}"
    return str(v)


def _codes_of_strings(values: list) -> tuple[np.ndarray, list[str]]:
    labels: dict[str, int] = {}
    codes = np.empty(len(values), dtype=np.int32)
    for i, v in enumerate(values):
        s = _text(v)
        if s == "":
            codes[i] = -1
        else:
            codes[i] = labels.setdefault(s, len(labels))
    return codes, list(labels)


def _column_values(omf: OmfFile, data: dict, n: int, label: str) -> tuple[str, np.ndarray, list[str] | None] | None:
    """(kind, values, labels) of one data column of n values, or None when it has no counterpart."""
    cls = data.get("__class__")
    what = f"данные «{_name(data, cls)}» элемента {label}"
    if cls == "ScalarData":
        v = omf.array(data.get("array"), what)
        if not isinstance(v, np.ndarray) or v.ndim != 1 or len(v) != n:
            raise OmfFormatError(f"OMF: {what} — длина {len(v)} не равна числу значений {n}")
        return "number", np.asarray(v, dtype=np.float64), None
    if cls == "MappedData":
        idx = omf.array(data.get("array"), what)
        if not isinstance(idx, np.ndarray) or idx.ndim != 1 or len(idx) != n:
            raise OmfFormatError(f"OMF: {what} — длина индексов не равна числу значений {n}")
        labels = _legend_labels(omf, data, label)
        if labels is None:
            return None
        if idx.dtype.kind == "f":
            idx = np.where(np.isfinite(idx), idx, -1)
        codes = idx.astype(np.int32)  # a separate array: the int64 indices are freed with idx
        codes[(idx < 0) | (idx >= len(labels))] = -1
        return "category", codes, labels
    if cls in ("StringData", "DateTimeData"):
        values = omf.array(data.get("array"), what)
        if isinstance(values, np.ndarray):
            values = values.tolist()
        if len(values) != n:
            raise OmfFormatError(f"OMF: {what} — длина {len(values)} не равна числу значений {n}")
        codes, labels = _codes_of_strings(values)
        return "category", codes, labels
    return None


def _textures_note(element: dict, label: str) -> str | None:
    textures = element.get("textures") or []
    if textures:
        return f"{label}: текстуры ({len(textures)}) не перенесены — изображения на объектах пока не поддерживаются"
    return None


def read_omf(path: Path, progress: Progress | None = None) -> OmfContent:
    """Everything of an OMF v1 file that BedrockForge can keep; the rest is listed in `warnings`."""
    progress = progress or _noop
    with OmfFile(path) as omf:
        content = OmfContent(project=_name(omf.project, Path(path).stem), description=str(omf.project.get("description") or ""))
        base = omf.origin()
        elements = omf.elements()
        if not elements:
            raise OmfFormatError("В файле OMF нет элементов")
        names_used: dict[str, int] = {}
        current = {"k": 0, "label": ""}
        omf.tick = lambda f: progress((current["k"] + 0.9 * f) / len(elements), current["label"])
        for k, (_uid, element) in enumerate(elements):
            cls = element.get("__class__")
            title = ELEMENT_TITLES.get(cls, cls)
            name = _name(element, f"{title} {k + 1}")
            if name in names_used:
                names_used[name] += 1
                name = f"{name} ({names_used[name]})"
            else:
                names_used[name] = 1
            label = f"{title} «{name}»"
            current.update(k=k, label=label)
            progress(k / len(elements), label)
            color = color_hex(element.get("color"))
            note = _textures_note(element, label)
            if note:
                content.warnings.append(note)
            if cls == "PointSetElement":
                content.warnings.append(f"{label}: не загружены — точечных объектов в BedrockForge пока нет (этап 9)")
                continue
            geometry = omf.get(element.get("geometry"), f"геометрия {label}")
            origin = base + vector(geometry.get("origin"), f"начало координат {label}", default=(0.0, 0.0, 0.0))
            data = _data(omf, element, label)
            if cls == "SurfaceElement":
                mesh = _surface(omf, geometry, name, color, origin, label)
                if element.get("description"):
                    mesh.attributes["Описание"] = str(element["description"])
                content.meshes.append(mesh)
                note = _skipped_data(data, label, "каркас хранит только геометрию")
                if note:
                    content.warnings.append(note)
            elif cls == "LineSetElement":
                layer, warnings = _lines(omf, geometry, data, name, color, origin, label)
                content.line_layers.append(layer)
                content.warnings.extend(warnings)
            elif cls == "VolumeElement":
                result = _volume(omf, geometry, data, name, origin, label)
                if isinstance(result, str):
                    content.warnings.append(result)
                    continue
                table, units, warnings = result
                content.blocks.append((name, table, color, units))
                content.warnings.extend(warnings)
            else:
                content.warnings.append(f"{label}: тип элемента {cls} не поддерживается")
        progress(1.0, "Элементы прочитаны")
    return content


def _surface(omf: OmfFile, geometry: dict, name: str, color: str | None, origin: np.ndarray, label: str) -> Mesh:
    cls = geometry.get("__class__")
    if cls == "SurfaceGeometry":
        v = omf.array(geometry.get("vertices"), f"вершины {label}")
        t = omf.array(geometry.get("triangles"), f"треугольники {label}")
        if not isinstance(v, np.ndarray) or v.ndim != 2 or v.shape[1] != 3:
            raise OmfFormatError(f"OMF: {label} — вершины должны быть массивом (N, 3)")
        if not isinstance(t, np.ndarray) or t.ndim != 2 or t.shape[1] != 3:
            raise OmfFormatError(f"OMF: {label} — треугольники должны быть массивом (M, 3)")
        vertices = v.astype(np.float64) + origin
        if len(t) and (t.min() < 0 or t.max() >= len(vertices)):
            raise OmfFormatError(f"OMF: {label} — номера вершин треугольников вне диапазона")
        if len(vertices) > np.iinfo(np.int32).max:
            raise OmfFormatError(f"OMF: {label} — слишком много вершин")
        triangles = t.astype(np.int32)
    elif cls == "SurfaceGridGeometry":
        vertices, triangles = _grid_surface(omf, geometry, origin, label)
    else:
        raise OmfFormatError(f"OMF: {label} — неизвестная геометрия поверхности {cls}")
    if not np.isfinite(vertices).all():
        raise OmfFormatError(f"OMF: {label} — координаты содержат пустые значения")
    return Mesh(name=name, vertices=np.ascontiguousarray(vertices), triangles=np.ascontiguousarray(triangles), color=color)


def _grid_surface(omf: OmfFile, geometry: dict, origin: np.ndarray, label: str) -> tuple[np.ndarray, np.ndarray]:
    """Nodes origin + u·axis_u + v·axis_v + offset_w·(axis_u × axis_v), u fastest; two triangles per cell."""
    tu = tensor(geometry.get("tensor_u"), f"tensor_u {label}")
    tv = tensor(geometry.get("tensor_v"), f"tensor_v {label}")
    au = vector(geometry.get("axis_u"), f"axis_u {label}", default=(1.0, 0.0, 0.0))
    av = vector(geometry.get("axis_v"), f"axis_v {label}", default=(0.0, 1.0, 0.0))
    au, av = au / np.linalg.norm(au), av / np.linalg.norm(av)
    if abs(float(au @ av)) > 1e-6:
        raise OmfFormatError(f"OMF: {label} — оси грида не перпендикулярны")
    aw = np.cross(au, av)
    nu, nv = len(tu) + 1, len(tv) + 1
    u = np.concatenate([[0.0], np.cumsum(tu)])
    v = np.concatenate([[0.0], np.cumsum(tv)])
    uu = np.tile(u, nv)
    vv = np.repeat(v, nu)
    vertices = origin + uu[:, None] * au + vv[:, None] * av
    if geometry.get("offset_w") is not None:
        w = omf.array(geometry.get("offset_w"), f"offset_w {label}")
        if not isinstance(w, np.ndarray) or w.ndim != 1 or len(w) != nu * nv:
            raise OmfFormatError(f"OMF: {label} — длина offset_w не равна числу узлов грида {nu * nv}")
        vertices = vertices + w.astype(np.float64)[:, None] * aw
    i = np.tile(np.arange(nu - 1), nv - 1)
    j = np.repeat(np.arange(nv - 1), nu - 1)
    a = i + j * nu
    b, c = a + 1, a + nu
    d = c + 1
    triangles = np.concatenate([np.column_stack([a, b, d]), np.column_stack([a, d, c])]).astype(np.int32)
    return vertices, triangles


def _lines(omf: OmfFile, geometry: dict, data: list[dict], name: str, color: str | None, origin: np.ndarray,
           label: str) -> tuple[LineLayer, list[str]]:
    """Segments chained in their order into polylines; data constant along a polyline becomes its attribute."""
    if geometry.get("__class__") != "LineSetGeometry":
        raise OmfFormatError(f"OMF: {label} — неизвестная геометрия линий {geometry.get('__class__')}")
    v = omf.array(geometry.get("vertices"), f"вершины {label}")
    s = omf.array(geometry.get("segments"), f"сегменты {label}")
    if not isinstance(v, np.ndarray) or v.ndim != 2 or v.shape[1] != 3:
        raise OmfFormatError(f"OMF: {label} — вершины должны быть массивом (N, 3)")
    if not isinstance(s, np.ndarray) or s.ndim != 2 or s.shape[1] != 2:
        raise OmfFormatError(f"OMF: {label} — сегменты должны быть массивом (M, 2)")
    vertices = v.astype(np.float64) + origin
    seg = s.astype(np.int64)
    if len(seg) and (seg.min() < 0 or seg.max() >= len(vertices)):
        raise OmfFormatError(f"OMF: {label} — номера вершин сегментов вне диапазона")
    if not np.isfinite(vertices).all():
        raise OmfFormatError(f"OMF: {label} — координаты содержат пустые значения")
    warnings: list[str] = []
    # Runs of segments where each one starts at the end of the previous one.
    if len(seg):
        breaks = np.flatnonzero(seg[1:, 0] != seg[:-1, 1]) + 1
        starts = np.concatenate([[0], breaks])
        ends = np.concatenate([breaks, [len(seg)]])
    else:
        starts = ends = np.zeros(0, np.int64)

    columns: list[tuple[str, list]] = []
    skipped: list[dict] = []
    for d in data:
        if d.get("location") != "segments":
            skipped.append(d)
            continue
        col = _column_values(omf, d, len(seg), label)
        if col is None:
            skipped.append(d)
            continue
        kind, values, labels = col
        if kind == "number":
            per_line = [values[a:b] for a, b in zip(starts, ends)]
            if not all(len(p) == 0 or (np.isnan(p).all() or (np.nanmin(p) == np.nanmax(p) and not np.isnan(p).any())) for p in per_line):
                skipped.append(d)
                continue
            columns.append((_name(d, "Поле"), [None if not len(p) or np.isnan(p[0]) else float(p[0]) for p in per_line]))
        else:
            per_line = [values[a:b] for a, b in zip(starts, ends)]
            if not all(len(p) == 0 or (p == p[0]).all() for p in per_line):
                skipped.append(d)
                continue
            columns.append((_name(d, "Поле"), [None if not len(p) or p[0] < 0 else labels[p[0]] for p in per_line]))
    note = _skipped_data(skipped, label, "у линии BedrockForge есть только атрибуты всей линии (данные на вершинах и меняющиеся вдоль линии не хранятся)")
    if note:
        warnings.append(note)
    names = unique_names([c[0] for c in columns])
    lines: list[Polyline] = []
    for n, (a, b) in enumerate(zip(starts, ends)):
        idx = np.concatenate([[seg[a, 0]], seg[a:b, 1]])
        closed = len(idx) > 3 and idx[0] == idx[-1]
        pts = vertices[idx]
        attrs = {names[c]: columns[c][1][n] for c in range(len(columns)) if columns[c][1][n] is not None}
        lines.append(Polyline(vertices=np.ascontiguousarray(pts), closed=bool(closed) or _closed_geom(pts), attributes=attrs))
    if not lines:
        warnings.append(f"{label}: в элементе нет сегментов — пустой слой")
    return LineLayer(name=name, lines=lines, color=color), warnings


def _closed_geom(pts: np.ndarray, tolerance: float = 1e-6) -> bool:
    return len(pts) > 2 and bool(np.all(np.abs(pts[0] - pts[-1]) <= tolerance))


def _axis_of(v: np.ndarray) -> tuple[int, float] | None:
    """(coordinate axis, sign) when v points along a coordinate axis, else None."""
    n = np.linalg.norm(v)
    if n == 0:
        return None
    u = v / n
    k = int(np.argmax(np.abs(u)))
    if abs(abs(u[k]) - 1.0) > 1e-9:
        return None
    return k, float(np.sign(u[k]))


def _volume(omf: OmfFile, geometry: dict, data: list[dict], name: str, origin: np.ndarray, label: str):
    """A tensor grid as blocks: centres and sizes of every cell, u fastest, then v, then w."""
    if geometry.get("__class__") != "VolumeGridGeometry":
        raise OmfFormatError(f"OMF: {label} — неизвестная геометрия объёма {geometry.get('__class__')}")
    tensors = [tensor(geometry.get(f"tensor_{a}"), f"tensor_{a} {label}") for a in "uvw"]
    axes = [vector(geometry.get(f"axis_{a}"), f"axis_{a} {label}", default=tuple(_AXES[i])) for i, a in enumerate("uvw")]
    placed = [_axis_of(a) for a in axes]
    if any(p is None for p in placed) or len({p[0] for p in placed}) != 3:
        return (f"{label}: не загружен — блочная модель повёрнута (оси не совпадают с X, Y, Z), "
                f"а блоки BedrockForge без поворота")
    nu, nv, nw = (len(t) for t in tensors)
    n = nu * nv * nw
    if n > MAX_VOLUME_CELLS:
        return f"{label}: не загружен — {n:,} ячеек больше предела {MAX_VOLUME_CELLS:,}".replace(",", " ")

    # Values first: cells without any value are not stored, so their coordinates are never computed.
    warnings: list[str] = []
    cols: list[Column] = []
    units: dict[str, str] = {}
    skipped: list[dict] = []
    for d in data:
        if d.get("location") != "cells":
            skipped.append(d)
            continue
        col = _column_values(omf, d, n, label)
        if col is None:
            skipped.append(d)
            continue
        kind, values, labels = col
        cname = _name(d, f"Поле {len(cols) + 1}")
        cols.append(Column(cname, kind, values, labels, source=str(d.get("__class__"))))
        desc = str(d.get("description") or "")
        if desc.startswith(_UNIT_PREFIX) and desc[len(_UNIT_PREFIX):].strip():
            units[cname] = desc[len(_UNIT_PREFIX):].strip()[:16]
    note = _skipped_data(skipped, label, "у блоков хранятся только значения в ячейках (числа, категории, текст)")
    if note:
        warnings.append(note)
    for c, unique in zip(cols, unique_names([c.name for c in cols])):
        if c.name in units and unique != c.name:
            units[unique] = units.pop(c.name)
        c.name = unique

    notes: list[str] = []
    lin: np.ndarray | None = None  # kept cells (u fastest); None — all of them
    if cols:
        keep = np.zeros(n, dtype=bool)
        for c in cols:
            keep |= np.isfinite(c.values) if c.kind == "number" else (c.values >= 0)
        if keep.any() and not keep.all():
            lin = np.flatnonzero(keep)
            notes.append(f"Ячеек сетки без значений: {n - len(lin):,} из {n:,} — не загружены".replace(",", " "))
            for c in cols:
                c.values = c.values[lin]
        del keep
    m = n if lin is None else len(lin)
    regular = all(np.allclose(t, t[0], rtol=0, atol=1e-9 * max(t[0], 1)) for t in tensors)
    if not regular:
        notes.append("Шаг сетки OMF переменный: блоки разного размера, родительская сетка — по наибольшему блоку")

    # Grid axis a (u, v, w) lies along coordinate placed[a] = (k, sign).
    centres = np.empty((m, 3), dtype=np.float64)
    sizes = np.empty((m, 3), dtype=np.float32 if regular else np.float64)
    cells = np.zeros(m, dtype=np.int64) if regular else None
    counts, parent, corner = [0] * 3, [0.0] * 3, [0.0] * 3
    order = sorted(range(3), key=lambda a: placed[a][0])  # grid axes in x, y, z order
    stride = {}
    acc = 1
    for a in order:
        stride[a] = acc
        acc *= len(tensors[a])
    div = [1, nu, nu * nv]
    centre_1d = [np.cumsum(t) - t / 2 for t in tensors]
    step = 1 << 22  # in slices: temporaries of the whole grid would add gigabytes at 50 million cells
    for start in range(0, m, step):
        stop = min(start + step, m)
        base = np.arange(start, stop, dtype=np.int64) if lin is None else lin[start:stop]
        for a in range(3):
            k, sign = placed[a]
            t = tensors[a]
            ia = base // div[a] % len(t) if div[a] > 1 else base % len(t)
            centres[start:stop, k] = origin[k] + sign * centre_1d[a][ia]
            if regular:
                sizes[start:stop, k] = t[0]
                cells[start:stop] += (ia if sign > 0 else len(t) - 1 - ia) * stride[a]
            else:
                sizes[start:stop, k] = t[ia]
    for a in range(3):
        k, sign = placed[a]
        t = tensors[a]
        counts[k] = len(t)
        parent[k] = float(t[0])
        corner[k] = float(origin[k] if sign > 0 else origin[k] - t.sum())
    del lin

    hint = {"origin": corner, "size": parent, "at": "corner"} if regular else None
    grid = RegularGrid(origin=corner, parent=parent, counts=counts, cells=cells) if regular else None
    table = BlockTable(format=FORMAT_NAME, centres=centres, sizes=sizes, attributes=cols,
                       roles={k: "" for k in ("x", "y", "z", "dx", "dy", "dz")}, grid_hint=hint, notes=notes,
                       regular=grid)
    return table, units, warnings


def describe(path: Path) -> dict:
    """The wizard's preview: elements, types, sizes known from the JSON, what will be skipped. No binary data is read."""
    try:
        omf = OmfFile(path)
    except OmfFormatError as exc:
        return {"error": str(exc)}
    except OSError as exc:
        return {"error": f"Файл не читается: {exc}"}
    with omf:
        items = []
        counts: dict[str, int] = {}
        try:
            elements = omf.elements()
        except OmfFormatError as exc:
            return {"error": str(exc)}
        for k, (_uid, e) in enumerate(elements):
            cls = e.get("__class__")
            title = ELEMENT_TITLES.get(cls, str(cls))
            item: dict = {"name": _name(e, f"{title} {k + 1}"), "type": title}
            geometry = omf.objects.get(e.get("geometry")) if isinstance(e.get("geometry"), str) else None
            g = (geometry or {}).get("__class__")
            if cls == "PointSetElement":
                item["skip"] = "точки пока не поддерживаются"
            elif cls == "SurfaceElement":
                item["as"] = "каркас"
                if g == "SurfaceGridGeometry":
                    try:
                        cells = len(geometry["tensor_u"]) * len(geometry["tensor_v"])
                        item["triangles"] = 2 * cells
                    except (TypeError, KeyError):
                        pass
            elif cls == "LineSetElement":
                item["as"] = "слой линий"
            elif cls == "VolumeElement":
                item["as"] = "блочная модель"
                try:
                    item["cells"] = len(geometry["tensor_u"]) * len(geometry["tensor_v"]) * len(geometry["tensor_w"])
                    axes = [vector(geometry.get(f"axis_{a}"), "", default=tuple(_AXES[i])) for i, a in enumerate("uvw")]
                    if any(_axis_of(a) is None for a in axes):
                        item.pop("as")
                        item["skip"] = "модель повёрнута"
                except (TypeError, KeyError, OmfFormatError):
                    pass
            else:
                item["skip"] = "тип не поддерживается"
            data = [omf.objects.get(u) for u in (e.get("data") or []) if isinstance(u, str)]
            item["data"] = [_name(d, d.get("__class__", "?")) for d in data if d]
            if e.get("textures"):
                item["textures"] = len(e["textures"])
            counts[title] = counts.get(title, 0) + 1
            items.append(item)
        parts = ", ".join(f"{t.lower()}: {c}" for t, c in counts.items())
        skipped = sum(1 for i in items if i.get("skip"))
        summary = f"Проект «{_name(omf.project, path.stem)}», элементов {len(items)} ({parts})"
        if skipped:
            summary += f"; не загрузятся: {skipped}"
        return {"summary": summary, "items": items}


# ------------------------------------------------------------------------------------- writing
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OmfWriter:
    """Writes OMF v1 as omf 1.0 does: binary blocks as they come, then the JSON, then its offset in the header.

    Coordinates are written as given (absolute, float64) with every origin at zero, so a file read back
    gives the same numbers bit for bit."""

    def __init__(self, path: Path, name: str, description: str = ""):
        self.path = Path(path)
        self._f: BinaryIO = self.path.open("wb")
        self._objects: dict[str, dict] = {}
        self._elements: list[str] = []
        self._project_uid = uuid.uuid4()
        self._name = name
        self._description = description
        self._f.write(b"\x00" * HEADER_SIZE)

    def __enter__(self) -> OmfWriter:
        return self

    def __exit__(self, exc_type, *_exc) -> None:
        if exc_type is None:
            self.close()
        else:
            self._f.close()

    @property
    def element_count(self) -> int:
        return len(self._elements)

    def _add(self, cls: str, **fields) -> str:
        uid = str(uuid.uuid4())
        now = _now()
        self._objects[uid] = {"date_created": now, "date_modified": now, **fields, "__class__": cls}
        return uid

    def _content(self, cls: str, name: str, description: str = "", **fields) -> str:
        return self._add(cls, name=name, description=description, **fields)

    def _binary(self, arr: np.ndarray, dtype: str) -> dict:
        a = np.ascontiguousarray(arr, dtype=dtype)
        start = self._f.tell()
        c = zlib.compressobj()
        flat = a.reshape(-1).view(np.uint8) if a.size else np.zeros(0, np.uint8)
        for i in range(0, len(flat), _CHUNK):
            self._f.write(c.compress(flat[i:i + _CHUNK].tobytes()))
        self._f.write(c.flush())
        return {"start": start, "dtype": dtype, "length": self._f.tell() - start}

    def _array(self, cls: str, arr: np.ndarray) -> str:
        dtype = "<i8" if np.asarray(arr).dtype.kind in "iub" else "<f8"
        return self._add(cls, array=self._binary(arr, dtype))

    def _scalar_data(self, name: str, values: np.ndarray, location: str, description: str = "") -> str:
        return self._content("ScalarData", name, description, array=self._array("ScalarArray", values.astype(np.float64)),
                             location=location)

    def _mapped_data(self, name: str, codes: np.ndarray, labels: list[str], location: str) -> str:
        values = self._add("StringArray", array=[str(x) for x in labels])
        legend = self._content("Legend", name, values=values)
        return self._content("MappedData", name, array=self._array("ScalarArray", codes.astype(np.int64)),
                             legends=[legend], location=location)

    def _string_data(self, name: str, values: list[str], location: str) -> str:
        return self._content("StringData", name, array=self._add("StringArray", array=values), location=location)

    def _element(self, cls: str, name: str, geometry: str, color: str | None, subtype: str, data: list[str],
                 description: str = "", textures: bool = False) -> None:
        fields = {"data": data, "color": color_rgb(color) or [128, 128, 128], "subtype": subtype, "geometry": geometry}
        if textures:
            fields["textures"] = []
        self._elements.append(self._content(cls, name, description, **fields))

    def add_surface(self, name: str, vertices: np.ndarray, triangles: np.ndarray, color: str | None) -> None:
        geometry = self._add("SurfaceGeometry", origin=[0.0, 0.0, 0.0],
                             vertices=self._array("Vector3Array", np.asarray(vertices, dtype=np.float64).reshape(-1, 3)),
                             triangles=self._array("Int3Array", np.asarray(triangles, dtype=np.int64).reshape(-1, 3)))
        self._element("SurfaceElement", name, geometry, color, "surface", [], textures=True)

    def add_lines(self, name: str, lines: list[tuple[np.ndarray, bool]], attributes: list[dict], color: str | None) -> list[str]:
        """Polylines as vertices + segments; attributes of a line go on all its segments.
        A closed line whose last vertex repeats the first is written as a loop on the first vertex."""
        warnings: list[str] = []
        parts_v: list[np.ndarray] = []
        parts_s: list[np.ndarray] = []
        line_of_segment: list[np.ndarray] = []
        offset = 0
        short = 0
        for n, (v, closed) in enumerate(lines):
            v = np.asarray(v, dtype=np.float64).reshape(-1, 3)
            loop = closed and len(v) > 3 and np.array_equal(v[0], v[-1])
            if loop:
                v = v[:-1]
            if len(v) < 2:
                short += 1
                continue
            i = np.arange(len(v), dtype=np.int64) + offset
            seg = np.column_stack([i[:-1], i[1:]])
            if loop:
                seg = np.vstack([seg, [i[-1], i[0]]])
            parts_v.append(v)
            parts_s.append(seg)
            line_of_segment.append(np.full(len(seg), n, dtype=np.int64))
            offset += len(v)
        if short:
            warnings.append(f"Линии «{name}»: {short} лин. из одной точки не записаны (в OMF линия — это сегменты)")
        if not parts_v:
            warnings.append(f"Линии «{name}»: нет линий из двух и более точек — слой не записан")
            return warnings
        vertices = np.vstack(parts_v)
        segments = np.vstack(parts_s)
        owner = np.concatenate(line_of_segment)
        data: list[str] = []
        keys: list[str] = []
        for a in attributes:
            for key in a:
                if key not in keys:
                    keys.append(key)
        for key in keys:
            values = [attributes[n].get(key) if n < len(attributes) else None for n in range(len(lines))]
            present = [v for v in values if v is not None]
            if present and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present):
                per_line = np.array([np.nan if v is None else float(v) for v in values], dtype=np.float64)
                data.append(self._scalar_data(key, per_line[owner], "segments"))
            elif present:
                labels = sorted({str(v) for v in present})
                pos = {s: i for i, s in enumerate(labels)}
                per_line = np.array([-1 if v is None else pos[str(v)] for v in values], dtype=np.int64)
                data.append(self._mapped_data(key, per_line[owner], labels, "segments"))
        geometry = self._add("LineSetGeometry", origin=[0.0, 0.0, 0.0],
                             vertices=self._array("Vector3Array", vertices), segments=self._array("Int2Array", segments))
        self._element("LineSetElement", name, geometry, color, "line", data)
        return warnings

    def add_volume(self, name: str, corner: list[float], size: list[float], counts: list[int],
                   cells: np.ndarray, columns: list[tuple[str, str, np.ndarray, list[str] | None, str | None]],
                   color: str | None) -> None:
        """A regular block model as a dense grid. `cells` is the Fortran index of every block; a cell
        without a block gets NaN (numbers) or −1 (categories). columns: (name, kind, values, labels, unit)."""
        total = int(np.prod(counts))
        data: list[str] = []
        for cname, kind, values, labels, unit in columns:
            if kind == "number":
                dense = np.full(total, np.nan, dtype=np.float64)
                dense[cells] = values
                data.append(self._scalar_data(cname, dense, "cells", f"{_UNIT_PREFIX}{unit}" if unit else ""))
            else:
                dense = np.full(total, -1, dtype=np.int64)
                dense[cells] = values
                data.append(self._mapped_data(cname, dense, list(labels or []), "cells"))
            del dense
        geometry = self._add(
            "VolumeGridGeometry", origin=[float(x) for x in corner],
            tensor_u=[float(size[0])] * counts[0], tensor_v=[float(size[1])] * counts[1], tensor_w=[float(size[2])] * counts[2],
            axis_u=[1.0, 0.0, 0.0], axis_v=[0.0, 1.0, 0.0], axis_w=[0.0, 0.0, 1.0],
        )
        self._element("VolumeElement", name, geometry, color, "volume", data)

    def close(self) -> None:
        if self._f.closed:
            return
        project = {"name": self._name, "description": self._description, "author": "", "revision": "",
                   "date": _now(), "units": "m", "elements": self._elements, "origin": [0.0, 0.0, 0.0]}
        now = _now()
        self._objects[str(self._project_uid)] = {"date_created": now, "date_modified": now, **project, "__class__": "Project"}
        json_start = self._f.tell()
        self._f.write(json.dumps(self._objects, ensure_ascii=False).encode("utf-8"))
        self._f.seek(0)
        self._f.write(MAGIC + struct.pack("<32s", VERSION) + self._project_uid.bytes + struct.pack("<Q", json_start))
        self._f.close()
