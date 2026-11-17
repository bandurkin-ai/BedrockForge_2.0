"""/api/v1/charts — saved displays along holes (striplog, ТЗ 7.16, ADR 0007 §3)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from . import service

router = APIRouter(prefix="/charts")
Svc = Annotated[ProjectService, Depends(projects)]


def _errors(fn: Any) -> Any:
    try:
        return fn()
    except service.ChartError as e:
        raise ApiError(422, "bad_chart", str(e), "Проверьте настройки отображения.") from e
    except service.NotFound as e:
        raise ApiError(404, "chart_not_found", "Отображение или набор не найдены.", "Обновите дерево проекта.") from e
    except service.NameTaken as e:
        raise ApiError(
            409, "name_taken", f"Отображение «{e.name}» в наборе уже есть.", "Выберите другое название."
        ) from e
    except service.Conflict as e:
        raise ApiError(
            409,
            "version_conflict",
            "Отображение изменили, пока вы его правили.",
            "Отображение обновлено — повторите правку.",
            extra={"chart": e.current},
        ) from e


@router.get("")
def list_all(svc: Svc, set_id: int | None = None, kind: str | None = None) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"charts": service.list_charts(conn, set_id, kind)}


class ChartIn(BaseModel):
    set_id: int
    name: str = Field(max_length=200)
    kind: Literal["striplog"] = "striplog"
    settings: dict[str, Any]


@router.post("")
def create(svc: Svc, body: ChartIn) -> dict[str, Any]:
    from bf_core.drillholes.edit import author

    project = svc.require_current(writable=True)

    def go() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            return service.create(conn, body.set_id, body.name, body.kind, body.settings, author())

    return _errors(go)  # type: ignore[no-any-return]


@router.get("/{chart_id}")
def get_one(svc: Svc, chart_id: int) -> dict[str, Any]:
    project = svc.require_current()

    def go() -> dict[str, Any]:
        with dialect.read_tx(project.engine) as conn:
            return service.get(conn, chart_id)

    return _errors(go)  # type: ignore[no-any-return]


class ChartPut(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    settings: dict[str, Any] | None = None
    expected_version: int = Field(ge=1)


@router.put("/{chart_id}")
def update(svc: Svc, chart_id: int, body: ChartPut) -> dict[str, Any]:
    project = svc.require_current(writable=True)

    def go() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            return service.update(conn, chart_id, body.name, body.settings, body.expected_version)

    return _errors(go)  # type: ignore[no-any-return]


@router.delete("/{chart_id}")
def delete(svc: Svc, chart_id: int) -> dict[str, Any]:
    project = svc.require_current(writable=True)

    def go() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            service.delete(conn, chart_id)
            return {"ok": True}

    return _errors(go)  # type: ignore[no-any-return]
