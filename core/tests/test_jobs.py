"""Jobs with real `spawn` workers (ТЗ 6.7, 7.6; ADR 0002 §4)."""

from __future__ import annotations

import time
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.jobs import store
from bf_core.jobs.service import jobs_of
from bf_core.projects.service import projects_of

pytestmark = pytest.mark.windows  # spawn workers, pipes and kills behave differently on Windows
API = "/api/v1"
FINAL = ("done", "failed", "cancelled")


@pytest.fixture()
def jc(pclient):  # type: ignore[no-untyped-def]
    pclient.post(API + "/projects", json={"name": "Задания"})
    return pclient


def submit(c, kind: str, **params: Any) -> tuple[str, int]:  # type: ignore[no-untyped-def]
    r = c.post(API + "/jobs", json={"kind": kind, "params": params})
    assert r.status_code == 200, r.text
    return r.json()["source"], r.json()["id"]


def wait_for(c, key: str, job_id: int, statuses=FINAL, timeout: float = 30.0) -> dict:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = c.get(f"{API}/jobs/{key}/{job_id}").json()
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} stuck: {job}")


def test_job_runs_with_progress_and_log(jc) -> None:  # type: ignore[no-untyped-def]
    key, jid = submit(jc, "diag.sleep", steps=5, step_s=0.02)
    job = wait_for(jc, key, jid)
    assert job["status"] == "done" and job["progress"] == 1.0 and job["result"]["steps"] == 5
    assert job["attempts"] == 1 and "secret" not in job
    lines = jc.get(f"{API}/jobs/{key}/{jid}/log").json()["lines"]
    assert [x["text"] for x in lines] == ["готово"]


def test_quick_queue_does_not_wait_for_heavy(jc) -> None:  # type: ignore[no-untyped-def]
    key, heavy = submit(jc, "diag.sleep", steps=100, step_s=0.05)
    wait_for(jc, key, heavy, statuses=("running",))
    t0 = time.monotonic()
    _, quick = submit(jc, "diag.quick")
    assert wait_for(jc, key, quick, timeout=10)["status"] == "done"
    assert time.monotonic() - t0 < 4
    assert jc.get(f"{API}/jobs/{key}/{heavy}").json()["status"] == "running"
    jc.post(f"{API}/jobs/{key}/{heavy}/cancel")
    assert wait_for(jc, key, heavy)["status"] == "cancelled"


def test_cancel_queued_at_once_running_at_next_tick_drops_partial(jc) -> None:  # type: ignore[no-untyped-def]
    # steps of 0.5 s: the cancel answer is read long before the worker's next tick, also on a slow CI machine
    # (with 0.05 s the worker could finish cancelling before the answer, and «Отмена…» was already «Отменено»)
    key, first = submit(jc, "diag.sleep", steps=40, step_s=0.5)
    _, second = submit(jc, "diag.sleep", steps=1)
    wait_for(jc, key, first, statuses=("running",))
    # a partial result of the running job
    project = projects_of(jc.core).require_current()
    now = dialect.utcnow()
    with dialect.write_tx(project.engine) as conn:
        conn.execute(
            sa.insert(objects).values(
                kind="wireframe",
                name="частичный",
                name_key="частичный",
                sort_key=0.0,
                state="building",
                version=1,
                meta={},
                job_id=first,
                created_at=now,
                updated_at=now,
            )
        )
    r = jc.post(f"{API}/jobs/{key}/{second}/cancel").json()
    assert r["status"] == "cancelled"
    r = jc.post(f"{API}/jobs/{key}/{first}/cancel").json()
    assert r["message"] == "Отмена…"
    assert wait_for(jc, key, first)["status"] == "cancelled"
    with dialect.read_tx(project.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(objects)).scalar_one() == 0


def test_killed_worker_requeues_then_fails_after_three(jc) -> None:  # type: ignore[no-untyped-def]
    key, ok = submit(jc, "diag.sleep", steps=3, crash_at=1, crash_attempts=1)
    job = wait_for(jc, key, ok)
    assert job["status"] == "done" and job["attempts"] == 2
    _, bad = submit(jc, "diag.sleep", steps=3, crash_at=1)
    job = wait_for(jc, key, bad, timeout=60)
    assert job["status"] == "failed" and job["attempts"] == 3 and job["error"]["code"] == "worker_lost"


def test_error_is_reported_and_retry_makes_new_job(jc) -> None:  # type: ignore[no-untyped-def]
    key, jid = submit(jc, "diag.fail")
    job = wait_for(jc, key, jid)
    assert job["status"] == "failed" and job["error"]["message"] == "Проверочная ошибка." and job["attempts"] == 1
    new = jc.post(f"{API}/jobs/{key}/{jid}/retry").json()["id"]
    again = wait_for(jc, key, new)
    assert new != jid and again["status"] == "failed"
    # the same parameters and where they come from: an export replaces what the first job wrote
    assert again["params"]["retry_of"] == {"id": jid, "since": job["started_at"]} and job["started_at"]
    third = jc.post(f"{API}/jobs/{key}/{new}/retry").json()["id"]
    assert wait_for(jc, key, third)["params"]["retry_of"] == {"id": new, "since": job["started_at"]}


def test_hanging_job_is_killed_after_cancel_grace(jc, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import bf_core.jobs.scheduler as sch

    monkeypatch.setattr(sch, "CANCEL_GRACE_S", 1.0)
    key, jid = submit(jc, "diag.hang", seconds=60)
    wait_for(jc, key, jid, statuses=("running",))
    jc.post(f"{API}/jobs/{key}/{jid}/cancel")
    assert wait_for(jc, key, jid, timeout=15)["status"] == "cancelled"
    # the heavy worker is replaced and takes new jobs
    _, nxt = submit(jc, "diag.sleep", steps=1)
    assert wait_for(jc, key, nxt, timeout=20)["status"] == "done"


def test_no_worker_reason_and_restart(jc) -> None:  # type: ignore[no-untyped-def]
    sched = jobs_of(jc.core)
    with sched._lock:
        sched.stopped_reason = "test"
        for w in [w for w in sched.workers if w.queue == "heavy"]:
            sched._kill(w)
    key, jid = submit(jc, "diag.sleep", steps=1)
    listed = {j["id"]: j for j in jc.get(API + "/jobs").json()["jobs"] if j["source"] == key}
    assert listed[jid]["why_waiting"].startswith("Нет работающего исполнителя")
    jc.post(API + "/jobs/workers/restart")
    assert wait_for(jc, key, jid, timeout=20)["status"] == "done"


def test_why_waiting_names_running_job(jc) -> None:  # type: ignore[no-untyped-def]
    key, first = submit(jc, "diag.sleep", steps=100, step_s=0.05)
    _, second = submit(jc, "diag.sleep", steps=1)
    wait_for(jc, key, first, statuses=("running",))
    listed = {j["id"]: j for j in jc.get(API + "/jobs").json()["jobs"]}
    assert f"№{first}" in listed[second]["why_waiting"] and "выполняется 0 мин" in listed[second]["why_waiting"]
    jc.post(f"{API}/jobs/{key}/{first}/cancel")
    wait_for(jc, key, second)


def test_recover_after_core_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.db import migrate
    from bf_core.db.schema import jobs

    engine = dialect.create_engine(dialect.sqlite_url(str(tmp_path / "p.sqlite")))
    migrate.upgrade(engine, "project")
    ids = [
        store.insert(
            engine,
            instance=False,
            queue="heavy",
            kind="diag.sleep",
            title="t",
            params={},
            secret=None,
            max_attempts=3,
            memory_mb=1,
        )
        for _ in range(2)
    ]
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.update(jobs).where(jobs.c.id == ids[0]).values(status="running", attempts=1))
        conn.execute(sa.update(jobs).where(jobs.c.id == ids[1]).values(status="running", attempts=3))
    assert store.recover(engine, False) == [ids[0]]
    assert store.get(engine, False, ids[0])["status"] == "queued"  # type: ignore[index]
    assert store.get(engine, False, ids[1])["status"] == "failed"  # type: ignore[index]
    engine.dispose()


def test_secret_is_never_shown_and_cleared_on_claim(jc) -> None:  # type: ignore[no-untyped-def]
    sched = jobs_of(jc.core)
    key, jid = sched.submit("diag.sleep", {"steps": 1}, secret="пароль-123")
    wait_for(jc, key, jid)
    body = jc.get(API + "/jobs").text + jc.get(f"{API}/jobs/{key}/{jid}").text
    assert "пароль-123" not in body
    src = sched.source(key)
    t, _ = store.tables(False)
    with dialect.read_tx(src.engine) as conn:  # type: ignore[union-attr]
        assert conn.execute(sa.select(t.c.secret).where(t.c.id == jid)).scalar_one() is None


def test_retry_goes_to_the_project_of_the_job(jc) -> None:  # type: ignore[no-untyped-def]
    key, jid = submit(jc, "diag.fail")
    wait_for(jc, key, jid)
    other = jc.post(API + "/projects", json={"name": "Другой"}).json()
    r = jc.post(f"{API}/jobs/{key}/{jid}/retry")
    assert r.status_code == 409 and r.json()["code"] == "job_project_closed"
    first = [e for e in jc.get(API + "/projects").json()["projects"] if e["uid"] != other["uid"]][0]
    jc.post(f"{API}/projects/{first['uid']}/open")
    r = jc.post(f"{API}/jobs/{key}/{jid}/retry").json()
    assert r["source"] == key
