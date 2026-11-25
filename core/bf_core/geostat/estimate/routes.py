"""/api/v1/geostat — the estimation of grades: the rows «Параметры оценки», the plans, the defaults of the wizard, the
run and the preview of a block (ТЗ 8.8; ADR 0024 §11; подшаг 18.13; the forms — docs/progress/e18-api.md §3–§7).

  * `GET /estimations[?model_id]`, `GET /estimations/{id}`, `POST /estimations` (the body of §3),
    `PUT /estimations/{id}` {expected_version, …}, `DELETE /estimations/{id}` — the rows (409 `name_taken`,
    `version_conflict {version}`).
  * `GET /estimation-plans[?model_id]`, `GET /estimation-plans/{id}`, `POST /estimation-plans` {name?, model_id, items},
    `PUT /estimation-plans/{id}` {expected_version, name?, items?}, `DELETE /estimation-plans/{id}`.
  * `GET /estimate/defaults?model_id&set_id&table&field[&block_domain&domain_table&domain_field&domain_values&weights
    &variogram_id&method&channel&seq]` — synchronously in a HEAVY slot.
  * `POST /estimate/run` {plan_id | estimation_ids, expected_version, replace?} → {source, id} of the job
    `estimate.run`; refused before it starts: 404, 409 `version_conflict`, `source_lost`, `field_exists {field}`, 422,
    423.
  * `POST /estimate/preview` {estimation | estimation_id, at, channel?, seq?} — one block, synchronously (≤ 100 мс).

The checks go in this order: the shape of the body (422 `bad_request`), the project (409 `no_project`, 423
`project_read_only` for what writes or starts a job), the parameters (422 / 404), the versions and names (409). Writes
publish `geostat.changed {kind: "estimation" | "estimation_plan", id, deleted?}`. Plain `def` routes run in the thread
pool; numpy and the kernels load on the first request, not with this module.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from bf_core.db import dialect
from bf_core.storage.blobs import BlobStore

from .routes_common import Payload, Sched, Svc, author, body_of, common_of, publish, registered, sync

router = APIRouter()
KIND_RUN = "estimate.run"
TITLE_RUN = "Оценка содержаний"


# --- the rows ---------------------------------------------------------------------------------------------------------


@router.get("/estimations")
def list_rows(svc: Svc, model_id: Annotated[int | None, Query()] = None) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"estimations": st.list_rows(conn, model_id)}


@router.get("/estimations/{estimation_id}")
def get_row(svc: Svc, estimation_id: int) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return st.get(conn, estimation_id)


@router.post("/estimations")
def create_row(svc: Svc, request: Request, body: Payload) -> dict[str, Any]:
    from . import store as st
    from . import store_check as sc
    from .spec import parse_estimation

    raw = body_of(body)
    project = svc.require_current(writable=True)
    spec = parse_estimation(raw)
    with dialect.read_tx(project.engine) as conn:
        prepared = sc.prepare(conn, project, spec)  # the samples are read outside the write transaction
    with dialect.write_tx(project.engine) as conn:
        out = st.create(conn, BlobStore(project.folder), project, spec, author=author(), prepared=prepared)
    publish(request, st.KIND, out["id"])
    return out


@router.put("/estimations/{estimation_id}")
def put_row(svc: Svc, request: Request, estimation_id: int, body: Payload) -> dict[str, Any]:
    from bf_core.geostat.variogram import store as ms

    from . import store as st
    from . import store_base as sb
    from . import store_check as sc
    from .spec import parse_estimation

    raw = body_of(body)
    project = svc.require_current(writable=True)
    prepared = None
    with dialect.read_tx(project.engine) as conn:
        row = sb.stored(conn, st.KIND, estimation_id)  # 404 first
        version = ms.clean_version(raw.get("expected_version"))
        if int(row.version) != version:
            raise sb.conflict(int(row.version))
        meta = dict(row.meta or {})
        merged = {k: meta[k] for k in st.KEYS if k in meta}
        merged.update({k: raw[k] for k in st.KEYS if k in raw})
        if any(k in raw for k in st.KEYS):  # the samples are read here, outside the write transaction
            prepared = sc.prepare(conn, project, parse_estimation(merged), old=sb.load_spec(meta),
                                  old_origin=meta.get("origin"))  # fmt: skip
    with dialect.write_tx(project.engine) as conn:
        out = st.update(conn, BlobStore(project.folder), project, estimation_id, raw, version, prepared=prepared)
    publish(request, st.KIND, estimation_id)
    return out


@router.delete("/estimations/{estimation_id}")
def delete_row(svc: Svc, request: Request, estimation_id: int) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = st.delete(conn, estimation_id)
    publish(request, st.KIND, estimation_id, deleted=True)
    for plan in out["plans"]:
        publish(request, st.PLAN, plan)
    return out


# --- the plans --------------------------------------------------------------------------------------------------------


@router.get("/estimation-plans")
def list_plans(svc: Svc, model_id: Annotated[int | None, Query()] = None) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"plans": st.plan_list(conn, model_id)}


@router.get("/estimation-plans/{plan_id}")
def get_plan(svc: Svc, plan_id: int) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return st.plan_get(conn, plan_id)


@router.post("/estimation-plans")
def create_plan(svc: Svc, request: Request, body: Payload) -> dict[str, Any]:
    from . import store as st
    from .spec import parse_plan

    raw = body_of(body)
    project = svc.require_current(writable=True)
    spec = parse_plan(raw)
    with dialect.write_tx(project.engine) as conn:
        out = st.plan_create(conn, spec, author=author())
    publish(request, st.PLAN, out["id"])
    return out


@router.put("/estimation-plans/{plan_id}")
def put_plan(svc: Svc, request: Request, plan_id: int, body: Payload) -> dict[str, Any]:
    from . import store as st

    raw = body_of(body)
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = st.plan_update(conn, plan_id, raw)
    publish(request, st.PLAN, plan_id)
    return out


@router.delete("/estimation-plans/{plan_id}")
def delete_plan(svc: Svc, request: Request, plan_id: int) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = st.plan_delete(conn, plan_id)
    publish(request, st.PLAN, plan_id, deleted=True)
    return out


# --- the defaults of the wizard ---------------------------------------------------------------------------------------


@router.get("/estimate/defaults")
def defaults(
    svc: Svc,
    model_id: Annotated[int | None, Query()] = None,
    set_id: Annotated[int | None, Query()] = None,
    table: Annotated[str | None, Query()] = None,
    field: Annotated[str | None, Query()] = None,
    block_domain: Annotated[str | None, Query()] = None,
    domain_table: Annotated[str | None, Query()] = None,
    domain_field: Annotated[str | None, Query()] = None,
    domain_values: Annotated[str | None, Query()] = None,
    weights: Annotated[str | None, Query()] = None,
    variogram_id: Annotated[int | None, Query()] = None,
    method: Annotated[str | None, Query()] = None,
    channel: Annotated[str | None, Query()] = None,
    seq: Annotated[int | None, Query()] = None,
) -> dict[str, Any]:
    from . import defaults as df

    project = svc.require_current()
    q = {"model_id": model_id, "set_id": set_id, "table": table, "field": field, "block_domain": block_domain,
         "domain_table": domain_table, "domain_field": domain_field, "weights": weights,
         "domain_values": [] if domain_values is None else [v for v in domain_values.split(";") if v.strip()],
         "variogram_id": variogram_id, "method": method}  # fmt: skip
    common = common_of({"channel": channel, "seq": seq})
    return sync(common, lambda: df.build(project, q))


# --- the run ----------------------------------------------------------------------------------------------------------


@router.post("/estimate/run")
def run(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from . import job_plan as jp
    from .spec import parse_run

    raw = body_of(body)
    project = svc.require_current(writable=True)
    spec = parse_run(raw)
    with dialect.read_tx(project.engine) as conn:
        plan = jp.build(conn, BlobStore(project.folder), spec)  # 404 / 409 / 422 as the job would answer
        names = [r.name for r in plan.rows]
    registered(KIND_RUN, "Оценка")
    params: dict[str, Any] = {"expected_version": spec.expected_version, "replace": spec.replace,
                              "plan_rows": len(names)}  # fmt: skip
    if spec.plan_id is not None:
        params["plan_id"] = spec.plan_id
    else:
        params["estimation_ids"] = list(spec.estimation_ids)
    title = f"Оценка: {names[0]}" if len(names) == 1 else f"Оценка: {len(names)} строк плана"
    key, job_id = s.submit(KIND_RUN, params, title=title)
    return {"source": key, "id": job_id}


# --- the preview of a block -------------------------------------------------------------------------------------------


@router.post("/estimate/preview")
def preview(svc: Svc, body: Payload) -> dict[str, Any]:
    from . import preview as pv

    raw = body_of(body)
    project = svc.require_current()
    common = common_of(raw)
    return sync(common, lambda: pv.preview(project, raw))

