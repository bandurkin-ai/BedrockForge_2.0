"""Micromine TRIDB reader (ТЗ 7.21, 9.4, 9.12; ADR 0013 §2): every wireframe of the file with its name, colour
and attributes; the geometry is read from its BLOB in portions, one wireframe at a time.

A TRIDB is an SQLite database with UTF-16 text. `GeneralInformation` — a row per wireframe (name, `Colour` as
0x00BBGGRR, extent, `Volume`, `SurfaceArea`, dates, flags); `Geometry` — its BLOB; `GeometryProperties` — rows
«property — value» (`PointCount`, `TriangleCount`, `Volume`, …; an older file has a column per property);
`UserAttributeDefinition` / `UserAttributeValue` — attributes of the user (`Type` 0 — text; an empty text is an
empty value); `PointAttribute*`, `TriangleAttribute*` (not loaded: a warning), `DisplayProperties`,
`DatabaseInformation`.

The geometry BLOB (little-endian): `FF FF FF FF`, version `00 00 05 00`, int64 length of the GUID (36), int64 9,
the GUID in ASCII, then blocks «int64 length, int64 number, data»: 1, 2, 3 — X, Y, Z as doubles, 4 — the
triangles as int32 triples from 0. Checked: the blocks fill the BLOB exactly, X, Y and Z have one length, the
numbers of vertices are in range, every coordinate is a number; the counts and the extent of the file are
compared with the geometry (a warning). Another version is read only when all of this holds, with a warning.

    open_tridb(path)        `Tridb`: `entries` — the composition without geometry, `read(entry)` — a `MeshSet`
    inspect_tridb(path)     (entries, warnings)
    read_tridb(path)        the wireframes one by one; the first that cannot be read stops the reading
    check_measures(…)       `Volume` and `SurfaceArea` of the file against the measures of the geometry (1e-9)

The vertices are not welded: the vertex list of the file is its geometry, and the BLOB is written back byte for
byte (`tridb_write.py`). `MeshSet.source["tridb"]` keeps what the writer needs for equal rows: {id, guid, points,
triangles, general — the `GeneralInformation` row by column (the name is `source["name"]`), properties —
[[rowid, Property, TextValue, RealValue(, GUID when it is not the GUID of the geometry)]], attr_defs — the rows
of `UserAttributeDefinition`, attr_values — [[rowid, AttributeID, ValueText, ValueInteger, ValueReal]], display,
database}.

The file is opened read-only and immutable (`db.dialect.open_sqlite_file`): nothing is written next to it.
Error codes: empty_file, not_tridb, truncated, broken, unsupported, no_meshes, too_large, bad_mesh,
bad_coordinates, empty_mesh.

Ported from BF `formats/wireframes.py` (`read_tridb`, `read_tridb_geometry`, `_attributes`). Differences: the
tables of the real files (properties and attributes as rows), a composition without geometry, a wireframe that
cannot be read does not lose the others, source rows, progress and cancellation, negative cases.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.formats.errors import FormatError
from bf_core.formats.meshset import MeshEntry, MeshSet, attr_value, clean_attrs, clean_name, n_text, source_of
from bf_core.formats.str_layers import scaled
from bf_core.formats.table import Progress, no_progress
from bf_core.formats.tridb_blob import HINT, Layout, blob_layout, fail, fill, quick_counts

FORMAT = "tridb"
SQLITE_MAGIC = b"SQLite format 3\x00"
MAX_MESHES = 50_000
MAX_PROPERTIES = 64  # rows of GeometryProperties kept for a wireframe
MAX_DEFS = 1000
MAX_VALUES = 2_000_000  # rows of UserAttributeValue kept for a file
MAX_SOURCE_BYTES = 64 << 10  # of `meta.source` as JSON
REL_TOL = 1e-9
STEPS = 50_000  # SQLite steps between two looks at the cancellation
GENERAL = (
    "ID", "Name", "Code", "Title", "Colour", "CreateDateTime", "CreateBy", "CreateCompany", "EditDateTime",
    "EditBy", "EditCompany", "MetaNotes", "Accuracy", "XMinimum", "XMaximum", "YMinimum", "YMaximum", "ZMinimum",
    "ZMaximum", "DTMPlane", "Volume", "SurfaceArea", "Validated", "IsLocked", "LockBy", "LockReason",
    "LockDateTime",
)  # fmt: skip
EXTENT = ("XMinimum", "YMinimum", "ZMinimum", "XMaximum", "YMaximum", "ZMaximum")
PROPERTIES = ("TriangulationID", "GUID", "Property", "TextValue", "RealValue")
DEFINITION = (
    "ID", "Name", "Type", "Precision", "DefaultValueText", "DefaultValueInteger", "DefaultValueReal", "Properties",
    "ProtoProperties",
)  # fmt: skip
VALUES = ("TriangulationID", "AttributeID", "ValueText", "ValueInteger", "ValueReal")
DISPLAY = ("TriangulationID", "SliceHatch", "SilhouetteHatch", "LabelPosition", "LabelOffset")
DATABASE = ("Version", "CreateDateTime", "OriginalProject")
TABLES = frozenset(
    {
        "DatabaseInformation", "GeneralInformation", "Geometry", "UserAttributeDefinition", "UserAttributeValue",
        "PointAttributeDefinition", "PointAttributeValue", "TriangleAttributeDefinition", "TriangleAttributeValue",
        "DisplayProperties", "GeometryProperties",
    }
)  # fmt: skip


def bgr_to_hex(value: Any) -> str | None:
    """Micromine keeps a colour as a Windows COLORREF (0x00BBGGRR)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return f"#{value & 0xFF:02x}{(value >> 8) & 0xFF:02x}{(value >> 16) & 0xFF:02x}"


def value_of(kind: Any, text: Any, integer: Any, real: Any) -> Any:
    """The value of a `UserAttributeValue` row: `Type` 0 — its text (empty → None); another type (no sample
    yet) — the real, else the integer, else the text."""
    found = text if kind in (0, None) else real if real is not None else integer if integer is not None else text
    return attr_value(found) if isinstance(found, str | int | float) else None


def _json(value: Any) -> Any:
    """A value of an SQLite row as JSON keeps it (`meta.source`): a number that is not finite is empty."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, bytes):
        return {"blob": value.hex()}
    return value


def _precheck(path: Path) -> list[str]:
    """The file is an SQLite database of its declared size; warnings about a journal left next to it."""
    size = path.stat().st_size
    if size == 0:
        raise fail("empty_file", "Файл TRIDB пуст.", "Выберите файл с каркасами.")
    with path.open("rb") as f:
        head = f.read(100)
    if head[:16] != SQLITE_MAGIC:
        raise fail("not_tridb", "Файл не является базой каркасов TRIDB (это не SQLite).", "Выберите файл .tridb.")
    page = int.from_bytes(head[16:18], "big") if len(head) >= 100 else 0
    page = 65536 if page == 1 else page
    if page < 512 or page & (page - 1):
        raise fail("truncated" if len(head) < 100 else "broken", "Файл TRIDB повреждён: заголовок базы неверен.")
    pages = int.from_bytes(head[28:32], "big")
    if (pages and head[24:28] == head[92:96] and pages * page > size) or size < page:
        raise fail(
            "truncated",
            f"Файл TRIDB обрезан: в нём {n_text(size)} байт из {n_text(max(pages, 1) * page)}.",
            "Скопируйте файл заново: копирование или загрузка прервались.",
        )
    left = [s for s in ("-journal", "-wal") if (j := Path(str(path) + s)).is_file() and j.stat().st_size]
    if left:
        return [
            f"Рядом с файлом лежит журнал незавершённой записи ({', '.join(left)}): последние изменения "
            "могли не попасть в файл — откройте и сохраните его в Micromine"
        ]
    return []


# --- the file --------------------------------------------------------------------------------------------


@dataclass
class _Item:
    """What the composition knows about a wireframe besides its `MeshEntry`."""

    rowid: int
    size: int
    raw_name: Any
    general: dict[str, Any] | None
    properties: list[list[Any]] | None = None  # None — not kept (too many, or an older table)
    counts: tuple[Any, Any] | None = None  # PointCount, TriangleCount the file declares
    values: list[list[Any]] = field(default_factory=list)
    display: list[Any] | None = None


class Tridb:
    """An open TRIDB: `entries` (the composition, no geometry) and `warnings` of the file; `read(entry)` gives
    one wireframe. Close it (or use `with`): the file stays open until then."""

    def __init__(self, path: str | Path, progress: Progress | None = None) -> None:
        self.path = Path(path)
        self.entries: list[MeshEntry] = []
        self.warnings: list[str] = _precheck(self.path)
        self._items: dict[int | str, _Item] = {}
        self._defs: list[list[Any]] | None = None
        self._database: list[list[Any]] = []
        self._tick: Progress = progress or no_progress
        self._at = 0.0
        self._stopped: BaseException | None = None
        from bf_core.db.dialect import open_sqlite_file

        try:
            self._conn = open_sqlite_file(self.path)  # read-only, immutable: nothing is written next to the file
        except sqlite3.Error as exc:
            raise fail("broken", "Файл TRIDB не открывается как база данных.") from exc
        try:
            self._conn.text_factory = lambda b: b.decode("utf-8", "replace")
            self._conn.set_progress_handler(self._steps, STEPS)
            try:
                self._load()
            except sqlite3.Error as exc:
                raise self._sqlite(exc) from exc
        except BaseException:
            self.close()
            raise
        self._tick(1.0, f"Каркасов в файле: {n_text(len(self.entries))}")

    def __enter__(self) -> Tridb:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def _steps(self) -> int:
        """SQLite calls this while a statement runs: a cancel raised by `progress` stops the statement."""
        try:
            self._tick(self._at, None)
        except BaseException as exc:
            self._stopped = exc
            return 1
        return 0

    def _sqlite(self, exc: sqlite3.Error) -> BaseException:
        if self._stopped is not None:
            stopped, self._stopped = self._stopped, None
            return stopped
        if "too big" in str(exc):
            return fail("too_large", "Каркас в файле TRIDB больше 1 ГБ: такой каркас не читается.", HINT)
        return fail("broken", "Файл TRIDB повреждён: база данных не читается.")

    # --- the composition ---------------------------------------------------------------------------------

    def _columns(self, table: str) -> list[str]:
        return [str(c[0]) for c in self._conn.execute(f'SELECT * FROM "{table}" LIMIT 0').description]

    def _rows(self, table: str, wanted: Sequence[str], order: str, limit: int | None = None) -> Iterator[list[Any]]:
        """`rowid` and the `wanted` columns of a table (None for a column the file does not have)."""
        have = set(self._columns(table))
        cols = ", ".join(f'"{c}"' if c in have else "NULL" for c in wanted)
        sql = f'SELECT rowid, {cols} FROM "{table}" ORDER BY {order}' + (f" LIMIT {limit}" if limit else "")
        return (list(row) for row in self._conn.execute(sql))

    def _load(self) -> None:
        self._tick(0.0, "Чтение состава TRIDB")
        master = self._conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall()
        tables = {str(n) for n, sql in master if str(sql or "").lstrip().upper().startswith("CREATE TABLE")}
        if not {"GeneralInformation", "Geometry"} <= tables or not {"ID", "Geometry"} <= set(self._columns("Geometry")):
            raise fail(
                "not_tridb",
                "В файле нет таблиц каркасов GeneralInformation и Geometry: это не TRIDB.",
                "Выберите файл .tridb, сохранённый Micromine.",
            )
        unknown = sorted(t for t in tables - TABLES if not t.startswith("sqlite_"))
        if unknown:
            self.warnings.append(f"Неизвестные таблицы в файле не загружены: {', '.join(unknown[:8])}")
        extra = [c for c in self._columns("GeneralInformation") if c not in GENERAL]
        if extra:
            self.warnings.append(f"Неизвестные столбцы GeneralInformation не загружены: {', '.join(extra[:8])}")
        geometry = self._conn.execute(
            f"SELECT rowid, ID, typeof(Geometry), length(Geometry) FROM Geometry ORDER BY ID LIMIT {MAX_MESHES + 1}"
        ).fetchall()
        too_many = fail(
            "too_large",
            f"В файле TRIDB больше {n_text(MAX_MESHES)} каркасов.",
            "Разделите файл на несколько в Micromine.",
        )
        if len(geometry) > MAX_MESHES:
            raise too_many
        rows = self._rows("GeneralInformation", GENERAL, "ID", MAX_MESHES + 1)
        info = {row[1]: dict(zip(GENERAL, row[1:], strict=True)) for row in rows}
        if len(info) > MAX_MESHES:
            raise too_many
        self._at = 0.2
        props, counts, keyed = self._properties(tables)
        self._at = 0.4
        values, undefined = self._attributes(tables)
        self._at = 0.6
        unloaded = self._element_attributes(tables)
        display = (
            {row[1]: [_json(v) for v in row[2:]] for row in self._rows("DisplayProperties", DISPLAY, "rowid")}
            if "DisplayProperties" in tables
            else {}
        )
        if "DatabaseInformation" in tables:
            self._database = [
                [_json(v) for v in r[1:]] for r in self._rows("DatabaseInformation", DATABASE, "rowid", 8)
            ]
        if undefined:
            self.warnings.append(f"Значения атрибутов без определения атрибута не загружены: {n_text(undefined)}")
        seen: set[Any] = set()
        for k, (rowid, key, kind, size) in enumerate(geometry):
            seen.add(key)
            general = info.get(key)
            raw = general.get("Name") if general else None
            name = clean_name(raw if isinstance(raw, str) else None, f"Каркас {key}")
            rows_of = props.get(key, [] if keyed else None)  # no rows in the file is not «rows not kept»
            item = _Item(int(rowid), int(size or 0), raw, general, rows_of, counts.get(key), values.get(key, []))
            item.display = display.get(key)
            entry = MeshEntry(key, name, None, None, color=bgr_to_hex(general.get("Colour")) if general else None)
            if isinstance(key, bool) or not isinstance(key, int):
                entry.key = f"rowid:{rowid}"
                entry.error = fail("broken", f"Каркас «{name}»: номер каркаса в файле — не целое число.").as_dict()
            elif kind != "blob" or not size:
                entry.error = fail("broken", f"Каркас «{name}»: в файле нет его геометрии.").as_dict()
            else:
                self._describe(entry, item, key in unloaded)
            self._items[entry.key] = item
            self.entries.append(entry)
            if k % 256 == 0:
                self._tick(0.6 + 0.4 * k / len(geometry), "Чтение состава TRIDB")
        for key, general in info.items():
            if key not in seen:
                name = clean_name(general["Name"] if isinstance(general["Name"], str) else None, f"Каркас {key}")
                error = fail("broken", f"Каркас «{name}»: в файле нет его геометрии.").as_dict()
                self.entries.append(MeshEntry(f"general:{key}", name, None, None, error=error))
        if not self.entries:
            raise fail("no_meshes", "В файле TRIDB нет каркасов.", "Выберите файл, в котором есть каркасы.")

    def _properties(
        self, tables: set[str]
    ) -> tuple[dict[Any, list[list[Any]] | None], dict[Any, tuple[Any, Any]], bool]:
        """(rows [rowid, GUID, Property, TextValue, RealValue] per wireframe — None when there are too many to
        keep, the counts the file declares, the table has a row per property)."""
        props: dict[Any, list[list[Any]] | None] = {}
        counts: dict[Any, tuple[Any, Any]] = {}
        if "GeometryProperties" not in tables:
            return props, counts, False
        have = set(self._columns("GeometryProperties"))
        if set(PROPERTIES) <= have:
            for rowid, key, *rest in self._rows("GeometryProperties", PROPERTIES, "rowid"):
                rows = props.setdefault(key, [])
                if rows is not None and len(rows) >= MAX_PROPERTIES:
                    props[key] = rows = None
                if rows is not None:
                    rows.append([rowid, *(_json(v) for v in rest)])
                if rest[1] in ("PointCount", "TriangleCount"):  # the last row of a property is the current one
                    n, m = counts.get(key, (None, None))
                    counts[key] = (rest[3], m) if rest[1] == "PointCount" else (n, rest[3])
            return props, counts, True
        if {"ID", "PointCount", "TriangleCount"} <= have:  # an older file: a column per property
            for _rowid, key, n, m in self._rows("GeometryProperties", ("ID", "PointCount", "TriangleCount"), "rowid"):
                counts[key] = (n, m)
        else:
            self.warnings.append("Таблица свойств геометрии GeometryProperties устроена иначе: свойства не загружены")
        return props, counts, False

    def _attributes(self, tables: set[str]) -> tuple[dict[Any, list[list[Any]]], int]:
        """(rows [rowid, AttributeID, ValueText, ValueInteger, ValueReal] per wireframe, values without a
        definition); the definitions go to `self._defs`."""
        values: dict[Any, list[list[Any]]] = {}
        if not {"UserAttributeDefinition", "UserAttributeValue"} <= tables:
            return values, 0
        if not {"ID", "Name"} <= set(self._columns("UserAttributeDefinition")) or not set(VALUES) <= set(
            self._columns("UserAttributeValue")
        ):
            self.warnings.append("Таблицы атрибутов каркасов устроены иначе: атрибуты не загружены")
            return values, 0
        defs = [
            [_json(v) for v in row[1:]] for row in self._rows("UserAttributeDefinition", DEFINITION, "ID", MAX_DEFS + 1)
        ]
        if len(defs) > MAX_DEFS:
            self.warnings.append(f"Определений атрибутов больше {n_text(MAX_DEFS)}: атрибуты не загружены")
            return values, 0
        self._defs = defs
        known = {d[0] for d in defs}
        types = sorted({d[2] for d in defs if d[2] not in (0, None)}, key=str)
        if types:
            self.warnings.append(
                f"Типы атрибутов {', '.join(map(str, types))} не встречались в проверенных файлах: значения "
                "прочитаны из числовых столбцов — проверьте их"
            )
        undefined = 0
        for k, (rowid, key, attr, *rest) in enumerate(
            self._rows("UserAttributeValue", VALUES, "rowid", MAX_VALUES + 1)
        ):
            if k >= MAX_VALUES:
                self.warnings.append(f"Значений атрибутов больше {n_text(MAX_VALUES)}: атрибуты не загружены")
                self._defs = None
                return {}, 0
            if attr in known:
                values.setdefault(key, []).append([rowid, attr, *(_json(v) for v in rest)])
            else:
                undefined += 1
        return values, undefined

    def _element_attributes(self, tables: set[str]) -> set[Any]:
        """Wireframes with attributes of points or triangles (not loaded)."""
        found: set[Any] = set()
        for table in ("PointAttributeValue", "TriangleAttributeValue"):
            if table in tables and "TriangulationID" in self._columns(table):
                found.update(r[0] for r in self._conn.execute(f'SELECT DISTINCT TriangulationID FROM "{table}"'))
        return found

    def _describe(self, entry: MeshEntry, item: _Item, element_attrs: bool) -> None:
        """Counts, extent, attributes and warnings of an entry (the head of its BLOB is read, not the blocks)."""
        try:
            with self._conn.blobopen("Geometry", "Geometry", item.rowid, readonly=True) as blob:
                entry.points, entry.triangles = quick_counts(blob, item.size)
        except (FormatError, sqlite3.Error) as exc:
            if self._stopped is not None:
                raise
            code = exc.code if isinstance(exc, FormatError) else "broken"
            entry.error = fail(code, f"Каркас «{entry.name}»: неизвестная структура геометрии TRIDB.").as_dict()
            return
        stated = item.counts or (None, None)
        if entry.triangles is None and isinstance(stated[1], int | float) and 0 <= stated[1] < 2**53:
            entry.triangles = int(stated[1])
        box = [item.general.get(c) for c in EXTENT] if item.general else []
        if box and all(isinstance(x, float) and math.isfinite(x) for x in box):
            entry.extent = [x for x in box if isinstance(x, float)]
        types = {d[0]: d[2] for d in self._defs or []}
        names = {d[0]: d[1] for d in self._defs or []}
        by_attr = {row[1]: row for row in item.values}
        pairs = [(names[a], value_of(types[a], *by_attr[a][2:])) for a in names if a in by_attr]
        entry.attrs, entry.warnings = clean_attrs(pairs)
        if element_attrs:
            entry.warnings.append("Атрибуты точек и треугольников каркаса не загружаются")

    # --- a wireframe -------------------------------------------------------------------------------------

    def size_of(self, entry: MeshEntry) -> int:
        """Bytes of the geometry of an entry (for the progress of reading several)."""
        return self._items[entry.key].size

    def read(self, entry: MeshEntry | int | str, progress: Progress | None = None) -> MeshSet:
        """The wireframe of an entry (or of its key). FormatError — this wireframe cannot be read; the file
        stays open for the others. `progress(fraction, message)` may raise to cancel."""
        found = entry if isinstance(entry, MeshEntry) else next((e for e in self.entries if e.key == entry), None)
        if found is not None and found.error:
            raise FormatError(**found.error)
        if found is None or found.key not in self._items:
            raise fail("broken", "Такого каркаса в файле TRIDB нет.", "Обновите состав файла.")
        tick = progress or no_progress
        item, name = self._items[found.key], found.name
        self._tick, self._at = tick, 0.0
        try:
            with self._conn.blobopen("Geometry", "Geometry", item.rowid, readonly=True) as blob:
                layout = blob_layout(blob, item.size, name)
                n, m = layout.points, layout.triangles
                xyz = np.empty((n, 3), dtype=np.float64)
                tri = np.empty((m, 3), dtype=np.int32)
                total, done = max(24 * n + 12 * m, 1), 0

                def step(read: int) -> None:
                    nonlocal done
                    done += read
                    tick(done / total, f"Чтение каркаса «{name}»")

                for k in range(3):
                    fill(blob, layout.blocks[k + 1], xyz[:, k], step)
                fill(blob, layout.blocks[4], tri.reshape(-1), step)
        except sqlite3.Error as exc:
            raise self._sqlite(exc) from exc
        mesh = MeshSet(name, xyz, tri, color=found.color, attrs=found.attrs, warnings=list(found.warnings))
        self._check(mesh, item, layout)
        mesh.source = self._source(mesh, found.key, item, layout)
        return mesh

    def _check(self, mesh: MeshSet, item: _Item, layout: Layout) -> None:
        """Compare the geometry with what the file says about it (what it does not say is not compared). A
        version that was not checked on real files is taken only when the file states the counts and the extent
        and all of them agree."""
        notes: list[str] = []
        n, m = item.counts or (None, None)
        if (n is not None and n != mesh.point_count) or (m is not None and m != mesh.triangle_count):
            notes.append(
                f"В свойствах файла {_count(n)} вершин и {_count(m)} треугольников, в геометрии — "
                f"{n_text(mesh.point_count)} и {n_text(mesh.triangle_count)}: взята геометрия"
            )
        box = [item.general.get(c) for c in EXTENT] if item.general else []
        stated = bool(box) and all(isinstance(x, int | float) for x in box)
        if stated and box != mesh.extent():
            notes.append("Охват каркаса в файле не совпадает с его геометрией: взят охват геометрии")
        extra = sorted(k for k in layout.blocks if k not in (1, 2, 3, 4))
        if extra:
            notes.append(f"Блоки геометрии № {', '.join(map(str, extra))} неизвестны и не загружены")
        if not layout.standard:
            shown = int.from_bytes(layout.version[2:], "little")
            if notes or not stated or n is None or m is None:
                raise fail(
                    "unsupported",
                    f"Каркас «{mesh.name}»: версия геометрии TRIDB {shown} не поддерживается (проверена версия 5).",
                    "Сохраните каркасы в текущей версии Micromine или экспортируйте их в DXF.",
                )
            notes.append(f"Версия геометрии TRIDB {shown} не встречалась в проверенных файлах: проверьте каркас")
        mesh.warnings.extend(notes)

    def _source(self, mesh: MeshSet, key: int | str, item: _Item, layout: Layout) -> dict[str, Any]:
        tridb: dict[str, Any] = {"id": key, "guid": layout.guid, "points": mesh.point_count}
        tridb["triangles"] = mesh.triangle_count
        if item.general is not None:
            tridb["general"] = {c: _json(v) for c, v in item.general.items() if c not in ("ID", "Name")}
        if item.properties is not None:  # the GUID of a row is kept only when it is not the GUID of the geometry
            tridb["properties"] = [[r[0], *r[2:], *([] if r[1] == layout.guid else [r[1]])] for r in item.properties]
        if self._defs is not None:
            tridb["attr_defs"], tridb["attr_values"] = self._defs, item.values
        if item.display is not None:
            tridb["display"] = item.display
        if self._database:
            tridb["database"] = self._database
        source = source_of(FORMAT, item.raw_name, mesh.name, tridb=tridb)
        for heavy in (("attr_values", "attr_defs"), ("properties",), ("general", "display", "database")):
            if len(json.dumps(source, ensure_ascii=False)) <= MAX_SOURCE_BYTES:
                break
            for k in heavy:
                tridb.pop(k, None)
            note = "Служебные строки исходного файла слишком велики и сохранены не полностью: экспорт создаст их заново"
            if note not in mesh.warnings:
                mesh.warnings.append(note)
        return source


def _count(value: Any) -> str:
    return n_text(int(value)) if isinstance(value, int | float) and math.isfinite(value) else "неизвестно сколько"


def open_tridb(path: str | Path, progress: Progress | None = None) -> Tridb:
    return Tridb(path, progress)


def inspect_tridb(path: str | Path, progress: Progress | None = None) -> tuple[list[MeshEntry], list[str]]:
    """The composition of a TRIDB without its geometry: (entries, warnings of the file)."""
    with Tridb(path, progress) as f:
        return f.entries, f.warnings


def read_tridb(
    path: str | Path, keys: Iterable[int | str] | None = None, progress: Progress | None = None
) -> Iterator[MeshSet]:
    """The wireframes of a TRIDB one at a time (`keys` — only these, in the order of the file). A wireframe
    that cannot be read raises its FormatError; to skip it and go on use `open_tridb`. The warnings of the
    file come with the first wireframe."""
    tick = progress or no_progress
    with Tridb(path, scaled(tick, 0.0, 0.05)) as f:
        wanted = None if keys is None else set(keys)
        todo = [e for e in f.entries if wanted is None or e.key in wanted]
        total = max(sum(f.size_of(e) for e in todo if not e.error), 1)
        done = 0
        for entry in todo:
            size = 0 if entry.error else f.size_of(entry)
            mesh = f.read(entry, scaled(tick, 0.05 + 0.95 * done / total, 0.05 + 0.95 * (done + size) / total))
            mesh.warnings[:0] = f.warnings if entry is todo[0] else []
            done += size
            yield mesh
        tick(1.0, f"Прочитано каркасов: {n_text(len(todo))}")


# --- measures of the file against the geometry -----------------------------------------------------------


def declared(source: Mapping[str, Any]) -> dict[str, float | None]:
    """`Volume` and `SurfaceArea` a TRIDB declares for a wireframe; None — not there (NULL, or −1: Micromine
    could not measure the solid)."""
    general = (source.get("tridb") or {}).get("general") or {}

    def number(value: Any) -> float | None:
        ok = isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
        return float(value) if ok else None

    return {"volume": number(general.get("Volume")), "area": number(general.get("SurfaceArea"))}


def check_measures(source: Mapping[str, Any], stats: Mapping[str, Any], rel: float = REL_TOL) -> list[str]:
    """Warnings when the volume or the area of the file differs from the measures of the geometry
    (`geo.meshes.mesh_stats`) by more than `rel` (ТЗ 9.12: 1e-9). A measure one side lacks is not compared."""
    out: list[str] = []
    file = declared(source)
    for key, label in (("volume", "Объём"), ("area", "Площадь поверхности")):
        ours, theirs = stats.get(key), file[key]
        if ours is None or theirs is None or abs(ours - theirs) <= rel * max(abs(ours), abs(theirs)):
            continue
        out.append(f"{label} каркаса в файле ({theirs!r}) не совпадает с вычисленным по геометрии ({ours!r})")
    return out
