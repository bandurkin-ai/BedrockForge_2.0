"""Wireframe (triangulated surface / solid) readers: Micromine TRIDB and DXF.

TRIDB layout is described in docs/FORMATS.md (it is not published by Micromine).
Every reader returns a list of Mesh objects with welded, validated geometry.
"""

from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

WIREFRAME_SUFFIXES = (".tridb", ".dxf")


class WireframeFormatError(ValueError):
    """The file cannot be read as a wireframe; the message is shown to the user."""


@dataclass
class Mesh:
    name: str
    vertices: np.ndarray  # (N, 3) float64
    triangles: np.ndarray  # (M, 3) int32, indices into vertices
    color: str | None = None  # "#rrggbb"
    attributes: dict = field(default_factory=dict)


# ---------------------------------------------------------------- geometry checks


def weld(vertices: np.ndarray, triangles: np.ndarray, tolerance: float = 1e-6) -> tuple[np.ndarray, np.ndarray]:
    """Merge coincident vertices (DXF faces repeat their corners) and drop unused ones."""
    if len(vertices) == 0:
        return vertices.reshape(0, 3), triangles.reshape(0, 3).astype(np.int32)
    key = np.round(vertices / tolerance).astype(np.int64)
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    welded = vertices[first]
    tris = inverse.reshape(-1)[triangles]
    used = np.unique(tris)
    remap = np.full(len(welded), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return welded[used], remap[tris].astype(np.int32)


def mesh_stats(vertices: np.ndarray, triangles: np.ndarray) -> dict:
    """Area, volume (for closed solids), closedness and degenerate triangles."""
    if len(triangles) == 0:
        return {"area": 0.0, "volume": None, "closed": False, "degenerate": 0, "open_edges": 0}
    a, b, c = (vertices[triangles[:, i]] for i in range(3))
    cross = np.cross(b - a, c - a)
    areas = 0.5 * np.linalg.norm(cross, axis=1)
    degenerate = int(np.count_nonzero(areas < 1e-9))

    edges = np.sort(np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    open_edges = int(np.count_nonzero(counts == 1))
    closed = open_edges == 0 and bool(np.all(counts == 2))

    volume = None
    if closed:
        # Divergence theorem, relative to the centroid to keep precision with UTM coordinates.
        o = vertices.mean(axis=0)
        volume = float(abs(np.einsum("ij,ij->i", a - o, np.cross(b - o, c - o)).sum()) / 6.0)
    return {
        "area": round(float(areas.sum()), 2),
        "volume": round(volume, 2) if volume is not None else None,
        "closed": closed,
        "degenerate": degenerate,
        "open_edges": open_edges,
    }


def _validate(mesh: Mesh) -> Mesh:
    v, t = mesh.vertices, mesh.triangles
    if v.ndim != 2 or v.shape[1] != 3 or t.ndim != 2 or t.shape[1] != 3:
        raise WireframeFormatError(f"Каркас «{mesh.name}»: неверная структура геометрии")
    if len(t) and (t.min() < 0 or t.max() >= len(v)):
        raise WireframeFormatError(f"Каркас «{mesh.name}»: номера вершин треугольников вне диапазона")
    if not np.isfinite(v).all():
        raise WireframeFormatError(f"Каркас «{mesh.name}»: координаты содержат пустые значения")
    return mesh


# ---------------------------------------------------------------- Micromine TRIDB

_TRIDB_MAGIC = b"\xff\xff\xff\xff"


def _bgr_to_hex(value) -> str | None:
    """Micromine stores colours as Windows COLORREF (0x00BBGGRR)."""
    try:
        c = int(value)
    except (TypeError, ValueError):
        return None
    return f"#{c & 0xFF:02x}{(c >> 8) & 0xFF:02x}{(c >> 16) & 0xFF:02x}"


def _blocks(blob: bytes, start: int, header_in_length: bool) -> dict[int, bytes] | None:
    out: dict[int, bytes] = {}
    pos = start
    while pos + 16 <= len(blob):
        length, number = struct.unpack_from("<qq", blob, pos)
        size = length - 16 if header_in_length else length
        if size < 0 or pos + 16 + size > len(blob):
            return None
        out[number] = blob[pos + 16 : pos + 16 + size]
        pos += 16 + size
    return out if pos == len(blob) else None


def parse_tridb_geometry(blob: bytes, point_count: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Decode one Geometry BLOB: X, Y, Z arrays (blocks 1–3) and triangle indices (block 4)."""
    if len(blob) < 24 or not blob.startswith(_TRIDB_MAGIC):
        raise WireframeFormatError("Неизвестная структура геометрии TRIDB")
    guid_length = struct.unpack_from("<q", blob, 8)[0]
    start = 24 + guid_length
    if not 0 <= guid_length <= 256 or start > len(blob):
        raise WireframeFormatError("Неизвестная структура геометрии TRIDB")
    for header_in_length in (False, True):
        blocks = _blocks(blob, start, header_in_length)
        if not blocks or not all(k in blocks for k in (1, 2, 3, 4)):
            continue
        xs, ys, zs = (np.frombuffer(blocks[k], dtype="<f8") for k in (1, 2, 3))
        if not (len(xs) == len(ys) == len(zs)) or len(blocks[4]) % 12:
            continue
        if point_count is not None and len(xs) != point_count:
            continue
        triangles = np.frombuffer(blocks[4], dtype="<i4").reshape(-1, 3).astype(np.int32)
        return np.column_stack([xs, ys, zs]), triangles
    raise WireframeFormatError("Не удалось разобрать геометрию TRIDB (блоки вершин и треугольников)")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _attributes(conn: sqlite3.Connection, tables: set[str]) -> dict[object, dict]:
    """User attributes per wireframe id; best effort, the layout varies between versions."""
    if not {"UserAttributeDefinition", "UserAttributeValue"} <= tables:
        return {}
    try:
        definition = _columns(conn, "UserAttributeDefinition")
        value = _columns(conn, "UserAttributeValue")
        name_col = "Name" if "Name" in definition else None
        if not name_col or not {"ID"} <= definition or not {"AttributeID", "Value"} <= value:
            return {}
        owner = "GeneralInformationID" if "GeneralInformationID" in value else "ID" if "ID" in value else None
        if owner is None:
            return {}
        names = dict(conn.execute(f'SELECT ID, "{name_col}" FROM UserAttributeDefinition'))
        out: dict[object, dict] = {}
        for wf, attr, val in conn.execute(f'SELECT "{owner}", AttributeID, Value FROM UserAttributeValue'):
            out.setdefault(wf, {})[str(names.get(attr, attr))] = val if isinstance(val, (int, float, str)) else str(val)
        return out
    except sqlite3.Error:
        return {}


def read_tridb(path: Path) -> list[Mesh]:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise WireframeFormatError(f"Файл не открывается как TRIDB: {exc}") from exc
    try:
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as exc:
            raise WireframeFormatError("Файл не является базой TRIDB (SQLite)") from exc
        if not {"GeneralInformation", "Geometry"} <= tables:
            raise WireframeFormatError("В файле нет таблиц GeneralInformation и Geometry — это не TRIDB")
        info_cols = _columns(conn, "GeneralInformation")
        name_col = "Name" if "Name" in info_cols else None
        colour_col = "Colour" if "Colour" in info_cols else None
        info = {
            row[0]: row[1:]
            for row in conn.execute(
                f'SELECT ID, {f"{name_col}" if name_col else "NULL"}, {f"{colour_col}" if colour_col else "NULL"} FROM GeneralInformation'
            )
        }
        counts: dict[object, int] = {}
        if "GeometryProperties" in tables and {"ID", "PointCount"} <= _columns(conn, "GeometryProperties"):
            counts = dict(conn.execute("SELECT ID, PointCount FROM GeometryProperties"))
        attributes = _attributes(conn, tables)

        meshes = []
        for wf_id, blob in conn.execute("SELECT ID, Geometry FROM Geometry ORDER BY ID"):
            if blob is None:
                continue
            name, colour = info.get(wf_id, (None, None))
            try:
                vertices, triangles = parse_tridb_geometry(bytes(blob), counts.get(wf_id))
            except WireframeFormatError:
                # A count that disagrees with the geometry is not worth losing the wireframe over.
                vertices, triangles = parse_tridb_geometry(bytes(blob))
            meshes.append(_validate(Mesh(
                name=str(name or f"Каркас {wf_id}"),
                vertices=vertices,
                triangles=triangles,
                color=_bgr_to_hex(colour),
                attributes=attributes.get(wf_id, {}),
            )))
    finally:
        conn.close()
    if not meshes:
        raise WireframeFormatError("В файле TRIDB нет каркасов")
    return meshes


# ---------------------------------------------------------------- DXF


def _dxf_color(entity, doc) -> str | None:
    from ezdxf import colors

    if entity.dxf.hasattr("true_color"):
        r, g, b = colors.int2rgb(entity.dxf.true_color)
        return f"#{r:02x}{g:02x}{b:02x}"
    aci = entity.dxf.get("color", 256)
    if aci in (0, 256):  # BYBLOCK / BYLAYER
        layer = doc.layers.get(entity.dxf.layer) if entity.dxf.layer in doc.layers else None
        aci = abs(layer.dxf.color) if layer is not None else 7
    if not 1 <= aci <= 255 or aci == 7:
        return None
    r, g, b = colors.aci2rgb(aci)
    return f"#{r:02x}{g:02x}{b:02x}"


def _triangulate(face: list[int]) -> list[tuple[int, int, int]]:
    """Fan triangulation; DXF faces have 3 or 4 corners, repeated corners make triangles."""
    unique: list[int] = []
    for i in face:
        if not unique or unique[-1] != i:
            unique.append(i)
    if len(unique) > 1 and unique[0] == unique[-1]:
        unique.pop()
    return [(unique[0], unique[k], unique[k + 1]) for k in range(1, len(unique) - 1)]


def read_dxf(path: Path) -> list[Mesh]:
    """POLYFACE / POLYMESH, 3DFACE and MESH entities, one wireframe per layer."""
    import ezdxf
    from ezdxf import recover
    from ezdxf.render import MeshBuilder

    try:
        doc, _ = recover.readfile(str(path))
    except (OSError, ezdxf.DXFStructureError) as exc:
        raise WireframeFormatError(f"Файл не читается как DXF: {exc}") from exc

    layers: dict[str, dict] = {}

    def target(entity) -> dict:
        name = entity.dxf.get("layer", "0")
        return layers.setdefault(name, {"vertices": [], "triangles": [], "count": 0, "color": _dxf_color(entity, doc)})

    def add(entity, vertices, faces) -> None:
        t = target(entity)
        base = t["count"]
        t["vertices"].extend((v[0], v[1], v[2]) for v in vertices)
        for face in faces:
            t["triangles"].extend(tuple(base + i for i in tri) for tri in _triangulate(list(face)))
        t["count"] += len(vertices)

    for entity in doc.modelspace():
        kind = entity.dxftype()
        try:
            if kind == "POLYLINE" and (entity.is_poly_face_mesh or entity.is_polygon_mesh):
                mesh = MeshBuilder.from_polyface(entity)
                add(entity, mesh.vertices, mesh.faces)
            elif kind == "MESH":
                mesh = MeshBuilder.from_mesh(entity)
                add(entity, mesh.vertices, mesh.faces)
            elif kind == "3DFACE":
                corners = [entity.dxf.get(f"vtx{i}") for i in range(4)]
                corners = [c for c in corners if c is not None]
                add(entity, corners, [list(range(len(corners)))])
        except (AttributeError, ValueError, IndexError):
            continue  # a broken entity should not stop the rest of the file

    meshes = []
    for name, t in layers.items():
        if not t["triangles"]:
            continue
        vertices, triangles = weld(np.array(t["vertices"], dtype=np.float64), np.array(t["triangles"], dtype=np.int64))
        meshes.append(_validate(Mesh(name=name, vertices=vertices, triangles=triangles, color=t["color"])))
    if not meshes:
        raise WireframeFormatError("В DXF нет каркасов: ожидаются POLYFACE (POLYLINE), 3DFACE или MESH")
    return meshes


def read_wireframes(path: Path, suffix: str) -> tuple[str, list[Mesh]]:
    """Returns (format name, meshes)."""
    suffix = suffix.lower()
    if suffix == ".tridb":
        return "Micromine TRIDB", read_tridb(path)
    if suffix == ".dxf":
        return "DXF", read_dxf(path)
    raise WireframeFormatError(f"Формат {suffix or 'без расширения'} не поддерживается для каркасов: нужны .tridb или .dxf")
