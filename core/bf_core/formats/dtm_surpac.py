"""Surpac DTM (text) with its string file — reading and writing wireframes (ТЗ 9.12; ADR 0013 §2).

A surface or a solid of Surpac is a pair of files: `name.str` — the points (a Surpac string file, `str_surpac`)
and `name.dtm` — the triangles on them:

    name.str,<anything>                                         the string file of the pair
    0, 0.000, 0.000, 0.000, END
    OBJECT, 1,
    TRISOLATION, 1, neighbours=no,validated=true,closed=yes
    1, 5, 2, 1, 0, 0, 0,                                        a triangle: its number, three points, neighbours
    …
    END

A point is named by its number: the records of the string file that have a string number (not the `0` records
that end a segment) counted from 1 in the order of the file — `LineSet.seq` + 1 of the Э7 reader. Every
TRISOLATION — a part of an OBJECT that stands alone — is a wireframe: named after the file when it is the only
one, else «file: объект N» («…, часть M» when the object has several). It gets the points its triangles use,
in the order of the string file, welded exactly. A triangle on a point that is not in the string file or has
no coordinate (never a 0 instead) is left out and counted; the neighbours are not read.

No sample of the format was at hand (ADR 0013, «Риски»): the reader follows the description of the format and
is lenient — any text in the first two lines, triangles before the first TRISOLATION, lines it cannot read are
counted in a warning. A file without the END line is cut short: an error.

    inspect_dtm(path)       (entries, warnings): the wireframes with their triangles; the points are not read
    read_dtm(path)          the wireframes one by one; `strings` — the string file when it is not next to the DTM
    write_dtm(path, meshes) both files: `path` and the string file of the same name (or `strings`)

Writing: an OBJECT with one TRISOLATION for every wireframe (the numbers a wireframe read from a DTM had, else
the next free object), its vertices as points of the string of that number — coordinates with `repr`, so they
come back bit for bit, — its triangles in their order; `closed` tells whether every edge has two triangles. A
wireframe read from a pair comes back as it was. Names, colours and attributes have no place in the pair
(a warning). Error codes: empty_file, not_dtm, unsupported, no_strings, truncated, no_meshes, too_large and
those of `str_surpac.read_surpac`.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from bf_core.formats.mesh_io import (
    MAX_TRIANGLES,
    MeshWritten,
    TextBlocks,
    chunks,
    fail,
    lost_attrs,
    no_meshes,
    numbered,
    part_files,
    tidy,
    too_many,
    word,
)
from bf_core.formats.meshset import MeshEntry, MeshSet, clean_name, n_text, name_in_file, source_of
from bf_core.formats.str_layers import scaled
from bf_core.formats.str_surpac import _MONTHS, AXIS_LINE, DEFAULT_ENCODING, END_LINE, SurpacStyle
from bf_core.formats.table import Progress, no_progress

FORMAT = "surpac_dtm"
CHUNK = 50_000  # points or triangles formatted at once
MAX_STRING = 32000  # the largest string number of Surpac
W_OBJECT, W_TRISOLATION, W_END = word(b"OBJECT"), word(b"TRISOLATION"), word(b"END")
_HINT = "Сохраните каркас в Surpac как текстовые файлы .dtm и .str и загрузите оба заново."


@dataclass
class _Part:
    object: int
    trisolation: int
    tris: list[np.ndarray] = field(default_factory=list)
    triangles: int = 0


class _Dtm:
    """One pass over a DTM file: its parts with their triangles (point numbers from 0). Without `geometry`
    the triangles are only counted."""

    def __init__(self, path: Path, tick: Progress, geometry: bool) -> None:
        self.parts: list[_Part] = []
        self.unread = 0
        if path.stat().st_size == 0:
            raise fail("empty_file", "Файл DTM пуст.", "Проверьте, что файл скопирован полностью.")
        with path.open("rb") as f:
            head = f.read(4096)
        if b"\x00" in head:
            raise fail("unsupported", "Двоичный файл Surpac DTM не поддерживается.", _HINT)
        skip = 3 if head.startswith(b"\xef\xbb\xbf") else 0
        first_line = head[skip:].split(b"\n")[0].strip()
        self.header = first_line.decode(DEFAULT_ENCODING, errors="replace")
        current: _Part | None = None
        number, ended, total = 1, False, 0
        for b in TextBlocks(path, tick, comma=True, message="Чтение DTM", skip=skip):
            cnt, first = b.counts(), b.first[:-1]
            has = np.flatnonzero(cnt > 0)
            key = np.zeros(b.lines, dtype=np.uint64)
            key[has] = b.words(first[has])
            marked = (key == W_OBJECT) | (key == W_TRISOLATION) | (key == W_END)
            marks = np.flatnonzero(marked)
            body = ~marked & (np.arange(b.lines) + b.base >= 2)  # the first two lines are the header
            rows = np.flatnonzero(body & (cnt >= 4))
            ids = b.ints((first[rows][:, None] + np.arange(4)).reshape(-1)).reshape(-1, 4)
            fine = (ids >= 1).all(axis=1)
            blank = ids[:, 0] == 0  # «0, …»: not a triangle
            self.unread += int(np.count_nonzero(~fine & ~blank)) + int(np.count_nonzero(body & (cnt > 0) & (cnt < 4)))
            rows, ids = rows[fine], ids[fine, 1:] - 1
            lo = 0
            for line in [*marks.tolist(), b.lines]:
                mine = slice(int(np.searchsorted(rows, lo)), int(np.searchsorted(rows, line)))
                found = mine.stop - mine.start
                if found:
                    if current is None:  # triangles before any TRISOLATION
                        current = _Part(number, 1)
                        self.parts.append(current)
                    current.triangles += found
                    total += found
                    if total > MAX_TRIANGLES:
                        raise too_many()
                    if geometry:
                        current.tris.append(np.where(ids[mine] < 2**31 - 1, ids[mine], 2**31 - 1).astype(np.int32))
                lo = line
                if line == b.lines:
                    break
                if key[line] == W_END:
                    ended = True
                    break
                value = int(b.ints(first[line : line + 1] + 1)[0]) if cnt[line] > 1 else -1
                if key[line] == W_OBJECT:
                    number = value if value >= 1 else number + 1
                    current = None
                else:
                    current = _Part(number, value if value >= 1 else 1)
                    self.parts.append(current)
            if ended:
                break
        if not ended:
            raise fail(
                "truncated",
                "Файл DTM обрезан: нет завершающей строки «END».",
                "Скопируйте файл заново: копирование или загрузка прервались.",
            )
        self.parts = [p for p in self.parts if p.triangles]
        if not self.parts:
            raise fail("no_meshes", "В файле DTM нет ни одного треугольника.", _HINT)

    def notes(self) -> list[str]:
        text = f"Строк DTM, которые не читаются как треугольники: {n_text(self.unread)} — пропущены"
        return [text] * bool(self.unread)

    def titles(self, path: Path, name: str | None) -> list[str]:
        base = clean_name(name or path.stem, "DTM")
        if len(self.parts) == 1:
            return [base]
        several = {p.object for p in self.parts if sum(q.object == p.object for q in self.parts) > 1}
        return [
            clean_name(f"{base}: объект {p.object}" + (f", часть {p.trisolation}" if p.object in several else ""))
            for p in self.parts
        ]


def strings_of(path: Path, header: str, strings: str | Path | None = None) -> Path:
    """The string file of a DTM: the one given, the one its first line names, the one of its own name."""
    if strings is not None:
        return Path(strings)
    named = re.split(r"[\\/]", header.split(",")[0].strip())[-1]
    wanted = [named] * named.lower().endswith(".str") + [path.stem + ".str"]
    try:
        there = {p.name.casefold(): p for p in path.parent.iterdir()}
    except OSError:
        there = {}
    for name in wanted:
        if name.casefold() in there:
            return there[name.casefold()]
    raise fail(
        "no_strings",
        f"Рядом с файлом DTM нет файла стрингов «{wanted[0]}»: точки каркаса хранятся в нём.",
        "Положите файлы .dtm и .str в одну папку (или выберите оба) и повторите загрузку.",
    )


def inspect_dtm(
    path: str | Path, progress: Progress | None = None, *, strings: str | Path | None = None, name: str | None = None
) -> tuple[list[MeshEntry], list[str]]:
    """The composition of a DTM without its geometry: (entries, warnings). The string file must be there, but
    it is not read: the points of a wireframe are not counted (None)."""
    path = Path(path)
    dtm = _Dtm(path, progress or no_progress, False)
    strings_of(path, dtm.header, strings)
    titles = dtm.titles(path, name)
    entries = [MeshEntry(i, t, None, p.triangles) for i, (t, p) in enumerate(zip(titles, dtm.parts, strict=True))]
    return entries, dtm.notes()


def read_dtm(
    path: str | Path,
    keys: Iterable[int | str] | None = None,
    progress: Progress | None = None,
    *,
    strings: str | Path | None = None,
    name: str | None = None,
) -> Iterator[MeshSet]:
    """The wireframes of a Surpac DTM and its string file one by one (`keys` — only these: `MeshEntry.key` of
    `inspect_dtm`). `name` — the name of the wireframes instead of that of the file. The warnings of both
    files come with the first wireframe. `progress(fraction, message)` may raise to cancel."""
    from bf_core.formats.str_surpac import read_surpac

    path = Path(path)
    tick = progress or no_progress
    dtm = _Dtm(path, scaled(tick, 0.0, 0.3), True)
    source = strings_of(path, dtm.header, strings)
    read = read_surpac(source, scaled(tick, 0.3, 0.7))
    points = np.full((read.records, 3), np.nan)
    for layer in read.layers:
        points[layer.seq] = layer.xyz
    notes = dtm.notes() + [f"Файл стрингов «{source.name}»: {w}" for w in read.warnings]
    del read
    wanted = None if keys is None else set(keys)
    todo = [i for i in range(len(dtm.parts)) if wanted is None or i in wanted]
    total, done, found = max(sum(dtm.parts[i].triangles for i in todo), 1), 0, 0
    titles = dtm.titles(path, name)
    for i in todo:
        part = dtm.parts[i]
        tri = part.tris[0] if len(part.tris) == 1 else np.concatenate(part.tris)
        part.tris = []
        share = scaled(tick, 0.7 + 0.3 * done / total, 0.7 + 0.3 * (done + len(tri)) / total)
        done += len(tri)
        warnings, notes = notes, []
        beyond = (tri >= len(points)).any(axis=1)
        if beyond.any():
            warnings.append(
                f"Треугольников на точках, которых нет в файле стрингов: {n_text(int(beyond.sum()))} — пропущены"
            )
            tri = tri[~beyond]
        ids, inverse = np.unique(tri, return_inverse=True)  # the points of this wireframe, in the order of the file
        v, t = tidy(points[ids], inverse.reshape(-1, 3), warnings, share)
        if len(t) == 0:
            notes = warnings
            continue
        found += 1
        about = {"object": part.object, "trisolation": part.trisolation, "strings": source.name}
        yield MeshSet(titles[i], v, t, source=source_of(FORMAT, None, titles[i], **about), warnings=warnings)
    if todo and not found:
        raise fail("no_meshes", "В файле DTM нет ни одного треугольника на точках файла стрингов.", _HINT)
    tick(1.0, f"Прочитано каркасов: {n_text(found)}")


def write_dtm(
    path: str | Path,
    meshes: Iterable[MeshSet],
    progress: Progress | None = None,
    *,
    total: int | None = None,
    strings: str | Path | None = None,
    date: dt.date | None = None,
) -> MeshWritten:
    """Write wireframes as a Surpac DTM and its string file (see the module docstring). `meshes` may be a
    generator — one wireframe is in memory at a time; `total` — their number, for the progress; `date` — the
    date in the header of the string file (today)."""
    from bf_core.geo.meshes import edge_summary

    path = Path(path)
    pair = Path(strings) if strings is not None else path.with_suffix(".str")
    tick = progress or no_progress
    tick(0.0, "Запись DTM")
    style = SurpacStyle()
    nl = style.newline
    day = date or dt.date.today()
    taken: set[tuple[int, int]] = set()
    count = points = triangles = with_attrs = named = 0
    with part_files(path, pair) as (out, pts):
        out.write(f"{pair.name},{nl}{END_LINE}{nl}".encode(DEFAULT_ENCODING, errors="replace"))
        pts.write(f",{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year % 100:02d},,{nl}{AXIS_LINE}{nl}".encode("ascii"))
        for _, mesh, share in numbered(meshes, total, tick):
            src = mesh.source if mesh.source.get("format") == FORMAT else {}
            was, part = src.get("object"), src.get("trisolation")
            number = (max((o for o, _ in taken), default=0) + 1, 1)  # the next free object
            if isinstance(was, int) and isinstance(part, int) and was >= 1 and part >= 1 and (was, part) not in taken:
                number = (int(was), int(part))  # the numbers the wireframe had in its DTM
            taken.add(number)
            n, m = mesh.point_count, mesh.triangle_count
            string = str(min(number[0], MAX_STRING))
            end = nl + style.null_line + nl
            for a, b in chunks(n, CHUNK):
                x, y, z = (style.coords(mesh.xyz[a:b, k].tolist()) for k in range(3))
                rows = [f"{string},{cy},{cx},{cz}" for cy, cx, cz in zip(y, x, z, strict=True)]
                pts.write((end.join(rows) + end).encode("ascii"))
                share(0.5 * b / n, f"Запись DTM: «{mesh.name}», точек {n_text(b)} из {n_text(n)}")
            closed = mesh.stats.get("closed") if mesh.stats else None
            if closed is None:
                closed = edge_summary(mesh.tri, n) == (0, 0)
            head = f"OBJECT, {number[0]},{nl}TRISOLATION, {number[1]}, neighbours=no,validated=false,"
            out.write(f"{head}closed={'yes' if closed else 'no'}{nl}".encode("ascii"))
            row = f"%d, %d, %d, %d, 0, 0, 0,{nl}".encode("ascii")
            for a, b in chunks(m, CHUNK):
                part = np.column_stack([np.arange(a + 1, b + 1), mesh.tri[a:b].astype(np.int64) + (points + 1)])
                out.write((row * (b - a)) % tuple(part.reshape(-1).tolist()))
                share(0.5 + 0.5 * b / m, f"Запись DTM: «{mesh.name}», треугольников {n_text(b)} из {n_text(m)}")
            count, points, triangles = count + 1, points + n, triangles + m
            with_attrs += bool(mesh.attrs)
            named += name_in_file(mesh) is not None or mesh.color is not None
        if not count:
            raise no_meshes("Surpac DTM")
        out.write(f"END{nl}".encode("ascii"))
        pts.write(f"{END_LINE}{nl}".encode("ascii"))
    warnings = lost_attrs(with_attrs, "Surpac DTM")
    if named:
        warnings.append(
            f"Названия и цвета каркасов в Surpac DTM не записываются ({n_text(named)}): "
            "при загрузке каркасы называются по файлу и номерам объектов"
        )
    tick(1.0, f"Записано каркасов: {n_text(count)}")
    size = path.stat().st_size + pair.stat().st_size
    return MeshWritten(path, count, points, triangles, size, warnings, [path, pair])
