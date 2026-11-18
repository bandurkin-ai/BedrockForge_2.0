"""Export of wireframes to files (ТЗ 7.21, 9.4, 9.5, 9.12; ADR 0013 §2, §5 `export.wireframes`; 8.18).

Job `export.wireframes` {path, format, wireframe_ids, options?, overwrite?, oda?}: the wireframes of the project
written into one file by the writers of `formats/` (one signature: path, wireframes, progress):

| format  | file                     | writer                               | options                             |
|---------|--------------------------|--------------------------------------|-------------------------------------|
| `tridb` | Micromine `.tridb`       | `tridb_write.write_tridb`            |                                     |
| `dxf`   | AutoCAD `.dxf`, polyface | `dxf_write_mesh.write_dxf_meshes`    |                                     |
| `dwg`   | AutoCAD `.dwg`           | the same DXF → ODA File Converter    | `version` R2000 … R2018 (R2013)     |
| `obj`   | Wavefront `.obj`         | `obj.write_obj`                      |                                     |
| `stl`   | `.stl`                   | `stl.write_stl`                      | `binary` (true; false — text)       |
| `dtm`   | Surpac `.dtm` + `.str`   | `dtm_surpac.write_dtm`               |                                     |

The wireframes go to the writer one at a time, in the order given (`wireframes.source.read_mesh`): the vertices
bit for bit, the triangles in the order of the file they came from (the blob `seq`), the name, colour and
attributes they have now, `meta.source`. So «импорт → экспорт → импорт» gives the same coordinates and the same
order bit for bit in every format that holds float64, a second export is the first one, and a TRIDB read and
written again has every geometry BLOB byte for byte and equal rows in its tables (ТЗ 5.4, ADR 0013 §2). A binary
STL holds float32: its writer says how much the coordinates changed. Memory: one wireframe.

The writers put a file under its name only when it is complete (`<name>.part` first, or a temporary folder for
a DWG), so a cancel or a failure leaves nothing. Warnings of the writers go to the journal of the job; one row
goes to `export_log`.

Files of other people (as `rasters/export.py`). The save window asks about one name — `path` as chosen. The
export may write another one: the name with the extension of the format after it (`тела` → `тела.tridb`,
`тела.txt` → `тела.txt.dxf`), and a Surpac DTM comes with its string file of the same name. None of those is
replaced without `overwrite: true`: the endpoint answers 409 `files_exist` with their names (the window asks
and starts again), and the job checks once more before it writes anything.

A DWG needs ODA File Converter (ADR 0009): its path of «Настройка» comes in `oda` (the job runs in a worker
process); the endpoint refuses at once when there is none — `oda_not_found` with the text of Д-13.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import export_log, objects, wireframes
from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler

from . import store as ws

MAX_WIREFRAMES = 5000  # of one export (the file of a model holds tens)


@dataclass(frozen=True)
class Target:
    label: str
    suffix: str  # added to a path that has another one
    more: tuple[str, ...] = ()  # files written next to it under its name


FORMATS: dict[str, Target] = {
    "tridb": Target("Micromine TRIDB", ".tridb"),
    "dxf": Target("DXF", ".dxf"),
    "dwg": Target("DWG", ".dwg"),
    "obj": Target("OBJ", ".obj"),
    "stl": Target("STL", ".stl"),
    "dtm": Target("Surpac DTM", ".dtm", (".str",)),
}


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def bad_format() -> ApiError:
    return ApiError(
        422,
        "bad_format",
        "Неизвестный формат экспорта каркасов.",
        "Выберите: Micromine TRIDB, DXF, DWG, OBJ, STL или Surpac DTM.",
    )


def target_path(path: str | Path, fmt: str) -> Path:
    """The file to write: the path as chosen, with the extension of the format when it has another one."""
    target = Path(path)
    suffix = FORMATS[fmt].suffix
    return target if target.suffix.lower() == suffix else target.with_name(target.name + suffix)


def planned(path: str | Path, fmt: str) -> list[Path]:
    """Every file the export to the chosen `path` writes, the main one first."""
    target = target_path(path, fmt)
    return [target, *(target.with_suffix(suffix) for suffix in FORMATS[fmt].more)]


def _options(fmt: str, raw: Any) -> dict[str, Any]:
    """The options of the format as the writer takes them; ApiError — a value it does not know."""
    raw = dict(raw or {})
    out: dict[str, Any] = {}
    if fmt == "dwg":
        from bf_core.formats.dwg import VERSIONS, WRITE_VERSION

        version = str(raw.get("version") or WRITE_VERSION).upper()
        if version not in VERSIONS:
            raise ApiError(
                422,
                "bad_version",
                f"Версия DWG «{raw.get('version')}» не поддерживается.",
                f"Выберите одну из версий: {', '.join(VERSIONS)}.",
            )
        out["version"] = version
    if fmt == "stl":
        out["binary"] = bool(raw.get("binary", True))
    return out


def request(
    conn: sa.Connection,
    *,
    path: str,
    format: str,  # noqa: A002 - the name of the parameter in the API
    wireframe_ids: list[int] | None = None,
    options: Any = None,
    overwrite: bool = False,
    oda: str | None = None,
) -> dict[str, Any]:
    """Checked parameters of the job `export.wireframes` (for the endpoint that starts it); ApiError otherwise.
    Files the export would replace without having been asked about are a 409 `files_exist` with their names in
    `files`, unless `overwrite`. `oda` — the ODA File Converter of «Настройка» (for a DWG)."""
    from bf_core.lines.export import check_path
    from bf_core.rasters.export import existing, files_exist

    if format not in FORMATS:
        raise bad_format()
    check_path(path)
    opts = _options(format, options)
    ids = list(dict.fromkeys(int(i) for i in wireframe_ids or []))
    if not ids:
        raise ApiError(
            404, "wireframe_not_found", "Каркасы для экспорта не выбраны.", "Выберите каркасы в дереве проекта."
        )
    if len(ids) > MAX_WIREFRAMES:
        raise ApiError(
            422,
            "too_many_wireframes",
            f"Каркасов для экспорта больше {_n(MAX_WIREFRAMES)}.",
            "Экспортируйте каркасы несколькими файлами.",
        )
    rows = conn.execute(
        sa.select(objects.c.id, wireframes.c.point_count, wireframes.c.triangle_count)
        .join(wireframes, wireframes.c.object_id == objects.c.id)
        .where(objects.c.id.in_(ids), objects.c.kind == ws.WIREFRAME, objects.c.state == "ready")
    ).all()
    if len(rows) != len(ids):
        raise ApiError(
            404, "wireframe_not_found", "Каркас не найден.", "Обновите дерево проекта и выберите каркасы заново."
        )
    if format == "dwg":
        from bf_core.formats.dwg import HINT_WRITE, find_converter

        if find_converter(oda) is None:
            raise ApiError(422, "oda_not_found", "Запись DWG недоступна: не найден ODA File Converter.", HINT_WRITE)
    if not overwrite:
        clash = existing(planned(path, format), Path(path))
        if clash:
            err = files_exist(clash)
            raise ApiError(409, err.code, err.message, err.hint, extra={"files": [p.name for p in clash]})
    return {
        "path": path,
        "format": format,
        "wireframe_ids": ids,
        "options": opts,
        "overwrite": bool(overwrite),
        "oda": oda,
        "triangles": sum(int(r.triangle_count) for r in rows),
        "largest": max(24 * int(r.point_count) + 12 * int(r.triangle_count) for r in rows),
    }


#: Memory of the export in sizes of the geometry of the largest wireframe (24 bytes a vertex, 12 a triangle).
#: Measured on a wireframe of 620 000 triangles: TRIDB 2.4, OBJ 3.1, DTM 4.2, STL 5.7, DXF 12.7 (the corners
#: of its polyfaces in the order of use and their text).
MEMORY_FACTOR = {"tridb": 4, "obj": 4, "dtm": 6, "stl": 8, "dxf": 16, "dwg": 16}


def _memory(params: dict[str, Any]) -> int:
    """One wireframe at a time: its arrays, its triangles in the order of the file and the portion being
    written (`largest` — the bytes of the geometry of the largest wireframe)."""
    factor = MEMORY_FACTOR.get(str(params.get("format")), 16)
    return max(512, 256 + int(int(params.get("largest") or 0) * factor / 2**20))


# --- writing -------------------------------------------------------------------------------------------


def write_wireframes(
    path: Path,
    meshes: Iterable[Any],
    fmt: str,
    *,
    total: int | None = None,
    options: dict[str, Any] | None = None,
    converter: str | None = None,
    progress: Any = None,
) -> tuple[dict[str, Any], list[str]]:
    """Write `meshset.MeshSet` wireframes as one file of `fmt` (with its companion). `meshes` may be a generator
    — one wireframe is in memory at a time; `total` — their number, for the progress. Returns the counts
    {wireframes, points, triangles, size, files} of what was written and the warnings. Raises `FormatError`."""
    from bf_core.formats.str_layers import scaled
    from bf_core.formats.table import no_progress

    tick = progress or no_progress
    opts = options or {}
    written: Any
    if fmt == "tridb":
        from bf_core.drillholes.edit import author
        from bf_core.formats.tridb_write import write_tridb

        written = write_tridb(path, meshes, tick, total=total, author=author())
    elif fmt == "dxf":
        from bf_core.formats.dxf_write_mesh import write_dxf_meshes

        written = write_dxf_meshes(path, meshes, tick, total=total)
    elif fmt == "dwg":
        from bf_core.formats.dwg import WRITE_VERSION, dwg_output
        from bf_core.formats.dxf_write_mesh import write_dxf_meshes

        version = str(opts.get("version") or WRITE_VERSION)
        with dwg_output(path, scaled(tick, 0.7, 1.0), version=version, converter=converter) as dxf:
            written = write_dxf_meshes(dxf, meshes, scaled(tick, 0.0, 0.7), total=total)
        written.path, written.size, written.files = path, path.stat().st_size, [path]
    elif fmt == "obj":
        from bf_core.formats.obj import write_obj

        written = write_obj(path, meshes, tick, total=total)
    elif fmt == "stl":
        from bf_core.formats.stl import write_stl

        written = write_stl(path, meshes, tick, total=total, binary=bool(opts.get("binary", True)))
    else:
        from bf_core.formats.dtm_surpac import write_dtm

        written = write_dtm(path, meshes, tick, total=total, strings=path.with_suffix(FORMATS["dtm"].more[0]))
    files = [Path(p).name for p in (getattr(written, "files", None) or [written.path])]
    counts = {
        "wireframes": int(written.meshes),
        "points": int(written.points),
        "triangles": int(written.triangles),
        "size": int(written.size),
        "files": files,
    }
    return counts, list(written.warnings)


@handler("export.wireframes", "Экспорт каркасов", queue="heavy", estimate_memory=_memory)
def export_wireframes(ctx: JobContext) -> dict[str, Any]:
    from bf_core.formats.errors import FormatError
    from bf_core.lines.export import late_tick
    from bf_core.rasters.export import existing, files_exist
    from bf_core.storage.blobs import BlobStore

    from . import source as src

    p = ctx.params
    fmt = str(p.get("format"))
    if fmt not in FORMATS:
        err = bad_format()
        raise JobError(err.code, err.message, err.hint)
    if ctx.project_folder is None:
        raise RuntimeError("export.wireframes is a project job")
    chosen = Path(p["path"])
    target = target_path(chosen, fmt)
    ids = [int(i) for i in p.get("wireframe_ids") or []]
    engine = ctx.engine()
    store = BlobStore(ctx.project_folder)
    ctx.tick(0.0, "Чтение каркасов")
    if not bool(p.get("overwrite")):  # asked for: the window showed the files that will be replaced
        clash = existing(planned(chosen, fmt), chosen)
        if clash:
            err = files_exist(clash)
            raise JobError(err.code, err.message, err.hint)

    def meshes() -> Iterator[Any]:
        for wf_id in ids:  # a short read transaction a wireframe: the writer holds one at a time
            with dialect.read_tx(engine) as conn:
                mesh = src.read_mesh(conn, store, wf_id)
            yield mesh

    def tick(fraction: float, message: str | None = None) -> None:
        if fraction >= 1.0:  # the last word of a writer: its file is under its name already
            late_tick(ctx, 1.0, message)
        else:
            ctx.tick(max(0.0, fraction), message)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        counts, warnings = write_wireframes(
            target, meshes(), fmt, total=len(ids), options=p.get("options"), converter=p.get("oda"), progress=tick
        )
    except FormatError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    except ApiError as exc:  # a wireframe went away while the file was written
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
        f"Записан файл «{target.name}» ({FORMATS[fmt].label}): каркасов {_n(counts['wireframes'])}, "
        f"треугольников {_n(counts['triangles'])}, вершин {_n(counts['points'])}"
        + (f"; рядом — {', '.join(f'«{name}»' for name in counts['files'][1:])}" if len(counts["files"]) > 1 else "")
    )
    with dialect.write_tx(engine) as conn:
        alive = conn.execute(sa.select(objects.c.id).where(objects.c.id.in_(ids[:1]))).scalar()
        conn.execute(
            sa.insert(export_log).values(
                object_id=alive,
                job_id=ctx.id,
                file_name=target.name,
                format=fmt,
                settings={"wireframe_ids": ids, "options": p.get("options") or {}, "files": counts["files"]},
                counts={**{k: v for k, v in counts.items() if k != "files"}, "warnings": len(warnings)},
                created_at=dialect.utcnow(),
            )
        )
    late_tick(ctx, 1.0, "Готово")
    return {"path": str(target), "format": fmt, **counts, "warnings": warnings}
