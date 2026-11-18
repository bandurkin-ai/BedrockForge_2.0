"""STL — binary and text — reading and writing wireframes (ТЗ 7.21, 9.12; ADR 0013 §2).

An STL is a soup of triangles: every triangle carries its three corners. The corners are welded exactly
(`meshset.ExactWelder`) in their order, so the vertices are numbered as the triangles use them.

Binary: 80 bytes of a header, the number of triangles (uint32), then 50 bytes a triangle — the normal and three
corners as float32, two bytes of attributes. A file is binary when its size is 84 + 50 · n, whatever its header
says (many binary files begin with the word `solid`). A float32 becomes the float64 of the same value: nothing
is rounded here, but the program that wrote the file did round — `source["precision"]` is "float32", and a
warning tells the step of the coordinates when they are beyond 100 000 (UTM). The header is not a name (it holds
the mark of the program that wrote the file) unless this writer wrote it; it is kept in `source["header"]`.
Text: `solid name`, facets with three `vertex x y z` lines each, `endsolid`; several solids are several
wireframes. A number is read as the float64 `float()` gives, so a file written with `repr` keeps float64.
A triangle with a coordinate that is not a number is left out and counted; normals are not read.

    inspect_stl(path)   (entries, warnings): names and numbers of triangles, without the geometry
    read_stl(path)      the wireframes one by one (a text file gives every solid as soon as it ends)
    write_stl(path, meshes, binary=True)

Writing. Binary — one solid: several wireframes become one (a warning); coordinates are rounded to float32,
and a warning tells the largest change. Text — a solid per wireframe, coordinates with `repr`. Normals are
computed (zero for a triangle without an area). A wireframe read from an STL comes back as it was.
Error codes: empty_file, not_stl, truncated, broken, no_meshes, too_large, unrepresentable.

Ported from BF `formats/wireframes.py::read_stl` (no writer in BF). Differences: chunks instead of the whole
file, exact welding, solids of a text file, a cut file is an error, the precision is told, limits.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import numpy as np

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
    too_many,
    word,
)
from bf_core.formats.meshset import ExactWelder, MeshEntry, MeshSet, clean_name, n_text, name_in_file, source_of
from bf_core.formats.table import Progress, no_progress

FORMAT = "stl"
HEADER_BYTES = 80
RECORD = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])  # 50 bytes
CHUNK = 1 << 17  # triangles read or written at once
MARK = b"BedrockForge 2 STL: "  # the header this writer makes: the name of the wireframe follows
UTM = 1e5  # coordinates beyond this: the step of float32 is told
W_SOLID, W_ENDSOLID, W_FACET, W_VERTEX = (word(w) for w in (b"solid", b"endsolid", b"facet", b"vertex"))
_HINT = "Сохраните каркас в исходной программе как STL заново и повторите загрузку."


def _step(largest: float) -> str:
    """The distance between two neighbouring float32 at a coordinate, as a text in metres."""
    step = float(np.spacing(np.float32(largest)))
    return (f"{step:.3f}" if step >= 0.001 else f"{step:.1e}").replace(".", ",")


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace")


def _kind(path: Path) -> tuple[bool, int, bytes]:
    """(the file is binary, its number of triangles, its header). Error codes: empty_file, not_stl, truncated."""
    size = path.stat().st_size
    if size == 0:
        raise fail("empty_file", "Файл STL пуст.", "Проверьте, что файл скопирован полностью.")
    with path.open("rb") as f:
        head = f.read(1 << 16)
    count = int.from_bytes(head[80:84], "little") if len(head) >= 84 else -1
    if count >= 0 and HEADER_BYTES + 4 + RECORD.itemsize * count == size:
        return True, count, head[:HEADER_BYTES]
    if b"\x00" not in head and head.lstrip(b"\xef\xbb\xbf \t\r\n")[:5].lower() == b"solid":
        return False, 0, b""
    fits = (size - HEADER_BYTES - 4) // RECORD.itemsize
    if count < 0 or b"\x00" not in head:  # too short for a binary file, or a text that is not an STL
        raise fail("not_stl", "Файл не похож на STL — ни на двоичный, ни на текстовый.", _HINT)
    if fits < count:
        raise fail(
            "truncated",
            f"Файл STL обрезан: в заголовке {n_text(count)} треугольников, в файле помещается {n_text(fits)}.",
            "Скопируйте файл заново: копирование или загрузка прервались.",
        )
    raise fail(
        "broken",
        f"Файл STL повреждён: в заголовке {n_text(count)} треугольников, а размер файла — на {n_text(fits)}.",
        _HINT,
    )


class _Solid:
    """The triangles of a wireframe as they come; a triangle on a coordinate that is not a number is left out."""

    def __init__(self) -> None:
        self.welder = ExactWelder()
        self.tris: list[np.ndarray] = []
        self.bad = 0
        self.largest = 0.0

    def add(self, corners: np.ndarray) -> None:
        """`corners` — float64 (3 · k)×3."""
        fine = np.isfinite(corners).all(axis=1).reshape(-1, 3).all(axis=1)
        if not fine.all():
            self.bad += int((~fine).sum())
            corners = corners[np.repeat(fine, 3)]
        if len(corners):
            self.largest = max(self.largest, float(np.abs(corners).max()))
            self.tris.append(self.welder.add(corners).reshape(-1, 3))

    def mesh(self, name: str, source: dict[str, Any], warnings: list[str]) -> MeshSet | None:
        if self.bad:
            warnings.append(f"Треугольников с координатами, которые не являются числами: {n_text(self.bad)} — пропущены")
        if not self.tris:
            return None
        tri = self.tris[0] if len(self.tris) == 1 else np.concatenate(self.tris)
        self.tris = []
        return MeshSet(name, self.welder.vertices(), tri, source=source, warnings=warnings)


def _binary(path: Path, count: int, header: bytes, tick: Progress, name: str | None) -> Iterator[MeshSet]:
    if count > MAX_TRIANGLES:
        raise too_many()
    solid = _Solid()
    with path.open("rb") as f:
        f.seek(HEADER_BYTES + 4)
        for a, b in chunks(count, CHUNK):
            data = np.fromfile(f, dtype=RECORD, count=b - a)
            solid.add(data["v"].reshape(-1, 3).astype(np.float64))
            tick(b / count, f"Чтение STL: треугольников {n_text(b)} из {n_text(count)}")
    text = _decode(header.split(b"\x00")[0]).strip()
    raw = text[len(MARK) :] if header.startswith(MARK) else None
    title = clean_name(raw if raw else (name or path.stem), "STL")
    source = source_of(FORMAT, raw, title, precision="float32", **({"header": text} if text and raw is None else {}))
    warnings: list[str] = []
    if solid.largest > UTM:
        warnings.append(
            "Координаты STL записаны в формате float32: при координатах больше 100 000 их шаг достигает "
            f"{_step(solid.largest)} м — точность ниже, чем у исходной модели"
        )
    mesh = solid.mesh(title, source, warnings)
    if mesh is None:
        raise fail("no_meshes", "В файле STL нет ни одного треугольника.", _HINT)
    yield mesh


class _Text:
    """The solids of a text STL, block by block. Without `geometry` the facets are only counted."""

    def __init__(self, path: Path, tick: Progress, name: str | None, geometry: bool) -> None:
        self.path, self.tick, self.fallback, self.geometry = path, tick, name, geometry
        self.raw: str | None = None  # the name of the solid that is open
        self.solid: _Solid | None = None
        self.rest = np.empty((0, 3))  # corners of a triangle the block ended inside
        self.facets = self.vertices = self.number = self.total = 0

    def _close(self, line: int | None) -> tuple[int, str, str | None, _Solid | None, int] | None:
        """The solid that is open ends (at a line, or with the file — then it is cut)."""
        if self.solid is None:
            return None
        if line is None or self.vertices != 3 * self.facets:
            cut = line is None
            raise fail(
                "truncated" if cut else "broken",
                "Файл STL обрезан: нет завершающей строки «endsolid»."
                if cut
                else f"Файл STL повреждён: в теле «{self.raw or ''}» граней {n_text(self.facets)}, "
                f"а вершин {n_text(self.vertices)} (должно быть по три на грань).",
                "Скопируйте файл заново: копирование или загрузка прервались." if cut else _HINT,
            )
        title = clean_name(self.raw if self.raw else (self.fallback or self.path.stem), "STL")
        found = (self.number, title, self.raw or None, self.solid, self.facets)
        self.solid, self.number = None, self.number + 1
        return found

    def __iter__(self) -> Iterator[tuple[int, str, str | None, _Solid | None, int]]:
        """(number, name, name as in the file, the triangles, facets) of every solid as it ends."""
        with self.path.open("rb") as f:
            skip = 3 if f.read(3) == b"\xef\xbb\xbf" else 0
        for b in TextBlocks(self.path, self.tick, message="Чтение STL", skip=skip):
            cnt, first = b.counts(), b.first[:-1]
            has = np.flatnonzero(cnt > 0)
            key = np.zeros(b.lines, dtype=np.uint64)
            key[has] = b.words(first[has])
            vertex, facet = np.flatnonzero(key == W_VERTEX), np.flatnonzero(key == W_FACET)
            marks = np.flatnonzero((key == W_SOLID) | (key == W_ENDSOLID))
            lo = 0
            for line in [*marks.tolist(), b.lines]:
                self._take(b, cnt, first, vertex, facet, lo, line)
                lo = line
                if line < b.lines:
                    found = self._close(line)
                    if found is not None:
                        yield found
                    if key[line] == W_SOLID:
                        a, n = int(first[line]), int(cnt[line])
                        self.raw = _decode(b.text(a + 1, a + n - 1)) if n > 1 else None
                        self.solid, self.facets, self.vertices, self.rest = _Solid(), 0, 0, np.empty((0, 3))
        found = self._close(None)
        if found is not None:
            yield found

    def _take(
        self, b: Tokens, cnt: np.ndarray, first: np.ndarray, vertex: np.ndarray, facet: np.ndarray, lo: int, hi: int
    ) -> None:
        """The facets of the lines [lo, hi) of a block go to the solid that is open."""
        lines = vertex[np.searchsorted(vertex, lo) : np.searchsorted(vertex, hi)]
        if self.solid is None or (len(lines) == 0 and lo == hi):
            return
        self.facets += int(np.searchsorted(facet, hi) - np.searchsorted(facet, lo))
        self.vertices += len(lines)
        self.total += len(lines) // 3
        if self.total > MAX_TRIANGLES:
            raise too_many()
        if self.geometry and len(lines):
            xyz = np.full((len(lines), 3), np.nan)
            full = cnt[lines] >= 4
            xyz[full] = b.floats((first[lines[full]][:, None] + np.arange(1, 4)).reshape(-1)).reshape(-1, 3)
            xyz = np.concatenate([self.rest, xyz]) if len(self.rest) else xyz
            whole = len(xyz) - len(xyz) % 3
            self.rest = xyz[whole:]
            self.solid.add(xyz[:whole])


def inspect_stl(
    path: str | Path, progress: Progress | None = None, *, name: str | None = None
) -> tuple[list[MeshEntry], list[str]]:
    """The composition of an STL file without its geometry: (entries, warnings). The vertices are not counted
    (None): an STL has none of its own, they are what welding its corners gives."""
    path = Path(path)
    tick = progress or no_progress
    binary, count, header = _kind(path)
    if binary:
        raw = _decode(header.split(b"\x00")[0]).strip()[len(MARK) :] if header.startswith(MARK) else None
        entries = [MeshEntry(0, clean_name(raw if raw else (name or path.stem), "STL"), None, count)] * bool(count)
    else:
        entries = [MeshEntry(k, title, None, n) for k, title, _, _, n in _Text(path, tick, name, False) if n]
    if not entries:
        raise fail("no_meshes", "В файле STL нет ни одного треугольника.", _HINT)
    tick(1.0, None)
    return entries, []


def read_stl(
    path: str | Path,
    keys: Iterable[int | str] | None = None,
    progress: Progress | None = None,
    *,
    name: str | None = None,
) -> Iterator[MeshSet]:
    """The wireframes of an STL file one by one (`keys` — only these: `MeshEntry.key` of `inspect_stl`).
    `name` — the name of a wireframe the file gives none (by default the name of the file).
    `progress(fraction, message)` may raise to cancel."""
    path = Path(path)
    tick = progress or no_progress
    wanted = None if keys is None else set(keys)
    binary, count, header = _kind(path)
    read = 0
    if binary:
        if wanted is None or 0 in wanted:
            yield from _binary(path, count, header, tick, name)
            read = 1
    else:
        for number, title, raw, solid, _ in _Text(path, tick, name, True):
            mesh = solid.mesh(title, source_of(FORMAT, raw, title), []) if solid is not None else None
            if mesh is not None and (wanted is None or number in wanted):
                read += 1
                yield mesh
        if not read and wanted is None:
            raise fail("no_meshes", "В файле STL нет ни одного треугольника.", _HINT)
    tick(1.0, f"Прочитано каркасов: {n_text(read)}")


# --- writing -------------------------------------------------------------------------------------------------


def _normals(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Unit normals of triangles; zero for a triangle without an area."""
    with np.errstate(all="ignore"):
        n = np.cross(b - a, c - a)
        length = np.sqrt(np.einsum("ij,ij->i", n, n))
        return np.where(((length > 0) & np.isfinite(length))[:, None], n / length[:, None], 0.0)


def _header(title: str) -> tuple[bytes, bool]:
    """(80 bytes: the mark and the name in UTF-8, cut at a whole character; the name was cut)."""
    room = HEADER_BYTES - len(MARK)
    raw = title.encode("utf-8")
    kept = raw[:room].decode("utf-8", errors="ignore").encode("utf-8")
    return (MARK + kept).ljust(HEADER_BYTES, b"\x00"), len(kept) < len(raw)


def write_stl(
    path: str | Path,
    meshes: Iterable[MeshSet],
    progress: Progress | None = None,
    *,
    total: int | None = None,
    binary: bool = True,
) -> MeshWritten:
    """Write wireframes as an STL, binary or text (see the module docstring). `meshes` may be a generator —
    one wireframe is in memory at a time; `total` — their number, for the progress."""
    path = Path(path)
    tick = progress or no_progress
    tick(0.0, "Запись STL")
    count = points = triangles = with_attrs = rounded = 0
    worst = largest = 0.0
    warnings: list[str] = []
    template = (
        b"facet normal %a %a %a\n outer loop\n  vertex %a %a %a\n  vertex %a %a %a\n  vertex %a %a %a\n"
        b" endloop\nendfacet\n"
    )
    with part_files(path) as (out,):
        for i, mesh, share in numbered(meshes, total, tick):
            title = " ".join((name_in_file(mesh) or mesh.name).split()) or mesh.name
            if binary and i == 0:
                header, cut = _header(title)
                out.write(header + b"\x00\x00\x00\x00")
                if cut:
                    warnings.append(f"Название «{title}» не помещается в заголовок STL: сокращено")
            elif not binary:
                out.write(f"solid {title}\n".encode())
            m = mesh.triangle_count
            for a, b in chunks(m, CHUNK):
                corners = mesh.xyz[mesh.tri[a:b]]  # triangles × 3 × 3
                normal = _normals(corners[:, 0], corners[:, 1], corners[:, 2])
                if binary:
                    data = np.zeros(b - a, dtype=RECORD)
                    with np.errstate(over="ignore"):
                        data["n"], data["v"] = normal, corners
                    if not np.isfinite(data["v"]).all():
                        raise fail(
                            "unrepresentable",
                            f"Координаты каркаса «{mesh.name}» слишком велики для двоичного STL (float32).",
                            "Выберите текстовый STL или другой формат.",
                        )
                    change = np.abs(data["v"].astype(np.float64) - corners)
                    rounded += int(np.count_nonzero(change))
                    worst, largest = max(worst, float(change.max())), max(largest, float(np.abs(corners).max()))
                    out.write(data.tobytes())
                else:
                    rows = np.concatenate([normal, corners.reshape(-1, 9)], axis=1)
                    out.write((template * (b - a)) % tuple(rows.reshape(-1).tolist()))
                share(b / m, f"Запись STL: «{mesh.name}», треугольников {n_text(b)} из {n_text(m)}")
            if not binary:
                out.write(f"endsolid {title}\n".encode())
            count, points, triangles = count + 1, points + mesh.point_count, triangles + m
            with_attrs += bool(mesh.attrs)
        if not count:
            raise no_meshes("STL")
        if binary:
            if triangles >= 2**32:
                raise too_many(2**32 - 1)
            out.seek(HEADER_BYTES)
            out.write(triangles.to_bytes(4, "little"))
    warnings += lost_attrs(with_attrs, "STL")
    if binary and count > 1:
        warnings.append(f"Двоичный STL хранит одно тело: каркасы ({n_text(count)}) записаны как один")
    if rounded:
        text = f"{worst:.3f}" if worst >= 0.0005 else f"{worst:.1e}"
        note = f"; шаг координат — до {_step(largest)} м" if largest > UTM else ""
        warnings.append(
            f"Двоичный STL хранит координаты в формате float32: они округлены, наибольшее изменение — "
            f"{text.replace('.', ',')} м{note}. Без потери точности каркас записывают текстовый STL и другие форматы"
        )
    tick(1.0, f"Записано каркасов: {n_text(count)}")
    return MeshWritten(path, count, points, triangles, path.stat().st_size, warnings, [path])
