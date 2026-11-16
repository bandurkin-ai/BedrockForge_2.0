"""/api/v1/jobs — the «Задания» tab (ТЗ 7.6): list with the reason of waiting, cancel, retry, log, workers."""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError

from . import store
from .registry import KINDS
from .scheduler import Scheduler
from .service import jobs

router = APIRouter(prefix="/jobs")
Sched = Annotated[Scheduler, Depends(jobs)]


class SubmitBody(BaseModel):
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    title: str | None = Field(default=None, max_length=400)


class WorkersBody(BaseModel):
    heavy: int = Field(ge=1, le=32)


def _not_found() -> ApiError:
    return ApiError(404, "job_not_found", "Задание не найдено.", "Обновите вкладку «Задания».")


def _why_waiting(s: Scheduler, key: str, job: dict[str, Any], queued: list[dict[str, Any]]) -> str | None:
    if job["status"] != "queued":
        return None
    if not s.alive(job["queue"]):
        return "Нет работающего исполнителя заданий. Нажмите «Перезапустить исполнителей»."
    reason = s.waiting.get((key, job["id"]))
    if reason:
        return reason
    ahead = [
        j for j in queued if j["queue"] == job["queue"] and (j["priority"], j["id"]) < (job["priority"], job["id"])
    ]
    parts = []
    if ahead:
        parts.append(f"Впереди заданий: {len(ahead)}")
    for run in s.running(job["queue"]):
        minutes = int((time.monotonic() - run.started) // 60)
        parts.append(f"№{run.job_id} «{run.title}» выполняется {minutes} мин")
    return ", ".join(parts) if parts else "Ожидает исполнителя"


@router.get("")
def list_jobs(s: Sched) -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    sources = [s.instance] + ([s.project] if s.project else []) + list(s.detached.values())
    for src in sources:
        rows = store.list_jobs(src.engine, src.instance)
        queued = [r for r in rows if r["status"] == "queued"]
        for r in rows:
            r["source"] = src.key
            r["why_waiting"] = _why_waiting(s, src.key, r, queued)
            out.append(r)
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return {"jobs": out, "workers": s.status()}


@router.post("")
def submit(s: Sched, body: SubmitBody) -> dict[str, Any]:
    if body.kind not in KINDS:
        raise ApiError(422, "unknown_job", f"Неизвестный вид задания: {body.kind}.", "Обновите программу.")
    try:
        key, job_id = s.submit(body.kind, body.params, title=body.title)
    except LookupError as exc:
        raise ApiError(409, "no_project", "Проект не открыт.", "Откройте или создайте проект.") from exc
    return {"source": key, "id": job_id}


def _source(s: Scheduler, key: str) -> Any:
    src = s.source(key)
    if src is None:
        raise _not_found()
    return src


@router.get("/{key}/{job_id}")
def get_job(s: Sched, key: str, job_id: int) -> dict[str, Any]:
    src = _source(s, key)
    row = store.get(src.engine, src.instance, job_id)
    if row is None:
        raise _not_found()
    return {**row, "source": key}


@router.get("/{key}/{job_id}/log")
def job_log(s: Sched, key: str, job_id: int) -> dict[str, Any]:
    src = _source(s, key)
    return {"lines": store.read_log(src.engine, src.instance, job_id)}


@router.post("/{key}/{job_id}/cancel")
def cancel(s: Sched, key: str, job_id: int) -> dict[str, Any]:
    try:
        s.cancel(key, job_id)
    except LookupError as exc:
        raise _not_found() from exc
    return get_job(s, key, job_id)


@router.post("/{key}/{job_id}/retry")
def retry(s: Sched, key: str, job_id: int) -> dict[str, Any]:
    try:
        new_id = s.retry(key, job_id)
    except LookupError as exc:
        raise _not_found() from exc
    return {"source": key, "id": new_id}


@router.post("/workers/restart")
def restart_workers(s: Sched) -> dict[str, Any]:
    s.restart_workers()
    return s.status()


@router.put("/workers")
def set_workers(s: Sched, body: WorkersBody) -> dict[str, Any]:
    s.set_heavy_workers(body.heavy)
    return s.status()
