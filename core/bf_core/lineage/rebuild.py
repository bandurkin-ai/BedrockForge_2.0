"""«Перестроить с зависимыми»: the plan of a rebuild and the registry of the makers (ADR 0025 §9; подшаг 18.Г2).

A node that is built by a job of its own module is rebuilt by that job on the parameters the module keeps; the graph
does not know them. The registry says, per kind of node, which node a job makes it (`owner`: a table made by an
operation is made by its result; a field of a block model — by the model) and how the module prepares its job
(`prepare`). `prepare` is the «Перестроить» route of the module itself, called with a stand-in of the scheduler that
only takes what is submitted (`Capture`): the rules of the module — the current tables by name, `dependents_lost`,
the version of a model — stay in one place and are not written twice.

    plan = make_plan(conn, node, with_dependents=True)   # the nodes to rebuild in a topological order
    step = prepare_step(svc, owner, body)                 # (job kind, params, title) of one node, on the data of now

A kind without a maker is 422 `lineage_rebuild_unsupported` (the node asked for) or is left out of the plan with
a reason (a node that depends on it, and everything built from that one). New kinds (Э18: the field of an estimation,
Э19: the reports) are added by `register`.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import dh_results, objects

from . import graph, store
from .providers import Ctx, label_of
from .store import Node

STALE_ONLY = (graph.STALE,)


@dataclass(frozen=True)
class Step:
    """One job of a module: what to submit for a node."""

    job_kind: str
    params: dict[str, Any]
    title: str


class Capture:
    """Stands in for the scheduler in the «Перестроить» route of a module: keeps what it would submit."""

    def __init__(self) -> None:
        self.steps: list[Step] = []

    def submit(self, kind: str, params: dict[str, Any], *, title: str | None = None, **_: Any) -> tuple[str, int]:
        self.steps.append(Step(kind, dict(params), title or ""))
        return "capture", len(self.steps)


Owner = Callable[[sa.Connection, Node], Node | None]
Prepare = Callable[[Any, Capture, Node, dict[str, Any]], None]


@dataclass(frozen=True)
class Maker:
    owner: Owner
    prepare: Prepare


MAKERS: dict[str, Maker] = {}


def register(kind: str, owner: Owner, prepare: Prepare) -> None:
    MAKERS[kind] = Maker(owner, prepare)


def unsupported(node: Node, name: str = "") -> ApiError:
    what = f"«{name}»" if name else f"№ {node.id}"
    return ApiError(
        422, "lineage_rebuild_unsupported", f"Объект {what} нельзя перестроить автоматически.",
        "Перестройте его командой своего модуля или создайте заново.", {**node.as_dict(), "name": name},
    )  # fmt: skip


def busy(node: Node, name: str) -> ApiError:
    return ApiError(409, "lineage_node_building", f"Объект «{name}» сейчас считается.", "Дождитесь окончания задания.",
                    node.as_dict())  # fmt: skip


# --- the makers of the kinds -------------------------------------------------------------------------------------


def _self(_conn: sa.Connection, node: Node) -> Node | None:
    return node


def _table_owner(conn: sa.Connection, node: Node) -> Node | None:
    """A table made by an operation (`TABLE_OPS`) is rebuilt by its result; a table of the user — nothing makes it."""
    from bf_core.composites.results import TABLE_OPS

    rid = conn.execute(
        sa.select(dh_results.c.id).where(dh_results.c.table_id == node.id, dh_results.c.op.in_(sorted(TABLE_OPS)))
    ).scalar()
    return None if rid is None else Node("dh_result", int(rid))


def _field_owner(conn: sa.Connection, node: Node) -> Node | None:
    """A field written by a fill, a code or a regularization is rebuilt with its model (`REBUILD` of the blocks)."""
    from .adapt_blocks import MODEL_MAKERS

    meta = conn.execute(
        sa.select(objects.c.meta).where(objects.c.kind == "block_model", objects.c.id == node.id)
    ).scalar()
    for f in (meta or {}).get("fields") or []:
        if str(f.get("name", "")).casefold() == node.sub and (f.get("source") or {}).get("op") in MODEL_MAKERS:
            return Node("object:block_model", node.id)
    return None


def _result(svc: Any, cap: Capture, node: Node, body: dict[str, Any]) -> None:
    from bf_core.composites import routes_ore

    routes_ore.rebuild(svc, cap, node.id, body)  # type: ignore[arg-type]  # ore → prep → run, as the module routes


def _variogram(svc: Any, cap: Capture, node: Node, _body: dict[str, Any]) -> None:
    from bf_core.geostat.variogram import routes_model

    routes_model.rebuild_model(svc, cap, node.id)  # type: ignore[arg-type]


def _qaqc_data(svc: Any, cap: Capture, node: Node, _body: dict[str, Any]) -> None:
    from bf_core.qaqc import router

    router.refresh_data(svc, cap, node.id)  # type: ignore[arg-type]


def _qaqc_report(svc: Any, cap: Capture, node: Node, _body: dict[str, Any]) -> None:
    from bf_core.qaqc import routes_reports

    routes_reports.rebuild_report(svc, cap, node.id)  # type: ignore[arg-type]


def _block_model(svc: Any, cap: Capture, node: Node, _body: dict[str, Any]) -> None:
    from bf_core.blocks import routes_ops  # noqa: F401  # puts the maker of a regularization into REBUILD
    from bf_core.blocks.fill import routes as fill_routes
    from bf_core.db import dialect

    with dialect.read_tx(svc.require_current().engine) as conn:
        version = conn.execute(
            sa.select(objects.c.version).where(objects.c.kind == "block_model", objects.c.id == node.id)
        ).scalar()
    if version is None:
        raise ApiError(404, "lineage_node_not_found", "Объект не найден.", "Обновите дерево проекта.", node.as_dict())
    fill_routes.rebuild(svc, cap, node.id, {"expected_version": int(version)})  # type: ignore[arg-type]


register("dh_result", _self, _result)
register("dh_table", _table_owner, _result)
register("object:variogram_model", _self, _variogram)
register("object:qaqc_data", _self, _qaqc_data)
register("object:qaqc_report", _self, _qaqc_report)
register("object:block_model", _self, _block_model)
register("bm_field", _field_owner, _block_model)


def prepare_step(svc: Any, node: Node, body: dict[str, Any]) -> Step:
    """The job that makes `node` (an owner, not a table or a field), with the parameters of now. The errors are
    those of the «Перестроить» route of its module (404, 409 `dependents_lost`, `source_lost`…)."""
    maker = MAKERS.get(node.kind)
    if maker is None:
        raise unsupported(node)
    from bf_core.jobs.registry import KINDS

    cap = Capture()
    maker.prepare(svc, cap, node, body)
    if len(cap.steps) != 1:
        raise unsupported(node)
    step = cap.steps[0]
    if step.job_kind not in KINDS:
        raise ApiError(422, "not_ready", "Перестроение этого вида не подключено в этой сборке.", "Обновите программу.")
    return step


# --- the plan ----------------------------------------------------------------------------------------------------


@dataclass
class Item:
    node: Node
    name: str
    state: str
    reason: str | None
    owner: Node
    deps: list[int] = field(default_factory=list)  # indexes of the items this one is built from
    covered_by: int | None = None  # the item whose job makes this node too (a table and its result)

    def as_dict(self) -> dict[str, Any]:
        return {**self.node.as_dict(), "name": self.name, "state": self.state, "reason": self.reason,
                "action": "covered" if self.covered_by is not None else "rebuild",
                "owner": self.owner.as_dict(), "deps": self.deps, "covered_by": self.covered_by}  # fmt: skip


@dataclass
class Plan:
    items: list[Item]
    skipped: list[dict[str, Any]]


def _skip(node: Node, name: str, reason: str, why: str) -> dict[str, Any]:
    return {**node.as_dict(), "name": name, "state": reason, "reason": why}


def _dependents_order(conn: sa.Connection, head: Node) -> list[Node]:
    """Everything built from `head`, directly or through others, nearest first (as `impact`)."""
    seen: set[Node] = {head}
    order: list[Node] = []
    queue: deque[list[Node]] = deque([[head]])
    while queue:
        level = queue.popleft()
        nxt: list[Node] = []
        for deps in store.dependents(conn, level).values():
            for dst in deps:
                if dst not in seen:
                    seen.add(dst)
                    nxt.append(dst)
        if nxt:
            alive = graph._lookup(Ctx(conn), nxt)  # noqa: SLF001
            nxt = [n for n in nxt if n in alive]
            order += nxt
            queue.append(nxt)
    return order


def make_plan(conn: sa.Connection, head: Node, *, with_dependents: bool = True, blobs: Any = None) -> Plan:
    """The nodes to rebuild: `head` (always: it was asked for) and, with `with_dependents`, what is built from it
    and is «устарел» now, in a topological order (a node after those it is built from). Pinned, being built and
    up-to-date nodes are left alone. «Источник удалён» and the kinds without a maker are listed in `skipped`
    with everything built from them. Errors: 404 `lineage_node_not_found`; 409 `lineage_node_building`,
    `lineage_node_pinned` (the head), `lineage_cycle`; 422 `lineage_rebuild_unsupported` (the head)."""
    ctx = Ctx(conn, blobs)
    have = graph._lookup(ctx, [head]).get(head)  # noqa: SLF001
    if have is None:
        raise graph.not_found(head)
    if have.building:
        raise busy(head, have.name)
    owner = _owner(conn, head)
    if owner is None:
        raise unsupported(head, have.name)
    cand: list[Node] = [head]
    skipped: list[dict[str, Any]] = []
    if with_dependents:
        later = _dependents_order(conn, head)
        states = graph.status(conn, later, store=blobs) if later else {}
        for n in later:
            s = states[n]
            if s.state == graph.LOST:
                skipped.append(_skip(n, s.name, s.state, s.reason or "источник удалён"))
            elif s.state == graph.STALE:
                cand.append(n)
    edges = graph._inputs(ctx, cand)  # noqa: SLF001
    owners: dict[Node, Node | None] = {n: (owner if n == head else _owner(conn, n)) for n in cand}
    names = graph._lookup(ctx, cand)  # noqa: SLF001
    states = graph.status(conn, cand, store=blobs)
    if states[head].state == graph.PINNED:
        raise ApiError(409, "lineage_node_pinned", f"Объект «{have.name}» закреплён.",
                       "Снимите закрепление, чтобы перестроить объект.", head.as_dict())  # fmt: skip
    blocked = _unsupported_closure(cand, owners, edges, names, skipped)
    order = _topological(cand, edges, blocked)
    index = {n: i for i, n in enumerate(order)}
    items: list[Item] = []
    first_by_owner: dict[Node, int] = {}
    for i, n in enumerate(order):
        o = owners[n]
        assert o is not None
        item = Item(n, names[n].name, states[n].state, states[n].reason, o)
        item.deps = sorted({index[r.node] for r in edges.get(n, ()) if r.node in index and index[r.node] < i})
        if o in first_by_owner:
            item.covered_by = first_by_owner[o]
        else:
            first_by_owner[o] = i
        items.append(item)
    return Plan(items, skipped)


def _owner(conn: sa.Connection, node: Node) -> Node | None:
    maker = MAKERS.get(node.kind)
    return None if maker is None else maker.owner(conn, node)


def _unsupported_closure(
    cand: list[Node], owners: dict[Node, Node | None], edges: dict[Node, list[Any]], names: dict[Node, Any],
    skipped: list[dict[str, Any]],
) -> set[Node]:  # fmt: skip
    """The nodes that cannot be rebuilt and, built from them, those that would be rebuilt on a stale input."""
    blocked: set[Node] = set()
    for n in cand[1:]:  # the head is checked by the caller
        if owners[n] is None:
            blocked.add(n)
            skipped.append(_skip(n, names[n].name, "unsupported",
                                 f"{label_of(n.kind)} «{names[n].name}» нельзя перестроить автоматически"))  # fmt: skip
    changed = True
    while changed:
        changed = False
        for n in cand[1:]:
            if n not in blocked and any(r.node in blocked for r in edges.get(n, ())):
                blocked.add(n)
                skipped.append(_skip(n, names[n].name, "unsupported",
                                     f"{label_of(n.kind)} «{names[n].name}» построен из объекта, который нельзя "
                                     "перестроить автоматически"))  # fmt: skip
                changed = True
    return blocked


def _topological(cand: list[Node], edges: dict[Node, list[Any]], blocked: set[Node]) -> list[Node]:
    """Kahn's order over the nodes to run (ties: the order of `cand`, nearest to the head first)."""
    nodes = [n for n in cand if n not in blocked]
    inside = set(nodes)
    waiting = {n: {r.node for r in edges.get(n, ()) if r.node in inside and r.node != n} for n in nodes}
    out: list[Node] = []
    done: set[Node] = set()
    while len(out) < len(nodes):
        ready = [n for n in nodes if n not in done and waiting[n] <= done]
        if not ready:
            raise graph.cycle()
        pick = ready[0]
        out.append(pick)
        done.add(pick)
    return out
