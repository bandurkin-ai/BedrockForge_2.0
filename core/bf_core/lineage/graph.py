"""The graph of dependencies of computed objects (ADR 0025 §5, §7, §8): states, the influence of a change, pins.

The state of a node is computed when it is read (pull), from the fingerprints of its inputs and recursively from the
states of the inputs; nothing is marked on a write:

  «current»      — every input exists, has the fingerprint it had when the node was built and is itself current
                   (or pinned), and the direct check of the module (§6) finds nothing;
  «stale»        — otherwise; `reason` is the first cause found along the chain («поле блочной модели «M.AU» ←
                   модель вариограммы «Au_D1» ← изменена таблица «assay»»);
  «source_lost»  — an input, or an input of an input, is deleted («источник удалён»), the name is in the reason;
  «pinned»       — the node is pinned (`pin`); `changed` tells whether it would be «stale» (the reason is kept);
                   for the nodes built from it it counts as current (№ 62);
  «building»     — the node is being written (`state = building`);
  «deleted»      — there is no such node (only in the answer to a question about it).

One call of `status` is a few queries per kind: the closure of the nodes is loaded by levels (inputs, then
fingerprints and names of all the nodes of a level by kind), then the states are folded with a memory of the call.
A cycle cannot appear (an input exists before the result), but is checked: `lineage_cycle`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError

from . import store
from .providers import Ctx, Info, label_of, provider_for
from .store import InputRef, Node

CURRENT, STALE, LOST, PINNED, BUILDING, DELETED = "current", "stale", "source_lost", "pinned", "building", "deleted"
MAX_LISTED = 2000  # nodes named in the answer of `impact` (the counts are of all)


@dataclass(frozen=True)
class NodeStatus:
    node: Node
    state: str
    name: str = ""
    reason: str | None = None
    changed: bool = False  # pinned: the inputs changed since

    def as_dict(self) -> dict[str, Any]:
        return {**self.node.as_dict(), "state": self.state, "name": self.name, "reason": self.reason,
                "changed": self.changed}  # fmt: skip


def cycle() -> ApiError:
    return ApiError(409, "lineage_cycle", "В графе зависимостей найден цикл.",
                    "Объект не может зависеть от самого себя: удалите связь или перестройте объект.")  # fmt: skip


def not_found(node: Node) -> ApiError:
    return ApiError(404, "lineage_node_not_found", "Объект не найден.", "Обновите дерево проекта.",
                    {"kind": node.kind, "id": node.id})  # fmt: skip


# --- loading ----------------------------------------------------------------------------------------------


def _lookup(ctx: Ctx, nodes: Iterable[Node]) -> dict[Node, Info]:
    by_kind: dict[str, list[Node]] = {}
    for n in nodes:
        by_kind.setdefault(n.kind, []).append(n)
    out: dict[Node, Info] = {}
    for kind, group in by_kind.items():
        got = provider_for(kind).lookup(ctx, [n.key() for n in group])
        out.update({Node(kind, k[0], k[1]): v for k, v in got.items()})
    return out


def _inputs(ctx: Ctx, nodes: Iterable[Node]) -> dict[Node, list[InputRef]]:
    """Inputs of existing nodes: what the module keeps itself (the provider), else the table `lineage`."""
    by_kind: dict[str, list[Node]] = {}
    for n in nodes:
        by_kind.setdefault(n.kind, []).append(n)
    out: dict[Node, list[InputRef]] = {}
    for kind, group in by_kind.items():
        got = provider_for(kind).inputs(ctx, [n.key() for n in group])
        for k, refs in got.items():
            out[Node(kind, k[0], k[1])] = refs
    rest = [n for n in dict.fromkeys(nodes) if n not in out]
    if rest:
        out.update(store.inputs_of(ctx.conn, rest))
    return out


class _Graph:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.info: dict[Node, Info | None] = {}
        self.edges: dict[Node, list[InputRef]] = {}
        self.pins: set[Node] = set()
        self.memo: dict[Node, NodeStatus] = {}
        self.stack: list[Node] = []

    def load(self, nodes: Iterable[Node]) -> None:
        level = list(dict.fromkeys(nodes))
        while level:
            found = _lookup(self.ctx, level)
            for n in level:
                self.info[n] = found.get(n)
            alive = [n for n in level if n in found]
            self.pins |= store.pins_of(self.ctx.conn, alive)
            self.edges.update(_inputs(self.ctx, alive))
            level = []
            seen = set(self.info)
            for n in alive:
                for ref in self.edges.get(n, ()):
                    if ref.node not in seen and ref.node.id:  # id 0: the source of an archive that is not in this one
                        seen.add(ref.node)
                        level.append(ref.node)
            for n in alive:
                for ref in self.edges.get(n, ()):
                    if not ref.node.id:
                        self.info.setdefault(ref.node, None)

    def resolve(self, node: Node) -> NodeStatus:
        done = self.memo.get(node)
        if done is not None:
            return done
        if node in self.stack:
            raise cycle()
        info = self.info.get(node)
        if info is None:
            return self.memo.setdefault(node, NodeStatus(node, DELETED))
        if info.building:
            return self.memo.setdefault(node, NodeStatus(node, BUILDING, info.name))
        self.stack.append(node)
        try:
            cause, lost = self._cause(node)
        finally:
            self.stack.pop()
        if node in self.pins:
            out = NodeStatus(node, PINNED, info.name, cause, changed=cause is not None)
        elif cause is None:
            out = NodeStatus(node, CURRENT, info.name)
        else:
            out = NodeStatus(node, LOST if lost else STALE, info.name, cause)
        self.memo[node] = out
        return out

    def _cause(self, node: Node) -> tuple[str | None, bool]:
        """The first cause on the way to the sources; a lost source outweighs a changed one."""
        cause, lost = None, False
        own = provider_for(node.kind).direct(self.ctx, node.key())
        if own is not None:
            cause, lost = own
        for ref in self.edges.get(node, ()):
            cur = self.info.get(ref.node) if ref.node.id else None
            label = label_of(ref.node.kind)
            if cur is None:
                found, gone = f"источник удалён: {label} «{ref.name}»", True
            elif ref.fp is not None and ref.fp != cur.fp:
                found, gone = f"изменено: {label} «{cur.name}»", False
            else:
                s = self.resolve(ref.node)
                if s.state not in (STALE, LOST):
                    continue
                found, gone = f"{label} «{cur.name}» ← {s.reason}", s.state == LOST
            if cause is None or (gone and not lost):
                cause, lost = found, gone
        return cause, lost


# --- the questions ----------------------------------------------------------------------------------------


def status(conn: sa.Connection, nodes: Sequence[Node], *, store: Any = None) -> dict[Node, NodeStatus]:
    """The states of `nodes` (ADR 0025 §5, §10: one batch for a section of the tree). `store` — the `BlobStore` of the
    project: with it the fields of a block model are also checked by their makers (calculator, density)."""
    g = _Graph(Ctx(conn, store))
    g.load(nodes)
    return {n: g.resolve(n) for n in dict.fromkeys(nodes)}


def fingerprint(conn: sa.Connection, node: Node) -> str | None:
    """The fingerprint of a node now; None — there is no such node."""
    got = _lookup(Ctx(conn), [node]).get(node)
    return None if got is None else got.fp


def impact(conn: sa.Connection, node: Node) -> dict[str, Any]:
    """«Влияние изменения» (§7): who is built from `node`, directly or through others, by the table `lineage`.
    {"total", "by_kind": {kind: n}, "nodes": [{kind, id, sub, name, depth}], "truncated"} — nearest first. A row of a
    node that does not exist any more is not counted."""
    ctx = Ctx(conn)
    depth: dict[Node, int] = {}
    order: list[Node] = []
    level, d = [node], 0
    while level:
        d += 1
        nxt: list[Node] = []
        for deps in store.dependents(conn, level).values():
            for dst in deps:
                if dst != node and dst not in depth:
                    depth[dst] = d
                    nxt.append(dst)
        names = _lookup(ctx, nxt)
        level = [n for n in nxt if n in names]
        order += level
        for n in nxt:
            if n not in names:
                depth.pop(n)
        ctx.cache.setdefault("names", {}).update({n: i.name for n, i in names.items()})
    names_by = ctx.cache.get("names", {})
    by_kind: dict[str, int] = {}
    for n in order:
        by_kind[n.kind] = by_kind.get(n.kind, 0) + 1
    listed = [{**n.as_dict(), "name": names_by.get(n, ""), "depth": depth[n]} for n in order[:MAX_LISTED]]
    return {"total": len(order), "by_kind": by_kind, "nodes": listed, "truncated": len(order) > MAX_LISTED}


def origin(conn: sa.Connection, node: Node, *, store: Any = None) -> list[dict[str, Any]]:
    """«Происхождение» (§10): the inputs of a node with the fingerprint they had and have now and their states."""
    ctx = Ctx(conn, store)
    g = _Graph(ctx)
    g.load([node])
    out = []
    for ref in g.edges.get(node, ()):
        cur = g.info.get(ref.node) if ref.node.id else None
        s = g.resolve(ref.node) if cur is not None else NodeStatus(ref.node, DELETED, ref.name)
        out.append({**ref.node.as_dict(), "name": cur.name if cur else ref.name, "fp": ref.fp,
                    "fp_now": None if cur is None else cur.fp, "state": s.state, "reason": s.reason})  # fmt: skip
    return out


def pin(conn: sa.Connection, node: Node, pinned: bool) -> NodeStatus:
    """«Закрепить» / «Снять» (§8): a pinned node is not «устарел». 404 `lineage_node_not_found`."""
    if fingerprint(conn, node) is None:
        raise not_found(node)
    store.set_pin(conn, node, pinned)
    return status(conn, [node])[node]


def record(conn: sa.Connection, dst: Node, inputs: Iterable[Node | InputRef]) -> list[InputRef]:
    """The inputs of a result — in the transaction that moves the result into place (§4: atomic with it).
    A `Node` gets its fingerprint and name as they are now; an `InputRef` is taken as given (the job read the input
    earlier). The rows of `dst` are replaced; a pin stays. 404 `lineage_node_not_found` — an input is gone."""
    given = list(inputs)
    bare = [i for i in given if isinstance(i, Node)]
    now = _lookup(Ctx(conn), bare) if bare else {}
    refs: list[InputRef] = []
    for i in given:
        if isinstance(i, InputRef):
            refs.append(i)
            continue
        got = now.get(i)
        if got is None:
            raise not_found(i)
        refs.append(InputRef(i, got.fp, got.name))
    store.write_inputs(conn, dst, refs)
    return refs


def sync(conn: sa.Connection, nodes: Sequence[Node]) -> int:
    """Writes the reverse index of nodes whose inputs the module keeps in its own form (results of sample
    processing, variogram models, reports…): the same rows `record` writes, from what `inputs` of the provider
    gives. Called where such an object is saved, and by `reindex`. Returns how many nodes got rows."""
    ctx = Ctx(conn)
    got = _inputs_derived(ctx, nodes)
    for n, refs in got.items():
        store.write_inputs(conn, n, refs)
    return len(got)


def _inputs_derived(ctx: Ctx, nodes: Sequence[Node]) -> dict[Node, list[InputRef]]:
    by_kind: dict[str, list[Node]] = {}
    for n in nodes:
        by_kind.setdefault(n.kind, []).append(n)
    out: dict[Node, list[InputRef]] = {}
    for kind, group in by_kind.items():
        for k, refs in provider_for(kind).inputs(ctx, [n.key() for n in group]).items():
            now = _lookup(ctx, [r.node for r in refs])
            out[Node(kind, k[0], k[1])] = [InputRef(r.node, now[r.node].fp if r.node in now else None, r.name)
                                           for r in refs]  # fmt: skip
    return out


def reindex(conn: sa.Connection) -> int:
    """The reverse index of a project saved before the graph (ADR 0025 §12: no migration of data): the rows of every
    result, model and report of the kinds that keep their inputs themselves. Safe to repeat."""
    from bf_core.db.schema import dh_results, objects

    nodes: list[Node] = [Node("dh_result", int(i)) for i in conn.execute(sa.select(dh_results.c.id)).scalars()]
    tables = conn.execute(sa.select(dh_results.c.table_id).where(dh_results.c.table_id.is_not(None)).distinct())
    nodes += [Node("dh_table", int(i)) for i in tables.scalars()]
    for kind in ("variogram_model", "qaqc_data", "qaqc_report", "block_model"):
        ids = conn.execute(sa.select(objects.c.id).where(objects.c.kind == kind)).scalars()
        nodes += [Node(f"object:{kind}", int(i)) for i in ids]
    return sync(conn, nodes)


def forget(conn: sa.Connection, node: Node) -> int:
    """A node was deleted: its own rows go (§4); those built from it keep theirs («источник удалён»)."""
    return store.forget(conn, node)


def forget_object(conn: sa.Connection, kind: str, object_id: int) -> None:
    """An object of `kind` (`objects.kind`) was deleted: its rows (and, for a block model, those of its fields) go."""
    store.forget_object(conn, kind, object_id)


def sweep(conn: sa.Connection) -> int:
    """Rows of nodes that do not exist any more (after a deletion that did not call `forget`, a rollback, an archive).
    Returns the nodes cleaned."""
    ctx = Ctx(conn)
    dst = store.all_dst(conn)
    alive = _lookup(ctx, dst)
    gone = [n for n in dst if n not in alive]
    for n in gone:
        store.forget(conn, n)
    return len(gone)
