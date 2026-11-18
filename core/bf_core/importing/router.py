"""Import wizards.

/api/v1/drillholes/import — «Импорт скважин» (ТЗ 7.7): look into a file, check a mapping, start.
/api/v1/import/objects — «Импорт файлов» (ТЗ 7.8, ADR 0011 §3): the composition of the chosen files of lines,
points and rasters (`inspect`, nothing is written) and the start of the job `import.objects`.
/api/v1/import/trenches — «Канавы по полилиниям» (ТЗ 7.35 Д-16, ADR 0011 §5): `inspect` — the trenches the
file gives with the chosen name and direction (nothing is written), and the start of the job `import.trenches`.
/api/v1/tools — the programs of the user the formats need (ODA File Converter for DWG): `tools.py`.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_holes, dh_tables, objects
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from .service import duplicate_targets
from .tools import router as tools_router

dh = APIRouter(prefix="/drillholes")
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Kind = Literal["collar", "survey", "assay", "interval", "event"]


class Options(BaseModel):
    encoding: str | None = None
    delimiter: str | None = None
    header_row: int = Field(default=1, ge=0, le=1000)
    sheet: str | None = None
    text_columns: list[str] = Field(default_factory=list)


class InspectBody(BaseModel):
    path: str = Field(max_length=2000)
    options: Options = Field(default_factory=Options)


class Component(BaseModel):
    column: str = Field(max_length=500)
    unit: str = Field(default="", max_length=50)


class FileSpec(InspectBody):
    kind: Kind | None = None
    mapping: dict[str, str] | None = None
    components: list[Component] | None = None
    attributes: list[str] | None = None
    force: bool = False
    table_name: str | None = Field(default=None, max_length=200)


class StartBody(BaseModel):
    set_id: int | None = None
    set_name: str | None = Field(default=None, max_length=200)
    crs: str | None = Field(default=None, max_length=20000)
    files: list[FileSpec] = Field(min_length=1, max_length=20)


def set_name(raw: str | None) -> str:
    """The name of a new set as it is stored: inner runs of spaces collapsed (the job uses the same)."""
    return " ".join((raw or "").split())


def _file(path: str) -> Path:
    p = Path(path)
    if not p.is_file():
        raise ApiError(404, "file_not_found", f"Файл не найден: {p.name}", "Выберите файл заново.")
    if p.stat().st_size > 4 << 30:
        raise ApiError(422, "file_too_large", "Файл больше 4 ГБ.", "Разделите файл на части.")
    return p


def _format_error(exc: Exception) -> ApiError:
    return ApiError(
        422, getattr(exc, "code", "format_error"), getattr(exc, "message", str(exc)), getattr(exc, "hint", "")
    )


# One read table is kept between «Проверить» calls of the wizard: changing the mapping of a 5 million-row
# file must not read it again. Keyed by path, size, mtime and read options.
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {}


def _read(path: Path, options: Options) -> Any:
    from bf_core.formats.registry import read_table

    from .jobs import options_of

    st = path.stat()
    key = f"{path}|{st.st_size}|{st.st_mtime_ns}|{options.model_dump_json()}"
    with _cache_lock:
        if _cache.get("key") == key:
            return _cache["table"]
    table = read_table(path, options_of(options.model_dump()))
    with _cache_lock:
        _cache.clear()
        _cache.update(key=key, table=table)
    return table


def forget_cached() -> None:
    with _cache_lock:
        _cache.clear()


@dh.post("/import/inspect")
def inspect(svc: Svc, body: InspectBody) -> dict[str, Any]:
    """Step «файл»: format, encoding, delimiter, sheets and the first rows as they are in the file."""
    svc.require_current()
    from bf_core.formats.errors import FormatError
    from bf_core.formats.inspect import inspect_table

    from .jobs import options_of

    path = _file(body.path)
    try:
        pv = inspect_table(path, options_of(body.options.model_dump()))
    except FormatError as exc:
        raise _format_error(exc) from exc
    info = pv.info
    return {
        "format": pv.kind,
        "summary": pv.summary,
        "info": {
            "encoding": info.encoding,
            "delimiter": info.delimiter,
            "header_row": info.header_row,
            "sheet": info.sheet,
            "sheets": info.sheets,
        },
        "rows": pv.rows,
        "numbers": pv.numbers,
        "fields": pv.fields,
        "estimated_rows": pv.estimated_rows,
        "warnings": pv.warnings,
        "size": path.stat().st_size,
    }


@dh.post("/import/check")
def check(svc: Svc, body: FileSpec) -> dict[str, Any]:
    """Step «колонки»: the whole file is read; kind and mapping are suggested unless given; issues and the
    normalized first rows (ТЗ 7.7 «Предпросмотр»)."""
    svc.require_current()
    from bf_core.formats.errors import FormatError

    from .service import detect_kind, normalize, suggest_components, suggest_mapping

    path = _file(body.path)
    try:
        table = _read(path, body.options)
    except FormatError as exc:
        raise _format_error(exc) from exc
    kind = body.kind or detect_kind(table.names)
    mapping = body.mapping if body.mapping is not None else (suggest_mapping(kind, table.names) if kind else {})
    components = (
        [c.model_dump() for c in body.components]
        if body.components is not None
        else (suggest_components(table, mapping) if kind == "assay" else [])
    )
    columns = [{"name": c.name, "kind": c.kind, "sample": _sample(c)} for c in table.columns]
    out: dict[str, Any] = {
        "rows": table.rows,
        "columns": columns,
        "kind": kind,
        "mapping": mapping,
        "components": components,
        "attributes": body.attributes or [],
        "warnings": table.warnings,
        "issues": [],
        "preview": [],
    }
    from .schema import SCHEMAS

    out["fields"] = [{"key": f.key, "label": f.label, "required": f.required} for f in SCHEMAS[kind]] if kind else []
    if kind is None:
        out["issues"] = [
            {"severity": "error", "code": "unknown_kind", "message": "Тип таблицы не распознан: выберите его вручную."}
        ]
        return out
    norm, issues = normalize(
        table, kind, mapping, components, body.attributes or [], force=body.force, table_name=body.table_name
    )
    out["issues"] = [i.as_dict() for i in issues]
    # «Загрузить с ошибками» is offered while the issues include errors in the data (reported as warnings once
    # the flag is on) and nothing else blocks: errors in the mapping are never loaded that way.
    data = _data_errors()
    out["can_force"] = any(i.code in data for i in issues) and all(
        i.severity == "warning" or i.code in data for i in issues
    )
    if norm is not None:
        out["counts"] = norm.counts
        out["preview"] = _preview(norm)
    return out


def _data_errors() -> frozenset[str]:
    from .service import DATA_ERRORS

    return DATA_ERRORS


def _sample(col: Any, n: int = 5) -> list[Any]:
    """The first values of a column without converting all of it (millions of rows) to Python."""
    values = col.values[:n] if col.kind == "number" else col.values.slice(0, n)
    return [_jsonable(v) for v in values.tolist()] if col.kind == "number" else values.to_pylist()


def _plain(v: Any) -> Any:
    """numpy scalars → Python (JSON)."""
    return v.item() if hasattr(v, "item") else v


def _jsonable(v: Any) -> Any:
    return None if isinstance(v, float) and v != v else v


def _preview(norm: Any, limit: int = 30) -> list[dict[str, Any]]:
    if norm.kind == "collar":
        return [
            {"hole_id": c.name, "x": c.x, "y": c.y, "z": c.z, "td": c.td, "azimuth": c.azimuth, "dip": c.dip, **c.attrs}
            for c in norm.collars[:limit]
        ]
    if norm.kind == "survey":
        return [
            {"hole_id": r[0], "depth": r[1], "azimuth": r[2], "dip": r[3], **(r[4] or {})} for r in norm.surveys[:limit]
        ]
    t = norm.table
    rows = []
    for i in range(min(limit, len(t.holes))):
        row = {"hole_id": t.holes[i], "from": _jsonable(float(t.from_m[i])), "to": _jsonable(float(t.to_m[i]))}
        row.update({c.name: _jsonable(_plain(vals[i])) for c, vals in zip(t.columns, t.values, strict=True)})
        rows.append(row)
    return rows


@dh.post("/import")
def start(svc: Svc, s: Sched, body: StartBody) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    for f in body.files:
        _file(f.path)
        if f.kind is None or f.mapping is None:
            raise ApiError(
                422,
                "mapping_missing",
                f"Для «{Path(f.path).name}» не назначены колонки.",
                "Пройдите шаг файла в мастере.",
            )
    clash = duplicate_targets([f.model_dump() for f in body.files])
    if clash:
        raise ApiError(409, "files_clash", clash, "Уберите лишний файл или дайте таблице другое название.")
    with dialect.read_tx(project.engine) as conn:
        if body.set_id is not None:
            if (
                conn.execute(
                    sa.select(objects.c.id).where(objects.c.id == body.set_id, objects.c.kind == "drillhole_set")
                ).first()
                is None
            ):
                raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Выберите набор заново.")
        else:
            name = set_name(body.set_name)
            if not name:
                raise ApiError(422, "name_empty", "Название набора не может быть пустым.", "Введите название набора.")
            taken = conn.execute(
                sa.select(objects.c.id).where(
                    objects.c.kind == "drillhole_set",
                    objects.c.name_key == dialect.name_key(name),
                    objects.c.state == "ready",
                )
            ).first()
            if taken is not None:
                raise ApiError(
                    409,
                    "name_taken",
                    f"Набор «{name}» уже есть.",
                    "Выберите его как набор для загрузки или введите другое название.",
                )
    forget_cached()  # the worker reads the files itself: free the wizard's copy
    params = {
        "set_id": body.set_id,
        "set_name": set_name(body.set_name) if body.set_id is None else None,
        "crs": body.crs,
        "files": [f.model_dump() for f in body.files],
    }
    title = "Импорт скважин" + (f" в «{params['set_name']}»" if params["set_name"] else "")
    key, job_id = s.submit("import.drillholes", params, title=title)
    return {"source": key, "id": job_id}


@dh.get("/sets")
def list_sets(svc: Svc) -> dict[str, Any]:
    """Sets of the open project for «Набор для загрузки» (the tree of sets — Э4)."""
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        holes = (
            sa.select(sa.func.count()).select_from(dh_holes).where(dh_holes.c.set_id == objects.c.id).scalar_subquery()
        )
        rows = [
            dict(r)
            for r in conn.execute(
                sa.select(objects.c.id, objects.c.name, objects.c.crs, holes.label("holes"))
                .where(objects.c.kind == "drillhole_set", objects.c.state == "ready")
                .order_by(objects.c.sort_key, objects.c.id)
            ).mappings()
        ]
        tables: dict[int, list[dict[str, Any]]] = {}
        for t in conn.execute(
            sa.select(dh_tables.c.set_id, dh_tables.c.name, dh_tables.c.kind, dh_tables.c.row_count)
            .where(dh_tables.c.state == "ready")
            .order_by(dh_tables.c.id)
        ).mappings():
            tables.setdefault(t["set_id"], []).append({"name": t["name"], "kind": t["kind"], "rows": t["row_count"]})
        for r in rows:  # «Заменит таблицу …» in the wizard
            r["tables"] = tables.get(r["id"], [])
        return {"sets": rows}


@dh.post("/import/forget")
def forget() -> dict[str, Any]:
    """The wizard closed: the table it kept read is not needed any more."""
    forget_cached()
    return {"ok": True}


class PrjBody(BaseModel):
    path: str = Field(max_length=2000)


@dh.post("/crs/describe")
def describe_prj(body: PrjBody) -> dict[str, Any]:
    """A .prj chosen in the wizard: its name and WKT (ТЗ 9.12)."""
    from bf_core.crs import CrsError, read_prj

    try:
        return read_prj(_file(body.path)).as_dict()
    except CrsError as exc:
        raise ApiError(
            422, "crs_unreadable", str(exc), "Выберите другой файл .prj или оставьте систему координат не заданной."
        ) from exc


# --- «Импорт файлов»: lines, points, rasters (ТЗ 7.8, ADR 0011 §3) -----------------------------------------

objects_router = APIRouter(prefix="/import/objects")


class ObjectOptions(BaseModel):
    encoding: str | None = Field(default=None, max_length=40)
    # columns of a table of points, when they are not to be guessed (z: "" — no heights)
    x: str | None = Field(default=None, max_length=500)
    y: str | None = Field(default=None, max_length=500)
    z: str | None = Field(default=None, max_length=500)
    name: str | None = Field(default=None, max_length=200)


class RasterLevel(BaseModel):
    """«Уровень растров по Z» (ТЗ 7.19): numbers, or text with a comma or a point; empty — not set."""

    elevation: float | str | None = None
    shift: float | str | None = None


class ObjectsBody(BaseModel):
    files: list[Annotated[str, Field(max_length=2000)]] = Field(min_length=1, max_length=500)
    options: dict[str, ObjectOptions] = Field(default_factory=dict)  # by the path of the main file
    raster_z: RasterLevel = Field(default_factory=RasterLevel)
    # an image without a georeference stands in the plane of the current section line (ТЗ 7.19)
    section: bool = True
    folder: str | None = Field(default=None, max_length=200)


def _objects_request(svc: ProjectService, body: ObjectsBody, *, writable: bool) -> dict[str, Any]:
    """Checked parameters of the composition and of the job: files, read options, the level, the section."""
    from bf_core.rasters.store import section_edges

    from .object_files import NUMBER_HINT, option_key, parse_level, usable_section

    project = svc.require_current(writable=writable)
    files = [str(_file(path)) for path in body.files]
    try:
        elevation, shift = parse_level(body.raster_z.model_dump())
    except ValueError as exc:
        raise ApiError(422, "not_a_number", str(exc), NUMBER_HINT) from exc
    section = None
    if body.section:
        with dialect.read_tx(project.engine) as conn:
            section = usable_section(section_edges(conn))
    return {
        "files": files,
        # the keys as `files` are spelled: «C:/a/b.csv» of the wizard and «C:\\a\\b.csv» of the job are one file
        "options": {option_key(path): o.model_dump(exclude_none=True) for path, o in body.options.items()},
        "raster_z": {"elevation": elevation, "shift": shift},
        "section": section,
        "folder": " ".join(body.folder.split()) or None if body.folder else None,
    }


@objects_router.post("/inspect")
def inspect_objects(svc: Svc, body: ObjectsBody) -> dict[str, Any]:
    """The composition of the chosen files: formats, layers, lines, points and vertices, CRS, warnings, rasters
    with their georeference, and what will not load and why. Nothing is written to the project."""
    from .object_files import inspect_files

    params = _objects_request(svc, body, writable=False)
    return inspect_files(params["files"], params["options"], params["raster_z"], params["section"])


@objects_router.post("")
def start_objects(svc: Svc, s: Sched, body: ObjectsBody) -> dict[str, Any]:
    from .object_files import plan

    params = _objects_request(svc, body, writable=True)
    groups, rest = plan(params["files"])
    if not groups:
        raise ApiError(
            422,
            "nothing_to_import",
            "Среди выбранных файлов нет ни одного, который можно загрузить.",
            "Подходят: стринги .str / .pit, .dxf, .shp, .mif, точки .xyz / .csv / .txt / .dat, растры с привязкой.",
            extra={"rest": rest},
        )
    names = [g.main.name for g in groups]
    title = "Импорт файлов: " + ", ".join(names[:3]) + (f" и ещё {len(names) - 3}" if len(names) > 3 else "")
    key, job_id = s.submit("import.objects", params, title=title[:400])
    return {"source": key, "id": job_id}


# --- «Канавы по полилиниям» (ТЗ 7.35 Д-16, ADR 0011 §5) ------------------------------------------------------

trenches_router = APIRouter(prefix="/import/trenches")
Name = Annotated[str, Field(max_length=500)]


class VertexTable(BaseModel):
    """How a table of vertices «канава, X, Y, Z» is read."""

    encoding: str | None = Field(default=None, max_length=40)
    delimiter: str | None = Field(default=None, max_length=20)
    header_row: int = Field(default=1, ge=0, le=1000)
    sheet: str | None = Field(default=None, max_length=200)


class TrenchLines(BaseModel):
    """The file of polylines and how its trenches are named and directed (`trench_lines.py`)."""

    path: str = Field(max_length=2000)
    encoding: str | None = Field(default=None, max_length=40)
    layers: list[Name] | None = Field(default=None, max_length=5000)  # null — every layer of lines
    name_from: Literal["string", "field"] = "string"
    name_field: Name | None = None
    # «Обратить направление»: every trench, or the named ones
    reverse: bool | Annotated[list[Name], Field(max_length=100_000)] = False
    x: Name | None = None
    y: Name | None = None
    z: Name | None = None
    table: VertexTable | None = None


class TrenchSamples(FileSpec):
    """The table «канава, от, до, …», mapped as a file of the drillhole wizard (ТЗ 7.7)."""

    kind: Literal["assay", "interval"] = "assay"  # type: ignore[assignment]
    replace: bool = False  # the set has a table of samples already: replace it, for all its holes


class TrenchesInspectBody(BaseModel):
    lines: TrenchLines
    limit: int = Field(default=30, ge=1, le=2000)  # trenches listed in the preview


class TrenchesBody(BaseModel):
    set_id: int | None = None
    set_name: str | None = Field(default=None, max_length=200)
    crs: str | None = Field(default=None, max_length=20000)
    lines: TrenchLines
    samples: TrenchSamples | None = None


def _lines_spec(lines: TrenchLines) -> dict[str, Any]:
    spec = lines.model_dump(exclude_none=True)
    spec["path"] = str(_file(lines.path))
    return spec


_JOB_STATUS = {"set_not_found": 404, "name_taken": 409, "assay_exists": 409}


def _job_error(exc: Any) -> ApiError:
    """A `JobError` of the checks shared with the job, as an answer of the API."""
    return ApiError(_JOB_STATUS.get(exc.code, 422), exc.code, exc.message, exc.hint)


@trenches_router.post("/inspect")
def inspect_trenches(svc: Svc, body: TrenchesInspectBody) -> dict[str, Any]:
    """What the file gives: layers, the fields a name can be taken from, counts, the total 3D length, warnings
    and the first trenches (name, vertices, length, the first vertex in the chosen direction). Nothing is written."""
    svc.require_current()
    from bf_core.jobs.context import JobError

    from .trench_lines import preview

    try:
        return preview(_lines_spec(body.lines), limit=body.limit)
    except JobError as exc:
        raise _job_error(exc) from exc


@trenches_router.post("")
def start_trenches(svc: Svc, s: Sched, body: TrenchesBody) -> dict[str, Any]:
    from bf_core.jobs.context import JobError

    from .trenches import _check_target

    project = svc.require_current(writable=True)
    samples = None
    if body.samples is not None:
        if body.samples.mapping is None:
            raise ApiError(
                422,
                "mapping_missing",
                f"Для «{Path(body.samples.path).name}» не назначены колонки.",
                "Назначьте колонки таблицы проб в мастере.",
            )
        samples = body.samples.model_dump()
        samples["path"] = str(_file(body.samples.path))
    params: dict[str, Any] = {
        "set_id": body.set_id,
        "set_name": set_name(body.set_name) if body.set_id is None else None,
        "crs": body.crs,
        "lines": _lines_spec(body.lines),
        "samples": samples,
    }
    try:
        with dialect.read_tx(project.engine) as conn:
            _check_target(conn, params, samples)  # the set, its name and its table of samples — before the queue
    except JobError as exc:
        raise _job_error(exc) from exc
    forget_cached()  # the worker reads the table of samples itself
    title = "Импорт канав" + (f" в «{params['set_name']}»" if params["set_name"] else "")
    key, job_id = s.submit("import.trenches", params, title=title)
    return {"source": key, "id": job_id}


router = APIRouter()
router.include_router(dh)
router.include_router(objects_router)
router.include_router(trenches_router)
router.include_router(tools_router)
