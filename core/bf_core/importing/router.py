"""/api/v1/drillholes/import — the wizard «Импорт скважин» (ТЗ 7.7): look into a file, check a mapping, start."""

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

router = APIRouter(prefix="/drillholes")
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


@router.post("/import/inspect")
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


@router.post("/import/check")
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


@router.post("/import")
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


@router.get("/sets")
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


@router.post("/import/forget")
def forget() -> dict[str, Any]:
    """The wizard closed: the table it kept read is not needed any more."""
    forget_cached()
    return {"ok": True}


class PrjBody(BaseModel):
    path: str = Field(max_length=2000)


@router.post("/crs/describe")
def describe_prj(body: PrjBody) -> dict[str, Any]:
    """A .prj chosen in the wizard: its name and WKT (ТЗ 9.12)."""
    from bf_core.crs import CrsError, read_prj

    try:
        return read_prj(_file(body.path)).as_dict()
    except CrsError as exc:
        raise ApiError(
            422, "crs_unreadable", str(exc), "Выберите другой файл .prj или оставьте систему координат не заданной."
        ) from exc
