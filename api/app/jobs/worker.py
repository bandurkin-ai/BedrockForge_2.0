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
        # The handlers may not have been loaded (an import that failed, or one still running in
        # another thread): load them now, and say why if that is impossible.
        try:
            _ensure_handlers()
        except Exception as exc:
            log.exception("job handlers cannot be loaded")
            _finish(session_factory, job.id, status="failed", message="Ошибка",
                    error=f"Обработчики заданий не загрузились: {type(exc).__name__}: {exc}")
            return "failed"
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


_handlers_lock = threading.Lock()


def _ensure_handlers() -> None:
    """Imports the handlers once (they register themselves). Under a lock: two worker threads
    importing at once could see a half-initialised module with no handlers registered yet."""
    with _handlers_lock:
        import importlib

        importlib.import_module("app.jobs.handlers")
        if "import.objects" not in HANDLERS:
            # A half-imported module left in sys.modules: import it afresh.
            import sys

            sys.modules.pop("app.jobs.handlers", None)
            importlib.import_module("app.jobs.handlers")


# Interactive operations: seconds, not minutes. They get a worker of their own.
QUICK_KINDS = ["geometry.operation"]


def kinds_from_env() -> list[str] | None:
    """JOB_KINDS=geometry.operation,… — the kinds this worker process takes (empty: all)."""
    raw = os.environ.get("JOB_KINDS", "").strip()
    return [k.strip() for k in raw.split(",") if k.strip()] or None


class Worker:
    """A polling loop; also requeues jobs of workers that died."""

    def __init__(self, session_factory: Callable[[], Session], name: str | None = None, kinds: list[str] | None = None):
        self.session_factory = session_factory
        self.name = (name or f"{socket.gethostname()}:{os.getpid()}")[:64]
        # Only these kinds of jobs (None: any). A worker for QUICK_KINDS keeps operations on lines
        # and wireframes from waiting behind an import of a big file.
        self.kinds = kinds
        self.stopping = threading.Event()
        # the job being run, for the beat file the API reads (app/jobs/beats.py)
        self.current: int | None = None
        self.started = time.time()

    def _beat(self) -> None:
        from app.jobs import beats

        while True:
            try:
                beats.write(self.name, self.kinds, self.current, self.started)
            except Exception:  # a full or read-only disk must not stop the jobs
                log.exception("worker beat of %s not written", self.name)
            if self.stopping.wait(beats.BEAT_SECONDS):
                break
        try:
            beats.remove(self.name)
        except Exception:
            pass

    def run(self) -> None:
        try:
            _ensure_handlers()
        except Exception:
            # Keep running: run_job loads them again and reports the error with the job.
            log.exception("job handlers cannot be loaded")
        last_sweep = 0.0
        last_gc = time.monotonic()
        threading.Thread(target=self._beat, name=f"job-beat-{self.name}", daemon=True).start()
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
                    job = queue.claim(s, self.name, self.kinds)
                if job is None:
                    self.stopping.wait(POLL_SECONDS)
                    continue
                log.info("job %s (%s) started", job.id, job.kind)
                self.current = job.id
                try:
                    status = run_job(self.session_factory, job)
                finally:
                    self.current = None
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
    worker = Worker(SessionLocal, kinds=kinds_from_env())
    signal.signal(signal.SIGTERM, lambda *_: worker.stopping.set())
    signal.signal(signal.SIGINT, lambda *_: worker.stopping.set())
    log.info("worker %s started (jobs: %s)", worker.name, ", ".join(worker.kinds) if worker.kinds else "all")
    worker.run()


if __name__ == "__main__":
    # `python -m app.jobs.worker` runs this file as the module "__main__", but the handlers register
    # themselves in "app.jobs.worker" (their import), a second copy of this module with its own
    # HANDLERS: the worker here would see none ("Неизвестный тип задания"). Run the real module.
    from app.jobs.worker import main as _main

    _main()
