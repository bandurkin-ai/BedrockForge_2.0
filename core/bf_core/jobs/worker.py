"""A worker process (`spawn`, ADR 0002 §4): takes jobs from the scheduler over a pipe, runs their handlers.

Messages to the scheduler: ("ready",), ("progress", id, p, msg), ("log", id, level, text), ("beat", id),
("done", id, result), ("error", id, {code, message, hint}, retry), ("cancelled", id).
From the scheduler: ("run", job), ("cancel", id), ("stop",).
"""

from __future__ import annotations

import os
import queue
import threading
import traceback
from multiprocessing.connection import Connection
from typing import Any

HEARTBEAT_S = 5.0


def _worker_log(queue_name: str) -> None:
    """The worker's own log file (its stderr is not the core's in a packaged app)."""
    import logging

    try:
        from bf_core.paths import logs_dir

        logs_dir().mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(logs_dir() / f"worker-{queue_name}.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(process)d %(levelname)s %(message)s"))
        root = logging.getLogger("bf_core")
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    except OSError:
        pass


def _warm_kernels() -> None:
    """Compile the numba kernels of the simplified copies of wireframes once, here, into NUMBA_CACHE_DIR: the
    process of the API then loads them from the cache. Made there cold, they held its requests for ~7 s at the
    first view of a wireframe (the audit of the demo waited for its findings). A small mesh: a tilted plane (flat
    areas) and a bumpy one, through the same calls and dtypes as a view."""
    import logging

    try:
        import numpy as np

        from bf_core.geo import meshes as gm

        n = 9
        x, y = np.meshgrid(np.arange(n, dtype=float), np.arange(n, dtype=float))
        idx = np.arange(n * n).reshape(n, n)
        a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
        tri = np.r_[np.c_[a, b, d], np.c_[a, d, c]]
        for z in (0.1 * x, np.sin(x) * np.cos(y)):
            v, t = gm.check_mesh(np.c_[x.ravel() + 500_000.0, y.ravel() + 7_000_000.0, z.ravel()], tri)
            for p in gm.parts_table(v, t):
                gm.levels(*gm.part_mesh(v, t, p))
    except Exception:  # noqa: BLE001 - a warm-up never stops the worker; the view compiles them itself then
        logging.getLogger("bf_core.worker").exception("warm-up of the kernels failed")


def main(conn: Connection, queue_name: str) -> None:
    conn.send(("hello",))  # before any import: the scheduler sees the process is alive and the pipe works
    _worker_log(queue_name)
    import logging

    wlog = logging.getLogger("bf_core.worker")
    wlog.info("worker %s: started", queue_name)
    try:
        _main(conn, queue_name)
    except BaseException:
        wlog.exception("worker %s: failed", queue_name)
        raise
    wlog.info("worker %s: stopped", queue_name)


def _main(conn: Connection, queue_name: str) -> None:
    os.environ.setdefault("BF_WORKER", queue_name)
    from bf_core.paths import configure_numba_cache

    configure_numba_cache()
    from .context import JobCancelled, JobContext, JobError
    from .registry import load_handlers

    kinds = load_handlers()
    import logging

    logging.getLogger("bf_core.worker").info("handlers: %s", ", ".join(sorted(kinds)))
    send_lock = threading.Lock()

    def send(msg: tuple[Any, ...]) -> None:
        with send_lock:
            conn.send(msg)

    jobs: queue.Queue[dict[str, Any] | None] = queue.Queue()
    cancel = threading.Event()
    current: dict[str, int | None] = {"id": None}
    cancelled_ids: set[int] = set()  # a cancel may arrive before the job starts running here
    state_lock = threading.Lock()

    def reader() -> None:
        lost = False
        try:
            while True:
                msg = conn.recv()
                if msg[0] == "run":
                    jobs.put(msg[1])
                elif msg[0] == "cancel":
                    with state_lock:
                        cancelled_ids.add(int(msg[1]))
                        if msg[1] == current["id"]:
                            cancel.set()
                elif msg[0] == "stop":
                    break
        except (EOFError, OSError):
            lost = True
        jobs.put(None)
        if lost:
            # The core is gone (killed): stop the running job, and leave for sure — a new core requeues it,
            # two copies of one job must never run.
            cancel.set()
            threading.Timer(2.0, lambda: os._exit(4)).start()

    def beat() -> None:
        while True:
            job_id = current["id"]
            if job_id is not None:
                try:
                    send(("beat", job_id))
                except (OSError, ValueError):
                    return
            if stop_beat.wait(HEARTBEAT_S):
                return

    stop_beat = threading.Event()
    threading.Thread(target=reader, name="worker-reader", daemon=True).start()
    threading.Thread(target=beat, name="worker-beat", daemon=True).start()
    send(("ready",))
    if queue_name == "quick" and not os.environ.get("BF_NO_WARMUP"):
        threading.Thread(target=_warm_kernels, name="worker-warm", daemon=True).start()
    while True:
        job = jobs.get()
        if job is None:
            break
        with state_lock:
            cancel.clear()
            current["id"] = job["id"]
            if job["id"] in cancelled_ids:
                cancel.set()
        ctx = JobContext(job, send, cancel)
        try:
            spec = kinds.get(job["kind"])
            if spec is None:
                raise JobError("unknown_job", f"Неизвестный вид задания: {job['kind']}.", "Обновите программу.")
            ctx.tick(0.0)
            result = spec.run(ctx) or {}
            send(("done", job["id"], result))
        except JobCancelled:
            send(("cancelled", job["id"]))
        except JobError as exc:
            send(("error", job["id"], {"code": exc.code, "message": exc.message, "hint": exc.hint}, exc.retry))
        except MemoryError:
            send(("error", job["id"], _memory_error(job), False))
            break  # a worker after MemoryError is restarted (ADR 0002 §4)
        except Exception:  # noqa: BLE001 - every failure is reported to the job, the worker lives on
            ctx.log(traceback.format_exc(limit=8), "error")
            err = {
                "code": "job_failed",
                "message": "Задание завершилось с ошибкой.",
                "hint": "Откройте журнал задания: в нём подробности. Повторите или отправьте журнал разработчику.",
            }
            send(("error", job["id"], err, True))
        finally:
            ctx.close()
            current["id"] = None
    stop_beat.set()


def _memory_error(job: dict[str, Any]) -> dict[str, str]:
    need = job.get("memory_mb")
    try:
        import psutil

        avail = psutil.virtual_memory().available / 2**30
    except Exception:  # noqa: BLE001
        avail = float("nan")
    need_txt = f"~{need / 1024:.1f} ГБ" if need else "больше, чем доступно"
    return {
        "code": "out_of_memory",
        "message": f"Не хватило памяти: нужно {need_txt}, доступно {avail:.1f} ГБ.",
        "hint": "Закройте другие программы или уменьшите объём данных задания и повторите.",
    }
