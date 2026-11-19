"""Points XYZ of a point cloud: a text table of millions of rows read as a stream, and a cloud written back as CSV
(ТЗ 7.35 Д-22, 9.12; ADR 0014 §3 «XYZ / CSV / TXT», «CSV, запись»).

`inspect_xyz(path, encoding=, x=, y=, z=, cls=)` → `XyzHead`: what the head of the file tells — the encoding (by
its first megabytes), the delimiter and whether the first line is a header (the rules of `points.read_points`,
Э7), the decimal sign, the columns X, Y, Z and «класс» (by name, or given; a file without a header — X, Y, Z by
position), the first rows for the wizard and the columns that are not loaded.
`XyzCloud(path, head, every=1).chunks()` → `lines.cloud.CloudChunk`s in the order of the file: the body is cut into
blocks of whole lines and each block is parsed by `pyarrow.csv` (runs of spaces and tabs become one delimiter
first); a block that has cells that are not plain numbers (`4 584 699,7`, «н/д») is parsed again by the number
reader of Э3. Only the four columns are converted; `seq` is the number of the data row (from 0; a row of another
number of cells is not counted), `every` = k takes the rows 0, k, 2k, … A row without X, Y or Z is not a point:
it is skipped and counted (a gap is never a zero), as is a row of another number of cells; a class that is not a
whole number 0–255 is 0 (counted). A pass can be repeated (the cloud is built in two). A UTF-16 file is read by
the table reader of Э3, at most `FALLBACK_ROWS` rows, with the hint «сохраните в LAS».
`write_cloud_csv(path, portions, …)` writes `X;Y;Z;класс[;h]`: numbers as the shortest text that reads back to the
same number of their own precision (Arrow's formatter): X, Y, Z — doubles (as `repr`), `h` — the float32 of the
cloud («0.13», not the double of the same value); empty `h` as an empty cell, portion by portion, the file under
its name only when complete.
Error codes: empty_file, unsupported, no_coordinates, no_column, too_many_points, unknown_delimiter,
unknown_encoding, unrepresentable.
"""

from __future__ import annotations

import codecs
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.formats.errors import FormatError
from bf_core.formats.table import Progress, no_progress

CLASS_NAMES = ("CLASS", "CLASSIFICATION", "КЛАСС", "CLS", "КОД_КЛАССА", "КЛАСС_ТОЧКИ")
HEAD_BYTES = 1 << 20  # the head of the file the layout is told by
SNIFF_BYTES = 4 << 20  # … and the encoding
BLOCK = 16 << 20  # bytes of the body parsed at a time
PREVIEW = 20
FALLBACK_ROWS = 5_000_000
OUT_DELIMITERS = (";", ",", "\t", "|", " ")
_WIDE = ("utf-16", "utf-16-le", "utf-16-be")
_HINT = "Нужна таблица с колонками X, Y, Z (по строке на точку): CSV, TXT или XYZ."
_LAS_HINT = "Сохраните облако в LAS (например, в CloudCompare) и загрузите его как LAS."


def _fail(code: str, message: str, hint: str = _HINT) -> FormatError:
    return FormatError(code, message, hint)


def _unknown_encoding(encoding: str) -> FormatError:
    return _fail("unknown_encoding", f"Неизвестная кодировка «{encoding}».", "Выберите кодировку из списка.")


def n_text(x: int) -> str:
    return f"{x:,}".replace(",", " ")


# ---------------------------------------------------------------- the head


@dataclass
class XyzHead:
    path: Path
    size: int
    encoding: str
    delimiter: str  # ",", ";", "\t", "|" or "whitespace"
    decimal: str  # "." or ","
    header: bool  # the first line names the columns
    names: list[str]  # of the columns, in the order of the file
    width: int  # cells in a data row
    fields: dict[str, str | None]  # "x", "y", "z", "class" → the column (class: None — not in the file)
    start: int  # the byte the data rows start at
    preview: list[list[str]]
    estimate: int  # data rows, by the size of the file and the length of the first rows
    warnings: list[str] = field(default_factory=list)

    @property
    def index(self) -> dict[str, int | None]:
        return {k: None if v is None else self.names.index(v) for k, v in self.fields.items()}

    @property
    def left_out(self) -> list[str]:
        used = {v for v in self.fields.values() if v}
        return [n for n in self.names if n not in used]

    def source(self) -> dict[str, Any]:
        """How the file is read (`meta.cloud.source.xyz`)."""
        return {
            "encoding": self.encoding, "delimiter": self.delimiter, "decimal": self.decimal, "header": self.header,
            "columns": self.names, "fields": dict(self.fields),
        }  # fmt: skip

    def as_dict(self) -> dict[str, Any]:
        """For the wizard (7.1.14): the layout, the columns, the first rows, what is not loaded."""
        not_loaded = [f"колонка «{n}»" for n in self.left_out]
        estimate = n_text(self.estimate)
        return {
            "format": "xyz", **self.source(), "preview": self.preview, "estimate": self.estimate,
            "not_loaded": not_loaded, "warnings": list(self.warnings),
            "summary": f"Точки XYZ: около {estimate} строк, колонки X, Y, Z — "
                       f"«{self.fields['x']}», «{self.fields['y']}», «{self.fields['z']}»"
                       + (f", класс — «{self.fields['class']}»" if self.fields["class"] else ""),
        }  # fmt: skip


def _head_lines(raw: bytes, start: int, encoding: str, whole: bool) -> list[tuple[str, int]]:
    """The non-empty lines of the head with the byte each one ends at (after its line feed)."""
    out: list[tuple[str, int]] = []
    pos = start
    pieces = raw[start:].split(b"\n")
    if not whole:
        pieces = pieces[:-1]  # the last piece may be cut
    for piece in pieces:
        pos += len(piece) + 1
        text = piece.decode(encoding, "replace").strip()
        if text:
            out.append((text, min(pos, len(raw))))
    return out


def _decimal(rows: list[list[str]], columns: list[int], delimiter: str) -> str:
    if delimiter == ",":
        return "."
    comma = point = 0
    for row in rows:
        for k in columns:
            cell = row[k].strip() if k < len(row) else ""
            comma += "," in cell
            point += "." in cell
    return "," if comma and not point else "."


def inspect_xyz(
    path: str | Path,
    *,
    encoding: str | None = None,
    x: str | None = None,
    y: str | None = None,
    z: str | None = None,
    cls: str | None = None,
) -> XyzHead:
    """What the head of a table of points tells (see the module docstring); `x`, `y`, `z`, `cls` — the columns
    when they are not to be guessed (`cls=""` — no class column)."""
    from bf_core.formats import registry
    from bf_core.formats.points import X_NAMES, Y_NAMES, Z_NAMES, _delimiter, _find, _starts_with_a_point
    from bf_core.formats.text import _split, sniff_encoding
    from bf_core.importing.names import match_column

    path = Path(path)
    size = path.stat().st_size
    with path.open("rb") as f:
        raw = f.read(HEAD_BYTES)
    if not raw.strip():
        raise _fail("empty_file", "Файл пуст.", "Проверьте, что файл скопирован полностью.")
    if not registry.looks_like_text(raw[: 1 << 16]):
        raise _fail("unsupported", "Файл не похож на текстовую таблицу точек: он двоичный.")
    if encoding:
        try:
            encoding = codecs.lookup(encoding).name
        except LookupError as exc:
            raise _unknown_encoding(encoding) from exc
    else:
        encoding = sniff_encoding(path, limit=SNIFF_BYTES)
    wide = encoding.replace("_", "-") in _WIDE
    start = 3 if raw.startswith(codecs.BOM_UTF8) else 0
    if wide:  # the lines of the head only: the body goes to the reader of Э3
        text = raw.decode(encoding, "replace").lstrip("﻿")
        lines = [(line.strip(), 0) for line in text.splitlines() if line.strip()]
        lines = lines if len(raw) == size else lines[:-1]
    else:
        lines = _head_lines(raw, start, "utf-8" if encoding.startswith("utf-8") else encoding, len(raw) == size)
    lines = lines[: PREVIEW * 10]
    if not lines:
        raise _fail("empty_file", "В файле нет строк с данными.", "Проверьте, что файл скопирован полностью.")
    texts = [t for t, _ in lines]
    delimiter = _delimiter(texts)
    header = not _starts_with_a_point(texts[0], delimiter)
    data = lines[1:] if header else lines
    rows = [_split(t, delimiter) for t, _ in data[: PREVIEW * 10]]
    counts: dict[int, int] = {}
    for row in rows:
        counts[len(row)] = counts.get(len(row), 0) + 1
    width = max(counts.items(), key=lambda item: (item[1], item[0]))[0] if counts else 0
    names = [" ".join(n.split()) for n in _split(texts[0], delimiter)] if header else []
    width = max(width, len(names))
    names += [f"Колонка {k + 1}" for k in range(len(names), width)]
    names = _unique(names)
    fx: str | None
    fy: str | None
    fz: str | None
    if not header and not (x or y):
        fx, fy = (names[0] if names else None), (names[1] if len(names) > 1 else None)
        fz = names[2] if len(names) > 2 else None
    else:
        fx = _find(names, x, "X") if x else match_column(names, X_NAMES)
        fy = _find(names, y, "Y") if y else match_column(names, Y_NAMES, {fx} if fx else set())
        fz = match_column(names, Z_NAMES, {n for n in (fx, fy) if n})
    if z:
        fz = _find(names, z, "Z")
    if cls is not None:
        fc = _find(names, cls, "класс") if cls else None
    else:
        fc = match_column(names, CLASS_NAMES, {n for n in (fx, fy, fz) if n}) if header else None
    if fx is None or fy is None or fx == fy:
        raise _fail(
            "no_coordinates",
            "В файле не найдены колонки координат X и Y.",
            f"Колонки файла: {', '.join(names[:40]) or 'нет'}. Назовите колонки X, Y, Z или укажите их при импорте.",
        )
    if fz is None:
        raise _fail(
            "no_column",
            "В файле не найдена колонка Z: облако точек загружается только с высотами.",
            f"Колонки файла: {', '.join(names[:40])}. Укажите колонку Z при импорте.",
        )
    fields = {"x": fx, "y": fy, "z": fz, "class": fc}
    decimal = _decimal(rows[:200], [names.index(n) for n in (fx, fy, fz)], delimiter)
    begin = lines[0][1] if header else start
    if not data:
        raise _fail("no_points", "В файле нет ни одной строки с точкой.", _HINT)
    span = (data[min(len(data), 200) - 1][1] - (lines[0][1] if header else start)) if not wide else 0
    estimate = max(1, int(size * min(len(data), 200) / span)) if span > 0 else len(data)
    warnings: list[str] = []
    left = [n for n in names if n not in {v for v in fields.values() if v}]
    if left:
        names_left = ", ".join(f"«{n}»" for n in left[:20])
        warnings.append(f"Колонки {names_left} не загружаются: облако хранит X, Y, Z и класс.")
    if wide:
        warnings.append(f"Файл в кодировке {encoding}: читается целиком, не больше {n_text(FALLBACK_ROWS)} строк.")
    preview = [row[:width] for row in rows[:PREVIEW]]
    return XyzHead(path, size, encoding, delimiter, decimal, header, names, width, fields, begin, preview, estimate,
                   warnings)  # fmt: skip


def _unique(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        base = title = name or "Поле"
        k = 1
        while title.casefold() in seen:
            k += 1
            title = f"{base}_{k}"
        seen.add(title.casefold())
        out.append(title)
    return out


# ---------------------------------------------------------------- the body

_TABS = bytes.maketrans(b"\t", b" ")


def _one_space(data: bytes) -> bytes:
    """Runs of spaces and tabs as one space, none at the ends of the lines (`bytes.replace` — no regex: a block
    of 16 MB takes milliseconds)."""
    data = data.translate(_TABS)
    while b"  " in data:
        data = data.replace(b"  ", b" ")
    data = data.replace(b"\n ", b"\n").replace(b" \n", b"\n").replace(b" \r", b"\r")
    return data[1:] if data.startswith(b" ") else data


@dataclass
class XyzStats:
    """What the last whole pass found."""

    rows: int = 0  # data rows of the file (with `every` — before the thinning)
    points: int = 0  # … that became points
    no_z: int = 0  # rows with X and Y but without Z
    no_xy: int = 0  # rows without X or Y
    ragged: int = 0  # rows with another number of cells
    bad_class: int = 0  # classes that are not a whole number 0–255 (taken as 0)
    slow: int = 0  # blocks parsed by the number reader of Э3


class XyzCloud:
    """The points of a table as a source of a cloud (`lines.cloud.ChunkSource`: `chunks` from the start every
    time it is called)."""

    def __init__(self, path: str | Path, head: XyzHead, *, every: int = 1, block: int = BLOCK) -> None:
        if every < 1 or block < 1:
            raise ValueError("every and block: positive numbers are expected")
        self.path, self.head, self.every, self.block = Path(path), head, every, block
        self.stats: XyzStats | None = None
        self._memo: tuple[np.ndarray, np.ndarray | None, np.ndarray] | None = None  # the UTF-16 file read by Э3

    @property
    def expected(self) -> int:
        return -(-self.head.estimate // self.every)

    @property
    def warnings(self) -> list[str]:
        out = list(self.head.warnings)
        st = self.stats
        if st is None:
            return out
        if st.no_z:
            out.append(f"Строк без Z: {n_text(st.no_z)} — точки пропущены (пустая высота не заменяется нулём).")
        if st.no_xy:
            out.append(f"Строк без X или Y (или не числами): {n_text(st.no_xy)} — пропущены.")
        if st.ragged:
            out.append(f"Строк с другим числом колонок: {n_text(st.ragged)} — пропущены.")
        if st.bad_class:
            out.append(f"Классов, которые не являются целым числом 0–255: {n_text(st.bad_class)} — записан класс 0.")
        return out

    def chunks(self, progress: Progress | None = None) -> Iterator[Any]:
        tick = progress or no_progress
        if self.head.encoding.replace("_", "-") in _WIDE:
            return self._fallback(tick)
        return self._stream(tick)

    # --- the points of a block of columns

    def _points(self, cols: list[np.ndarray], seq: np.ndarray, st: XyzStats) -> Any:
        """A portion of points of the columns X, Y, Z [, class] of data rows `seq`."""
        from bf_core.lines.cloud import CloudChunk

        st.rows += len(seq)
        if self.every > 1:
            take = np.flatnonzero(seq % self.every == 0)
            cols, seq = [c[take] for c in cols], seq[take]
        xyz = np.column_stack(cols[:3]).astype(np.float64, copy=False)
        xy = np.isfinite(xyz[:, :2]).all(axis=1)
        zok = np.isfinite(xyz[:, 2])
        st.no_xy += int(np.count_nonzero(~xy))
        st.no_z += int(np.count_nonzero(xy & ~zok))
        keep = xy & zok
        cls = None
        if len(cols) > 3:
            raw = cols[3]
            fit = np.isfinite(raw) & (raw >= 0) & (raw <= 255) & (raw == np.floor(raw))
            st.bad_class += int(np.count_nonzero(~fit & keep))
            cls = np.where(fit, raw, 0).astype(np.uint8)
        if not keep.all():
            xyz, seq = xyz[keep], seq[keep]
            cls = None if cls is None else cls[keep]
        st.points += len(xyz)
        if seq.size and int(seq[-1]) >= 1 << 32:
            raise _fail("too_many_points", "В файле больше 4 294 967 295 строк.", _LAS_HINT)
        return CloudChunk(np.ascontiguousarray(xyz), cls, None, seq.astype("<u4"))

    def _wanted(self) -> list[int]:
        idx = self.head.index
        return [k for k in (idx["x"], idx["y"], idx["z"], idx["class"]) if k is not None]

    def _parse(self, data: bytes, st: XyzStats) -> list[np.ndarray]:
        import pyarrow as pa
        import pyarrow.csv as pcsv

        h = self.head
        if h.delimiter == "whitespace":
            data = _one_space(data)
        names = [f"c{k}" for k in range(h.width)]
        wanted = [names[k] for k in self._wanted()]
        ragged = 0

        def skip(_row: Any) -> str:
            nonlocal ragged
            ragged += 1
            return "skip"

        read = pcsv.ReadOptions(column_names=names, block_size=1 << 24)
        parse = pcsv.ParseOptions(delimiter=" " if h.delimiter == "whitespace" else h.delimiter,
                                  invalid_row_handler=skip)  # fmt: skip
        try:
            table = pcsv.read_csv(
                pa.BufferReader(data), read_options=read, parse_options=parse,
                convert_options=pcsv.ConvertOptions(
                    include_columns=wanted, column_types=dict.fromkeys(wanted, pa.float64()),
                    decimal_point=h.decimal, check_utf8=False,
                ),
            )  # fmt: skip
            out = [table.column(c).to_numpy(zero_copy_only=False).astype(np.float64, copy=False) for c in wanted]
        except pa.ArrowInvalid:  # cells that are not plain numbers: the number reader of Э3, this block only
            from bf_core.formats.numbers import parse_numbers

            ragged = 0
            st.slow += 1
            table = pcsv.read_csv(
                pa.BufferReader(data), read_options=read, parse_options=parse,
                convert_options=pcsv.ConvertOptions(
                    include_columns=wanted, column_types=dict.fromkeys(wanted, pa.string()), check_utf8=False,
                    strings_can_be_null=True,
                ),
            )  # fmt: skip
            out = []
            for c in wanted:
                cells = table.column(c).to_pylist()
                if h.decimal == ",":
                    cells = [None if v is None else v.replace(",", ".") for v in cells]
                out.append(parse_numbers(cells).values)
        st.ragged += ragged
        return out

    def _stream(self, tick: Progress) -> Iterator[Any]:
        st = XyzStats()
        size = max(self.head.size, 1)
        base = 0
        with self.path.open("rb") as f:
            f.seek(self.head.start)
            done = self.head.start
            rest = b""
            while True:
                tick(min(done / size, 1.0), f"Чтение точек: {n_text(st.rows)} строк")
                block = f.read(self.block)
                done += len(block)
                data = rest + block
                if block:
                    cut = data.rfind(b"\n") + 1
                    if cut == 0:
                        rest = data
                        continue
                    data, rest = data[:cut], data[cut:]
                else:
                    rest = b""
                if data.strip():
                    cols = self._parse(data, st)
                    n = len(cols[0])
                    if n:
                        yield self._points(cols, np.arange(base, base + n, dtype=np.int64), st)
                        base += n
                if not block:
                    break
        self.stats = st
        tick(1.0, f"Прочитано точек: {n_text(st.points)}")

    def _fallback(self, tick: Progress) -> Iterator[Any]:
        if self._memo is None:
            from bf_core.formats.points import read_points

            f = self.head.fields
            read = read_points(self.path, tick, x=f["x"], y=f["y"], z=f["z"] or "", encoding=self.head.encoding)
            layer = read.layers[0]
            if read.records > FALLBACK_ROWS:
                raise _fail("too_many_points",
                            f"В файле {n_text(read.records)} строк: в кодировке {self.head.encoding} облако читается "
                            f"не больше {n_text(FALLBACK_ROWS)} строк.", _LAS_HINT)  # fmt: skip
            cls = None
            if f["class"] and layer.attrs is not None and f["class"] in layer.attrs.column_names:
                cls = np.asarray(layer.attrs.column(f["class"]).to_numpy(zero_copy_only=False), dtype=np.float64)
            # rows without X or Y are left out by the reader of Э3; `seq` — the numbers of the rows taken
            self._memo = (np.asarray(layer.xyz, dtype=np.float64), cls, np.asarray(layer.seq, dtype=np.int64))
        xyz, cls, rows = self._memo
        st = XyzStats()
        step = 1_000_000
        for a in range(0, len(xyz), step):
            cols = [xyz[a : a + step, k] for k in range(3)] + ([cls[a : a + step]] if cls is not None else [])
            yield self._points(cols, rows[a : a + step], st)
        self.stats = st
        tick(1.0, f"Прочитано точек: {n_text(st.points)}")


def open_xyz(path: str | Path, *, every: int = 1, **columns: Any) -> XyzCloud:
    """The head told (`inspect_xyz`) and the file opened as a source of a cloud."""
    return XyzCloud(path, inspect_xyz(path, **columns), every=every)


# ---------------------------------------------------------------- writing


@dataclass
class CloudCsvWritten:
    path: Path
    rows: int
    columns: list[str]
    size: int
    warnings: list[str] = field(default_factory=list)


def write_cloud_csv(
    path: str | Path,
    portions: Iterable[tuple[np.ndarray, np.ndarray, np.ndarray | None]],
    *,
    total: int,
    with_h: bool = False,
    delimiter: str = ";",
    decimal: str = ".",
    encoding: str = "utf-8",
    progress: Progress | None = None,
) -> CloudCsvWritten:
    """A cloud as `X;Y;Z;класс[;h]` (see the module docstring). `portions` — (xyz n×3, class n, h n | None) in
    the order to write; `total` — the points of all of them (for the progress)."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.csv as pcsv

    path = Path(path)
    tick = progress or no_progress
    if delimiter not in OUT_DELIMITERS:
        raise _fail(
            "unknown_delimiter",
            f"Неизвестный разделитель: {delimiter!r}",
            "Допустимы «;», «,», табуляция, «|», пробел.",
        )
    if decimal not in (".", ",") or decimal == delimiter:
        raise _fail(
            "unrepresentable",
            "Десятичный знак совпадает с разделителем колонок: числа нельзя будет прочитать.",
            "Выберите разделитель «;» или табуляцию либо десятичную точку.",
        )
    try:
        codec = codecs.lookup(encoding).name
    except LookupError as exc:
        raise _unknown_encoding(encoding) from exc
    titles = ["X", "Y", "Z", "класс", *(["h"] * with_h)]
    empty = "nan" if delimiter == " " else ""

    def text(values: np.ndarray) -> Any:
        arr = pa.array(values, mask=~np.isfinite(values)) if values.dtype.kind == "f" else pa.array(values)
        out = pc.cast(arr, pa.string())
        if decimal == ",":
            out = pc.replace_substring(out, ".", ",")
        return pc.fill_null(out, empty) if out.null_count else out

    part = path.with_name(path.name + ".part")
    rows = 0
    options = pcsv.WriteOptions(include_header=False, delimiter=delimiter, quoting_style="none")
    try:
        with part.open("wb") as out:
            out.write((delimiter.join(titles) + "\n").encode(codec))
            for xyz, cls, h in portions:
                tick(rows / max(total, 1), f"Запись точек: {n_text(rows)} из {n_text(total)}")
                cols = [text(np.ascontiguousarray(xyz[:, k])) for k in range(3)] + [text(np.asarray(cls))]
                if with_h:
                    # `h` of a cloud is a float32: its own shortest text («0.13»), not that of the float64 of the
                    # same value («0.12999999523162842»)
                    heights = np.full(len(xyz), np.nan, np.float32) if h is None else np.asarray(h)
                    cols.append(text(heights if heights.dtype == np.float32 else heights.astype(np.float64)))
                if not len(xyz):
                    continue
                pcsv.write_csv(pa.table(cols, names=[f"c{k}" for k in range(len(cols))]), out, options)
                rows += len(xyz)
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    tick(1.0, f"Записано точек: {n_text(rows)}")
    return CloudCsvWritten(path, rows, titles, path.stat().st_size)


__all__ = ["CloudCsvWritten", "XyzCloud", "XyzHead", "XyzStats", "inspect_xyz", "open_xyz", "write_cloud_csv"]
