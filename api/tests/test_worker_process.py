"""The worker as a separate process (`python -m app.jobs.worker`, as docker-compose runs it)."""

import os
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.jobs import queue


def test_worker_started_as_a_module_knows_its_handlers(db_engine, db_client, tmp_path):
    """Run as "__main__", the module used its own empty HANDLERS while the handlers registered in the
    imported copy: every job failed with "Неизвестный тип задания"."""
    with Session(db_engine) as s:
        job = queue.enqueue(s, "storage.gc", {}, title="gc")
        s.commit()
        job_id = job.id
    env = {**os.environ, "DATABASE_URL": str(db_engine.url.render_as_string(hide_password=False)),
           "DATA_DIR": str(tmp_path / "data"), "JOBS_IN_API": "0"}
    proc = subprocess.Popen([sys.executable, "-m", "app.jobs.worker"], cwd=Path(__file__).resolve().parent.parent,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        status = error = None
        deadline = time.time() + 30
        while time.time() < deadline:
            with Session(db_engine) as s:
                j = s.get(queue.Job, job_id) if hasattr(queue, "Job") else None
                if j is None:
                    from app.models import Job

                    j = s.get(Job, job_id)
                status, error = j.status, j.error
            if status in ("done", "failed", "cancelled"):
                break
            time.sleep(0.3)
    finally:
        proc.terminate()
        out = proc.communicate(timeout=15)[0]
    assert status == "done", f"{status}: {error}\n{out}"
