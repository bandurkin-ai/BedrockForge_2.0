"""What a handler sees while it runs in a worker: parameters, progress, log, cancellation, databases."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import sqlalchemy as sa


class JobCancelled(Exception):
    """Raised by `tick()` once the user asked to cancel: the handler unwinds, nothing partial is kept. A job that
    finished parts before (each committed) may attach their `events` ([[event, data], …]): the scheduler publishes."""

    events: list[list[Any]] | None = None


class JobError(Exception):
    """A user-facing failure: {code, message, hint} in Russian; `retry=False` — another attempt will not help."""

    def __init__(self, code: str, message: str, hint: str = "", retry: bool = False,
                 events: list[list[Any]] | None = None, extra: dict[str, Any] | None = None) -> None:  # fmt: skip
        super().__init__(message)
        self.code, self.message, self.hint, self.retry = code, message, hint, retry
        self.events = events  # [[event, data], …] of what was done before the failure: the scheduler publishes them
        # More keys of the stored error the user's window reads (e.g. `failed` of an import with `choices`, Э27 27.12)
        self.extra = extra or {}


class JobContext:
    def __init__(
        self,
        job: dict[str, Any],
        send: Callable[[tuple[Any, ...]], None],
        cancelled: threading.Event,
    ) -> None:
        self.job = job
        self.id: int = job["id"]
        self.params: dict[str, Any] = job.get("params") or {}
        self.secret: str | None = job.get("secret")
        self.project_folder: Path | None = Path(job["project_folder"]) if job.get("project_folder") else None
        self.instance_db: str | None = job.get("instance_db")
        self._send = send
        self._cancelled = cancelled
        self._last_sent = 0.0
        self._last_message: str | None = None
        self._engine: sa.Engine | None = None

    # --- progress, log, cancellation ----------------------------------------------------------

    def tick(self, progress: float | None = None, message: str | None = None) -> None:
        """Call at least once a second (ТЗ 6.7). Raises JobCancelled after a cancel request."""
        if self._cancelled.is_set():
            raise JobCancelled()
        now = time.monotonic()
        # At most 5 messages a second; a new text goes at once unless texts change faster than that.
        new_text = message is not None and message != self._last_message
        if now - self._last_sent >= 0.2 or (new_text and now - self._last_sent >= 0.05):
            self._last_sent = now
            if message is not None:
                self._last_message = message
            p = None if progress is None else max(0.0, min(1.0, float(progress)))
            self._send(("progress", self.id, p, message))

    def log(self, text: str, level: str = "info") -> None:
        self._send(("log", self.id, level, str(text)))

    @property
    def cancel_requested(self) -> bool:
        return self._cancelled.is_set()

    # --- databases ------------------------------------------------------------------------------

    def engine(self) -> sa.Engine:
        """The project database of this job (the worker's own engine; short write transactions only)."""
        if self._engine is None:
            from bf_core.db import dialect

            if self.project_folder is None:
                raise RuntimeError("instance job has no project database")
            self._engine = dialect.create_engine(dialect.sqlite_url(str(self.project_folder / "project.sqlite")))
        return self._engine

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
