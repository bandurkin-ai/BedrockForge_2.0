"""Export of line and point layers to files (ТЗ 7.18, 9.3, 9.5, 9.6, 9.12; ADR 0011 §3 `export.lines`).

Job `export.lines` {path, format, layer_ids | file_id, delimiter?, decimal?, encoding?}: the layers of the project
(`lines/store.py`) written by the writers of `formats/`:

| format   | file                     | writer                          | layers of points          |
|----------|--------------------------|---------------------------------|---------------------------|
| `str`    | Micromine `.str`, `.pit` | `str_micromine.write_str`       | left out (a warning)      |
| `surpac` | Surpac `.str` (text)     | `str_surpac.write_surpac`       | left out (a warning)      |
| `dxf`    | AutoCAD `.dxf`           | `dxf_write.write_dxf`           | POINT / TEXT              |
| `csv`    | `.csv`, `.txt`, `.xyz`   | `points.write_points`           | a row per point           |
| `dat`    | Micromine `.dat`         | `points.write_points_dat`       | a row per point           |

`csv` and `dat` take lines too: a row per vertex with the attributes of its line. SHP and MIF/MID have no writer
(ADR 0011 §2).

Same bytes back. When every layer comes from one file of the project and that file (its `source` blob) is of
the format asked for, its layout goes to the writer as `like`: the header of a Micromine STR (fields, widths,
code page, XML — read from the first block of the file) or of a Surpac STR (the title, axis and END records and
the style of the numbers — the file is read once more for them). An unchanged file comes out byte for byte;
`source_layout` in the result says that it was used. In every other case coordinates are written as the writers
do it (binary doubles, `repr`), so «импорт → экспорт → импорт» gives the same float64.

The writers put the file under its name only when it is complete (`<name>.part` first), so a cancel or a failure
leaves nothing. Warnings of the writers go to the journal of the job; one row goes to `export_log`.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, export_log, line_layers, objects
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.jobs.registry import handler

from . import store as ls_store

MAX_LAYERS = 2000


@dataclass(frozen=True)
class Target:
    label: str
    suffixes: tuple[str, ...]  # the first one is added to a path that has none of them
    points: bool = True  # the format takes layers of points


FORMATS: dict[str, Target] = {
    "str": Target("Micromine STR", (".str", ".pit"), points=False),
    "surpac": Target("Surpac STR", (".str",), points=False),
    "dxf": Target("DXF", (".dxf",)),
    "csv": Target("CSV", (".csv", ".txt", ".xyz")),
    "dat": Target("Micromine DAT", (".dat",)),
}
OPTIONS = ("delimiter", "decimal", "encoding")


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def bad_format() -> ApiError:
    return ApiError(
        422,
        "bad_format",
        "Неизвестный формат экспорта линий.",
        "Выберите: Micromine STR, Surpac STR, DXF, CSV или Micromine DAT.",
    )


def target_path(path: str | Path, fmt: str) -> Path:
    """The file to write: the path as chosen, with the extension of the format when it has another one."""
    target = Path(path)
    spec = FORMATS[fmt]
    return target if target.suffix.lower() in spec.suffixes else target.with_name(target.name + spec.suffixes[0])


def check_path(path: str) -> None:
    """The checks of a path chosen in a save dialog, as the table export does them."""
    if not Path(path).is_absolute():
        raise ApiError(422, "path_not_absolute", "Путь к файлу должен быть полным.", "Выберите файл в окне сохранения.")
    if not Path(path).parent.is_dir():
        raise ApiError(404, "folder_not_found", "Папка для файла не найдена.", "Выберите папку заново.")


def request(
    conn: sa.Connection,
    *,
    path: str,
    format: str,  # noqa: A002 - the name of the parameter in the API
    layer_ids: list[int] | None = None,
    file_id: int | None = None,
    overwrite: bool = False,
    **options: Any,
) -> dict[str, Any]:
    """Checked parameters of the job `export.lines` (for the endpoint that starts it); ApiError otherwise.
    The save window asked only about the chosen name: when the format adds its extension and that file is
    there already, 409 `files_exist` {files} unless `overwrite` (review Э7, as `rasters/export.py`)."""
    if format not in FORMATS:
        raise bad_format()
    check_path(path)
    final = target_path(path, format)
    if not overwrite and final != Path(path) and (final.is_file() or final.is_symlink()):
        from bf_core.rasters.export import files_exist

        err = files_exist([final])
        raise ApiError(409, err.code, err.message, err.hint, extra={"files": [final.name]})
    ids = list(dict.fromkeys(int(i) for i in layer_ids or []))
    if file_id is not None and not ids:
        ids = ls_store.layer_ids(conn, file_id) if _is_file(conn, file_id) else []
    if not ids:
        raise ApiError(
            404,
            "line_layer_not_found",
            "Слои линий для экспорта не найдены.",
            "Обновите дерево проекта и выберите слои заново.",
        )
    if len(ids) > MAX_LAYERS:
        raise ApiError(
            422,
            "too_many_layers",
            f"Слоёв для экспорта больше {MAX_LAYERS}.",
            "Экспортируйте слои несколькими файлами.",
        )
    rows = conn.execute(
        sa.select(objects.c.id, line_layers.c.point_count)
        .join(line_layers, line_layers.c.object_id == objects.c.id)
        .where(objects.c.id.in_(ids), objects.c.kind == ls_store.LINE_LAYER, objects.c.state == "ready")
    ).all()
    if len(rows) != len(ids):
        raise ApiError(
            404, "line_layer_not_found", "Слой линий не найден.", "Обновите дерево проекта и выберите слои заново."
        )
    out: dict[str, Any] = {
        "path": path,
        "format": format,
        "layer_ids": ids,
        "file_id": file_id,
        "points": sum(int(r.point_count) for r in rows),
    }
    out.update({k: options[k] for k in OPTIONS if options.get(k) is not None})
    return out


def _is_file(conn: sa.Connection, file_id: int) -> bool:
    found = conn.execute(
        sa.select(objects.c.id).where(objects.c.id == file_id, objects.c.kind == ls_store.LINE_FILE)
    ).first()
    return found is not None


def late_tick(ctx: JobContext, progress: float, message: str | None = None) -> None:
    """Progress once the file is in place: a cancel that comes this late does not undo a finished export."""
    with suppress(JobCancelled):
        ctx.tick(progress, message)


def _memory(params: dict[str, Any]) -> int:
    """The layers as arrays, the vertices in file order (`str_layers.gather`) and the cells of a portion as
    Python values: about 400 B a vertex."""
    return max(512, int(int(params.get("points") or 0) * 400 / 2**20) + 256)


# --- the layout of the source file ---------------------------------------------------------------------


def _source_of(conn: sa.Connection, layer_ids: list[int]) -> tuple[int | None, str, str | None]:
    """(file id, its name, sha256 of its `source` blob) when every layer comes from one file; else no file."""
    parents = set(conn.execute(sa.select(objects.c.parent_id).where(objects.c.id.in_(layer_ids))).scalars())
    if len(parents) != 1 or None in parents:
        return None, "", None
    (file_id,) = parents
    name = conn.execute(sa.select(objects.c.name).where(objects.c.id == file_id)).scalar() or ""
    sha = conn.execute(
        sa.select(blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == ls_store.OWNER,
            blob_owners.c.owner_id == file_id,
            blob_owners.c.role == "source",
        )
    ).scalar()
    return int(file_id), str(name), None if sha is None else str(sha)


def source_layout(fmt: str, source: Path | None, progress: Any = None) -> Any:
    """What the writer of `fmt` takes as `like` from the file the layers were loaded from; None — the file is
    of another format (or cannot be read any more): the writer lays the file out itself."""
    from bf_core.formats.errors import FormatError

    if source is None or not source.is_file():
        return None
    try:
        if fmt == "str":
            from bf_core.formats.micromine_header import read_header

            return read_header(source)
        if fmt == "surpac":
            from bf_core.formats.str_surpac import is_surpac, read_surpac

            with source.open("rb") as f:
                if not is_surpac(f.read(1 << 16)):
                    return None
            return read_surpac(source, progress).header
    except (FormatError, OSError):
        return None
    return None


# --- writing -------------------------------------------------------------------------------------------


def write_layers(
    path: Path,
    layers: list[Any],
    fmt: str,
    *,
    like: Any = None,
    title: str = "",
    options: dict[str, Any] | None = None,
    progress: Any = None,
) -> tuple[dict[str, int], list[str]]:
    """Write `lineset.LineSet` layers as one file of `fmt`. Returns the counts {layers, lines, points, size} of
    what was written and the warnings. Raises `FormatError` {code, message, hint}."""
    from bf_core.formats.errors import FormatError

    spec = FORMATS[fmt]
    opts = {k: v for k, v in (options or {}).items() if k in OPTIONS and v is not None}
    warnings: list[str] = []
    taken = [layer for layer in layers if layer.point_count]
    if not spec.points:
        skipped = [layer.name for layer in taken if layer.points_only]
        taken = [layer for layer in taken if not layer.points_only]
        if skipped:
            names = ", ".join(f"«{name}»" for name in skipped[:5]) + (" и другие" if len(skipped) > 5 else "")
            warnings.append(
                f"Слои точек не записаны в {spec.label} ({names}): для точек выберите CSV, Micromine DAT или DXF"
            )
    if not taken:
        if spec.points:
            raise FormatError("no_lines", f"Нечего записывать в {spec.label}: выбранные слои пусты.", "Выберите слой.")
        raise FormatError(
            "no_lines",
            f"Нечего записывать в {spec.label}: в выбранных слоях нет линий.",
            "Выберите слой с линиями; точки экспортируйте в CSV, Micromine DAT или DXF.",
        )
    written: Any
    if fmt == "str":
        from bf_core.formats.str_micromine import write_str

        written = write_str(path, taken, like=like, progress=progress)
    elif fmt == "surpac":
        from bf_core.formats.str_surpac import write_surpac

        written = write_surpac(path, taken, like=like, encoding=opts.get("encoding"), title=title, progress=progress)
    elif fmt == "dxf":
        from bf_core.formats.dxf_write import write_dxf

        written = write_dxf(path, taken, progress)
    elif fmt == "csv":
        from bf_core.formats.points import write_points

        written = write_points(path, taken, progress=progress, **opts)
    else:
        from bf_core.formats.points import write_points_dat

        written = write_points_dat(path, taken, progress=progress)
    if fmt in ("csv", "dat") and any(not layer.points_only for layer in taken):
        warnings.append("Линии записаны точками: по строке на вершину, с атрибутами своей линии")
    counts = {
        "layers": len(taken),
        "lines": sum(layer.line_count for layer in taken),
        "points": sum(layer.point_count for layer in taken),
        "size": int(written.size),
    }
    return counts, [*warnings, *written.warnings]


@handler("export.lines", "Экспорт линий", queue="heavy", estimate_memory=_memory)
def export_lines(ctx: JobContext) -> dict[str, Any]:
    from bf_core.formats.errors import FormatError
    from bf_core.storage.blobs import BlobStore

    p = ctx.params
    fmt = str(p.get("format"))
    if fmt not in FORMATS:
        err = bad_format()
        raise JobError(err.code, err.message, err.hint)
    if ctx.project_folder is None:
        raise RuntimeError("export.lines is a project job")
    target = target_path(p["path"], fmt)
    engine = ctx.engine()
    store = BlobStore(ctx.project_folder)
    ctx.tick(0.0, "Чтение слоёв")
    try:
        with dialect.read_tx(engine) as conn:
            ids = [int(i) for i in p.get("layer_ids") or []]
            if not ids and p.get("file_id") is not None:
                ids = ls_store.layer_ids(conn, int(p["file_id"]))
            layers = []
            for k, layer_id in enumerate(ids):
                ctx.tick(0.1 * k / len(ids))
                layers.append(ls_store.read_layer(conn, store, layer_id))
            file_id, file_name, sha = _source_of(conn, ids) if ids else (None, "", None)
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc

    def part(start: float, stop: float) -> Any:
        def tick(f: float, message: str | None = None) -> None:
            value = start + (stop - start) * max(0.0, min(1.0, f))
            if f >= 1.0 and stop >= 1.0:  # the last word of a writer: its file is under its name already
                late_tick(ctx, value, message)
            else:
                ctx.tick(value, message)

        return tick

    like = None
    if sha is not None and fmt in ("str", "surpac"):
        ctx.tick(0.1, "Чтение оформления исходного файла")
        like = source_layout(fmt, store.path_of(sha), part(0.1, 0.3))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        counts, warnings = write_layers(
            target, layers, fmt, like=like, title=Path(file_name).stem, options=p, progress=part(0.3, 1.0)
        )
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
    ctx.log(
        f"Записан файл «{target.name}» ({FORMATS[fmt].label}): слоёв {_n(counts['layers'])}, "
        f"линий {_n(counts['lines'])}, вершин {_n(counts['points'])}"
        + (", оформление исходного файла сохранено" if like is not None else "")
    )
    with dialect.write_tx(engine) as conn:
        conn.execute(
            sa.insert(export_log).values(
                object_id=file_id if file_id is not None else (ids[0] if ids else None),
                job_id=ctx.id,
                file_name=target.name,
                format=fmt,
                settings={
                    "layer_ids": ids,
                    "file_id": p.get("file_id"),
                    "source_layout": like is not None,
                    **{k: p.get(k) for k in OPTIONS},
                },
                counts={**counts, "warnings": len(warnings)},
                created_at=dialect.utcnow(),
            )
        )
    late_tick(ctx, 1.0, "Готово")
    return {"path": str(target), "format": fmt, **counts, "source_layout": like is not None, "warnings": warnings}
