"""Ids inside the `meta` of a resource report on a `.bfggis` import (подшаг 20.6; ADR 0027 решение 14, 21; ADR 0025 §11;
docs/progress/e20-api.md §7).

The reports come with the archive as objects (`archive/spec.py`: `objects` renumbered, the files with `blob_owners`, the
rows of the graph by `lineage.remap_ids`). Their `meta` names other rows by id: the model (`params.model_id`,
`model.id`), the filter of the library (`params.filter_id`), the wireframes and the surfaces of the quick rules
(`params.restrict`: a leaf), and `inputs[]` — the model, the fields of it (id «<model>:<name>»), the filter, the
wireframes. An id the archive does not have becomes `DEAD_ID` (the model, the filter, an input; the old number would
point at another row of the new project) or null (a leaf of the rules: it shows «недоступен», as in a filter,
`blocks/filters/remap.py`); the report still reads and the graph says «источник удалён». `params_hash` holds the ids: it
is made again. The fingerprint of the report in the graph (the SHA-256 of `data` and ALGO) and those of its inputs
have no ids, so a report that was current stays so. A mark «идёт построение» (the snapshot of a project that was
rebuilding it) is not carried over: its job is not a job of the new project, the report is as it was (`settle`).
`fields`, `totals`, `files` need nothing.

Called once all rows are written: `remap_resources(conn, maps["objects"])` in `importer._restore`, before
`remap_lineage`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.model.store_base import DEAD_ID

__all__ = ["remap_meta", "remap_resources"]

Lookup = Callable[[int], int | None]


def _new(old: Any, lookup: Lookup) -> Any:
    """The new id of `old`; `DEAD_ID` when the archive did not have it; null (nothing named) stays null."""
    if old is None or isinstance(old, bool):
        return old
    try:
        got = lookup(int(old))
    except (TypeError, ValueError):
        return DEAD_ID
    return DEAD_ID if got is None else int(got)


def _input(i: Any, objects_of: Lookup) -> Any:
    if not isinstance(i, Mapping):
        return i
    ident = i.get("id")
    if i.get("kind") == "bm_field":  # "<block model id>:<field>"
        model, _, name = str(ident).partition(":")
        return {**i, "id": f"{_new(model, objects_of)}:{name}"}
    return {**i, "id": _new(ident, objects_of)}


def _params(params: Mapping[str, Any], objects_of: Lookup) -> dict[str, Any]:
    from bf_core.blocks.filters.schema import remap_ids

    out = dict(params)
    if "model_id" in out:
        out["model_id"] = _new(out["model_id"], objects_of)
    if out.get("filter_id") is not None:
        out["filter_id"] = _new(out["filter_id"], objects_of)
    if isinstance(out.get("restrict"), Mapping):
        out["restrict"] = remap_ids(out["restrict"], objects_of)
    return out


def remap_meta(meta: Mapping[str, Any], objects_of: Lookup) -> dict[str, Any]:
    """The `meta` of a report with the ids of the new project (`objects_of`: old id → new id or None)."""
    out = dict(meta)
    if out.get("state") == "building":
        out["state"] = "ready"
    out.pop("job_id", None)
    if isinstance(out.get("params"), Mapping):
        out["params"] = _params(out["params"], objects_of)
        out["params_hash"] = _hash(out["params"], meta.get("params_hash"))
    if isinstance(out.get("model"), Mapping):
        out["model"] = {**out["model"], "id": _new(out["model"].get("id"), objects_of)}
    if isinstance(out.get("inputs"), list):
        out["inputs"] = [_input(i, objects_of) for i in out["inputs"]]
    return out


def _hash(params: Mapping[str, Any], old: Any) -> Any:
    """The hash of the parameters with the new ids; the old one when the parameters do not read (it is no state)."""
    from bf_core.resources import spec

    try:
        return spec.parse_params(dict(params)).params_hash()
    except ApiError:
        return old


def remap_resources(conn: sa.Connection, objects_map: Mapping[int, int]) -> int:
    """Renumbers the ids inside the `meta` of every resource report; returns how many reports changed."""
    from bf_core.db.schema import objects

    from .store import KIND

    changed = 0
    for ident, meta in conn.execute(sa.select(objects.c.id, objects.c.meta).where(objects.c.kind == KIND)).all():
        new = remap_meta(meta or {}, objects_map.get)
        if new != (meta or {}):
            conn.execute(sa.update(objects).where(objects.c.id == ident).values(meta=new))
            changed += 1
    return changed
