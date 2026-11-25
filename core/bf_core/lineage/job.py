"""The job `lineage.rebuild` — «Перестроить с зависимыми» (ADR 0025 §9; подшаг 18.Г2).

    {"items": [{kind, id, sub, name, owner: {kind, id, sub}, deps: [i], covered_by: i | null}], "drop_lost": bool,
     "memory_mb": int}

The items are the plan of `rebuild.make_plan` in a topological order. Node by node the job asks the module of the
node for its own job (`rebuild.prepare_step`: the parameters kept with the result, on the tables and models of now)
and runs the handler of that job in this process, with its own context (progress of the node inside the share of
the whole, the cancel of this job). A job of each node, not one job for all: the scheduler has one heavy worker by
default, and a job that waited for its own children there would wait for ever.

  * a cancel (`JobCancelled`, at any tick of any node) stops the rest: the nodes after it are not started; what was
    finished stays (each node is committed by its own job);
  * a node that fails: what is built from it (`deps`, transitively) is not started and stays «устарел»; the other
    nodes go on; at the end the job fails with the nodes named and the reason of each;
  * a node that a module replaced meanwhile (a column operation repeated on a new table has a new id) or one made
    by the job of an earlier node (`covered_by`: a table and its result) is not run again.

The result: {"nodes": [{kind, id, sub, name, outcome, message?, job_kind?, state_after?}], "done", "events"}; the
events of the node jobs travel in it (a worker does not publish), `lineage.changed {nodes}` is added.
"""

from __future__ import annotations

import traceback
from types import SimpleNamespace
from typing import Any

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.jobs.registry import KINDS, handler

from . import graph, rebuild
from .store import Node

KIND = "lineage.rebuild"
TITLE = "Перестроить с зависимыми"
DONE, FAILED, SKIPPED, COVERED, REPLACED = "done", "failed", "skipped", "covered", "replaced"


def memory_mb(params: dict[str, Any]) -> int:
    return max(256, int(params.get("memory_mb") or 512))


class NodeContext(JobContext):
    """The context a node job runs with: the job, the engine and the cancel of the whole; the progress of the
    node is a share of the progress of the whole."""

    def __init__(self, parent: JobContext, params: dict[str, Any], index: int, total: int, label: str) -> None:
        super().__init__({**parent.job, "params": params, "secret": None}, parent._send, parent._cancelled)  # noqa: SLF001
        self.parent, self.index, self.total, self.label = parent, index, total, label
        self._engine = parent.engine()

    def tick(self, progress: float | None = None, message: str | None = None) -> None:
        p = None if progress is None else (self.index + max(0.0, min(1.0, float(progress)))) / self.total
        self.parent.tick(p, f"{self.label}: {message}" if message else self.label)

    def close(self) -> None:
        """The engine is the one of the whole job: it closes with it."""
        self._engine = None


class _Svc:
    """What the «Перестроить» routes of the modules take of the project service: the open project."""

    def __init__(self, ctx: JobContext) -> None:
        folder = ctx.project_folder
        self.current = SimpleNamespace(engine=ctx.engine(), folder=folder, uid=f"job:{folder}", read_only=False)

    def require_current(self, writable: bool = False) -> Any:  # noqa: ARG002
        return self.current


def _item_node(it: dict[str, Any]) -> Node:
    return Node(str(it["kind"]), int(it["id"]), str(it.get("sub") or ""))


def _owner(it: dict[str, Any]) -> Node:
    o = it["owner"]
    return Node(str(o["kind"]), int(o["id"]), str(o.get("sub") or ""))


def _failure(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, JobError):
        return exc.code, exc.message
    if isinstance(exc, ApiError):
        return exc.code, exc.message
    return "internal", "Внутренняя ошибка ядра."


def run(ctx: JobContext, params: dict[str, Any] | None = None) -> dict[str, Any]:
    p = ctx.params if params is None else params
    items: list[dict[str, Any]] = list(p["items"])
    total = max(1, len(items))
    body = {"drop_lost": bool(p.get("drop_lost"))}
    svc = _Svc(ctx)
    outcome: list[str] = [""] * len(items)
    messages: dict[int, str] = {}
    kinds: dict[int, str] = {}
    events: list[list[Any]] = []
    ran: set[Node] = set()  # the owners whose job has run
    for i, it in enumerate(items):
        name = str(it.get("name") or f"№ {it['id']}")
        ctx.tick(i / total, f"{i + 1} из {len(items)}: «{name}»")
        cover = it.get("covered_by")
        if cover is not None:
            outcome[i] = SKIPPED if outcome[cover] in (FAILED, SKIPPED) else COVERED
            continue
        broken = [d for d in it.get("deps") or [] if outcome[d] in (FAILED, SKIPPED)]
        if broken:
            outcome[i] = SKIPPED
            messages[i] = f"не запущено: «{items[broken[0]].get('name')}» не перестроен"
            continue
        owner = _owner(it)
        with dialect.read_tx(ctx.engine()) as conn:
            gone = graph.fingerprint(conn, owner) is None
        if gone or owner in ran:  # replaced by a job of an earlier node, or already made by one
            outcome[i] = REPLACED if gone else COVERED
            continue
        try:
            step = rebuild.prepare_step(svc, owner, body)
            node_ctx = NodeContext(ctx, step.params, i, total, name)
            kinds[i] = step.job_kind
            result = KINDS[step.job_kind].run(node_ctx) or {}
        except JobCancelled:
            raise
        except (JobError, ApiError) as exc:
            code, message = _failure(exc)
            outcome[i], messages[i] = FAILED, message
            ctx.log(f"«{name}»: {message} ({code})", "error")
            continue
        except Exception as exc:  # noqa: BLE001 - one node must not take the others down without a word
            outcome[i], messages[i] = FAILED, _failure(exc)[1]
            ctx.log(f"«{name}»: {traceback.format_exc(limit=8)}", "error")
            continue
        ran.add(owner)
        outcome[i] = DONE
        events += [list(e) for e in result.get("events") or []]
    return _finish(ctx, items, outcome, messages, kinds, events)


def _finish(
    ctx: JobContext, items: list[dict[str, Any]], outcome: list[str], messages: dict[int, str], kinds: dict[int, str],
    events: list[list[Any]],
) -> dict[str, Any]:  # fmt: skip
    nodes = [_item_node(it) for it in items]
    with dialect.read_tx(ctx.engine()) as conn:
        after = graph.status(conn, nodes) if nodes else {}
    rows = []
    for i, (it, n) in enumerate(zip(items, nodes, strict=True)):
        s = after[n]
        row: dict[str, Any] = {**n.as_dict(), "name": it.get("name"), "outcome": outcome[i],
                               "state_after": "replaced" if s.state == graph.DELETED else s.state}  # fmt: skip
        if i in messages:
            row["message"] = messages[i]
        if i in kinds:
            row["job_kind"] = kinds[i]
        rows.append(row)
    events.append(["lineage.changed", {"nodes": [n.as_dict() for n in nodes]}])
    failed = [r for r in rows if r["outcome"] == FAILED]
    if failed:
        skipped = sum(1 for r in rows if r["outcome"] == SKIPPED)
        names = "; ".join(f"«{r['name']}» — {r['message']}" for r in failed)
        tail = f" Не запущено зависимых: {skipped}." if skipped else ""
        hint = "Исправьте причину и запустите «Перестроить с зависимыми» ещё раз: готовые объекты не пересчитаются."
        raise JobError("lineage_rebuild_failed", f"Не удалось перестроить: {names}.{tail}", hint)
    return {"nodes": rows, "done": sum(1 for r in rows if r["outcome"] == DONE), "events": events}


@handler(KIND, TITLE, queue="heavy", max_attempts=1, estimate_memory=memory_mb)
def rebuild_job(ctx: JobContext) -> dict[str, Any]:
    return run(ctx)


__all__ = ["KIND", "NodeContext", "rebuild_job", "run"]
