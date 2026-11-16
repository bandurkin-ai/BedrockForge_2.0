"""The job queue: enqueue, claim, report, finish, requeue after a crash."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.models import Job

TERMINAL = ("done", "failed", "cancelled")
# A running job whose worker sent no heartbeat for this long is considered lost.
STALE_SECONDS = 120
LOG_LIMIT = 500


def now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(session: Session, kind: str, params: dict, *, project_id: int | None = None,
            title: str = "", max_attempts: int = 3) -> Job:
    """Adds a job; it becomes visible to workers when the caller commits."""
    job = Job(project_id=project_id, kind=kind, title=title[:255], params=params, status="queued",
              max_attempts=max_attempts, log=[], progress=0.0, message="В очереди")
    session.add(job)
    session.flush()
    return job


def claim(session: Session, worker: str, kinds: list[str] | None = None) -> Job | None:
    """Takes the oldest queued job and marks it running (committed)."""
    where = "status = 'queued'"
    params: dict = {}
    if kinds:
        where += " AND kind = ANY(:kinds)"
        params["kinds"] = kinds
    row = session.execute(
        text(f"SELECT id FROM job WHERE {where} ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1"), params
    ).first()
    if row is None:
        session.rollback()
        return None
    t = now()
    session.execute(
        update(Job).where(Job.id == row[0]).values(
            status="running", worker=worker, attempts=Job.attempts + 1, started_at=t,
            heartbeat_at=t, updated_at=t, message="Выполняется", error=None,
        )
    )
    session.commit()
    return session.get(Job, row[0], populate_existing=True)


def report(session_factory: Callable[[], Session], job_id: int, *, progress: float | None = None,
           message: str | None = None, log: dict | None = None) -> bool:
    """Updates progress / message / journal in its own transaction; returns cancel_requested."""
    sets = ["heartbeat_at = now()", "updated_at = now()"]
    params: dict = {"id": job_id, "limit": LOG_LIMIT}
    if progress is not None:
        sets.append("progress = :progress")
        params["progress"] = max(0.0, min(1.0, float(progress)))
    if message is not None:
        sets.append("message = :message")
        params["message"] = message
    if log is not None:
        # Keep the last LOG_LIMIT entries.
        sets.append(
            "log = (SELECT COALESCE(jsonb_agg(e ORDER BY n), '[]'::jsonb) FROM ("
            "  SELECT e, n FROM jsonb_array_elements(log || jsonb_build_array(CAST(:entry AS jsonb))) "
            "  WITH ORDINALITY AS a(e, n) ORDER BY n DESC LIMIT :limit) last)"
        )
        params["entry"] = json.dumps(log, ensure_ascii=False)
    with session_factory() as s:
        cancel = s.execute(
            text(f"UPDATE job SET {', '.join(sets)} WHERE id = :id RETURNING cancel_requested"), params
        ).scalar()
        s.commit()
    return bool(cancel)


def request_cancel(session: Session, job: Job) -> Job:
    """A queued job is cancelled at once; a running one stops at its next progress report."""
    t = now()
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = t
        job.message = "Отменено"
    elif job.status == "running":
        job.cancel_requested = True
        job.message = "Отмена…"
    job.updated_at = t
    session.commit()
    return job


def requeue_stale(session: Session, stale_seconds: int = STALE_SECONDS) -> list[int]:
    """Running jobs whose worker died go back to the queue, or fail after max_attempts."""
    rows = session.execute(
        text(
            "UPDATE job SET "
            "  status = CASE WHEN attempts < max_attempts AND NOT cancel_requested THEN 'queued' "
            "                WHEN cancel_requested THEN 'cancelled' ELSE 'failed' END, "
            "  message = CASE WHEN attempts < max_attempts AND NOT cancel_requested "
            "                 THEN 'Повтор: рабочий процесс остановился (часто — из-за нехватки памяти)' "
            "                 WHEN cancel_requested THEN 'Отменено' ELSE 'Рабочий процесс остановился' END, "
            "  error = CASE WHEN attempts >= max_attempts AND NOT cancel_requested "
            "               THEN 'Рабочий процесс остановился, попытки исчерпаны. Чаще всего ему не хватает памяти: "
            "увеличьте память Docker (Settings → Resources → Memory)' ELSE error END, "
            "  finished_at = CASE WHEN attempts < max_attempts AND NOT cancel_requested THEN NULL ELSE now() END, "
            "  worker = NULL, updated_at = now() "
            "WHERE status = 'running' AND heartbeat_at < now() - make_interval(secs => :s) "
            "RETURNING id"
        ),
        {"s": stale_seconds},
    ).all()
    session.commit()
    return [r[0] for r in rows]


def job_out(job: Job) -> dict:
    return {
        "id": job.id, "project_id": job.project_id, "kind": job.kind, "title": job.title,
        "status": job.status, "progress": job.progress, "message": job.message,
        "result": job.result, "error": job.error, "attempts": job.attempts,
        "cancel_requested": job.cancel_requested,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def retry(session: Session, job: Job) -> Job:
    """A failed or cancelled job back in the queue as it was (the caller commits)."""
    job.status = "queued"
    job.error = None
    job.result = None
    job.progress = 0.0
    job.message = "В очереди (повтор)"
    job.attempts = 0
    job.cancel_requested = False
    job.started_at = None
    job.finished_at = None
    job.heartbeat_at = None
    job.worker = None
    job.updated_at = now()
    job.log = [*(job.log or []), {"t": now().isoformat(), "level": "info", "text": "Повтор задания"}]
    return job
