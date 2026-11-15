import json
import re
import shutil
import uuid
import zipfile
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.formats.micromine import MicromineFormatError
from app.formats.tabular import ReadOptions, TableRead
from app.importing.schema import KIND_LABELS, SCHEMAS
from app.importing.service import (
    UnsupportedFormatError,
    apply_mapping,
    check_supported,
    detect_kind,
    read_table,
    suggest_components,
    suggest_mapping,
    to_records,
)
from app.importing.store import commit_import
from app.models import Dataset
from app.routers.projects import get_project, get_set

router = APIRouter(prefix="/api/import", tags=["import"])

_FILE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_CHUNK = 1024 * 1024


class ColumnInfo(BaseModel):
    name: str
    dtype: str
    numeric: bool
    sample: list


class Suggestion(BaseModel):
    mapping: dict[str, str]
    components: list[dict[str, str]]


class ReadOptionsIn(BaseModel):
    """Reading parameters for CSV/TXT/Excel; null = detect automatically."""

    encoding: str | None = None
    delimiter: str | None = None
    header_row: int = Field(1, ge=0, le=1000)
    sheet: str | None = None


class UploadResponse(BaseModel):
    file_id: str
    filename: str
    format: str
    row_count: int
    columns: list[ColumnInfo]
    detected_kind: str | None
    suggestions: dict[str, Suggestion]
    preview: list[dict]
    # Parameters actually used to read the file (detected or chosen).
    options: ReadOptionsIn
    sheets: list[str]
    notes: list[str]


class Component(BaseModel):
    column: str
    unit: str


class ValidateRequest(BaseModel):
    file_id: str
    kind: str
    mapping: dict[str, str]
    components: list[Component] = Field(default_factory=list)
    attributes: list[str] = Field(default_factory=list)


class IssueOut(BaseModel):
    severity: str
    code: str
    message: str
    field: str | None = None
    count: int | None = None


class ValidateResponse(BaseModel):
    ok: bool
    row_count: int
    issues: list[IssueOut]
    columns: list[str]
    preview: list[dict]


@router.get("/schemas")
def schemas() -> dict[str, dict]:
    """Target tables and their fields, so the UI does not hardcode them."""
    return {
        kind: {
            "label": KIND_LABELS[kind],
            "fields": [
                {"key": f.key, "label": f.label, "required": f.required, "numeric": f.numeric}
                for f in fields
            ],
        }
        for kind, fields in SCHEMAS.items()
    }


# Each upload lives in its own folder: the file itself plus meta.json
# (original name and the reading options), so validation and commit read it
# exactly as the user saw it in the preview.
def _upload_folder(file_id: str) -> Path:
    if not _FILE_ID_RE.match(file_id):
        raise HTTPException(400, "Некорректный идентификатор файла")
    folder = settings.upload_dir / file_id
    if not (folder / "meta.json").exists():
        raise HTTPException(404, "Файл не найден, загрузите его заново")
    return folder


def _meta(folder: Path) -> dict:
    return json.loads((folder / "meta.json").read_text(encoding="utf-8"))


def _data_path(folder: Path, meta: dict) -> Path:
    return folder / f"data{meta['suffix']}"


# Upload -> validate -> commit reads the same file three times; a big Excel book
# takes seconds each time. Keep the last few readings (single-user app).
_CACHE: OrderedDict[tuple[str, str], TableRead] = OrderedDict()
_CACHE_SIZE = 4


def _cache_key(folder: Path, options: ReadOptions) -> tuple[str, str]:
    return str(folder), json.dumps(asdict(options), sort_keys=True)


def _remember(folder: Path, options: ReadOptions, table: TableRead) -> None:
    _CACHE[_cache_key(folder, options)] = table
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)


def _read(folder: Path, meta: dict, options: ReadOptions) -> TableRead:
    """Read the stored file; reading problems become HTTP errors the UI can show."""
    key = _cache_key(folder, options)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    try:
        table = read_table(_data_path(folder, meta), options)
    except UnsupportedFormatError as exc:
        raise HTTPException(415, str(exc)) from exc
    except (MicromineFormatError, ValueError, zipfile.BadZipFile, KeyError, OSError) as exc:
        raise HTTPException(422, f"Не удалось прочитать файл: {exc}") from exc
    _remember(folder, options, table)
    return table


def _read_stored(file_id: str) -> tuple[TableRead, dict]:
    folder = _upload_folder(file_id)
    meta = _meta(folder)
    return _read(folder, meta, ReadOptions(**meta["options"])), meta


def _used_options(table: TableRead, requested: ReadOptions) -> ReadOptions:
    """The options that reproduce this reading (detected values fixed)."""
    return ReadOptions(
        encoding=table.detected.get("encoding") if table.format == "text" else None,
        delimiter=table.detected.get("delimiter"),
        header_row=requested.header_row,
        sheet=table.detected.get("sheet"),
    )


def _column_info(df: pd.DataFrame) -> list[ColumnInfo]:
    head = to_records(df, 3)
    return [
        ColumnInfo(
            name=str(col),
            dtype=str(df[col].dtype),
            numeric=bool(pd.api.types.is_numeric_dtype(df[col])),
            sample=[row[col] for row in head],
        )
        for col in df.columns
    ]


def _describe(file_id: str, meta: dict, table: TableRead) -> UploadResponse:
    df = table.frame
    columns = [str(c) for c in df.columns]
    suggestions = {}
    for kind in SCHEMAS:
        mapping = suggest_mapping(kind, columns)
        suggestions[kind] = Suggestion(
            mapping=mapping,
            components=suggest_components(df, mapping) if kind == "assay" else [],
        )
    options = meta["options"]
    return UploadResponse(
        file_id=file_id,
        filename=meta["filename"],
        format=table.format,
        row_count=len(df),
        columns=_column_info(df),
        detected_kind=detect_kind(columns),
        suggestions=suggestions,
        preview=to_records(df, 10),
        options=ReadOptionsIn(**options) if table.format != "micromine" else ReadOptionsIn(encoding=table.detected.get("encoding")),
        sheets=table.sheets,
        notes=table.notes,
    )


@router.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile) -> UploadResponse:
    filename = file.filename or "upload"
    suffix = Path(filename).suffix.lower()
    try:
        check_supported(suffix)
    except UnsupportedFormatError as exc:
        raise HTTPException(415, str(exc)) from exc
    file_id = uuid.uuid4().hex
    folder = settings.upload_dir / file_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"data{suffix}"

    size = 0
    with path.open("wb") as out:
        while chunk := await file.read(_CHUNK):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                out.close()
                shutil.rmtree(folder, ignore_errors=True)
                raise HTTPException(413, "Файл слишком большой")
            out.write(chunk)

    meta = {"filename": filename, "suffix": suffix, "options": asdict(ReadOptions())}
    try:
        table = _read(folder, meta, ReadOptions())
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    used = _used_options(table, ReadOptions())
    _remember(folder, used, table)  # validate and commit read with the fixed options
    meta["options"] = asdict(used)
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return _describe(file_id, meta, table)


@router.post("/{file_id}/options", response_model=UploadResponse)
def reread(file_id: str, body: ReadOptionsIn) -> UploadResponse:
    """Read the uploaded file again with other encoding, delimiter, header row or sheet."""
    folder = _upload_folder(file_id)
    meta = _meta(folder)
    requested = ReadOptions(**body.model_dump())
    table = _read(folder, meta, requested)
    used = _used_options(table, requested)
    _remember(folder, used, table)
    meta["options"] = asdict(used)
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return _describe(file_id, meta, table)


class CommitRequest(ValidateRequest):
    # The drillhole set to write into; without it a new set is created in project_id.
    set_id: int | None = None
    project_id: int | None = None
    # Name of the new set (default "Скважины").
    set_name: str | None = Field(None, max_length=255)
    # Defaults to the name of the uploaded file.
    filename: str | None = None
    # Interval tables (kind "interval"): the table's name in the set (default: the file name).
    table_name: str | None = Field(None, max_length=255)


class CommitResponse(BaseModel):
    set_id: int
    job_id: int
    kind: str
    inserted: int
    replaced: int
    issues: list[IssueOut]


@router.post("/commit", response_model=CommitResponse)
def commit(req: CommitRequest, session: Session = Depends(get_session)) -> CommitResponse:
    """Validate the mapping again and replace the set's rows of this kind (a new set if none is given)."""
    if req.kind not in SCHEMAS:
        raise HTTPException(400, f"Неизвестный тип таблицы: {req.kind}")
    if req.set_id is not None:
        dataset = get_set(session, req.set_id)
    elif req.project_id is not None:
        get_project(session, req.project_id)
        dataset = None
    else:
        raise HTTPException(422, "Укажите набор скважин (set_id) или проект (project_id)")
    table, meta = _read_stored(req.file_id)
    df = table.frame
    components = [c.model_dump() for c in req.components] if req.kind == "assay" else []

    normalized, issues = apply_mapping(df, req.kind, req.mapping, components, req.attributes)
    out_issues = [IssueOut(**vars(i)) for i in issues]
    if normalized is None or any(i.severity == "error" for i in issues):
        raise HTTPException(422, {"message": "Назначение колонок содержит ошибки", "issues": [i.model_dump() for i in out_issues]})

    if dataset is None:
        dataset = Dataset(project_id=req.project_id, kind="drillholes", name=(req.set_name or "").strip() or "Скважины", meta={})
        session.add(dataset)
        session.flush()
    if req.kind == "interval":
        from pathlib import PurePath

        from app.importing.store import commit_interval_table

        name = (req.table_name or "").strip() or PurePath(req.filename or meta["filename"]).stem or "Интервалы"
        result = commit_interval_table(session, dataset.id, name[:255], normalized,
                                       filename=(req.filename or meta["filename"])[:255], mapping=req.mapping)
        return CommitResponse(set_id=dataset.id, job_id=result.job_id, kind=result.kind, inserted=result.inserted,
                              replaced=result.replaced, issues=out_issues)
    result = commit_import(
        session,
        dataset.id,
        req.kind,
        normalized,
        filename=(req.filename or meta["filename"])[:255],
        mapping=req.mapping,
        components=components,
    )
    return CommitResponse(
        set_id=dataset.id,
        job_id=result.job_id,
        kind=result.kind,
        inserted=result.inserted,
        replaced=result.replaced,
        issues=out_issues,
    )


@router.post("/validate", response_model=ValidateResponse)
def validate(req: ValidateRequest) -> ValidateResponse:
    if req.kind not in SCHEMAS:
        raise HTTPException(400, f"Неизвестный тип таблицы: {req.kind}")
    df = _read_stored(req.file_id)[0].frame

    normalized, issues = apply_mapping(
        df,
        req.kind,
        req.mapping,
        [c.model_dump() for c in req.components],
        req.attributes,
    )
    out_issues = [IssueOut(**vars(i)) for i in issues]
    ok = normalized is not None and not any(i.severity == "error" for i in issues)
    return ValidateResponse(
        ok=ok,
        row_count=len(df),
        issues=out_issues,
        columns=[] if normalized is None else [str(c) for c in normalized.columns],
        preview=[] if normalized is None else to_records(normalized, 20),
    )
