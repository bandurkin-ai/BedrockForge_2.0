"""The public view of the pit scenarios — the list entry, the full info and the state in the graph (split from `store`
in подшаг 21.21б-2 to keep the file under ~600 lines; `store` re-exports `graph_of`, `info`, `list_scenarios`, so the
imports of the callers stay as they were).

    graph_of(conn, ids, store=None)  → {id: {stale, stale_reason, source_lost, pinned}} (one batch)
    info(conn, id, store=None)       → `GET /pit/scenarios/{id}`
    list_scenarios(conn)             → `GET /pit/scenarios`

`store` is imported inside the functions: `store` imports this module at its end.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.db.schema import blob_owners, blobs, objects

if TYPE_CHECKING:
    from bf_core.storage.blobs import BlobStore

__all__ = ["graph_of", "info", "list_scenarios"]


def graph_of(conn: sa.Connection, ids: Sequence[int], store: BlobStore | None = None) -> dict[int, dict[str, Any]]:
    """The state of the scenarios in the graph: {id: {stale, stale_reason, source_lost, pinned}} (one batch)."""
    from bf_core import lineage

    from .store import NODE

    nodes = [lineage.Node(NODE, int(i)) for i in ids]
    got = lineage.status(conn, nodes, store=store) if nodes else {}
    out: dict[int, dict[str, Any]] = {}
    for n in nodes:
        s = got[n]
        lost = s.state == lineage.LOST
        out[n.id] = {"stale": s.state in (lineage.STALE, lineage.LOST) or (s.state == lineage.PINNED and s.changed),
                     "stale_reason": s.reason if s.state != lineage.CURRENT else None, "source_lost": lost,
                     "pinned": s.state == lineage.PINNED}  # fmt: skip
    return out


def _public(conn: sa.Connection, row: Any, owned: Mapping[str, tuple[str, int]],
            graph: Mapping[str, Any]) -> dict[str, Any]:  # fmt: skip
    from . import store as st

    meta = dict(row.meta or {})
    has = st.DATA in owned
    on = st._is_running(conn, row)  # noqa: SLF001
    state = "running" if on else ("error" if meta.get("state") == "error" else "ready")
    model = dict(meta.get("model") or {})
    shells = list(meta.get("shells") or []) if has else []
    return {
        "id": int(row.id), "name": row.name, "version": int(row.version), "state": state,
        "has_result": has, "params_changed": st._changed(meta, has),  # noqa: SLF001
        "model": {"id": model.get("id"), "name": model.get("name")}, "shells": len(shells),
        "value": shells[-1].get("value") if shells else None, "graph": dict(graph),
        "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat(),
        "job_id": meta.get("run_job") if on else meta.get("job_id"),
    }  # fmt: skip


def info(conn: sa.Connection, scenario_id: int, store: BlobStore | None = None) -> dict[str, Any]:
    """`GET /pit/scenarios/{id}`: the list entry plus the `error`, the parameters shown (the draft when there is one),
    their hash, `meta` (without the private keys) and the size of the files. The graph is checked with `store` (the
    makers of the fields) when it is given."""
    from . import store as st

    row = st._row(conn, scenario_id)  # noqa: SLF001
    owned = st._owned(conn, scenario_id)  # noqa: SLF001
    out = _public(conn, row, owned, graph_of(conn, [scenario_id], store)[scenario_id])
    meta = dict(row.meta or {})
    draft = meta.get("draft")
    shown = draft if isinstance(draft, Mapping) else {"params": meta.get("params"), "hash": meta.get("params_hash")}
    out.update({"error": meta.get("error") if out["state"] == "error" else None, "params": shown["params"],
                "params_hash": shown["hash"], "size": sum(s for _sha, s in owned.values()),
                "meta": {k: v for k, v in meta.items() if k not in ("draft", "run_job", "uid")}})  # fmt: skip
    return out


def list_scenarios(conn: sa.Connection) -> list[dict[str, Any]]:
    """Every scenario by name: the list entries with the state in the graph (one batch)."""
    from . import store as st

    rows = conn.execute(
        sa.select(objects).where(objects.c.kind == st.KIND).order_by(objects.c.name_key, objects.c.id)
    ).all()
    graph = graph_of(conn, [int(r.id) for r in rows])
    owned: dict[int, dict[str, tuple[str, int]]] = {int(r.id): {} for r in rows}
    q = (sa.select(blob_owners.c.owner_id, blob_owners.c.role, blob_owners.c.sha256, blobs.c.size)
         .join(blobs, blobs.c.sha256 == blob_owners.c.sha256)
         .where(blob_owners.c.owner_kind == st.OWNER, blob_owners.c.owner_id.in_(list(owned))))  # fmt: skip
    for r in conn.execute(q) if owned else ():
        owned[int(r.owner_id)][str(r.role)] = (str(r.sha256), int(r.size))
    return [_public(conn, r, owned[int(r.id)], graph[int(r.id)]) for r in rows]
