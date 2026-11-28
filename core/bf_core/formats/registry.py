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

Objects (Э7, ADR 0011 §2; wireframes — Э8, ADR 0013 §2): `OBJECT_FORMATS` maps a format to its reader, loaded
on first use by the name of its module. `detect_object(path)` → `ObjectFormat` by the extension; a `.str` and a
`.dm` are told by their content (Micromine data file or Surpac text; a Datamine table of a wireframe or of
strings); a binary Surpac `.str` and a Datamine `.dmx` are not read and are told for what they are.
`read_objects(path, progress)` → `ObjectsRead` is the one signature of every reader: path → layers
(`lineset.LineSet`), rasters (`raster_image.RasterRead`) and wireframes (`meshset.MeshSet`) + warnings;
`progress(fraction, message)` reports and may raise to cancel. Wireframes come one at a time (`meshes`): a file
of a hundred of them is never in memory whole; a DXF or a DWG gives its lines and its wireframes from one pass.
`inspect=True` gives the composition instead (`entries`, no geometry). `group_files(names)` pairs the files of a
selection: a main file with its companions (`.dbf .shx .prj .cpg` of a shapefile, `.mid` of a MIF, world / TAB /
GRF files of an image, the `.str` of a Surpac `.dtm`, the points table `…pt.dm` of a Datamine `…tr.dm`).
Point clouds (Э7.1, ADR 0014 §3, §6): a `.las` is a cloud (`ObjectFormat.cloud`): `read_objects` opens it and gives
the opened file as `ObjectsRead.cloud` — its points are read later, portion by portion, by the import
(`importing/object_cloud.py`); `inspect=True` gives `formats.las.LasInspect` as `detail`. A `.laz` is told by its
name: `laz_not_supported` with the hint of ADR 0014. A table of points goes into a cloud when the wizard asks for one
(the option `cloud` of the file): `formats/points_stream.py`, not through this registry.
OMF v1 (ADR 0017 §2, `_omf`): `detail` — the `OmfComposition`, `left_out`, `volumes` (entries, read by the import);
`inspect=True` — the JSON alone; reading — the line sets as `layers`, the surfaces one at a time. OMF 2 (ZIP) —
`omf2_not_supported` (`omf_file.open_omf`).

Ported from BF `formats/registry.py` (table part: `TABLE_SUFFIXES`; object part: `detect`, `pair_files` of
`rasters.py`) and `importing/service.py` (`read_table`, `check_supported`); block models and OMF come with
their stages.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from bf_core.formats.errors import FormatError
from bf_core.formats.registry_objects import (  # noqa: F401 (re-exports)
    _BY_OBJECT_SUFFIX,
    BLOCKS_KIND,
    CLOUD_KIND,
    DATAMINE,
    DATAMINE_BLOCKS,
    DATAMINE_STR,
    DRAWN,
    DWG,
    DXF,
    IMAGE_SUFFIXES,
    LAS,
    LINES_KIND,
    LISTED,
    MICROMINE_STR,
    MIF,
    OBJ,
    OBJECT_FORMATS,
    OBJECT_SUFFIXES,
    OMF,
    OMF_KIND,
    OPENED,
    POINT_SUFFIXES,
    POINTS,
    POINTS_KIND,
    RASTER,
    RASTER_KIND,
    SHP,
    STL,
    SUPPORTED,
    SURPAC_DTM,
    SURPAC_STR,
    TRIDB,
    WIREFRAMES_KIND,
    WORLD_SUFFIXES,
    FileGroup,
    ObjectFormat,
    ObjectsRead,
    cloud_crs,
    detect_object,
    group_files,
    omf_note,
    read_objects,
)
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


def looks_like_text(head: bytes) -> bool:
    """The start of a file is text (any of the table encodings), not markup and not binary data."""
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
    from bf_core.formats.micromine_header import header_start

    path = Path(path)
    suffix = Path(filename or path.name).suffix.lower()
    with path.open("rb") as f:
        head = f.read(1 << 16)
    if suffix == ".str":
        return UNKNOWN
    if not head:
        return _BY_SUFFIX.get(suffix, UNKNOWN)
    if header_start(head) is not None:
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
    if suffix in TABLE_SUFFIXES and looks_like_text(head):
        return CSV
    return UNKNOWN


def _shown_bytes(head: bytes) -> str:
    """The first bytes of a file as hex and as text (a code page is guessed: Windows-1251) for an error message."""
    text = "".join(c if c.isprintable() else "·" for c in head.decode("cp1251", errors="replace"))
    return f"{head.hex(' ')} (как текст: «{text}»)"


def unsupported_error(path: str | Path, filename: str | None = None) -> FormatError:
    """Why a file is not read as a table: the first bytes, what was looked for and was not found, what to send."""
    path = Path(path)
    suffix = Path(filename or path.name).suffix.lower()
    with path.open("rb") as f:
        head = f.read(32)
    if not head:
        return FormatError("empty_file", "Файл пуст.", "Проверьте, что файл скопирован полностью.")
    shown = _shown_bytes(head)
    if suffix not in TABLE_SUFFIXES:
        return FormatError(
            "unsupported",
            f"Формат файла «{suffix or 'без расширения'}» не поддерживается как таблица. Начало файла: {shown}.",
            "Подходят: Micromine .DAT, текст .csv / .txt, Excel .xlsx / .xlsm / .xls.",
        )
    if suffix == ".dat":
        looked = (
            "Искали в начале файла (после возможных пробелов и BOM): строку «THIS IS MICROMINE EXTENDED DATA FILE» "
            "или строку «<число> VARIABLES» — не найдены; на текстовую таблицу содержимое тоже не похоже "
            "(есть нулевые или управляющие байты)."
        )
    elif suffix in EXCEL_SUFFIXES:
        looked = "Искали книгу Excel (архив ZIP с xl/workbook.xml или файл OLE2 с листом) или текст — не найдены."
    else:
        looked = "Содержимое не похоже на текстовую таблицу (разметка HTML / XML, нулевые или управляющие байты)."
    return FormatError(
        "unsupported",
        f"Файл «{suffix}» не распознан как таблица. Начало файла (32 байта): {shown}. {looked}",
        "Пришлите первые 8 КБ файла разработчикам, сохраните таблицу в Micromine заново "
        "(Файл → Сохранить как → .DAT) или экспортируйте её в CSV и выберите CSV.",
    )


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
    raise unsupported_error(path, filename)
