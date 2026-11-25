"""/api/v1/blocks — the properties of the fields of a block model (подшаг 17.29; ТЗ 8.7.2, 8.7.5; ADR 0023 §2, §7, §9;
e17-api.md §4, §7). The router has no prefix of its own: `blocks/router.py` includes it under `/blocks`. The logic is
`fields_ops.py` (the forms of the field, «устарел», «Где используется», the edit, the deletion).

  * `GET /blocks/{id}/fields` → {id, name, version, stale, stale_reason, fields: [{name, kind, dtype, unit, description,
    role, labels_count, stats, file, source, stale, stale_reason, rebuildable, required, deletable, usage}]} — works in
    a project «только просмотр»;
  * `PATCH /blocks/{id}/fields/{name}` {expected_version, unit?, description?, role?} → {version, field} — only the
    keys present change (`null` clears); 404 `block_field_not_found`; 409 `version_conflict {version}`; 422 `bad_param
    {param}`, `role_invalid`, `frac_exists`, `field_rename_unsupported` (a `name` that is not the field's own);
  * `DELETE /blocks/{id}/fields/{name}?expected_version=N` → {version, deleted}; 422 `field_required` (the field of the
    volume), `field_in_use {usage}` («Где используется»); 404; 409;
  * `POST /blocks/{id}/fields/{name}/rebuild` {expected_version} → {source, id} of the job that made the field
    (`blocks.calc`, `blocks.density`, `blocks.classify`) again on `source.params`, replacing the field; 404; 409
    `version_conflict {version}`, `source_lost` (a field, a filter or a wireframe it read is gone); 422
    `not_rebuildable`.
Writes publish `objects.changed {kind: "block_model", id}`; in a project «только просмотр» — 423. The job not
registered in this build — 422 `not_ready`. Plain `def` routes run in the thread pool.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Payload = Annotated[Any, Body()]
EDITABLE = ("unit", "description", "role")
KIND = "block_model"


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна свойств.")
    return raw


def _version(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ApiError(422, "bad_param", "Нужна версия модели (целое число от 1).",
                       "Обновите свойства модели и повторите.", {"param": "expected_version"})  # fmt: skip
    return raw


def _changed(request: Request, bm_id: int) -> None:
    request.app.state.core.events.publish("objects.changed", {"kind": KIND, "id": bm_id})


def _open(conn: Any, folder: Any, bm_id: int) -> tuple[Any, Any]:
    from bf_core.storage.blobs import BlobStore

    from .model import Model

    store = BlobStore(folder)
    return store, Model.open(conn, store, bm_id)


@router.get("/{bm_id}/fields")
def list_fields(svc: Svc, bm_id: int) -> dict[str, Any]:
    from . import fields_ops as fo

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        store, model = _open(conn, project.folder, bm_id)
        why = fo.model_stale(conn, model)
        return {"id": model.id, "name": model.name, "version": model.version, "stale": why is not None,
                "stale_reason": why, "fields": fo.describe(conn, store, model)}  # fmt: skip


@router.patch("/{bm_id}/fields/{name:path}")
def patch_field(svc: Svc, request: Request, bm_id: int, name: str, body: Payload) -> dict[str, Any]:
    from . import fields_ops as fo

    project = svc.require_current(writable=True)
    raw = _body(body)
    version = _version(raw.get("expected_version"))
    edit: dict[str, Any] = {"name": name, **{k: raw[k] for k in EDITABLE if k in raw}}
    if "name" in raw and raw["name"] != name:
        edit["new_name"] = raw["name"]
    if len(edit) == 1:
        raise fo.bad("fields", "Нечего менять.", "Задайте единицу, описание или роль.")
    with dialect.write_tx(project.engine) as conn:
        new_version, changed = fo.edit_fields(conn, bm_id, version, [edit])
    _changed(request, bm_id)
    return {"version": new_version, "field": changed[0]}


@router.delete("/{bm_id}/fields/{name:path}")
def delete_field(
    svc: Svc, request: Request, bm_id: int, name: str, expected_version: Annotated[int | None, Query()] = None
) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from . import fields_ops as fo

    project = svc.require_current(writable=True)
    version = _version(expected_version)
    with dialect.write_tx(project.engine) as conn:
        new_version = fo.remove_field(conn, BlobStore(project.folder), bm_id, version, name)
    _changed(request, bm_id)
    return {"version": new_version, "deleted": name}


@router.post("/{bm_id}/fields/{name:path}/rebuild")
def rebuild_field(svc: Svc, s: Sched, bm_id: int, name: str, body: Payload) -> dict[str, Any]:
    from bf_core.jobs.registry import KINDS

    from . import fields_ops as fo

    project = svc.require_current(writable=True)
    version = _version(_body(body).get("expected_version"))
    with dialect.read_tx(project.engine) as conn:
        store, model = _open(conn, project.folder, bm_id)
        field = model.field(name)
        kind, params, title = fo.rebuild_request(conn, store, model, field, version)
    if kind not in KINDS:
        raise ApiError(422, "not_ready", "Операция ещё не подключена в этой сборке.", "Обновите программу.")
    key, job_id = s.submit(kind, params, title=title)
    return {"source": key, "id": job_id}
