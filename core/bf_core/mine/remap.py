"""Ids inside the `meta` of a pit scenario on a `.bfggis` import (подшаг 21.10; ADR 0028 решение 24; ADR 0025 §11;
docs/progress/e21-api.md §8).

The scenarios come with the archive as objects (`archive/spec.py`: `objects` renumbered, the files with
`blob_owners`, the rows of the graph by `lineage.remap_ids`). Their `meta` names other rows by id: the block model
(`params.model_id`, `model.id`), the wireframe of the terrain (`params.terrain_id`), the filter of the library
(`params.filter_id`), the wireframes and the surfaces of the restriction (`params.restrict`: a leaf) and of the rock
rules (`params.rocks.by.rules[].rules`), the wireframes the run made (`outputs.wireframes`: the DTMs of the shells, the
solids) and `inputs[]` — the model, the fields of it (id «<model>:<name>»), the filter, the wireframes. The same ids
are in the draft of the parameters (`meta.draft`). An id the archive does not have becomes `DEAD_ID` (the model, the
terrain, the filter, an output, an input; the old number would point at another row of the new project) or null (a
leaf of the rules: it shows «недоступен», as in a filter, `blocks/filters/remap.py`); the scenario still reads and
the graph says «источник удалён». `params_hash` holds the ids: it is made again (and the hash of the draft). The
fingerprint of the scenario in the graph (the SHA-256 of `data` and ALGO) and those of its inputs have no ids, so a
scenario that was current stays so. A mark «идёт расчёт» (the snapshot of a project that was running it) is not
carried over: its job is not a job of the new project, the scenario is as it was. `fields`, `shells`, `totals`,
`control` need nothing. The id of the scenario in `source.scenario_id` of its fields PIT / ORE in the
models is renumbered too (`remap_fields`, with the ids in `source.params`), and that in `meta.source.scenario_id` of its
DTMs and solids (`remap_wireframe`: `source.op` is `pit_surface` / `pit_solid`) — `dtm_write.outputs_of` finds them by
it, so a second «Поверхности» in the new project replaces them in place instead of making copies (21.21б-2).

Called once all rows are written: `remap_pit(conn, maps["objects"])` in `importer._restore`, before `remap_lineage`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.model.store_base import DEAD_ID

from .store_graph import OUTPUT_OPS

__all__ = ["DEAD_ID", "remap_fields", "remap_meta", "remap_params", "remap_pit", "remap_wireframe"]

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


def _rocks(rocks: Mapping[str, Any], objects_of: Lookup) -> dict[str, Any]:
    from bf_core.blocks.filters.schema import remap_ids

    by = rocks.get("by")
    if not isinstance(by, Mapping) or not isinstance(by.get("rules"), list):
        return dict(rocks)
    rules = [{**r, "rules": remap_ids(r["rules"], objects_of)} if isinstance(r, Mapping)
             and isinstance(r.get("rules"), Mapping) else r for r in by["rules"]]  # fmt: skip
    return {**rocks, "by": {**by, "rules": rules}}


def remap_params(params: Mapping[str, Any], objects_of: Lookup) -> dict[str, Any]:
    """`params` (canonical form) with the ids of the new project."""
    from bf_core.blocks.filters.schema import remap_ids

    out = dict(params)
    for key in ("model_id", "terrain_id", "filter_id"):
        if out.get(key) is not None:
            out[key] = _new(out[key], objects_of)
    if isinstance(out.get("restrict"), Mapping):
        out["restrict"] = remap_ids(out["restrict"], objects_of)
    if isinstance(out.get("rocks"), Mapping):
        out["rocks"] = _rocks(out["rocks"], objects_of)
    return out


def _hash(params: Mapping[str, Any], old: Any) -> Any:
    """The hash of the parameters with the new ids; the old one when the parameters do not read (it is no state)."""
    from . import spec

    try:
        return spec.parse_params(dict(params)).params_hash()
    except ApiError:
        return old


def _output(w: Any, objects_of: Lookup) -> Any:
    if isinstance(w, Mapping):  # {shell, rf, wireframe_id | id}
        key = "wireframe_id" if "wireframe_id" in w else "id"
        return {**w, key: _new(w.get(key), objects_of)} if key in w else dict(w)
    return _new(w, objects_of)


def remap_meta(meta: Mapping[str, Any], objects_of: Lookup) -> dict[str, Any]:
    """The `meta` of a scenario with the ids of the new project (`objects_of`: old id → new id or None)."""
    out = dict(meta)
    if out.get("state") == "building":
        out["state"] = "ready"
    out.pop("run_job", None)
    if isinstance(out.get("params"), Mapping):
        out["params"] = remap_params(out["params"], objects_of)
        out["params_hash"] = _hash(out["params"], meta.get("params_hash"))
    draft = out.get("draft")
    if isinstance(draft, Mapping) and isinstance(draft.get("params"), Mapping):
        params = remap_params(draft["params"], objects_of)
        out["draft"] = {**draft, "params": params, "hash": _hash(params, draft.get("hash"))}
    if isinstance(out.get("model"), Mapping):
        out["model"] = {**out["model"], "id": _new(out["model"].get("id"), objects_of)}
    if isinstance(out.get("inputs"), list):
        out["inputs"] = [_input(i, objects_of) for i in out["inputs"]]
    if isinstance(out.get("outputs"), Mapping) and isinstance(out["outputs"].get("wireframes"), list):
        wires = [_output(w, objects_of) for w in out["outputs"]["wireframes"]]
        out["outputs"] = {**out["outputs"], "wireframes": wires}
    return out


def remap_fields(meta: Mapping[str, Any], objects_of: Lookup) -> dict[str, Any]:
    """The `meta` of a block model with the id of the scenario in `source.scenario_id` of its fields PIT and ORE
    (`source.op == "pit"`) renumbered: the graph finds the maker of the field by it (`adapt_pit.pit_field_owner`)."""
    fields = []
    for f in meta.get("fields") or []:
        src = f.get("source") if isinstance(f, Mapping) else None
        if isinstance(src, Mapping) and src.get("op") == "pit" and src.get("scenario_id") is not None:
            new = {**src, "scenario_id": _new(src["scenario_id"], objects_of)}
            if isinstance(src.get("params"), Mapping):  # the model, the relief, the filter, the leaves of the rules
                new["params"] = remap_params(src["params"], objects_of)
            f = {**f, "source": new}
        fields.append(f)
    return {**meta, "fields": fields}


def remap_wireframe(meta: Mapping[str, Any], objects_of: Lookup) -> dict[str, Any]:
    """The `meta` of a DTM / solid a scenario made (`source.op` in `OUTPUT_OPS`) with `source.scenario_id` of the new
    project; any other wireframe is returned as it is."""
    src = meta.get("source")
    if not isinstance(src, Mapping) or src.get("op") not in OUTPUT_OPS or src.get("scenario_id") is None:
        return dict(meta)
    return {**meta, "source": {**src, "scenario_id": _new(src["scenario_id"], objects_of)}}


def remap_pit(conn: sa.Connection, objects_map: Mapping[int, int]) -> int:
    """Renumbers the ids inside the `meta` of every pit scenario, the id of the scenario in the fields PIT / ORE of
    the models and in the DTMs and solids it made; returns how many objects changed."""
    from bf_core.db.schema import objects

    from .store import KIND

    changed = 0
    for ident, meta in conn.execute(sa.select(objects.c.id, objects.c.meta).where(objects.c.kind == KIND)).all():
        new = remap_meta(meta or {}, objects_map.get)
        if new != (meta or {}):
            conn.execute(sa.update(objects).where(objects.c.id == ident).values(meta=new))
            changed += 1
    for ident, meta in conn.execute(sa.select(objects.c.id, objects.c.meta).where(
            objects.c.kind == "block_model")).all():  # fmt: skip
        if not any((f.get("source") or {}).get("op") == "pit" for f in (meta or {}).get("fields") or []):
            continue
        fresh = remap_fields(meta, objects_map.get)
        if fresh != meta:
            conn.execute(sa.update(objects).where(objects.c.id == ident).values(meta=fresh))
            changed += 1
    for ident, meta in conn.execute(sa.select(objects.c.id, objects.c.meta).where(
            objects.c.kind == "wireframe")).all():  # fmt: skip
        fresh = remap_wireframe(meta or {}, objects_map.get)
        if fresh != (meta or {}):
            conn.execute(sa.update(objects).where(objects.c.id == ident).values(meta=fresh))
            changed += 1
    return changed
