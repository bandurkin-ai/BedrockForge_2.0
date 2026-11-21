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
  * `POST /reports/{id}/files {kind: "xlsx", limit?}` → 202 {report_id, job_id, source, id} of `qaqc.report_file`
    (quick); `kind: "pdf"` — 422 `not_available` («Э22»); `limit` — lines of a sheet (1000 … 1 048 575, for checks).
    `POST /reports/{id}/save {kind, path}` → {path, size}: a copy of the built file («.xlsx» added to a path
    without an extension; 409 `file_not_built`, 422 `bad_path` — inside the project folder; works in a read-only
    project — it writes nothing into it). A picture is a PNG ≤ 2 МБ and ≤ 8192 px a side (415 `image_size`).
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
MAX_PX = 8192


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
    try:
        key, job_id = s.submit(qj.REPORT, params, title=title)
        if inputs is None:  # the job sets it too when it starts: a cancel before this line still finds it
            with dialect.write_tx(project.engine) as conn:
                rp.set_job(conn, report_id, job_id)
    except BaseException:
        if inputs is None:  # a new report whose job did not start: nothing is left `building`
            with dialect.write_tx(project.engine) as conn:
                rp.drop_building(conn, report_id)
        raise
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
    from bf_core.composites.xlsx_drawing import png_size

    try:
        w, h = png_size(bytes(data[:24]))
    except ValueError:
        raise ApiError(415, "not_png", "Рисунок графика — повреждённый PNG.", "Нарисуйте график заново.") from None
    if not (0 < w <= MAX_PX and 0 < h <= MAX_PX):
        raise ApiError(415, "image_size", f"Рисунок {w} × {h} пикселей — больше {MAX_PX} по стороне.",
                       "Уменьшите график и повторите.")  # fmt: skip

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


def _kind(raw: dict[str, Any]) -> str:
    kind = raw.get("kind", "xlsx")
    if kind == "pdf":
        raise ApiError(
            422,
            "not_available",
            "PDF отчёта QAQC пока недоступен.",
            "Появится на этапе Э22 (печать и макеты); сейчас сохраните XLSX.",
            extra={"param": "kind"},
        )
    if kind != "xlsx":
        raise ApiError(422, "bad_param", "Неизвестный вид файла отчёта.", "Выберите XLSX.", extra={"param": "kind"})
    return "xlsx"


@router.post("/reports/{report_id}/files")
def build_file(svc: Svc, s: Sched, report_id: int, body: Payload) -> JSONResponse:
    from . import jobs as qj
    from . import report_xlsx as rx

    project = svc.require_current(writable=True)
    raw = _body(body)
    _kind(raw)
    limit = raw.get("limit")
    wrong = isinstance(limit, bool) or not isinstance(limit, int) or not 1000 <= limit <= rx.MAX_LINES
    if limit is not None and wrong:
        raise ApiError(422, "bad_param", "Неверный предел строк листа.", f"От 1000 до {rx.MAX_LINES}.",
                       extra={"param": "limit"})  # fmt: skip
    with dialect.read_tx(project.engine) as conn:
        current = _ready(conn, project, report_id)
    params = {"report_id": report_id, "version": current["version"], "kind": "xlsx", "limit": limit}
    key, job_id = s.submit(qj.REPORT_FILE, params, title=f"XLSX отчёта QAQC: {current['name']}")
    return JSONResponse({"report_id": report_id, "job_id": job_id, "source": key, "id": job_id}, status_code=202)


@router.post("/reports/{report_id}/save")
def save_file(svc: Svc, report_id: int, body: Payload) -> dict[str, Any]:
    import os
    import shutil
    import uuid
    from pathlib import Path

    from . import report as rp

    project = svc.require_current()
    raw = _body(body)
    _kind(raw)
    target = raw.get("path")
    if not isinstance(target, str) or not target.strip():
        raise ApiError(422, "bad_param", "Не выбран путь файла.", "Выберите, куда сохранить отчёт.",
                       extra={"param": "path"})  # fmt: skip
    dst = Path(target)
    if not dst.suffix:
        dst = dst.with_suffix(".xlsx")
    inside = dst.resolve().is_relative_to(Path(project.folder).resolve())
    if inside:
        raise ApiError(422, "bad_path", "Нельзя сохранить файл внутрь папки проекта.",
                       "Выберите другую папку, например «Документы».", extra={"param": "path"})  # fmt: skip
    if not dst.parent.is_dir():
        raise ApiError(404, "folder_not_found", f"Папка «{dst.parent}» не найдена.", "Выберите другую папку.",
                       extra={"param": "path"})  # fmt: skip
    with dialect.read_tx(project.engine) as conn:
        _ready(conn, project, report_id)
        sha = rp.file_sha(conn, report_id)
    src = _store(project).path_of(sha) if sha else None
    if src is None or not src.is_file():
        raise ApiError(409, "file_not_built", "Файл XLSX отчёта ещё не сформирован.",
                       "Нажмите «Сформировать XLSX» и дождитесь конца задания.")  # fmt: skip
    tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise ApiError(409, "file_write_failed", f"Не удалось записать «{dst.name}».",
                       "Закройте файл в другой программе или выберите другой путь.") from exc  # fmt: skip
    return {"path": str(dst), "size": dst.stat().st_size}
