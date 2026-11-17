"""ESRI Shapefile → a layer of lines or points (ТЗ 7.35 Д-13; ADR 0009, ADR 0011 §2; pyshp — ADR 0001).

`read_shp(path, progress)` → `ShpRead` (a `str_layers.StrRead` with `crs`): one `lineset.LineSet` named after the file.
  * Point, MultiPoint → a layer of points (a MultiPoint record gives its attributes to each of its points);
    PolyLine → lines, every part is a line; Polygon → closed lines, every ring is a line with the attribute
    «кольцо» (its number within the record, from 1). A ring that does not repeat its first vertex gets it.
  * Z of the Z types is kept as it is; the other types have no Z: NaN, never 0. M is a vertex attribute «M»
    (a column of the attributes for points), left out when no vertex has a measure.
  * Values below −1e38 are «no data» (ESRI Shapefile Technical Description): NaN. A vertex without X or Y is
    left out, records without geometry and records deleted in the `.dbf` are skipped — all reported.
  * `.dbf` → attributes (`formats.dbf`): N and F are numbers (blank or `***` — NaN), a field of whole numbers
    too long for a float64 stays text (identifiers), C is text (blank — null), D is text `YYYY-MM-DD`, L is
    1 / 0 / NaN; other field types are skipped with a warning. Code page: `encoding`, else `.cpg`, else the
    language driver of the `.dbf` when it names a Cyrillic code page, else Windows-1251 with a warning.
  * `.prj` → `crs` (`bf_core.crs.CrsInfo`: WKT, name, EPSG); an unreadable one is a warning, not an error.
  * `.shx` is not needed: the records are walked in the `.shp` itself and checked against the file size before
    anything is allocated, so a header that promises more than the file holds is an error, not a «bomb».
`seq` numbers the vertices in the order of the file. `header` is a `ShpHeader`: geometry type, extent and the
fields of the `.dbf` as the file has them. Error codes: empty_file, not_shp, unsupported, truncated, broken,
no_lines. Geometry is read by pyshp record by record; files of single points whose records all have one length
and the columns of the `.dbf` are read as numpy tables (1 млн points with attributes in well under 10 s).
"""

from __future__ import annotations

import codecs
import mmap
import struct
import warnings as py_warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.formats.dbf import DbfField, DbfTable, codec_of, language_codec, read_dbf, unique_name
from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet, close, detect_closed
from bf_core.formats.str_layers import StrRead, n_text, scaled
from bf_core.formats.table import Progress, no_progress

if TYPE_CHECKING:
    from bf_core.crs import CrsInfo

DEFAULT_ENCODING = "cp1251"
NO_DATA = -1e38  # anything below is «no data»
CHUNK_ROWS = 1 << 17
TICK_VERTICES = 1 << 16  # progress (and a chance to cancel) at least this often
RING, MEASURE = "кольцо", "M"
POINT, LINE, POLYGON, MULTIPOINT = "point", "line", "polygon", "multipoint"
#: shape type → (kind, has Z, has M, name)
SHAPE_TYPES: dict[int, tuple[str, bool, bool, str]] = {
    1: (POINT, False, False, "Point"),
    3: (LINE, False, False, "PolyLine"),
    5: (POLYGON, False, False, "Polygon"),
    8: (MULTIPOINT, False, False, "MultiPoint"),
    11: (POINT, True, True, "PointZ"),
    13: (LINE, True, True, "PolyLineZ"),
    15: (POLYGON, True, True, "PolygonZ"),
    18: (MULTIPOINT, True, True, "MultiPointZ"),
    21: (POINT, False, True, "PointM"),
    23: (LINE, False, True, "PolyLineM"),
    25: (POLYGON, False, True, "PolygonM"),
    28: (MULTIPOINT, False, True, "MultiPointM"),
}
_HINT = "Скопируйте заново все файлы набора (.shp, .shx, .dbf, .prj, .cpg) или сохраните слой заново в ГИС."


@dataclass
class ShpHeader:
    shape_type: int
    type_name: str
    bbox: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax of the file header
    fields: list[DbfField] = field(default_factory=list)  # of the .dbf, as the file has them


@dataclass
class ShpRead(StrRead):
    """`records` — the records (objects) of the `.shp`; `crs` — from the `.prj`, None when there is none."""

    crs: CrsInfo | None = None


@dataclass
class _Geometry:
    xyz: np.ndarray  # N×3
    m: np.ndarray | None  # N; None — the type has no measures
    starts: np.ndarray | None  # first vertex of every part; None — points
    record: np.ndarray  # the record of every part (lines) or of every point
    ring: np.ndarray | None  # number of the part within its record, from 1
    records: int
    nulls: int = 0


def _fail(code: str, message: str, hint: str = _HINT) -> FormatError:
    return FormatError(code, message, hint)


def sidecar(path: Path, suffix: str) -> Path | None:
    """The file of the same set with another extension, whatever the case of its name."""
    for candidate in (path.with_suffix(suffix), path.with_suffix(suffix.upper())):
        if candidate.is_file():
            return candidate
    wanted = (path.stem + suffix).lower()
    try:
        return next((p for p in path.parent.iterdir() if p.name.lower() == wanted and p.is_file()), None)
    except OSError:
        return None


# ---------------------------------------------------------------- .shp


def _walk(buf: Any, size: int, tick: Progress) -> list[int]:
    """Length in bytes of the content of every record; every record is inside the file."""
    lengths: list[int] = []
    unpack = struct.Struct(">i").unpack_from
    pos = 100
    while pos < size:
        number = len(lengths) + 1
        if pos + 8 > size:
            raise _fail("truncated", f"Файл .shp обрезан: запись {n_text(number)} не дочитана.")
        length = 2 * unpack(buf, pos + 4)[0]
        if length < 4:
            raise _fail("broken", f"Файл .shp повреждён: запись {n_text(number)} имеет неверную длину.")
        if pos + 8 + length > size:
            raise _fail("truncated", f"Файл .shp обрезан: запись {n_text(number)} выходит за конец файла.")
        lengths.append(length)
        pos += 8 + length
        if not number & 0xFFFF:
            tick(pos / size, f"Проверка записей: {n_text(number)}")
    return lengths


def _points_fast(path: Path, size: int, shape_type: int, tick: Progress) -> _Geometry | None:
    """A file of single points whose records all have one length, read as a table; None — not such a file."""
    _, has_z, _, _ = SHAPE_TYPES[shape_type]
    with path.open("rb") as f:
        f.seek(104)
        first = f.read(4)
    if len(first) < 4:
        return None
    length = 2 * struct.unpack(">i", first)[0]
    measures = (shape_type == 21 and length == 28) or (shape_type == 11 and length == 36)
    if length != 20 + 8 * has_z + 8 * measures or (size - 100) % (8 + length):
        return None
    rows = (size - 100) // (8 + length)
    names = ["number", "length", "type", "x", "y"] + ["z"] * has_z + ["m"] * measures
    formats = [">i4", ">i4", "<i4", "<f8", "<f8"] + ["<f8"] * (has_z + measures)
    xyz = np.full((rows, 3), np.nan)
    m = np.empty(rows) if measures else None
    records: Any = np.memmap(path, dtype=np.dtype(list(zip(names, formats, strict=True))), mode="r", offset=100)
    try:
        for a in range(0, rows, CHUNK_ROWS):
            b = min(rows, a + CHUNK_ROWS)
            tick(a / rows, f"Чтение точек: {n_text(a)} из {n_text(rows)}")
            block = np.array(records[a:b])
            if (block["length"] != length // 2).any() or (block["type"] != shape_type).any():
                return None  # records without geometry or of another length: record by record
            xyz[a:b, 0], xyz[a:b, 1] = block["x"], block["y"]
            if has_z:
                xyz[a:b, 2] = block["z"]
            if m is not None:
                m[a:b] = block["m"]
    finally:
        mm = getattr(records, "_mmap", None)
        del records
        if mm is not None:
            mm.close()
    return _Geometry(xyz, m, None, np.arange(rows, dtype=np.int64), None, rows)


def _shapes(path: Path, size: int, shape_type: int, tick: Progress) -> _Geometry:
    """Every record through pyshp; the lengths of the records are checked first (`_walk`)."""
    import shapefile

    kind, has_z, has_m, _ = SHAPE_TYPES[shape_type]
    xy: list[Any] = []
    zs: list[float] = []
    ms: list[float | None] = []
    starts: list[int] = []
    record: list[int] = []
    ring: list[int] = []
    chunks: list[tuple[np.ndarray, np.ndarray | None, np.ndarray | None]] = []
    total = nulls = since = 0

    def flush() -> None:
        if xy:
            z = np.asarray(zs, dtype=np.float64) if has_z else None
            chunks.append((np.asarray(xy, dtype=np.float64), z, np.asarray(ms, dtype=np.float64) if has_m else None))
            xy.clear()
            zs.clear()
            ms.clear()

    with path.open("rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf:
            lengths = _walk(buf, size, scaled(tick, 0.0, 0.1))
        count = len(lengths)
        step = scaled(tick, 0.1, 1.0)
        with py_warnings.catch_warnings():
            py_warnings.simplefilter("ignore")  # pyshp warns in English; the checks above report in Russian
            reader = shapefile.ShpReader(shp=f)
            shapes = reader.iterShapes(shape_lengths_B=lengths)
            try:
                for index in range(count):
                    if since >= TICK_VERTICES or not index & 0xFFF:  # often enough to cancel within a second
                        since = 0
                        step(index / count, f"Чтение объектов: {n_text(index)} из {n_text(count)}")
                        if len(xy) >= 1 << 19:
                            flush()
                    try:
                        shape = next(shapes)
                    except (struct.error, shapefile.ShapefileException, LookupError, ValueError, StopIteration) as exc:
                        raise _fail("broken", f"Файл .shp повреждён: запись {n_text(index + 1)} не читается.") from exc
                    points = shape.points if shape is not None and shape.shapeType else None
                    if not points:
                        nulls += 1
                        continue
                    n = len(points)
                    xy.extend(points)
                    if has_z:
                        zs.extend(shape.z)
                    if has_m:
                        ms.extend(shape.m)
                    if kind in (POINT, MULTIPOINT):
                        record.extend([index] * n)
                    else:
                        parts = shape.parts or (0,)
                        if parts[0] != 0:  # its first vertices would join the object before it
                            raise _fail("broken", f"Файл .shp повреждён: запись {n_text(index + 1)} не читается.")
                        for k, first in enumerate(parts):
                            starts.append(total + first)
                            record.append(index)
                            ring.append(k + 1)
                    total += n
                    since += n
            finally:
                reader.close()
    flush()
    xyz = np.full((total, 3), np.nan)
    m = np.empty(total) if has_m else None
    a = 0
    for part_xy, part_z, part_m in chunks:
        b = a + len(part_xy)
        xyz[a:b, :2] = part_xy
        if part_z is not None:
            xyz[a:b, 2] = part_z
        if m is not None and part_m is not None:
            m[a:b] = part_m
        a = b
    lines = kind in (LINE, POLYGON)
    return _Geometry(
        xyz,
        m,
        np.asarray(starts, dtype=np.int64) if lines else None,
        np.asarray(record, dtype=np.int64),
        np.asarray(ring, dtype=np.float64) if kind == POLYGON else None,
        count,
        nulls,
    )


# ---------------------------------------------------------------- the layer


def _layer(name: str, kind: str, g: _Geometry, dbf: DbfTable | None, warnings: list[str]) -> LineSet:
    import pyarrow as pa

    xyz, m, record = g.xyz, g.m, g.record
    with np.errstate(invalid="ignore"):
        xyz[~(xyz >= NO_DATA)] = np.nan  # «no data» and infinities; NaN stays NaN
        xyz[np.isinf(xyz)] = np.nan
        if m is not None:
            m = np.where(np.isfinite(m) & (m >= NO_DATA), m, np.nan)
    keep = np.isfinite(xyz[:, :2]).all(axis=1)
    missing = int(len(keep) - keep.sum())
    if missing:
        warnings.append(f"{n_text(missing)} вершин без координаты X или Y пропущены")
    seq = np.arange(len(xyz), dtype=np.int64)
    offsets = closed = None
    ring = g.ring
    if g.starts is None:
        if dbf is not None and dbf.deleted.any():
            keep &= ~dbf.deleted[record]
        if not keep.all():
            xyz, record, seq = xyz[keep], record[keep], seq[keep]
            m = m[keep] if m is not None else None
    else:
        bounds = np.append(g.starts, len(xyz))
        if (np.diff(bounds) < 0).any() or (len(bounds) > 1 and bounds[0] < 0):
            raise _fail("broken", "Файл .shp повреждён: части объекта заданы не по порядку.")
        if dbf is not None and dbf.deleted.any():
            keep &= ~np.repeat(dbf.deleted[record], np.diff(bounds))
        csum = np.concatenate([[0], np.cumsum(keep)])
        counts = csum[bounds[1:]] - csum[bounds[:-1]]  # vertices left in every part
        full = counts > 0
        if not keep.all():
            xyz, seq = xyz[keep], seq[keep]
            m = m[keep] if m is not None else None
        record, counts = record[full], counts[full]
        ring = ring[full] if ring is not None else None
        offsets = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
        if kind == POLYGON:
            xyz, offsets, closed, take = close(xyz, offsets, np.ones(len(counts), dtype=bool))
            if take is not None:
                seq = seq[take]
                m = m[take] if m is not None else None
            still_open = int(len(closed) - closed.sum())
            if still_open:
                warnings.append(f"{n_text(still_open)} колец из менее чем трёх вершин оставлены незамкнутыми")
        else:
            closed = detect_closed(xyz, offsets)
    if len(xyz) == 0:
        raise _fail("no_lines", "В файле нет ни одного объекта с координатами.", "Проверьте, что слой не пуст.")

    names: list[str] = []
    arrays: list[Any] = []
    taken: set[str] = set()
    if dbf is not None:
        whole = len(record) == dbf.rows and bool((record == np.arange(dbf.rows)).all())
        index = None if whole else pa.array(record, type=pa.int64())
        for column_name, column in zip(dbf.names, dbf.columns, strict=True):
            values = pa.array(column, type=pa.float64()) if isinstance(column, np.ndarray) else column
            names.append(unique_name(column_name, taken))
            arrays.append(values if index is None else values.take(index))
    if ring is not None:
        names.append(unique_name(RING, taken))
        arrays.append(pa.array(ring, type=pa.float64()))
    measured = m is not None and bool(np.isfinite(m).any())
    vattrs = None
    if measured and offsets is None:
        names.append(unique_name(MEASURE, taken))
        arrays.append(pa.array(m, type=pa.float64()))
    elif measured:
        vattrs = pa.table([pa.array(m, type=pa.float64())], names=[MEASURE])
    attrs = pa.table(arrays, names=names) if arrays else None
    return LineSet(name, xyz, offsets, closed, seq.astype(np.uint32), attrs, vattrs)


def read_shp(
    path: str | Path,
    progress: Progress | None = None,
    *,
    encoding: str | None = None,
    dbf: str | Path | None = None,
    prj: str | Path | None = None,
    cpg: str | Path | None = None,
) -> ShpRead:
    """Read a shapefile into one layer (see the module docstring). `path` — the `.shp`; the other files of the
    set are found next to it (any case of the extension) unless given; `encoding` overrides the code page."""
    path = Path(path)
    tick = progress or no_progress
    tick(0.0, "Чтение Shapefile")
    codec, declared_codec = DEFAULT_ENCODING, False
    if encoding:
        try:
            codec, declared_codec = codecs.lookup(encoding).name, True
        except LookupError as exc:
            hint = "Выберите кодировку из списка."
            raise _fail("unknown_encoding", f"Неизвестная кодировка «{encoding}».", hint) from exc
    size = path.stat().st_size
    if size == 0:
        raise _fail("empty_file", "Файл пуст.", "Проверьте, что файл скопирован полностью.")
    with path.open("rb") as f:
        head = f.read(100)
    if head[:4] != struct.pack(">i", 9994):
        raise _fail("not_shp", "Это не файл Shapefile (.shp).", "Выберите файл .shp набора Shapefile.")
    if len(head) < 100:
        raise _fail("truncated", "Файл .shp обрезан: в нём нет заголовка.")
    declared = 2 * struct.unpack(">i", head[24:28])[0]
    shape_type = struct.unpack("<i", head[32:36])[0]
    bbox = struct.unpack("<4d", head[36:68])
    if shape_type == 31:
        raise _fail(
            "unsupported",
            "Shapefile с объёмными телами (MultiPatch) не поддерживается.",
            "Сохраните слой в ГИС как линии, полигоны или точки либо экспортируйте поверхность в DXF.",
        )
    if shape_type == 0:
        raise _fail("no_lines", "В файле нет ни одного объекта.", "Проверьте, что слой не пуст.")
    if shape_type not in SHAPE_TYPES:
        raise _fail("broken", f"Файл .shp повреждён: неизвестный тип геометрии {shape_type}.")
    kind, _has_z, _has_m, type_name = SHAPE_TYPES[shape_type]
    warnings: list[str] = []
    if declared != size:
        warnings.append(
            f"Длина файла в заголовке .shp ({n_text(declared)} байт) не равна настоящей ({n_text(size)} байт): "
            "возможно, файл скопирован не полностью"
        )

    geometry = _points_fast(path, size, shape_type, scaled(tick, 0.0, 0.25)) if kind == POINT else None
    if geometry is None:
        geometry = _shapes(path, size, shape_type, scaled(tick, 0.25 if kind == POINT else 0.0, 0.5))
    if geometry.records == 0:
        raise _fail("no_lines", "В файле нет ни одного объекта.", "Проверьте, что слой не пуст.")
    if geometry.nulls:
        warnings.append(f"{n_text(geometry.nulls)} записей без геометрии пропущены")

    dbf_path = Path(dbf) if dbf else sidecar(path, ".dbf")
    cpg_path = Path(cpg) if cpg else sidecar(path, ".cpg")
    prj_path = Path(prj) if prj else sidecar(path, ".prj")
    if not declared_codec and cpg_path is not None:
        with cpg_path.open("rb") as f:
            label = f.read(256).decode("ascii", "replace")
        found = codec_of(label)
        if found is None:
            warnings.append(f"Кодировка «{label.strip()}» из файла .cpg неизвестна: текст прочитан как Windows-1251")
        else:
            codec, declared_codec = found, True
    table: DbfTable | None = None
    if dbf_path is None:
        warnings.append("Рядом с файлом нет таблицы .dbf: атрибуты не загружены")
    else:
        if not declared_codec:
            found = language_codec(dbf_path)
            if found is not None:
                codec, declared_codec = found, True
        table = read_dbf(dbf_path, codec, scaled(tick, 0.5, 0.85), warnings)
        if table.rows != geometry.records:
            raise _fail(
                "broken",
                f"Число записей в .dbf ({n_text(table.rows)}) не равно числу объектов в .shp "
                f"({n_text(geometry.records)}): атрибуты нельзя сопоставить с объектами.",
                _HINT + " Чтобы загрузить только геометрию, уберите файл .dbf.",
            )
        if not declared_codec and not table.plain:
            warnings.append("Кодировка не указана (нет файла .cpg): текст прочитан как Windows-1251")
        removed = int(table.deleted.sum())
        if removed:
            warnings.append(f"{n_text(removed)} записей помечены в .dbf как удалённые: пропущены")
    tick(0.85, "Сборка слоя")
    layer = _layer(path.stem, kind, geometry, table, warnings)

    crs = None
    if prj_path is not None:
        from bf_core.crs import CrsError, read_prj

        try:
            crs = read_prj(prj_path)
        except (CrsError, OSError) as exc:
            warnings.append(f"Система координат из файла .prj не распознана: объект загружен без СК ({exc})")
    header = ShpHeader(shape_type, type_name, (bbox[0], bbox[1], bbox[2], bbox[3]), table.fields if table else [])
    tick(1.0, f"Прочитано вершин: {n_text(layer.point_count)}")
    return ShpRead("shp", [layer], header, codec, geometry.records, warnings, crs)


__all__ = ["ShpHeader", "ShpRead", "read_shp", "sidecar"]
