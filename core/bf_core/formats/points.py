"""Points XYZ: text tables (CSV, TXT, XYZ), Micromine DAT and Excel → a layer of points, and back (ТЗ 7.18, 9.12;
ADR 0011 §2).

`read_points(path, progress)` → `PointsRead` with one `lineset.LineSet` of points. The file is read by the table
readers of Э3 (`text.read_text`, `micromine.read_dat`, `excel.read_excel`): streaming, any of their encodings,
a decimal point or comma, «н/д» and the like as empty.
  * Columns X, Y, Z are found by name (`X`, `EAST`, `Восток`, `X, м` …, `importing.names`) or given (`x`, `y`,
    `z`; `z=""` — no heights). A text file whose first line starts with two numbers has no header: its columns
    are X, Y, Z (when the third holds numbers) and then attributes («Колонка 4» …).
  * The delimiter is the one that splits the lines evenly into the most numeric cells, so `1,5 2,5 3,5` is three
    numbers between spaces, not four cells between commas.
  * An empty Z is NaN, never 0; a file without a Z column gives points without heights (a warning). A row without
    X or Y is left out and counted (a `LineSet` holds finite X and Y); `seq` is the number of the row in the file.
  * Every other column is an attribute of the point: a number (NaN = empty) or a text (null = empty).
`write_points(path, layers)` writes a CSV: X, Y, Z and the attributes; numbers as `repr` (the shortest text that
reads back to the same double, so read → write → read is bit for bit), an empty value as an empty cell.
`write_points_dat` writes a Micromine DAT (EAST, NORTH, RL as binary doubles) through `micromine_write`.
Layers of lines are written by their vertices, with the attributes of the line on each; several layers go into
one file with a `LAYER` column.
Error codes (besides those of the table readers): unsupported, no_coordinates, no_column, no_points,
unrepresentable, unknown_delimiter.
"""

from __future__ import annotations

import codecs
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bf_core.formats.errors import FormatError
from bf_core.formats.str_layers import StrRead, attribute_names, gather, n_text, scaled
from bf_core.formats.table import Column, Progress, ReadOptions, TableRead, no_progress, unique_names

if TYPE_CHECKING:
    import numpy as np

    from bf_core.formats.lineset import LineSet
    from bf_core.formats.micromine_write import DatWritten

X_NAMES = ("X", "EAST", "EASTING", "XCOORD", "X_COORD", "XPT", "XP", "EAST_GRID", "GRID_EAST", "ВОСТОК", "Х")
Y_NAMES = ("Y", "NORTH", "NORTHING", "YCOORD", "Y_COORD", "YPT", "YP", "NORTH_GRID", "GRID_NORTH", "СЕВЕР", "У")
Z_NAMES = ("Z", "RL", "ELEV", "ELEVATION", "ZCOORD", "Z_COORD", "ZPT", "ZP", "ALTITUDE", "HEIGHT", "ОТМЕТКА",
           "АБС_ОТМЕТКА", "ВЫСОТА")  # fmt: skip
OUT_DELIMITERS = (";", ",", "\t", "|", " ")
CHUNK_ROWS = 50_000
LAYER_COLUMN = "LAYER"
_SCORED = (";", "\t", "|", "whitespace", ",")  # on equal scores the first one wins
_HINT = "Нужна таблица с колонками X, Y, Z (по строке на точку): CSV, TXT, XYZ или Micromine DAT."


@dataclass
class PointsRead(StrRead):
    """`format` — "points_csv" | "points_dat" | "points_xlsx" | "points_xls"; `header` — `table.ReadInfo` (the
    encoding, delimiter and header row that were used); `records` — the rows of the table."""

    fields: dict[str, str | None] = field(default_factory=dict)  # "x", "y", "z" → the column (z: None — no heights)


@dataclass
class PointsWritten:
    path: Path
    rows: int
    columns: list[str]
    size: int
    warnings: list[str] = field(default_factory=list)


def _fail(code: str, message: str, hint: str = _HINT) -> FormatError:
    return FormatError(code, message, hint)


# ---------------------------------------------------------------- reading


def _delimiter(lines: Sequence[str]) -> str:
    """The delimiter that splits most lines into the same number (> 1) of cells, most of them numbers."""
    from bf_core.formats.numbers import parse_value
    from bf_core.formats.text import _split

    sample = [line for line in lines if line.strip()][:200]
    best, best_score = ",", (0.0, 0.0, 0)
    for delimiter in _SCORED:
        try:
            rows = [_split(line, delimiter) for line in sample]
        except Exception:  # noqa: BLE001 — a quote the csv module does not accept: not this delimiter
            continue
        counts: dict[int, int] = {}
        for row in rows:
            counts[len(row)] = counts.get(len(row), 0) + 1
        if not counts:
            continue
        width, hits = max(counts.items(), key=lambda item: (item[1], item[0]))
        if width < 2:
            continue
        cells = [cell for row in rows[1:] or rows if len(row) == width for cell in row]
        numeric = sum(parse_value(cell)[1] == "number" for cell in cells) / max(len(cells), 1)
        score = (hits / len(sample), numeric, width)
        if score > best_score:
            best, best_score = delimiter, score
    return best


def _starts_with_a_point(line: str, delimiter: str) -> bool:
    """The first two cells are numbers: the line is a point (X, Y …), not the names of the columns."""
    from bf_core.formats.numbers import parse_value
    from bf_core.formats.text import _split

    cells = _split(line, delimiter)
    return len(cells) >= 2 and all(parse_value(c)[1] == "number" for c in cells[:2])


def _read_table(path: Path, options: ReadOptions, filename: str | None, tick: Progress) -> TableRead:
    from bf_core.formats import registry

    kind = registry.detect(path, filename)
    if kind == registry.DAT:
        from bf_core.formats.micromine import read_dat

        return read_dat(path, tick, encoding=options.encoding)
    if kind in (registry.XLSX, registry.XLS):
        from bf_core.formats.excel import read_excel

        return read_excel(path, options, tick)
    from bf_core.formats.text import head_lines, read_text, sniff_encoding

    with path.open("rb") as f:
        head = f.read(1 << 16)
    if not head:
        raise _fail("empty_file", "Файл пуст.", "Проверьте, что файл скопирован полностью.")
    if not registry.looks_like_text(head):
        raise _fail("unsupported", "Файл не похож на текстовую таблицу точек: он двоичный.")
    tick(0.0, "Определение кодировки")
    encoding = options.encoding or sniff_encoding(path, progress=scaled(tick, 0.0, 0.05))
    skip = max(options.header_row - 1, 0)
    lines = head_lines(path, encoding, skip + 200)[skip:]
    if not lines:
        raise _fail("empty_file", "В файле нет строк с данными.", "Проверьте, что файл скопирован полностью.")
    delimiter = options.delimiter or _delimiter(lines)
    header_row = options.header_row
    if header_row == 1 and _starts_with_a_point(lines[0], delimiter):
        header_row = 0  # the file has no header: the first line is already a point
    options = replace(options, encoding=encoding, delimiter=delimiter, header_row=header_row)
    return read_text(path, options, scaled(tick, 0.05, 1.0))


def _find(names: Sequence[str], wanted: str, what: str) -> str:
    for name in names:
        if name == wanted or name.casefold() == wanted.strip().casefold():
            return name
    raise _fail(
        "no_column",
        f"В файле нет колонки «{wanted}» (выбрана как {what}).",
        f"Колонки файла: {', '.join(names[:40])}.",
    )


def _coordinate(column: Column, warnings: list[str]) -> np.ndarray:
    """A coordinate column as float64; cells that are not numbers are NaN and named in the log."""
    if column.kind == "number":
        return column.values  # type: ignore[no-any-return]
    from bf_core.formats.numbers import parse_numbers

    parsed = parse_numbers(column.values)
    if parsed.bad:
        example = f", например «{parsed.bad_examples[0]}»" if parsed.bad_examples else ""
        warnings.append(f"{column.name}: значений, которые не являются числами, — {n_text(parsed.bad)}{example}")
    return parsed.values


def read_points(
    path: str | Path,
    progress: Progress | None = None,
    *,
    options: ReadOptions | None = None,
    x: str | None = None,
    y: str | None = None,
    z: str | None = None,
    name: str | None = None,
    filename: str | None = None,
    encoding: str | None = None,
) -> PointsRead:
    """Read a table of points into a layer (see the module docstring). `x`, `y`, `z` — the names of the
    coordinate columns when they are not to be guessed (`z=""` — no heights); `name` — the name of the layer
    (default: the file name); `filename` — the original name of a file stored under another one."""
    import numpy as np

    from bf_core.formats.lineset import LineSet
    from bf_core.importing.names import match_column

    path = Path(path)
    tick = progress or no_progress
    options = options or ReadOptions()
    if encoding:
        options = replace(options, encoding=encoding)
    tick(0.0, "Чтение точек")
    table = _read_table(path, options, filename, scaled(tick, 0.0, 0.85))
    names = table.names
    warnings = list(table.warnings)
    fx: str | None
    fy: str | None
    fz: str | None
    if table.info.header_row == 0 and not (x or y):  # no names: X, Y by position, then Z when it holds numbers
        fx = names[0] if names else None
        fy = names[1] if len(names) > 1 else None
        fz = names[2] if len(names) > 2 and table.columns[2].kind == "number" else None
    else:
        fx = _find(names, x, "X") if x else match_column(names, X_NAMES)
        fy = _find(names, y, "Y") if y else match_column(names, Y_NAMES, {fx} if fx else set())
        fz = match_column(names, Z_NAMES, {n for n in (fx, fy) if n})
    if z is not None:
        fz = _find(names, z, "Z") if z else None
    if fx is None or fy is None or fx == fy:
        raise _fail(
            "no_coordinates",
            "В файле не найдены колонки координат X и Y.",
            f"Колонки файла: {', '.join(names[:40]) or 'нет'}. Назовите колонки X, Y, Z или укажите их при импорте.",
        )
    if table.rows == 0:
        raise _fail("no_points", "В файле нет ни одной точки.", "Проверьте, что в таблице есть строки с координатами.")
    notes = [
        w for w in warnings if not any(w.startswith(f"{n}: колонка прочитана как текст") for n in (fx, fy, fz) if n)
    ]
    xs, ys = _coordinate(table.column(fx), notes), _coordinate(table.column(fy), notes)
    zs = _coordinate(table.column(fz), notes) if fz else np.full(table.rows, np.nan)
    if fz is None:
        notes.append("В файле нет колонки Z: точки загружены без высот")
    xyz = np.column_stack([xs, ys, zs]).astype(np.float64, copy=False)
    keep = np.isfinite(xyz[:, :2]).all(axis=1) & ~np.isinf(xyz[:, 2])
    dropped = int(table.rows - keep.sum())
    if dropped:
        notes.append(f"Строк без координаты X или Y: {n_text(dropped)} — они пропущены")
    if dropped == table.rows:
        raise _fail("no_points", "Ни в одной строке файла нет чисел в колонках X и Y.", _HINT)
    tick(0.9, "Сборка слоя точек")
    index = np.flatnonzero(keep)
    whole = dropped == 0
    attrs: dict[str, Any] = {}
    for column in table.columns:
        if column.name in (fx, fy, fz):
            continue
        if column.kind == "number":
            attrs[column.name] = column.values if whole else column.values[index]
        else:
            attrs[column.name] = column.values if whole else column.values.take(index)
    title = name or Path(filename or path.name).stem
    layer = LineSet(title, xyz if whole else xyz[index], None, None, index.astype(np.uint32), attrs or None)
    tick(1.0, f"Прочитано точек: {n_text(layer.point_count)}")
    info = table.info
    return PointsRead(
        f"points_{info.format}", [layer], info, info.encoding or "", table.rows, notes, {"x": fx, "y": fy, "z": fz}
    )


# ---------------------------------------------------------------- writing


def _columns(layers: Sequence[LineSet]) -> tuple[np.ndarray, list[Column]]:
    """Every vertex of the layers in the order to write, and its attributes (those of a line on each vertex)."""
    import numpy as np
    import pyarrow as pa

    if not layers or not sum(layer.point_count for layer in layers):
        raise _fail("no_points", "Нет точек для записи.", "Выберите слой, в котором есть точки.")
    g = gather(layers)
    columns: list[Column] = []
    if len(layers) > 1:
        labels = pa.array([layer.name for layer in layers], type=pa.string())
        columns.append(
            Column(LAYER_COLUMN, "text", pa.chunked_array([labels.take(pa.array(g.layer, type=pa.int64()))]))
        )
    for attr, scope in attribute_names(layers):
        columns.append(g.column(layers, attr, scope))
    taken = unique_names(["X", "Y", "Z", *(c.name for c in columns)])[3:]  # an attribute named X becomes X_2
    for column, title in zip(columns, taken, strict=True):
        column.name = title
    return np.ascontiguousarray(g.xyz), columns


def _quote(text: str, delimiter: str) -> str:
    if delimiter in text or '"' in text or text != text.strip() or not text:
        return '"' + text.replace('"', '""') + '"'
    return text


def write_points(
    path: str | Path,
    layers: Sequence[LineSet],
    *,
    delimiter: str = ";",
    decimal: str = ".",
    encoding: str = "utf-8",
    progress: Progress | None = None,
) -> PointsWritten:
    """Write the points (or the vertices of lines) as a CSV: a header, then a row per point. `delimiter` — one of
    `OUT_DELIMITERS` (a space: empty values are written as `nan` / `-`); `decimal` — "." or ","."""
    import numpy as np

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
        raise _fail(
            "unknown_encoding", f"Неизвестная кодировка «{encoding}».", "Выберите кодировку из списка."
        ) from exc
    tick(0.0, "Запись точек")
    xyz, columns = _columns(layers)
    rows = len(xyz)
    spaced = delimiter == " "
    empty_number, empty_text = ("nan", "-") if spaced else ("", "")
    warnings: list[str] = []
    broken = infinite = 0

    def number_cells(values: np.ndarray) -> list[str]:
        nonlocal infinite
        cells = [repr(v) for v in values.tolist()]
        if not np.isfinite(values).all():
            infinite += int(np.isinf(values).sum())
            cells = [c if math.isfinite(v) else empty_number for c, v in zip(cells, values.tolist(), strict=True)]
        return [c.replace(".", ",") for c in cells] if decimal == "," else cells

    def text_cells(values: list[str | None]) -> list[str]:
        nonlocal broken
        out: list[str] = []
        for value in values:
            if value is None:
                out.append(empty_text)
                continue
            if "\n" in value or "\r" in value:
                broken += 1
                value = " ".join(value.split())
            if spaced and (not value or any(ch.isspace() for ch in value) or value in ("-", "nan")):
                raise _fail(
                    "unrepresentable",
                    f"Текст «{value[:40]}» нельзя записать между пробелами.",
                    "Выберите разделитель «;» или табуляцию.",
                )
            out.append(value if spaced else _quote(value, delimiter))
        return out

    titles = ["X", "Y", "Z", *(c.name for c in columns)]
    part = path.with_name(path.name + ".part")
    try:
        with part.open("wb") as out:
            head = titles if spaced else [_quote(t, delimiter) for t in titles]
            out.write(_encode(delimiter.join(head) + "\n", codec))
            for start in range(0, rows, CHUNK_ROWS):
                tick(start / rows, f"Запись точек: {n_text(start)} из {n_text(rows)}")
                stop = min(rows, start + CHUNK_ROWS)
                cells = [number_cells(xyz[start:stop, k]) for k in range(3)]
                for column in columns:
                    if column.kind == "number":
                        cells.append(number_cells(column.values[start:stop]))
                    else:
                        cells.append(text_cells(column.values.slice(start, stop - start).to_pylist()))
                lines = [delimiter.join(row) for row in zip(*cells, strict=True)]
                out.write(_encode("\n".join(lines) + "\n", codec))
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    if broken:
        warnings.append(f"Переносы строк внутри текста заменены пробелами: {n_text(broken)} знач.")
    if infinite:
        warnings.append(f"Бесконечные значения записаны как пустые: {n_text(infinite)} знач.")
    tick(1.0, f"Записано точек: {n_text(rows)}")
    return PointsWritten(path, rows, titles, path.stat().st_size, warnings)


def _encode(text: str, codec: str) -> bytes:
    try:
        return text.encode(codec)
    except UnicodeEncodeError as exc:
        raise _fail(
            "unrepresentable",
            f"Символ «{exc.object[exc.start]}» нельзя записать в кодировке {codec}.",
            "Замените символ или выберите кодировку UTF-8.",
        ) from exc


def write_points_dat(path: str | Path, layers: Sequence[LineSet], *, progress: Progress | None = None) -> DatWritten:
    """Write the points as a Micromine DAT: EAST, NORTH, RL (binary doubles) and the attributes."""
    import numpy as np

    from bf_core.formats.micromine_write import write_dat

    xyz, columns = _columns(layers)
    coords = [
        Column(title, "number", np_column)
        for title, np_column in zip(("EAST", "NORTH", "RL"), xyz.T.copy(), strict=True)
    ]
    taken = unique_names(["EAST", "NORTH", "RL", *(c.name for c in columns)])[3:]
    for column, title in zip(columns, taken, strict=True):
        column.name = title
    with np.errstate(over="ignore"):  # the display decimals of huge values: the doubles themselves are written as is
        return write_dat(path, [*coords, *columns], progress=progress)


__all__ = ["PointsRead", "PointsWritten", "read_points", "write_points", "write_points_dat"]
