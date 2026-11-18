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

Objects (Э7, ADR 0011 §2) — lines, points and rasters: `OBJECT_FORMATS` maps a format to its reader, loaded on
first use by the name of its module. `detect_object(path)` → `ObjectFormat` by the extension; a `.str` is told by
its content (Micromine data file or Surpac text, `str_surpac.is_surpac`). `read_objects(path, progress)` →
`ObjectsRead` is the one signature of every reader: path → layers (`lineset.LineSet`) and rasters
(`raster_image.RasterRead`) + warnings; `progress(fraction, message)` reports and may raise to cancel.
`group_files(names)` pairs the files of a selection: a main file with its companions (`.dbf .shx .prj .cpg` of
a shapefile, `.mid` of a MIF, world / TAB / GRF files of an image).

Ported from BF `formats/registry.py` (table part: `TABLE_SUFFIXES`; object part: `detect`, `pair_files` of
`rasters.py`) and `importing/service.py` (`read_table`, `check_supported`); wireframes, block models and OMF come
with their stages.
"""

from __future__ import annotations

import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bf_core.formats.errors import FormatError
from bf_core.formats.table import Progress, ReadOptions, TableRead

if TYPE_CHECKING:
    from bf_core.crs import CrsInfo
    from bf_core.formats.lineset import LineSet
    from bf_core.formats.raster_image import RasterRead

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
    if suffix in TABLE_SUFFIXES and looks_like_text(head):
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


# ---------------------------------------------------------------- objects: lines, points, rasters (Э7)

MICROMINE_STR, SURPAC_STR, DXF, SHP, MIF, POINTS, RASTER = (
    "micromine_str",
    "surpac_str",
    "dxf",
    "shp",
    "mif",
    "points",
    "raster",
)
LINES_KIND, POINTS_KIND, RASTER_KIND = "lines", "points", "raster"
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")
WORLD_SUFFIXES = (".jgw", ".jpgw", ".pgw", ".pngw", ".tfw", ".tifw", ".bpw", ".bmpw", ".wld")
POINT_SUFFIXES = (".xyz", ".csv", ".txt", ".tsv", ".prn", ".dat")


@dataclass(frozen=True)
class ObjectFormat:
    key: str
    label: str  # for the wizard, in Russian
    kind: str  # "lines" | "points" | "raster": what the file usually holds (a line format may give points too)
    suffixes: tuple[str, ...]
    module: str  # imported on first use
    reader: str  # read(path, progress, **options) of that module
    sidecars: tuple[str, ...] = ()  # companions of the same name
    options: tuple[str, ...] = ()  # keyword arguments the reader takes from `read_objects`
    files: tuple[tuple[str, str], ...] = ()  # companion suffix → the reader's keyword for its path


_F = "bf_core.formats."
OBJECT_FORMATS: dict[str, ObjectFormat] = {
    f.key: f
    for f in (
        ObjectFormat(MICROMINE_STR, "Стринги Micromine (.STR, .PIT)", LINES_KIND, (".str", ".pit"),
                     _F + "str_micromine", "read_str", options=("encoding",)),
        ObjectFormat(SURPAC_STR, "Стринги Surpac (.str, текст)", LINES_KIND, (".str",),
                     _F + "str_surpac", "read_surpac", options=("encoding",)),
        ObjectFormat(DXF, "AutoCAD DXF: линии, точки, текст", LINES_KIND, (".dxf",), _F + "dxf_lines", "read_dxf"),
        ObjectFormat(SHP, "ESRI Shapefile", LINES_KIND, (".shp",), _F + "shp", "read_shp",
                     (".dbf", ".shx", ".prj", ".cpg"), ("encoding",),
                     ((".dbf", "dbf"), (".prj", "prj"), (".cpg", "cpg"))),
        ObjectFormat(MIF, "MapInfo MIF/MID", LINES_KIND, (".mif",), _F + "mif", "read_mif",
                     (".mid",), ("encoding",), ((".mid", "mid"),)),
        ObjectFormat(POINTS, "Точки XYZ (CSV, TXT, DAT)", POINTS_KIND, POINT_SUFFIXES,
                     _F + "points", "read_points", options=("encoding", "filename")),
        ObjectFormat(RASTER, "Растр с привязкой (JPG, PNG, TIFF, BMP)", RASTER_KIND, IMAGE_SUFFIXES,
                     _F + "raster_image", "read_raster", (*WORLD_SUFFIXES, ".tab", ".grf", ".prj")),
    )
}  # fmt: skip
_BY_OBJECT_SUFFIX = {s: f for f in OBJECT_FORMATS.values() if f.key != SURPAC_STR for s in f.suffixes}
_SIDECAR_OWNERS = {s: f for f in OBJECT_FORMATS.values() for s in f.sidecars}
OBJECT_SUFFIXES = tuple(dict.fromkeys(s for f in OBJECT_FORMATS.values() for s in (*f.suffixes, *f.sidecars)))


@dataclass
class ObjectsRead:
    """What any object reader gives: layers of lines and points, rasters, warnings for the operation log."""

    format: str  # a key of OBJECT_FORMATS
    label: str
    layers: list[LineSet] = field(default_factory=list)
    rasters: list[RasterRead] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    crs: CrsInfo | None = None
    detail: Any = None  # the reader's own result (StrRead, DxfRead, ShpRead, MifRead, PointsRead, RasterRead)


@dataclass
class FileGroup:
    main: str  # the file to read
    formats: tuple[str, ...]  # keys of OBJECT_FORMATS the extension allows (".str": two, told by content)
    sidecars: dict[str, str] = field(default_factory=dict)  # suffix → the companion file


def detect_object(path: str | Path, filename: str | None = None) -> ObjectFormat:
    """The object format of a file: by its extension, a `.str` by its content. `filename` is the original name
    when the file is stored under another one. Anything else is a `FormatError("unsupported")`."""
    path = Path(path)
    suffix = Path(filename or path.name).suffix.lower()
    if suffix == ".str":
        from bf_core.formats.micromine_header import _STANDARD_RE, MAGIC
        from bf_core.formats.str_surpac import is_surpac

        with path.open("rb") as f:
            head = f.read(1 << 16)
        if head.startswith(MAGIC) or _STANDARD_RE.match(head):
            return OBJECT_FORMATS[MICROMINE_STR]
        if is_surpac(head):
            return OBJECT_FORMATS[SURPAC_STR]
        raise FormatError(
            "unsupported",
            "Файл .str не похож ни на стринги Micromine, ни на текстовые стринги Surpac." if head else "Файл пуст.",
            "Двоичные стринги Surpac сохраните как текстовый String file (.str) и загрузите заново.",
        )
    found = _BY_OBJECT_SUFFIX.get(suffix)
    if found is not None:
        return found
    owner = _SIDECAR_OWNERS.get(suffix)
    if owner is not None:
        raise FormatError(
            "sidecar",
            f"Файл «{suffix}» — вспомогательный: сам по себе он не загружается.",
            f"Загрузите его вместе с основным файлом того же имени ({', '.join(owner.suffixes)}).",
        )
    raise FormatError(
        "unsupported",
        f"Формат файла «{suffix or 'без расширения'}» не поддерживается.",
        "Подходят: стринги .str / .pit, .dxf, .shp, .mif, точки .xyz / .csv / .txt / .dat, "
        "растры .jpg / .png / .tif / .bmp с файлом привязки.",
    )


def read_objects(
    path: str | Path,
    progress: Progress | None = None,
    *,
    filename: str | None = None,
    encoding: str | None = None,
    sidecars: Mapping[str, str | Path] | None = None,
    options: Mapping[str, Any] | None = None,
) -> ObjectsRead:
    """Read a file of lines, points or a raster with the reader of its format. `sidecars` {suffix: path} — the
    companion files when they are not next to the main one under its name; `encoding` overrides the code page
    where the format has one; `options` — further keyword arguments of the reader."""
    path = Path(path)
    fmt = detect_object(path, filename)
    try:
        read = getattr(import_module(fmt.module), fmt.reader)
    except (ImportError, AttributeError) as exc:
        raise FormatError(
            "unsupported",
            f"Чтение формата «{fmt.label}» в этой версии не подключено.",
            "Обновите программу или сохраните данные в другом формате.",
        ) from exc
    given = {"encoding": encoding, "filename": filename}
    kwargs: dict[str, Any] = {k: v for k, v in given.items() if k in fmt.options and v is not None}
    files = {suffix.lower(): Path(p) for suffix, p in (sidecars or {}).items()}
    if fmt.key == RASTER:
        if sidecars is not None:
            kwargs["sidecars"] = files
        if filename:
            kwargs["name"] = Path(filename).stem
    else:
        kwargs.update({key: files[suffix] for suffix, key in fmt.files if suffix in files})
    kwargs.update(options or {})
    detail = read(path, progress, **kwargs)
    out = ObjectsRead(fmt.key, fmt.label, detail=detail, crs=getattr(detail, "crs", None))
    if fmt.key == RASTER:
        out.rasters = [detail]
        out.crs = detail.georef.crs if detail.georef is not None else None
    else:
        out.layers = list(detail.layers)
    out.warnings = list(detail.warnings)
    return out


def group_files(names: Sequence[str]) -> tuple[list[FileGroup], list[str]]:
    """The files of a selection as main files with their companions, and the names that belong to nothing
    (a companion without its main file, an unknown extension). A companion goes to the main file of the same
    name (any case) that takes its extension; `map.jpg.tab` goes to `map.jpg`."""
    groups: list[FileGroup] = []
    rest: list[str] = []
    for name in names:
        suffix = Path(name).suffix.lower()
        keys = tuple(f.key for f in OBJECT_FORMATS.values() if suffix in f.suffixes)
        if keys:
            groups.append(FileGroup(name, keys))
    used = {g.main for g in groups}
    for name in names:
        if name in used:
            continue
        p = Path(name)
        suffix, stem = p.suffix.lower(), p.stem.casefold()
        owners = [
            g
            for g in groups
            if stem in (Path(g.main).stem.casefold(), Path(g.main).name.casefold())
            and any(suffix in OBJECT_FORMATS[k].sidecars for k in g.formats)
        ]
        for g in owners:
            g.sidecars.setdefault(suffix, name)
        if not owners:
            rest.append(name)
    return groups, rest
