"""/api/v1/view — the project view settings (ТЗ 7.0.10, ADR 0007 §2): read all, write a key with a version."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from .keys import BadValue
from .service import Conflict, read_all, write

router = APIRouter(prefix="/view")
Svc = Annotated[ProjectService, Depends(projects)]


@router.get("")
def get_view(svc: Svc) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"values": read_all(conn)}


class PutBody(BaseModel):
    value: Any = None
    expected_version: int = Field(ge=0)
    #: uid of the project the value belongs to: a write that comes late, after another project opened, is refused
    project: str | None = Field(default=None, max_length=64)


@router.put("/{key}")
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
