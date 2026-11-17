"""«Очистить выполненные» (ТЗ 7.35 Д-10, ADR 0008 «Замеры»: 10 000 заданий ≤ 1 с): finished jobs and their logs
of a project removed in one transaction."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _engine(work: Path) -> Any:
    from bf_core.db import dialect

    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    return dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))


def prepare(params: dict[str, Any], work: Path) -> None:
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import job_log, jobs
    from bf_core.projects.project import create_folder

    n, lines = int(params["jobs"]), int(params["log_lines"])
    folder = create_folder(work, "Задания", {})
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")
    engine = _engine(work)
    now = dialect.utcnow()
    try:
        with dialect.write_tx(engine) as conn:
            status = ("done", "cancelled", "failed", "queued")
            conn.execute(
                sa.insert(jobs),
                [
                    {
                        "queue": "nobody",
                        "kind": "diag.sleep",
                        "title": f"№{i}",
                        "status": status[i % 4],
                        "priority": 100,
                        "params": {},
                        "progress": 1.0,
                        "cancel_requested": False,
                        "attempts": 1,
                        "max_attempts": 3,
                        "memory_mb": 0,
                        "created_at": now,
                    }
                    for i in range(n)
                ],
            )
            first = int(conn.execute(sa.select(sa.func.min(jobs.c.id))).scalar_one())
            conn.execute(
                sa.insert(job_log),
                [
                    {
                        "job_id": first + i,
                        "seq": k + 1,
                        "at": now,
                        "level": "info",
                        "text": "строка журнала",
                    }
                    for i in range(n)
                    for k in range(lines)
                ],
            )
    finally:
        engine.dispose()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.jobs.store import clear_finished

    engine = _engine(work)
    try:
        removed = clear_finished(engine, False, ("done", "cancelled"))
    finally:
        engine.dispose()
    return {"removed": removed}
