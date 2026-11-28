"""Объектные форматы: линии, точки, растры, каркасы, облака точек, блочные модели ссылки и OMF.

Описание API — в докстринге `formats.registry`: `OBJECT_FORMATS`, `detect_object`, `read_objects`, `group_files`.
Эти имена реэкспортируются из `formats.registry`, поэтому импорты по коду не меняются (ТЗ 27.13, Э27).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bf_core.formats.errors import FormatError
from bf_core.formats.table import Progress, no_progress

if TYPE_CHECKING:
    from bf_core.crs import CrsInfo
    from bf_core.formats.lineset import LineSet
    from bf_core.formats.meshset import MeshEntry, MeshSet
    from bf_core.formats.raster_image import RasterRead

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
LAS = "las"
OMF = "omf"
DATAMINE_BLOCKS = "datamine_blocks"  # a block model of Datamine (`.fbm`; a `.dm` by its fields): the import of blocks
LINES_KIND, POINTS_KIND, RASTER_KIND, WIREFRAMES_KIND, CLOUD_KIND = "lines", "points", "raster", "wireframes", "cloud"
BLOCKS_KIND = "block_model"  # a block model: read by `importing.object_blockfile`, not by `read_objects`
OMF_KIND = "omf"  # a project of several kinds: wireframes, a line file, block models
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
    "каркасы .tridb / .obj / .stl / .dtm с .str / .dm, растры .jpg / .png / .tif / .bmp с файлом привязки, "
    "облака точек .las, блочные модели .dat / .csv / .dm / .fbm, проекты OMF v1 .omf."
)


@dataclass(frozen=True)
class ObjectFormat:
    key: str
    label: str  # for the wizard, in Russian
    kind: str  # lines | points | raster | wireframes | cloud: what the file usually holds (a drawing gives more)
    suffixes: tuple[str, ...]
    module: str  # imported on first use
    reader: str  # read(path, progress, **options) of that module; of a wireframe format — see `meshes`
    sidecars: tuple[str, ...] = ()  # companions of the same name
    options: tuple[str, ...] = ()  # keyword arguments the reader takes from `read_objects`
    files: tuple[tuple[str, str], ...] = ()  # companion suffix → the reader's keyword for its path
    meshes: str = ""  # OPENED | LISTED | DRAWN: the format gives wireframes, and how
    inspector: str = ""  # LISTED: inspect(path, progress, **options) → (entries, warnings)
    choices: tuple[tuple[str, str], ...] = ()  # (option, function of the module: header → what to choose from)
    cloud: bool = False  # a point cloud: `reader(path)` opens the file, `inspector(path)` tells what is in it


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
        ObjectFormat(LAS, "Облако точек LAS (.las)", CLOUD_KIND, (".las", ".laz"),
                     _F + "las", "open_las", inspector="inspect_las", cloud=True),
        ObjectFormat(DATAMINE_BLOCKS, "Блочная модель Datamine (.dm, .fbm)", BLOCKS_KIND, (".fbm",),
                     _F + "datamine_blocks", "open_blocks", options=("encoding",)),
        ObjectFormat(OMF, "Проект OMF v1 (.omf): каркасы, линии, блочные модели", OMF_KIND, (".omf",),
                     _F + "omf_read", "open_reader", meshes=OMF),
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
    cloud: Any = None  # a point cloud opened for reading (`formats.las.LasFile`): its points are not read yet
    unread: list[MeshEntry] = field(default_factory=list)
    preliminary: bool = False  # the reader was not checked on real files (Datamine: ADR 0013 §6)
    choices: dict[str, list[str]] = field(default_factory=dict)  # option → the fields to choose from (inspect)
    volumes: list[Any] = field(default_factory=list)  # OMF: `omf_compose.OmfEntry` of the volumes that load
    left_out: list[dict[str, Any]] = field(default_factory=list)  # {key, element, kind, what, count, reason}


@dataclass
class FileGroup:
    main: str  # the file to read
    formats: tuple[str, ...]  # keys of OBJECT_FORMATS the extension allows (".str", ".dm": told by content)
    sidecars: dict[str, str] = field(default_factory=dict)  # suffix → the companion file


def _datamine(path: Path) -> ObjectFormat:
    """A `.dm` by its fields: the triangles or the points of a wireframe, strings or a block model
    (`datamine.DmHeader.kind`)."""
    from bf_core.formats import datamine as dm
    from bf_core.formats.datamine_pair import wrong_kind

    header = dm.read_header(path)
    if header.kind in (dm.TRIANGLES, dm.POINTS):
        return OBJECT_FORMATS[DATAMINE]
    if header.kind == dm.STRINGS:
        return OBJECT_FORMATS[DATAMINE_STR]
    if header.kind == dm.BLOCK_MODEL:  # imported as a block model (ADR 0023 §7; `object_blockfile.is_blockfile`)
        return OBJECT_FORMATS[DATAMINE_BLOCKS]
    raise wrong_kind(path, header, "wireframe")  # a table of something else


def detect_object(path: str | Path, filename: str | None = None) -> ObjectFormat:
    """The object format of a file: by its extension, a `.str` and a `.dm` by their content. `filename` is the
    original name when the file is stored under another one. Anything else is a `FormatError("unsupported")`."""
    path = Path(path)
    suffix = Path(filename or path.name).suffix.lower()
    if suffix == ".str":
        from bf_core.formats.micromine_header import header_start
        from bf_core.formats.str_surpac import binary_error, is_surpac, is_surpac_binary

        with path.open("rb") as f:
            head = f.read(1 << 16)
        if header_start(head) is not None:
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
    if fmt.kind == BLOCKS_KIND:  # the import of block models reads them (`importing.object_blockfile`), not this
        raise FormatError(
            "block_model",
            f"Файл «{Path(filename or path.name).name}» — блочная модель, а не линии, точки или каркасы.",
            "Загрузите его через «Импорт файлов» как блочную модель.",
        )
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
    if fmt.cloud:
        if Path(filename or path.name).suffix.lower() == ".laz":
            from bf_core.formats.las_header import laz_error

            raise laz_error()
        if inspect and look is not None:
            out.detail = look(path)
            cloud_header = out.detail.header
        else:
            out.cloud = read(path)
            cloud_header = out.cloud.header
        out.warnings, out.crs = list(cloud_header.warnings), cloud_crs(cloud_header.crs_text(), out.warnings)
    elif fmt.key == OMF:
        _omf(out, read, path, progress or no_progress, inspect)
    elif fmt.meshes == OPENED:
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


def _omf(out: ObjectsRead, opener: Any, path: Path, tick: Progress, inspect: bool) -> None:
    """OMF v1 into `out` (see the module docstring)."""
    from bf_core.formats.meshset import MeshEntry
    from bf_core.formats.omf_compose import describe

    out.detail = describe(path) if inspect else None
    if inspect:
        comp = out.detail
        out.entries = [
            MeshEntry(e.key, e.name, e.points, e.triangles, e.color, error=e.error)
            for e in comp.elements if e.kind == "surface" and (e.loads or e.error)
        ]  # fmt: skip
    else:
        from bf_core.formats.str_layers import scaled

        reader = opener(path, scaled(tick, 0.0, 0.5))
        try:
            comp = out.detail = reader.composition
            out.layers = list(reader.line_layers(skip_bad=True))
        except BaseException:
            reader.close()
            raise
        has_surfaces = bool(reader.entries(("surface",)))
        out.meshes = _omf_surfaces(out, reader, scaled(tick, 0.5, 1.0)) if has_surfaces else []
        if not has_surfaces:
            reader.close()
    out.volumes = [e for e in comp.elements if e.kind == "volume" and e.loads]
    out.left_out = comp.left_out() if inspect else reader.left_out  # the reader's list grows as it reads
    # Reading, the notes of a surface that loads go with it (its journal lines and `meta.warnings`), not here.
    own = set() if inspect else {e.key for e in comp.elements if e.kind == "surface" and e.loads}
    labels = {e.key: e.label for e in comp.elements}
    out.warnings = [omf_note(labels, x) for x in out.left_out if x["key"] not in own]
    if comp.crs is None:
        out.warnings.append("Система координат в файле OMF не указана: объекты загружены без системы координат")
    else:
        out.crs = _omf_crs(comp.crs, out.warnings)


def omf_note(labels: Mapping[str, str], x: Mapping[str, Any], own: bool = False) -> str:
    """A line of the journal for an entry of `left_out`: «Поверхность «a»: текстуры (2) — …»; `own` — without
    the element (a note of the object made of it)."""
    count = f" ({x['count']})" if x["what"] == "текстуры" else ""
    text = f"{x['what']}{count} — {x['reason']}"
    return text if own else f"{labels.get(x['key'], x['element'])}: {text}"


def _omf_surfaces(out: ObjectsRead, reader: Any, tick: Progress) -> Iterator[MeshSet]:
    """The surfaces of an open OMF reader one at a time, each with the notes of its element; the reader is closed
    with the last one (or when the iteration is dropped). A surface that cannot be read goes to `unread`."""
    from bf_core.formats.meshset import MeshEntry

    reader.progress = tick
    try:
        for mesh in reader.surfaces(skip_bad=True):
            key = mesh.source.get("element")
            mesh.warnings.extend(omf_note({}, x, own=True) for x in reader.left_out if x["key"] == key)
            yield mesh
        out.unread.extend(MeshEntry(u["key"], u["name"], None, None, error=u["error"])
                          for u in reader.unread if u["kind"] == "surface")  # fmt: skip
    finally:
        reader.close()


def _omf_crs(found: Mapping[str, Any], warnings: list[str]) -> CrsInfo | None:
    """The CRS of the project of an OMF file BF2 wrote («Система координат: ‹имя› (EPSG:…)»): by its EPSG code."""
    from bf_core.crs import CrsError, describe

    epsg = found.get("epsg")
    if epsg is not None:
        try:
            return describe(f"EPSG:{int(epsg)}")
        except CrsError:
            pass
    warnings.append(f"Система координат «{found.get('name')}» файла OMF не распознана: объекты загружены без неё")
    return None


def cloud_crs(text: str | None, warnings: list[str]) -> CrsInfo | None:
    """The CRS label of a cloud from the text of its file (a WKT, «EPSG:…»); one that cannot be read — a warning."""
    if not text:
        return None
    from bf_core.crs import CrsError, describe

    try:
        return describe(text)
    except CrsError:
        warnings.append("Система координат облака не распознана: облако загружено без метки системы координат.")
        return None


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
