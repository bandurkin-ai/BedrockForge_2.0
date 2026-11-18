"""DXF meshes → wireframes, one per DXF layer (ТЗ 7.21, 9.5, 9.12; ADR 0013 §2).

`dxf_lines.read_dxf` reads the meshes of a drawing in the pass that reads its lines: its reader gives the mesh
entities of every block to `Meshes`, and `DxfRead.wireframes` holds a `MeshSet` per layer, in the order of the
file. `read_dxf_meshes` / `inspect_dxf_meshes` are that reading for a caller that wants wireframes only.

* POLYLINE with flag 64 — a polyface mesh: VERTEX records with flag 128 and without 64 are faces (71–74 — the
  numbers of their vertices from 1; a negative number — an invisible edge, 0 — no corner), the others — vertices.
* POLYLINE with flag 16 — a polygon mesh of M × N vertices (71, 72; of a smoothed one — 73, 74 without the
  control points): a cell is two triangles; closed in M (flag 1) or N (flag 32) — the last row meets the first.
* 3DFACE — three or four corners; MESH — the base mesh of a subdivision surface (92 — vertices, 93 — the face
  list), polygons of any number of corners as a fan.
A face of three corners is a triangle as it is; of four — a corner that repeats the one before is left out,
then three are a triangle and four — two triangles (0, 1, 2), (0, 2, 3). All four kinds store world coordinates:
the extrusion of a mesh entity does not turn it (as ezdxf reads them).

The vertices of a layer are welded exactly (`meshset.ExactWelder`) in the order the triangles of the file use
them: equal corners are one vertex, a vertex no face uses is left out. So the triangles and the corners come in
the order of the file whatever entities it is made of, and a wireframe written by `dxf_write_mesh` — which cuts
it into polyfaces of at most 32 767 vertices — comes back bit for bit, vertex numbers included.
A face on a vertex without a coordinate (never a 0 instead) or on a number that is not a vertex is left out and
counted in the warnings of the wireframe, as a face of fewer than three different corners is.
Memory: the result, one block, and a polyface that goes on over blocks packed (24 bytes a vertex, 16 a face).
`Limits.triangles` stops a "bomb" (too_large).

Ported from BF `formats/dxf_stream.py::read_dxf_meshes`. Differences: arrays instead of a Python loop over the
pairs, one pass with the lines, exact welding in the order of use, invisible edges, closed polygon meshes,
vertices kept by their numbers when one is broken, limits, Russian warnings.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from bf_core.formats.dxf_layers import color_of
from bf_core.formats.dxf_stream import Block, n_text
from bf_core.formats.meshset import ExactWelder, MeshEntry, MeshSet, clean_name, source_of
from bf_core.formats.table import Progress

FORMAT = "dxf"
SLICE = 1 << 18  # triangles welded at once (≈ 19 MB of corners)
POLYFACE, POLYMESH, FACE3D, SUBD = "POLYFACE", "POLYMESH", "3DFACE", "MESH"
Common = Callable[[Block, np.ndarray], tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]


def fan4(c: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Faces of up to four corners → triangles. `c` — F×4 vertex numbers, negative — no corner. Returns
    (triangles in the order of the faces, the number of triangles of every face): see the module docstring."""
    have = c >= 0
    k = have.sum(axis=1)
    if not have[:, :3].all():  # the corners there are come first
        c = np.take_along_axis(c, np.argsort(~have, axis=1, kind="stable"), axis=1)
    quad = np.flatnonzero(k == 4)
    if len(quad):
        q = c[quad]
        keep = np.ones(q.shape, dtype=bool)
        keep[:, 1:] = q[:, 1:] != q[:, :-1]
        last = 3 - np.argmax(keep[:, ::-1], axis=1)  # the last corner kept: left out when it repeats the first
        rows = np.flatnonzero((last > 0) & (q[np.arange(len(q)), last] == q[:, 0]))
        keep[rows, last[rows]] = False
        if not keep.all():
            c, k = c.copy(), k.copy()
            c[quad] = np.take_along_axis(np.where(keep, q, -1), np.argsort(~keep, axis=1, kind="stable"), axis=1)
            k[quad] = keep.sum(axis=1)
    use = np.column_stack([k >= 3, k == 4])
    return np.stack([c[:, :3], c[:, [0, 2, 3]]], axis=1)[use], use.sum(axis=1)


class _Piece(NamedTuple):
    """Triangles of entities of one kind: `tri` — rows of `xyz`, `ent` — the entity of every triangle."""

    ords: np.ndarray  # per entity: the number of its record in the file
    layers: np.ndarray  # per entity
    xyz: np.ndarray
    tri: np.ndarray
    ent: np.ndarray


class _Layer:
    def __init__(self, first: tuple[int, int, int]) -> None:
        self.first = first  # (record, ACI, true colour) of the first mesh entity
        self.welder = ExactWelder()
        self.tris: list[np.ndarray] = []
        self.kinds: Counter[str] = Counter()
        self.bad = self.flat = self.smooth = 0


class _Run:
    """A mesh POLYLINE whose VERTEX records go on over blocks, kept packed until it ends."""

    def __init__(self, meshes: Meshes, head: np.ndarray, rows: np.ndarray) -> None:
        self.meshes, self.head = meshes, head[None, :]
        self.parts: list[tuple[np.ndarray, ...]] = []
        self.count = 0
        self.append(rows)

    def append(self, rows: np.ndarray) -> None:
        self.parts.append(_pack(self.head, np.array([len(rows)]), rows))
        self.count += len(rows)
        if self.count > self.meshes.limit:
            raise self.meshes.too_many()

    def close(self) -> None:
        nv, xyz, ok, _, faces = (np.concatenate([p[i] for p in self.parts]) for i in range(5))
        self.parts = []
        for a in range(0, max(len(faces), 1), SLICE):  # the faces in portions: a polyface may hold millions
            part = faces[a : a + SLICE]
            piece = self.meshes.convert(self.head, np.array([nv.sum()]), xyz, ok, np.array([len(part)]), part, a == 0)
            self.meshes.count(len(piece.tri))
            self.meshes.weld(piece)


def _pack(
    head: np.ndarray, counts: np.ndarray, rows: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """VERTEX records of mesh polylines (`dxf_lines`: x, y, z, bulge, flags, has X and Y, 71–74) → (vertices
    of every polyline, their xyz, «every coordinate is a number», faces of every polyline, their numbers)."""
    owner = np.repeat(np.arange(len(head)), counts)
    flags = rows[:, 4].astype(np.int64)
    kind = head[:, 4].astype(np.int64)[owner]
    face = ((kind & 64) != 0) & ((flags & 128) != 0) & ((flags & 64) == 0)
    vertex = ~face & ~(((kind & 68) == 4) & ((flags & 16) != 0))  # not a control point of a smoothed polygon mesh
    xyz = np.ascontiguousarray(rows[vertex, :3])
    ok = (rows[vertex, 5] != 0) & np.isfinite(xyz).all(axis=1)
    nv = np.bincount(owner[vertex], minlength=len(head))
    nf = np.bincount(owner[face], minlength=len(head))
    return nv, xyz, ok, nf, rows[face, 6:10].astype(np.int32)


class Meshes:
    """Collects the mesh entities of a drawing block by block (see the module docstring). `common(b, sel)` —
    (record numbers, layer ids, ACI, true colour) of entities, as `dxf_lines` has them; `alive()` — a tick."""

    def __init__(self, common: Common, limit: int, alive: Callable[[], None]) -> None:
        self.common, self.limit, self.alive = common, limit, alive
        self.layers: dict[int, _Layer] = {}
        self.pending: list[_Piece] = []
        self.triangles = 0

    def too_many(self) -> Exception:
        from bf_core.formats.mesh_io import too_many

        return too_many(self.limit)

    def _note(
        self,
        kind: str | None,
        ords: np.ndarray,
        layers: np.ndarray,
        aci: np.ndarray,
        true: np.ndarray,
        bad: np.ndarray | None = None,
        flat: np.ndarray | None = None,
    ) -> None:
        """Entities of a kind: the colour of a layer is that of its first mesh entity; what was left out."""
        found, first = np.unique(layers, return_index=True)
        for lay, i in zip(found.tolist(), first.tolist(), strict=True):
            layer = self.layers.get(lay)
            mark = (int(ords[i]), int(aci[i]), int(true[i]))
            if layer is None:
                layer = self.layers[lay] = _Layer(mark)
            elif mark[0] < layer.first[0]:
                layer.first = mark
            mine = layers == lay
            if kind is not None:
                layer.kinds[kind] += int(mine.sum())
            layer.bad += int(bad[mine].sum()) if bad is not None else 0
            layer.flat += int(flat[mine].sum()) if flat is not None else 0

    def count(self, triangles: int) -> None:
        self.triangles += triangles
        if self.triangles > self.limit:
            raise self.too_many()

    def _add(self, piece: _Piece) -> None:
        self.count(len(piece.tri))
        if len(piece.tri):
            self.pending.append(piece)

    # --- entities ----------------------------------------------------------------------------------------

    def faces(self, b: Block, sel: np.ndarray) -> None:
        """3DFACE entities: the fourth corner may be left out or repeat the third."""
        ords, layers, aci, true = self.common(b, sel)
        cols = [[b.col(c + j)[sel] for c in (10, 20, 30)] for j in range(4)]
        xyz = np.stack(
            [np.column_stack([b.values(x), b.values(y), b.values(z, 0.0)]) for x, y, z in cols], axis=1
        )  # faces × 4 corners × 3
        given = np.column_stack([(x >= 0) & (y >= 0) for x, y, _ in cols])
        fine = given & np.isfinite(xyz).all(axis=2)
        bad = ~fine[:, :3].all(axis=1) | (given[:, 3] & ~fine[:, 3])
        c = np.tile(np.arange(4), (len(sel), 1))
        for j in range(1, 4):  # equal corners of a face get one number, as welding would give them
            for i in range(j - 1, -1, -1):
                c[:, j] = np.where((xyz[:, j] == xyz[:, i]).all(axis=1), c[:, i], c[:, j])
        c = np.where(given & ~bad[:, None], c + 4 * np.arange(len(sel))[:, None], -1)
        tri, made = fan4(c)
        self._note(FACE3D, ords, layers, aci, true, bad, (made == 0) & ~bad)
        self._add(_Piece(ords, layers, xyz.reshape(-1, 3), tri, np.repeat(np.arange(len(sel)), made)))

    def subd(self, b: Block, sel: np.ndarray) -> None:
        """MESH entities: the base mesh (level 0) of a subdivision surface."""
        from bf_core.formats import _meshkernel as k

        ords, layers, aci, true = self.common(b, sel)
        one = np.ones(1, dtype=np.int64)
        for i, r in enumerate(sel.tolist()):
            lo = int(b.z[r])
            hi = int(b.z[r + 1]) if r + 1 < len(b) else len(b.code)
            code, num = b.code[lo:hi], b.num[lo:hi]
            p92, p93 = np.flatnonzero(code == 92)[:1], np.flatnonzero(code == 93)[:1]
            tri, smooth, bad = np.empty((0, 3), dtype=np.int64), 0, one
            xyz = np.empty((0, 3))
            if len(p92) and len(p93) and p92[0] < p93[0]:
                at = slice(int(p92[0]) + 1, int(p93[0]))
                x, y, z = (num[at][code[at] == c] for c in (10, 20, 30))
                rest_code, rest_num = code[int(p93[0]) + 1 :], num[int(p93[0]) + 1 :]
                end = np.flatnonzero(rest_code == 94)[:1]  # the edges follow the faces under the same code 90
                stop = int(end[0]) if len(end) else len(rest_code)
                listed = rest_num[:stop][rest_code[:stop] == 90]
                size = num[p93[0]]
                listed = listed[: int(size)] if 0 <= size < len(listed) else listed
                if len(x) == len(y) == len(z) and len(x):
                    xyz = np.column_stack([x, y, z])
                    counts = np.empty(len(listed), dtype=np.int64)
                    corners = np.empty(len(listed), dtype=np.int64)
                    nf, nc, whole = k.face_list(np.ascontiguousarray(listed), len(xyz), counts, corners)
                    counts, corners = counts[:nf], corners[:nc]
                    lost = corners >= 0
                    lost[lost] = ~np.isfinite(xyz[corners[lost]]).all(axis=1)
                    corners[lost] = -1
                    tri = np.empty((int(np.maximum(counts - 2, 0).sum()), 3), dtype=np.int64)
                    made, left = k.fan(corners, counts, tri, np.empty(len(tri), dtype=np.int64))
                    tri, bad = tri[:made], one * (int(left) + (not whole))
                    level = num[code == 91][:1]
                    smooth = int(len(level) and level[0] > 0)
            lay = slice(i, i + 1)
            self._note(SUBD, ords[lay], layers[lay], aci[lay], true[lay], bad)
            self.layers[int(layers[i])].smooth += smooth
            self._add(_Piece(ords[lay], layers[lay], xyz, tri, np.zeros(len(tri), dtype=np.int64)))
            self.alive()

    def polylines(self, head: np.ndarray, counts: np.ndarray, rows: np.ndarray) -> None:
        """Mesh POLYLINE entities with their VERTEX records (`dxf_lines._runs`: `head` — record, layer, ACI,
        true colour, flags, elevation, normal, in model space, 71–74 of every polyline)."""
        self._add(self.convert(head, *_pack(head, counts, rows)))

    def run(self, head: np.ndarray, rows: np.ndarray) -> _Run:
        """A mesh polyline that goes on in the next block."""
        return _Run(self, head, rows)

    def convert(
        self,
        head: np.ndarray,
        nv: np.ndarray,
        xyz: np.ndarray,
        ok: np.ndarray,
        nf: np.ndarray,
        faces: np.ndarray,
        entities: bool = True,
    ) -> _Piece:
        """Packed polylines (`_pack`) → their triangles. `entities` False — more faces of a polyface that was
        counted already."""
        n = len(head)
        flags = head[:, 4].astype(np.int64)
        start = np.cumsum(nv) - nv
        bad, flat = np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.int64)
        parts: list[tuple[np.ndarray, np.ndarray]] = []
        if len(faces):  # polyfaces
            owner = np.repeat(np.arange(n), nf)
            number = np.abs(faces.astype(np.int64))
            c = np.where(number > 0, number - 1 + start[owner][:, None], -1)
            wrong = np.asarray((number > nv[owner][:, None]).any(axis=1))
            wrong |= ~np.append(ok, True)[np.where((c >= 0) & ~wrong[:, None], c, len(ok))].all(axis=1)
            tri, made = fan4(np.where(wrong[:, None], -1, c))
            bad += np.bincount(owner[wrong], minlength=n)
            flat += np.bincount(owner[(made == 0) & ~wrong], minlength=n)
            parts.append((tri, np.repeat(owner, made)))
        grid = np.flatnonzero((flags & 64) == 0)  # polygon meshes: M × N vertices, of a smoothed one — 73 × 74
        smooth = ((flags[grid] & 4) != 0) & (head[grid, 10] * head[grid, 11] != nv[grid])
        dims = np.where(smooth[:, None], head[grid, 12:14], head[grid, 10:12]).astype(np.int64)
        fits = np.asarray((dims[:, 0] * dims[:, 1] == nv[grid]) & (dims.min(axis=1, initial=1) >= 1))
        bad[grid[~fits]] += 1
        grid, dims = grid[fits], dims[fits]
        shape = np.column_stack([dims, (flags[grid] & 1) != 0, (flags[grid] & 32) != 0])
        for m, w, close_m, close_n in np.unique(shape, axis=0).tolist():
            i, j = np.meshgrid(np.arange(m if close_m else m - 1), np.arange(w if close_n else w - 1), indexing="ij")
            i, j, i1, j1 = i.ravel(), j.ravel(), (i.ravel() + 1) % m, (j.ravel() + 1) % w
            cell = np.column_stack([i * w + j, i * w + j1, i1 * w + j1, i1 * w + j])
            mine = grid[(shape == (m, w, close_m, close_n)).all(axis=1)]
            c = (cell[None, :, :] + start[mine][:, None, None]).reshape(-1, 4)
            owner = np.repeat(mine, len(cell))
            wrong = ~ok[c].all(axis=1)
            tri, made = fan4(np.where(wrong[:, None], -1, c))
            bad += np.bincount(owner[wrong], minlength=n)
            flat += np.bincount(owner[(made == 0) & ~wrong], minlength=n)
            parts.append((tri, np.repeat(owner, made)))
        tri = np.concatenate([p[0] for p in parts]) if parts else np.empty((0, 3), dtype=np.int64)
        ent = np.concatenate([p[1] for p in parts]) if parts else np.empty(0, dtype=np.int64)
        if len(parts) > 1:
            order = np.argsort(ent, kind="stable")
            tri, ent = tri[order], ent[order]
        ords, layers = head[:, 0].astype(np.int64), head[:, 1].astype(np.int32)
        for kind, mask in ((POLYFACE, (flags & 64) != 0), (POLYMESH, (flags & 64) == 0)):
            if mask.any():
                marks = (ords[mask], layers[mask], head[mask, 2], head[mask, 3], bad[mask], flat[mask])
                self._note(kind if entities else None, *marks)
        return _Piece(ords, layers, xyz, tri, ent)

    # --- welding -----------------------------------------------------------------------------------------

    def flush(self) -> None:
        """The entities of a block are all here: their triangles go to the layers in the order of the file."""
        pieces, self.pending = self.pending, []
        if len(pieces) == 1:
            self.weld(pieces[0])
        elif pieces:
            base = np.cumsum([0, *(len(p.xyz) for p in pieces)])
            order = np.argsort(np.concatenate([p.ords[p.ent] for p in pieces]), kind="stable")
            tri = np.concatenate([p.tri + base[i] for i, p in enumerate(pieces)])[order]
            layer = np.concatenate([p.layers[p.ent] for p in pieces])[order]
            self.weld(_Piece(layer, layer, np.concatenate([p.xyz for p in pieces]), tri, np.arange(len(tri))))

    def weld(self, piece: _Piece) -> None:
        """The corners of the triangles of a piece, in their order, into the vertices of their layers."""
        layer = piece.layers[piece.ent]
        found = np.unique(layer)
        for lay in found.tolist():
            tri = piece.tri if len(found) == 1 else piece.tri[layer == lay]
            target = self.layers[lay]
            for a in range(0, len(tri), SLICE):
                part = tri[a : a + SLICE]
                target.tris.append(target.welder.add(piece.xyz[part.reshape(-1)]).reshape(-1, 3))
                self.alive()

    # --- the result --------------------------------------------------------------------------------------

    def result(
        self, names: dict[int, str], tables: dict[int, tuple[int, int] | None]
    ) -> tuple[list[MeshSet], list[str]]:
        """(a wireframe per layer in the order of their first mesh entities, warnings of the file)."""
        self.flush()
        out: list[MeshSet] = []
        empty: list[str] = []
        for lay, layer in sorted(self.layers.items(), key=lambda item: item[1].first[0]):
            raw = names[lay]
            notes: list[str] = []
            if layer.bad:
                notes.append(
                    f"Граней и сеток с номерами вне списка вершин или на вершинах без координат: "
                    f"{n_text(layer.bad)} — пропущены"
                )
            if layer.flat:
                notes.append(f"Граней, у которых меньше трёх разных вершин: {n_text(layer.flat)} — пропущены")
            if layer.smooth:
                notes.append(
                    f"Сглаженные сетки MESH: {n_text(layer.smooth)} — загружена исходная сетка, без сглаживания"
                )
            if not layer.tris:
                empty.append(f"«{raw}»" + (f" ({notes[0].split(' — ')[0].lower()})" if notes else ""))
                continue
            tri = layer.tris[0] if len(layer.tris) == 1 else np.concatenate(layer.tris)
            layer.tris = []
            name = clean_name(raw)
            source = source_of(FORMAT, raw, name, layer=raw, entities=dict(sorted(layer.kinds.items())))
            color = color_of(layer.first[1], layer.first[2], tables.get(lay))
            out.append(MeshSet(name, layer.welder.vertices(), tri, color=color, source=source, warnings=notes))
        warnings = []
        if empty:
            shown = ", ".join(empty[:8]) + (" и другие" if len(empty) > 8 else "")
            warnings.append(f"Сетки слоёв без единого треугольника не загружены: {shown}")
        return out, warnings


# --- wireframes only -----------------------------------------------------------------------------------------


def read_dxf_meshes(
    path: str | Path, keys: Iterable[int | str] | None = None, progress: Progress | None = None, **options: Any
) -> Iterator[MeshSet]:
    """The wireframes of a DXF file one by one (`keys` — only these: numbers from 0 in the order of the file).
    The lines of the drawing are read too and dropped; `options` — those of `dxf_lines.read_dxf`. The
    warnings of the file come with the first wireframe. Error codes: those of `read_dxf` and no_meshes."""
    from bf_core.formats.dxf_lines import read_dxf
    from bf_core.formats.mesh_io import fail

    read = read_dxf(path, progress, **options)
    if not read.wireframes:
        raise fail(
            "no_meshes",
            "В файле DXF нет каркасов: сеток POLYFACE (POLYLINE), 3DFACE и MESH.",
            "Проверьте, что каркас экспортирован в DXF как сеть граней, а не как линии.",
        )
    wanted = None if keys is None else set(keys)
    first = True
    for i, mesh in enumerate(read.wireframes):
        if wanted is None or i in wanted:
            mesh.warnings[:0] = read.warnings if first else []
            first = False
            yield mesh


def inspect_dxf_meshes(
    path: str | Path, progress: Progress | None = None, **options: Any
) -> tuple[list[MeshEntry], list[str]]:
    """The composition of a DXF file as wireframes: (entries, warnings of the file). The file is read whole:
    the vertices of a DXF mesh are counted only once they are welded."""
    from bf_core.formats.dxf_lines import read_dxf
    from bf_core.formats.mesh_io import entry_of

    read = read_dxf(path, progress, **options)
    return [entry_of(i, mesh) for i, mesh in enumerate(read.wireframes)], read.warnings
