"""The warm-up of the workers (10.11; `ops/warm.py`, `jobs/worker.py::_warm_kernels`): every operation of the registry
and every other step is in both orders and runs as its job without a failure, leaving no folder behind; the quick
worker does not start a step while a job of its own runs. With real `spawn` workers (`BF_NO_WARMUP` off): on a cold
cache of numba a quick job is taken at once while the workers compile, and once the warm-up is done the first
stitch takes ~0.1 s; after a restart of the core with that cache, the first stitch right after the start of the
workers — while they load the kernels — takes at most 3 s (the criterion of 10.11; was ~0.9 s from the cache and
~8–13 s cold)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bf_core.api.app import CoreState, create_app
from bf_core.ops import registry
from bf_core.ops import warm as ow
from bf_core.projects.service import projects_of
from tests.conftest import TOKEN
from tests.opsutil import API, make_project, ok
from tests.test_import_job import wait_job

FIRST_S = 3.0


def test_the_orders_cover_every_operation_and_every_step() -> None:
    assert {op for op, _params in ow.STEPS.values()} == set(registry.PREPARE)
    every = set(ow.STEPS) | set(ow.OTHER)
    for order in (ow.QUICK, ow.HEAVY):
        assert len(order) == len(set(order)) and set(order) == every
    assert ow.QUICK[:2] == ("checks", "stitch") and ow.HEAVY[0] == "preview"  # the two halves of a stitch at once


def test_every_step_runs_as_its_job_and_leaves_no_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         caplog: pytest.LogCaptureFixture) -> None:  # fmt: skip
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    old, new = tmp_path / "bf-warm-old", tmp_path / "bf-warm-new"  # left by a worker stopped in the middle
    for folder in (old, new):
        folder.mkdir()
        (folder / "project.sqlite").write_bytes(b"")
    os.utime(old, (time.time() - 2 * ow.STALE_S, time.time() - 2 * ow.STALE_S))
    calls: list[bool] = []

    def busy() -> bool:  # a job of the worker runs for the first five looks
        calls.append(len(calls) < 5)
        return calls[-1]

    took = ow.warm(ow.QUICK, busy=busy)
    assert "warm-up step" not in caplog.text, caplog.text
    assert list(took) == list(ow.QUICK)
    assert calls[:6] == [True] * 5 + [False] and len(calls) == 5 + len(ow.QUICK)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bf-warm-new"]  # the stale one swept, ours removed
    again = ow.warm(("stitch", "boolean"))  # warm now: the operations take milliseconds
    assert again["stitch"] < 1.0 and again["boolean"] < 1.0, again


def test_a_failed_step_is_logged_and_the_next_goes_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      caplog: pytest.LogCaptureFixture) -> None:  # fmt: skip
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    def broken(_folder: Any, _op: str, _params: Any, job_id: int = 1) -> Any:
        raise RuntimeError("ядро не собралось")

    monkeypatch.setattr(ow, "run_step", broken)
    took = ow.warm(("boolean", "checks"))
    assert list(took) == ["checks"] and "warm-up step boolean failed" in caplog.text
    assert not list(tmp_path.iterdir())


# --- real workers ---------------------------------------------------------------------------------------------


def _core(tmp_path: Path) -> TestClient:
    st = CoreState(token=TOKEN, projects_dir=str(tmp_path / "Проекты"), data_dir=str(tmp_path / "roaming"))
    c = TestClient(create_app(st), base_url="http://127.0.0.1:5555", headers={"X-BF-Token": TOKEN})
    c.core = st  # type: ignore[attr-defined]
    return c


def _warmed(log: Path, after: int, timeout: float = 300.0) -> str:
    """The line of the log of a worker that says its warm-up is done (the `after`-th such line counts)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lines = [x for x in log.read_text(encoding="utf-8").splitlines() if "warm-up: " in x] if log.exists() else []
        if len(lines) > after:
            return lines[after]
        time.sleep(0.2)
    raise AssertionError(f"no warm-up in {log}")


def _timed(c: TestClient, op: str, params: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    t0 = time.monotonic()
    started = ok(c.post(f"{API}/operations", json={"op": op, "params": params}))
    job = wait_job(c, started["source"], started["id"], timeout=120)
    assert job["status"] == "done", job
    return time.monotonic() - t0, job


@pytest.mark.slow
def test_the_first_operation_after_a_start_is_warm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BF_NO_WARMUP", raising=False)
    monkeypatch.setenv("BF_LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(tmp_path / "numba"))  # cold: the workers compile into it
    logs = tmp_path / "local" / "logs"
    stitch = None
    with _core(tmp_path) as c:
        t0 = time.monotonic()
        project, ids = make_project(c)
        stitch = {"contours": [{"layer_id": ids["contours"], "lines": [0, 1]}]}
        r = ok(c.post(f"{API}/jobs", json={"kind": "diag.quick", "params": {}}))
        assert wait_job(c, r["source"], r["id"], timeout=60)["status"] == "done"
        taken = time.monotonic() - t0
        assert taken < 30  # taken while the workers compile: the warm-up runs beside, not before
        assert "warm-up: " in _warmed(logs / "worker-quick.log", 0)
        cold, job = _timed(c, "stitch", stitch)
        assert job["result"]["volumes"][0] == pytest.approx(1000.0, rel=1e-9)
        assert cold <= FIRST_S, cold
        uid = project.uid
        _warmed(logs / "worker-heavy.log", 0)  # its half in the cache too, before the core stops
    with _core(tmp_path) as c:  # a restart: the cache is there
        assert c.post(f"{API}/projects/{uid}/open").status_code == 200
        assert projects_of(c.core).require_current().uid == uid
        r = ok(c.post(f"{API}/jobs", json={"kind": "diag.quick", "params": {}}))
        assert wait_job(c, r["source"], r["id"], timeout=60)["status"] == "done"  # the quick worker is up
        first, job = _timed(c, "stitch", stitch)  # while it loads its kernels from the cache
        assert job["result"]["volumes"][0] == pytest.approx(1000.0, rel=1e-9)
        assert first <= FIRST_S, first
        line = _warmed(logs / "worker-quick.log", 1)
        boolean, _job = _timed(c, "boolean", {"op": "difference", "a": ids["a"], "b": ids["b"]})
        assert boolean <= FIRST_S, boolean
    print(f"first stitch: cold cache after the warm-up {cold:.2f} s, after a restart {first:.2f} s; "
          f"boolean {boolean:.2f} s; quick job taken in {taken:.1f} s; {line.split('warm-up: ')[1]}")  # fmt: skip
