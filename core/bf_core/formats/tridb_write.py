"""Micromine TRIDB writer (ТЗ 9.4, 9.12; ADR 0013 §2): `write_tridb(path, meshes)`.

The file is an SQLite database made with the statements of the real files (`sqlite_master` of `tests/data/real`),
text in UTF-16LE, pages of 4096 bytes. A wireframe is a `GeneralInformation` row, its geometry BLOB of version 5
(`tridb_blob.py`: `zeroblob`, then written in portions — the triangles in the order given, which is the order of
the file for an imported wireframe), `GeometryProperties` rows and its attributes of the user.

A wireframe read from a TRIDB carries the rows of its file in `source["tridb"]` (`tridb.py`). They are written
back as they were — with their numbers, GUID and rowids — while they still describe the wireframe:

* the id, when no wireframe written before has it (else the next free one);
* the name of the file, while the wireframe is called as the import called it (`meshset.name_in_file`); a name
  taken in the file gets « (2)» with a warning;
* `Colour`, while it is the colour of the wireframe;
* the GUID, `Volume`, `SurfaceArea`, `Validated` and the `GeometryProperties` rows, while the counts and the
  extent of the source are those of the geometry (the geometry of an object never changes in place; a source
  that does not fit is not trusted);
* a value of an attribute, while it reads as the value the wireframe has now; the definitions of the source
  keep their ids unless another wireframe of the file brought a different definition under the same id.

So a file read and written again has the same rows in every table and every geometry BLOB byte for byte (the
file itself differs: the pages of SQLite are laid out anew). Everything else is made anew: the extent, `Volume`
(None for a surface or a solid that is not closed — never 0 or −1) and `SurfaceArea` from `MeshSet.stats` or
`geo.meshes.mesh_stats`; `PointCount`, `TriangleCount`, `Volume`, `SurfaceArea` in `GeometryProperties`; a GUID
made from the bytes of the geometry (the same wireframe gives the same file again); dates `now`; `Validated` 0.
Numbers and yes/no of new or changed attributes are written as text into attributes of type 0 with a warning:
the codes of the numeric types were not seen in a real file (ADR 0013).

The file is written next to its place as `<name>.part` and renamed at the end; a failure or a cancel leaves
neither. Error codes: no_meshes, too_large.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.formats.errors import FormatError
from bf_core.formats.meshset import MeshSet, clean_attrs, n_text, name_in_file, name_key
from bf_core.formats.str_layers import scaled
from bf_core.formats.table import Progress, no_progress
from bf_core.formats.tridb import DEFINITION, EXTENT, GENERAL, bgr_to_hex, value_of
from bf_core.formats.tridb_blob import HEAD, blob_head, blob_size, block_head

DDL = (
    "CREATE TABLE DatabaseInformation (Version INTEGER, CreateDateTime INTEGER, OriginalProject TEXT)",
    "CREATE TABLE GeneralInformation (ID INTEGER, Name TEXT, Code TEXT, Title TEXT, Colour INTEGER, "
    "CreateDateTime INTEGER, CreateBy TEXT, CreateCompany TEXT, EditDateTime INTEGER, EditBy TEXT, EditCompany TEXT, "
    "MetaNotes TEXT, Accuracy INTEGER, XMinimum REAL, XMaximum REAL, YMinimum REAL, YMaximum REAL, ZMinimum REAL, "
    "ZMaximum REAL, DTMPlane INTEGER, Volume REAL, SurfaceArea REAL, Validated INTEGER, IsLocked INTEGER, "
    "LockBy TEXT, LockReason TEXT, LockDateTime INTEGER, PRIMARY KEY (ID), UNIQUE (NAME))",
    "CREATE TABLE Geometry (ID INTEGER, Geometry BLOB, PRIMARY KEY (ID))",
    "CREATE TABLE UserAttributeDefinition (ID INTEGER, Name TEXT, Type INTEGER, Precision INTEGER, "
    "DefaultValueText TEXT, DefaultValueInteger INTEGER, DefaultValueReal REAL, Properties, ProtoProperties, "
    "PRIMARY KEY (ID))",
    "CREATE TABLE UserAttributeValue (TriangulationID INTEGER, AttributeID INTEGER, ValueText TEXT, "
    "ValueInteger INTEGER, ValueReal REAL, PRIMARY KEY (TriangulationID, AttributeID), "
    "FOREIGN KEY (AttributeID) REFERENCES UserAttributeDefinition (ID) ON DELETE CASCADE)",
    "CREATE TABLE PointAttributeDefinition (ID INTEGER, Name TEXT, Type INTEGER, Precision INTEGER, "
    "DefaultValueText TEXT, DefaultValueInteger INTEGER, DefaultValueReal REAL, PRIMARY KEY (ID))",
    "CREATE TABLE PointAttributeValue (TriangulationID INTEGER, AttributeID INTEGER, AttributeValues BLOB, "
    "PRIMARY KEY (TriangulationID, AttributeID), "
    "FOREIGN KEY (AttributeID) REFERENCES PointAttributeDefinition (ID) ON DELETE CASCADE)",
    "CREATE TABLE TriangleAttributeDefinition (ID INTEGER, Name TEXT, Type INTEGER, Precision INTEGER, "
    "DefaultValueText TEXT, DefaultValueInteger INTEGER, DefaultValueReal REAL, PRIMARY KEY (ID))",
    "CREATE TABLE TriangleAttributeValue (TriangulationID INTEGER, AttributeID INTEGER, AttributeValues BLOB, "
    "PRIMARY KEY (TriangulationID, AttributeID), "
    "FOREIGN KEY (AttributeID) REFERENCES TriangleAttributeDefinition (ID) ON DELETE CASCADE)",
    "CREATE TABLE DisplayProperties (TriangulationID INTEGER, SliceHatch TEXT, SilhouetteHatch TEXT, "
    "LabelPosition INTEGER, LabelOffset REAL,PRIMARY KEY (TriangulationID), "
    "FOREIGN KEY (TriangulationID) REFERENCES GeneralInformation (ID) ON DELETE CASCADE)",
    "CREATE TABLE GeometryProperties (TriangulationID INTEGER, GUID TEXT, Property TEXT, TextValue TEXT, "
    "RealValue REAL)",
    "CREATE INDEX GeometryProperties_TriangulationId ON GeometryProperties (TriangulationID)",
)
ROWS = 1 << 20  # vertices or triangles written at once (8 and 12 MB)
DEFAULT_COLOUR = 0x808080  # a wireframe without a colour: grey
MAX_BLOB: int | None = None  # bytes of a geometry BLOB; None — the limit of SQLite (1 GB)
WAITING = 200_000  # rows kept until the end of the file; more are written at once (memory stays bounded)
#: A new `GeneralInformation` row: the values Micromine writes into the real files; `Validated` 0 — not checked.
NEW_ROW: dict[str, Any] = {
    "Code": "", "Title": "", "CreateCompany": "", "EditCompany": "", "MetaNotes": "", "Accuracy": 0, "DTMPlane": 0,
    "Validated": 0, "IsLocked": 0, "LockBy": "", "LockReason": "", "LockDateTime": 0,
}  # fmt: skip
NEW_DEFINITION = (0, 2, "", 0, 0.0, None, None)  # Type (text), Precision, the defaults: as the real files


@dataclass
class TridbWritten:
    path: Path
    meshes: int
    points: int
    triangles: int
    size: int
    warnings: list[str] = field(default_factory=list)


def hex_to_bgr(color: str) -> int:
    """ "#rrggbb" as a Windows COLORREF (0x00BBGGRR)."""
    return int(color[5:7] + color[3:5] + color[1:3], 16)


def _sql(value: Any) -> Any:
    """A value of `meta.source` (JSON) as SQLite takes it."""
    if isinstance(value, Mapping):
        blob = value.get("blob")
        return bytes.fromhex(blob) if isinstance(blob, str) else None
    return value if value is None or isinstance(value, int | float | str) else None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _rows(value: Any, sizes: tuple[int, ...]) -> list[list[Any]]:
    """Source rows (lists of one of the lengths `sizes`); anything else is not a row."""
    return [r for r in value if isinstance(r, list) and len(r) in sizes] if isinstance(value, list) else []


class _File:
    """The file being written: what is taken already (ids, names, definitions) and its warnings."""

    def __init__(self, conn: sqlite3.Connection, now: int, author: str) -> None:
        self.conn, self.now, self.author = conn, now, author
        self.ids: set[int] = set()
        self.names: set[str] = set()
        self.defs: dict[int, list[Any]] = {}
        self.database = False
        self.waiting: list[tuple[str, list[Any]]] = []  # rows without a rowid of their own
        self.renamed = self.as_text = self.meshes = self.points = self.triangles = 0
        self.limit = conn.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) if MAX_BLOB is None else MAX_BLOB

    # --- a wireframe -------------------------------------------------------------------------------------

    def add(self, mesh: MeshSet, tick: Progress) -> None:
        raw = mesh.source.get("tridb") if mesh.source.get("format") == "tridb" else None
        src: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        general: Mapping[str, Any] = src["general"] if isinstance(src.get("general"), Mapping) else {}
        n, m = mesh.point_count, mesh.triangle_count
        extent = mesh.extent()
        # the rows of the source describe this geometry: the same counts and extent, bit for bit
        same = src.get("points") == n and src.get("triangles") == m and [general.get(c) for c in EXTENT] == extent
        size = blob_size(n, m)
        if size > self.limit:
            raise FormatError(
                "too_large",
                f"Каркас «{mesh.name}» слишком велик для TRIDB: {n_text(size)} байт геометрии при пределе "
                f"{n_text(self.limit)}.",
                "Разделите каркас на несколько или выберите формат DXF.",
            )
        wanted = src.get("id")
        tid = max(self.ids | {0}) + 1
        if isinstance(wanted, int) and not isinstance(wanted, bool) and wanted not in self.ids:
            tid = wanted
        self.ids.add(tid)
        row = {**NEW_ROW, "CreateDateTime": self.now, "EditDateTime": self.now}
        row.update(CreateBy=self.author, EditBy=self.author, Volume=None, SurfaceArea=None)
        row.update({c: _sql(v) for c, v in general.items() if c in GENERAL})
        row.update(ID=tid, Name=self._name(mesh), Colour=self._colour(mesh, general.get("Colour")))
        row.update(zip(EXTENT, extent, strict=True))
        if not same:
            stats = mesh.stats if mesh.stats and "area" in mesh.stats and "volume" in mesh.stats else None
            if stats is None:
                from bf_core.geo.meshes import mesh_stats

                stats = mesh_stats(mesh.xyz, mesh.tri)
            row.update(Volume=stats["volume"], SurfaceArea=stats["area"], Validated=0)
        marks = ", ".join("?" * len(GENERAL))
        self.conn.execute(f"INSERT INTO GeneralInformation VALUES ({marks})", [row[c] for c in GENERAL])
        guid = src.get("guid") if same else None
        if not (isinstance(guid, str) and guid.isascii() and len(guid) == 36):
            guid = None  # made from the geometry while it is written
        guid = self._geometry(tid, mesh, guid, size, tick)
        self._properties(tid, guid, n, m, row, src.get("properties") if same else None)
        self._attributes(tid, mesh, src)
        display = src.get("display")
        if isinstance(display, list) and len(display) == 4:
            self.conn.execute("INSERT INTO DisplayProperties VALUES (?, ?, ?, ?, ?)", [tid, *map(_sql, display)])
        if not self.database and _rows(src.get("database"), (3,)):
            self.database = True
            rows = [[_sql(v) for v in r] for r in _rows(src.get("database"), (3,))]
            self.conn.executemany("INSERT INTO DatabaseInformation VALUES (?, ?, ?)", rows)
        self.meshes, self.points, self.triangles = self.meshes + 1, self.points + n, self.triangles + m

    def _name(self, mesh: MeshSet) -> str | None:
        name = name_in_file(mesh)
        if name is None:
            return None  # the file had no name: UNIQUE lets several rows be without one
        base, k = name, 1
        while name in self.names:
            k += 1
            name = f"{base} ({k})"
        self.renamed += k > 1
        self.names.add(name)
        return name

    @staticmethod
    def _colour(mesh: MeshSet, source: Any) -> int:
        if mesh.color is None:
            return source if _is_int(source) else DEFAULT_COLOUR
        return source if bgr_to_hex(source) == mesh.color else hex_to_bgr(mesh.color)

    def _geometry(self, tid: int, mesh: MeshSet, guid: str | None, size: int, tick: Progress) -> str:
        """The BLOB of a wireframe, written in portions. Returns its GUID: the one given, or one made from the
        bytes of the geometry (written into the head at the end)."""
        n, m = mesh.point_count, mesh.triangle_count
        self.conn.execute("INSERT INTO Geometry (ID, Geometry) VALUES (?, zeroblob(?))", (tid, size))
        digest = hashlib.blake2b(digest_size=16)
        done = 0

        def put(blob: Any, array: np.ndarray, dtype: str) -> None:
            nonlocal done
            data = memoryview(np.ascontiguousarray(array, dtype=dtype)).cast("B")
            blob.write(data)
            if guid is None:
                digest.update(data)
            done += len(data)
            tick(done / size, f"Запись каркаса «{mesh.name}»")

        with self.conn.blobopen("Geometry", "Geometry", tid) as blob:
            blob.write(blob_head(guid or "0" * 36))
            for k in range(3):
                blob.write(block_head(k + 1, 8 * n))
                for a in range(0, n, ROWS):
                    put(blob, mesh.xyz[a : a + ROWS, k], "<f8")
            blob.write(block_head(4, 12 * m))
            for a in range(0, m, ROWS):
                put(blob, mesh.tri[a : a + ROWS], "<i4")
            if guid is None:
                guid = str(uuid.UUID(bytes=digest.digest(), version=4))
                blob.seek(HEAD)
                blob.write(guid.encode("ascii"))
        return guid

    def _insert(self, table: str, rowid: Any, values: Sequence[Any]) -> None:
        """A row of the source goes under its rowid. A row without one, or whose rowid is taken (wireframes of
        several files), waits: it is written after the rows of every source, so it takes no rowid a later
        wireframe brings."""
        if _is_int(rowid):
            marks = ", ".join("?" * (len(values) + 1))
            try:
                self.conn.execute(f"INSERT INTO {table} (rowid, {_COLUMNS[table]}) VALUES ({marks})", [rowid, *values])
                return
            except sqlite3.IntegrityError:
                pass
        self.waiting.append((table, list(values)))
        if len(self.waiting) >= WAITING:
            self.flush()

    def flush(self) -> None:
        for table, values in self.waiting:
            self.conn.execute(f"INSERT INTO {table} VALUES ({', '.join('?' * len(values))})", values)
        self.waiting.clear()

    def _properties(self, tid: int, guid: str, n: int, m: int, row: Mapping[str, Any], source: Any) -> None:
        if isinstance(source, list):  # the rows of the file, as they were
            for r in _rows(source, (4, 5)):
                self._insert("GeometryProperties", r[0], [tid, _sql(r[4]) if len(r) == 5 else guid, *map(_sql, r[1:4])])
            return
        rows: list[tuple[str, Any]] = [("PointCount", float(n)), ("TriangleCount", float(m))]
        rows += [(key, row[key]) for key in ("Volume", "SurfaceArea") if isinstance(row[key], int | float)]
        for prop, value in rows:
            self._insert("GeometryProperties", None, [tid, guid, prop, None, float(value)])

    # --- attributes --------------------------------------------------------------------------------------

    def _definition(self, source: Sequence[Any]) -> int:
        """The id in the file of a definition of a source: its own, the id of an equal definition written
        before, or a new one when its id is taken by another definition."""
        row = [_sql(v) for v in source]
        found = row[0] if _is_int(row[0]) else None
        if found is not None and found in self.defs and self.defs[found][1:] != row[1:]:
            found = next((k for k, have in self.defs.items() if have[1:] == row[1:]), None)
        if found is None:
            found = max(self.defs.keys() | {0}) + 1
        if found not in self.defs:
            self.defs[found] = [found, *row[1:]]
            self.conn.execute(
                "INSERT INTO UserAttributeDefinition VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", self.defs[found]
            )
        return found

    def _new_definition(self, name: str) -> int:
        found = next((k for k, have in self.defs.items() if have[1] == name and have[2] == 0), None)
        return found if found is not None else self._definition([None, name, *NEW_DEFINITION])

    def _encode(self, value: Any) -> tuple[Any, Any, Any]:
        """(ValueText, ValueInteger, ValueReal) of a value: an empty one is an empty text, as in the real files;
        a number and yes/no go as text (the type of the attribute is text)."""
        if value is None or isinstance(value, str):
            return value or "", None, None
        self.as_text += 1
        if isinstance(value, bool):
            return "Да" if value else "Нет", None, None
        return (str(value) if isinstance(value, int) else repr(value)), None, None

    def _attributes(self, tid: int, mesh: MeshSet, src: Mapping[str, Any]) -> None:
        defs = [d for d in _rows(src.get("attr_defs"), (len(DEFINITION),)) if _is_int(d[0])]
        ids = {d[0]: self._definition(d) for d in defs}  # every definition of the source goes to the file
        by_attr = {r[1]: r for r in _rows(src.get("attr_values"), (5,)) if _is_int(r[1])}
        valued = [d for d in defs if d[0] in by_attr]
        # the names and values as the reader showed them: the same function over the same rows
        shown, _notes = clean_attrs((d[1], value_of(d[2], *by_attr[d[0]][2:])) for d in valued)
        known = {name_key(a["name"]): (d, a["value"]) for d, a in zip(valued, shown, strict=False)}
        unvalued = {name_key(" ".join(d[1].split())): d for d in defs if d[0] not in by_attr and isinstance(d[1], str)}
        out: list[tuple[Any, list[Any]]] = []
        for attr in mesh.attrs:
            key, value = name_key(attr["name"]), attr["value"]
            if key in known:
                d, old = known[key]
                r = by_attr[d[0]]
                unchanged = type(old) is type(value) and old == value
                out.append((r[0], [tid, ids[d[0]], *(map(_sql, r[2:]) if unchanged else self._encode(value))]))
            else:  # a definition of the source that had no value here, or a new attribute
                spare = unvalued.get(key)
                attr_id = ids[spare[0]] if spare is not None else self._new_definition(attr["name"])
                out.append((None, [tid, attr_id, *self._encode(value)]))
        for d in valued[len(shown) :]:  # over the limit of the model: never shown, written as they were
            r = by_attr[d[0]]
            out.append((r[0], [tid, ids[d[0]], *map(_sql, r[2:])]))
        taken: set[int] = set()
        for rowid, values in sorted(out, key=lambda item: (not _is_int(item[0]), item[0] if _is_int(item[0]) else 0)):
            if values[1] not in taken:  # two names of one definition cannot both have a value
                taken.add(values[1])
                self._insert("UserAttributeValue", rowid, values)

    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.renamed:
            out.append(f"Каркасы с одинаковыми названиями: {n_text(self.renamed)} — к названию добавлен номер")
        if self.as_text:
            out.append(
                f"Числа и значения «да/нет» атрибутов записаны текстом: {n_text(self.as_text)} "
                "(числовые типы атрибутов TRIDB не проверены на образцах)"
            )
        return out


_COLUMNS = {
    "GeometryProperties": "TriangulationID, GUID, Property, TextValue, RealValue",
    "UserAttributeValue": "TriangulationID, AttributeID, ValueText, ValueInteger, ValueReal",
}


def write_tridb(
    path: str | Path,
    meshes: Iterable[MeshSet],
    progress: Progress | None = None,
    *,
    total: int | None = None,
    now: int | None = None,
    author: str = "",
) -> TridbWritten:
    """Write wireframes as a Micromine TRIDB (see the module docstring). `meshes` may be a generator: one
    wireframe is in memory at a time; `total` — their number, for the progress (a sequence tells it itself).
    `now` (Unix seconds) and `author` go to the rows of wireframes that bring none from a TRIDB.
    `progress(fraction, message)` may raise to cancel."""
    path = Path(path)
    tick = progress or no_progress
    tick(0.0, "Запись TRIDB")
    count = total if total is not None else len(meshes) if hasattr(meshes, "__len__") else None  # type: ignore[arg-type]
    part = path.with_name(path.name + ".part")
    part.open("wb").close()  # an empty file: a folder that is missing or closed for writing is an OSError
    conn = sqlite3.connect(part, isolation_level=None)
    try:
        try:
            for pragma in ("page_size = 4096", "encoding = 'UTF-16le'", "journal_mode = MEMORY", "synchronous = OFF"):
                conn.execute(f"PRAGMA {pragma}")
            for statement in DDL:
                conn.execute(statement)
            conn.execute("BEGIN")
            out = _File(conn, int(time.time()) if now is None else int(now), author)
            for k, mesh in enumerate(meshes):
                a, b = (min(1.0, k / count), min(1.0, (k + 1) / count)) if count else (0.0, 0.0)
                out.add(mesh, scaled(tick, a, b))
            if not out.meshes:
                raise FormatError("no_meshes", "Нет каркасов для записи в TRIDB.", "Выберите каркасы для экспорта.")
            out.flush()
            conn.execute("COMMIT")
        except sqlite3.OperationalError as exc:  # the disk is full, the file cannot be written
            raise OSError(f"TRIDB: {exc}") from exc
        finally:
            conn.close()
        with part.open("rb+") as f:
            os.fsync(f.fileno())
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    tick(1.0, f"Записано каркасов: {n_text(out.meshes)}")
    return TridbWritten(path, out.meshes, out.points, out.triangles, path.stat().st_size, out.warnings())
