"""The only job claimer (ADR 0002 §4): a thread in the core that owns the worker processes.

Queues: `heavy` (N workers, default 1, ТЗ 7.6) and `quick` (1 warm worker that never waits for heavy jobs).
Sources: instance.sqlite (jobs without a project) and the open project; a project closed while its jobs run
stays attached until they end. Progress → SSE at once, → DB at most once a second.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from multiprocessing.connection import Connection, wait
from typing import Any

import sqlalchemy as sa

from bf_core.api.events import EventBus
from bf_core.db import dialect

from . import store
from .registry import KINDS, load_handlers

log = logging.getLogger("bf_core.jobs")

HEARTBEAT_LOST_S = 30.0
CANCEL_GRACE_S = 15.0
PROGRESS_DB_EVERY_S = 1.0
RESERVE_MB = 1536  # ADR 0002 §4: start while estimate ≤ free RAM − 1.5 GB
MAX_RESTARTS_PER_MIN = 5


@dataclass
class Source:
    """Where a job row lives: instance.sqlite (key "instance") or a project (key = project uid)."""

    key: str
    engine: sa.Engine
    instance: bool
    folder: str | None = None


#: Kinds of jobs that make lines, rasters or wireframes of a project, or what «Свойства» tell about them: their
#: end is an `objects.changed` event.
OBJECT_JOBS = frozenset({"import.objects", "model.dtm", "wireframe.check"})


@dataclass
class Running:
    job_id: int
    source: Source
    started: float
    title: str
    kind: str
    last_db: float = 0.0
    progress: float = 0.0
    message: str | None = None
    beat: float = field(default_factory=time.monotonic)
    cancel_at: float | None = None


@dataclass
class Worker:
    name: str
    queue: str
    process: Any
    conn: Connection
    ready: bool = False
    job: Running | None = None


class Scheduler:
    def __init__(self, events: EventBus, instance: Source, heavy_workers: int = 1) -> None:
        load_handlers()
        self.events = events
        self.instance = instance
        self.project: Source | None = None
        self.detached: dict[str, Source] = {}
        self.heavy_workers = max(1, heavy_workers)
        self.workers: list[Worker] = []
        self.waiting: dict[tuple[str, int], str] = {}  # (source key, job id) → reason
        self.stopped_reason: str | None = None
        self._restarts: list[float] = []
        self.on_release: Callable[[str], None] = lambda _key: None
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._ctx = mp.get_context("spawn")
        self._seq = 0
        self._last_dispatch = 0.0
        store.recover(instance.engine, True)
        with self._lock:
            self._ensure_workers()
        self._thread = threading.Thread(target=self._loop, name="job-scheduler", daemon=True)
        self._thread.start()

    # --- sources ----------------------------------------------------------------------------

    def attach_project(self, key: str, engine: sa.Engine, folder: str) -> None:
        """The opened project becomes the source of new claims; its interrupted jobs are recovered."""
        with self._lock:
            if key in self.detached:
                self.project = self.detached.pop(key)
            else:
                store.recover(engine, False)
                self.project = Source(key, engine, False, folder)
        self.wake()

    def detach_project(self, key: str) -> bool:
        """Called when the project is closed. True: jobs still run — the caller keeps the project open."""
        with self._lock:
            if self.project is None or self.project.key != key:
                return False
            busy = any(w.job and w.job.source.key == key for w in self.workers)
            if busy:
                self.detached[key] = self.project
            self.project = None
            return busy

    def busy(self, key: str) -> bool:
        """Jobs of this project run now."""
        with self._lock:
            return any(w.job and w.job.source.key == key for w in self.workers)

    def released(self, key: str) -> bool:
        """True once a detached project has no running jobs (the project service then closes it)."""
        with self._lock:
            return key not in self.detached

    def source(self, key: str) -> Source | None:
        with self._lock:
            if key == "instance":
                return self.instance
            if self.project and self.project.key == key:
                return self.project
            return self.detached.get(key)

    # --- public API -------------------------------------------------------------------------

    def submit(
        self,
        kind: str,
        params: dict[str, Any],
        *,
        title: str | None = None,
        secret: str | None = None,
        source: str | None = None,
    ) -> tuple[str, int]:
        """A new job in instance.sqlite (instance kinds) or in the open project — or in `source`, which must be
        the instance or an attached project (a retry goes to the project of the failed job)."""
        spec = KINDS.get(kind)
        if spec is None:
            raise KeyError(kind)
        with self._lock:
            if source is not None:
                src = (
                    self.instance
                    if source == "instance"
                    else (self.project if self.project and self.project.key == source else None)
                )
            else:
                src = self.instance if spec.instance else self.project
        if src is None:
            raise LookupError("no project")
        job_id = store.insert(
            src.engine,
            instance=src.instance,
            queue=spec.queue,
            kind=kind,
            title=title or spec.title,
            params=params,
            secret=secret,
            max_attempts=spec.max_attempts,
            memory_mb=int(spec.estimate_memory(params)),
        )
        self._publish(src, job_id)
        self.wake()
        return src.key, job_id

    def cancel(self, key: str, job_id: int) -> None:
        src = self.source(key)
        if src is None:
            raise LookupError(key)
        t, _ = store.tables(src.instance)
        with dialect.write_tx(src.engine) as conn:
            row = conn.execute(sa.select(t.c.status).where(t.c.id == job_id)).first()
            if row is None:
                raise LookupError(job_id)
            if row.status == "queued":
                conn.execute(
                    sa.update(t)
                    .where(t.c.id == job_id)
                    .values(status="cancelled", finished_at=dialect.utcnow(), secret=None, message="Отменено")
                )
            elif row.status == "running":
                conn.execute(sa.update(t).where(t.c.id == job_id).values(cancel_requested=True, message="Отмена…"))
        with self._lock:
            for w in self.workers:
                if w.job and w.job.source.key == key and w.job.job_id == job_id:
                    w.job.cancel_at = time.monotonic()
                    try:
                        w.conn.send(("cancel", job_id))
                    except OSError:
                        pass
        self._publish(src, job_id)
        self.wake()

    def retry(self, key: str, job_id: int) -> int:
        """«Повторить» a failed job: a new job with the same parameters (the same files) and `retry_of` {id, since
        — when the first job of the chain of retries started (UTC, ISO; None — it never ran)}: an export
        replaces the files it wrote itself before it failed (`wireframes/export.py`)."""
        src = self.source(key)
        if src is None:
            raise LookupError(key)
        old = store.get(src.engine, src.instance, job_id)
        if old is None:
            raise LookupError(job_id)
        if (old["params"] or {}).get("encrypted"):
            raise PermissionError("password")  # the password is not kept: the user starts the job again
        params = dict(old["params"] or {})
        before = params.get("retry_of")
        since = before.get("since") if isinstance(before, dict) else None
        params["retry_of"] = {"id": job_id, "since": since or old.get("started_at")}
        _, new_id = self.submit(old["kind"], params, title=old["title"], source=key)
        return new_id

    def restart_workers(self) -> None:
        with self._lock:
            self.stopped_reason = None
            self._restarts.clear()
            for w in list(self.workers):
                if w.job is None:
                    self._kill(w)
            self._ensure_workers()
        self.wake()

    def set_heavy_workers(self, n: int) -> None:
        with self._lock:
            self.heavy_workers = max(1, min(32, int(n)))
            idle = [w for w in self.workers if w.queue == "heavy" and w.job is None]
            extra = len([w for w in self.workers if w.queue == "heavy"]) - self.heavy_workers
            for w in idle[: max(0, extra)]:
                self._kill(w)
            self._ensure_workers()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "workers": [
                    {
                        "name": w.name,
                        "queue": w.queue,
                        "alive": w.process.is_alive(),
                        "job": w.job.job_id if w.job else None,
                    }
                    for w in self.workers
                ],
                "heavy_workers": self.heavy_workers,
                "stopped_reason": self.stopped_reason,
            }

    def alive(self, queue: str) -> bool:
        with self._lock:
            return self.stopped_reason is None and any(w.queue == queue and w.process.is_alive() for w in self.workers)

    def running(self, queue: str) -> list[Running]:
        with self._lock:
            return [w.job for w in self.workers if w.queue == queue and w.job]

    def wake(self) -> None:
        self._wake.set()

    def shutdown(self) -> None:
        self._stop.set()
        self.wake()
        self._thread.join(timeout=5)
        with self._lock:
            for w in self.workers:
                try:
                    w.conn.send(("stop",))
                except OSError:
                    pass
            deadline = time.monotonic() + 3
            for w in self.workers:
                w.process.join(timeout=max(0.0, deadline - time.monotonic()))
                if w.process.is_alive():
                    w.process.kill()
            self.workers.clear()

    # --- workers ----------------------------------------------------------------------------

    def _spawn(self, queue: str) -> None:
        from .worker import main

        self._seq += 1
        parent, child = self._ctx.Pipe()
        proc = self._ctx.Process(target=main, args=(child, queue), name=f"bf-{queue}-{self._seq}", daemon=True)
        proc.start()
        child.close()
        log.info("worker %s started: pid %s", proc.name, proc.pid)
        self.workers.append(Worker(proc.name, queue, proc, parent))

    def _ensure_workers(self) -> None:
        if self._stop.is_set() or self.stopped_reason:
            return
        want = {"heavy": self.heavy_workers, "quick": 1}
        for queue, n in want.items():
            have = len([w for w in self.workers if w.queue == queue])
            for _ in range(n - have):
                now = time.monotonic()
                self._restarts = [t for t in self._restarts if now - t < 60]
                if len(self._restarts) >= MAX_RESTARTS_PER_MIN:
                    self.stopped_reason = "Исполнитель заданий перезапускался слишком часто."
                    log.error("workers restart too often; stopped")
                    return
                self._spawn(queue)

    def _kill(self, w: Worker) -> None:
        try:
            w.process.kill()
        except Exception:  # noqa: BLE001
            pass
        w.process.join(timeout=2)
        try:
            w.conn.close()
        except OSError:
            pass
        if w in self.workers:
            self.workers.remove(w)

    # --- loop -------------------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001 - the scheduler never dies
                log.exception("scheduler tick failed")
                time.sleep(0.5)

    def _tick(self) -> None:
        with self._lock:
            conns = [w.conn for w in self.workers]
        if conns:
            ready = wait(conns, timeout=0.2)
        else:
            self._wake.wait(0.2)
            ready = []
        woken = self._wake.is_set()
        self._wake.clear()
        with self._lock:
            for w in list(self.workers):
                if w.conn in ready:
                    try:
                        while w.conn.poll():
                            self._on_message(w, w.conn.recv())
                    except (EOFError, OSError):
                        pass
                if not w.process.is_alive():
                    self._on_dead(w)
            self._watchdog()
            self._ensure_workers()
            now = time.monotonic()
            # Claims open a connection per source: on a wake-up (new job, a worker freed) or twice a second.
            if woken or ready or now - self._last_dispatch >= 0.5:
                self._last_dispatch = now
                self._dispatch()

    def _on_message(self, w: Worker, msg: tuple[Any, ...]) -> None:
        kind = msg[0]
        if kind == "hello":
            log.info("worker %s answers", w.name)
            return
        if kind == "ready":
            w.ready = True
            log.info("worker %s ready", w.name)
            return
        run = w.job
        if run is None or msg[1] != run.job_id:
            return
        src = run.source
        if kind == "beat":
            run.beat = time.monotonic()
            store.update(src.engine, src.instance, run.job_id, heartbeat_at=dialect.utcnow())
        elif kind == "progress":
            run.beat = time.monotonic()
            if msg[2] is not None:
                run.progress = float(msg[2])
            if msg[3] is not None:
                run.message = str(msg[3])
            now = time.monotonic()
            if now - run.last_db >= PROGRESS_DB_EVERY_S:
                run.last_db = now
                store.update(src.engine, src.instance, run.job_id, progress=run.progress, message=run.message)
            self._publish(src, run.job_id, progress=run.progress, message=run.message, status="running")
        elif kind == "log":
            store.add_log(src.engine, src.instance, run.job_id, str(msg[2]), str(msg[3]))
        elif kind == "done":
            self._finish(w, "done", result=msg[2], progress=1.0, message="Готово")
        elif kind == "cancelled":
            self._finish(w, "cancelled", message="Отменено")
        elif kind == "error":
            err, retry = msg[2], bool(msg[3])
            self._finish(w, "failed", error=err, message=err.get("message"), retry=retry)

    def _finish(self, w: Worker, status: str, retry: bool = False, **values: Any) -> None:
        run = w.job
        assert run is not None
        w.job = None
        src = run.source
        t, _ = store.tables(src.instance)
        if status != "done":
            store.finish_cleanup(src.engine, src.instance, run.job_id)
        with dialect.write_tx(src.engine) as conn:
            row = conn.execute(sa.select(t.c.attempts, t.c.max_attempts).where(t.c.id == run.job_id)).one()
            if status == "failed" and retry and row.attempts < row.max_attempts:
                conn.execute(
                    sa.update(t)
                    .where(t.c.id == run.job_id)
                    .values(status="queued", worker=None, message="Повтор после ошибки")
                )
            else:
                conn.execute(
                    sa.update(t)
                    .where(t.c.id == run.job_id)
                    .values(status=status, finished_at=dialect.utcnow(), **values)
                )
        self._publish(src, run.job_id)
        if status == "done" and run.kind in OBJECT_JOBS:
            # the lists of the interface are read again even if the job was over before the interface saw it run
            self.events.publish("objects.changed", {"kind": "job", "job": run.kind, "id": run.job_id})
        self._release(src)

    def _release(self, src: Source) -> None:
        if src.key in self.detached and not any(x.job and x.job.source.key == src.key for x in self.workers):
            self.detached.pop(src.key, None)
            self.on_release(src.key)

    def _on_dead(self, w: Worker) -> None:
        run = w.job
        log.warning("worker %s exited: code %s, job %s", w.name, w.process.exitcode, run.job_id if run else None)
        self._kill(w)
        self._restarts.append(time.monotonic())  # only unexpected exits count toward «слишком часто»
        if run is None:
            return
        src = run.source
        store.finish_cleanup(src.engine, src.instance, run.job_id)
        t, _ = store.tables(src.instance)
        with dialect.write_tx(src.engine) as conn:
            row = conn.execute(
                sa.select(t.c.attempts, t.c.max_attempts, t.c.cancel_requested).where(t.c.id == run.job_id)
            ).one()
            if row.cancel_requested:
                values: dict[str, Any] = {"status": "cancelled", "finished_at": dialect.utcnow(), "message": "Отменено"}
            elif row.attempts < row.max_attempts:
                values = {
                    "status": "queued",
                    "worker": None,
                    "message": "Исполнитель прервался: задание возвращено в очередь",
                }
            else:
                values = {
                    "status": "failed",
                    "finished_at": dialect.utcnow(),
                    "error": store.interrupted_error(row.attempts),
                }
            conn.execute(sa.update(t).where(t.c.id == run.job_id).values(**values))
        store.add_log(src.engine, src.instance, run.job_id, "warn", "Процесс исполнителя завершился во время задания.")
        self._publish(src, run.job_id)
        self._release(src)

    def _watchdog(self) -> None:
        now = time.monotonic()
        for w in list(self.workers):
            run = w.job
            if run is None:
                continue
            if run.cancel_at is not None and now - run.cancel_at > CANCEL_GRACE_S:
                log.warning("job %s ignores cancel: killing %s", run.job_id, w.name)
                w.process.kill()
            elif now - run.beat > HEARTBEAT_LOST_S:
                log.warning("job %s: no heartbeat, killing %s", run.job_id, w.name)
                w.process.kill()

    def _dispatch(self) -> None:
        self.waiting.clear()
        sources = [self.instance] + ([self.project] if self.project else [])
        for w in self.workers:
            if not w.ready or w.job is not None or not w.process.is_alive():
                continue
            for src in sources:
                if self._claim_for(w, src):
                    break

    def _claim_for(self, w: Worker, src: Source) -> bool:
        t, _ = store.tables(src.instance)
        with dialect.read_tx(src.engine) as conn:
            nxt = conn.execute(
                sa.select(t.c.id, t.c.memory_mb)
                .where(t.c.status == "queued", t.c.queue == w.queue)
                .order_by(t.c.priority, t.c.id)
                .limit(1)
            ).first()
        if nxt is None:
            return False
        reason = _memory_reason(int(nxt.memory_mb or 0))
        if reason == "never":
            store.update(
                src.engine,
                src.instance,
                nxt.id,
                status="failed",
                finished_at=dialect.utcnow(),
                secret=None,
                error={
                    "code": "out_of_memory",
                    "message": f"Заданию нужно ~{(nxt.memory_mb or 0) / 1024:.1f} ГБ — больше всей памяти компьютера.",
                    "hint": "Уменьшите объём данных задания.",
                },
            )
            self._publish(src, nxt.id)
            return False
        if reason:
            self.waiting[(src.key, nxt.id)] = reason
            return False
        with dialect.write_tx(src.engine) as conn:
            job = dialect.claim_job(conn, t, w.queue, w.name, dialect.utcnow())
        if job is None:
            return False
        payload = {
            "id": job["id"],
            "kind": job["kind"],
            "params": job["params"],
            "secret": job["secret"],
            "attempts": job["attempts"],
            "memory_mb": job["memory_mb"],
            "project_folder": src.folder,
        }
        w.job = Running(job["id"], src, time.monotonic(), job["title"], job["kind"])
        try:
            w.conn.send(("run", payload))
        except OSError:
            pass  # the worker is gone: _on_dead returns the job to the queue
        self._publish(src, job["id"])
        return True

    def _publish(self, src: Source, job_id: int, **live: Any) -> None:
        if live:
            self.events.publish("job", {"source": src.key, "id": job_id, **live})
            return
        row = store.get(src.engine, src.instance, job_id)
        if row:
            self.events.publish("job", {"source": src.key, **_brief(row)})


def _brief(row: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "kind", "title", "status", "progress", "message", "error", "attempts", "queue", "result")
    return {k: row.get(k) for k in keys}


def _memory_reason(need_mb: int) -> str | None:
    if need_mb <= 0:
        return None
    try:
        import psutil

        vm = psutil.virtual_memory()
    except Exception:  # noqa: BLE001
        return None
    if need_mb * 2**20 > vm.total:
        return "never"
    free_mb = vm.available / 2**20 - RESERVE_MB
    if need_mb > free_mb:
        return (
            f"Недостаточно памяти: заданию нужно ~{need_mb / 1024:.1f} ГБ, свободно {max(0.0, free_mb) / 1024:.1f} ГБ."
        )
    return None
