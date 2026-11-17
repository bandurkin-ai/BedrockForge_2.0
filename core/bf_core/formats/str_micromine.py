"""Micromine string files: `.STR` and `.PIT` (= STR + the Road field) — reading and writing (ТЗ 9.3; 7.35 Д-13).

An STR is a Micromine data file (`micromine.read_dat`): EAST, NORTH, RL (R 8), JOIN (N 12) and STRING (C 20),
then fields of the user. `read_str(path, progress)` → `str_layers.StrRead`:
  * a change of JOIN or STRING between two records starts a new line (a blank JOIN equals a blank JOIN);
  * layer = STRING text (blank → «Без кода»), in the order of the first line; a line of one vertex is a point;
  * a line is closed when its last vertex repeats the first; vertices are kept as they are in the file
    (a vertex without a coordinate stays NaN and is reported, a file without RL gives Z = NaN, never 0);
  * JOIN is a line attribute; every other field is a line attribute when it does not change along any line,
    otherwise a vertex attribute (Road of a `.PIT` marks segments and usually changes);
  * `header` is the `DatHeader` of the file and `seq` the record number of every vertex.
`write_str(path, layers, like=header)` writes the layers back with the fields, widths, code page and XML of the
source: read → write gives the same bytes (tests/formats/test_str_micromine.py, the two real files included).
Without `like` the layout is the one Micromine writes (BF `write_micromine_str`): EAST/NORTH/RL `R 8 6`,
JOIN `N 12 0`, STRING `C 20 0` (wider when a layer name needs it), then the attributes: whole numbers → N,
other numbers → N with fixed decimals when some are missing and that text keeps every value, else R; text → C.
JOIN of every line is kept when it differs from the line before (with the same STRING), else renumbered.

Ported from BF `formats/lines.py::read_micromine_str` and `formats/export.py::write_micromine_str`. Differences:
flat arrays instead of a Python object per line, attributes that change along a line are kept per vertex,
the order of the file and the header of the source are kept, progress and cancellation, Russian `FormatError`s.
Error codes (besides those of `read_dat` / `write_dat`): no_coordinates, no_lines.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from bf_core.formats.errors import FormatError
from bf_core.formats.lineset import LineSet
from bf_core.formats.micromine import read_dat
from bf_core.formats.micromine_header import DEFAULT_CODEPAGE, FIELD_TYPES, DatHeader, Field, codec_of, default_xml
from bf_core.formats.micromine_write import DatWritten, derive_fields, field_names, write_dat
from bf_core.formats.str_layers import (
    NO_CODE,
    Gathered,
    StrRead,
    attribute_names,
    blank,
    build_layers,
    changes,
    gather,
    n_text,
    norm,
    row_keys,
    scaled,
    strings,
    text_codes,
)
from bf_core.formats.table import Column, Progress, no_progress

if TYPE_CHECKING:
    import numpy as np

EAST_NAMES, NORTH_NAMES, RL_NAMES = ("EAST", "X"), ("NORTH", "Y"), ("RL", "Z", "ELEV")
JOIN, STRING, ROAD = "JOIN", "STRING", "ROAD"
JOIN_WIDTH = 12
STRING_WIDTH = 20
COORD_DECIMALS = 6  # display decimals of R fields in the samples; the value is a full double
_HINT = "Проверьте, что это файл стрингов Micromine (.STR, .PIT); таблицу точек загрузите как DAT."


def _role(names: Sequence[str], wanted: Sequence[str]) -> int | None:
    """Index of the field that plays a role: the first name of `wanted` found (first such field)."""
    upper = [n.strip().upper() for n in names]
    return next((upper.index(w) for w in wanted if w in upper), None)


def _join_breaks(join: Column) -> np.ndarray | None:
    """N fields with text that is not a number read as NaN: different texts still split lines."""
    import numpy as np

    cells = join.bad_cells or {}
    if not cells:
        return None
    out = np.zeros(len(join) - 1, dtype=bool)
    for r in sorted(cells):
        if r > 0 and cells.get(r - 1) != cells[r]:
            out[r - 1] = True
        if r + 1 < len(join) and cells.get(r + 1) != cells[r]:
            out[r] = True
    return out


def read_str(
    path: str | Path, progress: Progress | None = None, *, encoding: str | None = None, chunk_rows: int | None = None
) -> StrRead:
    """Read a Micromine `.STR` or `.PIT` file into line layers (see the module docstring)."""
    import numpy as np

    path = Path(path)
    tick = progress or no_progress
    table = read_dat(path, scaled(tick, 0.0, 0.8), encoding=encoding, chunk_rows=chunk_rows)
    header: DatHeader = table.info.header
    warnings = list(table.warnings)
    columns = table.columns
    names = [c.name for c in columns]
    found = [_role(names, role) for role in (EAST_NAMES, NORTH_NAMES, RL_NAMES, (JOIN,), (STRING,))]
    east, north, rl, join, code = (columns[i] if i is not None else None for i in found)
    if east is None or north is None or east.kind != "number" or north.kind != "number":
        raise FormatError("no_coordinates", "В файле нет числовых полей EAST и NORTH (X и Y).", _HINT)
    if rl is not None and rl.kind != "number":
        rl = None
    n = table.rows
    if n == 0:
        raise FormatError("no_lines", "В файле нет ни одной записи.", "Проверьте, что файл стрингов не пуст.")
    tick(0.8, "Сборка линий")
    z = rl.values if rl is not None else np.full(n, np.nan)
    xyz = np.column_stack([east.values, north.values, z]).astype(np.float64, copy=False)
    if rl is None:
        warnings.append("В файле нет поля RL (Z): высоты линий пустые")
    missing = int((~np.isfinite(xyz[:, :2])).any(axis=1).sum())
    if missing:
        warnings.append(f"{n_text(missing)} вершин без координаты X или Y пропущены")

    starts = np.ones(n, dtype=bool)
    split = np.zeros(n - 1, dtype=bool)
    if join is not None:
        split |= changes(row_keys(join))
        extra = _join_breaks(join) if join.kind == "number" else None
        if extra is not None:
            split |= extra
    if code is not None:
        codes, labels = text_codes(strings(code))
        split |= changes(codes)
        layer_names = [label.strip() or NO_CODE for label in labels] + [NO_CODE]
        if any(norm(label) != label.strip() for label in labels):
            warnings.append("В значениях STRING есть лишние пробелы: в именах слоёв они убраны")
        codes = np.where(codes < 0, len(labels), codes)
    else:
        warnings.append("В файле нет поля STRING: все линии — в одном слое")
        codes, layer_names = np.zeros(n, dtype=np.int64), [NO_CODE]
    starts[1:] = split
    used = {id(c) for c in (east, north, rl, code) if c is not None}
    others = [c for c in columns if id(c) not in used]
    pit = path.suffix.lower() == ".pit"
    if pit and _role([c.name for c in others], (ROAD,)) is None:
        warnings.append("В файле .PIT нет поля Road")
    line_only = (join.name,) if join is not None else ()
    layers = build_layers(xyz, starts, codes, layer_names, others, line_only, scaled(tick, 0.8, 1.0))
    lines = sum(layer.line_count for layer in layers)
    tick(1.0, f"Прочитано линий: {n_text(lines)}, вершин: {n_text(n)}")
    return StrRead("micromine_pit" if pit else "micromine_str", layers, header, header.encoding, n, warnings)


# ---------------------------------------------------------------- writing


def _decimal_digits(values: list[float]) -> int | None:
    """Fixed decimals that write every value back to the same double, or None (BF `_decimal_text`)."""
    decimals = 0
    for v in values:
        text = repr(v)
        if not math.isfinite(v) or "e" in text or "E" in text:
            return None
        decimals = max(decimals, len(text.partition(".")[2]))
    if decimals > 15 or any(float(f"{v:.{decimals}f}") != v or len(f"{v:.{decimals}f}") > 24 for v in values):
        return None
    return decimals


def _attribute_field(name: str, column: Column, encoding: str) -> Field:
    """Field of an attribute the source did not describe (BF `_attribute_column`): a blank N is a missing value
    in Micromine, so whole numbers and short decimals with gaps are N; other numbers R; text C."""
    import numpy as np

    if column.kind == "text":
        return derive_fields([Column(name, "text", column.values)], encoding)[0][0]
    values = column.values
    present = values[~np.isnan(values)]
    if np.isfinite(present).all():
        uniques = np.unique(present).tolist()
        if all(v == math.floor(v) and abs(v) < 1e15 for v in uniques):
            width = max([1, *(len(f"{v:.0f}") for v in uniques)]) + int(bool(np.signbit(present[present == 0]).any()))
            if width <= 24:
                return Field(name, "N", width, 0)
        if len(present) < len(values):
            digits = _decimal_digits(uniques)
            if digits is not None:
                return Field(name, "N", max([1, *(len(f"{v:.{digits}f}") for v in uniques)]), digits)
    return Field(name, "R", 8, COORD_DECIMALS)


def _encoded_len(text: str, encoding: str, what: str) -> int:
    from bf_core.formats.micromine_header import encode_text

    try:
        return len(encode_text(text, encoding))
    except UnicodeEncodeError as exc:
        raise FormatError(
            "unrepresentable",
            f"{what}: символ «{text[exc.start]}» нельзя записать в кодировке {encoding}.",
            "Замените символ или выберите кодовую страницу UTF-8.",
        ) from exc


def _layer_code(layer: LineSet) -> str:
    """STRING of a layer: its name; the layer «Без кода» has a blank STRING."""
    return "" if layer.name == NO_CODE else layer.name


def _joins(wanted: np.ndarray, codes: np.ndarray, width: int) -> np.ndarray:
    """JOIN of every written line: its own when it fits and differs from the line before with the same STRING,
    otherwise a number no line uses (BF `write_micromine_str`). A blank JOIN stays blank on the same terms."""
    import numpy as np

    limit = 10 ** (width - 1)
    with np.errstate(invalid="ignore"):
        ok = np.isnan(wanted) | ((wanted == np.floor(wanted)) & (np.abs(wanted) < limit))
    kept = wanted[ok & ~np.isnan(wanted)]
    fresh = int(max(0.0, kept.max())) if kept.size else 0
    out = wanted.copy()
    w, c, good = wanted.tolist(), codes.tolist(), ok.tolist()
    prev = math.nan
    for i, value in enumerate(w):
        clash = i > 0 and c[i] == c[i - 1] and (value == prev or (value != value and prev != prev))
        if not good[i] or clash:
            fresh += 1
            value = float(fresh)
        out[i] = value
        prev = value
    return out


def _source_fields(like: DatHeader | None, codes: list[str], encoding: str) -> tuple[list[Field], list[str]]:
    """Fields to write and the attribute name each one takes its values from (the column names of the reader)."""
    if like is not None:
        pairs = [(f, n) for f, n in zip(like.fields, like.names, strict=True) if f.kind in FIELD_TYPES]
        return [f for f, _ in pairs], [n for _, n in pairs]
    width = max([STRING_WIDTH, *(_encoded_len(t, encoding, f"Имя слоя «{t}»") for t in codes)])
    fields = [Field("EAST", "R", 8, COORD_DECIMALS), Field("NORTH", "R", 8, COORD_DECIMALS)]
    fields += [Field("RL", "R", 8, COORD_DECIMALS), Field(JOIN, "N", JOIN_WIDTH, 0), Field(STRING, "C", width, 0)]
    return fields, [f.name for f in fields]


def _columns(
    layers: Sequence[LineSet], g: Gathered, fields: list[Field], names: list[str], codes: list[str], warnings: list[str]
) -> tuple[list[Column], set[str]]:
    """Values of every field; the attributes used. A field takes the attribute of its name (as LineSet keeps it)."""
    import numpy as np
    import pyarrow as pa

    attributes = dict(attribute_names(layers))
    by_name = {norm(name).casefold(): name for name in reversed(list(attributes))}
    join_attr = next((name for name in attributes if name.strip().upper() == JOIN), None)
    used = {join_attr} if join_attr is not None else set()
    first = g.first
    index = {t: i for i, t in reversed(list(enumerate(codes)))}
    code_of_line = np.asarray([index[t] for t in codes], dtype=np.int64)[g.layer[first]]
    roles = {
        "x": _role(names, EAST_NAMES),
        "y": _role(names, NORTH_NAMES),
        "z": _role(names, RL_NAMES),
        "join": _role(names, (JOIN,)),
        "string": _role(names, (STRING,)),
    }
    out: list[Column] = []
    m = len(g.xyz)
    for i, (f, name) in enumerate(zip(fields, names, strict=True)):
        if i in (roles["x"], roles["y"], roles["z"]):
            axis = [roles["x"], roles["y"], roles["z"]].index(i)
            out.append(Column(name, "number", g.xyz[:, axis]))
        elif i == roles["join"]:
            own = g.column(layers, join_attr, attributes[join_attr]) if join_attr is not None else None
            if f.kind == "C":
                out.append(Column(name, "text", strings(own)) if own is not None else blank("text", m, name))
                continue
            wanted = own.values[first] if own is not None and own.kind == "number" else np.full(len(first), np.nan)
            joins = _joins(wanted, code_of_line, f.width if f.kind == "N" else JOIN_WIDTH)
            renumbered = int((~((joins == wanted) | (np.isnan(joins) & np.isnan(wanted)))).sum())
            if renumbered and join_attr is not None:
                warnings.append(f"{n_text(renumbered)} линиям присвоен новый JOIN: прежний совпадал с соседним")
            out.append(Column(name, "number", np.repeat(joins, g.counts)))
        elif i == roles["string"]:
            labels = pa.array(codes, type=pa.string()).take(pa.array(g.layer, type=pa.int64()))
            out.append(Column(name, "text", pa.chunked_array([labels])))
        elif norm(name).casefold() in by_name:
            attr = by_name[norm(name).casefold()]
            used.add(attr)
            column = g.column(layers, attr, attributes[attr])
            same = Column(name, column.kind, column.values)
            out.append(Column(name, "text", strings(column)) if f.kind == "C" else same)
        else:
            out.append(blank("text" if f.kind == "C" else "number", m, name))
    return out, used


def write_str(
    path: str | Path,
    layers: Sequence[LineSet],
    *,
    like: DatHeader | None = None,
    codepage: str = DEFAULT_CODEPAGE,
    progress: Progress | None = None,
) -> DatWritten:
    """Write line layers as one Micromine `.STR` / `.PIT` file (see the module docstring).

    `like`: the header of the file the layers were read from — its fields, code page and XML are reused, so an
    unchanged file is written byte for byte. Attributes the source did not have are added as new fields.
    """
    import numpy as np

    tick = progress or no_progress
    tick(0.0, "Подготовка линий")
    layers = [layer for layer in layers if layer.line_count and layer.point_count]
    if not layers:
        raise FormatError("no_lines", "Нет линий для записи в STR.", "Выберите слой, в котором есть линии.")
    encoding = like.encoding if like is not None else codec_of(codepage)
    if encoding is None:
        raise FormatError("unknown_encoding", f"Неизвестная кодовая страница: {codepage}", "Выберите Windows-1251.")
    g = gather(layers)
    warnings: list[str] = []
    codes = [_layer_code(layer) for layer in layers]
    fields, names = _source_fields(like, codes, encoding)

    # Fields without which the lines could not be told apart on reading are added to a source layout.
    same_code = np.asarray([codes[k] for k in g.layer[g.first].tolist()], dtype=object)
    added: list[Field] = []
    if _role(names, RL_NAMES) is None and bool(np.isfinite(g.xyz[:, 2]).any()):
        added.append(Field("RL", "R", 8, COORD_DECIMALS))
    if _role(names, (STRING,)) is None and len(set(codes)) > 1:
        added.append(Field(STRING, "C", max([1, *(_encoded_len(t, encoding, "STRING") for t in codes)]), 0))
    if _role(names, (JOIN,)) is None and bool((same_code[1:] == same_code[:-1]).any()):
        added.append(Field(JOIN, "N", JOIN_WIDTH, 0))
    if added:
        warnings.append(f"Добавлены поля {', '.join(f.name for f in added)}: без них линии файла нельзя различить")
    fields, names = [*fields, *added], [*names, *(f.name for f in added)]
    columns, taken = _columns(layers, g, fields, names, codes, warnings)
    tick(0.1, "Запись STR")
    new = [(name, scope) for name, scope in attribute_names(layers) if name not in taken]
    if new:
        short, notes = field_names([*(f.name for f in fields), *(name for name, _ in new)], encoding)
        for (name, scope), field_name in zip(new, short[len(fields) :], strict=True):
            column = g.column(layers, name, scope)
            fields.append(_attribute_field(field_name, column, encoding))
            columns.append(column)
        warnings += [note for note in notes if any(f"«{name.strip()}»" in note for name, _ in new)]
        if like is not None:
            warnings.append(f"Добавлены поля атрибутов: {', '.join(name for name, _ in new)}")

    if like is None:
        written = write_dat(path, columns, fields=fields, codepage=codepage, progress=scaled(tick, 0.1, 1.0))
    else:
        unchanged = [f.name for f in fields] == [f.name for f in like.fields if f.kind in FIELD_TYPES]
        xml = like.xml if unchanged and like.xml else None
        if xml is None and like.extended:
            names_out = [f.name for f in fields]
            xml = default_xml(names_out, like.codepage or DEFAULT_CODEPAGE, like.title, like.description)
        written = write_dat(path, columns, like=like, fields=fields, xml=xml, progress=scaled(tick, 0.1, 1.0))
    written.warnings[:0] = warnings
    return written


__all__ = ["StrRead", "read_str", "write_str"]
