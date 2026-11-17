"""Which table format a file is, and one entry point to read it (ТЗ 7.7, 9.1).

`detect(path, filename=None)` → "dat" | "csv" | "xlsx" | "xls" | "unknown", decided by the first
bytes of the file and then by its extension:
  * the Micromine magic line, or `N VARIABLES` at the start (Standard header) → "dat";
  * a ZIP archive holding a workbook → "xlsx" (also `.xlsm`); an OLE2 file with a workbook → "xls";
    the extension does not matter (`.xls` files that are really `.xlsx`, and the reverse, are common);
  * text (no zero bytes unless UTF-16, not HTML/XML) with a table extension → "csv" — this includes
    a text `.dat` and a tab-separated "`.xls`" exported by web systems;
  * an empty file → by its extension, so that the reader says «Файл пуст»;
  * anything else → "unknown". Micromine `.STR` is the DAT format but holds lines (Э7): "unknown" here.
`filename` is the original name when the file is stored under another one (uploads).

`read_table(path, options, progress, filename)` → `table.TableRead` reads any of them; an unknown
format is a `FormatError("unsupported")`.

Ported from BF `formats/registry.py` (table part: `TABLE_SUFFIXES`) and `importing/service.py`
(`read_table`, `check_supported`); objects (wireframes, lines, rasters, block models, OMF) come with their stages.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from bf_core.formats.errors import FormatError
from bf_core.formats.table import Progress, ReadOptions, TableRead

DAT, CSV, XLSX, XLS, UNKNOWN = "dat", "csv", "xlsx", "xls", "unknown"
TABLE_KINDS = (DAT, CSV, XLSX, XLS)
TEXT_SUFFIXES = (".csv", ".txt", ".tsv", ".prn")
EXCEL_SUFFIXES = (".xlsx", ".xlsm", ".xls")
TABLE_SUFFIXES = (".dat", *TEXT_SUFFIXES, *EXCEL_SUFFIXES)
LABELS = {DAT: "Micromine DAT", CSV: "CSV / TXT", XLSX: "Excel (.xlsx)", XLS: "Excel 97-2003 (.xls)"}

_BY_SUFFIX = {".dat": DAT, ".xlsx": XLSX, ".xlsm": XLSX, ".xls": XLS, **dict.fromkeys(TEXT_SUFFIXES, CSV)}
_MARKUP = (b"<html", b"<!doctype", b"<?xml", b"<table", b"<head", b"<body")
_OLE_BOOKS = tuple(name.encode("utf-16-le") for name in ("Workbook", "Book", "EncryptedPackage"))


def _is_workbook_archive(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return False
    return "xl/workbook.xml" in names or "xl/workbook.bin" in names


def _looks_like_text(head: bytes) -> bool:
    from bf_core.formats.text import _utf16_without_bom

    if head.lstrip(b"\xef\xbb\xbf \t\r\n")[:10].lower().startswith(_MARKUP):
        return False
    if head.startswith((b"\xff\xfe", b"\xfe\xff")) or _utf16_without_bom(head):
        return True
    control = sum(head.count(bytes([b])) for b in (*range(0, 9), *range(14, 26), *range(27, 32)))
    return b"\x00" not in head and control * 100 <= len(head)


def detect(path: str | Path, filename: str | None = None) -> str:
    """Table format of a file: "dat", "csv", "xlsx", "xls" or "unknown" (see the module docstring)."""
    from bf_core.formats.excel import BIFF_BOF, OLE_MAGIC, ZIP_MAGIC
    from bf_core.formats.micromine_header import _STANDARD_RE, MAGIC

    path = Path(path)
    suffix = Path(filename or path.name).suffix.lower()
    with path.open("rb") as f:
        head = f.read(1 << 16)
    if suffix == ".str":
        return UNKNOWN
    if not head:
        return _BY_SUFFIX.get(suffix, UNKNOWN)
    if head.startswith(MAGIC) or _STANDARD_RE.match(head):
        return DAT
    if head.startswith(ZIP_MAGIC):
        return XLSX if _is_workbook_archive(path) or suffix in (".xlsx", ".xlsm") else UNKNOWN
    if head.startswith(OLE_MAGIC):
        if suffix in EXCEL_SUFFIXES:
            return XLS
        with path.open("rb") as f:
            body = f.read(8 << 20)
        return XLS if any(name in body for name in _OLE_BOOKS) else UNKNOWN
    if head[:2] in BIFF_BOF and suffix == ".xls":
        return XLS
    if suffix in TABLE_SUFFIXES and _looks_like_text(head):
        return CSV
    return UNKNOWN


def read_table(
    path: str | Path,
    options: ReadOptions | None = None,
    progress: Progress | None = None,
    filename: str | None = None,
) -> TableRead:
    """Read a table of any supported format. Options apply to text and Excel files; `encoding` also to DAT."""
    path = Path(path)
    options = options or ReadOptions()
    kind = detect(path, filename)
    if kind == DAT:
        from bf_core.formats.micromine import read_dat

        return read_dat(path, progress, encoding=options.encoding)
    if kind == CSV:
        from bf_core.formats.text import read_text

        return read_text(path, options, progress)
    if kind in (XLSX, XLS):
        from bf_core.formats.excel import read_excel

        return read_excel(path, options, progress)
    suffix = Path(filename or path.name).suffix.lower()
    raise FormatError(
        "unsupported",
        f"Формат файла «{suffix or 'без расширения'}» не поддерживается или содержимое не похоже на таблицу.",
        "Подходят: Micromine .DAT, текст .csv / .txt, Excel .xlsx / .xlsm / .xls.",
    )
