"""The writers of the registry (`registry.py`): wrappers over the writers of the modules (Э4–Э8), one per pair
«type × format». Importing this module registers them; it imports no writer itself — each is imported when it
writes (the API loads the registry before BF-READY).

| type            | formats → writer                                                                     |
|-----------------|--------------------------------------------------------------------------------------|
| `drillhole_set` | `csv`, `dat` → `drillholes.export_book.write_tables` (folder); `xlsx` → `write_book`  |
| `wireframe`/`dtm` | `dxf`, `dwg`, `tridb`, `obj`, `stl`, `dtm` → `wireframes.export.write_wireframes`; `omf` |
| `line_file`     | `str`, `dxf`, `surpac` → `lines.export.write_layers` (the layout of its source file); `omf` |
| `cloud`         | `las` (by the source LAS when there is one, else a new file), `csv` → `lines.cloud_export` |
| `raster`        | `source` → `rasters.export.write_source`; `geotiff` → `write_geotiff`                |
| `block_model`   | `omf`; `dat`, `csv` — «этап Э17»                                                     |
| every type but rasters | `source` — the file as loaded, byte for byte (a set: a folder of its files)   |

OMF is written by `bf_core.exporting.omf_job.write_objects(path, *, wireframe_ids, line_file_ids,
block_model_ids, engine, store, progress) -> (counts with "size", warnings)` (ADR 0017 §4, track Б 11.7); until
it is there the format is unavailable with the reason.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

from bf_core.db import dialect

from . import names
from .registry import Env, Item, Reason, Tick, Writer, Written, register, unavailable

MB = 2**20
COPY_CHUNK = 1 << 20


def _mb(n: float, base: int = 256) -> int:
    return max(512, base + int(n / MB))


# --- «Исходный файл» -----------------------------------------------------------------------------------------

NO_SOURCE = Reason("Объект создан в проекте: исходного файла нет.", "Выберите другой формат для этого типа.")


def _copy(src: Path, dst: Path, tick: Tick, start: float, stop: float) -> int:
    size = max(src.stat().st_size, 1)
    done = 0
    part = dst.with_name(dst.name + ".part")
    try:
        with src.open("rb") as fin, part.open("wb") as fout:
            while block := fin.read(COPY_CHUNK):
                tick(start + (stop - start) * done / size, "Запись исходного файла")
                fout.write(block)
                done += len(block)
        os.replace(part, dst)
    finally:
        part.unlink(missing_ok=True)
    return done


def _source_suffixes(item: Item, _env: Env) -> tuple[str, ...]:
    if item.kind == "drillhole_set" or not item.sources:
        return ("",)
    first = item.sources[0]
    suffix = Path(first.name).suffix
    return (suffix, *(s for s, _sha in first.sidecars if s.casefold() != suffix.casefold()))


def _source_stem(item: Item) -> str:
    if item.kind == "drillhole_set" or not item.sources:
        return item.name
    return Path(item.sources[0].name).stem or item.name


def _write_source(item: Item, path: Path, env: Env, tick: Tick) -> Written:
    """The files the object was loaded from. A file several objects share (the wireframes of one TRIDB) is
    written once: the next objects name the file already written."""
    store = env.store
    if item.kind == "drillhole_set":
        path.mkdir(parents=True, exist_ok=True)
        taken = names.Folder(len(str(path)))
        pairs = []
        for s in item.sources:
            p = Path(s.name)
            stem = taken.claim(p.stem, (p.suffix, *(x for x, _ in s.sidecars)))
            pairs.append((s, path / (stem + p.suffix)))
    else:
        pairs = [(item.sources[0], path)]
    files: list[Path] = []
    notes: list[str] = []
    size = 0
    for k, (s, target) in enumerate(pairs):
        before = env.sources_written.get(s.sha256)
        if before is not None:
            notes.append(f"Исходный файл «{s.name}» уже записан: «{before.name}»")
            continue
        if not store.exists(s.sha256):
            raise _format_error(
                "source_missing", f"Исходный файл «{s.name}» не найден в проекте.", "Загрузите его заново."
            )
        size += _copy(store.path_of(s.sha256), target, tick, k / len(pairs), (k + 1) / len(pairs))
        files.append(target)
        env.sources_written[s.sha256] = target
        for suffix, sha in s.sidecars:
            side = target.with_suffix(suffix)
            if side != target and store.exists(sha):
                size += _copy(store.path_of(sha), side, tick, (k + 1) / len(pairs), (k + 1) / len(pairs))
                files.append(side)
    return Written(files, size, [], notes)


def _format_error(code: str, message: str, hint: str) -> Exception:
    from bf_core.formats.errors import FormatError

    return FormatError(code, message, hint)


def _has_source(item: Item) -> Reason | None:
    return None if item.sources else NO_SOURCE


def _source_writer(kind: str) -> Writer:
    return register(
        Writer(kind, "source", "Исходный файл", _write_source, _source_suffixes, accepts=_has_source,
               stem=_source_stem)
    )  # fmt: skip


# --- drillhole sets -------------------------------------------------------------------------------------------


def _set_tables(fmt: str) -> Any:
    def write(item: Item, path: Path, env: Env, tick: Tick) -> Written:
        from bf_core.drillholes.export_book import write_tables

        files, _rows, warnings = write_tables(
            path, item.id, fmt, engine=env.engine, project_folder=env.store.root.parent, tick=tick
        )
        return Written(files, sum(f.stat().st_size for f in files), warnings)

    return write


def _set_book(item: Item, path: Path, env: Env, tick: Tick) -> Written:
    from bf_core.drillholes.export_book import write_book

    files, _rows, warnings = write_book(
        path, item.id, engine=env.engine, project_folder=env.store.root.parent, tick=tick
    )
    return Written(files, sum(f.stat().st_size for f in files), warnings)


def _set_memory(item: Item) -> int:
    return _mb(int(item.info.get("rows") or 0) * 300)


# --- wireframes and DTM ---------------------------------------------------------------------------------------

WIREFRAME_FORMATS = (
    ("dxf", "DXF"),
    ("dwg", "DWG"),
    ("tridb", "Micromine TRIDB"),
    ("obj", "OBJ"),
    ("stl", "STL"),
    ("dtm", "Surpac DTM"),
)


def _wireframe_suffixes(fmt: str) -> Any:
    def suffixes(_item: Item, _env: Env) -> tuple[str, ...]:
        from bf_core.wireframes.export import FORMATS

        target = FORMATS[fmt]
        return (target.suffix, *target.more)

    return suffixes


def _write_wireframe(fmt: str) -> Any:
    def write(item: Item, path: Path, env: Env, tick: Tick) -> Written:
        from bf_core.wireframes import source as src
        from bf_core.wireframes.export import write_wireframes

        with dialect.read_tx(env.engine) as conn:
            mesh = src.read_mesh(conn, env.store, item.id)
        counts, warnings = write_wireframes(path, [mesh], fmt, total=1, converter=env.oda, progress=tick)
        return Written([path.parent / name for name in counts["files"]], int(counts["size"]), warnings)

    return write


def _wireframe_memory(fmt: str) -> Any:
    def memory(item: Item) -> int:
        from bf_core.wireframes.export import MEMORY_FACTOR

        return _mb(int(item.info.get("geometry") or 0) * MEMORY_FACTOR.get(fmt, 16))

    return memory


def _dwg_available(env: Env) -> Reason | None:
    from bf_core.formats.dwg import HINT_WRITE, find_converter

    if find_converter(env.oda) is None:
        return Reason("Запись DWG недоступна: не найден ODA File Converter.", HINT_WRITE)
    return None


# --- OMF ------------------------------------------------------------------------------------------------------

OMF_HOOK = ("bf_core.exporting.omf_job", "write_objects")
OMF_IDS = {"wireframe": "wireframe_ids", "dtm": "wireframe_ids", "line_file": "line_file_ids",
           "block_model": "block_model_ids"}  # fmt: skip


def _omf_writer() -> Any:
    try:
        return getattr(importlib.import_module(OMF_HOOK[0]), OMF_HOOK[1], None)
    except ImportError:
        return None


def _omf_available(_env: Env) -> Reason | None:
    if _omf_writer() is None:
        return Reason("Запись OMF в этой версии программы недоступна.", "Выберите другой формат.")
    return None


def _write_omf(item: Item, path: Path, env: Env, tick: Tick) -> Written:
    write = _omf_writer()
    if write is None:
        why = _omf_available(env)
        assert why is not None
        raise _format_error("omf_unavailable", why.message, why.hint)
    counts, warnings = write(path, **{OMF_IDS[item.kind]: [item.id]}, engine=env.engine, store=env.store, progress=tick)
    return Written([path], int(counts.get("size") or path.stat().st_size), list(warnings))


OMF_MAX_CELLS = 100_000_000  # `omf_job.MAX_CELLS` (ADR 0017 решение 14): the cells of the grid, written densely


def _omf_accepts(item: Item) -> Reason | None:
    cells = max(int(item.info.get("cells") or 0), int(item.info.get("grid_cells") or 0))
    if item.kind == "block_model" and cells > OMF_MAX_CELLS:
        return Reason("Сетка модели больше 100 млн ячеек: в OMF не записывается.", "Разделите модель на части.")
    return None


def _omf(kind: str) -> Writer:
    return register(
        Writer(kind, "omf", "OMF", _write_omf, lambda _i, _e: (".omf",), available=_omf_available,
               accepts=_omf_accepts, memory=lambda i: _mb(i.size * 2))
    )  # fmt: skip


# --- line files and clouds ------------------------------------------------------------------------------------

LINE_FORMATS = (("str", "Micromine STR"), ("dxf", "DXF"), ("surpac", "Surpac STR"))


def _write_lines(fmt: str) -> Any:
    def write(item: Item, path: Path, env: Env, tick: Tick) -> Written:
        from bf_core.lines import store as ls
        from bf_core.lines.export import source_layout, write_layers

        with dialect.read_tx(env.engine) as conn:
            layers = [ls.read_layer(conn, env.store, i) for i in ls.layer_ids(conn, item.id)]
        like = None
        own = item.sources[0] if item.sources else None
        if own is not None and fmt in ("str", "surpac") and env.store.exists(own.sha256):
            like = source_layout(fmt, env.store.path_of(own.sha256))
        counts, warnings = write_layers(path, layers, fmt, like=like, title=item.stem, progress=tick)
        return Written([path], int(counts["size"]), warnings)

    return write


def _lines_memory(item: Item) -> int:
    return _mb(int(item.info.get("points") or 0) * 400)


def _write_cloud(fmt: str) -> Any:
    def write(item: Item, path: Path, env: Env, tick: Tick) -> Written:
        import sqlalchemy as sa

        from bf_core.db.schema import objects
        from bf_core.formats.las_write import rewrite_classes
        from bf_core.lines.cloud import open_cloud
        from bf_core.lines.cloud_export import _csv, _has_source, _new_las

        cloud_id = int(item.info["cloud_id"])
        with dialect.read_tx(env.engine) as conn:
            cloud = open_cloud(conn, env.store, cloud_id)
            wkt = conn.execute(sa.select(objects.c.crs).where(objects.c.id == cloud_id)).scalar()
            by_source = fmt == "las" and _has_source(conn, cloud_id) and bool(item.sources)
        if fmt == "csv":
            written = _csv(cloud, path, {}, tick)
        elif by_source:
            written = rewrite_classes(
                env.store.path_of(item.sources[0].sha256), path, cloud.read("seq"), cloud.read("class"), tick
            )
        else:
            written = _new_las(cloud, path, wkt, tick)
        return Written([path], int(written.size), list(written.warnings))

    return write


# --- rasters --------------------------------------------------------------------------------------------------


def _raster_row(env: Env, raster_id: int) -> dict[str, Any]:
    from bf_core.rasters.export import _row

    with dialect.read_tx(env.engine) as conn:
        return _row(conn, raster_id)


def _raster_source_suffixes(item: Item, env: Env) -> tuple[str, ...]:
    from bf_core.rasters.export import planned

    files = planned(Path("x"), _raster_row(env, item.id), env.store, "source")
    return tuple(p.name[1:] for p in files) or (".tif",)


def _write_raster_source(item: Item, path: Path, env: Env, tick: Tick) -> Written:
    from bf_core.rasters.export import write_source

    target, files, warnings, notes = write_source(path, _raster_row(env, item.id), env.store, tick)
    paths = [target.parent / name for name in files]
    return Written(paths, sum(p.stat().st_size for p in paths), warnings, notes)


def _write_geotiff(item: Item, path: Path, env: Env, tick: Tick) -> Written:
    from bf_core.rasters.export import write_geotiff

    target, warnings = write_geotiff(path, _raster_row(env, item.id), env.store, tick)
    return Written([target], target.stat().st_size, warnings)


# --- the registry ---------------------------------------------------------------------------------------------

for _fmt, _title in (("csv", "CSV"), ("xlsx", "XLSX"), ("dat", "Micromine DAT")):
    register(
        Writer("drillhole_set", _fmt, _title, _set_book if _fmt == "xlsx" else _set_tables(_fmt),
               (lambda _i, _e: (".xlsx",)) if _fmt == "xlsx" else (lambda _i, _e: ("",)), memory=_set_memory)
    )  # fmt: skip
_source_writer("drillhole_set")

for _kind in ("wireframe", "dtm"):
    for _fmt, _title in WIREFRAME_FORMATS:
        register(
            Writer(_kind, _fmt, _title, _write_wireframe(_fmt), _wireframe_suffixes(_fmt),
                   available=_dwg_available if _fmt == "dwg" else (lambda _e: None), memory=_wireframe_memory(_fmt))
        )  # fmt: skip
    _omf(_kind)
    _source_writer(_kind)

for _fmt, _title in LINE_FORMATS:
    register(Writer("line_file", _fmt, _title, _write_lines(_fmt), (lambda _i, _e: (".str",)) if _fmt != "dxf"
                    else (lambda _i, _e: (".dxf",)), memory=_lines_memory))  # fmt: skip
_omf("line_file")
_source_writer("line_file")

register(Writer("cloud", "las", "LAS", _write_cloud("las"), lambda _i, _e: (".las",),
                memory=lambda i: _mb(int(i.info.get("points") or 0) * 8, 384)))  # fmt: skip
register(Writer("cloud", "csv", "CSV", _write_cloud("csv"), lambda _i, _e: (".csv",)))
_source_writer("cloud")

register(Writer("raster", "source", "Исходный файл с привязкой", _write_raster_source, _raster_source_suffixes,
                accepts=_has_source))  # fmt: skip
register(Writer("raster", "geotiff", "GeoTIFF", _write_geotiff, lambda _i, _e: (".tif",), accepts=_has_source,
                memory=lambda i: _mb(int(i.info.get("pixels") or 0) * 4, 384)))  # fmt: skip

_omf("block_model")
for _fmt, _title in (("dat", "Micromine DAT"), ("csv", "CSV")):
    unavailable("block_model", _fmt, _title, Reason("Экспорт блочных моделей в DAT и CSV — этап Э17.", "Выберите OMF."))
_source_writer("block_model")
