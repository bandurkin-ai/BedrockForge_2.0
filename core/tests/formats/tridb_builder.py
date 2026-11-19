"""Helpers that build small synthetic Micromine TRIDB files for tests (independent of the writer): the tables
are made with the statements of the real files (`sqlite_master` of `tests/data/real/*.tridb`, copied), the
geometry BLOB is packed with `struct` as ТЗ 9.4 describes it."""

from __future__ import annotations

import sqlite3
import struct
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

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
GUID = "00e1fa58-94da-11ee-87a3-f057a68c96f4"
VERSION = b"\x00\x00\x05\x00"


def block(number: int, data: bytes, length: int | None = None) -> bytes:
    return struct.pack("<qq", len(data) if length is None else length, number) + data


def geometry(
    xyz: Any,
    tri: Any,
    guid: str = GUID,
    *,
    version: bytes = VERSION,
    tag: int = 9,
    magic: bytes = b"\xff\xff\xff\xff",
    extra: Sequence[tuple[int, bytes]] = (),
) -> bytes:
    """The geometry BLOB: head, GUID, blocks 1–3 (X, Y, Z as doubles), block 4 (int32 triples), then `extra`."""
    v = np.asarray(xyz, dtype="<f8").reshape(-1, 3)
    t = np.asarray(tri, dtype="<i4").reshape(-1, 3)
    raw = guid.encode("ascii")
    out = magic + version + struct.pack("<qq", len(raw), tag) + raw
    for k in range(3):
        out += block(k + 1, np.ascontiguousarray(v[:, k]).tobytes())
    out += block(4, t.tobytes())
    for number, data in extra:
        out += block(number, data)
    return out


def general(wf_id: int, name: Any, xyz: Any, **over: Any) -> dict[str, Any]:
    """A `GeneralInformation` row as Micromine writes it (the values of the real files)."""
    v = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
    lo, hi = (v.min(axis=0), v.max(axis=0)) if len(v) else (np.zeros(3), np.zeros(3))
    row: dict[str, Any] = {
        "ID": wf_id, "Name": name, "Code": "", "Title": "", "Colour": 65312, "CreateDateTime": 1701937454,
        "CreateBy": "User", "CreateCompany": "", "EditDateTime": 1701937454, "EditBy": "User", "EditCompany": "",
        "MetaNotes": "", "Accuracy": 0, "XMinimum": float(lo[0]), "XMaximum": float(hi[0]),
        "YMinimum": float(lo[1]), "YMaximum": float(hi[1]), "ZMinimum": float(lo[2]), "ZMaximum": float(hi[2]),
        "DTMPlane": 0, "Volume": None, "SurfaceArea": None, "Validated": 257, "IsLocked": 0, "LockBy": "",
        "LockReason": "", "LockDateTime": 0,
    }  # fmt: skip
    row.update(over)
    return row


def build(
    path: Path,
    wireframes: Sequence[Mapping[str, Any]],
    *,
    encoding: str = "UTF-16le",
    defs: Sequence[Sequence[Any]] = (),
    ddl: Sequence[str] = DDL,
    script: str = "",
) -> Path:
    """A TRIDB with `wireframes`: {id, name, xyz, tri} and optionally `blob` (bytes instead of the packed
    geometry; None — no Geometry row), `general` (column overrides; None — no row), `guid`, `props` (rows
    (Property, TextValue, RealValue); default — PointCount and TriangleCount), `attrs` (rows (AttributeID,
    ValueText, ValueInteger, ValueReal)). `defs` — rows of UserAttributeDefinition; `script` — SQL run last."""
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"PRAGMA encoding = '{encoding}'")
        for statement in ddl:
            conn.execute(statement)
        for d in defs:
            row = [*d, *(None,) * (9 - len(d))]
            conn.execute("INSERT INTO UserAttributeDefinition VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
        for w in wireframes:
            wf_id, guid = int(w["id"]), str(w.get("guid", GUID))
            xyz, tri = w.get("xyz", np.zeros((0, 3))), w.get("tri", np.zeros((0, 3), dtype=np.int32))
            if w.get("general", {}) is not None:
                row = general(wf_id, w.get("name"), xyz, **dict(w.get("general", {})))
                marks = ", ".join("?" * len(row))
                conn.execute(f"INSERT INTO GeneralInformation ({', '.join(row)}) VALUES ({marks})", list(row.values()))
            blob = w.get("blob", geometry(xyz, tri, guid))
            if blob is not None:
                conn.execute("INSERT INTO Geometry (ID, Geometry) VALUES (?, ?)", (wf_id, blob))
            props = w.get("props", [("PointCount", None, float(len(xyz))), ("TriangleCount", None, float(len(tri)))])
            for prop, text, real in props:
                conn.execute("INSERT INTO GeometryProperties VALUES (?, ?, ?, ?, ?)", (wf_id, guid, prop, text, real))
            for attr_id, text, integer, real in w.get("attrs", ()):
                conn.execute(
                    "INSERT INTO UserAttributeValue VALUES (?, ?, ?, ?, ?)", (wf_id, attr_id, text, integer, real)
                )
        if script:
            conn.executescript(script)
        conn.commit()
    finally:
        conn.close()
    return path


def table_rows(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of every table with its rowid, in the order of the rowids; a value goes with its type (1 and
    1.0 are different, so are NULL and an empty text). The geometry BLOBs are among them, byte for byte."""
    conn = sqlite3.connect(path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        return {
            t: [tuple((type(x).__name__, x) for x in row) for row in conn.execute(f'SELECT rowid, * FROM "{t}"')]
            for t in tables
        }
    finally:
        conn.close()


def schema(path: Path) -> dict[str, Any]:
    """The statements of the file, its text encoding and page size, and what SQLite says about its integrity."""
    conn = sqlite3.connect(path)
    try:
        return {
            "master": conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall(),
            "encoding": conn.execute("PRAGMA encoding").fetchone()[0],
            "page_size": conn.execute("PRAGMA page_size").fetchone()[0],
            "integrity": [r[0] for r in conn.execute("PRAGMA integrity_check")],
            "foreign_keys": conn.execute("PRAGMA foreign_key_check").fetchall(),
        }
    finally:
        conn.close()
