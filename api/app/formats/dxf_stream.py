"""Streaming reader of DXF wireframes (ASCII DXF): POLYFACE and polygon-mesh POLYLINEs, 3DFACE, MESH.

ezdxf builds a Python object for every entity and vertex: a 200 MB DXF needs several GB of
memory. This reader walks the group-code pairs in 16 MB blocks and appends coordinates to
compact arrays per layer, so memory stays close to the size of the resulting geometry.
Binary DXF is left to ezdxf (formats/wireframes.read_dxf).
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_BLOCK = 16 << 20


class DxfStreamError(ValueError):
    pass


@dataclass
class _Layer:
    vertices: array = field(default_factory=lambda: array("d"))
    triangles: array = field(default_factory=lambda: array("q"))
    color: str | None = None

    @property
    def count(self) -> int:
        return len(self.vertices) // 3


def _pairs(path: Path):
    """(code, value) as (int, bytes), in file order, read in blocks."""
    carry = b""
    pending_code: bytes | None = None
    with path.open("rb") as f:
        while True:
            block = f.read(_BLOCK)
            if not block:
                break
            lines = (carry + block).split(b"\n")
            carry = lines.pop()
            it = iter(lines)
            if pending_code is not None:
                first = next(it, None)
                if first is None:
                    continue
                yield int(pending_code), first.strip()
                pending_code = None
            for code in it:
                value = next(it, None)
                if value is None:
                    pending_code = code
                    break
                try:
                    yield int(code), value.strip()
                except ValueError:
                    raise DxfStreamError(f"group code expected, got {code[:20]!r}") from None
    if pending_code is not None and carry.strip():
        yield int(pending_code), carry.strip()


def _aci_hex(aci: int) -> str | None:
    from ezdxf import colors

    if not 1 <= aci <= 255 or aci == 7:
        return None
    r, g, b = colors.aci2rgb(aci)
    return f"#{r:02x}{g:02x}{b:02x}"


def _true_hex(value: int) -> str:
    return f"#{(value >> 16) & 255:02x}{(value >> 8) & 255:02x}{value & 255:02x}"


def _fan(corners: list[int]) -> list[tuple[int, int, int]]:
    unique: list[int] = []
    for i in corners:
        if not unique or unique[-1] != i:
            unique.append(i)
    if len(unique) > 1 and unique[0] == unique[-1]:
        unique.pop()
    return [(unique[0], unique[k], unique[k + 1]) for k in range(1, len(unique) - 1)]


def read_dxf_meshes(path: Path) -> tuple[dict[str, tuple[np.ndarray, np.ndarray, str | None]], dict]:
    """Layer -> (float64 vertices, int64 triangles, colour). Also simple counts for the log."""
    with path.open("rb") as f:
        if f.read(22).startswith(b"AutoCAD Binary DXF"):
            raise DxfStreamError("binary DXF")

    layers: dict[str, _Layer] = {}
    layer_colors: dict[str, str | None] = {}
    counts = {"3DFACE": 0, "POLYFACE": 0, "POLYMESH": 0, "MESH": 0}

    section = None
    entity: dict | None = None  # attributes of the entity being read
    poly: dict | None = None  # an open POLYLINE (polyface / polygon mesh) with its VERTEX records
    table_layer: dict | None = None

    def layer_for(attrs: dict) -> _Layer:
        name = attrs.get("layer", "0")
        lay = layers.get(name)
        if lay is None:
            lay = layers[name] = _Layer()
        if lay.color is None:
            if "true" in attrs:
                lay.color = _true_hex(attrs["true"])
            elif attrs.get("aci", 256) not in (0, 256):
                lay.color = _aci_hex(attrs["aci"])
            else:
                lay.color = layer_colors.get(name)
        return lay

    def finish_face(attrs: dict) -> None:
        pts = [attrs.get(k) for k in (0, 1, 2, 3)]
        pts = [p for p in pts if p is not None and None not in p]
        if len(pts) < 3:
            return
        lay = layer_for(attrs)
        base = lay.count
        for p in pts:
            lay.vertices.extend(p)
        idx = list(range(base, base + len(pts)))
        # A 3DFACE repeats its third corner as the fourth for a triangle.
        if len(pts) == 4 and pts[3] == pts[2]:
            idx = idx[:3]
        for tri in _fan(idx):
            lay.triangles.extend(tri)
        counts["3DFACE"] += 1

    def finish_poly(p: dict) -> None:
        lay = layer_for(p)
        base = lay.count
        verts = p["verts"]
        for v in verts:
            lay.vertices.extend(v)
        if p["mesh"] == "face":
            for face in p["faces"]:
                corners = [abs(i) - 1 for i in face if i != 0]
                if any(c >= len(verts) or c < 0 for c in corners):
                    continue
                for tri in _fan([base + c for c in corners]):
                    lay.triangles.extend(tri)
            counts["POLYFACE"] += 1
        else:  # polygon mesh: M × N vertices in rows
            m, n = p.get("m", 0), p.get("n", 0)
            if m * n != len(verts):
                return
            for i in range(m - 1):
                for j in range(n - 1):
                    a, b = base + i * n + j, base + i * n + j + 1
                    c, d = base + (i + 1) * n + j + 1, base + (i + 1) * n + j
                    lay.triangles.extend((a, b, c, a, c, d))
            counts["POLYMESH"] += 1

    def finish_mesh(attrs: dict) -> None:
        verts = attrs.get("mverts", [])
        lay = layer_for(attrs)
        base = lay.count
        for v in verts:
            lay.vertices.extend(v)
        faces = attrs.get("mfaces", [])
        i = 0
        while i < len(faces):
            n = faces[i]
            corners = faces[i + 1:i + 1 + n]
            i += 1 + n
            if len(corners) == n and all(0 <= c < len(verts) for c in corners):
                for tri in _fan([base + c for c in corners]):
                    lay.triangles.extend(tri)
        counts["MESH"] += 1

    def close_entity() -> None:
        nonlocal entity
        if entity is None:
            return
        kind = entity["kind"]
        if kind == "POLYLINE" and entity.get("poly") is not None:
            # Layer and colour of the header, whichever order its codes came in.
            entity["poly"].update({k: entity[k] for k in ("layer", "aci", "true") if k in entity})
        elif kind == "3DFACE":
            finish_face(entity)
        elif kind == "MESH":
            finish_mesh(entity)
        elif kind == "VERTEX" and poly is not None:
            flags = entity.get("flags", 0)
            if poly["mesh"] == "face" and flags & 128 and not flags & 64:
                poly["faces"].append([entity.get(c, 0) for c in (71, 72, 73, 74)])
            elif entity.get(0) is not None and None not in entity[0]:
                poly["verts"].append(entity[0])
        entity = None

    for code, value in _pairs(path):
        if code == 0:
            close_entity()
            name = value.decode("ascii", "replace")
            if name == "SECTION":
                section = "?"
                continue
            if name == "ENDSEC":
                section = None
                continue
            if section == "TABLES":
                if table_layer is not None:
                    layer_colors[table_layer.get("name", "0")] = table_layer.get("color")
                table_layer = {} if name == "LAYER" else None
                continue
            if section != "ENTITIES":
                continue
            if name == "SEQEND":
                if poly is not None:
                    finish_poly(poly)
                poly = None
                continue
            if name == "VERTEX" and poly is None:
                continue
            if name == "POLYLINE":
                poly = None
                entity = {"kind": "POLYLINE"}
                continue
            entity = {"kind": name} if name in ("3DFACE", "MESH", "VERTEX") else None
            continue
        if section == "?":
            if code == 2:
                section = value.decode("ascii", "replace")
            continue
        if section == "TABLES" and table_layer is not None:
            if code == 2:
                table_layer["name"] = value.decode("cp1251", "replace")
            elif code == 62:
                table_layer["color"] = _aci_hex(abs(int(value)))
            elif code == 420:
                table_layer["color"] = _true_hex(int(value))
            continue
        if entity is None:
            continue
        kind = entity["kind"]
        if code == 8:
            entity["layer"] = value.decode("cp1251", "replace")
        elif code == 62:
            entity["aci"] = int(value)
        elif code == 420:
            entity["true"] = int(value)
        elif kind == "POLYLINE" and code == 70:
            flags = int(value)
            # The header of a polyface (64) or polygon mesh (16): its VERTEX records follow.
            if flags & 64:
                poly = {"mesh": "face", "verts": [], "faces": [], **{k: entity[k] for k in ("layer", "aci", "true") if k in entity}}
            elif flags & 16:
                poly = {"mesh": "grid", "verts": [], **{k: entity[k] for k in ("layer", "aci", "true") if k in entity}}
            entity["poly"] = poly
        elif kind == "POLYLINE" and code in (71, 72) and entity.get("poly"):
            entity["poly"]["m" if code == 71 else "n"] = int(value)
        elif kind in ("3DFACE", "VERTEX") and 10 <= code <= 33:
            slot, axis = code % 10, code // 10 - 1
            p = entity.setdefault(slot, [None, None, None])
            p[axis] = float(value)
        elif kind == "VERTEX" and code == 70:
            entity["flags"] = int(value)
        elif kind == "VERTEX" and code in (71, 72, 73, 74):
            entity[code] = int(value)
        elif kind == "MESH":
            if code == 92:
                entity["need_v"] = int(value)
                entity["mverts"] = []
            elif code in (10, 20, 30) and "mverts" in entity:
                if code == 10:
                    entity["mverts"].append([float(value), None, None])
                else:
                    entity["mverts"][-1][1 if code == 20 else 2] = float(value)
            elif code == 93:
                entity["need_f"] = int(value)
                entity["mfaces"] = []
            elif code == 90 and entity.get("need_f", 0) > len(entity.get("mfaces", [])):
                entity["mfaces"].append(int(value))
    close_entity()

    # A polyline header that announced a polyface keeps the layer of its POLYLINE entity.
    out = {}
    for name, lay in layers.items():
        if not lay.triangles:
            continue
        v = np.frombuffer(lay.vertices, dtype=np.float64).reshape(-1, 3)
        t = np.frombuffer(lay.triangles, dtype=np.int64).reshape(-1, 3)
        out[name] = (v, t, lay.color)
    return out, counts
