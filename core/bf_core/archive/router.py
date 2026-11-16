"""/api/v1/projects/export, /projects/import — the windows «Экспорт проекта» and «Импорт проекта» (ТЗ 7.4)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from .container import ArchiveError, BadPassword, TooNew
from .importer import inspect

router = APIRouter(prefix="/projects")
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]


class ExportBody(BaseModel):
    path: str = Field(max_length=1000)
    password: str | None = Field(default=None, max_length=1000)
    include_view: bool = True


class InspectBody(BaseModel):
    path: str = Field(max_length=1000)
    password: str | None = Field(default=None, max_length=1000)


class ImportBody(InspectBody):
    mode: Literal["new", "copy", "rename", "replace"] = "new"
    name: str | None = Field(default=None, max_length=200)
    apply_view: bool = True


def _archive_error(exc: ArchiveError) -> ApiError:
    if isinstance(exc, BadPassword):
        return ApiError(422, "bad_password", str(exc), "Введите пароль, с которым проект экспортирован.")
    if isinstance(exc, TooNew):
        return ApiError(422, "archive_too_new", str(exc), "Установите новую версию BedrockForge.")
    return ApiError(422, "archive_error", str(exc), "Проверьте файл: он мог быть повреждён при копировании.")


def _file(path: str) -> Path:
    p = Path(path)
    if not p.is_file():
        raise ApiError(404, "file_not_found", "Файл не найден.", "Выберите файл .bfggis заново.")
    return p


@router.post("/export")
def export_project(svc: Svc, s: Sched, body: ExportBody) -> dict[str, Any]:
    project = svc.require_current()
    if project.read_only:
        raise ApiError(
            423,
            "project_read_only",
            "Проект открыт только для чтения: экспорт выполняет окно, в котором проект открыт для записи.",
            "Экспортируйте проект из того окна или закройте его там.",
        )
    out = Path(body.path)
    if out.suffix.lower() != ".bfggis":
        out = out.with_name(out.name + ".bfggis")
    if not out.parent.is_dir():
        raise ApiError(422, "folder_not_found", "Папка для файла не найдена.", "Выберите другую папку.")
    title = f"Экспорт проекта «{project.meta()['name']}»"
    params = {"path": str(out), "include_view": body.include_view, "encrypted": bool(body.password)}
    key, job_id = s.submit("export.project", params, title=title, secret=body.password or None)
    return {"source": key, "id": job_id, "path": str(out)}


def _unique_copy_name(svc: ProjectService, name: str) -> str:
    taken = {dialect.name_key(e["name"]) for e in svc.list()}
    n = 2
    while dialect.name_key(f"{name} ({n})") in taken:
        n += 1
    return f"{name} ({n})"


@router.post("/import/inspect")
def inspect_file(svc: Svc, body: InspectBody) -> dict[str, Any]:
    try:
        info = inspect(_file(body.path), body.password)
    except ArchiveError as exc:
        raise _archive_error(exc) from exc
    if info.get("needs_password"):
        return info
    same = [e for e in svc.list() if dialect.name_key(e["name"]) == dialect.name_key(info["name"])]
    info["name_taken"] = bool(same)
    info["existing_uid"] = same[0]["uid"] if same else None
    info["copy_name"] = _unique_copy_name(svc, info["name"]) if same else info["name"]
    return info


@router.post("/import")
def import_file(svc: Svc, s: Sched, body: ImportBody) -> dict[str, Any]:
    path = _file(body.path)
    try:
        info = inspect(path, body.password)
    except ArchiveError as exc:
        raise _archive_error(exc) from exc
    if info.get("needs_password"):
        raise ApiError(422, "bad_password", "Файл защищён паролем.", "Введите пароль.")
    entries = svc.list()
    file_name = svc.clean_name(str(info.get("name") or "Проект"))
    same = [e for e in entries if dialect.name_key(e["name"]) == dialect.name_key(file_name)]
    params: dict[str, Any] = {
        "path": str(path),
        "root": str(svc.root),
        "apply_view": body.apply_view,
        "encrypted": bool(body.password),
        "token": uuid.uuid4().hex,  # a retry after a crash finds the project the first attempt made
    }
    if body.mode == "new" and not same:
        params["name"] = file_name
    elif body.mode in ("new", "copy"):
        params["name"] = _unique_copy_name(svc, file_name)
    elif body.mode == "rename":
        name = svc.clean_name(body.name or "")
        if any(dialect.name_key(e["name"]) == dialect.name_key(name) for e in entries):
            raise ApiError(409, "name_taken", f"Проект «{name}» уже есть.", "Выберите другое название.")
        params["name"] = name
    else:  # replace: the old project goes only after a successful import
        if not same:
            raise ApiError(404, "project_not_found", "Заменяемый проект не найден.", "Выберите «Создать копию».")
        old = same[0]
        if svc.busy(old["uid"]):
            raise ApiError(
                409,
                "project_busy",
                "У заменяемого проекта выполняются задания.",
                "Дождитесь их окончания или отмените их.",
            )
        if svc.current and svc.current.uid == old["uid"]:
            svc.close()
        params["name"] = file_name
        params["replace_folder"] = old["path"]
        params["replace_uid"] = old["uid"]
    title = f"Импорт проекта «{params['name']}»"
    key, job_id = s.submit("import.project", params, title=title, secret=body.password or None)
    return {"source": key, "id": job_id, "name": params["name"]}
