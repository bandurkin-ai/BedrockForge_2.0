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

WIREFRAME_SUFFIXES = (".tridb", ".dxf", ".obj", ".stl")


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
    """Merge coincident vertices (DXF faces repeat their corners) and drop unused ones.

    Vertices on the same `tolerance` grid point merge; the first of them is kept (exact float64).
    One lexicographic sort of the integer grid keys: about 1.3 s for 9 million corners.
    """
    if len(vertices) == 0:
        return vertices.reshape(0, 3), triangles.reshape(0, 3).astype(np.int32)
    key = np.round(vertices / tolerance).astype(np.int64)
    order = np.lexsort((key[:, 2], key[:, 1], key[:, 0]))
    ks = key[order]
    starts = np.empty(len(ks), dtype=bool)
    starts[0] = True
    np.any(ks[1:] != ks[:-1], axis=1, out=starts[1:])
    group = np.cumsum(starts) - 1
    inverse = np.empty(len(ks), dtype=np.int64)
    inverse[order] = group
    # The first vertex (in file order) of every group represents it.
    first = np.full(int(group[-1]) + 1, len(ks), dtype=np.int64)
    np.minimum.at(first, group, order)
    welded = vertices[first]
    tris = inverse[triangles]
    used = np.unique(tris)
    remap = np.full(len(welded), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return welded[used], remap[tris].astype(np.int32)


# Triangles per slice in mesh_stats: bounds the temporary memory (~200 MB per slice).
_STATS_SLICE = 1 << 20


def edge_summary(triangles: np.ndarray, vertex_count: int) -> tuple[int, int]:
    """(open edges, non-manifold edges): edges used by one triangle, and by more than two.

    One int64 key per edge (both orientations alike), sorted; ~11 bytes of memory per edge.
    """
    t = np.asarray(triangles)
    m = len(t)
    n = max(int(vertex_count), 1)
    keys = np.empty(3 * m, dtype=np.int64)
    for k, (i, j) in enumerate(((0, 1), (1, 2), (2, 0))):
        a = t[:, i].astype(np.int64)
        b = t[:, j].astype(np.int64)
        seg = keys[k * m:(k + 1) * m]
        np.minimum(a, b, out=seg)
        seg *= n
        seg += np.maximum(a, b)
    del a, b
    keys.sort()
    same_next = np.zeros(len(keys), dtype=bool)
    np.equal(keys[1:], keys[:-1], out=same_next[:-1])
    same_prev = np.zeros(len(keys), dtype=bool)
    same_prev[1:] = same_next[:-1]
    open_edges = int(np.count_nonzero(~same_prev & ~same_next))
    # A run of three or more equal keys: its first element has equal keys 1 and 2 places ahead.
    same_next2 = np.zeros(len(keys), dtype=bool)
    if len(keys) > 2:
        np.equal(keys[2:], keys[:-2], out=same_next2[:-2])
    nonmanifold = int(np.count_nonzero(~same_prev & same_next2))
    return open_edges, nonmanifold


def mesh_stats(vertices: np.ndarray, triangles: np.ndarray) -> dict:
    """Area, volume (for closed solids), closedness, orientation and degenerate triangles.

    Volume: signed tetrahedra from the centre of the extent (keeps precision with UTM
    coordinates), summed per slice with numpy's pairwise sum and across slices with fsum.
    """
    if len(triangles) == 0:
        return {"area": 0.0, "volume": None, "closed": False, "degenerate": 0, "open_edges": 0}
    import math

    v = np.asarray(vertices, dtype=np.float64)
    o = (v.min(axis=0) + v.max(axis=0)) / 2.0
    areas, volumes, degenerate = [], [], 0
    for start in range(0, len(triangles), _STATS_SLICE):
        tri = np.asarray(triangles[start:start + _STATS_SLICE])
        a, b, c = (v[tri[:, i]] - o for i in range(3))
        cross = np.cross(b - a, c - a)
        area = 0.5 * np.sqrt(np.einsum("ij,ij->i", cross, cross))
        degenerate += int(np.count_nonzero(area < 1e-9))
        areas.append(float(area.sum()))
        volumes.append(float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()))

    open_edges, nonmanifold = edge_summary(triangles, len(v))
    closed = open_edges == 0 and nonmanifold == 0
    volume = orientation = None
    if closed:
        signed = math.fsum(volumes) / 6.0
        volume = abs(signed)
        orientation = "outward" if signed > 0 else "inward"
    return {
        "area": round(math.fsum(areas), 2),
        "volume": round(volume, 2) if volume is not None else None,
        "closed": closed,
        "orientation": orientation,
        "degenerate": degenerate,
        "open_edges": open_edges,
        "nonmanifold_edges": nonmanifold,
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


_READ = 32 << 20  # bytes per read when streaming a geometry BLOB


def _block_table(f, total: int, start: int, header_in_length: bool) -> dict[int, tuple[int, int]] | None:
    """Block number -> (offset, size) by walking the block headers; None if they do not fit."""
    out: dict[int, tuple[int, int]] = {}
    pos = start
    while pos + 16 <= total:
        f.seek(pos)
        length, number = struct.unpack("<qq", f.read(16))
        size = length - 16 if header_in_length else length
        if size < 0 or pos + 16 + size > total:
            return None
        out[number] = (pos + 16, size)
        pos += 16 + size
    return out if pos == total else None


def _read_into(f, offset: int, size: int, out: np.ndarray) -> None:
    """Reads `size` bytes at `offset` into the 1-D array `out` (any dtype, possibly strided)."""
    item = out.dtype.itemsize
    f.seek(offset)
    done = 0
    step = _READ - _READ % item
    while done < size:
        chunk = f.read(min(step, size - done))
        if not chunk:
            raise WireframeFormatError("Геометрия TRIDB обрывается раньше времени")
        n = len(chunk) // item
        out[done // item:done // item + n] = np.frombuffer(chunk, dtype=out.dtype.newbyteorder("<"))
        done += len(chunk)


def read_tridb_geometry(f, total: int, point_count: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Decodes one Geometry BLOB from a file-like object (sqlite3.Blob, BytesIO) without holding
    it in memory: X, Y, Z (blocks 1–3) go straight into one (n, 3) float64 array, indices
    (block 4) into an (m, 3) int32 array. Two block-length conventions are accepted."""
    f.seek(0)
    head = f.read(24)
    if len(head) < 24 or not head.startswith(_TRIDB_MAGIC):
        raise WireframeFormatError("Неизвестная структура геометрии TRIDB")
    guid_length = struct.unpack_from("<q", head, 8)[0]
    start = 24 + guid_length
    if not 0 <= guid_length <= 256 or start > total:
        raise WireframeFormatError("Неизвестная структура геометрии TRIDB")
    for header_in_length in (False, True):
        table = _block_table(f, total, start, header_in_length)
        if not table or not all(k in table for k in (1, 2, 3, 4)):
            continue
        sizes = [table[k][1] for k in (1, 2, 3)]
        if len(set(sizes)) != 1 or sizes[0] % 8 or table[4][1] % 12:
            continue
        n = sizes[0] // 8
        if point_count is not None and n != point_count:
            continue
        vertices = np.empty((n, 3), dtype=np.float64)
        for k in range(3):
            _read_into(f, table[k + 1][0], table[k + 1][1], vertices[:, k])
        triangles = np.empty(table[4][1] // 4, dtype=np.int32)
        _read_into(f, table[4][0], table[4][1], triangles)
        return vertices, triangles.reshape(-1, 3)
    raise WireframeFormatError("Не удалось разобрать геометрию TRIDB (блоки вершин и треугольников)")


def parse_tridb_geometry(blob: bytes, point_count: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Decode one Geometry BLOB held in memory (see read_tridb_geometry)."""
    import io

    return read_tridb_geometry(io.BytesIO(blob), len(blob), point_count)


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
        rows = conn.execute("SELECT rowid, ID, length(Geometry) FROM Geometry ORDER BY ID").fetchall()
        for rowid, wf_id, size in rows:
            if not size:
                continue
            name, colour = info.get(wf_id, (None, None))
            # Streamed from the database: a 300 MB wireframe is never in memory twice.
            with conn.blobopen("Geometry", "Geometry", rowid, readonly=True) as blob:
                try:
                    vertices, triangles = read_tridb_geometry(blob, size, counts.get(wf_id))
                except WireframeFormatError:
                    # A count that disagrees with the geometry is not worth losing the wireframe over.
                    vertices, triangles = read_tridb_geometry(blob, size)
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
    """POLYFACE / POLYMESH, 3DFACE and MESH entities, one wireframe per layer.

    ASCII DXF is streamed (formats/dxf_stream.py); binary DXF and files the stream reader cannot
    follow go through ezdxf, which holds the whole drawing in memory."""
    from app.formats.dxf_stream import DxfStreamError, read_dxf_meshes

    try:
        found, _ = read_dxf_meshes(path)
    except (DxfStreamError, ValueError, UnicodeDecodeError):
        return _read_dxf_ezdxf(path)
    meshes = []
    for name, (v, t, color) in found.items():
        vertices, triangles = weld(v, t)
        meshes.append(_validate(Mesh(name=name, vertices=vertices, triangles=triangles, color=color)))
    if not meshes:
        raise WireframeFormatError("В DXF нет каркасов: ожидаются POLYFACE (POLYLINE), 3DFACE или MESH")
    return meshes


def _read_dxf_ezdxf(path: Path) -> list[Mesh]:
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


# ---------------------------------------------------------------- OBJ, STL


def read_obj(path: Path) -> list[Mesh]:
    """Wavefront OBJ: `v` vertices, `f` faces (v, v/vt, v/vt/vn, v//vn; negative = relative),
    one wireframe per `o` / `g` name. Polygons are fan-triangulated."""
    from array import array

    coords = array("d")
    groups: dict[str, array] = {}
    current = groups.setdefault("OBJ", array("q"))
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("v "):
                parts = line.split()
                try:
                    coords.extend((float(parts[1]), float(parts[2]), float(parts[3])))
                except (IndexError, ValueError) as exc:
                    raise WireframeFormatError(f"OBJ: неверная вершина «{line.strip()[:60]}»") from exc
            elif line.startswith("f "):
                n = len(coords) // 3
                try:
                    idx = [int(tok.split("/")[0]) for tok in line.split()[1:]]
                except ValueError as exc:
                    raise WireframeFormatError(f"OBJ: неверная грань «{line.strip()[:60]}»") from exc
                idx = [i - 1 if i > 0 else n + i for i in idx]
                for k in range(1, len(idx) - 1):
                    current.extend((idx[0], idx[k], idx[k + 1]))
            elif line.startswith(("o ", "g ")):
                name = line[2:].strip() or "OBJ"
                current = groups.setdefault(name, array("q"))
    v = np.frombuffer(coords, dtype=np.float64).reshape(-1, 3)
    meshes = []
    for name, tri in groups.items():
        if not tri:
            continue
        t = np.frombuffer(tri, dtype=np.int64).reshape(-1, 3)
        if t.min() < 0 or t.max() >= len(v):
            raise WireframeFormatError(f"OBJ «{name}»: номера вершин граней вне диапазона")
        used, local = np.unique(t, return_inverse=True)
        meshes.append(_validate(Mesh(name=name, vertices=v[used], triangles=local.reshape(-1, 3).astype(np.int32))))
    if not meshes:
        raise WireframeFormatError("В OBJ нет граней (строк «f»)")
    return meshes


_STL_NOTE = "STL хранит координаты в float32: на координатах UTM шаг около 0,5 м — точность ниже, чем у исходной модели"


def read_stl(path: Path) -> list[Mesh]:
    """Binary or ASCII STL: a triangle soup, welded. One wireframe named after the solid."""
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(84)
    count = int.from_bytes(head[80:84], "little") if len(head) >= 84 else -1
    name = path.stem
    if len(head) >= 84 and 84 + 50 * count == size:
        rec = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
        data = np.fromfile(path, dtype=rec, offset=84, count=count)
        soup = data["v"].reshape(-1, 3).astype(np.float64)
        label = head[:80].split(b"\x00")[0].decode("ascii", "replace").strip()
        if label and not label.lower().startswith("solid"):
            name = label[:100]
    else:
        text = path.read_text(encoding="ascii", errors="replace")
        if not text.lstrip().lower().startswith("solid"):
            raise WireframeFormatError("Файл не похож на STL (ни двоичный, ни текстовый)")
        first = text.lstrip().splitlines()[0].split(maxsplit=1)
        if len(first) > 1:
            name = first[1].strip()[:100]
        import re

        nums = re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", text)
        try:
            soup = np.array(nums, dtype=np.float64)
        except ValueError as exc:
            raise WireframeFormatError("STL: неверные координаты вершин") from exc
        if len(soup) % 3:
            raise WireframeFormatError("STL: число вершин не кратно трём")
    if len(soup) == 0:
        raise WireframeFormatError("В STL нет треугольников")
    vertices, triangles = weld(soup, np.arange(len(soup)).reshape(-1, 3))
    return [_validate(Mesh(name=name or "STL", vertices=vertices, triangles=triangles, attributes={"note": _STL_NOTE}))]


def read_wireframes(path: Path, suffix: str) -> tuple[str, list[Mesh]]:
    """Returns (format name, meshes)."""
    suffix = suffix.lower()
    if suffix == ".tridb":
        return "Micromine TRIDB", read_tridb(path)
    if suffix == ".dxf":
        return "DXF", read_dxf(path)
    if suffix == ".obj":
        return "OBJ", read_obj(path)
    if suffix == ".stl":
        return "STL", read_stl(path)
    raise WireframeFormatError(f"Формат {suffix or 'без расширения'} не поддерживается для каркасов: нужны {', '.join(WIREFRAME_SUFFIXES)}")
