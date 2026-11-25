"""Graph of dependencies and the freshness of computed objects (ТЗ 7.35 Д-35, ADR 0025).

    from bf_core import lineage
    lineage.record(conn, lineage.Node("object:variogram_model", id), [lineage.Node("dh_table", table_id)])
    lineage.status(conn, nodes)           # {node: NodeStatus(state, reason, …)}
    lineage.impact(conn, node)            # who is built from it (the window «Влияние изменения»)
    lineage.pin(conn, node, True)         # «Закрепить»

The provider modules (`providers.py`) are imported here, so every kind is known as soon as the package is.
"""

from . import providers as providers  # registers the kinds
from .graph import (
    BUILDING,
    CURRENT,
    DELETED,
    LOST,
    PINNED,
    STALE,
    NodeStatus,
    fingerprint,
    forget,
    forget_object,
    impact,
    origin,
    pin,
    record,
    reindex,
    status,
    sweep,
    sync,
)
from .store import InputRef, Node, remap_ids

__all__ = [
    "BUILDING", "CURRENT", "DELETED", "LOST", "PINNED", "STALE", "InputRef", "Node", "NodeStatus",
    "fingerprint", "forget", "forget_object", "impact", "origin", "pin", "providers", "record", "reindex",
    "remap_ids", "status", "sweep", "sync",
]  # fmt: skip
