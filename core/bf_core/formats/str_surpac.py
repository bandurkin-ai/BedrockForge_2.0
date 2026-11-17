"""Surpac string files (`.str`, text) — reading and writing (ТЗ 9.6; ADR 0011 §2).

File: line 1 — the header (location, date, purpose, memo); line 2 — the axis record `0, y1, x1, z1, y2, x2, z2`;
then records `number, Y, X, Z, D1, D2, …` (Y, the northing, comes first). A record with number 0 ends a segment;
`0, …, END` ends the file. Code page Windows-1251; UTF-8 with a BOM is recognised.

`read_surpac(path, progress)` → `str_layers.StrRead` of `lineset.LineSet`s: layer = string number (named «Стринг N»),
line = segment (a change of the number without a 0 record also ends one), a closed segment repeats its first vertex.
D1, D2, … are text columns: a line attribute when they do not change along any segment, otherwise per vertex;
an empty description is null. A Z that is not a number stays NaN; a vertex without X or Y is left out (reported).
`header` (`SurpacHeader`) keeps the header, axis and END lines, the bytes after END and the `SurpacStyle` of the
records (separators, alignment, decimals, line ends) learned from the start of the file; every record is checked
against it, and when all match (`header.exact`), `write_surpac(path, layers, like=header)` gives the same bytes.
Without `like` coordinates are written by `repr` (the shortest text of the same double), so write → read →
write is bit for bit; other attributes go to free D fields, commas inside values become «;» with a warning.
Error codes: empty_file, not_surpac, unsupported, truncated, too_large, unrepresentable, no_lines.

Ported from BF `formats/lines.py::read_surpac_str` (no writer in BF). Differences: streaming with progress and
cancellation, segments of one vertex are kept (points), descriptions per vertex, the style and the END record
are kept for writing back, a missing END is an error (BF: silently ignored), unreadable lines are reported.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet
from bf_core.formats.str_layers import StrRead, attribute_names, build_layers, gather, n_text, scaled
from bf_core.formats.table import Column, Progress, no_progress

if TYPE_CHECKING:
    import numpy as np

DEFAULT_ENCODING = "cp1251"
MAX_LINE = 1 << 20  # a record longer than this is not a Surpac record
MAX_DESC = 100  # Surpac has D1…D100
CHUNK_BYTES = 4 << 20
LEARN_LINES = 4096
AXIS_LINE = "0, 0.000, 0.000, 0.000, 0.000, 0.000, 0.000"
NULL_LINE = "0, 0.000, 0.000, 0.000,"
END_LINE = "0, 0.000, 0.000, 0.000, END"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_FIXED = re.compile(r"-?\d+(?:\.(\d*))?")
_CONTROL = re.compile(r"[\x00-\x1f]")
_LAYER = re.compile(r"(?:Стринг\s*)?(\d+)", re.IGNORECASE)
_HINT = "Сохраните стринги в Surpac как текстовый файл .str (String file) и загрузите его заново."


@dataclass(frozen=True)
class SurpacStyle:
    """How the records of a file are written."""

    newline: str = "\r\n"
    number_width: int = 0  # string numbers right-aligned to this width (0 — as they are)
    coord_prefix: str = " "  # text between a comma and a coordinate
    coord_width: int = 0  # coordinates right-aligned to this width
    decimals: int | None = None  # fixed decimals of coordinates; None — repr (the shortest text of the double)
    desc_prefix: str = " "  # text between a comma and a description
    empty_desc: str = ""  # how an empty description is written
    min_desc: int = 0  # description fields written even when all are empty (1 — a comma after Z)
    null_line: str = NULL_LINE  # the record that ends a segment

    def coord(self, v: float) -> str:
        text = "" if v != v else repr(v) if self.decimals is None else f"{v:.{self.decimals}f}"
        return self.coord_prefix + text.rjust(self.coord_width)

    def record(self, number: int, y: float, x: float, z: float, descs: Sequence[str | None]) -> str:
        last = max((i + 1 for i, d in enumerate(descs) if d), default=0)
        count = max(last, self.min_desc)
        parts = [str(number).rjust(self.number_width), self.coord(y), self.coord(x), self.coord(z)]
        for i in range(count):
            d = descs[i] if i < len(descs) else None
            parts.append(self.desc_prefix + d if d else self.empty_desc)
        return ",".join(parts)


@dataclass
class SurpacHeader:
    title: str  # line 1 as in the file
    axis: str  # line 2 as in the file
    end: str = END_LINE  # the END record as in the file
    end_newline: str = "\r\n"  # what follows it ("" at the very end of the file)
    tail: bytes = b""  # the bytes after the END record, kept when short
    style: SurpacStyle = field(default_factory=SurpacStyle)
    encoding: str = DEFAULT_ENCODING
    bom: bool = False
    descriptions: int = 0  # D fields in the file
    exact: bool = True  # every record matches `style`: writing with like=this header gives the same bytes


@dataclass
class SurpacWritten:
    path: Path
    records: int
    lines: int
    size: int
    warnings: list[str] = field(default_factory=list)


def _fail(code: str, message: str, hint: str = _HINT) -> FormatError:
    return FormatError(code, message, hint)


def _integer(token: str) -> int | None:
    text = token.strip()
    if text.isdigit() or (text[:1] == "-" and text[1:].isdigit()):
        return int(text)
    return None


def is_surpac(head: bytes) -> bool:
    """The start of a file looks like a Surpac text string file: text, and line 2 is a `0, …` axis record."""
    if b"\x00" in head[:4096]:
        return False
    lines = head.lstrip(b"\xef\xbb\xbf").split(b"\n", 2)
    return len(lines) >= 2 and lines[1].split(b",", 1)[0].strip() == b"0" and b"," in lines[1]


# ---------------------------------------------------------------- reading


def _learn(lines: Sequence[str], newline: str) -> SurpacStyle:
    """The style of the records from the first lines of the body (each property where it is first seen)."""
    number_width, prefix, width, decimals = 0, " ", 0, None
    desc_prefix: str | None = None
    empty_desc: str | None = None
    min_desc: int | None = None
    null_line: str | None = None
    data_seen = False
    for line in lines:
        tokens = line.split(",")
        number = _integer(tokens[0])
        if number is None or len(tokens) < 4:
            continue
        descs = tokens[4:]
        if number == 0:
            if any(t.strip().upper() == "END" for t in descs):
                break
            null_line = line if null_line is None else null_line
            continue
        if not data_seen:
            data_seen = True
            head, coords = tokens[0], tokens[1:4]
            number_width = len(head) if head != head.lstrip() else 0
            lead = [len(t) - len(t.lstrip(" ")) for t in coords]
            if len({len(t) for t in coords}) == 1 and max(lead) >= 2:
                prefix, width = "", len(coords[0])
            else:
                prefix = " " * lead[0]
            texts = [t.strip() for t in coords]
            fixed = [_FIXED.fullmatch(t) for t in texts]
            if all(fixed):
                places = {len(m.group(1) or "") for m in fixed if m}
                if len(places) == 1 and any(repr(float(t)) != t for t in texts):
                    decimals = places.pop()
        for t in descs:
            if t.strip() and desc_prefix is None:
                desc_prefix = t[: len(t) - len(t.lstrip(" "))]
            if not t.strip() and empty_desc is None:
                empty_desc = t
        if min_desc is None and not any(t.strip() for t in descs):
            min_desc = len(descs)
        if data_seen and None not in (desc_prefix, empty_desc, min_desc, null_line):
            break
    style = SurpacStyle(newline, number_width, prefix, width, decimals)
    return SurpacStyle(
        newline,
        number_width,
        prefix,
        width,
        decimals,
        desc_prefix if desc_prefix is not None else (prefix if width == 0 else " "),
        empty_desc or "",
        min_desc or 0,
        null_line if null_line is not None else style.record(0, 0.0, 0.0, 0.0, []),
    )


class _Body:
    """Records of the body, collected a chunk at a time as numpy / pyarrow arrays."""

    def __init__(self) -> None:
        self.parts: list[tuple[np.ndarray, np.ndarray, np.ndarray, list[list[Any]]]] = []
        self.number: list[int] = []
        self.y: list[float] = []
        self.x: list[float] = []
        self.z: list[float] = []
        self.start: list[bool] = []
        self.descs: list[list[str | None] | None] = []
        self.width = 0  # most descriptions in a record

    def flush(self) -> None:
        import numpy as np
        import pyarrow as pa

        if not self.number:
            return
        k = max((len(d) for d in self.descs if d), default=0)
        cols = [pa.array([d[j] if d and j < len(d) else None for d in self.descs], type=pa.string()) for j in range(k)]
        xyz = np.column_stack([self.x, self.y, self.z]).astype(np.float64)
        self.parts.append((np.asarray(self.number, dtype=np.int64), xyz, np.asarray(self.start, dtype=bool), cols))
        self.width = max(self.width, k)
        self.number, self.y, self.x, self.z, self.start, self.descs = [], [], [], [], [], []


def read_surpac(
    path: str | Path, progress: Progress | None = None, *, encoding: str | None = None, chunk_bytes: int = CHUNK_BYTES
) -> StrRead:
    """Read a Surpac text string file into line layers (see the module docstring)."""
    import numpy as np
    import pyarrow as pa

    path = Path(path)
    tick = progress or no_progress
    size = path.stat().st_size
    if size == 0:
        raise _fail("empty_file", "Файл пуст.", "Проверьте, что файл скопирован полностью.")
    tick(0.0, "Чтение стрингов Surpac")
    warnings: list[str] = []
    body = _Body()
    style: SurpacStyle | None = None
    title = axis = None
    newline = "\r\n"
    end: tuple[str, str] | None = None
    tail = b""
    exact, bom, line_no = True, False, 0
    bad: list[int] = []
    bad_coords = replaced = 0
    current: int | None = None
    open_segment = False
    codec = encoding or DEFAULT_ENCODING
    with path.open("rb") as f:
        head = f.read(4096)
        if head.startswith(b"THIS IS MICROMINE"):
            raise _fail("not_surpac", "Это файл Micromine, а не Surpac.", "Загрузите его как Micromine STR.")
        if head.startswith((b"\xff\xfe", b"\xfe\xff")):
            raise _fail("unsupported", "Файл в кодировке UTF-16 не поддерживается.", "Сохраните его в Windows-1251.")
        if b"\x00" in head:
            raise _fail("unsupported", "Двоичный файл стрингов Surpac пока не поддерживается.", _HINT)
        if head.startswith(b"\xef\xbb\xbf"):
            bom, codec = True, encoding or "utf-8"
        f.seek(3 if bom else 0)
        done = 3 if bom else 0
        rest = b""
        while end is None:
            chunk = f.read(chunk_bytes)
            done += len(chunk)
            data = rest + chunk
            cut = data.rfind(b"\n") + 1 if chunk else len(data)
            if not chunk and not data:
                break
            rest, data = data[cut:], data[:cut]
            if len(rest) > MAX_LINE:
                raise _fail("too_large", f"Строка {line_no + 1} длиннее {n_text(MAX_LINE)} байт.")
            text = data.decode(codec, errors="replace")
            if "�" in text:
                replaced += text.count("�")
            lines = text.split("\n")
            if lines and lines[-1] == "" and data.endswith(b"\n"):
                lines.pop()
            for i, raw in enumerate(lines):
                line_no += 1
                cr = raw.endswith("\r")
                ending = ("\r\n" if cr else "\n") if i < len(lines) - 1 or data.endswith(b"\n") else ""
                line = raw[:-1] if cr else raw
                if title is None:
                    title, newline = line, ending or newline
                    continue
                if ending and ending != newline:
                    exact = False
                if axis is None:
                    if _integer(line.split(",")[0]) != 0 or "," not in line:
                        raise _fail("not_surpac", "Вторая строка файла — не строка осей Surpac («0, …»).", _HINT)
                    axis = line
                    continue
                if style is None:
                    style = _learn([t.removesuffix("\r") for t in lines[i : i + LEARN_LINES]], newline)
                tokens = line.split(",")
                number = _integer(tokens[0])
                if number is None or number < 0 or len(tokens) < 4:
                    exact = False
                    if line.strip():
                        bad.append(line_no)
                    continue
                if number == 0:
                    if any(t.strip().upper() == "END" for t in tokens[4:]):
                        end = (line, ending)
                        exact = exact and not open_segment
                        tail = b"\n".join(data.split(b"\n")[i + 1 :]) + rest
                        break
                    exact = exact and line == style.null_line and open_segment
                    open_segment = False
                    continue
                coords: list[float] = []
                for t in tokens[1:4]:
                    try:
                        v = float(t)
                    except ValueError:
                        v = math.nan
                    if not math.isfinite(v):
                        bad_coords += 1
                        v = math.nan
                    coords.append(v)
                descs = tokens[4:]
                if len(descs) > MAX_DESC:
                    raise _fail("too_large", f"Строка {line_no}: описаний {len(descs)} — больше {MAX_DESC}.")
                values = [t.strip() or None for t in descs]
                if exact and style.record(number, coords[0], coords[1], coords[2], values) != line:
                    exact = False
                new = not open_segment or number != current
                if open_segment and number != current:
                    exact = False  # a segment ended without a 0 record
                body.number.append(number)
                body.y.append(coords[0])
                body.x.append(coords[1])
                body.z.append(coords[2])
                body.start.append(new)
                body.descs.append(values if any(values) else None)
                current, open_segment = number, True
            body.flush()
            tick(min(done / size, 1.0) * 0.9, f"Прочитано строк: {n_text(line_no)}")
            if not chunk:
                break
        if end is not None and len(tail) < MAX_LINE:
            tail += f.read(MAX_LINE)
            if f.read(1):
                exact = False
                warnings.append("После записи END в файле много данных: они не прочитаны")
                tail = b""
    if title is None or axis is None:
        raise _fail("truncated", "Файл обрезан: в нём нет строки осей и записей.")
    if end is None:
        raise _fail(
            "truncated",
            "Файл обрезан: нет завершающей записи «0, …, END».",
            "Скопируйте файл заново. Если файл создан другой программой без записи END, "
            f"добавьте в конец строку «{END_LINE}».",
        )
    if tail.strip():
        warnings.append("После записи END есть строки: они не прочитаны")
    if bad:
        shown = f"{n_text(len(bad))} строк не читаются как записи Surpac (например, строка {bad[0]})"
        warnings.append(f"{shown}: пропущены")
    if bad_coords:
        warnings.append(f"{n_text(bad_coords)} координат не являются числами: прочитаны как пустые")
    if replaced:
        warnings.append(f"{n_text(replaced)} символов не читаются в кодировке {codec}")
        exact = False
    if not body.parts:
        raise _fail("no_lines", "В файле нет ни одной вершины.", "Проверьте, что файл стрингов не пуст.")

    numbers = np.concatenate([p[0] for p in body.parts])
    xyz = np.concatenate([p[1] for p in body.parts])
    starts = np.concatenate([p[2] for p in body.parts])
    columns: list[Column] = []
    for j in range(body.width):
        chunks = [p[3][j] if j < len(p[3]) else pa.nulls(len(p[0]), type=pa.string()) for p in body.parts]
        columns.append(Column(f"D{j + 1}", "text", pa.chunked_array(chunks, type=pa.string())))
    missing = int((~np.isfinite(xyz[:, :2])).any(axis=1).sum())
    if missing:
        warnings.append(f"{n_text(missing)} вершин без координаты X или Y пропущены")
        header_exact = False
    else:
        header_exact = exact
    keys, codes = np.unique(numbers, return_inverse=True)
    names = [f"Стринг {k}" for k in keys.tolist()]
    layers = build_layers(xyz, starts, codes.ravel(), names, columns, (), scaled(tick, 0.9, 1.0))
    assert style is not None
    header = SurpacHeader(title, axis, end[0], end[1], tail, style, codec, bom, body.width, header_exact)
    tick(1.0, f"Прочитано вершин: {n_text(len(xyz))}")
    return StrRead("surpac_str", layers, header, codec, len(xyz), warnings)


# ---------------------------------------------------------------- writing


def _numbers(layers: Sequence[LineSet], warnings: list[str]) -> list[int]:
    """String number of every layer: from its name («Стринг 5» or «5»), else a free one."""
    wanted: list[int | None] = []
    for layer in layers:
        m = _LAYER.fullmatch(layer.name)
        wanted.append(int(m.group(1)) if m and int(m.group(1)) >= 1 else None)
    used: set[int] = set()
    out: list[int] = []
    for w in wanted:
        out.append(w if w is not None and w not in used else 0)
        used.add(out[-1])
    fresh, notes = 1, []
    for i, n in enumerate(out):
        if n == 0:
            while fresh in used:
                fresh += 1
            out[i] = fresh
            used.add(fresh)
            notes.append(f"«{layers[i].name}» → {fresh}")
    if notes:
        shown = ", ".join(notes[:5]) + (f" и ещё {len(notes) - 5}" if len(notes) > 5 else "")
        warnings.append(f"Слоям присвоены номера стрингов: {shown}")
    return out


def _descriptions(layers: Sequence[LineSet], warnings: list[str]) -> list[tuple[int, str, str]]:
    """(D index from 0, attribute, level): D1…D100 keep their place, other attributes take the free ones."""
    slots: list[tuple[int, str, str]] = []
    others: list[tuple[str, str]] = []
    for name, level in attribute_names(layers):
        m = re.fullmatch(r"[Dd](\d+)", name.strip())
        if m and 1 <= int(m.group(1)) <= MAX_DESC:
            slots.append((int(m.group(1)) - 1, name, level))
        elif name.strip().upper() != "JOIN":  # JOIN of Micromine is the line itself: segments keep it
            others.append((name, level))
    free = (j for j in range(MAX_DESC) if j not in {s for s, _, _ in slots})
    moved = []
    for name, level in others:
        j = next(free, None)
        if j is None:
            warnings.append(f"Атрибут «{name}» не записан: в Surpac не больше {MAX_DESC} описаний")
            continue
        slots.append((j, name, level))
        moved.append(f"«{name}» → D{j + 1}")
    if moved:
        warnings.append("Атрибуты записаны как описания: " + ", ".join(moved))
    return sorted(slots)


def _texts(column: Column) -> list[str | None]:
    """Values of a description: text in one line; numbers as typed (12.0 → "12"); empty → None."""
    return [(_CONTROL.sub(" ", t).strip() or None) if t else None for t in column.text()]


def write_surpac(
    path: str | Path,
    layers: Sequence[LineSet],
    *,
    like: SurpacHeader | None = None,
    encoding: str | None = None,
    title: str = "",
    date: dt.date | None = None,
    progress: Progress | None = None,
) -> SurpacWritten:
    """Write line layers as a Surpac text string file; `like` — the header of the source (same bytes back)."""
    import numpy as np

    path = Path(path)
    tick = progress or no_progress
    tick(0.0, "Подготовка линий")
    layers = [layer for layer in layers if layer.line_count and layer.point_count]
    if not layers:
        raise _fail("no_lines", "Нет линий для записи в Surpac STR.", "Выберите слой, в котором есть линии.")
    warnings: list[str] = []
    style = like.style if like is not None else SurpacStyle()
    codec = encoding or (like.encoding if like is not None else DEFAULT_ENCODING)
    nl = style.newline
    if like is not None:
        head = like.title + nl + like.axis + nl
        end = like.end + like.end_newline
    else:
        day = date or dt.date.today()
        line = _CONTROL.sub(" ", title).replace(",", ";")
        head = f"{line},{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year % 100:02d},," + nl + AXIS_LINE + nl
        end = END_LINE + nl
    numbers = _numbers(layers, warnings)
    g = gather(layers)
    slots = _descriptions(layers, warnings)
    width = max((j + 1 for j, _, _ in slots), default=0)
    columns = [(j, g.column(layers, name, level)) for j, name, level in slots]
    texts = [(j, _texts(c)) for j, c in columns]
    commas = sum(1 for _, values in texts for v in values if v and "," in v)
    if commas:
        warnings.append(f"{n_text(commas)} описаний содержали запятую: она заменена на «;»")
        texts = [(j, [v.replace(",", ";") if v else v for v in values]) for j, values in texts]
    number_of = np.asarray(numbers, dtype=np.int64)[g.layer].tolist()
    ends = set((np.cumsum(g.counts) - 1).tolist())
    xs, ys, zs = (g.xyz[:, k].tolist() for k in range(3))
    m = len(xs)
    part = path.with_name(path.name + ".part")
    try:
        with part.open("wb") as out:
            if like is not None and like.bom:
                out.write(b"\xef\xbb\xbf")
            out.write(_encode(head, codec))
            step = 1 << 16
            for a in range(0, m, step):
                tick(0.1 + 0.9 * a / m, f"Запись вершин: {n_text(a)} из {n_text(m)}")
                rows: list[str] = []
                for i in range(a, min(m, a + step)):
                    descs: list[str | None] = [None] * width
                    for j, values in texts:
                        descs[j] = values[i]
                    rows.append(style.record(number_of[i], ys[i], xs[i], zs[i], descs))
                    if i in ends:
                        rows.append(style.null_line)
                rows.append("")
                out.write(_encode(nl.join(rows), codec))
            out.write(_encode(end, codec))
            if like is not None:
                out.write(like.tail)
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    tick(1.0, f"Записано вершин: {n_text(m)}")
    return SurpacWritten(path, m, len(g.counts), path.stat().st_size, warnings)


def _encode(text: str, codec: str) -> bytes:
    try:
        return text.encode(codec)
    except UnicodeEncodeError as exc:
        raise _fail(
            "unrepresentable",
            f"Символ «{exc.object[exc.start]}» нельзя записать в кодировке {codec}.",
            "Замените символ или выберите кодировку UTF-8.",
        ) from exc


__all__ = ["SurpacHeader", "SurpacStyle", "SurpacWritten", "is_surpac", "read_surpac", "write_surpac"]
