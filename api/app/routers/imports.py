import re
import uuid
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.config import settings
from app.formats.micromine import MicromineFormatError
from app.importing.schema import KIND_LABELS, SCHEMAS
from app.importing.service import (
    UnsupportedFormatError,
    apply_mapping,
    detect_kind,
    read_table,
    suggest_components,
    suggest_mapping,
    to_records,
)

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


class UploadResponse(BaseModel):
    file_id: str
    filename: str
    format: str
    row_count: int
    columns: list[ColumnInfo]
    detected_kind: str | None
    suggestions: dict[str, Suggestion]
    preview: list[dict]


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


def _stored_path(file_id: str) -> Path:
    if not _FILE_ID_RE.match(file_id):
        raise HTTPException(400, "Некорректный идентификатор файла")
    matches = list(settings.upload_dir.glob(f"{file_id}.*"))
    if not matches:
        raise HTTPException(404, "Файл не найден, загрузите его заново")
    return matches[0]


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


@router.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile) -> UploadResponse:
    filename = file.filename or "upload"
    suffix = Path(filename).suffix.lower()
    file_id = uuid.uuid4().hex
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    path = settings.upload_dir / f"{file_id}{suffix}"

    size = 0
    with path.open("wb") as out:
        while chunk := await file.read(_CHUNK):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                out.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, "Файл слишком большой")
            out.write(chunk)

    try:
        df, fmt = read_table(path)
    except UnsupportedFormatError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(415, str(exc)) from exc
    except MicromineFormatError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422, f"Не удалось прочитать файл: {exc}") from exc

    columns = [str(c) for c in df.columns]
    suggestions = {}
    for kind in SCHEMAS:
        mapping = suggest_mapping(kind, columns)
        suggestions[kind] = Suggestion(
            mapping=mapping,
            components=suggest_components(df, mapping) if kind == "assay" else [],
        )

    return UploadResponse(
        file_id=file_id,
        filename=filename,
        format=fmt,
        row_count=len(df),
        columns=_column_info(df),
        detected_kind=detect_kind(columns),
        suggestions=suggestions,
        preview=to_records(df, 10),
    )


@router.post("/validate", response_model=ValidateResponse)
def validate(req: ValidateRequest) -> ValidateResponse:
    if req.kind not in SCHEMAS:
        raise HTTPException(400, f"Неизвестный тип таблицы: {req.kind}")
    df, _ = read_table(_stored_path(req.file_id))

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
