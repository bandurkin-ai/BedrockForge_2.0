"""Export of a raster (ТЗ 7.19, 9.7, 9.12; ADR 0011 §3 `export.raster`): the source file or a GeoTIFF.

Job `export.raster` {raster_id, path, format, overwrite?}:

* `source` — the image exactly as it was loaded (the `source` blob, byte for byte) under the chosen name with
  the extension of its kind, and next to it the georeference **as the project has it now**: a world file
  (`.jgw`, `.pgw`, `.tfw`, `.bpw` — `georef.world_text`, numbers by `repr`) and a `.prj` when the CRS is known.
  A world file holds neither the level nor a raster off the plan, so a vertical raster gets the companion files
  it was loaded with (`sidecar:<ext>` blobs: GRF, TAB, …) instead, with a warning that a shift made in the
  project is not in them, and the level of a plan raster is named in the journal. A TAB names its image
  (`File "…"`): that line is written with the name of the exported image (`retarget_tab`).
* `geotiff` — the pixels of the source without loss and the georeference in tags (`geotiff_write.write_geotiff`):
  the transform bit for bit, the level as the Z of the tie point, the EPSG code. A vertical raster has no place
  in a GeoTIFF: a Russian error that advises the source file.

The georeference is `rasters.georef` (`georef.describe`) with `rasters.elevation`; a raster without one is
exported as its source only. Every file is written as `<name>.part` and renamed when all of them are complete,
so a cancel or a failure leaves nothing. Warnings go to the journal of the job; one row goes to `export_log`.

Files of other people. The save window asks about one name — `path` as chosen. The export writes more: the
image may get the extension of its kind after the chosen name (`plan.tif` → `plan.tif.jpg`) and its companions
go next to it. None of those is replaced without `overwrite: true`: the endpoint answers 409 `files_exist`
with their names (`request` → `existing`; the window asks and starts again), and the job checks once more
before it writes anything.
"""

from __future__ import annotations

import errno
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, export_log, objects, rasters
from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler

RASTER = "raster"
OWNER = "object"
FORMATS = ("source", "geotiff")
SIDECAR = "sidecar:"
#: image kind (`raster_image.ImageInfo.format`) → its extensions (the first is added) and its world file
KINDS: dict[str, tuple[tuple[str, ...], str]] = {
    "JPEG": ((".jpg", ".jpeg"), ".jgw"),
    "PNG": ((".png",), ".pgw"),
    "TIFF": ((".tif", ".tiff"), ".tfw"),
    "BMP": ((".bmp",), ".bpw"),
}
GEOTIFF_SUFFIXES = (".tif", ".tiff")
COPY_CHUNK = 1 << 20


def raster_not_found() -> ApiError:
    return ApiError(404, "raster_not_found", "Растр не найден.", "Обновите дерево проекта.")


def bad_format() -> ApiError:
    return ApiError(422, "bad_format", "Неизвестный формат экспорта растра.", "Выберите «Исходный файл» или «GeoTIFF».")


def _row(conn: sa.Connection, raster_id: int) -> dict[str, Any]:
    row = (
        conn.execute(
            sa.select(objects.c.name, objects.c.crs, rasters)
            .join(rasters, rasters.c.object_id == objects.c.id)
            .where(objects.c.id == raster_id, objects.c.kind == RASTER, objects.c.state == "ready")
        )
        .mappings()
        .first()
    )
    if row is None:
        raise raster_not_found()
    out = dict(row)
    out["blobs"] = {
        str(r.role): str(r.sha256)
        for r in conn.execute(
            sa.select(blob_owners.c.role, blob_owners.c.sha256).where(
                blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == raster_id
            )
        )
    }
    return out


def request(
    conn: sa.Connection,
    *,
    raster_id: int,
    path: str,
    format: str,  # noqa: A002 - the name of the parameter in the API
    overwrite: bool = False,
    store: Any = None,
) -> dict[str, Any]:
    """Checked parameters of the job `export.raster` (for the endpoint that starts it); ApiError otherwise.
    With the blob `store` of the project: files the export would replace without having been asked about
    (`existing`) are a 409 `files_exist` with their names in `files`, unless `overwrite`."""
    from bf_core.lines.export import check_path

    if format not in FORMATS:
        raise bad_format()
    check_path(path)
    row = _row(conn, raster_id)
    if store is not None and not overwrite:
        clash = existing(planned(Path(path), row, store, format), Path(path))
        if clash:
            err = files_exist(clash)
            raise ApiError(409, err.code, err.message, err.hint, extra={"files": [p.name for p in clash]})
    return {
        "raster_id": raster_id, "path": path, "format": format, "overwrite": bool(overwrite),
        "width": row["width"], "height": row["height"],
    }  # fmt: skip


def _memory(params: dict[str, Any]) -> int:
    """A GeoTIFF is written from the decoded source image (up to 4 samples of a pixel) and a row of tiles."""
    if params.get("format") != "geotiff":
        return 256
    pixels = int(params.get("width") or 0) * int(params.get("height") or 0)
    return max(512, int(pixels * 4 / 2**20) + 384)


def georef_of(row: dict[str, Any], warnings: list[str]) -> Any:
    """`formats.georef.Georef` of a raster as the project keeps it; None — the raster has no georeference."""
    from bf_core import crs as crs_mod
    from bf_core.formats import georef as gr

    g = row.get("georef") or {}
    affine = g.get("affine")
    if not affine or len(affine) != 6:
        return None
    crs = None
    text = g.get("crs") or row.get("crs")
    if text:
        try:
            crs = crs_mod.describe(str(text))
        except crs_mod.CrsError:
            warnings.append("Система координат растра не распознана: файл записан без неё")
    affine_z = g.get("affine_z")
    return gr.Georef(
        tuple(float(v) for v in affine),  # type: ignore[arg-type]
        str(g.get("source") or gr.MANUAL),
        row.get("elevation"),
        [],
        crs,
        tuple(float(v) for v in affine_z) if affine_z else None,  # type: ignore[arg-type]
    )


def _copy(src: Path, dst: Path, tick: Any, start: float, stop: float) -> None:
    size = max(src.stat().st_size, 1)
    done = 0
    with src.open("rb") as fin, dst.open("wb") as fout:
        while True:
            tick(start + (stop - start) * done / size, "Запись исходного файла")
            block = fin.read(COPY_CHUNK)
            if not block:
                break
            fout.write(block)
            done += len(block)


def _number(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".").replace(".", ",")


_TAB_FILE_TEXT = r'(?i)(?:^|(?<=[\r\n]))([ \t]*File[ \t]+")[^"\r\n]*(")'
_TAB_FILE = re.compile(_TAB_FILE_TEXT.encode("ascii"))
_TAB_CHARSET = re.compile(rb'(?i)(?:^|(?<=[\r\n]))[ \t]*!charset[ \t]+"?([A-Za-z0-9_-]+)')


def retarget_tab(content: bytes, image_name: str) -> tuple[bytes, bool]:
    """A raster TAB whose `File "…"` line names `image_name` — the exported image next to it — instead of the
    file (often a full path) it was registered with. Every other byte stays: the control points, the line
    ends, a BOM. The name is written in the code page of the TAB: UTF-16 or UTF-8 by the BOM, else `!charset`,
    else UTF-8 when the text is UTF-8 with letters beyond ASCII, else Windows-1251 (as `georef._text` reads
    it). Returns (the text, done); not done — there is no such line or the name has letters the code page
    does not have: the TAB is returned as it was."""
    from bf_core.formats.mif import charset_codec

    for bom, wide in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if content.startswith(bom):
            try:
                text = content.decode(wide)
            except UnicodeDecodeError:
                return content, False
            changed, n = re.subn(_TAB_FILE_TEXT, lambda m: m[1] + image_name + m[2], text, count=1)
            return (changed.encode(wide), True) if n else (content, False)
    codec: str | None = "utf-8" if content.startswith(b"\xef\xbb\xbf") else None
    if codec is None:
        named = _TAB_CHARSET.search(content)
        codec = charset_codec(named[1].decode("ascii")) if named else None
    if codec is None:
        codec = "cp1251"
        if not content.isascii():
            try:
                content.decode("utf-8")
                codec = "utf-8"
            except UnicodeDecodeError:
                pass
    try:
        name = image_name.encode(codec)
    except (UnicodeEncodeError, LookupError):
        return content, False
    out, n = _TAB_FILE.subn(lambda m: m[1] + name + m[2], content, count=1)
    return (out, True) if n else (content, False)


@dataclass
class SourcePlan:
    """What the export of the source file writes: the image under `target` and the companion files."""

    target: Path
    source: Path  # the blob of the image
    texts: dict[Path, str | bytes] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def files(self) -> list[Path]:
        return [self.target, *self.texts]


def plan_source(target: Path, row: dict[str, Any], store: Any) -> SourcePlan:
    """The files of «исходный файл с привязкой» for the chosen `target`; nothing is written."""
    from bf_core.formats import georef as gr
    from bf_core.formats.errors import FormatError
    from bf_core.formats.raster_image import probe

    warnings: list[str] = []
    notes: list[str] = []
    sha = row["blobs"].get("source")
    if sha is None or not store.exists(sha):
        raise FormatError("raster_broken", "Исходный файл растра не найден в проекте.", "Загрузите растр заново.")
    source = store.path_of(sha)
    suffixes, world = KINDS.get(probe(source).format, ((target.suffix.lower() or ".img",), ".wld"))
    if target.suffix.lower() not in suffixes:
        target = target.with_name(target.name + suffixes[0])
    texts: dict[Path, str | bytes] = {}
    g = georef_of(row, warnings)
    if g is None:
        warnings.append("У растра нет привязки: записан только исходный файл")
    else:
        try:
            texts[target.with_suffix(world)] = gr.world_text(g)
            if g.elevation is not None:
                notes.append(
                    f"Высота растра ({_number(g.elevation)} м) в world-файл не записывается: "
                    "чтобы сохранить её, выберите GeoTIFF"
                )
        except FormatError:  # off the plan: the companion files as loaded
            for role, side_sha in sorted(row["blobs"].items()):
                if role.startswith(SIDECAR) and store.exists(side_sha):
                    suffix = "." + role[len(SIDECAR) :]
                    content = store.read_bytes(side_sha)
                    if suffix == ".tab":  # it names its image: the exported one, not the file it was made for
                        content, done = retarget_tab(content, target.name)
                        if not done:
                            warnings.append(
                                f"В TAB-файле привязки имя изображения не заменено на «{target.name}»: строки "
                                'File "…" в нём нет или имя не записывается в кодировке файла — исправьте её вручную'
                            )
                    texts[target.with_suffix(suffix)] = content
            warnings.append(
                "Привязка вертикального растра записана исходными файлами привязки"
                + (f" ({', '.join(p.suffix for p in texts)})" if texts else " не была: таких файлов в проекте нет")
                + ": сдвиг, сделанный в проекте, в них не попадает"
            )
        prj = target.with_suffix(".prj")
        if g.crs is not None and prj not in texts:
            texts[prj] = g.crs.wkt
    return SourcePlan(target, source, texts, warnings, notes)


def geotiff_target(target: Path) -> Path:
    """The GeoTIFF to write: the path as chosen, with `.tif` after it when it has another extension."""
    return target if target.suffix.lower() in GEOTIFF_SUFFIXES else target.with_name(target.name + GEOTIFF_SUFFIXES[0])


def planned(target: Path, row: dict[str, Any], store: Any, fmt: str) -> list[Path]:
    """Every file the export to the chosen `target` writes, the image first. A raster that cannot be exported
    (its source is gone) plans nothing: the job tells why."""
    from bf_core.formats.errors import FormatError

    if fmt != "source":
        return [geotiff_target(target)]
    try:
        return plan_source(target, row, store).files
    except (FormatError, OSError):
        return []


def existing(files: list[Path], chosen: Path) -> list[Path]:
    """Those of `files` that are there already and were not asked about: the save window asked about `chosen`
    only — not about the image under a longer name, nor about the companion files."""
    return [p for p in files if p != chosen and (p.is_file() or p.is_symlink())]


def files_exist(clash: list[Path]) -> Any:
    from bf_core.formats.errors import FormatError

    names = ", ".join(f"«{p.name}»" for p in clash[:10]) + (f" и ещё {len(clash) - 10}" if len(clash) > 10 else "")
    return FormatError(
        "files_exist",
        f"В папке уже есть файлы, которые экспорт заменит: {names}.",
        "Подтвердите замену этих файлов в окне экспорта или выберите другое имя файла.",
    )


def write_source(
    target: Path, row: dict[str, Any], store: Any, tick: Any, *, overwrite: bool = True
) -> tuple[Path, list[str], list[str], list[str]]:
    """The source image under `target` (with the extension of its kind) and its georeference files next to it.
    Returns (image path, names of every file written, warnings, notes). Without `overwrite` a file that is
    there already and is not `target` itself is not replaced: FormatError `files_exist`, nothing is written."""
    from bf_core import crs as crs_mod

    plan = plan_source(target, row, store)
    if not overwrite:
        clash = existing(plan.files, target)
        if clash:
            raise files_exist(clash)
    for path in plan.files:  # found now, not after the image is already under its name
        if path.is_dir():
            raise IsADirectoryError(errno.EISDIR, f"«{path.name}» — папка, а не файл")
    target, source, texts, warnings, notes = plan.target, plan.source, plan.texts, plan.warnings, plan.notes
    parts: list[tuple[Path, Path]] = []
    try:
        part = target.with_name(target.name + ".part")
        parts.append((part, target))
        _copy(source, part, tick, 0.0, 0.95)
        for path, content in texts.items():
            part = path.with_name(path.name + ".part")
            parts.append((part, path))
            if path.suffix == ".prj" and isinstance(content, str):
                try:
                    crs_mod.write_prj(part, content)  # the ESRI flavour GIS programs expect
                except (RuntimeError, ValueError):  # pyproj cannot turn this CRS into it: the WKT as kept
                    part.write_text(content, encoding="utf-8")
            elif isinstance(content, str):
                part.write_text(content, encoding="ascii", newline="\n")
            else:
                part.write_bytes(content)
        tick(0.98, "Запись файлов привязки")
        for part, path in parts:
            os.replace(part, path)
    finally:
        for part, _path in parts:
            part.unlink(missing_ok=True)
    return target, [path.name for _part, path in parts], warnings, notes


def write_geotiff(
    target: Path, row: dict[str, Any], store: Any, tick: Any, *, overwrite: bool = True
) -> tuple[Path, list[str]]:
    """The raster as one GeoTIFF: (path, warnings). Raises `FormatError` {code, message, hint}; without
    `overwrite` — `files_exist` when `.tif` had to be added to the chosen name and that file is there."""
    from bf_core.formats.errors import FormatError
    from bf_core.formats.geotiff_write import write_geotiff as write

    warnings: list[str] = []
    sha = row["blobs"].get("source")
    if sha is None or not store.exists(sha):
        raise FormatError("raster_broken", "Исходный файл растра не найден в проекте.", "Загрузите растр заново.")
    g = georef_of(row, warnings)
    if g is None:
        raise FormatError(
            "no_georef",
            "У растра нет привязки: GeoTIFF записать нельзя.",
            "Задайте привязку растра или экспортируйте исходный файл.",
        )
    chosen, target = target, geotiff_target(target)
    if not overwrite and existing([target], chosen):
        raise files_exist([target])
    written = write(target, store.path_of(sha), g, progress=tick)
    return target, [*warnings, *written.warnings]


@handler("export.raster", "Экспорт растра", queue="heavy", estimate_memory=_memory)
def export_raster(ctx: JobContext) -> dict[str, Any]:
    from bf_core.formats.errors import FormatError
    from bf_core.lines.export import late_tick
    from bf_core.storage.blobs import BlobStore

    p = ctx.params
    fmt = str(p.get("format"))
    if fmt not in FORMATS:
        err = bad_format()
        raise JobError(err.code, err.message, err.hint)
    if ctx.project_folder is None:
        raise RuntimeError("export.raster is a project job")
    raster_id = int(p["raster_id"])
    engine = ctx.engine()
    store = BlobStore(ctx.project_folder)
    ctx.tick(0.0, "Чтение растра")
    try:
        with dialect.read_tx(engine) as conn:
            row = _row(conn, raster_id)
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc

    def tick(fraction: float, message: str | None = None) -> None:
        if fraction >= 1.0:  # the last word of the writer: the file is under its name already
            late_tick(ctx, 1.0, message)
        else:
            ctx.tick(max(0.0, fraction), message)

    target = Path(p["path"])
    overwrite = bool(p.get("overwrite"))  # asked for: the window showed the files that will be replaced
    notes: list[str] = []
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "source":
            target, files, warnings, notes = write_source(target, row, store, tick, overwrite=overwrite)
        else:
            target, warnings = write_geotiff(target, row, store, tick, overwrite=overwrite)
            files = [target.name]
    except FormatError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    except OSError as exc:
        raise JobError(
            "file_unwritable",
            f"Файл не записан: {exc.strerror or exc}.",
            "Выберите другую папку или закройте файл в другой программе.",
        ) from exc
    for w in warnings:
        ctx.log(w, "warn")
    for note in notes:
        ctx.log(note)
    size = sum((target.parent / name).stat().st_size for name in files)
    label = "исходный файл" if fmt == "source" else "GeoTIFF"
    ctx.log(
        f"Растр «{row['name']}» записан ({label}, {row['width']} × {row['height']} пикселей): "
        + ", ".join(f"«{name}»" for name in files)
    )
    with dialect.write_tx(engine) as conn:
        conn.execute(
            sa.insert(export_log).values(
                object_id=raster_id,
                job_id=ctx.id,
                file_name=target.name,
                format=fmt,
                settings={"files": files},
                counts={
                    "width": row["width"],
                    "height": row["height"],
                    "files": len(files),
                    "size": size,
                    "warnings": len(warnings),
                },
                created_at=dialect.utcnow(),
            )
        )
    late_tick(ctx, 1.0, "Готово")
    return {"path": str(target), "format": fmt, "files": files, "size": size, "warnings": warnings}
