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
from collections.abc import Callable
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


def _lower_this_thread() -> None:
    """The warm-up runs at the lowest priority of this thread only: at the start of the program it must not take the
    processor from the window (its first frame under a software renderer — perf3d, ≤ 3 s on CI) nor from a job.
    Linux: the nice of a thread is its own (`setpriority` by the native id); Windows: the background mode of the
    thread (lower processor and disk priority). Elsewhere, or refused — the warm-up runs as it is."""
    import os
    import sys

    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetThreadPriority(kernel32.GetCurrentThread(), 0x00010000)  # THREAD_MODE_BACKGROUND_BEGIN
        elif sys.platform.startswith("linux"):
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
    except (OSError, AttributeError):
        pass


def _warm_kernels(queue_name: str = "quick", busy: Callable[[], bool] | None = None) -> None:
    """Compile the numba kernels of the operations, of the view and of the preview of a stitch once, here, into
    NUMBA_CACHE_DIR, or load them from it (`ops/warm.py`): the quick worker the operations first (the stitch the
    very first), the heavy one the strips of a stitch, `wireframe.check`, what the process of the API needs (the
    simplified copies, the section, the click) and the operations from the other end. Made cold where they were
    needed, they held the first view of a
    wireframe for ~7 s and the first operation for ~13 s (PROGRESS «Э9 9.36»). `busy()` — a job runs: the next step
    waits for its end."""
    import logging
    import time

    log = logging.getLogger("bf_core.worker")
    _lower_this_thread()
    try:
        from bf_core.ops import warm

        order = warm.QUICK if queue_name == "quick" else warm.HEAVY
        t0 = time.perf_counter()
        took = warm.warm(order, busy=busy)
        log.info("warm-up: %d of %d steps in %.1f s", len(took), len(order), time.perf_counter() - t0)
    except Exception:  # noqa: BLE001 - a warm-up never stops the worker; the job compiles them itself then
        log.exception("warm-up of the kernels failed")


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
    if not os.environ.get("BF_NO_WARMUP"):  # in a thread: the worker takes jobs at once; the quick one's are not slowed
        busy = (lambda: current["id"] is not None) if queue_name == "quick" else None
        threading.Thread(target=_warm_kernels, args=(queue_name, busy), name="worker-warm", daemon=True).start()
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
        except JobCancelled as exc:
            send(("cancelled", job["id"], exc.events))
        except JobError as exc:
            err: dict[str, Any] = {**exc.extra, "code": exc.code, "message": exc.message, "hint": exc.hint}
            if exc.events:
                err["events"] = exc.events  # the scheduler takes them out before it stores the error
            send(("error", job["id"], err, exc.retry))
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
