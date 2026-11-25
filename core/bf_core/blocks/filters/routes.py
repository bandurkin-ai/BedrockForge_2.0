"""/api/v1/filters — the library of filters (ТЗ 7.35 Д-02; ADR 0023 §8; e17-api.md §13).

  * `GET /filters` → {filters: [{id, name, folder, version, text, title, fields, note, created_at, updated_at}]};
  * `POST /filters` {name, rules | expr, note?, folder?} → the whole filter; 409 `name_taken`, 422 `name_empty`,
    `filter_rules {param}`, `expr_syntax {param, pos, …}`, `wireframe_not_found {param}`;
  * `GET /filters/{id}` → {…, rules, wireframes: [{id, name, available}]}; 404 `filter_not_found`;
  * `PUT /filters/{id}` {expected_version, name?, rules | expr?, note?} → the whole filter; none or another
    version — 409 `version_conflict {version}`;
  * `DELETE /filters/{id}` → {deleted: id}; a user — 422 `filter_in_use {usage}`;
  * `GET /filters/{id}/usage` → {usage: [{kind: "object" | "view", id, name, role}]};
  * `POST /filters/{id}/test` {model_id} and `POST /filters/test` {model_id, rules | expr} → {passed, total,
    weight}; a field the model lacks — 422 `filter_field_missing {field, model}`; 422 `not_ready` until
    `blocks/restrict.py` (17.8) is in.
Writes need a writable project (423 `project_read_only`) and publish `objects.changed {kind: "filter", id,
deleted?}`. Plain `def` routes run in the thread pool.
"""

from __future__ import annotations

import importlib
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from . import schema as fs
from . import store as st

router = APIRouter(prefix="/filters")
Svc = Annotated[ProjectService, Depends(projects)]
Payload = Annotated[Any, Body()]
EVENT = "objects.changed"


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна фильтров.")
    return raw


def _publish(request: Request, filter_id: int, deleted: bool = False) -> None:
    data: dict[str, Any] = {"kind": st.KIND, "id": filter_id}
    if deleted:
        data["deleted"] = True
    request.app.state.core.events.publish(EVENT, data)


def _author() -> str | None:
    from bf_core.drillholes.edit import author

    return author()


def _model_id(raw: dict[str, Any]) -> int:
    mid = raw.get("model_id")
    if isinstance(mid, bool) or not isinstance(mid, int) or mid < 1:
        raise ApiError(422, "bad_param", "Номер блочной модели задан неверно.", "Выберите модель из списка.",
                       {"param": "model_id"})  # fmt: skip
    return mid


def _test(svc: ProjectService, model_id: int, rules: dict[str, Any]) -> dict[str, Any]:
    """«Проверить на модели»: `{passed, total, weight}` of `blocks.restrict.test_rules(project, model_id, rules)`."""
    from bf_core.blocks import store as blocks

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        blocks.block_model_info(conn, model_id)  # 404 block_model_not_found
    try:
        restrict = importlib.import_module("bf_core.blocks.restrict")
    except ModuleNotFoundError:
        restrict = None
    run = getattr(restrict, "test_rules", None)
    if run is None:
        raise ApiError(422, "not_ready", "Проверка фильтра на модели ещё не готова.",
                       "Повторите после обновления программы.", {"param": "model_id"})  # fmt: skip
    out: dict[str, Any] = run(project, model_id, rules)
    return out


@router.get("")
def list_filters(svc: Svc) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"filters": st.list_filters(conn)}


@router.post("")
def create_filter(svc: Svc, request: Request, body: Payload) -> dict[str, Any]:
    raw = _body(body)
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        fid = st.create(conn, raw, author=_author())
        out = st.get(conn, fid)
    _publish(request, fid)
    return out


@router.post("/test")
def test_rules(svc: Svc, body: Payload) -> dict[str, Any]:
    raw = _body(body)
    rules = fs.rules_of_body(raw, required=True)
    assert rules is not None
    return _test(svc, _model_id(raw), rules)


@router.get("/{filter_id}")
def get_filter(svc: Svc, filter_id: int) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return st.get(conn, filter_id)


@router.put("/{filter_id}")
def put_filter(svc: Svc, request: Request, filter_id: int, body: Payload) -> dict[str, Any]:
    raw = _body(body)
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        st.update(conn, filter_id, raw)
        out = st.get(conn, filter_id)
    _publish(request, filter_id)
    return out


@router.delete("/{filter_id}")
def delete_filter(svc: Svc, request: Request, filter_id: int) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        st.delete(conn, filter_id)
    _publish(request, filter_id, deleted=True)
    return {"deleted": filter_id}


@router.get("/{filter_id}/usage")
def filter_usage(svc: Svc, filter_id: int) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"usage": st.usage(conn, filter_id)}


@router.post("/{filter_id}/test")
def test_stored(svc: Svc, filter_id: int, body: Payload) -> dict[str, Any]:
    raw = _body(body)
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        rules = st.rules_of(conn, filter_id)
    return _test(svc, _model_id(raw), rules)
