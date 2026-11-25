"""The table `lineage` (ADR 0025 §4, §8, §11): edges «dst was built from src» and pin marks.

A row of role `in` is one input of `dst`: the source node, its fingerprint at the time of the build (`src_fp`,
NULL — not compared) and its name (the reason after a deletion names it). A row of role `pin` marks `dst` as
pinned (§8; no source). Rows have no foreign keys: a deleted source leaves its row (that is how «источник
удалён» is told); a deleted node takes its own rows with it (`forget`). Nodes are `(kind, id, sub)`.

Everything here is one table, read in batches: `inputs_of` and `dependents` take many nodes of a kind in one
query (`IN` of ≤ 500 ids), so the state of a tree section is a handful of queries, not one per node.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

import sqlalchemy as sa

from bf_core.db.schema import lineage

IN, PIN = "in", "pin"
CHUNK = 500


class Node(NamedTuple):
    """A vertex of the graph: the kind, the id (of the `objects`, `dh_tables` or `dh_results` row) and, for a field
    of a block model (`bm_field`: id — the model), the casefolded name of the field."""

    kind: str
    id: int
    sub: str = ""

    def key(self) -> tuple[int, str]:
        return self.id, self.sub

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "sub": self.sub}


class InputRef(NamedTuple):
    """One input of a node: the source, its fingerprint when the node was built (None — not compared) and name."""

    node: Node
    fp: str | None
    name: str


def _groups(nodes: Iterable[Node]) -> dict[str, list[Node]]:
    out: dict[str, list[Node]] = {}
    for n in dict.fromkeys(nodes):
        out.setdefault(n.kind, []).append(n)
    return out


def _chunks(items: Sequence[Node]) -> Iterable[Sequence[Node]]:
    for i in range(0, len(items), CHUNK):
        yield items[i : i + CHUNK]


def inputs_of(conn: sa.Connection, nodes: Iterable[Node]) -> dict[Node, list[InputRef]]:
    """The stored inputs of the nodes, in the order they were recorded. A node with none is not in the answer."""
    out: dict[Node, list[InputRef]] = {}
    c = lineage.c
    for kind, group in _groups(nodes).items():
        for part in _chunks(group):
            wanted = {n.key() for n in part}
            q = (
                sa.select(c.dst_id, c.dst_sub, c.src_kind, c.src_id, c.src_sub, c.src_fp, c.src_name)
                .where(c.dst_kind == kind, c.role == IN, c.dst_id.in_([n.id for n in part]))
                .order_by(c.dst_id, c.dst_sub, c.pos)
            )
            for dst_id, dst_sub, src_kind, src_id, src_sub, src_fp, src_name in conn.execute(q).all():
                if (dst_id, dst_sub) in wanted:
                    out.setdefault(Node(kind, dst_id, dst_sub), []).append(
                        InputRef(Node(src_kind, src_id, src_sub), src_fp, src_name)
                    )
    return out


def pins_of(conn: sa.Connection, nodes: Iterable[Node]) -> set[Node]:
    """Which of the nodes are pinned."""
    out: set[Node] = set()
    c = lineage.c
    for kind, group in _groups(nodes).items():
        for part in _chunks(group):
            wanted = {n.key() for n in part}
            q = sa.select(c.dst_id, c.dst_sub).where(
                c.dst_kind == kind, c.role == PIN, c.dst_id.in_([n.id for n in part])
            )
            for r in conn.execute(q):
                if (int(r.dst_id), r.dst_sub) in wanted:
                    out.add(Node(kind, int(r.dst_id), r.dst_sub))
    return out


def dependents(conn: sa.Connection, nodes: Iterable[Node]) -> dict[Node, list[Node]]:
    """Who was built directly from each of the nodes (the reverse index): source → [dst]."""
    out: dict[Node, list[Node]] = {}
    c = lineage.c
    for kind, group in _groups(nodes).items():
        for part in _chunks(group):
            wanted = {n.key() for n in part}
            q = (
                sa.select(c.dst_kind, c.dst_id, c.dst_sub, c.src_id, c.src_sub)
                .where(c.src_kind == kind, c.role == IN, c.src_id.in_([n.id for n in part]))
                .order_by(c.dst_kind, c.dst_id, c.dst_sub)
            )
            for r in conn.execute(q):
                if (int(r.src_id), r.src_sub) in wanted:
                    out.setdefault(Node(kind, int(r.src_id), r.src_sub), []).append(
                        Node(r.dst_kind, int(r.dst_id), r.dst_sub)
                    )
    return out


def write_inputs(conn: sa.Connection, dst: Node, refs: Sequence[InputRef]) -> None:
    """The inputs of `dst` are exactly `refs` (the rows of role `in` replaced; a pin stays)."""
    c = lineage.c
    conn.execute(
        sa.delete(lineage).where(c.dst_kind == dst.kind, c.dst_id == dst.id, c.dst_sub == dst.sub, c.role == IN)
    )
    rows: dict[Node, dict[str, Any]] = {}
    for pos, r in enumerate(refs):
        rows.setdefault(
            r.node,
            {
                "dst_kind": dst.kind, "dst_id": dst.id, "dst_sub": dst.sub, "role": IN,
                "src_kind": r.node.kind, "src_id": r.node.id, "src_sub": r.node.sub,
                "pos": pos, "src_fp": r.fp, "src_name": (r.name or "")[:200],
            },
        )  # fmt: skip
    if rows:
        conn.execute(sa.insert(lineage), list(rows.values()))


def set_pin(conn: sa.Connection, node: Node, pinned: bool) -> None:
    c = lineage.c
    where = (c.dst_kind == node.kind, c.dst_id == node.id, c.dst_sub == node.sub, c.role == PIN)
    conn.execute(sa.delete(lineage).where(*where))
    if pinned:
        conn.execute(
            sa.insert(lineage).values(
                dst_kind=node.kind, dst_id=node.id, dst_sub=node.sub, role=PIN, src_kind="", src_id=0, src_sub="",
                pos=0, src_fp=None, src_name="",
            )
        )  # fmt: skip


def forget(conn: sa.Connection, node: Node) -> int:
    """A node was deleted: its own rows (inputs and pin) go. The rows of those built from it stay — they say
    «источник удалён»."""
    c = lineage.c
    res = conn.execute(sa.delete(lineage).where(c.dst_kind == node.kind, c.dst_id == node.id, c.dst_sub == node.sub))
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


def forget_object(conn: sa.Connection, kind: str, object_id: int) -> None:
    """An object of `kind` (as `objects.kind`) was deleted: the rows of its node, and for a block model of all its
    fields (`bm_field`), go."""
    c = lineage.c
    kinds = ["object:" + kind] + (["bm_field"] if kind == "block_model" else [])
    conn.execute(sa.delete(lineage).where(c.dst_kind.in_(kinds), c.dst_id == object_id))


def forget_many(conn: sa.Connection, kind: str, ids: Sequence[int]) -> None:
    c = lineage.c
    for i in range(0, len(ids), CHUNK):
        conn.execute(sa.delete(lineage).where(c.dst_kind == kind, c.dst_id.in_(list(ids[i : i + CHUNK]))))


def all_dst(conn: sa.Connection) -> list[Node]:
    """Every node that has rows of its own (for `sweep`)."""
    c = lineage.c
    q = sa.select(c.dst_kind, c.dst_id, c.dst_sub).distinct()
    return [Node(r.dst_kind, int(r.dst_id), r.dst_sub) for r in conn.execute(q)]


def count(conn: sa.Connection) -> int:
    return int(conn.execute(sa.select(sa.func.count()).select_from(lineage)).scalar_one())


def remap_ids(conn: sa.Connection, maps: Mapping[str, Mapping[int, int]]) -> int:
    """`.bfggis` import (ADR 0025 §11): the ids of the nodes of the archive become those of the new project.
    `maps` — {"objects": …, "dh_tables": …, "dh_results": …}. A row whose node is not in the archive is dropped
    (its own rows) or, for a source, keeps its name and loses its id (src_id 0: «источник удалён»; the old number
    would point at another object). Fingerprints stay as they are. Returns the rows kept."""
    c = lineage.c
    rows = [dict(r._mapping) for r in conn.execute(sa.select(lineage))]
    conn.execute(sa.delete(lineage))

    def new(kind: str, old: int) -> int | None:
        table = {"dh_table": "dh_tables", "dh_result": "dh_results"}.get(kind, "objects")
        return maps.get(table, {}).get(int(old))

    out: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in rows:
        dst = new(r["dst_kind"], r["dst_id"])
        if dst is None:
            continue
        r["dst_id"] = dst
        if r["role"] == IN:
            r["src_id"] = (new(r["src_kind"], r["src_id"]) if r["src_id"] else None) or 0
        out[tuple(r[k.name] for k in c if k.primary_key)] = r
    if out:
        conn.execute(sa.insert(lineage), list(out.values()))
    return len(out)
