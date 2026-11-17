"""«Очистить выполненные» (ТЗ 7.35 Д-10, ADR 0008): POST /api/v1/jobs/clear."""

from __future__ import annotations

import time

import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.jobs import store
from bf_core.jobs.service import jobs_of

API = "/api/v1"


def _add(engine: sa.Engine, instance: bool, status: str) -> int:
    # queue «nobody»: no worker claims these rows
    jid = store.insert(
        engine,
        instance=instance,
        queue="nobody",
        kind="diag.sleep",
        title=status,
        params={},
        secret=None,
        max_attempts=3,
        memory_mb=0,
    )
    store.update(engine, instance, jid, status=status)
    store.add_log(engine, instance, jid, "info", f"строка {status}")
    return jid


def _statuses(engine: sa.Engine, instance: bool) -> list[str]:
    return sorted(r["status"] for r in store.list_jobs(engine, instance))


@pytest.fixture()
def jc(pclient):  # type: ignore[no-untyped-def]
    pclient.post(API + "/projects", json={"name": "Очистка"})
    return pclient


def test_clear_removes_only_finished_and_their_logs(jc) -> None:  # type: ignore[no-untyped-def]
    sched = jobs_of(jc.core)
    assert sched.project is not None
    for src in (sched.project, sched.instance):
        for st in ("done", "cancelled", "failed"):
            _add(src.engine, src.instance, st)
    queued = _add(sched.project.engine, False, "queued")
    running = _add(sched.project.engine, False, "running")

    r = jc.post(API + "/jobs/clear", json={})
    assert r.status_code == 200, r.text
    assert r.json() == {"removed": 4}
    assert _statuses(sched.instance.engine, True) == ["failed"]
    assert "failed" in _statuses(sched.project.engine, False)
    assert "done" not in _statuses(sched.project.engine, False)
    assert store.read_log(sched.project.engine, False, queued)  # untouched

    r = jc.post(API + "/jobs/clear", json={"include_failed": True})
    assert r.json() == {"removed": 2}
    left = _statuses(sched.project.engine, False)
    assert "failed" not in left and "done" not in left
    _, log = store.tables(False)
    with dialect.read_tx(sched.project.engine) as conn:
        ids = {row[0] for row in conn.execute(sa.select(log.c.job_id))}
    assert ids <= {queued, running}


def test_clear_without_project_clears_instance(pclient) -> None:  # type: ignore[no-untyped-def]
    sched = jobs_of(pclient.core)
    _add(sched.instance.engine, True, "done")
    assert pclient.post(API + "/jobs/clear", json={}).json() == {"removed": 1}
    assert pclient.post(API + "/jobs/clear", json={}).json() == {"removed": 0}


def test_clear_read_only_project_is_423(jc, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from bf_core.projects.service import projects_of

    project = projects_of(jc.core).require_current()
    monkeypatch.setattr(project, "read_only", True)
    r = jc.post(API + "/jobs/clear", json={})
    assert r.status_code == 423
    body = r.json()
    assert body["code"] == "project_read_only" and body["hint"]


def test_clear_10000_jobs_under_a_second(pclient) -> None:  # type: ignore[no-untyped-def]
    sched = jobs_of(pclient.core)
    t, log = store.tables(True)
    now = dialect.utcnow()
    rows = [
        dict(
            queue="nobody",
            kind="diag.sleep",
            title=str(i),
            status="done",
            priority=100,
            params={},
            secret=None,
            progress=1.0,
            cancel_requested=False,
            attempts=1,
            max_attempts=3,
            memory_mb=0,
            created_at=now,
        )
        for i in range(10_000)
    ]
    with dialect.write_tx(sched.instance.engine) as conn:
        conn.execute(sa.insert(t), rows)
        first = conn.execute(sa.select(sa.func.min(t.c.id))).scalar_one()
        conn.execute(
            sa.insert(log), [dict(job_id=first + i, seq=1, at=now, level="info", text="x") for i in range(10_000)]
        )
    t0 = time.perf_counter()
    assert pclient.post(API + "/jobs/clear", json={}).json()["removed"] >= 10_000
    assert time.perf_counter() - t0 < 1.0
