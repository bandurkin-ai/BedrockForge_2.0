"""Writers, the reverse of the readers in formats/lines.py and formats/wireframes.py.

* `write_micromine_str` — Micromine STR (an extended DAT: EAST, NORTH, RL, JOIN, STRING + attributes);
* `write_dxf_lines`     — ASCII DXF with 3D POLYLINE / POINT entities, one DXF layer per line layer;
* `write_dxf_mesh`      — ASCII DXF with polyface meshes (the form Micromine itself writes), streamed;
* `write_obj`           — Wavefront OBJ, streamed.

The round trip «import → export → import» keeps every float64 bit for bit: STR stores coordinates
as binary doubles (type R, as the real Micromine files do), DXF and OBJ store `repr()` text, the
shortest decimal string that parses back to the same double.

Layouts follow the user's sample files (docs/FORMATS.md):

* STR: the magic line, the XML metadata (title, encoding `ibm-5347_P100-1998` = cp1251, one
  `field-class` object per field) padded with NUL to 4096 bytes, `N   VARIABLES`, variable lines
  `NAME(10)T WW DD`, fixed-width records ending with CRLF; JOIN is `N 12 0` written left-aligned,
  STRING is `C 20 0`.
* DXF: version AC1018 with code page ANSI_1251 and CRLF line ends, like the sample. The header,
  tables, blocks and objects come from ezdxf (a drawing AutoCAD opens); the entities are written
  here as text, in chunks, with their own handles.
"""

from __future__ import annotations

import io
import math
import numbers
import os
import re
from itertools import chain
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np

from app.formats.lines import _NO_CODE, LineLayer
from app.formats.micromine import HEADER_SIZE

__all__ = ["ExportError", "write_micromine_str", "write_dxf_lines", "write_dxf_mesh", "write_obj"]


class ExportError(ValueError):
    """The data cannot be written in the requested format; the message is shown to the user."""


# ---------------------------------------------------------------- common helpers


def _replace_into(path: Path, write) -> int:
    """Run `write(binary file)` on a temporary file next to `path`, then move it into place.
    A failed export leaves no half-written file. Returns the size in bytes."""
    path = Path(path)
    part = path.with_name(path.name + ".part")
    try:
        with part.open("wb") as f:
            write(f)
        size = part.stat().st_size
        os.replace(part, path)
        return size
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _line_vertices(vertices, where: str) -> np.ndarray:
    """(n, 3) float64; a 2D line gets Z = 0, a missing Z becomes 0 (as the readers do)."""
    v = np.asarray(vertices, dtype=np.float64)
    if v.size == 0:
        return np.zeros((0, 3))
    if v.ndim != 2 or v.shape[1] not in (2, 3):
        raise ExportError(f"{where}: координаты должны быть таблицей из 2 или 3 столбцов")
    if v.shape[1] == 2:
        v = np.column_stack([v, np.zeros(len(v))])
    if not np.isfinite(v[:, :2]).all() or np.isinf(v[:, 2]).any():
        raise ExportError(f"{where}: в координатах есть пропуски или бесконечные значения")
    if np.isnan(v[:, 2]).any():
        v = v.copy()
        v[:, 2] = np.nan_to_num(v[:, 2])
    return v


def _repeats_first(v: np.ndarray) -> bool:
    return len(v) > 2 and bool(np.array_equal(v[0], v[-1]))


def _mesh_arrays(name: str, vertices, triangles) -> tuple[np.ndarray, np.ndarray]:
    v = np.asarray(vertices)
    if v.dtype != np.float64:
        v = v.astype(np.float64)
    t = np.asarray(triangles)
    if v.ndim != 2 or v.shape[1] != 3:
        raise ExportError(f"Каркас «{name}»: вершины должны быть таблицей n × 3")
    if t.size == 0:
        return v, np.zeros((0, 3), dtype=np.int64)
    if t.ndim != 2 or t.shape[1] != 3 or t.dtype.kind not in "iu":
        raise ExportError(f"Каркас «{name}»: треугольники должны быть целочисленной таблицей m × 3")
    if int(t.min()) < 0 or int(t.max()) >= len(v):
        raise ExportError(f"Каркас «{name}»: номера вершин треугольников вне диапазона 0…{len(v) - 1}")
    if not np.isfinite(v).all():
        raise ExportError(f"Каркас «{name}»: в координатах вершин есть пропуски или бесконечные значения")
    return v, t


def _unique(names: list[str], key=str) -> list[str]:
    """Names made distinct with a « (2)» suffix; `key` decides what counts as the same name."""
    seen: set[str] = set()
    out = []
    for name in names:
        candidate, n = name, 1
        while key(candidate) in seen:
            n += 1
            candidate = f"{name} ({n})"
        seen.add(key(candidate))
        out.append(candidate)
    return out


def _rgb(color: str | None) -> tuple[int, int, int] | None:
    if not isinstance(color, str):
        return None
    m = re.fullmatch(r"#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})", color.strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ---------------------------------------------------------------- Micromine STR

_MAGIC_LINE = b"THIS IS MICROMINE EXTENDED DATA FILE!\x07\x1a\x01\r\n"
_STR_CODEPAGE = "ibm-5347_P100-1998"  # Windows-1251, the code page of the sample files
_STR_CODEC = "cp1251"
_TIME_STAMP = "67452301efcdab89"  # the constant Micromine writes in every meta-object
_RESERVED = ("EAST", "NORTH", "RL", "JOIN", "STRING")
_NAME_WIDTH = 10  # Micromine field names are at most 10 characters
_JOIN_WIDTH = 12
_STRING_WIDTH = 20
_TEXT_LIMIT = 255  # the widest character field of a Micromine file
_COORD_DECIMALS = 6  # display decimals of the R fields in the samples; the value is a full double


def _encode(text: str, what: str) -> bytes:
    try:
        return text.encode(_STR_CODEC)
    except UnicodeEncodeError as exc:
        raise ExportError(
            f"{what}: символ «{text[exc.start]}» нельзя записать в кодировке Windows-1251, "
            "в которой хранится Micromine STR"
        ) from exc


def _is_missing(value) -> bool:
    if value is None:
        return True
    try:
        return bool(value != value)  # NaN, NaT
    except (TypeError, ValueError):
        return False


def _clean_text(value) -> str:
    """One line of text: records are fixed-width, line breaks and tabs inside a value become spaces."""
    return re.sub(r"[\x00-\x1f]", " ", str(value)).rstrip()


def _field_names(keys: list[str]) -> dict[str, str]:
    """Attribute name -> field name: at most 10 characters of cp1251, unique without regard to case."""
    used = {name for name in _RESERVED}
    out: dict[str, str] = {}
    for n, key in enumerate(keys, start=1):
        base = re.sub(r"[\x00-\x1f|]", " ", key).strip() or f"FIELD{n}"
        _encode(base, f"Имя атрибута «{key}»")
        name, k = base[:_NAME_WIDTH].rstrip(), 1
        while name.upper() in used:
            k += 1
            suffix = str(k)
            name = base[:_NAME_WIDTH - len(suffix)].rstrip() + suffix
        used.add(name.upper())
        out[key] = name
    return out


def _decimal_text(values: list[float]) -> tuple[list[str], int] | None:
    """Floats as fixed-decimal text that parses back to the same doubles, or None when some value
    needs an exponent or too many digits."""
    decimals = 0
    for v in values:
        text = repr(v)
        if not math.isfinite(v) or "e" in text or "E" in text:
            return None
        decimals = max(decimals, len(text.partition(".")[2]))
    if decimals > 15:
        return None
    texts = [f"{v:.{decimals}f}" for v in values]
    if max(map(len, texts)) > 24 or any(float(t) != v for t, v in zip(texts, values)):
        return None
    return texts, decimals


def _attribute_column(name: str, values: list) -> tuple[str, int, int, object]:
    """(type, width, decimals, per-line data) of one attribute.

    Text -> C wide enough for the longest value; whole numbers -> N (text, blank = missing);
    other numbers -> R (binary double), or N with fixed decimals when some values are missing
    and every value is a short decimal: a blank N field is a missing value in Micromine, while
    R can only hold NaN for it."""
    present = [v for v in values if not _is_missing(v)]
    if present and all(isinstance(v, numbers.Real) for v in present):
        if all(isinstance(v, numbers.Integral) for v in present):
            texts = ["" if _is_missing(v) else str(int(v)) for v in values]
            width = max(map(len, texts))
            if width <= 24:
                return "N", width, 0, [t.encode("ascii") for t in texts]
        floats = [float(v) for v in present]
        if len(present) < len(values):
            decimal = _decimal_text(floats)
            if decimal is not None:
                it = iter(decimal[0])
                texts = ["" if _is_missing(v) else next(it) for v in values]
                return "N", max(map(len, texts)), decimal[1], [t.encode("ascii") for t in texts]
        return "R", 8, _COORD_DECIMALS, np.array([np.nan if _is_missing(v) else float(v) for v in values])
    encoded = [b"" if _is_missing(v) else _encode(_clean_text(v), f"Атрибут «{name}»") for v in values]
    width = max([1, *map(len, encoded)])
    if width > _TEXT_LIMIT:
        raise ExportError(f"Атрибут «{name}»: значение длиннее {_TEXT_LIMIT} символов не помещается в поле Micromine")
    return "C", width, 0, encoded


def _str_header(field_names: list[str]) -> bytes:
    xml = ['<?xml version="1.0" encoding="utf-8"?><metadata version="1.0">',
           f'<meta-object type="title" version="1.0" time-stamp="{_TIME_STAMP}" compulsory="true">'
           '<data title="" description=""/></meta-object>',
           f'<meta-object type="encoding" version="1.0" time-stamp="{_TIME_STAMP}" compulsory="true">'
           f'<data codepage="{_STR_CODEPAGE}"/></meta-object>']
    for name in field_names:
        xml.append(f'<meta-object type="field-class" version="1.0" time-stamp="{_TIME_STAMP}">'
                   f'<field name={quoteattr(name)}/><data field-class="Generic"/></meta-object>')
    xml.append("</metadata>")
    head = _MAGIC_LINE + "".join(xml).encode("utf-8")
    # NUL padding to 4096 bytes; a longer header (many fields) is padded to the next multiple.
    size = HEADER_SIZE * max(1, -(-(len(head) + 1) // HEADER_SIZE))
    return head.ljust(size, b"\x00")


def write_micromine_str(path: Path, layers: list[LineLayer]) -> dict:
    """Write line layers as one Micromine STR file.

    STRING is the layer name (empty for the «Без кода» layer of the reader), JOIN identifies the
    line: the line's own `JOIN` attribute is kept when it is a whole number that differs from the
    line before, otherwise a new number is given. A closed line repeats its first vertex at the
    end — that is how Micromine and `read_micromine_str` recognise it. Coordinates are binary
    doubles (type R): the round trip is bit-exact, `coordinate_tolerance` is 0.0.
    """
    lines: list[tuple[str, np.ndarray, dict]] = []
    for layer in layers:
        name = _clean_text(layer.name).strip()
        code = "" if name == _NO_CODE else name
        for n, line in enumerate(layer.lines, start=1):
            v = _line_vertices(line.vertices, f"Слой «{layer.name}», линия {n}")
            if len(v) == 0:
                continue
            if line.closed and len(v) > 2 and not np.all(np.abs(v[0] - v[-1]) <= 1e-6):
                v = np.vstack([v, v[:1]])
            lines.append((code, v, {str(k): value for k, value in (line.attributes or {}).items()}))
    if not lines:
        raise ExportError("Нет линий для записи в STR")

    # Attribute fields, in order of first appearance; the reserved names are fields of their own.
    keys: list[str] = []
    skipped: list[str] = []
    for _, _, attributes in lines:
        for key in attributes:
            if key.strip().upper() in _RESERVED:
                if key.strip().upper() != "JOIN" and key not in skipped:
                    skipped.append(key)
            elif key not in keys:
                keys.append(key)
    names = _field_names(keys)

    codes = [_encode(code, f"Имя слоя «{code}»") for code, _, _ in lines]
    string_width = max([_STRING_WIDTH, *map(len, codes)])
    if string_width > _TEXT_LIMIT:
        raise ExportError(f"Имя слоя длиннее {_TEXT_LIMIT} символов не помещается в поле STRING")

    # JOIN: keep the number the line came with when the pair (JOIN, STRING) still differs from the
    # line before it; any other line gets a number that no line uses.
    wanted: list[int | None] = []
    for _, _, attributes in lines:
        value = next((v for k, v in attributes.items() if k.strip().upper() == "JOIN"), None)
        ok = (isinstance(value, numbers.Real) and not isinstance(value, bool) and math.isfinite(value)
              and float(value) == int(value) and 0 <= int(value) < 10 ** (_JOIN_WIDTH - 1))
        wanted.append(int(value) if ok else None)
    fresh = max([0, *(w for w in wanted if w is not None)])
    joins: list[int] = []
    for i, w in enumerate(wanted):
        if w is None or (i > 0 and (joins[-1], codes[i - 1]) == (w, codes[i])):
            fresh += 1
            w = fresh
        joins.append(w)

    fields: list[tuple[str, str, int, int]] = [
        ("EAST", "R", 8, _COORD_DECIMALS), ("NORTH", "R", 8, _COORD_DECIMALS), ("RL", "R", 8, _COORD_DECIMALS),
        ("JOIN", "N", _JOIN_WIDTH, 0), ("STRING", "C", string_width, 0),
    ]
    columns: list[object] = [[str(j).encode("ascii") for j in joins], codes]
    for key in keys:
        kind, width, decimals, data = _attribute_column(key, [attrs.get(key) for _, _, attrs in lines])
        fields.append((names[key], kind, width, decimals))
        columns.append(data)

    counts = np.array([len(v) for _, v, _ in lines], dtype=np.int64)
    points = int(counts.sum())
    dtype = np.dtype({
        "names": [f"f{i}" for i in range(len(fields))] + ["eol"],
        "formats": ["<f8" if kind == "R" else f"S{width}" for _, kind, width, _ in fields] + ["S2"],
    })
    records = np.empty(points, dtype=dtype)
    xyz = np.vstack([v for _, v, _ in lines])
    for i in range(3):
        records[f"f{i}"] = xyz[:, i]
    for i, data in enumerate(columns, start=3):
        _, kind, width, _ = fields[i]
        if kind == "R":
            per_line = np.asarray(data, dtype="<f8")
        else:  # text is padded with spaces to the field width (numpy alone would pad with NUL)
            per_line = np.array([value.ljust(width) for value in data], dtype=f"S{width}")
        records[f"f{i}"] = np.repeat(per_line, counts)
    records["eol"] = b"\r\n"

    def write(f) -> None:
        f.write(_str_header([name for name, *_ in fields]))
        f.write(f"{len(fields):<4}VARIABLES\r\n".encode("ascii"))
        for name, kind, width, decimals in fields:
            f.write(name.encode(_STR_CODEC).ljust(_NAME_WIDTH) + f"{kind} {width:>2} {decimals:>2}\r\n".encode("ascii"))
        f.write(records.tobytes())

    size = _replace_into(path, write)
    return {
        "lines": len(lines),
        "points": points,
        "fields": [name for name, *_ in fields],
        "coordinate_tolerance": 0.0,
        "codepage": _STR_CODEPAGE,
        "single_point_lines": int((counts == 1).sum()),
        "renamed_fields": {key: name for key, name in names.items() if key != name},
        "skipped_attributes": skipped,
        "bytes": size,
    }


# ---------------------------------------------------------------- DXF

_DXF_VERSION = "R2004"  # AC1018, the version of the sample file; the first with true colour (420)
_DXF_CODEC = "cp1251"
_DXF_ERRORS = "dxf_backslash_replace"  # ezdxf's handler: other characters become \U+XXXX
_BAD_LAYER_CHARS = re.compile(r'[\x00-\x1f<>/\\":;?*|=`,]')
_CHUNK = 50_000  # records formatted at once
_FACE_BLOCK = 10_922  # triangles per polyface: at most 32 766 vertices, the limit is 32 767

_aci_table: np.ndarray | None = None


def _nearest_aci(rgb: tuple[int, int, int]) -> int:
    """The AutoCAD colour index closest to an RGB colour (7, «white or black», is left out)."""
    global _aci_table
    if _aci_table is None:
        from ezdxf import colors

        _aci_table = np.array([colors.aci2rgb(i) for i in range(1, 256)], dtype=np.int64)
    distance = ((_aci_table - np.array(rgb, dtype=np.int64)) ** 2).sum(axis=1)
    distance[6] = np.iinfo(np.int64).max
    return int(distance.argmin()) + 1


def _dxf_layer_names(names: list[str]) -> list[str]:
    cleaned = [(_BAD_LAYER_CHARS.sub("_", str(name)).strip() or "Слой")[:255] for name in names]
    return _unique(cleaned, key=str.casefold)


def _dxf_frame(layers: list[tuple[str, str | None]], extent: tuple[np.ndarray, np.ndarray] | None,
               handles: int) -> tuple[bytes, bytes, str, int]:
    """Everything around the entities: (bytes before them, bytes after, owner handle, first free handle).

    ezdxf builds the HEADER, CLASSES, TABLES (with the LAYER table), BLOCKS and OBJECTS sections
    of an AC1018 drawing; `handles` entity handles are reserved by raising $HANDSEED."""
    import ezdxf

    doc = ezdxf.new(_DXF_VERSION)
    doc.encoding = _DXF_CODEC
    doc.header["$DWGCODEPAGE"] = "ANSI_1251"
    if extent is not None:
        doc.header["$EXTMIN"] = tuple(float(x) for x in extent[0])
        doc.header["$EXTMAX"] = tuple(float(x) for x in extent[1])
    for name, color in layers:
        layer = doc.layers.get(name) if name in doc.layers else doc.layers.add(name)
        rgb = _rgb(color)
        if rgb is not None:
            layer.color = _nearest_aci(rgb)
            layer.rgb = rgb
    stream = io.StringIO()
    doc.write(stream)
    text = stream.getvalue()
    marker = "  0\nSECTION\n  2\nENTITIES\n"
    head, found, tail = text.partition(marker)
    seed = re.search(r"\$HANDSEED\n  5\n([0-9A-Fa-f]+)\n", head)
    if not found or seed is None or not tail.startswith("  0\nENDSEC"):
        raise ExportError("Не удалось подготовить заголовок DXF")
    first = int(seed.group(1), 16)
    head = head[:seed.start(1)] + f"{first + handles:X}" + head[seed.end(1):] + marker

    def crlf(part: str) -> bytes:
        return part.replace("\n", "\r\n").encode(_DXF_CODEC, errors=_DXF_ERRORS)

    return crlf(head), crlf(tail), doc.modelspace().block_record_handle, first


def _dxf_text(value: str) -> str:
    return value.encode(_DXF_CODEC, errors=_DXF_ERRORS).decode(_DXF_CODEC)


def _color_tags(color: str | None) -> str:
    rgb = _rgb(color)
    if rgb is None:
        return ""
    return f" 62\r\n{_nearest_aci(rgb):>6}\r\n420\r\n{(rgb[0] << 16) | (rgb[1] << 8) | rgb[2]:>9}\r\n"


def _entity(kind: str, owner: str, layer: str, extra: str = "") -> str:
    """The common start of an entity; the handle is a `%X` placeholder."""
    return f"  0\r\n{kind}\r\n  5\r\n%X\r\n330\r\n{owner}\r\n100\r\nAcDbEntity\r\n  8\r\n{layer}\r\n{extra}".replace("%", "%%").replace("%%X", "%X", 1)


def _vertex_text(first_handle: int, owner: str, layer: str, subclass: str, flags: int, v: np.ndarray) -> str:
    """VERTEX records of a polyline for the rows of `v`; coordinates are written with repr()."""
    record = (_entity("VERTEX", owner, layer, f"100\r\nAcDbVertex\r\n100\r\n{subclass}\r\n")
              + f" 10\r\n%r\r\n 20\r\n%r\r\n 30\r\n%r\r\n 70\r\n{flags:>6}\r\n")
    handles = range(first_handle, first_handle + len(v))
    x, y, z = v[:, 0].tolist(), v[:, 1].tolist(), v[:, 2].tolist()
    return (record * len(v)) % tuple(chain.from_iterable(zip(handles, x, y, z)))


def write_dxf_lines(path: Path, layers: list[LineLayer]) -> dict:
    """Write line layers as an ASCII DXF: one DXF layer per line layer (with its colour: the
    nearest ACI and the true colour), each line a 3D POLYLINE, a single-vertex line a POINT.

    A closed line gets the closed flag and is written without the repeated first vertex;
    `read_dxf_lines` adds it back. Coordinates are repr() text: the round trip is bit-exact.
    Line attributes are not stored: DXF entities have no attribute table."""
    names = _dxf_layer_names([layer.name for layer in layers])
    items: list[tuple[str, str | None, np.ndarray, bool]] = []
    for layer, name in zip(layers, names):
        for n, line in enumerate(layer.lines, start=1):
            v = _line_vertices(line.vertices, f"Слой «{layer.name}», линия {n}")
            if len(v) == 0:
                continue
            closed = bool(line.closed) and len(v) > 2
            if closed and _repeats_first(v):
                v = v[:-1]
                closed = len(v) > 2
            items.append((name, layer.color, v, closed))
    if not items:
        raise ExportError("Нет линий для записи в DXF")

    every = np.vstack([v for _, _, v, _ in items])
    handles = sum(1 if len(v) == 1 else len(v) + 2 for _, _, v, _ in items)
    head, tail, space, handle = _dxf_frame([(name, layer.color) for layer, name in zip(layers, names)],
                                           (every.min(axis=0), every.max(axis=0)), handles)
    counts = {"lines": 0, "points": 0, "single_points": 0}

    def write(f) -> None:
        nonlocal handle
        f.write(head)
        for name, color, v, closed in items:
            layer = _dxf_text(name)
            counts["points"] += len(v)
            if len(v) == 1:
                text = (_entity("POINT", space, layer, _color_tags(color) + "100\r\nAcDbPoint\r\n")
                        + " 10\r\n%r\r\n 20\r\n%r\r\n 30\r\n%r\r\n") % (handle, *v[0].tolist())
                handle += 1
                counts["single_points"] += 1
                f.write(text.encode(_DXF_CODEC))
                continue
            polyline = f"{handle:X}"
            out = [_entity("POLYLINE", space, layer, _color_tags(color) + "100\r\nAcDb3dPolyline\r\n") % handle
                   + f" 66\r\n     1\r\n 10\r\n0.0\r\n 20\r\n0.0\r\n 30\r\n0.0\r\n 70\r\n{9 if closed else 8:>6}\r\n"]
            handle += 1
            for a in range(0, len(v), _CHUNK):
                part = v[a:a + _CHUNK]
                out.append(_vertex_text(handle, polyline, layer, "AcDb3dPolylineVertex", 32, part))
                handle += len(part)
            out.append(_entity("SEQEND", polyline, layer) % handle)
            handle += 1
            counts["lines"] += 1
            f.write("".join(out).encode(_DXF_CODEC))
        f.write(tail)

    size = _replace_into(path, write)
    return {**counts, "layers": sorted(set(name for name, *_ in items), key=names.index), "bytes": size}


def write_dxf_mesh(path: Path, meshes: list[tuple[str, np.ndarray, np.ndarray, str | None]]) -> dict:
    """Write wireframes as an ASCII DXF, each on its own layer, as polyface meshes — the form of
    the user's sample (a Micromine export) and of `dxf_stream`: vertices are shared inside a
    polyface, faces refer to them by number.

    A polyface holds at most 32 767 vertices, so a wireframe is cut into polyfaces of 10 922
    triangles; a vertex used by several polyfaces is repeated in each (the reader welds them
    back). The file is written polyface by polyface: memory does not grow with the mesh.
    Coordinates are repr() text: bit-exact."""
    prepared = []
    for name, vertices, triangles, color in meshes:
        v, t = _mesh_arrays(str(name), vertices, triangles)
        if len(t):
            prepared.append((str(name), v, t, color))
    if not prepared:
        raise ExportError("Нет каркасов с треугольниками для записи в DXF")
    names = _dxf_layer_names([name for name, *_ in prepared])

    low = np.min([v.min(axis=0) for _, v, _, _ in prepared], axis=0)
    high = np.max([v.max(axis=0) for _, v, _, _ in prepared], axis=0)
    # An upper bound of the handles: a header and a SEQEND per polyface, a record per face and corner.
    handles = sum(4 * len(t) + 2 * (-(-len(t) // _FACE_BLOCK)) for _, _, t, _ in prepared)
    head, tail, space, handle = _dxf_frame([(n, c) for n, (_, _, _, c) in zip(names, prepared)], (low, high), handles)
    counts = {"vertices": 0, "triangles": 0, "polyfaces": 0}

    def write(f) -> None:
        nonlocal handle
        f.write(head)
        for name, (_, v, t, color) in zip(names, prepared):
            layer = _dxf_text(name)
            colors = _color_tags(color)
            for a in range(0, len(t), _FACE_BLOCK):
                block = np.asarray(t[a:a + _FACE_BLOCK])
                used, local = np.unique(block, return_inverse=True)
                local = local.reshape(-1, 3) + 1  # polyface vertex numbers start at 1
                polyline = f"{handle:X}"
                out = [_entity("POLYLINE", space, layer, colors + "100\r\nAcDbPolyFaceMesh\r\n") % handle
                       + " 66\r\n     1\r\n 10\r\n0.0\r\n 20\r\n0.0\r\n 30\r\n0.0\r\n 70\r\n    64\r\n"
                       + f" 71\r\n{len(used):>6}\r\n 72\r\n{len(block):>6}\r\n"]
                handle += 1
                out.append(_vertex_text(handle, polyline, layer, "AcDbPolyFaceMeshVertex", 192, v[used]))
                handle += len(used)
                face = (_entity("VERTEX", polyline, layer, "100\r\nAcDbFaceRecord\r\n")
                        + " 10\r\n0.0\r\n 20\r\n0.0\r\n 30\r\n0.0\r\n 70\r\n   128\r\n 71\r\n%6d\r\n 72\r\n%6d\r\n 73\r\n%6d\r\n")
                rows = zip(range(handle, handle + len(block)), local[:, 0].tolist(), local[:, 1].tolist(), local[:, 2].tolist())
                out.append((face * len(block)) % tuple(chain.from_iterable(rows)))
                handle += len(block)
                out.append(_entity("SEQEND", polyline, layer, colors) % handle)
                handle += 1
                f.write("".join(out).encode(_DXF_CODEC))
                counts["vertices"] += len(used)
                counts["triangles"] += len(block)
                counts["polyfaces"] += 1
        f.write(tail)

    size = _replace_into(path, write)
    return {"meshes": len(prepared), **counts, "layers": names, "bytes": size}


# ---------------------------------------------------------------- OBJ


def write_obj(path: Path, meshes: list[tuple[str, np.ndarray, np.ndarray, str | None]]) -> dict:
    """Write wireframes as a Wavefront OBJ: `o name`, `v x y z` (repr(): bit-exact), `f i j k`
    with 1-based numbers that count the vertices of the whole file. Written in chunks, UTF-8.
    OBJ has no colour of its own: it is kept as a `# color` comment that readers skip."""
    prepared = []
    for name, vertices, triangles, color in meshes:
        v, t = _mesh_arrays(str(name), vertices, triangles)
        if len(t):
            prepared.append((str(name), v, t, color))
    if not prepared:
        raise ExportError("Нет каркасов с треугольниками для записи в OBJ")
    names = _unique([re.sub(r"\s+", " ", name).strip() or "OBJ" for name, *_ in prepared])
    counts = {"vertices": 0, "triangles": 0}

    def write(f) -> None:
        f.write(b"# BedrockForge\n")
        base = 1
        for name, (_, v, t, color) in zip(names, prepared):
            rgb = _rgb(color)
            f.write(f"o {name}\n".encode("utf-8"))
            if rgb is not None:
                f.write("# color #{:02x}{:02x}{:02x}\n".format(*rgb).encode("ascii"))
            for a in range(0, len(v), _CHUNK):
                part = np.asarray(v[a:a + _CHUNK])
                f.write((("v %r %r %r\n" * len(part)) % tuple(part.ravel().tolist())).encode("ascii"))
            for a in range(0, len(t), _CHUNK):
                part = np.asarray(t[a:a + _CHUNK], dtype=np.int64) + base
                f.write((("f %d %d %d\n" * len(part)) % tuple(part.ravel().tolist())).encode("ascii"))
            base += len(v)
            counts["vertices"] += len(v)
            counts["triangles"] += len(t)

    size = _replace_into(path, write)
    return {"meshes": len(prepared), **counts, "names": names, "bytes": size}
