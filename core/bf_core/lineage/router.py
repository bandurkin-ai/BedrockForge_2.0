"""/api/v1/lineage — the states of the computed objects, «Влияние изменения», «Происхождение», «Закрепить»,
«Перестроить с зависимыми» (ADR 0025 §5, §7–10; подшаг 18.Г2).

A node is `{kind, id, sub?}`: kind — `dh_set`, `dh_table`, `dh_result`, `wireframe`, `bm_field` (id — the model, sub —
the casefolded name of the field) or `object:<kind of the object>`; an unknown kind — 422 `lineage_bad_kind`.

  * `POST /status {nodes: [node]}` → {items: [{kind, id, sub, state, name, reason, changed}]} in the order asked
    (≤ 5 000 nodes; one section of the tree). `state`: `current` | `stale` | `source_lost` | `pinned` (`changed` —
    the inputs changed since) | `building` | `deleted` (no such node: not an error here).
  * `GET /impact?kind=&id=&sub=` and `POST /impact {kind, id, sub?}` (or `{node}`) → {total, by_kind: {kind: n},
    nodes: [{kind, id, sub, name, depth}] (≤ 2 000, nearest first), truncated}. 404 `lineage_node_not_found`. The
    first call in a project made before the graph writes its index (`reindex`; not in a read-only project).
  * `GET /origin?kind=&id=&sub=` → {node: <status>, inputs: [{kind, id, sub, name, fp, fp_now, state, reason}]}.
  * `POST /pin {node | kind+id+sub, pinned}` → the status of the node; event `lineage.changed {nodes}`; 404; 423.
  * `POST /rebuild {node, with_dependents?: true, drop_lost?: false, dry_run?: false}` → 202 {job_id, source, id,
    plan: [{kind, id, sub, name, state, reason, action: rebuild | covered, owner, deps, covered_by}], skipped: [{kind,
    id, sub, name, state: source_lost | unsupported, reason}]} of the job `lineage.rebuild` (heavy); `dry_run` —
    200 {plan, skipped} and nothing is started. The node asked for is rebuilt always; with `with_dependents` — also
    what is built from it and is «устарел», in an order of building. 404; 409 `lineage_node_building`,
    `lineage_node_pinned`, and the refusals of the module of the first node (`dependents_lost` {lost} — repeat with
    `drop_lost`; `source_lost`); 422 `lineage_rebuild_unsupported`; 423.
Plain `def` routes run in the thread pool.
"""

from __future__ import annotations

import threading
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from . import graph, rebuild, store
from .store import Node

router = APIRouter(prefix="/lineage")
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Payload = Annotated[Any, Body()]
MAX_NODES = 5000
EVENT = "lineage.changed"
_indexed: set[str] = set()  # projects whose index was made from what the results keep (once per project)
_lock = threading.Lock()


def _bad(param: str, message: str, hint: str = "Повторите действие из дерева проекта.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


def _node(raw: Any, param: str = "node") -> Node:
    if not isinstance(raw, dict):
        raise _bad(param, "Объект задан неверно.")
    kind, ident, sub = raw.get("kind"), raw.get("id"), raw.get("sub") or ""
    if not isinstance(kind, str) or not kind:
        raise _bad(f"{param}.kind", "Не задан вид объекта.")
    if isinstance(ident, bool) or not isinstance(ident, int) or ident < 0:
        raise _bad(f"{param}.id", "Номер объекта — целое число.")
    if not isinstance(sub, str):
        raise _bad(f"{param}.sub", "Поле объекта задано неверно.")
    return Node(kind, ident, sub.casefold() if kind == "bm_field" else sub)


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise _bad("body", "Неверные данные запроса.")
    return raw


def _of_body(raw: dict[str, Any]) -> Node:
    """`{node: {...}}` or the fields of the node at the top."""
    return _node(raw["node"]) if "node" in raw else _node(raw, "body")


def _flag(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise _bad(key, "Параметр — «да» или «нет».")
    return value


def _blobs(project: Any) -> Any:
    from bf_core.storage.blobs import BlobStore

    return BlobStore(project.folder)


def _existing(conn: Any, node: Node) -> None:
    if graph.fingerprint(conn, node) is None:
        raise graph.not_found(node)


# --- states ----------------------------------------------------------------------------------------------------------


@router.post("/status")
def get_status(svc: Svc, body: Payload) -> dict[str, Any]:
    raw = _body(body).get("nodes")
    if not isinstance(raw, list):
        raise _bad("nodes", "Не задан список объектов.")
    if len(raw) > MAX_NODES:
        raise ApiError(422, "too_many_nodes", f"Слишком много объектов в одном запросе: больше {MAX_NODES}.",
                       "Запрашивайте состояния по разделам дерева.", {"param": "nodes"})  # fmt: skip
    nodes = [_node(r, f"nodes.{i}") for i, r in enumerate(raw)]
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        got = graph.status(conn, nodes, store=_blobs(project)) if nodes else {}
    return {"items": [got[n].as_dict() for n in nodes]}


def _impact(svc: ProjectService, node: Node) -> dict[str, Any]:
    project = svc.require_current()
    with _lock:
        fresh = project.uid not in _indexed
        _indexed.add(project.uid)
    if fresh and not project.read_only:
        with dialect.write_tx(project.engine) as conn:
            if store.count(conn) == 0:  # a project saved before the graph: the rows from what the results keep
                graph.reindex(conn)
    with dialect.read_tx(project.engine) as conn:
        _existing(conn, node)
        return graph.impact(conn, node)


@router.get("/impact")
def impact_get(svc: Svc, kind: Annotated[str, Query(min_length=1, max_length=60)], id: Annotated[int, Query(ge=0)],
               sub: Annotated[str, Query(max_length=200)] = "") -> dict[str, Any]:  # fmt: skip
    return _impact(svc, _node({"kind": kind, "id": id, "sub": sub}))


@router.post("/impact")
def impact_post(svc: Svc, body: Payload) -> dict[str, Any]:
    return _impact(svc, _of_body(_body(body)))


@router.get("/origin")
def origin(svc: Svc, kind: Annotated[str, Query(min_length=1, max_length=60)], id: Annotated[int, Query(ge=0)],
           sub: Annotated[str, Query(max_length=200)] = "") -> dict[str, Any]:  # fmt: skip
    node = _node({"kind": kind, "id": id, "sub": sub})
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        _existing(conn, node)
        blobs = _blobs(project)
        return {"node": graph.status(conn, [node], store=blobs)[node].as_dict(),
                "inputs": graph.origin(conn, node, store=blobs)}  # fmt: skip


@router.post("/pin")
def pin(svc: Svc, request: Request, body: Payload) -> dict[str, Any]:
    raw = _body(body)
    node, pinned = _of_body(raw), _flag(raw, "pinned", True)
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = graph.pin(conn, node, pinned)
    request.app.state.core.events.publish(EVENT, {"nodes": [node.as_dict()]})
    return out.as_dict()


# --- «Перестроить с зависимыми» --------------------------------------------------------------------------------------


def _memory(svc: Any, plan: rebuild.Plan, body: dict[str, Any], first: rebuild.Step) -> int:
    """The memory the job needs: the largest of its nodes (the first is known; the others as far as they can be
    prepared now — a node of a later step may need what an earlier step makes)."""
    from bf_core.jobs.registry import KINDS

    steps = [first]
    seen = {plan.items[0].owner}
    for it in plan.items[1:21]:
        if it.owner in seen:
            continue
        seen.add(it.owner)
        try:
            steps.append(rebuild.prepare_step(svc, it.owner, body))
        except ApiError:
            continue
    return max(int(KINDS[s.job_kind].estimate_memory(s.params)) for s in steps)


@router.post("/rebuild")
def rebuild_nodes(svc: Svc, s: Sched, body: Payload) -> JSONResponse:
    from bf_core.jobs.registry import KINDS

    raw = _body(body)
    node = _of_body(raw)
    with_dependents = _flag(raw, "with_dependents", True)
    body_for_modules = {"drop_lost": _flag(raw, "drop_lost", False)}
    dry_run = _flag(raw, "dry_run", False)
    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        plan = rebuild.make_plan(conn, node, with_dependents=with_dependents, blobs=_blobs(project))
    shown = {"plan": [i.as_dict() for i in plan.items], "skipped": plan.skipped}
    if dry_run:
        return JSONResponse(shown)
    from . import job

    if job.KIND not in KINDS:
        raise ApiError(422, "not_ready", "«Перестроить с зависимыми» не подключено в этой сборке.",
                       "Обновите программу.")  # fmt: skip
    first = rebuild.prepare_step(svc, plan.items[0].owner, body_for_modules)  # the refusals of the module, before a job
    params = {
        "items": [{"kind": i.node.kind, "id": i.node.id, "sub": i.node.sub, "name": i.name, "owner": i.owner.as_dict(),
                   "deps": i.deps, "covered_by": i.covered_by} for i in plan.items],
        "drop_lost": body_for_modules["drop_lost"],
        "memory_mb": _memory(svc, plan, body_for_modules, first),
    }  # fmt: skip
    name = plan.items[0].name
    many = sum(1 for i in plan.items if i.covered_by is None)
    title = f"Перестроить с зависимыми: «{name}» ({many})" if many > 1 else f"Перестроить: «{name}»"
    key, job_id = s.submit(job.KIND, params, title=title)
    return JSONResponse({"job_id": job_id, "source": key, "id": job_id, **shown}, status_code=202)
