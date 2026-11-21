"""/api/v1/qaqc/reports — QAQC reports (ADR 0020 §7, §10; подшаг 14.13; files of 14.14 come here too).

  * `GET /reports` → {items, limit: 200}; `GET /reports/{id}` → {id, name, version, state (`building` | `ready`),
    stale (null | `data` | `config` | `source_deleted`), data_name, data_sha256, inputs_sha256, elements, filter,
    charts, created_at, updated_at, built_at, job_id, files [{kind, size}], images, size, summary_size, summary}
    (`summary` only in `GET {id}`); `PUT /reports/{id} {name, expected_version}`; `DELETE /reports/{id}`.
  * `POST /reports {data_id, name?, elements?, filter?, charts?}` → 202 {report_id, job_id, source, id}: the
    object is `building` until the job `qaqc.report` (quick) ends; `charts` — `all` | `flagged` (default) | `none`.
  * `GET /reports/{id}/charts` → {charts [{key, section, element, kind?, view?, group?, title}] (≤ 200), total,
    omitted}; `PUT /reports/{id}/images/{key}` (body — PNG ≤ 2 МБ) → {key, images}: 413 `image_too_large`, 415
    `not_png`, 404 `chart_not_found`, 422 `too_many_images` (a key past 200).
  * `POST /reports/{id}/rebuild` → 202 {report_id, job_id, source, id}: the saved filter, elements and charts on
    the current data and settings (version + 1, the charts and the files are dropped); 409 `source_deleted`,
    `report_building`.
Events `objects.changed {kind: "qaqc_report", id, deleted?}`; 423 for every change of a read-only project."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

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
MAX_IMAGE = 2 * 1024 * 1024
PNG = b"\x89PNG\r\n\x1a\n"


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна отчёта.")
    return raw


def _changed(request: Request, report_id: int, **extra: Any) -> None:
    from . import report as rp

    request.app.state.core.events.publish("objects.changed", {"kind": rp.KIND, "id": report_id, **extra})


def _store(project: Any) -> Any:
    from bf_core.storage.blobs import BlobStore

    return BlobStore(project.folder)


def _submit(s: Scheduler, project: Any, report_id: int, title: str, inputs: Any = None) -> JSONResponse:
    from . import jobs as qj
    from . import report as rp

    params: dict[str, Any] = {"report_id": report_id}
    if inputs is not None:
        params["inputs"] = inputs
    key, job_id = s.submit(qj.REPORT, params, title=title)
    if inputs is None:
        with dialect.write_tx(project.engine) as conn:
            rp.set_job(conn, report_id, job_id)
    return JSONResponse({"report_id": report_id, "job_id": job_id, "source": key, "id": job_id}, status_code=202)


@router.get("/reports")
def list_reports(svc: Svc) -> dict[str, Any]:
    from . import report as rp

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"items": rp.list_reports(conn, _store(project)), "limit": rp.MAX_REPORTS}


@router.post("/reports")
def create_report(svc: Svc, s: Sched, request: Request, body: Payload) -> JSONResponse:
    from . import report as rp

    project = svc.require_current(writable=True)
    raw = _body(body)
    with dialect.write_tx(project.engine) as conn:
        inputs = rp.make_inputs(conn, raw.get("data_id"), raw.get("filter"), raw.get("elements"), raw.get("charts"))
        report_id = rp.create(conn, _store(project), raw.get("name"), inputs)
        name = rp.info(conn, _store(project), report_id)["name"]
    out = _submit(s, project, report_id, f"Отчёт QAQC: {name}")
    _changed(request, report_id)
    return out


@router.get("/reports/{report_id}")
def get_report(svc: Svc, report_id: int) -> dict[str, Any]:
    from . import report as rp

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return rp.info(conn, _store(project), report_id, summary=True)


@router.put("/reports/{report_id}")
def rename_report(svc: Svc, request: Request, report_id: int, body: Payload) -> dict[str, Any]:
    from . import report as rp

    project = svc.require_current(writable=True)
    raw = _body(body)
    with dialect.write_tx(project.engine) as conn:
        out = rp.rename(conn, _store(project), report_id, raw.get("name"), raw.get("expected_version"))
    _changed(request, report_id)
    return out


@router.delete("/reports/{report_id}")
def delete_report(svc: Svc, request: Request, report_id: int) -> dict[str, Any]:
    from . import report as rp

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        rp.delete(conn, report_id)
    _changed(request, report_id, deleted=True)
    return {"id": report_id, "deleted": True}


def _ready(conn: Any, project: Any, report_id: int) -> dict[str, Any]:
    from . import report as rp

    current = rp.info(conn, _store(project), report_id)
    if current["state"] != "ready":
        raise ApiError(409, "report_building", "Отчёт ещё строится.", "Дождитесь конца задания «Отчёт QAQC».")
    return current


@router.get("/reports/{report_id}/charts")
def report_charts(svc: Svc, report_id: int) -> dict[str, Any]:
    from . import report as rp

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        current = _ready(conn, project, report_id)
        summary = rp.read_json(conn, _store(project), report_id, rp.SUMMARY)
    return rp.charts_of(summary, current["charts"] or "flagged")


@router.put("/reports/{report_id}/images/{key}")
async def put_image(svc: Svc, request: Request, report_id: int, key: str) -> dict[str, Any]:
    from starlette.concurrency import run_in_threadpool

    from . import report as rp

    project = svc.require_current(writable=True)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_IMAGE:
            raise ApiError(413, "image_too_large", "Рисунок больше 2 МБ.", "Уменьшите размер графика и повторите.")
    if not bytes(data[:8]) == PNG:
        raise ApiError(415, "not_png", "Рисунок графика должен быть PNG.", "Передайте картинку в формате PNG.")

    def write() -> int:
        with dialect.write_tx(project.engine) as conn:
            _ready(conn, project, report_id)
            return rp.put_image(conn, _store(project), report_id, key, bytes(data))

    images = await run_in_threadpool(write)
    _changed(request, report_id)
    return {"key": key, "images": images}


@router.post("/reports/{report_id}/rebuild")
def rebuild_report(svc: Svc, s: Sched, report_id: int) -> JSONResponse:
    from . import report as rp

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        current = _ready(conn, project, report_id)
        row_meta = conn.execute(sa_meta(report_id)).scalar_one()
        data_id = rp.find_data(conn, (row_meta or {}).get("data_uid"))
        if data_id is None:
            raise ApiError(409, "source_deleted", "Данные отчёта удалены — перестроить отчёт нельзя.",
                           "Создайте новый отчёт по другим данным QAQC.")  # fmt: skip
        inputs = rp.make_inputs(conn, data_id, current["filter"], current["elements"], current["charts"])
    return _submit(s, project, report_id, f"Отчёт QAQC: {current['name']} (перестроить)", inputs)


def sa_meta(report_id: int) -> Any:
    import sqlalchemy as sa

    from bf_core.db.schema import objects

    return sa.select(objects.c.meta).where(objects.c.id == report_id)
