"""Export of a raster (ТЗ 7.19, 9.7, 9.12; ADR 0011 §3 `export.raster`): the source file or a GeoTIFF.

Job `export.raster` {raster_id, path, format}:

* `source` — the image exactly as it was loaded (the `source` blob, byte for byte) under the chosen name with
  the extension of its kind, and next to it the georeference **as the project has it now**: a world file
  (`.jgw`, `.pgw`, `.tfw`, `.bpw` — `georef.world_text`, numbers by `repr`) and a `.prj` when the CRS is known.
  A world file holds neither the level nor a raster off the plan, so a vertical raster gets the companion files
  it was loaded with (`sidecar:<ext>` blobs: GRF, TAB, …) instead, with a warning that a shift made in the
  project is not in them, and the level of a plan raster is named in the journal.
* `geotiff` — the pixels of the source without loss and the georeference in tags (`geotiff_write.write_geotiff`):
  the transform bit for bit, the level as the Z of the tie point, the EPSG code. A vertical raster has no place
  in a GeoTIFF: a Russian error that advises the source file.

The georeference is `rasters.georef` (`georef.describe`) with `rasters.elevation`; a raster without one is
exported as its source only. Every file is written as `<name>.part` and renamed when all of them are complete,
so a cancel or a failure leaves nothing. Warnings go to the journal of the job; one row goes to `export_log`.
"""

from __future__ import annotations

import os
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


def request(conn: sa.Connection, *, raster_id: int, path: str, format: str) -> dict[str, Any]:  # noqa: A002
    """Checked parameters of the job `export.raster` (for the endpoint that starts it); ApiError otherwise."""
    from bf_core.lines.export import check_path

    if format not in FORMATS:
        raise bad_format()
    check_path(path)
    row = _row(conn, raster_id)
    return {"raster_id": raster_id, "path": path, "format": format, "width": row["width"], "height": row["height"]}


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


def write_source(
    target: Path, row: dict[str, Any], store: Any, tick: Any
) -> tuple[Path, list[str], list[str], list[str]]:
    """The source image under `target` (with the extension of its kind) and its georeference files next to it.
    Returns (image path, names of every file written, warnings, notes)."""
    from bf_core import crs as crs_mod
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
                    texts[target.with_suffix("." + role[len(SIDECAR) :])] = store.read_bytes(side_sha)
            warnings.append(
                "Привязка вертикального растра записана исходными файлами привязки"
                + (f" ({', '.join(p.suffix for p in texts)})" if texts else " не была: таких файлов в проекте нет")
                + ": сдвиг, сделанный в проекте, в них не попадает"
            )
        prj = target.with_suffix(".prj")
        if g.crs is not None and prj not in texts:
            texts[prj] = g.crs.wkt
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


def write_geotiff(target: Path, row: dict[str, Any], store: Any, tick: Any) -> tuple[Path, list[str]]:
    """The raster as one GeoTIFF: (path, warnings). Raises `FormatError` {code, message, hint}."""
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
    if target.suffix.lower() not in GEOTIFF_SUFFIXES:
        target = target.with_name(target.name + GEOTIFF_SUFFIXES[0])
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
    notes: list[str] = []
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "source":
            target, files, warnings, notes = write_source(target, row, store, tick)
        else:
            target, warnings = write_geotiff(target, row, store, tick)
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
