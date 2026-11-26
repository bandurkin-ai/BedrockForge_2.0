"""/api/v1/resources — the reports of resources (ТЗ 8.10; ADR 0027 «API», решения 16, 28, 30; подшаг 20.7;
docs/progress/e20-api.md §2, §3, §5). The paths are relative to `/resources` (`router.py`):

  * `GET /defaults` → {params, ranges} — the defaults of the window (no project is needed).
  * `POST /reports` {params, name?, save} → {source, id} of the job `resources.report` (quick). `save` false (the
    default) — a preview, nothing is written; it works in a project «только просмотр». Refused before the job: 422
    `bad_request`, `bad_param {param}`, `report_too_big`, `group_numeric`, `filter_field_missing`; 404
    `block_model_not_found`, `block_field_not_found`, `filter_not_found`, `template_not_found`, `too_many_reports`
    (`save` true); 409 `no_project`; 423 `project_read_only` (`save` true; and any run in a project really opened
    «только просмотр»: its scheduler has no project, so the preview cannot run there — «Откройте проект для записи»).
    What only the data tells comes from the job (`model_changed`).
  * `GET /reports` → {reports [{id, name, state, version, model {id, name}, totals, graph {stale, stale_reason,
    source_lost, pinned}, files, created_at, …}]}.
  * `GET /reports/{id}?template=&cutoff=&page=&size=` → the answer of §3 of a stored report: the page of the rows
    (`size` ≤ 5000, 500) with `rows_total`, `form` (by `template` or the `template_id` of the parameters), `curve`,
    `control`, `excluded`, `stale`, `stale_reason`, `state`, `graph`, `warnings`. 404 `resource_report_not_found`,
    `template_not_found`.
  * `PUT /reports/{id}` {name, expected_version} → {id, version, name}; 409 `version_conflict {version}`,
    `report_building`, `name_taken`; 423. `DELETE /reports/{id}` → {id, deleted: true}; 409 `report_building`; 423
    (the window asks `lineage.impact` first, `askImpact`).
  * Events `resources.changed {kind: "resource_report", id, deleted?}` after a rename and a deletion (the job
    publishes its own with its result).

The templates, the export and the stubs of Д-36 / Д-37 — `routes_extra.py`. Plain `def` routes run in the thread pool;
numpy, pyarrow and the kernels are imported inside the functions (nothing heavy loads with this module).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from bf_core.db import dialect
from bf_core.geostat.estimate.routes_common import Payload, Sched, Svc, body_of, registered

from .spec import KIND

router = APIRouter()
EVENT = "resources.changed"
TITLE = "Отчёт по ресурсам"


def publish(request: Request, report_id: int, deleted: bool = False) -> None:
    """`resources.changed {kind: "resource_report", id, deleted?}`."""
    data: dict[str, Any] = {"kind": "resource_report", "id": report_id}
    if deleted:
        data["deleted"] = True
    request.app.state.core.events.publish(EVENT, data)


@router.get("/defaults")
def defaults() -> dict[str, Any]:
    from . import spec

    return {"params": spec.defaults_json(), "ranges": spec.ranges_json()}


# --- the run ----------------------------------------------------------------------------------------------------------


def check_request(conn: Any, store: Any, req: Any) -> str:
    """What the job would refuse before it reads the data: the model, the fields, the size, the filter and the fields of
    the rules, the template. → the name of the model."""
    from bf_core.blocks import calc_job, restrict
    from bf_core.blocks.filters import schema as fs
    from bf_core.blocks.model import Model

    from . import result as rr
    from . import store as st
    from . import templates

    if req.save:
        st.check_room(conn)  # 422 `too_many_reports` before the pass, not after it
    model = Model.open(conn, store, req.params.model_id)  # 404 `block_model_not_found`
    spec = req.params.resolved(model.fields, rr.z_range_of(model))  # 404 / 422 of the fields, `report_too_big`
    rr.core_from_spec(model, spec)  # 422 `bad_param`, `report_too_big`
    tree = calc_job.rules_tree(conn, spec.filter_id, spec.restrict)  # 404 `filter_not_found`
    if tree is not None:
        have = {str(f["name"]).casefold() for f in model.fields}
        for name in fs.fields_of(fs.normalize_rules(tree)):
            if name.casefold() not in have:
                raise restrict.field_missing(name, model.name)
    if spec.template_id is not None:
        templates.get(conn, spec.template_id)  # 404 `template_not_found`
    return model.name


def submit(s: Any, kind: str, params: dict[str, Any], title: str) -> tuple[str, int]:
    """`Scheduler.submit`; a project opened «только просмотр» is not attached to the scheduler (the job rows are in the
    project): 423 `project_read_only` in Russian, not a 500."""
    from .spec import refuse

    try:
        return s.submit(kind, params, title=title)  # type: ignore[no-any-return]
    except LookupError:
        raise refuse(
            "project_read_only",
            "Проект открыт только для чтения: задание в нём не запускается.",
            "Откройте проект для записи: закройте его в другом окне BedrockForge и откройте заново.",
        ) from None


@router.post("/reports")
def post_report(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from . import spec

    raw = body_of(body)
    req = spec.parse_request(raw)
    project = svc.require_current(writable=req.save)  # a preview writes nothing: it works «только просмотр»
    with dialect.read_tx(project.engine) as conn:
        name = check_request(conn, BlobStore(project.folder), req)
    registered(KIND, TITLE)
    params = {"params": raw["params"], "name": req.name, "save": req.save}
    key, job_id = submit(s, KIND, params, f"{TITLE} · {name}")
    return {"source": key, "id": job_id}


# --- the rows ---------------------------------------------------------------------------------------------------------


@router.get("/reports")
def list_reports(svc: Svc) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"reports": st.list_reports(conn)}


@router.get("/reports/{report_id}")
def get_report(svc: Svc, request: Request, report_id: int) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from . import result as rr
    from . import spec, templates, view
    from . import store as st

    q = spec.parse_query(request.query_params)
    project = svc.require_current()
    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        info = st.info(conn, report_id, store=store)  # 404 `resource_report_not_found`
        page = st.read_rows(conn, store, report_id, q.page, q.size)
        curve = st.read_curve(conn, store, report_id)
        control = st.read_control(conn, store, report_id) or {}
        sp, roles = view.stored_spec(conn, store, info["params"] or {})
        template_id = q.template or sp.template_id
        form = None
        if template_id is not None:
            template = templates.get(conn, template_id)  # 404 `template_not_found`
            table = st.read_table(conn, store, report_id)
            form = view.form_table(table, rr.elements_of(sp), template, roles, q.cutoff)
    excluded = control.pop("excluded", None)
    graph = info["graph"]
    rows = view.api_rows(page["rows"], len(sp.keys))
    out = view.report_json(sp, report_id=report_id, name=info["name"], version=info["version"], saved=True,
                           model=info["model"], rows=rows, rows_total=page["rows_total"], truncated=False,
                           totals=info["totals"], curve=curve, control=control or None, excluded=excluded, form=form,
                           warnings=info["warnings"] or [], files=info["files"])  # fmt: skip
    out.update({"params_hash": info["params_hash"], "page": page["page"], "size": page["size"], "state": info["state"],
                "stale": graph["stale"], "stale_reason": graph["stale_reason"], "graph": graph,
                "error": info["error"], "inputs": info["inputs"], "fields": info["fields"],
                "created_at": info["created_at"], "updated_at": info["updated_at"],
                "job_id": info["job_id"]})  # fmt: skip
    return out


@router.put("/reports/{report_id}")
def rename_report(svc: Svc, request: Request, report_id: int, body: Payload) -> dict[str, Any]:
    from . import spec
    from . import store as st

    name, version = spec.parse_rename(body)
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = st.rename(conn, report_id, name, version)  # 404; 409 `report_building`, `version_conflict`, `name_taken`
    publish(request, report_id)
    return out


@router.delete("/reports/{report_id}")
def delete_report(svc: Svc, request: Request, report_id: int) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        st.delete(conn, report_id)  # 404, 409 `report_building`
    publish(request, report_id, deleted=True)
    return {"id": report_id, "deleted": True}
