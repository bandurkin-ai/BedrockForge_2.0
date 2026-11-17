"""Writer of Micromine data files (.DAT) with Extended and Standard headers — ТЗ 9.12 (new in BF2).

`write_dat(path, columns, like=header)` writes the columns of a `TableRead` back with the field
types, widths, decimals, `|descriptions`, code page and XML of the source header: read → write →
read gives the same values bit for bit, and files written by Micromine or by this writer come out
byte for byte (tests/formats/test_micromine_write.py; the layout follows BF `write_micromine_str`).

Without `like` and `fields` the fields are derived from the columns: text → `C` as wide as the
longest value, numbers → `R` (binary double, the value is kept exactly; DECIMALS is only how
Micromine shows it). Names longer than 10 characters are shortened and reported in `warnings`.

Rules of the fixed-width record:
  * C: text in the code page, padded with spaces; null = blank. Control characters become spaces.
  * R, F, L, S, B: binary little-endian; L, S, B take only whole numbers in range (no NaN).
  * N: the number as text, left-aligned, with DECIMALS digits when that fits the field and parses
    back to the same double, otherwise the shortest text that does; NaN = blank.
What cannot be represented (a character missing in the code page, a value wider than its field)
is a `FormatError`, never a silent loss. The file appears under its name only when complete.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bf_core.formats.errors import FormatError
from bf_core.formats.micromine_header import (
    BINARY_SIZES,
    BINARY_TYPES,
    BOCU,
    DEFAULT_CODEPAGE,
    FIELD_TYPES,
    MAX_WIDTH,
    NAME_WIDTH,
    TEXT_LIMIT,
    DatHeader,
    Field,
    build_header,
    codec_of,
    default_xml,
    encode_text,
)
from bf_core.formats.table import Column, Progress, no_progress

if TYPE_CHECKING:
    import numpy as np

_CONTROL = re.compile(r"[\x00-\x1f]")
_NAME_JUNK = re.compile(r"[\x00-\x1f|]")
_CHUNK_BYTES = 8 << 20
_MAX_DECIMALS = 8
_INT_RANGE = {"L": (-(2**31), 2**31 - 1), "S": (-(2**15), 2**15 - 1), "B": (0, 255)}


@dataclass
class DatWritten:
    path: Path
    rows: int
    fields: list[Field]
    size: int
    warnings: list[str] = field(default_factory=list)


def _cannot(message: str, hint: str = "") -> FormatError:
    return FormatError("unrepresentable", message, hint or "Измените значение или параметры поля и повторите экспорт.")


def _encode(text: str, encoding: str, what: str) -> bytes:
    try:
        return encode_text(text, encoding)
    except UnicodeEncodeError as exc:
        shown = text if len(text) <= 40 else text[:40] + "…"
        raise _cannot(
            f"{what}: в «{shown}» есть символы, которых нет в кодировке {encoding}.",
            "Выберите кодовую страницу UTF-8 или замените эти символы.",
        ) from exc


def field_names(names: Sequence[str], encoding: str) -> tuple[list[str], list[str]]:
    """Field names of at most 10 characters, unique without regard to case (BF `_field_names`), and warnings."""
    limit = (lambda s: len(s)) if encoding == BOCU else (lambda s: len(s.encode(encoding, "replace")))
    used: set[str] = set()
    out: list[str] = []
    warnings: list[str] = []
    for n, original in enumerate(names, start=1):
        base = _NAME_JUNK.sub(" ", original).strip() or f"FIELD{n}"
        _encode(base, encoding, f"Имя поля «{original}»")
        name = base
        while limit(name) > NAME_WIDTH:
            name = name[:-1].rstrip()
        k = 1
        while name.upper() in used:
            k += 1
            name = base[: len(name)]
            while limit(name + str(k)) > NAME_WIDTH:
                name = name[:-1].rstrip()
            name += str(k)
        used.add(name.upper())
        out.append(name)
        if name != original:
            reason = "в Micromine имя поля не длиннее 10 символов и не повторяется"
            warnings.append(f"Имя поля «{original.strip()}» записано как «{name}»: {reason}")
    return out, warnings


def _decimals(values: np.ndarray) -> int:
    """The fewest decimals (0…8) that show every value in full."""
    import numpy as np

    finite = values[np.isfinite(values)]
    for d in range(_MAX_DECIMALS):
        if np.array_equal(np.round(finite, d), finite):
            return d
    return _MAX_DECIMALS


def _clean(text: str) -> str:
    """One line of text: records are fixed-width, line breaks and tabs inside a value become spaces."""
    return _CONTROL.sub(" ", text).rstrip()


def _labels(column: Column, start: int, count: int) -> tuple[list[str], np.ndarray]:
    """Distinct texts of rows [start, start + count) and the index of each row's text (−1 = blank)."""
    import numpy as np
    import pyarrow as pa

    if column.kind == "number":
        from bf_core.formats.numbers import number_text

        uniques, inverse = np.unique(column.values[start : start + count], return_inverse=True, equal_nan=True)
        labels = [number_text(u) for u in uniques.tolist()]
        return labels, inverse.ravel().astype(np.int64)
    chunk = column.values.slice(start, count)
    cells = chunk.combine_chunks() if isinstance(chunk, pa.ChunkedArray) else chunk
    encoded = cells.dictionary_encode()
    return encoded.dictionary.to_pylist(), encoded.indices.fill_null(-1).to_numpy(zero_copy_only=False).astype(np.int64)


def derive_fields(columns: Sequence[Column], encoding: str) -> tuple[list[Field], list[str]]:
    """Fields for columns that have no Micromine description yet: text → C, numbers → R."""
    names, warnings = field_names([c.name for c in columns], encoding)
    fields: list[Field] = []
    for name, column in zip(names, columns, strict=True):
        if column.kind == "number":
            fields.append(Field(name, "R", 8, _decimals(column.values)))
            continue
        width = 1
        for start in range(0, len(column), 1 << 18):
            labels, _ = _labels(column, start, 1 << 18)
            width = max([width, *(len(_encode(_clean(t), encoding, f"Колонка «{column.name}»")) for t in labels)])
        if width > TEXT_LIMIT:
            raise _cannot(
                f"Колонка «{column.name}»: значение длиннее {TEXT_LIMIT} символов не помещается в поле Micromine."
            )
        fields.append(Field(name, "C", width, 0))
    return fields, warnings


def _check_fields(fields: Sequence[Field], columns: Sequence[Column], encoding: str) -> None:
    if len(fields) != len(columns):
        raise FormatError(
            "broken", f"Полей {len(fields)}, а колонок {len(columns)}.", "Передайте по одному полю на колонку."
        )
    if len({len(c) for c in columns}) > 1:
        raise FormatError("broken", "Колонки разной длины.", "Все колонки таблицы должны иметь одно число строк.")
    for f, column in zip(fields, columns, strict=True):
        raw = _encode(f.name, encoding, f"Имя поля «{f.name}»")
        if (len(f.name) if encoding == BOCU else len(raw)) > NAME_WIDTH or _NAME_JUNK.search(f.name):
            raise _cannot(f"Имя поля «{f.name}» не подходит для Micromine: до {NAME_WIDTH} символов, без «|».")
        if _CONTROL.search(f.description):
            raise _cannot(f"Описание поля «{f.name}» содержит перевод строки.")
        _encode(f.description, encoding, f"Описание поля «{f.name}»")
        if f.kind not in FIELD_TYPES or not 1 <= f.width <= MAX_WIDTH or f.decimals < 0:
            raise FormatError(
                "broken", f"Поле «{f.name}»: тип {f.kind}, ширина {f.width} недопустимы.", "Типы: C, N, R, F, L, S, B."
            )
        if f.kind in BINARY_SIZES and f.width != BINARY_SIZES[f.kind]:
            raise FormatError(
                "broken", f"Поле «{f.name}» типа {f.kind} занимает {BINARY_SIZES[f.kind]} байт.", "Исправьте ширину."
            )
        if f.kind != "C" and column.kind != "number":
            raise _cannot(f"Поле «{f.name}» типа {f.kind} числовое, а колонка «{column.name}» текстовая.")


def _text_field(column: Column, f: Field, start: int, count: int, encoding: str) -> np.ndarray:
    import numpy as np

    labels, index = _labels(column, start, count)
    table = np.full(len(labels) + 1, b" " * f.width, dtype=f"S{f.width}")  # the last one is the blank
    for i, label in enumerate(labels):
        raw = _encode(_clean(label), encoding, f"Поле «{f.name}»")
        if len(raw) > f.width:
            raise _cannot(f"Поле «{f.name}» (ширина {f.width}): значение «{label[:40]}» не помещается.")
        table[i] = raw.ljust(f.width)
    return table[index]  # type: ignore[no-any-return]


def _number_field(values: np.ndarray, f: Field, encoding: str) -> np.ndarray:
    """Numbers as left-aligned text that parses back to the same doubles; NaN = blank."""
    import numpy as np

    out = np.full(len(values), b" " * f.width, dtype=f"S{f.width}")
    present = np.flatnonzero(~np.isnan(values))
    if not np.isfinite(values[present]).all():
        raise _cannot(f"Поле «{f.name}»: бесконечность нельзя записать числом.")
    texts = []
    for x in values[present].tolist():
        text = f"{x:.{f.decimals}f}"
        if float(text) != x or len(text) > f.width or (x == 0.0 and text.startswith("-") != repr(x).startswith("-")):
            text = repr(x)  # the shortest text that parses back to the same double
        if len(text) > f.width:
            raise _cannot(f"Поле «{f.name}» (N, ширина {f.width}): число {text} не помещается.")
        texts.append(
            encode_text(text, encoding).ljust(f.width) if encoding == BOCU else text.ljust(f.width).encode("ascii")
        )
    if texts:
        out[present] = texts
    return out


def _binary_field(values: np.ndarray, f: Field, warnings: list[str]) -> np.ndarray:
    import numpy as np

    if f.kind == "R":
        return values
    if f.kind == "F":
        with np.errstate(over="ignore"):
            single = values.astype(np.float32)
        ok = np.isnan(values) | (single.astype(np.float64) == values)
        if not ok.all():
            note = f"Поле «{f.name}» (F, float32): {int((~ok).sum())} знач. округлены до одинарной точности"
            if not any(w.startswith(f"Поле «{f.name}» (F") for w in warnings):
                warnings.append(note)
        return single  # type: ignore[no-any-return]
    lo, hi = _INT_RANGE[f.kind]
    whole = np.isfinite(values) & (values == np.floor(values)) & (values >= lo) & (values <= hi)
    if not whole.all():
        bad = values[~whole][0]
        raise _cannot(
            f"Поле «{f.name}» типа {f.kind} хранит только целые от {lo} до {hi}; значение {bad} записать нельзя."
        )
    return values.astype(BINARY_TYPES[f.kind])  # type: ignore[no-any-return]


def write_dat(
    path: str | Path,
    columns: Sequence[Column],
    *,
    like: DatHeader | None = None,
    fields: Sequence[Field] | None = None,
    extended: bool = True,
    codepage: str = DEFAULT_CODEPAGE,
    title: str = "",
    description: str = "",
    xml: str | None = None,
    progress: Progress | None = None,
) -> DatWritten:
    """Write columns as a Micromine DAT file.

    `like`: the header of a file read by `read_dat` — its fields, header kind, code page and XML are reused.
    `fields`: explicit field descriptions, one per column (their names are written, not the column names).
    `extended=False` writes the Standard header (no XML pages; the code page is then not recorded).
    `xml`: metadata to write as it is instead of the default title / encoding / field classes.
    """
    import numpy as np

    path = Path(path)
    tick = progress or no_progress
    warnings: list[str] = []
    if like is not None:
        fields = fields if fields is not None else [f for f in like.fields if f.kind in FIELD_TYPES]
        extended, encoding = like.extended, like.encoding
        codepage = like.codepage or codepage
        xml = xml if xml is not None else (like.xml or None)
    else:
        found = codec_of(codepage)
        if found is None:
            raise FormatError(
                "unknown_encoding", f"Неизвестная кодовая страница: {codepage}", "Выберите Windows-1251 или UTF-8."
            )
        encoding = found
    if fields is None:
        fields, warnings = derive_fields(columns, encoding)
    fields = list(fields)
    _check_fields(fields, columns, encoding)
    rows = len(columns[0]) if columns else 0
    if not extended:
        xml = None
    elif xml is None:
        xml = default_xml([f.name for f in fields], codepage, title, description)
    head = build_header(fields, encoding, xml)

    dtype = np.dtype(
        {
            "names": [f"f{i}" for i in range(len(fields))] + ["eol"],
            "formats": [BINARY_TYPES.get(f.kind, f"S{f.width}") for f in fields] + ["S2"],
        }
    )
    step = max(1, min(1 << 16, _CHUNK_BYTES // dtype.itemsize))  # a tick at least every ~0.5 s
    part = path.with_name(path.name + ".part")
    try:
        with part.open("wb") as out:
            out.write(head)
            for a in range(0, rows, step):
                tick(a / rows, f"Запись записей: {a} из {rows}")
                count = min(step, rows - a)
                block = np.empty(count, dtype=dtype)
                block["eol"] = b"\r\n"
                for i, (f, column) in enumerate(zip(fields, columns, strict=True)):
                    data: Any
                    if f.kind == "C":
                        data = _text_field(column, f, a, count, encoding)
                    elif f.kind == "N":
                        data = _number_field(column.values[a : a + count], f, encoding)
                    else:
                        data = _binary_field(column.values[a : a + count], f, warnings)
                    block[f"f{i}"] = data
                out.write(block.tobytes())
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    tick(1.0, f"Записано записей: {rows}")
    return DatWritten(path, rows, fields, path.stat().st_size, warnings)
