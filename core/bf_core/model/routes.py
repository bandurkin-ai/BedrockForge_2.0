"""/api/v1/implicit — the stored implicit models, the defaults of the wizard, the run (ТЗ 8.9; ADR 0026 «API»;
подшаг 19.16; the forms — docs/progress/e19-api.md §3–§7). The paths are relative to `/implicit` (`router.py`):

  * `GET /defaults` → {params, ranges} — the defaults of 8.9.4 and the ranges of the form (no project is needed).
  * `GET /models`, `GET /models/{id}`, `POST /models` (the body of §3), `PUT /models/{id}` {expected_version, …keys
    of §3} — the rows; the groups — `routes_groups.py`, the preview of the points — `routes_points.py`, the outputs
    and `DELETE` — `routes_outputs.py`.
  * `POST /models/{id}/build` and `…/update` {expected_version, threads?} → {source, id} of the job `implicit.build` /
    `implicit.update`. Refused before it starts: 404 `model_not_found`, 409 `model_building`, `version_conflict
    {version}`, 404 / 422 of the references and the parameters (`store.prepare`), 422 `not_ready` (Э19б groups, the
    relations other than «Согласно»), 423 `project_read_only`. The model is marked `building` (`store.begin`) in its
    own transaction before the job is queued, and put back when the queue refuses it; what only the data can tell
    (`no_contacts`, `rbf_singular`, `too_many_points`, `iso_check`) comes from the job.

The checks go in this order: the shape of the body (422 `bad_request`), the project (409 `no_project`, 423
`project_read_only`), the parameters (422 / 404), the versions and the state (409). Writes publish `implicit.changed
{kind: "implicit_model", id, deleted?}`. Plain `def` routes run in the thread pool; numpy and the kernels load on the
first request, not with this module.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from bf_core.db import dialect
from bf_core.storage.blobs import BlobStore

from .routes_base import (
    KIND_BUILD,
    KIND_UPDATE,
    TITLE_BUILD,
    TITLE_UPDATE,
    Payload,
    Sched,
    Svc,
    author,
    body_of,
    publish,
    registered,
)

router = APIRouter()


@router.get("/defaults")
def defaults() -> dict[str, Any]:
    from . import spec

    return {"params": spec.defaults_json(), "ranges": spec.ranges_json()}


# --- the rows ---------------------------------------------------------------------------------------------------------


@router.get("/models")
def list_models(svc: Svc) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"models": st.list_rows(conn)}


@router.get("/models/{model_id}")
def get_model(svc: Svc, model_id: int) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return st.get(conn, model_id)


@router.post("/models")
def create_model(svc: Svc, request: Request, body: Payload) -> dict[str, Any]:
    from . import store as st
    from .spec import parse_model

    raw = body_of(body)
    project = svc.require_current(writable=True)
    spec = parse_model(raw)
    with dialect.write_tx(project.engine) as conn:
        out = st.create(conn, BlobStore(project.folder), project, spec, author=author())
    publish(request, out["id"])
    return out


@router.put("/models/{model_id}")
def put_model(svc: Svc, request: Request, model_id: int, body: Payload) -> dict[str, Any]:
    from . import store as st

    raw = body_of(body)
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = st.update(conn, BlobStore(project.folder), project, model_id, raw, raw.get("expected_version"))
    publish(request, model_id)
    return out


# --- the run ----------------------------------------------------------------------------------------------------------


def _previous(meta: dict[str, Any]) -> tuple[str, Any]:
    """What the model goes back to when the job is cancelled or refused by the queue."""
    state = str(meta.get("state") or "ready")
    return ("ready", None) if state == "building" else (state, meta.get("error"))


def _start(svc: Svc, s: Sched, request: Request, model_id: int, body: Any, update: bool) -> dict[str, Any]:
    from . import job_stages as js
    from . import store as st
    from . import store_base as sb
    from .spec import parse_build

    raw = body_of(body)
    project = svc.require_current(writable=True)
    req = parse_build(raw)
    kind, title = (KIND_UPDATE, TITLE_UPDATE) if update else (KIND_BUILD, TITLE_BUILD)
    registered(kind, "Построение неявной модели")
    with dialect.read_tx(project.engine) as conn:  # what the job would refuse before it reads the data
        row = sb.stored(conn, model_id)
        if row.state == "building":
            raise sb.building()
        if int(row.version) != req.expected_version:
            raise sb.conflict(int(row.version))
        prep = st.prepare(conn, sb.load_spec(dict(row.meta or {})))
        js.refuse_unready(prep.spec)
        name = str(row.name)
    with dialect.write_tx(project.engine) as conn:
        before = st.begin(conn, model_id, req.expected_version)  # 409 again if another window was quicker
    meta = dict(before.meta or {})
    state, error = _previous(meta)
    stats = meta.get("stats") or {}
    params: dict[str, Any] = {"model_id": model_id, "expected_version": req.expected_version,
                              "previous_state": state, "previous_error": error, "n_patch": prep.spec.pou.n_patch}
    if req.threads is not None:
        params["threads"] = req.threads
    if isinstance(stats.get("points"), int):
        params["points"] = stats["points"]  # for `memory_mb`: the size of the last run
    try:
        key, job_id = s.submit(kind, params, title=f"{'Обновление' if update else 'Построение'}: {name}")
    except BaseException:
        with dialect.write_tx(project.engine) as conn:  # the queue did not take it: the model is as it was
            st.set_state(conn, model_id, state, error=error)
        raise
    publish(request, model_id)
    return {"source": key, "id": job_id}


@router.post("/models/{model_id}/build")
def build(svc: Svc, s: Sched, request: Request, model_id: int, body: Payload) -> dict[str, Any]:
    return _start(svc, s, request, model_id, body, False)


@router.post("/models/{model_id}/update")
def update(svc: Svc, s: Sched, request: Request, model_id: int, body: Payload) -> dict[str, Any]:
    return _start(svc, s, request, model_id, body, True)
