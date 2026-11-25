"""/api/v1/geostat/trends — the trends (ТЗ 7.35 Д-15; ADR 0022 §10, §14; e16-api.md «Тренды»).

  * `GET /trends` → {trends: [{id, name, kind, ang1, ang2, ang3, ratios, size, centre, color, origin, used, matrix,
    version, created_at, updated_at}]}; `GET /trends/{id}` → the same of one; 404 `trend_not_found`. A structural trend
    (`kind: "structural"`, Э19) has no angles and `matrix` is null: {source, source_info {set_name, table_name,
    wireframe_name, lost}, source_lost, radius, ratios, size, color, …}; of one — + `n_measurements` (null — a
    wireframe or a source that is gone).
  * `POST /trends` {name?, kind?: "global", ang1, ang2?, ang3?, ratios?, size?, centre?, color?, origin?,
    model_id?, structure?} → {id, name}. With `model_id` — a copy of the structure (from 1; by default — the largest
    a1), `origin {type: "model", model_id, structure}`, the name «Тренд · ‹модель›»; 404 `model_not_found`.
    `kind: "structural"` {source: {type: "measurements", set_id, table, alpha, dipdir, depth?} | {type: "mesh",
    wireframe_id, r_n?}, radius? (R_тр, 10–5000 m, 200), ratios?, size?, color?, name?} — the name «Структурный
    тренд»; 404 `set_not_found`, `table_not_found`, `wireframe_not_found`; 422 `bad_param {param: "source.alpha" …}`.
  * `PUT /trends/{id}` {expected_version, …} — the fields given (with `model_id` — «Обновить из модели»)
    → {id, name, version}; `expected_version` (required, the `version` read) not the stored one — 409
    `version_conflict` {version}; 404; 409 `name_taken`; 422 `bad_param {param: "expected_version"}`.
  * `DELETE /trends/{id}` → {deleted: id}; referred to — 409 `in_use {usage}`.
  * `GET /trends/{id}/usage` → {usage: [{id, kind, name, role}]}.
  * `GET /trends/{id}/discs` → {radius, n, total, thinned, discs: {x, y, z, nx, ny, nz}}: the discs of a structural
    trend — at the measurements (the upper unit normals; at most 50 000) or at the centres of the faces of the
    wireframe (at most 10 000); a global one — 422 `trend_not_structural`; a source that is gone — 409 `source_gone`.

Writes need a writable project (423 `project_read_only`) and publish `geostat.changed {kind: "trend", id, deleted?}`.
Plain `def` routes run in the thread pool.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from . import trend_store as ts
from .variogram import store as ms

router = APIRouter(prefix="/trends")
Svc = Annotated[ProjectService, Depends(projects)]
Payload = Annotated[Any, Body()]
EVENT = "geostat.changed"


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна тренда.")
    return raw


def _publish(request: Request, trend_id: int, deleted: bool = False) -> None:
    data: dict[str, Any] = {"kind": ts.KIND, "id": trend_id}
    if deleted:
        data["deleted"] = True
    request.app.state.core.events.publish(EVENT, data)


def _author() -> str | None:
    from bf_core.drillholes.edit import author

    return author()


@router.get("")
def list_trends(svc: Svc) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"trends": ts.list_trends(conn)}


@router.post("")
def create_trend(svc: Svc, request: Request, body: Payload) -> dict[str, Any]:
    raw = _body(body)
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = ts.create(conn, raw, author=_author())
    _publish(request, out["id"])
    return out


def _struct(e: Exception) -> ApiError:
    """A refusal of the structural trend (`StatsError`) as an API error: a source that is gone — 409."""
    out = ms.as_api(e)
    if out.code == "source_gone":
        out.status = 409
    return out


@router.get("/{trend_id}")
def get_trend(svc: Svc, trend_id: int) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        out = ts.form(conn, trend_id)
        meta = ts.meta_of(conn, trend_id)[1]
    if out["kind"] == "structural":
        from . import trend_struct as st

        out["n_measurements"] = None if out["source_lost"] else st.count_measurements(project, meta)
    return out


@router.get("/{trend_id}/discs")
def trend_discs(svc: Svc, trend_id: int) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        _name, meta = ts.meta_of(conn, trend_id)
    if meta.get("kind") != "structural":
        raise ApiError(422, "trend_not_structural", "У глобального тренда нет дисков: он показывается эллипсоидом.",
                       "Выберите структурный тренд.", {"param": "trend_id"})  # fmt: skip
    from . import trend_struct as st

    try:
        return st.discs(project, meta)
    except ValueError as e:
        raise _struct(e) from e


@router.put("/{trend_id}")
def put_trend(svc: Svc, request: Request, trend_id: int, body: Payload) -> dict[str, Any]:
    raw = _body(body)
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = ts.update(conn, trend_id, raw)
    _publish(request, trend_id)
    return out


@router.delete("/{trend_id}")
def delete_trend(svc: Svc, request: Request, trend_id: int) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        ts.delete(conn, trend_id)
    _publish(request, trend_id, deleted=True)
    return {"deleted": trend_id}


@router.get("/{trend_id}/usage")
def trend_usage(svc: Svc, trend_id: int) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"usage": ts.usage(conn, trend_id)}
