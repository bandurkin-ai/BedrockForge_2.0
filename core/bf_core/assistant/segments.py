"""The segment of a turn: the buffer of its events, the catch-up and the registry (подшаг 23.10; ADR 0029 решения
21, 24; docs/progress/e23-api.md §8-§10).

A segment is one run of the loop of a dialog (`loop.py`): from a question (`POST /turns`) or from the results of the
window (`POST /dialogs/{id}/tool_results`) to the end of the answer or to the commands the window must run. The loop
works in a thread of its own and puts its events here; a reader (the answer of the POST, or `GET …/stream?after=<n>`
after a reload of the window) takes them from the buffer. The core finishes a segment even when nobody reads it.

    seg = SEGMENTS.start(dialog_id)        one running segment per dialog (409 `assistant_busy` otherwise)
    seg.emit("text.delta", {...})          event number n = 1, 2, 3 … of the segment; ≤ 2000 are kept (the oldest go)
    for ev in seg.iter(after=0):           `Event(id, event, data)`; `None` — nothing for `keepalive` seconds (the route
        ...                                writes `: keepalive`); the iteration ends after the last event of a finished
                                           segment; it never raises
    seg.cancel()                           the flag of `POST …/cancel`; the loop closes the stream of the API
    SEGMENTS.get(dialog_id)                the running segment or None (`GET …/stream`: None — 204)
    SEGMENTS.ensure_room()                 at most `MAX_RUNNING` (8) segments at once, else 409 `assistant_busy`
    SEGMENTS.busy()                        ids of the dialogs with a running segment (`state: "running"` of the heads)

A finished segment leaves the registry at once: a reader that comes later finds the dialog already written (a second
replay of its events would show the same items twice). A reader that asks `after` older than the first kept event gets
the events from the first kept one (the window is told by the gap in the numbers).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from .errors import refuse

log = logging.getLogger("bf_core.assistant")

BUFFER_EVENTS = 2000  # решение 24
KEEPALIVE_S = 15.0  # §1: `: keepalive` every 15 s
MAX_RUNNING = 8  # turns at once: each holds a thread of the loop and a stream of the API
TOO_MANY = "Слишком много ответов одновременно."
TOO_MANY_HINT = "Дождитесь окончания одного из ответов или остановите лишний кнопкой «Стоп»."


@dataclass(frozen=True)
class Event:
    id: int  # the number in the segment, from 1
    event: str
    data: dict[str, Any]


class Segment:
    """The events of one run of the loop. Thread-safe: one writer (the loop), any number of readers."""

    def __init__(self, dialog_id: str, segment_id: str | None = None, limit: int = BUFFER_EVENTS) -> None:
        self.dialog_id = dialog_id
        self.id = segment_id or "seg-" + uuid.uuid4().hex[:12]
        self.limit = limit
        self._events: deque[Event] = deque(maxlen=limit)
        self._last = 0
        self._done = False
        self._cancel = threading.Event()
        self._closer: Callable[[], None] | None = None
        self._cond = threading.Condition()

    # --- writer ----------------------------------------------------------------------------------------------------

    def emit(self, event: str, data: dict[str, Any]) -> Event:
        """Adds an event (number = the previous + 1); wakes the readers. After `finish` nothing is added."""
        with self._cond:
            if self._done:
                return Event(self._last, event, data)
            self._last += 1
            ev = Event(self._last, event, data)
            self._events.append(ev)
            self._cond.notify_all()
            return ev

    def finish(self) -> None:
        """The loop is over: the readers get what is left and stop."""
        with self._cond:
            self._done = True
            self._closer = None
            self._cond.notify_all()

    # --- cancel ----------------------------------------------------------------------------------------------------

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        """`POST …/cancel`: raises the flag and closes the stream of the API that is being read (a slow server must not
        keep the user waiting). The loop sees the flag and ends the segment with `turn.end {state: "cancelled"}`."""
        self._cancel.set()
        with self._cond:
            closer = self._closer
        if closer is not None:
            try:
                closer()
            except Exception:  # noqa: BLE001 - closing a stream that already ended is not an error
                log.debug("assistant: closing the stream of the API failed", exc_info=True)

    def set_closer(self, closer: Callable[[], None] | None) -> None:
        """The loop gives the function that closes the stream being read (None - there is none)."""
        with self._cond:
            self._closer = closer
        if closer is not None and self._cancel.is_set():  # a cancel came before the stream was open
            try:
                closer()
            except Exception:  # noqa: BLE001
                log.debug("assistant: closing the stream of the API failed", exc_info=True)

    # --- readers ---------------------------------------------------------------------------------------------------

    @property
    def done(self) -> bool:
        return self._done

    @property
    def last_id(self) -> int:
        return self._last

    @property
    def first_id(self) -> int:
        """The number of the oldest event kept (0 - none yet)."""
        with self._cond:
            return self._events[0].id if self._events else 0

    def events_after(self, after: int = 0) -> list[Event]:
        """The kept events with a number above `after` (a snapshot; no waiting)."""
        with self._cond:
            return [e for e in self._events if e.id > after]

    def iter(self, after: int = 0, *, keepalive: float = KEEPALIVE_S) -> Iterator[Event | None]:
        """The events above `after` as they come; `None` every `keepalive` seconds of silence; ends after the last
        event of a finished segment. A reader that leaves (a closed connection) does not stop the segment."""
        cursor = max(int(after), 0)
        last_sent = time.monotonic()
        while True:
            with self._cond:
                batch = [e for e in self._events if e.id > cursor]
                if not batch and not self._done:
                    left = keepalive - (time.monotonic() - last_sent)
                    if left > 0:
                        self._cond.wait(left)
                        batch = [e for e in self._events if e.id > cursor]
                done = self._done
            if batch:
                for ev in batch:
                    cursor = ev.id
                    yield ev
                last_sent = time.monotonic()
            elif done:
                return
            elif time.monotonic() - last_sent >= keepalive:
                last_sent = time.monotonic()
                yield None

    def wait(self, timeout: float | None = None) -> bool:
        """Blocks until the segment is finished (tests, shutdown); True - it is."""
        with self._cond:
            return self._cond.wait_for(lambda: self._done, timeout)


class Segments:
    """The running segments, one per dialog."""

    def __init__(self) -> None:
        self._running: dict[str, Segment] = {}
        self._lock = threading.Lock()

    def start(self, dialog_id: str, segment_id: str | None = None, limit: int = BUFFER_EVENTS) -> Segment:
        """A new segment for the dialog; 409 `assistant_busy` when one is running."""
        with self._lock:
            if dialog_id in self._running:
                raise refuse("assistant_busy")
            self._room()
            seg = Segment(dialog_id, segment_id, limit)
            self._running[dialog_id] = seg
            return seg

    def _room(self) -> None:
        if len(self._running) >= MAX_RUNNING:
            raise refuse("assistant_busy", TOO_MANY, TOO_MANY_HINT)

    def ensure_room(self) -> None:
        """409 `assistant_busy` when `MAX_RUNNING` segments run (no new dialog is made for a turn that cannot start)."""
        with self._lock:
            self._room()

    def release(self, seg: Segment) -> None:
        """The loop is over (or could not begin): the dialog is free."""
        seg.finish()
        with self._lock:
            if self._running.get(seg.dialog_id) is seg:
                del self._running[seg.dialog_id]

    def rekey(self, seg: Segment, new_id: str) -> None:
        """A temporary dialog became a saved one while the segment ran (решение 31): the registry follows."""
        with self._lock:
            if self._running.get(seg.dialog_id) is seg:
                del self._running[seg.dialog_id]
                seg.dialog_id = new_id
                self._running[new_id] = seg

    def get(self, dialog_id: str) -> Segment | None:
        with self._lock:
            return self._running.get(dialog_id)

    def busy(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._running)

    def cancel(self, dialog_id: str) -> bool:
        """`POST …/cancel`: True - a segment was running and was asked to stop (no segment is not an error)."""
        seg = self.get(dialog_id)
        if seg is None:
            return False
        seg.cancel()
        return True

    def cancel_all(self) -> int:
        """The core goes down: every running segment is asked to stop."""
        with self._lock:
            running = list(self._running.values())
        for seg in running:
            seg.cancel()
        return len(running)


SEGMENTS = Segments()

__all__ = ["BUFFER_EVENTS", "KEEPALIVE_S", "MAX_RUNNING", "SEGMENTS", "Event", "Segment", "Segments"]
