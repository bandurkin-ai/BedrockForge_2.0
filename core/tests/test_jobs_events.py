"""The events a job carries in its result (`result.events`) reach the bus for the jobs of every module (18.27б: the
scheduler published them only for composite., qaqc., variography. and lineage. kinds, so `estimate.*` and `blocks.*`
ended without `objects.changed` / `geostat.changed`)."""

from __future__ import annotations

import time
from typing import Any

from tests import estapi_util as U
from tests.estapi_util import API, GS, finish, make_row, ok
from tests.test_estimate_api import run_body

reg, ea = U.reg, U.ea  # the fixtures


def seen(c: Any, name: str, want: dict[str, Any], timeout: float = 5.0) -> bool:
    """The scheduler publishes after it wrote the end of the job: wait for the event a moment."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(n == name and d == want for n, d in c.events):
            return True
        time.sleep(0.02)
    return False


def test_estimate_run_events_reach_the_bus(ea) -> None:  # type: ignore[no-untyped-def]
    a = make_row(ea)["id"]
    ea.events.clear()
    job = finish(ea, ok(ea.post(f"{GS}/estimate/run", json=run_body(ea, [a]))))
    assert job["status"] == "done", job
    assert seen(ea, "geostat.changed", {"kind": "estimation", "id": a})
    assert seen(ea, "objects.changed", {"kind": "block_model", "id": ea.w.model})


def test_blocks_job_events_reach_the_bus(ea) -> None:  # type: ignore[no-untyped-def]
    ea.events.clear()
    body = {"size": 10, "extent": [0, 0, 0, 60, 50, 40], "levels": [1, 1, 1]}
    job = finish(ea, ok(ea.post(f"{API}/blocks/prototype", json=body)))
    assert job["status"] == "done", job
    assert seen(ea, "objects.changed", {"kind": "block_model", "id": job["result"]["model_id"]})
