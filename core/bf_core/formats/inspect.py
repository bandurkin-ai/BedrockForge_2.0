"""Quick previews for the import wizard: what is inside a table file, without reading all of it.

`inspect_table(path, options, filename, limit)` → `TablePreview`: the format, how the file would be
read (encoding, delimiter, sheets — `info`), and the first `limit` rows as text exactly as they
are in the file, each with the number to give as `ReadOptions.header_row`. Bounded in time and
memory: a text file is examined by its first block of 1 МБ (the encoding by at most 64 МБ), a DAT by
its header and first records, a workbook by the first rows of one sheet.

Ported from BF `formats/inspect.py` (the Micromine header part of `inspect_str`); previews of
CSV and Excel are new (in BF the whole file was read to show ten rows). Previews of wireframes,
DXF, rasters and block models come with their stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bf_core.formats import registry
from bf_core.formats.errors import FormatError
from bf_core.formats.table import ReadInfo, ReadOptions, _cell, _text_of

SNIFF_LIMIT = 64 << 20


@dataclass
class TablePreview:
    kind: str  # "dat" | "csv" | "xlsx" | "xls"
    info: ReadInfo
    rows: list[list[str]]  # the first rows as text ("" = empty cell); for DAT — records, the names are in `fields`
    numbers: list[int]  # the number of each row for ReadOptions.header_row (0 for DAT records)
    summary: str  # one line for the wizard, in Russian
    fields: list[dict[str, Any]] = field(default_factory=list)  # DAT: name, type, width, decimals, description
    estimated_rows: int | None = None  # exact for DAT, an estimate for text, None for Excel
    warnings: list[str] = field(default_factory=list)


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def _dat(path: Path, options: ReadOptions, limit: int) -> TablePreview:
    from bf_core.formats.micromine import read_dat

    table = read_dat(path, encoding=options.encoding, max_rows=limit)
    header = table.info.header
    total = (path.stat().st_size - header.data_offset) // header.record_size
    texts = [c.text() for c in table.columns]
    rows = [[col[r] or "" for col in texts] for r in range(table.rows)]
    fields = [
        {"name": n, "type": f.kind, "width": f.width, "decimals": f.decimals, "description": f.description}
        for n, f in zip(header.names, header.fields, strict=True)
    ]
    names = ", ".join(header.names[:12]) + (" …" if len(header.names) > 12 else "")
    summary = f"Micromine DAT ({header.codepage or 'без кодовой страницы'}): записей {_n(total)}, поля: {names}"
    return TablePreview("dat", table.info, rows, [0] * len(rows), summary, fields, total, table.warnings)


def _csv(path: Path, options: ReadOptions, limit: int) -> TablePreview:
    from bf_core.formats import text

    text._check(options)
    size = path.stat().st_size
    if size == 0:
        raise FormatError("empty_file", "Файл пуст.", "Проверьте, что файл скопирован полностью.")
    encoding = options.encoding or text.sniff_encoding(path, SNIFF_LIMIT)
    skip = max(options.header_row - 1, 0)
    lines, done = next(((block, done) for block, _, done in text.line_blocks(path, encoding)), ([], size))
    delimiter = options.delimiter or text.detect_delimiter(lines[skip : skip + 200])
    rows = [[c.strip() for c in r] for r in text.split_lines(lines[:limit], delimiter, True)]
    estimate = round(len(lines) * size / done) if done else len(lines)  # exact when the file is one block
    info = ReadInfo("csv", encoding=encoding, delimiter=delimiter, header_row=options.header_row)
    shown_delimiter = {"\t": "табуляция", "whitespace": "пробелы"}.get(delimiter, f"«{delimiter}»")
    summary = f"Текст ({encoding}, разделитель {shown_delimiter}): строк ≈ {_n(estimate)}"
    return TablePreview("csv", info, rows, list(range(1, len(rows) + 1)), summary, estimated_rows=estimate)


def _excel(path: Path, options: ReadOptions, limit: int) -> TablePreview:
    from bf_core.formats import excel

    kind, book = excel._open(path)
    try:
        sheets = book.sheets
        if not sheets:
            raise excel._broken("в книге нет листов")
        if options.sheet is not None and options.sheet not in sheets:
            listed = ", ".join(f"«{s}»" for s in sheets[:20])
            raise FormatError("no_sheet", f"В книге нет листа «{options.sheet}»", f"Листы книги: {listed}.")
        name = options.sheet or next((s for s in sheets if excel._has_data(book, s, lambda f, m: None)), sheets[0])
        rows: list[list[str]] = []
        source = excel._guarded(book.rows(name))
        for row in source:
            rows.append([_text_of(_cell(v)) for v in row])
            if len(rows) >= limit:
                break
        source.close()
    finally:
        book.close()
    while rows and not any(rows[-1]):
        rows.pop()
    info = ReadInfo(kind, header_row=options.header_row, sheet=name, sheets=sheets)
    summary = f"{registry.LABELS[kind]}: лист «{name}», листов в книге: {len(sheets)}"
    return TablePreview(kind, info, rows, list(range(1, len(rows) + 1)), summary)


def inspect_table(
    path: str | Path, options: ReadOptions | None = None, filename: str | None = None, limit: int = 50
) -> TablePreview:
    """The first rows of a table file and how it would be read. FormatError when it cannot be read."""
    path = Path(path)
    options = options or ReadOptions()
    kind = registry.detect(path, filename)
    if kind == registry.DAT:
        return _dat(path, options, limit)
    if kind == registry.CSV:
        return _csv(path, options, limit)
    if kind in (registry.XLSX, registry.XLS):
        return _excel(path, options, limit)
    suffix = Path(filename or path.name).suffix.lower()
    raise FormatError(
        "unsupported",
        f"Формат файла «{suffix or 'без расширения'}» не поддерживается или содержимое не похоже на таблицу.",
        "Подходят: Micromine .DAT, текст .csv / .txt, Excel .xlsx / .xlsm / .xls.",
    )
