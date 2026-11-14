"""Worker: claims queued jobs and runs their handlers.

    python -m app.jobs.worker            one worker process (run several for parallel jobs)

The API process can also run worker threads (JOBS_IN_API, for local development).
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.jobs import queue
from app.models import Job

log = logging.getLogger("bedrockforge.jobs")

POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 10
GC_SECONDS = 3600
# Progress reports closer than this are merged (except the final one).
REPORT_INTERVAL = 0.25


class JobCancelled(Exception):
    pass


class JobError(Exception):
    """An expected failure: the message is shown to the user as is."""


Handler = Callable[["JobContext", dict], dict]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return register


@dataclass
class JobContext:
    job_id: int
    project_id: int | None
    session: Session
    session_factory: Callable[[], Session]
    _last: float = 0.0
    _pending: dict = field(default_factory=dict)

    def progress(self, fraction: float, message: str | None = None, *, force: bool = False) -> None:
        """Reports progress; raises JobCancelled if the user asked to stop."""
        t = time.monotonic()
        if not force and t - self._last < REPORT_INTERVAL:
            return
        self._last = t
        if queue.report(self.session_factory, self.job_id, progress=fraction, message=message):
            raise JobCancelled()

    def check_cancel(self) -> None:
        if queue.report(self.session_factory, self.job_id):
            raise JobCancelled()

    def log(self, text: str, level: str = "info") -> None:
        queue.report(self.session_factory, self.job_id,
                     log={"t": queue.now().isoformat(), "level": level, "text": text})


def _heartbeat(session_factory: Callable[[], Session], job_id: int, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            queue.report(session_factory, job_id)
        except Exception:  # the database may be briefly unavailable; the next beat retries
            log.exception("heartbeat of job %s failed", job_id)


def _finish(session_factory: Callable[[], Session], job_id: int, **values) -> None:
    with session_factory() as s:
        s.execute(update(Job).where(Job.id == job_id).values(
            finished_at=queue.now(), updated_at=queue.now(), worker=None, **values))
        s.commit()


def run_job(session_factory: Callable[[], Session], job: Job) -> str:
    """Runs one claimed job to its end; returns the final status."""
    fn = HANDLERS.get(job.kind)
    if fn is None:
        _finish(session_factory, job.id, status="failed", error=f"Неизвестный тип задания «{job.kind}»",
                message="Ошибка")
        return "failed"
    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(session_factory, job.id, stop), daemon=True)
    beat.start()
    session = session_factory()
    ctx = JobContext(job.id, job.project_id, session, session_factory)
    try:
        result = fn(ctx, dict(job.params or {}))
        # The results and the "done" status commit together.
        session.execute(update(Job).where(Job.id == job.id).values(
            status="done", progress=1.0, message="Готово", result=result or {},
            finished_at=queue.now(), updated_at=queue.now(), worker=None))
        session.commit()
        return "done"
    except JobCancelled:
        session.rollback()
        _finish(session_factory, job.id, status="cancelled", message="Отменено")
        return "cancelled"
    except JobError as exc:
        session.rollback()
        _finish(session_factory, job.id, status="failed", error=str(exc), message="Ошибка")
        return "failed"
    except Exception as exc:
        session.rollback()
        log.exception("job %s (%s) failed", job.id, job.kind)
        try:
            ctx.log(traceback.format_exc(limit=8), "error")
        except Exception:
            pass
        _finish(session_factory, job.id, status="failed", message="Ошибка",
                error=f"Внутренняя ошибка: {type(exc).__name__}: {exc}")
        return "failed"
    finally:
        stop.set()
        session.close()


def run_pending(session_factory: Callable[[], Session], *, worker: str = "inline", limit: int | None = None) -> int:
    """Runs queued jobs until the queue is empty (tests, scripts). Returns how many ran."""
    _ensure_handlers()
    n = 0
    while limit is None or n < limit:
        with session_factory() as s:
            job = queue.claim(s, worker)
        if job is None:
            return n
        run_job(session_factory, job)
        n += 1
    return n


def _ensure_handlers() -> None:
    from app.jobs import handlers  # noqa: F401  (registers the handlers)


class Worker:
    """A polling loop; also requeues jobs of workers that died."""

    def __init__(self, session_factory: Callable[[], Session], name: str | None = None):
        self.session_factory = session_factory
        self.name = (name or f"{socket.gethostname()}:{os.getpid()}")[:64]
        self.stopping = threading.Event()

    def run(self) -> None:
        _ensure_handlers()
        last_sweep = 0.0
        last_gc = time.monotonic()
        while not self.stopping.is_set():
            try:
                if time.monotonic() - last_gc > GC_SECONDS:
                    last_gc = time.monotonic()
                    from app.storage import gc

                    with self.session_factory() as s:
                        r = gc.collect(s)
                    log.info("storage gc: %s", r)
                if time.monotonic() - last_sweep > 30:
                    last_sweep = time.monotonic()
                    with self.session_factory() as s:
                        for job_id in queue.requeue_stale(s):
                            log.warning("job %s requeued after a lost worker", job_id)
                with self.session_factory() as s:
                    job = queue.claim(s, self.name)
                if job is None:
                    self.stopping.wait(POLL_SECONDS)
                    continue
                log.info("job %s (%s) started", job.id, job.kind)
                status = run_job(self.session_factory, job)
                log.info("job %s finished: %s", job.id, status)
            except Exception:
                log.exception("worker loop error")
                self.stopping.wait(2)

    def start_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name=f"job-worker-{self.name}", daemon=True)
        t.start()
        return t


def main() -> None:
    from app.db import SessionLocal

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    worker = Worker(SessionLocal)
    signal.signal(signal.SIGTERM, lambda *_: worker.stopping.set())
    signal.signal(signal.SIGINT, lambda *_: worker.stopping.set())
    log.info("worker %s started", worker.name)
    worker.run()


if __name__ == "__main__":
    main()
