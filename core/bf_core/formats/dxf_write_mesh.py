"""DXF writer of wireframes (ТЗ 7.21, 9.5, 9.12; ADR 0013 §2): `write_dxf_meshes(path, meshes)`.

A wireframe is a DXF layer (its colour: the nearest ACI and the true colour) of polyface meshes — POLYLINE with
flag 64, as Micromine writes them and as the sample of the owner has them: VERTEX records of the vertices (flag
192), then of the faces (flag 128; 71–73 — the numbers of the vertices from 1). The drawing around the entities
is that of `dxf_write`: AC1018, ANSI_1251, CRLF, the frame from ezdxf, the entities as text with handles of
their own.

The counts of a polyface are 16-bit groups, so it holds at most 32 767 vertices and 32 767 faces (the limit of
AutoCAD). A bigger wireframe is several polyfaces of its layer: its triangles in their order, as many a polyface
as fit both limits; a vertex that several polyfaces use is repeated in each, and the reader welds them back.

Coordinates are written with `repr`, the vertices of a polyface in the order its triangles use them, after an
exact weld. So the file depends only on the corners of the triangles in their order, and `dxf_meshes` gives
back the same triangles on the same coordinates bit for bit — and the same vertex numbers when the vertices
were numbered in the order of use, as those of every wireframe read from a DXF are: import → export → import
changes nothing, and the second export has the entities of the first byte for byte (the frame differs: ezdxf
gives every drawing GUIDs and the time of writing).

DXF has no attributes of a wireframe: they are not written (a warning). `meshes` may be a generator — one
wireframe is in memory at a time; the entities go to a temporary file first, because the frame (the LAYER
table, the extent) is known only when the last wireframe is. Error codes: no_meshes, too_large.

Ported from BF `formats/export.py::write_dxf_mesh`. Differences: `MeshSet`, a polyface of up to 32 767 faces,
vertices in the order of use (the vertex numbers survive the cycle), streaming, progress and cancellation.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterable
from itertools import chain
from pathlib import Path

import numpy as np

from bf_core.formats.dxf_write import CODEC, ERRORS, _color_tags, _encodable, _entity, _frame, layer_names
from bf_core.formats.mesh_io import MeshWritten, fail, lost_attrs, no_meshes, numbered, part_files
from bf_core.formats.meshset import MeshSet, n_text, name_in_file, weld_exact
from bf_core.formats.table import Progress, no_progress

MAX_VERTICES = MAX_FACES = 32767  # of a polyface: the groups 71 and 72 are 16-bit
FIRST_HANDLE = 1 << 20  # of the entities: above the handles ezdxf gives to the tables of any number of layers
CHUNK = 50_000  # records formatted at once


def blocks(tri: np.ndarray) -> list[tuple[int, int]]:
    """Triangles [a, b) of every polyface of a wireframe: as many as have at most `MAX_VERTICES` vertices,
    and at most `MAX_FACES`."""
    out: list[tuple[int, int]] = []
    a, m = 0, len(tri)
    while a < m:
        corners = np.asarray(tri[a : a + MAX_FACES]).reshape(-1)
        b = a + len(corners) // 3
        if len(corners) > MAX_VERTICES:  # the vertices used after every corner: where they pass the limit
            fresh = np.zeros(len(corners), dtype=np.int64)
            fresh[np.unique(corners, return_index=True)[1]] = 1
            b = a + int(np.searchsorted(np.cumsum(fresh)[2::3], MAX_VERTICES, side="right"))
        out.append((a, b))
        a = b
    return out


def in_order_of_use(tri: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(the vertices triangles use, in the order they first use them; the triangles on their numbers from 1)."""
    found, first, inverse = np.unique(tri.reshape(-1), return_index=True, return_inverse=True)
    order = np.argsort(first, kind="stable")
    rank = np.empty(len(found), dtype=np.int64)
    rank[order] = np.arange(1, len(found) + 1)
    return found[order], rank[inverse.reshape(-1)].reshape(-1, 3)


def _layer_name(name: str, taken: set[str]) -> str:
    """A name DXF accepts (`dxf_write.layer_names`) that no layer written before has."""
    base = out = layer_names([name])[0]
    k = 1
    while out.casefold() in taken:
        k += 1
        out = f"{base[: 255 - len(str(k)) - 1]}_{k}"
    taken.add(out.casefold())
    return out


def write_dxf_meshes(
    path: str | Path, meshes: Iterable[MeshSet], progress: Progress | None = None, *, total: int | None = None
) -> MeshWritten:
    """Write wireframes as an ASCII DXF of polyface meshes (see the module docstring). `total` — the number
    of wireframes of a generator, for the progress. `progress(fraction, message)` may raise to cancel."""
    path = Path(path)
    tick = progress or no_progress
    tick(0.0, "Запись DXF")
    space = _frame([], None, 0)[2]  # the owner of the entities: the model space of a new drawing
    layers: list[tuple[str, str | None]] = []
    taken: set[str] = set()
    renamed: list[str] = []
    low, high = np.full(3, np.inf), np.full(3, -np.inf)
    handle = FIRST_HANDLE
    points = triangles = polyfaces = with_attrs = 0
    body = tempfile.TemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".part")
    with body as entities, part_files(path) as (out,):
        for _, mesh, share in numbered(meshes, total, tick):
            wanted = name_in_file(mesh) or mesh.name
            name = _encodable(_layer_name(wanted, taken))
            if name != wanted:
                renamed.append(f"«{wanted}» → «{name}»")
            layers.append((name, mesh.color))
            with_attrs += bool(mesh.attrs)
            xyz, tri, _ = weld_exact(mesh.xyz, mesh.tri)
            seen = np.zeros(len(xyz), dtype=bool)
            for a, b in blocks(tri):
                used, local = in_order_of_use(np.asarray(tri[a:b]))
                corners = xyz[used]
                low, high = np.minimum(low, corners.min(axis=0)), np.maximum(high, corners.max(axis=0))
                entities.write(_polyface(name, space, mesh.color, handle, corners, local))
                handle += len(used) + len(local) + 2
                points += int(np.count_nonzero(~seen[used]))
                seen[used] = True
                polyfaces += 1
                share(b / len(tri), f"Запись DXF: «{mesh.name}», треугольников {n_text(b)} из {n_text(len(tri))}")
            triangles += len(tri)
        if not layers:
            raise no_meshes("DXF")
        head, tail, owner, first = _frame(layers, (low, high), handle)
        if owner != space or first > FIRST_HANDLE:  # (a million layers: never in practice)
            raise fail("too_large", "Каркасов слишком много для одного файла DXF.", "Экспортируйте их частями.")
        out.write(head)
        entities.seek(0)
        shutil.copyfileobj(entities, out, 4 << 20)
        out.write(tail)
    warnings = lost_attrs(with_attrs, "DXF")
    if renamed:
        shown = ", ".join(renamed[:5]) + (f" и ещё {n_text(len(renamed) - 5)}" if len(renamed) > 5 else "")
        warnings.append(f"Названия слоёв DXF изменены (недопустимые знаки или повтор): {shown}")
    tick(1.0, f"Записано каркасов: {n_text(len(layers))}")
    return MeshWritten(path, len(layers), points, triangles, path.stat().st_size, warnings, [path])


def _polyface(name: str, space: str, color: str | None, handle: int, xyz: np.ndarray, faces: np.ndarray) -> bytes:
    """A polyface mesh: its header, vertices, faces and SEQEND, with the handles from `handle` on."""

    def raw(text: str) -> bytes:  # a template of bytes: a number is put in by %a — its repr
        return text.replace("%r", "%a").encode(CODEC, errors=ERRORS)

    colors = _color_tags(color)
    owner = f"{handle:X}"
    out = [
        raw(_entity("POLYLINE", space, name, colors + "100\r\nAcDbPolyFaceMesh\r\n")) % handle
        + b" 66\r\n     1\r\n 10\r\n0.0\r\n 20\r\n0.0\r\n 30\r\n0.0\r\n 70\r\n    64\r\n"
        + f" 71\r\n{len(xyz):>6}\r\n 72\r\n{len(faces):>6}\r\n".encode("ascii")
    ]
    vertex = raw(
        _entity("VERTEX", owner, name, "100\r\nAcDbVertex\r\n100\r\nAcDbPolyFaceMeshVertex\r\n")
        + " 10\r\n%r\r\n 20\r\n%r\r\n 30\r\n%r\r\n 70\r\n   192\r\n"
    )
    face = raw(
        _entity("VERTEX", owner, name, "100\r\nAcDbFaceRecord\r\n")
        + " 10\r\n0.0\r\n 20\r\n0.0\r\n 30\r\n0.0\r\n 70\r\n   128\r\n 71\r\n%6d\r\n 72\r\n%6d\r\n 73\r\n%6d\r\n"
    )
    handle += 1
    for template, rows in ((vertex, xyz), (face, faces)):
        for a in range(0, len(rows), CHUNK):
            part = rows[a : a + CHUNK]
            ids = range(handle + a, handle + a + len(part))
            columns = zip(ids, part[:, 0].tolist(), part[:, 1].tolist(), part[:, 2].tolist(), strict=True)
            out.append((template * len(part)) % tuple(chain.from_iterable(columns)))
        handle += len(rows)
    out.append(raw(_entity("SEQEND", owner, name, colors)) % handle)
    return b"".join(out)
