"""/api/v1/charts — saved charts (ADR 0019 §2, §10): displays along holes (striplog, ТЗ 7.16, ADR 0007 §3), the
charts of a set of Э13 and swath plots of the project.

`GET /charts?set_id=&kind=`; `POST {set_id | null, kind, name, settings, auto_name}`; `GET` / `PUT` / `DELETE
/charts/{id}` with `PUT {name? | auto_name?, settings?, expected_version}`. Errors: 422 `bad_chart`,
`too_many_series`, `too_many_charts` (with `param`), 404 `chart_not_found`, 409 `name_taken`, `version_conflict`
(with the current chart), 423 — a project open read-only. Changes publish `objects.changed {kind: "chart", id,
set_id, chart: <kind>[, deleted]}`."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from . import kinds, service

router = APIRouter(prefix="/charts")
Svc = Annotated[ProjectService, Depends(projects)]


def _errors(fn: Any) -> Any:
    try:
        return fn()
    except service.ChartError as e:
        hint = "Удалите ненужные swath plot." if e.code == "too_many_charts" else "Проверьте настройки графика."
        extra = {"param": e.param} if e.param else None
        raise ApiError(422, e.code, str(e), hint, extra=extra) from e
    except service.NotFound as e:
        raise ApiError(404, "chart_not_found", "График или набор не найдены.", "Обновите дерево проекта.") from e
    except service.NameTaken as e:
        what = "Отображение" if e.kind == kinds.STRIPLOG else "График"
        where = "в проекте" if e.kind in kinds.PROJECT_KINDS else "в наборе"
        raise ApiError(409, "name_taken", f"{what} «{e.name}» {where} уже есть.", "Выберите другое название.") from e
    except service.Conflict as e:
        striplog = e.current["kind"] == kinds.STRIPLOG
        raise ApiError(
            409,
            "version_conflict",
            "Отображение изменили, пока вы его правили." if striplog else "График изменили, пока вы его правили.",
            "Отображение обновлено — повторите правку." if striplog else "График обновлён — повторите правку.",
            extra={"chart": e.current},
        ) from e


def _publish(request: Request, chart: dict[str, Any], deleted: bool = False) -> None:
    body = {"kind": "chart", "id": chart["id"], "set_id": chart["set_id"], "chart": chart["kind"]}
    request.app.state.core.events.publish("objects.changed", {**body, "deleted": True} if deleted else body)


@router.get("")
def list_all(svc: Svc, set_id: int | None = None, kind: str | None = None) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"charts": service.list_charts(conn, set_id, kind)}


class ChartIn(BaseModel):
    set_id: int | None = None
    name: str = Field(max_length=200)
    kind: str = Field(default=kinds.STRIPLOG, max_length=32)
    settings: dict[str, Any]
    auto_name: bool = False


@router.post("")
def create(svc: Svc, body: ChartIn, request: Request) -> dict[str, Any]:
    from bf_core.drillholes.edit import author

    project = svc.require_current(writable=True)

    def go() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            return service.create(
                conn, body.set_id, body.name, body.kind, body.settings, author(), auto_name=body.auto_name
            )

    out: dict[str, Any] = _errors(go)
    _publish(request, out)
    return out


@router.get("/{chart_id}")
def get_one(svc: Svc, chart_id: int) -> dict[str, Any]:
    project = svc.require_current()

    def go() -> dict[str, Any]:
        with dialect.read_tx(project.engine) as conn:
            return service.get(conn, chart_id)

    return _errors(go)  # type: ignore[no-any-return]


class ChartPut(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    #: the name built from the new settings: applied while the chart keeps its auto name (ADR 0019 §2)
    auto_name: str | None = Field(default=None, max_length=200)
    settings: dict[str, Any] | None = None
    expected_version: int = Field(ge=1)


@router.put("/{chart_id}")
def update(svc: Svc, chart_id: int, body: ChartPut, request: Request) -> dict[str, Any]:
    project = svc.require_current(writable=True)

    def go() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            return service.update(
                conn, chart_id, body.name, body.settings, body.expected_version, auto_name=body.auto_name
            )

    out: dict[str, Any] = _errors(go)
    _publish(request, out)
    return out


@router.delete("/{chart_id}")
def delete(svc: Svc, chart_id: int, request: Request) -> dict[str, Any]:
    project = svc.require_current(writable=True)

    def go() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            chart = service.get(conn, chart_id)
            service.delete(conn, chart_id)
            return chart

    chart: dict[str, Any] = _errors(go)
    _publish(request, chart, deleted=True)
    return {"ok": True}
