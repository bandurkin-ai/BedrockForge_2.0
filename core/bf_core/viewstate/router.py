"""/api/v1/view — the project view settings (ТЗ 7.0.10, ADR 0007 §2): read all, write a key with a version.
/api/v1/views — saved views (ТЗ 7.35 Д-09, ADR 0008): list, save, rename / overwrite, delete, apply."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from . import views
from .keys import BadValue
from .service import Conflict, read_all, write

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]


@router.get("/view")
def get_view(svc: Svc) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"values": read_all(conn)}


class PutBody(BaseModel):
    value: Any = None
    expected_version: int = Field(ge=0)
    #: uid of the project the value belongs to: a write that comes late, after another project opened, is refused
    project: str | None = Field(default=None, max_length=64)


@router.put("/view/{key}")
def put_view(svc: Svc, key: str, body: PutBody) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    if body.project is not None and body.project != project.uid:
        raise ApiError(410, "project_changed", "Открыт другой проект.", "Настройка вида прежнего проекта не записана.")
    try:
        with dialect.write_tx(project.engine) as conn:
            return write(conn, key, body.value, body.expected_version)
    except BadValue as e:
        raise ApiError(422, "bad_view_setting", str(e), "Проверьте значение настройки вида.") from e
    except Conflict as e:
        raise ApiError(
            409,
            "version_conflict",
            "Настройку вида только что изменили в другом окне.",
            "Значение обновлено — повторите изменение.",
            extra={"value": e.value, "version": e.version},
        ) from e


# ---- saved views -------------------------------------------------------------------------------------


def _view_errors(fn: Any) -> Any:
    try:
        return fn()
    except views.ViewError as e:
        raise ApiError(422, "bad_view", str(e), "Проверьте вид и повторите.") from e
    except views.NotFound as e:
        raise ApiError(404, "view_not_found", "Вид не найден: его удалили.", "Обновите раздел «Виды».") from e
    except views.Conflict as e:
        raise ApiError(
            409,
            "version_conflict",
            "Вид только что изменили в другом окне.",
            "Список видов обновлён — повторите действие.",
            extra={"version": e.version},
        ) from e


@router.get("/views")
def list_views(svc: Svc) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"views": views.list_views(conn), "limit": views.MAX_VIEWS}


@router.get("/views/{view_id}")
def get_saved_view(svc: Svc, view_id: int) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return _view_errors(lambda: views.get(conn, view_id))  # type: ignore[no-any-return]


class ViewBody(BaseModel):
    camera: Any
    name: str | None = Field(default=None, max_length=200)
    folder: str | None = Field(default=None, max_length=200)


@router.post("/views")
def save_view(svc: Svc, body: ViewBody) -> dict[str, Any]:
    from bf_core.drillholes.edit import author

    project = svc.require_current(writable=True)

    def run() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            return views.create(conn, body.camera, body.name, body.folder, author())

    return _view_errors(run)  # type: ignore[no-any-return]


class ViewUpdate(BaseModel):
    expected_version: int = Field(ge=0)
    name: str | None = Field(default=None, max_length=200)
    folder: str | None = Field(default=None, max_length=200)
    #: «Перезаписать текущим»: the camera now; the keys are taken from the project
    camera: Any = None


@router.put("/views/{view_id}")
def update_view(svc: Svc, view_id: int, body: ViewUpdate) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    folder = body.folder if "folder" in body.model_fields_set else ...

    def run() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            return views.update(conn, view_id, body.expected_version, name=body.name, folder=folder, camera=body.camera)

    return _view_errors(run)  # type: ignore[no-any-return]


@router.delete("/views/{view_id}")
def delete_view(svc: Svc, view_id: int) -> dict[str, Any]:
    project = svc.require_current(writable=True)

    def run() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            views.delete(conn, view_id)
        return {"deleted": view_id}

    return _view_errors(run)  # type: ignore[no-any-return]


@router.post("/views/{view_id}/apply")
def apply_view(svc: Svc, view_id: int) -> dict[str, Any]:
    project = svc.require_current(writable=True)

    def run() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            return views.apply(conn, view_id)

    return _view_errors(run)  # type: ignore[no-any-return]
