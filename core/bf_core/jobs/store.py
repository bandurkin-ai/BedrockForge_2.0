"""Job rows (ТЗ 6.7, ADR 0002 §4): insert, status changes, log, recovery after a restart, partial results."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, instance_job_log, instance_jobs, job_log, jobs, objects

ACTIVE = ("queued", "running")
FINAL = ("done", "failed", "cancelled")


def tables(instance: bool) -> tuple[sa.Table, sa.Table]:
    return (instance_jobs, instance_job_log) if instance else (jobs, job_log)


def insert(
    engine: sa.Engine,
    *,
    instance: bool,
    queue: str,
    kind: str,
    title: str,
    params: dict[str, Any],
    secret: str | None,
    max_attempts: int,
    memory_mb: int,
    priority: int = 100,
    project_uid: str | None = None,
) -> int:
    t, _ = tables(instance)
    with dialect.write_tx(engine) as conn:
        res = conn.execute(
            sa.insert(t).values(
                queue=queue,
                kind=kind,
                title=title,
                status="queued",
                priority=priority,
                params=params,
                secret=secret,
                progress=0.0,
                cancel_requested=False,
                attempts=0,
                max_attempts=max_attempts,
                memory_mb=memory_mb,
                project_uid=project_uid,
                created_at=dialect.utcnow(),
            )
        )
        key = res.inserted_primary_key
        assert key is not None
        return int(key[0])


def get(engine: sa.Engine, instance: bool, job_id: int) -> dict[str, Any] | None:
    t, _ = tables(instance)
    with dialect.read_tx(engine) as conn:
        row = conn.execute(sa.select(t).where(t.c.id == job_id)).mappings().first()
    return public(dict(row)) if row else None


def public(row: dict[str, Any]) -> dict[str, Any]:
    """A job as the API shows it: never the secret."""
    row = dict(row)
    row.pop("secret", None)
    for k in ("created_at", "started_at", "heartbeat_at", "finished_at"):
        if row.get(k) is not None:
            row[k] = row[k].isoformat()
    return row


def list_jobs(engine: sa.Engine, instance: bool, limit: int = 200) -> list[dict[str, Any]]:
    t, _ = tables(instance)
    with dialect.read_tx(engine) as conn:
        rows = conn.execute(sa.select(t).order_by(t.c.id.desc()).limit(limit)).mappings().all()
    return [public(dict(r)) for r in rows]


def update(engine: sa.Engine, instance: bool, job_id: int, **values: Any) -> None:
    t, _ = tables(instance)
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.update(t).where(t.c.id == job_id).values(**values))


def add_log(engine: sa.Engine, instance: bool, job_id: int, level: str, text: str) -> None:
    _, log = tables(instance)
    with dialect.write_tx(engine) as conn:
        seq = conn.execute(
            sa.select(sa.func.coalesce(sa.func.max(log.c.seq), 0)).where(log.c.job_id == job_id)
        ).scalar_one()
        conn.execute(
            sa.insert(log).values(job_id=job_id, seq=int(seq) + 1, at=dialect.utcnow(), level=level, text=text[:20_000])
        )


def read_log(engine: sa.Engine, instance: bool, job_id: int) -> list[dict[str, Any]]:
    _, log = tables(instance)
    with dialect.read_tx(engine) as conn:
        rows = conn.execute(sa.select(log).where(log.c.job_id == job_id).order_by(log.c.seq)).mappings().all()
    return [{"seq": r["seq"], "at": r["at"].isoformat(), "level": r["level"], "text": r["text"]} for r in rows]


def drop_partial(conn: sa.Connection, job_id: int) -> int:
    """Objects a job was building (state='building') and their file references: the job left nothing."""
    from bf_core.drillholes.store import drop_building

    drop_building(conn, job_id)  # tables an import was filling in existing sets
    ids = [
        r[0]
        for r in conn.execute(sa.select(objects.c.id).where(objects.c.job_id == job_id, objects.c.state == "building"))
    ]
    if ids:
        from bf_core.drillholes.store import drop_import_owners

        drop_import_owners(conn, ids)  # import_log goes by cascade: its source files must not stay owned
        conn.execute(
            sa.delete(blob_owners).where(blob_owners.c.owner_kind == "object", blob_owners.c.owner_id.in_(ids))
        )
        conn.execute(sa.delete(objects).where(objects.c.id.in_(ids)))
    return len(ids)


def finish_cleanup(engine: sa.Engine, instance: bool, job_id: int) -> None:
    if not instance:
        with dialect.write_tx(engine) as conn:
            drop_partial(conn, job_id)


def recover(engine: sa.Engine, instance: bool) -> list[int]:
    """After a core restart (ADR 0002 §4): running → queued while attempts remain, else failed;
    objects of interrupted jobs are dropped. Returns the ids that went back to the queue."""
    t, _ = tables(instance)
    back: list[int] = []
    now = dialect.utcnow()
    with dialect.write_tx(engine) as conn:
        rows = conn.execute(sa.select(t.c.id, t.c.attempts, t.c.max_attempts).where(t.c.status == "running")).all()
        for job_id, attempts, max_attempts in rows:
            if not instance:
                drop_partial(conn, job_id)
            if attempts < max_attempts:
                conn.execute(
                    sa.update(t)
                    .where(t.c.id == job_id)
                    .values(status="queued", worker=None, message="Возвращено в очередь")
                )
                back.append(job_id)
            else:
                conn.execute(
                    sa.update(t)
                    .where(t.c.id == job_id)
                    .values(status="failed", finished_at=now, error=interrupted_error(attempts))
                )
        if not instance:
            # building objects whose job is no longer running (cancelled or failed before cleanup)
            stale = (
                conn.execute(sa.select(objects.c.job_id).where(objects.c.state == "building").distinct())
                .scalars()
                .all()
            )
            for job_id in stale:
                if job_id is not None and job_id not in back:
                    drop_partial(conn, job_id)
    return back


def interrupted_error(attempts: int) -> dict[str, str]:
    return {
        "code": "worker_lost",
        "message": f"Исполнитель задания прерывался {attempts} раз.",
        "hint": "Повторите задание; если ошибка повторяется — скопируйте журнал и отправьте разработчику.",
    }
