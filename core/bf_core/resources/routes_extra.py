"""/api/v1/resources — the templates, the export, and the routes of Д-36 / Д-37 (подшаг 20.7; ADR 0027 «API»;
docs/progress/e20-api.md §5, §8–§10). Relative to `/resources` (`router.py`):

  * `GET /templates` → {templates [{id, name, builtin, version}]}; `GET /templates/{id}` → {template {id, name, builtin,
    version, labels, subtotals, total, key, section, columns, balance, units, decimals}} (404 `template_not_found`);
    `PUT /templates/{id}` {template, expected_version?} → {id, version} (`expected_version` of the template; a new one —
    without it; the two of the box — `jorc`, `gkz` — are rewritten); `DELETE /templates/{id}?expected_version=` →
    {id, deleted: true} (the two of the box — 422). 409 `version_conflict {version}`, 422 `bad_param {param}`, 423.
    Event `resources.changed {kind: "template", id, deleted?}`.
  * `POST /reports/{id}/export` {format: xlsx | csv, path, template?, cutoff?, overwrite?} → {source, id} of the job
    `resources.export` (quick; 20.5). Checked before it: 422 `bad_param`, 404 `resource_report_not_found`. It writes
    outside the project, so it works in a project «только просмотр». While the job is not in the build — 501
    `not_implemented`. The job is given {report_id, format, path, template, cutoff, overwrite}.
  * `POST /reports/{id}/drill`, `POST /reports/{id}/drill/rows` (Д-36, 20.9) and `POST /compare` (Д-37, 20.10) — the
    paths and the checks of the contract; the answer is 501 `not_implemented` until those sub-steps land. The body of
    a drill has no `report_id`: it is the one of the path.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.geostat.estimate.routes_common import Payload, Sched, Svc, body_of

router = APIRouter()
EVENT = "resources.changed"
EXPORT_KIND = "resources.export"


def not_implemented(what: str) -> ApiError:
    return ApiError(501, "not_implemented", f"{what} ещё не подключён в этой сборке.", "Обновите программу.")


def _template_event(request: Request, template_id: str, deleted: bool = False) -> None:
    data: dict[str, Any] = {"kind": "template", "id": template_id}
    if deleted:
        data["deleted"] = True
    request.app.state.core.events.publish(EVENT, data)


# --- the templates ----------------------------------------------------------------------------------------------------


@router.get("/templates")
def list_templates(svc: Svc) -> dict[str, Any]:
    from . import templates as tp

    with dialect.read_tx(svc.require_current().engine) as conn:
        found = tp.templates(conn)
    return {"templates": [{"id": t["id"], "name": t["name"], "builtin": t["id"] in tp.BOX, "version": t["version"]}
                          for t in found]}  # fmt: skip


@router.get("/templates/{template_id}")
def get_template(svc: Svc, template_id: str) -> dict[str, Any]:
    from . import templates as tp

    with dialect.read_tx(svc.require_current().engine) as conn:
        t = tp.get(conn, tp.check_id(template_id))
    return {"template": {**t, "builtin": t["id"] in tp.BOX}}


@router.put("/templates/{template_id}")
def put_template(svc: Svc, request: Request, template_id: str, body: Payload) -> dict[str, Any]:
    from . import templates as tp

    raw = body_of(body)
    template = raw.get("template")
    if not isinstance(template, dict):
        raise tp.bad("template", "Не задан шаблон отчёта.", "Передайте шаблон в поле template.")
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = tp.put(conn, template_id, {**template, "expected_version": raw.get("expected_version")})
    _template_event(request, out["id"])
    return {"id": out["id"], "version": out["version"]}


@router.delete("/templates/{template_id}")
def delete_template(svc: Svc, request: Request, template_id: str,
                    expected_version: int | None = Query(default=None, ge=1)) -> dict[str, Any]:  # fmt: skip
    from . import templates as tp

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = tp.delete(conn, template_id, expected_version)
    _template_event(request, out["id"], deleted=True)
    return {"id": out["id"], "deleted": True}


# --- the export (20.5) ------------------------------------------------------------------------------------------------


def _exists(svc: Svc, report_id: int) -> str:
    """The report is there (404 `resource_report_not_found`); → its name."""
    from . import store as st

    with dialect.read_tx(svc.require_current().engine) as conn:
        st.data_sha(conn, report_id)
        return str(st.info(conn, report_id)["name"])


@router.post("/reports/{report_id}/export")
def export_report(svc: Svc, s: Sched, report_id: int, body: Payload) -> dict[str, Any]:
    from . import export_job

    cur = svc.require_current()
    with dialect.read_tx(cur.engine) as conn:
        params = export_job.request(conn, cur.folder, report_id, body_of(body))  # 404 / 409 / 422 before the job
    name = _exists(svc, report_id)
    key, job_id = s.submit(EXPORT_KIND, params, title=f"Выгрузка отчёта «{name}»")
    return {"source": key, "id": job_id}


# --- Д-36, Д-37 (20.9, 20.10) -----------------------------------------------------------------------------------------


@router.post("/reports/{report_id}/drill")
def drill(svc: Svc, report_id: int, body: Payload) -> dict[str, Any]:
    from . import spec

    spec.parse_drill({**body_of(body), "report_id": report_id})
    _exists(svc, report_id)
    raise not_implemented("Переход к блокам")


@router.post("/reports/{report_id}/drill/rows")
def drill_rows(svc: Svc, report_id: int, body: Payload) -> dict[str, Any]:
    from . import spec

    spec.parse_drill_rows({**body_of(body), "report_id": report_id})
    _exists(svc, report_id)
    raise not_implemented("Таблица блоков ячейки")


@router.post("/compare")
def compare(svc: Svc, body: Payload) -> dict[str, Any]:
    from . import spec

    a, b = spec.parse_compare_reports(body_of(body))
    _exists(svc, a)
    _exists(svc, b)
    raise not_implemented("Сравнение отчётов")
