"""Wavefront OBJ — reading and writing wireframes (ТЗ 7.21, 9.12; ADR 0013 §2).

Reading. `v x y z` — a vertex (what follows the third number — a weight, a colour — is not read); `f` — a face,
its corners as `v`, `v/vt`, `v/vt/vn` or `v//vn`: a positive number counts the vertices of the file from 1, a
negative one — back from the last vertex before the face. A face of three corners is a triangle as it is, a
polygon — a fan from its first corner (corners that repeat the one before are left out). `o name` and `g name`
start a wireframe; a name met again goes on with its wireframe; faces before any of them are a wireframe named
after the file. A wireframe gets the vertices its faces use, in the order of the file, welded exactly.
`# color #rrggbb` after the name (as `write_obj` writes it) is the colour. Textures, normals and materials are
not read; lines and points (`l`, `p`) are counted in a warning. A face on a vertex that is not in the file or
has no coordinate (never a 0 instead), or of fewer than three different corners, is left out and counted.
The file is read once, in blocks (`mesh_io.TextBlocks`); memory is the vertices and the triangles of the file.

    inspect_obj(path)   (entries, warnings): the wireframes with their triangles, without the geometry
    read_obj(path)      the wireframes one by one (`keys` — only these)
    write_obj(path, meshes)

Writing: UTF-8, `o name` for every wireframe, its colour, `v` with `repr` (the same float64 comes back bit for
bit), `f` on the numbers of the vertices of the file. A wireframe read from an OBJ comes back as it was:
vertices, triangles and their order. Attributes are not written (a warning).
Error codes: empty_file, not_obj, no_meshes, too_large.

Ported from BF `formats/wireframes.py::read_obj`, `export.py::write_obj`. Differences: blocks of tokens instead
of a Python loop over lines, numbers by Arrow, exact welding, groups in the order of the file, relative numbers
by the place of the face, names in cp1251 when they are not UTF-8, a composition without geometry, limits.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from bf_core.formats.dxf_layers import ranges
from bf_core.formats.mesh_io import (
    MAX_TRIANGLES,
    MeshWritten,
    TextBlocks,
    Tokens,
    chunks,
    fail,
    lost_attrs,
    no_meshes,
    numbered,
    part_files,
    tidy,
    too_many,
    unique_name,
    word,
)  # fmt: skip
from bf_core.formats.meshset import MeshEntry, MeshSet, clean_name, n_text, name_in_file, source_of
from bf_core.formats.str_layers import scaled
from bf_core.formats.table import Progress, no_progress

FORMAT = "obj"
CHUNK = 100_000  # vertices or triangles formatted at once
W_V, W_F, W_O, W_G, W_HASH, W_L, W_P, W_COLOR = (word(w) for w in (b"v", b"f", b"o", b"g", b"#", b"l", b"p", b"color"))
_COLOR = re.compile(rb"#[0-9a-fA-F]{6}")
_HINT = "Сохраните каркас в исходной программе как Wavefront OBJ (текстовый) и загрузите заново."


@dataclass
class _Group:
    raw: str | None  # the name as the file has it; None — the faces before any `o` / `g`
    color: str | None = None
    triangles: int = 0
    bad: int = 0  # faces left out


class _Scan:
    """One pass over an OBJ file: the vertices, the triangles and the wireframe of every triangle. Without
    `geometry` only the wireframes and the numbers of their triangles are found."""

    def __init__(self, path: Path, tick: Progress, geometry: bool) -> None:
        self.geometry = geometry
        self.groups: list[_Group] = []
        self.index: dict[str | None, int] = {}
        self.current = -1  # the wireframe faces go to: −1 — none was named yet
        self.verts: list[np.ndarray] = []
        self.tris: list[np.ndarray] = []
        self.owners: list[np.ndarray] = []
        self.vertices = self.triangles = self.others = self.undecoded = 0
        size = path.stat().st_size
        if size == 0:
            raise fail("empty_file", "Файл OBJ пуст.", "Проверьте, что файл скопирован полностью.")
        with path.open("rb") as f:
            head = f.read(4096)
        if b"\x00" in head:
            raise fail("not_obj", "Файл не похож на OBJ: это не текстовый файл.", _HINT)
        skip = 3 if head.startswith(b"\xef\xbb\xbf") else 0
        for block in TextBlocks(path, tick, message="Чтение OBJ", skip=skip):
            self._feed(block)

    def _group(self, raw: str | None) -> int:
        found = self.index.get(raw)
        if found is None:
            found = self.index[raw] = len(self.groups)
            self.groups.append(_Group(raw))
        return found

    def _name(self, raw: bytes) -> str:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            self.undecoded += 1
            return raw.decode("cp1251", errors="replace")

    def _feed(self, b: Tokens) -> None:
        cnt, first = b.counts(), b.first[:-1]
        has = np.flatnonzero(cnt > 0)
        key = np.zeros(b.lines, dtype=np.uint64)
        key[has] = b.words(first[has])
        is_v = key == W_V
        self.others += int(np.count_nonzero((key == W_L) | (key == W_P)))
        faces = np.flatnonzero(key == W_F)
        named = np.flatnonzero((key == W_O) | (key == W_G))
        if self.current < 0 and len(faces) and (len(named) == 0 or faces[0] < named[0]):
            self.current = self._group(None)  # faces before any name: the wireframe of the file
        # the names of wireframes and their colours, in the order of the lines
        tinted = np.flatnonzero((key == W_HASH) & (cnt == 3))
        tinted = tinted[b.words(first[tinted] + 1) == W_COLOR]
        group_of = [self.current]  # the wireframe after 0, 1, 2… names of the block
        for line in np.sort(np.concatenate([named, tinted])).tolist():
            a, n = int(first[line]), int(cnt[line])
            if key[line] == W_HASH:
                value = b.text(a + 2, a + 2)
                if group_of[-1] >= 0 and _COLOR.fullmatch(value):
                    self.groups[group_of[-1]].color = value.decode("ascii").lower()
            else:
                group_of.append(self._group(self._name(b.text(a + 1, a + n - 1)) if n > 1 else None))
        self.current = group_of[-1]
        if len(faces):
            group = np.asarray(group_of, dtype=np.int64)[np.searchsorted(named, faces)]
            corners = cnt[faces] - 1
            if self.geometry:
                made = self._faces(b, first[faces], corners, self.vertices + np.cumsum(is_v)[faces])
                self.owners.append(np.repeat(group, made).astype(np.int32))
            else:
                made = np.maximum(corners - 2, 0)
            self.triangles += int(made.sum())
            if self.triangles > MAX_TRIANGLES:
                raise too_many()
            found = np.bincount(group, weights=made, minlength=len(self.groups))
            lost = np.bincount(group[made == 0], minlength=len(self.groups))
            for g in np.flatnonzero((found > 0) | (lost > 0)).tolist():
                self.groups[g].triangles += int(found[g])
                self.groups[g].bad += int(lost[g])
        if self.geometry:
            lines = np.flatnonzero(is_v)
            xyz = np.full((len(lines), 3), np.nan)
            full = cnt[lines] >= 4
            xyz[full] = b.floats((first[lines[full]][:, None] + np.arange(1, 4)).reshape(-1)).reshape(-1, 3)
            self.verts.append(xyz)
        self.vertices += int(np.count_nonzero(is_v))

    def _faces(self, b: Tokens, first: np.ndarray, corners: np.ndarray, before: np.ndarray) -> np.ndarray:
        """The triangles of the faces of a block (kept); returns the number of triangles of every face.
        `first` — the first token of every face line, `before` — the vertices of the file before it."""
        from bf_core.formats import _meshkernel as k

        raw = b.ints(ranges(first + 1, corners), slash=True)
        given = raw != k.MISSING
        number = np.where(raw > 0, raw - 1, np.repeat(before, corners) + np.where(given, raw, 0))
        number = np.where(given & (raw != 0) & (number >= 0) & (number < 2**31 - 1), number, -1)
        tri = np.empty((int(np.maximum(corners - 2, 0).sum()), 3), dtype=np.int64)
        owner = np.empty(len(tri), dtype=np.int64)
        count, _ = k.fan(np.ascontiguousarray(number), np.ascontiguousarray(corners, dtype=np.int64), tri, owner)
        self.tris.append(tri[:count].astype(np.int32))
        return np.bincount(owner[:count], minlength=len(corners))

    def notes(self) -> list[str]:
        out: list[str] = []
        if self.others:
            out.append(f"Линии и точки OBJ (строки «l» и «p») не загружаются: {n_text(self.others)}")
        if self.undecoded:
            out.append("Названия каркасов записаны не в UTF-8: прочитаны как Windows-1251")
        return out


def _open(path: Path, tick: Progress, geometry: bool) -> _Scan:
    scan = _Scan(path, tick, geometry)
    if scan.triangles == 0:
        raise fail("no_meshes", "В файле OBJ нет граней (строк «f») из трёх и более вершин: каркасов в нём нет.", _HINT)
    return scan


def _title(group: _Group, path: Path, name: str | None) -> str:
    return clean_name(group.raw if group.raw is not None else (name or path.stem), "OBJ")


def inspect_obj(
    path: str | Path, progress: Progress | None = None, *, name: str | None = None
) -> tuple[list[MeshEntry], list[str]]:
    """The composition of an OBJ file without its geometry: (entries, warnings). The vertices of a wireframe
    are not counted (None): they are known once its faces are read."""
    path = Path(path)
    scan = _open(path, progress or no_progress, False)
    entries = [
        MeshEntry(i, _title(g, path, name), None, g.triangles, g.color)
        for i, g in enumerate(scan.groups)
        if g.triangles
    ]
    return entries, scan.notes()


def read_obj(
    path: str | Path,
    keys: Iterable[int | str] | None = None,
    progress: Progress | None = None,
    *,
    name: str | None = None,
) -> Iterator[MeshSet]:
    """The wireframes of an OBJ file one by one (`keys` — only these: `MeshEntry.key` of `inspect_obj`).
    `name` — the name of the wireframe of faces that have none (by default the name of the file). The
    warnings of the file come with the first wireframe. `progress(fraction, message)` may raise to cancel."""
    path = Path(path)
    tick = progress or no_progress
    scan = _open(path, scaled(tick, 0.0, 0.7), True)
    xyz = np.concatenate(scan.verts) if scan.verts else np.empty((0, 3))
    tri = np.concatenate(scan.tris) if scan.tris else np.empty((0, 3), dtype=np.int32)
    owner = np.concatenate(scan.owners) if scan.owners else np.empty(0, dtype=np.int32)
    scan.verts, scan.tris, scan.owners = [], [], []
    several = len(scan.groups) > 1
    if several:  # the triangles of every wireframe together, in their order
        order = np.argsort(owner, kind="stable")
        bounds = np.searchsorted(owner[order], np.arange(len(scan.groups) + 1))
    wanted = None if keys is None else set(keys)
    todo = [i for i, g in enumerate(scan.groups) if g.triangles and (wanted is None or i in wanted)]
    notes = scan.notes()
    done, total, read = 0, max(sum(scan.groups[i].triangles for i in todo), 1), 0
    for i in todo:
        group = scan.groups[i]
        mine = tri[order[bounds[i] : bounds[i + 1]]] if several else tri
        share = scaled(tick, 0.7 + 0.3 * done / total, 0.7 + 0.3 * (done + len(mine)) / total)
        done += len(mine)
        title = _title(group, path, name)
        warnings, notes = notes, []
        beyond = (mine >= len(xyz)).any(axis=1)
        lost = group.bad + int(beyond.sum())
        if lost:
            warnings.append(
                f"Граней с номерами вне списка вершин или меньше чем с тремя разными вершинами: "
                f"{n_text(lost)} — пропущены"
            )
        mine = mine[~beyond] if lost else mine
        used = xyz
        if several:  # only the vertices of this wireframe: the work is its size, not that of the file
            ids, inverse = np.unique(mine, return_inverse=True)
            used, mine = xyz[ids], inverse.reshape(-1, 3)
        v, t = tidy(used, mine, warnings, share)
        if len(t) == 0:
            notes = [w for w in warnings if w in scan.notes()]  # those of the file go on to the next wireframe
            continue
        read += 1
        yield MeshSet(title, v, t, color=group.color, source=source_of(FORMAT, group.raw, title), warnings=warnings)
    if todo and not read:
        raise fail("no_meshes", "В файле OBJ нет ни одной грани, которую можно прочитать.", _HINT)
    tick(1.0, f"Прочитано каркасов: {n_text(read)}")


def write_obj(
    path: str | Path, meshes: Iterable[MeshSet], progress: Progress | None = None, *, total: int | None = None
) -> MeshWritten:
    """Write wireframes as a Wavefront OBJ (see the module docstring). `meshes` may be a generator — one
    wireframe is in memory at a time; `total` — their number, for the progress."""
    path = Path(path)
    tick = progress or no_progress
    tick(0.0, "Запись OBJ")
    taken: set[str] = set()
    count = points = triangles = with_attrs = base = 0
    renamed: list[str] = []
    with part_files(path) as (out,):
        out.write(b"# BedrockForge 2\n")
        for _, mesh, share in numbered(meshes, total, tick):
            wanted = " ".join((name_in_file(mesh) or mesh.name).split()) or mesh.name
            title = unique_name(wanted, taken)
            if title != wanted:
                renamed.append(f"«{wanted}» → «{title}»")
            out.write(f"o {title}\n".encode())
            if mesh.color:
                out.write(f"# color {mesh.color}\n".encode("ascii"))
            n, m = mesh.point_count, mesh.triangle_count
            for a, b in chunks(n, CHUNK):
                out.write((b"v %a %a %a\n" * (b - a)) % tuple(mesh.xyz[a:b].reshape(-1).tolist()))
                share(0.5 * b / n, f"Запись OBJ: «{mesh.name}», вершин {n_text(b)} из {n_text(n)}")
            for a, b in chunks(m, CHUNK):
                part = mesh.tri[a:b].astype(np.int64) + (base + 1)
                out.write((b"f %d %d %d\n" * (b - a)) % tuple(part.reshape(-1).tolist()))
                share(0.5 + 0.5 * b / m, f"Запись OBJ: «{mesh.name}», треугольников {n_text(b)} из {n_text(m)}")
            base, count, points, triangles = base + n, count + 1, points + n, triangles + m
            with_attrs += bool(mesh.attrs)
        if not count:
            raise no_meshes("OBJ")
    warnings = lost_attrs(with_attrs, "OBJ")
    if renamed:
        shown = ", ".join(renamed[:5]) + (f" и ещё {n_text(len(renamed) - 5)}" if len(renamed) > 5 else "")
        warnings.append(f"Каркасы с одинаковыми названиями записаны под разными: {shown}")
    tick(1.0, f"Записано каркасов: {n_text(count)}")
    return MeshWritten(path, count, points, triangles, path.stat().st_size, warnings, [path])


__all__ = ["inspect_obj", "read_obj", "write_obj"]
