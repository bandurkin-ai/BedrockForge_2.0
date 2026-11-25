"""A job and a maker for the tests of `lineage.rebuild` (18.Г2): objects of the kind `stub` built from the objects they
read. Registered in the process of the test and, through `BF_EXTRA_JOB_MODULES`, in the worker.

`meta`: `value` (what the object computes: 2·Σ(values of the inputs) + 1, a source keeps its own), `builds` (how many
times the job ran for it), `hold` (the job waits for a cancel), `started` (the job began), `fail` (the job fails)."""

from __future__ import annotations

import time
from typing import Any

import sqlalchemy as sa

from bf_core import lineage
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler
from bf_core.lineage import rebuild
from bf_core.lineage.store import Node

KIND = "stub.rebuild"
NODE = "object:stub"


def compute(values: list[float]) -> float:
    return 2.0 * sum(values) + 1.0


def _read(conn: sa.Connection, object_id: int) -> Any:
    q = sa.select(objects.c.name, objects.c.meta, objects.c.version).where(objects.c.id == object_id)
    return conn.execute(q).one()


def _patch(conn: sa.Connection, object_id: int, **meta: Any) -> None:
    row = _read(conn, object_id)
    conn.execute(
        sa.update(objects).where(objects.c.id == object_id).values(meta={**row.meta, **meta}, version=row.version + 1)
    )


@handler(KIND, "Заглушка перестроения", queue="heavy", max_attempts=1)
def stub_job(ctx: JobContext) -> dict[str, Any]:
    node = Node(NODE, int(ctx.params["object_id"]))
    with dialect.write_tx(ctx.engine()) as conn:
        _patch(conn, node.id, started=True)
        meta = _read(conn, node.id).meta
    ctx.tick(0.1, "Старт")
    if meta.get("hold"):
        for _ in range(600):
            ctx.tick(0.2, "Ждёт отмены")
            time.sleep(0.05)
    if meta.get("fail"):
        raise JobError("stub_failed", "Заглушка не смогла посчитать.", "Это тест.")
    with dialect.write_tx(ctx.engine()) as conn:
        refs = lineage.origin(conn, node)
        values = []
        for r in refs:
            m = _read(conn, int(r["id"])).meta
            values.append(float(m["value"]))
        _patch(conn, node.id, value=compute(values), builds=int(meta.get("builds", 0)) + 1)
        lineage.record(conn, node, [Node(NODE, int(r["id"])) for r in refs])
    return {"object_id": node.id, "events": [["objects.changed", {"kind": "stub", "id": node.id}]]}


def _prepare(svc: Any, cap: rebuild.Capture, node: Node, _body: dict[str, Any]) -> None:
    cap.submit(KIND, {"object_id": node.id}, title=f"Заглушка № {node.id}")


rebuild.register(NODE, lambda _conn, node: node, _prepare)
