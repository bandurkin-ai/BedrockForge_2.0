"""Which job workers are alive: each writes a small file in the shared data folder every few seconds.

The API reads them to tell the user why a job waits in the queue: no worker is running (a worker
process stopped — often killed for lack of memory on a big import), or the workers are busy with
the jobs ahead. Files, not a table: a worker that cannot reach the database still shows up.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from app.config import settings

BEAT_SECONDS = 5.0
# A worker not heard from for this long is taken as stopped.
ALIVE_SECONDS = 30.0


def beat_dir() -> Path:
    return Path(settings.data_dir) / "workers"


def _path(name: str) -> Path:
    return beat_dir() / (re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120] + ".json")


def write(name: str, kinds: list[str] | None, job_id: int | None, started: float) -> None:
    beat_dir().mkdir(parents=True, exist_ok=True)
    data = {"name": name, "kinds": kinds, "pid": os.getpid(), "job": job_id, "started": started, "seen": time.time()}
    tmp = _path(name).with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, _path(name))


def remove(name: str) -> None:
    try:
        _path(name).unlink()
    except FileNotFoundError:
        pass


def read_all(now: float | None = None) -> list[dict]:
    """Every worker heard from in the last hour: name, kinds (None: all), job it runs, seconds since
    it was last heard from, alive."""
    now = time.time() if now is None else now
    out = []
    folder = beat_dir()
    if not folder.is_dir():
        return out
    for p in folder.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        age = max(0.0, now - float(d.get("seen", 0)))
        if age > 3600:
            try:
                p.unlink()  # a worker gone long ago
            except OSError:
                pass
            continue
        out.append({"name": d.get("name"), "kinds": d.get("kinds"), "job": d.get("job"), "age": round(age, 1),
                    "alive": age <= ALIVE_SECONDS, "up_seconds": round(now - float(d.get("started", now)))})
    return sorted(out, key=lambda w: str(w["name"]))


def takes(worker: dict, kind: str) -> bool:
    return worker["alive"] and (not worker["kinds"] or kind in worker["kinds"])
