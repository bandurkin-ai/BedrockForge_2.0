"""Excel workbooks: `.xlsx` / `.xlsm` (openpyxl, streaming) and `.xls` (xlrd 2.0.2, BIFF2–8) — ТЗ 7.7, 9.6, 9.12.

`read_excel(path, options, progress)` → `table.TableRead`. Which library reads the file is decided
by its content (ZIP → openpyxl, OLE2 or a bare BIFF stream → xlrd), not by the extension.

  * Sheet: `options.sheet`, or the first sheet that has any data; `info.sheets` lists the worksheets
    (chart sheets are not offered).
  * `header_row` is the number of the row in the sheet (as Excel shows it); empty rows at the end
    of the sheet are not rows of the table.
  * Cells keep their types: numbers stay numbers, dates become ISO text (`2024-05-17`,
    `2024-06-01 14:30:00`, times `08:30:00`), text cells go through `formats.numbers` like CSV
    (`466543,962`, `<0.01`, `н/д`), error cells are their text (`#N/A` is "no data").
  * `.xlsx` is read row by row (`read_only`), ignoring the `<dimension>` record, which some
    exporters write wrong; formulas give their cached values. `.xls` holds at most 65 536 rows:
    xlrd loads the chosen sheet whole.

Ported from BF `formats/excel.py`. New: `.xls` (BF refused it), streaming with progress and
cancellation, a second pass for a column that turns out to be text late in the sheet, refusal of
archive "bombs", encrypted and `.xlsb` books with advice, `FormatError` instead of `ValueError`.
"""

from __future__ import annotations

import zipfile
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any

from bf_core.formats.errors import FormatError
from bf_core.formats.table import Progress, ReadInfo, ReadOptions, TableBuilder, TableRead, no_progress

ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
BIFF_BOF = (b"\x09\x08", b"\x09\x04", b"\x09\x02", b"\x09\x00")  # BOF records of BIFF5-8, 4, 3, 2
CHUNK_ROWS = 20_000  # rows given to the builder at a time
TICK_CELLS = 20_000  # cells between two progress reports (openpyxl reads about 10^5 cells a second)
MAX_UNPACKED = 4 << 30  # bytes of all parts of an .xlsx after unpacking
MAX_RATIO = 200  # compression ratio of a big part: sheets compress 10–50 times, a "bomb" 1000 times
BIG_PART = 64 << 20
MAX_XLS = 256 << 20  # xlrd reads the file into memory

_HINT_RESAVE = "Откройте файл в Excel и сохраните его заново как «Книга Excel (.xlsx)»."
_ENCRYPTED = "EncryptedPackage".encode("utf-16-le")


def _broken(detail: str = "") -> FormatError:
    extra = f" ({detail})" if detail else ""
    return FormatError("broken", f"Файл Excel повреждён, обрезан или это не книга Excel{extra}.", _HINT_RESAVE)


def book_kind(path: Path) -> str:
    """ "xlsx" or "xls" by the first bytes of the file; FormatError for anything else."""
    with path.open("rb") as f:
        head = f.read(8)
    if not head:
        raise FormatError("empty_file", "Файл пуст.", "Проверьте, что файл скопирован полностью.")
    if head.startswith(ZIP_MAGIC):
        return "xlsx"
    if head.startswith(OLE_MAGIC) or head[:2] in BIFF_BOF:
        return "xls"
    raise _broken()


def _trim(row: Any) -> list[Any]:
    """A row without the empty cells at its end (formatted but empty columns)."""
    n = len(row)
    while n and row[n - 1] in (None, ""):
        n -= 1
    return list(row[:n])


class _Xlsx:
    def __init__(self, path: Path) -> None:
        from openpyxl import load_workbook

        self._check_archive(path)
        # A file object, not the path: openpyxl refuses a workbook whose name ends with ".xls".
        self._file = path.open("rb")
        try:
            self.book = load_workbook(self._file, read_only=True, data_only=True, keep_links=False)
        except Exception as exc:  # noqa: BLE001 — whatever openpyxl raises on a damaged book
            self._file.close()
            raise _broken(type(exc).__name__) from exc
        self.sheets = [ws.title for ws in self.book.worksheets]

    @staticmethod
    def _check_archive(path: Path) -> None:
        try:
            with zipfile.ZipFile(path) as archive:
                parts = archive.infolist()
        except (zipfile.BadZipFile, OSError, ValueError) as exc:
            raise _broken("архив не читается") from exc
        names = {p.filename for p in parts}
        if "xl/workbook.bin" in names:
            raise FormatError("unsupported", "Двоичная книга Excel (.xlsb) не поддерживается.", _HINT_RESAVE)
        if "xl/workbook.xml" not in names:
            raise _broken("в архиве нет книги")
        total = sum(p.file_size for p in parts)
        bomb = any(p.file_size > BIG_PART and p.file_size > MAX_RATIO * max(p.compress_size, 1) for p in parts)
        if bomb or total > MAX_UNPACKED:
            raise FormatError(
                "too_large",
                f"Файл Excel распаковывается в {total / (1 << 30):.1f} ГБ: это слишком много или «архивная бомба».",
                "Сохраните нужный лист в отдельную книгу или в CSV.",
            )

    def rows(self, name: str) -> Iterator[Any]:
        sheet = self.book[name]
        sheet.reset_dimensions()  # do not trust <dimension>: read what is there
        return sheet.iter_rows(values_only=True)  # type: ignore[no-any-return]

    def close(self) -> None:
        self.book.close()
        self._file.close()


class _Xls:
    def __init__(self, path: Path) -> None:
        import xlrd

        if path.stat().st_size > MAX_XLS:
            raise FormatError(
                "too_large",
                f"Файл .xls больше {MAX_XLS >> 20} МБ.",
                "Сохраните книгу как .xlsx или нужный лист как CSV.",
            )
        try:
            self.book = xlrd.open_workbook(str(path), on_demand=True, formatting_info=False)
        except Exception as exc:  # noqa: BLE001 — XLRDError, CompDocError, struct.error … on a damaged book
            text = str(exc)
            if "encrypted" in text.lower() or self._has_encrypted_package(path):
                raise FormatError(
                    "encrypted",
                    "Книга Excel защищена паролем.",
                    "Снимите пароль в Excel (Файл → Сведения → Защитить книгу) и сохраните файл заново.",
                ) from exc
            raise _broken(text[:80]) from exc
        self.sheets = list(self.book.sheet_names())
        self._xlrd = xlrd

    @staticmethod
    def _has_encrypted_package(path: Path) -> bool:
        with path.open("rb") as f:
            return _ENCRYPTED in f.read(4 << 20)

    def _value(self, kind: int, value: Any) -> Any:
        xlrd = self._xlrd
        if kind in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
            return None
        if kind == xlrd.XL_CELL_DATE:
            try:
                moment = xlrd.xldate_as_datetime(value, self.book.datemode)
            except Exception:  # noqa: BLE001 — a number that is not a date: keep the number
                return value
            return moment.time() if 0 <= value < 1 else moment
        if kind == xlrd.XL_CELL_BOOLEAN:
            return bool(value)
        if kind == xlrd.XL_CELL_ERROR:
            return xlrd.error_text_from_code.get(value, "#ERR")
        return value

    def rows(self, name: str) -> Iterator[Any]:
        sheet = self.book.sheet_by_name(name)
        try:
            for r in range(sheet.nrows):
                yield [self._value(k, v) for k, v in zip(sheet.row_types(r), sheet.row_values(r), strict=True)]
        finally:
            self.book.unload_sheet(name)

    def close(self) -> None:
        self.book.release_resources()


def _guarded(rows: Iterator[Any]) -> Generator[list[Any], None, None]:
    """Rows of a sheet; whatever the library raises on damaged data becomes a FormatError."""
    while True:
        try:
            row = next(rows)
        except StopIteration:
            return
        except Exception as exc:  # noqa: BLE001 — zlib, XML, struct errors of a damaged sheet
            raise _broken(type(exc).__name__) from exc
        yield _trim(row)


def _open(path: Path) -> tuple[str, _Xlsx | _Xls]:
    kind = book_kind(path)
    return kind, (_Xlsx(path) if kind == "xlsx" else _Xls(path))


def sheet_names(path: str | Path) -> list[str]:
    """Worksheets of a workbook, in order."""
    _, book = _open(Path(path))
    try:
        return book.sheets
    finally:
        book.close()


def _has_data(book: _Xlsx | _Xls, name: str, tick: Progress) -> bool:
    for n, row in enumerate(_guarded(book.rows(name))):
        if row:
            return True
        if n % 5000 == 0:
            tick(0.0, f"Поиск листа с данными: «{name}»")
    return False


def read_excel(path: str | Path, options: ReadOptions | None = None, progress: Progress | None = None) -> TableRead:
    """Read one sheet of an Excel workbook (.xlsx, .xlsm, .xls)."""
    path = Path(path)
    options = options or ReadOptions()
    tick = progress or no_progress
    tick(0.0, "Открытие книги Excel")
    kind, book = _open(path)
    try:
        sheets = book.sheets
        if not sheets:
            raise _broken("в книге нет листов")
        if options.sheet is not None and options.sheet not in sheets:
            listed = ", ".join(f"«{s}»" for s in sheets[:20])
            raise FormatError("no_sheet", f"В книге нет листа «{options.sheet}»", f"Листы книги: {listed}.")
        # By default the first sheet that has any data.
        name = options.sheet or next((s for s in sheets if _has_data(book, s, tick)), sheets[0])
        late: set[int] = set()
        base = 0.0
        for _ in range(3):
            builder = TableBuilder(options.header_row, options.text_columns, sorted(late), trim_trailing_empty=True)
            chunk: list[list[Any]] = []
            count = cells = 0
            fraction = base
            for row in _guarded(book.rows(name)):
                chunk.append(row)
                count += 1
                cells += len(row)
                if cells >= TICK_CELLS or len(chunk) >= CHUNK_ROWS:
                    cells = 0
                    # The number of rows is not known in advance: the bar slows down as the sheet goes on.
                    fraction = base + (0.95 - base) * count / (count + 20_000)
                    tick(fraction, f"Чтение листа «{name}»: {count:,} строк".replace(",", " "))
                if len(chunk) >= CHUNK_ROWS:
                    builder.add(chunk)
                    chunk = []
            builder.add(chunk)
            if not builder.late_text:
                break
            late |= builder.late_text  # a column became text late in the sheet: read it again as text
            base = fraction
        else:
            raise _broken("лист меняется во время чтения")
        columns, warnings = builder.finish()
    finally:
        book.close()
    tick(1.0, f"Прочитано строк: {builder.rows:,}".replace(",", " "))
    info = ReadInfo(kind, header_row=options.header_row, sheet=name, sheets=sheets)
    return TableRead(columns, builder.rows, info, warnings)
