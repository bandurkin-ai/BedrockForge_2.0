"""Server-sent events of the core (ADR 0002 §3): any thread publishes, each window reads one `fetch` stream."""

from __future__ import annotations

import asyncio
import itertools
import json
import threading
from collections import deque
from typing import Any


class EventBus:
    """Fan-out of small JSON events. Keeps the last 200 for `Last-Event-ID` resumption."""

    def __init__(self) -> None:
        self._subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[tuple[int, str, str]]]] = set()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._recent: deque[tuple[int, str, str]] = deque(maxlen=200)

    def publish(self, event: str, data: dict[str, Any]) -> None:
        item = (next(self._ids), event, json.dumps(data, ensure_ascii=False, default=str))
        with self._lock:
            self._recent.append(item)
            subs = list(self._subs)
        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:  # the loop is closed: the subscriber is gone
                pass

    def subscribe(self, last_id: int | None = None) -> tuple[asyncio.Queue[tuple[int, str, str]], Any]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[int, str, str]] = asyncio.Queue(maxsize=10_000)
        entry = (loop, queue)
        with self._lock:
            self._subs.add(entry)
            missed = [x for x in self._recent if last_id is not None and x[0] > last_id]
        for item in missed:
            queue.put_nowait(item)

        def unsubscribe() -> None:
            with self._lock:
                self._subs.discard(entry)

        return queue, unsubscribe


def format_sse(item: tuple[int, str, str]) -> bytes:
    ev_id, event, data = item
    return f"id: {ev_id}\nevent: {event}\ndata: {data}\n\n".encode()
