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

Ported from BF `formats/registry.py` (table part: `TABLE_SUFFIXES`; object part: `detect`, `pair_files` of
`rasters.py`) and `importing/service.py` (`read_table`, `check_supported`); block models and OMF come with
their stages.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bf_core.formats.errors import FormatError
from bf_core.formats.table import Progress, ReadOptions, TableRead, no_progress

if TYPE_CHECKING:
    from bf_core.crs import CrsInfo
    from bf_core.formats.lineset import LineSet
    from bf_core.formats.meshset import MeshEntry, MeshSet
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


# ---------------------------------------------------------------- objects: lines, points, rasters, wireframes

MICROMINE_STR, SURPAC_STR, DXF, SHP, MIF, POINTS, RASTER = (
    "micromine_str",
    "surpac_str",
    "dxf",
    "shp",
    "mif",
    "points",
    "raster",
)
TRIDB, OBJ, STL, SURPAC_DTM, DATAMINE, DATAMINE_STR, DWG = (
    "tridb",
    "obj",
    "stl",
    "surpac_dtm",
    "datamine",
    "datamine_str",
    "dwg",
)
LINES_KIND, POINTS_KIND, RASTER_KIND, WIREFRAMES_KIND = "lines", "points", "raster", "wireframes"
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")
WORLD_SUFFIXES = (".jgw", ".jpgw", ".pgw", ".pngw", ".tfw", ".tifw", ".bpw", ".bmpw", ".wld")
POINT_SUFFIXES = (".xyz", ".csv", ".txt", ".tsv", ".prn", ".dat")
#: How a format gives its wireframes (`ObjectFormat.meshes`): OPENED — `reader(path, progress=…)` opens the file:
#: `entries`, `warnings`, `read(entry, progress)` (a wireframe that cannot be read does not lose the others);
#: LISTED — `reader(path, keys, progress)` gives them one by one and `inspector(path, progress)` the composition;
#: DRAWN — the reader of a drawing gives them with its lines (`detail.wireframes`).
OPENED, LISTED, DRAWN = "opened", "listed", "drawn"
SUPPORTED = (
    "Подходят: стринги .str / .pit, чертежи .dxf / .dwg, .shp, .mif, точки .xyz / .csv / .txt / .dat, "
    "каркасы .tridb / .obj / .stl / .dtm с .str / .dm, растры .jpg / .png / .tif / .bmp с файлом привязки."
)


@dataclass(frozen=True)
class ObjectFormat:
    key: str
    label: str  # for the wizard, in Russian
    kind: str  # "lines" | "points" | "raster" | "wireframes": what the file usually holds (a drawing gives more)
    suffixes: tuple[str, ...]
    module: str  # imported on first use
    reader: str  # read(path, progress, **options) of that module; of a wireframe format — see `meshes`
    sidecars: tuple[str, ...] = ()  # companions of the same name
    options: tuple[str, ...] = ()  # keyword arguments the reader takes from `read_objects`
    files: tuple[tuple[str, str], ...] = ()  # companion suffix → the reader's keyword for its path
    meshes: str = ""  # OPENED | LISTED | DRAWN: the format gives wireframes, and how
    inspector: str = ""  # LISTED: inspect(path, progress, **options) → (entries, warnings)
    choices: tuple[tuple[str, str], ...] = ()  # (option, function of the module: header → what to choose from)


_F = "bf_core.formats."
OBJECT_FORMATS: dict[str, ObjectFormat] = {
    f.key: f
    for f in (
        ObjectFormat(MICROMINE_STR, "Стринги Micromine (.STR, .PIT)", LINES_KIND, (".str", ".pit"),
                     _F + "str_micromine", "read_str", options=("encoding",)),
        ObjectFormat(SURPAC_STR, "Стринги Surpac (.str, текст)", LINES_KIND, (".str",),
                     _F + "str_surpac", "read_surpac", options=("encoding",)),
        ObjectFormat(DXF, "AutoCAD DXF: линии, точки, текст, каркасы", LINES_KIND, (".dxf",),
                     _F + "dxf_lines", "read_dxf", meshes=DRAWN),
        ObjectFormat(DWG, "AutoCAD DWG: линии, точки, текст, каркасы", LINES_KIND, (".dwg",),
                     _F + "dwg", "read_dwg", options=("converter",), meshes=DRAWN),
        ObjectFormat(SHP, "ESRI Shapefile", LINES_KIND, (".shp",), _F + "shp", "read_shp",
                     (".dbf", ".shx", ".prj", ".cpg"), ("encoding",),
                     ((".dbf", "dbf"), (".prj", "prj"), (".cpg", "cpg"))),
        ObjectFormat(MIF, "MapInfo MIF/MID", LINES_KIND, (".mif",), _F + "mif", "read_mif",
                     (".mid",), ("encoding",), ((".mid", "mid"),)),
        ObjectFormat(POINTS, "Точки XYZ (CSV, TXT, DAT)", POINTS_KIND, POINT_SUFFIXES,
                     _F + "points", "read_points", options=("encoding", "filename")),
        ObjectFormat(RASTER, "Растр с привязкой (JPG, PNG, TIFF, BMP)", RASTER_KIND, IMAGE_SUFFIXES,
                     _F + "raster_image", "read_raster", (*WORLD_SUFFIXES, ".tab", ".grf", ".prj")),
        ObjectFormat(TRIDB, "Каркасы Micromine (.tridb)", WIREFRAMES_KIND, (".tridb",),
                     _F + "tridb", "open_tridb", meshes=OPENED),
        ObjectFormat(OBJ, "Каркасы Wavefront OBJ", WIREFRAMES_KIND, (".obj",),
                     _F + "obj", "read_obj", options=("name",), meshes=LISTED, inspector="inspect_obj"),
        ObjectFormat(STL, "Каркасы STL", WIREFRAMES_KIND, (".stl",),
                     _F + "stl", "read_stl", options=("name",), meshes=LISTED, inspector="inspect_stl"),
        ObjectFormat(SURPAC_DTM, "Каркасы Surpac (.dtm с .str, текст)", WIREFRAMES_KIND, (".dtm",),
                     _F + "dtm_surpac", "read_dtm", (".str",), ("name",), ((".str", "strings"),),
                     meshes=LISTED, inspector="inspect_dtm"),
        ObjectFormat(DATAMINE, "Каркасы Datamine (…tr.dm с …pt.dm)", WIREFRAMES_KIND, (".dm",),
                     _F + "datamine_objects", "open_dm_wireframes", (".dm",), ("encoding", "split_by"),
                     ((".dm", "points"),), meshes=OPENED, choices=(("split_by", "split_fields"),)),
        ObjectFormat(DATAMINE_STR, "Стринги Datamine (.dm)", LINES_KIND, (".dm",),
                     _F + "datamine_objects", "read_dm_strings", options=("encoding", "layer_field"),
                     choices=(("layer_field", "layer_fields"),)),
    )
}  # fmt: skip
_BY_CONTENT = (".str", ".dm")  # extensions of two formats: the content of the file tells which
_BY_OBJECT_SUFFIX = {s: f for f in OBJECT_FORMATS.values() for s in f.suffixes if s not in _BY_CONTENT}
_SIDECAR_OWNERS = {s: f for f in OBJECT_FORMATS.values() for s in f.sidecars}
OBJECT_SUFFIXES = tuple(dict.fromkeys(s for f in OBJECT_FORMATS.values() for s in (*f.suffixes, *f.sidecars)))
#: A main file of these extensions goes with another main file of its name as its companion (`group_files`).
_SHARED = frozenset(_SIDECAR_OWNERS) & frozenset(s for f in OBJECT_FORMATS.values() for s in f.suffixes)


@dataclass
class ObjectsRead:
    """What any object reader gives: layers of lines and points, rasters, wireframes, warnings for the log.

    Wireframes. `meshes` (reading) gives them one at a time — iterate once; a drawing gives the list its reader
    made (take them from its front to free the memory). A wireframe that cannot be read does not stop the
    others where the format allows: its `MeshEntry` with `error` goes to `unread` as `meshes` goes on. An error
    of the file itself is raised by the iteration (a `FormatError`, an `OSError`). `entries` (`inspect=True`) is
    the composition without geometry; `meshes` is None then."""

    format: str  # a key of OBJECT_FORMATS
    label: str
    layers: list[LineSet] = field(default_factory=list)
    rasters: list[RasterRead] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    crs: CrsInfo | None = None
    detail: Any = None  # the reader's own result (StrRead, DxfRead, ShpRead, MifRead, PointsRead, RasterRead)
    entries: list[MeshEntry] = field(default_factory=list)
    meshes: Iterator[MeshSet] | list[MeshSet] | None = None
    unread: list[MeshEntry] = field(default_factory=list)
    preliminary: bool = False  # the reader was not checked on real files (Datamine: ADR 0013 §6)
    choices: dict[str, list[str]] = field(default_factory=dict)  # option → the fields to choose from (inspect)


@dataclass
class FileGroup:
    main: str  # the file to read
    formats: tuple[str, ...]  # keys of OBJECT_FORMATS the extension allows (".str", ".dm": told by content)
    sidecars: dict[str, str] = field(default_factory=dict)  # suffix → the companion file


def _datamine(path: Path) -> ObjectFormat:
    """A `.dm` by its fields: the triangles or the points of a wireframe, or strings (`datamine.DmHeader.kind`)."""
    from bf_core.formats import datamine as dm
    from bf_core.formats.datamine_pair import wrong_kind

    header = dm.read_header(path)
    if header.kind in (dm.TRIANGLES, dm.POINTS):
        return OBJECT_FORMATS[DATAMINE]
    if header.kind == dm.STRINGS:
        return OBJECT_FORMATS[DATAMINE_STR]
    raise wrong_kind(path, header, "wireframe")  # a block model (Э17), or a table of something else


def detect_object(path: str | Path, filename: str | None = None) -> ObjectFormat:
    """The object format of a file: by its extension, a `.str` and a `.dm` by their content. `filename` is the
    original name when the file is stored under another one. Anything else is a `FormatError("unsupported")`."""
    path = Path(path)
    suffix = Path(filename or path.name).suffix.lower()
    if suffix == ".str":
        from bf_core.formats.micromine_header import _STANDARD_RE, MAGIC
        from bf_core.formats.str_surpac import binary_error, is_surpac, is_surpac_binary

        with path.open("rb") as f:
            head = f.read(1 << 16)
        if head.startswith(MAGIC) or _STANDARD_RE.match(head):
            return OBJECT_FORMATS[MICROMINE_STR]
        if is_surpac(head):
            return OBJECT_FORMATS[SURPAC_STR]
        if is_surpac_binary(head):
            raise binary_error(Path(filename or path.name).name)
        raise FormatError(
            "unsupported",
            "Файл .str не похож ни на стринги Micromine, ни на текстовые стринги Surpac." if head else "Файл пуст.",
            "Двоичные стринги Surpac сохраните как текстовый String file (.str) и загрузите заново.",
        )
    if suffix == ".dm":
        return _datamine(path)
    if suffix == ".dmx":  # the compressed tables of Datamine Studio: told by the name, the file is not read
        from bf_core.formats.datamine_dmx import dmx_error

        raise dmx_error(Path(filename or path.name).name)
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
    raise FormatError("unsupported", f"Формат файла «{suffix or 'без расширения'}» не поддерживается.", SUPPORTED)


def _error(exc: FormatError) -> dict[str, str]:
    return {"code": exc.code, "message": exc.message, "hint": exc.hint}


def _each(out: ObjectsRead, opener: Any, path: Path, kwargs: dict[str, Any], tick: Progress) -> Iterator[MeshSet]:
    """The wireframes of an OPENED format one at a time. The file is opened by the first step and closed with
    the last one (or when the iteration is dropped); its warnings come with the first wireframe."""
    from bf_core.formats.str_layers import scaled

    with opener(path, progress=scaled(tick, 0.0, 0.05), **kwargs) as f:
        todo = [e for e in f.entries if not e.error]
        out.unread.extend(e for e in f.entries if e.error)
        start = 0.05
        if todo and hasattr(f, "load"):  # Datamine: the tables of the pair are read once for all wireframes
            f.load(scaled(tick, 0.05, 0.5))
            start = 0.5
        total, done, first = max(sum(f.size_of(e) for e in todo), 1), 0, True
        for entry in todo:
            size = f.size_of(entry)
            share = scaled(tick, start + (1.0 - start) * done / total, start + (1.0 - start) * (done + size) / total)
            done += size
            try:
                mesh = f.read(entry, share)
            except FormatError as exc:
                entry.error = _error(exc)
                out.unread.append(entry)
                continue
            if first:
                mesh.warnings[:0] = f.warnings
                first = False
            yield mesh
        tick(1.0, None)


def read_objects(
    path: str | Path,
    progress: Progress | None = None,
    *,
    filename: str | None = None,
    encoding: str | None = None,
    sidecars: Mapping[str, str | Path] | None = None,
    options: Mapping[str, Any] | None = None,
    settings: Mapping[str, Any] | None = None,
    inspect: bool = False,
) -> ObjectsRead:
    """Read a file of lines, points, wireframes or a raster with the reader of its format. `sidecars` {suffix:
    path} — the companion files when they are not next to the main one under its name; `encoding` overrides the
    code page where the format has one; `settings` — choices of the wizard by name (`split_by`, `layer_field`,
    `converter`): those the reader of the format takes are passed, the others are not for it; `options` —
    further keyword arguments of the reader, as they are. `inspect` — the composition: wireframes come as
    `entries` without their geometry (see `ObjectsRead`)."""
    path = Path(path)
    fmt = detect_object(path, filename)
    try:
        module = import_module(fmt.module)
        read = getattr(module, fmt.reader)
        look = getattr(module, fmt.inspector) if fmt.inspector else None
        choose = [(option, getattr(module, name)) for option, name in fmt.choices]
    except (ImportError, AttributeError) as exc:
        raise FormatError(
            "unsupported",
            f"Чтение формата «{fmt.label}» в этой версии не подключено.",
            "Обновите программу или сохраните данные в другом формате.",
        ) from exc
    given = {"encoding": encoding, "filename": filename, "name": Path(filename).stem if filename else None}
    given.update(settings or {})
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
    out = ObjectsRead(fmt.key, fmt.label, preliminary=bool(getattr(module, "PRELIMINARY", False)))
    header: Any = None
    if fmt.meshes == OPENED:
        if inspect:
            with read(path, progress=progress, **kwargs) as f:
                out.entries, out.warnings, header = list(f.entries), list(f.warnings), getattr(f, "header", None)
        else:
            out.meshes = _each(out, read, path, kwargs, progress or no_progress)
    elif fmt.meshes == LISTED:
        if inspect and look is not None:
            out.entries, out.warnings = look(path, progress, **kwargs)
        else:
            out.meshes = read(path, None, progress, **kwargs)
    else:
        detail = read(path, progress, **kwargs)
        out.detail, out.crs, header = detail, getattr(detail, "crs", None), getattr(detail, "header", None)
        if fmt.key == RASTER:
            out.rasters = [detail]
            out.crs = detail.georef.crs if detail.georef is not None else None
        else:
            out.layers = list(detail.layers)
        out.warnings = list(detail.warnings)
        if fmt.meshes == DRAWN and inspect:
            from bf_core.formats.mesh_io import entry_of

            out.entries = [entry_of(k, mesh) for k, mesh in enumerate(detail.wireframes)]
            detail.wireframes.clear()  # the composition does not hold the geometry
        elif fmt.meshes == DRAWN:
            out.meshes = detail.wireframes
    if inspect and header is not None:
        out.choices = {option: list(fields(header)) for option, fields in choose}
    return out


def group_files(names: Sequence[str]) -> tuple[list[FileGroup], list[str]]:
    """The files of a selection as main files with their companions, and the names that belong to nothing
    (a companion without its main file, an unknown extension). A companion goes to the main file of the same
    name (any case) that takes its extension; `map.jpg.tab` goes to `map.jpg`. A file that could be read by
    itself is a companion when the file it belongs to is chosen too: the `.str` of a Surpac `.dtm` of its name
    (the points of the surface, not strings to load) and the points `…pt.dm` of a Datamine `…tr.dm`."""
    mains: dict[str, tuple[str, ...]] = {}
    rest: list[str] = []
    for name in names:
        suffix = Path(name).suffix.lower()
        keys = tuple(f.key for f in OBJECT_FORMATS.values() if suffix in f.suffixes)
        if keys:
            mains.setdefault(name, keys)
    owned: dict[str, tuple[str, str]] = {}  # a main file that goes with another one → (that file, as which suffix)
    if any(Path(name).suffix.lower() in _SHARED for name in mains):
        by_name = {name.casefold(): name for name in mains}
        for name in mains:
            p = Path(name)
            suffix, other = p.suffix.lower(), None
            if suffix == ".dm" and p.stem.lower().endswith("pt"):
                from bf_core.formats.datamine_pair import twin_name

                other = by_name.get((twin_name(name) or "").casefold())
            elif suffix in _SHARED and suffix != ".dm":
                takers = (f.suffixes for f in OBJECT_FORMATS.values() if suffix in f.sidecars)
                twins = (p.stem + s for group in takers for s in group)
                other = next((by_name[t.casefold()] for t in twins if t.casefold() in by_name), None)
            if other is not None and other != name:
                owned[name] = (other, suffix)
    groups = [FileGroup(name, keys) for name, keys in mains.items() if name not in owned]
    by_main = {g.main: g for g in groups}
    for name, (other, suffix) in owned.items():
        by_main[other].sidecars.setdefault(suffix, name)
    for name in names:
        if name in mains:
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
