"""DXF writer of line and point layers (ТЗ 7.18, 9.5; ADR 0011 §2): `write_dxf(path, layers)`.

One DXF layer per `LineSet` (its colour: the nearest ACI and the true colour). A line is a 3D POLYLINE with its
VERTEX records, a line of one vertex and a point of a point layer — a POINT, or a TEXT when the layer has a `text`
attribute and the feature has a text (`height`, `rotation` go with it). The drawing is AC1018 with the code page
ANSI_1251 and CRLF line ends, as the files of the owner; characters outside the code page become `\\U+XXXX`.
The header, tables, blocks and objects come from ezdxf (a drawing AutoCAD opens); the entities are written as text
in portions with handles of their own, so memory does not grow with the size of the layers.

Coordinates are written with `repr` — the shortest text of the same double — so `dxf_lines.read_dxf` gives back
the same float64 bit for bit. A closed line whose last vertex is exactly its first is written without the repeat
and with the closed flag (the reader puts the repeat back); when the last vertex only nearly repeats the first,
every vertex is written and the line is found closed again by its ends.
DXF has no empty number: an empty Z is written as 0 and a text without a height gets `TEXT_HEIGHT`, both with a
warning. Other attributes have no place in a DXF entity and are not written (a warning names them).
Error codes: no_lines (nothing to write).

Ported from BF `formats/export.py::write_dxf_lines` (`_dxf_frame`, `_vertex_text`, `_nearest_aci`). Differences:
`LineSet` layers, texts, bit-exact closing, progress and cancellation, a temporary file renamed at the end.
"""

from __future__ import annotations

import codecs
import io
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.formats.dxf_stream import n_text
from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet
from bf_core.formats.table import Progress, no_progress

VERSION = "R2004"  # AC1018: the version of the owner's sample, the first with true colour (420)
CODEC = "cp1251"
ERRORS = "bf_dxf_unicode"  # characters outside the code page become \U+XXXX (UTF-16 units, as AutoCAD writes)
CHUNK = 50_000  # vertices formatted at once
TEXT_HEIGHT = 2.5  # the usual default of a metric drawing: a TEXT must have a height
TEXT_FIELDS = ("text", "height", "rotation")
_BAD_LAYER_CHARS = re.compile(r'[\x00-\x1f<>/\\":;?*|=`,]')
_COLOR = re.compile(r"#[0-9a-f]{6}")


def _escape(error: UnicodeError) -> tuple[str, int]:
    if not isinstance(error, UnicodeEncodeError):
        raise error
    units = error.object[error.start : error.end].encode("utf-16-be", errors="surrogatepass")
    return "".join(f"\\U+{units[i] << 8 | units[i + 1]:04X}" for i in range(0, len(units), 2)), error.end


codecs.register_error(ERRORS, _escape)


@dataclass
class DxfWritten:
    path: Path
    lines: int
    points: int
    texts: int
    vertices: int
    size: int
    warnings: list[str] = field(default_factory=list)


def _rgb(color: str | None) -> tuple[int, int, int] | None:
    if color is None or not _COLOR.fullmatch(color):
        return None
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


_aci_table: np.ndarray | None = None


def nearest_aci(rgb: tuple[int, int, int]) -> int:
    """The AutoCAD colour index closest to an RGB colour (7, «white or black», is left out)."""
    global _aci_table
    if _aci_table is None:
        from ezdxf import colors

        _aci_table = np.array([colors.aci2rgb(i) for i in range(1, 256)], dtype=np.int64)
    distance = ((_aci_table - np.array(rgb, dtype=np.int64)) ** 2).sum(axis=1)
    distance[6] = np.iinfo(np.int64).max
    return int(distance.argmin()) + 1


def layer_names(names: Sequence[str]) -> list[str]:
    """Names DXF accepts: forbidden characters replaced, not longer than 255, different without regard to case."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        base = name = (_BAD_LAYER_CHARS.sub("_", str(raw)).strip() or "Слой")[:255]
        k = 1
        while name.casefold() in seen:
            k += 1
            name = f"{base[: 255 - len(str(k)) - 1]}_{k}"
        seen.add(name.casefold())
        out.append(name)
    return out


def _encodable(value: str) -> str:
    """The text as it will be in the file: characters outside the code page as \\U+XXXX."""
    return value.encode(CODEC, errors=ERRORS).decode(CODEC)


def _frame(
    layers: Sequence[tuple[str, str | None]], extent: tuple[np.ndarray, np.ndarray] | None, handles: int
) -> tuple[bytes, bytes, str, int]:
    """Everything around the entities: (bytes before them, bytes after, owner handle, first free handle).

    ezdxf builds the HEADER, CLASSES, TABLES (with the LAYER table), BLOCKS and OBJECTS sections of an AC1018
    drawing; `handles` entity handles are reserved by raising $HANDSEED."""
    import ezdxf

    doc = ezdxf.new(VERSION)
    doc.encoding = CODEC
    doc.header["$DWGCODEPAGE"] = "ANSI_1251"
    if extent is not None:  # ezdxf takes $EXTMIN and $EXTMAX of the header from the model space layout
        doc.modelspace().dxf.extmin = tuple(float(x) for x in extent[0])
        doc.modelspace().dxf.extmax = tuple(float(x) for x in extent[1])
    for name, color in layers:
        layer = doc.layers.get(name) if name in doc.layers else doc.layers.add(name)
        rgb = _rgb(color)
        if rgb is not None:
            layer.color = nearest_aci(rgb)
            layer.rgb = rgb
    stream = io.StringIO()
    doc.write(stream)
    text = stream.getvalue()
    marker = "  0\nSECTION\n  2\nENTITIES\n"
    head, found, tail = text.partition(marker)
    seed = re.search(r"\$HANDSEED\n  5\n([0-9A-Fa-f]+)\n", head)
    if not found or seed is None or not tail.startswith("  0\nENDSEC"):
        raise FormatError("unsupported", "Не удалось подготовить заголовок DXF.", "Сообщите об ошибке разработчикам.")
    first = int(seed.group(1), 16)
    head = head[: seed.start(1)] + f"{first + handles:X}" + head[seed.end(1) :] + marker

    def crlf(part: str) -> bytes:
        return part.replace("\n", "\r\n").encode(CODEC, errors=ERRORS)

    return crlf(head), crlf(tail), doc.modelspace().block_record_handle, first


def _entity(kind: str, owner: str, layer: str, extra: str = "") -> str:
    """The common start of an entity as a %-template: the handle is the only placeholder (%X)."""
    fixed = f"330\r\n{owner}\r\n100\r\nAcDbEntity\r\n  8\r\n{layer}\r\n{extra}".replace("%", "%%")
    return f"  0\r\n{kind}\r\n  5\r\n%X\r\n{fixed}"


def _color_tags(color: str | None) -> str:
    rgb = _rgb(color)
    if rgb is None:
        return ""
    return f" 62\r\n{nearest_aci(rgb):>6}\r\n420\r\n{(rgb[0] << 16) | (rgb[1] << 8) | rgb[2]:>9}\r\n"


def _text_columns(layer: LineSet) -> tuple[list[str | None], np.ndarray, np.ndarray] | None:
    """(text, height, rotation) of every feature when the layer has a `text` attribute."""
    import pyarrow as pa

    table = layer.attrs
    if table is None or "text" not in table.column_names or not pa.types.is_string(table.column("text").type):
        return None
    rows = table.num_rows
    numbers = []
    for name in ("height", "rotation"):
        ok = name in table.column_names and pa.types.is_floating(table.column(name).type)
        numbers.append(table.column(name).to_numpy() if ok else np.full(rows, np.nan))
    return table.column("text").to_pylist(), numbers[0], numbers[1]


def write_dxf(path: str | Path, layers: Sequence[LineSet], progress: Progress | None = None) -> DxfWritten:
    """Write line and point layers as an ASCII DXF (see the module docstring)."""
    path = Path(path)
    tick = progress or no_progress
    tick(0.0, "Запись DXF")
    names = layer_names([layer.name for layer in layers])
    warnings: list[str] = []
    plans: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []  # per layer: first vertex, vertices written, flag
    handles = total = no_z = 0
    low = np.full(3, np.inf)
    high = np.full(3, -np.inf)
    lost: list[str] = []
    for layer in layers:
        n = layer.point_count
        offsets = np.arange(n + 1, dtype=np.int64) if layer.offsets is None else layer.offsets
        a, counts = offsets[:-1], np.diff(offsets)
        closed = np.zeros(len(a), dtype=bool) if layer.closed is None else layer.closed.astype(bool)
        if n:
            bits = layer.xyz.view(np.uint64)
            exact = (bits[a] == bits[offsets[1:] - 1]).all(axis=1)
            cut = closed & exact & (counts >= 4)  # the repeat of the first vertex is implied by the closed flag
            counts = counts - cut
            flags = np.where(cut, 9, 8)
            z = layer.xyz[:, 2]
            no_z += int(np.isnan(z).sum())
            low = np.minimum(low, np.where(np.isnan(layer.xyz), np.inf, layer.xyz).min(axis=0))
            high = np.maximum(high, np.where(np.isnan(layer.xyz), -np.inf, layer.xyz).max(axis=0))
        else:
            flags = np.zeros(0, dtype=np.int64)
        plans.append((a, counts, flags))
        handles += int(np.where(counts == 1, 1, counts + 2).sum())
        total += int(counts.sum())
        for name in layer.attrs.column_names if layer.attrs is not None else ():
            if name not in TEXT_FIELDS and name not in lost:
                lost.append(name)
        for name in layer.vattrs.column_names if layer.vattrs is not None else ():
            if name not in lost:
                lost.append(name)
    if total == 0:
        raise FormatError("no_lines", "Нет линий и точек для записи в DXF.", "Выберите слой, в котором есть объекты.")
    if no_z:
        warnings.append(f"{n_text(no_z)} вершин без высоты Z: в DXF записана высота 0")
    if lost:
        shown = ", ".join(f"«{name}»" for name in lost[:8])
        warnings.append(f"Атрибуты в DXF не записываются: {shown}{' и другие' if len(lost) > 8 else ''}")
    extent = (np.where(np.isfinite(low), low, 0.0), np.where(np.isfinite(high), high, 0.0))
    head, tail, space, handle = _frame(
        [(name, layer.color) for name, layer in zip(names, layers, strict=True)], extent, handles
    )
    counted = {"lines": 0, "points": 0, "texts": 0, "no_height": 0}
    done = 0
    part = path.with_name(path.name + ".part")
    try:
        with part.open("wb") as f:
            f.write(head)
            for layer, name, (first, counts, flags) in zip(layers, names, plans, strict=True):
                written = _write_layer(f, layer, _encodable(name), space, handle, first, counts, flags, counted)
                handle += int(np.where(counts == 1, 1, counts + 2).sum())
                done += written
                tick(done / total, f"Запись DXF: вершин {n_text(done)} из {n_text(total)}")
            f.write(tail)
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    if counted["no_height"]:
        shown = f"{TEXT_HEIGHT}".replace(".", ",")
        warnings.append(f"{n_text(counted['no_height'])} текстов без высоты: в DXF записана высота {shown}")
    tick(1.0, f"Записано вершин: {n_text(total)}")
    return DxfWritten(path, counted["lines"], counted["points"], counted["texts"], total, path.stat().st_size, warnings)


def _write_layer(
    f: Any,
    layer: LineSet,
    name: str,
    space: str,
    handle: int,
    first: np.ndarray,
    counts: np.ndarray,
    flags: np.ndarray,
    counted: dict[str, int],
) -> int:
    """The entities of one layer; returns the vertices written."""
    color = _color_tags(layer.color)
    xyz = np.where(np.isnan(layer.xyz), 0.0, layer.xyz) if np.isnan(layer.xyz).any() else layer.xyz
    texts = _text_columns(layer)
    point = _entity("POINT", space, name, color + "100\r\nAcDbPoint\r\n") + " 10\r\n%r\r\n 20\r\n%r\r\n 30\r\n%r\r\n"
    label = (
        _entity("TEXT", space, name, color + "100\r\nAcDbText\r\n")
        + " 10\r\n%r\r\n 20\r\n%r\r\n 30\r\n%r\r\n 40\r\n%r\r\n  1\r\n%s\r\n 50\r\n%r\r\n100\r\nAcDbText\r\n"
    )
    polyline = (
        _entity("POLYLINE", space, name, color + "100\r\nAcDb3dPolyline\r\n")
        + " 66\r\n     1\r\n 10\r\n0.0\r\n 20\r\n0.0\r\n 30\r\n0.0\r\n 70\r\n%6d\r\n"
    )
    vertex = (
        "  0\r\nVERTEX\r\n  5\r\n%X\r\n330\r\n%X\r\n"
        + f"100\r\nAcDbEntity\r\n  8\r\n{name}\r\n100\r\nAcDbVertex\r\n100\r\nAcDb3dPolylineVertex\r\n".replace(
            "%", "%%"
        )
        + " 10\r\n%r\r\n 20\r\n%r\r\n 30\r\n%r\r\n 70\r\n    32\r\n"
    )
    seqend = "  0\r\nSEQEND\r\n  5\r\n%X\r\n330\r\n%X\r\n" + f"100\r\nAcDbEntity\r\n  8\r\n{name}\r\n".replace(
        "%", "%%"
    )
    out: list[str] = []
    pending = 0

    def flush() -> None:
        nonlocal pending
        f.write("".join(out).encode(CODEC, errors=ERRORS))
        out.clear()
        pending = 0

    for i, (a, k, flag) in enumerate(zip(first.tolist(), counts.tolist(), flags.tolist(), strict=True)):
        if k == 1:
            x, y, z = xyz[a].tolist()
            text = texts[0][i] if texts is not None else None
            if texts is None or text is None:
                out.append(point % (handle, x, y, z))
                counted["points"] += 1
            else:
                height, rotation = float(texts[1][i]), float(texts[2][i])
                if not height > 0:
                    height = TEXT_HEIGHT
                    counted["no_height"] += 1
                line = " ".join(text.split()) if "\n" in text or "\r" in text else text
                out.append(label % (handle, x, y, z, height, line, rotation if rotation == rotation else 0.0))
                counted["texts"] += 1
            handle += 1
        else:
            owner = handle
            out.append(polyline % (owner, flag))
            handle += 1
            for lo in range(a, a + k, CHUNK):
                v = xyz[lo : min(lo + CHUNK, a + k)]
                rows = len(v)
                ids = range(handle, handle + rows)
                x, y, z = v[:, 0].tolist(), v[:, 1].tolist(), v[:, 2].tolist()
                args = tuple(chain.from_iterable(zip(ids, [owner] * rows, x, y, z, strict=True)))
                out.append((vertex * rows) % args)
                handle += rows
                if rows == CHUNK:
                    flush()
            out.append(seqend % (handle, owner))
            handle += 1
            counted["lines"] += 1
        pending += k
        if pending >= CHUNK:
            flush()
    flush()
    return int(counts.sum())
